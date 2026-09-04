param(
    [string]$RuntimeConfig = (Join-Path $PSScriptRoot 'runtime.json'),
    [switch]$NoBrowser,
    [switch]$HideConsole
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
$assetRoot = Join-Path $studioRoot 'assets'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

$script:LogRoot = $logRoot
$script:WebUrl = $null
$script:Children = @()
$script:Splash = $null
$script:FaultOpen = $false
$script:Quitting = $false

# --- the application name and the same semantic palette the web shell uses
$BrandName = 'MonkeyArch'
$BrandLineOne = 'ArchFlow modeling workspace'
$StepCount = 8

$Ink = [System.Drawing.Color]::FromArgb(25, 27, 25)
$Stone = [System.Drawing.Color]::FromArgb(240, 241, 236)
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

    $title.Top = 96
    $tagline.Top = 132
    $progress.Top = 208

    $script:Splash = @{
        Form = $form; Title = $title; Tagline = $tagline; Progress = $progress
        ProgressTrack = $progressTrack; ProgressFill = $progressFill
        Fault = $fault; Close = $close; Rim = $Rim
    }

    Set-SplashLayout
    $form.Show()
    $form.Activate()
    return $script:Splash
}

function Set-SplashLayout {
    $splash = $script:Splash
    foreach ($label in @($splash.Title, $splash.Tagline, $splash.Progress)) {
        $label.Left = 64
    }
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
    Write-Host "$BrandName did not start." -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    if (-not $script:Splash -or $script:Splash.Form.IsDisposed) { New-Splash | Out-Null }
    $splash = $script:Splash
    $splash.Form.BackColor = $FaultInk
    $splash.Title.Font = New-Object System.Drawing.Font('Segoe UI', 17, [System.Drawing.FontStyle]::Bold)
    $splash.Title.Text = "$BrandName did not start"
    $splash.Title.Top = 30
    $splash.Tagline.Text = 'The launcher refused. Nothing was left running.'
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

function Stop-Children {
    foreach ($child in $script:Children) {
        $process = $child.Process
        if ($process -and -not $process.HasExited) {
            # /T because the web child is cmd.exe with node under it: killing cmd alone
            # would leave a Vite server holding the port.
            try { & taskkill /PID $process.Id /T /F 2>&1 | Out-Null } catch { }
        }
    }
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
    $script:Quitting = $true
    if ($script:Tray) { $script:Tray.Visible = $false }
    Stop-Children
    [System.Windows.Forms.Application]::ExitThread()
}

# Everything below runs inside one try: a refusal reaches the reader as the sentence that
# explains it, in the splash window, not as a PowerShell stack trace over a console that is
# about to close.
try {

New-Splash | Out-Null

# --- runtime.json is the only input, and it is validated before anything is started
Set-SplashStep 1 'validating runtime.json'
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

Write-Host "$BrandName (ArchFlow Studio)" -ForegroundColor Cyan
Write-Host "  config  : $RuntimeConfig"
Write-Host "  project : $projectDir"
Write-Host "  api     : http://127.0.0.1:$apiPort   web: http://127.0.0.1:$webPort"

# --- preflight: python + fastapi, node_modules, free ports
Set-SplashStep 2 'checking python and fastapi'
$check = Invoke-Quiet $pythonExe ($pythonArgs + @('-c', 'import fastapi, uvicorn'))
if ($check.ExitCode -ne 0) {
    $requirements = Join-Path $repoRoot 'apps\archflow-studio\api\requirements.txt'
    throw "'$($runtime.python)' cannot import fastapi/uvicorn. Install them with:$([Environment]::NewLine)  $($runtime.python) -m pip install -r $requirements$([Environment]::NewLine)$($check.Output)"
}
Set-SplashStep 3 'checking web dependencies'
if (-not (Test-Path -LiteralPath (Join-Path $webRoot 'node_modules\.bin\vite.cmd'))) {
    Set-SplashStep 3 'installing web dependencies · this takes a few minutes'
    Write-Host "  web deps missing; running npm install (this takes a few minutes) ..." -ForegroundColor Yellow
    $installLog = Join-Path $logRoot "npm-install-$stamp.log"
    $installErr = Join-Path $logRoot "npm-install-$stamp.err.log"
    # Started rather than called, so the launch surface stays responsive through a long install.
    $install = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', 'npm.cmd install') -WorkingDirectory $webRoot -PassThru -NoNewWindow -RedirectStandardOutput $installLog -RedirectStandardError $installErr
    while (-not $install.HasExited) { Invoke-Pump 100 }
    $install.WaitForExit()
    if ($install.ExitCode -ne 0) {
        throw "npm install failed in $webRoot$([Environment]::NewLine)$(Get-LogTail $installErr 25)$([Environment]::NewLine)(full logs: $installLog, $installErr)"
    }
}
if (-not (Test-PortFree $apiPort)) { throw "port $apiPort is already in use (a $BrandName API is running, or another program holds it)." }
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

$apiOut = Join-Path $logRoot "api-$stamp.out.log"
$apiErr = Join-Path $logRoot "api-$stamp.err.log"
$webOut = Join-Path $logRoot "web-$stamp.out.log"
$webErr = Join-Path $logRoot "web-$stamp.err.log"
try {
    Set-SplashStep 4 'starting the API'
    $apiArgs = $pythonArgs + @('-m', 'archflow_studio_api.main', '--host', '127.0.0.1', '--port', "$apiPort")
    $api = Start-Process -FilePath $pythonExe -ArgumentList $apiArgs -WorkingDirectory $apiRoot -PassThru -NoNewWindow -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr
    $script:Children += [pscustomobject]@{ Name = 'the API'; Process = $api; ErrorLog = $apiErr }
    Set-SplashStep 5 'waiting for the API to answer /api/health'
    if (-not (Wait-Http "http://127.0.0.1:$apiPort/api/health" 60 $api)) {
        throw "the API did not answer /api/health. Its log said:$([Environment]::NewLine)$(Get-LogTail $apiErr 25)$([Environment]::NewLine)(full log: $apiErr)"
    }
    $health = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$apiPort/api/health").Content
    Set-SplashStep 5 'the API answered /api/health'
    Write-Host "  api up  : $health"

    Set-SplashStep 6 'starting the web client'
    $web = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "npm.cmd run dev -- --port $webPort --strictPort") -WorkingDirectory $webRoot -PassThru -NoNewWindow -RedirectStandardOutput $webOut -RedirectStandardError $webErr
    $script:Children += [pscustomobject]@{ Name = 'the web client'; Process = $web; ErrorLog = $webErr }
    Set-SplashStep 7 "waiting for the web client on :$webPort"
    if (-not (Wait-Http "http://127.0.0.1:$webPort/" 90 $web)) {
        throw "the web client did not answer on :$webPort. Its log said:$([Environment]::NewLine)$(Get-LogTail $webErr 25)$([Environment]::NewLine)$(Get-LogTail $webOut 10)$([Environment]::NewLine)(full logs: $webErr, $webOut)"
    }
    Set-SplashStep 7 'the web client answered'
    Write-Host "  web up  : http://127.0.0.1:$webPort"

    $script:WebUrl = "http://127.0.0.1:$webPort"
    Set-SplashStep 8 'opening the browser'
    if ($runtime.open_browser -and -not $NoBrowser) { Start-Process $script:WebUrl }
    # A beat with the last step on screen: the browser takes a moment to paint, and a surface
    # that vanished before it did would look like the launch had failed.
    Invoke-Pump 1400
    Close-Splash

    $tray = Start-Tray $script:WebUrl
    Write-Host ""
    Write-Host "$BrandName is running. Quit it from the tray icon." -ForegroundColor Green
    Write-Host "  logs: $logRoot"

    # The watchdog: the same check the console loop used to make, answering into a balloon
    # tip and a red window instead of into a console nobody is looking at.
    $watchdog = New-Object System.Windows.Forms.Timer
    $watchdog.Interval = 2000
    $watchdog.Add_Tick({
        if ($script:Quitting) { return }
        foreach ($child in $script:Children) {
            if ($child.Process.HasExited) {
                $script:Watchdog.Stop()
                $script:Quitting = $true
                $message = "$($child.Name) exited with code $($child.Process.ExitCode). Its log said:$([Environment]::NewLine)$(Get-LogTail $child.ErrorLog 25)$([Environment]::NewLine)(logs: $script:LogRoot)"
                if ($script:Tray) {
                    $script:Tray.ShowBalloonTip(8000, "$BrandName stopped", "$($child.Name) exited. The window behind this says why.", [System.Windows.Forms.ToolTipIcon]::Error)
                }
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
    [System.Windows.Forms.Application]::Run()
    $watchdog.Stop()
    $tray.Visible = $false
    $tray.Dispose()
} finally {
    Stop-Children
    Write-Host "$BrandName stopped."
}

} catch {
    # The inner finally above has already stopped whatever had started.
    if ($script:Tray) { try { $script:Tray.Visible = $false } catch { } }
    Show-Fault $_.Exception.Message
    exit 1
}
