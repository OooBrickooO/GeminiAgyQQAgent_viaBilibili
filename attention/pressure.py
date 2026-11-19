"""
attention/pressure.py — Layer 2 社交熵话题压力模型、余温阶梯维持、8-15轮渐进软上限与边际递减引擎

管理群聊话题压力模型：
1. 社交熵与边际递减（Social Entropy & Marginal Decay）：
   - 个人频次衰减：同一周期内单人连续发言边际价值按 1 / (1 + 0.6 * k) 递减
   - 参与人多样性：双人私聊式“二人转”（>=4条且独立发言人<=2）触发 0.45x 折价，越聊越便宜；
     3人以上多群友共鸣触发 1.2x~1.5x 热闹增益；触发压力后刷新账本。
2. 生命周期阶梯底压与 8-15 轮渐进软上限（Soft Cap）：
   - 第 1~7 轮：触发对话后进入 45s 余温期，维持 80% 触发上限（只扣 20%），余温结束后维持 50% 会话底压；
   - 第 8~14 轮（渐进变难过渡期）：余温底压按 70% -> 60% -> 50% -> 40% -> 30% -> 20% -> 10% 逐轮递减，会话底压同步递减；
   - 第 15 轮及以上（软上限达成）：发言完毕后压力直接回归 0%（0.0），不再赠送余温或会话底压，需从 0 重新积攒满压才能触发；
   - 被群友 @ / 唤名 / 引用回复时：立即刷新重置软上限轮次计数归零，恢复 80% 余温满活力状态；
   - 7min agy 生命结束销毁时：压力与软上限计数彻底归零（0%），清空账本重新等待。
3. 图片好奇心加压调整为原先的 60% (+0.24)。
"""
import time
from collections import defaultdict, deque
from attention.relation import get_user_affection_weight

# 兴趣词库 (触发压力暴击)
INTEREST_KEYWORDS = ["游戏", "Apex", "二次元", "动漫", "番", "吃", "好饿", "摸鱼", "下班"]

# 图片基础加压（原 0.4 的 60% = 0.24）
IMAGE_PRESSURE_BONUS = 0.24

# 余温期时长（秒，处于 30s~60s 黄金区间）
AFTERGLOW_DURATION_SEC = 45.0

# 群聊连续对话软上限阈值（8轮起逐渐变难，15轮起归零重攒）
SOFT_CAP_START_TURN = 8
SOFT_CAP_ZERO_TURN = 15

# --- 话题压力状态字典 ---
GROUP_PRESSURE = defaultdict(float)
GROUP_MEMBER_COUNTS = {}
LAST_MSG_TIME = {}
LAST_PRESSURE_TRIGGER_TIME = {}
PRESSURE_PENALTY = defaultdict(float)
COMBO_COUNT = defaultdict(int)
GROUP_SOFT_CAP_TURNS = defaultdict(int)       # gid_str -> 距离上次被@或会话开启已连续发言的轮数

# --- 社交熵（发言人频次与多样性）账本 ---
# 结构: gid_str -> deque of {"sender_id": str, "time": float}
PRESSURE_SPEAKER_LOG = defaultdict(lambda: deque(maxlen=30))

# --- 余温期与 Session 生命期联动状态 ---
AFTERGLOW_UNTIL = defaultdict(float)          # gid_str -> 余温期结束时间戳
AFTERGLOW_PENDING_DROP = defaultdict(bool)    # gid_str -> 余温期结束后是否待回落至会话底压
SESSION_ACTIVE_GROUPS = defaultdict(bool)     # gid_str -> 当前是否处于 7min agy 存活期


def get_group_soft_cap_turns(group_id_str: str) -> int:
    """获取指定群当前已累积的连续对话软上限轮数"""
    return GROUP_SOFT_CAP_TURNS.get(str(group_id_str), 0)


def increment_group_soft_cap_turn(group_id_str: str) -> int:
    """当弥酱在群聊完成一轮发言时，推进连续对话软上限轮数计数"""
    gid_str = str(group_id_str)
    GROUP_SOFT_CAP_TURNS[gid_str] = GROUP_SOFT_CAP_TURNS.get(gid_str, 0) + 1
    return GROUP_SOFT_CAP_TURNS[gid_str]


def _get_soft_cap_floors(group_id_str: str) -> tuple[float, float]:
    """
    根据当前群连续对话轮数计算 (余温期底压比例, 会话期底压比例)：
    - 0 ~ 7 轮：余温 80% (0.80)，会话 50% (0.50)
    - 8 ~ 14 轮：逐轮阶梯衰减 (余温: 70% -> 60% -> 50% -> 40% -> 30% -> 20% -> 10%，会话: 44% -> 37% -> 31% -> 24% -> 18% -> 11% -> 5%)
    - >= 15 轮：余温 0% (0.0)，会话 0% (0.0)，回归 0 压力重新积攒
    """
    turns = GROUP_SOFT_CAP_TURNS.get(str(group_id_str), 0)
    if turns < SOFT_CAP_START_TURN:
        return 0.80, 0.50
    if turns >= SOFT_CAP_ZERO_TURN:
        return 0.0, 0.0
    step = turns - (SOFT_CAP_START_TURN - 1)  # turns=8..14 -> step=1..7
    afterglow_ratio = max(0.0, round(0.80 - 0.10 * step, 2))
    session_floor_ratio = max(0.0, round(0.50 - 0.065 * step, 3))
    return afterglow_ratio, session_floor_ratio


def set_group_member_count(group_id_str: str, count: int):
    """更新群成员总数缓存"""
    GROUP_MEMBER_COUNTS[str(group_id_str)] = count


def get_group_member_count(group_id_str: str) -> int:
    """获取群成员总数缓存，默认兜底 200"""
    return GROUP_MEMBER_COUNTS.get(str(group_id_str), 200)


def get_group_threshold(group_id_str: str) -> float:
    """计算指定群当前的压力触发阈值"""
    gid_str = str(group_id_str)
    member_count = get_group_member_count(gid_str)
    base_threshold = max(2.5, member_count * 0.01)
    return max(2.5, base_threshold + PRESSURE_PENALTY.get(gid_str, 0.0))


def set_group_session_state(group_id_str: str, is_active: bool):
    """同步群聊 Session 存活状态"""
    gid_str = str(group_id_str)
    SESSION_ACTIVE_GROUPS[gid_str] = is_active


def sync_afterglow_decay(group_id_str: str, now: float = None):
    """
    检查并同步余温期状态：
    - 若余温期（45s）已结束且处于待回落状态：
      将余温高底压回落至当前轮次对应的会话底压，并在 Session 剩余生命期内维持。
    """
    gid_str = str(group_id_str)
    if now is None:
        now = time.time()

    if AFTERGLOW_PENDING_DROP.get(gid_str, False) and now > AFTERGLOW_UNTIL.get(gid_str, 0.0):
        threshold = get_group_threshold(gid_str)
        afterglow_ratio, session_floor_ratio = _get_soft_cap_floors(gid_str)
        floor_val = threshold * session_floor_ratio
        drop_amount = threshold * max(0.0, afterglow_ratio - session_floor_ratio)
        if SESSION_ACTIVE_GROUPS.get(gid_str, False):
            GROUP_PRESSURE[gid_str] = max(floor_val, min(GROUP_PRESSURE[gid_str] - drop_amount, floor_val))
        else:
            GROUP_PRESSURE[gid_str] = floor_val
        AFTERGLOW_PENDING_DROP[gid_str] = False
        pct_label = int(round(session_floor_ratio * 100))
        print(f"🌡️ [余温回落] 群[{gid_str}] 45s 余温期结束，压力回归至 {pct_label}% 基准 ({GROUP_PRESSURE[gid_str]:.2f}/{threshold:.2f} | 连聊轮次:{GROUP_SOFT_CAP_TURNS.get(gid_str, 0)})。")


def _compute_social_entropy_multiplier(gid_str: str, sender_id_str: str, now: float) -> tuple[float, float, int, int]:
    """
    计算当前发言的【个人频次衰减系数】与【参与人多样性系数】。
    返回: (freq_factor, diversity_factor, user_prev_count, unique_recent_speakers)
    """
    log = PRESSURE_SPEAKER_LOG[gid_str]
    # 清理超过 5 分钟 (300s) 的陈旧发言记录
    while log and (now - log[0]["time"]) > 300.0:
        log.popleft()

    # 1. 统计该用户在本轮周期（近5分钟）内此前已发言次数 k
    user_prev_count = sum(1 for item in log if item["sender_id"] == sender_id_str)
    freq_factor = 1.0 / (1.0 + 0.6 * user_prev_count)

    # 2. 统计最近 6 条发言（含本次）的独立参与人数 U
    recent_slice = list(log)[-5:]
    total_recent_msgs = len(recent_slice) + 1
    unique_speakers = len({item["sender_id"] for item in recent_slice} | {sender_id_str})

    if total_recent_msgs >= 4 and unique_speakers <= 2:
        # 单人自言自语或双人互聊“二人转”：触发 0.45x 折价，越聊越便宜
        diversity_factor = 0.45
    elif unique_speakers >= 3:
        # 3人及以上群友共同参与讨论：触发 1.2x ~ 1.5x 群聊共鸣增益
        diversity_factor = min(1.5, 1.0 + 0.2 * (unique_speakers - 2))
    else:
        diversity_factor = 1.0

    # 记录本次发言入账
    log.append({"sender_id": sender_id_str, "time": now})
    return freq_factor, diversity_factor, user_prev_count, unique_speakers


def get_group_pressure_snapshot(group_id_str: str) -> tuple[float, float, int]:
    """只读获取当前群聊压力快照 (current_pressure, current_threshold, combo)，不产生加压或频次记录"""
    gid_str = str(group_id_str)
    sync_afterglow_decay(gid_str)
    return GROUP_PRESSURE.get(gid_str, 0.0), get_group_threshold(gid_str), COMBO_COUNT.get(gid_str, 0)


def update_message_pressure(group_id_str: str, sender_id_str: str, raw_message: str) -> tuple:
    """
    更新群聊消息话题压力：
    1. 同步余温期与 8-15 轮渐进软上限底压（1-7轮:80%->50%，8-14轮阶梯衰减，15+轮:0%->0%从零重攒）
    2. 非 Session 存活期执行常规半衰期指数衰减 (5分钟衰减10%，>30分钟归零)
    3. 社交熵加压计算：(基础好感加压 + 兴趣词 + 60%图片加压) * 频次衰减 * 多样性乘数
    4. 计算当前阈值

    返回: (pressure_add, current_pressure, current_threshold, has_interest_kw, combo)
    """
    gid_str = str(group_id_str)
    uid_str = str(sender_id_str)
    now = time.time()

    last_t = LAST_MSG_TIME.get(gid_str, now)
    time_diff = now - last_t
    LAST_MSG_TIME[gid_str] = now

    current_threshold = get_group_threshold(gid_str)

    # 先同步余温期状态
    sync_afterglow_decay(gid_str, now)

    in_afterglow = now <= AFTERGLOW_UNTIL.get(gid_str, 0.0)
    in_session = SESSION_ACTIVE_GROUPS.get(gid_str, False)
    afterglow_ratio, session_floor_ratio = _get_soft_cap_floors(gid_str)

    if in_afterglow and afterglow_ratio > 0.0:
        # 余温期内：保持当前轮次对应的余温底压不向下衰减
        GROUP_PRESSURE[gid_str] = max(GROUP_PRESSURE[gid_str], current_threshold * afterglow_ratio)
    elif in_session:
        # Session 存活期内（余温期已过，或处于 >=15 轮 0 底压状态）：最低维持在 session_floor_ratio
        decay_factor = 0.95 ** (time_diff / 300.0)
        GROUP_PRESSURE[gid_str] = max(current_threshold * session_floor_ratio, GROUP_PRESSURE[gid_str] * decay_factor)
    else:
        # 常规潜水期：5分钟衰减 10%，30分钟完全冷场归零
        if time_diff > 1800:
            GROUP_PRESSURE[gid_str] = 0.0
            COMBO_COUNT[gid_str] = 0
            PRESSURE_PENALTY[gid_str] = 0.0
            GROUP_SOFT_CAP_TURNS[gid_str] = 0
            PRESSURE_SPEAKER_LOG[gid_str].clear()
        else:
            decay_factor = 0.90 ** (time_diff / 300.0)
            GROUP_PRESSURE[gid_str] *= decay_factor

    # 冷却惩罚衰减：5分钟不再触发压力，临时惩罚和连击数归零
    if now - LAST_PRESSURE_TRIGGER_TIME.get(gid_str, 0) > 300:
        PRESSURE_PENALTY[gid_str] = 0.0
        COMBO_COUNT[gid_str] = 0
        current_threshold = get_group_threshold(gid_str)

    # 计算社交熵（个人频次递减 + 参与人多样性乘数）
    freq_factor, diversity_factor, prev_k, unique_u = _compute_social_entropy_multiplier(gid_str, uid_str, now)
    entropy_mult = freq_factor * diversity_factor

    # 基础加压：基于好感分级适度平滑加权
    weight = get_user_affection_weight(uid_str)
    raw_base_add = weight * 0.6

    has_interest_kw = False
    bonus_add = 0.0
    for kw in INTEREST_KEYWORDS:
        if kw in raw_message:
            bonus_add += 0.3
            has_interest_kw = True
            break

    # 图片好奇心加压：调整为原先 0.4 的 60% (+0.24)
    if "[CQ:image" in raw_message or "[图片#" in raw_message:
        bonus_add += IMAGE_PRESSURE_BONUS

    # 综合社交熵衰减：在二人转或单人高频连发时，整体加压价值同步递减
    pressure_add = (raw_base_add + bonus_add) * entropy_mult
    GROUP_PRESSURE[gid_str] += pressure_add

    combo = COMBO_COUNT.get(gid_str, 0)
    return pressure_add, GROUP_PRESSURE[gid_str], current_threshold, has_interest_kw, combo


def is_in_pressure_cooldown(group_id_str: str, cooldown_sec: float = 15.0) -> bool:
    """
    检查群是否处于短时防并发爆发保护期（默认 15 秒短防抖，防止同一轮消息连环重击触发双爆发）。
    当连续对话进入 8~15 轮软上限区间时，适度拉长爆发冷却间隔。
    """
    gid_str = str(group_id_str)
    turns = GROUP_SOFT_CAP_TURNS.get(gid_str, 0)
    extra_cd = 0.0
    if turns >= SOFT_CAP_START_TURN:
        extra_cd = min(12.0, (turns - (SOFT_CAP_START_TURN - 1)) * 2.0)
    last_trigger = LAST_PRESSURE_TRIGGER_TIME.get(gid_str, 0.0)
    return (time.time() - last_trigger) < (cooldown_sec + extra_cd)


def activate_afterglow_pressure(group_id_str: str, current_threshold: float = None):
    """
    当对话被触发或本轮发信完成时调用：
    - 刷新清空参与人频次账本（重置“二人转”折价与个人疲劳）；
    - 根据当前连续对话轮数 (GROUP_SOFT_CAP_TURNS) 设定余温底压：
      * 0~7 轮：80% 余温底压（45s 后回落至 50%）
      * 8~14 轮：阶梯下调余温底压（70% -> 60% -> 50% -> 40% -> 30% -> 20% -> 10%）
      * >=15 轮：软上限达成，压力直接回归 0.0，不设余温保底，重新从 0 积攒压力。
    """
    gid_str = str(group_id_str)
    now = time.time()
    if current_threshold is None:
        current_threshold = get_group_threshold(gid_str)

    SESSION_ACTIVE_GROUPS[gid_str] = True
    PRESSURE_SPEAKER_LOG[gid_str].clear()

    turns = GROUP_SOFT_CAP_TURNS.get(gid_str, 0)
    afterglow_ratio, _ = _get_soft_cap_floors(gid_str)

    if afterglow_ratio <= 0.0:
        GROUP_PRESSURE[gid_str] = 0.0
        AFTERGLOW_UNTIL[gid_str] = 0.0
        AFTERGLOW_PENDING_DROP[gid_str] = False
        print(f"💤 [软上限归零] 群[{gid_str}] 已连续对话 {turns} 轮 (>=15轮)，压力直接回归 0.00/{current_threshold:.2f} 重新积攒。")
    else:
        GROUP_PRESSURE[gid_str] = current_threshold * afterglow_ratio
        AFTERGLOW_UNTIL[gid_str] = now + AFTERGLOW_DURATION_SEC
        AFTERGLOW_PENDING_DROP[gid_str] = True
        if turns >= SOFT_CAP_START_TURN:
            print(f"🌡️ [软上限渐进] 群[{gid_str}] 已连续对话 {turns} 轮，本轮余温底压下调至 {int(round(afterglow_ratio * 100))}% ({GROUP_PRESSURE[gid_str]:.2f}/{current_threshold:.2f})。")


def trigger_pressure_burst(group_id_str: str, current_threshold: float):
    """
    当 Layer 2 压力爆发触发时更新内部状态：
    - 激活当前轮次对应的余温底压，并刷新参与人频次账本
    """
    gid_str = str(group_id_str)
    now = time.time()

    last_trigger = LAST_PRESSURE_TRIGGER_TIME.get(gid_str, 0.0)
    if now - last_trigger < 120:
        COMBO_COUNT[gid_str] += 1
        if COMBO_COUNT[gid_str] > 4:
            PRESSURE_PENALTY[gid_str] += 0.2
    else:
        COMBO_COUNT[gid_str] = 1
        PRESSURE_PENALTY[gid_str] = 0.0

    LAST_PRESSURE_TRIGGER_TIME[gid_str] = now
    updated_threshold = get_group_threshold(gid_str)
    activate_afterglow_pressure(gid_str, updated_threshold)


def cap_group_pressure(group_id_str: str, current_threshold: float):
    """当连续发言达到上限被拦截时，截断防止压力无限暴涨"""
    gid_str = str(group_id_str)
    GROUP_PRESSURE[gid_str] = min(GROUP_PRESSURE[gid_str], current_threshold * 0.85)


def reset_group_pressure(group_id_str: str):
    """
    被 @ 、叫名字或引用回复直接唤醒时：
    刷新重置群聊连续对话软上限轮数 (GROUP_SOFT_CAP_TURNS = 0)、连击惩罚与频次账本，
    并恢复 80% 余温高底压状态（方便群友后续不用句句 @ 也能接两三句）。
    """
    gid_str = str(group_id_str)
    prev_turns = GROUP_SOFT_CAP_TURNS.get(gid_str, 0)
    GROUP_SOFT_CAP_TURNS[gid_str] = 0
    COMBO_COUNT[gid_str] = 0
    PRESSURE_PENALTY[gid_str] = 0.0
    threshold = get_group_threshold(gid_str)
    activate_afterglow_pressure(gid_str, threshold)
    if prev_turns > 0:
        print(f"🔄 [软上限刷新] 群[{gid_str}] 收到直接呼叫(@/唤名)，连续对话软上限已从 {prev_turns} 轮重置归零！")


def clear_group_pressure_on_session_end(group_id_str: str):
    """
    当 7min agy 会话生命结束销毁时调用：
    压力与连续对话软上限轮数彻底归零（0.0），清空余温标记与发言账本，重新等待下一轮激活。
    """
    gid_str = str(group_id_str)
    SESSION_ACTIVE_GROUPS[gid_str] = False
    GROUP_PRESSURE[gid_str] = 0.0
    AFTERGLOW_UNTIL[gid_str] = 0.0
    AFTERGLOW_PENDING_DROP[gid_str] = False
    PRESSURE_SPEAKER_LOG[gid_str].clear()
    COMBO_COUNT[gid_str] = 0
    PRESSURE_PENALTY[gid_str] = 0.0
    GROUP_SOFT_CAP_TURNS[gid_str] = 0
    print(f"📉 [压力归零] 群[{gid_str}] 7分钟 agy 生命结束，话题压力与软上限计数已彻底归零重置。")
