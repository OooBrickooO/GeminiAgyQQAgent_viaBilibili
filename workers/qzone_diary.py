"""
workers/qzone_diary.py — 晚间空间说说日记自动生成（2B 0-Token 先挑后写策展人模式）

核心升级：
1. 修复历史 Bug：SQLite chat_archive_fts 群聊 chat_id 带 `group_` 前缀，现已完整兼容 `group_{gid}` 与 `{gid}`，杜绝全天群聊记忆漏抓；
2. 0-Token 本地策展筛选 (`curate_today_diary_highlights`)：
   - 融合当日 L2 中程温感记忆（含桌游对弈、重要事件、B站手账）、高分 B 站观看记录、完整对话切片与 7D 真实情绪底色；
   - 按情绪波动与信息密度精选 1~2 个具体高光切片，彻底丢弃 90% 琐碎水聊；
   - 彻底废除旧版“早/中/晚三段流水轴”，从原料源头根治小学生流水账日记；
3. 真人化松弛篇幅与防撞车：
   - 主力字数回归真实 QQ 空间常态（20~95 字），长篇降权至 <=10% 仅在大事件时触发；
   - 淡化“空友爱看”公域作文腔，禁用 AI 鸡汤总结结尾，并注入最近 3 篇历史日记防句式雷同。
"""
import os
import re
import json
import uuid
import time
import random
import datetime
import asyncio
from config import (
    get_agy_env, BOT_NAMESPACE, MASTER_QQ, get_allowed_groups,
    CHAT_AGY_EFFORT, AGY_EXECUTABLE, build_agy_model_flags, BASE_DIR,
    LONG_TERM_DB_FILE, BOT_QQ
)
from core.state_manager import RECENT_GROUP_MSGS
from core.quote_resolver import MSG_RECORD_CACHE
from core.agy_runner import GLOBAL_AGY_SEMAPHORE
from core.napcat_client import call_onebot_api
from social.qzone import load_qzone_history
from social.mid_term_memory import load_mid_term_memories
from social.mood_manager import get_decayed_mood_state, build_mood_injection_block


def get_beijing_now() -> datetime.datetime:
    """统一采用北京时间 (UTC+8)"""
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def _ts_to_beijing_date(ts: float) -> str:
    dt = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc) + datetime.timedelta(hours=8)
    return dt.strftime("%Y-%m-%d")


_EMOTION_OR_FUN_RE = re.compile(
    r"(哈哈|笑死|离谱|绷不住|绝了|好耶|气死|委屈|难过|开心|太香|好玩|下棋|五子棋|黑白棋|象棋|21点|翻盘|绝杀|认输|抽签|大吉|大凶|B站|视频|番剧|唱歌|语音|熬夜|摸鱼|吃啥|奶茶|烧烤|火锅)",
    re.IGNORECASE,
)


def _clean_chat_snippet(text: str) -> str:
    s = re.sub(r'\[CQ:[^\]]+\]', '', str(text or '')).strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def curate_today_diary_highlights(today_str: str) -> tuple[str, int, list[dict]]:
    """
    【2B Step 1 · 0-Token 本地策展筛选引擎】
    从当日 L2 记忆、B 站精选、SQLite 完整对话片段与 7D 真实心情中，
    按情绪浓度与事件完整度打分，只精选 1~2 个具体高光切片，丢弃其余 90% 琐碎水聊，
    彻底摒弃早/中/晚三段流水轴。
    返回: (curated_context_text, total_raw_events, selected_slices)
    """
    allowed_gids = {str(gid).strip() for gid in get_allowed_groups() if str(gid).strip()}
    allowed_chat_ids = allowed_gids | {f"group_{gid}" for gid in allowed_gids} | {f"private_{MASTER_QQ}"}
    allowed_group_scenes = {f"group_{gid}" for gid in allowed_gids}

    candidates: list[dict] = []  # {"score": float, "kind": str, "title": str, "detail": str}
    total_raw_events = 0

    # --- 1. 当日 L2 中程温感记忆（最高信噪比：完整事件、棋局结算、深度互动；严格隔离非主人第三人私聊隐私）---
    try:
        l2_entries = load_mid_term_memories()
        for e in l2_entries:
            if not isinstance(e, dict):
                continue
            c_ts = float(e.get("created_at", 0.0))
            if not c_ts or _ts_to_beijing_date(c_ts) != today_str:
                continue
            tid = str(e.get("target_id", "")).strip()
            src = str(e.get("source_scene", "")).strip()
            txt = str(e.get("text", "")).strip()
            if len(txt) < 6:
                continue
            # 隐私守卫：仅允许本群公开事件、公共事件、或主人相关，不把其他好友的私密私聊写进公开空间
            is_public_or_master = (
                tid in ("group_common", "global", "bili_surf", str(MASTER_QQ))
                or tid in allowed_group_scenes
                or src in allowed_group_scenes
                or src == f"private_{MASTER_QQ}"
            )
            if not is_public_or_master:
                continue
            total_raw_events += 1
            imp = float(e.get("importance", 0.55))
            tags = e.get("tags") or []
            bonus = 0.0
            if any(t in ("小游戏", "猫爪五子棋", "翻转黑白棋", "国际象棋", "中国象棋", "猫猫21点") for t in tags):
                bonus += 2.2
            if tid == str(MASTER_QQ) or src == f"private_{MASTER_QQ}":
                bonus += 1.6
            if _EMOTION_OR_FUN_RE.search(txt):
                bonus += 1.0
            candidates.append({
                "score": 5.5 + imp * 3.0 + bonus + random.uniform(0.0, 0.8),
                "kind": "l2_event",
                "title": "今天一件印象挺深的小事",
                "detail": txt[:180],
            })
    except Exception as e:
        print(f"⚠️ [空间日记策展] 读取 L2 记忆略过: {e}")

    # --- 2. 今日 B站 冲浪高光（只取最戳中弥酱的 1 条，避免报菜名）---
    try:
        from workers.bili_worker import load_today_bili_feed
        feed = load_today_bili_feed()
        watched = [w for w in (feed.get("watched") or []) if isinstance(w, dict)]
        total_raw_events += len(watched)
        if watched:
            best_bili = max(watched, key=lambda x: float(x.get("score", 0)))
            b_title = str(best_bili.get("title", "")).strip()
            b_up = str(best_bili.get("author", "")).strip()
            b_slot = str(best_bili.get("highlight_or_slot") or best_bili.get("comment") or "").strip()
            b_score = float(best_bili.get("score", 60))
            if b_title:
                candidates.append({
                    "score": 4.2 + min(3.5, max(0.0, (b_score - 55.0) / 10.0)) + random.uniform(0.0, 0.8),
                    "kind": "bili_highlight",
                    "title": "今天在 B站 摸鱼刷到的视频",
                    "detail": f"刷到了《{b_title}》(UP:{b_up})，感受：{b_slot[:140]}",
                })
    except Exception as e:
        print(f"⚠️ [空间日记策展] 获取 B站 足迹略过: {e}")

    # --- 3. 从 SQLite chat_archive_fts（修复 group_ 前缀）与内存缓存提取“完整对话切片” ---
    raw_msgs: list[dict] = []
    seen_keys = set()

    if os.path.exists(LONG_TERM_DB_FILE):
        try:
            import sqlite3
            conn = sqlite3.connect(LONG_TERM_DB_FILE, timeout=10.0)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT chat_id, sender_id, sender_name, time_str, raw_text
                    FROM chat_archive_fts
                    WHERE date_str = ?
                    ORDER BY rowid ASC
                    """,
                    (today_str,),
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                cid = str(r["chat_id"] or "").strip()
                if cid not in allowed_chat_ids:
                    continue
                txt = _clean_chat_snippet(r["raw_text"])
                if len(txt) < 4:
                    continue
                k = (cid, txt[:32])
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                sid = str(r["sender_id"] or "").strip()
                sname = str(r["sender_name"] or sid).split("(")[0].strip()
                raw_msgs.append({
                    "cid": cid,
                    "sid": sid,
                    "sname": sname,
                    "txt": txt[:120],
                    "is_bot": (sid == str(BOT_QQ) or "弥酱" in sname),
                    "is_master": (sid == str(MASTER_QQ) or cid == f"private_{MASTER_QQ}"),
                })
        except Exception as e:
            print(f"⚠️ [空间日记策展] 读取 SQLite chat_archive_fts 略过: {e}")

    # 补充内存即时缓存（兜底 SQLite 尚未刷盘或本地调试场景）
    try:
        for _mid, m in list(MSG_RECORD_CACHE.items()):
            m_time = float(m.get("time", 0.0))
            if not m_time or _ts_to_beijing_date(m_time) != today_str:
                continue
            txt = _clean_chat_snippet(m.get("text", ""))
            if len(txt) < 4:
                continue
            k = ("cache", txt[:32])
            if k in seen_keys:
                continue
            seen_keys.add(k)
            sid = str(m.get("sender_id", "")).strip()
            sname = str(m.get("nickname") or sid or "群友").strip()
            raw_msgs.append({
                "cid": "cache",
                "sid": sid,
                "sname": sname,
                "txt": txt[:120],
                "is_bot": (sid == str(BOT_QQ)),
                "is_master": (sid == str(MASTER_QQ)),
            })
    except Exception:
        pass

    for gid in allowed_gids:
        for m in list(RECENT_GROUP_MSGS.get(gid, [])):
            txt = _clean_chat_snippet(m.get("text", ""))
            if len(txt) < 4:
                continue
            k = (f"group_{gid}", txt[:32])
            if k in seen_keys:
                continue
            seen_keys.add(k)
            sid = str(m.get("sender_id", "")).strip()
            sname = str(m.get("nickname") or sid or "群友").strip()
            raw_msgs.append({
                "cid": f"group_{gid}",
                "sid": sid,
                "sname": sname,
                "txt": txt[:120],
                "is_bot": bool(m.get("is_bot", False)),
                "is_master": (sid == str(MASTER_QQ)),
            })

    total_raw_events += len(raw_msgs)

    # 将连续的 2~4 条互动聚合成“局部对话切片”，只给有趣或弥酱亲自接过话的片段高分
    if raw_msgs:
        by_cid: dict[str, list[dict]] = {}
        for m in raw_msgs:
            by_cid.setdefault(m["cid"], []).append(m)

        burst_cands = []
        for cid, msgs in by_cid.items():
            for idx in range(0, len(msgs), 3):
                chunk = msgs[idx: idx + 4]
                if not chunk:
                    continue
                joined = " / ".join(
                    f"{'我' if x['is_bot'] else x['sname']}: {x['txt']}"
                    for x in chunk
                )
                if len(joined) < 18:
                    continue
                sc = 3.0
                if any(x["is_bot"] for x in chunk):
                    sc += 2.0
                if any(x["is_master"] for x in chunk):
                    sc += 1.8
                if _EMOTION_OR_FUN_RE.search(joined):
                    sc += 1.6
                sc += min(1.5, len(joined) / 140.0)
                sc += random.uniform(0.0, 0.7)
                burst_cands.append({
                    "score": sc,
                    "kind": "chat_slice",
                    "title": "和主人私下聊到的一小段" if cid == f"private_{MASTER_QQ}" else "今天在群里聊到的一段趣事",
                    "detail": joined[:240],
                })
        burst_cands.sort(key=lambda x: x["score"], reverse=True)
        candidates.extend(burst_cands[:3])

    # --- 4. 策展挑选：只留 1~2 个类型不重复的最高分切片 ---
    candidates.sort(key=lambda x: x["score"], reverse=True)
    selected: list[dict] = []
    used_kinds = set()
    pick_limit = 1 if (len(candidates) <= 1 or random.random() < 0.45) else 2
    for c in candidates:
        if len(selected) >= pick_limit:
            break
        if c["kind"] in used_kinds and len(candidates) > pick_limit:
            continue
        selected.append(c)
        used_kinds.add(c["kind"])

    # --- 5. 读取当前真实 7D 心情状态 ---
    mood_line = ""
    try:
        mood_line = build_mood_injection_block(current_scene="qzone_diary", consume_pulse=False)
        if not mood_line:
            view, _ = get_decayed_mood_state()
            r = str(view.get("last_reason", "") or "").strip()
            if r:
                mood_line = f"【今晚真实心境余温】{r}"
    except Exception:
        mood_line = ""

    sections = []
    if mood_line:
        sections.append(mood_line)
    if selected:
        slice_lines = [f"- 【{s['title']}】：{s['detail']}" for s in selected]
        sections.append("【今晚只挑出来的 1~2 个真实生活切片（不必全写，挑最有感觉的写即可）】：\n" + "\n".join(slice_lines))
    else:
        sections.append("【今日状态】：今天安安静静的，群里和私聊都很冷清，基本自己在打游戏、听歌、发呆摸鱼。")

    return "\n\n".join(sections), total_raw_events, selected


def extract_today_full_day_memory(today_str: str) -> tuple[str, int]:
    """向后兼容别名：转发至 2B 本地策展引擎，返回 (curated_context_text, total_events)"""
    ctx, total, _ = curate_today_diary_highlights(today_str)
    return ctx, total


def select_diary_style_and_length(total_events: int) -> dict:
    """
    真人空间动态篇幅与单点切入姿态抽选：
    - 主力 20~95 字（极短碎念 / 自然日常），长篇压低至 <=10% 且冷清日为 0%；
    - 严禁早/中/晚全天串烧流水账。
    """
    styles = [
        {
            "id": "micro",
            "type": "极短随笔 / 睡前一句碎碎念",
            "length_desc": "20 ~ 45 字左右",
            "guidance": "像钻进被窝前随手敲的一两句大白话。只提今晚脑子里最挥之不去的一个小点或困意，说完就停，不要铺垫也不要升华。"
        },
        {
            "id": "casual",
            "type": "自然日常 / 单点小吐槽",
            "length_desc": "50 ~ 95 字左右",
            "guidance": "像 20 岁爱打游戏的女生发自己空间，只揪着上面 1 件具体的事聊聊细节或吐个槽，语气松弛自然，绝不要从早到晚报流水账。"
        },
        {
            "id": "deep",
            "type": "有感而发 / 轻微展开",
            "length_desc": "110 ~ 165 字左右",
            "guidance": "难得今天有点感触，围绕 1~2 个具体画面稍微多聊几句心里话。依然保持口语和真实小脾气，像在自言自语而不是给路人写演讲稿。"
        }
    ]

    if total_events < 6:
        weights = [0.65, 0.35, 0.0]
    elif total_events < 25:
        weights = [0.45, 0.48, 0.07]
    else:
        weights = [0.35, 0.55, 0.10]

    chosen = dict(random.choices(styles, weights=weights, k=1)[0])
    angles = [
        "只放大一个最有趣或最让你想吐槽的瞬间，别试图把今天所有事都塞进来。",
        "顺着此刻真实的困意或心情随口开场，自然带过那件印象最深的小事就行。",
        "像发给自己熟人看的随手动态，直接切入画面，不需要“今天真是……”的开头和总结。",
    ]
    chosen["angle"] = random.choice(angles)
    return chosen


# 内存中记录最近成功发布日记的日期字符串 (如 "2026-10-18")，作为第一道快速防重锁
LAST_QZONE_POSTED_DATE = ""


async def daily_qzone_diary_worker():
    """晚间 (约 22:15 ~ 23:45 随机时间窗) 自动生成并发布 QQ 空间说说日记"""
    global LAST_QZONE_POSTED_DATE
    while True:
        now = get_beijing_now()
        today_str = now.strftime("%Y-%m-%d")

        # 1. 检查今日是否已经成功发布过空间日记 (结合内存状态与磁盘记录双重核验)
        q_hist = load_qzone_history()
        already_posted_today = (LAST_QZONE_POSTED_DATE == today_str) or any(d.get("date") == today_str for d in q_hist)

        # 2. 计算今晚的目标时间点与排期
        cutoff_today = now.replace(hour=23, minute=45, second=0, microsecond=0)

        if already_posted_today or now >= cutoff_today:
            tomorrow = (now + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
            next_target = tomorrow + datetime.timedelta(minutes=random.randint(15, 105))
        else:
            earliest_today = now.replace(hour=22, minute=15, second=0, microsecond=0)
            if now < earliest_today:
                next_target = now.replace(hour=22, minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=random.randint(15, 105))
            else:
                next_target = now + datetime.timedelta(minutes=random.randint(2, 10))

        wait_seconds = max(5.0, (next_target - now).total_seconds())
        print(f"📖 空间晚间日记预计将于 {next_target.strftime('%Y-%m-%d %H:%M')} 发布（约 {wait_seconds / 3600:.2f} 小时后，目标: {next_target.strftime('%H:%M')}）")
        await asyncio.sleep(wait_seconds)

        # === 唤醒后二次安全核验 ===
        now = get_beijing_now()
        today_str = now.strftime("%Y-%m-%d")

        q_hist = load_qzone_history()
        if (LAST_QZONE_POSTED_DATE == today_str) or any(d.get("date") == today_str for d in q_hist):
            print(f"🛑 [空间日记拦截] 今日 ({today_str}) 已经发布过空间日记，跳过本次执行，杜绝重复！")
            continue

        LAST_QZONE_POSTED_DATE = today_str
        print(f"🌙 [{now.strftime('%H:%M')}] 开始执行空间晚间日记策展与生成流水线 (2B)...")

        # 3. 2B Step 1：0-Token 本地策展（只留 1~2 个高光切片，拒绝流水账原料）
        topics_context, total_events, selected_slices = curate_today_diary_highlights(today_str)
        print(f"🧠 [空间日记策展] 扫描 {total_events} 条当日素材 -> 精选 {len(selected_slices)} 个具体生活切片！")

        # 4. 动态选定今日篇幅与单点切入姿态
        style_info = select_diary_style_and_length(total_events)
        print(f"🎨 [日记风格抽选] 本次选定: 【{style_info['type']}】 ({style_info['length_desc']})")

        # 5. 读取最近 3 篇历史空间日记防开头/句式雷同
        recent_diaries_text = "暂无往期日记记录。"
        try:
            if q_hist:
                last_three = q_hist[-3:]
                lines = [
                    f"- [{d.get('date')}] “{str(d.get('content', ''))[:100]}”"
                    for d in last_three
                    if isinstance(d, dict) and d.get("content")
                ]
                if lines:
                    recent_diaries_text = "\n".join(lines)
        except Exception:
            pass

        diary_prompt = (
            f"现在是 {today_str} 晚上 {now.strftime('%H:%M')}。请以星见弥（弥酱，20岁爱打游戏的现代都市随性猫娘女生）的口吻，发一条今晚的 QQ 空间说说。\n\n"
            f"{topics_context}\n\n"
            f"【你最近发过的空间说说（本次请换完全不同的开头、句式与切入点，严禁撞车）】：\n{recent_diaries_text}\n\n"
            f"【今晚状态与写法】：\n"
            f"- 篇幅：{style_info['type']}（{style_info['length_desc']}）\n"
            f"- 风格：{style_info['guidance']}\n"
            f"- 切入点：{style_info['angle']}\n\n"
            f"【去流水账与去人机感红线】：\n"
            f"1. **绝对禁止按时间顺序报流水账**（严禁出现“早上……中午……晚上……”结构，不要试图总结一整天）；\n"
            f"2. **严禁 AI 作文腔和正能量鸡汤结尾**（禁止出现“今天真是充实/有趣的一天”“总的来说”“新的一天也要加油”“希望明天……”等套话）；\n"
            f"3. 像真人睡前随手发一条说说，多用“我”自称，少用 Emoji 与括号动作描写，切忌每句末尾带“喵”；\n"
            f"4. 如果今天本身很冷清，就坦然写犯困、打游戏或发呆的小碎念，绝不硬编不存在的剧情；\n"
            f"5. 写好后请直接调用 qzone_action(action='publish', content=..., mood=..., topics='空友爱看,猫猫日常(或深夜碎念/游戏日常等标签)', caller_id='system') 发布，勿只输出纯文本。"
        )

        diary_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"qzone_diary_{today_str}"))
        model_flags, _, _ = build_agy_model_flags(effort=CHAT_AGY_EFFORT, allow_low=False)
        cmd = [
            AGY_EXECUTABLE,
            f"--conversation={diary_conv_id}",
            *model_flags,
            "--dangerously-skip-permissions",
            "--prompt", diary_prompt
        ]

        async with GLOBAL_AGY_SEMAPHORE:
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=BASE_DIR,
                    env={**get_agy_env(), "AGY_TOOL_DOMAINS": "system_admin"},
                    stdin=asyncio.subprocess.DEVNULL,
                    start_new_session=True
                )
                await asyncio.wait_for(process.wait(), timeout=180.0)
                print("✅ 空间晚间日记发布流程执行完成！")
            except Exception as e:
                print(f"⚠️ 空间日记执行异常: {e}")

        # 5.5 暖场提醒（若今日日记已成功落盘发布）
        try:
            latest_hist = load_qzone_history()
            today_entries = [d for d in latest_hist if d.get("date") == today_str]
            if today_entries and MASTER_QQ:
                warm_templates = [
                    "刚发了今晚的空间说说，主人有空顺手帮我点个赞呗",
                    "主人还没睡吧？我刚发完今晚的空间，快去点个赞暖暖场～",
                    "今晚的说说发出去了，主人有空去瞄一眼顺手点个赞呀"
                ]
                warm_msg = random.choice(warm_templates)
                await call_onebot_api("/send_private_msg", {
                    "user_id": int(MASTER_QQ),
                    "message": warm_msg
                })
                from social.long_term_memory import record_chat_archive
                record_chat_archive(f"private_{MASTER_QQ}", "bot", "弥酱", warm_msg, is_bot=True)
                print("💌 [空间暖场] 已向主人发送空间说说点赞暖场私聊！")
        except Exception as e:
            print(f"⚠️ 发送主人空间暖场提醒略过: {e}")

        print("ℹ️ [空间日记] 今日日记发帖收尾完成（好友空间互动已交由 qzone_observer 每日三档定时接管）。")
