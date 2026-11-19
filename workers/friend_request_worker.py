"""
workers/friend_request_worker.py — 后台常驻 AGY 好友申请巡检与高情商迎新官

核心职责：
1. 双源捕获好友申请（彻底解决漏接与 NTQQ 折叠吞申请问题）：
   - 实时源：接收 WebSocket `request.friend` 事件推入异步队列；
   - 巡检源：后台常驻协程每 180 秒主动轮询 NapCat `/get_doubt_friends_add_request`，将腾讯风控自动折叠进“被过滤好友通知（疑虑申请）”的请求自动捞出。
2. 协议层双保险自动同意：
   - 同时兼容标准 `/set_friend_add_request` 与 NapCat `/set_doubt_friends_add_request`，带自动重试。
3. 后台常驻 AGY 智能迎新接待 (`OBSERVER_AGY_SEMAPHORE`)：
   - 自动查询申请者昵称及群内过往交集（`USER_PROFILES` 与 L3 长期印象）；
   - 唤醒后台 AGY，由弥酱结合对方的【验证留言】亲自调用 `send_private_msg` 发送专属破冰招呼、写入初识印象，并向主人私聊汇报。
"""
import time
import uuid
import asyncio
from config import (
    get_agy_env, BOT_NAMESPACE, MASTER_QQ, BOT_QQ,
    AGY_EXECUTABLE, build_agy_model_flags, BASE_DIR
)
from core.napcat_client import (
    set_friend_add_request, get_doubt_friends_add_request,
    set_doubt_friends_add_request, get_stranger_info,
    send_private_msg_raw
)
from core.agy_runner import OBSERVER_AGY_SEMAPHORE
from core.state_manager import LAST_BOT_MSG_TIME, sync_bot_msgs_from_disk
from social.affection import USER_PROFILES, load_profiles, add_user_exp
from social.long_term_memory import get_user_core_facts_for_prompt, record_chat_archive
from attention.persona import STATIC_PERSONA

# 待处理好友申请异步队列
FRIEND_REQ_QUEUE: asyncio.Queue = asyncio.Queue()

# 已处理的申请 flag 集合与近期已迎新用户冷却表 (防止事件与轮询重复触发迎新)
PROCESSED_REQ_FLAGS = set()
RECENTLY_WELCOMED_USERS = {}
WELCOME_COOLDOWN_SEC = 600.0


async def enqueue_friend_request(
    user_id: str,
    flag: str,
    comment: str = "",
    nickname: str = "",
    is_doubt: bool = False
):
    """将捕获到的好友申请推入后台常驻处理队列"""
    uid_str = str(user_id or "").strip()
    flag_str = str(flag or "").strip()
    if not uid_str or uid_str == str(BOT_QQ):
        return

    if flag_str and flag_str in PROCESSED_REQ_FLAGS:
        return
    if flag_str:
        PROCESSED_REQ_FLAGS.add(flag_str)

    item = {
        "user_id": uid_str,
        "flag": flag_str,
        "comment": (comment or "").strip(),
        "nickname": (nickname or "").strip(),
        "is_doubt": bool(is_doubt),
        "queued_at": time.time()
    }
    await FRIEND_REQ_QUEUE.put(item)
    src_tag = "折叠过滤箱巡检" if is_doubt else "实时事件"
    print(f"📥 [好友申请入队] 来源:{src_tag} | QQ:{uid_str} | 验证留言:'{item['comment']}' | flag:{flag_str}")


async def _approve_at_protocol_level(flag: str, is_doubt: bool) -> bool:
    """在 OneBot / NapCat 协议层执行双通道同意操作（带重试保障）"""
    if not flag:
        return False

    for attempt in range(2):
        try:
            if is_doubt:
                res1 = await set_doubt_friends_add_request(flag, approve=True)
                if res1.get("status") == "ok" or res1.get("retcode") == 0:
                    return True
                res2 = await set_friend_add_request(flag, approve=True)
                if res2.get("status") == "ok" or res2.get("retcode") == 0:
                    return True
            else:
                res1 = await set_friend_add_request(flag, approve=True)
                if res1.get("status") == "ok" or res1.get("retcode") == 0:
                    return True
                # 若常规接口未成功，尝试作为被折叠疑虑申请通过
                res2 = await set_doubt_friends_add_request(flag, approve=True)
                if res2.get("status") == "ok" or res2.get("retcode") == 0:
                    return True
        except Exception as e:
            print(f"⚠️ [协议层同意好友重试 {attempt + 1}] 异常: {e}")
        await asyncio.sleep(1.5)
    return False


async def _handle_single_friend_request(item: dict):
    """处理单条好友申请：协议层同意 -> 唤醒后台常驻 AGY 个性化破冰与汇报"""
    uid_str = item["user_id"]
    flag = item["flag"]
    comment = item["comment"]
    nickname = item["nickname"]
    is_doubt = item["is_doubt"]

    # 1. 协议层立即同意好友申请
    approved = await _approve_at_protocol_level(flag, is_doubt)
    print(f"✅ [好友协议审批] QQ:{uid_str} | 结果:{'成功通过' if approved else '已尝试执行'} (is_doubt={is_doubt})")

    # 防重复迎新锁：10分钟内同一用户只触发一次 AGY 迎新与汇报
    now = time.time()
    last_welcomed = RECENTLY_WELCOMED_USERS.get(uid_str, 0.0)
    if (now - last_welcomed) < WELCOME_COOLDOWN_SEC:
        print(f"⏭️ [好友迎新防重] 用户 {uid_str} 近期已完成迎新接待，跳过重复触发。")
        return
    RECENTLY_WELCOMED_USERS[uid_str] = now

    # 给新好友加上初始经验与等待好友关系在腾讯侧生效
    add_user_exp(uid_str, 5.0)
    await asyncio.sleep(1.5)

    # 2. 补全申请者昵称与过往群内交集记忆
    if not nickname:
        info_res = await get_stranger_info(int(uid_str))
        if info_res.get("status") == "ok" and isinstance(info_res.get("data"), dict):
            nickname = info_res["data"].get("nickname") or info_res["data"].get("remark") or ""
    if not nickname:
        nickname = f"新朋友({uid_str})"

    load_profiles()
    prof = USER_PROFILES.get(uid_str, {})
    aff = float(prof.get("affection", 0.0))
    core_facts = get_user_core_facts_for_prompt(uid_str)
    if aff > 0 or core_facts:
        relation_ctx = f"你在群聊里已经认识这位朋友（当前熟悉度: {aff:.0f}）。\n{core_facts}"
    else:
        relation_ctx = "这是一位之前没怎么在群里深入聊过的新朋友。"

    comment_display = comment if comment else "（对方未填写验证留言）"
    source_desc = "从被腾讯折叠的过滤通知箱中捞出并通过" if is_doubt else "收到实时好友申请并自动通过"

    # 3. 唤醒后台常驻 AGY 执行高情商迎新与主人汇报
    turn_start_ts = time.time()
    welcome_conv_id = str(uuid.uuid5(BOT_NAMESPACE, f"friend_welcome_{uid_str}_{int(turn_start_ts)}"))
    prompt = (
        f"{STATIC_PERSONA}\n\n"
        f"【当前后台常驻任务：新好友申请通过后的专属破冰迎新与主人汇报】\n"
        f"- 申请者 QQ：{uid_str}\n"
        f"- 申请者昵称：{nickname}\n"
        f"- 验证留言：{comment_display}\n"
        f"- 处理通道：{source_desc}\n"
        f"- 过往交集：{relation_ctx}\n\n"
        f"【请立即依次完成以下动作（必须调用工具，切勿只输出纯文本）】：\n"
        f"1. **给新好友发破冰私聊**：调用 `send_private_msg(user_id={uid_str}, message=..., auto_split=True)`，"
        f"以星见弥自然、松弛、带点小俏皮的语气跟对方打招呼！如果对方写了有趣的验证留言（如自报家门、扩列、玩梗）或你在群里认识ta，请直接顺着留言内容自然接话；\n"
        f"2. **记忆归档（可选）**：如果对方的验证留言里自报了身份、称呼或喜好，请调用 `memory_action(action='save_fact', target_id='{uid_str}', content=..., fact_key='ta.初识备注')` 记下来；\n"
        f"3. **向自家主人私聊汇报**：调用 `send_private_msg(user_id={MASTER_QQ}, message=..., auto_split=False)`，"
        f"用你对 {MASTER_QQ} 自然随性的口吻（多用“我”，少用“喵”与括号动作），顺口告诉他刚通过了『{nickname}』({uid_str}) 的好友申请、对方留言以及你是怎么打招呼的。"
    )

    model_flags, _, _ = build_agy_model_flags(effort="medium", allow_low=False)
    cmd = [
        AGY_EXECUTABLE,
        f"--conversation={welcome_conv_id}",
        *model_flags,
        "--dangerously-skip-permissions",
        "--prompt", prompt
    ]

    agy_succeeded = False
    try:
        sub_env = get_agy_env()
        sub_env["AGY_TOOL_DOMAINS"] = "chat,memory"
        sub_env["AGY_CALLER_ID"] = str(MASTER_QQ)

        async with OBSERVER_AGY_SEMAPHORE:
            print(f"🌸 [AGY好友迎新官] 正在为新好友 {nickname}({uid_str}) 定制破冰问候与主人汇报...")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=BASE_DIR,
                env=sub_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True
            )
            await asyncio.wait_for(process.wait(), timeout=90.0)

        priv_key = f"private_{uid_str}"
        sync_bot_msgs_from_disk(priv_key)
        if LAST_BOT_MSG_TIME.get(priv_key, 0.0) >= turn_start_ts:
            agy_succeeded = True
            print(f"🎉 [AGY好友迎新完成] 已成功接待新好友 {nickname}({uid_str}) 并向主人汇报！")
    except Exception as e:
        print(f"⚠️ [AGY好友迎新异常] {uid_str}: {e}")

    # 4. 兜底保障：若 AGY 因网络波动未能成功发出欢迎消息，由底层保底发信，绝不冷落新好友
    if not agy_succeeded:
        try:
            fallback_welcome = (
                f"哈喽 {nickname}~ 弥酱已经通过你的好友申请啦喵！"
                + (f"（看到你的留言“{comment[:30]}”啦~）" if comment else "平时有空随时找我唠嗑呀~ (。-ω-)zzz")
            )
            await send_private_msg_raw(int(uid_str), fallback_welcome)
            record_chat_archive(f"private_{uid_str}", BOT_QQ, "弥酱", fallback_welcome, is_bot=True)

            fallback_report = (
                f"主人主人~ 弥酱刚刚自动通过了一位新朋友的好友申请喵！\n"
                f"• 昵称：{nickname} ({uid_str})\n"
                f"• 验证留言：{comment_display}\n"
                f"• 通道：{source_desc}"
            )
            await send_private_msg_raw(int(MASTER_QQ), fallback_report)
            record_chat_archive(f"private_{MASTER_QQ}", BOT_QQ, "弥酱", fallback_report, is_bot=True)
            print(f"🛡️ [好友迎新保底] 已完成对 {nickname}({uid_str}) 的保底欢迎与主人汇报。")
        except Exception as fb_err:
            print(f"❌ [好友迎新保底失败]: {fb_err}")


async def _poll_doubt_friend_requests_loop():
    """后台定时巡检协程：每 180 秒检查一次 NTQQ 被折叠的疑虑好友申请列表"""
    await asyncio.sleep(15.0)
    while True:
        try:
            res = await get_doubt_friends_add_request(count=20)
            if res.get("status") == "ok":
                data_list = res.get("data")
                if isinstance(data_list, dict):
                    data_list = data_list.get("requests") or data_list.get("list") or []
                if isinstance(data_list, list):
                    for req in data_list:
                        if not isinstance(req, dict):
                            continue
                        flag = str(req.get("flag") or "").strip()
                        uid = str(req.get("uin") or req.get("user_id") or "").strip()
                        nick = str(req.get("nick") or req.get("nickname") or "").strip()
                        msg = str(req.get("msg") or req.get("comment") or req.get("reason") or "").strip()
                        if uid and flag and flag not in PROCESSED_REQ_FLAGS:
                            await enqueue_friend_request(
                                user_id=uid,
                                flag=flag,
                                comment=msg,
                                nickname=nick,
                                is_doubt=True
                            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"⚠️ [折叠好友巡检略过]: {e}")
        await asyncio.sleep(180.0)


async def friend_request_worker():
    """后台常驻好友申请处理主协程（同时启动队列消费与折叠申请定时巡检）"""
    print("🤝 [常驻Worker启动] AGY 好友申请迎新官与折叠申请巡检器已就绪！")
    poll_task = asyncio.create_task(_poll_doubt_friend_requests_loop())
    try:
        while True:
            item = await FRIEND_REQ_QUEUE.get()
            try:
                await _handle_single_friend_request(item)
            except Exception as e:
                print(f"⚠️ [处理好友申请项异常]: {e}")
            finally:
                FRIEND_REQ_QUEUE.task_done()
    except asyncio.CancelledError:
        poll_task.cancel()
