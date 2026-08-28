# M061 — Alternative option-set boundary

- Origin: Maintenance
- Status: Done
- Depends on: M060, P059

## Goal

Publish prior accepted option identity and decision content when requesting the
next alternative, and convert cross-option set conflicts into typed production
rejections while P053 evidence is still available for P036 archiving.

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

- The second option request receives exact excluded option ids, option digests,
  topology signatures, and a non-authoritative decision projection.
- The provider must author a complete independent alternative; framework code
  does not rename, perturb, or repair an accepted proposal.
- Duplicate option identity, digest, or topology conflicts become stable typed
  production rejections before the runtime loses P053 envelopes.
- `study-013` is diagnostic-only because its envelopes were not retained; no
  experiment receipt is reconstructed from authoring records or console output.

## Tests

- Alternative context identity, authority, and current option binding.
- Distinct alternatives pass; duplicate ids fail with exact typed code.
- Failed authoring receipts and runtime P053 evidence remain persistable.
- P062 historical reload, architecture, V3, scope, and diff.

## Stop conditions

- Stop if framework code changes a provider-authored option id or topology.
- Stop if decision projection becomes a second option-set authority.
- Stop if missing P053 envelopes are reconstructed after process exit.

## Current evidence — 2026-08-18

- `study-013` produced two individually accepted semantic-spatial authoring
  receipts in 189,469 ms and 196,312 ms. Both used
  `clinic-linear-sequence-01`, so option-set construction raised
  `SpatialProposalError: option ids contains duplicates` outside the historical
  typed production-failure boundary.
- The two authoring receipts and embedded model receipts are durable, but their
  P053 authority envelopes were process-local and are not reconstructed. The
  study has a diagnostic and supersession receipt only; it has no experiment
  attempt, outcome, building result, or empirical comparison claim.
- Second-option requests now carry `SpatialAlternativeAuthoringContext@1` with
  exact excluded identity and a non-authoritative decision projection. Cross-
  option conflicts become `spatial_authoring.option_set_rejected` while the
  runtime still owns the P053 envelopes.
- Forty-one focused semantic-spatial, production-root, and promoted-study tests
  pass before boundary verification.


## Completion

- Completed: 2026-08-18
- Evidence: Study-013 retained two accepted duplicate-id authoring receipts but no reconstructable P053 envelopes; second-option exclusion context and typed option-set rejection added; 41 focused tests and archcheck passed
