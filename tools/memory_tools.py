"""
tools/memory_tools.py — L3 永久长效记忆写入与毫秒级混合检索 MCP 业务实现 V3.0

包含：
- save_long_term_memory_impl: 主动将重要事实、主人喜好/技术栈、双方约定或群梗永久存入 L3 数据库（支持 fact_key 状态演进）
- search_memory_impl: 毫秒级混合检索 L3 永久记忆库与全量历史聊天归档 (FTS5)，彻底告别盲猜整数 days_ago
- recall_past_memory_impl: 保持向后兼容，优先通过 FTS5 极速检索，同时兼容旧版 transcript.jsonl 回退
- record_pending_thought_impl / resolve_pending_thought_impl: 挂起心事管理
"""
import os
import json
import uuid
import datetime
from config import BOT_NAMESPACE, log_mcp_tool
from social.long_term_memory import (
    upsert_permanent_fact,
    search_long_term_and_archive,
    fulfill_permanent_fact,
    fulfill_permanent_facts_by_keyword,
)


async def save_long_term_memory_impl(
    target_id: str,
    content: str,
    category: str = "user_fact",
    keywords: str = "",
    fact_key: str = ""
) -> str:
    """
    将重要事实、人物画像、双方约定或群内名场面永久存入 L3 长效记忆库。
    """
    log_mcp_tool("save_long_term_memory", f"对象={target_id}, 类别={category}, 键={fact_key} -> {content[:50]}")
    try:
        res = upsert_permanent_fact(
            target_id=str(target_id),
            content=content,
            category=category,
            keywords=keywords,
            fact_key=fact_key
        )
        if not res:
            return "写入失败：记忆内容不能为空或过短。"
        if res.get("action") == "duplicate_skipped":
            return f"该条长期记忆已存在，无需重复记录：{content}"
        if res.get("action") == "superseded_old":
            return (
                f"✅ 已永久铭记并更新事实键 [{fact_key}]！\n"
                f"- 最新状态：{content}\n"
                f"- 已归档为历史曾用状态：{res.get('superseded_content')}"
            )
        return f"✅ 已永久铭记至 L3 长效记忆库（ID:{res.get('id')}）：{content}"
    except Exception as e:
        return f"保存长期记忆失败: {e}"


async def search_memory_impl(
    query: str,
    target_id: str = "auto",
    include_archive: bool = True
) -> str:
    """
    毫秒级混合检索 L3 永久长效记忆库与全量历史聊天归档（无需猜 days_ago）。
    """
    log_mcp_tool("search_memory", f"检索词='{query}', 对象={target_id}, 含聊天归档={include_archive}")
    try:
        return search_long_term_and_archive(
            query=query,
            target_id=target_id,
            include_archive=include_archive,
            limit=8
        )
    except Exception as e:
        return f"检索记忆失败: {e}"


async def recall_past_memory_impl(chat_id: str, days_ago: int = 1, keyword: str = "") -> str:
    """
    向后兼容的过去记忆搜索：
    1. 若提供了 keyword，优先直接走毫秒级 L3 + FTS5 历史归档全文检索（跨任意天数，不再因为猜错 days_ago 而漏搜）；
    2. 同时兼容按指定 days_ago 调取特定日期记录。
    """
    log_mcp_tool("recall_past_memory", f"通道 {chat_id}, {days_ago}天前, 关键词: '{keyword}'")
    try:
        fast_result = ""
        if keyword and keyword.strip():
            fast_result = search_long_term_and_archive(query=keyword.strip(), target_id=chat_id, include_archive=True, limit=6)
            if "均未找到" not in fast_result:
                return fast_result

        if days_ago <= 0:
            return fast_result or "days_ago 必须大于 0，或请直接传入 keyword 使用 search_memory 跨日期检索。"

        target_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8) - datetime.timedelta(days=days_ago)
        date_str = target_date.strftime("%Y-%m-%d")

        # 先从 SQLite chat_archive_fts 按日期精确调取
        from social.long_term_memory import _get_conn, init_long_term_db
        init_long_term_db()
        conn = _get_conn()
        if keyword:
            rows = conn.execute(
                """SELECT time_str, sender_name, raw_text FROM chat_archive_fts
                   WHERE date_str = ? AND raw_text LIKE ?
                   ORDER BY time_str ASC LIMIT 50""",
                (date_str, f"%{keyword}%")
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT time_str, sender_name, raw_text FROM chat_archive_fts
                   WHERE date_str = ? AND chat_id = ?
                   ORDER BY time_str DESC LIMIT 50""",
                (date_str, str(chat_id))
            ).fetchall()
        conn.close()

        if rows:
            lines = [f"[{r['time_str']}] {r['sender_name']}: {r['raw_text']}" for r in rows]
            return f"=== 成功调取 {date_str} 的历史对话记录（共{len(lines)}条） ===\n" + "\n".join(lines)

        # 兜底：回退读取旧版 transcript.jsonl
        daily_chat_id = f"{chat_id}_{date_str}"
        past_uuid = str(uuid.uuid5(BOT_NAMESPACE, daily_chat_id))
        home_dir = os.path.expanduser("~")
        from config import get_transcript_path
        log_path = get_transcript_path(past_uuid)
        if not os.path.exists(log_path):
            return fast_result or f"没有找到 {date_str} ({days_ago}天前) 的聊天记录。"

        lines = []
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        data = json.loads(line)
                        if data.get("type") == "USER_INPUT":
                            lines.append(f"当时记录: {data.get('content', '')[-300:]}")
                        elif data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                            lines.append(f"当时回复: {data.get('content')}")
                    except Exception:
                        pass
        if not lines:
            return fast_result or f"{date_str} 的记忆档案为空。"
        if keyword:
            lines = [l for l in lines if keyword.lower() in l.lower()]
        return f"=== 调取 {date_str} 归档记录 ===\n" + "\n".join(lines[-40:])
    except Exception as e:
        return f"读取记忆时出错: {str(e)}"


async def record_pending_thought_impl(user_id: str, thought: str, expire_days: int = 3) -> str:
    """在心里暗暗记下一件关于某人的未尽小事/待聊话题"""
    log_mcp_tool("record_pending_thought", f"记下关于 {user_id} 的心事: {thought[:40]}")
    try:
        from social.affection import add_pending_thought
        add_pending_thought(str(user_id), thought, expire_days)
        return f"已在心里记下关于用户 {user_id} 的小事（保留 {expire_days} 天）：{thought}"
    except Exception as e:
        return f"记录心事失败: {e}"


async def resolve_pending_thought_impl(user_id: str, keyword: str) -> str:
    """当某件惦记的事已经当面聊过或已了结时，在心里标记完成并放下"""
    log_mcp_tool("resolve_pending_thought", f"了结关于 {user_id} 的心事: {keyword}")
    try:
        from social.affection import resolve_pending_thought
        ok = resolve_pending_thought(str(user_id), keyword)
        if ok:
            return f"心里惦记的关于【{keyword}】的事已经聊完，已在心里放下。"
        else:
            return f"未找到匹配【{keyword}】的心事记录。"
    except Exception as e:
        return f"处理心事失败: {e}"


async def resolve_memory_fact_impl(
    fact_id: str = "",
    target_id: str = "",
    keyword: str = "",
    reason: str = ""
) -> str:
    """
    一键核销已兑现或已完成的约定、待办与过时记忆：
    - 同步清理 L3 permanent_facts（标记 status='fulfilled'，撤出常驻提示词）、L2 mid_term_memory 与心里挂念 (pending_thoughts)。
    """
    import os
    import re
    log_mcp_tool(
        "resolve_memory_fact",
        f"id={fact_id or '-'}, target={target_id or 'auto'}, kw={keyword or '-'}, reason={reason[:30]}"
    )
    raw_id = str(fact_id or "").strip()
    clean_kw = str(keyword or "").strip()
    resolved_tid = str(target_id or "").strip()
    if not resolved_tid or resolved_tid in ("auto", "all"):
        if os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip() == "private":
            resolved_tid = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()

    if not raw_id and not clean_kw:
        return "请至少提供 fact_id（如 '#L3:12' 或 '12'）或 keyword 关键词之一。"

    resolved_items = []
    try:
        from social.affection import USER_PROFILES, load_profiles, resolve_pending_thought
        from social.mid_term_memory import (
            delete_mid_term_memory,
            get_recent_memories_for_observer,
            extract_query_tokens,
        )

        # 1. 按显式 ID 精准核销
        if raw_id:
            norm_id = raw_id.lstrip("#").strip()
            low_id = norm_id.lower()
            if low_id.startswith("pt:"):
                pt_id = norm_id.split(":", 1)[1].strip()
                if resolved_tid:
                    if resolve_pending_thought(resolved_tid, pt_id):
                        resolved_items.append(f"心事(pt:{pt_id})")
                else:
                    load_profiles()
                    for uid in list(USER_PROFILES.keys()):
                        if resolve_pending_thought(uid, pt_id):
                            resolved_items.append(f"心事(pt:{pt_id})")
                            break
            elif low_id.startswith("l2:"):
                l2_id = norm_id.split(":", 1)[1].strip()
                if delete_mid_term_memory(memory_id=l2_id):
                    resolved_items.append(f"L2动态(l2:{l2_id})")
            else:
                if low_id.startswith("l3:"):
                    norm_id = norm_id.split(":", 1)[1].strip()
                m_num = re.search(r"(\d+)", norm_id)
                if m_num:
                    res = fulfill_permanent_fact(int(m_num.group(1)), resolution_note=reason)
                    if res.get("ok"):
                        resolved_items.append(f"L3约定(#{m_num.group(1)}: {res.get('content', '')})")
                        if not resolved_tid and res.get("target_id"):
                            resolved_tid = str(res["target_id"])
                        if not clean_kw and res.get("content"):
                            clean_kw = str(res["content"])[:18]

        # 2. 按关键词联动核销同源 L3 / 挂念心事 / L2 近期动态
        if clean_kw:
            l3_hits = fulfill_permanent_facts_by_keyword(
                target_id=resolved_tid or "auto",
                keyword=clean_kw,
                resolution_note=reason
            )
            for item in l3_hits:
                tag = f"L3约定(#{item.get('id')}: {item.get('content', '')})"
                if tag not in resolved_items:
                    resolved_items.append(tag)
                if not resolved_tid and item.get("target_id"):
                    resolved_tid = str(item["target_id"])

            if resolved_tid:
                if resolve_pending_thought(resolved_tid, clean_kw):
                    resolved_items.append(f"心事({clean_kw})")

                kw_low = clean_kw.lower()
                kw_tokens = set(extract_query_tokens(kw_low))
                for m_item in get_recent_memories_for_observer(resolved_tid, max_age_days=14.0, limit=15):
                    txt = str(m_item.get("text", ""))
                    txt_low = txt.lower()
                    hit = kw_low in txt_low
                    if not hit and kw_tokens:
                        m_tokens = set(extract_query_tokens(txt_low))
                        if m_tokens and len(kw_tokens & m_tokens) / max(1, len(kw_tokens)) >= 0.55:
                            hit = True
                    if hit and delete_mid_term_memory(memory_id=m_item["id"]):
                        resolved_items.append(f"L2动态({txt})")

        if resolved_items:
            return "已成功核销并放下以下过往约定/待办：\n- " + "\n- ".join(resolved_items)
        return f"未找到匹配（fact_id={raw_id or '无'}, keyword={clean_kw or '无'}）的活跃约定或记忆。"
    except Exception as e:
        return f"核销记忆失败: {e}"
