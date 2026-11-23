"""
social/bili_storyboard.py — V1 无字幕视频「故事板九宫格」真看画面引擎

灵感融合自：
- `SAOS-3624/dsh-see-now`（帧间视觉变化量打分选帧 + 多帧拼进一张网格图极低 Token 看画面）
- `c1overzz/dsh-bili-scroll`（无字幕视频调用 B站官方 videoshot 故事板雪碧图直接看懂画面）

优势：
- 零视频下载、零 ffmpeg 依赖，直接走 B 站官方 `/x/player/videoshot` 雪碧图；
- 用 Pillow 切帧 + 灰度下采样帧差筛选最多 9 张高信息量变化帧，拼成带编号与 MM:SS 时间戳的 3×3 九宫格 PNG；
- 供多模态模型直接调用 `view_file` 一眼看懂二游手书、猫猫视频、游戏高光操作！
"""
import os
import io
import glob
import time
import httpx
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
from config import IMAGE_CACHE_DIR
from social.bilibili_auth import load_bili_credential, load_bili_fingerprint


def _fmt_mmss(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _cleanup_old_storyboards(max_age_sec: float = 86400.0):
    try:
        now = time.time()
        for p in glob.glob(os.path.join(IMAGE_CACHE_DIR, "bili_storyboard_*.png")):
            try:
                if now - os.path.getmtime(p) > max_age_sec:
                    os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _frame_gray_sig(img: Image.Image) -> bytes:
    """将帧缩放到 32x18 灰度用于极速 L1 像素变化量计算"""
    return img.convert("L").resize((32, 18), Image.BILINEAR).tobytes()


def _frame_delta(sig_a: bytes, sig_b: bytes) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    total = sum(abs(a - b) for a, b in zip(sig_a, sig_b))
    return total / float(len(sig_a))


def _is_nearly_blank(sig: bytes) -> bool:
    if not sig:
        return True
    avg = sum(sig) / float(len(sig))
    if avg < 10.0 or avg > 248.0:
        return True
    var = sum(abs(v - avg) for v in sig) / float(len(sig))
    return var < 5.0


def select_diverse_frames_by_delta(
    frames: List[Tuple[int, Image.Image]],
    max_frames: int = 9,
) -> List[Tuple[int, Image.Image]]:
    """
    借鉴 dsh-see-now 的视觉变化量选帧策略：
    1) 过滤纯黑/纯白无效过场帧；
    2) 计算相邻帧视觉变化量 delta，跳过几乎静止的重复画面；
    3) 保证首尾画面覆盖的同时，优先挑出变化量最大的关键高能帧，并按时间顺序排列。
    """
    if not frames:
        return []
    if len(frames) <= max_frames:
        return frames

    sigs = [_frame_gray_sig(im) for _, im in frames]
    valid_indices = [i for i, s in enumerate(sigs) if not _is_nearly_blank(s)]
    if len(valid_indices) < max_frames:
        valid_indices = list(range(len(frames)))

    if len(valid_indices) <= max_frames:
        return [frames[i] for i in valid_indices]

    # 计算每一帧相对前一有效帧的视觉变化量
    deltas = {}
    prev_sig = sigs[valid_indices[0]]
    deltas[valid_indices[0]] = 999.0  # 保留开头建立场景
    for idx in valid_indices[1:]:
        d = _frame_delta(prev_sig, sigs[idx])
        deltas[idx] = d
        prev_sig = sigs[idx]

    # 必选首帧与尾帧，中间按时间分桶并在每桶内选视觉变化量最大的帧（兼顾全片均匀覆盖与高能瞬间）
    picked = {valid_indices[0], valid_indices[-1]}
    middle = valid_indices[1:-1]
    need = max_frames - len(picked)
    if need > 0 and middle:
        bucket_size = len(middle) / float(need)
        for b in range(need):
            lo = int(b * bucket_size)
            hi = max(lo + 1, int((b + 1) * bucket_size))
            bucket = middle[lo:hi]
            if not bucket:
                continue
            best_idx = max(bucket, key=lambda i: deltas.get(i, 0.0))
            picked.add(best_idx)

    # 若仍有空位，按全局最大 delta 补齐
    if len(picked) < max_frames:
        rest = sorted(
            [i for i in valid_indices if i not in picked],
            key=lambda i: deltas.get(i, 0.0),
            reverse=True,
        )
        for i in rest[: max_frames - len(picked)]:
            picked.add(i)

    ordered = sorted(picked)[:max_frames]
    return [frames[i] for i in ordered]


def pack_storyboard_grid(
    selected_frames: List[Tuple[int, Image.Image]],
    bvid: str = "",
    title: str = "",
    cell_w: int = 240,
    cell_h: int = 135,
    cols: int = 3,
) -> Image.Image:
    """将筛选后的最多 9 帧拼装成一张带编号与 MM:SS 时间戳的清晰 3×3 故事板网格图"""
    n = len(selected_frames)
    rows = max(1, (n + cols - 1) // cols)
    pad = 8
    header_h = 34
    total_w = cols * cell_w + (cols + 1) * pad
    total_h = header_h + rows * cell_h + (rows + 1) * pad

    canvas = Image.new("RGB", (total_w, total_h), (20, 22, 28))
    draw = ImageDraw.Draw(canvas, "RGBA")

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    header_txt = f"Bilibili Storyboard | {bvid} | {n} key frames"
    draw.text((pad, 10), header_txt, fill=(235, 240, 250), font=font)

    for idx, (ts_sec, frame_img) in enumerate(selected_frames):
        r = idx // cols
        c = idx % cols
        x0 = pad + c * (cell_w + pad)
        y0 = header_h + pad + r * (cell_h + pad)
        resized = frame_img.convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
        canvas.paste(resized, (x0, y0))

        # 边框与左上角编号+时间戳角标
        draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], outline=(70, 76, 92), width=1)
        badge = f"#{idx + 1} {_fmt_mmss(ts_sec)}"
        draw.rectangle([x0 + 4, y0 + 4, x0 + 74, y0 + 22], fill=(12, 14, 20, 195))
        draw.text((x0 + 8, y0 + 7), badge, fill=(255, 228, 120), font=font)

    return canvas


async def fetch_and_build_video_storyboard(
    bvid: str,
    cid: int = 0,
    title: str = "",
    credential=None,
    max_frames: int = 9,
) -> Dict[str, Any]:
    """
    从 B 站官方 `/x/player/videoshot` 拉取视频故事板雪碧图，按视觉变化量挑选最多 9 帧，
    拼装成单张九宫格 PNG 存入 image_cache/，返回绝对路径与时间戳清单。
    """
    cred = credential or load_bili_credential()
    fp = load_bili_fingerprint()
    cookie_parts = []
    if cred and getattr(cred, "sessdata", ""):
        cookie_parts.append(f"SESSDATA={cred.sessdata}")
    if cred and getattr(cred, "bili_jct", ""):
        cookie_parts.append(f"bili_jct={cred.bili_jct}")
    if cred and getattr(cred, "dedeuserid", ""):
        cookie_parts.append(f"DedeUserID={cred.dedeuserid}")
    b3 = (getattr(cred, "buvid3", "") if cred else "") or fp.get("buvid3", "")
    if b3:
        cookie_parts.append(f"buvid3={b3}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": f"https://www.bilibili.com/video/{bvid}",
    }
    if cookie_parts:
        headers["Cookie"] = "; ".join(cookie_parts)

    url = f"https://api.bilibili.com/x/player/videoshot?bvid={bvid}&index=1"
    if cid:
        url += f"&cid={int(cid)}"

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            rj = resp.json()
            if rj.get("code") != 0:
                return {"ok": False, "error": f"videoshot code={rj.get('code')}: {rj.get('message')}"}

            data = rj.get("data") or {}
            img_urls = data.get("image") or []
            if not img_urls:
                return {"ok": False, "error": "该视频暂无官方故事板雪碧图"}

            x_len = max(1, int(data.get("img_x_len", 10)))
            y_len = max(1, int(data.get("img_y_len", 10)))
            x_size = max(16, int(data.get("img_x_size", 160)))
            y_size = max(16, int(data.get("img_y_size", 90)))
            ts_index = data.get("index") or []

            # 最多取前 2 张雪碧图（最多 200 帧预览，覆盖绝大部分常规视频）
            frames: List[Tuple[int, Image.Image]] = []
            frame_cursor = 0
            for raw_u in img_urls[:2]:
                sheet_url = ("https:" + raw_u) if str(raw_u).startswith("//") else str(raw_u)
                sr = await client.get(sheet_url, headers=headers)
                if sr.status_code != 200 or not sr.content:
                    continue
                sheet_img = Image.open(io.BytesIO(sr.content)).convert("RGB")
                sw, sh = sheet_img.size
                for row in range(y_len):
                    for col in range(x_len):
                        left = col * x_size
                        top = row * y_size
                        if left + x_size > sw or top + y_size > sh:
                            continue
                        if ts_index and frame_cursor >= len(ts_index):
                            break
                        ts_val = int(ts_index[frame_cursor]) if frame_cursor < len(ts_index) else frame_cursor * 5
                        crop = sheet_img.crop((left, top, left + x_size, top + y_size))
                        frames.append((ts_val, crop))
                        frame_cursor += 1

        if not frames:
            return {"ok": False, "error": "故事板雪碧图切帧为空"}

        selected = select_diverse_frames_by_delta(frames, max_frames=max_frames)
        grid_img = pack_storyboard_grid(selected, bvid=bvid, title=title)

        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
        _cleanup_old_storyboards()
        out_path = os.path.join(IMAGE_CACHE_DIR, f"bili_storyboard_{bvid}.png")
        grid_img.save(out_path, format="PNG", optimize=True)

        return {
            "ok": True,
            "bvid": bvid,
            "image_path": out_path,
            "frame_count": len(selected),
            "timestamps": [_fmt_mmss(t) for t, _ in selected],
        }
    except Exception as e:
        return {"ok": False, "error": f"构建视频故事板异常: {type(e).__name__}: {e}"}
