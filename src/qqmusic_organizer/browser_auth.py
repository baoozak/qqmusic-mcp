from __future__ import annotations

import time
import sys
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Browser, Error as PlaywrightError, sync_playwright

from .qqmusic import normalized_uin


LOGIN_URL = "https://y.qq.com/"
AUTH_COOKIE_NAMES = {"qm_keyst", "qqmusic_key"}


def cookies_to_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name] = value
    return result


def login_cookie_is_ready(cookies: dict[str, str]) -> bool:
    return bool(normalized_uin(cookies) and AUTH_COOKIE_NAMES.intersection(cookies))


def cookie_header(cookies: dict[str, str]) -> str:
    if not login_cookie_is_ready(cookies):
        raise ValueError("QQ Music login cookies are incomplete")
    return "; ".join(f"{name}={value}" for name, value in sorted(cookies.items()))


def _launch_installed_browser(playwright: Any) -> Browser:
    errors: list[str] = []
    for channel in ("chrome", "msedge"):
        try:
            return playwright.chromium.launch(channel=channel, headless=False)
        except PlaywrightError as error:
            errors.append(f"{channel}: {error.message.splitlines()[0]}")
    raise RuntimeError("Could not launch Chrome or Edge. " + " | ".join(errors))


def capture_qqmusic_cookie(
    timeout_seconds: int = 300,
    *,
    browser_launcher: Callable[[Any], Browser] = _launch_installed_browser,
) -> str:
    if not 30 <= timeout_seconds <= 1800:
        raise ValueError("login timeout must be between 30 and 1800 seconds")
    with sync_playwright() as playwright:
        browser = browser_launcher(playwright)
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            print("请在打开的浏览器窗口中登录 QQ 音乐；检测到登录后会自动继续。", file=sys.stderr)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                cookies = cookies_to_dict(context.cookies([LOGIN_URL]))
                if login_cookie_is_ready(cookies):
                    time.sleep(1)
                    return cookie_header(cookies_to_dict(context.cookies([LOGIN_URL])))
                if not browser.is_connected():
                    raise RuntimeError("browser was closed before QQ Music login completed")
                time.sleep(1)
            raise TimeoutError(f"QQ Music login was not detected within {timeout_seconds} seconds")
        finally:
            context.close()
            browser.close()
