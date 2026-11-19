"""
workers/qzone_observer.py — 弥酱 QQ 空间自主巡检、语义互动与记忆沉淀状态机

核心机制：
1. 每日三次错峰冒泡巡检（00:00 ~ 06:00 严格静默免打扰）：
   - 早间档 `08:30 ~ 09:30`
   - 午后档 `14:30 ~ 15:30`
   - 晚间档 `20:30 ~ 21:30`
   基于 `qzone_observer_state.json` 持久化档期与已读说说 `tid`，服务重启不重跑。
2. 分层名单采集：
   - 特别关注 (`social.qzone_special_care`)：主人 + 挚友(`affection >= 850`) + WebUI/弥酱自加名单，每轮必巡；
   - 普通熟人 (`affection >= 200` 且在好友列表)：每轮轮询抽样最多 5 位。
3. 零 Token 预筛：仅当存在 48 小时内未读的新说说时，才占用 `BILI_AGY_SEMAPHORE` 发起一次批量语义评估。
4. 克制社交与记忆隔离：
   - 生病/低落/emo 动态绝不傻傻点赞；
   - 提取出的计划/近况以 `source_scene="qzone_{uid}"` 写入 `pending_thoughts` 或 `mid_term_memory(private_{uid})`，绝不在无关大群当众拆台；
   - 仅当特关好友出现明显生病/低落信号、处于白天活跃时段且冷却>=6小时时，才温情发起 `trigger_ai` 私聊关心。
"""
import os
import re
import json
import time
import uuid
import random
import asyncio
import datetime
from typing import Dict, List, Any, Tuple

from config import (
    MASTER_QQ, BOT_NAMESPACE, QZONE_OBSERVER_STATE_FILE, get_transcript_path
)
from core.agy_runner import BILI_AGY_SEMAPHORE, execute_agy_inference
from core.napcat_client import call_onebot_api
from attention.persona import STATIC_PERSONA
from attention.user_mute import is_user_muted
from attention.debounce_queue import trigger_ai
from social.affection import USER_PROFILES, load_profiles, add_pending_thought
from social.mid_term_memory import add_mid_term_memory
from social.qzone import fetch_user_qzone_feeds, like_friend_feed, comment_friend_feed
from social.qzone_special_care import get_effective_special_care_map


PATROL_SLOTS: List[Tuple[str, int, int, int, int]] = [
    ("morning", 8, 30, 9, 30),
    ("afternoon", 14, 30, 15, 30),
    ("evening", 20, 30, 21, 30),
]

ORDINARY_MIN_AFFECTION = 200.0
ORDINARY_SAMPLE_LIMIT = 5
FEED_FRESHNESS_SEC = 48 * 3600
PRIVATE_CARE_COOLDOWN_SEC = 6 * 3600
SEEN_TID_MAX_KEEP = 1200


def is_qzone_quiet_hours(now_utc8: datetime.datetime) -> bool:
    """00:00 ~ 05:59 为夜间静默期，不巡检不私聊打扰。"""
    return 0 <= now_utc8.hour < 6


def load_observer_state() -> Dict[str, Any]:
    """加载 QQ 空间巡检持久化状态。"""
    default_state: Dict[str, Any] = {
        "completed_slots": {},
        "seen_tids": {},
        "private_care_last_ts": {},
        "ordinary_cursor": 0,
        "recent_logs": [],
        "last_patrol_ts": 0.0,
    }
    if os.path.exists(QZONE_OBSERVER_STATE_FILE):
        try:
            with open(QZONE_OBSERVER_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "completed_slots": data.get("completed_slots") if isinstance(data.get("completed_slots"), dict) else {},
                    "seen_tids": data.get("seen_tids") if isinstance(data.get("seen_tids"), dict) else {},
                    "private_care_last_ts": data.get("private_care_last_ts") if isinstance(data.get("private_care_last_ts"), dict) else {},
                    "ordinary_cursor": int(data.get("ordinary_cursor", 0) or 0),
                    "recent_logs": data.get("recent_logs") if isinstance(data.get("recent_logs"), list) else [],
                    "last_patrol_ts": float(data.get("last_patrol_ts", 0.0) or 0.0),
                }
        except Exception:
            pass
    return default_state


def save_observer_state(state: Dict[str, Any]) -> None:
    """原子写入 QQ 空间巡检状态（自动裁剪历史记录防止无限膨胀）。"""
    try:
        # 裁剪完成档期只保留近 14 天
        slots = state.get("completed_slots") or {}
        if len(slots) > 14:
            for k in sorted(slots.keys())[:-14]:
                slots.pop(k, None)

        # 裁剪 seen_tids
        seen = state.get("seen_tids") or {}
        if len(seen) > SEEN_TID_MAX_KEEP:
            sorted_items = sorted(seen.items(), key=lambda kv: float(kv[1] or 0))
            state["seen_tids"] = dict(sorted_items[-SEEN_TID_MAX_KEEP:])

        # 裁剪 logs 最多 30 条
        logs = state.get("recent_logs") or []
        if len(logs) > 30:
            state["recent_logs"] = logs[-30:]

        tmp_path = QZONE_OBSERVER_STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, QZONE_OBSERVER_STATE_FILE)
    except Exception as e:
        print(f"⚠️ [qzone_observer] 保存巡检状态失败: {e}")


async def collect_patrol_targets(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    分层汇总本轮需要查看空间的目标好友：
    1) 全部生效的【特别关注】用户（每轮必巡）；
    2) 普通熟人好友（affection >= 200，按游标轮转抽取最多 ORDINARY_SAMPLE_LIMIT 位）。
    """
    if not USER_PROFILES:
        try:
            load_profiles()
        except Exception:
            pass

    special_map = get_effective_special_care_map()

    friend_nicks: Dict[str, str] = {}
    friend_list_ok = False
    try:
        f_data = await call_onebot_api("/get_friend_list")
        if f_data.get("status") == "ok" and isinstance(f_data.get("data"), list):
            friend_list_ok = True
            for fr in f_data["data"]:
                uid = str(fr.get("user_id", "")).strip()
                if uid:
                    friend_nicks[uid] = str(fr.get("remark") or fr.get("nickname") or "").strip()
    except Exception:
        pass

    targets: List[Dict[str, Any]] = []
    seen_uids = set()

    # 1. 特关全量
    for uid, info in special_map.items():
        if not uid or not uid.isdigit():
            continue
        seen_uids.add(uid)
        targets.append({
            "uin": uid,
            "nickname": friend_nicks.get(uid) or info.get("nickname") or f"好友_{uid}",
            "affection": int(info.get("affection", 0) or 0),
            "is_special_care": True,
            "care_source": info.get("source", "manual"),
            "care_reason": info.get("reason", ""),
        })

    # 2. 普通熟人轮转抽样
    ordinary_pool: List[str] = []
    candidate_uids = list(friend_nicks.keys()) if friend_list_ok else list(USER_PROFILES.keys())
    for uid in sorted(set(str(u) for u in candidate_uids)):
        if uid in seen_uids or not uid.isdigit():
            continue
        prof = USER_PROFILES.get(uid, {})
        aff = float(prof.get("affection", 0.0) or 0.0)
        if aff >= ORDINARY_MIN_AFFECTION:
            ordinary_pool.append(uid)

    if ordinary_pool:
        cursor = int(state.get("ordinary_cursor", 0) or 0) % len(ordinary_pool)
        picked: List[str] = []
        for i in range(min(ORDINARY_SAMPLE_LIMIT, len(ordinary_pool))):
            picked.append(ordinary_pool[(cursor + i) % len(ordinary_pool)])
        state["ordinary_cursor"] = (cursor + len(picked)) % len(ordinary_pool)

        for uid in picked:
            prof = USER_PROFILES.get(uid, {})
            targets.append({
                "uin": uid,
                "nickname": friend_nicks.get(uid) or prof.get("nickname") or f"好友_{uid}",
                "affection": int(prof.get("affection", 0) or 0),
                "is_special_care": False,
                "care_source": "ordinary",
                "care_reason": "",
            })

    return targets


def filter_unseen_fresh_feeds(
    feeds: List[Dict[str, Any]],
    seen_tids: Dict[str, Any],
    now_ts: float,
) -> List[Dict[str, Any]]:
    """零 Token 前置过滤：过滤已看过的 tid、超过 48 小时的旧动态以及纯空动态。"""
    fresh: List[Dict[str, Any]] = []
    for f in feeds or []:
        tid = str(f.get("tid", "")).strip()
        if not tid or tid in seen_tids:
            continue
        created_ts = int(f.get("created_time", 0) or 0)
        if created_ts > 0 and (now_ts - created_ts) > FEED_FRESHNESS_SEC:
            continue
        content = str(f.get("content", "") or "").strip()
        if not content and not f.get("has_images"):
            continue
        fresh.append(f)
    return fresh


def parse_qzone_eval_json(raw_text: str) -> List[Dict[str, Any]]:
    """从模型响应中解析 QQ 空间批量决策 JSON 数组。"""
    if not raw_text or not isinstance(raw_text, str):
        return []
    candidates = []
    m_fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", raw_text)
    if m_fence:
        candidates.append(m_fence.group(1))
    m_arr = re.search(r"(\[[\s\S]*\])", raw_text)
    if m_arr:
        candidates.append(m_arr.group(1))

    for cand in candidates:
        cleaned = re.sub(r'(?<!:)(?<!["\w])//[^\n]*', "", cand)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except Exception:
            continue
    return []


async def execute_patrol_decisions(
    decisions: List[Dict[str, Any]],
    feed_lookup: Dict[Tuple[str, str], Dict[str, Any]],
    state: Dict[str, Any],
    now_utc8: datetime.datetime,
    now_ts: float,
) -> Dict[str, int]:
    """执行点赞、克制评论、写入带 source_scene 隔离的记忆，以及特关私聊暖心关怀。"""
    stats = {"likes": 0, "comments": 0, "memories": 0, "private_care": 0}
    care_cooldowns = state.setdefault("private_care_last_ts", {})

    for dec in decisions:
        uin = str(dec.get("uin", "")).strip()
        tid = str(dec.get("tid", "")).strip()
        ctx = feed_lookup.get((uin, tid))
        if not ctx:
            continue

        nick = ctx.get("nickname", uin)
        is_special = bool(ctx.get("is_special_care", False))
        feed_content = str(ctx.get("content", ""))

        # 1. 点赞（若模型请求需要私聊安慰或属于负面危机信号，强制防御不点赞）
        should_like = bool(dec.get("should_like", False))
        need_care = bool(dec.get("need_private_care", False))
        if should_like and not need_care:
            l_res = await like_friend_feed(uin, tid)
            if l_res.get("success"):
                stats["likes"] += 1
                print(f"👍 [空间巡检] 已给 {'特关 ' if is_special else ''}{nick}({uin}) 的说说(tid={tid}) 点赞")

        # 2. 评论
        should_comment = bool(dec.get("should_comment", False))
        comment_text = str(dec.get("comment_text", "") or "").replace("\n", " ").strip().strip('"\'')
        if should_comment and 2 <= len(comment_text) <= 80:
            c_res = await comment_friend_feed(uin, tid, comment_text)
            if c_res.get("success"):
                stats["comments"] += 1
                print(f"💬 [空间巡检] 已评论 {nick}({uin}): “{comment_text}”")

        # 3. 记忆沉淀（严格带 source_scene=f"qzone_{uin}" 防大群泄露）
        mem_type = str(dec.get("memory_type", "none") or "none").strip().lower()
        mem_text = str(dec.get("memory_text", "") or "").strip()
        scene_tag = f"qzone_{uin}"
        if mem_text and mem_type == "pending_thought":
            add_pending_thought(user_id_str=uin, thought=f"[空间动态] {mem_text}", expire_days=3, source_scene=scene_tag)
            stats["memories"] += 1
            print(f"💭 [空间记忆] 已为 {nick}({uin}) 记录空间挂念(source_scene={scene_tag}): {mem_text}")
        elif mem_text and mem_type == "mid_term":
            add_mid_term_memory(
                target_id=uin,
                text=f"{nick}在QQ空间发动态: {mem_text}",
                importance=0.72 if is_special else 0.58,
                tags=["QQ空间", nick, uin],
                source_scene=scene_tag,
            )
            stats["memories"] += 1
            print(f"📝 [空间记忆] 已为 {nick}({uin}) 写入私聊中程记忆(source_scene={scene_tag}): {mem_text}")

        # 4. Option 3: 仅针对特关好友 + 非免打扰 + 白天活跃段(07:30~23:30) + >=6h 冷却 触发私聊关心
        if need_care and is_special:
            care_reason = str(dec.get("care_reason", "") or "空间动态流露出状态不太好").strip()
            last_care = float(care_cooldowns.get(uin, 0.0) or 0.0)
            is_daytime = (7, 30) <= (now_utc8.hour, now_utc8.minute) <= (23, 30)
            if is_daytime and not is_user_muted(uin) and (now_ts - last_care) >= PRIVATE_CARE_COOLDOWN_SEC:
                care_hint = (
                    f"【QQ空间特别关注·私聊关怀触发】\n"
                    f"你刚刚刷 QQ 空间时看到特别关注的好友【{nick}】(QQ:{uin})发了一条让人有点担心的说说：\n"
                    f"原文摘录：“{feed_content[:120]}”\n"
                    f"担心原因：{care_reason}\n"
                    f"【表达要求】：用你最自然温柔的朋友/小猫口吻主动去私聊轻轻关心他一下，不要生硬说“系统监测到你发说说”，"
                    f"可以说“刚刷空间好像看到你发的动态有点难受/不太对劲呀…”自然递个台阶让他想聊就聊聊，不想聊也不逼问。"
                )
                asyncio.create_task(
                    trigger_ai(
                        chat_id_str=f"private_{uin}",
                        group_id=0,
                        sender_id=uin,
                        combined_message=f"[系统事件: 弥酱刷QQ空间看到特关好友{nick}({uin})心情低落，主动来私聊轻声关心]",
                        is_private=True,
                        must_reply=True,
                        ephemeral_context=care_hint,
                    )
                )
                care_cooldowns[uin] = now_ts
                stats["private_care"] += 1
                print(f"💌 [特关关怀] 已触发向 {nick}({uin}) 的温柔私聊关心（冷却锁定 6 小时）")

    return stats


async def run_qzone_patrol_once(
    slot_name: str = "manual",
    force: bool = False,
) -> Dict[str, Any]:
    """
    执行一次完整的 QQ 空间巡检（可供定时档期与测试/手动调用）。
    """
    now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    now_ts = time.time()
    today_str = now_utc8.strftime("%Y-%m-%d")

    if not force and is_qzone_quiet_hours(now_utc8):
        return {"ok": True, "skipped": True, "reason": "quiet_hours"}

    state = load_observer_state()
    if not force and slot_name != "manual":
        today_done = state.setdefault("completed_slots", {}).get(today_str, [])
        if slot_name in today_done:
            return {"ok": True, "skipped": True, "reason": "slot_already_completed"}

    targets = await collect_patrol_targets(state)
    if not targets:
        save_observer_state(state)
        return {"ok": True, "skipped": True, "reason": "no_targets"}

    seen_tids = state.setdefault("seen_tids", {})
    collected_items: List[Dict[str, Any]] = []
    feed_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for tgt in targets:
        uin = tgt["uin"]
        res = await fetch_user_qzone_feeds(uin, count=6)
        if not res.get("ok"):
            if res.get("permission_denied"):
                print(f"🔒 [空间巡检] {tgt['nickname']}({uin}) 空间权限受限，本轮跳过")
            await asyncio.sleep(random.uniform(0.3, 0.7))
            continue

        fresh_feeds = filter_unseen_fresh_feeds(res.get("feeds", []), seen_tids, now_ts)
        for f in fresh_feeds[:3]:
            tid = str(f.get("tid", "")).strip()
            item = {
                "uin": uin,
                "tid": tid,
                "nickname": tgt["nickname"],
                "affection": tgt["affection"],
                "is_special_care": tgt["is_special_care"],
                "care_reason": tgt["care_reason"],
                "content": f.get("content", ""),
                "has_images": bool(f.get("has_images", False)),
                "image_count": int(f.get("image_count", 0) or 0),
                "like_count": int(f.get("like_count", 0) or 0),
            }
            collected_items.append(item)
            feed_lookup[(uin, tid)] = item
        await asyncio.sleep(random.uniform(0.4, 1.0))

    # 零新动态时零 Token 退出，但仍记录完成本档期避免重复轮询刷 QZone API
    if not collected_items:
        if slot_name != "manual":
            done_list = state.setdefault("completed_slots", {}).setdefault(today_str, [])
            if slot_name not in done_list:
                done_list.append(slot_name)
        state["last_patrol_ts"] = now_ts
        state.setdefault("recent_logs", []).append({
            "ts": now_ts,
            "time_str": now_utc8.strftime("%Y-%m-%d %H:%M"),
            "slot": slot_name,
            "targets_checked": len(targets),
            "feeds_found": 0,
            "likes": 0,
            "comments": 0,
            "memories": 0,
            "private_care": 0,
            "summary": "巡检一圈暂无 48h 内未读新说说（0 Token 退出）",
        })
        save_observer_state(state)
        print(f"🌙 [空间巡检] 本轮[{slot_name}]检查了 {len(targets)} 位好友，暂无未读新说说，0 Token 静默结束。")
        return {"ok": True, "targets_checked": len(targets), "feeds_found": 0, "stats": {"likes": 0, "comments": 0, "memories": 0, "private_care": 0}}

    # 阶段 3: 单次批量语义决策 (BILI_AGY_SEMAPHORE + medium)
    feed_blocks = []
    for idx, it in enumerate(collected_items[:12], 1):
        role_tag = "【特别关注】" if it["is_special_care"] else f"【熟人(好感{it['affection']})】"
        pic_tag = f" [附带{it['image_count']}张图]" if it["has_images"] else ""
        feed_blocks.append(
            f"[{idx}] {role_tag} {it['nickname']}(uin:{it['uin']}) | tid:{it['tid']}{pic_tag} | 赞:{it['like_count']}\n"
            f"    正文: {it['content'][:220] or '(纯图片动态)'}"
        )

    eval_prompt = (
        f"{STATIC_PERSONA}\n\n"
        "【情境设定】：你现在打开了自己的 QQ 空间好友动态，刚刷到下面这些朋友近两天发的新说说。请以星见弥（弥酱）的真实性格做出社交互动与记忆沉淀判断。\n\n"
        "【待阅说说清单】：\n" + "\n".join(feed_blocks) + "\n\n"
        "【输出规范】：请严格输出纯 JSON 数组（严禁使用 markdown 围栏，严禁 // 注释，每条输入对应输出一个对象）：\n"
        "[\n"
        "  {\n"
        '    "uin": "好友QQ号",\n'
        '    "tid": "说说tid",\n'
        '    "should_like": true,\n'
        '    "should_comment": false,\n'
        '    "comment_text": "若决定评论写在这里（10~35字，口语自然像真朋友，不要机械捧场，末尾别带句号），否则空串",\n'
        '    "memory_type": "none 或 pending_thought 或 mid_term",\n'
        '    "memory_text": "值得记住的近况/日程约定（如‘周末去上海漫展’‘下周四考驾照’），无意义日常填空串",\n'
        '    "need_private_care": false,\n'
        '    "care_reason": "仅当对方是【特别关注】且明显生病、受伤、悲伤崩溃时填原因，普通吐槽/小丧填false"\n'
        "  }\n"
        "]\n"
        "重要社交守则：\n"
        "1. 绝不给生病、受伤、亲人变故、强烈悲伤的内容点赞（should_like 必须 false）！\n"
        "2. 评论保持克制真实：每轮最多挑最有感触的 1~3 条评论，没必要每条都硬评；熟人客气自然，特关可以更俏皮亲近。\n"
        "3. `pending_thought` 用于短期内能挂念/问候的事；`mid_term` 用于长期近况变化；纯晒晚饭或水动态写 `none`。"
    )

    conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"qzone_patrol_{today_str}_{slot_name}_{int(now_ts)}"))
    decisions: List[Dict[str, Any]] = []
    try:
        await execute_agy_inference(
            chat_id_str="qzone_observer_channel",
            prompt=eval_prompt,
            dynamic_conv_id=conv_id,
            messages_list=[eval_prompt],
            custom_semaphore=BILI_AGY_SEMAPHORE,
            target_domains=["none"],
            effort="medium",
        )
        t_path = get_transcript_path(conv_id)
        if os.path.exists(t_path):
            with open(t_path, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if entry.get("type") == "PLANNER_RESPONSE":
                        parsed = parse_qzone_eval_json(entry.get("content", ""))
                        if parsed:
                            decisions = parsed
                            break
    except Exception as e:
        print(f"⚠️ [qzone_observer] 语义评估推理异常: {e}")

    # 无论本次决策几条，将本批送审的 tid 标记为已阅，防止反复扫同一条说说
    for it in collected_items[:12]:
        seen_tids[it["tid"]] = now_ts

    stats = await execute_patrol_decisions(decisions, feed_lookup, state, now_utc8, now_ts)

    if slot_name != "manual":
        done_list = state.setdefault("completed_slots", {}).setdefault(today_str, [])
        if slot_name not in done_list:
            done_list.append(slot_name)

    state["last_patrol_ts"] = now_ts
    state.setdefault("recent_logs", []).append({
        "ts": now_ts,
        "time_str": now_utc8.strftime("%Y-%m-%d %H:%M"),
        "slot": slot_name,
        "targets_checked": len(targets),
        "feeds_found": len(collected_items),
        **stats,
        "summary": (
            f"查看 {len(targets)} 位好友，发现 {len(collected_items)} 条新动态 | "
            f"赞 {stats['likes']} · 评 {stats['comments']} · 记 {stats['memories']} · 私聊关怀 {stats['private_care']}"
        ),
    })
    save_observer_state(state)
    print(f"✅ [空间巡检] 本轮[{slot_name}]完成: {state['recent_logs'][-1]['summary']}")
    return {
        "ok": True,
        "targets_checked": len(targets),
        "feeds_found": len(collected_items),
        "stats": stats,
    }


def _pick_due_slot(now_utc8: datetime.datetime, completed_today: List[str], daily_jitter: Dict[str, datetime.datetime]) -> str | None:
    """检查当前时刻是否已到达今天某个尚未执行的空间巡检档期。"""
    if is_qzone_quiet_hours(now_utc8):
        return None
    today_str = now_utc8.strftime("%Y-%m-%d")
    for slot_name, h1, m1, h2, m2 in PATROL_SLOTS:
        if slot_name in completed_today:
            continue
        key = f"{today_str}_{slot_name}"
        if key not in daily_jitter:
            start_dt = now_utc8.replace(hour=h1, minute=m1, second=0, microsecond=0)
            end_dt = now_utc8.replace(hour=h2, minute=m2, second=0, microsecond=0)
            span_sec = max(60, int((end_dt - start_dt).total_seconds()))
            daily_jitter[key] = start_dt + datetime.timedelta(seconds=random.randint(0, span_sec))
        target_dt = daily_jitter[key]
        # 若当前已过随机触发点（且仍在当日 23:55 之前），则触发该档期
        if now_utc8 >= target_dt and (now_utc8.hour < 23 or now_utc8.minute <= 55):
            return slot_name
    return None


async def qzone_patrol_worker():
    """
    QQ 空间每天三次冒泡巡检常驻协程：
    - 00:00 ~ 06:00 严禁冒泡打扰；
    - 每日三档：08:30~09:30 / 14:30~15:30 / 20:30~21:30（约每 6 小时一次）。
    """
    print("🔭 [QQ空间巡检] 已上线，每日三档(08:30/14:30/20:30附近，凌晨00:00~06:00静默)守护特关与熟人空间...")
    await asyncio.sleep(35)
    daily_jitter: Dict[str, datetime.datetime] = {}

    while True:
        try:
            now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
            today_str = now_utc8.strftime("%Y-%m-%d")

            # 清理过期的抖动时间表
            for k in list(daily_jitter.keys()):
                if not k.startswith(today_str):
                    daily_jitter.pop(k, None)

            if is_qzone_quiet_hours(now_utc8):
                wake_dt = now_utc8.replace(hour=6, minute=5, second=0, microsecond=0)
                sleep_sec = max(120, (wake_dt - now_utc8).total_seconds())
                print(f"🌙 [QQ空间巡检] 处于 00:00~06:00 夜间静默期，休眠 {sleep_sec/3600:.1f} 小时至清晨。")
                await asyncio.sleep(sleep_sec)
                continue

            state = load_observer_state()
            completed_today = state.get("completed_slots", {}).get(today_str, [])
            due_slot = _pick_due_slot(now_utc8, completed_today, daily_jitter)
            if due_slot:
                await run_qzone_patrol_once(slot_name=due_slot, force=False)
                await asyncio.sleep(300)
            else:
                await asyncio.sleep(300)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [qzone_observer] 运行循环异常: {e}")
            await asyncio.sleep(180)
