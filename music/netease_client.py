"""
music/netease_client.py — 网易云音乐公开轻量异步 HTTP 客户端

支持：
- 歌曲异步搜索 (公开搜索接口)
- 歌曲详情获取
- 公开歌单曲目获取
- 格式化 OneBot v11 音乐卡片 CQ 码 ([CQ:music,type=163,id=...])
"""
import re
import httpx
from typing import List, Dict, Optional


class NetEaseClient:
    SEARCH_URL = "https://music.163.com/api/search/get/web"
    PLAYLIST_URL = "https://music.163.com/api/v6/playlist/detail"
    SONG_DETAIL_URL = "https://music.163.com/api/v3/song/detail"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://music.163.com/",
        "Accept": "*/*",
    }

    @classmethod
    async def search_songs(cls, keyword: str, limit: int = 5) -> List[Dict]:
        """
        全网异步搜索网易云音乐歌曲
        返回格式: [{"id": 12345, "name": "...", "artists": "...", "album": "...", "cq_code": "..."}]
        """
        clean_kw = keyword.strip()
        if not clean_kw:
            return []

        params = {
            "s": clean_kw,
            "type": 1,       # 1: 单曲
            "offset": 0,
            "total": "true",
            "limit": max(1, min(limit, 20))
        }

        try:
            async with httpx.AsyncClient(timeout=8.0, headers=cls.HEADERS) as client:
                resp = await client.get(cls.SEARCH_URL, params=params)
                if resp.status_code != 200:
                    print(f"[NetEaseClient] 搜索失败，HTTP状态码: {resp.status_code}")
                    return []

                data = resp.json()
                raw_songs = data.get("result", {}).get("songs", [])
                results = []
                for s in raw_songs:
                    sid = s.get("id")
                    if not sid:
                        continue
                    artists_list = [a.get("name", "") for a in s.get("artists", []) if a.get("name")]
                    artists_str = ", ".join(artists_list) if artists_list else "未知歌手"
                    album_name = s.get("album", {}).get("name", "")

                    results.append({
                        "id": sid,
                        "name": s.get("name", "未知歌名"),
                        "artists": artists_str,
                        "album": album_name,
                        "cq_code": f"[CQ:music,type=163,id={sid}]"
                    })
                return results
        except Exception as e:
            print(f"[NetEaseClient] 搜索歌曲出错: {e}")
            return []

    @classmethod
    async def get_playlist_tracks(cls, playlist_id: str, limit: int = 100) -> List[Dict]:
        """
        根据歌单 ID 异步获取在线歌单内前 limit 首歌曲
        """
        clean_pid = str(playlist_id).strip()
        # 提取纯数字 ID
        match = re.search(r'\d+', clean_pid)
        if not match:
            return []
        numeric_id = match.group()

        params = {"id": numeric_id}
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=cls.HEADERS) as client:
                resp = await client.get(cls.PLAYLIST_URL, params=params)
                if resp.status_code != 200:
                    return []

                data = resp.json()
                playlist = data.get("playlist", {})
                tracks = playlist.get("tracks", [])
                results = []
                for t in tracks[:limit]:
                    tid = t.get("id")
                    if not tid:
                        continue
                    artists_list = [a.get("name", "") for a in t.get("ar", []) if a.get("name")]
                    artists_str = ", ".join(artists_list) if artists_list else "未知歌手"
                    album_name = t.get("al", {}).get("name", "")

                    results.append({
                        "id": tid,
                        "name": t.get("name", "未知歌名"),
                        "artists": artists_str,
                        "album": album_name,
                        "cq_code": f"[CQ:music,type=163,id={tid}]"
                    })
                return results
        except Exception as e:
            print(f"[NetEaseClient] 获取歌单曲目出错: {e}")
            return []
