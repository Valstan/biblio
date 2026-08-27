@echo off
rem net-monitor installer launcher. Comments are ASCII on purpose: .cmd is read in
rem the OEM codepage, and Cyrillic here would show up as garbage on another machine.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem Refuse to run from the folder Explorer uses to preview an archive (same check
rem as in the run launcher): the installed shortcut itself would be fine, but the
rem source files would vanish when Windows wipes the temp folder mid-copy.
set "HERE=%~dp0"
set "BAD="
echo "!HERE!" | findstr /i /c:"\\Temp\\Temp1_" /c:"\\Temp\\Rar$" /c:"\\Temp\\7z" /c:"\\Temp\\wz" /c:"\\INetCache\\" >nul && set "BAD=1"
if /i "!HERE!"=="%TEMP%\" set "BAD=1"
if defined BAD (
    echo.
    echo  Looks like the archive was opened without unpacking.
    echo  Please unpack it first: right-click the .zip - "Extract All",
    echo  then run this file from the unpacked folder.
    echo.
    echo  Snachala raspakuyte arhiv: pravyy klik po .zip - "Izvlech vse".
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "param($p) Get-ChildItem -LiteralPath $p -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" -args "%~dp0" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
rem Keep the window open so the person can read the result (version, path).
pause
