"""
webui/api/status_api.py — 弥酱系统概览与综合状态 API
"""
import os
import json
import time
import sqlite3
import datetime
from webui.config import (
    BOT_QQ,
    MASTER_QQ,
    LONG_TERM_DB_FILE,
    PROFILES_FILE,
    MID_TERM_MEMORY_FILE,
    MOOD_STATE_FILE
)
from social.mood_manager import get_decayed_mood_state, DIM_CN_NAMES, BASELINE_MOOD

SERVER_START_TIME = time.time()


def get_system_status() -> dict:
    now_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    uptime_sec = int(time.time() - SERVER_START_TIME)

    # 1. 情绪简况
    decayed_state, elapsed_sec = get_decayed_mood_state()
    mood_val = decayed_state.get("mood", BASELINE_MOOD)
    dims = decayed_state.get("dims", {})
    dominant_dim = "calm"
    max_dim_val = 0.0
    for k, v in dims.items():
        if v > max_dim_val:
            max_dim_val = v
            dominant_dim = k

    # 情绪状态标签 (基准线 60.0)
    if mood_val >= 84.0:
        mood_label = "亮堂雀跃 (极好说话)"
    elif mood_val >= 74.0:
        mood_label = "愉悦轻快 (非常好说话)"
    elif mood_val >= 66.0:
        mood_label = "温和舒展 (比较好说话)"
    elif mood_val >= 55.0:
        mood_label = "平静空白 (稳态基准)"
    elif mood_val >= 42.0:
        mood_label = "微闷傲娇 (不太好说话)"
    else:
        mood_label = "委屈低落 (难哄)"

    if max_dim_val >= 40.0:
        mood_label += f" · 偏向{DIM_CN_NAMES.get(dominant_dim, dominant_dim)}"

    # 2. L3 长期记忆库统计
    permanent_facts_count = 0
    superseded_count = 0
    chat_archive_count = 0
    if os.path.exists(LONG_TERM_DB_FILE):
        try:
            conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=3.0)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM permanent_facts WHERE status='active'")
            permanent_facts_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM permanent_facts WHERE status='superseded'")
            superseded_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM chat_archive_fts")
            chat_archive_count = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

    # 3. L2 中程记忆统计
    mid_term_count = 0
    if os.path.exists(MID_TERM_MEMORY_FILE):
        try:
            with open(MID_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    mid_term_count = len(data)
        except Exception:
            pass

    # 4. 用户画像统计
    user_count = 0
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    user_count = len(data)
        except Exception:
            pass

    return {
        "success": True,
        "bot_info": {
            "name": "星见弥 (Hoshimi Neko)",
            "bot_qq": str(BOT_QQ),
            "master_qq": str(MASTER_QQ),
            "version": "V3.0+ (双机协同·连续情绪流架构)",
            "server_time": now_dt.strftime("%Y-%m-%d %H:%M:%S (UTC+8)"),
            "uptime_seconds": uptime_sec,
            "uptime_human": f"{uptime_sec // 3600}小时 {(uptime_sec % 3600) // 60}分 {uptime_sec % 60}秒"
        },
        "mood_summary": {
            "current_mood": mood_val,
            "baseline": BASELINE_MOOD,
            "mood_label": mood_label,
            "dominant_emotion": DIM_CN_NAMES.get(dominant_dim, dominant_dim),
            "last_reason": decayed_state.get("last_reason", "") or "心情平静空白（对熟人热情，对陌生人正常自然）",
            "last_source_scene": decayed_state.get("last_source_scene", "") or "日常陪伴",
            "elapsed_since_update_sec": int(elapsed_sec)
        },
        "stats": {
            "permanent_facts_active": permanent_facts_count,
            "permanent_facts_superseded": superseded_count,
            "chat_archive_messages": chat_archive_count,
            "mid_term_memories": mid_term_count,
            "user_profiles_tracked": user_count
        }
    }

