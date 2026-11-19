"""
music/ — 网易云音乐生态与点歌服务模块
"""
from .netease_client import NetEaseClient
from .playlist_service import PlaylistService

__all__ = ["NetEaseClient", "PlaylistService"]
