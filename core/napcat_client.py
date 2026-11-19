"""
core/napcat_client.py — OneBot HTTP API 通信客户端封装

统一注入 Bearer Token 与 trust_env=False (铁律 3)。
所有对 NapCat/SnowLuma 的 HTTP 请求均汇聚于此模块。
"""
import httpx
import json
import asyncio
from config import ONEBOT_HTTP_URL, NAPCAT_TOKEN, BOT_QQ

# 缓存机器人在各群中的身份角色: group_id_str -> {"role": str, "time": float}
BOT_GROUP_ROLES = {}



def _get_headers() -> dict:
    """统一的 OneBot API 请求头"""
    return {"Authorization": f"Bearer {NAPCAT_TOKEN}"}


async def call_onebot_api(endpoint: str, payload: dict = None, timeout: float = 15.0) -> dict:
    """
    通用 OneBot HTTP API 调用器。
    endpoint: API 路径，如 '/send_group_msg'
    payload: JSON 请求体
    返回: 解析后的 JSON dict
    """
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    url = f"{ONEBOT_HTTP_URL}{endpoint}"
    async with httpx.AsyncClient(timeout=timeout, headers=_get_headers(), trust_env=False) as client:
        if payload is not None:
            resp = await client.post(url, json=payload)
        else:
            resp = await client.post(url)
        return resp.json()


async def call_onebot_api_raw(endpoint: str, payload: dict = None, timeout: float = 15.0) -> str:
    """同 call_onebot_api 但返回原始响应文本 (部分旧工具依赖原始文本)"""
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    url = f"{ONEBOT_HTTP_URL}{endpoint}"
    async with httpx.AsyncClient(timeout=timeout, headers=_get_headers(), trust_env=False) as client:
        if payload is not None:
            resp = await client.post(url, json=payload)
        else:
            resp = await client.post(url)
        return resp.text


async def send_group_msg_raw(group_id: int, message: str) -> str:
    """发送群消息，返回原始响应文本"""
    return await call_onebot_api_raw("/send_group_msg", {"group_id": group_id, "message": message})


async def send_private_msg_raw(user_id: int, message: str) -> str:
    """发送私聊消息，返回原始响应文本"""
    return await call_onebot_api_raw("/send_private_msg", {"user_id": user_id, "message": message})


async def set_input_status(user_id: int, event_type: int = 1) -> dict:
    """设置私聊聊天窗口输入状态 (event_type=1 表示正在输入)"""
    try:
        return await call_onebot_api("/set_input_status", {"user_id": int(user_id), "event_type": int(event_type)}, timeout=3.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def set_group_card(group_id: int, user_id: int, card: str) -> dict:
    """修改指定群中指定成员（或机器人自身）的群名片"""
    try:
        return await call_onebot_api("/set_group_card", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "card": card
        }, timeout=5.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def fetch_group_member_count(group_id_str: str) -> int:
    """异步获取群成员数量（带 Bearer 鉴权与 trust_env=False 保护）"""
    try:
        res = await call_onebot_api("/get_group_info", {"group_id": int(group_id_str)}, timeout=3.0)
        if res.get("status") == "ok" and "data" in res:
            return int(res["data"].get("member_count", 200))
    except Exception:
        pass
    return 200


async def set_group_ban(group_id: int, user_id: int, duration: int = 180) -> dict:
    """群单人禁言 (duration单位为秒，0为解除禁言)"""
    try:
        return await call_onebot_api("/set_group_ban", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "duration": int(duration)
        }, timeout=5.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def get_group_member_info(group_id: int, user_id: int, no_cache: bool = False) -> dict:
    """获取指定群成员的信息 (包含 role, card, nickname 等)"""
    try:
        return await call_onebot_api("/get_group_member_info", {
            "group_id": int(group_id),
            "user_id": int(user_id),
            "no_cache": no_cache
        }, timeout=5.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def fetch_bot_group_role(group_id: int) -> str:
    """获取机器人在指定群中的身份 (owner / admin / member)，缓存 10 分钟"""
    import time
    gid_str = str(group_id)
    now = time.time()
    cached = BOT_GROUP_ROLES.get(gid_str)
    if cached and (now - cached.get("time", 0) < 600):
        return cached.get("role", "member")

    try:
        resp = await get_group_member_info(group_id, int(BOT_QQ))
        if resp.get("status") == "ok" and "data" in resp:
            role = resp["data"].get("role", "member")
            BOT_GROUP_ROLES[gid_str] = {"role": role, "time": now}
            return role
    except Exception:
        pass
    return "member"


async def send_poke(group_id: int, user_id: int) -> dict:
    """发送戳一戳 (如果在群内调用 group_poke，否则调用 friend_poke)"""
    try:
        if group_id and int(group_id) > 0:
            return await call_onebot_api("/group_poke", {
                "group_id": int(group_id),
                "user_id": int(user_id)
            }, timeout=5.0)
        else:
            return await call_onebot_api("/friend_poke", {
                "user_id": int(user_id)
            }, timeout=5.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def set_friend_add_request(flag: str, approve: bool = True, remark: str = "") -> dict:
    """处理常规好友申请 (approve=True 通过, approve=False 拒绝)"""
    try:
        payload = {
            "flag": str(flag),
            "approve": bool(approve),
        }
        if remark:
            payload["remark"] = str(remark)
        return await call_onebot_api("/set_friend_add_request", payload, timeout=10.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def get_doubt_friends_add_request(count: int = 20) -> dict:
    """获取 NTQQ 被过滤/折叠的疑虑好友申请列表 (NapCat 扩展接口)"""
    try:
        return await call_onebot_api("/get_doubt_friends_add_request", {"count": int(count)}, timeout=10.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def set_doubt_friends_add_request(flag: str, approve: bool = True) -> dict:
    """处理 NTQQ 被过滤/折叠的疑虑好友申请 (NapCat 扩展接口)"""
    try:
        return await call_onebot_api("/set_doubt_friends_add_request", {
            "flag": str(flag),
            "approve": bool(approve)
        }, timeout=10.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


async def get_stranger_info(user_id: int) -> dict:
    """查询陌生人/新申请好友的基础 QQ 资料（获取昵称等）"""
    try:
        return await call_onebot_api("/get_stranger_info", {
            "user_id": int(user_id),
            "no_cache": False
        }, timeout=8.0)
    except Exception as e:
        return {"status": "failed", "error": str(e)}


