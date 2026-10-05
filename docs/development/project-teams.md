# Project teams: M1

Use File → Project team in MonkeyHub. The owner selects an existing project,
provides their display name and creates a role-bound invitation. Share the
invitation privately; it expires after 30 minutes and admits one device.
A retry by that same device and credential is idempotent if the reply was lost.

The member opens their own Hub, pastes the invitation, supplies a name, and
chooses an empty local folder. If needed the project id is appended to that
folder, because Runtime project roots retain their existing naming contract.
The Hub starts that member's own Runtime. Progress and reconnect/resume appear
in the same panel; closing the panel does not cancel the download. Reopening
Hub restores the saved connection. Completed chunks remain in the nonproject
cache after interruption and are verified before P036 installs the replica.
An interrupted installation finishes its pinned initial snapshot first, then
background synchronization fetches the latest Stage without overwriting user files.

Viewer is read-only, designer may produce candidates, moderator may accept
Stages through the existing exact-base flow. The owner changes or revokes roles
in the same panel. Revocation prevents remote access immediately; a disconnected
replica cannot learn a new role until it reconnects. Retained local files are
not remotely deleted. Offline candidates are preserved and their retained
closure and named actor position catch up automatically after reconnecting.
Formal HEAD publication and merge/working locks are outside M1.

## Source setup

Build the pinned helper with the official Rust toolchain:

    cargo build --locked --manifest-path packages/monkeymesh/native/Cargo.toml

The normal Hub source entry finds `native/target/debug/monkeymesh-tcp` (or .exe).
Distribution builds compile a release helper from the same committed source.
There is no second launcher, account, scheduler, VPN or generic owner API proxy.
HTTP and SSE keep their existing wire format over transparent authenticated
streams; the limited member ingress enforces peer + credential + project role.

Windows device secrets use account Credential Manager. Unix source/development
uses a mode-0600 file in the explicit Hub nonproject configuration root.
Stable per-project transport keys derive from that device secret, so simultaneous
projects do not compete for one relay endpoint identity. The member actor identity
remains stable. Membership config and Runtime actor files retain credential hashes only.
Invitations, tokens, project bytes and device keys must not go into bug reports.

## Verification boundary

`apps/monkeyhub/api/tests/test_team_m1.py` uses two isolated Hub instances,
real owned Runtime processes and real Iroh in loopback-only mode, using
synthetic projects and disposable credentials. It verifies software behavior;
it is not the two-physical-machine or cross-network product acceptance.
Board realtime is M3. Merge and workspace locking are M2.

Verified member roles are recorded by Runtime in a bound observation file under
the explicit Hub `team-state` root, outside projects and deletable caches. Offline
restart preserves learned viewer/revoked restrictions; missing or corrupt
observations are read-only. A stale invitation never restores write access.
