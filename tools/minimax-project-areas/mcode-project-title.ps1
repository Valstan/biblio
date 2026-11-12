# Держит заголовок окна MiniMax Code = "<Проект> — MiniMax Code".
#
# Приложение само ставит заголовок "MiniMax Code" (и переустанавливает его),
# поэтому метку в панели задач держим мы через Win32 SetWindowText.
# Цикл: сначала часто (окно ловится и переименовывается), потом редко —
# до тех пор, пока окно живо. Один скрытый процесс на окно, ~40 МБ RAM.
#
# Использование (вызывается из mcode-project-start.ps1):
#   powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File mcode-project-title.ps1 -TargetPid 1234 -Title "Гоньба — MiniMax Code"

param(
    # ВНИМАНИЕ: параметр не может называться $Pid — это автопеременная PowerShell (только чтение).
    [Parameter(Mandatory = $true)][int]$TargetPid,
    [Parameter(Mandatory = $true)][string]$Title,
    [int]$FastSeconds = 120,
    [int]$MaxSeconds = 43200
)

$ErrorActionPreference = 'SilentlyContinue'

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class MCodeWin {
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool SetWindowText(IntPtr hWnd, string lpString);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public static IntPtr[] WindowsOf(uint pid) {
        var list = new System.Collections.Generic.List<IntPtr>();
        EnumWindows((h, l) => {
            uint p;
            GetWindowThreadProcessId(h, out p);
            if (p == pid && IsWindowVisible(h)) list.Add(h);
            return true;
        }, IntPtr.Zero);
        return list.ToArray();
    }

    public static string Title(IntPtr h) {
        var sb = new StringBuilder(512);
        GetWindowText(h, sb, sb.Capacity);
        return sb.ToString();
    }
}
'@

function Set-Titles {
    $applied = 0
    foreach ($h in [MCodeWin]::WindowsOf([uint32]$TargetPid)) {
        try { if ([MCodeWin]::SetWindowText($h, $Title)) { $applied++ } } catch { }
    }
    return $applied
}

# Фаза 1: окно ещё создаётся, ловим его как можно быстрее.
$fastUntil = (Get-Date).AddSeconds($FastSeconds)
while ((Get-Date) -lt $fastUntil) {
    if ((Set-Titles) -gt 0) { Start-Sleep -Milliseconds 700 } else { Start-Sleep -Milliseconds 500 }
}

# Фаза 2: держим заголовок, пока окно живо (приложение его перебивает).
$maxUntil = (Get-Date).AddSeconds($MaxSeconds)
while ((Get-Date) -lt $maxUntil) {
    Start-Sleep -Seconds 5
    $hs = [MCodeWin]::WindowsOf([uint32]$TargetPid)
    if ($hs.Count -eq 0) { break }
    foreach ($h in $hs) {
        try { [MCodeWin]::SetWindowText($h, $Title) | Out-Null } catch { }
    }
}
