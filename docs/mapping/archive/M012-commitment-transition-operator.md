# M012 — Commitment lifecycle transitions through DecisionOperator

- Origin: Modify
- Status: Ready
- Depends on: P015, P041, M009

## Goal

Repair the current `DecisionOperator@2` application path so an exact-base
operator can carry a valid lifecycle successor for an existing commitment
without granting arbitrary replacement authority.

## Write scope

- `archflow/state/decision_operator.py`
- `tests/test_operational_markov_compiler.py`
- `docs/ARCHITECTURE.md`
- `docs/mapping/`

## Acceptance

- A commitment already present in operational state may advance only through a
  lifecycle transition accepted by the P015 transition function.
- Owner, named-authority, and immutable revision policies remain fail closed.
- Authorization, release, revision, and supersession preserve commitment
  identity, authority, predecessor/successor lineage, and monitor state.
- An arbitrary same-ID replacement still fails as an identity collision.
- Exact-base compilation, hard-gate separation, and all existing
  `DecisionOperator@2` records remain unchanged.

## Tests

- Proposed commitment authorization.
- Active commitment release.
- Active commitment revision with successor lineage.
- Unauthorized and immutable transition rejection.
- Arbitrary same-ID replacement rejection.
- Existing decision-operator regressions.

## Stop conditions

- Stop if the repair requires a new source of commitment authority.
- Stop if a clarification or model decision can bypass the P015 transition
  function.
- Stop if the change turns a receipt into direct canonical-state mutation.


## Completion

- Completed: 2026-07-25
- Evidence: DecisionOperator@2 now validates existing same-ID commitment updates by replaying the P015 lifecycle transition function with the operator authority; proposed authorization, active release, revision plus successor lineage, and named revision authority pass, while immutable, unauthorized, monitor-rewrite, and arbitrary replacement attempts fail closed; 151 tests passed with 1 external smoke skip, compileall, M012 scope, map rendering, and 5 devctl tests passed.
