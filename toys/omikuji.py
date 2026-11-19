"""
toys/omikuji.py — 浅草寺风格日系猫猫每日御神签服务

核心机制：
1. 【单日单抽铁律】：采用 (当前日期 + 用户QQ) 组合计算固定 MD5 哈希种子，保证同一群友当天无论抽多少次结果绝对一致；
2. 【二抽傲娇抓包】：若当天已经抽过签，返回傲娇抓包提示并重述今日既定运势，杜绝刷签与作弊；
3. 【丰富签文池】：涵盖大吉、中吉、小吉、吉、末吉、凶六等签位，配有七言绝句签诗、今日所宜、今日所忌与弥酱专属批注。
"""
import os
import json
import hashlib
import datetime
from typing import Dict, Tuple
from config import OMIKUJI_DAILY_FILE

# 内存单日已抽记录缓存：(today_str, user_id) -> 抽签次数（同步落盘持久化）
_DAILY_DRAW_RECORD: Dict[Tuple[str, str], int] = {}

OMIKUJI_FORTUNES = [
    # --- 大吉 (特上等) ---
    {
        "level": "🌟【大吉】",
        "title": "第七签 · 晴空喵鸣",
        "poem": "云开月露伴猫眠，莫向愁池叹往年。\n顺水乘风随自意，一碗鲜鱼胜万钱。",
        "yi": "大保底单抽即出金、买奶茶买一送一、向主人撒娇、摸鱼",
        "ji": "借钱给群友、早八、熬夜改Bug",
        "comment": "哇！运气好到爆棚了喵！今天无论抽卡还是做决定都顺顺当当的，要是出金了可得分弥酱一条小鱼干哦！"
    },
    {
        "level": "🌟【大吉】",
        "title": "第十二签 · 锦鲤翻波",
        "poem": "春风一夜落千花，踏遍青山景色华。\n得遇良朋知心客，欢声笑语暖人家。",
        "yi": "表白心仪之人、开黑连胜、吃顿大餐、安利喜欢的番剧",
        "ji": "犹豫不决、生闷气、立Flag",
        "comment": "今天头顶冒吉星呢！连平时最容易掉线的网络都会变得超丝滑，趁现在赶紧去实现心里想的事喵～"
    },

    # --- 中吉 (上等) ---
    {
        "level": "✨【中吉】",
        "title": "第十五签 · 暖阳伸腰",
        "poem": "暖阳初照瓦檐前，蜷卧绒毯意坦然。\n偶有微风吹落叶，随心自在胜神仙。",
        "yi": "睡到自然醒、下班准时打卡、整理桌面、喝杯热可可",
        "ji": "逞能抢话、剧透新番结局、贪凉吹冷风",
        "comment": "很舒服的一天喵～就算手头有点小麻烦也能轻轻松松化解，保持好心情最重要啦！"
    },
    {
        "level": "✨【中吉】",
        "title": "第二十一签 · 踏雪寻梅",
        "poem": "深巷清幽闻暗香，步随清露过高墙。\n功夫不负辛勤客，终见梅枝映早霜。",
        "yi": "耐心打磨技能、打通搁置的游戏关卡、读一本好书",
        "ji": "急于求成、浮躁暴食、跟人打赌",
        "comment": "稍微再坚持一下下，一直期待的好消息说不定今天傍晚就会悄悄敲门咯喵～"
    },

    # --- 小吉 (中上等) ---
    {
        "level": "🍀【小吉】",
        "title": "第二十八签 · 檐角风铃",
        "poem": "清风拂动玉玲珑，几缕柔光入梦中。\n莫道寻常无喜事，偶拾贝壳满怀彤。",
        "yi": "散步看晚霞、在群里唠嗑、吃甜品、偶遇粘人流浪猫",
        "ji": "久坐不动、忽略身体疲劳、胡思乱想",
        "comment": "平淡日子里藏着甜甜的小确幸喵～路边的小猫可能都会多看你两眼呢，心情会很轻快！"
    },
    {
        "level": "🍀【小吉】",
        "title": "第三十三签 · 纸鸢乘风",
        "poem": "细线随风引素琴，轻身起舞向青岑。\n行舟莫畏微波浪，自有清澜助远寻。",
        "yi": "听听主人的歌单、尝试新口味的零食、给朋友点赞",
        "ji": "过度内耗、丢三落四、乱点未知链接",
        "comment": "稳扎稳打的小吉之运！别去跟虚无缥缈的烦恼较劲，做点自己开心的事最划算喵～"
    },

    # --- 吉 (中等) ---
    {
        "level": "🍃【吉】",
        "title": "第四十签 · 静水流深",
        "poem": "平川极目水平流，一叶轻舟泛碧丘。\n待到秋深枫满径，自有硕果报从头。",
        "yi": "平平淡淡按部就班、多喝水润喉、早点睡觉休息",
        "ji": "冲动消费、在游戏里上头红温、熬夜刷短视频",
        "comment": "安安静静、平稳顺遂的一天。没有惊吓就是最好的福气喵，弥酱陪你一起窝着摸鱼！"
    },

    # --- 末吉 (中下等) ---
    {
        "level": "🍂【末吉】",
        "title": "第四十九签 · 暮色猫影",
        "poem": "斜阳虽好近黄昏，小径独行寻旧痕。\n莫向残霞空叹惋，归家炉火自温馨。",
        "yi": "回家吃热腾腾的饭菜、检查手机电量、早点关电脑",
        "ji": "赶最后一班车、深夜emo发动态、打排位上分",
        "comment": "运势稍微有点平平喵…游戏可能偶尔小逆风，但只要别上头硬怼，回家吃饱饱就什么事都没有啦！"
    },

    # --- 凶 (下等，化险为夷反差萌) ---
    {
        "level": "🐾【凶】",
        "title": "第五十六签 · 踩到猫尾",
        "poem": "浓雾蒙蒙不见山，险途前路步维艰。\n且收锋芒安自守，待得云散再开颜。",
        "yi": "低调潜水、多检查出门带钥匙、投喂弥酱转移霉运",
        "ji": "抽卡大保底、硬刚老板/导师、借伞不还",
        "comment": "哎呀！踩到小猫尾巴被哈气了喵！今天千万别去碰抽卡池，容易大保底歪常驻…快摸摸弥酱的猫耳帮你把霉运吸走喵！"
    }
]


class OmikujiService:
    @staticmethod
    def _get_today_str() -> str:
        """获取当前北京时间 YYYY-MM-DD"""
        now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
        return now.strftime("%Y-%m-%d")

    @classmethod
    def _load_daily_records(cls, today_str: str) -> Dict[str, int]:
        """从磁盘读取当日抽签记录，自动剔除过期日期"""
        records: Dict[str, int] = {}
        if os.path.exists(OMIKUJI_DAILY_FILE):
            try:
                with open(OMIKUJI_DAILY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("date") == today_str:
                    raw_recs = data.get("records") or {}
                    if isinstance(raw_recs, dict):
                        for k, v in raw_recs.items():
                            records[str(k)] = int(v)
            except Exception:
                pass
        # 同步内存缓存
        for uid, cnt in records.items():
            _DAILY_DRAW_RECORD[(today_str, uid)] = max(_DAILY_DRAW_RECORD.get((today_str, uid), 0), cnt)
        return records

    @classmethod
    def _save_daily_records(cls, today_str: str, records: Dict[str, int]) -> None:
        """原子保存当日抽签记录"""
        try:
            os.makedirs(os.path.dirname(OMIKUJI_DAILY_FILE), exist_ok=True)
            tmp_path = f"{OMIKUJI_DAILY_FILE}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"date": today_str, "records": records}, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, OMIKUJI_DAILY_FILE)
        except Exception:
            pass

    @classmethod
    def _get_fortune_for_user(cls, today_str: str, uid: str) -> dict:
        """依据日期与用户 ID 计算当日固定签文"""
        seed_str = f"{today_str}_{uid}_omikuji_seed"
        hash_digest = hashlib.md5(seed_str.encode("utf-8")).hexdigest()
        fortune_idx = int(hash_digest, 16) % len(OMIKUJI_FORTUNES)
        return OMIKUJI_FORTUNES[fortune_idx]

    @classmethod
    def get_today_fortune_info(cls, user_id: str) -> dict:
        """只读查询用户今日运势信息（不增加抽签计数，供小游戏运势联动）"""
        uid = str(user_id).strip()
        today = cls._get_today_str()
        records = cls._load_daily_records(today)
        has_drawn_today = (uid in records) or ((today, uid) in _DAILY_DRAW_RECORD)
        f = cls._get_fortune_for_user(today, uid)
        level = f["level"]
        return {
            "level": level,
            "title": f["title"],
            "is_great_luck": ("大吉" in level or "中吉" in level),
            "is_bad_luck": ("凶" in level),
            "has_drawn": has_drawn_today,
        }

    @classmethod
    def draw(cls, user_id: str, nickname: str = "") -> str:
        """
        为指定用户抽签。
        每天每人只能抽一次，依据 (today_str + user_id) 哈希值确定签文，并通过持久化防止子进程重启重抽作弊。
        """
        uid = str(user_id).strip()
        today = cls._get_today_str()
        key = (today, uid)

        # 读取磁盘持久化抽签记录，彻底解决 stdio MCP 重启后记录丢失的问题
        records = cls._load_daily_records(today)
        has_drawn_today = (uid in records) or (key in _DAILY_DRAW_RECORD)
        new_count = max(records.get(uid, 0), _DAILY_DRAW_RECORD.get(key, 0)) + 1
        records[uid] = new_count
        _DAILY_DRAW_RECORD[key] = new_count
        cls._save_daily_records(today, records)

        f = cls._get_fortune_for_user(today, uid)
        name_display = nickname or f"群友 {uid}"

        if has_drawn_today:
            return (
                f"（小猫按住你的爪爪）唔… {name_display} 你今天已经求过签啦！\n"
                f"不要想通过作弊重抽换签喵，神仙可是会生气的～\n\n"
                f"📜【你今日的既定运势回顾】：\n"
                f"运势：{f['level']}（{f['title']}）\n"
                f"【今日宜】：{f['yi']}\n"
                f"【今日忌】：{f['ji']}\n"
                f"弥酱批注：{f['comment']}"
            )

        card_text = (
            f"⛩️ ✨【浅草寺 · 弥酱猫猫御神签】✨ ⛩️\n"
            f"求签人：{name_display}\n"
            f"日期：{today}\n"
            f"--------------------------\n"
            f"运势评定：{f['level']}\n"
            f"签位题名：{f['title']}\n\n"
            f"【签诗】\n{f['poem']}\n\n"
            f"【今日宜】{f['yi']}\n"
            f"【今日忌】{f['ji']}\n"
            f"--------------------------\n"
            f"🐾 弥酱批注：{f['comment']}"
        )
        return card_text
