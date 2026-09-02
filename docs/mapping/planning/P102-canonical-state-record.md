# P102 — Canonical StateRecord (one government)

**Status:** active (registry registration deferred until codex's P089 registry edit lands)
**Lane:** productization and componentization (P088–P093, P100)
**Retires:** `DevelopedDesignState` as an *authoring input*. It stays only as a forwarded view
(`developed_design_view`) with lineage, until the producer loop reads `StateRecord@1` directly.
**Depends on:** P100 (DerivationTable@1, semantic references), P098 (ProjectLevels/Grids), kernel
`OperationalMarkovState` / `DependencyEdge` / `DesignObligation`.

## Why

Production and the kernel were two governments. The kernel already holds the canonical state model
(facts, bindings, locks, commitments, obligations, dependency edges, 25 relation kinds with propagation
rules); production bypassed it and authored `DevelopedDesignState` by hand from per-project scripts.
The review of 2026-09-02 (`docs/claude-worktree/2026-09-02-state-record-architecture-review.md`,
verdict Modify) settled the shape the user asked for: **one record, entities of typed schemas** — not six
peer "universe tables".

## Shape (`archflow/state/state_record.py`, schema `StateRecord@1`)

```
StateRecord
├─ entities     Entity(entity_id, schema ∈ {Level@1, GridAxis@1, Type@1, Element@1, Assembly@1,
│                      Space@1, Reading@1, Component@1}, fields, parent_id, basis_refs, lineage)
├─ parameters   Parameter(key, value, unit, expr, inputs, epistemic_status, source_ref, lock_authority, lineage)
├─ relations    Relation(kind, subject, object, datum_role, propagation, validator: ValidatorBinding, …)
├─ obligations  kernel DesignObligation (kept apart from relations: a relation *creates* obligations)
├─ evidence_refs / basis_refs / predecessor_ref / decision_ref / invalidated_refs
└─ stage        StageBinding(workflow_ref, envelope_ref, stage_id, predecessor_exit_binding_ref)
Derived, never authored: geometry program, validation results, receipts, SQLite indexes.
```

`dependency_edges()` lowers relations, parameter inputs, and entity field references (`base_level`,
`host`, `component_id`, `parent_id`) to kernel `DependencyEdge`s; `closure(changed_refs)` walks them.
Gaps fail typed (`StateRecordError`): unknown schema, self-relation, unknown validator kind, parameter
input or relation endpoint that does not exist.

## Acceptance

- [x] `tests/test_state_record.py` (5): round trip + digest, relations/obligations apart, edges and
      closure, typed gaps, `developed_design_view` forwards to the legacy state.
- [x] Villa west portico authored as `StateRecord@1` (6 Component, 3 Level, 14 GridAxis, 13 Reading, 4 Element
      entities; 19 parameters with expr/inputs; 4 support relations with datum roles and `support_contact`
      validators) and fed through `developed_design_view` into the real producer loop; Rhino readback equals
      run-016 within 0.25 mm on all 14 objects (villa run reference-001, report record
      `portico-by-reference-report-7d6c705b…`). Rocca: the whole record (84 entities, 52 parameters) runs through the
      hardened runner and reproduces runner-002 (27 objects, 0.0 m): `project://rocca-pisana/runs/equivalence-002/records/state-record-equivalence-1cb2d33473614addcdd80254eb6ff8aa0ed8dbf1d1f12ebbc2db38683e6a5eda.json`. Villa west band likewise (51 objects,
      0.0 m, state digest identical): `project://villa-rotonda-reconstruction/runs/equivalence-001/records/state-record-equivalence-6bcb52152c6766ba969b1f44c986066b39ff7a1ee9e5fbfa4715c124108f68de.json`.
- [x] Relation validators run from the record: `archflow/capabilities/relation_checks.py` measures every
      relation with a validator binding against realized bounds (analytic or Rhino readback) and writes
      `RelationCheck@1` / `RelationCheckReport@1`; `support_contact` implemented (level in either orientation,
      declared engagement measured, datum must hold the face), other kinds report `unchecked`, nothing is healed.
      Villa reference-001 readback: 4/4 held, gaps 0.00000 (record `relation-check-report-a-56e12e9d…`);
      `tests/test_relation_checks.py` 4/4 (a moved column violates and is not healed).
- [ ] `developed_design_view` marked `lineage.retired_at` once the *compiler* reads the record directly (the production
      entry and the runner already take the record; the view is called from exactly two places: `_as_developed_state`
      and `run_project`).

## Do-not-do

No semantic healing (no snapping geometry to satisfy a relation); no second relation table beside the
kernel's `ArchitecturalRelation`; no numbers in relations that are not `datum_role`s or declared
engagement depths.

## Evidence

- 2026-09-02 villa reference-001: `project://villa-rotonda-reconstruction/runs/reference-001/records/portico-by-reference-report-7d6c705bc857db4ebf7e88ba49e4788dca9344a5af01106df1757ccc01b53cf1.json` (records `state-record-a/b1/b2`, `derivations-*`, `portico-geometry-program-*`).
- `tests/test_state_record.py` 5/5; `tests/test_relation_checks.py` 4/4; villa `check_reference_relations.py` → `relation-check-report-a`.
- 2026-09-02 night: runner input = `StateRecord@1` (`SchematicPack`/`ElementPack` retired as inputs); relation checks per seat
  (`seat-relation-check`); the villa abutment's 48 mm base offset surfaced as a violated `stands-on` relation until it was declared
  on the relation as an engagement — the checker made a silent reading offset explicit.
