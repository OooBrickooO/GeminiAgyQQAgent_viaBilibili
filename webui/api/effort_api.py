"""
webui/api/effort_api.py — 局部对话通道 low 强度开关 WebUI 管理 API (1C + L1)

仅作用于前台对话的局部通道 (group_xxx / private_xxx)，
未开启的通道默认走 medium/high，其他后台任务与全局逻辑不受影响。
"""
import os
import json
import datetime
from webui.config import (
    ALLOWED_GROUPS_FILE,
    PROFILES_FILE,
    MASTER_QQ,
    CHAT_STATE_FILE,
)
from core.state_manager import (
    get_low_effort_channels,
    set_channel_low_enabled,
    clear_all_low_effort_channels,
    normalize_low_channel_id,
)


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc) + datetime.timedelta(hours=8)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def get_low_effort_overview() -> dict:
    """获取局部 low 开关全景：已开启列表 + 可快速切换的群聊与私聊通道"""
    enabled_map = get_low_effort_channels(force_reload=True)

    # 1. 读取活跃白名单群
    allowed_groups = []
    if os.path.exists(ALLOWED_GROUPS_FILE):
        try:
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                raw_g = json.load(f)
            if isinstance(raw_g, list):
                allowed_groups = [str(g).strip() for g in raw_g if str(g).strip()]
        except Exception:
            pass

    # 2. 读取用户画像（主人 + 高好感/常见私聊对象）
    profiles = {}
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                raw_p = json.load(f)
            if isinstance(raw_p, dict):
                profiles = raw_p
        except Exception:
            pass

    # 3. 读取近期在 chat_state.json 出现过的私聊通道
    recent_private_ids = set()
    if os.path.exists(CHAT_STATE_FILE):
        try:
            with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
            if isinstance(st, dict):
                for k in st.keys():
                    if str(k).startswith("private_"):
                        recent_private_ids.add(str(k).replace("private_", "", 1))
        except Exception:
            pass

    channels = []
    seen = set()

    # 3.1 主人私聊永远排第一位
    master_cid = f"private_{MASTER_QQ}"
    master_info = enabled_map.get(master_cid)
    channels.append({
        "chat_id": master_cid,
        "type": "private",
        "label": f"👑 主人私聊 ({MASTER_QQ})",
        "enabled": bool(master_info),
        "updated_at": _fmt_ts(master_info.get("updated_at", 0.0)) if master_info else "-",
    })
    seen.add(master_cid)

    # 3.2 活跃白名单群
    for gid in sorted(set(allowed_groups)):
        cid = f"group_{gid}"
        if cid in seen:
            continue
        info = enabled_map.get(cid)
        channels.append({
            "chat_id": cid,
            "type": "group",
            "label": (info.get("label") if info and info.get("label") else f"💬 群聊 {gid}"),
            "enabled": bool(info),
            "updated_at": _fmt_ts(info.get("updated_at", 0.0)) if info else "-",
        })
        seen.add(cid)

    # 3.3 近期私聊或高好感好友
    priv_candidates = []
    for uid, prof in profiles.items():
        uid_str = str(uid).strip()
        if not uid_str or uid_str == str(MASTER_QQ):
            continue
        aff = float((prof or {}).get("affection", 0.0))
        if aff >= 200.0 or uid_str in recent_private_ids or f"private_{uid_str}" in enabled_map:
            nick = str((prof or {}).get("nickname") or uid_str)
            priv_candidates.append((aff, uid_str, nick))
    priv_candidates.sort(key=lambda x: x[0], reverse=True)
    for aff, uid_str, nick in priv_candidates[:20]:
        cid = f"private_{uid_str}"
        if cid in seen:
            continue
        info = enabled_map.get(cid)
        channels.append({
            "chat_id": cid,
            "type": "private",
            "label": f"💌 私聊 {nick} ({uid_str})",
            "enabled": bool(info),
            "updated_at": _fmt_ts(info.get("updated_at", 0.0)) if info else "-",
        })
        seen.add(cid)

    # 3.4 手动添加且不在上面候选中的已开启通道
    for cid, info in enabled_map.items():
        if cid in seen:
            continue
        ctype = "private" if cid.startswith("private_") else "group"
        channels.append({
            "chat_id": cid,
            "type": ctype,
            "label": info.get("label") or cid,
            "enabled": True,
            "updated_at": _fmt_ts(info.get("updated_at", 0.0)),
        })
        seen.add(cid)

    return {
        "success": True,
        "enabled_count": len(enabled_map),
        "default_mode": "medium / high (全局默认禁 low)",
        "enabled_channels": list(enabled_map.keys()),
        "channels": channels,
    }


def manage_low_effort_api(action: str = "toggle", chat_id: str = "", label: str = "") -> dict:
    """管理局部对话通道 low 强度开关: toggle | enable | disable | clear_all"""
    act = str(action or "toggle").strip().lower()

    if act == "clear_all":
        clear_all_low_effort_channels()
        res = get_low_effort_overview()
        res["message"] = "已清空全部局部 low 开关，所有对话恢复默认 medium/high。"
        return res

    cid = normalize_low_channel_id(chat_id)
    if not cid:
        return {"success": False, "error": "缺少有效的 chat_id（格式如 private_YOUR_MASTER_QQ 或 group_123456）"}

    cur_map = get_low_effort_channels(force_reload=True)
    cur_on = bool(cur_map.get(cid, {}).get("enabled", False))

    if act == "toggle":
        target_on = not cur_on
    elif act in ("enable", "on", "add"):
        target_on = True
    elif act in ("disable", "off", "remove"):
        target_on = False
    else:
        return {"success": False, "error": f"不支持的 action: {action}"}

    set_channel_low_enabled(cid, target_on, label=label)
    res = get_low_effort_overview()
    res["message"] = f"通道 {cid} 已{'开启局部强制 low 档' if target_on else '关闭 low 档并恢复默认 medium/high'}。"
    return res
