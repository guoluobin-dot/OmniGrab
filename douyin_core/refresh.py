"""Refresh expired media URLs by re-reading the author's profile.

TikTok signs media URLs with a lifetime of only a few minutes. When a user
downloads long after reading, the stored URLs return 403. This module re-runs
a lightweight browser read and maps the fresh posts back onto the stale ones.
"""
from __future__ import annotations

from typing import Callable

from .browser_reader import BrowserProfileReader
from .logger import get_logger

logger = get_logger(__name__)
StatusCallback = Callable[[str], None] | None


def profile_url_for_post(post: dict) -> str:
    """Derive the author profile URL from a normalised post."""
    web_url = str(post.get("web_url") or "")
    if "tiktok.com/" in web_url:
        for part in web_url.split("/"):
            if part.startswith("@") and len(part) > 1:
                return f"https://www.tiktok.com/{part}"
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    sec_uid = str(author.get("sec_uid") or "")
    if sec_uid:
        return f"https://www.douyin.com/user/{sec_uid}"
    return ""


def refresh_posts(
    posts: list[dict],
    *,
    status_callback: StatusCallback = None,
    max_scrolls: int = 400,
) -> dict[str, dict]:
    """Return ``{aweme_id: fresh_post}`` for every post that could be re-captured.

    Posts are grouped by profile; each profile costs one browser session.
    ``max_scrolls`` bounds how deep the refresh reads (defaults deep enough to
    cover a full profile).
    """
    wanted_ids = {str(post.get("aweme_id") or "") for post in posts}
    wanted_ids.discard("")

    by_profile: dict[str, list[str]] = {}
    for post in posts:
        profile_url = profile_url_for_post(post)
        post_id = str(post.get("aweme_id") or "")
        if profile_url and post_id:
            by_profile.setdefault(profile_url, []).append(post_id)

    refreshed: dict[str, dict] = {}
    for number, (profile_url, ids) in enumerate(by_profile.items(), start=1):
        if status_callback:
            status_callback(
                f"正在刷新媒体链接（{number}/{len(by_profile)} 个主页）…"
                "约需 1-2 分钟，请在弹出的浏览器中保持登录"
            )
        reader = BrowserProfileReader(max_scrolls=max_scrolls)
        try:
            result = reader.read_profile(
                profile_url,
                max_count=0,
                status_callback=lambda message: status_callback and status_callback(message),
            )
        except Exception as exc:
            logger.warning("刷新主页 %s 失败：%s", profile_url, exc)
            if status_callback:
                status_callback(f"刷新该主页失败，已跳过：{exc}")
            continue
        finally:
            reader.close()
        for fresh in result.posts:
            fresh_id = str(fresh.get("aweme_id") or "")
            if fresh_id in wanted_ids:
                refreshed[fresh_id] = fresh
        missing = [pid for pid in ids if pid not in refreshed]
        if missing:
            logger.info("刷新后仍未取到 %d 个作品的新链接", len(missing))
    return refreshed
