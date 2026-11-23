"""
webui/config.py — WebUI 专属网络与服务配置中心
"""
import os
import sys

# 保证根目录在 sys.path 中以便导入主项目组件
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import (
    BASE_DIR,
    BOT_QQ,
    MASTER_QQ,
    ALLOWED_GROUPS_FILE,
    MOOD_STATE_FILE,
    LIBIDO_STATE_FILE,
    MID_TERM_MEMORY_FILE,
    LONG_TERM_DB_FILE,
    PROFILES_FILE,
    CHAT_STATE_FILE
)

WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEBUI_DIR, "static")
TEMPLATE_DIR = os.path.join(WEBUI_DIR, "templates")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8088
DEFAULT_REFRESH_INTERVAL_SEC = 5

