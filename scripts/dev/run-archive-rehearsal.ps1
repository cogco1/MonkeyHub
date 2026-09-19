<#
Internal development only. MonkeyHub is the sole production launcher.

Rehearse one project archive on this machine, end to end: export the given
project, restore it under -RestoreParent, start one project runtime on the
restored copy, ask tools/rehearse_project_archive.py to compare the restored
identities and continue one bounded candidate from the restored base, then stop
the runtime again. The summary block on stdout is the driver's, printed in the
order GH-56 asks for, and this script adds nothing to it.

The rehearsal proves that a project survived the move and can keep designing;
it does not prove CAD export. So the runtime is started with the deterministic
intent provider and with geometry export off, which is the only configuration
this wrapper has been run in: an OCCT or Rhino failure would otherwise land on
the candidate row and read as if the archive had lost something. Pass
-WithCadExport to leave CAD export at the runtime's own default.

This is the local dress rehearsal, not the acceptance. The acceptance GH-56
closes on is the owner running this same wrapper on a clean Windows user, with a
real project, where only the archive was carried across.
#>
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$SourceProject,
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$RestoreParent,
    [string]$EnvironmentLabel = 'isolated folder on this machine',
    [ValidateRange(1, 65535)][int]$Port = 8111,
    [switch]$WithCadExport
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$driver = Join-Path $repoRoot 'tools\rehearse_project_archive.py'
$runtimeScript = Join-Path $repoRoot 'scripts\dev\run-project-runtime.ps1'
# Every path the caller named is made absolute against the location this
# shell is standing in, before anything is created or handed on. .NET and the
# driver both read a relative path against the process working directory,
# which is not the caller's location and is not the same for both of them.
$SourceProject = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SourceProject)
$ArchivePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ArchivePath)
$RestoreParent = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RestoreParent)
if (-not (Test-Path -LiteralPath (Join-Path $SourceProject 'project.json') -PathType Leaf)) {
    throw "-SourceProject must name a P036 project directory; there is no project.json in: $SourceProject"
}
if (-not (Test-Path -LiteralPath $RestoreParent)) {
    # Windows PowerShell 5.1's New-Item has no -LiteralPath, and its -Path
    # reads [ ] as a wildcard; this creates exactly the folder that was named.
    [void][IO.Directory]::CreateDirectory($RestoreParent)
}
$arguments = @(
    '--source', $SourceProject,
    '--archive', $ArchivePath,
    '--restore-parent', $RestoreParent,
    '--python', $Python,
    '--environment-label', $EnvironmentLabel
)

# One phase at a time: the runtime can only bind the restored project once the
# restore has created it.
& $Python $driver --phase export-restore @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# The archive names the project, the same way the driver and the restore do; a
# source folder is only a location and may be called anything.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
try {
    $entry = $zip.GetEntry('manifest.json')
    if ($null -eq $entry) { throw "the archive holds no manifest.json: $ArchivePath" }
    $stream = $entry.Open()
    $reader = New-Object IO.StreamReader($stream)
    try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose(); $stream.Dispose() }
}
finally { $zip.Dispose() }
$projectId = $manifest.transfer.project_id
if ([string]::IsNullOrWhiteSpace($projectId)) {
    throw "the archive manifest names no project: $ArchivePath"
}
$restored = Join-Path $RestoreParent $projectId
if (-not (Test-Path -LiteralPath (Join-Path $restored 'project.json') -PathType Leaf)) {
    throw "the restore did not produce a project at: $restored"
}

$cadExport = 'off'
if ($WithCadExport) { $cadExport = $null }
$job = Start-Job -ScriptBlock {
    param($script, $projectDir, $port, $python, $cadExport)
    $env:ARCHFLOW_STUDIO_INTENT_PROVIDER = 'deterministic'
    if ($cadExport) { $env:ARCHFLOW_STUDIO_CAD_EXPORT = $cadExport }
    & $script -ProjectDir $projectDir -Port $port -Python $python
} -ArgumentList $runtimeScript, $restored, $Port, $Python, $cadExport
$runtimePid = $null
$code = 1
try {
    $health = "http://127.0.0.1:$Port/api/health"
    $deadline = (Get-Date).AddSeconds(120)
    while ($null -eq $runtimePid -and (Get-Date) -lt $deadline) {
        if ($job.State -in @('Failed', 'Completed', 'Stopped')) { break }
        try {
            $answer = Invoke-RestMethod -Uri $health -TimeoutSec 5
            if ($answer.status -eq 'ok') { $runtimePid = $answer.processId }
        }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if ($null -eq $runtimePid) {
        Receive-Job -Job $job | Write-Host
        throw "the project runtime did not answer $health"
    }
    & $Python $driver --phase verify @arguments --runtime-url "http://127.0.0.1:$Port"
    $code = $LASTEXITCODE
}
finally {
    Stop-Job -Job $job
    # The job hosts the runtime script and the interpreter it started is that
    # script's own child, so it is stopped by the id the health route reported.
    # When health never answered there is no such id, and whatever is listening
    # on this port is that same unreachable start: it is found by the port
    # rather than left holding it.
    if ($null -eq $runtimePid) {
        # NetTCPIP is absent on some Windows installations, and a missing
        # command is a terminating error here. Finding no listener is a reason
        # to stop looking, never a reason to leave the job behind.
        try {
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
            if ($listener) { $runtimePid = ($listener | Select-Object -First 1).OwningProcess }
        }
        catch { }
    }
    if ($null -ne $runtimePid) {
        try { Stop-Process -Id $runtimePid -Force -ErrorAction Stop } catch { }
    }
    Remove-Job -Job $job -Force
}
exit $code
