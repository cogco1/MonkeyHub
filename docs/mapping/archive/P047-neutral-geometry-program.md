# P047 — Neutral geometry program and hybrid building artifact

- Origin: Planning
- Status: Done
- Depends on: P024, P029

## Goal

Compile Architect-supplied architectural semantics into a deterministic,
platform-neutral geometry program and hybrid building artifact without teaching
the geometry kernel building types, styles, or project answers.

## Write scope

- `archflow/state/geometry_program.py`
- `archflow/runtime/geometry_compiler.py`
- `archflow/state/README.md`
- `archflow/runtime/README.md`
- `tests/test_geometry_program.py`
- `tests/test_geometry_compiler.py`
- `docs/mapping/`

## Acceptance

- Geometry uses typed units, tolerances, coordinate frames, object identity,
  dependencies, and deterministic operation order.
- The kernel vocabulary is limited to generic curves, solids, transforms,
  extrusion, revolve, loft, sweep, arrays, booleans, and asset instances.
- Architectural meaning remains in exact-base semantic bindings and
  commitments, not geometry-kernel conditionals.
- Door and window assemblies bind host cuts, frames, leaves, glazing, hardware
  placeholders, interfaces, and clearance envelopes.
- Detail that is not economical to parameterize uses immutable provenance-bound
  mesh, BRep, profile, heightfield, Rhino block, Revit family, or equivalent
  asset references with explicit transforms and sockets.
- Detail may mature from envelope to functional assembly to fine asset without
  changing unrelated accepted geometry.
- Failed operations and lossy asset substitutions are explicit receipts.

## Tests

- Primitive and operation-graph determinism.
- Door/window hosted assembly and clearance bindings.
- Parametric profile and array cases.
- Provenance-bound detail asset instance and missing-asset Red.
- No building, style, Pantheon, Minecraft, Rhino, or Revit answer in the
  geometry kernel.

## Stop conditions

- Stop if the kernel gains building-type conditionals.
- Stop if LLM prose bypasses typed geometry compilation.
- Stop if an external asset lacks immutable provenance, scale, transform, or
  host/interface binding.

## Implemented contract

- `GeometryProgramProposal@1` now binds exact project/run/base and P024
  candidate identity to typed units, tolerance, frames, generic operations,
  semantic bindings, hosted assemblies, assets, revisions, and retirements.
- The compiler derives deterministic frame, semantic, operation, and stable
  object digests. Changed upstream objects, frames, or semantic values
  invalidate retained dependents until explicitly acknowledged.
- Stable-object changes require exact predecessor-digest preconditions;
  disappearance requires retirement evidence. Unrelated object digests remain
  unchanged.
- Missing assets and malformed graphs return explicit Red receipts. Lossy
  substitution requires a separate requested/replacement digest and loss
  receipt.
- This card does not execute or render geometry. Deterministic realization,
  post-operation observation, use-scenario validation, and paper views remain
  P048/P030 work.


## Completion

- Completed: 2026-07-28
- Evidence: Implemented GeometryProgramProposal@1 and deterministic exact-base compiler with typed units, tolerances, frames, stable object identities, semantic and commitment bindings, hosted assemblies, immutable assets, explicit scale/socket placement, topological ordering, predecessor-digest revision and retirement preconditions, dependency/frame/semantic invalidation acknowledgements, explicit Red issues, and lossy substitution receipts; 11 focused tests and 348 full tests passed with 3 explicit skips, compileall, 95-file architecture firewall, P047 scope, and machine verification passed.
