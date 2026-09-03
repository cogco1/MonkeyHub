# P108 Refoundation — One Vertical Slice to the Validation Receipt

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** calibrated 2026-09-03 (see "Calibration results" at the end) — awaiting Kaiwen's go to execute.
Written after Kaiwen adopted codex's review of the two earlier
plans (`2026-09-03-p108-phase1-foundation-plan.md`, `2026-09-03-p108-round1-plan-a-read-slice.md`, both
SUPERSEDED). Execution waits for (a) Kaiwen's go after calibration and (b) the governance items sent to the
main session (firewall repoint to `apps/archflow-studio/api`, P108 card rewrite, brief Addendum 3, registry,
archive card + tag).

**Goal:** Retire Preview Slice 01 and rebuild ArchFlow Studio inside `apps/archflow-studio` as `web/` +
`api/` so that one vertical slice runs end to end on the villa project:
`exact HEAD ↔ exact StateRecord ref/base ↔ component + dependency projection ↔ pick a component and submit an
intent ↔ typed proposal / BLOCKED_NEEDS_HUMAN ↔ impact closure ↔ detached candidate (harness run, compiled
program + optional Rhino artifact, all content-addressed) ↔ validation receipt` — with **no canonical commit**.

**Architecture:** FastAPI (`api/archflow_studio_api`) is a BFF: request validation, routing, SSE, job
lifecycle. Every design answer comes from `archflow` by delegation: `open_located_project`,
`FilesystemProjectRepository`, `StateRecord` views (`developed_design_view`, `design_components_of`,
`dependency_edges`, `closure`), `run_project` for candidates (harness stage, precedent
`tools/verify_state_record.py`), `check_relations` reports, `validate_submission`. Pydantic models are
transport shapes; the browser consumes an OpenAPI-generated client (`@hey-api/openapi-ts`) — no hand-written TS
mirror. The viewer (`ThreeDmViewport.tsx`, `sceneInspection.ts`, rhino3dm sync) moves; the stdlib gateway,
presence-probe kernel, monolithic App and placeholder panels are deleted in the same change that replaces
them.

**Tech Stack:** Python 3.12, fastapi 0.141.1 (native `fastapi.sse.EventSourceResponse`), pydantic 2.13,
uvicorn, httpx (tests). React 19, three.js 0.185, rhino3dm 8.32.2 (wasm), Vite 8, TypeScript 7,
`@hey-api/openapi-ts` 0.99.0. `unittest` for the api; `tsc` + Vite build for the web.

## Global Constraints

- Write scope: `apps/archflow-studio/` only, plus dated lane docs in `docs/claude-worktree/`. Kernel gaps are
  carded to Kaiwen (two are already named below: K1, K2); never fixed in passing. `server/` and `shared/`
  at the repo root are firewall tripwires — never create them.
- Firewall (self-enforced until the policy repoint lands, machine-enforced after): `api/` never imports
  `rhino3dm`, `numpy`, `networkx`, `OCP`, `build123d`, `shapely`, `trimesh`, `scipy`, `tests`, `tools`,
  `probes`. `py -3.12 tools/archcheck.py` must print `ARCHITECTURE PASS` after every Python change.
- Browser law: never import archflow; never infer impact client-side; never declare "validation passed"
  client-side; chat is not version history; never write the project directory or HEAD; a `.3dm` never implies
  success. No new databases, no networkx, no studio-owned MutationEngine/ProjectState/Validator, no parallel
  version history. **Every gateway error renders as a visible error state with the server's code and detail —
  never an empty list.**
- Three-state law: held / violated / unchecked are distinct; unchecked is never green; the server issues the
  advance verdict.
- Round-1 boundary: one server-configured project root (env `ARCHFLOW_STUDIO_PROJECT_DIR` or
  `--project-dir`; **no default path in code**); proposal-only; canonical write, live model providers, login
  identity disabled; missing data → `BLOCKED_NEEDS_HUMAN` with a concrete question.
- Transport rule: Pydantic DTOs never mirror an archflow schema field by field; canonical payloads travel as
  opaque `dict` where the client only displays them.
- Writes the api MAY perform: `repository.create_run(...)` and `repository.put_json(...)` into run areas of the
  bound project (that is what a candidate is). Writes it MUST NOT perform: anything to `HEAD`, `canonical/`,
  `input/`, or any file outside P036 record/workspace areas.
- Git: explicit paths only, never `add -A`; commits end with
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Tests: `py -3.12 -m unittest discover -s apps/archflow-studio/api/tests -t apps/archflow-studio/api -v`
  from the repo root. The api package inserts the repo root on `sys.path` in its `__init__`.

## Kernel facts this plan is built on (all verified by execution on 2026-09-03)

- `open_located_project(project_id, *, local_projects_root)` → `(location, FilesystemProjectRepository)`;
  villa opens in 0.01 s; `read_head()` → `ProjectVersionRef(project_id, version=0, state_sha256=2aa733c5…)`.
- `StateRecord.state_digest` is a **property** and raises unless the record carries `base`; the villa
  `input/runner/state-record.json` has no `base`. **The one sanctioned binding is
  `record.bound_to(run)`** (kernel commit 44cf960, 2026-09-03): "a read-only projection (Studio) binds
  against the repository HEAD the same way — never a hand-built base". The studio builds
  `RunRef(project_id, reference_run_id, repository.read_head())` and calls `bound_to`; it never constructs
  `base` by hand and never uses `dataclasses.replace` for binding. Verified on the real villa:
  `record.bound_to(RunRef(pid, "runner-002", read_head()))` → developed-view digest `344b2206…` (identical to
  runner-002's receipt); a synthetic run id (`studio-projection`) changes the digest — **the run id
  participates in the digest**, so the projection binds to the reference run's id, never a made-up one.
- **Record-name parsing rule (from the benchmark's real-project failure):** a retained record is
  `<kind>-<64 hex>.json`; parse with `^(?P<kind>.+)-(?P<sha>[0-9a-f]{64})\.json$` and compare `kind` by
  **equality**, never by prefix — `workflow-001` holds both `project-stage-workflow-<sha>.json` and
  `project-stage-workflow-freeze-receipt-<sha>.json`. Applies to every lookup in this plan
  (`runner-run-receipt`, `seat-rhino-execution`, `seat-relation-check`, `seat-geometry-program`).
- `developed_design_view(record, run=run, portfolio_id="declared-schematic", branch_id="runner-v1",
  selection_decision_ref="decision:declared-schematic-selection")` reproduces runner-002's receipt digest
  `344b2206…` exactly (the `RunOptions` defaults). With other kwargs the digest differs.
- Villa record today: 96 entities, 41 `Component@1`, 0 parameters, 0 relations, 0 dependency edges, stage
  binding all null. Closure is therefore trivial; the UI must say so.
- `run_project(repository, *, run, stage_guard, record, seats, options)` needs a `StageExecutionGuard`; for
  stage N>0 `require_stage_run_envelope` demands the retained stage N-1 exit — the villa has none retained
  (only `equivalence-harness-*` envelopes exist). Candidate runs therefore use the harness pattern: a
  one-stage `ProjectStageWorkflow` at `DesignPhase.DESIGN_DEVELOPMENT` retained in the candidate run and an
  envelope from `open_stage_run_envelope(...)`. `run_project` calls `asyncio.run` internally → it must run in
  a worker thread, never inside an async handler.
- Candidate outputs are content-addressed run records: `seat-geometry-program` (the compiled program),
  `seat-relation-check` (`RelationCheckReport@1` with `held` and `fully_checked`), `seat-round-receipt`,
  `runner-run-receipt` (`RunnerRunReceipt@3`: `seat_results[].program_ref/program_digest/objects/
  relation_check_ref/cad`, `seat_execution_complete`). With `RunOptions.export=True` (Rhino, ~37 s per
  seat) also `seat-rhino-execution` (`RhinoCadExecutionReceipt@4`: `artifact_relative_path`,
  `inspection.file_sha256`, `identity.binding{base, program_ref, program_digest, design_state_digest, run_id,
  stage_id}`, `status`, `readback_verified`) and the `.3dm` under `runs/<run>/workspaces/cad-<stage>-<seat>/`.
- `seats.json` (villa): `commitment_ref`, `branch_id`, `provider_identity{provider_id, model_id,
  provider_version, provider_fingerprint}`, `seats[]{seat_id, disciplines, phases, owned_component_ids,
  consumes, reviewer}` → `SeatSpec` via the six-line adapter in `tools/run_project.py::_seat` (tools is
  firewall-forbidden; the api carries the same six lines as `adapters/seats.py`).
- `canonical_state_from_dict(repository.load_current_state())` **fails on the villa** ("canonical state schema
  drifted": `load_current_state()` returns a ref-based `CanonicalProjectState@1` —
  `authoritative_record_refs` / `derived_record_refs` / `phase`; `CanonicalSnapshot@2` is the retained
  snapshot record kind). `validate_submission(CanonicalState, CandidateSubmission, validators)` therefore runs
  against `CanonicalState(ref=head)` with empty facts, labeled as such (K2 → card **P110**: on an empty fact
  base the two production validators have nothing to check, so the receipt is effectively
  `artifact-present` only — the Validation DTO says exactly that).
- **Digest scope (main-session calibration):** the run id enters `state_digest`, the program digest and
  **also `record.digest`** (`to_dict` carries `run_id` and `base`), so a bound record's digests change with
  the run it is bound to. "Did this edit change anything" is answered by comparing the **authored** (unbound)
  content — `authoredRecordDigest = StateRecord.from_dict(payload).digest` before `bound_to` — or two records
  bound to the same run; never a candidate's bound digest against the projection's.
- `ProjectArtifactRef(project_id, artifact_id, relative_path, sha256, media_type)` exists; artifacts are
  enumerated from `seat-rhino-execution` receipts, never by `rglob`.
- Run/record identifiers: `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`.

## Kernel cards to raise (not fixed here)

- **K1 — apply a typed operator to `StateRecord@1`.** `DecisionOperator`/`compile_decision_operator` act on
  `OperationalMarkovState`; nothing in the kernel applies a typed intent to a `StateRecord`. Round 1 keeps the
  candidate-record construction (`dataclasses.replace` of one entity field or one parameter value, protected
  set checked against `record.closure`) as a **candidate under this card in the api application layer**, per
  AGENTS.md rule 3 ("if nothing can be retired, the abstraction is not canonical yet: write it as a candidate
  under the card, not into archflow/"). It contains no propagation logic — propagation is the runner's.
- **K2 — `CanonicalState` projection of a State-Record project.** `canonical_state_from_dict` rejects the
  villa HEAD. Until a kernel projection exists, the validation receipt is computed over
  `CanonicalState(ref=head)` and the receipt DTO carries `canonicalFacts: "unavailable (schema drifted)"`.

## Directory (final state of this plan)

```
apps/archflow-studio/
  README.md
  api/
    requirements.txt
    openapi.json                       # committed snapshot; drift-tested
    archflow_studio_api/
      __init__.py                      # repo root on sys.path
      main.py                          # create_app(settings) + uvicorn entry
      settings.py                      # StudioSettings.from_env()/from_args(); no default path
      ports.py                         # the six Protocols, moved from backend/ports.py
      transport/                       # Pydantic DTOs (errors, project, state, artifacts, pick, proposal, candidate, validation, events)
      application/                     # binding, projection, artifacts, pick, intent, impact, candidate, validation, events, jobs
      adapters/                        # seats.py, harness.py, records.py (URI/ref helpers)
      routes/                          # one APIRouter per feature
    tests/
  web/
    package.json  package-lock.json  tsconfig.json  vite.config.ts  index.html  .gitignore  scripts/
    src/
      main.tsx  styles.css
      app/                             # shell, layout, error boundary, session state
      features/                        # project, state, viewer-bindings, pick, intent, proposal, impact, candidate, validation, events
      viewer/                          # ThreeDmViewport.tsx, sceneInspection.ts (moved)
      api/generated/                   # hey-api output (committed)
      api/client.ts                    # base URL, error normalisation
```

Deleted by this plan: `backend/` (all), `run_server.py`, `launch.py`, `src/App.tsx`,
`src/components/{CapabilityPanel,StageRail}.tsx`, `src/contracts/studio.ts`, `src/gateway/*`,
`src/ports/studioPorts.ts` (the TS reservations are superseded by generated types). Tag
`studio-preview-slice-01` is created by the main session before Task 9 deletes them.

---

### Task 1: api skeleton — settings, app factory, moved ports, health, OpenAPI snapshot

**Files:**
- Create: `api/requirements.txt`, `api/archflow_studio_api/__init__.py`, `settings.py`, `main.py`,
  `transport/__init__.py`, `transport/errors.py`, `transport/health.py`, `routes/__init__.py`,
  `routes/health.py`, `api/tools_openapi.py` (dump script), `api/openapi.json`
- Move: `backend/ports.py` → `api/archflow_studio_api/ports.py` (git mv; docstring line updated; Protocol
  bodies byte-identical)
- Test: `api/tests/__init__.py`, `api/tests/test_health.py`, `api/tests/test_openapi_snapshot.py`

**Interfaces:**
- `StudioSettings(project_dir: Path, rhino_export: bool = False, powershell: Path | None = None)`;
  `StudioSettings.from_env()` reads `ARCHFLOW_STUDIO_PROJECT_DIR` (required — missing → `SettingsError`
  with the exact variable name), `ARCHFLOW_STUDIO_RHINO_EXPORT` ("1"), `ARCHFLOW_STUDIO_POWERSHELL`.
- `create_app(settings: StudioSettings) -> FastAPI`; `app.state.settings`; exception handlers map
  `StudioError` subclasses to `{"schema": "StudioError@1", "code", "detail"}` with their HTTP status.
- `transport/errors.py`: `class StudioError(Exception)` with `status`, `code`, `detail`; subclasses
  `NotBound(503, "PROJECT_NOT_BOUND")`, `NotFound(404, code)`, `StaleBase(409, "STALE_BASE")`,
  `ProjectMismatch(403, "PROJECT_MISMATCH")`, `DigestMismatch(409, "ARTIFACT_DIGEST_MISMATCH")`,
  `BlockedNeedsHuman(422, "BLOCKED_NEEDS_HUMAN")` carrying `question: str`.
- `GET /api/health` → `StudioHealth` `{schema: "StudioHealth@3", status, service: "archflow-studio-api",
  readOnly: true, canonicalWriteAuthority: false, projectBound: bool}`.
- `api/openapi.json` = `create_app(StudioSettings(project_dir=Path("unbound"))).openapi()` dumped by
  `py -3.12 api/tools_openapi.py`; the snapshot test fails when the live schema differs (regenerate + commit).

- [ ] Steps: write `test_health.py` (200, schema name, `canonicalWriteAuthority is False`) and
  `test_openapi_snapshot.py` (live `app.openapi()` == file) → run (fail: package missing) → implement →
  run (pass) → `archcheck` → commit
  `P108 refoundation: api skeleton, ports moved, OpenAPI snapshot under test`.

---

### Task 2: Project binding + exact HEAD + StateRecord projection (base attached, digest reproducible)

**Files:**
- Create: `application/binding.py`, `application/projection.py`, `transport/project.py`,
  `transport/state.py`, `routes/project.py`, `routes/state.py`
- Test: `api/tests/support.py` (fixture: `FilesystemProjectRepository.initialize` + one run + retained
  `state-record` + `input/runner/{state-record.json,seats.json}` shaped like the villa's, with 2
  `Component@1`, 1 `Level@1`, 1 `GridAxis@1`, 1 `Element@1` whose producer is `produce_wall` with real
  references, 1 `Parameter` with `lock_authority`, 1 `support` relation with a `support_contact` validator —
  so closure, locks and three-state checks are non-trivial), `test_binding.py`, `test_projection.py`

**Interfaces:**
- `ProjectBinding.open(settings) -> ProjectBinding` holding `repository`, `location`, `project_id`;
  `head() -> ProjectVersionRef`; `reference_run(run_id=None) -> RunRef` resolved in this order: `?run=`,
  then `settings.reference_run` (env `ARCHFLOW_STUDIO_REFERENCE_RUN`), then the rule — the newest complete
  `runner-run-receipt` (`seat_execution_complete` for @3, `accepted` for @1) among runs that are **not
  harness runs** (a receipt whose `workflow_ref` names a workflow with `workflow_id` ∈ {`equivalence-harness`,
  `studio-candidate-harness`} is excluded; receipts without `workflow_ref` are eligible). Verified on the
  villa: the rule selects `runner-002` alone (the patch/equivalence runs are harness runs) and studio
  candidates can never become the reference. The DTO reports `referenceRunSource` (`query` | `config` |
  `rule` | `none`). A run-less project (nothing eligible, nothing given) still projects: the record binds to
  `RunRef(project_id, "studio-projection", head)` — a value object that need not exist on disk — with
  `referenceReceipt: null`, `matchesReferenceReceipt: null` and the honesty line "no eligible reference run:
  projection bound to the studio run id; its digests are not comparable to any receipt".
- `projection(binding, run_id=None) -> StateProjection` (frozen dataclass, application layer):
  loads `input/runner/state-record.json` via `layout.resolve_relative`, `StateRecord.from_dict`,
  `run = RunRef(project_id, reference_run_id, repository.read_head())`, `record = record.bound_to(run)`,
  `developed_design_view(record, run=run,
  portfolio_id="declared-schematic", branch_id="runner-v1",
  selection_decision_ref="decision:declared-schematic-selection")`; exposes `record`, `state`,
  `record_digest = record.digest`, `state_digest = state.state_digest`, `components =
  design_components_of(record)`, `edges = record.dependency_edges()`, `levels/grids` counts, `stage` refs.
- DTO `StateProjectionDto` (`schema "StudioStateProjection@2"`): `projectId`, `head{version, stateSha256}`,
  `referenceRun{runId, baseVersion, baseSha256}`, `recordSource`, `recordDigest`, `stateDigest`,
  `activePhase`, `counts{entities, components, parameters, relations, obligations, dependencyEdges}`,
  `componentTree[]{componentId, parentComponentId, semanticKind, intent, maturity, revision}`,
  `elements[]{elementId, componentId, producer, numericFields{key: value}}` (the intent grammar's targets),
  `parameters[]{key, value, unit, lockAuthority, epistemicStatus, inputs[]}`,
  `dependencyEdges[]{source, target, kind}`, `stageBinding{workflowRef, envelopeRef, stageId}` (nulls kept),
  `honesty[]` (strings the UI must show verbatim, e.g. "0 dependency edges declared: impact closure is
  direct-only").
- Routes: `GET /api/project` (binding + head + reference run), `GET /api/state?run=` (projection).
- Tests: binding unbound → 503; head version 0; projection digest equals `developed_design_view` computed
  in the test with the same kwargs; villa-shaped fixture yields the component tree; missing runner record →
  404 `STATE_RECORD_NOT_FOUND` with the exact path in `detail`.

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: exact HEAD and a base-attached StateRecord projection whose digest reproduces the runner's`.

---

### Task 3: Artifacts from receipts (ref + SHA + run/base + status), digest-checked download

**Files:**
- Create: `application/artifacts.py`, `transport/artifacts.py`, `routes/artifacts.py`
- Test: `api/tests/test_artifacts.py` (fixture writes a `seat-rhino-execution`-shaped record via `put_json`
  plus the `.3dm` bytes under the run workspace; a second record whose file was tampered)

**Interfaces:**
- `list_artifacts(binding) -> tuple[ArtifactRecord, ...]`: for every run, every
  `seat-rhino-execution-*.json` record listed through `repository.list_json(run=, destination=RUN_RECORD)`
  (and the branch area `RUN_BRANCH` where present), loaded through `load_json` (digest-verified), mapped to
  `ArtifactRecord(artifact_id=sha256, project_id, run_id, stage_id, relative_path, sha256=
  inspection.file_sha256, size_bytes=inspection.file_bytes, status, readback_verified, base{version,
  sha256}, program_ref, program_digest, design_state_digest, receipt_ref)` → wire as `ProjectArtifactRef`
  fields plus binding; `relative_path` is project-relative (`runs/<run>/workspaces/cad-<stage>/<file>`),
  resolved by joining the receipt's workspace dir (derived from `stage_id`) and `artifact_relative_path`.
- `artifact_bytes(binding, sha256) -> bytes`: locate by sha in the listing, resolve through
  `layout.resolve_relative`, recompute sha256 of the file; mismatch → `DigestMismatch`; unknown → 404.
- Routes: `GET /api/artifacts` (`StudioArtifactList@2`), `GET /api/artifacts/{sha256}/bytes`
  (`application/octet-stream`, `ETag: "<sha256>"`).
- Tests: listing carries run/base/status/sha; bytes served; tampered file → 409; unknown sha → 404;
  `rglob` is not used (grep assertion in test).

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: artifacts are receipts first - ref, SHA, run and base travel together`.

---

### Task 4: Semantic pick resolution

**Files:** `application/pick.py`, `transport/pick.py`, `routes/pick.py`; test `test_pick.py`.

**Interfaces:** `POST /api/pick/resolve` body `{stateDigest, userStrings: {k: v}}` → `PickResolution@1`
`{status: resolved|unbound|unknown_component, componentId, elementId, detail}`; keys read: `archflow:component`,
`archflow:object_ref` (prefix `object:` stripped, matched to an `Element@1` entity id), never invented; a
`stateDigest` that differs from the current projection → `StaleBase`. Tests: resolved with element; unbound
(no `archflow:` keys); unknown component named in detail; stale digest → 409.

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: a picked object resolves server-side to one semantic component or says why not`.

---

### Task 5: Intent → typed proposal (deterministic grammar at the IntentProvider seam)

**Files:** `application/intent.py` (class `DeterministicIntentProvider` implementing
`ports.IntentProvider.propose(*, session_ref, message, context_refs)`), `application/proposals.py`
(in-memory proposal store keyed by id, per process, explicitly not history), `transport/proposal.py`,
`routes/proposals.py`; tests `test_intent_grammar.py`, `test_proposals.py`.

**Grammar (exact; anything else → BLOCKED_NEEDS_HUMAN with a question naming the accepted forms):**
```
set <field> to <number>[<unit>]
set <field> = <number>[<unit>]
increase <field> by <number>%
decrease <field> by <number>%
… [keep <ref>[, <ref>…]]
```
`<field>` must be a **scalar numeric entry of the selected `Element@1`'s `fields["params"]`** (the villa's
elements keep their numbers there — e.g. `params.height` on the `prism` row `portico-roof-abutment-west`;
polyline vertices such as `params.profile[i][j]` and `references.*` datum offsets are NOT grammar targets —
moving a coordinate by prose is the "silently invented coordinate" the boundary forbids) or a
`Parameter.key` of the record; `<ref>` must be an entity id or parameter key. Selection comes from the
request (`targetComponentId`, optional `elementId`), never parsed from prose. The projection's
`elements[].numericFields` lists exactly these `params.<key>` scalars.

**Interfaces:**
- `POST /api/proposals` body `{stateDigest, targetComponentId, elementId?, utterance}` →
  `Proposal@1` `{proposalId, status: "proposed", baseStateDigest, recordDigest, target{componentId,
  elementId, key}, change{old, new, unit}, protected[], decisionOperator: DecisionOperator.to_dict(),
  impact: Impact@1 (Task 6)}` or HTTP 422 `BLOCKED_NEEDS_HUMAN {question, acceptedForms[]}`.
- `decisionOperator` is a real `DecisionOperator(decision_id, decision_type="studio.parameter_change" |
  "studio.element_field_change", base_state_digest=state_digest, authority_id="studio:proposal-only",
  intent=utterance, preconditions=(StateCondition(ref, EQUALS, old),), bindings=(ParameterBinding(key,
  new, source_ref="studio:intent"),), add_locks=(StateLock(target_ref=ref, authority_id="studio:user",
  source_ref="studio:intent") for ref in protected), invalidates=closure, evidence_refs=(record_ref,))` —
  typed, exact-base, no write authority; not compiled (K1).
- Stale `stateDigest` → 409; body `projectId` (optional) ≠ bound → 403; a parameter with
  `lock_authority` set and not released → `BLOCKED_NEEDS_HUMAN("parameter X is locked by Y; release it
  explicitly?")`; a change whose closure intersects `protected` → status `conflict` with the intersecting refs
  (still a proposal, never executed until the user resolves).
- Tests: table of utterances → parse result; villa-shaped fixture with 0 parameters → parameter intent
  blocked with a question that names "0 parameters"; element field change proposed; locked parameter
  blocked; stale base 409; cross-project 403; conflict when protecting a downstream ref.

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: the IntentProvider seam speaks a deterministic grammar and returns typed proposals or a question`.

---

### Task 6: Impact closure

**Files:** `application/impact.py`, `transport/impact.py` (folded into the proposal response); test
`test_impact.py`.

**Interfaces:** `impact(projection, changed_refs, protected) -> Impact` `{direct[], propagated[] =
record.closure(changed_refs) minus direct, protected[], conflicts[] = propagated ∩ protected, locks[] =
parameters with lock_authority in closure, unknownCoverage: components with no edge touching them (count +
ids), honesty[]}` — no inference beyond the kernel's closure. Tests on the fixture's relation/parameter
chain (a→b→c: changing a propagates to b and c; protecting c conflicts).

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: impact is the kernel closure, shown with what it cannot see`.

---

### Task 7: Detached candidate — harness run through `run_project` (worker thread + SSE)

**Files:** `adapters/seats.py`, `adapters/harness.py`, `application/candidate.py`, `application/jobs.py`,
`application/events.py` (implements `ports.StudioEventSink`; ring buffer + subscribers),
`transport/candidate.py`, `transport/events.py`, `routes/candidates.py`, `routes/events.py`; tests
`test_candidate.py` (runs the real `run_project` on a temporary **copy of the villa input files** initialised
into a fresh repository under `tempfile` — export off; asserts a `runner-run-receipt` with
`seat_execution_complete`, `seat-geometry-program` refs, and a `seat-relation-check` report with three-state
counts), `test_events.py`, `test_sse.py`.

**Mechanism (all kernel calls):**
1. `run_id = f"studio-cand-{utc %Y%m%d-%H%M%S}-{proposal_id[:8]}"`; `run = repository.create_run(run_id,
   base=binding.head())`.
2. Candidate record: `successor = replace(record, entities=…)` where the targeted `Element@1`'s
   `fields["params"][key]` (or the `Parameter.value`) is replaced with `change.new` — the K1 candidate code,
   ≤ 30 lines, in `application/candidate.py::successor_record` — then `successor.bound_to(run)` (binding
   is the kernel's; the studio only edits the one authored value).
3. `state = developed_design_view(candidate_record, run=run, portfolio_id="declared-schematic",
   branch_id="runner-v1", selection_decision_ref="decision:declared-schematic-selection")`.
4. Harness guard (`adapters/harness.py`): `ProjectStageWorkflow(project_id, workflow_id=
   "studio-candidate-harness", stages=(ProjectStage(stage_id="studio-candidate", stage_index=0,
   phase=DesignPhase.DESIGN_DEVELOPMENT, required_roles=("geometry-program",), required_checks=
   ("studio-candidate-relations",), close_obligation_id="close-studio-candidate"),), basis_refs=
   ("decision:studio-candidate-harness",))` retained via `put_json(record_kind="studio-candidate-workflow")`;
   `open_stage_run_envelope(workflow, workflow_ref=…uri, run_id, base_version=run.base.version,
   base_state_sha256=run.base.require_digest(), branch_id="runner-v1", branch_epoch=1,
   subject_ref="state:developed-design-state", state_digest=state.state_digest, stage_index=0,
   close_obligation=DesignObligation(obligation_id="close-studio-candidate", statement="A studio candidate
   harness stage; it closes nothing in the project's own workflow.", source_ref=
   "workflow:studio-candidate-harness/stage-0", subject_refs=("state:developed-design-state",),
   validator_ref="validator:composite-stage-closure"))` retained as `studio-candidate-envelope`;
   `StageExecutionGuard(workflow, workflow_record_ref, envelope, envelope_record_ref)`.
5. Seats from `input/runner/seats.json` through `adapters/seats.py` (the six-line `SeatSpec` adapter);
   `RunOptions(commitment_ref, provider_identity=GeometryProposalProviderIdentity(**…), branch_id=
   seats.branch_id or "runner-v1", export=settings.rhino_export, workspace_root=layout.run(run_id).root /
   "workspaces", powershell=settings.powershell)`.
6. `receipt = run_project(...)` inside `asyncio.to_thread`; job states `queued → running → succeeded |
   failed` published to the event sink with the run id and wall time; `ProjectRunnerError` → job failed with
   the message (never swallowed).
7. Candidate DTO (`Candidate@1`): `{candidateId=run_id, proposalId, status, base{version, sha256},
   stateDigest, receiptRef, seatResults[]{seatId, status, programRef, programDigest, objects,
   relationCheckRef, cad?}, relationChecks{held, violated, unchecked, fullyChecked, heldFlag}, artifacts[]
   (Task 3 shape, only when exported), wallTimeS, harness: "studio-candidate-harness (not a project stage
   advance)"}`.
- Routes: `POST /api/proposals/{id}/candidate` → 202 `{jobId, candidateId}`; `GET /api/candidates/{id}`;
  `GET /api/events` (SSE via `EventSourceResponse`, replay then live, `StudioEvent@1{seq, at, type, …}`).
- Tests as listed; wall time recorded in the test output.

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: a candidate is a harness run of the kernel runner, content-addressed and never canonical`.

---

### Task 8: Validation receipt and the server's advance verdict

**Files:** `application/validation.py`, `transport/validation.py`, `routes/validation.py`; test
`test_validation.py`.

**Mechanism:** `CanonicalState(ref=binding.head())` (K2 — facts unavailable, labeled);
`CandidateSubmission(submission_id=candidate_id, base=binding.head(), workspace_id=run_id,
intent=proposal.utterance, delta=CandidateDelta(artifacts_add=tuple(ArtifactRef(artifact_id=program_digest,
uri=program_ref, media_type="application/json", sha256=program_sha) for each seat program)), claims=(Claim
("studio.candidate.run_id", run_id, evidence_refs=(receipt_ref,)),), evidence_refs=(receipt_ref, *artifact
ids))` — calibration C2 showed `ArtifactPresentValidator` requires every added artifact id in
`evidence_refs` (`artifact.evidence_missing` otherwise);
`validate_submission(state, submission, (ArtifactPresentValidator(), ObligationDischargeValidator(),
AuthorizedCommitmentClaimsValidator()))` → `ValidationReceipt` (typed, `passed`, `findings[]`).
`RequiredClaimsValidator` is **excluded**: it is compatibility-only and returns
`compatibility.goal_contract_missing` on production state (C2) — noted under K2. Composite verdict (server-issued): `advance = receipt.passed and
runner.seat_execution_complete and relation.held and relation.fully_checked`; `blockedBy[]` lists every
failing clause by name. DTO `Validation@1{receipt{receiptId, submissionDigest, checkedState, passed,
findings[]}, canonicalFacts: "unavailable (schema drifted; K2)", relationChecks{…three-state…},
runnerComplete, advance, blockedBy[]}`. Route `GET /api/candidates/{id}/validation`. Tests: passed path on
the villa-copy candidate; a stale-base submission (head moved) yields `state.base_mismatch`; unchecked
relations alone flip `advance` to false with `blockedBy=["relations.fully_checked"]`.

- [ ] Steps: tests → fail → implement → pass → archcheck → commit
  `P108 refoundation: validation is the kernel's receipt plus a server verdict that never greens the unchecked`.

---

### Task 9: web — move, generate the client, build the reviewed shell, retire the old app

**Files:**
- Move: `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `index.html`,
  `.gitignore`, `scripts/` → `web/`; `src/viewer/*`, `src/main.tsx`, `src/styles.css` → `web/src/…`.
- Delete (git rm): `src/App.tsx`, `src/components/*`, `src/contracts/*`, `src/gateway/*`, `src/ports/*`,
  `backend/` (all), `run_server.py`, `launch.py`. **Precondition:** tag `studio-preview-slice-01` exists
  (main session); the task checks `git tag -l` and stops if absent.
- Create: `web/src/api/client.ts` (base URL, `StudioError` normalisation: every non-2xx → thrown
  `StudioApiError{code, detail, status}`), `web/src/api/generated/**` (hey-api output from
  `api/openapi.json`), `web/tools/openapi-ts/package.json` (an isolated generator install:
  `@hey-api/openapi-ts@0.99.0` + `typescript@5` — calibration C4 showed hey-api crashes under the web's
  TypeScript 7.0.2 (`ts.SyntaxKind` undefined: the Go-based compiler ships no JS API) and works under
  TS 5; the web keeps TS 7.0.2 for `typecheck`); `npm run api:generate` in `web/` =
  `npm --prefix tools/openapi-ts exec openapi-ts -- -i ../../api/openapi.json -o ../../src/api/generated`,
  `web/src/app/{App.tsx, Shell.tsx, ErrorPanel.tsx, useSession.ts}`,
  `web/src/features/{project,state,pick,intent,proposal,impact,candidate,validation,events}/*.tsx`.
- Modify: `web/package.json` (name `archflow-studio-web`; scripts `api:generate`, `dev`, `build`,
  `typecheck`, `sync:rhino3dm`; devDependency `@hey-api/openapi-ts@0.99.0`), `vite.config.ts` proxy →
  `http://127.0.0.1:8000`, `ThreeDmViewport.tsx` (+ `openFile(file, sourceLabel)`, `onPick(userStrings)`
  raycast; behavior otherwise unchanged).

**Shell (reviewed skeleton; every panel bound to a real route):** top bar = project id · HEAD `v{n}`
`{sha:8}` · reference run · base · phase · stage binding (or `NO STAGE BINDING`) · authority `PROPOSAL-ONLY ·
CANONICAL WRITE DISABLED`; left = component tree (click = select), elements + parameters of the selection,
artifacts (receipt list, click = load labeled `CANONICAL · run <id> · sha`); center = viewer with source chip
`CANONICAL` / `CANDIDATE` / `LOCAL · UNBOUND`; right = Intent (grammar hint + input over the selection) →
Typed Proposal (operator summary, change, protected) → Impact (direct/propagated/conflicts/locks/unknown +
honesty lines) → Human Review (`Run candidate` button; disabled with reason when status ≠ proposed) →
Validation (receipt findings, three-state chips, `advance` verdict with `blockedBy`); bottom = event stream
(SSE), candidate runs (receipts), no chat log. Every failed call → `ErrorPanel` with code + detail.

- [ ] Steps: move → install → generate → typecheck/build green with the shell → live smoke against the villa
  (bind, tree, pick, intent `set height to 5.0` on a selected element, proposal, impact, candidate run
  (export off), validation) with screenshots → delete the old app → typecheck/build again → commit
  `P108 refoundation: the studio shell runs the slice end to end on generated types; Preview Slice 01 retired`.

---

### Task 10: README, archive note, full battery

- [ ] `apps/archflow-studio/README.md` rewritten (run: `py -3.12 -m pip install -r api/requirements.txt`;
  `$env:ARCHFLOW_STUDIO_PROJECT_DIR=…; py -3.12 -m archflow_studio_api.main` from `api/`; `npm run dev`
  from `web/`; route table; boundary; K1/K2; three-state rule; tag of the retired slice).
- [ ] Battery: `archcheck`, api tests, kernel suite (`tests/`), web typecheck + build, OpenAPI snapshot
  test. Commit `P108 refoundation documented; battery green`.

---

## Execution protocol (from the main session's benchmark, 2026-09-03)

Fable 5.1 and Opus 5 scored 29/29 on the same pinned plan under independent judging; the differences were
second-order and cancelled. So:

1. **Pin, then dispatch.** Each task is pinned before dispatch — routes, DTO fields, error codes, file list,
   test names and assertions written out — and given to an Opus worker (`Agent`, `model: "opus"`) in an
   isolated worktree (`isolation: "worktree"`). This session plans, reviews and judges; it does not write the
   pinned code itself.
2. **Dry-run on real data first.** Before dispatching a task that touches the project, its kernel calls are
   dry-run against the real villa project (read-only) or a temporary copy (writes) — the calibration script
   `calibrate_chain.py` is the template.
3. **Judge with independent scripts, not worker reports.** Acceptance of every task runs an independent
   verification script against kernel truth (digests recomputed from `archflow`, record kinds counted from the
   repository, receipts reloaded through `load_json`) plus the test suite and `archcheck`. A worker's
   self-report is not evidence.
4. **Merge only what the judge passed**, task by task, explicit paths, never `add -A`.

## Self-Review

1. **Chain coverage:** HEAD (T2) ↔ record/base (T2) ↔ tree/dependencies (T2) ↔ pick + intent (T4, T5) ↔
   proposal/BLOCKED (T5) ↔ impact (T6) ↔ candidate + SHA (T7, T3) ↔ validation receipt (T8); no canonical
   commit anywhere (no route touches `compare_and_swap`/HEAD). Codex's nine findings: #1 chain-first (T5–T8
   are the core; the read side is T2–T4 only as prerequisites), #2 generated client (T9), #3 single gateway
   (old backend deleted in T9), #4 `open_located_project` (T2), #5/#6 receipts + SHA-checked download (T3),
   #7 property + base attach (T2, verified), #8 no default path (T1), #9 visible errors (T9 rule).
2. **Placeholder scan:** each task names files, interfaces, mechanisms and test assertions; code for the
   kernel-integration steps is specified call-by-call; UI is specified by panel and route binding.
3. **Type consistency:** `stateDigest` is the developed-view digest everywhere (projection, pick, proposal
   base, envelope); `recordDigest` is `record.digest`; artifact identity is the file sha256 in T3, T7 and T9;
   `BLOCKED_NEEDS_HUMAN` is HTTP 422 with `question` in T1's error model and T5's route.

## Calibration results (2026-09-03, executed against a temporary copy of the villa inputs)

- **Symbols:** all 37 kernel/FastAPI symbols this plan names import (`ConditionComparator` members:
  exists/absent/equals/not_equals).
- **C1 — harness candidate run: PASS.** `run_project` with the studio harness workflow + envelope on the
  unchanged villa record: `seat_execution_complete=True`, seats `seat-structure` (1 object) and
  `seat-envelope` (60 objects) `proposal_accepted`, `seat-relation-check` reports present with three-state
  counts (structure: held 1; envelope: 0 checks), **wall time 0.17 s** with export off. Run records written:
  17 kinds including `studio-candidate-workflow`, `studio-candidate-envelope`, `seat-geometry-program`,
  `runner-run-receipt`.
- **C3 — changed candidate: PASS.** `params.height` of `portico-roof-abutment-west` (prism) ×1.2 →
  `state_digest` changes, runner accepts in 0.19 s, both seat program digests differ from baseline.
  **C3b — bad value: visible failure.** Negative height → `ProjectRunnerError: portico-roof-abutment-west
  height must be positive` (typed, surfaced as the job's failure, never swallowed).
- **C2 — validation receipt: PASS with two corrections applied to T8.** With the four validators the receipt
  is `passed=False`: `artifact.evidence_missing` (artifact ids must be in `evidence_refs`) and
  `compatibility.goal_contract_missing` (`RequiredClaimsValidator` is compatibility-only). A stale base
  yields `state.base_mismatch`. T8 now uses three validators and puts artifact ids in `evidence_refs`.
- **C4 — generated client: PASS under TypeScript 5, FAIL under 7.0.2.** `@hey-api/openapi-ts@0.99.0`
  generates `types.gen.ts`/`sdk.gen.ts`/`client.gen.ts` (+ an SSE helper) from a dumped FastAPI schema
  offline; T9 isolates the generator with its own TS 5 install.
- **Digest note:** the temp copy's HEAD sha differs from the real villa's, so its `state_digest`
  (`fccde06f…`) differs from runner-002's `344b2206…` — on the real project the projection reproduces
  `344b2206…` exactly (verified separately).
