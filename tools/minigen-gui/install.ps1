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
    # ⚠️ Arguments чистим ЯВНО: CreateShortcut загружает существующий ярлык,
    # и старые поля остаются. Так в ярлыке «МиниГен» остался адрес старой
    # панели (http://127.0.0.1:8099/) — мёртвый после архивирования панели.
    $link.Arguments = ""
    $link.WorkingDirectory = $PSScriptRoot
    $link.Description = "МиниГен — единая программа с графическим интерфейсом"
    $link.Save()
    Write-Host "Ярлык на рабочем столе: $shortcut" -ForegroundColor Green
} catch {
    Write-Host "Ярлык создать не удалось: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Ярлык старой панели (страница управления :8098) — в архив: страница
# больше не существует, а ярлык вёл бы в никуда.
$oldPanelShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "МиниГен — панель (управление).lnk"
if (Test-Path $oldPanelShortcut) {
    try {
        Move-Item $oldPanelShortcut (Join-Path "C:\Users\valstan\bin\_archive" "МиниГен — панель (управление).lnk") -Force
        Write-Host "Ярлык старой панели убран в архив" -ForegroundColor Yellow
    } catch {
        Write-Host "Ярлык старой панели не убран: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# ── автозапуск: демоны должны жить без окна ────────────────────────────────
# Старая панель с супервизором архивирована (23.11): её страницы :8099/:8098
# больше нет, а демонов (лечащий, сетевой сторож, сторож сессий) держит сама
# программа — в фоновом режиме моста, без окна и без окна консоли.
$startup = [Environment]::GetFolderPath("Startup")
$oldStartup = Join-Path $startup "МиниГен.lnk"
if (Test-Path $oldStartup) {
    try {
        $oldLink = (New-Object -ComObject WScript.Shell).CreateShortcut($oldStartup)
        if ($oldLink.Arguments -like "*mcode-panel-supervisor*") {
            Remove-Item $oldStartup -Force
            Write-Host "Старый автозапуск (супервизор старой панели) убран" -ForegroundColor Yellow
        }
    } catch {}
}
$pyw = ""
foreach ($candidate in @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"))) {
    if (Test-Path $candidate) { $pyw = $candidate; break }
}
if (-not $pyw) {
    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($cmd) { $pyw = $cmd.Source }
}
if ($pyw) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $autoLink = $shell.CreateShortcut((Join-Path $startup "МиниГен (демоны).lnk"))
        $autoLink.TargetPath = $pyw
        $autoLink.Arguments = '"' + (Join-Path $PSScriptRoot "bridge\minigen_bridge.py") + '" --daemon'
        $autoLink.WorkingDirectory = (Join-Path $PSScriptRoot "bridge")
        $autoLink.Description = "МиниГен — фоновый режим: сторожа аккаунтов, сети и сессий"
        $autoLink.Save()
        Write-Host "Автозапуск демонов: $(Join-Path $startup 'МиниГен (демоны).lnk')" -ForegroundColor Green
    } catch {
        Write-Host "Автозапуск не настроен: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "pythonw.exe не найден — автозапуск демонов не настроен." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Готово. Запуск: ярлык «МиниГен» на рабочем столе или «Запустить.cmd»." -ForegroundColor Green
