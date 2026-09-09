param([string]$InstallDirectory)

# Copies one fixed candidate. Process lifecycle belongs to launch-hub.ps1.
# ASCII source keeps this script readable by Windows PowerShell 5.1 without a BOM.
$ErrorActionPreference = 'Stop'

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
            exit 0
        }
    }
    # Finish copying beside the destination before making this version visible.
    # A failed transfer must not leave a partly installed default version.
    $stagedDestination = $destination + '.installing-' + [Guid]::NewGuid().ToString('N')
    $targetParent = [IO.Path]::GetDirectoryName($destination)
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($stagedDestination)) -ne $targetParent) {
        throw 'The installation staging directory must share the destination parent.'
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
    Write-Host 'You can create a desktop shortcut to that file after installation.'
    Write-Host 'No system Python, Node, PATH, desktop shortcut or project was changed.'
} catch {
    if ($stagedDestination -and (Test-Path -LiteralPath $stagedDestination)) {
        Write-Host "Incomplete installation files remain at: $stagedDestination"
    }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
