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

    def gate_probe_script(self) -> str:
        """Build the in-page JS probe used by the reader's login-gate check."""
        keywords = json.dumps(list(self.login_gate_keywords), ensure_ascii=False)
        selectors = ", ".join(self.gate_selectors)
        probe = f"const ks={keywords}; const t=document.body?(document.body.innerText||''):''; if (ks.some(x=>t.includes(x))) return true;"
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
    """Scaffold for future TikTok support; collection is not implemented yet."""

    name = "tiktok"
    home_url = "https://www.tiktok.com/"
    cookie_domain = ".tiktok.com"
    login_cookie_names = ("sessionid",)
    login_gate_keywords = ("log in to", "login to continue", "scan the qr code")
    gate_selectors = ("[data-e2e='login-container']",)
    implemented = False

    def matches(self, url: str) -> bool:
        return "tiktok.com" in urlparse(url).netloc.lower()

    def _unsupported(self) -> None:
        raise NotImplementedError("TikTok 支持将在下一步实现；当前仅支持抖音链接")

    def is_profile_response(self, url: str) -> bool:
        self._unsupported()
        return False

    def extract_items(self, payload: Any) -> list[dict]:
        self._unsupported()
        return []

    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        self._unsupported()
        return {}

    def parse_item(self, raw: dict) -> dict[str, Any]:
        self._unsupported()
        return {}

    def gate_probe_script(self) -> str:
        self._unsupported()
        return ""


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
