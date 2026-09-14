# MonkeyHub — application entry

MonkeyHub opens directly into project conversations: projects and chats on the left, the conversation and composer in the center, and existing Arch, Diagram, Board, Fab or Monitor pages in a resizable panel on the right. Those five entries live in one vertical rail along the far right edge, which also carries the bound project and the control that opens or closes the tool panel; a tool page embedded there draws no second copy of them. The installed Codex or Claude CLI continues each conversation using its native session. See the [shared entry and agent lookup sequence](../../docs/WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md#0-monkeyhub-统一入口).

New project creates one in the workspace: name it, and Hub initializes an empty P036 project at version 0 through the same path `tools/create_project.py` uses, then opens a conversation in it. The workspace is a location in Hub settings — the saved folder, else the folder the current project already lives in, else `workspace/projects` under this Hub's runtime root. A name that is not a project id, a name that would leave that folder, and a folder that already holds anything are each refused by their own reason, and nothing existing is written over. An existing complete ArchFlow project folder can still be added directly. Send a message to start.

Hub settings, bottom left, hold the global choices: language, theme, text size, the default CLI connection and the default model. Each connection is checked once per run, read-only: whether its CLI is installed, whether that CLI reports itself signed in, and which models it lists. Codex answers `model/list` on its own app-server protocol and Claude Code carries its list in the SDK control protocol's initialize answer — the same one `supportedModels()` reads — so each catalogue is that account's own. A connection that cannot be read says why and still accepts a model id typed by hand. Nothing is guessed, no conversation is started to find out, and no credential is read or reported. A check is reused until it ages out; the transcript's polling never starts a CLI, and Hub settings can ask again.

A new conversation starts from those defaults; an existing conversation keeps the connection, model and native CLI session it was created with, and changing a default never reaches one. The composer states which connection is answering and offers that conversation's model. Changing it applies to that conversation's next messages only: its connection, its native CLI session and its project stay as they are, the global default is not rewritten, and a reply already running keeps the model it started with. The connection itself is still chosen once, in Hub settings. Coding Plan uses the installed Claude CLI's configured Anthropic-compatible endpoint and authentication. A connection being available means its executable/configuration was found; login or provider errors are reported when the CLI runs.

Each conversation has an archive button in the sidebar. Archived chats are hidden from the current list and remain available through Archived chats, where their full messages and candidate links can be read and the conversation restored. Restore a chat before sending its next message. A running reply must finish or be explicitly stopped before its chat can be archived. Archiving preserves the native CLI session and its transcript in the same saved chat file, including across a Hub restart.

Before a message or tool page opens, Hub binds its managed Studio to that chat's project. Each project has its own Studio, so other projects can keep working while a chat runs. Chat tools inspect the actual Studio schema and call its existing deterministic proposal/candidate and drawing APIs; they do not call the intent model a second time. Available domain actions remain limited to those APIs. Review, endorsement and formal issue retain their existing application flows.

Each of those calls stays in the conversation as one row: the tool, its method and path, and the CLI's own word for how it ended. Both installed CLIs are read the same way — Codex reports one MCP item per call, Claude reports a `tool_use` block answered later by a `tool_result`, joined by its tool id — and a CLI's own file tools appear as the activity they are. Only this adapter's bound tools speak for the project: a result from anything else, or from a failed call, never names a candidate. A refused or failed call remains visible instead of disappearing. The diagnostics under a row are collapsed and bounded — the values a person can act on, or a short preview with the remaining length stated; a whole state or schema document is never copied into the transcript. The rows are saved with the conversation, so reloading the page or reopening Hub shows the same history.

The rail's project entry answers for the conversation's own project: its name, folder, published version and accepted Stage as the project itself records them, and the candidates this conversation has produced or read. A candidate is named as a candidate — not endorsed, not issued — and never as a version. Selecting a conversation in another project re-reads that project; a conversation with no project says so.

When the current turn successfully reads back a candidate, the right panel opens it once, even while the conversation continues working on drawings. The result row also offers to open it manually. MonkeyArch opens on `?embedded=tool&candidate=<run>`, binding that exact run while the conversation stays where it was. Repeated clicks on the same open candidate and later transcript polls retain the loaded page; an already running project can open its candidate directly from its scoped application URL. A slow opening response is discarded if the selected chat or project changed in the meantime. A run that cannot be opened is refused by name; no default view stands in for a conversation's own result. Opening a candidate is looking at it — it is not acceptance, endorsement or issue.

The home page labels these workspaces by their task: Modeling, Drawings, Usage, Presentation and Fabrication. Existing MonkeyArch/Diagram/Board/Monitor/Fab API identifiers and service boundaries remain the implementation names.

MonkeyHub opens without a building project. It starts MonkeyMonitor independently and shares one Studio service between MonkeyArch, MonkeyDiagram and MonkeyBoard. MonkeyDiagram opens that service at /?view=documents; MonkeyBoard opens at /?view=board for single-operator drawing layout and meeting presentation.

The integrated package also includes MonkeyFab. Its card opens `/?view=fab` in the Hub, where users prepare print parts or upload an already sliced `.gcode.3mf`. The page calls the independent MonkeyFab CLI with the same embedded Python; it needs no extra service or project.

The Hub runs the existing services and owns only the child processes it starts. It does not initialize projects or provide a plugin or general model gateway.

## Packaged Windows entry

The chat paperclip accepts files; files can also be dropped or pasted into the composer. Send text, attachments, or both. A message accepts up to 8 files, 20 MiB each and 40 MiB in total. Sent files stay with the conversation and can be downloaded after reopening or archiving it. PNG, JPEG, WebP and GIF use the native model image input; PDFs and other files are available through the CLI's file-reading tools. Attachments are reference files in the Hub runtime, not imports into a building project.

The package keeps the repository layout and includes prebuilt Hub and Studio web directories. The installer supplies an embedded Python with the API, CAD and PDF dependencies already installed. No Git or npm command is needed to run that package.

Every build includes `apps/monkeyfab` and its preparation/send dependencies in the same runtime. One Hub source commit in `build-info.json` identifies every included application, for both desktop and browser entrypoints.

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

For source development, run `python -m pip install -e "apps/monkeyfab[send]"` from this repository using the Python environment that starts Hub. Fab code lives in [apps/monkeyfab](../monkeyfab/README.md); Hub always calls that checkout’s CLI, and complete desktop/browser packages include it by default. No second repository or source ref is needed.

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
| Chat transcripts, archive state, project associations and native CLI session ids | runtime-root/chats/; these are conversations, not building state |
| Chat attachment bytes | runtime-root/chats/<session-id>/attachments/; transcripts retain file metadata only |
| Optional Studio usage diagnostics read by Monitor | runtime-root/diagnostics/monkeymonitor/ |
| Building data and retained runs | The selected project's existing ArchFlow project interfaces |
| Print STL parts and assembly table | The new or empty absolute output directory explicitly entered in the Fab page; the independent CLI writes it |

User preferences and application launch configuration have separate purposes and cannot overwrite one another. No credentials are stored in application configuration. Hub reads the existing saved model defaults when it starts Studio; changing a saved default does not replace an already running compiler.

No StudioSettings object is created for the Hub. Choose an existing complete project before starting MonkeyArch, MonkeyDiagram or MonkeyBoard; the Studio health check must confirm that binding. Clearing the Hub's selected path does not delete a project. Each project has its own Studio process and port; Arch, Diagram and Board share that project's process. Pass projectDir to app status/start/stop requests to select it without changing saved launch settings. A running chat protects only its own Studio from being stopped; other projects can keep working independently.

## HTTP contract

Agents use this same Hub: read the conversation's project, its scoped application status and health, then use that project's actual running service URL and its relevant API contract. Hub's chat CLI receives its bound project and a small stdio tool connection that reads the selected Studio action schema on demand. Source development uses `devctl module`, and shared toolbox lookup uses `hgs skills list/show`; the shared skill catalog is not yet an executable chat tool.

Chat API: `GET /api/chat/providers` (add `?refresh=true` to check the installed CLIs again), `GET /api/chat/workspace`, `POST /api/chat/projects`, `GET /api/chat/projects`, `GET /api/chat/sessions`, `POST /api/chat/sessions`, `GET /api/chat/sessions/{id}`, `POST /api/chat/sessions/{id}/messages`, `PUT /api/chat/sessions/{id}/model`, `PUT /api/chat/sessions/{id}/archive`, `POST /api/chat/sessions/{id}/permissions/{permission_id}` and `POST /api/chat/sessions/{id}/stop`. Permission decisions require the bound `projectId` and one offered `optionId`, or explicit null to cancel; stale requests return 409. The same native session is resumed on the next turn; an interrupted Hub run remains visible and can be continued after reopening.

Session summaries and details include `archived`, defaulting to false for older saved files. The session list defaults to `?archived=false`; `?archived=true` lists only archived chats, and either view can also filter by `projectId`. Reading an individual chat retains its full transcript in either state. `PUT /api/chat/sessions/{id}/archive` takes `{"archived": true}` to archive or `{"archived": false}` to restore and returns `ChatDetail`. Repeating the same value leaves the record unchanged. A running chat returns 409 `CHAT_RUNNING`; sending to an archived chat returns 409 `CHAT_ARCHIVED` until it is restored.

A chat project carries the published `version` and accepted `stage` its own P036 records hold, or null when they cannot be read; a candidate is never reported there. A chat message has role `user`, `assistant` or `tool`. A `tool` message is one MCP call: its first content line is the summary and the rest are its bounded diagnostics; `candidateId` names a finished candidate when that call reported one, and is absent everywhere else, including in records written before this field existed.

The actual schema is available at GET /openapi.json. Generate a client from this running schema and use a separate client instance for each service.

`POST /api/chat/sessions/{id}/messages` accepts optional `attachments: [{name, mimeType, data}]`, where `data` is plain base64, and allows empty text when a file is attached. Messages return only `{id, name, mimeType, size}` attachment metadata. `GET /api/chat/sessions/{id}/attachments/{attachment_id}` downloads an attachment belonging to that conversation.

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

## Project runtime and recovery

Hub keeps one runtime for each exact project identity and resolved path. Switching or reopening a page attaches to that runtime; it does not stop another project or restart a crashed worker. `GET /api/runtime` reports project bindings, the published P036 position, committed Stage history, owned worker health, attached chats, and active/recent operations. `GET /api/runtime/events` streams changes and begins every connection with a fresh snapshot, including after an old or foreign event cursor.

Worker and session observation continues every second. Retained history refreshes for active work, submitted mutations, attachment and worker changes; an idle runtime reuses its current projection and checks external project changes every 30 seconds. Status reads verify retained receipts and exact sources without rebuilding candidate previews. The Hub entry point closes SSE subscriptions before draining accepted HTTP work and owned processes during shutdown.

| Request | Result |
| --- | --- |
| `POST /api/runtime/projects/open` with `projectId`, `projectDir` | Attach or reopen a runtime; no worker is started |
| `GET /api/runtime/projects/{runtime_id}` | The same project/worker/operation/retained-state snapshot |
| `POST /api/runtime/projects/{runtime_id}/recover` with `projectId` | Inspect retained results, then restart a crashed owned Studio on its original port; no mutation is replayed |
| `POST /api/runtime/projects/{runtime_id}/close` with `projectId` | Cancel that project's agent turns and pending permissions, close its ACP connections, and drain its owned Studio; other projects continue |
| `/api/runtime/projects/{runtime_id}/studio/api/...` | Forward the existing Studio request to its verified, bound worker |

Embedded Studio tools and chat mutations use this project-scoped forwarding path. Each mutation has its own UUID `Idempotency-Key`; diagnostic `X-Monkey-Operation` spans remain separate. Repeating a key with the same request returns its admission/result, while a different request returns `409 OPERATION_ID_CONFLICT`. Chat's `studio_request` optionally accepts the same UUID as `operationId`.

Hub assigns candidate run ids before dispatch, so a lost 202 cannot hide which retained run to inspect. A completed candidate requires the existing complete runner/composed-model readback. Stage acceptance is reported committed only when the matching candidate, branch and exact parent Stage are reachable from the retained branch. A prepared Stage file alone is not a commit. An interrupted request without that proof stays `needs_recovery`; recovery never resends it. A confirmed refusal before dispatch remains a failure even if an older candidate exists.

Operation admissions and HTTP replies live in the Hub process. Restarting Hub reconstructs retained candidates, committed Stages and saved chats, but does not restore proposals/jobs or requests that never reached a retained run. Reusing a candidate operation UUID after a Hub restart refuses an already existing run and directs the caller to read it. This is not a durable background task queue. Warm provider/geometry reuse remains inside the existing Studio/ACP owners; external Rhino bridges and other processes Hub does not own are not automatically restarted. Installed-package and second-machine validation remain separate from this source change.
