# probe.ps1 — индикаторы состояния сети и канала Claude Code.
#
# ЖЁСТКИЙ ИНВАРИАНТ: этот файл только ЧИТАЕТ состояние. Ни одной команды, меняющей
# систему, здесь быть не должно — тогда диагностику можно гонять в любой момент.
# Всё, что меняет настройки, живёт в recovery.ps1.

# ---------------- эталон выхода ----------------
# Ожидаемая страна выхода. Сверка с ней — защита от случайной смены страны при
# переподключении VPN. **Пустая строка = не следить за страной**, и это умолчание:
# на машине без VPN «страна не та» — не авария, а просто место, где человек живёт.
#
# Проверять надо именно СТРАНУ, а не диапазон адресов: замер 2026-08-26 показал, что
# клиент VPN держит несколько узлов и переключается между ними сам (за одну сессию
# выход сменился с одного адреса на другой — оба Германия). Привязка к префиксу давала
# бы ложную тревогу на каждом штатном переключении.
$script:EgressExpectedCountry = ''

# Режимы наблюдения. 'auto' — определить по машине, 'on' — следить всегда,
# 'off' — не следить. Нужны, чтобы утилита не изобретала аварии там, где функции нет:
# без VPN отсутствие туннеля — норма, без Claude Code его канал никого не волнует.
$script:VpnMode    = 'auto'
$script:ClaudeMode = 'auto'

# Запомненный факт «VPN на этой машине есть» (сохраняется в настройках). Нужен, чтобы
# выключение VPN-клиента не выглядело как «VPN тут не настроен»: иначе утилита ровно в
# момент падения туннеля перестала бы следить за страной и утечкой — то есть замолчала
# бы там, где обязана кричать.
$script:VpnSeen = $false

# Локальный HTTP-эндпоинт статистики xray (Go expvar). Даёт счётчики трафика по
# inbound/outbound без единого внешнего запроса.
$script:XrayStatsUrl = 'http://127.0.0.1:11111/debug/vars'

# Признаки VPN-адаптеров (для Get-VpnState). Radmin и прочие «вечно Up» исключены
# на уровне Get-TunnelState; здесь фильтр шире — это просто список имён.
$script:VpnNameRegex = 'wireguard|openvpn|tap-|\btun\b|tunnel|vpn|proton|outline|amnezia|warp|radmin'
$script:VpnExclude   = 'teredo|isatap|6to4|loopback'

# Кандидаты DNS для теста (имя = подпись в таблице) и домены замера.
$script:DnsCandidates = [ordered]@{
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
$script:BenchDomains = @('ya.ru','vk.com','wikipedia.org')
function Get-DnsBenchDomains { $script:BenchDomains }

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
    # Якоря нарочно из разных «миров»: в одних сетях режут западные сервисы, в других —
    # российские. Если бы все якоря были одного происхождения, блокировка именно их
    # выглядела бы как «интернета нет».
    param(
        [string[]]$IpAnchors = @('1.1.1.1','77.88.8.8','8.8.8.8'),
        [string[]]$HostAnchors = @('cloudflare.com','ya.ru')
    )
    $ipOk = $false
    foreach ($a in $IpAnchors) { if (Test-TcpPort $a 443 1200) { $ipOk = $true; break } }
    $dnsOk = $false
    foreach ($h in $HostAnchors) {
        try {
            $ans = Resolve-DnsName -Name $h -Type A -DnsOnly -QuickTimeout -ErrorAction Stop |
                   Where-Object { $_.IPAddress } | Select-Object -First 1
            if ($ans -and (Test-TcpPort $ans.IPAddress 443 1200)) { $dnsOk = $true; break }
        } catch {}
    }
    [pscustomobject]@{ IpOk = $ipOk; DnsOk = $dnsOk; Ok = ($ipOk -and $dnsOk) }
}

# ---------------- 2. tunnel: жив ли туннель VPN ----------------

function Get-TunnelState {
    # Туннельный адаптер ищем по признакам, а не по имени: адаптер пересоздаётся при
    # каждом переподключении клиента, а называться может по-разному у разных клиентов.
    # Radmin/TeamViewer/Hamachi исключены намеренно: их адаптеры постоянно в состоянии
    # Up и никогда не бывают маршрутом по умолчанию — приняв их за туннель, утилита
    # показывала бы «туннель лёг» круглосуточно.
    $tun = Get-NetAdapter -ErrorAction SilentlyContinue |
           Where-Object { $_.Status -eq 'Up' -and
                          (($_.InterfaceDescription + ' ' + $_.Name) -match 'sing-tun|wintun|wireguard|tun\b|tap-|openvpn|amnezia|proton|outline|warp|vpn') -and
                          (($_.InterfaceDescription + ' ' + $_.Name) -notmatch 'radmin|teamviewer|hamachi|virtualbox|vmware|hyper-v|bluetooth') }
    $def = Get-DefaultRouteInfo
    $viaTunnel = $false
    if ($tun -and $def) { $viaTunnel = @($tun.Name) -contains $def.Alias }

    # Соединения ядра VPN наружу — полезная подробность, но НЕ признак здоровья:
    # у WireGuard трафик несёт драйвер ядра, и процесса с сокетами нет вовсе. Если
    # требовать их наличия, живой туннель объявляется мёртвым.
    $core = Get-Process -Name xray, sing-box, v2ray, hysteria, openvpn, wireguard, wg,
                              tun2socks, nekobox, Happ, amneziawg -ErrorAction SilentlyContinue

    # Штатные подключения Windows (IKEv2/L2TP/SSTP) не имеют ни своего процесса, ни
    # адаптера с узнаваемым именем — их видно только через Get-VpnConnection.
    $builtIn = @(Get-VpnConnection -ErrorAction SilentlyContinue) +
               @(Get-VpnConnection -AllUserConnection -ErrorAction SilentlyContinue) |
               Where-Object { $_.ConnectionStatus -eq 'Connected' }
    $upstream = @()
    foreach ($p in $core) {
        $conns = Get-NetTCPConnection -OwningProcess $p.Id -State Established -ErrorAction SilentlyContinue |
                 Where-Object { $_.RemoteAddress -notmatch '^(127\.|::1|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)' }
        foreach ($c in $conns) { $upstream += "$($c.RemoteAddress):$($c.RemotePort)" }
    }
    $upstream = @($upstream | Select-Object -Unique)

    # Ожидается ли здесь VPN. Без этого «туннеля нет» на домашнем компьютере без VPN
    # выглядело бы как поломка — вечный оранжевый значок.
    #
    # Внимание на два слагаемых: заданная страна выхода и запомненный факт «VPN тут
    # был». Именно они удерживают контроль включённым, когда клиент VPN выключился
    # целиком: без них падение туннеля выключало бы надзор за страной ровно в тот
    # момент, когда трафик пошёл напрямую. Системный прокси в признаки НЕ входит —
    # прокси есть у многих и без всякого VPN.
    $configured = switch ($script:VpnMode) {
        'off'     { $false }
        'require' { $true }
        'on'      { $true }
        default   { [bool]($tun -or $core -or $builtIn -or $script:VpnSeen -or $script:EgressExpectedCountry) }
    }

    # Штатное подключение Windows считается поднятым туннелем: адаптера с узнаваемым
    # именем у него может не быть, но связь через него идёт.
    $adapterUp = [bool]($tun -or $builtIn)
    $name = if ($tun) { @($tun.Name)[0] } elseif ($builtIn) { @($builtIn)[0].Name } else { $null }

    [pscustomobject]@{
        Configured  = $configured
        AdapterUp   = $adapterUp
        AdapterName = $name
        ViaTunnel   = ($viaTunnel -or [bool]$builtIn)
        CoreRunning = [bool]$core
        Upstream    = $upstream
        Ok          = ($adapterUp -and ($viaTunnel -or [bool]$builtIn))
    }
}

# ---------------- 3. claude: жив ли канал до Anthropic ----------------

function Test-ClaudePresent {
    # Есть ли на машине Claude Code. Нужно, чтобы не проверять — и уж точно не бить
    # тревогу про — канал сервиса, которым человек не пользуется.
    switch ($script:ClaudeMode) {
        'off' { return $false }
        'on'  { return $true }
    }
    if ($env:CLAUDE_PID -or $env:ANTHROPIC_BASE_URL -or $env:ANTHROPIC_API_KEY) { return $true }
    if (Test-Path (Join-Path $env:USERPROFILE '.claude')) { return $true }
    if (Get-Command claude -ErrorAction SilentlyContinue) { return $true }
    if (Get-Process -Name claude -ErrorAction SilentlyContinue) { return $true }
    $false
}

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
function Set-ExpectedCountry([string]$cc) {
    # Пустое значение допустимо и означает «не следить за страной» — иначе контроль
    # нельзя было бы выключить, только сменить на другую страну.
    $script:EgressExpectedCountry = if ($cc) { $cc.ToUpper() } else { '' }
}
function Set-WatchModes([string]$vpn, [string]$claude) {
    if ($vpn)    { $script:VpnMode    = $vpn }
    if ($claude) { $script:ClaudeMode = $claude }
}
function Set-VpnSeen([bool]$seen) { $script:VpnSeen = $seen }
function Get-VpnSeen { $script:VpnSeen }
function Get-WatchModes { [pscustomobject]@{ Vpn = $script:VpnMode; Claude = $script:ClaudeMode } }

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
    $where = if ($info.City) { "$($info.Country), $($info.City)" } else { $info.Country }

    # Контроль страны не задан — просто сообщаем, где выход. Без ожидания «сменилась»
    # быть не может: человеку без VPN незачем читать, что его страна «не та».
    if (-not $ExpectedCountry) {
        return [pscustomobject]@{ Ok = $true; Verified = $true; Ip = $info.Ip; Country = $info.Country
                                  City = $info.City; Expected = ''
                                  Reason = "выход $($info.Ip) — $where" }
    }

    $match = ($info.Country -eq $ExpectedCountry)
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
    # Функции, которых на машине нет, не могут быть сломаны. Без этого утилита на
    # компьютере без VPN круглосуточно показывала бы «туннель не поднят».
    $vpnWatched = [bool]($Tunnel -and $Tunnel.Configured)

    if ($Egress -and $Egress.Expected -and $Egress.Verified -and -not $Egress.Ok -and $viaTunnel) {
        return [pscustomobject]@{ Class = 'egress-changed'; Severity = 'alarm'
            Text = $Egress.Reason }
    }
    # Утечка: трафик перестал идти в туннель, и наружу при этом что-то уходит.
    # Порог в байтах в секунду, а не в байтах: часть трафика идёт напрямую штатно
    # (так настроена маршрутизация клиента), и накопление за долгую паузу между
    # замерами не должно выглядеть как авария.
    if ($vpnWatched -and -not $viaTunnel -and $Flow -and $Flow.Seconds -gt 0 -and
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
    if ($vpnWatched -and -not $Tunnel.Ok -and $Link.Ok) {
        $why = if (-not $Tunnel.AdapterUp) { 'туннель VPN не поднят' }
               else { 'трафик идёт мимо туннеля VPN' }
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

# ---------------- вспомогательные индикаторы (только чтение) ----------------
# Перенесены сюда из net-monitor.ps1: их гоняет фоновый исполнитель, которому
# доступен только этот модуль. Инвариант файла сохраняется — всё читающее.

function Get-VpnState {
    # 1) штатные VPN-подключения Windows
    $names = @()
    foreach ($v in @(Get-VpnConnection -ErrorAction SilentlyContinue) + @(Get-VpnConnection -AllUserConnection -ErrorAction SilentlyContinue)) {
        if ($v.ConnectionStatus -eq 'Connected') { $names += $v.Name }
    }
    # 2) адаптеры сторонних VPN (WireGuard/OpenVPN/Proton/...)
    $ifaces = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.OperationalStatus -eq 'Up' } |
        Where-Object { ($_.Name + ' ' + $_.Description) -match $script:VpnNameRegex -and ($_.Name + ' ' + $_.Description) -notmatch $script:VpnExclude }
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

function Format-Speed([double]$bps) {
    if ($bps -ge 1MB) { '{0:0.0} МБ/с' -f ($bps/1MB) }
    elseif ($bps -ge 1KB) { '{0:0} КБ/с' -f ($bps/1KB) }
    else { '{0:0} Б/с' -f $bps }
}

function Format-Bytes([double]$b) {
    if ($b -ge 1GB) { '{0:0.00} ГБ' -f ($b/1GB) }
    elseif ($b -ge 1MB) { '{0:0.0} МБ' -f ($b/1MB) }
    elseif ($b -ge 1KB) { '{0:0} КБ' -f ($b/1KB) }
    else { '{0:0} Б' -f $b }
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
    foreach ($k in $script:DnsCandidates.Keys) { if ($curr -notcontains $script:DnsCandidates[$k]) { $list[$k] = $script:DnsCandidates[$k] } }
    foreach ($name in $list.Keys) {
        $ip = $list[$name]
        $times = @()
        foreach ($d in $script:BenchDomains) {
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

# ---------------- готовые отчёты для окон ----------------
# Текст собирается здесь, потому что сбор — это десятки секунд сетевых проверок:
# исполняться они должны в фоне, а окно лишь показывает готовую строку.

function Get-StatusReport {
    param([string]$Version = '')
    $inet = Test-Link
    $vpn  = Get-VpnState
    $def  = Get-DefaultRouteInfo
    $dns  = @(Get-CurrentDns)
    $sb = New-Object System.Text.StringBuilder
    $vtag = if ($Version) { "  ·  net-monitor v$Version" } else { '' }
    [void]$sb.AppendLine("=== Статус сети  $(Get-Date -Format 'HH:mm:ss')$vtag ===")
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

    # --- канал Claude Code и выходная точка ---
    $tun = Get-TunnelState
    if (Test-ClaudePresent) {
        [void]$sb.AppendLine('--- канал Claude Code ---')
        $cc = Test-ClaudeChannel
        [void]$sb.AppendLine("Связь с Anthropic: " + $(if ($cc.Ok) { "ЕСТЬ ($($cc.Reason), $($cc.Ms) мс)" } else { "НЕТ — $($cc.Reason)" }))
        [void]$sb.AppendLine("Путь Claude Code: " + $(if ($cc.Via) { $cc.Via } else { 'напрямую, без прокси' }))
    }
    if ($tun.AdapterUp) {
        [void]$sb.AppendLine("Туннель: $($tun.AdapterName) — " + $(if ($tun.ViaTunnel) { 'трафик идёт через него' } else { 'поднят, но трафик идёт мимо' }))
        if ($tun.Upstream) { [void]$sb.AppendLine("Узлы VPN: " + ($tun.Upstream -join ', ')) }
    } elseif ($tun.Configured) {
        [void]$sb.AppendLine('Туннель: не поднят')
    } else {
        [void]$sb.AppendLine('VPN: на этой машине не обнаружен — контроль туннеля выключен')
    }
    $eg = Test-Egress
    [void]$sb.AppendLine("Выход в интернет: " + $eg.Reason)
    $st = Get-XrayStats
    if ($st) {
        [void]$sb.AppendLine(("Всего через прокси: Claude Code {0}, туннель {1}, мимо туннеля {2}" -f
            (Format-Bytes ($st.HttpIn + $st.HttpOut)), (Format-Bytes ($st.ProxyIn + $st.ProxyOut)), (Format-Bytes ($st.DirectIn + $st.DirectOut))))
    }
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Интерфейсы (Up):')
    foreach ($i in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
             Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' }) {
        $ips = ($i.GetIPProperties().UnicastAddresses | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } | ForEach-Object { $_.Address.ToString() }) -join ', '
        [void]$sb.AppendLine("  $($i.Name) [$($i.NetworkInterfaceType)] $ips")
    }
    $sb.ToString()
}

function Get-ClaudeChannelReport {
    # Отчёт окна «Канал Claude Code». Внутри полторы секунды замера трафика и сетевые
    # проверки — вызывать только из фонового исполнителя.
    $sb = New-Object System.Text.StringBuilder
    $cc  = Test-ClaudeChannel
    $tun = Get-TunnelState
    $eg  = Test-Egress
    $s1 = Get-XrayStats; Start-Sleep -Milliseconds 1500; $s2 = Get-XrayStats
    $fl = Get-FlowDelta $s1 $s2

    [void]$sb.AppendLine("=== Канал Claude Code  $(Get-Date -Format 'HH:mm:ss') ===")
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine("Связь с Anthropic: " + $(if ($cc.Ok) { "ЕСТЬ — $($cc.Reason), отклик $($cc.Ms) мс" } else { "НЕТ — $($cc.Reason)" }))
    [void]$sb.AppendLine("Проверено тем же путём, которым ходит Claude Code: " + $(if ($cc.Via) { $cc.Via } else { 'напрямую' }))
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine("Туннель: " + $(if ($tun.Ok) { "$($tun.AdapterName), трафик идёт через него" }
                                         elseif ($tun.AdapterUp) { "$($tun.AdapterName) поднят, но трафик идёт мимо" }
                                         else { 'не поднят' }))
    if ($tun.Upstream) { [void]$sb.AppendLine("Узлы VPN: " + ($tun.Upstream -join ', ')) }
    [void]$sb.AppendLine("Выход: $($eg.Reason)")
    [void]$sb.AppendLine('')
    if ($fl) {
        [void]$sb.AppendLine("--- за последние $([int]$fl.Seconds) с ---")
        [void]$sb.AppendLine("Трафик Claude Code: $(Format-Bytes $fl.ClaudeBytes)")
        [void]$sb.AppendLine("Через туннель:      $(Format-Bytes $fl.TunnelBytes)")
        # «Утечка» — только когда трафик перестал идти через туннель. Часть трафика
        # штатно ходит напрямую (так настроена маршрутизация клиента), и подписывать
        # это утечкой значило бы пугать владельца при каждой открытой странице.
        $leakMark = if (-not $tun.ViaTunnel -and $fl.DirectBytes -gt 0) { '   ← мимо туннеля!' } else { '' }
        [void]$sb.AppendLine("Мимо туннеля:       $(Format-Bytes $fl.DirectBytes)$leakMark")
        [void]$sb.AppendLine('')
        # Тот самый вопрос: Клод завис или интернет отвалился.
        $answer = if (-not $cc.Ok) { 'СЕТЬ: канал до Anthropic не отвечает — Claude Code ждёт впустую.' }
                  elseif ($fl.ClaudeBytes -gt 0) { 'РАБОТАЕТ: канал жив и токены идут прямо сейчас.' }
                  else { 'КАНАЛ ЖИВ, но обмена нет: либо Claude Code сейчас думает/ждёт вас, либо он подвис — сеть тут не виновата.' }
        [void]$sb.AppendLine("Вердикт: $answer")
    } else {
        [void]$sb.AppendLine('Счётчики xray недоступны (порт 11111 не отвечает) — трафик по процессам не виден.')
    }
    $sb.ToString()
}

function Get-FullHealth {
    param($PrevStats)
    $link   = Test-Link
    $tunnel = Get-TunnelState
    # Канал Claude Code проверяем только если он тут используется: иначе на чужой
    # машине это была бы десятисекундная пауза ради тревоги о ненужном сервисе.
    $claude = if (Test-ClaudePresent) { Test-ClaudeChannel } else { $null }
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
