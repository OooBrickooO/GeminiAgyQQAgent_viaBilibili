"""
social/memes.py — 本地表情包仓库管理

管理 memes.json 与 memes/ 目录：入库、检索、概要与列表。
"""
import os
import json
import uuid
import datetime
import random
from config import MEMES_FILE, MEMES_DIR


def get_all_memes_summary(limit: int = 6, brief_mode: bool = True) -> str:
    """
    获取已收藏表情包仓库的简要概要（供 Prompt 注入）。
    - brief_mode=True（默认）：仅注入总数量与简短标签名（不含长描述），
      避免表情包画面长描述在首轮唤醒时臃肿或触发云端预审误判；
    - brief_mode=False：包含标签与描述。
    """
    if os.path.exists(MEMES_FILE):
        try:
            with open(MEMES_FILE, "r", encoding="utf-8") as f:
                memes = json.load(f)
                if memes:
                    items = []
                    for m in memes[-limit:]:
                        tag = m.get("tag", "表情")
                        if brief_mode:
                            items.append(f"【{tag}】")
                        else:
                            desc = m.get("description", "")
                            items.append(f"【{tag}】({desc})")
                    return (
                        f"【弥酱已收藏表情包仓库（共{len(memes)}张，可用 send_meme(tag_or_desc=...) 发送，或调 manage_meme_vault(action='list') 查看全部）】：\n"
                        f"近期常用标签：{' | '.join(items)}"
                    )
        except Exception:
            pass
    return ""


def list_all_memes() -> list:
    """返回全部表情包列表"""
    if os.path.exists(MEMES_FILE):
        try:
            with open(MEMES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _file_md5(path: str) -> str:
    """计算文件 MD5，失败返回空串"""
    import hashlib
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def find_meme_by_md5(md5: str) -> dict | None:
    """按文件内容 MD5 在仓库中查重；旧记录缺少 md5 字段时按其物理文件惰性补算"""
    if not md5:
        return None
    for m in list_all_memes():
        stored = m.get("md5")
        if not stored:
            fn = m.get("filename")
            if fn:
                stored = _file_md5(os.path.join(MEMES_DIR, fn))
        if stored and stored == md5:
            return m
    return None


def save_meme_record(local_img_path: str, tag: str, description: str) -> dict:
    """将图片正式收录进表情包仓库，返回新记录；若仓库已存在内容完全相同的图片，则直接复用已有记录（不重复拷盘）"""
    import shutil

    md5 = _file_md5(local_img_path)
    existing = find_meme_by_md5(md5)
    if existing:
        return {**existing, "_duplicate": True}

    meme_id = f"meme_{uuid.uuid4().hex[:8]}"
    ext = os.path.splitext(local_img_path)[1] or ".jpg"
    target_filename = f"{meme_id}{ext}"
    target_path = os.path.join(MEMES_DIR, target_filename)

    shutil.copy2(local_img_path, target_path)

    memes = []
    if os.path.exists(MEMES_FILE):
        try:
            with open(MEMES_FILE, "r", encoding="utf-8") as f:
                memes = json.load(f)
        except Exception:
            memes = []

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_item = {
        "id": meme_id,
        "filename": target_filename,
        "tag": tag.strip(),
        "description": description.strip(),
        "created_at": now_str,
        "use_count": 0,
        "md5": md5
    }
    memes.append(new_item)

    with open(MEMES_FILE, "w", encoding="utf-8") as f:
        json.dump(memes, f, ensure_ascii=False, indent=2)

    return new_item


def find_meme_by_keyword(tag_or_desc: str = "") -> dict:
    """按标签或描述关键词模糊检索表情包，未找到则随机"""
    memes = list_all_memes()
    if not memes:
        return None

    target_meme = None
    if tag_or_desc:
        kw = tag_or_desc.lower().strip()
        # 优先精准匹配 tag
        for m in memes:
            if kw in m.get("tag", "").lower() or m.get("tag", "").lower() in kw:
                target_meme = m
                break
        # 其次匹配 description
        if not target_meme:
            for m in memes:
                if kw in m.get("description", "").lower():
                    target_meme = m
                    break

    if not target_meme:
        target_meme = random.choice(memes)

    return target_meme


def increment_meme_use_count(meme_item: dict):
    """增加指定表情包的使用计数并落盘"""
    memes = list_all_memes()
    for m in memes:
        if m.get("id") == meme_item.get("id"):
            m["use_count"] = m.get("use_count", 0) + 1
            break
    try:
        with open(MEMES_FILE, "w", encoding="utf-8") as f:
            json.dump(memes, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def delete_meme_record(identifier: str) -> dict | None:
    """根据 ID 或标签删除指定的表情包，并删除对应物理图片"""
    if not os.path.exists(MEMES_FILE):
        return None
    try:
        with open(MEMES_FILE, "r", encoding="utf-8") as f:
            memes = json.load(f)
    except Exception:
        return None

    clean_id = identifier.strip().lower()
    target_idx = None
    target_meme = None
    for idx, m in enumerate(memes):
        if m.get("id", "").lower() == clean_id or m.get("tag", "").lower() == clean_id:
            target_idx = idx
            target_meme = m
            break

    if target_idx is not None and target_meme:
        memes.pop(target_idx)
        try:
            with open(MEMES_FILE, "w", encoding="utf-8") as f:
                json.dump(memes, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 同步删除本地物理文件
        try:
            filename = target_meme.get("filename")
            if filename:
                p = os.path.join(MEMES_DIR, filename)
                if os.path.exists(p):
                    os.remove(p)
        except Exception:
            pass
        return target_meme
    return None

