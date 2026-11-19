"""
webui/api/whitelist_api.py — WebUI 群聊白名单与群 Bot 免压白名单管理 API
支持：
1. 自动从 NapCat OneBot 接口拉取机器人当前所在的所有 QQ 群（群名、群号、人数），并与 allowed_groups.json 合并展示；
2. 一键切换（Toggle）、手动新增或移出活跃白名单群（秒级热生效）；
3. 可视化管理群内第三方 Bot 免压白名单（bot_whitelist.json），支持按 QQ 号或昵称智能解析添加与移除。
"""
import os
import re
import json
import datetime
import urllib.request
from config import (
    ALLOWED_GROUPS_FILE,
    BOT_WHITELIST_FILE,
    CHAT_STATE_FILE,
    ONEBOT_HTTP_URL,
    NAPCAT_TOKEN,
    get_bot_whitelist_dict,
    log_mcp_tool
)
from tools.system_tools import _resolve_bot_qq_and_name


def _fetch_napcat_group_list() -> list[dict]:
    """同步请求 NapCat /get_group_list 获取机器人当前所在的 QQ 群列表（超时 2.5s 平滑降级）"""
    url = f"{ONEBOT_HTTP_URL.rstrip('/')}/get_group_list"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NAPCAT_TOKEN}"
        },
        method="POST"
    )
    try:
        # 绕过系统代理直连内网 OneBot
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2.5) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            if raw.get("status") == "ok" and isinstance(raw.get("data"), list):
                return raw["data"]
    except Exception:
        pass
    return []


def _load_allowed_groups_list() -> list[str]:
    if os.path.exists(ALLOWED_GROUPS_FILE):
        try:
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(g).strip() for g in data if str(g).strip()]
        except Exception:
            pass
    return []


def _save_allowed_groups_list(groups: list[str]):
    clean_list = []
    seen = set()
    for g in groups:
        s = str(g).strip()
        if s and s not in seen:
            seen.add(s)
            clean_list.append(s)
    tmp_path = f"{ALLOWED_GROUPS_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean_list, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, ALLOWED_GROUPS_FILE)


def get_whitelist_overview() -> dict:
    """获取群聊白名单全景数据（融合 OneBot 实时群列表、本地活跃状态与群 Bot 免压白名单）"""
    allowed_list = _load_allowed_groups_list()
    allowed_set = set(allowed_list)

    # 读取本地聊天状态统计近期消息数
    recent_counts = {}
    if os.path.exists(CHAT_STATE_FILE):
        try:
            with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
            rgm = st.get("recent_group_msgs", {})
            if isinstance(rgm, dict):
                for gid, msgs in rgm.items():
                    if isinstance(msgs, list):
                        recent_counts[str(gid).strip()] = len(msgs)
        except Exception:
            pass

    napcat_groups = _fetch_napcat_group_list()
    groups_map = {}

    for item in napcat_groups:
        gid = str(item.get("group_id", "")).strip()
        if not gid:
            continue
        groups_map[gid] = {
            "group_id": gid,
            "group_name": item.get("group_name") or f"QQ群 {gid}",
            "member_count": int(item.get("member_count", 0) or 0),
            "max_member_count": int(item.get("max_member_count", 0) or 0),
            "whitelisted": gid in allowed_set,
            "recent_msg_count": recent_counts.get(gid, 0),
            "in_bot_group_list": True
        }

    # 补充已在白名单或本地有缓存但 OneBot 未返回的群号
    for gid in list(allowed_list) + list(recent_counts.keys()):
        gid_str = str(gid).strip()
        if gid_str and gid_str not in groups_map:
            groups_map[gid_str] = {
                "group_id": gid_str,
                "group_name": f"群聊 {gid_str}",
                "member_count": 0,
                "max_member_count": 0,
                "whitelisted": gid_str in allowed_set,
                "recent_msg_count": recent_counts.get(gid_str, 0),
                "in_bot_group_list": False
            }

    # 排序：已开启白名单的排前面，其次按近期活跃度和成员数排序
    sorted_groups = sorted(
        groups_map.values(),
        key=lambda x: (
            0 if x["whitelisted"] else 1,
            -x["recent_msg_count"],
            -x["member_count"],
            x["group_id"]
        )
    )

    # 读取群 Bot 免压白名单
    bot_dict = get_bot_whitelist_dict()
    bot_list = []
    for k, info in bot_dict.items():
        if isinstance(info, dict):
            bot_list.append({
                "key": str(k),
                "qq": str(info.get("qq", "")).strip(),
                "name": str(info.get("name", "")).strip() or "未命名Bot",
                "added_at": str(info.get("added_at", "")).strip() or "--"
            })
        else:
            bot_list.append({
                "key": str(k),
                "qq": str(k),
                "name": "群Bot",
                "added_at": "--"
            })

    return {
        "success": True,
        "napcat_online": len(napcat_groups) > 0,
        "whitelisted_count": len(allowed_set),
        "total_groups_count": len(sorted_groups),
        "groups": sorted_groups,
        "bot_whitelist": bot_list
    }


def manage_group_whitelist_api(action: str, group_id: str) -> dict:
    """WebUI 添加、移出或切换指定 QQ 群的白名单状态"""
    act = (action or "").strip().lower()
    clean_gid = re.sub(r"[^\d]", "", str(group_id or "").strip())
    if not clean_gid:
        return {"success": False, "error": "请输入有效的纯数字 QQ 群号"}

    try:
        current = _load_allowed_groups_list()
        if act == "add":
            if clean_gid not in current:
                current.append(clean_gid)
            msg = f"已将群 {clean_gid} 加入活跃白名单"
        elif act == "remove":
            current = [g for g in current if g != clean_gid]
            msg = f"已将群 {clean_gid} 移出活跃白名单"
        elif act == "toggle":
            if clean_gid in current:
                current = [g for g in current if g != clean_gid]
                msg = f"已关闭群 {clean_gid} 的活跃白名单"
            else:
                current.append(clean_gid)
                msg = f"已开启群 {clean_gid} 的活跃白名单"
        else:
            return {"success": False, "error": f"不支持的操作类型: {act}"}

        _save_allowed_groups_list(current)
        log_mcp_tool("webui_group_whitelist", f"{act} -> {clean_gid}")
        res = get_whitelist_overview()
        res["message"] = msg
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


def manage_bot_whitelist_api(action: str, bot_id_or_name: str, bot_name: str = "") -> dict:
    """WebUI 添加或移除群 Bot 免压白名单"""
    act = (action or "").strip().lower()
    raw_target = str(bot_id_or_name or "").strip()
    if not raw_target:
        return {"success": False, "error": "请提供群 Bot 的 QQ 号或群内昵称"}

    try:
        data = get_bot_whitelist_dict()
        if act == "add":
            qq, resolved_name = _resolve_bot_qq_and_name(raw_target, bot_name)
            if not qq and not resolved_name:
                return {"success": False, "error": "无法识别有效的 QQ 号或昵称"}

            now_str = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            key = qq if qq else f"name:{resolved_name}"
            if qq and f"name:{resolved_name}" in data:
                data.pop(f"name:{resolved_name}", None)

            data[key] = {
                "qq": qq,
                "name": resolved_name or f"群Bot({qq})",
                "added_at": now_str
            }
            with open(BOT_WHITELIST_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log_mcp_tool("webui_bot_whitelist", f"add -> {qq or resolved_name}")
            res = get_whitelist_overview()
            res["message"] = f"已将群 Bot [{resolved_name or qq}] 加入免压白名单"
            return res

        elif act == "remove":
            qq, resolved_name = _resolve_bot_qq_and_name(raw_target, "")
            removed = False
            for k in list(data.keys()):
                info = data.get(k, {})
                entry_qq = str(info.get("qq", "")).strip() if isinstance(info, dict) else ""
                entry_name = str(info.get("name", "")).strip() if isinstance(info, dict) else ""
                if (
                    k == raw_target
                    or (qq and (k == qq or entry_qq == qq))
                    or (entry_name and raw_target.lower() in entry_name.lower())
                ):
                    data.pop(k, None)
                    removed = True

            if not removed:
                return {"success": False, "error": f"未在免压白名单中找到 {raw_target}"}

            with open(BOT_WHITELIST_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log_mcp_tool("webui_bot_whitelist", f"remove -> {raw_target}")
            res = get_whitelist_overview()
            res["message"] = f"已从免压白名单中移除 {raw_target}"
            return res
        else:
            return {"success": False, "error": f"不支持的操作类型: {act}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
