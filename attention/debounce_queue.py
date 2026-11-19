"""
attention/debounce_queue.py — 5s 防抖聚合队列、长寿命 Session 调度与事件分发 V4.0

管理：
- 群聊/私聊统一 5.0 秒防抖聚合队列（最大 12.0 秒硬截断防刷屏饿死）
- 群聊 7min / 私聊 10min 长寿命 Session 激活、续期与首尾分离上下文组装
- 统一由【直接唤名/@】与【社交熵压力模型（含 45s 80% 余温高底压 + 50% 会话底压）】驱动群聊对话
- 主人专属强杀指令 (@弥酱 停 / 别说了)
"""
import time
import os
import json
import re
import base64
import random
import asyncio
from config import BOT_QQ, MASTER_QQ, get_allowed_groups, is_bot_whitelisted
from core.state_manager import (
    LAST_HUMAN_MSG_TIME, LAST_BOT_MSG_TIME, CONSECUTIVE_BOT_MSG_COUNT,
    RECENT_GROUP_MSGS,
    save_chat_state, record_bot_spoke, reset_awake_counter
)
from core.napcat_client import (
    fetch_group_member_count, set_input_status,
    send_private_msg_raw, send_group_msg_raw, set_friend_add_request
)
from core.quote_resolver import (
    record_message_to_cache, resolve_message_quotes,
    get_message_detail
)
from core.agy_runner import (
    ACTIVE_AI_TASKS, ACTIVE_SESSIONS, format_runtime_status,
    execute_agy_inference, get_or_create_active_session,
    record_session_turn_completed, reset_session_on_safety_retry,
    rotate_session_if_due, PromptSafetyRejectedError
)
from social.image_vault import process_message_images
from social.affection import add_user_exp, add_user_affection, get_user_profile
from attention.relation import get_user_relation_tag
from attention.pressure import (
    GROUP_MEMBER_COUNTS, set_group_member_count,
    update_message_pressure, get_group_pressure_snapshot,
    get_group_soft_cap_turns, SOFT_CAP_START_TURN, SOFT_CAP_ZERO_TURN,
    trigger_pressure_burst, cap_group_pressure,
    reset_group_pressure, is_in_pressure_cooldown
)
from social.bilibili_master_bridge import (
    handle_master_bili_login, handle_master_bili_logout,
    handle_master_bili_history, detect_bili_link
)
from social.bilibili_client import prefetch_bili_watch_context
from attention.user_mute import (
    is_user_muted, mute_user, unmute_user,
    parse_mute_command, parse_unmute_command
)
from attention.context_builder import (
    build_full_ai_prompt, build_chat_context, build_private_chat_context
)
from social.long_term_memory import record_chat_archive
from workers.post_turn_observer import get_latest_bot_reply
from tools.message_tools import record_bot_spoke_persistent
from toys.board_games import (
    get_active_game, analyze_and_execute_game_turn, try_fast_start_game
)


async def _handle_game_dual_track(
    chat_id_str: str,
    group_id,
    sender_id,
    nickname: str,
    raw_clean_text: str,
    raw_message: str,
    is_private: bool,
) -> tuple[bool, str | None]:
    """
    小游戏双轨调度拦截器：
    - 返回 (True, None): 已在 0-Token 快速通道完成走子、发图与台词回复，调用方直接 return。
    - 返回 (False, extra_ctx): 未命中游戏 (extra_ctx is None)，或命中游戏但需要唤醒 LLM (extra_ctx != None)。
    """
    if get_active_game(chat_id_str) is not None:
        game_res = analyze_and_execute_game_turn(chat_id_str, str(sender_id), nickname, raw_clean_text)
    else:
        game_res = try_fast_start_game(chat_id_str, str(sender_id), nickname, raw_clean_text, is_private=is_private)

    if not game_res or not game_res.get("handled"):
        return False, None

    img_path = game_res.get("image_path")
    img_cq = ""
    if img_path and os.path.exists(img_path):
        try:
            with open(img_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
            img_cq = f"[CQ:image,file=base64://{b64_data}]"
        except Exception as e:
            print(f"[BoardGames] 读取渲染棋盘图失败: {e}")

    # 分支 A：0-Token 快速回应（纯落子 / 悔棋 / 干脆开局 / 非法坐标提示）
    if not game_res.get("should_trigger_llm_hook"):
        reply_text = (game_res.get("reply_text") or "").strip()
        combined_msg = f"{img_cq}\n{reply_text}".strip() if img_cq else reply_text
        if combined_msg:
            if is_private:
                await send_private_msg_raw(int(sender_id), combined_msg)
            else:
                await send_group_msg_raw(int(group_id), combined_msg)
            log_txt = (("[棋盘图] " if img_cq else "") + reply_text).strip()
            record_bot_spoke_persistent(chat_id_str, log_txt)
            record_chat_archive(chat_id_str, str(BOT_QQ), "弥酱", log_txt, is_bot=True)
        add_user_exp(str(sender_id), 1.0)
        add_user_affection(str(sender_id), 1.0, raw_message)
        return True, None

    # 分支 B：亮点 / 每5回合 / 终局 / 聊天融合 -> 先瞬发棋盘图，再挂载战况摘要进正常 LLM 思考
    if img_cq and game_res.get("action_type") != "CHAT_ONLY":
        if is_private:
            await send_private_msg_raw(int(sender_id), img_cq)
        else:
            await send_group_msg_raw(int(group_id), img_cq)
        record_bot_spoke_persistent(chat_id_str, "[棋盘图]")
        record_chat_archive(chat_id_str, str(BOT_QQ), "弥酱", "[棋盘图]", is_bot=True)

    tip = (
        "最新棋盘图已由系统立刻发给对方，你无需再调发图工具或 play_board_game，直接结合战况与心情自然互动回应即可！"
        if img_cq
        else "请直接结合下面桌游实时战况与当前心情自然互动回应，无需重复调用 play_board_game！"
    )
    if game_res.get("game_over"):
        tip += "（本局已结束并自动完成了好感度、7D心情及L2记忆结算，可顺势傲娇调侃/安慰或邀约再来一局！）"

    extra_ctx = f"\n{game_res.get('board_summary_for_llm', '')}\n（系统提示：{tip}）"
    return False, extra_ctx

# --- 消息防抖聚合队列 ---
DEBOUNCE_QUEUES = {}
GROUP_DEBOUNCE_WAIT = 4.8
DIRECT_DEBOUNCE_WAIT = 2.2
DEBOUNCE_WAIT_TIME = GROUP_DEBOUNCE_WAIT
MAX_DEBOUNCE_CAP = 12.0
DIRECT_MAX_DEBOUNCE_CAP = 8.0

# --- 通道并发锁 ---
CHAT_LOCKS = {}

# --- 正在“整轮回复”中的通道集合 ---
# 覆盖 trigger_ai 的完整生命周期（组装 Prompt、等信号量、agy 推理、发信收尾），
# 比 ACTIVE_AI_TASKS（仅在子进程存活期有效）更完整，用于思考期新消息降维。
INFLIGHT_TURNS = set()


def get_current_runtime_status() -> str:
    """获取当前看板状态"""
    return format_runtime_status(list(DEBOUNCE_QUEUES.keys()))


def is_channel_busy(chat_id_str: str) -> bool:
    """通道是否正处于一轮回复流程中（含子进程思考期与前后收尾期）"""
    return chat_id_str in ACTIVE_AI_TASKS or chat_id_str in INFLIGHT_TURNS


async def trigger_ai(
    chat_id_str: str,
    group_id: int,
    sender_id,
    combined_message: str,
    is_breathe: bool = False,
    is_private: bool = False,
    is_awake_reply: bool = False,
    messages_list: list = None,
    must_reply: bool = False,
    ephemeral_context: str = None
) -> list:
    """获取或续期长寿命 Session，组装 Prompt 并唤起大模型思考（包装层：全程登记通道繁忙状态）"""
    INFLIGHT_TURNS.add(chat_id_str)
    try:
        return await _trigger_ai_impl(
            chat_id_str, group_id, sender_id, combined_message,
            is_breathe=is_breathe, is_private=is_private, is_awake_reply=is_awake_reply,
            messages_list=messages_list, must_reply=must_reply, ephemeral_context=ephemeral_context
        )
    finally:
        INFLIGHT_TURNS.discard(chat_id_str)


async def _trigger_ai_impl(
    chat_id_str: str,
    group_id: int,
    sender_id,
    combined_message: str,
    is_breathe: bool = False,
    is_private: bool = False,
    is_awake_reply: bool = False,
    messages_list: list = None,
    must_reply: bool = False,
    ephemeral_context: str = None
) -> list:
    """获取或续期长寿命 Session (群聊7min/私聊10min)，组装 Prompt 并唤起大模型思考"""
    if messages_list is None:
        messages_list = [combined_message]

    sess, is_continuation_turn = get_or_create_active_session(
        chat_id_str=chat_id_str,
        is_private=is_private,
        group_id=group_id,
        sender_id=str(sender_id)
    )
    dynamic_conv_id = sess["conv_id"]
    turn_start_ts = time.time()

    prompt = build_full_ai_prompt(
        chat_id_str=chat_id_str,
        group_id=group_id,
        sender_id=str(sender_id),
        combined_message=combined_message,
        is_breathe=is_breathe,
        is_private=is_private,
        is_awake_reply=is_awake_reply,
        must_reply=must_reply,
        ephemeral_context=ephemeral_context,
        is_continuation_turn=is_continuation_turn
    )

    from core.intent_router import route_message_domains, route_chat_effort
    merged_msg_text = "\n".join(messages_list or [combined_message])
    target_domains = await route_message_domains(
        message_text=merged_msg_text,
        sender_id=str(sender_id),
        is_private=is_private,
        is_breathe=is_breathe,
        chat_id_str=chat_id_str
    )
    chat_effort = route_chat_effort(
        message_text=merged_msg_text,
        is_continuation_turn=is_continuation_turn,
        is_breathe=is_breathe,
        target_domains=target_domains,
        is_private=is_private,
        sender_id=str(sender_id)
    )

    try:
        tools_called = await execute_agy_inference(
            chat_id_str=chat_id_str,
            prompt=prompt,
            dynamic_conv_id=dynamic_conv_id,
            messages_list=messages_list,
            target_domains=target_domains,
            caller_id=str(sender_id),
            effort=chat_effort
        )
    except PromptSafetyRejectedError as e:
        tools_called = []
        print(f"🚨 [自愈拦截] 通道 {chat_id_str} 遭遇 Google 提示词安全拦截: {e}")
        print(f"🔄 [启动秒级自愈] 正在重置会话并以纯净安全模式（剥离历史记忆与前情包袱）重新推理...")

        retry_conv_id = reset_session_on_safety_retry(chat_id_str)
        if not is_private and group_id:
            retry_combined = (
                "【L0 核心响应焦点 (本次请集中针对以下最新消息做出反应)：】\n"
                + "\n".join(messages_list or [combined_message])
            )
        else:
            retry_combined = (
                "【L0 核心响应焦点 (对方刚刚发送给你的私聊消息)：】\n"
                + "\n".join(messages_list or [combined_message])
            )

        retry_prompt = build_full_ai_prompt(
            chat_id_str=chat_id_str,
            group_id=group_id,
            sender_id=str(sender_id),
            combined_message=retry_combined,
            is_breathe=is_breathe,
            is_private=is_private,
            is_awake_reply=is_awake_reply,
            must_reply=must_reply,
            ephemeral_context=ephemeral_context,
            is_continuation_turn=False,
            safe_retry_mode=True
        )
        print(f"⚡ [自愈重推] 正在以纯净安全模式重新调度推理 (conv_id={retry_conv_id[:8]}...)...")
        try:
            tools_called = await execute_agy_inference(
                chat_id_str=chat_id_str,
                prompt=retry_prompt,
                dynamic_conv_id=retry_conv_id,
                messages_list=messages_list,
                target_domains=target_domains,
                caller_id=str(sender_id),
                effort="high"
            )
            print(f"🎉 [自愈成功] 通道 {chat_id_str} 经精简滑窗重推成功完成，顺利脱困！")
        except Exception as retry_err:
            print(f"⚠️ [自愈二次受阻] 通道 {chat_id_str} 自愈重推仍未能完成: {retry_err}")
            tools_called = []

        state_key = f"private_{sender_id}" if is_private else str(group_id)
        from workers.post_turn_observer import check_bot_spoke_in_turn
        bot_spoke_disk, bot_reply_disk = check_bot_spoke_in_turn(state_key, turn_start_ts)

        if not tools_called and not bot_spoke_disk:
            SAFETY_FALLBACK_REPLIES = [
                "嘶……刚才脑子一下卡壳了，你刚刚说啥来着？",
                "？刚才好像走神了，没太反应过来",
                "信号突然卡了一下，这句算我没听清",
                "有点没跟上你的脑回路，换个说法听听？",
                "嗯？刚刚好像有奇怪的电波飘过去了，重说一遍"
            ]
            fallback_msg = random.choice(SAFETY_FALLBACK_REPLIES)
            try:
                if not is_private and group_id:
                    print(f"🛡️ [安全保底] 群[{group_id}] 触发萌混过关兜底回复: '{fallback_msg}'")
                    await send_group_msg_raw(int(group_id), fallback_msg)
                    record_bot_spoke(str(group_id), fallback_msg)
                    tools_called = ["send_group_msg"]
                elif is_private and sender_id:
                    print(f"🛡️ [安全保底] 私聊[{sender_id}] 触发萌混过关兜底回复: '{fallback_msg}'")
                    await send_private_msg_raw(int(sender_id), fallback_msg)
                    tools_called = ["send_private_msg"]
            except Exception as send_err:
                print(f"⚠️ [安全保底发信失败]: {send_err}")

    # 记录本轮会话交互并刷新 7min/10min 寿命计时与群聊 45s 80% 余温期
    # 7min/10min 无对话到期后，由 agy_runner 自动销毁会话并调用 consolidate_session_memory 整理记忆
    state_key = f"private_{sender_id}" if is_private else str(group_id)
    if 'check_bot_spoke_in_turn' not in locals():
        from workers.post_turn_observer import check_bot_spoke_in_turn
    if 'bot_spoke_disk' not in locals():
        bot_spoke_disk, bot_reply_disk = check_bot_spoke_in_turn(state_key, turn_start_ts)

    spoke = any(t in (tools_called or []) for t in ("send_group_msg", "send_private_msg", "send_meme", "send_local_image", "send_image_to_group", "send_image_to_private", "send_voice_msg")) or bot_spoke_disk
    bot_reply = bot_reply_disk if ('bot_reply_disk' in locals() and bot_reply_disk) else ""
    if spoke and not bot_reply:
        await asyncio.sleep(0.3)
        bot_reply = get_latest_bot_reply(state_key, turn_start_ts)

    voice_used = ("send_voice_msg" in (tools_called or [])) or (bot_reply or "").startswith("[语音(")

    if spoke:
        record_session_turn_completed(
            chat_id_str=chat_id_str,
            sender_id=str(sender_id),
            user_messages=messages_list,
            bot_reply=bot_reply,
            group_id=group_id,
            is_private=is_private,
            turn_start_ts=turn_start_ts,
            voice_used=voice_used
        )
    else:
        # 本轮被提前打断或未开口发信，不计入有效轮次，防止下轮被错误判断为已落盘的 continuation 轮次丢失人设锚点
        print(f"ℹ️ [轮次保留] 通道 {chat_id_str} 未产生实质开口发信，不递增会话轮次 (turn_count 保持)，确保下轮具备完整人设防护。")

    return tools_called


async def process_debounced_queue(chat_id_str: str, wait_time: float = 5.0):
    """等待 5s 防抖时间到期后，集中出队并拉起大模型推理"""
    if wait_time > 0:
        await asyncio.sleep(wait_time)
    else:
        await asyncio.sleep(0)

    if chat_id_str not in CHAT_LOCKS:
        CHAT_LOCKS[chat_id_str] = asyncio.Lock()

    async with CHAT_LOCKS[chat_id_str]:
        if chat_id_str in DEBOUNCE_QUEUES:
            data = DEBOUNCE_QUEUES.pop(chat_id_str)
            is_private = data["is_private"]

            if not is_private and not data.get("must_reply", False):
                gid_str = str(data["group_id"])
                last_bot_t = LAST_BOT_MSG_TIME.get(gid_str, 0.0)
                last_queued = data.get("last_queued_time", data.get("first_queued_time", 0.0))
                if last_bot_t >= last_queued and last_bot_t > 0:
                    print(f"🛑 [排他去重] 通道 {chat_id_str} 积压消息均在大模型发信前进入且无新发言，取消重复触发。")
                    return

            # 滚动会话：同一 conv_id 满 4~5 轮后无感轮转为新 conv_id（需在判定 is_cont 之前执行，轮转后自动按首轮全量上下文组装）
            rotate_session_if_due(chat_id_str)

            # 判断当前是否已处于活跃 Session 续轮（若为续轮则仅提取增量群聊背景，不重复灌输25条旧历史）
            active_sess = ACTIVE_SESSIONS.get(chat_id_str)
            is_cont = bool(active_sess and active_sess.get("turn_count", 0) > 0)

            if is_private:
                sender_id = data.get("sender_id")
                if sender_id:
                    asyncio.create_task(set_input_status(int(sender_id), 1))

                priv_context = build_private_chat_context(
                    str(sender_id),
                    exclude_user_count=len(data["messages"]),
                    max_history=3 if is_cont else 6
                )
                new_messages_part = (
                    "【L0 核心响应焦点 (对方刚刚发送给你的私聊消息)：】\n"
                    + "\n".join(data["messages"])
                )
                combined_message = f"{priv_context}\n\n{new_messages_part}" if priv_context else new_messages_part
            else:
                group_id_str = str(data["group_id"])
                since_ts = active_sess.get("last_inference_ts", 0.0) if is_cont else 0.0
                context_part = build_chat_context(group_id_str, since_ts=since_ts)
                new_messages_part = (
                    "【L0 核心响应焦点 (本次请集中针对以下最新消息做出反应)：】\n"
                    + "\n".join(data["messages"])
                )
                combined_message = f"{context_part}\n\n{new_messages_part}" if context_part else new_messages_part

            print(f"📦 [5s防抖结束] 通道 {chat_id_str} 消息聚合完毕 (共 {len(data['messages'])} 条) | {get_current_runtime_status()}")
            await trigger_ai(
                chat_id_str,
                data["group_id"],
                data["sender_id"],
                combined_message,
                is_private=is_private,
                is_awake_reply=data["is_awake_reply"],
                messages_list=data["messages"],
                must_reply=data.get("must_reply", False),
                ephemeral_context=data.get("ephemeral_context")
            )


async def enqueue_message(
    chat_id_str: str,
    group_id: int,
    sender_id,
    raw_message: str,
    is_private: bool,
    is_awake_reply: bool,
    must_reply: bool = False,
    ephemeral_context: str = None,
    debounce_wait: float = DEBOUNCE_WAIT_TIME
):
    """将消息放入 5s 防抖聚合队列（新消息顺延 5s，最高硬截断 12s）"""
    now = time.time()
    if chat_id_str in DEBOUNCE_QUEUES:
        q = DEBOUNCE_QUEUES[chat_id_str]
        q["messages"].append(raw_message)
        q["last_queued_time"] = now
        q["sender_id"] = sender_id
        if must_reply:
            q["must_reply"] = True
        if ephemeral_context:
            existing_ctx = q.get("ephemeral_context", "")
            if not existing_ctx or "特意提到了你" in ephemeral_context:
                q["ephemeral_context"] = ephemeral_context

        first_time = q.get("first_queued_time", now)
        elapsed_total = now - first_time
        # 滑动防抖：直呼/私聊走较短硬上限(8s)，普通群聊走 12s 上限
        debounce_cap = DIRECT_MAX_DEBOUNCE_CAP if debounce_wait <= 3.0 else MAX_DEBOUNCE_CAP
        if elapsed_total < debounce_cap:
            if q["task"] and not q["task"].done():
                q["task"].cancel()
            next_wait = min(debounce_wait, max(0.5, debounce_cap - elapsed_total))
            q["task"] = asyncio.create_task(process_debounced_queue(chat_id_str, next_wait))

        print(f"📥 [防抖消息合并] 通道 {chat_id_str} 追加新消息 (当前积压: {len(q['messages'])}条) | {get_current_runtime_status()}")
    else:
        messages_list = [raw_message]

        if is_channel_busy(chat_id_str):
            task_info = ACTIVE_AI_TASKS.get(chat_id_str)
            if is_private and task_info and not task_info.get("spoke", False):
                # 私聊且本轮大模型尚未发出任何一句话：安全地中止并把新消息合并进去重新思考（最终只回答一次）
                print(f"\n⚡ [私聊打断] 私聊通道 {chat_id_str} 侦测到新消息且本轮尚未开口！正在中止正在运行的大模型以合并重新思考...")
                task_info["aborted"] = True
                try:
                    task_info["process"].terminate()
                except Exception:
                    pass
                messages_list = task_info["messages"] + messages_list
            else:
                # 思考期消息降维：本轮已经开口（或处于组装/收尾期、群聊思考期）时，新消息只作为“下一次开口的前情背景”，
                # 此处不创建新的防抖 Task、不拉起新的推理 call，彻底杜绝连环抢答与重复回答。
                # （消息本身已在入口处写入 RECENT_GROUP_MSGS 滑窗与聊天归档，续轮时会作为增量上文传入。）
                print(f"🪶 [思考期降维] 通道 {chat_id_str} 本轮回复进行中，新消息仅沉淀为下轮背景，不触发新调用。| {get_current_runtime_status()}")
                return

        DEBOUNCE_QUEUES[chat_id_str] = {
            "messages": messages_list,
            "group_id": group_id,
            "sender_id": sender_id,
            "is_private": is_private,
            "is_awake_reply": is_awake_reply,
            "must_reply": must_reply,
            "ephemeral_context": ephemeral_context,
            "first_queued_time": now,
            "last_queued_time": now,
            "debounce_wait": debounce_wait,
            "task": None
        }
        print(f"📥 [新消息入队] 通道 {chat_id_str} (进入 {debounce_wait:.1f}s 防抖) | {get_current_runtime_status()}")
        DEBOUNCE_QUEUES[chat_id_str]["task"] = asyncio.create_task(process_debounced_queue(chat_id_str, debounce_wait))


async def _async_update_member_count(group_id_str: str):
    """异步获取并更新群成员总数"""
    count = await fetch_group_member_count(group_id_str)
    set_group_member_count(group_id_str, count)


async def handle_incoming_event(message_str: str):
    """处理 OneBot WebSocket 推送的原始事件"""
    try:
        data = json.loads(message_str)
        post_type = data.get('post_type')
        if post_type in ('message', 'message_sent'):
            message_type = data.get('message_type')
            sender = data.get('sender', {})
            sender_id = sender.get('user_id')
            message_id = data.get('message_id')

            # 核心护城河：录入机器人自己的发言，防自言自语，然后直接 return 忽略
            if str(sender_id) == str(BOT_QQ) or post_type == 'message_sent':
                if message_type == 'group':
                    group_id_str = str(data.get('group_id', ''))
                    if group_id_str in get_allowed_groups():
                        record_bot_spoke(group_id_str, data.get('raw_message', ''))
                return

            nickname = sender.get('card') or sender.get('nickname') or str(sender_id)
            raw_message = data.get('raw_message', '')

            # 1. 录入引用溯源轻量内存缓存池
            record_message_to_cache(message_id, sender_id, nickname, raw_message)

            # 2. 嗅探并解析 [CQ:reply] 引用回复
            resolved_message, is_replying_to_bot = await resolve_message_quotes(raw_message, BOT_QQ)

            # 3. 嗅探并提取图片，生成 [图片#img_X] 简短锚点
            display_msg, new_imgs = process_message_images(
                resolved_message, sender_id, nickname, data.get('group_id'),
                relation_tag_func=get_user_relation_tag
            )
            if new_imgs:
                print(f"🖼️ [图片嗅探] 捕获 {len(new_imgs)} 张新图片: {[i['id'] for i in new_imgs]} | 发送者: {nickname}")

            # --- 1. 处理群聊 ---
            if message_type == 'group':
                group_id = data['group_id']
                group_id_str = str(group_id)
                chat_id_str = f"group_{group_id}"

                if group_id_str not in get_allowed_groups():
                    return

                # 【主人专属硬核权限】：手动 @ 强制终止思考与清空队列
                if str(sender_id) == str(MASTER_QQ):
                    is_at_bot = f"[CQ:at,qq={BOT_QQ}]" in raw_message or any(kw in raw_message for kw in ["弥酱", "星见弥", "弥弥"])
                    stop_keywords = ["停", "别说了", "闭嘴", "终止", "取消", "别回", "打断", "stop", "住手", "别发"]
                    if is_at_bot and any(kw in raw_message.lower() for kw in stop_keywords):
                        print(f"🛑 [主人手动终止] 收到主人的强行中止指令: '{raw_message}'！立即终止当前群的所有任务...")
                        if chat_id_str in ACTIVE_AI_TASKS:
                            try:
                                proc = ACTIVE_AI_TASKS[chat_id_str]["process"]
                                import os, signal
                                try:
                                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                                except Exception:
                                    proc.kill()
                                print(f"🛑 [主人手动终止] 已物理斩首通道 {chat_id_str} 正在运行的大模型进程组！")
                            except Exception:
                                pass
                            ACTIVE_AI_TASKS.pop(chat_id_str, None)
                        if chat_id_str in DEBOUNCE_QUEUES:
                            try:
                                DEBOUNCE_QUEUES[chat_id_str]["task"].cancel()
                            except Exception:
                                pass
                            DEBOUNCE_QUEUES.pop(chat_id_str, None)
                            print(f"🛑 [主人手动终止] 已清空通道 {chat_id_str} 的待处理防抖队列！")
                        return

                    bili_login_keywords = ["登录b站", "b站登录", "b站扫码", "绑定b站", "b站状态", "b站账号", "检查b站"]
                    if is_at_bot and any(kw in raw_message.lower() for kw in bili_login_keywords):
                        await handle_master_bili_login(sender_id, group_id)
                        return

                    bili_logout_keywords = ["退出b站", "b站退出", "解绑b站", "b站解绑", "b站注销", "注销b站"]
                    if is_at_bot and any(kw in raw_message.lower() for kw in bili_logout_keywords):
                        await handle_master_bili_logout(sender_id, group_id)
                        return

                    bili_history_keywords = ["b站历史", "看b站历史", "观看历史", "刷啥视频", "刷了啥", "你看过啥", "冲浪历史", "今日冲浪", "浏览记录"]
                    if is_at_bot and any(kw in raw_message.lower() for kw in bili_history_keywords):
                        await handle_master_bili_history(sender_id, group_id)
                        return

                is_whitelisted_bot = is_bot_whitelisted(str(sender_id), nickname)

                # 人类群友发言：重置机器人连续发言计数，更新人类最后发言时间（白名单 Bot 不刷新人类活跃计时）
                if not is_whitelisted_bot:
                    LAST_HUMAN_MSG_TIME[group_id_str] = time.time()
                    CONSECUTIVE_BOT_MSG_COUNT[group_id_str] = 0
                    save_chat_state()
                record_chat_archive(chat_id_str, str(sender_id), nickname, display_msg, is_bot=False)

                # --- Layer 2: 社交熵话题压力累加（白名单群 Bot 豁免压力计算） ---
                if group_id_str not in GROUP_MEMBER_COUNTS:
                    GROUP_MEMBER_COUNTS[group_id_str] = 200
                    asyncio.create_task(_async_update_member_count(group_id_str))

                if is_whitelisted_bot:
                    pressure_add = 0.0
                    has_interest_kw = False
                    current_pressure, current_threshold, combo = get_group_pressure_snapshot(group_id_str)
                else:
                    pressure_add, current_pressure, current_threshold, has_interest_kw, combo = update_message_pressure(
                        group_id_str, str(sender_id), raw_message
                    )

                RECENT_GROUP_MSGS[group_id_str].append({
                    "time": time.time(),
                    "sender_id": sender_id,
                    "nickname": nickname,
                    "text": display_msg,
                    "message_id": message_id,
                    "is_bot": False
                })

                kw_tag = "+兴趣" if has_interest_kw else ("(Bot白名单免压)" if is_whitelisted_bot else "")
                rel_tag = "🤖[群Bot白名单]" if is_whitelisted_bot else get_user_relation_tag(str(sender_id))
                soft_turns = get_group_soft_cap_turns(group_id_str)
                print(
                    f"📩 收到群[{group_id}] {rel_tag}{nickname}: {display_msg} | "
                    f"加压: +{pressure_add:.2f}{kw_tag} | 压力: {current_pressure:.2f}/{current_threshold:.2f} "
                    f"(连击:{combo}|软上限轮次:{soft_turns}) | {get_current_runtime_status()}"
                )

                clean_text = re.sub(r'\[CQ:[^\]]+\]', '', raw_message).strip()
                has_image = ("[CQ:image" in raw_message) or ("[图片#" in display_msg)
                has_meaningful_content = (len(clean_text) > 0 and clean_text not in [".", "。", "？", "?", "！", "!", "…"]) or has_image

                formatted_message = f"{nickname}({sender_id}) 说：{display_msg}"
                should_enqueue = False
                is_awake_reply = False
                must_reply = False
                ephemeral_context = None
                debounce_wait = GROUP_DEBOUNCE_WAIT  # 默认群聊聚合防抖 4.8s

                is_at_bot = (f"[CQ:at,qq={BOT_QQ}]" in raw_message) or is_replying_to_bot
                is_called_name = any(n in raw_message for n in ["弥酱", "星见弥", "弥弥"])

                # 优先处理群友专属指令：别理我 / 暂时别回复我 / 可以理我了
                if (is_at_bot or is_called_name) and not is_whitelisted_bot:
                    clean_cmd_text = display_msg.replace(f"[CQ:at,qq={BOT_QQ}]", "").strip()
                    is_mute_cmd, mute_sec, mute_desc = parse_mute_command(clean_cmd_text)
                    if is_mute_cmd:
                        mute_user(str(sender_id), mute_sec, mute_desc, group_id_str)
                        ack_msg = f"[CQ:at,qq={sender_id}] 好哦，弥酱接下来的 {mute_desc} 都不主动回复你啦～好好休息喵！（想让弥酱理你时随时说【可以理我了】就好啦～）"
                        await send_group_msg_raw(int(group_id), ack_msg)
                        return

                    if parse_unmute_command(clean_cmd_text):
                        was_muted = unmute_user(str(sender_id))
                        if was_muted:
                            ack_msg = f"[CQ:at,qq={sender_id}] 收到！已经解除静默啦，弥酱又可以陪你聊天了喵～"
                        else:
                            ack_msg = f"[CQ:at,qq={sender_id}] 弥酱本来就可以理你呀～随时找我玩喵！"
                        await send_group_msg_raw(int(group_id), ack_msg)
                        return

                    if is_user_muted(str(sender_id)):
                        print(f"[用户静默拦截] 用户[{nickname}({sender_id})] 处于静默保护期，已自动忽略其直接呼叫。")
                        is_at_bot = False
                        is_called_name = False

                # 【小游戏双轨路由】：优先检测当前进行中棋局 / 干脆开局指令
                game_extra_ctx = None
                if not is_whitelisted_bot and not is_user_muted(str(sender_id)):
                    clean_game_input = re.sub(r'\[CQ:[^\]]+\]', '', raw_message).strip()
                    fast_done, game_extra_ctx = await _handle_game_dual_track(
                        chat_id_str=chat_id_str,
                        group_id=group_id,
                        sender_id=sender_id,
                        nickname=nickname,
                        raw_clean_text=clean_game_input,
                        raw_message=raw_message,
                        is_private=False,
                    )
                    if fast_done:
                        return

                if game_extra_ctx:
                    should_enqueue = True
                    is_awake_reply = True
                    must_reply = True
                    debounce_wait = DIRECT_DEBOUNCE_WAIT
                    reset_group_pressure(group_id_str)
                    reset_awake_counter(group_id_str)
                    clean_msg = re.sub(r'\[CQ:[^\]]+\]', '', display_msg).strip()
                    formatted_message = f"{nickname}({sender_id}) 在【桌游对弈中】说道：{clean_msg}"
                    ephemeral_context = (ephemeral_context or "") + game_extra_ctx

                # 触发条件 1：被 @、叫名字或引用回复
                # - 人类群友 @：立即刷新软上限轮次计数归零，激活 80% 余温高底压并必回；
                # - 白名单群 Bot @：不刷新软上限计数，且受 10-15 轮软上限约束（>=15轮不再被白名单Bot直接@唤醒，防双机死循环）。
                elif is_at_bot or is_called_name:
                    if is_whitelisted_bot and soft_turns >= SOFT_CAP_ZERO_TURN:
                        print(f"🛡️ [Bot白名单防套娃] 群[{group_id}] 已达 {soft_turns} 轮软上限，忽略白名单 Bot [{nickname}] 的呼叫。")
                    else:
                        should_enqueue = True
                        is_awake_reply = True
                        must_reply = not is_whitelisted_bot
                        if not is_whitelisted_bot:
                            reset_group_pressure(group_id_str)
                            debounce_wait = DIRECT_DEBOUNCE_WAIT  # 直接@或叫名字，快响应 2.2s 防抖
                        reset_awake_counter(group_id_str)
                        clean_msg = display_msg.replace(f"[CQ:at,qq={BOT_QQ}]", "").strip()
                        formatted_message = f"{nickname}({sender_id}) @了你并说道：{clean_msg}"
                        ephemeral_context = (
                            "刚才有人在群里特意提到了你（或叫了你的名字）。请自然地回应他，直接发消息说话即可，不要反向机械 @ 对方。"
                        )

                # 触发条件 2：话题压力突破阈值（白名单 Bot 发言不加压且不触发压力爆发）
                elif not is_whitelisted_bot and current_pressure >= current_threshold and has_meaningful_content:
                    if is_user_muted(str(sender_id)):
                        pass
                    elif is_channel_busy(chat_id_str):
                        # 思考期降维：消息已进入 RECENT_GROUP_MSGS，作为下轮背景，不触发压力爆发与新调用
                        print(f"🪶 群[{group_id}] 压力已满但通道正在回复中，消息仅沉淀为下轮背景，不重复触发。")
                    elif is_in_pressure_cooldown(group_id_str, cooldown_sec=8.0) and chat_id_str not in DEBOUNCE_QUEUES:
                        print(f"🛡️ 群[{group_id}] 压力爆发短冷却保护中，跳过瞬时重复触发。")
                    elif CONSECUTIVE_BOT_MSG_COUNT.get(group_id_str, 0) < 3:
                        in_sess = chat_id_str in ACTIVE_SESSIONS
                        tag_str = "余温/会话顺接" if in_sess else "Layer 2 压力爆发"
                        print(f"💥 [{tag_str}] 群[{group_id}] 压力达标 ({current_pressure:.2f}/{current_threshold:.2f} | 软上限轮次:{soft_turns})，进入 5s 防抖！| {get_current_runtime_status()}")
                        trigger_pressure_burst(group_id_str, current_threshold)
                        reset_awake_counter(group_id_str)
                        should_enqueue = True
                        is_awake_reply = True
                        if in_sess:
                            ephemeral_context = (
                                "你正在群里和大家热聊中，群友接着刚才的话头继续发言了。请顺着最新消息自然接话，直接说话即可，不要机械 @ 对方。"
                            )
                        else:
                            ephemeral_context = (
                                "群里大家聊得很热闹，你的话题压力已满，忍不住想插嘴！若针对特定某句话插嘴，可用 reply_to=目标msg_id 引用；若是顺着当前话头接话，直接发言即可。"
                            )
                    else:
                        print(f"🛑 群[{group_id}] 插话拦截：机器人已连续发言达上限(3条)，等待群友接话。")
                        cap_group_pressure(group_id_str, current_threshold)

                # 触发条件 3：当前通道正处于 5s 防抖倒计时收集中，期间人类群友追加的有效发言自动汇入本次 5s 防抖包
                elif not is_whitelisted_bot and chat_id_str in DEBOUNCE_QUEUES and has_meaningful_content and not is_user_muted(str(sender_id)):
                    should_enqueue = True
                    is_awake_reply = True

                if not is_whitelisted_bot:
                    add_user_exp(str(sender_id), 1.0)

                bili_link = detect_bili_link(display_msg) if not is_whitelisted_bot else None
                if bili_link:
                    prefetched = await prefetch_bili_watch_context(bili_link)
                    if prefetched:
                        bili_hint = "\n" + prefetched
                    else:
                        bili_hint = (
                            f"\n【B站视频嗅探提示】：检测到群友分享了 B站 视频[{bili_link}]！"
                            "可调用 bili_video(action='inspect', ...) 读取字幕与热评后展开讨论吐槽！"
                        )
                    ephemeral_context = (ephemeral_context or "") + bili_hint

                if should_enqueue:
                    if not is_whitelisted_bot:
                        add_user_affection(str(sender_id), 1.0, raw_message)
                    await enqueue_message(
                        chat_id_str, group_id, sender_id, formatted_message,
                        is_private=False, is_awake_reply=is_awake_reply,
                        must_reply=must_reply, ephemeral_context=ephemeral_context,
                        debounce_wait=debounce_wait
                    )

            # --- 2. 处理私聊 ---
            elif message_type == 'private':
                print(f"📩 收到私聊[{nickname}({sender_id})] 消息: {raw_message} | {get_current_runtime_status()}")
                chat_id_str = f"private_{sender_id}"

                if str(sender_id) == str(MASTER_QQ):
                    bili_login_keywords = ["登录b站", "b站登录", "b站扫码", "绑定b站", "b站状态", "b站账号", "检查b站"]
                    if any(kw in raw_message.lower() for kw in bili_login_keywords):
                        await handle_master_bili_login(sender_id, None)
                        return

                    bili_logout_keywords = ["退出b站", "b站退出", "解绑b站", "b站解绑", "b站注销", "注销b站"]
                    if any(kw in raw_message.lower() for kw in bili_logout_keywords):
                        await handle_master_bili_logout(sender_id, None)
                        return

                    bili_history_keywords = ["b站历史", "看b站历史", "观看历史", "刷啥视频", "刷了啥", "你看过啥", "冲浪历史", "今日冲浪", "浏览记录"]
                    if any(kw in raw_message.lower() for kw in bili_history_keywords):
                        await handle_master_bili_history(sender_id, None)
                        return

                add_user_exp(str(sender_id), 1.0)
                add_user_affection(str(sender_id), 1.0, raw_message)

                LAST_HUMAN_MSG_TIME[chat_id_str] = time.time()
                CONSECUTIVE_BOT_MSG_COUNT[chat_id_str] = 0
                RECENT_GROUP_MSGS[chat_id_str].append({
                    "time": time.time(),
                    "sender_id": sender_id,
                    "nickname": nickname,
                    "text": display_msg,
                    "message_id": message_id,
                    "is_bot": False
                })
                save_chat_state()
                record_chat_archive(chat_id_str, str(sender_id), nickname, display_msg, is_bot=False)

                clean_game_input = re.sub(r'\[CQ:[^\]]+\]', '', raw_message).strip()
                fast_done, game_extra_ctx = await _handle_game_dual_track(
                    chat_id_str=chat_id_str,
                    group_id=None,
                    sender_id=sender_id,
                    nickname=nickname,
                    raw_clean_text=clean_game_input,
                    raw_message=raw_message,
                    is_private=True,
                )
                if fast_done:
                    return

                ephemeral_context = game_extra_ctx
                bili_link = detect_bili_link(display_msg)
                if bili_link:
                    prefetched = await prefetch_bili_watch_context(bili_link)
                    if prefetched:
                        ephemeral_context = (ephemeral_context or "") + "\n" + prefetched
                    else:
                        bili_hint = (
                            f"【B站视频嗅探提示】：对方私聊分享了 B站 视频[{bili_link}]！"
                            "可调用 bili_video(action='inspect', ...) 读取字幕与热评后与对方深入讨论！"
                        )
                        ephemeral_context = (ephemeral_context or "") + "\n" + bili_hint

                # 私聊统一进入 5.0s 防抖，激活/续期 10 分钟 (600s) 长寿命会话
                formatted_message = f"{nickname}({sender_id}) 私聊你说：{display_msg}"
                await enqueue_message(
                    chat_id_str, None, sender_id, formatted_message,
                    is_private=True, is_awake_reply=False,
                    must_reply=True, ephemeral_context=ephemeral_context,
                    debounce_wait=DIRECT_DEBOUNCE_WAIT
                )

        # --- 3. 处理通知类事件 (戳一戳 / 消息撤回) ---
        elif post_type == 'notice':
            await handle_notice_event(data)

        # --- 4. 处理请求类事件 (好友申请全额自动通过 + 主人实时汇报) ---
        elif post_type == 'request':
            await handle_request_event(data)

    except Exception as e:
        print(f"处理事件时出错: {e}")


# --- 戳一戳与撤回冷却字典 ---
LAST_POKE_TIME = {}
LAST_RECALL_TIME = {}


async def handle_notice_event(data: dict):
    """处理 OneBot notice 通知类事件 (戳一戳、群消息撤回)"""
    notice_type = data.get("notice_type")

    # 1. 戳一戳 (Poke)
    if notice_type == "notify" and data.get("sub_type") == "poke":
        target_id = str(data.get("target_id", ""))
        if target_id != str(BOT_QQ):
            return

        user_id = data.get("user_id")
        user_id_str = str(user_id)
        group_id = data.get("group_id")
        is_private = group_id is None or int(group_id) == 0

        if not is_private:
            group_id_str = str(group_id)
            if group_id_str not in get_allowed_groups():
                return
            chat_id_str = f"group_{group_id}"
            reset_group_pressure(group_id_str)
        else:
            chat_id_str = f"private_{user_id}"

        now = time.time()
        cooldown_key = (user_id_str, chat_id_str)
        if is_user_muted(user_id_str):
            print(f"🐾 [戳一戳忽略] 用户 {user_id_str} 处于静默保护期，忽略戳一戳响应。")
            return

        if now - LAST_POKE_TIME.get(cooldown_key, 0) < 15.0:
            print(f"🐾 [戳一戳节流] {chat_id_str} 来自 {user_id_str} 的戳一戳触发过快，忽略本次大模型唤醒。")
            return
        LAST_POKE_TIME[cooldown_key] = now

        nickname = str(user_id)
        profile = get_user_profile(user_id_str)
        if profile and profile.get("nickname"):
            nickname = profile["nickname"]

        rel_tag = get_user_relation_tag(user_id_str)
        print(f"🐾 [戳一戳唤醒] {rel_tag}{nickname}({user_id_str}) 在 {chat_id_str} 戳了戳弥酱！")

        if is_private:
            formatted_message = f"[系统提示: {rel_tag}{nickname}({user_id_str}) 在私聊中戳了戳你的猫耳/脸颊]"
        else:
            formatted_message = f"[系统提示: 群友 {rel_tag}{nickname}({user_id_str}) 戳了戳你的猫耳/脸颊]"

        ephemeral_context = (
            f"刚才 {rel_tag}{nickname} 戳了戳你的猫耳/脸颊。请结合熟悉度背景与互动边界做出自然的猫娘反应；若想互动可调用 poke_user 工具回戳一下！"
        )

        add_user_exp(user_id_str, 1.0)
        add_user_affection(user_id_str, 1.0, "[戳一戳]")

        await enqueue_message(
            chat_id_str, group_id if not is_private else None, user_id,
            formatted_message, is_private=is_private, is_awake_reply=False,
            must_reply=True, ephemeral_context=ephemeral_context, debounce_wait=DIRECT_DEBOUNCE_WAIT
        )

    # 2. 群消息撤回 (Group Recall)
    elif notice_type == "group_recall":
        group_id = data.get("group_id")
        group_id_str = str(group_id)
        if group_id_str not in get_allowed_groups():
            return

        chat_id_str = f"group_{group_id}"
        if chat_id_str in DEBOUNCE_QUEUES or chat_id_str in ACTIVE_AI_TASKS:
            return

        user_id = data.get("user_id")
        user_id_str = str(user_id)
        operator_id = data.get("operator_id")
        operator_id_str = str(operator_id)
        message_id = str(data.get("message_id", ""))

        if operator_id_str != user_id_str:
            print(f"🤫 [群消息撤回] 群[{group_id}] 管理员 {operator_id_str} 撤回了用户 {user_id_str} 的消息，弥酱保持沉默。")
            return

        if user_id_str == str(BOT_QQ):
            return

        if random.random() > 0.08:
            return

        now = time.time()
        if now - LAST_RECALL_TIME.get(group_id_str, 0) < 600.0:
            return

        msg_detail = await get_message_detail(message_id)
        if not msg_detail:
            return
        recalled_text = msg_detail.get("text", "").strip()
        sender_nick = msg_detail.get("nickname") or user_id_str

        clean_recalled = re.sub(r'\[CQ:[^\]]+\]', '', recalled_text).strip()
        trivial_words = ["好的", "收到", "哈哈", "确实", "没啥", "发错了", "打错了", "撤回", "1", "2", "？", "!", "。", "."]
        if len(clean_recalled) < 6 or clean_recalled in trivial_words:
            return

        LAST_RECALL_TIME[group_id_str] = now
        print(f"👀 [撤回抓包吃瓜(低概率命中)] 群[{group_id}] {sender_nick}({user_id_str}) 撤回了: {recalled_text}")

        formatted_message = f"[系统事件: 群友 {sender_nick}({user_id_str}) 刚刚在群里撤回了一条消息，原内容是：“{recalled_text}”]"
        ephemeral_context = (
            f"群友 {sender_nick} 刚刚在群里手滑撤回了一条消息：“{recalled_text}”。若觉得有趣可随口调侃一句，若无需接话可保持沉默。"
        )

        await enqueue_message(
            chat_id_str, group_id, user_id,
            formatted_message, is_private=False, is_awake_reply=False,
            must_reply=False, ephemeral_context=ephemeral_context, debounce_wait=DEBOUNCE_WAIT_TIME
        )


async def handle_request_event(data: dict):
    """处理 OneBot request 请求类事件 (交由后台常驻 AGY 好友迎新官同意申请、定制破冰问候并向主人汇报)"""
    request_type = data.get("request_type")
    if request_type == "friend":
        user_id = data.get("user_id")
        user_id_str = str(user_id or "")
        comment = str(data.get("comment") or "").strip()
        flag = str(data.get("flag") or "")
        is_doubt = bool(data.get("sub_type") == "doubt" or data.get("is_doubt"))

        print(f"🤝 [收到好友申请事件] 来自 QQ: {user_id_str} | 验证留言: '{comment}' | flag: {flag}")
        from workers.friend_request_worker import enqueue_friend_request
        await enqueue_friend_request(
            user_id=user_id_str,
            flag=flag,
            comment=comment,
            is_doubt=is_doubt
        )
