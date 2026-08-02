# M028 — Hosted assembly contract normalization

- Origin: Modify
- Status: Done
- Depends on: M027, P050

## Goal

Repair the provider-facing P050 boundary exposed by real P026 run
`gold-agent-cli-005`. Make the typed door/window role requirements visible in
the machine contract and canonicalize keyed object collections whose order has
no design meaning, without repairing substantive omissions or dependencies on
the model's behalf.

## Acceptance

- Door assemblies declare `clearance`, `frame`, `hardware`, `host_cut`, and
  `leaf` as required roles.
- Window assemblies declare `clearance`, `frame`, `glazing`, `hardware`, and
  `host_cut` as required roles.
- Parameters, assembly members, and proposal-level keyed collections are
  canonicalized by their existing name, role, or identity key.
- Duplicate keys remain malformed provider output.
- Missing roles, invalid function arity, and semantic dependency
  contradictions remain bounded hard repair issues.
- The model remains proposal-only and no project-specific geometry or platform
  answer enters the contract.

## Write scope

- `archflow/state/geometry_program.py`
- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `docs/mapping/`

## Tests

- Unsorted keyed collections canonicalize deterministically.
- Duplicate member roles and identity keys remain rejected.
- Door/window required-role metadata matches typed state behavior.
- Focused producer tests, architecture firewall, and full unittest discovery.

## Stop conditions

- Stop before synthesizing a missing member, geometry object, or dependency.
- Stop if normalization deletes a duplicate or changes a semantic value.
- Stop before increasing P026 live repair rounds or weakening hard gates.


## Completion

- Completed: 2026-08-02
- Evidence: Exposed one typed required_assembly_roles source for door and window contracts; canonicalized provider parameters members frames assets bindings operations assemblies revisions and retirements by existing keys without deduplication; retained duplicate missing-role function-arity semantic-dependency and authority hard rejection; 14 focused tests, 4-path scope, architecture firewall, and formal three-command verification passed after one documented transient performance-gate retry.
