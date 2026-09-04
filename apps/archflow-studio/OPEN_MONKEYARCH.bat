@echo off
rem One-click start for MonkeyArch. Double-click this file, or the Desktop shortcut
rem make-desktop-shortcut.ps1 writes. %~dp0 is this file's own folder, so the current
rem directory does not matter. Arguments are passed through, e.g. -NoBrowser.
rem
rem There is no console to read and none to close. The launcher opens a splash window
rem while the two servers come up, a refusal turns that window red, and once the browser
rem is open a tray icon is what stops the pair again. That is why nothing here waits on
rem the launcher and nothing here pauses: this file starts it and gets out of the way.
rem
rem powershell.exe and not pwsh.exe: pwsh runs MTA on Windows, and the launcher's WinForms
rem message loop needs a single-threaded apartment. Windows PowerShell 5.1 is always there.
rem
rem -WindowStyle Hidden hides the launcher's own console; `start /min` keeps this file's
rem cmd window off the screen while it hands over. Opened through the Desktop shortcut --
rem which make-desktop-shortcut.ps1 saves with WindowStyle 7, minimised -- nothing is
rem painted at all. Double-clicked directly in Explorer, cmd still flashes for the moment
rem it takes to reach the line below; a .bat cannot refuse the console it is given.
setlocal
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch-studio.ps1" -HideConsole %*
exit
