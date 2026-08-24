"""Discover local HTTP proxies so media downloads can route around blocks.

TikTok media edges are unreachable from some networks while the local proxy
tool keeps working. Windows proxy settings live in the registry (python does
not read them), so candidates are collected from the environment, the WinINET
configuration, and — as a last resort — a configured-but-disabled localhost
proxy whose port is still alive.
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import requests

WININET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _port_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _normalise(proxy_server: str) -> dict[str, str] | None:
    """Turn a WinINET ProxyServer value into a requests proxies dict."""
    value = (proxy_server or "").strip()
    if not value:
        return None
    # "http=host:port;https=host:port" per-protocol form
    if ";" in value or "=" in value:
        parts = dict(
            piece.split("=", 1) for piece in value.split(";") if "=" in piece
        )
        target = parts.get("https") or parts.get("http")
        if not target:
            return None
        value = target
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if not parsed.hostname or not parsed.port:
        return None
    return {"http": f"http://{parsed.hostname}:{parsed.port}", "https": f"http://{parsed.hostname}:{parsed.port}"}


def _registry_proxy_settings() -> tuple[bool, str]:
    try:
        import winreg
    except ImportError:  # non-Windows
        return False, ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WININET_KEY) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except OSError:
                server = ""
            return bool(enabled), server or ""
    except OSError:
        return False, ""


def proxy_candidates() -> list[dict[str, str] | None]:
    """Ordered proxy configs to try: direct first, then discovered proxies."""
    import os

    candidates: list[dict[str, str] | None] = [None]

    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value:
            normalised = _normalise(value)
            if normalised and normalised not in candidates:
                candidates.append(normalised)

    enabled, server = _registry_proxy_settings()
    normalised = _normalise(server)
    if normalised:
        if enabled:
            if normalised not in candidates:
                candidates.append(normalised)
        elif normalised["http"].startswith("http://127.0.0.1") or normalised["http"].startswith("http://localhost"):
            # A configured-but-disabled localhost proxy is still worth one
            # probe: many proxy tools leave the port listening.
            host = urlparse(normalised["http"]).hostname or "127.0.0.1"
            port = urlparse(normalised["http"]).port or 0
            if port and _port_alive(host, port) and normalised not in candidates:
                candidates.append(normalised)
    return candidates


def download_works_through(proxies: dict[str, str] | None, test_url: str = "https://www.tiktok.com/") -> bool:
    try:
        response = requests.get(
            test_url,
            proxies=proxies,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"},
        )
        return response.status_code < 500
    except requests.RequestException:
        return False
