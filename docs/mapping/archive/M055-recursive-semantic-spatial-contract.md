# M055 — Recursive semantic-spatial contract

- Origin: Maintenance
- Status: Done
- Depends on: P054, P056, P059

## Goal

Publish the exact recursive JSON shapes already enforced by
`SpatialOptionProposal@2` so a detached model can author the typed proposal
without guessing nested fields, while preserving strict parsing and project
ownership of every architectural value.

## Write scope

- `archflow/capabilities/semantic_spatial_authoring.py`
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

- The model contract exposes exact fields and primitive/container types for
  grid basis, footprint cells, levels, massing volumes and bounds, zones,
  components, connections, and constraint responses.
- Component maturity and constraint response enums are explicit; logical refs
  and local ids remain distinguished without publishing any building answer.
- A regression cross-checks every published nested field set against a real
  `SpatialOptionProposal.to_dict()` structure so contract drift fails locally.
- Strict parser behavior and the retained `study-007` failure remain unchanged;
  a live successor uses a new source and contract identity.

## Tests

- Recursive field/type contract and serializer cross-check.
- Existing malformed, topology, ownership, source, and authority negatives.
- P062 historical reload, architecture firewall, V3 boundary, scope, and diff.

## Stop conditions

- Stop if the contract embeds project geometry, typology, parameters, or a
  sample building answer.
- Stop if output is normalized, repaired, or accepted outside the existing
  typed parser.
- Stop if historical experiment records are rewritten.

## Current evidence — 2026-08-18

- `study-007` proves the five fixed authority values are now understood: the
  provider returned `hard_usability_verdict=null`. Its next exact rejection was
  `SpatialProposalError: spatial grid basis schema drifted` because the prior
  contract exposed only top-level proposal fields and left all nested JSON
  shapes implicit.
- `SemanticSpatialAuthoringContract@2` now publishes exact generic fields and
  primitive/container types for every nested proposal structure, plus explicit
  component-maturity and constraint-response enums. It contains no project
  values, geometry, parameters, or typology.
- A serializer-backed regression cross-checks all published nested field sets;
  nineteen focused tests pass with the strict parser unchanged.


## Completion

- Completed: 2026-08-18
- Evidence: Recursive generic SpatialOptionProposal contract published as @2; serializer-backed field-set regression; 19 focused tests and archcheck passed; study-007 retained unchanged
