"""
webui/api/memory_api.py — L3 永久记忆、FTS5 全文检索引擎与 L2 中程温感记忆 API
"""
import os
import time
import math
import sqlite3
import json
import datetime
from webui.config import LONG_TERM_DB_FILE, MID_TERM_MEMORY_FILE
from social.long_term_memory import (
    search_long_term_and_archive,
    _segment_for_fts,
    delete_permanent_fact,
    init_long_term_db
)
from social.mid_term_memory import (
    load_mid_term_memories,
    delete_mid_term_memory,
    _compute_activation,
    BASE_HALF_LIFE_DAYS
)


def get_permanent_facts(category: str = "all", status: str = "all", query: str = "", limit: int = 100) -> dict:
    init_long_term_db()
    if not os.path.exists(LONG_TERM_DB_FILE):
        return {"success": True, "facts": [], "categories": {}}

    conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        # 获取分类统计
        cur = conn.cursor()
        cur.execute("SELECT category, count(*) FROM permanent_facts GROUP BY category")
        categories_stats = {row[0]: row[1] for row in cur.fetchall()}

        sql = "SELECT * FROM permanent_facts WHERE 1=1"
        params = []
        if category and category != "all":
            sql += " AND category = ?"
            params.append(category)
        if status and status != "all":
            sql += " AND status = ?"
            params.append(status)
        if query and query.strip():
            sql += " AND (content LIKE ? OR keywords LIKE ? OR fact_key LIKE ?)"
            q_like = f"%{query.strip()}%"
            params.extend([q_like, q_like, q_like])

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cur.execute(sql, params)
        facts = []
        for r in cur.fetchall():
            facts.append({
                "id": r["id"],
                "target_id": r["target_id"],
                "category": r["category"],
                "fact_key": r["fact_key"],
                "content": r["content"],
                "keywords": r["keywords"],
                "created_at": r["created_at"],
                "timestamp": r["timestamp"],
                "status": r["status"],
                "superseded_by": r["superseded_by"]
            })

        return {
            "success": True,
            "total": len(facts),
            "categories": categories_stats,
            "facts": facts
        }
    except Exception as e:
        return {"success": False, "error": str(e), "facts": []}
    finally:
        conn.close()


def delete_permanent_fact_api(fact_id: int) -> dict:
    """WebUI 手动删除指定 ID 的 L3 永久事实"""
    try:
        ok = delete_permanent_fact(int(fact_id))
        if ok:
            return {"success": True, "deleted_id": int(fact_id), "message": f"已成功删除 L3 永久记忆 #{fact_id}"}
        return {"success": False, "error": f"未找到 ID={fact_id} 的永久记忆"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def simulate_fts_search(query_str: str, target_id: str = "auto") -> dict:
    if not query_str or not query_str.strip():
        return {"success": True, "query": "", "elapsed_ms": 0.0, "result_text": "请输入检索词"}

    start_t = time.perf_counter()
    try:
        text_res = search_long_term_and_archive(
            query=query_str.strip(),
            target_id=target_id,
            include_archive=True,
            limit=8
        )
        elapsed_ms = round((time.perf_counter() - start_t) * 1000.0, 2)
        return {
            "success": True,
            "query": query_str,
            "target_id": target_id,
            "elapsed_ms": elapsed_ms,
            "result_text": text_res
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - start_t) * 1000.0, 2)
        return {
            "success": False,
            "query": query_str,
            "elapsed_ms": elapsed_ms,
            "error": str(e),
            "result_text": f"检索异常: {e}"
        }


def get_mid_term_memories() -> dict:
    data = load_mid_term_memories()
    if not data:
        return {"success": True, "total": 0, "memories": []}

    now_ts = time.time()
    results = []

    for item in data:
        if not isinstance(item, dict):
            continue

        mem_id = str(item.get("id", ""))
        raw_text = item.get("text", "")
        t_created = float(item.get("created_at") or item.get("timestamp") or now_ts)
        imp = max(0.0, min(1.0, float(item.get("importance", 0.5))))
        a0 = float(item.get("activation") or item.get("initial_activation") or 0.85)
        h_days = round(float(item.get("half_life_days") or (BASE_HALF_LIFE_DAYS * (1.0 + 2.0 * imp))), 1)

        # 艾宾浩斯衰减激活度计算（与 social/mid_term_memory.py 严格一致）
        act = round(max(0.0, min(1.0, _compute_activation(item, now_ts))), 3)

        # 状态分段
        if act >= 0.5:
            temp_status = "活跃闪回区 (Warm)"
            color = "#10b981"
        elif act >= 0.2:
            temp_status = "逐渐冷却区 (Cooling)"
            color = "#f59e0b"
        else:
            temp_status = "冷凝淡出区 (Fading)"
            color = "#6b7280"

        # 相对时间展示
        hours_ago = max(0.0, (now_ts - t_created) / 3600.0)
        if hours_ago < 1.0:
            time_ago_str = f"{max(1, int(hours_ago * 60))}分钟前"
        elif hours_ago < 24.0:
            time_ago_str = f"{int(hours_ago)}小时前"
        else:
            time_ago_str = f"{int(hours_ago / 24.0)}天前"

        results.append({
            "id": mem_id,
            "text": raw_text,
            "target_id": item.get("target_id", "public"),
            "tags": item.get("tags") or [],
            "source": item.get("source", "chat"),
            "activation": act,
            "initial_activation": a0,
            "half_life_days": h_days,
            "recall_count": int(item.get("recall_count", 0)),
            "created_at_ts": t_created,
            "time_ago": time_ago_str,
            "temp_status": temp_status,
            "temp_color": color
        })

    # 按当前激活度从高到低排序
    results.sort(key=lambda x: x["activation"], reverse=True)

    return {
        "success": True,
        "total": len(results),
        "memories": results
    }


def delete_mid_term_memory_api(memory_id: str = "", text: str = "") -> dict:
    """WebUI 手动删除指定的 L2 中程温感记忆"""
    try:
        ok = delete_mid_term_memory(memory_id=memory_id, text=text)
        if ok:
            return {"success": True, "deleted_id": memory_id, "message": "已成功删除该条 L2 中程温感记忆"}
        return {"success": False, "error": "未找到匹配的 L2 温感记忆条目"}
    except Exception as e:
        return {"success": False, "error": str(e)}

