# Project Runtime

MonkeyHub is the application. This directory retains the internal Python package
`archflow_studio_api` and shared artwork; its historical name is not a second product.
The [Project Runtime contract](../../docs/PROJECT_RUNTIME.md) defines project binding,
process identity, lifecycle and API forwarding.

Board and Arch render directly inside the Hub frontend. Diagram is the page editor
opened from Board. Their source is in `../monkeyhub/web/workspaces/src/`; there is no
Studio web server, standalone browser shell, iframe boundary or second frontend build.
Application settings, navigation and language catalogs belong to Hub.

## 1. Runtime responsibilities

The FastAPI adapter validates requests, streams progress and owns candidate jobs.
State, geometry, validation and project writes continue through the registered
`archflow`, `monkeyarch`, `monkeydiagram` and P036 owners. The runtime binds exactly
one explicit project; separate open projects use separate Hub-managed processes.

Viewing a candidate does not change the editing base. Continuing a candidate,
endorsing a direction and formally issuing a version remain separate actions.
The workspace retains local view state and drafts; it does not own canonical project data.

## 2. Development and configuration

Normal use starts through MonkeyHub. For API tests and isolated development:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/run-project-runtime.ps1 -ProjectDir '<a P036 project directory>'
```

`-Port` selects the API port and `-Python` selects the interpreter. There is no HTML
hosting option or default project. Install this directory's API requirements and
root `.[cad-occt]` in the chosen Python environment for normal geometry export.

The runtime reads `ARCHFLOW_STUDIO_PROJECT_DIR`, `ARCHFLOW_STUDIO_CAD_EXPORT`,
`ARCHFLOW_STUDIO_REFERENCE_RUN` and intent configuration from its launching environment.
Hub resolves user/application preferences before starting it. Explicit `--project-dir`
overrides the project environment variable. An explicitly requested run must exist;
an unset reference run uses the newest complete non-harness runner receipt.

Local mode binds loopback. Remote mode still requires `ARCHFLOW_STUDIO_TOKEN` and
explicit `ARCHFLOW_STUDIO_ORIGINS`; API callers send the bearer token. Health and
protocol are public, and the remaining API routes require authentication. Hub workspace
clients use the same-origin project forwarding path, with their own protocol identity,
SDK client and mutation idempotency keys; they do not read tokens from browser URLs.

Build or develop the single frontend from `apps/monkeyhub/web`:

```powershell
npm ci
npm ci --prefix workspaces/tools/openapi-ts
npm run api:check
npm test
npm run build
npm run dev
```

The development frontend forwards `/api` to `MONKEYHUB_API_URL` (default port 8790).
Workspace browser regressions may instead mount the test-only fixture with
`npx vite --config workspaces/test/vite.config.ts --port 5174`, targeting an explicit
`ARCHFLOW_STUDIO_API_URL`. That fixture is not included in production builds.

## 3. The API

### Continue a candidate

Show a candidate in the versions strip, then choose **Continue from this version**
beside the model source. **Next edit starts from** names the run used by the
component picker, frame, intent, proposal and candidate worker. A second edit
starts with the first candidate's changes, without rewriting `input/` or HEAD.
The draft and conversation remain; proposals from another base cannot be applied
or refined until that base is selected again. **Return to default editing base**
reloads the default projection and its model. A failed switch leaves the previous
base in place and shows the error.

The client reads `GET /api/state?run=<runId>`, then sends that run as the optional
`sourceRunId` on `POST /api/intents` or `/api/proposals`, alongside its `stateDigest`.
The proposal retains this source through modifications, queueing and execution;
the worker rechecks both identities and the canonical base. Omitting the field
keeps the existing default-reference policy, including skipping harness runs.
Frame/volumes reads accept `?run=`, and pick/closure accept `sourceRunId`.

This continuation slice covers scalar edits. Program-sheet editing and generating
massing options still use the default base; those actions are unavailable while
continuing a candidate. Candidate massing remains viewable. `PROJECT.md` aliases
and compass still require the explicitly declared state digest to match; this
action does not rebind them or infer missing controls.

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

**Artifacts.** `GET /api/artifacts` lists what the export receipts certify — the
`seat-occt-execution` receipts of the ordinary in-process export and the `seat-rhino-execution`
receipts of the Rhino export — each row keeping the receipt's own `available` /
`unavailableReason` rather than being filtered out. One OCCT receipt is **two rows** sharing its
`receiptRef`, stage and program binding: the exact STEP and the mesh preview of the same
model. Every row says what it is in two words: `format` (`step` or `3dm`, what a reader must
know to open it) and `representation` (`exact`, the delivered geometry — a STEP B-rep or a
Rhino export that was read back — or `preview`, a render mesh for looking at, never a
NURBS/B-rep delivery). The browser hands only a `3dm` to its viewer and offers the STEP as a
download; a version is one card per run, never one per file. Bytes are content-addressed:
`GET /api/artifacts/{sha256}/bytes` re-hashes the file on disk and refuses with
`ARTIFACT_DIGEST_MISMATCH` if it does not hash to the digest in the path. A file in this list
is a file that was written — never a claim that anything about it passed.

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
- Kernel-card candidates relayed to the repository owner from this slice, fixed nowhere in passing: typed
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

From `apps/monkeyhub/web`:

```powershell
npm test
npm run api:check
npm run typecheck
npm run build
```

`archcheck` must print `ARCHITECTURE PASS` after every Python change; it is the firewall, not
a lint. The API tests run on real fixtures — a real P036 project via
`FilesystemProjectRepository.initialize` — and there are no mocks in them.

The Hub workspaces have focused unit tests for restoring the original model after display projections,
semantic carrier matching, viewport PNG encoding, and which listed artifact the viewer is handed
(the `3dm` preview, never the exact STEP). Its end-to-end acceptance remains a live
smoke against a **temporary copy** of a project with export on: bind → choose or pick a component → propose → run the candidate →
preview its export under the `CANDIDATE` chip → read the review-readiness card → send an abstract
sentence and get a question card → open the evidence drawer; then the light theme and the
900 px fold.

Optional browser smoke scripts (`../monkeyhub/web/workspaces/test/*.browser.mjs`) use an installed `playwright` package, or a filesystem module path supplied through `PLAYWRIGHT_MODULE`. `documentCanvas.browser.mjs` also requires `DOCUMENT_FIXTURES` pointing to its disposable PDF/image fixtures; these scripts use an explicit Project Runtime and the test-only workspace fixture and are separate from `npm test`.

## Local Runtime and shared project collaboration

One shared service retains the team's design branches. Each architect runs a separate local
Runtime, with the same P036 project identity under its own project root, and generates
geometry locally. Uploading a candidate leaves the shared branch unchanged. Explicit
acceptance uses the existing shared validation and branch CAS; the next pull gives another
Runtime the accepted Stage and its model bytes for continued editing.

Use the existing packaged Python and API entry point. No Vite server is required. Each
project directory's final component must equal its `project_id`; separate roots can be
`workspace/shared/projects/demo-project` and `workspace/alice/projects/demo-project`.

For the shared process, configure:

```powershell
$env:ARCHFLOW_STUDIO_MODE = 'remote'
$env:ARCHFLOW_STUDIO_SERVICE_ROLE = 'shared_project'
$env:ARCHFLOW_STUDIO_ACTORS_FILE = 'E:\YourRuntime\config\actors.json'
$env:ARCHFLOW_STUDIO_ORIGINS = 'http://127.0.0.1:8891'
$env:ARCHFLOW_STUDIO_CAD_EXPORT = 'off'
python -m archflow_studio_api.main --project-dir E:\YourRuntime\workspace\shared\projects\demo-project --host 127.0.0.1 --port 8890
```

The external actor file names unique credentials and explicit actions. Replace the example
token values in your own configuration; keep this file outside project folders and packages.

```json
{"actors":[
  {"actor_id":"alice","token":"REPLACE_ALICE_TOKEN","projects":{"demo-project":["read","propose","accept"]}},
  {"actor_id":"reviewer","token":"REPLACE_REVIEWER_TOKEN","projects":{"demo-project":["read","accept"]}}
]}
```

In a separate shell for Alice's local Runtime, configure:

```powershell
$env:ARCHFLOW_STUDIO_MODE = 'local'
$env:ARCHFLOW_STUDIO_SERVICE_ROLE = 'runtime'
$env:ARCHFLOW_STUDIO_SYNC_URL = 'http://127.0.0.1:8890'
$env:ARCHFLOW_STUDIO_SYNC_PROJECT_ID = 'demo-project'
$env:ARCHFLOW_STUDIO_SYNC_TOKEN = 'REPLACE_ALICE_TOKEN'
$env:ARCHFLOW_STUDIO_CAD_EXPORT = 'occt'
python -m archflow_studio_api.main --project-dir E:\YourRuntime\workspace\alice\projects\demo-project --host 127.0.0.1 --port 8891
```

Initialize the shared project's first Stage with the existing model-source endpoint. Then
`POST http://127.0.0.1:8891/api/sync/pull` installs a same-identity local copy, including
necessary retained artifacts. The usual proposal/candidate endpoints compute locally;
`POST /api/sync/push?candidateId=...` submits without accepting. The existing accept and
branch endpoints on this connected Runtime delegate to the shared service and pull the
result. A reviewer can accept an already uploaded candidate without a propose grant.

An outdated candidate returns a branch conflict and stays available locally. Pull the new
Stage and rerun the intended revision explicitly. Repeating a completed accept returns the
same Stage; repeating a pull reuses identical bytes. A changed published HEAD requires a
separate published-version synchronization path, which this slice explicitly refuses.

Each connected Runtime uses one actor and listens only on loopback; a shared service can
authenticate multiple actors. The first shared project must already exist. The two-client
test uses disposable synthetic inputs and actual OCCT models:

```powershell
python -m pytest apps/archflow-studio/api/tests/test_collaboration_http.py -q
```

`ARCHFLOW_COLLABORATION_PYTHON` and `ARCHFLOW_COLLABORATION_SOURCE_ROOT` can point that test
at an unpacked candidate to verify its isolated processes. This same-machine check does
not replace two-machine network/member testing. Current installed services are unaffected.
