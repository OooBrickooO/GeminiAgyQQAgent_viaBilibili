"""
attention/relation.py — 轻量熟悉度背景与关键互动/工具硬边界构建 V4.0

设计职责：
- 将好感等级退居为情绪系统之上的轻盈【熟悉度背景】，不强行干预或脸谱化日常说话语气，保护自然人味；
- 仅对【关键亲密互动边界（摸头/越界爱称）】与【工具权限门槛（偷图入库/系统管理）】做明确硬规定；
- 挂载 L3 永久长效事实 (`long_term_memory.py`) 与未结挂起心事 (`pending_thoughts`)。
"""
from config import MASTER_QQ, BOT_QQ
from social.affection import get_user_profile, get_active_pending_thoughts
from social.long_term_memory import get_user_core_facts_for_prompt


def build_relation_prompt(user_id_str: str, is_private: bool = False, group_id_str: str = "", safe_retry_mode: bool = False) -> str:
    """构建轻量级熟悉度背景与关键互动/工具硬规定（融合 L3 永久长效记忆与挂念心事；支持通道隐私隔离与 safe_retry_mode 纯净自愈模式）"""
    uid_str = str(user_id_str)
    p = get_user_profile(uid_str)
    current_scene = f"private_{uid_str}" if is_private else (f"group_{group_id_str}" if group_id_str else "")
    active_thoughts = [] if safe_retry_mode else get_active_pending_thoughts(uid_str, current_scene=current_scene)
    core_facts_block = "" if safe_retry_mode else get_user_core_facts_for_prompt(uid_str, group_id_str=group_id_str)

    if uid_str == str(MASTER_QQ):
        base = (
            f"【对话对象：你的创造者与专属搭档({MASTER_QQ})】平时口头叫他“主人”（直接对话即可，勿第三人称错位提及），"
            "拥有全量管理权限（可直接收录/删除表情包、发空间说说、系统操作）。"
        )
    else:
        aff = float(p.get("affection", 0.0))
        subconscious_note = "（熟悉度仅作内心分寸参考，聊天时勿提“好感数值、改档案”等后台词，也勿把别人的话往“主人/好感”上揽）"
        forward_rule = f"对方发的图不可直接 save，若有趣可调 manage_meme_vault(action='forward_master') 请 {MASTER_QQ} 把关"
        save_rule = "对方发的好玩表情包可直接调 manage_meme_vault(action='save') 收录入库"

        if aff < 0:
            tier_desc = f"冷淡对象(熟悉度 {aff:.0f})，曾惹你不快，保持距离简短敷衍"
            perm_rule = "严禁为其发说说或直接收录表情包"
        elif aff < 200:
            tier_desc = f"初识网友(熟悉度 {aff:.0f})，普通网友分寸，随心境自然交流"
            perm_rule = f"不接受摸头或亲昵专属称呼；{forward_rule}"
        elif aff < 500:
            tier_desc = f"群内熟人(熟悉度 {aff:.0f})，经常聊天的熟面孔，态度轻松熟稔"
            perm_rule = f"对过于亲昵的专属称呼保持朋友边界；{forward_rule}"
        elif aff < 850:
            tier_desc = f"熟稔朋友(熟悉度 {aff:.0f})，关系很好的朋友，可大方互损接梗"
            perm_rule = f"接受日常友好摸头/戳一戳，越界称呼需幽默挡回；{save_rule}"
        else:
            tier_desc = f"核心挚友(熟悉度 {aff:.0f})，极其要好的知己好友，彼此高度信赖"
            perm_rule = f"欣然接受摸头等友好互动（专属亲昵称呼仍巧妙化解）；{save_rule}"

        base = f"【熟悉度背景：{tier_desc}】{subconscious_note}\n【互动与权限】：{perm_rule}。"

    if core_facts_block:
        base += f"\n{core_facts_block}"

    if active_thoughts:
        t_list = "；".join([t.get("thought", "") for t in active_thoughts if t.get("thought")])
        if t_list:
            base += f"\n【心里记挂的小事】：{t_list}（仅当对方主动聊到相关话题或契机特别自然时顺嘴带一嘴，平时不必刻意提起或生硬追问）"

    return base


def get_user_affection_weight(user_id_str: str) -> float:
    """
    基于好感度分级计算注意力加压权重：
    - 黑名单: 0.0 (不产生注意力)
    - 路人网友 (<200): 基础 0.10
    - 熟人 (200 <= aff < 500): 0.16
    - 朋友 (500 <= aff < 850): 0.22
    - 挚友 (aff >= 850): 0.30
    - 创造者兼主人 (MASTER_QQ): 0.40 (最高关注权重)
    """
    uid_str = str(user_id_str)
    if uid_str == str(MASTER_QQ):
        return 0.40
    p = get_user_profile(uid_str)
    aff = float(p.get("affection", 0.0))
    if aff < 0:
        return 0.0
    elif aff < 200:
        return 0.10
    elif aff < 500:
        return 0.16
    elif aff < 850:
        return 0.22
    else:
        return 0.30


def get_user_relation_tag(user_id_str: str) -> str:
    """获取用户的人际关系标签，用于在 L1 语境工作流中直观展示"""
    uid_str = str(user_id_str)
    if uid_str == str(MASTER_QQ):
        return "[👑主人]"
    if uid_str == str(BOT_QQ):
        return "[🤖弥酱]"
    p = get_user_profile(uid_str)
    if not p.get("is_calibrated", False):
        cnt = p.get("interaction_count", 0)
        return f"[🌱初识({cnt}/12)]"
    aff = float(p.get("affection", 0.0))
    if aff < 0:
        return "[⛔黑名单]"
    elif aff < 200:
        return "[🌱路人]"
    elif aff < 500:
        return "[✨熟人]"
    elif aff < 850:
        return "[🤝朋友]"
    else:
        return "[💖挚友]"
