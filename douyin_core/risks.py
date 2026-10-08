"""Platform-risk boundaries used by every application integration."""
from __future__ import annotations

import json
import random
import time
from typing import Any


class DouyinCoreError(RuntimeError):
    """Base exception for recoverable collection failures."""


class RiskBlockedError(DouyinCoreError):
    """The platform returned a rate-limit, verification or risk-control page."""


class SessionExpiredError(DouyinCoreError):
    """The persistent browser session needs an interactive login."""


class ParseError(DouyinCoreError):
    """A response could not be parsed into a supported post."""


# These phrases are only meaningful in an API-level error message.  Do not
# scan a whole aweme payload: normal fields such as ``is_verify`` and a post
# description can legitimately contain "verify", "risk" or "登录".
RISK_MESSAGE_KEYWORDS = (
    "captcha",
    "验证码",
    "风控",
    "风险控制",
    "访问过于频繁",
    "请登录",
    "登录后查看",
    "login required",
    "risk control",
)
ERROR_MESSAGE_KEYS = ("status_msg", "statusMessage", "message", "error", "error_message", "detail")


def random_delay(minimum: float = 0.5, maximum: float = 1.5) -> None:
    """Apply a small jitter between page/API actions."""
    time.sleep(random.uniform(minimum, maximum))


def assert_not_risk_response(payload: Any) -> None:
    """Reject actual platform control responses without inspecting post content."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("<"):
            raise RiskBlockedError("平台返回非 JSON 页面，可能触发风控或需要登录")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RiskBlockedError("平台返回非 JSON 内容，无法安全解析") from exc
    if not isinstance(payload, dict):
        return

    status = payload.get("status_code", payload.get("statusCode"))
    messages = [str(payload.get(key, "")) for key in ERROR_MESSAGE_KEYS if payload.get(key)]
    message = " ".join(messages).lower()
    is_error_status = status not in (None, 0, "0", "success", True)
    if is_error_status and any(keyword.lower() in message for keyword in RISK_MESSAGE_KEYWORDS):
        raise RiskBlockedError("平台要求验证、登录或限制访问")


def ensure_session(reader: Any, profile_url: str = "https://www.douyin.com/") -> bool:
    """Open a visible persistent profile and require the caller to complete login if gated.

    It deliberately never solves CAPTCHAs; it provides a stable handoff to a human.
    """
    if getattr(reader, "headless", False):
        raise SessionExpiredError("会话失效：请关闭 headless 后在可见浏览器中完成登录或验证")
    try:
        driver = reader.open_session()
        driver.get(profile_url)
        reader.wait_for_document()
        if reader.has_login_gate():
            reader.wait_for_manual_login(profile_url)
        return True
    except (SessionExpiredError, ParseError):
        raise
    except Exception as exc:
        raise SessionExpiredError(f"无法建立浏览器会话：{exc}") from exc
    finally:
        reader.close()
