# MonkeyArch desktop host

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

`target/release/MonkeyArch.exe` is the native host. It is **not a standalone
application bundle**. `tools/package_monkeyapps.py` owns the distribution tree,
Python dependencies, existing Hub/Studio frontend builds and package validation.
The package places this EXE at its root alongside `source-version.txt`,
`apps/monkeyhub/run.py` and `_runtime/python/python.exe`. It supplies
`ARCHFLOW_SOURCE_REVISION` when building from an exact source archive. Checkout
builds resolve the actual Git HEAD. The EXE refuses a different runtime source
revision. `MonkeyArch.exe --version` prints its version and source revision as
JSON without opening a window.

For explicit source development (both frontend `dist` directories must exist):

```powershell
& ./target/release/MonkeyArch.exe `
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

## Lifecycle and diagnostics

The shell selects an ephemeral `127.0.0.1` port and sends Hub a fresh managed
instance UUID. A positive `/api/health` must match the spawned PID, desktop parent
PID, UUID, source revision and `monkeyhub-api` protocol `0.1.0` before navigation.
It never attaches to an occupied port. `--port <number>` selects a diagnostic
port, and `--startup-timeout-seconds <1..600>` adjusts the default 60-second wait.

Startup, identity failures, loss of health and root crashes are visible in the
window. `runtime/logs/desktop-<instance UUID>.log` includes the resolved endpoint,
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
root respawn or operation replay. An exited root requires closing and reopening
the desktop application.

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
set `MONKEYARCH_TEST_PYTHON` when `python` is not on the test runner's PATH.
The package's real Windows smoke tests separately exercise the native window
and actual Hub. An installer, clean-machine acceptance and root restart/recovery
remain subsequent issue #21 phases.
