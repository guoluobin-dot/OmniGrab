"""Tests for persistent desktop download-directory settings."""

from __future__ import annotations

import json
from pathlib import Path

from src.gui.app_settings import load_download_dir, save_download_dir


def test_saved_download_directory_is_loaded_on_next_start(tmp_path: Path) -> None:
    settings_file = tmp_path / "config" / "app_settings.json"
    selected = tmp_path / "chosen-downloads"

    saved = save_download_dir(selected, settings_file)

    assert Path(saved) == selected
    assert Path(load_download_dir(settings_file, tmp_path / "default-downloads")) == selected
    assert json.loads(settings_file.read_text(encoding="utf-8")) == {
        "download_dir": str(selected)
    }


def test_missing_or_invalid_settings_fall_back_to_default_directory(tmp_path: Path) -> None:
    settings_file = tmp_path / "config" / "app_settings.json"
    default_dir = tmp_path / "default-downloads"

    assert Path(load_download_dir(settings_file, default_dir)) == default_dir
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("not valid json", encoding="utf-8")

    assert Path(load_download_dir(settings_file, default_dir)) == default_dir
    assert default_dir.is_dir()
