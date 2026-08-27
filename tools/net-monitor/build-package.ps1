# build-package.ps1 — собрать архив net-monitor для передачи другому человеку.
#
# Зачем не EXE: утилита меняет DNS, умеет перезапускать сетевой адаптер и службу VPN и
# просит права администратора. Ровно такое поведение антивирусы и ищут, а самодельный
# неподписанный EXE вдобавок к этому поведению добавляет подозрительную упаковку и
# нулевую репутацию у SmartScreen. В виде скриптов поведение приписывается подписанному
# powershell.exe, а получатель может прочитать, что именно он запускает, — для утилиты с
# такими правами это не приятная мелочь, а условие, без которого её незачем запускать.
#
# Запуск:  powershell -NoProfile -ExecutionPolicy Bypass -File build-package.ps1
# Один файл вместо папки lib:  ... -Single

param(
    [switch]$Folder,          # оставить lib\*.ps1 отдельными файлами
    [string]$OutDir = ''      # куда положить архив (по умолчанию — рядом со скриптом)
)

# По умолчанию собираем ОДНИМ файлом: получатель распаковывает архив вручную, и папку
# lib при этом легко не заметить — а без неё скрипт откажется работать. Ключ -Folder
# оставляет модули раздельными (удобнее для правок, но требует аккуратной распаковки).
$Single = -not $Folder

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
if (-not $OutDir) { $OutDir = $here }

# Версия релиза — из константы главного скрипта (единственного места, где она
# задаётся). Она попадает в имя архива: получатель и владелец всегда видят, ЧТО
# именно за файл у них лежит, а установщик покажет её при обновлении.
$verMatch = [regex]::Match([System.IO.File]::ReadAllText((Join-Path $here 'net-monitor.ps1'), [System.Text.Encoding]::UTF8),
                           "(?m)^\`$AppVersion\s*=\s*'([^']+)'")
if (-not $verMatch.Success) { throw "В net-monitor.ps1 не нашлась строка `$AppVersion = '...' — сборщик не знает версию." }
$version = $verMatch.Groups[1].Value

$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("net-monitor-pkg-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pkgDir = Join-Path $stage 'net-monitor'
New-Item -ItemType Directory -Path $pkgDir -Force | Out-Null

# Всё дальнейшее — в try/finally: при ошибке сборки временная папка иначе остаётся в
# %TEMP% (проверено: несколько неудачных прогонов оставляли за собой мусор).
try {

# Файлы .ps1 обязаны уехать в UTF-8 с BOM: утилита запускается через powershell 5.1,
# который без BOM читает файл как ANSI — кириллица ломается, и скрипт молча перестаёт
# работать. Это уже случалось, поэтому здесь не «на всякий случай», а обязательный шаг.
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
function Copy-WithBom([string]$src, [string]$dst) {
    $text = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($dst, $text, $utf8Bom)
}

if ($Single) {
    # Склейка: вместо загрузки модулей с диска вставляем их текст here-string'ом в
    # $script:LibSource. Так папку lib нельзя потерять при распаковке, а фоновый
    # исполнитель получает тот же текст модулей, что и главный поток. Результат —
    # обычный читаемый .ps1, не «сборка».
    $main = [System.IO.File]::ReadAllText((Join-Path $here 'net-monitor.ps1'), [System.Text.Encoding]::UTF8)
    $libText = (@(foreach ($m in @('probe.ps1', 'recovery.ps1')) {
        "# --- начало lib\$m (вставлено сборщиком) ---" + [Environment]::NewLine +
        [System.IO.File]::ReadAllText((Join-Path $here "lib\$m"), [System.Text.Encoding]::UTF8) + [Environment]::NewLine +
        "# --- конец lib\$m ---"
    }) -join [Environment]::NewLine)
    # Текст модулей не должен содержать строки-терминатора here-string — иначе
    # склеенный файл оборвётся посреди модуля и перестанет парситься.
    if ($libText -match "(?m)^'@") { throw "В модулях встретилась строка '@ — here-string склейки сломается." }
    $inline = '$script:LibSource = @''' + [Environment]::NewLine + $libText + [Environment]::NewLine + '''@' +
              [Environment]::NewLine + '. ([scriptblock]::Create($script:LibSource))'
    # Заменяем весь блок загрузки модулей (от $script:LibSource = '' до выполнения).
    $loader = [regex]'(?ms)^\$script:LibSource = ''''\r?\n.*?^\. \(\[scriptblock\]::Create\(\$script:LibSource\)\)'
    $m = $loader.Match($main)
    if (-not $m.Success) {
        throw 'Не нашёл блок загрузки модулей в net-monitor.ps1 — сборщик устарел, поправьте регулярное выражение.'
    }
    # Склейка строк по индексам, а не Regex.Replace: в тексте замены полно знаков $
    # (это же код PowerShell), и .NET принял бы их за групповые подстановки — файл
    # раздувается и перестаёт парситься.
    $main = $main.Substring(0, $m.Index) + $inline + $main.Substring($m.Index + $m.Length)
    [System.IO.File]::WriteAllText((Join-Path $pkgDir 'net-monitor.ps1'), $main, $utf8Bom)
    # Контроль: склеенный файл обязан парситься — иначе получатель получит утилиту,
    # которая молча не стартует.
    $perr = $null
    [System.Management.Automation.Language.Parser]::ParseInput($main, [ref]$null, [ref]$perr) | Out-Null
    if ($perr) { throw "Склеенный net-monitor.ps1 не парсится: $($perr[0].Message)" }
} else {
    Copy-WithBom (Join-Path $here 'net-monitor.ps1') (Join-Path $pkgDir 'net-monitor.ps1')
    New-Item -ItemType Directory -Path (Join-Path $pkgDir 'lib') -Force | Out-Null
    foreach ($m in @('probe.ps1', 'recovery.ps1')) {
        Copy-WithBom (Join-Path $here "lib\$m") (Join-Path $pkgDir "lib\$m")
    }
}

# .cmd — в ASCII и без BOM: cmd.exe не понимает BOM, а кириллица в нём читается в
# OEM-кодировке и превратилась бы в мусор на чужой машине.
# Все три файла обязательны: без инструкции получателю пакет неполон, а тихий пропуск
# приводил к «Готово» и совету прочитать файл, которого в архиве нет.
foreach ($f in @('Запустить.cmd', 'Установить.cmd', 'README.md', 'ДЛЯ-ПОЛУЧАТЕЛЯ.txt')) {
    $src = Join-Path $here $f
    if (-not (Test-Path $src)) { throw "Не найден обязательный файл пакета: $f" }
    Copy-Item $src (Join-Path $pkgDir $f)
}
# Установщик — тоже .ps1, значит тоже обязан уехать с BOM.
Copy-WithBom (Join-Path $here 'install.ps1') (Join-Path $pkgDir 'install.ps1')

$zip = Join-Path $OutDir "net-monitor-$version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
# Упаковываем содержимое папки, а не саму папку: иначе после «Извлечь всё» в
# C:\Utils\net-monitor получалось C:\Utils\net-monitor\net-monitor.
Compress-Archive -Path (Join-Path $pkgDir '*') -DestinationPath $zip -CompressionLevel Optimal

    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash
    $size = [math]::Round((Get-Item $zip).Length / 1KB, 1)
    $files = (Get-ChildItem $pkgDir -Recurse -File | Measure-Object).Count

    Write-Host ''
    Write-Host "Готово: $zip  (v$version, $size КБ, файлов: $files, режим: $(if ($Single) { 'один скрипт' } else { 'скрипт + папка lib' }))"
    Write-Host "SHA256: $hash"
    Write-Host ''
    Write-Host 'Как передать:'
    Write-Host '  Почтой НЕ отправится — Gmail и Outlook режут .cmd даже внутри архива.'
    Write-Host '  Отправляйте файлом в Telegram или ссылкой на облачный диск.'
    Write-Host ''
    Write-Host 'Что написать получателю: текст лежит в ДЛЯ-ПОЛУЧАТЕЛЯ.txt внутри архива.'
    Write-Host 'Хеш выше можно приложить к сообщению — чтобы он сверил, что дошло то же самое.'
}
finally {
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
