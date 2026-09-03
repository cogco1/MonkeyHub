# P108 — Vibe modeling client/server frontend (Studio lane)

**Status:** ready — executed by a separate session (新建会话), directed by Kaiwen, not by this repo's main session
**Lane:** productization and componentization
**Depends on:** P102 (StateRecord is the source of truth), P103 (diff data), and — for the sub-second preview —
P107; until P107 lands the frontend tolerates the 37-second Rhino path or works over existing candidates.
**Write scope:** `apps/archflow-studio/` only. Codex is active in `archflow/runtime/`, `archflow/state/` and
`governance/`; the frontend session does not touch those. Kernel gaps are carded, never fixed in passing.
**Retires:** Preview Slice 01 — the stdlib `ThreadingHTTPServer` gateway, the presence-probe `kernel.py`, the
monolithic `App.tsx`, `StageRail`/`CapabilityPanel`, the old launcher, the POST-501 tests and the current page
layout. Retained at tag `studio-preview-slice-01` and described in `docs/mapping/archive/studio-preview-slice-01.md`;
it is not copied into a live `archive/` directory.

## Direction (third and final ruling, 2026-09-03; codex review verified by 新建会话, adopted by Kaiwen)

The product is rebuilt **inside the `apps/archflow-studio` namespace**, not in a top-level tree:

- `web/` — Vite + React, `src/{app,features,viewer,api/generated}`.
- `api/` — `archflow_studio_api/{main.py,routes,application,adapters,transport}` + `tests/`, on
  **FastAPI + Pydantic + uvicorn** (fastapi 0.141.1 with native `fastapi.sse.EventSourceResponse`).
- Pydantic describes **transport DTOs only**. TypeScript types and the client are **generated from OpenAPI**
  (`@hey-api/openapi-ts`); no second hand-written TS contract set.
- FastAPI does BFF work only: request validation, SSE, task lifecycle. Design state, dependencies, validation,
  geometry and commit are all calls into archflow.

**Migrated by moving, never by copying:** `ThreeDmViewport.tsx`, `sceneInspection.ts`, the rhino3dm wasm
sync/build path, the elevation / Z-up / fit-camera logic, and the boundary rules (local-unbound mode, no canonical
write, the browser never imports the kernel). **The six reserved Protocols in `backend/ports.py`** — including
`IntentProvider` ("Translate a user utterance into a proposal candidate, never a commit") — are the seams every
ruling has named: the file retires, the protocols migrate verbatim into `api/archflow_studio_api/ports.py`.

## The four fences (Kaiwen, 2026-09-02), re-pointed to the new namespace

1. **Machine-enforced firewall.** `governance/architecture_policy.json` checks `apps/archflow-studio/api` (and
   still `apps/archflow-studio/backend` until it is gone): importing rhino3dm, numpy, networkx, OCP, build123d,
   shapely, trimesh, scipy, tests, tools or probes there is a `LAYER_AUTHORITY_VIOLATION`. Verified to fire with a
   probe file. The browser bundle never imports archflow. `server/` and `shared/` stay checked as tripwires for the
   rejected top-level layout.
2. **DO-NOT-REBUILD inventory** in the brief, precise to module paths, each marked "import, never reimplement".
3. **Named seams only:** `api/archflow_studio_api/ports.py` (the migrated protocols) and the OpenAPI-generated
   client. Declare a port first, implement by delegation to archflow.
4. **The AGENTS.md rule binds this lane:** one canonical abstraction in, one parallel abstraction out.

One fence the machine cannot hold: **the client never computes geometry** — preview meshes are tessellated by the
backend and pushed. No client-side CSG.

## Strict reuse (verified symbol by symbol)

Project identity via `ProjectVersionRef` / `RunRef` / `BranchRef` / `open_located_project()` /
`FilesystemProjectRepository`; the authored-record binding via `StateRecord.bound_to(run)` — the one sanctioned
path from a portable record to one that can name its state; the tree via `StateRecord` entities and
`DesignStateTree`; parameters and dependencies via `StateRecord.dependency_edges()` and
`OperationalMarkovState.dependencies`; stages via `ProjectStageWorkflow` + `StageEvidencePack`; mutation via
`DecisionOperator` / `compile_decision_operator()` / `compile_nested_decision()`; impact via kernel closure and
invalidation receipts; validation via `validate_submission()` / `ValidationReceipt`; candidates via
`GeometryProgramProposal` and the compiler; commit via `archflow/commit/committer.py` + P036 compare-and-swap (not
opened in round one); viewing via `ThreeDmViewport` and `ViewerAssetProvider`.

**A data pitfall the benchmark exposed:** run `workflow-001` holds two records whose names begin with
`project-stage-workflow-` — the workflow and its freeze receipt. Resolve the workflow by the exact shape
`project-stage-workflow-<64 hex>.json`, never by prefix alone.

**Display rule with kernel backing:** `RelationCheckReport.held` means none violated and does not subsume
unchecked; the kernel also exposes `fully_checked`. Show held / violated / unchecked as three states; the server
issues the advance verdict.

**Kernel gaps carded from the calibration (2026-09-03, raised by 新建会话, verified by the main session):**
P109 — no kernel function applies a typed intent to a `StateRecord@1` (`compile_decision_operator` is typed to
`OperationalMarkovState`); the Studio carries a single-value-replace candidate under that card. P110 —
`canonical_state_from_dict` rejects a State-Record project's HEAD (`CanonicalProjectState@1`, ref-based), so the
validation receipt runs on `CanonicalState(ref=head)` with empty facts and must say so. Round one depends on neither.

**Reference-run rule (defect found in the plan's Task 2):** "the newest run whose receipt is complete" picks
`array-patch-001` on the real villa today (harness and patch experiments also retain complete receipts), and
after the first candidate it would pick `studio-cand-*`. The reference run is either configured explicitly or
selected by a criterion that excludes harness, equivalence, patch and Studio candidate runs; the projection's
digest reproduces the reference receipt (`344b2206…` for `runner-002`) only under the reference run's own id.
*Resolved in the plan (2026-09-03, verified on the villa by both sessions):* `?run=` → `ARCHFLOW_STUDIO_REFERENCE_RUN`
→ newest complete receipt whose workflow is not a harness (`workflow_id` in {equivalence-harness,
studio-candidate-harness}; a `RunnerRunReceipt@1` without `workflow_ref` counts). On the villa that leaves
`runner-002` alone; a Studio candidate can never become the reference by construction.

**Digest scope:** the run id enters both `state_digest` and the program digest (`_StateIdentity`). "Did this edit
change anything" is answered by comparing two records bound to the same run, or the authored content before
binding — never a candidate's digest against the projection's, which differ even for an identical record.
*Resolved in the plan:* projection and candidate DTOs carry `authoredRecordDigest` (the digest before binding;
`c5c7843d…` on the villa) and content change is judged on that alone; the bound digests stay, labelled.

**Candidate execution mode (calibrated):** the villa retains no stage-run envelopes, so candidates run through the
harness pattern of `tools/verify_state_record.py` (a one-stage workflow and envelope retained in the candidate
run); a candidate run without Rhino export takes about 0.2 s and a bad value fails visibly as `ProjectRunnerError`.

## Round-one stopping line

exact HEAD ↔ exact StateRecord ref/base ↔ component and dependency projection ↔ intent on a selected component
↔ typed proposal / `BLOCKED_NEEDS_HUMAN` ↔ impact closure ↔ detached candidate artifact + SHA ↔ validation receipt.
**No canonical commit.** The read side (bind / projection / tree / selection) exists as the front of that chain,
not as a separate browser stage. Live model providers and login identity stay disabled. Missing information stops
visibly with a concrete question; nothing invented.

## Working method (Kaiwen, 2026-09-03)

Planning, review and judging go to a Fable-class session; implementation of a *pinned* plan (routes, fields,
error codes, files, tests all fixed) goes to Opus workers in isolated worktrees, dispatched through the Agent
tool's `model` parameter. Before dispatch the planner dry-runs the plan against real project data; acceptance is
an independent judge script checked against kernel-computed truth, never the worker's own report. The benchmark
that established this: both models scored 29/29 on the same pinned slice; the only defect found was in the plan.

## Acceptance

- [ ] The chain above runs end to end on one configured external project and stops at the validation receipt.
- [ ] Every exported object carries the existing `archflow:*` user-string identity; no second key set.
- [ ] The six protocols exist verbatim in `api/archflow_studio_api/ports.py`; viewer assets are moved, not copied.
- [ ] `python tools/archcheck.py` stays green throughout; no Pydantic model mirrors an archflow schema.
- [ ] Tests: contract, API, stale-base, cross-project rejection, UI state.
- [ ] Tag `studio-preview-slice-01` exists at the last commit where the demo is intact.

## Revision history

- 2026-09-02: opened for `apps/archflow-studio` (brief + four fences).
- 2026-09-03 (first): Kaiwen's four decisions — top-level `/client /server /shared`, FastAPI, `SYMMETRIC_WITH`,
  numbered layers. The kernel items (`SYMMETRIC_WITH`, `layer_by_component`) landed and stand.
- 2026-09-03 (second): an independent review reversed the layout and the FastAPI migration; build in place on
  the stdlib seams. A benchmark of that plan was run by Fable 5.1 and Opus 5 in isolated worktrees (both 29/29
  on the independent judge; measurement only, not merged).
- 2026-09-03 (third, final): codex review adopted — rebuild inside the studio namespace as `web/` + `api/` on
  FastAPI, retire Preview Slice 01 behind a tag, migrate viewer assets and the six protocols by moving.
