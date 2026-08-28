# M060 — Bounded semantic-spatial self-repair

- Origin: Maintenance
- Status: Done
- Depends on: M059, P059

## Goal

Allow one detached model-authored replacement after deterministic rejection of
a semantic-spatial proposal, using the exact rejected output and diagnostic,
without normalizing, patching, accepting, or persisting a framework-written
building answer.

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

- Only a successful provider response rejected by deterministic proposal or
  context validation may receive one repair request.
- Repair input contains the exact prior output, stable rejection code, bounded
  diagnostic, and unchanged current contracts; it requests a complete replacement.
- Provider failure and request-binding mismatch are never retried internally.
- All failed and accepted semantic authoring receipts and all P053 envelopes are
  retained; only the accepted original typed option enters the option set.
- `study-012` remains immutable and a successor freezes the repair policy first.

## Tests

- Repair feedback identity, unchanged authority, and context binding.
- One-repair success, one-repair exhaustion, and provider-failure no-retry.
- Production receipt retention, P062 historical reload, architecture, V3, scope.

## Stop conditions

- Stop if framework code edits proposal fields or permits normalization.
- Stop if more than one repair is possible per requested option.
- Stop if rejected evidence is discarded or rewritten.

## Current evidence — 2026-08-18

- `study-012` froze the decision projection but stopped before selection. Its
  first P053 call succeeded in 199,500 ms, then deterministic validation rejected
  the proposal with `spatial_authoring.source_rejected` because it cited evidence
  absent from current design state. The 199,662 ms attempt is durably
  `pipeline_rejected`; no terminal building or comparable result is claimed.
- The production root now permits exactly one complete model-authored replacement
  after a bound successful response is deterministically rejected. Exact prior
  output, output digest, request and receipt ids, rejection code, bounded message,
  base state, and unchanged contracts enter the repair request.
- Failed and accepted semantic authoring receipts are persisted as distinct P036
  records. Provider failure or request-binding mismatch stops after one call;
  framework code never patches a proposal field.
- Thirty-nine focused semantic-spatial, production-root, and promoted-study tests
  pass before the card boundary verification.


## Completion

- Completed: 2026-08-18
- Evidence: Study-012 retained exact source rejection; one complete model-authored replacement is allowed only after deterministic rejection; all receipts retained, provider failures not retried; 39 focused tests and archcheck passed
