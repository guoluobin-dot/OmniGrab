"""Anchor runtime-writable locations for dev runs and frozen builds.

Dev mode keeps the historical project-relative layout (``config/``,
``downloads/``, ``logs/`` next to the working directory). Frozen builds
(PyInstaller) resolve writable storage beside the executable so the app stays
portable ("绿色版"); when that location is not writable (e.g. Program Files)
they fall back to the per-user application-data directory.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "OmniGrab"


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    return Path(sys.executable).resolve().parent


def user_data_root() -> Path:
    """Per-user application-data directory for the current platform."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / APP_DIR_NAME


def _ensure_writable(directory: Path) -> bool:
    probe = directory / f".write_probe_{os.getpid()}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def runtime_root() -> Path:
    if not is_frozen():
        # Historical behaviour: relative to the current working directory.
        return Path.cwd()
    base = executable_dir()
    if _ensure_writable(base):
        return base
    fallback = user_data_root()
    _ensure_writable(fallback)
    return fallback


def config_dir() -> Path:
    return runtime_root() / "config"


def downloads_dir() -> Path:
    return runtime_root() / "downloads"


def logs_dir() -> Path:
    return runtime_root() / "logs"
