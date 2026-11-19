"""
tools/omikuji_tools.py — 浅草寺猫猫御神签 MCP 业务实现
"""
from config import log_mcp_tool
from toys.omikuji import OmikujiService


async def draw_daily_omikuji_impl(user_id: str, nickname: str = "") -> str:
    """为指定群友抽取今日浅草寺猫猫神签（每日每人限一次，依日期哈希防刷）"""
    log_mcp_tool("draw_daily_omikuji", f"用户: {user_id} ({nickname})")
    try:
        clean_uid = str(user_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
        return OmikujiService.draw(clean_uid, nickname=nickname)
    except Exception as e:
        return f"求签出错: {e}"
