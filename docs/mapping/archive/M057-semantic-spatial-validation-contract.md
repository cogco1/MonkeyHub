# M057 — Semantic-spatial validation contract

- Origin: Maintenance
- Status: Done
- Depends on: P054, P056, P059

## Goal

Expose the exact generic geometric and program-consistency rules already used
by deterministic schematic validation, including inclusive coordinate and area
semantics, so a detached model can satisfy the gate without project-specific
examples or post-hoc output repair.

## Write scope

- `archflow/capabilities/spatial.py`
- `archflow/capabilities/semantic_spatial_authoring.py`
- `tests/test_spatial_proposals.py`
- `tests/test_semantic_spatial_authoring.py`
- `tests/integration/test_multi_building_experiments.py`
- `probes/p062-clinic-case/`
- `probes/p062-experiment-study/`
- `docs/EXPERIMENT_PROTOCOL.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A model-facing validation contract states inclusive X/Y/Z bounds, X/Z
  footprint projection, cell-area calculation, site/range containment, named
  level containment, one-zone-per-function, relationship endpoint/direction,
  and required-response coverage rules.
- Current site envelope, compatible footprint ranges, current function refs,
  and relationship endpoint/direction facts are compiled from the project
  context and retained in the request, not hardcoded in framework defaults.
- Tests cross-check the published derived facts with the exact typed program,
  site, and validator behavior, including the retained projection failure.
- Strict validation is unchanged and no project output is normalized or
  repaired; `study-009` remains immutable and a successor gets a new identity.

## Tests

- Validation-contract determinism, inclusive projection, area, level, zone,
  relationship, and required-response facts.
- Existing spatial negatives and provider-prompt publication.
- P062 historical reload, architecture firewall, V3 boundary, scope, and diff.

## Stop conditions

- Stop if a clinic coordinate, topology, volume, or parameter is placed in
  `archflow/`.
- Stop if the contract and validator disagree on inclusive-coordinate or area
  semantics.
- Stop if failed output is silently altered.

## Current evidence — 2026-08-18

- `study-009` passed strict shape and reference validation. Its retained exact
  failure, `massing volume projects outside the proposed footprint`, exposes
  that inclusive volume-to-footprint and cell-area rules were still implicit.
  The provider used 48 footprint cells at 25 square metres each but declared
  volume bounds whose inclusive X/Z union required more cells.
- `SpatialAuthoringValidationContract@1` now publishes inclusive coordinate,
  area, envelope, level, volume projection, function zoning, relationship, and
  required-response rules with current envelope/range/function/relationship
  facts compiled from project context.
- No clinic geometry or parameter is in framework code and no output repair is
  authorized. Twenty-seven focused tests pass with validation unchanged.


## Completion

- Completed: 2026-08-18
- Evidence: Generic SpatialAuthoringValidationContract publishes inclusive coordinate area envelope level zoning relationship and response rules with current derived facts; 27 focused tests and archcheck passed; study-009 retained unchanged
