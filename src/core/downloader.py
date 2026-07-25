"""
下载器模块
============
负责下载视频和图片到本地，支持进度显示、断点续传、去重。
"""

import os
import time
import requests
from typing import Optional, Callable

from src.utils.logger import get_logger
from src.utils.database import DownloadDB

logger = get_logger(__name__)


class Downloader:
    """文件下载器"""

    def __init__(self, download_dir: str = "downloads", max_retry: int = 3):
        """
        初始化下载器

        Args:
            download_dir: 下载根目录
            max_retry: 最大重试次数
        """
        self.download_dir = download_dir
        self.max_retry = max_retry
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
        })

        # 确保下载目录存在
        self.videos_dir = os.path.join(download_dir, "videos")
        self.images_dir = os.path.join(download_dir, "images")
        os.makedirs(self.videos_dir, exist_ok=True)
        os.makedirs(self.images_dir, exist_ok=True)

        # 初始化数据库（用于去重）
        self.db = DownloadDB(os.path.join(download_dir, "history.db"))

    def download_file(
        self,
        url: str,
        filepath: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        下载单个文件

        Args:
            url: 文件下载 URL
            filepath: 本地保存路径
            progress_callback: 进度回调 callback(downloaded, total)

        Returns:
            下载是否成功
        """
        if not url:
            logger.error("下载 URL 为空")
            return False

        # 检查文件是否已存在（断点续传）
        if os.path.exists(filepath):
            logger.info(f"文件已存在，跳过: {filepath}")
            return True

        for attempt in range(1, self.max_retry + 1):
            try:
                resp = self.session.get(url, stream=True, timeout=30)
                resp.raise_for_status()

                total_size = int(resp.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 8192

                # 确保目录存在
                os.makedirs(os.path.dirname(filepath), exist_ok=True)

                with open(filepath, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, total_size)

                logger.info(f"下载完成: {filepath} ({downloaded} bytes)")
                return True

            except Exception as e:
                logger.warning(f"下载失败 (尝试 {attempt}/{self.max_retry}): {e}")
                if attempt < self.max_retry:
                    time.sleep(2 * attempt)
                else:
                    logger.error(f"下载最终失败: {filepath}")
                    return False

        return False

    def download_video(
        self,
        post: dict,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        下载单个视频作品

        Args:
            post: 作品信息字典
            progress_callback: 进度回调

        Returns:
            下载是否成功
        """
        aweme_id = post.get("aweme_id", "")

        # 检查是否已下载过（去重）
        if self.db.is_downloaded(aweme_id):
            logger.info(f"作品已下载过，跳过: {aweme_id}")
            return True

        video_url = post.get("video_url", "")
        if not video_url:
            logger.error(f"作品 {aweme_id} 无视频链接")
            return False

        # 生成安全文件名
        desc = self._safe_filename(post.get("desc", aweme_id))
        filename = f"{desc}_{aweme_id}.mp4"
        filepath = os.path.join(self.videos_dir, filename)

        success = self.download_file(video_url, filepath, progress_callback)

        if success:
            # 记录到数据库
            self.db.add_record(aweme_id, post.get("desc", ""), "video", filepath)

        return success

    def download_image_set(
        self,
        post: dict,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        下载图集作品

        Args:
            post: 作品信息字典
            progress_callback: 进度回调

        Returns:
            下载是否成功
        """
        aweme_id = post.get("aweme_id", "")

        if self.db.is_downloaded(aweme_id):
            logger.info(f"图集已下载过，跳过: {aweme_id}")
            return True

        image_urls = post.get("image_urls", [])
        if not image_urls:
            logger.error(f"作品 {aweme_id} 无图片链接")
            return False

        # 为图集创建子目录
        desc = self._safe_filename(post.get("desc", aweme_id))
        folder_name = f"{desc}_{aweme_id}"
        folder_path = os.path.join(self.images_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)

        all_success = True
        for i, url in enumerate(image_urls):
            ext = ".jpg"
            if ".png" in url:
                ext = ".png"
            elif ".webp" in url:
                ext = ".webp"

            filename = f"{i + 1:03d}{ext}"
            filepath = os.path.join(folder_path, filename)

            success = self.download_file(url, filepath, progress_callback)
            if not success:
                all_success = False

        if all_success:
            self.db.add_record(aweme_id, post.get("desc", ""), "image", folder_path)

        return all_success

    def download_post(
        self,
        post: dict,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """
        下载单个作品（自动判断类型）

        Args:
            post: 作品信息字典
            progress_callback: 进度回调

        Returns:
            下载是否成功
        """
        post_type = post.get("type", "video")
        if post_type == "image":
            return self.download_image_set(post, progress_callback)
        else:
            return self.download_video(post, progress_callback)

    def download_batch(
        self,
        posts: list,
        progress_callback: Optional[Callable[[int, int, int, bool], None]] = None,
    ) -> dict:
        """
        批量下载作品

        Args:
            posts: 作品列表
            progress_callback: 进度回调 callback(current, total, success_count, is_current_success)

        Returns:
            下载结果统计 {"success": N, "failed": N, "skipped": N}
        """
        total = len(posts)
        success_count = 0
        failed_count = 0
        skipped_count = 0

        for i, post in enumerate(posts):
            current = i + 1
            aweme_id = post.get("aweme_id", "")

            # 检查去重
            if self.db.is_downloaded(aweme_id):
                skipped_count += 1
                logger.info(f"[{current}/{total}] 跳过已下载: {aweme_id}")
                if progress_callback:
                    progress_callback(current, total, success_count, True)
                continue

            logger.info(f"[{current}/{total}] 正在下载: {post.get('desc', '')[:30]}...")

            success = self.download_post(post)

            if success:
                success_count += 1
            else:
                failed_count += 1

            if progress_callback:
                progress_callback(current, total, success_count, success)

        result = {
            "success": success_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "total": total,
        }

        logger.info(
            f"批量下载完成: 成功 {success_count}, 失败 {failed_count}, 跳过 {skipped_count}, 总计 {total}"
        )

        return result

    @staticmethod
    def _safe_filename(name: str, max_length: int = 50) -> str:
        """
        生成安全的文件名（去除非法字符）

        Args:
            name: 原始名称
            max_length: 最大长度

        Returns:
            安全的文件名
        """
        # 替换 Windows 文件名中的非法字符
        illegal_chars = r'<>:"/\|?*'
        for char in illegal_chars:
            name = name.replace(char, "_")

        # 去除首尾空格和点
        name = name.strip(". ")

        # 截断过长的名称
        if len(name) > max_length:
            name = name[:max_length]

        # 如果为空，使用默认名
        if not name:
            name = "unnamed"

        return name
