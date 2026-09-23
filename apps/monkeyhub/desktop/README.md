# MonkeyHub desktop host

This Windows Tauri 2 host opens the existing MonkeyHub/Studio UI. It starts one
Hub root through `apps/monkeyhub/run.py`; Hub continues to own Studio, Monitor,
agent sessions and accepted operations. The shell never reads or writes project
state. P036 remains the project persistence authority.

## Build and package

Use Rust stable with the MSVC Windows target, Visual Studio C++ build tools and
Microsoft Edge WebView2 Runtime. From this directory:

```powershell
cargo test --locked
cargo build --locked --release
```

`target/release/MonkeyHub.exe` is the native host. It is **not a standalone
application bundle**. `tools/package_monkeyapps.py` owns the distribution tree,
Python dependencies, existing Hub/Studio frontend builds and package validation.
The package places this EXE at its root alongside `source-version.txt`,
`apps/monkeyhub/run.py` and `_runtime/python/python.exe`. It supplies
`ARCHFLOW_SOURCE_REVISION` when building from an exact source archive. Checkout
builds resolve the actual Git HEAD. The EXE refuses a different runtime source
revision. `MonkeyHub.exe --version` prints its version and source revision as
JSON without opening a window.

From the repository root, inspect the saved packaging locations with
`python tools/package_monkeyapps.py --show-paths`, then build the optional
desktop candidate with `python tools/package_monkeyapps.py --source-ref HEAD --desktop`.
This uses the existing external staging/cache/output roots and adds the EXE to
that same bundle; the browser entry remains included. Double-click the bundled
`MonkeyHub.exe`. A checkout build still needs the explicit options below.

When shortcuts are selected, the desktop installer keeps one `MonkeyHub.lnk`
pointing at this bundle's `MonkeyHub.exe`, updates a recognized older
`MonkeyHub.lnk` browser launcher, and removes a recognized legacy `MonkeyArch.lnk`.
Unrelated shortcuts are preserved. The bundle still includes `OPEN_MONKEYHUB.cmd`
for browser access; both surfaces use the same frontend, Python and applications.
The default version directory has a `-desktop` suffix. Existing installation
files and user data are retained; missing desktop or Fab files fail installation.
Installer tests use private temporary installation and shortcut directories.

Fab lives in this repository's `apps/monkeyfab/` and is included in every package,
with preparation and `send` dependencies. No external Fab repository or version
argument is needed. For source mode, install `apps/monkeyfab[send]` into the
`--python` environment from the repository root.

For explicit source development (both frontend `dist` directories must exist):

```powershell
& ./target/release/MonkeyHub.exe `
  --source-root C:/absolute/ARCHFLOW_V4 `
  --python C:/absolute/python.exe `
  --runtime-root C:/absolute/isolated-monkeyhub-runtime
```

There is no checkout discovery or active-install fallback. With no overrides,
the EXE uses its own parent as the source root and `%LOCALAPPDATA%/MonkeyHub` as
the existing nonproject runtime root. Hub holds `runtime/hub.lock` through its
complete shutdown, including a drain after the desktop is force-closed. A second
desktop or browser Hub using the same root refuses to start; use a separate
explicit runtime root when running independent instances.
An already-running older Hub that predates this lock must be stopped before
using its runtime directory; this host does not stop or adopt that process.

## Lifecycle and diagnostics

The shell selects an ephemeral `127.0.0.1` port and sends Hub a fresh managed
instance UUID. A positive `/api/health` must match the spawned PID, desktop parent
PID, UUID, source revision and `monkeyhub-api` protocol `0.1.0` before navigation.
It never attaches to an occupied port. `--port <number>` selects a diagnostic
port, and `--startup-timeout-seconds <1..600>` adjusts the default 60-second wait.

Startup, identity failures, loss of health and root crashes are visible in the
window. Unexpected exit also shows the last 24 lines from at most 8 KiB of its
log, including dependency errors or a runtime directory already in use.
`runtime/logs/desktop-<instance UUID>.log` includes the resolved endpoint,
owned PID, source revision, lifecycle transitions and Hub stdout/stderr. A
missing WebView2 or invalid launch configuration uses a native error dialog.
WebView cache lives in `runtime/cache/desktop-webview`, outside application assets
and project directories. Existing application settings and chats keep their
existing Hub-owned locations.

Closing requests `stop` and closes the root's stdin writer. The window remains
visible while Hub drains accepted operations and stops its own workers. There
is no forced shutdown deadline or worker-specific shell cleanup. If the host is
terminated, the OS closes the stdin pipe and Hub follows the same EOF shutdown.
A temporary health outage waits for the same instance; there is no automatic
root respawn or operation replay. After Hub has loaded, a temporary outage only
updates the native title: the current WebView document, open tool frames and
unsubmitted inputs stay in place. Recovery restores the title without reloading.
An exited root requires closing and reopening the desktop application.

## Patch update handoff

The Hub stages and validates updates beside the current version and exposes
`GET /api/updates/restart` only after an explicit restart request has passed its
idle/admission checks. The desktop accepts only a full commit and derives the
sibling `<commit-prefix>-desktop` directory itself. Neither the page nor the
HTTP reply can supply an arbitrary executable path. Checkout hosts cannot
activate installed updates.

The current desktop drains its Hub through the same managed stdin shutdown and
waits for the Hub to exit. A short-lived instance of the current EXE then waits
for that desktop to exit before opening the staged EXE with the same runtime
root. It is not a separate launcher or service. The new desktop initially shows
only its status page. It verifies its own Hub using the unchanged compiled
revision/PID/instance checks and reports that identity through a private stdout
pipe. The helper independently verifies that identity and calls the new Hub's
idempotent `/api/updates/complete` to activate the existing application entry.
Only the helper's `commit` on the trial's private stdin allows its project UI to
load. No WebView command bridge is added.

If startup or activation fails, the helper sends `stop` to only its trial
desktop, which drains only its own Hub. It waits for complete exit before
reopening the original EXE with the same runtime root. The original Hub's
`/api/updates/rollback` restores the old entry before its UI opens. There is no
forced-kill deadline and no overwrite of a running bundle. Losing the helper's
pipe before commit also stops the trial cleanly. This rollback covers the
initial, unopened trial; it does not downgrade a version after users have
continued project work in it.

The main window's navigation is restricted to the verified Hub origin and the
embedded status page. Existing Stage comparisons and Board source links can open
child native windows only on the same Hub or on a healthy worker origin currently
reported by that verified Hub's `/api/runtime`. Each child rechecks that membership
on navigation and closes with the main runtime. Other new windows are denied.
The existing Hub embeds its worker pages using their current runtime URLs.
No Tauri capabilities, command handlers or
plugins are granted to either local status content or the loopback UI. There is
no arbitrary shell, filesystem or process bridge exposed to JavaScript. The
browser development entry and Three.js viewport remain unchanged.

## Verification scope

`cargo test --locked` covers exact identity/protocol rejection, URL boundaries,
occupied-port refusal, spawn failure, child crash, stdin/EOF shutdown, operation
drain, independent-root isolation, reopen and bounded child-window origins.
Tests use disposable source and runtime directories plus a small Python child;
set `MONKEYHUB_TEST_PYTHON` when `python` is not on the test runner's PATH.
Update tests additionally exercise exact sibling selection, independent trial
identity, helper EOF/commit behavior, and native trial shutdown/reopen without
loading project UI before commit. Native trial tests never activate the user's
actual desktop shortcut; full installer/handoff tests require a private desktop
directory as well as an isolated runtime root.
The Windows workflow builds the complete ZIP through `package_monkeyapps.py
--desktop`, checks its checksum and runs the existing installer in a private
directory. Native lifecycle tests then run with the installed embedded Python
and the EXE's default paths; their child environment has only Windows tools on
PATH, with no developer Python/Node, virtual environment or Vite dependency.
The existing P036 test fixture is created outside the application; it is test
input, not an application import from the development checkout. Reinstallation
between close and reopen must preserve its retained bytes and user settings.
The package manifest identifies Node/ACP versions, Python dependency lock and
frontend asset hashes alongside the bound desktop/source version.

This is an isolated environment on the Windows runner, which already supplies
WebView2; it is not a second clean user machine. Clean-machine acceptance and root
restart/recovery remain subsequent issue #21 work. Local modeling drafts and
undo belong to the modeling workspace; the shell does not recover unsynced edits.
