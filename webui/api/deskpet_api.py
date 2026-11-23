"""
webui/api/deskpet_api.py — 8088 WebUI 星见弥桌宠与屏幕分析/CUA 控制面板 API
同步兼容 FastAPI 与 NativeWebUIHandler。
"""
import asyncio
import httpx
from config import NAPCAT_TOKEN, DESKPET_API_CANDIDATES, INTERNAL_BUBBLE_PORT
from tools.deskpet_tools import (
    load_deskpet_local_state,
    save_deskpet_local_state,
    pet_say_impl,
    pet_act_impl,
)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NAPCAT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _call_deskpet_sync(path: str, payload: dict = None, method: str = "GET", timeout: float = 2.0) -> tuple[str, dict | None]:
    """优先直连 DeskPet :7797，若 Windows 防火墙拦截入站连接则自动通过本机 18302 反向轮询通道调用"""
    with httpx.Client(timeout=0.9, trust_env=False) as client:
        for base in DESKPET_API_CANDIDATES:
            try:
                if method.upper() == "GET":
                    r = client.get(f"{base}{path}", headers=_headers())
                else:
                    r = client.post(f"{base}{path}", json=payload or {}, headers=_headers())
                if r.status_code == 200:
                    return base, r.json()
            except Exception:
                continue

    # 降级走 127.0.0.1:18302/deskpet_proxy 反向轮询桥
    try:
        with httpx.Client(timeout=max(2.0, timeout + 1.0), trust_env=False) as client:
            r = client.post(
                f"http://127.0.0.1:{INTERNAL_BUBBLE_PORT}/deskpet_proxy",
                json={"method": method.upper(), "path": path, "payload": payload or {}, "timeout": timeout},
                headers=_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("ok"):
                    return "反向穿透桥(:18302)", data
    except Exception:
        pass
    return "", None


def _find_live_deskpet_url(timeout: float = 1.2) -> tuple[str, dict | None]:
    return _call_deskpet_sync("/api/mcp/state", method="GET", timeout=timeout)


def get_deskpet_overview_api() -> dict:
    """获取桌宠在线状态、主动看屏吐槽开关、CUA 权限档位与实时前台窗口信息"""
    local_st = load_deskpet_local_state()
    base_url, live = _find_live_deskpet_url(timeout=1.2)
    if live and live.get("ok"):
        pro = live.get("proactive") or {}
        prefs = live.get("prefs") or {}
        cua = live.get("cua") or {}
        asr = live.get("asr") or {}
        return {
            "success": True,
            "online": True,
            "endpoint": base_url,
            "pet_window_connected": bool(live.get("petConnected")),
            "figure": live.get("figure", "mi"),
            "figure_name": live.get("figureName", "星见弥 (弥酱)"),
            "proactive_peek": bool(pro.get("enabled", True)),
            "allow_in_game": bool(pro.get("allowInGame", True)),
            "in_game": bool(pro.get("inGame", False)),
            "foreground_app": pro.get("foregroundApp") or "",
            "foreground_title": pro.get("foregroundTitle") or "",
            "normal_interval_sec": int(pro.get("normalIntervalSec") or 900),
            "game_interval_sec": int(pro.get("gameIntervalSec") or 240),
            "cua_enabled": bool(cua.get("enabled", True)),
            "cua_control": bool(cua.get("control", True)),
            "cua_permission": cua.get("permission", "ask-once"),
            "cua_granted_remaining_sec": int(cua.get("grantedRemainingSec") or 0),
            "scale": float(prefs.get("scale") or 1.0),
            "roam": prefs.get("roam", "calm"),
            "sound": bool(prefs.get("sound", True)),
            "theme": prefs.get("theme", "dark"),
            "pass_when_fullscreen": bool(prefs.get("passWhenFullscreen", False)),
            "asr_ready": bool(asr.get("ready", False)),
            "asr_hotkey": asr.get("hotkey", "LeftAlt*2"),
            "asr_mode": asr.get("mode", "hold"),
            "type_hotkey": asr.get("typeHotkey", "LeftCtrl*2"),
            "type_key_enabled": bool(asr.get("typeKeyEnabled", True)),
        }

    return {
        "success": True,
        "online": False,
        "endpoint": "",
        "pet_window_connected": False,
        "figure": "mi",
        "figure_name": "星见弥 (弥酱)",
        "proactive_peek": bool(local_st.get("proactive_peek", True)),
        "allow_in_game": bool(local_st.get("allow_in_game", True)),
        "in_game": False,
        "foreground_app": "",
        "foreground_title": "",
        "normal_interval_sec": int(local_st.get("normal_interval_sec", 900)),
        "game_interval_sec": int(local_st.get("game_interval_sec", 240)),
        "cua_enabled": bool(local_st.get("cua_enabled", True)),
        "cua_control": bool(local_st.get("cua_control", True)),
        "cua_permission": local_st.get("cua_permission", "ask-once"),
        "cua_granted_remaining_sec": 0,
        "scale": float(local_st.get("scale", 1.4)),
        "roam": local_st.get("roam", "calm"),
        "sound": bool(local_st.get("sound", True)),
        "theme": local_st.get("theme", "dark"),
        "pass_when_fullscreen": bool(local_st.get("pass_when_fullscreen", False)),
        "asr_ready": False,
        "asr_hotkey": local_st.get("asr_hotkey", "LeftAlt*2"),
        "asr_mode": local_st.get("asr_mode", "hold"),
        "type_hotkey": local_st.get("type_hotkey", "LeftCtrl*2"),
        "type_key_enabled": bool(local_st.get("type_key_enabled", True)),
    }


def manage_deskpet_api(
    action: str = "update_prefs",
    proactive_peek: str = "",
    allow_in_game: str = "",
    normal_interval_sec: str = "",
    game_interval_sec: str = "",
    cua_enabled: str = "",
    cua_control: str = "",
    cua_permission: str = "",
    scale: str = "",
    roam: str = "",
    pass_when_fullscreen: str = "",
    talk_hotkey: str = "",
    talk_mode: str = "",
    type_hotkey: str = "",
    type_key_enabled: str = "",
    script: str = "",
    emotion: str = "auto",
) -> dict:
    """更新桌宠开关/设置、立即触发看屏吐槽，或在桌宠上测试说话/动作"""
    base_url, _ = _find_live_deskpet_url(timeout=1.2)

    if action == "peek_now":
        if not base_url:
            return {"success": False, "error": "Windows 桌宠客户端 (DeskPet:7797) 当前未启动"}
        try:
            _, data = _call_deskpet_sync(
                "/api/mcp/peek_now",
                {"reason": "主人从 8088 WebUI 手动召唤看屏吐槽"},
                method="POST",
                timeout=8.0,
            )
            data = data or {}
            return {
                "success": bool(data.get("ok")),
                "message": "已触发截屏并投递给弥酱大脑，稍等几秒她就会在桌面上冒气泡+语音吐槽啦！" if data.get("ok") else f"触发未成功: {data.get('reason')}",
                "overview": get_deskpet_overview_api(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    if action == "say_test" and script.strip():
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(pet_say_impl(script.strip(), emotion=emotion or "auto", voice=True))
                msg = "已异步下发桌宠气泡与 9D 语音"
            else:
                msg = asyncio.run(pet_say_impl(script.strip(), emotion=emotion or "auto", voice=True))
            return {"success": True, "message": msg, "overview": get_deskpet_overview_api()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    if action == "act_test" and script.strip():
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(pet_act_impl(script.strip()))
                msg = f"已下发桌宠动作: {script.strip()}"
            else:
                msg = asyncio.run(pet_act_impl(script.strip()))
            return {"success": True, "message": msg, "overview": get_deskpet_overview_api()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # 默认 action == "update_prefs"
    local_patch = {}
    remote_patch = {}
    cua_patch = {}

    def _to_bool(v: str) -> bool:
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    if proactive_peek != "":
        b = _to_bool(proactive_peek)
        local_patch["proactive_peek"] = b
        remote_patch["proactivePeek"] = b
    if allow_in_game != "":
        b = _to_bool(allow_in_game)
        local_patch["allow_in_game"] = b
        remote_patch["allowInGame"] = b
    if normal_interval_sec != "" and str(normal_interval_sec).isdigit():
        n = max(30, int(normal_interval_sec))
        local_patch["normal_interval_sec"] = n
        remote_patch["normalIntervalSec"] = n
    if game_interval_sec != "" and str(game_interval_sec).isdigit():
        n = max(30, int(game_interval_sec))
        local_patch["game_interval_sec"] = n
        remote_patch["gameIntervalSec"] = n
    if cua_enabled != "":
        b = _to_bool(cua_enabled)
        local_patch["cua_enabled"] = b
        cua_patch["enabled"] = b
    if cua_control != "":
        b = _to_bool(cua_control)
        local_patch["cua_control"] = b
        cua_patch["control"] = b
    if cua_permission in ("ask-each-turn", "ask-before-acting", "ask-once", "never-ask"):
        local_patch["cua_permission"] = cua_permission
        cua_patch["permission"] = cua_permission
    if scale != "":
        try:
            sc = max(0.5, min(10.0, float(scale)))
            local_patch["scale"] = sc
            remote_patch["scale"] = sc
        except Exception:
            pass
    if roam in ("off", "calm", "free"):
        local_patch["roam"] = roam
        remote_patch["roam"] = roam
    if pass_when_fullscreen != "":
        b = _to_bool(pass_when_fullscreen)
        local_patch["pass_when_fullscreen"] = b
        remote_patch["passWhenFullscreen"] = b
    if talk_hotkey != "":
        hk = str(talk_hotkey).strip()
        local_patch["asr_hotkey"] = hk
        remote_patch["talkHotkey"] = hk
    if talk_mode in ("auto", "toggle", "hold"):
        local_patch["asr_mode"] = talk_mode
        remote_patch["talkMode"] = talk_mode
    if type_hotkey != "":
        thk = str(type_hotkey).strip()
        local_patch["type_hotkey"] = thk
        remote_patch["typeHotkey"] = thk
    if type_key_enabled != "":
        b = _to_bool(type_key_enabled)
        local_patch["type_key_enabled"] = b
        remote_patch["typeKeyEnabled"] = b

    if cua_patch:
        remote_patch["cua"] = cua_patch

    if local_patch:
        save_deskpet_local_state(local_patch)

    if base_url and remote_patch:
        _call_deskpet_sync("/api/mcp/prefs", remote_patch, method="POST", timeout=3.0)

    return {
        "success": True,
        "message": "桌宠与屏幕感知设置已更新",
        "overview": get_deskpet_overview_api(),
    }
