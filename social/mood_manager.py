"""
social/mood_manager.py — 星见弥连续情绪流状态机 V5.2 (新版 E1 四把防通胀物理锁 + M2 437条静态全复合词库)

核心职责：
1. 7 维微观情绪结构 (happy/calm/sweet/proud/worry/sulky/jealous，0~100) + 宏观基调 mood (0~100，基准线 60.0)
2. 统一五档阈值梯度：
   - 0 档 (静默/未激活): 0 ~ 29
   - 1 档 (轻度): 30 ~ 54
   - 2 档 (中度): 55 ~ 69
   - 3 档 (强烈): 70 ~ 89
   - 4 档 (爆表极值): 90 ~ 100
3. 底层防通胀四把物理锁 (新版 E1：自然演化)：
   - 锁 1 · 五段高位阻力 (S 型边际递减): 单次原始冲量收紧至 [-35, +35]，正向增长按五段分段平滑积分 (1.00x / 0.70x / 0.45x / 0.33x / 0.22x)
   - 锁 2 · 温和对立压制 (最高兼容 1 : 0.7 跨极复合): 暖色系 (happy/calm/sweet/proud) 与冷色系 (worry/sulky/jealous) 对立柔和回压，绝不一刀清零，保护 1:0.7 跨极共存
   - 锁 3 · 两段式退潮衰减: >= 70 半衰期缩短至 22.5 分钟 (20~25min)，< 70 半衰期维持 55 分钟 (50~60min)，跨 70 边界精确分段指数扣减时间
   - 锁 4 · 观察者源头限流: 单次结算最多允许变动 3 个情绪维度（按绝对值排序仅保留前 3 个），不对排名第 3 名及以后的激活维度做人为归零或额外加速衰减
4. 极简折中派单行 Prompt 注入 (build_mood_injection_block)：
   - 存在 last_reason 且距离记录时间 <= 3600 秒: 【当前心情】<映射词>（<极短起因>）
   - 无起因或已超过 1 小时: 【当前心情】<映射词>
"""
import os
import json
import time
import math
from config import MOOD_STATE_FILE, MASTER_QQ
from social.mood_lexicon import (
    DIM_ORDER,
    quantize_single_tier,
    lookup_mood_lexicon,
    SINGLE_DIM_LEXICON,
    MACRO_MOOD_LEXICON,
    quantize_macro_mood_bucket,
)

BASELINE_MOOD = 60.0
HALF_LIFE_MOOD_SEC = 5.0 * 3600.0        # 宏观心境 5 小时半衰期
HALF_LIFE_DIM_HIGH_SEC = 22.5 * 60.0     # 锁 3: >= 70 强档/爆表半衰期 22.5 分钟 (20~25min)
HALF_LIFE_DIM_LOW_SEC = 55.0 * 60.0      # 锁 3: < 70 轻/中度半衰期 55.0 分钟 (50~60min)
HALF_LIFE_DIM_SEC = HALF_LIFE_DIM_LOW_SEC  # 向后兼容 WebUI 引用

MOOD_GAIN_EFFICIENCY = 0.75
DIM_GAIN_EFFICIENCY = 1.00               # 锁 1 由五段分段阻力系数精准控制边际收益
MAX_SINGLE_DIM_RAW_IMPULSE = 35.0        # 锁 1: 收紧单次单维原始冲量至 [-35, +35]
MAX_OBSERVER_DIMS_PER_TURN = 3           # 锁 4: 单次情绪结算最多允许变动 3 个维度

DIM_KEYS = ["happy", "calm", "sweet", "proud", "worry", "sulky", "jealous"]
WARM_DIMS = ("happy", "calm", "sweet", "proud")
COLD_DIMS = ("worry", "sulky", "jealous")

# 锁 1: 五段高位阻力区间与边际系数 (跨档位平滑分段积分)
RESISTANCE_BANDS = (
    (0.0, 30.0, 1.00),
    (30.0, 55.0, 0.70),
    (55.0, 70.0, 0.45),
    (70.0, 90.0, 0.33),
    (90.0, 100.0, 0.22),
)

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
    now = time.time()
    return {
        "mood": BASELINE_MOOD,
        "dims": {k: 0.0 for k in DIM_KEYS},
        "last_reason": "",
        "last_source_scene": "",
        "updated_at": now,
        "reason_updated_at": now,
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
                upd_ts = float(data.get("updated_at", time.time()))
                state["updated_at"] = upd_ts
                # 兼容旧状态文件：若无 reason_updated_at 字段则缺省回退至 updated_at
                raw_reason_ts = data.get("reason_updated_at", None)
                state["reason_updated_at"] = float(raw_reason_ts) if raw_reason_ts is not None else upd_ts
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


def integrate_dim_growth(current_val: float, raw_delta: float) -> float:
    """
    【锁 1 · 五段高位阻力（S 型边际递减）】
    - 原始冲量 raw_delta 先收紧至 [-35, +35]
    - 负向冲量直接扣减并夹逼至 >= 0
    - 正向增长按当前值所处档位逐段平滑积分：
      0~29 (0~30): 1.00x
      30~54 (30~55): 0.70x
      55~69 (55~70): 0.45x
      70~89 (70~90): 0.33x
      90~100: 0.22x
    """
    cur = max(0.0, min(100.0, float(current_val or 0.0)))
    clamped = max(-MAX_SINGLE_DIM_RAW_IMPULSE, min(MAX_SINGLE_DIM_RAW_IMPULSE, float(raw_delta or 0.0)))
    if clamped <= 0.0:
        return round(max(0.0, cur + clamped), 2)

    rem_impulse = clamped
    val = cur
    for lo, hi, coeff in RESISTANCE_BANDS:
        if val >= hi or rem_impulse <= 0.0:
            continue
        if val < lo:
            val = lo
        room = hi - val
        needed_raw = room / coeff
        if rem_impulse <= needed_raw:
            val += rem_impulse * coeff
            rem_impulse = 0.0
            break
        else:
            val = hi
            rem_impulse -= needed_raw

    return round(max(0.0, min(100.0, val)), 2)


def decay_single_dim_two_stage(val: float, dt_sec: float) -> float:
    """
    【锁 3 · 两段式退潮衰减】
    - 数值 >= 70（强档/爆表）：半衰期 22.5 分钟 (HALF_LIFE_DIM_HIGH_SEC)
    - 数值 < 70（轻/中度）：半衰期 55.0 分钟 (HALF_LIFE_DIM_LOW_SEC)
    - 跨 70 边界衰减时，按分段指数公式精确扣减时间。
    """
    v0 = max(0.0, min(100.0, float(val or 0.0)))
    dt = max(0.0, float(dt_sec or 0.0))
    if v0 <= 0.0 or dt <= 0.0:
        return round(v0, 1)

    if v0 > 70.0:
        # 计算从 v0 衰减到 70.0 所需的精确秒数: 70 = v0 * 0.5^(t_to_70 / T_high)
        t_to_70 = HALF_LIFE_DIM_HIGH_SEC * (math.log(v0 / 70.0) / math.log(2.0))
        if dt <= t_to_70:
            res = v0 * math.pow(0.5, dt / HALF_LIFE_DIM_HIGH_SEC)
            return round(max(0.0, min(100.0, res)), 1)
        else:
            rem_dt = dt - t_to_70
            res = 70.0 * math.pow(0.5, rem_dt / HALF_LIFE_DIM_LOW_SEC)
            return round(max(0.0, min(100.0, res)), 1)
    else:
        res = v0 * math.pow(0.5, dt / HALF_LIFE_DIM_LOW_SEC)
        return round(max(0.0, min(100.0, res)), 1)


def apply_gentle_opposite_suppression(
    prev_dims: dict,
    new_dims: dict,
    effective_gains: dict,
    suppression_ratio: float = 0.32,
    coexist_cap_ratio: float = 0.70,
) -> dict:
    """
    【锁 2 · 温和对立压制（最高兼容 1 : 0.7 跨极复合）】
    - 暖色系 (happy, calm, sweet, proud) 与冷色系 (worry, sulky, jealous)
    - 当一侧出现正向有效增长时，对对立侧按 `dom_gain * suppression_ratio`（0.32x）柔和回压，绝不一刀清零，
      严禁微弱波动直接腰斩高数值主情绪（如 sulky=85 时 happy 仅从 26 涨到 30，sulky 仅温和回落约 1.3 点）。
    - 1 : 0.7 跨极共存约束：
      * 仅当增长侧确实成为更强主导极（`dom_peak >= 30.0` 且 `dom_peak >= cur_v`）时，
        才对超过 `0.7 * dom_peak` 的对立维施加柔缓上限约束（至少扣减 `base_pull`，并柔缓收束至 `coexist_limit` 边界）；
      * 若对立维本身数值高于增长侧（`cur_v > dom_peak`，即原对立情绪仍是主极），仅按 `dom_gain * suppression_ratio` 柔和回压；
      * 若对立维原本已在 `0.7 * dom_peak` 以内，则仅在无自身正增长时施加极轻微压制（0.10x）或保留，自然保护跨极复合情绪。
    """
    out = dict(new_dims)
    warm_gain = sum(max(0.0, float(effective_gains.get(k, 0.0))) for k in WARM_DIMS)
    cold_gain = sum(max(0.0, float(effective_gains.get(k, 0.0))) for k in COLD_DIMS)

    if warm_gain <= 0.0 and cold_gain <= 0.0:
        return out

    # 判断本轮主导增长侧（若两侧同时有增长，按净主导侧对另一侧做温和压制）
    if warm_gain >= cold_gain and warm_gain > 0.0:
        dom_keys, opp_keys = WARM_DIMS, COLD_DIMS
        dom_gain = warm_gain - cold_gain * 0.5
    else:
        dom_keys, opp_keys = COLD_DIMS, WARM_DIMS
        dom_gain = cold_gain - warm_gain * 0.5

    if dom_gain <= 0.0:
        return out

    dom_peak = max((float(out.get(k, 0.0)) for k in dom_keys), default=0.0)
    coexist_limit = round(dom_peak * coexist_cap_ratio, 2)
    base_pull = dom_gain * suppression_ratio

    for ok in opp_keys:
        own_gain = float(effective_gains.get(ok, 0.0))
        cur_v = float(out.get(ok, 0.0))
        if cur_v <= 0.0:
            continue

        if dom_peak >= 30.0 and dom_peak >= cur_v:
            # 增长侧已成为更强主导极
            if cur_v > coexist_limit:
                # 优先按 base_pull 柔和回压；若压制后仍高于 0.7 * dom_peak，则柔缓收束至 coexist_limit 边界内（绝不清零）
                pulled = cur_v - base_pull
                target_v = min(pulled, coexist_limit)
                out[ok] = round(max(0.0, target_v), 1)
            else:
                # 原本就在 0.7 倍以内：保护跨极共存，仅在无自身正增长时施加极轻微压制 (0.10x) 且不跌破原值 85%
                if own_gain <= 0.0:
                    mild_drop = min(cur_v * 0.15, dom_gain * 0.10)
                    out[ok] = round(max(0.0, cur_v - mild_drop), 1)
        else:
            # 对立侧原情绪仍强于增长侧（或增长侧尚未激活），绝不触发 0.7 砍刀，仅按比例温和回压
            if own_gain <= 0.0:
                out[ok] = round(max(0.0, cur_v - base_pull), 1)

    return out


def filter_top_k_dim_deltas(dim_deltas: dict, max_dims: int = MAX_OBSERVER_DIMS_PER_TURN) -> dict:
    """
    【锁 4 · 观察者源头限流】
    单次情绪结算最多允许变动 3 个情绪维度（若模型输出超过 3 个非零维度，按绝对值排序仅保留前 3 个）。
    """
    if not isinstance(dim_deltas, dict):
        return {}
    non_zero = []
    for k in DIM_KEYS:
        if k in dim_deltas:
            try:
                v = float(dim_deltas.get(k, 0.0) or 0.0)
            except Exception:
                v = 0.0
            if abs(v) > 1e-6:
                non_zero.append((k, v))
    non_zero.sort(key=lambda item: (-abs(item[1]), DIM_KEYS.index(item[0])))
    selected = non_zero[:max_dims]
    return {k: v for k, v in selected}


def get_decayed_mood_state() -> tuple[dict, float]:
    """
    读取并计算按当前时间戳实时两段式退潮衰减后的情绪视图。
    返回 (decayed_view_dict, elapsed_seconds)
    """
    raw = load_mood_state()
    now = time.time()
    dt = max(0.0, now - float(raw.get("updated_at", now)))

    f_mood = math.pow(0.5, dt / HALF_LIFE_MOOD_SEC) if dt > 0 else 1.0
    decayed_mood = round(BASELINE_MOOD + (raw["mood"] - BASELINE_MOOD) * f_mood, 1)
    decayed_mood = max(0.0, min(100.0, decayed_mood))

    decayed_dims = {}
    for k in DIM_KEYS:
        decayed_dims[k] = decay_single_dim_two_stage(raw["dims"].get(k, 0.0), dt)

    view = {
        "mood": decayed_mood,
        "dims": decayed_dims,
        "last_reason": raw["last_reason"],
        "last_source_scene": raw["last_source_scene"],
        "updated_at": raw["updated_at"],
        "reason_updated_at": raw.get("reason_updated_at", raw["updated_at"]),
        "pulse_consumed": raw["pulse_consumed"],
        "decay_noted": raw["decay_noted"],
    }
    return view, dt


def mood_band_phrase(mood: float) -> str:
    """将宏观基调分值 (0~100) 转化为 M2 基线简短描述（向后兼容外部调用）"""
    bucket = quantize_macro_mood_bucket(mood)
    return MACRO_MOOD_LEXICON[bucket]


def dim_feel_phrase(key: str, val: float) -> str:
    """将单维微观情绪转化为五档对应短词（<30 未激活返回空串，向后兼容外部调用）"""
    tier = quantize_single_tier(val)
    if tier <= 0 or key not in DIM_KEYS:
        return ""
    return SINGLE_DIM_LEXICON.get((key, tier), "")


def _render_current_feel_line(mood: float, dims: dict) -> str:
    """使用 M2 437条静态全复合词库 O(1) 查表返回当前心情短词"""
    return lookup_mood_lexicon(mood, dims)


def apply_mood_update(
    mood_delta: float,
    dim_deltas: dict,
    reason: str,
    source_scene: str
) -> dict:
    """
    由慢车道观察者调用：先按当前时间结算两段式衰减，再应用四把防通胀物理锁并持久化。
    - 锁 4: 单次最多允许变动 3 个微观维度（按绝对值取 Top-3）
    - 锁 1: 单维原始冲量夹逼至 [-35, +35]，正向增长按五段阻力平滑积分
    - 锁 2: 暖/冷对立侧柔和压制 + 1:0.7 跨极共存保护
    - 新版 E1: 不对排名第 3 名及以后的激活维度做人为归零或额外加速衰减
    """
    raw_prev = load_mood_state()
    view, _ = get_decayed_mood_state()
    now = time.time()

    clamped_delta = max(-20.0, min(20.0, float(mood_delta or 0.0)))
    mood_step = round((clamped_delta / 1.25) * MOOD_GAIN_EFFICIENCY, 1)
    new_mood = max(0.0, min(100.0, round(view["mood"] + mood_step, 1)))

    # 锁 4: 观察者源头限流 (最多保留前 3 个变动维度)
    filtered_deltas = filter_top_k_dim_deltas(dim_deltas, max_dims=MAX_OBSERVER_DIMS_PER_TURN)

    prev_dims = dict(view["dims"])
    new_dims = dict(prev_dims)
    effective_gains = {}
    max_dim_shift = 0.0

    # 锁 1: 五段高位阻力积分
    for k, raw_d in filtered_deltas.items():
        updated_v = integrate_dim_growth(prev_dims.get(k, 0.0), raw_d * DIM_GAIN_EFFICIENCY)
        eff_diff = round(updated_v - prev_dims.get(k, 0.0), 2)
        effective_gains[k] = eff_diff
        max_dim_shift = max(max_dim_shift, abs(eff_diff))
        new_dims[k] = round(updated_v, 1)

    # 锁 2: 温和对立压制 (1:0.7 跨极共存保护)
    new_dims = apply_gentle_opposite_suppression(prev_dims, new_dims, effective_gains)

    clean_reason = (reason or "").strip()
    meaningful_shift = (abs(clamped_delta) >= 1.5 or max_dim_shift >= 3.0) and bool(clean_reason)

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
        "updated_at": now if (meaningful_shift or abs(mood_step) >= 0.5 or max_dim_shift >= 1.0) else view["updated_at"],
        "reason_updated_at": now if meaningful_shift else view.get("reason_updated_at", view["updated_at"]),
        "pulse_consumed": False if meaningful_shift else view["pulse_consumed"],
        "decay_noted": False if meaningful_shift else view["decay_noted"],
        "history": history,
    }
    save_mood_state(new_state)
    return new_state


def build_mood_injection_block(current_scene: str = "", consume_pulse: bool = True) -> str:
    """
    【1.5 极简折中派单行 Prompt 注入】
    彻底删除冗长的多行心理旁白以及“不是非这样不可”等卸力引导，改为极简高密度单行注入：
    - 若存在 last_reason 且距离 reason_updated_at <= 3600 秒（1 小时）：
      【当前心情】<映射词>（<极短起因>）
    - 若无起因或已超过 1 小时：
      【当前心情】<映射词>
    """
    view, _ = get_decayed_mood_state()
    mood = view["mood"]
    dims = view["dims"]
    last_reason = (view.get("last_reason") or "").strip()
    reason_ts = float(view.get("reason_updated_at", view.get("updated_at", 0.0)))
    reason_age_sec = max(0.0, time.time() - reason_ts)

    if consume_pulse and not view.get("pulse_consumed", True):
        raw = load_mood_state()
        raw["pulse_consumed"] = True
        save_mood_state(raw)

    mapped_word = lookup_mood_lexicon(mood, dims)
    if last_reason and reason_age_sec <= 3600.0:
        return f"【当前心情】{mapped_word}（{last_reason}）"
    return f"【当前心情】{mapped_word}"


def get_mood_brief_for_observer() -> str:
    """生成供慢车道观察者参考的当前情绪快照简报"""
    view, _ = get_decayed_mood_state()
    active_dims = [f"{DIM_CN_NAMES.get(k, k)}:{v:.0f}" for k, v in view["dims"].items() if v >= 15.0]
    dims_str = "，".join(active_dims) if active_dims else "平静无波澜"
    mapped = lookup_mood_lexicon(view["mood"], view["dims"])
    return f"心境基调={view['mood']:.1f}/100（{mapped}），活跃维度=[{dims_str}]，上次来历=「{view['last_reason'] or '无'}」"
