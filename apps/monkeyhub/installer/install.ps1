param(
    [string]$InstallDirectory,
    [switch]$Interactive,
    [switch]$CreateDesktopShortcut,
    [string]$DesktopDirectory,
    [switch]$OpenHub
)

# Copies one fixed candidate. Its selected host owns process lifecycle.
# ASCII source keeps this script readable by Windows PowerShell 5.1 without a BOM.
$ErrorActionPreference = 'Stop'

function Complete-Installation([string]$Directory) {
    $entry = Join-Path $Directory $entryName
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
        $shell = New-Object -ComObject WScript.Shell
        # Both surfaces use this installation's frontend, Python and applications.
        $entries = @(@{ Link = 'MonkeyHub.lnk'; Entry = 'OPEN_MONKEYHUB.cmd'; WindowStyle = 7 })
        if ($desktopBuild) {
            $entries += @{ Link = 'MonkeyArch.lnk'; Entry = 'MonkeyArch.exe'; WindowStyle = 1 }
        }
        foreach ($item in $entries) {
            $link = Join-Path $desktop $item.Link
            $shortcut = $shell.CreateShortcut($link)
            if ((Test-Path -LiteralPath $link) -and [IO.Path]::GetFileName($shortcut.TargetPath) -ne $item.Entry) {
                Write-Warning "The existing shortcut points to another application and was left alone: $link"
                continue
            }
            $target = Join-Path $Directory $item.Entry
            $shortcut.TargetPath = $target
            $shortcut.WorkingDirectory = $Directory
            $shortcut.Description = 'Open MonkeyHub and its local applications'
            $shortcut.IconLocation = (Join-Path $Directory 'apps\archflow-studio\assets\monkeyarch.ico') + ',0'
            $shortcut.WindowStyle = $item.WindowStyle
            $shortcut.Save()
            $written = $shell.CreateShortcut($link)
            if ($written.TargetPath -ne $target -or $written.WorkingDirectory -ne $Directory) {
                throw "The desktop shortcut does not point to this installation: $link"
            }
            Write-Host "Desktop shortcut: $link"
        }
    }
    if ($launch) {
        $windowStyle = if ($desktopBuild) { 'Normal' } else { 'Hidden' }
        Start-Process -FilePath $entry -WorkingDirectory $Directory -WindowStyle $windowStyle
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
    $buildInfo = Get-Content -LiteralPath (Join-Path $packageRoot 'build-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($buildInfo.sourceCommit -ne $version) { throw 'The package build metadata does not match its source commit.' }
    $desktopBuild = $null -ne $buildInfo.desktop
    $entryName = if ($desktopBuild) { 'MonkeyArch.exe' } else { 'OPEN_MONKEYHUB.cmd' }
    if ($desktopBuild -and $buildInfo.desktop.sourceCommit -ne $version) {
        throw 'The desktop host metadata does not match the bundled Hub source.'
    }
    $fabVersion = [string]$buildInfo.monkeyFabCommit
    if ($fabVersion -and $fabVersion -notmatch '^[0-9a-f]{40}$') { throw 'The package has no valid MonkeyFab source commit.' }
    $versionName = $version.Substring(0, 12)
    if ($fabVersion) { $versionName += '-fab-' + $fabVersion.Substring(0, 12) }
    if ($desktopBuild) { $versionName += '-desktop' }
    $required = @(
        'source-version.txt', 'build-info.json', 'OPEN_MONKEYHUB.cmd', '_runtime\python\python.exe',
        'apps\monkeyhub\run.py', 'apps\monkeyhub\launch-hub.ps1',
        'apps\monkeyhub\web\dist\index.html', 'apps\archflow-studio\web\dist\index.html'
    )
    if ($fabVersion) {
        $required += @('apps\monkeyfab\src\monkeyfab\__main__.py', 'apps\monkeyfab\pyproject.toml')
    }
    if ($desktopBuild) { $required += @('MonkeyArch.exe', '_runtime\desktop-Cargo.lock') }
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $relative) -PathType Leaf)) {
            throw "The extracted package is incomplete: $relative"
        }
    }
    if (-not $InstallDirectory) {
        if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; supply -InstallDirectory.' }
        $InstallDirectory = Join-Path $env:LOCALAPPDATA ('MonkeyHub\versions\' + $versionName)
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
            if ($same) {
                $installedBuild = Get-Content -LiteralPath (Join-Path $destination 'build-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
                $same = $installedBuild.sourceCommit -eq $version -and [string]$installedBuild.monkeyFabCommit -eq $fabVersion
                $same = $same -and (($null -ne $installedBuild.desktop) -eq $desktopBuild)
            }
            if (-not $same) {
                throw "The destination already contains files. Choose a new directory: $destination"
            }
            Write-Host "This build is already installed: $destination"
            Write-Host "Open: $(Join-Path $destination $entryName)"
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
    if ($fabVersion) { Write-Host "Included MonkeyFab source $fabVersion" }
    Write-Host "Open: $(Join-Path $destination $entryName)"
    Write-Host 'No system Python, Node, PATH or project was changed.'
    Complete-Installation $destination
} catch {
    if ($stagedDestination -and (Test-Path -LiteralPath $stagedDestination)) {
        Write-Host "Incomplete installation files remain at: $stagedDestination"
    }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
