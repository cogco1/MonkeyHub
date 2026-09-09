param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA 'MonkeyHub'),
    [Parameter(Mandatory = $true)][string]$HubWebDir,
    [Parameter(Mandatory = $true)][string]$StudioWebDir,
    [ValidateRange(1, 65535)][int]$Port = 8790,
    [switch]$NoBrowser,
    [switch]$HideConsole
)

# Reuse the Studio desktop shell; the Hub Python process owns its application services.
& (Join-Path $PSScriptRoot '..\archflow-studio\launch-studio.ps1') -Hub -Python $Python `
    -RuntimeRoot $RuntimeRoot -HubWebDir $HubWebDir -StudioWebDir $StudioWebDir `
    -Port $Port -NoBrowser:$NoBrowser -HideConsole:$HideConsole
