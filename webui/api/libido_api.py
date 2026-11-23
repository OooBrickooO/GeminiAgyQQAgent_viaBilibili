"""
webui/api/libido_api.py — dsh-libido-panel 0和谐原生面板与用户授权白名单 WebUI API
"""
import os
import json
from webui.config import MASTER_QQ, PROFILES_FILE
from social.libido_panel import (
    PRESET_EVENTS,
    TIERS,
    load_libido_state,
    compute_current_state,
    build_libido_mood_line,
    build_72h_history,
    apply_libido_event,
    trigger_resolve,
    set_libido_value,
    trigger_manual_wake,
    manage_libido_user,
    update_libido_settings,
)


def _load_profiles_safe() -> dict:
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    return {}


def get_libido_overview() -> dict:
    st = load_libido_state(force=True)
    view = compute_current_state(st)
    history = build_72h_history(st, view)
    mood_line = build_libido_mood_line(view)

    allowed_map = st.get("allowed_users") or {}
    profiles = _load_profiles_safe()

    # 组装用户授权开关列表（主人置顶 + 已登记授权用户 + 好感排序好友候选）
    users = []
    seen = set()

    def _is_on(entry) -> bool:
        if isinstance(entry, dict):
            return bool(entry.get("enabled", False))
        return bool(entry)

    # 1. 主人永远置顶
    m_uid = str(MASTER_QQ)
    m_entry = allowed_map.get(m_uid, {"enabled": True, "label": "主人"})
    users.append({
        "user_id": m_uid,
        "nickname": "👑 主人",
        "affection": 1000.0,
        "enabled": _is_on(m_entry),
        "is_master": True,
        "added_at": m_entry.get("added_at", "") if isinstance(m_entry, dict) else "",
    })
    seen.add(m_uid)

    # 2. 已在 allowed_users 中的其他用户
    for uid, entry in allowed_map.items():
        uid_s = str(uid).strip()
        if not uid_s or uid_s in seen:
            continue
        prof = profiles.get(uid_s) or {}
        nick = (entry.get("label") if isinstance(entry, dict) else "") or prof.get("nickname") or uid_s
        users.append({
            "user_id": uid_s,
            "nickname": str(nick),
            "affection": float(prof.get("affection", 0.0)),
            "enabled": _is_on(entry),
            "is_master": False,
            "added_at": entry.get("added_at", "") if isinstance(entry, dict) else "",
        })
        seen.add(uid_s)

    # 3. user_profiles.json 中的其余好友候选（按好感度倒序前 25 名）
    cands = []
    for uid, prof in profiles.items():
        uid_s = str(uid).strip()
        if not uid_s or not uid_s.isdigit() or uid_s in seen:
            continue
        aff = float((prof or {}).get("affection", 0.0))
        nick = str((prof or {}).get("nickname") or uid_s)
        cands.append((aff, uid_s, nick))
    cands.sort(key=lambda x: x[0], reverse=True)
    for aff, uid_s, nick in cands[:25]:
        users.append({
            "user_id": uid_s,
            "nickname": nick,
            "affection": aff,
            "enabled": False,
            "is_master": False,
            "added_at": "",
        })
        seen.add(uid_s)

    enabled_user_count = sum(1 for u in users if u["enabled"])

    return {
        "success": True,
        "state": view,
        "tiers": TIERS,
        "mood_line": mood_line,
        "history": history,
        "events": (st.get("events") or [])[:40],
        "presets": PRESET_EVENTS,
        "users": users,
        "enabled_user_count": enabled_user_count,
    }


def manage_libido_api(
    action: str = "event",
    value: int = 0,
    delta: int = 0,
    note: str = "",
    user_id: str = "",
    label: str = "",
    enabled: str = "",
    private_only: str = "",
    baseline: str = "",
    season_start_day: str = "",
    season_days: str = "",
) -> dict:
    """
    统一操作接口：
    - event: 记入事件 (delta, note)
    - set: 手动设值 (value, note)
    - resolve: 解决一次归位初始值 (note)
    - wake: 手动记睡醒 (note)
    - user_toggle / user_enable / user_disable / user_remove: 管理授权白名单用户
    - settings: 更新开关与周期参数 (enabled, private_only, baseline, season_start_day, season_days, note)
    """
    act = str(action or "").strip().lower()
    msg = ""

    if act == "event":
        r = apply_libido_event(int(delta), note=str(note or ""))
        msg = f"已记录事件：{note or delta}（实际变动 {r['applied']:+d}，{r['before']} → {r['after']}）"
    elif act == "set":
        r = set_libido_value(int(value), note=str(note or "手动调整"))
        msg = f"已手动调整数值：{r['before']} → {r['after']}"
    elif act == "resolve":
        r = trigger_resolve(note=str(note or "解决一次"))
        msg = f"已执行解决归位：{r['before']} → {r['after']}"
    elif act == "wake":
        r = trigger_manual_wake(note=str(note or "手动睡醒结算"))
        if r.get("skipped"):
            res = get_libido_overview()
            res["message"] = r.get("message", "今日睡醒已结算")
            return res
        msg = f"已完成睡醒结算：{r['before']} → {r['after']}"
    elif act in ("user_toggle", "user_enable", "user_disable", "user_remove"):
        sub = act.replace("user_", "")
        r = manage_libido_user(sub, user_id=user_id, label=label)
        if not r.get("ok"):
            return {"success": False, "error": r.get("error", "操作用户失败")}
        msg = f"已更新用户 {user_id} 的开放权限。"
    elif act == "settings":
        def _parse_bool(s: str):
            sv = str(s or "").strip().lower()
            if sv in ("1", "true", "yes", "on"):
                return True
            if sv in ("0", "false", "no", "off"):
                return False
            return None

        update_libido_settings(
            enabled=_parse_bool(enabled),
            private_only=_parse_bool(private_only),
            baseline=int(baseline) if str(baseline).strip().lstrip("-").isdigit() else None,
            season_start_day=int(season_start_day) if str(season_start_day).strip().isdigit() else None,
            season_days=int(season_days) if str(season_days).strip().isdigit() else None,
            note=note if note != "__KEEP__" else None,
        )
        msg = "已保存面板参数与全局开关设置。"
    else:
        return {"success": False, "error": f"不支持的 action: {action}"}

    res = get_libido_overview()
    res["message"] = msg
    return res
