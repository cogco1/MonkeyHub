@echo off
setlocal
set "HUB_ROOT=%~dp0"
set "HUB_PYTHON=%HUB_ROOT%_runtime\python\python.exe"
if not exist "%HUB_PYTHON%" (
  echo This entry opens an installed MonkeyHub candidate.
  echo For source development, use apps\monkeyhub\launch-hub.ps1 -Python ^<python.exe^>.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HUB_ROOT%apps\monkeyhub\launch-hub.ps1" -Python "%HUB_PYTHON%" -HubWebDir "%HUB_ROOT%apps\monkeyhub\web\dist" -StudioWebDir "%HUB_ROOT%apps\archflow-studio\web\dist" -HideConsole %*
exit /b %ERRORLEVEL%
