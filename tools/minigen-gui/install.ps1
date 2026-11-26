# МиниГен — установщик (Windows / PowerShell).
# Идемпотентен: можно запускать повторно (повторный запуск = обновление).
# Файл сохранён в UTF-8 С BOM: PowerShell 5.1 читает .ps1 без BOM как ANSI,
# и кириллица ломает парсер.
#
#   Запуск:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Что делает:
#   1) проверяет Node.js (нужен для сборки окна) и Python (нужен мосту);
#   2) ставит зависимости и собирает программу;
#   3) УСТАНАВЛИВАЕТ программу в своё место: %LOCALAPPDATA%\Programs\МиниГен
#      — и только потом делает ярлыки (рабочий стол + автозапуск демонов).
#
# ⚠️ Почему программа ставится отдельно от репозитория (23.11): если она
# живёт в рабочем дереве, её версия зависит от того, какая ветка там
# checkout. Любой `git checkout` в репозитории (у нас это делают агенты)
# молча меняет программу, которая запущена у владельца. Своё место — версия
# определена, и репозиторий её не трогает.
#
# Исходники остаются в репозитории: программа их знает (настройка
# `codeRepo`) и может править их через PR — но запускается не оттуда.

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

function Find-Pythonw {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"))
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return ""
}

function Write-AsciiFile([string]$Path, [string]$Text) {
    # .cmd читается cmd'ом как ANSI: без BOM и без не-ASCII.
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.Encoding]::ASCII)
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

# ── установка в своё место ──────────────────────────────────────────────────
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\МиниГен"
$appDir = Join-Path $installRoot "app"
$electronDir = Join-Path $installRoot "electron"

Write-Host "Устанавливаю в $installRoot ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $appDir | Out-Null

# Рантайм Electron (тот же бинарь, что в node_modules — копия, не ссылка).
if (Test-Path (Join-Path $PSScriptRoot "node_modules\electron\dist")) {
    New-Item -ItemType Directory -Force -Path $electronDir | Out-Null
    & robocopy (Join-Path $PSScriptRoot "node_modules\electron\dist") `
              (Join-Path $electronDir "dist") /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy (electron) не отработал (код $LASTEXITCODE)" }
    Copy-Item (Join-Path $PSScriptRoot "node_modules\electron\package.json") `
              (Join-Path $electronDir "package.json") -Force
    Copy-Item (Join-Path $PSScriptRoot "node_modules\electron\path.txt") `
              (Join-Path $electronDir "path.txt") -Force -ErrorAction SilentlyContinue
}

# Собранное окно, мост и база знаний — в app/.
foreach ($pair in @(@("out", "out"), @("bridge", "bridge"))) {
    $src = Join-Path $PSScriptRoot $pair[0]
    $dst = Join-Path $appDir $pair[1]
    if (Test-Path $src) {
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        & robocopy $src $dst /MIR /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy ($($pair[0])) не отработал (код $LASTEXITCODE)" }
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $appDir "docs") | Out-Null
Copy-Item (Join-Path $PSScriptRoot "docs\knowledge-pack.md") `
          (Join-Path $appDir "docs\knowledge-pack.md") -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $PSScriptRoot "package.json") (Join-Path $appDir "package.json") -Force

# Запускалки в установленном месте (ASCII, без BOM — их читает cmd).
Write-AsciiFile (Join-Path $installRoot "Запустить.cmd") @"
@echo off
rem MiniGen launcher (installed copy, ASCII only).
setlocal
cd /d "%~dp0"
if not exist "%~dp0electron\dist\electron.exe" (
  echo Electron runtime not found - re-run install.ps1 in the repository.
  pause
  exit /b 1
)
start "" "%~dp0electron\dist\electron.exe" "%~dp0app"
"@
Write-AsciiFile (Join-Path $installRoot "Автозапуск.cmd") @"
@echo off
rem MiniGen background mode: the daemons (healer, network watcher, session
rem watcher) are kept alive without a window. ASCII only.
setlocal
cd /d "%~dp0"
set "PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw.exe"
start "" "%PYTHONW%" "%~dp0app\bridge\minigen_bridge.py" --daemon
"@

# Ярлык на рабочем столе — в установленное место.
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "МиниГен.lnk"
try {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = (Join-Path $installRoot "Запустить.cmd")
    # ⚠️ Arguments чистим ЯВНО: CreateShortcut загружает существующий ярлык
    # и старые поля остаются (так в нём остался адрес старой панели).
    $link.Arguments = ""
    $link.WorkingDirectory = $installRoot
    $link.Description = "МиниГен — единая программа с графическим интерфейсом"
    $link.Save()
    Write-Host "Ярлык на рабочем столе: $shortcut" -ForegroundColor Green
} catch {
    Write-Host "Ярлык создать не удалось: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Ярлык старой панели (страница управления :8098) — в архив: страницы больше нет.
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
$pyw = Find-Pythonw
if ($pyw) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $autoLink = $shell.CreateShortcut((Join-Path $startup "МиниГен (демоны).lnk"))
        $autoLink.TargetPath = $pyw
        $autoLink.Arguments = '"' + (Join-Path $appDir "bridge\minigen_bridge.py") + '" --daemon'
        $autoLink.WorkingDirectory = (Join-Path $appDir "bridge")
        $autoLink.Description = "МиниГен — фоновый режим: сторожа аккаунтов, сети и сессий"
        $autoLink.Save()
        Write-Host "Автозапуск демонов: $(Join-Path $startup 'МиниГен (демоны).lnk')" -ForegroundColor Green
    } catch {
        Write-Host "Автозапуск не настроен: $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "pythonw.exe не найден — автозапуск демонов не настроен." -ForegroundColor Yellow
}

# ── где исходники: программа правит их через PR ────────────────────────────
# Исходники остаются в репозитории, программа запускается отдельно.
$settingsPath = Join-Path $env:LOCALAPPDATA "minigen-gui\settings.json"
$sourceRepo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
New-Item -ItemType Directory -Force -Path (Split-Path $settingsPath) | Out-Null
$settings = @{}
if (Test-Path $settingsPath) {
    try {
        $settings = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch { $settings = @{} }
}
if (-not $settings.PSObject.Properties["codeRepo"] -or -not $settings.codeRepo) {
    # Ключа не было (или пустой) — дописываем, остальное не трогаем.
    if ($settings -isnot [PSCustomObject]) { $settings = [PSCustomObject]@{} }
    Add-Member -InputObject $settings -NotePropertyName "codeRepo" `
               -NotePropertyValue $sourceRepo -Force
    $json = $settings | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Настройки: путь исходников = $sourceRepo" -ForegroundColor Green
} else {
    Write-Host "Настройки уже есть, путь исходников: $($settings.codeRepo)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Готово. Программа установлена: $installRoot" -ForegroundColor Green
Write-Host "Запуск: ярлык «МиниГен» на рабочем столе." -ForegroundColor Green
Write-Host "Обновление: после git pull — снова запустите install.ps1." -ForegroundColor Yellow

# ── первый запуск: копия спросит про свою папку (промт #131) ───────────────
# ⚠️ Вопрос задаёт **окно** при первом открытии, а не установщик: программу
# переносят на другую машину, и установщик там был не этот. Установщик лишь
# говорит владельцу, что вопрос будет, — чтобы он не искал его потом.
# Отказ не ломает программу: копия работает локально.
Write-Host ""
Write-Host "При первом запуске программа спросит: регистрировать ли папку этой" -ForegroundColor Cyan
Write-Host "копии в репозитории программы (Valstan/biblio). Отказ не ломает" -ForegroundColor Cyan
Write-Host "программу — копия будет работать локально." -ForegroundColor Cyan
