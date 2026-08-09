# M044 - Incremental geometry predecessor

- Origin: Modify
- Status: Done
- Depends on: M043, P026, P047, P050

## Goal

Make a revised sandbox candidate a true geometry-state transition by supplying
the exact rejected compiled geometry program as its predecessor instead of
starting another unrelated initial proposal.

## Acceptance

- A geometry request with `prior_program` publishes both its exact digest and
  complete compiled predecessor program.
- The authoring contract requires stable identities, revision preconditions,
  retirements, and dependency-response acknowledgements relative to that
  predecessor.
- P026 passes the rejected concept geometry program into revised geometry
  production while retaining the rejected candidate record as evidence.
- The compiler remains the authority for exact predecessor, stale revision,
  retirement, and dependency-change validation.
- Initial concept geometry has no predecessor and no fabricated lifecycle
  records.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/runtime/sandbox_gold.py`
- `tests/test_geometry_proposal_producer.py`
- `tests/integration/test_sandbox_gold.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Exact full predecessor and digest publication.
- Rejected candidate geometry becomes revised candidate predecessor.
- Deterministic revision preconditions and retirement for a removed blocker.
- Accepted/rejected candidate persistence and reload.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the change would infer a predecessor, mutate the rejected program,
  silently rebase stale geometry, or bypass compiler lifecycle checks.


## Completion

- Completed: 2026-08-10
- Evidence: Published the complete compiled predecessor beside its exact digest, passed rejected concept geometry into revised P026 production, and kept both producer and runtime consistency compilation bound to the same prior; deterministic tests prove exact revision preconditions and blocker retirement; 39 focused/integration tests, architecture firewall, and full discovery passed.
