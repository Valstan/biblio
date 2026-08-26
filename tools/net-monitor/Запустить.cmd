@echo off
rem net-monitor - network status in the tray. Installs nothing, plain PowerShell.
rem Comments are ASCII on purpose: .cmd is read in the OEM codepage, and Cyrillic
rem here would show up as garbage in Notepad on another machine.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem Refuse to run from the folder Explorer uses to preview an archive: the utility
rem would work, but "start with Windows" would then save a path Windows later wipes.
rem Only the real preview patterns are checked - a normal folder that merely has
rem "temp" in its name must not be blocked.
set "HERE=%~dp0"
set "BAD="
echo "!HERE!" | findstr /i /c:"\\Temp\\Temp1_" /c:"\\Temp\\Rar$" /c:"\\Temp\\7z" /c:"\\Temp\\wz" /c:"\\INetCache\\" >nul && set "BAD=1"
if /i "!HERE!"=="%TEMP%\" set "BAD=1"
if defined BAD (
    echo.
    echo  Looks like the archive was opened without unpacking:
    echo  !HERE!
    echo.
    echo  Please unpack it first: right-click the .zip - "Extract All",
    echo  for example into C:\Utils\net-monitor, then run this file from there.
    echo.
    echo  Snachala raspakuyte arhiv: pravyy klik po .zip - "Izvlech vse".
    echo.
    pause
    exit /b 1
)

rem Files that came from the internet are marked as blocked; clear the mark so
rem PowerShell does not ask about each file. The path is passed as an argument,
rem so quotes and apostrophes in it cause no trouble.
powershell -NoProfile -ExecutionPolicy Bypass -Command "param($p) Get-ChildItem -LiteralPath $p -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" -args "%~dp0" >nul 2>&1

start "" powershell -sta -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0net-monitor.ps1"