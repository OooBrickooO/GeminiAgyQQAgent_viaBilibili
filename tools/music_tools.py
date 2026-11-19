"""
tools/music_tools.py — 网易云音乐点歌与主人曲库分享 MCP 业务实现

包含：
- search_music_card_impl: 全网搜索歌曲并发送真实 OneBot 音乐卡片
- play_master_song_impl: 从主人的私藏曲库中选曲并发送音乐卡片（支持风格/歌手关键词或随机盲抽）
"""
from config import log_mcp_tool
from music.playlist_service import PlaylistService
from music.netease_client import NetEaseClient


async def search_music_card_impl(keyword: str, reason: str = "") -> str:
    """全网搜索网易云音乐并生成音乐卡片与推荐语"""
    log_mcp_tool("search_music_card", f"关键词: {keyword}")
    try:
        clean_kw = keyword.strip()
        if not clean_kw:
            return "请指定想搜索的歌名或歌手喵。"

        songs = await NetEaseClient.search_songs(clean_kw, limit=1)
        if not songs:
            return f"弥酱在网易云搜了一圈，没找到关于【{clean_kw}】的歌曲喵～换个关键词试试？"

        target_song = songs[0]
        return await PlaylistService.format_song_recommendation(
            target_song, is_from_master=False, custom_comment=reason
        )
    except Exception as e:
        return f"点歌失败: {e}"


async def play_master_song_impl(keyword_or_tag: str = "", reason: str = "") -> str:
    """从主人的珍藏曲库中挑选一首歌并生成音乐卡片（支持 Vocaloid、Mili、音游或歌手/歌名筛选）"""
    log_mcp_tool("play_master_song", f"筛选关键词: {keyword_or_tag}")
    try:
        song = await PlaylistService.pick_master_song(keyword_or_tag)
        if not song:
            return f"主人的歌单里暂时没有匹配【{keyword_or_tag}】的歌呢喵～要不弥酱帮你全网搜搜看？"

        return await PlaylistService.format_song_recommendation(
            song, is_from_master=True, custom_comment=reason
        )
    except Exception as e:
        return f"调取主人歌单失败: {e}"
