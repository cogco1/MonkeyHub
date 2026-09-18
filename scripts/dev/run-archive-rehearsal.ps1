<#
Internal development only. MonkeyHub is the sole production launcher.

Rehearse one project archive on this machine, end to end: export the given
project, restore it under -RestoreParent, start one project runtime on the
restored copy, ask tools/rehearse_project_archive.py to compare the restored
identities and continue one bounded candidate from the restored base, then stop
the runtime again. The summary block on stdout is the driver's, printed in the
order GH-56 asks for, and this script adds nothing to it.

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
    [ValidateRange(1, 65535)][int]$Port = 8111
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$driver = Join-Path $repoRoot 'tools\rehearse_project_archive.py'
$runtimeScript = Join-Path $repoRoot 'scripts\dev\run-project-runtime.ps1'
if (-not (Test-Path -LiteralPath (Join-Path $SourceProject 'project.json') -PathType Leaf)) {
    throw "-SourceProject must name a P036 project directory; there is no project.json in: $SourceProject"
}
$SourceProject = (Resolve-Path -LiteralPath $SourceProject).ProviderPath
$projectId = Split-Path -Leaf $SourceProject
if (-not (Test-Path -LiteralPath $RestoreParent)) {
    New-Item -ItemType Directory -Path $RestoreParent | Out-Null
}
$RestoreParent = (Resolve-Path -LiteralPath $RestoreParent).ProviderPath
$restored = Join-Path $RestoreParent $projectId
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
if (-not (Test-Path -LiteralPath (Join-Path $restored 'project.json') -PathType Leaf)) {
    throw "the restore did not produce a project at: $restored"
}

$job = Start-Job -ScriptBlock {
    param($script, $projectDir, $port, $python)
    & $script -ProjectDir $projectDir -Port $port -Python $python
} -ArgumentList $runtimeScript, $restored, $Port, $Python
$runtimePid = $null
$code = 1
try {
    $health = "http://127.0.0.1:$Port/api/health"
    $deadline = (Get-Date).AddSeconds(60)
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
    # script's own child, so the one process the health route identified is
    # stopped by the id it reported rather than by guessing at the port.
    if ($null -ne $runtimePid) {
        try { Stop-Process -Id $runtimePid -Force -ErrorAction Stop } catch { }
    }
    Remove-Job -Job $job -Force
}
exit $code
