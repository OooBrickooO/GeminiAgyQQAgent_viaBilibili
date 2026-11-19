"""
toys/games/othello_engine.py — 翻转黑白棋 (8x8) 位置/行动力/终局 Alpha-Beta 引擎
"""
import copy
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from toys.board_renderer import render_othello_board
from toys.games.session_store import (
    DIFF_NAMES_CN,
    OTHELLO_COLS,
    clear_active_game,
    get_mood_playstyle,
    push_history,
    save_active_game,
    settle_match_outcome,
    situational_line,
)

_OTHELLO_WEIGHTS = [
    [120, -22,  18,  10,  10,  18, -22, 120],
    [-22, -45,  -4,  -2,  -2,  -4, -45, -22],
    [ 18,  -4,   8,   4,   4,   8,  -4,  18],
    [ 10,  -2,   4,   1,   1,   4,  -2,  10],
    [ 10,  -2,   4,   1,   1,   4,  -2,  10],
    [ 18,  -4,   8,   4,   4,   8,  -4,  18],
    [-22, -45,  -4,  -2,  -2,  -4, -45, -22],
    [120, -22,  18,  10,  10,  18, -22, 120],
]


def init_othello_board() -> List[List[int]]:
    b = [[0] * 8 for _ in range(8)]
    b[3][3], b[3][4], b[4][3], b[4][4] = 2, 1, 1, 2
    return b


def fmt_othello(r: int, c: int) -> str:
    return f"{OTHELLO_COLS[c]}{r + 1}"


def parse_othello_coord(text: str) -> Optional[Tuple[int, int]]:
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s:
        return None
    m = re.search(r"(?:^|[^A-Za-z0-9])([A-Ha-h])[\s\-_,，:：]*([1-8])(?:[^A-Za-z0-9]|$)", s)
    if m:
        return (int(m.group(2)) - 1, OTHELLO_COLS.index(m.group(1).upper()))
    m2 = re.match(r"^\s*([1-8])[\s\-_,，:：]*([A-Ha-h])\s*$", s)
    if m2:
        return (int(m2.group(1)) - 1, OTHELLO_COLS.index(m2.group(2).upper()))
    return None


def _othello_flips(board: List[List[int]], r: int, c: int, player: int) -> List[Tuple[int, int]]:
    if not (0 <= r < 8 and 0 <= c < 8) or board[r][c] != 0:
        return []
    opp = 1 if player == 2 else 2
    all_flips: List[Tuple[int, int]] = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc, ray = r + dr, c + dc, []
            while 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == opp:
                ray.append((nr, nc))
                nr += dr
                nc += dc
            if ray and 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == player:
                all_flips.extend(ray)
    return all_flips


def othello_valid_moves(board: List[List[int]], player: int) -> List[Tuple[int, int, List[Tuple[int, int]]]]:
    moves = []
    for r in range(8):
        for c in range(8):
            flips = _othello_flips(board, r, c, player)
            if flips:
                moves.append((r, c, flips))
    return moves


def _apply_othello_move(board: List[List[int]], r: int, c: int, player: int, flips: List[Tuple[int, int]]) -> None:
    board[r][c] = player
    for fr, fc in flips:
        board[fr][fc] = player


def _othello_counts(board: List[List[int]]) -> Tuple[int, int, int]:
    black = sum(1 for r in range(8) for c in range(8) if board[r][c] == 1)
    white = sum(1 for r in range(8) for c in range(8) if board[r][c] == 2)
    return black, white, 64 - black - white


def _eval_othello_board(board: List[List[int]], w_atk: float, w_def: float) -> float:
    black, white, empty = _othello_counts(board)
    if empty == 0 or (not othello_valid_moves(board, 1) and not othello_valid_moves(board, 2)):
        diff = white - black
        return (100_000.0 + diff) if diff > 0 else ((-100_000.0 + diff) if diff < 0 else 0.0)

    corner_adj = {
        (0, 1): (0, 0), (1, 0): (0, 0), (1, 1): (0, 0),
        (0, 6): (0, 7), (1, 7): (0, 7), (1, 6): (0, 7),
        (6, 0): (7, 0), (7, 1): (7, 0), (6, 1): (7, 0),
        (6, 7): (7, 7), (7, 6): (7, 7), (6, 6): (7, 7),
    }
    pos_score = 0.0
    for r in range(8):
        for c in range(8):
            v = board[r][c]
            if v == 0:
                continue
            w = _OTHELLO_WEIGHTS[r][c]
            cr = corner_adj.get((r, c))
            if cr and board[cr[0]][cr[1]] != 0:
                w = 8
            pos_score += w if v == 2 else -w

    mobility = (len(othello_valid_moves(board, 2)) * w_atk - len(othello_valid_moves(board, 1)) * w_def) * 14.0
    piece_diff = (white - black) * (12.0 if empty <= 12 else 1.5 * w_atk)
    return pos_score + mobility + piece_diff


def _othello_alphabeta(
    board: List[List[int]], depth: int, alpha: float, beta: float, player: int, w_atk: float, w_def: float
) -> float:
    moves = othello_valid_moves(board, player)
    opp = 1 if player == 2 else 2
    if depth <= 0:
        return _eval_othello_board(board, w_atk, w_def)
    if not moves:
        if not othello_valid_moves(board, opp):
            return _eval_othello_board(board, w_atk, w_def)
        return _othello_alphabeta(board, depth - 1, alpha, beta, opp, w_atk, w_def)

    moves.sort(key=lambda m: _OTHELLO_WEIGHTS[m[0]][m[1]], reverse=True)
    val = -1e12 if player == 2 else 1e12
    for r, c, flips in moves:
        nb = copy.deepcopy(board)
        _apply_othello_move(nb, r, c, player, flips)
        sc = _othello_alphabeta(nb, depth - 1, alpha, beta, opp, w_atk, w_def)
        if player == 2:
            val = max(val, sc)
            alpha = max(alpha, val)
        else:
            val = min(val, sc)
            beta = min(beta, val)
        if alpha >= beta:
            break
    return val


def choose_othello_bot_move(
    board: List[List[int]], difficulty: str, w_atk: float, w_def: float
) -> Tuple[int, int, List[Tuple[int, int]]]:
    moves = othello_valid_moves(board, 2)
    if difficulty == "easy":
        return max(moves, key=lambda m: len(m[2]) * 6 + (_OTHELLO_WEIGHTS[m[0]][m[1]] * 0.4))

    _b, _w, empty = _othello_counts(board)
    depth = 6 if (difficulty == "hard" and empty <= 10) else (4 if difficulty == "hard" else 2)
    moves.sort(key=lambda m: _OTHELLO_WEIGHTS[m[0]][m[1]], reverse=True)
    best_move, best_val = moves[0], -1e12
    for r, c, flips in moves:
        nb = copy.deepcopy(board)
        _apply_othello_move(nb, r, c, 2, flips)
        sc = _othello_alphabeta(nb, depth - 1, -1e12, 1e12, 1, w_atk, w_def)
        if sc > best_val:
            best_val, best_move = sc, (r, c, flips)
    return best_move


def othello_board_summary(sess: dict, event_desc: str) -> str:
    st = sess["state"]
    board = st["board"]
    b_cnt, w_cnt, _ = _othello_counts(board)
    valid = [fmt_othello(r, c) for r, c, _ in othello_valid_moves(board, 1)]
    lu, lb = st.get("last_user_move"), st.get("last_bot_move")
    return (
        f"【当前桌游：翻转黑白棋（{DIFF_NAMES_CN.get(sess['difficulty'], '认真')}｜当前弥酱棋风：{get_mood_playstyle()['style']}）】\n"
        f"- 当前子数：黑子({sess['player_name']}) {b_cnt} : {w_cnt} 白子(弥酱)\n"
        f"- 第 {sess['turn_count']} 回合；玩家上步：{fmt_othello(lu[0], lu[1]) if lu else '无'} ｜ 弥酱上步：{fmt_othello(lb[0], lb[1]) if lb else '无'}\n"
        f"- 玩家当前合法落子点({len(valid)})：{', '.join(valid[:12]) or '无合法步'}\n"
        f"- 战况说明：{event_desc}"
    )


def step_othello(sess: dict, move_rc: Tuple[int, int], has_chitchat: bool) -> Dict[str, Any]:
    r, c = move_rc
    st = sess["state"]
    board = st["board"]
    user_flips = _othello_flips(board, r, c, 1)
    if not user_flips:
        valid_str = ", ".join(fmt_othello(vr, vc) for vr, vc, _ in othello_valid_moves(board, 1))
        return {
            "ok": False, "handled": True, "action_type": "INVALID_MOVE", "image_path": None,
            "reply_text": f"在 {fmt_othello(r, c)} 落子夹不到白棋哦~ 目前可落子点：{valid_str or '无'}",
            "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
        }

    push_history(sess)
    _apply_othello_move(board, r, c, 1, user_flips)
    st["last_user_move"] = [r, c]
    all_flipped = list(user_flips)
    sess["turn_count"] = int(sess.get("turn_count", 0)) + 1
    sess["updated_at"] = time.time()

    corners = {(0, 0), (0, 7), (7, 0), (7, 7)}
    playstyle = get_mood_playstyle()
    bot_notes, tag = [], "NORMAL"
    is_highlight = (r, c) in corners or len(user_flips) >= 5
    last_bot_rc = None

    while True:
        if not othello_valid_moves(board, 2):
            if not othello_valid_moves(board, 1):
                break
            tag = "BOT_PASS"
            bot_notes.append("弥酱无步可走被动Pass")
            break
        br, bc, b_flips = choose_othello_bot_move(board, sess["difficulty"], playstyle["w_atk"], playstyle["w_def"])
        _apply_othello_move(board, br, bc, 2, b_flips)
        last_bot_rc = [br, bc]
        st["last_bot_move"] = last_bot_rc
        all_flipped.extend(b_flips)
        bot_notes.append(f"弥酱下在{fmt_othello(br, bc)}(翻{len(b_flips)}子)")
        if (br, bc) in corners:
            tag, is_highlight = "CORNER", True
        elif len(b_flips) >= 5:
            tag, is_highlight = "MASSIVE_FLIP", True
        if othello_valid_moves(board, 1) or not othello_valid_moves(board, 2):
            break
        tag, is_highlight = "FORCED_PASS", True

    st["flipped_cells"] = [list(pt) for pt in all_flipped]
    b_cnt, w_cnt, _ = _othello_counts(board)
    next_user_valid = [(vr, vc) for vr, vc, _ in othello_valid_moves(board, 1)]
    bot_pt = tuple(last_bot_rc) if last_bot_rc else None

    if not next_user_valid and not othello_valid_moves(board, 2):
        img = render_othello_board(board, valid_moves=[], last_user_move=(r, c), last_bot_move=bot_pt, flipped_cells=all_flipped)
        outcome, desc = (
            ("user_win", f"终局比分 黑 {b_cnt} : 白 {w_cnt}，玩家获胜！") if b_cnt > w_cnt
            else ("bot_win", f"终局比分 黑 {b_cnt} : 白 {w_cnt}，弥酱翻盘/保胜！") if w_cnt > b_cnt
            else ("draw", f"终局比分 {b_cnt} : {w_cnt} 平分秋色！")
        )
        settle_match_outcome(sess, outcome, desc, bool(sess.get("is_private")))
        summary = othello_board_summary(sess, desc)
        clear_active_game(sess["chat_id"])
        return {
            "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
            "reply_text": desc, "should_trigger_llm_hook": True,
            "board_summary_for_llm": summary, "game_over": True,
        }

    save_active_game(sess)
    img = render_othello_board(board, valid_moves=next_user_valid, last_user_move=(r, c), last_bot_move=bot_pt, flipped_cells=all_flipped)
    bc_coord = fmt_othello(last_bot_rc[0], last_bot_rc[1]) if last_bot_rc else "Pass"
    desc = f"玩家落子 {fmt_othello(r, c)}(翻{len(user_flips)}子)；" + ("；".join(bot_notes) or "双方交替")
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
        "reply_text": situational_line("othello", tag, {"bot_coord": bc_coord, "flipped_cnt": len(all_flipped), "b_cnt": b_cnt, "w_cnt": w_cnt}),
        "should_trigger_llm_hook": bool(is_highlight or has_chitchat or (sess["turn_count"] % 5 == 0)),
        "board_summary_for_llm": othello_board_summary(sess, desc),
        "game_over": False,
    }
