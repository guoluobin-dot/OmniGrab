"""Detect a usable Chrome/Chromium installation before launching the GUI."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CHROME_DOWNLOAD_URL = "https://www.google.com/chrome/"


def _candidates() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("CHROME_PATH")
    if override:
        paths.append(Path(override))
    if sys.platform == "win32":
        roots = [
            value
            for value in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA"))
            if value
        ]
        relative_names = (
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
            Path("Chromium") / "Application" / "chrome.exe",
        )
        paths.extend(root / name for root in map(Path, roots) for name in relative_names)
    elif sys.platform == "darwin":
        paths.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            located = shutil.which(name)
            if located:
                paths.append(Path(located))
        paths.extend(
            Path(fixed)
            for fixed in ("/opt/google/chrome/chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser")
        )
    return paths


def find_chrome() -> str | None:
    """Return the first existing Chrome/Chromium executable path, if any."""
    for candidate in _candidates():
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def chrome_missing_message() -> str:
    return (
        "未检测到已安装的 Chrome 浏览器。\n\n"
        "本工具通过真实 Chrome 会话读取作品，请先安装 Google Chrome（或 Chromium）后重新打开程序。\n"
        "安装完成后无需其他配置，程序会自动使用。"
    )
