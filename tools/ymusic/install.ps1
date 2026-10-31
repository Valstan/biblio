$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $here '.venv'

if (-not (Test-Path $venv)) {
    Write-Host 'Создаю venv…' -ForegroundColor Cyan
    python -m venv $venv
}

$python = Join-Path $venv 'Scripts\python.exe'
Write-Host 'Ставлю зависимости…' -ForegroundColor Cyan
& $python -m pip install --quiet --disable-pip-version-check --upgrade pip
& $python -m pip install --quiet --disable-pip-version-check yandex-music mutagen cryptography

if ($LASTEXITCODE -ne 0) {
    Write-Host 'Установка не удалась' -ForegroundColor Red
    exit 1
}

Write-Host 'Готово. Запуск: .\Запустить.cmd' -ForegroundColor Green
