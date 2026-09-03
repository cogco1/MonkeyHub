# P108 — Vibe modeling client/server frontend (Studio lane)

**Status:** ready — executed by a separate session (新建会话), directed by Kaiwen, not by this repo's main session
**Lane:** productization and componentization
**Depends on:** P102 (StateRecord is the source of truth), P103 (diff data), and — for the sub-second preview —
P107; until P107 lands the frontend works read-only over existing runs or tolerates the 37-second Rhino path.
**Write scope:** `apps/archflow-studio/` only. Codex is active in `archflow/runtime/`, `archflow/state/` and
`governance/`; the frontend session does not touch those.
**Retires:** nothing by itself — and that is the point: it must not create parallel abstractions either.

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
3. **Named seams only:** `backend/ports.py`, `backend/contracts.py`, `src/gateway`. Declare a port first, then
   implement it by delegation to archflow.
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
