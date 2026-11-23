"""
toys/games/xiangqi_engine.py — 中国象棋 (9x10) 纯 Python 互动风完整规则与 AI 引擎 (4A)

支持：
- 完整标准规则：九宫限制、将帅照面禁手、蹩马腿、塞象眼、炮隔山打牛、兵卒过河前后、送将禁手、将死/困毙判负；
- 双轨走子输入：
  1) 标准中文纵线记谱（炮二平五 / 马八进七 / 车一进二 / 前马进七 / 炮2平5 / 马8进7）；
  2) UCCI 网格坐标法（A~I 列、0~9 行，如 B2E2 / H2 E2 / h2到e2）；
- Alpha-Beta + 静态吃子搜索 + MVV-LVA + 象棋位置价值表 (PST) + 弥酱 7D 心情棋风联动；
- 0-Token 快速回应 + 亮点战术事件钩子（将军/吃车马炮/过河/每6回合/终局）。
"""
import random
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from toys.games.session_store import (
    DIFF_NAMES_CN,
    clear_active_game,
    get_mood_playstyle,
    push_history,
    save_active_game,
    settle_match_outcome,
    situational_line,
)

XQ_COLS = "ABCDEFGHI"
PIECE_VALUES = {"K": 20000, "R": 900, "C": 460, "H": 420, "E": 200, "A": 200, "P": 100}
PIECE_DISPLAY = {
    "K": "帅", "A": "仕", "E": "相", "H": "马", "R": "车", "C": "炮", "P": "兵",
    "k": "将", "a": "士", "e": "象", "h": "马", "r": "车", "c": "炮", "p": "卒",
}
RED_FILE_CN = ["九", "八", "七", "六", "五", "四", "三", "二", "一"]
CN_TO_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
}
NUM_TO_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}

# 象棋精简位置价值表（红方视角 r=0 顶黑底线, r=9 底红底线；黑方上下翻转）
_PST_P = [
    [9, 9, 9, 11, 13, 11, 9, 9, 9],
    [19, 24, 34, 42, 44, 42, 34, 24, 19],
    [19, 24, 32, 37, 37, 37, 32, 24, 19],
    [19, 23, 27, 29, 30, 29, 27, 23, 19],
    [14, 18, 20, 27, 29, 27, 20, 18, 14],
    [7, 0, 13, 0, 16, 0, 13, 0, 7],
    [7, 0, 7, 0, 15, 0, 7, 0, 7],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
]
_PST_H = [
    [4, 8, 16, 12, 4, 12, 16, 8, 4],
    [4, 10, 28, 16, 8, 16, 28, 10, 4],
    [12, 14, 16, 20, 18, 20, 16, 14, 12],
    [8, 24, 18, 24, 20, 24, 18, 24, 8],
    [6, 16, 14, 18, 16, 18, 14, 16, 6],
    [4, 12, 16, 14, 12, 14, 16, 12, 4],
    [2, 6, 8, 6, 10, 6, 8, 6, 2],
    [4, 2, 8, 8, 4, 8, 8, 2, 4],
    [0, 2, 4, 4, -2, 4, 4, 2, 0],
    [0, -4, 0, 0, 0, 0, 0, -4, 0],
]
_PST_C = [
    [6, 4, 0, -10, -12, -10, 0, 4, 6],
    [2, 2, 0, -4, -14, -4, 0, 2, 2],
    [2, 2, 0, -10, -8, -10, 0, 2, 2],
    [0, 0, -2, 4, 10, 4, -2, 0, 0],
    [0, 0, 0, 2, 8, 2, 0, 0, 0],
    [-2, 0, 4, 2, 6, 2, 4, 0, -2],
    [0, 0, 0, 2, 4, 2, 0, 0, 0],
    [4, 0, 8, 6, 12, 6, 8, 0, 4],
    [0, 2, 4, 6, 6, 6, 4, 2, 0],
    [0, 0, 2, 6, 6, 6, 2, 0, 0],
]
_PST_R = [
    [14, 14, 12, 18, 16, 18, 12, 14, 14],
    [16, 20, 18, 24, 26, 24, 18, 20, 16],
    [12, 12, 12, 18, 18, 18, 12, 12, 12],
    [12, 18, 16, 22, 22, 22, 16, 18, 12],
    [12, 14, 12, 18, 18, 18, 12, 14, 12],
    [12, 16, 14, 20, 20, 20, 14, 16, 12],
    [6, 10, 8, 14, 14, 14, 8, 10, 6],
    [4, 8, 6, 14, 12, 14, 6, 8, 4],
    [8, 4, 8, 16, 8, 16, 8, 4, 8],
    [-2, 10, 6, 14, 12, 14, 6, 10, -2],
]


def init_xiangqi_board() -> List[List[str]]:
    return [
        ["r", "h", "e", "a", "k", "a", "e", "h", "r"],
        ["", "", "", "", "", "", "", "", ""],
        ["", "c", "", "", "", "", "", "c", ""],
        ["p", "", "p", "", "p", "", "p", "", "p"],
        ["", "", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["P", "", "P", "", "P", "", "P", "", "P"],
        ["", "C", "", "", "", "", "", "C", ""],
        ["", "", "", "", "", "", "", "", ""],
        ["R", "H", "E", "A", "K", "A", "E", "H", "R"],
    ]


def init_xiangqi_state() -> Dict[str, Any]:
    return {
        "board": init_xiangqi_board(),
        "last_user_move": None,
        "last_bot_move": None,
        "in_check_side": "",
        "captured_by_user": [],
        "captured_by_bot": [],
    }


def xq_side(p: str) -> str:
    if not p:
        return ""
    return "r" if p.isupper() else "b"


def xq_sq_to_ucci(r: int, c: int) -> str:
    return f"{XQ_COLS[c]}{9 - r}"


def xq_ucci_to_sq(tok: str) -> Optional[Tuple[int, int]]:
    s = (tok or "").strip().upper()
    if len(s) != 2 or s[0] not in XQ_COLS or s[1] not in "0123456789":
        return None
    return (9 - int(s[1]), XQ_COLS.index(s[0]))


def fmt_xq_ucci(mv: Tuple[int, int, int, int]) -> str:
    return f"{xq_sq_to_ucci(mv[0], mv[1])}{xq_sq_to_ucci(mv[2], mv[3])}"


def fmt_xq_cn(board: List[List[str]], mv: Tuple[int, int, int, int]) -> str:
    """将一步走法格式化为标准中文纵线记谱（如 炮二平五 / 马8进7）"""
    r1, c1, r2, c2 = mv
    p = board[r1][c1]
    if not p:
        return fmt_xq_ucci(mv)
    is_red = p.isupper()
    p_name = PIECE_DISPLAY.get(p, p)
    f1 = (9 - c1) if is_red else (c1 + 1)
    f2 = (9 - c2) if is_red else (c2 + 1)
    f1_str = NUM_TO_CN[f1] if is_red else str(f1)

    if r1 == r2:
        act = "平"
        t_str = NUM_TO_CN[f2] if is_red else str(f2)
    else:
        forward = (r2 < r1) if is_red else (r2 > r1)
        act = "进" if forward else "退"
        if p.upper() in ("K", "R", "C", "P"):
            steps = abs(r2 - r1)
            t_str = NUM_TO_CN.get(steps, str(steps)) if is_red else str(steps)
        else:
            t_str = NUM_TO_CN[f2] if is_red else str(f2)
    return f"{p_name}{f1_str}{act}{t_str}"


def find_general(board: List[List[str]], side: str) -> Optional[Tuple[int, int]]:
    target = "K" if side == "r" else "k"
    r_range = range(7, 10) if side == "r" else range(0, 3)
    for r in r_range:
        for c in range(3, 6):
            if board[r][c] == target:
                return (r, c)
    return None


def generals_facing(board: List[List[str]]) -> bool:
    rk = find_general(board, "r")
    bk = find_general(board, "b")
    if not rk or not bk or rk[1] != bk[1]:
        return False
    col = rk[1]
    for r in range(bk[0] + 1, rk[0]):
        if board[r][col]:
            return False
    return True


def _generate_pseudo_xq_moves(board: List[List[str]], side: str) -> List[Tuple[int, int, int, int]]:
    moves: List[Tuple[int, int, int, int]] = []
    opp = "b" if side == "r" else "r"

    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if not p or xq_side(p) != side:
                continue
            pu = p.upper()
            if pu == "K":
                r_lo, r_hi = (7, 9) if side == "r" else (0, 2)
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = r + dr, c + dc
                    if r_lo <= nr <= r_hi and 3 <= nc <= 5 and xq_side(board[nr][nc]) != side:
                        moves.append((r, c, nr, nc))
            elif pu == "A":
                r_lo, r_hi = (7, 9) if side == "r" else (0, 2)
                for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                    nr, nc = r + dr, c + dc
                    if r_lo <= nr <= r_hi and 3 <= nc <= 5 and xq_side(board[nr][nc]) != side:
                        moves.append((r, c, nr, nc))
            elif pu == "E":
                r_lo, r_hi = (5, 9) if side == "r" else (0, 4)
                for dr, dc in ((-2, -2), (-2, 2), (2, -2), (2, 2)):
                    nr, nc = r + dr, c + dc
                    er, ec = r + dr // 2, c + dc // 2
                    if r_lo <= nr <= r_hi and 0 <= nc <= 8 and not board[er][ec] and xq_side(board[nr][nc]) != side:
                        moves.append((r, c, nr, nc))
            elif pu == "H":
                for dr, dc, lr, lc in (
                    (-2, -1, -1, 0), (-2, 1, -1, 0),
                    (2, -1, 1, 0), (2, 1, 1, 0),
                    (-1, -2, 0, -1), (1, -2, 0, -1),
                    (-1, 2, 0, 1), (1, 2, 0, 1),
                ):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < 10 and 0 <= nc < 9 and not board[r + lr][c + lc] and xq_side(board[nr][nc]) != side:
                        moves.append((r, c, nr, nc))
            elif pu == "R":
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = r + dr, c + dc
                    while 0 <= nr < 10 and 0 <= nc < 9:
                        t = board[nr][nc]
                        if not t:
                            moves.append((r, c, nr, nc))
                        else:
                            if xq_side(t) == opp:
                                moves.append((r, c, nr, nc))
                            break
                        nr += dr
                        nc += dc
            elif pu == "C":
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = r + dr, c + dc
                    jumped = False
                    while 0 <= nr < 10 and 0 <= nc < 9:
                        t = board[nr][nc]
                        if not jumped:
                            if not t:
                                moves.append((r, c, nr, nc))
                            else:
                                jumped = True
                        else:
                            if t:
                                if xq_side(t) == opp:
                                    moves.append((r, c, nr, nc))
                                break
                        nr += dr
                        nc += dc
            elif pu == "P":
                fwd = -1 if side == "r" else 1
                crossed = (r <= 4) if side == "r" else (r >= 5)
                dirs = [(fwd, 0)] + ([(0, -1), (0, 1)] if crossed else [])
                for dr, dc in dirs:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < 10 and 0 <= nc < 9 and xq_side(board[nr][nc]) != side:
                        moves.append((r, c, nr, nc))
    return moves


def is_xq_in_check(board: List[List[str]], side: str) -> bool:
    kp = find_general(board, side)
    if kp is None:
        return True
    if generals_facing(board):
        return True
    opp = "b" if side == "r" else "r"
    kr, kc = kp
    for mv in _generate_pseudo_xq_moves(board, opp):
        if mv[2] == kr and mv[3] == kc:
            return True
    return False


def generate_legal_xq_moves(board: List[List[str]], side: str) -> List[Tuple[int, int, int, int]]:
    legal = []
    for mv in _generate_pseudo_xq_moves(board, side):
        r1, c1, r2, c2 = mv
        cap = board[r2][c2]
        board[r2][c2] = board[r1][c1]
        board[r1][c1] = ""
        if not is_xq_in_check(board, side):
            legal.append(mv)
        board[r1][c1] = board[r2][c2]
        board[r2][c2] = cap
    return legal


_CN_PIECE_MAP = {
    "帅": "K", "帥": "K", "将": "K", "將": "K",
    "仕": "A", "士": "A",
    "相": "E", "象": "E",
    "马": "H", "馬": "H",
    "车": "R", "車": "R", "俥": "R",
    "炮": "C", "砲": "C", "包": "C",
    "兵": "P", "卒": "P",
}


def parse_xiangqi_move(board: List[List[str]], text: str, side: str = "r") -> Tuple[Optional[Tuple[int, int, int, int]], str]:
    """
    中国象棋双轨输入解析器：
    1) 标准中文纵线记谱：炮二平五 / 马8进7 / 车一进二 / 兵七进一 / 前马进七 / 后炮平五
    2) UCCI 网格坐标法：B2E2 / B2 E2 / H2到E2
    返回: (move_tuple_or_None, matched_token)
    """
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s:
        return None, ""
    legal = generate_legal_xq_moves(board, side)
    if not legal:
        return None, ""

    # 1. 标准中文纵线记谱（含前/后/中消歧）
    m_cn = re.search(
        r"([前后中])?([帅帥将將仕士相象马馬车車俥炮砲包兵卒])([一二三四五六七八九1-9])?\s*([进退平進])\s*([一二三四五六七八九1-9])",
        s,
    )
    if m_cn:
        pos_mod = m_cn.group(1) or ""
        p_char = m_cn.group(2)
        f1_raw = m_cn.group(3) or ""
        act = "进" if m_cn.group(4) in ("进", "進") else m_cn.group(4)
        t_raw = m_cn.group(5)
        target_pu = _CN_PIECE_MAP.get(p_char, "")
        t_num = CN_TO_NUM.get(t_raw, 0)
        f1_num = CN_TO_NUM.get(f1_raw, 0) if f1_raw else 0

        if target_pu and t_num:
            cands = []
            for mv in legal:
                r1, c1, r2, c2 = mv
                p = board[r1][c1]
                if p.upper() != target_pu:
                    continue
                is_red = (side == "r")
                mv_f1 = (9 - c1) if is_red else (c1 + 1)
                mv_f2 = (9 - c2) if is_red else (c2 + 1)
                if f1_num and mv_f1 != f1_num:
                    continue
                if act == "平":
                    if r1 != r2 or mv_f2 != t_num:
                        continue
                else:
                    is_fwd = (r2 < r1) if is_red else (r2 > r1)
                    if (act == "进" and not is_fwd) or (act == "退" and is_fwd) or r1 == r2:
                        continue
                    if target_pu in ("K", "R", "C", "P"):
                        if abs(r2 - r1) != t_num:
                            continue
                    else:
                        if mv_f2 != t_num:
                            continue
                cands.append(mv)

            if cands:
                if len(cands) > 1 and pos_mod:
                    # 红方“前”为 r 更小（靠上），黑方“前”为 r 更大（靠下）
                    cands.sort(key=lambda x: x[0], reverse=(side == "b"))
                    if pos_mod == "前":
                        return cands[0], m_cn.group(0)
                    elif pos_mod == "后":
                        return cands[-1], m_cn.group(0)
                    elif pos_mod == "中" and len(cands) >= 3:
                        return cands[len(cands) // 2], m_cn.group(0)
                return cands[0], m_cn.group(0)
            return (-1, -1, -1, -1), m_cn.group(0)

    # 2. UCCI 网格坐标法: B2E2 / B2 E2 / H2到E2
    m_ucci = re.search(
        r"(?:^|[^A-Za-z0-9])([A-Ia-i][0-9])[\s\-_,，:：>到至]*([A-Ia-i][0-9])(?:[^A-Za-z0-9]|$)",
        s,
    )
    if m_ucci:
        sq1 = xq_ucci_to_sq(m_ucci.group(1))
        sq2 = xq_ucci_to_sq(m_ucci.group(2))
        if sq1 and sq2:
            for mv in legal:
                if mv[0] == sq1[0] and mv[1] == sq1[1] and mv[2] == sq2[0] and mv[3] == sq2[1]:
                    return mv, m_ucci.group(0).strip()
            return (-1, -1, -1, -1), m_ucci.group(0).strip()

    return None, ""


def _eval_xq_board(board: List[List[str]], w_atk: float = 1.0, w_def: float = 1.0) -> int:
    """黑方（弥酱）视角评估：正数黑优，负数红优"""
    score = 0
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if not p:
                continue
            pu = p.upper()
            base = PIECE_VALUES[pu]
            is_black = p.islower()
            pr = (9 - r) if is_black else r
            pst = 0
            if pu == "P":
                pst = _PST_P[pr][c]
            elif pu == "H":
                pst = _PST_H[pr][c]
            elif pu == "C":
                pst = _PST_C[pr][c]
            elif pu == "R":
                pst = _PST_R[pr][c]
            elif pu in ("A", "E"):
                pst = int(12 * w_def) if is_black else 12

            if is_black:
                if pu in ("R", "C", "H", "P") and r >= 5:
                    pst = int(pst * w_atk)
                score += base + pst
            else:
                score -= base + pst
    return score


def _order_xq_moves(board: List[List[str]], moves: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    def _k(mv):
        cap = board[mv[2]][mv[3]]
        att = board[mv[0]][mv[1]]
        if cap:
            return 10 * PIECE_VALUES[cap.upper()] - PIECE_VALUES[att.upper()]
        return 0
    return sorted(moves, key=_k, reverse=True)


def _xq_quiescence(board, alpha: int, beta: int, maximizing_black: bool, qdepth: int, w_atk: float, w_def: float, deadline: float) -> int:
    stand = _eval_xq_board(board, w_atk, w_def)
    if qdepth <= 0 or time.time() > deadline:
        return stand
    if maximizing_black:
        if stand >= beta:
            return beta
        if stand > alpha:
            alpha = stand
        side = "b"
    else:
        if stand <= alpha:
            return alpha
        if stand < beta:
            beta = stand
        side = "r"

    caps = [mv for mv in _generate_pseudo_xq_moves(board, side) if board[mv[2]][mv[3]]]
    for mv in _order_xq_moves(board, caps)[:8]:
        r1, c1, r2, c2 = mv
        cap = board[r2][c2]
        if cap.upper() == "K":
            return 99999 if maximizing_black else -99999
        board[r2][c2] = board[r1][c1]
        board[r1][c1] = ""
        val = _xq_quiescence(board, alpha, beta, not maximizing_black, qdepth - 1, w_atk, w_def, deadline)
        board[r1][c1] = board[r2][c2]
        board[r2][c2] = cap
        if maximizing_black:
            if val >= beta:
                return beta
            if val > alpha:
                alpha = val
        else:
            if val <= alpha:
                return alpha
            if val < beta:
                beta = val
    return alpha if maximizing_black else beta


def _alphabeta_xq(board, depth: int, alpha: int, beta: int, maximizing_black: bool, w_atk: float, w_def: float, deadline: float) -> int:
    side = "b" if maximizing_black else "r"
    legal = generate_legal_xq_moves(board, side)
    if not legal:
        return (-99999 - depth) if maximizing_black else (99999 + depth)
    if depth <= 0 or time.time() > deadline:
        return _xq_quiescence(board, alpha, beta, maximizing_black, 2, w_atk, w_def, deadline)

    ordered = _order_xq_moves(board, legal)
    if maximizing_black:
        best = -10**9
        for mv in ordered:
            r1, c1, r2, c2 = mv
            cap = board[r2][c2]
            board[r2][c2] = board[r1][c1]
            board[r1][c1] = ""
            val = _alphabeta_xq(board, depth - 1, alpha, beta, False, w_atk, w_def, deadline)
            board[r1][c1] = board[r2][c2]
            board[r2][c2] = cap
            if val > best:
                best = val
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        return best
    else:
        best = 10**9
        for mv in ordered:
            r1, c1, r2, c2 = mv
            cap = board[r2][c2]
            board[r2][c2] = board[r1][c1]
            board[r1][c1] = ""
            val = _alphabeta_xq(board, depth - 1, alpha, beta, True, w_atk, w_def, deadline)
            board[r1][c1] = board[r2][c2]
            board[r2][c2] = cap
            if val < best:
                best = val
            if best < beta:
                beta = best
            if alpha >= beta:
                break
        return best


def choose_xiangqi_bot_move(state: Dict[str, Any], difficulty: str = "normal") -> Optional[Tuple[int, int, int, int]]:
    board = [row[:] for row in state["board"]]
    legal = generate_legal_xq_moves(board, "b")
    if not legal:
        return None

    ps = get_mood_playstyle()
    w_atk, w_def = ps["w_atk"], ps["w_def"]
    depth = 1 if difficulty == "easy" else (3 if difficulty == "hard" else 2)
    budget = 0.6 if difficulty == "easy" else (1.3 if difficulty == "hard" else 0.9)
    deadline = time.time() + budget

    scored = []
    alpha = -10**9
    for mv in _order_xq_moves(board, legal):
        r1, c1, r2, c2 = mv
        cap = board[r2][c2]
        board[r2][c2] = board[r1][c1]
        board[r1][c1] = ""
        val = _alphabeta_xq(board, depth - 1, alpha, 10**9, False, w_atk, w_def, deadline)
        board[r1][c1] = board[r2][c2]
        board[r2][c2] = cap
        if difficulty == "easy":
            val += random.randint(-95, 95)
        elif difficulty == "normal":
            val += random.randint(-15, 15)
        scored.append((val, mv))
        if val > alpha:
            alpha = val

    scored.sort(key=lambda x: x[0], reverse=True)
    if difficulty == "easy" and len(scored) >= 3:
        return random.choice(scored[:3])[1]
    return scored[0][1]


def xiangqi_board_summary(sess: dict, event_desc: str) -> str:
    st = sess["state"]
    style = get_mood_playstyle()["style"]
    u_cap = "".join(PIECE_DISPLAY.get(p, p) for p in st.get("captured_by_user", [])[-6:]) or "无"
    b_cap = "".join(PIECE_DISPLAY.get(p, p) for p in st.get("captured_by_bot", [])[-6:]) or "无"
    return (
        f"【中国象棋实时战况 | 难度: {DIFF_NAMES_CN.get(sess['difficulty'], '正常')} | 棋风: {style}】\n"
        f"- 当前回合: 第 {sess['turn_count']} 轮 | 红方(玩家)已吃子: [{u_cap}] | 黑方(弥酱)已吃子: [{b_cap}]\n"
        f"- 局势事件: {event_desc}"
    )


def step_xiangqi(sess: dict, raw_text: str, has_chitchat: bool = False) -> Dict[str, Any]:
    from toys.board_renderer import render_xiangqi_board

    st = sess["state"]
    board = st["board"]

    mv, matched_tok = parse_xiangqi_move(board, raw_text, side="r")
    if mv is None:
        return {
            "ok": True, "handled": True, "action_type": "CHAT_ONLY", "image_path": None,
            "reply_text": "", "should_trigger_llm_hook": True,
            "board_summary_for_llm": xiangqi_board_summary(sess, "玩家没有报出新走法，正在就当前中国象棋棋局与你交流。"),
            "game_over": False,
        }
    if mv[0] == -1:
        return {
            "ok": False, "handled": True, "action_type": "INVALID_MOVE", "image_path": None,
            "reply_text": f"这步「{matched_tok}」不符合象棋规则（可能蹩马腿/塞象眼/将帅照面或会导致红帅被将军）哦，换一步试试~",
            "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
        }

    push_history(sess)
    u_cn = fmt_xq_cn(board, mv)
    u_ucci = fmt_xq_ucci(mv)
    r1, c1, r2, c2 = mv
    u_cap = board[r2][c2]
    board[r2][c2] = board[r1][c1]
    board[r1][c1] = ""
    st["last_user_move"] = [[r1, c1], [r2, c2]]
    if u_cap:
        st.setdefault("captured_by_user", []).append(u_cap)
    sess["turn_count"] = int(sess.get("turn_count", 0)) + 1
    sess["updated_at"] = time.time()

    # 检查黑方是否被将死/困毙
    bot_legal = generate_legal_xq_moves(board, "b")
    bot_in_chk = is_xq_in_check(board, "b")
    st["in_check_side"] = "b" if bot_in_chk else ""

    if not bot_legal:
        sess["finished"] = True
        desc = f"玩家红方走出 {u_cn} ({u_ucci})，黑方{'被将死' if bot_in_chk else '无子可动困毙'}，红方精彩获胜！"
        img = render_xiangqi_board(board, last_user_move=st["last_user_move"], last_bot_move=st.get("last_bot_move"), in_check_side=st["in_check_side"], captured_by_user=st.get("captured_by_user"), captured_by_bot=st.get("captured_by_bot"))
        settle_match_outcome(sess, "user_win", desc, bool(sess.get("is_private")))
        summary = xiangqi_board_summary(sess, desc)
        clear_active_game(sess["chat_id"])
        return {
            "ok": True, "handled": True, "action_type": "MOVE_END", "image_path": img,
            "reply_text": desc, "should_trigger_llm_hook": True, "board_summary_for_llm": summary, "game_over": True,
        }

    # 黑方（弥酱）应招
    bot_mv = choose_xiangqi_bot_move(st, sess.get("difficulty", "normal"))
    if bot_mv is None:
        sess["finished"] = True
        desc = "黑方无合法走法，红方获胜！"
        img = render_xiangqi_board(board, last_user_move=st["last_user_move"], last_bot_move=st.get("last_bot_move"), in_check_side=st["in_check_side"], captured_by_user=st.get("captured_by_user"), captured_by_bot=st.get("captured_by_bot"))
        settle_match_outcome(sess, "user_win", desc, bool(sess.get("is_private")))
        clear_active_game(sess["chat_id"])
        return {"ok": True, "handled": True, "action_type": "MOVE_END", "image_path": img, "reply_text": desc, "should_trigger_llm_hook": True, "board_summary_for_llm": xiangqi_board_summary(sess, desc), "game_over": True}

    b_cn = fmt_xq_cn(board, bot_mv)
    b_ucci = fmt_xq_ucci(bot_mv)
    br1, bc1, br2, bc2 = bot_mv
    b_cap = board[br2][bc2]
    board[br2][bc2] = board[br1][bc1]
    board[br1][bc1] = ""
    st["last_bot_move"] = [[br1, bc1], [br2, bc2]]
    if b_cap:
        st.setdefault("captured_by_bot", []).append(b_cap)

    user_legal = generate_legal_xq_moves(board, "r")
    user_in_chk = is_xq_in_check(board, "r")
    st["in_check_side"] = "r" if user_in_chk else ""

    if not user_legal:
        sess["finished"] = True
        desc = f"弥酱黑方走出 {b_cn} ({b_ucci})，红方{'被绝杀将死' if user_in_chk else '困毙无子可动'}，弥酱拿下本局！"
        img = render_xiangqi_board(board, last_user_move=st["last_user_move"], last_bot_move=st["last_bot_move"], in_check_side=st["in_check_side"], captured_by_user=st.get("captured_by_user"), captured_by_bot=st.get("captured_by_bot"))
        settle_match_outcome(sess, "bot_win", desc, bool(sess.get("is_private")))
        summary = xiangqi_board_summary(sess, desc)
        clear_active_game(sess["chat_id"])
        return {
            "ok": True, "handled": True, "action_type": "MOVE_END", "image_path": img,
            "reply_text": desc, "should_trigger_llm_hook": True, "board_summary_for_llm": summary, "game_over": True,
        }

    save_active_game(sess)
    img = render_xiangqi_board(
        board,
        last_user_move=st["last_user_move"],
        last_bot_move=st["last_bot_move"],
        in_check_side=st["in_check_side"],
        captured_by_user=st.get("captured_by_user"),
        captured_by_bot=st.get("captured_by_bot"),
    )

    tag = "NORMAL"
    is_highlight = False
    if user_in_chk:
        tag, is_highlight = "BOT_CHECK", True
    elif b_cap and b_cap.upper() in ("R", "C", "H"):
        tag, is_highlight = "BOT_CAPTURE_MAJOR", True
    elif u_cap and u_cap.upper() in ("R", "C", "H"):
        tag, is_highlight = "USER_CAPTURE_MAJOR", True
    elif b_cap:
        tag = "BOT_CAPTURE"

    trigger_llm = bool(has_chitchat or is_highlight or (sess["turn_count"] % 6 == 0))
    extra = {
        "bot_move": f"{b_cn} ({b_ucci})",
        "user_move": f"{u_cn} ({u_ucci})",
        "bot_cap_cn": PIECE_DISPLAY.get(b_cap, ""),
        "user_cap_cn": PIECE_DISPLAY.get(u_cap, ""),
    }
    reply_line = situational_line("xiangqi", tag, extra)
    event_desc = f"玩家红方走 {u_cn}({u_ucci})，弥酱黑方应以 {b_cn}({b_ucci})（事件: {tag}）。"
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
        "reply_text": reply_line, "should_trigger_llm_hook": trigger_llm,
        "board_summary_for_llm": xiangqi_board_summary(sess, event_desc) if trigger_llm else "",
        "game_over": False,
    }
