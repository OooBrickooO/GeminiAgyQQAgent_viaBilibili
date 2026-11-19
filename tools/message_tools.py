"""
tools/message_tools.py — 文本消息发送与现代网络语境自适应连发引擎 V4.4

核心机制：
1. 默认开启自适应分段模式 (`auto_split: bool = True`)：
   - 彻底解放 LLM 参数负担，日常群聊/私聊自动拆分为 2~4 条真人连发气泡；
   - 智能豁免：遇到代码块 (```)、结构化列表、富媒体 CQ 码、极短单句时自动退化为单条直发。
2. 头部 CQ 码隔离与原子保护：
   - 自动剥离 `[CQ:reply,...]` 与 `[CQ:at,...]` 仅挂载于第 1 条气泡，彻底修复“带引用回复就不拆句”的旧 Bug；
   - 保护颜文字、括号动作与行内 `[CQ:face,...]` 不被从中间劈裂。
3. 现代网络语境两级切分 (`split_message_bubbles`)：
   - 一级按换行 (`\n`) 与句末强标点 (`！？!?~` + 紧随颜文字) 切分；
   - 二级对超长逗号句居中折断，对过短碎片/纯括号动作自动就近吸附，并通过最短邻接对合并收敛至最多 4 条气泡。
4. 拟真打字长度加权时延 (`calc_typing_delay`)：
   - 根据下一条气泡的字数动态计算 `0.5s ~ 2.5s` 的打字间隔，短句秒出、长句停顿。
"""
import os
import re
import random
import asyncio
import httpx
from config import log_mcp_tool, safe_log, BOT_QQ, MASTER_QQ, INTERNAL_BUBBLE_URL, NAPCAT_TOKEN
from core.state_manager import record_bot_spoke_persistent
from core.napcat_client import (
    call_onebot_api_raw,
    send_group_msg_raw,
    send_private_msg_raw,
    send_poke
)
from attention.user_mute import is_user_muted
from social.long_term_memory import record_chat_archive


def clean_message_no_period(message: str) -> str:
    """去除句尾与行内的冷漠中文句号 '。'，保留其余所有情绪标点（！、？、～、，、…等）"""
    cleaned = (message or "").replace("。", "").strip()
    if cleaned.endswith(".") and not cleaned.endswith(".."):
        cleaned = cleaned.rstrip(".")
    return cleaned


def _visible_length(text: str) -> int:
    """计算剥离 CQ 码后的可见字符长度"""
    return len(re.sub(r'\[CQ:[^\]]+\]', '', text or "").strip())


def calc_typing_delay(next_bubble: str) -> float:
    """
    根据下一条待发气泡的可见字数加权计算打字等待间隔 (严格锁定在 0.5s ~ 2.5s 之间)：
    - 短句/惊叹 (5~10字): ~0.6s - 0.9s
    - 中等句子 (20~30字): ~1.3s - 1.8s
    - 较长分句 (40字+): ~2.2s - 2.5s
    """
    v_len = _visible_length(next_bubble)
    raw_delay = 0.45 + v_len * 0.045 + random.uniform(-0.12, 0.12)
    return round(max(0.5, min(2.5, raw_delay)), 2)


calc_waifu_typing_delay = calc_typing_delay  # 保持向后兼容别名


def _should_bypass_split(body_text: str, auto_split: bool) -> bool:
    """判断是否应豁免自动分段拆句而采用整段单条直发"""
    if not auto_split:
        return True

    raw = (body_text or "").strip()
    if not raw:
        return True

    # 1. 含代码块、Markdown 标题或富媒体卡片时不拆句
    if "```" in raw or re.search(r'(?:^|\n)#{1,4}\s+\S+', raw):
        return True
    if any(tag in raw for tag in ("[CQ:image,", "[CQ:record,", "[CQ:video,", "[CQ:music,", "[CQ:json,", "[CQ:xml,")):
        return True

    # 2. 含多行结构化列表/编号排版（专业回答/盘点清单）时不拆句
    if re.search(r'(?:^|\n)\s*(?:\d+\.|[-*•])\s+.+\n\s*(?:\d+\.|[-*•])\s+', raw):
        return True

    # 3. 极短单行文本无需拆句
    if "\n" not in raw and _visible_length(raw) <= 16:
        return True

    return False


_should_bypass_waifu_split = _should_bypass_split  # 保持向后兼容别名


def _extract_leading_cq_prefix(message: str) -> tuple[str, str]:
    """剥离消息头部的 [CQ:reply,...] 与 [CQ:at,...] 前缀，仅挂载于首条气泡"""
    m = re.match(r'^((?:\[CQ:(?:reply|at),[^\]]+\]\s*)+)(.*)$', message or "", flags=re.DOTALL)
    if m:
        return m.group(1), m.group(2)
    return "", (message or "")


def _split_long_comma_clause(clause: str, threshold: int = 46) -> list[str]:
    """当单个无强标点的分句过长 (>threshold) 时，在靠近中点的逗号/分号/省略号处温柔折断一次"""
    if _visible_length(clause) <= threshold:
        return [clause]

    mid = len(clause) // 2
    candidates = [m.end() for m in re.finditer(r'[，、；]|……|——', clause)]
    valid = [pos for pos in candidates if 8 <= pos <= len(clause) - 8]
    if not valid:
        return [clause]

    best_pos = min(valid, key=lambda p: abs(p - mid))
    left = clause[:best_pos].rstrip("，、； ")
    right = clause[best_pos:].lstrip()
    out = [p for p in (left, right) if p]
    return out if out else [clause]


def split_message_bubbles(message: str, max_bubbles: int = 4) -> list[str]:
    """
    现代互联网语境自适应拆句引擎：
    1. 自动剥离头部 [CQ:reply] / [CQ:at] 并在最终仅挂回第 1 条气泡；
    2. 保护行内 [CQ:...] 与括号/颜文字不被切碎；
    3. 优先尊重现代网友换行 (\n) 分段习惯，并结合句末强标点 (！？!?~) 切分；
    4. 自动将纯括号动作或极短碎片吸附至相邻句，并通过最短邻接合并收敛至 max_bubbles (默认 4 条)。
    """
    leading_prefix, body = _extract_leading_cq_prefix(message)
    body = body.strip()
    if not body:
        return [leading_prefix.strip()] if leading_prefix.strip() else []

    # 若包含重型富媒体标签，直接整条返回
    if any(tag in body for tag in ("[CQ:image,", "[CQ:record,", "[CQ:video,", "[CQ:music,")):
        clean_single = clean_message_no_period(body)
        return [f"{leading_prefix}{clean_single}"] if clean_single else []

    # 1. 原子掩码：保护行内 CQ 码、括号内颜文字/短旁白不被内部标点切开
    atoms = []

    def _mask_atom(match):
        idx = len(atoms)
        atoms.append(match.group(0))
        return f"\x00ATOM{idx}\x00"

    masked = re.sub(r'\[CQ:[^\]]+\]', _mask_atom, body)
    masked = re.sub(r'\([^)\n]{1,24}\)|（[^）\n]{1,24}）', _mask_atom, masked)

    def _unmask(s: str) -> str:
        def _repl(m):
            i = int(m.group(1))
            return atoms[i] if 0 <= i < len(atoms) else ""
        return re.sub(r'\x00ATOM(\d+)\x00', _repl, s)

    # 2. 一级切分：按换行符 (\n+) 切出自然行
    raw_lines = [ln.strip() for ln in re.split(r'\n+', masked) if ln.strip()]

    # 3. 二级切分：对较长行按句末强标点 (！？!?~。及紧跟的颜文字/引号) 拆开
    # 注意：句末标点后若紧跟颜文字 (如 "！(╯°Д°)╯︵ ┻━┻" 或 "！（>д<）ノ")，将其整体保留在该句末尾
    sent_pattern = re.compile(
        r'([。！？!?~]+(?:[”’」』]*)(?:[ \t]*\x00ATOM\d+\x00[^\n。！？!?~，]{0,12})?)'
    )
    raw_segments = []
    for ln in raw_lines:
        unmasked_ln = _unmask(ln)
        if _visible_length(unmasked_ln) <= 22:
            raw_segments.append(unmasked_ln)
            continue

        tokens = sent_pattern.split(ln)
        buf = ""
        for tok in tokens:
            if not tok:
                continue
            if sent_pattern.fullmatch(tok):
                buf += tok
                seg_clean = _unmask(buf).strip()
                if seg_clean:
                    raw_segments.extend(_split_long_comma_clause(seg_clean))
                buf = ""
            else:
                buf += tok
        if buf.strip():
            seg_clean = _unmask(buf).strip()
            if seg_clean:
                raw_segments.extend(_split_long_comma_clause(seg_clean))

    # 4. 清理句号并吸附纯括号舞台动作 / 极短碎片
    cleaned_segments = []
    for seg in raw_segments:
        c = clean_message_no_period(seg)
        if c:
            cleaned_segments.append(c)

    if not cleaned_segments:
        fallback = clean_message_no_period(body)
        return [f"{leading_prefix}{fallback}"] if fallback else []

    merged = []
    idx = 0
    while idx < len(cleaned_segments):
        cur = cleaned_segments[idx]
        # 若当前段是纯括号动作（如 "（气呼呼地反手戳你脑门）"），且后面还有台词，合并到下一句开头
        if re.fullmatch(r'[\(（].*[\)）]', cur) and (idx + 1) < len(cleaned_segments):
            cleaned_segments[idx + 1] = f"{cur} {cleaned_segments[idx + 1]}"
            idx += 1
            continue
        # 若当前碎片过短 (< 5字) 且可与前一句自然拼接
        if _visible_length(cur) < 5 and merged and (_visible_length(merged[-1]) + _visible_length(cur) <= 38):
            sep = "" if merged[-1][-1:] in "！!？?~…—)）" else " "
            merged[-1] = f"{merged[-1]}{sep}{cur}"
        else:
            merged.append(cur)
        idx += 1

    # 5. 若气泡数超出上限 max_bubbles，迭代合并最短的相邻气泡对，保持各条气泡长度均衡
    while len(merged) > max_bubbles:
        best_i = min(
            range(len(merged) - 1),
            key=lambda i: _visible_length(merged[i]) + _visible_length(merged[i + 1])
        )
        left, right = merged[best_i], merged[best_i + 1]
        sep = " " if left[-1:] not in "！!？?~，、；)）" else ""
        merged[best_i:best_i + 2] = [f"{left}{sep}{right}"]

    if leading_prefix and merged:
        merged[0] = f"{leading_prefix}{merged[0]}"

    return merged


split_waifu_bubbles = split_message_bubbles  # 保持向后兼容别名


async def _dispatch_bubbles(kind: str, target_id: int, bubbles: list[str]) -> str:
    """
    发送多气泡序列 (1C-1)：
    优先投递给常驻 bot_daemon 本机异步调度器（第1条发出后立刻返回，第2~N条后台按时延发出）；
    若本机调度器未启动（如离线调试），则自动回退内联逐条发送。
    """
    delays = [0.0] + [calc_typing_delay(b) for b in bubbles[1:]]
    try:
        async with httpx.AsyncClient(timeout=8.5, trust_env=False) as client:
            resp = await client.post(
                INTERNAL_BUBBLE_URL,
                headers={"Authorization": f"Bearer {NAPCAT_TOKEN}"},
                json={
                    "kind": kind,
                    "target_id": int(target_id),
                    "bubbles": bubbles,
                    "delays": delays,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return str(data.get("message") or f"已分段连发 {len(bubbles)} 条气泡消息")
    except Exception:
        pass

    raw_sender = send_group_msg_raw if kind == "group" else send_private_msg_raw
    for idx, bubble in enumerate(bubbles):
        if idx > 0:
            await asyncio.sleep(delays[idx])
        await raw_sender(int(target_id), bubble)
    return f"已分段连发 {len(bubbles)} 条气泡消息"


def is_cross_channel_authorized(cross_channel: bool = False) -> bool:
    """
    跨通道发信授权校验：
    仅当显式传入 cross_channel=True，且当前调用者为主人(MASTER_QQ)或系统任务（或未锁定通道的后台任务）时，
    豁免物理通道防串台锁，允许向指定目标群/私聊传话。
    """
    if not cross_channel:
        return False
    caller = os.environ.get("AGY_CALLER_ID", "").strip()
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    if caller in (str(MASTER_QQ), "system", "系统"):
        return True
    if not caller and locked_type in ("", "none"):
        return True
    return False


async def send_group_msg_impl(
    group_id: int,
    message: str,
    auto_split: bool = True,
    reply_to: int = 0,
    at_user: str = "",
    cross_channel: bool = False,
    **kwargs
) -> str:
    """发送消息到指定的 QQ 群（默认开启自适应分段拆句与 0.5s~2.5s 长度加权延迟；内置 1A 防串台硬锁，主人传话可凭 cross_channel=True 解锁）"""
    if "waifu_mode" in kwargs:
        auto_split = bool(kwargs["waifu_mode"])

    # 1A 防串台物理通道硬锁（若为主人授权的跨通道传话，豁免重定向拦截）
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    if is_cross_channel_authorized(cross_channel):
        safe_log(f"🔓 [跨通道授权] 主人指令跨通道发送群消息 -> 目标群[{group_id}]")
    else:
        if locked_type == "private" and locked_id:
            safe_log(f"🛡️ [防串台硬锁] 当前为私聊会话(private_{locked_id})，已自动拦截向群[{group_id}]的误发并重定向回私聊[{locked_id}]！")
            _, clean_body = _extract_leading_cq_prefix(message)
            return await send_private_msg_impl(int(locked_id), clean_body or message, auto_split=auto_split)
        elif locked_type == "group" and locked_id and str(group_id) != locked_id:
            safe_log(f"🛡️ [防串台硬锁] 当前为群聊会话(group_{locked_id})，已自动修正误传群号({group_id} -> {locked_id})！")
            group_id = int(locked_id)

    log_mcp_tool("send_group_msg", f"群 {group_id} (auto_split={auto_split}, reply_to={reply_to}, at={at_user}) -> {message[:60]}")
    try:
        prefix = ""
        clean_reply_id = 0
        if reply_to:
            try:
                raw_str = str(reply_to).strip()
                id_match = re.search(r'(-?\d+)', raw_str)
                if id_match:
                    clean_reply_id = int(id_match.group(1))
            except Exception:
                clean_reply_id = 0

        if clean_reply_id != 0 and "[CQ:reply" not in message:
            prefix += f"[CQ:reply,id={clean_reply_id}]"

        if at_user and "[CQ:at" not in message:
            clean_at = str(at_user).replace("[CQ:at,qq=", "").replace("]", "").strip()
            if clean_at:
                if is_user_muted(clean_at):
                    safe_log(f"🛑 [发送防护] 目标用户 {clean_at} 处于静默保护期，已自动剥离 @")
                else:
                    prefix += f"[CQ:at,qq={clean_at}] "

        final_msg = f"{prefix}{message}"

        if not _should_bypass_split(message, auto_split):
            bubbles = split_message_bubbles(final_msg, max_bubbles=4)
            if len(bubbles) > 1:
                dispatch_msg = await _dispatch_bubbles("group", group_id, bubbles)
                clean_full = clean_message_no_period(final_msg)
                record_bot_spoke_persistent(str(group_id), clean_full)
                record_chat_archive(f"group_{group_id}", BOT_QQ, "弥酱", clean_full, is_bot=True)
                return dispatch_msg

        clean_msg = clean_message_no_period(final_msg)
        resp = await send_group_msg_raw(group_id, clean_msg)
        record_bot_spoke_persistent(str(group_id), clean_msg)
        record_chat_archive(f"group_{group_id}", BOT_QQ, "弥酱", clean_msg, is_bot=True)
        return resp
    except Exception as e:
        return f"调用出错: {str(e)}"


async def send_private_msg_impl(
    user_id: int,
    message: str,
    auto_split: bool = True,
    cross_channel: bool = False,
    **kwargs
) -> str:
    """发送私聊消息给指定的 QQ 好友（默认开启自适应分段拆句与 0.5s~2.5s 长度加权延迟；内置 1A 防串台硬锁，主人传话可凭 cross_channel=True 解锁）"""
    if "waifu_mode" in kwargs:
        auto_split = bool(kwargs["waifu_mode"])

    # 1A 防串台物理通道硬锁（若为主人授权的跨通道传话，豁免重定向拦截）
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    if is_cross_channel_authorized(cross_channel):
        safe_log(f"🔓 [跨通道授权] 主人指令跨通道发送私聊 -> 目标QQ[{user_id}]")
    else:
        if locked_type == "private" and locked_id and str(user_id) != locked_id:
            safe_log(f"🛡️ [防串台硬锁] 当前为私聊会话(private_{locked_id})，已自动修正误传目标QQ({user_id} -> {locked_id})！")
            user_id = int(locked_id)
        elif locked_type == "group" and locked_id:
            safe_log(f"🛡️ [防串台硬锁] 当前为群聊会话(group_{locked_id})，已自动拦截向私聊[{user_id}]的误发并重定向回群[{locked_id}]！")
            return await send_group_msg_impl(int(locked_id), message, auto_split=auto_split)

    log_mcp_tool("send_private_msg", f"私聊 {user_id} (auto_split={auto_split}) -> {message[:60]}")
    try:
        priv_key = f"private_{user_id}"
        if not _should_bypass_split(message, auto_split):
            bubbles = split_message_bubbles(message, max_bubbles=4)
            if len(bubbles) > 1:
                dispatch_msg = await _dispatch_bubbles("private", user_id, bubbles)
                clean_full = clean_message_no_period(message)
                record_bot_spoke_persistent(priv_key, clean_full)
                record_chat_archive(priv_key, BOT_QQ, "弥酱", clean_full, is_bot=True)
                return dispatch_msg

        clean_msg = clean_message_no_period(message)
        resp = await send_private_msg_raw(user_id, clean_msg)
        record_bot_spoke_persistent(priv_key, clean_msg)
        record_chat_archive(priv_key, BOT_QQ, "弥酱", clean_msg, is_bot=True)
        return resp
    except Exception as e:
        return f"调用出错: {str(e)}"


async def get_group_list_impl() -> str:
    """获取当前机器人的 QQ 群列表"""
    log_mcp_tool("get_group_list", "获取群列表")
    try:
        return await call_onebot_api_raw("/get_group_list", timeout=10.0)
    except Exception as e:
        return f"调用出错: {str(e)}"


async def get_friend_list_impl() -> str:
    """获取当前机器人的 QQ 好友列表"""
    log_mcp_tool("get_friend_list", "获取好友列表")
    try:
        return await call_onebot_api_raw("/get_friend_list", timeout=10.0)
    except Exception as e:
        return f"调用出错: {str(e)}"


async def poke_user_impl(group_id: int = 0, user_id: int = 0, cross_channel: bool = False) -> str:
    """对指定群成员或私聊好友发送戳一戳"""
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    if is_cross_channel_authorized(cross_channel):
        safe_log(f"🔓 [跨通道授权] 主人指令跨通道戳一戳 -> 群[{group_id}]/QQ[{user_id}]")
    else:
        if locked_type == "private" and locked_id:
            group_id = 0
            user_id = int(locked_id)
        elif locked_type == "group" and locked_id and group_id != int(locked_id):
            group_id = int(locked_id)

    target_desc = f"群[{group_id}]成员[{user_id}]" if group_id else f"私聊好友[{user_id}]"
    log_mcp_tool("poke_user", f"发送戳一戳 -> {target_desc}")
    try:
        res = await send_poke(group_id, user_id)
        if res.get("status") == "ok" or res.get("retcode") == 0:
            return f"已成功戳了戳 {target_desc} 喵！"
        return f"戳一戳未成功，响应: {res}"
    except Exception as e:
        return f"戳一戳出错: {str(e)}"
