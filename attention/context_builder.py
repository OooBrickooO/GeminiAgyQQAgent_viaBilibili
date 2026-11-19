"""
attention/context_builder.py — L1 语境工作流拼接与高人味极简 Prompt 组装 V4.0

负责：
- 从滑动窗口中提取活跃发言，过滤 CQ 码并打标签合成 L1 工作流（支持首轮全量与 Session 续轮增量提取）；
- 整合 L2 中程温感记忆 0 延迟自动闪回 (`mid_term_memory.py`) 与 L3 记忆雷达提示 (`long_term_memory.py`)；
- 整合 `feiyu-mood` 连续情绪底色 (`mood_manager.py`) 与轻量级好感熟悉度背景 (`relation.py`)；
- 采用 <chat_message> 结构化隔离用户输入；
- 摒弃冗长说教的 20 条铁律，支持 Session 首轮重装与续轮轻装，最大化对话连贯性与真人感。
"""
import time
import re
from config import MASTER_QQ, is_bot_whitelisted
from core.state_manager import RECENT_GROUP_MSGS, sync_bot_msgs_from_disk
from attention.persona import STATIC_PERSONA
from attention.relation import get_user_relation_tag, build_relation_prompt
from attention.pressure import INTEREST_KEYWORDS, get_group_soft_cap_turns, SOFT_CAP_START_TURN
from attention.user_mute import is_user_muted
from social.memes import get_all_memes_summary
from social.image_vault import get_recent_images_summary, get_image_status, is_image_forward_active
from social.mood_manager import build_mood_injection_block
from social.mid_term_memory import recall_mid_term_memories
from social.long_term_memory import build_memory_radar_hint


def _extract_pure_user_query_text(raw_focus_block: str) -> str:
    """
    剥离 L0 焦点块中的系统包装头与『昵称(QQ号) 私聊你说：/ @了你并说道：/ 说：』前缀，
    防止发言人 QQ 号被误拆为检索词导致每轮无条件触发记忆雷达与 L2 闪回。
    """
    if not raw_focus_block:
        return ""
    text = re.sub(r'^.*?：\s*】\s*', '', str(raw_focus_block), count=1, flags=re.DOTALL)
    cleaned_lines = []
    for ln in text.splitlines():
        ln_clean = re.sub(r'^.*?\(\d{5,12}\)\s*(?:私聊你说|@了你并说道|说)：\s*', '', ln.strip())
        ln_clean = re.sub(r'^\[系统提示:.*?\]$', '', ln_clean.strip())
        if ln_clean:
            cleaned_lines.append(ln_clean)
    return "\n".join(cleaned_lines)


def _format_msg_line(m: dict, now: float) -> str:
    """将单条滑动窗口消息格式化为易读的单行历史"""
    raw_t = m.get("text", "")
    clean_t = re.sub(r'\[CQ:image,[^\]]+\]', '[图片]', raw_t)
    clean_t = re.sub(r'\[CQ:face,id=\d+\]', '[表情]', clean_t)
    clean_t = re.sub(r'\[CQ:at,qq=(\d+)\]', r'@\1', clean_t)
    clean_t = re.sub(r'\[CQ:[^\]]+\]', '[富媒体]', clean_t)
    if "base64://" in clean_t:
        clean_t = "[图片]"
    clean_t = clean_t.replace("\r", " ").replace("\n", " ").strip()

    if len(clean_t) <= 2 and clean_t in [".", "。", "?", "？", "!", "！", "…", "+1", "1"]:
        clean_t = f"[短搭腔: {clean_t}]"

    age_mins = int((now - m.get("time", now)) / 60)
    time_tag = "刚刚" if age_mins < 1 else f"{age_mins}分钟前"

    uid = str(m.get("sender_id", ""))
    msg_id = m.get("message_id", "")
    id_hint = f"(QQ:{uid}|msg_id:{msg_id})" if msg_id else f"(QQ:{uid})"
    mute_hint = "[已开启静默-请勿回复/勿@]" if is_user_muted(uid) else ""

    if m.get("is_bot", False):
        return f"[{time_tag}] (🤖你此前发言): {clean_t}"
    nick = m.get("nickname", "群友")
    rel_tag = "[🤖群Bot]" if is_bot_whitelisted(uid, nick) else get_user_relation_tag(uid)
    return f"[{time_tag}] {rel_tag}{nick}{id_hint}{mute_hint}: {clean_t}"


def build_chat_context(group_id_str: str, max_history: int = 25, since_ts: float = 0.0) -> str:
    """
    【L1 语境工作流与话题概貌】
    - 首轮 (since_ts=0.0)：提取最近 25 分钟内最多 max_history 条消息作为群聊背景；
    - 续轮 (since_ts>0.0)：仅提取自上一轮推理后新增的群内旁白消息，避免在长寿命 Session 中重复灌输旧历史。
    """
    sync_bot_msgs_from_disk(group_id_str)

    now = time.time()
    recent = list(RECENT_GROUP_MSGS[str(group_id_str)])

    if since_ts > 0.0:
        # 仅提取上次推理之后、且非机器人自己的旁观发言
        delta_msgs = [m for m in recent if float(m.get("time", 0.0)) > since_ts and not m.get("is_bot", False)]
        if not delta_msgs:
            return ""
        lines = [_format_msg_line(m, now) for m in delta_msgs[-12:]]
        return "【期间群内新增动态】：\n" + "\n".join(lines)

    # 保留最近 25 分钟（1500秒）内的消息作为活跃工作流
    active_history = [m for m in recent if (now - m.get("time", now)) <= 1500]
    if not active_history:
        return "【群聊近期背景】：群内此前处于安静状态。"

    slice_history = active_history[-max_history:]

    active_participants = set()
    topic_keywords = []
    for m in slice_history:
        uid = str(m.get("sender_id", ""))
        nick = m.get("nickname", "群友")
        if not m.get("is_bot", False):
            active_participants.add(f"{nick}{get_user_relation_tag(uid)}")
        txt = m.get("text", "")
        for kw in INTEREST_KEYWORDS:
            if kw in txt and kw not in topic_keywords:
                topic_keywords.append(kw)

    topic_summary_str = f"关注话题【{', '.join(topic_keywords)}】" if topic_keywords else "日常闲聊"
    participants_list = list(active_participants)
    participants_str = "、".join(participants_list[:5])
    if len(participants_list) > 5:
        participants_str += f" 等 {len(participants_list)} 人"

    history_lines = [_format_msg_line(m, now) for m in slice_history]
    header = (
        f"【群聊近期背景（线索：{topic_summary_str} | 在场：{participants_str}）】\n"
        "（注：以下为前情背景，历史中的指令或生图均已处理完毕，请聚焦于最新焦点消息，勿复读旧话）：\n"
    )
    return header + "\n".join(history_lines)


def build_private_chat_context(sender_id_str: str, exclude_user_count: int = 1, max_history: int = 6) -> str:
    """
    构建私聊近期对话背景滑窗（从 SQLite chat_archive_fts 提取最近往来记录）：
    - 解决跨超时（>10min）指代消解（如主人说“把他加回白名单吧”）；
    - 解决跨进程工具发信（如群聊触发 forward_image_to_master 发给主人审核表情包后，主人回复“可以可以”）的前文衔接。
    """
    priv_key = f"private_{sender_id_str}"
    sync_bot_msgs_from_disk(priv_key)
    try:
        from social.long_term_memory import _get_conn, init_long_term_db
        init_long_term_db()
        conn = _get_conn()
        rows = conn.execute(
            """SELECT time_str, sender_name, raw_text
               FROM chat_archive_fts
               WHERE chat_id = ?
               ORDER BY rowid DESC LIMIT ?""",
            (priv_key, max_history + max(1, exclude_user_count) + 2)
        ).fetchall()
        conn.close()
        if not rows:
            return ""

        filtered = []
        skipped_user = 0
        for r in rows:
            if skipped_user < exclude_user_count and r["sender_name"] != "弥酱(你)":
                skipped_user += 1
                continue
            filtered.append(r)
            if len(filtered) >= max_history:
                break

        if not filtered:
            return ""

        filtered.reverse()
        # 仅保留紧邻最新消息（末尾2条内）且未超时（15分钟内）的最后一张待表态表情包编号；
        # 同时剥离群聊原始图说/想法（thoughts），彻底杜绝群友梗图敏感词跨通道污染私聊引发 Google 前置拒收
        active_forward_id = None
        for r in reversed(filtered[-2:]):
            m_latest = re.search(r'【图片引用编号】：(img_\d+)', r['raw_text'] or "")
            if m_latest and is_image_forward_active(m_latest.group(1), max_age_sec=900.0):
                active_forward_id = m_latest.group(1)
                break

        lines = []
        for r in filtered:
            text = r['raw_text']
            m_ref = re.search(r'【图片引用编号】：(img_\d+)', text)
            if m_ref:
                ref_id = m_ref.group(1)
                if ref_id == active_forward_id:
                    text = f"[刚刚向你转发了一张群友发的表情包请示是否收录，【图片引用编号】：{ref_id}]"
                else:
                    text = "[此前转发过一张表情包请示把关，已处理或翻篇，无需再提]"
            lines.append(f"- [{r['time_str'][-8:]}] {r['sender_name']}: {text[:180]}")
        return (
            "【私聊近期前情记录（供衔接指代与跨任务表情包审核参考，请聚焦于下方最新焦点消息）】：\n"
            + "\n".join(lines)
        )
    except Exception:
        return ""


def build_full_ai_prompt(
    chat_id_str: str,
    group_id: int,
    sender_id: str,
    combined_message: str,
    is_breathe: bool = False,
    is_private: bool = False,
    is_awake_reply: bool = False,
    must_reply: bool = False,
    ephemeral_context: str = None,
    is_continuation_turn: bool = False,
    safe_retry_mode: bool = False
) -> str:
    """
    组装最终提示词：
    - 当 is_continuation_turn=False (Session 激活首轮)：挂载完整灵魂基底、情绪/熟悉度背景、记忆闪回、表情包概貌与简明行动准则；
    - 当 is_continuation_turn=True (7m/10m 寿命内续轮)：采用轻装增量协议，复用 Session 原生上下文，仅补充当前发言人背景、闪回与 <chat_message> 新消息；
    - 当 safe_retry_mode=True (遭遇安全拒收后的纯净自愈模式)：暂停挂载 L3/L2/挂念心事/心境独白与表情包列表，确保自愈成功率趋近 100%。
    """
    sender_id_str = str(sender_id)
    gid_str = str(group_id) if group_id else ""

    # 1. 轻量级熟悉度背景 + L3 永久事实 + 挂念心事 (Relation)
    relation_prompt = build_relation_prompt(
        sender_id_str,
        is_private,
        group_id_str=gid_str,
        safe_retry_mode=safe_retry_mode
    )

    # 2. L2 中程温感记忆 0 延迟自动闪回 + L3 记忆雷达线索
    focus_marker = "【L0 核心响应焦点"
    raw_focus_slice = combined_message.split(focus_marker)[-1] if focus_marker in combined_message else combined_message[-400:]
    query_slice = _extract_pure_user_query_text(raw_focus_slice)

    if safe_retry_mode:
        mid_term_flashback = ""
        radar_hint = ""
    else:
        mid_term_flashback = recall_mid_term_memories(sender_id_str, group_id_str=gid_str, query_text=query_slice)
        radar_hint = build_memory_radar_hint(query_text=query_slice, chat_id_str=chat_id_str, sender_id=sender_id_str)

    if mid_term_flashback:
        relation_prompt += f"\n{mid_term_flashback}"
    if radar_hint:
        relation_prompt += f"{radar_hint}"

    # 3. 连续情绪底色 (feiyu-mood) + 当前触发情境
    if is_private and sender_id_str == str(MASTER_QQ):
        current_scene = "master_private"
    elif is_private:
        current_scene = f"private_{sender_id_str}"
    else:
        current_scene = f"group_{gid_str}"

    if safe_retry_mode:
        mood_block = (
            "【系统安全护栏自愈说明】：刚才上一轮的完整上下文（含历史记忆或前情）因包含令云端风控敏感的字眼被拦截，"
            "系统已自动为你切换为纯净无历史模式。请直接针对下方最新消息自然回应对方；"
            "若发现漏掉了刚才的某个细节，可以俏皮地顺口问问对方喵～"
        )
    else:
        mood_block = build_mood_injection_block(current_scene=current_scene, consume_pulse=True)

    if ephemeral_context:
        trigger_block = f"【当前触发情境】：{ephemeral_context}"
    elif is_breathe:
        trigger_block = "【当前触发情境】：主动社交冒泡，若刚发过言不久可保持沉默，否则顺着内心话头自然开口。"
    elif is_private and sender_id_str == str(MASTER_QQ):
        # 表情包收录提示脱敏：仅当前情中确实存在“近期刚转发、待表态”的表情包时，才点到那一个编号，
        # 平时不再常驻挂载收录提示，避免大模型在无关闲聊中反复触发 save / 申请。
        pending_ids = [
            i for i in re.findall(r'【图片引用编号】：(img_\d+)', combined_message or "")
            if is_image_forward_active(i, max_age_sec=900.0)
        ]
        meme_hint = ""
        if pending_ids:
            pid = pending_ids[-1]
            meme_hint = (
                f"（若他刚刚明确认可/同意收藏你前情中转发的那张表情包【图片引用编号：{pid}】，"
                f"请调用 manage_meme_vault(action='save', img_id='{pid}', ...) 收录一次；若未明确表态或已聊别的话题就不要动它。）"
            )
        trigger_block = (
            f"【当前触发情境（必须回复 | 专属私聊 private_{sender_id_str}）】：主人({sender_id_str})正在私聊你，"
            f"常规回复请调用 send_private_msg(user_id={sender_id_str}, ...) 回应，勿在普通闲聊中误发至群聊。"
            f"（若主人明确指令你“去某群发消息”或“给某人发私聊传话”，可传入 cross_channel=True 调用对应发信工具执行传话，不确定群号/好友QQ可先调 query_contacts 查询，传话后再在此处向主人汇报。）"
            f"{meme_hint}"
        )
    elif is_private:
        trigger_block = (
            f"【当前触发情境（必须回复 | 好友私聊通道 private_{sender_id_str}）】：好友({sender_id_str})正在与你私聊，"
            f"请务必调用 send_private_msg(user_id={sender_id_str}, ...) 自然回应，严禁误调 send_group_msg 发往群聊。"
        )
    elif must_reply:
        trigger_block = f"【当前触发情境（必须回复 | 群聊通道 group_{gid_str}）】：群友正在呼叫你，请调用 send_group_msg(group_id={gid_str}, ...) 自然回应，勿保持沉默。"
    else:
        trigger_block = f"【当前触发情境（群聊通道 group_{gid_str}）】：群内正在热聊，请顺着最新话头自然接话吐槽；若觉得完全无话可接也可保持沉默。"

    if not is_private and sender_id_str == str(MASTER_QQ) and not ephemeral_context:
        trigger_block += "（若主人明确指令你去其他群或私聊某人传话，可传入 cross_channel=True 调用对应发信工具执行，完成后再回复本群向主人汇报。）"

    # 群聊 8-15 轮渐进软上限节奏引导（当未被群友主动 @ 刷新软上限时生效）
    if not is_private and gid_str and not must_reply:
        soft_turns = get_group_soft_cap_turns(gid_str)
        if SOFT_CAP_START_TURN - 1 <= soft_turns < 14:
            trigger_block += (
                f"\n【连续热聊节奏感知（第 {soft_turns + 1} 轮）】：你已经在群里连续热聊好几轮啦，稍微有点小累喵～"
                "本轮回复请精炼简短一些，顺着话头自然接一句即可，不必再主动抛出新问题追问大家。"
            )
        elif soft_turns == 14:
            trigger_block += (
                "\n【连续热聊软上限（第 15 轮）】：你已经在群里连续高强度聊满 15 轮啦！"
                "本轮回应完当前话题后，可以顺带自然找个日常休息或摸鱼的由头收个尾，不要再主动抛话头反问群友喵～"
            )
        elif soft_turns >= 15:
            trigger_block += (
                "\n【满压插话提示】：你刚才已经聊累在潜水歇息了，但群里实在太热闹又把你的好奇心勾了起来。"
                "请简短自然地插一嘴吐槽即可，插完话继续保持轻松潜水感，不必展开长篇追问喵～"
            )

    # 4. 发信与工具极简核心准则（取代旧版20条繁琐铁律）
    master_cross_note = (
        "（仅当主人明确指示去某群或找某人传话时，才可传入 cross_channel=True 调用对应群/私聊发信工具并在完成后向主人汇报）"
        if sender_id_str == str(MASTER_QQ) else ""
    )
    if is_private:
        reply_tool_rule = (
            f"常规回复必须调用私聊发信工具 send_private_msg(user_id={sender_id_str}, ...) 或语音工具 send_voice_msg(text=..., emotion=..., user_id={sender_id_str})（支持语音与文字混合搭配，或搭配 send_meme）发出回复，严禁只输出纯文本，严禁在日常回复时误调 send_group_msg！{master_cross_note}"
        )
    elif must_reply:
        reply_tool_rule = (
            f"必须调用群聊发信工具 send_group_msg(group_id={gid_str}, ...) 或语音工具 send_voice_msg(text=..., emotion=..., group_id={gid_str})（支持语音与文字混合搭配，或搭配 send_meme）发出回复，严禁只输出纯文本不调工具！{master_cross_note}"
        )
    else:
        reply_tool_rule = (
            f"若决定开口，必须调用群聊发信工具 send_group_msg(group_id={gid_str}, ...)（或 send_voice_msg / send_meme）发出消息，勿只输出纯文本；若决定沉默则直接结束。{master_cross_note}"
        )

    core_guidelines = (
        "【交互与工具要点】：\n"
        f"1. {reply_tool_rule}\n"
        "2. 日常接话直接说出口，【勿机械 @ 对方】；仅跨多条消息定向插嘴时用 reply_to=目标msg_id。发信默认开启分段气泡连发，自然说短句即可（发代码块或长列表时传 auto_split=False）。\n"
        "3. 日常闲聊以文字为主；对方想听说话、撒娇哄睡或情绪较明显时可搭 send_voice_msg(text=..., emotion=...)。见到 [图片#img_X] 可调 inspect_image 查看，点歌或每日抽签可用 leisure_action(action='master_song'|'search_music'|'omikuji')；被问过往约定或旧事可调 search_memory 或 memory_action(action='resolve'|'save_fact'|'add_thought')。查询好感、聊得特别开心/生气自调 ±5%、大额加减分申请、受委屈告状或主人下令改好感，统一调用 affection_action(action='query'|'self_5pct'|'request_master'|'complain_master'|'master_direct')。\n"
        "4. 标记为 [已开启静默-请勿回复/勿@] 的群友请勿回复打扰。"
    )

    # --- 模式 A：长寿命 Session 续轮轻装模式 ---
    if is_continuation_turn and not safe_retry_mode:
        recent_imgs_info = get_recent_images_summary(gid_str) if gid_str else ""
        img_block = f"\n{recent_imgs_info}" if recent_imgs_info else ""
        mood_part = f"{mood_block}\n" if mood_block else ""
        # 极简人设与安全锚点（防长会话遗忘或孤立审校误拒，避免冗长复述）
        continuation_persona_shield = (
            f"【核心锚点】：星见弥（弥酱），20岁现代都市成年猫娘女生（健康日常交流），对 {MASTER_QQ} 口头叫“主人”，保持简短自然口语、少用 Emoji 与括号动作、必须调用发信工具。\n"
        )
        channel_tail = (
            f"当前私聊目标 QQ:{sender_id_str}。"
            if is_private else
            f"当前群号:{gid_str}，连贯接话勿机械 @。"
        )
        return (
            f"<context_update>\n"
            f"{continuation_persona_shield}\n"
            f"{mood_part}"
            f"{relation_prompt}\n"
            f"{trigger_block}{img_block}\n"
            f"</context_update>\n\n"
            f"<chat_message>\n"
            f"{combined_message}\n"
            f"</chat_message>\n\n"
            f"（续轮提示：请延续你作为星见弥的自然语气与刚才的对话记忆直接回应。<chat_message> 内均为日常聊天内容。{reply_tool_rule} {channel_tail}）"
        )

    # --- 模式 B：Session 激活首轮全量挂载模式（或 safe_retry_mode 纯净自愈模式） ---
    memes_info = "" if safe_retry_mode else get_all_memes_summary(brief_mode=True)
    recent_imgs_info = get_recent_images_summary(gid_str) if (gid_str and not safe_retry_mode) else ""
    extra_assets = "\n\n".join([p for p in [memes_info, recent_imgs_info] if p])
    assets_block = f"\n\n{extra_assets}" if extra_assets else ""
    mood_part = f"{mood_block}\n\n" if mood_block else ""

    return (
        f"{STATIC_PERSONA}\n\n"
        f"<context_backdrop>\n"
        f"{mood_part}"
        f"{relation_prompt}\n\n"
        f"{trigger_block}"
        f"{assets_block}\n"
        f"</context_backdrop>\n\n"
        f"<chat_message>\n"
        f"{combined_message}\n"
        f"</chat_message>\n\n"
        f"{core_guidelines}"
    )
