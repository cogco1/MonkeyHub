# M058 — Schematic selection contract

- Origin: Maintenance
- Status: Done
- Depends on: P056, P059

## Goal

Publish the exact schematic-selection output contract and preserve exact field
differences when a successful provider response cannot be parsed, without
changing option validation or selection authority.

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

- Selection requests publish exactly four output fields, the fixed output
  schema, the exact current option-set digest, and allowed option ids.
- A provider may choose and justify only; it gains no validation, persistence,
  or canonical-write authority.
- Missing and extra output fields produce a stable exact diagnostic rather than
  a generic `ValueError` label.
- `study-010` remains an immutable three-successful-call failed result; a
  successor gets a new source and contract identity.

## Tests

- Selection prompt contract, valid output, unknown option, stale digest, and
  exact missing/extra field diagnostics.
- Production propagation and P062 historical reload.
- Architecture firewall, V3 boundary, scope, and diff.

## Stop conditions

- Stop if selection can alter supplied options or geometry.
- Stop if the current option-set digest is inferred rather than copied exactly.
- Stop if historical output is normalized or rewritten.

## Current evidence — 2026-08-18

- `study-010` produced two accepted semantic-spatial options and a third
  successful P053 selection response. The selected option id was valid, but
  the model returned `selected_proposal_digest` instead of the required
  `exact_option_set_digest`; the historical attempt remains failed with three
  exact provider receipts and no terminal building.
- Selection prompts now publish four exact fields, fixed schema and option-set
  digest, and the allowed option ids. Field mismatch diagnostics retain exact
  missing and extra names, and production propagates that typed code.
- Twenty-four focused production-root and promoted-study tests pass.


## Completion

- Completed: 2026-08-18
- Evidence: Exact four-field selection contract and stable missing/extra diagnostics; study-010 retains three successful provider calls and two accepted options; 24 focused tests and archcheck passed
