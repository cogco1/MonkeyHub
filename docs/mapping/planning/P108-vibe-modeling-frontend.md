# P108 — Vibe modeling client/server frontend (Studio lane)

**Status:** ready — executed by a separate session (新建会话), directed by Kaiwen, not by this repo's main session
**Lane:** productization and componentization
**Depends on:** P102 (StateRecord is the source of truth), P103 (diff data), and — for the sub-second preview —
P107; until P107 lands the frontend works read-only over existing runs or tolerates the 37-second Rhino path.
**Write scope:** `apps/archflow-studio/` only. Codex is active in `archflow/runtime/`, `archflow/state/` and
`governance/`; the frontend session does not touch those. Kernel gaps are carded, never fixed in passing.
**Retires:** the studio's read-only-preview limitation — the phase-2 C/S vertical slice is built *in place* on
the studio's reserved seams. Nothing parallel is created and nothing existing is abandoned.

## What it is

The user's full specification is retained verbatim at
`docs/claude-worktree/2026-09-02-vibe-modeling-frontend-spec.md`: natural-language intent, semantic design
state, dependency-aware mutation, task decomposition, validation, geometry, .3dm, human review — with the design
state canonical and the .3dm an artifact. The merged handoff brief (spec plus fences plus repo inventory plus
mapping) is `docs/claude-worktree/2026-09-02-vibe-modeling-frontend-brief.md`.

## The four fences (Kaiwen, 2026-09-02)

1. **Machine-enforced firewall.** `governance/architecture_policy.json` now checks
   `apps/archflow-studio/backend`: importing geometry or graph libraries there (rhino3dm, numpy, networkx, OCP,
   build123d, shapely, trimesh, scipy) or tests/tools/probes is a `LAYER_AUTHORITY_VIOLATION`. Verified to fire:
   a probe import failed archcheck, then was reverted. The reverse fence already stands in the Studio README:
   the browser bundle never imports archflow.
2. **DO-NOT-REBUILD inventory** in the brief, precise to module paths, each marked "import, never reimplement".
3. **Named seams only:** `backend/ports.py`, `backend/contracts.py` (paired one-to-one with
   `src/contracts/studio.ts`), and `src/gateway`. Declare a port first, then implement it by delegation to
   archflow. The reserved `IntentProvider` protocol ("Translate a user utterance into a proposal candidate,
   never a commit") is implemented where it already sits.
4. **The AGENTS.md rule binds this lane too:** one canonical abstraction in, one parallel abstraction out.

One fence the machine cannot hold, stated here instead: **the client never computes geometry — preview meshes
are tessellated by the backend and pushed.** No client-side CSG.

## Genuinely new things this lane builds (per the spec)

The mutation engine (state patches with protected sets, mutation preview, the leakage metric), the planner and
scheduler over the record's dependency closure, the intent parser at the reserved `IntentProvider` seam, and the
review UX. What is NOT new: the state model, dependency graph, producers, validation, export and versioning —
the brief maps every spec section to its existing home.

## Acceptance

- [ ] The spec's demo 1 (four columns to six, protected pediment/axis/width, leakage 0) runs end to end on the
      villa record with a mutation diff and a new immutable version in the P036 repository.
- [ ] The spec's demo 2 (cornice depth plus 20 percent, columns and pediment untouched) likewise.
- [ ] Every geometry object in the exported .3dm carries the existing `archflow:*` user-string identity; no
      second key set (the M088 single-source rule).
- [ ] `python tools/archcheck.py` stays green throughout; no new module duplicates an inventory entry.
- [ ] The mutation-leakage metric is defined and reported per mutation, cleanly enough for the paper's benchmark.

## Kaiwen's four decisions (2026-09-03)

1. **Stack: FastAPI + Pydantic adopted** (spec section 14). One hard rule rides with it: a Pydantic model is a
   transport shape, never a second canonical state. Any model whose fields mirror an archflow schema is the
   duplicate-function module this lane exists to prevent; the server converts at the boundary and delegates.
   The firewall backs this: `server/` and `shared/` are pre-registered checked roots in
   `governance/architecture_policy.json` (geometry/graph libraries and direct `rhino3dm` imports are
   `LAYER_AUTHORITY_VIOLATION` there — .3dm writing lives in archflow adapters and P107). Proven to fire before
   the tree even existed.
2. **Layout: top-level `/client` `/server` `/shared`** per spec section 3. `apps/archflow-studio` is the retained
   demo (see Retires above).
3. **Symmetry is kernel vocabulary now:** `ArchitecturalRelationKind.SYMMETRIC_WITH` landed in
   `archflow/relations/contracts.py` with participant roles `first`/`second`/`axis` — the mirror axis is a
   participant, not a number, per the no-numbers-in-relations discipline. `StateRecord` accepts it immediately
   (the record's kinds are bound to the kernel enum).
4. **Layers: the numbered category scheme** (spec section 15, `00_SITE` … `90_DEBUG`) is the export scheme for
   this lane. Mechanism landed kernel-side: `expected_object_semantics` / `translate_to_rhino_python` /
   `prepare_rhino_three_dm_export` take `layer_by_component`; the layer becomes `<category>::<component>`, so
   identity survives the renumbering. The kernel never guesses a category (P049 neutrality): an unmapped
   component stays visibly on the historical `archflow::` path. **This lane supplies the category map** from the
   record's component tree using the spec's list.

## Revised the same day (2026-09-03, after an independent review Kaiwen adopted)

Decisions 1 and 2 below are **reversed**; 3 and 4 stand. The review — verified symbol-by-symbol against the
code by the main session — concluded that a top-level `/client /server /shared` tree and a FastAPI migration
would create exactly the parallel structures this lane exists to prevent, when the studio's reserved seams were
built for this expansion. So:

- **The phase-2 C/S vertical slice is built inside `apps/archflow-studio`**, along its existing structure:
  `src/contracts/studio.ts` gains read-model / proposal / diff / receipt / event DTOs (transport shapes, no
  domain-model copies); `backend/contracts.py` mirrors them one-to-one; `backend/kernel.py` grows from a
  presence probe into the real read-only/proposal orchestration facade; `backend/server.py` gains HTTP + SSE
  routes on the stdlib server (no FastAPI migration — no reason exists yet); `src/gateway` gains the calls;
  `ThreeDmViewport.tsx` gains semantic pick, candidate overlay and before/after while keeping local read-only
  mode; the `stage: 1 | 2 | 3 | 4` hardcode in `src/ports/studioPorts.ts` is replaced by binding to
  `ProjectStageWorkflow`.
- **The strict reuse mapping is authoritative and was verified** (every symbol exists): project identity via
  `ProjectVersionRef`/`RunRef`/`BranchRef`/`open_located_project()`/`FilesystemProjectRepository`; tree via
  `StateRecord` entities and `DesignStateTree`; parameters and dependencies via `StateRecord.dependency_edges()`
  and `OperationalMarkovState.dependencies`; stages via `ProjectStageWorkflow` + `StageEvidencePack`; design
  mutation via `DecisionOperator`/`compile_decision_operator()`/`compile_nested_decision()`; impact via kernel
  closure/invalidation receipts; validation via `validate_submission()`/`ValidationReceipt`
  (archflow/validation, archflow/commit); candidates via `GeometryProgramProposal` and the existing compiler;
  commits via `archflow/commit/committer.py` + P036 compare-and-swap; viewing via `ThreeDmViewport` and the
  reserved `ViewerAssetProvider`.
- **Forbidden:** new databases, networkx graphs, a MutationEngine/ProjectState/Validator of the studio's own,
  or any parallel version history. The `server/` and `shared/` roots stay in the architecture policy as
  tripwires for the rejected layout.
- **Display rule with kernel backing:** `RelationCheckReport.held` means "none violated" and deliberately does
  NOT subsume unchecked relations; the kernel now also exposes `fully_checked`, and the UI must show
  held / violated / unchecked as three states with the server issuing the advance verdict.
- **Round-one boundary (Kaiwen confirmed):** one server-configured external project root; proposal-only —
  candidate and validation are the stopping line, canonical write, live model providers and login identity stay
  disabled; missing information stops visibly as `BLOCKED_NEEDS_HUMAN` with a concrete question, never a
  silently invented coordinate, relation or piece of architectural knowledge.
