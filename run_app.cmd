@echo off
setlocal
set "PROJECT_DIR=C:\Users\Administrator\Documents\New project7.23\douyin-downloader"
set "PYTHON_EXE=C:\Program Files\Python312\python.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

"%PYTHON_EXE%" "%PROJECT_DIR%\main.py" 1>"%LOG_DIR%\startup_stdout.log" 2>"%LOG_DIR%\startup_stderr.log"
>"%LOG_DIR%\startup_exit_code.txt" echo %ERRORLEVEL%
