# Создаёт на рабочем столе ярлык изолированного MiniMax Code для каждого проекта.
#
# Имя ярлыка: "<Проект> — MiniMax.lnk" -> в панели задач видно, какой проект открыт.
# Старые ярлыки областей (МОЗГ/КАРМАН/САРАФАН/Казанская Гоньба/Матрица РМЗ) уезжают
# в папку "MiniMax — старые ярлыки": часть из них открывала НЕ тот проект.
#
# Пересоздать: powershell -ExecutionPolicy Bypass -File C:\Users\valstan\bin\make-mcode-project-shortcuts.ps1

$ErrorActionPreference = 'Stop'

$Desktop   = [Environment]::GetFolderPath('Desktop')
$Config    = 'C:\Users\valstan\bin\mcode-project-areas.json'
$PS        = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$Icon      = 'C:\Users\valstan\AppData\Local\Programs\MiniMax Code\MiniMax Code.exe,0'
$Starter   = 'C:\Users\valstan\bin\mcode-project-start.ps1'
$Stopper   = 'C:\Users\valstan\bin\mcode-project-stop.ps1'
$shell     = New-Object -ComObject WScript.Shell

$cfg = (Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json)

# --- 1. Старые ярлыки -> в папку (не удаляем: их можно вернуть) ---
$stale = @('МОЗГ.lnk','КАРМАН.lnk','САРАФАН.lnk','Казанская Гоньба.lnk','Матрица РМЗ.lnk','MatricaRMZ.lnk')
$backup = Join-Path $Desktop 'MiniMax — старые ярлыки'
$moved = 0
foreach ($s in $stale) {
    $from = Join-Path $Desktop $s
    if (Test-Path $from) {
        if (-not (Test-Path $backup)) { New-Item -ItemType Directory -Force -Path $backup | Out-Null }
        Move-Item -LiteralPath $from -Destination (Join-Path $backup $s) -Force
        Write-Output "Убран в архив: $s"
        $moved++
    }
}

# --- 2. Ярлык на каждый проект ---
foreach ($p in $cfg.projects) {
    $lnkPath = Join-Path $Desktop ("{0} — MiniMax.lnk" -f $p.title)
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath       = $PS
    $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Starter`" -Project $($p.slug)"
    $lnk.WorkingDirectory = $p.folder
    $lnk.IconLocation     = $Icon
    $lnk.Description      = "MiniMax Code — проект $($p.title). Свои сессии, своя рабочая папка, в панели задач видно имя проекта"
    $lnk.WindowStyle      = 7
    $lnk.Save()
    Write-Output "Создан: $($p.title) — MiniMax.lnk  ->  $($p.folder)"

    # Стоп этого проекта — отдельным ярлыком в той же папке (чистка рабочего стола)
    $stopPath = Join-Path $Desktop ("{0} — MiniMax (стоп).lnk" -f $p.title)
    $sl = $shell.CreateShortcut($stopPath)
    $sl.TargetPath       = $PS
    $sl.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Stopper`" -Project $($p.slug)"
    $sl.WorkingDirectory = $p.folder
    $sl.IconLocation     = $Icon
    $sl.Description      = "Остановить MiniMax Code по проекту $($p.title)"
    $sl.WindowStyle      = 7
    $sl.Save()
}

# --- 3. Общий стоп ---
$stopAll = Join-Path $Desktop 'MiniMax — стоп все проекты.lnk'
$sa = $shell.CreateShortcut($stopAll)
$sa.TargetPath       = $PS
$sa.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Stopper`""
$sa.WorkingDirectory = 'D:\valstan\REPO'
$sa.IconLocation     = $Icon
$sa.Description      = 'Остановить все изолированные экземпляры MiniMax Code по проектам'
$sa.WindowStyle      = 7
$sa.Save()

Write-Output ""
Write-Output ("Ярлыков создано: {0} (+ стопы) · старых убрано в архив: {1}" -f $cfg.projects.Count, $moved)
Write-Output ("Папка старых ярлыков: {0}" -f $backup)
