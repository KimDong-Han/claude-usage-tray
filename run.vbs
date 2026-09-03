' Launch the tray app with no console window.
' ASCII only - wscript reads this file as ANSI, so non-ASCII breaks parsing.
Option Explicit
Dim sh, fso, q, py, app, base
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
q = Chr(34)
base = fso.GetParentFolderName(WScript.ScriptFullName)
app = base & "\tray.py"
py = "pythonw.exe"
sh.Run q & py & q & " " & q & app & q, 0, False
