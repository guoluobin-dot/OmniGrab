@echo off
chcp 65001 >nul
title 抖音内容下载工具
cd /d "C:\Users\Administrator\Documents\New project7.23\douyin-downloader"
"C:\Program Files\Python312\python.exe" main.py
if errorlevel 1 (
    echo.
    echo 程序启动出错！请检查 Python 和依赖是否已安装。
    echo 运行: pip install -r requirements.txt
    pause
)
