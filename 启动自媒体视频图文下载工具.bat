@echo off
chcp 65001 >nul
title 自媒体视频图文下载工具 OmniGrab
:: SINGLE ENTRY POINT - double-click to run
:: Priority: .venv (latest, 极速启动) > dist exe > system python

cd /d "%~dp0"
set "ROOT=%~dp0"

:: 0) Single-file exe（推荐，双击即用，无弹窗）
if exist "%ROOT%自媒体视频图文下载工具.exe" (
    echo [Launch] Starting single-file exe...
    start "" "%ROOT%自媒体视频图文下载工具.exe"
    exit /b 0
)

:: 1) Project venv (latest code, 启动已优化)
if exist "%ROOT%.venv\Scripts\python.exe" (
    echo [Launch] Using project venv (极速启动)...
    "%ROOT%.venv\Scripts\python.exe" "%ROOT%main.py"
    goto :END
)
if exist "%ROOT%.venv\Scripts\pythonw.exe" (
    "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%main.py"
    goto :END
)

:: 2) Portable exe (fallback)
if exist "%ROOT%dist\OmniGrab\OmniGrab.exe" (
    echo [Launch] Found portable exe, starting...
    start "" "%ROOT%dist\OmniGrab\OmniGrab.exe"
    exit /b 0
)

:: 3) System python
where python >nul 2>&1
if %errorlevel%==0 (
    echo [Launch] Using system python...
    python "%ROOT%main.py"
    goto :END
)
where py >nul 2>&1
if %errorlevel%==0 (
    echo [Launch] Using py launcher...
    py -3 "%ROOT%main.py"
    goto :END
)

echo.
echo [Error] No Python found and no dist\OmniGrab\OmniGrab.exe
echo  Please either keep dist folder or install Python 3.10+ and run: pip install -r requirements.txt
echo.
pause
exit /b 1

:END
if errorlevel 1 (
    echo.
    echo [Tip] Launch failed, maybe missing dependencies.
    echo  Try: pip install -r requirements.txt
    pause
)
exit /b %errorlevel%