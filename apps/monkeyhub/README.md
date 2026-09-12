# MonkeyHub — application entry

MonkeyHub opens directly into project conversations: projects and chats on the left, the conversation and composer in the center, and existing Arch, Diagram, Board, Fab or Monitor pages in a resizable panel on the right. Those five entries live in one vertical rail along the far right edge, which also carries the bound project and the control that opens or closes the tool panel; a tool page embedded there draws no second copy of them. The installed Codex or Claude CLI continues each conversation using its native session. See the [shared entry and agent lookup sequence](../../docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#0-monkeyhub-统一入口).

New project creates one in the workspace: name it, and Hub initializes an empty P036 project at version 0 through the same path `tools/create_project.py` uses, then opens a conversation in it. The workspace is a location in Hub settings — the saved folder, else the folder the current project already lives in, else `workspace/projects` under this Hub's runtime root. A name that is not a project id, a name that would leave that folder, and a folder that already holds anything are each refused by their own reason, and nothing existing is written over. An existing complete ArchFlow project folder can still be added directly. Send a message to start.

Hub settings, bottom left, hold the global choices: language, theme, text size, the default CLI connection and the default model. Each connection is checked once per run, read-only: whether its CLI is installed, whether that CLI reports itself signed in, and which models it lists. Codex answers `model/list` on its own app-server protocol and Claude Code carries its list in the SDK control protocol's initialize answer — the same one `supportedModels()` reads — so each catalogue is that account's own. A connection that cannot be read says why and still accepts a model id typed by hand. Nothing is guessed, no conversation is started to find out, and no credential is read or reported. A check is reused until it ages out; the transcript's polling never starts a CLI, and Hub settings can ask again.

A new conversation starts from those defaults; an existing conversation keeps the connection, model and native CLI session it was created with, and changing a default never reaches one. The composer states which connection is answering and offers that conversation's model. Changing it applies to that conversation's next messages only: its connection, its native CLI session and its project stay as they are, the global default is not rewritten, and a reply already running keeps the model it started with. The connection itself is still chosen once, in Hub settings. Coding Plan uses the installed Claude CLI's configured Anthropic-compatible endpoint and authentication. A connection being available means its executable/configuration was found; login or provider errors are reported when the CLI runs.

Before a message or tool page opens, Hub binds its managed Studio to that chat's project. A running chat prevents switching that service to another project. Chat tools inspect the actual Studio schema and call its existing deterministic proposal/candidate and drawing APIs; they do not call the intent model a second time. Available domain actions remain limited to those APIs. Review, endorsement and formal issue retain their existing application flows.

Each of those calls stays in the conversation as one row: the tool, its method and path, and the CLI's own word for how it ended. Both installed CLIs are read the same way — Codex reports one MCP item per call, Claude reports a `tool_use` block answered later by a `tool_result`, joined by its tool id — and a CLI's own file tools appear as the activity they are. Only this adapter's bound tools speak for the project: a result from anything else, or from a failed call, never names a candidate. A refused or failed call remains visible instead of disappearing. The diagnostics under a row are collapsed and bounded — the values a person can act on, or a short preview with the remaining length stated; a whole state or schema document is never copied into the transcript. The rows are saved with the conversation, so reloading the page or reopening Hub shows the same history.

The rail's project entry answers for the conversation's own project: its name, folder, published version and accepted Stage as the project itself records them, and the candidates this conversation has produced or read. A candidate is named as a candidate — not endorsed, not issued — and never as a version. Selecting a conversation in another project re-reads that project; a conversation with no project says so.

When a call reports that a candidate finished, that row offers to open it: the right panel opens MonkeyArch on `?embedded=tool&candidate=<run>`, which binds that exact candidate run rather than the project's reference run, and the conversation stays where it was. A run that cannot be opened is refused by name; no default view stands in for a conversation's own result. Opening a candidate is looking at it — it is not acceptance, endorsement or issue.

The home page labels these workspaces by their task: Modeling, Drawings, Usage, Presentation and Fabrication. Existing MonkeyArch/Diagram/Board/Monitor/Fab API identifiers and service boundaries remain the implementation names.

MonkeyHub opens without a building project. It starts MonkeyMonitor independently and shares one Studio service between MonkeyArch, MonkeyDiagram and MonkeyBoard. MonkeyDiagram opens that service at /?view=documents; MonkeyBoard opens at /?view=board for single-operator drawing layout and meeting presentation.

The integrated package also includes MonkeyFab. Its card opens `/?view=fab` in the Hub, where users prepare print parts or upload an already sliced `.gcode.3mf`. The page calls the independent MonkeyFab CLI with the same embedded Python; it needs no extra service or project.

The Hub runs the existing services and owns only the child processes it starts. It does not initialize projects or provide a plugin or general model gateway.

## Packaged Windows entry

The package keeps the repository layout and includes prebuilt Hub and Studio web directories. The installer supplies an embedded Python with the API, CAD and PDF dependencies already installed. No Git or npm command is needed to run that package.

An integrated build includes the selected MonkeyFab source and its preparation/send dependencies in that same runtime. `build-info.json` binds both repositories' commits; the installer distinguishes packages with different Fab versions even when their ArchFlow commit is the same.

The root OPEN_MONKEYHUB.cmd calls this entry:

```powershell
.\apps\monkeyhub\launch-hub.ps1 -Python "$PWD\_runtime\python\python.exe" -RuntimeRoot "$env:LOCALAPPDATA\MonkeyHub" -HubWebDir "$PWD\apps\monkeyhub\web\dist" -StudioWebDir "$PWD\apps\archflow-studio\web\dist" -HideConsole
```

The wrapper reuses the Studio launcher's splash, tray, logs and monitoring. Quit MonkeyHub requests normal shutdown and waits for its applications; accepted candidate jobs finish and event streams close. It does not force-close a busy application or stop a process found on an occupied port.

## Python entry and development

Use Python 3.12 or later with apps/archflow-studio/api/requirements.txt and apps/monkeyhub/api/requirements.txt. Studio's normal OCCT export additionally uses the root project's cad-occt extra. The packaged interpreter may be newer if the installer has verified its binary dependencies.

For new Codex chats in a source checkout, install the pinned ACP adapter once with `npm ci --prefix apps/monkeyhub`. Hub uses `agent-client-protocol==0.12.1` and `@agentclientprotocol/codex-acp==1.11.0`, passing the installed native Codex executable through `CODEX_PATH` instead of choosing the adapter's bundled Codex. The compatibility check uses Codex 0.153.4. Node must be on PATH. A missing dependency is shown as unavailable; sending a message never downloads an adapter. This source integration does not update an already installed Hub package.

Each new Codex chat keeps one adapter process between turns. Hub saves its opaque ACP session ID separately from old CLI IDs and restores it after reopening; a failed restore is reported without creating another chat or resending the turn. Existing Codex CLI chats and Claude chats continue through their original transport. Model choices still apply to that conversation and use the installed native configuration. Permission requests appear in the tool activity with the adapter's own options; only a submitted choice responds, and stop or shutdown cancels pending requests.

```powershell
& $Python .\apps\monkeyhub\run.py --runtime-root 'E:\MonkeyHub local test' --port 8790 --hub-web-dir .\apps\monkeyhub\web\dist --studio-web-dir .\apps\archflow-studio\web\dist --no-browser
```

run.py adds the source and API import paths explicitly, including for embedded Python that ignores PYTHONPATH. Its fixed --service studio and --service monitor forms call the existing service entry points; child processes use the same sys.executable.

For source development, install the independent [MonkeyFab repository](https://github.com/cogco1/MonkeyFab) with its `send` extra into the Python environment used to start Hub. If that package is absent, its card reports that it is not included. Production distributions carry its selected source under `apps/monkeyfab/src`.

An API-only development run may omit --hub-web-dir. To connect a separate local web development server, pass --web-origin http://127.0.0.1:5175 (substitute its actual port). The packaged web and API use one origin. Studio startup requires its built index.html; supplying no Studio build does not prevent Monitor from running.

Run `npm --prefix apps/monkeyhub/web run dev` for the web development server on 127.0.0.1:5175; its `/api` proxy targets `MONKEYHUB_API_URL`, defaulting to `http://127.0.0.1:8790`.

For a headless test, add --managed-stdin --managed-instance-id followed by a fresh UUID and keep the process's stdin pipe. Writing stop followed by a newline, or closing that owned pipe, requests shutdown. --no-browser suppresses automatic browser opening. Normal standalone CLI use remains available.

To try uncommitted work, build both web apps and start `launch-hub.ps1` with an external runtime root and ports of its own, keeping the installed package untouched. Such a run is a source trial: it runs the working tree, and the packaged installer still exports a committed Git snapshot, so a source trial never means the installed application has been updated. Give a source trial its own project — `tools/create_project.py`, or for a disposable one the shared `make_project` fixture — rather than opening a real building project.

## Configuration and data

The default runtime root is LOCALAPPDATA/MonkeyHub; --runtime-root selects another absolute nonproject directory.

| Content | Owner and location |
| --- | --- |
| Language, theme, font scale, model defaults, and the default chat connection and model | Existing Studio settings owner; APPDATA/MonkeyArch/settings.json |
| Chosen Studio project/run, workspace folder for new projects, CAD export and service ports | Same settings owner; runtime-root/config/applications.json |
| Hub-owned child stdout/stderr | runtime-root/logs/ |
| Chat transcripts, project associations and native CLI session ids | runtime-root/chats/; these are conversations, not building state |
| Optional Studio usage diagnostics read by Monitor | runtime-root/diagnostics/monkeymonitor/ |
| Building data and retained runs | The selected project's existing ArchFlow project interfaces |
| Print STL parts and assembly table | The new or empty absolute output directory explicitly entered in the Fab page; the independent CLI writes it |

User preferences and application launch configuration have separate purposes and cannot overwrite one another. No credentials are stored in application configuration. Hub reads the existing saved model defaults when it starts Studio; changing a saved default does not replace an already running compiler.

No StudioSettings object is created for the Hub. Choose an existing complete project before starting MonkeyArch, MonkeyDiagram or MonkeyBoard; the Studio health check must confirm that binding. Clearing the Hub's selected path does not delete a project. Stop the affected service before changing its launch configuration; an independent Monitor can stay running while Studio changes project.

## HTTP contract

Agents use this same Hub: read health, application status and selected application settings, then use the actual running service URL and its relevant API contract. Hub's chat CLI receives its bound project and a small stdio tool connection that reads the selected Studio action schema on demand. Source development uses `devctl module`, and shared toolbox lookup uses `hgs skills list/show`; the shared skill catalog is not yet an executable chat tool.

Chat API: `GET /api/chat/providers` (add `?refresh=true` to check the installed CLIs again), `GET /api/chat/workspace`, `POST /api/chat/projects`, `GET /api/chat/projects`, `GET /api/chat/sessions`, `POST /api/chat/sessions`, `GET /api/chat/sessions/{id}`, `POST /api/chat/sessions/{id}/messages`, `PUT /api/chat/sessions/{id}/model`, `POST /api/chat/sessions/{id}/permissions/{permission_id}` and `POST /api/chat/sessions/{id}/stop`. Permission decisions require the bound `projectId` and one offered `optionId`, or explicit null to cancel; stale requests return 409. The same native session is resumed on the next turn; an interrupted Hub run remains visible and can be continued after reopening.

A chat project carries the published `version` and accepted `stage` its own P036 records hold, or null when they cannot be read; a candidate is never reported there. A chat message has role `user`, `assistant` or `tool`. A `tool` message is one MCP call: its first content line is the summary and the rest are its bounded diagnostics; `candidateId` names a finished candidate when that call reported one, and is absent everywhere else, including in records written before this field existed.

The actual schema is available at GET /openapi.json. Generate a client from this running schema and use a separate client instance for each service.

| Request | Result |
| --- | --- |
| GET /api/health | Hub service/version, actual process and parent process IDs, managed instance ID, source revision |
| GET /api/apps | AppStatus[] for the five fixed application cards; Fab is hosted by Hub when installed |
| POST /api/apps/{app_id}/start | 202 with starting or current status; repeat requests reuse the owned process |
| POST /api/apps/{app_id}/stop | 202 with stopping or current status; stopping any Studio card stops their shared service |
| GET/PUT /api/settings/user | Existing UserSettingsDto and existing local preference routes |
| GET/PUT /api/settings/apps | projectDir, referenceRun, cadExport, studioPort, monitorPort |
| GET /api/fab/profiles | Printer envelopes from the installed MonkeyFab CLI |
| POST /api/fab/prepare | Prepare a local STL/OBJ in the explicitly selected output directory and return the CLI result |
| POST /api/fab/send | Validate or upload a sliced file; return the CLI JSON result without starting a print |

Fab's card has no independent Stop button. Its preparation request requires source units and explicit local paths. Send requires a sliced `.gcode.3mf`, printer address and request-local LAN access code; dry run needs no code and makes no printer connection. The page clears the access-code field when sending, and neither settings nor request errors retain it. Slicing, support selection and printer/material settings remain in Bambu Studio. Actual machine compatibility depends on that printer's LAN interface and firmware.

Default ports are Hub 8790, Studio 8789 and Monitor 8788. Poll GET /api/apps while starting or stopping; open the returned URL only when state is running. States are stopped, starting, running, stopping, error and unavailable. An application error contains code and detail. Port conflicts, missing projects/builds and wrong service identity are explicit failures, not successful launches.

The source identity comes from root source-version.txt in a package, or the actual checkout HEAD. Missing or malformed identity cannot be presented as a verified launch. A responding child must match its launch UUID, source revision, service version and the owned PID (or its direct child's parent PID, for Windows Python venv redirectors). Stop always uses the owned stdin pipe rather than a reported PID.

Logs and process handles belong to this local run. This does not add a persistent task queue or restore in-flight jobs after a crash. Independent package and second-machine validation are handled by the installation workstream.
