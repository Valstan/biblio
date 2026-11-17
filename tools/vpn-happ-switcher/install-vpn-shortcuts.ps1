# install-vpn-shortcuts.ps1 — два ярлыка на рабочий стол: VPN вкл / VPN выкл.
#
# Создаёт:
#   «VPN — включить (Happ).lnk»   -> vpn-on.ps1
#   «VPN — выключить (Happ).lnk»  -> vpn-off.ps1
#
# Оба скрипта сами поднимают права администратора (UAC) — маршрут RustDesk
# и службу Happ иначе не тронуть.
#
# Пересоздать: powershell -ExecutionPolicy Bypass -File C:\Users\valstan\vpn\install-vpn-shortcuts.ps1

$ErrorActionPreference = 'Stop'

$Desktop = [Environment]::GetFolderPath('Desktop')
$VPN_DIR = 'C:\Users\valstan\vpn'
$PS      = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$shell   = New-Object -ComObject WScript.Shell

$items = @(
    @{ Name = 'VPN — включить (Happ).lnk'
       Script = 'vpn-on.ps1'
       Icon = 'C:\Program Files\flyfrogllc\happ\happ.exe,0'
       Desc = 'VPN: поднять Happ, прокси 10809, маршрут RustDesk мимо туннеля, перезапустить «МиниГен»' },
    @{ Name = 'VPN — выключить (Happ).lnk'
       Script = 'vpn-off.ps1'
       Icon = 'C:\Program Files\flyfrogllc\happ\happ.exe,0'
       Desc = 'VPN: погасить Happ, прокси MiniMax на резервный туннель 11081, снять маршрут RustDesk' }
)

foreach ($it in $items) {
    $path = Join-Path $Desktop $it.Name
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath       = $PS
    $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $VPN_DIR $it.Script)`""
    $lnk.WorkingDirectory = $VPN_DIR
    if (Test-Path $it.Icon.Split(',')[0]) { $lnk.IconLocation = $it.Icon }
    $lnk.Description      = $it.Desc
    $lnk.WindowStyle      = 7
    $lnk.Save()
    Write-Output "Создан: $($it.Name)"
}

Write-Output ''
Write-Output ("Ярлыков: {0} · папка: {1}" -f $items.Count, $Desktop)
