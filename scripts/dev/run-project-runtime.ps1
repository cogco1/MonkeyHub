<#
Internal development only. MonkeyHub is the sole production launcher.

Start one project runtime (today: the ArchFlow Studio API, archflow_studio_api) on an
explicit project, in the foreground, for tests and development: an API smoke run,
Playwright, a fixture regression, a MonkeyDiagram change you want to see without going
through Hub project navigation.

Nothing else: no launch window, no tray, no configuration file, no default project, no
lifecycle management. Ctrl+C stops it. Every other setting is the environment the API
already reads (ARCHFLOW_STUDIO_CAD_EXPORT, ARCHFLOW_STUDIO_INTENT_PROVIDER, ...; see
apps/archflow-studio/README.md). This process serves project APIs only; workspace UI belongs to MonkeyHub.
#>
param(
    [Parameter(Mandatory = $true)][string]$ProjectDir,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'project.json') -PathType Leaf)) {
    throw "-ProjectDir must name a P036 project directory; there is no project.json in: $ProjectDir"
}
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).ProviderPath
$pythonPath = @($repoRoot, (Join-Path $repoRoot 'apps\archflow-studio\api'))
if ($env:PYTHONPATH) { $pythonPath += $env:PYTHONPATH }
$env:PYTHONPATH = $pythonPath -join [IO.Path]::PathSeparator
$env:PYTHONUNBUFFERED = '1'
$arguments = @('-m', 'archflow_studio_api.main', '--project-dir', $ProjectDir, '--host', '127.0.0.1', '--port', "$Port")
& $Python @arguments
exit $LASTEXITCODE
