# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：免安装便携版（onedir + 无控制台窗口）。

构建产物位于 dist/OmniGrab/，整体压缩后即可分发。
"""
from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("douyin_core")
    + collect_submodules("selenium")
    + ["webdriver_manager", "qrcode"]
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OmniGrab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="OmniGrab",
)
