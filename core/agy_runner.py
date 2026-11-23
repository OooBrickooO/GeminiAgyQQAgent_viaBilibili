"""
core/agy_runner.py — agy 子进程拉起、长寿命会话管理 (7m/10m TTL，语音触发单次延至 20m)、并发信号量与任务监控 V4.0

管理：
- ACTIVE_SESSIONS 会话生命周期管理：
  * 群聊激活后默认维持 7 分钟 (420s) 无对话寿命，私聊默认维持 10 分钟 (600s) 无对话寿命；
  * 若某轮触发语音合成 (send_voice_msg)，仅该次对话的存活寿命与单轮超时均放宽至 20 分钟 (1200s)，后续未触发语音则自动恢复默认；
  * 寿命期内复用同一 conv_id 保持极佳人设与短期记忆连贯性；
  * 到期无对话自动销毁会话，将群压力从 50% 归零，并异步拉起 `consolidate_session_memory` 整理记忆。
- GLOBAL_AGY_SEMAPHORE 全局并发控制与超时保护
- AGY_CALLER_ID 环境变量注入（支撑 MCP 工具层防注入硬权限校验）
"""
import os
import re
import signal
import sys
import tempfile
import time
import uuid
import random
import datetime
import asyncio
from config import (
    get_agy_env, BOT_NAMESPACE, DEFAULT_AGY_MODEL, DEFAULT_AGY_EFFORT,
    AGY_EXECUTABLE, build_agy_model_flags, BASE_DIR, MASTER_QQ, deskpet_log
)
from core.transcript_stream import watch_process_transcript

class PromptSafetyRejectedError(Exception):
    """Google Gemini 前置安全分类器拒收 Prompt 异常 (Pre-submission Policy Rejection)"""
    pass

# --- 会话寿命与单轮超时常量 (秒) ---
GROUP_SESSION_TTL = 420.0          # 群聊无对话存活寿命：7 分钟
PRIVATE_SESSION_TTL = 600.0        # 私聊无对话存活寿命：10 分钟
VOICE_SESSION_TTL = 1200.0         # 触发语音合成后的单次会话保活寿命：20 分钟

DEFAULT_INFERENCE_TIMEOUT = 600.0  # 默认单轮推理超时斩首上限：10 分钟
VOICE_INFERENCE_TIMEOUT = 1200.0   # 触发语音合成的单轮推理超时斩首上限：20 分钟


def get_base_session_ttl(is_private: bool) -> float:
    """获取通道默认的无对话存活寿命 (私聊10分钟 / 群聊7分钟)"""
    return PRIVATE_SESSION_TTL if is_private else GROUP_SESSION_TTL

# --- 滚动会话 (Rolling Session) ---
# 私聊每 5 轮、群聊每 8 轮无感轮转为新 conv_id，防止 agy 磁盘 Transcript 滚雪球导致输入 Token 二次方增长
PRIVATE_ROLLING_TURNS = 5
GROUP_ROLLING_TURNS = 8


def get_rolling_turn_limit(chat_id_str: str = "", is_private: bool = False) -> int:
    if is_private or str(chat_id_str).startswith("private_"):
        return PRIVATE_ROLLING_TURNS
    return GROUP_ROLLING_TURNS

# 视为“实质性开口”的发信类工具名
SEND_TOOL_NAMES = ("send_group_msg", "send_private_msg", "send_meme", "send_local_image", "send_image_to_group", "send_image_to_private", "send_voice_msg", "pet_say", "pet_ask")

# --- 全局并发控制 ---
GLOBAL_AGY_SEMAPHORE = asyncio.Semaphore(6)

# --- B站探索专属独立并发控制 ---
BILI_AGY_SEMAPHORE = asyncio.Semaphore(1)

# --- 慢车道事后内省观察者独立并发控制 ---
OBSERVER_AGY_SEMAPHORE = asyncio.Semaphore(1)

# --- 正在思考的任务字典 ---
# 结构: chat_id_str -> {"process": asyncio.subprocess.Process, "messages": list, "start_time": float}
ACTIVE_AI_TASKS = {}

# --- 活跃长寿命会话字典 ---
# 结构: chat_id_str -> {
#   "conv_id": str, "is_private": bool, "group_id": int,
#   "created_at": float, "last_active_at": float, "last_inference_ts": float,
#   "turn_count": int, "primary_sender_id": str, "turns_log": list,
#   "ttl_seconds": float, "expiry_task": asyncio.Task, "afterglow_task": asyncio.Task
# }
ACTIVE_SESSIONS = {}


def get_daily_conv_id(chat_id_str: str) -> str:
    """按天生成独立隔离的 UUID5 对话上下文 ID（供后台定时 Worker 等非交互任务使用）"""
    now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    today_str = now_utc8.strftime("%Y-%m-%d")
    daily_chat_id_str = f"{chat_id_str}_{today_str}"
    return str(uuid.uuid5(BOT_NAMESPACE, daily_chat_id_str))


def _schedule_session_expiry(chat_id_str: str):
    """启动或重置指定通道的无对话到期销毁计时器"""
    sess = ACTIVE_SESSIONS.get(chat_id_str)
    if not sess:
        return
    old_task = sess.get("expiry_task")
    if old_task and not old_task.done():
        old_task.cancel()
    ttl = sess["ttl_seconds"]
    sess["expiry_task"] = asyncio.create_task(_session_expiry_watchdog(chat_id_str, ttl))


def _schedule_afterglow_drop(chat_id_str: str, group_id: int, delay_sec: float = 45.5):
    """在群聊触发/发言后挂载 45s 余温结束回调，将 80% 余温底压准时回归至 50% 维持到会话结束"""
    sess = ACTIVE_SESSIONS.get(chat_id_str)
    if not sess or not group_id:
        return
    old_task = sess.get("afterglow_task")
    if old_task and not old_task.done():
        old_task.cancel()

    async def _afterglow_worker():
        try:
            await asyncio.sleep(delay_sec)
            if chat_id_str in ACTIVE_SESSIONS:
                from attention.pressure import sync_afterglow_decay
                sync_afterglow_decay(str(group_id))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"⚠️ [余温回调异常] {chat_id_str}: {e}")

    sess["afterglow_task"] = asyncio.create_task(_afterglow_worker())


async def _session_expiry_watchdog(chat_id_str: str, ttl_seconds: float):
    """监控会话无对话时长，到期后销毁会话、归零群压力并整理记忆"""
    try:
        while True:
            await asyncio.sleep(ttl_seconds)
            sess = ACTIVE_SESSIONS.get(chat_id_str)
            if not sess:
                return

            # 若当前通道正在思考中，等待思考完成后重新计算空闲时长
            if chat_id_str in ACTIVE_AI_TASKS:
                ttl_seconds = 15.0
                continue

            now = time.time()
            idle_sec = now - sess.get("last_active_at", now)
            target_ttl = sess.get("ttl_seconds", ttl_seconds)
            if idle_sec < (target_ttl - 1.0):
                ttl_seconds = max(2.0, target_ttl - idle_sec)
                continue

            # 正式到期：弹出并销毁会话
            expired_sess = ACTIVE_SESSIONS.pop(chat_id_str, None)
            if not expired_sess:
                return

            ag_task = expired_sess.get("afterglow_task")
            if ag_task and not ag_task.done():
                ag_task.cancel()

            is_private = expired_sess.get("is_private", False)
            group_id = expired_sess.get("group_id")
            turns_log = expired_sess.get("turns_log") or []
            mins = int(target_ttl // 60)

            print(
                f"🌙 [会话寿命到期] 通道 {chat_id_str} 已满 {mins} 分钟无对话输入，"
                f"销毁 agy 会话 (conv_id={expired_sess['conv_id'][:8]}..., 累计 {expired_sess.get('total_turns', expired_sess.get('turn_count', 0))} 轮)。"
            )

            # 群聊会话结束：压力从 50% 彻底归零，重新等待
            if not is_private and group_id:
                from attention.pressure import clear_group_pressure_on_session_end
                clear_group_pressure_on_session_end(str(group_id))

            # 异步整理本场会话记忆
            if turns_log:
                from workers.post_turn_observer import consolidate_session_memory
                snapshot = {
                    "chat_id_str": chat_id_str,
                    "is_private": is_private,
                    "group_id": group_id,
                    "primary_sender_id": expired_sess.get("primary_sender_id", ""),
                    "turns_log": list(turns_log)
                }
                asyncio.create_task(consolidate_session_memory(snapshot))
            return
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"⚠️ [会话看门狗异常] {chat_id_str}: {e}")


def rotate_session_if_due(chat_id_str: str) -> bool:
    """
    滚动会话 (Rolling Session)：若当前 conv_id 已累计满 4~5 轮，无感轮转为新的 conv_id 并回到“首轮”状态。
    对外 7min/10min 会话寿命、群压力与软上限计数完全不受影响；新 conv_id 的首轮依靠 L1 纯净滑窗衔接前情。
    返回是否发生了轮转。必须仅在两轮推理之间调用。
    """
    sess = ACTIVE_SESSIONS.get(chat_id_str)
    if not sess:
        return False
    default_limit = get_rolling_turn_limit(chat_id_str)
    if sess.get("turn_count", 0) < sess.get("roll_limit", default_limit):
        return False

    now = time.time()
    old_conv = sess["conv_id"]
    new_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"{chat_id_str}_roll_{int(now * 1000)}"))
    sess["conv_id"] = new_conv_id
    sess["turn_count"] = 0
    sess["last_inference_ts"] = 0.0
    sess["roll_limit"] = default_limit
    print(
        f"🔁 [滚动会话轮转] 通道 {chat_id_str} 同一 conv 已满轮数，"
        f"无感切换新 conv_id ({old_conv[:8]}... -> {new_conv_id[:8]}...)，前情由 L1 滑窗衔接。"
    )
    return True


def get_or_create_active_session(
    chat_id_str: str,
    is_private: bool = False,
    group_id: int = None,
    sender_id: str = ""
) -> tuple[dict, bool]:
    """
    获取或激活指定通道的 agy 长寿命会话。
    返回: (session_dict, is_continuation_turn)
    """
    now = time.time()
    if chat_id_str in ACTIVE_SESSIONS:
        rotate_session_if_due(chat_id_str)
        sess = ACTIVE_SESSIONS[chat_id_str]
        sess["last_active_at"] = now
        if sender_id and str(sender_id) not in ("系统", ""):
            sess["primary_sender_id"] = str(sender_id)
        base_ttl = get_base_session_ttl(sess.get("is_private", is_private))
        if sess.get("ttl_seconds") != base_ttl:
            print(f"🔄 [会话寿命恢复] 通道 {chat_id_str} 开启新一轮对话，暂恢复默认寿命 {int(base_ttl // 60)} 分钟。")
            sess["ttl_seconds"] = base_ttl
        _schedule_session_expiry(chat_id_str)
        is_continuation = sess.get("turn_count", 0) > 0
        return sess, is_continuation

    ttl = get_base_session_ttl(is_private)
    new_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"{chat_id_str}_sess_{int(now * 1000)}"))
    sess = {
        "conv_id": new_conv_id,
        "is_private": is_private,
        "group_id": group_id,
        "created_at": now,
        "last_active_at": now,
        "last_inference_ts": 0.0,
        "turn_count": 0,
        "total_turns": 0,
        "roll_limit": get_rolling_turn_limit(chat_id_str),
        "primary_sender_id": str(sender_id) if sender_id else "",
        "turns_log": [],
        "ttl_seconds": ttl,
        "expiry_task": None,
        "afterglow_task": None
    }
    ACTIVE_SESSIONS[chat_id_str] = sess

    if not is_private and group_id:
        from attention.pressure import set_group_session_state
        set_group_session_state(str(group_id), True)

    _schedule_session_expiry(chat_id_str)
    print(f"🌱 [会话激活] 通道 {chat_id_str} 创建全新 agy 会话 (conv_id={new_conv_id[:8]}..., 寿命={int(ttl // 60)}mins)")
    return sess, False


def record_session_turn_completed(
    chat_id_str: str,
    sender_id: str,
    user_messages: list,
    bot_reply: str,
    group_id: int = None,
    is_private: bool = False,
    turn_start_ts: float = None,
    voice_used: bool = False
):
    """
    当一轮 agy 思考完成后调用：
    - 更新会话轮次计数、记录本轮对话至 turns_log；
    - 重置 7min (群聊) / 10min (私聊) 无对话寿命计时器；
    - 若为群聊，刷新 45s 余温期（80% 底压 -> 45s 后回归 50% 维持至寿命结束）。
    - turn_start_ts: 本轮推理起点。last_inference_ts 以起点而非完成时刻记录，
      这样思考期间涌入的新群消息（已降维为背景）会在下一轮作为增量上文传入，而不会被漏掉。
    """
    now = time.time()
    sess = ACTIVE_SESSIONS.get(chat_id_str)
    if not sess:
        return

    sess["last_active_at"] = now
    sess["last_inference_ts"] = turn_start_ts if turn_start_ts else now
    sess["turn_count"] = sess.get("turn_count", 0) + 1
    sess["total_turns"] = sess.get("total_turns", 0) + 1
    if sender_id and str(sender_id) not in ("系统", ""):
        sess["primary_sender_id"] = str(sender_id)

    if bot_reply:
        sess["turns_log"].append({
            "sender_id": str(sender_id),
            "user_messages": list(user_messages or []),
            "bot_reply": bot_reply,
            "time": now
        })

        # 长会话累计满 12 轮执行一次阶段性情绪与记忆沉淀，其余未满整段的轮次在会话过期时统一结算（降频省额）
        chunk_threshold = 12
        if len(sess["turns_log"]) >= chunk_threshold:
            from workers.post_turn_observer import consolidate_session_memory
            chunk_snapshot = {
                "chat_id_str": chat_id_str,
                "is_private": is_private,
                "group_id": group_id,
                "primary_sender_id": sess.get("primary_sender_id", ""),
                "turns_log": list(sess["turns_log"])
            }
            sess["turns_log"].clear()
            asyncio.create_task(consolidate_session_memory(chunk_snapshot))

    # 根据本轮是否触发语音合成设置无对话寿命：触发语音该次延长至 20 分钟，否则恢复默认 7min/10min
    base_ttl = get_base_session_ttl(sess.get("is_private", is_private))
    prev_ttl = sess.get("ttl_seconds", base_ttl)
    target_ttl = VOICE_SESSION_TTL if voice_used else base_ttl
    sess["ttl_seconds"] = target_ttl
    if voice_used:
        print(
            f"🎙️ [语音保活延长] 通道 {chat_id_str} 本轮触发语音合成，"
            f"无对话存活寿命由 {int(base_ttl // 60)} 分钟延长至 {int(VOICE_SESSION_TTL // 60)} 分钟（仅限本次）。"
        )
    elif prev_ttl != base_ttl:
        print(f"🔄 [会话寿命恢复] 通道 {chat_id_str} 本轮未触发语音合成，无对话存活寿命恢复默认 {int(base_ttl // 60)} 分钟。")
    _schedule_session_expiry(chat_id_str)

    # 群聊发信完毕后：推进软上限轮次计数，并根据当前轮次设置余温底压（1-7轮:80%，8-14轮阶梯衰减，15+轮归零重攒）
    if not is_private and group_id:
        from attention.pressure import (
            increment_group_soft_cap_turn, activate_afterglow_pressure,
            get_group_soft_cap_turns, SOFT_CAP_ZERO_TURN
        )
        if bot_reply:
            increment_group_soft_cap_turn(str(group_id))
        activate_afterglow_pressure(str(group_id))
        if get_group_soft_cap_turns(str(group_id)) < SOFT_CAP_ZERO_TURN:
            _schedule_afterglow_drop(chat_id_str, int(group_id), delay_sec=45.5)
        else:
            old_ag = sess.get("afterglow_task")
            if old_ag and not old_ag.done():
                old_ag.cancel()


def log_safety_rejection(
    chat_id_str: str,
    dynamic_conv_id: str,
    prompt: str,
    full_out_str: str,
    tools_called: list,
    messages_list: list = None,
    caller_id: str = None,
    ignored: bool = False
):
    """
    Google 前置拒收诊断日志：记录拒收发生时的通道类型、会话轮次、Prompt 特征（是否含私聊前情/转发图编号及其状态）、
    本轮是否已发信、拒收原文片段与最新用户消息，写入控制台与 logs/safety_reject.log (JSONL)，
    用于事后判断是否存在“跨对话污染”（历史前情/长会话 Transcript 携带敏感语料）。
    本函数绝不抛异常，不影响主流程。
    """
    try:
        import json
        import os
        now = time.time()
        sess = ACTIVE_SESSIONS.get(chat_id_str) or {}
        prompt = prompt or ""

        # Prompt 特征
        img_refs = list(dict.fromkeys(re.findall(r'【图片引用编号】：(img_\d+)', prompt)))
        img_status = {}
        try:
            from social.image_vault import get_image_status
            for i in img_refs:
                img_status[i] = get_image_status(i) or "unknown"
        except Exception:
            pass

        # 拒收原文片段
        snippet = ""
        for kw in ("The prompt could not be submitted", "Prohibited Use policy", "sensitive words that violate Google"):
            idx = (full_out_str or "").find(kw)
            if idx != -1:
                snippet = full_out_str[max(0, idx - 80): idx + 240].replace("\n", " ")
                break

        record = {
            "time": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "chat_id": chat_id_str,
            "chat_type": "private" if chat_id_str.startswith("private_") else ("group" if chat_id_str.startswith("group_") else "other"),
            "caller_id": str(caller_id) if caller_id is not None else "",
            "conv_id": (dynamic_conv_id or "")[:8],
            "session_turn_in_conv": sess.get("turn_count"),
            "session_total_turns": sess.get("total_turns"),
            "session_age_sec": int(now - sess["created_at"]) if sess.get("created_at") else None,
            "is_continuation_prompt": prompt.lstrip().startswith("<context_update"),
            "prompt_chars": len(prompt),
            "has_private_history": "【私聊近期前情记录" in prompt,
            "has_group_history": "【群聊近期背景" in prompt or "【期间群内新增动态】" in prompt,
            "forwarded_img_refs_in_prompt": img_status,
            "tools_called": list(tools_called or []),
            "already_spoke": ignored,
            "action": "ignored_tail_rejection" if ignored else "raise_and_retry",
            "latest_user_msgs": [str(m)[:80] for m in (messages_list or [])[-3:]],
            "rejection_snippet": snippet,
        }
        print(
            f"🔬 [拒收诊断] {record['chat_type']} {chat_id_str} | conv={record['conv_id']} "
            f"轮次={record['session_turn_in_conv']}/{record['session_total_turns']} 续轮={record['is_continuation_prompt']} "
            f"Prompt={record['prompt_chars']}字 私聊前情={record['has_private_history']} 群前情={record['has_group_history']} "
            f"转发图引用={img_status or '无'} 已发信={ignored} -> {record['action']}"
        )
        try:
            from core.log_manager import LOGS_DIR
            with open(os.path.join(LOGS_DIR, "safety_reject.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            # 完整 Prompt 落盘留样，供高精度定位敏感情境
            dump_dir = os.path.join(LOGS_DIR, "rejected_prompts")
            os.makedirs(dump_dir, exist_ok=True)
            ts_str = datetime.datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
            conv_tag = (dynamic_conv_id or "unknown")[:8]
            dump_path = os.path.join(dump_dir, f"{ts_str}_{chat_id_str}_{conv_tag}.txt")
            with open(dump_path, "w", encoding="utf-8") as pf:
                pf.write(f"=== REJECTION INFO ===\nTime: {record['time']}\nChat: {chat_id_str}\nConv: {dynamic_conv_id}\nSnippet: {snippet}\n\n=== FULL PROMPT ===\n")
                pf.write(prompt)
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ [拒收诊断] 日志记录失败（不影响主流程）: {e}")


def reset_session_on_safety_retry(chat_id_str: str) -> str:
    """当遭遇安全拒收需自愈重推时，重置当前会话的 conv_id 并设为首轮状态"""
    now = time.time()
    new_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"{chat_id_str}_retry_{int(now * 1000)}"))
    if chat_id_str in ACTIVE_SESSIONS:
        sess = ACTIVE_SESSIONS[chat_id_str]
        sess["conv_id"] = new_conv_id
        sess["turn_count"] = 0
        sess["last_active_at"] = now
        sess["ttl_seconds"] = get_base_session_ttl(sess.get("is_private", False))
        _schedule_session_expiry(chat_id_str)
    return new_conv_id


def format_runtime_status(waiting_queues: list = None) -> str:
    """实时生成当前系统并发、防抖队列与存活会话看板字符串"""
    now = time.time()
    running_chats = list(ACTIVE_AI_TASKS.keys())
    running_info = []
    for cid, info in ACTIVE_AI_TASKS.items():
        dur = now - info.get("start_time", now)
        running_info.append(f"{cid}({dur:.1f}s)")
    running_str = f"{len(running_chats)}个 [{', '.join(running_info)}]" if running_chats else "0 (空闲)"

    waiting_chats = waiting_queues if waiting_queues is not None else []
    waiting_str = f"{len(waiting_chats)}个 [{', '.join(waiting_chats)}]" if waiting_chats else "0"

    sess_info = []
    for cid, s in ACTIVE_SESSIONS.items():
        rem = max(0, int(s["ttl_seconds"] - (now - s["last_active_at"])))
        sess_info.append(f"{cid}(T{s['turn_count']},{rem}s)")
    sess_str = f"{len(sess_info)}个 [{', '.join(sess_info)}]" if sess_info else "0"

    return f"【并发看板】正在推理: {running_str} | 排队防抖: {waiting_str} | 活跃会话: {sess_str}"


class AgyEffortUnsupportedError(Exception):
    """agy 自身校验报错：当前 model 不支持 --effort (invalid model selection)，去掉 --effort 重跑即可"""
    pass


EFFORT_UNSUPPORTED_MARKERS = ("invalid model selection", "--effort is not supported")


async def execute_agy_inference(
    chat_id_str: str,
    prompt: str,
    dynamic_conv_id: str,
    messages_list: list = None,
    custom_semaphore: asyncio.Semaphore = None,
    target_domains: list = None,
    effort: str = None,
    caller_id: str = None,
    pre_spawn_refresh=None
) -> list:
    """
    对外入口：正常携带 --effort 执行；若 agy 偶发校验报错 "--effort is not supported for model"
    （agy 侧模型能力校验抖动，此时整轮会静默无回复），自动去掉 --effort 重跑一次，避免丢消息。
    """
    try:
        return await _execute_agy_inference_once(
            chat_id_str, prompt, dynamic_conv_id, messages_list=messages_list,
            custom_semaphore=custom_semaphore, target_domains=target_domains,
            effort=effort, caller_id=caller_id, drop_effort=False,
            pre_spawn_refresh=pre_spawn_refresh
        )
    except AgyEffortUnsupportedError:
        print(f"🔁 [effort 兜底重跑] 通道 {chat_id_str} agy 报 --effort 不被支持，去掉 --effort 重跑一次...")
        return await _execute_agy_inference_once(
            chat_id_str, prompt, dynamic_conv_id, messages_list=messages_list,
            custom_semaphore=custom_semaphore, target_domains=target_domains,
            effort=effort, caller_id=caller_id, drop_effort=True,
            pre_spawn_refresh=pre_spawn_refresh
        )


def _is_voice_turn_active(task_state: dict = None, voice_marker_path: str = "") -> bool:
    """检查当前推理轮次是否已触发语音合成调用（支持 transcript 与本地标记文件双路探测）。"""
    if task_state and task_state.get("voice_triggered"):
        return True
    if voice_marker_path and os.path.exists(voice_marker_path):
        if task_state is not None:
            task_state["voice_triggered"] = True
        return True
    return False


async def _wait_process_with_voice_timeout(
    process: asyncio.subprocess.Process,
    start_time: float,
    chat_id_str: str,
    task_state: dict,
    voice_marker_path: str,
    default_timeout: float = DEFAULT_INFERENCE_TIMEOUT,
    voice_timeout: float = VOICE_INFERENCE_TIMEOUT,
    poll_interval: float = 2.0
) -> float:
    """
    等待子进程完成，并按本轮是否触发语音合成动态切换单轮超时上限：
    - 默认 default_timeout (10 分钟 / 600s)；
    - 检测到本轮触发 send_voice_msg 后，仅本轮放宽至 voice_timeout (20 分钟 / 1200s)。
    """
    wait_task = asyncio.create_task(process.wait())
    announced_extension = False
    try:
        while True:
            has_voice = _is_voice_turn_active(task_state, voice_marker_path)
            active_timeout = voice_timeout if has_voice else default_timeout
            if has_voice and not announced_extension:
                announced_extension = True
                print(
                    f"🎙️ [语音超时延长] 通道 {chat_id_str} 本轮触发语音合成，"
                    f"单轮进程超时斩首上限由 {int(default_timeout // 60)} 分钟延长至 {int(voice_timeout // 60)} 分钟（{int(voice_timeout)}s）。"
                )

            remaining = active_timeout - (time.time() - start_time)
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Inference timeout reached ({active_timeout}s)")

            step = min(remaining, poll_interval)
            done, _ = await asyncio.wait({wait_task}, timeout=step)
            if wait_task in done:
                await wait_task
                return active_timeout
    finally:
        if not wait_task.done():
            wait_task.cancel()
            try:
                await wait_task
            except (asyncio.CancelledError, Exception):
                pass


async def _execute_agy_inference_once(
    chat_id_str: str,
    prompt: str,
    dynamic_conv_id: str,
    messages_list: list = None,
    custom_semaphore: asyncio.Semaphore = None,
    target_domains: list = None,
    effort: str = None,
    caller_id: str = None,
    drop_effort: bool = False,
    pre_spawn_refresh=None
) -> list:
    """
    通过信号量调度并拉起 agy 子进程进行大模型思考。
    支持传入 caller_id 注入环境变量 AGY_CALLER_ID 实现底层工具硬权限锁。
    挂载 transcript 增量日志监听与动态超时保护（默认 600 秒，触发语音合成本轮放宽至 1200 秒）。
    返回本次思考中调用的工具名列表。
    """
    start_time = time.time()
    allow_low = True
    model_flags, resolved_model, resolved_effort = build_agy_model_flags(
        effort=effort,
        drop_effort=drop_effort,
        allow_low=allow_low
    )
    effective_effort = None if drop_effort else resolved_effort
    domain_tag = f"domains={','.join(target_domains)}" if target_domains else "domains=all"
    model_tag = f"model={resolved_model}, effort={effective_effort}, {domain_tag}"
    print(f"🧠 [启动大模型] 通道: {chat_id_str} ({model_tag}) | {format_runtime_status()}")

    cmd = [
        AGY_EXECUTABLE,
        f"--conversation={dynamic_conv_id}"
    ]
    cmd.extend(model_flags)
    cmd.extend([
        "--dangerously-skip-permissions",
        "--prompt", prompt
    ])

    target_semaphore = custom_semaphore or GLOBAL_AGY_SEMAPHORE
    sem_name = "B站专属独立锁" if custom_semaphore is BILI_AGY_SEMAPHORE else "全局大模型锁"

    if target_semaphore.locked():
        print(f"⏳ [排队等待] 通道 {chat_id_str} 正在等待{sem_name}释放... | {format_runtime_status()}")

    tools_called = []
    async with target_semaphore:
        print(f"⚡ [获得{sem_name}] 通道 {chat_id_str} 开始执行大模型推理... | {format_runtime_status()}")
        if pre_spawn_refresh is not None:
            refreshed = await pre_spawn_refresh()
            if refreshed:
                prompt = refreshed.get("prompt", prompt)
                messages_list = refreshed.get("messages_list", messages_list)
                target_domains = refreshed.get("target_domains", target_domains)
                effort = refreshed.get("effort", effort)
                model_flags, resolved_model, resolved_effort = build_agy_model_flags(
                    effort=effort,
                    drop_effort=drop_effort,
                    allow_low=allow_low
                )
                effective_effort = None if drop_effort else resolved_effort
                domain_tag = f"domains={','.join(target_domains)}" if target_domains else "domains=all"
                model_tag = f"model={resolved_model}, effort={effective_effort}, {domain_tag}"
                print(f"🔄 [刷新推理负载] 通道: {chat_id_str} ({model_tag}) | {format_runtime_status()}")
                cmd = [
                    AGY_EXECUTABLE,
                    f"--conversation={dynamic_conv_id}"
                ]
                cmd.extend(model_flags)
                cmd.extend([
                    "--dangerously-skip-permissions",
                    "--prompt", prompt
                ])
        voice_marker_path = os.path.join(tempfile.gettempdir(), f"qqagent_voice_{uuid.uuid4().hex}.flag")
        active_timeout_sec = int(DEFAULT_INFERENCE_TIMEOUT)
        try:
            sub_env = get_agy_env()
            sub_env["AGY_VOICE_MARKER_FILE"] = voice_marker_path
            if target_domains:
                sub_env["AGY_TOOL_DOMAINS"] = ",".join(target_domains)
            if caller_id is not None:
                sub_env["AGY_CALLER_ID"] = str(caller_id)

            # 1A 防串台物理通道硬锁：将当前会话所属通道类型与目标ID写入子进程环境变量
            if chat_id_str == "desktop_master":
                sub_env["QQ_LOCKED_TARGET_TYPE"] = "deskpet"
                sub_env["QQ_LOCKED_TARGET_ID"] = str(MASTER_QQ)
            elif chat_id_str.startswith("private_"):
                sub_env["QQ_LOCKED_TARGET_TYPE"] = "private"
                sub_env["QQ_LOCKED_TARGET_ID"] = chat_id_str.replace("private_", "").strip()
            elif chat_id_str.startswith("group_"):
                sub_env["QQ_LOCKED_TARGET_TYPE"] = "group"
                sub_env["QQ_LOCKED_TARGET_ID"] = chat_id_str.replace("group_", "").strip()

            captured_output = []

            async def _stream_pipe(stream, out_pipe):
                try:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        text = line.decode("utf-8", errors="replace")
                        captured_output.append(text)
                        if chat_id_str == "desktop_master":
                            deskpet_log(text)
                        else:
                            out_pipe.write(text)
                            out_pipe.flush()
                except Exception:
                    pass

            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=BASE_DIR,
                env=sub_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True
            )
            pipe_stdout_task = asyncio.create_task(_stream_pipe(process.stdout, sys.stdout))
            pipe_stderr_task = asyncio.create_task(_stream_pipe(process.stderr, sys.stderr))

            task_state = {
                "process": process,
                "messages": messages_list or [prompt],
                "start_time": start_time,
                "spoke": False,            # 本轮是否已经通过发信工具开口（由 transcript 监听器实时置位）
                "voice_triggered": False   # 本轮是否触发过语音合成调用（触发时本轮超时与保活延长至 20 分钟）
            }
            ACTIVE_AI_TASKS[chat_id_str] = task_state

            watcher_task = asyncio.create_task(
                watch_process_transcript(
                    dynamic_conv_id=dynamic_conv_id,
                    chat_id_str=chat_id_str,
                    process=process,
                    messages_list=messages_list,
                    start_time=start_time,
                    target_domains=target_domains,
                    task_state=task_state
                )
            )

            try:
                try:
                    await _wait_process_with_voice_timeout(
                        process=process,
                        start_time=start_time,
                        chat_id_str=chat_id_str,
                        task_state=task_state,
                        voice_marker_path=voice_marker_path
                    )
                except asyncio.TimeoutError:
                    active_timeout_sec = int(
                        VOICE_INFERENCE_TIMEOUT
                        if _is_voice_turn_active(task_state, voice_marker_path)
                        else DEFAULT_INFERENCE_TIMEOUT
                    )
                    print(f"⚠️ [思考超时] 通道 {chat_id_str} 超过 {active_timeout_sec}.0s 被强制终止！即刻斩首进程组...")
                    try:
                        pgid = os.getpgid(process.pid)
                        os.killpg(pgid, signal.SIGKILL)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=3.0)
                    except Exception:
                        pass
                    raise
            finally:
                if watcher_task:
                    if watcher_task.done():
                        try:
                            tools_called = watcher_task.result() or []
                        except Exception:
                            tools_called = []
                    else:
                        try:
                            tools_called = await asyncio.wait_for(watcher_task, timeout=2.0) or []
                        except Exception:
                            watcher_task.cancel()
                            tools_called = []
                if _is_voice_turn_active(task_state, voice_marker_path) and "send_voice_msg" not in tools_called:
                    tools_called.append("send_voice_msg")
                pipe_stdout_task.cancel()
                pipe_stderr_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(pipe_stdout_task, pipe_stderr_task, return_exceptions=True),
                        timeout=1.0
                    )
                except Exception:
                    pass

            elapsed = time.time() - start_time
            full_out_str = "".join(captured_output)

            # 优先检查是否被新消息主动打断 (terminate -> -15 或 aborted 标志)
            # 若已被打断，新消息防抖队列已接管，严禁再触发安全拒收自愈重试避免双并发撞车
            if process.returncode == -15 or task_state.get("aborted"):
                print(f"⚠️ [思考被打断] 通道 {chat_id_str} (耗时 {elapsed:.1f}s) | 等待合并新消息重新推理...")
                return tools_called

            # agy 自身的模型/effort 校验失败：进程未真正推理、没有任何发信，去掉 --effort 重跑
            if (
                effective_effort
                and not tools_called
                and any(mk in full_out_str for mk in EFFORT_UNSUPPORTED_MARKERS)
            ):
                print(f"⚠️ [effort 校验失败] 通道 {chat_id_str}: {full_out_str.strip()[:200]}")
                raise AgyEffortUnsupportedError(full_out_str.strip()[:200])

            if (
                "The prompt could not be submitted" in full_out_str
                or "Prohibited Use policy" in full_out_str
                or "sensitive words that violate Google" in full_out_str
            ):
                spoke_already = any(t in SEND_TOOL_NAMES for t in (tools_called or []))
                if not spoke_already and start_time:
                    try:
                        from workers.post_turn_observer import check_bot_spoke_in_turn
                        state_key = chat_id_str.replace("group_", "") if chat_id_str.startswith("group_") else chat_id_str
                        disk_spoke, disk_reply = check_bot_spoke_in_turn(state_key, start_time)
                        if disk_spoke:
                            spoke_already = True
                            if (disk_reply or "").startswith("[语音(") or _is_voice_turn_active(task_state, voice_marker_path):
                                fallback_t = "send_voice_msg"
                            else:
                                fallback_t = "send_private_msg" if chat_id_str.startswith("private_") else "send_group_msg"
                            if fallback_t not in tools_called:
                                tools_called.append(fallback_t)
                    except Exception:
                        pass

                log_safety_rejection(
                    chat_id_str=chat_id_str,
                    dynamic_conv_id=dynamic_conv_id,
                    prompt=prompt,
                    full_out_str=full_out_str,
                    tools_called=tools_called,
                    messages_list=messages_list,
                    caller_id=caller_id,
                    ignored=spoke_already
                )
                if spoke_already:
                    # 本轮已经通过发信工具实质性回复过用户（拒收发生在收尾阶段的第二次 API 往返），
                    # 此时严禁抛出拒收异常触发重推与“安全兜底”追发，直接视为本轮成功。
                    print(f"⚠️ [尾部拒收忽略] 通道 {chat_id_str} 本轮已成功发信 ({', '.join(tools_called)})，收尾阶段的 Google 拒收提示被忽略，不再重推/不追发兜底。")
                else:
                    print(f"🛑 [安全门禁预警] 通道 {chat_id_str} 捕获到 Google API 前置违规拒收事件！")
                    raise PromptSafetyRejectedError("The prompt could not be submitted. (Prohibited Use policy)")

            tools_summary = ", ".join(tools_called) if tools_called else "无 (保持沉默/直接返回)"
            print(f"✅ [思考完毕] 通道 {chat_id_str} (总耗时 {elapsed:.1f}s | 调用的工具: {tools_summary}) | {format_runtime_status()}")

        except asyncio.TimeoutError:
            print(f"⚠️ [思考超时释放] 通道 {chat_id_str} {active_timeout_sec}秒超时处理完毕，释放大模型锁。")
        except (AgyEffortUnsupportedError, PromptSafetyRejectedError):
            raise
        except Exception as e:
            print(f"❌ [推理执行异常] 通道 {chat_id_str}: {type(e).__name__}: {e}", file=sys.stderr)
            raise
        finally:
            ACTIVE_AI_TASKS.pop(chat_id_str, None)
            try:
                if voice_marker_path and os.path.exists(voice_marker_path):
                    os.remove(voice_marker_path)
            except Exception:
                pass

    return tools_called
