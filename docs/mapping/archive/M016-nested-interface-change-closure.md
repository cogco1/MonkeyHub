# M016 — Nested interface change closure

- Origin: Modify
- Status: Done
- Depends on: P022, P041

## Goal

Make nested interface propagation observe every logical state change instead
of maintaining a partial hand-written list of `StateDelta` fields.

## Write scope

- `archflow/state/design_state.py`
- `tests/test_design_controller.py`
- `governance/work_registry.json`
- `docs/mapping/`

## Acceptance

- Facts, bindings, locks, commitments, obligations, dependencies,
  invalidations, and evidence contribute their changed logical references.
- Discharging an obligation reopens an interface that explicitly cites that
  obligation.
- Lock and dependency changes are visible to explicitly named interfaces.
- Unchanged siblings remain closed.
- The repair does not invent unnamed or transitive architectural dependencies.

## Tests

- Obligation lifecycle interface propagation.
- Lock and dependency interface propagation.
- Existing local-only nested-state closure.
- Full regression.

## Stop conditions

- Stop if propagation requires guessing an undeclared relationship.
- Stop if the change mutates a target node rather than reporting that its
  explicitly declared interface must reopen.


## Completion

- Completed: 2026-07-28
- Evidence: State-diff change extraction now covers facts, bindings, locks, commitments, obligations, dependencies, invalidations, and evidence; obligation, lock, and dependency interface regressions pass; architecture firewall, 17 focused tests, 311 full tests with 1 explicit external smoke skip, and compileall passed.
