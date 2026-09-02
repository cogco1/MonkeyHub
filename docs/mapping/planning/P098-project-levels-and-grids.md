# P098 — Project levels and grids as shared datums

- Origin: Planning
- Status: Ready after P096
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
