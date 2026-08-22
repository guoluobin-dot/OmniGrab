"""Normalise public browser responses. Direct API calls are retained for legacy use only."""
from __future__ import annotations

import re
import time
from typing import Any

import requests

from .logger import get_logger
from .risks import ParseError, assert_not_risk_response, random_delay

logger = get_logger(__name__)


class DouyinAPI:
    SEC_UID_PATTERN = re.compile(r"[?&]sec_uid=([^&]+)|/user/([A-Za-z0-9_\-=]+)")
    SHORT_URL_PATTERN = re.compile(r"https?://v\.douyin\.com/")
    DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.douyin.com/"}

    @staticmethod
    def extract_sec_uid(url: str) -> str | None:
        if not url:
            return None
        query_match = re.search(r"[?&]sec_uid=([^&]+)", url)
        if query_match:
            return query_match.group(1)
        match = DouyinAPI.SEC_UID_PATTERN.search(url)
        if match:
            return next((part for part in match.groups() if part), None)
        if DouyinAPI.SHORT_URL_PATTERN.search(url):
            try:
                response = requests.get(url, headers=DouyinAPI.DEFAULT_HEADERS, allow_redirects=True, timeout=10)
                return DouyinAPI.extract_sec_uid(response.url)
            except requests.RequestException:
                return None
        return None

    @staticmethod
    def parse_aweme(aweme: dict[str, Any]) -> dict[str, Any]:
        """Parse defensively, keeping URL-only media by default (R5/R6)."""
        if not isinstance(aweme, dict):
            raise ParseError("作品响应不是对象")
        try:
            aweme_id = str(aweme.get("aweme_id") or "")
            images_info = aweme.get("image_post_info") if isinstance(aweme.get("image_post_info"), dict) else {}
            images = aweme.get("images") or images_info.get("images") or []
            images = images if isinstance(images, list) else []
            is_image = bool(images)
            author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
            stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
            created = int(aweme.get("create_time") or 0)
            post = {
                "aweme_id": aweme_id, "desc": aweme.get("desc") or "无标题", "create_time": created,
                "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)) if created else "未知",
                "type": "image" if is_image else "video", "type_str": "图文" if is_image else "视频",
                "duration": 0, "cover": "", "video_url": "", "image_urls": [], "music_url": "",
                "web_url": f"https://www.douyin.com/{'note' if is_image else 'video'}/{aweme_id}" if aweme_id else "",
                "author": {"nickname": author.get("nickname", ""), "sec_uid": author.get("sec_uid", ""), "uid": author.get("uid", "")},
                "stats": {key: int(stats.get(key, 0) or 0) for key in ("digg_count", "comment_count", "share_count", "play_count")},
            }
            if is_image:
                post["image_urls"] = [url for image in images if isinstance(image, dict) if (url := DouyinAPI._last_url(image) or DouyinAPI._last_url(image.get("display_image")))]
                post["cover"] = post["image_urls"][0] if post["image_urls"] else ""
            else:
                video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
                h264_candidates, other_candidates = [], []
                for variant in video.get("bit_rate", []) or []:
                    if isinstance(variant, dict):
                        bitrate = int(variant.get("bit_rate") or 0)
                        h264_url = DouyinAPI._first_url(variant.get("play_addr_h264"))
                        other_url = DouyinAPI._first_url(variant.get("play_addr"))
                        if h264_url:
                            h264_candidates.append((bitrate, h264_url))
                        elif other_url:
                            other_candidates.append((bitrate, other_url))
                # Qt's Windows backend is significantly more reliable with
                # H.264 than with newer CDN variants such as H.265 or AV1.
                candidates = h264_candidates or other_candidates
                post["video_url"] = DouyinAPI.get_no_watermark_url(max(candidates, default=(0, ""), key=lambda item: item[0])[1])
                if not post["video_url"]:
                    post["video_url"] = DouyinAPI.get_no_watermark_url(next((DouyinAPI._first_url(video.get(key)) for key in ("play_addr_h264", "play_addr", "download_addr") if DouyinAPI._first_url(video.get(key))), ""))
                post["duration"] = int(video.get("duration") or 0)
                post["cover"] = next((DouyinAPI._first_url(video.get(key)) for key in ("origin_cover", "cover", "dynamic_cover") if DouyinAPI._first_url(video.get(key))), "")
            music = aweme.get("music") if isinstance(aweme.get("music"), dict) else {}
            post["music_url"] = DouyinAPI._first_url(music.get("play_url"))
            return post
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("parse_aweme failed; raw aweme id=%r", aweme.get("aweme_id"))
            raise ParseError(f"作品字段结构发生变化：{exc}") from exc

    _parse_aweme = parse_aweme

    @staticmethod
    def _first_url(value: Any) -> str:
        if isinstance(value, str): return value
        if isinstance(value, list): return next((str(item) for item in value if item), "")
        if isinstance(value, dict):
            return next((DouyinAPI._first_url(value.get(key)) for key in ("url_list", "download_url_list", "display_image", "origin_image") if DouyinAPI._first_url(value.get(key))), "")
        return ""

    @staticmethod
    def _last_url(value: Any) -> str:
        if isinstance(value, str): return value
        if isinstance(value, list): return next((str(item) for item in reversed(value) if item), "")
        if isinstance(value, dict):
            return next((DouyinAPI._last_url(value.get(key)) for key in ("url_list", "download_url_list", "display_image", "origin_image") if DouyinAPI._last_url(value.get(key))), "")
        return ""

    @staticmethod
    def get_no_watermark_url(url: str) -> str:
        return url.replace("playwm", "play").replace("watermark=1", "watermark=0") if url else ""

    def __init__(self, cookie: str = "") -> None:
        self.session = requests.Session(); self.session.headers.update(self.DEFAULT_HEADERS)
        if cookie: self.session.headers["Cookie"] = cookie

    def get_user_posts(self, sec_uid: str, max_count: int = 0, callback=None) -> list[dict[str, Any]]:
        """Legacy un-signed endpoint with clear risk failure behaviour; browser collection is preferred."""
        response = self.session.get("https://www.douyin.com/aweme/v1/web/aweme/post/", params={"sec_user_id": sec_uid, "count": min(max_count or 20, 20), "aid": "6383"}, timeout=15)
        assert_not_risk_response(response.text)
        payload = response.json(); assert_not_risk_response(payload)
        posts = [self.parse_aweme(item) for item in payload.get("aweme_list", []) if isinstance(item, dict)]
        if callback: callback(len(posts), len(posts), False)
        random_delay()
        return posts[:max_count] if max_count else posts
