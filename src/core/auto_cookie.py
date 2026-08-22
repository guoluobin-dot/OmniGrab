"""
自动化 Cookie 获取模块
======================
两种方式自动获取抖音 Cookie:
1. requests 方式（无需额外依赖）- 直接请求抖音网页获取基础 Cookie
2. selenium 方式（需要 selenium）- 浏览器自动化获取完整 Cookie

优先使用 selenium，如果未安装则降级为 requests 方式。
"""

import os
import re
import time
import json
import random
import requests
from typing import Iterable, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _get_missing_keys(cookie: str, required_keys: Iterable[str]) -> List[str]:
    """返回 Cookie 中缺失或为空的必需字段。

    使用精确的 Cookie 名称匹配，避免 ``not_ttwid=value`` 之类的字段被
    误认为是 ``ttwid``。
    """
    required = list(required_keys)
    if not cookie:
        return required

    present_keys = set()
    for item in cookie.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name and value:
            present_keys.add(name)

    return [key for key in required if key not in present_keys]


class AutoCookieFetcher:
    """自动化 Cookie 获取器"""

    DOUYIN_HOME = "https://www.douyin.com"
    REQUIRED_KEYS = ["ttwid"]
    DEFAULT_TIMEOUT = 30

    def __init__(self, headless: bool = True, browser_type: str = "chrome",
                 proxy: Optional[dict] = None, timeout: int = DEFAULT_TIMEOUT):
        self.headless = headless
        self.browser_type = browser_type
        self.proxy = proxy
        self.timeout = timeout
        self.driver = None
        self._has_selenium = self._check_selenium()

    @staticmethod
    def _check_selenium() -> bool:
        """检查 selenium 是否可用"""
        try:
            import selenium
            return True
        except ImportError:
            return False

    def fetch_cookie(self, target_url: Optional[str] = None,
                     max_retries: int = 3, wait_for_login: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        自动获取 Cookie
        优先 selenium，降级 requests
        """
        url = target_url or self.DOUYIN_HOME

        if self._has_selenium:
            logger.info("使用 Selenium 浏览器自动化获取 Cookie")
            return self._fetch_with_selenium(url, max_retries, wait_for_login)

        if wait_for_login:
            return None, "手动登录需要安装 selenium 和可用的浏览器驱动"

        logger.info("Selenium 未安装，使用 requests 方式获取 Cookie")
        return self._fetch_with_requests(url, max_retries)

    def _fetch_with_requests(self, url: str, max_retries: int) -> Tuple[Optional[str], Optional[str]]:
        """使用 requests 获取 Cookie（无需 selenium）"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        session = requests.Session()
        session.headers.update(headers)
        if self.proxy:
            session.proxies.update(self.proxy)

        for attempt in range(1, max_retries + 1):
            logger.info(f"requests 方式获取 Cookie 尝试 {attempt}/{max_retries}")

            try:
                # 第一次请求获取基础 Cookie
                resp = session.get(url, timeout=self.timeout, allow_redirects=True)
                logger.info(f"响应状态码: {resp.status_code}")

                # 提取 Set-Cookie
                cookies = session.cookies.get_dict()
                logger.info(f"获取到 {len(cookies)} 个 Cookie 字段: {list(cookies.keys())}")

                # 构造 Cookie 字符串
                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

                if cookie_str:
                    # 尝试获取 ttwid（可能需要第二次请求）
                    if "ttwid" not in cookies:
                        logger.info("首次请求未获取 ttwid，尝试触发...")
                        # 访问推荐页面触发更多 Cookie
                        try:
                            resp2 = session.get(
                                "https://www.douyin.com/aweme/v1/web/general/search/single/",
                                params={"device_platform": "webapp", "aid": "6383", "keyword": "test", "count": 1},
                                timeout=10,
                            )
                            cookies2 = session.cookies.get_dict()
                            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies2.items())
                            logger.info(f"二次获取到 {len(cookies2)} 个 Cookie 字段")
                        except Exception as e:
                            logger.warning(f"二次获取 Cookie 失败: {e}")

                    # 手动生成 ttwid（抖音的 ttwid 可以通过特定 API 获取）
                    if "ttwid" not in cookies:
                        logger.info("尝试通过 API 获取 ttwid...")
                        ttwid = self._generate_ttwid(session)
                        if ttwid:
                            cookies["ttwid"] = ttwid
                            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

                    # 验证
                    is_valid, missing = self._validate_cookie(cookie_str)
                    if is_valid or "ttwid" not in missing:
                        logger.info("Cookie 获取成功!")
                        return cookie_str, None
                    else:
                        logger.warning(f"Cookie 缺少必需字段: {missing}")

                # 尝试从响应头中提取 Set-Cookie
                set_cookies = resp.headers.get("Set-Cookie", "")
                if set_cookies and "ttwid" in set_cookies:
                    logger.info("从响应头提取到 ttwid")
                    # 解析 Set-Cookie
                    for part in set_cookies.split(","):
                        if "ttwid=" in part:
                            ttwid_match = re.search(r'ttwid=([^;]+)', part)
                            if ttwid_match:
                                cookies["ttwid"] = ttwid_match.group(1)
                                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                                return cookie_str, None

            except Exception as e:
                logger.error(f"requests 获取 Cookie 失败 (尝试 {attempt}): {e}")

            if attempt < max_retries:
                time.sleep(random.uniform(2, 5))

        return None, "requests 方式无法获取完整 Cookie，请安装 selenium (pip install selenium webdriver-manager) 或手动提供 Cookie"

    def _generate_ttwid(self, session: requests.Session) -> Optional[str]:
        """通过抖音 API 生成 ttwid"""
        try:
            # 抖音的 ttwid 可以通过访问特定端点获取
            resp = session.post(
                "https://ttwid.bytedance.com/ttwid/union_register/",
                json={
                    "region": "cn",
                    "aid": 1768,
                    "needFid": False,
                    "service": "www.douyin.com",
                    "migrate_info": {"ticket": "", "source": "node"},
                    "cbUrl": "https://www.douyin.com",
                    "union": True,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                # ttwid 在 Set-Cookie 响应头中
                set_cookie = resp.headers.get("Set-Cookie", "")
                match = re.search(r'ttwid=([^;]+)', set_cookie)
                if match:
                    logger.info("通过 API 成功获取 ttwid")
                    return match.group(1)
        except Exception as e:
            logger.warning(f"生成 ttwid 失败: {e}")
        return None

    def _fetch_with_selenium(self, url: str, max_retries: int, wait_for_login: bool) -> Tuple[Optional[str], Optional[str]]:
        """使用 selenium 获取 Cookie"""
        error_msg = "未知错误"

        if wait_for_login:
            self.headless = False

        for attempt in range(1, max_retries + 1):
            logger.info(f"Selenium 获取 Cookie 尝试 {attempt}/{max_retries}")

            try:
                self._init_driver()
                if not self.driver:
                    return None, "浏览器驱动初始化失败"

                logger.info(f"正在访问: {url}")
                self.driver.get(url)
                self._wait_for_page_load()

                if wait_for_login:
                    self._wait_for_login_manual()

                cookie = self._extract_cookie()
                if cookie:
                    is_valid, missing = self._validate_cookie(cookie)
                    if is_valid:
                        return cookie, None
                    elif "ttwid" not in missing:
                        return cookie, None

                logger.info("Cookie 不完整，尝试滚动触发...")
                self._scroll_to_trigger_cookies()
                time.sleep(2)

                cookie = self._extract_cookie()
                if cookie:
                    is_valid, missing = self._validate_cookie(cookie)
                    if is_valid or "ttwid" not in missing:
                        return cookie, None

            except Exception as e:
                error_msg = str(e)
                logger.error(f"尝试 {attempt} 失败: {e}")
            finally:
                self._close_driver()

            if attempt < max_retries:
                time.sleep(random.uniform(2, 5))

        return None, f"Cookie 获取失败: {error_msg}"

    def _init_driver(self):
        try:
            if self.browser_type == "edge":
                self._init_edge()
            else:
                self._init_chrome()
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise

    def _init_chrome(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options

        try:
            # 优先使用已缓存的 chromedriver
            import os
            home = os.path.expanduser("~")
            cached_paths = []
            wdm_base = os.path.join(home, ".wdm", "drivers", "chromedriver")
            if os.path.exists(wdm_base):
                for root, dirs, files in os.walk(wdm_base):
                    for f in files:
                        if f == "chromedriver.exe":
                            cached_paths.append(os.path.join(root, f))

            if cached_paths:
                service = Service(cached_paths[0])
                logger.info(f"使用缓存 chromedriver: {cached_paths[0]}")
            else:
                from webdriver_manager.chrome import ChromeDriverManager
                service = Service(ChromeDriverManager().install())
        except Exception as e:
            logger.warning(f"webdriver-manager 失败，尝试系统 chromedriver: {e}")
            service = Service()

        options = Options()
        if self.headless:
            options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-notifications")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        if self.proxy:
            options.add_argument(f"--proxy-server={self.proxy}")

        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        })
        self.driver.set_page_load_timeout(self.timeout)

    def _init_edge(self):
        from selenium import webdriver
        from selenium.webdriver.edge.service import Service
        from selenium.webdriver.edge.options import Options

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")

        self.driver = webdriver.Edge(service=Service(), options=options)
        self.driver.set_page_load_timeout(self.timeout)

    def _wait_for_page_load(self):
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)
        except Exception as e:
            logger.warning(f"等待页面加载超时: {e}")

    def _wait_for_login_manual(self):
        input("请在浏览器中登录后按 Enter 继续...")

    def _extract_cookie(self) -> Optional[str]:
        try:
            cookies = self.driver.get_cookies()
            if not cookies:
                return None
            return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value"))
        except Exception as e:
            logger.error(f"提取 Cookie 失败: {e}")
            return None

    def _scroll_to_trigger_cookies(self):
        try:
            for i in range(3):
                self.driver.execute_script(f"window.scrollTo(0, {300 * (i + 1)});")
                time.sleep(1)
        except Exception:
            pass

    def _validate_cookie(self, cookie: str) -> Tuple[bool, list]:
        missing = _get_missing_keys(cookie, self.REQUIRED_KEYS)
        return len(missing) == 0, missing

    def _close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


def auto_fetch_cookie(target_url: Optional[str] = None, headless: bool = True,
                      wait_for_login: bool = False, max_retries: int = 3) -> Tuple[Optional[str], Optional[str]]:
    fetcher = AutoCookieFetcher(headless=headless, timeout=30)
    cookie, error = fetcher.fetch_cookie(target_url=target_url, max_retries=max_retries, wait_for_login=wait_for_login)

    if cookie:
        from src.utils.cookie_helper import save_cookie
        save_cookie(cookie)

    return cookie, error
