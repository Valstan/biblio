# kaspersky-matrica.ps1 — настройка Касперского под «Матрица РМЗ» (2026-08-26)
#
# Что делает: находит на ЭТОМ компьютере Касперского, клиент Матрицы, сторож (watchdog)
# и адрес прод-сервера; печатает/показывает ГОТОВЫЕ строки для внесения в Касперский,
# рендерит инструкцию под реальные пути и умеет штатный экспорт/импорт настроек
# Касперского (avp.com EXPORT/IMPORT) — единственный поддерживаемый способ перенести
# конфигурацию на другие компьютеры.
#
# ЧЕГО ОН НАМЕРЕННО НЕ ДЕЛАЕТ (и не может — это факт продукта, а не лень автора):
#   * не добавляет исключения молча. У потребительского Касперского НЕТ команды
#     «добавить исключение» — ни CLI, ни реестра, ни файла конфигурации;
#   * не пытается «переписать настройки, пока защита выключена»: Самозащита Касперского
#     — отдельный механизм, она НЕ выключается вместе с защитой в реальном времени и
#     блокирует правку своих файлов/реестра извне в любом состоянии защиты.
#   Проверено на Kaspersky Standard 21.26: `avp.com HELP` → есть EXPORT/IMPORT,
#   команд EXCLUSION/TRUSTED/ADD нет.
#
# Режимы:
#   kaspersky-matrica.ps1                    — окно с готовыми строками + запись инструкции
#   kaspersky-matrica.ps1 -Quiet             — то же в консоль, без окна
#   kaspersky-matrica.ps1 -Json              — машинный вывод (для встраивания в Матрицу)
#   kaspersky-matrica.ps1 -Verify            — проверка состояния (что цело, что пропало)
#   kaspersky-matrica.ps1 -Export <файл.cfg> — сохранить эталон настроек Касперского
#   kaspersky-matrica.ps1 -Import <файл.cfg> — применить эталон на этом компьютере
#   -SettingsPassword <пароль>               — если в Касперском стоит пароль на настройки
#
# Запуск: Запустить.cmd (или powershell -sta -ep bypass -file kaspersky-matrica.ps1)
[CmdletBinding()]
param(
    [switch]$Quiet,
    [switch]$Json,
    [switch]$Verify,
    [string]$Export,
    [string]$Import,
    [string]$SettingsPassword,
    [string]$Report
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

# Запасной адрес: берётся, только если рукопожатие клиента недоступно. Тот же дефолт,
# что зашит в самом клиенте (electron-app/src/main/index.ts: MATRICA_API_URL ?? …).
$DefaultApiBaseUrl = 'https://a6fd55b8e0ae.vps.myjino.ru'
# IP НАМЕРЕННО не зашит: он определяется DNS-запросом по имени хоста в момент запуска.
# Причина не косметическая — репозиторий публичный, а IP-литерал прод-сервера это
# recon-деталь (правило D-038 портфеля: адреса в тексты публичных репо не кладём).
# Побочная польза: при переезде сервера инструмент не врёт устаревшим адресом.
$KnownProdIps      = @()

# --------------------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------------------

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-FirstExistingPath {
    param([string[]]$Candidates)
    foreach ($c in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        if (Test-Path -LiteralPath $c) { return $c }
    }
    ''
}

function ConvertTo-WinPath {
    # Приложение отдаёт userData как ...\Roaming\@matricarmz/electron-app — прямой слэш
    # приходит из имени пакета. Windows такой путь откроет, а Касперский сверяет строку
    # исключения текстом: со слэшем правило молча не совпадёт. Плюс схлопываем путь к
    # родителю-вендору (@matricarmz), чтобы одно исключение накрыло и electron-app, и
    # его CDP-двойники (electron-app-cdp-9222), которые плодит смоук.
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $p = $Path.Replace('/', '\').TrimEnd('\')
    $parent = Split-Path -Parent $p
    if ($parent -and (Split-Path -Leaf $parent).StartsWith('@')) { return $parent }
    $p
}

function ConvertTo-UserMask {
    # C:\Users\<имя>\AppData\Local\... -> C:\Users\*\AppData\Local\...
    # Маска нужна, чтобы одно исключение подходило любому пользователю на любом компе.
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $profileRoot = Split-Path -Parent $env:USERPROFILE   # обычно C:\Users
    $leaf = Split-Path -Leaf $env:USERPROFILE            # имя пользователя
    if ($Path -like "$env:USERPROFILE*") {
        return ($profileRoot + '\*' + $Path.Substring($env:USERPROFILE.Length))
    }
    $Path -replace [regex]::Escape("\$leaf\"), '\*\'
}

# --------------------------------------------------------------------------------------
# Обнаружение: Касперский
# --------------------------------------------------------------------------------------

function Get-KasperskyInfo {
    $result = [ordered]@{
        Found = $false; Name = ''; Version = ''; InstallDir = ''; AvpCom = ''
        Running = $false; Edition = ''
    }
    $uninstallRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $entry = $null
    foreach ($root in $uninstallRoots) {
        $found = Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
                 Where-Object { $_.PSObject.Properties['DisplayName'] -and
                                $_.DisplayName -match 'Kaspersky|Касперск' } |
                 Select-Object -First 1
        if ($found) { $entry = $found; break }
    }
    if ($entry) {
        $result.Found   = $true
        $result.Name    = [string]$entry.DisplayName
        if ($entry.PSObject.Properties['DisplayVersion']) { $result.Version = [string]$entry.DisplayVersion }
        if ($entry.PSObject.Properties['InstallLocation']) { $result.InstallDir = [string]$entry.InstallLocation }
    }

    # Точная редакция и корень продукта — из ветки продукта (надёжнее Uninstall).
    foreach ($base in @('HKLM:\SOFTWARE\WOW6432Node\KasperskyLab', 'HKLM:\SOFTWARE\KasperskyLab')) {
        $avpKeys = Get-ChildItem -Path $base -ErrorAction SilentlyContinue |
                   Where-Object { $_.PSChildName -like 'AVP*' }
        foreach ($k in $avpKeys) {
            $env_ = Get-ItemProperty -Path (Join-Path $k.PSPath 'environment') -ErrorAction SilentlyContinue
            if ($env_) {
                if ($env_.PSObject.Properties['ProductName'])    { $result.Edition = [string]$env_.ProductName }
                if ($env_.PSObject.Properties['ProductVersion'] -and -not $result.Version) {
                    $result.Version = [string]$env_.ProductVersion
                }
                if ($env_.PSObject.Properties['ProductRoot'] -and (Test-Path -LiteralPath ([string]$env_.ProductRoot))) {
                    $result.InstallDir = [string]$env_.ProductRoot
                    $result.Found = $true
                }
            }
        }
    }

    if ($result.InstallDir) {
        $candidate = Join-Path $result.InstallDir 'avp.com'
        if (Test-Path -LiteralPath $candidate) { $result.AvpCom = $candidate }
    }
    if (-not $result.AvpCom) {
        foreach ($root in @("${env:ProgramFiles(x86)}\Kaspersky Lab", "$env:ProgramFiles\Kaspersky Lab")) {
            if (-not (Test-Path -LiteralPath $root)) { continue }
            $hit = Get-ChildItem -Path $root -Recurse -Filter 'avp.com' -ErrorAction SilentlyContinue |
                   Select-Object -First 1
            if ($hit) { $result.AvpCom = $hit.FullName; $result.Found = $true; break }
        }
    }
    $result.Running = [bool](Get-Process -Name 'avp' -ErrorAction SilentlyContinue)
    [pscustomobject]$result
}

# --------------------------------------------------------------------------------------
# Обнаружение: Матрица РМЗ, сторож, прод-адрес
# --------------------------------------------------------------------------------------

function Get-MatricaInfo {
    $localAppData = $env:LOCALAPPDATA
    $appData      = $env:APPDATA
    $info = [ordered]@{
        HandshakeFound = $false; HandshakePath = ''; HandshakeAgeHours = $null
        AppExe = ''; AppDir = ''; AppVersion = ''
        WatchdogExe = ''; WatchdogDir = ''
        DataDir = ''; UserDataDir = ''; UpdatesDir = ''
        ApiBaseUrl = ''; ApiHost = ''; ApiIps = @()
        Tasks = @(); Source = ''
    }

    # 1) Источник истины — рукопожатие, которое пишет само приложение.
    $hsPath = Join-Path (Join-Path $appData 'MatricaRMZ') 'watchdog.json'
    $info.HandshakePath = $hsPath
    if (Test-Path -LiteralPath $hsPath) {
        try {
            $hs = Get-Content -LiteralPath $hsPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $info.HandshakeFound = $true
            $info.Source = 'handshake'
            foreach ($pair in @(
                @('appExePath', 'AppExe'), @('userDataDir', 'UserDataDir'),
                @('updatesRootDir', 'UpdatesDir'), @('apiBaseUrl', 'ApiBaseUrl'),
                @('version', 'AppVersion'))) {
                if ($hs.PSObject.Properties[$pair[0]]) { $info[$pair[1]] = [string]$hs.($pair[0]) }
            }
            if ($hs.PSObject.Properties['updatedAtMs'] -and $hs.updatedAtMs) {
                $updated = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]$hs.updatedAtMs).LocalDateTime
                $info.HandshakeAgeHours = [math]::Round(((Get-Date) - $updated).TotalHours, 1)
            }
        } catch {
            $info.HandshakeFound = $false
        }
    }

    # 2) Реестр (HKCU InstallLocation), 3) стандартные пути — если рукопожатия нет.
    if (-not $info.AppExe) {
        $regHit = Get-ItemProperty -Path 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
                  Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -match 'MatricaRMZ|Матрица' } |
                  Select-Object -First 1
        if ($regHit -and $regHit.PSObject.Properties['InstallLocation'] -and $regHit.InstallLocation) {
            $candidate = Join-Path ([string]$regHit.InstallLocation) 'MatricaRMZ.exe'
            if (Test-Path -LiteralPath $candidate) { $info.AppExe = $candidate; $info.Source = 'registry' }
        }
    }
    if (-not $info.AppExe) {
        $info.AppExe = Get-FirstExistingPath @(
            (Join-Path $localAppData 'Programs\MatricaRMZ\MatricaRMZ.exe'),
            (Join-Path $localAppData 'Programs\@matricarmzelectron-app\MatricaRMZ.exe')
        )
        if ($info.AppExe) { $info.Source = 'standard-path' }
    }
    if ($info.AppExe) { $info.AppDir = Split-Path -Parent $info.AppExe }

    # Сторож ставится приложением/инсталлятором в фиксированное место.
    $wdDir = Join-Path $localAppData 'Programs\MatricaRMZ-Watchdog'
    $wdExe = Join-Path $wdDir 'matricarmz-watchdog.exe'
    $info.WatchdogDir = $wdDir
    if (Test-Path -LiteralPath $wdExe) { $info.WatchdogExe = $wdExe }

    $dataDir = Join-Path $appData 'MatricaRMZ'
    if (Test-Path -LiteralPath $dataDir) { $info.DataDir = $dataDir } else { $info.DataDir = $dataDir }

    # Прод-адрес: из рукопожатия, иначе значение по умолчанию из кода клиента.
    if (-not $info.ApiBaseUrl) { $info.ApiBaseUrl = $DefaultApiBaseUrl }
    try {
        $uri = [Uri]$info.ApiBaseUrl
        $info.ApiHost = $uri.Host
    } catch { $info.ApiHost = '' }
    if ($info.ApiHost) {
        try {
            $info.ApiIps = @([System.Net.Dns]::GetHostAddresses($info.ApiHost) |
                Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                ForEach-Object { $_.IPAddressToString })
        } catch { $info.ApiIps = @() }
    }
    if (-not $info.ApiIps -or $info.ApiIps.Count -eq 0) { $info.ApiIps = $KnownProdIps }

    # Плановые задачи сторожа (их тоже сносил антивирусный свип — см. историю проекта).
    foreach ($taskName in @('MatricaRMZ\Watchdog Logon', 'MatricaRMZ\Watchdog Periodic')) {
        $exists = $false
        try {
            $null = & "$env:SystemRoot\System32\schtasks.exe" /Query /TN $taskName 2>$null
            $exists = ($LASTEXITCODE -eq 0)
        } catch { $exists = $false }
        $info.Tasks += [pscustomobject]@{ Name = $taskName; Exists = $exists }
    }

    [pscustomobject]$info
}

# --------------------------------------------------------------------------------------
# Сборка плана настройки (что именно вносить в Касперский)
# --------------------------------------------------------------------------------------

function Get-ExclusionPlan {
    param($Matrica)
    $trusted = @()
    foreach ($exe in @($Matrica.AppExe, $Matrica.WatchdogExe)) {
        if ($exe) { $trusted += ($exe.Replace('/', '\')) }
    }
    # UserDataDir (база SQLite, логи, файл ключа) — самая пишущая папка приложения:
    # без неё исключения выглядят полными, а постоянное сканирование остаётся.
    $folders = @()
    foreach ($raw in @($Matrica.AppDir, $Matrica.WatchdogDir, $Matrica.DataDir,
                       $Matrica.UserDataDir, $Matrica.UpdatesDir)) {
        $d = ConvertTo-WinPath $raw
        if ($d -and ($folders -notcontains $d)) { $folders += $d }
    }
    $masks = @()
    foreach ($d in $folders) {
        $m = ConvertTo-UserMask $d
        if ($m -and ($masks -notcontains $m)) { $masks += $m }
    }
    [pscustomobject]@{
        TrustedApps    = $trusted
        ExcludeFolders = $folders
        ExcludeMasks   = $masks
        NetworkHost    = $Matrica.ApiHost
        NetworkIps     = $Matrica.ApiIps
        NetworkPort    = 443
        TrustedFlags   = @(
            'Не проверять открываемые файлы',
            'Не контролировать активность программы',
            'Не наследовать ограничения родительского процесса',
            'Не контролировать активность дочерних программ',
            'Разрешить взаимодействие с интерфейсом программы',
            'Не проверять сетевой трафик'
        )
    }
}

# --------------------------------------------------------------------------------------
# Рендер инструкции под реальные пути
# --------------------------------------------------------------------------------------

function Write-Guide {
    param($Kav, $Matrica, $Plan, [string]$OutPath)
    $tpl = Join-Path $PSScriptRoot 'guide.ru.md'
    if (-not (Test-Path -LiteralPath $tpl)) { return '' }
    $text = Get-Content -LiteralPath $tpl -Raw -Encoding UTF8
    $map = @{
        '{{KAV_NAME}}'      = $(if ($Kav.Edition) { $Kav.Edition } elseif ($Kav.Name) { $Kav.Name } else { 'не найден' })
        '{{KAV_VERSION}}'   = $(if ($Kav.Version) { $Kav.Version } else { '—' })
        '{{AVP}}'           = $(if ($Kav.AvpCom) { $Kav.AvpCom } else { 'не найден' })
        '{{APP_EXE}}'       = $Matrica.AppExe
        '{{APP_DIR}}'       = $Matrica.AppDir
        '{{WD_EXE}}'        = $Matrica.WatchdogExe
        '{{WD_DIR}}'        = $Matrica.WatchdogDir
        '{{DATA_DIR}}'      = $Matrica.DataDir
        '{{PROD_HOST}}'     = $Matrica.ApiHost
        '{{PROD_IP}}'       = ($Matrica.ApiIps -join ', ')
        '{{APP_DIR_MASK}}'  = (ConvertTo-UserMask $Matrica.AppDir)
        '{{WD_DIR_MASK}}'   = (ConvertTo-UserMask $Matrica.WatchdogDir)
        '{{DATA_DIR_MASK}}' = (ConvertTo-UserMask $Matrica.DataDir)
        '{{EXCLUDE_LIST}}'  = ($Plan.ExcludeFolders -join "`r`n")
        '{{MASK_LIST}}'     = ($Plan.ExcludeMasks -join "`r`n")
        '{{TRUSTED_LIST}}'  = ($Plan.TrustedApps -join "`r`n")
    }
    foreach ($k in $map.Keys) { $text = $text.Replace($k, [string]$map[$k]) }
    if (-not $OutPath) {
        $dir = Join-Path $env:LOCALAPPDATA 'kaspersky-matrica'
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $OutPath = Join-Path $dir 'Инструкция-Касперский-Матрица.md'
    }
    Set-Content -LiteralPath $OutPath -Value $text -Encoding UTF8
    $OutPath
}

# --------------------------------------------------------------------------------------
# Экспорт / импорт настроек Касперского (штатный avp.com)
# --------------------------------------------------------------------------------------

function Invoke-AvpSettings {
    param(
        [ValidateSet('EXPORT', 'IMPORT')][string]$Action,
        [string]$File,
        $Kav,
        [string]$Password
    )
    if (-not $Kav.AvpCom) { throw 'avp.com не найден — Касперский не установлен или установлен нестандартно.' }
    if ($Action -eq 'IMPORT' -and -not (Test-Path -LiteralPath $File)) {
        throw "Файл эталона не найден: $File"
    }
    if (-not (Test-IsAdmin)) {
        throw 'Нужны права администратора. Запусти «Запустить-от-администратора.cmd» либо PowerShell от имени администратора.'
    }
    $argList = @($Action)
    if ($Password) { $argList += "/password=$Password" }
    $argList += $File
    Write-Host "Выполняю: `"$($Kav.AvpCom)`" $Action <файл>" -ForegroundColor Cyan
    $out = & $Kav.AvpCom @argList 2>&1
    $code = $LASTEXITCODE
    $out | ForEach-Object { Write-Host "  $_" }
    if ($code -ne 0) {
        throw "avp.com $Action завершился с кодом $code. Если в Касперском включён пароль на управление настройками — добавь -SettingsPassword."
    }
    Write-Host "Готово: $Action выполнен ($File)" -ForegroundColor Green
}

# --------------------------------------------------------------------------------------
# Проверка состояния
# --------------------------------------------------------------------------------------

function Invoke-VerifyState {
    param($Kav, $Matrica)
    $lines = @()
    $problems = 0

    $lines += "=== Проверка состояния  $(Get-Date -Format 'yyyy-MM-dd HH:mm') ==="
    if ($Kav.Found) {
        $run = if ($Kav.Running) { 'работает' } else { 'установлен, служба не запущена' }
        $lines += "Касперский: $($Kav.Edition) $($Kav.Version) — $run"
    } else {
        $lines += 'Касперский: НЕ НАЙДЕН (настраивать нечего)'
    }

    if ($Matrica.AppExe) {
        $lines += "Матрица: на месте — $($Matrica.AppExe)"
    } else {
        $lines += 'Матрица: ФАЙЛ НЕ НАЙДЕН — приложение не установлено ЛИБО удалено антивирусом'
        $problems++
    }
    if ($Matrica.WatchdogExe) {
        $lines += "Сторож: на месте — $($Matrica.WatchdogExe)"
    } else {
        $lines += 'Сторож: ФАЙЛ НЕ НАЙДЕН — типичный признак, что антивирус его забрал (Go-бинарь без подписи)'
        $problems++
    }
    foreach ($t in $Matrica.Tasks) {
        if ($t.Exists) { $lines += "Плановая задача «$($t.Name)»: есть" }
        else { $lines += "Плановая задача «$($t.Name)»: ОТСУТСТВУЕТ"; $problems++ }
    }
    if ($Matrica.HandshakeFound) {
        $age = if ($null -ne $Matrica.HandshakeAgeHours) { "$($Matrica.HandshakeAgeHours) ч назад" } else { 'время неизвестно' }
        $lines += "Рукопожатие приложения: есть (обновлено $age)"
    } else {
        $lines += 'Рукопожатие приложения: нет (приложение ни разу не стартовало на этом пользователе)'
    }

    # Сеть до прода — TCP:443, без DNS-зависимости, если IP уже известен.
    $target = if ($Matrica.ApiIps -and $Matrica.ApiIps.Count -gt 0) { $Matrica.ApiIps[0] } else { $Matrica.ApiHost }
    if ($target) {
        $ok = $false
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $iar = $client.BeginConnect($target, 443, $null, $null)
            $ok = $iar.AsyncWaitHandle.WaitOne(3000) -and $client.Connected
        } catch { $ok = $false } finally { $client.Close() }
        # $($target):443 — не "$target:443": двоеточие после имени переменной PowerShell
        # разбирает как указание области видимости, и подстановка молча даёт пустоту.
        if ($ok) { $lines += "Связь с сервером ($($target):443): есть" }
        else { $lines += "Связь с сервером ($($target):443): НЕТ ОТВЕТА (может быть сеть, VPN или блокировка)"; $problems++ }
    }

    $lines += ''
    $lines += 'ВАЖНО про границу проверки: прочитать список исключений Касперского программно'
    $lines += 'НЕЛЬЗЯ — он не отдаёт их ни через командную строку, ни через реестр. Поэтому'
    $lines += 'проверка отвечает на вопрос «всё ли на месте и работает», а НЕ «внесены ли'
    $lines += 'исключения». Единственное честное подтверждение исключений — увидеть их в окне'
    $lines += 'Касперского (Настройки → Исключения и действия при обнаружении угроз).'
    $lines += ''
    $lines += $(if ($problems -eq 0) { 'ИТОГ: проблем не обнаружено.' } else { "ИТОГ: проблем — $problems (см. выше)." })
    ,$lines
}

# --------------------------------------------------------------------------------------
# Окно с готовыми строками
# --------------------------------------------------------------------------------------

function Show-Window {
    param($Kav, $Matrica, $Plan, [string]$GuidePath)

    Add-Type -AssemblyName System.Windows.Forms, System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Касперский × Матрица РМЗ — что внести в исключения'
    $form.Size = New-Object System.Drawing.Size(940, 680)
    $form.StartPosition = 'CenterScreen'
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

    $head = New-Object System.Windows.Forms.Label
    $head.Dock = 'Top'; $head.Height = 74; $head.Padding = New-Object System.Windows.Forms.Padding(10, 8, 10, 0)
    $kavText = if ($Kav.Found) { "$($Kav.Edition) $($Kav.Version)" } else { 'НЕ НАЙДЕН на этом компьютере' }
    $head.Text = "Касперский: $kavText`r`n" +
                 "Матрица: $(if ($Matrica.AppExe) { $Matrica.AppExe } else { 'не найдена (установи приложение и запусти это окно снова)' })`r`n" +
                 "Сервер: $($Matrica.ApiHost)  (IP: $($Matrica.ApiIps -join ', '))   Порт: 443"

    $panel = New-Object System.Windows.Forms.TableLayoutPanel
    $panel.Dock = 'Fill'; $panel.ColumnCount = 2; $panel.AutoScroll = $true
    $panel.Padding = New-Object System.Windows.Forms.Padding(10)
    [void]$panel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$panel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 120)))

    $script:allText = New-Object System.Text.StringBuilder

    function Add-Section {
        param([string]$Title, [string]$Hint)
        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text = $Title; $lbl.AutoSize = $true
        $lbl.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
        $lbl.Margin = New-Object System.Windows.Forms.Padding(0, 14, 0, 2)
        $panel.Controls.Add($lbl, 0, $panel.RowCount); $panel.SetColumnSpan($lbl, 2)
        $panel.RowCount++
        if ($Hint) {
            $h = New-Object System.Windows.Forms.Label
            $h.Text = $Hint; $h.AutoSize = $true; $h.ForeColor = [System.Drawing.Color]::DimGray
            $h.MaximumSize = New-Object System.Drawing.Size(860, 0)
            $panel.Controls.Add($h, 0, $panel.RowCount); $panel.SetColumnSpan($h, 2)
            $panel.RowCount++
        }
        [void]$script:allText.AppendLine("### $Title")
    }

    function Add-Row {
        param([string]$Value)
        if ([string]::IsNullOrWhiteSpace($Value)) { return }
        $tb = New-Object System.Windows.Forms.TextBox
        $tb.Text = $Value; $tb.ReadOnly = $true; $tb.Width = 760
        $tb.Font = New-Object System.Drawing.Font('Consolas', 9)
        $btn = New-Object System.Windows.Forms.Button
        $btn.Text = 'Копировать'; $btn.Width = 110
        $captured = $Value
        $btn.Add_Click({
            try { [System.Windows.Forms.Clipboard]::SetText($captured) } catch {}
        }.GetNewClosure())
        $panel.Controls.Add($tb, 0, $panel.RowCount)
        $panel.Controls.Add($btn, 1, $panel.RowCount)
        $panel.RowCount++
        [void]$script:allText.AppendLine($Value)
    }

    Add-Section 'A. Доверенные программы' ('Настройки Касперского → Настройки безопасности → «Исключения и действия при обнаружении угроз» → «Указать доверенные программы» → Добавить. Для каждой поставь все галочки: ' + ($Plan.TrustedFlags -join '; ') + '.')
    foreach ($t in $Plan.TrustedApps) { Add-Row $t }
    if ($Plan.TrustedApps.Count -eq 0) { Add-Row '(не найдено — установи Матрицу и открой это окно снова)' }

    Add-Section 'B. Исключения — папки этого компьютера' 'Там же → «Управление исключениями» → Добавить → поле «Файл или папка».'
    foreach ($f in $Plan.ExcludeFolders) { Add-Row $f }

    Add-Section 'B2. Те же исключения масками (для эталона на весь парк)' 'Маска не привязана к имени пользователя — подойдёт на любом компьютере. Вноси их вместо B, если готовишь эталонный файл настроек для других компов.'
    foreach ($m in $Plan.ExcludeMasks) { Add-Row $m }

    Add-Section 'C. Сеть — если не поставил галочку «Не проверять сетевой трафик»' 'Настройки → Сетевой экран → «Настроить пакетные правила» → Добавить разрешающее правило: протокол TCP, удалённый адрес и порт ниже.'
    foreach ($ip in $Plan.NetworkIps) { Add-Row "$ip" }
    Add-Row '443'
    if ($Plan.NetworkHost) { Add-Row $Plan.NetworkHost }

    $bottom = New-Object System.Windows.Forms.FlowLayoutPanel
    $bottom.Dock = 'Bottom'; $bottom.Height = 96; $bottom.Padding = New-Object System.Windows.Forms.Padding(10, 8, 10, 8)

    $bCopyAll = New-Object System.Windows.Forms.Button
    $bCopyAll.Text = 'Копировать всё'; $bCopyAll.Width = 140; $bCopyAll.Height = 30
    $bCopyAll.Add_Click({ try { [System.Windows.Forms.Clipboard]::SetText($script:allText.ToString()) } catch {} })

    $bGuide = New-Object System.Windows.Forms.Button
    $bGuide.Text = 'Открыть инструкцию'; $bGuide.Width = 160; $bGuide.Height = 30
    $capturedGuide = $GuidePath
    $bGuide.Add_Click({ if ($capturedGuide -and (Test-Path -LiteralPath $capturedGuide)) { Start-Process notepad $capturedGuide } }.GetNewClosure())

    $bKav = New-Object System.Windows.Forms.Button
    $bKav.Text = 'Открыть Касперский'; $bKav.Width = 160; $bKav.Height = 30
    $kavUi = if ($Kav.InstallDir) { Join-Path $Kav.InstallDir 'avpui.exe' } else { '' }
    $bKav.Enabled = [bool]($kavUi -and (Test-Path -LiteralPath $kavUi))
    $bKav.Add_Click({ try { Start-Process $kavUi } catch {} }.GetNewClosure())

    $bVerify = New-Object System.Windows.Forms.Button
    $bVerify.Text = 'Проверить состояние'; $bVerify.Width = 160; $bVerify.Height = 30
    $bVerify.Add_Click({
        $report = (Invoke-VerifyState -Kav $Kav -Matrica $Matrica) -join "`r`n"
        [System.Windows.Forms.MessageBox]::Show($report, 'Проверка состояния') | Out-Null
    }.GetNewClosure())

    $bExport = New-Object System.Windows.Forms.Button
    $bExport.Text = 'Сохранить эталон…'; $bExport.Width = 150; $bExport.Height = 30
    $bExport.Add_Click({
        $dlg = New-Object System.Windows.Forms.SaveFileDialog
        $dlg.Filter = 'Настройки Касперского (*.cfg)|*.cfg|Все файлы (*.*)|*.*'
        $dlg.FileName = 'matrica-kaspersky-baseline.cfg'
        if ($dlg.ShowDialog() -eq 'OK') {
            try {
                Invoke-AvpSettings -Action 'EXPORT' -File $dlg.FileName -Kav $Kav -Password $SettingsPassword
                [System.Windows.Forms.MessageBox]::Show("Эталон сохранён:`r`n$($dlg.FileName)`r`n`r`nНа другом компьютере примени его кнопкой «Применить эталон…» (нужны права администратора и та же версия Касперского).", 'Готово') | Out-Null
            } catch {
                [System.Windows.Forms.MessageBox]::Show([string]$_, 'Не получилось') | Out-Null
            }
        }
    }.GetNewClosure())

    $bImport = New-Object System.Windows.Forms.Button
    $bImport.Text = 'Применить эталон…'; $bImport.Width = 150; $bImport.Height = 30
    $bImport.Add_Click({
        $warn = [System.Windows.Forms.MessageBox]::Show(
            "Импорт ЗАМЕНИТ ВСЕ настройки Касперского на этом компьютере снимком из файла (не только исключения Матрицы).`r`n`r`nВерсия Касперского должна совпадать с той, где делался эталон.`r`n`r`nПродолжить?",
            'Подтверждение', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning)
        if ($warn -ne 'Yes') { return }
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Filter = 'Настройки Касперского (*.cfg;*.dat)|*.cfg;*.dat|Все файлы (*.*)|*.*'
        if ($dlg.ShowDialog() -eq 'OK') {
            try {
                Invoke-AvpSettings -Action 'IMPORT' -File $dlg.FileName -Kav $Kav -Password $SettingsPassword
                [System.Windows.Forms.MessageBox]::Show('Эталон применён.', 'Готово') | Out-Null
            } catch {
                [System.Windows.Forms.MessageBox]::Show([string]$_, 'Не получилось') | Out-Null
            }
        }
    }.GetNewClosure())

    $note = New-Object System.Windows.Forms.Label
    $note.Text = 'Настройки вносишь ты — Касперский не даёт менять их программно (Самозащита не выключается вместе с защитой).'
    $note.AutoSize = $true; $note.ForeColor = [System.Drawing.Color]::DimGray
    $note.Margin = New-Object System.Windows.Forms.Padding(4, 8, 0, 0)

    $bottom.Controls.AddRange(@($bCopyAll, $bGuide, $bKav, $bVerify, $bExport, $bImport, $note))

    $form.Controls.Add($panel)
    $form.Controls.Add($bottom)
    $form.Controls.Add($head)

    # Иначе окно открывается прокрученным вниз (фокус уезжает на первое поле ввода)
    # и заголовок первого раздела не виден — читается как «список начинается сразу».
    $form.Add_Shown({
        $form.ActiveControl = $bCopyAll
        $panel.AutoScrollPosition = New-Object System.Drawing.Point(0, 0)
    }.GetNewClosure())

    [void]$form.ShowDialog()
}

# --------------------------------------------------------------------------------------
# Главный поток
# --------------------------------------------------------------------------------------

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$kav = Get-KasperskyInfo
$matrica = Get-MatricaInfo
$plan = Get-ExclusionPlan -Matrica $matrica

if ($Export) { Invoke-AvpSettings -Action 'EXPORT' -File $Export -Kav $kav -Password $SettingsPassword; return }
if ($Import) { Invoke-AvpSettings -Action 'IMPORT' -File $Import -Kav $kav -Password $SettingsPassword; return }

if ($Json) {
    [pscustomobject]@{
        generatedAt = (Get-Date).ToString('o')
        kaspersky   = $kav
        matrica     = $matrica
        plan        = $plan
        canAutoApply = $false
        autoApplyReason = 'Consumer Kaspersky exposes no CLI/registry/file API to add an exclusion; Self-Defense blocks external config tampering independently of real-time protection state. Supported automation: avp.com EXPORT/IMPORT of the whole settings blob.'
    } | ConvertTo-Json -Depth 6
    return
}

if ($Verify) {
    (Invoke-VerifyState -Kav $kav -Matrica $matrica) | ForEach-Object { Write-Host $_ }
    return
}

$guidePath = Write-Guide -Kav $kav -Matrica $matrica -Plan $plan -OutPath $Report

if ($Quiet) {
    Write-Host "=== Доверенные программы ==="
    $plan.TrustedApps | ForEach-Object { Write-Host "  $_" }
    Write-Host "=== Исключения (папки) ==="
    $plan.ExcludeFolders | ForEach-Object { Write-Host "  $_" }
    Write-Host "=== Те же исключения масками (для эталона на парк) ==="
    $plan.ExcludeMasks | ForEach-Object { Write-Host "  $_" }
    Write-Host "=== Сеть ==="
    Write-Host "  хост: $($plan.NetworkHost)   IP: $($plan.NetworkIps -join ', ')   порт: $($plan.NetworkPort)"
    if ($guidePath) { Write-Host "`nИнструкция: $guidePath" }
    return
}

Show-Window -Kav $kav -Matrica $matrica -Plan $plan -GuidePath $guidePath
