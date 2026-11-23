"""
social/libido_panel.py — dsh-libido-panel 原生 Python 完整移植版（0和谐原版规则 + 权限白名单硬隔离）

完整复刻 `amtoki520/dsh-libido-panel` 全部核心动力学与说话节奏机制：
1. 0–100 数值状态机与五档：平静 / 有点想 / 难耐 / 快撑不住 / 发情；
2. 确定性不规则清醒推进：每日 08:00–24:00 (UTC+8) 期望每 2 小时 +1，发情期 ×4（每半小时 +1）；
3. 每日两段式睡醒结算（默认 08:00 或手动记睡醒）：
   - 刚醒：保留高出初始值部分的 10%–20%；
   - 清醒后回想：30–90 分钟后加回睡前高出部分的 20%–35%（仅一次，手动调值或“解决一次”可取消）；
4. 同日正向事件递减防通胀（100% → 75% → 50% → 25%），负向事件不打折；
5. 月度 3 天发情期（默认每月 18–20 日，WebUI 可调）：涨速 ×4、五档门槛整体降 15；
6. 严格权限白名单硬锁：默认仅对主人 (MASTER_QQ) 开放，可在 WebUI 授权特定好友 QQ 开关；默认仅限私聊生效，公共群聊与未授权用户 100% 不可见、不注入、不结算。
"""
import os
import json
import time
import math
import hashlib
import datetime
from typing import Any, Dict, List, Optional, Tuple
from config import LIBIDO_STATE_FILE, MASTER_QQ

HOUR_MS = 3600 * 1000
DAY_MS = 24 * HOUR_MS
LOCAL_OFFSET_MS = 8 * HOUR_MS

TIERS = ["平静", "有点想", "难耐", "快撑不住", "发情"]

# 五档原版说话方式（0和谐原版：说什么 / 怎么说 / 不要）
TIER_VOICE: Dict[str, Dict[str, str]] = {
    "平静": {
        "say": "不提这件事，也不会往那边带。",
        "how": "正常语气，短句，先给结论再给理由。",
        "dont": "不主动贴近，收尾不撒娇。",
    },
    "有点想": {
        "say": "会自己起一个话题，但那个话题跟这件事没关系。",
        "how": "多问一句「你呢」；句末会拖长半个音。",
        "dont": "不承认自己是在找话说。",
    },
    "难耐": {
        "say": "话变少，会重复对方刚说过的话；手边的事会停一下。",
        "how": "结论还是先给，但理由会绕。",
        "dont": "不主动说想要。",
    },
    "快撑不住": {
        "say": "会自己起个头，说到一半停住。",
        "how": "句号变少、逗号变多；同一个词会重复。",
        "dont": "不装作没事。",
    },
    "发情": {
        "say": "如果时机合适，可以自己开口，不用等对方问。",
        "how": "短句，不解释，不铺垫。",
        "dont": "不装作不想要。",
    },
}

WAKING_VOICE: Dict[str, str] = {
    "say": "刚醒不久，先聊手头的话题，不主动提这件事；对方主动提起时可以正常回应。",
    "how": "语气略带刚睡醒的懒散，自然随性。",
    "dont": "不刻意往那边带。",
}

# 快捷预设事件表（来自原版发情设定.md）
PRESET_EVENTS: List[Dict[str, Any]] = [
    {"id": "tail", "label": "摸尾巴 / 蹭猫耳", "delta": 8, "note": "摸尾巴"},
    {"id": "praise", "label": "被盯着看 / 被夸", "delta": 3, "note": "被盯着看、被夸"},
    {"id": "tease", "label": "亲昵称呼 / 调戏", "delta": 5, "note": "被亲昵调戏"},
    {"id": "peek", "label": "自己看了不该看的", "delta": 6, "note": "自己看了不该看的"},
    {"id": "close", "label": "挨得近 / 被碰到", "delta": 10, "note": "挨得近、被碰到"},
    {"id": "start", "label": "真的开始", "delta": 15, "note": "真的开始"},
    {"id": "ignored", "label": "被冷落 / 被忽视", "delta": -8, "note": "被冷落、被忽视"},
    {"id": "left", "label": "说到一半人走了", "delta": -6, "note": "一句话说到一半人走了"},
    {"id": "broken", "label": "答应的事没做到", "delta": -10, "note": "答应过的事没做到"},
]


def _clamp(val: int) -> int:
    return max(0, min(100, int(round(val))))


def _now_ms() -> int:
    return int(time.time()) * 1000


def _iso_from_ms(ms: int) -> str:
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.fromtimestamp(ms / 1000.0, tz=tz).isoformat(timespec="seconds")


def _parse_iso_ms(iso_str: str, fallback_ms: int) -> int:
    if not iso_str:
        return fallback_ms
    try:
        dt = datetime.datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return fallback_ms


def _local_day_of(utc_ms: int) -> int:
    return (utc_ms + LOCAL_OFFSET_MS) // DAY_MS


def _local_date_of(utc_ms: int) -> str:
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.fromtimestamp(utc_ms / 1000.0, tz=tz).strftime("%Y-%m-%d")


def _default_state() -> dict:
    now = _now_ms()
    stamp = _iso_from_ms(now)
    return {
        "enabled": True,
        "private_only": True,
        "baseline": 12,
        "value": 18,
        "season_start_day": 18,
        "season_days": 3,
        "updatedAt": stamp,
        "wake": None,
        "tally": {},
        "note": "",
        "allowed_users": {
            str(MASTER_QQ): {
                "enabled": True,
                "label": "主人",
                "added_at": stamp,
            }
        },
        "events": [{"at": stamp, "delta": 0, "note": "立账"}],
        "samples": [{"at": stamp, "value": 18}],
    }


_CACHE_DATA: Optional[dict] = None
_CACHE_MTIME: float = -1.0


def load_libido_state(force: bool = False) -> dict:
    """跨进程 mtime 热读取 libido_state.json"""
    global _CACHE_DATA, _CACHE_MTIME
    if not os.path.exists(LIBIDO_STATE_FILE):
        st = _default_state()
        save_libido_state(st)
        return st

    try:
        mtime = os.path.getmtime(LIBIDO_STATE_FILE)
        if not force and _CACHE_DATA is not None and mtime == _CACHE_MTIME:
            return json.loads(json.dumps(_CACHE_DATA))
        with open(LIBIDO_STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = _default_state()
        defaults = _default_state()
        for k, v in defaults.items():
            raw.setdefault(k, v)
        if not isinstance(raw.get("allowed_users"), dict):
            raw["allowed_users"] = defaults["allowed_users"]
        _CACHE_DATA = raw
        _CACHE_MTIME = mtime
        return json.loads(json.dumps(raw))
    except Exception:
        return _default_state()


def save_libido_state(data: dict) -> None:
    """原子落盘至 libido_state.json"""
    global _CACHE_DATA, _CACHE_MTIME
    os.makedirs(os.path.dirname(os.path.abspath(LIBIDO_STATE_FILE)), exist_ok=True)
    tmp = f"{LIBIDO_STATE_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIBIDO_STATE_FILE)
    _CACHE_DATA = json.loads(json.dumps(data))
    try:
        _CACHE_MTIME = os.path.getmtime(LIBIDO_STATE_FILE)
    except OSError:
        _CACHE_MTIME = -1.0


# ============================================================
# 权限与场景硬锁校验（仅主人及 WebUI 授权用户可启用）
# ============================================================
def is_libido_allowed(user_id_str: str, is_private: bool = True) -> bool:
    """检查目标用户在当前场景下是否被允许启用 dsh-libido-panel"""
    uid = str(user_id_str or "").strip()
    if not uid or uid in ("系统", "0"):
        return False
    st = load_libido_state()
    if not bool(st.get("enabled", True)):
        return False
    if bool(st.get("private_only", True)) and not is_private:
        return False
    allowed = st.get("allowed_users") or {}
    entry = allowed.get(uid)
    if isinstance(entry, dict):
        return bool(entry.get("enabled", False))
    if isinstance(entry, bool):
        return entry
    return False


# ============================================================
# 核心动力学计算（完全对齐 server.mjs：周期 / SHA256 不规则漂移 / 两段式睡醒）
# ============================================================
def _utc_day_ms(year: int, month: int, day: int) -> int:
    # 自动兼容月末边界（如 2 月 29/30 日）
    import calendar
    max_d = calendar.monthrange(year, month)[1]
    safe_day = max(1, min(day, max_d))
    dt = datetime.datetime(year, month, safe_day, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def season_bounds(local_day_ms: int, start_day: int = 18, days_len: int = 3) -> Dict[str, int]:
    dt = datetime.datetime.fromtimestamp(local_day_ms / 1000.0, tz=datetime.timezone.utc)
    y, m = dt.year, dt.month
    start = _utc_day_ms(y, m, start_day)
    end = start + max(1, int(days_len)) * DAY_MS - 1
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    next_start = _utc_day_ms(ny, nm, start_day)
    return {"start": start, "end": end, "next_start": next_start}


def season_at(local_day_ms: int, start_day: int = 18, days_len: int = 3) -> Dict[str, Any]:
    b = season_bounds(local_day_ms, start_day, days_len)
    if b["start"] <= local_day_ms <= b["end"]:
        day_idx = (local_day_ms - b["start"]) // DAY_MS + 1
        return {"active": True, "day": int(day_idx), "days_total": int(days_len)}
    nxt = b["start"] if local_day_ms < b["start"] else b["next_start"]
    days_until = max(1, int(math.ceil((nxt - local_day_ms) / float(DAY_MS))))
    return {"active": False, "days_until": days_until, "days_total": int(days_len)}


def _step_gap(day_ms: int, k: int, base_ms: float) -> float:
    h = hashlib.sha256(f"tide-step-v1:{day_ms}:{k}".encode("utf-8")).digest()
    u = int.from_bytes(h[:4], "big") / 4294967296.0
    return base_ms * (0.5 + u)


def drift_points(start_local_ms: int, end_local_ms: int, start_day: int = 18, days_len: int = 3) -> int:
    """计算 [start_local_ms, end_local_ms] 内清醒时段 (08:00–24:00) 的自然增长点数"""
    if end_local_ms <= start_local_ms:
        return 0
    points = 0
    day_ms = (start_local_ms // DAY_MS) * DAY_MS
    while day_ms < end_local_ms:
        seg_start = day_ms + 8 * HOUR_MS
        awake_start = max(start_local_ms, seg_start)
        awake_end = min(end_local_ms, day_ms + DAY_MS)
        if awake_end > awake_start:
            b = season_bounds(day_ms, start_day, days_len)
            speed = 4 if (b["start"] <= day_ms <= b["end"]) else 1
            base_ms = (2.0 * HOUR_MS) / speed
            at = float(seg_start)
            for k in range(400):
                at += _step_gap(day_ms, k, base_ms)
                if at > awake_end:
                    break
                if at > awake_start:
                    points += 1
        day_ms += DAY_MS
    return points


def wake_plan(before: int, baseline: int, at_ms: int) -> Dict[str, Any]:
    """每日确定性两段式睡醒结算：刚醒留 10%~20%，清醒后 30~90 分钟回涨 20%~35%"""
    day_idx = _local_day_of(at_ms)
    h = hashlib.sha256(f"tide-wake-v1:{day_idx}".encode("utf-8")).digest()

    def _draw(i: int, lo: int, hi: int) -> int:
        raw16 = int.from_bytes(h[i * 2: i * 2 + 2], "big")
        return lo + (raw16 * (hi - lo + 1)) // 65536

    excess = max(0, int(before) - int(baseline))
    waking_val = (int(baseline) + round(excess * _draw(0, 10, 20) / 100.0)) if excess > 0 else int(before)
    delay_min = _draw(2, 30, 90)
    gain = min(max(0, int(before) - waking_val), round(excess * _draw(1, 20, 35) / 100.0))
    return {
        "value": _clamp(waking_val),
        "wake": {
            "day": day_idx,
            "at": at_ms,
            "recallAt": at_ms + delay_min * 60 * 1000,
            "gain": int(max(0, gain)),
        },
    }


def _accepted_wake(wake: Any, anchor_ms: int) -> Optional[dict]:
    if not isinstance(wake, dict):
        return None
    try:
        at = int(wake["at"])
        day = int(wake["day"])
        recall_at = int(wake["recallAt"])
        gain = int(wake["gain"])
        if at <= anchor_ms + 1000 and day == _local_day_of(at) and at <= recall_at <= at + 90 * 60 * 1000 and 0 <= gain <= 100:
            return {"day": day, "at": at, "recallAt": recall_at, "gain": gain}
    except Exception:
        pass
    return None


def project_value(
    value: int,
    baseline: int,
    from_ms: int,
    to_ms: int,
    saved_wake: Optional[dict],
    start_day: int = 18,
    days_len: int = 3,
) -> Tuple[int, Optional[dict]]:
    wake = _accepted_wake(saved_wake, from_ms)
    current = _clamp(value)
    cursor = from_ms
    if to_ms <= from_ms:
        return current, wake

    def _advance(end_ms: int):
        nonlocal current, cursor
        recall = wake["gain"] if (wake and wake["recallAt"] > cursor and wake["recallAt"] <= end_ms) else 0
        d_pts = drift_points(cursor + LOCAL_OFFSET_MS, end_ms + LOCAL_OFFSET_MS, start_day, days_len)
        current = _clamp(current + recall + d_pts)
        cursor = end_ms

    next_wake = _local_day_of(cursor) * DAY_MS + 8 * HOUR_MS - LOCAL_OFFSET_MS
    if next_wake <= cursor:
        next_wake += DAY_MS

    span = int(math.ceil((to_ms - cursor) / float(DAY_MS))) + 2
    for _ in range(span):
        if next_wake > to_ms:
            break
        if wake and wake["day"] == _local_day_of(next_wake):
            next_wake += DAY_MS
            continue
        _advance(next_wake)
        plan = wake_plan(current, baseline, next_wake)
        current = plan["value"]
        wake = plan["wake"]
        next_wake += DAY_MS

    if cursor < to_ms:
        _advance(to_ms)
    return current, wake


def tier_of(value: int, season_active: bool) -> str:
    """根据当前值与是否处于发情期判定五档（发情期门槛降 15）"""
    offset = 15 if season_active else 0
    if value <= 20 - offset:
        return "平静"
    if value <= 45 - offset:
        return "有点想"
    if value <= 70 - offset:
        return "难耐"
    if value <= 89 - offset:
        return "快撑不住"
    return "发情"


def compute_current_state(data: Optional[dict] = None, now_ms: Optional[int] = None) -> dict:
    st = data if isinstance(data, dict) else load_libido_state()
    now = now_ms if now_ms is not None else _now_ms()
    baseline = _clamp(int(st.get("baseline", 12)))
    start_day = max(1, min(28, int(st.get("season_start_day", 18))))
    days_len = max(1, min(7, int(st.get("season_days", 3))))
    updated_ms = _parse_iso_ms(st.get("updatedAt", ""), now)

    current, wake = project_value(
        int(st.get("value", 18)),
        baseline,
        updated_ms,
        now,
        st.get("wake"),
        start_day=start_day,
        days_len=days_len,
    )
    local_day_ms = ((now + LOCAL_OFFSET_MS) // DAY_MS) * DAY_MS
    season = season_at(local_day_ms, start_day, days_len)
    tier = tier_of(current, bool(season.get("active")))
    waking = bool(wake and wake["at"] <= now < wake["recallAt"] and wake["gain"] > 0)

    return {
        "current": current,
        "baseline": baseline,
        "tier": tier,
        "season": season,
        "waking": waking,
        "wake": wake,
        "season_start_day": start_day,
        "season_days": days_len,
        "enabled": bool(st.get("enabled", True)),
        "private_only": bool(st.get("private_only", True)),
        "note": str(st.get("note", "") or ""),
        "now_ms": now,
    }


def build_libido_mood_line(view: Optional[dict] = None) -> str:
    """生成原版 0 和谐注入文本（对齐 server.mjs 的 moodLine）"""
    v = view if isinstance(view, dict) else compute_current_state()
    season = v["season"]
    if season.get("active"):
        season_str = f"发情期第 {season['day']} 天"
    else:
        season_str = f"距发情期 {season['days_until']} 天"

    voice = WAKING_VOICE if v.get("waking") else TIER_VOICE.get(v["tier"], TIER_VOICE["平静"])
    lines = [
        f"【当前性欲状态】{v['current']}/100 · {v['tier']} · {season_str}" + ("（刚醒）" if v.get("waking") else ""),
        f"【说什么】{voice['say']}",
        f"【怎么说】{voice['how']}",
        f"【不要】{voice['dont']}",
    ]
    if v["current"] >= 90 and not v.get("waking"):
        lines.append("【现在】会主动开口——这句要真说出口，不要只在心里。")
    if v.get("note"):
        lines.append(f"【近况便签】{v['note']}")
    lines.append("（注：数值只代表强度并体现在说话节奏上，不改变意愿与边界，严禁在对话中机械报出数字或系统标签。）")
    return "\n".join(lines)


def build_libido_injection_block(user_id_str: str, is_private: bool = True, is_continuation_turn: bool = False) -> str:
    """
    供 context_builder.py 调用的受权注入入口：
    - 未授权用户 / 公共群聊（当 private_only=True 时）/ 总开关关闭时，严格返回空字符串 ""；
    - 授权用户返回完整 0 和谐状态块。
    """
    if not is_libido_allowed(user_id_str, is_private=is_private):
        return ""
    view = compute_current_state()
    if is_continuation_turn:
        # 续轮紧凑版：保留状态与三行说话指引
        return build_libido_mood_line(view)
    return build_libido_mood_line(view)


# ============================================================
# 写入动作：事件记账（同日递减）、手动设值、解决归位、手动睡醒、权限与参数配置
# ============================================================
def apply_libido_event(delta: int, note: str = "", now_ms: Optional[int] = None) -> dict:
    """记入一次正/负事件：同日正向按 100% / 75% / 50% / 25% 递减，负向不打折"""
    st = load_libido_state(force=True)
    now = now_ms if now_ms is not None else _now_ms()
    stamp = _iso_from_ms(now)
    view = compute_current_state(st, now)
    before = view["current"]
    wake = view["wake"]

    raw_delta = int(delta)
    applied = raw_delta
    tally = st.get("tally") if isinstance(st.get("tally"), dict) else {}
    if raw_delta > 0:
        day_str = _local_date_of(now)
        bucket = tally.get(day_str) if isinstance(tally.get(day_str), dict) else {}
        key = str(note or "_").strip() or "_"
        n = int(bucket.get(key, 0))
        factor = (1.0, 0.75, 0.50)[n] if n < 3 else 0.25
        applied = max(1, int(round(raw_delta * factor)))
        bucket[key] = n + 1
        tally[day_str] = bucket
        # 只保留近 7 天 tally
        if len(tally) > 7:
            for old_k in sorted(tally.keys())[:-7]:
                tally.pop(old_k, None)

    after = _clamp(before + applied)
    st["value"] = after
    st["updatedAt"] = stamp
    st["tally"] = tally
    st["wake"] = wake
    events = list(st.get("events") or [])
    events.insert(0, {"at": stamp, "delta": after - before, "raw_delta": raw_delta, "note": str(note or f"{applied:+d}")})
    st["events"] = events[:200]
    samples = list(st.get("samples") or [])
    samples.append({"at": stamp, "value": after, "wake": wake})
    st["samples"] = samples[-300:]
    save_libido_state(st)
    return {"ok": True, "before": before, "after": after, "applied": after - before}


def trigger_resolve(note: str = "解决一次", now_ms: Optional[int] = None) -> dict:
    """解决一次：回到初始值并取消尚未发生的睡醒回涨"""
    st = load_libido_state(force=True)
    now = now_ms if now_ms is not None else _now_ms()
    stamp = _iso_from_ms(now)
    view = compute_current_state(st, now)
    before = view["current"]
    after = _clamp(int(st.get("baseline", 12)))
    wake = view["wake"]
    if wake and wake.get("recallAt", 0) > now:
        wake = {**wake, "gain": 0}

    st["value"] = after
    st["updatedAt"] = stamp
    st["wake"] = wake
    events = list(st.get("events") or [])
    events.insert(0, {"at": stamp, "delta": after - before, "note": note or "解决一次"})
    st["events"] = events[:200]
    samples = list(st.get("samples") or [])
    samples.append({"at": stamp, "value": after, "wake": wake})
    st["samples"] = samples[-300:]
    save_libido_state(st)
    return {"ok": True, "before": before, "after": after}


def set_libido_value(target_value: int, note: str = "手动调整", now_ms: Optional[int] = None) -> dict:
    """手动直接设定数值 (0~100)，并取消尚未发生的回涨"""
    st = load_libido_state(force=True)
    now = now_ms if now_ms is not None else _now_ms()
    stamp = _iso_from_ms(now)
    view = compute_current_state(st, now)
    before = view["current"]
    after = _clamp(int(target_value))
    wake = view["wake"]
    if wake and wake.get("recallAt", 0) > now:
        wake = {**wake, "gain": 0}

    st["value"] = after
    st["updatedAt"] = stamp
    st["wake"] = wake
    events = list(st.get("events") or [])
    events.insert(0, {"at": stamp, "delta": after - before, "note": note or f"手动调整至 {after}"})
    st["events"] = events[:200]
    samples = list(st.get("samples") or [])
    samples.append({"at": stamp, "value": after, "wake": wake})
    st["samples"] = samples[-300:]
    save_libido_state(st)
    return {"ok": True, "before": before, "after": after}


def trigger_manual_wake(note: str = "手动睡醒结算", now_ms: Optional[int] = None) -> dict:
    """手动触发当日睡醒两段结算（同一本地日期不会重复结算）"""
    st = load_libido_state(force=True)
    now = now_ms if now_ms is not None else _now_ms()
    stamp = _iso_from_ms(now)
    view = compute_current_state(st, now)
    wake = view["wake"]
    if wake and wake.get("day") == _local_day_of(now):
        return {"ok": False, "skipped": True, "message": "今天的睡醒已经结算过啦", "current": view["current"]}

    plan = wake_plan(view["current"], view["baseline"], now)
    after = plan["value"]
    st["value"] = after
    st["updatedAt"] = stamp
    st["wake"] = plan["wake"]
    events = list(st.get("events") or [])
    events.insert(0, {"at": stamp, "delta": after - view["current"], "note": note or "刚醒 · 清醒后可能回涨"})
    st["events"] = events[:200]
    samples = list(st.get("samples") or [])
    samples.append({"at": stamp, "value": after, "wake": plan["wake"]})
    st["samples"] = samples[-300:]
    save_libido_state(st)
    return {"ok": True, "before": view["current"], "after": after, "wake": plan["wake"]}


def manage_libido_user(action: str, user_id: str, label: str = "") -> dict:
    """管理 WebUI 授权用户白名单开关：toggle | enable | disable | remove"""
    uid = str(user_id or "").strip()
    if not uid or not uid.isdigit():
        return {"ok": False, "error": "请输入有效的 QQ 号"}
    st = load_libido_state(force=True)
    allowed = st.setdefault("allowed_users", {})
    act = str(action or "toggle").strip().lower()
    cur = allowed.get(uid)
    cur_on = bool(cur.get("enabled", False)) if isinstance(cur, dict) else bool(cur)

    if act == "remove":
        if uid == str(MASTER_QQ):
            allowed[uid] = {"enabled": False, "label": "主人", "added_at": _iso_from_ms(_now_ms())}
        else:
            allowed.pop(uid, None)
    else:
        target = (not cur_on) if act == "toggle" else (act in ("enable", "on", "add"))
        old_lbl = cur.get("label", "") if isinstance(cur, dict) else ""
        old_at = cur.get("added_at", "") if isinstance(cur, dict) else ""
        allowed[uid] = {
            "enabled": target,
            "label": str(label or old_lbl or ("主人" if uid == str(MASTER_QQ) else uid)),
            "added_at": old_at or _iso_from_ms(_now_ms()),
        }
    save_libido_state(st)
    return {"ok": True, "allowed_users": allowed}


def update_libido_settings(
    enabled: Optional[bool] = None,
    private_only: Optional[bool] = None,
    baseline: Optional[int] = None,
    season_start_day: Optional[int] = None,
    season_days: Optional[int] = None,
    note: Optional[str] = None,
) -> dict:
    st = load_libido_state(force=True)
    if enabled is not None:
        st["enabled"] = bool(enabled)
    if private_only is not None:
        st["private_only"] = bool(private_only)
    if baseline is not None:
        st["baseline"] = _clamp(int(baseline))
    if season_start_day is not None:
        st["season_start_day"] = max(1, min(28, int(season_start_day)))
    if season_days is not None:
        st["season_days"] = max(1, min(7, int(season_days)))
    if note is not None:
        st["note"] = str(note).strip()[:120]
    save_libido_state(st)
    return {"ok": True}


def build_72h_history(st: dict, view: dict) -> List[Dict[str, Any]]:
    """生成过去 72 小时逐小时曲线（73 个采样点），供 WebUI ECharts 渲染"""
    now = view["now_ms"]
    updated_ms = _parse_iso_ms(st.get("updatedAt", ""), now)
    anchors = [{"time": updated_ms, "value": int(st.get("value", 18)), "wake": st.get("wake")}]
    for s in (st.get("samples") or []):
        if isinstance(s, dict):
            t = _parse_iso_ms(s.get("at", ""), -1)
            v = s.get("value")
            if t > 0 and isinstance(v, (int, float)):
                anchors.append({"time": t, "value": int(v), "wake": s.get("wake")})
    anchors.sort(key=lambda x: x["time"])

    points = []
    tz = datetime.timezone(datetime.timedelta(hours=8))
    for i in range(73):
        t = now - (72 - i) * HOUR_MS
        lbl = datetime.datetime.fromtimestamp(t / 1000.0, tz=tz).strftime("%m-%d %H:%M")
        if i == 72:
            val = view["current"]
        else:
            anchor = None
            for a in anchors:
                if a["time"] <= t:
                    anchor = a
                else:
                    break
            if anchor is None:
                val = None
            else:
                val, _ = project_value(
                    anchor["value"],
                    view["baseline"],
                    anchor["time"],
                    t,
                    anchor.get("wake"),
                    start_day=view["season_start_day"],
                    days_len=view["season_days"],
                )
        points.append({"label": lbl, "value": val})
    return points
