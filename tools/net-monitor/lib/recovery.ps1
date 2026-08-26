# recovery.ps1 — лестница автовосстановления сети, снапшот «золотой» конфигурации и откат.
#
# Требует загруженного probe.ps1 (индикаторы). Всё, что здесь меняет систему, обязано:
#   1) иметь предусловие — ступень применяется только к своему классу поломки;
#   2) записывать состояние до и после в журнал;
#   3) после выполнения сверять страну выхода — и останавливать всё при расхождении.
#
# ЗАПРЕЩЕНО НАВСЕГДА (инвариант кода, не настройка) — см. Assert-Forbidden:
#   · менять метрики в сторону приоритета физического адаптера: это «чинит интернет»,
#     молча переводя выход на домашний адрес — владелец видит рабочую сеть, а сервер
#     на другом конце видит другую страну;
#   · включать IPv6: туннель его не несёт, включение создаёт путь мимо туннеля;
#   · netsh winsock reset / netsh int ip reset: отката нет, на машине антивирус и
#     драйвер split-tunnel — восстановление ручное;
#   · перезапускать туннельный адаптер напрямую: им владеет ядро VPN;
#   · править конфиги клиента VPN и переключать сервер/подписку — смена страны это
#     решение владельца, а не утилиты.

$script:RecoveryLog = @()

function Add-RecoveryLog {
    param([string]$Step, [string]$Before, [string]$After, [string]$Result)
    $e = [pscustomobject]@{ At = (Get-Date); Step = $Step; Before = $Before; After = $After; Result = $Result }
    $script:RecoveryLog += $e
    if (Get-Command Write-Log -ErrorAction SilentlyContinue) {
        Write-Log ("recovery [{0}] {1} → {2} : {3}" -f $Step, $Before, $After, $Result)
    }
    $e
}

function Get-RecoveryLog { $script:RecoveryLog }

function Test-IsAdminR {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-IsTunnelAdapter {
    # Туннельный адаптер принадлежит ядру VPN: перезапускать его напрямую нельзя.
    param([string]$Name, [string]$Description)
    (("$Name $Description") -match 'sing-tun|wintun|wireguard|tun\b|tap-|openvpn|vpn')
}

function Assert-Forbidden {
    # Явный стоп-лист. Вызывается ступенями перед действием, чтобы запрет нельзя было
    # обойти правкой настроек — только правкой кода, осознанно.
    param([string]$Action)
    $forbidden = @(
        'metric-favor-physical',
        'ipv6-enable',
        'winsock-reset',
        'ip-reset',
        'restart-tunnel-adapter',
        'vpn-switch-server',
        'vpn-edit-config'
    )
    if ($forbidden -contains $Action) {
        throw "Действие '$Action' запрещено конструкцией утилиты (см. шапку recovery.ps1)."
    }
}

# ---------------- снапшот «золотой» конфигурации ----------------

function Get-NetworkFingerprint {
    # Отпечаток сети: снапшот, снятый в одной сети, нельзя применять в другой — иначе
    # утилита пропишет чужие адреса.
    #
    # Опорой служит шлюз ФИЗИЧЕСКОГО адаптера, а не «маршрут по умолчанию»: последний
    # ведёт в туннель, чей шлюз пересоздаётся при каждом переподключении VPN — такой
    # отпечаток не совпал бы сам с собой, и откат не применился бы никогда.
    $gwIp = $null; $gwMac = $null; $dhcp = $null; $profName = $null
    try {
        $physAdapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
                          Where-Object { $_.Status -eq 'Up' } | Select-Object -ExpandProperty ifIndex)
        $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
                 Where-Object { $physAdapters -contains $_.InterfaceIndex -and $_.NextHop -match '^\d' -and $_.NextHop -ne '0.0.0.0' } |
                 Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
        if ($route) {
            $gwIp = $route.NextHop
            $n = Get-NetNeighbor -IPAddress $gwIp -ErrorAction SilentlyContinue |
                 Where-Object { $_.LinkLayerAddress } | Select-Object -First 1
            if ($n) { $gwMac = $n.LinkLayerAddress }
        }
        $cfg = Get-CimInstance Win32_NetworkAdapterConfiguration -ErrorAction SilentlyContinue |
               Where-Object { $_.DHCPEnabled -and $_.DHCPServer } | Select-Object -First 1
        if ($cfg) { $dhcp = $cfg.DHCPServer }
        $p = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($p) { $profName = $p.Name }
    } catch {}
    [pscustomobject]@{ GatewayIp = $gwIp; GatewayMac = $gwMac; DhcpServer = $dhcp; ProfileName = $profName }
}

function New-NetSnapshot {
    # Снимается только читающими командами.
    $adapters = Get-NetAdapter -ErrorAction SilentlyContinue |
        Select-Object Name, InterfaceIndex, InterfaceDescription, Status, MacAddress
    $ifaces = Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Select-Object InterfaceIndex, InterfaceAlias, InterfaceMetric, AutomaticMetric, Dhcp

    # В снимок DNS попадают только физические адаптеры. Туннельный исключён намеренно:
    # его настройки принадлежат VPN-клиенту (правило запрещает их трогать), а его
    # ifIndex меняется при каждом пересоздании — «восстановление по индексу» после
    # переподключения попало бы в чужой интерфейс. Loopback исключён как бессмысленный.
    $physIdx = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ifIndex)
    $dns = Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $physIdx -contains $_.InterfaceIndex } |
        Select-Object InterfaceIndex, InterfaceAlias, ServerAddresses,
                      @{ n = 'Mac'; e = { (Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue).MacAddress } }

    $bind = Get-NetAdapterBinding -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue |
        Select-Object Name, Enabled
    $egress = Test-Egress

    [pscustomobject]@{
        At          = (Get-Date)
        Fingerprint = Get-NetworkFingerprint
        Adapters    = @($adapters)
        Interfaces  = @($ifaces)
        Dns         = @($dns)
        Ipv6Binding = @($bind)
        EgressIp    = $egress.Ip
        Country     = $egress.Country
    }
}

function Test-SnapshotApplicable {
    # Снапшот применим только в той же сети, где снят.
    param($Snapshot)
    if (-not $Snapshot) { return $false }
    $now = Get-NetworkFingerprint
    $a = $Snapshot.Fingerprint
    if (-not $a) { return $false }
    # Пустой отпечаток нельзя считать совпадением: два «ничего» равны друг другу, и
    # снимок из чужой сети прошёл бы проверку.
    if (-not $a.GatewayIp -or -not $a.GatewayMac -or -not $now.GatewayIp -or -not $now.GatewayMac) { return $false }
    ($a.GatewayIp -eq $now.GatewayIp) -and ($a.GatewayMac -eq $now.GatewayMac)
}

# ---------------- ступени лестницы ----------------

function Invoke-StepFlushDns {
    # Ступень 1. Лечит залипшие отрицательные ответы, закэшированные во время обрыва.
    # Не рвёт ни соединений, ни туннеля. Требует прав администратора.
    if (-not (Test-IsAdminR)) {
        return Add-RecoveryLog 'flush-dns' 'кэш DNS' '—' 'ПРОПУСК: нужны права администратора'
    }
    $before = try { (Get-DnsClientCache -ErrorAction SilentlyContinue | Measure-Object).Count } catch { '?' }
    try {
        Clear-DnsClientCache -ErrorAction Stop
        Add-RecoveryLog 'flush-dns' "записей: $before" 'кэш очищен' 'выполнено'
    } catch {
        Add-RecoveryLog 'flush-dns' "записей: $before" '—' "ошибка: $_"
    }
}

function Invoke-StepRestoreDns {
    # Ступень 2. Возвращает DNS из снапшота — точный откат наших же правок.
    param($Snapshot)
    if (-not (Test-IsAdminR)) {
        return Add-RecoveryLog 'restore-dns' '—' '—' 'ПРОПУСК: нужны права администратора'
    }
    if (-not (Test-SnapshotApplicable $Snapshot)) {
        return Add-RecoveryLog 'restore-dns' '—' '—' 'ПРОПУСК: снапшот снят в другой сети'
    }
    foreach ($d in $Snapshot.Dns) {
        $curr = (Get-DnsClientServerAddress -InterfaceIndex $d.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses
        if (($curr -join ',') -eq (($d.ServerAddresses) -join ',')) { continue }
        try {
            if ($d.ServerAddresses -and $d.ServerAddresses.Count -gt 0) {
                Set-DnsClientServerAddress -InterfaceIndex $d.InterfaceIndex -ServerAddresses $d.ServerAddresses -ErrorAction Stop
            } else {
                Set-DnsClientServerAddress -InterfaceIndex $d.InterfaceIndex -ResetServerAddresses -ErrorAction Stop
            }
            Add-RecoveryLog 'restore-dns' "$($d.InterfaceAlias): $($curr -join ',')" ($d.ServerAddresses -join ',') 'выполнено'
        } catch {
            Add-RecoveryLog 'restore-dns' "$($d.InterfaceAlias)" '—' "ошибка: $_"
        }
    }
}

function Invoke-StepPhysicalDns {
    # Ступень 3. Смена DNS на ФИЗИЧЕСКОМ адаптере — и только когда туннель лежит.
    #
    # При живом туннеле эта ступень бессмысленна: имена для проксируемого трафика
    # резолвит ядро VPN своим DoH, а «интерфейс маршрута по умолчанию» — сам туннель,
    # чью настройку клиент перезапишет при переподключении. Осмысленно ровно тогда,
    # когда машина работает напрямую.
    param([string[]]$Servers = @('77.88.8.8','1.1.1.1'))
    if (-not (Test-IsAdminR)) {
        return Add-RecoveryLog 'physical-dns' '—' '—' 'ПРОПУСК: нужны права администратора'
    }
    # Условие — «трафик идёт мимо туннеля», а не «туннель нездоров»: поднятый туннель
    # без связи с узлом всё ещё держит маршрут по умолчанию, и системный DNS в этом
    # состоянии ничего не решает, а правка останется висеть.
    $tun = Get-TunnelState
    if ($tun.ViaTunnel) {
        return Add-RecoveryLog 'physical-dns' 'трафик идёт через туннель' '—' 'ПРОПУСК: при живом туннеле системный DNS на канал не влияет'
    }
    $phys = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1
    if (-not $phys) {
        return Add-RecoveryLog 'physical-dns' '—' '—' 'ПРОПУСК: активный физический адаптер не найден'
    }
    $before = @((Get-DnsClientServerAddress -InterfaceIndex $phys.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses)
    $wasDhcp = [bool]((Get-NetIPInterface -InterfaceIndex $phys.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).Dhcp -eq 'Enabled')

    $restore = {
        # Возврат к исходному состоянию. Для интерфейса на DHCP правильный откат —
        # именно сброс, а не запись прежнего адреса вручную: иначе DNS перестанет быть
        # управляемым по DHCP.
        try {
            if ($wasDhcp -or $before.Count -eq 0) {
                Set-DnsClientServerAddress -InterfaceIndex $phys.ifIndex -ResetServerAddresses -ErrorAction Stop
            } else {
                Set-DnsClientServerAddress -InterfaceIndex $phys.ifIndex -ServerAddresses $before -ErrorAction Stop
            }
            Clear-DnsClientCache -ErrorAction SilentlyContinue
        } catch {}
    }.GetNewClosure()

    try {
        Set-DnsClientServerAddress -InterfaceIndex $phys.ifIndex -ServerAddresses $Servers -ErrorAction Stop
        Clear-DnsClientCache -ErrorAction SilentlyContinue
    } catch {
        return Add-RecoveryLog 'physical-dns' "$($phys.Name)" '—' "ошибка: $_"
    }

    # Проверяем, что резолв действительно заработал. Если нет — откатываем немедленно,
    # не дожидаясь конца лестницы: неудачный резолвер способен убить разрешение имён
    # совсем, а снимка рабочей конфигурации во время аварии может и не оказаться.
    Start-Sleep -Seconds 2
    $link = Test-Link
    if ($link.DnsOk) {
        Add-RecoveryLog 'physical-dns' "$($phys.Name): $($before -join ',')" ($Servers -join ',') 'выполнено, резолв заработал'
    } else {
        & $restore
        Add-RecoveryLog 'physical-dns' "$($phys.Name): $($before -join ',')" ($before -join ',') 'не помогло — вернул как было'
    }
}

function Invoke-StepRestartVpn {
    # Ступень 4. Перезапуск службы VPN-клиента. РВЁТ все проксируемые соединения и,
    # главное, при переподключении клиент может выбрать другой узел — то есть другую
    # страну. Поэтому: страна фиксируется до, сверяется после, и при расхождении вся
    # автоматика останавливается. Утилита не пытается «вернуть как было» — выбор узла
    # не её решение.
    param([string]$ServiceName = 'HappService', [int]$WaitSec = 20)
    if (-not (Test-IsAdminR)) {
        return Add-RecoveryLog 'restart-vpn' '—' '—' 'ПРОПУСК: нужны права администратора'
    }
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) {
        return Add-RecoveryLog 'restart-vpn' '—' '—' "ПРОПУСК: служба $ServiceName не найдена"
    }
    $before = Test-Egress
    try {
        Restart-Service -Name $ServiceName -Force -ErrorAction Stop
    } catch {
        return Add-RecoveryLog 'restart-vpn' "страна: $($before.Country)" '—' "ошибка: $_"
    }
    # Ждём, пока туннель поднимется заново.
    $deadline = (Get-Date).AddSeconds($WaitSec)
    do { Start-Sleep -Seconds 2; $tun = Get-TunnelState } while (-not $tun.Ok -and (Get-Date) -lt $deadline)

    # Проверка страны построена на ПОДТВЕРЖДЕНИИ, а не на отсутствии возражений:
    # «страну определить не удалось» — это повод остановиться и позвать владельца, а не
    # молча записать успех. Иначе недоступный geoip-сервис открывал бы дорогу к работе
    # через чужую страну.
    $after = Test-Egress
    $res = if (-not $tun.Ok) { 'служба перезапущена, туннель ещё не поднялся' }
           elseif (-not $after.Verified) { 'СТОП: страну выхода подтвердить не удалось' }
           elseif (-not $after.Ok) { "СТОП: страна выхода $($after.Country) вместо $($after.Expected)" }
           elseif ($before.Verified -and $before.Country -ne $after.Country) { 'СТОП: страна выхода сменилась' }
           else { "выполнено, выход прежний ($($after.Country))" }
    Add-RecoveryLog 'restart-vpn' "страна: $(if ($before.Verified) { $before.Country } else { 'не определена' })" `
                    "страна: $($after.Country) ($($after.Ip))" $res
}

function Invoke-StepRestartPhysical {
    # Ступень 5. Перезапуск физического адаптера. Рвёт всё, включая канал самого VPN
    # (туннель поднимется заново — с тем же риском смены узла, что и в ступени 4).
    # Только для случая «нет линка или адрес APIPA»: иначе вреда больше, чем пользы.
    param([int]$WaitSec = 25)
    if (-not (Test-IsAdminR)) {
        return Add-RecoveryLog 'restart-physical' '—' '—' 'ПРОПУСК: нужны права администратора'
    }
    $phys = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $phys) {
        return Add-RecoveryLog 'restart-physical' '—' '—' 'ПРОПУСК: физический адаптер не найден'
    }
    # Страховка на случай, если «физическим» окажется туннель: его перезапуск запрещён.
    if (Test-IsTunnelAdapter $phys.Name $phys.InterfaceDescription) {
        Assert-Forbidden 'restart-tunnel-adapter'
    }
    $ip = (Get-NetIPAddress -InterfaceIndex $phys.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress
    $needed = (-not $ip) -or ($ip -match '^169\.254\.') -or ($phys.Status -ne 'Up')
    if (-not $needed) {
        return Add-RecoveryLog 'restart-physical' "$($phys.Name): $ip" '—' 'ПРОПУСК: адрес и линк в порядке, перезапуск не нужен'
    }
    $before = Test-Egress
    try {
        Restart-NetAdapter -Name $phys.Name -Confirm:$false -ErrorAction Stop
    } catch {
        return Add-RecoveryLog 'restart-physical' "$($phys.Name): $ip" '—' "ошибка: $_"
    }
    $deadline = (Get-Date).AddSeconds($WaitSec)
    do { Start-Sleep -Seconds 3; $link = Test-Link } while (-not $link.Ok -and (Get-Date) -lt $deadline)

    # Вернувшаяся связь сама по себе не успех: если туннель не поднялся, машина вышла
    # в интернет напрямую с домашнего адреса. Это авария, а не починка, и объявлять её
    # успехом нельзя — иначе владелец продолжит работать не через ту страну.
    $tun   = Get-TunnelState
    $after = Test-Egress
    $res = if (-not $link.Ok) { 'адаптер перезапущен, связи ещё нет' }
           elseif (-not $tun.ViaTunnel) { 'СТОП: связь вернулась МИМО туннеля — выход не через VPN' }
           elseif (-not $after.Verified) { 'СТОП: страну выхода подтвердить не удалось' }
           elseif (-not $after.Ok) { "СТОП: страна выхода $($after.Country) вместо $($after.Expected)" }
           elseif ($before.Verified -and $before.Country -ne $after.Country) { 'СТОП: страна выхода сменилась' }
           else { "выполнено, выход прежний ($($after.Country))" }
    Add-RecoveryLog 'restart-physical' "$($phys.Name): $ip" "линк: $($link.Ok), туннель: $($tun.ViaTunnel), страна: $($after.Country)" $res
}

# ---------------- оркестрация ----------------

function Get-RecoveryPlan {
    # Ступени выбираются по КЛАССУ поломки, а не проходом сверху вниз: слепой проход
    # добрался бы до перезапуска адаптера при обычной проблеме с DNS.
    param([string]$Class, [bool]$AllowVpnRestart = $false, [bool]$AllowAdapterRestart = $false)
    switch ($Class) {
        'dns-down'    { @('flush-dns','restore-dns','physical-dns') }
        'tunnel-down' {
            $s = @('flush-dns')
            if ($AllowVpnRestart) { $s += 'restart-vpn' }
            $s
        }
        'claude-down' {
            $s = @('flush-dns')
            if ($AllowVpnRestart) { $s += 'restart-vpn' }
            $s
        }
        'no-link' {
            $s = @()
            if ($AllowAdapterRestart) { $s += 'restart-physical' }
            if ($AllowVpnRestart)     { $s += 'restart-vpn' }
            $s
        }
        # Смена страны и утечка НЕ лечатся автоматикой: это решение владельца.
        'egress-changed' { @() }
        'leak'           { @() }
        default          { @() }
    }
}

function Invoke-RecoveryStep {
    # Выполняет ОДИН шаг лестницы. Нужен пошаговый режим, потому что вызывающая
    # сторона — таймер трея: длинная синхронная лестница заморозила бы значок и меню
    # на всё время аварии, ровно когда владельцу нужнее всего на них смотреть.
    param([string]$Step, $Snapshot)
    switch ($Step) {
        'flush-dns'        { Invoke-StepFlushDns }
        'restore-dns'      { Invoke-StepRestoreDns $Snapshot }
        'physical-dns'     { Invoke-StepPhysicalDns }
        'restart-vpn'      { Invoke-StepRestartVpn }
        'restart-physical' { Invoke-StepRestartPhysical }
        default            { Add-RecoveryLog $Step '—' '—' 'неизвестный шаг' }
    }
}

function Invoke-Recovery {
    # Синхронный прогон всей лестницы. Годится для консоли и скриптов; трей использует
    # пошаговый режим (Invoke-RecoveryStep), потому что здесь есть длинные ожидания.
    # Возвращает объект с исходом: что делали, чем закончилось, нужно ли звать владельца.
    param(
        $Snapshot,
        [bool]$AllowVpnRestart = $false,
        [bool]$AllowAdapterRestart = $false,
        [int]$GraceSec = 10,
        [scriptblock]$OnProgress = $null
    )
    $health = Get-FullHealth
    $class  = $health.Verdict.Class

    if ($class -eq 'ok' -or $health.Verdict.Severity -eq 'ok') {
        return [pscustomobject]@{ Started = $false; Class = $class; Steps = @(); Healed = $true
                                  NeedOwner = $false; Text = 'восстановление не требуется' }
    }
    if ($class -in @('egress-changed','leak')) {
        return [pscustomobject]@{ Started = $false; Class = $class; Steps = @(); Healed = $false
                                  NeedOwner = $true; Text = $health.Verdict.Text }
    }

    # Ступень 0: дать сети шанс вернуться самой.
    if ($OnProgress) { & $OnProgress "ждём $GraceSec с — вдруг вернётся само" }
    Start-Sleep -Seconds $GraceSec
    $health = Get-FullHealth ($health.Stats)
    if ($health.Verdict.Severity -eq 'ok') {
        Add-RecoveryLog 'grace' $class 'восстановилось само' 'ничего делать не пришлось'
        return [pscustomobject]@{ Started = $false; Class = $class; Steps = @(); Healed = $true
                                  NeedOwner = $false; Text = 'вернулось само за время ожидания' }
    }

    $class = $health.Verdict.Class
    $plan  = Get-RecoveryPlan $class $AllowVpnRestart $AllowAdapterRestart
    if ($plan.Count -eq 0) {
        return [pscustomobject]@{ Started = $false; Class = $class; Steps = @(); Healed = $false
                                  NeedOwner = $true
                                  Text = "для случая «$($health.Verdict.Text)» разрешённых действий нет" }
    }

    $done = @()
    foreach ($step in $plan) {
        if ($OnProgress) { & $OnProgress "шаг: $step" }
        $entry = Invoke-RecoveryStep $step $Snapshot
        $done += $step
        if ($entry -and $entry.Result -like 'СТОП*') {
            return [pscustomobject]@{ Started = $true; Class = $class; Steps = $done; Healed = $false
                                      NeedOwner = $true; Text = $entry.Result }
        }
        Start-Sleep -Seconds 3
        $health = Get-FullHealth
        if ($health.Verdict.Severity -eq 'ok') {
            return [pscustomobject]@{ Started = $true; Class = $class; Steps = $done; Healed = $true
                                      NeedOwner = $false; Text = "починилось после шага «$step»" }
        }
        # Авария выхода, возникшая по ходу лестницы, прекращает её немедленно: дальше
        # лечить нечего, а следующий шаг может усугубить.
        if ($health.Verdict.Severity -eq 'alarm') {
            return [pscustomobject]@{ Started = $true; Class = $health.Verdict.Class; Steps = $done; Healed = $false
                                      NeedOwner = $true; Text = "остановлено: $($health.Verdict.Text)" }
        }
    }

    # Лестница пройдена без успеха — возвращаем известное хорошее состояние, чтобы не
    # оставлять систему в состоянии наших полумер.
    if ($Snapshot) {
        if ($OnProgress) { & $OnProgress 'не помогло — откат к сохранённой конфигурации' }
        Invoke-StepRestoreDns $Snapshot
    }
    [pscustomobject]@{ Started = $true; Class = $class; Steps = $done; Healed = $false
                       NeedOwner = $true; Text = 'лестница пройдена, не помогло — откатились к сохранённой конфигурации' }
}
