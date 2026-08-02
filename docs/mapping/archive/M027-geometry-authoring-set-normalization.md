# M027 — Geometry authoring set normalization

- Origin: Modify
- Status: Done
- Depends on: M026, P050

## Goal

Repair the exact P050 provider boundary exposed by real P026 run
`gold-agent-cli-004`. Distinguish non-semantic ordering differences in JSON
lists from substantive dependency contradictions, while retaining strict
duplicate rejection and all existing authority boundaries.

## Acceptance

- Unique identifier/reference lists that represent sets are canonicalized to
  lexical order before typed construction.
- Duplicate identifiers/references remain malformed output; normalization
  never silently removes them.
- `responds_to_object_ids` remains a subset of `input_object_ids`, and
  `responds_to_binding_ids` remains a subset of `semantic_binding_ids`.
- The machine-readable authoring contract states those cross-field invariants
  and the empty-input consequence for zero-input geometry functions.
- No geometry, dimension, component, material, or platform answer enters the
  capability contract.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `docs/mapping/`

## Tests

- Unsorted unique set-like JSON fields canonicalize deterministically.
- Duplicate set-like fields are rejected with bounded repair feedback.
- Response-subset contradictions remain hard, path-specific repair issues.
- Focused P050 tests, architecture firewall, and full unittest discovery.

## Stop conditions

- Stop before changing a semantic dependency or deleting a response reference
  on the provider's behalf.
- Stop if normalization would accept duplicates or undeclared fields.
- Stop before treating M027 as P026 Gold or increasing live retry counts.


## Completion

- Completed: 2026-08-02
- Evidence: Canonicalized unique order-insensitive provider identifier/reference arrays without deduplication; retained duplicate and response-subset hard rejection; exposed machine-readable response/input/binding invariants; 10 focused tests, scope check, architecture firewall, and formal three-command verification passed. Real run gold-agent-cli-004 remains rejected evidence and was not retried.
