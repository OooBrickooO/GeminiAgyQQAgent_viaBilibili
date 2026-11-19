"""
tools/trend_tools.py — 晨起热搜与网络热梗实时嗅探 MCP 业务实现
"""
from config import log_mcp_tool
from trends.trend_manager import TrendManager


async def get_morning_trends_impl() -> str:
    """获取今晨热搜与新闻热点摘要（当天缓存复用）"""
    log_mcp_tool("get_morning_trends", "查询今晨热搜摘要")
    try:
        return await TrendManager.get_morning_trends_summary()
    except Exception as e:
        return f"获取热搜失败: {e}"


async def search_current_meme_impl(keyword: str) -> str:
    """动态追查网络新梗、突发时事与社区讨论详情"""
    log_mcp_tool("search_current_meme", f"关键词: {keyword}")
    try:
        clean_kw = keyword.strip()
        if not clean_kw:
            return "请输入想查询的梗或事件关键词喵。"
        return await TrendManager.search_live_meme(clean_kw)
    except Exception as e:
        return f"查询热梗失败: {e}"
