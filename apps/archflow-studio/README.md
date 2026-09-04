# ArchFlow Studio

## 1. What it is

ArchFlow Studio is the product shell for ArchFlow. It is two programs:

- **`api/`** — a FastAPI **BFF** (`archflow_studio_api`). It validates requests, streams
  progress, owns the candidate job lifecycle, and shapes answers for the wire. Every design
  question — state, dependencies, impact, geometry, validation — is an `archflow` call. It
  re-derives no kernel answer, and a machine-enforced firewall keeps `rhino3dm`, `numpy`,
  `networkx`, `OCP`, `build123d`, `shapely`, `trimesh`, `scipy`, `tests`, `tools` and
  `probes` out of it.
- **`web/`** — a React 19 / three.js / rhino3dm-wasm browser client (Vite) that talks to
  that API through an **OpenAPI-generated SDK**. Nothing in `web/src` declares a DTO: the
  request and response shapes in `web/src/api/generated/` are the server's own schema,
  regenerated and diffed by `npm run api:check`.

  The shell is two surfaces and a drawer (design:
  `docs/claude-worktree/2026-09-03-p108-chat-shell-design.md`). A **conversation** column
  on the left: what you said, and what the server answered — a typed-proposal card, a
  question card (`BLOCKED_NEEDS_HUMAN`, the question verbatim, the accepted forms as quick
  replies), a refusal card (code and detail verbatim), a candidate card that follows its job,
  a review-readiness card (the server's word, the five clause chips, the three relation chips). The
  **stage** on the right: the 3DM viewer (three.js + rhino3dm, unchanged from the previous
  shell), a source chip (`RUN` / `CANDIDATE` / `LOCAL`), the last resolved pick, the camera
  tools, and a versions strip of every exported model the receipts certify. The **evidence
  drawer** (bottom right, or pinned as a third column): every honesty line, the identities,
  the candidate and validation receipts and the event stream, verbatim and one click away.
  The visual system is dark-first in the register of the Unreal Editor; the light theme is
  served from the same tokens; the fonts are Roboto and Roboto Mono from Google Fonts with
  system fallbacks.

The round-1 boundary, verbatim from the plan:

> **Round-1 boundary:** one server-configured project root (env `ARCHFLOW_STUDIO_PROJECT_DIR`
> or `--project-dir`; no default path in code); proposal-only; canonical write, live model
> providers, login identity disabled; missing data → `BLOCKED_NEEDS_HUMAN` with a concrete
> question.

Nothing in round 1 commits. The chain ends at a validation receipt and server review readiness; no
route issues anything, writes `canonical/` or `input/`, or calls `compare_and_swap`. The
only writes the API performs are `repository.create_run(...)` and `repository.put_json(...)`
into run areas of the bound project — which is what running a candidate is.

## 2. Run it

**One click.** The application is called **MonkeyArch**; the methodology and the protocol it
runs are still ArchFlow. `OPEN_MONKEYARCH.bat` — or the Desktop shortcut
`make-desktop-shortcut.ps1` writes, `打开 MonkeyArch.lnk` — starts both halves and opens the
browser at the web client. **There is no console.** The .bat starts Windows PowerShell hidden
(`powershell.exe` and not `pwsh`: a WinForms message loop needs an STA thread, and pwsh runs
MTA on Windows), and the shortcut is saved with window style 7 so the cmd window that hands
over is never painted. What you see instead is a splash window in the icon's three colours —
the wordmark, a monkey hammering at a wireframe box, and a progress line that follows the real
steps: validating runtime.json, python and fastapi, web dependencies, starting the API,
`/api/health`, starting the web client, its first answer, opening the browser. **A refusal turns
that same window red**, with the launcher's own sentence in it and a Close button; nothing waits
in a console for a keypress.

Its one input is `runtime.json` beside it, and the line you normally change is the first:
`project_dir`, the P036 project the API binds. The rest are `reference_run`, `rhino_export`,
`powershell`, `intent_provider` and `codex` (the environment variables the sections below
describe), `python` (the interpreter command, `py -3.12`), `api_port`, `web_port` and
`open_browser`. Two optional keys name the agent more exactly: `intent_model` (the model the
provider runs, forwarded as `ARCHFLOW_STUDIO_INTENT_MODEL`) and `intent_timeout_s` (how long one
compile may take, default 120, forwarded as `ARCHFLOW_STUDIO_INTENT_TIMEOUT_S`). **The paths in
it are absolute and machine-specific**; it is not a file to copy between machines unchanged.
The launcher validates all of it before it starts anything, so a `project_dir` with no
`project.json` in it, a port already held, or a Python that cannot import FastAPI is a refusal
naming the reason — never a half-started pair.

**Quitting.** Once the browser is open the splash goes and a **tray icon** stays: "Open
MonkeyArch" reopens the tab, "Show logs" opens `.runtime/`, and **"Quit MonkeyArch" stops the
API and the web client together** and exits the launcher; double-clicking the icon opens the
app. Windows 11 hides a tray icon nobody has pinned yet — it is under the notification area's
chevron until you drag it out. If one of the two servers dies on its own, the launcher says so
in a balloon tip, shows the tail of that server's log in the same red window, stops the other
server and exits. Each start writes `api-<stamp>.out.log`, `api-<stamp>.err.log` and the same
pair for the web client into `apps/archflow-studio/.runtime/` (git-ignored); that is where a
server which would not start says why, and the launcher quotes the tail of it in the window
rather than making you go looking. `launch-studio.ps1` takes `-NoBrowser`,
`-RuntimeConfig <path>` and `-HideConsole` (what the .bat passes), so a second project can be
launched without editing the one beside it, and running the script by hand from a console gives
you the console output as well as the splash. Windows PowerShell 5.1 is the floor: WinForms
only, no WPF, no extra runtime.

The icon on the shortcut, on the splash window and in the tray is `assets/monkeyarch.ico` —
a monkey hanging by one arm from the amber keystone of a limestone arch, ink tile, drawn at
16, 24, 32, 48, 64, 128 and 256 px by `assets/make_icon.py` (Pillow), each size from its own
spec rather than downscaled from one image. It is the animal that is spent down as the tile
shrinks, not the arch: below 48 px the eyes, the free hand and one leg go, below 32 px the
arm and the swing go and the figure hangs straight under the keystone, and at 16 px the tail
goes too. `assets/monkeyarch-icon-512.png` is the same drawing at 512 px, for anywhere an
`.ico` will not do. Redraw both with
`py -3.12 apps/archflow-studio/assets/make_icon.py`; the shortcut points at the `.ico` by
absolute path, so an existing shortcut picks up a redraw without being rewritten, but a
shortcut written before the MonkeyArch icon arrived names the retired `archflow.ico` and has
to be written again with `make-desktop-shortcut.ps1`. The loading
animation is four frames in `assets/loading/` (`frame-01.png` … `frame-04.png`), cycled at 8 fps
by the splash window and, in the browser, by the same overlay while the client waits for the API
and while the viewport parses exports. `assets/loading/make_frames.py` draws the four
frames with the icon's own monkey (it imports the figure from `make_icon.py`, so the icon and the
animation cannot drift apart), and `web/scripts/sync-loading.mjs`
copies them into the served public directory at `npm run dev` and `npm run build` — the same way
the rhino3dm runtime is synced, so `assets/loading/` stays the one source.

**Install** (from the repo root):

```powershell
py -3.12 -m pip install -r apps/archflow-studio/api/requirements.txt
py -3.12 -m pip install httpx2        # tests only, for fastapi.testclient
```

**The API** — from `apps/archflow-studio/api`:

```powershell
$env:ARCHFLOW_STUDIO_PROJECT_DIR = "<a P036 project directory>"
$env:ARCHFLOW_STUDIO_REFERENCE_RUN = "runner-002"   # optional; see below
py -3.12 -m archflow_studio_api.main                # 127.0.0.1:8000
```

`main` takes `--host`, `--port` and `--project-dir` (which overrides the env var). There is
no default project root anywhere in the code: an unset `ARCHFLOW_STUDIO_PROJECT_DIR` is a
startup refusal, because an API that guessed a root could bind — and then write a run into —
a project nobody chose.

`ARCHFLOW_STUDIO_REFERENCE_RUN` names which run the projection answers for. Unset, the rule
chooses: the newest **complete, non-harness** runner receipt in the project. A project with
no such run binds to the run id `studio-projection`, which claims nothing. A request may
override both with `GET /api/state?run=<runId>`, and a run named explicitly must exist.

**Local mode and remote mode.** The API serves a named, versioned boundary — the open ArchFlow
protocol (`docs/PROTOCOL.md`), stated at `GET /api/protocol`. Which side of it this process is
on is `ARCHFLOW_STUDIO_MODE`:

| variable | default | what it does |
| --- | --- | --- |
| `ARCHFLOW_STUDIO_MODE` | `local` | `local` is the pair the launcher starts here: one user, no token, no CORS — nothing about it changes. `remote` is the same API reached over a network. |
| `ARCHFLOW_STUDIO_BIND` | `127.0.0.1` | the interface the listener binds. `--host` overrides it; left out, these settings decide. |
| `ARCHFLOW_STUDIO_TOKEN` | *(unset)* | the bearer token remote mode requires. Never read in local mode. |
| `ARCHFLOW_STUDIO_ORIGINS` | *(unset)* | comma-separated list of the origins a browser client is served from; the CORS policy remote mode adds. |

In `remote` mode the process **refuses to start** without either of the last two — a remote
server with no token would answer anyone who found the port, and one that guessed which sites
may call it would be guessing about a browser's security. `StudioSettings` will not construct
such a process at all. With both, every `/api` route except `GET /api/health`
and `GET /api/protocol` requires `Authorization: Bearer <token>` and answers
`401 UNAUTHENTICATED` without it, unknown paths included, so an anonymous caller learns nothing
about which paths exist. Local mode is unchanged in every respect.

The web client picks its server up the same way: `VITE_ARCHFLOW_API_URL` at build time (else the
origin it was served from), an optional `?token=` on the first load which it reads once and
removes from the address bar, and `GET /api/protocol` before anything else — a server whose
protocol major is not 2 gets a refusal screen. `archflow/2` replaces the validation and event
field `advance` with `reviewReady`; client and server must both speak this major before the
client reads design responses.

Exported candidates (optional, slow — roughly 37 s per seat, and it drives Rhino):

```powershell
$env:ARCHFLOW_STUDIO_RHINO_EXPORT = "1"
$env:ARCHFLOW_STUDIO_POWERSHELL = "<path to powershell.exe>"
```

Running a candidate **writes a run into the bound project**. Bind the API to a temporary copy
of the inputs whenever you are exercising the candidate step; never point it at a real
project you are not prepared to have a new run directory in.

**The web client** — from `apps/archflow-studio/web`:

```powershell
npm install
npm --prefix tools/openapi-ts install   # isolated generator, pinned to TypeScript 5
npm run dev                             # http://127.0.0.1:5174, /api proxied to :8000
```

The other scripts:

| script | what it does |
| --- | --- |
| `npm run api:dump` | writes `.generated/openapi.json` from `create_app(...).openapi()` — the app itself, in process; it touches no filesystem and binds no project, so no server need be running |
| `npm run api:generate` | dumps, then regenerates `src/api/generated/` from that schema |
| `npm run api:check` | regenerates into a temp directory and diffs; exit 1 on drift |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run sync` | copies the rhino3dm runtime and the four loading frames into `.generated/public/`; `dev` and `build` run it first |
| `npm run build` | syncs those assets, typechecks, then `vite build` |

`@hey-api/openapi-ts` crashes under TypeScript 7, so the generator lives in
`web/tools/openapi-ts/` with its own `package.json`, its own `node_modules` and its own
TypeScript 5. The app itself is built with TypeScript 7. There is **no committed OpenAPI
snapshot**: the one description of this API is the FastAPI app, and `api:check` is what keeps
the committed client honest to it.

### Language and settings

The toolbar's **Settings** dialog has six operator-facing categories. Language, theme,
interface size and event-stream visibility are browser preferences and are the only editable
values in this browser client. They are stored as one versioned value in `localStorage`; a
`?lang=en` or `?lang=zh-CN` query overrides the stored language at startup and is then
remembered. Project, model,
geometry, server and diagnostic rows are read-only facts from `GET /api/project` or
`GET /api/protocol`; a value those routes do not expose is labelled as unavailable rather
than inferred from `runtime.json` or the host machine.

Client-owned labels come from complete typed `en` and `zh-CN` catalogs. For caller-approved
English prose that arrives at runtime, the client may use Chrome's on-device Translator API
as a progressive enhancement. The English source and Chinese translation occupy the same
layout cell: changing language hides the inactive layer but does not replace the source in
React state or the DOM. Unsupported browsers, unavailable language packs and translation
failures fall back to the English source. Codes, paths, hashes, identifiers, protocol strings
and accepted command forms remain verbatim, and translations are display-only: they never
enter an API request, proposal, receipt or project artifact.

## 3. The API

Every route is under `/api`. Every error, without exception, is the one body
`{"code": "<CODE>", "detail": "<text>"}` — plus `question` and, when non-empty,
`acceptedForms` for `BLOCKED_NEEDS_HUMAN`. That includes unknown routes (404 `NOT_FOUND`),
wrong methods (405 `METHOD_NOT_ALLOWED`), request-validation failures (422 `REQUEST_INVALID`)
and unexpected bugs (500 `INTERNAL_ERROR`, with no traceback on the wire). Anything else the
framework itself refuses before a route runs — a malformed `Range` header, say — keeps its own
status under the code `HTTP_ERROR`; no route code can reach it, because route code raises
`StudioError` and that has its own handler. No DTO carries a
`schema` tag, and no DTO carries a constant-false flag such as `readOnly` or
`canonicalWriteAuthority`.

One policy about what a `detail` may say: **it never carries the server's project directory.**
An error is read by whoever ran into it, and where this process keeps the project on disk is no
part of an answer about a design — an `OSError` is named by its class and the system's own
message rather than by the path it came with. `projectDir` on `GET /api/project` stays, because
that is an operator asking the binding question and being answered.

Three groups of refusal are **shared**, and the table below does not repeat them per row:

- the framework's four above (404 `NOT_FOUND`, 405 `METHOD_NOT_ALLOWED`, 422 `REQUEST_INVALID`,
  500 `INTERNAL_ERROR`);
- **binding**: 503 `PROJECT_NOT_BOUND` from every route that opens the project — all of them
  except `GET /api/health`, `GET /api/proposals/{id}`, `GET /api/jobs/{id}` and
  `GET /api/events`, which answer from this process's own memory and never open the project;
- **projection**: 404 `RUN_NOT_FOUND`, 404 `STATE_RECORD_NOT_FOUND` and 422
  `STATE_RECORD_INVALID` from every route that reads the selected record — `/api/project`
  (`RUN_NOT_FOUND` only), `/api/state`, `/api/pick/resolve`, `/api/intents`, both
  `/api/proposals` POSTs and both `/api/candidates` reads;
- **action base**: 409 `REFERENCE_STATE_NOT_EXACT`, 409 `REFERENCE_BASE_STALE` or 409
  `REFERENCE_STATE_MISMATCH` from an intent, proposal, option, program or candidate request
  that cannot stand on the reference run's exact retained State Record. The same run remains
  readable for inspection;
- **proposal**: 404 `PROPOSAL_NOT_FOUND` from every route that reads a proposal back —
  `GET /api/proposals/{id}`, starting a candidate, and reading a candidate or its validation.

| method | path | response DTO | errors beyond the shared ones |
| --- | --- | --- | --- |
| GET | `/api/health` | `StudioHealth` | none — `projectBound` is a boolean, not a refusal |
| GET | `/api/protocol` | `ServerIdentityDto` | none — it opens no project, so a foreign server and an unbound one are different answers |
| GET | `/api/projects` | `ProjectListDto` | — |
| GET | `/api/projects/{projectId}` | `ProjectBindingDto` — the general form | 404 `PROJECT_NOT_FOUND` |
| GET | `/api/project` | `ProjectBindingDto` — the default-project shortcut | — |
| GET | `/api/state?run=` | `StateProjectionDto` | — |
| GET | `/api/artifacts` | `ArtifactListDto` | — (a run it cannot read is named in `skippedRuns`, never a refusal) |
| GET | `/api/artifacts/{sha256}/bytes` | binary (`ETag`, RFC 6266 `Content-Disposition`, `Cache-Control: no-store`) | 404 `ARTIFACT_NOT_FOUND`, 409 `ARTIFACT_UNREADABLE`, 409 `ARTIFACT_DIGEST_MISMATCH` |
| POST | `/api/captures` → 201 | `ViewportCaptureDto` (body `ViewportCaptureRequestDto`: `runId`, `pngBase64`) | 404 `RUN_NOT_FOUND`, 422 `CAPTURE_INVALID`, 409 `CAPTURE_WRITE_FAILED` |
| POST | `/api/pick/resolve` | `PickResolutionDto` (body `PickRequestDto`) | 409 `STALE_BASE` |
| POST | `/api/intents` → 201 | `IntentDto` = `agent` (`AgentReadingDto`) + `proposal` (`ProposalDto`) (body `IntentRequestDto`) | 422 `BLOCKED_NEEDS_HUMAN` (the agent's question, or the grammar's with `acceptedForms`), 502 `INTENT_AGENT_FAILED`, 409 `STALE_BASE`, 403 `PROJECT_MISMATCH` |
| POST | `/api/proposals` → 201 | `ProposalDto` (body `ProposalRequestDto`) | 422 `BLOCKED_NEEDS_HUMAN` (+ `question`, `acceptedForms`), 409 `STALE_BASE`, 403 `PROJECT_MISMATCH` |
| GET | `/api/proposals/{id}` | `ProposalDto` | — |
| POST | `/api/proposals/{id}/candidate` → 202 | `CandidateAcceptedDto` | 409 `PROPOSAL_NOT_RUNNABLE`, 409 `STALE_BASE`, 409 `CANDIDATE_ID_COLLISION` |
| GET | `/api/jobs/{id}` | `JobDto` | 404 `JOB_NOT_FOUND` |
| GET | `/api/candidates/{id}` | `CandidateDto` | 404 `CANDIDATE_NOT_FOUND` — this process ran no such candidate, its run is still queued or running, or its job failed; the detail says which, and a failed job carries the reason |
| GET | `/api/candidates/{id}/validation` | `ValidationDto` | 409 `CANDIDATE_NOT_FINISHED`, 404 `CANDIDATE_NOT_FOUND` |
| GET | `/api/events?limit=N` | SSE of `StudioEventDto` | — |

`GET /api/events` without `limit` holds the connection open and carries live events; a client
resumes with `Last-Event-ID: <seq>`. With `limit` it is a catch-up read: at most that many
events, replay included, closing as soon as the backlog drains — so a bounded read always
terminates, even on a quiet process.

`GET /api/health` is an operator's question — is the process up, does its binding open. The
browser does not ask it: its own first question is stronger, and `GET /api/project` plus
`GET /api/state` either return the binding or fail with a code the top bar renders.

## 4. The slice

One chain, end to end, and every link is the kernel's answer shaped for the wire.

**Published design ↔ record.** The project is opened with `open_located_project()` /
`FilesystemProjectRepository`. When a reference run exists, its runner receipt must name a
content-addressed `state_record_ref` below that run's records. The server verifies the P036
reference, the record's project/run/base identity, `state_record_digest`, and the receipt's
`design_state_digest`; it never rebinds mutable `input/runner/state-record.json` onto that run.
Only a project with no eligible reference run starts from that authored work in progress.
Legacy or damaged references and runs based on an older HEAD remain inspectable, but every
route that would create design work refuses them with a named 409 action-base error.

**Projection.** `GET /api/state` returns the component tree (`design_components_of`), the
elements with their `params.<key>` scalars, the declared parameters with their locks, the
kernel's dependency edges (`StateRecord.dependency_edges()`), and `honesty[]` — the lines
that say what this record does **not** answer. A record whose tree will not build reports
`componentTreeError` and still serves its entities; the tree is a view of those ids, and
failing to arrange them is not a claim that they are absent. Such a record has no bound view
either, so `stateDigest` and `activePhase` come back `null` — nothing produced a number a
receipt could be compared against — and the kernel's sentence is repeated in `honesty[]`. Every
other route stands *on* that view, so a pick, a proposal, an impact or a candidate against such
a record refuses with `422 STATE_RECORD_INVALID` carrying the same sentence rather than
answering from a tree it does not have. A record that parses and then cannot be read at all —
an `Element@1` with no `component_id`, bytes in another encoding — is the same refusal: the
authored file is what is wrong, and no such record is ever answered with a 500.

**Artifacts.** `GET /api/artifacts` lists what the `seat-rhino-execution` receipts certify,
each row keeping the receipt's own `available` / `unavailableReason` rather than being
filtered out. Bytes are content-addressed: `GET /api/artifacts/{sha256}/bytes` re-hashes the
file on disk and refuses with `ARTIFACT_DIGEST_MISMATCH` if it does not hash to the digest in
the path. A `.3dm` in this list is a file that was written — never a claim that anything about
it passed.

**Viewport captures.** `POST /api/captures` accepts the loaded model's existing `runId` and
PNG bytes, then P036 retains the image at
`runs/<runId>/workspaces/studio-captures/viewport-<sha256>.png`. The response exposes only
that project-relative path. A capture is inspection output, not a receipt-certified model
artifact, and saving one neither adds it to `GET /api/artifacts` nor changes `HEAD`.

**Pick.** A click sends the object's `archflow:*` user strings, the document's own strings,
and the object name. The server answers **resolved**, **unbound** (this project never produced
that object — a fact, not an error) or **unknown_component** (it claims an identity this
record cannot honour). An element is only ever resolved *within the component the object
claims*: an object naming one component and matching an element of another is two claims that
disagree, and the component answers alone. Separately and without enforcing anything, the
answer reports `sourceState` — `current`, `stale` or `unknown` — because opening last week's
export to look at it is legitimate; the base is enforced where a change is proposed. For bytes
this API served, the document-level strings sent with a pick come from the receipt that
certified those bytes when the file itself carries none: the server re-hashes the file before
serving it, so the receipt's claim is a claim about the document on screen and not about one
that used to be there. A durable server-side lookup by sha256 — so that a file opened from
anywhere could be identified the same way — is carded, not built.

**Intent, compiled.** `POST /api/intents` takes the architect's sentence in any words —
"make the west portico a little taller" — and hands it to the process's **intent compiler**,
chosen by `ARCHFLOW_STUDIO_INTENT_PROVIDER`: `deterministic` (the default: the sentence is
taken as already in the grammar), `codex` (a local `codex exec` subprocess — ephemeral,
read-only sandbox, schema-bound answer, the user's own login; `ARCHFLOW_STUDIO_CODEX` names
the executable, `ARCHFLOW_STUDIO_INTENT_MODEL` a model) or `anthropic` (the Messages API;
the key is the SDK's to read from `ANTHROPIC_API_KEY` and this process never holds it;
`ARCHFLOW_STUDIO_INTENT_MODEL` defaults to `claude-sonnet-5`). `ARCHFLOW_STUDIO_INTENT_TIMEOUT_S`
bounds either (120 s). The agent is shown one thing, the **record sheet** — the components,
elements, numeric fields, parameters and honesty lines the projection already answers with,
plus the grammar — and answers with one JSON object: a compiled sentence against one element
the sheet names, or a question. It never sees the file system, runs nothing, and produces no
coordinate. Its sentence then goes through the grammar below exactly as a typed one would, so
the proposal that comes back is the record's; a sentence the grammar cannot type, or an agent
that asked instead, is the same `422 BLOCKED_NEEDS_HUMAN`, with the agent's reading kept in
`detail` so the reader knows who said what. The answer carries `agent` — provider, model, the
compiled sentence, `why`, latency, the prompt's sha256 — beside `proposal`, so nothing the model
said can be mistaken for something the record answered. An agent that fails is
`502 INTENT_AGENT_FAILED` carrying its own last words.

**Intent, typed.** A sentence in the grammar is parsed, not interpreted, against four exact
forms:

```
set <field> to <number>[ <unit>]
set <field> = <number>[ <unit>]
increase <field> by <number> %
decrease <field> by <number> %
```

Any of them may end with `keep <ref>[, <ref>…]` to name what the change must not disturb. The
*field* is never read out of prose: it is resolved against the selection the request carried
(`targetComponentId`, optional `elementId`) and only against a scalar number the record
actually declares. The optional *unit* is checked and never converted: on a parameter it must
be the unit the record declares, and an element's `params.<key>` declares none at all — the
record holds those numbers bare — so `set height to 2200 mm` on an element is a question about
which number was meant, not a silently dropped word that would put 2200 into a field holding
0.6. Anything outside the grammar — and anything the record cannot answer — becomes
`422 BLOCKED_NEEDS_HUMAN` with a concrete question and the accepted forms, never a guess about
a building.

**Proposal.** What comes back is a typed, exact-base `DecisionOperator` under the
proposal-only authority `studio:proposal-only`. It is never applied.

**Impact.** `record.closure` is the one propagation rule in this system; the API calls it and
arranges the answer. There is **one definition of conflict**: the whole closure intersected
with the protected refs, the target included — protecting the very thing you are changing is a
conflict in exactly the sense protecting something downstream is. And the honest half travels
with it: components that appear in no dependency edge are reported as *unknown*, because an
empty propagation from a record nobody wired means "this record was never asked", not "nothing
else is affected".

**Candidate.** `POST /api/proposals/{id}/candidate` runs `run_project` under the
`studio-candidate-harness` workflow on a background worker, and reports progress on
`/api/events`. It is **a harness run, not a project stage advance**: run ids are unique per
candidate, the wire carries the harness statement verbatim, and the stale base is re-checked
where the record is read. The runner's own refusals arrive as a *failed job carrying the
runner's sentence*, printed verbatim rather than flattened into a status word — and reading
that candidate back is `404 CANDIDATE_NOT_FOUND` pointing at the job, because a candidate that
failed at the seat pack or the base check never created a run at all.

**Validation.** `GET /api/candidates/{id}/validation` calls the kernel's `validate_submission`
over `CanonicalState(ref=head)` with three production validators — `artifact-present`,
`obligation-discharge`, `authorized-commitment-claims` — and reports the receipt's findings
and its `passed` unedited. Beside it stands the server's **review readiness**, a fixed
conjunction of five named clauses:

```
reviewReady  ⟺  validation.receipt
             ∧  runner.seat_execution_complete
             ∧  relations.held
             ∧  relations.fully_checked
             ∧  runner.exports_available
```

The fifth clause reads two records, not one: the run receipt's own seat rows and the artifact
records this run retained. The runner fills a seat row's `cad` block only when it was asked to
export, so that block is the evidence an export happened at all — every seat carrying one must
have succeeded *and* be matched by an `available`, `succeeded` artifact found by the very
`execution_ref` the runner wrote there, and every artifact the candidate carries must be
available and succeeded. A seat that exported and left no artifact record blocks review readiness
and says so in `honesty[]`. The clause is vacuous only for a candidate no seat of which
attempted an export — which is now something the receipt states, rather than something an empty
artifact list was taken to mean.

`blockedBy[]` names every clause that refused. The fourth clause is the point of the other
three: `held` is true whenever nothing was **violated**, including when nothing was
**checked**, so a candidate whose relations nobody could check is never green. Review readiness is
memoised per (candidate, issue) — a client polling the readout must not appear on the event
stream as a server deciding over and over, and a new issue is a different question that gets
a fresh answer and a fresh event.

And `effectiveChecks` is narrower than `validators` on purpose: a P036 published state is a ref-based
`CanonicalProjectState@1` carrying no facts, commitments or obligations, so two of the three
validators run over an empty state and find nothing to object to. That is not the same as
passing them, and the payload says so (kernel card **P110**).

## 5. Three digests, and which to compare

| name | what it identifies | compare it for |
| --- | --- | --- |
| `stateDigest` | the selected record **bound to a run** — run- and base-scoped | "is this the state the client was just given?" Picks, proposals and candidates are all checked against it, and a mismatch is `409 STALE_BASE`. It is also the number a runner receipt cites, which is why `matchesReferenceReceipt` can compare the projection against the reference run's own `designStateDigest`. It is `null` when the kernel would not build the bound view (§4), and then there is nothing to compare. |
| `recordDigest` | the selected record's **content**, invariant under binding | "is this the same record?" — the same content bound to two runs has two `stateDigest`s and one `recordDigest`. |
| `published.stateSha256` | the project's published version | "has the project issued since?" It is half of the validation memo key, with `published.version`. |

A client that confused the first two would compare a project against itself. They are named
apart for that reason, and neither is ever abbreviated for anything but display: the shell
shortens digests to eight characters to read them, and always sends back the full value it
was given.

## 6. What the browser may not do

**Three-state law**, verbatim:

> held / violated / unchecked are distinct; unchecked is never green; the server reports
> candidate review readiness.

The chips follow it literally: held is green only when `held > 0 && violated == 0 &&
unchecked == 0`; violated is red; unchecked is amber. The review-ready badge is the server's
boolean and nothing else. It does not issue the run or advance the stage; only `project.issue`
has that authority.

**Browser law**, verbatim:

> never import archflow; never infer impact client-side; never declare "validation passed"
> client-side; chat is not version history; never write the project directory, never issue; a
> `.3dm` never implies success.

Three consequences worth stating outright. Every gateway error renders as a **visible card**
carrying the server's `code` and `detail` (and `question` / `acceptedForms`) in the transcript
— never an empty list, because an empty list is the one way a browser can turn a server's
refusal into a silent, confident-looking answer. The transcript is a view of this tab, not
version history: it is dropped on reload, and the event stream in the drawer is a bounded live
view of a running process. And the evidence drawer is where a sentence goes when the
conversation does not quote it: verbatim, counted on its tab, one click away — a ruling of
2026-09-03 that folding is not hiding, so long as nothing is summarised. The one error the
shell *handles* rather than merely displays is `STALE_BASE` — it re-projects and says "the
project moved under you" in the transcript.

## 7. Cards this slice stands on

- **P110** — CanonicalState projection. Why `effectiveChecks` is narrower than `validators`
  (section 4).
- Kernel-card candidates relayed to Kaiwen from this slice, fixed nowhere in passing: typed
  `ParameterBinding` values; `runner-run-receipt` not carrying its own `receipt_ref` (the
  record it is in is the only thing a caller can name it with); non-ASCII path segments
  rejected by `require_project_relative_path`.
- **Preview Slice 01 is retired.** The stdlib gateway, the presence-probe kernel, the
  monolithic shell and the old launcher were deleted in Task 9 and are retained at the tag
  `studio-preview-slice-01`. P108 plans an archive note at
  `docs/mapping/archive/studio-preview-slice-01.md`; until it is written, the tag is the
  record.

## 8. Verify

From the repo root:

```powershell
py -3.12 tools/archcheck.py
py -3.12 -m unittest discover -s apps/archflow-studio/api/tests -t apps/archflow-studio/api -v
py -3.12 -m unittest discover -s tests
```

From `apps/archflow-studio/web`:

```powershell
npm test
npm run api:check
npm run typecheck
npm run build
```

`archcheck` must print `ARCHITECTURE PASS` after every Python change; it is the firewall, not
a lint. The API tests run on real fixtures — a real P036 project via
`FilesystemProjectRepository.initialize` — and there are no mocks in them.

The web shell has focused unit tests for restoring the original model after display projections,
semantic carrier matching, and viewport PNG encoding. Its end-to-end acceptance remains a live
smoke against a **temporary copy** of a project with export on: bind → choose or pick a component → propose → run the candidate →
preview its export under the `CANDIDATE` chip → read the review-readiness card → send an abstract
sentence and get a question card → open the evidence drawer; then the light theme and the
900 px fold.
