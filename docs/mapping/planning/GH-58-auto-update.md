# GH-58 automatic desktop updates

Issue: https://github.com/cogco1/MonkeyHub/issues/58
Base: `e183a69a`.

Owner decision, 2026-09-25: desktop updates become fully automatic now ("全自动，下次启动即新版"). This reorders #58's earlier sequencing, which put signing before automatic updates. Automatic updates ship at the same trust level as today's manual flow and say so: an unsigned prerelease channel. Publisher signature verification stays in #58 and plugs into the same check later.

- Release path: a `release-candidate` push also publishes `MonkeyHub-<new>-from-<old>.patch.zip` for each of the latest three published desktop releases and `MonkeyHub-<new>-update-index.json`, with the full candidate ZIP as the fallback entry. The build job reads the public releases with its read-only token; only the existing publish job writes.
- Hub: the packaged desktop checks about 30 s after start and then every 6 h while 自动更新 is on (default on, saved with the user settings). It takes the newest greater prerelease, downloads the patch for its exact base commit under a size cap, checks its SHA-256 and binds it to the release's ReleaseManifest@1, then prepares it through the existing staging path. No matching patch is reported as "needs full update". Nothing runs while an update is preparing or applying.
- Activation: a ready update never restarts the application mid-work. At a normal quit the desktop entry is switched through `install.ps1 -ActivateInstalled`; the next start finishes the transaction itself (health, full verification, entry) or restores the previous entry. "重启并更新" remains for an immediate switch.
