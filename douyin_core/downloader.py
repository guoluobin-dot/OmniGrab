from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from threading import Event
from typing import Callable
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests

from .database import DownloadDB
from .logger import get_logger
from .proxy import proxy_candidates

logger = get_logger(__name__)

# Network-level failures worth retrying through a discovered proxy,
# or resuming from the partial file (Bilibili CDN 经常中途断流).
_PROXY_RETRY_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
_RESUMABLE_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)

# Check yt-dlp availability
try:
    import yt_dlp
    _YTDLP_AVAILABLE = True
except ImportError:
    _YTDLP_AVAILABLE = False
    logger.warning("yt-dlp 未安装，B 站下载将仅使用内置实现。建议运行: pip install yt-dlp")

# WBI 签名密钥（会定期更新，这里使用通用的）
_WBI_IMG_KEY = "7cd084941338484aae1ad9425b84077c"
_WBI_SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"
_WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 25, 30, 41, 24, 57, 34, 22, 44, 56,
    51, 54, 48, 52, 17, 55, 40, 59, 26, 37, 60, 4, 16, 36, 20, 61, 6, 11, 13, 7,
    62, 21, 63, 1, 0,
]


def _get_mixin_key(orig: str) -> str:
    """对 imgKey 和 subKey 进行字符顺序打乱"""
    return "".join(orig[i] for i in _WBI_MIXIN_KEY_ENC_TAB)[:32]


def _sign_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """为请求参数添加 WBI 签名 (w_rid, wts)"""
    mixin_key = _get_mixin_key(img_key + sub_key)
    curr_time = int(time.time())
    params["wts"] = curr_time
    # 按 key 排序
    query = urlencode(sorted(params.items()))
    wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = wbi_sign
    return params


def _add_wbi_signature(url: str) -> str:
    """给 B 站 API URL 添加 WBI 签名"""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    # 转换为单值字典
    params = {k: v[0] for k, v in params.items()}
    signed_params = _sign_wbi(params, _WBI_IMG_KEY, _WBI_SUB_KEY)
    new_query = urlencode(signed_params)
    return urlunparse(parsed._replace(query=new_query))


_VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4v")


def _friendly_ytdlp_tiktok_error(text: str) -> str:
    """Translate yt-dlp's TikTok failures into something actionable.

    Almost every variant here means the same thing to the user: the session is
    anonymous or region-blocked, so the extractor is served a login wall.
    """
    lowered = text.lower()
    if "unexpected response from webpage" in lowered or "unable to download webpage" in lowered:
        return ("TikTok 未向当前会话返回播放地址（通常是未登录或存在地区限制）。"
                "请在工具弹出的浏览器窗口中登录 TikTok 后重新读取作品再下载")
    if "login" in lowered or "sign in" in lowered or "captcha" in lowered or "verify" in lowered:
        return "TikTok 要求登录或人机验证：请在浏览器窗口中完成登录/验证后重试"
    if "timed out" in lowered or "connection" in lowered:
        return "网络无法连接 TikTok（可能需要开启系统代理）"
    if "unsupported url" in lowered or "no video formats" in lowered:
        return "该链接没有可用的视频格式，请确认是公开的视频/商品页"
    return f"TikTok 视频下载失败：{text[:160]}"


def _write_netscape_cookie_file(cookies: list[dict[str, str]]) -> str | None:
    """Write browser cookies as a Netscape cookie jar for yt-dlp.

    Returns the temp file path, or None when there is nothing to write. The
    caller is responsible for deleting it.
    """
    usable = [
        c for c in cookies
        if c.get("name") and c.get("value") and c.get("domain", "").lstrip(".")
    ]
    if not usable:
        return None
    lines = ["# Netscape HTTP Cookie File", ""]
    for cookie in usable:
        domain = cookie.get("domain", "")
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        # Netscape: domain, includeSubdomains, path, secure, expiry, name, value
        lines.append("\t".join([
            domain, "TRUE" if domain.startswith(".") else "FALSE",
            cookie.get("path") or "/", secure,
            str(int(cookie.get("expiry") or cookie.get("expirationDate") or 0)),
            cookie["name"], cookie["value"],
        ]))
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="tiktok_cookies_", delete=False, encoding="utf-8"
    )
    try:
        handle.write("\n".join(lines) + "\n")
    finally:
        handle.close()
    return handle.name


def _is_image_header(head: bytes) -> bool:
    """Detect JPEG/PNG/GIF/WebP magic bytes in the first block of a file."""
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF")):
        return True
    # WEBP is a RIFF container with a WEBP fourcc at offset 8.
    return head.startswith(b"RIFF") and head[8:12] == b"WEBP"


def _origin_of(url: str) -> str:
    """Return ``scheme://host`` for a URL, or "" when it cannot be parsed."""
    parsed = urlparse(str(url or ""))
    if not parsed.scheme.startswith("http") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


class _DownloadCancelled(Exception):
    pass


class Downloader:
    """Downloads only when explicitly invoked; collection returns URLs by default."""
    def __init__(self, download_dir: str = "downloads", max_retry: int = 3, deduplicate: bool = True, cookie: str = "", headers: dict | None = None, session: requests.Session | None = None, cancel_event: Event | None = None, prefer_ytdlp: bool = False) -> None:
        self.download_dir, self.max_retry, self.session = download_dir, max_retry, session or requests.Session()
        self.last_error = ""
        self._cancel_event = cancel_event
        self._working_proxies: dict[str, str] | None = None
        self._proxy_candidates: list[dict[str, str] | None] | None = None
        self.prefer_ytdlp = prefer_ytdlp
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

    def _cookie_pairs(self, post: dict) -> list[dict[str, str]]:
        """Cookies to hand to yt-dlp, preferring the structured session cookie.

        The reader can supply per-cookie metadata (``post["cookies"]``), which
        keeps the Netscape jar accurate; the flat header string is the fallback.
        """
        structured = post.get("cookies")
        if isinstance(structured, list) and structured:
            return [c for c in structured if isinstance(c, dict)]
        header = post.get("cookie") or self.session.headers.get("Cookie", "") or ""
        pairs: list[dict[str, str]] = []
        for segment in str(header).split(";"):
            name, separator, value = segment.strip().partition("=")
            if separator and name:
                pairs.append({
                    "domain": ".tiktok.com", "path": "/", "secure": True,
                    "name": name, "value": value,
                })
        return pairs

    @staticmethod
    def _expects_video(filepath: str) -> bool:
        """True when this target must end up a playable video file.

        Keeps the image check scoped to video targets so legitimate image-set
        downloads (.jpg/.png) keep working while a cover image saved as
        ``.mp4`` is rejected instead of being reported as a success.
        """
        return os.path.splitext(str(filepath))[1].lower() in _VIDEO_SUFFIXES

    def download_file(self, url: str, filepath: str, progress_callback: Callable[[int, int], None] | None = None, headers: dict | None = None, max_retry: int | None = None) -> bool:
        if self.is_cancelled():
            self.last_error = "下载已被用户中断"
            return False
        if not url:
            self.last_error = "作品没有可用的媒体下载地址"
            return False
        if os.path.exists(filepath): return True
        attempts = max_retry if max_retry is not None else self.max_retry
        # B 站 CDN 对大文件经常中途断流：保留 .part 断点续传，而不是删掉重下。
        temp_path = f"{filepath}.part"
        already = 0
        try:
            already = os.path.getsize(temp_path)
        except OSError:
            already = 0
        for attempt in range(1, attempts + 1):
            if self.is_cancelled():
                self.last_error = "下载已被用户中断"
                return False
            try:
                resume_headers = dict(headers or {})
                if already > 0:
                    resume_headers["Range"] = f"bytes={already}-"
                response = self._request_media(url, resume_headers or headers)
                content_type = response.headers.get("content-type", "").lower()
                if response.status_code in (403, 410):
                    self.last_error = "媒体链接已过期或被拒绝（403），请重新读取作品后再下载"
                    if os.path.exists(temp_path): os.remove(temp_path)
                    return False
                if response.status_code == 416:
                    # Range 超出范围：多半是上次的 .part 已完整，校验后直接用
                    if already > 0:
                        os.replace(temp_path, filepath); return True
                    if os.path.exists(temp_path): os.remove(temp_path)
                    already = 0
                    continue
                if "text/html" in content_type or "application/json" in content_type:
                    self.last_error = f"下载地址返回非媒体内容 ({content_type})"
                    if os.path.exists(temp_path): os.remove(temp_path)
                    already = 0
                    return False
                if content_type.startswith("image/") and self._expects_video(filepath):
                    # 封面图冒充视频地址：不能写成 .mp4，必须报错而不是产出坏文件
                    self.last_error = f"下载地址返回的是图片而非视频 ({content_type})，请重新读取作品"
                    if os.path.exists(temp_path): os.remove(temp_path)
                    already = 0
                    return False
                response.raise_for_status()
                # 服务器不支持断点续传时会返回 200 全量：此时从头写
                if response.status_code != 206 and already > 0:
                    already = 0
                os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
                remaining = int(response.headers.get("content-length", 0))
                total = already + remaining if remaining else 0
                downloaded = already
                mode = "ab" if (already > 0 and response.status_code == 206) else "wb"
                if mode == "wb":
                    downloaded = 0
                with open(temp_path, mode) as output:
                    for chunk in response.iter_content(chunk_size=65536):
                        if self.is_cancelled():
                            raise _DownloadCancelled()
                        if chunk: output.write(chunk); downloaded += len(chunk); progress_callback and progress_callback(downloaded, total)
                if not downloaded and not already: raise ValueError("媒体响应为空")
                already = downloaded
                # Validate the bytes we actually wrote: an image or an error page
                # must never be handed back as a successful download.
                with open(temp_path, "rb") as f:
                    head = f.read(16)
                if head.startswith((b"<!DO", b"<htm", b"<HTM", b'{"', b"{\\")):
                    raise ValueError(
                        f"下载内容不是媒体文件，头部为 {head[:20]!r}，可能是链接已过期或被风控拦截"
                    )
                if _is_image_header(head) and self._expects_video(filepath):
                    raise ValueError(
                        "下载内容是图片（封面）而不是视频：平台未返回播放地址，"
                        "请先在浏览器中登录 TikTok 后重新读取作品"
                    )
                # content-length 校验：长度对不上说明还是断流，继续续传而不是判失败
                if total and downloaded < total:
                    raise requests.exceptions.ChunkedEncodingError(
                        f"下载不完整（已下载 {downloaded}/{total} 字节），自动断点续传"
                    )
                os.replace(temp_path, filepath); return True
            except _DownloadCancelled:
                self.last_error = "下载已被用户中断"
                # 用户主动中断：保留 .part 以便继续下载时续传
                return False
            except Exception as exc:
                if isinstance(exc, ValueError) and ("不是媒体文件" in str(exc) or "是图片" in str(exc)):
                    self.last_error = str(exc)
                    if os.path.exists(temp_path): os.remove(temp_path)
                    already = 0
                    return False
                if isinstance(exc, ValueError) and "媒体响应为空" in str(exc):
                    self.last_error = str(exc)
                    if os.path.exists(temp_path): os.remove(temp_path)
                    already = 0
                else:
                    try:
                        already = os.path.getsize(temp_path)
                    except OSError:
                        already = 0
                    self.last_error = self._friendly_network_error(exc, already)
                logger.warning("下载失败 (%d/%d): %s", attempt, attempts, exc)
                if attempt < attempts: time.sleep(min(2 * attempt, 10))
        return False

    @staticmethod
    def _friendly_network_error(exc: Exception, downloaded: int) -> str:
        """把 requests/urllib3 的长异常压缩成用户能看懂的一句话。"""
        text = str(exc)
        if "IncompleteRead" in text or isinstance(exc, _RESUMABLE_ERRORS):
            if downloaded > 0:
                return f"网络中断（已下载 {downloaded // 1024 // 1024}MB），重试仍未完成，可点“继续下载”接着下"
            return "网络中断，连接被服务器重置，可点“继续下载”重试"
        short = text.strip().splitlines()[0] if text.strip() else repr(exc)
        return short[:200]

    def _request_media(self, url: str, headers: dict | None = None) -> requests.Response:
        """GET the media URL, falling back to discovered proxies on network blocks."""
        attempts: list[dict[str, str] | None] = [self._working_proxies] if self._working_proxies else []
        if self._proxy_candidates is None:
            self._proxy_candidates = proxy_candidates()
        for candidate in self._proxy_candidates:
            if candidate not in attempts:
                attempts.append(candidate)
        last_error: Exception | None = None
        for index, proxies in enumerate(attempts):
            if self.is_cancelled():
                raise last_error or RuntimeError("cancelled")
            try:
                response = self.session.get(url, stream=True, timeout=(15, 120), allow_redirects=True, headers=headers, proxies=proxies)
                if proxies is not None:
                    self._working_proxies = proxies
                    if proxies is not attempts[0]:
                        logger.info("媒体下载改经本地代理 %s 进行", proxies.get("https"))
                return response
            except _PROXY_RETRY_ERRORS as exc:
                last_error = exc
                logger.debug("直连/代理候选 %d 失败：%s", index, exc)
                continue
        assert last_error is not None
        raise last_error

    def download_video(self, post: dict, progress_callback=None) -> bool:
        aweme_id = post.get("aweme_id", "")
        if self.db and self.db.is_downloaded(aweme_id): return True
        path = os.path.join(self.videos_dir, f"{self._post_stem(post)}.mp4")
        
        # Check if it's a Bilibili video (has bvid/cid and video_url is an API endpoint)
        video_url = post.get("video_url", "")
        is_bilibili = "api.bilibili.com/x/player/wbi/playurl" in video_url and ("bvid=" in video_url or "cid=" in video_url)
        is_tiktok = "tiktokcdn.com" in video_url or "tiktokv.com" in video_url or "ttcdn-us.com" in video_url

        if is_bilibili:
            if self.prefer_ytdlp and _YTDLP_AVAILABLE:
                # Use yt-dlp as primary
                logger.info("使用 yt-dlp 作为主要下载器...")
                success = self._download_bilibili_with_ytdlp(post, path, progress_callback)
                # Fallback to built-in if yt-dlp fails
                if not success:
                    logger.info("yt-dlp 下载失败，尝试内置下载器...")
                    success = self._download_bilibili_video(post, path, progress_callback)
            else:
                # Use built-in as primary
                success = self._download_bilibili_video(post, path, progress_callback)
                # Fallback to yt-dlp if built-in fails
                if not success and _YTDLP_AVAILABLE:
                    logger.info("内置下载失败，尝试使用 yt-dlp 备选下载...")
                    success = self._download_bilibili_with_ytdlp(post, path, progress_callback)
        elif is_tiktok:
            success = self.download_file(video_url, path, progress_callback, headers=self._post_headers(post))
            if not success and post.get("web_url"):
                # TikTok 只给封面/不给播放地址时，用 yt-dlp 重新解析一次
                success = self._download_tiktok_with_ytdlp(post, path, progress_callback)
        else:
            success = self.download_file(video_url, path, progress_callback, headers=self._post_headers(post))

        if success:
            self._apply_publish_time(path, post)
        if success and self.db: self.db.add_record(aweme_id, post.get("desc", ""), "video", path)
        return success

    def _download_bilibili_video(self, post: dict, output_path: str, progress_callback=None) -> bool:
        """Download Bilibili video via playurl API (DASH format)."""
        video_url = post.get("video_url", "")
        if not video_url:
            self.last_error = "B站视频缺少 playurl API 地址"
            return False
        
        # 添加 WBI 签名
        signed_url = _add_wbi_signature(video_url)
        
        # Referer 必须是具体的视频页面，不是首页
        referer = post.get("web_url") or post.get("referer") or "https://www.bilibili.com/"
        headers = {"Origin": "https://www.bilibili.com", "Referer": referer}
        
        try:
            # Fetch playurl API response
            response = self._request_media(signed_url, headers)
            if response.status_code != 200:
                self.last_error = f"获取播放链接失败: HTTP {response.status_code}"
                return False
            
            data = response.json()
            if data.get("code") != 0:
                self.last_error = f"播放链接API错误: {data.get('message', '未知错误')}"
                return False
            
            dash = data.get("data", {}).get("dash", {})
            video_list = dash.get("video", [])
            audio_list = dash.get("audio", [])
            
            if not video_list or not audio_list:
                # 尝试获取 durl (旧版非 DASH 格式)
                durl = data.get("data", {}).get("durl", [])
                if durl:
                    # 降级：直接下载单个 MP4
                    video_src = durl[0].get("url", "")
                    if video_src:
                        return self.download_file(video_src, output_path, progress_callback, headers=headers)
                self.last_error = "未找到可用的视频/音频流"
                return False
            
            # Select best quality (highest bandwidth)
            best_video = max(video_list, key=lambda x: x.get("bandwidth", 0))
            best_audio = max(audio_list, key=lambda x: x.get("bandwidth", 0))
            
            video_src = best_video.get("baseUrl") or best_video.get("base_url") or ""
            audio_src = best_audio.get("baseUrl") or best_audio.get("base_url") or ""
            
            if not video_src or not audio_src:
                self.last_error = "视频/音频流地址为空"
                return False
            
            # Check if ffmpeg is available for merging
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                self.last_error = "需要安装 ffmpeg 才能合并 B 站视频流。请安装 ffmpeg 并添加到 PATH。"
                return False
            
            # Download video and audio streams to temp files
            temp_dir = os.path.dirname(output_path)
            video_temp = os.path.join(temp_dir, f"{os.path.basename(output_path)}.video.tmp")
            audio_temp = os.path.join(temp_dir, f"{os.path.basename(output_path)}.audio.tmp")
            
            try:
                logger.info("下载视频流...")
                if not self.download_file(video_src, video_temp, progress_callback, headers=headers, max_retry=max(6, self.max_retry)):
                    return False

                logger.info("下载音频流...")
                if not self.download_file(audio_src, audio_temp, progress_callback, headers=headers, max_retry=max(6, self.max_retry)):
                    return False
                
                # Merge with ffmpeg
                logger.info("合并视频音频...")
                cmd = [
                    ffmpeg_path, "-y", "-loglevel", "error",
                    "-i", video_temp, "-i", audio_temp,
                    "-c", "copy", output_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=300)
                if result.returncode != 0:
                    self.last_error = f"ffmpeg 合并失败: {result.stderr.decode(errors='ignore')}"
                    return False
                
                return True
            finally:
                # Cleanup temp files
                for temp_file in (video_temp, audio_temp):
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    except OSError:
                        pass
                        
        except Exception as exc:
            self.last_error = f"B站视频下载失败: {exc}"
            logger.exception("Bilibili video download failed")
            return False

    def _download_tiktok_with_ytdlp(self, post: dict, output_path: str, progress_callback=None) -> bool:
        """Fallback for TikTok: let yt-dlp resolve the signed play URL itself.

        TikTok increasingly returns an empty ``playAddr`` to anonymous sessions
        and applies region rules to the CDN links it does hand out. yt-dlp keeps
        its own extractor logic current, so it recovers those cases — provided we
        pass the browser session cookies and the local proxy that Python cannot
        reach TikTok without.
        """
        if not _YTDLP_AVAILABLE:
            self.last_error = "TikTok 播放地址缺失，且未安装 yt-dlp（pip install yt-dlp）"
            return False
        web_url = post.get("web_url") or ""
        if not web_url.startswith("http"):
            self.last_error = "TikTok 播放地址缺失，且缺少可用于重取的作品链接"
            return False

        def progress_hook(d):
            if self.is_cancelled():
                raise Exception("用户取消下载")
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                if progress_callback and total:
                    progress_callback(done, total)

        base = output_path[: -len(".mp4")] if output_path.endswith(".mp4") else output_path
        ydl_opts = {
            "outtmpl": f"{base}.%(ext)s",
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [progress_hook],
            "noplaylist": True,
            "retries": max(2, self.max_retry),
            "http_headers": {"Referer": post.get("referer") or "https://www.tiktok.com/"},
        }
        # 会话 Cookie 走 Netscape 文件：yt-dlp 需要它来通过 TikTok 的登录校验，
        # 直接塞进 Cookie 请求头既不生效也会泄漏到子域。
        cookie_file = None
        try:
            cookie_file = _write_netscape_cookie_file(self._cookie_pairs(post))
            if cookie_file:
                ydl_opts["cookiefile"] = cookie_file
        except Exception as exc:
            logger.debug("导出 Cookie 文件失败：%s", exc)
        # Python 直连 TikTok 常被本地代理挡住；带上已发现的代理候选
        if self._proxy_candidates is None:
            self._proxy_candidates = proxy_candidates()
        for candidate in self._proxy_candidates:
            if candidate:
                ydl_opts["proxy"] = candidate["https"]
                break

        try:
            logger.info("TikTok 播放地址缺失，改用 yt-dlp 解析：%s", web_url)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([web_url])
            if os.path.exists(base + ".part"):
                os.remove(base + ".part")
            for ext in (".mp4", ".mkv", ".webm", ".mov"):
                if os.path.exists(base + ext):
                    if ext != ".mp4":
                        os.replace(base + ext, output_path)
                    break
            if os.path.exists(output_path):
                self._apply_publish_time(output_path, post)
                return True
            self.last_error = "yt-dlp 未产出视频文件"
            return False
        except Exception as exc:
            if "用户取消下载" in str(exc):
                self.last_error = "下载已被用户中断"
            else:
                self.last_error = _friendly_ytdlp_tiktok_error(str(exc))
                logger.warning("yt-dlp TikTok fallback failed: %s", exc)
            return False
        finally:
            if cookie_file:
                try:
                    os.remove(cookie_file)
                except OSError:
                    pass

    def _download_bilibili_with_ytdlp(self, post: dict, output_path: str, progress_callback=None) -> bool:
        """使用 yt-dlp 下载 B 站视频（支持合集、番剧、字幕、更高画质）"""
        if not _YTDLP_AVAILABLE:
            self.last_error = "yt-dlp 未安装，请运行: pip install yt-dlp"
            return False
        
        web_url = post.get("web_url") or post.get("video_url") or ""
        if not web_url:
            self.last_error = "缺少视频页面 URL"
            return False
        
        # 如果是 playurl API，转换为视频页面 URL
        if "api.bilibili.com/x/player/wbi/playurl" in web_url:
            # 从 video_url 中提取 bvid
            import urllib.parse as up
            parsed = up.urlparse(web_url)
            params = up.parse_qs(parsed.query)
            bvid = params.get("bvid", [""])[0]
            if bvid:
                web_url = f"https://www.bilibili.com/video/{bvid}"
        
        if not web_url.startswith("http"):
            self.last_error = "无效的视频 URL"
            return False
        
        # Progress hook
        progress_data = {"downloaded": 0, "total": 0}
        
        def progress_hook(d):
            if self.is_cancelled():
                raise Exception("用户取消下载")
            if d["status"] == "downloading":
                progress_data["downloaded"] = d.get("downloaded_bytes", 0)
                progress_data["total"] = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                if progress_callback and progress_data["total"]:
                    progress_callback(progress_data["downloaded"], progress_data["total"])
            elif d["status"] == "finished":
                if progress_callback:
                    progress_callback(progress_data["total"] or 1, progress_data["total"] or 1)
        
        # yt-dlp options
        ydl_opts = {
            "outtmpl": output_path.replace(".mp4", ".%(ext)s"),
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [progress_hook],
            "noprogress": True,
            "retries": self.max_retry,
            "fragment_retries": self.max_retry,
            "ignoreerrors": False,
            "extract_flat": False,
            "writesubtitles": False,
            "writeautomaticsub": False,
        }
        
        # Add cookies if available
        cookie = post.get("cookie") or self.session.headers.get("Cookie", "")
        if cookie:
            ydl_opts["http_headers"] = {"Cookie": cookie, "Referer": "https://www.bilibili.com/"}
        else:
            ydl_opts["http_headers"] = {"Referer": "https://www.bilibili.com/"}
        
        try:
            logger.info(f"使用 yt-dlp 下载: {web_url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([web_url])
            
            # Find the actual output file (yt-dlp may change extension)
            base = output_path.replace(".mp4", "")
            for ext in [".mp4", ".mkv", ".webm", ".flv"]:
                if os.path.exists(base + ext):
                    if ext != ".mp4":
                        os.rename(base + ext, output_path)
                    break
            
            if os.path.exists(output_path):
                self._apply_publish_time(output_path, post)
                return True
            else:
                self.last_error = "yt-dlp 下载完成但找不到输出文件"
                return False
                
        except Exception as exc:
            if "用户取消下载" in str(exc):
                self.last_error = "下载已被用户中断"
            else:
                self.last_error = f"yt-dlp 下载失败: {exc}"
                logger.exception("yt-dlp download failed")
            return False

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
        """Per-platform media servers validate their own Referer (e.g. TikTok).

        Shop 视频走 TikTok 的电商 CDN，除了 Referer 还需要携带 Origin，
        否则会直接返回 403。
        """
        referer = str(post.get("referer") or "")
        headers: dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        origin = _origin_of(referer)
        if origin and ("shop.tiktok.com" in origin or "/shop" in urlparse(referer).path):
            headers["Origin"] = origin
        return headers or None

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
        return extension if extension in {".jpg", ".jpeg", ".png", ".webp", ".avif"} else ".jpg"

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
