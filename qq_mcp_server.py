"""
qq_mcp_server.py — 弥酱 QQ Agent MCP Server 根入口 V3.5 (高内聚领域折叠版)

定位：
微服务装配层：按业务领域划分高内聚工具集，通过 AGY_TOOL_DOMAINS 环境变量支持动态按需激活。
六大专业领域：
1. chat (核心沟通与社交, 9工具): send_group_msg, send_private_msg, send_voice_msg, send_meme, poke_user, send_local_image, inspect_image, leisure_action, affection_action
2. memory (记忆心事, 2工具): search_memory, memory_action
3. group_admin (群聊治理与时事, 3工具): manage_group_ban, query_contacts, query_community_trends
4. meme (表情资产, 1工具): manage_meme_vault
5. system_admin (空间动态与特关, 1工具): qzone_action
6. game (桌游娱乐, 1工具): play_board_game
"""
import os
import sys
import json
import builtins
import contextlib
import subprocess

# 标记 stdio MCP 模式：在所有模块导入与工具运行时阻断向 stdout 打印非 JSON-RPC 文本
os.environ.setdefault("QQ_MCP_STDIO_MODE", "1")


def _trigger_quota_sync_subprocess():
    """若宿主机存在 Dashboard 配额同步脚本，以完全静默子进程触发，严禁线程改写 sys.stdout。"""
    dashboard_path = os.environ.get("AGY_DASHBOARD_PATH", "/path/to/antigravityDashBoard")
    if not os.path.isdir(dashboard_path):
        return
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.append({dashboard_path!r}); "
                "from quota_sync import sync_quota_cache_now; sync_quota_cache_now()",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


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
    from tools.message_tools import (
        send_group_msg_impl, send_private_msg_impl, get_group_list_impl,
        get_friend_list_impl, poke_user_impl
    )
    from tools.admin_tools import (
        ban_group_member_impl, unban_group_member_impl
    )
    from tools.image_tools import (
        send_image_to_group_impl, send_image_to_private_impl,
        inspect_image_impl
    )
    from tools.memory_tools import (
        record_pending_thought_impl,
        resolve_pending_thought_impl,
        resolve_memory_fact_impl,
        save_long_term_memory_impl,
        search_memory_impl
    )
    from tools.affection_tools import (
        complain_to_master_impl, query_affection_impl, adjust_affection_impl
    )
    from tools.meme_tools import (
        forward_image_to_master_impl, save_meme_impl,
        send_meme_impl, list_memes_impl, delete_meme_impl
    )
    from tools.qzone_tools import (
        publish_qzone_feed_impl, recall_qzone_memory_impl,
        like_friend_qzone_feed_impl, comment_friend_qzone_feed_impl,
        manage_qzone_special_care_impl
    )
    from tools.music_tools import (
        search_music_card_impl, play_master_song_impl
    )
    from tools.omikuji_tools import (
        draw_daily_omikuji_impl
    )
    from tools.trend_tools import (
        get_morning_trends_impl, search_current_meme_impl
    )
    from tools.voice_tools import (
        send_voice_msg_impl
    )
    from toys.board_games import (
        play_board_game_impl
    )

    sanitize_process_env()
    mcp = MCPServer("qq-mcp-server")


def is_domain_enabled(domain_name: str) -> bool:
    """检查特定领域是否允许向大模型激活"""
    domains_env = os.environ.get("AGY_TOOL_DOMAINS", "all").strip().lower()
    if domains_env == "none":
        return False
    if domains_env == "all":
        return True
    enabled = set(d.strip() for d in domains_env.split(",") if d.strip())
    return domain_name in enabled


# ============================================================
# 1. 核心高频沟通域 (chat) — 日常互动常驻，零参数负担，极速响应
# ============================================================
if is_domain_enabled("chat"):

    @mcp.tool()
    async def send_group_msg(
        group_id: int,
        message: str,
        auto_split: bool = True,
        reply_to: int = 0,
        at_user: str = "",
        cross_channel: bool = False
    ) -> str:
        """发送消息到指定的QQ群。默认开启自动按换行与语气切分为多条短气泡连发（仅在输出代码块或结构化长列表时将 auto_split 设为 False）。支持 reply_to 引用或 at_user 艾特。cross_channel 默认 False（锁定当前通道防发错）；仅当主人明确指令你去指定群传话时设为 True。"""
        return await send_group_msg_impl(group_id, message, auto_split=auto_split, reply_to=reply_to, at_user=at_user, cross_channel=cross_channel)


    @mcp.tool()
    async def send_private_msg(
        user_id: int,
        message: str,
        auto_split: bool = True,
        cross_channel: bool = False
    ) -> str:
        """发送私聊消息给指定的QQ好友。默认开启自动按换行与语气切分为多条短气泡连发（仅在输出代码块或结构化长列表时将 auto_split 设为 False）。cross_channel 默认 False（锁定当前通道防发错）；仅当主人明确指令你去私聊某人传话时设为 True。"""
        return await send_private_msg_impl(user_id, message, auto_split=auto_split, cross_channel=cross_channel)


    @mcp.tool()
    async def send_voice_msg(
        text: str,
        emotion: str = "auto",
        group_id: int = 0,
        user_id: int = 0,
        cross_channel: bool = False
    ) -> str:
        """发送弥酱音色语音消息（支持中/日/英混读），可单独发或与文字搭配。
        - text: 纯口语台词，勿带括号动作或表情符号（建议5~50字自然短句）
        - emotion: 'auto'|'normal'|'gentle'|'sweet'|'tsundere'|'tease'|'angry'|'sad'|'shy'|'whisper'，支持 '主+副' 复合（如 'tsundere+shy'）
        - group_id / user_id: 目标群号或私聊QQ号
        - cross_channel: 默认 False；仅当主人明确指令跨通道发送时设为 True"""
        return await send_voice_msg_impl(text=text, emotion=emotion, group_id=group_id, user_id=user_id, cross_channel=cross_channel)


    @mcp.tool()
    async def send_meme(
        group_id: int = 0,
        tag_or_desc: str = "",
        user_id: int = 0,
        cross_channel: bool = False
    ) -> str:
        """从本地表情包仓库中检索并发送表情包到群聊或私聊中。cross_channel 默认 False（防发错通道），仅主人指令跨通道发送时设为 True。"""
        return await send_meme_impl(group_id, tag_or_desc, user_id, cross_channel=cross_channel)


    @mcp.tool()
    async def poke_user(group_id: int = 0, user_id: int = 0, cross_channel: bool = False) -> str:
        """对指定群成员或私聊好友发送戳一戳动作。支持在被群友戳了之后回戳互动，或向主人/挚友打趣互动。cross_channel 默认 False，仅主人明确指令跨通道时设为 True。"""
        return await poke_user_impl(group_id, user_id, cross_channel=cross_channel)


    @mcp.tool()
    async def send_local_image(
        image_path: str,
        group_id: int = 0,
        user_id: int = 0,
        cross_channel: bool = False
    ) -> str:
        """发送本地生成的图片到指定的QQ群或私聊好友（二选一填入 group_id 或 user_id，支持 Base64 内存直传）。cross_channel 默认 False，仅主人明确指令跨通道传话时设为 True。"""
        if group_id and int(group_id) > 0:
            return await send_image_to_group_impl(int(group_id), image_path, cross_channel=cross_channel)
        if user_id and int(user_id) > 0:
            return await send_image_to_private_impl(int(user_id), image_path, cross_channel=cross_channel)
        return "错误：必须提供有效的 group_id 或 user_id。"


    @mcp.tool()
    async def inspect_image(img_id: str, question: str = "") -> str:
        """查看并感知聊天中发送的图片（如 [图片#img_X]）。会自动确保图片就绪并输出本地路径，结合视觉能力看懂游戏战绩、表情包细节。"""
        return await inspect_image_impl(img_id, question)


    @mcp.tool()
    async def leisure_action(
        action: str,
        keyword: str = "",
        reason: str = "",
        user_id: str = "",
        nickname: str = ""
    ) -> str:
        """
        【休闲娱乐综合工具：音乐点播与浅草寺御神签】（注意：本工具返回推荐文案/CQ卡片或签文文本，拿到返回结果后仍需调用 send_group_msg 或 send_private_msg 发送给对方）
        - action='master_song': 从主人的私藏曲库挑选宝藏音乐并生成 OneBot 音乐卡片（可选填 keyword 筛选曲风/歌手，或填 reason 推荐语）。
        - action='search_music': 在网易云音乐全网搜索指定歌曲并生成 OneBot 音乐卡片（必填 keyword 歌名或歌手，可选 reason）。
        - action='omikuji': 为指定用户抽取今日浅草寺猫猫御神签，每日每人限抽一次（必填 user_id，可选 nickname）。
        """
        act = (action or "").strip().lower()
        if act == "master_song":
            return await play_master_song_impl(keyword, reason)
        elif act == "search_music":
            return await search_music_card_impl(keyword, reason)
        elif act == "omikuji":
            return await draw_daily_omikuji_impl(user_id, nickname)
        return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'master_song' | 'search_music' | 'omikuji'"}, ensure_ascii=False)


    @mcp.tool()
    async def affection_action(
        action: str = "query",
        user_id: str = "",
        inquirer_id: str = "",
        amount: float = 5.0,
        reason: str = "",
        set_score: float = None,
        approve_pending: bool = False
    ) -> str:
        """
        【好感度查询、情绪调节与向主人告状/审批统一工具】
        - action='query': 查询目标用户的好感度与关系印象（传 user_id 目标QQ, inquirer_id 提问者QQ；主人获精确数值，普通群友获主观模糊印象）。
        - action='self_5pct': 弥酱自己觉得聊得开心或生气时，自主增/扣最多 ±5% 好感度（amount 传 -5.0 ~ +5.0 代表百分比，每日加分限2次，扣分不限）。
        - action='request_master': 弥酱特别激动（超级开心感动或极其生气委屈）想突破5%大额加减分时，向主人私聊发起申请（传 user_id, amount 拟增减分值, reason 原因）。
        - action='complain_master': 遇到持续不友善或冒犯发言的用户时，私聊向主人反馈倾诉（传 user_id 与 reason）。
        - action='master_direct': 仅当主人明确下令给某人加减/设定好感或批准申请时调用（可传 amount、set_score 或 approve_pending=True）。
        """
        act = (action or "query").strip().lower()
        if act == "query":
            return await query_affection_impl(user_id=user_id, inquirer_id=inquirer_id)
        elif act in ("self_5pct", "request_master", "master_direct"):
            return await adjust_affection_impl(
                user_id=user_id,
                mode=act,
                amount=amount,
                reason=reason,
                set_score=set_score,
                approve_pending=approve_pending,
            )
        elif act == "complain_master":
            return await complain_to_master_impl(user_id=user_id, reason=reason)
        return json.dumps({"ok": False, "error": f"未知 action '{action}'，可选 'query' | 'self_5pct' | 'request_master' | 'complain_master' | 'master_direct'"}, ensure_ascii=False)


# ============================================================
# 2. 记忆与心事域 (memory) — L3 长期记忆库与挂念心事
# ============================================================
if is_domain_enabled("memory"):

    @mcp.tool()
    async def search_memory(query: str, target_id: str = "auto", include_archive: bool = True) -> str:
        """毫秒级混合检索 L3 永久长效记忆库与全量历史聊天归档（无需猜 days_ago！只需传入关键词或话题 query 即可跨天跨月搜出长期事实与历史原话）。"""
        return await search_memory_impl(query, target_id, include_archive)


    @mcp.tool()
    async def memory_action(
        action: str,
        target_id: str = "auto",
        content: str = "",
        category: str = "user_fact",
        keywords: str = "",
        fact_key: str = "",
        fact_id: str = "",
        keyword: str = "",
        expire_days: int = 3,
        reason: str = ""
    ) -> str:
        """
        【长期记忆铭记、心事记录与约定核销统一工具】
        - action='save_fact': 将重要事实、主人或群友喜好/配置、双方约定或群梗永久存入 L3 记忆库（传 target_id, content, 可选 category='user_fact'|'shared_promise'|'group_meme', keywords, fact_key 如 'ta.初识备注' 自动覆盖同键旧状态）。
        - action='add_thought': 在心里暗暗记下一件关于某人的未尽小事或打算后续关心的话题（传 target_id 为对方QQ, content 为心事内容, expire_days=1~7 默认3天）。
        - action='resolve': 核销已兑现/已完成的约定、待办或放下已聊完的心事（同步清理 L3 约定、L2 动态与心里挂念；可传 fact_id 如 '#L3:12'/'pt:xxx'，或传 keyword 关键词，以及 target_id, reason）。
        """
        act = (action or "").strip().lower()
        if act == "save_fact":
            return await save_long_term_memory_impl(target_id, content, category, keywords, fact_key)
        elif act == "add_thought":
            return await record_pending_thought_impl(target_id, content, expire_days)
        elif act in ("resolve", "resolve_thought", "resolve_fact"):
            eff_kw = keyword or keywords or content
            if act == "resolve_thought" and not fact_id and target_id and eff_kw:
                return await resolve_pending_thought_impl(target_id, eff_kw)
            return await resolve_memory_fact_impl(
                fact_id=fact_id,
                target_id=target_id,
                keyword=eff_kw,
                reason=reason
            )
        return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'save_fact' | 'add_thought' | 'resolve'"}, ensure_ascii=False)


# ============================================================
# 3. 群聊治理与时事域 (group_admin) — 禁言惩戒、群列表与时事热梗（群白名单与Bot白名单已迁移至WebUI）
# ============================================================
if is_domain_enabled("group_admin"):

    @mcp.tool()
    async def manage_group_ban(
        action: str,
        group_id: int,
        user_id: int,
        duration_minutes: int = 3,
        reason: str = ""
    ) -> str:
        """
        【群成员发言管理】对违规刷屏或不当发言的群成员设置或解除暂时禁言（需弥酱为群管理员）。
        - action='ban': 设置 1~30 分钟暂时禁言冷静期（严禁对主人及群主管理员使用）。
        - action='unban': 解除指定成员的禁言状态。
        """
        act = (action or "").strip().lower()
        if act == "ban":
            return await ban_group_member_impl(group_id, user_id, duration_minutes, reason)
        elif act == "unban":
            return await unban_group_member_impl(group_id, user_id)
        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'ban' | 'unban'"}, ensure_ascii=False)


    @mcp.tool()
    async def query_contacts(contact_type: str = "all") -> str:
        """
        【联系人与群列表查询】
        - contact_type='groups': 获取机器人当前所在的全部QQ群列表。
        - contact_type='friends': 获取机器人的QQ好友列表。
        - contact_type='all': 同时获取群聊与好友列表概况。
        """
        ctype = (contact_type or "all").strip().lower()
        if ctype == "groups":
            return await get_group_list_impl()
        elif ctype == "friends":
            return await get_friend_list_impl()
        else:
            g_res = await get_group_list_impl()
            f_res = await get_friend_list_impl()
            return f"【群聊列表】\n{g_res}\n\n【好友列表】\n{f_res}"


    @mcp.tool()
    async def query_community_trends(action: str = "trends", keyword: str = "") -> str:
        """
        【时事热搜与网络热梗】
        - action='trends': 获取今晨弥酱浏览到的热搜榜单与时事摘要（每天缓存复用）。
        - action='meme': 动态追查网络新梗、流行缩写或突发事件含义（需传入 keyword）。
        """
        act = (action or "trends").strip().lower()
        if act == "meme":
            if not keyword:
                return "请传入要查询的梗或热词关键词 keyword"
            return await search_current_meme_impl(keyword)
        else:
            return await get_morning_trends_impl()


# ============================================================
# 4. 表情包资产域 (meme) — 仓库收藏、删除与把关审核
# ============================================================
if is_domain_enabled("meme"):

    @mcp.tool()
    async def manage_meme_vault(
        action: str,
        img_id: str = "",
        tag: str = "",
        description: str = "",
        meme_id_or_tag: str = "",
        thoughts: str = ""
    ) -> str:
        """
        【表情包资产库维护】
        - action='save': 正式收录优质表情包入库（好感>=500或主人专属）。需传入 img_id, tag, description。
        - action='delete': 从表情包仓库删除指定表情包。需传入 meme_id_or_tag。
        - action='list': 查看仓库中已收藏的全部表情包列表。
        - action='forward_master': 路人/熟人发了好图时，私聊转发给自家主人求助把关。需传入 img_id 与 thoughts。
        """
        act = (action or "").strip().lower()
        if act == "save":
            return await save_meme_impl(img_id, tag, description)
        elif act == "delete":
            return await delete_meme_impl(meme_id_or_tag)
        elif act == "list":
            return await list_memes_impl()
        elif act == "forward_master":
            return await forward_image_to_master_impl(img_id, thoughts)
        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'save' | 'delete' | 'list' | 'forward_master'"}, ensure_ascii=False)


# ============================================================
# 5. 空间动态与特关管理域 (system_admin) — 说说日记、好友圈互动与特关名单
# ============================================================
if is_domain_enabled("system_admin"):

    @mcp.tool()
    async def qzone_action(
        action: str,
        content: str = "",
        mood: str = "日常",
        topics: str = "",
        days_ago: int = -1,
        keyword: str = "",
        friend_uin: str = "",
        feed_tid: str = "",
        comment_text: str = "",
        caller_id: str = "",
        nickname: str = "",
        reason: str = ""
    ) -> str:
        """
        【QQ空间说说日记、好友圈互动与特别关注管理】
        - action='publish': 发布一条空间说说日记（仅限主人指令或系统定时任务）。需传 content, mood, topics。
        - action='recall': 搜索回忆历史空间说说。可选 days_ago(-1为全部) 和 keyword。
        - action='like': 给熟人好友空间说说点赞。需传 friend_uin 与 feed_tid。
        - action='comment': 给好友说说发表评论。需传 friend_uin, feed_tid, comment_text。
        - action='care_list': 查看当前生效的 QQ 空间【特别关注（特关）】好友名单。
        - action='care_add': 将合拍的熟人好友(好感>=200)或主人指定的用户加入空间特别关注（传 friend_uin 目标QQ, 可选 nickname, reason）。
        - action='care_remove': 将指定用户移出空间特别关注（传 friend_uin 目标QQ，仅主人可用）。
        """
        act = (action or "").strip().lower()
        if act == "publish":
            return await publish_qzone_feed_impl(content, mood, topics, caller_id)
        elif act == "recall":
            return await recall_qzone_memory_impl(days_ago, keyword)
        elif act == "like":
            return await like_friend_qzone_feed_impl(friend_uin, feed_tid)
        elif act == "comment":
            return await comment_friend_qzone_feed_impl(friend_uin, feed_tid, comment_text)
        elif act in ("care_list", "care_add", "care_remove"):
            sub_act = act.replace("care_", "")
            return await manage_qzone_special_care_impl(
                action=sub_act,
                user_id=friend_uin,
                nickname=nickname,
                reason=reason or content
            )
        else:
            return json.dumps({"ok": False, "error": f"未知 action '{action}'，必须为 'publish' | 'recall' | 'like' | 'comment' | 'care_list' | 'care_add' | 'care_remove'"}, ensure_ascii=False)


if is_domain_enabled("game"):
    @mcp.tool()
    async def play_board_game(
        action: str = "start",
        game_type: str = "gomoku",
        difficulty: str = "normal",
        move: str = "",
        chat_id: str = "",
        user_id: str = "",
        user_name: str = "",
    ) -> str:
        """
        [桌游小游戏中心] 与当前聊天对象开启或操作零费用本地棋牌小游戏（支持悔棋、认输与棋局状态查看）。
        - action:
          * "start": 新开一局（会自动生成并发送开局视觉棋盘/牌桌图给对方）
          * "move": 落子或出牌（move 填坐标如 "H8"、"D3"，或 21点指令 "要牌"/"停牌"/"双倍"，或 1A2B "1234"）
          * "undo": 悔棋（退回上一步）
          * "resign": 认输并结束当前对局
          * "status": 查看当前棋局进度
        - game_type: "gomoku"(猫爪五子棋) | "othello"(翻转黑白棋) | "blackjack"(猫猫21点) | "1a2b"(破译密码)
        - difficulty: "easy"(轻松入门) | "normal"(认真较量) | "hard"(全力以赴)
        - chat_id / user_id / user_name: 可留空，默认自动锁定当前会话频道与发言者。
        """
        locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
        locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
        eff_uid = str(user_id or os.environ.get("AGY_CALLER_ID", "") or (locked_id if locked_type == "private" else "")).strip()
        eff_uname = str(user_name or eff_uid).strip()
        eff_cid = str(chat_id or "").strip()
        if not eff_cid:
            if locked_type == "private" and locked_id:
                eff_cid = f"private_{locked_id}"
            elif locked_type == "group" and locked_id:
                eff_cid = f"group_{locked_id}"
        elif not eff_cid.startswith(("private_", "group_")):
            eff_cid = f"private_{eff_cid}" if locked_type == "private" else f"group_{eff_cid}"

        async def _send_img(img_path: str):
            from tools.image_tools import send_image_to_group_impl, send_image_to_private_impl
            if eff_cid.startswith("private_"):
                uid_num = int(eff_cid.replace("private_", "", 1))
                await send_image_to_private_impl(uid_num, img_path)
            elif eff_cid.startswith("group_"):
                gid_num = int(eff_cid.replace("group_", "", 1))
                await send_image_to_group_impl(gid_num, img_path)
            elif eff_cid.isdigit():
                await send_image_to_group_impl(int(eff_cid), img_path)

        return await play_board_game_impl(
            action=action,
            chat_id=eff_cid,
            user_id=eff_uid,
            user_name=eff_uname,
            game_type=game_type,
            difficulty=difficulty,
            move=move,
            send_image_cb=_send_img,
        )


if __name__ == "__main__":
    _install_mcp_print_guard()
    _trigger_quota_sync_subprocess()
    mcp.run()
