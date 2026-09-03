# P108 — Vibe modeling client/server frontend (Studio lane)

**Status:** ready — executed by a separate session (新建会话), directed by Kaiwen, not by this repo's main session
**Lane:** productization and componentization
**Depends on:** P102 (StateRecord is the source of truth), P103 (diff data), and — for the sub-second preview —
P107; until P107 lands the frontend works read-only over existing runs or tolerates the 37-second Rhino path.
**Write scope:** `client/`, `server/`, `shared/`, and `apps/archflow-studio/` (the last only to mark it as the
retained demo and to harvest its viewer pieces). Codex is active in `archflow/runtime/`, `archflow/state/` and
`governance/`; the frontend session does not touch those. Tests live under `server/tests` and the client's own
test setup — the repo-root `tests/` belongs to archflow.
**Retires:** `apps/archflow-studio` as the product shell. Kaiwen's ruling (2026-09-03): the studio was a
low-cost reproduction demo; the formal product is this lane. The demo stays read-only until its useful pieces
(the rhino3dm-wasm three.js viewer, the launch pattern) are harvested or it is removed; it must not be extended.

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
3. **Named seams only**, relocated with the layout decision: `server/ports.py`, `shared/contracts/`, and the
   client's gateway module. Declare a port first, then implement it by delegation to archflow. The reserved
   `IntentProvider` protocol ("Translate a user utterance into a proposal candidate, never a commit") moves from
   the demo's `backend/ports.py` into `server/ports.py` — moved, not duplicated.
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
