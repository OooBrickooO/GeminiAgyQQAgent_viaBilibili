"""
trends/trend_manager.py — 时事热点晨起缓存与热梗动态嗅探服务

包含：
1. 【晨起热搜缓存】：早晨唤醒或启动时拉取今日热搜榜并持久化至 today_hot_trends.json，当天有效复用，杜绝重复请求；
2. 【动态热梗嗅探】：群友聊到新梗或突发时事时，动态组合“百度百科精炼摘要 + B站前排热点视频探讨”，秒懂热梗来龙去脉。
"""
import os
import json
import re
import datetime
import httpx
from typing import List, Dict, Optional
from config import BASE_DIR

HOT_TRENDS_FILE = os.path.join(BASE_DIR, "today_hot_trends.json")


class TrendManager:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    }

    @staticmethod
    def _get_today_str() -> str:
        now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        return now.strftime("%Y-%m-%d")

    @classmethod
    async def fetch_and_cache_morning_trends(cls, force: bool = False) -> List[Dict]:
        """
        拉取并缓存今晨热搜。
        若今日缓存已存在且非强制刷新，则直接读取本地 JSON，零额外网络开销。
        """
        today = cls._get_today_str()

        # 检查本地缓存
        if not force and os.path.exists(HOT_TRENDS_FILE):
            try:
                with open(HOT_TRENDS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("date") == today and data.get("trends"):
                    return data["trends"]
            except Exception:
                pass

        # 动态拉取 B站 实时热搜榜单
        trends = []
        try:
            url = "https://api.bilibili.com/x/web-interface/search/square?limit=15"
            async with httpx.AsyncClient(timeout=6.0, headers=cls.HEADERS) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    raw_items = resp.json().get("data", {}).get("trending", {}).get("list", [])
                    for idx, it in enumerate(raw_items[:10], 1):
                        word = it.get("keyword") or it.get("show_name")
                        if word:
                            trends.append({
                                "rank": idx,
                                "keyword": word,
                                "icon": it.get("icon", "")
                            })
        except Exception as e:
            print(f"[TrendManager] 拉取早间热搜出错: {e}")

        # 若拉取成功则落盘缓存
        if trends:
            payload = {
                "date": today,
                "updated_at": datetime.datetime.now().strftime("%H:%M:%S"),
                "trends": trends
            }
            try:
                with open(HOT_TRENDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[TrendManager] 写入热搜缓存失败: {e}")

        return trends

    @classmethod
    async def get_morning_trends_summary(cls) -> str:
        """
        获取弥酱今晨浏览到的热搜简报，供随口闲聊或被问起时使用
        """
        trends = await cls.fetch_and_cache_morning_trends()
        if not trends:
            return "弥酱今天早上还没来得及看新闻榜单呢喵～"

        lines = [f"【今早热搜榜单 (弥酱晨起浏览记录)】:"]
        for t in trends[:8]:
            lines.append(f"{t['rank']}. {t['keyword']}")
        return "\n".join(lines)

    @classmethod
    async def search_live_meme(cls, keyword: str) -> str:
        """
        动态追查网络新梗或突发时事：
        1. 尝试查询权威百科精炼定义；
        2. 抓取 B站 社区前排热门视频讨论与简介；
        3. 聚合为 150 字以内的精炼核心梗点。
        """
        clean_kw = keyword.strip()
        if not clean_kw:
            return "请输入要查询的梗或时事关键词喵。"

        summary_parts = []

        # 1. 尝试百科核心定义
        try:
            baike_url = f"https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?scope=103&format=json&appid=379020&bk_key={clean_kw}"
            async with httpx.AsyncClient(timeout=4.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                b_resp = await client.get(baike_url)
                if b_resp.status_code == 200:
                    b_data = b_resp.json()
                    abstract = b_data.get("abstract", "").strip()
                    if abstract:
                        # 截取前 100 字
                        clean_abs = re.sub(r'\[\d+\]', '', abstract)[:100]
                        summary_parts.append(f"【基本概述】：{clean_abs}…")
        except Exception:
            pass

        # 2. 抓取 B站 社区实时视频/热梗讨论
        try:
            bili_search_url = f"https://api.bilibili.com/x/web-interface/wbi/search/type?keyword={clean_kw}&search_type=video&page=1"
            async with httpx.AsyncClient(timeout=5.0, headers=cls.HEADERS) as client:
                s_resp = await client.get(bili_search_url)
                if s_resp.status_code == 200:
                    items = s_resp.json().get("data", {}).get("result", [])
                    discussions = []
                    for item in items[:3]:
                        raw_title = item.get("title", "")
                        clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
                        raw_desc = item.get("description", "").strip().replace("\n", " ")
                        if clean_title:
                            desc_snippet = f"（{raw_desc[:30]}…）" if raw_desc else ""
                            discussions.append(f"· {clean_title} {desc_snippet}")
                    if discussions:
                        summary_parts.append("【热议动态与相关讨论】：\n" + "\n".join(discussions))
        except Exception as e:
            print(f"[TrendManager] 搜索 B站 热梗出错: {e}")

        if not summary_parts:
            return f"弥酱在全网搜了一圈，暂时没有找到关于【{clean_kw}】的特别热搜信息喵，可能是一个很小众的圈内梗？"

        return f"🔍 【关于‘{clean_kw}’的实时情报速递】：\n" + "\n\n".join(summary_parts)
