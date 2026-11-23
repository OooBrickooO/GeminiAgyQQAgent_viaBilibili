"""
core/intent_router.py — 0ms 本地极速意图路由网关与三档思考深度路由 (Zero-Latency Intent Gateway V5.2)

职责：
1. 0ms 本地规则与特征路由 (Fast-Path):
   - 基于高性能正则与权限拓扑进行毫秒级领域裁切；
   - 彻底告别子进程冷启动开销与 2.0s 超时风险，0 额度消耗，0 内存浪费；
2. 领域安全拓扑：
   - chat / memory / meme: 核心对话、记忆心事与表情包资产（100% 保底常驻）；
   - bili: B站视频链接、BV号、UP主、动态与搜索特征命中时按需挂载；
   - group_admin: 禁言、踢人、白名单等群管指令特征命中时按需挂载；
   - system_admin: 空间说说、系统运维等指令仅在主人 (MASTER_QQ) 明确触发时挂载；
3. 三档可靠路由判定矩阵 (`route_chat_effort_with_reason` / `route_chat_effort`)：
   - 全面解禁前台 `low` 档 (`gemini-4-hydrogen-low`)，结合硬护栏、定向 `low` 规则与常规基准精细分级。
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
    r'(五子棋|黑白棋|奥赛罗|翻转棋|国际象棋|西洋棋|chess|中国象棋|象棋|xiangqi|21点|二十一点|blackjack|1a2b|猜数字|破译密码|下棋|来一?[局盘把场]|玩一?[局盘把]?|认输|悔棋|桌游|小游戏)',
    re.IGNORECASE
)

RE_DESKPET = re.compile(
    r'(桌宠|桌面|看屏|屏幕|截图|截屏|看一眼电脑|电脑上|窗口|鼠标|点击|双击|右键|滚轮|帮我点|帮我打字|按键|快捷键|主动吐槽|局内吐槽)',
    re.IGNORECASE
)

# 深度记忆检索指令线索
RE_DEEP_MEMORY_CUES = re.compile(
    r'(记得|之前|上次|昨天|前天|答应|约定|升级|完成|忘了|忘掉|回忆|印象|多久|什么时候)',
    re.IGNORECASE
)

# 复杂问句 / 技术代码求助 / 情感低谷安慰 / 虚实话外视角探讨
RE_HIGH_COMPLEX_OR_SOUL_CUES = re.compile(
    r'(为什么|怎么回事|怎么办|如何|帮我|代码|bug|报错|分析|原理|算法|架构|部署|'
    r'存在|真实|数字生命|人工智能|ai|虚拟|灵魂|意识|程序|设置|提示词|'
    r'难过|委屈|心事|伤心|哭了|崩溃|绝望|失眠|孤独|焦虑|抱抱我|安慰|生病|发烧|住院)',
    re.IGNORECASE
)

# 敏感工具触发词（点歌、抽签、发语音、好感调整、收藏表情包、特关等，禁降为 low）
RE_SENSITIVE_TOOL_CUES = re.compile(
    r'(点歌|放首歌|唱首歌|听歌|抽签|运势|求签|语音|说句话|叫一声|亲口|'
    r'好感|扣分|加分|收藏|偷图|收录|特关|特别关注|传话|发说说)',
    re.IGNORECASE
)

RE_URL_LINK = re.compile(r'(https?://\S+|b23\.tv/\S+|www\.\S+)', re.IGNORECASE)
RE_BOT_NAME_CALL = re.compile(r'(弥酱|星见弥|弥弥)')

# 极简日常轻搭腔词表
RE_LIGHT_BANTER = re.compile(
    r'^(早|早安|早上好|午安|晚安|睡了|哈哈+|嘿嘿+|草+|笑死|确实|好滴|好的|收到|ok|好耶|贴贴|摸摸|抱抱|喵+|呜+|哼+|略+|6+|牛的|太草了|在吗|干嘛呢|摸鱼)[~～!！。.,，？?喵]*$',
    re.IGNORECASE
)


def _extract_pure_focus_text(raw_text: str) -> str:
    """从 combined_message 或 messages_list 中提取最新待响应的纯用户发言文本"""
    if not raw_text:
        return ""
    focus_marker = "【L0 核心响应焦点"
    text = raw_text.split(focus_marker)[-1] if focus_marker in raw_text else raw_text
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("【L0 核心响应焦点"):
            continue
        if s.startswith("[系统提示:") and "戳了戳" in s:
            lines.append("[戳一戳]")
            continue
        # 剥离 "昵称(QQ号) 私聊你说：/ @了你并说道：/ 说："
        s = re.sub(r'^[^\n]{1,50}?\(\d{5,12}\)\s*(?:在【桌游对弈中】说道|私聊你说|@了你并说道|说)：\s*', '', s)
        s = re.sub(r'\[CQ:at,qq=\d+\]', '', s).strip()
        if s:
            lines.append(s)
    return "\n".join(lines).strip()


def route_chat_effort_with_reason(
    message_text: str,
    is_continuation_turn: bool = False,
    is_breathe: bool = False,
    target_domains: list[str] = None,
    is_private: bool = False,
    sender_id: str = "",
    was_interrupted: bool = False,
    is_retry: bool = False,
    must_reply: bool = False,
    ephemeral_context: str = "",
    user_affection: float = None,
    soft_cap_turns: int = 0,
    context_has_image: bool = False,
    chat_id_str: str = "",
    force_low_override: bool = None,
) -> tuple[str, str]:
    """
    【模块一 · 局部通道级 low 强制开关 + 默认 medium/high 两档路由 (1C + 1-α + L1)】
    返回: (effort, rule_tag)
    - 默认状态下，前台所有对话 100% 禁止落入 low，统一以 medium 为常规基准、复杂/视觉/工具/走心升至 high；
    - 仅当主人显式为某个局部对话通道 (chat_id_str) 开启 low 开关时，该通道强制走 low（安全重试除外），
      且完全不影响其他任何群聊或私聊通道。
    """
    raw = (message_text or "").strip()
    ephemeral = (ephemeral_context or "").strip()
    pure = _extract_pure_focus_text(raw)
    pure_no_cq = re.sub(r'\[CQ:[^\]]+\]', '', pure).strip()
    domains = set(target_domains or [])
    sender_str = str(sender_id or "").strip()

    # ============================================================
    # 优先级 0 · 安全拦截自愈重试走 high（确保自愈脱困）
    # ============================================================
    if is_retry:
        return "high", "P0_guard:safety_or_silent_retry->high"

    # ============================================================
    # 优先级 1 · 全局默认极速 low 路由 (gemini-4-hydrogen-low)
    # 主人要求默认推理模型改为 hydrogen low，避免思考过长导致消息与桌宠吐槽延迟
    # ============================================================
    if force_low_override is False:
        return "medium", "L1_override_medium->medium"

    return "low", "P1_default:hydrogen_low_fast_response->low"


def route_chat_effort(
    message_text: str,
    is_continuation_turn: bool = False,
    is_breathe: bool = False,
    target_domains: list[str] = None,
    is_private: bool = False,
    sender_id: str = "",
    was_interrupted: bool = False,
    is_retry: bool = False,
    must_reply: bool = False,
    ephemeral_context: str = "",
    user_affection: float = None,
    soft_cap_turns: int = 0,
    context_has_image: bool = False,
    chat_id_str: str = "",
    force_low_override: bool = None,
) -> str:
    """兼容原有签名，直接返回 effort 字符串 ('low' | 'medium' | 'high')"""
    eff, _ = route_chat_effort_with_reason(
        message_text=message_text,
        is_continuation_turn=is_continuation_turn,
        is_breathe=is_breathe,
        target_domains=target_domains,
        is_private=is_private,
        sender_id=sender_id,
        was_interrupted=was_interrupted,
        is_retry=is_retry,
        must_reply=must_reply,
        ephemeral_context=ephemeral_context,
        user_affection=user_affection,
        soft_cap_turns=soft_cap_turns,
        context_has_image=context_has_image,
        chat_id_str=chat_id_str,
        force_low_override=force_low_override,
    )
    return eff


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
        return ["chat", "game", "memory", "meme", "bili", "group_admin", "system_admin", "deskpet"]

    sender_str = str(sender_id).strip()
    is_master = (sender_str == str(MASTER_QQ)) or (str(chat_id_str) == "desktop_master")
    clean_text = (message_text or "").strip()

    # 1. 基础保底领域：核心对话、记忆心事与表情包资产（零参数负担，极速响应）
    matched_domains = set(["chat", "memory", "meme"])
    if str(chat_id_str) == "desktop_master":
        matched_domains.add("deskpet")

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
        if str(chat_id_str) == "desktop_master" or RE_DESKPET.search(clean_text):
            master_domains.add("deskpet")
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
