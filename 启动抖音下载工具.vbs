' 抖音下载工具 · 单启动器（无弹窗）
' 用法：以后只双击这一个文件即可。
' 说明：单文件 exe 冷启动要解包 30~60 秒，黑屏等待是正常的，别重复双击。
Option Explicit
Dim sh, fso, dir, exe
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
exe = dir & "\抖音下载工具.exe"
If Not fso.FileExists(exe) Then
  MsgBox "找不到 抖音下载工具.exe：" & vbCrLf & exe, 48, "启动失败"
  WScript.Quit 1
End If
sh.CurrentDirectory = dir
sh.Run Chr(34) & exe & Chr(34), 1, False
