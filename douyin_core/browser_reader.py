"""Read public profile responses from a real, persistent Chrome session."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .config import CoreConfig
from .douyin_api import DouyinAPI  # noqa: F401  (re-exported for compatibility)
from .logger import get_logger
from .platforms import (
    DouyinAdapter,
    PlatformAdapter,
    detect_platform,
    extract_aweme_list,
    extract_user_info,
    is_profile_response,
)
from .risks import ParseError, RiskBlockedError, SessionExpiredError, assert_not_risk_response, random_delay

logger = get_logger(__name__)
StatusCallback = Callable[[str], None] | None
ProgressCallback = Callable[[int, int, str], None] | None


class BrowserReadError(RuntimeError): pass


@dataclass
class ProfileReadResult:
    posts: list[dict]
    user_info: dict[str, Any]
    cookie: str = ""
    user_agent: str = ""
    source: str = "browser"
    diagnostics: dict[str, Any] = field(default_factory=dict)


def cookie_header_from_browser(cookies: Iterable[dict[str, Any]]) -> str:
    return "; ".join(f"{item['name']}={item['value']}" for item in cookies if item.get("name") and item.get("value"))


def parse_performance_message(entry: dict[str, Any]) -> dict[str, Any] | None:
    try:
        message: Any = entry.get("message", entry)
        if isinstance(message, str): message = json.loads(message)
        return message.get("message", message) if isinstance(message, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError): return None


class BrowserProfileReader:
    DEFAULT_USER_AGENT = CoreConfig().user_agent
    DEFAULT_BROWSER_PROFILE_DIR = CoreConfig().browser_profile_dir

    def __init__(
        self,
        cookie: str = "",
        *,
        headless: bool = False,
        timeout: int = 35,
        max_scrolls: int = 240,
        idle_rounds: int = 12,
        scroll_wait: float = 1.2,
        verification_timeout: int = 0,
        user_agent: str = DEFAULT_USER_AGENT,
        browser_profile_dir: str | None = None,
        keep_open: bool = False,
        platform: PlatformAdapter | None = None,
        continue_event: Event | None = None,
        cancel_event: Event | None = None,
        login_wait_callback: Callable[[], None] | None = None,
    ) -> None:
        self.cookie, self.headless, self.timeout = cookie, headless, timeout
        self.max_scrolls, self.idle_rounds, self.scroll_wait = max_scrolls, idle_rounds, scroll_wait
        # ``verification_timeout <= 0`` means "wait indefinitely"; callers can
        # still cap the interactive wait explicitly.
        self.verification_timeout = verification_timeout
        self.user_agent, self.browser_profile_dir, self.keep_open = user_agent, os.path.abspath(browser_profile_dir or self.DEFAULT_BROWSER_PROFILE_DIR), keep_open
        self.platform = platform
        self.continue_event = continue_event or Event()
        self.cancel_event = cancel_event or Event()
        self.login_wait_callback = login_wait_callback
        self._login_poll_interval = 2.0
        self.driver: Any = None; self._raw_awemes: dict[str, dict] = {}; self._user_info: dict[str, Any] = {}; self._pending_requests: dict[str, str] = {}; self._processed_requests: set[str] = set(); self._response_count = 0

    def open_session(self):
        if self.driver: return self.driver
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError as exc: raise BrowserReadError("未安装 Selenium") from exc
        options = Options()
        # Do not wait for every media/analytics request.  Douyin can keep
        # those requests alive long enough for Chrome to report a renderer
        # timeout, even when the login page is already visible.
        options.page_load_strategy = "eager"
        if self.headless: options.add_argument("--headless=new")
        for flag in ("--disable-gpu", "--window-size=1440,1000", "--disable-blink-features=AutomationControlled", "--disable-extensions", "--disable-popup-blocking", "--no-first-run", "--disable-notifications"):
            options.add_argument(flag)
        options.add_argument(f"--user-agent={self.user_agent}"); os.makedirs(self.browser_profile_dir, exist_ok=True); options.add_argument(f"--user-data-dir={self.browser_profile_dir}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"]); options.add_experimental_option("useAutomationExtension", False); options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        try:
            self.driver = webdriver.Chrome(service=Service(), options=options)
        except Exception as exc:
            if not self._is_profile_lock_failure(exc):
                raise
            self._cleanup_stale_profile_locks()
            try:
                self.driver = webdriver.Chrome(service=Service(), options=options)
            except Exception as retry_exc:
                self.close()
                raise BrowserReadError(
                    "浏览器启动失败：会话目录被残留的自动化 Chrome 占用。"
                    "请关闭本工具之前打开的所有 Chrome 窗口后重试；"
                    f"详细原因：{retry_exc}"
                ) from retry_exc
        self.driver.set_page_load_timeout(self.timeout)
        self.driver.execute_cdp_cmd("Network.enable", {"maxTotalBufferSize": 100_000_000, "maxResourceBufferSize": 50_000_000})
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
        self._restore_cookie_if_present(); return self.driver

    @staticmethod
    def _is_profile_lock_failure(exc: Exception) -> bool:
        message = str(exc)
        return any(marker in message for marker in ("session not created", "Chrome instance exited", "user data directory"))

    def _cleanup_stale_profile_locks(self) -> None:
        """Remove lock files left by crashed automation Chrome instances."""
        import glob
        import subprocess

        for pattern in ("Singleton*", "lockfile"):
            for path in glob.glob(os.path.join(self.browser_profile_dir, pattern)):
                try:
                    os.remove(path)
                except OSError:
                    logger.debug("无法删除残留锁文件：%s", path)
        try:
            subprocess.run(["taskkill", "/F", "/IM", "chromedriver.exe"], capture_output=True, timeout=10)
        except Exception:
            pass

    def close(self) -> None:
        if self.driver:
            try: self.driver.quit()
            except Exception: pass
            self.driver = None

    def wait_for_document(self) -> None:
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            WebDriverWait(self.driver, self.timeout).until(lambda d: d.find_element(By.TAG_NAME, "body"))
        except Exception as exc: logger.warning("页面加载等待超时：%s", exc)
        time.sleep(min(2.0, self.scroll_wait + .5))

    def _driver_alive(self) -> bool:
        if not self.driver: return False
        try:
            self.driver.current_url
            return True
        except Exception:
            return False

    def has_login_gate(self) -> bool:
        if not self._driver_alive(): return False
        try: return bool(self.driver.execute_script(self.platform.gate_probe_script()))
        except Exception: return False

    # Legacy private spelling retained for the desktop's pre-refactor tests.
    _has_login_gate = has_login_gate

    def has_session_cookie(self) -> bool:
        """Positive signal: a cookie that only exists after a real login."""
        if not self._driver_alive(): return False
        try: names = {item.get("name") for item in self.driver.get_cookies()}
        except Exception: return False
        return any(name in names for name in self.platform.login_cookie_names)

    def wait_for_login(self, profile_url: str, status_callback: StatusCallback = None) -> None:
        """Block until the human finishes login/verification in the browser.

        The wait is unlimited by default.  It resumes when any of these
        happens: the operator clicks "我已登录" (``continue_event``), a login
        cookie appears, or the on-page gate disappears.  Each resume
        re-navigates once and succeeds when posts were captured or the gate
        cleared; otherwise it keeps waiting.
        """
        if self.headless: raise SessionExpiredError("需要人工登录或验证码；headless 模式无法继续")
        if self.login_wait_callback:
            try: self.login_wait_callback()
            except Exception: logger.debug("login_wait_callback failed", exc_info=True)
        started = time.monotonic()
        previously_gated = self.has_login_gate()
        had_session_cookie = self.has_session_cookie()
        self._status(status_callback, "请在浏览器窗口完成登录或验证；完成后将自动继续，也可点击“我已登录，继续”")
        while True:
            if self.cancel_event.is_set(): raise SessionExpiredError("已取消登录等待")
            if not self._driver_alive(): raise SessionExpiredError("浏览器窗口已关闭，请重新读取")
            manual_continue = self.continue_event.is_set()
            if manual_continue: self.continue_event.clear()
            gated_now = self.has_login_gate()
            session_now = self.has_session_cookie()
            gate_cleared = previously_gated and not gated_now
            cookie_gained = session_now and not had_session_cookie
            previously_gated = gated_now
            had_session_cookie = had_session_cookie or session_now
            if manual_continue or gate_cleared or cookie_gained:
                self._status(status_callback, "检测到登录操作，正在重新读取主页…")
                self._navigate(profile_url); self.wait_for_document()
                try: self._collect_network_responses()
                except RiskBlockedError: logger.info("登录恢复读取仍命中风控页；继续等待人工处理")
                self._harvest_dom_payloads()
                if len(self._raw_awemes) > 0 or not self.has_login_gate(): return
                self._status(status_callback, "仍未读取到作品；请确认已完成登录/验证后再次继续")
            if self.verification_timeout > 0 and time.monotonic() - started >= self.verification_timeout:
                raise SessionExpiredError("等待人工登录或验证超时")
            time.sleep(self._login_poll_interval)

    def wait_for_manual_login(self, profile_url: str) -> None:
        """Backward-compatible wrapper around :meth:`wait_for_login`."""
        self.wait_for_login(profile_url)

    def read_profile(self, profile_url: str, *, max_count: int = 0, status_callback: StatusCallback = None, progress_callback: ProgressCallback = None) -> ProfileReadResult:
        if not profile_url.strip(): raise BrowserReadError("请输入有效的博主主页链接")
        profile_url = profile_url.strip()
        if self.platform is None: self.platform = detect_platform(profile_url)
        if not self.platform.implemented:
            raise BrowserReadError(f"{self.platform.name} 平台支持尚未实现；当前仅支持抖音链接")
        self._raw_awemes.clear(); self._user_info = {}; self._pending_requests.clear(); self._processed_requests.clear(); self._response_count = 0
        try:
            self._status(status_callback, f"正在启动{self.platform.name}浏览器会话"); self.open_session()
            self._status(status_callback, "浏览器已打开，正在加载主页")
            self._navigate(profile_url); self.wait_for_document(); self._safe_collect(profile_url, status_callback)
            self._harvest_dom_payloads()
            if self.has_login_gate() and (not max_count or len(self._raw_awemes) < max_count):
                self._status(status_callback, "检测到登录限制，请在浏览器完成登录或验证")
                self.wait_for_login(profile_url, status_callback)
            posts = self._scroll_and_collect(profile_url, max_count, status_callback, progress_callback)
            if not posts:
                # Last-chance recovery: instead of closing the browser right
                # away, give the operator a chance to log in / solve a check.
                if self.headless: raise BrowserReadError("未读取到作品，请完成登录/验证后重试")
                self._status(status_callback, "未读取到作品：可能需要登录或人机验证，请在浏览器窗口中完成后继续")
                self.wait_for_login(profile_url, status_callback)
                posts = self._scroll_and_collect(profile_url, max_count, status_callback, progress_callback)
            if not posts: raise BrowserReadError("未读取到作品，请完成登录/验证后重试")
            if not self._user_info: self._user_info = {"nickname": posts[0]["author"]["nickname"], "aweme_count": len(posts)}
            return ProfileReadResult(posts, self._user_info, cookie_header_from_browser(self.driver.get_cookies()), self.user_agent, diagnostics={"network_responses": self._response_count, "raw_awemes": len(self._raw_awemes)})
        except (RiskBlockedError, SessionExpiredError, BrowserReadError): raise
        except Exception as exc: raise BrowserReadError(f"浏览器读取失败：{exc}") from exc
        finally:
            if not self.keep_open:
                self.close()

    def _safe_collect(self, profile_url: str, status_callback: StatusCallback = None) -> None:
        """Collect responses; recover interactively when a risk page appears."""
        try:
            self._collect_network_responses()
        except RiskBlockedError:
            if self.headless: raise
            self._status(status_callback, "平台返回了验证/风控页面，请在浏览器中完成验证或登录")
            self.wait_for_login(profile_url, status_callback)

    def _scroll_and_collect(self, profile_url: str, max_count: int, status_callback: StatusCallback = None, progress_callback: ProgressCallback = None) -> list[dict]:
        """Scroll the profile, ingest responses and parse the captured posts."""
        expected, idle, previous = int(self._user_info.get("aweme_count", 0) or 0), 0, len(self._raw_awemes)
        target_text = str(expected) if expected else "全部公开作品"
        self._status(status_callback, f"正在自动滚动主页加载作品，当前 {previous} 个，目标 {target_text}")
        for scroll_number in range(1, self.max_scrolls + 1):
            if (max_count and len(self._raw_awemes) >= max_count) or (expected and len(self._raw_awemes) >= expected): break
            self._scroll_profile_once()
            random_delay(max(.2, self.scroll_wait*.7), self.scroll_wait+ .3)
            try:
                self._collect_network_responses()
            except RiskBlockedError:
                if self.headless: raise
                self._status(status_callback, "平台返回了验证/风控页面，请在浏览器中完成验证或登录")
                self.wait_for_login(profile_url, status_callback)
            current = len(self._raw_awemes)
            if current > previous:
                idle, previous = 0, current; message = f"自动滚动 {scroll_number}/{self.max_scrolls}：已读取 {current}/{target_text} 个作品"; self._status(status_callback, message)
                if progress_callback: progress_callback(current, expected, message)
            else: idle += 1
            if idle >= self.idle_rounds:
                if expected and current < expected:
                    self._status(status_callback, f"连续 {idle} 次滚动未加载新作品，当前读取 {current}/{expected} 个；主页可能已无更多可公开读取的作品")
                break
        raw = list(self._raw_awemes.values())[:max_count or None]; posts = []
        for item in raw:
            try: posts.append(self.platform.parse_item(item))
            except ParseError as exc: logger.warning("跳过无法解析的作品：%s", exc)
        return posts

    def _status(self, callback: StatusCallback, message: str) -> None:
        logger.info(message)
        if callback: callback(message)

    def _navigate(self, url: str) -> None:
        """Keep an already-visible page usable when its subresources time out."""
        try:
            self.driver.get(url)
        except Exception as exc:
            logger.warning("页面完整加载超时，继续使用已打开页面：%s", exc)
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass

    def _scroll_profile_once(self) -> None:
        """Advance both page and common content scrollers to trigger lazy loading."""
        self.driver.execute_script(
            """
            const step = Math.max(600, Math.floor((window.innerHeight || 800) * 0.9));
            const page = document.scrollingElement || document.documentElement || document.body;
            window.scrollBy({top: step, left: 0, behavior: 'auto'});
            if (page) page.scrollTop += step;

            const roots = [
              document.querySelector('main'),
              document.querySelector('[role="main"]'),
              document.querySelector('#root'),
            ].filter(Boolean);
            for (const root of roots) {
              const candidates = [root, ...root.querySelectorAll('div')];
              let best = null;
              for (const element of candidates) {
                const style = window.getComputedStyle(element);
                if (element.scrollHeight > element.clientHeight + 80 &&
                    /(auto|scroll)/.test(style.overflowY)) {
                  if (!best || element.clientHeight > best.clientHeight) best = element;
                }
              }
              if (best) best.scrollTop += step;
            }
            window.dispatchEvent(new WheelEvent('wheel', {deltaY: step, bubbles: true}));
            return {top: page ? page.scrollTop : 0, height: page ? page.scrollHeight : 0};
            """
        )

    def _restore_cookie_if_present(self) -> None:
        if not self.cookie: return
        self._navigate(self.platform.home_url)
        domain = self.platform.cookie_domain
        for segment in self.cookie.split(";"):
            name, separator, value = segment.strip().partition("=")
            if separator and name and value:
                try: self.driver.add_cookie({"name": name, "value": value, "domain": domain, "path": "/"})
                except Exception: pass

    def _collect_network_responses(self) -> None:
        for entry in self.driver.get_log("performance"):
            message = parse_performance_message(entry)
            if not message: continue
            params, method = message.get("params", {}), message.get("method")
            if method == "Network.responseReceived" and self.platform.is_profile_response(params.get("response", {}).get("url", "")):
                self._pending_requests[params.get("requestId", "")] = params["response"]["url"]
            if method == "Network.loadingFinished" and params.get("requestId") in self._pending_requests:
                request_id = params["requestId"]
                if request_id not in self._processed_requests:
                    self._processed_requests.add(request_id)
                    try:
                        body = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}).get("body", "")
                        assert_not_risk_response(body); self._ingest_payload(json.loads(body)); self._response_count += 1
                    except RiskBlockedError: raise
                    except Exception as exc: logger.debug("忽略无效网络响应：%s", exc)

    def _ingest_payload(self, payload: Any) -> None:
        user = self.platform.extract_user_info(payload)
        if user: self._user_info = user
        for aweme in self.platform.extract_items(payload):
            key = self.platform.item_id(aweme)
            if key: self._raw_awemes.setdefault(key, aweme)

    def _harvest_dom_payloads(self) -> None:
        """Ingest SSR state (e.g. TikTok pre-login first page) from script tags."""
        if not self._driver_alive(): return
        for script_id in self.platform.dom_data_script_ids():
            try:
                element = self.driver.find_element("xpath", f"//script[@id='{script_id}']")
            except Exception:
                continue
            try:
                raw = element.get_attribute("textContent") or element.get_attribute("innerHTML") or ""
                payload = json.loads(raw)
            except Exception:
                logger.debug("忽略无法解析的内嵌数据：%s", script_id)
                continue
            for container in find_item_containers(payload):
                self._ingest_payload(container)


def find_item_containers(node: Any, depth: int = 0) -> Iterable[Any]:
    """Yield dicts that look like post-list containers inside arbitrary SSR JSON."""
    if depth > 6: return
    if isinstance(node, dict):
        if "itemList" in node or "aweme_list" in node:
            yield node
        else:
            for value in node.values():
                yield from find_item_containers(value, depth + 1)
    elif isinstance(node, list):
        for value in node[:80]:
            yield from find_item_containers(value, depth + 1)
