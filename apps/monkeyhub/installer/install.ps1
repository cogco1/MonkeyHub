param(
    [string]$InstallDirectory,
    [switch]$Interactive,
    [switch]$CreateDesktopShortcut,
    [string]$DesktopDirectory,
    [switch]$OpenHub,
    [switch]$RequireSignedRelease,
    [switch]$VerifyReleaseManifest,
    [string]$ReleaseManifest,
    [string]$ReleaseSignature,
    [string]$ReleaseArchive,
    [string]$ExpectedPublisherThumbprint,
    [switch]$RequireTrustedPublisherChain
)

# Copies one fixed candidate. Its selected host owns process lifecycle.
# ASCII source keeps this script readable by Windows PowerShell 5.1 without a BOM.
$ErrorActionPreference = 'Stop'

# -RequireSignedRelease verifies a chain, never one check: a pinned publisher
# signature over the ReleaseManifest@1 bytes, that manifest bound to the exact
# candidate archive it names, and that archive bound to every file of the
# extracted package about to be copied. A valid signature over an unrelated
# manifest, or over this manifest beside a changed package, is refused, and the
# whole chain is checked before the first installation, shortcut or launch.
# The manifest format and its closed artifact table belong to
# tools/package_monkeyapps.py; this script reads them and adds no second format.

function Resolve-PublisherFingerprint([string]$Value) {
    # certutil and the Windows certificate dialog print separators, so accept
    # those and nothing else: a truncated or mistyped pin must not pass as a
    # shorter fingerprint. Length names the algorithm; there is no third one.
    $candidate = ($Value -replace '[\s:-]', '')
    $algorithm = $null
    if ($candidate -match '^[0-9A-Fa-f]+$') {
        if ($candidate.Length -eq 64) { $algorithm = 'sha256' }
        elseif ($candidate.Length -eq 40) { $algorithm = 'sha1' }
    }
    if (-not $algorithm) {
        throw 'Expected publisher thumbprint must be 64 hexadecimal characters (SHA-256) or 40 (SHA-1).'
    }
    [ordered]@{ algorithm = $algorithm; value = $candidate.ToUpperInvariant() }
}

function Import-PkcsAssembly {
    if ('System.Security.Cryptography.Pkcs.SignedCms' -as [type]) { return }
    try {
        Add-Type -AssemblyName System.Security.Cryptography.Pkcs -ErrorAction Stop
    } catch {
        # Windows PowerShell 5.1 exposes SignedCms through System.Security.
        Add-Type -AssemblyName System.Security -ErrorAction Stop
    }
}

function Import-ZipAssembly {
    # ZipArchive is the type this script constructs, so it is the one to test
    # for. It lives in System.IO.Compression, which Windows PowerShell 5.1 does
    # not resolve from the FileSystem assembly alone; both hosts need both.
    if ('System.IO.Compression.ZipArchive' -as [type]) { return }
    Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
}

function Get-StreamSha256($Stream, $Hasher) {
    [BitConverter]::ToString($Hasher.ComputeHash($Stream)).Replace('-', '')
}

function Get-FileSha256([string]$Path) {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::OpenRead($Path)
        try { return (Get-StreamSha256 $stream $hasher).ToLowerInvariant() } finally { $stream.Dispose() }
    } finally {
        $hasher.Dispose()
    }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Get-CertificateFingerprint($Certificate, [string]$Algorithm) {
    # Computed from the certificate bytes rather than read from .Thumbprint,
    # which is SHA-1 only, so the same pin works on PowerShell 7 and 5.1.
    $hasher = if ($Algorithm -eq 'sha1') {
        [System.Security.Cryptography.SHA1]::Create()
    } else {
        [System.Security.Cryptography.SHA256]::Create()
    }
    try { return [BitConverter]::ToString($hasher.ComputeHash($Certificate.RawData)).Replace('-', '') }
    finally { $hasher.Dispose() }
}

function Read-ReleaseInput([string]$Path, [string]$Label, [int]$MaximumBytes) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label file was not found: $Path"
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $length = (Get-Item -LiteralPath $resolved).Length
    if ($length -gt $MaximumBytes) {
        throw "$Label file is $length bytes, too large to be release evidence."
    }
    return , [IO.File]::ReadAllBytes($resolved)
}

# Message digests this script accepts inside the signed message. A signature the
# pinned publisher made over a SHA-1 digest would let a collision on the manifest
# bytes carry the whole chain, so the algorithm is pinned as well as the key.
$AcceptedDigestOids = @{
    '2.16.840.1.101.3.4.2.1' = 'SHA-256'
    '2.16.840.1.101.3.4.2.2' = 'SHA-384'
    '2.16.840.1.101.3.4.2.3' = 'SHA-512'
}

function Test-ReleaseManifestSignature([byte[]]$ManifestBytes, [byte[]]$SignatureBytes,
                                       $ExpectedFingerprint, [switch]$RequireTrustedChain) {
    Import-PkcsAssembly
    $contentInfo = [System.Security.Cryptography.Pkcs.ContentInfo]::new($ManifestBytes)
    $cms = [System.Security.Cryptography.Pkcs.SignedCms]::new($contentInfo, $true)
    try {
        $cms.Decode($SignatureBytes)
    } catch {
        throw "Release signature verification failed: $($_.Exception.Message)"
    }
    # A detached SignedCms verifies against the bytes supplied to its
    # constructor and ignores any content the encoded message carries, so a
    # signature made over a different manifest cannot authenticate this one by
    # smuggling its own copy along. Both supported hosts are tested for that.
    # The signer count is settled first so that every later step, and every
    # refusal message, is about the one signer this release is pinned to. A
    # second signer must not be able to report itself as a digest problem.
    if ($cms.SignerInfos.Count -ne 1) {
        throw "Expected exactly one embedded release signer; found $($cms.SignerInfos.Count)."
    }
    $digestOid = $cms.SignerInfos[0].DigestAlgorithm.Value
    if (-not $AcceptedDigestOids.ContainsKey([string]$digestOid)) {
        throw "Release signature verification failed: unsupported message digest algorithm $digestOid."
    }
    try {
        # true checks the pinned signature mathematics against the manifest bytes.
        # false additionally asks the platform to build a trusted certificate
        # chain for the signer. Neither mode is claimed here to have checked
        # revocation, and this script performs no revocation lookup of its own.
        $cms.CheckSignature(-not $RequireTrustedChain.IsPresent)
    } catch {
        throw "Release signature verification failed: $($_.Exception.Message)"
    }
    if ($null -eq $cms.SignerInfos[0].Certificate) {
        throw 'The release signer embedded no certificate to pin.'
    }
    $certificate = $cms.SignerInfos[0].Certificate
    $actual = Get-CertificateFingerprint $certificate $ExpectedFingerprint.algorithm
    if ($actual -ne $ExpectedFingerprint.value) {
        throw ("Release signer $($ExpectedFingerprint.algorithm) fingerprint " +
               "does not match the expected publisher: $actual")
    }
    $chain = if ($RequireTrustedChain) { 'required-trusted-chain' } else { 'pinned-signature-only' }
    [ordered]@{
        signerThumbprint = $actual
        thumbprintAlgorithm = $ExpectedFingerprint.algorithm
        signerSubject = $certificate.Subject
        chainValidation = $chain
        revocationCheck = 'not-demonstrated'
    }
}

function ConvertFrom-ReleaseManifest([byte[]]$ManifestBytes) {
    # Parse the same bytes the signature was checked over, never a second read.
    $text = [Text.Encoding]::UTF8.GetString($ManifestBytes).TrimStart([char]0xFEFF)
    try {
        $document = $text | ConvertFrom-Json
    } catch {
        throw "The release manifest is not readable JSON: $($_.Exception.Message)"
    }
    if ($null -eq $document -or $document.schema -ne 'ReleaseManifest@1') {
        throw 'The release manifest is not a ReleaseManifest@1 document.'
    }
    return $document
}

function Open-SignedArchive([string]$Path) {
    # One handle for the whole run, opened without sharing write or delete. The
    # digest that the signed manifest is checked against and every later read of
    # the members are then the same bytes: re-opening the path would leave a
    # window in which the verified archive is replaced before it is compared.
    Import-ZipAssembly
    return [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
}

function Compare-PackageWithArchive([string]$PackageRoot, $ArchiveStream, [string]$Prefix) {
    # Every extracted file must be the signed archive's copy of it, and the
    # package must carry nothing the archive does not. The manifest closes over
    # the distributed archive, so this is what extends that closure to the tree.
    # One trailing separator, the same way the copy step derives its prefix, so
    # a package extracted at a drive root still yields correct relative names.
    $root = $PackageRoot.TrimEnd('\') + '\'
    # Ordinal keys with an explicit duplicate refusal: a default hashtable would
    # merge two archive members differing only in case and under-count the set.
    $expected = [Collections.Generic.Dictionary[string, string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    $seen = 0
    $ArchiveStream.Position = 0
    # leaveOpen, because this same handle is read again for the tree that
    # actually becomes the installation.
    $archive = [IO.Compression.ZipArchive]::new(
        $ArchiveStream, [IO.Compression.ZipArchiveMode]::Read, $true)
    try {
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            foreach ($entry in $archive.Entries) {
                if ($entry.FullName.EndsWith('/')) { continue }
                if (-not $entry.FullName.StartsWith("$Prefix/", [StringComparison]::OrdinalIgnoreCase)) {
                    throw "The signed candidate archive carries $($entry.FullName) outside $Prefix/."
                }
                $relative = $entry.FullName.Substring($Prefix.Length + 1)
                if ($expected.ContainsKey($relative)) {
                    throw "The signed candidate archive names $relative more than once."
                }
                $stream = $entry.Open()
                try { $expected[$relative] = Get-StreamSha256 $stream $hasher } finally { $stream.Dispose() }
            }
            # Directories are walked too, and no descent happens through a
            # reparse point: a junction added to the tree would contribute no
            # file here while Copy-Item -Recurse still installs its target.
            foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
                $relative = $item.FullName.Substring($root.Length).Replace('\', '/')
                if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    throw "This package does not match the signed candidate archive: $relative is a reparse point."
                }
                if ($item.PSIsContainer) { continue }
                if (-not $expected.ContainsKey($relative)) {
                    throw "This package does not match the signed candidate archive: $relative is not in it."
                }
                $stream = [IO.File]::OpenRead($item.FullName)
                try { $digest = Get-StreamSha256 $stream $hasher } finally { $stream.Dispose() }
                if ($digest -ne $expected[$relative]) {
                    throw "This package does not match the signed candidate archive: $relative differs from it."
                }
                $seen++
            }
        } finally {
            $hasher.Dispose()
        }
    } finally {
        $archive.Dispose()
    }
    if ($seen -ne $expected.Count) {
        throw ("This package does not match the signed candidate archive: " +
               "$($expected.Count - $seen) signed file(s) are missing.")
    }
    return $seen
}

function Assert-SignedReleaseBinding($Document, [string]$PackageRoot, [string]$ArchivePath,
                                     [string]$SourceCommit) {
    # A signature only says who wrote the manifest. These bindings say that this
    # manifest is about this archive, this source commit and this extracted tree.
    $archive = (Resolve-Path -LiteralPath $ArchivePath).Path
    $name = [IO.Path]::GetFileName($archive)
    if ($Document.archive -ne $name) {
        throw "The candidate archive $name is not the archive listed in the signed manifest."
    }
    $listed = @($Document.artifacts | Where-Object { $_.path -eq $name })
    if ($listed.Count -ne 1) {
        throw "The signed manifest does not list $name once in its closed artifact table."
    }
    # Held open for the rest of the run, so the bytes measured here stay the
    # bytes every member comparison below reads.
    $stream = Open-SignedArchive $archive
    $size = $stream.Length
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = (Get-StreamSha256 $stream $hasher).ToLowerInvariant() } finally { $hasher.Dispose() }
    if ($size -ne $listed[0].size -or $digest -ne $listed[0].sha256) {
        throw "The candidate archive does not match the signed manifest: SHA-256 $digest, $size bytes."
    }
    if ($Document.release.sourceCommit -ne $SourceCommit) {
        throw ("The signed manifest describes source commit $($Document.release.sourceCommit), " +
               "but this package carries $SourceCommit.")
    }
    $actual = Get-FileSha256 (Join-Path $PackageRoot 'build-info.json')
    if ($Document.buildInfo.sha256 -ne $actual) {
        throw ("The signed manifest binds build-info.json to $($Document.buildInfo.sha256), " +
               "but this package carries $actual.")
    }
    $prefix = [string]$Document.artifactPrefix
    if (-not $prefix) { throw 'The signed manifest names no artifact prefix for its archive members.' }
    [ordered]@{
        archiveName = $name
        archivePath = $archive
        archiveStream = $stream
        artifactPrefix = $prefix
        archiveSha256 = $digest
        boundPackageFiles = (Compare-PackageWithArchive $PackageRoot $stream $prefix)
    }
}

function New-ReleaseSignatureEvidence([string]$ManifestPath, [string]$SignaturePath,
                                      [byte[]]$ManifestBytes, [byte[]]$SignatureBytes,
                                      $Signer, $Document, $Binding) {
    $archiveName = $null
    $archiveDigest = $null
    $boundFiles = 0
    $statement = 'A pinned publisher signature authenticates this ReleaseManifest@1. ' +
                 'It was not bound to any installed package, and no revocation check was performed.'
    if ($Binding) {
        $archiveName = $Binding.archiveName
        $archiveDigest = $Binding.archiveSha256
        $boundFiles = $Binding.boundPackageFiles
        $statement = 'A pinned publisher signature authenticates this ReleaseManifest@1, which is ' +
                     'bound to the named candidate archive and to every installed file. No ' +
                     'certificate revocation check was performed or is claimed.'
    }
    [ordered]@{
        schema = 'ReleaseSignatureEvidence@1'
        manifest = [IO.Path]::GetFileName($ManifestPath)
        signature = [IO.Path]::GetFileName($SignaturePath)
        archive = $archiveName
        manifestSha256 = Get-BytesSha256 $ManifestBytes
        signatureSha256 = Get-BytesSha256 $SignatureBytes
        archiveSha256 = $archiveDigest
        signerThumbprint = $Signer.signerThumbprint
        thumbprintAlgorithm = $Signer.thumbprintAlgorithm
        signerSubject = $Signer.signerSubject
        chainValidation = $Signer.chainValidation
        revocationCheck = $Signer.revocationCheck
        boundSourceCommit = $Document.release.sourceCommit
        boundPackageFiles = $boundFiles
        manifestTrustStatus = $Document.trust.status
        statement = $statement
    }
}

function Test-SignedRelease([string]$PackageRoot, [string]$SourceCommit) {
    $expected = Resolve-PublisherFingerprint $ExpectedPublisherThumbprint
    # 1MB, not more: Windows PowerShell 5.1 parses JSON with a smaller ceiling
    # than PowerShell 7, and a release manifest is a few kilobytes.
    $manifestBytes = Read-ReleaseInput $ReleaseManifest 'Release manifest' 1MB
    $signatureBytes = Read-ReleaseInput $ReleaseSignature 'Release signature' 1MB
    $signer = Test-ReleaseManifestSignature $manifestBytes $signatureBytes $expected `
        -RequireTrustedChain:$RequireTrustedPublisherChain.IsPresent
    $document = ConvertFrom-ReleaseManifest $manifestBytes
    $binding = $null
    if ($PackageRoot) {
        if (-not (Test-Path -LiteralPath $ReleaseArchive -PathType Leaf)) {
            throw "Release archive file was not found: $ReleaseArchive"
        }
        $binding = Assert-SignedReleaseBinding $document $PackageRoot $ReleaseArchive $SourceCommit
    }
    [ordered]@{
        evidence = (New-ReleaseSignatureEvidence $ReleaseManifest $ReleaseSignature `
                    $manifestBytes $signatureBytes $signer $document $binding)
        binding = $binding
    }
}

function Write-TrustNotice($Signed, [int]$InstalledFiles) {
    if ($Signed) {
        $evidence = $Signed.evidence
        $evidence | ConvertTo-Json -Depth 4
        Write-Host ("Trust: publisher signature verified, $($evidence.thumbprintAlgorithm) " +
                    "$($evidence.signerThumbprint), $($evidence.chainValidation), " +
                    "revocation $($evidence.revocationCheck).")
        Write-Host ("Bound to $($evidence.archive); $InstalledFiles installed files verified " +
                    "against it; the release manifest itself records $($evidence.manifestTrustStatus).")
    } else {
        Write-Host ('Trust: candidate-unsigned. No publisher signature was verified for this ' +
                    'installation; it is a development candidate, not a supported release.')
    }
}

function Test-InstalledHubEntry([string]$Target, [string]$Entry) {
    if ([IO.Path]::GetFileName($Target) -ne $Entry -or
        -not (Test-Path -LiteralPath $Target -PathType Leaf)) { return $false }
    $directory = [IO.Path]::GetDirectoryName($Target)
    $metadata = Join-Path $directory 'build-info.json'
    $versionFile = Join-Path $directory 'source-version.txt'
    if (-not (Test-Path -LiteralPath $metadata -PathType Leaf) -or
        -not (Test-Path -LiteralPath $versionFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $directory 'apps/monkeyhub/run.py') -PathType Leaf)) {
        return $false
    }
    try {
        $build = Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
        $source = (Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8).Trim()
        return $source -match '^[0-9a-f]{40}$' -and $build.sourceCommit -eq $source
    } catch { return $false }
}

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
        # WScript.Shell writes the shortcut; the launch surface itself is launch-hub.ps1 or the desktop window.
        $shell = New-Object -ComObject WScript.Shell
        # The native package has one app shortcut; its browser launcher remains in the bundle.
        $entries = if ($desktopBuild) {
            @(@{ Link = 'MonkeyHub.lnk'; Entry = 'MonkeyHub.exe'; WindowStyle = 1 })
        } else {
            @(@{ Link = 'MonkeyHub.lnk'; Entry = 'OPEN_MONKEYHUB.cmd'; WindowStyle = 7 })
        }
        foreach ($item in $entries) {
            $link = Join-Path $desktop $item.Link
            $shortcut = $shell.CreateShortcut($link)
            if (Test-Path -LiteralPath $link) {
                $ownedEntry = Test-InstalledHubEntry $shortcut.TargetPath $item.Entry
                $oldBrowser = $desktopBuild -and (Test-InstalledHubEntry $shortcut.TargetPath 'OPEN_MONKEYHUB.cmd')
                if (-not $ownedEntry -and -not $oldBrowser) {
                    Write-Warning "The existing shortcut points to another application and was left alone: $link"
                    continue
                }
            }
            $target = Join-Path $Directory $item.Entry
            $shortcut.TargetPath = $target
            $shortcut.Arguments = ''
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
            if ($desktopBuild) {
                $legacyLink = Join-Path $desktop 'MonkeyArch.lnk'
                if (Test-Path -LiteralPath $legacyLink -PathType Leaf) {
                    $legacyTarget = $shell.CreateShortcut($legacyLink).TargetPath
                    if (Test-InstalledHubEntry $legacyTarget 'MonkeyArch.exe') {
                        Remove-Item -LiteralPath $legacyLink
                    }
                }
            }
        }
    }
    if ($launch) {
        $windowStyle = if ($desktopBuild) { 'Normal' } else { 'Hidden' }
        Start-Process -FilePath $entry -WorkingDirectory $Directory -WindowStyle $windowStyle
    }
}

try {
    if ($VerifyReleaseManifest) {
        # Inspect a downloaded manifest before extracting anything. This mode
        # installs nothing, so it binds no package and says so in its evidence.
        if ($RequireSignedRelease) {
            throw 'Choose either -VerifyReleaseManifest or -RequireSignedRelease, not both.'
        }
        if (-not $ReleaseManifest -or -not $ReleaseSignature -or -not $ExpectedPublisherThumbprint) {
            throw '-VerifyReleaseManifest needs -ReleaseManifest, -ReleaseSignature and -ExpectedPublisherThumbprint.'
        }
        if ($ReleaseArchive) {
            throw '-VerifyReleaseManifest binds no package; use -RequireSignedRelease to bind -ReleaseArchive.'
        }
        (Test-SignedRelease $null $null).evidence | ConvertTo-Json -Depth 4
        exit 0
    }
    if ($RequireSignedRelease) {
        # Opting in must never degrade to an unsigned installation: a missing or
        # malformed input is a refusal here, not a reason to carry on.
        if (-not $ReleaseManifest -or -not $ReleaseSignature -or -not $ReleaseArchive -or
            -not $ExpectedPublisherThumbprint) {
            throw ('-RequireSignedRelease needs -ReleaseManifest, -ReleaseSignature, -ReleaseArchive ' +
                   'and -ExpectedPublisherThumbprint.')
        }
        Resolve-PublisherFingerprint $ExpectedPublisherThumbprint | Out-Null
    } elseif ($ReleaseManifest -or $ReleaseSignature -or $ReleaseArchive -or
              $ExpectedPublisherThumbprint -or $RequireTrustedPublisherChain) {
        throw 'Release signature inputs require -RequireSignedRelease or -VerifyReleaseManifest.'
    }
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
    $entryName = if ($desktopBuild) { 'MonkeyHub.exe' } else { 'OPEN_MONKEYHUB.cmd' }
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
        'apps\monkeyhub\web\dist\index.html', 'apps\archflow-studio\web\dist\index.html',
        'apps\monkeyfab\src\monkeyfab\__main__.py', 'apps\monkeyfab\pyproject.toml'
    )
    if ($desktopBuild) { $required += @('MonkeyHub.exe', '_runtime\desktop-Cargo.lock') }
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $relative) -PathType Leaf)) {
            throw "The extracted package is incomplete: $relative"
        }
    }
    # Nothing below this point may run unverified: the destination branches
    # create shortcuts, launch the entry and move a new current version into
    # place, and an already-installed build takes that path too.
    $signedRelease = $null
    if ($RequireSignedRelease) {
        $signedRelease = Test-SignedRelease $packageRoot $version
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
            # The package on disk being authentic says nothing about the copy
            # that is already here and is about to be shortcut and launched.
            $installedFiles = 0
            if ($signedRelease) {
                $installedFiles = Compare-PackageWithArchive $destination `
                    $signedRelease.binding.archiveStream $signedRelease.binding.artifactPrefix
            }
            Write-TrustNotice $signedRelease $installedFiles
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
    # Verify the copy that becomes the installation, not only the tree it was
    # read from: the extracted package stays writable while this runs.
    $installedFiles = 0
    if ($signedRelease) {
        $installedFiles = Compare-PackageWithArchive $stagedDestination `
            $signedRelease.binding.archiveStream $signedRelease.binding.artifactPrefix
    }
    if (Test-Path -LiteralPath $destination) {
        # Delete only the explicitly chosen, still-empty directory; false makes
        # a concurrent file creation a refusal rather than a recursive deletion.
        [IO.Directory]::Delete($destination, $false)
    }
    Move-Item -LiteralPath $stagedDestination -Destination $destination -ErrorAction Stop
    Write-TrustNotice $signedRelease $installedFiles
    Write-Host "Installed MonkeyHub source $version"
    if ($fabVersion) { Write-Host "Included MonkeyFab source $fabVersion" }
    Write-Host "Open: $(Join-Path $destination $entryName)"
    Write-Host 'No system Python, Node, PATH or project was changed.'
    Complete-Installation $destination
} catch {
    if ($stagedDestination -and (Test-Path -LiteralPath $stagedDestination)) {
        Write-Host "Incomplete installation files remain at: $stagedDestination"
    }
    # One unwrapped line, so a refusal reads the same in a redirected log as on
    # screen; the error record follows for the host's own formatting.
    Write-Host "Refused: $($_.Exception.Message)"
    Write-Error $_ -ErrorAction Continue
    exit 1
}
