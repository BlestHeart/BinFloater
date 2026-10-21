@echo off
chcp 936 >nul 2>&1
title 二进制悬浮窗 启动器
cd /d "%~dp0"

echo ================================================
echo   二进制悬浮窗 · BinFloater
echo ================================================
echo.

rem ---- 1. 优先用打包好的 onedir 版本（不解包，最快最稳） ----
if exist "dist\BinFloater\BinFloater.exe" (
    echo [启动] 使用 dist\BinFloater\BinFloater.exe
    start "" "dist\BinFloater\BinFloater.exe"
    exit /b 0
)

rem ---- 2. 退回单文件版 ----
if exist "dist\BinFloater.exe" (
    echo [启动] 使用 dist\BinFloater.exe
    start "" "dist\BinFloater.exe"
    exit /b 0
)

rem ---- 3. 退回源码运行 ----
where python >nul 2>&1
if %errorlevel%==0 (
    echo [启动] 用 Python 源码运行
    start "" pythonw main.py
    exit /b 0
)

echo [错误] 找不到可运行的程序。
echo.
echo 请确认本文件夹内容完整，或安装 Python 3.8+ 后重试。
echo.
pause
exit /b 1
