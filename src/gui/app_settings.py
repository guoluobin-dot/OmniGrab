"""Persisted, user-specific settings for the desktop application."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Union


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
SETTINGS_FILE = PROJECT_ROOT / "config" / "app_settings.json"

PathLike = Union[str, os.PathLike[str]]


def _normalise_directory(directory: PathLike) -> Path:
    """Return an absolute directory path without requiring it to exist first."""
    return Path(os.path.abspath(os.path.expanduser(os.fspath(directory))))


def load_download_dir(
    settings_file: PathLike = SETTINGS_FILE,
    default_dir: PathLike = DEFAULT_DOWNLOAD_DIR,
) -> str:
    """Load the saved directory, falling back safely when settings are unavailable."""
    settings_path = Path(settings_file)
    fallback = _normalise_directory(default_dir)
    selected = fallback

    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
        configured_dir = payload.get("download_dir") if isinstance(payload, dict) else None
        if isinstance(configured_dir, str) and configured_dir.strip():
            selected = _normalise_directory(configured_dir.strip())
    except (OSError, ValueError, TypeError):
        # A first launch, a manually removed file, or malformed settings should
        # never stop the application from opening.
        pass

    try:
        selected.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback.mkdir(parents=True, exist_ok=True)
        selected = fallback

    return str(selected)


def save_download_dir(
    download_dir: PathLike,
    settings_file: PathLike = SETTINGS_FILE,
) -> str:
    """Persist the directory atomically and return its normalised path."""
    selected = _normalise_directory(download_dir)
    selected.mkdir(parents=True, exist_ok=True)

    settings_path = Path(settings_file)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = settings_path.with_suffix(f"{settings_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps({"download_dir": str(selected)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(settings_path)
    return str(selected)
