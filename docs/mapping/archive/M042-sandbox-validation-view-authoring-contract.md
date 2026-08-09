# M042 - Sandbox validation-view authoring contract

- Origin: Modify
- Status: Done
- Depends on: M041, P026, P048, P050

## Goal

Expose the deterministic sandbox's conservative voxel, clear-height, exterior
opening, and host-cut semantics before model geometry is authored.

## Acceptance

- P026 publishes the exact validation voxel resolution as a sourced realization
  requirement.
- Geometry authoring states that every positive-volume physical overlap occupies
  a validation cell.
- Clear height is the positive-Y cell offset to the nearest occupied blocker and
  must meet the supplied integer-cell threshold.
- An exterior entrance aperture must be unoccupied, walkable, and touch an XZ
  boundary column of the derived occupied envelope.
- Every `host_cut` member is a boolean-intersection aperture volume of its named
  host and a cutter, never a residual difference result.
- Residual host material, when needed, is a separate terminal boolean-difference
  output; no runtime geometry is repaired or invented.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/runtime/sandbox_gold.py`
- `tests/test_geometry_proposal_producer.py`
- `tests/integration/test_sandbox_gold.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Residual boolean output rejected as a host-cut aperture.
- Exact P026 voxel resolution requirement publication.
- Existing sandbox opening and clear-height validation.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the change would weaken positive-overlap occupancy, fabricate an
  opening, rotate or repair model geometry after authoring, or move hard-gate
  authority into the proposal producer.


## Completion

- Completed: 2026-08-10
- Evidence: Published the exact 1m P026 validation resolution, positive-overlap occupancy, integer clear-height offset, exterior opening-column, and aperture-volume host-cut rules; rejected residual difference outputs as host cuts before compilation after run 017 exposed both hidden semantics; 39 focused/integration tests, architecture firewall, and deterministic full discovery passed.
