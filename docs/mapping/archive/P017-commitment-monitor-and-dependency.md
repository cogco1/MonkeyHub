# P017 — Commitment monitor and dependency propagation

- Origin: Planning
- Status: Completed
- Depends on: P008, P016, M009

## Goal

Validate proposed state transitions against active commitments, compile
temporal progress only where necessary, and convert violations into traceable
findings and repair obligations without letting monitors design geometry.

## Write scope

- `archflow/validation/commitments.py`
- `archflow/validation/README.md`
- `archflow/submission/`
- `tests/test_commitment_monitor.py`
- `docs/mapping/`

## Transition boundary

```text
X_t + CandidateDelta -> proposed X*_t+1
  -> hard commitment monitors
  -> findings + dependency-local obligations
  -> reject / repair / authorized negotiation / accept
```

Simple commitments use direct predicates. Only commitments containing temporal
semantics such as once, until, or eventually receive a finite monitor state in
the operational Markov state.

## Acceptance

- Achievement and maintenance commitments produce distinct monitor behavior.
- Candidate geometry or parameters cannot modify an active commitment.
- Violations name commitment, measurement, threshold, evidence, dependency
  path, and permitted resolution actions.
- Findings become obligations; monitors never make design edits.
- Conditional and blocked obligation lifecycle uses the M009 typed semantics;
  monitors do not encode blockers inside prose or mark obligation references as
  invalidated deliverables.
- Dependency propagation invalidates only named downstream commitments and
  evidence.
- Negotiable commitments can produce a revision proposal but not self-authorize
  it.
- Hard commitment failure cannot be waived by aesthetics or expert advice.
- Monitor state is minimal future-facing data; full history remains external.

## Tests

- Achievement reached and maintenance preserved.
- Locked-grid or capacity maintenance violation.
- Dependency-local invalidation.
- Unauthorized commitment mutation rejection.
- Temporal monitor state round trip.
- Hard-gate separation from soft evaluation.

## Completion evidence

- `archflow/validation/commitments.py` keeps criterion observations bound to
  the exact candidate, branch, and base-state digest.
- Violations compile into typed findings, open/blocked obligations, named local
  dependency impacts, and authority-bound revision proposals without any
  candidate or canonical-state writer.
- Explicit activation evidence and `once_activated` monitor state now follow
  the same lifecycle path; the regression is covered by a serialize/reload
  test.
- `python -m unittest tests.test_commitment_monitor
  tests.test_commitment_contract tests.test_commitment_compiler
  tests.test_operational_markov_compiler tests.test_operational_transition -v`
  passes 41 tests.
- `python -m compileall -q archflow/validation archflow/submission
  tests/test_commitment_monitor.py` passes.
- `python tools/devctl.py check-scope P017 ...` passes for all four touched
  paths.

## Stop conditions

- Stop if every commitment is forced into a temporal automaton.
- Stop if a monitor can edit a candidate or canonical state.
- Stop if dependency propagation requires replaying the full transcript.


## Completion

- Completed: 2026-07-26
- Evidence: 41 related commitment/state/transition tests passed; compileall passed; P017 scope check passed; fixed explicit activation evidence lifecycle regression; docs and typed monitor coverage updated.
