"""
social/bilibili_auth.py — B站账号凭证管理与无头扫码认证中心

核心能力：
1. 本地凭证原子读写 (Atomic Save/Load)，杜绝突发掉电或强制杀进程导致凭证文件写坏
2. 手机端 B站 App 无头扫码登录 (生成 Base64 PNG 图并在本地落盘，供直接推送到 QQ 聊天气泡)
3. 凭证长效性异步校验与状态反查 (通过官方 /x/web-interface/nav 直连直验)
4. 深度适配 2024~2026 最新 B站 crossDomain 跨域票据换取 Cookie 逻辑，杜绝扫码后空凭证瞬间掉线
5. 全面强制 trust_env=False 直连国内 Passport，彻底规避海外/机房代理 IP 触发的 86038 异地秒作废风控
6. 采用项目内置 httpx 异步网络库，零额外依赖引入，确保守护进程启动绝对零报错
"""
import os
import sys
import json
import time
import uuid
import io
import base64
import asyncio
import hashlib
import hmac
from urllib.parse import parse_qs, urlparse, urlencode
from pathlib import Path

import httpx

try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

from config import BILI_CREDENTIAL_FILE, IMAGE_CACHE_DIR

# 容错引入 bilibili_api 用于 Credential 对象构建
try:
    from bilibili_api import Credential, user
    BILI_API_AVAILABLE = True
except ImportError:
    BILI_API_AVAILABLE = False
    Credential = None
    user = None

DEFAULT_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*"
}


class BiliLoginSession:
    """扫码登录会话状态载体"""
    def __init__(self, qrcode_key: str, qrcode_url: str):
        self.session_id: str = uuid.uuid4().hex
        self.qrcode_key: str = qrcode_key
        self.qrcode_url: str = qrcode_url
        self.created_at: float = time.time()
        self.is_done: bool = False


# 全局登录会话单例
_CURRENT_LOGIN_SESSION: BiliLoginSession | None = None


def load_bili_credential() -> "Credential | None":
    """
    从本地凭证文件加载 Credential 对象。
    采用极严苛的格式防御，如果文件损坏或被清空，优雅降级返回 None 而不抛异常。
    """
    if not os.path.exists(BILI_CREDENTIAL_FILE):
        return None

    try:
        with open(BILI_CREDENTIAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ [bili_auth] 读取凭证文件失败 ({type(e).__name__}): {e}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        return None

    sessdata = str(data.get("sessdata") or data.get("SESSDATA") or "").strip()
    bili_jct = str(data.get("bili_jct") or data.get("BILI_JCT") or "").strip()
    buvid3 = str(data.get("buvid3") or data.get("BUVID3") or "").strip()
    dedeuserid = str(data.get("dedeuserid") or data.get("DedeUserID") or "").strip()

    if not sessdata:
        return None

    if BILI_API_AVAILABLE and Credential:
        return Credential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            dedeuserid=dedeuserid
        )
    return None


def save_bili_credential(cred_dict: dict) -> str:
    """
    原子写入凭证到本地磁盘：先写 .tmp 临时文件，再通过 os.replace 替换，
    彻底消除写一半被杀进程损坏文件的隐患，同时保留/写入扩展指纹字段 (buvid4 / bili_ticket)。
    """
    os.makedirs(os.path.dirname(os.path.abspath(BILI_CREDENTIAL_FILE)), exist_ok=True)
    tmp_path = BILI_CREDENTIAL_FILE + ".tmp"

    existing = {}
    if os.path.exists(BILI_CREDENTIAL_FILE):
        try:
            with open(BILI_CREDENTIAL_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}

    clean_data = {
        "sessdata": str(cred_dict.get("sessdata") or cred_dict.get("SESSDATA") or existing.get("sessdata") or "").strip(),
        "bili_jct": str(cred_dict.get("bili_jct") or cred_dict.get("BILI_JCT") or existing.get("bili_jct") or "").strip(),
        "buvid3": str(cred_dict.get("buvid3") or cred_dict.get("BUVID3") or existing.get("buvid3") or "").strip(),
        "buvid4": str(cred_dict.get("buvid4") or cred_dict.get("BUVID4") or existing.get("buvid4") or "").strip(),
        "bili_ticket": str(cred_dict.get("bili_ticket") or existing.get("bili_ticket") or "").strip(),
        "bili_ticket_ts": float(cred_dict.get("bili_ticket_ts") or existing.get("bili_ticket_ts") or 0.0),
        "dedeuserid": str(cred_dict.get("dedeuserid") or cred_dict.get("DedeUserID") or cred_dict.get("mid") or existing.get("dedeuserid") or "").strip(),
    }

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, BILI_CREDENTIAL_FILE)
    print(f"✅ [bili_auth] 凭证已成功原子落盘: {BILI_CREDENTIAL_FILE} (UID: {clean_data['dedeuserid']})", file=sys.stderr)
    return BILI_CREDENTIAL_FILE


# ============================================================
# 现代 B站 协议指纹补全 (buvid3 / buvid4 / bili_ticket) 与 WBI 签名
# ============================================================
_WBI_KEY_CACHE: dict = {"img_key": "", "sub_key": "", "fetched_at": 0.0}

_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def load_bili_fingerprint() -> dict:
    """读取本地落盘的 buvid3 / buvid4 / bili_ticket 指纹缓存"""
    if not os.path.exists(BILI_CREDENTIAL_FILE):
        return {"buvid3": "", "buvid4": "", "bili_ticket": "", "bili_ticket_ts": 0.0}
    try:
        with open(BILI_CREDENTIAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                "buvid3": str(data.get("buvid3") or data.get("BUVID3") or "").strip(),
                "buvid4": str(data.get("buvid4") or data.get("BUVID4") or "").strip(),
                "bili_ticket": str(data.get("bili_ticket") or "").strip(),
                "bili_ticket_ts": float(data.get("bili_ticket_ts") or 0.0),
            }
    except Exception:
        pass
    return {"buvid3": "", "buvid4": "", "bili_ticket": "", "bili_ticket_ts": 0.0}


def update_bili_fingerprint_cookies(updates: dict) -> dict:
    """将新获取的 buvid3/buvid4/bili_ticket 合并回写本地凭证文件（不丢失已有字段）"""
    if not updates or not isinstance(updates, dict):
        return load_bili_fingerprint()
    if not os.path.exists(BILI_CREDENTIAL_FILE):
        return load_bili_fingerprint()
    try:
        with open(BILI_CREDENTIAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("sessdata"):
            return load_bili_fingerprint()
        changed = False
        for k in ("buvid3", "buvid4", "bili_ticket"):
            v = str(updates.get(k) or "").strip()
            if v and v != str(data.get(k, "")):
                data[k] = v
                changed = True
        if updates.get("bili_ticket"):
            data["bili_ticket_ts"] = time.time()
            changed = True
        if changed:
            save_bili_credential(data)
    except Exception:
        pass
    return load_bili_fingerprint()


async def ensure_bili_fingerprints(credential: "Credential | None" = None) -> dict:
    """
    确保当前 B站 会话具备完整的现代浏览器指纹 Cookie：
    1) 缺少 buvid3/buvid4 时，自动调 GET /x/frontend/finger/spi 获取官方真实指纹；
    2) 缺少或超过 48h 的 bili_ticket 时，自动通过 HMAC-SHA256 调用 GenWebTicket 换取；
    3) 自动回写至 bili_credential.json（并同步补全传入的 credential.buvid3）。
    """
    cred = credential or load_bili_credential()
    fp = load_bili_fingerprint()
    if cred and getattr(cred, "buvid3", "") and not fp.get("buvid3"):
        fp["buvid3"] = str(cred.buvid3).strip()

    updates = {}
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    cookie_dict = {}
    if cred and getattr(cred, "sessdata", ""):
        cookie_dict["SESSDATA"] = cred.sessdata
    if cred and getattr(cred, "bili_jct", ""):
        cookie_dict["bili_jct"] = cred.bili_jct
    if cred and getattr(cred, "dedeuserid", ""):
        cookie_dict["DedeUserID"] = str(cred.dedeuserid)

    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False, headers=base_headers, cookies=cookie_dict) as client:
            if not fp.get("buvid3") or not fp.get("buvid4"):
                try:
                    r_spi = await client.get("https://api.bilibili.com/x/frontend/finger/spi")
                    j_spi = r_spi.json()
                    if j_spi.get("code") == 0 and isinstance(j_spi.get("data"), dict):
                        b3 = str(j_spi["data"].get("b_3") or "").strip()
                        b4 = str(j_spi["data"].get("b_4") or "").strip()
                        if b3:
                            fp["buvid3"] = b3
                            updates["buvid3"] = b3
                        if b4:
                            fp["buvid4"] = b4
                            updates["buvid4"] = b4
                except Exception:
                    pass

            ticket_age = time.time() - float(fp.get("bili_ticket_ts") or 0.0)
            if not fp.get("bili_ticket") or ticket_age > 48 * 3600:
                try:
                    ts = int(time.time())
                    hexsign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
                    csrf = str(getattr(cred, "bili_jct", "") or "") if cred else ""
                    params = {
                        "key_id": "ec02",
                        "hexsign": hexsign,
                        "context[ts]": str(ts),
                        "csrf": csrf,
                    }
                    r_tk = await client.post(
                        "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
                        params=params,
                    )
                    j_tk = r_tk.json()
                    if j_tk.get("code") == 0 and isinstance(j_tk.get("data"), dict):
                        tk = str(j_tk["data"].get("ticket") or "").strip()
                        if tk:
                            fp["bili_ticket"] = tk
                            fp["bili_ticket_ts"] = float(ts)
                            updates["bili_ticket"] = tk
                except Exception:
                    pass
    except Exception:
        pass

    if cred and fp.get("buvid3") and not getattr(cred, "buvid3", ""):
        try:
            cred.buvid3 = fp["buvid3"]
        except Exception:
            pass

    if updates:
        update_bili_fingerprint_cookies(updates)
    return fp


def _get_mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


async def get_wbi_keys(credential: "Credential | None" = None, force: bool = False) -> tuple[str, str]:
    """获取并缓存 B站 WBI img_key 与 sub_key（2 小时缓存）"""
    now = time.time()
    if (
        not force
        and _WBI_KEY_CACHE["img_key"]
        and _WBI_KEY_CACHE["sub_key"]
        and (now - float(_WBI_KEY_CACHE["fetched_at"])) < 7200.0
    ):
        return _WBI_KEY_CACHE["img_key"], _WBI_KEY_CACHE["sub_key"]

    cred = credential or load_bili_credential()
    cookies = {}
    if cred and getattr(cred, "sessdata", ""):
        cookies["SESSDATA"] = cred.sessdata
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False, headers=DEFAULT_BILI_HEADERS, cookies=cookies) as client:
            resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
            rj = resp.json()
            wbi_img = (rj.get("data") or {}).get("wbi_img") or {}
            img_url = str(wbi_img.get("img_url", ""))
            sub_url = str(wbi_img.get("sub_url", ""))
            img_key = img_url.rsplit("/", 1)[-1].split(".")[0] if img_url else ""
            sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0] if sub_url else ""
            if img_key and sub_key:
                _WBI_KEY_CACHE["img_key"] = img_key
                _WBI_KEY_CACHE["sub_key"] = sub_key
                _WBI_KEY_CACHE["fetched_at"] = now
                return img_key, sub_key
    except Exception:
        pass
    return _WBI_KEY_CACHE["img_key"], _WBI_KEY_CACHE["sub_key"]


async def sign_wbi_params(params: dict, credential: "Credential | None" = None) -> dict:
    """对请求查询参数执行 B站 标准 WBI 签名，返回附带 wts 与 w_rid 的新字典"""
    signed = {str(k): str(v) for k, v in (params or {}).items() if v is not None}
    img_key, sub_key = await get_wbi_keys(credential)
    if not img_key or not sub_key:
        return signed
    mixin_key = _get_mixin_key(img_key + sub_key)
    signed["wts"] = str(int(time.time()))
    ordered = dict(sorted(signed.items()))
    filtered = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in ordered.items()
    }
    query_str = urlencode(filtered)
    w_rid = hashlib.md5((query_str + mixin_key).encode("utf-8")).hexdigest()
    filtered["w_rid"] = w_rid
    return filtered


def clear_bili_credential() -> bool:
    """清除本地 B 站登录凭证，实现注销解绑"""
    if os.path.exists(BILI_CREDENTIAL_FILE):
        try:
            os.remove(BILI_CREDENTIAL_FILE)
            print(f"🗑️ [bili_auth] 已成功清除本地 B 站凭证: {BILI_CREDENTIAL_FILE}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"⚠️ [bili_auth] 清除凭证失败: {e}", file=sys.stderr)
            return False
    return False


async def verify_bili_credential(credential: "Credential | None" = None) -> dict:
    """
    向 B站 服务器请求一次个人基本资料，验证当前凭证是否真实有效。
    优先通过 httpx (trust_env=False) 直连 /x/web-interface/nav 接口，避免老旧库环境代理报错。
    """
    cred = credential or load_bili_credential()
    sessdata = cred.sessdata if cred else ""
    dedeuserid = cred.dedeuserid if cred else ""
    bili_jct = cred.bili_jct if cred else ""

    # 如果无法通过 Credential 取到，尝试直接读本地 JSON 文件
    if not sessdata and os.path.exists(BILI_CREDENTIAL_FILE):
        try:
            with open(BILI_CREDENTIAL_FILE, "r", encoding="utf-8") as f:
                local_data = json.load(f)
                sessdata = str(local_data.get("sessdata") or local_data.get("SESSDATA") or "").strip()
                dedeuserid = str(local_data.get("dedeuserid") or local_data.get("DedeUserID") or "").strip()
                bili_jct = str(local_data.get("bili_jct") or local_data.get("BILI_JCT") or "").strip()
        except Exception:
            pass

    if not sessdata:
        return {"logged_in": False, "message": "尚未登录 B站，未发现有效凭证"}

    cookies = {
        "SESSDATA": sessdata,
        "bili_jct": bili_jct,
        "DedeUserID": dedeuserid
    }

    try:
        async with httpx.AsyncClient(
            trust_env=False,
            headers=DEFAULT_BILI_HEADERS,
            cookies=cookies,
            timeout=8.0
        ) as client:
            resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
            res_data = resp.json()

        if res_data.get("code") == 0 and res_data.get("data", {}).get("isLogin"):
            user_info = res_data["data"]
            real_uid = str(user_info.get("mid") or dedeuserid)
            return {
                "logged_in": True,
                "uid": real_uid,
                "name": user_info.get("uname", ""),
                "level": user_info.get("level_info", {}).get("current_level", 0),
                "sign": user_info.get("sign", ""),
                "face": user_info.get("face", ""),
                "message": "B站凭证有效"
            }
        else:
            err_msg = res_data.get("message", "登录态校验失败")
            return {
                "logged_in": False,
                "uid": dedeuserid,
                "message": f"凭证已失效或过期 ({err_msg})"
            }
    except Exception as e:
        # 网络异常时，尝试用 bilibili_api 原生做备选校验
        if BILI_API_AVAILABLE and cred and dedeuserid and user:
            try:
                u = user.User(uid=int(dedeuserid), credential=cred)
                info = await u.get_user_info()
                return {
                    "logged_in": True,
                    "uid": dedeuserid,
                    "name": info.get("name", ""),
                    "level": info.get("level", 0),
                    "sign": info.get("sign", ""),
                    "face": info.get("face", ""),
                    "message": "B站凭证有效"
                }
            except Exception:
                pass
        return {
            "logged_in": False,
            "uid": dedeuserid,
            "message": f"校验网络异常 ({type(e).__name__}: {e})"
        }


async def generate_login_qr() -> dict:
    """
    启动扫码登录流程：
    1. 强制 trust_env=False 直连国内 passport.bilibili.com，彻底杜绝梯子海外机房 IP 触发 B 站异地扫码风控 86038
    2. 生成 qrcode_key 与手机登录 URL
    3. 本地生成高质量 PNG 二维码图片并落盘，转为 Base64 供直接推送 QQ
    """
    global _CURRENT_LOGIN_SESSION

    # 先检查是否已经登录且有效
    check_res = await verify_bili_credential()
    if check_res.get("logged_in"):
        return {
            "ok": True,
            "already_logged_in": True,
            "name": check_res.get("name"),
            "uid": check_res.get("uid"),
            "message": f"当前已登录账号【{check_res.get('name')}】(UID: {check_res.get('uid')})，无需重复扫码。"
        }

    try:
        gen_url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate?source=main-fe-header"
        async with httpx.AsyncClient(
            trust_env=False,
            headers=DEFAULT_BILI_HEADERS,
            timeout=8.0
        ) as client:
            resp = await client.get(gen_url)
            res_data = resp.json()

        if res_data.get("code") != 0 or not res_data.get("data"):
            return {"ok": False, "message": f"B站接口拒绝生成二维码: {res_data.get('message')}"}

        data = res_data["data"]
        qrcode_key = data["qrcode_key"]
        qrcode_url = data["url"]

        # 生成二维码 PNG 字节流
        if not QRCODE_AVAILABLE:
            return {"ok": False, "message": "未安装 qrcode 依赖库，无法渲染二维码图片"}

        qr = qrcode.QRCode(border=2)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")

        # 落盘到本地缓存
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
        qr_file = os.path.join(IMAGE_CACHE_DIR, "bili_login_qr.png")
        with open(qr_file, "wb") as f:
            f.write(img_bytes)

        # 注册会话单例
        new_session = BiliLoginSession(qrcode_key=qrcode_key, qrcode_url=qrcode_url)
        _CURRENT_LOGIN_SESSION = new_session

        return {
            "ok": True,
            "already_logged_in": False,
            "session_id": new_session.session_id,
            "qrcode_file": qr_file,
            "qrcode_base64": img_base64,
            "qrcode_url": qrcode_url,
            "message": "二维码生成成功，请用手机 B站 App 扫码（180秒内有效）"
        }
    except Exception as e:
        _CURRENT_LOGIN_SESSION = None
        return {"ok": False, "message": f"生成登录二维码异常: {type(e).__name__}: {e}"}


async def poll_login_status(session_id: str | None = None) -> dict:
    """
    轮询当前正在进行的登录会话状态。
    支持 session_id 校验，杜绝旧任务并发串台误报超时。
    扫码成功后自动请求 crossDomain 票据链接，精确提取 SESSDATA / bili_jct / DedeUserID 并原子落盘。
    """
    global _CURRENT_LOGIN_SESSION

    if not _CURRENT_LOGIN_SESSION:
        check_res = await verify_bili_credential()
        if check_res.get("logged_in"):
            return {"status": "already_logged_in", "message": "已登录"}
        return {"status": "no_session", "message": "没有正在进行的扫码会话，请先生成二维码"}

    # 孤儿协程串台防御：若 session_id 不匹配当前活跃会话，静默终止
    if session_id and session_id != _CURRENT_LOGIN_SESSION.session_id:
        return {"status": "stale_session", "message": "会话已被新任务接管，旧任务自动终止"}

    current_key = _CURRENT_LOGIN_SESSION.qrcode_key
    poll_url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={current_key}&source=main-fe-header"

    try:
        async with httpx.AsyncClient(
            trust_env=False,
            headers=DEFAULT_BILI_HEADERS,
            timeout=8.0
        ) as client:
            resp = await client.get(poll_url)
            res_data = resp.json()

        if res_data.get("code") != 0 or not res_data.get("data"):
            return {"status": "error", "message": f"轮询响应异常: {res_data}"}

        poll_result = res_data["data"]
        code = poll_result.get("code")

        # 86101: 未扫码
        if code == 86101:
            return {"status": "waiting", "message": "等待用户扫码..."}

        # 86090: 已扫码，等待确认
        elif code == 86090:
            return {"status": "scanning", "message": "手机已扫码，请在手机端点击【确认登录】..."}

        # 86038: 二维码已失效或超时
        elif code == 86038:
            if _CURRENT_LOGIN_SESSION and _CURRENT_LOGIN_SESSION.qrcode_key == current_key:
                _CURRENT_LOGIN_SESSION = None
            return {"status": "timeout", "message": "二维码已超时失效，请重新获取"}

        # 0: 登录成功！处理 crossDomain 跨域票据
        elif code == 0:
            cross_url = poll_result.get("url", "")
            extracted_cookies = {}

            # 请求 crossDomain 票据链接，从 Set-Cookie 响应头中抓取真实凭证
            if cross_url:
                try:
                    async with httpx.AsyncClient(
                        trust_env=False,
                        headers=DEFAULT_BILI_HEADERS,
                        timeout=8.0,
                        follow_redirects=True
                    ) as c_client:
                        c_resp = await c_client.get(cross_url)
                        for cookie in getattr(c_client.cookies, "jar", []):
                            extracted_cookies[cookie.name] = cookie.value
                        for hist in getattr(c_resp, "history", []):
                            for cookie in getattr(hist.cookies, "jar", []):
                                extracted_cookies[cookie.name] = cookie.value
                        for cookie in getattr(c_resp.cookies, "jar", []):
                            extracted_cookies[cookie.name] = cookie.value
                        for header_val in c_resp.headers.get_list("set-cookie"):
                            try:
                                from http.cookies import SimpleCookie
                                sc = SimpleCookie()
                                sc.load(header_val)
                                for k, morsel in sc.items():
                                    extracted_cookies[k] = morsel.value
                            except Exception:
                                pass
                except Exception as ce:
                    print(f"⚠️ [bili_auth] 访问 crossDomain 票据链接报错: {ce}", file=sys.stderr)

            # 兜底：若 cross_url 的 query 参数中依然带有参数，补充解析
            if "?" in cross_url:
                parsed_qs = parse_qs(urlparse(cross_url).query)
                for k, vals in parsed_qs.items():
                    if k not in extracted_cookies and vals:
                        extracted_cookies[k] = vals[0]

            sessdata = extracted_cookies.get("SESSDATA", "").strip()
            bili_jct = extracted_cookies.get("bili_jct", "").strip()
            dedeuserid = str(extracted_cookies.get("DedeUserID", "")).strip()
            buvid3 = extracted_cookies.get("buvid3", "").strip()

            # 如果仍然缺少 dedeuserid，调用一次 nav 获取真实 mid
            if sessdata and not dedeuserid:
                try:
                    temp_cookies = {"SESSDATA": sessdata, "bili_jct": bili_jct}
                    async with httpx.AsyncClient(
                        trust_env=False,
                        headers=DEFAULT_BILI_HEADERS,
                        cookies=temp_cookies,
                        timeout=6.0
                    ) as n_client:
                        n_resp = await n_client.get("https://api.bilibili.com/x/web-interface/nav")
                        n_json = n_resp.json()
                        if n_json.get("code") == 0:
                            dedeuserid = str(n_json.get("data", {}).get("mid", "")).strip()
                except Exception:
                    pass

            if not sessdata:
                _CURRENT_LOGIN_SESSION = None
                print(f"⚠️ [bili_auth] 扫码成功但未能提取到 SESSDATA，提取到的 keys: {list(extracted_cookies.keys())}", file=sys.stderr)
                return {
                    "status": "error",
                    "message": "扫码成功但未能从票据响应中提取到 SESSDATA，请重试"
                }

            # 原子持久化凭证
            save_bili_credential({
                "sessdata": sessdata,
                "bili_jct": bili_jct,
                "buvid3": buvid3,
                "dedeuserid": dedeuserid
            })

            # 清理当前会话
            _CURRENT_LOGIN_SESSION = None

            # 立即进行一次真实校验并获取个人昵称
            verify_res = await verify_bili_credential()
            return {
                "status": "done",
                "message": "登录成功！",
                "name": verify_res.get("name", "B站用户"),
                "uid": verify_res.get("uid", dedeuserid)
            }

        return {"status": "waiting", "message": f"状态等待中 (code: {code})"}
    except Exception as e:
        return {"status": "error", "message": f"轮询扫码状态异常: {type(e).__name__}: {e}"}


