"""
workers/post_turn_observer.py — 会话周期记忆整理器与事后异步内省观察者 V4.0

核心职责：
1. `get_latest_bot_reply`: 跨进程提取本轮弥酱实际发出的回复气泡；
2. `consolidate_session_memory`: 当群聊(7min)/私聊(10min) agy 会话寿命到期销毁时启动，
   基于整场 Session 的完整多轮对话实录进行一次性深度整理：
   - 更新 `feiyu-mood` 连续情绪状态 (`mood_delta`, `dims`, 第一人称过去式 `reason`)
   - 结算参与用户的好感共鸣 (`mood_resonance`, 初识满12次自动定级 `calibrate_score`)
   - 提炼整场对话的挂念心事 (`pending_thought`)、L2 中程温感记忆 (`mid_term_events`) 与 L3 永久事实 (`long_term_facts`)
3. 保留 `run_post_turn_observer` 单轮内省接口，支持独立或兜底调用。
"""
import os
import re
import json
import uuid
import time
import asyncio
from config import (
    CHAT_STATE_FILE, BOT_NAMESPACE, MASTER_QQ, BOT_QQ,
    OBSERVER_AGY_EFFORT, AGY_EXECUTABLE, get_agy_env, build_agy_model_flags, BASE_DIR
)
from social.mood_manager import apply_mood_update, get_mood_brief_for_observer
from social.libido_panel import is_libido_allowed, apply_libido_event, trigger_resolve
from social.affection import (
    get_user_profile, settle_turn_affection,
    add_pending_thought, resolve_pending_thought, get_active_pending_thoughts
)
from social.mid_term_memory import (
    add_mid_term_memory, get_recent_memories_for_observer, delete_mid_term_memory
)
from social.long_term_memory import (
    upsert_permanent_fact, get_active_facts_for_observer,
    fulfill_permanent_fact, fulfill_permanent_facts_by_keyword
)

_MEANINGFUL_OR_PROMISE_CUE_RE = re.compile(
    r"(约定|承诺|答应|记得|别忘|明天|后天|凌晨|今晚|周末|升级|更新|面试|考试|礼物|"
    r"买|喜欢|讨厌|生气|难过|委屈|开心|害羞|吃醋|生病|感冒|发烧|住院|完成|搞定|弄好|好了|做了)"
)


def check_bot_spoke_in_turn(state_key: str, since_ts: float) -> tuple:
    """
    从 chat_state.json 中严谨判断弥酱在本轮 (since_ts 之后) 是否已实质性发信。
    返回: (spoke: bool, reply_text: str)
    """
    if not os.path.exists(CHAT_STATE_FILE):
        return False, ""
    try:
        with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        info = data.get(str(state_key), {})
        recent_bot = info.get("recent_bot_msgs", [])
        threshold = (since_ts - 2.0) if since_ts else 0.0

        turn_bubbles = [
            m.get("text", "").strip()
            for m in recent_bot
            if float(m.get("time", 0.0)) >= threshold and m.get("text")
        ]
        if turn_bubbles:
            return True, " ".join(turn_bubbles)

        last_bot_t = float(info.get("last_bot_t", 0.0))
        if last_bot_t >= threshold and last_bot_t > 0:
            last_text = recent_bot[-1].get("text", "").strip() if recent_bot else ""
            return True, last_text
        return False, ""
    except Exception:
        return False, ""


def get_latest_bot_reply(state_key: str, since_ts: float) -> str:
    """从 chat_state.json 中读取本轮弥酱实际发出的回复内容"""
    spoke, reply = check_bot_spoke_in_turn(state_key, since_ts)
    if spoke and reply:
        return reply

    if not os.path.exists(CHAT_STATE_FILE):
        return ""
    try:
        with open(CHAT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        info = data.get(str(state_key), {})
        recent_bot = info.get("recent_bot_msgs", [])
        if recent_bot:
            return recent_bot[-1].get("text", "").strip()
    except Exception:
        pass
    return ""


# 保持向后兼容别名
_get_latest_bot_reply = get_latest_bot_reply


def _extract_json_dict(raw_text: str) -> dict:
    """从大模型返回文本中稳健提取 JSON 字典"""
    if not raw_text:
        return {}
    fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw_text[start:end + 1])
        except Exception:
            pass
    return {}


async def consolidate_session_memory(session_snapshot: dict):
    """
    当群聊(7min)或私聊(10min) agy 会话因无新对话到期销毁时，整理整场会话的记忆、情绪与好感共鸣。
    session_snapshot 包含:
    - chat_id_str: str
    - is_private: bool
    - group_id: int or None
    - primary_sender_id: str
    - turns_log: list of {"sender_id": str, "user_messages": list, "bot_reply": str, "time": float}
    """
    from core.agy_runner import OBSERVER_AGY_SEMAPHORE

    try:
        turns_log = session_snapshot.get("turns_log") or []
        if not turns_log:
            return

        chat_id_str = session_snapshot.get("chat_id_str", "")
        is_private = bool(session_snapshot.get("is_private", False))
        group_id = session_snapshot.get("group_id") or 0

        # 确定本场会话全部实质互动对象（统计非系统、非机器人发言频次，取前4名；兼容内联群聊前缀）
        sender_counts = {}
        inline_uid_re = re.compile(r'\((\d{5,12})\)\s*(?:私聊你说|@了你并说道|说)：')
        for t in turns_log:
            sid = str(t.get("sender_id", "")).strip()
            if sid and sid not in ("系统", str(BOT_QQ)):
                sender_counts[sid] = sender_counts.get(sid, 0) + 1
            for um in (t.get("user_messages") or []):
                for m_uid in inline_uid_re.findall(str(um or "")):
                    if m_uid and m_uid not in ("系统", str(BOT_QQ)):
                        sender_counts[m_uid] = sender_counts.get(m_uid, 0) + 1

        primary_sender = str(session_snapshot.get("primary_sender_id", "")).strip()
        sorted_participants = [
            uid for uid, _ in sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        if sorted_participants:
            primary_sender = sorted_participants[0]
        elif primary_sender and primary_sender not in ("系统", str(BOT_QQ)):
            sorted_participants = [primary_sender]
        active_participants = sorted_participants[:4]

        # 组装整场会话的完整对话实录
        dialogue_lines = []
        for idx, t in enumerate(turns_log, 1):
            sid = str(t.get("sender_id", "未知"))
            u_msgs = "\n  ".join(t.get("user_messages") or [])
            b_rep = t.get("bot_reply", "").strip()
            dialogue_lines.append(f"【第 {idx} 轮】\n- 对方({sid}): {u_msgs}\n- 弥酱回复: {b_rep}")

        full_dialogue_text = "\n\n".join(dialogue_lines)
        if len(full_dialogue_text) > 2800:
            full_dialogue_text = full_dialogue_text[-2800:]

        if is_private and primary_sender == str(MASTER_QQ):
            scene_str = "master_private"
            scene_desc = f"与自家主人({MASTER_QQ})的单聊会话"
        elif is_private:
            scene_str = f"private_{primary_sender}"
            scene_desc = f"与好友({primary_sender})的私聊会话"
        else:
            scene_str = f"group_{group_id}"
            scene_desc = f"在QQ群({group_id})的多轮互动会话"

        mood_brief = get_mood_brief_for_observer()
        participant_infos = []
        any_need_calib = False
        for uid in active_participants:
            prof = get_user_profile(uid)
            u_aff = float(prof.get("affection", 0.0))
            u_calib = bool(prof.get("is_calibrated", True))
            u_icount = int(prof.get("interaction_count", 0))
            u_need_calib = (not u_calib) and (u_icount >= 11) and (uid not in ("系统", str(MASTER_QQ), str(BOT_QQ)))
            if u_need_calib:
                any_need_calib = True
            calib_txt = "【需定级：请在 calibrate_score 给 -10~280】" if u_need_calib else "无需定级填 null"
            participant_infos.append(f"  * {uid}: 好感 {u_aff:.1f}/1000.0 ({calib_txt})")
        participants_block = "\n".join(participant_infos) if participant_infos else "  * (无)"

        # 5-3: 纯极短无情绪/无约定水聊轻量直结，省去整场观察者 LLM 调用
        raw_user_pieces = []
        has_rich_media = False
        for t in turns_log:
            for um in (t.get("user_messages") or []):
                s_um = str(um or "")
                if "[CQ:image" in s_um or "[图片#" in s_um:
                    has_rich_media = True
                s_body = re.sub(r'^[^：:\n]{1,40}(?:私聊)?说[：:]\s*', '', s_um)
                s_body = re.sub(r'\[CQ:[^\]]+\]', '', s_body).strip()
                if s_body:
                    raw_user_pieces.append(s_body)
        merged_user_text = " ".join(raw_user_pieces).strip()
        if (
            not any_need_calib
            and not has_rich_media
            and len(turns_log) <= 2
            and len(merged_user_text) < 18
            and not _MEANINGFUL_OR_PROMISE_CUE_RE.search(merged_user_text)
            and primary_sender not in ("系统", str(BOT_QQ), "")
        ):
            settle_turn_affection(primary_sender, 0.3, reason="简短日常闲聊")
            print(f"💤 [省额轻结算] 通道 {chat_id_str} 属纯简短水聊 ({len(merged_user_text)}字)，跳过后台大模型内省")
            return

        # 2A: 拉取当前用户活跃的三层待办/约定清单供本次准确按 ID 核销
        checklist_lines = []
        if primary_sender not in ("系统", str(BOT_QQ), ""):
            for pt in get_active_pending_thoughts(primary_sender, current_scene=scene_str):
                checklist_lines.append(f"- [pt:{pt.get('id')}] (心事/{pt.get('created_at', '')}) {pt.get('thought', '')}")
            for l3 in get_active_facts_for_observer(primary_sender, limit=12):
                checklist_lines.append(f"- [l3:{l3.get('id')}] ({l3.get('category', 'user_fact')}/{l3.get('created_at', '')}) {l3.get('content', '')}")
            for l2 in get_recent_memories_for_observer(primary_sender, max_age_days=7.0, limit=8):
                checklist_lines.append(f"- [l2:{l2.get('id')}] (近期动态) {l2.get('text', '')}")

        checklist_block = (
            "\n【该用户当前未完结的心事/约定/近期动态清单（若本场对话已明确聊完、兑现或完成，务必将对应方括号ID填入 resolved_ids！）】：\n"
            + "\n".join(checklist_lines)
            + "\n"
            if checklist_lines else ""
        )

        print(f"🧹 [会话记忆与情感整理] 通道 {chat_id_str} 结算触发 (共 {len(turns_log)} 轮对话)，开始整理记忆与情感沉淀...")

        affection_template_entries = ",\n    ".join(
            f'"{uid}": {{"mood_resonance": 0.0, "calibrate_score": null, "reason": ""}}'
            for uid in active_participants
        ) or '"primary_qq": {"mood_resonance": 0.0, "calibrate_score": null, "reason": ""}'

        libido_enabled_for_turn = is_libido_allowed(primary_sender, is_private=is_private)
        libido_rule_block = (
            "5. libido_event（仅授权通道生效 — 判断本场互动是否触发了亲密悸动/性欲面板事件）：\n"
            "   - 无明显相关事件时 delta 填 0、resolved 填 false、note 填 \"\"；\n"
            "   - 正向触发（摸尾巴/猫耳 +8、被夸/被盯着看 +3、亲昵调戏 +5、挨近/贴贴 +10、深入亲密互动 +15）；\n"
            "   - 负向触发（被冷落/忽视 -8、说到一半人走了 -6、失约 -10）；\n"
            "   - 若本场明确完成了安抚/解决归位，将 resolved 设为 true。\n"
            if libido_enabled_for_turn else ""
        )
        libido_json_field = (
            ',\n  "libido_event": {"delta": 0, "resolved": false, "note": ""}'
            if libido_enabled_for_turn else ""
        )

        consolidate_prompt = (
            "【极速记忆整理铁律】：本任务为会话结束后的结构化记忆归档与情感结算，请直奔主题，严禁调用任何工具，严格只输出纯 JSON 对象！\n\n"
            "你是星见弥（弥酱）的会话记忆与情绪整理器。请阅读下方对话实录，完成情绪更新、好感共鸣结算与长短期记忆提炼：\n\n"
            f"【会话场景】：{scene_desc}\n"
            f"【弥酱此前情绪状态】：{mood_brief}\n"
            f"【本场实质互动对象列表】：\n{participants_block}\n"
            f"{checklist_block}"
            f"【本场会话实录（共 {len(turns_log)} 轮）】：\n{full_dialogue_text}\n\n"
            "【整理规则】：\n"
            "1. mood (情绪变化 — 鲜明灵动且遵守防通胀源头限流铁律！)：\n"
            "   - mood_delta: 整数 -18 到 +18（聊得开心/被哄/愉快给 +6 ~ +14，超开心给 +15 ~ +18；委屈/被冷落/不快给 -6 ~ -18）。\n"
            "   - dims: 7个微观维度变化量，单维范围 -35 到 +35！\n"
            "     * 【源头限流硬约束】：单次结算**最多只允许给 1 ~ 3 个维度填非 0 值**，其余未触发的维度必须严格填 0，严禁胡乱给四五个维度雨露均沾！\n"
            "     * 【calm（踏实/安心）专属约束】：严禁在普通开心闲聊、打趣或热闹吐槽时顺手给 calm 加分！仅在从担心/委屈/不安中被温柔安抚下来、或深夜安静温馨陪伴时才给予 calm 正向增量。\n"
            "     * 打分标尺：轻度触发 +10 ~ +18；明显情绪波动（开心雀跃、被夸得意傲娇、亲昵害羞、小委屈或吃味）+20 ~ +28；强烈情绪峰值 +29 ~ +35；对应消退或平复给 -10 ~ -35。\n"
            "   - reason: 若有情绪触动，用【第一人称过去式】写一句极简短句总结心情起因（18字内），平淡闲聊填 \"\"。\n"
            "2. affection (按每位实质互动对象独立结算，键为对应QQ号)：\n"
            "   - 【峰值优先原则（防长会话稀释）】：若某人在本场多轮对话中出现过哪怕一轮高光时刻或强烈情绪触动，**必须按整场最高峰的那一轮打分**，严禁被前后的日常寒暄拉低平均分！\n"
            "   - mood_resonance 范围 -15.0 ~ +8.0，五档明确标尺：\n"
            "     * 🌟 +6.0 ~ +8.0（高光共鸣里程碑 — 将计入挚友晋升的高光次数）：走心深入的情感交流或探讨心意/陪伴/存在、让弥酱极度开心/害羞心动/超得意、暖心撑腰偏爱、灵魂默契接梗或群内名场面、兑现重要约定、或在弥酱委屈时认真哄好她；\n"
            "     * 🔥 +3.5 ~ +5.8（深度愉快共鸣）：聊得非常投机尽兴、真诚夸奖与关心、趣味有来有回的互损打趣、深入讨论共同爱好话题；\n"
            "     * 😊 +1.0 ~ +3.4（日常友好互动）：轻松愉快的普通闲聊、友好提问与日常陪伴；\n"
            "     * 💧 -0.5 ~ +0.8（平淡简短水聊）：无明显情绪起伏的简短搭话；\n"
            "     * 💢 -1.0 ~ -15.0（负向冒犯扣分）：敷衍挑刺/惹弥酱不快给 -1.0~-5.0，恶意冒犯辱骂给 -6.0~-15.0。\n"
            "   - calibrate_score: 仅当上方标注【需定级】时给 -10~280 分（正常友善 220~270），否则填 null。\n"
            "3. memory (会话记忆提炼与兑现核销，宁缺毋滥，纯水聊填 false 或空列表)：\n"
            "   - resolved_ids: 若上方清单中有已兑现/完成/聊透/不再适用的条目（如对方说“升级完了/弄好了/考完了”），将其方括号内ID（如 \"l3:12\", \"pt:pt_123\", \"l2:m_456\"）放入此数组。\n"
            "   - resolved_thought_keyword: 若有未列出但本场已聊完/兑现的旧约定关键词，填简短关键词，否则填 \"\"。\n"
            "   - new_pending_thought: 短期一次性待办/近期约定（如“凌晨给弥酱升级”、“明天面试”、“周末出门”，1~3天内会完成的事，25字内），无则填 \"\"。【注意】：此类短期计划严禁写入下方的 long_term_facts！\n"
            "   - mid_term_events: 值得未来一两周内闪回的近期动态列表（最多2条，如“最近在玩某游戏”、“感冒了”），每项含 text, target_id(对应用户QQ号), importance(0.3~0.9), tags(2~4个词), related(3~5个同义检索词)。\n"
            "   - long_term_facts: 仅存真正长期稳定、值得永久铭记的事实列表（最多2条，如职业、长期爱好、长期羁绊约定、群内名场面梗；一次性短期计划/待办严禁写入 L3！），每项含 category('user_fact'|'shared_promise'|'group_meme'), target_id(对应用户QQ或群), fact_key(如 'ta.喜好'), content, keywords。\n"
            "4. 【安全合规措辞准则】：提炼 mood.reason、new_pending_thought、mid_term_events 与 long_term_facts 时，请使用温馨、可爱、日常化的表达（如“亲昵贴贴、撒娇互动、摸摸头、闹别扭”），避免直接写入露骨感官刺激或主仆惩罚字眼，防止后续唤醒时触发云端安全拦截。\n"
            f"{libido_rule_block}\n"
            "请严格只输出如下 JSON 格式：\n"
            "```json\n"
            "{\n"
            "  \"mood\": {\"mood_delta\": 0, \"dims\": {\"happy\": 0, \"calm\": 0, \"sweet\": 0, \"proud\": 0, \"worry\": 0, \"sulky\": 0, \"jealous\": 0}, \"reason\": \"\"},\n"
            "  \"affection\": {\n"
            f"    {affection_template_entries}\n"
            "  },\n"
            "  \"memory\": {\n"
            "    \"resolved_ids\": [],\n"
            "    \"new_pending_thought\": \"\",\n"
            "    \"resolved_thought_keyword\": \"\",\n"
            "    \"mid_term_events\": [\n"
            "      {\"should_save\": false, \"target_id\": \"\", \"text\": \"\", \"importance\": 0.6, \"tags\": [], \"related\": []}\n"
            "    ],\n"
            "    \"long_term_facts\": [\n"
            "      {\"should_save\": false, \"target_id\": \"\", \"category\": \"user_fact\", \"fact_key\": \"\", \"content\": \"\", \"keywords\": \"\"}\n"
            "    ]\n"
            "  }"
            f"{libido_json_field}\n"
            "}\n"
            "```"
        )

        obs_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"sess_consolidate_{int(time.time() * 1000)}_{chat_id_str}"))
        model_flags, _, _ = build_agy_model_flags(effort=OBSERVER_AGY_EFFORT, allow_low=True)
        cmd = [
            AGY_EXECUTABLE,
            f"--conversation={obs_conv_id}",
            *model_flags,
            "--dangerously-skip-permissions",
            "--prompt", consolidate_prompt
        ]

        async with OBSERVER_AGY_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=BASE_DIR,
                env={**get_agy_env(), "AGY_TOOL_DOMAINS": "none"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=55.0)
            except asyncio.TimeoutError:
                proc.kill()
                return

        raw_out = stdout.decode("utf-8", errors="ignore").strip()
        parsed = _extract_json_dict(raw_out)
        if not parsed:
            return

        # 1. 更新情绪状态（锁 4：最多允许变动 3 个情绪维度，按绝对值排序仅保留前 3 个）
        mood_blk = parsed.get("mood", {})
        if isinstance(mood_blk, dict):
            from social.mood_manager import filter_top_k_dim_deltas
            m_delta = float(mood_blk.get("mood_delta", 0.0) or 0.0)
            raw_d_deltas = mood_blk.get("dims", {}) or {}
            d_deltas = filter_top_k_dim_deltas(raw_d_deltas, max_dims=3)
            m_reason = str(mood_blk.get("reason", "") or "").strip()
            new_mood_state = apply_mood_update(m_delta, d_deltas, m_reason, scene_str)
            if m_reason:
                print(f"💭 [会话情绪沉淀] 基调={new_mood_state['mood']:.1f} | 变动维度={list(d_deltas.keys())} | 来历: {m_reason}")

        # 2. 更新好感共鸣与定级（支持多参与者逐人结算，并兼容旧版单对象输出）
        aff_blk = parsed.get("affection", {})
        if isinstance(aff_blk, dict):
            if "mood_resonance" in aff_blk:
                if primary_sender not in ("系统", str(BOT_QQ), ""):
                    m_res = float(aff_blk.get("mood_resonance", 0.0) or 0.0)
                    c_score = aff_blk.get("calibrate_score", None)
                    a_reason = str(aff_blk.get("reason", "") or "").strip()
                    settle_turn_affection(primary_sender, m_res, calibrate_score=c_score, reason=a_reason)
            else:
                for raw_uid, u_aff in aff_blk.items():
                    uid_clean = str(raw_uid or "").strip()
                    if not uid_clean.isdigit() or uid_clean == str(BOT_QQ) or not isinstance(u_aff, dict):
                        continue
                    m_res = float(u_aff.get("mood_resonance", 0.0) or 0.0)
                    c_score = u_aff.get("calibrate_score", None)
                    a_reason = str(u_aff.get("reason", "") or "").strip()
                    settle_turn_affection(uid_clean, m_res, calibrate_score=c_score, reason=a_reason)

        # 3. 落盘记忆与兑现核销（支持 pt/l3/l2 按 ID 核销与关键词兜底）
        mem_blk = parsed.get("memory", {})
        if isinstance(mem_blk, dict) and primary_sender not in ("系统", str(BOT_QQ), ""):
            for raw_rid in (mem_blk.get("resolved_ids") or []):
                rid = str(raw_rid or "").strip()
                if not rid or ":" not in rid:
                    continue
                kind, real_id = rid.split(":", 1)
                kind_low = kind.strip().lower()
                real_id = real_id.strip()
                if kind_low == "pt" and real_id:
                    if resolve_pending_thought(primary_sender, real_id):
                        print(f"✅ [内省核销心事] 用户 {primary_sender}: pt:{real_id}")
                elif kind_low == "l3" and real_id.isdigit():
                    res = fulfill_permanent_fact(int(real_id), resolution_note="会话内省自动核销")
                    if res.get("ok"):
                        print(f"✅ [内省核销L3约定] #{real_id}: {res.get('content', '')}")
                elif kind_low == "l2" and real_id:
                    if delete_mid_term_memory(memory_id=real_id):
                        print(f"✅ [内省清理L2动态] l2:{real_id}")

            res_kw = str(mem_blk.get("resolved_thought_keyword", "") or "").strip()
            if res_kw:
                resolve_pending_thought(primary_sender, res_kw)
                kw_l3 = fulfill_permanent_facts_by_keyword(
                    primary_sender, res_kw, resolution_note="会话内省关键词自动核销"
                )
                for item in kw_l3:
                    print(f"✅ [内省关键词核销L3] #{item.get('id')}: {item.get('content', '')}")

            new_pt = str(mem_blk.get("new_pending_thought", "") or "").strip()
            if new_pt and len(new_pt) >= 4:
                add_pending_thought(primary_sender, new_pt, expire_days=3, source_scene=chat_id_str)
                print(f"📌 [会话记下心事] 用户 {primary_sender} (来源:{chat_id_str}): {new_pt}")

            # 处理 L2 中程记忆
            mt_list = mem_blk.get("mid_term_events") or []
            if isinstance(mem_blk.get("mid_term_event"), dict):
                mt_list.append(mem_blk["mid_term_event"])
            for mt_ev in mt_list[:2]:
                if isinstance(mt_ev, dict) and mt_ev.get("should_save") and mt_ev.get("text"):
                    t_uid = str(mt_ev.get("target_id") or primary_sender).strip()
                    mt_text = str(mt_ev.get("text"))
                    add_mid_term_memory(
                        target_id=t_uid,
                        text=mt_text,
                        importance=float(mt_ev.get("importance", 0.6) or 0.6),
                        tags=mt_ev.get("tags") or [],
                        related=mt_ev.get("related") or [],
                        source_scene=chat_id_str
                    )
                    print(f"🫧 [会话L2记忆入库] 用户 {t_uid} (来源:{chat_id_str}): {mt_text}")

            # 处理 L3 永久事实
            lt_list = mem_blk.get("long_term_facts") or []
            if isinstance(mem_blk.get("long_term_fact"), dict):
                lt_list.append(mem_blk["long_term_fact"])
            for lt_fact in lt_list[:2]:
                if isinstance(lt_fact, dict) and lt_fact.get("should_save") and lt_fact.get("content"):
                    cat = str(lt_fact.get("category", "user_fact") or "user_fact")
                    raw_tid = str(lt_fact.get("target_id") or primary_sender).strip()
                    t_id = f"group_{group_id}" if (cat == "group_meme" and group_id) else raw_tid
                    lt_content = str(lt_fact.get("content"))
                    upsert_permanent_fact(
                        target_id=t_id,
                        content=lt_content,
                        category=cat,
                        keywords=str(lt_fact.get("keywords", "") or ""),
                        fact_key=str(lt_fact.get("fact_key", "") or ""),
                        source_scene=chat_id_str
                    )
                    print(f"🏛️ [会话L3永久记忆入库] 对象 {t_id} ({lt_fact.get('fact_key')} | 来源:{chat_id_str}): {lt_content}")

        # 4. 若本场属于授权用户的私聊/允许场景，结算 dsh-libido-panel 事件
        if libido_enabled_for_turn:
            lib_blk = parsed.get("libido_event")
            if isinstance(lib_blk, dict):
                try:
                    if lib_blk.get("resolved"):
                        r_res = trigger_resolve(note=str(lib_blk.get("note") or "对话安抚归位"))
                        print(f"🔥 [Libido结算] 归位初始值: {r_res.get('before')} -> {r_res.get('after')}")
                    else:
                        l_delta = int(lib_blk.get("delta", 0) or 0)
                        if l_delta != 0:
                            l_delta = max(-15, min(20, l_delta))
                            l_note = str(lib_blk.get("note") or "").strip() or "对话互动"
                            ev_res = apply_libido_event(l_delta, note=l_note)
                            print(f"🔥 [Libido结算] {l_note} ({ev_res.get('applied'):+d}): {ev_res.get('before')} -> {ev_res.get('after')}")
                except Exception as le:
                    print(f"⚠️ [Libido结算异常]: {le}")

        print(f"✅ [会话整理完毕] 通道 {chat_id_str} 记忆归档与情感沉淀完成！")
    except Exception as e:
        print(f"⚠️ 会话记忆整理异常 ({session_snapshot.get('chat_id_str')}): {e}")


async def run_post_turn_observer(
    chat_id_str: str,
    group_id: int,
    sender_id: str,
    user_messages: list,
    turn_start_ts: float,
    is_private: bool = False,
    is_breathe: bool = False
):
    """兼容单轮独立观察调用：构造单轮快照交由 consolidate_session_memory 处理"""
    await asyncio.sleep(0.5)
    sender_str = str(sender_id).strip()
    state_key = f"private_{sender_str}" if is_private else str(group_id)
    bot_reply = get_latest_bot_reply(state_key, turn_start_ts)
    if not bot_reply:
        return

    snapshot = {
        "chat_id_str": chat_id_str,
        "is_private": is_private,
        "group_id": group_id,
        "primary_sender_id": sender_str,
        "turns_log": [
            {
                "sender_id": sender_str,
                "user_messages": user_messages or [],
                "bot_reply": bot_reply,
                "time": time.time()
            }
        ]
    }
    await consolidate_session_memory(snapshot)
