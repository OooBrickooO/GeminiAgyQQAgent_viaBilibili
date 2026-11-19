"""
core/intent_router.py — 0ms 本地极速意图路由网关 (Zero-Latency Intent Gateway)

职责：
1. 0ms 本地规则与特征路由 (Fast-Path):
   - 基于高性能正则与权限拓扑进行毫秒级领域裁切；
   - 彻底告别子进程冷启动开销与 2.0s 超时风险，0 额度消耗，0 内存浪费；
2. 领域安全拓扑：
   - chat / memory / meme: 核心对话、记忆心事与表情包资产（100% 保底常驻）；
   - bili: B站视频链接、BV号、UP主、动态与搜索特征命中时按需挂载；
   - group_admin: 禁言、踢人、白名单等群管指令特征命中时按需挂载；
   - system_admin: 空间说说、系统运维等指令仅在主人 (MASTER_QQ) 明确触发时挂载；
3. 输出挂载领域标签: ["chat", "memory", "meme", "bili", "group_admin", "system_admin"]
"""
import re
from config import MASTER_QQ

# 快速特征识别正则 (0ms 本地嗅探)
RE_BILI = re.compile(
    r'(b站|哔哩|bilibili|bv\w{8,}|av\d+|b23\.tv|up主|阿婆主|弹幕|投币|一键三连|视频|小破站|番剧|追番|动态|私信|评论区|关注up|查up|查视频)',
    re.IGNORECASE
)
RE_GROUP_ADMIN = re.compile(
    r'(解禁|禁言|踢出|群管理|群列表|群成员|拉黑|踢人|解除禁言|热搜|热梗|传话|去群|发到群|给.{1,15}发消息|跟.{1,15}私聊)',
    re.IGNORECASE
)
RE_SYSTEM_ADMIN = re.compile(
    r'(重启|系统状态|发说说|空间动态|说说日记|打小报告|健康检查|空间|说说|运行状态|服务器状态|告状|特关|特别关注)',
    re.IGNORECASE
)
RE_QZONE_CARE = re.compile(
    r'(特关|特别关注|空间说说|空间日记)',
    re.IGNORECASE
)

RE_GAME = re.compile(
    r'(五子棋|黑白棋|奥赛罗|翻转棋|21点|二十一点|blackjack|1a2b|猜数字|破译密码|下棋|来一?[局盘把场]|玩一?[局盘把]?|认输|悔棋|桌游|小游戏)',
    re.IGNORECASE
)
RE_DEEP_OR_TOOL_CUES = re.compile(
    r'(记得|之前|上次|昨天|前天|答应|约定|升级|完成|忘了|忘掉|为什么|怎么|帮我|代码|bug|报错|分析|觉得|存在|真实|难过|委屈|心事|点歌|放首歌|抽签|运势|语音|说句话|叫一声|好感|收藏|表情包|特关|特别关注)',
    re.IGNORECASE
)


def route_chat_effort(
    message_text: str,
    is_continuation_turn: bool = False,
    is_breathe: bool = False,
    target_domains: list[str] = None,
    is_private: bool = False,
    sender_id: str = ""
) -> str:
    """
    1A 动态思考深度分级路由（前台严格只用 medium / high，绝不用 low）：
    - high: 会话首轮、主动冒泡、带图/B站/管理域、长句(>=40字)、提问、或触发记忆/情感/工具线索；
    - medium: 活跃 Session 续轮中的短句纯文字闲聊，兼顾速度、额度与真人轻快感。
    """
    if not is_continuation_turn or is_breathe:
        return "high"

    raw = (message_text or "").strip()
    if "[图片#" in raw or "[CQ:image" in raw:
        return "high"

    domains = set(target_domains or [])
    if domains & {"bili", "group_admin", "system_admin"}:
        return "high"

    # 剥离外层发送者前缀再测纯文本长度与特征
    pure = re.sub(r'^[^\n]{1,40}?\(\d{5,12}\)\s*(?:私聊你说|@了你并说道|说)：', '', raw, flags=re.MULTILINE).strip()
    if len(pure) >= 40 or "?" in pure or "？" in pure:
        return "high"

    if RE_DEEP_OR_TOOL_CUES.search(pure):
        return "high"

    # 主人私聊中稍长的对话默认保持 high
    if is_private and str(sender_id).strip() == str(MASTER_QQ) and len(pure) >= 24:
        return "high"

    return "medium"


async def route_message_domains(
    message_text: str,
    sender_id: str,
    is_private: bool = False,
    is_breathe: bool = False,
    force_all: bool = False,
    chat_id_str: str = ""
) -> list[str]:
    """
    0ms 本地极速意图路由主入口。
    返回本次推理应激活的工具领域列表，如: ["chat", "memory", "meme", "bili"]
    """
    if force_all:
        return ["chat", "game", "memory", "meme", "bili", "group_admin", "system_admin"]

    sender_str = str(sender_id).strip()
    is_master = (sender_str == str(MASTER_QQ))
    clean_text = (message_text or "").strip()

    # 1. 基础保底领域：核心对话、记忆心事与表情包资产（零参数负担，极速响应）
    matched_domains = set(["chat", "memory", "meme"])

    # 2. 呼吸场景：默认仅对话+记忆+表情；若涉及B站则动态附加
    if is_breathe:
        if RE_BILI.search(clean_text):
            matched_domains.add("bili")
        return sorted(list(matched_domains))

    game_active = False
    if chat_id_str:
        try:
            from toys.board_games import has_active_game
            game_active = has_active_game(str(chat_id_str))
        except Exception:
            game_active = False

    # 3. 主人专属通道（精准按需分流，杜绝 Schema 膨胀导致思考过载）：
    # 日常闲聊只给基础对话工具(chat, memory, meme)；命中具体指令才动态激活对应领域
    if is_master:
        master_domains = set(["chat", "memory", "meme"])
        if RE_BILI.search(clean_text):
            master_domains.add("bili")
        if RE_GROUP_ADMIN.search(clean_text):
            master_domains.add("group_admin")
        if RE_SYSTEM_ADMIN.search(clean_text):
            master_domains.add("system_admin")
        if game_active or RE_GAME.search(clean_text):
            master_domains.add("game")
        return sorted(list(master_domains))

    # 4. 普通群友与私聊：0ms 本地特征嗅探
    if RE_BILI.search(clean_text):
        matched_domains.add("bili")

    if game_active or RE_GAME.search(clean_text):
        matched_domains.add("game")

    if not is_private and RE_GROUP_ADMIN.search(clean_text):
        matched_domains.add("group_admin")

    if RE_QZONE_CARE.search(clean_text):
        matched_domains.add("system_admin")

    # 敏感写入(publish)与移出特关等在 tools/qzone_tools.py 底层仍受 AGY_CALLER_ID == MASTER_QQ 硬权限锁保护
    return sorted(list(matched_domains))


