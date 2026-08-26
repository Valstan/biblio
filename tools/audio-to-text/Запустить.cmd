@echo off
rem Double-click to launch the audio-to-text GUI from the local venv.
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0run.ps1"
