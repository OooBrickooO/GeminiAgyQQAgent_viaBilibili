"""
toys/games/gomoku_engine.py — 猫爪五子棋 (15x15) 棋型评估、VCF 算杀与 Alpha-Beta 引擎
"""
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from toys.board_renderer import render_gomoku_board
from toys.games.session_store import (
    DIFF_NAMES_CN,
    GOMOKU_COLS,
    clear_active_game,
    get_mood_playstyle,
    push_history,
    save_active_game,
    settle_match_outcome,
    situational_line,
)

_PATTERN_SCORES = {
    "five": 100_000_000, "live_four": 10_000_000, "rush_four": 420_000,
    "live_three": 110_000, "sleep_three": 7_500, "live_two": 2_200,
    "sleep_two": 280, "dead": 20, "none": 0,
}


def fmt_gomoku(r: int, c: int) -> str:
    return f"{GOMOKU_COLS[c]}{r + 1}"


def parse_gomoku_coord(text: str) -> Optional[Tuple[int, int]]:
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s:
        return None
    m = re.search(r"(?:^|[^A-Za-z0-9])([A-Oa-o])[\s\-_,，:：]*(1[0-5]|[1-9])(?:[^A-Za-z0-9]|$)", s)
    if m:
        return (int(m.group(2)) - 1, GOMOKU_COLS.index(m.group(1).upper()))
    # 仅在纯坐标短句下允许数字+字母 (避免 "3D电影" 误触)
    m2 = re.match(r"^\s*(1[0-5]|[1-9])[\s\-_,，:：]*([A-Oa-o])\s*$", s)
    if m2:
        return (int(m2.group(1)) - 1, GOMOKU_COLS.index(m2.group(2).upper()))
    return None


def _check_gomoku_win(board: List[List[int]], r: int, c: int, player: int) -> Optional[List[Tuple[int, int]]]:
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        line = [(r, c)]
        for sign in (1, -1):
            nr, nc = r + sign * dr, c + sign * dc
            while 0 <= nr < 15 and 0 <= nc < 15 and board[nr][nc] == player:
                line.append((nr, nc)) if sign > 0 else line.insert(0, (nr, nc))
                nr += sign * dr
                nc += sign * dc
        if len(line) >= 5:
            return line[:5]
    return None


def _analyze_line_pattern(board: List[List[int]], r: int, c: int, dr: int, dc: int, player: int) -> str:
    def scan(sr: int, sc: int) -> Tuple[int, bool, int]:
        cnt, gap_cnt = 0, 0
        nr, nc = r + sr, c + sc
        while 0 <= nr < 15 and 0 <= nc < 15 and board[nr][nc] == player:
            cnt += 1
            nr += sr
            nc += sc
        open_end = 0 <= nr < 15 and 0 <= nc < 15 and board[nr][nc] == 0
        if open_end:
            jr, jc = nr + sr, nc + sc
            while 0 <= jr < 15 and 0 <= jc < 15 and board[jr][jc] == player:
                gap_cnt += 1
                jr += sr
                jc += sc
        return cnt, open_end, gap_cnt

    c1, o1, g1 = scan(dr, dc)
    c2, o2, g2 = scan(-dr, -dc)
    total = 1 + c1 + c2
    if total >= 5:
        return "five"
    if total == 4:
        return "live_four" if (o1 and o2) else ("rush_four" if (o1 or o2) else "dead")
    if (o1 and total + g1 >= 4 and g1 > 0) or (o2 and total + g2 >= 4 and g2 > 0):
        return "rush_four"
    if total == 3:
        return "live_three" if (o1 and o2) else ("sleep_three" if (o1 or o2) else "dead")
    if o1 and o2 and ((total + g1 == 3 and g1 > 0) or (total + g2 == 3 and g2 > 0)):
        return "live_three"
    if total == 2:
        return "live_two" if (o1 and o2) else ("sleep_two" if (o1 or o2) else "none")
    return "none"


def _eval_gomoku_point(board: List[List[int]], r: int, c: int, player: int) -> Tuple[int, Dict[str, int]]:
    counts = {"five": 0, "live_four": 0, "rush_four": 0, "live_three": 0, "sleep_three": 0, "live_two": 0}
    score = 0
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        pat = _analyze_line_pattern(board, r, c, dr, dc, player)
        score += _PATTERN_SCORES.get(pat, 0)
        if pat in counts:
            counts[pat] += 1
    if counts["rush_four"] >= 2 or (counts["rush_four"] >= 1 and counts["live_three"] >= 1):
        score += 2_500_000
    elif counts["live_three"] >= 2:
        score += 1_200_000
    return score, counts


def _gomoku_candidates(board: List[List[int]], dist: int = 2) -> List[Tuple[int, int]]:
    cands = set()
    has_stone = False
    for r in range(15):
        for c in range(15):
            if board[r][c] != 0:
                has_stone = True
                for nr in range(max(0, r - dist), min(15, r + dist + 1)):
                    for nc in range(max(0, c - dist), min(15, c + dist + 1)):
                        if board[nr][nc] == 0:
                            cands.add((nr, nc))
    return list(cands) if has_stone else [(7, 7)]


def _gomoku_ranked_candidates(
    board: List[List[int]], turn_player: int, w_atk: float, w_def: float, limit: int = 12
) -> List[Tuple[int, int, int, int, int, Dict[str, int], Dict[str, int]]]:
    opp = 1 if turn_player == 2 else 2
    out = []
    for r, c in _gomoku_candidates(board):
        atk_sc, atk_cnt = _eval_gomoku_point(board, r, c, turn_player)
        def_sc, def_cnt = _eval_gomoku_point(board, r, c, opp)
        combined = int(atk_sc * w_atk + def_sc * w_def + 24 - (abs(r - 7) + abs(c - 7)))
        out.append((combined, r, c, atk_sc, def_sc, atk_cnt, def_cnt))
    out.sort(key=lambda x: x[0], reverse=True)
    return [(r, c, comb, a, d, ac, dc) for comb, r, c, a, d, ac, dc in out[:limit]]


def _gomoku_vcf_search(board: List[List[int]], attacker: int, depth: int) -> Optional[Tuple[int, int]]:
    """浅层连续冲四/活四算杀 (VCF)"""
    if depth <= 0:
        return None
    defender = 1 if attacker == 2 else 2
    for r, c, _comb, _a, _d, atk_cnt, _dc in _gomoku_ranked_candidates(board, attacker, 1.3, 0.5, limit=8):
        if atk_cnt["five"] > 0 or atk_cnt["live_four"] > 0:
            return (r, c)
        if atk_cnt["rush_four"] > 0:
            board[r][c] = attacker
            opp_blocks = [
                (br, bc) for br, bc in _gomoku_candidates(board, dist=1)
                if _eval_gomoku_point(board, br, bc, attacker)[1]["five"] > 0
            ]
            if len(opp_blocks) >= 2:
                board[r][c] = 0
                return (r, c)
            if len(opp_blocks) == 1:
                br, bc = opp_blocks[0]
                if _eval_gomoku_point(board, br, bc, defender)[1]["five"] == 0:
                    board[br][bc] = defender
                    sub = _gomoku_vcf_search(board, attacker, depth - 1)
                    board[br][bc] = 0
                    if sub is not None:
                        board[r][c] = 0
                        return (r, c)
            board[r][c] = 0
    return None


def _gomoku_static_eval(board: List[List[int]], w_atk: float, w_def: float) -> float:
    bot_best, user_best, bot_sum, user_sum = 0, 0, 0.0, 0.0
    for r, c in _gomoku_candidates(board):
        sb, _ = _eval_gomoku_point(board, r, c, 2)
        su, _ = _eval_gomoku_point(board, r, c, 1)
        bot_best, user_best = max(bot_best, sb), max(user_best, su)
        bot_sum += sb * 0.12
        user_sum += su * 0.12
    return (bot_best * 0.75 + bot_sum) * w_atk - (user_best * 0.85 + user_sum) * w_def


def _gomoku_alphabeta(
    board: List[List[int]], depth: int, alpha: float, beta: float, maximizing: bool, w_atk: float, w_def: float
) -> float:
    ranked = _gomoku_ranked_candidates(board, 2 if maximizing else 1, w_atk, w_def, limit=6)
    if not ranked or depth <= 0:
        return _gomoku_static_eval(board, w_atk, w_def)
    if ranked[0][5]["five"] > 0:
        return (90_000_000 + depth * 1000) if maximizing else -(90_000_000 + depth * 1000)

    val = -1e15 if maximizing else 1e15
    stone = 2 if maximizing else 1
    for r, c, *_ in ranked:
        board[r][c] = stone
        sc = _gomoku_alphabeta(board, depth - 1, alpha, beta, not maximizing, w_atk, w_def)
        board[r][c] = 0
        if maximizing:
            val = max(val, sc)
            alpha = max(alpha, val)
        else:
            val = min(val, sc)
            beta = min(beta, val)
        if alpha >= beta:
            break
    return val


def choose_gomoku_bot_move(board: List[List[int]], difficulty: str, w_atk: float, w_def: float) -> Tuple[int, int, str]:
    """返回 (r, c, tactical_tag) — 绝不故意放水，严格随难度改变搜索深度"""
    ranked = _gomoku_ranked_candidates(board, 2, w_atk, w_def, limit=10)
    if not ranked:
        return (7, 7, "OPENING")

    for r, c, _comb, _a, _d, acnt, _dc in ranked:
        if acnt["five"] > 0:
            return (r, c, "BOT_FIVE")
    for r, c, _comb, _a, _d, _ac, dcnt in ranked:
        if dcnt["five"] > 0:
            return (r, c, "BLOCK_FOUR")

    if difficulty == "easy":
        r, c, _, _, _, acnt, dcnt = ranked[0]
        if acnt["live_four"] > 0:
            return (r, c, "BOT_LIVE_FOUR")
        if dcnt["live_four"] > 0 or dcnt["rush_four"] > 0:
            return (r, c, "BLOCK_FOUR")
        if dcnt["live_three"] > 0:
            return (r, c, "BLOCK_THREE")
        return (r, c, "BOT_LIVE_THREE" if acnt["live_three"] > 0 else "MIDGAME")

    for r, c, _comb, _a, _d, acnt, _dc in ranked:
        if acnt["live_four"] > 0:
            return (r, c, "BOT_LIVE_FOUR")
    if difficulty == "hard":
        vcf_move = _gomoku_vcf_search(board, attacker=2, depth=5)
        if vcf_move is not None:
            return (vcf_move[0], vcf_move[1], "BOT_VCF_ATTACK")

    urgent = [x for x in ranked if x[6]["live_four"] > 0 or x[6]["rush_four"] >= 2 or (x[6]["rush_four"] >= 1 and x[6]["live_three"] >= 1)]
    if urgent:
        return (urgent[0][0], urgent[0][1], "BLOCK_FOUR")

    depth = 3 if difficulty == "hard" else 2
    best_move, best_val = (ranked[0][0], ranked[0][1]), -1e16
    for r, c, comb, *_ in ranked[: (7 if difficulty == "hard" else 5)]:
        board[r][c] = 2
        val = _gomoku_alphabeta(board, depth - 1, -1e15, 1e15, False, w_atk, w_def) + comb * 0.05
        board[r][c] = 0
        if val > best_val:
            best_val, best_move = val, (r, c)

    mr, mc = best_move
    _, m_acnt = _eval_gomoku_point(board, mr, mc, 2)
    _, m_dcnt = _eval_gomoku_point(board, mr, mc, 1)
    if m_dcnt["rush_four"] > 0 or m_dcnt["live_three"] >= 2:
        tag = "BLOCK_FOUR"
    elif m_acnt["live_three"] > 0 or m_acnt["rush_four"] > 0:
        tag = "BOT_LIVE_THREE"
    elif m_dcnt["live_three"] > 0:
        tag = "BLOCK_THREE"
    else:
        tag = "MIDGAME"
    return (mr, mc, tag)


def _find_gomoku_threats(board: List[List[int]], limit: int = 4) -> List[Tuple[int, int]]:
    threats = []
    for r, c in _gomoku_candidates(board, dist=2):
        su, cu = _eval_gomoku_point(board, r, c, 1)
        sb, cb = _eval_gomoku_point(board, r, c, 2)
        if any(cu[k] > 0 for k in ("five", "live_four", "rush_four")) or any(cb[k] > 0 for k in ("five", "live_four")):
            threats.append((max(su, sb), (r, c)))
        elif cu["live_three"] > 0:
            threats.append((su, (r, c)))
    threats.sort(key=lambda x: x[0], reverse=True)
    return [pt for _, pt in threats[:limit]]


def gomoku_board_summary(sess: dict, event_desc: str) -> str:
    st = sess["state"]
    lu, lb = st.get("last_user_move"), st.get("last_bot_move")
    threats = [fmt_gomoku(r, c) for r, c in st.get("threat_points", [])]
    return (
        f"【当前桌游：猫爪五子棋（{DIFF_NAMES_CN.get(sess['difficulty'], '认真')}｜当前弥酱棋风：{get_mood_playstyle()['style']}）】\n"
        f"- 玩家 {sess['player_name']} 执黑，弥酱执白；第 {sess['turn_count']} 回合；"
        f"剩余悔棋次数：{max(0, sess['max_undo'] - sess['undo_used'])}\n"
        f"- 玩家上步：{fmt_gomoku(lu[0], lu[1]) if lu else '无'} ｜ 弥酱落子：{fmt_gomoku(lb[0], lb[1]) if lb else '无'} ｜ 关键威胁空点：{', '.join(threats) or '无'}\n"
        f"- 战况说明：{event_desc}"
    )


def _finish_gomoku(sess: dict, board: List[List[int]], outcome: str, reply: str, desc: str, win_line=None) -> Dict[str, Any]:
    st = sess["state"]
    if win_line:
        st["win_line"] = win_line
    img = render_gomoku_board(board, last_user_move=st.get("last_user_move"), last_bot_move=st.get("last_bot_move"), win_line=win_line)
    settle_match_outcome(sess, outcome, desc, bool(sess.get("is_private")))
    summary = gomoku_board_summary(sess, desc)
    clear_active_game(sess["chat_id"])
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
        "reply_text": reply, "should_trigger_llm_hook": True,
        "board_summary_for_llm": summary, "game_over": True,
    }


def step_gomoku(sess: dict, move_rc: Tuple[int, int], has_chitchat: bool) -> Dict[str, Any]:
    r, c = move_rc
    st = sess["state"]
    board = st["board"]
    if board[r][c] != 0:
        return {
            "ok": False, "handled": True, "action_type": "INVALID_MOVE", "image_path": None,
            "reply_text": f"坐标 {fmt_gomoku(r, c)} 已经有棋子啦，换个空位下吧~",
            "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
        }

    push_history(sess)
    board[r][c] = 1
    st["last_user_move"] = [r, c]
    sess["turn_count"] = int(sess.get("turn_count", 0)) + 1
    sess["updated_at"] = time.time()

    win_line = _check_gomoku_win(board, r, c, 1)
    if win_line:
        return _finish_gomoku(sess, board, "user_win", f"哇，被你用 {fmt_gomoku(r, c)} 连成五子了！你赢啦~", f"玩家落子 {fmt_gomoku(r, c)} 达成五连珠，漂亮地赢下了比赛！", win_line)
    if all(board[rr][cc] != 0 for rr in range(15) for cc in range(15)):
        return _finish_gomoku(sess, board, "draw", "棋盘居然全下满啦，居然是平局！", "棋盘已经下满，双方旗鼓相当打成平局！")

    playstyle = get_mood_playstyle()
    br, bc, tag = choose_gomoku_bot_move(board, sess["difficulty"], playstyle["w_atk"], playstyle["w_def"])
    board[br][bc] = 2
    st["last_bot_move"] = [br, bc]

    bot_win = _check_gomoku_win(board, br, bc, 2)
    if bot_win:
        return _finish_gomoku(sess, board, "bot_win", f"将死！我在 {fmt_gomoku(br, bc)} 五连珠啦，本喵赢咯✨", f"玩家走 {fmt_gomoku(r, c)} 后，弥酱在 {fmt_gomoku(br, bc)} 落子达成五连珠绝杀获胜！", bot_win)
    if all(board[rr][cc] != 0 for rr in range(15) for cc in range(15)):
        return _finish_gomoku(sess, board, "draw", "满盘和棋啦！下得真过瘾~", "双方走满整盘棋，战成平局！")

    if sess["turn_count"] <= 2 and tag == "MIDGAME":
        tag = "OPENING"
    threats = _find_gomoku_threats(board)
    st["threat_points"] = [list(pt) for pt in threats]
    save_active_game(sess)

    img = render_gomoku_board(board, last_user_move=(r, c), last_bot_move=(br, bc), threat_points=threats)
    is_highlight = tag in ("BOT_LIVE_FOUR", "BOT_VCF_ATTACK", "BLOCK_FOUR")
    should_llm = bool(is_highlight or has_chitchat or (sess["turn_count"] % 5 == 0))
    uc, bc_str = fmt_gomoku(r, c), fmt_gomoku(br, bc)
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
        "reply_text": situational_line("gomoku", tag, {"user_coord": uc, "bot_coord": bc_str}),
        "should_trigger_llm_hook": should_llm,
        "board_summary_for_llm": gomoku_board_summary(sess, f"玩家落子 {uc}，弥酱反手落子 {bc_str}（战术判定：{tag}）。"),
        "game_over": False,
    }
