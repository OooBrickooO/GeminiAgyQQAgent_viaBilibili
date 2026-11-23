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
    save_chat_state, record_bot_spoke, reset_awake_counter,
    is_channel_low_enabled, set_channel_low_enabled,
    get_low_effort_channels, clear_all_low_effort_channels
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
    handle_master_bili_history, handle_master_bili_diagnose,
    handle_master_bili_test_dynamic, detect_bili_link
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
GROUP_DEBOUNCE_WAIT = 3.0
DIRECT_DEBOUNCE_WAIT = 3.0
DEBOUNCE_WAIT_TIME = GROUP_DEBOUNCE_WAIT
MAX_DEBOUNCE_CAP = 12.0
DIRECT_MAX_DEBOUNCE_CAP = 8.0

# --- 通道并发锁 ---
CHAT_LOCKS = {}

# --- 正在“整轮回复”中的通道集合 ---
# 覆盖 trigger_ai 的完整生命周期（组装 Prompt、等信号量、agy 推理、发信收尾），
# 比 ACTIVE_AI_TASKS（仅在子进程存活期有效）更完整，用于思考期新消息降维。
INFLIGHT_TURNS = set()

# --- 阶段四 4.1 思考期急件待补答暂存池 ---
# 当通道处于 INFLIGHT_TURNS 繁忙期时，若收到群聊直接 @/叫名字/引用回复弥酱（或显式急件如桌宠说话/打字），
# 将急件暂存于此，待当前轮次彻底结束并释放 INFLIGHT_TURNS 后自动拉起极短防抖补答，杜绝重要呼叫被吞；
# QQ 私聊不入此池，保持“未开口可合并/打断，已开口/收尾走思考期降维”的老策略。
PENDING_URGENT_TURNS = {}

# --- 私聊开机前热合并缓冲 ---
# 覆盖 INFLIGHT_TURNS 已登记、但 agy 子进程尚未启动前的窗口期（如排队等全局信号量/组装 Prompt），
# 让等待期间新到达的私聊消息直接并入当前轮次，无需拆成两轮回复。
PRE_SPAWN_PRIVATE_BUFFERS = {}


def get_current_runtime_status() -> str:
    """获取当前看板状态"""
    return format_runtime_status(list(DEBOUNCE_QUEUES.keys()))


def is_channel_busy(chat_id_str: str) -> bool:
    """通道是否正处于一轮回复流程中（含子进程思考期与前后收尾期）"""
    return chat_id_str in ACTIVE_AI_TASKS or chat_id_str in INFLIGHT_TURNS


def _build_private_combined_message(
    chat_id_str: str,
    sender_id,
    messages_list: list,
    is_cont: bool
) -> str:
    """组装私聊近期上下文背景与 L0 最新聚焦消息块"""
    priv_context = build_private_chat_context(
        str(sender_id),
        exclude_user_count=len(messages_list),
        max_history=3 if is_cont else 6
    )
    focus_title = (
        "【L0 核心响应焦点 (主人刚刚在电脑桌宠上对你说的话或桌面事件)：】\n"
        if chat_id_str == "desktop_master"
        else "【L0 核心响应焦点 (对方刚刚发送给你的私聊消息)：】\n"
    )
    new_messages_part = focus_title + "\n".join(messages_list)
    return f"{priv_context}\n\n{new_messages_part}" if priv_context else new_messages_part


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
    ephemeral_context: str = None,
    was_interrupted: bool = False
) -> list:
    """获取或续期长寿命 Session，组装 Prompt 并唤起大模型思考（包装层：全程登记通道繁忙状态，并在结束后检查待补答急件）"""
    INFLIGHT_TURNS.add(chat_id_str)
    try:
        return await _trigger_ai_impl(
            chat_id_str, group_id, sender_id, combined_message,
            is_breathe=is_breathe, is_private=is_private, is_awake_reply=is_awake_reply,
            messages_list=messages_list, must_reply=must_reply, ephemeral_context=ephemeral_context,
            was_interrupted=was_interrupted
        )
    finally:
        INFLIGHT_TURNS.discard(chat_id_str)
        PRE_SPAWN_PRIVATE_BUFFERS.pop(chat_id_str, None)
        urgent = PENDING_URGENT_TURNS.pop(chat_id_str, None)
        if urgent and urgent.get("messages"):
            print(
                f"📬 [思考期急件补答] 通道 {chat_id_str} 刚结束上一轮回复，检测到思考期暂存的 "
                f"{len(urgent['messages'])} 条重要呼叫急件，立即拉起极短防抖补答！"
            )
            now_u = time.time()
            followup_wait = 1.2
            if chat_id_str in DEBOUNCE_QUEUES:
                q = DEBOUNCE_QUEUES[chat_id_str]
                q["messages"] = urgent["messages"] + q["messages"]
                q["must_reply"] = True
                q["sender_id"] = urgent.get("sender_id", q.get("sender_id"))
                if urgent.get("ephemeral_context") and not q.get("ephemeral_context"):
                    q["ephemeral_context"] = urgent["ephemeral_context"]
            else:
                DEBOUNCE_QUEUES[chat_id_str] = {
                    "messages": list(urgent["messages"]),
                    "group_id": urgent.get("group_id", group_id),
                    "sender_id": urgent.get("sender_id", sender_id),
                    "is_private": urgent.get("is_private", is_private),
                    "is_awake_reply": True,
                    "must_reply": True,
                    "ephemeral_context": urgent.get("ephemeral_context"),
                    "first_queued_time": now_u,
                    "last_queued_time": now_u,
                    "debounce_wait": followup_wait,
                    "was_interrupted": False,
                    "task": asyncio.create_task(process_debounced_queue(chat_id_str, followup_wait)),
                }


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
    ephemeral_context: str = None,
    was_interrupted: bool = False
) -> list:
    """获取或续期长寿命 Session (群聊7min/私聊10min)，组装 Prompt 并唤起大模型思考"""
    if messages_list is None:
        messages_list = [combined_message]

    pre_spawn_buf = None
    if is_private:
        pre_spawn_buf = {
            "extra_messages": [],
            "extra_ephemeral": None,
            "spawned": False,
        }
        PRE_SPAWN_PRIVATE_BUFFERS[chat_id_str] = pre_spawn_buf

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

    from core.intent_router import route_message_domains, route_chat_effort_with_reason
    merged_msg_text = "\n".join(messages_list or [combined_message])
    target_domains = await route_message_domains(
        message_text=merged_msg_text,
        sender_id=str(sender_id),
        is_private=is_private,
        is_breathe=is_breathe,
        chat_id_str=chat_id_str
    )
    soft_turns = get_group_soft_cap_turns(str(group_id)) if (not is_private and group_id) else 0
    # 仅检查本轮最新消息 (merged_msg_text / messages_list) 与 ephemeral_context 是否含图片，
    # 严禁扫描包含 25 分钟 L1 群历史的 combined_message，避免历史旧图污染纯文字压力出场与普通冒泡。
    focus_img_probe = f"{merged_msg_text}\n{ephemeral_context or ''}"
    ctx_has_img = ("[图片#" in focus_img_probe) or ("[CQ:image" in focus_img_probe)
    chat_effort, effort_rule_tag = route_chat_effort_with_reason(
        message_text=merged_msg_text,
        is_continuation_turn=is_continuation_turn,
        is_breathe=is_breathe,
        target_domains=target_domains,
        is_private=is_private,
        sender_id=str(sender_id),
        was_interrupted=was_interrupted,
        is_retry=False,
        must_reply=must_reply,
        ephemeral_context=ephemeral_context or "",
        soft_cap_turns=soft_turns,
        context_has_image=ctx_has_img,
        chat_id_str=chat_id_str,
    )
    print(f"🧭 [局部档位路由] 通道 {chat_id_str} -> effort={chat_effort} | 命中规则: {effort_rule_tag}")

    async def _rebuild_if_buffered():
        nonlocal prompt, target_domains, chat_effort, combined_message, ephemeral_context
        if not is_private or pre_spawn_buf is None or pre_spawn_buf.get("spawned"):
            return None
        extra_msgs = pre_spawn_buf["extra_messages"]
        extra_eph = pre_spawn_buf["extra_ephemeral"]
        pre_spawn_buf["spawned"] = True
        if not extra_msgs:
            return None
        drained = list(extra_msgs)
        extra_msgs.clear()
        pre_spawn_buf["extra_ephemeral"] = None
        messages_list.extend(drained)
        if extra_eph:
            ephemeral_context = f"{ephemeral_context}\n{extra_eph}" if ephemeral_context else extra_eph
        combined_message = _build_private_combined_message(
            chat_id_str=chat_id_str,
            sender_id=sender_id,
            messages_list=messages_list,
            is_cont=is_continuation_turn,
        )
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
        merged_now = "\n".join(messages_list)
        target_domains = await route_message_domains(
            message_text=merged_now,
            sender_id=str(sender_id),
            is_private=is_private,
            is_breathe=is_breathe,
            chat_id_str=chat_id_str
        )
        probe_now = f"{merged_now}\n{ephemeral_context or ''}"
        has_img_now = ("[图片#" in probe_now) or ("[CQ:image" in probe_now)
        chat_effort, effort_tag_now = route_chat_effort_with_reason(
            message_text=merged_now,
            is_continuation_turn=is_continuation_turn,
            is_breathe=is_breathe,
            target_domains=target_domains,
            is_private=is_private,
            sender_id=str(sender_id),
            was_interrupted=was_interrupted,
            is_retry=False,
            must_reply=must_reply,
            ephemeral_context=ephemeral_context or "",
            soft_cap_turns=0,
            context_has_image=has_img_now,
            chat_id_str=chat_id_str,
        )
        print(
            f"📦 [私聊开机前热合并] 通道 {chat_id_str} 合并等待期间新到的 {len(drained)} 条消息 "
            f"(累计 {len(messages_list)} 条 | 重路由 effort={chat_effort}, rule={effort_tag_now})"
        )
        return {
            "prompt": prompt,
            "messages_list": messages_list,
            "target_domains": target_domains,
            "effort": chat_effort,
        }

    try:
        tools_called = await execute_agy_inference(
            chat_id_str=chat_id_str,
            prompt=prompt,
            dynamic_conv_id=dynamic_conv_id,
            messages_list=messages_list,
            target_domains=target_domains,
            caller_id=str(sender_id),
            effort=chat_effort,
            pre_spawn_refresh=_rebuild_if_buffered if is_private else None
        )

        # 【阶段二 2.2.4 翻车自动升档熔断保险】：
        # 在必回场景（私聊、被直接 @/唤名、戳一戳，即 is_private or must_reply）下，
        # 若本轮 low 推理结束时未调用任何发信工具（静默未回复），自动升档至 medium 重跑 1 次！
        if chat_effort == "low" and (is_private or must_reply) and not is_breathe:
            state_key_chk = "desktop_master" if chat_id_str == "desktop_master" else (f"private_{sender_id}" if is_private else str(group_id))
            from workers.post_turn_observer import check_bot_spoke_in_turn
            spoke_low_disk, _ = check_bot_spoke_in_turn(state_key_chk, turn_start_ts)
            spoke_low_tools = any(
                t in (tools_called or [])
                for t in ("send_group_msg", "send_private_msg", "send_meme", "send_local_image", "send_image_to_group", "send_image_to_private", "send_voice_msg", "pet_say", "pet_ask")
            )
            if not spoke_low_tools and not spoke_low_disk:
                print(
                    f"⚠️ [翻车升档熔断] 通道 {chat_id_str} 在必回场景下使用 low 档未调用发信工具（静默未回复），"
                    "立即自动升档至 medium 重跑 1 次！"
                )
                tools_called = await execute_agy_inference(
                    chat_id_str=chat_id_str,
                    prompt=prompt,
                    dynamic_conv_id=dynamic_conv_id,
                    messages_list=messages_list,
                    target_domains=target_domains,
                    caller_id=str(sender_id),
                    effort="medium"
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

        state_key = "desktop_master" if chat_id_str == "desktop_master" else (f"private_{sender_id}" if is_private else str(group_id))
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
                if chat_id_str == "desktop_master":
                    from tools.deskpet_tools import pet_say_impl
                    await pet_say_impl(f"【疑惑】{fallback_msg}", emotion="auto", voice=False)
                    tools_called = ["pet_say"]
                elif not is_private and group_id:
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
    state_key = "desktop_master" if chat_id_str == "desktop_master" else (f"private_{sender_id}" if is_private else str(group_id))
    if 'check_bot_spoke_in_turn' not in locals():
        from workers.post_turn_observer import check_bot_spoke_in_turn
    if 'bot_spoke_disk' not in locals():
        bot_spoke_disk, bot_reply_disk = check_bot_spoke_in_turn(state_key, turn_start_ts)

    spoke = any(t in (tools_called or []) for t in ("send_group_msg", "send_private_msg", "send_meme", "send_local_image", "send_image_to_group", "send_image_to_private", "send_voice_msg", "pet_say", "pet_ask")) or bot_spoke_disk
    bot_reply = bot_reply_disk if ('bot_reply_disk' in locals() and bot_reply_disk) else ""
    if spoke and not bot_reply:
        await asyncio.sleep(0.3)
        bot_reply = get_latest_bot_reply(state_key, turn_start_ts)

    voice_used = ("send_voice_msg" in (tools_called or [])) or (bot_reply or "").startswith("[语音(") or (bot_reply or "").startswith("[桌宠气泡+")

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


async def process_debounced_queue(chat_id_str: str, wait_time: float = DEBOUNCE_WAIT_TIME):
    """等待防抖时间到期后，集中出队并拉起大模型推理"""
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
                if sender_id and chat_id_str != "desktop_master":
                    asyncio.create_task(set_input_status(int(sender_id), 1))

                combined_message = _build_private_combined_message(
                    chat_id_str=chat_id_str,
                    sender_id=sender_id,
                    messages_list=data["messages"],
                    is_cont=is_cont,
                )
            else:
                group_id_str = str(data["group_id"])
                since_ts = active_sess.get("last_inference_ts", 0.0) if is_cont else 0.0
                context_part = build_chat_context(group_id_str, since_ts=since_ts)
                new_messages_part = (
                    "【L0 核心响应焦点 (本次请集中针对以下最新消息做出反应)：】\n"
                    + "\n".join(data["messages"])
                )
                combined_message = f"{context_part}\n\n{new_messages_part}" if context_part else new_messages_part

            actual_wait = float(data.get("debounce_wait", wait_time or DEBOUNCE_WAIT_TIME))
            print(f"📦 [{actual_wait:.1f}s防抖结束] 通道 {chat_id_str} 消息聚合完毕 (共 {len(data['messages'])} 条) | {get_current_runtime_status()}")
            await trigger_ai(
                chat_id_str,
                data["group_id"],
                data["sender_id"],
                combined_message,
                is_private=is_private,
                is_awake_reply=data["is_awake_reply"],
                messages_list=data["messages"],
                must_reply=data.get("must_reply", False),
                ephemeral_context=data.get("ephemeral_context"),
                was_interrupted=bool(data.get("was_interrupted", False))
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
    debounce_wait: float = DEBOUNCE_WAIT_TIME,
    is_urgent_call: bool = False,
    no_queue_if_busy: bool = False
):
    """将消息放入防抖聚合队列（支持思考期急件待补答暂存与打断标记传递；no_queue_if_busy=True 时若已在合成语音/发信则不排队追发第二句）"""
    now = time.time()
    if chat_id_str in DEBOUNCE_QUEUES:
        q = DEBOUNCE_QUEUES[chat_id_str]
        q["messages"].append(raw_message)
        q["last_queued_time"] = now
        q["sender_id"] = sender_id
        if must_reply:
            q["must_reply"] = True
        if debounce_wait < float(q.get("debounce_wait", DEBOUNCE_WAIT_TIME)):
            q["debounce_wait"] = debounce_wait
        if ephemeral_context:
            existing_ctx = q.get("ephemeral_context", "")
            if not existing_ctx or "特意提到了你" in ephemeral_context:
                q["ephemeral_context"] = ephemeral_context

        first_time = q.get("first_queued_time", now)
        elapsed_total = now - first_time
        # 滑动防抖：私聊/直呼/桌宠走较短硬上限(8s)，普通群聊走 12s 上限
        debounce_cap = (
            DIRECT_MAX_DEBOUNCE_CAP
            if (is_private or q.get("must_reply") or debounce_wait < DEBOUNCE_WAIT_TIME)
            else MAX_DEBOUNCE_CAP
        )
        if elapsed_total < debounce_cap:
            if q["task"] and not q["task"].done():
                q["task"].cancel()
            next_wait = min(debounce_wait, max(0.5, debounce_cap - elapsed_total))
            q["task"] = asyncio.create_task(process_debounced_queue(chat_id_str, next_wait))

        print(f"📥 [防抖消息合并] 通道 {chat_id_str} 追加新消息 (当前积压: {len(q['messages'])}条) | {get_current_runtime_status()}")
    else:
        messages_list = [raw_message]
        was_interrupted = False

        if is_channel_busy(chat_id_str):
            task_info = ACTIVE_AI_TASKS.get(chat_id_str)
            if is_private and task_info and not task_info.get("spoke", False):
                # 私聊且本轮大模型尚未发出任何一句话：安全地中止并把新消息合并进去重新思考（最终只回答一次）
                print(f"\n⚡ [私聊打断] 私聊通道 {chat_id_str} 侦测到新消息且本轮尚未开口！正在中止正在运行的大模型以合并重新思考...")
                task_info["aborted"] = True
                was_interrupted = True
                try:
                    task_info["process"].terminate()
                except Exception:
                    pass
                messages_list = task_info["messages"] + messages_list
            else:
                pre_buf = PRE_SPAWN_PRIVATE_BUFFERS.get(chat_id_str) if is_private else None
                if is_private and not task_info and pre_buf and not pre_buf.get("spawned", False):
                    # 处于 INFLIGHT 组装/等信号量阶段尚未启动子进程：直接热合并进当前待思考队列，免于拆轮
                    pre_buf["extra_messages"].append(raw_message)
                    if ephemeral_context and not pre_buf.get("extra_ephemeral"):
                        pre_buf["extra_ephemeral"] = ephemeral_context
                    print(
                        f"⚡ [私聊开机前热合并] 通道 {chat_id_str} 正在等待调度尚未拉起推理，"
                        f"新消息已直接并入本轮 (待合并 {len(pre_buf['extra_messages'])} 条) | {get_current_runtime_status()}"
                    )
                    return
                if no_queue_if_busy:
                    print(f"🪶 [DeskPet 防排队合并] 通道 {chat_id_str} 已在合成语音或回复中，本次互动不排队追发第二句回复。| {get_current_runtime_status()}")
                    return
                # 【阶段四 4.1 思考期急件防漏接】：
                # 仅对群聊直接 @/叫名字/引用回复弥酱 (is_urgent_call / not is_private and must_reply)
                # 或显式标记急件（如桌宠语音/打字/回答 is_urgent_call=True）暂存入 PENDING_URGENT_TURNS；
                # 普通 QQ 私聊已开口 (spoke=True) 或收尾期恢复老方法，走下方思考期降维沉淀，避免连发逐条追答。
                if is_urgent_call or (not is_private and must_reply):
                    bucket = PENDING_URGENT_TURNS.get(chat_id_str)
                    if bucket:
                        bucket["messages"].append(raw_message)
                        bucket["sender_id"] = sender_id
                        if ephemeral_context and not bucket.get("ephemeral_context"):
                            bucket["ephemeral_context"] = ephemeral_context
                    else:
                        PENDING_URGENT_TURNS[chat_id_str] = {
                            "messages": [raw_message],
                            "group_id": group_id,
                            "sender_id": sender_id,
                            "is_private": is_private,
                            "ephemeral_context": ephemeral_context,
                            "queued_at": now,
                        }
                    print(
                        f"📌 [思考期急件登记] 通道 {chat_id_str} 正在回复中，收到重要呼叫（已暂存 "
                        f"{len(PENDING_URGENT_TURNS[chat_id_str]['messages'])} 条待补答急件，本轮结束后即刻补答）| {get_current_runtime_status()}"
                    )
                    return

                # 思考期降维：QQ 私聊已开口/收尾期与普通群聊水聊统一仅沉淀为下轮前情背景，不额外拉起补答轮
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
            "was_interrupted": was_interrupted,
            "task": None
        }
        print(f"📥 [新消息入队] 通道 {chat_id_str} (进入 {debounce_wait:.1f}s 防抖) | {get_current_runtime_status()}")
        DEBOUNCE_QUEUES[chat_id_str]["task"] = asyncio.create_task(process_debounced_queue(chat_id_str, debounce_wait))


async def _handle_master_low_effort_command(
    sender_id,
    raw_message: str,
    chat_id_str: str,
    is_private: bool,
    group_id: int = None,
    is_at_bot: bool = False,
) -> bool:
    """
    【模块一 · 主人专属 0-LLM 局部 low 开关指令】
    严格仅对 MASTER_QQ 开放，其他任何用户一律返回 False（不触发、不修改）。
    仅作用于当前所在对话通道 (chat_id_str)，不影响其他群聊或私聊。
    """
    if str(sender_id) != str(MASTER_QQ):
        return False

    clean = re.sub(r'\[CQ:[^\]]+\]', '', str(raw_message or '')).strip()
    clean = re.sub(r'^(?:@?(?:弥酱|星见弥|弥弥)|@\S+)[\s,，:：!！~～]*', '', clean).strip()
    cmd_norm = re.sub(r'\s+', '', clean).lower()
    if not cmd_norm:
        return False

    if not is_private and not is_at_bot:
        return False

    async def _reply(msg: str):
        if is_private:
            await send_private_msg_raw(int(sender_id), msg)
        elif group_id:
            await send_group_msg_raw(int(group_id), msg)

    if cmd_norm in ("开启low", "开启low档", "打开low", "打开low档", "启用low", "开low", "切换low"):
        label = f"主人私聊({sender_id})" if is_private else f"群聊({group_id})"
        set_channel_low_enabled(chat_id_str, True, label=label)
        await _reply(f"⚡ 已仅对当前对话通道 [{chat_id_str}] 开启局部强制 low 档（不影响其他群聊与私聊；发送「关闭low」可随时恢复默认 medium/high）。")
        return True

    if cmd_norm in ("关闭low", "关闭low档", "取消low", "停用low", "关low", "恢复medium", "退出low"):
        set_channel_low_enabled(chat_id_str, False)
        await _reply(f"✅ 已关闭当前对话通道 [{chat_id_str}] 的局部 low 档，恢复默认 medium/high 路由。")
        return True

    if cmd_norm in ("low状态", "查看low", "模型档位", "当前档位", "档位状态"):
        cur_on = is_channel_low_enabled(chat_id_str)
        state_str = "⚡ 局部强制 low（仅当前通道生效）" if cur_on else "✅ 默认 medium / high（已禁用 low）"
        await _reply(f"🧭 当前对话通道 [{chat_id_str}] 模型档位：{state_str}")
        return True

    if is_private and cmd_norm in ("查看全部low", "全部low", "low列表", "所有low"):
        enabled = get_low_effort_channels(force_reload=True)
        if not enabled:
            await _reply("✅ 当前没有任何对话通道开启 low 档，全通道均为默认 medium/high。")
        else:
            lines = ["⚡ 当前已开启局部强制 low 档的对话通道："]
            for cid, info in enabled.items():
                lbl = info.get("label") or cid
                lines.append(f"- {cid} ({lbl})")
            lines.append("（发送「全部关闭low」可一键清空恢复默认）")
            await _reply("\n".join(lines))
        return True

    if is_private and cmd_norm in ("全部关闭low", "关闭全部low", "清空low", "重置low"):
        clear_all_low_effort_channels()
        await _reply("✅ 已一键清空全部局部 low 开关，所有群聊与私聊均已恢复默认 medium/high。")
        return True

    return False


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
                        if PENDING_URGENT_TURNS.pop(chat_id_str, None) is not None:
                            print(f"🛑 [主人手动终止] 已清空通道 {chat_id_str} 的思考期急件待补答池！")
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

                    bili_diag_keywords = ["b站诊断", "诊断b站", "b站自检"]
                    if is_at_bot and any(kw in raw_message.lower() for kw in bili_diag_keywords):
                        await handle_master_bili_diagnose(sender_id, group_id)
                        return

                    bili_test_dyn_keywords = ["测试b站动态", "b站动态测试"]
                    if is_at_bot and any(kw in raw_message.lower() for kw in bili_test_dyn_keywords):
                        await handle_master_bili_test_dynamic(sender_id, group_id)
                        return

                    if await _handle_master_low_effort_command(
                        sender_id=sender_id,
                        raw_message=raw_message,
                        chat_id_str=chat_id_str,
                        is_private=False,
                        group_id=group_id,
                        is_at_bot=is_at_bot,
                    ):
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
                debounce_wait = GROUP_DEBOUNCE_WAIT  # 默认群聊聚合防抖 3.0s

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
                            debounce_wait = DIRECT_DEBOUNCE_WAIT  # 直接@或叫名字，统一 3.0s 防抖
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
                        print(f"💥 [{tag_str}] 群[{group_id}] 压力达标 ({current_pressure:.2f}/{current_threshold:.2f} | 软上限轮次:{soft_turns})，进入 {GROUP_DEBOUNCE_WAIT:.1f}s 防抖！| {get_current_runtime_status()}")
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

                # 触发条件 3：当前通道正处于防抖倒计时收集中，期间人类群友追加的有效发言自动汇入本次防抖包
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
                        debounce_wait=debounce_wait,
                        is_urgent_call=bool((is_at_bot or is_called_name) and not is_whitelisted_bot)
                    )

            # --- 2. 处理私聊 ---
            elif message_type == 'private':
                print(f"📩 收到私聊[{nickname}({sender_id})] 消息: {raw_message} | {get_current_runtime_status()}")
                chat_id_str = f"private_{sender_id}"

                if str(sender_id) == str(MASTER_QQ):
                    bili_diag_keywords = ["b站诊断", "诊断b站", "b站自检"]
                    if any(kw in raw_message.lower() for kw in bili_diag_keywords):
                        await handle_master_bili_diagnose(sender_id, None)
                        return

                    bili_test_dyn_keywords = ["测试b站动态", "b站动态测试"]
                    if any(kw in raw_message.lower() for kw in bili_test_dyn_keywords):
                        await handle_master_bili_test_dynamic(sender_id, None)
                        return

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

                    if await _handle_master_low_effort_command(
                        sender_id=sender_id,
                        raw_message=raw_message,
                        chat_id_str=chat_id_str,
                        is_private=True,
                        group_id=None,
                        is_at_bot=True,
                    ):
                        return

                    if await _handle_master_deskpet_command(
                        sender_id=sender_id,
                        raw_message=raw_message,
                        via_deskpet=False,
                    ):
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

                # 私聊统一进入 3.0s 防抖，激活/续期 10 分钟 (600s) 长寿命会话
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


# ============================================================
# 5. 星见弥桌宠 (DeskPet) 专属 0-LLM 指令与跨机事件处理引擎
# ============================================================
async def _handle_master_deskpet_command(
    sender_id,
    raw_message: str,
    via_deskpet: bool = False,
) -> bool:
    """
    主人专属 0-LLM 桌宠与主动看屏吐槽快捷控制指令：
    - 开启主动吐槽 / 关闭主动吐槽 / 开启主动看屏 / 关闭主动看屏
    - 开启局内吐槽 / 关闭局内吐槽
    - 桌宠状态
    """
    if str(sender_id) != str(MASTER_QQ):
        return False

    clean = re.sub(r'\[CQ:[^\]]+\]', '', str(raw_message or '')).strip()
    clean = re.sub(r'^(?:@?(?:弥酱|星见弥|弥弥)|@\S+)[\s,，:：!！~～]*', '', clean).strip()
    cmd_norm = re.sub(r'\s+', '', clean).lower()
    if not cmd_norm:
        return False

    from tools.deskpet_tools import pet_set_impl, pet_say_impl, get_deskpet_overview

    async def _reply_ack(msg_text: str):
        if via_deskpet:
            await pet_say_impl(f"【开心】{msg_text}", emotion="auto", voice=False)
        else:
            await send_private_msg_raw(int(MASTER_QQ), msg_text)

    if cmd_norm in ("开启主动吐槽", "打开主动吐槽", "开启主动看屏", "打开主动看屏", "开启看屏吐槽"):
        res = await pet_set_impl(proactive_peek=True)
        await _reply_ack(f"好耶～弥酱已经开启主动看屏吐槽啦（含游戏局内实时陪伴）喵！\n{res}")
        return True

    if cmd_norm in ("关闭主动吐槽", "关掉主动吐槽", "关闭主动看屏", "关掉主动看屏", "关闭看屏吐槽", "暂停主动吐槽"):
        res = await pet_set_impl(proactive_peek=False)
        await _reply_ack(f"收到～弥酱已经关掉主动看屏吐槽啦，安安静静待在桌面陪主人喵！\n{res}")
        return True

    if cmd_norm in ("开启局内吐槽", "打开局内吐槽", "允许局内吐槽"):
        res = await pet_set_impl(allow_in_game=True)
        await _reply_ack(f"收到！打游戏局内也允许弥酱实时看屏吐槽战况啦喵～\n{res}")
        return True

    if cmd_norm in ("关闭局内吐槽", "关掉局内吐槽", "禁止局内吐槽"):
        res = await pet_set_impl(allow_in_game=False)
        await _reply_ack(f"明白～打游戏局内弥酱就不主动冒气泡打扰主人操作啦喵！\n{res}")
        return True

    if cmd_norm in ("桌宠状态", "查看桌宠", "桌宠诊断"):
        ov = await get_deskpet_overview()
        online_str = "🟢 在线" if ov.get("online") else "⚪ 离线 (Windows 端未启动)"
        win_str = "已连接透明穿透窗口" if ov.get("pet_window_connected") else "未打开窗口"
        peek_str = "开启" if ov.get("proactive_peek") else "关闭"
        game_str = "允许局内吐槽" if ov.get("allow_in_game") else "局内免打扰"
        fg_str = f"[{ov.get('foreground_app')}] {ov.get('foreground_title')}" if ov.get("foreground_app") else "未知"
        cua_str = f"{'开启' if ov.get('cua_enabled') else '关闭'} (权限档位: {ov.get('cua_permission')})"
        report = (
            f"🐾 【星见弥桌宠 (DeskPet) 状态看板】\n"
            f"- 客户端状态: {online_str} ({win_str})\n"
            f"- 当前形象: {ov.get('figure_name')} ({int(float(ov.get('scale', 1.0)) * 100)}%)\n"
            f"- 主动看屏吐槽: {peek_str} ({game_str} | 日常间隔:{ov.get('normal_interval_sec')}s / 游戏间隔:{ov.get('game_interval_sec')}s)\n"
            f"- CUA 电脑操作: {cua_str}\n"
            f"- 当前电脑前台: {fg_str}"
        )
        await _reply_ack(report)
        return True

    return False


async def handle_deskpet_event(payload: dict):
    """
    接收来自 Windows 桌宠客户端 (DeskPet :7797 -> :18302/deskpet_event) 的实时交互与看屏事件：
    - event_type: 'typed' | 'speech' | 'answer' | 'touch' | 'screen_peek'
    """
    try:
        event_type = str(payload.get("event_type", "typed")).strip().lower()
        raw_text = str(payload.get("text", "")).strip()
        must_reply = bool(payload.get("must_reply", True))
        interrupt = bool(payload.get("interrupt", False))
        chat_id_str = "desktop_master"
        sender_id = str(MASTER_QQ)

        print(f"🐾 [DeskPet 事件] 类型={event_type} | 内容={raw_text[:80]} | {get_current_runtime_status()}")

        # 1. 处理主人手动终止指令或 0-LLM 桌宠开关指令
        if event_type in ("typed", "speech") and raw_text:
            stop_keywords = ["停", "停止", "终止", "闭嘴", "别说了", "打住", "stop"]
            if raw_text.lower() in stop_keywords:
                if chat_id_str in ACTIVE_AI_TASKS:
                    t_info = ACTIVE_AI_TASKS.pop(chat_id_str, None)
                    if t_info:
                        t_info["aborted"] = True
                        try:
                            t_info["process"].terminate()
                        except Exception:
                            pass
                if chat_id_str in DEBOUNCE_QUEUES:
                    try:
                        DEBOUNCE_QUEUES[chat_id_str]["task"].cancel()
                    except Exception:
                        pass
                    DEBOUNCE_QUEUES.pop(chat_id_str, None)
                PENDING_URGENT_TURNS.pop(chat_id_str, None)
                PRE_SPAWN_PRIVATE_BUFFERS.pop(chat_id_str, None)
                print("🛑 [DeskPet 主人终止] 已中止桌宠通道当前推理。")
                return

            if await _handle_master_deskpet_command(sender_id, raw_text, via_deskpet=True):
                return

        # 2. 语音说话键 (LeftAlt*2) 强打断：即使当前轮已开口，说话键新语音也立即打断重跑（100% 复刻 Coopanion interrupt 语义）
        if interrupt:
            PENDING_URGENT_TURNS.pop(chat_id_str, None)
            if chat_id_str in ACTIVE_AI_TASKS:
                t_info = ACTIVE_AI_TASKS.get(chat_id_str)
                if t_info:
                    print("⚡ [DeskPet 语音键打断] 主人按住说话键发来新语音，立即打断正在进行的桌宠推理！")
                    t_info["aborted"] = True
                    t_info["spoke"] = False
                    try:
                        t_info["process"].terminate()
                    except Exception:
                        pass

        # 3. 构造各事件类型的消息与情境提示
        from tools.deskpet_tools import _save_cua_jpeg
        ephemeral_context = None
        debounce_wait = 0.4  # 桌宠打字/语音已是一整句，0.4s 极速响应

        if event_type == "screen_peek":
            # 若桌宠通道正忙，跳过本次主动看屏，绝不排队堆积
            if is_channel_busy(chat_id_str) or chat_id_str in DEBOUNCE_QUEUES:
                print("🪶 [DeskPet 主动看屏跳过] 桌宠通道正忙，跳过本次主动看屏。")
                return

            jpeg_b64 = str(payload.get("jpeg_base64", "")).strip()
            if not jpeg_b64:
                return
            shot_path = _save_cua_jpeg(jpeg_b64, prefix="peek")
            fg_app = str(payload.get("foreground_app", "")).strip()
            fg_title = str(payload.get("foreground_title", "")).strip()
            reason = str(payload.get("reason", "主动看一眼主人的电脑屏幕")).strip()
            in_game = bool(payload.get("in_game", False))
            game_tag = "【游戏局内实时战况观察】" if in_game else "【桌面日常陪伴看屏】"

            formatted_message = (
                f"[桌面伴随看屏 {game_tag}] 触发契机：{reason}\n"
                f"- 当前前台程序: [{fg_app}] {fg_title}\n"
                f"- 屏幕截图本地绝对路径: {shot_path}"
            )
            ephemeral_context = (
                f"{game_tag}你正站在主人电脑屏幕底边陪他，刚刚偷偷看了一眼他的电脑屏幕（触发原因：{reason}；前台窗口：[{fg_app}] {fg_title}）。\n"
                f"📸 **请第一步先调用 `view_file(AbsolutePath='{shot_path}')` 查看这张屏幕截图**，看清主人正在玩什么游戏（战况、血量、装备、操作或剧情）、写什么代码或看什么视频，\n"
                f"🔊【本轮语音策略：识屏 100% 必配日语语音 (中文气泡 + 日语真声)】：然后必须调用 `pet_say(script='中文气泡台词', ja_voice='地道日语猫娘配音台词', emotion='auto', voice=True)`"
                f"（`script` 用你作为猫娘弥酱鲜活、接地气的中文写一两句吐槽并带 `【得意】`/`【开心】`/`<眨眼>` 标记；`ja_voice` 填写对应意思的地道二次元猫娘日语口语）喵！"
            )
            debounce_wait = 0.2

        elif event_type == "speech":
            use_voice = (random.random() < 0.25)
            voice_policy_hint = (
                "🔊【本轮语音策略：开启日语配音 (中文气泡 + 日语语音)】调用 `pet_say` 时请传入 `voice=True`，`script` 写中文气泡台词，`ja_voice` 填入对应的地道二次元猫娘日语口语台词！"
                if use_voice else
                "🔇【本轮语音策略：仅冒中文气泡 (不配音秒回)】本次为普通日常对话，调用 `pet_say` 时请传 `voice=False`（不填 `ja_voice`），仅用生动的中文台词与【动作, 表情】秒回主人即可！（⚠️特例：如果本轮中你调用了 `pet_cua_screenshot` / `pet_cua_zoom` / `inspect_image` 查看屏幕或图片，则必须改为传 `voice=True` 并填写 `ja_voice`！）"
            )
            formatted_message = f"[voice] 主人({sender_id}) 在桌宠按住说话键对你说：{raw_text}"
            ephemeral_context = (
                "主人刚刚在电脑桌面上按住说话键直接对你说了这句话（本地语音转写，可能有同音字）。\n"
                f"{voice_policy_hint}\n"
                "若他让你看屏幕或操作电脑，请先调用 `pet_cua_screenshot` / `pet_cua_action`。"
            )
        elif event_type == "typed":
            # 若打字框粘贴了图片，落盘保存并附带路径供 view_file 查看
            imgs = payload.get("images") or []
            img_notes = []
            for idx, im in enumerate(imgs[:4], start=1):
                if isinstance(im, dict) and im.get("base64"):
                    p = _save_cua_jpeg(im["base64"], prefix=f"typed_img{idx}")
                    img_notes.append(f"[附图#{idx} 本地路径: {p}（可调 view_file 查看）]")
            img_suffix = ("\n" + "\n".join(img_notes)) if img_notes else ""
            has_attached_img = bool(img_notes)
            use_voice = True if has_attached_img else (random.random() < 0.25)
            if has_attached_img:
                voice_policy_hint = (
                    "🔊【本轮语音策略：打字附图 100% 必配日语语音 (中文气泡 + 日语真声)】主人给你发了图片！"
                    "请先用 `view_file` 看图，随后调用 `pet_say` 时必须传 `voice=True`，`script` 写中文气泡台词，`ja_voice` 填入对应的地道二次元猫娘日语配音台词！"
                )
            elif use_voice:
                voice_policy_hint = (
                    "🔊【本轮语音策略：开启日语配音 (中文气泡 + 日语语音)】调用 `pet_say` 时请传 `voice=True`，`script` 写中文气泡台词，`ja_voice` 填入对应的地道二次元猫娘日语口语台词！"
                )
            else:
                voice_policy_hint = (
                    "🔇【本轮语音策略：仅冒中文气泡 (不配音秒回)】本次为普通打字闲聊，调用 `pet_say` 时请传 `voice=False`（不填 `ja_voice`），仅用生动的中文气泡+【动作, 表情】秒回即可！（⚠️特例：若本轮主动调用了 `pet_cua_screenshot` / `pet_cua_zoom` / `inspect_image` 看屏看图，则必须改为 `voice=True` 并填 `ja_voice`！）"
                )
            formatted_message = f"[typed] 主人({sender_id}) 在桌宠打字框对你说：{raw_text}{img_suffix}"
            ephemeral_context = (
                "主人刚刚在电脑桌宠的打字框里给你发了消息。\n"
                f"{voice_policy_hint}\n"
                "若涉及看当前屏幕或帮忙操作电脑，可调用 `pet_cua_screenshot` / `pet_cua_zoom` / `pet_cua_action`！"
            )
        elif event_type == "answer":
            use_voice = (random.random() < 0.25)
            voice_policy_hint = (
                "🔊【本轮语音策略：开启日语配音】调用 `pet_say` 请传 `voice=True` 并填对应日语口语 `ja_voice`。"
                if use_voice else
                "🔇【本轮语音策略：仅冒中文气泡 (不配音秒回)】调用 `pet_say` 请传 `voice=False`（不填 `ja_voice`）秒回中文气泡即可！"
            )
            formatted_message = f"[answer] 主人回应了你刚才的桌宠提问气泡：{raw_text}"
            ephemeral_context = f"主人刚刚在你的桌宠提问气泡中做出了选择/回答，请调用 `pet_say` 顺着他的回答自然接话喵！\n{voice_policy_hint}"
        elif event_type == "touch":
            use_voice = (random.random() < 0.18)
            voice_policy_hint = (
                "🔊【本轮语音策略：惊喜开启日语配音】调用 `pet_say` 请传 `voice=True`，`script` 写中文撒娇/傲娇气泡，`ja_voice` 填入对应娇嗔/开心的日语猫娘口语（如 `もう！お兄ちゃんったら、くすぐったいにゃ～！`）！"
                if use_voice else
                "🔇【本轮语音策略：仅冒中文气泡 (不配音秒回)】本次为日常摸摸/戳戳互动，调用 `pet_say` 时请传 `voice=False`（不填 `ja_voice`），仅用中文气泡+`【害羞】`/`【生气】`/`【喜欢】`动作快速回应一句即可！"
            )
            formatted_message = f"[touch] 桌面互动：{raw_text}"
            ephemeral_context = (
                f"刚刚在电脑桌面上，{raw_text}（桌宠身体已经当场做出了物理反射）。\n"
                f"{voice_policy_hint}"
            )
            debounce_wait = 0.6
        else:
            return

        # 4. 记录主人的互动与经验值，并送入防抖调度队列
        add_user_exp(sender_id, 1.0)
        add_user_affection(sender_id, 1.0, raw_text or f"[{event_type}]")
        LAST_HUMAN_MSG_TIME[chat_id_str] = time.time()
        CONSECUTIVE_BOT_MSG_COUNT[chat_id_str] = 0
        if event_type != "screen_peek":
            record_chat_archive(chat_id_str, sender_id, "主人", f"[桌宠:{event_type}] {raw_text}", is_bot=False)

        await enqueue_message(
            chat_id_str=chat_id_str,
            group_id=None,
            sender_id=sender_id,
            raw_message=formatted_message,
            is_private=True,
            is_awake_reply=False,
            must_reply=must_reply,
            ephemeral_context=ephemeral_context,
            debounce_wait=debounce_wait,
            is_urgent_call=(event_type not in ("touch", "screen_peek")),
            no_queue_if_busy=(event_type in ("touch", "screen_peek")),
        )
    except Exception as e:
        print(f"⚠️ [DeskPet 事件处理异常]: {e}")

