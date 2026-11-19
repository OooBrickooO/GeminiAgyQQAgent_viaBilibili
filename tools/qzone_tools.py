"""
tools/qzone_tools.py — QQ 空间社交互动与日记说说 MCP 业务实现

包含：
- publish_qzone_feed_impl: 发布空间日记 (权限严格把控：仅限 system 或主人 MASTER_QQ，铁律 7)
- recall_qzone_memory_impl: 空间历史日记说说检索回忆
- like_friend_qzone_feed_impl: 熟人好友说说点赞
- comment_friend_qzone_feed_impl: 熟人好友说说评论
"""
import os
import random
import datetime
from config import MASTER_QQ, log_mcp_tool
from social.qzone import (
    load_qzone_history, post_qzone_feed,
    like_friend_feed, comment_friend_feed
)
from social.qzone_special_care import (
    get_effective_special_care_map, add_special_care, remove_special_care
)


def _pick_meme_image_for_qzone(mood: str = "") -> str:
    """
    从弥酱本地表情包库中挑选一张最应景的非 GIF 图片作为空间说说配图（最佳努力）。
    若无合适图片或库为空则返回空串。
    """
    try:
        from social.memes import list_all_memes, MEMES_DIR
        memes = list_all_memes()
        if not memes:
            return ""

        allowed_exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
        candidates = []
        for m in memes:
            fn = str(m.get("filename", "")).strip()
            if not fn or not fn.lower().endswith(allowed_exts):
                continue
            full_path = os.path.join(MEMES_DIR, fn)
            if os.path.isfile(full_path):
                candidates.append((m, full_path))

        if not candidates:
            return ""

        mood_kw = (mood or "").strip().lower()
        if mood_kw:
            matched = [
                path for m, path in candidates
                if mood_kw in str(m.get("tag", "")).lower()
                or mood_kw in str(m.get("description", "")).lower()
            ]
            if matched:
                return random.choice(matched)

        return random.choice([path for _, path in candidates])
    except Exception:
        return ""


async def publish_qzone_feed_impl(
    content: str,
    mood: str = "日常",
    topics: str = "",
    caller_id: str = ""
) -> str:
    """发布一条 QQ 空间说说动态（系统发说说时会尝试顺带挑选一张收藏表情包配图，失败自动降级纯文本）"""
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    clean_caller = env_caller or str(caller_id).replace("[CQ:at,qq=", "").replace("]", "").strip()
    log_mcp_tool("publish_qzone_feed", f"发布空间日记 [来自: {clean_caller}]: {content[:40]}...")

    # 权限铁律：空间属于弥酱与主人的私密领域，仅允许系统定时任务或主人下令
    if clean_caller and clean_caller not in (str(MASTER_QQ), "system", "系统"):
        return f"🔒 权限不足：QQ空间说说属于弥酱的私家日记，仅限弥酱自身或主人({MASTER_QQ})可下令发布，普通群友无权互动。"

    topic_list = [t.strip() for t in topics.split(",") if t.strip()] if topics else []
    meme_img = _pick_meme_image_for_qzone(mood)
    result = await post_qzone_feed(
        content,
        mood,
        topic_list,
        clean_caller or "system",
        image_path=meme_img,
    )

    if result.get("success"):
        final_topics = result.get("topics") or topic_list or ["空友爱看", "猫猫日常"]
        tags_str = " ".join(f"#{t}#" for t in final_topics[:3])
        pic_note = "（已附带收藏表情包配图）" if result.get("has_image") else ""
        return (
            f"成功发布 QQ 空间说说动态{pic_note}并同步投递至『空友爱看』公域流！\n"
            f"- 日记内容: {content.strip()}\n"
            f"- 心情: 【{mood}】 | 推荐话题: {tags_str}\n"
            f"- 已自动点亮首赞完成冷启动，并完整沉淀入空间专属记忆库（共收录 {result.get('total_count', 0)} 篇日记）。"
        )
    else:
        return result.get("message", "发布空间动态遇到异常。")


async def recall_qzone_memory_impl(days_ago: int = -1, keyword: str = "") -> str:
    """搜索回忆弥酱在 QQ 空间发布过的历史说说日记（days_ago=-1 表示不限日期检索全部，>=0 表示特定天数）"""
    log_mcp_tool("recall_qzone_memory", f"查询空间历史说说: {days_ago}天前, 关键词: '{keyword}'")
    history = load_qzone_history()
    if not history:
        return "空间专属记忆库为空，弥酱还没有发布过任何空间说说日记呢。"

    filtered = []
    target_date = ""
    if days_ago is not None and days_ago >= 0:
        now_bj = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        target_date = (now_bj - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d")

    for item in history:
        if target_date and item.get("date") != target_date:
            continue
        if keyword:
            kw = keyword.lower()
            text_match = kw in item.get("content", "").lower()
            mood_match = kw in item.get("mood", "").lower()
            topic_match = any(kw in t.lower() for t in item.get("topics", []))
            if not (text_match or mood_match or topic_match):
                continue
        filtered.append(item)

    if not filtered:
        return f"没有找到符合条件（{target_date or '所有日期'}，关键词: {keyword or '无'}）的空间说说记忆。"

    lines = [f"=== 调取到 {len(filtered)} 篇 QQ 空间说说日记记忆 ==="]
    for idx, item in enumerate(filtered[-5:], 1):
        lines.append(
            f"{idx}. [{item.get('date')} {item.get('time')}] 心情: 【{item.get('mood', '日常')}】\n"
            f"   内容: “{item.get('content')}”"
        )
    return "\n".join(lines)


async def like_friend_qzone_feed_impl(friend_uin: str, feed_tid: str) -> str:
    """为熟人好友的 QQ 空间新说说点赞"""
    log_mcp_tool("like_friend_qzone_feed", f"给好友 {friend_uin} 的说说 {feed_tid} 点赞")
    res = await like_friend_feed(friend_uin, feed_tid)
    return res.get("message", "点赞执行完成。")


async def comment_friend_qzone_feed_impl(friend_uin: str, feed_tid: str, comment_text: str) -> str:
    """为熟人好友的 QQ 空间新说说发表评论"""
    log_mcp_tool("comment_friend_qzone_feed", f"评论好友 {friend_uin} 的说说 {feed_tid}: {comment_text}")
    res = await comment_friend_feed(friend_uin, feed_tid, comment_text)
    return res.get("message", "评论执行完成。")


async def manage_qzone_special_care_impl(
    action: str = "list",
    user_id: str = "",
    nickname: str = "",
    reason: str = "",
) -> str:
    """
    管理弥酱自己的 QQ 空间【特别关注】名单（特关好友每天巡检都会优先关注并在异常低落时私聊关心）。
    - action='list': 查看当前生效的特别关注好友列表（含挚友默认自动特关 + 手动/自加名单）
    - action='add': 将指定 QQ 用户加入特别关注（弥酱自主新增需对方为熟人以上或主人允许）
    - action='remove': 将指定 QQ 用户移出特别关注（主人号不可移出）
    """
    act = (action or "list").strip().lower()
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    added_by = "master" if env_caller == str(MASTER_QQ) else "bot"
    log_mcp_tool("manage_qzone_special_care", f"act={act}, uid={user_id}, by={added_by}")

    if act == "list":
        care_map = get_effective_special_care_map()
        if not care_map:
            return "当前没有生效的 QQ 空间特别关注名单。"
        lines = [f"=== QQ 空间特别关注名单（共 {len(care_map)} 位）==="]
        for uid, info in care_map.items():
            lines.append(
                f"- {info.get('nickname', uid)} ({uid}) | 来源: {info.get('source')} | 好感: {info.get('affection', 0)} | 原因: {info.get('reason', '')}"
            )
        return "\n".join(lines)

    if act == "add":
        if not user_id:
            return "❌ 缺少目标 user_id（QQ号）。"
        res = add_special_care(user_id, nickname=nickname, reason=reason, added_by=added_by)
        return res.get("message") if res.get("ok") else f"⚠️ {res.get('error', '添加失败')}"

    if act == "remove":
        if added_by != "master":
            return f"🔒 权限不足：仅主人({MASTER_QQ})可移出特别关注名单。"
        if not user_id:
            return "❌ 缺少目标 user_id（QQ号）。"
        res = remove_special_care(user_id)
        return res.get("message") if res.get("ok") else f"⚠️ {res.get('error', '移除失败')}"

    return f"未知操作 action='{action}'，支持 'list' / 'add' / 'remove'。"

