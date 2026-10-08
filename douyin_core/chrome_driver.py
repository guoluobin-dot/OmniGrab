"""Resolve a ChromeDriver quickly and without ever blocking indefinitely.

Chrome updates often invalidate the cached chromedriver.  When that happens
Selenium Manager tries to download a new build from the network; on a slow or
restricted connection that download hangs for minutes with no feedback, so the
GUI freezes at "正在启动 Chrome 进程…" and no browser window ever appears.

This module keeps every step bounded:

1. reuse a chromedriver already on disk whose major version matches Chrome;
2. otherwise ask Selenium Manager in ``--offline`` mode (instant, no network);
3. only then allow a single online download, with a hard timeout;
4. otherwise raise a clear, actionable error instead of hanging.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .logger import get_logger

logger = get_logger(__name__)

StatusCallback = Callable[[str], None] | None

#: Seconds allowed for the (first time only) chromedriver download.
DOWNLOAD_TIMEOUT = 120
#: Seconds allowed for the Chrome process itself to come up.
LAUNCH_TIMEOUT = 90


class DriverSetupError(RuntimeError):
    """No usable chromedriver could be prepared."""


def _notify(status: StatusCallback, message: str) -> None:
    logger.info(message)
    if status:
        try:
            status(message)
        except Exception:  # pragma: no cover - callback is best effort
            pass


def find_chrome() -> str | None:
    """Return the Chrome/Chromium executable path, if installed."""
    override = os.environ.get("CHROME_PATH")
    if override and os.path.isfile(override):
        return override
    if sys.platform == "win32":
        roots = [
            value
            for value in (
                os.environ.get("PROGRAMFILES"),
                os.environ.get("PROGRAMFILES(X86)"),
                os.environ.get("LOCALAPPDATA"),
            )
            if value
        ]
        for root in roots:
            for rel in (
                os.path.join("Google", "Chrome", "Application", "chrome.exe"),
                os.path.join("Chromium", "Application", "chrome.exe"),
            ):
                candidate = os.path.join(root, rel)
                if os.path.isfile(candidate):
                    return candidate
        return None
    if sys.platform == "darwin":
        for candidate in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            if os.path.isfile(candidate):
                return candidate
        return None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        located = shutil.which(name)
        if located:
            return located
    return None


def _win32_file_version(path: str) -> tuple[int, ...] | None:
    """Read the Win32 file version without needing pywin32."""
    try:
        import ctypes

        version_api = ctypes.windll.version
        size = version_api.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version_api.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version_api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None

        class VSFixedFileInfo(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
                ("dwProductVersionMS", ctypes.c_uint32),
                ("dwProductVersionLS", ctypes.c_uint32),
                ("dwFileFlagsMask", ctypes.c_uint32),
                ("dwFileFlags", ctypes.c_uint32),
                ("dwFileOS", ctypes.c_uint32),
                ("dwFileType", ctypes.c_uint32),
                ("dwFileSubtype", ctypes.c_uint32),
                ("dwFileDateMS", ctypes.c_uint32),
                ("dwFileDateLS", ctypes.c_uint32),
            ]

        info = ctypes.cast(pointer, ctypes.POINTER(VSFixedFileInfo)).contents
        high, low = info.dwFileVersionMS, info.dwFileVersionLS
        return (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.debug("读取文件版本失败 %s: %s", path, exc)
        return None


def chrome_version(chrome_path: str | None = None) -> tuple[int, ...] | None:
    path = chrome_path or find_chrome()
    if not path:
        return None
    if sys.platform == "win32":
        return _win32_file_version(path)
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_version(f"{proc.stdout.decode(errors='replace')} {proc.stderr.decode(errors='replace')}")


def _parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)\.?(\d+)?", text)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def driver_version(driver_path: str) -> tuple[int, ...] | None:
    """Return the version reported by ``chromedriver --version``."""
    try:
        proc = subprocess.run(
            [driver_path, "--version"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_version(proc.stdout.decode(errors="replace"))


def _selenium_manager() -> str | None:
    try:
        import selenium
    except ImportError:
        return None
    base = Path(selenium.__file__).resolve().parent / "webdriver" / "common"
    subdir = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    name = "selenium-manager.exe" if sys.platform == "win32" else "selenium-manager"
    candidate = base / subdir / name
    return str(candidate) if candidate.is_file() else None


def _iter_cached_drivers() -> list[str]:
    home = Path.home()
    roots = [
        home / ".cache" / "selenium" / "chromedriver",
        home / ".wdm" / "drivers" / "chromedriver",
    ]
    found: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            found.extend(str(path) for path in root.rglob("chromedriver.exe"))
        except OSError as exc:
            logger.debug("扫描驱动缓存失败 %s: %s", root, exc)
    on_path = shutil.which("chromedriver")
    if on_path:
        found.append(on_path)
    return found


def _run_selenium_manager(args: list[str], timeout: int) -> str | None:
    manager = _selenium_manager()
    if not manager:
        return None
    env = dict(os.environ)
    env["SE_AVOID_STATS"] = "true"
    try:
        proc = subprocess.run(
            [manager, "--browser", "chrome", *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        logger.warning("selenium-manager 超时（%ss）：%s", timeout, " ".join(args))
        return None
    except OSError as exc:
        logger.warning("selenium-manager 无法运行：%s", exc)
        return None
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    match = re.search(r"Driver path:\s*(.+)", output)
    if not match:
        logger.debug("selenium-manager 未返回驱动路径 rc=%s: %s", proc.returncode, output.strip())
        return None
    path = match.group(1).strip().strip('"')
    return path if os.path.isfile(path) else None


def resolve_chromedriver(status: StatusCallback = None) -> str | None:
    """Return a chromedriver path matching the installed Chrome, or ``None``.

    ``None`` means "let Selenium Manager decide" (only used as a last resort;
    the caller must still guard the launch with :data:`LAUNCH_TIMEOUT`).
    Raises :class:`DriverSetupError` when nothing usable can be prepared.
    """
    chrome_path = find_chrome()
    if not chrome_path:
        raise DriverSetupError(
            "未检测到 Chrome 浏览器。请先安装 Google Chrome 后重试。"
        )
    chrome_ver = chrome_version(chrome_path)
    major = chrome_ver[0] if chrome_ver else None
    logger.info("Chrome 版本：%s（%s）", ".".join(map(str, chrome_ver)) if chrome_ver else "未知", chrome_path)

    # 1) Reuse a driver already on disk (offline, instant).
    best: tuple[tuple[int, ...], str] | None = None
    for candidate in _iter_cached_drivers():
        version = driver_version(candidate)
        if not version:
            continue
        if major is not None and version[0] != major:
            continue
        if best is None or version > best[0]:
            best = (version, candidate)
    if best:
        _notify(status, f"使用本地缓存的 Chrome 驱动 { '.'.join(map(str, best[0])) }")
        return best[1]

    # 2) Ask Selenium Manager offline first - never touches the network.
    offline = _run_selenium_manager(["--offline"], timeout=15)
    if offline:
        version = driver_version(offline)
        if version and (major is None or version[0] == major):
            _notify(status, f"使用本地缓存的 Chrome 驱动 { '.'.join(map(str, version)) }")
            return offline

    # 3) Last resort: one bounded online download (first run only).
    _notify(
        status,
        "本地没有匹配版本的 Chrome 驱动，正在联网下载（首次约 1~2 分钟，请稍候）…",
    )
    downloaded = _run_selenium_manager([], timeout=DOWNLOAD_TIMEOUT)
    if downloaded:
        _notify(status, "Chrome 驱动准备完成")
        return downloaded

    raise DriverSetupError(
        "Chrome 驱动准备失败：本地没有与当前 Chrome 匹配的 chromedriver，"
        "且联网下载超时或失败。\n"
        "处理办法：1) 检查网络后重试；2) 手动下载对应版本的 chromedriver，"
        f"放到 {Path.home() / '.cache' / 'selenium' / 'chromedriver'} 目录下。"
    )
