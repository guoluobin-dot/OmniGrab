"""
自动化 Cookie 获取模块
======================
使用 Selenium 浏览器自动化技术，自动访问抖音网站并获取 Cookie。

核心流程:
1. 启动无头浏览器（或可见浏览器）
2. 访问抖音主页 URL
3. 等待页面加载完成
4. 自动提取浏览器中的 Cookie
5. 验证 Cookie 有效性（ttwid, msToken 等）
6. 返回 Cookie 字符串供后续使用

异常处理:
- 浏览器驱动未安装 -> 自动下载
- 页面加载超时 -> 重试机制
- Cookie 获取失败 -> 降级方案
- 页面结构变化 -> 多策略容错
"""

import os
import sys
import time
import json
import random
from typing import Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


class AutoCookieFetcher:
    """自动化 Cookie 获取器"""

    # 抖音主页
    DOUYIN_HOME = "https://www.douyin.com"

    # 必需的 Cookie 字段
    REQUIRED_KEYS = ["ttwid"]
    # 期望的 Cookie 字段（有更好，没有也能用）
    PREFERRED_KEYS = ["msToken", "sessionid", "uid_tt", "sid_tt"]

    # 默认超时时间（秒）
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        headless: bool = True,
        browser_type: str = "chrome",
        proxy: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        """
        初始化自动化 Cookie 获取器

        Args:
            headless: 是否使用无头模式（不显示浏览器窗口）
            browser_type: 浏览器类型，支持 "chrome" 或 "edge"
            proxy: 代理地址，如 "http://127.0.0.1:7890"
            timeout: 页面加载超时时间（秒）
        """
        self.headless = headless
        self.browser_type = browser_type
        self.proxy = proxy
        self.timeout = timeout
        self.driver = None

    def fetch_cookie(
        self,
        target_url: Optional[str] = None,
        max_retries: int = 3,
        wait_for_login: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        自动获取抖音 Cookie

        Args:
            target_url: 目标页面 URL，默认为抖音主页
            max_retries: 最大重试次数
            wait_for_login: 是否等待用户手动登录（弹出可见浏览器）

        Returns:
            (cookie_string, error_message)
            成功时 error_message 为 None
            失败时 cookie_string 为 None
        """
        url = target_url or self.DOUYIN_HOME

        # 如果需要等待登录，则使用可见模式
        if wait_for_login:
            self.headless = False

        for attempt in range(1, max_retries + 1):
            logger.info(f"自动获取 Cookie 尝试 {attempt}/{max_retries}")

            try:
                # 启动浏览器
                self._init_driver()
                if not self.driver:
                    return None, "浏览器驱动初始化失败，请安装 selenium 和浏览器驱动"

                # 访问目标页面
                logger.info(f"正在访问: {url}")
                self.driver.get(url)

                # 等待页面加载
                self._wait_for_page_load()

                # 如果需要等待登录，暂停让用户操作
                if wait_for_login:
                    logger.info("请在浏览器中完成登录，登录后程序将继续...")
                    self._wait_for_login_manual()

                # 尝试获取 Cookie
                cookie = self._extract_cookie()

                if cookie:
                    # 验证 Cookie
                    is_valid, missing = self._validate_cookie(cookie)
                    if is_valid:
                        logger.info("Cookie 获取成功且验证通过!")
                        return cookie, None
                    else:
                        logger.warning(f"Cookie 获取成功但缺少字段: {missing}")
                        # 如果缺少关键字段，但 ttwid 存在，仍然返回
                        if "ttwid" not in missing:
                            logger.info("ttwid 存在，Cookie 可用")
                            return cookie, None

                # Cookie 不完整，尝试滚动页面触发更多 Cookie
                logger.info("Cookie 不完整，尝试触发更多 Cookie...")
                self._scroll_to_trigger_cookies()
                time.sleep(2)

                cookie = self._extract_cookie()
                if cookie:
                    is_valid, _ = self._validate_cookie(cookie)
                    if is_valid or "ttwid" not in _get_missing_keys(cookie, self.REQUIRED_KEYS):
                        return cookie, None

            except Exception as e:
                logger.error(f"尝试 {attempt} 失败: {e}")
                error_msg = str(e)
            finally:
                self._close_driver()

            if attempt < max_retries:
                wait = random.uniform(2, 5)
                logger.info(f"等待 {wait:.1f} 秒后重试...")
                time.sleep(wait)

        return None, f"Cookie 获取失败，已尝试 {max_retries} 次。最后错误: {error_msg if 'error_msg' in dir() else '未知'}"

    def _init_driver(self):
        """初始化浏览器驱动"""
        try:
            if self.browser_type == "chrome":
                self._init_chrome()
            elif self.browser_type == "edge":
                self._init_edge()
            else:
                logger.error(f"不支持的浏览器类型: {self.browser_type}")
        except ImportError as e:
            logger.error(f"Selenium 未安装: {e}")
            logger.error("请运行: pip install selenium webdriver-manager")
            raise
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise

    def _init_chrome(self):
        """初始化 Chrome 浏览器"""
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        except ImportError:
            # 如果没有 webdriver-manager，尝试系统自带的 chromedriver
            service = Service()
            logger.warning("未安装 webdriver-manager，使用系统 chromedriver")

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        if self.proxy:
            options.add_argument(f"--proxy-server={self.proxy}")

        self.driver = webdriver.Chrome(service=service, options=options)

        # 执行 CDP 命令来修改 webdriver 标识
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                """
            },
        )

        self.driver.set_page_load_timeout(self.timeout)
        logger.info("Chrome 浏览器已启动")

    def _init_edge(self):
        """初始化 Edge 浏览器"""
        from selenium import webdriver
        from selenium.webdriver.edge.service import Service
        from selenium.webdriver.edge.options import Options

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
        )

        if self.proxy:
            options.add_argument(f"--proxy-server={self.proxy}")

        service = Service()
        self.driver = webdriver.Edge(service=service, options=options)
        self.driver.set_page_load_timeout(self.timeout)
        logger.info("Edge 浏览器已启动")

    def _wait_for_page_load(self):
        """等待页面加载完成"""
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            # 等待 body 标签出现
            WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            logger.info("页面已加载")

            # 额外等待动态内容加载
            time.sleep(3)

        except Exception as e:
            logger.warning(f"等待页面加载超时: {e}")
            # 超时后仍然继续尝试获取 Cookie

    def _wait_for_login_manual(self):
        """等待用户手动登录"""
        print("\n" + "=" * 50)
        print("  浏览器已打开，请在浏览器中登录抖音账号")
        print("  登录成功后，请回到终端按 Enter 继续...")
        print("=" * 50 + "\n")
        input("按 Enter 键继续...")

    def _extract_cookie(self) -> Optional[str]:
        """从浏览器中提取 Cookie"""
        try:
            cookies = self.driver.get_cookies()
            if not cookies:
                logger.warning("未获取到任何 Cookie")
                return None

            # 将 Cookie 列表转换为字符串
            cookie_parts = []
            for cookie in cookies:
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                if name and value:
                    cookie_parts.append(f"{name}={value}")

            cookie_str = "; ".join(cookie_parts)
            logger.info(f"获取到 {len(cookie_parts)} 个 Cookie 字段")
            return cookie_str

        except Exception as e:
            logger.error(f"提取 Cookie 失败: {e}")
            return None

    def _scroll_to_trigger_cookies(self):
        """滚动页面以触发更多 Cookie 设置"""
        try:
            for i in range(3):
                self.driver.execute_script(
                    f"window.scrollTo(0, {300 * (i + 1)});"
                )
                time.sleep(1)

            # 模拟鼠标移动
            actions_script = """
                var event = new MouseEvent('mousemove', {
                    'view': window,
                    'bubbles': true,
                    'cancelable': true
                });
                document.dispatchEvent(event);
            """
            self.driver.execute_script(actions_script)
            time.sleep(1)

        except Exception as e:
            logger.warning(f"滚动触发 Cookie 失败: {e}")

    def _validate_cookie(self, cookie: str) -> Tuple[bool, list]:
        """
        验证 Cookie 是否包含必需字段

        Returns:
            (is_valid, missing_keys)
        """
        missing = _get_missing_keys(cookie, self.REQUIRED_KEYS)
        is_valid = len(missing) == 0
        return is_valid, missing

    def _close_driver(self):
        """关闭浏览器驱动"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None


def _get_missing_keys(cookie: str, required_keys: list) -> list:
    """检查 Cookie 中缺少哪些必需字段"""
    missing = []
    for key in required_keys:
        if f"{key}=" not in cookie:
            missing.append(key)
    return missing


def auto_fetch_cookie(
    target_url: Optional[str] = None,
    headless: bool = True,
    wait_for_login: bool = False,
    max_retries: int = 3,
) -> Tuple[Optional[str], Optional[str]]:
    """
    便捷函数：自动获取抖音 Cookie

    Args:
        target_url: 目标页面 URL
        headless: 是否无头模式
        wait_for_login: 是否等待手动登录
        max_retries: 重试次数

    Returns:
        (cookie_string, error_message)
    """
    fetcher = AutoCookieFetcher(headless=headless, timeout=30)
    cookie, error = fetcher.fetch_cookie(
        target_url=target_url,
        max_retries=max_retries,
        wait_for_login=wait_for_login,
    )

    if cookie:
        # 保存到配置文件
        from src.utils.cookie_helper import save_cookie
        save_cookie(cookie)
        logger.info("Cookie 已自动保存到配置文件")

    return cookie, error
