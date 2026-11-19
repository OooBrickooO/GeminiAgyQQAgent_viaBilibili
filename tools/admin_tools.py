"""
tools/admin_tools.py — 群管理权限工具实现 (禁言/解禁)

严格安全保护：
- 绝对禁止禁言主人 (MASTER_QQ)
- 绝对禁止禁言机器人自身 (BOT_QQ)
- 严禁提供任何修改他人群名片的工具 (防止 Prompt 注入诱导改名)
"""
import asyncio
from config import MASTER_QQ, BOT_QQ
from core.napcat_client import set_group_ban, get_group_member_info


async def ban_group_member_impl(group_id: int, user_id: int, duration_minutes: int = 3, reason: str = "") -> str:
    """
    对指定群成员执行禁言惩罚。
    参数：
    - group_id: 目标群号
    - user_id: 被禁言群友的 QQ 号
    - duration_minutes: 禁言时长（单位：分钟，默认 3 分钟，上限 43200 分钟）
    - reason: 禁言理由（可选）
    """
    uid_str = str(user_id)
    if uid_str == str(MASTER_QQ):
        return "❌ 操作被拒绝：绝对禁止禁言主人！主人拥有最高特权喵。"
    if uid_str == str(BOT_QQ):
        return "❌ 操作被拒绝：弥酱不能禁言自己喵！"

    # 时长保护
    minutes = max(1, min(int(duration_minutes), 43200))
    duration_seconds = minutes * 60

    try:
        # 预先检查对方是否也是管理/群主
        info = await get_group_member_info(group_id, user_id)
        if info.get("status") == "ok" and "data" in info:
            target_role = info["data"].get("role", "member")
            if target_role in ("owner", "admin"):
                return f"❌ 禁言失败：用户 {user_id} 是该群的{target_role}（群主或管理员），弥酱没有权限禁言管理喵！"

        res = await set_group_ban(group_id, user_id, duration_seconds)
        if res.get("status") == "ok":
            reason_str = f"（原因: {reason}）" if reason else ""
            return f"✅ 禁言成功：已成功将群[{group_id}]的用户 {user_id} 禁言 {minutes} 分钟{reason_str}喵！"
        else:
            return f"❌ 禁言失败：底层接口返回 {res}（可能是弥酱在本群不是管理员，或者权限不足喵）"
    except Exception as e:
        return f"❌ 禁言执行异常: {e}"


async def unban_group_member_impl(group_id: int, user_id: int) -> str:
    """
    解除指定群成员的禁言状态。
    参数：
    - group_id: 目标群号
    - user_id: 被解禁群友的 QQ 号
    """
    try:
        res = await set_group_ban(group_id, user_id, 0)
        if res.get("status") == "ok":
            return f"✅ 解禁成功：已成功解除群[{group_id}]用户 {user_id} 的禁言状态喵！"
        else:
            return f"❌ 解禁失败：{res}"
    except Exception as e:
        return f"❌ 解禁执行异常: {e}"
