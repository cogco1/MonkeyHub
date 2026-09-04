# Put "打开 MonkeyArch.lnk" on the Desktop, pointing at OPEN_MONKEYARCH.bat, and take the
# superseded "打开 ArchFlow Studio.lnk" off it: the .bat that one pointed at is gone, so a
# shortcut left behind under the old name is a shortcut that no longer opens anything.
#
# This file is saved as UTF-8 *with* a BOM on purpose. Windows PowerShell 5.1 reads a
# BOM-less script in the system ANSI code page (936 on this machine), which would turn the
# shortcut's name into mojibake on the Desktop. Keep the BOM if you edit this file.

$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$target = Join-Path $studioRoot 'OPEN_MONKEYARCH.bat'
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "missing launcher: $target" }

# The icon lives beside the script, in the repository. The MonkeyArch icon once it is
# drawn, the ArchFlow one until then -- the rename lands in its own change, and a shortcut
# script that assumed one name would refuse to run on the other side of it.
$icon = $null
foreach ($name in @('monkeyarch.ico', 'archflow.ico')) {
    $candidate = Join-Path $studioRoot "assets\$name"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $icon = $candidate; break }
}
if (-not $icon) { throw "missing icon: no monkeyarch.ico and no archflow.ico in $(Join-Path $studioRoot 'assets')" }
$iconLocation = "$icon,0"

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop '打开 MonkeyArch.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $studioRoot
$shortcut.Description = 'Open MonkeyArch: the ArchFlow Studio API and web client, on the project in runtime.json'
$shortcut.IconLocation = $iconLocation
# 7 is minimised. The launcher hides its own PowerShell window, but the .bat that starts it
# is still a cmd.exe with a console: opened minimised, that console is never painted, and
# the first thing on screen is the splash.
$shortcut.WindowStyle = 7
$shortcut.Save()

# Read it back: a shortcut that was written is not yet a shortcut that resolves.
$written = $shell.CreateShortcut($link)
Write-Host "shortcut written: $link"
Write-Host "  target : $($written.TargetPath)"
Write-Host "  workdir: $($written.WorkingDirectory)"
Write-Host "  icon   : $($written.IconLocation)"
Write-Host "  window : $($written.WindowStyle)"
if ($written.TargetPath -ne $target) { throw "the shortcut points at $($written.TargetPath), not $target" }
if ($written.WorkingDirectory -ne $studioRoot) { throw "the shortcut works in $($written.WorkingDirectory), not $studioRoot" }
if ($written.IconLocation -ne $iconLocation) { throw "the shortcut draws $($written.IconLocation), not $iconLocation" }
if ($written.WindowStyle -ne 7) { throw "the shortcut opens with window style $($written.WindowStyle), not 7 (minimised)" }

# The old name, and only when it is still the old launcher it points at: a shortcut somebody
# retargeted by hand is theirs, not this script's to remove.
$retired = Join-Path $desktop '打开 ArchFlow Studio.lnk'
if (Test-Path -LiteralPath $retired -PathType Leaf) {
    $old = $shell.CreateShortcut($retired)
    if ([System.IO.Path]::GetFileName($old.TargetPath) -eq 'OPEN_ARCHFLOW_STUDIO.bat') {
        Remove-Item -LiteralPath $retired -Force
        Write-Host "shortcut removed: $retired (it pointed at the retired OPEN_ARCHFLOW_STUDIO.bat)"
    } else {
        Write-Host "shortcut left alone: $retired points at $($old.TargetPath), which this script did not write"
    }
}
