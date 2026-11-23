# МиниГен — установщик (Windows / PowerShell).
# Идемпотентен: можно запускать повторно (повторный запуск = обновление).
# Файл сохранён в UTF-8 С BOM: PowerShell 5.1 читает .ps1 без BOM как ANSI,
# и кириллица ломает парсер.
#
#   Запуск:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Что делает:
#   1) проверяет Node.js (нужен для сборки окна);
#   2) ставит зависимости (node_modules) и собирает программу;
#   3) кладёт ярлык «МиниГен» на рабочий стол.
#
# Python нужен самой программе (мост «МиниГена» на Python) — проверяется тут
# же: без него окно откроется, но останется без состояния.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Node {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) { return $node.Source }
    throw "Node.js не найден. Установите Node.js 18+ и запустите установщик снова."
}

function Find-Python {
    foreach ($candidate in @("python", "py -3")) {
        $parts = $candidate.Split(" ")
        try {
            & $parts[0] $parts[1..($parts.Length - 1)] --version | Out-Null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch {}
    }
    return ""
}

$node = Find-Node
Write-Host "Node.js: $node" -ForegroundColor Cyan

$python = Find-Python
if ($python) {
    Write-Host "Python: $python (мост будет работать)" -ForegroundColor Cyan
} else {
    Write-Host "Python не найден — окно откроется, но без состояния областей." -ForegroundColor Yellow
    Write-Host "Установите Python 3.10+ и запустите установщик снова." -ForegroundColor Yellow
}

Write-Host "Ставлю зависимости (это надолго, только при первом запуске)..." -ForegroundColor Cyan
& npm install --no-audit --fund=false
if ($LASTEXITCODE -ne 0) { throw "npm install не отработал (код $LASTEXITCODE)" }

Write-Host "Собираю программу..." -ForegroundColor Cyan
& npm run build
if ($LASTEXITCODE -ne 0) { throw "сборка не отработала (код $LASTEXITCODE)" }

$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "МиниГен.lnk"
$launcher = Join-Path $PSScriptRoot "Запустить.cmd"
try {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = $launcher
    $link.WorkingDirectory = $PSScriptRoot
    $link.Description = "МиниГен — единая программа с графическим интерфейсом"
    $link.Save()
    Write-Host "Ярлык на рабочем столе: $shortcut" -ForegroundColor Green
} catch {
    Write-Host "Ярлык создать не удалось: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Готово. Запуск: ярлык «МиниГен» на рабочем столе или «Запустить.cmd»." -ForegroundColor Green
