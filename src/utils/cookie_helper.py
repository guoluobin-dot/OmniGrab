"""
Cookie 管理模块
================
帮助用户配置和管理抖音网页版 Cookie。
Cookie 是访问抖音 API 的关键认证凭据。
"""

import os
import json
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Cookie 配置文件路径
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "config", "cookie.json")


def save_cookie(cookie: str) -> bool:
    """
    保存 Cookie 到配置文件

    Args:
        cookie: 抖音网页版 cookie 字符串

    Returns:
        保存是否成功
    """
    try:
        config_dir = os.path.dirname(COOKIE_FILE)
        os.makedirs(config_dir, exist_ok=True)

        data = {"cookie": cookie}
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("Cookie 已保存到配置文件")
        return True
    except Exception as e:
        logger.error(f"保存 Cookie 失败: {e}")
        return False


def load_cookie() -> Optional[str]:
    """
    从配置文件加载 Cookie

    Returns:
        Cookie 字符串，加载失败返回 None
    """
    try:
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cookie = data.get("cookie", "")
                if cookie:
                    logger.info("已从配置文件加载 Cookie")
                    return cookie
        return None
    except Exception as e:
        logger.error(f"加载 Cookie 失败: {e}")
        return None


def get_cookie_guide() -> str:
    """
    获取 Cookie 获取指南

    Returns:
        获取 Cookie 的步骤说明
    """
    return """
获取抖音 Cookie 的步骤：
========================

1. 打开浏览器（推荐 Chrome），访问 https://www.douyin.com
2. 登录你的抖音账号
3. 按 F12 打开开发者工具
4. 切换到 "Network"（网络）选项卡
5. 刷新页面
6. 在请求列表中找到任意一个请求（如 www.douyin.com 的请求）
7. 在请求头中找到 "Cookie" 字段
8. 复制完整的 Cookie 值
9. 粘贴到程序的 Cookie 输入框中

注意：
- Cookie 中必须包含 ttwid 和 msToken 字段
- Cookie 有效期通常为数天到数周，过期后需要重新获取
- 不要泄露你的 Cookie 给他人
"""


def validate_cookie(cookie: str) -> bool:
    """
    验证 Cookie 是否包含必要字段

    Args:
        cookie: Cookie 字符串

    Returns:
        是否包含必要字段
    """
    if not cookie:
        return False

    # 检查必要字段
    has_ttwid = "ttwid" in cookie
    has_mstoken = "msToken" in cookie

    if not has_ttwid:
        logger.warning("Cookie 中缺少 ttwid 字段")
    if not has_mstoken:
        logger.warning("Cookie 中缺少 msToken 字段")

    return has_ttwid or has_mstoken
