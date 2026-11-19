"""
webui/server.py — WebUI 独立服务与高并发双引擎调度器
支持 FastAPI + Uvicorn + WebSocket 全双工实时流推送；
并在无第三方依赖环境下自动平滑降级至原生 ThreadingHTTPServer。
"""
import os
import sys
import json
import time
import asyncio
import mimetypes
import argparse
from urllib.parse import urlparse, parse_qs

# 解决 Windows 控制台编码问题
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 引入 WebUI 专属配置与各解耦 API 模块
from webui.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    STATIC_DIR,
    TEMPLATE_DIR
)
from webui.api.status_api import get_system_status
from webui.api.mood_api import get_mood_details
from webui.api.affection_api import get_affection_data, manage_affection_api
from webui.api.memory_api import (
    get_permanent_facts,
    simulate_fts_search,
    get_mid_term_memories,
    delete_permanent_fact_api,
    delete_mid_term_memory_api
)
from webui.api.logs_api import get_recent_logs
from webui.api.graph_api import get_mind_graph_data
from webui.api.whitelist_api import (
    get_whitelist_overview,
    manage_group_whitelist_api,
    manage_bot_whitelist_api
)
from webui.api.qzone_care_api import (
    get_qzone_care_overview,
    manage_qzone_special_care_api
)

# 检测是否可用高性能 ASGI 依赖 (FastAPI + Uvicorn)
HAS_FASTAPI = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


# =========================================================================
# 引擎 1：FastAPI + Uvicorn + WebSocket 极速全双工推送引擎
# =========================================================================
def create_fastapi_app() -> "FastAPI":
    app = FastAPI(
        title="星见弥认知与情绪可视化仪表盘 API",
        version="2.0.0",
        description="支持实时 WebSocket 全双工状态流与 REST 查询"
    )

    # 动态静态文件处理器 (替代易发生 Linux 权限 401 拦截的 StaticFiles)
    @app.get("/static/{file_path:path}")
    async def serve_static(file_path: str):
        clean_path = file_path.lstrip("/\\")
        abs_file = os.path.abspath(os.path.join(STATIC_DIR, clean_path))
        abs_static_dir = os.path.abspath(STATIC_DIR)

        # 路径穿越防范
        if not abs_file.startswith(abs_static_dir):
            return Response(content="Access Denied", status_code=403, media_type="text/plain")

        if not os.path.exists(abs_file) or not os.path.isfile(abs_file):
            return Response(content=f"File Not Found: {file_path}", status_code=404, media_type="text/plain")

        mime_type, _ = mimetypes.guess_type(abs_file)
        if abs_file.endswith(".css"):
            mime_type = "text/css"
        elif abs_file.endswith(".js"):
            mime_type = "application/javascript"

        try:
            with open(abs_file, "rb") as f:
                content = f.read()
            return Response(
                content=content,
                media_type=mime_type or "application/octet-stream",
                headers={"Cache-Control": "public, max-age=3600"}
            )
        except Exception as e:
            return Response(content=str(e), status_code=500, media_type="text/plain")

    # 活跃 WebSocket 连接池
    active_websockets: list[WebSocket] = []

    @app.get("/", response_class=HTMLResponse)
    async def index_page():
        index_file = os.path.join(TEMPLATE_DIR, "index.html")
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())

    @app.get("/api/status")
    async def api_status():
        return get_system_status()

    @app.get("/api/mood")
    async def api_mood():
        return get_mood_details()

    @app.get("/api/affection")
    async def api_affection():
        return get_affection_data()

    @app.get("/api/affection/manage")
    @app.post("/api/affection/manage")
    async def api_affection_manage(
        action: str = "delta",
        user_id: str = "",
        value: float = 0.0,
        reason: str = "",
    ):
        return manage_affection_api(action=action, user_id=user_id, value=value, reason=reason)

    @app.get("/api/memory/facts")
    async def api_facts(category: str = "all", status: str = "all", query: str = "", limit: int = 100):
        return get_permanent_facts(category, status, query, limit)

    @app.get("/api/memory/facts/delete")
    @app.post("/api/memory/facts/delete")
    async def api_delete_fact(id: int = 0):
        return delete_permanent_fact_api(id)

    @app.delete("/api/memory/facts/{fact_id}")
    async def api_delete_fact_rest(fact_id: int):
        return delete_permanent_fact_api(fact_id)

    @app.get("/api/memory/search")
    async def api_search(q: str = "", target_id: str = "auto"):
        return simulate_fts_search(q, target_id)

    @app.get("/api/memory/mid_term")
    async def api_mid_term():
        return get_mid_term_memories()

    @app.get("/api/memory/mid_term/delete")
    @app.post("/api/memory/mid_term/delete")
    async def api_delete_mid_term(id: str = "", text: str = ""):
        return delete_mid_term_memory_api(memory_id=id, text=text)

    @app.delete("/api/memory/mid_term/{memory_id}")
    async def api_delete_mid_term_rest(memory_id: str):
        return delete_mid_term_memory_api(memory_id=memory_id)

    @app.get("/api/logs")
    async def api_logs(type: str = "all", lines: int = 80):
        return get_recent_logs(type, lines)

    @app.get("/api/graph")
    async def api_graph():
        return get_mind_graph_data()

    @app.get("/api/whitelist")
    async def api_whitelist():
        return get_whitelist_overview()

    @app.get("/api/whitelist/group")
    @app.post("/api/whitelist/group")
    async def api_whitelist_group(action: str = "toggle", group_id: str = ""):
        return manage_group_whitelist_api(action, group_id)

    @app.get("/api/whitelist/bot")
    @app.post("/api/whitelist/bot")
    async def api_whitelist_bot(action: str = "add", target: str = "", name: str = ""):
        return manage_bot_whitelist_api(action, target, name)

    @app.get("/api/qzone/special_care")
    async def api_qzone_care():
        return get_qzone_care_overview()

    @app.get("/api/qzone/special_care/manage")
    @app.post("/api/qzone/special_care/manage")
    async def api_qzone_care_manage(
        action: str = "add",
        user_id: str = "",
        nickname: str = "",
        reason: str = "",
    ):
        return manage_qzone_special_care_api(action, user_id, nickname, reason)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        active_websockets.append(ws)
        try:
            # 建立时立即下发当前全景状态
            init_status = get_system_status()
            init_mood = get_mood_details()
            await ws.send_text(json.dumps({
                "type": "state_push",
                "status": init_status,
                "mood": init_mood
            }, ensure_ascii=False))

            while True:
                # 每 2.5 秒主动向前端推送最新衰减情绪与状态
                await asyncio.sleep(2.5)
                push_status = get_system_status()
                push_mood = get_mood_details()
                await ws.send_text(json.dumps({
                    "type": "state_push",
                    "status": push_status,
                    "mood": push_mood
                }, ensure_ascii=False))
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            if ws in active_websockets:
                active_websockets.remove(ws)

    return app


# =========================================================================
# 引擎 2：原生 ThreadingHTTPServer 零依赖平滑兜底引擎
# =========================================================================
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

class NativeWebUIHandler(BaseHTTPRequestHandler):
    server_version = "HoshimiWebUI/1.0"

    def log_message(self, format, *args):
        if self.path.startswith("/api/status") or self.path.startswith("/api/mood"):
            return
        super().log_message(format, *args)

    def _send_json_response(self, data: dict, status_code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_POST(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            index_path = os.path.join(TEMPLATE_DIR, "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "Index template not found")
            return

        if path.startswith("/static/"):
            rel_path = path[len("/static/"):].lstrip("/\\")
            file_path = os.path.join(STATIC_DIR, rel_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                mime_type, _ = mimetypes.guess_type(file_path)
                with open(file_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime_type or "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "File not found")
            return

        try:
            if path == "/api/status":
                self._send_json_response(get_system_status())
                return
            if path == "/api/mood":
                self._send_json_response(get_mood_details())
                return
            if path == "/api/affection":
                self._send_json_response(get_affection_data())
                return
            if path == "/api/affection/manage":
                act = query.get("action", ["delta"])[0]
                uid = query.get("user_id", [""])[0]
                val = float(query.get("value", [0.0])[0])
                reason = query.get("reason", [""])[0]
                self._send_json_response(manage_affection_api(act, uid, val, reason))
                return
            if path == "/api/memory/facts":
                cat = query.get("category", ["all"])[0]
                st = query.get("status", ["all"])[0]
                q = query.get("query", [""])[0]
                limit = int(query.get("limit", [100])[0])
                self._send_json_response(get_permanent_facts(cat, st, q, limit))
                return
            if path == "/api/memory/facts/delete":
                fid = int(query.get("id", [0])[0])
                self._send_json_response(delete_permanent_fact_api(fid))
                return
            if path == "/api/memory/search":
                q = query.get("q", [""])[0]
                target_id = query.get("target_id", ["auto"])[0]
                self._send_json_response(simulate_fts_search(q, target_id))
                return
            if path == "/api/memory/mid_term":
                self._send_json_response(get_mid_term_memories())
                return
            if path == "/api/memory/mid_term/delete":
                mid = query.get("id", [""])[0]
                txt = query.get("text", [""])[0]
                self._send_json_response(delete_mid_term_memory_api(memory_id=mid, text=txt))
                return
            if path == "/api/logs":
                log_type = query.get("type", ["all"])[0]
                lines = int(query.get("lines", [80])[0])
                self._send_json_response(get_recent_logs(log_type, lines))
                return
            if path == "/api/graph":
                self._send_json_response(get_mind_graph_data())
                return
            if path == "/api/whitelist":
                self._send_json_response(get_whitelist_overview())
                return
            if path == "/api/whitelist/group":
                act = query.get("action", ["toggle"])[0]
                gid = query.get("group_id", [""])[0]
                self._send_json_response(manage_group_whitelist_api(act, gid))
                return
            if path == "/api/whitelist/bot":
                act = query.get("action", ["add"])[0]
                target = query.get("target", [""])[0]
                name = query.get("name", [""])[0]
                self._send_json_response(manage_bot_whitelist_api(act, target, name))
                return
            if path == "/api/qzone/special_care":
                self._send_json_response(get_qzone_care_overview())
                return
            if path == "/api/qzone/special_care/manage":
                act = query.get("action", ["add"])[0]
                uid = query.get("user_id", [""])[0]
                nick = query.get("nickname", [""])[0]
                reason = query.get("reason", [""])[0]
                self._send_json_response(manage_qzone_special_care_api(act, uid, nick, reason))
                return

            self.send_error(404, "API Endpoint Not Found")
        except Exception as e:
            self._send_json_response({"success": False, "error": str(e)}, status_code=500)


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    engine_name = "FastAPI + Uvicorn (ASGI / WebSocket 全双工流)" if HAS_FASTAPI else "Python 原生 ThreadingHTTPServer"

    print("\n" + "="*65)
    print(" 🌟 星见弥 (Hoshimi Neko) 认知与情绪可视化仪表盘")
    print("="*65)
    print(f" 🚀 驱动引擎   : {engine_name}")
    print(f" 🌐 监听地址   : http://{host}:{port}")
    print(f" 💻 本地访问   : http://127.0.0.1:{port}")
    print(f" 🔒 Tailscale  : http://YOUR_EDGE_NODE_IP:{port} (笔记本/手机直连)")
    if HAS_FASTAPI:
        print(f" 📖 交互式文档 : http://127.0.0.1:{port}/docs")
    print("="*65 + "\n")

    if HAS_FASTAPI:
        app = create_fastapi_app()
        uvicorn.run(app, host=host, port=port, log_level="warning")
    else:
        server_address = (host, port)
        httpd = ThreadingHTTPServer(server_address, NativeWebUIHandler)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="星见弥 WebUI 服务端")
    parser.add_argument("--host", default=DEFAULT_HOST, help="绑定 IP，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="绑定端口，默认 8088")
    args = parser.parse_args()

    run_server(args.host, args.port)
