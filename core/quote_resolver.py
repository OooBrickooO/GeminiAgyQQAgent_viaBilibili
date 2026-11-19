"""
core/quote_resolver.py — 群聊回复/引用语义还原与上下文关联引擎

功能：
1. 维护最近 300 条消息的轻量内存缓存 (MSG_RECORD_CACHE)。
2. 捕获 [CQ:reply,id=xxx]，优先从本地缓存提取原消息作者与摘要；未命中则异步回源 NapCat /get_msg。
3. 将 [CQ:reply] 还原为富有自然语义的文本：【回复 @昵称: "原消息摘要"】 实际文本。
4. 自动清理紧随其后的冗余 [CQ:at,qq=被引用人]。
5. 嗅探是否正在引用回复弥酱 (is_replying_to_bot)，精准对焦。
"""
import re
import time
import asyncio
from collections import OrderedDict
from config import BOT_QQ
from core.napcat_client import call_onebot_api

# 内存消息轻量缓存：message_id (str) -> {"sender_id": str, "nickname": str, "text": str, "time": float}
MSG_RECORD_CACHE = OrderedDict()
MAX_CACHE_SIZE = 300


def record_message_to_cache(message_id, sender_id, nickname: str, raw_text: str):
    """将新收到的消息录入引用溯源缓存池"""
    if not message_id:
        return
    mid_str = str(message_id)
    # 简要过滤过长 CQ 码用于纯净摘要
    clean_snippet = re.sub(r'\[CQ:image,[^\]]+\]', '[图片]', raw_text)
    clean_snippet = re.sub(r'\[CQ:reply,[^\]]+\]', '', clean_snippet).strip()

    MSG_RECORD_CACHE[mid_str] = {
        "sender_id": str(sender_id),
        "nickname": str(nickname or sender_id),
        "text": clean_snippet,
        "time": time.time()
    }
    if len(MSG_RECORD_CACHE) > MAX_CACHE_SIZE:
        MSG_RECORD_CACHE.popitem(last=False)


async def get_message_detail(message_id: str) -> dict:
    """获取消息详情，优先查本地缓存，未命中则请求 NapCat /get_msg"""
    mid_str = str(message_id)
    if mid_str in MSG_RECORD_CACHE:
        return MSG_RECORD_CACHE[mid_str]

    # 本地未命中，尝试调用 OneBot HTTP API /get_msg 获取
    try:
        int_id = int(message_id)
        resp = await call_onebot_api("/get_msg", {"message_id": int_id}, timeout=2.0)
        if resp.get("status") == "ok" and "data" in resp:
            data = resp["data"]
            sender = data.get("sender", {})
            sender_id = str(sender.get("user_id", ""))
            nickname = sender.get("card") or sender.get("nickname") or sender_id
            raw_msg = data.get("raw_message", "")
            clean_snippet = re.sub(r'\[CQ:image,[^\]]+\]', '[图片]', raw_msg)
            clean_snippet = re.sub(r'\[CQ:reply,[^\]]+\]', '', clean_snippet).strip()

            info = {
                "sender_id": sender_id,
                "nickname": nickname,
                "text": clean_snippet,
                "time": float(data.get("time", time.time()))
            }
            # 存入缓存
            MSG_RECORD_CACHE[mid_str] = info
            if len(MSG_RECORD_CACHE) > MAX_CACHE_SIZE:
                MSG_RECORD_CACHE.popitem(last=False)
            return info
    except Exception as e:
        pass

    return None


async def resolve_message_quotes(raw_message: str, bot_qq: int = BOT_QQ) -> tuple[str, bool]:
    """
    解析消息中的 [CQ:reply,id=xxx]，还原为自然语义文本。
    返回: (resolved_message, is_replying_to_bot)
    """
    reply_pattern = r'\[CQ:reply,id=(-?\d+)\]'
    match = re.search(reply_pattern, raw_message)
    if not match:
        return raw_message, False

    reply_id = match.group(1)
    info = await get_message_detail(reply_id)

    is_replying_to_bot = False
    if info:
        target_uid = info.get("sender_id", "")
        target_nick = info.get("nickname", "群友")
        target_text = info.get("text", "")

        if str(target_uid) == str(bot_qq):
            is_replying_to_bot = True

        # 截短原消息，避免上下文过长
        clean_excerpt = target_text.replace("\r", " ").replace("\n", " ").strip()
        if len(clean_excerpt) > 35:
            clean_excerpt = clean_excerpt[:35] + "…"
        if not clean_excerpt:
            clean_excerpt = "图片/表情"

        quote_tag = f"【回复 @{target_nick}: \"{clean_excerpt}\"】 "

        # 替换 CQ:reply
        replaced = raw_message[:match.start()] + quote_tag + raw_message[match.end():]

        # 顺便清理紧随其后的冗余 [CQ:at,qq=被引用人]（QQ官方回复机制通常会自动追加@被回复人）
        if target_uid:
            redundant_at = rf'\[CQ:at,qq={re.escape(str(target_uid))}\]\s*'
            replaced = re.sub(redundant_at, '', replaced, count=1)

        return replaced.strip(), is_replying_to_bot
    else:
        # 无法溯源原消息时的兜底表示
        replaced = raw_message[:match.start()] + f"【回复未知消息#{reply_id}】 " + raw_message[match.end():]
        return replaced.strip(), False
