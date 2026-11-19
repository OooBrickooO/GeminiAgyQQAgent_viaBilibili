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
    r'(五子棋|黑白棋|奥赛罗|翻转棋|21点|二十一点|blackjack|1a2b|猜数字|破译密码|下棋|来一?[局盘把场]|玩一?[局盘把]?|认输|悔棋|桌游|小游戏)',
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
) -> tuple[str, str]:
    """
    【阶段二 2.2 三档路由判定矩阵（结合 2A + 2C + 严格硬护栏）】
    返回: (effort, rule_tag)
    effort ∈ {"low", "medium", "high"}（经由 build_agy_model_flags 映射至 gemini-4-hydrogen-{low|medium|high}）
    """
    raw = (message_text or "").strip()
    ephemeral = (ephemeral_context or "").strip()
    pure = _extract_pure_focus_text(raw)
    pure_no_cq = re.sub(r'\[CQ:[^\]]+\]', '', pure).strip()
    domains = set(target_domains or [])
    sender_str = str(sender_id or "").strip()
    is_master = (sender_str == str(MASTER_QQ))

    has_image = (
        "[图片#" in raw
        or "[CQ:image" in raw
        or "[图片#" in ephemeral
        or bool(context_has_image)
    )
    has_link = bool(RE_URL_LINK.search(pure_no_cq) or (not is_breathe and RE_BILI.search(pure_no_cq)))
    has_question = (not is_breathe) and ("?" in pure_no_cq or "？" in pure_no_cq)
    has_deep_memory = (not is_breathe) and bool(RE_DEEP_MEMORY_CUES.search(pure_no_cq))
    has_high_complex = (not is_breathe) and bool(RE_HIGH_COMPLEX_OR_SOUL_CUES.search(pure_no_cq))
    has_sensitive_tool = (not is_breathe) and bool(RE_SENSITIVE_TOOL_CUES.search(pure_no_cq))
    is_long_text = (not is_breathe) and (len(pure_no_cq) >= 45)
    is_poke_event = ("[戳一戳]" in pure) or ("戳了戳你的猫耳" in raw) or ("戳了戳你的猫耳" in ephemeral)

    # 获取好感度（若未显式传入且非主人/系统，则从本地画像快速读取）
    aff = user_affection
    if aff is None:
        if is_master:
            aff = 1000.0
        elif sender_str and sender_str not in ("系统", "0"):
            try:
                from social.affection import get_user_profile
                prof = get_user_profile(sender_str)
                aff = float(prof.get("affection", 0.0))
            except Exception:
                aff = 0.0
        else:
            aff = 0.0

    # ============================================================
    # 优先级 1 · 绝对禁用 low 的硬护栏（命中任一至少 medium 或 high）
    # ============================================================
    if is_retry:
        return "high", "P1_guard:safety_or_silent_retry"

    if was_interrupted:
        # 发生打断思考：本轮曾触发打断旧思考并重新合并 -> 严禁 low
        if has_image or is_long_text or has_high_complex or (domains & {"bili", "group_admin", "system_admin"}):
            return "high", "P1_guard:interrupted_complex->high"
        return "medium", "P1_guard:interrupted_forbidden_low->medium"

    non_chat_domains = domains & {"bili", "group_admin", "system_admin", "game"}
    if non_chat_domains or has_deep_memory:
        # 非纯聊天工具域 / 深度 memory 检索 -> 严禁 low
        if (domains & {"bili", "group_admin", "system_admin"}) or has_image or is_long_text or has_high_complex:
            return "high", f"P1_guard:domain_or_deep_mem({','.join(sorted(non_chat_domains)) or 'memory'})->high"
        return "medium", f"P1_guard:domain_or_deep_mem({','.join(sorted(non_chat_domains)) or 'memory'})->medium"

    if has_image or has_link or is_long_text or has_high_complex:
        # 高复杂度/视觉/走心信号：含图片锚点、链接、长文本(>=45字)、复杂问句/技术代码/情感安慰/虚实探讨
        if has_image or is_long_text or has_high_complex:
            return "high", "P1_guard:visual_or_long_or_soul_complex->high"
        return "medium", "P1_guard:link_or_moderate_complex->medium"

    # ============================================================
    # 优先级 2 · 定向 low 规则（仅在未触发优先级 1 硬护栏时生效）
    # ============================================================
    # 2.1 自由冒泡 (workers/breathe.py)
    if is_breathe:
        is_lure_master_or_bff = is_private and (is_master or aff >= 850.0)
        has_pending_or_bili_hook = any(
            kw in (ephemeral + "\n" + raw)
            for kw in ("挂念回访", "老友近况回访", "心里惦记的小事", "B站冲浪抛梗", "B站好物", "精准投喂", "pending_thought")
        )
        if is_lure_master_or_bff:
            return "medium", "P2_breathe_exempt:private_master_or_bff->medium"
        if has_pending_or_bili_hook:
            return "medium", "P2_breathe_exempt:pending_thought_or_bili_share->medium"
        return "low", "P2_breathe:group_casual_or_normal_outreach->low"

    # 2.2 群聊话题压力出场（非 must_reply，无图片且无复杂/敏感工具域 -> 一律 low；普通问号放宽，复杂问句已由 P1 拦截）
    if not is_private and not must_reply:
        if not has_sensitive_tool:
            return "low", "P2_group_pressure_burst:no_image_no_tool->low"
        return "medium", "P2_group_pressure_burst:with_sensitive_tool->medium"

    # 2.3 朋友以下（好感度 < 500，严格不包含“朋友”级别）的私聊首轮起手
    if is_private and not is_continuation_turn and not is_master and aff < 500.0:
        is_calling_name = bool(RE_BOT_NAME_CALL.search(pure_no_cq))
        needs_thought = has_question or has_sensitive_tool or len(pure_no_cq) >= 24
        if not is_calling_name and not needs_thought:
            return "low", f"P2_private_first_turn_under_friend(aff={aff:.0f}<500)->low"
        return "medium", f"P2_private_first_turn_under_friend_escalated(aff={aff:.0f})->medium"

    # ============================================================
    # 优先级 3 · 常规对话基准（含主人、挚友、朋友，及所有通道后续轮次）
    # ============================================================
    # 3.1 戳一戳反击 -> low
    if is_poke_event:
        return "low", "P3_regular:poke_counterattack->low"

    # 3.2 群聊高轮次自然收尾 (soft_cap_turns >= 7，即第 8 轮起的软上限热聊收尾，且无问号/无敏感工具词/短句)
    if not is_private and soft_cap_turns >= 7 and len(pure_no_cq) <= 30 and not has_question and not has_sensitive_tool:
        return "low", f"P3_regular:group_high_turns_wind_down(turn={soft_cap_turns})->low"

    # 3.3 纯文字极简互动（极短接茬 <= 22 字且无问号/无敏感工具词，或早晚安/哈哈/草等轻搭腔）
    # 注意：对于好感度 >= 500（朋友/挚友/主人）的私聊首轮（is_continuation_turn=False），仅当命中明确轻搭腔词表 (RE_LIGHT_BANTER) 时降为 low，其余正常首轮对话保持 medium 基准以与 <500 路人/熟人严格区分。
    if (
        len(pure_no_cq) <= 22
        and not has_question
        and not has_sensitive_tool
        and (
            is_continuation_turn
            or bool(RE_LIGHT_BANTER.match(pure_no_cq))
            or (not is_private and len(pure_no_cq) <= 14)
        )
    ):
        return "low", f"P3_regular:ultra_short_banter(len={len(pure_no_cq)})->low"

    # 3.4 默认常规基准 -> medium
    return "medium", "P3_regular:default_baseline->medium"


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
