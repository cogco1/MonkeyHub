param(
    [string]$InstallDirectory,
    [switch]$Interactive,
    [switch]$CreateDesktopShortcut,
    [string]$DesktopDirectory,
    [switch]$OpenHub
)

# Copies one fixed candidate. Process lifecycle belongs to launch-hub.ps1.
# ASCII source keeps this script readable by Windows PowerShell 5.1 without a BOM.
$ErrorActionPreference = 'Stop'

function Complete-Installation([string]$Directory) {
    $entry = Join-Path $Directory 'OPEN_MONKEYHUB.cmd'
    $makeShortcut = $CreateDesktopShortcut.IsPresent
    $launch = $OpenHub.IsPresent
    if ($Interactive) {
        $answer = Read-Host 'Create a MonkeyHub desktop shortcut? [Y/n]'
        $makeShortcut = $answer -eq '' -or $answer -match '^[Yy]'
        $answer = Read-Host 'Open MonkeyHub now? [Y/n]'
        $launch = $answer -eq '' -or $answer -match '^[Yy]'
    }
    if ($makeShortcut) {
        $desktop = if ($DesktopDirectory) { $DesktopDirectory } else { [Environment]::GetFolderPath('Desktop') }
        if (-not [IO.Path]::IsPathRooted($desktop) -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
            throw 'The desktop shortcut directory must be an existing absolute directory.'
        }
        # Same WScript.Shell shortcut mechanism as Studio's make-desktop-shortcut.ps1.
        $link = Join-Path $desktop 'MonkeyHub.lnk'
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($link)
        if ((Test-Path -LiteralPath $link) -and [IO.Path]::GetFileName($shortcut.TargetPath) -ne 'OPEN_MONKEYHUB.cmd') {
            Write-Warning "The existing shortcut points to another application and was left alone: $link"
        } else {
            $shortcut.TargetPath = $entry
            $shortcut.WorkingDirectory = $Directory
            $shortcut.Description = 'Open MonkeyHub and its local applications'
            $shortcut.IconLocation = (Join-Path $Directory 'apps\archflow-studio\assets\monkeyarch.ico') + ',0'
            $shortcut.WindowStyle = 7
            $shortcut.Save()
            $written = $shell.CreateShortcut($link)
            if ($written.TargetPath -ne $entry -or $written.WorkingDirectory -ne $Directory) {
                throw "The desktop shortcut does not point to this installation: $link"
            }
            Write-Host "Desktop shortcut: $link"
        }
    }
    if ($launch) {
        Start-Process -FilePath $entry -WorkingDirectory $Directory -WindowStyle Hidden
    }
}

try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'This candidate requires Windows x64.'
    }
    $packageRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
    $versionFile = Join-Path $packageRoot 'source-version.txt'
    $version = (Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8).Trim()
    if ($version -notmatch '^[0-9a-f]{40}$') { throw 'The package has no valid source commit.' }
    $required = @(
        'source-version.txt', 'OPEN_MONKEYHUB.cmd', '_runtime\python\python.exe',
        'apps\monkeyhub\run.py', 'apps\monkeyhub\launch-hub.ps1',
        'apps\monkeyhub\web\dist\index.html', 'apps\archflow-studio\web\dist\index.html'
    )
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $relative) -PathType Leaf)) {
            throw "The extracted package is incomplete: $relative"
        }
    }
    if (-not $InstallDirectory) {
        if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; supply -InstallDirectory.' }
        $InstallDirectory = Join-Path $env:LOCALAPPDATA ('MonkeyHub\versions\' + $version.Substring(0, 12))
    }
    if (-not [IO.Path]::IsPathRooted($InstallDirectory)) {
        throw 'InstallDirectory must be an absolute path.'
    }
    $destination = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
    $sourcePrefix = $packageRoot.TrimEnd('\') + '\'
    if ($destination.Equals($packageRoot.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
        $destination.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Choose an installation directory outside the extracted package.'
    }
    if (Test-Path -LiteralPath $destination) {
        if (-not (Test-Path -LiteralPath $destination -PathType Container)) {
            throw "The installation path is already a file: $destination"
        }
        if (@(Get-ChildItem -LiteralPath $destination -Force).Count -gt 0) {
            $installedVersion = Join-Path $destination 'source-version.txt'
            $same = (Test-Path -LiteralPath $installedVersion -PathType Leaf) -and
                ((Get-Content -LiteralPath $installedVersion -Raw -Encoding UTF8).Trim() -eq $version)
            foreach ($relative in $required) {
                $same = $same -and (Test-Path -LiteralPath (Join-Path $destination $relative) -PathType Leaf)
            }
            if (-not $same) {
                throw "The destination already contains files. Choose a new directory: $destination"
            }
            Write-Host "This source version is already installed: $destination"
            Write-Host "Open: $(Join-Path $destination 'OPEN_MONKEYHUB.cmd')"
            Complete-Installation $destination
            exit 0
        }
    }
    # Finish copying beside the destination before making this version visible.
    # A failed transfer must not leave a partly installed default version.
    $targetParent = [IO.Path]::GetDirectoryName($destination)
    $stagedDestination = Join-Path $targetParent ('.mh-' + [Guid]::NewGuid().ToString('N'))
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($stagedDestination)) -ne $targetParent) {
        throw 'The installation staging directory must share the destination parent.'
    }
    # Stay within legacy Win32 limits even when LongPathsEnabled is off.
    foreach ($entry in Get-ChildItem -LiteralPath $packageRoot -Recurse -Force) {
        $relative = $entry.FullName.Substring($sourcePrefix.Length)
        $limit = if ($entry.PSIsContainer) { 248 } else { 260 }
        foreach ($root in @($packageRoot, $destination, $stagedDestination)) {
            if ((Join-Path $root $relative).Length -ge $limit) {
                throw 'The extraction or installation path is too long. Use a shorter folder path; no Windows setting needs to change.'
            }
        }
    }
    New-Item -ItemType Directory -Path $stagedDestination -Force | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $packageRoot -Force) {
        Copy-Item -LiteralPath $entry.FullName -Destination $stagedDestination -Recurse -ErrorAction Stop
    }
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $stagedDestination $relative) -PathType Leaf)) {
            throw "Installation did not finish: $relative. Keep the extracted package and choose a new destination."
        }
    }
    if (Test-Path -LiteralPath $destination) {
        # Delete only the explicitly chosen, still-empty directory; false makes
        # a concurrent file creation a refusal rather than a recursive deletion.
        [IO.Directory]::Delete($destination, $false)
    }
    Move-Item -LiteralPath $stagedDestination -Destination $destination -ErrorAction Stop
    Write-Host "Installed MonkeyHub source $version"
    Write-Host "Open: $(Join-Path $destination 'OPEN_MONKEYHUB.cmd')"
    Write-Host 'No system Python, Node, PATH or project was changed.'
    Complete-Installation $destination
} catch {
    if ($stagedDestination -and (Test-Path -LiteralPath $stagedDestination)) {
        Write-Host "Incomplete installation files remain at: $stagedDestination"
    }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
