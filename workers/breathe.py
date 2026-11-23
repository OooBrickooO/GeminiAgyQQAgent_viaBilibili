"""
workers/breathe.py — 双通道时间随机主动冒泡引擎 V4.0 (5A 叠 5C：日常亲切问候 + 公开记忆轻回访 + 多模态彩蛋)

包含两大时间随机主动社交协程（保持高斯随机唤醒，绝非整点打卡）：
1. group_breathe_worker (群聊时间随机冒泡)：
   - 简单亲切问候 & 时段随口招呼 (~35%)：按唤醒当下时段自然解锁早安/午饭/摸鱼/晚饭/晚安/“在干嘛呢”，早晚安每通道每天最多 1 次，且防连续同款复读；
   - 弥酱轻日常碎碎念 (~20%)：拆除旧版“必须结尾硬留开放式提问”的播报腔，说完自然留白；
   - B 站冲浪 / 热搜轻吐槽 (~25%，大幅回调，去主持腔)；
   - 5C 群内公开共同经历轻回访 (~20%，严格隔离私聊与空间隐私，仅读本群公开聊过或下过棋的记录)；
   - 5C 冷场自适应退避：连续 2 次冒泡无人接话自动拉长间隔 (×1.4)，有人开口立刻恢复。
2. private_outreach_worker (私聊时间随机主动问候与日常分享)：
   - 活跃时段微调为 07:30 ~ 23:00，更自然覆盖早安/晚安；
   - 保留四道防骚扰护栏（好友校验、未回熔断、个体冷却、群聊正嗨避让）；
   - 弱化旧版“悬念钓话套路”，提升简单亲切问候（“在干嘛呢”“早呀/晚安啦”“吃饭没”）与按主人/挚友/朋友/熟人分层的日常感至主力 (~40~45%)；
   - 5C 对主人/挚友低概率搭配短语音、点歌或约棋彩蛋。
"""
import os
import time
import json
import random
import datetime
import asyncio
from config import get_allowed_groups, BOT_QQ, MASTER_QQ, CHAT_STATE_FILE
from core.state_manager import (
    LAST_HUMAN_MSG_TIME, LAST_BOT_MSG_TIME,
    RECENT_GROUP_MSGS, check_recent_bot_msg,
    load_chat_state, reset_awake_counter
)
from core.napcat_client import set_group_card, call_onebot_api
from core.agy_runner import ACTIVE_AI_TASKS
from attention.context_builder import build_chat_context
from attention.debounce_queue import trigger_ai, get_current_runtime_status, DEBOUNCE_QUEUES, CHAT_LOCKS
from attention.user_mute import is_user_muted
from trends.trend_manager import TrendManager
from social.affection import (
    load_profiles, get_user_profile,
    get_active_pending_thoughts
)
from social.mid_term_memory import (
    load_mid_term_memories,
    get_recent_user_mid_term_memory,
    _relative_time_label,
)
from social.mood_manager import get_decayed_mood_state, mood_band_phrase, dim_feel_phrase, BASELINE_MOOD
from social.long_term_memory import init_long_term_db, _get_conn

# 记录各群当前名片状态，确保每天仅切换一次
GROUP_CARD_STATE = {}  # gid_str -> "sleep" | "awake"


def _beijing_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


async def sync_group_card_state(gid_str: str, target_state: str):
    """安全同步群名片昼夜作息"""
    if GROUP_CARD_STATE.get(gid_str) == target_state:
        return
    GROUP_CARD_STATE[gid_str] = target_state
    new_card = "星见弥(。-ω-)zzz" if target_state == "sleep" else "星见弥"
    try:
        await set_group_card(int(gid_str), int(BOT_QQ), new_card)
        print(f"🏷️ 群[{gid_str}] 群名片已安全同步为作息态：{new_card}")
    except Exception as e:
        print(f"⚠️ 群[{gid_str}] 同步群名片失败: {e}")


# ============================================================
# 轻量冒泡防复读 / 早晚安每日锁 / 冷场退避持久化 (_breathe_meta)
# ============================================================
def _load_breathe_meta() -> dict:
    today_str = _beijing_now().strftime("%Y-%m-%d")
    default_meta = {
        "today_date": today_str,
        "morning_greeted": [],
        "night_greeted": [],
        "last_hook_kind": {},
        "group_cold_streak": {},
    }
    if not os.path.exists(CHAT_STATE_FILE):
        return default_meta
    try:
        with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta = data.get("_breathe_meta", {}) if isinstance(data, dict) else {}
        if not isinstance(meta, dict) or meta.get("today_date") != today_str:
            default_meta["last_hook_kind"] = meta.get("last_hook_kind", {}) if isinstance(meta, dict) else {}
            default_meta["group_cold_streak"] = meta.get("group_cold_streak", {}) if isinstance(meta, dict) else {}
            return default_meta
        for k, v in default_meta.items():
            meta.setdefault(k, v)
        return meta
    except Exception:
        return default_meta


def _save_breathe_meta(meta: dict):
    try:
        data = {}
        if os.path.exists(CHAT_STATE_FILE):
            try:
                with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        data["_breathe_meta"] = meta
        tmp = f"{CHAT_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CHAT_STATE_FILE)
    except Exception as e:
        print(f"⚠️ 保存冒泡元数据失败: {e}")


def _time_slot_greeting_guide(now_bj: datetime.datetime, chat_id_str: str, meta: dict, is_private: bool = False) -> tuple[str, str]:
    """
    根据当前北京时间段生成自然、无压力的简单亲切问候引导。
    返回: (greet_subkind, prompt_text)
    - 早安/晚安每通道每天最多触发 1 次（超过则自然回落至通用日常问候）。
    """
    hm = now_bj.hour * 60 + now_bj.minute
    morning_done = chat_id_str in (meta.get("morning_greeted") or [])
    night_done = chat_id_str in (meta.get("night_greeted") or [])

    if (6 * 60 + 30) <= hm <= (9 * 60 + 30) and not morning_done:
        if is_private:
            return "morning", (
                "【日常亲切问候·早安】：现在是早上，顺手给对方发一句简单自然的早安问候（比如“早呀”“醒了没”“今天起这么早吗”之类），"
                "一两句简短轻快就好，像朋友刚醒顺手发消息，别说教也别像客服打卡。"
            )
        return "morning", (
            "【日常亲切问候·群里早安】：现在是早上，群里挺安静的，随口在群里打个简单自然的招呼（比如“早呀”“醒了没呀”或顺嘴提一句自己刚睡醒），"
            "简短亲切，一两句就好，别硬塞主持式追问。"
        )

    if (21 * 60 + 30) <= hm <= (22 * 60 + 55) and not night_done:
        if is_private:
            return "night", (
                "【日常亲切问候·晚安】：快到睡觉时间了，随口给对方发一句温柔轻松的睡前问候（比如“还没睡呀”“今天累不累，早点休息”“晚安啦”），"
                "自然亲切、不煽情、一两句就收住。"
            )
        return "night", (
            "【日常亲切问候·群里晚安】：夜深了群里很安静，探个头随口问一句“还没睡呀”或跟大家道句轻松的“晚安啦”，"
            "简短温暖就好，不强求大家接话。"
        )

    if (11 * 60 + 30) <= hm <= (13 * 60 + 30):
        return "lunch", (
            "【日常亲切问候·饭点随口问】：刚好到午饭点，随口问一句“午饭吃啥了”或者“干饭了吗”，也可以顺嘴提一句自己也在纠结吃什么，"
            "就像熟人饭点顺嘴唠一句，轻松自然。"
        )

    if (14 * 60) <= hm <= (17 * 60 + 30):
        return "afternoon", (
            "【日常亲切问候·午后摸鱼】：下午这个点容易犯困摸鱼，随口发一句简单的“在干嘛呢”“下午好困呀”“都在摸鱼吗”，"
            "也可以搭一张轻松的表情包（send_meme），别写长篇大论。"
        )

    if (18 * 60) <= hm <= (20 * 60 + 30):
        return "evening", (
            "【日常亲切问候·傍晚招呼】：傍晚了，随口问一句“下班/放学没”“晚饭吃啥啦”或“晚上打算干嘛呢”，"
            "简短家常、带着一点熟人亲切感。"
        )

    # 通用时段简单问候
    general_prompts = [
        "【日常亲切问候·随口打招呼】：突然想起冒个泡，发一句最简单自然的“在干嘛呢”或“都在忙啥呀”，一两句话、轻松随意，不搞复杂套路。",
        "【日常亲切问候·轻探头】：探个头简单打个招呼（可以顺手用 send_meme 丢一张可爱探头表情包配一句短短的“在干嘛呀”/“冒个泡”），自然亲切就好。",
    ]
    return "general", random.choice(general_prompts)


def _find_public_group_memory_hook(gid_str: str) -> str:
    """
    【5C · 群内公开共同经历轻回访（严格物理隔离私聊与空间隐私）】
    仅扫描 target_id == group_{gid} 或 source_scene == group_{gid} 的公开 L2 记忆（如昨天群里一起下过的棋、公开聊过的趣事），
    绝不触碰任何 private_* 或 qzone_* 记忆。
    """
    group_scene = f"group_{gid_str}"
    now_ts = time.time()
    public_cands = []
    try:
        entries = load_mid_term_memories()
        for e in entries:
            if not isinstance(e, dict):
                continue
            tid = str(e.get("target_id", "")).strip()
            src = str(e.get("source_scene", "")).strip()
            # 严格物理隔离：必须明确来源于本群公开记忆，严禁 private_* / qzone_*
            if src.startswith(("private", "qzone")):
                continue
            if tid != group_scene and src != group_scene:
                continue
            c_ts = float(e.get("created_at", 0.0))
            age_hours = (now_ts - c_ts) / 3600.0 if c_ts else 999.0
            if not (2.0 <= age_hours <= 96.0):
                continue
            txt = str(e.get("text", "")).strip()
            if len(txt) >= 8:
                rel_t = _relative_time_label(c_ts, now_ts)
                public_cands.append((rel_t, txt))
    except Exception:
        pass

    if not public_cands:
        return ""

    rel_t, mem_txt = random.choice(public_cands[-6:])
    return (
        f"【5C·群内公开回忆轻回访】：你突然想起{rel_t}在咱们这个群里公开聊过/发生过的一件事：「{mem_txt}」。\n"
        "顺口在群里轻轻提一嘴调侃或续一句日常感想（就像老群友突然想起昨天的乐子，轻松自然，别像查户口也别硬@人）。"
    )


def _find_bili_or_trend_hook() -> str:
    """
    从今日 B 站冲浪记录或晨间热搜中提取具体吐槽点，去掉“播报员/主持腔”，像真群友随口说一句。
    """
    hook_options = []
    try:
        from workers.bili_worker import get_today_bili_summary
        bili_sums = get_today_bili_summary()
        if bili_sums:
            chosen_bili = random.choice(bili_sums[-3:])
            hook_options.append(
                f"【日常冲浪随口分享】：你今天在 B 站刷到了这个视频：{chosen_bili}。\n"
                "像平时跟群友摸鱼唠嗑那样，只挑里面一个让你觉得好玩或离谱的小点随口吐槽一两句就行，"
                "不用像播报员那样介绍全貌，也不用刻意在句尾硬塞“你们怎么看/大家看过吗”。"
            )
    except Exception:
        pass

    try:
        trends_str = TrendManager.get_cached_trends_text()
        if trends_str and "暂无" not in trends_str:
            lines = [ln.strip() for ln in trends_str.splitlines() if ln.strip() and not ln.startswith("===")]
            if lines:
                picked = random.choice(lines[:8])
                hook_options.append(
                    f"【日常吃瓜随口一嘴】：你刚刷手机瞄到一条热点：「{picked}」。\n"
                    "像同龄群友随口吃瓜那样淡淡吐槽一两句即可，自然留白，绝不要用主持人口吻强行暖场。"
                )
    except Exception:
        pass

    return random.choice(hook_options) if hook_options else ""


def _find_light_daily_slice_hook(now_bj: datetime.datetime) -> str:
    """
    弥酱自己的轻日常碎碎念（结合当下 7D 心情 + 听歌/犯困/嘴馋/下棋等松弛生活碎片）。
    """
    view, _ = get_decayed_mood_state()
    mood = view["mood"]
    dims = view["dims"]
    last_reason = view["last_reason"]
    feel_phrase = mood_band_phrase(mood)
    top_dims = sorted([(k, v) for k, v in dims.items() if v >= 30.0], key=lambda x: x[1], reverse=True)
    dim_str = "，".join([dim_feel_phrase(k, v) for k, v in top_dims[:2] if dim_feel_phrase(k, v)])

    if last_reason and (top_dims or abs(mood - BASELINE_MOOD) >= 8.0):
        return (
            f"【轻日常碎碎念·带着心情探头】：你这会儿{feel_phrase}{('，' + dim_str) if dim_str else ''}（因为早些时候：{last_reason}）。\n"
            "顺着此刻的真实心情在群里随口碎碎念一两句，或者搭一张符合心情的 send_meme 表情包探个头，说完自然留白即可。"
        )

    slices = [
        "你刚听完一首歌有点放空，顺口在群里感叹一句或者随手提一句想找点新歌听，语气松弛。",
        "你这会儿有点小馋或者想摸鱼，随口在群里念叨一句日常小事（比如想喝奶茶/有点犯困/突然手痒想下盘象棋或五子棋），自然简短，不用硬抛问题。",
        "群里安静了挺久，你带着点慵懒日常感出来冒个泡，发一句简短俏皮的话或者配合 send_meme 丢张表情包刷个存在感即可。",
    ]
    return f"【轻日常碎碎念】：{random.choice(slices)}"


def _select_group_cold_hook(gid_str: str, now_bj: datetime.datetime, meta: dict) -> tuple[str, str, str]:
    """
    群聊冷场随机冒泡加权话头选择器 (5A 叠 5C)：
    - greeting (~35%): 简单亲切问候 & 时段随口招呼（防连续重复，早晚安每日限 1 次）
    - daily_slice (~20%): 弥酱轻日常碎碎念
    - bili_trend (~25%): B站/热搜轻吐槽（去主持腔）
    - public_memory (~20%): 5C 群内公开共同经历轻回访（无素材自动回落前三类）
    返回: (hook_kind, greet_subkind, hook_prompt_text)
    """
    chat_id_str = f"group_{gid_str}"
    last_kind = (meta.get("last_hook_kind") or {}).get(chat_id_str, "")

    greet_subkind, greet_prompt = _time_slot_greeting_guide(now_bj, chat_id_str, meta, is_private=False)
    daily_prompt = _find_light_daily_slice_hook(now_bj)
    bili_prompt = _find_bili_or_trend_hook()
    pub_mem_prompt = _find_public_group_memory_hook(gid_str)

    weighted_pool: list[tuple[str, str, str, float]] = []
    # 防止连续两轮都抽中简单问候
    greet_w = 10.0 if last_kind == "greeting" else 35.0
    weighted_pool.append(("greeting", greet_subkind, greet_prompt, greet_w))
    weighted_pool.append(("daily_slice", "", daily_prompt, 20.0))
    if bili_prompt:
        weighted_pool.append(("bili_trend", "", bili_prompt, 25.0))
    else:
        weighted_pool[1] = ("daily_slice", "", daily_prompt, 35.0)
    if pub_mem_prompt:
        weighted_pool.append(("public_memory", "", pub_mem_prompt, 20.0))

    total_w = sum(x[3] for x in weighted_pool)
    r = random.uniform(0.0, total_w)
    acc = 0.0
    chosen = weighted_pool[0]
    for item in weighted_pool:
        acc += item[3]
        if acc >= r:
            chosen = item
            break

    hook_kind, subkind, prompt_txt, _ = chosen

    # 5C 低概率多模态轻量彩蛋 (~15%)
    if random.random() < 0.15:
        prompt_txt += (
            "\n（5C 可选轻彩蛋：如果契合当下气氛，也可以顺手搭一张 send_meme 表情包、发一条极短的自然语音 send_voice_msg、"
            "或者随口问一句有没有人来下盘五子棋/象棋；不顺手就纯文字，完全不必勉强。）"
        )
    return hook_kind, subkind, prompt_txt


async def group_breathe_worker(group_id_str: str):
    """独立群组的时间随机主动社交状态机 (V4.0 — 5A 叠 5C)"""
    gid_str = str(group_id_str)
    chat_id_str = f"group_{gid_str}"
    while True:
        if gid_str not in get_allowed_groups():
            await asyncio.sleep(60)
            continue

        now = _beijing_now()

        # 【夜间绝对静默 23:00 ~ 06:00】
        if now.hour >= 23 or now.hour < 6:
            asyncio.create_task(sync_group_card_state(gid_str, "sleep"))
            if now.hour >= 23:
                target_6am = (now + datetime.timedelta(days=1)).replace(hour=6, minute=random.randint(5, 30), second=0, microsecond=0)
            else:
                target_6am = now.replace(hour=6, minute=random.randint(5, 30), second=0, microsecond=0)
            sleep_time = max(60, (target_6am - now).total_seconds())
            print(f"🌙 群[{gid_str}] 进入夜间睡觉期 (23:00~06:00)，关闭主动冒泡，深睡至早晨 (约 {sleep_time/3600:.1f} 小时)")
            await asyncio.sleep(sleep_time)
            continue

        asyncio.create_task(sync_group_card_state(gid_str, "awake"))
        asyncio.create_task(TrendManager.fetch_and_cache_morning_trends())

        # 5C 冷场自适应退避：若该群连续 >=2 次冒泡无人接话，自动拉长下次随机间隔 ×1.4
        meta_pre = _load_breathe_meta()
        cold_streak = int((meta_pre.get("group_cold_streak") or {}).get(gid_str, 0))
        backoff_mult = 1.4 if cold_streak >= 2 else 1.0

        sleep_minutes = max(140, min(230, random.gauss(185, 25))) * backoff_mult
        sleep_time = sleep_minutes * 60
        backoff_note = f" [冷场退避×{backoff_mult:.1f}]" if backoff_mult > 1.0 else ""
        print(f"⏱️ 群[{gid_str}] 进入随机休眠{backoff_note}，预计 {sleep_time/3600:.1f} 小时 ({sleep_minutes:.0f} 分钟) 后苏醒")
        await asyncio.sleep(sleep_time)

        if gid_str not in get_allowed_groups():
            continue

        now_wake = _beijing_now()
        if now_wake.hour >= 23 or now_wake.hour < 6:
            asyncio.create_task(sync_group_card_state(gid_str, "sleep"))
            if now_wake.hour >= 23:
                target_6am = (now_wake + datetime.timedelta(days=1)).replace(hour=6, minute=random.randint(5, 30), second=0, microsecond=0)
            else:
                target_6am = now_wake.replace(hour=6, minute=random.randint(5, 30), second=0, microsecond=0)
            sleep_time = max(60, (target_6am - now_wake).total_seconds())
            await asyncio.sleep(sleep_time)
            continue

        # 物理复核磁盘状态，严防自言自语 & 更新冷场退避计数
        load_chat_state()
        last_human_t = LAST_HUMAN_MSG_TIME.get(gid_str, 0.0)
        last_bot_t = LAST_BOT_MSG_TIME.get(gid_str, 0.0)
        meta = _load_breathe_meta()

        if (last_bot_t >= last_human_t and last_bot_t > 0) or (last_human_t == 0.0) or check_recent_bot_msg(gid_str, n=1):
            # 无人接话 -> 递增冷场退避计数
            streak_map = meta.setdefault("group_cold_streak", {})
            streak_map[gid_str] = min(5, int(streak_map.get(gid_str, 0)) + 1)
            _save_breathe_meta(meta)
            print(f"🫁 群[{gid_str}] 呼吸取消：群内最后一条发言仍是弥酱或人类未接话 (冷场计数={streak_map[gid_str]})，坚决不自言自语。")
            continue
        else:
            # 人类有发言 -> 立即清零冷场退避计数
            if (meta.get("group_cold_streak") or {}).get(gid_str, 0) > 0:
                meta.setdefault("group_cold_streak", {})[gid_str] = 0
                _save_breathe_meta(meta)

        now_ts = time.time()
        recent = list(RECENT_GROUP_MSGS[gid_str])
        active_history = [m for m in recent if (now_ts - m.get("time", now_ts)) <= 600]

        meaningful_chat = False
        for m in active_history:
            if not m.get("is_bot", False):
                txt = m.get("text", "").strip()
                if len(txt) > 2 and txt not in [".", "。", "？", "?", "！", "!", "…"]:
                    meaningful_chat = True
                    break

        bot_recent = [
            m.get("text", "").strip()
            for m in list(RECENT_GROUP_MSGS.get(gid_str, []))
            if m.get("is_bot") and len(m.get("text", "").strip()) > 1
        ]
        bot_recent_texts = bot_recent[-4:]
        repetition_hint = ""
        if bot_recent_texts:
            repetition_hint = (
                "\n【你近期在该群发表过的发言记录（本次请换全新话题与句式，严禁雷同）】：\n"
                + "\n".join(f"- {txt}" for txt in bot_recent_texts)
            )

        breathe_active_angles = [
            "顺着大家当前聊得最热的焦点，轻飘飘送上一句精准幽默的补刀或随性调侃（自然融入氛围）。",
            "带着点看戏的小腹黑，抓住刚才聊天中某个群友的小口误、搞笑发言或下饭操作进行打趣。",
            "像个随性自在的同龄群友一样，顺口插一句表达自己的即兴看法或赞同。"
        ]

        if not meaningful_chat:
            hook_kind, subkind, chosen_hook = _select_group_cold_hook(gid_str, now_wake, meta)
            # 更新防复读与早晚安每日锁
            meta.setdefault("last_hook_kind", {})[chat_id_str] = hook_kind
            if subkind == "morning" and chat_id_str not in meta.setdefault("morning_greeted", []):
                meta["morning_greeted"].append(chat_id_str)
            elif subkind == "night" and chat_id_str not in meta.setdefault("night_greeted", []):
                meta["night_greeted"].append(chat_id_str)
            _save_breathe_meta(meta)

            ephemeral_context = (
                f"{chosen_hook}\n"
                "【交流原则】：保持真人随口发消息的松弛感和亲切度，简短自然即可，不要硬塞客服式/主持式追问；严禁调用生图工具。"
                f"{repetition_hint}"
            )
            hook_tag = f"{hook_kind}:{subkind}" if subkind else hook_kind
        else:
            chosen_angle = random.choice(breathe_active_angles)
            ephemeral_context = (
                f"你刚切进群，发现大家刚才在闲聊热闹。\n"
                f"本次切入视角：{chosen_angle}\n"
                "【定向回复指南】：若针对某位群友之前的特定有趣发言接话，可在 send_group_msg 中传入 reply_to=目标msg_id 明确对象；若只是顺应氛围吐槽，直接发言即可，坚决不要多余@人！\n"
                f"{repetition_hint}"
            )
            hook_tag = "active_chat_join"

        if chat_id_str in ACTIVE_AI_TASKS or chat_id_str in DEBOUNCE_QUEUES:
            print(f"🫁 群[{gid_str}] 呼吸避让：通道正在思考或有待处理消息，取消本次呼吸冒泡。")
            continue

        context_part = build_chat_context(gid_str)
        combined_message = (
            f"{context_part}\n\n"
            "【当前触发状态】：内部时间随机主动冒泡（当前无人在直接呼叫你）。\n"
            "请顺着当前情境自然轻松地开口，像熟人随手冒个泡一样亲切。"
        )

        print(f"🫁 群[{gid_str}] 触发时间随机冒泡 [{hook_tag}]！| {get_current_runtime_status()}")

        async def _run_breathe_locked():
            if chat_id_str not in CHAT_LOCKS:
                CHAT_LOCKS[chat_id_str] = asyncio.Lock()
            async with CHAT_LOCKS[chat_id_str]:
                if chat_id_str in DEBOUNCE_QUEUES or chat_id_str in ACTIVE_AI_TASKS:
                    return
                reset_awake_counter(gid_str)
                await trigger_ai(
                    chat_id_str,
                    int(gid_str),
                    "系统",
                    combined_message,
                    is_breathe=True,
                    ephemeral_context=ephemeral_context
                )

        asyncio.create_task(_run_breathe_locked())


# ============================================================
# 通道二：私聊时间随机主动问候与日常分享引擎 (Private Outreach Worker)
# ============================================================

def _load_outreach_meta() -> dict:
    """从 chat_state.json 中读取私聊主动出击追踪元数据"""
    today_str = _beijing_now().strftime("%Y-%m-%d")
    default_meta = {
        "today_date": today_str,
        "today_total_count": 0,
        "today_master_count": 0,
        "user_last_outreach_ts": {}
    }
    if not os.path.exists(CHAT_STATE_FILE):
        return default_meta
    try:
        with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta = data.get("_private_outreach_meta", {})
        if not isinstance(meta, dict) or meta.get("today_date") != today_str:
            default_meta["user_last_outreach_ts"] = meta.get("user_last_outreach_ts", {}) if isinstance(meta, dict) else {}
            return default_meta
        return meta
    except Exception:
        return default_meta


def _save_outreach_meta(meta: dict):
    """原子更新私聊主动出击元数据至 chat_state.json"""
    try:
        data = {}
        if os.path.exists(CHAT_STATE_FILE):
            try:
                with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["_private_outreach_meta"] = meta
        tmp = f"{CHAT_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CHAT_STATE_FILE)
    except Exception as e:
        print(f"⚠️ 保存私聊主动社交元数据失败: {e}")


async def _fetch_qq_friend_map() -> dict:
    """拉取当前机器人真实好友列表，返回 {uid_str: nickname}"""
    friend_map = {str(MASTER_QQ): "主人"}
    try:
        resp = await call_onebot_api("/get_friend_list", {}, timeout=8.0)
        if resp.get("status") == "ok":
            for item in (resp.get("data") or []):
                uid = str(item.get("user_id", "")).strip()
                nick = item.get("remark") or item.get("nickname") or uid
                if uid and uid != str(BOT_QQ):
                    friend_map[uid] = nick
    except Exception:
        pass
    return friend_map


def _get_user_recent_group_quote(uid_str: str) -> str:
    """查找目标用户过去 24 小时内在任意白名单群说过的一两句有趣发言"""
    now_ts = time.time()
    quotes = []
    for gid in get_allowed_groups():
        for m in reversed(list(RECENT_GROUP_MSGS.get(str(gid), []))):
            if str(m.get("sender_id", "")) == uid_str and not m.get("is_bot"):
                if now_ts - float(m.get("time", 0.0)) <= 86400.0:
                    txt = str(m.get("text", "")).strip()
                    if len(txt) >= 4 and "[CQ:" not in txt:
                        quotes.append(txt)
                        if len(quotes) >= 2:
                            break
    if quotes:
        return " / ".join(quotes[:2])

    try:
        init_long_term_db()
        conn = _get_conn()
        today_str = _beijing_now().strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT raw_text FROM chat_archive_fts
               WHERE sender_id = ? AND date_str = ? AND chat_id LIKE 'group_%'
               ORDER BY time_str DESC LIMIT 2""",
            (uid_str, today_str)
        ).fetchall()
        conn.close()
        if rows:
            return " / ".join([r["raw_text"] for r in rows if len(r["raw_text"]) >= 4])
    except Exception:
        pass
    return ""


def _build_private_lure_prompt(
    uid_str: str,
    nickname: str,
    aff: float,
    now_bj: datetime.datetime = None,
    breathe_meta: dict = None,
) -> tuple[str, str, str]:
    """
    私聊时间随机主动问候与日常分享选择器 (V4.0 — 5A 叠 5C)：
    - 大幅提升简单亲切问候（“在干嘛呢”“早呀/晚安啦”“吃饭没”）+ 按亲疏分层的日常感至主力 (~40~45%)；
    - 搭配群聊延伸悄悄话、挂念回访、B站分享与低概率 5C 多模态彩蛋；
    返回: (tactic_name, greet_subkind, ephemeral_context_str)
    """
    now_dt = now_bj or _beijing_now()
    meta = breathe_meta if isinstance(breathe_meta, dict) else _load_breathe_meta()
    chat_id_str = f"private_{uid_str}"
    is_master = (uid_str == str(MASTER_QQ))
    is_bff = (aff >= 850.0)
    is_friend = (aff >= 500.0)

    # 亲疏分层语气锚点
    if is_master:
        tone_hint = "面对最亲近的主人，语气黏人随性、可以轻轻撒个娇或顺手报备一句自己刚在摸鱼干嘛。"
    elif is_bff:
        tone_hint = f"面对铁杆挚友 {nickname}，语气像损友熟人一样自然直爽，随手唠两句日常或丢张梗图。"
    elif is_friend:
        tone_hint = f"面对关系不错的朋友 {nickname}，语气轻松亲切、没有压力，像老友平时发条简短问候。"
    else:
        tone_hint = f"面对刚熟络起来的 {nickname}，语气礼貌轻快、简单自然，不越界不油腻。"

    weighted_tactics: list[tuple[str, str, str, float]] = []

    # 1. 主力 (~42%): 简单亲切问候 & 时段日常报备（彻底告别“禁止简单问好”的刻意钓话）
    greet_subkind, base_greet = _time_slot_greeting_guide(now_dt, chat_id_str, meta, is_private=True)
    last_kind = (meta.get("last_hook_kind") or {}).get(chat_id_str, "")
    greet_weight = 22.0 if last_kind == "private_greeting" else 44.0
    weighted_tactics.append((
        f"日常亲切问候·{greet_subkind}",
        greet_subkind,
        f"{base_greet}\n{tone_hint}\n（自然发一两句短消息即可，不必刻意留悬念套路。）",
        greet_weight,
    ))

    # 2. 群聊延伸的“私下开小灶 / 悄悄话”
    group_quote = _get_user_recent_group_quote(uid_str)
    if group_quote:
        weighted_tactics.append((
            "群聊延伸·私下唠两句",
            "",
            f"【主动私聊·顺着群聊搭话】：你注意到 {nickname}(QQ:{uid_str}) 今天在群里说过：「{group_quote}」。\n"
            f"跑来私聊顺嘴接一句或者打趣两句，{tone_hint}",
            22.0,
        ))

    # 3. 记忆闪回 / 挂念轻回访
    pts = get_active_pending_thoughts(uid_str, current_scene=chat_id_str)
    mt_mem = get_recent_user_mid_term_memory(uid_str, max_age_days=6.0, current_scene=chat_id_str)
    mem_hook = pts[0].get("thought", "") if pts else mt_mem
    if mem_hook:
        weighted_tactics.append((
            "挂念回访·关心近况",
            "",
            f"【主动私聊·挂念回访】：你心里还记着 {nickname}(QQ:{uid_str}) 之前提过的这件事：「{mem_hook}」。\n"
            f"发条私聊顺嘴问问后来怎么样了，语气自然轻快，别像例行查岗。{tone_hint}",
            20.0,
        ))

    # 4. B站新鲜手账轻投喂
    try:
        from workers.bili_worker import get_today_bili_summary
        bili_sums = get_today_bili_summary()
        if bili_sums:
            picked_bili = random.choice(bili_sums[-3:])
            weighted_tactics.append((
                "B站好物·私下分享",
                "",
                f"【主动私聊·随手分享】：你今天在 B 站刷到了这个视频：{picked_bili}。\n"
                f"觉得 {nickname} 可能会觉得好玩，顺手私聊分享一两句槽点。{tone_hint}",
                14.0,
            ))
    except Exception:
        pass

    # 5. 轻日常报备 / 心情分享（主人 & 挚友）
    if is_master or is_bff:
        view, _ = get_decayed_mood_state()
        feel_str = mood_band_phrase(view["mood"])
        weighted_tactics.append((
            "日常碎碎念·探头搭话",
            "",
            f"【主动私聊·日常碎碎念】：你这会儿{feel_str}，看 {nickname} 半天没冒头，顺手发个私聊唠两句日常。\n"
            f"可搭一张 send_meme 探头表情包，或者随口问他在干嘛。{tone_hint}",
            16.0,
        ))

    total_w = sum(x[3] for x in weighted_tactics)
    r = random.uniform(0.0, total_w)
    acc = 0.0
    chosen = weighted_tactics[0]
    for item in weighted_tactics:
        acc += item[3]
        if acc >= r:
            chosen = item
            break

    tactic_name, subkind, prompt_text, _ = chosen

    # 5C 低概率多模态彩蛋（仅主人/挚友 ~18%）：早晚安/犯困可搭短语音、或推歌/约一盘象棋五子棋
    if (is_master or is_bff) and random.random() < 0.18:
        prompt_text += (
            "\n（5C 可选轻彩蛋：如果此刻气氛很贴合，可以顺手发一条极短的自然语音 send_voice_msg、"
            "或者分享一首好听的歌、随口约一盘五子棋/象棋；不顺手就直接发文字，自然第一。）"
        )

    return tactic_name, subkind, prompt_text


async def private_outreach_worker():
    """
    私聊时间随机主动问候与日常分享后台协程：
    白天活跃时段 (07:30 ~ 23:00) 随机苏醒巡检，严守四道防骚扰护栏。
    """
    await asyncio.sleep(900)

    while True:
        try:
            now = _beijing_now()
            hm = now.hour * 60 + now.minute
            # 仅在 07:30 ~ 23:00 活跃时段主动发起私聊（自然覆盖早安/晚安）
            if hm < (7 * 60 + 30) or hm >= (23 * 60):
                await asyncio.sleep(1800)
                continue

            # 随机高斯间隔 2.2 ~ 4.0 小时苏醒检查一次
            sleep_mins = max(130, min(240, random.gauss(175, 25)))
            print(f"💌 [私聊主动引擎] 进入随机休眠，预计 {sleep_mins:.0f} 分钟后寻觅私聊问候目标...")
            await asyncio.sleep(sleep_mins * 60)

            now = _beijing_now()
            hm_wake = now.hour * 60 + now.minute
            if hm_wake < (7 * 60 + 30) or hm_wake >= (23 * 60):
                continue

            meta = _load_outreach_meta()
            if int(meta.get("today_total_count", 0)) >= 4:
                continue

            load_chat_state()
            load_profiles()
            friend_map = await _fetch_qq_friend_map()
            now_ts = time.time()
            last_outreach_map = meta.get("user_last_outreach_ts", {})

            weighted_candidates = []

            for uid_str, nickname in friend_map.items():
                if is_user_muted(uid_str):
                    continue

                is_master = (uid_str == str(MASTER_QQ))
                p = get_user_profile(uid_str)
                aff = 1000.0 if is_master else float(p.get("affection", 0.0))

                # 护栏一：好感度门槛 (熟人 >= 200 及以上)
                if not is_master and aff < 200.0:
                    continue

                priv_key = f"private_{uid_str}"
                last_bot_t = LAST_BOT_MSG_TIME.get(priv_key, 0.0)
                last_human_t = LAST_HUMAN_MSG_TIME.get(priv_key, 0.0)

                # 护栏二：【未回熔断铁律 (Ghosting Fuse)】
                if last_bot_t > last_human_t and last_bot_t > 0:
                    if not is_master:
                        continue
                    if (now_ts - last_bot_t) < 8 * 3600:
                        continue

                # 护栏三：个体冷却周期核验
                last_out_ts = float(last_outreach_map.get(uid_str, 0.0))
                if is_master:
                    if int(meta.get("today_master_count", 0)) >= 2:
                        continue
                    cooldown_sec = 4.5 * 3600
                    weight = 45
                elif aff >= 850.0:
                    cooldown_sec = 24 * 3600
                    weight = 28
                elif aff >= 500.0:
                    cooldown_sec = 40 * 3600
                    weight = 20
                else:
                    cooldown_sec = 72 * 3600
                    weight = 8

                if (now_ts - last_out_ts) < cooldown_sec:
                    continue
                if (now_ts - max(last_bot_t, last_human_t)) < 3600:
                    continue

                # 护栏四：若该用户最近 20 分钟刚在群里活跃互动过，暂缓私聊
                recently_in_group = False
                for gid in get_allowed_groups():
                    for m in list(RECENT_GROUP_MSGS.get(str(gid), []))[-10:]:
                        if str(m.get("sender_id", "")) == uid_str and (now_ts - float(m.get("time", 0.0))) < 1200:
                            recently_in_group = True
                            break
                if recently_in_group:
                    continue

                weighted_candidates.append((uid_str, nickname, aff, weight))

            if not weighted_candidates:
                print("💌 [私聊主动引擎] 当前无符合防打扰护栏的候选好友，本轮跳过。")
                continue

            total_w = sum(item[3] for item in weighted_candidates)
            r_val = random.uniform(0, total_w)
            upto = 0.0
            chosen_target = weighted_candidates[0]
            for item in weighted_candidates:
                upto += item[3]
                if upto >= r_val:
                    chosen_target = item
                    break

            target_uid, target_nick, target_aff, _ = chosen_target
            b_meta = _load_breathe_meta()
            tactic_name, greet_subkind, lure_ephemeral = _build_private_lure_prompt(
                target_uid, target_nick, target_aff, now_bj=now, breathe_meta=b_meta
            )
            chat_id_str = f"private_{target_uid}"

            if chat_id_str in ACTIVE_AI_TASKS or chat_id_str in DEBOUNCE_QUEUES:
                continue

            # 更新防复读与早晚安每日锁
            b_meta.setdefault("last_hook_kind", {})[chat_id_str] = (
                "private_greeting" if tactic_name.startswith("日常亲切问候") else tactic_name
            )
            if greet_subkind == "morning" and chat_id_str not in b_meta.setdefault("morning_greeted", []):
                b_meta["morning_greeted"].append(chat_id_str)
            elif greet_subkind == "night" and chat_id_str not in b_meta.setdefault("night_greeted", []):
                b_meta["night_greeted"].append(chat_id_str)
            _save_breathe_meta(b_meta)

            combined_msg = (
                f"【当前触发状态】：内部时间随机私聊问候（视角：{tactic_name} | 目标好友：{target_nick}({target_uid})）。\n"
                "请务必调用 send_private_msg（或配合 send_meme / send_voice_msg）自然亲切地发出这条私聊消息！"
            )

            print(f"💌 [私聊主动出击] 选中目标 {target_nick}({target_uid}, 好感:{target_aff:.0f}) | 视角: 【{tactic_name}】")

            meta["today_total_count"] = int(meta.get("today_total_count", 0)) + 1
            if target_uid == str(MASTER_QQ):
                meta["today_master_count"] = int(meta.get("today_master_count", 0)) + 1
            meta.setdefault("user_last_outreach_ts", {})[target_uid] = now_ts
            _save_outreach_meta(meta)

            if chat_id_str not in CHAT_LOCKS:
                CHAT_LOCKS[chat_id_str] = asyncio.Lock()
            async with CHAT_LOCKS[chat_id_str]:
                await trigger_ai(
                    chat_id_str=chat_id_str,
                    group_id=0,
                    sender_id=target_uid,
                    combined_message=combined_msg,
                    is_breathe=True,
                    is_private=True,
                    must_reply=True,
                    ephemeral_context=lure_ephemeral
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ 私聊主动社交引擎异常: {e}")
            await asyncio.sleep(120)


async def breathe_manager():
    """管理所有白名单群的独立呼吸协程 + 私聊主动问候协程"""
    running_workers = {}
    private_worker_task = asyncio.create_task(private_outreach_worker())

    while True:
        try:
            if private_worker_task.done():
                private_worker_task = asyncio.create_task(private_outreach_worker())

            allowed = get_allowed_groups()
            for gid in allowed:
                if gid not in running_workers or running_workers[gid].done():
                    running_workers[gid] = asyncio.create_task(group_breathe_worker(gid))

            for running_gid in list(running_workers.keys()):
                if running_gid not in allowed:
                    task = running_workers.pop(running_gid)
                    if not task.done():
                        task.cancel()
                    print(f"🛑 [呼吸管理器] 群[{running_gid}] 已移出白名单，已终止其定时呼吸任务。")
        except Exception as e:
            print(f"⚠️ 呼吸状态机管理器异常: {e}")
        await asyncio.sleep(60)
