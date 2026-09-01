# P084 — Parthenon completeness and spatial closure repair

- Origin: user visual-defect report and rebuild request, 2026-08-29
- Status: Done
- Depends on: P079, P080, P081, P082, P083

## Goal

Repair the fail-open path that allowed a formally COMPLETE Parthenon stage to
omit its principal doors and let interior supports intersect the cella
partition, then retain a fresh four-stage reconstruction instead of overwriting
the faulty candidate.

## Boundaries

- Reusable completeness and spatial validation mechanisms live in `archflow/`;
  Parthenon facts and geometry remain project-instance data in the runner and
  retained workspace records.
- Existing `research-001..003` and `reconstruction-001..003` runs remain
  immutable evidence. The repair writes `research-004` and
  `reconstruction-004` only.
- Exact numerical geometry needs an explicit measured, derived, or typed
  candidate basis. Topology-only evidence cannot silently authorize a precise
  coordinate.
- The rebuilt model remains `HOLD`; passing these checks does not grant
  canonical-write or historical-certainty authority.

## Acceptance

1. A typology/stage completeness contract enumerates required decision families
   and fails closed when openings, connectivity, supports, or another required
   family is absent.
2. Parameter-basis validation distinguishes measured, derived, and declared
   candidate numbers from topology-only claims; an exact number backed only by
   topology is rejected.
3. Executable spatial validation checks required components, clear opening
   volumes, forbidden solid intersections, containment, and minimum clearance.
4. Stage readiness and closure booleans are derived from retained evidence,
   geometry, spatial, and convergence receipts; no stage closes its obligation
   before its realized model passes the relevant gates.
5. `reconstruction-004` contains independently accessible east and west cella
   doors, keeps the mutually disconnected rooms, and has no interior-support
   overlap with the cella partition or perimeter walls.
6. Four Z-up metre-unit stage packs compile from one inherited branch identity,
   remain `HOLD`, and are bound by the project progress snapshot and repo anchor.

## Write scope

- `archflow/capabilities/architectural_completeness.py`
- `archflow/capabilities/spatial_validation.py`
- `tools/run_parthenon_reconstruction.py`
- `tests/test_architectural_completeness.py`
- `tests/test_spatial_validation.py`
- `tests/test_parthenon_reconstruction.py`
- `probes/parthenon-reconstruction.anchor.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Verification

- Negative tests: missing required door, topology-only exact coordinate, and
  column-through-wall each fail closed.
- Deterministic four-stage rebuild and headless inspection of the retained 3DMs.
- Project verification command and architecture firewall.

## Stop conditions

- Stop before inventing an exact historical dimension without a typed basis.
- Stop if the new run would overwrite predecessor evidence.
- Stop if a stage can report COMPLETE from record existence or literal booleans
  rather than measured receipts.



## Completion

- Completed: 2026-08-29
- Evidence: project://parthenon-reconstruction/exports/parthenon-progress-snapshot-ac778e607e87f1f9625121f3e70742b28c0dffcc1426365f893d3b98b4f4799a.json
- Evidence: project://parthenon-reconstruction/runs/reconstruction-004/branches/idealized-periclean-original/records/stage-3-spatial-validation-c07db4eb9f3547111bc1f61c5b642378cb4b5fc0802add672d6a968dce7b3969.json
- Evidence: 21 tests PASS; ARCHITECTURE PASS (144 files); live Web RAG 8 HTTP calls and four COMPLETE HOLD packs
