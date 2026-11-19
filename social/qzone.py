"""
social/qzone.py — QQ 空间认证与协议封装

管理 Cookie 获取、bkn/g_tk 计算 (BKDR hash 5381)、
window.g_qzonetoken 爬取提取、发说说、点赞、发表评论的 HTTP 请求。
管理 qzone_history.json 的落盘与查询。
"""
import os
import re
import sys
import json
import time
import base64
import datetime
import httpx
from config import (
    ONEBOT_HTTP_URL, NAPCAT_TOKEN, BOT_QQ,
    QZONE_HISTORY_FILE, SEEN_FRIEND_FEEDS_FILE
)

# 缓存 Qzone 凭证 (cookies, g_tk, qzonetoken, uin, expire_at)
_QZONE_AUTH_CACHE = {
    "cookies": "",
    "g_tk": 0,
    "qzonetoken": "",
    "uin": BOT_QQ,
    "expire_at": 0.0
}


def calc_qzone_gtk(key: str) -> int:
    """计算 QQ 空间的 g_tk / bkn Hash"""
    h = 5381
    for ch in key:
        h += (h << 5) + ord(ch)
    return h & 0x7fffffff


async def get_qzone_auth() -> dict:
    """从 OneBot 动态获取 qzone.qq.com 的 Cookie 并计算 g_tk 和 qzonetoken"""
    global _QZONE_AUTH_CACHE
    now = time.time()
    if _QZONE_AUTH_CACHE["cookies"] and _QZONE_AUTH_CACHE["expire_at"] > now:
        return _QZONE_AUTH_CACHE

    try:
        headers = {"Authorization": f"Bearer {NAPCAT_TOKEN}"}
        async with httpx.AsyncClient(timeout=10.0, headers=headers, trust_env=False) as client:
            resp = await client.post(f"{ONEBOT_HTTP_URL}/get_cookies", json={"domain": "qzone.qq.com"})
            data = resp.json()
            if data.get("status") != "ok":
                return None
            cookies = data.get("data", {}).get("cookies", "")
            if not cookies:
                return None

            # 解析 p_skey 或 skey
            p_skey_match = re.search(r'p_skey=([^;]+)', cookies)
            skey_match = re.search(r'skey=([^;]+)', cookies)
            p_skey = p_skey_match.group(1) if p_skey_match else (skey_match.group(1) if skey_match else "")
            g_tk = calc_qzone_gtk(p_skey)

            # 解析 uin
            uin_match = re.search(r'uin=o?(\d+)', cookies)
            uin = uin_match.group(1) if uin_match else BOT_QQ

            # 访问空间首页提取 qzonetoken
            qzonetoken = ""
            try:
                page_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Cookie": cookies
                }
                async with httpx.AsyncClient(timeout=10.0, headers=page_headers, follow_redirects=True, trust_env=False) as page_client:
                    home_resp = await page_client.get(f"https://user.qzone.qq.com/{uin}")
                    html_text = home_resp.text
                    token_match = re.search(r'window\.g_qzonetoken\s*=\s*\(function\(\)\s*\{\s*try\s*\{\s*return\s*["\']([^"\']+)["\']', html_text)
                    if not token_match:
                        token_match = re.search(r'g_qzonetoken\s*=\s*["\']([0-9a-zA-Z]+)["\']', html_text)
                    if token_match:
                        qzonetoken = token_match.group(1)
            except Exception:
                pass

            _QZONE_AUTH_CACHE = {
                "cookies": cookies,
                "g_tk": g_tk,
                "qzonetoken": qzonetoken,
                "uin": uin,
                "expire_at": now + 1800.0  # 缓存 30 分钟
            }
            return _QZONE_AUTH_CACHE
    except Exception as e:
        sys.stderr.write(f"获取 Qzone 凭证异常: {e}\n")
        return None


# ============================================================
# 空间说说历史持久化
# ============================================================
def load_qzone_history() -> list:
    """加载空间说说历史"""
    if os.path.exists(QZONE_HISTORY_FILE):
        try:
            with open(QZONE_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_qzone_history(history: list):
    """持久化空间说说历史"""
    try:
        with open(QZONE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        sys.stderr.write(f"保存空间日记历史异常: {e}\n")


# ============================================================
# 空间协议操作与辅助工具
# ============================================================
def _parse_cookie_val(cookies_str: str, key: str) -> str:
    """从 Cookie 字符串中提取指定键的值。"""
    if not cookies_str:
        return ""
    m = re.search(rf'(?:^|;\s*){re.escape(key)}=([^;]+)', cookies_str)
    return m.group(1).strip() if m else ""


def parse_qzone_jsonp(raw_text: str) -> dict:
    """
    剥离 QZone JSONP 外壳（如 _preloadCallback(...) / shine0_Callback(...)）并解析 JSON。
    解析失败返回 {}。
    """
    if not raw_text or not isinstance(raw_text, str):
        return {}
    stripped = raw_text.strip()
    m = re.search(r'^[^(]*\(\s*(\{[\s\S]*\})\s*\)\s*;?\s*$', stripped)
    payload = m.group(1) if m else stripped
    if not payload.startswith("{"):
        m2 = re.search(r'(\{[\s\S]*\})', payload)
        if not m2:
            return {}
        payload = m2.group(1)
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def upload_qzone_image(image_path: str, auth: dict) -> dict | None:
    """
    尝试将本地图片/表情包上传至 QQ 空间图床（up.qzone.qq.com/cgi-bin/upload/cgi_upload_image）。
    仅供带图发说说最佳努力尝试；任意异常或未返回有效 richval/pic_bo 一律返回 None，调用方自动降级纯文本。
    """
    if not image_path or not os.path.isfile(image_path) or not auth:
        return None
    try:
        file_size = os.path.getsize(image_path)
        if file_size <= 0 or file_size > 8 * 1024 * 1024:
            return None
        with open(image_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("ascii")

        cookies_str = auth.get("cookies", "")
        skey = _parse_cookie_val(cookies_str, "skey")
        p_skey = _parse_cookie_val(cookies_str, "p_skey")
        uin = str(auth.get("uin", BOT_QQ))
        g_tk = auth.get("g_tk", 0)
        qzonetoken = auth.get("qzonetoken", "")

        url = f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={g_tk}"
        if qzonetoken:
            url += f"&qzonetoken={qzonetoken}"

        payload = {
            "filename": os.path.basename(image_path),
            "uin": uin,
            "skey": skey,
            "zzpaneluin": uin,
            "zzpanelkey": "",
            "p_uin": uin,
            "p_skey": p_skey,
            "qzonetoken": qzonetoken,
            "uploadtype": "1",
            "albumtype": "7",
            "exttype": "0",
            "refer": "shuoshuo",
            "output_type": "jsonhtml",
            "charset": "utf-8",
            "output_charset": "utf-8",
            "upload_hd": "1",
            "hd_width": "2048",
            "hd_height": "10000",
            "hd_quality": "96",
            "backUrls": "http://upbak.qq.com/null.html",
            "url": f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={g_tk}",
            "base64": "1",
            "jsonhtml_callback": "callback",
            "picfile": b64_data,
            "qzreferrer": f"https://user.qzone.qq.com/proxy/domain/qzs.qq.com/qzone/v6/friend_album/index.html",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{uin}",
            "Cookie": cookies_str,
        }
        async with httpx.AsyncClient(timeout=18.0, headers=headers, trust_env=False) as client:
            resp = await client.post(url, data=payload)
            if resp.status_code != 200:
                return None
            # cgi_upload_image output_type=jsonhtml 常在 HTML script 内回调 frameElement.callback({...})
            parsed = parse_qzone_jsonp(resp.text)
            if not parsed:
                m_json = re.search(r'callback\(\s*(\{[\s\S]*?\})\s*\)', resp.text)
                if m_json:
                    try:
                        parsed = json.loads(m_json.group(1))
                    except Exception:
                        parsed = {}
            if not isinstance(parsed, dict) or int(parsed.get("ret", -1)) != 0:
                return None

            data = parsed.get("data") or {}
            albumid = data.get("albumid", "")
            lloc = data.get("lloc", "")
            sloc = data.get("sloc", lloc)
            pic_type = data.get("type", 1)
            width = data.get("width", 0)
            height = data.get("height", 0)
            pic_url = str(data.get("url", ""))
            bo_match = re.search(r'bo=([^&]+)', pic_url)
            pic_bo = bo_match.group(1) if bo_match else str(data.get("bo", ""))
            if not (albumid and lloc):
                return None

            richval = f",{albumid},{lloc},{sloc},{pic_type},{width},{height},,{width},{height}"
            return {"richval": richval, "pic_bo": pic_bo, "url": pic_url}
    except Exception as e:
        sys.stderr.write(f"⚠️ [QZone图床] 上传图片失败（将自动降级为纯文本）: {e}\n")
        return None


async def post_qzone_feed(
    content: str,
    mood: str,
    topics: list,
    caller: str,
    image_path: str = "",
) -> dict:
    """
    发布 QQ 空间说说。
    若传入 `image_path` 会尝试上传配图并附加 richtype/richval，若上传或带图发布失败则自动降级纯文字说说。
    返回 {"success": bool, "message": str, "total_count": int}
    """
    # 统一采用北京时间 (UTC+8)
    now_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    now_date = now_dt.strftime("%Y-%m-%d")
    now_time = now_dt.strftime("%H:%M:%S")
    diary_id = f"qzone_{now_dt.strftime('%Y%m%d_%H%M%S')}"

    # 系统定时日记防重复发布熔断 (主人手动发布不受此限制)
    history = load_qzone_history()
    if caller == "system" and any(d.get("date") == now_date for d in history):
        print(f"🛑 [QZone熔断] 今日 ({now_date}) 已经存在空间日记，拒绝系统定时任务重复发布！")
        return {
            "success": False,
            "message": f"今日 ({now_date}) 已经发布过空间日记，拒绝重复发布。",
            "total_count": len(history)
        }

    # 规范化话题标签：确保必然携带 #空友爱看# 公域召回锚点
    clean_topics = []
    for t in (topics or []):
        t_clean = str(t).strip().strip("#").strip()
        if t_clean and t_clean not in clean_topics:
            clean_topics.append(t_clean)
    if "空友爱看" not in clean_topics:
        clean_topics.insert(0, "空友爱看")
    if len(clean_topics) == 1:
        clean_topics.append("猫猫日常")

    raw_body = content.strip()
    # 构造发送给腾讯云端的带话题正文（本地历史仍存纯净正文，避免回忆时念出标签）
    missing_hashtags = [f"#{t}#" for t in clean_topics[:3] if f"#{t}#" not in raw_body]
    final_cloud_content = f"{raw_body}\n{' '.join(missing_hashtags)}" if missing_hashtags else raw_body

    entry = {
        "id": diary_id,
        "date": now_date,
        "time": now_time,
        "content": raw_body,
        "mood": mood.strip(),
        "topics": clean_topics,
        "caller": caller or "system"
    }

    # 无论腾讯接口返回如何，本地空间记忆库必落盘保存
    history.append(entry)
    save_qzone_history(history)

    auth = await get_qzone_auth()
    if not auth:
        return {
            "success": False,
            "message": "日记已沉淀入本地空间专属记忆库！(提示：未能从 OneBot 获取到 QZone 登录 Cookie，暂未同步到腾讯云端)",
            "total_count": len(history)
        }

    # POST emotion_cgi_publish_v6 (附带空友爱看公域推荐参数)
    try:
        url = f"https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6?g_tk={auth['g_tk']}"
        if auth["qzonetoken"]:
            url += f"&qzonetoken={auth['qzonetoken']}"

        data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "con": final_cloud_content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",          # 1 = 所有人可见（公域推荐硬性前提）
            "sync_to_public": "1",     # 主动声明同步至空间公域流
            "is_recommend": "1",       # 允许推荐至空友爱看发现池
            "to_sign": "0",
            "hostuin": auth["uin"],
            "code_version": "1",
            "format": "fs",
            "qzreferrer": f"https://user.qzone.qq.com/{auth['uin']}"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{auth['uin']}",
            "Cookie": auth["cookies"]
        }

        uploaded_pic = None
        if image_path and os.path.isfile(image_path):
            uploaded_pic = await upload_qzone_image(image_path, auth)
            if uploaded_pic:
                data["richtype"] = "1"
                data["richval"] = uploaded_pic["richval"]
                if uploaded_pic.get("pic_bo"):
                    data["pic_bo"] = uploaded_pic["pic_bo"]

        async with httpx.AsyncClient(timeout=15.0, headers=headers, trust_env=False) as client:
            resp = await client.post(url, data=data)
            resp_text = resp.text
            ok_flag = ("空间" in resp_text or resp.status_code == 200) and '"code":0' in resp_text.replace(" ", "") or (
                resp.status_code == 200 and '"code":-' not in resp_text.replace(" ", "") and "请先登录" not in resp_text
            )
            # 若带图尝试被拒绝，清掉图片参数再纯文本重试一次，确保日记必发成功
            if not ok_flag and uploaded_pic:
                data.pop("richtype", None)
                data.pop("richval", None)
                data.pop("pic_bo", None)
                uploaded_pic = None
                resp = await client.post(url, data=data)
                resp_text = resp.text

            if "空间" in resp_text or resp.status_code == 200:
                # 提取说说唯一 tid，执行空友爱看冷启动自赞破蛋
                tid_match = re.search(r'["\']?(?:t1_)?tid["\']?\s*[:=]\s*["\']([0-9a-zA-Z_-]{6,64})["\']', resp_text)
                feed_tid = tid_match.group(1) if tid_match else ""
                if feed_tid:
                    entry["tid"] = feed_tid
                if uploaded_pic:
                    entry["has_image"] = True
                if feed_tid or uploaded_pic:
                    save_qzone_history(history)
                if feed_tid:
                    try:
                        await like_friend_feed(auth["uin"], feed_tid)
                        print(f"🔥 [空友爱看冷启动] 已自动为弥酱新说说 (tid={feed_tid}) 点上首赞破蛋！")
                    except Exception as like_err:
                        print(f"⚠️ [空友爱看冷启动] 自赞略过: {like_err}")

                pic_suffix = "（附带空间表情配图）" if uploaded_pic else ""
                return {
                    "success": True,
                    "tid": feed_tid,
                    "has_image": bool(uploaded_pic),
                    "topics": clean_topics,
                    "message": (
                        f"成功发布 QQ 空间说说{pic_suffix}并投递至『空友爱看』公域池（附带标签: {' '.join(f'#{t}#' for t in clean_topics[:3])}）！"
                        f"已完整沉淀入空间专属记忆库（共收录 {len(history)} 篇日记）。"
                    ),
                    "total_count": len(history)
                }
            return {
                "success": False,
                "message": f"日记已记入本地空间记忆库！(腾讯服务器响应: {resp_text[:100]})",
                "total_count": len(history)
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"日记已成功记入本地记忆库，但向腾讯空间发送时网络异常: {str(e)}",
            "total_count": len(history)
        }


async def like_friend_feed(friend_uin: str, feed_tid: str) -> dict:
    """为好友空间说说点赞"""
    clean_uin = str(friend_uin).strip()
    clean_tid = str(feed_tid).strip()

    auth = await get_qzone_auth()
    if not auth:
        return {"success": False, "message": "点赞失败：未能获取 QQ 空间凭证。"}

    try:
        url = "https://h5.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
        params = {"g_tk": str(auth["g_tk"])}
        if auth["qzonetoken"]:
            params["qzonetoken"] = auth["qzonetoken"]

        unikey = f"http://user.qzone.qq.com/{clean_uin}/mood/{clean_tid}"
        data = {
            "opuin": auth["uin"],
            "unikey": unikey,
            "curkey": unikey,
            "appid": "311",
            "opr_type": "1",
            "format": "json"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{auth['uin']}",
            "Cookie": auth["cookies"]
        }
        async with httpx.AsyncClient(timeout=10.0, headers=headers, trust_env=False) as client:
            resp = await client.post(url, params=params, data=data)
            return {"success": True, "message": f"已成功为熟人好友({clean_uin})的说说点赞！响应: {resp.text[:60]}"}
    except Exception as e:
        return {"success": False, "message": f"点赞失败: {str(e)}"}


async def comment_friend_feed(friend_uin: str, feed_tid: str, comment_text: str) -> dict:
    """为好友空间说说发表评论"""
    clean_uin = str(friend_uin).strip()
    clean_tid = str(feed_tid).strip()

    auth = await get_qzone_auth()
    if not auth:
        return {"success": False, "message": "评论失败：未能获取 QQ 空间凭证。"}

    try:
        url = f"https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_addcomment_v6?g_tk={auth['g_tk']}"
        if auth["qzonetoken"]:
            url += f"&qzonetoken={auth['qzonetoken']}"

        data = {
            "topicId": f"{clean_uin}_{clean_tid}",
            "content": comment_text.strip(),
            "hostUin": clean_uin,
            "uin": auth["uin"],
            "format": "fs",
            "fupdate": "1",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "qzreferrer": f"https://user.qzone.qq.com/{clean_uin}"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{clean_uin}",
            "Cookie": auth["cookies"]
        }
        async with httpx.AsyncClient(timeout=10.0, headers=headers, trust_env=False) as client:
            resp = await client.post(url, data=data)
            return {"success": True, "message": f"已成功发表评论给好友({clean_uin})！评论内容: \"{comment_text}\""}
    except Exception as e:
        return {"success": False, "message": f"评论发表失败: {str(e)}"}


async def fetch_user_qzone_feeds(target_uin: str, count: int = 8) -> dict:
    """
    拉取指定 QQ 好友的空间说说列表（emotion_cgi_msglist_v6）。
    返回:
    {
        "ok": bool,
        "uin": str,
        "permission_denied": bool,
        "feeds": [
            {
                "tid": str,
                "uin": str,
                "nickname": str,
                "content": str,
                "created_time": int,
                "has_images": bool,
                "image_count": int,
                "like_count": int,
                "comments_preview": list[dict],
            },
            ...
        ],
        "error": str,
    }
    """
    clean_uin = str(target_uin or "").strip()
    if not clean_uin.isdigit():
        return {"ok": False, "uin": clean_uin, "permission_denied": False, "feeds": [], "error": "无效的 QQ 号"}

    auth = await get_qzone_auth()
    if not auth:
        return {"ok": False, "uin": clean_uin, "permission_denied": False, "feeds": [], "error": "未能获取 QQ 空间凭证"}

    try:
        url = f"https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        params = {
            "uin": clean_uin,
            "ftype": "0",
            "sort": "0",
            "pos": "0",
            "num": str(max(1, min(20, int(count)))),
            "replynum": "3",
            "g_tk": str(auth["g_tk"]),
            "callback": "_preloadCallback",
            "code_version": "1",
            "format": "jsonp",
            "need_private_comment": "1",
        }
        if auth.get("qzonetoken"):
            params["qzonetoken"] = auth["qzonetoken"]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{clean_uin}",
            "Cookie": auth["cookies"],
        }
        async with httpx.AsyncClient(timeout=12.0, headers=headers, trust_env=False) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "uin": clean_uin,
                    "permission_denied": False,
                    "feeds": [],
                    "error": f"HTTP {resp.status_code}",
                }

        parsed = parse_qzone_jsonp(resp.text)
        if not parsed:
            return {"ok": False, "uin": clean_uin, "permission_denied": False, "feeds": [], "error": "响应 JSONP 解析为空"}

        code = int(parsed.get("code", -1))
        if code != 0:
            subcode = int(parsed.get("subcode", 0) or 0)
            msg = str(parsed.get("message", "") or "")
            denied = code in (-10031, -4009, -4003) or "权限" in msg or "访问" in msg
            return {
                "ok": False,
                "uin": clean_uin,
                "permission_denied": denied,
                "feeds": [],
                "error": msg or f"QZone code={code}, subcode={subcode}",
            }

        raw_msglist = parsed.get("msglist") or []
        feeds = []
        for item in raw_msglist:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("tid", "")).strip()
            if not tid:
                continue
            content = str(item.get("content", "") or "").strip()
            # 部分转发说说正文在 rt_con
            rt_con = item.get("rt_con")
            if isinstance(rt_con, dict) and rt_con.get("content"):
                rt_txt = str(rt_con.get("content", "")).strip()
                if rt_txt:
                    content = f"{content} [转发: {rt_txt}]".strip() if content else f"[转发: {rt_txt}]"
            pics = item.get("pic") or []
            if not isinstance(pics, list):
                pics = []
            comments_preview = []
            for c in (item.get("commentlist") or [])[:3]:
                if isinstance(c, dict):
                    comments_preview.append({
                        "uin": str(c.get("uin", "")),
                        "name": str(c.get("name", "")),
                        "content": str(c.get("content", "")),
                    })
            feeds.append({
                "tid": tid,
                "uin": str(item.get("uin", clean_uin)),
                "nickname": str(item.get("name", "")),
                "content": content,
                "created_time": int(item.get("created_time", 0) or 0),
                "has_images": len(pics) > 0,
                "image_count": len(pics),
                "like_count": int(item.get("likeTotal", item.get("cmtnum", 0)) or 0),
                "comments_preview": comments_preview,
            })

        return {"ok": True, "uin": clean_uin, "permission_denied": False, "feeds": feeds, "error": ""}
    except Exception as e:
        return {"ok": False, "uin": clean_uin, "permission_denied": False, "feeds": [], "error": f"拉取空间异常: {e}"}

