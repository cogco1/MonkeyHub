# M037 — Terminal geometry and walkability contract

- Origin: Modify
- Status: Done
- Depends on: M036, P048, P050

## Goal

Publish terminal-geometry occupancy and project-required walkable-void
semantics at the geometry authoring boundary. A model must know whether an
operation output is physical material or an intermediate construction object
before it can author a usable spatial proposal.

## Acceptance

- Every geometry request publishes the exact terminal-object rule used by
  deterministic sandbox realization.
- A terminal `solid` is described as occupied material volume rather than an
  abstract room or envelope.
- Boolean and host-cut scope is explicit so cutting one host cannot imply
  cutting an unrelated terminal solid.
- The supplied `commitment:maintain-egress` publishes a requirement for at
  least one connected walkable region with supported clearance.
- Human guidance is emitted from the same machine-readable realization
  contract.
- Runtime realization and usability remain the proof authorities; the
  framework authors no geometry or void.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Terminal physical-object and solid-occupancy contract publication.
- Maintain-egress walkable-region requirement publication.
- Instruction equality with the machine-readable realization contract.
- Existing proposal acceptance and rejection behavior remains unchanged.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if a realization requirement cannot be traced to deterministic P048
  semantics or a supplied commitment.
- Stop before framework-authored geometry, an inferred void, an extra retry,
  or a weaker realization/usability gate.


## Completion

- Completed: 2026-08-09
- Evidence: Published P048 terminal physical-object, terminal-solid occupancy, boolean and host-cut scope semantics plus the supplied maintain-egress walkable-region requirement from one machine-readable contract; runtime usability remains authoritative; 36 focused/integration tests, architecture firewall, and deterministic full discovery passed.
