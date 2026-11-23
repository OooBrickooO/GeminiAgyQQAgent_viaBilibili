"""
social/bilibili_playwright.py — B站 3C 第二轨：Playwright 无头浏览器真实操作兜底引擎

设计原则：
1. 按需拉起无头 Chromium，用完即刻关闭，常驻内存占用为 0；
2. 复用本地 SESSDATA / bili_jct / DedeUserID / buvid3 / buvid4 Cookie 注入真实浏览器上下文；
3. 自动回收浏览器新生成的 buvid3 / buvid4 / bili_ticket 反哺给第一轨原生协议；
4. 若宿主机尚未安装 playwright，优雅返回可操作提示而不影响守护进程启动；
5. 操作受阻时自动保存诊断截图至 image_cache/ 方便主人排查风控或验证码。
"""
import os
import time
import json
import tempfile
from typing import Any, Dict, Optional
from config import IMAGE_CACHE_DIR
from social.bilibili_auth import (
    load_bili_credential,
    load_bili_fingerprint,
    update_bili_fingerprint_cookies,
)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False


def _build_playwright_cookies(cred, fp: dict) -> list[dict]:
    cookies = []
    kv = {
        "SESSDATA": getattr(cred, "sessdata", "") or "",
        "bili_jct": getattr(cred, "bili_jct", "") or "",
        "DedeUserID": str(getattr(cred, "dedeuserid", "") or ""),
        "buvid3": getattr(cred, "buvid3", "") or fp.get("buvid3", ""),
        "buvid4": fp.get("buvid4", ""),
        "bili_ticket": fp.get("bili_ticket", ""),
    }
    for name, val in kv.items():
        if val:
            cookies.append({
                "name": name,
                "value": str(val),
                "domain": ".bilibili.com",
                "path": "/",
            })
    return cookies


async def _harvest_context_cookies(context) -> None:
    """将无头浏览器中新获取的真实指纹 Cookie 回写本地，反哺第一轨原生协议"""
    try:
        all_cookies = await context.cookies(["https://www.bilibili.com", "https://t.bilibili.com"])
        updates = {}
        for c in all_cookies:
            name = c.get("name", "")
            val = c.get("value", "")
            if name in ("buvid3", "buvid4", "bili_ticket") and val:
                updates[name] = val
        if updates:
            update_bili_fingerprint_cookies(updates)
    except Exception:
        pass


async def _save_diag_screenshot(page, prefix: str) -> str:
    try:
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
        path = os.path.join(IMAGE_CACHE_DIR, f"{prefix}_{int(time.time())}.png")
        await page.screenshot(path=path, full_page=False)
        return path
    except Exception:
        return ""


async def playwright_favorite_video(
    bvid: str,
    aid: int,
    target_folder_id: int,
    target_folder_title: str = "默认收藏夹",
    credential=None,
) -> Dict[str, Any]:
    """
    【3C 第二轨 · Playwright 收藏兜底】
    在真实无头 Chromium 浏览器上下文中打开视频页：
    1) 优先在页面真实同源环境下执行 fetch('/x/v3/fav/resource/deal')（带完整浏览器指纹与 Cookie）；
    2) 若仍未成功，则模拟真人点击页面收藏按钮 -> 勾选目标收藏夹 -> 点击确定；
    3) 失败自动留存诊断截图。
    """
    if not PLAYWRIGHT_AVAILABLE or async_playwright is None:
        return {
            "ok": False,
            "error": "宿主机未安装 playwright（请运行 pip install playwright && playwright install chromium 启用 3C 浏览器兜底）",
        }

    cred = credential or load_bili_credential()
    if not cred or not getattr(cred, "sessdata", ""):
        return {"ok": False, "error": "尚未登录 B站，无法启动 Playwright 收藏兜底"}

    fp = load_bili_fingerprint()
    cookies = _build_playwright_cookies(cred, fp)
    csrf = str(getattr(cred, "bili_jct", "") or "")
    video_url = f"https://www.bilibili.com/video/{bvid}"

    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        await page.goto(video_url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1800)
        await _harvest_context_cookies(context)

        # 阶段 A：在真实浏览器页面上下文内发同源认证 POST（自带真实 TLS/指纹/Cookie）
        if aid and target_folder_id:
            inpage_res = await page.evaluate(
                """async ({aid, folderId, csrf}) => {
                    try {
                        const body = new URLSearchParams({
                            rid: String(aid),
                            type: "2",
                            add_media_ids: String(folderId),
                            del_media_ids: "",
                            platform: "web",
                            eab_x: "1",
                            ramval: "0",
                            ga: "1",
                            gaia_source: "web_normal",
                            csrf: csrf
                        });
                        const resp = await fetch("https://api.bilibili.com/x/v3/fav/resource/deal", {
                            method: "POST",
                            credentials: "include",
                            headers: {"Content-Type": "application/x-www-form-urlencoded"},
                            body: body.toString()
                        });
                        return await resp.json();
                    } catch (e) {
                        return {code: -999, message: String(e)};
                    }
                }""",
                {"aid": int(aid), "folderId": int(target_folder_id), "csrf": csrf},
            )
            code = inpage_res.get("code") if isinstance(inpage_res, dict) else -999
            if code in (0, 11201) or "已收藏" in str((inpage_res or {}).get("message", "")):
                return {
                    "ok": True,
                    "bvid": bvid,
                    "folder": target_folder_title,
                    "folder_id": target_folder_id,
                    "via": "playwright_inpage_fetch",
                    "message": f"已通过 Playwright 浏览器上下文将视频【{bvid}】收藏至【{target_folder_title}】！",
                }

        # 阶段 B：真实 UI 点击收藏按钮 -> 勾选目标分类夹 -> 确定
        fav_btn_selectors = [
            ".video-fav",
            "[title*='收藏']",
            ".toolbar-left-item-wrap .video-fav-icon",
        ]
        clicked = False
        for sel in fav_btn_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                try:
                    await loc.click(timeout=4000)
                    clicked = True
                    break
                except Exception:
                    continue

        if clicked:
            await page.wait_for_timeout(1200)
            # 查找匹配目标收藏夹名称的选项
            folder_loc = page.locator(f"label:has-text('{target_folder_title}')").first
            if await folder_loc.count() == 0:
                folder_loc = page.locator(".group-list label, .fav-list label").first
            if await folder_loc.count() > 0:
                await folder_loc.click(timeout=3000)
                await page.wait_for_timeout(400)
                confirm_loc = page.locator("button.submit-move, button:has-text('确定')").first
                if await confirm_loc.count() > 0:
                    async with page.expect_response(
                        lambda r: "/x/v3/fav/resource/deal" in r.url,
                        timeout=8000,
                    ) as resp_info:
                        await confirm_loc.click(timeout=3000)
                    deal_resp = await resp_info.value
                    try:
                        rj = await deal_resp.json()
                    except Exception:
                        rj = {}
                    if rj.get("code") in (0, 11201):
                        return {
                            "ok": True,
                            "bvid": bvid,
                            "folder": target_folder_title,
                            "folder_id": target_folder_id,
                            "via": "playwright_ui_click",
                            "message": f"已通过 Playwright 真实点击将视频【{bvid}】收藏至【{target_folder_title}】！",
                        }

        shot = await _save_diag_screenshot(page, f"bili_pw_fav_fail_{bvid}")
        return {
            "ok": False,
            "via": "playwright_failed",
            "screenshot": shot,
            "error": f"Playwright 收藏兜底未命中成功回执 (截图: {shot or '无'})",
        }
    except Exception as e:
        return {"ok": False, "via": "playwright_exc", "error": f"Playwright 收藏异常: {type(e).__name__}: {e}"}
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if pw:
                await pw.stop()
        except Exception:
            pass


async def playwright_publish_dynamic(
    text: str,
    img_bytes: bytes = b"",
    img_ext: str = "jpg",
    bvid: str = "",
    credential=None,
) -> Dict[str, Any]:
    """
    【3C 第二轨 · Playwright 动态发布兜底】
    打开 https://t.bilibili.com/ 真实网页动态发布框：
    - 填写文案（若传 bvid 且正文中无链接则自动附带直链）；
    - 若提供封面图字节，自动通过文件选择器上传配图；
    - 点击发布按钮并监听 /x/dynamic/feed/create/dyn 真实接口回执；
    - 失败时自动保存诊断截图辅助排查风控/验证码。
    """
    if not PLAYWRIGHT_AVAILABLE or async_playwright is None:
        return {
            "ok": False,
            "error": "宿主机未安装 playwright（请运行 pip install playwright && playwright install chromium 启用 3C 浏览器兜底）",
        }

    cred = credential or load_bili_credential()
    if not cred or not getattr(cred, "sessdata", ""):
        return {"ok": False, "error": "尚未登录 B站，无法启动 Playwright 发布动态"}

    clean_text = (text or "").strip()
    if bvid:
        vlink = f"https://www.bilibili.com/video/{bvid}"
        if vlink not in clean_text:
            clean_text = f"{clean_text}\n{vlink}"

    fp = load_bili_fingerprint()
    cookies = _build_playwright_cookies(cred, fp)

    pw = None
    browser = None
    tmp_img_path = ""
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        await page.goto("https://t.bilibili.com/", wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(2200)
        await _harvest_context_cookies(context)

        # 定位动态输入框
        editor_selectors = [
            ".bili-rich-textarea__inner",
            "div[contenteditable='true']",
            "textarea",
        ]
        editor = None
        for sel in editor_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                editor = loc
                break

        if editor is None:
            shot = await _save_diag_screenshot(page, "bili_pw_dyn_no_editor")
            return {
                "ok": False,
                "via": "playwright_no_editor",
                "screenshot": shot,
                "error": f"Playwright 打开动态页未找到编辑框（可能未登录或遇风控，截图: {shot or '无'}）",
            }

        await editor.click(timeout=5000)
        await page.wait_for_timeout(300)
        await editor.fill("")
        await page.keyboard.insert_text(clean_text)
        await page.wait_for_timeout(600)

        uploaded_img = False
        if img_bytes:
            try:
                with tempfile.NamedTemporaryFile(suffix=f".{img_ext or 'jpg'}", delete=False) as tf:
                    tf.write(img_bytes)
                    tmp_img_path = tf.name

                # 尝试点击配图按钮展开图片上传 input
                img_btn = page.locator(".bili-dyn-publishing__image-upload, [title*='图片'], .bili-dyn-publishing__tool-item").first
                if await img_btn.count() > 0:
                    try:
                        await img_btn.click(timeout=3000)
                        await page.wait_for_timeout(600)
                    except Exception:
                        pass

                file_input = page.locator("input[type='file']").first
                if await file_input.count() > 0:
                    await file_input.set_input_files(tmp_img_path)
                    await page.wait_for_timeout(2500)
                    uploaded_img = True
            except Exception as ie:
                print(f"⚠️ [bilibili_playwright] 动态传图略过，继续纯文本发布: {ie}")

        # 定位并点击发布按钮，同时拦截 create/dyn 响应
        pub_btn_selectors = [
            ".bili-dyn-publishing__action.launcher",
            "button:has-text('发布')",
            ".bili-dyn-publishing__action",
        ]
        pub_btn = None
        for sel in pub_btn_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                pub_btn = loc
                break

        if pub_btn is None:
            shot = await _save_diag_screenshot(page, "bili_pw_dyn_no_btn")
            return {
                "ok": False,
                "via": "playwright_no_btn",
                "screenshot": shot,
                "error": f"Playwright 未找到动态发布按钮 (截图: {shot or '无'})",
            }

        dyn_resp_json = {}
        try:
            async with page.expect_response(
                lambda r: "create/dyn" in r.url or "dynamic_svr/create" in r.url,
                timeout=12000,
            ) as resp_info:
                await pub_btn.click(timeout=5000)
            resp_obj = await resp_info.value
            try:
                dyn_resp_json = await resp_obj.json()
            except Exception:
                dyn_resp_json = {}
        except Exception as wait_err:
            shot = await _save_diag_screenshot(page, "bili_pw_dyn_timeout")
            return {
                "ok": False,
                "via": "playwright_timeout",
                "screenshot": shot,
                "error": f"Playwright 点击发布后未捕获到成功响应 ({wait_err} | 截图: {shot or '无'})",
            }

        if isinstance(dyn_resp_json, dict) and dyn_resp_json.get("code") == 0:
            d_part = dyn_resp_json.get("data") or {}
            dyn_id = d_part.get("dyn_id_str") or d_part.get("dyn_id") or d_part.get("dynamic_id_str") or ""
            return {
                "ok": True,
                "stage": "playwright_browser_fallback",
                "dyn_id": dyn_id,
                "has_image": uploaded_img,
                "bvid": bvid,
                "raw": dyn_resp_json,
            }

        shot = await _save_diag_screenshot(page, "bili_pw_dyn_rejected")
        return {
            "ok": False,
            "via": "playwright_rejected",
            "code": dyn_resp_json.get("code") if isinstance(dyn_resp_json, dict) else -1,
            "screenshot": shot,
            "error": f"Playwright 发布动态被接口拒收: {json.dumps(dyn_resp_json, ensure_ascii=False)} (截图: {shot or '无'})",
        }
    except Exception as e:
        return {"ok": False, "via": "playwright_exc", "error": f"Playwright 动态发布异常: {type(e).__name__}: {e}"}
    finally:
        if tmp_img_path and os.path.exists(tmp_img_path):
            try:
                os.remove(tmp_img_path)
            except Exception:
                pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if pw:
                await pw.stop()
        except Exception:
            pass
