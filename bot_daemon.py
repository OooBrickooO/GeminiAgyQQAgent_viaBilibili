"""
bot_daemon.py — 弥酱 QQ Agent 常驻守护进程根入口

极简装配层：负责环境初始化、代理锁定、常驻 Worker 注册与 WebSocket 事件分发。
"""
import os
import sys
import asyncio
import subprocess
import websockets
from config import sanitize_process_env, ONEBOT_WS_URL, BASE_DIR
from core.bubble_dispatcher import start_bubble_dispatcher
from attention.debounce_queue import handle_incoming_event
from workers.breathe import breathe_manager
from workers.daily_memory import daily_memory_summarization_worker
from workers.qzone_diary import daily_qzone_diary_worker
from workers.qzone_observer import qzone_patrol_worker
from workers.bili_worker import bili_surfing_worker, bili_dynamic_daily_worker
from workers.friend_request_worker import friend_request_worker
from trends.trend_manager import TrendManager


async def connect_napcat():
    print(f"正在连接到 NapCat/SnowLuma 的耳朵 ({ONEBOT_WS_URL})...")
    # 启动本机异步气泡连发调度器与常驻后台 Worker
    await start_bubble_dispatcher()
    asyncio.create_task(breathe_manager())
    asyncio.create_task(daily_memory_summarization_worker())
    asyncio.create_task(daily_qzone_diary_worker())
    asyncio.create_task(qzone_patrol_worker())
    asyncio.create_task(bili_surfing_worker())
    asyncio.create_task(bili_dynamic_daily_worker())
    asyncio.create_task(friend_request_worker())
    asyncio.create_task(TrendManager.fetch_and_cache_morning_trends())

    while True:
        try:
            async with websockets.connect(ONEBOT_WS_URL) as ws:
                print("✅ 顺风耳连接成功！开始 24 小时监听...")
                while True:
                    msg = await ws.recv()
                    await handle_incoming_event(msg)
        except Exception as e:
            print(f"❌ 连接断开，3秒后自动重连... ({e})")
            await asyncio.sleep(3)


if __name__ == "__main__":
    from core.log_manager import setup_categorized_logging
    setup_categorized_logging()

    print("=========== 智能体启动流程 ===========")
    sanitize_process_env()

    print("1. 正在强制锁定代理节点...")
    force_script = os.path.join(BASE_DIR, "force_global.py")
    if os.path.exists(force_script):
        subprocess.run([sys.executable, force_script])

    print("\n2. 正在启动网络监听...")
    asyncio.run(connect_napcat())
