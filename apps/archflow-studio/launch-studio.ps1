param(
    [string]$RuntimeConfig = (Join-Path $PSScriptRoot 'runtime.json'),
    [switch]$NoBrowser,
    [switch]$HideConsole,
    [switch]$Hub,
    [string]$Python,
    [string]$RuntimeRoot,
    [string]$HubWebDir,
    [string]$StudioWebDir,
    [ValidateRange(1, 65535)][int]$Port = 8790
)

# One-click start for MonkeyArch: the ArchFlow Studio API (FastAPI) and the web client
# (Vite), bound to the project named in runtime.json. Launching is meant to feel like
# opening an application, so there is no console to read and no console to close:
#
#   - a splash window says what is happening while the two servers come up, and a
#     refusal turns that same window red instead of pausing a black box;
#   - once the browser is open the splash goes and a tray icon stays, and "Quit
#     MonkeyArch" in its menu is what stops both servers.
#
# The methodology and the protocol are still ArchFlow; MonkeyArch is the application.
#
# Windows PowerShell 5.1 is the floor: WinForms only, no WPF, no `??`, no ternary, no
# .NET-Core-only overloads. -HideConsole is what OPEN_MONKEYARCH.bat passes; run this
# script from a console without it and you get the console output as well as the splash.

$ErrorActionPreference = 'Stop'
# UTF-8 so a project path with Chinese characters prints as itself. The setter also flips the
# console output code page, which is the half that actually makes it render; it throws when the
# process has no console (a hidden or fully redirected launch), and that is not a reason to fail.
# UTF8Encoding($false) keeps a BOM out of a redirected stdout.
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
# Throws once a control already exists in the process; nothing has been built yet, but a
# reload in the same host would hit that, and the setting is cosmetic either way.
try { [System.Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false) } catch { }

if ($HideConsole) {
    # -WindowStyle Hidden already hides the window this process was given; hiding it again
    # from inside covers the launch that did not get that switch, and costs nothing when the
    # window is hidden already. A console-less process answers IntPtr.Zero and is left alone.
    $windowApi = @'
[DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
'@
    try {
        $native = Add-Type -MemberDefinition $windowApi -Name 'ConsoleWindow' -Namespace 'MonkeyArch' -PassThru
        $consoleWindow = $native::GetConsoleWindow()
        if ($consoleWindow -ne [System.IntPtr]::Zero) { $native::ShowWindow($consoleWindow, 0) | Out-Null }
    } catch { }
}

$studioRoot = $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $studioRoot '..\..')).Path
$apiRoot = Join-Path $studioRoot 'api'
$webRoot = Join-Path $studioRoot 'web'
$logRoot = Join-Path $studioRoot '.runtime'
if ($Hub) {
    if (-not $RuntimeRoot) { $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'MonkeyHub' }
    $RuntimeRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RuntimeRoot)
    $logRoot = Join-Path $RuntimeRoot 'logs'
}
$assetRoot = Join-Path $studioRoot 'assets'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

$script:LogRoot = $logRoot
$script:WebUrl = $null
$script:Children = @()
$script:Splash = $null
$script:FaultOpen = $false
$script:Quitting = $false
$script:LaunchLocks = @()

# --- the application name and the same semantic palette the web shell uses
$BrandName = 'MonkeyArch'
$BrandLineOne = 'ArchFlow modeling workspace'
if ($Hub) {
    $BrandName = 'MonkeyHub'
    $BrandLineOne = 'Monkey applications on this computer'
}
$StepCount = 8

$Ink = [System.Drawing.Color]::FromArgb(25, 27, 25)
$Stone = [System.Drawing.Color]::FromArgb(240, 241, 236)
$StoneInk = [System.Drawing.Color]::FromArgb(211, 214, 208)
$StoneDim = [System.Drawing.Color]::FromArgb(179, 184, 177)
$Accent = [System.Drawing.Color]::FromArgb(127, 166, 210)
$AccentInk = [System.Drawing.Color]::FromArgb(14, 27, 40)
$Rim = [System.Drawing.Color]::FromArgb(52, 57, 52)
$FaultInk = [System.Drawing.Color]::FromArgb(46, 24, 23)
$FaultPanel = [System.Drawing.Color]::FromArgb(35, 18, 17)
$FaultRed = [System.Drawing.Color]::FromArgb(210, 123, 114)

function Get-BrandIcon {
    # One icon, drawn by assets\make_icon.py and never written here. The splash and the
    # tray fall back to their own defaults when it is missing, so this returns $null
    # rather than throwing: a missing icon is not a reason to refuse to start.
    $path = Join-Path $assetRoot 'monkeyarch.ico'
    if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    return $null
}

function New-Label([string]$Text, [System.Drawing.Font]$Font, [System.Drawing.Color]$Colour) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Font = $Font
    $label.ForeColor = $Colour
    $label.BackColor = [System.Drawing.Color]::Transparent
    $label.AutoSize = $true
    return $label
}

function New-Splash {
    # This compact launch surface is built before runtime.json is read because the first
    # refusal needs somewhere to appear. Its rail is determinate: unlike the browser's
    # network/3DM waits, the launcher knows the eight steps it performs.
    $form = New-Object System.Windows.Forms.Form
    $form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
    $form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
    $form.ClientSize = New-Object System.Drawing.Size(640, 360)
    $form.BackColor = $Ink
    $form.Text = $BrandName
    $form.TopMost = $true
    $form.ShowInTaskbar = $true
    $form.KeyPreview = $true
    $iconPath = Get-BrandIcon
    if ($iconPath) {
        try { $form.Icon = New-Object System.Drawing.Icon($iconPath) } catch { }
    }
    $form.Add_Paint({
        param($sender, $eventArgs)
        if (-not $script:Splash) { return }
        $pen = New-Object System.Drawing.Pen($script:Splash.Rim, 1)
        $eventArgs.Graphics.DrawRectangle($pen, 0, 0, $sender.ClientSize.Width - 1, $sender.ClientSize.Height - 1)
        $pen.Dispose()

        # A static maker's mark, not a second progress animation: the arch, its
        # keystone, and the hanging monkey are the same reduced drawing used by
        # the browser boot surface. The real eight-step rail remains the only
        # thing that moves.
        if ($script:Splash.ProgressTrack.Visible) {
            $graphics = $eventArgs.Graphics
            $graphicsState = $graphics.Save()
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
            $graphics.TranslateTransform(64, 48)
            $graphics.ScaleTransform(0.92, 0.92)

            $archPen = New-Object System.Drawing.Pen($script:Splash.MarkArch, 1.6)
            $figurePen = New-Object System.Drawing.Pen($script:Splash.MarkFigure, 2.1)
            foreach ($markPen in @($archPen, $figurePen)) {
                $markPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
                $markPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
                $markPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
            }
            $keyBrush = New-Object System.Drawing.SolidBrush($script:Splash.MarkKeystone)
            $groundBrush = New-Object System.Drawing.SolidBrush($script:Splash.MarkGround)

            $graphics.DrawArc($archPen, 6, 7, 44, 44, 180, 180)
            $graphics.DrawLine($archPen, 6, 29, 6, 52)
            $graphics.DrawLine($archPen, 50, 29, 50, 52)

            [System.Drawing.PointF[]]$keyPoints = @(
                (New-Object System.Drawing.PointF -ArgumentList 23, 3),
                (New-Object System.Drawing.PointF -ArgumentList 33, 3),
                (New-Object System.Drawing.PointF -ArgumentList 31, 17),
                (New-Object System.Drawing.PointF -ArgumentList 25, 17)
            )
            $graphics.FillPolygon($keyBrush, $keyPoints)

            $graphics.DrawLine($figurePen, 28, 16, 26, 25)
            $graphics.FillEllipse($groundBrush, 13, 26.5, 7, 7)
            $graphics.DrawEllipse($figurePen, 13, 26.5, 7, 7)
            $graphics.FillEllipse($groundBrush, 26, 26, 7, 7)
            $graphics.DrawEllipse($figurePen, 26, 26, 7, 7)
            $graphics.FillEllipse($groundBrush, 16, 22, 14, 14)
            $graphics.DrawEllipse($figurePen, 16, 22, 14, 14)

            $bodyPath = New-Object System.Drawing.Drawing2D.GraphicsPath
            $bodyPath.AddBezier(20.5, 35.5, 18.3, 39, 18.9, 44.1, 22.5, 47.2)
            $bodyPath.AddLine(22.5, 47.2, 29, 46.5)
            $bodyPath.AddBezier(29, 46.5, 31.8, 43.1, 31.7, 38.7, 28.8, 35.3)
            $graphics.DrawPath($figurePen, $bodyPath)
            $graphics.DrawLine($figurePen, 21, 38, 13, 43)
            $graphics.DrawLine($figurePen, 23, 47, 18, 52)
            $graphics.DrawLine($figurePen, 28, 46.5, 32, 50.7)
            $graphics.DrawBezier($figurePen, 29, 38.5, 39, 33.5, 49, 39.5, 49, 47.5)
            $graphics.DrawBezier($figurePen, 49, 47.5, 49, 53.5, 44, 56.5, 39, 55.5)
            $graphics.DrawBezier($figurePen, 39, 55.5, 34, 54.5, 32, 50.5, 34, 46.5)
            $graphics.DrawBezier($figurePen, 34, 46.5, 36, 43.5, 41, 43.5, 43, 46.5)
            $graphics.DrawBezier($figurePen, 43, 46.5, 44, 48.5, 43, 50.5, 41, 50.5)

            $bodyPath.Dispose()
            $groundBrush.Dispose()
            $keyBrush.Dispose()
            $figurePen.Dispose()
            $archPen.Dispose()
            $graphics.Restore($graphicsState)
        }
    })

    $strip = New-Object System.Windows.Forms.Panel
    $strip.BackColor = $Accent
    $strip.Height = 2
    $strip.Dock = [System.Windows.Forms.DockStyle]::Top
    $form.Controls.Add($strip)

    $title = New-Label $BrandName (New-Object System.Drawing.Font('Segoe UI', 18, [System.Drawing.FontStyle]::Bold)) $Stone
    $tagline = New-Label $BrandLineOne (New-Object System.Drawing.Font('Segoe UI', 11)) $StoneDim
    $progress = New-Label '' (New-Object System.Drawing.Font('Segoe UI', 9)) $StoneDim

    $progressTrack = New-Object System.Windows.Forms.Panel
    $progressTrack.BackColor = $Rim
    $progressTrack.Size = New-Object System.Drawing.Size(512, 2)
    $progressTrack.Location = New-Object System.Drawing.Point(64, 190)

    $progressFill = New-Object System.Windows.Forms.Panel
    $progressFill.BackColor = $Accent
    $progressFill.Size = New-Object System.Drawing.Size(0, 2)
    $progressFill.Location = New-Object System.Drawing.Point(0, 0)
    $progressTrack.Controls.Add($progressFill)

    $fault = New-Object System.Windows.Forms.TextBox
    $fault.Multiline = $true
    $fault.ReadOnly = $true
    $fault.ScrollBars = [System.Windows.Forms.ScrollBars]::Vertical
    $fault.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $fault.BackColor = $FaultPanel
    $fault.ForeColor = $Stone
    $fault.Font = New-Object System.Drawing.Font('Consolas', 9)
    $fault.Size = New-Object System.Drawing.Size(552, 184)
    $fault.Location = New-Object System.Drawing.Point(44, 100)
    $fault.Visible = $false

    $close = New-Object System.Windows.Forms.Button
    $close.Text = 'Close'
    $close.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $close.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $close.FlatAppearance.BorderSize = 0
    $close.BackColor = $Accent
    $close.ForeColor = $AccentInk
    $close.Size = New-Object System.Drawing.Size(104, 30)
    $close.Location = New-Object System.Drawing.Point(492, 298)
    $close.Visible = $false
    $close.Add_Click({ $script:FaultOpen = $false })

    foreach ($control in @($title, $tagline, $progressTrack, $progress, $fault, $close)) {
        $form.Controls.Add($control)
    }
    $form.Add_KeyDown({
        param($sender, $eventArgs)
        if ($eventArgs.KeyCode -eq [System.Windows.Forms.Keys]::Escape -and $script:FaultOpen) { $script:FaultOpen = $false }
    })

    $title.Top = 55
    $tagline.Top = 87
    $progress.Top = 208

    $script:Splash = @{
        Form = $form; Title = $title; Tagline = $tagline; Progress = $progress
        ProgressTrack = $progressTrack; ProgressFill = $progressFill
        Fault = $fault; Close = $close; Rim = $Rim
        MarkArch = $StoneDim; MarkFigure = $StoneInk; MarkKeystone = $Accent
        MarkGround = $Ink
    }

    Set-SplashLayout
    $form.Show()
    $form.Activate()
    return $script:Splash
}

function Set-SplashLayout {
    $splash = $script:Splash
    if ($splash.Fault.Visible) {
        $splash.Title.Left = 44
        $splash.Tagline.Left = 44
    } else {
        $splash.Title.Left = 124
        $splash.Tagline.Left = 124
    }
    $splash.Progress.Left = 64
}

function Set-SplashStep([int]$Index, [string]$Text) {
    Write-Host ("  step " + $Index + "/" + $StepCount + " : " + $Text)
    $splash = $script:Splash
    if (-not $splash -or $splash.Form.IsDisposed) { return }
    $splash.Progress.Text = "$Index / $StepCount  ·  $Text"
    $boundedIndex = [Math]::Max(0, [Math]::Min($StepCount, $Index))
    $splash.ProgressFill.Width = [int][Math]::Round($splash.ProgressTrack.Width * $boundedIndex / $StepCount)
    Set-SplashLayout
    Invoke-Pump 0
}

function Invoke-Pump([int]$Milliseconds) {
    # The launch surface lives on this thread, so every wait in this script is a wait that pumps.
    [System.Windows.Forms.Application]::DoEvents()
    if ($Milliseconds -le 0) { return }
    $deadline = (Get-Date).AddMilliseconds($Milliseconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 25
        [System.Windows.Forms.Application]::DoEvents()
    }
}

function Show-Fault([string]$Message) {
    # The same window, turned red. Every `throw` sentence in this script is written to be read
    # here, so nothing is reworded on the way: the message is the message.
    Write-Host ''
    Write-Host "$BrandName needs attention." -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    if (-not $script:Splash -or $script:Splash.Form.IsDisposed) { New-Splash | Out-Null }
    $splash = $script:Splash
    $splash.Form.BackColor = $FaultInk
    $splash.Title.Font = New-Object System.Drawing.Font('Segoe UI', 17, [System.Drawing.FontStyle]::Bold)
    $splash.Title.Text = "$BrandName needs attention"
    $splash.Title.Top = 30
    $splash.Tagline.Text = 'See the details and logs below.'
    $splash.Tagline.ForeColor = $FaultRed
    $splash.Tagline.Top = 70
    $splash.Progress.Visible = $false
    $splash.ProgressTrack.Visible = $false
    $splash.Fault.Text = ($Message -replace "`r`n", "`n") -replace "`n", "`r`n"
    $splash.Fault.Visible = $true
    $splash.Close.Visible = $true
    $splash.Close.BringToFront()
    Set-SplashLayout
    $splash.Form.TopMost = $true
    $splash.Form.Show()
    $splash.Form.Activate()

    $script:FaultOpen = $true
    $splash.Form.Add_FormClosed({ $script:FaultOpen = $false })
    while ($script:FaultOpen -and -not $splash.Form.IsDisposed) { Invoke-Pump 50 }
    if (-not $splash.Form.IsDisposed) { $splash.Form.Close() }
}

function Close-Splash {
    $splash = $script:Splash
    if (-not $splash) { return }
    if (-not $splash.Form.IsDisposed) { $splash.Form.Close() }
    $script:Splash = $null
}

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
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
        } catch { }
        Invoke-Pump 400
    }
    return $false
}

function Lock-LaunchPorts([int[]]$Ports) {
    foreach ($number in ($Ports | Sort-Object -Unique)) {
        if ($number -lt 1 -or $number -gt 65535) { throw "Invalid port: $number" }
        $mutex = New-Object System.Threading.Mutex($false, "Local\MonkeyApps-Port-$number")
        $acquired = $false
        try { $acquired = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            $mutex.Dispose()
            throw "Another Monkey launcher is already opening or using port $number. Use its existing window or tray icon."
        }
        $script:LaunchLocks += $mutex
        if (-not (Test-PortFree $number)) {
            throw "Port $number is already in use. Choose another port or close that application yourself; this launcher has not taken ownership of it."
        }
    }
}

function Get-SourceRevision([string]$Root) {
    $versionFile = Join-Path $Root 'source-version.txt'
    if (Test-Path -LiteralPath $versionFile -PathType Leaf) {
        $revision = (Get-Content -LiteralPath $versionFile -Raw -Encoding utf8).Trim()
    } else {
        $result = Invoke-Quiet 'git' @('-C', $Root, 'rev-parse', 'HEAD')
        if ($result.ExitCode -ne 0) { throw 'Cannot identify this source version. The packaged source-version.txt is missing and Git could not read HEAD.' }
        $revision = $result.Output.Trim()
    }
    if ($revision -notmatch '^[0-9a-fA-F]{40}$') { throw 'The source version must be a complete 40-character Git commit id.' }
    return $revision.ToLowerInvariant()
}

function ConvertTo-ProcessArgument([string]$Value) {
    # Windows command-line quoting, including quotes and trailing backslashes in paths.
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    return '"' + [regex]::Replace($escaped, '(\\+)$', '$1$1') + '"'
}

function Start-OwnedProcess {
    param([string]$Name, [string]$Exe, [string[]]$Arguments, [string]$Directory,
          [string]$OutputLog, [string]$ErrorLog)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Exe
    $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $info.WorkingDirectory = $Directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    # Copy bytes asynchronously without PowerShell callbacks on background .NET threads.
    $outStream = New-Object System.IO.FileStream($OutputLog, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite, 1)
    $errStream = New-Object System.IO.FileStream($ErrorLog, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite, 1)
    try { [void]$process.Start() } catch { $outStream.Dispose(); $errStream.Dispose(); $process.Dispose(); throw }
    $child = [pscustomobject]@{
        Name = $Name; Process = $process; ErrorLog = $ErrorLog; OutputLog = $OutputLog
        OutputStream = $outStream; ErrorStream = $errStream
        OutputCopy = $process.StandardOutput.BaseStream.CopyToAsync($outStream)
        ErrorCopy = $process.StandardError.BaseStream.CopyToAsync($errStream)
        StopRequested = $false; LogsClosed = $false
    }
    $script:Children += $child
    return $child
}

function Wait-ManagedHealth {
    param([string]$Url, [int]$Seconds, [System.Diagnostics.Process]$Process,
          [string]$InstanceId, [string]$Service, [string]$SourceRevision)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) { return $false }
        $health = $null
        try { $health = Invoke-RestMethod -Uri $Url -TimeoutSec 2 } catch { }
        if ($health) {
            if ($health.status -ne 'ok' -or $health.service -cne $Service -or
                $health.managedInstanceId -cne $InstanceId -or
                ($health.processId -ne $Process.Id -and $health.parentProcessId -ne $Process.Id) -or
                $health.sourceRevision -cne $SourceRevision -or -not $health.serverVersion) {
                throw "Service identity mismatch at $Url. Expected $Service, instance $InstanceId, PID $($Process.Id), source $SourceRevision; received $($health.service), instance $($health.managedInstanceId), PID $($health.processId), source $($health.sourceRevision), version $($health.serverVersion)."
            }
            Write-Host "  service : $($health.service) $($health.serverVersion), source $($health.sourceRevision), PID $($health.processId)"
            return $true
        }
        Invoke-Pump 200
    }
    return $false
}

function Get-ManagedViteScript {
    # Keep the existing dev sync and Vite configuration, but own Node directly and close Vite.
    return @'
let stopping = false;
let finish;
const stopped = new Promise(resolve => { finish = resolve; });
const stop = () => { stopping = true; finish(); };
let pending = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', data => {
  pending += data;
  const lines = pending.split(/\r?\n/);
  pending = lines.pop();
  if (lines.some(line => line.trim() === 'stop')) stop();
});
process.stdin.on('end', stop);
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
let server;
try {
  await import('./scripts/sync-rhino3dm.mjs');
  const { createServer } = await import('vite');
  server = await createServer({ server: { port: Number(process.argv[1]), strictPort: true } });
  if (!stopping) await server.listen();
  if (!stopping) console.log('MONKEY_WEB_READY', process.argv[2], process.pid);
  await stopped;
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (server) await server.close();
  process.stdin.destroy();
}
'@
}

function Wait-OwnedWeb([object]$Child, [string]$InstanceId, [int]$WebPort) {
    $deadline = (Get-Date).AddSeconds(90)
    $marker = "MONKEY_WEB_READY $InstanceId $($Child.Process.Id)"
    while ((Get-Date) -lt $deadline) {
        if ($Child.Process.HasExited) { return $false }
        if ((Get-LogTail $Child.OutputLog 20).Contains($marker)) {
            return (Wait-Http "http://127.0.0.1:$WebPort/" 5 $Child.Process)
        }
        Invoke-Pump 200
    }
    return $false
}

function Stop-Children {
    $script:Quitting = $true
    if ($script:Watchdog) { $script:Watchdog.Stop() }
    $active = @($script:Children | Where-Object { -not $_.Process.HasExited })
    if ($active.Count) {
        if (-not $script:Splash -or $script:Splash.Form.IsDisposed) { New-Splash | Out-Null }
        Set-SplashStep 8 'Finishing running work and closing services...'
    }
    foreach ($child in $script:Children) {
        if (-not $child.Process.HasExited -and -not $child.StopRequested) {
            $child.StopRequested = $true
            try {
                $child.Process.StandardInput.WriteLine('stop')
                $child.Process.StandardInput.Flush()
            } catch { } finally {
                # EOF is also a normal stop request, including when the launcher exits.
                try { $child.Process.StandardInput.Close() } catch { }
            }
        }
    }
    foreach ($child in $script:Children) {
        while (-not $child.Process.HasExited) { Invoke-Pump 50 }
        if (-not $child.LogsClosed) {
            $child.Process.WaitForExit()
            try {
                $child.OutputCopy.GetAwaiter().GetResult()
                $child.ErrorCopy.GetAwaiter().GetResult()
            } finally {
                $child.OutputStream.Dispose()
                $child.ErrorStream.Dispose()
                $child.LogsClosed = $true
            }
        }
    }
    Close-Splash
}

function Start-Tray([string]$url) {
    # What is left on screen once the app is open: one icon, three sentences, and the only
    # way to stop the pair that does not involve finding a process.
    $tray = New-Object System.Windows.Forms.NotifyIcon
    $iconPath = Get-BrandIcon
    if ($iconPath) {
        $tray.Icon = New-Object System.Drawing.Icon($iconPath)
    } else {
        # A NotifyIcon with no icon is invisible, and an invisible tray icon is an
        # application with no way to quit it. Any icon beats none.
        $tray.Icon = [System.Drawing.SystemIcons]::Application
    }
    $tray.Text = "$BrandName · $url"
    $menu = New-Object System.Windows.Forms.ContextMenuStrip
    $openItem = $menu.Items.Add("Open $BrandName")
    $logsItem = $menu.Items.Add('Show logs')
    [void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
    $quitItem = $menu.Items.Add("Quit $BrandName")
    $openItem.Font = New-Object System.Drawing.Font($menu.Font, [System.Drawing.FontStyle]::Bold)
    $tray.ContextMenuStrip = $menu
    $openItem.Add_Click({ Start-Process $script:WebUrl })
    $logsItem.Add_Click({ Start-Process 'explorer.exe' $script:LogRoot })
    $quitItem.Add_Click({ Invoke-Quit })
    $tray.Add_DoubleClick({ Start-Process $script:WebUrl })
    $script:Tray = $tray
    $tray.Visible = $true
    $tray.ShowBalloonTip(4000, $BrandName, "Running on $url. Quit it from this icon.", [System.Windows.Forms.ToolTipIcon]::Info)
    return $tray
}

function Invoke-Quit {
    if ($script:Quitting) { return }
    $script:Quitting = $true
    Stop-Children
    if ($script:Tray) { $script:Tray.Visible = $false }
    [System.Windows.Forms.Application]::ExitThread()
}

function Invoke-AppLoop {
    Set-SplashStep 8 'opening the application'
    if ($script:OpenBrowser -and -not $NoBrowser) { Start-Process $script:WebUrl }
    Close-Splash
    $tray = Start-Tray $script:WebUrl
    Write-Host "$BrandName is running. Quit it from the tray icon." -ForegroundColor Green
    Write-Host "  logs: $logRoot"
    $watchdog = New-Object System.Windows.Forms.Timer
    $watchdog.Interval = 2000
    $watchdog.Add_Tick({
        if ($script:Quitting) { return }
        foreach ($child in $script:Children) {
            if ($child.Process.HasExited) {
                $script:Watchdog.Stop()
                $message = "$($child.Name) exited with code $($child.Process.ExitCode). Its log said:$([Environment]::NewLine)$(Get-LogTail $child.ErrorLog 25)$([Environment]::NewLine)(logs: $script:LogRoot)"
                Stop-Children
                if ($script:Tray) { $script:Tray.Visible = $false }
                Show-Fault $message
                [System.Windows.Forms.Application]::ExitThread()
                return
            }
        }
    })
    $script:Watchdog = $watchdog
    $watchdog.Start()
    try { [System.Windows.Forms.Application]::Run() } finally {
        $watchdog.Stop()
        $watchdog.Dispose()
        $tray.Visible = $false
        $tray.Dispose()
        $script:Watchdog = $null
        $script:Tray = $null
    }
}

function Invoke-HubLaunch {
    Set-SplashStep 1 'checking the Hub package'
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Hub -Python must name the installed Python executable.' }
    foreach ($directory in @($HubWebDir, $StudioWebDir)) {
        if (-not $directory -or -not (Test-Path -LiteralPath (Join-Path $directory 'index.html') -PathType Leaf)) {
            throw "The built web directory is missing its index.html: $directory"
        }
    }
    $Python = (Resolve-Path -LiteralPath $Python).ProviderPath
    $HubWebDir = (Resolve-Path -LiteralPath $HubWebDir).ProviderPath
    $StudioWebDir = (Resolve-Path -LiteralPath $StudioWebDir).ProviderPath
    $entry = Join-Path $repoRoot 'apps\monkeyhub\run.py'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw "The Hub entry is missing: $entry" }
    Lock-LaunchPorts @($Port)
    $revision = Get-SourceRevision $repoRoot
    $instanceId = [Guid]::NewGuid().ToString()
    $hubOut = Join-Path $logRoot "hub-$stamp.out.log"
    $hubErr = Join-Path $logRoot "hub-$stamp.err.log"
    $hubArgs = @('-u', $entry, '--runtime-root', $RuntimeRoot, '--hub-web-dir', $HubWebDir,
        '--studio-web-dir', $StudioWebDir, '--port', "$Port", '--no-browser',
        '--managed-stdin', '--managed-instance-id', $instanceId)
    Set-SplashStep 4 'starting MonkeyHub'
    $child = Start-OwnedProcess 'MonkeyHub' $Python $hubArgs $repoRoot $hubOut $hubErr
    Set-SplashStep 5 "waiting for MonkeyHub on :$Port"
    if (-not (Wait-ManagedHealth "http://127.0.0.1:$Port/api/health" 60 $child.Process $instanceId 'monkeyhub-api' $revision)) {
        throw "MonkeyHub did not answer with its service identity. Its log said:$([Environment]::NewLine)$(Get-LogTail $hubErr 25)$([Environment]::NewLine)(full log: $hubErr)"
    }
    $script:WebUrl = "http://127.0.0.1:$Port"
    $script:OpenBrowser = $true
}

# Everything below runs inside one try: a refusal reaches the reader as the sentence that
# explains it, in the splash window, not as a PowerShell stack trace over a console that is
# about to close.
try {

New-Splash | Out-Null

if ($Hub) {
    Invoke-HubLaunch
    Invoke-AppLoop
    return
}

# --- runtime.json selects the project and is validated before anything is started
Set-SplashStep 1 'validating runtime.json'
if (-not (Test-Path -LiteralPath $RuntimeConfig -PathType Leaf)) {
    $runtimeExample = Join-Path $studioRoot 'runtime.example.json'
    throw "Runtime configuration is missing: $RuntimeConfig$([Environment]::NewLine)Copy '$runtimeExample' to your own runtime.json, then set project_dir to an existing P036 project and python to your environment's Python executable.$([Environment]::NewLine)Pass -RuntimeConfig <your config path> when keeping the configuration outside the Studio directory."
}
$runtime = Get-Content -LiteralPath $RuntimeConfig -Raw -Encoding utf8 | ConvertFrom-Json
if ($runtime.schema_version -ne 'archflow-studio-runtime@1') { throw "runtime.json has an unsupported schema_version: $($runtime.schema_version)" }
$projectDir = [string]$runtime.project_dir
if (-not $projectDir) { throw "Set project_dir to an existing P036 project directory in '$RuntimeConfig'. The example leaves this field empty for you to fill in." }
if (-not (Test-Path -LiteralPath $projectDir -PathType Container)) {
    throw "project_dir does not exist: $projectDir  (named by $RuntimeConfig)"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectDir 'project.json') -PathType Leaf)) {
    throw "project_dir is not a P036 project (no project.json in it): $projectDir  (named by $RuntimeConfig)"
}
$apiPort = if ($runtime.api_port) { [int]$runtime.api_port } else { 8000 }
$webPort = if ($runtime.web_port) { [int]$runtime.web_port } else { 5174 }

# An existing executable path stays intact, including spaces in a venv path.
# A quoted executable may also have simple flags; retain the legacy "py -3.12" form.
$pythonCommand = ([string]$runtime.python).Trim()
if ($pythonCommand -match '^"([^"]+)"(?:\s+(.*))?$') {
    $pythonParts = @($Matches[1]) + @($Matches[2] -split '\s+' | Where-Object { $_ })
} elseif ($pythonCommand -and (Test-Path -LiteralPath $pythonCommand -PathType Leaf)) {
    $pythonParts = @($pythonCommand)
} else {
    $pythonParts = @($pythonCommand -split '\s+' | Where-Object { $_ })
}
if ($pythonParts.Count -eq 0) { $pythonParts = @('py', '-3.12') }
$pythonExe = $pythonParts[0]
$pythonArgs = @()
if ($pythonParts.Count -gt 1) { $pythonArgs = @($pythonParts[1..($pythonParts.Count - 1)]) }

Write-Host "$BrandName (ArchFlow Studio)" -ForegroundColor Cyan
Write-Host "  config  : $RuntimeConfig"
Write-Host "  project : $projectDir"
Write-Host "  api     : http://127.0.0.1:$apiPort   web: http://127.0.0.1:$webPort"

# --- preflight: python + fastapi, node_modules, free ports
Set-SplashStep 2 'checking python and fastapi'
$check = Invoke-Quiet $pythonExe ($pythonArgs + @('-c', 'import fastapi, uvicorn, json, sys; print(json.dumps(dict(executable=sys.executable, arguments=sys.orig_argv[1:-2])))'))
if ($check.ExitCode -ne 0) {
    $requirements = Join-Path $repoRoot 'apps\archflow-studio\api\requirements.txt'
    throw "'$($runtime.python)' cannot import fastapi/uvicorn. Install them with:$([Environment]::NewLine)  $($runtime.python) -m pip install -r $requirements$([Environment]::NewLine)$($check.Output)"
}
# Resolve py -3.12 to its interpreter; keep actual Python flags without owning a launcher shim.
$pythonProbe = ($check.Output.Trim() -split '[\r\n]+')[-1] | ConvertFrom-Json
$pythonExe = [string]$pythonProbe.executable
$pythonArgs = @($pythonProbe.arguments)
if ($apiPort -eq $webPort) { throw 'The API and web client need different ports.' }
Lock-LaunchPorts @($apiPort, $webPort)
$sourceRevision = Get-SourceRevision $repoRoot
Set-SplashStep 3 'checking web dependencies'
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules\.bin\vite.cmd'))) {
    Set-SplashStep 3 'installing web dependencies · this takes a few minutes'
    Write-Host "  web deps missing; running npm install (this takes a few minutes) ..." -ForegroundColor Yellow
    $installLog = Join-Path $logRoot "npm-install-$stamp.log"
    $installErr = Join-Path $logRoot "npm-install-$stamp.err.log"
    # Started rather than called, so the launch surface stays responsive through a long install.
    $install = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', 'npm.cmd install') -WorkingDirectory $webRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $installLog -RedirectStandardError $installErr
    while (-not $install.HasExited) { Invoke-Pump 100 }
    $install.WaitForExit()
    if ($install.ExitCode -ne 0) {
        throw "npm install failed in $webRoot$([Environment]::NewLine)$(Get-LogTail $installErr 25)$([Environment]::NewLine)(full logs: $installLog, $installErr)"
    }
}
if ([string]$runtime.codex -and -not (Test-Path -LiteralPath ([string]$runtime.codex) -PathType Leaf)) {
    Write-Host "  warning : codex not found at $($runtime.codex); the API refuses to start with intent_provider codex until it is (it needs codex --version to sign its receipts)." -ForegroundColor Yellow
}

# --- environment the API reads (settings.py and intent_agent.py name these; no default project in code)
$env:ARCHFLOW_STUDIO_PROJECT_DIR = $projectDir
if ([string]$runtime.reference_run) { $env:ARCHFLOW_STUDIO_REFERENCE_RUN = [string]$runtime.reference_run } else { Remove-Item Env:ARCHFLOW_STUDIO_REFERENCE_RUN -ErrorAction SilentlyContinue }
# CAD export is one setting, `cad_export` (occt | rhino | off), forwarded as ARCHFLOW_STUDIO_CAD_EXPORT.
# A runtime.json that still carries the older `rhino_export` boolean and no `cad_export` keeps being
# read as it always was (true turns export on, false keeps it off; the API maps "on" to its ordinary
# OCCT export and never to Rhino). A file naming neither leaves both variables unset, so the API's own
# default applies. Nothing is left over from a previous launch in either case. A `cad_export` that
# is only whitespace names nothing: it is trimmed before it counts as present, so it neither
# forwards a blank the API would read as its default nor silences an explicit `rhino_export`.
$cadExport = ([string]$runtime.cad_export).Trim()
if ($cadExport) {
    $env:ARCHFLOW_STUDIO_CAD_EXPORT = $cadExport
    Remove-Item Env:ARCHFLOW_STUDIO_RHINO_EXPORT -ErrorAction SilentlyContinue
} else {
    Remove-Item Env:ARCHFLOW_STUDIO_CAD_EXPORT -ErrorAction SilentlyContinue
    if ($null -ne $runtime.rhino_export) {
        if ($runtime.rhino_export) { $env:ARCHFLOW_STUDIO_RHINO_EXPORT = '1' } else { $env:ARCHFLOW_STUDIO_RHINO_EXPORT = '0' }
    } else {
        Remove-Item Env:ARCHFLOW_STUDIO_RHINO_EXPORT -ErrorAction SilentlyContinue
    }
}
if ([string]$runtime.powershell) { $env:ARCHFLOW_STUDIO_POWERSHELL = [string]$runtime.powershell }
if ([string]$runtime.intent_provider) { $env:ARCHFLOW_STUDIO_INTENT_PROVIDER = [string]$runtime.intent_provider }
if ([string]$runtime.intent_model) { $env:ARCHFLOW_STUDIO_INTENT_MODEL = [string]$runtime.intent_model }
if ([string]$runtime.intent_timeout_s) { $env:ARCHFLOW_STUDIO_INTENT_TIMEOUT_S = [string]$runtime.intent_timeout_s }
if ([string]$runtime.codex) { $env:ARCHFLOW_STUDIO_CODEX = [string]$runtime.codex }
# A deliberate local save overrides only the three existing intent defaults. Removing
# a saved field restores the runtime/environment choice assembled above. The file cannot
# select a project, executable, CAD backend or credential, and remote mode never reads it.
if ((-not ([string]$env:ARCHFLOW_STUDIO_MODE).Trim() -or $env:ARCHFLOW_STUDIO_MODE.Trim() -eq 'local') -and $env:APPDATA) {
    try {
        $userSettingsFile = Join-Path $env:APPDATA 'MonkeyArch\settings.json'
        if (Test-Path -LiteralPath $userSettingsFile -PathType Leaf) {
            $userSettingsJson = Get-Content -LiteralPath $userSettingsFile -Raw -Encoding utf8
            if (-not $userSettingsJson.TrimStart().StartsWith('{')) { throw 'user settings must be an object' }
            $savedUserSettings = $userSettingsJson | ConvertFrom-Json
            $unknownSettings = @($savedUserSettings.PSObject.Properties.Name | Where-Object { $_ -cnotin @('language', 'theme', 'fontScale', 'intentProvider', 'intentModel', 'intentTimeoutS') })
            if ($unknownSettings.Count) { throw 'unsupported user setting' }
            if ($null -ne $savedUserSettings.language -and ($savedUserSettings.language -isnot [string] -or $savedUserSettings.language -cnotin @('en', 'zh-CN'))) { throw 'invalid language' }
            if ($null -ne $savedUserSettings.theme -and ($savedUserSettings.theme -isnot [string] -or $savedUserSettings.theme -cnotin @('dark', 'light', 'system'))) { throw 'invalid theme' }
            if ($null -ne $savedUserSettings.fontScale -and ($savedUserSettings.fontScale.GetType() -notin @([int], [long], [double], [decimal]) -or $savedUserSettings.fontScale -notin @(0.9, 1, 1.1))) { throw 'invalid fontScale' }
            if ($null -ne $savedUserSettings.intentProvider -and ($savedUserSettings.intentProvider -isnot [string] -or $savedUserSettings.intentProvider -cnotin @('deterministic', 'codex', 'anthropic'))) { throw 'invalid intentProvider' }
            if ($null -ne $savedUserSettings.intentModel -and ($savedUserSettings.intentModel -isnot [string] -or -not $savedUserSettings.intentModel.Trim() -or $savedUserSettings.intentModel -match '[\x00-\x1f\x7f]')) { throw 'invalid intentModel' }
            if ($null -ne $savedUserSettings.intentTimeoutS -and ($savedUserSettings.intentTimeoutS.GetType() -notin @([int], [long], [double], [decimal]) -or $savedUserSettings.intentTimeoutS -le 0 -or [double]::IsNaN($savedUserSettings.intentTimeoutS) -or [double]::IsInfinity($savedUserSettings.intentTimeoutS))) { throw 'invalid intentTimeoutS' }
            if ($null -ne $savedUserSettings.intentProvider) { $env:ARCHFLOW_STUDIO_INTENT_PROVIDER = $savedUserSettings.intentProvider }
            if ($null -ne $savedUserSettings.intentModel) { $env:ARCHFLOW_STUDIO_INTENT_MODEL = $savedUserSettings.intentModel.Trim() }
            if ($null -ne $savedUserSettings.intentTimeoutS) { $env:ARCHFLOW_STUDIO_INTENT_TIMEOUT_S = ([double]$savedUserSettings.intentTimeoutS).ToString([Globalization.CultureInfo]::InvariantCulture) }
        }
    } catch {
        Write-Warning 'Saved user settings could not be read; continuing with runtime and environment defaults.'
    }
}
# Redirected python output is block-buffered; an unbuffered child is the difference between a
# log that says why it died and an empty file.
$env:PYTHONUNBUFFERED = '1'

$apiOut = Join-Path $logRoot "api-$stamp.out.log"
$apiErr = Join-Path $logRoot "api-$stamp.err.log"
$webOut = Join-Path $logRoot "web-$stamp.out.log"
$webErr = Join-Path $logRoot "web-$stamp.err.log"
Set-SplashStep 4 'starting the API'
$instanceId = [Guid]::NewGuid().ToString()
$apiArgs = $pythonArgs + @('-m', 'archflow_studio_api.main', '--host', '127.0.0.1', '--port', "$apiPort",
    '--managed-stdin', '--managed-instance-id', $instanceId)
$api = Start-OwnedProcess 'the Studio API' $pythonExe $apiArgs $apiRoot $apiOut $apiErr
Set-SplashStep 5 'waiting for the API identity'
if (-not (Wait-ManagedHealth "http://127.0.0.1:$apiPort/api/health" 60 $api.Process $instanceId 'archflow-studio-api' $sourceRevision)) {
    throw "The API did not answer with its service identity. Its log said:$([Environment]::NewLine)$(Get-LogTail $apiErr 25)$([Environment]::NewLine)(full log: $apiErr)"
}

Set-SplashStep 6 'starting the web client'
$env:ARCHFLOW_STUDIO_API_URL = "http://127.0.0.1:$apiPort"
$node = (Get-Command 'node.exe' -ErrorAction Stop).Source
$webArgs = @('--input-type=module', '-e', (Get-ManagedViteScript), '--', "$webPort", $instanceId)
$web = Start-OwnedProcess 'the Studio web client' $node $webArgs $webRoot $webOut $webErr
Set-SplashStep 7 "waiting for the web client on :$webPort"
if (-not (Wait-OwnedWeb $web $instanceId $webPort)) {
    throw "The web client did not start on :$webPort. Its log said:$([Environment]::NewLine)$(Get-LogTail $webErr 25)$([Environment]::NewLine)$(Get-LogTail $webOut 10)$([Environment]::NewLine)(full logs: $webErr, $webOut)"
}
$script:WebUrl = "http://127.0.0.1:$webPort"
$script:OpenBrowser = [bool]$runtime.open_browser
Invoke-AppLoop

} catch {
    $failure = $_.Exception.Message
    Stop-Children
    if ($script:Tray) { try { $script:Tray.Visible = $false } catch { } }
    Show-Fault $failure
    exit 1
} finally {
    Stop-Children
    foreach ($mutex in $script:LaunchLocks) { $mutex.ReleaseMutex(); $mutex.Dispose() }
}
