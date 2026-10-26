@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Проверка окружения ===
py -3 ocvpn.py doctor
echo.
echo === Автозапуск при входе в систему ===
py -3 ocvpn.py install
echo.
echo Готово. Запуск вручную: Запустить.cmd
pause
