# M040 - Machine-readable geometry function contracts

- Origin: Modify
- Status: Done
- Depends on: M039, P050

## Goal

Replace ambiguous colon-encoded geometry function hints with exact structured
parameter and input-arity contracts, and reject typed but unsupported function
parameters before compilation or realization.

## Acceptance

- Every declared geometry operation kind publishes structured minimum/maximum
  input arity and parameter records.
- Each parameter record identifies its name, `GeometryParameterKind`, required
  status, exact unit, and any allowed canonical `value_json` strings.
- The request explicitly distinguishes the parameter `kind` field from a
  semantic choice encoded in `value_json`.
- Typed proposals with missing, extra, wrong-kind, wrong-unit, or disallowed
  function parameters are rejected with an actionable repair issue.
- The generic kernel gains no building-type answer, fallback geometry, hard
  gate, or canonical-write authority.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Declared function vocabulary and structured curve/solid contracts.
- Disallowed typed curve basis becomes a persisted repair issue.
- Existing accepted proposal compilation and realization.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the change requires embedding architectural semantics in generic
  geometry functions, accepting undeclared parameters, or weakening compiler
  or realization gates.


## Completion

- Completed: 2026-08-10
- Evidence: Published exact structured input-arity and parameter contracts for every neutral geometry function, distinguished parameter kind from canonical semantic value_json, and added pre-compile rejection for missing extra wrong-kind wrong-unit and disallowed values; 26 focused tests, architecture firewall, and deterministic full discovery passed.
