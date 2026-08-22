from __future__ import annotations
from threading import Event
from typing import Callable
from .browser_reader import BrowserProfileReader, BrowserReadError
from .downloader import Downloader
from .logger import get_logger

logger = get_logger(__name__)
class AutoPipeline:
    def __init__(self, download_dir: str = "downloads", headless: bool = False, max_retries: int = 1, max_scrolls: int = 240, browser_profile_dir: str | None = None) -> None:
        self.download_dir, self.headless, self.max_retries, self.max_scrolls, self.browser_profile_dir = download_dir, headless, max_retries, max_scrolls, browser_profile_dir
    def run(
        self,
        profile_url: str,
        max_posts: int = 0,
        auto_download: bool = True,
        progress_callback: Callable | None = None,
        status_callback: Callable | None = None,
        cookie: str = "",
        deduplicate: bool = True,
        continue_event: Event | None = None,
        cancel_event: Event | None = None,
        login_wait_callback: Callable | None = None,
    ) -> dict:
        result = {"success": False, "user_info": None, "posts": [], "download_result": None, "error": None, "cookie": "", "user_agent": ""}
        try:
            reader = BrowserProfileReader(
                cookie=cookie,
                headless=self.headless,
                max_scrolls=self.max_scrolls,
                browser_profile_dir=self.browser_profile_dir,
                continue_event=continue_event,
                cancel_event=cancel_event,
                login_wait_callback=login_wait_callback,
            )
            read = reader.read_profile(profile_url, max_count=max_posts, status_callback=status_callback, progress_callback=progress_callback)
            result.update(posts=read.posts, user_info=read.user_info, cookie=read.cookie or cookie, user_agent=read.user_agent)
            if auto_download:
                result["download_result"] = Downloader(self.download_dir, deduplicate=deduplicate, cookie=result["cookie"], headers={"User-Agent": read.user_agent}).download_batch(read.posts, progress_callback=progress_callback)
            result["success"] = True
        except Exception as exc:
            result["error"] = str(exc); logger.exception("流程失败")
            if status_callback: status_callback(f"读取失败：{exc}")
        return result
