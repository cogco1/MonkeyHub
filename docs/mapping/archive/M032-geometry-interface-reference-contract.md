# M032 — Geometry interface reference contract

- Origin: Modify
- Status: Done
- Depends on: M029, P050

## Goal

Make the geometry provider's machine-readable `interface_refs` contract exactly
match typed-state validation and the interfaces already available in supplied
spatial connections or model-authored semantic-component candidate records, so
a model cannot pass the published contract and then fail on an unstated rule.

## Acceptance

- One exported portable logical-reference pattern drives both typed validation
  and the provider JSON Schema; asset URI syntax remains a separate contract.
- Every geometry-authoring request enumerates interface references derived only
  from supplied spatial-option connections and exact semantic-component
  candidate-value records.
- Every hosted-assembly `interface_refs` value must be a member of that exact
  request enumeration as well as a valid portable logical reference.
- Bare identifiers, machine paths, `file:` URIs, and well-formed but unavailable
  references fail before geometry compilation with a precise persisted issue.
- Existing pre-M032 P026 round records, including run 007, still reload without
  rewriting their stored request payloads.
- No interface is invented, normalized into a different meaning, or selected by
  the framework.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/state/geometry_program.py`
- `archflow/state/operational_state.py`
- `tests/test_geometry_proposal_contract.py`
- `tests/test_geometry_proposal_producer.py`
- `tests/integration/test_sandbox_gold.py`
- `docs/mapping/`

## Tests

- Published Schema patterns equal the typed validators' exported sources.
- Actual request payloads enumerate only supplied spatial relationship and
  semantic-component candidate refs.
- Accepted and rejected provider outputs prove exact membership enforcement.
- Run-007 persisted lineage remains read-only and reloadable.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if enforcement requires inventing an interface absent from project
  records.
- Stop if old persisted receipts must be rewritten.
- Stop before broad issue aggregation, retry-policy changes, or any hard-gate
  relaxation; those are separate concerns.


## Completion

- Completed: 2026-08-04
- Evidence: Published one machine-checkable portable-reference source to typed validation and provider Schema, enumerated only supplied spatial and semantic-component candidate refs, enforced exact membership before compilation, preserved read-only run-007 reload, and passed 32 focused tests, scope, architecture, and formal full-suite verification.
