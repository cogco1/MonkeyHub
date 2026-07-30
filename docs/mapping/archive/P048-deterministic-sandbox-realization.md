# P048 — Deterministic sandbox realization and paper views

- Origin: Planning
- Status: Done
- Depends on: P004, P030, P047

## Goal

Evaluate the neutral geometry program into a reloadable hybrid scene, derive a
bounded voxel validation view, and render paper-ready views without requiring
Minecraft, Rhino, Revit, or another external platform.

## Write scope

- `archflow/realization/`
- `archflow/adapters/sandbox_render.py`
- `archflow/realization/README.md`
- `tests/test_sandbox_realization.py`
- `tests/test_sandbox_render.py`
- `tests/fixtures/geometry/`
- `tests/integration/test_sandbox_use_scenarios.py`
- `docs/mapping/`

## Acceptance

- Program, scene, derived voxel view, semantic bindings, and render outputs have
  exact digest chains.
- Hybrid geometry preserves analytic/mesh detail while the voxel view remains
  a derived resolution-bounded usability representation.
- Local detail may use finer representation without forcing one global voxel
  resolution.
- P030 entrance, route, headroom, level, vertical-circulation, and use-zone
  scenarios run against the exact sandbox candidate.
- Iso, plan, longitudinal section, transverse section, and elevation derive
  from the same accepted scene.
- Rejected, repaired, and accepted realizations reload without an external
  application.

## Tests

- Geometry-program-to-scene determinism.
- Exact scene-to-voxel binding.
- Multi-resolution detail preservation.
- P030 Gold and Red scenarios.
- Deterministic paper views and reload.

## Stop conditions

- Stop if voxelization becomes the only canonical geometry.
- Stop if a renderer silently repairs geometry.
- Stop if external-platform availability becomes a sandbox acceptance
  prerequisite.

## Implemented contract

- `HybridSandboxScene@1` deterministically retains supported analytic CSG,
  curves, immutable mesh payloads, source-object digests, semantic bindings,
  frames, exact base, and workspace. Unsupported operations reject explicitly.
- `DerivedSandboxVoxelView@1` binds the exact scene and realization receipt,
  derives bounded occupancy, walkability, openings, regions, and support, and
  retains finer per-object samples without replacing canonical geometry.
- P030 Gold and isolated-level Red run against a real P048-derived observation;
  the Gold includes an explicit evidence-bound vertical circulation path.
- Five deterministic SVG paper views bind the same scene digest and have no
  repair channel.
- Scene, realization receipt, voxel view, render set, and downstream
  rejected/repaired/accepted disposition records round-trip without an
  external application.
- This first evaluator intentionally supports only typed solid, curve, boolean,
  and immutable mesh-instance operations. Transform, extrusion, revolve, loft,
  sweep, and array realization remain explicit rejected capabilities rather
  than simulated success.


## Completion

- Completed: 2026-07-29
- Evidence: Implemented a deterministic platform-neutral sandbox vertical slice: exact P047 program to reloadable analytic/CSG/mesh HybridSandboxScene@1, explicit unsupported-operation rejection, exact scene-bound DerivedSandboxVoxelView@1 with local detail sampling and retained canonical geometry, five deterministic SVG paper views, reloadable downstream disposition records, and a real P048-to-P030 multilevel Gold/isolated Red using the realization receipt digest; 8 focused tests and 357 full tests passed with 2 skips, compileall, 98-file architecture firewall, P048 scope, and machine verification passed. This does not claim full CAD operation coverage or P026 accepted Gold.
