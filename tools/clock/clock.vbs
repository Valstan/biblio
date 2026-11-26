Option Explicit
' clock.vbs - quiet launcher for the clock widget.
'
' Runs the widget in a hidden PowerShell window: no console appears at all, only
' the widget itself. The PowerShell process does not belong to any console of
' the caller, so closing the terminal it was started from does not kill the
' widget (that was the bug on 26.11.2026: the widget died with the console).
'
' ASCII only on purpose: .vbs is read in the OEM codepage, and Cyrillic here
' would show up as garbage in Notepad on another machine.

Dim sh, fso, here, ps, pwsh, q, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
ps = here & "\clock.ps1"
pwsh = sh.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"

If Not fso.FileExists(ps) Then
    MsgBox "clock.ps1 not found next to the launcher: " & here, vbExclamation
    WScript.Quit 1
End If

' Nested quotes in a VBScript literal are easy to get wrong, so build the
' command line from Chr(34) instead of writing """" by hand.
q = Chr(34)
cmd = q & pwsh & q & " -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & q & ps & q

' 0 = hidden window, False = do not wait: the script keeps running alone.
sh.Run cmd, 0, False
