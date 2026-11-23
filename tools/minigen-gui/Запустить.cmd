@echo off
rem MiniGen launcher (ASCII only, no BOM - cmd reads it as-is).
rem Installs dependencies on first run, then starts the program window.
setlocal
cd /d "%~dp0"

if not exist "node_modules\electron\dist\electron.exe" (
  echo First run: installing dependencies, please wait...
  powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  if errorlevel 1 (
    echo.
    echo Install failed. See the message above.
    pause
    exit /b 1
  )
)

if not exist "out\main\index.js" (
  echo Building the program...
  call npm run build
  if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
  )
)

start "" "%~dp0node_modules\.bin\electron.cmd" "%~dp0"
