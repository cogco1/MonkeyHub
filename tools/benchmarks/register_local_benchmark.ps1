<#
.SYNOPSIS
Registers or unregisters MonkeyHub's daily local benchmark (GH-547): one scheduled task of this account.

.DESCRIPTION
The task \MonkeyHub\DailyLocalBenchmark runs `tools\benchmarks\daily_benchmark.py local` from the main
checkout with pythonw.exe (no window), as the current user and without elevation:

- Task Scheduler starts it when Windows declares the computer idle (an idle trigger), never on battery,
  and stops it when the computer stops being idle or goes on battery, or after two hours.
- The runner waits until nobody has used the computer for 15 minutes (Windows 8 and later no longer use
  a task's own idle duration), measures at most once a day, and skips while any MonkeyHub runs or the
  processors are busy. It logs every start, skip and result to
  <development root>\temp\benchmark-local\logs\local.log and pushes each result to
  results/windows-local/ on benchmark-data with this account's Git credentials.

Without -Register or -Unregister the script only prints the task it would register; -WhatIf prints the
same and changes nothing. See docs/development/benchmarks.md ("Local runs").

.PARAMETER Register
Registers the task, replacing an earlier registration of the same task.

.PARAMETER Unregister
Removes the task; the runner's logs and results stay where they are.

.PARAMETER Python
The python.exe whose pythonw.exe runs the task (default: the first python.exe on PATH that is not the
Microsoft Store alias). It needs the repository's Python dependencies, as for running the benchmark.

.PARAMETER Checkout
The checkout the task runs the runner from (default: the main worktree of this repository, so a task
registered from a task worktree does not point into a worktree that will be retired).

.PARAMETER DevRoot
The development root (default: the one tools\dev\workspace.py configured).

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File tools\benchmarks\register_local_benchmark.ps1

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File tools\benchmarks\register_local_benchmark.ps1 -Register

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File tools\benchmarks\register_local_benchmark.ps1 -Unregister
#>
[CmdletBinding(SupportsShouldProcess = $true, DefaultParameterSetName = 'Show')]
param(
    [Parameter(ParameterSetName = 'Register', Mandatory = $true)][switch]$Register,
    [Parameter(ParameterSetName = 'Unregister', Mandatory = $true)][switch]$Unregister,
    [Parameter(ParameterSetName = 'Show')][Parameter(ParameterSetName = 'Register')][string]$Python,
    [Parameter(ParameterSetName = 'Show')][Parameter(ParameterSetName = 'Register')][string]$Checkout,
    [Parameter(ParameterSetName = 'Show')][Parameter(ParameterSetName = 'Register')][string]$DevRoot
)

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

$TaskPath = '\MonkeyHub\'
$TaskName = 'DailyLocalBenchmark'
$Runner = 'tools\benchmarks\daily_benchmark.py'

function Get-ExistingTask {
    Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
}

# A native command's exit code and output; Windows PowerShell would turn its stderr into a terminating error.
function Invoke-Native([string]$File, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $File @Arguments 2>&1 | ForEach-Object { "$_" }
        [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
    } finally {
        $ErrorActionPreference = $previous
    }
}

if ($Unregister) {
    $existing = Get-ExistingTask
    if (-not $existing) {
        Write-Host "No task $TaskPath$TaskName is registered; nothing to remove."
        return
    }
    if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", 'Unregister the scheduled task')) {
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
        Write-Host "Removed $TaskPath$TaskName. The runner's logs and results stay under <development root>\temp\benchmark-local."
    }
    return
}

# The interpreter: pythonw.exe beside the chosen python.exe, so the task opens no window.
if (-not $Python) {
    $found = Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } | Select-Object -First 1
    if (-not $found) { throw 'No python.exe on PATH; pass -Python <python.exe>.' }
    $Python = $found.Source
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$pythonw = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw "No pythonw.exe beside $Python." }

# The checkout: this repository's main worktree unless one is named.
if (-not $Checkout) {
    $common = Invoke-Native git @('-C', $PSScriptRoot, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if ($common.ExitCode -ne 0) { throw "Cannot find the repository of ${PSScriptRoot}: $($common.Output); pass -Checkout." }
    $Checkout = Split-Path -Parent $common.Output.Trim()
}
$Checkout = (Resolve-Path -LiteralPath $Checkout).Path
$script = Join-Path $Checkout $Runner
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "$script does not exist; pass -Checkout." }
if ((Invoke-Native $Python @($script, 'local', '--help')).ExitCode -ne 0) {
    throw "$script has no local runner yet (local --help failed): update $Checkout, or pass -Checkout."
}

# The development root, as tools\dev\workspace.py configured it.
if (-not $DevRoot) {
    $paths = Invoke-Native $Python @((Join-Path $Checkout 'tools\dev\workspace.py'), 'paths')
    if ($paths.ExitCode -ne 0) { throw "tools\dev\workspace.py paths failed: $($paths.Output); pass -DevRoot." }
    $DevRoot = ($paths.Output | ConvertFrom-Json).workspaceRoot
}
if (-not $DevRoot -or -not [IO.Path]::IsPathRooted($DevRoot)) { throw 'No development root; pass -DevRoot.' }
# A trailing backslash would escape the closing quote of the task's argument.
$DevRoot = $DevRoot.TrimEnd('\', '/')

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$escape = { param([string]$value) [Security.SecurityElement]::Escape($value) }
$arguments = "`"$script`" local --dev-root `"$DevRoot`""
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$(& $escape $identity.Name)</Author>
    <Description>MonkeyHub daily local benchmark (GH-547): once a day while nobody uses the computer, measures fresh copies of the installed MonkeyHub on an isolated runtime root and pushes the result to results/windows-local/ on benchmark-data. Log: $(& $escape (Join-Path $DevRoot 'temp\benchmark-local\logs\local.log')). See docs/development/benchmarks.md.</Description>
    <URI>$(& $escape "$TaskPath$TaskName")</URI>
  </RegistrationInfo>
  <Triggers>
    <IdleTrigger>
      <Enabled>true</Enabled>
    </IdleTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$(& $escape $identity.User.Value)</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>true</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$(& $escape $pythonw)</Command>
      <Arguments>$(& $escape $arguments)</Arguments>
      <WorkingDirectory>$(& $escape $Checkout)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$existing = Get-ExistingTask
Write-Host "Task:     $TaskPath$TaskName ($(if ($existing) { "registered now, state $($existing.State)" } else { 'not registered now' }))"
Write-Host "Runs as:  $($identity.Name), only while logged on, without elevation"
Write-Host "Command:  `"$pythonw`" $arguments"
Write-Host "In:       $Checkout"
Write-Host "Log:      $(Join-Path $DevRoot 'temp\benchmark-local\logs\local.log')"
Write-Host ''
Write-Host $xml

if (-not $Register) {
    Write-Host ''
    Write-Host 'Nothing was registered. Run again with -Register to register this task, or -Unregister to remove it.'
    return
}
if ($PSCmdlet.ShouldProcess("$TaskPath$TaskName", 'Register the scheduled task')) {
    $task = Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Xml $xml -Force
    Write-Host ''
    Write-Host "Registered $TaskPath$TaskName (state $($task.State))."
}
