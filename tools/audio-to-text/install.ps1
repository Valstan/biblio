# audio-to-text installer (Windows / PowerShell). Idempotent: safe to re-run.
# Messages are ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1
# as the system ANSI codepage (often cp1251), which mangles non-ASCII text.
# Russian docs live in README.md.
#
#   Run:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Creates a local venv (.venv), installs CPU torch + GigaAM + ffmpeg
# (via imageio-ffmpeg, no system install). Nothing leaves the machine beyond
# downloading packages from PyPI and the GigaAM model on first transcription.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venv = Join-Path $PSScriptRoot ".venv"
$py = Join-Path $venv "Scripts\python.exe"

function Find-Python {
    # Prefer torch-compatible CPython (3.10-3.12), then any python.
    foreach ($c in @("py -3.12", "py -3.11", "py -3.10", "python", "py -3")) {
        $parts = $c.Split(" ")
        try {
            & $parts[0] $parts[1..($parts.Length - 1)] --version | Out-Null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch {}
    }
    throw "Python 3.10-3.12 not found. Install Python from python.org and re-run."
}

if (-not (Test-Path $py)) {
    $python = Find-Python
    Write-Host "Creating venv ($python)..." -ForegroundColor Cyan
    $parts = $python.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv .venv
}

Write-Host "Upgrading pip..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip wheel | Out-Null

Write-Host "Installing PyTorch (CPU build, GigaAM needs <=2.5.1)..." -ForegroundColor Cyan
& $py -m pip install "torch<=2.5.1" --index-url https://download.pytorch.org/whl/cpu

Write-Host "Installing GigaAM, soundfile, ffmpeg, drag-and-drop..." -ForegroundColor Cyan
& $py -m pip install -r requirements.txt

Write-Host ""
Write-Host "Done. Run the tool:" -ForegroundColor Green
Write-Host "  - double-click the launcher .cmd file in this folder, or" -ForegroundColor Green
Write-Host "  - powershell -ExecutionPolicy Bypass -File run.ps1" -ForegroundColor Green
Write-Host ""
Write-Host "First transcription downloads the GigaAM model (~0.5 GB) once." -ForegroundColor Yellow
