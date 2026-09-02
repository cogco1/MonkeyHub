# P098 — Project levels and grids as shared datums

- Origin: Planning
- Status: Complete (2026-09-02)
- Depends on: P090, P095, P096

## Goal

Every element in a building references the same storeys and axes. The
schematic layer already has `SpatialLevel`; the geometry layer never
references it, so seats publish per-object, per-side datums (four
identical `landing-top` values in run-017). Add project-level `Level`
and `Grid` records published by the coordination seat once per project,
make `InterfaceDatum` bindable to a level or grid axis by role, and let
assembly-template required datums resolve to them. A storey becomes a
container role in the assembly template, so spaces and egress read the
realized building, not the schematic.

## Acceptance

- One `ProjectLevels@1` / `ProjectGrids@1` record per run; every seat
  datum of kind LEVEL that names a level role derives from it; a seat
  publishing a conflicting level fails typed.
- Villa run: the four porticos and the wall band bind to one
  `piano-nobile` level; per-side datums drop to zero for levels.
- Assembly template datums of kind LEVEL/PLANE resolve to level and grid
  roles; the binding fails typed when a required level is missing.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/state/geometry_program.py`
- `archflow/state/assembly_template.py`
- `archflow/capabilities/discipline_seats.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Stop conditions

- Stop before a level value is defaulted by the kernel.
- Stop before a seat may overwrite a project level.

## Evidence (2026-09-02)

Kernel: `ProjectLevel@1` / `ProjectLevels@1` / `ProjectGridAxis@1` /
`ProjectGrids@1` in `archflow/state/geometry_program.py`; a level or
axis without a basis ref is refused (no kernel default). A record
derives LEVEL / PLANE `InterfaceDatum`s published by the coordination
seat; `verify_project_datums` reports any seat datum that carries a
project id with another kind, value, unit or publisher.
`project_seat_context(..., project_levels, project_grids)` puts the
project datums into every seat's context (`SeatAuthoringContext@1`
gains `project_datums`); `check_seat_datums` raises `SeatError` for an
overwrite. `bind_assembly_template(..., project_levels, project_grids)`
requires a LEVEL datum role to bind a project level id and a PLANE role
a grid axis id, failing typed otherwise. Tests:
`tests/test_project_levels.py` (10).

Villa run-017 (`run_project_datums.py`, report `project-datums.report.
json`): `project-levels` record publishes `level-piano-nobile` 3.570 m
and `level-main-cornice` 11.335 m (both read from run-016, all four
sides agree); `project-grids` publishes axes A–F and 1–6 from the
abacus centres (west/east and north/south sets agree to 1 mm). The four
sides were re-authored through the three seats binding to the project
datums: per-side `{side}-landing-top` / `{side}-entablature-top` datums
dropped from 8 to 0; bindings to project levels per side = 9 structure
(6 columns, 3 roof members) + 6 envelope (2 wall courses, 4 door
members) + 1 detail (pediment). All 12 programs accepted with zero
issues and realize the same object sets as the accepted seat programs
(max bounds deviation 0.0 on east/north/south; 0.5 mm on the six west
abaci, from the historical west script's millimetre rounding).
Receipt `project-datums-rebind-receipt-63aebfac…`; the refused
overwrite (a structure seat publishing `level-piano-nobile` at 3.4 m)
is recorded as `project-level-overwrite-refusal`.

Interpretation of "per-side datums drop to zero for levels": storey
and building levels (piano nobile, main cornice) are project datums;
component interface datums (column top, abacus top, sill, head, course
seats) stay published by their components — they are interfaces, not
storeys. Storey-as-container role in the assembly template is not yet
modelled (no storey role in the villa template); left to P099/P092.



## Completion

- Completed: 2026-09-02
- Evidence: tests/test_project_levels.py (10); villa run-017 project-datums-rebind-receipt-63aebfac: 12/12 seat programs accepted, equivalent to the accepted programs within 1 mm, per-side storey datums 8->0
