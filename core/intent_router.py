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
    r'(b站|哔哩|bilibili|bv\w{8,}|av\d+|b23\.tv|up主|阿婆主|弹幕|投币|一键三连|三连|收藏夹|收藏视频|默认收藏|取消收藏|转发动态|发动态|视频|小破站|番剧|追番|动态|私信|评论区|关注up|查up|查视频)',
    re.IGNORECASE
)
RE_TRENDS = re.compile(
    r'(热搜|热梗|热榜|流行词|什么梗|啥梗|微博热搜|知乎热榜|时事|新闻|今天有什么大事|吃瓜)',
    re.IGNORECASE
)
RE_GROUP_ADMIN = re.compile(
    r'(解禁|禁言|踢出|群管理|群列表|群成员|拉黑|踢人|解除禁言|好友列表|联系人|传话|去群|发到群|给.{1,15}发消息|跟.{1,15}私聊)',
    re.IGNORECASE
)
RE_QQ_BRIDGE = re.compile(
    r'(去群|发到群|发给|群里说|群消息|私聊|转告|传话|发qq|发消息给|好友列表|群列表|点歌|放首歌|抽签|御神签|好感|表情包|发语音到)',
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
    # 优先级 0 · 安全拦截自愈重试永走 high（任何通道绝不降档）
    # ============================================================
    if is_retry:
        return "high", "P0_guard:safety_or_silent_retry->high"

    resolved_cid = str(chat_id_str or "").strip()
    if not resolved_cid and is_private and sender_str and sender_str not in ("系统", "0"):
        resolved_cid = f"private_{sender_str}"

    # ============================================================
    # 优先级 0.5 · 桌宠专属独立轨道 (desktop_master 固定走 low，与 QQ 对话彻底隔离)
    # ============================================================
    if resolved_cid == "desktop_master":
        if force_low_override is False:
            return "medium", "P0_deskpet:override_medium->medium"
        return "low", "P0_deskpet:desktop_master->low"

    # ============================================================
    # 优先级 1 · 局部通道级强制 low 开关 (L1：仅对主人手动开启的指定 QQ 对话生效)
    # ============================================================
    if force_low_override is True:
        return "low", f"L1_channel_low_forced({resolved_cid or 'override'})->low"
    if force_low_override is None and resolved_cid:
        try:
            from core.state_manager import is_channel_low_enabled
            if is_channel_low_enabled(resolved_cid):
                return "low", f"L1_channel_low_forced({resolved_cid})->low"
        except Exception:
            pass

    # ============================================================
    # 优先级 2 · QQ 默认高质两档路由 (medium 基准 / high 复杂升档，全面禁用自动 low)
    # ============================================================
    has_image = (
        "[图片#" in raw
        or "[CQ:image" in raw
        or "[图片#" in ephemeral
        or "[桌面伴随看屏" in raw
        or "截图本地绝对路径" in raw
        or bool(context_has_image)
    )
    has_link = bool(RE_URL_LINK.search(pure_no_cq) or (not is_breathe and RE_BILI.search(pure_no_cq)))
    has_deep_memory = (not is_breathe) and bool(RE_DEEP_MEMORY_CUES.search(pure_no_cq))
    has_high_complex = (not is_breathe) and bool(RE_HIGH_COMPLEX_OR_SOUL_CUES.search(pure_no_cq))
    is_long_text = (not is_breathe) and (len(pure_no_cq) >= 45)

    # 注意：区分「已挂载的工具池 (domains)」与「本轮实际命中的重工具意图 (active_heavy_domains)」
    # 主人私聊常驻了 bili/group_admin/system_admin 工具，其他通道也可能因会话粘性保留着这些域，
    # 因此只在当前轮次本身命中重工具线索时才触发 high 升档，避免简单闲聊（如“在干嘛”“好滴”）浪费 high 额度。
    focus_combined = f"{pure_no_cq}\n{ephemeral}"
    explicit_heavy = set()
    if not is_breathe and RE_BILI.search(focus_combined):
        explicit_heavy.add("bili")
    if not is_breathe and RE_GROUP_ADMIN.search(focus_combined):
        explicit_heavy.add("group_admin")
    if not is_breathe and RE_SYSTEM_ADMIN.search(focus_combined):
        explicit_heavy.add("system_admin")
    if not is_breathe and RE_DESKPET.search(focus_combined):
        explicit_heavy.add("deskpet")

    active_heavy_domains = (explicit_heavy & domains) if domains else explicit_heavy
    non_chat_domains = active_heavy_domains | (domains & {"game"})

    if was_interrupted:
        if has_image or is_long_text or has_high_complex or active_heavy_domains:
            return "high", "P1_guard:interrupted_complex->high"
        return "medium", "P1_guard:interrupted->medium"

    if non_chat_domains or has_deep_memory:
        if active_heavy_domains or has_image or is_long_text or has_high_complex:
            return "high", f"P1_guard:domain_or_deep_mem({','.join(sorted(non_chat_domains)) or 'memory'})->high"
        return "medium", f"P1_guard:domain_or_deep_mem({','.join(sorted(non_chat_domains)) or 'memory'})->medium"

    if has_image or has_link or is_long_text or has_high_complex:
        if has_image or is_long_text or has_high_complex:
            return "high", "P1_guard:visual_or_long_or_soul_complex->high"
        return "medium", "P1_guard:link_or_moderate_complex->medium"

    if is_breathe:
        return "medium", "P2_breathe:default_medium"

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
    chat_id_str: str = "",
    recent_context_text: str = "",
) -> list[str]:
    """
    0ms 本地极速意图路由主入口（方案 T1：场景物理分桶 + 近2轮滑窗嗅探 + 会话级域粘性）。
    返回本次推理应激活的工具领域列表，如: ["bili", "chat", "meme", "memory", "trends"]
    """
    if force_all:
        return ["bili", "chat", "deskpet", "game", "group_admin", "meme", "memory", "system_admin", "trends"]

    sender_str = str(sender_id).strip()
    cid_str = str(chat_id_str or "").strip()
    is_deskpet_channel = (cid_str == "desktop_master")
    is_master = (sender_str == str(MASTER_QQ)) or is_deskpet_channel
    clean_text = (message_text or "").strip()
    recent_txt = (recent_context_text or "").strip()
    probe_text = f"{recent_txt}\n{clean_text}".strip() if recent_txt else clean_text

    # 读取当前活跃会话已激活的粘性域（7min/10min 会话期内防追问掉工具）
    sticky_domains = set()
    if cid_str and not is_breathe:
        try:
            from core.agy_runner import get_session_sticky_domains
            sticky_domains = get_session_sticky_domains(cid_str)
        except Exception:
            sticky_domains = set()

    game_active = False
    if cid_str:
        try:
            from toys.board_games import has_active_game
            game_active = has_active_game(cid_str)
        except Exception:
            game_active = False

    def _persist_sticky(newly_hit: set[str]):
        if cid_str and newly_hit and not is_breathe:
            try:
                from core.agy_runner import update_session_sticky_domains
                update_session_sticky_domains(cid_str, newly_hit)
            except Exception:
                pass

    # ============================================================
    # 桶 1 · 桌宠专属通道 (desktop_master)：纯桌宠物理隔离，不默认混入 QQ 发信工具
    # ============================================================
    if is_deskpet_channel:
        domains = {"deskpet", "memory"}
        newly_hit = set()
        if RE_BILI.search(probe_text):
            newly_hit.add("bili")
        if RE_TRENDS.search(probe_text):
            newly_hit.add("trends")
        if RE_SYSTEM_ADMIN.search(probe_text):
            newly_hit.add("system_admin")
        if RE_QQ_BRIDGE.search(probe_text) or RE_GROUP_ADMIN.search(probe_text):
            newly_hit.update({"chat", "meme"})
            if RE_GROUP_ADMIN.search(probe_text):
                newly_hit.add("group_admin")
        if game_active or RE_GAME.search(probe_text):
            newly_hit.add("game")

        allowed_sticky = sticky_domains & {"bili", "trends", "system_admin", "chat", "meme", "group_admin", "game"}
        domains |= allowed_sticky | newly_hit
        _persist_sticky(newly_hit)
        return sorted(list(domains))

    # ============================================================
    # 桶 2 · 主动社交冒泡 (is_breathe)：轻装核心 + trends（涉及 B站则挂 bili）
    # ============================================================
    if is_breathe:
        domains = {"chat", "memory", "meme", "trends"}
        if RE_BILI.search(probe_text):
            domains.add("bili")
        return sorted(list(domains))

    # ============================================================
    # 桶 3 · 主人私聊专属桶 (private_MASTER)：常驻全套 QQ + B站工具，永不误杀；仅 game/deskpet 按需粘性挂载
    # ============================================================
    if is_master and is_private:
        domains = {"chat", "memory", "meme", "bili", "trends", "group_admin", "system_admin"}
        newly_hit = set()
        if game_active or RE_GAME.search(probe_text):
            newly_hit.add("game")
        if RE_DESKPET.search(probe_text):
            newly_hit.add("deskpet")

        allowed_sticky = sticky_domains & {"game", "deskpet"}
        domains |= allowed_sticky | newly_hit
        _persist_sticky(newly_hit)
        return sorted(list(domains))

    # ============================================================
    # 桶 4 · 主人在群聊中：轻装核心 + trends + 命中即会话粘性保留
    # ============================================================
    if is_master:
        domains = {"chat", "memory", "meme", "trends"}
        newly_hit = set()
        if RE_BILI.search(probe_text):
            newly_hit.add("bili")
        if RE_GROUP_ADMIN.search(probe_text):
            newly_hit.add("group_admin")
        if RE_SYSTEM_ADMIN.search(probe_text):
            newly_hit.add("system_admin")
        if game_active or RE_GAME.search(probe_text):
            newly_hit.add("game")
        if RE_DESKPET.search(probe_text):
            newly_hit.add("deskpet")

        allowed_sticky = sticky_domains & {"bili", "group_admin", "system_admin", "game", "deskpet"}
        domains |= allowed_sticky | newly_hit
        _persist_sticky(newly_hit)
        return sorted(list(domains))

    # ============================================================
    # 桶 5 · 普通群友与好友私聊：trends 独立可用 + 扩展域命中即会话粘性保留
    # ============================================================
    domains = {"chat", "memory", "meme"}
    newly_hit = set()
    if RE_TRENDS.search(probe_text):
        newly_hit.add("trends")
    if RE_BILI.search(probe_text):
        newly_hit.add("bili")
    if game_active or RE_GAME.search(probe_text):
        newly_hit.add("game")
    if not is_private and RE_GROUP_ADMIN.search(probe_text):
        newly_hit.add("group_admin")
    if RE_QZONE_CARE.search(probe_text):
        newly_hit.add("system_admin")

    allowed_non_master_sticky = {"trends", "bili", "game", "system_admin"}
    if not is_private:
        allowed_non_master_sticky.add("group_admin")
    domains |= (sticky_domains & allowed_non_master_sticky) | newly_hit
    _persist_sticky(newly_hit)

    # 敏感写入(publish)与移出特关等在 tools/qzone_tools.py 底层仍受 AGY_CALLER_ID == MASTER_QQ 硬权限锁保护
    return sorted(list(domains))
