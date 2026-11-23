"""
workers/friend_request_worker.py — 后台常驻 0-LLM 好友申请自动审批与轻迎新通道

核心职责：
1. 双源捕获好友申请（彻底解决漏接与 NTQQ 折叠吞申请问题）：
   - 实时源：接收 WebSocket `request.friend` 事件推入异步队列；
   - 巡检源：后台常驻协程每 180 秒主动轮询 NapCat `/get_doubt_friends_add_request`，将腾讯风控自动折叠进“被过滤好友通知（疑虑申请）”的请求自动捞出。
2. 协议层双保险默认自动通过：
   - 同时兼容标准 `/set_friend_add_request` 与 NapCat `/set_doubt_friends_add_request`，带自动重试。
3. 纯本地 0-LLM 极速迎新与主人汇报：
   - 无需拉起大模型子进程，0 额度消耗、0 队列阻塞；
   - 通过后发送自然轻快的打招呼私聊，并向主人 (MASTER_QQ) 汇报申请者信息与验证留言。
"""
import time
import asyncio
from config import MASTER_QQ, BOT_QQ
from core.napcat_client import (
    set_friend_add_request, get_doubt_friends_add_request,
    set_doubt_friends_add_request, get_stranger_info,
    send_private_msg_raw
)
from social.affection import add_user_exp
from social.long_term_memory import record_chat_archive

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
    """处理单条好友申请：协议层默认自动通过 -> 0-LLM 本地极速迎新与主人汇报"""
    uid_str = item["user_id"]
    flag = item["flag"]
    comment = item["comment"]
    nickname = item["nickname"]
    is_doubt = item["is_doubt"]

    # 1. 协议层立即默认同意好友申请
    approved = await _approve_at_protocol_level(flag, is_doubt)
    print(f"✅ [好友协议审批] QQ:{uid_str} | 结果:{'成功通过' if approved else '已尝试执行'} (is_doubt={is_doubt})")

    # 防重复迎新锁：10分钟内同一用户只触发一次迎新与汇报
    now = time.time()
    last_welcomed = RECENTLY_WELCOMED_USERS.get(uid_str, 0.0)
    if (now - last_welcomed) < WELCOME_COOLDOWN_SEC:
        print(f"⏭️ [好友迎新防重] 用户 {uid_str} 近期已完成迎新接待，跳过重复触发。")
        return
    RECENTLY_WELCOMED_USERS[uid_str] = now

    # 给新好友加上初始经验与等待好友关系在腾讯侧生效
    add_user_exp(uid_str, 5.0)
    await asyncio.sleep(1.5)

    # 2. 补全申请者昵称
    if not nickname:
        try:
            info_res = await get_stranger_info(int(uid_str))
            if info_res.get("status") == "ok" and isinstance(info_res.get("data"), dict):
                nickname = info_res["data"].get("nickname") or info_res["data"].get("remark") or ""
        except Exception:
            pass
    if not nickname:
        nickname = f"新朋友({uid_str})"

    comment_display = comment if comment else "（对方未填写验证留言）"
    source_desc = "从被腾讯折叠的过滤通知箱中捞出并通过" if is_doubt else "收到实时好友申请并自动通过"

    # 3. 0-LLM 极速直发迎新招呼与主人汇报（无模型调用、零额度占用）
    try:
        welcome_msg = (
            f"哈喽 {nickname}~ 已经通过你的好友申请啦！"
            + (f"看到你的留言「{comment[:30]}」了，有空随时找我唠嗑呀~" if comment else "有空随时找我唠嗑呀~")
        )
        await send_private_msg_raw(int(uid_str), welcome_msg)
        record_chat_archive(f"private_{uid_str}", BOT_QQ, "弥酱", welcome_msg, is_bot=True)

        report_msg = (
            f"刚刚自动通过了一位新朋友的好友申请：\n"
            f"• 昵称：{nickname} ({uid_str})\n"
            f"• 验证留言：{comment_display}\n"
            f"• 通道：{source_desc}"
        )
        await send_private_msg_raw(int(MASTER_QQ), report_msg)
        record_chat_archive(f"private_{MASTER_QQ}", BOT_QQ, "弥酱", report_msg, is_bot=True)
        print(f"🎉 [0-LLM好友自动审批完成] 已通过 {nickname}({uid_str}) 并完成迎新与主人汇报。")
    except Exception as e:
        print(f"❌ [好友自动迎新发信异常] {uid_str}: {e}")


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
