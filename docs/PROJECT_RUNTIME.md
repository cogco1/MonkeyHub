# The Project Runtime

MonkeyHub is the application. Everything a user sees — projects, chats, settings, the
Board, Arch and Diagram workspaces, Monitor, Fab — is MonkeyHub. Behind each open project
the Hub runs one **Project Runtime**: a project-scoped backend process that binds exactly
one P036 project directory and owns every project-scoped computation and record access for
it. This document is that runtime's contract: what the Hub gives it, what it owns, what it
must not own, how it is identified and reached, and how a developer starts one alone.

Code: `apps/archflow-studio/api/archflow_studio_api` — the package keeps its historical name;
registry module `studio.shell` (module ids do not follow product names, [REPO_LAYOUT §3](REPO_LAYOUT.md)).
The service id `studio`, the server name `monkeyarch-api` and the forwarding path segment
`/studio/` are process and wire names kept for compatibility; they name this runtime and
nothing else (§9). Issue #127 records the migration that gives the frontend the same shape;
the wire protocol of the Hub side is [PROTOCOL.md, "MonkeyHub project runtime"](PROTOCOL.md#monkeyhub-project-runtime).

## 1. One process per open project

- Key: `studio:` + the normalised, resolved project directory
  (`apps/monkeyhub/api/monkeyhub_api/applications.py`, `_child_key`; `workers.py`,
  `project_key`); runtime id `uuid5(NAMESPACE_URL, "<projectId>:<normcase path>")`
  (`runtime.py`). Identical project ids in different folders never share a process,
  admission or projection.
- The Hub creates, monitors and stops it (`applications.py`, `workers.py`). Nothing else does
  in production: one lifecycle owner (`AGENTS.md`, "Extend behavior, remove superseded paths").
- Ports: the configured `studioPort` for the configured project, an ephemeral loopback port
  for any other open project (`applications.py`, `start`). A port that is already bound is
  `PORT_IN_USE` (`workers.py`, `start`); a foreign listener is never adopted.
- MonkeyArch, MonkeyDiagram and MonkeyBoard of one project share that one process
  (`applications.py`, `APPS`: three app ids, one service id).

## 2. What MonkeyHub supplies

Command (`applications.py`, `_command`; `workers.py` appends the port and the managed flags):

```text
<hub python> apps/monkeyhub/run.py --service studio --host 127.0.0.1 --web-dir <built web dir>
             --port <port> --managed-stdin --managed-instance-id <uuid>
```

Environment: every inherited `ARCHFLOW_STUDIO_*` variable is removed first, then exactly
these are set (`applications.py`, `_command`):

| Variable | Source |
| --- | --- |
| `ARCHFLOW_STUDIO_MODE` | always `local` |
| `ARCHFLOW_STUDIO_PROJECT_DIR` | the opened project's directory |
| `ARCHFLOW_STUDIO_CAD_EXPORT` | Hub application settings `cadExport` (`occt` \| `rhino` \| `off`) |
| `ARCHFLOW_STUDIO_REFERENCE_RUN` | Hub application settings `referenceRun`, when set |
| `ARCHFLOW_STUDIO_INTENT_PROVIDER` | the user's saved preference, else `deterministic` |
| `ARCHFLOW_STUDIO_INTENT_MODEL`, `ARCHFLOW_STUDIO_INTENT_TIMEOUT_S` | the user's saved preferences, when set |
| `MONKEYMONITOR_DATA_DIR` | `<runtime root>/diagnostics/monkeymonitor` |

The runtime reads only that environment for its own configuration
(`archflow_studio_api/settings.py`, `StudioSettings.from_env`). It never reads the user
preference file or the application settings file to decide how it runs; those files are the
Hub's (§4). The full variable list the settings module understands, including the remote-mode
and shared-project ones the Hub does not set, is documented in `settings.py`.

Stdin: `stop` followed by a newline, or EOF, requests a graceful stop that lets accepted work
finish (`workers.py`; `archflow_studio_api/main.py --managed-stdin`).

`--web-dir`: until Phase 4 of #127 the runtime also serves the legacy Studio web bundle at
`/`. That is hosting a client, not owning one (§4).

## 3. What it owns

Every project-scoped responsibility the product needs, behind `/api` (`routes/__init__.py`):

| Concern | Routes | Owner module(s) |
| --- | --- | --- |
| Project binding and preparation | `GET /api/project`, `POST /api/project/modeling`, `GET /api/projects*` | `studio.binding` |
| Canonical state, frame, volumes, closure | `GET /api/state`, `/state/frame`, `/state/volumes`, `POST /api/state/closure` | state owners in `archflow/state`, projection in `studio.binding` |
| Program sheet and semantics | `GET`\|`POST /api/program`, `GET /api/semantics` | `studio.program`, `state.program_sheet`, `semantics.registry` |
| Massing options | `POST`\|`GET /api/options`, `POST /api/options/{id}/select` | `studio.options` |
| Proposals and picking | `POST /api/proposals*`, `POST /api/pick/resolve`, `GET /api/proposals/{id}`, decisions | `studio.intent`, geometry owners in `monkeyarch` |
| Candidates, jobs, validation | `POST /api/proposals/{id}/candidate`, `POST /api/candidates/combine`, `GET /api/jobs/{id}`, `GET /api/candidates/{id}*` | `studio.candidate`, `studio.validation`, `runtime.project_runner` |
| Runtime status of this process | `GET /api/runtime` — live jobs and retained candidate outcomes, read-only | `studio.candidate` |
| Stages, branches, working copies, episodes | `GET /api/design-history`, `POST /api/design-stages/initialize`, `POST /api/candidates/{id}/accept`, `/api/design-branches`, `/api/working-copies*`, `/api/episodes*` | `studio.intent` |
| Documents, artifacts, captures, drawings, board, studies | `/api/documents*`, `/api/artifacts*`, `/api/model-assets`, `/api/captures`, `/api/drawings*`, `/api/board*`, `/api/studies*` | `studio.artifacts`, `studio.board`, `studio.study`, `documentation.drawings` |
| Annotations and intents | `/api/model-annotations`, `/api/document-annotations`, `/api/document-comments`, `/api/intents*` | `studio.intent` |
| Capabilities | `/api/capabilities*` | `studio.intent` |
| Events | `GET /api/events`, `POST /api/events/*` | `studio.candidate`, `studio.shell` |
| Shared-project role | `/api/sync/*` | `studio.binding` |

The three remaining route files answer about the process, not about the project:
`routes/health.py` and `routes/protocol.py` are its identity (§5), and `routes/settings.py` is
served on the Hub's behalf (§4). All three belong to `studio.shell`.

Also: the CAD/OCCT/Rhino execution and inspection adapters (`archflow/adapters`), selected by
`ARCHFLOW_STUDIO_CAD_EXPORT`; the evaluator/generator jobs; and every retained-record write
through `archflow.project.repository`. In production the runtime is the only writer of a
project. The Hub's runtime records and admission journal are observations, not a second
project repository (PROTOCOL.md, "MonkeyHub project runtime").

Owner module names above are those in `governance/module_registry.json` (`docs/SYSTEM_MAP.md`
renders them); where a concern has several owners the registry is authoritative.

## 4. What it does not own

- **Settings authority.** User preferences (`%APPDATA%\MonkeyArch\settings.json`) and
  application settings (`<runtime root>/config/applications.json`) belong to MonkeyHub: the Hub
  edits them, and injects the resolved values at launch (§2). The code that reads and writes
  those files lives in this package (`settings.py`) because the Hub imports it and mounts
  `routes/settings.py`; the runtime serves `GET/PUT /api/settings/user` in local mode for that
  reason and consults the file for nothing of its own. No second preference store may be added
  on either side.
- **Product navigation, lifecycle, launchers.** Projects, chats, the tool rail, the launch
  window, the tray, shortcuts and the desktop window are `hub.shell`
  (`apps/monkeyhub/launch-hub.ps1`, `apps/monkeyhub/desktop`).
- **Monitor.** A separate process (`monkeymonitor`), started by the Hub, coupled to the
  runtime only through `MONKEYMONITOR_DATA_DIR`.
- **Any user interface.** The legacy Studio web shell (`apps/archflow-studio/web/src/app/*`,
  `features/settings`, `i18n`, `styles.css`; registry module `studio.web.shell`) is a client of
  this runtime that is migrating into MonkeyHub (#127). It takes no new product-level
  behaviour: product behaviour goes to the Hub shell, workspace behaviour to `workspaces/*`,
  project-scoped backend behaviour here.

## 5. Identity and health

`GET /api/health` (`routes/health.py`) answers `managedInstanceId`, `processId`,
`parentProcessId`, `sourceRevision`, `serverVersion` and `projectBound`. The Hub accepts a
worker only when all of them match its own launch (`workers.py`, health verification), and
then requires `GET /api/project` to name the exact `projectId` and a normcase-equal
`projectDir`. A mismatch is `SERVICE_IDENTITY_MISMATCH` and the child is stopped; the start
deadline is 30 s (`START_TIMEOUT`); a verified worker that stops answering becomes
`unavailable`. `GET /api/protocol` (`protocol.py`) answers `archflow/<major>` and the
capability list computed from the settings; the client refuses a foreign or mismatched server
at handshake (PROTOCOL.md §1).

## 6. How it is reached

In production only through the Hub's forwarding path
`/api/runtime/projects/{runtime_id}/studio/{path}` (`monkeyhub_api/main.py`, `runtime.py`
`forward`): path allowlist (`/api/...` and `/openapi.json` only), project id checked in query
and body (`PROJECT_MISMATCH`), `Idempotency-Key` admission for mutations, `X-Monkey-Candidate`
and `X-Monkey-Worker` for candidate-producing requests, an allowlist of forwarded request
headers, and a hand-piped SSE relay for `/api/events`. Chat tools use the same path. A
runtime that is not `ready` or `busy` and healthy answers `WORKER_UNAVAILABLE`. Direct access
to the runtime's own port is for development and tests (§8).

## 7. Isolation

- One process, one project, one port. A crash affects one project; `recover` restarts it on
  the same port after inspecting retained outcomes and replays no mutation; `close` drains it.
  Other projects continue (PROTOCOL.md, "MonkeyHub project runtime").
- The Hub keeps no copy of canonical state, candidates or documents; its projection is rebuilt
  from this runtime's read routes and the project's retained receipts.
- Nothing in this package holds cross-project state: every request is answered against the
  one bound project directory.

## 8. Starting one yourself

For tests and development — an API smoke run, Playwright, a fixture regression, a workspace
change you want to see without going through the Hub:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/run-project-runtime.ps1 -ProjectDir '<a P036 project directory>' [-Port 8000] [-WebDir apps/archflow-studio/web/dist] [-Python <python.exe>]
```

or directly `python -m archflow_studio_api.main --project-dir <dir> --host 127.0.0.1 --port 8000`
with the checkout and `apps/archflow-studio/api` on `PYTHONPATH`. Configuration is the
`ARCHFLOW_STUDIO_*` environment of that shell; there is no launch window, tray, configuration
file or default project (#126). Fixtures: `tools/create_project.py --project <dir>` for an
empty project; `apps/archflow-studio/api/tests/support.py` (`make_empty_project`) inside tests.

"Independently runnable" is not "independent product entry": a runtime started this way is a
development instance, and MonkeyHub remains the only production launcher.

## 9. Change rules

- Routes and wire shapes are stable. Removing or renaming a stable field or path is a protocol
  major change (PROTOCOL.md).
- New project-scoped behaviour goes into this package's `application/` owners; new product
  behaviour goes to the Hub; nothing new goes into the legacy web shell.
- The names `studio` (service id, worker key prefix, forwarding segment), `monkeyarch-api`
  (server name) and the `archflow_studio_api` package are renamed together, once, after the
  legacy shell is gone (#127 Phase 4) — never piecemeal, because clients validate the
  forwarding path and the server name at handshake.
- The runtime never grows a persistent user-settings file, a launcher, a tray or a UI of its
  own.
