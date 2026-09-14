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

Signing, update verification and a release-revocation procedure remain open in
[issue #58](https://github.com/cogco1/MonkeyHub/issues/58). Candidates have no
automatic update or security patch channel; a fix reaches an installation only
through a newly built and installed candidate.

## Credentials and private data

Do not commit or distribute provider credentials, API tokens, signing material or
private project data. Keep them out of runtime configuration intended for sharing,
build metadata, SBOMs, diagnostics and issue attachments. Packaging selects committed
source paths; it does not authorize including private data in those paths.
