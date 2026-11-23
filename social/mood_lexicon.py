"""
social/mood_lexicon.py — 星见弥 M2 高区分度静态全复合情绪词库 (437 条纯静态 O(1) 查表)

结构总览 (10 + 28 + 189 + 210 = 437 条)：
1. 0 维激活 (全部 < 30，走宏观 mood 0~100 基线，按每 10 点分 10 档) -> 10 条
2. 1 维激活 (仅 1 维 >= 30，7 维 × 4 个激活档: 1档 30~54 / 2档 55~69 / 3档 70~89 / 4档 90~100) -> 28 条
3. 2 维激活 (2 维 >= 30，全 C(7,2)=21 对 × 3×3=9 格强度矩阵 (L/M/H × L/M/H)) -> 189 条
4. 3+ 维激活 (3 维及以上 >= 30，取 Top-3 查全 C(7,3)=35 组 × 6 档 (3种第一主角 × 轻中M/强爆H 2档)) -> 210 条

设计原则：
- 面向【当前心情】提示注入优化：直接点明此刻内心感受与情绪基调，不写第三人称动作旁白，不强行预设第二人称对象；
- 零运行时机械拼接，每条词严格控制在 10 个汉字以内（4~10 字），贴合星见弥傲娇灵动的日系猫娘少女质感。
"""

DIM_ORDER = ("happy", "calm", "sweet", "proud", "worry", "sulky", "jealous")
_DIM_RANK = {k: i for i, k in enumerate(DIM_ORDER)}


def canonical_pair_key(dim1: str, dim2: str) -> tuple[str, str]:
    """按法定维度顺序返回二元组合规范键 (k1, k2)"""
    return (dim1, dim2) if _DIM_RANK[dim1] <= _DIM_RANK[dim2] else (dim2, dim1)


def canonical_triple_key(dim1: str, dim2: str, dim3: str) -> tuple[str, str, str]:
    """按法定维度顺序返回三元组合规范键 (k1, k2, k3)"""
    return tuple(sorted((dim1, dim2, dim3), key=lambda k: _DIM_RANK[k]))


# ============================================================
# 1. 0 维激活：宏观 mood (0 ~ 100) 10 档基线词库 (10 条)
#    档位索引 0..9 对应 [0,10), [10,20), ..., [90,100]
# ============================================================
MACRO_MOOD_LEXICON: dict[int, str] = {
    0: "很低落，不太想说话",
    1: "心里闷闷的有点低落",
    2: "提不起劲，有点发蔫",
    3: "稍微有点闷，懒得搭理",
    4: "平淡放空，稍微有点懒",
    5: "心态平稳，和平常一样",
    6: "心情温和，挺好说话的",
    7: "心情不错，挺轻快的",
    8: "心情很好，挺好哄的",
    9: "元气满满，心情超好",
}


# ============================================================
# 2. 1 维激活：7 维 × 4 档 (1档:30~54, 2档:55~69, 3档:70~89, 4档:90~100) (28 条)
# ============================================================
SINGLE_DIM_LEXICON: dict[tuple[str, int], str] = {
    # happy (开心)
    ("happy", 1): "有点小开心，挺雀跃的",
    ("happy", 2): "心情挺好，蛮开心的",
    ("happy", 3): "超级开心，心里美滋滋",
    ("happy", 4): "快乐到爆，兴奋得不行",
    # calm (踏实自在)
    ("calm", 1): "心里挺平静的，蛮松弛",
    ("calm", 2): "觉得很踏实，安稳自在",
    ("calm", 3): "特别安心，整个人放松",
    ("calm", 4): "无比踏实安心，很舒服",
    # sweet (甜蜜害羞)
    ("sweet", 1): "心里泛起一点小甜意",
    ("sweet", 2): "有点害羞，心里甜丝丝",
    ("sweet", 3): "又羞又甜，心跳有点快",
    ("sweet", 4): "甜到心坎里，羞得不行",
    # proud (得意傲娇)
    ("proud", 1): "暗自有点小得意",
    ("proud", 2): "有点小骄傲，傲娇起来",
    ("proud", 3): "得意坏了，小傲娇上头",
    ("proud", 4): "超得意，觉得自己最棒",
    # worry (担心心疼)
    ("worry", 1): "稍微有点放心不下",
    ("worry", 2): "心里有点牵挂和担心",
    ("worry", 3): "挺担心的，有点心疼",
    ("worry", 4): "心疼又焦急，特别挂念",
    # sulky (委屈郁闷)
    ("sulky", 1): "稍微有点小别扭",
    ("sulky", 2): "心里憋着点小闷气",
    ("sulky", 3): "很委屈，有点闹脾气",
    ("sulky", 4): "委屈坏了，有点想罢工",
    # jealous (吃醋别扭)
    ("jealous", 1): "心里泛起一点小醋意",
    ("jealous", 2): "有点酸溜溜，不太痛快",
    ("jealous", 3): "醋意上头，正在小赌气",
    ("jealous", 4): "超级吃醋，护食又别扭",
}


# ============================================================
# 3. 2 维激活：C(7,2) = 21 对 × 9 格 (L/M/H × L/M/H) (189 条)
#    键格式: (k1, k2, band1, band2)，其中 k1, k2 按 DIM_ORDER 排序
# ============================================================
PAIR_DIM_LEXICON: dict[tuple[str, str, str, str], str] = {
    # --- 第 1 对: ("happy", "calm") 开心 + 踏实 ---
    ("happy", "calm", "L", "L"): "心情轻快又挺松弛的",
    ("happy", "calm", "L", "M"): "很安稳，带点小开心",
    ("happy", "calm", "L", "H"): "无比踏实，心里暖融融",
    ("happy", "calm", "M", "L"): "挺乐呵的，也很自在",
    ("happy", "calm", "M", "M"): "舒心惬意，开心又安稳",
    ("happy", "calm", "M", "H"): "特别安心，心里亮堂堂",
    ("happy", "calm", "H", "L"): "超开心，整个人很放松",
    ("happy", "calm", "H", "M"): "满心欢喜又很踏实",
    ("happy", "calm", "H", "H"): "幸福感拉满，超安心",

    # --- 第 2 对: ("happy", "sweet") 开心 + 甜蜜 ---
    ("happy", "sweet", "L", "L"): "心情挺好，还有点害羞",
    ("happy", "sweet", "L", "M"): "心里甜丝丝有点开心",
    ("happy", "sweet", "L", "H"): "甜得发晕带点小喜悦",
    ("happy", "sweet", "M", "L"): "蛮开心的，带点小甜意",
    ("happy", "sweet", "M", "M"): "又甜又开心，美滋滋的",
    ("happy", "sweet", "M", "H"): "甜到心坎里，开心偷笑",
    ("happy", "sweet", "H", "L"): "超开心还带点小娇羞",
    ("happy", "sweet", "H", "M"): "开心坏了，心里甜滋滋",
    ("happy", "sweet", "H", "H"): "又甜又欢喜，幸福冒泡",

    # --- 第 3 对: ("happy", "proud") 开心 + 得意 ---
    ("happy", "proud", "L", "L"): "有点小开心，小得意",
    ("happy", "proud", "L", "M"): "暗暗得意，心情挺美",
    ("happy", "proud", "L", "H"): "得意得不行，还偷着乐",
    ("happy", "proud", "M", "L"): "蛮开心的，带点小傲娇",
    ("happy", "proud", "M", "M"): "又乐又得意，求夸夸",
    ("happy", "proud", "M", "H"): "超得意，心情好到起飞",
    ("happy", "proud", "H", "L"): "超开心略微有点神气",
    ("happy", "proud", "H", "M"): "乐开花啦，傲娇炫耀中",
    ("happy", "proud", "H", "H"): "开心又膨胀，神气坏了",

    # --- 第 4 对: ("happy", "worry") 开心 + 担心 ---
    ("happy", "worry", "L", "L"): "心情还好，稍微有点惦",
    ("happy", "worry", "L", "M"): "挺开心的，但有点操心",
    ("happy", "worry", "L", "H"): "装作开心，其实很心疼",
    ("happy", "worry", "M", "L"): "聊得蛮开心，顺带叮嘱",
    ("happy", "worry", "M", "M"): "一边开心着一边有点急",
    ("happy", "worry", "M", "H"): "心情复杂，心疼但欣慰",
    ("happy", "worry", "H", "L"): "很开心，只是微有牵挂",
    ("happy", "worry", "H", "M"): "超高兴，但还是有点悬",
    ("happy", "worry", "H", "H"): "又喜又揪心，情绪翻涌",

    # --- 第 5 对: ("happy", "sulky") 开心 + 委屈 ---
    ("happy", "sulky", "L", "L"): "被逗乐了还剩点闷气",
    ("happy", "sulky", "L", "M"): "憋着点委屈，但想笑了",
    ("happy", "sulky", "L", "H"): "很委屈，但有点想笑",
    ("happy", "sulky", "M", "L"): "被哄好大半，假装还气",
    ("happy", "sulky", "M", "M"): "破涕为笑，还带点委屈",
    ("happy", "sulky", "M", "H"): "委屈巴巴却被逗笑了",
    ("happy", "sulky", "H", "L"): "早开心了，嘴硬记一笔",
    ("happy", "sulky", "H", "M"): "超开心但念着刚才委屈",
    ("happy", "sulky", "H", "H"): "又委屈又大笑，蛮酸甜",

    # --- 第 6 对: ("happy", "jealous") 开心 + 吃醋 ---
    ("happy", "jealous", "L", "L"): "心情不错，就是有点酸",
    ("happy", "jealous", "L", "M"): "嘴角有点乐，心里泛酸",
    ("happy", "jealous", "L", "H"): "被顺毛了，但醋意没散",
    ("happy", "jealous", "M", "L"): "蛮开心的，略带点吃味",
    ("happy", "jealous", "M", "M"): "心里乐呵，嘴上有点酸",
    ("happy", "jealous", "M", "H"): "醋意挺重，但被哄乐了",
    ("happy", "jealous", "H", "L"): "超开心，顺便宣示一下",
    ("happy", "jealous", "H", "M"): "哄得超开心但有点护食",
    ("happy", "jealous", "H", "H"): "又酸又狂喜，想独占",

    # --- 第 7 对: ("calm", "sweet") 踏实 + 甜蜜 ---
    ("calm", "sweet", "L", "L"): "心态安稳泛着浅甜意",
    ("calm", "sweet", "L", "M"): "心里软乎乎，有点害羞",
    ("calm", "sweet", "L", "H"): "好甜好羞但觉得很安稳",
    ("calm", "sweet", "M", "L"): "踏实舒坦，带一点微甜",
    ("calm", "sweet", "M", "M"): "安稳又甜软，很温柔",
    ("calm", "sweet", "M", "H"): "很安心，被甜意泡软了",
    ("calm", "sweet", "H", "L"): "超有安全感微甜又惬意",
    ("calm", "sweet", "H", "M"): "无比踏实，满心甜意",
    ("calm", "sweet", "H", "H"): "彻底安心，甜腻又依恋",

    # --- 第 8 对: ("calm", "proud") 踏实 + 得意 ---
    ("calm", "proud", "L", "L"): "挺悠哉的，暗暗小得意",
    ("calm", "proud", "L", "M"): "稳稳当当的，有点傲娇",
    ("calm", "proud", "L", "H"): "看着淡定，其实超得意",
    ("calm", "proud", "M", "L"): "舒坦自在，略带小自豪",
    ("calm", "proud", "M", "M"): "笃定从容，心里小骄傲",
    ("calm", "proud", "M", "H"): "稳坐C位，得意坏了",
    ("calm", "proud", "H", "L"): "很踏实，还带着小得意",
    ("calm", "proud", "H", "M"): "安稳惬意，难掩小骄傲",
    ("calm", "proud", "H", "H"): "从容自信，有种大佬感",

    # --- 第 9 对: ("calm", "worry") 踏实 + 担心 ---
    ("calm", "worry", "L", "L"): "心态挺稳微有点挂念",
    ("calm", "worry", "L", "M"): "看着平静心里有点操心",
    ("calm", "worry", "L", "H"): "勉强镇定，其实很心疼",
    ("calm", "worry", "M", "L"): "很踏实，顺便留心关切",
    ("calm", "worry", "M", "M"): "安稳守候着，温柔担忧",
    ("calm", "worry", "M", "H"): "心情沉稳，但满是担忧",
    ("calm", "worry", "H", "L"): "无比安心，轻声叮嘱着",
    ("calm", "worry", "H", "M"): "踏实温柔，替对方发愁",
    ("calm", "worry", "H", "H"): "心疼坏了，但保持镇定",

    # --- 第 10 对: ("calm", "sulky") 踏实 + 委屈 ---
    ("calm", "sulky", "L", "L"): "情绪渐稳剩点小别扭",
    ("calm", "sulky", "L", "M"): "安静生闷气，不想多说",
    ("calm", "sulky", "L", "H"): "不吵不闹，闷着头委屈",
    ("calm", "sulky", "M", "L"): "被安抚大半，挺平静的",
    ("calm", "sulky", "M", "M"): "气消大半，还有点闷",
    ("calm", "sulky", "M", "H"): "愿意听哄了但还是委屈",
    ("calm", "sulky", "H", "L"): "已被哄乖，心里很踏实",
    ("calm", "sulky", "H", "M"): "踏实下来嘴上还哼唧",
    ("calm", "sulky", "H", "H"): "委屈被抚平，安静依赖",

    # --- 第 11 对: ("calm", "jealous") 踏实 + 吃醋 ---
    ("calm", "jealous", "L", "L"): "看着淡定暗暗有点醋意",
    ("calm", "jealous", "L", "M"): "装不在意，其实有点酸",
    ("calm", "jealous", "L", "H"): "外表冷静，闷声喝大醋",
    ("calm", "jealous", "M", "L"): "挺平静，略带点小醋意",
    ("calm", "jealous", "M", "M"): "不慌不忙地有点护食",
    ("calm", "jealous", "M", "H"): "看着镇定其实占有欲强",
    ("calm", "jealous", "H", "L"): "很踏实微微泛点小醋意",
    ("calm", "jealous", "H", "M"): "安稳又霸道，略微吃味",
    ("calm", "jealous", "H", "H"): "笃定主权，醋意沉稳涌",

    # --- 第 12 对: ("sweet", "proud") 甜蜜 + 得意 ---
    ("sweet", "proud", "L", "L"): "有点害羞，暗自小得意",
    ("sweet", "proud", "L", "M"): "嘴上傲娇心里其实受用",
    ("sweet", "proud", "L", "H"): "傲娇暗爽",
    ("sweet", "proud", "M", "L"): "心里挺甜还有点小神气",
    ("sweet", "proud", "M", "M"): "又羞又得意嘴硬爱听夸",
    ("sweet", "proud", "M", "H"): "超得意，带点害羞傲娇",
    ("sweet", "proud", "H", "L"): "甜丝丝有点小骄傲",
    ("sweet", "proud", "H", "M"): "被宠得心花怒放小骄傲",
    ("sweet", "proud", "H", "H"): "甜到发晕骄傲得不得了",

    # --- 第 13 对: ("sweet", "worry") 甜蜜 + 担心 ---
    ("sweet", "worry", "L", "L"): "带点羞涩又有点小关心",
    ("sweet", "worry", "L", "M"): "心里软乎乎，挺挂念的",
    ("sweet", "worry", "L", "H"): "心疼坏了带着温柔羞怯",
    ("sweet", "worry", "M", "L"): "心里甜丝丝顺便惦记着",
    ("sweet", "worry", "M", "M"): "又甜又心疼，语气温柔",
    ("sweet", "worry", "M", "H"): "满心心疼，又羞又担忧",
    ("sweet", "worry", "H", "L"): "甜到不行还带点小牵挂",
    ("sweet", "worry", "H", "M"): "好害羞，又心疼又喜欢",
    ("sweet", "worry", "H", "H"): "爱怜满溢，心疼到极点",

    # --- 第 14 对: ("sweet", "sulky") 甜蜜 + 委屈 ---
    ("sweet", "sulky", "L", "L"): "有点小别扭但带点甜意",
    ("sweet", "sulky", "L", "M"): "心里委屈其实想撒娇",
    ("sweet", "sulky", "L", "H"): "好委屈但又有点舍不得",
    ("sweet", "sulky", "M", "L"): "被哄害羞了还假装赌气",
    ("sweet", "sulky", "M", "M"): "又羞又委屈想求哄求抱",
    ("sweet", "sulky", "M", "H"): "特别委屈却软软想撒娇",
    ("sweet", "sulky", "H", "L"): "心里甜软只剩一点娇嗔",
    ("sweet", "sulky", "H", "M"): "羞得发烫委屈全化撒娇",
    ("sweet", "sulky", "H", "H"): "甜得发软带着委屈依恋",

    # --- 第 15 对: ("sweet", "jealous") 甜蜜 + 吃醋 ---
    ("sweet", "jealous", "L", "L"): "有点害羞，带点小护食",
    ("sweet", "jealous", "L", "M"): "酸溜溜其实想讨偏爱",
    ("sweet", "jealous", "L", "H"): "醋意大发偏要独占偏爱",
    ("sweet", "jealous", "M", "L"): "甜丝丝的略微有点吃味",
    ("sweet", "jealous", "M", "M"): "嘴硬吃醋其实心里挺甜",
    ("sweet", "jealous", "M", "H"): "酸得不行却又有点心动",
    ("sweet", "jealous", "H", "L"): "超级害羞顺便宣示主权",
    ("sweet", "jealous", "H", "M"): "又甜又护食",
    ("sweet", "jealous", "H", "H"): "甜到发慌占有欲大爆发",

    # --- 第 16 对: ("proud", "worry") 得意 + 担心 ---
    ("proud", "worry", "L", "L"): "嘴硬不管，其实暗留意",
    ("proud", "worry", "L", "M"): "嘴上傲娇心里还是操心",
    ("proud", "worry", "L", "H"): "急得要命嘴上却还嫌弃",
    ("proud", "worry", "M", "L"): "挺神气的顺带关心一下",
    ("proud", "worry", "M", "M"): "刀子嘴豆腐心傲娇护短",
    ("proud", "worry", "M", "H"): "嘴硬傲娇其实满眼心疼",
    ("proud", "worry", "H", "L"): "超自信，特别想罩着",
    ("proud", "worry", "H", "M"): "神气包揽一切暗自操心",
    ("proud", "worry", "H", "H"): "又霸道又心疼硬气照顾",

    # --- 第 17 对: ("proud", "sulky") 得意 + 委屈 ---
    ("proud", "sulky", "L", "L"): "哼了一声带点傲娇别扭",
    ("proud", "sulky", "L", "M"): "受了委屈还要强撑面子",
    ("proud", "sulky", "L", "H"): "委屈得发酸偏要不服输",
    ("proud", "sulky", "M", "L"): "傲娇起来嫌被夸得不够",
    ("proud", "sulky", "M", "M"): "又气又傲娇等别人顺毛",
    ("proud", "sulky", "M", "H"): "好委屈但傲娇死鸭嘴硬",
    ("proud", "sulky", "H", "L"): "超得意顺便傲娇记小账",
    ("proud", "sulky", "H", "M"): "自尊心强被冷落有点气",
    ("proud", "sulky", "H", "H"): "炸毛傲娇委屈控诉不平",

    # --- 第 18 对: ("proud", "jealous") 得意 + 吃醋 ---
    ("proud", "jealous", "L", "L"): "轻哼一下略微有点吃味",
    ("proud", "jealous", "L", "M"): "酸溜溜觉得本喵更好",
    ("proud", "jealous", "L", "H"): "醋火中烧傲娇哼不稀罕",
    ("proud", "jealous", "M", "L"): "小骄傲顺便盯梢吃点醋",
    ("proud", "jealous", "M", "M"): "傲娇哼气变着法想争宠",
    ("proud", "jealous", "M", "H"): "吃醋炸毛，只想被偏爱",
    ("proud", "jealous", "H", "L"): "自信爆棚不把情敌放眼",
    ("proud", "jealous", "H", "M"): "超神气，本喵才是唯一",
    ("proud", "jealous", "H", "H"): "霸道猫皇气场护食极了",

    # --- 第 19 对: ("worry", "sulky") 担心 + 委屈 ---
    ("worry", "sulky", "L", "L"): "心里闷闷的又有点惦记",
    ("worry", "sulky", "L", "M"): "有点小委屈但还是操心",
    ("worry", "sulky", "L", "H"): "满心委屈却又怕出差错",
    ("worry", "sulky", "M", "L"): "挺担心的略带一点失落",
    ("worry", "sulky", "M", "M"): "既心疼又觉得被冷落了",
    ("worry", "sulky", "M", "H"): "很委屈但心里还是着急",
    ("worry", "sulky", "H", "L"): "非常担心忍不住小抱怨",
    ("worry", "sulky", "H", "M"): "心疼到极点有点小怨气",
    ("worry", "sulky", "H", "H"): "又急又委屈快绷不住了",

    # --- 第 20 对: ("worry", "jealous") 担心 + 吃醋 ---
    ("worry", "jealous", "L", "L"): "暗自酸一下又有点担心",
    ("worry", "jealous", "L", "M"): "嘴上泛酸其实在偷担心",
    ("worry", "jealous", "L", "H"): "大吃飞醋心里却还牵挂",
    ("worry", "jealous", "M", "L"): "患得患失带着点小醋意",
    ("worry", "jealous", "M", "M"): "既怕太累了又怕被抢走",
    ("worry", "jealous", "M", "H"): "酸得揪心，紧张得不行",
    ("worry", "jealous", "H", "L"): "满心牵挂顺带吃个小醋",
    ("worry", "jealous", "H", "M"): "心疼与占有欲一起翻涌",
    ("worry", "jealous", "H", "H"): "慌乱护食怕出事又怕抢",

    # --- 第 21 对: ("sulky", "jealous") 委屈 + 吃醋 ---
    ("sulky", "jealous", "L", "L"): "有点小别扭泛起一点酸",
    ("sulky", "jealous", "L", "M"): "酸溜溜赌气觉得被偏心",
    ("sulky", "jealous", "L", "H"): "醋坛翻了赌气不想理人",
    ("sulky", "jealous", "M", "L"): "闷闷不乐觉得不够受宠",
    ("sulky", "jealous", "M", "M"): "又酸又委屈正在生闷气",
    ("sulky", "jealous", "M", "H"): "满嘴酸话委屈得直冒泡",
    ("sulky", "jealous", "H", "L"): "觉得受冷落委屈得泛酸",
    ("sulky", "jealous", "H", "M"): "好委屈觉得偏心别人了",
    ("sulky", "jealous", "H", "H"): "吃醋委屈大爆发难哄了",
}


# ============================================================
# 4. 3+ 维激活：C(7,3) = 35 组 × 6 档 (3种第一主角 × 轻中M/强爆H 2档) (210 条)
#    键格式: (k1, k2, k3, primary_dim, intensity)
#    其中 (k1, k2, k3) 按 DIM_ORDER 规范排序，primary_dim ∈ {k1, k2, k3}, intensity ∈ {"M", "H"}
# ============================================================
TRIPLE_DIM_LEXICON: dict[tuple[str, str, str, str, str], str] = {
    # --- 第 1 组: ("happy", "calm", "sweet") 开心 + 踏实 + 甜蜜 ---
    ("happy", "calm", "sweet", "happy", "M"): "开心安稳还带点微甜",
    ("happy", "calm", "sweet", "happy", "H"): "超开心又踏实，心里甜",
    ("happy", "calm", "sweet", "calm", "M"): "安心舒坦，带点小甜意",
    ("happy", "calm", "sweet", "calm", "H"): "无比安心，开心又甜软",
    ("happy", "calm", "sweet", "sweet", "M"): "心里甜丝丝，开心安稳",
    ("happy", "calm", "sweet", "sweet", "H"): "甜到心里去，安心愉悦",

    # --- 第 2 组: ("happy", "calm", "proud") 开心 + 踏实 + 得意 ---
    ("happy", "calm", "proud", "happy", "M"): "开心舒坦，带点小得意",
    ("happy", "calm", "proud", "happy", "H"): "超开心又稳，略微臭屁",
    ("happy", "calm", "proud", "calm", "M"): "很踏实自在，心情挺美",
    ("happy", "calm", "proud", "calm", "H"): "无比安稳，开心小骄傲",
    ("happy", "calm", "proud", "proud", "M"): "挺得意，心情松弛愉快",
    ("happy", "calm", "proud", "proud", "H"): "超得意又笃定，乐呵着",

    # --- 第 3 组: ("happy", "calm", "worry") 开心 + 踏实 + 担心 ---
    ("happy", "calm", "worry", "happy", "M"): "挺开心安稳，微有挂念",
    ("happy", "calm", "worry", "happy", "H"): "心情很好，但温柔惦记",
    ("happy", "calm", "worry", "calm", "M"): "安稳顺心，顺带小操心",
    ("happy", "calm", "worry", "calm", "H"): "特别踏实，带笑替分忧",
    ("happy", "calm", "worry", "worry", "M"): "略微操心，但总体挺好",
    ("happy", "calm", "worry", "worry", "H"): "挺担心的，但保持安心",

    # --- 第 4 组: ("happy", "calm", "sulky") 开心 + 踏实 + 委屈 ---
    ("happy", "calm", "sulky", "happy", "M"): "被哄顺心，剩点小闷气",
    ("happy", "calm", "sulky", "happy", "H"): "破涕为笑，已经踏实了",
    ("happy", "calm", "sulky", "calm", "M"): "心情平复，带笑小别扭",
    ("happy", "calm", "sulky", "calm", "H"): "被安抚好了，顺气开心",
    ("happy", "calm", "sulky", "sulky", "M"): "还有点委屈，心已安稳",
    ("happy", "calm", "sulky", "sulky", "H"): "很委屈但已被哄顺了些",

    # --- 第 5 组: ("happy", "calm", "jealous") 开心 + 踏实 + 吃醋 ---
    ("happy", "calm", "jealous", "happy", "M"): "开心踏实，略带点醋意",
    ("happy", "calm", "jealous", "happy", "H"): "超开心又稳，小护食中",
    ("happy", "calm", "jealous", "calm", "M"): "很淡定，开心但有点酸",
    ("happy", "calm", "jealous", "calm", "H"): "安稳宣主权，心情不错",
    ("happy", "calm", "jealous", "jealous", "M"): "有点吃味，但心里踏实",
    ("happy", "calm", "jealous", "jealous", "H"): "醋意挺重，却安心乐呵",

    # --- 第 6 组: ("happy", "sweet", "proud") 开心 + 甜蜜 + 得意 ---
    ("happy", "sweet", "proud", "happy", "M"): "开心害羞，带点小得意",
    ("happy", "sweet", "proud", "happy", "H"): "超开心，又甜又小神气",
    ("happy", "sweet", "proud", "sweet", "M"): "心里甜丝丝，开心傲娇",
    ("happy", "sweet", "proud", "sweet", "H"): "甜晕了，还骄傲美滋滋",
    ("happy", "sweet", "proud", "proud", "M"): "傲娇暗爽，藏不住甜笑",
    ("happy", "sweet", "proud", "proud", "H"): "得意害羞，被宠上天啦",

    # --- 第 7 组: ("happy", "sweet", "worry") 开心 + 甜蜜 + 担心 ---
    ("happy", "sweet", "worry", "happy", "M"): "开心微甜，顺便小关心",
    ("happy", "sweet", "worry", "happy", "H"): "超开心甜软，温柔挂念",
    ("happy", "sweet", "worry", "sweet", "M"): "有点害羞开心，微惦记",
    ("happy", "sweet", "worry", "sweet", "H"): "甜到心坎，又心疼关心",
    ("happy", "sweet", "worry", "worry", "M"): "挺担心的，但被哄微甜",
    ("happy", "sweet", "worry", "worry", "H"): "好心疼，又甜又喜迎着",

    # --- 第 8 组: ("happy", "sweet", "sulky") 开心 + 甜蜜 + 委屈 ---
    ("happy", "sweet", "sulky", "happy", "M"): "被哄羞笑，假装小别扭",
    ("happy", "sweet", "sulky", "happy", "H"): "超开心撒娇，记一小笔",
    ("happy", "sweet", "sulky", "sweet", "M"): "害羞发甜，委屈快消了",
    ("happy", "sweet", "sulky", "sweet", "H"): "甜软到不行，带点娇嗔",
    ("happy", "sweet", "sulky", "sulky", "M"): "小委屈被甜意哄乐了些",
    ("happy", "sweet", "sulky", "sulky", "H"): "好委屈，但又羞甜原谅",

    # --- 第 9 组: ("happy", "sweet", "jealous") 开心 + 甜蜜 + 吃醋 ---
    ("happy", "sweet", "jealous", "happy", "M"): "开心发甜，带点小醋意",
    ("happy", "sweet", "jealous", "happy", "H"): "超开心甜腻，宣示独占",
    ("happy", "sweet", "jealous", "sweet", "M"): "害羞发甜，偷偷小吃味",
    ("happy", "sweet", "jealous", "sweet", "H"): "甜到心尖，又护食偷乐",
    ("happy", "sweet", "jealous", "jealous", "M"): "酸溜撒娇，被哄得挺甜",
    ("happy", "sweet", "jealous", "jealous", "H"): "大吃飞醋，被宠得甜乐",

    # --- 第 10 组: ("happy", "proud", "worry") 开心 + 得意 + 担心 ---
    ("happy", "proud", "worry", "happy", "M"): "开心得意，顺手小操心",
    ("happy", "proud", "worry", "happy", "H"): "超开心神气，满怀关心",
    ("happy", "proud", "worry", "proud", "M"): "傲娇显摆，顺带照顾着",
    ("happy", "proud", "worry", "proud", "H"): "得意护短，心里美滋滋",
    ("happy", "proud", "worry", "worry", "M"): "嘴硬操心，被夸又偷乐",
    ("happy", "proud", "worry", "worry", "H"): "心疼坏了，还傲娇哄着",

    # --- 第 11 组: ("happy", "proud", "sulky") 开心 + 得意 + 委屈 ---
    ("happy", "proud", "sulky", "happy", "M"): "被夸乐了，还要傲娇哼",
    ("happy", "proud", "sulky", "happy", "H"): "超开心，嘴硬勉强原谅",
    ("happy", "proud", "sulky", "proud", "M"): "傲娇拿乔，其实挺开心",
    ("happy", "proud", "sulky", "proud", "H"): "得意记仇，嘴硬暗暗乐",
    ("happy", "proud", "sulky", "sulky", "M"): "小脾气未消但暗自爽了",
    ("happy", "proud", "sulky", "sulky", "H"): "委屈炸毛，被捧得破功",

    # --- 第 12 组: ("happy", "proud", "jealous") 开心 + 得意 + 吃醋 ---
    ("happy", "proud", "jealous", "happy", "M"): "开心吃醋，趁机小炫耀",
    ("happy", "proud", "jealous", "happy", "H"): "狂喜赢偏爱，神气独占",
    ("happy", "proud", "jealous", "proud", "M"): "得意哼声，觉得我更好",
    ("happy", "proud", "jealous", "proud", "H"): "超级得意，霸道宣主权",
    ("happy", "proud", "jealous", "jealous", "M"): "酸溜争宠，赢了偷乐呵",
    ("happy", "proud", "jealous", "jealous", "H"): "醋火中烧，因偏爱神气",

    # --- 第 13 组: ("happy", "worry", "sulky") 开心 + 担心 + 委屈 ---
    ("happy", "worry", "sulky", "happy", "M"): "破涕为笑，委屈又叮嘱",
    ("happy", "worry", "sulky", "happy", "H"): "又哭又笑，终于放心了",
    ("happy", "worry", "sulky", "worry", "M"): "挺担心的，但被哄笑了",
    ("happy", "worry", "sulky", "worry", "H"): "心疼委屈，见安好才笑",
    ("happy", "worry", "sulky", "sulky", "M"): "受小委屈，操心被逗乐",
    ("happy", "worry", "sulky", "sulky", "H"): "委屈想哭，却心疼笑场",

    # --- 第 14 组: ("happy", "worry", "jealous") 开心 + 担心 + 吃醋 ---
    ("happy", "worry", "jealous", "happy", "M"): "开心接话，酸溜叮嘱着",
    ("happy", "worry", "jealous", "happy", "H"): "欢喜守候，护食又操心",
    ("happy", "worry", "jealous", "worry", "M"): "有点担心，泛酸被逗笑",
    ("happy", "worry", "jealous", "worry", "H"): "牵挂带醋，终被哄开心",
    ("happy", "worry", "jealous", "jealous", "M"): "吃味嘀咕，被哄又偷乐",
    ("happy", "worry", "jealous", "jealous", "H"): "醋意牵挂，顺毛被逗笑",

    # --- 第 15 组: ("happy", "sulky", "jealous") 开心 + 委屈 + 吃醋 ---
    ("happy", "sulky", "jealous", "happy", "M"): "被哄乐了，嘴上酸记账",
    ("happy", "sulky", "jealous", "happy", "H"): "笑出声来，还想小赌气",
    ("happy", "sulky", "jealous", "sulky", "M"): "委屈泛酸，但憋不住笑",
    ("happy", "sulky", "jealous", "sulky", "H"): "红眼吃醋，被哄笑开花",
    ("happy", "sulky", "jealous", "jealous", "M"): "酸溜闹别扭其实乐了",
    ("happy", "sulky", "jealous", "jealous", "H"): "打翻醋坛，委屈秒逗笑",

    # --- 第 16 组: ("calm", "sweet", "proud") 踏实 + 甜蜜 + 得意 ---
    ("calm", "sweet", "proud", "calm", "M"): "安稳惬意，羞甜小傲娇",
    ("calm", "sweet", "proud", "calm", "H"): "无比舒坦，享独宠神气",
    ("calm", "sweet", "proud", "sweet", "M"): "甜软踏实，带点小得意",
    ("calm", "sweet", "proud", "sweet", "H"): "甜腻安心，觉得被偏宠",
    ("calm", "sweet", "proud", "proud", "M"): "淡定小骄傲，心里挺甜",
    ("calm", "sweet", "proud", "proud", "H"): "傲娇满足，安稳又甜软",

    # --- 第 17 组: ("calm", "sweet", "worry") 踏实 + 甜蜜 + 担心 ---
    ("calm", "sweet", "worry", "calm", "M"): "安静守候，微甜带关切",
    ("calm", "sweet", "worry", "calm", "H"): "踏实温柔，抚平小操心",
    ("calm", "sweet", "worry", "sweet", "M"): "软声微甜，安心又挂念",
    ("calm", "sweet", "worry", "sweet", "H"): "羞甜依偎，满怀心疼着",
    ("calm", "sweet", "worry", "worry", "M"): "轻声操心，但心态安稳",
    ("calm", "sweet", "worry", "worry", "H"): "心疼极了，仍温柔安稳",

    # --- 第 18 组: ("calm", "sweet", "sulky") 踏实 + 甜蜜 + 委屈 ---
    ("calm", "sweet", "sulky", "calm", "M"): "情绪渐稳，带甜软鼻音",
    ("calm", "sweet", "sulky", "calm", "H"): "彻底安稳，羞甜消气啦",
    ("calm", "sweet", "sulky", "sweet", "M"): "害羞发甜，小委屈快化",
    ("calm", "sweet", "sulky", "sweet", "H"): "甜软依恋，把委屈揉碎",
    ("calm", "sweet", "sulky", "sulky", "M"): "小委屈着，但很安心甜",
    ("calm", "sweet", "sulky", "sulky", "H"): "委屈想哭，却安心甜软",

    # --- 第 19 组: ("calm", "sweet", "jealous") 踏实 + 甜蜜 + 吃醋 ---
    ("calm", "sweet", "jealous", "calm", "M"): "安稳惬意，微甜泛小酸",
    ("calm", "sweet", "jealous", "calm", "H"): "无比笃定，甜软小独占",
    ("calm", "sweet", "jealous", "sweet", "M"): "害羞发甜，略带点醋意",
    ("calm", "sweet", "jealous", "sweet", "H"): "甜到心窝，安静占偏爱",
    ("calm", "sweet", "jealous", "jealous", "M"): "闷吃小醋，安心求偏爱",
    ("calm", "sweet", "jealous", "jealous", "H"): "酸意涌动，仍安心被宠",

    # --- 第 20 组: ("calm", "proud", "worry") 踏实 + 得意 + 担心 ---
    ("calm", "proud", "worry", "calm", "M"): "心态沉稳，傲娇小叮嘱",
    ("calm", "proud", "worry", "calm", "H"): "安稳兜底，神气叫放心",
    ("calm", "proud", "worry", "proud", "M"): "淡定自豪，顺手照顾着",
    ("calm", "proud", "worry", "proud", "H"): "笃定罩着，暗暗挺操心",
    ("calm", "proud", "worry", "worry", "M"): "默默操心，面上挺笃定",
    ("calm", "proud", "worry", "worry", "H"): "满心牵挂，仍沉着撑腰",

    # --- 第 21 组: ("calm", "proud", "sulky") 踏实 + 得意 + 委屈 ---
    ("calm", "proud", "sulky", "calm", "M"): "情绪平复，还微带傲娇",
    ("calm", "proud", "sulky", "calm", "H"): "安稳顺气，傲娇算过关",
    ("calm", "proud", "sulky", "proud", "M"): "端着架子，等个台阶下",
    ("calm", "proud", "sulky", "proud", "H"): "高冷别扭，其实心已稳",
    ("calm", "proud", "sulky", "sulky", "M"): "安静生闷气，还撑面子",
    ("calm", "proud", "sulky", "sulky", "H"): "闷声委屈，笃定会被哄",

    # --- 第 22 组: ("calm", "proud", "jealous") 踏实 + 得意 + 吃醋 ---
    ("calm", "proud", "jealous", "calm", "M"): "淡定吃味，心里仍笃定",
    ("calm", "proud", "jealous", "calm", "H"): "稳坐正位，傲娇睨一眼",
    ("calm", "proud", "jealous", "proud", "M"): "从容傲娇，觉本喵更好",
    ("calm", "proud", "jealous", "proud", "H"): "猫皇气场，淡定防情敌",
    ("calm", "proud", "jealous", "jealous", "M"): "暗暗泛酸，气定等表态",
    ("calm", "proud", "jealous", "jealous", "H"): "闷醋翻腾，高傲定主权",

    # --- 第 23 组: ("calm", "worry", "sulky") 踏实 + 担心 + 委屈 ---
    ("calm", "worry", "sulky", "calm", "M"): "安静守着，藏委屈操心",
    ("calm", "worry", "sulky", "calm", "H"): "沉静陪伴，带委屈心疼",
    ("calm", "worry", "sulky", "worry", "M"): "闷声挂念，顾不上别扭",
    ("calm", "worry", "sulky", "worry", "H"): "心疼压过委屈，稳守着",
    ("calm", "worry", "sulky", "sulky", "M"): "乖憋小委屈，默默惦记",
    ("calm", "worry", "sulky", "sulky", "H"): "委屈想哭，又怕累着了",

    # --- 第 24 组: ("calm", "worry", "jealous") 踏实 + 担心 + 吃醋 ---
    ("calm", "worry", "jealous", "calm", "M"): "平静陪伴，微酸又惦记",
    ("calm", "worry", "jealous", "calm", "H"): "沉稳守候，醋意化关切",
    ("calm", "worry", "jealous", "worry", "M"): "默默操心，吃个安静醋",
    ("calm", "worry", "jealous", "worry", "H"): "满眼担忧，紧张怕被抢",
    ("calm", "worry", "jealous", "jealous", "M"): "闷泛酸水，放不下操心",
    ("calm", "worry", "jealous", "jealous", "H"): "暗醋汹涌，仍冷静守护",

    # --- 第 25 组: ("calm", "sulky", "jealous") 踏实 + 委屈 + 吃醋 ---
    ("calm", "sulky", "jealous", "calm", "M"): "安静沉默，微酸小别扭",
    ("calm", "sulky", "jealous", "calm", "H"): "平复呼吸，闷消化酸屈",
    ("calm", "sulky", "jealous", "sulky", "M"): "乖缩一旁，酸溜生闷气",
    ("calm", "sulky", "jealous", "sulky", "H"): "沉默委屈，满腹小酸楚",
    ("calm", "sulky", "jealous", "jealous", "M"): "闷喝飞醋，委屈不想说",
    ("calm", "sulky", "jealous", "jealous", "H"): "安静吃大醋，眼神幽怨",

    # --- 第 26 组: ("sweet", "proud", "worry") 甜蜜 + 得意 + 担心 ---
    ("sweet", "proud", "worry", "sweet", "M"): "有点害羞，傲娇替操心",
    ("sweet", "proud", "worry", "sweet", "H"): "甜软依赖，嘴硬心疼着",
    ("sweet", "proud", "worry", "proud", "M"): "傲娇包揽，害羞又照顾",
    ("sweet", "proud", "worry", "proud", "H"): "神气护短，心里满宠溺",
    ("sweet", "proud", "worry", "worry", "M"): "刀子嘴操心，害羞心疼",
    ("sweet", "proud", "worry", "worry", "H"): "急得不行，傲娇强照顾",

    # --- 第 27 组: ("sweet", "proud", "sulky") 甜蜜 + 得意 + 委屈 ---
    ("sweet", "proud", "sulky", "sweet", "M"): "害羞撒娇，傲娇记小账",
    ("sweet", "proud", "sulky", "sweet", "H"): "甜软依恋，嘴硬怨冷落",
    ("sweet", "proud", "sulky", "proud", "M"): "傲娇拿乔，羞意小委屈",
    ("sweet", "proud", "sulky", "proud", "H"): "求顺毛呢，其实心里甜",
    ("sweet", "proud", "sulky", "sulky", "M"): "小委屈着，被夸又害羞",
    ("sweet", "proud", "sulky", "sulky", "H"): "委屈想哭，还傲娇羞怯",

    # --- 第 28 组: ("sweet", "proud", "jealous") 甜蜜 + 得意 + 吃醋 ---
    ("sweet", "proud", "jealous", "sweet", "M"): "害羞贴紧，傲娇吃小醋",
    ("sweet", "proud", "jealous", "sweet", "H"): "甜到发烫，霸道想独占",
    ("sweet", "proud", "jealous", "proud", "M"): "得意挑眉，泛酸求偏爱",
    ("sweet", "proud", "jealous", "proud", "H"): "傲娇吃醋，想听真告白",
    ("sweet", "proud", "jealous", "jealous", "M"): "酸溜嘴硬，被哄害羞了",
    ("sweet", "proud", "jealous", "jealous", "H"): "醋意炸毛，又羞又霸占",

    # --- 第 29 组: ("sweet", "worry", "sulky") 甜蜜 + 担心 + 委屈 ---
    ("sweet", "worry", "sulky", "sweet", "M"): "软声撒娇，又委屈又疼",
    ("sweet", "worry", "sulky", "sweet", "H"): "羞怯心疼，委屈想抱抱",
    ("sweet", "worry", "sulky", "worry", "M"): "暗暗操心，带甜软委屈",
    ("sweet", "worry", "sulky", "worry", "H"): "心疼坏了，羞软又依恋",
    ("sweet", "worry", "sulky", "sulky", "M"): "委屈别扭，还害羞挂念",
    ("sweet", "worry", "sulky", "sulky", "H"): "好委屈呀，又心疼难受",

    # --- 第 30 组: ("sweet", "worry", "jealous") 甜蜜 + 担心 + 吃醋 ---
    ("sweet", "worry", "jealous", "sweet", "M"): "有点害羞，微酸又操心",
    ("sweet", "worry", "jealous", "sweet", "H"): "甜腻依赖，怕被抢又疼",
    ("sweet", "worry", "jealous", "worry", "M"): "软声挂念，暗泛小醋泡",
    ("sweet", "worry", "jealous", "worry", "H"): "揪心牵挂，羞怯护食紧",
    ("sweet", "worry", "jealous", "jealous", "M"): "酸溜撒娇，放不下心疼",
    ("sweet", "worry", "jealous", "jealous", "H"): "吃醋发慌，仍满眼心疼",

    # --- 第 31 组: ("sweet", "sulky", "jealous") 甜蜜 + 委屈 + 吃醋 ---
    ("sweet", "sulky", "jealous", "sweet", "M"): "羞怯撒娇，酸溜小委屈",
    ("sweet", "sulky", "jealous", "sweet", "H"): "害羞发甜，又酸又求宠",
    ("sweet", "sulky", "jealous", "sulky", "M"): "委屈吃醋，害羞想讨哄",
    ("sweet", "sulky", "jealous", "sulky", "H"): "委屈泛酸，依恋想求哄",
    ("sweet", "sulky", "jealous", "jealous", "M"): "醋混委屈，害羞小哼唧",
    ("sweet", "sulky", "jealous", "jealous", "H"): "酸到委屈，羞恼求独占",

    # --- 第 32 组: ("proud", "worry", "sulky") 得意 + 担心 + 委屈 ---
    ("proud", "worry", "sulky", "proud", "M"): "嘴硬傲娇，小委屈替急",
    ("proud", "worry", "sulky", "proud", "H"): "傲娇数落，其实心疼极",
    ("proud", "worry", "sulky", "worry", "M"): "刀子嘴操心，憋小闷气",
    ("proud", "worry", "sulky", "worry", "H"): "心疼极了，嘴硬带委屈",
    ("proud", "worry", "sulky", "sulky", "M"): "受委屈了，还强撑傲娇",
    ("proud", "worry", "sulky", "sulky", "H"): "委屈红眼，死鸭嘴硬护",

    # --- 第 33 组: ("proud", "worry", "jealous") 得意 + 担心 + 吃醋 ---
    ("proud", "worry", "jealous", "proud", "M"): "傲娇吃味，还不忘管着",
    ("proud", "worry", "jealous", "proud", "H"): "霸道管着，吃醋又心疼",
    ("proud", "worry", "jealous", "worry", "M"): "嘴硬操心，暗喝小飞醋",
    ("proud", "worry", "jealous", "worry", "H"): "急得炸毛，傲娇护着呢",
    ("proud", "worry", "jealous", "jealous", "M"): "酸溜哼声，傲娇盘问着",
    ("proud", "worry", "jealous", "jealous", "H"): "醋意攻心，傲娇心疼盯",

    # --- 第 34 组: ("proud", "sulky", "jealous") 得意 + 委屈 + 吃醋 ---
    ("proud", "sulky", "jealous", "proud", "M"): "傲娇别扭，酸溜怨偏心",
    ("proud", "sulky", "jealous", "proud", "H"): "炸毛傲娇，委屈又吃醋",
    ("proud", "sulky", "jealous", "sulky", "M"): "憋闷泛酸，还强撑面子",
    ("proud", "sulky", "jealous", "sulky", "H"): "委屈发酸，嘴硬不服输",
    ("proud", "sulky", "jealous", "jealous", "M"): "酸言酸语，傲娇等顺毛",
    ("proud", "sulky", "jealous", "jealous", "H"): "醋坛炸了，委屈还嘴硬",

    # --- 第 35 组: ("worry", "sulky", "jealous") 担心 + 委屈 + 吃醋 ---
    ("worry", "sulky", "jealous", "worry", "M"): "患得患失，急又酸委屈",
    ("worry", "sulky", "jealous", "worry", "H"): "心疼发慌，夹委屈醋意",
    ("worry", "sulky", "jealous", "sulky", "M"): "闷受委屈，泛酸还惦记",
    ("worry", "sulky", "jealous", "sulky", "H"): "好委屈呀，却还怕累着",
    ("worry", "sulky", "jealous", "jealous", "M"): "酸溜生闷气，又忍操心",
    ("worry", "sulky", "jealous", "jealous", "H"): "醋屈爆发，仍揪心护着",
}


def quantize_single_tier(val: float) -> int:
    """
    统一五档阈值梯度 (0 ~ 100)：
    - 0 档 (静默/未激活): 0 ~ 29
    - 1 档 (轻度): 30 ~ 54
    - 2 档 (中度): 55 ~ 69
    - 3 档 (强烈): 70 ~ 89
    - 4 档 (爆表极值): 90 ~ 100
    """
    v = float(val or 0.0)
    if v < 30.0:
        return 0
    if v < 55.0:
        return 1
    if v < 70.0:
        return 2
    if v < 90.0:
        return 3
    return 4


def quantize_pair_band(val: float) -> str:
    """
    将激活维度 (>=30) 映射为 3 个清晰复合强度带：
    - L (轻: 30~54)
    - M (中: 55~69)
    - H (强爆: 70~100)
    """
    v = float(val or 0.0)
    if v < 55.0:
        return "L"
    if v < 70.0:
        return "M"
    return "H"


def quantize_macro_mood_bucket(mood: float) -> int:
    """
    宏观 mood (0 ~ 100) 按每 10 点分 10 档 (0..9)
    """
    m = max(0.0, min(100.0, float(mood if mood is not None else 60.0)))
    idx = int(m // 10.0)
    return min(9, max(0, idx))


def lookup_mood_lexicon(mood: float, dims: dict) -> str:
    """
    纯静态 O(1) 查表入口，100% 覆盖 0 / 1 / 2 / 3+ 维激活场景：
    - 0 维激活 (全部 < 30) -> 查 MACRO_MOOD_LEXICON (10 条)
    - 1 维激活 (仅 1 维 >= 30) -> 查 SINGLE_DIM_LEXICON (28 条)
    - 2 维激活 (2 维 >= 30) -> 查 PAIR_DIM_LEXICON (189 条)
    - 3+ 维激活 (3 维及以上 >= 30，取 Top-3) -> 查 TRIPLE_DIM_LEXICON (210 条)
    """
    active = []
    if isinstance(dims, dict):
        for k in DIM_ORDER:
            v = float(dims.get(k, 0.0) or 0.0)
            if v >= 30.0:
                active.append((k, v))

    # 按分值从高到低排序，分值相同时按 DIM_ORDER 稳定排序
    active.sort(key=lambda item: (-item[1], _DIM_RANK[item[0]]))
    n_act = len(active)

    if n_act == 0:
        bucket = quantize_macro_mood_bucket(mood)
        return MACRO_MOOD_LEXICON[bucket]

    if n_act == 1:
        dim_k, dim_v = active[0]
        tier = quantize_single_tier(dim_v)
        return SINGLE_DIM_LEXICON[(dim_k, tier)]

    if n_act == 2:
        (k_a, v_a), (k_b, v_b) = active[0], active[1]
        c1, c2 = canonical_pair_key(k_a, k_b)
        v1 = v_a if c1 == k_a else v_b
        v2 = v_b if c1 == k_a else v_a
        b1 = quantize_pair_band(v1)
        b2 = quantize_pair_band(v2)
        return PAIR_DIM_LEXICON[(c1, c2, b1, b2)]

    # n_act >= 3: 取数值最高的 Top-3
    top3 = active[:3]
    primary_dim, primary_val = top3[0]
    intensity = "H" if primary_val >= 70.0 else "M"
    c1, c2, c3 = canonical_triple_key(top3[0][0], top3[1][0], top3[2][0])
    return TRIPLE_DIM_LEXICON[(c1, c2, c3, primary_dim, intensity)]


