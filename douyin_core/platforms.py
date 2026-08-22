"""Per-platform adapters for response matching, parsing and login detection.

Each adapter describes how one site's network responses are recognised and
parsed, and which signals indicate a login gate or a completed login. The
browser reader stays platform-agnostic and delegates every site-specific
decision here.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from .douyin_api import DouyinAPI
from .risks import ParseError


def extract_aweme_list(payload: Any) -> list[dict]:
    for candidate in (
        payload,
        payload.get("data") if isinstance(payload, dict) else None,
        payload.get("result") if isinstance(payload, dict) else None,
    ):
        if isinstance(candidate, dict) and isinstance(candidate.get("aweme_list"), list):
            return [value for value in candidate["aweme_list"] if isinstance(value, dict)]
    return []


def extract_user_info(payload: Any) -> dict[str, Any]:
    for candidate in (
        payload,
        payload.get("data") if isinstance(payload, dict) else None,
        payload.get("result") if isinstance(payload, dict) else None,
    ):
        user = candidate.get("user") or candidate.get("user_info") if isinstance(candidate, dict) else None
        if isinstance(user, dict):
            avatar = user.get("avatar_thumb") or user.get("avatar_medium") or {}
            urls = avatar.get("url_list", []) if isinstance(avatar, dict) else []
            return {
                "nickname": user.get("nickname", "未知"),
                "sec_uid": user.get("sec_uid", ""),
                "uid": user.get("uid", ""),
                "follower_count": user.get("follower_count", 0),
                "following_count": user.get("following_count", 0),
                "aweme_count": user.get("aweme_count", 0),
                "favoriting_count": user.get("favoriting_count", 0),
                "signature": user.get("signature", ""),
                "avatar": urls[0] if urls else "",
            }
    return {}


class PlatformAdapter(ABC):
    """Contract shared by every supported platform."""

    name: str = ""
    home_url: str = ""
    cookie_domain: str = ""
    # Cookies that only exist after a real interactive login.
    login_cookie_names: tuple[str, ...] = ()
    # Body-text markers that indicate the page is gated behind login/captcha.
    login_gate_keywords: tuple[str, ...] = ()
    # DOM markers (CSS selectors) for captcha/login overlays.
    gate_selectors: tuple[str, ...] = ()
    # False until the adapter is fully implemented.
    implemented: bool = True

    @abstractmethod
    def matches(self, url: str) -> bool:
        """Return True when ``url`` belongs to this platform."""

    @abstractmethod
    def is_profile_response(self, url: str) -> bool:
        """Return True when ``url`` is a profile/post-list API response."""

    @abstractmethod
    def extract_items(self, payload: Any) -> list[dict]:
        """Extract raw post dicts from a captured response payload."""

    @abstractmethod
    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        """Extract author/profile information from a captured payload."""

    @abstractmethod
    def parse_item(self, raw: dict) -> dict[str, Any]:
        """Normalise one raw post into the shared post schema."""

    def item_id(self, raw: dict) -> str:
        """Stable unique key used to deduplicate captured posts."""
        return str(raw.get("aweme_id") or "")

    def dom_data_script_ids(self) -> tuple[str, ...]:
        """Script-tag ids carrying SSR data worth harvesting (empty = none)."""
        return ()

    def gate_probe_script(self) -> str:
        """Build the in-page JS probe used by the reader's login-gate check.

        Body text is lowercased so English keywords match any capitalisation;
        CJK keywords are unaffected.
        """
        keywords = json.dumps([k.lower() for k in self.login_gate_keywords], ensure_ascii=False)
        selectors = ", ".join(self.gate_selectors)
        probe = f"const ks={keywords}; const t=(document.body?(document.body.innerText||''):'').toLowerCase(); if (ks.some(x=>t.includes(x))) return true;"
        if selectors:
            escaped = selectors.replace("'", "\\'")
            probe += f" return Boolean(document.querySelector('{escaped}'));"
        else:
            probe += " return false;"
        return probe


class DouyinAdapter(PlatformAdapter):
    name = "douyin"
    home_url = "https://www.douyin.com/"
    cookie_domain = ".douyin.com"
    # ttwid/msToken are anonymous; sessionid/sid_* appear after real login.
    login_cookie_names = ("sessionid", "sid_tt", "sid_guard")
    login_gate_keywords = (
        "登录后查看更多作品",
        "请登录后查看",
        "验证码",
        "扫码登录",
        "二维码登录",
        "立即登录",
        "拖动滑块",
        "请完成验证",
        "请通过验证",
    )
    gate_selectors = (
        ".captcha_verify_container",
        "iframe[src*='captcha']",
        "[class*='login-guide']",
        "[id*='captcha']",
    )

    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "douyin.com" in host or "iesdouyin.com" in host

    def is_profile_response(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in ("/aweme/v1/web/aweme/post", "/aweme/v1/web/user/profile"))

    def extract_items(self, payload: Any) -> list[dict]:
        return extract_aweme_list(payload)

    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        return extract_user_info(payload)

    def parse_item(self, raw: dict) -> dict[str, Any]:
        return DouyinAPI.parse_aweme(raw)


class TikTokAdapter(PlatformAdapter):
    """Collect public TikTok profiles through the same real-browser session.

    The web app loads profile posts from ``/api/post/item_list`` (and newer
    GraphQL endpoints); both return an ``itemList`` structure that this
    adapter normalises into the shared post schema.
    """

    name = "tiktok"
    home_url = "https://www.tiktok.com/"
    cookie_domain = ".tiktok.com"
    # sessionid/sessionid_ss only exist after a real interactive login.
    login_cookie_names = ("sessionid", "sessionid_ss")
    # Matched against lowercased page text; keep phrases long enough to avoid
    # the always-present "Log in" header button.
    login_gate_keywords = (
        "log in to see more",
        "login to see more",
        "please log in",
        "you need to log in",
        "log in to search",
        "scan the qr code",
        "your login has expired",
        # Slider/puzzle captcha wording served before any profile content.
        "drag the puzzle piece",
        "puzzle piece into place",
        "slide to verify",
        "安全验证",
        "拖动滑块",
    )
    gate_selectors = (
        "[data-e2e='login-modal']",
        "[class*='LoginModal']",
        "#captcha_container",
        "[class*='captcha']",
        "iframe[src*='captcha']",
    )
    implemented = True

    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host == "tiktok.com" or host.endswith(".tiktok.com")

    def is_profile_response(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        # GraphQL responses are matched by shape in extract_items; the URL is
        # shared by every query, so only the dedicated REST endpoint is exact.
        return "/api/post/item_list" in path or path.rstrip("/").endswith("/graphql")

    def extract_items(self, payload: Any) -> list[dict]:
        for candidate in (
            payload,
            payload.get("data") if isinstance(payload, dict) else None,
        ):
            if isinstance(candidate, dict) and isinstance(candidate.get("itemList"), list):
                return [value for value in candidate["itemList"] if isinstance(value, dict)]
        return []

    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        for candidate in (
            payload,
            payload.get("data") if isinstance(payload, dict) else None,
        ):
            info = candidate.get("userInfo") if isinstance(candidate, dict) else None
            user = info.get("user") if isinstance(info, dict) else None
            if isinstance(user, dict):
                stats = info.get("stats") if isinstance(info, dict) and isinstance(info.get("stats"), dict) else {}
                avatar = user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb") or []
                urls = avatar.get("urlList", []) if isinstance(avatar, dict) else []
                return {
                    "nickname": user.get("nickname", ""),
                    "sec_uid": user.get("secUid", "") or user.get("uniqueId", ""),
                    "uid": str(user.get("id", "") or ""),
                    "follower_count": _as_int(stats.get("followerCount")),
                    "following_count": _as_int(stats.get("followingCount")),
                    "aweme_count": _as_int(stats.get("videoCount")),
                    "favoriting_count": _as_int(stats.get("heartCount") if stats.get("heartCount") else stats.get("diggCount")),
                    "signature": user.get("signature", ""),
                    "avatar": urls[0] if urls else "",
                }
        return {}

    def item_id(self, raw: dict) -> str:
        return str(raw.get("id") or "")

    def dom_data_script_ids(self) -> tuple[str, ...]:
        # Pre-login first-page posts live in SSR state, not in API responses.
        return ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE")

    def parse_item(self, raw: dict) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ParseError("TikTok 作品不是有效对象")
        post_id = self.item_id(raw)
        author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        unique_id = str(author.get("uniqueId") or "")
        image_urls = self._image_urls(raw)
        video_url = "" if image_urls else self._video_url(raw)
        if not post_id or (not image_urls and not video_url):
            raise ParseError(f"TikTok 作品缺少媒体地址：{post_id or raw!r}")
        is_image = bool(image_urls)
        stats_raw = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
        kind = "photo" if is_image else "video"
        web_url = f"https://www.tiktok.com/@{unique_id}/{kind}/{post_id}" if unique_id else ""
        return {
            "aweme_id": post_id,
            "desc": raw.get("desc") or "无标题",
            "create_time": _as_int(raw.get("createTime")),
            "type": "image" if is_image else "video",
            "image_urls": image_urls,
            "video_url": video_url,
            "web_url": web_url,
            "referer": "https://www.tiktok.com/",
            "author": {
                "nickname": author.get("nickname", ""),
                "sec_uid": author.get("secUid", "") or unique_id,
                "uid": str(author.get("id", "") or ""),
            },
            "stats": {
                "digg_count": _as_int(stats_raw.get("diggCount")),
                "comment_count": _as_int(stats_raw.get("commentCount")),
                "share_count": _as_int(stats_raw.get("shareCount")),
                "collect_count": _as_int(stats_raw.get("collectCount")),
            },
        }

    @staticmethod
    def _image_urls(raw: dict) -> list[str]:
        containers = []
        image_post = raw.get("imagePost")
        if isinstance(image_post, dict):
            containers.append(image_post.get("images"))
        legacy = raw.get("image_post_info")
        if isinstance(legacy, dict):
            containers.append(legacy.get("images"))
        urls: list[str] = []
        for container in containers:
            for image in container or []:
                if not isinstance(image, dict):
                    continue
                address = image.get("imageURL") if isinstance(image.get("imageURL"), dict) else image.get("display_image")
                if isinstance(address, dict):
                    for url in address.get("urlList") or address.get("url_list") or []:
                        if url:
                            urls.append(str(url))
                            break
        return urls

    @staticmethod
    def _video_url(raw: dict) -> str:
        video = raw.get("video") if isinstance(raw.get("video"), dict) else {}
        for key in ("downloadAddr", "playAddr"):
            value = video.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        bitrate_info = video.get("bitrateInfo")
        if isinstance(bitrate_info, list):
            best = ""
            for entry in bitrate_info:
                play = entry.get("PlayAddr", {}) if isinstance(entry, dict) else {}
                url_list = play.get("UrlList") or []
                if url_list:
                    best = str(url_list[0])
                    if _as_int(entry.get("Bitrate")) <= 2_000_000:
                        return best
            return best
        return ""


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


ADAPTERS: tuple[PlatformAdapter, ...] = (DouyinAdapter(), TikTokAdapter())


def detect_platform(url: str) -> PlatformAdapter:
    """Pick the adapter matching ``url``; unknown links fall back to douyin."""
    for adapter in ADAPTERS:
        try:
            if adapter.matches(url):
                return adapter
        except Exception:
            continue
    return ADAPTERS[0]


def is_profile_response(url: str) -> bool:
    """Backward-compatible douyin-only helper kept for older importers."""
    return DouyinAdapter().is_profile_response(url)
