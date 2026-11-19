"""
social/mood_manager.py — 星见弥连续情绪流状态机 (基于 dsh-feiyu-mood 进阶架构)

核心职责：
1. 双层情绪模型：
   - 宏观心境基调 mood (0~100，基准线 60.0，5小时指数半衰期，按 (moodDelta/1.25)*0.75 灵敏推进；情绪越高越好说话)
   - 微观七维情绪 dims (happy/calm/sweet/proud/worry/sulky/jealous，0~100，60分钟指数半衰期，按 0.80x 效率累加)
   - 第一人称过去式内心来历 last_reason + 产生场景 last_source_scene
2. 三层渐变式上下文注入 (build_mood_injection_block)：
   - 阶段一【脉冲层】：情绪刚变化的首轮，完整注入「（这会儿）... + （刚才）{last_reason}」（说事不说调）
   - 阶段二【余温层】：首轮已消费但仍在半衰期内，隐去具体事件细节，仅注入心理余温底色（防失忆且防祥林嫂复读）
   - 阶段三【跨场景桥接层】：主人私聊 ⇄ 群聊切换时，自动注入跨空间情绪延续桥接提示
"""
import os
import json
import time
import math
from config import MOOD_STATE_FILE, MASTER_QQ

BASELINE_MOOD = 60.0
HALF_LIFE_MOOD_SEC = 5.0 * 3600.0   # 5小时半衰期（原12h过稳，缩短后潮汐起伏更鲜明灵动）
HALF_LIFE_DIM_SEC = 60.0 * 60.0     # 60分钟半衰期（原90min，配合高爆发力脉冲实现敏锐起落）

MOOD_GAIN_EFFICIENCY = 0.75         # 总体情绪获取效率：调整为此前灵敏度(/1.25)的 75%（等效推进系数 0.60）
DIM_GAIN_EFFICIENCY = 0.80          # 7D具体小情绪获得效率：调整为此前效率的 80%

DIM_KEYS = ["happy", "calm", "sweet", "proud", "worry", "sulky", "jealous"]

DIM_CN_NAMES = {
    "happy": "开心",
    "calm": "踏实自在",
    "sweet": "甜蜜害羞",
    "proud": "得意傲娇",
    "worry": "担心心疼",
    "sulky": "委屈郁闷",
    "jealous": "吃醋别扭",
}


def _default_mood_state() -> dict:
    return {
        "mood": BASELINE_MOOD,
        "dims": {k: 0.0 for k in DIM_KEYS},
        "last_reason": "",
        "last_source_scene": "",
        "updated_at": time.time(),
        "pulse_consumed": True,
        "decay_noted": True,
        "history": [],
    }


def load_mood_state() -> dict:
    """从磁盘读取原始情绪状态，不存在则初始化"""
    state = _default_mood_state()
    if os.path.exists(MOOD_STATE_FILE):
        try:
            with open(MOOD_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                state["mood"] = float(data.get("mood", BASELINE_MOOD))
                raw_dims = data.get("dims", {})
                if isinstance(raw_dims, dict):
                    for k in DIM_KEYS:
                        state["dims"][k] = max(0.0, min(100.0, float(raw_dims.get(k, 0.0))))
                state["last_reason"] = str(data.get("last_reason", "")).strip()
                state["last_source_scene"] = str(data.get("last_source_scene", "")).strip()
                state["updated_at"] = float(data.get("updated_at", time.time()))
                state["pulse_consumed"] = bool(data.get("pulse_consumed", True))
                state["decay_noted"] = bool(data.get("decay_noted", True))
                raw_hist = data.get("history", [])
                if isinstance(raw_hist, list):
                    state["history"] = [
                        {"ts": float(h["ts"]), "mood": float(h["mood"])}
                        for h in raw_hist
                        if isinstance(h, dict) and "ts" in h and "mood" in h
                    ]
        except Exception:
            pass
    return state


def save_mood_state(state: dict):
    """原子落盘情绪状态"""
    try:
        tmp_path = f"{MOOD_STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, MOOD_STATE_FILE)
    except Exception as e:
        print(f"⚠️ 保存情绪状态失败: {e}")


def get_decayed_mood_state() -> tuple[dict, float]:
    """
    读取并计算按当前时间戳实时指数衰减后的情绪视图。
    返回 (decayed_view_dict, elapsed_seconds)
    """
    raw = load_mood_state()
    now = time.time()
    dt = max(0.0, now - float(raw.get("updated_at", now)))

    f_mood = math.pow(0.5, dt / HALF_LIFE_MOOD_SEC) if dt > 0 else 1.0
    f_dim = math.pow(0.5, dt / HALF_LIFE_DIM_SEC) if dt > 0 else 1.0

    decayed_mood = round(BASELINE_MOOD + (raw["mood"] - BASELINE_MOOD) * f_mood, 1)
    decayed_mood = max(0.0, min(100.0, decayed_mood))

    decayed_dims = {}
    for k in DIM_KEYS:
        val = round(raw["dims"].get(k, 0.0) * f_dim, 1)
        decayed_dims[k] = max(0.0, min(100.0, val))

    view = {
        "mood": decayed_mood,
        "dims": decayed_dims,
        "last_reason": raw["last_reason"],
        "last_source_scene": raw["last_source_scene"],
        "updated_at": raw["updated_at"],
        "pulse_consumed": raw["pulse_consumed"],
        "decay_noted": raw["decay_noted"],
    }
    return view, dt


def mood_band_phrase(mood: float) -> str:
    """
    将宏观基调分值 (0~100，基准线 60.0) 转化为第一人称自然心境描述与好说话程度：
    - 基准时 (55~65)：心情表现为平静空白，对熟人热情，对陌生人正常；
    - 情绪越高越好说话（越温和宽容、好商量）；情绪越低越没那么好说话。
    """
    m = round(mood)
    if m >= 84:
        return "心里亮堂雀跃、心情极佳（情绪很高，这会儿超级好说话，对谁都温和宽容、好商量）"
    if m >= 74:
        return "心里愉悦轻快（情绪挺高，这会儿非常好说话，平时傲娇的事也容易松口）"
    if m >= 66:
        return "心里温和舒展（情绪高于基准，比平时更好说话一些）"
    if m >= 55:
        return "心情平静空白（日常基准态：心里无特殊波澜，对熟人热情，对陌生人正常自然）"
    if m >= 42:
        return "心里稍微有点闷闷的（情绪低于基准，没平时那么好说话，略带点小挑剔或小傲娇）"
    return "心里挺低落难受的（情绪低谷，这会儿不太好说话，对不熟的人态度简短冷淡）"


def dim_feel_phrase(key: str, val: float) -> str:
    """将微观情绪维度转化为自然口语心理感受"""
    v = round(val)
    if v < 15:
        return ""
    high = v >= 42
    mapping = {
        "happy":   "心里直冒泡地开心" if high else "带着点小雀跃",
        "calm":    "整个人很踏实自在" if high else "心里挺安稳松弛的",
        "sweet":   "心里甜丝丝的还有点害羞" if high else "带着点软乎乎的甜意",
        "proud":   "尾巴都快翘天上去了（傲娇小得意）" if high else "心里暗暗有点小得意",
        "worry":   "心里直犯嘀咕、挺挂心心疼的" if high else "稍微有点替对方操心",
        "sulky":   "心里堵着一口小闷气、有点委屈" if high else "稍微有点小别扭小郁闷",
        "jealous": "酸溜溜的有点吃味" if high else "心里泛起一点点小醋意",
    }
    return mapping.get(key, "")


def _render_current_feel_line(mood: float, dims: dict) -> str:
    """合成（这会儿）心理状态短句"""
    base_phrase = mood_band_phrase(mood)
    sorted_dims = sorted(
        [(k, v) for k, v in dims.items() if v >= 15.0],
        key=lambda x: x[1],
        reverse=True
    )
    dim_phrases = [dim_feel_phrase(k, v) for k, v in sorted_dims[:2]]
    dim_phrases = [p for p in dim_phrases if p]
    if dim_phrases:
        return f"{base_phrase}，{'，'.join(dim_phrases)}"
    return base_phrase


def apply_mood_update(
    mood_delta: float,
    dim_deltas: dict,
    reason: str,
    source_scene: str
) -> dict:
    """
    由慢车道观察者调用：先按当前时间结算衰减，再应用本轮情绪增量并持久化。
    - mood 按 round((mood_delta / 1.25) * 0.75, 1) 推进（获取效率为原 /1.25 的 75%），并记录真实历史轨迹点至 history
    - dims 单次冲量上限 [-50.0, 65.0]，按 80% 获得效率 (DIM_GAIN_EFFICIENCY = 0.80) 累加并夹逼在 [0, 100]
    """
    raw_prev = load_mood_state()
    view, _ = get_decayed_mood_state()
    now = time.time()

    clamped_delta = max(-20.0, min(20.0, float(mood_delta or 0.0)))
    mood_step = round((clamped_delta / 1.25) * MOOD_GAIN_EFFICIENCY, 1)
    new_mood = max(0.0, min(100.0, round(view["mood"] + mood_step, 1)))

    new_dims = dict(view["dims"])
    max_dim_shift = 0.0
    if isinstance(dim_deltas, dict):
        for k in DIM_KEYS:
            if k in dim_deltas:
                raw_d = max(-50.0, min(65.0, float(dim_deltas.get(k, 0.0) or 0.0)))
                d_val = round(raw_d * DIM_GAIN_EFFICIENCY, 1)
                max_dim_shift = max(max_dim_shift, abs(d_val))
                new_dims[k] = max(0.0, min(100.0, round(new_dims.get(k, 0.0) + d_val, 1)))

    clean_reason = (reason or "").strip()
    meaningful_shift = (abs(clamped_delta) >= 1.5 or max_dim_shift >= 4.0) and bool(clean_reason)

    # 维护最近 48 小时的真实宏观心境历史轨迹点，供 WebUI 绘制真实情感心电图 (EKG)
    cutoff_ts = now - 48.0 * 3600.0
    history = [
        h for h in raw_prev.get("history", [])
        if isinstance(h, dict) and float(h.get("ts", 0.0)) >= cutoff_ts
    ]
    if not history and raw_prev.get("updated_at"):
        prev_ts = float(raw_prev.get("updated_at", now - 600.0))
        if prev_ts >= cutoff_ts and prev_ts < now - 2.0:
            history.append({"ts": round(prev_ts, 1), "mood": round(float(raw_prev.get("mood", BASELINE_MOOD)), 1)})

    if meaningful_shift or abs(mood_step) >= 0.5:
        if not history or (now - float(history[-1]["ts"])) >= 10.0:
            history.append({"ts": round(now - 1.0, 1), "mood": view["mood"]})
        history.append({"ts": round(now, 1), "mood": new_mood})
    history = history[-120:]

    new_state = {
        "mood": new_mood,
        "dims": new_dims,
        "last_reason": clean_reason if meaningful_shift else view["last_reason"],
        "last_source_scene": source_scene if meaningful_shift else view["last_source_scene"],
        "updated_at": now if (meaningful_shift or abs(mood_step) >= 0.5) else view["updated_at"],
        "pulse_consumed": False if meaningful_shift else view["pulse_consumed"],
        "decay_noted": False if meaningful_shift else view["decay_noted"],
        "history": history,
    }
    save_mood_state(new_state)
    return new_state


def build_mood_injection_block(current_scene: str, consume_pulse: bool = True) -> str:
    """
    构建注入 context_builder.py 的三层渐变式情绪上下文块：
    - 脉冲层（刚发生情绪波动的第 1 轮）：完整注入（这会儿）+（刚才）{last_reason}
    - 跨场景桥接层（Scene-Switch Bridge）：当私聊与群聊场景切换时生成桥接内心独白
    - 余温层（已消费脉冲但仍在半衰期内）：只注入心理底色与余温，隐去旧事件防复读
    """
    view, dt = get_decayed_mood_state()
    mood = view["mood"]
    dims = view["dims"]
    last_reason = view["last_reason"]
    last_scene = view["last_source_scene"]
    max_dim_val = max(dims.values()) if dims else 0.0
    has_active_emotion = (max_dim_val >= 15.0) or (abs(mood - BASELINE_MOOD) >= 8.0)

    feel_line = _render_current_feel_line(mood, dims)
    mood_header = f"【当前内心情绪底色（心境 {mood:.0f}/100 · 基准线 60 | 情绪越高越好说话）】"

    # 1. 阶段一 & 阶段三：脉冲层 / 跨场景桥接层 (情绪刚变化后的首轮注入)
    if not view["pulse_consumed"] and last_reason and dt <= 3600.0:
        if consume_pulse:
            raw = load_mood_state()
            raw["pulse_consumed"] = True
            save_mood_state(raw)

        # 判断是否发生了跨场景切换 (主人/好友私聊 ⇄ 群聊)
        if current_scene and last_scene and current_scene != last_scene:
            is_last_priv = (last_scene == "master_private" or last_scene.startswith("private_"))
            is_curr_priv = (current_scene == "master_private" or current_scene.startswith("private_"))
            if is_last_priv and current_scene.startswith("group_"):
                who_desc = f"自家主人({MASTER_QQ})" if last_scene == "master_private" else "好友"
                return (
                    f"{mood_header}\n"
                    f"（这会儿）{feel_line}\n"
                    f"（跨场景余温）刚才你在和{who_desc}的私聊里：{last_reason}。现在你已切到【群聊({current_scene})】里回应群友"
                    f"（注意替对方保密私聊具体内容，必须调用 send_group_msg 在群内发言，也别把这段心理描述念出来）。"
                )
            elif last_scene.startswith("group_") and is_curr_priv:
                if current_scene == "master_private":
                    return (
                        f"{mood_header}\n"
                        f"（这会儿）{feel_line}\n"
                        f"（跨场景余温）刚才你在群里：{last_reason}。现在你已切回【与主人({MASTER_QQ})的专属私聊】，"
                        f"可以自然流露一点刚才的小情绪或顺势向他吐槽，但务必调用 send_private_msg 发在私聊里，绝不要误发回群聊。"
                    )
                else:
                    return (
                        f"{mood_header}\n"
                        f"（这会儿）{feel_line}\n"
                        f"（跨场景余温）刚才你在群里：{last_reason}。现在你正在【私聊({current_scene})】中回应好友，"
                        f"请务必调用 send_private_msg 回复私聊，切勿误发到群聊。"
                    )

        # 同场景脉冲注入 (原汁原味 feiyu-mood event 模式：说事不说调)
        return (
            f"{mood_header}\n"
            f"（这会儿）{feel_line}\n"
            f"（刚才）{last_reason}\n"
            f"（注：这是你这会儿心里的底子，不是非这样不可——对方聊别的你就按自己的判断自然接话，也千万别把这段心理描述原样念出来。）"
        )

    # 2. 阶段二：余温层 (具体事件脉冲已消费，但情绪仍在半衰期内，防跨轮失忆且防祥林嫂复读)
    if has_active_emotion:
        return (
            f"{mood_header}\n"
            f"（这会儿）早些时候攒下的那点情绪余温还在心里留着底子：{feel_line}。"
            f"不用再去翻旧账提之前为什么会有这个心情，带着这份自然的心境顺着眼前的天聊即可。"
        )

    # 3. 消散提示 (当情绪已自然平复回基准线，提醒一次回归平静)
    if not view["decay_noted"] and dt >= 1200.0:
        if consume_pulse:
            raw = load_mood_state()
            raw["decay_noted"] = True
            save_mood_state(raw)
        return (
            f"{mood_header}\n"
            "（这会儿）过了好一阵子，之前那点情绪劲儿已经慢慢淡了，心情回到了平静空白的基准状态（对熟人热情，对陌生人正常自然）。"
        )

    # 4. 稳态基准层 (处于基准线 60 附近且无特殊活跃脉冲时：平静空白，对熟人热情，对陌生人正常)
    return (
        f"{mood_header}\n"
        f"（这会儿）{feel_line}。"
    )


def get_mood_brief_for_observer() -> str:
    """生成供慢车道观察者参考的当前情绪快照简报"""
    view, _ = get_decayed_mood_state()
    active_dims = [f"{DIM_CN_NAMES.get(k, k)}:{v:.0f}" for k, v in view["dims"].items() if v >= 8.0]
    dims_str = "，".join(active_dims) if active_dims else "平静无波澜"
    return f"心境基调={view['mood']:.1f}/100，活跃维度=[{dims_str}]，上次来历=「{view['last_reason'] or '无'}」"

