param([switch]$DebugLog)

$ErrorActionPreference = 'Stop'

# ============================ CONFIG ============================
$FontFamily   = 'Segoe UI'
$FontSize     = 13          # points; the native taskbar clock is about 9
$FontBold     = $false
$MarginRight  = 10          # pixels from the right screen edge
$MarginBottom = 6           # pixels from the taskbar edge
$Format       = 'ddd, dd MMM yyyy, HH:mm'
$ShowSystemClock = $false   # hide the native taskbar clock (Windows 11 key)
# The chip sits just ABOVE the taskbar. Drawing it inside the taskbar strip does
# not work: on Windows 11 the taskbar surface is drawn over topmost windows
# (verified 26.11.2026 - the clock was invisible in that mode).
# =================================================================

Add-Type -AssemblyName System.Windows.Forms, System.Drawing

# --- single instance: the autostart entry and a manual start must not fight ---
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\clock-kompik', [ref]$created)
if (-not $created) { exit 0 }

Add-Type @'
using System;
using System.Runtime.InteropServices;
public class ClockNative {
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern IntPtr FindWindow(string c, string n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetWindowRgn(IntPtr h, IntPtr rgn, bool redraw);
  [DllImport("gdi32.dll")] public static extern IntPtr CreateRoundRectRgn(int l, int t, int r, int b, int w, int h);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@

function Get-TaskbarRect {
  $h = [ClockNative]::FindWindow('Shell_TrayWnd', $null)
  if ($h -eq [IntPtr]::Zero) { return $null }
  $r = New-Object ClockNative+RECT
  if ([ClockNative]::GetWindowRect($h, [ref]$r)) { return $r }
  return $null
}

function Test-DarkTheme {
  $v = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -ErrorAction SilentlyContinue
  if ($v -and $v.PSObject.Properties['AppsUseLightTheme'] -and $v.AppsUseLightTheme -eq 0) { return $true }
  return $false
}

function Set-SystemClockHidden([bool]$hidden) {
  $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
  $want = $(if ($hidden) { 0 } else { 1 })
  $cur = (Get-ItemProperty -Path $key -Name 'ShowSystrayDateTimeValueName' -ErrorAction SilentlyContinue).ShowSystrayDateTimeValueName
  if ($null -ne $cur -and $cur -eq $want) { return }
  New-ItemProperty -Path $key -Name 'ShowSystrayDateTimeValueName' -Value $want -PropertyType DWord -Force | Out-Null
  Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer.exe }
}

# The chip takes the colour of the taskbar, so it looks native next to it.
# Sampling the real pixel beats guessing a hex value: the taskbar is acrylic and
# its shade depends on the wallpaper.
function Get-TaskbarColor {
  $tb = Get-TaskbarRect
  if ($tb) {
    try {
      $bmp = New-Object System.Drawing.Bitmap 1, 1
      $g = [System.Drawing.Graphics]::FromImage($bmp)
      $g.CopyFromScreen($tb.Right - 3, ($tb.Top + $tb.Bottom) / 2, 0, 0, (New-Object System.Drawing.Size(1, 1)))
      $g.Dispose()
      $c = $bmp.GetPixel(0, 0)
      $bmp.Dispose()
      # a transparent-looking pixel means we sampled the wallpaper, not the bar
      if ($c.A -gt 0) { return $c }
    } catch {}
  }
  if (Test-DarkTheme) { return [System.Drawing.Color]::FromArgb(43, 43, 43) }
  return [System.Drawing.Color]::FromArgb(243, 243, 243)
}

[System.Windows.Forms.Application]::EnableVisualStyles()

# Font has ambiguous constructors (bool vs FontStyle) - New-Object with three
# positional arguments fails with "Multiple ambiguous overloads". Name the type.
# The function must be defined before the first call: PowerShell runs the file
# top to bottom.
function New-ClockFont([double]$size) {
    $style = $(if ($FontBold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular })
    return New-Object System.Drawing.Font -ArgumentList @($FontFamily, [float]$size, $style)
}

$isDark = Test-DarkTheme

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.ShowInTaskbar = $false
$form.TopMost = $true
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.BackColor = Get-TaskbarColor
$form.Padding = New-Object System.Windows.Forms.Padding 8, 2, 10, 2

$label = New-Object System.Windows.Forms.Label
$label.AutoSize = $false
$label.TextAlign = [System.Drawing.ContentAlignment]::MiddleRight
$label.BackColor = $form.BackColor
$label.Font = New-ClockFont $FontSize
$label.ForeColor = $(if ($isDark) { [System.Drawing.Color]::FromArgb(240, 240, 240) } else { [System.Drawing.Color]::FromArgb(32, 32, 32) })
$form.Controls.Add($label)

$culture = [System.Globalization.CultureInfo]::GetCultureInfo((Get-Culture).Name)

function Update-Clock {
  $text = [DateTime]::Now.ToString($Format, $culture)
  $label.Text = $text
  $size = $label.GetPreferredSize([System.Drawing.Size]::Empty)
  $width = $size.Width + 20
  $height = [Math]::Max($size.Height + 6, 24)
  $form.Width = $width
  $form.Height = $height
  $label.Width = $size.Width
  $label.Height = $height

  $tb = Get-TaskbarRect
  if ($tb) {
    $bottom = $tb.Top - $MarginBottom
    $form.Left = $tb.Right - $MarginRight - $width
    $form.Top = $bottom - $height
  }

  if ($DebugLog) {
    $line = '{0} form={1},{2} size={3}x{4} label="{5}" lw={6} lh={7} vis={8} back={9} tb={10}' -f `
        (Get-Date -Format 'HH:mm:ss'), $form.Left, $form.Top, $form.Width, $form.Height, $label.Text,
        $label.Width, $label.Height, $form.Visible, $form.BackColor.R, $(if ($tb) { "$($tb.Left),$($tb.Top),$($tb.Right),$($tb.Bottom)" } else { 'none' })
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'clock-debug.log') -Value $line
  }
}

# Rounded corners. The region must be applied to an ALREADY SHOWN window: applied
# before the form is shown, the window ends up with an empty region and nothing
# is painted at all (verified on 26.11.2026 - the clock was invisible).
function Set-ChipRegion {
  if (-not $form.IsHandleCreated -or -not $form.Visible) { return }
  $rgn = [ClockNative]::CreateRoundRectRgn(0, 0, $form.Width + 1, $form.Height + 1, 14, 14)
  [void][ClockNative]::SetWindowRgn($form.Handle, $rgn, $true)
}

Update-Clock

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({ Update-Clock; Set-ChipRegion })
$timer.Start()

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemBigger = $menu.Items.Add('Bigger', $null, { $script:FontSize = $script:FontSize + 1; $label.Font = New-ClockFont $script:FontSize; Update-Clock })
$itemSmaller = $menu.Items.Add('Smaller', $null, { $script:FontSize = [Math]::Max(7, $script:FontSize - 1); $label.Font = New-ClockFont $script:FontSize; Update-Clock })
[void]$menu.Items.Add('-')
$itemShowSystem = $menu.Items.Add('Show system clock', $null, { Set-SystemClockHidden $false })
$itemHideSystem = $menu.Items.Add('Hide system clock', $null, { Set-SystemClockHidden $true })
[void]$menu.Items.Add('-')
$itemQuit = $menu.Items.Add('Exit', $null, { $timer.Stop(); $form.Close() })
$form.ContextMenuStrip = $menu
$form.Add_FormClosing({ $timer.Stop() })
$form.Add_Shown({ Set-ChipRegion })

if ($ShowSystemClock -eq $false) { Set-SystemClockHidden $true }

[void]$form.ShowDialog()
