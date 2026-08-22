Option Explicit

Dim ws, fso, projectDir, runScript, cmdExe, command

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectDir = "C:\Users\Administrator\Documents\New project7.23\douyin-downloader"
runScript = projectDir & "\run_app.cmd"

If Not fso.FileExists(runScript) Then
    MsgBox "Startup script was not found: " & runScript, vbCritical, "Douyin Downloader"
    WScript.Quit 1
End If

cmdExe = ws.ExpandEnvironmentStrings("%ComSpec%")
command = """" & cmdExe & """ /d /c call """ & runScript & """"

On Error Resume Next
ws.Run command, 0, False
If Err.Number <> 0 Then
    MsgBox "Startup failed: " & Err.Description, vbCritical, "Douyin Downloader"
    WScript.Quit 1
End If
On Error GoTo 0
