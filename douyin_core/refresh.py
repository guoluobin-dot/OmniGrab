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
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    # Shop 视频只能回到原商品页换新链接，主页滚动拿不到
    if "shop.tiktok.com/" in web_url or "/shop/pdp/" in web_url or "/view/product/" in web_url:
        return web_url
    if "tiktok.com/" in web_url:
        for part in web_url.split("/"):
            if part.startswith("@") and len(part) > 1:
                return f"https://www.tiktok.com/{part}"
    if "xiaohongshu.com/" in web_url or "xhslink.com/" in web_url:
        uid = str(author.get("uid") or author.get("sec_uid") or "")
        if uid:
            return f"https://www.xiaohongshu.com/user/profile/{uid}"
        return ""
    if "bilibili.com/" in web_url or "b23.tv" in web_url:
        uid = str(author.get("uid") or author.get("sec_uid") or "")
        if uid and uid.isdigit():
            return f"https://space.bilibili.com/{uid}"
        return ""
    sec_uid = str(author.get("sec_uid") or "")
    if sec_uid:
        return f"https://www.douyin.com/user/{sec_uid}"
    return ""


def refresh_posts(
    posts: list[dict],
    *,
    status_callback: StatusCallback = None,
    max_scrolls: int = 400,
    cookies: list[dict] | None = None,
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
                # 保留原会话 Cookie：新链接仍需按旧 Referer 下载
                if cookies and not fresh.get("cookies"):
                    fresh["cookies"] = cookies
                refreshed[fresh_id] = fresh
        missing = [pid for pid in ids if pid not in refreshed]
        if missing:
            logger.info("刷新后仍未取到 %d 个作品的新链接", len(missing))
    return refreshed
