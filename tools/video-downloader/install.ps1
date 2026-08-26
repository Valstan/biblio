# video-downloader installer (Windows / PowerShell). Idempotent: safe to re-run.
# ASCII-only messages (PowerShell 5.1 reads BOM-less .ps1 as ANSI codepage).
# Russian docs live in README.md.
#
#   Run:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Creates a local venv (.venv) and installs yt-dlp + imageio-ffmpeg
# (bundled ffmpeg binary, no system install). Nothing leaves the machine
# beyond downloading packages from PyPI.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venv = Join-Path $PSScriptRoot ".venv"
$py = Join-Path $venv "Scripts\python.exe"

function Find-Python {
    foreach ($c in @("py -3.12", "py -3.11", "python", "py -3")) {
        $parts = $c.Split(" ")
        try {
            & $parts[0] $parts[1..($parts.Length - 1)] --version | Out-Null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch {}
    }
    throw "Python 3 not found. Install Python from python.org and re-run."
}

if (-not (Test-Path $py)) {
    $python = Find-Python
    Write-Host "Creating venv ($python)..." -ForegroundColor Cyan
    $parts = $python.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv .venv
}

Write-Host "Upgrading pip..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip wheel | Out-Null

Write-Host "Installing yt-dlp + ffmpeg..." -ForegroundColor Cyan
& $py -m pip install --upgrade -r requirements.txt

Write-Host ""
Write-Host "Done. Run the tool:" -ForegroundColor Green
Write-Host "  - double-click the launcher .cmd file in this folder, or" -ForegroundColor Green
Write-Host "  - powershell -ExecutionPolicy Bypass -File run.ps1" -ForegroundColor Green
Write-Host ""
Write-Host "Tip: re-run this installer once a month - yt-dlp updates often" -ForegroundColor Yellow
Write-Host "as sites change their players." -ForegroundColor Yellow
