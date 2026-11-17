# vpn-lib.ps1 — общие куски переключателя VPN Happ (вкл/выкл) на PC79.
# Вызывается из vpn-on.ps1 и vpn-off.ps1 (dot-source), выполнять не нужно.
#
# Логика владельца (17.11): при ВКЛЮЧЁННОМ VPN прокладываются маршруты для RustDesk
# (чтобы он шёл мимо туннеля), при ВЫКЛЮЧЕННОМ — для MiniMax ставится прокси
# (свой резервный туннель), чтобы «МиниГен» продолжал работать.

$ErrorActionPreference = 'Continue'

$VPN_DIR       = 'C:\Users\valstan\vpn'
$LOG_FILE      = Join-Path $VPN_DIR 'vpn-switch.log'
$PS_EXE        = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

# Пути выхода в сеть
$HAPP_HTTP     = 'http://127.0.0.1:10809'     # прокси Happ (ядро xray)
$HAPP_SOCKS    = 'socks5://127.0.0.1:10808'
$RESERVE_HTTP  = 'http://127.0.0.1:11081'      # свой резервный туннель (xtunnel.exe)
$RESERVE_SOCKS = 'socks5://127.0.0.1:11080'

# RustDesk на этой машине ходит на Бокс 1 — маршрут мимо туннеля
$RUSTDESK_IP   = '92.51.22.114'
$LAN_GATEWAY   = '192.168.1.1'

# Проба «живости» MiniMax: любой HTTP-ответ (даже 400) = путь работает
$PROBE_URL     = 'https://agent.minimax.io/minimax-cloud/api/v1/signin/status'

$script:LogLines = @()

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    $script:LogLines += $line
    try { Add-Content -LiteralPath $LOG_FILE -Value $line -Encoding UTF8 } catch {}
    # Именно Console.WriteLine, а не Write-Output: вывод в конвейрь PowerShell
    # делал бы возвращаемое значение функции массивом и лгал проверки -not (...)
    try { [Console]::WriteLine($line) } catch {}
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Port([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Port([int]$port, [int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Port $port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# Проба MiniMax через указанный прокси. True = соединение установлено.
function Test-Api([string]$proxy) {
    try {
        $code = (& curl.exe -s -o NUL -w '%{http_code}' -m 20 -x $proxy $PROBE_URL 2>$null)
        $code = "$code".Trim()
        if ($code -and $code -ne '000') {
            Write-Log ("проба MiniMax через {0}: HTTP {1} — путь живой" -f $proxy, $code)
            return $true
        }
        Write-Log ("проба MiniMax через {0}: ответа нет (000)" -f $proxy)
    } catch {
        Write-Log ("проба MiniMax через {0}: ошибка {1}" -f $proxy, $_.Exception.Message)
    }
    return $false
}

# Переменные прокси: в реестре пользователя + в текущем процессе + broadcast WM_SETTINGCHANGE.
function Set-ProxyEnv([string]$http, [string]$socks) {
    $envKey = 'HKCU:\Environment'
    Set-ItemProperty $envKey -Name 'HTTP_PROXY'  -Value $http -Type String
    Set-ItemProperty $envKey -Name 'HTTPS_PROXY' -Value $http -Type String
    Set-ItemProperty $envKey -Name 'ALL_PROXY'   -Value $socks -Type String
    Set-ItemProperty $envKey -Name 'NO_PROXY'    -Value 'localhost,127.0.0.1,::1' -Type String
    $env:HTTP_PROXY  = $http
    $env:HTTPS_PROXY = $http
    $env:ALL_PROXY   = $socks
    $env:NO_PROXY    = 'localhost,127.0.0.1,::1'
    try {
        $sig = @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
        Add-Type -MemberDefinition $sig -Name EnvBroadcast -Namespace Vpn -ErrorAction SilentlyContinue | Out-Null
        $r = [UIntPtr]::Zero
        [Vpn.EnvBroadcast]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r) | Out-Null
    } catch {}
    Write-Log ("переменные прокси: HTTP_PROXY={0} ALL_PROXY={1}" -f $http, $socks)
}

# Системный прокси Windows (для браузеров и приложений).
function Set-SystemProxy([bool]$on) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    if ($on) {
        Set-ItemProperty $key -Name 'ProxyEnable'  -Value 1 -Type DWord
        Set-ItemProperty $key -Name 'ProxyServer'  -Value '127.0.0.1:10809' -Type String
    } else {
        Set-ItemProperty $key -Name 'ProxyEnable' -Value 0 -Type DWord
    }
    Write-Log ("системный прокси Windows: {0}" -f $(if ($on) { 'включён (127.0.0.1:10809)' } else { 'выключен' }))
}

function Test-RustDeskRoute {
    # Точное совпадение строки активного маршрута, а не «есть IP в дампе таблицы»
    $pat = [regex]::Escape($RUSTDESK_IP) + '\s+255\.255\.255\.255\s+' + [regex]::Escape($LAN_GATEWAY)
    $hit = route print $RUSTDESK_IP 2>$null | Select-String -Pattern $pat
    return [bool]$hit
}

function Add-RustDeskRoute {
    if (Test-RustDeskRoute) {
        Write-Log ("маршрут RustDesk {0} уже проложен" -f $RUSTDESK_IP)
        return $true
    }
    if (-not (Test-Admin)) {
        Write-Log "маршрут RustDesk: нужны права администратора — перезапусти с повышением"
        return $false
    }
    route -p add $RUSTDESK_IP mask 255.255.255.255 $LAN_GATEWAY metric 1 | Out-Null
    if (Test-RustDeskRoute) {
        Write-Log ("маршрут RustDesk проложен: {0} -> {1} (мимо туннеля)" -f $RUSTDESK_IP, $LAN_GATEWAY)
        return $true
    }
    Write-Log "маршрут RustDesk НЕ проложен — команда route не сработала"
    return $false
}

function Remove-RustDeskRoute {
    if (-not (Test-RustDeskRoute)) {
        Write-Log "маршрута RustDesk нет — снимать нечего"
        return $true
    }
    if (-not (Test-Admin)) {
        Write-Log "снятие маршрута RustDesk: нужны права администратора"
        return $false
    }
    route delete $RUSTDESK_IP | Out-Null
    Start-Sleep -Seconds 1
    if (Test-RustDeskRoute) {
        Write-Log "маршрут RustDesk НЕ снят"
        return $false
    }
    Write-Log ("маршрут RustDesk снят: {0}" -f $RUSTDESK_IP)
    return $true
}

# Перезапуск контура «МиниГен» (панель + лечащий) — чтобы процессы унаследовали
# НОВЫЕ переменные прокси. Старый процесс новых переменных не наследует.
function Restart-MiniGen {
    Write-Log "перезапускаю контур «МиниГен» (панель + лечащий)"

    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*mcode-panel-supervisor.js*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*mcode-panel.js*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*minimax-autheal.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 3

    Start-Process -FilePath 'wscript.exe' `
        -ArgumentList 'C:\Users\valstan\bin\mcode-panel-supervisor.vbs' -WindowStyle Hidden
    Start-Sleep -Seconds 5

    $panelUp = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest 'http://127.0.0.1:8099/' -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { $panelUp = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    $healer = [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*minimax-autheal.py*' })
    if ($panelUp -and $healer) {
        Write-Log "«МиниГен» поднят: панель отвечает, лечащий жив"
        return $true
    }
    Write-Log ("«МиниГен» поднят НЕ полностью: панель={0} лечащий={1}" -f $panelUp, $healer)
    return $false
}

# Свой резервный туннель (нужен, когда Happ выключен).
function Ensure-ReserveTunnel {
    if (Test-Port 11081) {
        Write-Log "резервный туннель уже живой (11081)"
        return $true
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $VPN_DIR 'start-tunnel.ps1') 2>&1 |
        ForEach-Object { Write-Log ("  туннель: {0}" -f $_) }
    if (Wait-Port 11081 15) {
        Write-Log "резервный туннель поднят (11081)"
        return $true
    }
    Write-Log "резервный туннель НЕ поднялся"
    return $false
}

function Ensure-Happ {
    $svc = Get-Service -Name 'HappService' -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Running') {
        try {
            Start-Service -Name 'HappService' -ErrorAction Stop
            Write-Log "служба HappService запущена"
        } catch {
            Write-Log ("службу HappService не поднять: {0}" -f $_.Exception.Message)
        }
    } elseif ($svc) {
        Write-Log "служба HappService уже работает"
    } else {
        Write-Log "службы HappService на машине нет"
    }
    $gui = Get-CimInstance Win32_Process -Filter "Name='Happ.exe'" -ErrorAction SilentlyContinue
    if (-not $gui) {
        $exe = 'C:\Program Files\flyfrogllc\happ\happ.exe'
        if (Test-Path $exe) {
            try {
                Start-Process -FilePath $exe -WindowStyle Hidden -ErrorAction Stop
                Write-Log "окно Happ запущено"
            } catch {
                Write-Log ("окно Happ не запустилось: {0}" -f $_.Exception.Message)
            }
        }
    } else {
        Write-Log "окно Happ уже открыто"
    }
    if (Wait-Port 10809 40) {
        Write-Log "прокси Happ слушает 10809"
        return $true
    }
    Write-Log "прокси Happ НЕ поднялся (10809 не слушается)"
    return $false
}

function Stop-Happ {
    Get-CimInstance Win32_Process -Filter "Name='Happ.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
        }
    try {
        Stop-Service -Name 'HappService' -Force -ErrorAction Stop
        Write-Log "служба HappService остановлена"
    } catch {
        Write-Log ("службу HappService не остановить: {0}" -f $_.Exception.Message)
    }
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Test-Port 10809)) { break }
        Start-Sleep -Seconds 1
    }
    if (Test-Port 10809) {
        Write-Log "порт 10809 всё ещё слушается — туннель не умер"
        return $false
    }
    Write-Log "туннель Happ погас (10809 закрыт)"
    return $true
}

function Show-Result([string]$title, [string]$msg, [switch]$Quiet) {
    if (-not $Quiet) {
        try {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
            [System.Windows.Forms.MessageBox]::Show($msg, $title) | Out-Null
        } catch {}
    }
}
