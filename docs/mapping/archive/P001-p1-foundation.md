# P001 — P1 contracts and fake walking skeleton

- Origin: Planning
- Status: Done
- Depends on: P000

## Goal

Implement the smallest executable proof of the V4 boundary: immutable canonical
state, isolated working metadata, candidate submission, read-only validation
and evaluation, single-writer compare-and-swap commit, and a one-step Fake
Architect run.

## Acceptance

- Canonical state contains decision-relevant facts and references, never raw
  transcript or tool traffic.
- A submission binds an exact base state.
- Review components cannot mutate canonical state.
- Rejected or stale submissions leave the store unchanged.
- The standard-library test suite proves one accepted and one rejected run.

## Excluded

Real MCP, real LLM calls, aesthetic authority, multi-agent scheduling,
cross-process persistence, and replay claims.


## Completion

- Completed: 2026-07-24
- Evidence: 14 standard-library unit tests passed
- Evidence: accepted CLI smoke advanced canonical state 0 to 1
- Evidence: rejected CLI smoke preserved canonical state at version 0
- Evidence: compileall and write-scope checks passed
