"""
config.py — 弥酱 QQ Agent 全局配置中心

统一管理项目基准路径、身份认证、网络代理隔离、数据文件路径与审计日志。
所有其他模块统一从此处 import 常量与工具函数，严禁任何硬编码路径。
"""
import os
import uuid
import json
import sys
import re
import datetime

# ============================================================
# 1. 动态基准路径 (铁律 1：严禁盘符或绝对路径硬编码)
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ALLOWED_GROUPS_FILE = os.path.join(BASE_DIR, "allowed_groups.json")
BOT_WHITELIST_FILE = os.path.join(BASE_DIR, "bot_whitelist.json")
PROFILES_FILE = os.path.join(BASE_DIR, "user_profiles.json")
IMAGE_VAULT_FILE = os.path.join(BASE_DIR, "image_vault.json")
MEMES_FILE = os.path.join(BASE_DIR, "memes.json")
IMAGE_CACHE_DIR = os.path.join(BASE_DIR, "image_cache")
MEMES_DIR = os.path.join(BASE_DIR, "memes")
QZONE_HISTORY_FILE = os.path.join(BASE_DIR, "qzone_history.json")
SEEN_FRIEND_FEEDS_FILE = os.path.join(BASE_DIR, "seen_friend_feeds.json")
QZONE_SPECIAL_CARE_FILE = os.path.join(BASE_DIR, "qzone_special_care.json")
QZONE_OBSERVER_STATE_FILE = os.path.join(BASE_DIR, "qzone_observer_state.json")
CHAT_STATE_FILE = os.path.join(BASE_DIR, "chat_state.json")
MCP_LOG_FILE = os.path.join(BASE_DIR, "mcp_activity.log")
DESKPET_LOG_FILE = os.path.join(BASE_DIR, "logs", "deskpet.log")
DESKPET_LINE_RE = re.compile(r'(DeskPet|desktop_master|桌宠|>> pet_|\[deskpet)')
MUTED_USERS_FILE = os.path.join(BASE_DIR, "muted_users.json")

# B站智能体相关配置路径
BILI_CREDENTIAL_FILE = os.path.join(BASE_DIR, "bili_credential.json")
BILI_CACHE_FILE = os.path.join(BASE_DIR, "today_bili_feed.json")
BILI_SEEN_FILE = os.path.join(BASE_DIR, "seen_bili_videos.json")
BILI_NOTES_DIR = os.path.join(BASE_DIR, "bili_notes")
BILI_INBOX_STATE_FILE = os.path.join(BASE_DIR, "bili_inbox_state.json")
BILI_DYNAMIC_HISTORY_FILE = os.path.join(BASE_DIR, "bili_dynamic_history.json")
BILI_COMMENT_HISTORY_FILE = os.path.join(BASE_DIR, "bili_comment_history.json")

# 情绪状态机与三层记忆系统配置路径
MOOD_STATE_FILE = os.path.join(BASE_DIR, "mood_state.json")
LIBIDO_STATE_FILE = os.path.join(BASE_DIR, "libido_state.json")
MID_TERM_MEMORY_FILE = os.path.join(BASE_DIR, "mid_term_memory.json")
LONG_TERM_DB_FILE = os.path.join(BASE_DIR, "long_term_memory.db")
MEMORY_ARCHIVE_DIR = os.path.join(BASE_DIR, "memory_archive")

# 娱乐玩具与互动小游戏配置路径
OMIKUJI_DAILY_FILE = os.path.join(BASE_DIR, "omikuji_daily.json")
BOARD_GAMES_STATE_FILE = os.path.join(BASE_DIR, "board_games_state.json")
GAME_RENDERS_DIR = os.path.join(IMAGE_CACHE_DIR, "games")

# 确保媒体与笔记目录存在
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
os.makedirs(GAME_RENDERS_DIR, exist_ok=True)
os.makedirs(MEMES_DIR, exist_ok=True)
os.makedirs(BILI_NOTES_DIR, exist_ok=True)
os.makedirs(MEMORY_ARCHIVE_DIR, exist_ok=True)

# ============================================================
# 2. 身份与认证常量 (请替换为你的真实 QQ 号与 NapCat Token，或通过环境变量注入)
# ============================================================
BOT_QQ = os.environ.get("BOT_QQ", "YOUR_BOT_QQ")
MASTER_QQ = os.environ.get("MASTER_QQ", "YOUR_MASTER_QQ")
NAPCAT_TOKEN = os.environ.get("NAPCAT_TOKEN", "YOUR_NAPCAT_TOKEN")
BOT_NAMESPACE = uuid.UUID("f5a8b792-c12e-4b3f-9a1d-8e7c6d5b4a3f")

# ============================================================
# 3. 端口与通信 URL (请替换为你的 OneBot 网关 IP / 本地地址)
# ============================================================
ONEBOT_GATEWAY_IP = os.environ.get("ONEBOT_GATEWAY_IP", "YOUR_ONEBOT_SERVER_IP")
ONEBOT_HTTP_URL = os.environ.get("ONEBOT_HTTP_URL", f"http://{ONEBOT_GATEWAY_IP}:8300")
ONEBOT_WS_URL = os.environ.get("ONEBOT_WS_URL", f"ws://{ONEBOT_GATEWAY_IP}:8301/?access_token={NAPCAT_TOKEN}")

# 本地常驻守护进程异步气泡投递端点 (1C-1：MCP 发完首条/提交队列后立即返回，由 bot_daemon 异步连发)
INTERNAL_BUBBLE_PORT = 18302
INTERNAL_BUBBLE_URL = f"http://127.0.0.1:{INTERNAL_BUBBLE_PORT}/enqueue_bubbles"

# GPT-SoVITS-v5 本地/跨机语音合成服务配置 (远端算力主机 IP / 本机 127.0.0.1)
TTS_PC_TAILSCALE_IP = os.environ.get("TTS_PC_TAILSCALE_IP", "YOUR_TTS_SERVER_IP")
TTS_API_PORT = 9880
TTS_API_CANDIDATES = (
    f"http://{TTS_PC_TAILSCALE_IP}:{TTS_API_PORT}",
    f"http://127.0.0.1:{TTS_API_PORT}",
)

# 星见弥桌宠与反作弊安全屏幕感知/CUA 客户端配置 (DeskPet, 远端客户端 IP / 本机 127.0.0.1)
DESKPET_PC_TAILSCALE_IP = os.environ.get("DESKPET_PC_TAILSCALE_IP", TTS_PC_TAILSCALE_IP)
DESKPET_API_PORT = 7797
DESKPET_API_CANDIDATES = (
    f"http://127.0.0.1:{DESKPET_API_PORT}",
    f"http://{DESKPET_PC_TAILSCALE_IP}:{DESKPET_API_PORT}",
)
DESKPET_CACHE_DIR = os.path.join(IMAGE_CACHE_DIR, "deskpet")
DESKPET_STATE_FILE = os.path.join(BASE_DIR, "deskpet_state.json")
os.makedirs(DESKPET_CACHE_DIR, exist_ok=True)

# agy 可执行文件路径（优先当前用户路径，自动兼容系统路径）
AGY_EXECUTABLE = os.environ.get("AGY_EXECUTABLE", os.path.expanduser("~/.local/bin/agy"))
if not os.path.exists(AGY_EXECUTABLE):
    _cand = os.path.expanduser("~/.local/bin/agy")
    AGY_EXECUTABLE = _cand if os.path.exists(_cand) else ("/usr/local/bin/agy" if os.path.exists("/usr/local/bin/agy") else "agy")

AGY_MODEL_FAMILY = "gemini-4-hydrogen"
DEFAULT_AGY_MODEL = f"{AGY_MODEL_FAMILY}-low"

# ============================================================
# 3.1. 各通道分级思考深度 (Effort 分级控制，严禁使用 xhigh/Extra)
# ============================================================
CHAT_AGY_EFFORT = "low"        # 前台主对话默认极速深度 (gemini-4-hydrogen-low)，确保消息与桌宠吐槽秒级响应
BILI_AGY_EFFORT = "medium"     # B站专属摸鱼冲浪（单视频精看结构化手账评分与热梗提炼，medium 兼顾精度与额度）
ROUTER_AGY_EFFORT = "low"      # 话前辅助分类参考
OBSERVER_AGY_EFFORT = "low"    # 后台事后内省观察者（好感共鸣/情绪跃迁/三层记忆抽取与核销）
BACKGROUND_AGY_EFFORT = "low"  # 每日记忆总结等夜间批处理任务

DEFAULT_AGY_EFFORT = CHAT_AGY_EFFORT  # 默认兜底兼容 (low)
ALLOWED_AGY_EFFORTS = ("low", "medium", "high")


def normalize_agy_effort(effort: str = None, allow_low: bool = True) -> str:
    """规范化思考深度：仅允许 low/medium/high，严禁使用 xhigh/Extra"""
    eff = str(effort or DEFAULT_AGY_EFFORT).strip().lower()
    if eff not in ALLOWED_AGY_EFFORTS:
        eff = "high"
    if not allow_low and eff == "low":
        eff = "medium"
    return eff


def build_agy_model_flags(
    effort: str = None,
    model: str = None,
    drop_effort: bool = False,
    allow_low: bool = True
) -> tuple[list[str], str, str]:
    """
    统一构造 agy CLI 的 --model 与 --effort 参数：
    - 若目标模型属于 gemini-4-hydrogen 系列（或已带 -low/-medium/-high 后缀），
      直接映射为 gemini-4-hydrogen-{low|medium|high} 且不重复传 --effort，防止 CLI 报无效参数；
    - 否则按标准 --model + --effort 拼接。
    返回: (cli_flags, resolved_model, resolved_effort)
    """
    eff = normalize_agy_effort(effort, allow_low=allow_low)
    base_model = str(model or DEFAULT_AGY_MODEL).strip()
    if not base_model:
        return ([f"--effort={eff}"] if not drop_effort else []), "", eff

    if base_model.startswith("gemini-4-hydrogen"):
        resolved_model = f"gemini-4-hydrogen-{eff}"
        return [f"--model={resolved_model}"], resolved_model, eff

    flags = [f"--model={base_model}"]
    if not drop_effort and not any(base_model.endswith(s) for s in ("-low", "-medium", "-high", "-xhigh")):
        flags.append(f"--effort={eff}")
    return flags, base_model, eff

# ============================================================
# 4. 网络代理隔离 (铁律 2)
# ============================================================
def sanitize_process_env():
    """清空当前 Python 主进程的代理环境变量，防止 WS/HTTP 本地连接被 Clash 拦截"""
    for key in ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def get_agy_env() -> dict:
    """生成传递给 agy 子进程的独立环境变量 (让大模型能翻墙访问 Google API)"""
    sub_env = os.environ.copy()
    sub_env["HTTP_PROXY"] = "http://127.0.0.1:7890"
    sub_env["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    # 让 agy 对本地 QQ API (8300)、TTS 主力机 (9880) 及云端内网直连，不走代理
    no_proxy = f"127.0.0.1,localhost,172.17.0.2,172.17.0.1,172.18.0.1,{ONEBOT_GATEWAY_IP},{TTS_PC_TAILSCALE_IP},{DESKPET_PC_TAILSCALE_IP},YOUR_EDGE_NODE_IP"
    sub_env["NO_PROXY"] = no_proxy
    sub_env["no_proxy"] = no_proxy
    # Supervisor / 后台守护环境缺失补全
    if "HOME" not in sub_env:
        sub_env["HOME"] = os.path.expanduser("~")
    return sub_env


# ============================================================
# 5. 白名单读取辅助
# ============================================================
def get_allowed_groups() -> set:
    """从 allowed_groups.json 热读取当前活跃白名单群"""
    try:
        if os.path.exists(ALLOWED_GROUPS_FILE):
            with open(ALLOWED_GROUPS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def get_bot_whitelist_dict() -> dict:
    """
    从 bot_whitelist.json 热读取群 Bot 免压白名单字典。
    返回格式: {key_str: {"qq": str, "name": str, "added_at": str}}
    """
    try:
        if os.path.exists(BOT_WHITELIST_FILE):
            with open(BOT_WHITELIST_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, list):
                return {str(item).strip(): {"qq": str(item).strip(), "name": ""} for item in raw if str(item).strip()}
    except Exception:
        pass
    return {}


def get_bot_whitelist() -> set:
    """获取所有已登记在群 Bot 免压白名单中的 QQ 号集合"""
    data = get_bot_whitelist_dict()
    qqs = set()
    for k, v in data.items():
        if isinstance(v, dict) and v.get("qq"):
            qqs.add(str(v["qq"]).strip())
        elif str(k).isdigit():
            qqs.add(str(k).strip())
    return qqs


def is_bot_whitelisted(sender_id: str, nickname: str = "") -> bool:
    """
    判断指定群发言者是否属于主人指出的群 Bot 白名单（免计入群聊压力）。
    支持按 QQ 号精确匹配，或按预登记的 Bot 昵称匹配（首次命中昵称时自动回填绑定 QQ 号）。
    """
    uid_str = str(sender_id or "").strip()
    nick_str = str(nickname or "").strip()
    if not uid_str and not nick_str:
        return False

    data = get_bot_whitelist_dict()
    if not data:
        return False

    if uid_str and uid_str in data:
        return True

    for k, info in list(data.items()):
        if isinstance(info, dict):
            entry_qq = str(info.get("qq", "")).strip()
            entry_name = str(info.get("name", "")).strip()
            if uid_str and entry_qq and uid_str == entry_qq:
                return True
            if nick_str and entry_name and nick_str.lower() == entry_name.lower():
                # 若此前仅通过昵称登记，首次收到其发言时自动绑定真实 QQ 号落盘
                if uid_str and not entry_qq:
                    try:
                        data.pop(k, None)
                        info["qq"] = uid_str
                        data[uid_str] = info
                        with open(BOT_WHITELIST_FILE, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                return True
    return False


# ============================================================
# 6. MCP 工具审计日志与脑区/Transcript 路径兼容
# ============================================================
def _get_candidate_brain_roots() -> list:
    """收集宿主机上所有可能由 agy CLI 或 IDE 创建的 brain 目录路径（全用户兼容）"""
    base_homes = []
    for env_k in ("HOME", "USERPROFILE"):
        v = os.environ.get(env_k)
        if v and v not in base_homes:
            base_homes.append(v)
    cur_expand = os.path.expanduser("~")
    if cur_expand and cur_expand not in base_homes:
        base_homes.append(cur_expand)
    for fixed in ("/home/your_user", "/root"):
        if fixed not in base_homes:
            base_homes.append(fixed)

    candidates = []
    for h in base_homes:
        for app in ("antigravity", "antigravity-ide"):
            candidates.append(os.path.join(h, ".gemini", app, "brain"))
    return candidates


def get_brain_dir() -> str:
    """返回当前环境下的 Antigravity brain 根目录，自动兼容 Linux CLI 与 IDE（全用户候选探测）"""
    cand = _get_candidate_brain_roots()
    for c in cand:
        if os.path.isdir(c):
            return c
    return cand[0]


def get_transcript_path(dynamic_conv_id: str) -> str:
    """返回指定会话的 transcript.jsonl 路径，优先在所有潜在用户目录中探测已存在的文件"""
    cid_str = str(dynamic_conv_id)
    cand_roots = _get_candidate_brain_roots()
    for root in cand_roots:
        p = os.path.join(root, cid_str, ".system_generated", "logs", "transcript.jsonl")
        if os.path.exists(p):
            return p
    # 若尚未生成，以第一个存在的 brain 根路径或首位候选路径为基准
    primary_brain = get_brain_dir()
    return os.path.join(primary_brain, cid_str, ".system_generated", "logs", "transcript.jsonl")


def get_full_transcript_path(dynamic_conv_id: str) -> str:
    """返回指定会话的 transcript_full.jsonl 路径"""
    t_path = get_transcript_path(dynamic_conv_id)
    full_path = t_path.replace("transcript.jsonl", "transcript_full.jsonl")
    if os.path.exists(full_path):
        return full_path
    return t_path


def is_mcp_stdio_mode() -> bool:
    """检查当前是否运行在 stdio MCP 服务模式，严禁向 stdout 写入非 JSON-RPC 文本。"""
    return os.environ.get("QQ_MCP_STDIO_MODE", "").strip() == "1"


def is_deskpet_log_line(text: str) -> bool:
    """判断日志行是否属于 DeskPet 桌宠专属通道"""
    return bool(DESKPET_LINE_RE.search(str(text or "")))


def deskpet_log(*args, sep: str = " ") -> None:
    """桌宠专属日志：仅落盘 logs/deskpet.log（5MB 轮转为 .old），不进入滚动控制台与 all.log"""
    text = sep.join(str(a) for a in args).rstrip("\n")
    if not text.strip():
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(DESKPET_LOG_FILE), exist_ok=True)
        if os.path.exists(DESKPET_LOG_FILE) and os.path.getsize(DESKPET_LOG_FILE) > 5 * 1024 * 1024:
            bak = DESKPET_LOG_FILE + ".old"
            try:
                if os.path.exists(bak):
                    os.remove(bak)
                os.rename(DESKPET_LOG_FILE, bak)
            except Exception:
                pass
        with open(DESKPET_LOG_FILE, "a", encoding="utf-8") as f:
            for ln in text.split("\n"):
                if ln.strip():
                    f.write(f"[{now}] {ln.rstrip()}\n")
    except Exception:
        pass


def safe_log(*args, **kwargs):
    """stdio MCP 模式下将诊断日志自动路由至 stderr，避免污染 MCP JSON-RPC 管道；DeskPet 日志单独分流至 logs/deskpet.log。"""
    sep = kwargs.get("sep", " ")
    text = sep.join(str(a) for a in args)
    if "file" not in kwargs and is_deskpet_log_line(text):
        deskpet_log(text)
        return
    if "file" not in kwargs and is_mcp_stdio_mode():
        kwargs["file"] = sys.stderr
    print(*args, **kwargs)


def log_mcp_tool(tool_name: str, detail: str):
    """格式化打印并落盘 MCP 工具调用，让后台、守护进程与独立查看器实时清晰可见"""
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    full_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"🔧 [{now_str}] [MCP工具响应] >> {tool_name}: {detail}"
    if tool_name.startswith("pet_") or os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip() == "deskpet":
        deskpet_log(log_line)
        return
    try:
        sys.stderr.write(f"\n{log_line}\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        with open(MCP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{log_line}\n")
    except Exception:
        pass
    # 同步双写至 logs/tool.log，保障 log.py tool 独立查看器不受子进程管道影响实时可见
    try:
        tool_log_file = os.path.join(BASE_DIR, "logs", "tool.log")
        with open(tool_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{full_now}] {log_line}\n")
    except Exception:
        pass
