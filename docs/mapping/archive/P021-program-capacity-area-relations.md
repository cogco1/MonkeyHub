# P021 — Program, capacity, area, and relationship compilation

- Origin: Planning
- Status: Ready after P043
- Depends on: P020, P043

## Goal

Derive building-scoped program hypotheses from the current brief: users,
activities, functions, capacities, net/gross area ranges, and functional
relationships. Produce alternatives and evidence, not a fixed footprint.

## Write scope

- `archflow/state/design_program.py`
- `archflow/runtime/program_compiler.py`
- `archflow/capabilities/programming.py`
- `archflow/state/__init__.py`
- `archflow/runtime/README.md`
- `archflow/capabilities/README.md`
- `tests/test_program_compiler.py`
- `docs/mapping/`

## Acceptance

- Functions arise from the current building request and evidence, not a global
  typology table with generation authority.
- Capacity and area values are ranges with assumptions and source references.
- Net area, gross allowance, footprint, and total floor area remain distinct.
- Adjacency, separation, public/private, noise, and circulation relationships
  are explicit without prescribing a unique layout.
- At least two non-isomorphic building requests compile without shared
  building-specific answers.

## Tests

- Two non-isomorphic building requests with no shared project answers.
- Missing scale produces bounded scenarios rather than one hidden default.
- Explicit maximum footprint remains a constraint, not a derived program fact.
- No room-list, dimension, palette, or topology production default.

## Stop conditions

- Stop if the compiler emits coordinates or a single massing answer.
- Stop if retrieved facts lose provenance when entering design state.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented DesignProgram@1 with first-class assumptions, exact-base provenance, bounded capacity/net-area/gross-allowance/footprint/total-floor-area ranges, explicit non-geometric relationship hypotheses, and no generation authority.
- Evidence: Program compiler rejects cross-project evidence and single hidden scale defaults, preserves explicit constraints by reference, emits unresolved obligations instead of invented values, and exposes read-only obligation-driven programming snapshots.
- Evidence: Two non-isomorphic request fixtures remain disjoint; 164 tests passed with one opt-in live retrieval smoke skipped, compileall passed, P021 scope passed, and instance-default scan was clean.
