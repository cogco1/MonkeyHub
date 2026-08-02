# ArchFlow V4 cloud workspace

This repository includes a GitHub Codespaces environment for browser and
mobile access. The cloud workspace runs the platform-neutral Python core only.
It does not connect to Rhino, Revit, Minecraft, or a local desktop session.

## Open from a phone

1. Open the private GitHub repository in a mobile browser.
2. Choose **Code → Codespaces → Create codespace**.
3. Keep the browser tab open while the container is created.
4. Open the integrated terminal and run:

   ```text
   python tools/devctl.py status
   python tools/archcheck.py
   python -m unittest discover -s tests
   ```

The post-create step installs the package and runs the architecture firewall.
GitHub Actions also exposes a manually triggered **ArchFlow Verify** workflow.

## Authority boundary

- Codespaces and Actions are execution environments, not acceptance authority.
- Project records remain content-addressed and reloadable.
- Model-provider identity receipts remain mandatory; secrets must be supplied
  through GitHub Codespaces or Actions secrets and are never committed.
- Cloud test output cannot replace ArchFlow hard gates or canonical promotion.

## Current work status

P049 is completed. P050 contains a record-driven proposal producer but remains
active until its mandatory verification receipt passes. M024 and P026 remain
dependency-blocked; the cloud environment must not report them as complete.
