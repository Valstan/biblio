# clock installer (Windows / PowerShell). Idempotent: safe to re-run.
# Messages are ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1
# as the system ANSI codepage (often cp1251) and mangles non-ASCII text.
# Russian docs live in README.md.
#
#   Run:  powershell -ExecutionPolicy Bypass -File install.ps1
#
# Installs the utility into the user profile, adds it to the Startup folder so
# it appears on every logon, hides the native taskbar clock and starts it.
# Nothing is installed system-wide and no administrator rights are needed.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$ToolsDir  = Join-Path $env:LOCALAPPDATA 'Programs\clock'
$Target    = Join-Path $ToolsDir 'clock.ps1'
$VbsTarget = Join-Path $ToolsDir 'clock.vbs'
$Startup   = [Environment]::GetFolderPath('Startup')
$Shortcut = Join-Path $Startup 'KompikClock.lnk'
$Key       = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'

Write-Host ''
Write-Host "clock: installing into $ToolsDir" -ForegroundColor Cyan

if (-not (Test-Path $ToolsDir)) { New-Item -ItemType Directory -Force $ToolsDir | Out-Null }

# --- 1. copy the payload ---
$source = Join-Path $PSScriptRoot 'clock.ps1'
if (-not (Test-Path $source)) { throw "clock.ps1 not found next to the installer ($PSScriptRoot)." }
Copy-Item -LiteralPath $source -Destination $Target -Force
$vbsSource = Join-Path $PSScriptRoot 'clock.vbs'
if (-not (Test-Path $vbsSource)) { throw "clock.vbs not found next to the installer ($PSScriptRoot)." }
Copy-Item -LiteralPath $vbsSource -Destination $VbsTarget -Force
Write-Host "  payload: $Target"

# --- 2. stop a running copy so the new one can start ---
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*clock.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# --- 3. autostart shortcut: wscript, not powershell ---
# The shortcut points at wscript.exe so that no console window appears at logon
# and the widget does not belong to any console: closing a terminal cannot kill it.
$WshShell = New-Object -ComObject WScript.Shell
$lnk = $WshShell.CreateShortcut($Shortcut)
$lnk.TargetPath       = "$env:SystemRoot\System32\wscript.exe"
$lnk.Arguments        = """$VbsTarget"""
$lnk.WorkingDirectory = $ToolsDir
$lnk.Description      = 'Kompik clock: one-line date and time'
$lnk.WindowStyle      = 7
$lnk.Save()
Write-Host "  autostart: $Shortcut"

# --- 4. hide the native taskbar clock (Windows 11) ---
$cur = (Get-ItemProperty -Path $Key -Name 'ShowSystrayDateTimeValueName' -ErrorAction SilentlyContinue).ShowSystrayDateTimeValueName
if ($null -eq $cur -or $cur -ne 0) {
    New-ItemProperty -Path $Key -Name 'ShowSystrayDateTimeValueName' -Value 0 -PropertyType DWord -Force | Out-Null
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer.exe }
    Write-Host '  native taskbar clock: hidden'
} else {
    Write-Host '  native taskbar clock: already hidden'
}

# --- 5. start it (quiet: no console window) ---
Start-Process "$env:SystemRoot\System32\wscript.exe" -ArgumentList "`"$VbsTarget`""
Start-Sleep -Seconds 3

$alive = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -like '*clock.ps1*' }
if ($alive) {
    Write-Host ''
    Write-Host 'Done. The clock is running.' -ForegroundColor Green
    Write-Host "  payload:    $Target"
    Write-Host "  autostart:  $Shortcut"
    Write-Host ''
    Write-Host 'Right-click the clock: bigger / smaller / show the system clock / exit.' -ForegroundColor Green
    Write-Host 'To remove: delete the folder, the shortcut, and set'
    Write-Host "  HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced\ShowSystrayDateTimeValueName = 1"
    Write-Host ''
} else {
    Write-Host ''
    Write-Host 'The clock did not start. Run it by hand to see the error:' -ForegroundColor Red
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File `"$Target`""
    Write-Host ''
}
