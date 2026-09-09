# MonkeyHub local services

MonkeyHub opens without a building project. It starts MonkeyMonitor independently and shares one Studio service between MonkeyArch and MonkeyDiagram. MonkeyDiagram opens that service at /?view=documents. MonkeyBoard is listed as unavailable.

The Hub runs the existing services and owns only the child processes it starts. It does not initialize projects or provide a plugin or general model gateway.

## Packaged Windows entry

The package keeps the repository layout and includes prebuilt Hub and Studio web directories. The installer supplies an embedded Python with the API, CAD and PDF dependencies already installed. No Git or npm command is needed to run that package.

The root OPEN_MONKEYHUB.cmd calls this entry:

```powershell
.\apps\monkeyhub\launch-hub.ps1 -Python "$PWD\_runtime\python\python.exe" -RuntimeRoot "$env:LOCALAPPDATA\MonkeyHub" -HubWebDir "$PWD\apps\monkeyhub\web\dist" -StudioWebDir "$PWD\apps\archflow-studio\web\dist" -HideConsole
```

The wrapper reuses the Studio launcher's splash, tray, logs and monitoring. Quit MonkeyHub requests normal shutdown and waits for its applications; accepted candidate jobs finish and event streams close. It does not force-close a busy application or stop a process found on an occupied port.

## Python entry and development

Use Python 3.12 or later with apps/archflow-studio/api/requirements.txt. Studio's normal OCCT export additionally uses the root project's cad-occt extra. The packaged interpreter may be newer if the installer has verified its binary dependencies.

```powershell
& $Python .\apps\monkeyhub\run.py --runtime-root 'E:\MonkeyHub local test' --port 8790 --hub-web-dir .\apps\monkeyhub\web\dist --studio-web-dir .\apps\archflow-studio\web\dist --no-browser
```

run.py adds the source and API import paths explicitly, including for embedded Python that ignores PYTHONPATH. Its fixed --service studio and --service monitor forms call the existing service entry points; child processes use the same sys.executable.

An API-only development run may omit --hub-web-dir. To connect a separate local web development server, pass --web-origin http://127.0.0.1:5175 (substitute its actual port). The packaged web and API use one origin. Studio startup requires its built index.html; supplying no Studio build does not prevent Monitor from running.

For a headless test, add --managed-stdin --managed-instance-id followed by a fresh UUID and keep the process's stdin pipe. Writing stop followed by a newline, or closing that owned pipe, requests shutdown. --no-browser suppresses automatic browser opening. Normal standalone CLI use remains available.

## Configuration and data

The default runtime root is LOCALAPPDATA/MonkeyHub; --runtime-root selects another absolute nonproject directory.

| Content | Owner and location |
| --- | --- |
| Language, theme, font scale and model defaults | Existing Studio settings owner; APPDATA/MonkeyArch/settings.json |
| Chosen Studio project/run, CAD export and service ports | Same settings owner; runtime-root/config/applications.json |
| Hub-owned child stdout/stderr | runtime-root/logs/ |
| Optional Studio usage diagnostics read by Monitor | runtime-root/diagnostics/monkeymonitor/ |
| Building data and retained runs | The selected project's existing ArchFlow project interfaces |

User preferences and application launch configuration have separate purposes and cannot overwrite one another. No credentials are stored in application configuration. Hub reads the existing saved model defaults when it starts Studio; changing a saved default does not replace an already running compiler.

No StudioSettings object is created for the Hub. Choose an existing complete project before starting MonkeyArch or MonkeyDiagram; the Studio health check must confirm that binding. Clearing the Hub's selected path does not delete a project. Stop the applications before changing their project or ports.

## HTTP contract

The actual schema is available at GET /openapi.json. Generate a client from this running schema and use a separate client instance for each service.

| Request | Result |
| --- | --- |
| GET /api/health | Hub service/version, actual process and parent process IDs, managed instance ID, source revision |
| GET /api/apps | AppStatus[] for the four fixed application cards |
| POST /api/apps/{app_id}/start | 202 with starting or current status; repeat requests reuse the owned process |
| POST /api/apps/{app_id}/stop | 202 with stopping or current status; stopping either Studio card stops their shared service |
| GET/PUT /api/settings/user | Existing UserSettingsDto and existing local preference routes |
| GET/PUT /api/settings/apps | projectDir, referenceRun, cadExport, studioPort, monitorPort |

Default ports are Hub 8790, Studio 8789 and Monitor 8788. Poll GET /api/apps while starting or stopping; open the returned URL only when state is running. States are stopped, starting, running, stopping, error and unavailable. An application error contains code and detail. Port conflicts, missing projects/builds and wrong service identity are explicit failures, not successful launches.

The source identity comes from root source-version.txt in a package, or the actual checkout HEAD. Missing or malformed identity cannot be presented as a verified launch. A responding child must match its launch UUID, source revision, service version and the owned PID (or its direct child's parent PID, for Windows Python venv redirectors). Stop always uses the owned stdin pipe rather than a reported PID.

Logs and process handles belong to this local run. This does not add a persistent task queue or restore in-flight jobs after a crash. Independent package and second-machine validation are handled by the installation workstream.
