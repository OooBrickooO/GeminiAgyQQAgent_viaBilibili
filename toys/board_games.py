"""
toys/board_games.py — 弥酱桌面游戏双轨调度中心与兼容外观入口

具体游戏规则与 AI 搜索算法已解耦至 `toys/games/` 子包：
- `session_store.py`: 会话存储、心情棋风、好感/L2 结算、战术事件台词翻译
- `gomoku_engine.py`: 猫爪五子棋 (15x15)
- `othello_engine.py`: 翻转黑白棋 (8x8)
- `cards_and_numbers.py`: 猫猫21点 & 1A2B 破译密码
"""
import json
import re
import time
import unicodedata
from typing import Any, Dict, Optional

from config import MASTER_QQ, log_mcp_tool
from social.affection import get_user_profile
from toys.board_renderer import (
    render_blackjack_table,
    render_chess_board,
    render_gomoku_board,
    render_othello_board,
    render_xiangqi_board,
)
from toys.games.cards_and_numbers import (
    bj_hand_value,
    bj_summary,
    gen_1a2b_secret,
    new_shuffled_deck,
    step_1a2b,
    step_blackjack,
    summary_1a2b,
)
from toys.games.chess_engine import (
    chess_board_summary,
    init_chess_state,
    parse_chess_move,
    step_chess,
)
from toys.games.xiangqi_engine import (
    init_xiangqi_state,
    parse_xiangqi_move,
    step_xiangqi,
    xiangqi_board_summary,
)
from toys.games.gomoku_engine import (
    choose_gomoku_bot_move,
    fmt_gomoku,
    gomoku_board_summary,
    parse_gomoku_coord,
    step_gomoku,
)
from toys.games.othello_engine import (
    choose_othello_bot_move,
    fmt_othello,
    init_othello_board,
    othello_board_summary,
    othello_valid_moves,
    parse_othello_coord,
    step_othello,
)
from toys.games.session_store import (
    DIFF_NAMES_CN,
    GAME_NAMES_CN,
    clear_active_game,
    get_active_game,
    get_mood_playstyle,
    has_active_game,
    normalize_chat_id,
    normalize_difficulty,
    normalize_game_type,
    save_active_game,
    settle_match_outcome,
    strip_noise_for_chitchat,
)
from toys.omikuji import OmikujiService

# 兼容别名导出
_get_mood_playstyle = get_mood_playstyle
_choose_gomoku_bot_move = choose_gomoku_bot_move
_choose_othello_bot_move = choose_othello_bot_move


def _summary_for_session(sess: dict, event_desc: str, show_all_bot: bool = False) -> str:
    gt = sess["game_type"]
    if gt == "gomoku":
        return gomoku_board_summary(sess, event_desc)
    if gt == "othello":
        return othello_board_summary(sess, event_desc)
    if gt == "chess":
        return chess_board_summary(sess, event_desc)
    if gt == "xiangqi":
        return xiangqi_board_summary(sess, event_desc)
    if gt == "blackjack":
        return bj_summary(sess, event_desc, show_all_bot=show_all_bot)
    return summary_1a2b(sess, event_desc)


def start_game(
    chat_id_str: str,
    player_id: str,
    player_name: str,
    game_type_raw: str,
    difficulty_raw: str = "normal",
    is_private: bool = False,
) -> Dict[str, Any]:
    """初始化一局新游戏，返回图像路径、文案与状态"""
    chat_id_str = normalize_chat_id(chat_id_str, is_private=is_private)
    gtype = normalize_game_type(game_type_raw) or "gomoku"
    diff = normalize_difficulty(difficulty_raw, default="normal")

    try:
        aff = float(get_user_profile(str(player_id)).get("affection", 0))
    except Exception:
        aff = 0.0
    max_undo = 2 if (str(player_id) == str(MASTER_QQ) or aff >= 500) else 1

    now = time.time()
    sess: Dict[str, Any] = {
        "chat_id": str(chat_id_str),
        "is_private": bool(is_private),
        "game_type": gtype,
        "player_id": str(player_id),
        "player_name": str(player_name or player_id),
        "difficulty": diff,
        "turn_count": 0,
        "undo_used": 0,
        "max_undo": max_undo,
        "history": [],
        "finished": False,
        "created_at": now,
        "updated_at": now,
        "state": {},
    }

    image_path: Optional[str] = None
    should_llm = False

    if gtype == "gomoku":
        board = [[0] * 15 for _ in range(15)]
        sess["state"] = {"board": board, "last_user_move": None, "last_bot_move": None, "threat_points": [], "win_line": None}
        image_path = render_gomoku_board(board)
        reply_text = f"【猫爪五子棋（{DIFF_NAMES_CN[diff]}）】开局啦！你执黑先手，直接发坐标（如 H8）就能落子，最多可悔棋 {max_undo} 次哦~"
        board_summary = _summary_for_session(sess, "新开局，黑方（玩家）先手。")

    elif gtype == "othello":
        board = init_othello_board()
        valid = [(r, c) for r, c, _ in othello_valid_moves(board, 1)]
        sess["state"] = {"board": board, "last_user_move": None, "last_bot_move": None, "flipped_cells": []}
        image_path = render_othello_board(board, valid_moves=valid)
        valid_str = ", ".join(fmt_othello(r, c) for r, c in valid)
        reply_text = f"【翻转黑白棋（{DIFF_NAMES_CN[diff]}）】摆好棋盘啦！你执黑先行，绿点位置可落子（{valid_str}），发坐标就能下~"
        board_summary = _summary_for_session(sess, "新开局，黑方（玩家）先手。")

    elif gtype == "chess":
        st = init_chess_state()
        sess["state"] = st
        image_path = render_chess_board(st["board"])
        reply_text = f"【国际象棋（{DIFF_NAMES_CN[diff]}）】棋盘摆好啦！你执白先手，直接发起止坐标（如 e2e4 / e2 e4）或代数记谱（如 Nf3 / O-O）就能走子，最多可悔棋 {max_undo} 次哦~"
        board_summary = _summary_for_session(sess, "国际象棋新开局，白方（玩家）先手。")

    elif gtype == "xiangqi":
        st = init_xiangqi_state()
        sess["state"] = st
        image_path = render_xiangqi_board(st["board"])
        reply_text = f"【中国象棋（{DIFF_NAMES_CN[diff]}）】楚河汉界摆好啦！你执红先行，发中文记谱（如 炮二平五 / 马8进7）或棋盘坐标（如 H2E2）都能走子，最多可悔棋 {max_undo} 次哦~"
        board_summary = _summary_for_session(sess, "中国象棋新开局，红方（玩家）先手。")

    elif gtype == "blackjack":
        deck = new_shuffled_deck()
        p_hand, b_hand = [deck.pop(), deck.pop()], [deck.pop(), deck.pop()]
        try:
            fortune = OmikujiService.get_today_fortune_info(str(player_id))
        except Exception:
            fortune = {}

        p_val, _ = bj_hand_value(p_hand)
        b_val, _ = bj_hand_value(b_hand)
        sess["state"] = {"deck": deck, "player_hand": p_hand, "bot_hand": b_hand, "doubled": False, "fortune": fortune}

        f_prefix = ""
        if fortune.get("has_drawn"):
            if fortune.get("is_great_luck"):
                f_prefix = f"今天你抽中了【{fortune['title']}】牌运拉满呢✨ "
            elif fortune.get("is_bad_luck"):
                f_prefix = f"看你今天签运是【{fortune['title']}】，可要当心别爆牌哦~ "
            else:
                f_prefix = f"今日签运【{fortune['title']}】加持中~ "

        if p_val == 21 or b_val == 21:
            sess["finished"] = True
            outcome, desc = (
                ("draw", "双方开局同时拿到 Blackjack (21点) 平局！") if (p_val == 21 and b_val == 21)
                else ("user_win", "玩家起手天胡 Blackjack (21点) 秒杀庄家！") if p_val == 21
                else ("bot_win", "弥酱起手拿到 Blackjack (21点) 直接拿下！")
            )
            image_path = render_blackjack_table(p_hand, b_hand, hide_bot_hole=False, player_score=p_val, bot_score_display=f"{b_val} pts", status_title="Cat Blackjack - Instant Showdown!")
            settle_match_outcome(sess, outcome, desc, is_private, extra_multiplier=1.2)
            should_llm = True
            board_summary = _summary_for_session(sess, f_prefix + desc, show_all_bot=True)
            reply_text = f_prefix + desc
        else:
            image_path = render_blackjack_table(p_hand, b_hand, hide_bot_hole=True, player_score=p_val, bot_score_display="?", status_title="Cat Blackjack")
            reply_text = f"{f_prefix}【猫猫21点】发牌完毕！你目前 {p_val} 点，发送「要牌」「停牌」或「双倍」即可出招~"
            board_summary = _summary_for_session(sess, "开局发牌完成，等待玩家操作（要牌/停牌/双倍）。")

    else:
        max_att = 10 if diff == "easy" else (7 if diff == "hard" else 8)
        sess["state"] = {"secret": gen_1a2b_secret(), "guesses": [], "max_attempts": max_att, "hint_given": False}
        reply_text = f"【1A2B 破译密码（{DIFF_NAMES_CN[diff]}）】我想好了一组 4 位不重复的数字密码！你一共有 {max_att} 次猜测机会，直接发 4 位数字就行~"
        board_summary = _summary_for_session(sess, "密码已生成，等待首次破译。")

    if sess.get("finished"):
        clear_active_game(chat_id_str)
    else:
        save_active_game(sess)

    return {
        "ok": True, "handled": True, "action_type": "FAST_START", "game_type": gtype,
        "image_path": image_path, "reply_text": reply_text,
        "should_trigger_llm_hook": should_llm, "board_summary_for_llm": board_summary,
        "game_over": bool(sess.get("finished")),
    }


def _handle_undo(sess: dict) -> Dict[str, Any]:
    gtype = sess["game_type"]
    if gtype not in ("gomoku", "othello", "chess", "xiangqi"):
        return {"ok": False, "handled": True, "action_type": "UNDO", "reply_text": "扑克/猜数字游戏不能悔棋哦，继续勇敢出招吧~", "image_path": None, "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False}
    hist = sess.get("history", [])
    if not hist:
        return {"ok": False, "handled": True, "action_type": "UNDO", "reply_text": "还没有落子呢，没办法悔棋哦~", "image_path": None, "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False}
    if sess["undo_used"] >= sess["max_undo"]:
        return {"ok": False, "handled": True, "action_type": "UNDO", "reply_text": f"这局 {sess['max_undo']} 次悔棋机会已经用光啦！不许再赖皮了哼~", "image_path": None, "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False}

    prev_state = hist.pop()
    sess["state"] = prev_state
    sess["undo_used"] += 1
    sess["turn_count"] = max(0, int(sess.get("turn_count", 1)) - 1)
    sess["updated_at"] = time.time()
    save_active_game(sess)

    if gtype == "gomoku":
        img = render_gomoku_board(prev_state["board"], last_user_move=prev_state.get("last_user_move"), last_bot_move=prev_state.get("last_bot_move"), threat_points=prev_state.get("threat_points"))
    elif gtype == "chess":
        img = render_chess_board(prev_state["board"], last_user_move=prev_state.get("last_user_move"), last_bot_move=prev_state.get("last_bot_move"), in_check_side=prev_state.get("in_check_side", ""), captured_by_user=prev_state.get("captured_by_user"), captured_by_bot=prev_state.get("captured_by_bot"))
    elif gtype == "xiangqi":
        img = render_xiangqi_board(prev_state["board"], last_user_move=prev_state.get("last_user_move"), last_bot_move=prev_state.get("last_bot_move"), in_check_side=prev_state.get("in_check_side", ""), captured_by_user=prev_state.get("captured_by_user"), captured_by_bot=prev_state.get("captured_by_bot"))
    else:
        valid = [(r, c) for r, c, _ in othello_valid_moves(prev_state["board"], 1)]
        flipped_tuples = [tuple(pt) for pt in (prev_state.get("flipped_cells") or []) if len(pt) == 2]
        img = render_othello_board(prev_state["board"], valid_moves=valid, last_user_move=prev_state.get("last_user_move"), last_bot_move=prev_state.get("last_bot_move"), flipped_cells=flipped_tuples)

    left = sess["max_undo"] - sess["undo_used"]
    return {
        "ok": True, "handled": True, "action_type": "UNDO", "image_path": img,
        "reply_text": f"好啦好啦，特批你退回上一步~（本局还剩 {left} 次悔棋机会）",
        "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
    }


def _handle_resign(sess: dict) -> Dict[str, Any]:
    gtype = sess["game_type"]
    sess["finished"] = True
    desc = f"玩家在【{GAME_NAMES_CN.get(gtype, gtype)}】第 {sess['turn_count']} 回合主动认输。"
    if gtype == "1a2b":
        desc += f" 密码答案其实是 {sess['state']['secret']}。"
    settle_match_outcome(sess, "user_resign", desc, bool(sess.get("is_private")))
    summary = _summary_for_session(sess, desc, show_all_bot=True)
    clear_active_game(sess["chat_id"])
    return {
        "ok": True, "handled": True, "action_type": "RESIGN", "image_path": None,
        "reply_text": "承让啦~ 认输也是勇气的表现呢！",
        "should_trigger_llm_hook": True, "board_summary_for_llm": summary, "game_over": True,
    }


def try_fast_start_game(
    chat_id_str: str,
    sender_id: str,
    nickname: str,
    raw_clean_text: str,
    is_private: bool = False,
) -> Optional[Dict[str, Any]]:
    """无活跃棋局时识别干脆的开局短句并在 0-Token 快速通道直接开局发图"""
    if get_active_game(chat_id_str) is not None:
        return None
    text = unicodedata.normalize("NFKC", raw_clean_text or "").strip()
    text = re.sub(r"^(?:@\S+\s*|弥酱|星见弥|弥弥)+[,，:：!！~～\s]*", "", text).strip()
    if not text or len(text) > 35:
        return None
    m_start = re.match(
        r"^\s*(?:(?:我们|咱们|一起)\s*)*(?:来一?[局盘把场]|玩一?[局盘把]?|下一?[局盘把]?|开一?[局盘把]|开始|我想玩|我想下|陪我玩|陪我下|来下)\s*一?[局盘把场]?\s*(.+)$",
        text,
    )
    if not m_start:
        return None
    gtype = normalize_game_type(m_start.group(1))
    if not gtype:
        return None
    diff = normalize_difficulty(m_start.group(1), default="normal")
    return start_game(chat_id_str, sender_id, nickname, gtype, diff, is_private=is_private)


def analyze_and_execute_game_turn(
    chat_id_str: str,
    sender_id: str,
    nickname: str,
    raw_clean_text: str,
) -> Dict[str, Any]:
    """双轨中枢入口：在活跃棋局中识别当前玩家的走子/操作/悔棋/认输/闲聊并调度执行"""
    sess = get_active_game(chat_id_str)
    if not sess:
        return {"handled": False, "action_type": "NONE"}
    if str(sender_id) != str(sess["player_id"]):
        return {"handled": False, "action_type": "OTHER_USER"}

    text = unicodedata.normalize("NFKC", raw_clean_text or "").strip()
    gtype = sess["game_type"]

    if re.search(r"(认输|投降|不玩了|退出游戏|结束游戏|不下了|掀桌)", text):
        return _handle_resign(sess)
    if re.search(r"(悔棋|退一步|撤回上一步|重下上一步)", text):
        return _handle_undo(sess)

    if gtype == "gomoku":
        rc = parse_gomoku_coord(text)
        if rc is not None:
            tok = fmt_gomoku(rc[0], rc[1])
            return step_gomoku(sess, rc, strip_noise_for_chitchat(text, [tok, tok.lower()]))
        hint = "玩家没有报出新坐标，正在和你就当前棋局闲聊/提问。"
    elif gtype == "othello":
        rc = parse_othello_coord(text)
        if rc is not None:
            tok = fmt_othello(rc[0], rc[1])
            return step_othello(sess, rc, strip_noise_for_chitchat(text, [tok, tok.lower()]))
        hint = "玩家没有落子，正在就棋局提问或闲聊。"
    elif gtype == "chess":
        st = sess["state"]
        mv, tok = parse_chess_move(st["board"], st["castling"], st.get("ep_target"), text, side="w")
        if mv is not None:
            return step_chess(sess, text, strip_noise_for_chitchat(text, [tok]))
        hint = "玩家没有报出国际象棋走法，正在就当前棋局提问或闲聊。"
    elif gtype == "xiangqi":
        st = sess["state"]
        mv, tok = parse_xiangqi_move(st["board"], text, side="r")
        if mv is not None:
            return step_xiangqi(sess, text, strip_noise_for_chitchat(text, [tok]))
        hint = "玩家没有报出中国象棋走法，正在就当前棋局提问或闲聊。"
    elif gtype == "blackjack":
        if re.search(r"(双倍|加倍|\bdouble\b)", text, re.IGNORECASE):
            return step_blackjack(sess, "double", strip_noise_for_chitchat(text, ["双倍", "加倍", "double"]))
        if re.search(r"(停牌|不要了|开牌|摊牌|就这样|\bstand\b)", text, re.IGNORECASE):
            return step_blackjack(sess, "stand", strip_noise_for_chitchat(text, ["停牌", "不要了", "开牌", "摊牌", "就这样", "stand"]))
        if re.search(r"(要牌|再来一张|拿牌|继续要|\bhit\b|^要$)", text, re.IGNORECASE):
            return step_blackjack(sess, "hit", strip_noise_for_chitchat(text, ["要牌", "再来一张", "拿牌", "继续要", "hit"]))
        hint = "玩家没有要牌/停牌，正在牌桌上和你聊天。"
    elif gtype == "1a2b":
        m_num = re.search(r"(?:^|[^\d])(\d{4})(?:[^\d]|$)", text)
        if m_num:
            return step_1a2b(sess, m_num.group(1), strip_noise_for_chitchat(text, [m_num.group(1)]))
        hint = "玩家正在思考并和你交流，尚未给出 4 位猜测数字。"
    else:
        return {"handled": False, "action_type": "NONE"}

    return {
        "ok": True, "handled": True, "action_type": "CHAT_ONLY", "image_path": None,
        "reply_text": "", "should_trigger_llm_hook": True,
        "board_summary_for_llm": _summary_for_session(sess, hint),
        "game_over": False,
    }


async def play_board_game_impl(
    action: str,
    chat_id: str,
    user_id: str,
    user_name: str = "",
    game_type: str = "gomoku",
    difficulty: str = "normal",
    move: str = "",
    send_image_cb=None,
) -> str:
    """统一棋牌小游戏 MCP 后端入口 (start | move | undo | resign | status)"""
    act = (action or "status").strip().lower()
    cid, uid = normalize_chat_id(chat_id), str(user_id or "").strip()
    uname = user_name or uid

    if not cid or not uid:
        return json.dumps({"ok": False, "error": "缺少 chat_id 或 user_id"}, ensure_ascii=False)

    if act == "start":
        res = start_game(cid, uid, uname, game_type, difficulty, is_private=cid.startswith("private_"))
    elif act in ("move", "play"):
        if not get_active_game(cid):
            return json.dumps({"ok": False, "error": "当前没有进行中的对局，请先使用 action='start' 开局。"}, ensure_ascii=False)
        res = analyze_and_execute_game_turn(cid, uid, uname, move)
        if res.get("action_type") == "CHAT_ONLY":
            return json.dumps({"ok": False, "error": f"未能解析出合法走子指令：{move}"}, ensure_ascii=False)
    elif act == "undo":
        sess = get_active_game(cid)
        if not sess:
            return json.dumps({"ok": False, "error": "当前没有进行中的对局。"}, ensure_ascii=False)
        res = _handle_undo(sess)
    elif act in ("resign", "quit", "end"):
        sess = get_active_game(cid)
        if not sess:
            return json.dumps({"ok": False, "error": "当前没有进行中的对局。"}, ensure_ascii=False)
        res = _handle_resign(sess)
    elif act == "status":
        sess = get_active_game(cid)
        if not sess:
            return json.dumps({"ok": True, "active": False, "message": "当前没有任何进行中的小游戏。"}, ensure_ascii=False)
        return json.dumps({"ok": True, "active": True, "game_type": sess["game_type"], "summary": _summary_for_session(sess, "对局进行中")}, ensure_ascii=False)
    else:
        return json.dumps({"ok": False, "error": f"未知 action: {action}"}, ensure_ascii=False)

    img_path, img_sent = res.get("image_path"), False
    if img_path and send_image_cb is not None:
        try:
            await send_image_cb(img_path)
            img_sent = True
        except Exception as e:
            print(f"[BoardGames] MCP 发送棋盘图片失败: {e}")

    log_mcp_tool(f"play_board_game({act})", f"chat={cid}, game={res.get('game_type', '')}, img_sent={img_sent}")
    return json.dumps({
        "ok": bool(res.get("ok", True)),
        "action_type": res.get("action_type"),
        "image_sent": img_sent,
        "image_path": img_path,
        "suggested_reply": res.get("reply_text"),
        "board_summary": res.get("board_summary_for_llm"),
        "game_over": bool(res.get("game_over", False)),
    }, ensure_ascii=False)
