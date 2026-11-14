# Autostart for the MiniMax areas panel (http://127.0.0.1:8099).
# Started from the Startup folder at logon. Watchdog: if node dies, restart it.
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI without a BOM,
# and Cyrillic comments then break the parser (gotcha, 14.11.2026).

$ErrorActionPreference = 'Continue'

$Port   = 8099
$Script = 'C:\Users\valstan\bin\mcode-panel.js'
$LogDir = "$env:LOCALAPPDATA\mcode-web"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-Panel {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/state" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Start-Panel {
    $cmd = "start /b node `"$Script`" $Port >> `"$LogDir\panel.out.log`" 2>> `"$LogDir\panel.err.log`""
    cmd.exe /c $cmd | Out-Null
    Start-Sleep -Seconds 6
    if (Test-Panel) {
        Add-Content -Path "$LogDir\panel.out.log" -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - panel up"
    } else {
        Add-Content -Path "$LogDir\panel.err.log" -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - panel did not come up"
    }
}

if (-not (Test-Panel)) { Start-Panel }

# Watchdog: silently bring the panel back within a minute.
while ($true) {
    Start-Sleep -Seconds 60
    if (-not (Test-Panel)) { Start-Panel }
}
