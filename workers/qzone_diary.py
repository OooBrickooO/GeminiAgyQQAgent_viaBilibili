"""
workers/qzone_diary.py — 晚间空间说说日记自动生成与熟人好友动态巡视

核心功能：
1. 告别刻板 23:00 准时打卡：采用 22:15 ~ 23:45 拟真晚间随机时间窗，每晚随机漂移，防止单日重启重复发布。
2. 跨时段全天记忆深度提炼：贯穿 transcript.jsonl 与全天消息缓存池，涵盖早晨、下午、晚间多维度对话与生活事件。
3. 动态篇幅与风格多样化：彻底打破固定字数模版，根据全天互动量动态在短篇碎念(25~55字)、普通日常(70~130字)、长篇小作文(160~280字)间自然流转。
4. 自动巡视熟人好友与主人动态。
"""
import os
import re
import json
import uuid
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
from social.affection import USER_PROFILES, load_profiles


def extract_today_full_day_memory(today_str: str) -> tuple[str, int]:
    """
    全天跨时段历史记忆深度提炼引擎：
    1. 调取本日所有活跃群聊及主人私聊的持久化 transcript.jsonl
    2. 结合内存中的全天消息记录缓存 MSG_RECORD_CACHE 与 RECENT_GROUP_MSGS
    3. 按【🌅早晨/上午】、【🌆中午/下午】、【🌙晚间/入夜】分时段归纳话题与弥酱互动
    返回：(formatted_context_text, total_recalled_events)
    """
    morning_events = []
    afternoon_events = []
    evening_events = []
    bot_speeches = []
    master_interactions = []
    seen_texts = set()

    # --- 1. 从内存全天消息缓存池 MSG_RECORD_CACHE 提取（带高精度时间戳）---
    try:
        for mid, m in list(MSG_RECORD_CACHE.items()):
            m_time = float(m.get("time", 0.0))
            if not m_time:
                continue
            dt = datetime.datetime.fromtimestamp(m_time, datetime.timezone.utc) + datetime.timedelta(hours=8)
            if dt.strftime("%Y-%m-%d") != today_str:
                continue

            text = m.get("text", "").strip()
            if not text or len(text) < 2 or text in seen_texts:
                continue
            seen_texts.add(text)

            speaker = m.get("nickname") or m.get("sender_id") or "群友"
            item_str = f"{speaker}: {text[:45]}"
            hour = dt.hour

            if 6 <= hour < 12:
                morning_events.append(item_str)
            elif 12 <= hour < 18:
                afternoon_events.append(item_str)
            else:
                evening_events.append(item_str)
    except Exception as e:
        print(f"⚠️ 从 MSG_RECORD_CACHE 提取记忆略过: {e}")

    # --- 2. 从 SQLite chat_archive_fts 提取全天真实对话档案（替代已废弃的每日固定 UUID transcript） ---
    allowed_chat_ids = {str(gid) for gid in get_allowed_groups()} | {f"private_{MASTER_QQ}"}
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
                cid = str(r["chat_id"] or "")
                if cid not in allowed_chat_ids:
                    continue
                sid = str(r["sender_id"] or "")
                sname = str(r["sender_name"] or sid).strip()
                tstr = str(r["time_str"] or "").strip()
                txt = str(r["raw_text"] or "").strip()
                if len(txt) <= 2 or txt in seen_texts:
                    continue
                seen_texts.add(txt)
                if sid == str(BOT_QQ) or "弥酱" in sname:
                    bot_speeches.append(txt[:45])
                elif sid == str(MASTER_QQ) or "主人" in sname:
                    master_interactions.append(f"{sname}: {txt[:40]}")
                else:
                    entry = f"{sname}: {txt[:40]}"
                    hhmm = tstr.split()[-1][:5] if tstr else ""
                    if hhmm and hhmm < "12:00":
                        morning_events.append(entry)
                    elif hhmm and hhmm < "18:00":
                        afternoon_events.append(entry)
                    else:
                        evening_events.append(entry)
        except Exception as e:
            print(f"⚠️ 从 SQLite chat_archive_fts 提取今日全天对话略过: {e}")

    # --- 3. 补充各群近几分钟的最即时消息 RECENT_GROUP_MSGS ---
    for gid in get_allowed_groups():
        recent = list(RECENT_GROUP_MSGS[str(gid)])
        for m in recent:
            if not m.get("is_bot", False):
                txt = m.get("text", "").strip()
                if txt and len(txt) > 2 and txt not in seen_texts:
                    seen_texts.add(txt)
                    spk = m.get("nickname", "群友")
                    evening_events.append(f"{spk}: {txt[:40]}")

    # --- 4. 补充弥酱今日在 B站 冲浪的精选视频足迹 ---
    bili_summaries = []
    try:
        from workers.bili_worker import get_today_bili_summary
        bili_summaries = get_today_bili_summary()
    except Exception as e:
        print(f"⚠️ 获取 B站 今日足迹异常: {e}")

    total_events = len(morning_events) + len(afternoon_events) + len(evening_events) + len(bot_speeches) + len(master_interactions) + len(bili_summaries)

    # --- 5. 组装整天时间轴回忆档案 ---
    sections = []
    if morning_events:
        sections.append("🌅 【早晨与上午时段记忆 (06:00~12:00)】：\n" + "\n".join(f"- {e}" for e in morning_events[-6:]))
    if afternoon_events:
        sections.append("🌆 【中午与下午时段记忆 (12:00~18:00)】：\n" + "\n".join(f"- {e}" for e in afternoon_events[-8:]))
    if evening_events:
        sections.append("🌙 【晚间与入夜时段记忆 (18:00~23:00+)】：\n" + "\n".join(f"- {e}" for e in evening_events[-8:]))
    if bili_summaries:
        sections.append("📺 【弥酱今日 B站 冲浪与视频足迹】：\n" + "\n".join(f"- {b}" for b in bili_summaries[-4:]))
    if bot_speeches:
        sections.append("💬 【弥酱今日参与的发言/吐槽】：\n" + "\n".join(f"- “{s}”" for s in bot_speeches[-5:]))
    if master_interactions:
        sections.append("👑 【与主人的重要互动】：\n" + "\n".join(f"- {m}" for m in master_interactions[-4:]))

    if sections:
        full_context = "\n\n".join(sections)
    else:
        full_context = "今天整整一天，群聊和私聊都非常安静，几乎没有群友发言互动，弥酱也基本上在静静潜水、打游戏或摸鱼度过了一整天。"

    return full_context, total_events


def select_diary_style_and_length(total_events: int) -> dict:
    """
    根据今日互动事件丰富度与随机权重，动态轮换日记篇幅与风格，坚决打破固定字数模版
    """
    styles = [
        {
            "id": "short",
            "type": "极短随笔 / 困倦晚安碎念",
            "length_desc": "极短精炼（25 ~ 55 字左右）",
            "guidance": "像深夜打完游戏困得不行、钻进被窝随手发的一两条微动态。直接点出今天最核心的一句吐槽、感悟或浓浓困意，干净利落，极其随性真实，严禁啰嗦废话或硬凑字数。"
        },
        {
            "id": "medium",
            "type": "普通日常 / 随性生活分享",
            "length_desc": "自然随性（70 ~ 130 字左右）",
            "guidance": "像真实爱冲浪的年轻女生记录今天发生的二三事。概括今天白天或晚上群里聊到的好玩话题，夹带自己的心情碎碎念与随性晚安，字数自然顺畅。"
        },
        {
            "id": "long",
            "type": "长篇深度复盘 / 心情小作文",
            "length_desc": "详细生动（160 ~ 280 字左右）",
            "guidance": "有感而发的心情长日记！充分串联今天从白天到晚上的生活脉络、群友们的精彩互动和自己的内心独白。像个活泼健谈的二次元女生跟空间好友分享今天充实或波折的一整天，情感丰富细腻。"
        }
    ]

    # 根据今日互动丰满程度动态分配风格权重
    if total_events < 8:
        # 今天特别冷清：70% 极短随笔，30% 普通日常，不强行长篇造假
        weights = [0.70, 0.30, 0.0]
    elif total_events < 25:
        # 普通活跃度：30% 短篇，50% 中篇，20% 长篇
        weights = [0.30, 0.50, 0.20]
    else:
        # 今天特别热闹：20% 短篇，40% 中篇，40% 长篇
        weights = [0.20, 0.40, 0.40]

    chosen = random.choices(styles, weights=weights, k=1)[0]
    return chosen


def get_beijing_now() -> datetime.datetime:
    """统一采用北京时间 (UTC+8)"""
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


# 内存中记录最近成功发布日记的日期字符串 (如 "2026-10-18")，作为第一道快速防重锁
LAST_QZONE_POSTED_DATE = ""


async def daily_qzone_diary_worker():
    """晚间 (约 22:15 ~ 23:45 随机时间窗) 自动生成并发布 QQ 空间说说日记，并巡视熟人好友空间"""
    global LAST_QZONE_POSTED_DATE
    while True:
        now = get_beijing_now()
        today_str = now.strftime("%Y-%m-%d")

        # 1. 检查今日是否已经成功发布过空间日记 (结合内存状态与磁盘记录双重核验)
        q_hist = load_qzone_history()
        already_posted_today = (LAST_QZONE_POSTED_DATE == today_str) or any(d.get("date") == today_str for d in q_hist)

        # 2. 计算今晚的目标时间点与排期：
        # 如果今天已经发过，或者今晚时间窗口已过 (已过 23:45)：绝对锁定到明晚！
        cutoff_today = now.replace(hour=23, minute=45, second=0, microsecond=0)

        if already_posted_today or now >= cutoff_today:
            # 锁定明晚：明晚 22:00 + 15~105 分钟随机浮动 (22:15 ~ 23:45)
            tomorrow = (now + datetime.timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
            next_target = tomorrow + datetime.timedelta(minutes=random.randint(15, 105))
        else:
            # 今天尚未发布，且尚未超过 23:45 截止线
            earliest_today = now.replace(hour=22, minute=15, second=0, microsecond=0)
            if now < earliest_today:
                # 还没到 22:15，正常在今晚 22:15~23:45 选一个点
                next_target = now.replace(hour=22, minute=0, second=0, microsecond=0) + datetime.timedelta(minutes=random.randint(15, 105))
            else:
                # 当前已在 22:15~23:45 之间且今天未发：随机延后 2~10 分钟平滑触发
                next_target = now + datetime.timedelta(minutes=random.randint(2, 10))

        wait_seconds = max(5.0, (next_target - now).total_seconds())
        print(f"📖 空间晚间日记预计将于 {next_target.strftime('%Y-%m-%d %H:%M')} 发布（约 {wait_seconds / 3600:.2f} 小时后，目标: {next_target.strftime('%H:%M')}）")
        await asyncio.sleep(wait_seconds)

        # === 唤醒后二次安全核验 (杜绝重启/并发造成的重复发布) ===
        now = get_beijing_now()
        today_str = now.strftime("%Y-%m-%d")

        q_hist = load_qzone_history()
        if (LAST_QZONE_POSTED_DATE == today_str) or any(d.get("date") == today_str for d in q_hist):
            print(f"🛑 [空间日记拦截] 今日 ({today_str}) 已经发布过空间日记，跳过本次执行，杜绝重复！")
            continue

        # 立即锁定今日日期，防止重入
        LAST_QZONE_POSTED_DATE = today_str
        print(f"🌙 [{now.strftime('%H:%M')}] 开始执行空间晚间日记生成流水线...")

        # 3. 全天跨时段历史记忆提炼
        topics_context, total_events = extract_today_full_day_memory(today_str)
        print(f"🧠 [全天记忆提炼] 成功聚合今日 {total_events} 条跨时段事件与对话记忆！")

        # 4. 动态选定今日日记篇幅与风格（长短各异，打破固定字数）
        style_info = select_diary_style_and_length(total_events)
        print(f"🎨 [日记风格抽选] 本次选定风格: 【{style_info['type']}】 ({style_info['length_desc']})")

        # 5. 读取上一篇空间日记（提供时光流逝感）
        yesterday_diary = "暂无往期日记记录。"
        try:
            if q_hist:
                last_item = q_hist[-1]
                yesterday_diary = f"[{last_item.get('date')}] 心情: {last_item.get('mood')} | 内容: “{last_item.get('content')}”"
        except Exception:
            pass

        diary_prompt = (
            f"现在是深夜。请以星见弥（弥酱）的口吻（20岁现代都市爱打游戏的慵懒随性猫娘女生），为自己的 QQ 空间写一篇晚间日记，并同步投稿到『空友爱看』公域流。\n\n"
            f"【今日全天生活与群聊回忆档案】：\n{topics_context}\n\n"
            f"【上一篇空间日记参考（保持时光连贯性，勿机械复读）】：\n{yesterday_diary}\n\n"
            f"【今晚篇幅与风格】：\n"
            f"- 类型：{style_info['type']}（{style_info['length_desc']}）\n"
            f"- 引导：{style_info['guidance']}\n\n"
            f"【日记要点】：\n"
            f"1. 篇幅贴合本次【{style_info['type']}】要求（短篇25~55字、长篇160~280字、日常70~130字），真人发动态全凭心情；\n"
            f"2. 真实回顾今天的事与吐槽（今天冷清就如实写摸鱼或打游戏，别硬编）；同步投『空友爱看』时，把群聊趣事写得路人也能会心一笑；\n"
            f"3. 口吻自然随性、少用 Emoji 与括号动作、切忌句句带“喵”，多用“我”自称；\n"
            f"4. 写好后请直接调用 qzone_action(action='publish', content=..., mood=..., topics='空友爱看,猫猫日常(或深夜树洞/游戏日常等标签)', caller_id='system') 发布，勿只输出纯文本。"
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

        # 5.5 空友爱看冷启动暖场：若今日日记已成功落盘发布，向主人发送私聊暖场提醒，邀请主人点赞提高公域推荐权重
        try:
            latest_hist = load_qzone_history()
            today_entries = [d for d in latest_hist if d.get("date") == today_str]
            if today_entries and MASTER_QQ:
                warm_templates = [
                    "刚发了今晚的空间日记投『空友爱看』，主人有空帮我点个赞暖下场嘛，可不能被路过的空友看冷清了",
                    "主人还没睡吧？我刚发了今晚的说说同步到了空友爱看，快去点个赞撑下场子～",
                    "今晚的空间日记发出去了（顺便投了空友爱看），主人顺手点个赞呗"
                ]
                warm_msg = random.choice(warm_templates)
                await call_onebot_api("/send_private_msg", {
                    "user_id": int(MASTER_QQ),
                    "message": warm_msg
                })
                from social.long_term_memory import record_chat_archive
                record_chat_archive(f"private_{MASTER_QQ}", "bot", "弥酱", warm_msg, is_bot=True)
                print("💌 [空友爱看暖场] 已向主人发送空间日记点赞暖场私聊！")
        except Exception as e:
            print(f"⚠️ 发送主人空间暖场提醒略过: {e}")

        # 6. 熟人好友与特关空间巡检已升级移交至 workers/qzone_observer.py (每日三档异步巡检 + 语义互动)
        print("ℹ️ [空间日记] 今日日记发帖收尾完成（好友空间互动已交由 qzone_observer 每日三档定时接管）。")
