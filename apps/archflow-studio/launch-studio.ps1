param(
    [string]$RuntimeConfig = (Join-Path $PSScriptRoot 'runtime.json'),
    [switch]$NoBrowser
)

# One-click start for ArchFlow Studio: the API (FastAPI) and the web client (Vite), bound
# to the project named in runtime.json. Closing this window stops both. Same shape as
# LINEPLUS's launch-current.ps1: a runtime.json is the only input, and it is validated first.
#
# Windows PowerShell 5.1 is the floor. No `??`, no ternary, no .NET-Core-only overloads.

$ErrorActionPreference = 'Stop'
# UTF-8 so a project path with Chinese characters prints as itself. The setter also flips the
# console output code page, which is the half that actually makes it render; it throws when the
# process has no console (a hidden or fully redirected launch), and that is not a reason to fail.
# UTF8Encoding($false) keeps a BOM out of a redirected stdout.
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$studioRoot = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $studioRoot '..\..')).Path
$apiRoot = Join-Path $studioRoot 'api'
$webRoot = Join-Path $studioRoot 'web'
$logRoot = Join-Path $studioRoot '.runtime'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

function Invoke-Quiet {
    # Run a native command and hand back its exit code and its combined output. Native stderr
    # under $ErrorActionPreference = 'Stop' is a terminating NativeCommandError in 5.1: one
    # deprecation warning from python or one progress line from npm would otherwise abort the
    # launcher with the warning as its message. Lower the preference for the call only.
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output | Out-String) }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-PortFree([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1', $port); return $false } catch { return $true } finally { $client.Close() }
}

function Get-LogTail([string]$path, [int]$lines) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return '' }
    $tail = Get-Content -LiteralPath $path -Tail $lines -ErrorAction SilentlyContinue
    if (-not $tail) { return '' }
    return ($tail -join [Environment]::NewLine)
}

function Wait-Http([string]$url, [int]$seconds, [System.Diagnostics.Process]$Process) {
    # Poll until the URL answers. A child that has already exited is answered immediately:
    # waiting the full timeout on a dead process only delays the log the reader needs.
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process -and $Process.HasExited) { return $false }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

# Everything below runs inside one try: a refusal reaches the reader as the sentence that
# explains it, not as a PowerShell stack trace over a .bat window that is about to pause.
try {

# --- runtime.json is the only input, and it is validated before anything is started
if (-not (Test-Path -LiteralPath $RuntimeConfig -PathType Leaf)) { throw "runtime.json is required: $RuntimeConfig" }
$runtime = Get-Content -LiteralPath $RuntimeConfig -Raw -Encoding utf8 | ConvertFrom-Json
if ($runtime.schema_version -ne 'archflow-studio-runtime@1') { throw "runtime.json has an unsupported schema_version: $($runtime.schema_version)" }
$projectDir = [string]$runtime.project_dir
if (-not $projectDir) { throw "runtime.json must name project_dir (a P036 project directory): $RuntimeConfig" }
if (-not (Test-Path -LiteralPath $projectDir -PathType Container)) {
    throw "project_dir does not exist: $projectDir  (named by $RuntimeConfig)"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectDir 'project.json') -PathType Leaf)) {
    throw "project_dir is not a P036 project (no project.json in it): $projectDir  (named by $RuntimeConfig)"
}
$apiPort = if ($runtime.api_port) { [int]$runtime.api_port } else { 8000 }
$webPort = if ($runtime.web_port) { [int]$runtime.web_port } else { 5174 }

# "py -3.12" is a command plus its arguments; the executable itself must not contain spaces.
$pythonParts = @(([string]$runtime.python) -split '\s+' | Where-Object { $_ })
if ($pythonParts.Count -eq 0) { $pythonParts = @('py', '-3.12') }
$pythonExe = $pythonParts[0]
$pythonArgs = @()
if ($pythonParts.Count -gt 1) { $pythonArgs = @($pythonParts[1..($pythonParts.Count - 1)]) }

Write-Host "ArchFlow Studio" -ForegroundColor Cyan
Write-Host "  config  : $RuntimeConfig"
Write-Host "  project : $projectDir"
Write-Host "  api     : http://127.0.0.1:$apiPort   web: http://127.0.0.1:$webPort"

# --- preflight: python + fastapi, node_modules, free ports
$check = Invoke-Quiet $pythonExe ($pythonArgs + @('-c', 'import fastapi, uvicorn'))
if ($check.ExitCode -ne 0) {
    $requirements = Join-Path $repoRoot 'apps\archflow-studio\api\requirements.txt'
    throw "'$($runtime.python)' cannot import fastapi/uvicorn. Install them with:$([Environment]::NewLine)  $($runtime.python) -m pip install -r $requirements$([Environment]::NewLine)$($check.Output)"
}
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules\.bin\vite.cmd'))) {
    Write-Host "  web deps missing; running npm install (this takes a few minutes) ..." -ForegroundColor Yellow
    Push-Location $webRoot
    try {
        $install = Invoke-Quiet 'npm.cmd' @('install')
        if ($install.ExitCode -ne 0) { throw "npm install failed in $webRoot$([Environment]::NewLine)$($install.Output)" }
    } finally { Pop-Location }
}
if (-not (Test-PortFree $apiPort)) { throw "port $apiPort is already in use (a Studio API is running, or another program holds it)." }
if (-not (Test-PortFree $webPort)) { throw "port $webPort is already in use (a Vite dev server is running, or another program holds it)." }
if ([string]$runtime.codex -and -not (Test-Path -LiteralPath ([string]$runtime.codex) -PathType Leaf)) {
    Write-Host "  warning : codex not found at $($runtime.codex); the API refuses to start with intent_provider codex until it is (it needs codex --version to sign its receipts)." -ForegroundColor Yellow
}

# --- environment the API reads (settings.py and intent_agent.py name these; no default project in code)
$env:ARCHFLOW_STUDIO_PROJECT_DIR = $projectDir
if ([string]$runtime.reference_run) { $env:ARCHFLOW_STUDIO_REFERENCE_RUN = [string]$runtime.reference_run } else { Remove-Item Env:ARCHFLOW_STUDIO_REFERENCE_RUN -ErrorAction SilentlyContinue }
if ($runtime.rhino_export) { $env:ARCHFLOW_STUDIO_RHINO_EXPORT = '1' } else { $env:ARCHFLOW_STUDIO_RHINO_EXPORT = '0' }
if ([string]$runtime.powershell) { $env:ARCHFLOW_STUDIO_POWERSHELL = [string]$runtime.powershell }
if ([string]$runtime.intent_provider) { $env:ARCHFLOW_STUDIO_INTENT_PROVIDER = [string]$runtime.intent_provider }
if ([string]$runtime.intent_model) { $env:ARCHFLOW_STUDIO_INTENT_MODEL = [string]$runtime.intent_model }
if ([string]$runtime.intent_timeout_s) { $env:ARCHFLOW_STUDIO_INTENT_TIMEOUT_S = [string]$runtime.intent_timeout_s }
if ([string]$runtime.codex) { $env:ARCHFLOW_STUDIO_CODEX = [string]$runtime.codex }
# Redirected python output is block-buffered; an unbuffered child is the difference between a
# log that says why it died and an empty file.
$env:PYTHONUNBUFFERED = '1'

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$apiOut = Join-Path $logRoot "api-$stamp.out.log"
$apiErr = Join-Path $logRoot "api-$stamp.err.log"
$webOut = Join-Path $logRoot "web-$stamp.out.log"
$webErr = Join-Path $logRoot "web-$stamp.err.log"
$children = @()
try {
    $apiArgs = $pythonArgs + @('-m', 'archflow_studio_api.main', '--host', '127.0.0.1', '--port', "$apiPort")
    $api = Start-Process -FilePath $pythonExe -ArgumentList $apiArgs -WorkingDirectory $apiRoot -PassThru -NoNewWindow -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr
    $children += [pscustomobject]@{ Name = 'the API'; Process = $api; ErrorLog = $apiErr }
    if (-not (Wait-Http "http://127.0.0.1:$apiPort/api/health" 60 $api)) {
        throw "the API did not answer /api/health. Its log said:$([Environment]::NewLine)$(Get-LogTail $apiErr 25)$([Environment]::NewLine)(full log: $apiErr)"
    }
    $health = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$apiPort/api/health").Content
    Write-Host "  api up  : $health"

    $web = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "npm.cmd run dev -- --port $webPort --strictPort") -WorkingDirectory $webRoot -PassThru -NoNewWindow -RedirectStandardOutput $webOut -RedirectStandardError $webErr
    $children += [pscustomobject]@{ Name = 'the web client'; Process = $web; ErrorLog = $webErr }
    if (-not (Wait-Http "http://127.0.0.1:$webPort/" 90 $web)) {
        throw "the web client did not answer on :$webPort. Its log said:$([Environment]::NewLine)$(Get-LogTail $webErr 25)$([Environment]::NewLine)$(Get-LogTail $webOut 10)$([Environment]::NewLine)(full logs: $webErr, $webOut)"
    }
    Write-Host "  web up  : http://127.0.0.1:$webPort"

    if ($runtime.open_browser -and -not $NoBrowser) { Start-Process "http://127.0.0.1:$webPort" }
    Write-Host ""
    Write-Host "Studio is running. Close this window or press Ctrl+C to stop both servers." -ForegroundColor Green
    Write-Host "  logs: $logRoot"
    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($child in $children) {
            if ($child.Process.HasExited) {
                throw "$($child.Name) exited with code $($child.Process.ExitCode). Its log said:$([Environment]::NewLine)$(Get-LogTail $child.ErrorLog 25)$([Environment]::NewLine)(logs: $logRoot)"
            }
        }
    }
} finally {
    foreach ($child in $children) {
        $process = $child.Process
        if ($process -and -not $process.HasExited) {
            # /T because the web child is cmd.exe with node under it: killing cmd alone
            # would leave a Vite server holding the port.
            try { & taskkill /PID $process.Id /T /F 2>&1 | Out-Null } catch { }
        }
    }
    Write-Host "Studio stopped."
}

} catch {
    # The inner finally above has already stopped whatever had started.
    Write-Host ""
    Write-Host "ArchFlow Studio did not start." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
