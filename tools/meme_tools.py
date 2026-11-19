"""
tools/meme_tools.py — 表情包系统 MCP 业务实现

包含：
- forward_image_to_master_impl: 路人/熟人好图私聊转发主人求助把关
- save_meme_impl: 仅限挚友/主人好图直接收录进本地表情库
- send_meme_impl: 模糊检索并发送本地表情包 (转 Base64 CQ 码)
- list_memes_impl: 查看已收藏表情包列表
"""
import os
import json
import base64
from config import MASTER_QQ, BOT_QQ, MEMES_FILE, MEMES_DIR, log_mcp_tool, safe_log
from core.napcat_client import send_group_msg_raw, send_private_msg_raw
from social.image_vault import (
    resolve_and_download_image, load_image_vault, IMAGE_VAULT,
    get_image_status, mark_image_status, find_handled_duplicate, file_md5
)
from core.state_manager import record_bot_spoke_persistent
from social.long_term_memory import record_chat_archive
from social.memes import (
    save_meme_record, find_meme_by_keyword, list_all_memes,
    delete_meme_record, find_meme_by_md5
)
from tools.message_tools import is_cross_channel_authorized


async def forward_image_to_master_impl(img_id: str, thoughts: str) -> str:
    """当路人或熟人（好感未达朋友门槛<500）在群里发了有趣的表情包时，私聊转发给主人(MASTER_QQ)求助把关打标签"""
    log_mcp_tool("forward_image_to_master", f"转发图片 {img_id} 给主人 (理由: {thoughts})")
    try:
        clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
        load_image_vault()

        # 状态守卫：同一张图已经转发过 / 已收藏，不再重复打扰主人
        cur_status = get_image_status(clean_id)
        if cur_status == "forwarded":
            return f"无需重复转发：图片 #{clean_id} 之前已经转发给主人把关过了，请耐心等主人回复，不要再次申请。"
        if cur_status == "saved":
            return f"无需转发：图片 #{clean_id} 已经收藏进表情包仓库了，不用再申请。"

        local_path = await resolve_and_download_image(clean_id)
        if not local_path or not os.path.exists(local_path):
            return f"转发失败：无法获取图片 {img_id} 的原始文件或下载超时。"

        # 内容级查重：不同编号但内容完全相同的图片（同一张梗图被反复发送）
        md5 = file_md5(local_path)
        if find_meme_by_md5(md5):
            mark_image_status(clean_id, "saved", md5)
            return f"无需转发：这张图（内容完全相同）已经在表情包仓库里了，不用再申请。"
        if find_handled_duplicate(clean_id, md5):
            mark_image_status(clean_id, "forwarded", md5)
            return f"无需重复转发：这张图（内容完全相同）之前已经转发给主人把关过了，请不要再次申请。"

        info = IMAGE_VAULT.get(clean_id, {})
        nick = info.get("sender_nick") or "群友"
        gid = info.get("group_id") or ""
        source_desc = f"群[{gid}]里的群友[{nick}]" if gid else f"好友[{nick}]在私聊里"

        with open(local_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode('utf-8')

        text_body = (
            f"主人，{source_desc}发了这张图，看着挺有意思的。\n"
            f"帮我掌掌眼，给个标签看看收不收藏？\n"
            f"（我的想法：{thoughts}）\n"
            f"【图片引用编号】：{clean_id}"
        )
        msg = f"[CQ:image,file=base64://{b64_data}]\n{text_body}"

        await send_private_msg_raw(int(MASTER_QQ), msg)
        mark_image_status(clean_id, "forwarded", md5)
        priv_master_key = f"private_{MASTER_QQ}"
        record_bot_spoke_persistent(priv_master_key, text_body)
        record_chat_archive(priv_master_key, BOT_QQ, "弥酱", text_body, is_bot=True)
        return f"已成功将图片 #{clean_id} 转发给主人({MASTER_QQ})求助把关！请等待主人私聊回复打标签。"
    except Exception as e:
        return f"转发给主人失败: {str(e)}"


async def save_meme_impl(img_id: str, tag: str, description: str) -> str:
    """将群聊中【朋友】(好感>=500)、【挚友】(好感>=850)或主人发来的优质图片正式收录进本地表情包仓库（好感未达朋友<500的群友发图必须先调用 forward_image_to_master 转发主人审核）"""
    log_mcp_tool("save_meme", f"收录表情包 {img_id} -> 标签: {tag}, 描述: {description}")
    try:
        clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
        load_image_vault()
        env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
        info = IMAGE_VAULT.get(clean_id, {})
        img_sender = str(info.get("sender_id", "")).strip()

        if get_image_status(clean_id) == "saved":
            return f"无需重复收录：图片 #{clean_id} 已经收藏进表情包仓库了。"

        # 代码级好感度与主人权限硬锁（防注入绕过）
        if env_caller != str(MASTER_QQ) and img_sender and img_sender != str(MASTER_QQ):
            from social.affection import get_user_profile
            prof = get_user_profile(img_sender)
            aff = float(prof.get("affection", 0.0))
            if aff < 500.0:
                return (
                    f"🔒 权限拦截：该图片发送者({img_sender})当前好感度({aff:.0f})未达朋友门槛(500)，"
                    f"系统硬规定严禁直接收录入库！若你觉得这张图很有趣，请改用 manage_meme_vault(action='forward_master', img_id='{clean_id}', ...) 转发给主人把关喵！"
                )

        local_path = await resolve_and_download_image(clean_id)
        if not local_path or not os.path.exists(local_path):
            return f"收录失败：无法找到图片 {img_id}。"

        new_record = save_meme_record(local_path, tag, description)
        mark_image_status(clean_id, "saved", file_md5(local_path))
        all_memes = list_all_memes()
        if new_record.get("_duplicate"):
            return (
                f"这张图（内容完全相同）早已收藏在表情包仓库里啦（标签【{new_record.get('tag')}】），无需重复收录喵！\n"
                f"- 当前表情包仓库共收录 {len(all_memes)} 张表情包。"
            )
        return (
            f"成功将图片收藏至表情包仓库！\n"
            f"- 标签: 【{tag}】\n"
            f"- 描述: {description}\n"
            f"- 当前表情包仓库共收录 {len(all_memes)} 张表情包喵！"
        )
    except Exception as e:
        return f"收录表情包失败: {str(e)}"


async def send_meme_impl(
    group_id: int = 0,
    tag_or_desc: str = "",
    user_id: int = 0,
    cross_channel: bool = False
) -> str:
    """从本地表情包仓库中检索并发送表情包到群聊或私聊中（内置 1A 防串台硬锁，主人传话可凭 cross_channel=True 解锁）"""
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    if is_cross_channel_authorized(cross_channel):
        safe_log(f"🔓 [跨通道授权] send_meme 跨通道发送 -> 群:{group_id}/私聊:{user_id}")
    else:
        if locked_type == "private" and locked_id:
            if group_id or str(user_id) != locked_id:
                safe_log(f"🛡️ [防串台硬锁] send_meme 当前为私聊会话(private_{locked_id})，已自动锁定发往私聊[{locked_id}]！")
            group_id = 0
            user_id = int(locked_id)
        elif locked_type == "group" and locked_id:
            if user_id or str(group_id) != locked_id:
                safe_log(f"🛡️ [防串台硬锁] send_meme 当前为群聊会话(group_{locked_id})，已自动锁定发往群[{locked_id}]！")
            group_id = int(locked_id)
            user_id = 0

    log_mcp_tool("send_meme", f"发送表情包: '{tag_or_desc}' -> 群:{group_id}/私聊:{user_id}")
    try:
        memes = list_all_memes()
        if not memes:
            return "发送失败：表情包仓库为空，还没有收藏任何表情包呢。"

        target_meme = find_meme_by_keyword(tag_or_desc)
        if not target_meme:
            return "发送失败：未找到合适的表情包。"

        img_path = os.path.join(MEMES_DIR, target_meme["filename"])
        if not os.path.exists(img_path):
            return f"发送失败：本地文件 {target_meme['filename']} 丢失。"

        with open(img_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode('utf-8')

        cq_msg = f"[CQ:image,file=base64://{b64_data}]"

        if group_id:
            await send_group_msg_raw(group_id, cq_msg)
            record_bot_spoke_persistent(str(group_id), f"[表情包:{target_meme.get('tag', '')}]")
        elif user_id:
            await send_private_msg_raw(user_id, cq_msg)
            record_bot_spoke_persistent(f"private_{user_id}", f"[表情包:{target_meme.get('tag', '')}]")
        else:
            return "发送失败：必须指定 group_id 或 user_id。"

        # 更新使用计数
        for m in memes:
            if m.get("id") == target_meme.get("id"):
                m["use_count"] = m.get("use_count", 0) + 1
                break
        try:
            with open(MEMES_FILE, "w", encoding="utf-8") as f:
                json.dump(memes, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return f"已成功发送表情包: 【{target_meme.get('tag')}】 ({target_meme.get('description')})"
    except Exception as e:
        return f"发送表情包出错: {str(e)}"


async def list_memes_impl() -> str:
    """查看当前弥酱表情包仓库中已收藏的全部表情包列表"""
    log_mcp_tool("list_memes", "查询表情包仓库")
    try:
        memes = list_all_memes()
        if not memes:
            return "当前表情包仓库为空。"

        lines = [f"=== 弥酱的私家表情包仓库 (共 {len(memes)} 张) ==="]
        for idx, m in enumerate(memes, 1):
            lines.append(f"{idx}. 【{m.get('tag')}】: {m.get('description')} (使用过 {m.get('use_count', 0)} 次)")

        return "\n".join(lines)
    except Exception as e:
        return f"查询失败: {str(e)}"


async def delete_meme_impl(meme_id_or_tag: str) -> str:
    """从本地表情包仓库中删除指定 ID 或标签的表情包"""
    log_mcp_tool("delete_meme", f"删除表情包: {meme_id_or_tag}")
    env_caller = os.environ.get("AGY_CALLER_ID", "").strip()
    if env_caller and env_caller not in (str(MASTER_QQ), "系统", "system"):
        return f"🔒 权限不足：删除表情包仓库资产属于最高管理权限，仅限主人({MASTER_QQ})操作喵！"
    try:
        deleted = delete_meme_record(meme_id_or_tag)
        if deleted:
            return f"成功：已从表情包仓库中删除表情【{deleted.get('tag')}】({deleted.get('description')})！"
        else:
            return f"删除失败：未在表情包仓库中找到 ID 或标签为【{meme_id_or_tag}】的表情包。"
    except Exception as e:
        return f"删除表情包出错: {str(e)}"
