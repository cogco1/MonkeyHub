# Security policy

MonkeyHub is a public research preview. It reads and writes project files and can
launch local tools and agent CLIs. Current Windows candidates are unsigned and are
not supported production releases.

## Reporting a vulnerability

GitHub private vulnerability reporting is not enabled for this repository, and no
security contact is published here. There is no published response-time commitment.

Please do not post exploit steps, proof-of-concept inputs, credentials or private
project data in public issues, pull requests or logs. To request a private reporting
route, open an issue naming only the affected component and asking for a private
channel. Wait for that channel before sharing sensitive details.

## Release verification

Candidate builds include a CycloneDX SBOM and a `ReleaseManifest@1` beside the ZIP.
The SBOM distinguishes installed components from frontend and Rust build inputs;
a build dependency is not proof that its code appears in the compiled output.

From a source checkout, verify the downloaded release directory with:

```powershell
python tools/package_monkeyapps.py --verify '<candidate>.zip.release-manifest.json'
```

The command checks listed file sizes and hashes, unlisted files with the same
release prefix, and the actual build metadata and SBOM copies inside the ZIP.
It also checks that the bundled and sidecar SBOM digests agree.

These checks establish consistency with the manifest, not publisher identity.
Current manifests explicitly record `candidate-unsigned` and `signed: false`.
An attacker who can replace both the package and manifest can recompute their
hashes. Obtain the manifest through a channel you already trust.

## Signed installation

The installer can refuse to install a release it cannot authenticate. Running
`install.ps1 -RequireSignedRelease` with a detached CMS signature over the
manifest, the candidate ZIP and an expected publisher fingerprint verifies, in
this order and before copying any file, creating any shortcut, launching the
application or making a version current:

1. the signature over the exact manifest bytes, made by exactly one signer whose
   certificate matches a SHA-256 (64 hex) or SHA-1 (40 hex) fingerprint, and made
   with a SHA-256/384/512 message digest — weaker digests, and a second signer,
   are refused. Prefer the SHA-256 form: the 40-hex thumbprint Windows shows is
   a SHA-1 digest of the certificate and is the weaker of the two pins;
2. that the manifest's closed artifact table lists this exact ZIP by name, size
   and SHA-256, and that its `release.sourceCommit` and `buildInfo.sha256` match
   this package — a valid signature over an unrelated manifest is refused;
3. that every extracted file equals the corresponding member of that ZIP, with
   no extra file, no missing file and no reparse point. The ZIP is opened once,
   without sharing write access, so the bytes that were hashed against the
   manifest stay the bytes every comparison below reads;
4. that the tree which actually becomes the installation satisfies (3) as well —
   the staged copy before it is made current, or an existing installation of the
   same build before it is shortcut or launched.

Opting in never degrades to an unsigned installation: a missing input, an
unreadable manifest or a malformed fingerprint is a refusal. `-VerifyReleaseManifest`
checks a downloaded manifest's signature alone and installs nothing.
`-RequireTrustedPublisherChain` additionally requires the platform to build a
trusted certificate chain. **The installer performs no revocation lookup of its
own and claims none in either mode**; the printed evidence always records
`revocationCheck: not-demonstrated`. Under `-RequireTrustedPublisherChain` the
chain is built by the platform under its own default policy, which on Windows
may consult CRL or OCSP endpoints — that is the platform's behaviour, is not
configured or relied on here, and is not evidence that revocation was checked.

**This repository holds no publisher key and the builder signs nothing.** The
verifier exists and is tested with ephemeral in-process keys; obtaining a real
signing certificate, signing releases as part of publishing, distributing the
expected fingerprint over a trusted channel, and a revocation procedure all
remain open in [issue #58](https://github.com/cogco1/MonkeyHub/issues/58).
There is also no rollback protection: a genuinely signed older release, served
with its own archive, is accepted in full — nothing compares versions or signing
times. An
installation performed without `-RequireSignedRelease` prints
`Trust: candidate-unsigned` and is not a supported production release.
Installed desktop candidates update themselves from an unsigned prerelease channel (HTTPS, SHA-256 and
ReleaseManifest@1 checks, no publisher signature, so exactly the trust of a manually downloaded
prerelease); it can be turned off in Settings > Software update, see apps/monkeyhub/installer/README.md.

## Credentials and private data

Do not commit or distribute provider credentials, API tokens, signing material or
private project data. Keep them out of runtime configuration intended for sharing,
build metadata, SBOMs, diagnostics and issue attachments. Packaging selects committed
source paths; it does not authorize including private data in those paths.
