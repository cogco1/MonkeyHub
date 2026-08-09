# M039 — Thin-geometry voxelization

- Origin: Modify
- Status: Done
- Depends on: M038, P048

## Goal

Make derived sandbox voxels retain thin terminal material without treating
openable door leaves as permanent walkability obstructions. Exact analytic and
mesh scene geometry remains canonical; the change is limited to the lossy
validation view.

## Acceptance

- A physical analytic AABB occupies every voxel cell with positive-volume
  overlap, rather than only cells whose centers lie inside it.
- Non-AABB geometry uses bounded overlap-local sampling and never falls back to
  its whole bounding box.
- Openable door-leaf geometry remains in the canonical `HybridScene` but is
  excluded from the static derived occupancy set.
- Host-cut opening evidence is sampled with the same overlap-aware method.
- Thin floors and walls survive 1m validation voxelization while exact scene
  geometry remains canonical.
- Voxel budgets, support graphs, usability gates, and canonical-write authority
  remain unchanged.

## Write scope

- `archflow/realization/sandbox.py`
- `tests/test_sandbox_realization.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Thin analytic slab positive-overlap occupancy.
- Exact boundary contact does not occupy an adjacent cell.
- Mesh occupancy remains geometry-sampled rather than bounding-box-filled.
- Existing room opening, walkability, and support gates.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the fix requires replacing retained exact geometry with voxels,
  filling arbitrary mesh bounding boxes, weakening occupancy/usability gates,
  or changing canonical-write authority.


## Completion

- Completed: 2026-08-10
- Evidence: Changed derived voxel occupancy from center-only to positive-overlap analytic sampling with bounded overlap-local sampling for non-AABBs, retained door leaves in the scene while excluding them from static occupancy, and proved run-014 offline improvement from 4/2/0 occupied-walkable-openings to 60/17/4; 21 focused/integration tests, architecture firewall, and deterministic full discovery passed.
