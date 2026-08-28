# M053 — Provider-success rejection recovery

- Origin: Maintenance
- Status: Done
- Depends on: P056, P059

## Goal

Persist and reload a P056 attempt when P053 returned a successful provider
receipt but deterministic semantic, spatial, or geometry validation rejected
the proposal, then let P062 classify that attempt honestly without claiming a
terminal building.

## Write scope

- `archflow/project/production_transition.py`
- `archflow/runtime/production_runtime.py`
- `archflow/evaluation/experiment.py`
- `tests/test_production_transition.py`
- `tests/test_production_root_compiler.py`
- `tests/test_experiment_protocol.py`
- `tests/integration/test_multi_building_experiments.py`
- `probes/p062-clinic-case/`
- `probes/p062-experiment-study/`
- `docs/EXPERIMENT_PROTOCOL.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A failed production attempt may retain either a failed provider receipt or
  successful P053 evidence followed by a deterministic pipeline rejection.
- Provider success plus pipeline rejection never creates a production
  checkpoint, lifecycle successor, terminal building, fallback, or canonical
  write.
- Reload preserves exact P053 envelopes, attempt identity, rejection error,
  and retry lineage without rewriting historical provider-failure records.
- P062 can close an early pipeline rejection with successful provider
  receipts, zero or partial terminal evidence, typed unknown measurements, and
  a failed—not completed—assignment lifecycle.
- The unrecoverable study-005 controller incident is retained as a diagnostic
  only; a new code-identified study performs the next authorized attempt.

## Tests

- Successful-provider envelope persistence and reload as a rejected production
  attempt.
- Runtime conversion of deterministic post-provider rejection into a durable
  `ProductionRuntimeStepFailed` boundary.
- Experiment pipeline-rejection receipt with no invented terminal evidence and
  an ineligible all-unknown outcome.
- P062 probe reload, architecture firewall, V3 boundary, scope, diff, and
  relevant full discovery.

## Stop conditions

- Stop if provider success is reported as building completion.
- Stop if rejection evidence is reconstructed from console text or a lost
  process transcript.
- Stop if the repair weakens completed-transition checkpoint requirements,
  P053 validation, P036 authority, no-fallback, or experiment comparability.
- Stop before replaying the same frozen study attempt; a source change requires
  a new preregistration and code identity.

## Current evidence — 2026-08-18

- `ProductionFailedAttemptReceipt@1` now distinguishes provider failure from
  deterministic rejection after a successful P053 envelope. Both paths remain
  failed production attempts and neither creates a production checkpoint.
- P062 can retain `pipeline_rejected` with successful provider evidence and
  zero terminal records, then produce only ineligible typed-unknown metrics.
- The historical `study-005` process observed provider success followed by
  `spatial_authoring.proposal_rejected`, but the old runtime lost the envelope
  while trying to archive it. A P036 diagnostic records only those known facts,
  explicitly refuses receipt reconstruction, and claims no building or result.
- `study-006` freezes the repaired source and contract identities, the unchanged
  authorized 300 s provider profile, and nine planned assignments before any
  new provider invocation.


## Completion

- Completed: 2026-08-18
- Evidence: 47 focused production/experiment/integration tests passed; devctl verify M053 PASS; study-005 diagnostic and study-006 preregistration retained through P036
