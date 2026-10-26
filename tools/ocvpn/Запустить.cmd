@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю ocvpn (ядро + OpenCode + дашборд)...
py -3 ocvpn.py up
echo.
echo ocvpn остановлен.
pause
