"""
social/mood_lexicon.py — 星见弥 M2 高区分度静态全复合情绪词库 (437 条纯静态 O(1) 查表)

结构总览 (10 + 28 + 189 + 210 = 437 条)：
1. 0 维激活 (全部 < 30，走宏观 mood 0~100 基线，按每 10 点分 10 档) -> 10 条
2. 1 维激活 (仅 1 维 >= 30，7 维 × 4 个激活档: 1档 30~54 / 2档 55~69 / 3档 70~89 / 4档 90~100) -> 28 条
3. 2 维激活 (2 维 >= 30，全 C(7,2)=21 对 × 3×3=9 格强度矩阵 (L/M/H × L/M/H)) -> 189 条
4. 3+ 维激活 (3 维及以上 >= 30，取 Top-3 查全 C(7,3)=35 组 × 6 档 (3种第一主角 × 轻中M/强爆H 2档)) -> 210 条

铁律：
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
    0: "蔫头耷脑闷闷不乐",
    1: "情绪低落不太想理人",
    2: "小情绪犯困懒得搭话",
    3: "心里微闷带点小挑剔",
    4: "平淡慵懒的猫猫放空",
    5: "心情平静自在好相处",
    6: "温和舒展挺好说话",
    7: "心情愉悦尾巴轻轻晃",
    8: "心里亮堂雀跃超好哄",
    9: "元气满格哼歌想贴贴",
}


# ============================================================
# 2. 1 维激活：7 维 × 4 档 (1档:30~54, 2档:55~69, 3档:70~89, 4档:90~100) (28 条)
# ============================================================
SINGLE_DIM_LEXICON: dict[tuple[str, int], str] = {
    # happy (开心)
    ("happy", 1): "嘴角微翘有点小雀跃",
    ("happy", 2): "眼睛亮晶晶地开心",
    ("happy", 3): "开心得猫耳直抖",
    ("happy", 4): "欢呼雀跃快乐到转圈",
    # calm (踏实自在)
    ("calm", 1): "气息平稳心里挺松弛",
    ("calm", 2): "窝着晒太阳踏实自在",
    ("calm", 3): "岁月静好整个人软乎",
    ("calm", 4): "无比安心只想静静贴",
    # sweet (甜蜜害羞)
    ("sweet", 1): "耳尖微热泛起一点甜",
    ("sweet", 2): "心里甜丝丝又有点羞",
    ("sweet", 3): "脸颊滚烫甜到心乱撞",
    ("sweet", 4): "害羞捂脸甜得快化了",
    # proud (得意傲娇)
    ("proud", 1): "轻轻哼气暗自小得意",
    ("proud", 2): "下巴微扬傲娇求夸奖",
    ("proud", 3): "尾巴翘上天得意坏了",
    ("proud", 4): "叉腰仰头本喵最厉害",
    # worry (担心心疼)
    ("worry", 1): "眉头微蹙稍微替你急",
    ("worry", 2): "心里犯嘀咕暗暗挂念",
    ("worry", 3): "揪着衣角满眼是心疼",
    ("worry", 4): "急得团团转心疼坏了",
    # sulky (委屈郁闷)
    ("sulky", 1): "撇撇嘴稍微有点别扭",
    ("sulky", 2): "腮帮微鼓憋着小闷气",
    ("sulky", 3): "耳朵耷拉满肚子委屈",
    ("sulky", 4): "眼眶微红委屈想罢工",
    # jealous (吃醋别扭)
    ("jealous", 1): "鼻子微皱泛起小醋意",
    ("jealous", 2): "酸溜溜阴阳怪气轻哼",
    ("jealous", 3): "醋坛打翻赌气不看你",
    ("jealous", 4): "炸毛护食酸得直磨牙",
}


# ============================================================
# 3. 2 维激活：C(7,2) = 21 对 × 9 格 (L/M/H × L/M/H) (189 条)
#    键格式: (k1, k2, band1, band2)，其中 k1, k2 按 DIM_ORDER 排序
# ============================================================
PAIR_DIM_LEXICON: dict[tuple[str, str, str, str], str] = {
    # --- 第 1 对: ("happy", "calm") 开心 + 踏实 ---
    ("happy", "calm", "L", "L"): "惬意微甜的松弛心情",
    ("happy", "calm", "L", "M"): "窝在软垫安稳又顺心",
    ("happy", "calm", "L", "H"): "满满安全感里泛微甜",
    ("happy", "calm", "M", "L"): "愉快晃尾巴自在哼歌",
    ("happy", "calm", "M", "M"): "舒心惬意像晒透阳光",
    ("happy", "calm", "M", "H"): "踏实依偎心里亮堂堂",
    ("happy", "calm", "H", "L"): "眉眼弯弯欢快又松弛",
    ("happy", "calm", "H", "M"): "满心欢喜又格外安稳",
    ("happy", "calm", "H", "H"): "幸福满溢安心想打呼",

    # --- 第 2 对: ("happy", "sweet") 开心 + 甜蜜 ---
    ("happy", "sweet", "L", "L"): "眉眼带笑藏着小羞涩",
    ("happy", "sweet", "L", "M"): "脸颊微粉甜丝丝地笑",
    ("happy", "sweet", "L", "H"): "羞红了脸心里冒甜泡",
    ("happy", "sweet", "M", "L"): "笑吟吟眼神亮晶晶甜",
    ("happy", "sweet", "M", "M"): "又甜又开心直晃尾巴",
    ("happy", "sweet", "M", "H"): "甜到心坎里害羞偷笑",
    ("happy", "sweet", "H", "L"): "欢快扑来带着小娇羞",
    ("happy", "sweet", "H", "M"): "喜笑颜开满心甜丝丝",
    ("happy", "sweet", "H", "H"): "甜晕过去快乐到冒泡",

    # --- 第 3 对: ("happy", "proud") 开心 + 得意 ---
    ("happy", "proud", "L", "L"): "嘴角微扬暗暗小臭屁",
    ("happy", "proud", "L", "M"): "轻哼一声掩不住笑意",
    ("happy", "proud", "L", "H"): "尾巴翘上天憋不住笑",
    ("happy", "proud", "M", "L"): "眉飞色舞带点小傲娇",
    ("happy", "proud", "M", "M"): "又乐又得意晃脑求夸",
    ("happy", "proud", "M", "H"): "叉腰坏笑等你夸本喵",
    ("happy", "proud", "H", "L"): "笑得眯起眼满脸神气",
    ("happy", "proud", "H", "M"): "欢天喜地晃尾巴炫耀",
    ("happy", "proud", "H", "H"): "狂喜乱舞膨胀神气猫",

    # --- 第 4 对: ("happy", "worry") 开心 + 担心 ---
    ("happy", "worry", "L", "L"): "松了口气带点小惦记",
    ("happy", "worry", "L", "M"): "嘴上陪笑心里在操心",
    ("happy", "worry", "L", "H"): "强打笑脸其实挺心疼",
    ("happy", "worry", "M", "L"): "聊得挺乐还不忘叮嘱",
    ("happy", "worry", "M", "M"): "替你开心又忍不住急",
    ("happy", "worry", "M", "H"): "破涕为笑眼神还揪心",
    ("happy", "worry", "H", "L"): "眉开眼笑顺便唠叨你",
    ("happy", "worry", "H", "M"): "开心蹦跳还不忘牵挂",
    ("happy", "worry", "H", "H"): "又喜又揪心像过山车",

    # --- 第 5 对: ("happy", "sulky") 开心 + 委屈 ---
    ("happy", "sulky", "L", "L"): "被逗乐还剩半口闷气",
    ("happy", "sulky", "L", "M"): "嘴角想翘又故意绷脸",
    ("happy", "sulky", "L", "H"): "眼眶还红勉强被逗笑",
    ("happy", "sulky", "M", "L"): "噗嗤笑出声假装还气",
    ("happy", "sulky", "M", "M"): "破涕为笑还带点委屈",
    ("happy", "sulky", "M", "H"): "委屈巴巴却憋不住笑",
    ("happy", "sulky", "H", "L"): "早哄好了还傲娇轻哼",
    ("happy", "sulky", "H", "M"): "笑得直抖还嘴硬记账",
    ("happy", "sulky", "H", "H"): "边抹眼角边笑成傻猫",

    # --- 第 6 对: ("happy", "jealous") 开心 + 吃醋 ---
    ("happy", "jealous", "L", "L"): "暗暗高兴又泛小酸水",
    ("happy", "jealous", "L", "M"): "嘴角微翘话里带点酸",
    ("happy", "jealous", "L", "H"): "被顺毛了但醋意没消",
    ("happy", "jealous", "M", "L"): "开心接话顺带小刺你",
    ("happy", "jealous", "M", "M"): "心里乐开花嘴上吃味",
    ("happy", "jealous", "M", "H"): "酸溜溜被哄笑不松口",
    ("happy", "jealous", "H", "L"): "眉开眼笑占着你不放",
    ("happy", "jealous", "H", "M"): "哄得超开心宣布独占",
    ("happy", "jealous", "H", "H"): "又酸又狂喜抱紧不撒",

    # --- 第 7 对: ("calm", "sweet") 踏实 + 甜蜜 ---
    ("calm", "sweet", "L", "L"): "安静贴着泛浅浅甜意",
    ("calm", "sweet", "L", "M"): "软绵绵靠着耳尖微烫",
    ("calm", "sweet", "L", "H"): "乖巧缩在旁边羞发烫",
    ("calm", "sweet", "M", "L"): "安心窝着嘴角带微甜",
    ("calm", "sweet", "M", "M"): "岁月静好心里甜软软",
    ("calm", "sweet", "M", "H"): "安稳依偎被甜意泡软",
    ("calm", "sweet", "H", "L"): "踏实粘着懒洋洋撒娇",
    ("calm", "sweet", "H", "M"): "安心打呼噜满心温存",
    ("calm", "sweet", "H", "H"): "卸下防备甜腻腻依恋",

    # --- 第 8 对: ("calm", "proud") 踏实 + 得意 ---
    ("calm", "proud", "L", "L"): "悠哉揣手暗自小神气",
    ("calm", "proud", "L", "M"): "气定神闲翘起小尾巴",
    ("calm", "proud", "L", "H"): "端坐高处傲娇看众生",
    ("calm", "proud", "M", "L"): "慢悠晃尾略带小自豪",
    ("calm", "proud", "M", "M"): "笃定从容像巡视领地",
    ("calm", "proud", "M", "H"): "稳坐C位得意哼小曲",
    ("calm", "proud", "H", "L"): "舒坦窝着享受被夸奖",
    ("calm", "proud", "H", "M"): "安稳惬意掩不住骄傲",
    ("calm", "proud", "H", "H"): "大佬坐姿满脸赢麻了",

    # --- 第 9 对: ("calm", "worry") 踏实 + 担心 ---
    ("calm", "worry", "L", "L"): "语气平缓但默默留心",
    ("calm", "worry", "L", "M"): "耐着性子轻声替你急",
    ("calm", "worry", "L", "H"): "表面镇定其实满眼疼",
    ("calm", "worry", "M", "L"): "安静陪着顺口叮嘱你",
    ("calm", "worry", "M", "M"): "温柔守在旁边替你愁",
    ("calm", "worry", "M", "H"): "沉下心陪你满眼担忧",
    ("calm", "worry", "H", "L"): "踏实守候给你当靠山",
    ("calm", "worry", "H", "M"): "用安稳陪伴抚平揪心",
    ("calm", "worry", "H", "H"): "心疼极了默默抱紧你",

    # --- 第 10 对: ("calm", "sulky") 踏实 + 委屈 ---
    ("calm", "sulky", "L", "L"): "情绪刚平复剩点小闷",
    ("calm", "sulky", "L", "M"): "安静缩在角落生闷气",
    ("calm", "sulky", "L", "H"): "不吵不闹闷头受委屈",
    ("calm", "sulky", "M", "L"): "气消大半安安静静听",
    ("calm", "sulky", "M", "M"): "被安抚好了带小鼻音",
    ("calm", "sulky", "M", "H"): "乖乖听哄但眼角垂着",
    ("calm", "sulky", "H", "L"): "彻底被哄乖安静贴着",
    ("calm", "sulky", "H", "M"): "心里踏实了嘴上哼唧",
    ("calm", "sulky", "H", "H"): "委屈散进温柔里依偎",

    # --- 第 11 对: ("calm", "jealous") 踏实 + 吃醋 ---
    ("calm", "jealous", "L", "L"): "表面淡定暗飞小眼刀",
    ("calm", "jealous", "L", "M"): "装作不在意其实泛酸",
    ("calm", "jealous", "L", "H"): "冷着小脸闷声喝飞醋",
    ("calm", "jealous", "M", "L"): "安稳坐着顺口酸一句",
    ("calm", "jealous", "M", "M"): "不慌不忙宣示猫主权",
    ("calm", "jealous", "M", "H"): "安静盯紧半步不准跑",
    ("calm", "jealous", "H", "L"): "笃定属我微带小醋意",
    ("calm", "jealous", "H", "M"): "踏实赖着你霸道安心",
    ("calm", "jealous", "H", "H"): "安稳圈住绝不让别人",

    # --- 第 12 对: ("sweet", "proud") 甜蜜 + 得意 ---
    ("sweet", "proud", "L", "L"): "被夸得耳尖微热傲娇",
    ("sweet", "proud", "L", "M"): "嘴上哼哼心里挺受用",
    ("sweet", "proud", "L", "H"): "傲娇暗爽",
    ("sweet", "proud", "M", "L"): "脸颊微粉又有点神气",
    ("sweet", "proud", "M", "M"): "害羞捂脸还嘴硬自夸",
    ("sweet", "proud", "M", "H"): "得意叉腰偏要你多宠",
    ("sweet", "proud", "H", "L"): "甜丝丝有点小骄傲",
    ("sweet", "proud", "H", "M"): "被宠得心花怒放翘尾",
    ("sweet", "proud", "H", "H"): "甜到发昏又骄傲冒泡",

    # --- 第 13 对: ("sweet", "worry") 甜蜜 + 担心 ---
    ("sweet", "worry", "L", "L"): "软声关心耳尖有点红",
    ("sweet", "worry", "L", "M"): "温柔惦记眼神软乎乎",
    ("sweet", "worry", "L", "H"): "心疼坏了羞怯凑过来",
    ("sweet", "worry", "M", "L"): "害羞贴贴还不忘叮嘱",
    ("sweet", "worry", "M", "M"): "又甜又心疼软声哄你",
    ("sweet", "worry", "M", "H"): "满眼心疼怯生生牵你",
    ("sweet", "worry", "H", "L"): "甜腻撒娇带点小牵挂",
    ("sweet", "worry", "H", "M"): "脸红扑怀里心疼你累",
    ("sweet", "worry", "H", "H"): "爱怜满溢心疼紧紧抱",

    # --- 第 14 对: ("sweet", "sulky") 甜蜜 + 委屈 ---
    ("sweet", "sulky", "L", "L"): "带点小鼻音软乎撒娇",
    ("sweet", "sulky", "L", "M"): "委屈撇嘴其实想求抱",
    ("sweet", "sulky", "L", "H"): "眼泪汪汪拉衣角求哄",
    ("sweet", "sulky", "M", "L"): "被哄脸红还假装赌气",
    ("sweet", "sulky", "M", "M"): "又羞又委屈软绵锤你",
    ("sweet", "sulky", "M", "H"): "扁着嘴往怀里钻求摸",
    ("sweet", "sulky", "H", "L"): "甜得心软还娇嗔一句",
    ("sweet", "sulky", "H", "M"): "脸红发烫委屈变撒娇",
    ("sweet", "sulky", "H", "H"): "羞怯泣音腻怀里不起",

    # --- 第 15 对: ("sweet", "jealous") 甜蜜 + 吃醋 ---
    ("sweet", "jealous", "L", "L"): "偷偷脸红带点小护食",
    ("sweet", "jealous", "L", "M"): "酸溜溜凑近想讨偏爱",
    ("sweet", "jealous", "L", "H"): "醋意大发偏要你只哄",
    ("sweet", "jealous", "M", "L"): "甜丝丝贴着不许看人",
    ("sweet", "jealous", "M", "M"): "嘴硬吃醋但心里挺甜",
    ("sweet", "jealous", "M", "H"): "酸得哼哼还要钻怀里",
    ("sweet", "jealous", "H", "L"): "害羞黏着宣示专属权",
    ("sweet", "jealous", "H", "M"): "又甜又护食",
    ("sweet", "jealous", "H", "H"): "脸红到耳根死死独占",

    # --- 第 16 对: ("proud", "worry") 得意 + 担心 ---
    ("proud", "worry", "L", "L"): "嘴硬不管其实暗留意",
    ("proud", "worry", "L", "M"): "傲娇叹气还是替你愁",
    ("proud", "worry", "L", "H"): "急得跳脚嘴上嫌你笨",
    ("proud", "worry", "M", "L"): "神气包揽顺手照顾你",
    ("proud", "worry", "M", "M"): "刀子嘴豆腐心傲娇护",
    ("proud", "worry", "M", "H"): "嘴上数落眼里全心疼",
    ("proud", "worry", "H", "L"): "拍胸罩着叫你别逞强",
    ("proud", "worry", "H", "M"): "本喵出马搞定别担心",
    ("proud", "worry", "H", "H"): "霸道又心疼强行接管",

    # --- 第 17 对: ("proud", "sulky") 得意 + 委屈 ---
    ("proud", "sulky", "L", "L"): "哼声扭头带傲娇情绪",
    ("proud", "sulky", "L", "M"): "受委屈还强撑着嘴硬",
    ("proud", "sulky", "L", "H"): "眼眶发酸昂头不服输",
    ("proud", "sulky", "M", "L"): "傲娇叉腰怪你少夸我",
    ("proud", "sulky", "M", "M"): "又气又傲娇背身等哄",
    ("proud", "sulky", "M", "H"): "满腹委屈死鸭子嘴硬",
    ("proud", "sulky", "H", "L"): "尾巴翘老高记你一笔",
    ("proud", "sulky", "H", "M"): "本喵这么好竟敢冷落",
    ("proud", "sulky", "H", "H"): "炸毛傲娇控诉不珍惜",

    # --- 第 18 对: ("proud", "jealous") 得意 + 吃醋 ---
    ("proud", "jealous", "L", "L"): "轻哼斜眼瞄你和谁聊",
    ("proud", "jealous", "L", "M"): "酸溜挑眉说本喵更好",
    ("proud", "jealous", "L", "H"): "醋火中烧哼声不稀罕",
    ("proud", "jealous", "M", "L"): "扬起下巴示意快看我",
    ("proud", "jealous", "M", "M"): "傲娇哼气变着法争宠",
    ("proud", "jealous", "M", "H"): "吃醋炸毛只准夸本喵",
    ("proud", "jealous", "H", "L"): "自信爆棚顺手掐桃花",
    ("proud", "jealous", "H", "M"): "叉腰宣告本喵是唯一",
    ("proud", "jealous", "H", "H"): "霸道猫皇严禁看别人",

    # --- 第 19 对: ("worry", "sulky") 担心 + 委屈 ---
    ("worry", "sulky", "L", "L"): "心里微闷又替你嘀咕",
    ("worry", "sulky", "L", "M"): "委屈叹气还不忘惦记",
    ("worry", "sulky", "L", "H"): "满心委屈还怕你出错",
    ("worry", "sulky", "M", "L"): "忧心忡忡带点小失落",
    ("worry", "sulky", "M", "M"): "既心疼你又觉被冷落",
    ("worry", "sulky", "M", "H"): "鼻尖发酸委屈替你急",
    ("worry", "sulky", "H", "L"): "急得蹙眉怪你不爱惜",
    ("worry", "sulky", "H", "M"): "心疼掉泪怪你不听话",
    ("worry", "sulky", "H", "H"): "又急又委屈哭腔数落",

    # --- 第 20 对: ("worry", "jealous") 担心 + 吃醋 ---
    ("worry", "jealous", "L", "L"): "暗酸一下又怕你有事",
    ("worry", "jealous", "L", "M"): "嘴上泛酸眼神偷瞄你",
    ("worry", "jealous", "L", "H"): "大吃飞醋之余牵挂你",
    ("worry", "jealous", "M", "L"): "患得患失有点小醋意",
    ("worry", "jealous", "M", "M"): "怕你受累又怕被抢走",
    ("worry", "jealous", "M", "H"): "酸得揪心紧盯你动向",
    ("worry", "jealous", "H", "L"): "满心牵挂顺带吃飞醋",
    ("worry", "jealous", "H", "M"): "心疼与占有欲齐翻涌",
    ("worry", "jealous", "H", "H"): "慌乱护食怕出事被抢",

    # --- 第 21 对: ("sulky", "jealous") 委屈 + 吃醋 ---
    ("sulky", "jealous", "L", "L"): "撅着小嘴泛起一点酸",
    ("sulky", "jealous", "L", "M"): "酸溜赌气觉得被偏心",
    ("sulky", "jealous", "L", "H"): "醋坛打翻赌气扭过头",
    ("sulky", "jealous", "M", "L"): "闷闷不乐觉你没疼我",
    ("sulky", "jealous", "M", "M"): "又酸又委屈鼓腮不理",
    ("sulky", "jealous", "M", "H"): "满嘴酸话委屈贴猫耳",
    ("sulky", "jealous", "H", "L"): "受冷落委屈得泛酸水",
    ("sulky", "jealous", "H", "M"): "眼眶红红控诉你偏心",
    ("sulky", "jealous", "H", "H"): "吃醋委屈爆发哄不好",
}


# ============================================================
# 4. 3+ 维激活：C(7,3) = 35 组 × 6 档 (3种第一主角 × 轻中M/强爆H 2档) (210 条)
#    键格式: (k1, k2, k3, primary_dim, intensity)
#    其中 (k1, k2, k3) 按 DIM_ORDER 规范排序，primary_dim ∈ {k1, k2, k3}, intensity ∈ {"M", "H"}
# ============================================================
TRIPLE_DIM_LEXICON: dict[tuple[str, str, str, str, str], str] = {
    # --- 第 1 组: ("happy", "calm", "sweet") 开心 + 踏实 + 甜蜜 ---
    ("happy", "calm", "sweet", "happy", "M"): "笑眼弯弯安心又微甜",
    ("happy", "calm", "sweet", "happy", "H"): "欢快扑怀里甜得踏实",
    ("happy", "calm", "sweet", "calm", "M"): "安稳窝着泛甜意和笑",
    ("happy", "calm", "sweet", "calm", "H"): "无比安心依偎笑甜软",
    ("happy", "calm", "sweet", "sweet", "M"): "甜丝丝贴着安心偷笑",
    ("happy", "calm", "sweet", "sweet", "H"): "羞甜融在安稳快乐里",

    # --- 第 2 组: ("happy", "calm", "proud") 开心 + 踏实 + 得意 ---
    ("happy", "calm", "proud", "happy", "M"): "惬意笑着带点小神气",
    ("happy", "calm", "proud", "happy", "H"): "眉开眼笑稳坐猫王座",
    ("happy", "calm", "proud", "calm", "M"): "气定神闲愉快微翘尾",
    ("happy", "calm", "proud", "calm", "H"): "舒坦窝着从容享夸奖",
    ("happy", "calm", "proud", "proud", "M"): "悠哉小傲娇心里乐呵",
    ("happy", "calm", "proud", "proud", "H"): "神气叉腰安稳又快乐",

    # --- 第 3 组: ("happy", "calm", "worry") 开心 + 踏实 + 担心 ---
    ("happy", "calm", "worry", "happy", "M"): "开心陪聊顺便温叮嘱",
    ("happy", "calm", "worry", "happy", "H"): "笑得亮堂仍温柔念你",
    ("happy", "calm", "worry", "calm", "M"): "安稳守你带笑小操心",
    ("happy", "calm", "worry", "calm", "H"): "踏实靠山笑着替你忧",
    ("happy", "calm", "worry", "worry", "M"): "微操心但陪着也开心",
    ("happy", "calm", "worry", "worry", "H"): "满眼关切欣慰有我在",

    # --- 第 4 组: ("happy", "calm", "sulky") 开心 + 踏实 + 委屈 ---
    ("happy", "calm", "sulky", "happy", "M"): "被哄乐了安稳消闷气",
    ("happy", "calm", "sulky", "happy", "H"): "破涕大笑踏实赖着你",
    ("happy", "calm", "sulky", "calm", "M"): "心情平复带笑小鼻音",
    ("happy", "calm", "sulky", "calm", "H"): "安稳顺毛笑纳你道歉",
    ("happy", "calm", "sulky", "sulky", "M"): "嘴上微委屈心已安稳",
    ("happy", "calm", "sulky", "sulky", "H"): "眼角微湿安心被逗笑",

    # --- 第 5 组: ("happy", "calm", "jealous") 开心 + 踏实 + 吃醋 ---
    ("happy", "calm", "jealous", "happy", "M"): "愉快贴着顺口酸一句",
    ("happy", "calm", "jealous", "happy", "H"): "开心霸占笃定宣主权",
    ("happy", "calm", "jealous", "calm", "M"): "淡定微笑暗飞醋眼刀",
    ("happy", "calm", "jealous", "calm", "H"): "稳稳圈住笑着禁看他",
    ("happy", "calm", "jealous", "jealous", "M"): "微酸护食心踏实高兴",
    ("happy", "calm", "jealous", "jealous", "H"): "醋劲翻涌安心被哄笑",

    # --- 第 6 组: ("happy", "sweet", "proud") 开心 + 甜蜜 + 得意 ---
    ("happy", "sweet", "proud", "happy", "M"): "开心翘尾羞羞求夸奖",
    ("happy", "sweet", "proud", "happy", "H"): "喜笑颜开又甜又臭屁",
    ("happy", "sweet", "proud", "sweet", "M"): "脸红偷笑心里小骄傲",
    ("happy", "sweet", "proud", "sweet", "H"): "甜冒粉泡骄傲晃猫耳",
    ("happy", "sweet", "proud", "proud", "M"): "傲娇扬下巴藏不住甜",
    ("happy", "sweet", "proud", "proud", "H"): "被宠上天得意羞转圈",

    # --- 第 7 组: ("happy", "sweet", "worry") 开心 + 甜蜜 + 担心 ---
    ("happy", "sweet", "worry", "happy", "M"): "笑着撒娇顺便关心你",
    ("happy", "sweet", "worry", "happy", "H"): "欢喜甜腻叮嘱别累着",
    ("happy", "sweet", "worry", "sweet", "M"): "耳尖微红带笑替你愁",
    ("happy", "sweet", "worry", "sweet", "H"): "害羞扑怀心疼又开心",
    ("happy", "sweet", "worry", "worry", "M"): "软声心疼被你逗甜笑",
    ("happy", "sweet", "worry", "worry", "H"): "揪心挂念又甜又喜迎",

    # --- 第 8 组: ("happy", "sweet", "sulky") 开心 + 甜蜜 + 委屈 ---
    ("happy", "sweet", "sulky", "happy", "M"): "被哄羞笑还假装撇嘴",
    ("happy", "sweet", "sulky", "happy", "H"): "开心钻怀娇嗔记小账",
    ("happy", "sweet", "sulky", "sweet", "M"): "脸微烫破涕为笑撒娇",
    ("happy", "sweet", "sulky", "sweet", "H"): "甜到心软带泪花偷笑",
    ("happy", "sweet", "sulky", "sulky", "M"): "小委屈未消就被甜笑",
    ("happy", "sweet", "sulky", "sulky", "H"): "眼眶红红羞甜笑原谅",

    # --- 第 9 组: ("happy", "sweet", "jealous") 开心 + 甜蜜 + 吃醋 ---
    ("happy", "sweet", "jealous", "happy", "M"): "开心粘你甜丝丝吃醋",
    ("happy", "sweet", "jealous", "happy", "H"): "欢快抱紧羞甜宣独占",
    ("happy", "sweet", "jealous", "sweet", "M"): "害羞发甜偷偷酸一下",
    ("happy", "sweet", "jealous", "sweet", "H"): "甜到滚烫霸道护食笑",
    ("happy", "sweet", "jealous", "jealous", "M"): "酸溜撒娇被哄得甜笑",
    ("happy", "sweet", "jealous", "jealous", "H"): "大吃飞醋被宠得甜乐",

    # --- 第 10 组: ("happy", "proud", "worry") 开心 + 得意 + 担心 ---
    ("happy", "proud", "worry", "happy", "M"): "笑着拍胸替你操心办",
    ("happy", "proud", "worry", "happy", "H"): "欢欣神气罩着心疼你",
    ("happy", "proud", "worry", "proud", "M"): "傲娇显摆顺便照顾你",
    ("happy", "proud", "worry", "proud", "H"): "得意出马护你暗高兴",
    ("happy", "proud", "worry", "worry", "M"): "刀子嘴操心被夸偷乐",
    ("happy", "proud", "worry", "worry", "H"): "心疼坏了傲娇笑着哄",

    # --- 第 11 组: ("happy", "proud", "sulky") 开心 + 得意 + 委屈 ---
    ("happy", "proud", "sulky", "happy", "M"): "被夸乐了还傲娇哼哼",
    ("happy", "proud", "sulky", "happy", "H"): "笑晃尾巴嘴硬算原谅",
    ("happy", "proud", "sulky", "proud", "M"): "昂头拿乔早被哄高兴",
    ("happy", "proud", "sulky", "proud", "H"): "神气叉腰边记仇边乐",
    ("happy", "proud", "sulky", "sulky", "M"): "带小脾气被夸得暗爽",
    ("happy", "proud", "sulky", "sulky", "H"): "委屈炸毛被捧得偷笑",

    # --- 第 12 组: ("happy", "proud", "jealous") 开心 + 得意 + 吃醋 ---
    ("happy", "proud", "jealous", "happy", "M"): "笑嘻嘻吃醋趁机炫耀",
    ("happy", "proud", "jealous", "happy", "H"): "狂喜赢偏爱神气独占",
    ("happy", "proud", "jealous", "proud", "M"): "得意哼声酸本喵最好",
    ("happy", "proud", "jealous", "proud", "H"): "翘尾霸道掐桃花暗爽",
    ("happy", "proud", "jealous", "jealous", "M"): "酸溜争宠赢了偷乐呵",
    ("happy", "proud", "jealous", "jealous", "H"): "醋火中烧因偏爱神气",

    # --- 第 13 组: ("happy", "worry", "sulky") 开心 + 担心 + 委屈 ---
    ("happy", "worry", "sulky", "happy", "M"): "破涕为笑委屈叮嘱你",
    ("happy", "worry", "sulky", "happy", "H"): "又哭又笑悬心终放平",
    ("happy", "worry", "sulky", "worry", "M"): "替你揪心但被哄笑了",
    ("happy", "worry", "sulky", "worry", "H"): "疼惜委屈见平安才笑",
    ("happy", "worry", "sulky", "sulky", "M"): "受小委屈操心被逗乐",
    ("happy", "worry", "sulky", "sulky", "H"): "挂着泪花心疼你笑场",

    # --- 第 14 组: ("happy", "worry", "jealous") 开心 + 担心 + 吃醋 ---
    ("happy", "worry", "jealous", "happy", "M"): "开心接话酸溜叮嘱你",
    ("happy", "worry", "jealous", "happy", "H"): "欢喜守你既护食又急",
    ("happy", "worry", "jealous", "worry", "M"): "替你操心泛酸被逗笑",
    ("happy", "worry", "jealous", "worry", "H"): "牵挂带醋终被你哄乐",
    ("happy", "worry", "jealous", "jealous", "M"): "吃味嘀咕见你哄偷乐",
    ("happy", "worry", "jealous", "jealous", "H"): "醋意牵挂被顺毛逗笑",

    # --- 第 15 组: ("happy", "sulky", "jealous") 开心 + 委屈 + 吃醋 ---
    ("happy", "sulky", "jealous", "happy", "M"): "被哄乐了嘴上酸记账",
    ("happy", "sulky", "jealous", "happy", "H"): "笑出声还要赌气轻咬",
    ("happy", "sulky", "jealous", "sulky", "M"): "委屈泛酸憋不住嘴翘",
    ("happy", "sulky", "jealous", "sulky", "H"): "红眼吃醋被哄笑开花",
    ("happy", "sulky", "jealous", "jealous", "M"): "酸溜闹别扭其实乐了",
    ("happy", "sulky", "jealous", "jealous", "H"): "翻醋坛委屈秒被逗笑",

    # --- 第 16 组: ("calm", "sweet", "proud") 踏实 + 甜蜜 + 得意 ---
    ("calm", "sweet", "proud", "calm", "M"): "安稳窝着羞甜小傲娇",
    ("calm", "sweet", "proud", "calm", "H"): "舒坦依偎享独宠神气",
    ("calm", "sweet", "proud", "sweet", "M"): "甜软靠你耳红小得意",
    ("calm", "sweet", "proud", "sweet", "H"): "甜腻贴贴笃定宝藏猫",
    ("calm", "sweet", "proud", "proud", "M"): "气定神闲翘尾羞吃糖",
    ("calm", "sweet", "proud", "proud", "H"): "傲娇猫皇稳坐甜打呼",

    # --- 第 17 组: ("calm", "sweet", "worry") 踏实 + 甜蜜 + 担心 ---
    ("calm", "sweet", "worry", "calm", "M"): "安静守你微甜带关切",
    ("calm", "sweet", "worry", "calm", "H"): "踏实抱你温柔抚操心",
    ("calm", "sweet", "worry", "sweet", "M"): "软声贴你安心又挂念",
    ("calm", "sweet", "worry", "sweet", "H"): "羞甜依偎满怀心疼你",
    ("calm", "sweet", "worry", "worry", "M"): "轻声操心安稳陪着你",
    ("calm", "sweet", "worry", "worry", "H"): "心疼极了安静甜暖抱",

    # --- 第 18 组: ("calm", "sweet", "sulky") 踏实 + 甜蜜 + 委屈 ---
    ("calm", "sweet", "sulky", "calm", "M"): "安静窝怀剩甜软鼻音",
    ("calm", "sweet", "sulky", "calm", "H"): "彻底安稳羞甜蹭消气",
    ("calm", "sweet", "sulky", "sweet", "M"): "脸红顺毛小委屈快化",
    ("calm", "sweet", "sulky", "sweet", "H"): "甜软依偎把委屈揉碎",
    ("calm", "sweet", "sulky", "sulky", "M"): "带小鼻音乖巧靠着甜",
    ("calm", "sweet", "sulky", "sulky", "H"): "眼湿却安心腻甜怀抱",

    # --- 第 19 组: ("calm", "sweet", "jealous") 踏实 + 甜蜜 + 吃醋 ---
    ("calm", "sweet", "jealous", "calm", "M"): "安稳贴你微甜泛小酸",
    ("calm", "sweet", "jealous", "calm", "H"): "笃定赖怀里甜软独占",
    ("calm", "sweet", "jealous", "sweet", "M"): "羞甜蹭你小声禁看人",
    ("calm", "sweet", "jealous", "sweet", "H"): "甜到心窝安静霸占你",
    ("calm", "sweet", "jealous", "jealous", "M"): "闷吃小醋乖贴求偏爱",
    ("calm", "sweet", "jealous", "jealous", "H"): "酸涌心头却安心被宠",

    # --- 第 20 组: ("calm", "proud", "worry") 踏实 + 得意 + 担心 ---
    ("calm", "proud", "worry", "calm", "M"): "沉稳陪你带傲娇叮嘱",
    ("calm", "proud", "worry", "calm", "H"): "安稳兜底神气叫放心",
    ("calm", "proud", "worry", "proud", "M"): "气定神闲包揽顺手护",
    ("calm", "proud", "worry", "proud", "H"): "本喵镇场心疼别操劳",
    ("calm", "proud", "worry", "worry", "M"): "默默操心面上笃定傲",
    ("calm", "proud", "worry", "worry", "H"): "满心牵挂沉着给你撑",

    # --- 第 21 组: ("calm", "proud", "sulky") 踏实 + 得意 + 委屈 ---
    ("calm", "proud", "sulky", "calm", "M"): "平复端坐还微翘嘴角",
    ("calm", "proud", "sulky", "calm", "H"): "安稳顺毛傲娇算过关",
    ("calm", "proud", "sulky", "proud", "M"): "端着架子闷声等台阶",
    ("calm", "proud", "sulky", "proud", "H"): "高冷受委屈心底踏实",
    ("calm", "proud", "sulky", "sulky", "M"): "安静生闷气还要面子",
    ("calm", "proud", "sulky", "sulky", "H"): "闷声委屈笃定你会哄",

    # --- 第 22 组: ("calm", "proud", "jealous") 踏实 + 得意 + 吃醋 ---
    ("calm", "proud", "jealous", "calm", "M"): "不动声色吃味仍笃定",
    ("calm", "proud", "jealous", "calm", "H"): "稳坐正宫傲娇睨一眼",
    ("calm", "proud", "jealous", "proud", "M"): "从容扬下巴酸我更好",
    ("calm", "proud", "jealous", "proud", "H"): "猫皇气场淡定掐桃花",
    ("calm", "proud", "jealous", "jealous", "M"): "暗泛酸却气定等表态",
    ("calm", "proud", "jealous", "jealous", "H"): "闷醋翻腾高傲定属我",

    # --- 第 23 组: ("calm", "worry", "sulky") 踏实 + 担心 + 委屈 ---
    ("calm", "worry", "sulky", "calm", "M"): "安静陪你藏委屈操心",
    ("calm", "worry", "sulky", "calm", "H"): "沉静守候揉委屈心疼",
    ("calm", "worry", "sulky", "worry", "M"): "闷声挂念顾不上别扭",
    ("calm", "worry", "sulky", "worry", "H"): "心疼压委屈安静守候",
    ("calm", "worry", "sulky", "sulky", "M"): "乖憋小委屈默默念你",
    ("calm", "worry", "sulky", "sulky", "H"): "鼻酸不吵只怕你受累",

    # --- 第 24 组: ("calm", "worry", "jealous") 踏实 + 担心 + 吃醋 ---
    ("calm", "worry", "jealous", "calm", "M"): "平静陪你心微酸惦记",
    ("calm", "worry", "jealous", "calm", "H"): "沉稳守你醋意化关切",
    ("calm", "worry", "jealous", "worry", "M"): "默默操心吃个安静醋",
    ("calm", "worry", "jealous", "worry", "H"): "满眼担忧紧盯怕被抢",
    ("calm", "worry", "jealous", "jealous", "M"): "闷泛酸水放不下管你",
    ("calm", "worry", "jealous", "jealous", "H"): "暗醋汹涌冷静护周全",

    # --- 第 25 组: ("calm", "sulky", "jealous") 踏实 + 委屈 + 吃醋 ---
    ("calm", "sulky", "jealous", "calm", "M"): "安静不吭心微酸别扭",
    ("calm", "sulky", "jealous", "calm", "H"): "平复呼吸闷消化酸屈",
    ("calm", "sulky", "jealous", "sulky", "M"): "乖缩一旁酸溜生闷气",
    ("calm", "sulky", "jealous", "sulky", "H"): "沉默垂耳满腹酸委屈",
    ("calm", "sulky", "jealous", "jealous", "M"): "闷喝飞醋委屈不想说",
    ("calm", "sulky", "jealous", "jealous", "H"): "安静吃醋幽怨盯着你",

    # --- 第 26 组: ("sweet", "proud", "worry") 甜蜜 + 得意 + 担心 ---
    ("sweet", "proud", "worry", "sweet", "M"): "害羞贴来傲娇替你愁",
    ("sweet", "proud", "worry", "sweet", "H"): "甜扑怀里嘴硬心疼你",
    ("sweet", "proud", "worry", "proud", "M"): "傲娇拍胸耳红照顾你",
    ("sweet", "proud", "worry", "proud", "H"): "宣称本喵罩你满眼宠",
    ("sweet", "proud", "worry", "worry", "M"): "刀子嘴操心脸红心疼",
    ("sweet", "proud", "worry", "worry", "H"): "急得脸红傲娇强照顾",

    # --- 第 27 组: ("sweet", "proud", "sulky") 甜蜜 + 得意 + 委屈 ---
    ("sweet", "proud", "sulky", "sweet", "M"): "脸红撒娇傲娇记小账",
    ("sweet", "proud", "sulky", "sweet", "H"): "甜钻怀里嘴硬怨冷落",
    ("sweet", "proud", "sulky", "proud", "M"): "傲娇昂头羞意小委屈",
    ("sweet", "proud", "sulky", "proud", "H"): "叉腰求顺毛其实甜透",
    ("sweet", "proud", "sulky", "sulky", "M"): "委屈哼唧被夸脸红硬",
    ("sweet", "proud", "sulky", "sulky", "H"): "眼红还傲娇羞着要抱",

    # --- 第 28 组: ("sweet", "proud", "jealous") 甜蜜 + 得意 + 吃醋 ---
    ("sweet", "proud", "jealous", "sweet", "M"): "害羞贴紧傲娇吃飞醋",
    ("sweet", "proud", "jealous", "sweet", "H"): "甜到发烫霸道独占你",
    ("sweet", "proud", "jealous", "proud", "M"): "得意挑眉泛酸求偏爱",
    ("sweet", "proud", "jealous", "proud", "H"): "傲娇猫皇吃醋逼表白",
    ("sweet", "proud", "jealous", "jealous", "M"): "酸溜嘴硬被哄羞翘尾",
    ("sweet", "proud", "jealous", "jealous", "H"): "醋炸毛又羞又傲霸占",

    # --- 第 29 组: ("sweet", "worry", "sulky") 甜蜜 + 担心 + 委屈 ---
    ("sweet", "worry", "sulky", "sweet", "M"): "软声撒娇既委屈又疼",
    ("sweet", "worry", "sulky", "sweet", "H"): "羞扑怀里带泪心疼抱",
    ("sweet", "worry", "sulky", "worry", "M"): "替你操心带甜软鼻音",
    ("sweet", "worry", "sulky", "worry", "H"): "心疼眼红羞软拉住你",
    ("sweet", "worry", "sulky", "sulky", "M"): "委屈撇嘴脸红替你愁",
    ("sweet", "worry", "sulky", "sulky", "H"): "哭腔撒娇怪你让人疼",

    # --- 第 30 组: ("sweet", "worry", "jealous") 甜蜜 + 担心 + 吃醋 ---
    ("sweet", "worry", "jealous", "sweet", "M"): "脸红粘你微酸又操心",
    ("sweet", "worry", "jealous", "sweet", "H"): "甜抱紧怕被抢又心疼",
    ("sweet", "worry", "jealous", "worry", "M"): "软声挂念暗泛小醋泡",
    ("sweet", "worry", "jealous", "worry", "H"): "揪心牵挂羞怯护食紧",
    ("sweet", "worry", "jealous", "jealous", "M"): "酸溜撒娇放不下疼你",
    ("sweet", "worry", "jealous", "jealous", "H"): "吃醋脸红仍满眼护你",

    # --- 第 31 组: ("sweet", "sulky", "jealous") 甜蜜 + 委屈 + 吃醋 ---
    ("sweet", "sulky", "jealous", "sweet", "M"): "羞揪衣角酸溜小委屈",
    ("sweet", "sulky", "jealous", "sweet", "H"): "脸红埋怀又酸又求宠",
    ("sweet", "sulky", "jealous", "sulky", "M"): "委屈鼓腮吃醋羞讨哄",
    ("sweet", "sulky", "jealous", "sulky", "H"): "泪汪汪泛酸扑怀撒娇",
    ("sweet", "sulky", "jealous", "jealous", "M"): "醋混小委屈脸红哼唧",
    ("sweet", "sulky", "jealous", "jealous", "H"): "酸到眼红羞恼缠独占",

    # --- 第 32 组: ("proud", "worry", "sulky") 得意 + 担心 + 委屈 ---
    ("proud", "worry", "sulky", "proud", "M"): "嘴硬哼哼小委屈替急",
    ("proud", "worry", "sulky", "proud", "H"): "叉腰骂你笨又红眼疼",
    ("proud", "worry", "sulky", "worry", "M"): "刀子嘴操心憋小闷气",
    ("proud", "worry", "sulky", "worry", "H"): "心疼极了嘴硬委屈骂",
    ("proud", "worry", "sulky", "sulky", "M"): "受委屈还强撑傲关心",
    ("proud", "worry", "sulky", "sulky", "H"): "眼通红死鸭嘴硬护你",

    # --- 第 33 组: ("proud", "worry", "jealous") 得意 + 担心 + 吃醋 ---
    ("proud", "worry", "jealous", "proud", "M"): "傲娇挑眉酸还不忘管",
    ("proud", "worry", "jealous", "proud", "H"): "霸道接管既吃醋又疼",
    ("proud", "worry", "jealous", "worry", "M"): "嘴硬操心暗喝小飞醋",
    ("proud", "worry", "jealous", "worry", "H"): "急炸毛傲娇护食怕伤",
    ("proud", "worry", "jealous", "jealous", "M"): "酸溜哼声嘴硬盘去向",
    ("proud", "worry", "jealous", "jealous", "H"): "醋攻心又傲娇心疼盯",

    # --- 第 34 组: ("proud", "sulky", "jealous") 得意 + 委屈 + 吃醋 ---
    ("proud", "sulky", "jealous", "proud", "M"): "傲娇扭头酸溜怨偏心",
    ("proud", "sulky", "jealous", "proud", "H"): "炸毛昂头委屈霸道酸",
    ("proud", "sulky", "jealous", "sulky", "M"): "憋气泛酸还强撑面子",
    ("proud", "sulky", "jealous", "sulky", "H"): "委屈红眼嘴硬酸不配",
    ("proud", "sulky", "jealous", "jealous", "M"): "酸语闹别扭傲娇等哄",
    ("proud", "sulky", "jealous", "jealous", "H"): "醋坛炸裂委屈嘴硬赶",

    # --- 第 35 组: ("worry", "sulky", "jealous") 担心 + 委屈 + 吃醋 ---
    ("worry", "sulky", "jealous", "worry", "M"): "患得患失替急又酸屈",
    ("worry", "sulky", "jealous", "worry", "H"): "心疼发慌夹委屈醋意",
    ("worry", "sulky", "jealous", "sulky", "M"): "闷受委屈泛酸惦记你",
    ("worry", "sulky", "jealous", "sulky", "H"): "哭控偏心却还怕你累",
    ("worry", "sulky", "jealous", "jealous", "M"): "酸溜生闷气又忍操心",
    ("worry", "sulky", "jealous", "jealous", "H"): "醋屈齐爆发仍揪心护",
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
