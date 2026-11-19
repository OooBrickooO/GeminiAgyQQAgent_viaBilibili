"""
tools/system_tools.py — 群白名单与群 Bot 免压白名单热加载、添加、移出与查询 MCP 业务实现

负责：
1. 对 allowed_groups.json 中动态追加/移出群号与全量查询；
2. 对 bot_whitelist.json 中动态追加/移出群 Bot（使弥酱不再将该群 Bot 的发言计入话题压力，避免人机互聊套娃）；
权限控制：白名单修改受主人(MASTER_QQ)专属保护。
"""
import os
import re
import json
import datetime
from config import (
    ALLOWED_GROUPS_FILE, BOT_WHITELIST_FILE, PROFILES_FILE,
    CHAT_STATE_FILE, LONG_TERM_DB_FILE, MASTER_QQ,
    get_bot_whitelist_dict, log_mcp_tool
)


async def add_group_to_whitelist_impl(group_id: str, caller_id: str = "") -> str:
    """将指定 QQ 群加入活跃白名单"""
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    effective_caller = env_caller or str(caller_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
    log_mcp_tool("add_group_to_whitelist", f"申请将群 {group_id} 移入白名单 (caller: {effective_caller})")
    if effective_caller and effective_caller not in (str(MASTER_QQ), "system", "系统"):
        return f"🔒 权限不足：群白名单属于核心管理系统，仅限主人({MASTER_QQ})有权操作喵！"

    try:
        if os.path.exists(ALLOWED_GROUPS_FILE):
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                groups = json.load(f)
        else:
            groups = []

        clean_gid = str(group_id).strip()
        str_groups = [str(g).strip() for g in groups]
        if clean_gid not in str_groups:
            str_groups.append(clean_gid)
            with open(ALLOWED_GROUPS_FILE, "w", encoding="utf-8") as f:
                json.dump(str_groups, f, indent=2, ensure_ascii=False)
            return f"成功：已将群 {clean_gid} 加入热更新白名单！你现在开始会在该群主动冒泡了。"
        else:
            return f"提示：群 {clean_gid} 已经在白名单中，无需重复添加。"
    except Exception as e:
        return f"修改白名单时出错: {str(e)}"


async def remove_group_from_whitelist_impl(group_id: str, caller_id: str = "") -> str:
    """将指定 QQ 群移出活跃白名单"""
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    effective_caller = env_caller or str(caller_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
    log_mcp_tool("remove_group_from_whitelist", f"申请将群 {group_id} 移出白名单 (caller: {effective_caller})")
    if effective_caller and effective_caller not in (str(MASTER_QQ), "system", "系统"):
        return f"🔒 权限不足：群白名单属于核心管理系统，仅限主人({MASTER_QQ})有权操作喵！"

    try:
        if os.path.exists(ALLOWED_GROUPS_FILE):
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                groups = json.load(f)
        else:
            groups = []

        clean_gid = str(group_id).strip()
        str_groups = [str(g).strip() for g in groups]
        if clean_gid in str_groups:
            str_groups = [g for g in str_groups if g != clean_gid]
            with open(ALLOWED_GROUPS_FILE, "w", encoding="utf-8") as f:
                json.dump(str_groups, f, indent=2, ensure_ascii=False)
            return f"成功：已将群 {clean_gid} 移出活跃白名单！你将不再在该群自动进行定时呼吸冒泡或主动搭话。"
        else:
            return f"提示：群 {clean_gid} 本就不在活跃白名单中。"
    except Exception as e:
        return f"移出白名单时出错: {str(e)}"


async def get_whitelist_groups_impl() -> str:
    """获取当前所有活跃白名单群号列表"""
    log_mcp_tool("get_whitelist_groups", "查询活跃白名单群列表")
    try:
        if os.path.exists(ALLOWED_GROUPS_FILE):
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                groups = json.load(f)
        else:
            groups = []

        if not groups:
            return "当前活跃白名单为空，未添加任何群。"
        lines = [f"=== 当前活跃白名单群列表 (共 {len(groups)} 个) ==="]
        for idx, g in enumerate(groups, 1):
            lines.append(f"{idx}. 群号: {g}")
        return "\n".join(lines)
    except Exception as e:
        return f"查询白名单时出错: {str(e)}"


def _resolve_bot_qq_and_name(bot_id_or_name: str, bot_name: str = "") -> tuple[str, str]:
    """
    从传入的 QQ 号、[CQ:at,qq=...] 或昵称中智能解析 (qq, name)。
    若仅提供了昵称，自动从近期聊天缓存、用户档案与历史归档库中匹配对应的 QQ 号。
    """
    raw = str(bot_id_or_name or "").strip()
    name_hint = str(bot_name or "").strip()

    at_match = re.search(r'\[CQ:at,qq=(\d+)\]', raw)
    if at_match:
        qq = at_match.group(1)
    else:
        num_match = re.search(r'\b(\d{5,12})\b', raw)
        qq = num_match.group(1) if num_match else ""

    if not name_hint and not qq and raw:
        name_hint = raw

    # 若已有 QQ 但无昵称，或仅有昵称需反查 QQ，扫描本地状态与归档
    try:
        if os.path.exists(CHAT_STATE_FILE):
            with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
            recent_groups = st.get("recent_group_msgs", {})
            for _, msgs in recent_groups.items():
                for m in reversed(msgs or []):
                    m_qq = str(m.get("sender_id", "")).strip()
                    m_nick = str(m.get("nickname", "")).strip()
                    if qq and m_qq == qq and not name_hint and m_nick:
                        name_hint = m_nick
                        break
                    if not qq and name_hint and m_nick and name_hint.lower() in m_nick.lower():
                        qq = m_qq
                        name_hint = m_nick
                        break
    except Exception:
        pass

    if not qq and name_hint and os.path.exists(LONG_TERM_DB_FILE):
        try:
            import sqlite3
            conn = sqlite3.connect(LONG_TERM_DB_FILE)
            row = conn.execute(
                "SELECT sender_id, sender_name FROM chat_archive_fts WHERE sender_name LIKE ? ORDER BY rowid DESC LIMIT 1",
                (f"%{name_hint}%",)
            ).fetchone()
            conn.close()
            if row and str(row[0]).isdigit():
                qq = str(row[0]).strip()
                if not bot_name:
                    name_hint = str(row[1]).strip()
        except Exception:
            pass

    if qq and not name_hint and os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                profs = json.load(f)
            if qq in profs and isinstance(profs[qq], dict):
                name_hint = str(profs[qq].get("nickname", "")).strip()
        except Exception:
            pass

    return qq, name_hint


async def add_bot_to_whitelist_impl(bot_id_or_name: str, bot_name: str = "", caller_id: str = "") -> str:
    """将指定群 Bot 加入免压白名单（之后不再将该群 Bot 的发言计入群聊压力）"""
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    effective_caller = env_caller or str(caller_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
    log_mcp_tool("add_bot_to_whitelist", f"申请将群 Bot [{bot_id_or_name} / {bot_name}] 加入免压白名单 (caller: {effective_caller})")
    if effective_caller and effective_caller not in (str(MASTER_QQ), "system", "系统"):
        return f"🔒 权限不足：群 Bot 白名单仅限主人({MASTER_QQ})有权指定喵！"

    qq, resolved_name = _resolve_bot_qq_and_name(bot_id_or_name, bot_name)
    if not qq and not resolved_name:
        return "请提供要加入免压白名单的群 Bot 的 QQ 号或群内昵称喵！"

    try:
        data = get_bot_whitelist_dict()
        key = qq if qq else f"name:{resolved_name}"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data[key] = {
            "qq": qq,
            "name": resolved_name,
            "added_at": now_str
        }
        with open(BOT_WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        display_label = f"{resolved_name}({qq})" if (qq and resolved_name) else (qq or resolved_name)
        return f"成功：已将群 Bot [{display_label}] 加入 Bot 免压白名单！弥酱今后不会再将它的发言计入群聊话题压力了喵。"
    except Exception as e:
        return f"添加群 Bot 白名单时出错: {str(e)}"


async def remove_bot_from_whitelist_impl(bot_id_or_name: str, caller_id: str = "") -> str:
    """将指定群 Bot 移出免压白名单"""
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    effective_caller = env_caller or str(caller_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
    log_mcp_tool("remove_bot_from_whitelist", f"申请将群 Bot [{bot_id_or_name}] 移出免压白名单 (caller: {effective_caller})")
    if effective_caller and effective_caller not in (str(MASTER_QQ), "system", "系统"):
        return f"🔒 权限不足：群 Bot 白名单仅限主人({MASTER_QQ})有权修改喵！"

    target = str(bot_id_or_name or "").strip()
    at_m = re.search(r'\[CQ:at,qq=(\d+)\]', target)
    if at_m:
        target = at_m.group(1)

    try:
        data = get_bot_whitelist_dict()
        removed = []
        for k, info in list(data.items()):
            entry_qq = str(info.get("qq", "")) if isinstance(info, dict) else ""
            entry_name = str(info.get("name", "")) if isinstance(info, dict) else ""
            if k == target or entry_qq == target or (target and entry_name.lower() == target.lower()):
                removed.append(f"{entry_name}({entry_qq})" if (entry_name and entry_qq) else (entry_qq or entry_name or k))
                data.pop(k, None)

        if removed:
            with open(BOT_WHITELIST_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return f"成功：已将 {', '.join(removed)} 从群 Bot 免压白名单中移出！"
        return f"提示：未在群 Bot 免压白名单中找到 [{target}]。"
    except Exception as e:
        return f"移出群 Bot 白名单时出错: {str(e)}"


async def get_bot_whitelist_impl() -> str:
    """查询当前群 Bot 免压白名单列表"""
    log_mcp_tool("get_bot_whitelist", "查询群 Bot 免压白名单列表")
    try:
        data = get_bot_whitelist_dict()
        if not data:
            return "当前群 Bot 免压白名单为空，尚未登记任何群 Bot。"
        lines = [f"=== 当前群 Bot 免压白名单 (共 {len(data)} 个，发言不计入话题压力) ==="]
        for idx, (k, info) in enumerate(data.items(), 1):
            if isinstance(info, dict):
                qq = info.get("qq") or "待首次发言自动绑定"
                name = info.get("name") or "未备注昵称"
                lines.append(f"{idx}. 昵称: {name} | QQ: {qq}")
            else:
                lines.append(f"{idx}. QQ: {k}")
        return "\n".join(lines)
    except Exception as e:
        return f"查询群 Bot 白名单时出错: {str(e)}"

