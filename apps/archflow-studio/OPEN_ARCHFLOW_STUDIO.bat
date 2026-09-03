@echo off
rem One-click start for ArchFlow Studio. Double-click this file, or the Desktop shortcut
rem make-desktop-shortcut.ps1 writes. %~dp0 is this file's own folder, so the current
rem directory does not matter. Arguments are passed through, e.g. -NoBrowser.
setlocal
set "STUDIO_ROOT=%~dp0"
title ArchFlow Studio
where pwsh.exe >nul 2>&1
if not errorlevel 1 (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_ROOT%launch-studio.ps1" %*
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_ROOT%launch-studio.ps1" %*
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :failed
exit /b 0

:failed
echo.
echo Studio did not start cleanly. Exit code: %RC%. The message above says why.
pause
exit /b %RC%
