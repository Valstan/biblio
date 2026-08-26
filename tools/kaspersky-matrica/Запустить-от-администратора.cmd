@echo off
rem Same window, elevated - required by the "export/import baseline" buttons
rem (avp.com EXPORT / IMPORT needs administrator rights).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-sta','-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0kaspersky-matrica.ps1\"'"
