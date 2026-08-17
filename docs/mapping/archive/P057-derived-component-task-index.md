# P057 — Derived component and task index

- Origin: Planning
- Status: Ready
- Depends on: P055, P022

## Goal

Derive one queryable component index and bounded task context from existing
component, control-state, geometry-binding, dependency, and lifecycle records.
The index accelerates model context assembly but owns no design fact and is not
a third tree.

## Write scope

- `archflow/state/`
- `archflow/runtime/`
- `archflow/project/`
- `tests/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- `ComponentIndex` is deterministically rebuildable from authoritative records
  and joins stable component ids to parents, stage, geometry objects,
  dependencies, unresolved roles, tasks, and source references.
- P022 remains the control/context tree and `DesignComponent` remains the sole
  building-composition tree; the index stores no independent parentage.
- Task contexts contain only the selected component neighborhood and explicit
  dependencies, with digests that reject stale reuse.
- Missing, contradictory, or stale source records fail closed; deleting the
  index loses no canonical information.
- Any durable index snapshot is written only through a P036-derived artifact
  port and can always be regenerated.

## Tests

- Deterministic rebuild, component-local task slicing, dependency inclusion,
  stale digest rejection, and delete-and-rebuild equivalence.
- Proof that index parentage cannot diverge from `DesignComponent`.
- Architecture firewall and full unit discovery.

## Stop conditions

- Stop if the index becomes a design authority, persistence authority, or
  second/third semantic tree.


## Completion

- Completed: 2026-08-16
- Evidence: Derived ComponentIndex and bounded task contexts from exact P022 control tree, DesignComponent composition records, developed dependencies/tasks, compiled semantic bindings/objects, and P055 lifecycle receipts; stale or contradictory inputs fail closed, parentage remains embedded DesignComponent authority, P036 run-record snapshots are optional and regenerable. Focused 5 tests, P057 verification, scope PASS 4 paths, ARCHITECTURE PASS 117 files, full discovery 528 tests with 2 explicit skips, V3 boundary, and diff check passed.
