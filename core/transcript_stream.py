"""
core/transcript_stream.py — transcript.jsonl 增量日志流式监听与工具回捕

职责：
1. 实时捕获大模型调用的工具（如 send_group_msg, send_private_msg, send_meme 等）；
2. 秒级更新本地机器人发言状态 (record_bot_spoke)，维护连击与会话防抖状态。
"""
import os
import json
import asyncio
from config import get_transcript_path
from core.state_manager import record_bot_spoke


async def watch_process_transcript(
    dynamic_conv_id: str,
    chat_id_str: str,
    process: asyncio.subprocess.Process,
    messages_list: list = None,
    start_time: float = None,
    target_domains: list = None,
    task_state: dict = None
) -> list:
    """实时监控 agy 的 transcript.jsonl，实时捕获大模型调用的工具并打印日志，更新机器人发言状态

    task_state: 可选，ACTIVE_AI_TASKS 中本通道的状态字典；一旦检测到发信类工具调用即置 task_state["spoke"]=True，
    供防抖队列判断“本轮是否已开口”（已开口则新消息降维为背景，不再打断/抢答）。
    """
    log_path = get_transcript_path(dynamic_conv_id)

    last_pos = 0
    if os.path.exists(log_path):
        try:
            last_pos = os.path.getsize(log_path)
        except Exception:
            pass

    tools_called = []
    is_deskpet_turn = (chat_id_str == "desktop_master")
    if is_deskpet_turn:
        try:
            from tools.deskpet_tools import push_deskpet_status
            asyncio.create_task(push_deskpet_status("think", "在想"))
        except Exception:
            pass

    def _process_tool_call(tc):
        tname = tc.get("name") or tc.get("toolName", "unknown")
        args = tc.get("args") or tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        if tname not in tools_called:
            tools_called.append(tname)

        if task_state is not None and tname in ("send_group_msg", "send_private_msg", "send_meme", "send_local_image", "send_image_to_group", "send_image_to_private", "send_voice_msg", "pet_say", "pet_ask"):
            task_state["spoke"] = True
            if tname in ("send_voice_msg", "pet_say"):
                task_state["voice_triggered"] = True

        print(f"   ⚙️ [{chat_id_str}] 大模型正在调用工具: >> {tname}")
        if is_deskpet_turn or tname.startswith("pet_"):
            try:
                from tools.deskpet_tools import push_deskpet_status, map_tool_to_deskpet_status
                skind, stext, sdetail = map_tool_to_deskpet_status(tname, args if isinstance(args, dict) else {})
                if skind:
                    asyncio.create_task(push_deskpet_status(skind, stext, sdetail))
            except Exception:
                pass

        if tname in ("send_group_msg", "send_private_msg", "send_local_image", "send_image_to_group", "send_image_to_private", "send_meme", "send_voice_msg", "pet_say", "pet_ask", "qzone_action", "publish_qzone_feed", "like_friend_qzone_feed", "comment_friend_qzone_feed"):
            target_gid = str(args.get("group_id", ""))
            if not target_gid and chat_id_str.startswith("group_"):
                target_gid = chat_id_str.replace("group_", "")
            elif chat_id_str == "desktop_master":
                target_gid = "desktop_master"
            msg_text = str(args.get("message") or args.get("text") or args.get("script") or args.get("question") or "(弥酱发言)")
            if target_gid:
                record_bot_spoke(target_gid, msg_text)

    while process.returncode is None:
        await asyncio.sleep(0.4)
        if not os.path.exists(log_path):
            log_path = get_transcript_path(dynamic_conv_id)
        if os.path.exists(log_path):
            try:
                curr_size = os.path.getsize(log_path)
                if curr_size > last_pos:
                    with open(log_path, "r", encoding="utf-8") as f:
                        f.seek(last_pos)
                        new_content = f.read()
                        last_pos = f.tell()

                    for line in new_content.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                            for tc in item.get("tool_calls", []):
                                _process_tool_call(tc)
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            # 若 transcript.jsonl 尚未就绪，通过持久化状态检测大模型是否已先发信，实时解锁防抖抢答
            if task_state is not None and not task_state.get("spoke", False) and start_time:
                try:
                    from workers.post_turn_observer import check_bot_spoke_in_turn
                    state_key = chat_id_str.replace("group_", "") if chat_id_str.startswith("group_") else chat_id_str
                    spoke_disk, _ = check_bot_spoke_in_turn(state_key, start_time)
                    if spoke_disk:
                        task_state["spoke"] = True
                except Exception:
                    pass

    # 进程结束退出时做最后一次读取
    if os.path.exists(log_path):
        try:
            curr_size = os.path.getsize(log_path)
            if curr_size > last_pos:
                with open(log_path, "r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    new_content = f.read()
                for line in new_content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        for tc in item.get("tool_calls", []):
                            _process_tool_call(tc)
                    except Exception:
                        pass
        except Exception:
            pass

    # 终极保底：若 transcript 读取遗漏但实际在 chat_state 中已经成功发信，回填工具名，杜绝误报沉默/误触发兜底
    if not tools_called and start_time:
        try:
            from workers.post_turn_observer import check_bot_spoke_in_turn
            state_key = chat_id_str.replace("group_", "") if chat_id_str.startswith("group_") else chat_id_str
            spoke_disk, reply_text = check_bot_spoke_in_turn(state_key, start_time)
            if spoke_disk:
                fallback_tool = "pet_say" if chat_id_str == "desktop_master" else ("send_private_msg" if chat_id_str.startswith("private_") else "send_group_msg")
                tools_called.append(fallback_tool)
                if task_state is not None:
                    task_state["spoke"] = True
                print(f"   ⚙️ [{chat_id_str}] (通过持久化状态回捕发信工具: >> {fallback_tool})")
        except Exception:
            pass

    if is_deskpet_turn:
        try:
            from tools.deskpet_tools import push_deskpet_status
            asyncio.create_task(push_deskpet_status(None))
        except Exception:
            pass

    return tools_called
