# Запускает изолированный экземпляр MiniMax Code для ОДНОГО проекта.
#
# Что даёт изоляцию (проверено D-168, G535/G538): оба параметра сразу —
#   --user-data-dir=<dataDir>\electron   (снимает single-instance lock)
#   MINIMAX_DATA_DIR / MAVIS_DATA_DIR    (свои auth, сессии, настройки)
# Одного из двух — недостаточно.
#
# Что даёт различимость в панели задач:
#   --app-user-model-id=MiniMaxCode.<slug>  (своя AUMID -> окна не группируются)
#   + переименование окна в "<Проект> — MiniMax Code" (mcode-project-title.ps1)
#
# Использование: powershell -ExecutionPolicy Bypass -File C:\Users\valstan\bin\mcode-project-start.ps1 -Project gonba

param(
    [Parameter(Mandatory = $true)][string]$Project
)

$ErrorActionPreference = 'Stop'

$ConfigFile = 'C:\Users\valstan\bin\mcode-project-areas.json'
$Exe        = 'C:\Users\valstan\AppData\Local\Programs\MiniMax Code\MiniMax Code.exe'
$AreasRoot  = "$env:USERPROFILE\.minimax-areas"

$cfg   = (Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json)
$proj  = $cfg.projects | Where-Object { $_.slug -eq $Project }
if (-not $proj) {
    Write-Host "Проект '$Project' не найден в $ConfigFile"
    Write-Host ("Доступные: " + (($cfg.projects.slug) -join ', '))
    exit 1
}
if (-not (Test-Path $Exe)) { Write-Host "Не найден MiniMax Code.exe"; exit 1 }
if (-not (Test-Path $proj.folder)) { Write-Host "Папка проекта не найдена: $($proj.folder)"; exit 1 }

$dataDir = Join-Path $AreasRoot ("p-" + $proj.slug)

# Каталог данных проекта: свои сессии, свои настройки.
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
}

# Авторизация: копируем токен из уже авторизованной области — иначе каждый
# новый проект требовал бы отдельного входа (device-flow, по одному за раз).
$authSrc = Join-Path $cfg.authTemplate 'auth\prod\en\mcode-public'
$authDst = Join-Path $dataDir 'auth\prod\en\mcode-public'
if (-not (Test-Path (Join-Path $authDst 'auth.json'))) {
    if (Test-Path $authSrc) {
        New-Item -ItemType Directory -Force -Path $authDst | Out-Null
        Copy-Item (Join-Path $authSrc '*') $authDst -Force -ErrorAction SilentlyContinue
        Write-Host "  авторизация скопирована из $($cfg.authTemplate)"
    } else {
        Write-Host "  ВНИМАНИЕ: шаблон авторизации не найден ($authSrc) — нужен вход в этой области"
    }
}

$env:MINIMAX_DATA_DIR = $dataDir
$env:MAVIS_DATA_DIR   = $dataDir

$userData = Join-Path $dataDir 'electron'
$aumid    = "MiniMaxCode.$($proj.slug)"
$winTitle = "$($proj.title) — MiniMax Code"

$argList = @(
    "--user-data-dir=`"$userData`"",
    "--app-user-model-id=$aumid",
    "`"$($proj.folder)`""
)

$p = Start-Process -FilePath $Exe -ArgumentList $argList -PassThru -WindowStyle Normal

# Переименование окна: приложение ставит заголовок "MiniMax Code" само,
# поэтому метку в панели задач ставим мы (Win32 SetWindowText).
$tagger = 'C:\Users\valstan\bin\mcode-project-title.ps1'
if (Test-Path $tagger) {
    # Заголовок содержит пробелы — Start-Process склеивает аргументы без кавычек,
    # поэтому кавычки проставляем явно (иначе фоновый теггер падает на разборе args).
    $taggerArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
        '-File', ('"{0}"' -f $tagger),
        '-TargetPid', $p.Id,
        '-Title', ('"{0}"' -f $winTitle)
    )
    Start-Process -FilePath 'powershell.exe' -ArgumentList $taggerArgs -WindowStyle Hidden | Out-Null
}

Write-Host "Запущен проект: $($proj.title)"
Write-Host "  папка проекта : $($proj.folder)"
Write-Host "  каталог данных: $dataDir"
Write-Host "  имя в панели  : $winTitle"
Write-Host "  pid           : $($p.Id)"
