"""
webui/api/qzone_care_api.py — WebUI QQ 空间特别关注（特关）与巡检状态接口
"""
import datetime
import httpx
from typing import Dict, Any, List
from config import ONEBOT_HTTP_URL, NAPCAT_TOKEN, MASTER_QQ
from social.affection import USER_PROFILES, load_profiles
from social.qzone_special_care import (
    get_effective_special_care_map, add_special_care, remove_special_care, load_special_care_data
)
from workers.qzone_observer import load_observer_state


def _fetch_onebot_friends_sync() -> Dict[str, str]:
    """同步尽力从 OneBot 拉取好友 QQ->昵称映射，离线时静默返回空映射。"""
    headers = {}
    if NAPCAT_TOKEN:
        headers["Authorization"] = f"Bearer {NAPCAT_TOKEN}"
    try:
        resp = httpx.post(
            f"{ONEBOT_HTTP_URL.rstrip('/')}/get_friend_list",
            json={},
            headers=headers,
            timeout=2.0,
            trust_env=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok" and isinstance(data.get("data"), list):
                return {
                    str(fr.get("user_id", "")).strip(): str(fr.get("remark") or fr.get("nickname") or "").strip()
                    for fr in data["data"]
                    if str(fr.get("user_id", "")).strip()
                }
    except Exception:
        pass
    return {}


def get_qzone_care_overview() -> Dict[str, Any]:
    """汇总返回 QQ 空间特别关注名单、候选熟人好友、以及最近巡检记录。"""
    if not USER_PROFILES:
        try:
            load_profiles()
        except Exception:
            pass

    friend_nicks = _fetch_onebot_friends_sync()
    care_map = get_effective_special_care_map()
    raw_care = load_special_care_data()
    obs_state = load_observer_state()

    now_utc8 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    today_str = now_utc8.strftime("%Y-%m-%d")
    today_slots = (obs_state.get("completed_slots") or {}).get(today_str, [])

    special_list: List[Dict[str, Any]] = []
    source_order = {"master": 0, "bff_auto": 1, "bot": 2, "master_manual": 3, "webui": 4}
    for uid, info in care_map.items():
        nick = friend_nicks.get(uid) or info.get("nickname") or f"好友_{uid}"
        special_list.append({
            "uin": uid,
            "nickname": nick,
            "avatar_url": f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=100",
            "affection": int(info.get("affection", 0) or 0),
            "tier": info.get("tier", ""),
            "source": info.get("source", "webui"),
            "reason": info.get("reason", ""),
            "added_by": info.get("added_by", "webui"),
            "removable": bool(info.get("removable", uid != str(MASTER_QQ))),
        })

    special_list.sort(
        key=lambda x: (source_order.get(x["source"], 9), -x["affection"], x["uin"])
    )

    # 汇总可一键加入特关的候选好友（非特关，按好感度排序）
    candidates: List[Dict[str, Any]] = []
    seen_cand = set(care_map.keys())
    all_known_uids = set(USER_PROFILES.keys()) | set(friend_nicks.keys())
    for uid in all_known_uids:
        if not uid or not str(uid).isdigit() or uid in seen_cand:
            continue
        prof = USER_PROFILES.get(uid, {})
        aff = int(prof.get("affection", 0) or 0)
        nick = friend_nicks.get(uid) or prof.get("nickname") or f"好友_{uid}"
        candidates.append({
            "uin": uid,
            "nickname": nick,
            "affection": aff,
            "is_excluded_bff": uid in (raw_care.get("excluded_auto") or []),
            "in_friend_list": uid in friend_nicks,
        })
    candidates.sort(key=lambda x: (-x["affection"], not x["in_friend_list"], x["uin"]))

    bff_auto_cnt = sum(1 for x in special_list if x["source"] == "bff_auto")
    manual_cnt = sum(1 for x in special_list if x["source"] not in ("master", "bff_auto"))

    recent_logs = list(reversed(obs_state.get("recent_logs") or []))[:15]

    return {
        "success": True,
        "summary": {
            "total_care": len(special_list),
            "bff_auto_count": bff_auto_cnt,
            "manual_count": manual_cnt,
            "seen_tid_count": len(obs_state.get("seen_tids") or {}),
            "today_slots_done": today_slots,
            "onebot_online": bool(friend_nicks),
        },
        "special_care": special_list,
        "candidates": candidates[:40],
        "recent_logs": recent_logs,
    }


def manage_qzone_special_care_api(
    action: str = "add",
    user_id: str = "",
    nickname: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """WebUI 增删空间特别关注接口（立即保存并同步返回最新全景）。"""
    act = (action or "add").strip().lower()
    if act == "add":
        res = add_special_care(user_id, nickname=nickname, reason=reason, added_by="webui")
    elif act == "remove":
        res = remove_special_care(user_id)
    else:
        return {"success": False, "error": f"未知操作: {action}"}

    if not res.get("ok"):
        return {"success": False, "error": res.get("error", "操作失败")}

    overview = get_qzone_care_overview()
    overview["message"] = res.get("message", "操作成功")
    return overview
