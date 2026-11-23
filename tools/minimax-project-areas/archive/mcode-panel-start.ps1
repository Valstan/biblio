# Запускает панель MiniMax Code по областям проектов (фоном, переживает закрытие терминала).
# Панель: http://127.0.0.1:8099 — живой статус, очки, «новый аккаунт», тихий ремонт.

$ErrorActionPreference = 'Continue'

$Port  = 8099
$Script = 'C:\Users\valstan\bin\mcode-panel.js'
$LogDir = "$env:LOCALAPPDATA\mcode-web"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Уже запущена?
$alive = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/state" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $alive = $true }
} catch { }

if ($alive) {
    Write-Host "Панель уже работает: http://127.0.0.1:$Port"
    Start-Process "http://127.0.0.1:$Port"
    exit 0
}

# Отдельно от терминала, чтобы панель жила дальше.
$cmd = "start /b node `"$Script`" $Port > `"$LogDir\panel.out.log`" 2>&1"
cmd.exe /c $cmd | Out-Null

Start-Sleep -Seconds 3
$ok = $false
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/state" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) { $ok = $true }
} catch { }

if ($ok) {
    Write-Host "Панель поднята: http://127.0.0.1:$Port" -ForegroundColor Green
    Start-Process "http://127.0.0.1:$Port"
} else {
    Write-Host "Панель не поднялась, лог: $LogDir\panel.out.log" -ForegroundColor Yellow
}
