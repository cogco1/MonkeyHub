# M026 — Geometry authoring schema contract repair

- Origin: Modify
- Status: Done
- Depends on: P050

## Goal

Repair the provider-facing P050 authoring boundary exposed by the first real
P026 Agent CLI run. The request currently names
`GeometryProposalAuthoringOutput@1` without describing its exact envelope or
`GeometryProgramProposalBody@1` topology, while the parser rejects every field
outside that hidden structure. Make the protocol self-describing and its
bounded repair feedback actionable without adding a building answer or moving
acceptance authority into the model.

## Acceptance

- The request carries an exact machine-readable output contract for the
  envelope and every proposal-body field consumed by the typed parser.
- The contract describes frames, tolerance, operations, assets, semantic
  bindings, assemblies, retirements, and revision preconditions using the
  existing generic vocabulary and typed field shapes only.
- Top-level and nested field drift produces path-specific repair issues that
  enter the next model round; the producer never silently rewrites output.
- A request-only scripted provider can author an accepted proposal from the
  supplied records, function contracts, and output contract.
- Proposal-only, no-hard-gate, no-canonical-write, and no-platform authority
  boundaries remain unchanged.
- No massing, opening, room, dimension, typology, material, or platform default
  enters the contract.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `docs/mapping/`

## Tests

- Exact authoring-contract topology and static no-instance-default scan.
- Top-level and nested field-drift repair feedback.
- Request-only scripted-provider accept and repair rounds.
- P050 focused suite, architecture firewall, and full unittest discovery.

## Stop conditions

- Stop if success requires accepting an undeclared key, coercing a malformed
  value, or translating a model-invented schema behind the parser.
- Stop if the output contract begins to prescribe a building instance rather
  than the generic typed protocol.
- Stop before treating M026 verification as P026 Gold; the real CLI run and
  full acceptance chain remain P026 evidence.


## Completion

- Completed: 2026-08-02
- Evidence: Added an exact machine-readable GeometryProposal authoring contract covering every parsed frame tolerance parameter asset binding operation hosted assembly revision and retirement field without instance defaults; top-level and nested drift now return path-specific bounded repair issues and no parser fallback; 7 focused tests, 5-path scope check, ARCHITECTURE PASS (100 files), and 383 full tests with 2 explicit skips passed.
