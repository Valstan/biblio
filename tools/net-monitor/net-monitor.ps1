# net-monitor.ps1 — трей-монитор сети для Windows 11 (biblio/tools, 2026-08-26)
#
# Значок в трее: зелёный = всё работает, оранжевый = частично (нет DNS / лёг туннель /
# не отвечает канал до Anthropic), красный = связи нет. Синее кольцо = активен VPN,
# красное кольцо = авария выхода (сменилась страна или трафик пошёл мимо туннеля).
# Тултип: скорость, VPN, DNS, состояние канала Claude Code.
# Меню: статус сети · канал Claude Code · «через что идёт сайт X» · тест DNS ·
# автовосстановление интернета · автозапуск · лог.
#
# Запуск: Запустить.cmd (или powershell -sta -ep bypass -file net-monitor.ps1)
# Диагностика без GUI:  powershell -ep bypass -file net-monitor.ps1 -Once
# Проверка логики восстановления, без изменений в системе: ... -SelfTest
#
# Смена DNS и восстановление требуют прав администратора — пункт меню
# «Перезапустить от администратора».
param(
    [switch]$Once,     # разовый прогон проверок в консоль, без трея
    [switch]$SelfTest  # печатает, какие шаги были бы выбраны; ничего не меняет
)

$ErrorActionPreference = 'SilentlyContinue'

# ---------------- конфиг ----------------
# Версия утилиты — ЕДИНСТВЕННОЕ место, где она задаётся. Показывается в меню трея,
# логе и отчёте статуса; сборщик build-package.ps1 читает её отсюда для имени архива,
# установщик install.ps1 — для сравнения «что стоит / что ставим».
$AppVersion = '1.1.0'
$AppName  = 'BrainNetMonitor'
$DataDir  = Join-Path $env:LOCALAPPDATA 'net-monitor'
$LogFile  = Join-Path $DataDir 'log.txt'
$ConfFile = Join-Path $DataDir 'config.json'
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

# Якоря «интернет жив», список DNS-кандидатов и признаки VPN-адаптеров заданы в
# lib/probe.ps1: их использует и трей, и фоновый исполнитель. Дубля здесь быть не
# должно — иначе трей считал бы состояние по одному набору, а диагностика по другому.

function Write-Log([string]$msg) {
    try { Add-Content -Path $LogFile -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding UTF8 } catch {}
}

function Load-Config {
    # Умолчания намеренно безопасные: мониторинг работает сразу, а всё, что меняет
    # систему, владелец включает сам.
    # Умолчания рассчитаны на ЧУЖУЮ машину: ничего не предполагаем про VPN, страну и
    # Claude Code — иначе на компьютере без них утилита показывала бы вечную аварию.
    $def = [ordered]@{
        autoDns             = $false
        autoRecover         = $false   # лестница восстановления
        allowVpnRestart     = $false   # ступень «перезапустить службу VPN»
        allowAdapterRestart = $false   # ступень «перезапустить сетевой адаптер»
        graceSec            = 10       # сколько ждать, вдруг вернётся само
        expectedCountry     = ''       # пусто = не следить за страной выхода
        vpnMode             = 'auto'   # auto | on | off — следить ли за туннелем
        claudeMode          = 'auto'   # auto | on | off — следить ли за каналом Claude Code
        vpnService          = ''       # имя службы VPN-клиента для перезапуска (пусто = определить)
        setupDone           = $false   # был ли задан вопрос про страну при первом запуске
        vpnSeen             = $false   # на этой машине VPN когда-либо видели
    }
    $cfg = [pscustomobject]$def
    if (Test-Path $ConfFile) {
        try {
            $saved = Get-Content $ConfFile -Raw | ConvertFrom-Json
            foreach ($k in $def.Keys) {
                if ($null -ne $saved.$k) { $cfg.$k = $saved.$k }
            }
        } catch {}
    }
    $cfg
}
function Save-Config($cfg) { try { $cfg | ConvertTo-Json | Set-Content $ConfFile -Encoding UTF8 } catch {} }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------- модули ----------------
# probe.ps1  — индикаторы состояния (только читает: Test-TcpPort, Get-DefaultRouteInfo,
#              Test-Link, Get-TunnelState, Test-ClaudeChannel, Test-Egress, Get-FullHealth)
# recovery.ps1 — лестница восстановления, снапшот и откат (всё, что меняет систему)
# Исходник модулей сохраняется СТРОКОЙ ($script:LibSource): его же выполняет у себя
# фоновый исполнитель. Так один и тот же код работает и из папки lib, и в склеенной
# сборке (build-package.ps1 -Single заменяет этот блок на встроенный текст модулей).
$script:LibSource = ''
foreach ($m in @('probe.ps1','recovery.ps1')) {
    $p = Join-Path $PSScriptRoot "lib\$m"
    if (-not (Test-Path $p)) { throw "Не найден модуль lib\$m рядом со скриптом ($PSScriptRoot)." }
    $script:LibSource += [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) + [Environment]::NewLine
}
. ([scriptblock]::Create($script:LibSource))

# ---------------- сетевые проверки ----------------
# Все читающие проверки (Get-VpnState, Get-NetSnapshot, Invoke-DnsBench, отчёты…)
# живут в lib/probe.ps1 — их выполняет и фоновый исполнитель. Здесь остаётся только
# то, что МЕНЯЕТ систему (DNS) и потому исполняется в потоке интерфейса.

function Get-DnsTargetInterface {
    # Куда писать DNS. НЕ «интерфейс маршрута по умолчанию»: при активном VPN это сам
    # туннель, чьи настройки принадлежат VPN-клиенту — он их перезапишет при следующем
    # переподключении, а сброс на DHCP там способен оборвать резолв до реконнекта.
    # Настройка имеет смысл только на физическом адаптере.
    $phys = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
            Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1
    if ($phys) { return [pscustomobject]@{ IfIndex = $phys.ifIndex; Alias = $phys.Name } }
    Get-DefaultRouteInfo
}

function Apply-Dns([string[]]$servers) {
    $def = Get-DnsTargetInterface
    if (-not $def) { return 'Не найден сетевой адаптер для настройки DNS.' }
    if (Test-IsAdmin) {
        try {
            Set-DnsClientServerAddress -InterfaceIndex $def.IfIndex -ServerAddresses $servers -ErrorAction Stop
            Clear-DnsClientCache -ErrorAction SilentlyContinue
            Write-Log "DNS применён: $($servers -join ', ') (if=$($def.Alias))"
            return "Поставлено на «$($def.Alias)»: $($servers -join ', ')"
        } catch { return "Ошибка применения: $_" }
    } else {
        # одно UAC-окно на смену
        $args = "-NoProfile -Command Set-DnsClientServerAddress -InterfaceIndex $($def.IfIndex) -ServerAddresses " +
                (($servers | ForEach-Object { "'$_'" }) -join ',') + '; Clear-DnsClientCache'
        try {
            Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList $args -Wait
            Write-Log "DNS применён (elevated): $($servers -join ', ')"
            return "Поставлено (через окно администратора): $($servers -join ', ')"
        } catch { return 'Отменено (нужны права администратора).' }
    }
}

function Reset-DnsToDhcp {
    $def = Get-DnsTargetInterface
    if (-not $def) { return 'Не найден сетевой адаптер для настройки DNS.' }
    if (Test-IsAdmin) {
        try { Set-DnsClientServerAddress -InterfaceIndex $def.IfIndex -ResetServerAddresses -ErrorAction Stop; return 'DNS возвращён на автомат (DHCP).' }
        catch { return "Ошибка: $_" }
    } else {
        try {
            Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList "-NoProfile -Command Set-DnsClientServerAddress -InterfaceIndex $($def.IfIndex) -ResetServerAddresses" -Wait
            return 'DNS возвращён на автомат (DHCP).'
        } catch { return 'Отменено.' }
    }
}

# ---------------- режим -Once (диагностика в консоль) ----------------
if ($Once) {
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    $cfgOnce = Load-Config
    Set-ExpectedCountry $cfgOnce.expectedCountry
    Set-WatchModes $cfgOnce.vpnMode $cfgOnce.claudeMode
    Set-VpnSeen ([bool]$cfgOnce.vpnSeen)
    $s1 = Get-NetSnapshot; $x1 = Get-XrayStats
    Start-Sleep -Seconds 2
    $s2 = Get-NetSnapshot; $x2 = Get-XrayStats
    $dt = ($s2.At - $s1.At).TotalSeconds
    Write-Host (Get-StatusReport -Version $AppVersion)
    Write-Host ("Скорость за 2 с:  ↓ {0}   ↑ {1}" -f (Format-Speed (($s2.Rx-$s1.Rx)/$dt)), (Format-Speed (($s2.Tx-$s1.Tx)/$dt)))
    $fl = Get-FlowDelta $x1 $x2
    if ($fl) {
        Write-Host ("За те же 2 с:  Claude Code {0}, туннель {1}, мимо туннеля {2}" -f
            (Format-Bytes $fl.ClaudeBytes), (Format-Bytes $fl.TunnelBytes), (Format-Bytes $fl.DirectBytes))
    }
    exit 0
}

# ---------------- режим -SelfTest (проверка логики, без изменений) ----------------
if ($SelfTest) {
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    $cfgT = Load-Config
    Set-ExpectedCountry $cfgT.expectedCountry
    Set-WatchModes $cfgT.vpnMode $cfgT.claudeMode
    Set-VpnSeen ([bool]$cfgT.vpnSeen)
    Write-Host "=== Самопроверка net-monitor v$AppVersion (ничего не меняется) ==="
    Write-Host ("Права администратора: {0}" -f $(if (Test-IsAdmin) { 'есть' } else { 'НЕТ — шаги восстановления будут пропускаться' }))
    Write-Host ("Автовосстановление: {0} · перезапуск VPN: {1} · перезапуск адаптера: {2} · пауза: {3} с · ожидаемая страна: {4}" -f
        $(if ($cfgT.autoRecover) { 'включено' } else { 'выключено' }),
        $(if ($cfgT.allowVpnRestart) { 'разрешён' } else { 'запрещён' }),
        $(if ($cfgT.allowAdapterRestart) { 'разрешён' } else { 'запрещён' }),
        $cfgT.graceSec, $cfgT.expectedCountry)

    Write-Host "`n--- какие шаги были бы выбраны для каждого класса поломки ---"
    foreach ($c in @('no-link','dns-down','tunnel-down','claude-down','egress-changed','leak')) {
        $plan = Get-RecoveryPlan $c ([bool]$cfgT.allowVpnRestart) ([bool]$cfgT.allowAdapterRestart)
        Write-Host ("  {0,-15} → {1}" -f $c, $(if ($plan.Count) { $plan -join ' → ' } else { '(нет разрешённых действий — зову владельца)' }))
    }

    Write-Host "`n--- запреты, которые нельзя снять настройкой ---"
    foreach ($f in @('metric-favor-physical','ipv6-enable','winsock-reset','ip-reset','restart-tunnel-adapter','vpn-switch-server','vpn-edit-config')) {
        try { Assert-Forbidden $f; Write-Host "  ПЛОХО: $f не заблокирован" }
        catch { Write-Host "  заблокировано: $f" }
    }

    # Проверка стоп-листа сама по себе мало что доказывает: она подтверждает лишь, что в
    # списке есть строки. Поэтому каждая ступень вызывается по-настоящему — без прав
    # администратора любая из них обязана остановиться на своём предусловии и НИЧЕГО не
    # сделать. Так ловятся ошибки в самих проверках, а не только в списке.
    Write-Host "`n--- сухой прогон ступеней: на чём каждая останавливается ---"
    if (Test-IsAdmin) {
        Write-Host '  ПРОПУСК: запущено от администратора — сухой прогон изменил бы настройки.'
        Write-Host '  Для этой проверки запустите самопроверку БЕЗ прав администратора.'
    } else {
        foreach ($s in @('flush-dns','restore-dns','physical-dns','restart-vpn','restart-physical')) {
            try {
                $e = Invoke-RecoveryStep $s $null
                Write-Host ("  {0,-17} → {1}" -f $s, $(if ($e) { $e.Result } else { '(без записи в журнал)' }))
            } catch {
                Write-Host ("  {0,-17} → ИСКЛЮЧЕНИЕ: {1}" -f $s, $_.Exception.Message)
            }
        }
    }

    Write-Host "`n--- текущее состояние ---"
    $h = Get-FullHealth
    Write-Host ("  вердикт: [{0}] {1}" -f $h.Verdict.Class, $h.Verdict.Text)
    Write-Host ("  связь: IP={0} DNS={1} · туннель: {2} · Anthropic: {3} · выход: {4}" -f
        $h.Link.IpOk, $h.Link.DnsOk, $h.Tunnel.Ok, $h.Claude.Ok, $(if ($h.Egress) { "$($h.Egress.Ip) $($h.Egress.Country)" } else { 'не проверялся' }))

    Write-Host "`n--- снапшот и его применимость ---"
    $sn = New-NetSnapshot
    Write-Host ("  отпечаток сети: шлюз {0} / {1}" -f $sn.Fingerprint.GatewayIp, $sn.Fingerprint.GatewayMac)
    Write-Host ("  применим к текущей сети: {0}" -f (Test-SnapshotApplicable $sn))
    exit 0
}

# ---------------- GUI (трей) ----------------
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, Microsoft.VisualBasic

# DestroyIcon нужен, чтобы освобождать HICON после Icon.FromHandle — сам .NET его не
# освобождает, а значок в трее перерисовывается многократно за сутки работы.
if (-not ('NetMonitorNative' -as [type])) {
    Add-Type -Namespace '' -Name 'NetMonitorNative' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
public static extern bool DestroyIcon(System.IntPtr handle);
'@
}

# один экземпляр
$mutex = New-Object System.Threading.Mutex($false, "Global\$AppName")
if (-not $mutex.WaitOne(0, $false)) {
    [System.Windows.Forms.MessageBox]::Show('net-monitor уже запущен (значок в трее).', $AppName) | Out-Null
    exit 0
}

$cfg = Load-Config
# Режимы применяем до построения меню: пункт «Канал Claude Code…» показывается по
# результату Test-ClaudePresent, а тот зависит от claudeMode из настроек.
Set-ExpectedCountry $cfg.expectedCountry
Set-WatchModes $cfg.vpnMode $cfg.claudeMode
Set-VpnSeen ([bool]$cfg.vpnSeen)

# ---------------- фоновый исполнитель ----------------
# ЛЕЧЕНИЕ ЗАВИСАНИЙ МЕНЮ. Раньше все сетевые проверки шли прямо в тике таймера, то
# есть в потоке интерфейса: пока Test-ClaudeChannel ждал свои 10 секунд таймаута,
# правый клик стоял в очереди — меню появлялось с опозданием, где попало, и висело
# с курсором ожидания. Теперь долгие операции выполняет ОТДЕЛЬНЫЙ runspace, а поток
# интерфейса только забирает готовые результаты и рисует.
#
# Устройство: один runspace + очередь заданий, строго по одному за раз. Сериализация
# намеренная: пробы и шаги восстановления не должны толкаться (шаг меняет систему,
# проба тут же мерила бы переходное состояние). Задание = скрипт (строка) + аргументы
# + OnDone-обработчик, который выполняется в потоке интерфейса из тика.
$script:workerRs = [runspacefactory]::CreateRunspace()
$script:workerRs.Open()
$initPs = [powershell]::Create()
$initPs.Runspace = $script:workerRs
# Внутри runspace — те же модули, что и здесь (текстом, см. $script:LibSource), плюс
# свой Write-Log в тот же файл. Состояние probe-модуля (страна, режимы) там СВОЁ:
# каждое задание получает свежие значения аргументами и выставляет их само — иначе
# смена настроек в меню не доходила бы до фона.
[void]$initPs.AddScript(@'
param($libSource, $logFile)
$ErrorActionPreference = 'SilentlyContinue'
$script:WorkerLogFile = $logFile
function Write-Log([string]$msg) {
    # Путь — в script-переменной: параметры init-скрипта после его завершения
    # недоступны, и лог из фоновых заданий молча пропадал бы.
    try { Add-Content -Path $script:WorkerLogFile -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding UTF8 } catch {}
}
. ([scriptblock]::Create($libSource))
'@).AddArgument($script:LibSource).AddArgument($LogFile)
[void]$initPs.Invoke()
$initPs.Dispose()

$script:jobQueue  = New-Object System.Collections.Queue
$script:jobActive = $null

# Каждое задание начинается с установки режимов — свежих, из текущих настроек.
$JobPreamble = @'
param($a)
Set-ExpectedCountry $a.ExpectedCountry
Set-WatchModes $a.VpnMode $a.ClaudeMode
Set-VpnSeen $a.VpnSeen
'@

function Get-ModeArgs {
    @{ ExpectedCountry = (Get-ExpectedCountry); VpnMode = $cfg.vpnMode
       ClaudeMode = $cfg.claudeMode; VpnSeen = (Get-VpnSeen) }
}

function Add-WorkerJob([string]$Kind, [string]$Body, [hashtable]$Arg, [scriptblock]$OnDone) {
    $script:jobQueue.Enqueue(@{ Kind = $Kind; Script = ($JobPreamble + $Body); Arg = $Arg; OnDone = $OnDone })
}

function Test-WorkerBusyWith([string]$Kind) {
    if ($script:jobActive -and $script:jobActive.Kind -eq $Kind) { return $true }
    foreach ($j in $script:jobQueue) { if ($j.Kind -eq $Kind) { return $true } }
    $false
}

function Update-Worker {
    # Вызывается из тика. Забирает готовый результат (и зовёт OnDone в потоке
    # интерфейса), затем запускает следующее задание из очереди.
    if ($script:jobActive -and $script:jobActive.Handle.IsCompleted) {
        $j = $script:jobActive
        $script:jobActive = $null
        $res = $null
        try {
            # Один выход — отдаём как есть, несколько — массивом (DNS-тест
            # возвращает строки таблицы; «последний элемент» потерял бы их).
            $out = @($j.PS.EndInvoke($j.Handle))
            $res = if ($out.Count -le 1) { $out[0] } else { $out }
        }
        catch { Write-Log "worker error ($($j.Kind)): $_" }
        $j.PS.Dispose()
        if ($j.OnDone) {
            try { & $j.OnDone $res } catch { Write-Log "worker OnDone error ($($j.Kind)): $_" }
        }
    }
    if (-not $script:jobActive -and $script:jobQueue.Count -gt 0) {
        $j = $script:jobQueue.Dequeue()
        $ps = [powershell]::Create()
        $ps.Runspace = $script:workerRs
        [void]$ps.AddScript($j.Script)
        [void]$ps.AddArgument($j.Arg)
        $j.PS = $ps
        $j.Handle = $ps.BeginInvoke()
        $script:jobActive = $j
    }
}

function New-TrayIcon([string]$state, [bool]$vpn, [bool]$egressAlarm = $false) {
    # state: green|orange|red|gray. Кольцо: синее = VPN активен,
    # красное = авария выхода (сменилась страна или трафик пошёл мимо туннеля).
    $bmp = New-Object System.Drawing.Bitmap 16, 16
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'
    $color = switch ($state) {
        'green'  { [System.Drawing.Color]::FromArgb(46, 204, 64) }
        'orange' { [System.Drawing.Color]::FromArgb(255, 165, 0) }
        'red'    { [System.Drawing.Color]::FromArgb(220, 53, 40) }
        default  { [System.Drawing.Color]::Gray }
    }
    $brush = New-Object System.Drawing.SolidBrush $color
    $g.FillEllipse($brush, 2, 2, 12, 12)
    if ($egressAlarm) {
        $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 0, 0)), 2
        $g.DrawEllipse($pen, 1, 1, 14, 14)
        $pen.Dispose()
    } elseif ($vpn) {
        $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(0, 120, 255)), 2
        $g.DrawEllipse($pen, 1, 1, 14, 14)
        $pen.Dispose()
    }
    $brush.Dispose(); $g.Dispose()
    $h = $bmp.GetHicon()
    $icon = [System.Drawing.Icon]::FromHandle($h)
    # Icon.FromHandle не владеет хэндлом, поэтому копируем иконку и сразу освобождаем
    # и HICON, и bitmap: значок меняется часто, и утечка на каждой смене накапливалась бы
    # в процессе, который живёт сутками.
    $copy = $icon.Clone()
    $icon.Dispose()
    [void][NetMonitorNative]::DestroyIcon($h)
    $bmp.Dispose()
    $copy
}

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = New-TrayIcon 'gray' $false
$notify.Text = "net-monitor v$AppVersion — запуск…"
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

# Заголовок с версией — чтобы всегда было видно, КАКАЯ сборка сейчас крутится
# (наведение мышью показывает, из какой папки она запущена).
$miVersion = New-Object System.Windows.Forms.ToolStripMenuItem("net-monitor v$AppVersion")
$miVersion.Enabled = $false
$miVersion.ToolTipText = $PSScriptRoot
[void]$menu.Items.Add($miVersion)
$menu.Items.Add('-') | Out-Null

$miStatus  = $menu.Items.Add('Статус сети…')
# Пункт про Claude Code показываем только тем, у кого он есть.
$miClaude  = if (Test-ClaudePresent) { $menu.Items.Add('Канал Claude Code…') } else { $null }
$miSite    = $menu.Items.Add('Через что идёт сайт…')
$miBench   = $menu.Items.Add('Тест DNS-серверов…')
$miAuto    = New-Object System.Windows.Forms.ToolStripMenuItem('Авто-тест DNS раз в час + ставить лучший')
$miAuto.CheckOnClick = $true
$miAuto.Checked = [bool]$cfg.autoDns
[void]$menu.Items.Add($miAuto)
$menu.Items.Add('-') | Out-Null

# --- автовосстановление интернета ---
$miRec = New-Object System.Windows.Forms.ToolStripMenuItem('Восстанавливать интернет автоматически')
$miRec.CheckOnClick = $true
$miRec.Checked = [bool]$cfg.autoRecover
[void]$menu.Items.Add($miRec)

$miRecSub = New-Object System.Windows.Forms.ToolStripMenuItem('…что при этом разрешено')
$miRecVpn = New-Object System.Windows.Forms.ToolStripMenuItem('Перезапускать службу VPN (рвёт соединения!)')
$miRecVpn.CheckOnClick = $true
$miRecVpn.Checked = [bool]$cfg.allowVpnRestart
$miRecNic = New-Object System.Windows.Forms.ToolStripMenuItem('Перезапускать сетевой адаптер (рвёт всё!)')
$miRecNic.CheckOnClick = $true
$miRecNic.Checked = [bool]$cfg.allowAdapterRestart
$miRecCountry = New-Object System.Windows.Forms.ToolStripMenuItem(
    "Ожидаемая страна выхода: $(if ($cfg.expectedCountry) { $cfg.expectedCountry } else { 'не задана' })…")
[void]$miRecSub.DropDownItems.AddRange(@($miRecVpn, $miRecNic, $miRecCountry))
[void]$menu.Items.Add($miRecSub)
$miRecNow = $menu.Items.Add('Починить сейчас (разово)…')
$menu.Items.Add('-') | Out-Null
$miRun     = New-Object System.Windows.Forms.ToolStripMenuItem('Автозапуск при входе в Windows')
$miRun.CheckOnClick = $true
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$miRun.Checked = [bool](Get-ItemProperty -Path $runKey -Name $AppName -ErrorAction SilentlyContinue)
[void]$menu.Items.Add($miRun)
if (-not (Test-IsAdmin)) { $miElev = $menu.Items.Add('Перезапустить от администратора (для смены DNS без окон)') }
$miLog     = $menu.Items.Add('Открыть лог')
$menu.Items.Add('-') | Out-Null
$miExit    = $menu.Items.Add('Выход')
$notify.ContextMenuStrip = $menu

# ---- состояние ----
$script:prev = Get-NetSnapshot
$script:prevStats = Get-XrayStats
$script:lastState = ''
$script:lastInetOk = $null
$script:lastClaudeOk = $null
$script:claudeAt = [datetime]::MinValue   # канал проверяем не каждые 5 с, а раз в минуту
$script:claude = $null
$script:egressAt = [datetime]::MinValue   # страну — раз в 5 минут
$script:egress = $null
$script:healthyStreak = 0                 # подряд здоровых тиков — для снятия снапшота
$script:badStreak = 0                     # подряд больных — порог запуска восстановления
$script:goldSnapshot = $null
$script:recovery = $null                  # состояние идущего восстановления (автомат)
$script:recoveryFails = 0                 # неудачных попыток подряд
$script:recoveryCooldownUntil = [datetime]::MinValue
$script:lastVerdict = $null
$script:ownerAlerted = ''
# Определяется один раз при старте: следить ли за каналом Claude Code. На машине без
# него проверка была бы десятисекундной паузой ради тревоги о ненужном сервисе.
$script:claudeWatched = Test-ClaudePresent
Write-Log ("режимы: VPN=$($cfg.vpnMode), Claude=$($cfg.claudeMode) (следим: $script:claudeWatched), страна=" +
           $(if ($cfg.expectedCountry) { $cfg.expectedCountry } else { 'не задана' }))

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000

# Скрипт периодического опроса — выполняется в фоновом runspace. Что мерить в этот
# раз, решает тик (флаги *Due): канал Anthropic — раз в минуту, страну — раз в 5 минут,
# снапшот — по здоровой серии.
$ProbeJobBody = @'
$snap  = Get-NetSnapshot
$inet  = Test-Link
$vpn   = Get-VpnState
$tun   = Get-TunnelState
$stats = Get-XrayStats
$claude = $null
if ($a.ClaudeWatched) {
    if (-not $inet.IpOk) {
        $claude = [pscustomobject]@{ Ok = $false; Status = $null; Ms = 0; Via = $null; Reason = 'нет связи' }
    } elseif ($a.ClaudeDue) {
        $claude = Test-ClaudeChannel
    }
}
$egress = if ($a.EgressDue -and $inet.Ok) { Test-Egress } else { $null }
$gold   = if ($a.SnapshotDue) { New-NetSnapshot } else { $null }
[pscustomobject]@{ Snap = $snap; Inet = $inet; Vpn = $vpn; Tun = $tun; Stats = $stats
                   Claude = $claude; Egress = $egress; Gold = $gold }
'@

# Скрипт одного шага восстановления — тоже в фоне: restart-vpn ждёт поднятия туннеля
# до 20 с, и в потоке интерфейса это снова заморозило бы меню.
$StepJobBody = @'
Invoke-RecoveryStep $a.Step $a.Snapshot $a.VpnService
'@

function Invoke-ProbeResult {
    # Обработка готового замера. Работает в потоке интерфейса, но только с готовыми
    # данными — ни одного сетевого вызова здесь быть не должно.
    param($r)

    $snap = $r.Snap
    $dt = ($snap.At - $script:prev.At).TotalSeconds
    if ($dt -le 0) { $dt = 5 }
    $down = ($snap.Rx - $script:prev.Rx) / $dt
    $up   = ($snap.Tx - $script:prev.Tx) / $dt
    if ($down -lt 0) { $down = 0 }; if ($up -lt 0) { $up = 0 }
    $script:prev = $snap

    $inet = $r.Inet
    $vpn  = $r.Vpn
    $tun  = $r.Tun

    $flow = Get-FlowDelta $script:prevStats $r.Stats
    if ($r.Stats) { $script:prevStats = $r.Stats }

    if (-not $script:claudeWatched) {
        $script:claude = $null
    } elseif ($r.Claude) {
        $script:claude = $r.Claude
        # Время замера обновляем только по настоящей проверке: синтетическое «нет
        # связи» не должно откладывать настоящую на минуту после возврата линка.
        if ($inet.IpOk) { $script:claudeAt = Get-Date }
    }

    if ($r.Egress) {
        $script:egress = $r.Egress
        $script:egressAt = Get-Date
    }

    if ($r.Gold) {
        $script:goldSnapshot = $r.Gold
        Write-Log "снят снапшот рабочей конфигурации (выход $($r.Gold.EgressIp)/$($r.Gold.Country))"
    }

    $verdict = Get-HealthVerdict $inet $tun $script:claude $script:egress $flow
        $script:lastVerdict = $verdict

        # Запоминаем, что VPN на этой машине есть. Иначе выключенный клиент выглядел бы
        # как «VPN тут не настроен», и надзор за страной отключался бы сам.
        if (($tun.AdapterUp -or $tun.CoreRunning) -and -not $cfg.vpnSeen) {
            $cfg.vpnSeen = $true
            Set-VpnSeen $true
            Save-Config $cfg
            Write-Log 'на этой машине обнаружен VPN — контроль туннеля включён'
        }

        # Однократный вопрос про страну — когда интерфейс уже жив и туннель успел
        # подняться. Отметку «спросили» ставим только после фактического вопроса:
        # при автозапуске вместе с Windows клиент VPN поднимается не сразу, и
        # преждевременная отметка означала бы «не спросим уже никогда».
        if ($script:askCountry -and -not (Get-ExpectedCountry)) {
            if ($tun.AdapterUp -and $script:egress -and $script:egress.Verified -and $script:egress.Country) {
                $script:askCountry = $false
                $cfg.setupDone = $true
                Save-Config $cfg
                $eg0 = $script:egress
                $where0 = if ($eg0.City) { "$($eg0.Country), $($eg0.City)" } else { $eg0.Country }
                $ans = [System.Windows.Forms.MessageBox]::Show(
                    "Обнаружен VPN. Сейчас выход в интернет — $where0 (адрес $($eg0.Ip)).`n`nСледить, чтобы страна выхода оставалась $($eg0.Country)? Утилита предупредит, если она изменится — например, когда клиент VPN сам переключится на другой сервер.`n`nПереключать страну она никогда не будет.",
                    $AppName, 'YesNo', 'Question')
                if ($ans -eq 'Yes') {
                    Set-ExpectedCountry $eg0.Country
                    $cfg.expectedCountry = (Get-ExpectedCountry)
                    Save-Config $cfg
                    $miRecCountry.Text = "Ожидаемая страна выхода: $($cfg.expectedCountry)…"
                    Write-Log "контроль страны включён: $($cfg.expectedCountry)"
                    $script:egressAt = [datetime]::MinValue
                }
            } elseif (((Get-Date) - $script:startedAt).TotalMinutes -ge 10) {
                # Десять минут прошло, туннеля так и нет — VPN тут, видимо, не нужен.
                $script:askCountry = $false
                $cfg.setupDone = $true
                Save-Config $cfg
            }
        }
        $egressAlarm = ($verdict.Class -in @('egress-changed','leak'))

        $state = switch ($verdict.Severity) {
            'ok'      { 'green' }
            'partial' { 'orange' }
            'alarm'   { 'orange' }
            default   { 'red' }
        }

        $key = "$state|$($vpn.Active)|$egressAlarm"
        if ($key -ne $script:lastState) {
            $old = $notify.Icon
            $notify.Icon = New-TrayIcon $state $vpn.Active $egressAlarm
            if ($old) { $old.Dispose() }
            $script:lastState = $key
        }

        # --- уведомления о смене состояния ---
        $inetOk = ($verdict.Severity -eq 'ok')
        if ($null -ne $script:lastInetOk -and $inetOk -ne $script:lastInetOk) {
            if ($inetOk) { $notify.ShowBalloonTip(3000, 'Связь восстановилась', $verdict.Text, 'Info') }
            else { $notify.ShowBalloonTip(5000, 'Проблема со связью', $verdict.Text, 'Warning') }
            Write-Log "смена состояния: [$($verdict.Class)] $($verdict.Text)"
        }
        $script:lastInetOk = $inetOk

        # Канал Claude Code — отдельная новость: ради этого и затевался мониторинг.
        $ccOk = [bool]($script:claude -and $script:claude.Ok)
        if ($null -ne $script:lastClaudeOk -and $ccOk -ne $script:lastClaudeOk) {
            if ($ccOk) { $notify.ShowBalloonTip(3000, 'Claude Code снова на связи', 'Канал до Anthropic отвечает.', 'Info') }
            else { $notify.ShowBalloonTip(6000, 'Claude Code потерял связь', "Канал до Anthropic не отвечает: $($script:claude.Reason)", 'Warning') }
            Write-Log "канал Claude: ok=$ccOk ($($script:claude.Reason))"
        }
        $script:lastClaudeOk = $ccOk

        # Авария выхода: показываем один раз на каждое новое состояние, не каждые 5 с.
        if ($egressAlarm -and $script:ownerAlerted -ne $verdict.Text) {
            $notify.ShowBalloonTip(10000, 'ВНИМАНИЕ: выход в интернет изменился', $verdict.Text, 'Error')
            Write-Log "АВАРИЯ ВЫХОДА: $($verdict.Text)"
            $script:ownerAlerted = $verdict.Text
        }
        if (-not $egressAlarm) { $script:ownerAlerted = '' }

        # --- снапшот «золотой» конфигурации: три здоровых тика подряд ---
        # Сам снапшот снимает фоновый опрос (флаг SnapshotDue) — здесь только серия.
        if ($verdict.Severity -eq 'ok' -and $script:egress -and $script:egress.Verified -and $script:egress.Ok) {
            $script:healthyStreak++
        } else {
            $script:healthyStreak = 0
        }

        # --- автовосстановление: по одному шагу за тик ---
        #
        # Лестница НЕ выполняется целиком внутри тика: её шаги и паузы занимают минуты,
        # а тик идёт в потоке интерфейса — значок, меню и окна замерли бы ровно на то
        # время, когда владелец на них смотрит. Поэтому здесь конечный автомат: каждый
        # тик делает один шаг и возвращает управление.
        $bad = ($verdict.Severity -in @('down','partial'))
        if ($bad) { $script:badStreak++ } else { $script:badStreak = 0 }

        if ($script:recovery) {
            # --- идёт восстановление ---
            $r = $script:recovery
            if ($r.Pending) {
                # Шаг выполняется в фоне; пока он не вернулся, машину не двигаем —
                # иначе следующий шаг лёг бы поверх работающего.
                $notify.Text = "восстановление: $($r.Phase)…"
            }
            elseif ($verdict.Severity -eq 'ok') {
                $notify.ShowBalloonTip(5000, 'Связь восстановлена',
                    $(if ($r.Steps.Count) { "Помогло: $($r.Steps -join ' → ')" } else { 'Вернулось само.' }), 'Info')
                Write-Log "восстановление завершено: помогло [$($r.Steps -join ', ')]"
                $script:recovery = $null
                $script:recoveryFails = 0
                $script:recoveryCooldownUntil = [datetime]::MinValue
            }
            elseif ($verdict.Severity -eq 'alarm') {
                # Авария выхода: дальше не лечим и не повторяем — ждём владельца.
                $notify.ShowBalloonTip(10000, 'Восстановление остановлено', $verdict.Text, 'Error')
                Write-Log "восстановление остановлено: $($verdict.Text)"
                $script:recovery = $null
                $script:recoveryCooldownUntil = (Get-Date).AddMinutes(30)
            }
            elseif ((Get-Date) -lt $r.WaitUntil) {
                $notify.Text = "восстановление: жду ($($r.Phase))"
            }
            elseif ($r.Index -ge $r.Plan.Count) {
                # Лестница пройдена без успеха — возвращаем известное рабочее состояние.
                if ($script:goldSnapshot) {
                    $argR = Get-ModeArgs
                    $argR.Step = 'restore-dns'; $argR.Snapshot = $script:goldSnapshot; $argR.VpnService = ''
                    Add-WorkerJob 'step' $StepJobBody $argR $null
                }
                # Пауза перед следующей попыткой, нарастающая. Без неё утилита при
                # стойкой поломке гоняла бы лестницу каждые полминуты — с балунами и,
                # что хуже, с повторными перезапусками VPN, если они разрешены.
                $script:recoveryFails++
                $wait = [Math]::Min(30, 5 * [Math]::Pow(2, $script:recoveryFails - 1))
                $script:recoveryCooldownUntil = (Get-Date).AddMinutes($wait)
                $notify.ShowBalloonTip(10000, 'Восстановить не удалось',
                    "Пробовал: $($r.Steps -join ' → '). Следующая попытка через $wait мин — или почините вручную.", 'Error')
                Write-Log "восстановление не помогло: [$($r.Steps -join ', ')]; следующая попытка через $wait мин"
                $script:recovery = $null
            }
            else {
                $step = $r.Plan[$r.Index]
                $r.Index++
                $r.Steps += $step
                $r.Phase = $step
                $r.Pending = $true
                $notify.Text = "восстановление: $step"
                Write-Log "шаг восстановления: $step"
                $argS = Get-ModeArgs
                $argS.Step = $step; $argS.Snapshot = $script:goldSnapshot; $argS.VpnService = $cfg.vpnService
                Add-WorkerJob 'step' $StepJobBody $argS {
                    param($entry)
                    $rr = $script:recovery
                    if (-not $rr) { return }
                    $rr.Pending = $false
                    $rr.WaitUntil = (Get-Date).AddSeconds(5)   # дать сети устояться до проверки
                    if ($entry -and $entry.Result -like 'СТОП*') {
                        $notify.ShowBalloonTip(10000, 'Восстановление остановлено', $entry.Result, 'Error')
                        Write-Log "восстановление остановлено на шаге $($rr.Phase) : $($entry.Result)"
                        $script:recovery = $null
                    }
                }
            }
        }
        elseif ($miRec.Checked -and $bad -and $script:badStreak -ge 3 -and
                (Get-Date) -ge $script:recoveryCooldownUntil) {
            # Порог в несколько тиков подряд: одиночная заминка (медленный ответ, пауза
            # у провайдера) не должна запускать лестницу — тем более её тяжёлые ступени.
            $plan = Get-RecoveryPlan $verdict.Class ([bool]$miRecVpn.Checked) ([bool]$miRecNic.Checked)
            # Ступени, рвущие соединения, требуют более уверенного диагноза.
            if ($script:badStreak -lt 6) {
                $plan = @($plan | Where-Object { $_ -notin @('restart-vpn','restart-physical') })
            }
            if ($plan.Count) {
                Write-Log "запускаю восстановление, класс: $($verdict.Class), план: [$($plan -join ', ')]"
                $script:recovery = [pscustomobject]@{
                    Class = $verdict.Class; Plan = @($plan); Index = 0; Steps = @(); Pending = $false
                    Phase = 'пауза'; WaitUntil = (Get-Date).AddSeconds([int]$cfg.graceSec)
                }
                $notify.Text = "восстановление: жду $($cfg.graceSec) с"
            }
        }

        # --- тултип ---
        $cty = if ($script:egress -and $script:egress.Country) { $script:egress.Country } else { '?' }
        $vtxt = if ($vpn.Active) { if ($vpn.DefaultVia) { "VPN:$cty" } else { 'VPN:часть' } }
                elseif ($tun.Configured) { 'VPN:нет' } else { "выход:$cty" }
        $txt = "v {0} ^ {1} | {2}" -f (Format-Speed $down), (Format-Speed $up), $vtxt
        if ($script:claudeWatched) {
            $ccTxt = if ($null -eq $script:claude) { 'CC:?' } elseif ($script:claude.Ok) { "CC:ok $($script:claude.Ms)мс" } else { 'CC:НЕТ' }
            $txt += " | $ccTxt"
        }
        if ($txt.Length -gt 63) { $txt = $txt.Substring(0, 63) }   # лимит NotifyIcon.Text
        $notify.Text = $txt
}

$timer.Add_Tick({
    # Тик лёгкий: забрать готовые результаты фона, обработать, заказать следующий
    # замер. Сами сетевые проверки здесь не выполняются — меню и значок не замирают.
    #
    # Защита от повторного входа сохранена: OnDone может открыть модальное окно
    # (вопрос про страну), и пока оно висит, WinForms продолжает качать сообщения.
    if ($script:inTick) { return }
    $script:inTick = $true
    try {
        Update-Worker
        if (-not (Test-WorkerBusyWith 'probe') -and -not (Test-WorkerBusyWith 'step')) {
            $arg = Get-ModeArgs
            $arg.ClaudeWatched = [bool]$script:claudeWatched
            $arg.ClaudeDue = [bool]($script:claudeWatched -and
                (((Get-Date) - $script:claudeAt).TotalSeconds -ge 60 -or $null -eq $script:claude))
            $arg.EgressDue = [bool](((Get-Date) - $script:egressAt).TotalMinutes -ge 5)
            $arg.SnapshotDue = [bool]($script:healthyStreak -ge 3 -and
                (-not $script:goldSnapshot -or ((Get-Date) - $script:goldSnapshot.At).TotalMinutes -ge 30))
            Add-WorkerJob 'probe' $ProbeJobBody $arg { param($r) if ($r) { Invoke-ProbeResult $r } }
        }
    } catch { Write-Log "tick error: $_" }
    finally { $script:inTick = $false }
})

# часовой авто-DNS
$dnsTimer = New-Object System.Windows.Forms.Timer
$dnsTimer.Interval = 3600 * 1000
$dnsTimer.Add_Tick({
    # Сам замер (десятки DNS-запросов) идёт в фоне; здесь только заказ и обработка.
    if (-not $miAuto.Checked) { return }
    if (Test-WorkerBusyWith 'dnsbench') { return }
    Add-WorkerJob 'dnsbench' 'Invoke-DnsBench' (Get-ModeArgs) {
        param($rows)
        try {
            $best = @($rows) | Where-Object { $_.Ms -ne $null -and $_.Answers -ge 2 } | Sort-Object Ms | Select-Object -First 2
            if (-not $best) { Write-Log 'авто-DNS: ни один сервер не ответил, ничего не меняю'; return }
            $curr = @(Get-CurrentDns)
            $want = @($best | ForEach-Object { $_.Ip })
            if ($curr.Count -ge 1 -and $curr[0] -eq $want[0]) { Write-Log "авто-DNS: лучший уже стоит ($($want[0]))"; return }
            if (-not (Test-IsAdmin)) { Write-Log 'авто-DNS: нет прав администратора, пропуск (запусти от админа)'; return }
            $msg = Apply-Dns $want
            $notify.ShowBalloonTip(4000, 'Авто-DNS', $msg, 'Info')
        } catch { Write-Log "auto-dns error: $_" }
    }
})
$dnsTimer.Start()

# ---- обработчики меню ----
# Окна собирают текст в фоне: сбор — это те же десятки секунд сетевых проверок, и в
# потоке интерфейса он снова заморозил бы меню (ровно та жалоба, ради которой фоновый
# исполнитель и появился). Окно открывается сразу с заглушкой, текст доезжает следом.
$miStatus.Add_Click({
    $f = New-Object System.Windows.Forms.Form
    $f.Text = "Статус сети — net-monitor v$AppVersion"
    $f.Size = New-Object System.Drawing.Size(620, 440)
    $f.StartPosition = 'CenterScreen'
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Multiline = $true; $tb.ReadOnly = $true; $tb.ScrollBars = 'Vertical'
    $tb.Dock = 'Fill'; $tb.Font = New-Object System.Drawing.Font('Consolas', 10)
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = 'Обновить'; $btn.Dock = 'Bottom'
    $refresh = {
        $tb.Text = 'Собираю данные… (окно можно закрыть, проверки продолжатся в фоне)'
        $btn.Enabled = $false
        $arg = Get-ModeArgs
        $arg.Version = $AppVersion
        Add-WorkerJob 'status' 'Get-StatusReport -Version $a.Version' $arg {
            param($text)
            if ($f.IsDisposed) { return }
            $tb.Text = "$text" -replace "`n", "`r`n"
            $btn.Enabled = $true
        }.GetNewClosure()
    }.GetNewClosure()
    $btn.Add_Click($refresh)
    $f.Controls.Add($tb); $f.Controls.Add($btn)
    $f.Show()
    & $refresh
})

$miSite.Add_Click({
    $site = [Microsoft.VisualBasic.Interaction]::InputBox('Адрес сайта (без https://), например: гоньба.рф или github.com', 'Через что идёт сайт', '')
    if ($site) {
        $site = $site -replace '^https?://', '' -replace '/.*$', ''
        $arg = Get-ModeArgs
        $arg.Site = $site
        Add-WorkerJob 'site' 'Get-SiteRouteVerdict $a.Site' $arg {
            param($v)
            [System.Windows.Forms.MessageBox]::Show("$v", 'Маршрут сайта') | Out-Null
        }
    }
})

$miBench.Add_Click({
    $benchDomains = @(Get-DnsBenchDomains)
    $f = New-Object System.Windows.Forms.Form
    $f.Text = 'Тест DNS-серверов (среднее по: ' + ($benchDomains -join ', ') + ')'
    $f.Size = New-Object System.Drawing.Size(560, 460)
    $f.StartPosition = 'CenterScreen'
    $lv = New-Object System.Windows.Forms.ListView
    $lv.View = 'Details'; $lv.FullRowSelect = $true; $lv.Dock = 'Fill'
    [void]$lv.Columns.Add('Сервер', 240); [void]$lv.Columns.Add('IP', 130); [void]$lv.Columns.Add('мс', 60); [void]$lv.Columns.Add('ответов', 70)
    $panel = New-Object System.Windows.Forms.FlowLayoutPanel
    $panel.Dock = 'Bottom'; $panel.Height = 40
    $bRun = New-Object System.Windows.Forms.Button; $bRun.Text = 'Прогнать тест'; $bRun.Width = 120
    $bApply = New-Object System.Windows.Forms.Button; $bApply.Text = 'Поставить выбранный (или лучший)'; $bApply.Width = 220
    $bDhcp = New-Object System.Windows.Forms.Button; $bDhcp.Text = 'Вернуть автомат (DHCP)'; $bDhcp.Width = 160
    $panel.Controls.AddRange(@($bRun, $bApply, $bDhcp))
    $f.Controls.Add($lv); $f.Controls.Add($panel)
    $doRun = {
        # Замер идёт в фоне: это десятки DNS-запросов, окно и меню не должны замирать.
        $lv.Items.Clear()
        [void]$lv.Items.Add((New-Object System.Windows.Forms.ListViewItem('Меряю… (можно пользоваться меню)')))
        $bRun.Enabled = $false
        Add-WorkerJob 'dnsbench' 'Invoke-DnsBench' (Get-ModeArgs) {
            param($rows)
            if ($f.IsDisposed) { return }
            $lv.Items.Clear()
            foreach ($r in (@($rows) | Sort-Object { if ($_.Ms -eq $null) { 99999 } else { $_.Ms } })) {
                $it = New-Object System.Windows.Forms.ListViewItem($r.Name)
                [void]$it.SubItems.Add($r.Ip)
                [void]$it.SubItems.Add($(if ($r.Ms -ne $null) { "$($r.Ms)" } else { '—' }))
                [void]$it.SubItems.Add("$($r.Answers)/$($benchDomains.Count)")
                if ($r.Ms -eq $null) { $it.ForeColor = [System.Drawing.Color]::Gray }
                [void]$lv.Items.Add($it)
            }
            $bRun.Enabled = $true
        }.GetNewClosure()
    }.GetNewClosure()
    $bRun.Add_Click($doRun)
    $bApply.Add_Click({
        $ip = $null
        # SubItems.Count: во время замера в списке строка-заглушка без колонки IP.
        if ($lv.SelectedItems.Count -gt 0 -and $lv.SelectedItems[0].SubItems.Count -gt 1) { $ip = $lv.SelectedItems[0].SubItems[1].Text }
        elseif ($lv.Items.Count -gt 0 -and $lv.Items[0].SubItems.Count -gt 1) { $ip = $lv.Items[0].SubItems[1].Text }
        if (-not $ip) { return }
        # вторым — следующий живой из таблицы
        $second = $null
        foreach ($it in $lv.Items) { if ($it.SubItems[1].Text -ne $ip -and $it.SubItems[2].Text -ne '—') { $second = $it.SubItems[1].Text; break } }
        $servers = @($ip); if ($second) { $servers += $second }
        $msg = Apply-Dns $servers
        [System.Windows.Forms.MessageBox]::Show($msg, 'Смена DNS') | Out-Null
    })
    $bDhcp.Add_Click({ [System.Windows.Forms.MessageBox]::Show((Reset-DnsToDhcp), 'Смена DNS') | Out-Null })
    $f.Show()
    & $doRun
})

if ($miClaude) { $miClaude.Add_Click({
    $f = New-Object System.Windows.Forms.Form
    $f.Text = 'Канал Claude Code'
    $f.Size = New-Object System.Drawing.Size(680, 420)
    $f.StartPosition = 'CenterScreen'
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Multiline = $true; $tb.ReadOnly = $true; $tb.ScrollBars = 'Vertical'
    $tb.Dock = 'Fill'; $tb.Font = New-Object System.Drawing.Font('Consolas', 10)

    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = 'Обновить'; $btn.Dock = 'Bottom'
    # Отчёт собирается фоном (Get-ClaudeChannelReport в probe.ps1): внутри полторы
    # секунды замера трафика и сетевые проверки. GetNewClosure обязателен: скриптблоки
    # PowerShell не захватывают локальные переменные, и к моменту клика по немодальному
    # окну $f/$tb были бы уже недоступны — кнопка молча ничего не делала бы.
    $refresh = {
        $tb.Text = 'Собираю данные… (окно можно закрыть, проверки продолжатся в фоне)'
        $btn.Enabled = $false
        Add-WorkerJob 'claude-report' 'Get-ClaudeChannelReport' (Get-ModeArgs) {
            param($text)
            if ($f.IsDisposed) { return }
            $tb.Text = "$text" -replace "`n", "`r`n"
            $btn.Enabled = $true
        }.GetNewClosure()
    }.GetNewClosure()
    $btn.Add_Click($refresh)
    $f.Controls.Add($tb); $f.Controls.Add($btn)
    $f.Show()
    & $refresh
}) }

$miRec.Add_Click({
    $cfg.autoRecover = $miRec.Checked
    Save-Config $cfg
    if ($miRec.Checked) {
        $msg = "Автовосстановление включено.`n`nЧто будет делать: подождёт $($cfg.graceSec) с, потом попробует безопедные шаги — сброс кэша DNS, возврат сохранённых настроек, при лежащем туннеле смена DNS на физическом адаптере.`n`nПерезапуск VPN и адаптера остаются запрещёнными, пока вы не разрешите их отдельно (подменю «что при этом разрешено»)."
        if (-not (Test-IsAdmin)) { $msg += "`n`nСейчас нет прав администратора — шаги будут пропускаться. Перезапустите от администратора." }
        [System.Windows.Forms.MessageBox]::Show($msg, $AppName) | Out-Null
    }
})

$miRecVpn.Add_Click({
    if ($miRecVpn.Checked) {
        $r = [System.Windows.Forms.MessageBox]::Show(
            "Разрешить перезапуск службы VPN при обрыве?`n`nЭто рвёт ВСЕ соединения через туннель. Главное: при переподключении клиент VPN может выбрать другой узел — то есть другую страну.`n`nУтилита сверяет страну до и после, и при расхождении сразу остановится и позовёт вас. Но саму смену страны она предотвратить не может.`n`nВключить?",
            $AppName, 'YesNo', 'Warning')
        if ($r -ne 'Yes') { $miRecVpn.Checked = $false; return }
    }
    $cfg.allowVpnRestart = $miRecVpn.Checked
    Save-Config $cfg
})

$miRecNic.Add_Click({
    if ($miRecNic.Checked) {
        $r = [System.Windows.Forms.MessageBox]::Show(
            "Разрешить перезапуск сетевого адаптера?`n`nСамый жёсткий из разрешённых шагов: рвёт всё, включая канал самого VPN, и применяется только когда нет линка или адрес APIPA (169.254.x.x).`n`nВключить?",
            $AppName, 'YesNo', 'Warning')
        if ($r -ne 'Yes') { $miRecNic.Checked = $false; return }
    }
    $cfg.allowAdapterRestart = $miRecNic.Checked
    Save-Config $cfg
})

$miRecCountry.Add_Click({
    $cur = Get-ExpectedCountry
    $v = [Microsoft.VisualBasic.Interaction]::InputBox(
        "Код страны, в которой должен быть выход в интернет (две буквы, например DE).`n`nОставьте пустым, чтобы не следить за страной — тогда утилита просто покажет, где выход.`n`nУтилита никогда не переключает страну сама: она следит и предупреждает.",
        'Ожидаемая страна выхода', $cur)
    if ($null -eq $v) { return }   # нажали «Отмена»
    $v = $v.Trim()
    # Пусто = выключить контроль. Иначе только буквы: ввод вида «12» дал бы вечную тревогу.
    if ($v -and $v -notmatch '^[A-Za-z]{2}$') {
        [System.Windows.Forms.MessageBox]::Show('Нужен код из двух букв (DE, RU, NL…) или пустое поле, чтобы не следить.', $AppName) | Out-Null
        return
    }
    Set-ExpectedCountry $v
    $cfg.expectedCountry = (Get-ExpectedCountry)
    Save-Config $cfg
    $miRecCountry.Text = "Ожидаемая страна выхода: $(if ($cfg.expectedCountry) { $cfg.expectedCountry } else { 'не задана' })…"
    $script:egressAt = [datetime]::MinValue   # перепроверить сразу
})

$miRecNow.Add_Click({
    if (-not (Test-IsAdmin)) {
        [System.Windows.Forms.MessageBox]::Show('Для восстановления нужны права администратора — воспользуйтесь пунктом «Перезапустить от администратора».', $AppName) | Out-Null
        return
    }
    if ($script:recovery) {
        [System.Windows.Forms.MessageBox]::Show('Восстановление уже идёт — дождитесь его окончания.', $AppName) | Out-Null
        return
    }
    # Диагноз берётся из последнего тика, а не считается заново: полный опрос занимает
    # десятки секунд, и владелец нажал бы кнопку, за которой ничего не происходит.
    $cls = if ($script:lastVerdict) { $script:lastVerdict.Class } else { 'ok' }
    $txt = if ($script:lastVerdict) { $script:lastVerdict.Text } else { 'состояние ещё не измерено' }
    $plan = Get-RecoveryPlan $cls ([bool]$miRecVpn.Checked) ([bool]$miRecNic.Checked)
    if (-not $plan.Count) {
        [System.Windows.Forms.MessageBox]::Show("Сейчас: $txt`n`nДля этого случая разрешённых действий нет — либо всё в порядке, либо это ситуация, которую утилита намеренно не лечит сама.", $AppName) | Out-Null
        return
    }
    $r = [System.Windows.Forms.MessageBox]::Show(
        "Сейчас: $txt`n`nБудут выполнены шаги:`n$($plan -join ' → ')`n`nЗапустить?", $AppName, 'YesNo', 'Question')
    if ($r -ne 'Yes') { return }
    # Запускаем тот же автомат, что и автоматика: шаги пойдут по тикам, трей не замрёт.
    $script:recovery = [pscustomobject]@{
        Class = $cls; Plan = @($plan); Index = 0; Steps = @(); Pending = $false
        Phase = 'запуск вручную'; WaitUntil = (Get-Date)
    }
    # Ручной запуск снимает паузу после прошлых неудач: владелец решил попробовать ещё.
    $script:recoveryCooldownUntil = [datetime]::MinValue
    Write-Log "ручное восстановление: класс $cls, план [$($plan -join ', ')]"
})

$miAuto.Add_Click({
    $cfg.autoDns = $miAuto.Checked
    Save-Config $cfg
    if ($miAuto.Checked -and -not (Test-IsAdmin)) {
        [System.Windows.Forms.MessageBox]::Show('Авто-смена DNS требует прав администратора. Тест будет идти, но применение — пропускаться. Перезапусти от администратора (пункт меню).', $AppName) | Out-Null
    }
})

$miRun.Add_Click({
    if ($miRun.Checked) {
        # Частая ошибка при получении утилиты архивом: запустить её прямо из окна
        # просмотра, не распаковав. Тогда папка лежит во временном каталоге, который
        # Windows однажды вычистит, — автозапуск тихо перестанет работать, а в реестре
        # останется ссылка в никуда. Поэтому такой путь не записываем.
        if ($PSScriptRoot -like "*\Temp\*" -or $PSScriptRoot -like "$env:TEMP*") {
            [System.Windows.Forms.MessageBox]::Show(
                "Утилита запущена из временной папки:`n$PSScriptRoot`n`nПохоже, архив открыт без распаковки. Автозапуск с такого пути сломается, когда Windows очистит папку.`n`nПеренесите папку в постоянное место (например, C:\Utils\net-monitor) и включите автозапуск оттуда.",
                $AppName) | Out-Null
            $miRun.Checked = $false
            return
        }
        $cmd = Join-Path $PSScriptRoot 'Запустить.cmd'
        if (-not (Test-Path $cmd)) {
            [System.Windows.Forms.MessageBox]::Show("Не найден файл запуска:`n$cmd", $AppName) | Out-Null
            $miRun.Checked = $false
            return
        }
        Set-ItemProperty -Path $runKey -Name $AppName -Value "`"$cmd`""
    } else {
        Remove-ItemProperty -Path $runKey -Name $AppName -ErrorAction SilentlyContinue
    }
})

if ($miElev) {
    $miElev.Add_Click({
        Start-Process powershell -Verb RunAs -ArgumentList "-sta -ep bypass -WindowStyle Hidden -File `"$PSCommandPath`""
        $notify.Visible = $false
        [System.Windows.Forms.Application]::Exit()
    })
}

$miLog.Add_Click({ if (Test-Path $LogFile) { Start-Process notepad $LogFile } else { [System.Windows.Forms.MessageBox]::Show('Лог пока пуст.', $AppName) | Out-Null } })

$miExit.Add_Click({
    $timer.Stop(); $dnsTimer.Stop()
    $notify.Visible = $false
    # Работающее фоновое задание не ждём: это читающая проверка, а шаг восстановления
    # при выходе владелец останавливает осознанно. Runspace закрываем асинхронно.
    try {
        if ($script:jobActive) { $script:jobActive.PS.Stop() }
        $script:workerRs.CloseAsync()
    } catch {}
    [System.Windows.Forms.Application]::Exit()
})

Write-Log "запуск net-monitor v$AppVersion (из $PSScriptRoot)"

# Первое знакомство: если на машине есть VPN, один раз спрашиваем, следить ли за
# страной выхода. Так у человека без VPN вопроса не возникает вовсе, а тот, кто ради
# страны VPN и держит, не должен искать эту настройку в подменю.
#
# Сам вопрос задаётся в тике, а не здесь: модальное окно до запуска цикла сообщений
# повисло бы поверх ещё не ожившего интерфейса.
$script:askCountry = (-not $cfg.setupDone)
$script:startedAt = Get-Date

# Первый тик — почти сразу, но уже внутри цикла сообщений. Раньше здесь стоял ручной
# вызов OnTick до Application::Run: тик проходил вне цикла сообщений, и открытое им
# модальное окно приводило к вложенным тикам, перезаписывающим состояние.
$timer.Interval = 300
$timer.Start()
$firstTick = {
    if ($timer.Interval -ne 5000) { $timer.Interval = 5000 }
}.GetNewClosure()
$timer.Add_Tick($firstTick)

[System.Windows.Forms.Application]::Run()
$mutex.ReleaseMutex()
