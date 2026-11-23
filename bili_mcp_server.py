"""
bili_mcp_server.py — 弥酱 Bilibili 专属独立 MCP 服务入口 V3.0 (高内聚领域版)

定位：
与 qq_mcp_server.py 保持物理隔离的微服务节点。
负责将大模型的 B站 冲浪、读字幕、热评洞察与真实账号互动转化为底层动作。
按领域高内聚收拢为 4 大专业工具：
1. bili_video: 视频透视、检索与足迹 (inspect / search / history)
2. bili_social: 社交互动与用户档案 (like / favorite / unfavorite / list_folders / folder_content / reply / follow / user_info)
3. bili_inbox: 消息收件箱与私信 (unread / replies / at / recent_dms / send_dm)
4. bili_auth: 账号凭证与扫码认证 (whoami / login_qr / login_check / logout)

支持 AGY_TOOL_DOMAINS 动态激活控制：未指定 "bili" 领域时零开销挂起。
"""
import os
import sys
import json
import asyncio
import builtins
import contextlib

# 标记 stdio MCP 模式：在所有模块导入与工具运行时阻断向 stdout 打印非 JSON-RPC 文本
os.environ.setdefault("QQ_MCP_STDIO_MODE", "1")


def _install_mcp_print_guard():
    """保证运行期任意 print() 默认写入 stderr，确保 MCP JSON-RPC stdout 传输绝对纯净。"""
    orig_print = builtins.print

    def _stderr_default_print(*args, **kwargs):
        target = kwargs.get("file")
        if target is None or target is sys.stdout:
            kwargs["file"] = sys.stderr
        return orig_print(*args, **kwargs)

    builtins.print = _stderr_default_print


with contextlib.redirect_stdout(sys.stderr):
    from mcp.server.mcpserver import MCPServer
    from config import sanitize_process_env, log_mcp_tool
    from social.bilibili_auth import (
        verify_bili_credential, generate_login_qr, poll_login_status,
        clear_bili_credential
    )
    from social.bilibili_client import (
        resolve_bvid, get_video_details, get_video_subtitles,
        get_video_hot_comments, search_videos, favorite_video,
        like_video, reply_video, follow_up, report_video_heartbeat, get_watch_history,
        get_bili_unread_messages, get_bili_received_replies, get_bili_received_at,
        get_bili_recent_dms, send_bili_dm, get_user_favorite_folders,
        get_favorite_folder_videos, unfavorite_video, get_bili_user_info,
        publish_bili_dynamic
    )

    # 确保无头环境纯净
    sanitize_process_env()

    mcp = MCPServer("bilibili-agent-mcp")


def is_bili_domain_enabled() -> bool:
    """检查当前会话是否需要激活 B站 生态工具"""
    domains_env = os.environ.get("AGY_TOOL_DOMAINS", "all").strip().lower()
    if domains_env == "none":
        return False
    if domains_env == "all":
        return True
    enabled = set(d.strip() for d in domains_env.split(",") if d.strip())
    return "bili" in enabled


# ============================================================
# 仅当 bili 领域启用时，注册以下 4 大高内聚工具
# ============================================================
if is_bili_domain_enabled():

    # --------------------------------------------------------
    # 1. 视频内容、搜索与历史 (bili_video)
    # --------------------------------------------------------
    @mcp.tool()
    async def bili_video(
        action: str,
        bvid_or_url: str = "",
        keyword: str = "",
        num: int = 8,
        order: str = "totalrank"
    ) -> str:
        """
        【B站视频服务】透析视频内容、全网搜索或查看足迹。
        - action='inspect': 深度透视单个视频（标题、UP主、AI字幕全文、热门前排评论）。需传入 bvid_or_url。
        - action='search': 全网搜索视频列表。需传入 keyword，可选 order('totalrank'|'click'|'pubdate') 和 num。
        - action='history': 查看弥酱自己近期看过的视频历史列表。可选 num。
        """
        log_mcp_tool("bili_video", f"action={action}, target={bvid_or_url or keyword}")
        act = (action or "").strip().lower()

        if act == "inspect":
            bvid = await resolve_bvid(bvid_or_url)
            if not bvid:
                return json.dumps({"ok": False, "error": f"无法从【{bvid_or_url}】中解析出有效的 BV 号或短链"}, ensure_ascii=False)

            detail = await get_video_details(bvid)
            if not detail.get("ok"):
                return json.dumps(detail, ensure_ascii=False)

            cid = detail.get("cid", 0)
            sub_task = get_video_subtitles(bvid, cid=cid)
            com_task = get_video_hot_comments(bvid, num=8)
            sub_res, comments = await asyncio.gather(sub_task, com_task)

            asyncio.create_task(report_video_heartbeat(bvid, cid=cid, aid=detail.get("aid", 0), duration=detail.get("duration", 0)))

            result = {
                "ok": True,
                "bvid": bvid,
                "title": detail.get("title"),
                "author": detail.get("author"),
                "play": detail.get("play"),
                "danmaku": detail.get("danmaku"),
                "duration_seconds": detail.get("duration"),
                "description": detail.get("desc"),
                "has_subtitle": sub_res.get("has_subtitle", False),
                "subtitles_text": sub_res.get("full_text", "") if sub_res.get("has_subtitle") else "(本视频暂无官方字幕)",
                "hot_comments": comments
            }
            return json.dumps(result, ensure_ascii=False)

        elif act == "search":
            if not keyword:
                return json.dumps({"ok": False, "error": "搜索操作必须提供 keyword 参数"}, ensure_ascii=False)
            res = await search_videos(keyword, num=max(1, min(int(num), 20)), order=order)
            return json.dumps({"ok": True, "keyword": keyword, "count": len(res), "results": res}, ensure_ascii=False)

        elif act == "history":
            res = await get_watch_history(num=max(1, min(int(num), 30)))
            return json.dumps(res, ensure_ascii=False)

        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'inspect' | 'search' | 'history'"}, ensure_ascii=False)


    # --------------------------------------------------------
    # 2. 视频社交互动 (bili_social)
    # --------------------------------------------------------
    @mcp.tool()
    async def bili_social(
        action: str,
        bvid_or_url: str = "",
        message: str = "",
        rpid: int = 0,
        parent_rpid: int = 0,
        folder: str = "默认收藏夹",
        mid_or_uid: int = 0,
        page: int = 1
    ) -> str:
        """
        【B站社交与收藏互动】对 B站 视频、UP 主或收藏夹执行操作。
        - action='like': 点赞视频。需传入 bvid_or_url。
        - action='favorite': 收藏视频到分类收藏夹。需传入 bvid_or_url，可选 folder。
        - action='unfavorite': 取消收藏视频。需传入 bvid_or_url，可选 folder。
        - action='list_folders': 查看弥酱的所有收藏夹列表（含名称、ID、视频数）。
        - action='folder_content': 查看指定收藏夹中的视频列表。可传入 folder (名称或ID) 与 page。
        - action='reply': 发表视频评论或楼中楼回复。需传入 bvid_or_url 与 message；rpid>0 时为楼中楼回复，parent_rpid 可对准具体子评论。
        - action='follow': 关注 UP 主。需传入 mid_or_uid。
        - action='user_info': 查询自己(mid_or_uid=0)或指定UP主(mid_or_uid>0)的B站个人空间档案与最新投稿。
        - action='publish_dynamic': 发布一条 B站 动态（3C 混合双轨：现代协议优先 + Playwright 兜底）。需传入 message，可选传 bvid_or_url 自动附带封面图与视频直链。
        """
        log_mcp_tool("bili_social", f"action={action}, target={bvid_or_url or mid_or_uid or folder}")
        act = (action or "").strip().lower()

        if act == "like":
            bvid = await resolve_bvid(bvid_or_url)
            if not bvid:
                return json.dumps({"ok": False, "error": "无效的 BV 号或短链"}, ensure_ascii=False)
            res = await like_video(bvid)
            return json.dumps(res, ensure_ascii=False)

        elif act == "favorite":
            bvid = await resolve_bvid(bvid_or_url)
            if not bvid:
                return json.dumps({"ok": False, "error": "无效的 BV 号或短链"}, ensure_ascii=False)
            res = await favorite_video(bvid, folder_name=folder)
            return json.dumps(res, ensure_ascii=False)

        elif act == "unfavorite":
            bvid = await resolve_bvid(bvid_or_url)
            if not bvid:
                return json.dumps({"ok": False, "error": "无效的 BV 号或短链"}, ensure_ascii=False)
            res = await unfavorite_video(bvid, folder_name=folder)
            return json.dumps(res, ensure_ascii=False)

        elif act == "list_folders":
            res = await get_user_favorite_folders()
            return json.dumps(res, ensure_ascii=False)

        elif act == "folder_content":
            res = await get_favorite_folder_videos(folder_name_or_id=folder, page=page)
            return json.dumps(res, ensure_ascii=False)

        elif act == "reply":
            bvid = await resolve_bvid(bvid_or_url)
            if not bvid:
                return json.dumps({"ok": False, "error": "无效的 BV 号或短链"}, ensure_ascii=False)
            if not message:
                return json.dumps({"ok": False, "error": "发表评论必须传入 message 内容"}, ensure_ascii=False)
            res = await reply_video(bvid, message=message, rpid=rpid, parent_rpid=parent_rpid)
            return json.dumps(res, ensure_ascii=False)

        elif act == "follow":
            if not mid_or_uid:
                return json.dumps({"ok": False, "error": "关注 UP 主必须提供 mid_or_uid 参数"}, ensure_ascii=False)
            res = await follow_up(int(mid_or_uid))
            return json.dumps(res, ensure_ascii=False)

        elif act == "user_info":
            res = await get_bili_user_info(uid=int(mid_or_uid or 0))
            return json.dumps(res, ensure_ascii=False)

        elif act == "publish_dynamic":
            if not (message or "").strip():
                return json.dumps({"ok": False, "error": "发布 B站 动态必须提供 message 正文内容"}, ensure_ascii=False)
            bvid = ""
            cover_url = ""
            if (bvid_or_url or "").strip():
                bvid = await resolve_bvid(bvid_or_url) or ""
                if bvid:
                    det = await get_video_details(bvid)
                    if det.get("ok"):
                        cover_url = str(det.get("pic") or "")
            res = await publish_bili_dynamic(text=message.strip(), image_url=cover_url, bvid=bvid)
            return json.dumps(res, ensure_ascii=False)

        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'like' | 'favorite' | 'unfavorite' | 'list_folders' | 'folder_content' | 'reply' | 'follow' | 'user_info' | 'publish_dynamic'"}, ensure_ascii=False)


    # --------------------------------------------------------
    # 3. 收件箱与私信中心 (bili_inbox)
    # --------------------------------------------------------
    @mcp.tool()
    async def bili_inbox(
        action: str,
        limit: int = 10,
        receiver_uid: int = 0,
        message: str = ""
    ) -> str:
        """
        【B站收件箱与私信】查看收到的互动消息或发送私信。
        - action='unread': 查看未读消息概况（回复、@我的、收到的赞、私信未读数）。
        - action='replies': 获取“回复我的”评论列表（含对方言论与 rpid，可配合 bili_social reply 回复）。可选 limit。
        - action='at': 获取“@我的”消息列表。可选 limit。
        - action='recent_dms': 获取近期私信会话与对话记录。可选 limit。
        - action='send_dm': 向指定 UID 发送 B站 私信。需传入 receiver_uid 与 message。
        """
        log_mcp_tool("bili_inbox", f"action={action}, receiver={receiver_uid}")
        act = (action or "").strip().lower()
        num_clamped = max(1, min(int(limit or 10), 20))

        if act == "unread":
            res = await get_bili_unread_messages()
            return json.dumps(res, ensure_ascii=False)

        elif act == "replies":
            res = await get_bili_received_replies(limit=num_clamped)
            return json.dumps(res, ensure_ascii=False)

        elif act == "at":
            res = await get_bili_received_at(limit=num_clamped)
            return json.dumps(res, ensure_ascii=False)

        elif act == "recent_dms":
            res = await get_bili_recent_dms(limit=min(num_clamped, 10))
            return json.dumps(res, ensure_ascii=False)

        elif act == "send_dm":
            if not receiver_uid or not message:
                return json.dumps({"ok": False, "error": "发送私信必须提供 receiver_uid 和 message 参数"}, ensure_ascii=False)
            res = await send_bili_dm(receiver_uid=int(receiver_uid), content=message)
            return json.dumps(res, ensure_ascii=False)

        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'unread' | 'replies' | 'at' | 'recent_dms' | 'send_dm'"}, ensure_ascii=False)


    # --------------------------------------------------------
    # 4. 账号状态与凭证管理 (bili_auth)
    # --------------------------------------------------------
    @mcp.tool()
    async def bili_auth(action: str) -> str:
        """
        【B站账号认证】检查登录状态、发起扫码登录或退出。
        - action='whoami': 检查当前 B站 登录状态、昵称、UID、等级和头像。
        - action='login_qr': 生成登录二维码与密钥（专属主人绑定使用）。
        - action='login_check': 轮询刚才二维码的扫码状态与自动保存凭证。
        - action='logout': 退出并清除本地 B站 登录凭证。
        """
        log_mcp_tool("bili_auth", f"action={action}")
        act = (action or "").strip().lower()

        if act == "whoami":
            res = await verify_bili_credential()
            return json.dumps(res, ensure_ascii=False)

        elif act == "login_qr":
            res = await generate_login_qr()
            return json.dumps(res, ensure_ascii=False)

        elif act == "login_check":
            res = await poll_login_status()
            return json.dumps(res, ensure_ascii=False)

        elif act == "logout":
            ok = clear_bili_credential()
            return json.dumps({
                "ok": ok,
                "message": "已成功注销并清除本地 B站 登录凭证" if ok else "当前本地暂无登录凭证或清除失败"
            }, ensure_ascii=False)

        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'whoami' | 'login_qr' | 'login_check' | 'logout'"}, ensure_ascii=False)


if __name__ == "__main__":
    # 保持与 FastMCP stdio 兼容启动
    _install_mcp_print_guard()
    mcp.run()
