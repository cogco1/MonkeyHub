# P049 — Neutral geometry function evaluators

- Origin: Planning
- Status: Done
- Depends on: P047, P048

## Goal

Implement deterministic sandbox evaluators for the geometry function
vocabulary the type layer already declares, so REVOLVE, EXTRUSION, LOFT,
SWEEP, TRANSFORM, ARRAY, and non-polyline curves stop being
compile-only names. The kernel gains mathematics, never building-type
answers: a cylinder is a function of profile and axis; which cylinder a
project needs remains Architect-derived project data.

## Acceptance

- REVOLVE and EXTRUSION realize as analytic representations with exact
  point-containment tests (cylinder, cone, frustum, and prism become
  expressible without mesh approximation).
- LOFT and SWEEP realize as deterministically tessellated meshes bounded
  by the explicit asset vertex/face limits and declared loss codes.
- TRANSFORM and ARRAY compose affine placement and bounded replication
  without introducing new geometry authority.
- CURVE gains an explicit basis parameter (polyline | bezier) with
  tolerance-bounded deterministic sampling.
- Voxel derivation, five-view rendering, and use-scenario validation
  consume every new representation through the existing observation
  contract without new instance defaults.
- Realization stays deterministic: identical programs produce identical
  scene, view, and render digests across independent runs.
- The realization `_SUPPORTED` set and the declared
  `GeometryOperationKind` vocabulary converge; any remaining gap is a
  typed rejection with an explicit loss code, never silence.

## Write scope

- `archflow/state/geometry_program.py`
- `archflow/realization/`
- `archflow/adapters/sandbox_render.py`
- `tests/`
- `docs/mapping/`

## Tests

- Per-evaluator unit tests with analytic ground truth (containment,
  bounds, tessellation counts).
- Double-run digest equality for every new representation.
- Voxel containment property tests against analytic solids.
- Static no-instance-default scan.

## Stop conditions

- Stop if any evaluator requires a building-type default, fixed
  dimension, or palette to function.
- Stop if determinism across runs cannot be proven for a new
  representation.
- Stop before widening asset payload authority or bypassing the
  explicit vertex/face bounds.


## Completion

- Completed: 2026-08-02
- Evidence: Machine verification passed architecture firewall and the complete unittest suite.
- Evidence: 373 repository tests passed with 2 explicit external skips; 23 focused geometry/compiler/realization tests passed.
- Evidence: Evaluator tests prove analytic containment and bounds, bounded deterministic loft/sweep tessellation with loss codes, affine transform/array composition, observation consumption, and independent scene/view/render digest equality.
- Evidence: Static scan of the new evaluator branches found no building-type, platform, palette, or material defaults.
