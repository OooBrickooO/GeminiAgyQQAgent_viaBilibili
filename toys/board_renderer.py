"""
toys/board_renderer.py — 基于 Pillow 的轻量猫爪主题视觉棋盘与牌桌渲染器

设计原则：
- 清爽纯粹的棋盘与桌面视觉：仅展示棋盘/手牌、坐标轴、最新落子高亮、关键防点/合法步及简明比分；
- 不绘制冗长文字心情 HUD，情绪与吐槽交由引擎语义台词与大模型实时互动表达；
- 自动清理过期渲染缓存，避免磁盘膨胀。
"""
import os
import time
import glob
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
from config import GAME_RENDERS_DIR

_FONT_CACHE = {}


def _load_font(size: int = 16, bold: bool = False) -> ImageFont.ImageFont:
    """跨平台自动探测中英文字体，兜底安全回退"""
    cache_key = (size, bold)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    candidates = [
        # Linux CJK / Noto / WenQuanYi
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                fnt = ImageFont.truetype(path, size=size)
                _FONT_CACHE[cache_key] = fnt
                return fnt
            except Exception:
                continue

    try:
        fnt = ImageFont.load_default(size=size)
    except TypeError:
        fnt = ImageFont.load_default()
    _FONT_CACHE[cache_key] = fnt
    return fnt


def _cleanup_old_renders(max_age_sec: float = 1800.0):
    """顺手清理半小时前的旧棋盘图片，避免缓存目录堆积"""
    try:
        now = time.time()
        for p in glob.glob(os.path.join(GAME_RENDERS_DIR, "*.png")):
            try:
                if now - os.path.getmtime(p) > max_age_sec:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _save_board_image(img: Image.Image, prefix: str) -> str:
    os.makedirs(GAME_RENDERS_DIR, exist_ok=True)
    _cleanup_old_renders()
    filename = f"{prefix}_{int(time.time() * 1000)}.png"
    out_path = os.path.join(GAME_RENDERS_DIR, filename)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


def _draw_cat_paw_emblem(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, color: tuple):
    """在棋子中央绘制精致的小猫肉垫轮廓/标记"""
    if radius < 7:
        return
    pad_r = radius * 0.28
    draw.ellipse([cx - pad_r, cy - pad_r * 0.65 + radius * 0.14, cx + pad_r, cy + pad_r * 0.9 + radius * 0.14], fill=color)
    toe_r = radius * 0.12
    offsets = [(-0.34, -0.18), (-0.12, -0.34), (0.12, -0.34), (0.34, -0.18)]
    for ox, oy in offsets:
        tx, ty = cx + ox * radius, cy + oy * radius
        draw.ellipse([tx - toe_r, ty - toe_r, tx + toe_r, ty + toe_r], fill=color)


def render_gomoku_board(
    board: List[List[int]],
    last_user_move: Optional[Tuple[int, int]] = None,
    last_bot_move: Optional[Tuple[int, int]] = None,
    win_line: Optional[List[Tuple[int, int]]] = None,
    threat_points: Optional[List[Tuple[int, int]]] = None,
) -> str:
    """
    绘制 680x680 暖木色猫爪五子棋棋盘 (15x15)：
    - board 值约定: 0=空, 1=黑子(玩家), 2=白子(弥酱)
    - 四周均清晰标注列(A-O)与行(1-15)
    - 高亮双方最新落子、五连胜线与关键活三/冲四威胁防点
    """
    size = 680
    n = 15
    margin = 52
    step = (size - 2 * margin) / (n - 1)

    img = Image.new("RGB", (size, size), (235, 196, 138))
    draw = ImageDraw.Draw(img, "RGBA")

    # 微妙木纹水平底纹与外框阴影
    for y in range(0, size, 6):
        shade = 3 if (y // 6) % 2 == 0 else -3
        draw.line([(0, y), (size, y)], fill=(235 + shade, 196 + shade, 138 + shade, 60), width=2)

    board_x0 = margin - 20
    board_y0 = margin - 20
    board_x1 = size - margin + 20
    board_y1 = size - margin + 20
    draw.rectangle([board_x0, board_y0, board_x1, board_y1], outline=(108, 72, 38), width=3)

    # 棋盘网格线
    grid_color = (96, 62, 32)
    for i in range(n):
        pos = margin + i * step
        w = 2 if i in (0, n - 1) else 1
        draw.line([(margin, pos), (size - margin, pos)], fill=grid_color, width=w)
        draw.line([(pos, margin), (pos, size - margin)], fill=grid_color, width=w)

    # 星位与天元
    star_coords = [(3, 3), (3, 11), (11, 3), (11, 11), (7, 7)]
    for r, c in star_coords:
        cx = margin + c * step
        cy = margin + r * step
        draw.ellipse([cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5], fill=grid_color)

    # 四周坐标标注 (列 A-O，行 1-15)
    coord_font = _load_font(15, bold=True)
    cols = "ABCDEFGHIJKLMNO"
    for i in range(n):
        cx = margin + i * step
        cy = margin + i * step
        col_lbl = cols[i]
        row_lbl = str(i + 1)
        draw.text((cx, margin - 33), col_lbl, fill=(85, 52, 24), font=coord_font, anchor="mm")
        draw.text((cx, size - margin + 33), col_lbl, fill=(85, 52, 24), font=coord_font, anchor="mm")
        draw.text((margin - 33, cy), row_lbl, fill=(85, 52, 24), font=coord_font, anchor="mm")
        draw.text((size - margin + 33, cy), row_lbl, fill=(85, 52, 24), font=coord_font, anchor="mm")

    # 绘制关键威胁空点微光圈（活三/冲四提示）
    if threat_points:
        for tr, tc in threat_points:
            if 0 <= tr < n and 0 <= tc < n and board[tr][tc] == 0:
                tx = margin + tc * step
                ty = margin + tr * step
                draw.ellipse(
                    [tx - step * 0.34, ty - step * 0.34, tx + step * 0.34, ty + step * 0.34],
                    outline=(235, 75, 110, 175),
                    width=2,
                )

    # 绘制棋子
    stone_r = step * 0.43
    for r in range(n):
        for c in range(n):
            val = board[r][c]
            if val == 0:
                continue
            cx = margin + c * step
            cy = margin + r * step
            # 棋子底部投影
            draw.ellipse(
                [cx - stone_r + 2, cy - stone_r + 3, cx + stone_r + 2, cy + stone_r + 3],
                fill=(45, 28, 12, 85),
            )
            if val == 1:
                # 黑子（玩家）
                draw.ellipse([cx - stone_r, cy - stone_r, cx + stone_r, cy + stone_r], fill=(32, 34, 42))
                draw.ellipse(
                    [cx - stone_r * 0.55, cy - stone_r * 0.65, cx - stone_r * 0.1, cy - stone_r * 0.2],
                    fill=(95, 100, 115, 130),
                )
            else:
                # 弥酱白子（带淡粉猫爪肉垫底纹）
                draw.ellipse(
                    [cx - stone_r, cy - stone_r, cx + stone_r, cy + stone_r],
                    fill=(252, 249, 244),
                    outline=(195, 185, 170),
                    width=1,
                )
                _draw_cat_paw_emblem(draw, cx, cy, stone_r * 0.68, (242, 165, 185, 165))

    # 高亮玩家最近落子（青蓝色准星环）
    if last_user_move:
        ur, uc = last_user_move
        if 0 <= ur < n and 0 <= uc < n and board[ur][uc] != 0:
            ux, uy = margin + uc * step, margin + ur * step
            hr = stone_r * 0.55
            draw.ellipse([ux - hr, uy - hr, ux + hr, uy + hr], outline=(80, 185, 255), width=2)

    # 高亮弥酱最新落子（樱桃粉/红色十字标识 + 外环）
    if last_bot_move:
        br, bc = last_bot_move
        if 0 <= br < n and 0 <= bc < n and board[br][bc] != 0:
            bx, by = margin + bc * step, margin + br * step
            hr = stone_r + 2.5
            draw.ellipse([bx - hr, by - hr, bx + hr, by + hr], outline=(235, 68, 95), width=3)
            draw.line([(bx - 5, by), (bx + 5, by)], fill=(235, 68, 95), width=2)
            draw.line([(bx, by - 5), (bx, by + 5)], fill=(235, 68, 95), width=2)

    # 五连胜利线高亮
    if win_line and len(win_line) >= 2:
        p1 = win_line[0]
        p2 = win_line[-1]
        x1, y1 = margin + p1[1] * step, margin + p1[0] * step
        x2, y2 = margin + p2[1] * step, margin + p2[0] * step
        draw.line([(x1, y1), (x2, y2)], fill=(255, 200, 45, 220), width=6)

    return _save_board_image(img, "gomoku")


def render_othello_board(
    board: List[List[int]],
    valid_moves: Optional[List[Tuple[int, int]]] = None,
    last_user_move: Optional[Tuple[int, int]] = None,
    last_bot_move: Optional[Tuple[int, int]] = None,
    flipped_cells: Optional[List[Tuple[int, int]]] = None,
) -> str:
    """
    绘制 600x640 祖母绿黑白棋棋盘 (8x8)：
    - board 值约定: 0=空, 1=黑子(玩家), 2=白子(弥酱)
    - 显示玩家合法可落子小绿点，高亮最新落子与被翻转棋子，底部展示简洁双方子数对比
    """
    w, h = 600, 640
    n = 8
    margin = 52
    cell = (w - 2 * margin) / n

    img = Image.new("RGB", (w, h), (22, 62, 48))
    draw = ImageDraw.Draw(img, "RGBA")

    # 棋盘区域双色格纹 (经典祖母绿)
    for r in range(n):
        for c in range(n):
            x0 = margin + c * cell
            y0 = margin + r * cell
            x1 = x0 + cell
            y1 = y0 + cell
            bg = (34, 118, 78) if (r + c) % 2 == 0 else (29, 104, 68)
            draw.rectangle([x0, y0, x1, y1], fill=bg, outline=(16, 56, 38), width=2)

    # 外边框
    draw.rectangle(
        [margin - 2, margin - 2, w - margin + 2, margin + n * cell + 2],
        outline=(210, 185, 130),
        width=3,
    )

    # 坐标标注 (A-H, 1-8)
    coord_font = _load_font(17, bold=True)
    cols = "ABCDEFGH"
    for i in range(n):
        cx = margin + (i + 0.5) * cell
        cy = margin + (i + 0.5) * cell
        draw.text((cx, margin - 24), cols[i], fill=(235, 228, 200), font=coord_font, anchor="mm")
        draw.text((margin - 24, cy), str(i + 1), fill=(235, 228, 200), font=coord_font, anchor="mm")
        draw.text((w - margin + 24, cy), str(i + 1), fill=(235, 228, 200), font=coord_font, anchor="mm")

    # 合法可落子提示点
    if valid_moves:
        dot_r = cell * 0.14
        for vr, vc in valid_moves:
            if 0 <= vr < n and 0 <= vc < n and board[vr][vc] == 0:
                vx = margin + (vc + 0.5) * cell
                vy = margin + (vr + 0.5) * cell
                draw.ellipse([vx - dot_r, vy - dot_r, vx + dot_r, vy + dot_r], fill=(165, 245, 185, 195))

    flipped_set = set(flipped_cells or [])
    black_cnt = 0
    white_cnt = 0
    stone_r = cell * 0.39

    for r in range(n):
        for c in range(n):
            val = board[r][c]
            if val == 0:
                continue
            if val == 1:
                black_cnt += 1
            else:
                white_cnt += 1

            cx = margin + (c + 0.5) * cell
            cy = margin + (r + 0.5) * cell
            draw.ellipse(
                [cx - stone_r + 2, cy - stone_r + 3, cx + stone_r + 2, cy + stone_r + 3],
                fill=(10, 30, 22, 110),
            )
            if val == 1:
                draw.ellipse([cx - stone_r, cy - stone_r, cx + stone_r, cy + stone_r], fill=(28, 30, 36), outline=(70, 75, 85), width=1)
            else:
                draw.ellipse([cx - stone_r, cy - stone_r, cx + stone_r, cy + stone_r], fill=(250, 247, 242), outline=(185, 180, 170), width=1)
                _draw_cat_paw_emblem(draw, cx, cy, stone_r * 0.62, (242, 165, 185, 160))

            if (r, c) in flipped_set:
                draw.ellipse([cx - stone_r * 0.45, cy - stone_r * 0.45, cx + stone_r * 0.45, cy + stone_r * 0.45], outline=(255, 215, 90, 200), width=2)

    if last_user_move:
        ur, uc = last_user_move
        if 0 <= ur < n and 0 <= uc < n and board[ur][uc] != 0:
            ux, uy = margin + (uc + 0.5) * cell, margin + (ur + 0.5) * cell
            draw.ellipse([ux - stone_r - 2, uy - stone_r - 2, ux + stone_r + 2, uy + stone_r + 2], outline=(90, 195, 255), width=2)

    if last_bot_move:
        br, bc = last_bot_move
        if 0 <= br < n and 0 <= bc < n and board[br][bc] != 0:
            bx, by = margin + (bc + 0.5) * cell, margin + (br + 0.5) * cell
            draw.ellipse([bx - stone_r - 2, by - stone_r - 2, bx + stone_r + 2, by + stone_r + 2], outline=(245, 78, 108), width=3)

    # 底部简洁比分栏
    bar_y = margin + n * cell + 36
    score_font = _load_font(20, bold=True)
    draw.ellipse([margin + 60, bar_y - 12, margin + 84, bar_y + 12], fill=(28, 30, 36), outline=(220, 220, 220), width=1)
    draw.text((margin + 96, bar_y), f"You: {black_cnt}", fill=(245, 245, 245), font=score_font, anchor="lm")

    draw.ellipse([w - margin - 205, bar_y - 12, w - margin - 181, bar_y + 12], fill=(250, 247, 242), outline=(180, 180, 180), width=1)
    draw.text((w - margin - 169, bar_y), f"Mi-chan: {white_cnt}", fill=(255, 225, 232), font=score_font, anchor="lm")

    return _save_board_image(img, "othello")


def render_blackjack_table(
    player_hand: List[str],
    bot_hand: List[str],
    hide_bot_hole: bool = True,
    player_score: int = 0,
    bot_score_display: str = "?",
    status_title: str = "Cat Blackjack",
) -> str:
    """
    绘制 600x400 质感墨绿天鹅绒扑克桌，展示弥酱庄家手牌与玩家手牌。
    牌面字符串格式例: '♠A', '♥10', '♦K', '♣7'
    """
    w, h = 600, 400
    img = Image.new("RGB", (w, h), (18, 72, 52))
    draw = ImageDraw.Draw(img, "RGBA")

    # 椭圆木桌边与绒面内框
    draw.rounded_rectangle([14, 14, w - 14, h - 14], radius=24, outline=(145, 98, 52), width=6)
    draw.rounded_rectangle([24, 24, w - 24, h - 24], radius=18, outline=(215, 185, 115, 120), width=2)

    title_font = _load_font(18, bold=True)
    label_font = _load_font(16, bold=True)
    card_font = _load_font(22, bold=True)
    small_font = _load_font(14, bold=False)

    draw.text((w // 2, 44), status_title, fill=(245, 225, 165), font=title_font, anchor="mm")

    def _draw_card_row(cards: List[str], top_y: int, hide_second: bool = False):
        card_w, card_h = 74, 108
        gap = 16
        total_w = len(cards) * card_w + max(0, len(cards) - 1) * gap
        start_x = (w - total_w) // 2
        for idx, card in enumerate(cards):
            x0 = start_x + idx * (card_w + gap)
            y0 = top_y
            x1 = x0 + card_w
            y1 = y0 + card_h
            draw.rounded_rectangle([x0 + 3, y0 + 4, x1 + 3, y1 + 4], radius=8, fill=(8, 28, 18, 115))
            if hide_second and idx == 1:
                draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=(235, 120, 148), outline=(255, 240, 244), width=2)
                draw.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y1 - 6], radius=5, outline=(255, 220, 230, 180), width=1)
                _draw_cat_paw_emblem(draw, (x0 + x1) / 2, (y0 + y1) / 2, 22, (255, 244, 248))
            else:
                draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill=(252, 250, 246), outline=(200, 195, 185), width=2)
                suit = card[0] if card else ""
                rank = card[1:] if len(card) > 1 else card
                ink = (210, 42, 58) if suit in ("♥", "♦") else (30, 32, 40)
                draw.text((x0 + 10, y0 + 12), f"{suit}{rank}", fill=ink, font=small_font, anchor="lt")
                draw.text(((x0 + x1) / 2, (y0 + y1) / 2 + 2), f"{suit}{rank}", fill=ink, font=card_font, anchor="mm")
                draw.text((x1 - 10, y1 - 10), rank, fill=ink, font=small_font, anchor="rb")

    # 弥酱（庄家）手牌区
    draw.text((w // 2, 76), f"Mi-chan (Dealer): {bot_score_display}", fill=(255, 220, 230), font=label_font, anchor="mm")
    _draw_card_row(bot_hand, 94, hide_second=hide_bot_hole)

    # 玩家手牌区
    draw.text((w // 2, 236), f"Your Hand: {player_score} pts", fill=(220, 248, 255), font=label_font, anchor="mm")
    _draw_card_row(player_hand, 254, hide_second=False)

    return _save_board_image(img, "blackjack")
