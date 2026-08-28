# M059 — Bounded schematic-selection context

- Origin: Maintenance
- Status: Done
- Depends on: M058, P059

## Goal

Give the detached selector a deterministic decision projection of each already
validated option instead of replaying the full recursive ownership tree, while
keeping the exact option-set digest and returning the untouched selected option.

## Write scope

- `archflow/runtime/production_compiler.py`
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

- Selection receives a deterministic summary of program organization, massing,
  topology, component roles, zones, and tradeoff rationale for each option.
- Each summary binds the exact immutable proposal and option digests; selection
  still returns one id from the original validated option set.
- Full option records remain durable P036 evidence and are neither rewritten nor
  reconstructed from the decision projection.
- `study-011` remains an immutable two-success/one-timeout result and a successor
  freezes the new projection contract before another provider call.

## Tests

- Projection determinism, identity binding, bounded content, and option recovery.
- Selection contract, stale/unknown selection rejection, P062 historical reload.
- Architecture firewall, V3 boundary, scope, and diff.

## Stop conditions

- Stop if the projection becomes a second option or ownership authority.
- Stop if the selector can mutate, rank, or synthesize alternatives.
- Stop if historical study evidence is rewritten.

## Current evidence — 2026-08-18

- `study-011` retained two successful semantic-spatial calls and one schematic
  selection timeout. The exact provider durations were 185,139 ms, 258,984 ms,
  and 342,157 ms; the last receipt records `model.timeout` against the configured
  300-second boundary. No terminal building or comparable sample is claimed.
- The timed-out selection request carried 21,055 input bytes because it replayed
  both complete recursive options. The deterministic decision projection retains
  functional organization, massing, topology, component, zone, connection, risk,
  and exact digest identity while dropping repeated provenance and authority data.
- Applied to the retained `study-011` option set, the option portion contracts
  from 19,739 to 9,566 bytes (51.538 percent) without changing either option.
- Twenty-six focused production-root and promoted-study tests pass.


## Completion

- Completed: 2026-08-18
- Evidence: Study-011 retained two successes plus exact selection timeout; digest-bound decision projection reduces retained option payload 19739 to 9566 bytes without changing options; 26 focused tests and archcheck passed
