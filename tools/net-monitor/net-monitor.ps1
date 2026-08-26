# net-monitor.ps1 — трей-монитор сети для Windows 11 (brain_matrica tools, 2026-08-26)
#
# Значок в трее: зелёный = интернет есть, оранжевый = частично (IP-связь есть, DNS не
# отвечает — или наоборот), красный = интернета нет. Синее кольцо = активен VPN.
# Тултип: скорость приёма/отдачи, VPN, текущий DNS.
# Меню: статус сети · «через что идёт сайт X» · тест DNS-серверов с применением лучшего ·
# авто-тест DNS раз в час · автозапуск · лог.
#
# Запуск: Запустить.cmd (или powershell -sta -ep bypass -file net-monitor.ps1)
# Диагностика без GUI: powershell -ep bypass -file net-monitor.ps1 -Once
#
# Смена DNS требует прав администратора — пункт меню «Перезапустить от администратора».
param(
    [switch]$Once   # разовый прогон проверок в консоль, без трея
)

$ErrorActionPreference = 'SilentlyContinue'

# ---------------- конфиг ----------------
$AppName  = 'BrainNetMonitor'
$DataDir  = Join-Path $env:LOCALAPPDATA 'net-monitor'
$LogFile  = Join-Path $DataDir 'log.txt'
$ConfFile = Join-Path $DataDir 'config.json'
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

# Кандидаты DNS для теста (имя = подпись в таблице)
$DnsCandidates = [ordered]@{
    'Yandex 77.88.8.8'      = '77.88.8.8'
    'Yandex 77.88.8.1'      = '77.88.8.1'
    'Google 8.8.8.8'        = '8.8.8.8'
    'Google 8.8.4.4'        = '8.8.4.4'
    'Cloudflare 1.1.1.1'    = '1.1.1.1'
    'Cloudflare 1.0.0.1'    = '1.0.0.1'
    'Quad9 9.9.9.9'         = '9.9.9.9'
    'AdGuard 94.140.14.14'  = '94.140.14.14'
    'OpenDNS 208.67.222.222'= '208.67.222.222'
}
$BenchDomains = @('ya.ru','vk.com','wikipedia.org')

# Якоря «интернет жив». IP-якоря проверяются TCP-коннектом на 443 (без DNS вообще),
# хост-якорь дополнительно доказывает, что работает резолвинг.
# ВАЖНО: в некоторых сетях провайдер/шлюз фильтрует мелкие сайты при живом интернете —
# поэтому якоря только крупные, иначе будут ложные «нет интернета».
$IpAnchors   = @('77.88.8.8','1.1.1.1')   # :443
$HostAnchor  = 'ya.ru'                     # :443

$VpnNameRegex = 'wireguard|openvpn|tap-|\btun\b|tunnel|vpn|proton|outline|amnezia|warp|radmin'
$VpnExclude   = 'teredo|isatap|6to4|loopback'

function Write-Log([string]$msg) {
    try { Add-Content -Path $LogFile -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) -Encoding UTF8 } catch {}
}

function Load-Config {
    if (Test-Path $ConfFile) { try { return Get-Content $ConfFile -Raw | ConvertFrom-Json } catch {} }
    [pscustomobject]@{ autoDns = $false }
}
function Save-Config($cfg) { try { $cfg | ConvertTo-Json | Set-Content $ConfFile -Encoding UTF8 } catch {} }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------- сетевые проверки ----------------

function Test-TcpPort([string]$target, [int]$port = 443, [int]$timeoutMs = 1500) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect($target, $port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($timeoutMs) -and $c.Connected) { return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

function Get-DefaultRouteInfo {
    # интерфейс с маршрутом по умолчанию (минимальная метрика)
    $r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
         Sort-Object -Property { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
    if ($r) { [pscustomobject]@{ IfIndex = $r.InterfaceIndex; Alias = $r.InterfaceAlias } }
}

function Get-VpnState {
    # 1) штатные VPN-подключения Windows
    $names = @()
    foreach ($v in @(Get-VpnConnection -ErrorAction SilentlyContinue) + @(Get-VpnConnection -AllUserConnection -ErrorAction SilentlyContinue)) {
        if ($v.ConnectionStatus -eq 'Connected') { $names += $v.Name }
    }
    # 2) адаптеры сторонних VPN (WireGuard/OpenVPN/Proton/...)
    $ifaces = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.OperationalStatus -eq 'Up' } |
        Where-Object { ($_.Name + ' ' + $_.Description) -match $VpnNameRegex -and ($_.Name + ' ' + $_.Description) -notmatch $VpnExclude }
    foreach ($i in $ifaces) { $names += $i.Name }
    $names = $names | Select-Object -Unique
    $defaultVia = $false
    $def = Get-DefaultRouteInfo
    if ($def -and ($names -contains $def.Alias)) { $defaultVia = $true }
    [pscustomobject]@{ Active = [bool]$names; Names = $names; DefaultVia = $defaultVia; VpnAliases = $names }
}

function Get-CurrentDns {
    $def = Get-DefaultRouteInfo
    if (-not $def) { return @() }
    (Get-DnsClientServerAddress -InterfaceIndex $def.IfIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses
}

function Get-NetSnapshot {
    # суммарные байты по default-route интерфейсу (fallback: все Up кроме loopback)
    $def = Get-DefaultRouteInfo
    $all = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' }
    $sel = $all
    if ($def) {
        $m = $all | Where-Object { $_.Name -eq $def.Alias }
        if ($m) { $sel = $m }
    }
    $rx = 0L; $tx = 0L
    foreach ($i in $sel) { $s = $i.GetIPv4Statistics(); $rx += $s.BytesReceived; $tx += $s.BytesSent }
    [pscustomobject]@{ Rx = $rx; Tx = $tx; At = (Get-Date) }
}

function Test-Internet {
    $ipOk = $false
    foreach ($a in $IpAnchors) { if (Test-TcpPort $a 443 1500) { $ipOk = $true; break } }
    $dnsOk = $false
    try {
        $ans = Resolve-DnsName -Name $HostAnchor -Type A -DnsOnly -QuickTimeout -ErrorAction Stop | Where-Object { $_.IPAddress } | Select-Object -First 1
        if ($ans -and (Test-TcpPort $ans.IPAddress 443 1500)) { $dnsOk = $true }
    } catch {}
    [pscustomobject]@{ IpOk = $ipOk; DnsOk = $dnsOk }
}

function Format-Speed([double]$bps) {
    if ($bps -ge 1MB) { '{0:0.0} МБ/с' -f ($bps/1MB) }
    elseif ($bps -ge 1KB) { '{0:0} КБ/с' -f ($bps/1KB) }
    else { '{0:0} Б/с' -f $bps }
}

function Resolve-FirstIPv4([string]$name) {
    try {
        $r = Resolve-DnsName -Name $name -Type A -DnsOnly -QuickTimeout -ErrorAction Stop | Where-Object { $_.IPAddress } | Select-Object -First 1
        if ($r) { return $r.IPAddress }
    } catch {}
    $null
}

function Get-SiteRouteVerdict([string]$site) {
    $ip = Resolve-FirstIPv4 $site
    if (-not $ip) { return "«$site»: имя не резолвится (DNS не ответил)." }
    $vpn = Get-VpnState
    $alias = $null
    try {
        $rt = Find-NetRoute -RemoteIPAddress $ip -ErrorAction Stop
        $alias = ($rt | Select-Object -ExpandProperty InterfaceAlias -Unique | Select-Object -First 1)
    } catch {}
    if (-not $alias) { return "«$site» → $ip : маршрут определить не удалось." }
    if ($vpn.VpnAliases -contains $alias) {
        "«$site» → $ip`nПойдёт ЧЕРЕЗ VPN (интерфейс: $alias)."
    } else {
        $tail = if ($vpn.Active) { "`n(VPN при этом активен: $($vpn.Names -join ', ') — но этот сайт идёт мимо него)" } else { '' }
        "«$site» → $ip`nПойдёт НАПРЯМУЮ (интерфейс: $alias).$tail"
    }
}

function Invoke-DnsBench {
    # → массив [pscustomobject] Name, Ip, Ms (среднее по доменам; $null = не ответил)
    $rows = @()
    $list = [ordered]@{}
    $curr = @(Get-CurrentDns)
    for ($i = 0; $i -lt $curr.Count; $i++) { $list["Текущий #$($i+1) ($($curr[$i]))"] = $curr[$i] }
    foreach ($k in $DnsCandidates.Keys) { if ($curr -notcontains $DnsCandidates[$k]) { $list[$k] = $DnsCandidates[$k] } }
    foreach ($name in $list.Keys) {
        $ip = $list[$name]
        $times = @()
        foreach ($d in $BenchDomains) {
            $t = Measure-Command {
                try { Resolve-DnsName -Name $d -Server $ip -Type A -DnsOnly -QuickTimeout -ErrorAction Stop | Out-Null; $script:__ok = $true }
                catch { $script:__ok = $false }
            }
            if ($script:__ok) { $times += $t.TotalMilliseconds }
        }
        $ms = if ($times.Count -gt 0) { [math]::Round(($times | Measure-Object -Average).Average, 0) } else { $null }
        $rows += [pscustomobject]@{ Name = $name; Ip = $ip; Ms = $ms; Answers = $times.Count }
    }
    $rows
}

function Apply-Dns([string[]]$servers) {
    $def = Get-DefaultRouteInfo
    if (-not $def) { return 'Не найден интерфейс с маршрутом по умолчанию.' }
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
    $def = Get-DefaultRouteInfo
    if (-not $def) { return 'Не найден интерфейс с маршрутом по умолчанию.' }
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

function Get-StatusReport {
    $inet = Test-Internet
    $vpn  = Get-VpnState
    $def  = Get-DefaultRouteInfo
    $dns  = @(Get-CurrentDns)
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("=== Статус сети  $(Get-Date -Format 'HH:mm:ss') ===")
    $inetLine = if ($inet.IpOk -and $inet.DnsOk) { 'ЕСТЬ (IP-связь + DNS работают)' }
                elseif ($inet.IpOk) { 'ЧАСТИЧНО: IP-связь есть, но DNS не отвечает — похоже, проблема в DNS-сервере' }
                elseif ($inet.DnsOk) { 'СТРАННО: DNS отвечает, IP-якоря нет' }
                else { 'НЕТ (ни один якорь не отвечает)' }
    [void]$sb.AppendLine("Интернет: $inetLine")
    [void]$sb.AppendLine("Выход в сеть: " + $(if ($def) { $def.Alias } else { 'маршрут по умолчанию не найден!' }))
    if ($vpn.Active) {
        $via = if ($vpn.DefaultVia) { 'ВЕСЬ трафик идёт через VPN' } else { 'VPN активен, но трафик по умолчанию идёт МИМО него' }
        [void]$sb.AppendLine("VPN: включён ($($vpn.Names -join ', ')) — $via")
    } else {
        [void]$sb.AppendLine('VPN: не активен, всё напрямую')
    }
    [void]$sb.AppendLine("DNS сейчас: " + $(if ($dns) { $dns -join ', ' } else { 'не задан / DHCP не выдал' }))
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Интерфейсы (Up):')
    foreach ($i in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
             Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' }) {
        $ips = ($i.GetIPProperties().UnicastAddresses | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } | ForEach-Object { $_.Address.ToString() }) -join ', '
        [void]$sb.AppendLine("  $($i.Name) [$($i.NetworkInterfaceType)] $ips")
    }
    $sb.ToString()
}

# ---------------- режим -Once (диагностика в консоль) ----------------
if ($Once) {
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    $s1 = Get-NetSnapshot; Start-Sleep -Seconds 2; $s2 = Get-NetSnapshot
    $dt = ($s2.At - $s1.At).TotalSeconds
    Write-Host (Get-StatusReport)
    Write-Host ("Скорость за 2 с:  ↓ {0}   ↑ {1}" -f (Format-Speed (($s2.Rx-$s1.Rx)/$dt)), (Format-Speed (($s2.Tx-$s1.Tx)/$dt)))
    exit 0
}

# ---------------- GUI (трей) ----------------
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, Microsoft.VisualBasic

# один экземпляр
$mutex = New-Object System.Threading.Mutex($false, "Global\$AppName")
if (-not $mutex.WaitOne(0, $false)) {
    [System.Windows.Forms.MessageBox]::Show('net-monitor уже запущен (значок в трее).', $AppName) | Out-Null
    exit 0
}

$cfg = Load-Config

function New-TrayIcon([string]$state, [bool]$vpn) {
    # state: green|orange|red|gray
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
    if ($vpn) {
        $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(0, 120, 255)), 2
        $g.DrawEllipse($pen, 1, 1, 14, 14)
        $pen.Dispose()
    }
    $brush.Dispose(); $g.Dispose()
    $h = $bmp.GetHicon()
    [System.Drawing.Icon]::FromHandle($h)
}

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = New-TrayIcon 'gray' $false
$notify.Text = 'net-monitor: запуск…'
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miStatus  = $menu.Items.Add('Статус сети…')
$miSite    = $menu.Items.Add('Через что идёт сайт…')
$miBench   = $menu.Items.Add('Тест DNS-серверов…')
$miAuto    = New-Object System.Windows.Forms.ToolStripMenuItem('Авто-тест DNS раз в час + ставить лучший')
$miAuto.CheckOnClick = $true
$miAuto.Checked = [bool]$cfg.autoDns
[void]$menu.Items.Add($miAuto)
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
$script:lastState = ''
$script:lastInetOk = $null

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000

$timer.Add_Tick({
    try {
        $snap = Get-NetSnapshot
        $dt = ($snap.At - $script:prev.At).TotalSeconds
        if ($dt -le 0) { $dt = 5 }
        $down = ($snap.Rx - $script:prev.Rx) / $dt
        $up   = ($snap.Tx - $script:prev.Tx) / $dt
        if ($down -lt 0) { $down = 0 }; if ($up -lt 0) { $up = 0 }
        $script:prev = $snap

        $inet = Test-Internet
        $vpn  = Get-VpnState
        $state = if ($inet.IpOk -and $inet.DnsOk) { 'green' } elseif ($inet.IpOk -or $inet.DnsOk) { 'orange' } else { 'red' }

        $key = "$state|$($vpn.Active)"
        if ($key -ne $script:lastState) {
            $old = $notify.Icon
            $notify.Icon = New-TrayIcon $state $vpn.Active
            if ($old) { $old.Dispose() }
            $script:lastState = $key
        }

        $inetOk = ($state -eq 'green')
        if ($null -ne $script:lastInetOk -and $inetOk -ne $script:lastInetOk) {
            if ($inetOk) { $notify.ShowBalloonTip(3000, 'Интернет восстановился', 'Якоря снова отвечают.', 'Info') }
            else {
                $why = if ($inet.IpOk) { 'IP-связь есть, DNS молчит' } elseif ($inet.DnsOk) { 'DNS жив, IP-якорь молчит' } else { 'не отвечает ничего' }
                $notify.ShowBalloonTip(5000, 'Проблема с интернетом', $why, 'Warning')
            }
            Write-Log "смена состояния: inetOk=$inetOk"
        }
        $script:lastInetOk = $inetOk

        $dns = @(Get-CurrentDns) -join ','
        $vtxt = if ($vpn.Active) { if ($vpn.DefaultVia) { 'VPN:весь' } else { 'VPN:часть' } } else { 'VPN:нет' }
        $txt = "v {0}  ^ {1} | {2} | DNS {3}" -f (Format-Speed $down), (Format-Speed $up), $vtxt, $dns
        if ($txt.Length -gt 63) { $txt = $txt.Substring(0, 63) }   # лимит NotifyIcon.Text
        $notify.Text = $txt
    } catch { Write-Log "tick error: $_" }
})

# часовой авто-DNS
$dnsTimer = New-Object System.Windows.Forms.Timer
$dnsTimer.Interval = 3600 * 1000
$dnsTimer.Add_Tick({
    if (-not $miAuto.Checked) { return }
    try {
        $rows = Invoke-DnsBench
        $best = $rows | Where-Object { $_.Ms -ne $null -and $_.Answers -ge 2 } | Sort-Object Ms | Select-Object -First 2
        if (-not $best) { Write-Log 'авто-DNS: ни один сервер не ответил, ничего не меняю'; return }
        $curr = @(Get-CurrentDns)
        $want = @($best | ForEach-Object { $_.Ip })
        if ($curr.Count -ge 1 -and $curr[0] -eq $want[0]) { Write-Log "авто-DNS: лучший уже стоит ($($want[0]))"; return }
        if (-not (Test-IsAdmin)) { Write-Log 'авто-DNS: нет прав администратора, пропуск (запусти от админа)'; return }
        $msg = Apply-Dns $want
        $notify.ShowBalloonTip(4000, 'Авто-DNS', $msg, 'Info')
    } catch { Write-Log "auto-dns error: $_" }
})
$dnsTimer.Start()

# ---- обработчики меню ----
$miStatus.Add_Click({
    $f = New-Object System.Windows.Forms.Form
    $f.Text = 'Статус сети'
    $f.Size = New-Object System.Drawing.Size(620, 440)
    $f.StartPosition = 'CenterScreen'
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Multiline = $true; $tb.ReadOnly = $true; $tb.ScrollBars = 'Vertical'
    $tb.Dock = 'Fill'; $tb.Font = New-Object System.Drawing.Font('Consolas', 10)
    $tb.Text = (Get-StatusReport) -replace "`n", "`r`n"
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = 'Обновить'; $btn.Dock = 'Bottom'
    $btn.Add_Click({ $tb.Text = (Get-StatusReport) -replace "`n", "`r`n" })
    $f.Controls.Add($tb); $f.Controls.Add($btn)
    $f.Show()
})

$miSite.Add_Click({
    $site = [Microsoft.VisualBasic.Interaction]::InputBox('Адрес сайта (без https://), например: гоньба.рф или github.com', 'Через что идёт сайт', '')
    if ($site) {
        $site = $site -replace '^https?://', '' -replace '/.*$', ''
        $v = Get-SiteRouteVerdict $site
        [System.Windows.Forms.MessageBox]::Show($v, 'Маршрут сайта') | Out-Null
    }
})

$miBench.Add_Click({
    $f = New-Object System.Windows.Forms.Form
    $f.Text = 'Тест DNS-серверов (среднее по: ' + ($BenchDomains -join ', ') + ')'
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
        $lv.Items.Clear()
        $f.Cursor = 'WaitCursor'
        [System.Windows.Forms.Application]::DoEvents()
        $script:benchRows = Invoke-DnsBench
        foreach ($r in ($script:benchRows | Sort-Object { if ($_.Ms -eq $null) { 99999 } else { $_.Ms } })) {
            $it = New-Object System.Windows.Forms.ListViewItem($r.Name)
            [void]$it.SubItems.Add($r.Ip)
            [void]$it.SubItems.Add($(if ($r.Ms -ne $null) { "$($r.Ms)" } else { '—' }))
            [void]$it.SubItems.Add("$($r.Answers)/$($BenchDomains.Count)")
            if ($r.Ms -eq $null) { $it.ForeColor = [System.Drawing.Color]::Gray }
            [void]$lv.Items.Add($it)
        }
        $f.Cursor = 'Default'
    }
    $bRun.Add_Click($doRun)
    $bApply.Add_Click({
        $ip = $null
        if ($lv.SelectedItems.Count -gt 0) { $ip = $lv.SelectedItems[0].SubItems[1].Text }
        elseif ($lv.Items.Count -gt 0) { $ip = $lv.Items[0].SubItems[1].Text }
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

$miAuto.Add_Click({
    $cfg.autoDns = $miAuto.Checked
    Save-Config $cfg
    if ($miAuto.Checked -and -not (Test-IsAdmin)) {
        [System.Windows.Forms.MessageBox]::Show('Авто-смена DNS требует прав администратора. Тест будет идти, но применение — пропускаться. Перезапусти от администратора (пункт меню).', $AppName) | Out-Null
    }
})

$miRun.Add_Click({
    if ($miRun.Checked) {
        $cmd = Join-Path $PSScriptRoot 'Запустить.cmd'
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
    [System.Windows.Forms.Application]::Exit()
})

Write-Log 'запуск'
$timer.Start()
# первый тик сразу, не через 5 с
$timer.GetType().GetMethod('OnTick', [System.Reflection.BindingFlags]'NonPublic,Instance').Invoke($timer, @([System.EventArgs]::Empty)) | Out-Null

[System.Windows.Forms.Application]::Run()
$mutex.ReleaseMutex()
