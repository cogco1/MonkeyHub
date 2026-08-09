# M038 — Realization threshold contract

- Origin: Modify
- Status: Done
- Depends on: M037, P026, P050

## Goal

Carry exact project-derived realization thresholds into geometry authoring
without transferring hard-gate authority or inventing geometry. The geometry
model should receive the same quantitative target that the next deterministic
validator will apply.

## Acceptance

- The geometry producer accepts only exact, deterministic
  `GeometryRealizationRequirement@1` values with canonical JSON thresholds and
  supplied source references.
- P026 derives footprint, clear-height, circulation, entrance, and per-function
  zone thresholds from the current model-authored proposal and candidate
  values.
- Continuous meter requirements use the same conservative voxel-quantization
  source as runtime validation.
- Every geometry request publishes the exact threshold records and instructs
  the model to treat them as hard targets.
- Runtime realization, usability, and use-scenario validators remain the only
  proof authorities.
- No requirement supplies geometry operations, topology, component identities,
  or a framework-authored building answer.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/runtime/sandbox_gold.py`
- `tests/test_geometry_proposal_producer.py`
- `tests/integration/test_sandbox_gold.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Exact requirement schema, canonical threshold, and source validation.
- Geometry-request publication and deterministic ordering.
- P026 meter-to-cell footprint, clearance, circulation, and entrance targets.
- Per-function observed-region target publication.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if a threshold cannot be derived from the current proposal, candidate
  values, or declared validation resolution.
- Stop before framework-authored geometry, hard-gate transfer, non-canonical
  thresholds, an extra model retry, or persisted-record rewriting.


## Completion

- Completed: 2026-08-10
- Evidence: Added exact canonical GeometryRealizationRequirement@1 inputs, derived P026 footprint clearance circulation entrance and use-zone targets from current model semantics using the same conservative voxel quantization, and published them without hard-gate authority; 37 focused/integration tests, architecture firewall, and deterministic full discovery passed.
