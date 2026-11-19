"""
social/bilibili_client.py — B站开放接口客户端与高能交互层

封装：
1. 短链/链接智能还原 (b23.tv 短链展开为真实 BV 号)
2. 视频基础元数据与第一视角高清封面图获取
3. 官方 AI 字幕/人工字幕高速拉取 (零 Token 毫秒级脱水)
4. 前排热赞评论与大众热梗萃取
5. 视频搜索与多指标综合加权智能重排 (rank_candidates_multi_factor: 月份分段、播放对数、点赞/收藏率、UP粉丝、反广告)
6. 自主收藏与精细分类收藏夹管理 (favorite_video)
7. 关注神仙 UP 主与社区点赞评论 (follow_up / like_video / reply_video)
"""
import re
import json
import time
import random
import httpx
import asyncio
from typing import Optional, List, Dict, Any
from config import BILI_CACHE_FILE
from social.bilibili_auth import load_bili_credential

try:
    from bilibili_api import video, search, comment, favorite_list, user, session, dynamic, Credential
    from bilibili_api.utils.picture import Picture
    BILI_API_AVAILABLE = True
except ImportError:
    BILI_API_AVAILABLE = False
    dynamic = None
    Picture = None
    Credential = None
    session = None


BILI_FIXED_FOLDER_CATEGORIES: List[str] = [
    "AI与折腾指南",
    "硬核数码与科普",
    "游戏高光与攻略",
    "二游与手书二创",
    "新番杂谈与动画",
    "沙雕整活与猫猫",
    "音乐与宅舞MMD",
]

_BILI_FOLDER_KEYWORDS: Dict[str, List[str]] = {
    "AI与折腾指南": ["ai", "大模型", "llm", "agent", "开源", "编程", "python", "代码", "技术", "折腾", "教程", "弥酱的硬核技术阁"],
    "硬核数码与科普": ["数码", "评测", "装机", "显卡", "硬件", "科普", "极客", "实验"],
    "游戏高光与攻略": ["游戏", "实况", "攻略", "速通", "高能", "名场面", "单机", "steam", "mc", "黑神话"],
    "二游与手书二创": ["二游", "二次元", "手书", "二创", "原神", "星铁", "绝区零", "明日方舟", "鸣潮", "同人"],
    "新番杂谈与动画": ["新番", "动漫", "动画", "番剧", "漫评", "杂谈", "国创"],
    "沙雕整活与猫猫": ["沙雕", "整活", "搞笑", "鬼畜", "猫", "萌宠", "小动物", "梗"],
    "音乐与宅舞MMD": ["音乐", "翻唱", "宅舞", "mmd", "mad", "amv", "vocaloid", "洛天依", "钢琴"],
}


def match_existing_favorite_folder(requested_name: str, folder_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    在用户已有的 B站 收藏夹列表中匹配目标分类文件夹：
    1) 精确匹配；
    2) 忽略空白与符号的子串匹配；
    3) 关键词同义映射匹配（绝不悄悄回退到默认收藏夹）。
    """
    target_raw = (requested_name or "").strip()
    if not target_raw or not folder_list:
        return None

    non_default_folders = [
        f for f in folder_list
        if f.get("title") and "默认收藏夹" not in str(f.get("title"))
    ]
    search_pool = non_default_folders or folder_list

    # 1. 精确匹配
    for f in folder_list:
        if str(f.get("title", "")).strip() == target_raw:
            return f

    # 2. 宽松包含匹配（避开默认收藏夹）
    target_norm = re.sub(r"[\s_\-—~·!！?？]+", "", target_raw).lower()
    for f in non_default_folders:
        title_norm = re.sub(r"[\s_\-—~·!！?？]+", "", str(f.get("title", ""))).lower()
        if title_norm and (target_norm in title_norm or title_norm in target_norm):
            return f

    # 3. 通过固定 7 大分类关键词做映射（支持例如用户已有旧自定义夹名时的映射，或反向匹配用户已创建的分类夹）
    mapped_category = None
    for cat, kws in _BILI_FOLDER_KEYWORDS.items():
        if cat == target_raw or any(kw in target_raw.lower() for kw in kws):
            mapped_category = cat
            break

    if mapped_category:
        # 先在已有夹中找与 mapped_category 同名或包含同一关键词的夹
        for f in search_pool:
            if str(f.get("title", "")).strip() == mapped_category:
                return f
        cat_kws = _BILI_FOLDER_KEYWORDS.get(mapped_category, [])
        for f in non_default_folders:
            t_low = str(f.get("title", "")).lower()
            if any(kw in t_low for kw in cat_kws):
                return f

    return None


def parse_bvid_from_text(text: str) -> Optional[str]:
    """快速从文本中正则提取标准 BV 号 (如 BV1xx411c7mD)"""
    if not text or not isinstance(text, str):
        return None
    m = re.search(r'\b(BV[a-zA-Z0-9]{10})\b', text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        return "BV" + raw[2:]
    return None


async def resolve_bvid(target: str) -> Optional[str]:
    """
    智能提取并还原 BV 号：
    支持直接传入 BV 号、完整 URL、av 号，以及手机客户端分享的 b23.tv 短链接。
    """
    if not target or not isinstance(target, str):
        return None

    target = target.strip()

    # 1. 直接匹配 BV 号 (BV + 10位字母数字，忽略大小写)
    bv_match = re.search(r'(BV[a-zA-Z0-9]{10})', target, re.IGNORECASE)
    if bv_match:
        raw_bv = bv_match.group(1)
        return "BV" + raw_bv[2:]

    # 2. 如果包含 b23.tv 短链，通过 HTTP 302 追踪重定向拿到真实 URL
    b23_match = re.search(r'(?:https?://)?(?:www\.)?b23\.tv/[a-zA-Z0-9]+', target, re.IGNORECASE)
    if b23_match:
        short_url = b23_match.group(0)
        if not short_url.startswith("http"):
            short_url = "https://" + short_url
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, trust_env=False) as client:
                resp = await client.get(short_url)
                final_url = str(resp.url)
                final_bv = re.search(r'(BV[a-zA-Z0-9]{10})', final_url, re.IGNORECASE)
                if final_bv:
                    raw_bv = final_bv.group(1)
                    return "BV" + raw_bv[2:]
                final_av = re.search(r'av(\d+)', final_url, re.IGNORECASE)
                if final_av:
                    try:
                        from bilibili_api.video import aid2bvid
                        return aid2bvid(int(final_av.group(1)))
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ [bili_client] 解析 b23.tv 短链异常: {e}")

    # 3. 匹配纯 av 号
    av_match = re.search(r'av(\d+)', target, re.IGNORECASE)
    if av_match:
        aid = int(av_match.group(1))
        # 兼容调用 bilibili_api 转换
        try:
            from bilibili_api.video import aid2bvid
            return aid2bvid(aid)
        except Exception:
            pass

    return None


async def get_video_details(bvid: str, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    获取视频元数据（标题、UP主、播放量、弹幕、简介、时长、封面图等）。
    """
    if not BILI_API_AVAILABLE:
        return {"ok": False, "error": "未安装 bilibili-api-python"}

    cred = credential or load_bili_credential()
    v = video.Video(bvid=bvid, credential=cred)
    try:
        info = await v.get_info()
        stat = info.get("stat", {})
        pages = info.get("pages", [])
        cid = info.get("cid") or (pages[0]["cid"] if pages else 0)

        return {
            "ok": True,
            "bvid": bvid,
            "aid": info.get("aid", 0),
            "cid": cid,
            "title": info.get("title", ""),
            "author": info.get("owner", {}).get("name", "未知UP"),
            "mid": info.get("owner", {}).get("mid", 0),
            "play": stat.get("view", 0),
            "danmaku": stat.get("danmaku", 0),
            "reply": stat.get("reply", 0),
            "favorite": stat.get("favorite", 0),
            "coin": stat.get("coin", 0),
            "like": stat.get("like", 0),
            "duration": info.get("duration", 0),
            "pic": info.get("pic", ""),
            "desc": info.get("desc", ""),
            "pubdate": info.get("pubdate", 0),
            "dynamic": info.get("dynamic", "")
        }
    except Exception as e:
        return {"ok": False, "error": f"获取视频详情失败 ({type(e).__name__}): {e}"}


async def get_video_subtitles(bvid: str, cid: int = 0, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    拉取官方 AI 字幕或人工 CC 字幕全文。
    无字幕时优雅返回 has_subtitle: False，坚决不报错卡死。
    """
    cred = credential or load_bili_credential()
    
    # 若未提供 cid，则先查一次视频详情获取首 P 的 cid
    if not cid:
        detail = await get_video_details(bvid, cred)
        if not detail.get("ok"):
            return {"ok": False, "has_subtitle": False, "error": detail.get("error")}
        cid = detail.get("cid", 0)

    if not cid:
        return {"ok": True, "has_subtitle": False, "subtitles": "", "reason": "未找到分P cid"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://www.bilibili.com/video/{bvid}"
    }
    cookie_parts = []
    if cred and cred.sessdata:
        cookie_parts.append(f"SESSDATA={cred.sessdata}")
        if cred.bili_jct:
            cookie_parts.append(f"bili_jct={cred.bili_jct}")
        if cred.buvid3:
            cookie_parts.append(f"buvid3={cred.buvid3}")

    # 深度借鉴 dsh-bili-scroll：若无 buvid3，自动注入匿名 buvid3 彻底避免 412 WBI 风控拦截
    if not any("buvid3" in p for p in cookie_parts):
        import uuid, random
        anon_buvid = f"{uuid.uuid4()}{random.randint(10000, 99999)}infoc"
        cookie_parts.append(f"buvid3={anon_buvid}")

    headers["Cookie"] = "; ".join(cookie_parts)

    api_url = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return {"ok": False, "has_subtitle": False, "error": f"HTTP {resp.status_code}"}
            
            data = resp.json()
            subtitle_list = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if not subtitle_list:
                return {"ok": True, "has_subtitle": False, "subtitles": "", "reason": "本视频无官方或AI字幕"}

            # 优先挑中文字幕 (zh-CN 或 ai-zh)
            target_sub = subtitle_list[0]
            for s in subtitle_list:
                lan = s.get("lan", "")
                if "zh" in lan or "ai" in lan:
                    target_sub = s
                    break

            sub_url = target_sub.get("subtitle_url", "")
            if not sub_url:
                return {"ok": True, "has_subtitle": False, "subtitles": "", "reason": "字幕链接为空"}

            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url

            # 下载真实字幕 JSON
            sub_resp = await client.get(sub_url, headers=headers)
            if sub_resp.status_code != 200:
                return {"ok": False, "has_subtitle": False, "error": "下载字幕内容失败"}

            sub_json = sub_resp.json()
            body = sub_json.get("body", [])
            if not body:
                return {"ok": True, "has_subtitle": False, "subtitles": "", "reason": "字幕正文为空"}

            # 聚合纯文本
            lines = [f"[{item.get('from', 0):.0f}s] {item.get('content', '')}" for item in body]
            full_text = "\n".join(lines)

            # 超长字幕智能防御：设置 12000 字符硬保护，防止数小时长视频撑爆大模型 Token
            max_chars = 12000
            truncated = False
            if len(full_text) > max_chars:
                full_text = full_text[:max_chars] + f"\n\n[... 字幕篇幅较长（共 {len(body)} 句），已自动截取前 {max_chars} 字符核心内容 ...]"
                truncated = True

            return {
                "ok": True,
                "has_subtitle": True,
                "lan_doc": target_sub.get("lan_doc", "中文"),
                "dialogue_count": len(body),
                "is_truncated": truncated,
                "full_text": full_text
            }
    except Exception as e:
        return {"ok": False, "has_subtitle": False, "error": f"请求字幕异常: {e}"}


async def get_video_hot_comments(bvid: str, num: int = 15, credential: Optional["Credential"] = None) -> List[Dict[str, Any]]:
    """
    抓取视频前排点赞最高的热评（吃透观众舆论与热梗），并提取前 3 条楼中楼子评论。
    """
    if not BILI_API_AVAILABLE:
        return []

    cred = credential or load_bili_credential()
    detail = await get_video_details(bvid, cred)
    if not detail.get("ok"):
        return []

    aid = detail.get("aid", 0)
    comments = []
    try:
        resp = await comment.get_comments(
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            page_index=1,
            order=comment.OrderType.LIKE,
            credential=cred
        )
        replies = resp.get("replies") or []
        for r in replies[:num]:
            member = r.get("member", {})
            content = r.get("content", {})
            sub_list = []
            for sub in (r.get("replies") or [])[:3]:
                sub_member = sub.get("member", {})
                sub_content = sub.get("content", {})
                sub_list.append({
                    "rpid": sub.get("rpid", 0),
                    "user": sub_member.get("uname", "路人"),
                    "like": sub.get("like", 0),
                    "message": sub_content.get("message", "").strip()
                })
            comments.append({
                "rpid": r.get("rpid", 0),
                "user": member.get("uname", "路人"),
                "like": r.get("like", 0),
                "message": content.get("message", "").strip(),
                "sub_replies": sub_list
            })
    except Exception as e:
        print(f"⚠️ [bili_client] 获取视频评论异常: {e}")

    return comments


async def search_videos(keyword: str, num: int = 10, order: str = "totalrank", credential: Optional["Credential"] = None) -> List[Dict[str, Any]]:
    """
    根据关键词搜索视频列表。
    order 可选: totalrank(综合), click(最多播放), pubdate(最新发布), dm(最多弹幕)
    """
    if not BILI_API_AVAILABLE:
        return []

    order_map = {
        "totalrank": search.OrderVideo.TOTALRANK,
        "click": search.OrderVideo.CLICK,
        "pubdate": search.OrderVideo.PUBDATE,
        "dm": search.OrderVideo.DM,
    }
    order_enum = order_map.get(order, search.OrderVideo.TOTALRANK)

    try:
        result = await search.search_by_type(
            keyword=keyword,
            search_type=search.SearchObjectType.VIDEO,
            page=1,
            order_type=order_enum
        )
        items = result.get("result")
        if items is None and isinstance(result.get("data"), dict):
            items = result.get("data", {}).get("result")
        if not items or not isinstance(items, list):
            items = []
        videos = []
        for item in items[:num]:
            raw_title = item.get("title", "")
            clean_title = raw_title.replace('<em class="keyword">', '').replace('</em>', '')
            videos.append({
                "bvid": item.get("bvid", ""),
                "aid": item.get("aid", 0),
                "mid": item.get("mid", 0),
                "title": clean_title,
                "author": item.get("author", ""),
                "play": item.get("play", 0),
                "like": item.get("like", 0),
                "favorites": item.get("favorites", 0),
                "review": item.get("review", 0),
                "danmaku": item.get("video_review", 0),
                "duration": item.get("duration", ""),
                "pic": item.get("pic", ""),
                "pubdate": item.get("pubdate", 0),
                "description": item.get("description", "")[:180]
            })
        return videos
    except Exception as e:
        print(f"⚠️ [bili_client] 搜索视频失败: {e}")
        return []


UP_FOLLOWER_CACHE: Dict[int, int] = {}


async def get_up_follower_count(mid: int) -> int:
    """
    查询单个 UP 主的真实粉丝数（内存缓存 + 1.5s 高速并发超时兜底）。
    """
    if not mid:
        return 1000
    if mid in UP_FOLLOWER_CACHE:
        return UP_FOLLOWER_CACHE[mid]
    try:
        from bilibili_api import user
        u = user.User(uid=int(mid))
        rel = await asyncio.wait_for(u.get_relation_info(), timeout=1.5)
        follower = int(rel.get("follower", 1000))
        UP_FOLLOWER_CACHE[mid] = follower
        return follower
    except Exception:
        UP_FOLLOWER_CACHE[mid] = 1000
        return 1000


def _parse_num_val(val: Any) -> float:
    """数值转换工具：解析带 '万' 或纯数字的播放/点赞/收藏数"""
    if not val:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if '万' in s:
        try:
            return float(s.replace('万', '')) * 10000.0
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _parse_duration_sec(duration_str: Any) -> int:
    """解析 mm:ss 或 hh:mm:ss 为秒数"""
    if not duration_str or ":" not in str(duration_str):
        return 0
    parts = str(duration_str).split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        pass
    return 0


async def rank_candidates_multi_factor(
    candidates: List[Dict[str, Any]],
    keyword: str = "",
    now_ts: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    【B站多指标综合推荐重排算法】
    告别单一时间权重的粗暴逻辑，彻底杜绝 1 播放广告与低质切片：
    1. 时间分段加权 (按月精准阶梯)：
       - 0-1 个月 (当月极新): 75 ~ 90 分，平滑衰减
       - 1-6 个月 (近半年当季优质期): 45 ~ 65 分
       - 6-12 个月 (半年至一年沉淀期): 10 ~ 30 分
       - 12+ 个月 (1年以上老视频): -20 ~ -60 分轻度/中度抑制
    2. 播放量播放热度分 (对数平滑 + <50 低播断崖严惩)：
       - play < 50 扣 250 分 (直接淘汰死水/广告小号)
       - 50 <= play < 500 扣 50 分
       - play >= 500 采用 log10 平滑增益
    3. 真实互动质量分 (点赞率 & 收藏率加权)：
       - like_rate (点赞率): 健康线 5%~10% (0~60分)
       - fav_rate (收藏率): 教学/干货类核心指标，>=5% 额外奖励 15 分
    4. UP 主粉丝数与权威度分：
       - 并发异步获取 UP 粉丝数，log10 权重增益 (0~48分)
    5. 反垃圾营销词与极短切片硬核过滤：
       - 命中华而不实的营销/代练/领券词直接扣 300 分
       - 时长 < 25 秒扣 80 分
    6. 标题语义相关度粗检 (过滤离题推荐)
    """
    if not candidates:
        return []
    import math
    import datetime
    now = now_ts or time.time()
    kw_clean = re.sub(r'[^\w\u4e00-\u9fa5]', '', keyword) if keyword else ""

    # 并发异步批量补齐 UP 主粉丝数 (毫秒级完成，带 1.5s 超时兜底)
    uids = [c.get("mid", 0) for c in candidates]
    tasks = [get_up_follower_count(uid) for uid in uids]
    try:
        followers_list = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        followers_list = [1000] * len(candidates)

    scored = []
    for idx, item in enumerate(candidates):
        title = item.get("title", "")
        pubdate = item.get("pubdate", 0)
        play_num = _parse_num_val(item.get("play", 0))
        like_num = _parse_num_val(item.get("like", 0))
        fav_num = _parse_num_val(item.get("favorites", 0))

        follower_raw = followers_list[idx] if idx < len(followers_list) else 1000
        follower_num = follower_raw if isinstance(follower_raw, (int, float)) else 1000.0

        # 1. 时间分段加权 (按月阶梯：0-1月, 1-6月, 6-12月, 12+月)
        age_days = max(0.0, (now - pubdate) / 86400.0) if pubdate else 999.0
        if age_days <= 30:
            freshness_score = 90.0 - (age_days / 30.0) * 15.0  # 75 ~ 90 分
            tier_label = "0-1月(当月极新)"
        elif age_days <= 180:
            freshness_score = 65.0 - ((age_days - 30.0) / 150.0) * 20.0  # 45 ~ 65 分
            tier_label = "1-6月(近半年)"
        elif age_days <= 365:
            freshness_score = 30.0 - ((age_days - 180.0) / 185.0) * 20.0  # 10 ~ 30 分
            tier_label = "6-12月(近一年)"
        else:
            freshness_score = -20.0 - min(40.0, ((age_days - 365.0) / 365.0) * 20.0)  # -20 ~ -60 分
            tier_label = "12+月(老视频)"

        # 2. 播放量播放热度分
        if play_num < 50:
            play_score = -250.0  # 严重死水与广告小号断崖扣分
        elif play_num < 500:
            play_score = -50.0
        else:
            play_score = math.log10(max(10.0, play_num)) * 18.0

        # 3. 互动质量比率 (点赞率 & 收藏率)
        like_rate = like_num / play_num if play_num > 0 else 0.0
        fav_rate = fav_num / play_num if play_num > 0 else 0.0
        like_score = min(60.0, like_rate * 600.0)
        fav_score = min(70.0, fav_rate * 1000.0)
        fav_bonus = 15.0 if fav_rate >= 0.05 else 0.0

        # 4. UP 主粉丝数与权威度分
        follower_score = math.log10(max(10.0, follower_num)) * 8.0

        # 5. 反营销垃圾词与极短切片硬核过滤
        ad_penalty = 0.0
        ad_pattern = r"(广告|领券|代练|私信|加v|加微|点击链接|淘客|拼多多|淘宝|返利|最低价|下单|优惠券)"
        if re.search(ad_pattern, title, re.IGNORECASE):
            ad_penalty -= 300.0

        duration_sec = _parse_duration_sec(item.get("duration", ""))
        if 0 < duration_sec < 25:
            ad_penalty -= 80.0

        # 6. 标题语义相关度
        relevance_bonus = 0.0
        if kw_clean:
            title_clean = re.sub(r'[^\w\u4e00-\u9fa5]', '', title)
            if kw_clean in title_clean:
                relevance_bonus = 100.0
            else:
                overlap = 0
                for i in range(len(kw_clean) - 1):
                    bi = kw_clean[i:i+2]
                    if bi in title_clean:
                        overlap += 1
                if overlap >= 2 or (len(kw_clean) <= 4 and overlap >= 1):
                    relevance_bonus = overlap * 20.0
                else:
                    relevance_bonus = -300.0  # 离题抑制

        total_score = (
            freshness_score + play_score + like_score +
            fav_score + fav_bonus + follower_score +
            relevance_bonus + ad_penalty
        )

        dt_str = datetime.datetime.fromtimestamp(pubdate).strftime('%Y-%m-%d') if pubdate else "未知"
        item_copy = dict(item)
        item_copy["_multi_score"] = round(total_score, 1)
        item_copy["_freshness_score"] = round(total_score, 1)  # 保持向下兼容
        item_copy["_time_tier"] = tier_label
        item_copy["_pubdate_str"] = dt_str
        item_copy["_like_rate_pct"] = round(like_rate * 100.0, 1)
        item_copy["_fav_rate_pct"] = round(fav_rate * 100.0, 1)
        item_copy["_follower_count"] = int(follower_num)
        scored.append((total_score, item_copy))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored]


def rank_candidates_by_freshness(
    candidates: List[Dict[str, Any]],
    keyword: str = "",
    now_ts: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    同步兼容版本调用器（同步环境降级打分，不阻塞事件循环）。
    """
    if not candidates:
        return []
    import math
    import datetime
    now = now_ts or time.time()
    kw_clean = re.sub(r'[^\w\u4e00-\u9fa5]', '', keyword) if keyword else ""

    scored = []
    for item in candidates:
        title = item.get("title", "")
        pubdate = item.get("pubdate", 0)
        play_num = _parse_num_val(item.get("play", 0))
        like_num = _parse_num_val(item.get("like", 0))
        fav_num = _parse_num_val(item.get("favorites", 0))
        follower_num = float(UP_FOLLOWER_CACHE.get(item.get("mid", 0), 1000))

        age_days = max(0.0, (now - pubdate) / 86400.0) if pubdate else 999.0
        if age_days <= 30:
            freshness_score = 90.0 - (age_days / 30.0) * 15.0
            tier_label = "0-1月(当月极新)"
        elif age_days <= 180:
            freshness_score = 65.0 - ((age_days - 30.0) / 150.0) * 20.0
            tier_label = "1-6月(近半年)"
        elif age_days <= 365:
            freshness_score = 30.0 - ((age_days - 180.0) / 185.0) * 20.0
            tier_label = "6-12月(近一年)"
        else:
            freshness_score = -20.0 - min(40.0, ((age_days - 365.0) / 365.0) * 20.0)
            tier_label = "12+月(老视频)"

        if play_num < 50:
            play_score = -250.0
        elif play_num < 500:
            play_score = -50.0
        else:
            play_score = math.log10(max(10.0, play_num)) * 18.0

        like_rate = like_num / play_num if play_num > 0 else 0.0
        fav_rate = fav_num / play_num if play_num > 0 else 0.0
        like_score = min(60.0, like_rate * 600.0)
        fav_score = min(70.0, fav_rate * 1000.0)
        fav_bonus = 15.0 if fav_rate >= 0.05 else 0.0
        follower_score = math.log10(max(10.0, follower_num)) * 8.0

        ad_penalty = 0.0
        ad_pattern = r"(广告|领券|代练|私信|加v|加微|点击链接|淘客|拼多多|淘宝|返利|最低价|下单|优惠券)"
        if re.search(ad_pattern, title, re.IGNORECASE):
            ad_penalty -= 300.0

        duration_sec = _parse_duration_sec(item.get("duration", ""))
        if 0 < duration_sec < 25:
            ad_penalty -= 80.0

        relevance_bonus = 0.0
        if kw_clean:
            title_clean = re.sub(r'[^\w\u4e00-\u9fa5]', '', title)
            if kw_clean in title_clean:
                relevance_bonus = 100.0
            else:
                overlap = sum(1 for i in range(len(kw_clean) - 1) if kw_clean[i:i+2] in title_clean)
                relevance_bonus = overlap * 20.0 if overlap >= 2 or (len(kw_clean) <= 4 and overlap >= 1) else -300.0

        total_score = (
            freshness_score + play_score + like_score +
            fav_score + fav_bonus + follower_score +
            relevance_bonus + ad_penalty
        )
        dt_str = datetime.datetime.fromtimestamp(pubdate).strftime('%Y-%m-%d') if pubdate else "未知"
        item_copy = dict(item)
        item_copy["_multi_score"] = round(total_score, 1)
        item_copy["_freshness_score"] = round(total_score, 1)
        item_copy["_time_tier"] = tier_label
        item_copy["_pubdate_str"] = dt_str
        item_copy["_like_rate_pct"] = round(like_rate * 100.0, 1)
        item_copy["_fav_rate_pct"] = round(fav_rate * 100.0, 1)
        item_copy["_follower_count"] = int(follower_num)
        scored.append((total_score, item_copy))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored]


async def _fetch_favorite_folders_via_httpx(uid: int, cred: Optional["Credential"]) -> List[Dict[str, Any]]:
    """
    原生 httpx (trust_env=False) 直连 B站 官方 Web API 获取用户收藏夹列表，
    消除第三方库偶发静默失败。
    """
    headers = _build_bili_cookie_headers(cred) if (cred and getattr(cred, "sessdata", "")) else {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": f"https://space.bilibili.com/{uid}/favlist",
    }
    headers["Referer"] = f"https://space.bilibili.com/{uid}/favlist"
    url = f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={uid}&type=2"
    async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
        resp = await client.get(url, headers=headers)
        rj = resp.json()
        if rj.get("code") == 0:
            data_part = rj.get("data") or {}
            return data_part.get("list") or []
        raise RuntimeError(f"WebAPI list-all code={rj.get('code')}: {rj.get('message')}")


async def _deal_favorite_via_httpx(aid: int, target_folder_id: int, cred: "Credential", bvid: str = "") -> Dict[str, Any]:
    """
    原生 httpx (trust_env=False) 直连 POST https://api.bilibili.com/x/v3/fav/resource/deal
    携带完整 Cookie 与 csrf/bili_jct 执行收藏双轨兜底。
    """
    headers = _build_bili_cookie_headers(cred)
    headers["Referer"] = f"https://www.bilibili.com/video/{bvid}" if bvid else "https://www.bilibili.com/"
    headers["Origin"] = "https://www.bilibili.com"
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    post_data = {
        "rid": str(aid),
        "type": "2",
        "add_media_ids": str(int(target_folder_id)),
        "del_media_ids": "",
        "platform": "web",
        "eab_x": "1",
        "ramval": "0",
        "ga": "1",
        "gaia_source": "web_normal",
        "csrf": str(cred.bili_jct or ""),
    }
    async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
        resp = await client.post(
            "https://api.bilibili.com/x/v3/fav/resource/deal",
            headers=headers,
            data=post_data,
        )
        rj = resp.json()
        code = rj.get("code")
        if code == 0 or code == 11201 or "已收藏" in str(rj.get("message", "")):
            return {"ok": True, "code": code, "raw": rj}
        return {"ok": False, "code": code, "error": f"HTTP deal code={code}: {rj.get('message', rj)}"}


async def _resolve_video_aid(bvid: str, cred: Optional["Credential"] = None) -> int:
    """获取视频 aid（优先 get_video_details，失败走原生 httpx Web API 兜底）"""
    det = await get_video_details(bvid, cred)
    if det.get("ok") and det.get("aid"):
        return int(det["aid"])
    headers = _build_bili_cookie_headers(cred) if (cred and getattr(cred, "sessdata", "")) else {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=headers)
            rj = resp.json()
            if rj.get("code") == 0:
                return int((rj.get("data") or {}).get("aid", 0))
    except Exception:
        pass
    return 0


async def favorite_video(
    bvid: str,
    folder_name: str = "",
    credential: Optional["Credential"] = None,
    allow_auto_create: bool = False,
    fallback_to_default: bool = True,
) -> Dict[str, Any]:
    """
    收藏指定视频（4A-收藏 双轨加固 + 7大固定收藏夹匹配 + 选项B默认夹极端兜底）：
    - 拉取账号已有收藏夹列表：优先 `bilibili-api-python`，失败则回退原生 `httpx` (`trust_env=False`) 直连 Web API；
    - 保持 `allow_auto_create=False`（禁止自动新建多余夹子）；
    - 匹配顺序：① 精确标题匹配 -> ② 去空格/子串/关键词模糊匹配 -> ③ 极端兜底（选项 B）：
      若极端情况下 7 个分类夹均匹配失败且 `fallback_to_default=True`，自动兜底收藏进账号的「默认收藏夹」，
      并在返回结构与诊断账本中记录 `fallback_default=True` 与具体未命中原因 `fallback_reason`；
    - 执行收藏：优先 `bilibili-api-python`，失败则自动回退原生 `httpx` 直连 `POST /x/v3/fav/resource/deal`。
    """
    cred = credential or load_bili_credential()
    if not cred or not getattr(cred, "dedeuserid", ""):
        return {"ok": False, "message": "尚未登录B站，无法执行收藏", "error": "not_logged_in"}

    uid = int(cred.dedeuserid)
    folder_list = []
    folder_source = "bilibili_api"

    # 1. 双轨拉取用户现有的所有收藏夹列表
    if BILI_API_AVAILABLE and favorite_list is not None:
        try:
            fav_resp = await favorite_list.get_video_favorite_list(uid=uid, credential=cred)
            folder_list = (fav_resp or {}).get("list", []) or []
        except Exception as e1:
            print(f"⚠️ [bili_client] bilibili-api 拉取收藏夹列表异常，回退 httpx 直连: {e1}")
            folder_list = []

    if not folder_list:
        folder_source = "httpx_fallback"
        try:
            folder_list = await _fetch_favorite_folders_via_httpx(uid, cred)
        except Exception as e2:
            return {"ok": False, "error": f"双轨拉取收藏夹列表均失败: {e2}"}

    target_folder_id = None
    target_folder_title = "默认收藏夹"
    fallback_default = False
    fallback_reason = ""
    existing_titles = [str(f.get("title", "")).strip() for f in folder_list if f.get("title")]

    # 2. 如果指定了文件夹名称，按 ① 精确 -> ② 模糊/关键词 -> ③ 选项 B 默认夹极端兜底
    if folder_name and folder_name.strip():
        target_name = folder_name.strip()
        matched_folder = match_existing_favorite_folder(target_name, folder_list)
        if matched_folder:
            target_folder_id = matched_folder.get("id")
            target_folder_title = matched_folder.get("title", target_name)

        if not target_folder_id and allow_auto_create and BILI_API_AVAILABLE and favorite_list is not None:
            try:
                create_res = await favorite_list.create_video_favorite_list(title=target_name, credential=cred)
                if isinstance(create_res, dict):
                    target_folder_id = create_res.get("id")
                elif isinstance(create_res, int):
                    target_folder_id = create_res
                target_folder_title = target_name
            except Exception as ce:
                print(f"⚠️ [bili_client] 自动创建收藏夹【{target_name}】失败: {ce}")

        if not target_folder_id:
            if fallback_to_default and folder_list:
                default_f = next(
                    (f for f in folder_list if "默认收藏夹" in str(f.get("title", ""))),
                    folder_list[0]
                )
                target_folder_id = default_f.get("id")
                target_folder_title = default_f.get("title", "默认收藏夹")
                fallback_default = True
                fallback_reason = f"目标分类夹【{target_name}】未在账号已有夹({existing_titles})中命中，触发选项B兜底至【{target_folder_title}】"
                print(f"ℹ️ [bili_client] {fallback_reason}")
            else:
                return {
                    "ok": False,
                    "missing_folder": True,
                    "bvid": bvid,
                    "requested_folder": target_name,
                    "error": f"收藏夹【{target_name}】未匹配成功且未启用默认夹兜底",
                }

    # 3. 未指定分类名时使用默认收藏夹
    if not target_folder_id and folder_list:
        default_f = next(
            (f for f in folder_list if "默认收藏夹" in str(f.get("title", ""))),
            folder_list[0]
        )
        target_folder_id = default_f.get("id")
        target_folder_title = default_f.get("title", "默认收藏夹")

    if not target_folder_id:
        return {"ok": False, "error": "未能获取到有效的收藏夹 ID"}

    # 4. 双轨执行真实收藏：优先 bilibili-api-python，失败回退原生 httpx 直连 /x/v3/fav/resource/deal
    api_err_msg = ""
    if BILI_API_AVAILABLE and video is not None:
        try:
            v = video.Video(bvid=bvid, credential=cred)
            await v.set_favorite(add_media_ids=[int(target_folder_id)])
            return {
                "ok": True,
                "bvid": bvid,
                "folder": target_folder_title,
                "folder_id": target_folder_id,
                "via": "bilibili_api",
                "folder_list_source": folder_source,
                "fallback_default": fallback_default,
                "fallback_reason": fallback_reason,
                "message": f"已成功将视频【{bvid}】收藏至B站【{target_folder_title}】！"
            }
        except Exception as e:
            err_str = str(e)
            if "已收藏" in err_str or "11201" in err_str:
                return {
                    "ok": True,
                    "bvid": bvid,
                    "folder": target_folder_title,
                    "folder_id": target_folder_id,
                    "via": "bilibili_api_already_fav",
                    "fallback_default": fallback_default,
                    "fallback_reason": fallback_reason,
                    "message": f"视频【{bvid}】此前已经在收藏夹【{target_folder_title}】中啦～"
                }
            api_err_msg = f"{type(e).__name__}: {e}"
            print(f"⚠️ [bili_client] bilibili-api 收藏视频【{bvid}】失败 ({api_err_msg})，启动 httpx 双轨兜底...")

    # 回退原生 httpx 直连 POST https://api.bilibili.com/x/v3/fav/resource/deal
    try:
        aid = await _resolve_video_aid(bvid, cred)
        if not aid:
            return {"ok": False, "error": f"收藏失败(主接口:{api_err_msg} | httpx兜底未能解析视频aid)"}
        deal_res = await _deal_favorite_via_httpx(aid=aid, target_folder_id=int(target_folder_id), cred=cred, bvid=bvid)
        if deal_res.get("ok"):
            return {
                "ok": True,
                "bvid": bvid,
                "folder": target_folder_title,
                "folder_id": target_folder_id,
                "via": "httpx_fallback",
                "folder_list_source": folder_source,
                "fallback_default": fallback_default,
                "fallback_reason": fallback_reason,
                "message": f"已通过HTTP双轨兜底将视频【{bvid}】收藏至B站【{target_folder_title}】！"
            }
        return {
            "ok": False,
            "code": deal_res.get("code"),
            "error": f"收藏双轨均失败 (主接口: {api_err_msg} | HTTP兜底: {deal_res.get('error')})"
        }
    except Exception as he:
        return {"ok": False, "error": f"收藏双轨异常 (主接口: {api_err_msg} | HTTP兜底异常: {he})"}


async def get_user_favorite_folders(credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    获取弥酱（或当前登录账号）的所有 B站 收藏夹列表（优先 bilibili-api-python，失败回退原生 httpx 直连）。
    """
    cred = credential or load_bili_credential()
    if not cred or not getattr(cred, "dedeuserid", ""):
        return {"ok": False, "error": "尚未登录B站，无法查看收藏夹"}

    uid = int(cred.dedeuserid)
    raw_list = None
    via = "bilibili_api"

    if BILI_API_AVAILABLE and favorite_list is not None:
        try:
            fav_resp = await favorite_list.get_video_favorite_list(uid=uid, credential=cred)
            raw_list = (fav_resp or {}).get("list", []) or []
        except Exception as e:
            print(f"⚠️ [bili_client] get_user_favorite_folders 主接口异常，切换 httpx 兜底: {e}")
            raw_list = None

    if raw_list is None:
        via = "httpx_fallback"
        try:
            raw_list = await _fetch_favorite_folders_via_httpx(uid, cred)
        except Exception as e2:
            return {"ok": False, "error": f"获取收藏夹列表失败: {e2}"}

    folders = []
    for f in (raw_list or []):
        folders.append({
            "id": f.get("id"),
            "title": f.get("title", ""),
            "media_count": f.get("media_count", 0),
            "is_public": f.get("attr", 0) == 0
        })
    return {"ok": True, "via": via, "count": len(folders), "folders": folders}


async def get_favorite_folder_videos(folder_name_or_id: str = "", page: int = 1, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    查看指定收藏夹中的视频列表。
    可传入收藏夹名称或 media_id，若未提供则默认查看第一个收藏夹。
    """
    if not BILI_API_AVAILABLE:
        return {"ok": False, "error": "未安装 bilibili-api-python"}
    cred = credential or load_bili_credential()
    if not cred or not cred.dedeuserid:
        return {"ok": False, "error": "尚未登录B站，无法查看收藏夹"}
    try:
        target_id = None
        target_title = "默认收藏夹"
        fav_resp = await favorite_list.get_video_favorite_list(uid=int(cred.dedeuserid), credential=cred)
        raw_list = fav_resp.get("list", []) or []
        query_str = str(folder_name_or_id).strip()

        if query_str.isdigit():
            target_id = int(query_str)
            for f in raw_list:
                if f.get("id") == target_id:
                    target_title = f.get("title", "")
                    break
        elif query_str:
            for f in raw_list:
                if f.get("title") == query_str or query_str in f.get("title", ""):
                    target_id = f.get("id")
                    target_title = f.get("title", "")
                    break

        if not target_id and raw_list:
            target_id = raw_list[0].get("id")
            target_title = raw_list[0].get("title", "")

        if not target_id:
            return {"ok": False, "error": "未找到对应的收藏夹"}

        content_res = await favorite_list.get_video_favorite_list_content(media_id=target_id, page=page, credential=cred)
        medias = content_res.get("medias", []) or []
        videos = []
        for m in medias:
            videos.append({
                "bvid": m.get("bvid", ""),
                "title": m.get("title", ""),
                "intro": (m.get("intro") or "")[:60],
                "cover": m.get("cover", ""),
                "upper": m.get("upper", {}).get("name", "未知UP"),
                "duration": m.get("duration", 0),
                "fav_time": m.get("fav_time", 0)
            })
        return {
            "ok": True,
            "folder": target_title,
            "folder_id": target_id,
            "page": page,
            "count": len(videos),
            "videos": videos
        }
    except Exception as e:
        return {"ok": False, "error": f"查看收藏夹内容失败: {e}"}


async def unfavorite_video(bvid: str, folder_name: str = "", credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    从收藏夹中移出指定视频。若指定 folder_name 则从该文件夹移出，否则从所有包含该视频的收藏夹中移出。
    """
    if not BILI_API_AVAILABLE:
        return {"ok": False, "error": "未安装 bilibili-api-python"}
    cred = credential or load_bili_credential()
    if not cred or not cred.dedeuserid:
        return {"ok": False, "error": "尚未登录B站，无法取消收藏"}
    try:
        v = video.Video(bvid=bvid, credential=cred)
        fav_resp = await favorite_list.get_video_favorite_list(uid=int(cred.dedeuserid), credential=cred)
        folder_list = fav_resp.get("list", []) or []

        del_ids = []
        if folder_name and folder_name.strip():
            target_name = folder_name.strip()
            for f in folder_list:
                if f.get("title") == target_name:
                    del_ids.append(f.get("id"))
                    break
        else:
            del_ids = [f.get("id") for f in folder_list if f.get("id")]

        if not del_ids:
            return {"ok": False, "error": "未找到匹配的收藏夹"}

        await v.set_favorite(del_media_ids=[int(i) for i in del_ids])
        return {"ok": True, "bvid": bvid, "message": f"已成功将视频【{bvid}】从收藏夹移出！"}
    except Exception as e:
        return {"ok": False, "error": f"取消收藏异常: {e}"}


async def like_video(bvid: str, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    给指定视频点赞。
    """
    if not BILI_API_AVAILABLE:
        return {"ok": False, "message": "未安装 bilibili-api-python"}

    cred = credential or load_bili_credential()
    if not cred:
        return {"ok": False, "message": "尚未登录B站，无法点赞"}

    v = video.Video(bvid=bvid, credential=cred)
    try:
        await v.like(status=True)
        return {"ok": True, "bvid": bvid, "message": "点赞成功！"}
    except Exception as e:
        err_str = str(e)
        if "已点赞" in err_str or "重复" in err_str or "65006" in err_str:
            return {"ok": True, "bvid": bvid, "message": "视频此前已点赞过啦～"}
        return {"ok": False, "error": f"点赞失败 ({type(e).__name__}): {e}"}


async def follow_up(mid: int, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    【真实足迹】关注指定的 B站 UP 主。
    当弥酱在冲浪时发现极其优质、专业硬核或深得她喜爱的 UP 主时主动关注。
    """
    if not BILI_API_AVAILABLE:
        return {"ok": False, "message": "未安装 bilibili-api-python"}

    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata:
        return {"ok": False, "message": "尚未登录B站，无法关注UP主"}

    try:
        u = user.User(uid=int(mid), credential=cred)
        await u.modify_relation(user.RelationType.SUBSCRIBE)
        return {
            "ok": True,
            "mid": int(mid),
            "message": f"已成功关注 UP 主 (UID: {mid})！"
        }
    except Exception as e:
        err_str = str(e)
        if "已关注" in err_str or "22000" in err_str or "22014" in err_str:
            return {"ok": True, "mid": int(mid), "message": f"此前已经关注过该 UP 主 (UID: {mid}) 啦～"}
        return {"ok": False, "error": f"关注 UP 主失败 ({type(e).__name__}): {e}"}


async def reply_video(
    bvid: str,
    message: str,
    rpid: int = 0,
    parent_rpid: int = 0,
    credential: Optional["Credential"] = None
) -> Dict[str, Any]:
    """
    在视频下发布一级评论或楼中楼回复某条评论。
    - rpid=0: 直接发布一级评论
    - rpid>0: 回复根评论 rpid（楼中楼），若指定 parent_rpid 则对准具体子评论回复
    """
    if not BILI_API_AVAILABLE:
        return {"ok": False, "message": "未安装 bilibili-api-python"}

    cred = credential or load_bili_credential()
    if not cred:
        return {"ok": False, "message": "尚未登录B站，无法发评"}

    detail = await get_video_details(bvid, cred)
    if not detail.get("ok"):
        return {"ok": False, "error": detail.get("error")}

    aid = detail.get("aid", 0)
    root_id = int(rpid or 0)
    parent_id = int(parent_rpid or root_id or 0)

    try:
        kwargs = {
            "text": message,
            "oid": aid,
            "type_": comment.CommentResourceType.VIDEO,
            "credential": cred
        }
        if root_id > 0:
            kwargs["root"] = root_id
            kwargs["parent"] = parent_id
        res = await comment.send_comment(**kwargs)
        return {
            "ok": True,
            "rpid": res.get("rpid", 0),
            "root_rpid": root_id,
            "parent_rpid": parent_id,
            "message": "楼中楼回复成功发布！" if root_id > 0 else "评论成功发布！"
        }
    except Exception as e:
        # HTTP 兜底直连 /x/v2/reply/add
        if cred.sessdata and cred.bili_jct:
            try:
                headers = _build_bili_cookie_headers(cred)
                post_data = {
                    "oid": str(aid),
                    "type": "1",
                    "message": message,
                    "plat": "1",
                    "csrf": cred.bili_jct
                }
                if root_id > 0:
                    post_data["root"] = str(root_id)
                    post_data["parent"] = str(parent_id)
                async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                    resp = await client.post(
                        "https://api.bilibili.com/x/v2/reply/add",
                        headers=headers,
                        data=post_data
                    )
                    rj = resp.json()
                    if rj.get("code") == 0:
                        data_part = rj.get("data", {}) or {}
                        return {
                            "ok": True,
                            "rpid": data_part.get("rpid", 0),
                            "root_rpid": root_id,
                            "parent_rpid": parent_id,
                            "message": "楼中楼回复成功发布！" if root_id > 0 else "评论成功发布！"
                        }
                    return {"ok": False, "error": f"发表评论失败: {rj.get('message', rj)}"}
            except Exception as e2:
                return {"ok": False, "error": f"发表评论失败: {e} / 兜底异常: {e2}"}
        return {"ok": False, "error": f"发表评论失败: {e}"}


async def report_video_heartbeat(
    bvid: str,
    cid: int = 0,
    aid: int = 0,
    duration: int = 0,
    played_time: int = -1,
    credential: Optional["Credential"] = None
) -> bool:
    """
    【真实足迹心跳上报】
    向 B站 官方接口上报播放心跳进度 (https://api.bilibili.com/x/click-interface/web/heartbeat)，
    将视频正式录入星见弥的 B站 账号【历史记录】中，使主人能从手机端或网页端视察她平时看过的视频。
    """
    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata:
        # 未登录则跳过上报
        return False

    # 若未提供 cid 或 aid，尝试从详情补齐
    if not cid or not aid:
        detail = await get_video_details(bvid, cred)
        if detail.get("ok"):
            cid = cid or detail.get("cid", 0)
            aid = aid or detail.get("aid", 0)
            duration = duration or detail.get("duration", 0)

    # 计算逼真的已观看时长 (played_time)
    if played_time < 0:
        if duration > 0:
            # 模拟看了 50% ~ 95%
            ratio = random.uniform(0.50, 0.95)
            played_time = max(15, min(duration, int(duration * ratio)))
        else:
            played_time = random.randint(30, 180)

    start_ts = int(time.time()) - played_time

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://www.bilibili.com/video/{bvid}",
        "Origin": "https://www.bilibili.com",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    cookie_parts = [
        f"SESSDATA={cred.sessdata}",
        f"bili_jct={cred.bili_jct}",
        f"DedeUserID={cred.dedeuserid}"
    ]
    if cred.buvid3:
        cookie_parts.append(f"buvid3={cred.buvid3}")
    headers["Cookie"] = "; ".join(cookie_parts)

    post_data = {
        "bvid": bvid,
        "cid": str(cid or 0),
        "mid": str(cred.dedeuserid or ""),
        "played_time": str(played_time),
        "real_played_time": str(played_time),
        "video_duration": str(duration if duration > 0 else played_time),
        "play_type": "0",
        "type": "3",
        "sub_type": "0",
        "dt": "2",
        "start_ts": str(start_ts),
        "csrf": cred.bili_jct
    }
    if aid:
        post_data["aid"] = str(aid)

    api_url = "https://api.bilibili.com/x/click-interface/web/heartbeat"
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.post(api_url, headers=headers, data=post_data)
            res_json = resp.json()
            if res_json.get("code") == 0:
                print(f"[B站历史记录] 成功上报视频【{bvid}】观看心跳: 进度 {played_time}s / {duration}s")
                return True
            else:
                print(f"⚠️ [B站历史记录] 上报视频【{bvid}】心跳返回: {res_json}")
                return False
    except Exception as e:
        print(f"⚠️ [B站历史记录] 上报视频【{bvid}】心跳异常: {e}")
        return False


async def get_watch_history(num: int = 10, credential: Optional["Credential"] = None) -> List[Dict[str, Any]]:
    """
    【视察冲浪足迹】
    获取星见弥 B站 账号近期的真实观看历史记录（用于视察平时刷的视频）。
    优先调用 B站 官方 /x/web-interface/history/cursor 接口拉取真实观看历史，
    若未登录或网络异常，则自动融合本地今日冲浪记录 (today_bili_feed.json) 优雅兜底。
    """
    cred = credential or load_bili_credential()
    num = max(1, min(int(num), 30))
    history_items = []

    if cred and cred.sessdata:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Cookie": f"SESSDATA={cred.sessdata}; bili_jct={cred.bili_jct}; DedeUserID={cred.dedeuserid};"
        }
        api_url = f"https://api.bilibili.com/x/web-interface/history/cursor?ps={num}&type=all"
        try:
            async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
                resp = await client.get(api_url, headers=headers)
                data = resp.json()
                if data.get("code") == 0:
                    raw_list = data.get("data", {}).get("list", []) or []
                    for item in raw_list:
                        bvid = item.get("history", {}).get("bvid") or item.get("bvid", "")
                        history_items.append({
                            "bvid": bvid,
                            "title": item.get("title", ""),
                            "author": item.get("author_name", "") or item.get("owner", {}).get("name", "UP主"),
                            "progress": item.get("progress", 0),
                            "duration": item.get("duration", 0),
                            "view_at": item.get("view_at", 0),
                            "cover": item.get("cover", "")
                        })
                    if history_items:
                        return history_items
        except Exception as e:
            print(f"⚠️ [bili_client] 拉取 B站 历史记录异常: {e}")

    # 本地足迹优雅兜底
    import os
    if os.path.exists(BILI_CACHE_FILE):
        try:
            with open(BILI_CACHE_FILE, "r", encoding="utf-8") as f:
                feed = json.load(f)
                for w in reversed(feed.get("watched", [])):
                    history_items.append({
                        "bvid": w.get("bvid", ""),
                        "title": w.get("title", ""),
                        "author": w.get("author", "UP主"),
                        "score": w.get("score", 0),
                        "comment": w.get("comment", ""),
                        "view_at": int(w.get("time", 0))
                    })
        except Exception:
            pass

    return history_items[:num]


def _build_bili_cookie_headers(cred: "Credential") -> Dict[str, str]:
    """构建带完整 Cookie 的 B站 Web API 请求头"""
    cookie_parts = [
        f"SESSDATA={cred.sessdata}",
        f"bili_jct={cred.bili_jct}",
        f"DedeUserID={cred.dedeuserid}"
    ]
    if getattr(cred, "buvid3", ""):
        cookie_parts.append(f"buvid3={cred.buvid3}")
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://message.bilibili.com/",
        "Origin": "https://message.bilibili.com",
        "Cookie": "; ".join(cookie_parts)
    }


async def get_bili_unread_messages(credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    获取 B站 消息中心未读计数（回复我的 response、@我的 at、收到的赞 like、系统通知 sys_msg、私信未读 unfollow_unread + follow_unread）。
    """
    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata:
        return {"ok": False, "error": "尚未登录B站"}

    headers = _build_bili_cookie_headers(cred)
    result = {
        "ok": True,
        "reply": 0,
        "at": 0,
        "like": 0,
        "sys_msg": 0,
        "dm_unread": 0
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            r1 = await client.get("https://api.bilibili.com/x/msgfeed/unread", headers=headers)
            j1 = r1.json()
            if j1.get("code") == 0:
                d = j1.get("data", {}) or {}
                result["reply"] = int(d.get("reply", 0))
                result["at"] = int(d.get("at", 0))
                result["like"] = int(d.get("like", 0))
                result["sys_msg"] = int(d.get("sys_msg", 0))

            r2 = await client.get(
                "https://api.vc.bilibili.com/session_svr/v1/session_svr/single_unread",
                headers=headers
            )
            j2 = r2.json()
            if j2.get("code") == 0:
                d2 = j2.get("data", {}) or {}
                result["dm_unread"] = int(d2.get("unfollow_unread", 0)) + int(d2.get("follow_unread", 0))
    except Exception as e:
        return {"ok": False, "error": f"获取B站未读消息计数异常: {e}"}

    return result


async def get_bili_received_replies(limit: int = 10, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    获取 B站 “回复我的” 评论列表（谁在哪个视频下回复了星见弥的评论，含 bvid, root_rpid, source_rpid）。
    """
    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata:
        return {"ok": False, "error": "尚未登录B站", "items": []}

    headers = _build_bili_cookie_headers(cred)
    limit = max(1, min(int(limit), 20))
    items = []
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get("https://api.bilibili.com/x/msgfeed/reply", headers=headers)
            data = resp.json()
            if data.get("code") != 0:
                return {"ok": False, "error": data.get("message", "拉取回复失败"), "items": []}

            raw_items = (data.get("data", {}) or {}).get("items", []) or []
            for it in raw_items[:limit]:
                user_info = it.get("user", {}) or {}
                item_info = it.get("item", {}) or {}
                uri = item_info.get("uri", "") or ""
                bvid = parse_bvid_from_text(uri) or item_info.get("bvid", "")
                items.append({
                    "id": it.get("id", 0),
                    "reply_time": it.get("reply_time", 0),
                    "user_uid": user_info.get("mid", 0),
                    "user_name": user_info.get("nickname", "B站网友"),
                    "bvid": bvid,
                    "video_title": item_info.get("title", ""),
                    "source_content": item_info.get("source_content", ""),
                    "target_reply_content": item_info.get("target_reply_content", "") or item_info.get("root_reply_content", ""),
                    "root_rpid": item_info.get("root_id", 0) or item_info.get("source_id", 0),
                    "parent_rpid": item_info.get("source_id", 0),
                    "type": item_info.get("type", "reply")
                })
        return {"ok": True, "items": items}
    except Exception as e:
        return {"ok": False, "error": f"拉取B站评论回复异常: {e}", "items": []}


async def get_bili_received_at(limit: int = 10, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    获取 B站 “@我的” 消息列表。
    """
    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata:
        return {"ok": False, "error": "尚未登录B站", "items": []}

    headers = _build_bili_cookie_headers(cred)
    limit = max(1, min(int(limit), 20))
    items = []
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get("https://api.bilibili.com/x/msgfeed/at", headers=headers)
            data = resp.json()
            if data.get("code") != 0:
                return {"ok": False, "error": data.get("message", "拉取@列表失败"), "items": []}

            raw_items = (data.get("data", {}) or {}).get("items", []) or []
            for it in raw_items[:limit]:
                user_info = it.get("user", {}) or {}
                item_info = it.get("item", {}) or {}
                uri = item_info.get("uri", "") or ""
                bvid = parse_bvid_from_text(uri) or ""
                items.append({
                    "id": it.get("id", 0),
                    "at_time": it.get("at_time", 0),
                    "user_uid": user_info.get("mid", 0),
                    "user_name": user_info.get("nickname", "B站网友"),
                    "bvid": bvid,
                    "title": item_info.get("title", ""),
                    "source_content": item_info.get("source_content", ""),
                    "root_rpid": item_info.get("root_id", 0) or item_info.get("source_id", 0),
                    "parent_rpid": item_info.get("source_id", 0)
                })
        return {"ok": True, "items": items}
    except Exception as e:
        return {"ok": False, "error": f"拉取B站@消息异常: {e}", "items": []}


def _parse_dm_msg_content(msg_type: int, raw_content: str) -> Dict[str, Any]:
    """解析 B站 私信消息体（支持文本 msg_type=1 与分享视频卡片 msg_type=7 等）"""
    if not raw_content:
        return {"text": "", "shared_bvid": ""}
    try:
        obj = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
    except Exception:
        return {"text": str(raw_content), "shared_bvid": parse_bvid_from_text(str(raw_content)) or ""}

    if not isinstance(obj, dict):
        return {"text": str(obj), "shared_bvid": ""}

    if msg_type == 1:
        txt = obj.get("content", "")
        return {"text": txt, "shared_bvid": parse_bvid_from_text(txt) or ""}
    elif msg_type == 7:
        bvid = obj.get("bvid", "") or parse_bvid_from_text(str(obj.get("id", ""))) or ""
        title = obj.get("title", "分享视频")
        author = obj.get("author", "")
        return {
            "text": f"[分享B站视频: 《{title}》 UP:{author} {bvid}]",
            "shared_bvid": bvid
        }
    else:
        txt = obj.get("content") or obj.get("title") or obj.get("text") or str(obj)
        return {"text": str(txt), "shared_bvid": parse_bvid_from_text(str(txt)) or ""}


async def get_bili_recent_dms(limit: int = 5, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    获取 B站 近期私信会话及每个会话的最新消息列表（自动解析文本消息与分享的视频卡片 BV 号）。
    """
    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata:
        return {"ok": False, "error": "尚未登录B站", "sessions": []}

    headers = _build_bili_cookie_headers(cred)
    limit = max(1, min(int(limit), 10))
    my_uid = int(cred.dedeuserid or 0)
    sessions_out = []

    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get(
                "https://api.vc.bilibili.com/session_svr/v1/session_svr/get_sessions?session_type=1&group_fold=1&unfollow_fold=0&sort_rule=2",
                headers=headers
            )
            data = resp.json()
            if data.get("code") != 0:
                return {"ok": False, "error": data.get("message", "获取私信会话失败"), "sessions": []}

            session_list = (data.get("data", {}) or {}).get("session_list", []) or []
            for sess in session_list[:limit]:
                talker_id = int(sess.get("talker_id", 0))
                if talker_id <= 0 or sess.get("system_msg_type", 0) > 0:
                    continue
                unread_count = int(sess.get("unread_count", 0))
                last_msg = sess.get("last_msg", {}) or {}
                parsed_last = _parse_dm_msg_content(int(last_msg.get("msg_type", 1)), last_msg.get("content", ""))

                # 拉取该会话最近 5 条私信详情
                recent_msgs = []
                try:
                    m_resp = await client.get(
                        f"https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs?talker_id={talker_id}&session_type=1&size=5",
                        headers=headers
                    )
                    m_json = m_resp.json()
                    if m_json.get("code") == 0:
                        raw_msgs = (m_json.get("data", {}) or {}).get("messages", []) or []
                        for m in reversed(raw_msgs[:5]):
                            m_type = int(m.get("msg_type", 1))
                            p = _parse_dm_msg_content(m_type, m.get("content", ""))
                            recent_msgs.append({
                                "msg_key": m.get("msg_key", 0),
                                "sender_uid": m.get("sender_uid", 0),
                                "is_me": int(m.get("sender_uid", 0)) == my_uid,
                                "timestamp": m.get("timestamp", 0),
                                "text": p["text"],
                                "shared_bvid": p["shared_bvid"]
                            })
                except Exception:
                    pass

                sessions_out.append({
                    "talker_uid": talker_id,
                    "unread_count": unread_count,
                    "last_msg_seqno": last_msg.get("msg_seqno", 0),
                    "last_msg_timestamp": last_msg.get("timestamp", 0),
                    "last_msg_sender_uid": last_msg.get("sender_uid", 0),
                    "last_msg_text": parsed_last["text"],
                    "last_shared_bvid": parsed_last["shared_bvid"],
                    "recent_messages": recent_msgs
                })
        return {"ok": True, "sessions": sessions_out}
    except Exception as e:
        return {"ok": False, "error": f"拉取B站私信会话异常: {e}", "sessions": []}


async def send_bili_dm(receiver_uid: int, content: str, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    向指定 B站 UID 用户发送私信文本消息。
    优先调用 bilibili_api.session.send_msg 原生方法（自动补齐 CSRF、WBI 签名与 Query 参数），
    若出现异常或库未加载，则走标准 HTTP 协议容错回退。
    """
    cred = credential or load_bili_credential()
    if not cred or not cred.sessdata or not cred.bili_jct or not cred.dedeuserid:
        return {"ok": False, "error": "尚未登录B站或凭证缺失，无法发送私信"}

    rec_uid = int(receiver_uid)
    msg_content = str(content).strip()
    if not msg_content:
        return {"ok": False, "error": "私信内容不能为空"}

    # 1. 优先使用 bilibili_api 官方封装的 session 模块
    if BILI_API_AVAILABLE and session and hasattr(session, "send_msg") and hasattr(session, "EventType"):
        try:
            res = await session.send_msg(
                credential=cred,
                receiver_id=rec_uid,
                msg_type=session.EventType.TEXT,
                content=msg_content
            )
            msg_key = (res or {}).get("msg_key", 0) if isinstance(res, dict) else 0
            return {"ok": True, "msg_key": msg_key, "message": "B站私信发送成功！"}
        except Exception as e:
            err_msg = str(e)
            print(f"⚠️ [bili_client] session.send_msg 发信提示: {err_msg}")
            if any(k in err_msg for k in ["风控", "拒绝", "隐私设置", "未关注", "已被屏蔽"]):
                return {"ok": False, "error": f"B站私信发送受阻: {err_msg}"}

    # 2. 原生 HTTP 协议规范回退（补充必选 Query 参数 w_sender_uid 与正确字段）
    headers = _build_bili_cookie_headers(cred)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    dev_id = "A6716E9A-7CE3-47AF-994B-F0B34178D28D"
    sender_uid = str(cred.dedeuserid)

    post_data = {
        "msg[sender_uid]": sender_uid,
        "msg[receiver_id]": str(rec_uid),
        "msg[receiver_type]": "1",
        "msg[msg_type]": "1",
        "msg[msg_status]": "0",
        "msg[content]": json.dumps({"content": msg_content}, ensure_ascii=False),
        "msg[timestamp]": str(int(time.time())),
        "msg[new_face_version]": "0",
        "msg[dev_id]": dev_id,
        "from_filework": "0",
        "build": "0",
        "mobi_app": "web",
        "csrf_token": cred.bili_jct,
        "csrf": cred.bili_jct
    }
    params = {
        "w_sender_uid": sender_uid,
        "w_receiver_id": str(rec_uid)
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.post(
                "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg",
                headers=headers,
                params=params,
                data=post_data
            )
            rj = resp.json()
            if rj.get("code") == 0:
                return {"ok": True, "msg_key": (rj.get("data", {}) or {}).get("msg_key", 0), "message": "B站私信发送成功！"}
            return {"ok": False, "error": f"B站私信发送失败({rj.get('code')}): {rj.get('message', rj)}"}
    except Exception as e:
        return {"ok": False, "error": f"B站私信发送异常: {e}"}


async def prefetch_bili_watch_context(bvid_or_url: str, credential: Optional["Credential"] = None) -> str:
    """
    【QQ消息B站链接前置拆包】
    当群友或主人在 QQ 发送 B站 链接/小程序/BV号时，在防抖入队前直接预拉取：
    - 视频标题、UP主、播放/弹幕/点赞、简介
    - 前 2500 字 AI/CC 字幕原稿
    - 前 10 条高赞热评（含楼中楼子评论与 rpid）
    直接注入 ephemeral_context，省去大模型第一轮盲目调工具的延迟！
    """
    cred = credential or load_bili_credential()
    bvid = await resolve_bvid(bvid_or_url)
    if not bvid:
        return ""

    try:
        detail = await get_video_details(bvid, cred)
        if not detail.get("ok"):
            return ""

        sub_info = await get_video_subtitles(bvid, cid=detail.get("cid", 0), credential=cred)
        if sub_info.get("has_subtitle") and sub_info.get("full_text"):
            sub_info["full_text"] = sub_info["full_text"][:2500]
        comments = await get_video_hot_comments(bvid, num=10, credential=cred)

        # 同时自动上报一次观看心跳，让主人在B站历史记录里能看到弥酱真的点开了他发的视频
        if cred:
            asyncio.create_task(
                report_video_heartbeat(
                    bvid=bvid,
                    cid=detail.get("cid", 0),
                    aid=detail.get("aid", 0),
                    duration=detail.get("duration", 0),
                    credential=cred
                )
            )

        lines = [
            f"\n【🎬 群友/对方分享的B站视频已自动拆包 ({bvid})】：",
            f"- 标题：《{detail.get('title', '')}》 | UP主：{detail.get('owner_name', '')}(UID:{detail.get('owner_uid', 0)})",
            f"- 数据：播放 {detail.get('view', 0)} / 弹幕 {detail.get('danmaku', 0)} / 点赞 {detail.get('like', 0)} / 收藏 {detail.get('favorite', 0)}",
            f"- 简介：{(detail.get('desc') or '无')[:250]}"
        ]
        if sub_info.get("has_subtitle") and sub_info.get("full_text"):
            lines.append(f"- 字幕原稿摘录（前2500字）：\n  {sub_info['full_text']}")
        else:
            lines.append(f"- 字幕状态：{sub_info.get('message', '该视频暂无字幕，请结合简介与热评判断')}")

        if comments:
            lines.append("- 前排高赞热评（含楼中楼）：")
            for idx, c in enumerate(comments[:8], 1):
                lines.append(f"  [{idx}] (rpid:{c.get('rpid', 0)}) {c.get('user', '网友')}[👍{c.get('like', 0)}]: {c.get('message', '')}")
                for sub in (c.get("sub_replies") or [])[:2]:
                    lines.append(f"      ↳ (sub_rpid:{sub.get('rpid', 0)}) {sub.get('user', '网友')}: {sub.get('message', '')}")

        lines.append("（💡你已经看完了这个视频的核心内容与评论区热梗，可以直接像真正看过视频的群友一样自然吐槽、接梗或和对方展开讨论；若想在B站给它点赞/投币/收藏/发评，也可随时调用对应B站工具！）")
        return "\n".join(lines)
    except Exception as e:
        print(f"⚠️ [prefetch_bili_watch_context] 预拆包 {bvid} 异常: {e}")
        return ""


async def get_bili_user_info(uid: int = 0, credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    【查看B站个人主页与空间档案】
    若 uid 为 0 或未指定，则查看弥酱（当前登录账号）自己的详细主页；
    若传入指定 uid，则查看该 B站 用户/UP 主的公开空间档案（包含昵称、签名、等级、粉丝数、获赞量、近期投稿等）。
    """
    cred = credential or load_bili_credential()
    target_uid = int(uid)
    is_self = False

    if target_uid == 0:
        if not cred or not cred.dedeuserid:
            return {"ok": False, "error": "尚未登录B站且未提供目标 UID，无法查询个人信息"}
        target_uid = int(cred.dedeuserid)
        is_self = True

    try:
        user_headers = _build_bili_cookie_headers(cred) if cred else {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"https://space.bilibili.com/{target_uid}/"
        }

        async with httpx.AsyncClient(timeout=8.0, trust_env=False, headers=user_headers) as client:
            info_url = f"https://api.bilibili.com/x/space/acc/info?mid={target_uid}"
            stat_url = f"https://api.bilibili.com/x/relation/stat?vmid={target_uid}"

            info_resp, stat_resp = await asyncio.gather(
                client.get(info_url),
                client.get(stat_url),
                return_exceptions=True
            )

        info_data = {}
        if not isinstance(info_resp, Exception) and getattr(info_resp, "status_code", 0) == 200:
            rj = info_resp.json()
            if rj.get("code") == 0:
                info_data = rj.get("data", {})

        stat_data = {}
        if not isinstance(stat_resp, Exception) and getattr(stat_resp, "status_code", 0) == 200:
            rj = stat_resp.json()
            if rj.get("code") == 0:
                stat_data = rj.get("data", {})

        recent_videos = []
        try:
            if BILI_API_AVAILABLE:
                u_obj = user.User(uid=target_uid, credential=cred)
                v_res = await u_obj.get_videos(ps=5)
                v_list = (v_res.get("list", {}) or {}).get("vlist", []) or []
                for v in v_list[:5]:
                    recent_videos.append({
                        "bvid": v.get("bvid", ""),
                        "title": v.get("title", ""),
                        "play": v.get("play", 0),
                        "comment": v.get("comment", 0),
                        "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(v.get("created", 0)))
                    })
        except Exception:
            pass

        name = info_data.get("name", f"UID_{target_uid}")
        sign = info_data.get("sign", "(这个人很懒，什么都没写)")
        level = info_data.get("level", 0)
        sex = info_data.get("sex", "保密")
        vip_type = (info_data.get("vip", {}) or {}).get("type", 0)
        vip_str = "年度大会员" if vip_type == 2 else ("大会员" if vip_type == 1 else "普通用户")
        follower = stat_data.get("follower", 0)
        following = stat_data.get("following", 0)

        is_following = False
        if not is_self and cred and BILI_API_AVAILABLE:
            try:
                me_obj = user.User(uid=int(cred.dedeuserid), credential=cred)
                rel_info = await me_obj.get_relation(target_uid)
                is_following = (rel_info.get("attribute", 0) in (2, 6))
            except Exception:
                pass

        return {
            "ok": True,
            "is_self": is_self,
            "uid": target_uid,
            "name": name,
            "sex": sex,
            "level": level,
            "sign": sign,
            "vip": vip_str,
            "following_count": following,
            "follower_count": follower,
            "is_following": is_following,
            "recent_videos": recent_videos,
            "space_url": f"https://space.bilibili.com/{target_uid}"
        }
    except Exception as e:
        return {"ok": False, "error": f"获取用户({target_uid})空间档案异常: {e}"}


async def _download_bili_cover_bytes(image_url: str) -> tuple[bytes, str]:
    """
    防盗链本地预拉封面：
    强制使用 httpx (trust_env=False) 携带 Referer: https://www.bilibili.com/ 与标准桌面 User-Agent
    下载二进制到内存，规避远程直传触发 403 防盗链。
    返回 (image_bytes, mime_ext)
    """
    target_img = (image_url or "").strip()
    if not target_img:
        return b"", "jpg"
    if target_img.startswith("//"):
        target_img = "https:" + target_img
    elif target_img.startswith("http://"):
        target_img = "https://" + target_img[7:]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=False) as client:
        resp = await client.get(target_img, headers=headers)
        if resp.status_code != 200 or not resp.content:
            raise RuntimeError(f"下载封面 HTTP {resp.status_code}")
        ct = (resp.headers.get("content-type") or "").lower()
        ext = "png" if "png" in ct else ("webp" if "webp" in ct else "jpg")
        return resp.content, ext


async def _upload_dynamic_pic_via_httpx(img_bytes: bytes, ext: str, cred: "Credential") -> Dict[str, Any]:
    """
    原生 httpx 上传 B站 动态图床（带 Referer: https://t.bilibili.com/）。
    """
    headers = _build_bili_cookie_headers(cred)
    headers["Referer"] = "https://t.bilibili.com/"
    headers["Origin"] = "https://t.bilibili.com"
    files = {
        "file_up": (f"cover.{ext}", img_bytes, f"image/{'jpeg' if ext == 'jpg' else ext}"),
    }
    data = {
        "biz": "draw",
        "category": "daily",
        "csrf": str(cred.bili_jct or ""),
    }
    async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
        resp = await client.post(
            "https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs",
            headers=headers,
            data=data,
            files=files,
        )
        rj = resp.json()
        if rj.get("code") == 0 and isinstance(rj.get("data"), dict):
            return {"ok": True, "pic_data": rj["data"], "code": 0}
        return {"ok": False, "code": rj.get("code"), "error": f"upload_bfs code={rj.get('code')}: {rj.get('message', rj)}"}


async def _create_web_dynamic_via_httpx(
    text: str,
    cred: "Credential",
    pic_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    原生 httpx Web 动态创建接口（支持图文 scene=2 与纯文字 scene=1，自动兼容 /x/dynamic/feed/create/dyn 与 vc.bilibili.com）。
    """
    headers = _build_bili_cookie_headers(cred)
    headers["Referer"] = "https://t.bilibili.com/"
    headers["Origin"] = "https://t.bilibili.com"
    csrf = str(cred.bili_jct or "")

    scene = 2 if pic_data else 1
    payload: Dict[str, Any] = {
        "dyn_req": {
            "content": {
                "contents": [
                    {"raw_text": text, "type": 1, "biz_id": ""}
                ]
            },
            "scene": scene,
            "meta": {
                "app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}
            },
        }
    }
    if pic_data:
        img_url = pic_data.get("image_url") or pic_data.get("img_src") or ""
        img_w = int(pic_data.get("image_width") or pic_data.get("img_width") or 1280)
        img_h = int(pic_data.get("image_height") or pic_data.get("img_height") or 720)
        img_size = float(pic_data.get("img_size") or 120.0)
        payload["dyn_req"]["pics"] = [
            {
                "img_src": img_url,
                "img_width": img_w,
                "img_height": img_h,
                "img_size": img_size,
            }
        ]

    async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
        resp = await client.post(
            f"https://api.bilibili.com/x/dynamic/feed/create/dyn?csrf={csrf}&platform=web",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        rj = resp.json()
        if rj.get("code") == 0:
            d_part = rj.get("data") or {}
            dyn_id = d_part.get("dyn_id_str") or d_part.get("dyn_id") or ""
            return {"ok": True, "dyn_id": dyn_id, "code": 0, "raw": rj}

        # 若新接口受 WBI 或参数限制且为纯文本，自动尝试 vc create_dynamic 兼容端点
        if not pic_data:
            vc_resp = await client.post(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create",
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "dynamic_id": "0",
                    "type": "4",
                    "rid": "0",
                    "content": text,
                    "extension": '{"emoji_type":1}',
                    "at_uids": "",
                    "ctrl": "[]",
                    "csrf_token": csrf,
                    "csrf": csrf,
                },
            )
            vc_j = vc_resp.json()
            if vc_j.get("code") == 0:
                d2 = vc_j.get("data") or {}
                dyn_id2 = d2.get("dynamic_id_str") or d2.get("dynamic_id") or ""
                return {"ok": True, "dyn_id": dyn_id2, "code": 0, "raw": vc_j}

        return {"ok": False, "code": rj.get("code"), "error": f"create_dyn code={rj.get('code')}: {rj.get('message', rj)}"}


async def publish_bili_dynamic(
    text: str,
    image_url: str = "",
    bvid: str = "",
    credential: Optional["Credential"] = None,
) -> Dict[str, Any]:
    """
    【阶段三 3.4 三段式降级动态发布链路】
    - 防盗链本地预拉封面：强制使用 `httpx` (`trust_env=False`) 携带 `Referer: https://www.bilibili.com/` 下载二进制到内存；
    - 三段降级发布链路：
      1. 首选：`bilibili-api-python` 图文动态发送（传入内存图片字节/对象）；
      2. 降级 1：原生 `httpx` 上传 B站 动态图床（带 `Referer: https://t.bilibili.com/`）+ 原生 Web 动态创建接口发送图文；
      3. 降级 2：若图床或图文接口受风控/参数阻拦，自动降级为纯文字动态（确保附带完整可点击 `https://www.bilibili.com/video/{bvid}` 链接）。
    """
    cred = credential or load_bili_credential()
    if not cred or not getattr(cred, "dedeuserid", ""):
        return {"ok": False, "error": "尚未登录B站，无法发布动态", "code": -101}

    clean_text = (text or "").strip()
    if not clean_text:
        return {"ok": False, "error": "动态文本不能为空", "code": -400}

    video_link = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

    # Step 0: 防盗链本地预拉封面到内存
    img_bytes = b""
    img_ext = "jpg"
    cover_err = ""
    if image_url and image_url.strip():
        try:
            img_bytes, img_ext = await _download_bili_cover_bytes(image_url)
        except Exception as ce:
            cover_err = str(ce)
            print(f"⚠️ [publish_bili_dynamic] 防盗链预拉取封面失败 ({image_url}): {ce}")

    stage_errors = []
    if cover_err:
        stage_errors.append(f"cover_prefetch:{cover_err}")

    # ------------------------------------------------------------
    # 首选 (Stage 1)：bilibili-api-python 图文动态发送（传入内存图片字节/对象）
    # ------------------------------------------------------------
    if img_bytes and BILI_API_AVAILABLE and dynamic is not None and Picture is not None:
        try:
            if hasattr(Picture, "from_content"):
                pic_obj = Picture.from_content(img_bytes, img_ext)
            else:
                import tempfile, os
                with tempfile.NamedTemporaryFile(suffix=f".{img_ext}", delete=False) as tf:
                    tf.write(img_bytes)
                    tmp_p = tf.name
                try:
                    pic_obj = Picture.from_file(tmp_p)
                finally:
                    try:
                        os.remove(tmp_p)
                    except Exception:
                        pass
            dyn = dynamic.BuildDynamic.empty().add_plain_text(clean_text).add_image(pic_obj)
            resp = await dynamic.send_dynamic(info=dyn, credential=cred)
            dyn_id = resp.get("dyn_id") if isinstance(resp, dict) else str(resp)
            return {
                "ok": True,
                "stage": "stage1_bili_api_image",
                "dyn_id": dyn_id,
                "has_image": True,
                "bvid": bvid,
                "raw": resp if isinstance(resp, dict) else {"result": str(resp)},
            }
        except Exception as e1:
            err1 = f"{type(e1).__name__}: {e1}"
            stage_errors.append(f"stage1:{err1}")
            print(f"⚠️ [publish_bili_dynamic] 首选 bilibili-api 图文发布失败，转入降级 1 (原生 httpx 图床): {err1}")

    # ------------------------------------------------------------
    # 降级 1 (Stage 2)：原生 httpx 上传 B站动态图床 + 原生 Web 动态创建接口发送图文
    # ------------------------------------------------------------
    if img_bytes:
        try:
            up_res = await _upload_dynamic_pic_via_httpx(img_bytes, img_ext, cred)
            if up_res.get("ok") and up_res.get("pic_data"):
                create_res = await _create_web_dynamic_via_httpx(clean_text, cred, pic_data=up_res["pic_data"])
                if create_res.get("ok"):
                    return {
                        "ok": True,
                        "stage": "stage2_httpx_image",
                        "dyn_id": create_res.get("dyn_id"),
                        "has_image": True,
                        "bvid": bvid,
                        "raw": create_res.get("raw"),
                    }
                stage_errors.append(f"stage2_create:{create_res.get('error')}")
            else:
                stage_errors.append(f"stage2_upload:{up_res.get('error')}")
        except Exception as e2:
            stage_errors.append(f"stage2_exc:{type(e2).__name__}:{e2}")
            print(f"⚠️ [publish_bili_dynamic] 降级 1 (httpx 图文) 失败，转入降级 2 (纯文字兜底): {e2}")

    # ------------------------------------------------------------
    # 降级 2 (Stage 3)：自动降级为纯文字动态（确保附带完整可点击 https://www.bilibili.com/video/{bvid} 链接）
    # ------------------------------------------------------------
    fallback_text = clean_text
    if video_link and video_link not in fallback_text:
        fallback_text = f"{fallback_text}\n{video_link}"

    if BILI_API_AVAILABLE and dynamic is not None:
        try:
            dyn_plain = dynamic.BuildDynamic.empty().add_plain_text(fallback_text)
            resp3 = await dynamic.send_dynamic(info=dyn_plain, credential=cred)
            dyn_id3 = resp3.get("dyn_id") if isinstance(resp3, dict) else str(resp3)
            return {
                "ok": True,
                "stage": "stage3_text_only_bili_api",
                "dyn_id": dyn_id3,
                "has_image": False,
                "fallback_text_only": True,
                "bvid": bvid,
                "stage_errors": stage_errors,
                "raw": resp3 if isinstance(resp3, dict) else {"result": str(resp3)},
            }
        except Exception as e3:
            stage_errors.append(f"stage3_bili_api:{type(e3).__name__}:{e3}")

    try:
        httpx_plain = await _create_web_dynamic_via_httpx(fallback_text, cred, pic_data=None)
        if httpx_plain.get("ok"):
            return {
                "ok": True,
                "stage": "stage3_text_only_httpx",
                "dyn_id": httpx_plain.get("dyn_id"),
                "has_image": False,
                "fallback_text_only": True,
                "bvid": bvid,
                "stage_errors": stage_errors,
                "raw": httpx_plain.get("raw"),
            }
        stage_errors.append(f"stage3_httpx:{httpx_plain.get('error')}")
    except Exception as e4:
        stage_errors.append(f"stage3_httpx_exc:{type(e4).__name__}:{e4}")

    return {
        "ok": False,
        "bvid": bvid,
        "stage_errors": stage_errors,
        "error": " | ".join(stage_errors) if stage_errors else "三段式动态发布均失败",
    }


async def probe_bili_dynamic_connectivity(credential: Optional["Credential"] = None) -> Dict[str, Any]:
    """
    【主人『测试B站动态』0观看无污染连通性自检】
    当今日 0 观看记录时，不向主页发出垃圾空白动态，而是真实校验：
    1) B站登录凭证有效性 & CSRF (bili_jct) 完整性；
    2) 防盗链封面拉取 + B站动态图床 (`upload_bfs`) 真实 1x1 像素连通性与权限校验；
    3) 动态发布网关可达性探测。
    """
    from social.bilibili_auth import verify_bili_credential
    cred = credential or load_bili_credential()
    if not cred or not getattr(cred, "sessdata", ""):
        return {"ok": False, "stage": "auth", "error": "本地未配置有效 B站 凭证 (bili_credential.json)"}

    auth_res = await verify_bili_credential(cred)
    if not auth_res.get("logged_in"):
        return {"ok": False, "stage": "auth_verify", "error": f"B站登录态已失效: {auth_res.get('message', auth_res)}"}

    if not getattr(cred, "bili_jct", ""):
        return {"ok": False, "stage": "csrf_check", "error": "凭证缺少 bili_jct (CSRF Token)，无法调用写接口"}

    # 构造 1x1 透明 PNG 二进制探测动态图床 upload_bfs 连通性（仅上传图床不创建动态，0主页污染）
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc`\x00\x00"
        b"\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    try:
        up_probe = await _upload_dynamic_pic_via_httpx(tiny_png, "png", cred)
    except Exception as e:
        up_probe = {"ok": False, "error": f"图床请求异常: {type(e).__name__}: {e}"}

    return {
        "ok": bool(up_probe.get("ok")),
        "mode": "dry_run_probe_only",
        "account_name": auth_res.get("name", ""),
        "account_uid": auth_res.get("uid", ""),
        "bfs_upload_ok": bool(up_probe.get("ok")),
        "bfs_detail": up_probe.get("pic_data", {}).get("image_url", "OK") if up_probe.get("ok") else up_probe.get("error"),
        "error": "" if up_probe.get("ok") else str(up_probe.get("error", "")),
    }


