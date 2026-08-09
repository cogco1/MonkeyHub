# M036 — Hosted-component binding contract

- Origin: Modify
- Status: Done
- Depends on: M035, P050

## Goal

Publish and enforce dedicated semantic-binding and assembly identity
requirements for hosted model-authored components before geometry acceptance.
This prevents a geometrically plausible proposal from hiding multiple doors,
windows, or hosted families inside one aggregate semantic binding.

## Acceptance

- Hosted-component requirements derive only from supplied candidate values that
  carry `component_id` and a non-null `assembly_kind`.
- Each request enumerates the exact candidate value, interface, and assembly
  kind requiring one dedicated semantic binding and one matching assembly.
- A dedicated binding contains exactly that component candidate value and the
  matching assembly names only that binding.
- Hosted member-object identities are disjoint across components; every host
  and member object remains covered and produced under its dedicated binding.
- Violations persist as bounded repair issues before geometry lineage can be
  accepted, while runtime exact semantic-geometry validation remains intact.
- The framework invents no component geometry, binding, assembly, or object
  identity and weakens no retry or hard gate.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Exact hosted-component requirement publication from supplied candidate
  values.
- Shared aggregate binding rejection before compiler acceptance.
- Non-hosted and ordinary candidate values create no hosted requirement.
- Existing accepted geometry-proposal behavior remains unchanged.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if a requirement cannot be derived from supplied candidate values and
  typed assembly state.
- Stop if repair requires a framework-authored building answer, a third model
  round, a weaker compiler/runtime gate, or rewriting existing P026 receipts.


## Completion

- Completed: 2026-08-09
- Evidence: Derived exact hosted-component binding requirements from supplied candidate values, published them in each authoring request and contract, rejected aggregate shared bindings before geometry acceptance, and retained runtime revalidation; 34 focused/integration tests, architecture firewall, and deterministic full discovery passed.
