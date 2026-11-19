"""
attention/user_mute.py — 群友静默管理系统 (群友专属“别理我/暂时别回复我”指令)

功能：
1. 群友@唤起“别理我”/“暂时别回复我”等指令时，禁止对其直接回复 12h (或自定义时长)
2. 识别并解析自定义时长（如“别理我 2小时”、“别回复我 半小时”、“屏蔽我 1天”等）
3. 识别“可以理我了”/“解除屏蔽”等解禁指令并恢复直接回复
4. 本地 JSON 原子落盘，进程重启状态不丢
"""
import os
import re
import json
import time
from typing import Tuple, Dict, Any
from config import MUTED_USERS_FILE, safe_log


def load_muted_users() -> Dict[str, Any]:
    """加载当前所有静默用户字典，自动过滤已过期的记录"""
    if not os.path.exists(MUTED_USERS_FILE):
        return {}
    try:
        with open(MUTED_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                now = time.time()
                # 剔除已过期记录
                active = {uid: info for uid, info in data.items() if info.get("expire_at", 0) > now}
                return active
    except Exception as e:
        safe_log(f"⚠️ [user_mute] 读取 muted_users.json 失败: {e}")
    return {}


def save_muted_users(data: Dict[str, Any]):
    """原子更新 muted_users.json"""
    try:
        tmp_file = MUTED_USERS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, MUTED_USERS_FILE)
    except Exception as e:
        safe_log(f"⚠️ [user_mute] 保存 muted_users.json 异常: {e}")


def is_user_muted(user_id: str) -> bool:
    """检查指定用户当前是否处于静默保护期"""
    if not user_id:
        return False
    muted = load_muted_users()
    info = muted.get(str(user_id))
    if not info:
        return False
    expire_at = info.get("expire_at", 0)
    return time.time() < expire_at


def mute_user(user_id: str, duration_seconds: float, duration_desc: str, group_id: str = "") -> Dict[str, Any]:
    """将指定用户加入静默列表"""
    uid_str = str(user_id)
    muted = load_muted_users()
    now = time.time()
    expire_at = now + duration_seconds
    record = {
        "user_id": uid_str,
        "group_id": str(group_id),
        "set_at": now,
        "expire_at": expire_at,
        "duration_desc": duration_desc
    }
    muted[uid_str] = record
    save_muted_users(muted)
    safe_log(f"[用户静默生效] 用户 {uid_str} 已开启静默，时长: {duration_desc} (截至 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expire_at))})")
    return record


def unmute_user(user_id: str) -> bool:
    """解禁指定用户"""
    uid_str = str(user_id)
    muted = load_muted_users()
    if uid_str in muted:
        muted.pop(uid_str, None)
        save_muted_users(muted)
        safe_log(f"[用户静默解除] 用户 {uid_str} 已解除静默，恢复正常互动！")
        return True
    return False


def parse_mute_command(text: str) -> Tuple[bool, float, str]:
    """
    解析文本中是否包含“别理我/暂时别回复我”等静默指令及对应时长。
    返回 (is_mute_cmd, duration_seconds, duration_desc)
    """
    if not text or not isinstance(text, str):
        return False, 0.0, ""

    clean = text.strip()
    mute_keywords = [
        "暂时别回复我", "别回复我", "不要回复我", "不用回复我",
        "暂时别理我", "别理我", "不要理我", "不用理我",
        "屏蔽我", "安静点", "暂时安静", "别管我", "暂时别管我"
    ]
    if not any(kw in clean for kw in mute_keywords):
        return False, 0.0, ""

    # 默认 12 小时
    duration_hours = 12.0

    # 1. 常见自然语言时长词
    if "半天" in clean:
        duration_hours = 12.0
    elif "一个半小时" in clean or "1个半小时" in clean:
        duration_hours = 1.5
    elif "两个半小时" in clean or "2个半小时" in clean:
        duration_hours = 2.5
    elif "半小时" in clean or "半个小时" in clean:
        duration_hours = 0.5
    else:
        # 2. 正则提取数字 + 单位
        m = re.search(r'(\d+(?:\.\d+)?)\s*(天|日|d|days?|小时|个半小时|小时半|h|hrs?|hours?|分钟|分|m|mins?|minutes?|秒|s|secs?)', clean, re.IGNORECASE)
        if m:
            num = float(m.group(1))
            unit = m.group(2).lower()
            if any(u in unit for u in ["天", "日", "d"]):
                duration_hours = num * 24.0
            elif any(u in unit for u in ["小时", "h"]):
                duration_hours = num
            elif any(u in unit for u in ["分", "m"]):
                duration_hours = num / 60.0
            elif any(u in unit for u in ["秒", "s"]):
                duration_hours = num / 3600.0

    # 上下限约束：最少 1 分钟，最多 30 天
    duration_hours = max(1.0 / 60.0, min(duration_hours, 24.0 * 30))

    if duration_hours >= 24 and (duration_hours % 24 == 0):
        desc = f"{int(duration_hours // 24)}天"
    elif duration_hours >= 1:
        desc = f"{duration_hours:g}小时"
    else:
        desc = f"{int(round(duration_hours * 60))}分钟"

    return True, duration_hours * 3600.0, desc


def parse_unmute_command(text: str) -> bool:
    """解析文本中是否包含解禁静默指令"""
    if not text or not isinstance(text, str):
        return False
    unmute_keywords = [
        "可以理我了", "可以回复我了", "恢复回复", "解除屏蔽",
        "取消静默", "解除静默", "允许回复", "来理我", "理理我", "理我一下"
    ]
    return any(kw in text for kw in unmute_keywords)
