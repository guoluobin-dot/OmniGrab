"""runtime_paths 在打包（frozen）与开发模式下的路径锚定测试。"""
import sys
from pathlib import Path

from douyin_core import runtime_paths


def test_dev_mode_keeps_project_relative_layout(monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: False)
    assert runtime_paths.runtime_root() == Path.cwd()
    assert runtime_paths.config_dir() == Path.cwd() / "config"
    assert runtime_paths.downloads_dir() == Path.cwd() / "downloads"
    assert runtime_paths.logs_dir() == Path.cwd() / "logs"


def test_is_frozen_defaults_to_false() -> None:
    assert runtime_paths.is_frozen() is bool(getattr(sys, "frozen", False))


def test_frozen_build_writes_beside_the_executable(tmp_path: Path, monkeypatch) -> None:
    exe_dir = tmp_path / "app-portable"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "OmniGrab.exe"))

    assert runtime_paths.runtime_root() == exe_dir
    assert runtime_paths.config_dir() == exe_dir / "config"


def test_frozen_build_falls_back_to_user_data_when_exe_dir_unreadable(
    tmp_path: Path, monkeypatch
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(locked / "OmniGrab.exe"))
    monkeypatch.setattr(
        runtime_paths,
        "_ensure_writable",
        lambda directory: False if directory == locked else True,
    )
    monkeypatch.setattr(runtime_paths, "user_data_root", lambda: tmp_path / "userdata")

    assert runtime_paths.runtime_root() == tmp_path / "userdata"


def test_user_data_root_uses_platform_convention(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(Path.home()))
    assert runtime_paths.user_data_root() == Path.home() / "OmniGrab"
