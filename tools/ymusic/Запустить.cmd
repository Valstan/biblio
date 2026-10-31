@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo   venv не найден - ставлю зависимости (один раз).
    echo.
    powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
    if errorlevel 1 (
        echo   Не получилось поставить. Проверь, что Python 3.10+ есть в PATH.
        pause
        exit /b 1
    )
)

echo.
echo   ymusic - скачивание треков плейлиста Яндекс.Музыки
echo.
echo   1) Показать треклист плейлиста
echo   2) Скачать плейлист
echo   3) Перекачать в лучшем качестве
echo   4) Выйти
echo.

set "choice="
set /p choice="Выбор: "

if "%choice%"=="1" (
    set /p PL="Ссылка или UUID плейлиста: "
    "%PY%" "%~dp0ymusic.py" list "%PL%"
    pause
    goto :eof
)
if "%choice%"=="2" (
    set /p PL="Ссылка или UUID плейлиста: "
    set /p OUT="Папка для файлов: "
    "%PY%" "%~dp0ymusic.py" download "%PL%" -o "%OUT%"
    pause
    goto :eof
)
if "%choice%"=="3" (
    set /p PL="Ссылка или UUID плейлиста: "
    set /p OUT="Папка для файлов: "
    "%PY%" "%~dp0ymusic.py" upgrade "%PL%" -o "%OUT%"
    pause
    goto :eof
)

exit /b 0
