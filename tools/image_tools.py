"""
tools/image_tools.py — 本地图片发送工具 (Base64 容器隔离)

负责读取本地物理图片，转为 Base64 CQ 码发送给 OneBot，确保 Docker 容器环境下的图片正常发送 (铁律 4)。
"""
import os
import base64
from config import log_mcp_tool, safe_log
from core.napcat_client import call_onebot_api_raw


from core.state_manager import record_bot_spoke_persistent
from tools.message_tools import is_cross_channel_authorized


async def send_image_to_group_impl(group_id: int, image_path: str, cross_channel: bool = False) -> str:
    """发送本地生成的图片到指定的 QQ 群（内置 1A 防串台硬锁，主人传话可凭 cross_channel=True 解锁）"""
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    if is_cross_channel_authorized(cross_channel):
        safe_log(f"🔓 [跨通道授权] send_image_to_group 跨通道发送 -> 群[{group_id}]")
    else:
        if locked_type == "private" and locked_id:
            safe_log(f"🛡️ [防串台硬锁] send_image_to_group 当前为私聊会话(private_{locked_id})，自动重定向至 send_image_to_private！")
            return await send_image_to_private_impl(int(locked_id), image_path)
        elif locked_type == "group" and locked_id and str(group_id) != locked_id:
            group_id = int(locked_id)

    log_mcp_tool("send_image_to_group", f"群 {group_id} -> 发送图片 {os.path.basename(image_path)}")
    try:
        if not os.path.exists(image_path):
            return f"错误：图片路径 {image_path} 不存在"

        with open(image_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode('utf-8')

        message = f"[CQ:image,file=base64://{b64_data}]"
        resp = await call_onebot_api_raw("/send_group_msg", {"group_id": group_id, "message": message}, timeout=30.0)
        record_bot_spoke_persistent(str(group_id), "[图片]")
        return resp
    except Exception as e:
        return f"调用出错: {str(e)}"


async def send_image_to_private_impl(user_id: int, image_path: str, cross_channel: bool = False) -> str:
    """发送本地生成的图片给指定的 QQ 好友（内置 1A 防串台硬锁，主人传话可凭 cross_channel=True 解锁）"""
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    if is_cross_channel_authorized(cross_channel):
        safe_log(f"🔓 [跨通道授权] send_image_to_private 跨通道发送 -> 私聊[{user_id}]")
    else:
        if locked_type == "private" and locked_id and str(user_id) != locked_id:
            user_id = int(locked_id)
        elif locked_type == "group" and locked_id:
            safe_log(f"🛡️ [防串台硬锁] send_image_to_private 当前为群聊会话(group_{locked_id})，自动重定向至 send_image_to_group！")
            return await send_image_to_group_impl(int(locked_id), image_path)

    log_mcp_tool("send_image_to_private", f"私聊 {user_id} -> 发送图片 {os.path.basename(image_path)}")
    try:
        if not os.path.exists(image_path):
            return f"错误：图片路径 {image_path} 不存在"

        with open(image_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode('utf-8')

        message = f"[CQ:image,file=base64://{b64_data}]"
        resp = await call_onebot_api_raw("/send_private_msg", {"user_id": user_id, "message": message}, timeout=30.0)
        record_bot_spoke_persistent(f"private_{user_id}", "[图片]")
        return resp
    except Exception as e:
        return f"调用出错: {str(e)}"


async def inspect_image_impl(img_id: str, question: str = "") -> str:
    """查看群友发送的图片画面内容（支持本地缓存与 view_file 视觉联动）"""
    from social.image_vault import resolve_and_download_image, IMAGE_VAULT
    log_mcp_tool("inspect_image", f"请求查看图片 {img_id}" + (f" (提问: {question})" if question else ""))
    try:
        clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
        local_path = await resolve_and_download_image(clean_id)
        if not local_path or not os.path.exists(local_path):
            return f"查看图片失败：图片 {img_id} 下载超时或文件不存在。"

        info = IMAGE_VAULT.get(clean_id, {})
        nick = info.get("sender_nick", "群友")
        rel = info.get("relation", "[群友]")
        deskpet_voice_rule = (
            "\n🔊【桌宠识图必配日语语音铁律】：当前为桌宠识图回合！看完图片后调用 `pet_say` 回复主人时，"
            "必须传入 `voice=True`，`script` 写中文气泡台词（带【动作, 表情】），并务必在 `ja_voice` 参数填入对应的地道二次元猫娘日语配音台词！"
            if os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip() == "deskpet"
            else ""
        )

        return (
            f"=== 成功检索并下载图片 [{clean_id}] ===\n"
            f"- 发送者: {rel}{nick}\n"
            f"- 图片本地绝对路径: {local_path}\n"
            f"💡【视觉查看指引】：请立刻调用系统内置的 view_file 工具传入该绝对路径（view_file(AbsolutePath='{local_path}')），"
            f"利用系统多模态视觉能力看清画面中展示的文字、人物、游戏战绩或梗图细节，随后结合语境自然吐槽或回复喵！{deskpet_voice_rule}"
        )
    except Exception as e:
        return f"查看图片异常: {str(e)}"
