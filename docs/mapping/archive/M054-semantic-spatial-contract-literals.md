# M054 — Semantic-spatial contract literals

- Origin: Maintenance
- Status: Done
- Depends on: P054, P056, P059

## Goal

Make the model-facing semantic-spatial output contract publish the exact
proposal authority literals already enforced by `SpatialOptionProposal@2`, and
retain the underlying deterministic rejection code and message when production
stops after a successful provider call.

## Write scope

- `archflow/capabilities/semantic_spatial_authoring.py`
- `archflow/runtime/production_compiler.py`
- `tests/test_semantic_spatial_authoring.py`
- `tests/test_production_root_compiler.py`
- `tests/integration/test_multi_building_experiments.py`
- `probes/p062-clinic-case/`
- `probes/p062-experiment-study/`
- `docs/EXPERIMENT_PROTOCOL.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- The machine-facing contract states `proposal_only=true`, `selected=false`,
  `hard_usability_verdict=null`, `design_development_complete=false`, and
  `execution_ready=false` as exact typed literals.
- The contract does not weaken parser validation or grant selection,
  evaluation, execution, persistence, or canonical-write authority.
- A rejected semantic-spatial result reaches the durable production failure
  with its exact typed rejection code and bounded diagnostic message.
- The retained `study-006` failure remains an immutable result; a successor
  live attempt requires a newly frozen code and contract identity.

## Tests

- Contract-literal shape and immutable-authority regression.
- Provider-success malformed authority literal is rejected with the exact
  underlying message.
- Production compiler propagates typed semantic-spatial rejection diagnostics.
- P062 probe reload, architecture firewall, V3 boundary, scope, and diff.

## Stop conditions

- Stop if the parser accepts `false` in place of the required unknown verdict.
- Stop if a rejected model output is normalized or silently repaired.
- Stop if historical experiment evidence is rewritten or replayed under its
  frozen study identity.

## Current evidence — 2026-08-18

- The retained `study-006` provider output set
  `hard_usability_verdict=false`; deterministic replay proves the exact parser
  rejection was `SpatialProposalError: spatial proposal acquired forbidden
  authority`. The generated component tree, volumes, zones, and program
  relations were otherwise present, so this is a contract self-description
  defect rather than a provider or persistence failure.
- The model-facing contract now publishes the five exact typed literals, and
  the production compiler carries the typed rejection code and bounded
  diagnostic into the durable failure boundary.
- Thirty-two focused semantic-spatial, production-root, and promoted-study
  tests pass without normalizing the historical failed output.


## Completion

- Completed: 2026-08-18
- Evidence: Exact proposal authority literals published; typed rejection diagnostics propagated; 32 focused tests and archcheck passed; study-006 retained unchanged
