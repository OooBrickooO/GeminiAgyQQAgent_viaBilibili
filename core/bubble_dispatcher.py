"""
core/bubble_dispatcher.py — 常驻守护进程异步气泡连发调度器 (1C-1)

职责：
- 在 bot_daemon 进程内启动轻量本机 HTTP 服务 (127.0.0.1:18302)，接收来自 MCP 工具进程的多气泡发送任务；
- 按会话通道 (`group_{id}` / `private_{id}`) 严格 FIFO 串行调度气泡，气泡 1 发送完毕后立即回执 HTTP 200 释放 MCP 与 agy；
- 气泡 2~N 留在常驻守护进程后台按拟真打字时延依次吐出，不因 agy 子进程退出而丢失后续气泡。
"""
import json
import time
import uuid
import asyncio
from config import INTERNAL_BUBBLE_PORT, NAPCAT_TOKEN
from core.napcat_client import send_group_msg_raw, send_private_msg_raw

CHANNEL_QUEUES: dict[str, asyncio.Queue] = {}
CHANNEL_WORKERS: dict[str, asyncio.Task] = {}
_DISPATCHER_SERVER = None

# --- DeskPet 反向长轮询命令桥 (穿透 Windows 防火墙，无需放行入站 7797 端口即可双向通信) ---
DESKPET_LAST_STATE: dict = {}
DESKPET_LAST_SEEN: float = 0.0
DESKPET_PENDING_CMDS: list[dict] = []
DESKPET_CMD_WAITERS: dict[str, dict] = {}
DESKPET_POLL_WAKE: asyncio.Event | None = None


def _get_poll_wake() -> asyncio.Event:
    global DESKPET_POLL_WAKE
    if DESKPET_POLL_WAKE is None:
        DESKPET_POLL_WAKE = asyncio.Event()
    return DESKPET_POLL_WAKE


async def enqueue_deskpet_proxy_cmd(method: str, path: str, payload: dict = None, timeout: float = 20.0) -> dict:
    """将发往 DeskPet 的 /api/mcp/* 请求放入反向轮询队列，等待 Windows DeskPet 客户端拉取并回传执行结果"""
    global DESKPET_LAST_STATE, DESKPET_LAST_SEEN
    now = time.time()
    if now - DESKPET_LAST_SEEN > 18.0:
        return {"ok": False, "offline": True, "error": "Windows 桌宠客户端 (DeskPet) 当前未连接反向通道"}

    if method.upper() == "GET" and path == "/api/mcp/state" and DESKPET_LAST_STATE:
        return DESKPET_LAST_STATE

    cmd_id = uuid.uuid4().hex
    evt = asyncio.Event()
    waiter = {"event": evt, "result": None}
    DESKPET_CMD_WAITERS[cmd_id] = waiter
    DESKPET_PENDING_CMDS.append({
        "cmd_id": cmd_id,
        "method": method.upper(),
        "path": path,
        "payload": payload or {},
    })
    _get_poll_wake().set()

    try:
        await asyncio.wait_for(evt.wait(), timeout=max(1.5, float(timeout)))
        res = waiter.get("result")
        if isinstance(res, dict):
            return res
        return {"ok": False, "error": "empty_proxy_result"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"等待桌宠客户端响应超时 ({timeout}s)"}
    finally:
        DESKPET_CMD_WAITERS.pop(cmd_id, None)


async def _channel_worker(chan_key: str):
    """单通道严格 FIFO 气泡发送协程"""
    queue = CHANNEL_QUEUES[chan_key]
    while True:
        item = await queue.get()
        try:
            kind, target_id, text, delay, done_evt = item
            if delay and delay > 0:
                await asyncio.sleep(float(delay))
            if kind == "group":
                await send_group_msg_raw(int(target_id), text)
            else:
                await send_private_msg_raw(int(target_id), text)
            if done_evt is not None:
                done_evt.set()
        except Exception as e:
            print(f"⚠️ [气泡队列发送异常] {chan_key}: {e}")
            if item and len(item) >= 5 and item[4] is not None and not item[4].is_set():
                item[4].set()
        finally:
            queue.task_done()


def _ensure_channel_worker(chan_key: str) -> asyncio.Queue:
    if chan_key not in CHANNEL_QUEUES:
        CHANNEL_QUEUES[chan_key] = asyncio.Queue()
    task = CHANNEL_WORKERS.get(chan_key)
    if task is None or task.done():
        CHANNEL_WORKERS[chan_key] = asyncio.create_task(_channel_worker(chan_key))
    return CHANNEL_QUEUES[chan_key]


async def enqueue_bubbles_and_wait_first(
    kind: str,
    target_id: int,
    bubbles: list[str],
    delays: list[float]
) -> str:
    """
    将气泡序列投递到指定通道队列，等待第 1 条气泡发出后立即返回，剩余气泡在后台依次发出。
    """
    if not bubbles:
        return "无待发气泡"

    chan_key = f"{kind}_{int(target_id)}"
    queue = _ensure_channel_worker(chan_key)

    first_done = asyncio.Event()
    await queue.put((kind, int(target_id), bubbles[0], 0.0, first_done))

    for idx, b in enumerate(bubbles[1:], start=1):
        d = float(delays[idx]) if idx < len(delays) else 1.0
        await queue.put((kind, int(target_id), b, d, None))

    try:
        await asyncio.wait_for(first_done.wait(), timeout=8.0)
    except asyncio.TimeoutError:
        return f"首条气泡等待超时，已交由后台队列发送 {len(bubbles)} 条"

    return f"已分段连发 {len(bubbles)} 条气泡消息"


async def _handle_http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """极简 HTTP/1.1 请求处理器，仅处理本机 /enqueue_bubbles POST 请求"""
    status_code = 200
    resp_body = {"ok": True}
    try:
        raw_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3.0)
        head_lines = raw_head.decode("utf-8", errors="ignore").split("\r\n")
        request_line = head_lines[0] if head_lines else ""
        parts = request_line.split()
        method = parts[0].upper() if len(parts) >= 1 else ""
        path = parts[1] if len(parts) >= 2 else ""

        headers = {}
        for line in head_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        content_len = int(headers.get("content-length", "0") or "0")
        body_bytes = b""
        if content_len > 0:
            body_bytes = await asyncio.wait_for(reader.readexactly(content_len), timeout=8.0)

        auth = headers.get("authorization", "")
        if auth != f"Bearer {NAPCAT_TOKEN}":
            status_code = 401
            resp_body = {"ok": False, "error": "unauthorized"}
        elif method == "POST" and path == "/deskpet_event":
            payload = json.loads(body_bytes.decode("utf-8"))
            from attention.debounce_queue import handle_deskpet_event
            asyncio.create_task(handle_deskpet_event(payload))
            resp_body = {"ok": True, "message": "deskpet_event_accepted"}
        elif method == "POST" and path == "/deskpet_poll":
            global DESKPET_LAST_STATE, DESKPET_LAST_SEEN
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            if isinstance(payload.get("state"), dict):
                DESKPET_LAST_STATE = payload["state"]
            DESKPET_LAST_SEEN = time.time()
            if not DESKPET_PENDING_CMDS:
                wake = _get_poll_wake()
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=2.5)
                except asyncio.TimeoutError:
                    pass
            DESKPET_LAST_SEEN = time.time()
            cmds = list(DESKPET_PENDING_CMDS)
            DESKPET_PENDING_CMDS.clear()
            resp_body = {"ok": True, "commands": cmds}
        elif method == "POST" and path == "/deskpet_result":
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            DESKPET_LAST_SEEN = time.time()
            cmd_id = str(payload.get("cmd_id", ""))
            waiter = DESKPET_CMD_WAITERS.get(cmd_id)
            if waiter:
                waiter["result"] = payload.get("result")
                waiter["event"].set()
            resp_body = {"ok": True}
        elif method == "POST" and path == "/deskpet_proxy":
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            p_method = str(payload.get("method", "POST"))
            p_path = str(payload.get("path", "/api/mcp/state"))
            p_body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            p_timeout = float(payload.get("timeout", 20.0))
            resp_body = await enqueue_deskpet_proxy_cmd(p_method, p_path, p_body, timeout=p_timeout)
        elif method != "POST" or path != "/enqueue_bubbles":
            status_code = 404
            resp_body = {"ok": False, "error": "not_found"}
        else:
            payload = json.loads(body_bytes.decode("utf-8"))
            kind = str(payload.get("kind", "")).strip()
            target_id = int(payload.get("target_id", 0))
            bubbles = [str(b) for b in (payload.get("bubbles") or []) if str(b).strip()]
            delays = [float(d) for d in (payload.get("delays") or [])]
            if kind not in ("group", "private") or not target_id or not bubbles:
                status_code = 400
                resp_body = {"ok": False, "error": "invalid_payload"}
            else:
                msg = await enqueue_bubbles_and_wait_first(kind, target_id, bubbles, delays)
                resp_body = {"ok": True, "message": msg}
    except Exception as e:
        status_code = 500
        resp_body = {"ok": False, "error": str(e)}

    try:
        body_out = json.dumps(resp_body, ensure_ascii=False).encode("utf-8")
        status_text = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found"}.get(status_code, "Error")
        raw_resp = (
            f"HTTP/1.1 {status_code} {status_text}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body_out)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8") + body_out
        writer.write(raw_resp)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_bubble_dispatcher():
    """启动本机异步气泡投递服务"""
    global _DISPATCHER_SERVER
    if _DISPATCHER_SERVER is not None:
        return
    try:
        _DISPATCHER_SERVER = await asyncio.start_server(_handle_http_client, "0.0.0.0", INTERNAL_BUBBLE_PORT)
        print(f"🫧 [异步气泡与跨机事件网关] 已在 0.0.0.0:{INTERNAL_BUBBLE_PORT} 就绪 (1C-1 非阻塞连发 & 跨机事件启用，伴侣端日志独立见 logs/deskpet.log)")
    except OSError as e:
        print(f"⚠️ [异步气泡调度器] 端口 {INTERNAL_BUBBLE_PORT} 绑定失败（将自动回退内联连发）: {e}")
