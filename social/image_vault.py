"""
social/image_vault.py — 消息图片嗅探与 [图片#img_X] 锚点池

解析群聊文本中的 [CQ:image,...] ，生成简短锚点并缓存元信息；
负责从 URL 或 OneBot /get_image 下载图片到本地 image_cache/。
"""
import os
import re
import html
import json
import time
import hashlib
import httpx
from config import IMAGE_VAULT_FILE, IMAGE_CACHE_DIR, ONEBOT_HTTP_URL, NAPCAT_TOKEN

# 内存中的图片引用池
IMAGE_VAULT = {}
IMAGE_COUNTER = 0


def load_image_vault():
    """从磁盘加载图片引用池（原地刷新字典，保证其他模块 from-import 持有的引用保持同步）"""
    global IMAGE_COUNTER
    if os.path.exists(IMAGE_VAULT_FILE):
        try:
            with open(IMAGE_VAULT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            IMAGE_VAULT.clear()
            IMAGE_VAULT.update(data)
            if IMAGE_VAULT:
                nums = [int(k.split('_')[-1]) for k in IMAGE_VAULT.keys() if '_' in k and k.split('_')[-1].isdigit()]
                if nums:
                    IMAGE_COUNTER = max(IMAGE_COUNTER, max(nums))
        except Exception:
            pass


def save_image_vault():
    """持久化图片引用池（先写临时文件再原子替换，避免 bot 主进程与 MCP 子进程并发读写时读到半截文件）"""
    try:
        tmp_path = f"{IMAGE_VAULT_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(IMAGE_VAULT, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, IMAGE_VAULT_FILE)
    except Exception:
        pass


# 启动时立即加载
load_image_vault()


def _image_dedup_key(params: dict, clean_url: str) -> str:
    """提取图片的稳定去重键：优先 CQ 的 file 字段（通常为内容 md5 文件名），其次 URL 中的 fileid，最后退化为完整 URL"""
    file_val = (params.get('file') or '').strip()
    if file_val and not file_val.startswith(('http://', 'https://', 'base64://')):
        return f"file:{file_val.lower()}"
    if clean_url:
        m = re.search(r'[?&]fileid=([^&]+)', clean_url)
        if m:
            return f"fileid:{m.group(1)}"
        return f"url:{clean_url}"
    return ""


def file_md5(path: str) -> str:
    """计算本地文件的 MD5，失败返回空串"""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def get_image_status(img_id: str) -> str:
    """读取图片处理状态（实时从磁盘刷新，跨进程可见）：pending / forwarded / saved；未知图片返回空串"""
    clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
    load_image_vault()
    info = IMAGE_VAULT.get(clean_id)
    if not info:
        return ""
    return info.get("status", "pending")


def is_image_forward_active(img_id: str, max_age_sec: float = 900.0) -> bool:
    """判断转发给主人把关的表情包是否仍处于近期有效待表态窗口内（默认15分钟内且状态为forwarded）"""
    clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
    load_image_vault()
    info = IMAGE_VAULT.get(clean_id)
    if not info or info.get("status") != "forwarded":
        return False
    ts = float(info.get("status_time") or info.get("time") or 0.0)
    if ts <= 0.0:
        return False
    return (time.time() - ts) <= max_age_sec


def mark_image_status(img_id: str, status: str, md5: str = ""):
    """回写图片处理状态（read-modify-write，供 MCP 子进程调用）"""
    clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
    load_image_vault()
    info = IMAGE_VAULT.get(clean_id)
    if not info:
        return
    info["status"] = status
    info["status_time"] = time.time()
    if md5:
        info["md5"] = md5
    save_image_vault()


def find_handled_duplicate(img_id: str, md5: str) -> dict | None:
    """在图片池中查找与 md5 内容完全相同、且已经处理过（已转发/已收藏）的其他图片记录"""
    if not md5:
        return None
    clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
    load_image_vault()
    for k, v in IMAGE_VAULT.items():
        if k != clean_id and v.get("md5") == md5 and v.get("status", "pending") in ("forwarded", "saved"):
            return v
    return None


def process_message_images(raw_text: str, sender_id: str, nickname: str, group_id, relation_tag_func=None):
    """
    嗅探消息中的 [CQ:image, ...]，生成 [图片#img_X] 简短锚点并缓存元信息到 IMAGE_VAULT

    - 同一张图（按 file / fileid 去重键）重复出现时复用历史 img_id，不再生成新编号；
    - 已处理（已转发/已收藏）的图片不再向大模型作为“待处理新图”暴露。
    relation_tag_func: 可选回调，接收 user_id_str 返回关系标签字符串 (注入以避免循环引用)
    """
    global IMAGE_COUNTER
    image_pattern = r'\[CQ:image,([^\]]+)\]'
    matches = list(re.finditer(image_pattern, raw_text))
    if not matches:
        return raw_text, []

    # 先同步磁盘，避免覆盖 MCP 子进程回写的处理状态
    load_image_vault()

    replaced_text = raw_text
    extracted_imgs = []

    for match in matches:
        full_tag = match.group(0)
        params_str = match.group(1)
        params = {}
        for p in params_str.split(','):
            if '=' in p:
                k, v = p.split('=', 1)
                params[k.strip()] = v.strip()

        # 兼容处理：url 可能在 url 字段，也可能在 file 字段，并且 CQ 码中的 & 会被转义为 &amp;
        raw_url = params.get('url', '')
        if not raw_url and params.get('file', '').startswith('http'):
            raw_url = params.get('file', '')
        clean_url = html.unescape(raw_url) if raw_url else ""

        relation = ""
        if relation_tag_func:
            relation = relation_tag_func(str(sender_id))

        dedup_key = _image_dedup_key(params, clean_url)
        existing_id = None
        if dedup_key:
            for k, v in IMAGE_VAULT.items():
                if v.get("dedup_key") == dedup_key:
                    existing_id = k
                    break

        if existing_id:
            # 重复图片：复用旧编号。仍处于待处理状态时刷新来源与时间（使其继续出现在当前群的近期图片池）；已处理的保持原状态不变。
            info = IMAGE_VAULT[existing_id]
            if info.get("status", "pending") == "pending":
                info["time"] = time.time()
                info["sender_id"] = str(sender_id)
                info["sender_nick"] = nickname
                info["group_id"] = str(group_id) if group_id else ""
                info["relation"] = relation
            replaced_text = replaced_text.replace(full_tag, f"[图片#{existing_id}]")
            continue

        IMAGE_COUNTER += 1
        img_id = f"img_{IMAGE_COUNTER}"

        info = {
            "id": img_id,
            "url": clean_url,
            "file": params.get('file', ''),
            "sender_id": str(sender_id),
            "sender_nick": nickname,
            "group_id": str(group_id) if group_id else "",
            "time": time.time(),
            "relation": relation,
            "status": "pending",
            "dedup_key": dedup_key
        }
        IMAGE_VAULT[img_id] = info
        extracted_imgs.append(info)
        replaced_text = replaced_text.replace(full_tag, f"[图片#{img_id}]")

        # 异步预下载图片到本地 image_cache/，确保大模型看图时秒级就绪
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(resolve_and_download_image(img_id))
        except Exception:
            pass

    if len(IMAGE_VAULT) > 1000:
        keys = list(IMAGE_VAULT.keys())
        for k in keys[:-1000]:
            IMAGE_VAULT.pop(k, None)
            old_file = os.path.join(IMAGE_CACHE_DIR, f"{k}.jpg")
            if os.path.exists(old_file):
                try:
                    os.remove(old_file)
                except Exception:
                    pass

    save_image_vault()
    return replaced_text, extracted_imgs


def get_recent_images_summary(group_id_str: str, limit: int = 3) -> str:
    """获取最近 15 分钟内指定群的【待处理】图片引用池概要（已转发/已收藏的图片不再暴露，避免反复申请/偷图）"""
    load_image_vault()
    recent = []
    now = time.time()
    for img_id in reversed(list(IMAGE_VAULT.keys())):
        item = IMAGE_VAULT[img_id]
        if item.get("status", "pending") != "pending":
            continue
        if item.get("group_id") == str(group_id_str) and (now - item.get("time", now)) <= 900:
            age_mins = int((now - item.get("time", now)) / 60)
            tag = "刚刚" if age_mins < 1 else f"{age_mins}分钟前"
            rel = item.get("relation", "[群友]")
            nick = item.get("sender_nick", "")
            cache_file = os.path.join(IMAGE_CACHE_DIR, f"{img_id}.jpg")
            path_hint = f" (本地文件已就绪: {cache_file})" if os.path.exists(cache_file) else ""
            recent.append(f"- [图片#{img_id}]：来自 {rel}{nick} ({tag}){path_hint}")
            if len(recent) >= limit:
                break
    if recent:
        return "【近期群聊图片引用池（可用 inspect_image 工具或 view_file 查看画面）】：\n" + "\n".join(recent)
    return ""


async def resolve_and_download_image(img_id: str):
    """根据 img_id (如 'img_1' 或本地路径) 解析并下载图片到 image_cache/ 返回本地路径"""
    clean_id = img_id.replace("[图片#", "").replace("]", "").replace("#", "").strip()
    if os.path.exists(clean_id):
        return clean_id

    vault = {}
    if os.path.exists(IMAGE_VAULT_FILE):
        try:
            with open(IMAGE_VAULT_FILE, "r", encoding="utf-8") as f:
                vault = json.load(f)
        except Exception:
            vault = {}

    info = vault.get(clean_id)
    if not info:
        return None

    cache_path = os.path.join(IMAGE_CACHE_DIR, f"{clean_id}.jpg")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        return cache_path

    url = info.get("url")
    file_name = info.get("file")

    if url:
        url = html.unescape(url)

    # 1. 尝试从 URL 直接下载 (trust_env=False 避免腾讯国内CDN图片误走海外节点)
    if url and url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 0:
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                    return cache_path
        except Exception:
            pass

    # 2. 尝试从 OneBot 的 /get_image 获取
    if file_name:
        try:
            headers = {"Authorization": f"Bearer {NAPCAT_TOKEN}"}
            async with httpx.AsyncClient(timeout=15.0, headers=headers, trust_env=False) as client:
                resp = await client.post(f"{ONEBOT_HTTP_URL}/get_image", json={"file": file_name})
                data = resp.json()
                if data.get("status") == "ok":
                    img_data = data.get("data", {})
                    f_url = img_data.get("url")
                    f_path = img_data.get("file")
                    if f_path and os.path.exists(f_path):
                        import shutil
                        shutil.copy2(f_path, cache_path)
                        return cache_path
                    elif f_url and f_url.startswith("http"):
                        f_url = html.unescape(f_url)
                        dl_resp = await client.get(f_url)
                        if dl_resp.status_code == 200 and len(dl_resp.content) > 0:
                            with open(cache_path, "wb") as f:
                                f.write(dl_resp.content)
                            return cache_path
        except Exception:
            pass

    return None


def cleanup_image_cache(max_keep: int = 1000, max_age_hours: int = 168) -> int:
    """
    【图片缓存清理治理】
    自动清理 image_cache/ 目录下过期的临时群聊图片。
    - 针对 img_*.jpg 等临时嗅探图进行清理
    - 严格保留白名单文件（如 bili_login_qr.png）
    - 超过 max_age_hours (默认1周/168小时) 或超过 max_keep (默认保留最新1000张) 的文件自动从磁盘移除
    返回清理的文件数量
    """
    if not os.path.exists(IMAGE_CACHE_DIR):
        return 0

    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    cleaned = 0

    img_files = []
    try:
        for entry in os.scandir(IMAGE_CACHE_DIR):
            if not entry.is_file():
                continue
            name = entry.name
            if name.lower().startswith("img_") and name.lower().endswith((".jpg", ".png", ".jpeg", ".gif", ".webp")):
                try:
                    stat = entry.stat()
                    img_files.append((entry.path, stat.st_mtime))
                except Exception:
                    pass
    except Exception as e:
        print(f"⚠️ [image_cache] 扫描图片缓存目录异常: {e}")
        return 0

    # 按修改时间从新到旧排序
    img_files.sort(key=lambda x: x[1], reverse=True)

    keep_count = 0
    for path, mtime in img_files:
        keep_count += 1
        if keep_count > max_keep or mtime < cutoff:
            try:
                os.remove(path)
                cleaned += 1
            except Exception:
                pass

    if cleaned > 0:
        print(f"🧹 [image_cache] 已自动清理 {cleaned} 张过期群聊图片缓存 (保留最新 {len(img_files) - cleaned} 张)")
    return cleaned


# 启动时执行一次全量缓存整理，清理以往积压的历史过期图片 (上限1000张/1周)
cleanup_image_cache(max_keep=1000, max_age_hours=168)
