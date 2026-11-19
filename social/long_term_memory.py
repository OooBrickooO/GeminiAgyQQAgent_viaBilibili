"""
social/long_term_memory.py — L3 永久长效记忆库与全量历史聊天归档混合检索引擎 (SQLite FTS5)

核心职责：
1. 永久存储不可遗忘的重要事实 (`permanent_facts`)：
   - 人物画像 (`user_fact`)、专属约定 (`shared_promise`)、群共同记忆/群梗 (`group_meme`)、情感里程碑 (`milestone`)
   - 支持事实键演进 (`fact_key` & `superseded_by`)：状态变化时将旧记录归档为曾用状态，既保持最新又不丢历史。
2. 实时全量聊天归档 (`chat_archive_fts`)：
   - 自动将群聊与私聊的真实人类发言与弥酱回复写入 SQLite FTS5 全文检索表，实现毫秒级跨月跨年原话搜索。
3. 前置【记忆雷达 (Memory Radar)】与 `search_memory` 毫秒级混合检索：
   - 彻底告别旧版盲猜整数 `days_ago` 的 `recall_past_memory`！
"""
import os
import re
import json
import time
import sqlite3
import datetime
from config import LONG_TERM_DB_FILE, PROFILES_FILE, MASTER_QQ, BOT_QQ
from social.mid_term_memory import extract_query_tokens

_DB_INITIALIZED = False


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _segment_for_fts(text: str) -> str:
    """将文本转化为适合 SQLite FTS5 unicode61 检索的空格分隔词元串 (单字 + 2-gram + 3-gram + 英文数字)"""
    if not text:
        return ""
    clean = re.sub(r'\[CQ:[^\]]+\]', ' ', str(text)).lower()
    tokens = []
    # 英文数字
    for m in re.finditer(r'[a-z0-9_]+', clean):
        tokens.append(m.group(0))
    # 中文字符级与 n-gram 双保险，无需外部重依赖即可实现毫秒级精准召回
    for block in re.findall(r'[\u4e00-\u9fff]+', clean):
        for ch in block:
            tokens.append(ch)
        for i in range(len(block) - 1):
            tokens.append(block[i:i + 2])
        for i in range(len(block) - 2):
            tokens.append(block[i:i + 3])
    return " ".join(tokens)


def init_long_term_db():
    """初始化 SQLite 表结构与 FTS5 全文索引表，并自动无损迁移旧 memory_capsule"""
    global _DB_INITIALIZED
    if _DB_INITIALIZED and os.path.exists(LONG_TERM_DB_FILE):
        return
    try:
        conn = _get_conn()
        with conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS permanent_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'user_fact',
                    fact_key TEXT DEFAULT '',
                    content TEXT NOT NULL,
                    keywords TEXT DEFAULT '',
                    source_scene TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    status TEXT DEFAULT 'active',
                    superseded_by INTEGER DEFAULT NULL,
                    fulfilled_at TEXT DEFAULT ''
                );
            """)
            try:
                conn.execute("ALTER TABLE permanent_facts ADD COLUMN source_scene TEXT DEFAULT '';")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE permanent_facts ADD COLUMN fulfilled_at TEXT DEFAULT '';")
            except Exception:
                pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_target ON permanent_facts(target_id, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_factkey ON permanent_facts(target_id, fact_key, status);")

            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS permanent_facts_fts USING fts5(
                    fact_id UNINDEXED,
                    target_id UNINDEXED,
                    segmented_text
                );
            """)

            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chat_archive_fts USING fts5(
                    chat_id UNINDEXED,
                    sender_id UNINDEXED,
                    sender_name UNINDEXED,
                    date_str UNINDEXED,
                    time_str UNINDEXED,
                    raw_text,
                    segmented_text
                );
            """)

        _migrate_legacy_capsules_if_needed(conn)
        conn.close()
        _DB_INITIALIZED = True
    except Exception as e:
        print(f"⚠️ 初始化长效记忆数据库异常: {e}")


def _migrate_legacy_capsules_if_needed(conn: sqlite3.Connection):
    """首次启动时，自动将 user_profiles.json 中现有的旧版 memory_capsule 迁移入 L3 永久库"""
    if not os.path.exists(PROFILES_FILE):
        return
    try:
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM permanent_facts;")
        row = cur.fetchone()
        if row and row["cnt"] > 0:
            return

        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            profiles = json.load(f)

        now_ts = time.time()
        now_str = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
        migrated = 0
        with conn:
            for uid, p in profiles.items():
                cap = (p.get("memory_capsule") or "").strip()
                if cap and cap != "无":
                    seg = _segment_for_fts(cap)
                    c = conn.execute(
                        """INSERT INTO permanent_facts
                           (target_id, category, fact_key, content, keywords, created_at, timestamp, status)
                           VALUES (?, 'user_fact', 'legacy.过往印象浓缩', ?, '', ?, ?, 'active')""",
                        (str(uid), cap, now_str, now_ts)
                    )
                    fid = c.lastrowid
                    conn.execute(
                        "INSERT INTO permanent_facts_fts (fact_id, target_id, segmented_text) VALUES (?, ?, ?)",
                        (str(fid), str(uid), seg)
                    )
                    migrated += 1
        if migrated > 0:
            print(f"✅ [记忆迁移] 已无损将 {migrated} 位用户的旧版 memory_capsule 迁入 L3 永久长效记忆库！")
    except Exception as e:
        print(f"⚠️ 旧版记忆胶囊迁移提示: {e}")


def upsert_permanent_fact(
    target_id: str,
    content: str,
    category: str = "user_fact",
    keywords: str = "",
    fact_key: str = "",
    source_scene: str = ""
) -> dict:
    """
    写入或更新一条 L3 永久长效记忆（支持记录来源场景 source_scene 以实现群聊/私聊隔离）。
    - 若提供了非空 fact_key（如 "主人.服务器配置", "ta.备考状态"），且同一 target_id 下已有同键 active 记录，
      则自动将旧记录置为 status='superseded' 并指向新记录 ID，保留历史变迁轨迹。
    - 若未提供 fact_key，但正文高度雷同，则直接跳过防重复。
    """
    init_long_term_db()
    clean_content = (content or "").strip()
    if not clean_content or len(clean_content) < 3:
        return {}

    target_str = str(target_id).strip()
    clean_key = (fact_key or "").strip()
    clean_cat = (category or "user_fact").strip()
    clean_kw = (keywords or "").strip()
    clean_scene = str(source_scene or "").strip()
    if not clean_scene:
        if target_str.startswith("group_"):
            clean_scene = target_str
        else:
            lock_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
            lock_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
            if lock_type == "private":
                clean_scene = f"private_{lock_id}" if lock_id else "private"
            elif lock_type == "group" and lock_id:
                clean_scene = f"group_{lock_id}"

    now_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")
    now_ts = now_dt.timestamp()

    try:
        conn = _get_conn()
        superseded_old_content = None
        with conn:
            # 1. 检查是否完全重复
            existing = conn.execute(
                "SELECT id, content, source_scene FROM permanent_facts WHERE target_id = ? AND status = 'active'",
                (target_str,)
            ).fetchall()
            for r in existing:
                if r["content"].strip() == clean_content:
                    if clean_scene and not (r["source_scene"] or "").strip():
                        conn.execute(
                            "UPDATE permanent_facts SET source_scene = ? WHERE id = ?",
                            (clean_scene, r["id"])
                        )
                    conn.close()
                    return {"id": r["id"], "action": "duplicate_skipped", "content": clean_content}

            # 2. 插入新记录
            seg = _segment_for_fts(f"{clean_content} {clean_kw} {clean_key}")
            cur = conn.execute(
                """INSERT INTO permanent_facts
                   (target_id, category, fact_key, content, keywords, source_scene, created_at, timestamp, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                (target_str, clean_cat, clean_key, clean_content, clean_kw, clean_scene, now_str, now_ts)
            )
            new_id = cur.lastrowid
            conn.execute(
                "INSERT INTO permanent_facts_fts (fact_id, target_id, segmented_text) VALUES (?, ?, ?)",
                (str(new_id), target_str, seg)
            )

            # 3. 若指定了 fact_key，把同 target_id + fact_key 的旧记录标记为 superseded
            if clean_key:
                old_rows = conn.execute(
                    """SELECT id, content FROM permanent_facts
                       WHERE target_id = ? AND fact_key = ? AND status = 'active' AND id != ?""",
                    (target_str, clean_key, new_id)
                ).fetchall()
                for old_r in old_rows:
                    superseded_old_content = old_r["content"]
                    conn.execute(
                        "UPDATE permanent_facts SET status = 'superseded', superseded_by = ? WHERE id = ?",
                        (new_id, old_r["id"])
                    )

            # 4. 群文化/群梗滚动保鲜：每个群最多保留最新 5 条 active 群梗，超出部分自动归档为 superseded
            if target_str.startswith("group_"):
                active_memes = conn.execute(
                    """SELECT id FROM permanent_facts
                       WHERE target_id = ? AND status = 'active'
                       ORDER BY timestamp DESC""",
                    (target_str,)
                ).fetchall()
                if len(active_memes) > 5:
                    for stale_row in active_memes[5:]:
                        conn.execute(
                            "UPDATE permanent_facts SET status = 'superseded', superseded_by = ? WHERE id = ?",
                            (new_id, stale_row["id"])
                        )
        conn.close()
        return {
            "id": new_id,
            "action": "superseded_old" if superseded_old_content else "inserted",
            "superseded_content": superseded_old_content,
            "content": clean_content,
        }
    except Exception as e:
        print(f"⚠️ 写入长效记忆失败: {e}")
        return {}


def record_chat_archive(
    chat_id: str,
    sender_id: str,
    sender_name: str,
    raw_text: str,
    is_bot: bool = False
):
    """
    实时将群聊/私聊的真实发言归档至 SQLite FTS5 表 `chat_archive_fts`，
    支撑毫秒级跨月跨年原话搜索，彻底替代慢速扫描 transcript.jsonl。
    """
    clean = re.sub(r'\[CQ:image,[^\]]+\]', '[图片]', str(raw_text or "")).strip()
    clean = re.sub(r'\[CQ:[^\]]+\]', '', clean).strip()
    if not clean or len(clean) < 2:
        return

    init_long_term_db()
    now_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    date_str = now_dt.strftime("%Y-%m-%d")
    time_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    speaker = "弥酱(你)" if is_bot else f"{sender_name}({sender_id})"
    seg = _segment_for_fts(f"{speaker} {clean}")

    try:
        conn = _get_conn()
        with conn:
            conn.execute(
                """INSERT INTO chat_archive_fts
                   (chat_id, sender_id, sender_name, date_str, time_str, raw_text, segmented_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(speaker), date_str, time_str, clean, seg)
            )
        conn.close()
    except Exception:
        pass


def delete_permanent_fact(fact_id: int) -> bool:
    """根据 fact_id 手动删除指定的 L3 永久事实（同步清理 FTS5 索引表）"""
    init_long_term_db()
    try:
        fid = int(fact_id)
        conn = _get_conn()
        with conn:
            cur = conn.execute("DELETE FROM permanent_facts WHERE id = ?", (fid,))
            deleted_count = cur.rowcount
            conn.execute("DELETE FROM permanent_facts_fts WHERE fact_id = ?", (str(fid),))
        conn.close()
        return deleted_count > 0
    except Exception as e:
        print(f"⚠️ 删除长效记忆失败 (id={fact_id}): {e}")
        return False


def fulfill_permanent_fact(fact_id: int, resolution_note: str = "") -> dict:
    """
    将指定的 L3 永久事实/约定标记为已兑现完成 (status='fulfilled')：
    - 从常驻提示词注入中立即撤下（不再反复冒泡追问）；
    - 保留记录供 search_memory 历史回忆检索（显示为【已兑现/已完成】）。
    """
    init_long_term_db()
    try:
        fid = int(fact_id)
        now_str = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, target_id, category, fact_key, content, status FROM permanent_facts WHERE id = ?",
            (fid,)
        ).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": f"未找到 L3 记忆 ID={fid}"}
        if row["status"] == "fulfilled":
            conn.close()
            return {"ok": True, "already_fulfilled": True, "id": fid, "content": row["content"]}

        with conn:
            conn.execute(
                "UPDATE permanent_facts SET status = 'fulfilled', fulfilled_at = ? WHERE id = ?",
                (now_str, fid)
            )
        conn.close()
        return {
            "ok": True,
            "id": fid,
            "target_id": row["target_id"],
            "category": row["category"],
            "fact_key": row["fact_key"],
            "content": row["content"],
            "resolution_note": resolution_note,
        }
    except Exception as e:
        print(f"⚠️ 核销 L3 记忆失败 (id={fact_id}): {e}")
        return {"ok": False, "error": str(e)}


def fulfill_permanent_facts_by_keyword(target_id: str, keyword: str, resolution_note: str = "") -> list[dict]:
    """按目标对象与关键词（支持子串与核心词元重合匹配）核销活跃的 L3 约定/待办"""
    init_long_term_db()
    clean_kw = (keyword or "").strip().lower()
    if not clean_kw:
        return []
    uid = str(target_id or "").strip()
    kw_tokens = set(extract_query_tokens(clean_kw))
    matched_results = []
    try:
        conn = _get_conn()
        if uid and uid not in ("auto", "all"):
            rows = conn.execute(
                """SELECT id, target_id, category, fact_key, content, keywords
                   FROM permanent_facts
                   WHERE target_id = ? AND status = 'active'
                   ORDER BY timestamp DESC LIMIT 30""",
                (uid,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, target_id, category, fact_key, content, keywords
                   FROM permanent_facts
                   WHERE status = 'active'
                   ORDER BY timestamp DESC LIMIT 40"""
            ).fetchall()
        conn.close()

        for r in rows:
            blob = f"{r['fact_key']} {r['content']} {r['keywords']}".lower()
            hit = clean_kw in blob
            if not hit and kw_tokens:
                row_tokens = set(extract_query_tokens(blob))
                if row_tokens and len(kw_tokens & row_tokens) / max(1, len(kw_tokens)) >= 0.55:
                    hit = True
            if hit:
                res = fulfill_permanent_fact(r["id"], resolution_note=resolution_note)
                if res.get("ok"):
                    matched_results.append(res)
    except Exception as e:
        print(f"⚠️ 按关键词核销 L3 记忆异常: {e}")
    return matched_results


def get_active_facts_for_observer(target_id: str, limit: int = 12) -> list[dict]:
    """供后台观察者获取当前用户活跃 L3 记录清单（含短 ID 与时间，用于精确按 ID 核销）"""
    init_long_term_db()
    uid = str(target_id or "").strip()
    if not uid:
        return []
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, category, fact_key, content, source_scene, created_at
               FROM permanent_facts
               WHERE target_id = ? AND status = 'active'
               ORDER BY timestamp DESC LIMIT ?""",
            (uid, limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def count_user_permanent_facts(target_id: str) -> int:
    """统计指定用户名下有效 (active) 的永久事实条数（用于挚友 850 分硬门槛校验）"""
    init_long_term_db()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM permanent_facts WHERE target_id = ? AND status = 'active'",
            (str(target_id).strip(),)
        ).fetchone()
        conn.close()
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


_TIME_BOUND_PROMISE_RE = re.compile(
    r'(凌晨|今晚|今天|明天|后天|周末|待会|一会|马上|等下|准备|打算|升级|面试|考试|提交|修理|更新)'
)


def get_user_core_facts_for_prompt(target_id: str, group_id_str: str = "", limit: int = 6) -> str:
    """
    供 relation.py / context_builder.py 调用：
    读取关于该用户的常驻 L3 永久事实（以及本群的群梗），附带轻量 ID 与时间标记支撑即时核销；
    严格遵守通道隔离：当处于群聊 (group_id_str 非空) 时，过滤掉私聊立下的约定/事实与其他群的专属事实。
    """
    init_long_term_db()
    uid = str(target_id).strip()
    cur_group_scene = f"group_{group_id_str}" if group_id_str else ""
    now_ts = time.time()
    try:
        conn = _get_conn()
        raw_rows = conn.execute(
            """SELECT id, fact_key, content, category, source_scene, created_at, timestamp FROM permanent_facts
               WHERE target_id = ? AND status = 'active'
               ORDER BY timestamp DESC LIMIT ?""",
            (uid, limit * 3)
        ).fetchall()

        rows = []
        for r in raw_rows:
            sc = str(r["source_scene"] or "").strip()
            cat = str(r["category"] or "user_fact").strip()
            if cur_group_scene:
                # 群聊场景下：屏蔽私聊来源事实、其他群来源事实，以及非本群立下的专属约定 (shared_promise)
                if sc.startswith("private"):
                    continue
                if sc.startswith("group_") and sc != cur_group_scene:
                    continue
                if cat == "shared_promise" and sc != cur_group_scene:
                    continue
            # 2C 软降权兜底：超过 5 天的明显一次性时效性约定不主动常驻注入（仍可通过 search_memory 检索）
            age_days = (now_ts - float(r["timestamp"] or now_ts)) / 86400.0
            if cat == "shared_promise" and age_days > 5.0 and _TIME_BOUND_PROMISE_RE.search(str(r["content"] or "")):
                continue
            rows.append(r)
            if len(rows) >= limit:
                break

        group_rows = []
        if group_id_str:
            group_rows = conn.execute(
                """SELECT fact_key, content FROM permanent_facts
                   WHERE target_id = ? AND status = 'active'
                   ORDER BY timestamp DESC LIMIT 3""",
                (f"group_{group_id_str}",)
            ).fetchall()
        conn.close()

        lines = []
        for r in rows:
            fk = f"[{r['fact_key']}] " if r["fact_key"] and not r["fact_key"].startswith("legacy.") else ""
            cat = str(r["category"] or "user_fact")
            date_short = str(r["created_at"] or "")[:10]
            if cat == "shared_promise":
                id_tag = f"[#L3:{r['id']}|{date_short}] " if date_short else f"[#L3:{r['id']}] "
            else:
                id_tag = ""
            lines.append(f"- {id_tag}{fk}{r['content']}")
        for gr in group_rows:
            fk = f"【{gr['fact_key'].replace('群梗.', '')}】" if gr["fact_key"] else ""
            lines.append(f"- [本群内部梗/名场面] {fk}{gr['content']}（仅在话题自然契合时顺嘴带出或调侃）")

        if not lines:
            return ""
        return "【你铭记于心的长期事实与印象（若某条带 #L3:ID 的约定对方已明确说做完/取消，可顺手调 memory_action(action='resolve', fact_id=...) 核销）】：\n" + "\n".join(lines)
    except Exception:
        return ""


def build_memory_radar_hint(query_text: str, chat_id_str: str = "", sender_id: str = "") -> str:
    """
    【前置记忆雷达 (Memory Radar)】：
    1. 优先匹配本群内部梗/黑话库 (`permanent_facts`)，实现0延迟群梗闪回；
    2. 预扫一次 `chat_archive_fts`（排除今天），若发现历史归档中曾深入聊过当前消息提及的稀有词元，注入一行极短的雷达线索引导模型按需调 `search_memory`。
       严格遵守通道隔离：群聊中仅探测本群的历史归档，绝不把私聊或其他群的记忆捅到本群。
    """
    ignore_ids = {str(sender_id).strip(), str(MASTER_QQ), str(BOT_QQ)}
    if chat_id_str:
        ignore_ids.add(str(chat_id_str).replace("group_", "").replace("private_", "").strip())

    q_tokens = [
        t for t in extract_query_tokens(query_text)
        if len(t) >= 2 and t not in ignore_ids and not re.fullmatch(r'\d{5,12}', t)
    ]
    if not q_tokens:
        return ""

    init_long_term_db()
    today_str = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    clean_query_lower = (query_text or "").lower()

    try:
        conn = _get_conn()
        # 1. 优先探测本群内部梗/黑话库 (Group Cultural Meme Flashback)
        if chat_id_str and str(chat_id_str).startswith("group_"):
            meme_rows = conn.execute(
                """SELECT fact_key, content, keywords
                   FROM permanent_facts
                   WHERE target_id = ?
                   ORDER BY timestamp DESC LIMIT 15""",
                (str(chat_id_str),)
            ).fetchall()
            for m_row in meme_rows:
                kw_raw = f"{m_row['fact_key'].replace('群梗.', '')} {m_row['keywords']}"
                kw_list = [k.strip().lower() for k in re.split(r'[\s,，、/]+', kw_raw) if len(k.strip()) >= 2]
                matched_kw = next((kw for kw in kw_list if kw in clean_query_lower), None)
                if not matched_kw:
                    matched_kw = next((t for t in q_tokens if len(t) >= 2 and t in kw_raw.lower()), None)
                if matched_kw:
                    fk_label = f"【{m_row['fact_key'].replace('群梗.', '')}】" if m_row["fact_key"] else ""
                    conn.close()
                    return (
                        f"\n💡 【本群内部梗闪回】：检测到关键词『{matched_kw}』命中本群记忆梗 {fk_label}：{m_row['content']}（契机自然时可顺嘴接梗调侃）"
                    )

        # 2. 探测跨天历史聊天原话归档 (segmented_text 已直接按 2-gram/3-gram/英文词元建索引)
        # 群聊下允许同时探测本群与当前发言人本人的私聊归档，严禁泄漏其他人的私聊
        q_tokens.sort(key=len, reverse=True)
        is_group_chat = bool(chat_id_str and str(chat_id_str).startswith("group_"))
        sender_clean = str(sender_id or "").strip()
        has_valid_sender = bool(sender_clean and sender_clean not in ("0", "系统", "system"))
        has_priv_radar_cue = any(w in clean_query_lower for w in ("私聊", "跟你说", "对你说", "找你聊", "之前说", "上次说"))
        min_radar_cnt = 1 if has_priv_radar_cue else 2

        for tok in q_tokens[:6]:
            safe_tok = re.sub(r'["\'\s]', '', tok.lower())
            if len(safe_tok) < 2:
                continue
            if is_group_chat:
                if has_valid_sender:
                    rows = conn.execute(
                        """SELECT date_str, COUNT(*) AS cnt
                           FROM chat_archive_fts
                           WHERE segmented_text MATCH ? AND date_str != ? AND (chat_id = ? OR chat_id = ?)
                           GROUP BY date_str
                           ORDER BY date_str DESC LIMIT 2""",
                        (f'"{safe_tok}"', today_str, str(chat_id_str), f"private_{sender_clean}")
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT date_str, COUNT(*) AS cnt
                           FROM chat_archive_fts
                           WHERE segmented_text MATCH ? AND date_str != ? AND chat_id = ?
                           GROUP BY date_str
                           ORDER BY date_str DESC LIMIT 2""",
                        (f'"{safe_tok}"', today_str, str(chat_id_str))
                    ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT date_str, COUNT(*) AS cnt
                       FROM chat_archive_fts
                       WHERE segmented_text MATCH ? AND date_str != ?
                       GROUP BY date_str
                       ORDER BY date_str DESC LIMIT 2""",
                    (f'"{safe_tok}"', today_str)
                ).fetchall()
            if rows and rows[0]["cnt"] >= min_radar_cnt:
                d_str = rows[0]["date_str"]
                cnt = rows[0]["cnt"]
                conn.close()
                return (
                    f"\n💡 【记忆雷达提示】：检索到历史归档（如 {d_str}）中有 {cnt} 条关于『{tok}』的过往聊天记录（含你与当前说话人的私聊互动）。"
                    f"若对方正在追问过去的事或你需要回忆当时的具体细节，可主动调用 search_memory(query='{tok}') 工具查看原话。"
                )
        conn.close()
    except Exception:
        pass
    return ""


def search_long_term_and_archive(
    query: str,
    target_id: str = "auto",
    include_archive: bool = True,
    limit: int = 8
) -> str:
    """
    毫秒级混合检索工具实现（同时搜索 L3 永久事实库含历史演进 + 全量历史聊天归档库；支持 target_id 过滤与通道隐私隔离）。
    """
    init_long_term_db()
    clean_q = (query or "").strip()
    if not clean_q:
        return "请提供非空的搜索关键词 query。"

    clean_target = str(target_id or "auto").strip()
    has_specific_target = clean_target not in ("auto", "all", "")

    # 识别当前所属物理通道与当前发言人 ID：
    # 群聊中默认屏蔽他人私聊与其他群记录，但允许主动检索【当前发言人本人(caller_id)】的私聊记忆！
    lock_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    lock_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    caller_id = os.environ.get("AGY_CALLER_ID", "").strip()
    if caller_id in ("0", "系统", "system"):
        caller_id = ""
    cur_group_scene = f"group_{lock_id}" if (lock_type == "group" and lock_id) else ""
    fetch_limit = limit * 4 if cur_group_scene else limit * 2

    tokens = extract_query_tokens(clean_q)
    if not tokens:
        tokens = [clean_q]

    fts_terms = []
    for t in tokens[:5]:
        seg = _segment_for_fts(t)
        if seg:
            fts_terms.append(f'({seg})')
    fts_expr = " OR ".join(fts_terms) if fts_terms else _segment_for_fts(clean_q)

    results_out = []
    try:
        conn = _get_conn()

        # 1. 搜索 L3 永久事实表 (包含 active 与 superseded 历史变迁)
        fact_rows = []
        if fts_expr:
            if has_specific_target:
                fact_rows = conn.execute(
                    """SELECT pf.id, pf.target_id, pf.category, pf.fact_key, pf.content, pf.source_scene, pf.created_at, pf.status
                       FROM permanent_facts_fts fts
                       JOIN permanent_facts pf ON CAST(fts.fact_id AS INTEGER) = pf.id
                       WHERE fts.segmented_text MATCH ? AND pf.target_id = ?
                       ORDER BY bm25(permanent_facts_fts) LIMIT ?""",
                    (fts_expr, clean_target, fetch_limit)
                ).fetchall()
            if not fact_rows:
                fact_rows = conn.execute(
                    """SELECT pf.id, pf.target_id, pf.category, pf.fact_key, pf.content, pf.source_scene, pf.created_at, pf.status
                       FROM permanent_facts_fts fts
                       JOIN permanent_facts pf ON CAST(fts.fact_id AS INTEGER) = pf.id
                       WHERE fts.segmented_text MATCH ?
                       ORDER BY bm25(permanent_facts_fts) LIMIT ?""",
                    (fts_expr, fetch_limit)
                ).fetchall()

        if not fact_rows:
            like_pat = f"%{clean_q}%"
            fact_rows = conn.execute(
                """SELECT id, target_id, category, fact_key, content, source_scene, created_at, status
                   FROM permanent_facts
                   WHERE content LIKE ? OR keywords LIKE ? OR fact_key LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (like_pat, like_pat, like_pat, fetch_limit)
            ).fetchall()

        if cur_group_scene and fact_rows:
            filtered_facts = []
            for r in fact_rows:
                sc = str(r["source_scene"] or "").strip()
                tid = str(r["target_id"] or "").strip()
                cat = str(r["category"] or "user_fact").strip()
                is_caller_private = bool(
                    sc.startswith("private")
                    and caller_id
                    and tid == caller_id
                    and sc in ("private", f"private_{caller_id}")
                )
                if sc.startswith("private") and not is_caller_private:
                    continue
                if sc.startswith("group_") and sc != cur_group_scene:
                    continue
                if cat == "shared_promise" and sc != cur_group_scene and not is_caller_private:
                    continue
                filtered_facts.append(r)
            fact_rows = filtered_facts[:limit]
        else:
            fact_rows = fact_rows[:limit]

        if fact_rows:
            results_out.append("=== 📌 [L3 永久长效记忆库匹配结果] ===")
            for r in fact_rows:
                st_map = {
                    "active": "【当前有效】",
                    "fulfilled": "【已兑现/已完成】",
                    "superseded": "【历史曾用/已演进】",
                }
                st_tag = st_map.get(r["status"], f"【{r['status']}】")
                fk_tag = f"({r['fact_key']}) " if r["fact_key"] else ""
                sc = str(r["source_scene"] or "").strip()
                priv_tag = " [与发言人本人的私聊记忆·勿向他人宣扬]" if (cur_group_scene and sc.startswith("private")) else ""
                results_out.append(
                    f"- {st_tag}{priv_tag} [#L3:{r['id']} | 对象:{r['target_id']} | 记录于 {r['created_at']}] {fk_tag}{r['content']}"
                )

        # 2. 搜索全量历史聊天归档表 chat_archive_fts
        if include_archive:
            arch_rows = []
            if cur_group_scene:
                # 群聊场景下：检索本群历史对话归档 + 当前发言人(caller_id)本人的私聊原话归档，严禁泄露他人私聊或其他群原话
                if caller_id:
                    priv_chat_id = f"private_{caller_id}"
                    if fts_expr:
                        arch_rows = conn.execute(
                            """SELECT chat_id, sender_name, time_str, raw_text
                               FROM chat_archive_fts
                               WHERE segmented_text MATCH ? AND (chat_id = ? OR chat_id = ?)
                               ORDER BY bm25(chat_archive_fts) LIMIT ?""",
                            (fts_expr, cur_group_scene, priv_chat_id, limit * 2)
                        ).fetchall()
                    if not arch_rows:
                        arch_rows = conn.execute(
                            """SELECT chat_id, sender_name, time_str, raw_text
                               FROM chat_archive_fts
                               WHERE raw_text LIKE ? AND (chat_id = ? OR chat_id = ?)
                               ORDER BY time_str DESC LIMIT ?""",
                            (f"%{clean_q}%", cur_group_scene, priv_chat_id, limit * 2)
                        ).fetchall()
                else:
                    if fts_expr:
                        arch_rows = conn.execute(
                            """SELECT chat_id, sender_name, time_str, raw_text
                               FROM chat_archive_fts
                               WHERE segmented_text MATCH ? AND chat_id = ?
                               ORDER BY bm25(chat_archive_fts) LIMIT ?""",
                            (fts_expr, cur_group_scene, limit * 2)
                        ).fetchall()
                    if not arch_rows:
                        arch_rows = conn.execute(
                            """SELECT chat_id, sender_name, time_str, raw_text
                               FROM chat_archive_fts
                               WHERE raw_text LIKE ? AND chat_id = ?
                               ORDER BY time_str DESC LIMIT ?""",
                            (f"%{clean_q}%", cur_group_scene, limit * 2)
                        ).fetchall()
            else:
                if fts_expr:
                    if has_specific_target:
                        arch_rows = conn.execute(
                            """SELECT chat_id, sender_name, time_str, raw_text
                               FROM chat_archive_fts
                               WHERE segmented_text MATCH ? AND (sender_id = ? OR chat_id = ? OR chat_id = ?)
                               ORDER BY bm25(chat_archive_fts) LIMIT ?""",
                            (fts_expr, clean_target, clean_target, f"private_{clean_target}", limit * 2)
                        ).fetchall()
                    if not arch_rows:
                        arch_rows = conn.execute(
                            """SELECT chat_id, sender_name, time_str, raw_text
                               FROM chat_archive_fts
                               WHERE segmented_text MATCH ?
                               ORDER BY bm25(chat_archive_fts) LIMIT ?""",
                            (fts_expr, limit * 2)
                        ).fetchall()

                if not arch_rows:
                    arch_rows = conn.execute(
                        """SELECT chat_id, sender_name, time_str, raw_text
                           FROM chat_archive_fts
                           WHERE raw_text LIKE ?
                           ORDER BY time_str DESC LIMIT ?""",
                        (f"%{clean_q}%", limit * 2)
                    ).fetchall()

            if arch_rows:
                results_out.append("\n=== 💬 [历史聊天原话归档匹配片段（仅供回忆参考，切勿向历史记录中的旧群号误发消息）] ===")
                for ar in arch_rows[:limit * 2]:
                    arch_chat_id = str(ar["chat_id"] or "")
                    scene_label = (
                        "与该群友本人的私聊(对方主动提及可回应，勿外传)"
                        if (cur_group_scene and arch_chat_id.startswith("private_"))
                        else arch_chat_id
                    )
                    results_out.append(
                        f"- [{ar['time_str']} | 历史场景:{scene_label}] {ar['sender_name']}: {ar['raw_text']}"
                    )

        conn.close()
    except Exception as e:
        return f"检索记忆库时出错: {e}"

    if not results_out:
        return f"在永久记忆库与历史聊天归档中均未找到与『{clean_q}』相关的记录。"

    return "\n".join(results_out)

