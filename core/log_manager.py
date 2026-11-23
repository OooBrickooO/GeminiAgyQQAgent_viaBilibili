"""
core/log_manager.py — 弥酱多通道流式日志分流与审计中心

职责：
1. 统一拦截 sys.stdout / sys.stderr，实现零阻塞双写分流：
   - 原样透传至系统标准输出 (journalctl -u qqagent)，保障 100% 原始混合流不受任何影响；
   - 实时分类沉淀至本地 logs/ 目录，支持独立按业务领域单独查看：
     * logs/all.log       - 全量混合流 (一起看)
     * logs/chat.log      - 聊天与群交互流 (消息收发、加压、防抖、发信)
     * logs/thought.log   - 深度思维心流 (弥酱思考脉搏、耗时、模型启停)
     * logs/bili.log      - B站冲浪生态 (视频摸鱼、打分、手账、安利)
     * logs/tool.log      - MCP工具动作执行 (发信、发图、点赞、评论等实际动作)
     * logs/error.log     - 告警与异常 (错误、超时、风控拦截)
     * logs/deskpet.log   - 桌宠专属日志（不进入滚动控制台与 all.log）
2. 自动单文件容量滚动保护 (默认单文件上限 5MB)，防止占满硬盘空间。
"""
import os
import sys
import re
import datetime
from config import BASE_DIR, deskpet_log, is_deskpet_log_line

LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

MAX_LOG_SIZE = 5 * 1024 * 1024  # 5MB 自动回滚轮转

PATTERNS = {
    "chat": re.compile(r'(📩|📥|📦|💬|🐾|加压|压力|群\[|收到私聊|收到群|防抖|唤醒|搭腔|静默|撤回|消息合并|用户静默)'),
    "thought": re.compile(r'(💭|🧠|⚡.*大模型锁|✅.*总耗时|思考完毕|思考超时|思维心流|大模型推理|思考被打断)'),
    "bili": re.compile(r'(🏄|🎬|⭐|📬.*安利|💡.*话题回响|B站|bili|手账|冲浪|巡检收件箱)'),
    "tool": re.compile(r'(⚙️|🔧|🎙️|MCP|send_group_msg|send_private_msg|send_voice_msg|send_meme|send_local_image|poke_user|inspect_image|leisure_action|affection_action|search_memory|memory_action|manage_group_ban|query_contacts|query_community_trends|manage_meme_vault|qzone_action|play_board_game|bili_video|bili_social|bili_inbox|bili_auth)'),
    "error": re.compile(r'(⚠️|❌|🛑|🚨|error|Exception|Traceback|failed|TimeoutError|Prohibited|拒收|违规)', re.IGNORECASE)
}


class CategorizedStreamTee:
    """标准流拦截器，既完整透传原流，又根据内容特征异步分流写入独立日志文件"""

    def __init__(self, original_stream, is_stderr=False):
        self.original_stream = original_stream
        self.is_stderr = is_stderr
        self.buffer = ""

    def write(self, data):
        # 按行缓冲分流：DeskPet 专属行仅落盘 logs/deskpet.log，其余行保持 100% 透传原流与分类写入
        try:
            self.buffer += str(data)
            if "\n" in self.buffer:
                lines = self.buffer.split("\n")
                self.buffer = lines[-1]
                for line in lines[:-1]:
                    self._handle_line(line)
        except Exception:
            pass
        return len(data) if isinstance(data, str) else 0

    def _handle_line(self, line: str):
        if is_deskpet_log_line(line):
            deskpet_log(line)
            if PATTERNS["error"].search(line):
                clean = line.strip()
                if clean:
                    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._write_to_file("error.log", f"[{now_str}] {clean}")
            return

        try:
            self.original_stream.write(line + "\n")
            self.original_stream.flush()
        except Exception:
            pass

        self._dispatch_line(line)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.original_stream, name)

    def _write_to_file(self, filename: str, line: str):
        filepath = os.path.join(LOGS_DIR, filename)
        try:
            # 轮转保护：超过 5MB 归档重命名
            if os.path.exists(filepath) and os.path.getsize(filepath) > MAX_LOG_SIZE:
                bak_path = f"{filepath}.old"
                if os.path.exists(bak_path):
                    try:
                        os.remove(bak_path)
                    except Exception:
                        pass
                try:
                    os.rename(filepath, bak_path)
                except Exception:
                    pass

            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"{line}\n")
        except Exception:
            pass

    def _dispatch_line(self, line: str):
        clean_line = line.strip()
        if not clean_line:
            return

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"[{now_str}] {clean_line}"

        # 必写 all.log
        self._write_to_file("all.log", formatted)

        # stderr 自动归入 error.log
        if self.is_stderr and any(kw in clean_line.lower() for kw in ("error", "traceback", "exception", "failed", "warning")):
            self._write_to_file("error.log", formatted)

        # 按正则分流写入特定文件
        for cat, pat in PATTERNS.items():
            if pat.search(clean_line):
                self._write_to_file(f"{cat}.log", formatted)


def setup_categorized_logging():
    """初始化并挂载日志分类分流器"""
    if not isinstance(sys.stdout, CategorizedStreamTee):
        sys.stdout = CategorizedStreamTee(sys.stdout, is_stderr=False)
    if not isinstance(sys.stderr, CategorizedStreamTee):
        sys.stderr = CategorizedStreamTee(sys.stderr, is_stderr=True)

