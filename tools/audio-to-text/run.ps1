# audio-to-text launcher: run the GUI from the local venv.
# ASCII-only (see install.ps1 note on PowerShell 5.1 / BOM).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) {
    # pythonw hides the console; fall back to python if absent.
    $py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path $py)) {
    Write-Host "venv not found. Run install.ps1 first." -ForegroundColor Red
    exit 1
}
& $py (Join-Path $PSScriptRoot "gui.py")
