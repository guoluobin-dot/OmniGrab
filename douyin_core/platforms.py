"""Per-platform adapters for response matching, parsing and login detection.

Each adapter describes how one site's network responses are recognised and
parsed, and which signals indicate a login gate or a completed login. The
browser reader stays platform-agnostic and delegates every site-specific
decision here.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from .logger import get_logger

logger = get_logger(__name__)

from .douyin_api import DouyinAPI
from .risks import ParseError


# 明确是图片的扩展名/后缀。TikTok 封面图长这样：
# ``.../xxx~tplv-tiktokx-origin.image?dr=...``、``originCover``、``.jpeg`` 等。
_IMAGE_SUFFIXES = (
    ".image", ".jpeg", ".jpg", ".png", ".webp", ".avif", ".gif", ".heic", ".tiff",
)


def is_video_media_url(url: str) -> bool:
    """True when ``url`` plausibly points at a video stream, not a cover image.

    TikTok serves covers from the same CDN host as the video, so host matching
    alone would happily return a JPEG. Requiring a video container (or a
    known video path segment) is what keeps a saved ``.mp4`` an actual video.
    """
    if not isinstance(url, str) or not url.startswith("http"):
        return False
    lowered = url.lower()
    # Strip query/fragment before inspecting the path.
    path = lowered.split("?", 1)[0].split("#", 1)[0]
    if any(path.endswith(suffix) for suffix in _IMAGE_SUFFIXES):
        return False
    if "/video/" in path or "/tos/" in path and "/video/" in path:
        return True
    if any(path.endswith(ext) for ext in (".mp4", ".m3u8", ".mpd", ".mov", ".webm")):
        return True
    return False


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


class TikTokLoginRequired(ParseError):
    """TikTok withheld the play address because the session is not logged in."""


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
        # TikTok Shop 页面（shop.tiktok.com、/shop/pdp/…）由专用适配器处理
        if self.is_shop_url(url): return False
        host = urlparse(url).netloc.lower()
        return host == "tiktok.com" or host.endswith(".tiktok.com")

    def is_profile_response(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        # GraphQL responses are matched by shape in extract_items; the URL is
        # shared by every query, so only the dedicated REST endpoint is exact.
        # Single video/photo also returns item via API / SIGI_STATE
        if "/api/post/item_list" in path or path.rstrip("/").endswith("/graphql"):
            return True
        # TikTok single video/photo detail APIs
        if "/api/item/detail" in path or "/api/video/detail" in path or "/api/photo/detail" in path:
            return True
        if "/api/post/detail" in path:
            return True
        # Fallback: any TikTok api with item/video id
        if "tiktok.com/api/" in url.lower() and ("itemId" in url or "item_id" in url.lower()):
            return True
        return False

    def is_single_item_url(self, url: str) -> bool:
        """Return True when url is a single TikTok video/photo, not a profile."""
        path = urlparse(url).path.lower()
        return "/video/" in path or "/photo/" in path

    def is_shop_url(self, url: str) -> bool:
        """Return True for TikTok Shop pages (product detail or store)."""
        return TikTokShopAdapter.matches_shop_url(url)

    def dom_media_script(self) -> str:
        """In-page JS that surfaces media URLs rendered straight into the DOM."""
        return ""

    def extract_items(self, payload: Any) -> list[dict]:
        # 1) Standard profile list
        for candidate in (
            payload,
            payload.get("data") if isinstance(payload, dict) else None,
        ):
            if isinstance(candidate, dict) and isinstance(candidate.get("itemList"), list):
                return [value for value in candidate["itemList"] if isinstance(value, dict)]
        # 2) Single item via SIGI_STATE -> ItemModule
        for candidate in (
            payload,
            payload.get("data") if isinstance(payload, dict) else None,
        ):
            if isinstance(candidate, dict) and isinstance(candidate.get("ItemModule"), dict):
                items = [v for v in candidate["ItemModule"].values() if isinstance(v, dict) and v.get("id")]
                if items:
                    return items
        # 3) Single item via __UNIVERSAL_DATA__ -> webapp.video-detail
        single = self._extract_single_from_universal(payload)
        if single:
            return [single]
        # 4) Direct itemStruct / videoData
        for candidate in (
            payload,
            payload.get("data") if isinstance(payload, dict) else None,
            payload.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail") if isinstance(payload, dict) else None,
        ):
            if isinstance(candidate, dict):
                for key in ("itemStruct", "itemInfo"):
                    val = candidate.get(key)
                    if isinstance(val, dict) and val.get("id"):
                        return [val]
                    if isinstance(val, dict) and isinstance(val.get("itemStruct"), dict) and val["itemStruct"].get("id"):
                        return [val["itemStruct"]]
                if isinstance(candidate.get("videoData"), dict) and candidate["videoData"].get("id"):
                    return [candidate["videoData"]]
        return []

    @staticmethod
    def _extract_single_from_universal(payload: Any) -> dict | None:
        try:
            if not isinstance(payload, dict):
                return None
            scope = payload.get("__DEFAULT_SCOPE__") if isinstance(payload.get("__DEFAULT_SCOPE__"), dict) else None
            if scope is None and isinstance(payload.get("data"), dict):
                scope = payload["data"].get("__DEFAULT_SCOPE__")
            if not isinstance(scope, dict):
                return None
            detail = scope.get("webapp.video-detail")
            if not isinstance(detail, dict):
                # Photo detail uses similar key
                detail = scope.get("webapp.photo-detail") or scope.get("webapp.video-detail")
            if not isinstance(detail, dict):
                return None
            # New shape: {statusCode, videoId: {itemInfo: {itemStruct}}}
            for k in ("videoId", "photoId", "detail"):
                sub = detail.get(k) if isinstance(detail.get(k), dict) else None
                if isinstance(sub, dict):
                    info = sub.get("itemInfo") if isinstance(sub.get("itemInfo"), dict) else sub
                    struct = info.get("itemStruct") if isinstance(info.get("itemStruct"), dict) else info
                    if isinstance(struct, dict) and struct.get("id"):
                        return struct
            # Direct itemInfo at top
            info = detail.get("itemInfo")
            if isinstance(info, dict):
                struct = info.get("itemStruct") if isinstance(info.get("itemStruct"), dict) else info
                if isinstance(struct, dict) and struct.get("id"):
                    return struct
            # VideoData
            if isinstance(detail.get("videoData"), dict) and detail["videoData"].get("id"):
                return detail["videoData"]
        except Exception:
            return None
        return None

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
            # playAddr/downloadAddr 为空是“未登录时被地区限制”的典型表现：
            # 此时页面上只有封面图，绝不能拿它当视频下载。
            gated = isinstance(raw.get("video"), dict) and not any(
                raw["video"].get(key) for key in ("downloadAddr", "playAddr", "bitrateInfo")
            )
            detail = "平台未返回播放地址（通常需要先在浏览器登录 TikTok）" if gated else "缺少媒体地址"
            raise (TikTokLoginRequired if gated else ParseError)(
                f"TikTok 作品{detail}：{post_id or list(raw)[:6]}"
            )
        is_image = bool(image_urls)
        stats_raw = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
        kind = "photo" if is_image else "video"
        created = _as_int(raw.get("createTime"))
        web_url = f"https://www.tiktok.com/@{unique_id}/{kind}/{post_id}" if unique_id else ""
        # TikTok CDN validates Referer as the specific video page, not generic homepage
        referer = web_url or "https://www.tiktok.com/"
        return {
            "aweme_id": post_id,
            "desc": raw.get("desc") or "无标题",
            "create_time": created,
            "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)) if created else "未知",
            "type": "image" if is_image else "video",
            "type_str": "图文" if is_image else "视频",
            "image_urls": image_urls,
            "video_url": video_url,
            "web_url": web_url,
            "referer": referer,
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
        # 带货（Shop）视频有时把播放地址放在作品顶层而不是 video 下
        if not video:
            video = raw
        # Direct keys (new TikTok sometimes uses camelCase variants)
        for key in ("downloadAddr", "playAddr", "downloadAddrH264", "playAddrH264", "playAddrByteVC1"):
            value = video.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        # Some payloads store URL inside dict like {"UrlList": [...]}
        for key in ("downloadAddr", "playAddr"):
            val = video.get(key)
            if isinstance(val, dict):
                for url in val.get("UrlList") or val.get("urlList") or []:
                    if isinstance(url, str) and url.startswith("http"):
                        return str(url)
        bitrate_info = video.get("bitrateInfo")
        if isinstance(bitrate_info, list):
            best = ""
            for entry in bitrate_info:
                play = entry.get("PlayAddr", {}) if isinstance(entry, dict) else {}
                url_list = play.get("UrlList") or play.get("urlList") or []
                if url_list:
                    best = str(url_list[0])
                    if _as_int(entry.get("Bitrate")) <= 2_000_000:
                        return best
            if best:
                return best
        # Last resort: deep search the payload for a real video URL (shop/new schema).
        # NOTE: 封面图（*.image / .jpeg）也托管在 tiktokcdn 上，误当作视频会导致
        # 下载到 JPEG 却存成 .mp4，因此这里必须显式排除图片。
        def _deep_find(obj: Any, depth: int = 0) -> str:
            if depth > 4:
                return ""
            if isinstance(obj, str) and is_video_media_url(obj):
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    found = _deep_find(v, depth + 1)
                    if found:
                        return found
            elif isinstance(obj, list):
                for v in obj[:10]:
                    found = _deep_find(v, depth + 1)
                    if found:
                        return found
            return ""
        deep = _deep_find(video)
        if deep:
            return deep
        # Log keys for debugging when still empty and dump raw for that id
        if video:
            logger.debug("TikTok video dict keys=%s id=%s", list(video.keys())[:20], raw.get("id"))
            try:
                import os as _os
                dbg_dir = _os.path.join("logs")
                _os.makedirs(dbg_dir, exist_ok=True)
                dbg_path = _os.path.join(dbg_dir, f"tiktok_debug_{raw.get('id','unknown')}.json")
                if not _os.path.exists(dbg_path):
                    import json as _json
                    with open(dbg_path, "w", encoding="utf-8") as _f:
                        _json.dump(raw, _f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        return ""


class BilibiliAdapter(PlatformAdapter):
    name = "bilibili"
    home_url = "https://www.bilibili.com/"
    cookie_domain = ".bilibili.com"
    login_cookie_names = ("SESSDATA", "bili_jct", "DedeUserID")
    login_gate_keywords = (
        "登录后查看",
        "请先登录",
        "扫码登录",
        "二维码登录",
        "登录哔哩哔哩",
        "验证中心",
        "滑动验证",
        "点击验证",
    )
    gate_selectors = (
        ".bili-mini-login",
        "[class*='login-panel']",
        "iframe[src*='passport']",
        "[class*='geetest']",
        ".bili-header__avatar--unlogin",
    )
    implemented = True

    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "bilibili.com" in host or "b23.tv" in host

    def is_profile_response(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in (
            "/x/space/wbi/arc/search",
            "/x/space/arc/search",
            "/dynamic_svr/v1/dynamic_svr/space_history",
            "/x/polymer/web-dynamic/v1/feed/space",
            "/x/web-interface/view",
            "/x/player/wbi/playurl",
        ))

    def is_single_item_url(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return "/video/" in path or "/read/" in path or "b23.tv" in urlparse(url).netloc.lower()

    def extract_items(self, payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        items = []
        if "list" in data and isinstance(data["list"], dict):
            vlist = data["list"].get("vlist")
            if isinstance(vlist, list):
                items.extend(vlist)
        if "archives" in data and isinstance(data["archives"], list):
            items.extend(data["archives"])
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                if isinstance(item, dict):
                    dynamic_id = item.get("id_str", "")
                    desc = item.get("desc", "")
                    # New polymer dynamic API format
                    if "modules" in item and isinstance(item["modules"], dict):
                        module_dynamic = item["modules"].get("module_dynamic")
                        if isinstance(module_dynamic, dict):
                            major = module_dynamic.get("major")
                            if isinstance(major, dict):
                                items.append({
                                    "dynamic_item": dict(major, id_str=dynamic_id),
                                    "desc": desc,
                                    "dynamic_id": dynamic_id
                                })
                    # Legacy dynamic API format (space_history)
                    elif "card" in item and isinstance(item["card"], dict):
                        try:
                            card = json.loads(item["card"])
                            if "item" in card and isinstance(card["item"], dict):
                                card_item = card["item"]
                                if "modules" in card_item:
                                    major = card_item["modules"].get("module_dynamic", {}).get("major", {})
                                    if major:
                                        items.append({
                                            "dynamic_item": dict(major, id_str=dynamic_id),
                                            "desc": card_item.get("desc", ""),
                                            "dynamic_id": dynamic_id
                                        })
                        except (json.JSONDecodeError, TypeError):
                            pass
        return [item for item in items if isinstance(item, dict)]

    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if not isinstance(data, dict):
            return {}
        if "list" in data and isinstance(data["list"], dict):
            upper = data["list"].get("upper")
            if isinstance(upper, dict):
                face = upper.get("face", "")
                return {
                    "nickname": upper.get("name", "未知"),
                    "sec_uid": str(upper.get("mid", "")),
                    "uid": str(upper.get("mid", "")),
                    "follower_count": 0,
                    "following_count": 0,
                    "aweme_count": data["list"].get("page", {}).get("count", 0),
                    "favoriting_count": 0,
                    "signature": upper.get("sign", ""),
                    "avatar": face,
                }
        if "user_info" in data and isinstance(data["user_info"], dict):
            user = data["user_info"]
            face = user.get("face", "")
            return {
                "nickname": user.get("name", user.get("uname", "未知")),
                "sec_uid": str(user.get("mid", "")),
                "uid": str(user.get("mid", "")),
                "follower_count": user.get("follower", 0),
                "following_count": user.get("following", 0),
                "aweme_count": user.get("archive_count", 0),
                "favoriting_count": 0,
                "signature": user.get("sign", ""),
                "avatar": face,
            }
        return {}

    def item_id(self, raw: dict) -> str:
        if "bvid" in raw:
            return str(raw["bvid"])
        if "aid" in raw:
            return f"av{raw['aid']}"
        if "dynamic_item" in raw:
            dyn = raw["dynamic_item"]
            if isinstance(dyn, dict):
                return str(dyn.get("id_str") or dyn.get("rid_str") or dyn.get("dynamic_id") or "")
        # dynamic_id may be at top level (from extract_items)
        return str(raw.get("dynamic_id") or raw.get("id_str") or raw.get("rid_str") or "")

    def dom_data_script_ids(self) -> tuple[str, ...]:
        # B站主页视频列表通过 API 获取，不依赖 SSR 数据
        return ()

    def parse_item(self, raw: dict) -> dict[str, Any]:
        if "dynamic_item" in raw:
            return self._parse_dynamic(raw)
        if "bvid" in raw or "aid" in raw:
            return self._parse_video(raw)
        raise ParseError("B站作品数据格式不识别")

    def _parse_video(self, raw: dict) -> dict[str, Any]:
        bvid = raw.get("bvid") or ""
        aid = raw.get("aid") or 0
        if not bvid and aid:
            bvid = f"av{aid}"
        title = raw.get("title") or raw.get("desc") or "无标题"
        pubdate = int(raw.get("pubdate") or raw.get("ctime") or 0)
        owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
        stat = raw.get("stat") if isinstance(raw.get("stat"), dict) else {}
        videos = int(raw.get("videos") or 1)
        cid = raw.get("cid") or (raw.get("pages", [{}])[0].get("cid") if raw.get("pages") else 0)
        pic = raw.get("pic") or ""
        duration = int(raw.get("duration") or 0)
        web_url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        video_url = ""
        
        # If no cid, try to fetch from video view API (vlist doesn't include cid)
        if not cid and bvid and not bvid.startswith("av"):
            cid = self._fetch_cid_from_view_api(bvid)
        elif not cid and aid:
            cid = self._fetch_cid_from_view_api(f"av{aid}")
        
        if cid:
            video_url = f"https://api.bilibili.com/x/player/wbi/playurl?bvid={bvid}&cid={cid}&qn=116"
        return {
            "aweme_id": self.item_id(raw),
            "desc": title,
            "create_time": pubdate,
            "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pubdate)) if pubdate else "未知",
            "type": "video",
            "type_str": "视频",
            "duration": duration,
            "cover": pic,
            "video_url": video_url,
            "image_urls": [],
            "music_url": "",
            "web_url": web_url,
            "referer": web_url or "https://www.bilibili.com/",
            "author": {
                "nickname": owner.get("name", ""),
                "sec_uid": str(owner.get("mid", "")),
                "uid": str(owner.get("mid", "")),
            },
            "stats": {
                "digg_count": int(stat.get("like", 0)),
                "comment_count": int(stat.get("reply", 0)),
                "share_count": int(stat.get("share", 0)),
                "collect_count": int(stat.get("favorite", 0)),
                "play_count": int(stat.get("view", 0)),
            },
        }

    def _fetch_cid_from_view_api(self, bvid: str) -> int:
        """从视频详情 API 获取首个分 P 的 cid"""
        try:
            import requests
            from .downloader import _add_wbi_signature
            api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            signed_url = _add_wbi_signature(api_url)
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://www.bilibili.com/"}
            resp = requests.get(signed_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    pages = data.get("data", {}).get("pages", [])
                    if pages:
                        return int(pages[0].get("cid", 0))
        except Exception:
            pass
        return 0

    def _parse_dynamic(self, raw: dict) -> dict[str, Any]:
        dyn = raw.get("dynamic_item") if isinstance(raw.get("dynamic_item"), dict) else {}
        dynamic_id = raw.get("dynamic_id") or dyn.get("id_str") or dyn.get("rid_str") or ""
        desc = raw.get("desc") or dyn.get("desc", {}).get("text", "") if isinstance(dyn.get("desc"), dict) else ""
        timestamp = int(dyn.get("timestamp") or dyn.get("ctime") or raw.get("timestamp") or 0)
        modules = dyn.get("modules", {}) if isinstance(dyn.get("modules"), dict) else {}
        module_author = modules.get("module_author", {}) if isinstance(modules.get("module_author"), dict) else {}
        author = module_author.get("author", {}) if isinstance(module_author.get("author"), dict) else {}
        module_dynamic = modules.get("module_dynamic", {}) if isinstance(modules.get("module_dynamic"), dict) else {}
        major = module_dynamic.get("major", {}) if isinstance(module_dynamic.get("major"), dict) else {}
        stat = module_dynamic.get("stat", {}) if isinstance(module_dynamic.get("stat"), dict) else {}
        image_urls = []
        video_url = ""
        is_video = False
        if "opus" in major and isinstance(major["opus"], dict):
            opus = major["opus"]
            pics = opus.get("pics", [])
            for pic in pics:
                if isinstance(pic, dict):
                    url = pic.get("url") or pic.get("src") or ""
                    if url:
                        image_urls.append(url)
        elif "draw" in major and isinstance(major["draw"], dict):
            draw = major["draw"]
            items = draw.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    url = item.get("src") or item.get("url") or ""
                    if url:
                        image_urls.append(url)
        elif "archive" in major and isinstance(major["archive"], dict):
            is_video = True
            archive = major["archive"]
            bvid = archive.get("bvid") or ""
            cid = archive.get("cid") or 0
            if bvid and cid:
                video_url = f"https://api.bilibili.com/x/player/wbi/playurl?bvid={bvid}&cid={cid}&qn=116"
            elif not bvid and archive.get("aid"):
                bvid = f"av{archive['aid']}"
                video_url = f"https://api.bilibili.com/x/player/wbi/playurl?bvid={bvid}&cid={cid}&qn=116"
        elif "video" in major and isinstance(major["video"], dict):
            is_video = True
            video = major["video"]
            bvid = video.get("bvid") or ""
            cid = video.get("cid") or 0
            if bvid and cid:
                video_url = f"https://api.bilibili.com/x/player/wbi/playurl?bvid={bvid}&cid={cid}&qn=116"
        if not image_urls and not video_url:
            raise ParseError(f"动态缺少媒体地址：{dynamic_id}")
        web_url = f"https://t.bilibili.com/{dynamic_id}" if dynamic_id else ""
        # 对于动态视频，如果有 archive/video 信息，优先使用视频页面作为 referer
        video_page_url = ""
        if is_video:
            if "archive" in major and isinstance(major["archive"], dict):
                bvid = major["archive"].get("bvid") or ""
                if bvid:
                    video_page_url = f"https://www.bilibili.com/video/{bvid}"
            elif "video" in major and isinstance(major["video"], dict):
                bvid = major["video"].get("bvid") or ""
                if bvid:
                    video_page_url = f"https://www.bilibili.com/video/{bvid}"
        referer = video_page_url or web_url or "https://www.bilibili.com/"
        return {
            "aweme_id": dynamic_id,
            "desc": desc or "无标题",
            "create_time": timestamp,
            "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) if timestamp else "未知",
            "type": "video" if is_video else "image",
            "type_str": "视频" if is_video else "图文",
            "duration": 0,
            "cover": image_urls[0] if image_urls else "",
            "video_url": video_url,
            "image_urls": image_urls,
            "music_url": "",
            "web_url": web_url,
            "referer": referer,
            "author": {
                "nickname": author.get("name", author.get("uname", "")),
                "sec_uid": str(author.get("mid", "")),
                "uid": str(author.get("mid", "")),
            },
            "stats": {
                "digg_count": int(stat.get("like", 0)),
                "comment_count": int(stat.get("reply", 0)),
                "share_count": int(stat.get("forward", 0)),
                "collect_count": 0,
                "play_count": 0,
            },
        }


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        pass
    # 小红书点赞数偶尔是 "12.3万" 这类字符串
    try:
        text = str(value).strip().replace(",", "")
        if text.endswith("万"):
            return int(float(text[:-1]) * 10000)
        if text.endswith("千"):
            return int(float(text[:-1]) * 1000)
        return int(float(text))
    except (TypeError, ValueError):
        return 0


class XiaohongshuAdapter(PlatformAdapter):
    """小红书（RED）图文/视频采集：原理与抖音一致。

    浏览器访问博主主页 ``https://www.xiaohongshu.com/user/profile/{id}``
    时，前端通过 ``/api/sns/web/v1(user_post|feed)`` 拉取笔记列表；
    本适配器从这些网络响应中提取原始笔记并归一化为通用帖子结构，
    复用通用下载器按 type 分流（image 走图片集，video 走视频）。
    """

    name = "xiaohongshu"
    home_url = "https://www.xiaohongshu.com/"
    cookie_domain = ".xiaohongshu.com"
    # web_session 仅在真实登录后出现；a1/webId 匿名也有。
    login_cookie_names = ("web_session",)
    login_gate_keywords = (
        "登录后查看",
        "请先登录",
        "扫码登录",
        "立即登录",
        "验证码",
        "滑块验证",
        "拖动滑块",
        "login",
    )
    gate_selectors = (
        "[class*='login-modal']",
        "[class*='login-container']",
        "iframe[src*='captcha']",
        "[class*='captcha']",
    )
    implemented = True

    def matches(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "xiaohongshu.com" in host or "xhslink.com" in host

    def is_profile_response(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in (
            "/api/sns/web/v1/user_post",
            "/api/sns/web/v2/user_post",
            "/api/sns/web/v1/feed",
            "/api/sns/web/v1/user/userinfo",
            "/api/sns/web/v2/user",
        ))

    def extract_items(self, payload: Any) -> list[dict]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, list):
            # 单笔记 feed：data 为数组，元素可能包着 note_card/note_detail
            return [note for item in data if isinstance(item, dict) for note in (self._unwrap_card(item),) if note]
        if not isinstance(data, dict):
            return []
        items: list[dict] = []
        notes = data.get("notes")
        if isinstance(notes, list):
            items.extend(value for value in notes if isinstance(value, dict))
        note_list = data.get("note_list") or data.get("noteList")
        if isinstance(note_list, list):
            for value in note_list:
                if isinstance(value, dict):
                    note = self._unwrap_card(value)
                    if note:
                        items.append(note)
        return items

    @staticmethod
    def _unwrap_card(item: dict) -> dict | None:
        for key in ("note_card", "noteCard", "note_detail", "noteDetail", "note"):
            card = item.get(key)
            if isinstance(card, dict) and (card.get("note_id") or card.get("noteId")):
                merged = dict(item)
                merged.update(card)
                return merged
        if item.get("note_id") or item.get("noteId"):
            return item
        return None

    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if isinstance(data, list):
            notes = [self._unwrap_card(item) for item in data if isinstance(item, dict)]
            notes = [note for note in notes if note]
            first = notes[0] if notes else None
            return self._user_from_note(first) if first else {}
        if not isinstance(data, dict):
            return {}
        for key in ("user", "basic_info", "basicInfo", "user_info", "userInfo"):
            user = data.get(key)
            if isinstance(user, dict) and (user.get("user_id") or user.get("userId") or user.get("nickname")):
                return self._normalise_user(user, data)
        notes = data.get("notes")
        if isinstance(notes, list):
            for note in notes:
                if isinstance(note, dict):
                    info = self._user_from_note(note)
                    if info.get("uid") or info.get("nickname") != "未知":
                        return info
        return {}

    @staticmethod
    def _user_from_note(note: dict | None) -> dict[str, Any]:
        if not isinstance(note, dict):
            return {}
        user = note.get("user")
        if not isinstance(user, dict):
            return {}
        return XiaohongshuAdapter._normalise_user(user, {})

    @staticmethod
    def _normalise_user(user: dict, data: dict) -> dict[str, Any]:
        avatar = user.get("avatar") or user.get("images") or user.get("avatar_url") or ""
        if not isinstance(avatar, str):
            avatar = ""
        return {
            "nickname": user.get("nickname") or user.get("nick_name") or user.get("name") or "未知",
            "sec_uid": str(user.get("user_id") or user.get("userId") or user.get("id") or ""),
            "uid": str(user.get("user_id") or user.get("userId") or user.get("id") or ""),
            "follower_count": _as_int(user.get("fans") or user.get("follower_count") or data.get("fans")),
            "following_count": _as_int(user.get("follows") or user.get("following_count") or data.get("follows")),
            "aweme_count": _as_int(user.get("note_count") or data.get("note_count")),
            "favoriting_count": _as_int(user.get("liked_count") or data.get("liked_count")),
            "signature": user.get("desc") or user.get("signature") or "",
            "avatar": avatar,
        }

    def item_id(self, raw: dict) -> str:
        return str(raw.get("note_id") or raw.get("noteId") or "")

    def parse_item(self, raw: dict) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ParseError("小红书笔记不是有效对象")
        note_id = self.item_id(raw)
        if not note_id:
            raise ParseError("小红书笔记缺少 note_id")
        image_urls = self._image_urls(raw)
        video_url = self._video_url(raw)
        if not image_urls and not video_url:
            raise ParseError(f"小红书笔记缺少媒体地址：{note_id}")
        is_video = bool(video_url) or raw.get("type") == "video"
        if is_video and not video_url:
            raise ParseError(f"小红书视频缺少播放地址：{note_id}")
        title = raw.get("display_title") or raw.get("displayTitle") or raw.get("title") or ""
        desc = raw.get("desc") or ""
        text = title or (desc[:60] if isinstance(desc, str) else "") or "无标题"
        created_ms = raw.get("time") or raw.get("create_time") or 0
        try:
            created = int(int(created_ms) // 1000) if int(created_ms) > 10_000_000_000 else int(created_ms or 0)
        except (TypeError, ValueError):
            created = 0
        cover = self._cover(raw)
        web_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        stats = raw.get("interact_info") or raw.get("interactInfo") if isinstance(raw.get("interact_info") or raw.get("interactInfo"), dict) else {}
        if not isinstance(stats, dict):
            stats = {}
        return {
            "aweme_id": note_id,
            "desc": text,
            "create_time": created,
            "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)) if created else "未知",
            "type": "video" if is_video else "image",
            "type_str": "视频" if is_video else "图文",
            "duration": _as_int((raw.get("video_info_v2") or raw.get("video") or {}).get("duration") if isinstance(raw.get("video_info_v2") or raw.get("video"), dict) else 0),
            "cover": cover,
            "video_url": video_url,
            "image_urls": image_urls,
            "music_url": "",
            "web_url": web_url,
            "referer": web_url,
            "author": {
                "nickname": user.get("nickname") or user.get("nick_name") or "",
                "sec_uid": str(user.get("user_id") or user.get("userId") or ""),
                "uid": str(user.get("user_id") or user.get("userId") or ""),
            },
            "stats": {
                "digg_count": _as_int(stats.get("liked_count") or stats.get("likedCount")),
                "comment_count": _as_int(stats.get("comment_count") or stats.get("commentCount")),
                "share_count": _as_int(stats.get("share_count") or stats.get("shareCount")),
                "collect_count": _as_int(stats.get("collected_count") or stats.get("collectedCount")),
            },
        }

    @staticmethod
    def _video_url(raw: dict) -> str:
        container = raw.get("video_info_v2") or raw.get("videoInfoV2") or raw.get("video")
        if not isinstance(container, dict):
            return ""
        media = container.get("media")
        if not isinstance(media, dict):
            # 兼容部分响应直接把 stream 放 video 下
            media = container if isinstance(container.get("stream"), dict) else {}
        stream = media.get("stream")
        if not isinstance(stream, dict):
            return ""
        for codec in ("h264", "h265", "h266", "av1"):
            tracks = stream.get(codec)
            if isinstance(tracks, list):
                for track in tracks:
                    if isinstance(track, dict):
                        url = track.get("master_url") or track.get("masterUrl") or ""
                        if isinstance(url, str) and url.startswith("http"):
                            return url
                # 有该编码但无 master_url 时尝试 backup_urls
                for track in tracks:
                    if isinstance(track, dict):
                        backups = track.get("backup_urls") or track.get("backupUrls") or []
                        for url in backups if isinstance(backups, list) else []:
                            if isinstance(url, str) and url.startswith("http"):
                                return url
        return ""

    @staticmethod
    def _image_urls(raw: dict) -> list[str]:
        images = raw.get("image_list") or raw.get("imageList") or []
        urls: list[str] = []
        if isinstance(images, list):
            for image in images:
                url = XiaohongshuAdapter._pick_image_url(image)
                if url:
                    urls.append(url)
        return urls

    @staticmethod
    def _pick_image_url(image: Any) -> str:
        if isinstance(image, str):
            return image if image.startswith("http") else ""
        if not isinstance(image, dict):
            return ""
        default = image.get("url_default") or image.get("urlDefault") or image.get("url") or ""
        if isinstance(default, str) and default.startswith("http"):
            return default
        best, best_width = "", -1
        for info in image.get("info_list") or image.get("infoList") or []:
            if not isinstance(info, dict):
                continue
            url = info.get("url") or ""
            if not (isinstance(url, str) and url.startswith("http")):
                continue
            try:
                width = int(info.get("width") or 0)
            except (TypeError, ValueError):
                width = 0
            if width >= best_width:
                best, best_width = url, width
        return best

    @classmethod
    def _cover(cls, raw: dict) -> str:
        cover = raw.get("cover")
        url = cls._pick_image_url(cover)
        if url:
            return url
        images = cls._image_urls(raw)
        return images[0] if images else ""


class TikTokShopAdapter(PlatformAdapter):
    """TikTok Shop 商品详情页 / 店铺页：抓取挂在商品与店铺上的带货视频。

    Shop 页面是纯客户端渲染（商品数据来自 ``oec.tiktok.com`` 等内部接口），
    直连请求会被 WAF 拦截，因此和抖音/TikTok 一样依赖真实浏览器会话里
    捕获到的网络响应。视频条目有两种常见形态：

    - 商家自己上传的商品视频：``{"video_id": ..., "play_url": ..., "cover_url": ...}``
    - 关联/带货视频：标准 TikTok ``itemStruct``（与博主主页视频同构）
    """

    name = "tiktok_shop"
    home_url = "https://shop.tiktok.com/"
    cookie_domain = ".tiktok.com"
    login_cookie_names = ("sessionid", "sessionid_ss")
    login_gate_keywords = TikTokAdapter.login_gate_keywords + (
        "sign in to continue",
        "log in to view",
        "please sign in",
        "sign in to shop",
    )
    gate_selectors = TikTokAdapter.gate_selectors
    implemented = True

    # 商品页路径（TikTok 会同时使用 shop.tiktok.com 与 www.tiktok.com）
    PRODUCT_PATH_MARKERS = ("/view/product/", "/pdp/", "/shop/pdp/", "/shop/product/", "/product/")
    # 店铺页路径：视频更分散，按主页方式滚动抓取
    STORE_PATH_MARKERS = ("/store/", "/shop/store/", "/seller/")
    # 承载商品/视频数据的接口
    API_HOSTS = ("oec.tiktok.com", "ec.tiktok.com", "shop.tiktok.com")
    API_PATH_MARKERS = (
        "/api/ec/product",
        "/api/shop/product",
        "/product/detail",
        "/product/related",
        "/related/video",
        "/api/product/detail",
        "/api/shop/",
    )

    @classmethod
    def matches_shop_url(cls, url: str) -> bool:
        parsed = urlparse(str(url or ""))
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if host == "shop.tiktok.com" or host.endswith(".shop.tiktok.com"):
            return True
        if host == "tiktok.com" or host.endswith(".tiktok.com"):
            return any(marker in path for marker in cls.PRODUCT_PATH_MARKERS + cls.STORE_PATH_MARKERS)
        return False

    @classmethod
    def is_product_url(cls, url: str) -> bool:
        path = urlparse(str(url or "")).path.lower()
        return any(marker in path for marker in cls.PRODUCT_PATH_MARKERS)

    def matches(self, url: str) -> bool:
        return self.matches_shop_url(url)

    def is_single_item_url(self, url: str) -> bool:
        """商品详情页只有一组视频，无需无限滚动；店铺页按主页处理。"""
        return self.is_product_url(url)

    def is_profile_response(self, url: str) -> bool:
        """Only capture API calls that can carry product/video payloads.

        Matching every ``shop.tiktok.com`` request would also pull in
        telemetry and static assets, so the path must look like a data API.
        """
        parsed = urlparse(url)
        host, path = parsed.netloc.lower(), parsed.path.lower()
        if not any(host == api or host.endswith(f".{api}") for api in self.API_HOSTS):
            if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
                return False
        if any(marker in path for marker in self.API_PATH_MARKERS):
            return True
        return "/api/" in path and any(
            keyword in path for keyword in ("product", "pdp", "shop", "video", "seller", "store")
        )

    def dom_data_script_ids(self) -> tuple[str, ...]:
        return ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE", "__MODERN_SSR_DATA__")

    def item_id(self, raw: dict) -> str:
        for key in ("video_id", "aweme_id", "item_id", "product_id", "id"):
            value = raw.get(key)
            if value:
                return str(value)
        # DOM 里抓到的播放地址没有 id：用地址摘要保证去重与命名稳定
        url = str(raw.get("url") or raw.get("video_url") or raw.get("play_url") or "")
        return "shop_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] if url else ""

    def extract_items(self, payload: Any) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()
        for container in self._iter_dicts(payload, depth=8):
            for key in (
                "relatedVideos", "related_videos", "videos", "videoList", "video_list",
                "promoVideos", "promo_videos", "items", "itemList", "awemeList",
            ):
                for entry in container.get(key) or []:
                    if isinstance(entry, dict) and self._is_video_entry(entry):
                        identifier = self.item_id(entry)
                        if identifier and identifier not in seen:
                            seen.add(identifier)
                            items.append(entry)
        # 关联带货视频常常直接就是标准 itemStruct，复用 TikTok 解析
        for entry in TikTokAdapter().extract_items(payload):
            identifier = self.item_id(entry)
            if identifier and identifier not in seen:
                seen.add(identifier)
                items.append(entry)
        return items

    def extract_user_info(self, payload: Any) -> dict[str, Any]:
        for container in self._iter_dicts(payload, depth=8):
            info = container.get("productInfo") or container.get("product_detail")
            if isinstance(info, dict):
                product = info.get("product") if isinstance(info.get("product"), dict) else info
                title = product.get("title") or product.get("product_name") or ""
                if title:
                    shop = info.get("shop") if isinstance(info.get("shop"), dict) else {}
                    shop_name = shop.get("shop_name") or ""
                    return {
                        "nickname": f"{title}" + (f"（{shop_name}）" if shop_name else ""),
                        "sec_uid": str(product.get("product_id") or product.get("id") or ""),
                        "uid": str(product.get("product_id") or product.get("id") or ""),
                        "follower_count": 0,
                        "following_count": 0,
                        "aweme_count": 0,
                        "favoriting_count": _as_int(product.get("sold_count")),
                        "signature": str(product.get("description") or "")[:200],
                        "avatar": "",
                    }
        return TikTokAdapter().extract_user_info(payload)

    def parse_item(self, raw: dict) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ParseError("TikTok Shop 视频不是有效对象")
        # 标准 itemStruct（关联带货视频 / 店铺页视频）走 TikTok 解析
        if isinstance(raw.get("video"), dict) or raw.get("imagePost") or raw.get("image_post_info"):
            return TikTokAdapter().parse_item(raw)
        post_id = self.item_id(raw)
        video_url = self._video_url(raw)
        if not post_id or not video_url:
            raise ParseError(f"TikTok Shop 视频缺少媒体地址：{post_id or list(raw)[:6]}")
        created = _as_int(raw.get("create_time") or raw.get("createTime") or raw.get("update_time"))
        cover = ""
        for key in ("cover_url", "cover", "thumbnail_url", "thumbnail", "poster"):
            value = raw.get(key)
            if isinstance(value, str) and value.startswith("http"):
                cover = value
                break
            if isinstance(value, dict):
                urls = value.get("url_list") or value.get("urlList") or []
                if urls:
                    cover = str(urls[0])
                    break
        title = raw.get("title") or raw.get("desc") or raw.get("product_name") or "TikTok Shop 视频"
        web_url = self._page_url(raw)
        return {
            "aweme_id": post_id,
            "desc": str(title)[:120],
            "create_time": created,
            "create_time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)) if created else "未知",
            "type": "video",
            "type_str": "Shop视频",
            "duration": _as_int(raw.get("duration")),
            "cover": cover,
            "image_urls": [],
            "video_url": video_url,
            "music_url": "",
            "web_url": web_url,
            # Shop CDN 校验 Referer，必须是商品页而不是 TikTok 首页
            "referer": web_url or "https://shop.tiktok.com/",
            "author": {
                "nickname": raw.get("author_nickname") or raw.get("seller_name") or "",
                "sec_uid": str(raw.get("seller_id") or ""),
                "uid": str(raw.get("seller_id") or ""),
            },
            "stats": {
                "digg_count": _as_int(raw.get("digg_count") or raw.get("like_count")),
                "comment_count": _as_int(raw.get("comment_count")),
                "share_count": _as_int(raw.get("share_count")),
                "collect_count": 0,
            },
        }

    def dom_media_script(self) -> str:
        """Shop 视频常常只存在于 <video> 标签里，接口响应里不一定带地址。"""
        return """
        const out = [];
        const seen = {};
        const heading = document.querySelector('h1');
        const meta = document.querySelector('meta[property="og:title"]');
        const label = (meta && meta.content) || (heading && heading.innerText) || document.title || '';
        const add = (src, cover) => {
          if (!src || src.indexOf('blob:') === 0) return;
          if (src.indexOf('http') !== 0) return;
          if (seen[src]) return;
          seen[src] = 1;
          out.push({url: src, cover_url: cover || '', title: label || '', product_url: location.href});
        };
        for (const el of document.querySelectorAll('video')) {
          add(el.currentSrc || el.src || '', el.poster || '');
        }
        for (const el of document.querySelectorAll('video source[src], source[src*=".mp4"]')) {
          add(el.getAttribute('src') || '', '');
        }
        return out;
        """

    @staticmethod
    def _page_url(raw: dict) -> str:
        for key in ("web_url", "product_url", "page_url", "share_url"):
            value = raw.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        return ""

    @staticmethod
    def _is_video_entry(entry: dict) -> bool:
        if isinstance(entry.get("video"), dict) and TikTokShopAdapter._video_url(entry):
            return True
        for key in ("play_url", "video_url", "download_url", "playUrl", "videoUrl", "playAddr"):
            value = entry.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return True
            if isinstance(value, dict) and TikTokShopAdapter._video_url(entry):
                return True
        return False

    @classmethod
    def _video_url(cls, raw: dict) -> str:
        for key in (
            "play_url", "video_url", "download_url", "playUrl", "videoUrl",
            "downloadUrl", "playAddr", "downloadAddr", "url",
        ):
            value = raw.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                for url in value.get("url_list") or value.get("urlList") or value.get("UrlList") or []:
                    if isinstance(url, str) and url.startswith("http"):
                        return url
        nested = raw.get("video")
        if isinstance(nested, dict):
            url = cls._video_url(nested)
            if url:
                return url
        bitrate = raw.get("bitrateInfo") or raw.get("bitrate_info")
        if isinstance(bitrate, list):
            for entry in bitrate:
                if not isinstance(entry, dict):
                    continue
                play = entry.get("PlayAddr") or entry.get("playAddr") or {}
                urls = play.get("UrlList") or play.get("urlList") or []
                if urls:
                    return str(urls[0])
        return ""

    @classmethod
    def _iter_dicts(cls, node: Any, depth: int = 0):
        if depth > 10:
            return
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from cls._iter_dicts(value, depth + 1)
        elif isinstance(node, list):
            for value in node[:60]:
                yield from cls._iter_dicts(value, depth + 1)


ADAPTERS: tuple[PlatformAdapter, ...] = (
    # ADAPTERS[0] is the fallback for unknown links, so 抖音 must stay first.
    DouyinAdapter(),
    TikTokShopAdapter(),
    TikTokAdapter(),
    BilibiliAdapter(),
    XiaohongshuAdapter(),
)


def normalize_profile_url(url: str) -> str:
    """修复从浏览器/聊天窗口复制时常见的残缺链接，避免 Selenium 报 invalid argument。

    处理的情况（幂等，可反复调用）：
    - ``httpswww.douyin.com...`` / ``httpwww...`` / ``https:www...`` -> ``https://www...``
    - ``www.douyin.com/...``（缺 scheme）-> 自动补 ``https://``
    - ``.comuser/`` -> ``.com/user/``、``.com@`` -> ``.com/@``（复制时丢了 ``/``）
    - ``...AJ8arqqnfrom_tab_name=main``（丢了 ``?``）-> 自动补 ``?``
    """
    if not isinstance(url, str):
        return url
    fixed = url.strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\\", "/")
    if not fixed:
        return fixed
    # 1) 修 scheme：httpswww. / httpwww. / https:/www. / https:www. 等
    #    同时覆盖 httpsshop.tiktok.com 这类“丢掉了 ://”的子域名链接
    fixed = re.sub(r"^(https?)[ :/]*(?=[a-z0-9-]+\.)", r"\1://", fixed, flags=re.IGNORECASE)
    # 2) 修缺失的 / ：.comuserMS4 -> .com/user/MS4、.com@xxx -> .com/@xxx 等
    fixed = re.sub(r"(\.com)user", r"\1/user", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"(/user)(?![/])", r"\1/", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"(\.com)(@[^/])", r"\1/\2", fixed)
    # 3) 缺 scheme 的补 https://
    if re.match(r"^(www\.|douyin\.com|tiktok\.com|shop\.tiktok\.com|bilibili\.com|xiaohongshu\.com|b23\.tv|v\.douyin\.com)", fixed, re.IGNORECASE):
        fixed = "https://" + fixed
    # 4) 查询串丢了 ? ：...xxxfrom_tab_name=main -> ...xxx?from_tab_name=main
    if "?" not in fixed and "from_tab_name=" in fixed:
        fixed = re.sub(r"([A-Za-z0-9_\-])((?:from_tab_name)=)", r"\1?\2", fixed)
    return fixed


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
