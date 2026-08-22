from __future__ import annotations
import json
import os
from typing import Optional

from .runtime_paths import config_dir

def _default_cookie_file() -> str:
    return str(config_dir() / "cookie.json")

def save_cookie(cookie: str, cookie_file: Optional[str] = None) -> bool:
    cookie_file = cookie_file or _default_cookie_file()
    try:
        os.makedirs(os.path.dirname(cookie_file) or ".", exist_ok=True)
        with open(cookie_file, "w", encoding="utf-8") as output: json.dump({"cookie": cookie}, output, ensure_ascii=False, indent=2)
        return True
    except OSError: return False
def load_cookie(cookie_file: Optional[str] = None) -> Optional[str]:
    cookie_file = cookie_file or _default_cookie_file()
    try:
        with open(cookie_file, encoding="utf-8") as source: return json.load(source).get("cookie") or None
    except (OSError, json.JSONDecodeError): return None
def validate_cookie(cookie: str) -> bool:
    names = {part.strip().partition("=")[0] for part in cookie.split(";") if "=" in part}
    return bool({"ttwid", "msToken"} & names)
def get_cookie_guide() -> str:
    return "在可见 Chrome 中打开 douyin.com 并登录；持久化 browser_profile 会自动复用会话。不要共享 Cookie。"
