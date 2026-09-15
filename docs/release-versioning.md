# MonkeyHub candidate release versioning

The `release-candidate` branch is a promotion pointer for internal Windows prereleases.

Moving that branch to an integrated `main` commit triggers the existing Windows desktop workflow. The workflow creates a deterministic release-only commit that changes only the desktop Cargo package version, builds and installs the exact snapshot, verifies the installed `MonkeyArch.exe --version`, then publishes a GitHub prerelease tagged `v0.1.N` where `N` is the GitHub Actions workflow run number.

Normal `main` pushes continue to build review artifacts but do not publish a GitHub Release.

Candidate releases remain unsigned until the signing slice of #58 lands. Each package still includes the existing ReleaseManifest@1, SHA-256 sidecar, and CycloneDX SBOM.
