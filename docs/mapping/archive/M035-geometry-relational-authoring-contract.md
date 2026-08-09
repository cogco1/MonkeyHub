# M035 — Geometry relational authoring contract

- Origin: Modify
- Status: Done
- Depends on: M032, P050

## Goal

Publish and enforce the geometry predecessor and hosted-assembly relations
required by the compiler at the model authoring boundary. A model
must receive these relations before authoring rather than discover hidden
compiler semantics after producing an otherwise shape-correct proposal.

## Acceptance

- Every geometry-authoring request publishes the exact available predecessor
  digest and constrains `proposal_body.predecessor_program_digest` to that
  value, including `null` for an initial proposal.
- The machine-readable cross-field contract states that an assembly host object
  remains distinct from every member object.
- The machine-readable cross-field contract states that every `host_cut`
  member object is produced by an operation whose `input_object_ids` contains
  the assembly `host_object_id`.
- Human-readable instructions are generated from the same relations and do not
  add a hidden alternate rule.
- Initial and prior-program proposals retain exact compiler behavior with no
  inferred predecessor or geometry fallback.
- Existing persisted P026 receipts remain read-only and reloadable.
- Compiler hard gates, retry count, provider identity, and canonical-write
  authority remain unchanged.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Initial predecessor-null publication and Schema constraint.
- Prior-program predecessor-digest publication and Schema constraint.
- Hosted-cut dependency cross-field contract publication.
- Accepted and rejected proposal compiler behavior remains unchanged.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the relation cannot be derived from the supplied prior program and
  typed assembly contract.
- Stop if repair would require inventing geometry, weakening compiler gates,
  increasing live repair rounds, or rewriting persisted P026 receipts.


## Completion

- Completed: 2026-08-09
- Evidence: Published exact predecessor const and shared machine-readable hosted-assembly relational invariants at the provider boundary; retained compiler gates and bounded rounds; 22 focused producer tests, architecture firewall, and deterministic full discovery passed.
