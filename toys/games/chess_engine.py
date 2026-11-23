"""
toys/games/chess_engine.py — 国际象棋 (8x8) 纯 Python 互动风完整规则与 AI 引擎 (4A)

支持：
- 完整标准规则：王车易位（长/短易位及穿将校验）、吃过路兵 (En Passant)、兵底线升变 (Q/R/B/N)、
  将军/送将禁手、将死、逼和、子力不足判和、50步与三次重复判和；
- 坐标与代数记谱双轨输入：UCI 起止坐标 (e2e4 / e2 e4 / e7e8q) + SAN 标准记谱 (Nf3 / O-O / exd5 / e4)；
- Alpha-Beta + 静态吃子搜索 (Quiescence) + MVV-LVA + 位置价值表 (PST) + 弥酱 7D 心情棋风联动；
- 0-Token 快速回应 + 亮点战术事件钩子（将军/吃大子/升变/易位/每6回合/终局）。
"""
import copy
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

FILES = "abcdefgh"
PIECE_VALUES = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 20000}
PIECE_CN = {
    "P": "兵", "N": "马", "B": "象", "R": "车", "Q": "后", "K": "王",
    "p": "黑兵", "n": "黑马", "b": "黑象", "r": "黑车", "q": "黑后", "k": "黑王",
}

# 经典轻量化 Piece-Square Tables（白方视角：row 0=第8横线，row 7=第1横线；黑方上下翻转）
_PST_P = [
    [0, 0, 0, 0, 0, 0, 0, 0],
    [50, 50, 50, 50, 50, 50, 50, 50],
    [10, 10, 20, 30, 30, 20, 10, 10],
    [5, 5, 10, 26, 26, 10, 5, 5],
    [0, 0, 0, 22, 22, 0, 0, 0],
    [5, -5, -10, 0, 0, -10, -5, 5],
    [5, 10, 10, -20, -20, 10, 10, 5],
    [0, 0, 0, 0, 0, 0, 0, 0],
]
_PST_N = [
    [-50, -40, -30, -30, -30, -30, -40, -50],
    [-40, -20, 0, 0, 0, 0, -20, -40],
    [-30, 0, 10, 15, 15, 10, 0, -30],
    [-30, 5, 15, 20, 20, 15, 5, -30],
    [-30, 0, 15, 20, 20, 15, 0, -30],
    [-30, 5, 10, 15, 15, 10, 5, -30],
    [-40, -20, 0, 5, 5, 0, -20, -40],
    [-50, -40, -30, -30, -30, -30, -40, -50],
]
_PST_B = [
    [-20, -10, -10, -10, -10, -10, -10, -20],
    [-10, 0, 0, 0, 0, 0, 0, -10],
    [-10, 0, 8, 12, 12, 8, 0, -10],
    [-10, 5, 8, 12, 12, 8, 5, -10],
    [-10, 0, 10, 12, 12, 10, 0, -10],
    [-10, 10, 10, 10, 10, 10, 10, -10],
    [-10, 6, 0, 0, 0, 0, 6, -10],
    [-20, -10, -10, -10, -10, -10, -10, -20],
]
_PST_R = [
    [0, 0, 0, 0, 0, 0, 0, 0],
    [6, 12, 12, 12, 12, 12, 12, 6],
    [-5, 0, 0, 0, 0, 0, 0, -5],
    [-5, 0, 0, 0, 0, 0, 0, -5],
    [-5, 0, 0, 0, 0, 0, 0, -5],
    [-5, 0, 0, 0, 0, 0, 0, -5],
    [-5, 0, 0, 0, 0, 0, 0, -5],
    [0, 0, 0, 6, 6, 0, 0, 0],
]
_PST_Q = [
    [-20, -10, -10, -5, -5, -10, -10, -20],
    [-10, 0, 0, 0, 0, 0, 0, -10],
    [-10, 0, 5, 5, 5, 5, 0, -10],
    [-5, 0, 5, 5, 5, 5, 0, -5],
    [0, 0, 5, 5, 5, 5, 0, -5],
    [-10, 5, 5, 5, 5, 5, 0, -10],
    [-10, 0, 5, 0, 0, 0, 0, -10],
    [-20, -10, -10, -5, -5, -10, -10, -20],
]
_PST_K = [
    [-30, -40, -40, -50, -50, -40, -40, -30],
    [-30, -40, -40, -50, -50, -40, -40, -30],
    [-30, -40, -40, -50, -50, -40, -40, -30],
    [-30, -40, -40, -50, -50, -40, -40, -30],
    [-20, -30, -30, -40, -40, -30, -30, -20],
    [-10, -20, -20, -20, -20, -20, -20, -10],
    [20, 20, 0, 0, 0, 0, 20, 20],
    [20, 30, 10, 0, 0, 10, 30, 20],
]
_PST_MAP = {"P": _PST_P, "N": _PST_N, "B": _PST_B, "R": _PST_R, "Q": _PST_Q, "K": _PST_K}


def init_chess_board() -> List[List[str]]:
    return [
        ["r", "n", "b", "q", "k", "b", "n", "r"],
        ["p", "p", "p", "p", "p", "p", "p", "p"],
        ["", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        ["", "", "", "", "", "", "", ""],
        ["P", "P", "P", "P", "P", "P", "P", "P"],
        ["R", "N", "B", "Q", "K", "B", "N", "R"],
    ]


def init_chess_state() -> Dict[str, Any]:
    board = init_chess_board()
    st = {
        "board": board,
        "castling": {"K": True, "Q": True, "k": True, "q": True},
        "ep_target": None,
        "halfmove_clock": 0,
        "position_counts": {},
        "last_user_move": None,
        "last_bot_move": None,
        "in_check_side": "",
        "captured_by_user": [],
        "captured_by_bot": [],
    }
    pk = chess_position_key(board, st["castling"], st["ep_target"], "w")
    st["position_counts"][pk] = 1
    return st


def is_white(p: str) -> bool:
    return bool(p) and p.isupper()


def is_black(p: str) -> bool:
    return bool(p) and p.islower()


def piece_side(p: str) -> str:
    if not p:
        return ""
    return "w" if p.isupper() else "b"


def sq_to_str(r: int, c: int) -> str:
    return f"{FILES[c]}{8 - r}"


def str_to_sq(s: str) -> Optional[Tuple[int, int]]:
    s = (s or "").strip().lower()
    if len(s) != 2 or s[0] not in FILES or s[1] not in "12345678":
        return None
    return (8 - int(s[1]), FILES.index(s[0]))


def fmt_chess_move(move: Tuple[int, int, int, int, str]) -> str:
    r1, c1, r2, c2, promo = move
    return f"{sq_to_str(r1, c1)}{sq_to_str(r2, c2)}{promo.lower() if promo else ''}"


def chess_position_key(board: List[List[str]], castling: Dict[str, bool], ep_target: Optional[List[int]], turn: str) -> str:
    rows = "/".join("".join(cell or "." for cell in row) for row in board)
    c_str = "".join(k for k in ("K", "Q", "k", "q") if castling.get(k)) or "-"
    ep_str = sq_to_str(ep_target[0], ep_target[1]) if ep_target else "-"
    return f"{rows} {turn} {c_str} {ep_str}"


def find_king(board: List[List[str]], side: str) -> Optional[Tuple[int, int]]:
    target = "K" if side == "w" else "k"
    for r in range(8):
        for c in range(8):
            if board[r][c] == target:
                return (r, c)
    return None


def is_square_attacked(board: List[List[str]], r: int, c: int, by_side: str) -> bool:
    # 1. Pawn attacks
    if by_side == "w":
        for dc in (-1, 1):
            pr, pc = r + 1, c + dc
            if 0 <= pr < 8 and 0 <= pc < 8 and board[pr][pc] == "P":
                return True
    else:
        for dc in (-1, 1):
            pr, pc = r - 1, c + dc
            if 0 <= pr < 8 and 0 <= pc < 8 and board[pr][pc] == "p":
                return True

    # 2. Knight attacks
    kn = "N" if by_side == "w" else "n"
    for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == kn:
            return True

    # 3. King attacks
    kg = "K" if by_side == "w" else "k"
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == kg:
                return True

    # 4. Rook / Queen orthogonal rays
    rq = ("R", "Q") if by_side == "w" else ("r", "q")
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = r + dr, c + dc
        while 0 <= nr < 8 and 0 <= nc < 8:
            p = board[nr][nc]
            if p:
                if p in rq:
                    return True
                break
            nr += dr
            nc += dc

    # 5. Bishop / Queen diagonal rays
    bq = ("B", "Q") if by_side == "w" else ("b", "q")
    for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        nr, nc = r + dr, c + dc
        while 0 <= nr < 8 and 0 <= nc < 8:
            p = board[nr][nc]
            if p:
                if p in bq:
                    return True
                break
            nr += dr
            nc += dc

    return False


def is_in_check(board: List[List[str]], side: str) -> bool:
    kp = find_king(board, side)
    if kp is None:
        return True
    opp = "b" if side == "w" else "w"
    return is_square_attacked(board, kp[0], kp[1], opp)


def apply_chess_move_pure(
    board: List[List[str]],
    castling: Dict[str, bool],
    ep_target: Optional[List[int]],
    move: Tuple[int, int, int, int, str],
) -> Tuple[List[List[str]], Dict[str, bool], Optional[List[int]], str, bool]:
    """执行一步棋并返回 (new_board, new_castling, new_ep, captured_piece, is_castle)"""
    r1, c1, r2, c2, promo = move
    nb = [row[:] for row in board]
    nc = dict(castling)
    p = nb[r1][c1]
    captured = nb[r2][c2]
    is_castle = False
    new_ep = None

    # 吃过路兵
    if p.upper() == "P" and ep_target and r2 == ep_target[0] and c2 == ep_target[1] and not captured:
        captured = nb[r1][c2]
        nb[r1][c2] = ""

    nb[r1][c1] = ""
    if promo:
        nb[r2][c2] = promo.upper() if p.isupper() else promo.lower()
    else:
        nb[r2][c2] = p

    # 王车易位
    if p == "K" and r1 == 7 and c1 == 4:
        nc["K"] = False
        nc["Q"] = False
        if c2 == 6:
            nb[7][5], nb[7][7] = nb[7][7], ""
            is_castle = True
        elif c2 == 2:
            nb[7][3], nb[7][0] = nb[7][0], ""
            is_castle = True
    elif p == "k" and r1 == 0 and c1 == 4:
        nc["k"] = False
        nc["q"] = False
        if c2 == 6:
            nb[0][5], nb[0][7] = nb[0][7], ""
            is_castle = True
        elif c2 == 2:
            nb[0][3], nb[0][0] = nb[0][0], ""
            is_castle = True

    # 车移动或被吃更新易位权
    for (rr, cc, key) in ((7, 7, "K"), (7, 0, "Q"), (0, 7, "k"), (0, 0, "q")):
        if (r1 == rr and c1 == cc) or (r2 == rr and c2 == cc):
            nc[key] = False

    # 兵两格推进设置过路兵目标
    if p == "P" and r1 == 6 and r2 == 4:
        new_ep = [5, c1]
    elif p == "p" and r1 == 1 and r2 == 3:
        new_ep = [2, c1]

    return nb, nc, new_ep, captured, is_castle


def generate_legal_chess_moves(
    board: List[List[str]],
    castling: Dict[str, bool],
    ep_target: Optional[List[int]],
    side: str,
) -> List[Tuple[int, int, int, int, str]]:
    opp = "b" if side == "w" else "w"
    pseudo: List[Tuple[int, int, int, int, str]] = []

    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if not p or piece_side(p) != side:
                continue
            pu = p.upper()
            if pu == "P":
                step = -1 if side == "w" else 1
                start_r = 6 if side == "w" else 1
                promo_r = 0 if side == "w" else 7
                nr = r + step
                if 0 <= nr < 8 and not board[nr][c]:
                    if nr == promo_r:
                        for pr in ("Q", "R", "B", "N"):
                            pseudo.append((r, c, nr, c, pr if side == "w" else pr.lower()))
                    else:
                        pseudo.append((r, c, nr, c, ""))
                        if r == start_r and not board[r + 2 * step][c]:
                            pseudo.append((r, c, r + 2 * step, c, ""))
                for dc in (-1, 1):
                    nc = c + dc
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        target = board[nr][nc]
                        is_ep = bool(ep_target and nr == ep_target[0] and nc == ep_target[1])
                        if (target and piece_side(target) == opp) or is_ep:
                            if nr == promo_r:
                                for pr in ("Q", "R", "B", "N"):
                                    pseudo.append((r, c, nr, nc, pr if side == "w" else pr.lower()))
                            else:
                                pseudo.append((r, c, nr, nc, ""))
            elif pu == "N":
                for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < 8 and 0 <= nc < 8 and piece_side(board[nr][nc]) != side:
                        pseudo.append((r, c, nr, nc, ""))
            elif pu in ("B", "R", "Q"):
                dirs = []
                if pu in ("B", "Q"):
                    dirs.extend(((-1, -1), (-1, 1), (1, -1), (1, 1)))
                if pu in ("R", "Q"):
                    dirs.extend(((-1, 0), (1, 0), (0, -1), (0, 1)))
                for dr, dc in dirs:
                    nr, nc = r + dr, c + dc
                    while 0 <= nr < 8 and 0 <= nc < 8:
                        t = board[nr][nc]
                        if not t:
                            pseudo.append((r, c, nr, nc, ""))
                        else:
                            if piece_side(t) == opp:
                                pseudo.append((r, c, nr, nc, ""))
                            break
                        nr += dr
                        nc += dc
            elif pu == "K":
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < 8 and 0 <= nc < 8 and piece_side(board[nr][nc]) != side:
                            pseudo.append((r, c, nr, nc, ""))
                # 易位
                if side == "w" and r == 7 and c == 4 and not is_square_attacked(board, 7, 4, "b"):
                    if castling.get("K") and board[7][7] == "R" and not board[7][5] and not board[7][6]:
                        if not is_square_attacked(board, 7, 5, "b") and not is_square_attacked(board, 7, 6, "b"):
                            pseudo.append((7, 4, 7, 6, ""))
                    if castling.get("Q") and board[7][0] == "R" and not board[7][1] and not board[7][2] and not board[7][3]:
                        if not is_square_attacked(board, 7, 3, "b") and not is_square_attacked(board, 7, 2, "b"):
                            pseudo.append((7, 4, 7, 2, ""))
                elif side == "b" and r == 0 and c == 4 and not is_square_attacked(board, 0, 4, "w"):
                    if castling.get("k") and board[0][7] == "r" and not board[0][5] and not board[0][6]:
                        if not is_square_attacked(board, 0, 5, "w") and not is_square_attacked(board, 0, 6, "w"):
                            pseudo.append((0, 4, 0, 6, ""))
                    if castling.get("q") and board[0][0] == "r" and not board[0][1] and not board[0][2] and not board[0][3]:
                        if not is_square_attacked(board, 0, 3, "w") and not is_square_attacked(board, 0, 2, "w"):
                            pseudo.append((0, 4, 0, 2, ""))

    legal = []
    for mv in pseudo:
        nb, _, _, _, _ = apply_chess_move_pure(board, castling, ep_target, mv)
        if not is_in_check(nb, side):
            legal.append(mv)
    return legal


def is_insufficient_material(board: List[List[str]]) -> bool:
    pieces = []
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if p and p.upper() != "K":
                pieces.append((p.upper(), (r + c) % 2))
    if not pieces:
        return True
    if len(pieces) == 1 and pieces[0][0] in ("N", "B"):
        return True
    if len(pieces) == 2 and all(pt == "B" for pt, _ in pieces) and pieces[0][1] == pieces[1][1]:
        return True
    return False


def parse_chess_move(
    board: List[List[str]],
    castling: Dict[str, bool],
    ep_target: Optional[List[int]],
    text: str,
    side: str = "w",
) -> Tuple[Optional[Tuple[int, int, int, int, str]], str]:
    """
    国际象棋双轨输入解析器：
    1) UCI / 起止坐标法：e2e4 / e2 e4 / e2-e4 / e2到e4 / e7e8q / e7 e8=Q
    2) SAN 标准代数记谱：O-O / O-O-O / 短易位 / 长易位 / Nf3 / exd5 / e4 / Nbd2 / e8=Q
    返回: (move_tuple_or_None, matched_token)
    """
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s:
        return None, ""
    legal = generate_legal_chess_moves(board, castling, ep_target, side)
    if not legal:
        return None, ""

    # 1. 易位关键词 / SAN 易位
    m_castle = re.search(r"(?:^|[^A-Za-z0-9])(O-O-O|0-0-0|O-O|0-0|长易位|短易位)(?:[^A-Za-z0-9]|$)", s, re.IGNORECASE)
    if m_castle:
        tok = m_castle.group(1)
        is_long = tok.lower() in ("o-o-o", "0-0-0", "长易位")
        row = 7 if side == "w" else 0
        target_c = 2 if is_long else 6
        for mv in legal:
            if mv[0] == row and mv[1] == 4 and mv[2] == row and mv[3] == target_c:
                return mv, tok

    # 2. UCI / 坐标起止法: e2e4 / e2 e4 / e2->e4 / e2到e4 / e7e8q
    m_uci = re.search(
        r"(?:^|[^A-Za-z0-9])([a-hA-H][1-8])[\s\-_,，:：>到至]*([a-hA-H][1-8])(?:=?([qrbnQRBN]))?(?:[^A-Za-z0-9]|$)",
        s,
    )
    if m_uci:
        sq1 = str_to_sq(m_uci.group(1))
        sq2 = str_to_sq(m_uci.group(2))
        promo_req = (m_uci.group(3) or "").upper()
        if sq1 and sq2:
            matched = [mv for mv in legal if mv[0] == sq1[0] and mv[1] == sq1[1] and mv[2] == sq2[0] and mv[3] == sq2[1]]
            if matched:
                if promo_req:
                    for mv in matched:
                        if mv[4].upper() == promo_req:
                            return mv, m_uci.group(0).strip()
                # 默认自动升后
                for mv in matched:
                    if mv[4].upper() == "Q":
                        return mv, m_uci.group(0).strip()
                return matched[0], m_uci.group(0).strip()
            # 坐标格式合法但步法不合法，返回特殊标记供上层提示
            return (-1, -1, -1, -1, f"ILLEGAL:{m_uci.group(1).lower()}{m_uci.group(2).lower()}"), m_uci.group(0).strip()

    # 3. SAN 代数记谱: Nf3, Bxc6, exd5, e4, R1e2, e8=Q
    m_san = re.search(
        r"(?:^|[^A-Za-z0-9])([KQRBN])?([a-h])?([1-8])?(x)?([a-h][1-8])(?:=?([QRBN]))?[+#]?(?:[^A-Za-z0-9]|$)",
        s,
    )
    if m_san:
        piece_ch = m_san.group(1) or "P"
        dis_file = m_san.group(2)
        dis_rank = m_san.group(3)
        is_cap = bool(m_san.group(4))
        dest_sq = str_to_sq(m_san.group(5))
        promo_req = (m_san.group(6) or "").upper()
        if dest_sq:
            cands = []
            for mv in legal:
                r1, c1, r2, c2, pr = mv
                if (r2, c2) != dest_sq:
                    continue
                p = board[r1][c1]
                if p.upper() != piece_ch:
                    continue
                if dis_file and FILES[c1] != dis_file:
                    continue
                if dis_rank and str(8 - r1) != dis_rank:
                    continue
                if is_cap and not board[r2][c2] and not (piece_ch == "P" and ep_target and [r2, c2] == ep_target):
                    continue
                if promo_req and pr.upper() != promo_req:
                    continue
                cands.append(mv)
            if cands:
                for mv in cands:
                    if not promo_req and mv[4].upper() == "Q":
                        return mv, m_san.group(0).strip()
                return cands[0], m_san.group(0).strip()

    return None, ""


def _eval_chess_board(board: List[List[str]], w_atk: float = 1.0, w_def: float = 1.0) -> int:
    """从黑方（弥酱）视角评估局势分值：正数代表黑方占优，负数代表白方（玩家）占优"""
    score = 0
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if not p:
                continue
            pu = p.upper()
            val = PIECE_VALUES[pu]
            if p.islower():  # 黑方（弥酱）
                pst = _PST_MAP[pu][7 - r][c]
                # 激进棋风鼓励前压，防守棋风强化王安全与后方结构
                if pu != "K" and r >= 4:
                    pst = int(pst * w_atk)
                elif pu == "K":
                    pst = int(pst * w_def)
                score += val + pst
            else:  # 白方（玩家）
                pst = _PST_MAP[pu][r][c]
                score -= val + pst
    return score


def _order_chess_moves(board: List[List[str]], moves: List[Tuple[int, int, int, int, str]]) -> List[Tuple[int, int, int, int, str]]:
    def _mv_key(mv):
        r1, c1, r2, c2, promo = mv
        s = 0
        victim = board[r2][c2]
        attacker = board[r1][c1]
        if victim:
            s += 10 * PIECE_VALUES[victim.upper()] - PIECE_VALUES[attacker.upper()]
        if promo:
            s += 800
        return s
    return sorted(moves, key=_mv_key, reverse=True)


def _quiescence(board, castling, ep_target, alpha: int, beta: int, maximizing_black: bool, qdepth: int, w_atk: float, w_def: float, deadline: float) -> int:
    stand_pat = _eval_chess_board(board, w_atk, w_def)
    if qdepth <= 0 or time.time() > deadline:
        return stand_pat
    if maximizing_black:
        if stand_pat >= beta:
            return beta
        if stand_pat > alpha:
            alpha = stand_pat
        side = "b"
    else:
        if stand_pat <= alpha:
            return alpha
        if stand_pat < beta:
            beta = stand_pat
        side = "w"

    legal = generate_legal_chess_moves(board, castling, ep_target, side)
    cap_moves = [mv for mv in legal if board[mv[2]][mv[3]] or mv[4]]
    for mv in _order_chess_moves(board, cap_moves)[:8]:
        nb, nc, nep, _, _ = apply_chess_move_pure(board, castling, ep_target, mv)
        val = _quiescence(nb, nc, nep, alpha, beta, not maximizing_black, qdepth - 1, w_atk, w_def, deadline)
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


def _alphabeta_chess(board, castling, ep_target, depth: int, alpha: int, beta: int, maximizing_black: bool, w_atk: float, w_def: float, deadline: float) -> int:
    side = "b" if maximizing_black else "w"
    legal = generate_legal_chess_moves(board, castling, ep_target, side)
    if not legal:
        if is_in_check(board, side):
            return (-99999 - depth) if maximizing_black else (99999 + depth)
        return 0
    if depth <= 0 or time.time() > deadline:
        return _quiescence(board, castling, ep_target, alpha, beta, maximizing_black, 2, w_atk, w_def, deadline)

    ordered = _order_chess_moves(board, legal)
    if maximizing_black:
        best = -10**9
        for mv in ordered:
            nb, nc, nep, _, _ = apply_chess_move_pure(board, castling, ep_target, mv)
            val = _alphabeta_chess(nb, nc, nep, depth - 1, alpha, beta, False, w_atk, w_def, deadline)
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
            nb, nc, nep, _, _ = apply_chess_move_pure(board, castling, ep_target, mv)
            val = _alphabeta_chess(nb, nc, nep, depth - 1, alpha, beta, True, w_atk, w_def, deadline)
            if val < best:
                best = val
            if best < beta:
                beta = best
            if alpha >= beta:
                break
        return best


def choose_chess_bot_move(state: Dict[str, Any], difficulty: str = "normal") -> Optional[Tuple[int, int, int, int, str]]:
    board = state["board"]
    castling = state["castling"]
    ep_target = state.get("ep_target")
    legal = generate_legal_chess_moves(board, castling, ep_target, "b")
    if not legal:
        return None

    ps = get_mood_playstyle()
    w_atk, w_def = ps["w_atk"], ps["w_def"]
    depth = 1 if difficulty == "easy" else (3 if difficulty == "hard" else 2)
    time_budget = 0.6 if difficulty == "easy" else (1.3 if difficulty == "hard" else 0.9)
    deadline = time.time() + time_budget

    scored = []
    alpha = -10**9
    for mv in _order_chess_moves(board, legal):
        nb, nc, nep, _, _ = apply_chess_move_pure(board, castling, ep_target, mv)
        val = _alphabeta_chess(nb, nc, nep, depth - 1, alpha, 10**9, False, w_atk, w_def, deadline)
        if difficulty == "easy":
            val += random.randint(-90, 90)
        elif difficulty == "normal":
            val += random.randint(-15, 15)
        scored.append((val, mv))
        if val > alpha:
            alpha = val

    scored.sort(key=lambda x: x[0], reverse=True)
    if difficulty == "easy" and len(scored) >= 3:
        return random.choice(scored[:3])[1]
    return scored[0][1]


def chess_board_summary(sess: dict, event_desc: str) -> str:
    st = sess["state"]
    style = get_mood_playstyle()["style"]
    u_cap = "".join(PIECE_CN.get(p, p) for p in st.get("captured_by_user", [])[-6:]) or "无"
    b_cap = "".join(PIECE_CN.get(p, p) for p in st.get("captured_by_bot", [])[-6:]) or "无"
    lm_u = st.get("last_user_move")
    lm_b = st.get("last_bot_move")
    u_str = f"{sq_to_str(*lm_u[0])}->{sq_to_str(*lm_u[1])}" if lm_u else "无"
    b_str = f"{sq_to_str(*lm_b[0])}->{sq_to_str(*lm_b[1])}" if lm_b else "无"
    return (
        f"【国际象棋实时战况 | 难度: {DIFF_NAMES_CN.get(sess['difficulty'], '正常')} | 棋风: {style}】\n"
        f"- 当前回合: 第 {sess['turn_count']} 轮 | 玩家(白方)最新走子: {u_str} | 弥酱(黑方)最新走子: {b_str}\n"
        f"- 吃子概况: 玩家吃掉 [{u_cap}] | 弥酱吃掉 [{b_cap}]\n"
        f"- 局势事件: {event_desc}"
    )


def step_chess(sess: dict, raw_text: str, has_chitchat: bool = False) -> Dict[str, Any]:
    from toys.board_renderer import render_chess_board

    st = sess["state"]
    board = st["board"]
    castling = st["castling"]
    ep_target = st.get("ep_target")

    mv, matched_tok = parse_chess_move(board, castling, ep_target, raw_text, side="w")
    if mv is None:
        return {
            "ok": True, "handled": True, "action_type": "CHAT_ONLY", "image_path": None,
            "reply_text": "", "should_trigger_llm_hook": True,
            "board_summary_for_llm": chess_board_summary(sess, "玩家没有报出新走法，正在就当前国际象棋棋局与你交流。"),
            "game_over": False,
        }
    if mv[0] == -1:
        bad_str = mv[4].replace("ILLEGAL:", "")
        return {
            "ok": False, "handled": True, "action_type": "INVALID_MOVE", "image_path": None,
            "reply_text": f"步法 {bad_str} 不符合规则（可能路径被挡或会导致白王被将军）哦，换一步试试~",
            "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
        }

    push_history(sess)
    u_piece = board[mv[0]][mv[1]]
    nb, nc, nep, u_cap, u_castle = apply_chess_move_pure(board, castling, ep_target, mv)
    st["board"], st["castling"], st["ep_target"] = nb, nc, nep
    st["last_user_move"] = [[mv[0], mv[1]], [mv[2], mv[3]]]
    st["halfmove_clock"] = 0 if (u_piece.upper() == "P" or u_cap) else (int(st.get("halfmove_clock", 0)) + 1)
    if u_cap:
        st.setdefault("captured_by_user", []).append(u_cap)
    sess["turn_count"] = int(sess.get("turn_count", 0)) + 1
    sess["updated_at"] = time.time()

    u_mv_str = fmt_chess_move(mv)
    pk_b = chess_position_key(nb, nc, nep, "b")
    pos_counts = st.setdefault("position_counts", {})
    pos_counts[pk_b] = int(pos_counts.get(pk_b, 0)) + 1

    # 检查黑方是否被将死/逼和/和棋
    bot_legal = generate_legal_chess_moves(nb, nc, nep, "b")
    bot_in_chk = is_in_check(nb, "b")
    st["in_check_side"] = "b" if bot_in_chk else ""

    if not bot_legal or is_insufficient_material(nb) or st["halfmove_clock"] >= 100 or pos_counts[pk_b] >= 3:
        sess["finished"] = True
        if not bot_legal and bot_in_chk:
            outcome, desc = "user_win", f"玩家白方走出 {u_mv_str} 将死黑王，精彩获胜！"
        else:
            outcome, desc = "draw", f"玩家走出 {u_mv_str} 后触发和棋（逼和/子力不足/三次重复）。"
        img = render_chess_board(nb, last_user_move=st["last_user_move"], last_bot_move=st.get("last_bot_move"), in_check_side=st["in_check_side"], captured_by_user=st.get("captured_by_user"), captured_by_bot=st.get("captured_by_bot"))
        settle_match_outcome(sess, outcome, desc, bool(sess.get("is_private")))
        summary = chess_board_summary(sess, desc)
        clear_active_game(sess["chat_id"])
        return {
            "ok": True, "handled": True, "action_type": "MOVE_END", "image_path": img,
            "reply_text": desc, "should_trigger_llm_hook": True, "board_summary_for_llm": summary, "game_over": True,
        }

    # 弥酱（黑方）应招
    bot_mv = choose_chess_bot_move(st, sess.get("difficulty", "normal"))
    if bot_mv is None:
        sess["finished"] = True
        desc = "黑方无合法走法，对局结束。"
        img = render_chess_board(nb, last_user_move=st["last_user_move"], last_bot_move=st.get("last_bot_move"), in_check_side=st["in_check_side"], captured_by_user=st.get("captured_by_user"), captured_by_bot=st.get("captured_by_bot"))
        settle_match_outcome(sess, "draw", desc, bool(sess.get("is_private")))
        clear_active_game(sess["chat_id"])
        return {"ok": True, "handled": True, "action_type": "MOVE_END", "image_path": img, "reply_text": desc, "should_trigger_llm_hook": True, "board_summary_for_llm": chess_board_summary(sess, desc), "game_over": True}

    b_piece = nb[bot_mv[0]][bot_mv[1]]
    nb2, nc2, nep2, b_cap, b_castle = apply_chess_move_pure(nb, nc, nep, bot_mv)
    st["board"], st["castling"], st["ep_target"] = nb2, nc2, nep2
    st["last_bot_move"] = [[bot_mv[0], bot_mv[1]], [bot_mv[2], bot_mv[3]]]
    st["halfmove_clock"] = 0 if (b_piece.upper() == "P" or b_cap) else (int(st.get("halfmove_clock", 0)) + 1)
    if b_cap:
        st.setdefault("captured_by_bot", []).append(b_cap)

    b_mv_str = fmt_chess_move(bot_mv)
    pk_w = chess_position_key(nb2, nc2, nep2, "w")
    pos_counts[pk_w] = int(pos_counts.get(pk_w, 0)) + 1

    user_legal = generate_legal_chess_moves(nb2, nc2, nep2, "w")
    user_in_chk = is_in_check(nb2, "w")
    st["in_check_side"] = "w" if user_in_chk else ""

    if not user_legal or is_insufficient_material(nb2) or st["halfmove_clock"] >= 100 or pos_counts[pk_w] >= 3:
        sess["finished"] = True
        if not user_legal and user_in_chk:
            outcome, desc = "bot_win", f"弥酱黑方走出 {b_mv_str} 将死白王（Checkmate）拿下本局！"
        else:
            outcome, desc = "draw", f"弥酱走出 {b_mv_str} 后双方平局（逼和/子力不足/三次重复）。"
        img = render_chess_board(nb2, last_user_move=st["last_user_move"], last_bot_move=st["last_bot_move"], in_check_side=st["in_check_side"], captured_by_user=st.get("captured_by_user"), captured_by_bot=st.get("captured_by_bot"))
        settle_match_outcome(sess, outcome, desc, bool(sess.get("is_private")))
        summary = chess_board_summary(sess, desc)
        clear_active_game(sess["chat_id"])
        return {
            "ok": True, "handled": True, "action_type": "MOVE_END", "image_path": img,
            "reply_text": desc, "should_trigger_llm_hook": True, "board_summary_for_llm": summary, "game_over": True,
        }

    save_active_game(sess)
    img = render_chess_board(
        nb2,
        last_user_move=st["last_user_move"],
        last_bot_move=st["last_bot_move"],
        in_check_side=st["in_check_side"],
        captured_by_user=st.get("captured_by_user"),
        captured_by_bot=st.get("captured_by_bot"),
    )

    # 战术标签与亮点钩子判定
    tag = "NORMAL"
    is_highlight = False
    if user_in_chk:
        tag, is_highlight = "BOT_CHECK", True
    elif b_cap and b_cap.upper() in ("Q", "R"):
        tag, is_highlight = "BOT_CAPTURE_MAJOR", True
    elif u_cap and u_cap.upper() in ("Q", "R"):
        tag, is_highlight = "USER_CAPTURE_MAJOR", True
    elif bot_mv[4] or mv[4]:
        tag, is_highlight = "PROMOTION", True
    elif b_castle or u_castle:
        tag = "CASTLE"
    elif b_cap:
        tag = "BOT_CAPTURE"

    trigger_llm = bool(has_chitchat or is_highlight or (sess["turn_count"] % 6 == 0))
    extra = {
        "bot_move": b_mv_str,
        "user_move": u_mv_str,
        "bot_cap_cn": PIECE_CN.get(b_cap, ""),
        "user_cap_cn": PIECE_CN.get(u_cap, ""),
    }
    reply_line = situational_line("chess", tag, extra)
    event_desc = f"玩家走 {u_mv_str}，弥酱应以 {b_mv_str}（事件: {tag}）。"
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
        "reply_text": reply_line, "should_trigger_llm_hook": trigger_llm,
        "board_summary_for_llm": chess_board_summary(sess, event_desc) if trigger_llm else "",
        "game_over": False,
    }
