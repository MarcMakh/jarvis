' Launches the JARVIS server hidden (no console window, no browser).
' Used by the auto-start-on-login shortcut. Stop it with scripts\stop_jarvis.cmd.
Set sh = CreateObject("WScript.Shell")
projDir = sh.CurrentDirectory
' Resolve the project root relative to this script's location.
scriptPath = WScript.ScriptFullName
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(scriptPath))
sh.CurrentDirectory = root
sh.Run "cmd /c uv run jarvis --no-browser", 0, False
