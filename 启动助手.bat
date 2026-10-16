@echo off
chcp 65001 >nul
title 传图台助手
cd /d "%~dp0host"

echo ============================================================
echo   48色墨水屏 · 传图台助手
echo ============================================================
echo.

rem ---- 找 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [x] 没找到 python。
    echo     去 https://www.python.org/downloads/ 装一个，
    echo     安装时记得勾上 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

rem ---- 检查 pyserial ----
python -c "import serial" >nul 2>nul
if errorlevel 1 (
    echo [ ] 缺少 pyserial，正在自动安装...
    python -m pip install pyserial
    if errorlevel 1 (
        echo [x] 装不上。手动跑一下：python -m pip install pyserial
        pause
        exit /b 1
    )
    echo [√] pyserial 装好了
)

echo [√] 依赖齐了
echo.
echo 串口默认用 COM6。要换的话先执行：
echo     set EPD_PORT=COM7
echo.

rem ---- 开浏览器 ----
start "" http://127.0.0.1:5174/device.html

echo 助手正在运行。这个窗口别关。
echo 浏览器里右上角点「配网」填 WiFi。
echo.
echo 关掉这个窗口 = 停止助手。
echo ------------------------------------------------------------
echo.

python server.py

echo.
echo 助手已停止。
pause
