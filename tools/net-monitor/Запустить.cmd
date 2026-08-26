@echo off
rem net-monitor — значок сети в трее. Ничего не устанавливает, чистый PowerShell.
start "" powershell -sta -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0net-monitor.ps1"
