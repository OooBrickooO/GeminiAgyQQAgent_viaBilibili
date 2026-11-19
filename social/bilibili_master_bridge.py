"""
social/bilibili_master_bridge.py — 主人 B站 管理桥梁与事件联动

职责：
1. 监听主人专属指令（“登录B站”、“B站扫码”），即刻生成二维码并通过 Base64 图片直传发给主人
2. 启动异步无头轮询协程，在主人手机 App 确认后即时私聊通报绑定结果
3. 文本 B 站链接嗅探器，协助上下文感知群友的视频分享
"""
import re
import asyncio
from config import MASTER_QQ
from core.napcat_client import send_private_msg_raw, send_group_msg_raw
from social.bilibili_auth import (
    generate_login_qr, poll_login_status, verify_bili_credential,
    clear_bili_credential
)


async def handle_master_bili_logout(sender_id: int, group_id: int = None) -> bool:
    """处理主人发起的 B站 注销/解绑指令"""
    if str(sender_id) != str(MASTER_QQ):
        return False
    ok = clear_bili_credential()
    if ok:
        msg = "主人，弥酱已成功清除本地 B站 登录凭证并完成退出解绑喵！如需重新登录可随时对我说【登录B站】～"
    else:
        msg = "主人，当前本地并未登录任何 B站 账号或凭证本就不存在喵～"
    if group_id:
        await send_group_msg_raw(int(group_id), msg)
    else:
        await send_private_msg_raw(int(sender_id), msg)
    return True


async def handle_master_bili_history(sender_id: int, group_id: int = None) -> bool:
    """处理主人视察弥酱 B站 观看历史的指令"""
    if str(sender_id) != str(MASTER_QQ):
        return False
    from social.bilibili_client import get_watch_history
    history = await get_watch_history(num=6)
    if not history:
        msg = "主人，弥酱今天还没刷过 B站 视频，或者暂未登录 B站 账号喵～"
    else:
        lines = ["📺 主人，这是弥酱最近刷到的 B站 视频历史记录喵："]
        for idx, item in enumerate(history[:6], 1):
            title = item.get("title", "")
            author = item.get("author", "未知UP")
            bvid = item.get("bvid", "")
            score = item.get("score")
            comment = item.get("comment")
            line = f"{idx}. 《{title}》\n   UP: {author} ({bvid})"
            if score and comment:
                line += f"\n   弥酱心得: {comment} (评分: {score}分)"
            lines.append(line)
        lines.append("（在手机/网页端登录弥酱账号也可以在【历史记录】里直接看到哦～）")
        msg = "\n".join(lines)
    if group_id:
        await send_group_msg_raw(int(group_id), msg)
    else:
        await send_private_msg_raw(int(sender_id), msg)
    return True


# 全局后台轮询守护任务句柄
_CURRENT_POLL_TASK: asyncio.Task | None = None


async def handle_master_bili_login(sender_id: int, group_id: int = None) -> bool:
    """
    处理主人发起的 B站 扫码登录指令。
    双重权限防御：严格校验仅允许 MASTER_QQ 执行。
    自动生成 PNG 并在 QQ 中私聊直传图片，启动后台轮询任务。
    并发控制：生成新二维码前强制 Cancel 上一次未完成的轮询协程，杜绝并发误杀与超时误报。
    返回是否成功命中并处理。
    """
    global _CURRENT_POLL_TASK

    if str(sender_id) != str(MASTER_QQ):
        print(f"🛑 [B站权限拦截] 非主人 QQ({sender_id}) 试图触发 B站登录，已物理熔断拦截！")
        return False

    print(f"📱 [B站认证] 主人({sender_id}) 发起 B站 登录指令，正在生成二维码...")

    # 先检查是否已经登录且凭证有效
    check_res = await verify_bili_credential()
    if check_res.get("logged_in"):
        name = check_res.get("name")
        uid = check_res.get("uid")
        msg = f"主人～ 弥酱当前已经成功登录B站账号【{name}】(UID: {uid}) 啦，无需重复扫码！\n如需换绑，直接在服务器删除 bili_credential.json 即可～"
        if group_id:
            await send_group_msg_raw(int(group_id), msg)
        else:
            await send_private_msg_raw(int(sender_id), msg)
        return True

    # 孤儿任务并发熔断：如果上一个轮询协程还在运行，立即主动取消，避免串台误杀新二维码
    if _CURRENT_POLL_TASK and not _CURRENT_POLL_TASK.done():
        print("🛑 [B站认证] 检测到上一次扫码轮询任务仍在运行，正在主动 Cancel 终止旧任务...")
        _CURRENT_POLL_TASK.cancel()
        _CURRENT_POLL_TASK = None

    # 生成二维码
    qr_res = await generate_login_qr()
    if not qr_res.get("ok"):
        err_msg = f"主人，生成 B站 登录二维码失败啦: {qr_res.get('message')}"
        if group_id:
            await send_group_msg_raw(int(group_id), err_msg)
        else:
            await send_private_msg_raw(int(sender_id), err_msg)
        return True

    session_id = qr_res.get("session_id")
    img_b64 = qr_res.get("qrcode_base64")
    cq_img = f"[CQ:image,file=base64://{img_b64}]"
    prompt_text = (
        f"{cq_img}\n"
        "主人，请用手机 B站 App 扫描上方二维码登录（180秒内有效）~\n"
        "在手机上确认后，弥酱会自动完成绑定并通知你！"
    )

    # 优先发到私聊，如果在群里触发则私聊发送并群里提示
    await send_private_msg_raw(int(MASTER_QQ), prompt_text)
    if group_id:
        await send_group_msg_raw(int(group_id), "主人，弥酱已将 B站 登录二维码私聊发送给你啦，请在手机上扫码确认哦～")

    # 启动后台轮询守护协程，并绑定当前会话唯一 session_id
    _CURRENT_POLL_TASK = asyncio.create_task(_poll_login_worker(int(MASTER_QQ), session_id=session_id))
    return True


async def _poll_login_worker(master_id: int, session_id: str | None = None):
    """
    后台守护任务：每 3 秒检测一次主人扫码状态，最多轮询 180 秒。
    深度绑定 session_id，若被新任务接管则自动静默退出，严禁误报超时。
    """
    max_attempts = 60  # 60 * 3s = 180s
    try:
        for _ in range(max_attempts):
            await asyncio.sleep(3)
            res = await poll_login_status(session_id=session_id)
            status = res.get("status")

            if status == "stale_session":
                print(f"🛑 [B站认证] 会话({str(session_id)[:8]})已过时被新任务替代，协程静默结束。")
                return

            elif status == "done":
                name = res.get("name", "B站用户")
                uid = res.get("uid", "")
                success_msg = f"🎉 主人！B站账号【{name}】(UID: {uid}) 登录成功啦！\n弥酱现在可以使用专属账号冲浪、读字幕和收藏宝藏视频啦～"
                await send_private_msg_raw(master_id, success_msg)
                print(f"✅ [B站认证] 主人扫码成功，账号【{name}】({uid}) 已就绪！")
                return

            elif status == "timeout":
                await send_private_msg_raw(master_id, "⚠️ 主人，刚才的 B站 登录二维码已超时失效啦。如需登录可随时对我说【登录B站】重新获取~")
                return

            elif status == "error":
                err_msg = res.get("message", "未知错误")
                await send_private_msg_raw(master_id, f"⚠️ 主人，扫码登录处理过程中出错了喵: {err_msg}")
                return

            elif status == "no_session":
                return

        # 超时退出
        await send_private_msg_raw(master_id, "⚠️ 主人，B站 扫码已超时（超过180秒未确认），会话已自动结束。")
    except asyncio.CancelledError:
        print(f"🛑 [B站认证] 扫码轮询协程已响应取消信号并安全退出 ({str(session_id)[:8]})。")
        return


def detect_bili_link(text: str) -> str | None:
    """
    检测文本中是否包含 B 站视频链接或 BV 号。
    支持 BV/bv 号、b23.tv 短链接、完整视频链接、手机端链接以及 av 号。
    若命中，返回匹配到的原始子串，供 Prompt 强化。
    """
    if not text or not isinstance(text, str):
        return None
    match = re.search(
        r'(?:https?://)?(?:www\.|m\.)?b23\.tv/[a-zA-Z0-9]+|'
        r'(?:https?://)?(?:www\.|m\.)?bilibili\.com/video/(?:BV[a-zA-Z0-9]{10}|av\d+)|'
        r'\bBV[a-zA-Z0-9]{10}\b|'
        r'\bav\d+\b',
        text,
        re.IGNORECASE
    )
    return match.group(0) if match else None
