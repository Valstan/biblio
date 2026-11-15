' Quiet start of the MiniMax panel supervisor - no console window at all.
'
' ASCII only on purpose: WScript reads .vbs in the ANSI code page, so non-ASCII
' comments can break the parser. Owner, 15.11.2026: the console window that used
' to hang on screen was the old launcher - closing it killed the watchdog and
' the panel together.
'
' Window style 0 = hidden, False = do not wait.
' NB: inside a VBScript string a quote is doubled - the node path has a space in
' it, and without the quotes WScript.Shell.Run cannot parse the command (it
' looked for "C:\Program" and silently did nothing).
'
Dim sh, nodeExe, scriptPath, cmd, fso, f

Set sh = CreateObject("WScript.Shell")
nodeExe = sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\nodejs\node.exe"
scriptPath = "C:\Users\valstan\bin\mcode-panel-supervisor.js"
cmd = """" & nodeExe & """ """ & scriptPath & """"

On Error Resume Next
sh.Run cmd, 0, False
If Err.Number <> 0 Then
  Set fso = CreateObject("Scripting.FileSystemObject")
  Set f = fso.OpenTextFile("C:\Users\valstan\bin\mcode-panel-supervisor.lastrun.log", True)
  f.WriteLine Now & " run failed: " & Err.Number & " " & Err.Description & " cmd=" & cmd
  f.Close
  Err.Clear
End If
