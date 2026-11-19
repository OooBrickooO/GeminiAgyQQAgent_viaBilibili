"""
social/affection.py — 长期好感与羁绊成长体系 V3.5 (朋友稳态区 + 挚友850殿堂门槛 + 双版本调节)

核心机制：
1. 阶梯划分：
   - ⛔ 黑名单 (< 0)
   - 🌱 路人 (0 ~ 199，软保底 0.0)
   - ✨ 熟人 (200 ~ 499，初识12次定级默认落点 220~270，软保底 200.0)
   - 🤝 朋友 (500 ~ 849，主力稳态区，软保底 500.0；其中 700~849 为冲击挚友的高山阻尼区)
   - 💖 挚友 (850 ~ 1000，稀有殿堂级，软保底 820.0，需通过 849.0 双重硬性羁绊锁方可晋升)
2. 告别原版“每日10%总额乘法斩杀”：
   - 当日有互动 = 0 衰减！
   - 当日无互动 = 温和固定小额回落（不跌破各阶梯软保底；700+ 无深度共鸣时向 650.0 朋友稳态吸引子微退）。
3. 慢车道全自动结算 (`settle_turn_affection`)：
   - 由事后异步内省观察者按峰值优先原则将情绪共鸣转化为好感增量，700分以上纯水群基础陪伴分清零 (Base=0)，仅深度共鸣可按 0.80x 推进。
4. 双版本手动调节：
   - 弥酱版：`bot_self_adjust_affection` (±5%自主微调，每日加分限2次) / `submit_affection_request` (大额向主人申请)
   - 主人版：`master_manage_affection` (无限制直调、审批申请、高光调整、破格授信)
"""
import os
import re
import json
import math
import random
import datetime
from config import PROFILES_FILE, BOT_QQ, MASTER_QQ

# 内存中的用户画像缓存
USER_PROFILES = {}


def _beijing_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)


def load_profiles():
    """从磁盘加载用户画像"""
    global USER_PROFILES
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                USER_PROFILES = json.load(f)
        except Exception:
            USER_PROFILES = {}


def save_profiles():
    """将用户画像原子持久化到磁盘"""
    try:
        tmp_path = f"{PROFILES_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(USER_PROFILES, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, PROFILES_FILE)
    except Exception as e:
        print(f"⚠️ 保存用户画像失败: {e}")


def check_best_friend_gate(profile: dict, user_id_str: str) -> tuple[bool, str]:
    """
    校验是否满足从【朋友 (849.0)】晋升为【💖挚友 (>=850.0)】的双重硬性羁绊门槛：
    1. 时间与了解厚度：等级 >= Lv.35 (或互动次数 >= 80)，相识满 21 天，且 L3 永久记忆库中专属有效事实 >= 5 条；
    2. 高光情感里程碑：累计高光情绪共鸣次数 (high_resonance_count) >= 3 次，或获得主人显式批准 (master_approved_bff)。
    """
    if str(user_id_str) == str(MASTER_QQ):
        return True, "主人无条件满羁绊"

    if profile.get("master_approved_bff", False):
        return True, "已获主人显式授信"

    level = int(profile.get("level", 0))
    icount = int(profile.get("interaction_count", 0))
    high_res = int(profile.get("high_resonance_count", 0))

    # 计算建档相识天数
    now_dt = _beijing_now()
    first_date_str = profile.get("first_seen_date", "")
    days_known = 30  # 老用户默认视为已相识足够天数
    if first_date_str:
        try:
            first_dt = datetime.datetime.strptime(first_date_str, "%Y-%m-%d").date()
            days_known = max(0, (now_dt.date() - first_dt).days)
        except Exception:
            days_known = 30

    # 查询 L3 永久记忆条数
    fact_cnt = 0
    try:
        from social.long_term_memory import count_user_permanent_facts
        fact_cnt = count_user_permanent_facts(user_id_str)
    except Exception:
        pass

    missing = []
    if level < 35 and icount < 80:
        missing.append(f"互动厚度不足(当前Lv.{level}/需Lv.35)")
    if days_known < 21:
        missing.append(f"相识天数不足({days_known}/21天)")
    if fact_cnt < 5:
        missing.append(f"深度了解事实不足({fact_cnt}/5条L3永久记忆)")
    if high_res < 3:
        missing.append(f"高光共鸣里程碑不足({high_res}/3次)")

    if missing:
        return False, "；".join(missing)
    return True, "已达成全部挚友羁绊硬门槛"


def _apply_daily_settlement_for_profile(uid_str: str, profile: dict, today: str, now_str: str):
    """单用户跨天结算：活跃免衰减 + 未互动阶梯软保底温和回落"""
    # 经验温和维护
    exp_val = float(profile.get("exp", 0.0))
    profile["level"] = min(100, int(math.sqrt(exp_val * 2)))

    current_aff = float(profile.get("affection", 0.0))
    today_interacted = bool(profile.get("today_interacted", False)) or (float(profile.get("today_gain", 0.0)) > 0.0)
    today_deep_res = bool(profile.get("today_deep_resonance", False))

    if uid_str != str(MASTER_QQ) and profile.get("is_calibrated", False):
        if current_aff >= 850.0:
            # 挚友殿堂区 (软保底 820.0)：当日有互动 0 衰减，无互动微退 1.5
            if not today_interacted:
                current_aff = max(820.0, round(current_aff - 1.5, 1))
        elif current_aff >= 700.0:
            # 700~849 高山阻尼区：若当日无深度共鸣，以 -2.5/天 向 650.0 朋友稳态吸引子自然回落
            if not today_deep_res:
                current_aff = max(650.0, round(current_aff - 2.5, 1))
        elif current_aff >= 500.0:
            # 500~699 朋友主力稳态区 (软保底 500.0)：当日有互动 0 衰减，无互动仅 -2.0/天
            if not today_interacted:
                current_aff = max(500.0, round(current_aff - 2.0, 1))
        elif current_aff >= 200.0:
            # 200~499 熟人区 (软保底 200.0)：当日有互动 0 衰减，无互动仅 -3.0/天
            if not today_interacted:
                current_aff = max(200.0, round(current_aff - 3.0, 1))
        elif current_aff > 0.0:
            # 0~199 路人区：未互动 -2.0/天
            if not today_interacted:
                current_aff = max(0.0, round(current_aff - 2.0, 1))

        profile["affection"] = current_aff

    profile["today_gain"] = 0.0
    profile["today_interacted"] = False
    profile["today_deep_resonance"] = False
    profile["today_self_bonus_count"] = 0
    profile["last_date"] = today

    # 清理过期心事
    valid_thoughts = [
        t for t in profile.get("pending_thoughts", [])
        if t.get("expire_at", "9999-99-99") >= now_str and t.get("status") != "resolved"
    ]
    profile["pending_thoughts"] = valid_thoughts


def get_user_profile(user_id_str: str) -> dict:
    """获取用户画像，不存在则创建默认画像；跨天时自动执行新制稳态结算"""
    load_profiles()
    now_dt = _beijing_now()
    today = now_dt.strftime("%Y-%m-%d")
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")

    uid_str = str(user_id_str)
    if uid_str not in USER_PROFILES:
        USER_PROFILES[uid_str] = {
            "exp": 0.0,
            "affection": 0.0,
            "today_gain": 0.0,
            "today_interacted": False,
            "today_deep_resonance": False,
            "today_self_bonus_count": 0,
            "level": 0,
            "interaction_count": 0,
            "is_calibrated": False,
            "first_impression_points": 0.0,
            "high_resonance_count": 0,
            "pending_affection_request": None,
            "first_seen_date": today,
            "pending_thoughts": [],
            "last_date": today
        }
    profile = USER_PROFILES[uid_str]
    if "pending_thoughts" not in profile:
        profile["pending_thoughts"] = []
    if "high_resonance_count" not in profile:
        profile["high_resonance_count"] = 0
    if "today_self_bonus_count" not in profile:
        profile["today_self_bonus_count"] = 0
    if "pending_affection_request" not in profile:
        profile["pending_affection_request"] = None

    if profile.get("last_date") != today:
        _apply_daily_settlement_for_profile(uid_str, profile, today, now_str)
        save_profiles()
    return profile


def add_pending_thought(user_id_str: str, thought: str, expire_days: int = 3, source_scene: str = "") -> dict:
    """为指定用户添加一条挂起心事/暗暗惦记的小事（记录来源通道，实现群聊/私聊隔离）"""
    uid_str = str(user_id_str)
    p = get_user_profile(uid_str)
    now_dt = _beijing_now()
    expire_dt = now_dt + datetime.timedelta(days=expire_days)
    clean_t = (thought or "").strip()
    if not clean_t:
        return {}

    clean_scene = str(source_scene or "").strip()
    if not clean_scene:
        lock_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
        lock_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
        if lock_type == "private":
            clean_scene = f"private_{lock_id}" if lock_id else "private"
        elif lock_type == "group" and lock_id:
            clean_scene = f"group_{lock_id}"

    # 防重复
    for existing in p["pending_thoughts"]:
        if existing.get("thought") == clean_t:
            if clean_scene and not existing.get("source_scene"):
                existing["source_scene"] = clean_scene
                save_profiles()
            return existing

    item = {
        "id": f"pt_{int(now_dt.timestamp())}_{random.randint(100, 999)}",
        "thought": clean_t,
        "source_scene": clean_scene,
        "created_at": now_dt.strftime("%Y-%m-%d %H:%M"),
        "expire_at": expire_dt.strftime("%Y-%m-%d %H:%M"),
        "status": "pending"
    }
    p["pending_thoughts"].append(item)
    p["pending_thoughts"] = p["pending_thoughts"][-5:]
    save_profiles()
    return item


def resolve_pending_thought(user_id_str: str, thought_id_or_keyword: str) -> bool:
    """将心里挂起的心事标记为已结/移除（支持精确ID、子串、以及双字重叠加权匹配）"""
    uid_str = str(user_id_str)
    p = get_user_profile(uid_str)
    thoughts = p.get("pending_thoughts", [])
    if not thoughts:
        return False

    target = (thought_id_or_keyword or "").strip().lower()
    if not target:
        return False
    matched_idx = -1
    # 1. 优先精确 ID 或直接子串匹配
    for idx, item in enumerate(thoughts):
        item_id = str(item.get("id", "")).strip().lower()
        thought_text = str(item.get("thought", "")).lower()
        if item_id == target or target in thought_text:
            matched_idx = idx
            break

    # 2. 模糊回退：剥离常见完结词后缀后再试子串，或按字符 bigram / 重叠率 (>=0.55) 匹配
    if matched_idx < 0 and len(target) >= 2:
        t_clean = re.sub(r"[\s，。！？、,.!?~—…\-_]+", "", target)
        t_core = re.sub(r"(?:已完成|已搞定|完成|完毕|搞定|做好|弄好|好了|结束|兑现|取消|那件事|的事|了)$", "", t_clean)
        if len(t_core) >= 2:
            for idx, item in enumerate(thoughts):
                if t_core in str(item.get("thought", "")).lower():
                    matched_idx = idx
                    break

    if matched_idx < 0 and len(target) >= 2:
        t_clean = re.sub(r"[\s，。！？、,.!?~—…\-_]+", "", target)
        t_bigrams = {t_clean[i:i+2] for i in range(max(0, len(t_clean) - 1))} if len(t_clean) >= 2 else set(t_clean)
        best_score = 0.0
        for idx, item in enumerate(thoughts):
            th_clean = re.sub(r"[\s，。！？、,.!?~—…\-_]+", "", str(item.get("thought", "")).lower())
            if not th_clean:
                continue
            th_bigrams = {th_clean[i:i+2] for i in range(max(0, len(th_clean) - 1))} if len(th_clean) >= 2 else set(th_clean)
            union_base = max(1, min(len(t_bigrams), len(th_bigrams)))
            bigram_score = len(t_bigrams & th_bigrams) / union_base if t_bigrams else 0.0
            char_score = len(set(t_clean) & set(th_clean)) / max(1, len(set(t_clean)))
            score = max(bigram_score, char_score * 0.85)
            if score >= 0.55 and score > best_score:
                best_score = score
                matched_idx = idx

    if matched_idx >= 0:
        p["pending_thoughts"] = [item for idx, item in enumerate(thoughts) if idx != matched_idx]
        save_profiles()
        return True
    return False


def get_active_pending_thoughts(user_id_str: str, current_scene: str = "") -> list:
    """
    获取指定用户当前有效且未过期的心事列表（支持按来源通道过滤）：
    - 当 current_scene 为群聊 ('group_XXX') 时：仅返回在当前群产生的心事，严禁将私聊或其他群的约定泄露到本群；
    - 当 current_scene 为私聊 ('private_XXX') 或未指定时：可返回该用户的全部有效心事。
    """
    uid_str = str(user_id_str)
    load_profiles()
    p = USER_PROFILES.get(uid_str, {})
    thoughts = p.get("pending_thoughts", [])
    if not thoughts:
        return []

    now_str = _beijing_now().strftime("%Y-%m-%d %H:%M")
    valid = []
    changed = False
    for item in thoughts:
        if item.get("expire_at", "9999-99-99") >= now_str and item.get("status") != "resolved":
            valid.append(item)
        else:
            changed = True

    if changed:
        p["pending_thoughts"] = valid
        save_profiles()

    scene_str = str(current_scene or "").strip()
    if scene_str.startswith("group_"):
        return [item for item in valid if str(item.get("source_scene", "")).strip() == scene_str]

    return valid


def get_user_level(exp: float) -> int:
    """计算等级"""
    return min(100, int(math.sqrt(max(0.0, exp) * 2)))


def add_user_exp(user_id_str: str, amount: float = 1.0):
    """增加用户活跃经验"""
    if str(user_id_str) == str(BOT_QQ):
        return
    p = get_user_profile(str(user_id_str))
    p["exp"] = float(p.get("exp", 0.0)) + amount
    p["level"] = get_user_level(p["exp"])
    save_profiles()


def add_user_affection(user_id_str: str, amount: float, raw_message: str = ""):
    """
    快车道入队时的基础陪伴打卡：
    - 标记今日已互动 (`today_interacted = True`)，免除当日衰减；
    - 初识前 12 次互动累计 `interaction_count` 与印象分；
    - 已定级且 `< 700` 分的用户先记入基础陪伴分 `Base = +1.5`（700+ 高山阻尼区水群基础分归零，交由慢车道观察者按共鸣结算）。
    """
    uid_str = str(user_id_str)
    if uid_str == str(BOT_QQ):
        return
    p = get_user_profile(uid_str)
    p["today_interacted"] = True
    p["interaction_count"] = int(p.get("interaction_count", 0)) + 1

    # 初识 12 次观察期
    if not p.get("is_calibrated", False):
        pos_words = ["可爱", "贴贴", "摸摸", "投喂", "喜欢你", "喜欢", "抱抱", "夸", "大佬", "谢谢", "好玩", "开心", "厉害"]
        neg_words = ["傻逼", "弱智", "滚", "闭嘴", "恶心", "废物", "死", "脑瘫", "草泥马", "垃圾", "智障"]

        is_neg = any(w in raw_message for w in neg_words)
        is_pos = any(w in raw_message for w in pos_words)

        if is_neg:
            p["first_impression_points"] = float(p.get("first_impression_points", 0.0)) - 25.0
            p["has_bad_behavior"] = True
        elif is_pos:
            p["first_impression_points"] = float(p.get("first_impression_points", 0.0)) + 18.0
        else:
            p["first_impression_points"] = float(p.get("first_impression_points", 0.0)) + 8.0

        if p["interaction_count"] >= 12:
            pts = float(p.get("first_impression_points", 0.0))
            has_bad = p.get("has_bad_behavior", False)
            if pts < 0:
                initial_aff = max(-10.0, pts)
            elif has_bad:
                initial_aff = min(180.0, max(0.0, 50.0 + pts * 0.5))
            else:
                initial_aff = min(280.0, max(220.0, 200.0 + pts * 0.7))

            p["affection"] = round(initial_aff, 1)
            p["is_calibrated"] = True
            print(f"[+] [初识自动定级] 用户 {uid_str} 满 12 次互动完成定级：好感度 {p['affection']}")
        save_profiles()
        return

    current_aff = float(p.get("affection", 0.0))
    # 700 分以上进入高山阻尼区：普通发言基础分 Base = 0.0，只靠慢车道深度共鸣加分
    if current_aff >= 700.0:
        save_profiles()
        return

    # 700 分以下享受基础陪伴分 Base = +1.5 (受阶段沉淀倍率与每日上限 28.0 约束)
    mult = 1.0 if current_aff < 350.0 else (0.90 if current_aff < 500.0 else 0.85)
    base_gain = round(1.5 * mult, 2)
    today_gain = float(p.get("today_gain", 0.0))
    rem = max(0.0, 28.0 - today_gain)
    actual = min(base_gain, rem)
    if actual > 0:
        p["affection"] = round(min(700.0, current_aff + actual), 1)
        p["today_gain"] = round(today_gain + actual, 2)
    save_profiles()


def settle_turn_affection(
    user_id_str: str,
    mood_resonance: float,
    calibrate_score: float = None,
    reason: str = ""
) -> dict:
    """
    由慢车道事后观察者 (`workers/post_turn_observer.py`) 100% 自动调用的好感结算函数：
    - 将本轮对话激发的 `mood_resonance` (-15.0 ~ +8.0) 自动转化为长期好感度变化；
    - 初识期的高光共鸣 (`res >= 6.0`) 同样正常计入 `high_resonance_count` 里程碑；
    - 在 700~849 高山阻尼区，仅当 `mood_resonance >= 3.5`（真情实感的深度共鸣）时才按 `0.80x` 推进；
    - 在 849.0 处严格执行 `check_best_friend_gate` 双重硬性晋升锁！
    """
    uid_str = str(user_id_str)
    if uid_str in (str(BOT_QQ), "系统", ""):
        return {}

    p = get_user_profile(uid_str)
    p["today_interacted"] = True

    res = max(-20.0, min(10.0, float(mood_resonance or 0.0)))

    # 优先记录高光共鸣里程碑 (单次/峰值共鸣 >= 6.0)，初识期触发的高光同样记入
    resonance_updated = False
    if res >= 6.0:
        p["high_resonance_count"] = int(p.get("high_resonance_count", 0)) + 1
        p["today_deep_resonance"] = True
        resonance_updated = True
        print(f"✨ [高光共鸣里程碑] 用户 {uid_str} 累计高光共鸣达到 {p['high_resonance_count']} 次 ({reason})")
    elif res >= 3.5:
        p["today_deep_resonance"] = True
        resonance_updated = True

    # 1. 若观察者给出了明确的初识定级分且尚未定级
    if not p.get("is_calibrated", False) and calibrate_score is not None and p.get("interaction_count", 0) >= 10:
        bounded = round(max(-10.0, min(290.0, float(calibrate_score))), 1)
        p["affection"] = bounded
        p["is_calibrated"] = True
        p["calibration_reason"] = reason
        save_profiles()
        return {"action": "calibrated", "affection": bounded}

    if not p.get("is_calibrated", False):
        if resonance_updated:
            save_profiles()
        return {"action": "observing", "affection": p.get("affection", 0.0)}

    current_aff = float(p.get("affection", 0.0))

    # 2. 负向冒犯扣分（无视每日上限，直接扣减；严重冒犯可击穿软保底）
    if res < -0.5:
        new_aff = round(max(-10.0, current_aff + res), 1)
        p["affection"] = new_aff
        save_profiles()
        print(f"💔 [观察者好感倒扣] 用户 {uid_str} 因冒犯/负向情绪共鸣 ({res:.1f}) -> 当前好感: {new_aff} ({reason})")
        return {"action": "deducted", "delta": res, "affection": new_aff}

    if res <= 0.5:
        if resonance_updated:
            save_profiles()
        return {"action": "no_change", "affection": current_aff}

    # 3. 正向共鸣加分：区分 <700 稳态成长期 与 >=700 高山阻尼区
    today_gain = float(p.get("today_gain", 0.0))
    if current_aff < 700.0:
        mult = 1.0 if current_aff < 350.0 else (0.90 if current_aff < 500.0 else 0.85)
        raw_delta = res * mult
        daily_cap = 28.0
    else:
        # 700+ 高山阻尼区：普通温和闲聊 (res < 3.5) 不加分！只有深度共鸣才按 0.80x (850+ 为 0.60x) 加分
        if res < 3.5:
            if resonance_updated:
                save_profiles()
            return {"action": "dampened_at_high_altitude", "affection": current_aff}
        mult = 0.80 if current_aff < 850.0 else 0.60
        raw_delta = res * mult
        daily_cap = 16.0

    rem = max(0.0, daily_cap - today_gain)
    final_delta = round(min(raw_delta, rem), 2)
    if final_delta <= 0.0:
        if resonance_updated:
            save_profiles()
        return {"action": "daily_cap_reached", "affection": current_aff}

    target_aff = round(min(1000.0, current_aff + final_delta), 1)

    # 4. 849.0 挚友双重硬性羁绊锁校验
    if current_aff < 850.0 and target_aff >= 850.0:
        can_promote, gate_msg = check_best_friend_gate(p, uid_str)
        if not can_promote:
            target_aff = 849.0
            print(f"🔒 [挚友门槛拦截] 用户 {uid_str} 好感度已达半步挚友上限 849.0，暂未突破 850 ({gate_msg})")

    p["affection"] = target_aff
    p["today_gain"] = round(today_gain + final_delta, 2)
    save_profiles()
    print(f"💗 [观察者好感共鸣] 用户 {uid_str} +{final_delta:.1f} -> 当前好感: {target_aff:.1f} ({reason})")
    return {"action": "increased", "delta": final_delta, "affection": target_aff}


def _apply_bff_gate_if_needed(profile: dict, uid_str: str, current_aff: float, target_aff: float) -> tuple[float, bool]:
    """公共挚友硬锁拦截检查；返回 (修正后分值, 是否被拦截)"""
    if current_aff < 850.0 and target_aff >= 850.0:
        can_promote, _ = check_best_friend_gate(profile, uid_str)
        if not can_promote:
            return 849.0, True
    return target_aff, False


def bot_self_adjust_affection(user_id_str: str, percentage: float, reason: str = "") -> dict:
    """
    弥酱自主好感微调（±5% 封顶）：
    - 聊得特别开心时自主加最多 +5%（同用户每日限 2 次防刷分）；
    - 生气或不爽时自主扣最多 -5%（无次数限制）。
    """
    uid_str = str(user_id_str).strip()
    if uid_str in (str(BOT_QQ), "系统", ""):
        return {"ok": False, "error": "无效目标用户"}
    if uid_str == str(MASTER_QQ):
        return {"ok": False, "error": "主人拥有至高好感羁绊，无需微调数值"}

    p = get_user_profile(uid_str)
    p["today_interacted"] = True
    pct = max(-5.0, min(5.0, float(percentage or 0.0)))
    if abs(pct) < 0.1:
        return {"ok": False, "error": "调节百分比过小（需在 -5.0 ~ +5.0 且非零）"}

    current_aff = float(p.get("affection", 0.0))
    base_ref = max(60.0, abs(current_aff))
    raw_delta = round(base_ref * (pct / 100.0), 1)
    if raw_delta == 0.0:
        raw_delta = 1.0 if pct > 0 else -1.0

    if raw_delta > 0:
        used_cnt = int(p.get("today_self_bonus_count", 0))
        if used_cnt >= 2:
            return {
                "ok": False,
                "error": f"今日对 {uid_str} 的自主 +5% 加分次数已达上限 (2/2)，若极其感动请向主人发起大额申请",
                "affection": current_aff,
            }
        target_aff = round(min(1000.0, current_aff + raw_delta), 1)
        target_aff, gated = _apply_bff_gate_if_needed(p, uid_str, current_aff, target_aff)
        actual_delta = round(target_aff - current_aff, 1)
        p["affection"] = target_aff
        p["today_self_bonus_count"] = used_cnt + 1
        if not p.get("is_calibrated", False):
            p["first_impression_points"] = float(p.get("first_impression_points", 0.0)) + actual_delta
        save_profiles()
        return {
            "ok": True,
            "direction": "up",
            "pct": pct,
            "delta": actual_delta,
            "affection": target_aff,
            "gated": gated,
            "used_today": p["today_self_bonus_count"],
        }

    target_aff = round(max(-10.0, current_aff + raw_delta), 1)
    actual_delta = round(target_aff - current_aff, 1)
    p["affection"] = target_aff
    if not p.get("is_calibrated", False):
        p["first_impression_points"] = float(p.get("first_impression_points", 0.0)) + actual_delta
    save_profiles()
    return {
        "ok": True,
        "direction": "down",
        "pct": pct,
        "delta": actual_delta,
        "affection": target_aff,
        "gated": False,
    }


def submit_affection_request(user_id_str: str, proposed_delta: float, reason: str = "") -> dict:
    """弥酱在特别激动（超开心或超生气）时，登记一条向主人申请无限制加/扣分的待审批记录"""
    uid_str = str(user_id_str).strip()
    if uid_str in (str(BOT_QQ), "系统", ""):
        return {"ok": False, "error": "无效目标用户"}
    if uid_str == str(MASTER_QQ):
        return {"ok": False, "error": "主人无需申请调分"}

    delta_val = round(float(proposed_delta or 0.0), 1)
    if abs(delta_val) < 0.1:
        return {"ok": False, "error": "申请调整的分值不能为 0"}

    p = get_user_profile(uid_str)
    now_dt = _beijing_now()
    req = {
        "id": f"req_{int(now_dt.timestamp())}_{random.randint(100, 999)}",
        "proposed_delta": delta_val,
        "reason": (reason or "").strip() or "情绪特别激动申请调整好感",
        "created_at": now_dt.strftime("%Y-%m-%d %H:%M"),
        "current_affection": round(float(p.get("affection", 0.0)), 1),
    }
    p["pending_affection_request"] = req
    save_profiles()
    return {"ok": True, "request": req, "affection": float(p.get("affection", 0.0))}


def master_manage_affection(user_id_str: str, action: str, value: float = 0.0, reason: str = "") -> dict:
    """
    主人专属无限制好感管理接口（供聊天指令与 WebUI 面板共用）：
    - action='delta': 直接增减 value 分（无上限约束）
    - action='set': 直接设为 value 分
    - action='pct_5': 按当前基数增减 ±5% (value >= 0 为 +5%, < 0 为 -5%)
    - action='approve_request': 批准并执行挂起的 pending_affection_request
    - action='reject_request': 驳回并清除挂起的 pending_affection_request
    - action='delta_resonance': 增减高光共鸣次数 (int(value))
    - action='toggle_bff': 切换主人破格挚友授信 (master_approved_bff)
    """
    uid_str = str(user_id_str).strip()
    if uid_str in (str(BOT_QQ), "系统", ""):
        return {"ok": False, "error": "无效目标用户"}

    p = get_user_profile(uid_str)
    act = (action or "").strip().lower()
    old_aff = float(p.get("affection", 0.0))

    if act == "approve_request":
        req = p.get("pending_affection_request")
        if not isinstance(req, dict):
            return {"ok": False, "error": f"用户 {uid_str} 当前没有待审批的调分申请"}
        delta_val = float(req.get("proposed_delta", 0.0))
        new_aff = round(max(-10.0, min(1000.0, old_aff + delta_val)), 1)
        p["affection"] = new_aff
        p["is_calibrated"] = True
        p["today_interacted"] = True
        if new_aff >= 850.0:
            p["master_approved_bff"] = True
        if delta_val > 0:
            p["exp"] = float(p.get("exp", 0.0)) + max(1.0, delta_val * 0.2)
            p["level"] = get_user_level(p["exp"])
        req_reason = req.get("reason", "")
        p["pending_affection_request"] = None
        save_profiles()
        return {
            "ok": True,
            "action": "approve_request",
            "old_affection": old_aff,
            "new_affection": new_aff,
            "delta": round(new_aff - old_aff, 1),
            "reason": reason or req_reason,
        }

    if act == "reject_request":
        if not p.get("pending_affection_request"):
            return {"ok": False, "error": f"用户 {uid_str} 当前没有待审批的调分申请"}
        p["pending_affection_request"] = None
        save_profiles()
        return {"ok": True, "action": "reject_request", "old_affection": old_aff, "new_affection": old_aff}

    if act == "pct_5":
        sign = 1.0 if float(value or 0.0) >= 0 else -1.0
        base_ref = max(60.0, abs(old_aff))
        delta_val = round(base_ref * 0.05 * sign, 1)
        new_aff = round(max(-10.0, min(1000.0, old_aff + delta_val)), 1)
        p["affection"] = new_aff
        p["is_calibrated"] = True
        p["today_interacted"] = True
        if new_aff >= 850.0:
            p["master_approved_bff"] = True
        save_profiles()
        return {
            "ok": True,
            "action": "pct_5",
            "old_affection": old_aff,
            "new_affection": new_aff,
            "delta": round(new_aff - old_aff, 1),
            "reason": reason,
        }

    if act == "delta":
        delta_val = float(value or 0.0)
        new_aff = round(max(-10.0, min(1000.0, old_aff + delta_val)), 1)
        p["affection"] = new_aff
        p["is_calibrated"] = True
        p["today_interacted"] = True
        if new_aff >= 850.0:
            p["master_approved_bff"] = True
        if delta_val > 0:
            p["exp"] = float(p.get("exp", 0.0)) + max(1.0, delta_val * 0.2)
            p["level"] = get_user_level(p["exp"])
        save_profiles()
        return {
            "ok": True,
            "action": "delta",
            "old_affection": old_aff,
            "new_affection": new_aff,
            "delta": round(new_aff - old_aff, 1),
            "reason": reason,
        }

    if act == "set":
        new_aff = round(max(-10.0, min(1000.0, float(value or 0.0))), 1)
        p["affection"] = new_aff
        p["is_calibrated"] = True
        p["today_interacted"] = True
        if new_aff >= 850.0:
            p["master_approved_bff"] = True
        save_profiles()
        return {
            "ok": True,
            "action": "set",
            "old_affection": old_aff,
            "new_affection": new_aff,
            "delta": round(new_aff - old_aff, 1),
            "reason": reason,
        }

    if act == "delta_resonance":
        old_res = int(p.get("high_resonance_count", 0))
        new_res = max(0, old_res + int(value or 0))
        p["high_resonance_count"] = new_res
        if new_res > old_res:
            p["today_deep_resonance"] = True
        save_profiles()
        return {
            "ok": True,
            "action": "delta_resonance",
            "old_resonance": old_res,
            "high_resonance_count": new_res,
            "new_affection": old_aff,
        }

    if act == "toggle_bff":
        new_flag = not bool(p.get("master_approved_bff", False))
        p["master_approved_bff"] = new_flag
        save_profiles()
        return {
            "ok": True,
            "action": "toggle_bff",
            "master_approved_bff": new_flag,
            "new_affection": old_aff,
        }

    return {"ok": False, "error": f"不支持的操作类型: {action}"}


def settle_daily_profiles():
    """每日凌晨全员等级与跨天好感稳态统一结算（由 daily_memory worker 调用）"""
    load_profiles()
    now_dt = _beijing_now()
    today = now_dt.strftime("%Y-%m-%d")
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")

    for uid_str, p in USER_PROFILES.items():
        if not p.get("is_calibrated", False) and p.get("interaction_count", 0) >= 12:
            pts = float(p.get("first_impression_points", 0.0))
            has_bad = p.get("has_bad_behavior", False)
            if pts < 0:
                initial_aff = max(-10.0, pts)
            elif has_bad:
                initial_aff = min(180.0, max(0.0, 50.0 + pts * 0.5))
            else:
                initial_aff = min(280.0, max(220.0, 200.0 + pts * 0.7))
            p["affection"] = round(initial_aff, 1)
            p["is_calibrated"] = True
            print(f"[+] [夜间结算定级] 用户 {uid_str} 完成初识定级: {p['affection']}")

        if p.get("last_date") != today:
            _apply_daily_settlement_for_profile(uid_str, p, today, now_str)
    save_profiles()
