"""
workers/daily_memory.py — 03:00 每日守夜人：长中效记忆查漏补缺与全员等级/稳态好感结算 V3.0

职责（彻底废除旧版“30字单句覆盖 memory_capsule”的僵硬机制）：
- 每天凌晨 03:00 自动唤醒，调用 settle_daily_profiles() 执行全员等级与稳态好感跨天结算；
- 从 SQLite `chat_archive_fts` 中读取昨日真实交互记录，查漏补缺提炼出漏记的 L3 永久事实 (`permanent_facts`，带 `fact_key` 状态取代) 与 L2 中程温感记忆 (`mid_term_memory`)。
"""
import re
import json
import uuid
import datetime
import asyncio
from config import (
    get_agy_env, BOT_NAMESPACE, MASTER_QQ,
    BACKGROUND_AGY_EFFORT, AGY_EXECUTABLE, build_agy_model_flags, BASE_DIR
)
from core.agy_runner import OBSERVER_AGY_SEMAPHORE
from social.affection import (
    USER_PROFILES, load_profiles, save_profiles, settle_daily_profiles
)
from social.mid_term_memory import add_mid_term_memory
from social.long_term_memory import init_long_term_db, _get_conn, upsert_permanent_fact


def get_beijing_now() -> datetime.datetime:
    """统一采用北京时间 (UTC+8)"""
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def _extract_json_dict(raw_text: str) -> dict:
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


async def daily_memory_summarization_worker():
    """每天 03:00 (北京时间) 自动唤醒，执行全员好感稳态结算与 L2/L3 记忆夜间查漏补缺"""
    while True:
        try:
            now_bj = get_beijing_now()
            next_run = now_bj.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_run <= now_bj:
                next_run += datetime.timedelta(days=1)
            wait_seconds = max(5.0, (next_run - now_bj).total_seconds())
            print(f"🛌 距离下次夜间记忆沉淀与好感稳态结算还有 {wait_seconds / 3600:.1f} 小时 (目标: {next_run.strftime('%Y-%m-%d %H:%M')})")
            await asyncio.sleep(wait_seconds)

            now_bj = get_beijing_now()
            yesterday_str = (now_bj - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            print(f"🔄 开始执行昨日 ({yesterday_str}) 夜间记忆沉淀与全员等级/好感稳态结算...")

            # 1. 全员等级与好感稳态跨天统一结算
            settle_daily_profiles()

            # 1.1 磁盘防爆治理：自动清理 image_cache/ 目录中过期的群聊临时图片 (保留最新1000张或1周内)
            try:
                from social.image_vault import cleanup_image_cache
                cleanup_image_cache(max_keep=1000, max_age_hours=168)
            except Exception as e:
                print(f"⚠️ [daily_memory] 清理图片缓存异常: {e}")

            # 2. 从 SQLite chat_archive_fts 中提取昨日活跃熟人/朋友/主人的对话记录，查漏补缺写入 L2/L3
            init_long_term_db()
            load_profiles()

            for user_id_str, p in list(USER_PROFILES.items()):
                if float(p.get("affection", 0.0)) < 200.0 and str(user_id_str) != str(MASTER_QQ):
                    continue

                conn = _get_conn()
                rows = conn.execute(
                    """SELECT time_str, chat_id, sender_name, raw_text
                       FROM chat_archive_fts
                       WHERE date_str = ? AND (sender_id = ? OR chat_id = ?)
                       ORDER BY time_str ASC LIMIT 120""",
                    (yesterday_str, str(user_id_str), f"private_{user_id_str}")
                ).fetchall()
                conn.close()

                meaningful_rows = [r for r in rows if len(str(r["raw_text"] or "").strip()) >= 3]
                is_master_user = str(user_id_str) == str(MASTER_QQ)
                is_uncalibrated = not bool(p.get("is_calibrated", True))
                min_rows = 4 if (is_master_user or is_uncalibrated) else 12
                if len(meaningful_rows) < min_rows:
                    continue

                recent_rows = rows[-80:]
                distinct_chats = {str(r["chat_id"]).strip() for r in recent_rows if r["chat_id"]}
                default_scene = (
                    list(distinct_chats)[0]
                    if len(distinct_chats) == 1
                    else f"private_{user_id_str}"
                )
                log_lines = [f"[{r['time_str']} | 场景:{r['chat_id']}] {r['sender_name']}: {r['raw_text']}" for r in recent_rows]
                log_text = "\n".join(log_lines)

                prompt = (
                    f"你是星见弥（弥酱）的夜间记忆整理器。请阅读昨天 ({yesterday_str}) 你与用户({user_id_str})的对话记录，"
                    "提炼出值得补充归档的【L3 永久长效事实（上限2条，宁缺毋滥）】与【L2 中程温感动态（上限2条）】。\n"
                    "【2A 严格边界】：一次性短期计划/临时待办（如“凌晨升级”“明天面试”“周末出门”“待会修Bug”）严禁写入 L3 `long_term_facts`，只记录到 L2 `mid_term_events`；L3 仅存真正长期稳定的人物画像、身份偏好与久远重要约定。\n"
                    "请保留每条记忆对应的产生场景 `source_scene`（直接填对话记录中的场景标识，如 `private_xxx` 或 `group_xxx`）。\n"
                    "如果昨天只是普通打招呼或已经没有新事实，返回空列表即可。严禁调用任何工具，严格只输出如下 JSON：\n"
                    "```json\n"
                    "{\n"
                    "  \"long_term_facts\": [\n"
                    "    {\"category\": \"user_fact\", \"fact_key\": \"ta.某方面\", \"content\": \"具体长期事实\", \"keywords\": \"同义词1 同义词2\", \"source_scene\": \"\"}\n"
                    "  ],\n"
                    "  \"mid_term_events\": [\n"
                    "    {\"text\": \"近期发生的具体事件动态\", \"importance\": 0.65, \"tags\": [\"标签1\"], \"related\": [\"扩词1\", \"扩词2\"], \"source_scene\": \"\"}\n"
                    "  ]\n"
                    "}\n"
                    "```\n\n"
                    f"昨日对话记录：\n{log_text}"
                )

                daily_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"daily_mem_v3_{yesterday_str}_{user_id_str}"))
                try:
                    model_flags, _, _ = build_agy_model_flags(effort=BACKGROUND_AGY_EFFORT, allow_low=True)
                    cmd = [
                        AGY_EXECUTABLE,
                        f"--conversation={daily_conv_id}",
                        *model_flags,
                        "--dangerously-skip-permissions",
                        "--prompt", prompt
                    ]
                    async with OBSERVER_AGY_SEMAPHORE:
                        process = await asyncio.create_subprocess_exec(
                            *cmd,
                            cwd=BASE_DIR,
                            env={**get_agy_env(), "AGY_TOOL_DOMAINS": "none"},
                            stdin=asyncio.subprocess.DEVNULL,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60.0)
                        res_dict = _extract_json_dict(stdout.decode("utf-8", errors="ignore"))

                    for lf in (res_dict.get("long_term_facts") or [])[:2]:
                        if isinstance(lf, dict) and lf.get("content"):
                            lf_scene = str(lf.get("source_scene") or "").strip() or default_scene
                            upsert_permanent_fact(
                                target_id=str(user_id_str),
                                content=str(lf.get("content")),
                                category=str(lf.get("category", "user_fact")),
                                keywords=str(lf.get("keywords", "")),
                                fact_key=str(lf.get("fact_key", "")),
                                source_scene=lf_scene
                            )
                            print(f"🏛️ [夜间L3长效沉淀] 用户 {user_id_str} (来源:{lf_scene}): {lf.get('content')}")

                    for me in (res_dict.get("mid_term_events") or [])[:2]:
                        if isinstance(me, dict) and me.get("text"):
                            me_scene = str(me.get("source_scene") or "").strip() or default_scene
                            add_mid_term_memory(
                                target_id=str(user_id_str),
                                text=str(me.get("text")),
                                importance=float(me.get("importance", 0.6) or 0.6),
                                tags=me.get("tags") or [],
                                related=me.get("related") or [],
                                source_scene=me_scene
                            )
                            print(f"🫧 [夜间L2中程沉淀] 用户 {user_id_str} (来源:{me_scene}): {me.get('text')}")
                except Exception as e:
                    print(f"⚠️ 夜间提炼用户 {user_id_str} 记忆异常: {e}")

            # 3. 潜水学习与群文化偷师 (Group Cultural Assimilation)：从昨日全群热聊中提炼本群专属黑话/名场面梗
            from config import get_allowed_groups
            for group_id_str in list(get_allowed_groups()):
                chat_id_key = f"group_{group_id_str}"
                conn = _get_conn()
                g_rows = conn.execute(
                    """SELECT time_str, sender_name, raw_text
                       FROM chat_archive_fts
                       WHERE date_str = ? AND chat_id = ? AND sender_name != '弥酱(你)'
                       ORDER BY time_str ASC LIMIT 150""",
                    (yesterday_str, chat_id_key)
                ).fetchall()
                conn.close()

                if len(g_rows) < 15:
                    continue

                g_log_lines = [f"[{r['time_str']}] {r['sender_name']}: {r['raw_text']}" for r in g_rows[-100:]]
                g_log_text = "\n".join(g_log_lines)

                group_meme_prompt = (
                    f"你是星见弥（弥酱）的群聊观察笔记。请阅读昨天 ({yesterday_str}) QQ群({group_id_str})的群友聊天记录，\n"
                    "提炼出 1~2 条最有趣、最值得弥酱记住的【本群内部梗 / 高频好玩口癖 / 群友名场面】。\n"
                    "【安全与质量铁律】：\n"
                    "1. 宁缺毋滥！如果昨天只是普通平淡问答、没有形成有趣的群梗或高频口癖，请直接返回空列表 `[]`。\n"
                    "2. 严格过滤不文明用语、恶意攻击与广告引流词汇，只吸收健康幽默、能拉近群友距离的互动梗。\n"
                    "3. 严禁调用任何工具，严格只输出如下 JSON：\n"
                    "```json\n"
                    "{\n"
                    "  \"group_memes\": [\n"
                    "    {\n"
                    "      \"fact_key\": \"群梗.简短梗名\",\n"
                    "      \"content\": \"梗的来历与适用语境简述\",\n"
                    "      \"keywords\": \"触发检索关键词1 关键词2 关键词3\"\n"
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    "```\n\n"
                    f"昨日群聊记录：\n{g_log_text}"
                )

                g_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"group_meme_v4_{yesterday_str}_{group_id_str}"))
                try:
                    model_flags, _, _ = build_agy_model_flags(effort=BACKGROUND_AGY_EFFORT, allow_low=True)
                    cmd = [
                        AGY_EXECUTABLE,
                        f"--conversation={g_conv_id}",
                        *model_flags,
                        "--dangerously-skip-permissions",
                        "--prompt", group_meme_prompt
                    ]
                    async with OBSERVER_AGY_SEMAPHORE:
                        process = await asyncio.create_subprocess_exec(
                            *cmd,
                            cwd=BASE_DIR,
                            env={**get_agy_env(), "AGY_TOOL_DOMAINS": "none"},
                            stdin=asyncio.subprocess.DEVNULL,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=60.0)
                        g_res = _extract_json_dict(stdout.decode("utf-8", errors="ignore"))

                    for gm in (g_res.get("group_memes") or [])[:2]:
                        if isinstance(gm, dict) and gm.get("content"):
                            upsert_permanent_fact(
                                target_id=chat_id_key,
                                content=str(gm.get("content")),
                                category="group_meme",
                                keywords=str(gm.get("keywords", "")),
                                fact_key=str(gm.get("fact_key", "群梗.日常"))
                            )
                            print(f"🎭 [夜间群文化偷师] 群 {group_id_str} ({gm.get('fact_key')}): {gm.get('content')}")
                except Exception as e:
                    print(f"⚠️ 夜间提炼群 {group_id_str} 文化梗异常: {e}")

            save_profiles()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ 每日记忆总结循环异常: {e}")
            await asyncio.sleep(60)
