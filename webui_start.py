#!/usr/bin/env python3
"""
webui_start.py — 星见弥 (Hoshimi Neko) WebUI 一键启动入口
可在 Windows 本地或 Linux 边缘服务器直接运行。
支持绑定 0.0.0.0 并通过 Tailscale 专网 IP (如 YOUR_EDGE_NODE_IP:8088) 进行访问。
"""
import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from webui.server import run_server, DEFAULT_HOST, DEFAULT_PORT
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="星见弥 WebUI 服务端启动器")
    parser.add_argument("--host", default=DEFAULT_HOST, help="绑定地址 (默认 0.0.0.0，允许 Tailscale/局域网访问)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="绑定端口 (默认 8088)")
    args = parser.parse_args()

    run_server(args.host, args.port)
