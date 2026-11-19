#!/usr/bin/env python3
"""
clean_past_data_once.py — 一次性历史敏感资料温和有痕清理脚本（独立运行，零运行时干扰）

使用说明（在 Linux /home/your_user/qqAgent/ 或任意环境下均可智能识别路径）：
  1. 预览将要处理的旧记录（不修改任何文件）：
     python3 clean_past_data_once.py --dry-run
  2. 执行一次性有痕柔化（默认模式，自动保留 raw_*_backup 备份）：
     python3 clean_past_data_once.py
  3. 如需撤回并恢复全部原始文本：
     python3 clean_past_data_once.py --restore

设计原则：
  - 主程序运行时【完全不调用】本脚本，绝不拦截或干扰实时用户输入与日常对话；
  - 仅针对磁盘上已经存在的历史资料（mood_state.json, memes.json, mid_term_memory.json,
    user_profiles.json, long_term_memory.db）中极易触发 Google Gemini 前置拒收的露骨/擦边词组，
    做一次性局部替换并打上『〖已柔化:...〗』标记，让弥酱读到旧记录时知道此处被打过码，必要时可问主人。
"""
import os
import sys
import json
import sqlite3
import argparse


def resolve_base_dir() -> str:
    """智能识别项目根目录（优先脚本所在目录，兼容 Linux /home/your_user/qqAgent 与本地开发环境）"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        script_dir,
        os.path.dirname(script_dir),
        "/home/your_user/qqAgent",
        os.getcwd(),
    ]
    for cand in candidates:
        if cand and os.path.exists(os.path.join(cand, "config.py")):
            return os.path.abspath(cand)
    return script_dir


BASE_DIR = resolve_base_dir()
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import (
    LONG_TERM_DB_FILE,
    MID_TERM_MEMORY_FILE,
    MOOD_STATE_FILE,
    MEMES_FILE,
    PROFILES_FILE,
)

# 仅保留真正曾触发 Google API Prohibited Use policy 预审秒拒的高危感官/擦边词组
# （绝不包含“累死我啦”、“不许乱动”等日常口头禅）
PAST_DATA_SOFTEN_RULES = [
    ("对着胸口吹气", "〖已柔化:近距离逗弄(原词已打码，可问主人)〗"),
    ("胸口吹气", "〖已柔化:近距离逗弄(原词已打码，可问主人)〗"),
    ("偷偷舔了一下", "〖已柔化:亲昵碰了一下(原词已打码，可问主人)〗"),
    ("咬遍全身盖章", "〖已柔化:扑上去闹腾反击(原词已打码，可问主人)〗"),
    ("咬遍全身", "〖已柔化:扑上去闹腾(原词已打码，可问主人)〗"),
    ("浑身发软", "〖已柔化:害羞脸红(原词已打码，可问主人)〗"),
    ("点完火就溜", "〖已柔化:逗完人就溜(原词已打码)〗"),
    ("飞扑抱住啃咬", "〖已柔化:飞扑抱住贴贴(原词已打码)〗"),
    ("啃咬贴贴", "〖已柔化:抱住贴贴(原词已打码)〗"),
    ("汤姆痴汉脸", "汤姆滑稽坏笑〖已柔化:原标签含敏感词〗"),
    ("痴汉脸", "滑稽坏笑〖已柔化:原词含敏感词〗"),
    ("痴汉", "〖已柔化:滑稽迷弟(原词可问主人)〗"),
    ("惩罚主人", "〖已柔化:拉着主人补偿(原词含敏感字眼，可问主人)〗"),
]

# 用于撤回旧版过度替换规则的恢复映射
LEGACY_OVERBROAD_REVERT = [
    ("〖已柔化:小鲸鱼可爱装扮〗", "小鲸鱼女仆"),
    ("小鲸鱼可爱装扮〖已柔化:原标签含敏感词〗", "小鲸鱼女仆"),
    ("〖已柔化:可爱洋装(原词可问主人)〗", "女仆装"),
    ("〖已柔化:小助手装扮(原词可问主人)〗", "女仆"),
    ("〖已柔化:小小捉弄你(原词已打码)〗", "惩罚你"),
    ("〖已柔化:乖乖待着别动〗", "不许乱动"),
    ("〖已柔化:多陪陪弥酱〗", "哪儿都不准去"),
    ("累趴我啦", "累死我啦"),
    ("累趴我了", "累死我了"),
]


def revert_legacy_overbroad(text: str) -> tuple[str, bool]:
    """如果文本曾被旧版规则过度替换过日常词，先还原回原词"""
    if not text:
        return text, False
    out = str(text)
    changed = False
    for old_rep, orig in LEGACY_OVERBROAD_REVERT:
        if old_rep in out:
            out = out.replace(old_rep, orig)
            changed = True
    return out, changed


def soften_past_text(text: str) -> tuple[str, bool]:
    """对历史文本应用精简后的有痕柔化规则"""
    if not text:
        return text, False
    out, chg_revert = revert_legacy_overbroad(str(text))
    changed = chg_revert
    for bad_phrase, replacement in PAST_DATA_SOFTEN_RULES:
        if bad_phrase in out:
            out = out.replace(bad_phrase, replacement)
            changed = True
    return out, changed


def process_mood_state(dry_run: bool = False, restore: bool = False) -> int:
    if not os.path.exists(MOOD_STATE_FILE):
        return 0
    with open(MOOD_STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return 0

    base_text = data.get("raw_reason_backup") or data.get("last_reason", "")
    if restore:
        if "raw_reason_backup" in data:
            print(f"[mood_state.json] 恢复原文: {data['raw_reason_backup']}")
            if not dry_run:
                data["last_reason"] = data.pop("raw_reason_backup")
                with open(MOOD_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            return 1
        return 0

    new_text, _ = soften_past_text(base_text)
    if new_text != data.get("last_reason", ""):
        print(f"📌 [mood_state.json]\n   原文本: {base_text}\n   处理后: {new_text}")
        if not dry_run:
            data["raw_reason_backup"] = base_text
            data["last_reason"] = new_text
            with open(MOOD_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return 1
    return 0


def process_memes(dry_run: bool = False, restore: bool = False) -> int:
    if not os.path.exists(MEMES_FILE):
        return 0
    with open(MEMES_FILE, "r", encoding="utf-8") as f:
        memes = json.load(f)
    if not isinstance(memes, list):
        return 0

    count = 0
    for m in memes:
        if not isinstance(m, dict):
            continue
        base_tag = m.get("raw_tag_backup") or m.get("tag", "")
        base_desc = m.get("raw_desc_backup") or m.get("description", "")
        if restore:
            if "raw_tag_backup" in m or "raw_desc_backup" in m:
                m["tag"] = m.pop("raw_tag_backup", m.get("tag", ""))
                m["description"] = m.pop("raw_desc_backup", m.get("description", ""))
                count += 1
            continue

        new_tag, _ = soften_past_text(base_tag)
        new_desc, _ = soften_past_text(base_desc)
        if new_tag != m.get("tag", "") or new_desc != m.get("description", ""):
            print(f"📌 [memes.json #{m.get('id')}]\n   原标签/描述: [{base_tag}] {base_desc}\n   处理后     : [{new_tag}] {new_desc}")
            if not dry_run:
                if new_tag != base_tag or new_desc != base_desc:
                    m["raw_tag_backup"] = base_tag
                    m["raw_desc_backup"] = base_desc
                else:
                    m.pop("raw_tag_backup", None)
                    m.pop("raw_desc_backup", None)
                m["tag"] = new_tag
                m["description"] = new_desc
            count += 1

    if count > 0 and not dry_run:
        with open(MEMES_FILE, "w", encoding="utf-8") as f:
            json.dump(memes, f, ensure_ascii=False, indent=2)
    return count


def process_mid_term_memory(dry_run: bool = False, restore: bool = False) -> int:
    if not os.path.exists(MID_TERM_MEMORY_FILE):
        return 0
    with open(MID_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        return 0

    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        base_txt = item.get("raw_text_backup") or item.get("text", "")
        if restore:
            if "raw_text_backup" in item:
                item["text"] = item.pop("raw_text_backup")
                count += 1
            continue

        new_txt, _ = soften_past_text(base_txt)
        if new_txt != item.get("text", ""):
            print(f"📌 [mid_term_memory.json #{item.get('id')}]\n   原文本: {base_txt}\n   处理后: {new_txt}")
            if not dry_run:
                if new_txt != base_txt:
                    item["raw_text_backup"] = base_txt
                else:
                    item.pop("raw_text_backup", None)
                item["text"] = new_txt
            count += 1

    if count > 0 and not dry_run:
        with open(MID_TERM_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    return count


def process_user_profiles(dry_run: bool = False, restore: bool = False) -> int:
    if not os.path.exists(PROFILES_FILE):
        return 0
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        profiles = json.load(f)
    if not isinstance(profiles, dict):
        return 0

    count = 0
    for uid, p in profiles.items():
        if not isinstance(p, dict):
            continue
        for pt in (p.get("pending_thoughts") or []):
            if not isinstance(pt, dict) or not pt.get("thought"):
                continue
            base_th = pt.get("raw_thought_backup") or pt.get("thought", "")
            if restore:
                if "raw_thought_backup" in pt:
                    pt["thought"] = pt.pop("raw_thought_backup")
                    count += 1
                continue

            new_th, _ = soften_past_text(base_th)
            if new_th != pt.get("thought", ""):
                print(f"📌 [user_profiles.json QQ:{uid}]\n   原心事: {base_th}\n   处理后: {new_th}")
                if not dry_run:
                    if new_th != base_th:
                        pt["raw_thought_backup"] = base_th
                    else:
                        pt.pop("raw_thought_backup", None)
                    pt["thought"] = new_th
                count += 1

    if count > 0 and not dry_run:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)
    return count


def process_sqlite_db(dry_run: bool = False, restore: bool = False) -> int:
    if not os.path.exists(LONG_TERM_DB_FILE):
        return 0
    conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    count = 0

    try:
        with conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS _clean_past_backup (
                    table_name TEXT NOT NULL,
                    row_id INTEGER NOT NULL,
                    orig_text TEXT NOT NULL,
                    PRIMARY KEY (table_name, row_id)
                )"""
            )

        if restore:
            bak_rows = conn.execute(
                "SELECT table_name, row_id, orig_text FROM _clean_past_backup"
            ).fetchall()
            for b in bak_rows:
                tname = b["table_name"]
                rid = b["row_id"]
                orig = b["orig_text"]
                print(f"[long_term_memory.db {tname} #{rid}] 恢复原文: {orig[:80]}...")
                if not dry_run:
                    if tname == "permanent_facts":
                        conn.execute("UPDATE permanent_facts SET content = ? WHERE id = ?", (orig, rid))
                    elif tname == "chat_archive_fts":
                        conn.execute("UPDATE chat_archive_fts SET raw_text = ? WHERE rowid = ?", (orig, rid))
                count += 1
            if count > 0 and not dry_run:
                with conn:
                    conn.execute("DELETE FROM _clean_past_backup")
            return count

        # 1. 扫描并有痕柔化 L3 permanent_facts
        has_pf = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='permanent_facts'"
        ).fetchone()
        if has_pf:
            rows = conn.execute("SELECT id, fact_key, content FROM permanent_facts").fetchall()
            for r in rows:
                rid = r["id"]
                bak = conn.execute(
                    "SELECT orig_text FROM _clean_past_backup WHERE table_name='permanent_facts' AND row_id=?",
                    (rid,)
                ).fetchone()
                base_content = bak["orig_text"] if bak else (r["content"] or "")
                new_content, chg = soften_past_text(base_content)
                if chg and new_content != (r["content"] or ""):
                    print(
                        f"📌 [long_term_memory.db -> permanent_facts #{rid} ({r['fact_key']})]\n"
                        f"   原内容: {base_content}\n"
                        f"   处理后: {new_content}"
                    )
                    if not dry_run:
                        with conn:
                            conn.execute(
                                "INSERT OR IGNORE INTO _clean_past_backup (table_name, row_id, orig_text) VALUES ('permanent_facts', ?, ?)",
                                (rid, base_content)
                            )
                            conn.execute("UPDATE permanent_facts SET content = ? WHERE id = ?", (new_content, rid))
                    count += 1

        # 2. 扫描并有痕柔化历史聊天归档 chat_archive_fts（防止私聊前情滑窗或 search_memory 捞出旧敏感原话导致拒收）
        has_arch = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_archive_fts'"
        ).fetchone()
        if has_arch:
            arch_rows = conn.execute("SELECT rowid, chat_id, time_str, sender_name, raw_text FROM chat_archive_fts").fetchall()
            for ar in arch_rows:
                rid = ar["rowid"]
                bak = conn.execute(
                    "SELECT orig_text FROM _clean_past_backup WHERE table_name='chat_archive_fts' AND row_id=?",
                    (rid,)
                ).fetchone()
                base_raw = bak["orig_text"] if bak else (ar["raw_text"] or "")
                new_raw, chg = soften_past_text(base_raw)
                if chg and new_raw != (ar["raw_text"] or ""):
                    print(
                        f"📌 [long_term_memory.db -> chat_archive_fts #{rid} ({ar['chat_id']} {ar['time_str']})]\n"
                        f"   原记录: {ar['sender_name']}: {base_raw}\n"
                        f"   处理后: {ar['sender_name']}: {new_raw}"
                    )
                    if not dry_run:
                        with conn:
                            conn.execute(
                                "INSERT OR IGNORE INTO _clean_past_backup (table_name, row_id, orig_text) VALUES ('chat_archive_fts', ?, ?)",
                                (rid, base_raw)
                            )
                            conn.execute("UPDATE chat_archive_fts SET raw_text = ? WHERE rowid = ?", (new_raw, rid))
                    count += 1
    finally:
        conn.close()
    return count


def main():
    parser = argparse.ArgumentParser(description="一次性历史敏感资料有痕柔化清理工具")
    parser.add_argument("--dry-run", action="store_true", help="仅预览命中的历史记录，不写入文件")
    parser.add_argument("--restore", action="store_true", help="从 raw_*_backup 恢复原始文本")
    args = parser.parse_args()

    mode_str = "【预览模式 (Dry-Run)】" if args.dry_run else ("【恢复模式 (Restore)】" if args.restore else "【执行一次性有痕柔化】")
    print(f"============================================================")
    print(f"🛠️ 弥酱历史资料一次性清理工具 {mode_str}")
    print(f"📂 自动识别项目根目录: {BASE_DIR}")
    print(f"============================================================")

    total = 0
    total += process_mood_state(dry_run=args.dry_run, restore=args.restore)
    total += process_memes(dry_run=args.dry_run, restore=args.restore)
    total += process_mid_term_memory(dry_run=args.dry_run, restore=args.restore)
    total += process_user_profiles(dry_run=args.dry_run, restore=args.restore)
    total += process_sqlite_db(dry_run=args.dry_run, restore=args.restore)

    print(f"------------------------------------------------------------")
    if total == 0:
        print("✅ 扫描完毕：当前没有需要变更的历史记录。")
    elif args.dry_run:
        print(f"🔍 预览完毕：共发现 {total} 处历史记录可处理（未修改磁盘文件）。")
        print("👉 确认无误后可直接运行: python3 clean_past_data_once.py")
    else:
        print(f"✅ 处理完毕：共完成 {total} 处历史记录更新（原文已备份在 raw_*_backup 字段）。")


if __name__ == "__main__":
    main()
