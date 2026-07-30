# M008 — Retire the minimal OperationalMarkovState@1 overclaim

- Origin: Modify
- Status: Ready after P041
- Depends on: P041

## Goal

Turn the existing runtime `OperationalMarkovState@1` and
`TransitionProposal@1` path into an explicit compatibility seam over P041,
without leaving two competing operational-state authorities or claiming that
the minimal V1 tuple is a sufficient design state.

## Write scope

- `archflow/runtime/operational_transition.py`
- `archflow/runtime/__init__.py`
- `tests/test_operational_transition.py`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Acceptance

- V1 is labelled compatibility-only and no longer makes an unqualified
  Markov-sufficiency claim.
- New design controllers consume P041 types rather than extending V1.
- Existing P008/P009-compatible traces still load through one explicit
  adapter, or fail with a versioned migration error.
- V1 cannot create commitments, dependency closure, phase completion, or
  canonical authority by implication.
- No duplicate reducer, persistence authority, or hidden building default is
  introduced.

## Tests

- Existing operational-transition regression suite.
- V1-to-P041 compatibility or explicit migration rejection.
- Duplicate-authority and instance-literal scans.

## Stop conditions

- Stop if compatibility requires silently inventing absent V1 state.
- Stop if the adapter weakens exact-base or verified-observation checks.


## Completion

- Completed: 2026-07-25
- Evidence: Renamed the V1 runtime classes as explicit Legacy compatibility types while preserving source aliases and trace schemas; added fail-closed require_compiled_operational_state boundary that accepts V2 and rejects V1 automatic migration; documented missing factors and preserved exact-base observation behavior; 118 tests passed with 1 explicit external smoke skip, compileall, scope, map, and literal scans passed.
