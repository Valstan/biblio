# vpn-on.ps1 — «VPN — включить» (Happ).
#
# Что делает (заказ владельца 17.11):
#   1. Поднимает Happ (служба + окно) и ждёт прокси 10809.
#   2. Включает системный прокси Windows на 127.0.0.1:10809.
#   3. Прокладывает маршрут для RustDesk: 92.51.22.114 -> 192.168.1.1,
#      чтобы RustDesk шёл мимо туннеля (иначе P2P не собирается, сессии рвутся).
#   4. Ставит переменные прокси для «МиниГена» на прокси Happ.
#   5. Перезапускает контур «МиниГен», чтобы процессы унаследовали новые переменные.
#   6. Проверяет, что MiniMax отвечает.
#
# Нужны права администратора (маршрут и служба) — скрипт сам поднимает права.
# Ярлык на рабочем столе: «VPN — включить (Happ)».

param([switch]$NoElevate, [switch]$Quiet)

. (Join-Path $PSScriptRoot 'vpn-lib.ps1')

if (-not $NoElevate -and -not (Test-Admin)) {
    Start-Process -FilePath $PS_EXE -Verb RunAs -WindowStyle Hidden -ArgumentList `
        "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 0
}

Write-Log '=== VPN: включаю Happ ==='

$ok = $true

# 1. Happ
if (-not (Ensure-Happ)) { $ok = $false }

# 2. Системный прокси
Set-SystemProxy $true

# 3. Маршрут RustDesk (мимо туннеля)
if (-not (Add-RustDeskRoute)) { $ok = $false }

# 4. Прокси для «МиниГена» — на Happ
Set-ProxyEnv $HAPP_HTTP $HAPP_SOCKS

# 5. Перезапуск контура, чтобы процессы унаследовали переменные
if (-not (Restart-MiniGen)) { $ok = $false }

# 6. Проба MiniMax
if (Test-Api $HAPP_HTTP) {
    Write-Log 'итог: VPN включён, MiniMax отвечает'
} else {
    Write-Log 'итог: VPN включён, но MiniMax не отвечает — проверь журнал'
    $ok = $false
}

$msg = @"
МиниГен — VPN включён (Happ).

Прокси Happ: 10809 (http) / 10808 (socks)
Системный прокси Windows: включён
Маршрут RustDesk 92.51.22.114 -> 192.168.1.1: $(if (Test-RustDeskRoute) { 'проложен' } else { 'НЕ проложен (нужны права админа)' })
Контур «МиниГен»: перезапущен
MiniMax: $(if (Test-Api $HAPP_HTTP) { 'отвечает' } else { 'НЕ отвечает' })
Журнал: $LOG_FILE
"@
Show-Result 'VPN — включить' $msg -Quiet:$Quiet
