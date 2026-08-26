# probe.ps1 — индикаторы состояния сети и канала Claude Code.
#
# ЖЁСТКИЙ ИНВАРИАНТ: этот файл только ЧИТАЕТ состояние. Ни одной команды, меняющей
# систему, здесь быть не должно — тогда диагностику можно гонять в любой момент.
# Всё, что меняет настройки, живёт в recovery.ps1.

# ---------------- эталон выхода ----------------
# Ожидаемая страна выхода. Сверка с ней — защита от случайной смены страны при
# переподключении VPN.
#
# Проверять надо именно СТРАНУ, а не диапазон адресов: замер 2026-08-26 показал, что
# клиент Happ держит несколько узлов и переключается между ними сам (за одну сессию
# выход сменился с 103.109.234.211 на 185.136.243.42 — оба Германия). Привязка к
# префиксу давала бы ложную тревогу на каждом штатном переключении.
$script:EgressExpectedCountry = 'DE'

# Локальный HTTP-эндпоинт статистики xray (Go expvar). Даёт счётчики трафика по
# inbound/outbound без единого внешнего запроса.
$script:XrayStatsUrl = 'http://127.0.0.1:11111/debug/vars'

# Сервис определения выходного адреса и его страны. ipinfo.io отдаёт country и city
# и на замере 2026-08-26 ответил верно (DE, Frankfurt); ifconfig.co на том же адресе
# выдал заведомо неверную страну, поэтому в резерве стоит только определение самого
# IP, без страны.
$script:EgressGeoUrl = 'https://ipinfo.io/json'
$script:EgressIpUrls = @('https://api.ipify.org', 'https://ifconfig.me/ip')

# Кэш «адрес → страна», чтобы не дёргать geoip на каждой проверке.
$script:EgressCache = @{}

function Test-TcpPort([string]$target, [int]$port = 443, [int]$timeoutMs = 1500) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect($target, $port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($timeoutMs) -and $c.Connected) { return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

function Get-DefaultRouteInfo {
    $r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
         Sort-Object -Property { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
    if ($r) { [pscustomobject]@{ IfIndex = $r.InterfaceIndex; Alias = $r.InterfaceAlias } }
}

function Get-ProxyEndpoint {
    # Путь, которым реально ходит Claude Code. Источник истины — переменные окружения
    # его процесса; на этой машине это http://127.0.0.1:10809 (HTTP-инбаунд xray).
    foreach ($v in @($env:HTTPS_PROXY, $env:https_proxy, $env:HTTP_PROXY, $env:http_proxy)) {
        if ($v) { return $v }
    }
    # Резерв: системный прокси пользователя (WinINET)
    try {
        $s = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
        if ($s.ProxyEnable -eq 1 -and $s.ProxyServer) {
            $p = $s.ProxyServer
            if ($p -notmatch '^https?://') { $p = "http://$p" }
            return $p
        }
    } catch {}
    $null
}

function Get-AnthropicBaseUrl {
    if ($env:ANTHROPIC_BASE_URL) { return $env:ANTHROPIC_BASE_URL.TrimEnd('/') }
    'https://api.anthropic.com'
}

# ---------------- 1. link: есть ли интернет вообще ----------------

function Test-Link {
    param([string[]]$IpAnchors = @('77.88.8.8','1.1.1.1'), [string]$HostAnchor = 'ya.ru')
    $ipOk = $false
    foreach ($a in $IpAnchors) { if (Test-TcpPort $a 443 1500) { $ipOk = $true; break } }
    $dnsOk = $false
    try {
        $ans = Resolve-DnsName -Name $HostAnchor -Type A -DnsOnly -QuickTimeout -ErrorAction Stop |
               Where-Object { $_.IPAddress } | Select-Object -First 1
        if ($ans -and (Test-TcpPort $ans.IPAddress 443 1500)) { $dnsOk = $true }
    } catch {}
    [pscustomobject]@{ IpOk = $ipOk; DnsOk = $dnsOk; Ok = ($ipOk -and $dnsOk) }
}

# ---------------- 2. tunnel: жив ли туннель VPN ----------------

function Get-TunnelState {
    # Туннельный адаптер ищем по признакам sing-tun/Wintun, а не по жёсткому имени:
    # адаптер пересоздаётся при каждом переподключении клиента.
    $tun = Get-NetAdapter -ErrorAction SilentlyContinue |
           Where-Object { $_.Status -eq 'Up' -and
                          (($_.InterfaceDescription + ' ' + $_.Name) -match 'sing-tun|wintun|wireguard|tun\b|tap-|openvpn') }
    $def = Get-DefaultRouteInfo
    $viaTunnel = $false
    if ($tun -and $def) { $viaTunnel = @($tun.Name) -contains $def.Alias }

    # Внешний канал держит не адаптер, а процесс ядра (xray/sing-box): его
    # установленные соединения наружу — прямое доказательство живого uplink.
    $core = Get-Process -Name xray, sing-box -ErrorAction SilentlyContinue
    $upstream = @()
    foreach ($p in $core) {
        $conns = Get-NetTCPConnection -OwningProcess $p.Id -State Established -ErrorAction SilentlyContinue |
                 Where-Object { $_.RemoteAddress -notmatch '^(127\.|::1|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)' }
        foreach ($c in $conns) { $upstream += "$($c.RemoteAddress):$($c.RemotePort)" }
    }
    $upstream = $upstream | Select-Object -Unique

    [pscustomobject]@{
        AdapterUp   = [bool]$tun
        AdapterName = if ($tun) { @($tun.Name)[0] } else { $null }
        ViaTunnel   = $viaTunnel
        CoreRunning = [bool]$core
        Upstream    = $upstream
        Ok          = ([bool]$tun -and $viaTunnel -and $upstream.Count -gt 0)
    }
}

# ---------------- 3. claude: жив ли канал до Anthropic ----------------

function Test-ClaudeChannel {
    # Единственная честная проверка: идёт ТЕМ ЖЕ путём, что и Claude Code — через его
    # прокси. Ключ не нужен: 401 от Anthropic означает, что весь путь TCP→TLS→HTTP
    # работает, и ничего секретного при этом не отправляется.
    #
    # Почему не ping и не счёт соединений: TUN-драйвер отвечает на ICMP сам (0 мс даже
    # при мёртвом канале), а соединения Claude Code всегда идут на loopback-прокси и
    # остаются Established при полном обрыве.
    param([int]$TimeoutSec = 10)
    $url   = (Get-AnthropicBaseUrl) + '/v1/models'
    $proxy = Get-ProxyEndpoint
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $p = @{ Uri = $url; TimeoutSec = $TimeoutSec; UseBasicParsing = $true; ErrorAction = 'Stop' }
        if ($proxy) { $p.Proxy = $proxy }
        $r = Invoke-WebRequest @p
        $sw.Stop()
        return [pscustomobject]@{ Ok = $true; Status = $r.StatusCode; Ms = [int]$sw.ElapsedMilliseconds; Via = $proxy; Reason = 'ответ получен' }
    } catch {
        $sw.Stop()
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        # Важно, ЧЕЙ это ответ. Запрос идёт через прокси VPN-клиента, и когда тот не
        # может выйти наружу, он отвечает сам — своим 502/503/407. Принять такой ответ
        # за живой канал значило бы показывать «всё хорошо» при мёртвом туннеле.
        # Поэтому успехом считаются только коды, которые реально приходят от API.
        if ($code -in @(200, 401, 403, 429)) {
            return [pscustomobject]@{ Ok = $true; Status = $code; Ms = [int]$sw.ElapsedMilliseconds; Via = $proxy
                                      Reason = "HTTP $code от Anthropic — канал жив" }
        }
        if ($code) {
            return [pscustomobject]@{ Ok = $false; Status = $code; Ms = [int]$sw.ElapsedMilliseconds; Via = $proxy
                                      Reason = "HTTP $code — так отвечает прокси, а не Anthropic: канал наружу не работает" }
        }
        return [pscustomobject]@{ Ok = $false; Status = $null; Ms = [int]$sw.ElapsedMilliseconds; Via = $proxy; Reason = $_.Exception.Message }
    }
}

# ---------------- 4. egress: в той ли мы стране ----------------

function Get-EgressInfo {
    # Возвращает выходной адрес и его страну. Запрашивается только собственный адрес —
    # никакие данные наружу не отправляются. Идёт через тот же прокси, что Claude Code,
    # чтобы видеть именно его выходную точку.
    #
    # Таймаут намеренно короткий: функцию вызывает таймер трея, и три медленных
    # запроса подряд заморозили бы значок на десятки секунд.
    param([int]$TimeoutSec = 4)
    $proxy = Get-ProxyEndpoint
    $mk = { param($p) if ($proxy) { $p.Proxy = $proxy }; $p }

    try {
        $p = & $mk @{ Uri = $script:EgressGeoUrl; TimeoutSec = $TimeoutSec; UseBasicParsing = $true; ErrorAction = 'Stop' }
        $j = (Invoke-WebRequest @p).Content | ConvertFrom-Json
        if ($j.ip) {
            if ($j.country) { $script:EgressCache[$j.ip] = $j.country }
            return [pscustomobject]@{ Ip = $j.ip; Country = $j.country; City = $j.city; Org = $j.org }
        }
    } catch {}

    # Резерв: узнаём хотя бы адрес. Страну берём из кэша, если этот адрес уже видели.
    foreach ($u in $script:EgressIpUrls) {
        try {
            $p = & $mk @{ Uri = $u; TimeoutSec = $TimeoutSec; UseBasicParsing = $true; ErrorAction = 'Stop' }
            $ip = (Invoke-WebRequest @p).Content.Trim()
            if ($ip -match '^\d{1,3}(\.\d{1,3}){3}$') {
                return [pscustomobject]@{ Ip = $ip; Country = $script:EgressCache[$ip]; City = $null; Org = $null }
            }
        } catch {}
    }
    $null
}

function Get-ExpectedCountry { $script:EgressExpectedCountry }
function Set-ExpectedCountry([string]$cc) { if ($cc) { $script:EgressExpectedCountry = $cc.ToUpper() } }

function Test-Egress {
    # Три состояния, и путать их нельзя:
    #   Verified=$false          — страну узнать не удалось. Это НЕ подтверждение:
    #                              шаги, рискующие сменить узел, обязаны его требовать,
    #                              иначе «неизвестно» стало бы способом обойти защиту.
    #   Verified=$true, Ok=$true — страна проверена и совпадает.
    #   Verified=$true, Ok=$false — страна проверена и НЕ совпадает: тревога.
    param([string]$ExpectedCountry = $script:EgressExpectedCountry)
    $info = Get-EgressInfo
    if (-not $info) {
        return [pscustomobject]@{ Ok = $false; Verified = $false; Ip = $null; Country = $null
                                  Expected = $ExpectedCountry; Reason = 'выходной адрес определить не удалось' }
    }
    if (-not $info.Country) {
        return [pscustomobject]@{ Ok = $false; Verified = $false; Ip = $info.Ip; Country = $null
                                  Expected = $ExpectedCountry
                                  Reason = "выход $($info.Ip), страну проверить не удалось" }
    }
    $match = ($info.Country -eq $ExpectedCountry)
    $where = if ($info.City) { "$($info.Country), $($info.City)" } else { $info.Country }
    [pscustomobject]@{
        Ok       = $match
        Verified = $true
        Ip       = $info.Ip
        Country  = $info.Country
        City     = $info.City
        Expected = $ExpectedCountry
        Reason   = if ($match) { "выход $($info.Ip) — $where, как и ожидалось" }
                   else { "СТРАНА ВЫХОДА СМЕНИЛАСЬ: $($info.Ip) — $where вместо $ExpectedCountry" }
    }
}

# ---------------- 5. flow: идёт ли трафик на самом деле ----------------

function Get-XrayStats {
    # Счётчики кумулятивные. inbound.http — порт, которым ходит Claude Code;
    # outbound.proxy — трафик через туннель; рост outbound.direct означает, что часть
    # трафика пошла мимо туннеля, то есть утечку.
    try {
        $j = (Invoke-WebRequest -Uri $script:XrayStatsUrl -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop).Content | ConvertFrom-Json
        if (-not $j.stats) { return $null }
        [pscustomobject]@{
            HttpIn      = [int64]$j.stats.inbound.http.uplink
            HttpOut     = [int64]$j.stats.inbound.http.downlink
            ProxyIn     = [int64]$j.stats.outbound.proxy.uplink
            ProxyOut    = [int64]$j.stats.outbound.proxy.downlink
            DirectIn    = [int64]$j.stats.outbound.direct.uplink
            DirectOut   = [int64]$j.stats.outbound.direct.downlink
            At          = (Get-Date)
        }
    } catch { $null }
}

function Get-FlowDelta {
    param($Prev, $Curr)
    if (-not $Prev -or -not $Curr) { return $null }
    $sec = ($Curr.At - $Prev.At).TotalSeconds
    if ($sec -le 0) { return $null }
    [pscustomobject]@{
        ClaudeBytes = [math]::Max(0, ($Curr.HttpIn - $Prev.HttpIn) + ($Curr.HttpOut - $Prev.HttpOut))
        TunnelBytes = [math]::Max(0, ($Curr.ProxyIn - $Prev.ProxyIn) + ($Curr.ProxyOut - $Prev.ProxyOut))
        DirectBytes = [math]::Max(0, ($Curr.DirectIn - $Prev.DirectIn) + ($Curr.DirectOut - $Prev.DirectOut))
        Seconds     = $sec
    }
}

# ---------------- сводный вердикт ----------------

function Get-HealthVerdict {
    # Классифицирует поломку. Именно класс, а не «плохо/хорошо», определяет, какую
    # ступень восстановления имеет смысл применять.
    param($Link, $Tunnel, $Claude, $Egress, $Flow)

    # Порядок здесь принципиален. «Страна сменилась» объявляется ТОЛЬКО когда трафик
    # действительно идёт через туннель — иначе упавший туннель (трафик пошёл напрямую,
    # выход стал домашним) выглядел бы как смена узла VPN. Это разные аварии: первую
    # лечить нельзя вообще, вторую лечит перезапуск VPN.
    $viaTunnel = [bool]($Tunnel -and $Tunnel.ViaTunnel)

    if ($Egress -and $Egress.Verified -and -not $Egress.Ok -and $viaTunnel) {
        return [pscustomobject]@{ Class = 'egress-changed'; Severity = 'alarm'
            Text = $Egress.Reason }
    }
    # Утечка: трафик перестал идти в туннель, и наружу при этом что-то уходит.
    # Порог в байтах в секунду, а не в байтах: часть трафика идёт напрямую штатно
    # (так настроена маршрутизация клиента), и накопление за долгую паузу между
    # замерами не должно выглядеть как авария.
    if (-not $viaTunnel -and $Flow -and $Flow.Seconds -gt 0 -and
        (($Flow.DirectBytes / $Flow.Seconds) -gt 20KB)) {
        return [pscustomobject]@{ Class = 'leak'; Severity = 'alarm'
            Text = 'трафик пошёл МИМО туннеля — выход с домашнего адреса' }
    }
    if (-not $Link.IpOk -and -not $Link.DnsOk) {
        return [pscustomobject]@{ Class = 'no-link'; Severity = 'down'
            Text = 'нет связи вообще: ни один якорь не отвечает' }
    }
    if ($Link.IpOk -and -not $Link.DnsOk) {
        return [pscustomobject]@{ Class = 'dns-down'; Severity = 'partial'
            Text = 'IP-связь есть, DNS молчит' }
    }
    if ($Tunnel -and -not $Tunnel.Ok -and $Link.Ok) {
        $why = if (-not $Tunnel.AdapterUp) { 'туннель не поднят' }
               elseif (-not $Tunnel.ViaTunnel) { 'туннель поднят, но трафик идёт мимо него' }
               else { 'туннель поднят, но связи с узлом VPN нет' }
        return [pscustomobject]@{ Class = 'tunnel-down'; Severity = 'partial'
            Text = "интернет есть, но $why" }
    }
    if ($Claude -and -not $Claude.Ok) {
        return [pscustomobject]@{ Class = 'claude-down'; Severity = 'partial'
            Text = "канал до Anthropic не отвечает ($($Claude.Reason))" }
    }
    if ($Claude -and $Claude.Ok -and $Flow -and $Flow.ClaudeBytes -eq 0) {
        return [pscustomobject]@{ Class = 'claude-idle'; Severity = 'ok'
            Text = 'канал жив, но Claude Code сейчас ничего не шлёт' }
    }
    [pscustomobject]@{ Class = 'ok'; Severity = 'ok'; Text = 'всё работает' }
}

function Get-FullHealth {
    param($PrevStats)
    $link   = Test-Link
    $tunnel = Get-TunnelState
    $claude = Test-ClaudeChannel
    $stats  = Get-XrayStats
    $flow   = Get-FlowDelta $PrevStats $stats
    $egress = if ($link.Ok) { Test-Egress } else { $null }
    [pscustomobject]@{
        Link = $link; Tunnel = $tunnel; Claude = $claude; Egress = $egress
        Stats = $stats; Flow = $flow
        Verdict = (Get-HealthVerdict $link $tunnel $claude $egress $flow)
        At = (Get-Date)
    }
}
