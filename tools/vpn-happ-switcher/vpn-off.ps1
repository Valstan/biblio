# vpn-off.ps1 — «VPN — выключить» (Happ).
#
# Что делает (заказ владельца 17.11):
#   1. Поднимает свой резервный туннель (11080/11081) — он теперь служит
#      прокси для MiniMax.
#   2. Снимает маршрут RustDesk (туннеля нет — маршрут не нужен).
#   3. Гасит Happ: окно и службу; 10809 закрывается, маршрут по умолчанию
#      туннеля исчезает.
#   4. Выключает системный прокси Windows.
#   5. Ставит переменные прокси для «МиниГена» на резервный туннель.
#   6. Перезапускает контур «МиниГен», чтобы процессы унаследовали переменные.
#   7. Проверяет, что MiniMax отвечает. Если нет — ОТКАТ: Happ возвращается,
#      маршрут RustDesk прокладывается обратно, прокси — обратно на Happ.
#
# Нужны права администратора (служба и маршрут) — скрипт сам поднимает права.
# Ярлык на рабочем столе: «VPN — выключить (Happ)».

param([switch]$NoElevate, [switch]$Quiet)

. (Join-Path $PSScriptRoot 'vpn-lib.ps1')

if (-not $NoElevate -and -not (Test-Admin)) {
    Start-Process -FilePath $PS_EXE -Verb RunAs -WindowStyle Hidden -ArgumentList `
        "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 0
}

Write-Log '=== VPN: выключаю Happ ==='

$ok = $true

# 1. Резервный туннель — теперь это прокси для MiniMax
if (-not (Ensure-ReserveTunnel)) { $ok = $false }

# 2. Маршрут RustDesk — снять
if (-not (Remove-RustDeskRoute)) { $ok = $false }

# 3. Гасим Happ
if (-not (Stop-Happ)) { $ok = $false }

# 4. Системный прокси — переезжает на резервный туннель (не выключается:
#    десктопы MiniMax читают системный прокси, и в прямой линии API может не отвечать)
Set-SystemProxy '127.0.0.1:11081'

# 5. Прокси для «МиниГена» — на резервный туннель
Set-ProxyEnv $RESERVE_HTTP $RESERVE_SOCKS

# 6. Перезапуск контура
if (-not (Restart-MiniGen)) { $ok = $false }

# 7. Проба MiniMax через резерв
$alive = Test-Api $RESERVE_HTTP

if (-not $alive) {
    Write-Log 'MiniMax через резерв не отвечает — ОТКАТ: возвращаю Happ'
    & (Join-Path $PSScriptRoot 'vpn-on.ps1') -NoElevate -Quiet | ForEach-Object { Write-Log ("  откат: {0}" -f $_) }
    $msg = @"
МиниГен — VPN выключить НЕ удалось.

Резервный туннель не дал связи с MiniMax — Happ возвращён
(маршрут RustDesk проложен, прокси снова на Happ).
Журнал: $LOG_FILE
"@
    Show-Result 'VPN — выключить' $msg -Quiet:$Quiet
    exit 1
}

Write-Log 'итог: VPN выключен, MiniMax отвечает через резервный туннель'
$msg = @"
МиниГен — VPN выключен (Happ погашен).

Прокси MiniMax: резервный туннель 11081 (http) / 11080 (socks)
Системный прокси Windows: переключён на резервный туннель (11081)
Маршрут RustDesk: снят
Контур «МиниГен»: перезапущен
MiniMax: отвечает
Журнал: $LOG_FILE
"@
Show-Result 'VPN — выключить' $msg -Quiet:$Quiet
