"""
自动化流程控制器
==================
将 Cookie 获取、作品解析、下载整合为一个全自动流程。
用户只需提供博主主页 URL，工具自动完成所有操作。

流程:
1. 用户输入博主主页 URL
2. 自动获取 Cookie（如果已有有效 Cookie 则跳过）
3. 解析博主信息
4. 获取作品列表
5. 自动下载所有作品
6. 返回下载结果
"""

import os
import time
from typing import Optional, Callable

from src.core.douyin_api import DouyinAPI
from src.core.downloader import Downloader
from src.core.auto_cookie import AutoCookieFetcher
from src.utils.cookie_helper import load_cookie, save_cookie, validate_cookie
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AutoPipeline:
    """全自动化处理管道"""

    def __init__(
        self,
        download_dir: str = "downloads",
        headless: bool = True,
        max_retries: int = 3,
    ):
        """
        初始化自动化管道

        Args:
            download_dir: 下载目录
            headless: 浏览器是否无头模式
            max_retries: Cookie 获取重试次数
        """
        self.download_dir = download_dir
        self.headless = headless
        self.max_retries = max_retries

    def run(
        self,
        profile_url: str,
        max_posts: int = 0,
        auto_download: bool = True,
        progress_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ) -> dict:
        """
        执行全自动化流程

        Args:
            profile_url: 抖音博主主页 URL
            max_posts: 最大下载数量，0 表示全部
            auto_download: 是否自动下载
            progress_callback: 进度回调 callback(current, total, message)
            status_callback: 状态回调 callback(status_text)

        Returns:
            结果字典 {
                "success": bool,
                "user_info": dict,
                "posts": list,
                "download_result": dict,
                "error": str or None
            }
        """
        result = {
            "success": False,
            "user_info": None,
            "posts": [],
            "download_result": None,
            "error": None,
        }

        def _status(msg):
            logger.info(msg)
            if status_callback:
                status_callback(msg)

        try:
            # === 步骤 1: 获取 Cookie ===
            _status("步骤 1/4: 正在获取 Cookie...")

            cookie = load_cookie()
            need_fetch = True

            if cookie and validate_cookie(cookie):
                # 尝试使用已有 Cookie
                _status("发现已保存的 Cookie，验证中...")
                api = DouyinAPI(cookie=cookie)
                sec_uid = DouyinAPI.extract_sec_uid(profile_url)
                if sec_uid:
                    test_user = api.get_user_info(sec_uid)
                    if test_user:
                        _status(f"Cookie 有效，博主: {test_user['nickname']}")
                        need_fetch = False
                    else:
                        _status("已有 Cookie 无效，需要重新获取")
                else:
                    _status("已有 Cookie 可能过期，需要重新获取")

            if need_fetch:
                _status("正在启动浏览器自动获取 Cookie...")
                if progress_callback:
                    progress_callback(0, 4, "获取 Cookie 中")

                fetcher = AutoCookieFetcher(headless=self.headless, timeout=30)
                cookie, error = fetcher.fetch_cookie(
                    target_url=profile_url,
                    max_retries=self.max_retries,
                )

                if not cookie:
                    if "selenium" in str(error).lower() or "requests 方式" in str(error):
                result["error"] = (
                    f"Cookie 自动获取失败: {error}\n"
                    f"解决方案:\n"
                    f"1. 安装 selenium: pip install selenium webdriver-manager\n"
                    f"2. 或切换到手动模式，自行提供 Cookie\n"
                    f"3. 获取 Cookie 指南: 访问 https://www.douyin.com → F12 → Network → 复制 Cookie"
                )
            else:
                result["error"] = f"Cookie 获取失败: {error}"
                    _status(f"❌ {result['error']}")
                    return result

                save_cookie(cookie)
                _status("Cookie 获取成功并已保存")

            # === 步骤 2: 解析博主信息 ===
            _status("步骤 2/4: 正在解析博主信息...")

            api = DouyinAPI(cookie=cookie)
            sec_uid = DouyinAPI.extract_sec_uid(profile_url)

            if not sec_uid:
                result["error"] = "无法从链接中提取博主信息，请检查 URL"
                _status(f"❌ {result['error']}")
                return result

            user_info = api.get_user_info(sec_uid)
            if not user_info:
                result["error"] = "获取博主信息失败，Cookie 可能已过期"
                _status(f"❌ {result['error']}")
                return result

            result["user_info"] = user_info
            _status(
                f"博主: {user_info['nickname']} | "
                f"作品: {user_info['aweme_count']} | "
                f"粉丝: {user_info['follower_count']}"
            )

            if progress_callback:
                progress_callback(1, 4, f"博主: {user_info['nickname']}")

            # === 步骤 3: 获取作品列表 ===
            _status("步骤 3/4: 正在获取作品列表...")

            def fetch_progress(count, total, has_more):
                _status(f"  已获取 {count} 个作品...")
                if progress_callback:
                    # 步骤 3 对应进度 1-3
                    sub_progress = count / max(total, 1)
                    overall = 1 + sub_progress
                    progress_callback(overall, 4, f"获取作品 {count}")

            posts = api.get_user_posts(sec_uid, max_count=max_posts, callback=fetch_progress)

            if not posts:
                result["error"] = "未获取到任何作品"
                _status(f"❌ {result['error']}")
                return result

            result["posts"] = posts
            _status(f"共获取到 {len(posts)} 个作品")

            if progress_callback:
                progress_callback(3, 4, f"共 {len(posts)} 个作品")

            # === 步骤 4: 下载作品 ===
            if not auto_download:
                _status("步骤 4/4: 跳过下载（仅获取列表）")
                result["success"] = True
                return result

            _status(f"步骤 4/4: 正在下载 {len(posts)} 个作品...")

            downloader = Downloader(self.download_dir)

            def download_progress(current, total, success_count, is_success):
                symbol = "✓" if is_success else "✗"
                _status(f"  [{current}/{total}] {symbol} 成功:{success_count}")
                if progress_callback:
                    sub = current / max(total, 1)
                    overall = 3 + sub
                    progress_callback(overall, 4, f"下载 {current}/{total}")

            download_result = downloader.download_batch(
                posts, progress_callback=download_progress
            )

            result["download_result"] = download_result
            result["success"] = True

            _status(
                f"完成！成功: {download_result['success']}, "
                f"失败: {download_result['failed']}, "
                f"跳过: {download_result['skipped']}"
            )

            if progress_callback:
                progress_callback(4, 4, "完成")

        except Exception as e:
            result["error"] = f"自动化流程异常: {e}"
            logger.error(f"自动化流程异常: {e}", exc_info=True)
            _status(f"❌ {result['error']}")

        return result

