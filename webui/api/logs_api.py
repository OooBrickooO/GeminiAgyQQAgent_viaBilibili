"""
webui/api/logs_api.py — 弥酱多通道流式日志审计 API
"""
import os
from webui.config import BASE_DIR

LOGS_DIR = os.path.join(BASE_DIR, "logs")

VALID_LOG_TYPES = {
    "all": "all.log",
    "chat": "chat.log",
    "thought": "thought.log",
    "tool": "tool.log",
    "bili": "bili.log",
    "error": "error.log"
}


def get_recent_logs(log_type: str = "all", max_lines: int = 80) -> dict:
    filename = VALID_LOG_TYPES.get(log_type, "all.log")
    filepath = os.path.join(LOGS_DIR, filename)

    if not os.path.exists(filepath):
        return {
            "success": True,
            "type": log_type,
            "file": filename,
            "exists": False,
            "lines": [f"【日志暂未生成】等待对应事件触发写入 ({filename})..."]
        }

    try:
        max_lines = min(200, max(10, int(max_lines)))
        # 读取末尾指定行数
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            recent_lines = [l.rstrip("\r\n") for l in all_lines[-max_lines:]]

        return {
            "success": True,
            "type": log_type,
            "file": filename,
            "exists": True,
            "total_lines": len(all_lines),
            "lines": recent_lines
        }
    except Exception as e:
        return {
            "success": False,
            "type": log_type,
            "file": filename,
            "error": str(e),
            "lines": [f"读取日志异常: {e}"]
        }

