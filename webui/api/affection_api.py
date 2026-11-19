"""
webui/api/affection_api.py — 好感度羁绊体系与社交画像 API
"""
import os
import json
import datetime
from webui.config import PROFILES_FILE, MASTER_QQ, BOT_QQ
from social.affection import check_best_friend_gate, master_manage_affection


def get_affection_data() -> dict:
    profiles = {}
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    profiles = data
        except Exception:
            profiles = {}

    user_list = []
    tier_counts = {
        "best_friend": 0,    # 💖 挚友 (>= 850)
        "friend": 0,         # 🤝 朋友 (500 ~ 849)
        "acquaintance": 0,   # ✨ 熟人 (200 ~ 499)
        "passerby": 0,       # 🌱 路人 (0 ~ 199)
        "blacklist": 0       # ⛔ 黑名单 (< 0)
    }

    now_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)

    for user_id_str, prof in profiles.items():
        if not isinstance(prof, dict):
            continue

        aff = float(prof.get("affection", 0.0))
        is_master = str(user_id_str) == str(MASTER_QQ)
        is_bot = str(user_id_str) == str(BOT_QQ)

        if aff < 0.0:
            tier_key = "blacklist"
            tier_name = "⛔ 黑名单"
            badge_color = "#ef4444"
        elif aff >= 850.0 or is_master:
            tier_key = "best_friend"
            tier_name = "💖 挚友 (殿堂级)"
            badge_color = "#ec4899"
        elif aff >= 500.0:
            tier_key = "friend"
            tier_name = "🤝 朋友 (稳态区)"
            badge_color = "#3b82f6"
        elif aff >= 200.0:
            tier_key = "acquaintance"
            tier_name = "✨ 熟人"
            badge_color = "#10b981"
        else:
            tier_key = "passerby"
            tier_name = "🌱 路人"
            badge_color = "#6b7280"

        tier_counts[tier_key] += 1

        # 计算相识天数
        first_date_str = prof.get("first_seen_date", "")
        days_known = 0
        if first_date_str:
            try:
                first_dt = datetime.datetime.strptime(first_date_str, "%Y-%m-%d").date()
                days_known = max(0, (now_dt.date() - first_dt).days)
            except Exception:
                days_known = 0

        # 挚友门槛硬锁校验
        can_promote, gate_reason = check_best_friend_gate(prof, user_id_str)
        pending_req = prof.get("pending_affection_request")
        if not isinstance(pending_req, dict):
            pending_req = None

        user_list.append({
            "user_id": str(user_id_str),
            "is_master": is_master,
            "is_bot": is_bot,
            "nickname": prof.get("nickname", "") or ("主人" if is_master else f"群友 {user_id_str}"),
            "affection": round(aff, 1),
            "level": int(prof.get("level", 0)),
            "exp": round(float(prof.get("exp", 0.0)), 1),
            "interaction_count": int(prof.get("interaction_count", 0)),
            "high_resonance_count": int(prof.get("high_resonance_count", 0)),
            "is_calibrated": bool(prof.get("is_calibrated", False)),
            "master_approved_bff": bool(prof.get("master_approved_bff", False)),
            "pending_request": pending_req,
            "days_known": days_known,
            "tier_key": tier_key,
            "tier_name": tier_name,
            "badge_color": badge_color,
            "can_promote_bff": can_promote,
            "gate_reason": gate_reason,
            "last_date": prof.get("last_date", "")
        })

    # 按是否有待审批申请、主人身份、好感度从高到低排序
    user_list.sort(
        key=lambda u: (1 if u["is_master"] else 0, 1 if u["pending_request"] else 0, u["affection"]),
        reverse=True
    )
    pending_requests_count = sum(1 for u in user_list if u["pending_request"])

    return {
        "success": True,
        "total_users": len(user_list),
        "pending_requests_count": pending_requests_count,
        "tier_counts": tier_counts,
        "users": user_list
    }


def manage_affection_api(action: str, user_id: str, value: float = 0.0, reason: str = "") -> dict:
    """WebUI 好感度实时管理接口（增减/设定/±5%微调/审批申请/高光次数/破格授信）"""
    uid = str(user_id or "").strip()
    if not uid:
        return {"success": False, "error": "缺少目标用户 QQ 号"}

    res = master_manage_affection(uid, action=action, value=value, reason=reason)
    if not res.get("ok"):
        return {"success": False, "error": res.get("error", "操作失败")}

    latest = get_affection_data()
    latest["operation"] = res
    return latest

