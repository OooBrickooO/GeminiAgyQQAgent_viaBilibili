"""
tools/voice_tools.py — 星见弥 GPT-SoVITS-v5 多情感语音合成与发送引擎 V1.0

核心设计：
1. 八维声学情感矩阵 (`EMOTION_PROFILES`)：
   - 精选自 sister 模型的 8 段高质量无叹气/高辨识度参考切片 (normal/gentle/sweet/tsundere/tease/angry/sad/shy)；
   - 支持 `emotion="auto"` 自动联动 `social/mood_manager.py` (feiyu-mood 七维情绪状态机)，当大模型未显式指定情感时自动匹配当前主导心境。
2. 台词发音净水器 (`sanitize_tts_text`)：
   - 自动剥离 CQ 码、括号舞台动作描写（如 `(摇尾巴)`）、颜文字与 Emoji，防止 TTS 机械朗读符号名称；
   - 支持中日英三语无缝混读 (`text_lang="auto"`)。
3. 游戏零干扰与跨机低延迟 (`_synthesize_speech_wav`)：
   - 请求 `media_type="wav"` 走内存 `BytesIO` 直出，零 `ffmpeg` 子进程创建开销，配合 `batch_size=1` 平滑 GPU 瞬时功耗；
   - 自动探测本地 (`127.0.0.1:9880`) 与 Tailscale 专网主力机 (`YOUR_TTS_SERVER_IP:9880`) 并缓存活跃端点。
4. 无感文字降级保护 (Scheme A Fallback)：
   - 设 1.5s 极速连接探测；当 Windows 主力机未开机或 TTS 服务未启动时，输出提醒并自动降级调用文字气泡发送，确保当轮回复 100% 送达且不触发重复发信。
"""
import os
import re
import time
import base64
from datetime import datetime, timezone, timedelta
import httpx
from config import log_mcp_tool, safe_log, BOT_QQ, TTS_API_CANDIDATES
from core.state_manager import record_bot_spoke_persistent
from core.napcat_client import call_onebot_api_raw
from attention.user_mute import is_user_muted
from social.long_term_memory import record_chat_archive
from tools.message_tools import send_group_msg_impl, send_private_msg_impl, is_cross_channel_authorized

# 缓存最近一次连通的 TTS 服务端点，免除每次重复探测
_ACTIVE_TTS_BASE_URL: str = ""

# WebUI 黄金基准小参（对应截图 + Euler steps=16：batch_size=1, Euler steps=16, 分段间隔=0.3, 语速=1.0, top_k=15, top_p=0.8, temperature=0.7, 重复惩罚=1.35）
DEFAULT_TOP_K = 15
DEFAULT_TOP_P = 0.8
DEFAULT_TEMPERATURE = 0.7
DEFAULT_SAMPLE_STEPS = 16
DEFAULT_SPEED_FACTOR = 1.0
DEFAULT_BATCH_SIZE = 1
DEFAULT_FRAGMENT_INTERVAL = 0.3
DEFAULT_REPETITION_PENALTY = 1.35

# 精选 9 维声学情感参考配置（路径相对于 GPT-SoVITS-v5 工作目录）
# 注：全部保持 speed_factor=1.0，避免 SynthesizerTrnV3.enc_p 触发 F.interpolate(mode="linear") 导致音素边界糊化带电音
EMOTION_PROFILES = {
    "normal": {
        "ref_audio_path": "emotions/normal.wav",
        "prompt_text": "いないみたいだね。まあ、お兄ちゃんあんまりそういうの得意じゃないし。",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "日常平静",
    },
    "gentle": {
        "ref_audio_path": "emotions/gentle.wav",
        "prompt_text": "うん、わかった。いい子で待ってるから早く帰ってきてね。",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "乖巧温柔",
    },
    "sweet": {
        "ref_audio_path": "emotions/sweet.wav",
        "prompt_text": "そのまま私のことぎゅってしてほしいな。",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "撒娇甜美",
    },
    "tsundere": {
        "ref_audio_path": "emotions/tsundere.wav",
        "prompt_text": "これ店舗限定のやつだから、わざわざ買いに行ったんでしょ？",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "傲娇得意",
    },
    "tease": {
        "ref_audio_path": "emotions/tease.wav",
        "prompt_text": "ちょっとだけ覗いちゃおうかな。こんなの置いてったお兄ちゃんが悪いんだもんね",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "坏笑调侃",
    },
    "angry": {
        "ref_audio_path": "emotions/angry.wav",
        "prompt_text": "はあ、なんでそんなひどいことが言えるの？",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "生气嗔怪",
    },
    "sad": {
        "ref_audio_path": "emotions/sad.wav",
        "prompt_text": "今日はお兄ちゃんとゲームしたり、アニメ見たりして過ごす予定だったのに。",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "委屈失落",
    },
    "shy": {
        "ref_audio_path": "emotions/shy.wav",
        "prompt_text": "あっ、あっ、急に頭撫でてどうしたの？",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.8,
        "temperature": 0.7,
        "sample_steps": 16,
        "label": "害羞慌乱",
    },
    "whisper": {
        "ref_audio_path": "emotions/gentle.wav",
        "aux_ref_audio_paths": ["emotions/normal.wav"],
        "prompt_text": "うん、わかった。いい子で待ってるから早く帰ってきてね。",
        "prompt_lang": "ja",
        "speed_factor": 1.0,
        "top_p": 0.75,
        "temperature": 0.65,
        "sample_steps": 16,
        "label": "轻声耳语",
    },
}

# 情绪别名与 feiyu-mood 七维情绪映射表
EMOTION_ALIASES = {
    # 英文别名与 feiyu-mood 维度名
    "calm": "normal",
    "default": "normal",
    "daily": "normal",
    "worry": "gentle",
    "care": "gentle",
    "soft": "gentle",
    "love": "sweet",
    "spoiled": "sweet",
    "cute": "sweet",
    "proud": "tsundere",
    "smug": "tsundere",
    "happy": "tease",
    "playful": "tease",
    "joke": "tease",
    "jealous": "angry",
    "mad": "angry",
    "upset": "angry",
    "sulky": "sad",
    "cry": "sad",
    "grieved": "sad",
    "flustered": "shy",
    "embarrassed": "shy",
    "sleep": "whisper",
    "night": "whisper",
    "bedtime": "whisper",
    "quiet": "whisper",
    # 中文直传兼容
    "日常": "normal",
    "平静": "normal",
    "普通": "normal",
    "温柔": "gentle",
    "乖巧": "gentle",
    "体贴": "gentle",
    "撒娇": "sweet",
    "甜美": "sweet",
    "卖萌": "sweet",
    "傲娇": "tsundere",
    "嘴硬": "tsundere",
    "得意": "tsundere",
    "调侃": "tease",
    "坏笑": "tease",
    "吃瓜": "tease",
    "开心": "tease",
    "生气": "angry",
    "吃醋": "angry",
    "嗔怪": "angry",
    "抗议": "angry",
    "委屈": "sad",
    "失落": "sad",
    "难过": "sad",
    "郁闷": "sad",
    "害羞": "shy",
    "慌张": "shy",
    "耳语": "whisper",
    "悄悄话": "whisper",
    "轻声": "whisper",
    "哄睡": "whisper",
    "晚安": "whisper",
}


def is_late_night_window() -> bool:
    """
    判定当前是否处于深夜陪伴时段 (北京时间 00:00 ~ 06:00)。
    支持通过环境变量 FORCE_LATE_NIGHT_TTS=1 强制开启以便调试。
    """
    if os.environ.get("FORCE_LATE_NIGHT_TTS", "").strip() == "1":
        return True
    tz_bj = timezone(timedelta(hours=8))
    now_bj = datetime.now(tz_bj)
    return 0 <= now_bj.hour < 6


def _normalize_single_emotion(token: str) -> str | None:
    """将单个情绪词规范化为 EMOTION_PROFILES 的标准 key。"""
    t = (token or "").strip().lower()
    if not t:
        return None
    if t in EMOTION_PROFILES:
        return t
    if t in EMOTION_ALIASES:
        return EMOTION_ALIASES[t]
    return None


def _build_blended_profile(primary_key: str, secondary_key: str) -> tuple[str, dict]:
    """
    方案 3-A：构建主副双情感潜空间风格向量插值配置 (0.65 主情感 + 0.35 副情感)。
    主情感决定 GPT 语调骨架与 CFM 基准参考，副情感通过 aux_ref_audio_paths 注入 ge 风格向量融合。
    """
    p1 = EMOTION_PROFILES[primary_key]
    p2 = EMOTION_PROFILES[secondary_key]
    if primary_key == secondary_key or p1["ref_audio_path"] == p2["ref_audio_path"]:
        return primary_key, dict(p1)

    blended_speed = round(0.65 * float(p1.get("speed_factor", 1.0)) + 0.35 * float(p2.get("speed_factor", 1.0)), 3)
    aux_list = [p2["ref_audio_path"]]
    for extra in p1.get("aux_ref_audio_paths", []):
        if extra not in aux_list and extra != p1["ref_audio_path"]:
            aux_list.append(extra)

    blended_key = f"{primary_key}+{secondary_key}"
    blended_profile = {
        "ref_audio_path": p1["ref_audio_path"],
        "aux_ref_audio_paths": aux_list,
        "prompt_text": p1["prompt_text"],
        "prompt_lang": p1["prompt_lang"],
        "speed_factor": DEFAULT_SPEED_FACTOR,
        "top_p": float(p1.get("top_p", DEFAULT_TOP_P)),
        "temperature": float(p1.get("temperature", DEFAULT_TEMPERATURE)),
        "sample_steps": int(p1.get("sample_steps", DEFAULT_SAMPLE_STEPS)),
        "label": f"{p1['label']}+{p2['label']}",
    }
    return blended_key, blended_profile


def _apply_late_night_adjustment(
    raw_input: str,
    resolved_key: str,
    profile: dict
) -> tuple[str, dict]:
    """
    方案 2-B：深夜“耳语/陪伴模式” (00:00 ~ 06:00)。
    - 若传入 'auto' 或 'normal'，且最终解析出的心境为日常/温柔 (非强情绪斗嘴)，自动接管为深夜轻声陪伴态；
    - 若显式指定或情绪机处于傲娇/生气/害羞/复合等强情绪，保留原情绪性格与 speed_factor=1.0（防止线性插值电音），
      通过收敛 temperature=0.65 / top_p=0.75 柔化深夜听感。
    """
    if not is_late_night_window():
        return resolved_key, profile

    prof = dict(profile)
    if raw_input in ("auto", "normal", "calm", "default", "daily", "日常", "平静", "普通") and resolved_key in ("normal", "gentle"):
        whisper_prof = dict(EMOTION_PROFILES["whisper"])
        whisper_prof["label"] = "深夜轻语"
        return "whisper", whisper_prof

    if resolved_key != "whisper":
        prof["speed_factor"] = DEFAULT_SPEED_FACTOR
        prof["temperature"] = min(float(prof.get("temperature", DEFAULT_TEMPERATURE)), 0.65)
        prof["top_p"] = min(float(prof.get("top_p", DEFAULT_TOP_P)), 0.75)
        if "深夜" not in prof.get("label", ""):
            prof["label"] = f"{prof.get('label', resolved_key)}·深夜柔化"
    return resolved_key, prof


def resolve_emotion_profile(emotion: str = "auto") -> tuple[str, dict]:
    """
    解析目标情感标签，返回 (规范化情感key, 对应配置dict)。
    支持：
    1. 单一情感标签（如 'sweet', 'tsundere', 'whisper'）；
    2. 接口 α 组合语法糖：'主情感+副情感'（如 'tsundere+shy', 'sad+sweet', 'tease+gentle'），按 65%:35% 潜空间融合；
    3. 'auto' 自动联动 feiyu-mood 实时衰减后的七维情绪状态，当双维并存时自动触发复合声线；
    4. 方案 2-B 深夜陪伴模式自动柔化。
    """
    raw = (emotion or "auto").strip().lower()

    # 1. 检测接口 α 的 '+' / '/' / ',' 复合情感语法 (如 "tsundere+shy")
    for sep in ("+", "/", ",", "＋", "、"):
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep) if p.strip()]
            norm_parts = []
            for p in parts:
                nk = _normalize_single_emotion(p)
                if nk and nk not in norm_parts:
                    norm_parts.append(nk)
            if len(norm_parts) >= 2:
                b_key, b_prof = _build_blended_profile(norm_parts[0], norm_parts[1])
                return _apply_late_night_adjustment(raw, b_key, b_prof)
            elif len(norm_parts) == 1:
                k0 = norm_parts[0]
                return _apply_late_night_adjustment(raw, k0, dict(EMOTION_PROFILES[k0]))

    # 2. 单一显式情感匹配
    single_key = _normalize_single_emotion(raw)
    if single_key:
        return _apply_late_night_adjustment(raw, single_key, dict(EMOTION_PROFILES[single_key]))

    # 3. 'auto' 自动联动 feiyu-mood 实时衰减七维情绪 (支持单维与双维自动复合)
    try:
        from social.mood_manager import get_decayed_mood_state
        view, _ = get_decayed_mood_state()
        dims = view.get("dims", {})
        if isinstance(dims, dict) and dims:
            sorted_dims = sorted(dims.items(), key=lambda x: float(x[1]), reverse=True)
            top1_dim, top1_val = sorted_dims[0][0], float(sorted_dims[0][1])
            top2_dim, top2_val = (sorted_dims[1][0], float(sorted_dims[1][1])) if len(sorted_dims) > 1 else ("", 0.0)

            emo1 = _normalize_single_emotion(top1_dim) if top1_val >= 18.0 else None
            if emo1:
                # 若存在显著的第二情绪维 (例如 proud+sweet -> tsundere+shy 口嫌体正直)，自动构建复合声线
                emo2 = _normalize_single_emotion(top2_dim) if (top2_val >= 14.0 and top2_val >= 0.55 * top1_val) else None
                if emo2:
                    # 特判：当傲娇/吃醋与甜蜜害羞并存时，将 sweet 维度精准映射为 shy（害羞慌乱）
                    if emo1 in ("tsundere", "angry") and top2_dim == "sweet":
                        emo2 = "shy"
                    elif top1_dim == "sweet" and emo2 in ("tsundere", "angry"):
                        emo1, emo2 = emo2, "shy"
                    if emo1 != emo2:
                        b_key, b_prof = _build_blended_profile(emo1, emo2)
                        return _apply_late_night_adjustment(raw, b_key, b_prof)

                return _apply_late_night_adjustment(raw, emo1, dict(EMOTION_PROFILES[emo1]))
    except Exception:
        pass

    return _apply_late_night_adjustment(raw, "normal", dict(EMOTION_PROFILES["normal"]))


def sanitize_tts_text(raw_text: str) -> str:
    """
    清洗送往 TTS 引擎的台词文本：
    1. 移除所有 CQ 码 ([CQ:...])；
    2. 移除括号内的动作/神态/心理旁白（如 (摇尾巴)、（小声嘀咕）、*蹭蹭*），避免 TTS 把括号旁白念出来；
    3. 移除 Emoji 与复杂颜文字，仅保留中日英文字、数字与自然语调标点。
    """
    if not raw_text:
        return ""
    text = str(raw_text)
    # 1. 剥离 CQ 码
    text = re.sub(r'\[CQ:[^\]]+\]', '', text)
    # 2. 剥离圆括号/方括号/星号动作描写 (如 （笑）、(哼)、[捂脸])
    text = re.sub(r'（[^）]{1,25}）', '', text)
    text = re.sub(r'\([^\)]{1,25}\)', '', text)
    text = re.sub(r'\[[^\]]{1,15}\]', '', text)
    # 3. 剥离常见 Emoji 与特殊图形符号 (保留中日韩统一表意文字、假名、英数与常用标点)
    text = re.sub(
        r'[^\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff'
        r'a-zA-Z0-9\s，。！？、～~…—,.!?\'\"「」『』（）()：:；;·]',
        '',
        text
    )
    # 合并多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def _synthesize_speech_wav(spoken_text: str, profile: dict) -> tuple[bytes | None, str]:
    """
    向 GPT-SoVITS-v5 api_v2.py 请求合成 WAV 音频字节流。
    自动探测可用端点 (Tailscale 主力机 YOUR_TTS_SERVER_IP:9880 / 本地 127.0.0.1:9880)。
    返回: (wav_bytes, error_msg)
    """
    global _ACTIVE_TTS_BASE_URL

    payload = {
        "text": spoken_text,
        "text_lang": "auto",
        "ref_audio_path": profile["ref_audio_path"],
        "aux_ref_audio_paths": profile.get("aux_ref_audio_paths", []),
        "prompt_text": profile["prompt_text"],
        "prompt_lang": profile["prompt_lang"],
        "top_k": int(profile.get("top_k", DEFAULT_TOP_K)),
        "top_p": float(profile.get("top_p", DEFAULT_TOP_P)),
        "temperature": float(profile.get("temperature", DEFAULT_TEMPERATURE)),
        "sample_steps": int(profile.get("sample_steps", DEFAULT_SAMPLE_STEPS)),
        "text_split_method": "cut2",
        "batch_size": DEFAULT_BATCH_SIZE,
        "fragment_interval": DEFAULT_FRAGMENT_INTERVAL,
        "speed_factor": float(profile.get("speed_factor", DEFAULT_SPEED_FACTOR)),
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": True,
        "repetition_penalty": DEFAULT_REPETITION_PENALTY,
    }

    # 构建优先探测队列：若已有缓存成功的端点则排第一
    candidates = list(TTS_API_CANDIDATES)
    if _ACTIVE_TTS_BASE_URL and _ACTIVE_TTS_BASE_URL in candidates:
        candidates.remove(_ACTIVE_TTS_BASE_URL)
        candidates.insert(0, _ACTIVE_TTS_BASE_URL)

    # 2.5s 连接探测（兼容 Tailscale 隧道冷唤醒），读取超时 25s（合成长句留足余量），严禁走代理 (trust_env=False)
    timeout_cfg = httpx.Timeout(connect=2.5, read=25.0, write=5.0, pool=2.0)
    err_list = []

    async with httpx.AsyncClient(timeout=timeout_cfg, trust_env=False) as client:
        for base_url in candidates:
            tts_url = f"{base_url.rstrip('/')}/tts"
            try:
                resp = await client.post(tts_url, json=payload)
                if resp.status_code == 200 and len(resp.content) > 200:
                    _ACTIVE_TTS_BASE_URL = base_url
                    return resp.content, ""
                else:
                    err_detail = resp.text[:160] if resp.text else f"HTTP {resp.status_code}"
                    err_list.append(f"{base_url} 返回异常 ({resp.status_code}): {err_detail}")
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                err_list.append(f"{base_url} 未启动或不可达 ({type(e).__name__})")
                continue
            except Exception as e:
                err_list.append(f"{base_url} 请求异常: {e}")
                continue

    return None, " | ".join(err_list) if err_list else "未知连接错误"


async def send_voice_msg_impl(
    text: str,
    emotion: str = "auto",
    group_id: int = 0,
    user_id: int = 0,
    cross_channel: bool = False
) -> str:
    """
    合成弥酱专属二次元妹妹语音并发送到当前群聊或私聊。
    内置：
    - 1A 物理通道防串台硬锁（自动识别当前是群聊还是私聊；主人传话可凭 cross_channel=True 解锁）；
    - 离线/失败自动降级方案 A（主力机未开 TTS 时自动退化为普通文字气泡发出）。
    """
    # 1. 物理通道硬锁校验与目标自动补全
    locked_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
    locked_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
    allow_cross = is_cross_channel_authorized(cross_channel)

    is_private_target = False
    if allow_cross:
        if user_id and not group_id:
            is_private_target = True
        elif group_id:
            is_private_target = False
        else:
            return "错误：跨通道发送语音需明确指定目标 group_id 或 user_id。"
        safe_log(f"🔓 [跨通道授权] send_voice_msg 跨通道发送 -> 群:{group_id}/私聊:{user_id}")
    elif locked_type == "private" and locked_id:
        is_private_target = True
        user_id = int(locked_id)
        group_id = 0
    elif locked_type == "group" and locked_id:
        is_private_target = False
        group_id = int(locked_id)
    else:
        if user_id and not group_id:
            is_private_target = True
        elif group_id:
            is_private_target = False
        else:
            return "错误：未指定发送目标 group_id 或 user_id。"

    # 2. 群聊静默拦截检查
    if not is_private_target and group_id:
        at_matches = re.findall(r'\[CQ:at,qq=(\d+)\]', text or "")
        for target_qq in at_matches:
            if is_user_muted(target_qq):
                return f"发送拦截：群成员 {target_qq} 当前处于静默免打扰状态，已取消发送。"

    # 3. 清洗语音台词并解析情感配置
    spoken_text = sanitize_tts_text(text)
    if not spoken_text:
        # 若清洗后无有效可朗读文字（例如全是颜文字），直接降级走文字发送
        if is_private_target:
            return await send_private_msg_impl(user_id, text, cross_channel=cross_channel)
        return await send_group_msg_impl(group_id, text, cross_channel=cross_channel)

    emo_key, emo_profile = resolve_emotion_profile(emotion)
    target_desc = f"私聊 {user_id}" if is_private_target else f"群 {group_id}"
    log_mcp_tool(
        "send_voice_msg",
        f"{target_desc} | 情感={emo_key}({emo_profile['label']}) | 台词: {spoken_text[:45]}"
    )

    # 4. 标记本轮已触发语音合成调用（供父进程将单轮超时/会话保活寿命延长至 20 分钟），再调用 GPT-SoVITS
    voice_marker_file = os.environ.get("AGY_VOICE_MARKER_FILE", "").strip()
    if voice_marker_file:
        try:
            with open(voice_marker_file, "w", encoding="utf-8") as mf:
                mf.write("1")
        except Exception:
            pass

    t0 = time.time()
    wav_bytes, err_msg = await _synthesize_speech_wav(spoken_text, emo_profile)
    elapsed = round(time.time() - t0, 2)

    # 5. 失败/未开机 -> 自动走方案 A 降级为普通文字气泡回复
    if not wav_bytes:
        safe_log(f"⚠️ [TTS 自动降级] {err_msg} -> 自动切换为文字消息发送: {text[:40]}")
        log_mcp_tool("send_voice_msg_fallback", f"TTS不可用({err_msg})，自动降级为文字回复 -> {target_desc}")
        if is_private_target:
            fallback_resp = await send_private_msg_impl(user_id, text, cross_channel=cross_channel)
        else:
            fallback_resp = await send_group_msg_impl(group_id, text, cross_channel=cross_channel)
        return (
            f"【提示：主力机 TTS 语音服务当前未开启或不可达（{err_msg}），"
            f"已自动为你将该内容作为普通文字消息成功发出，无需再次调用发信工具】\n"
            f"文字发送回执: {fallback_resp}"
        )

    # 6. 封装 Base64 语音 CQ 码并发送至 OneBot
    try:
        b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
        cq_record = f"[CQ:record,file=base64://{b64_audio}]"

        if is_private_target:
            resp = await call_onebot_api_raw(
                "/send_private_msg",
                {"user_id": user_id, "message": cq_record},
                timeout=30.0
            )
            priv_key = f"private_{user_id}"
            archive_text = f"[语音({emo_profile['label']}): {spoken_text}]"
            record_bot_spoke_persistent(priv_key, archive_text)
            record_chat_archive(priv_key, BOT_QQ, "弥酱", archive_text, is_bot=True)
        else:
            resp = await call_onebot_api_raw(
                "/send_group_msg",
                {"group_id": group_id, "message": cq_record},
                timeout=30.0
            )
            archive_text = f"[语音({emo_profile['label']}): {spoken_text}]"
            record_bot_spoke_persistent(str(group_id), archive_text)
            record_chat_archive(f"group_{group_id}", BOT_QQ, "弥酱", archive_text, is_bot=True)

        safe_log(f"🎙️ [语音已送达] {target_desc} | 情感={emo_key} | 耗时={elapsed}s | 大小={len(wav_bytes)//1024}KB")
        return f"语音发送成功 (情感={emo_key}, 合成耗时={elapsed}s): {resp}"
    except Exception as e:
        safe_log(f"⚠️ [OneBot 语音投递异常] {e} -> 自动降级为文字发送")
        if is_private_target:
            return await send_private_msg_impl(user_id, text, cross_channel=cross_channel)
        return await send_group_msg_impl(group_id, text, cross_channel=cross_channel)
