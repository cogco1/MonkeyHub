# P004 — Voxel observation contract

- Origin: Planning
- Status: Done
- Depends on: P003

## Goal

Convert a Minecraft candidate artifact or bounded world scan into a detached,
read-only `VoxelObservation@1` that validators and experts can inspect without
receiving authority to mutate the world or canonical state.

## Write scope

- `archflow/adapters/voxel_observation.py`
- `archflow/adapters/README.md`
- `tests/test_voxel_observation.py`
- `tests/fixtures/voxel/`
- `docs/mapping/`

## Acceptance

- Observation binds the exact artifact digest, workspace, and base state.
- It reports envelope, occupied cells, walkable cells, openings, connected
  interior regions, support relations, and bounded unknowns.
- Extraction is deterministic for saved fixtures.
- Missing or incomplete world data is explicit; it is never silently invented.
- Extraction cannot call build, execute, undo, commit, or other write tools.

## Tests

- Deterministic extraction from a small room and disconnected-room fixture.
- Source-digest mismatch rejection.
- Read-only tool allow-list test.
- Malformed and oversized input produce bounded failures.

## Stop conditions

- Stop if the selected Minecraft backend cannot expose enough block data to
  distinguish interior, opening, and walkable cells.
- Stop after two failures of the same fixture or extraction path.


## Completion

- Completed: 2026-07-24
- Evidence: 6 deterministic voxel-observation tests passed
- Evidence: 32 total standard-library tests passed
- Evidence: compileall and P004 scope checks passed
