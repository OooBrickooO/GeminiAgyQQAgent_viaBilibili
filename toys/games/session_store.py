"""
toys/games/session_store.py — 牌局会话持久化、心情棋风、局末结算与战术台词翻译
"""
import copy
import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

from config import BOARD_GAMES_STATE_FILE
from social.affection import settle_turn_affection
from social.mid_term_memory import add_mid_term_memory
from social.mood_manager import apply_mood_update, get_decayed_mood_state

GAME_IDLE_TIMEOUT = 1800.0  # 30 分钟无操作自动过期清理

GAME_NAMES_CN = {
    "gomoku": "猫爪五子棋",
    "othello": "翻转黑白棋",
    "chess": "国际象棋",
    "xiangqi": "中国象棋",
    "blackjack": "猫猫21点",
    "1a2b": "1A2B破译密码",
}

DIFF_NAMES_CN = {
    "easy": "轻松入门",
    "normal": "认真较量",
    "hard": "全力以赴",
}

GOMOKU_COLS = "ABCDEFGHIJKLMNO"
OTHELLO_COLS = "ABCDEFGH"

_GAMES_LOADED = False
_GAMES_MTIME: float = -1.0
_GAME_SESSIONS: Dict[str, dict] = {}


def normalize_chat_id(chat_id_raw: str, is_private: Optional[bool] = None) -> str:
    cid = str(chat_id_raw or "").strip()
    if not cid:
        return ""
    if cid.startswith("private_") or cid.startswith("group_"):
        return cid
    if is_private is True:
        return f"private_{cid}"
    return f"group_{cid}"


def _load_sessions(force: bool = False) -> Dict[str, dict]:
    global _GAMES_LOADED, _GAMES_MTIME, _GAME_SESSIONS
    exists = os.path.exists(BOARD_GAMES_STATE_FILE)
    if _GAMES_LOADED and not force:
        if exists:
            try:
                cur_mtime = os.path.getmtime(BOARD_GAMES_STATE_FILE)
                if cur_mtime == _GAMES_MTIME:
                    return _GAME_SESSIONS
            except OSError:
                return _GAME_SESSIONS
        else:
            if _GAMES_MTIME > 0:
                _GAME_SESSIONS = {}
                _GAMES_MTIME = -1.0
            return _GAME_SESSIONS

    _GAMES_LOADED = True
    if not exists:
        _GAME_SESSIONS = {}
        _GAMES_MTIME = -1.0
        return _GAME_SESSIONS
    try:
        cur_mtime = os.path.getmtime(BOARD_GAMES_STATE_FILE)
        with open(BOARD_GAMES_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _GAME_SESSIONS = data if isinstance(data, dict) else {}
        _GAMES_MTIME = cur_mtime
    except Exception as e:
        print(f"[BoardGames] 读取牌局状态失败: {e}")
        _GAME_SESSIONS = {}
    return _GAME_SESSIONS


def _save_sessions() -> None:
    global _GAMES_LOADED, _GAMES_MTIME
    try:
        os.makedirs(os.path.dirname(BOARD_GAMES_STATE_FILE), exist_ok=True)
        tmp_path = f"{BOARD_GAMES_STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_GAME_SESSIONS, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, BOARD_GAMES_STATE_FILE)
        _GAMES_LOADED = True
        try:
            _GAMES_MTIME = os.path.getmtime(BOARD_GAMES_STATE_FILE)
        except OSError:
            pass
    except Exception as e:
        print(f"[BoardGames] 保存牌局状态失败: {e}")


def get_active_game(chat_id_str: str) -> Optional[dict]:
    """获取当前会话进行中的有效棋局（若超时或已结束会自动清理并返 None）"""
    cid = str(chat_id_str or "").strip()
    if not cid:
        return None
    sessions = _load_sessions()
    norm_cid = normalize_chat_id(cid)
    candidates = [cid]
    if norm_cid not in candidates:
        candidates.append(norm_cid)
    raw_group = norm_cid.replace("group_", "", 1) if norm_cid.startswith("group_") else ""
    if raw_group and raw_group not in candidates:
        candidates.append(raw_group)

    matched_key = next((k for k in candidates if k in sessions), None)
    if not matched_key:
        return None

    sess = sessions[matched_key]
    if sess.get("finished") or (time.time() - float(sess.get("updated_at", 0)) > GAME_IDLE_TIMEOUT):
        for k in set(candidates):
            sessions.pop(k, None)
        _save_sessions()
        return None

    canonical = normalize_chat_id(matched_key, is_private=bool(sess.get("is_private")))
    if canonical and canonical != matched_key:
        sessions.pop(matched_key, None)
        sess["chat_id"] = canonical
        sessions[canonical] = sess
        _save_sessions()
    return sess


def has_active_game(chat_id_str: str) -> bool:
    return get_active_game(chat_id_str) is not None


def save_active_game(sess: dict) -> None:
    sessions = _load_sessions()
    canonical = normalize_chat_id(sess.get("chat_id", ""), is_private=bool(sess.get("is_private")))
    sess["chat_id"] = canonical
    if canonical.startswith("group_"):
        sessions.pop(canonical.replace("group_", "", 1), None)
    sessions[canonical] = sess
    _save_sessions()


def clear_active_game(chat_id_str: str) -> None:
    cid = str(chat_id_str or "").strip()
    if not cid:
        return
    sessions = _load_sessions()
    norm_cid = normalize_chat_id(cid)
    keys = {cid, norm_cid}
    if norm_cid.startswith("group_"):
        keys.add(norm_cid.replace("group_", "", 1))
    removed = False
    for k in keys:
        if k in sessions:
            sessions.pop(k, None)
            removed = True
    if removed:
        _save_sessions()


def push_history(sess: dict) -> None:
    hist = sess.setdefault("history", [])
    hist.append(copy.deepcopy(sess["state"]))
    if len(hist) > 6:
        hist.pop(0)


def get_mood_playstyle() -> Dict[str, Any]:
    """读取弥酱当前 7D 心情维度，动态映射为攻防权重与棋风名"""
    try:
        view, _val = get_decayed_mood_state()
        dims = view.get("dims", {}) if isinstance(view, dict) else {}
    except Exception:
        dims = {}

    proud = float(dims.get("proud", 0.3))
    sulky = float(dims.get("sulky", 0.0))
    jealous = float(dims.get("jealous", 0.0))
    worry = float(dims.get("worry", 0.0))
    calm = float(dims.get("calm", 0.5))

    if proud >= 0.6 or sulky >= 0.45 or jealous >= 0.45:
        return {"style": "激进猛攻流", "w_atk": 1.35, "w_def": 0.85, "dims": dims}
    if worry >= 0.45 or calm >= 0.68:
        return {"style": "铁血严防流", "w_atk": 0.90, "w_def": 1.35, "dims": dims}
    return {"style": "稳健均衡流", "w_atk": 1.05, "w_def": 1.05, "dims": dims}


def normalize_game_type(raw: str) -> Optional[str]:
    t = (raw or "").strip().lower()
    if any(k in t for k in ("国际象棋", "西洋棋", "chess")):
        return "chess"
    if any(k in t for k in ("中国象棋", "象棋", "xiangqi")):
        return "xiangqi"
    if any(k in t for k in ("五子棋", "gomoku", "wuziqi")):
        return "gomoku"
    if any(k in t for k in ("黑白棋", "奥赛罗", "翻转棋", "othello", "reversi")):
        return "othello"
    if any(k in t for k in ("21点", "二十一点", "blackjack", "bj", "猫猫21")):
        return "blackjack"
    if any(k in t for k in ("1a2b", "猜数字", "破译密码", "密码")):
        return "1a2b"
    return None


def normalize_difficulty(raw: str, default: str = "normal") -> str:
    t = (raw or "").strip().lower()
    if any(k in t for k in ("简单", "入门", "新手", "初级", "easy")):
        return "easy"
    if any(k in t for k in ("困难", "地狱", "认真", "全力", "高手", "高级", "hard", "expert")):
        return "hard"
    if any(k in t for k in ("普通", "中等", "正常", "中级", "normal", "medium")):
        return "normal"
    return default


def strip_noise_for_chitchat(raw_text: str, matched_tokens: List[str]) -> bool:
    """剔除落子指令与常见连接词后，判断是否还伴随显著闲聊（融合聊天+走棋）"""
    cleaned = raw_text or ""
    for tok in matched_tokens:
        if tok:
            cleaned = cleaned.replace(tok, "")
    cleaned = re.sub(
        r"(我|下在|下到|落子|走在|走|下|猜|试试|要牌|停牌|双倍|加倍|再来一张|开牌|不要了|吧|啊|呀|哦|呢|嘛|\s|[,.!?，。！？~])",
        "",
        cleaned,
    )
    return len(cleaned) >= 4


def settle_match_outcome(
    sess: dict,
    outcome: str,
    detail_note: str,
    is_private: bool,
    extra_multiplier: float = 1.0,
) -> Dict[str, Any]:
    """对局结束统一结算：好感度共振 + 7D 心情波动 + 频道隔离 L2 中期记忆"""
    player_id = str(sess.get("player_id", ""))
    player_name = str(sess.get("player_name", player_id))
    chat_id_str = str(sess.get("chat_id", ""))
    game_type = sess.get("game_type", "gomoku")
    game_cn = GAME_NAMES_CN.get(game_type, game_type)
    diff = sess.get("difficulty", "normal")
    diff_cn = DIFF_NAMES_CN.get(diff, diff)
    turns = int(sess.get("turn_count", 0))
    diff_bonus = {"easy": 0.85, "normal": 1.0, "hard": 1.25}.get(diff, 1.0) * extra_multiplier

    outcome_cfg = {
        "user_win": (0.72, 0.04, {"happy": 0.08, "sulky": 0.06, "sweet": 0.04}, f"{player_name}获胜"),
        "bot_win": (0.55, 0.08, {"proud": 0.14, "happy": 0.10, "sweet": 0.03}, "弥酱获胜"),
        "draw": (0.65, 0.05, {"happy": 0.06, "calm": 0.05}, "平局"),
        "user_resign": (0.20, 0.02, {"proud": 0.07}, f"{player_name}认输"),
    }
    base_res, mood_delta, dim_deltas, result_label = outcome_cfg.get(outcome, outcome_cfg["user_resign"])
    resonance = base_res if outcome == "user_resign" else min(1.0, base_res * diff_bonus)

    aff_res = {}
    try:
        aff_res = settle_turn_affection(
            user_id_str=player_id,
            mood_resonance=resonance,
            reason=f"与弥酱游玩【{game_cn}({diff_cn})】：{result_label}",
        )
    except Exception as e:
        print(f"[BoardGames] 好感度结算异常: {e}")

    try:
        apply_mood_update(
            mood_delta=mood_delta,
            dim_deltas=dim_deltas,
            reason=f"{game_cn}对局结束({result_label})",
            source_scene=chat_id_str,
        )
    except Exception as e:
        print(f"[BoardGames] 心情更新异常: {e}")

    group_scene = chat_id_str if chat_id_str.startswith("group_") else f"group_{chat_id_str}"
    target_l2_id = str(player_id) if is_private else group_scene
    scene_l2 = f"private_{player_id}" if is_private else group_scene
    mem_text = (
        f"和 {player_name}({player_id}) 玩了一局【{game_cn}】({diff_cn}，共 {turns} 回合)，"
        f"结果：{result_label}。{detail_note}"
    )
    try:
        add_mid_term_memory(
            target_id=target_l2_id,
            text=mem_text,
            importance=0.68,
            tags=["小游戏", game_cn],
            related=[player_name],
            source_scene=scene_l2,
        )
    except Exception as e:
        print(f"[BoardGames] 写入L2记忆异常: {e}")

    return {"result_label": result_label, "affection": aff_res}


def situational_line(game_type: str, tag: str, extra: Dict[str, Any]) -> str:
    """0-Token 战术事件翻译官：依据事件标签 + 当前心情风格给出灵动短句"""
    style = get_mood_playstyle()["style"]
    if game_type == "gomoku":
        coord, uc = extra.get("bot_coord", ""), extra.get("user_coord", "")
        pool = {
            "OPENING": [f"好耶，那我下在 {coord} 啦！轮到你出招~", f"接招！我在 {coord} 落子咯，看看你怎么破局✨"],
            "BLOCK_FOUR": [f"呜哇好险！差点被你在 {uc} 连起来，我在 {coord} 先把口子堵死了！", f"休想得逞！一眼看穿你 {uc} 的杀招啦，{coord} 挡住！"],
            "BLOCK_THREE": [f"哼哼，想在 {uc} 偷偷搭活三？我在 {coord} 提前卡位啦~", f"这步 {uc} 有点东西嘛，不过我 {coord} 先按住不让你舒服！"],
            "BOT_LIVE_THREE": [f"轮到本喵发力咯~ 落子 {coord}，这边已经悄悄连出气势了哦！", f"看我这手 {coord}！现在压力来到你这边啦（托腮）"],
            "BOT_LIVE_FOUR": [f"抓到破绽啦！我在 {coord} 落子，准备接招吧✨"],
            "BOT_VCF_ATTACK": [f"计算完成！{coord}！这一步可是看穿好几手后面的连环招哦~"],
            "MIDGAME": {
                "激进猛攻流": [f"哼，我就下在 {coord}！今天气势很足可不会让着你~", f"贴身拆招，落子 {coord}！轮到你了！"],
                "铁血严防流": [f"稳一点，把 {coord} 守好比较踏实…轮到你落子啦。", f"先在 {coord} 筑好防线，看看你接下来怎么走~"],
                "稳健均衡流": [f"我落子在 {coord} 哦~ 下一步到你啦！", f"思考了一下决定走 {coord}，请多指教✨"],
            }.get(style, [f"我走 {coord}，轮到你啦~"]),
        }
        return random.choice(pool.get(tag, [f"我在 {coord} 落子啦，轮到你了！"]))

    if game_type == "othello":
        coord, flipped = extra.get("bot_coord", ""), extra.get("flipped_cnt", 0)
        b_cnt, w_cnt = extra.get("b_cnt", 0), extra.get("w_cnt", 0)
        if tag == "BOT_PASS":
            return f"呜…我这边没有合法落子点只能 Pass 啦，还是你的回合！（黑 {b_cnt} : 白 {w_cnt}）"
        if tag == "FORCED_PASS":
            return f"我在 {coord} 翻了 {flipped} 颗，结果你没地方可下了耶~ 那本喵连下到你有步走为止！（黑 {b_cnt} : 白 {w_cnt}）"
        if tag == "CORNER":
            return f"角落 {coord} 归本喵啦！这下稳稳拿捏住一条边了✨（当前 黑 {b_cnt} : 白 {w_cnt}）"
        if tag == "MASSIVE_FLIP":
            return f"哗啦—— {coord} 一口气翻了 {flipped} 颗棋子！爽到飞起~（黑 {b_cnt} : 白 {w_cnt}）"
        return f"我下在 {coord}，翻回 {flipped} 颗！（黑 {b_cnt} : 白 {w_cnt}，绿点是你可以下的位置哦）"

    if game_type == "blackjack" and tag == "HIT_SAFE":
        return f"给你发了一张 {extra.get('new_card', '')}，现在总共 {extra.get('p_val', 0)} 点~ 要继续“要牌”还是“停牌”呢？"

    if game_type == "1a2b":
        guess, a, b, left = extra.get("guess", ""), extra.get("a", 0), extra.get("b", 0), extra.get("left", 0)
        if tag == "ZERO_HIT":
            return f"{guess} → 0A0B！完美避开了所有正确数字，反而帮你排除掉 4 个选项了呢（还剩 {left} 次）"
        if tag == "CLOSE":
            return f"{guess} → {a}A{b}B！哇，好接近答案了！再推敲一下位置（还剩 {left} 次）"
        return f"{guess} → {a}A{b}B！还剩 {left} 次猜测机会哦~"

    if game_type == "chess":
        bm, cap_b, cap_u = extra.get("bot_move", ""), extra.get("bot_cap_cn", ""), extra.get("user_cap_cn", "")
        pool = {
            "BOT_CHECK": [f"将军！我走 {bm}，你的白王得赶紧避避风头咯~", f"Check! 我走出 {bm}，看看你怎么解将✨"],
            "BOT_CAPTURE_MAJOR": [f"抓到破绽啦！{bm} 直接收走你的{cap_b}，这波血赚~", f"我走 {bm} 吃掉{cap_b}咯，现在轮到你反击！"],
            "USER_CAPTURE_MAJOR": [f"呜哇我的{cap_u}！好狠的手啊…那我走 {bm} 回敬一下！", f"居然把我的{cap_u}吃掉了哼，我走 {bm} 稳住阵脚！"],
            "CASTLE": [f"王车易位到位！我走 {bm}，接下来进入中局较量咯~"],
            "PROMOTION": [f"升变时刻！我应以 {bm}，局势一下子紧张起来了呢~"],
            "BOT_CAPTURE": [f"我走 {bm} 顺手吃掉{cap_b}，轮到你出招啦~"],
            "NORMAL": {
                "激进猛攻流": [f"气势拉满，黑方走 {bm}！看你敢不敢对攻~", f"不跟你客气，我走 {bm}！轮到你了！"],
                "铁血严防流": [f"稳扎稳打，黑方走 {bm}，先把阵型护好…轮到你啦。", f"我走 {bm} 筑好防线，请出招~"],
                "稳健均衡流": [f"黑方走 {bm} 哦~ 轮到你啦（坐标如 e2e4 或记谱如 Nf3 都行）", f"思考了一下走 {bm}，请多指教✨"],
            }.get(style, [f"黑方走 {bm}，轮到你啦~"]),
        }
        return random.choice(pool.get(tag, [f"黑方走 {bm}，轮到你了~"]))

    if game_type == "xiangqi":
        bm, cap_b, cap_u = extra.get("bot_move", ""), extra.get("bot_cap_cn", ""), extra.get("user_cap_cn", "")
        pool = {
            "BOT_CHECK": [f"将军！黑方走 {bm}，红帅小心咯~", f"看招！{bm} 直接将军，你怎么应？"],
            "BOT_CAPTURE_MAJOR": [f"哈哈抓到机会啦！{bm} 吃掉你的{cap_b}，这下主动权归我咯~", f"黑方 {bm} 拿下{cap_b}！轮到红方反击啦~"],
            "USER_CAPTURE_MAJOR": [f"呜啊我的{cap_u}没了！那我黑方走 {bm}，可不会轻易认输哦~", f"好痛的一手…我走 {bm} 补防一下，接着来！"],
            "BOT_CAPTURE": [f"黑方走 {bm} 顺手吃掉{cap_b}，轮到你啦~"],
            "NORMAL": {
                "激进猛攻流": [f"今天手气正好，黑方走 {bm}！接招吧~", f"冲冲冲，黑方 {bm}！轮到红方了！"],
                "铁血严防流": [f"稳一点比较好，黑方走 {bm} 守住要道…轮到你咯。", f"先稳阵脚，黑方 {bm}，看你怎么破局~"],
                "稳健均衡流": [f"黑方走 {bm} 啦~ 轮到你出招（发中文记谱如 炮二平五 或坐标如 H2E2 都可以）", f"我走 {bm}，请红方赐教✨"],
            }.get(style, [f"黑方走 {bm}，轮到你啦~"]),
        }
        return random.choice(pool.get(tag, [f"黑方走 {bm}，轮到你咯~"]))

    return "轮到你咯~"
