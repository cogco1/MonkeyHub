# P006 — State-responsive expert capabilities

- Origin: Planning
- Status: Done
- Depends on: P003

## Goal

Define experts as discoverable, bounded capabilities selected from the current
canonical state, observations, and open obligations—not as a fixed pipeline
that limits the primary Architect Agent.

Initial expert roles are program/use, circulation, and
structure/constructibility. Experts advise or propose local deltas; they never
write canonical state.

## Write scope

- `archflow/capabilities/experts.py`
- `archflow/capabilities/README.md`
- `tests/test_expert_capabilities.py`
- `docs/mapping/`

## Acceptance

- Expert selection is driven by current obligations and available evidence.
- An expert receives a detached snapshot and returns structured advice,
  findings, or a proposal bound to the exact base state.
- No expert can execute MCP world writes or call the committer.
- Time, output, retry, and failure behavior are bounded.
- Optional expert failure is fail-open with a receipt; missing evidence is not
  fabricated.
- Adding an expert requires registration, not scheduler rewrites.

## Tests

- Different states discover different relevant experts.
- Detached-input and no-state-mutation tests.
- Timeout, exception, and oversized-output receipts are bounded.
- Registration order does not become execution order.

## Stop conditions

- Stop if expert routing starts encoding a mandatory design sequence.
- Stop after two failures of the same routing or isolation test.


## Completion

- Completed: 2026-07-24
- Evidence: 8 expert capability tests and full 45-test unittest suite pass; P006 scope check passes for 4 paths
