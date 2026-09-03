# Put "打开 ArchFlow Studio.lnk" on the Desktop, pointing at OPEN_ARCHFLOW_STUDIO.bat.
#
# This file is saved as UTF-8 *with* a BOM on purpose. Windows PowerShell 5.1 reads a
# BOM-less script in the system ANSI code page (936 on this machine), which would turn the
# shortcut's name into mojibake on the Desktop. Keep the BOM if you edit this file.

$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$target = Join-Path $studioRoot 'OPEN_ARCHFLOW_STUDIO.bat'
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "missing launcher: $target" }

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop '打开 ArchFlow Studio.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $studioRoot
$shortcut.Description = 'Start the ArchFlow Studio API and web client on the project in runtime.json'
$shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,109"
$shortcut.WindowStyle = 1
$shortcut.Save()

# Read it back: a shortcut that was written is not yet a shortcut that resolves.
$written = $shell.CreateShortcut($link)
Write-Host "shortcut written: $link"
Write-Host "  target : $($written.TargetPath)"
Write-Host "  workdir: $($written.WorkingDirectory)"
if ($written.TargetPath -ne $target) { throw "the shortcut points at $($written.TargetPath), not $target" }
if ($written.WorkingDirectory -ne $studioRoot) { throw "the shortcut works in $($written.WorkingDirectory), not $studioRoot" }
