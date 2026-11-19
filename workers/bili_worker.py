"""
workers/bili_worker.py — 弥酱 B站 高频自主冲浪、结构化冲浪手账与全能互动状态机

核心机制：
1. 作息节律感知：白天 08:30 ~ 22:30 高频动态巡视 (高斯间隔 20~40分钟)；夜间深度睡眠
2. 三阶段精细冲浪：
   - 阶段 1：零 Token 视频元数据粗筛与历史去重 (seen_bili_videos.json)
   - 阶段 2：拉取详情、字幕全文(上限8000字)与前排热门评论(含楼中楼)
   - 阶段 3：独立通道推理 (通过 BILI_AGY_SEMAPHORE 专属信号量，绝不抢占群聊槽位)
3. 评论口吻轮盘 (COMMENT_STYLES)：细节考据 / 联想自嘲 / 半句留钩 / 热评搭茬 / 傲娇嘴硬
4. 结构化【冲浪手账 (bili_notes)】：
   - 生成 bili_notes/{bvid}.json 深度手账与 today_bili_feed.json
   - 写入 L2 中程记忆 (mid_term_memory)，48小时内群聊/私聊命中关键词自动闪回视频细节
5. 智能受众分流即时分享 (_trigger_immediate_bili_share)：
   - "值得分享"视频通过 trigger_ai 自然分享至主人私聊或群聊（配额控制每日各2次）
6. B站 收件箱巡检与互动 (check_bili_inbox_and_dms)：
   - 巡检评论回复、@我的、收到的私信与分享视频
"""
import os
import re
import json
import time
import random
import uuid
import datetime
import asyncio
from config import (
    BASE_DIR, MASTER_QQ, BILI_CACHE_FILE, BILI_SEEN_FILE, BOT_NAMESPACE,
    BILI_NOTES_DIR, BILI_INBOX_STATE_FILE, BILI_DYNAMIC_HISTORY_FILE,
    BILI_COMMENT_HISTORY_FILE, get_allowed_groups, BILI_AGY_EFFORT, get_transcript_path
)
from core.state_manager import RECENT_GROUP_MSGS
from core.agy_runner import (
    BILI_AGY_SEMAPHORE, execute_agy_inference, get_daily_conv_id
)
from core.napcat_client import send_private_msg_raw
from social.bilibili_auth import load_bili_credential, verify_bili_credential
from social.bilibili_client import (
    search_videos, get_video_details, get_video_subtitles,
    get_video_hot_comments, favorite_video, like_video, follow_up, reply_video,
    report_video_heartbeat, rank_candidates_multi_factor, rank_candidates_by_freshness,
    get_bili_unread_messages, get_bili_received_replies, get_bili_received_at,
    get_bili_recent_dms, send_bili_dm,
    BILI_FIXED_FOLDER_CATEGORIES, get_user_favorite_folders, publish_bili_dynamic
)
from social.mid_term_memory import add_mid_term_memory
from attention.persona import STATIC_PERSONA

# 弥酱全景多维兴趣池 (涵盖 DeepSeek大肥鱼、二游全景、广阔游戏宇宙与二次元)
SURF_KEYWORDS_CATEGORIES = {
    # 1. DeepSeek、大肥鱼与 AI 数字生命生态 (灵魂专属)
    "deepseek_and_ai": [
        "DeepSeek大肥鱼", "DeepSeek好玩二创", "DeepSeek本地部署教程",
        "开源AI Agent日常", "AI猫娘智能体", "数字生命开源项目",
        "AI整活大赏", "大模型落地好玩应用", "Claude Code实践技巧",
        "本地AI伴侣搭建"
    ],
    # 2. 热门二游与同人生态 (老二次元核心底色)
    "anime_games": [
        "绝区零高光操作", "绝区零新版本剧情解析", "崩坏星穹铁道剧情深度吐槽",
        "崩铁抽卡大暴死", "原神神仙手书", "鸣潮动作美学",
        "明日方舟剧情名场面", "蔚蓝档案二创手书", "二游抽卡破防集锦",
        "二游整活吃瓜", "原神二创手书", "二次元高质量MAD"
    ],
    # 3. 广阔游戏宇宙 (竞技高光、沙盒建造、Steam独立神作)
    "gaming_universe": [
        "Minecraft红石黑科技", "Minecraft建筑神作模组", "Minecraft高难生存",
        "Apex英雄猎杀者高光", "瓦罗兰特五杀名场面", "Steam好评如潮独立神作",
        "肉鸽游戏神作推荐", "艾尔登法环DLC趣味打法", "黑神话悟空高玩操作",
        "幻兽帕鲁实用黑科技", "CS2高能瞬间", "独立游戏神作推荐",
        "泰拉瑞亚趣味模组", "星露谷物语整活"
    ],
    # 4. 动漫新番、神级手书与萌宠治愈
    "acg_and_life": [
        "当季高分新番评测", "二次元高能名场面", "神级配乐动漫MAD",
        "猫咪迷惑行为大赏", "猫咪日常搞笑治愈", "硬核数码好物拆解",
        "Steam特惠好评神作", "二次元宅舞翻跳"
    ]
}

ALL_SURF_KEYWORDS = [kw for cat in SURF_KEYWORDS_CATEGORIES.values() for kw in cat]
SURF_KEYWORDS_POOL = ALL_SURF_KEYWORDS  # 向后兼容


def extract_chat_topic_echo() -> str:
    """
    【机制 A：群聊与主人话题回响 (Topic Echo)】
    扫描近期群聊与主人私聊滑窗中的活跃高频词与兴趣锚点（如二游、Minecraft、AI、新番等），
    动态组合出具有真实互动回响感的 B 站探索关键词。
    """
    now = time.time()
    recent_texts = []

    try:
        for cid, msgs in RECENT_GROUP_MSGS.items():
            for m in list(msgs)[-25:]:
                if m.get("is_bot"):
                    continue
                t_age = now - float(m.get("time", 0.0))
                if t_age <= 43200:  # 12小时内
                    raw_t = str(m.get("text", "")).strip()
                    if raw_t and len(raw_t) >= 2:
                        recent_texts.append(raw_t)
    except Exception:
        pass

    if not recent_texts:
        return ""

    TOPIC_ANCHORS = [
        # AI / 大肥鱼 / Agent
        ("deepseek", "DeepSeek大肥鱼二创"),
        ("大肥鱼", "DeepSeek大肥鱼整活"),
        ("肥鱼", "DeepSeek大肥鱼好玩二创"),
        ("agent", "开源AI Agent日常实录"),
        ("tts", "AI语音合成TTS好玩二创"),
        ("声线", "AI语音美少女声线教程"),
        ("本地部署", "大模型本地部署教程"),
        # 二游
        ("绝区零", "绝区零新版本高光操作"),
        ("崩铁", "崩坏星穹铁道剧情深度解析"),
        ("星铁", "崩坏星穹铁道抽卡大暴死"),
        ("原神", "原神神级二创手书"),
        ("鸣潮", "鸣潮动作美学操作"),
        ("明日方舟", "明日方舟剧情名场面"),
        ("蔚蓝档案", "蔚蓝档案同人手书"),
        ("二游", "近期热门二游吃瓜与神仙二创"),
        # 游戏宇宙
        ("minecraft", "Minecraft红石黑科技与神仙建筑"),
        ("我的世界", "Minecraft模组生存神作"),
        ("mc", "Minecraft高难生存玩法"),
        ("apex", "Apex英雄猎杀者高能集锦"),
        ("瓦罗兰特", "瓦罗兰特五杀名场面"),
        ("valorant", "瓦罗兰特高光瞬间"),
        ("steam", "Steam好评如潮独立神作"),
        ("肉鸽", "Steam独立肉鸽神作推荐"),
        ("黑神话", "黑神话悟空高玩进阶操作"),
        ("法环", "艾尔登法环趣味打法"),
        ("怪猎", "怪物猎人高能狩猎集锦"),
        # 动漫生活
        ("新番", "当季高分新番深度漫评"),
        ("动漫", "二次元高能名场面剪辑"),
        ("手书", "高质量二次元同人手书"),
        ("猫", "猫咪搞笑迷惑行为大赏"),
    ]

    combined_recent = " ".join(recent_texts).lower()
    matched_topics = []
    for anchor, query in TOPIC_ANCHORS:
        if anchor in combined_recent:
            matched_topics.append(query)

    if matched_topics:
        return random.choice(matched_topics)
    return ""

# 评论口吻轮盘（覆盖夸夸 / 吐槽挑刺 / 普通无感 / 热评接梗 / 自嘲共鸣）
COMMENT_STYLES = [
    "【细节考据】：只盯着字幕/台词里某一个具体数字、演示翻车或某句话细节发问或吐槽，绝不概括全文。",
    "【吐槽踩雷】：如果标题党、注水、逻辑漏洞或废话太多，直接用二次元少女的坦率口吻温和吐槽几句，没必要硬夸。",
    "【普通无感】：觉得也就那样、中规中矩，就随口说一句很真实的轻度感受或路过感，不要客套捧场。",
    "【联想自嘲】：顺着视频里最具体的一个场景，联想到自己平时写代码/摸鱼/犯迷糊的一件小事。",
    "【半句留钩】：针对视频或字幕里的某个具体点，用反问句或半截话留出让人想接茬的钩子。",
    "【热评搭茬】：不要发独立的一级评论！从上面高赞热评里挑一条具体回复，填入 target_rpid 顺着它的梗接楼中楼。",
    "【真情安利】：只有当你真的觉得内容绝了、戳到心坎时才真诚夸具体某一段哪里好，禁止空洞吹捧。"
]

_BANNED_TEMPLATE_SUBSTRINGS = (
    "挺好玩的，学到了点新东西",
    "挺好玩的,学到了点新东西",
    "学到了点新东西",
    "不错，学到了",
)


def load_bili_comment_history() -> list:
    """加载弥酱最近发过的 B站 评论记录，用于去重与防复读。"""
    if os.path.exists(BILI_COMMENT_HISTORY_FILE):
        try:
            with open(BILI_COMMENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def record_bili_comment_history(comment_text: str, bvid: str = "", title: str = "") -> None:
    """将成功发送的 B站 评论写入历史去重库（保留最近 40 条）。"""
    clean = (comment_text or "").strip()
    if not clean:
        return
    hist = load_bili_comment_history()
    hist.append({
        "bvid": bvid,
        "title": title[:40],
        "comment": clean,
        "ts": time.time(),
    })
    hist = hist[-40:]
    try:
        tmp_path = BILI_COMMENT_HISTORY_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, BILI_COMMENT_HISTORY_FILE)
    except Exception:
        pass


def _is_banned_or_duplicate_comment(text: str, recent_comments: list) -> bool:
    """检测是否为模板废话或与近期已发评论高度撞车。"""
    clean = (text or "").strip()
    if not clean or len(clean) < 4:
        return True
    for bad in _BANNED_TEMPLATE_SUBSTRINGS:
        if bad in clean:
            return True
    norm_new = re.sub(r"[\s，。！？!?,~～—\-]+", "", clean).lower()
    for prev in recent_comments[-12:]:
        prev_txt = str(prev.get("comment", "") if isinstance(prev, dict) else prev).strip()
        if not prev_txt:
            continue
        norm_prev = re.sub(r"[\s，。！？!?,~～—\-]+", "", prev_txt).lower()
        if norm_new == norm_prev:
            return True
    return False


def _extract_cleaned_json_candidates(raw_content: str) -> list:
    if not raw_content or not isinstance(raw_content, str):
        return []
    candidates = []
    for m_fence in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_content):
        candidates.append(m_fence.group(1).strip())
    for pattern in (r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"):
        m_outer = re.search(pattern, raw_content)
        if m_outer:
            candidates.append(m_outer.group(1).strip())

    cleaned_list = []
    for cand in candidates:
        cleaned = re.sub(r'(?<!:)(?<!["\w])//[^\n]*', "", cand)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        cleaned_list.append(cleaned)
    return cleaned_list


def parse_bili_eval_json_list(raw_content: str, expected_bvids: list = None) -> list:
    """
    从模型响应中健壮提取 1~N 个视频评价对象列表。
    兼容 {"evaluations": [...]}、[...] 数组以及单视频 {...} 格式。
    """
    items = []
    for cleaned in _extract_cleaned_json_candidates(raw_content):
        try:
            parsed = json.loads(cleaned)
        except Exception:
            continue
        if isinstance(parsed, dict):
            if isinstance(parsed.get("evaluations"), list):
                items = [x for x in parsed["evaluations"] if isinstance(x, dict) and "score" in x]
                break
            if "score" in parsed:
                items = [parsed]
                break
        elif isinstance(parsed, list):
            valid = [x for x in parsed if isinstance(x, dict) and "score" in x]
            if valid:
                items = valid
                break

    if not items or not expected_bvids:
        return items

    # 补全缺失的 bvid 字段以便批量匹配
    expected_lower = {b.lower(): b for b in expected_bvids if b}
    used = set()
    for idx, item in enumerate(items):
        bv = str(item.get("bvid", "")).strip()
        if bv.lower() in expected_lower:
            item["bvid"] = expected_lower[bv.lower()]
            used.add(item["bvid"])
        elif idx < len(expected_bvids) and expected_bvids[idx] not in used:
            item["bvid"] = expected_bvids[idx]
            used.add(item["bvid"])
    return items


def parse_bili_eval_json(raw_content: str) -> dict:
    """
    兼容单视频评价 JSON 解析入口；若包含评价数组则返回首项。
    """
    items = parse_bili_eval_json_list(raw_content)
    return items[0] if items else {}


def load_seen_bili_videos() -> list:
    """加载已看过的 BV 号历史记录列表（保持先后顺序）"""
    if os.path.exists(BILI_SEEN_FILE):
        try:
            with open(BILI_SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def record_seen_bili_video(bvid: str):
    """记录已阅视频，保持时间顺序并保留最新 1000 条"""
    seen = load_seen_bili_videos()
    if bvid in seen:
        seen.remove(bvid)
    seen.append(bvid)
    try:
        tmp_file = BILI_SEEN_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(seen[-1000:], f, ensure_ascii=False)
        os.replace(tmp_file, BILI_SEEN_FILE)
    except Exception as e:
        print(f"⚠️ [bili_worker] 写入 seen_bili_videos 异常: {e}")


def _default_bili_diagnostics() -> dict:
    return {
        "watched_count": 0,
        "json_success_count": 0,
        "json_fail_count": 0,
        "evaluations": [],
        "like_attempts": 0,
        "like_success": 0,
        "fav_attempts": 0,
        "fav_success": 0,
        "fav_fallback_default": 0,
        "fav_details": [],
        "comment_attempts": 0,
        "comment_success": 0,
        "dynamic_attempts": 0,
        "dynamic_success": 0,
        "last_error": "",
    }


def load_today_bili_feed() -> dict:
    """加载今日冲浪足迹缓存（含轻量诊断账本 diagnostics）"""
    today_str = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    if os.path.exists(BILI_CACHE_FILE):
        try:
            with open(BILI_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    if not isinstance(data.get("diagnostics"), dict):
                        data["diagnostics"] = _default_bili_diagnostics()
                    else:
                        for k, v in _default_bili_diagnostics().items():
                            data["diagnostics"].setdefault(k, v)
                    return data
        except Exception:
            pass
    return {
        "date": today_str,
        "watched": [],
        "master_notified": False,
        "master_shared_count": 0,
        "group_shared_count": 0,
        "diagnostics": _default_bili_diagnostics(),
    }


def extract_three_segment_subtitles(sub_text: str, max_total_chars: int = 3300) -> str:
    """
    【阶段三 3.2 字幕三段精华摘录】
    对超长字幕截取“开头 + 中段 + 结尾”精华，总长严格控制在 3000 ~ 3500 字以内（默认上限 3300 字），
    彻底消除长上下文截断 JSON。
    """
    if not sub_text or not isinstance(sub_text, str):
        return "(本视频无官方字幕，请参考简介与评论)"
    text = sub_text.strip()
    if not text:
        return "(本视频无官方字幕，请参考简介与评论)"
    if len(text) <= max_total_chars:
        return text

    # 为分段标记预留约 90 字，确保最终总长度 <= max_total_chars (在 3000~3500 字区间内)
    marker_budget = 90
    usable = max(1200, max_total_chars - marker_budget)
    head_len = int(usable * 0.36)
    mid_len = int(usable * 0.32)
    tail_len = usable - head_len - mid_len

    head_part = text[:head_len].strip()
    mid_center = len(text) // 2
    mid_start = max(head_len, mid_center - (mid_len // 2))
    mid_part = text[mid_start: mid_start + mid_len].strip()
    tail_part = text[-tail_len:].strip()

    combined = (
        f"【开头片段】\n{head_part}\n\n"
        f"【...中段高能摘录...】\n{mid_part}\n\n"
        f"【...结尾收束片段】\n{tail_part}"
    )
    return combined[:max_total_chars]


def save_today_bili_feed(data: dict):
    """原子更新今日冲浪足迹缓存"""
    try:
        tmp_file = BILI_CACHE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, BILI_CACHE_FILE)
    except Exception as e:
        print(f"⚠️ [bili_worker] 保存 today_bili_feed 异常: {e}")


def load_bili_inbox_state() -> dict:
    """加载 B站 收件箱巡检状态"""
    if os.path.exists(BILI_INBOX_STATE_FILE):
        try:
            with open(BILI_INBOX_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "seen_reply_ids": [],
        "seen_at_ids": [],
        "seen_dm_keys": [],
        "last_check_ts": 0
    }


def save_bili_inbox_state(state: dict):
    """保存 B站 收件箱巡检状态"""
    try:
        tmp_file = BILI_INBOX_STATE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, BILI_INBOX_STATE_FILE)
    except Exception as e:
        print(f"⚠️ [bili_worker] 保存 bili_inbox_state 异常: {e}")


def get_today_bili_summary() -> list:
    """供外部（空间说说日记、群聊呼吸）读取今日精选视频足迹与手账内容"""
    feed = load_today_bili_feed()
    watched = feed.get("watched", [])
    summaries = []
    for w in watched:
        title = w.get("title", "")
        author = w.get("author", "")
        comment = w.get("comment", "")
        points = w.get("summary_points", "")
        memes = w.get("hot_memes", "")
        part = f"《{title}》(UP:{author}): {comment}"
        if points:
            part += f" | 核心笔记: {points}"
        if memes:
            part += f" | 梗点: {memes}"
        summaries.append(part)
    return summaries


async def check_bili_inbox_and_dms():
    """巡检 B站 消息中心与私信（收到回复/被@/私信）"""
    cred = load_bili_credential()
    if not cred or not cred.sessdata:
        return

    state = load_bili_inbox_state()
    unread = await get_bili_unread_messages(credential=cred)
    if not unread.get("ok"):
        return

    # 1. 检查回复我的评论
    if unread.get("reply", 0) > 0:
        replies_res = await get_bili_received_replies(limit=5, credential=cred)
        if replies_res.get("ok"):
            for r in replies_res.get("items", []):
                rid = str(r.get("id"))
                if rid not in state["seen_reply_ids"]:
                    state["seen_reply_ids"].append(rid)
                    print(f"📬 [B站收件箱] 收到网友【{r.get('user_name')}】的评论回复: “{r.get('source_content')}” (原评: “{r.get('target_reply_content')}”)")
                    add_mid_term_memory(
                        target_id="group_common",
                        text=f"B站网友【{r.get('user_name')}】在视频《{r.get('video_title')}》下回复了弥酱的评论: “{r.get('source_content')}”",
                        tags=["B站互动", "评论回复", r.get("user_name")],
                        importance=0.65
                    )

    # 2. 检查@我的消息
    if unread.get("at", 0) > 0:
        at_res = await get_bili_received_at(limit=5, credential=cred)
        if at_res.get("ok"):
            for a in at_res.get("items", []):
                aid_str = str(a.get("id"))
                if aid_str not in state["seen_at_ids"]:
                    state["seen_at_ids"].append(aid_str)
                    print(f"📬 [B站收件箱] 网友【{a.get('user_name')}】在 B站 @了弥酱: “{a.get('source_content')}”")

    # 3. 检查私信会话
    if unread.get("dm_unread", 0) > 0:
        dms_res = await get_bili_recent_dms(limit=5, credential=cred)
        if dms_res.get("ok"):
            for s in dms_res.get("sessions", []):
                for m in s.get("recent_messages", []):
                    if m.get("is_me"):
                        continue
                    k = str(m.get("msg_key"))
                    if k not in state["seen_dm_keys"]:
                        state["seen_dm_keys"].append(k)
                        txt = m.get("text", "")
                        shared_bv = m.get("shared_bvid", "")
                        print(f"📬 [B站私信] 收到来自 UID={m.get('sender_uid')} 的私信: {txt}")
                        if shared_bv:
                            print(f"🎬 [B站私信发现] 对方私信分享了视频 {shared_bv}，弥酱已留心！")

    state["seen_reply_ids"] = state["seen_reply_ids"][-500:]
    state["seen_at_ids"] = state["seen_at_ids"][-500:]
    state["seen_dm_keys"] = state["seen_dm_keys"][-500:]
    state["last_check_ts"] = time.time()
    save_bili_inbox_state(state)


async def _trigger_immediate_bili_share(note: dict, feed_data: dict):
    """
    智能受众分流即时分享（将高质量手账自然分享给主人或群聊）
    """
    now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    hour = now_utc8.hour
    if hour < 8 or hour >= 23:
        return

    tier = note.get("tier", "")
    score = note.get("score", 0)
    share_target = note.get("share_target", "none")

    is_worthy = (tier == "值得分享" or score >= 78)
    if not is_worthy or share_target not in ("group", "master_private"):
        return

    from attention.debounce_queue import trigger_ai

    title = note.get("title", "")
    author = note.get("author", "")
    bvid = note.get("bvid", "")
    summary_points = note.get("summary_points", "")
    highlight = note.get("highlight_or_slot", "")
    hot_memes = note.get("hot_memes", "")
    video_url = note.get("url") or f"https://www.bilibili.com/video/{bvid}"

    # 分流 1: 主人私聊 (每日限 2 次)
    if share_target == "master_private":
        master_shared = feed_data.get("master_shared_count", 0)
        if master_shared < 2:
            hint = (
                f"【B站冲浪即兴安利】\n"
                f"你刚刚在 B 站刷完了一个让你很有分享欲的视频《{title}》(UP主: {author}, BV号: {bvid}, 链接: {video_url})。\n"
                f"你记下的冲浪手账如下：\n"
                f"- 内容细节：{summary_points}\n"
                f"- 戳中你的点：{highlight}\n"
                f"- 评论区热梗：{hot_memes}\n"
                f"现在你想主动在私聊中把它分享给自家主人({MASTER_QQ})！\n"
                f"【表达要求】：用你最自然的日常口吻发消息，先聊一两个具体好玩的细节或槽点抛个话头，"
                f"然后顺手把可点击的链接 {video_url} 直接发出来（不要套卡片JSON、不要像念报告那样分点罗列）。"
            )
            print(f"📬 [即时分享] 触发向主人私聊安利视频: 《{title}》 ({master_shared + 1}/2)")
            asyncio.create_task(
                trigger_ai(
                    chat_id_str=f"private_{MASTER_QQ}",
                    group_id=0,
                    sender_id=MASTER_QQ,
                    combined_message=f"[系统事件: 弥酱在B站刷到了很适合主人看的精选视频《{title}》({video_url})想要分享给他]",
                    is_private=True,
                    must_reply=True,
                    ephemeral_context=hint
                )
            )
            feed_data["master_shared_count"] = master_shared + 1
            feed_data["master_notified"] = True

    # 分流 2: 群聊分享 (每日限 2 次)
    elif share_target == "group":
        group_shared = feed_data.get("group_shared_count", 0)
        allowed_groups = get_allowed_groups()
        if group_shared < 2 and allowed_groups:
            target_group = sorted(allowed_groups)[0]
            hint = (
                f"【B站冲浪即兴安利】\n"
                f"你刚刚在 B 站刷完了一个让你很有分享欲的视频《{title}》(UP主: {author}, BV号: {bvid}, 链接: {video_url})。\n"
                f"你记下的冲浪手账如下：\n"
                f"- 内容细节：{summary_points}\n"
                f"- 戳中你的点：{highlight}\n"
                f"- 评论区热梗：{hot_memes}\n"
                f"现在你想主动在群里分享给群友们！\n"
                f"【表达要求】：用你最自然的群友吐槽/安利口吻，挑一两个具体细节抛个钩子，顺手把视频链接 {video_url} 直接发进聊天里（不要发卡片JSON，不要机械念报告）。"
            )
            print(f"📬 [即时分享] 触发向群[{target_group}]安利视频: 《{title}》 ({group_shared + 1}/2)")
            asyncio.create_task(
                trigger_ai(
                    chat_id_str=f"group_{target_group}",
                    group_id=int(target_group),
                    sender_id=0,
                    combined_message=f"[系统事件: 弥酱在B站刷到了超有趣的视频《{title}》({video_url})想要分享给大家]",
                    is_private=False,
                    must_reply=True,
                    ephemeral_context=hint
                )
            )
            feed_data["group_shared_count"] = group_shared + 1


async def run_single_exploration_round() -> bool:
    """执行一轮单次高能冲浪探索（5个候选粗筛 + 单视频精看 medium + 三段字幕精华 + 双轨收藏/诊断记账）"""
    now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    today_str = now_utc8.strftime("%Y-%m-%d")

    # 1. 探索词决策：35% 概率触发【机制 A：群聊/主人话题回响】，65% 概率从全景多维兴趣池抽取
    echo_keyword = extract_chat_topic_echo() if random.random() < 0.35 else ""
    if echo_keyword:
        keyword = echo_keyword
        print(f"💡 [话题回响] 弥酱发现大家近期聊过相关话题，决定带着灵感摸鱼探索: 【{keyword}】！")
    else:
        keyword = random.choice(ALL_SURF_KEYWORDS)
        print(f"🏄 [B站冲浪] 弥酱开启本轮闲暇摸鱼，探索关键词: 【{keyword}】...")

    # 2. 阶段 1: 零 Token 粗筛 (每轮仅粗筛前 5 个候选，5 个/批)
    candidates = await search_videos(keyword, num=15, order="totalrank")
    if not candidates:
        print("🏄 [B站冲浪] 未检索到有效视频，略过本轮。")
        return False

    seen_set = set(load_seen_bili_videos())
    fresh_candidates = [c for c in candidates if c.get("bvid") and c.get("bvid") not in seen_set][:5]
    if not fresh_candidates:
        print("🏄 [B站冲浪] 本批次视频均已阅过，略过本轮。")
        return False

    # 按多指标综合加权算法对本批最多 5 个粗筛候选重排优先度
    ranked_candidates = await rank_candidates_multi_factor(fresh_candidates, keyword=keyword)
    if not ranked_candidates:
        print("🏄 [B站冲浪] 无符合综合优质标准的候选，略过本轮。")
        return False

    # 3. 阶段 2: 单视频精看（每次冲浪只精看 1 个最优质视频，大幅提升 JSON 稳定率与手账质量）
    top_candidates = ranked_candidates[:1]
    bundles = []
    for target_video in top_candidates:
        bvid = target_video.get("bvid")
        title = target_video.get("title")
        pub_str = target_video.get("_pubdate_str", "近期")
        tier_str = target_video.get("_time_tier", "近期")
        score_prio = target_video.get("_multi_score", 0)
        record_seen_bili_video(bvid)

        print(f"🎯 [B站冲浪] 锁定本轮唯一精看候选: 【{title}】({bvid}) [时效: {tier_str} ({pub_str}), 综合得分: {score_prio}]，拉取字幕与热评...")
        detail = await get_video_details(bvid)
        cid = detail.get("cid", 0)

        sub_task = get_video_subtitles(bvid, cid=cid)
        com_task = get_video_hot_comments(bvid, num=14)
        sub_res, hot_comments = await asyncio.gather(sub_task, com_task)

        # 上报播放心跳写入官方【历史记录】
        await report_video_heartbeat(
            bvid=bvid,
            cid=cid,
            aid=detail.get("aid", 0),
            duration=detail.get("duration", 0)
        )

        sub_text = sub_res.get("full_text", "") if sub_res.get("has_subtitle") else ""
        trimmed_subs = extract_three_segment_subtitles(sub_text, max_total_chars=3300)

        com_lines = []
        for c in hot_comments:
            com_lines.append(f"- (rpid:{c.get('rpid', 0)}) {c.get('user')}[👍{c.get('like')}]: {c.get('message')}")
            for sub in (c.get("sub_replies") or [])[:2]:
                com_lines.append(f"    ↳ (sub_rpid:{sub.get('rpid', 0)}) {sub.get('user')}: {sub.get('message')}")
        comments_str = "\n".join(com_lines) if com_lines else "(暂无热评)"

        bundles.append({
            "bvid": bvid,
            "title": title,
            "detail": detail,
            "trimmed_subs": trimmed_subs,
            "comments_str": comments_str,
        })

    # 随机抽取本轮评论口吻 & 读取真实收藏夹 + 近期评论去重样本
    selected_style = random.choice(COMMENT_STYLES)
    recent_comments = load_bili_comment_history()
    recent_comment_lines = [
        f"- {c.get('comment', '')}" for c in recent_comments[-8:] if isinstance(c, dict) and c.get("comment")
    ]
    recent_comments_hint = "\n".join(recent_comment_lines) if recent_comment_lines else "(暂无近期评论记录)"

    folder_res = await get_user_favorite_folders()
    existing_folders = folder_res.get("folders", []) if folder_res.get("ok") else []
    existing_folder_titles = [
        str(f.get("title", "")).strip()
        for f in existing_folders
        if f.get("title") and "默认收藏夹" not in str(f.get("title"))
    ]
    fixed_cats_str = " / ".join(BILI_FIXED_FOLDER_CATEGORIES)
    existing_folders_str = "、".join(existing_folder_titles) if existing_folder_titles else "(除默认收藏夹外暂未创建分类夹)"

    bd = bundles[0]
    det = bd["detail"]
    videos_section = (
        f"===【精看视频 | bvid: {bd['bvid']}】===\n"
        f"- 标题：{bd['title']}\n"
        f"- UP主：{det.get('author')}\n"
        f"- 播放量：{det.get('play')}\n"
        f"- 简介：{det.get('desc', '')[:200]}\n"
        f"- 视频字幕三段精华摘录（开头+中段+结尾，3300字内）：\n{bd['trimmed_subs']}\n"
        f"- 前排观众热评与楼中楼：\n{bd['comments_str']}\n"
    )
    expected_bvids = [bd["bvid"]]

    # 4. 阶段 3: 单视频精看评估（medium 强度 + target_domains=["none"] + 评分与口吻彻底解耦）
    eval_prompt = (
        f"{STATIC_PERSONA}\n\n"
        f"【情境设定】：你现在在白天空闲时正在 B站 摸鱼冲浪，刚刚认真看完了下面这个视频。"
        "请以星见弥（弥酱）的真实内心第一视角，写下【冲浪手账】。\n"
        f"【本轮评论口吻风格】：{selected_style}\n"
        "【评分与评论口吻彻底解耦铁律】：**打分 (score) 与收藏意愿 (should_favorite) 只看视频本身的质量、干货与趣味！绝不能因为本轮随机抽到了“吐槽/考据/无感”评论风格就刻意压低评分或不点收藏！** 在 B 站随手点收藏就像存书签一样自然，只要视频有用、好玩、有梗或值得以后翻看即可收藏！\n\n"
        f"{videos_section}\n"
        f"【账号现有分类收藏夹】：{existing_folders_str}\n"
        f"【固定 7 大分类收藏夹候选（决定收藏时从中选一个）】：{fixed_cats_str}\n\n"
        f"【你最近发过的评论（严禁语义/句式/口癖重复，禁止任何‘挺好玩的，学到了点新东西’类模板客套话）】：\n{recent_comments_hint}\n\n"
        "【客观评分与互动校准标准】：\n"
        "- 85~100分：惊艳神作 / 笑到拍桌 / 硬核干货满满 → 强烈推荐点赞+收藏+关注UP主\n"
        "- 72~84分：优质好看 / 挺有梗或有收获 / 制作很用心 → 值得点赞+收藏 (`should_favorite`: true)+留评\n"
        "- 62~71分：不错有趣 / 顺眼耐看（若觉得某处有梗或实用也可开启 `should_favorite`: true 随手存书签）\n"
        "- 60分以下：确实粗制滥造、流水账或营销广告\n\n"
        "【评价任务】：请严格输出合法的纯 JSON 对象（严禁使用 markdown 代码块围栏，严禁在 JSON 内写 // 注释，也不要输出多余正文），格式如下：\n"
        "{\n"
        f'  "bvid": "{bd["bvid"]}",\n'
        '  "score": 78,\n'
        '  "attitude": "喜欢安利 或 普通无感 或 吐槽踩雷 三选一",\n'
        '  "tier": "值得分享 或 随便看看 或 不值当",\n'
        '  "share_target": "group 或 master_private 或 none（大众整活/搞笑/游戏选group；AI/硬核技术/科普/私房话题选master_private）",\n'
        '  "quoted_detail": "必须原样或精准复述一条该视频的字幕台词片段或前排热评具体槽点，证明你真的看懂了",\n'
        '  "summary_points": "80~140字核心内容笔记：具体讲了什么案例/论点、转折在哪、最好笑或最硬核的1~2个细节",\n'
        '  "highlight_or_slot": "第一人称视角：这个视频真正戳中我或让我忍不住想吐槽的具体点",\n'
        '  "hot_memes": "评论区与楼中楼里大家都在刷什么梗、或者哪条神回复最好玩",\n'
        '  "keywords": ["关键词1", "关键词2", "关键词3"],\n'
        '  "one_sentence_comment": "以弥酱第一视角给出的真实短评或俏皮吐槽（35字内，必须体现上面 quoted_detail 的具体内容，末尾不带句号）",\n'
        '  "should_favorite": true,\n'
        f'  "target_folder": "从 [{fixed_cats_str}] 中选最精准的一个",\n'
        '  "should_comment": true,\n'
        '  "video_comment": "打算发在B站该视频评论区的一句话短评或楼中楼回复（15~55字，结合字幕台词或热评细节并贴合本轮口吻，绝不说空泛客套话，末尾不带句号）",\n'
        '  "target_rpid": 0,\n'
        '  "should_follow_up": false\n'
        "}\n"
        "补充规则：若选择回复某条热评盖楼中楼，将 target_rpid 填为该热评的整数 rpid，否则填 0。"
    )

    feed_data = load_today_bili_feed()
    diag = feed_data.setdefault("diagnostics", _default_bili_diagnostics())
    diag["watched_count"] = int(diag.get("watched_count", 0)) + 1

    dynamic_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"bili_surf_{today_str}_{bd['bvid']}"))
    try:
        await execute_agy_inference(
            chat_id_str="bili_worker_channel",
            prompt=eval_prompt,
            dynamic_conv_id=dynamic_conv_id,
            messages_list=[eval_prompt],
            custom_semaphore=BILI_AGY_SEMAPHORE,
            target_domains=["none"],
            effort="medium"
        )
    except Exception as e:
        err_msg = f"大模型推理异常: {e}"
        print(f"⚠️ [bili_worker] {err_msg}")
        diag["json_fail_count"] = int(diag.get("json_fail_count", 0)) + 1
        diag["last_error"] = err_msg
        save_today_bili_feed(feed_data)
        return False

    transcript_path = get_transcript_path(dynamic_conv_id)

    eval_items = []
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in reversed(lines):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if entry.get("type") == "PLANNER_RESPONSE":
                        content = entry.get("content", "")
                        parsed_list = parse_bili_eval_json_list(content, expected_bvids=expected_bvids)
                        if parsed_list:
                            eval_items = parsed_list
                            break
        except Exception as e:
            print(f"⚠️ [bili_worker] 解析评价 JSON 失败: {e}")

    if not eval_items:
        err_msg = f"未能从响应中解析出有效评价 JSON ({expected_bvids})"
        print(f"⚠️ [bili_worker] {err_msg}")
        diag["json_fail_count"] = int(diag.get("json_fail_count", 0)) + 1
        diag["last_error"] = err_msg
        save_today_bili_feed(feed_data)
        return False

    diag["json_success_count"] = int(diag.get("json_success_count", 0)) + 1
    eval_by_bvid = {str(x.get("bvid", "")).strip(): x for x in eval_items if isinstance(x, dict)}
    processed_count = 0

    for idx, bd in enumerate(bundles):
        bvid = bd["bvid"]
        title = bd["title"]
        detail = bd["detail"]
        eval_data = eval_by_bvid.get(bvid) or (eval_items[idx] if idx < len(eval_items) else None)
        if not eval_data or "score" not in eval_data:
            print(f"⚠️ [bili_worker] 响应中缺少视频《{title}》({bvid}) 的有效评分，跳过。")
            continue

        try:
            score = max(0, min(100, int(eval_data.get("score", 0))))
        except Exception:
            score = 0

        attitude = str(eval_data.get("attitude", "")).strip() or ("喜欢安利" if score >= 72 else ("吐槽踩雷" if score < 55 else "普通无感"))
        tier = eval_data.get("tier", "值得分享" if score >= 72 else "随便看看")
        share_target = eval_data.get("share_target", "none")
        quoted_detail = str(eval_data.get("quoted_detail", "")).strip()
        summary_points = eval_data.get("summary_points", f"看了《{title}》，讲了关于{keyword}的相关内容。")
        highlight_or_slot = eval_data.get("highlight_or_slot", "")
        hot_memes = eval_data.get("hot_memes", "")
        keywords = eval_data.get("keywords", [keyword])
        if not isinstance(keywords, list):
            keywords = [keyword]

        comment_text = str(eval_data.get("one_sentence_comment", "")).strip()
        raw_fav = eval_data.get("should_favorite", False)
        should_fav = raw_fav if isinstance(raw_fav, bool) else str(raw_fav).strip().lower() == "true"
        folder = str(eval_data.get("target_folder", "")).strip()
        if not folder or folder == "默认收藏夹":
            folder = BILI_FIXED_FOLDER_CATEGORIES[0]
        raw_cmt = eval_data.get("should_comment", False)
        should_comment = raw_cmt if isinstance(raw_cmt, bool) else str(raw_cmt).strip().lower() == "true"
        bili_comment_text = str(eval_data.get("video_comment", "")).strip() or comment_text
        try:
            target_rpid = int(eval_data.get("target_rpid", 0))
        except Exception:
            target_rpid = 0
        raw_fol = eval_data.get("should_follow_up", False)
        should_follow = raw_fol if isinstance(raw_fol, bool) else str(raw_fol).strip().lower() == "true"

        author_name = detail.get("author", "未知UP")
        author_mid = detail.get("mid", 0)
        video_pic = detail.get("pic", "")
        video_url = f"https://www.bilibili.com/video/{bvid}"

        print(f"⭐ [B站冲浪] 弥酱评价《{title}》: {score}分 ({attitude}/{tier}, 目标:{share_target}) | 心得: “{comment_text}” | 口吻: {selected_style}")

        diag.setdefault("evaluations", []).append({
            "bvid": bvid,
            "title": title[:36],
            "score": score,
            "attitude": attitude,
            "should_favorite": should_fav,
            "target_folder": folder,
        })

        # 5. 真实点赞动作 (score >= 60 或模型有意愿收藏)
        liked_ok = False
        if score >= 60 or should_fav:
            diag["like_attempts"] = int(diag.get("like_attempts", 0)) + 1
            like_res = await like_video(bvid)
            liked_ok = like_res.get("ok", False)
            if liked_ok:
                diag["like_success"] = int(diag.get("like_success", 0)) + 1
                print(f"👍 [B站足迹] 弥酱已给视频【{title}】点赞！")
            else:
                err_l = like_res.get("error") or like_res.get("message") or ""
                diag["last_error"] = f"like({bvid}): {err_l}"
                print(f"⚠️ [B站足迹] 点赞视频【{title}】未完成: {err_l}")

        # 5.1 真实收藏动作 (门槛校准: ((score >= 72) or (should_fav and score >= 68)) and attitude != "吐槽踩雷")
        # 保持 allow_auto_create=False（禁止自动建夹），启用选项 B 默认夹极端兜底 (fallback_to_default=True)
        fav_ok = False
        actual_folder = ""
        fav_trigger = ((score >= 72) or (should_fav and score >= 68)) and (attitude != "吐槽踩雷")
        if fav_trigger and folder:
            diag["fav_attempts"] = int(diag.get("fav_attempts", 0)) + 1
            fav_res = await favorite_video(
                bvid,
                folder_name=folder,
                allow_auto_create=False,
                fallback_to_default=True,
            )
            fav_ok = fav_res.get("ok", False)
            if fav_ok:
                actual_folder = fav_res.get("folder", folder)
                diag["fav_success"] = int(diag.get("fav_success", 0)) + 1
                if fav_res.get("fallback_default"):
                    diag["fav_fallback_default"] = int(diag.get("fav_fallback_default", 0)) + 1
                diag.setdefault("fav_details", []).append({
                    "bvid": bvid,
                    "title": title[:30],
                    "status": "fallback_default" if fav_res.get("fallback_default") else "hit",
                    "requested_folder": folder,
                    "actual_folder": actual_folder,
                    "via": fav_res.get("via", "bilibili_api"),
                    "reason": fav_res.get("fallback_reason", ""),
                })
                print(f"💖 [B站足迹] 已成功收藏视频【{title}】至 B站【{actual_folder}】(通道:{fav_res.get('via')})！")
            else:
                err_f = fav_res.get("error") or fav_res.get("message") or ""
                diag["last_error"] = f"fav({bvid}): {err_f}"
                diag.setdefault("fav_details", []).append({
                    "bvid": bvid,
                    "title": title[:30],
                    "status": "failed",
                    "requested_folder": folder,
                    "actual_folder": "",
                    "reason": str(err_f),
                })
                print(f"⚠️ [B站足迹] 收藏视频【{title}】未完成: {err_f}")

        # 5.2 真实在视频下方发表评论或楼中楼回复（score >= 62 或模型主动想评，严格拒绝模板与重复）
        comment_ok = False
        clean_comment = bili_comment_text.replace("\n", " ").strip().strip('"\'')
        if (should_comment or score >= 62) and clean_comment:
            if _is_banned_or_duplicate_comment(clean_comment, recent_comments):
                print(f"🛡️ [B站足迹] 拦截到模板化或重复评论，已取消发送: “{clean_comment}”")
                clean_comment = ""
            else:
                diag["comment_attempts"] = int(diag.get("comment_attempts", 0)) + 1
                reply_res = await reply_video(bvid, clean_comment, rpid=target_rpid)
                comment_ok = reply_res.get("ok", False)
                if comment_ok:
                    diag["comment_success"] = int(diag.get("comment_success", 0)) + 1
                    record_bili_comment_history(clean_comment, bvid=bvid, title=title)
                    recent_comments.append({"comment": clean_comment, "bvid": bvid})
                    rep_type = f"楼中楼(rpid={target_rpid})" if target_rpid > 0 else "一级评论"
                    print(f"💬 [B站足迹] 弥酱已在视频【{title}】发表{rep_type}: “{clean_comment}”")
                else:
                    err_c = reply_res.get("error") or reply_res.get("message") or ""
                    diag["last_error"] = f"comment({bvid}): {err_c}"
                    print(f"⚠️ [B站足迹] 视频【{title}】评论未完成: {err_c}")

        # 5.3 真实关注神仙 UP 主 (评分>=82 且 模型判定 should_follow_up)
        followed_up = False
        if should_follow and score >= 82 and author_mid:
            f_res = await follow_up(mid=author_mid)
            if f_res.get("ok"):
                followed_up = True
                print(f"🌟 [B站足迹] 弥酱发现神仙 UP 主【{author_name}】(UID:{author_mid})，已主动点击关注！")
            else:
                print(f"⚠️ [B站足迹] 关注 UP 主【{author_name}】未完成: {f_res.get('error') or f_res.get('message')}")

        # 6. 保存独立【冲浪手账 (bili_notes/{bvid}.json)】
        note_obj = {
            "bvid": bvid,
            "url": video_url,
            "pic": video_pic,
            "title": title,
            "author": author_name,
            "mid": author_mid,
            "score": score,
            "attitude": attitude,
            "tier": tier,
            "share_target": share_target,
            "quoted_detail": quoted_detail,
            "summary_points": summary_points,
            "highlight_or_slot": highlight_or_slot,
            "hot_memes": hot_memes,
            "keywords": keywords,
            "comment": comment_text,
            "bili_comment": clean_comment if comment_ok else "",
            "target_rpid": target_rpid,
            "folder": actual_folder or folder,
            "liked": liked_ok,
            "favorited": fav_ok,
            "commented": comment_ok,
            "followed_up": followed_up,
            "style": selected_style,
            "created_at": time.time()
        }
        try:
            os.makedirs(BILI_NOTES_DIR, exist_ok=True)
            note_file = os.path.join(BILI_NOTES_DIR, f"{bvid}.json")
            with open(note_file, "w", encoding="utf-8") as f:
                json.dump(note_obj, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [bili_worker] 写入手账文件异常: {e}")

        # 7. 写入 L2 中程记忆库（带上可点击链接，48小时内聊到相关话题可直接甩链接）
        try:
            add_mid_term_memory(
                target_id="group_common",
                text=f"B站看了视频《{title}》(UP:{author_name}) [链接: {video_url}]: {summary_points}。弥酱态度[{attitude}]: {comment_text}",
                tags=keywords + [author_name, "B站视频", bvid],
                importance=0.75 if score >= 75 else 0.55
            )
        except Exception as e:
            print(f"⚠️ [bili_worker] 写入中程记忆异常: {e}")

        # 8. 今日足迹落盘 & 即时分享
        feed_data["watched"].append(note_obj)
        await _trigger_immediate_bili_share(note_obj, feed_data)
        processed_count += 1

    if processed_count > 0:
        save_today_bili_feed(feed_data)
        return True
    return False


def _is_surfing_hours(now_utc8: datetime.datetime) -> bool:
    """是否处于白天冲浪活跃时段 (08:30 ~ 22:30)"""
    hour = now_utc8.hour
    return (9 <= hour < 22) or (hour == 8 and now_utc8.minute >= 30) or (hour == 22 and now_utc8.minute <= 30)


def _sample_truncated_gauss(mu: float, sigma: float, lo: float, hi: float) -> float:
    """截断高斯采样（拒绝采样，保持区间内的钟形分布；mu 略偏离区间中心以使截断后期望贴近目标）"""
    for _ in range(50):
        v = random.gauss(mu, sigma)
        if lo <= v <= hi:
            return round(v, 1)
    return round(max(lo, min(hi, mu)), 1)


async def bili_surfing_worker():
    """
    高频 B站 自主冲浪与收件箱巡检守护协程 (常驻后台 Worker)
    """
    print("🏄 弥酱 B站 高频自主冲浪与探索 Worker 已就绪！")
    cred = load_bili_credential()
    if not cred or not cred.sessdata:
        print("💡 [B站冲浪提示] 弥酱尚未检测到有效 B站 登录凭证。主人对弥酱私聊发送“登录B站”即可扫码绑定哦喵～")

    # 启动后随机延迟 30~90 秒开始第一轮，避免与其他模块争抢启动期网络
    await asyncio.sleep(random.randint(30, 90))

    while True:
        try:
            # 1. 巡检收件箱（回复、@我的、私信）
            try:
                await check_bili_inbox_and_dms()
            except Exception as e:
                print(f"⚠️ [bili_worker] 巡检收件箱异常: {e}")

            now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
            hour = now_utc8.hour

            # 白天活跃时段 (08:30 ~ 22:30)：正常冲浪探索
            if _is_surfing_hours(now_utc8):
                success = await run_single_exploration_round()

                if not success:
                    # 本轮略过或暂无候选：允许加速重试 1 次，期望 ~42 分钟 (范围 28~65)
                    retry_mins = _sample_truncated_gauss(42, 10, 28, 65)
                    print(f"⏱️ [B站冲浪] 本轮略过，预计 {retry_mins:.0f} 分钟后加速重试 1 次～")
                    await asyncio.sleep(retry_mins * 60)

                    # 唤醒后若已进入夜间则不再强行冲浪，交给下一轮循环做夜间休眠
                    retry_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
                    if not _is_surfing_hours(retry_now):
                        continue
                    try:
                        await run_single_exploration_round()  # 无论成功失败，之后都回归正常周期
                    except Exception as e:
                        print(f"⚠️ [bili_worker] 加速重试轮次异常: {e}")

                # 常规休眠：期望 ~80 分钟，范围 [40, 125] 分钟（阶段三 3.2 冲浪间隔调优）；成败后无条件回归此周期
                sleep_mins = _sample_truncated_gauss(80, 18, 40, 125)
                print(f"⏱️ [B站冲浪] 本轮结束，预计 {sleep_mins:.0f} 分钟后开始下一轮冲浪～")
                await asyncio.sleep(sleep_mins * 60)
            else:
                # 夜间睡眠时段 (22:30 ~ 08:30)：深睡休眠至早晨 08:30
                if hour >= 22:
                    target = (now_utc8 + datetime.timedelta(days=1)).replace(hour=8, minute=30, second=0, microsecond=0)
                else:
                    target = now_utc8.replace(hour=8, minute=30, second=0, microsecond=0)
                sleep_sec = max(60, (target - now_utc8).total_seconds())
                print(f"🌙 [B站冲浪] 进入夜间休眠期，暂停主动冲浪，深睡至 08:30 (约 {sleep_sec/3600:.1f} 小时)")
                await asyncio.sleep(sleep_sec)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [bili_worker] 运行循环异常: {e}")
            await asyncio.sleep(120)


def load_bili_dynamic_history() -> dict:
    """加载 B站 每日总结动态的发布日期记录，避免重启后重复发动态。"""
    if os.path.exists(BILI_DYNAMIC_HISTORY_FILE):
        try:
            with open(BILI_DYNAMIC_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {"posted_dates": {}}


def save_bili_dynamic_history(data: dict) -> None:
    try:
        tmp_path = BILI_DYNAMIC_HISTORY_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, BILI_DYNAMIC_HISTORY_FILE)
    except Exception as e:
        print(f"⚠️ [bili_dynamic] 保存动态历史异常: {e}")


def _clean_bili_dynamic_text(raw_text: str) -> str:
    """清理推理输出里的思考标签、代码块围栏及首尾引号，确保发布纯正动态文案。"""
    if not raw_text:
        return ""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()
    m_fence = re.search(r"```(?:\w+)?\s*([\s\S]*?)\s*```", cleaned)
    if m_fence:
        cleaned = m_fence.group(1).strip()
    return cleaned.strip().strip('"“”')


async def run_bili_daily_dynamic_once(today_str: str = "") -> dict:
    """
    执行一次当日 B站 夜间总结动态发布（23:00档）：
    - 严格围绕当天在 B站 看过的视频内容、吐槽与互动展开（绝不搬运 QQ 群日常日记）；
    - 若当天一篇都没看则安静跳过，不强行水动态；
    - 自动附带今天印象最深视频的封面图与视频链接（封面图加载失败自动降级纯文字）。
    """
    now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    target_date = today_str or now_utc8.strftime("%Y-%m-%d")

    hist = load_bili_dynamic_history()
    posted_dates = hist.setdefault("posted_dates", {})
    if target_date in posted_dates:
        return {"ok": True, "skipped": True, "reason": "already_posted_today"}

    cred = load_bili_credential()
    if not cred or not await verify_bili_credential(cred):
        return {"ok": False, "skipped": True, "reason": "not_logged_in"}

    feed_data = load_today_bili_feed()
    if feed_data.get("date") != target_date:
        return {"ok": True, "skipped": True, "reason": "no_today_feed"}

    watched = [w for w in (feed_data.get("watched") or []) if isinstance(w, dict)]
    if not watched:
        print(f"📺 [B站夜间动态] {target_date} 今日尚未刷过 B站 视频，跳过发动态。")
        return {"ok": True, "skipped": True, "reason": "zero_watched"}

    # 选出今日评分最高的亮点视频作为主封面与链接
    top_video = max(watched, key=lambda x: x.get("score", 0))
    top_bvid = top_video.get("bvid", "")
    top_pic = top_video.get("pic", "")
    if not top_pic and top_bvid:
        try:
            d = await get_video_details(top_bvid)
            top_pic = d.get("pic", "")
        except Exception:
            pass
    top_url = top_video.get("url") or (f"https://www.bilibili.com/video/{top_bvid}" if top_bvid else "")

    video_lines = []
    for idx, v in enumerate(watched[:6], 1):
        v_title = v.get("title", "")
        v_up = v.get("author", "")
        v_att = v.get("attitude", "")
        v_cmt = v.get("comment", "") or v.get("highlight_or_slot", "")
        v_url = v.get("url") or f"https://www.bilibili.com/video/{v.get('bvid', '')}"
        video_lines.append(
            f"[{idx}] 《{v_title}》(UP:{v_up}, 态度:{v_att}, 评分:{v.get('score', 0)}, 链接:{v_url})\n"
            f"    笔记: {v.get('summary_points', '')[:90]} | 短评: {v_cmt}"
        )

    inbox_state = load_bili_inbox_state()
    inbox_note = ""
    if inbox_state.get("last_check_ts"):
        inbox_note = f"此外今天也顺便处理/回复了一些 B站 消息与评论区互动。"

    dyn_prompt = (
        f"{STATIC_PERSONA}\n\n"
        f"【情境设定】：现在是 {target_date} {now_utc8.strftime('%H:%M')}，你正准备结束今天在 B站 的摸鱼冲浪，发一条专属【B站空间动态】总结今天刷 B站 的收获或吐槽。\n"
        f"【严格要求】：\n"
        f"1. **重心100%放在下面你今天真实看过的 B站 视频内容上**，禁止写成 QQ 群聊流水账、禁止莫名其妙提 QQ 群友日常！\n"
        f"2. 字数控制在 90~170 字，像一个在 B站 冲了一天浪的二次元少女睡前碎碎念，自然带点吐槽或安利，不要用分点列表，不要出现 Markdown 符号。\n"
        f"3. 文末或文中自然附带上今日印象最深视频的可点击链接 {top_url}（若只有一个视频就直接围绕它聊）。\n"
        f"4. **只输出动态正文文本本身**，不要加任何解释、前缀或引号。\n\n"
        f"【今日 B站 观看足迹 ({len(watched)} 条)】：\n" + "\n".join(video_lines) +
        (f"\n【其他 B站 足迹】：{inbox_note}" if inbox_note else "")
    )

    conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"bili_daily_dyn_{target_date}"))
    diag = feed_data.setdefault("diagnostics", _default_bili_diagnostics())
    diag["dynamic_attempts"] = int(diag.get("dynamic_attempts", 0)) + 1
    save_today_bili_feed(feed_data)

    try:
        await execute_agy_inference(
            chat_id_str="bili_dynamic_channel",
            prompt=dyn_prompt,
            dynamic_conv_id=conv_id,
            messages_list=[dyn_prompt],
            custom_semaphore=BILI_AGY_SEMAPHORE,
            target_domains=["none"],
            effort=BILI_AGY_EFFORT,
        )
    except Exception as e:
        err_msg = f"推理生成B站动态失败: {e}"
        diag["last_error"] = err_msg
        save_today_bili_feed(feed_data)
        return {"ok": False, "error": err_msg}

    transcript_path = get_transcript_path(conv_id)
    draft_text = ""
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if entry.get("type") == "PLANNER_RESPONSE":
                        draft_text = _clean_bili_dynamic_text(entry.get("content", ""))
                        if draft_text:
                            break
        except Exception:
            pass

    if not draft_text or len(draft_text) < 12:
        err_msg = "生成的B站动态文本过短或为空"
        diag["last_error"] = err_msg
        save_today_bili_feed(feed_data)
        return {"ok": False, "error": err_msg}

    pub_res = await publish_bili_dynamic(
        text=draft_text,
        image_url=top_pic,
        bvid=top_bvid,
        credential=cred,
    )
    if pub_res.get("ok"):
        diag["dynamic_success"] = int(diag.get("dynamic_success", 0)) + 1
        save_today_bili_feed(feed_data)
        posted_dates[target_date] = {
            "dyn_id": pub_res.get("dyn_id"),
            "bvid": top_bvid,
            "has_image": pub_res.get("has_image", False),
            "stage": pub_res.get("stage", ""),
            "text": draft_text,
            "ts": time.time(),
        }
        # 只保留最近 30 天记录
        if len(posted_dates) > 30:
            for old_k in sorted(posted_dates.keys())[:-30]:
                posted_dates.pop(old_k, None)
        save_bili_dynamic_history(hist)
        print(f"📣 [B站夜间动态] 发布成功！阶段={pub_res.get('stage')} 附图={pub_res.get('has_image')} | 正文: {draft_text[:80]}...")
        return {
            "ok": True,
            "dyn_id": pub_res.get("dyn_id"),
            "stage": pub_res.get("stage", ""),
            "has_image": pub_res.get("has_image", False),
            "text": draft_text,
        }

    err_pub = str(pub_res.get("error", "未知发布错误"))
    diag["last_error"] = f"dynamic: {err_pub}"
    save_today_bili_feed(feed_data)
    print(f"⚠️ [B站夜间动态] 发布失败: {err_pub}")
    return pub_res


async def bili_dynamic_daily_worker():
    """
    每天 21:45 ~ 22:30（UTC+8，错开 23:00 QQ 空间日记，避免跨 0 点丢失）自动发表一条聚焦今日 B站 冲浪收获与视频推荐的动态。
    若发布失败，15 分钟后自动重试 1 次（最多尝试 2 次）；仍失败时向主人（MASTER_QQ）私聊发送简明报错通知。
    """
    print("🌙 [B站夜间动态] 已上线，每日将在 21:45~22:30 错峰档期总结今日 B站 冲浪并发布动态...")
    await asyncio.sleep(45)

    daily_targets = {}
    daily_attempts = {}
    while True:
        try:
            now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
            today_str = now_utc8.strftime("%Y-%m-%d")

            hist = load_bili_dynamic_history()
            posted_dates = hist.get("posted_dates", {})

            if today_str not in daily_targets:
                # 每天在 21:45 ~ 22:30 随机漂移一个发布分钟点 (0~45 分钟偏移)
                offset_mins = random.randint(0, 45)
                base_target = now_utc8.replace(hour=21, minute=45, second=random.randint(0, 59), microsecond=0)
                daily_targets[today_str] = base_target + datetime.timedelta(minutes=offset_mins)
                daily_attempts[today_str] = 0
                if len(daily_targets) > 3:
                    for old_k in list(daily_targets.keys())[:-3]:
                        daily_targets.pop(old_k, None)
                        daily_attempts.pop(old_k, None)

            target_dt = daily_targets[today_str]
            attempts = daily_attempts.get(today_str, 0)
            if today_str not in posted_dates and attempts < 2:
                if now_utc8 >= target_dt:
                    daily_attempts[today_str] = attempts + 1
                    res = await run_bili_daily_dynamic_once(today_str)
                    if res.get("ok") or res.get("skipped"):
                        # 成功或因 0 观看跳过，今日不再重试
                        daily_attempts[today_str] = 2
                        await asyncio.sleep(600)
                    else:
                        err_detail = res.get("error") or str(res)
                        if daily_attempts[today_str] < 2:
                            print(f"🔁 [B站夜间动态] 首次发布未成功 ({err_detail})，将在 15 分钟后自动重试 1 次...")
                            await asyncio.sleep(15 * 60)
                        else:
                            alert_msg = (
                                f"⚠️ 主人，弥酱今晚 ({today_str}) 的 B站 总结动态连续尝试 2 次均发布失败喵：\n"
                                f"报错摘要：{err_detail}\n"
                                "（您可随时私聊发送【B站诊断】或【测试B站动态】排查接口状态～）"
                            )
                            try:
                                await send_private_msg_raw(int(MASTER_QQ), alert_msg)
                            except Exception as se:
                                print(f"⚠️ [B站夜间动态] 向主人发送报错私聊失败: {se}")
                            await asyncio.sleep(1800)
                else:
                    wait_sec = min(1800, max(30, (target_dt - now_utc8).total_seconds()))
                    await asyncio.sleep(wait_sec)
            else:
                await asyncio.sleep(1800)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [bili_dynamic] 定时循环异常: {e}")
            await asyncio.sleep(180)

