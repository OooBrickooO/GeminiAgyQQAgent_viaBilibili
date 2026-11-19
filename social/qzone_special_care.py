"""
social/qzone_special_care.py — QQ 空间【特别关注（特关）】自管理数据模块

设计机制：
1. 弥补官方 QZone 网页协议无可靠“特关标签”的缺口，维护本地特关清单 (`qzone_special_care.json`)；
2. 弥酱的主人 (`MASTER_QQ`) 与所有挚友级别好友 (`affection >= 850`) 自动默认纳入特关名单（支持 WebUI 显式取消排除）；
3. 支持通过 WebUI 手动增删、以及弥酱本人在对话中自主调用 MCP 工具将高好感好友纳入特关。
"""
import os
import json
import time
from typing import Dict, Any
from config import MASTER_QQ, QZONE_SPECIAL_CARE_FILE
from social.affection import USER_PROFILES, load_profiles


BFF_MIN_AFFECTION = 850
BOT_AUTO_ADD_MIN_AFFECTION = 200


def _clean_uin(uin: Any) -> str:
    return str(uin or "").replace("[CQ:at,qq=", "").replace("]", "").strip()


def load_special_care_data() -> Dict[str, Any]:
    """读取本地特关配置文件 (`qzone_special_care.json`)。"""
    default_data: Dict[str, Any] = {"entries": {}, "excluded_auto": []}
    if os.path.exists(QZONE_SPECIAL_CARE_FILE):
        try:
            with open(QZONE_SPECIAL_CARE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                entries = data.get("entries")
                excluded = data.get("excluded_auto")
                return {
                    "entries": entries if isinstance(entries, dict) else {},
                    "excluded_auto": [str(x) for x in excluded] if isinstance(excluded, list) else [],
                }
        except Exception:
            pass
    return default_data


def save_special_care_data(data: Dict[str, Any]) -> bool:
    """原子落盘写入特关配置。"""
    try:
        payload = {
            "entries": data.get("entries") if isinstance(data.get("entries"), dict) else {},
            "excluded_auto": sorted(list({str(x) for x in (data.get("excluded_auto") or []) if str(x).strip()})),
        }
        tmp_path = QZONE_SPECIAL_CARE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, QZONE_SPECIAL_CARE_FILE)
        return True
    except Exception as e:
        print(f"⚠️ [qzone_special_care] 保存特关配置失败: {e}")
        return False


def _ensure_profiles_loaded() -> None:
    if not USER_PROFILES:
        try:
            load_profiles()
        except Exception:
            pass


def _get_tier_name(affection: int) -> str:
    if affection < 0:
        return "黑名单"
    if affection >= 850:
        return "挚友"
    if affection >= 500:
        return "朋友"
    if affection >= 200:
        return "熟人"
    return "路人"


def get_effective_special_care_map() -> Dict[str, Dict[str, Any]]:
    """
    合并返回当前生效的全部【特别关注】名单（字典 key 为 str(uin)）：
    - 主人 (`MASTER_QQ`) 永久生效
    - `affection >= 850`（挚友）自动纳入（除非在 excluded_auto 中）
    - 手动 / 弥酱自加记录 (`entries`)
    """
    _ensure_profiles_loaded()
    raw = load_special_care_data()
    entries = raw.get("entries", {})
    excluded_auto = set(raw.get("excluded_auto", []))

    effective: Dict[str, Dict[str, Any]] = {}

    # 1. 弥酱的主人永久在列
    master_uid = _clean_uin(MASTER_QQ)
    if master_uid:
        m_prof = USER_PROFILES.get(master_uid, {})
        effective[master_uid] = {
            "uin": master_uid,
            "nickname": m_prof.get("nickname") or "主人",
            "affection": int(m_prof.get("affection", 999) or 999),
            "tier": "自家主人",
            "source": "master",
            "reason": "自家主人，空间动态必看",
            "added_by": "system",
            "added_at": 0.0,
            "removable": False,
        }

    # 2. 挚友级别 (>= 850) 自动默认特关
    for uid, prof in list(USER_PROFILES.items()):
        clean_id = _clean_uin(uid)
        if not clean_id or not clean_id.isdigit():
            continue
        if clean_id == master_uid or clean_id in excluded_auto:
            continue
        aff = int(prof.get("affection", 0) or 0)
        if aff >= BFF_MIN_AFFECTION:
            effective[clean_id] = {
                "uin": clean_id,
                "nickname": prof.get("nickname") or f"好友_{clean_id}",
                "affection": aff,
                "tier": _get_tier_name(aff),
                "source": "bff_auto",
                "reason": "挚友级别好感默认开启特别关注",
                "added_by": "auto_bff",
                "added_at": 0.0,
                "removable": True,
            }

    # 3. 手动 / 弥酱自主添加的特关（可补充昵称、理由，或覆盖默认信息）
    for uid, item in entries.items():
        clean_id = _clean_uin(uid)
        if not clean_id or not isinstance(item, dict):
            continue
        prof = USER_PROFILES.get(clean_id, {})
        aff = int(prof.get("affection", 0) or 0)
        existing = effective.get(clean_id, {})
        resolved_nick = (
            item.get("nickname")
            or prof.get("nickname")
            or existing.get("nickname")
            or f"好友_{clean_id}"
        )
        added_by = str(item.get("added_by", "webui"))
        effective[clean_id] = {
            "uin": clean_id,
            "nickname": resolved_nick,
            "affection": aff if clean_id != master_uid else existing.get("affection", 999),
            "tier": existing.get("tier") or _get_tier_name(aff),
            "source": "master" if clean_id == master_uid else added_by,
            "reason": str(item.get("reason") or existing.get("reason") or "手动加入特别关注"),
            "added_by": added_by,
            "added_at": float(item.get("added_at", 0.0) or 0.0),
            "removable": clean_id != master_uid,
        }

    return effective


def is_special_care(uin: Any) -> bool:
    clean_id = _clean_uin(uin)
    if not clean_id:
        return False
    return clean_id in get_effective_special_care_map()


def add_special_care(
    uin: Any,
    nickname: str = "",
    reason: str = "",
    added_by: str = "webui",
) -> Dict[str, Any]:
    """
    新增或更新一位好友的 QQ 空间特别关注状态。
    """
    clean_id = _clean_uin(uin)
    if not clean_id or not clean_id.isdigit():
        return {"ok": False, "error": f"无效的 QQ 号: {uin}"}

    _ensure_profiles_loaded()
    prof = USER_PROFILES.get(clean_id, {})
    aff = int(prof.get("affection", 0) or 0)

    # 若是弥酱自主调用增加（非 WebUI/主人显式），限制仅能对熟人好友以上 (>=200) 或主人使用
    if added_by == "bot" and clean_id != _clean_uin(MASTER_QQ) and aff < BOT_AUTO_ADD_MIN_AFFECTION:
        return {
            "ok": False,
            "error": f"对方({clean_id})当前好感度仅 {aff}（未达熟人门槛 {BOT_AUTO_ADD_MIN_AFFECTION}），暂不随意加入特别关注",
        }

    raw = load_special_care_data()
    entries = raw.setdefault("entries", {})
    excluded_auto = raw.setdefault("excluded_auto", [])
    if clean_id in excluded_auto:
        excluded_auto = [x for x in excluded_auto if x != clean_id]
        raw["excluded_auto"] = excluded_auto

    resolved_nick = (nickname or "").strip() or prof.get("nickname") or entries.get(clean_id, {}).get("nickname") or ""
    entries[clean_id] = {
        "uin": clean_id,
        "nickname": resolved_nick,
        "reason": (reason or "").strip() or "重点关心他的空间动态",
        "added_by": added_by or "webui",
        "added_at": time.time(),
    }
    if not save_special_care_data(raw):
        return {"ok": False, "error": "保存特关配置文件失败"}

    eff = get_effective_special_care_map().get(clean_id, entries[clean_id])
    return {"ok": True, "entry": eff, "message": f"已将 {resolved_nick or clean_id}({clean_id}) 设为空间特别关注"}


def remove_special_care(uin: Any) -> Dict[str, Any]:
    """
    移出 QQ 空间特别关注；若对方是自动纳入的挚友，则加入 excluded_auto 避免下一秒又自动出现。
    主人账号受保护不允许取消。
    """
    clean_id = _clean_uin(uin)
    if not clean_id or not clean_id.isdigit():
        return {"ok": False, "error": f"无效的 QQ 号: {uin}"}
    if clean_id == _clean_uin(MASTER_QQ):
        return {"ok": False, "error": "主人账号受保护，不能移出特别关注"}

    _ensure_profiles_loaded()
    raw = load_special_care_data()
    entries = raw.setdefault("entries", {})
    excluded_auto = raw.setdefault("excluded_auto", [])

    removed_manual = clean_id in entries
    if removed_manual:
        entries.pop(clean_id, None)

    prof = USER_PROFILES.get(clean_id, {})
    aff = int(prof.get("affection", 0) or 0)
    added_excl = False
    if aff >= BFF_MIN_AFFECTION and clean_id not in excluded_auto:
        excluded_auto.append(clean_id)
        added_excl = True

    if not save_special_care_data(raw):
        return {"ok": False, "error": "保存特关配置失败"}

    return {
        "ok": True,
        "removed_manual": removed_manual,
        "excluded_bff_auto": added_excl,
        "message": f"已将 {clean_id} 移出空间特别关注",
    }
