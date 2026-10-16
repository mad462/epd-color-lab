@echo off
rem 起一个本地静态服务器，给 device.html 用。
rem 必须走 http://127.0.0.1（file:// 下 fetch 和 Web Serial 都会被浏览器禁掉）。
cd /d "%~dp0"
echo.
echo   打开 http://127.0.0.1:5174/device.html
echo   按 Ctrl+C 停止
echo.
python -m http.server 5174
