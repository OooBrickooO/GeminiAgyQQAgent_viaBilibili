"""
webui/api/mood_api.py — 弥酱连续情绪流与 7 维状态机 API
"""
import time
import math
import datetime
from social.mood_manager import (
    load_mood_state,
    get_decayed_mood_state,
    BASELINE_MOOD,
    HALF_LIFE_MOOD_SEC,
    HALF_LIFE_DIM_SEC,
    DIM_KEYS,
    DIM_CN_NAMES
)


def get_mood_details() -> dict:
    raw_state = load_mood_state()
    decayed_state, elapsed_sec = get_decayed_mood_state()

    raw_dims = raw_state.get("dims", {})
    decayed_dims = decayed_state.get("dims", {})

    dim_list = []
    radar_indicators = []
    radar_decayed_values = []
    radar_raw_values = []

    for k in DIM_KEYS:
        cn_name = DIM_CN_NAMES.get(k, k)
        val_decayed = decayed_dims.get(k, 0.0)
        val_raw = raw_dims.get(k, 0.0)
        dim_list.append({
            "key": k,
            "name": cn_name,
            "value": val_decayed,
            "raw_value": val_raw,
            "percentage": min(100.0, max(0.0, val_decayed))
        })
        radar_indicators.append({"name": cn_name, "max": 100})
        radar_decayed_values.append(val_decayed)
        radar_raw_values.append(val_raw)

    # 衰减率计算
    f_mood = math.pow(0.5, elapsed_sec / HALF_LIFE_MOOD_SEC) if elapsed_sec > 0 else 1.0
    f_dim = math.pow(0.5, elapsed_sec / HALF_LIFE_DIM_SEC) if elapsed_sec > 0 else 1.0

    # 构造 24 小时情绪心电图 (EKG) 时间序列数据（优先基于真实历史脉冲轨迹 + 5h 指数衰减插值）
    history_points = []
    now_ts = time.time()
    raw_history = sorted(
        [
            {"ts": float(h["ts"]), "mood": float(h["mood"])}
            for h in (raw_state.get("history") or [])
            if isinstance(h, dict) and "ts" in h and "mood" in h
        ],
        key=lambda x: x["ts"]
    )
    # 兼容尚未积累 history 数组的旧状态文件：将 updated_at 作为真实脉冲锚点加入
    last_upd = float(raw_state.get("updated_at", now_ts))
    last_raw_mood = float(raw_state.get("mood", BASELINE_MOOD))
    if not raw_history or abs(raw_history[-1]["ts"] - last_upd) > 5.0:
        raw_history.append({"ts": last_upd, "mood": last_raw_mood})
        raw_history.sort(key=lambda x: x["ts"])

    for i in range(24):
        offset_hours = 23 - i
        t_end = now_ts - offset_hours * 3600
        t_start = t_end - 3600
        t_dt = datetime.datetime.fromtimestamp(t_end, tz=datetime.timezone.utc) + datetime.timedelta(hours=8)
        time_str = t_dt.strftime("%H:00")

        if offset_hours == 0:
            m_val = decayed_state.get("mood", BASELINE_MOOD)
        else:
            # 1. 检查该 1 小时窗口 (t_start, t_end] 内是否发生过真实情绪脉冲峰值/谷值
            bucket_snaps = [h for h in raw_history if t_start < h["ts"] <= t_end]
            if bucket_snaps:
                # 取该小时段内偏离基准线最显著的脉冲点，真实还原心电图峰谷！
                peak_snap = max(bucket_snaps, key=lambda h: abs(h["mood"] - BASELINE_MOOD))
                m_val = round(peak_snap["mood"], 1)
            else:
                # 2. 查找 t_end 之前最近的一次真实情绪快照，按 5h 半衰期计算真实指数回落轨迹
                prior_snaps = [h for h in raw_history if h["ts"] <= t_end]
                if prior_snaps:
                    anchor = prior_snaps[-1]
                    dt_sec = max(0.0, t_end - anchor["ts"])
                    decay_f = math.pow(0.5, dt_sec / HALF_LIFE_MOOD_SEC)
                    m_val = round(BASELINE_MOOD + (anchor["mood"] - BASELINE_MOOD) * decay_f, 1)
                else:
                    # 3. 早于首个记录点的时间段：围绕基准线呈现自然呼吸节律，并在脉冲前夕体现灵动起伏
                    first_snap = raw_history[0]
                    dt_before = max(0.0, first_snap["ts"] - t_end)
                    hour_of_day = t_dt.hour
                    wave = 2.8 * math.sin((hour_of_day - 9) / 12.0 * math.pi) + 1.4 * math.cos(hour_of_day * 0.9)
                    if dt_before <= 2.5 * 3600:
                        pre_w = math.exp(-dt_before / 3600.0) * 0.45
                        m_val = round((1.0 - pre_w) * (BASELINE_MOOD + wave) + pre_w * first_snap["mood"], 1)
                    else:
                        m_val = round(BASELINE_MOOD + wave, 1)

        history_points.append({
            "time": time_str,
            "mood": max(0.0, min(100.0, m_val)),
            "baseline": BASELINE_MOOD
        })

    return {
        "success": True,
        "mood": {
            "current": decayed_state.get("mood", BASELINE_MOOD),
            "raw": raw_state.get("mood", BASELINE_MOOD),
            "baseline": BASELINE_MOOD,
            "last_reason": decayed_state.get("last_reason", "") or "心情平静空白（对熟人热情，对陌生人正常自然）",
            "last_source_scene": decayed_state.get("last_source_scene", "") or "日常陪伴",
            "updated_at": raw_state.get("updated_at", now_ts),
            "elapsed_seconds": int(elapsed_sec),
            "pulse_consumed": decayed_state.get("pulse_consumed", True),
            "half_life_mood_hours": HALF_LIFE_MOOD_SEC / 3600.0,
            "half_life_dim_minutes": HALF_LIFE_DIM_SEC / 60.0,
            "decay_factor_mood": round(f_mood, 4),
            "decay_factor_dim": round(f_dim, 4)
        },
        "dims": dim_list,
        "radar": {
            "indicators": radar_indicators,
            "decayed_values": radar_decayed_values,
            "raw_values": radar_raw_values
        },
        "timeline_ekg": history_points
    }

