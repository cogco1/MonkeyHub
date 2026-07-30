# P008 — Obligation-driven repair loop

- Origin: Planning
- Status: Done
- Depends on: P005, P006, P007

## Goal

Close the asynchronous design loop: convert failed gates and selected expert
findings into explicit obligations, let the primary Architect decide the next
local repair, and reconcile only a newly submitted candidate.

## Write scope

- `archflow/runtime/repair_loop.py`
- `archflow/runtime/README.md`
- `archflow/submission/`
- `tests/test_repair_loop.py`
- `docs/mapping/`

## Acceptance

- Each iteration begins from one canonical state and an isolated workspace.
- Experts are selected from that state's observation and obligations.
- Findings become traceable obligations without directly editing the design.
- The Architect may revise locally, replace the candidate, or declare an
  unresolved trade-off.
- Only a validated candidate bound to the exact base state reaches the
  single-writer committer.
- The loop has explicit limits for iterations, wall time, repeated findings,
  and no-progress transitions.
- Rejected candidates and expert failures leave canonical state unchanged.

## Tests

- One Red fixture is repaired into Gold in bounded iterations.
- Stale-base, repeated-finding, no-progress, and iteration-limit exits.
- Optional expert failure remains fail-open with evidence.
- Exactly one accepted transition advances exactly one state version.

## Stop conditions

- Stop on two consecutive iterations without a changed artifact or obligation
  set.
- Stop on repeated identical hard findings after one targeted repair.
- Stop when the next action requires user authorship or external world access.


## Completion

- Completed: 2026-07-24
- Evidence: 7 repair-loop tests and full 57-test unittest suite pass; compileall and P008 seven-path scope check pass
