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

The round-1 boundary, verbatim from the plan:

> **Round-1 boundary:** one server-configured project root (env `ARCHFLOW_STUDIO_PROJECT_DIR`
> or `--project-dir`; no default path in code); proposal-only; canonical write, live model
> providers, login identity disabled; missing data → `BLOCKED_NEEDS_HUMAN` with a concrete
> question.

Nothing in round 1 commits. The chain ends at a validation receipt and a server verdict; no
route writes `HEAD`, `canonical/` or `input/`, and no route calls `compare_and_swap`. The
only writes the API performs are `repository.create_run(...)` and `repository.put_json(...)`
into run areas of the bound project — which is what running a candidate is.

## 2. Run it

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
| `npm run build` | syncs the rhino3dm runtime, typechecks, then `vite build` |

`@hey-api/openapi-ts` crashes under TypeScript 7, so the generator lives in
`web/tools/openapi-ts/` with its own `package.json`, its own `node_modules` and its own
TypeScript 5. The app itself is built with TypeScript 7. There is **no committed OpenAPI
snapshot**: the one description of this API is the FastAPI app, and `api:check` is what keeps
the committed client honest to it.

## 3. The API

Every route is under `/api`. Every error, without exception, is the one body
`{"code": "<CODE>", "detail": "<text>"}` — plus `question` and, when non-empty,
`acceptedForms` for `BLOCKED_NEEDS_HUMAN`. That includes unknown routes (404 `NOT_FOUND`),
wrong methods (405 `METHOD_NOT_ALLOWED`), request-validation failures (422 `REQUEST_INVALID`)
and unexpected bugs (500 `INTERNAL_ERROR`, with no traceback on the wire). No DTO carries a
`schema` tag, and no DTO carries a constant-false flag such as `readOnly` or
`canonicalWriteAuthority`.

One policy about what a `detail` may say: **it never carries the server's project directory.**
An error is read by whoever ran into it, and where this process keeps the project on disk is no
part of an answer about a design — an `OSError` is named by its class and the system's own
message rather than by the path it came with. `projectDir` on `GET /api/project` stays, because
that is an operator asking the binding question and being answered.

| method | path | response DTO | errors beyond the shared ones |
| --- | --- | --- | --- |
| GET | `/api/health` | `StudioHealth` | none — `projectBound` is a boolean, not a refusal |
| GET | `/api/project` | `ProjectBindingDto` | 503 `PROJECT_NOT_BOUND`, 404 `RUN_NOT_FOUND` |
| GET | `/api/state?run=` | `StateProjectionDto` | 503 `PROJECT_NOT_BOUND`, 404 `RUN_NOT_FOUND`, 404 `STATE_RECORD_NOT_FOUND`, 422 `STATE_RECORD_INVALID` |
| GET | `/api/artifacts` | `ArtifactListDto` | 503 `PROJECT_NOT_BOUND` |
| GET | `/api/artifacts/{sha256}/bytes` | binary (`ETag`, RFC 6266 `Content-Disposition`, `Cache-Control: no-store`) | 404 `ARTIFACT_NOT_FOUND`, 409 `ARTIFACT_UNREADABLE`, 409 `ARTIFACT_DIGEST_MISMATCH` |
| POST | `/api/pick/resolve` | `PickResolutionDto` (body `PickRequestDto`) | 409 `STALE_BASE` |
| POST | `/api/proposals` → 201 | `ProposalDto` (body `ProposalRequestDto`) | 422 `BLOCKED_NEEDS_HUMAN` (+ `question`, `acceptedForms`), 409 `STALE_BASE`, 403 `PROJECT_MISMATCH` |
| GET | `/api/proposals/{id}` | `ProposalDto` | 404 `PROPOSAL_NOT_FOUND` |
| POST | `/api/proposals/{id}/candidate` → 202 | `CandidateAcceptedDto` | 404 `PROPOSAL_NOT_FOUND`, 409 `PROPOSAL_NOT_RUNNABLE`, 409 `STALE_BASE`, 409 `CANDIDATE_ID_COLLISION` |
| GET | `/api/jobs/{id}` | `JobDto` | 404 `JOB_NOT_FOUND` |
| GET | `/api/candidates/{id}` | `CandidateDto` | 404 `CANDIDATE_NOT_FOUND` (while the run is still queued or running, said so in the detail) |
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

**HEAD ↔ record.** The project is opened with `open_located_project()` /
`FilesystemProjectRepository`; the authored State Record at `input/runner/state-record.json`
is bound to a run through `StateRecord.bound_to(run)` and never by any other route.

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

**Pick.** A click sends the object's `archflow:*` user strings, the document's own strings,
and the object name. The server answers **resolved**, **unbound** (this project never produced
that object — a fact, not an error) or **unknown_component** (it claims an identity this
record cannot honour). An element is only ever resolved *within the component the object
claims*: an object naming one component and matching an element of another is two claims that
disagree, and the component answers alone. Separately and without enforcing anything, the
answer reports `sourceState` — `current`, `stale` or `unknown` — because opening last week's
export to look at it is legitimate; the base is enforced where a change is proposed.

**Intent.** An utterance is parsed, not interpreted, against four exact forms:

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
runner's sentence*, printed verbatim rather than flattened into a status word.

**Validation.** `GET /api/candidates/{id}/validation` calls the kernel's `validate_submission`
over `CanonicalState(ref=head)` with three production validators — `artifact-present`,
`obligation-discharge`, `authorized-commitment-claims` — and reports the receipt's findings
and its `passed` unedited. Beside it stands the server's own **verdict**, a fixed conjunction
of five named clauses:

```
advance  ⟺  validation.receipt
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
available and succeeded. A seat that exported and left no artifact record blocks the advance
and says so in `honesty[]`. The clause is vacuous only for a candidate no seat of which
attempted an export — which is now something the receipt states, rather than something an empty
artifact list was taken to mean.

`blockedBy[]` names every clause that refused. The fourth clause is the point of the other
three: `held` is true whenever nothing was **violated**, including when nothing was
**checked**, so a candidate whose relations nobody could check is never green. The verdict is
memoised per (candidate, HEAD) — a client polling the readout must not appear on the event
stream as a server deciding over and over, and a moved HEAD is a different question that gets
a fresh answer and a fresh event.

And `effectiveChecks` is narrower than `validators` on purpose: a P036 `HEAD` is a ref-based
`CanonicalProjectState@1` carrying no facts, commitments or obligations, so two of the three
validators run over an empty state and find nothing to object to. That is not the same as
passing them, and the payload says so (kernel card **P110**).

## 5. Three digests, and which to compare

| name | what it identifies | compare it for |
| --- | --- | --- |
| `stateDigest` | the authored record **bound to a run** — run- and base-scoped | "is this the state the client was just given?" Picks, proposals and candidates are all checked against it, and a mismatch is `409 STALE_BASE`. It is also the number a runner receipt cites, which is why `matchesReferenceReceipt` can compare the projection against the reference run's own `designStateDigest`. |
| `recordDigest` | the record's **content**, invariant under binding | "is this the same authored record?" — the same content bound to two runs has two `stateDigest`s and one `recordDigest`. |
| `head.stateSha256` | the project's canonical version | "has the project moved?" It is half of the validation memo key, with `head.version`. |

A client that confused the first two would compare a project against itself. They are named
apart for that reason, and neither is ever abbreviated for anything but display: the shell
shortens digests to eight characters to read them, and always sends back the full value it
was given.

## 6. What the browser may not do

**Three-state law**, verbatim:

> held / violated / unchecked are distinct; unchecked is never green; the server issues the
> advance verdict.

The chips follow it literally: held is green only when `held > 0 && violated == 0 &&
unchecked == 0`; violated is red; unchecked is amber. The advance badge is the server's
boolean and nothing else.

**Browser law**, verbatim:

> never import archflow; never infer impact client-side; never declare "validation passed"
> client-side; chat is not version history; never write the project directory or HEAD; a
> `.3dm` never implies success.

Two consequences worth stating outright. Every gateway error renders as a **visible error
state** carrying the server's `code` and `detail` (and `question` / `acceptedForms`) in place
of the panel's content — never an empty list, because an empty list is the one way a browser
can turn a server's refusal into a silent, confident-looking answer. And the event panel is a
live view of a running process: it is bounded, it is dropped on reload, and nothing in it is a
record of what the project is. The one error the shell *handles* rather than merely displays is
`STALE_BASE` — it re-projects and says "the project moved under you" out loud.

## 7. Cards this slice stands on

- **P109** — typed operator on the State Record. Until the kernel offers a successor
  operation, a candidate's successor record is the K1 candidate-under-card: recomputed for the
  candidate and said so on the wire, never promoted.
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
npm run api:check
npm run typecheck
npm run build
```

`archcheck` must print `ARCHITECTURE PASS` after every Python change; it is the firewall, not
a lint. The API tests run on real fixtures — a real P036 project via
`FilesystemProjectRepository.initialize` — and there are no mocks in them.
