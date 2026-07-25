"""
日志模块
========
提供统一的日志记录功能，支持控制台和文件输出。
"""

import os
import logging
from datetime import datetime


def get_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    """
    获取一个配置好的 logger

    Args:
        name: logger 名称
        log_dir: 日志目录

    Returns:
        配置好的 Logger 对象
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # 文件输出
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(
            log_dir, f"douyin_{datetime.now().strftime('%Y%m%d')}.log"
        )
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
    except Exception:
        pass  # 文件日志失败不影响运行

    return logger
