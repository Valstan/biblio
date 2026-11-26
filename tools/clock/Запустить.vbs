' clock - quiet launcher. Double-click this file: no console window appears,
' only the widget. The widget keeps running after this launcher exits, and it
' does not die when some other terminal is closed - it does not belong to any
' console (see clock.vbs).
'
' ASCII only on purpose: .vbs is read in the OEM codepage.

Option Explicit

Dim sh, fso, here, vbs, q, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
vbs = here & "\clock.vbs"

If Not fso.FileExists(vbs) Then
    MsgBox "clock.vbs not found next to the launcher: " & here, vbExclamation
    WScript.Quit 1
End If

q = Chr(34)
cmd = q & sh.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\wscript.exe" & q & " " & q & vbs & q

sh.Run cmd, 0, False
