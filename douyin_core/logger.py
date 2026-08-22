"""Consistent logging for the shared package."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from .runtime_paths import is_frozen, logs_dir


def get_logger(name: str, log_dir: str | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
    logger.addHandler(stream)
    # Frozen builds get a crash/diagnosis log by default; dev runs stay quiet on disk.
    if log_dir is None and is_frozen():
        try:
            log_dir = str(logs_dir())
        except OSError:
            log_dir = None
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = logging.FileHandler(os.path.join(log_dir, f"douyin_{datetime.now():%Y%m%d}.log"), encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logger.addHandler(file_handler)
        except OSError:
            pass
    return logger
