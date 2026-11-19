"""
workers/breathe.py — 双通道主动社交引擎 V3.0 (群聊真实话头驱动 + 私聊主动打招呼与“勾引对话”引擎)

包含两大主动社交协程：
1. group_breathe_worker (群聊主动冒泡去人机化)：
   - 彻底废除旧版 10 条“15字看窗外/伸懒腰/发呆”无病呻吟独白！
   - 修复原版群安静时找不到群友心事 (`pending_thoughts`) 的 Bug，扫描本群近 48 小时活跃熟人/朋友的未结心事与 L2 温感记忆进行点名回访；
   - 结合今日 B 站手账 / 晨间热搜抛出带互动提问钩子的话题，或顺着当下 `feiyu-mood` 情绪与表情包自然探头。
2. private_outreach_worker (私聊主动打招呼与“勾引对话”引擎)：
   - 白天活跃时段 (08:30 ~ 22:30) 不定时向 👑主人、💖挚友(>=850)、🤝朋友(>=500) 及 ✨熟人(>=200) 主动发起私聊破冰；
   - 严守四道防骚扰护栏（好友校验、未回熔断铁律 Ghosting Fuse `last_bot_t > last_human_t`、个体冷却周期、群聊正嗨避让）；
   - 内置五大“勾引对话 (Lure & Hook)”战术剧本（群聊延伸背地里开小灶、欲言又止好奇心钩子、挂念突袭、精准投喂、情绪撒娇招惹）。
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
    USER_PROFILES, load_profiles, get_user_profile,
    get_active_pending_thoughts
)
from social.mid_term_memory import get_recent_user_mid_term_memory
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


def _find_group_friends_followup_hook(gid_str: str) -> str:
    """
    扫描本群近 48 小时内发过言、好感度 >= 200（熟人/朋友/挚友/主人）且未开启静默的群友，
    提取其未结心事 (`pending_thoughts`) 或近几天的 L2 中程温感记忆 (`mid_term_memory`)，
    生成真实自然的【点名回访/翻牌子】话头！
    """
    now_ts = time.time()
    recent_msgs = list(RECENT_GROUP_MSGS.get(gid_str, []))
    seen_speakers = {}  # uid -> nickname

    # 1. 从内存滑窗收集近期发言群友
    for m in reversed(recent_msgs):
        if m.get("is_bot"):
            continue
        uid = str(m.get("sender_id", "")).strip()
        if uid and uid != str(BOT_QQ) and uid not in seen_speakers:
            seen_speakers[uid] = m.get("nickname") or uid

    # 2. 若滑窗人数较少，从 SQLite chat_archive_fts 补充本群近 2 天发言人
    if len(seen_speakers) < 5:
        try:
            init_long_term_db()
            conn = _get_conn()
            d_today = _beijing_now().strftime("%Y-%m-%d")
            d_yest = (_beijing_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            rows = conn.execute(
                """SELECT DISTINCT sender_id, sender_name FROM chat_archive_fts
                   WHERE chat_id = ? AND date_str IN (?, ?) AND sender_id != ?
                   LIMIT 20""",
                (f"group_{gid_str}", d_today, d_yest, str(BOT_QQ))
            ).fetchall()
            conn.close()
            for r in rows:
                uid = str(r["sender_id"]).strip()
                if uid and uid not in seen_speakers:
                    raw_name = str(r["sender_name"]).split("(")[0].strip()
                    seen_speakers[uid] = raw_name or uid
        except Exception:
            pass

    candidates = []
    for uid, nick in seen_speakers.items():
        if is_user_muted(uid):
            continue
        p = get_user_profile(uid)
        aff = float(p.get("affection", 0.0))
        if aff < 200.0 and uid != str(MASTER_QQ):
            continue

        pts = get_active_pending_thoughts(uid, current_scene=f"group_{gid_str}")
        if pts:
            t_text = pts[0].get("thought", "")
            if t_text:
                candidates.append((nick, uid, f"心里惦记的小事：{t_text}"))
                continue

        mt_mem = get_recent_user_mid_term_memory(uid, max_age_days=4.0, current_scene=f"group_{gid_str}")
        if mt_mem:
            candidates.append((nick, uid, f"近期聊过的事：{mt_mem}"))

    if not candidates:
        return ""

    nick, uid, hook_detail = random.choice(candidates)
    return (
        f"【真实话头·老友近况回访】：群里这会儿挺安静的，你突然想起群友 {nick}(QQ:{uid}) 之前提过的这件事——「{hook_detail}」。\n"
        f"请直接在群里随口点名 {nick}（直接叫他名字即可，不必死板加@）问问他这件事后来怎么样了，或者顺着调侃一句，把群里的气氛自然带起来！"
    )


def _find_bili_or_trend_hook() -> str:
    """
    从今日 B 站冲浪记录或晨间热搜中提取有具体细节和槽点的话头，
    引导弥酱抛出一个具体奇葩点并附带开放式小问题引群友接话。
    """
    hook_options = []
    try:
        from workers.bili_worker import get_today_bili_summary
        bili_sums = get_today_bili_summary()
        if bili_sums:
            chosen_bili = random.choice(bili_sums[-3:])
            hook_options.append(
                f"【真实话头·B站冲浪抛梗】：你今天在 B 站刷到了这个视频：{chosen_bili}。\n"
                "请不要只发一句干巴巴的感叹！挑里面一个最有趣或最让你想吐槽的具体点跟群友分享一两句，"
                "并在结尾顺势留一个容易接茬的日常开放式小问题，吸引大家出来接话！"
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
                    f"【真实话头·吃瓜热搜抛梗】：你刚刚在网上冲浪瞄到了这条热点：「{picked}」。\n"
                    "请用同龄群友吃瓜吐槽的自然口吻在群里提一嘴这件事，顺便问一句群友们有没有关注到这个瓜，引发大家讨论！"
                )
    except Exception:
        pass

    return random.choice(hook_options) if hook_options else ""


def _find_mood_driven_hook() -> str:
    """
    读取当前 feiyu-mood 情绪状态，生成带着真实情绪色彩与表情包的探头话头。
    """
    view, _ = get_decayed_mood_state()
    mood = view["mood"]
    dims = view["dims"]
    last_reason = view["last_reason"]
    feel_phrase = mood_band_phrase(mood)
    top_dims = sorted([(k, v) for k, v in dims.items() if v >= 15.0], key=lambda x: x[1], reverse=True)
    dim_str = "，".join([dim_feel_phrase(k, v) for k, v in top_dims[:2] if dim_feel_phrase(k, v)])

    if last_reason and (top_dims or abs(mood - BASELINE_MOOD) >= 8.0):
        return (
            f"【真实话头·带着情绪探头】：你这会儿{feel_phrase}{('，' + dim_str) if dim_str else ''}（底子是因为早些时候：{last_reason}）。\n"
            "看群里这么安静，你决定出来冒个泡！你可以顺着当下的心情跟大家搭句话、求安利一首歌或好玩的游戏，"
            "或者直接配合调用 send_meme 工具丢一张符合你现在心情的表情包出来探头！"
        )

    return (
        "【真实话头·冒头找乐子】：群里安静了好一会儿，你决定主动抛个接地气的日常互动话题出来暖场，"
        "也可以顺手调 send_meme 发张探头表情包；"
        "一定要带一个让群友容易顺口接茬的话题钩子，严禁发没人接得上话的无病呻吟独白！"
    )


async def group_breathe_worker(group_id_str: str):
    """独立群组的主动社交状态机 (Layer 1 — 真实话头驱动版)"""
    gid_str = str(group_id_str)
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
        sleep_minutes = max(140, min(230, random.gauss(185, 25)))
        sleep_time = sleep_minutes * 60
        print(f"⏱️ 群[{gid_str}] 进入休眠，预计 {sleep_time/3600:.1f} 小时 ({sleep_minutes:.0f} 分钟) 后苏醒")
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

        # 物理复核磁盘状态，严防自言自语
        load_chat_state()
        last_human_t = LAST_HUMAN_MSG_TIME.get(gid_str, 0.0)
        last_bot_t = LAST_BOT_MSG_TIME.get(gid_str, 0.0)

        if (last_bot_t >= last_human_t and last_bot_t > 0) or (last_human_t == 0.0) or check_recent_bot_msg(gid_str, n=1):
            print(f"🫁 群[{gid_str}] 呼吸取消：群内最后一条发言仍是弥酱或人类未接话，坚决不自言自语。")
            continue

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
            "像个随性自在的同龄群友一样，顺口插一句表达自己的即兴看法或赞同，也可以顺手追问一句细节。"
        ]

        if not meaningful_chat:
            # 组装真实话头池（B站与热搜抛梗 / 情绪与表情包探头；群安静时不点名追问个人心事，仅在对方发言或私聊时顺口提）
            hooks = []
            bili_trend_hook = _find_bili_or_trend_hook()
            if bili_trend_hook:
                hooks.extend([bili_trend_hook, bili_trend_hook])
            hooks.append(_find_mood_driven_hook())

            chosen_hook = random.choice(hooks)
            ephemeral_context = (
                f"{chosen_hook}\n"
                "【交流原则】：说话要有具体内容和让人好接话的钩子，可开启 auto_split=True；严禁调用生图工具。"
                f"{repetition_hint}"
            )
        else:
            chosen_angle = random.choice(breathe_active_angles)
            ephemeral_context = (
                f"你刚切进群，发现大家刚才在闲聊热闹。\n"
                f"本次切入视角：{chosen_angle}\n"
                "【定向回复指南】：若针对某位群友之前的特定有趣发言接话，可在 send_group_msg 中传入 reply_to=目标msg_id 明确对象；若只是顺应氛围吐槽，直接发言即可，坚决不要多余@人！\n"
                f"{repetition_hint}"
            )

        chat_id_str = f"group_{gid_str}"
        if chat_id_str in ACTIVE_AI_TASKS or chat_id_str in DEBOUNCE_QUEUES:
            print(f"🫁 群[{gid_str}] 呼吸避让：通道正在思考或有待处理消息，取消本次呼吸冒泡。")
            continue

        context_part = build_chat_context(gid_str)
        combined_message = (
            f"{context_part}\n\n"
            "【当前触发状态】：内部主动社交破冰（当前无人在直接呼叫你）。\n"
            "请遵循当前触发情境中的话头自然开口，把气氛带起来！"
        )

        print(f"🫁 群[{gid_str}] 触发 Layer 1 话头驱动冒泡！| {get_current_runtime_status()}")

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
# 通道二：私聊主动打招呼与“勾引对话”引擎 (Private Outreach Worker)
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
    """查找目标用户过去 24 小时内在任意白名单群说过的一两句有趣发言（用于战术1：私下开小灶悄悄话）"""
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

    # 从 SQLite 历史归档补充查询今日该用户在群里的发言
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


def _build_private_lure_prompt(uid_str: str, nickname: str, aff: float) -> tuple[str, str]:
    """
    五大主动破冰话头剧本选择器：
    根据目标用户的关系阶段、今日群聊表现、挂念心事与弥酱当前情绪，
    挑选最能激发对方回复欲的私聊破冰视角！
    返回 (tactic_name, ephemeral_context_str)
    """
    is_master = (uid_str == str(MASTER_QQ))
    is_bff = (aff >= 850.0)
    tactics_pool = []

    # 战术 1：【群聊延伸的“私下开小灶 / 悄悄话”】（只要今天在群里发过言，真实感最强，高权重！）
    group_quote = _get_user_recent_group_quote(uid_str)
    if group_quote:
        tactics_pool.append((
            "群聊延伸·私下开小灶",
            f"【主动私聊·私下说悄悄话】：你注意到 {nickname}(QQ:{uid_str}) 今天在群里说过：「{group_quote}」。\n"
            f"你决定现在跑来【私聊】找他单独聊聊这件事！你可以顺着他刚才在群里的发言打趣调侃，或者就他提到的那个话题私下细聊两句，"
            f"语气里透出在群外单独找他聊悄悄话的熟稔与默契感！"
        ))
        tactics_pool.append(tactics_pool[-1])  # 双倍权重

    # 战术 2：【记忆闪回 / 挂念回访】（有未结心事或 L2 近期温感记忆时触发）
    pts = get_active_pending_thoughts(uid_str, current_scene=f"private_{uid_str}")
    mt_mem = get_recent_user_mid_term_memory(uid_str, max_age_days=6.0, current_scene=f"private_{uid_str}")
    mem_hook = pts[0].get("thought", "") if pts else mt_mem
    if mem_hook:
        tactics_pool.append((
            "挂念回访·关心近况",
            f"【主动私聊·挂念回访】：你心里还记着 {nickname}(QQ:{uid_str}) 之前提过的这件事：「{mem_hook}」。\n"
            f"发条私聊顺嘴问问后来怎么样了，语气自然轻快，别像例行查岗。"
        ))
        tactics_pool.append(tactics_pool[-1])

    # 战术 3：【欲言又止 / 好奇心留白钩子 (Curiosity Hook)】（通用高回复率破冰法）
    if is_master:
        tactics_pool.append((
            "欲言又止·引发主人好奇",
            f"【主动私聊·向主人抛话头】：突然想找 {MASTER_QQ} 搭句话，不用把话说满；"
            f"可先 poke_user(user_id={MASTER_QQ}) 轻轻戳他一下，然后留半截带点小神秘的日常话头引他来问你。"
        ))
    else:
        tactics_pool.append((
            "欲言又止·好奇心钩子",
            f"【主动私聊·好奇心钩子】：主动私聊找好友 {nickname}(QQ:{uid_str}) 搭话，"
            "别发干巴巴的“在吗/你好”，可先 poke_user 戳一下，再随口扔半句带悬念的日常话题。"
        ))

    # 战术 4：【精准投喂安利（B站新鲜手账）】
    try:
        from workers.bili_worker import get_today_bili_summary
        bili_sums = get_today_bili_summary()
        if bili_sums:
            picked_bili = random.choice(bili_sums[-3:])
            tactics_pool.append((
                "B站好物·私下投喂",
                f"【主动私聊·精准投喂】：你今天在 B 站刷到了这个视频：{picked_bili}。\n"
                f"你第一反应觉得 {nickname} 肯定会感兴趣或者能懂这个槽点，于是专门私聊分享给他，挑一处最有趣的细节抛个话头和他讨论！"
            ))
    except Exception:
        pass

    # 战术 5：【傲娇搭话 / 情绪分享】（专属主人 & 挚友）
    if is_master or is_bff:
        view, _ = get_decayed_mood_state()
        feel_str = mood_band_phrase(view["mood"])
        tactics_pool.append((
            "傲娇探头·日常唠嗑",
            f"【主动私聊·日常搭话】：你这会儿{feel_str}，看 {nickname} 半天没冒头，顺手发个私聊找他唠两句。\n"
            "可搭一张 send_meme 探头表情包，或者用熟稔自然的语气吐槽他怎么还没出现。"
        ))

    return random.choice(tactics_pool)


async def private_outreach_worker():
    """
    私聊主动打招呼与“勾引对话”后台协程：
    白天活跃时段 (08:30 ~ 22:30) 定期巡检，遵守四道防骚扰护栏，向主人、挚友、朋友及熟人发起高回复率破冰私聊。
    """
    # 启动后先静候 15 分钟再开始首次评估
    await asyncio.sleep(900)

    while True:
        try:
            now = _beijing_now()
            # 仅在 08:30 ~ 22:30 活跃时段主动发起私聊
            if now.hour < 8 or (now.hour == 8 and now.minute < 30) or now.hour >= 23 or (now.hour == 22 and now.minute >= 30):
                await asyncio.sleep(1800)
                continue

            # 随机高斯间隔 2.2 ~ 4.0 小时苏醒检查一次
            sleep_mins = max(130, min(240, random.gauss(175, 25)))
            print(f"💌 [私聊主动引擎] 进入休眠，预计 {sleep_mins:.0f} 分钟后寻觅私聊勾引目标...")
            await asyncio.sleep(sleep_mins * 60)

            now = _beijing_now()
            if now.hour < 8 or now.hour >= 23:
                continue

            meta = _load_outreach_meta()
            # 每日全局配额上限：最多 4 次主动私聊
            if int(meta.get("today_total_count", 0)) >= 4:
                continue

            load_chat_state()
            load_profiles()
            friend_map = await _fetch_qq_friend_map()
            now_ts = time.time()
            last_outreach_map = meta.get("user_last_outreach_ts", {})

            weighted_candidates = []  # list of (uid_str, nickname, aff, weight)

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

                # 护栏二：【未回熔断铁律 (Ghosting Fuse)】—— 上条私聊对方未回，绝不热脸贴冷屁股连续发！
                if last_bot_t > last_human_t and last_bot_t > 0:
                    if not is_master:
                        continue
                    # 对主人：若未回且不足 8 小时，同样避让不刷屏
                    if (now_ts - last_bot_t) < 8 * 3600:
                        continue

                # 护栏三：个体冷却周期核验
                last_out_ts = float(last_outreach_map.get(uid_str, 0.0))
                if is_master:
                    if int(meta.get("today_master_count", 0)) >= 2:
                        continue
                    cooldown_sec = 4.5 * 3600
                    weight = 45
                elif aff >= 850.0:  # 挚友
                    cooldown_sec = 24 * 3600
                    weight = 28
                elif aff >= 500.0:  # 朋友 (主力稳态区)
                    cooldown_sec = 40 * 3600
                    weight = 20
                else:               # 熟人 (200~499，惊喜低频破冰)
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

            # 按权重随机抽取一位幸运目标
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
            tactic_name, lure_ephemeral = _build_private_lure_prompt(target_uid, target_nick, target_aff)
            chat_id_str = f"private_{target_uid}"

            if chat_id_str in ACTIVE_AI_TASKS or chat_id_str in DEBOUNCE_QUEUES:
                continue

            combined_msg = (
                f"【当前触发状态】：内部主动私聊破冰（视角：{tactic_name} | 目标好友：{target_nick}({target_uid})）。\n"
                "请务必调用 send_private_msg（或配合 poke_user / send_meme）主动向对方发出这条私聊消息，自然开启对话！"
            )

            print(f"💌 [私聊主动出击] 选中目标 {target_nick}({target_uid}, 好感:{target_aff:.0f}) | 战术: 【{tactic_name}】")

            # 更新配额与冷却时间戳
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
    """管理所有白名单群的独立呼吸协程 + 私聊主动勾引协程"""
    running_workers = {}
    private_worker_task = asyncio.create_task(private_outreach_worker())

    while True:
        try:
            # 维护私聊主动社交协程存活
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
