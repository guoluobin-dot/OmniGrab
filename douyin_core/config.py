"""Configuration shared by desktop and service callers."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class CoreConfig:
    # A visible, persistent browser is the safe default: headless sessions may be rejected.
    headless: bool = False
    browser_profile_dir: str = field(default_factory=lambda: os.path.abspath(os.path.join("config", "browser_profile")))
    browser_pool_size: int = 2
    max_concurrency: int = 2
    memory_restart_mb: int = 1200
    download_dir: str = "downloads"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
