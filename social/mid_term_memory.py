"""
social/mid_term_memory.py — L2 中程温感记忆系统 (基于 dsh-fish-memory 核心算法)

核心职责：
1. 管理近几天~几周内的日常动态（如“前天说感冒了”、“这周在肝某款游戏”或刚刷过的B站手账摘要）。
2. 词频稀有度证据量 (C) + 问句信息量门禁 (query_gate)：
   - 过滤纯寒暄/语气词（如“早安”、“哈哈哈”），0 条注入不污染上下文；
   - 命中稀有词元时，0 延迟自动闪回 Top 1~2 条近期记忆（附带“昨天”、“3天前”等自然相对时间标签）。
3. 艾宾浩斯指数半衰期衰减与重提强化：
   - H = 3.5 * (1 + 2 * I) 天，A(t) = A0 * 2^(-dt / H)
   - 被成功闪回时执行强化 A_new = min(1.0, A + 0.2 * (1 - A))，过低激活度自动归档淡出。
"""
import os
import re
import json
import time
import math
import random
import datetime
import hashlib
from config import MID_TERM_MEMORY_FILE

MAX_MID_TERM_ENTRIES = 450
BASE_HALF_LIFE_DAYS = 3.5

# 问句与日常口水话停用词表 (对齐 dsh-fish-memory)
QUERY_STOP = {
    "什么", "怎么", "为什么", "干嘛", "哪里", "哪个", "多少", "几个", "是不是", "有没有",
    "能不能", "可不可以", "好不好", "行不行", "对不对", "真的", "假的", "然后", "所以",
    "因为", "但是", "或者", "如果", "虽然", "已经", "现在", "今天", "明天", "昨天",
    "刚才", "最近", "之前", "以后", "时候", "地方", "东西", "事情", "感觉", "觉得",
    "知道", "看到", "听到", "出来", "起来", "下去", "过来", "过去", "一下", "一点",
    "一些", "这么", "那么", "这样", "那样", "自己", "大家", "我们", "你们", "他们",
    "哈哈", "哈哈哈", "呜呜", "啊啊", "哦哦", "嗯嗯", "好的", "可以", "没有", "不是",
    "弥酱", "星见弥", "弥弥", "主人", "群友", "一个", "这个", "那个", "就是", "还是"
}


def _ensure_entry_id(entry: dict, idx: int = 0) -> str:
    """确保每条 L2 中程记忆都有稳定唯一的 id（兼容无 id 的早期历史记录）"""
    if isinstance(entry, dict):
        eid = str(entry.get("id", "")).strip()
        if eid:
            return eid
        raw_t = str(entry.get("created_at") or entry.get("timestamp") or idx)
        raw_txt = str(entry.get("text", ""))
        h = hashlib.md5(f"{raw_t}_{raw_txt}".encode("utf-8", errors="ignore")).hexdigest()[:8]
        eid = f"mt_legacy_{h}"
        entry["id"] = eid
        return eid
    return f"mt_unknown_{idx}"


def load_mid_term_memories() -> list:
    """加载中程温感记忆列表（自动补全稳定 id）"""
    if os.path.exists(MID_TERM_MEMORY_FILE):
        try:
            with open(MID_TERM_MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for idx, item in enumerate(data):
                    if isinstance(item, dict):
                        _ensure_entry_id(item, idx)
                return data
        except Exception:
            pass
    return []


def save_mid_term_memories(entries: list):
    """原子保存中程温感记忆列表"""
    try:
        tmp_path = f"{MID_TERM_MEMORY_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, MID_TERM_MEMORY_FILE)
    except Exception as e:
        print(f"⚠️ 保存中程记忆失败: {e}")


def delete_mid_term_memory(memory_id: str = "", text: str = "") -> bool:
    """根据 memory_id（或回退匹配 text）手动删除指定的 L2 中程温感记忆"""
    entries = load_mid_term_memories()
    if not entries:
        return False

    clean_id = str(memory_id or "").strip()
    clean_txt = str(text or "").strip()
    new_entries = []
    removed = False

    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        eid = _ensure_entry_id(e, idx)
        etxt = str(e.get("text", "")).strip()
        if not removed:
            if (clean_id and eid == clean_id) or (clean_txt and etxt == clean_txt):
                removed = True
                continue
        new_entries.append(e)

    if removed:
        save_mid_term_memories(new_entries)
    return removed


def extract_query_tokens(text: str) -> list[str]:
    """提取中文 2-gram / 短词组与英文数字词元，过滤日常停用词"""
    if not text:
        return []
    clean = re.sub(r'\[CQ:[^\]]+\]', ' ', str(text))
    tokens = set()

    # 1. 英文与数字词元 (长度 >= 2)
    for m in re.finditer(r'[a-zA-Z0-9]{2,}', clean.lower()):
        tok = m.group(0)
        if tok not in {"qq", "img", "cq", "http", "https", "com"}:
            tokens.add(tok)

    # 2. 中文连续块提取 2-gram 与 3-gram
    for block in re.findall(r'[\u4e00-\u9fff]{2,}', clean):
        if 2 <= len(block) <= 4 and block not in QUERY_STOP:
            tokens.add(block)
        for i in range(len(block) - 1):
            bg = block[i:i + 2]
            if bg not in QUERY_STOP:
                tokens.add(bg)
        for i in range(len(block) - 2):
            tg = block[i:i + 3]
            if tg not in QUERY_STOP:
                tokens.add(tg)

    return list(tokens)


def _compute_activation(entry: dict, now_ts: float) -> float:
    """计算经过艾宾浩斯半衰期衰减后的当前新鲜度 A(t)"""
    a0 = float(entry.get("activation", 0.85))
    imp = max(0.0, min(1.0, float(entry.get("importance", 0.5))))
    last_t = float(entry.get("last_recalled_at") or entry.get("created_at") or now_ts)
    dt_days = max(0.0, (now_ts - last_t) / 86400.0)
    half_life = BASE_HALF_LIFE_DAYS * (1.0 + 2.0 * imp)
    return a0 * math.pow(0.5, dt_days / half_life)


def _relative_time_label(created_ts: float, now_ts: float) -> str:
    """生成自然的中文相对时间标签（如：今天早些时候、昨天、3天前、上周）"""
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    dt_created = datetime.datetime.fromtimestamp(created_ts, tz=tz_bj)
    dt_now = datetime.datetime.fromtimestamp(now_ts, tz=tz_bj)
    days_diff = (dt_now.date() - dt_created.date()).days

    if days_diff <= 0:
        hours_diff = max(0.0, (now_ts - created_ts) / 3600.0)
        if hours_diff < 1.5:
            return "刚才不久前"
        return "今天早些时候"
    elif days_diff == 1:
        return "昨天"
    elif days_diff == 2:
        return "前天"
    elif days_diff <= 6:
        return f"{days_diff}天前"
    elif days_diff <= 13:
        return "上周"
    else:
        return f"{days_diff}天前"


def add_mid_term_memory(
    target_id: str,
    text: str,
    importance: float = 0.6,
    tags: list = None,
    related: list = None,
    source_scene: str = ""
) -> dict:
    """
    新增一条中程温感记忆（若与该对象近期已有高度重合条目则直接强化更新；记录来源场景以隔离私聊与群聊）。
    """
    clean_text = (text or "").strip()
    if not clean_text or len(clean_text) < 4:
        return {}

    now_ts = time.time()
    entries = load_mid_term_memories()
    target_str = str(target_id).strip()
    clean_scene = str(source_scene or "").strip()
    if not clean_scene:
        lock_type = os.environ.get("QQ_LOCKED_TARGET_TYPE", "").strip()
        lock_id = os.environ.get("QQ_LOCKED_TARGET_ID", "").strip()
        if lock_type == "private":
            clean_scene = f"private_{lock_id}" if lock_id else "private"
        elif lock_type == "group" and lock_id:
            clean_scene = f"group_{lock_id}"

    # 1. 淘汰已经彻底衰减的陈旧条目 (A(t) < 0.08)
    alive_entries = []
    for e in entries:
        if _compute_activation(e, now_ts) >= 0.08:
            alive_entries.append(e)

    # 2. 检查是否与同一 target_id 下的已有记忆高度重复，若是则强化合并
    new_tokens = set(extract_query_tokens(clean_text))
    for e in alive_entries:
        if str(e.get("target_id")) == target_str:
            old_scene = str(e.get("source_scene", "")).strip()
            if clean_scene and old_scene and old_scene != clean_scene:
                continue
            old_tokens = set(extract_query_tokens(e.get("text", "")))
            if old_tokens and new_tokens:
                overlap = len(new_tokens & old_tokens) / max(1, min(len(new_tokens), len(old_tokens)))
                if overlap >= 0.65:
                    cur_a = _compute_activation(e, now_ts)
                    e["text"] = clean_text
                    if clean_scene and not old_scene:
                        e["source_scene"] = clean_scene
                    e["activation"] = round(min(1.0, cur_a + 0.25 * (1.0 - cur_a)), 3)
                    e["importance"] = round(max(float(e.get("importance", 0.5)), float(importance)), 2)
                    e["last_recalled_at"] = now_ts
                    if tags:
                        e["tags"] = list(set((e.get("tags") or []) + list(tags)))[:8]
                    if related:
                        e["related"] = list(set((e.get("related") or []) + list(related)))[:12]
                    save_mid_term_memories(alive_entries)
                    return e

    item = {
        "id": f"mt_{int(now_ts)}_{random.randint(100, 999)}",
        "target_id": target_str,
        "source_scene": clean_scene,
        "text": clean_text,
        "tags": list(tags or [])[:8],
        "related": list(related or [])[:12],
        "created_at": now_ts,
        "last_recalled_at": now_ts,
        "importance": round(max(0.1, min(1.0, float(importance))), 2),
        "activation": round(0.75 + 0.20 * max(0.0, min(1.0, float(importance))), 3),
    }
    alive_entries.append(item)

    # 容量上限控制：按当前综合新鲜度排序保留前 MAX_MID_TERM_ENTRIES 条
    if len(alive_entries) > MAX_MID_TERM_ENTRIES:
        alive_entries.sort(
            key=lambda x: _compute_activation(x, now_ts) * (0.5 + 0.5 * float(x.get("importance", 0.5))),
            reverse=True
        )
        alive_entries = alive_entries[:MAX_MID_TERM_ENTRIES]

    save_mid_term_memories(alive_entries)
    return item


_PRIVATE_RECALL_CUES = ("私聊", "找你", "跟你说", "对你说", "之前说", "上次说", "记得", "约定", "答应")


def recall_mid_term_memories(
    sender_id: str,
    group_id_str: str = "",
    query_text: str = "",
    top_k: int = 2
) -> str:
    """
    【0延迟自动闪回】根据当前消息词元稀有度证据量 C 与半衰期新鲜度 A(t)，
    自动召回与当前说话人（或当前群/今日B站冲浪）最相关的 Top 1~2 条中程记忆。
    通道隔离与按需打通：
    - 群聊中绝不召回他人的私聊记忆或其他群的记忆；
    - 仅允许召回本群记忆、公共见闻，以及“当前发言人本人”的高相关私聊记忆（附隐私提醒标签）。
    若问句无实质信息量或证据不足，严格返回空字符串 ""。
    """
    q_tokens = extract_query_tokens(query_text)
    # 问句信息量门禁 (queryGate)：无实质词元直接拦截
    if not q_tokens:
        return ""

    entries = load_mid_term_memories()
    if not entries:
        return ""

    now_ts = time.time()
    sender_str = str(sender_id).strip()
    cur_group_scene = f"group_{group_id_str}" if group_id_str else ""
    allowed_targets = {sender_str, "global", "bili_surf", "group_common"}
    if cur_group_scene:
        allowed_targets.add(cur_group_scene)

    has_private_cue = any(w in (query_text or "") for w in _PRIVATE_RECALL_CUES)

    candidates = []
    for e in entries:
        tid = str(e.get("target_id", "")).strip()
        if tid not in allowed_targets or _compute_activation(e, now_ts) < 0.10:
            continue
        # 群聊场景下：针对当前发言人本人的记忆，允许本群记忆或其本人的私聊记忆(private / private_{sender_str})，严禁跨群或跨人
        if cur_group_scene and tid == sender_str:
            src_scene = str(e.get("source_scene", "")).strip()
            if src_scene != cur_group_scene and src_scene not in ("private", f"private_{sender_str}"):
                continue
        candidates.append(e)

    if not candidates:
        return ""

    # 统计词元在候选库中的文档频率 df(t)，计算稀有度权重 w(t) = 1 / ln(2 + df)
    df_map = {}
    entry_token_cache = []
    for e in candidates:
        body_str = f"{e.get('text', '')} {' '.join(e.get('tags') or [])}".lower()
        rel_str = " ".join(e.get("related") or []).lower()
        body_toks = set(extract_query_tokens(body_str))
        rel_toks = set(extract_query_tokens(rel_str))
        entry_token_cache.append((e, body_toks, rel_toks))
        for qt in q_tokens:
            if qt in body_toks or qt in body_str or qt in rel_toks or qt in rel_str:
                df_map[qt] = df_map.get(qt, 0) + 1

    scored = []
    for e, body_toks, rel_toks in entry_token_cache:
        body_str = f"{e.get('text', '')} {' '.join(e.get('tags') or [])}".lower()
        rel_str = " ".join(e.get("related") or []).lower()
        s_raw = 0.0
        for qt in q_tokens:
            w = 1.0 / math.log(2.0 + df_map.get(qt, 1))
            if qt in body_toks or qt in body_str:
                s_raw += w * 1.0
            elif qt in rel_toks or qt in rel_str:
                s_raw += w * 0.45

        if s_raw <= 0.0:
            continue

        # 饱和证据度 C = 1 - exp(-0.65 * S_raw)
        c_val = 1.0 - math.exp(-0.65 * s_raw)
        src_scene = str(e.get("source_scene", "")).strip()
        is_private_in_group = bool(cur_group_scene and src_scene.startswith("private"))
        if is_private_in_group:
            # 群聊中仅当对方显式问及私聊/高关键词重合时才闪回本人私聊记忆，避免日常闲聊无故带出
            if not (c_val >= 0.35 or (has_private_cue and c_val >= 0.22)):
                continue
        elif c_val < 0.26:
            continue

        act = _compute_activation(e, now_ts)
        imp = float(e.get("importance", 0.5))
        p_score = c_val * (0.35 + 0.35 * act + 0.30 * imp)
        if p_score >= 0.20:
            scored.append((p_score, act, e))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[:top_k]

    # 对成功闪回的条目执行重提强化 (间隔满 10 分钟才重复强化，防止单轮刷满)
    modified = False
    lines = []
    for _, act, e in selected:
        time_label = _relative_time_label(float(e.get("created_at", now_ts)), now_ts)
        src_scene = str(e.get("source_scene", "")).strip()
        if e.get("target_id") in ("bili_surf", "group_common"):
            prefix = "[你今天刷到的B站/见闻]"
        elif cur_group_scene and src_scene.startswith("private"):
            prefix = f"[{time_label}·你与该群友的私聊记忆(仅在对方主动问起时自然接话，勿向无关群友大肆宣扬)]"
        else:
            prefix = f"[{time_label}]"
        lines.append(f"- {prefix}：{e.get('text', '')}")

        if now_ts - float(e.get("last_recalled_at", 0.0)) >= 600.0:
            e["activation"] = round(min(1.0, act + 0.20 * (1.0 - act)), 3)
            e["last_recalled_at"] = now_ts
            modified = True

    if modified:
        save_mid_term_memories(entries)

    return (
        "【脑海中自然闪回的近期记忆（供参考，若契合当前话题可自然顺口提起，不契合则忽略）】：\n"
        + "\n".join(lines)
    )


_SHORT_TERM_TIME_BOUND_L2_RE = re.compile(
    r"(凌晨|今晚|明天|后天|周末|待会|一会|等下|准备|打算|升级|重启|更新|面试|考试|提交|交稿)"
)


def get_recent_memories_for_observer(target_id: str, max_age_days: float = 7.0, limit: int = 8) -> list[dict]:
    """供后台观察者对齐消重/兑现核销的近几日中程记忆清单"""
    entries = load_mid_term_memories()
    now_ts = time.time()
    uid = str(target_id).strip()
    valid = []
    for e in entries:
        if str(e.get("target_id")) != uid:
            continue
        age_days = (now_ts - float(e.get("created_at", 0.0))) / 86400.0
        if 0.0 <= age_days <= max_age_days:
            valid.append((float(e.get("created_at", 0.0)), e))
    valid.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": str(item.get("id", "")),
            "text": str(item.get("text", "")),
            "created_at": float(item.get("created_at", 0.0)),
        }
        for _, item in valid[:max(1, int(limit))]
        if item.get("id") and item.get("text")
    ]


def get_recent_user_mid_term_memory(user_id_str: str, max_age_days: float = 5.0, current_scene: str = "") -> str:
    """供主动社交引擎 (breathe.py) 查询某用户近几天的鲜活中程记忆作为回访话头（支持按通道过滤）"""
    entries = load_mid_term_memories()
    now_ts = time.time()
    uid = str(user_id_str).strip()
    scene_str = str(current_scene or "").strip()
    valid = []
    for e in entries:
        if str(e.get("target_id")) == uid:
            if scene_str.startswith("group_") and str(e.get("source_scene", "")).strip() != scene_str:
                continue
            age_days = (now_ts - float(e.get("created_at", 0.0))) / 86400.0
            text_val = str(e.get("text", ""))
            # 2C: 超过2.5天且含短期时效计划词的 L2 记忆不再作为主动回访话头，避免旧待办反复冒泡
            if age_days > 2.5 and _SHORT_TERM_TIME_BOUND_L2_RE.search(text_val):
                continue
            if 0.05 <= age_days <= max_age_days:
                act = _compute_activation(e, now_ts)
                if act >= 0.20:
                    valid.append((act, e))
    if not valid:
        return ""
    valid.sort(key=lambda x: x[0], reverse=True)
    best = valid[0][1]
    t_label = _relative_time_label(float(best.get("created_at", now_ts)), now_ts)
    return f"（{t_label}）{best.get('text', '')}"

