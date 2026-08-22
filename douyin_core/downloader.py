from __future__ import annotations

import os
import re
import time
from threading import Event
from typing import Callable
from urllib.parse import urlparse

import requests

from .database import DownloadDB
from .logger import get_logger
from .risks import RiskBlockedError

logger = get_logger(__name__)


class _DownloadCancelled(Exception):
    pass


class Downloader:
    """Downloads only when explicitly invoked; collection returns URLs by default."""
    def __init__(self, download_dir: str = "downloads", max_retry: int = 3, deduplicate: bool = True, cookie: str = "", headers: dict | None = None, session: requests.Session | None = None, cancel_event: Event | None = None) -> None:
        self.download_dir, self.max_retry, self.session = download_dir, max_retry, session or requests.Session()
        self.last_error = ""
        self._cancel_event = cancel_event
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.douyin.com/"})
        if cookie: self.session.headers["Cookie"] = cookie
        if headers: self.session.headers.update(headers)
        self.videos_dir, self.images_dir = os.path.join(download_dir, "videos"), os.path.join(download_dir, "images")
        os.makedirs(self.videos_dir, exist_ok=True); os.makedirs(self.images_dir, exist_ok=True)
        self._image_indexes = self._existing_image_indexes()
        self.db = DownloadDB(os.path.join(download_dir, "history.db")) if deduplicate else None

    def cancel(self) -> None:
        """Request cancellation without forcibly terminating the active thread."""
        if self._cancel_event is None:
            self._cancel_event = Event()
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return bool(self._cancel_event and self._cancel_event.is_set())

    def download_file(self, url: str, filepath: str, progress_callback: Callable[[int, int], None] | None = None, headers: dict | None = None) -> bool:
        if self.is_cancelled():
            self.last_error = "下载已被用户中断"
            return False
        if not url:
            self.last_error = "作品没有可用的媒体下载地址"
            return False
        if os.path.exists(filepath): return True
        for attempt in range(1, self.max_retry + 1):
            if self.is_cancelled():
                self.last_error = "下载已被用户中断"
                return False
            temp_path = f"{filepath}.part"
            try:
                response = self.session.get(url, stream=True, timeout=30, allow_redirects=True, headers=headers); response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type or "application/json" in content_type: raise RiskBlockedError(f"下载地址返回非媒体内容 ({content_type})")
                os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True); total, downloaded = int(response.headers.get("content-length", 0)), 0
                with open(temp_path, "wb") as output:
                    for chunk in response.iter_content(chunk_size=8192):
                        if self.is_cancelled():
                            raise _DownloadCancelled()
                        if chunk: output.write(chunk); downloaded += len(chunk); progress_callback and progress_callback(downloaded, total)
                if not downloaded: raise ValueError("媒体响应为空")
                os.replace(temp_path, filepath); return True
            except _DownloadCancelled:
                self.last_error = "下载已被用户中断"
                if os.path.exists(temp_path): os.remove(temp_path)
                return False
            except Exception as exc:
                self.last_error = str(exc)
                if os.path.exists(temp_path): os.remove(temp_path)
                logger.warning("下载失败 (%d/%d): %s", attempt, self.max_retry, exc)
                if attempt < self.max_retry: time.sleep(2 * attempt)
        return False

    def download_video(self, post: dict, progress_callback=None) -> bool:
        aweme_id = post.get("aweme_id", "")
        if self.db and self.db.is_downloaded(aweme_id): return True
        path = os.path.join(self.videos_dir, f"{self._post_stem(post)}.mp4")
        success = self.download_file(post.get("video_url", ""), path, progress_callback, headers=self._post_headers(post))
        if success:
            self._apply_publish_time(path, post)
        if success and self.db: self.db.add_record(aweme_id, post.get("desc", ""), "video", path)
        return success

    def download_image_set(self, post: dict, progress_callback=None) -> bool:
        aweme_id = post.get("aweme_id", "")
        if self.db and self.db.is_downloaded(aweme_id): return True
        image_urls = post.get("image_urls", [])
        if not image_urls:
            self.last_error = "图文作品没有可用的图片地址"
            return False
        date_key = self._image_date_key(post)
        first_index = self._reserve_image_indexes(date_key, len(image_urls))
        file_paths = [
            os.path.join(
                self.images_dir,
                f"{date_key}_{first_index + offset}{self._image_extension(url)}",
            )
            for offset, url in enumerate(image_urls)
        ]
        success = all(
            self.download_file(url, file_path, progress_callback, headers=self._post_headers(post))
            for url, file_path in zip(image_urls, file_paths)
        )
        if not success and self.is_cancelled():
            # A record is only created for a complete image post. Remove files
            # produced for an interrupted set so a later resume stays clean.
            for file_path in file_paths:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except OSError:
                    logger.warning("Unable to remove partial image file: %s", file_path)
        if success:
            for file_path in file_paths:
                self._apply_publish_time(file_path, post)
        if success and self.db:
            self.db.add_record(aweme_id, post.get("desc", ""), "image", file_paths)
        return success

    def download_post(self, post: dict, progress_callback=None) -> bool: return self.download_image_set(post, progress_callback) if post.get("type") == "image" else self.download_video(post, progress_callback)
    def download_batch(self, posts: list[dict], progress_callback=None) -> dict:
        result = {"success": 0, "failed": 0, "skipped": 0, "total": len(posts)}
        # Sort by the author's publish time rather than the page/API order.
        # The rank is also embedded in names so Explorer's normal name-ascending
        # order shows newest work at the top and oldest at the bottom.
        ordered_posts = sorted(posts, key=lambda post: self._publish_timestamp(post), reverse=True)
        width = max(3, len(str(len(ordered_posts))))
        for index, original_post in enumerate(ordered_posts, 1):
            if self.is_cancelled():
                result.update(cancelled=True, remaining=len(ordered_posts) - index + 1)
                break
            post = dict(original_post)
            post["_download_order"] = index
            post["_download_order_width"] = width
            if self.db and self.db.is_downloaded(post.get("aweme_id", "")):
                result["skipped"] += 1
                result.setdefault("skipped_details", []).append(
                    self._result_detail(post, "本地已有完整文件，已跳过重复下载")
                )
                success = True
            else:
                self.last_error = ""
                success = self.download_post(post)
                if success:
                    result["success"] += 1
                if self.is_cancelled():
                    result.update(cancelled=True, remaining=len(ordered_posts) - index)
                    break
                else:
                    if not success:
                        result["failed"] += 1
                        result.setdefault("failed_details", []).append(
                            self._result_detail(post, self.last_error or "下载未完成")
                        )
            if progress_callback: progress_callback(index, len(ordered_posts), result["success"], success)
        return result

    @staticmethod
    def _post_headers(post: dict) -> dict | None:
        """Per-platform media servers validate their own Referer (e.g. TikTok)."""
        referer = str(post.get("referer") or "")
        return {"Referer": referer} if referer else None

    @staticmethod
    def _result_detail(post: dict, reason: str) -> dict:
        return {
            "aweme_id": str(post.get("aweme_id", "")),
            "title": str(post.get("desc") or post.get("aweme_id") or "未命名作品"),
            "reason": reason,
        }

    @staticmethod
    def _publish_timestamp(post: dict) -> int:
        try:
            return max(0, int(post.get("create_time", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _post_stem(self, post: dict) -> str:
        timestamp = self._publish_timestamp(post)
        published_at = time.strftime("%Y%m%d_%H%M%S", time.localtime(timestamp)) if timestamp else "00000000_000000"
        order = post.get("_download_order")
        if isinstance(order, int):
            width = int(post.get("_download_order_width", 3) or 3)
            prefix = f"{order:0{width}d}_{published_at}"
        else:
            prefix = published_at
        return f"{prefix}_{self._safe_filename(post.get('desc', post.get('aweme_id', '')))}_{post.get('aweme_id', '')}"

    def _existing_image_indexes(self) -> dict[str, int]:
        indexes: dict[str, int] = {}
        pattern = re.compile(r"^(\d{8})_(\d+)(?:\.[^.]+)$")
        try:
            entries = os.scandir(self.images_dir)
        except OSError:
            return indexes
        with entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                match = pattern.match(entry.name)
                if match:
                    date_key, index_text = match.groups()
                    indexes[date_key] = max(indexes.get(date_key, 0), int(index_text))
        return indexes

    def _reserve_image_indexes(self, date_key: str, count: int) -> int:
        first_index = self._image_indexes.get(date_key, 0) + 1
        self._image_indexes[date_key] = first_index + count - 1
        return first_index

    def _image_date_key(self, post: dict) -> str:
        timestamp = self._publish_timestamp(post)
        return time.strftime("%Y%m%d", time.localtime(timestamp)) if timestamp else "00000000"

    @staticmethod
    def _image_extension(url: str) -> str:
        extension = os.path.splitext(urlparse(url).path)[1].lower()
        return extension if extension in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"

    def _apply_publish_time(self, path: str, post: dict) -> None:
        timestamp = self._publish_timestamp(post)
        if timestamp:
            try:
                os.utime(path, (timestamp, timestamp))
            except OSError:
                logger.debug("无法设置作品发布时间：%s", path)
    @staticmethod
    def _safe_filename(name: str, max_length: int = 50) -> str:
        """Create a Windows-safe title fragment for a file or directory name.

        Douyin descriptions frequently contain real line breaks.  They are
        control characters on Windows and must be removed in addition to the
        familiar ``<>:"/\\|?*`` characters.
        """
        text = str(name)
        text = re.sub(r'[\x00-\x1f\x7f<>:"/\\\\|?*]+', "_", text)
        text = re.sub(r"\s+", " ", text).strip(". ")
        text = text[:max_length].rstrip(". ")
        if not text:
            return "unnamed"

        # Windows does not allow these device names, even when an extension is
        # present (for example ``CON.txt``).
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if text.split(".", 1)[0].upper() in reserved:
            text = f"_{text}"[:max_length].rstrip(". ")
        return text or "unnamed"
