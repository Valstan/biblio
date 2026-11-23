# Тихий запуск супервизора панели MiniMax. Окна не создаём (владелец, 15.11.2026).
#
# Раньше здесь был бесконечный цикл сторожа, запускался из .cmd в автозапуске,
# и его окно консоли висело на экране. Закрыть его означало убить и сторожа, и
# панель (оба сидели на одной консоли cmd). Теперь сторож — node-супервизор
# (mcode-panel-supervisor.js): он поднимает панель, чинит её после падения и
# слушает кнопки «пуск / стоп / перезапуск». Этот скрипт только поднимает его
# и сразу выходит — консоль не держится.
#
# Супервизор сам запускается из автозапуска через mcode-panel-supervisor.vbs
# (wscript, окно 0) — то есть вообще без окна.
#
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI without a BOM,
# and Cyrillic comments then break the parser (gotcha, 14.11.2026).

$ErrorActionPreference = 'Continue'

$Node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $Node) { $Node = 'C:\Program Files\nodejs\node.exe' }
$Script = 'C:\Users\valstan\bin\mcode-panel-supervisor.js'

if (-not (Test-Path $Script)) {
    Write-Host "No supervisor: $Script" -ForegroundColor Yellow
    exit 1
}

Start-Process -FilePath $Node -ArgumentList $Script -WindowStyle Hidden
Write-Host "Supervisor started (no window). Control page: http://127.0.0.1:8098" -ForegroundColor Green
exit 0
