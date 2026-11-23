"""
tools/deskpet_tools.py — 星见弥 (弥酱) 桌宠交互、双轨屏幕感知与 CUA 电脑操作引擎 V1.0

核心设计：
1. 桌宠气泡、动作与 9D 语音三合一 (`pet_say_impl`)：
   - 原生支持 Coopanion 同款 `【动作, 表情】`（先做动作再换新气泡）与 `<表情>`（打字到该处同步触发微表情）行内脚本语法；
   - 自动按 `【...】` 拍点切分台词并调用 `tools/voice_tools.py` 合成 9 维情感/潜空间复合 WAV 语音，下发至 DeskPet 前端通过 WebAudio 立体声播放并同步气泡停留时长与说话弹动；
   - 当 DeskPet 窗口未启动时，平滑降级至向主人发送 QQ 私聊消息，确保回复 100% 不丢失。
2. 反作弊安全屏幕视觉分析 (`pet_cua_screenshot_impl` / `pet_cua_zoom_impl` / `pet_cua_windows_impl`)：
   - 底层由 DeskPet 的 Win32 `koffi` + GDI `BitBlt(SRCCOPY | CAPTUREBLT)` 子进程执行（0 全局钩子、0 进程注入、自动开启 `setContentProtection` 隐藏桌宠防套娃）；
   - 截图自动落盘至 `image_cache/deskpet/` 并引导 `agy` 调用 `view_file` 多模态真看画面；
   - `pet_cua_zoom` 支持按显示器物理原分辨率重截局部区域，精准看清 IDE 报错小字与游戏 UI。
3. CUA 键鼠操作与三把物理安全锁 (`pet_cua_action_impl`)：
   - 代码级 `AGY_CALLER_ID == MASTER_QQ` 硬权限锁，严防外部群聊或屏幕文字 Prompt Injection；
   - 桌宠气泡四档权限审批（`ask-once` 30 分钟免问 + 终端窗口独立二次确认）；
   - `GetLastInputInfo` + 指针位移 `>2px` 2 秒人类静止让位锁（人类一动键鼠立刻停手让位）。
4. 三端联动的「主动看屏吐槽总开关 (`proactivePeek`)」状态管理 (`get_deskpet_overview` / `manage_deskpet_settings`)。
"""
import os
import re
import time
import json
import base64
import httpx
from config import (
    log_mcp_tool,
    safe_log,
    BOT_QQ,
    MASTER_QQ,
    NAPCAT_TOKEN,
    INTERNAL_BUBBLE_PORT,
    DESKPET_API_CANDIDATES,
    DESKPET_CACHE_DIR,
    DESKPET_STATE_FILE,
)
from core.state_manager import record_bot_spoke_persistent
from social.long_term_memory import record_chat_archive
from tools.message_tools import clean_message_no_period, send_private_msg_impl
from tools.voice_tools import (
    resolve_emotion_profile,
    sanitize_tts_text,
    _synthesize_speech_wav,
)

# 缓存最近一次连通的 DeskPet 服务端点
_ACTIVE_DESKPET_BASE_URL: str = ""

DEFAULT_DESKPET_LOCAL_STATE = {
    "proactive_peek": True,
    "allow_in_game": True,
    "normal_interval_sec": 900,
    "game_interval_sec": 240,
    "cua_enabled": True,
    "cua_control": True,
    "cua_permission": "ask-once",
    "scale": 1.4,
    "roam": "calm",
    "sound": True,
    "theme": "dark",
    "pass_when_fullscreen": False,
    "asr_hotkey": "LeftAlt*2",
    "asr_mode": "hold",
    "type_hotkey": "LeftCtrl*2",
    "type_key_enabled": True,
    "updated_at": 0.0,
}


def load_deskpet_local_state() -> dict:
    """读取本地持久化的桌宠开关与偏好配置（供 DeskPet 离线时也能读写状态，上线即同步）"""
    state = dict(DEFAULT_DESKPET_LOCAL_STATE)
    try:
        if os.path.exists(DESKPET_STATE_FILE):
            with open(DESKPET_STATE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                state.update(raw)
    except Exception:
        pass
    return state


def save_deskpet_local_state(patch: dict) -> dict:
    """更新并落盘桌宠配置状态"""
    state = load_deskpet_local_state()
    state.update(patch)
    state["updated_at"] = time.time()
    try:
        with open(DESKPET_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        safe_log(f"⚠️ [DeskPet] 保存 deskpet_state.json 失败: {e}")
    return state


def _get_auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {NAPCAT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }


async def _probe_deskpet_base_url(timeout: float = 1.5) -> str:
    """极速探测当前可用的 DeskPet 客户端端点（本机 127.0.0.1:7797 或 Tailscale YOUR_TTS_SERVER_IP:7797）"""
    global _ACTIVE_DESKPET_BASE_URL
    candidates = []
    if _ACTIVE_DESKPET_BASE_URL:
        candidates.append(_ACTIVE_DESKPET_BASE_URL)
    for c in DESKPET_API_CANDIDATES:
        if c not in candidates:
            candidates.append(c)

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for base in candidates:
            try:
                resp = await client.get(f"{base}/api/mcp/state", headers=_get_auth_headers())
                if resp.status_code == 200:
                    _ACTIVE_DESKPET_BASE_URL = base
                    return base
            except Exception:
                continue

    _ACTIVE_DESKPET_BASE_URL = ""
    return ""


async def call_deskpet_api(path: str, payload: dict = None, method: str = "POST", timeout: float = 25.0) -> dict:
    """调用 DeskPet 客户端 /api/mcp/* 接口（优先直连 7797，若 Windows 防火墙拦截入站则自动走本机 18302 反向轮询桥）"""
    global _ACTIVE_DESKPET_BASE_URL
    base = await _probe_deskpet_base_url(timeout=1.0)
    if base:
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                url = f"{base}{path}"
                if method.upper() == "GET":
                    resp = await client.get(url, headers=_get_auth_headers())
                else:
                    resp = await client.post(url, json=payload or {}, headers=_get_auth_headers())
                if resp.status_code == 200:
                    return resp.json()
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception:
            _ACTIVE_DESKPET_BASE_URL = ""

    # 降级通过本机 bubble_dispatcher (:18302/deskpet_proxy) 反向长轮询穿透
    try:
        async with httpx.AsyncClient(timeout=max(3.0, timeout + 2.0), trust_env=False) as client:
            resp = await client.post(
                f"http://127.0.0.1:{INTERNAL_BUBBLE_PORT}/deskpet_proxy",
                json={"method": method.upper(), "path": path, "payload": payload or {}, "timeout": timeout},
                headers=_get_auth_headers(),
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        return {"ok": False, "offline": True, "error": str(e)}

    return {"ok": False, "offline": True, "error": "Windows 桌宠客户端 (DeskPet:7797) 当前未启动或未连接"}


async def push_deskpet_status(kind: str = None, text: str = "", detail: str = "") -> None:
    """
    向桌宠头顶推送实时工作状态气泡（由 transcript_stream 异步调用，0 阻塞）：
    kind 取值: 'think' | 'look' | 'click' | 'scroll' | 'type' | 'read' | 'browse' | 'search' | 'write' | 'wait' | 'work' | None
    """
    status_obj = None
    if kind:
        status_obj = {"kind": kind, "text": text or "在忙"}
        if detail:
            status_obj["detail"] = detail[:20]
    await call_deskpet_api("/api/mcp/status", {"status": status_obj}, method="POST", timeout=2.0)


def map_tool_to_deskpet_status(tool_name: str, args: dict = None) -> tuple[str, str, str]:
    """将 qqAgent 的 MCP 工具名映射为 Coopanion 同款头顶状态泡 (kind, text, detail)"""
    args = args or {}
    if tool_name == "pet_say":
        has_voice = bool(args.get("voice")) or bool(str(args.get("ja_voice") or "").strip())
        if has_voice:
            return "work", "在合成语音…", ""
        return "", "", ""
    if tool_name in ("pet_ask", "pet_act", "pet_move_to", "pet_set"):
        return "", "", ""
    if tool_name in ("pet_cua_screenshot", "pet_cua_zoom", "inspect_image", "view_file"):
        return "look", "在看屏幕", ""
    if tool_name == "pet_cua_windows":
        return "look", "在看开着的窗口", ""
    if tool_name == "pet_cua_action":
        act = str(args.get("action", "click")).lower()
        if act in ("type", "key"):
            return "type", "在打字" if act == "type" else "在按键", ""
        if act == "scroll":
            return "scroll", "在滚动", ""
        if act == "wait":
            return "wait", "在等待", ""
        if act == "focus":
            return "click", "在切窗口", ""
        return "click", "在操作鼠标", ""
    if tool_name in ("search_memory", "recall_past_memory", "search_permanent_memory"):
        q = str(args.get("query") or args.get("keyword") or "").strip()
        return "search", "在翻记忆", q
    if tool_name in ("memory_action", "save_long_term_memory", "record_pending_thought"):
        return "write", "在记心事", ""
    if tool_name.startswith("bili_"):
        return "browse", "在刷B站", ""
    if tool_name in ("qzone_action", "publish_qzone_feed"):
        return "write", "在看QQ空间", ""
    if tool_name == "play_board_game":
        return "work", "在琢磨棋局", ""
    return "work", "在忙", ""


def _split_script_beats_for_tts(script: str) -> list[str]:
    """
    按 Coopanion `【动作, 表情】` 分拍规则提取每个气泡拍的纯语音文本（剥离 `<表情>` 行内标签），
    供逐拍合成 GPT-SoVITS-v5 WAV 音频。
    """
    parts = re.split(r'【[^】]*】', script or "")
    out = []
    for p in parts:
        # 剥离 <表情> 行内动作标签
        no_inline = re.sub(r'[<＜][^>＞\n]{1,32}[>＞]', '', p).strip()
        if no_inline:
            out.append(no_inline)
    return out


def _save_cua_jpeg(jpeg_b64: str, prefix: str = "screen") -> str:
    """将 DeskPet 返回的 JPEG Base64 落盘至 image_cache/deskpet/ 并清理过期旧截图"""
    os.makedirs(DESKPET_CACHE_DIR, exist_ok=True)
    now_ms = int(time.time() * 1000)
    filename = f"{prefix}_{now_ms}.jpg"
    filepath = os.path.join(DESKPET_CACHE_DIR, filename)
    raw_bytes = base64.b64decode(jpeg_b64)
    with open(filepath, "wb") as f:
        f.write(raw_bytes)

    # 仅保留最近 20 张桌宠截图，防止磁盘膨胀
    try:
        files = sorted(
            [os.path.join(DESKPET_CACHE_DIR, fn) for fn in os.listdir(DESKPET_CACHE_DIR) if fn.endswith(".jpg")],
            key=lambda p: os.path.getmtime(p),
        )
        for old_f in files[:-20]:
            try:
                os.remove(old_f)
            except Exception:
                pass
    except Exception:
        pass
    return os.path.abspath(filepath)


def _verify_master_cua_permission(tool_name: str) -> str | None:
    """防 Prompt Injection 硬权限锁：仅当调用源为 MASTER_QQ 时放行 CUA 写操作"""
    caller_id = os.environ.get("AGY_CALLER_ID", "").strip()
    if caller_id and caller_id != str(MASTER_QQ):
        safe_log(f"🚨 [安全拦截] 非主人({caller_id}) 越权尝试调用桌宠电脑操作工具 {tool_name}，已物理阻断！")
        return f"权限拒绝：只有主人({MASTER_QQ})本人在桌宠侧发起的指令才能操作电脑屏幕与键鼠喵！"
    return None


def _contains_japanese_kana(text: str) -> bool:
    """检测文本中是否包含平假名/片假名（用于防止误把纯中文送入日语 GPT-SoVITS 声库）"""
    return bool(re.search(r'[\u3040-\u30ff]', text or ""))


def _split_ja_voice_for_tts(ja_voice: str) -> list[str]:
    """切分日语配音文本：支持通过 '|' 或 `【...】` 分段，并剥离 `<表情>` 行内标记"""
    parts = re.split(r'【[^】]*】|\|+', ja_voice or "")
    out = []
    for p in parts:
        no_inline = re.sub(r'[<＜][^>＞\n]{1,32}[>＞]', '', p).strip()
        if no_inline:
            out.append(no_inline)
    return out


async def pet_say_impl(
    script: str,
    emotion: str = "auto",
    voice: bool = False,
    ja_voice: str = "",
) -> str:
    """
    让桌宠弥酱在屏幕气泡中说出中文台词，并支持按「中文气泡 (script) + 日语真声 (ja_voice)」播放 9D 情感语音与表情动作。
    支持 `【表情/动作】`（先做动作再换气泡）与 `<表情/动作>`（打字到此处时触发）。
    """
    raw_script = clean_message_no_period(script or "")
    if not raw_script:
        return "台词内容为空"

    clean_ja_raw = (ja_voice or "").strip()
    should_speak = bool(voice) or bool(clean_ja_raw)
    if should_speak and not clean_ja_raw and not _contains_japanese_kana(raw_script):
        # 严格贯彻「中文气泡 + 日语语音」方案：未传日语台词 ja_voice 且 script 为纯中文时自动仅发气泡，避免中文 TTS 违和感
        should_speak = False
        safe_log("🐾 [DeskPet] pet_say 未提供日语台词 ja_voice 且 script 不含假名，自动降级为纯中文气泡 (免除中文 TTS 违和感)")

    resolved_emotion, emo_profile = resolve_emotion_profile(emotion)
    emotion_label = emo_profile.get("label", resolved_emotion)
    log_mcp_tool(
        "pet_say",
        f"桌宠气泡发言 [{'中文气泡+日语语音' if should_speak else '纯中文气泡'} | 声线:{resolved_emotion}({emotion_label})]: {raw_script[:60]}",
    )

    # 1. 若开启语音，按 Japanese (`text_lang="ja"`) 为日语配音片段合成 GPT-SoVITS-v5 WAV 音频
    wav_b64_list = []
    if should_speak:
        voice_beats = _split_ja_voice_for_tts(clean_ja_raw) if clean_ja_raw else _split_script_beats_for_tts(raw_script)
        if any(len(sanitize_tts_text(bt)) >= 2 for bt in voice_beats):
            await push_deskpet_status("work", "在合成语音…", emotion_label)
        for bt in voice_beats:
            clean_tts = sanitize_tts_text(bt)
            if len(clean_tts) >= 2:
                wav_bytes, _ = await _synthesize_speech_wav(clean_tts, emo_profile, text_lang="ja")
                if wav_bytes:
                    wav_b64_list.append(base64.b64encode(wav_bytes).decode("ascii"))

    # 2. 发送至 DeskPet 本地客户端
    res = await call_deskpet_api(
        "/api/mcp/say",
        {"script": raw_script, "emotion": resolved_emotion, "wav_base64_list": wav_b64_list},
        timeout=10.0,
    )

    # 记录发言至 desktop_master 与主人私聊归档，保持跨端上下文连贯
    plain_record = re.sub(r'【[^】]*】|[<＜][^>＞\n]{1,32}[>＞]', '', raw_script).strip() or raw_script
    voice_tag = f"[桌宠气泡+日语语音({emotion_label})] " if any(wav_b64_list) else "[桌宠气泡] "
    record_bot_spoke_persistent("desktop_master", f"{voice_tag}{plain_record}")
    record_chat_archive("desktop_master", str(BOT_QQ), "弥酱", f"{voice_tag}{plain_record}", is_bot=True)

    # 标记语音触发以延长单轮推理超时保护
    marker_file = os.environ.get("AGY_VOICE_MARKER_FILE", "").strip()
    if marker_file and any(wav_b64_list):
        try:
            with open(marker_file, "w", encoding="utf-8") as mf:
                mf.write(resolved_emotion)
        except Exception:
            pass

    if res.get("ok") and res.get("sent"):
        est = res.get("estimatedSeconds", 3.0)
        dropped = res.get("dropped") or []
        drop_note = f"（已忽略未识别动作词: {', '.join(dropped)}）" if dropped else ""
        tts_note = f"，已同步播放「{emotion_label}」日语真声" if any(wav_b64_list) else ""
        return f"已在桌宠气泡说出（共 {res.get('beatsCount', 1)} 拍，预计显示 {est} 秒{tts_note}）{drop_note}"

    # 降级：若桌宠窗口未打开，自动转发至主人 QQ 私聊，确保不丢消息
    safe_log("ℹ️ [DeskPet 离线降级] 桌宠窗口未连接，自动将 pet_say 台词转发至主人 QQ 私聊。")
    await send_private_msg_impl(int(MASTER_QQ), plain_record, auto_split=True, cross_channel=True)
    return f"桌宠窗口当前未开启，已自动将台词通过 QQ 私聊发送给主人：{plain_record[:40]}"


async def pet_ask_impl(question: str, options: list[str] = None, allow_own_answer: bool = True) -> str:
    """在桌宠气泡中向主人提出带选项的问题（最多 3~4 个选项，主人可按数字键 1-4 或点击回答）"""
    q_clean = clean_message_no_period(question or "")
    opts = [str(o).strip() for o in (options or []) if str(o).strip()][:4]
    log_mcp_tool("pet_ask", f"桌宠提问: {q_clean} | 选项: {opts}")

    res = await call_deskpet_api(
        "/api/mcp/ask",
        {"question": q_clean, "options": opts, "allow_own_answer": allow_own_answer},
    )
    record_bot_spoke_persistent("desktop_master", f"[桌宠提问] {q_clean} ({'/'.join(opts)})")
    record_chat_archive("desktop_master", str(BOT_QQ), "弥酱", f"[桌宠提问] {q_clean}", is_bot=True)

    if res.get("ok") and res.get("sent"):
        return f"已在桌宠弹出提问气泡：「{q_clean}」（选项：{', '.join(opts)}），主人的回答将以 [answer] 事件送达。"
    return f"提问气泡发送失败：{res.get('error', '桌宠窗口未连接')}"


async def pet_act_impl(actions: str) -> str:
    """让桌宠弥酱不说话，依次做一串表情或动作（如 'happy, jump' 或 '害羞, 转圈'）"""
    log_mcp_tool("pet_act", f"桌宠动作: {actions}")
    res = await call_deskpet_api("/api/mcp/act", {"actions": actions})
    if res.get("ok"):
        done_acts = res.get("actions") or []
        dropped = res.get("dropped") or []
        note = f"（略过未知词: {', '.join(dropped)}）" if dropped else ""
        return f"桌宠已执行动作/表情: {', '.join(done_acts)}{note}"
    return f"桌宠动作执行失败: {res.get('error', '未连接')}"


async def pet_move_to_impl(to: str = "center", run: bool = False) -> str:
    """让桌宠弥酱走到或跑到屏幕指定横向位置（'left' / 'center' / 'right' / 'cursor' 或 0.0~1.0）"""
    log_mcp_tool("pet_move_to", f"移动到: {to} (run={run})")
    res = await call_deskpet_api("/api/mcp/move", {"to": to, "run": run})
    if res.get("ok"):
        mode_str = "小跑" if run else "走"
        return f"弥酱正在桌面上向 {to} {mode_str}去～"
    return f"桌宠移动失败: {res.get('error', '未连接')}"


async def pet_set_impl(
    proactive_peek: bool = None,
    allow_in_game: bool = None,
    roam: str = "",
    scale: float = 0.0,
) -> str:
    """修改桌宠弥酱的习惯与开关（包括开启/关闭主动看屏吐槽总开关 proactive_peek、走动频率 roam、大小 scale）"""
    patch = {}
    local_patch = {}
    if proactive_peek is not None:
        patch["proactivePeek"] = bool(proactive_peek)
        local_patch["proactive_peek"] = bool(proactive_peek)
    if allow_in_game is not None:
        patch["allowInGame"] = bool(allow_in_game)
        local_patch["allow_in_game"] = bool(allow_in_game)
    if roam in ("off", "calm", "free"):
        patch["roam"] = roam
        local_patch["roam"] = roam
    if scale and 0.5 <= float(scale) <= 10.0:
        patch["scale"] = round(float(scale), 2)
        local_patch["scale"] = round(float(scale), 2)

    if local_patch:
        save_deskpet_local_state(local_patch)

    log_mcp_tool("pet_set", f"更新桌宠设置: {patch}")
    res = await call_deskpet_api("/api/mcp/prefs", patch)
    desc_parts = []
    if "proactivePeek" in patch:
        desc_parts.append(f"主动看屏吐槽总开关已{'开启' if patch['proactivePeek'] else '关闭'}")
    if "allowInGame" in patch:
        desc_parts.append(f"游戏局内吐槽已{'开启' if patch['allowInGame'] else '关闭'}")
    if "roam" in patch:
        desc_parts.append(f"走动模式已设为 {patch['roam']}")
    if "scale" in patch:
        desc_parts.append(f"桌宠大小已设为 {int(patch['scale'] * 100)}%")

    summary = "、".join(desc_parts) or "设置已保存"
    if res.get("ok"):
        return f"✅ {summary}（已实时同步至桌宠客户端）。"
    return f"✅ {summary}（已保存至本地配置，待桌宠客户端启动后自动生效）。"


async def pet_cua_screenshot_impl(display: int = 0) -> str:
    """截取主人电脑屏幕画面（自动隐身桌宠防遮挡、绘制当前鼠标指针位置）并返回本地路径供 view_file 查看"""
    err = _verify_master_cua_permission("pet_cua_screenshot")
    if err:
        return err

    log_mcp_tool("pet_cua_screenshot", f"截取显示器 display={display or 'auto'}")
    res = await call_deskpet_api("/api/mcp/cua", {"op": "screenshot", "display": display})
    if not res.get("ok"):
        return f"截屏失败：{res.get('error', '未知错误')}"

    jpeg_b64 = res.get("jpeg_base64", "")
    if not jpeg_b64:
        return "截屏失败：未获取到画面数据"

    local_path = _save_cua_jpeg(jpeg_b64, prefix="screen")
    fg_title = res.get("foreground") or "未知窗口"
    fg_app = res.get("foregroundApp") or ""
    cursor = res.get("cursor") or {}
    w, h = res.get("width", 1280), res.get("height", 800)
    disp_num = res.get("display", 1)

    return (
        f"=== 📸 成功截取电脑屏幕 (显示器 #{disp_num}, 截图尺寸 {w}×{h}) ===\n"
        f"- 当前前台窗口: [{fg_app}] {fg_title}\n"
        f"- 鼠标指针坐标: ({cursor.get('x', 0)}, {cursor.get('y', 0)})\n"
        f"- 截图本地绝对路径: {local_path}\n"
        f"💡【视觉分析与操作指引】：\n"
        f"1. 请立即调用系统内置的 `view_file(AbsolutePath='{local_path}')` 查看这张屏幕截图；\n"
        f"2. 若画面中的报错小字或局部图标看不清，可调用 `pet_cua_zoom(x, y, width, height)` 按原始高清分辨率放大局部；\n"
        f"3. 后续任何点击 (`pet_cua_action(action='click', x=..., y=...)`) 的坐标均直接采用这张截图 ({w}×{h}) 上的像素坐标！\n"
        f"🔊【识屏必配日语语音铁律】：你刚刚查看了主人的屏幕画面！接下来调用 `pet_say` 回复主人时，必须传入 `voice=True`，`script` 写中文气泡台词（带【动作, 表情】），并务必在 `ja_voice` 参数中填入对应的地道二次元猫娘日语配音台词（如 `ja_voice='お兄ちゃん、画面見たよ！すっごく面白そうだにゃ～！'`）！"
    )


async def pet_cua_zoom_impl(x: int, y: int, width: int = 360, height: int = 260) -> str:
    """按显示器原始物理分辨率高清重截最近一张截图中的局部矩形区域，专门用于看清代码报错、终端小字或密集 UI"""
    err = _verify_master_cua_permission("pet_cua_zoom")
    if err:
        return err

    log_mcp_tool("pet_cua_zoom", f"局部高清放大 ({x}, {y}, {width}×{height})")
    res = await call_deskpet_api(
        "/api/mcp/cua",
        {"op": "zoom", "x": int(x), "y": int(y), "width": int(width), "height": int(height)},
    )
    if not res.get("ok"):
        return f"局部放大截屏失败：{res.get('error', '未知错误')}"

    local_path = _save_cua_jpeg(res["jpeg_base64"], prefix="zoom")
    return (
        f"=== 🔍 成功截取局部原分辨率高清图 (区域 x={x}, y={y}, {res.get('width')}×{res.get('height')}) ===\n"
        f"- 高清局部图绝对路径: {local_path}\n"
        f"💡 请立即调用 `view_file(AbsolutePath='{local_path}')` 查看高清细节！\n"
        f"🔊【识屏必配日语语音铁律】：你刚刚查看了主人的局部高清画面！接下来调用 `pet_say` 回复主人时，必须传入 `voice=True`，`script` 写中文气泡台词（带【动作, 表情】），并务必在 `ja_voice` 参数中填入对应的地道二次元猫娘日语配音台词！"
    )


async def pet_cua_windows_impl() -> str:
    """列出主人电脑桌面上所有可见的顶层窗口（标题、所属程序、所在显示器、是否前台或最小化）"""
    err = _verify_master_cua_permission("pet_cua_windows")
    if err:
        return err

    log_mcp_tool("pet_cua_windows", "枚举桌面可见顶层窗口")
    res = await call_deskpet_api("/api/mcp/cua", {"op": "windows"})
    if not res.get("ok"):
        return f"获取窗口列表失败：{res.get('error', '未知错误')}"

    wins = res.get("windows") or []
    if not wins:
        return "当前桌面上没有检测到可见的顶层窗口。"

    lines = [f"=== 🪟 当前桌面可见顶层窗口 (共 {len(wins)} 个，按从前到后排序) ==="]
    for idx, w in enumerate(wins[:25], start=1):
        tags = []
        if w.get("focused"):
            tags.append("★当前前台")
        if w.get("minimized"):
            tags.append("已最小化")
        if w.get("display"):
            tags.append(f"显示器#{w['display']}")
        tag_str = f" [{' | '.join(tags)}]" if tags else ""
        app_str = f"({w.get('app')}) " if w.get("app") else ""
        lines.append(f"{idx}. {app_str}「{w.get('title') or '无标题'}」{tag_str} (handle={w.get('handle')})")
    return "\n".join(lines)


async def pet_cua_action_impl(
    action: str,
    x: int = 0,
    y: int = 0,
    to_x: int = 0,
    to_y: int = 0,
    button: str = "left",
    clicks: int = 1,
    down: int = 0,
    right: int = 0,
    text: str = "",
    keys: str = "",
    hold_seconds: float = 0.0,
    window: str = "",
    wait_seconds: float = 0.0,
    screenshot: bool = True,
) -> str:
    """
    执行电脑键鼠或窗口操作（自带 2 秒人类静止让位锁与桌宠气泡权限审批）：
    - action='click': 点击 (x, y)，支持 button='left'|'right'|'middle' 与 clicks=1|2|3
    - action='move': 移动鼠标悬停到 (x, y)
    - action='drag': 从 (x, y) 按住左键拖动到 (to_x, to_y)
    - action='scroll': 在 (x, y) 滚动滚轮，down>0 向下滚，down<0 向上滚
    - action='type': 按 Unicode 输入文字 text（末尾加 \\n 可按回车；打字途中主人一碰键鼠自动停手让位）
    - action='key': 按下快捷键或按键序列 keys（如 'ctrl+s', 'alt+tab', 'enter'），可选 hold_seconds 按住秒数
    - action='focus': 将标题或进程名包含 window 的窗口切到前台（若最小化则自动还原）
    - action='wait': 等待 wait_seconds 秒（最多 30 秒）后重新截屏
    """
    err = _verify_master_cua_permission(f"pet_cua_action({action})")
    if err:
        return err

    act = (action or "").strip().lower()
    if act == "mouse_down":
        act = "mouseDown"
    elif act == "mouse_up":
        act = "mouseUp"

    log_mcp_tool("pet_cua_action", f"执行 CUA 动作: {act} (x={x}, y={y}, text='{text[:20]}', keys='{keys}', win='{window}')")
    payload = {
        "op": act,
        "x": int(x),
        "y": int(y),
        "to_x": int(to_x),
        "to_y": int(to_y),
        "button": button,
        "clicks": int(clicks),
        "down": int(down),
        "right": int(right),
        "text": text,
        "keys": keys,
        "hold_seconds": float(hold_seconds),
        "window": window,
        "seconds": float(wait_seconds),
        "screenshot": bool(screenshot),
    }
    res = await call_deskpet_api("/api/mcp/cua", payload, timeout=65.0)
    if not res.get("ok"):
        if res.get("yielded"):
            return f"⏸️ 【已自动让位给主人】：{res.get('error')}（请先停手或询问主人是否需要继续操作）"
        if res.get("permitted") is False:
            return f"🔒 【未获授权】：{res.get('error')}"
        return f"操作未完成：{res.get('error', '未知原因')}"

    lines = [f"✅ 已完成电脑操作 `{act}`"]
    if act == "type" and isinstance(res.get("result"), dict):
        lines[0] += f"（成功输入 {res['result'].get('typed', len(text))} 个字符）"
    if res.get("foreground"):
        lines.append(f"- 当前前台窗口: [{res.get('foregroundApp') or ''}] {res.get('foreground')}")
    if res.get("cursor"):
        c = res["cursor"]
        lines.append(f"- 当前鼠标位置: ({c.get('x')}, {c.get('y')})")

    if res.get("jpeg_base64"):
        shot_path = _save_cua_jpeg(res["jpeg_base64"], prefix=f"after_{act}")
        lines.append(f"- 操作后最新屏幕截图: {shot_path}")
        lines.append(f"💡 可调用 `view_file(AbsolutePath='{shot_path}')` 确认操作后的最新画面结果。")
    return "\n".join(lines)


async def get_deskpet_overview() -> dict:
    """获取桌宠与屏幕感知系统全景状态（供 8088 WebUI 桌宠控制台与诊断指令使用）"""
    local_st = load_deskpet_local_state()
    live = await call_deskpet_api("/api/mcp/state", method="GET", timeout=1.5)
    if live.get("ok"):
        # 将实时状态回写同步到本地
        pro = live.get("proactive") or {}
        prefs = live.get("prefs") or {}
        cua = live.get("cua") or {}
        return {
            "success": True,
            "online": True,
            "pet_window_connected": bool(live.get("petConnected")),
            "figure": live.get("figure", "mi"),
            "figure_name": live.get("figureName", "星见弥 (弥酱)"),
            "proactive_peek": bool(pro.get("enabled", True)),
            "allow_in_game": bool(pro.get("allowInGame", True)),
            "in_game": bool(pro.get("inGame", False)),
            "foreground_app": pro.get("foregroundApp") or "",
            "foreground_title": pro.get("foregroundTitle") or "",
            "normal_interval_sec": int(pro.get("normalIntervalSec") or 900),
            "game_interval_sec": int(pro.get("gameIntervalSec") or 240),
            "cua_enabled": bool(cua.get("enabled", True)),
            "cua_control": bool(cua.get("control", True)),
            "cua_permission": cua.get("permission", "ask-once"),
            "cua_granted_remaining_sec": int(cua.get("grantedRemainingSec") or 0),
            "scale": float(prefs.get("scale") or 1.0),
            "roam": prefs.get("roam", "calm"),
            "sound": bool(prefs.get("sound", True)),
            "theme": prefs.get("theme", "dark"),
            "pass_when_fullscreen": bool(prefs.get("passWhenFullscreen", True)),
            "asr_ready": bool((live.get("asr") or {}).get("ready", False)),
            "asr_hotkey": (live.get("asr") or {}).get("hotkey", "LeftAlt*2"),
        }

    return {
        "success": True,
        "online": False,
        "pet_window_connected": False,
        "figure": "mi",
        "figure_name": "星见弥 (弥酱)",
        "proactive_peek": bool(local_st.get("proactive_peek", True)),
        "allow_in_game": bool(local_st.get("allow_in_game", True)),
        "in_game": False,
        "foreground_app": "",
        "foreground_title": "",
        "normal_interval_sec": int(local_st.get("normal_interval_sec", 900)),
        "game_interval_sec": int(local_st.get("game_interval_sec", 240)),
        "cua_enabled": bool(local_st.get("cua_enabled", True)),
        "cua_control": bool(local_st.get("cua_control", True)),
        "cua_permission": local_st.get("cua_permission", "ask-once"),
        "cua_granted_remaining_sec": 0,
        "scale": float(local_st.get("scale", 1.0)),
        "roam": local_st.get("roam", "calm"),
        "sound": bool(local_st.get("sound", True)),
        "theme": local_st.get("theme", "dark"),
        "pass_when_fullscreen": bool(local_st.get("pass_when_fullscreen", True)),
        "asr_ready": False,
        "asr_hotkey": "LeftAlt*2",
    }
