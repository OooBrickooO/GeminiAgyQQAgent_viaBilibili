"""
toys/games/cards_and_numbers.py — 猫猫21点 (含御神签联动) 与 1A2B 破译密码引擎
"""
import random
import time
from typing import Any, Dict, List, Tuple

from toys.board_renderer import render_blackjack_table
from toys.games.session_store import (
    DIFF_NAMES_CN,
    clear_active_game,
    get_mood_playstyle,
    save_active_game,
    settle_match_outcome,
    situational_line,
)


def new_shuffled_deck() -> List[str]:
    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    deck = [f"{s}{r}" for s in suits for r in ranks]
    random.shuffle(deck)
    return deck


def bj_hand_value(cards: List[str]) -> Tuple[int, bool]:
    total, aces = 0, 0
    for c in cards:
        r = c[1:] if len(c) > 1 else c
        if r == "A":
            total += 11
            aces += 1
        elif r in ("J", "Q", "K"):
            total += 10
        else:
            total += int(r)
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total, (aces > 0)


def bj_summary(sess: dict, event_desc: str, show_all_bot: bool = False) -> str:
    st = sess["state"]
    p_cards, b_cards = st["player_hand"], st["bot_hand"]
    p_val, _ = bj_hand_value(p_cards)
    if show_all_bot:
        b_val, _ = bj_hand_value(b_cards)
        b_disp = f"{' '.join(b_cards)} ({b_val}点)"
    else:
        b_disp = f"{b_cards[0]} [暗牌]"
    fortune = st.get("fortune", {})
    f_note = f"今日签运：{fortune.get('title', '未抽签')}" if fortune.get("has_drawn") else "今日未抽签"
    return (
        f"【当前桌游：猫猫21点（{DIFF_NAMES_CN.get(sess['difficulty'], '认真')}｜{f_note}）】\n"
        f"- 庄家(弥酱)手牌：{b_disp}\n"
        f"- 玩家({sess['player_name']})手牌：{' '.join(p_cards)} ({p_val}点)"
        f"{' [已双倍加注]' if st.get('doubled') else ''}\n"
        f"- 牌局动态：{event_desc}"
    )


def step_blackjack(sess: dict, action: str, has_chitchat: bool) -> Dict[str, Any]:
    st = sess["state"]
    deck, p_hand, b_hand = st["deck"], st["player_hand"], st["bot_hand"]
    is_private = bool(sess.get("is_private"))
    sess["turn_count"] = int(sess.get("turn_count", 0)) + 1
    sess["updated_at"] = time.time()

    if action == "double" and len(p_hand) > 2:
        return {
            "ok": False, "handled": True, "action_type": "INVALID_MOVE", "image_path": None,
            "reply_text": "双倍加注只能在初始两张牌的时候选择哦~ 发送「要牌」或「停牌」吧！",
            "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
        }

    if action in ("hit", "double"):
        if action == "double":
            st["doubled"] = True
        new_card = deck.pop()
        p_hand.append(new_card)
        p_val, _ = bj_hand_value(p_hand)

        if p_val > 21:
            b_val, _ = bj_hand_value(b_hand)
            img = render_blackjack_table(p_hand, b_hand, hide_bot_hole=False, player_score=p_val, bot_score_display=f"{b_val} pts", status_title="Cat Blackjack - Bust!")
            desc = f"玩家抽到 {new_card} 总点数 {p_val} 爆牌啦，庄家弥酱 ({b_val}点) 获胜！"
            settle_match_outcome(sess, "bot_win", desc, is_private, extra_multiplier=(1.4 if st.get("doubled") else 1.0))
            summary = bj_summary(sess, desc, show_all_bot=True)
            clear_active_game(sess["chat_id"])
            return {
                "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
                "reply_text": f"抽到 {new_card}… {p_val} 点爆牌咯~",
                "should_trigger_llm_hook": True, "board_summary_for_llm": summary, "game_over": True,
            }

        if action == "hit" and p_val < 21:
            save_active_game(sess)
            img = render_blackjack_table(p_hand, b_hand, hide_bot_hole=True, player_score=p_val, bot_score_display="?")
            return {
                "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
                "reply_text": situational_line("blackjack", "HIT_SAFE", {"new_card": new_card, "p_val": p_val}),
                "should_trigger_llm_hook": bool(has_chitchat or p_val >= 20),
                "board_summary_for_llm": bj_summary(sess, f"玩家要牌抽中 {new_card}，手牌 {p_val} 点。"),
                "game_over": False,
            }

    p_val, _ = bj_hand_value(p_hand)
    hit_soft17 = (sess["difficulty"] == "hard") or (get_mood_playstyle()["w_atk"] > 1.2)
    while True:
        b_val, is_soft = bj_hand_value(b_hand)
        if b_val < 17 or (b_val == 17 and is_soft and hit_soft17):
            b_hand.append(deck.pop())
        else:
            break
    b_val, _ = bj_hand_value(b_hand)

    img = render_blackjack_table(p_hand, b_hand, hide_bot_hole=False, player_score=p_val, bot_score_display=f"{b_val} pts", status_title="Cat Blackjack - Showdown!")
    if b_val > 21:
        outcome, desc = "user_win", f"弥酱摸牌到 {b_val} 点爆牌了！玩家以 {p_val} 点稳稳拿下！"
    elif p_val > b_val:
        outcome, desc = "user_win", f"摊牌对比：玩家 {p_val} 点 > 弥酱 {b_val} 点，玩家获胜！"
    elif b_val > p_val:
        outcome, desc = "bot_win", f"摊牌对比：弥酱 {b_val} 点 > 玩家 {p_val} 点，弥酱获胜！"
    else:
        outcome, desc = "draw", f"双方都是 {p_val} 点，握手言和平局！"

    mult = 1.5 if st.get("doubled") else 1.0
    if outcome == "user_win" and st.get("fortune", {}).get("is_great_luck"):
        mult *= 1.2
    settle_match_outcome(sess, outcome, desc, is_private, extra_multiplier=mult)
    summary = bj_summary(sess, desc, show_all_bot=True)
    clear_active_game(sess["chat_id"])
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": img,
        "reply_text": desc, "should_trigger_llm_hook": True,
        "board_summary_for_llm": summary, "game_over": True,
    }


def gen_1a2b_secret() -> str:
    digits = list("0123456789")
    random.shuffle(digits)
    return "".join(digits[:4])


def _eval_1a2b(secret: str, guess: str) -> Tuple[int, int]:
    a = sum(1 for i in range(4) if secret[i] == guess[i])
    b = sum(1 for ch in guess if ch in secret) - a
    return a, b


def summary_1a2b(sess: dict, event_desc: str) -> str:
    st = sess["state"]
    hist = [f"{i + 1}. {g['guess']} -> {g['a']}A{g['b']}B" for i, g in enumerate(st["guesses"])]
    return (
        f"【当前桌游：1A2B破译密码（{DIFF_NAMES_CN.get(sess['difficulty'], '认真')}）】\n"
        f"- 已猜 {len(st['guesses'])}/{st['max_attempts']} 次\n"
        f"- 历史猜测：{' | '.join(hist) if hist else '暂无'}\n"
        f"- 动态：{event_desc}"
    )


def step_1a2b(sess: dict, guess: str, has_chitchat: bool) -> Dict[str, Any]:
    if len(set(guess)) != 4:
        return {
            "ok": False, "handled": True, "action_type": "INVALID_MOVE", "image_path": None,
            "reply_text": f"密码里的 4 个数字是互不重复的哦，{guess} 里有重复数字，换一个试试~",
            "should_trigger_llm_hook": False, "board_summary_for_llm": "", "game_over": False,
        }

    st = sess["state"]
    secret = st["secret"]
    a, b = _eval_1a2b(secret, guess)
    st["guesses"].append({"guess": guess, "a": a, "b": b})
    sess["turn_count"] = len(st["guesses"])
    sess["updated_at"] = time.time()
    left = st["max_attempts"] - len(st["guesses"])
    is_private = bool(sess.get("is_private"))

    if a == 4 or left <= 0:
        outcome = "user_win" if a == 4 else "bot_win"
        desc = (
            f"玩家第 {len(st['guesses'])} 次尝试 {guess} → 4A0B 成功破译密码！" if a == 4
            else f"机会用完啦~ 最后一次猜 {guess} ({a}A{b}B)，正确密码是 {secret}！"
        )
        reply = (
            f"4A0B！第 {len(st['guesses'])} 次就破译出来啦，好厉害✨" if a == 4
            else f"{a}A{b}B… 次数用光咯！正确密码其实是 {secret} 呢~"
        )
        settle_match_outcome(sess, outcome, desc, is_private)
        summary = summary_1a2b(sess, desc)
        clear_active_game(sess["chat_id"])
        return {
            "ok": True, "handled": True, "action_type": "MOVE", "image_path": None,
            "reply_text": reply, "should_trigger_llm_hook": True,
            "board_summary_for_llm": summary, "game_over": True,
        }

    hint_msg = ""
    if sess["difficulty"] == "easy" and len(st["guesses"]) == 4 and not st.get("hint_given"):
        st["hint_given"] = True
        idx = random.randint(0, 3)
        hint_msg = f"（看你推理这么认真，悄悄透个小提示：第 {idx + 1} 位数字是 {secret[idx]} 哦~）"

    save_active_game(sess)
    tag = "ZERO_HIT" if (a == 0 and b == 0) else ("CLOSE" if (a >= 2 or (a + b) == 4) else "PROGRESS")
    reply_line = situational_line("1a2b", tag, {"guess": guess, "a": a, "b": b, "left": left}) + hint_msg
    return {
        "ok": True, "handled": True, "action_type": "MOVE", "image_path": None,
        "reply_text": reply_line,
        "should_trigger_llm_hook": bool((a >= 3) or (left == 1) or has_chitchat or (sess["turn_count"] % 5 == 0)),
        "board_summary_for_llm": summary_1a2b(sess, f"猜 {guess} 得 {a}A{b}B，剩 {left} 次机会。{hint_msg}"),
        "game_over": False,
    }
