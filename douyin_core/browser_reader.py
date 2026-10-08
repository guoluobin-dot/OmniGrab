"""Read public profile responses from a real, persistent Chrome session."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import requests

from .chrome_driver import LAUNCH_TIMEOUT, DriverSetupError, resolve_chromedriver
from .config import CoreConfig
from .douyin_api import DouyinAPI  # noqa: F401  (re-exported for compatibility)
from .logger import get_logger
from .platforms import (
    DouyinAdapter,
    PlatformAdapter,
    TikTokLoginRequired,
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
    # Structured browser cookies (with domain/path/secure) so downstream tools
    # such as yt-dlp can rebuild an accurate session instead of a flat header.
    cookies: list[dict[str, Any]] = field(default_factory=list)

    def with_cookies(self, posts: list[dict]) -> list[dict]:
        """Attach this session's cookies to every post that lacks them."""
        if not self.cookies:
            return posts
        for post in posts:
            if isinstance(post, dict):
                post.setdefault("cookies", self.cookies)
        return posts


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
        pause_event: Event | None = None,
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
        self.pause_event = pause_event or Event()
        # pause_event: set = paused, clear = running (default running)
        if self.pause_event.is_set():
            self.pause_event.clear()
        self.login_wait_callback = login_wait_callback
        self._login_poll_interval = 2.0
        self.driver: Any = None; self._raw_awemes: dict[str, dict] = {}; self._user_info: dict[str, Any] = {}; self._pending_requests: dict[str, str] = {}; self._processed_requests: set[str] = set(); self._response_count = 0

    def _clean_profile_cache(self, profile_dir: str) -> None:
        """Delete heavy cache folders that slow Chrome startup but keep login (Cookies, Local Storage)."""
        import shutil
        # Folders that can be safely removed to speed up launch (keep Cookies, Local Storage)
        cache_patterns = [
            "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
            "Service Worker/CacheStorage", "Service Worker/ScriptCache",
            "Default/Cache", "Default/Code Cache", "Default/GPUCache",
            "Default/Service Worker/CacheStorage", "AutofillStrikeDatabase",
            "optimization_guide_model_store", "DawnCache", "GraphiteDawnCache",
        ]
        for pattern in cache_patterns:
            p = os.path.join(profile_dir, pattern)
            if os.path.exists(p):
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except Exception:
                    pass

    def open_session(self, status_callback: StatusCallback = None):
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
        for flag in (
            "--disable-gpu",
            "--window-size=1440,1000",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--disable-popup-blocking",
            "--no-first-run",
            "--disable-notifications",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-default-apps",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=VizDisplayCompositor",
            "--disable-hang-monitor",
            "--disable-prompt-on-repost",
            "--disable-domain-reliability",
        ):
            options.add_argument(flag)
        # Speed up by blocking images on TikTok single page (optional)
        # Use per-platform profile dir to avoid Douyin's huge cache slowing TikTok
        profile_dir = self.browser_profile_dir
        if self.platform is not None and self.platform.name == "tiktok":
            profile_dir = os.path.join(os.path.dirname(self.browser_profile_dir.rstrip(os.sep)), "browser_profile_tiktok")
        # Clean heavy cache before launch (keep login)
        try:
            self._clean_profile_cache(profile_dir)
        except Exception:
            pass
        options.add_argument(f"--user-agent={self.user_agent}"); os.makedirs(profile_dir, exist_ok=True); options.add_argument(f"--user-data-dir={profile_dir}")
        # Reduce cache size via prefs
        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.images": 1,
        })
        # Store resolved dir for lock cleanup
        self._resolved_profile_dir = profile_dir
        options.add_experimental_option("excludeSwitches", ["enable-automation"]); options.add_experimental_option("useAutomationExtension", False); options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        self._status(status_callback, "正在检测 Chrome 驱动…")
        # Resolve the driver ourselves: Selenium Manager may block for minutes on
        # the network when Chrome was updated, which froze the UI before the
        # browser window could open.
        try:
            driver_path = resolve_chromedriver(status_callback)
        except DriverSetupError as exc:
            raise BrowserReadError(str(exc)) from exc
        service = Service(executable_path=driver_path) if driver_path else Service()
        try:
            self._status(status_callback, "正在启动 Chrome 进程…")
            self.driver = self._launch_driver(service, options, status_callback)
        except Exception as exc:
            if not self._is_profile_lock_failure(exc):
                raise
            self._cleanup_stale_profile_locks()
            try:
                self.driver = self._launch_driver(service, options, status_callback)
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

    def _launch_driver(self, service, options, status_callback: StatusCallback = None) -> Any:
        """Start Chrome with a watchdog so a stuck launch fails loudly.

        ``webdriver.Chrome`` normally returns in seconds; when it never does
        (blocked driver download, hung profile, security software) the previous
        behaviour was to wait forever with the UI stuck on "正在启动 Chrome
        进程…".  Now we time out with an actionable message instead.
        """
        import threading

        from selenium import webdriver

        outcome: dict[str, Any] = {}

        def _target() -> None:
            try:
                outcome["driver"] = webdriver.Chrome(service=service, options=options)
            except Exception as exc:  # noqa: BLE001 - re-raised on the caller thread
                outcome["error"] = exc

        worker = threading.Thread(target=_target, name="chrome-launch", daemon=True)
        worker.start()
        worker.join(LAUNCH_TIMEOUT)
        if worker.is_alive():
            logger.warning("Chrome 启动超过 %ss 仍未返回，放弃等待", LAUNCH_TIMEOUT)

            def _reap() -> None:
                worker.join()
                late_driver = outcome.get("driver")
                if late_driver is not None:
                    try:
                        late_driver.quit()
                    except Exception:
                        pass

            threading.Thread(target=_reap, name="chrome-launch-reaper", daemon=True).start()
            try:
                service.stop()
            except Exception:
                pass
            raise BrowserReadError(
                f"Chrome 启动超时（{LAUNCH_TIMEOUT} 秒）。\n"
                "常见原因：安全软件拦截、Chrome 版本过旧，或上一次的 Chrome 窗口残留占用配置目录。\n"
                "请关闭所有 Chrome 窗口后重试；若仍失败，请重新安装 Chrome。"
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["driver"]

    def _cleanup_stale_profile_locks(self) -> None:
        """Remove lock files left by crashed automation Chrome instances."""
        import glob
        import subprocess

        profile_dir = getattr(self, "_resolved_profile_dir", self.browser_profile_dir)
        for pattern in ("Singleton*", "lockfile"):
            for path in glob.glob(os.path.join(profile_dir, pattern)):
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

    def _try_http_tiktok_single(self, profile_url: str, status_callback: StatusCallback = None) -> ProfileReadResult | None:
        """Fast path for TikTok single video/photo: direct HTTP + SSR parse, no browser."""
        try:
            self._status(status_callback, "尝试直连 TikTok 获取单条作品（免浏览器高速模式）…")
            headers = {
                "User-Agent": self.user_agent,
                "Referer": "https://www.tiktok.com/",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            }
            resp = requests.get(profile_url, headers=headers, timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                logger.debug("HTTP TikTok single failed status=%s", resp.status_code)
                return None
            html = resp.text
            # Quick gate check
            if "captcha" in html.lower() or "verify" in html.lower() and "tiktok" in html.lower():
                logger.debug("HTTP response appears to be captcha page")
                # fall back to browser
                return None
            payloads: list[Any] = []
            for script_id in ("SIGI_STATE", "__UNIVERSAL_DATA_FOR_REHYDRATION__"):
                m = re.search(rf'<script[^>]*id="{script_id}"[^>]*>(.*?)</script>', html, re.DOTALL)
                if m:
                    try:
                        payloads.append(json.loads(m.group(1)))
                    except Exception:
                        continue
            if not payloads:
                logger.debug("HTTP: no SSR script found")
                return None
            # Reuse platform extractor directly
            tmp_raw: dict[str, dict] = {}
            tmp_user: dict[str, Any] = {}
            for payload in payloads:
                # direct ingest like harvest does
                user = self.platform.extract_user_info(payload)
                if user:
                    tmp_user = user
                for aweme in self.platform.extract_items(payload):
                    key = self.platform.item_id(aweme)
                    if key:
                        tmp_raw.setdefault(key, aweme)
                # also walk containers
                for container in find_item_containers(payload):
                    user2 = self.platform.extract_user_info(container)
                    if user2:
                        tmp_user = user2
                    for aweme in self.platform.extract_items(container):
                        key = self.platform.item_id(aweme)
                        if key:
                            tmp_raw.setdefault(key, aweme)
            if not tmp_raw:
                logger.debug("HTTP: no items extracted")
                return None
            # Parse
            posts: list[dict] = []
            for item in tmp_raw.values():
                try:
                    posts.append(self.platform.parse_item(item))
                except ParseError as exc:
                    logger.warning("HTTP parsed skip: %s", exc)
            if not posts:
                return None
            user_info = tmp_user or {"nickname": posts[0]["author"]["nickname"], "aweme_count": len(posts)}
            logger.info("HTTP TikTok single succeeded: %s items", len(posts))
            self._status(status_callback, f"直连成功，已读取 {len(posts)} 个作品（免浏览器）")
            # Reuse current UA and empty cookie (download will use referer)
            return ProfileReadResult(posts, user_info, "", self.user_agent, diagnostics={"network_responses": 0, "raw_awemes": len(tmp_raw), "mode": "http"})
        except Exception as exc:
            logger.debug("HTTP TikTok single exception: %s", exc)
            return None

    def _try_http_bilibili_profile(self, profile_url: str, status_callback: StatusCallback = None) -> ProfileReadResult | None:
        """Fast path for Bilibili user profile: direct HTTP to video list API."""
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(profile_url)
            if "space.bilibili.com" not in parsed.netloc:
                return None
            
            # Extract mid from URL
            path_parts = parsed.path.strip("/").split("/")
            if not path_parts:
                return None
            mid = path_parts[0]
            if not mid.isdigit():
                return None
            
            self._status(status_callback, "尝试直连 B 站获取视频列表（免浏览器高速模式）…")
            
            headers = {
                "User-Agent": self.user_agent,
                "Referer": f"https://space.bilibili.com/{mid}",
                "Origin": "https://space.bilibili.com",
                "Accept": "application/json, text/plain, */*",
            }
            if self.cookie:
                headers["Cookie"] = self.cookie
            
            # Call video list API with WBI signature (with pagination support)
            from .downloader import _add_wbi_signature
            all_videos = []
            page = 1
            page_size = 50
            max_pages = (max_count + page_size - 1) // page_size if max_count else 10  # default max 10 pages
            
            while page <= max_pages:
                api_url = f"https://api.bilibili.com/x/space/wbi/arc/search?mid={mid}&ps={page_size}&pn={page}"
                signed_url = _add_wbi_signature(api_url)
                
                resp = requests.get(signed_url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    logger.debug("HTTP Bilibili profile failed status=%s page=%s", resp.status_code, page)
                    break
                
                data = resp.json()
                if data.get("code") != 0:
                    logger.debug("HTTP Bilibili API error page=%s: %s", page, data.get("message"))
                    break
                
                vlist = data.get("data", {}).get("list", {}).get("vlist", [])
                if not vlist:
                    break
                    
                all_videos.extend(vlist)
                
                if max_count and len(all_videos) >= max_count:
                    all_videos = all_videos[:max_count]
                    break
                    
                page += 1
                random_delay(0.3, 0.8)  # Rate limiting
            
            if not all_videos:
                logger.debug("HTTP Bilibili: no videos found")
                return None
            
            # Create payload with all videos
            payload = {
                "data": {
                    "list": {"vlist": all_videos},
                    "upper": {}
                }
            }
            # Also try to get user info
            user_resp = requests.get(f"https://api.bilibili.com/x/space/acc/info?mid={mid}", headers=headers, timeout=10)
            if user_resp.status_code == 200:
                user_data = user_resp.json()
                if user_data.get("code") == 0:
                    payload = {"data": {"list": data.get("data", {}).get("list", {}), "user_info": user_data.get("data", {})}}
            
            tmp_raw: dict[str, dict] = {}
            tmp_user: dict[str, Any] = {}
            
            user = self.platform.extract_user_info(payload)
            if user:
                tmp_user = user
            for aweme in self.platform.extract_items(payload):
                key = self.platform.item_id(aweme)
                if key:
                    tmp_raw.setdefault(key, aweme)
            
            if not tmp_raw:
                logger.debug("HTTP Bilibili: no items extracted")
                return None
            
            posts: list[dict] = []
            for item in tmp_raw.values():
                try:
                    posts.append(self.platform.parse_item(item))
                except ParseError as exc:
                    logger.warning("HTTP Bilibili parsed skip: %s", exc)
            
            if not posts:
                return None
            
            user_info = tmp_user or {"nickname": posts[0]["author"]["nickname"], "aweme_count": len(posts)}
            logger.info("HTTP Bilibili profile succeeded: %s items", len(posts))
            self._status(status_callback, f"直连成功，已读取 {len(posts)} 个视频（免浏览器）")
            
            cookie_header = ""
            if self.cookie:
                cookie_header = self.cookie
            
            return ProfileReadResult(posts, user_info, cookie_header, self.user_agent, diagnostics={"network_responses": 1, "raw_awemes": len(tmp_raw), "mode": "http"})
        except Exception as exc:
            logger.debug("HTTP Bilibili profile exception: %s", exc)
            return None

    def read_profile(self, profile_url: str, *, max_count: int = 0, status_callback: StatusCallback = None, progress_callback: ProgressCallback = None) -> ProfileReadResult:
        if not profile_url.strip(): raise BrowserReadError("请输入有效的博主主页链接")
        profile_url = profile_url.strip()
        # 自动修复复制时丢了 :// / ? 的残缺链接（如 httpswww.douyin.comuser...）
        try:
            from .platforms import normalize_profile_url
            fixed_url = normalize_profile_url(profile_url)
            if fixed_url != profile_url:
                self._status(status_callback, f"检测到链接格式残缺，已自动修复为：{fixed_url}")
                profile_url = fixed_url
        except Exception:
            pass
        # 快速校验：避免把非法 URL 送进 Selenium（会报 invalid argument 然后误进登录等待死循环）
        parsed = urlparse(profile_url)
        if not parsed.scheme.startswith("http") or not parsed.netloc:
            raise BrowserReadError(
                f"链接格式不正确，浏览器无法打开：{profile_url}\n"
                "请从浏览器地址栏完整复制（以 https:// 开头），例如：https://www.douyin.com/user/MS4w..."
            )
        if self.platform is None: self.platform = detect_platform(profile_url)
        if not self.platform.implemented:
            raise BrowserReadError(f"{self.platform.name} 平台支持尚未实现")
        self._raw_awemes.clear(); self._user_info = {}; self._pending_requests.clear(); self._processed_requests.clear(); self._response_count = 0
        try:
            # 单页链接（TikTok 单条视频/图文、TikTok Shop 商品详情页）：无需滚动
            is_tiktok_single = hasattr(self.platform, "is_single_item_url") and self.platform.is_single_item_url(profile_url)  # type: ignore
            is_shop_product = bool(getattr(self.platform, "is_product_url", None) and self.platform.is_product_url(profile_url))  # type: ignore
            if is_tiktok_single and self.platform.name == "tiktok":
                http_result = self._try_http_tiktok_single(profile_url, status_callback)
                if http_result is not None:
                    return http_result
            
            # Try HTTP fast path for Bilibili profile
            is_bilibili_profile = self.platform.name == "bilibili" and "space.bilibili.com" in profile_url
            if is_bilibili_profile and not self.headless:
                http_result = self._try_http_bilibili_profile(profile_url, status_callback)
                if http_result is not None:
                    return http_result
            
            self._status(status_callback, f"正在启动{self.platform.name}浏览器会话…"); self.open_session(status_callback)
            # 单页链接不需要滚动：导航后等待 JS 接口返回即可
            self._status(status_callback, "浏览器已打开，正在加载主页…")
            self._navigate_to_profile_videos(profile_url); self.wait_for_document()
            # 单页场景的接口触发较晚，多给一点时间
            if is_tiktok_single:
                time.sleep(3.0)
            self._safe_collect(profile_url, status_callback)
            self._harvest_dom_payloads(); self._harvest_dom_media()
            if is_tiktok_single:
                # 单页：等待 SSR/API 落定后直接解析
                if len(self._raw_awemes) == 0:
                    time.sleep(2.5)
                    try: self._collect_network_responses()
                    except RiskBlockedError: pass
                    self._harvest_dom_payloads(); self._harvest_dom_media()
                if self.has_login_gate():
                    self._status(status_callback, "检测到登录限制，请在浏览器完成登录或验证")
                    self.wait_for_login(profile_url, status_callback)
                    self._harvest_dom_payloads(); self._harvest_dom_media()
                posts = self._parse_collected_posts(0, status_callback)
                if not posts:
                    # 接口可能更晚返回，再重试一次
                    time.sleep(2.0)
                    try: self._collect_network_responses()
                    except RiskBlockedError: pass
                    self._harvest_dom_payloads(); self._harvest_dom_media()
                    posts = self._parse_collected_posts(0, status_callback)
                if not posts:
                    if self.headless: raise BrowserReadError("未读取到作品，请完成登录/验证后重试")
                    self._status(status_callback, "未读取到作品：可能需要登录或人机验证，请在浏览器窗口中完成后继续")
                    self.wait_for_login(profile_url, status_callback)
                    self._harvest_dom_payloads(); self._harvest_dom_media()
                    posts = self._parse_collected_posts(0, status_callback)
                if not posts:
                    hint = "该商品页可能没有公开视频，或视频需要登录后查看" if is_shop_product else "请检查链接是否为公开视频/图文"
                    raise BrowserReadError(f"未读取到作品，{hint}")
                if not self._user_info: self._user_info = {"nickname": posts[0]["author"]["nickname"] or "TikTok Shop", "aweme_count": len(posts)}
                return self._finish(posts)
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
            return self._finish(posts)
        except (RiskBlockedError, SessionExpiredError, BrowserReadError, TikTokLoginRequired): raise
        except Exception as exc: raise BrowserReadError(f"浏览器读取失败：{exc}") from exc
        finally:
            if not self.keep_open:
                self.close()

    def _finish(self, posts: list[dict]) -> ProfileReadResult:
        """Wrap parsed posts with the live browser session needed to download them."""
        cookies = self.driver.get_cookies() if self._driver_alive() else []
        result = ProfileReadResult(
            posts, self._user_info, cookie_header_from_browser(cookies), self.user_agent,
            diagnostics={"network_responses": self._response_count, "raw_awemes": len(self._raw_awemes)},
            cookies=cookies,
        )
        result.with_cookies(result.posts)
        return result

    def _safe_collect(self, profile_url: str, status_callback: StatusCallback = None) -> None:
        """Collect responses; recover interactively when a risk page appears."""
        try:
            self._collect_network_responses()
        except RiskBlockedError:
            if self.headless: raise
            self._status(status_callback, "平台返回了验证/风控页面，请在浏览器中完成验证或登录")
            self.wait_for_login(profile_url, status_callback)

    def _parse_collected_posts(self, max_count: int = 0, status_callback: StatusCallback = None) -> list[dict]:
        """Parse captured items, and surface a clear reason when TikTok gated them.

        When the session is anonymous, TikTok returns every post with an empty
        ``playAddr``. Silently returning zero posts would leave the user with a
        bare "未读取到作品"; the dedicated error explains what to actually do.
        """
        raw = list(self._raw_awemes.values())[: max_count or None]
        posts: list[dict] = []
        gated = 0
        for item in raw:
            try:
                posts.append(self.platform.parse_item(item))
            except TikTokLoginRequired as exc:
                gated += 1
                logger.info("TikTok 未返回播放地址（需要登录）：%s", exc)
            except ParseError as exc:
                logger.warning("跳过无法解析的作品：%s", exc)
        self._login_required_posts = gated
        if gated and not posts:
            raise TikTokLoginRequired(
                f"已捕获 {len(raw)} 个作品，但平台未返回任何播放地址。"
                "这通常表示当前 TikTok 会话未登录：请在弹出的浏览器窗口中登录一次"
                "（工具已保存会话，之后即可正常下载），或该账号存在地区访问限制。"
            )
        if gated:
            self._status(
                status_callback,
                f"提示：有 {gated}/{len(raw)} 个作品平台未返回播放地址（通常是未登录 TikTok）",
            )
            logger.info("有 %d/%d 个作品因未登录而没有播放地址", gated, len(raw))
        return posts

    def _check_pause(self, status_callback: StatusCallback = None) -> None:
        """If paused, block until resumed or cancelled."""
        while self.pause_event.is_set():
            if self.cancel_event.is_set():
                raise SessionExpiredError("已取消读取")
            if status_callback:
                # avoid spamming, status is updated by caller
                pass
            time.sleep(0.5)

    def _scroll_and_collect(self, profile_url: str, max_count: int, status_callback: StatusCallback = None, progress_callback: ProgressCallback = None) -> list[dict]:
        """Scroll the profile, ingest responses and parse the captured posts."""
        expected, idle, previous = int(self._user_info.get("aweme_count", 0) or 0), 0, len(self._raw_awemes)
        target_text = str(expected) if expected else "全部公开作品"
        self._status(status_callback, f"正在自动滚动主页加载作品，当前 {previous} 个，目标 {target_text}")
        for scroll_number in range(1, self.max_scrolls + 1):
            # pause support
            self._check_pause(status_callback)
            if self.cancel_event.is_set():
                raise SessionExpiredError("已取消读取")
            if (max_count and len(self._raw_awemes) >= max_count) or (expected and len(self._raw_awemes) >= expected): break
            self._scroll_profile_once()
            random_delay(max(.2, self.scroll_wait*.7), self.scroll_wait+ .3)
            try:
                self._collect_network_responses()
            except RiskBlockedError:
                if self.headless: raise
                self._status(status_callback, "平台返回了验证/风控页面，请在浏览器中完成验证或登录")
                self.wait_for_login(profile_url, status_callback)
            self._harvest_dom_media()
            current = len(self._raw_awemes)
            if current > previous:
                idle, previous = 0, current; message = f"自动滚动 {scroll_number}/{self.max_scrolls}：已读取 {current}/{target_text} 个作品"; self._status(status_callback, message)
                if progress_callback: progress_callback(current, expected, message)
            else: idle += 1
            if idle >= self.idle_rounds:
                if expected and current < expected:
                    self._status(status_callback, f"连续 {idle} 次滚动未加载新作品，当前读取 {current}/{expected} 个；主页可能已无更多可公开读取的作品")
                break
        return self._parse_collected_posts(max_count, status_callback)

    def _status(self, callback: StatusCallback, message: str) -> None:
        logger.info(message)
        if callback: callback(message)

    def _navigate(self, url: str) -> None:
        """Keep an already-visible page usable when its subresources time out."""
        try:
            self.driver.get(url)
        except Exception as exc:
            # 非法 URL（如缺 https://）会报 invalid argument：必须直接失败，
            # 否则空页面会被误判成“登录限制”，进入无限等待。
            if "invalid argument" in str(exc).lower():
                raise BrowserReadError(
                    f"链接格式不正确，浏览器无法打开：{url}\n"
                    "请从浏览器地址栏完整复制（以 https:// 开头）"
                ) from exc
            logger.warning("页面完整加载超时，继续使用已打开页面：%s", exc)
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass

    def _navigate_to_profile_videos(self, profile_url: str) -> None:
        """Platform-specific navigation to ensure video list is loaded."""
        if self.platform.name == "bilibili":
            self._navigate_to_bilibili_videos(profile_url)
        else:
            self._navigate(profile_url)

    def _navigate_to_bilibili_videos(self, profile_url: str) -> None:
        """Navigate to Bilibili user profile and click video tab to load video list."""
        from urllib.parse import urlparse
        
        # First navigate to profile
        self._navigate(profile_url)
        self.wait_for_document()
        time.sleep(2.0)
        
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            # Check if we're on a user space page
            parsed = urlparse(profile_url)
            if "space.bilibili.com" in parsed.netloc:
                # Try to click the "视频" tab
                video_tab_selectors = [
                    "//a[contains(@href, '/video')]",
                    "//div[contains(@class, 'nav-item') and contains(text(), '视频')]",
                    "//span[text()='视频']/ancestor::a",
                    "//a[@data-tab='video']",
                    "//li[contains(@class, 'tab') and .//text()='视频']",
                ]
                
                for selector in video_tab_selectors:
                    try:
                        elements = self.driver.find_elements(By.XPATH, selector)
                        for elem in elements:
                            if elem.is_displayed() and elem.is_enabled():
                                logger.info("点击 B 站视频标签: %s", selector)
                                self.driver.execute_script("arguments[0].click();", elem)
                                time.sleep(2.0)
                                break
                    except Exception:
                        continue
        except Exception as exc:
            logger.debug("B 站视频标签点击失败，继续尝试: %s", exc)
        
        # Also try to trigger API call by scrolling
        self._scroll_profile_once()
        time.sleep(1.0)

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
                if request_id in self._processed_requests:
                    continue
                self._processed_requests.add(request_id)
                url = self._pending_requests.get(request_id, "")
                try:
                    body = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}).get("body", "")
                except Exception as exc:
                    logger.debug("忽略无法读取的响应体 %s：%s", url[:120], exc)
                    continue
                if not isinstance(body, str):
                    continue
                stripped = body.lstrip()[:200]
                if stripped.startswith("<"):
                    # 只有 HTML 页面才可能是风控/验证页；其它非 JSON 响应直接跳过
                    assert_not_risk_response(body); continue
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    logger.debug("跳过非 JSON 响应 %s", url[:120])
                    continue
                try:
                    assert_not_risk_response(payload); self._ingest_payload(payload); self._response_count += 1
                except RiskBlockedError: raise
                except Exception as exc: logger.debug("忽略无效网络响应：%s", exc)

    def _ingest_payload(self, payload: Any) -> None:
        user = self.platform.extract_user_info(payload)
        if user: self._user_info = user
        for aweme in self.platform.extract_items(payload):
            key = self.platform.item_id(aweme)
            if key: self._raw_awemes.setdefault(key, aweme)

    def _harvest_dom_media(self) -> None:
        """Ingest media URLs the platform only renders into <video> elements.

        TikTok Shop 商品视频常常由播放器直接持有地址，既不出现在接口响应里，
        也不在 SSR 状态中，只能从渲染后的 DOM 里取。
        """
        if not self._driver_alive(): return
        probe = getattr(self.platform, "dom_media_script", lambda: "")()
        if not probe: return
        try:
            entries = self.driver.execute_script(probe)
        except Exception as exc:
            logger.debug("读取页面内媒体地址失败：%s", exc)
            return
        if not isinstance(entries, list): return
        for entry in entries:
            if not isinstance(entry, dict): continue
            url = str(entry.get("url") or "")
            if not url.startswith("http"): continue
            key = self.platform.item_id(entry) or url
            self._raw_awemes.setdefault(key, entry)

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
            # Directly try the payload itself (handles ItemModule single video)
            self._ingest_payload(payload)
            for container in find_item_containers(payload):
                self._ingest_payload(container)


def find_item_containers(node: Any, depth: int = 0) -> Iterable[Any]:
    """Yield dicts that look like post-list containers inside arbitrary SSR JSON."""
    if depth > 6: return
    if isinstance(node, dict):
        if "itemList" in node or "aweme_list" in node or "ItemModule" in node:
            yield node
            # Still walk inside in case deeper containers also contain items
            # (e.g. __DEFAULT_SCOPE__ contains both itemList and ItemModule at different depths)
        for value in node.values():
            yield from find_item_containers(value, depth + 1)
    elif isinstance(node, list):
        for value in node[:80]:
            yield from find_item_containers(value, depth + 1)
