# Останавливает изолированные экземпляры MiniMax Code по проектам.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File C:\Users\valstan\bin\mcode-project-stop.ps1 -Project gonba
#   powershell -ExecutionPolicy Bypass -File C:\Users\valstan\bin\mcode-project-stop.ps1            # все проекты
#
# Основной десктоп (без --user-data-dir проекта) не трогаем.

param(
    [string]$Project = ''
)

$ErrorActionPreference = 'SilentlyContinue'

$procs = Get-CimInstance Win32_Process -Filter "Name = 'MiniMax Code.exe'"

$targets = $procs | Where-Object {
    $cl = $_.CommandLine
    if (-not $cl) { return $false }
    if ($cl -notmatch '--user-data-dir=') { return $false }   # основной десктоп
    if ($Project -and $cl -notmatch ("p-" + [regex]::Escape($Project) + "(\\|`"|\s|$)")) { return $false }
    return $true
}

if (-not $targets) {
    Write-Host "Нечего останавливать (проект='$Project')"
    exit 0
}

$ids = @()
foreach ($t in $targets) {
    $ids += $t.ProcessId
    Write-Output "Стоп: pid=$($t.ProcessId)  $((($t.CommandLine -split '--app-user-model-id=')[1] -split ' ')[0])"
}

foreach ($id in ($ids | Sort-Object -Unique)) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}

Write-Host "Остановлено процессов: $(($ids | Sort-Object -Unique).Count)"
