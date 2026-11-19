"""
music/playlist_service.py — 主人私藏歌单与网易云音乐点播业务服务

负责：
1. 优先从主人的珍藏曲库中检索或随机抽取歌曲（支持 Vocaloid、Mili、音游、J-Pop 等风格与歌手筛选）
2. 全网点歌后备支持
3. 组合生成带 OneBot v11 原生卡片代码与少女随笔推荐词的完整消息
"""
import os
import json
import random
from typing import Optional, Dict, List
from .netease_client import NetEaseClient

# 本地可选主人的红心歌单路径（若存在则优先即时读取）
LOCAL_MASTER_TRACKS_PATH = os.environ.get("LOCAL_MASTER_TRACKS_PATH", r"path\to\favorite_tracks.json")


class PlaylistService:
    _cached_master_tracks: Optional[List[Dict]] = None

    @classmethod
    def _load_local_master_tracks(cls) -> List[Dict]:
        """按需加载主人的本地珍藏曲目缓存（若文件存在）"""
        if cls._cached_master_tracks is not None:
            return cls._cached_master_tracks

        if os.path.exists(LOCAL_MASTER_TRACKS_PATH):
            try:
                with open(LOCAL_MASTER_TRACKS_PATH, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                cleaned = []
                for item in raw_data:
                    orig_id = item.get("originalId") or item.get("id")
                    if not orig_id:
                        continue
                    artists_list = [a.get("name", "") for a in item.get("artists", []) if a.get("name")]
                    artists_str = ", ".join(artists_list) if artists_list else "未知歌手"
                    cleaned.append({
                        "id": orig_id,
                        "name": item.get("name", "未知歌曲"),
                        "artists": artists_str,
                        "album": item.get("album", {}).get("name", "") if isinstance(item.get("album"), dict) else "",
                        "cq_code": f"[CQ:music,type=163,id={orig_id}]"
                    })
                cls._cached_master_tracks = cleaned
                return cleaned
            except Exception as e:
                print(f"[PlaylistService] 读取本地主人歌单出错: {e}")
        return []

    @classmethod
    async def pick_master_song(cls, keyword: str = "") -> Optional[Dict]:
        """
        从主人的私藏曲库中挑选一首歌：
        - 若提供 keyword，按歌名、歌手或曲风关键词模糊匹配；
        - 若未提供 keyword 或未匹配到，则从主人的珍藏曲库中随机盲抽一首宝藏曲目；
        - 若本地无文件，则退化至全网以关键词检索。
        """
        tracks = cls._load_local_master_tracks()
        clean_kw = keyword.strip().lower()

        if tracks:
            if clean_kw:
                # 模糊匹配歌名或歌手
                matched = [
                    t for t in tracks
                    if clean_kw in t["name"].lower() or clean_kw in t["artists"].lower()
                ]
                if matched:
                    return random.choice(matched)

            # 无指定关键词或未匹配到时随机抽取一首
            return random.choice(tracks)

        # 兜底：若本地暂无静态歌单文件，直接通过全网搜索检索
        search_kw = keyword if clean_kw else "Mili"
        results = await NetEaseClient.search_songs(search_kw, limit=1)
        if results:
            return results[0]
        return None

    @classmethod
    async def format_song_recommendation(
        cls,
        song: Dict,
        is_from_master: bool = True,
        custom_comment: str = ""
    ) -> str:
        """
        构造包含真实音乐卡片与弥酱个性化推荐文案的完整回复
        """
        name = song.get("name", "")
        artists = song.get("artists", "")
        cq_code = song.get("cq_code", "")

        if custom_comment:
            comment_text = custom_comment
        elif is_from_master:
            openings = [
                f"哼哼，这可是主人歌单里的私家宝藏！《{name}》- {artists}，戴上耳机前奏超绝的喵～",
                f"当当！从主人的红心歌单里挑出来的《{name}》- {artists}，便宜你们啦喵！",
                f"喏，主人最爱听的之一哦！《{name}》- {artists}，快去听听看喵～",
            ]
            comment_text = random.choice(openings)
        else:
            openings = [
                f"帮你找到了这首《{name}》- {artists}，快戴上耳机听听看喵～",
                f"网易云传送门来咯！《{name}》- {artists}，请查收喵～",
            ]
            comment_text = random.choice(openings)

        # OneBot 原生音乐卡片紧随文案发送
        return f"{comment_text}\n{cq_code}"
