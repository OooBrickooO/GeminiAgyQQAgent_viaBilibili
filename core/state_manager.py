"""
core/state_manager.py — 群聊活跃状态、发言时间戳与连续计数持久化

管理 LAST_HUMAN_MSG_TIME, LAST_BOT_MSG_TIME, CONSECUTIVE_BOT_MSG_COUNT,
AWAKE_STATES, RECENT_GROUP_MSGS 等全局内存状态，并支持原子落盘到 chat_state.json。
"""
import json
import time
from collections import defaultdict, deque
from config import CHAT_STATE_FILE, BOT_QQ, safe_log

# --- 全局状态追踪记录 ---
# 记录每个通道是否发送过长身份设定（用于精简上下文提速）
INITIALIZED_CONVS = set()
# 记录每个群组最后一次收到消息的时间 (秒)
LAST_MSG_TIME = {}
# 记录每个群人类群友最后发言的时间戳
LAST_HUMAN_MSG_TIME = defaultdict(float)
# 记录每个群机器人最后发言的时间戳
LAST_BOT_MSG_TIME = defaultdict(float)
# 记录机器人连续发言次数（0间隙，中间无人类发言）
CONSECUTIVE_BOT_MSG_COUNT = defaultdict(int)
# 记录每个群组进入"唤醒/兴奋"状态的时间 (秒)
AWAKE_STATES = {}
# 记录每个群组在单次唤醒期间自发搭腔的轮次计数（严格上限1轮，杜绝无限对话死循环）
AWAKE_REPLY_COUNT = defaultdict(int)
# 群组最近消息滑动窗口 (拉长至30条，实现注意力分层与工作流记忆)
RECENT_GROUP_MSGS = defaultdict(lambda: deque(maxlen=30))
# 群成员数缓存
GROUP_MEMBER_COUNTS = {}


def reset_awake_counter(group_id_str: str):
    """当发生显式主动唤醒（@、叫名字、压力爆发、主动呼吸）时，重置唤醒接话计数器"""
    AWAKE_REPLY_COUNT[str(group_id_str)] = 0


def increment_awake_counter(group_id_str: str) -> int:
    """单次唤醒期搭腔计数自增并返回最新值"""
    gid = str(group_id_str)
    AWAKE_REPLY_COUNT[gid] += 1
    return AWAKE_REPLY_COUNT[gid]


def get_awake_counter(group_id_str: str) -> int:
    """获取当前唤醒接话轮次"""
    return AWAKE_REPLY_COUNT[str(group_id_str)]


def load_chat_state():
    """从磁盘加载各群聊天时间戳与连续计数，避免服务重启后失忆自言自语"""
    global LAST_HUMAN_MSG_TIME, LAST_BOT_MSG_TIME, CONSECUTIVE_BOT_MSG_COUNT
    try:
        import os
        if os.path.exists(CHAT_STATE_FILE):
            with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for gid, info in data.items():
                        if str(gid).startswith("_") or not isinstance(info, dict):
                            continue
                        LAST_HUMAN_MSG_TIME[gid] = float(info.get("last_human_t", 0.0))
                        LAST_BOT_MSG_TIME[gid] = float(info.get("last_bot_t", 0.0))
                        CONSECUTIVE_BOT_MSG_COUNT[gid] = int(info.get("consecutive_bot_count", 0))
                safe_log("💾 [状态持久化] 成功从磁盘载入群聊发言状态与时间戳！")
    except Exception as e:
        safe_log(f"载入群聊状态失败: {e}")


# ============================================================
# 局部通道级 low 强度开关持久化（跨进程 mtime 热感知，默认全关）
# ============================================================
_LOW_EFFORT_CACHE: dict = {}
_LOW_EFFORT_MTIME: float = -1.0


def normalize_low_channel_id(raw_cid: str) -> str:
    """规范化通道 ID：支持 private_xxx / group_xxx"""
    cid = str(raw_cid or "").strip()
    if not cid:
        return ""
    if cid.startswith(("private_", "group_")):
        return cid
    if cid.isdigit():
        return f"group_{cid}"
    return cid


def get_low_effort_channels(force_reload: bool = False) -> dict:
    """
    从 CHAT_STATE_FILE 热读取当前已局部开启 low 档的通道字典。
    返回格式: {chat_id_str: {"enabled": True, "updated_at": float, "label": str}}
    """
    global _LOW_EFFORT_CACHE, _LOW_EFFORT_MTIME
    import os
    if not os.path.exists(CHAT_STATE_FILE):
        _LOW_EFFORT_CACHE = {}
        _LOW_EFFORT_MTIME = -1.0
        return {}
    try:
        mtime = os.path.getmtime(CHAT_STATE_FILE)
        if not force_reload and mtime == _LOW_EFFORT_MTIME:
            return dict(_LOW_EFFORT_CACHE)
        with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_map = data.get("_low_effort_channels", {}) if isinstance(data, dict) else {}
        cleaned = {}
        if isinstance(raw_map, dict):
            for k, v in raw_map.items():
                cid = normalize_low_channel_id(k)
                if not cid:
                    continue
                if isinstance(v, dict) and v.get("enabled", True):
                    cleaned[cid] = {
                        "enabled": True,
                        "updated_at": float(v.get("updated_at", 0.0)),
                        "label": str(v.get("label", "")),
                    }
                elif v is True:
                    cleaned[cid] = {"enabled": True, "updated_at": 0.0, "label": ""}
        elif isinstance(raw_map, list):
            for item in raw_map:
                cid = normalize_low_channel_id(str(item))
                if cid:
                    cleaned[cid] = {"enabled": True, "updated_at": 0.0, "label": ""}
        _LOW_EFFORT_CACHE = cleaned
        _LOW_EFFORT_MTIME = mtime
        return dict(cleaned)
    except Exception:
        return dict(_LOW_EFFORT_CACHE)


def is_channel_low_enabled(chat_id_str: str) -> bool:
    """检查指定对话通道是否已被主人手动开启局部强制 low 档（默认 False）"""
    cid = normalize_low_channel_id(chat_id_str)
    if not cid:
        return False
    channels = get_low_effort_channels()
    return bool(channels.get(cid, {}).get("enabled", False))


def set_channel_low_enabled(chat_id_str: str, enabled: bool, label: str = "") -> dict:
    """原子更新指定对话通道的局部 low 开关并落盘至 CHAT_STATE_FILE"""
    global _LOW_EFFORT_CACHE, _LOW_EFFORT_MTIME
    import os
    cid = normalize_low_channel_id(chat_id_str)
    if not cid:
        return get_low_effort_channels(force_reload=True)

    data = {}
    if os.path.exists(CHAT_STATE_FILE):
        try:
            with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    low_map = data.get("_low_effort_channels", {})
    if not isinstance(low_map, dict):
        low_map = {}

    if enabled:
        low_map[cid] = {
            "enabled": True,
            "updated_at": time.time(),
            "label": str(label or low_map.get(cid, {}).get("label", "")),
        }
    else:
        low_map.pop(cid, None)

    data["_low_effort_channels"] = low_map
    temp_file = f"{CHAT_STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, CHAT_STATE_FILE)

    _LOW_EFFORT_CACHE = {
        normalize_low_channel_id(k): v
        for k, v in low_map.items()
        if isinstance(v, dict) and v.get("enabled", True)
    }
    try:
        _LOW_EFFORT_MTIME = os.path.getmtime(CHAT_STATE_FILE)
    except OSError:
        _LOW_EFFORT_MTIME = -1.0
    return dict(_LOW_EFFORT_CACHE)


def clear_all_low_effort_channels() -> dict:
    """一键清空所有局部开启的 low 通道，恢复全局默认 medium/high"""
    global _LOW_EFFORT_CACHE, _LOW_EFFORT_MTIME
    import os
    data = {}
    if os.path.exists(CHAT_STATE_FILE):
        try:
            with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data["_low_effort_channels"] = {}
    temp_file = f"{CHAT_STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, CHAT_STATE_FILE)
    _LOW_EFFORT_CACHE = {}
    try:
        _LOW_EFFORT_MTIME = os.path.getmtime(CHAT_STATE_FILE)
    except OSError:
        _LOW_EFFORT_MTIME = -1.0
    return {}


def save_chat_state():
    """将各群发言时间戳即时落盘，严格保留已有扩展字段（如 recent_bot_msgs）并采用原子替换"""
    try:
        import os
        data = {}
        if os.path.exists(CHAT_STATE_FILE):
            try:
                with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        all_gids = set(LAST_HUMAN_MSG_TIME.keys()) | set(LAST_BOT_MSG_TIME.keys())
        for gid in all_gids:
            item = data.get(gid, {})
            item["last_human_t"] = LAST_HUMAN_MSG_TIME[gid]
            item["last_bot_t"] = LAST_BOT_MSG_TIME[gid]
            item["consecutive_bot_count"] = CONSECUTIVE_BOT_MSG_COUNT[gid]
            data[gid] = item

        temp_file = f"{CHAT_STATE_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, CHAT_STATE_FILE)
    except Exception:
        pass


def record_bot_spoke_persistent(group_id_str: str, text: str = "(弥酱发言)"):
    """
    跨进程发信闭环落地 (方案 C 核心)：
    无论在守护进程内部还是独立 MCP 工具进程中调用，均即时安全合并落盘到 CHAT_STATE_FILE。
    - 采用 Read-Modify-Write 机制，绝不冲掉其他进程记录的 last_human_t
    - 采用原子文件替换 (os.replace)，杜绝并发读写产生半截文件损坏
    - 同步更新当前进程内存中的 LAST_BOT_MSG_TIME 与滑动窗口
    """
    import os
    gid = str(group_id_str)
    now_t = time.time()

    try:
        disk_data = {}
        if os.path.exists(CHAT_STATE_FILE):
            try:
                with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
            except Exception:
                disk_data = {}

        info = disk_data.get(gid, {})
        last_bot = float(info.get("last_bot_t", 0.0))
        consecutive = int(info.get("consecutive_bot_count", 0))

        # 5 秒内算同一轮发言
        if (now_t - last_bot) >= 5.0:
            consecutive += 1

        info["last_bot_t"] = now_t
        info["consecutive_bot_count"] = consecutive
        if "last_human_t" not in info:
            info["last_human_t"] = LAST_HUMAN_MSG_TIME.get(gid, 0.0)

        # 跨进程记忆闭环：将机器人真实发言持久化留存，杜绝主进程滑窗漏网
        clean_text = text.strip()
        if clean_text and clean_text != "(弥酱发言)":
            recent_bot = info.get("recent_bot_msgs", [])
            # 避免5秒内重复记入同一条
            if not recent_bot or (clean_text != recent_bot[-1].get("text") and now_t - recent_bot[-1].get("time", 0.0) >= 2.0):
                recent_bot.append({"time": now_t, "text": clean_text})
            info["recent_bot_msgs"] = recent_bot[-12:]

        disk_data[gid] = info

        # 原子写入：写临时文件后重命名替换
        temp_file = f"{CHAT_STATE_FILE}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(disk_data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, CHAT_STATE_FILE)
    except Exception as e:
        safe_log(f"跨进程写入发言状态异常: {e}")

    # 同时同步当前进程内存
    record_bot_spoke(gid, text)


def sync_bot_msgs_from_disk(group_id_str: str):
    """
    【跨进程记忆无感对齐】
    当 MCP Server 在独立子进程发信后，主进程构建 L1 工作流前主动从 CHAT_STATE_FILE
    对齐机器人最近发信文本，彻底消除网络上报延迟产生的记忆断层与自言自语。
    """
    import os
    gid = str(group_id_str)
    if not os.path.exists(CHAT_STATE_FILE):
        return

    try:
        with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        info = disk_data.get(gid, {})
        disk_msgs = info.get("recent_bot_msgs", [])
        if not disk_msgs:
            return

        now_t = time.time()
        mem_msgs = list(RECENT_GROUP_MSGS[gid])
        need_reorder = False

        for dm in disk_msgs:
            dt = dm.get("time", 0.0)
            dtext = dm.get("text", "").strip()
            if not dtext or (now_t - dt) > 900:  # 超过15分钟的过往记录不补
                continue
            # 检查主进程内存中是否已有这条发言
            already_in_mem = any(
                m.get("is_bot") and (m.get("text") == dtext or dtext in m.get("text", "") or m.get("text", "") in dtext)
                and abs(m.get("time", 0.0) - dt) < 10.0
                for m in mem_msgs
            )
            if not already_in_mem:
                RECENT_GROUP_MSGS[gid].append({
                    "time": dt,
                    "sender_id": BOT_QQ,
                    "nickname": "弥酱",
                    "text": dtext,
                    "is_bot": True
                })
                need_reorder = True

        if need_reorder:
            sorted_msgs = sorted(list(RECENT_GROUP_MSGS[gid]), key=lambda x: x.get("time", 0.0))
            RECENT_GROUP_MSGS[gid].clear()
            RECENT_GROUP_MSGS[gid].extend(sorted_msgs)
    except Exception as e:
        pass


def record_bot_spoke(group_id_str: str, text: str = "(弥酱发言)"):
    """全链路记录机器人发信事件：更新最后发言时间、连续发言计数、滑动窗口及刷新唤醒状态"""
    now_t = time.time()
    time_since_last = now_t - LAST_BOT_MSG_TIME.get(group_id_str, 0.0)

    # 12 秒内若与上一条自己发言高度连续或已包含于完整记录（兼容异步多气泡连发与 WS 回声）：
    # 算作同一轮发言，不增加连续发言计数，更新时间戳并刷新唤醒状态
    if RECENT_GROUP_MSGS[group_id_str] and RECENT_GROUP_MSGS[group_id_str][-1].get("is_bot", False):
        prev_text = RECENT_GROUP_MSGS[group_id_str][-1].get("text", "")
        clean_new = text.strip()
        is_same_burst = time_since_last < 5.0 or (
            time_since_last < 14.0 and clean_new and clean_new != "(弥酱发言)" and clean_new in prev_text
        )
        if is_same_burst:
            LAST_BOT_MSG_TIME[group_id_str] = now_t
            AWAKE_STATES[group_id_str] = now_t
            if clean_new and clean_new != "(弥酱发言)" and clean_new not in prev_text:
                RECENT_GROUP_MSGS[group_id_str][-1]["text"] = f"{prev_text} {clean_new}".strip()
            save_chat_state()
            return

    LAST_BOT_MSG_TIME[group_id_str] = now_t
    CONSECUTIVE_BOT_MSG_COUNT[group_id_str] += 1
    AWAKE_STATES[group_id_str] = now_t
    RECENT_GROUP_MSGS[group_id_str].append({
        "time": now_t,
        "sender_id": BOT_QQ,
        "nickname": "弥酱",
        "text": text.strip(),
        "is_bot": True
    })
    save_chat_state()
    safe_log(f"🤖 [状态更新] 群[{group_id_str}] 录入弥酱发言 (当前连续发言计数: {CONSECUTIVE_BOT_MSG_COUNT[group_id_str]})")


def check_recent_bot_msg(group_id_str: str, n: int = 6) -> bool:
    """防自言自语屏障：检查最近n条里是否有自己的发言"""
    recent = list(RECENT_GROUP_MSGS[group_id_str])
    for m in recent[-n:]:
        if m.get("is_bot", False):
            return True
    return False


# 启动时立即加载持久化状态
load_chat_state()
