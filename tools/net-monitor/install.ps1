# install.ps1 — установка/обновление net-monitor на этом компьютере.
#
# Зачем установка, если скрипт и так работает из любой папки: чтобы не путаться в
# версиях. Правило простое: из репозитория/архива утилиту не запускают — её ставят.
# На компьютере живёт РОВНО ОДНА копия (папка ниже), установка новой версии сама
# закрывает работающую старую и полностью замещает её файлы. Какая версия крутится —
# видно в первой строке меню трея.
#
# Запуск: Установить.cmd (или powershell -ep bypass -file install.ps1)
# Права администратора не нужны: всё в профиле пользователя.

$ErrorActionPreference = 'Stop'
# Без этого кириллица сообщений превращается в кашу в OEM-кодировке консоли.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$AppName = 'BrainNetMonitor'
$src = $PSScriptRoot
$dst = Join-Path $env:LOCALAPPDATA 'Programs\net-monitor'

function Get-VersionFrom([string]$scriptPath) {
    # Версия читается из константы главного скрипта — единственного места, где она
    # задаётся. Регулярка, а не dot-source: выполнять чужой файл ради константы нельзя.
    if (-not (Test-Path $scriptPath)) { return $null }
    $m = [regex]::Match([System.IO.File]::ReadAllText($scriptPath, [System.Text.Encoding]::UTF8),
                        "(?m)^\`$AppVersion\s*=\s*'([^']+)'")
    if ($m.Success) { $m.Groups[1].Value } else { $null }
}

$newVer = Get-VersionFrom (Join-Path $src 'net-monitor.ps1')
if (-not $newVer) { throw "Рядом с установщиком нет net-monitor.ps1 с версией — установка невозможна ($src)." }
$oldVer = Get-VersionFrom (Join-Path $dst 'net-monitor.ps1')

if ((Resolve-Path $src).Path -eq $dst -or (Test-Path $dst) -and ((Resolve-Path $dst).Path -eq (Resolve-Path $src).Path)) {
    throw 'Установщик запущен из установленной копии — ставить её саму в себя нет смысла. Запустите его из распакованного архива или репозитория.'
}

Write-Host ''
Write-Host "net-monitor: установка v$newVer" -NoNewline
if ($oldVer) { Write-Host "  (заменит установленную v$oldVer)" } else { Write-Host '  (первая установка)' }

# --- 1. Закрыть работающий экземпляр (какой бы версии и из какой бы папки он ни шёл) ---
$running = Get-CimInstance Win32_Process -Filter "Name like 'powershell%'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -match 'net-monitor\.ps1' }
foreach ($p in @($running)) {
    Write-Host "  закрываю работающий монитор (PID $($p.ProcessId))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($running) { Start-Sleep -Seconds 1 }   # дать мьютексу и значку в трее освободиться

# --- 2. Полностью заместить файлы (чтобы от старой версии не осталось хвостов) ---
# Настройки и лог живут отдельно (%LOCALAPPDATA%\net-monitor) и при обновлении
# сохраняются — сносится только папка программы.
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
New-Item -ItemType Directory -Path $dst -Force | Out-Null

Copy-Item (Join-Path $src 'net-monitor.ps1') $dst
if (Test-Path (Join-Path $src 'lib')) { Copy-Item (Join-Path $src 'lib') $dst -Recurse }
foreach ($f in @('Запустить.cmd', 'README.md', 'ДЛЯ-ПОЛУЧАТЕЛЯ.txt')) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p $dst }
}
Get-ChildItem $dst -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

# --- 3. Автозапуск и ярлык — всегда на УСТАНОВЛЕННУЮ копию ---
# Это ключ против путаницы версий: даже если автозапуск раньше указывал на репозиторий
# или на старую папку, отсюда он начинает указывать на установленную копию.
$cmd = Join-Path $dst 'Запустить.cmd'
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $AppName -Value "`"$cmd`""

$lnkDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
try {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut((Join-Path $lnkDir 'net-monitor.lnk'))
    $lnk.TargetPath = $cmd
    $lnk.WorkingDirectory = $dst
    $lnk.Description = "net-monitor v$newVer — монитор сети в трее"
    $lnk.Save()
} catch { Write-Host '  (ярлык в меню «Пуск» создать не удалось — не страшно)' }

# --- 4. Запустить установленную копию ---
Start-Process $cmd -WorkingDirectory $dst

Write-Host ''
Write-Host "Готово: v$newVer установлена в $dst и запущена."
Write-Host 'Автозапуск при входе в Windows включён; проверить версию можно в первой строке меню трея.'
Write-Host ''
