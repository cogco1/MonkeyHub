# P092 — Wall and window family extraction

- Origin: Planning
- Status: Complete (2026-09-02)
- Depends on: P090, P091, P095, P098 (P089 dropped 2026-09-02: the seat scripts stand in for the runner)

## Goal

Harvest the wall-with-openings and window/door-array families from the
villa run-012 monolith (3,572 lines, zero archflow imports) into
solver-plus-bridge capability pairs on the stair pattern: the
mathematics — enumeration, topology, local-basis baking — enters the
capability layer with zero typology constants, and the typology — ratio
bands, sill heights, reveal bands — lifts into P091 template records
with citations. Contact and hosting run through P090 datums, so a window
array carries exclusion masks derived from portico-roof obligations and
the attic-window-through-roof defect class becomes unrepresentable.
Villa then replays as reconstruction-016 through the P089 runner;
equivalence against the repaired run-014 model is the acceptance
instrument, and the reuse metrics become paper evidence.

## Acceptance

- Wall and window solvers are authority-free with a passing
  no-instance-default static scan; bridges bake local-basis assemblies
  to neutral geometry programs.
- Interface duties are declared, not coded: walls host voids requested
  by opening solvers; window arrays accept INTERSECTS exclusions from
  portico obligations.
- reconstruction-016 replays object-equivalent to the repaired run-014
  model except at documented defect fixes.
- Three reuse metrics are reported: runs-to-acceptance per stage, inline
  geometry bytes per run, catalog hit rate.

## Write scope

- `archflow/capabilities/`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Solver enumeration and negative paths with scripted inputs.
- Void-hosting negotiation between wall and window solvers.
- Replay equivalence harness against retained run-014 records.
- Architecture firewall.

## Stop conditions

- Stop when a ratio has only single-case support — park it in project
  records under the two-vote rule rather than promoting it.
- Stop if any solver requires a building-type default to function.

## Scope amendment (2026-09-02)

Harvest as element families, not as operation copies (see registry
note). Write scope extended to `archflow/adapters/cad_program.py`,
`archflow/realization/sandbox.py` and `archflow/state/geometry_program.py`
for the three kernel changes the element form needed. The run-014 replay
through the P089 runner is replaced by object equivalence against the
run-016 canonical model on the run-017 seat basis.

## Evidence (2026-09-02)

Kernel. `archflow/capabilities/wall_solver.py`: `WallElement` (reference
line = exterior face, thickness, height, storey datum), `OpeningRequest`,
`solve_wall` → one wall extrusion, one through-cut tool and one
wall-∩-tool aperture per opening (the assembly's HOST_CUT, exported
hidden), one boolean difference; `HostedVoid` is what the wall grants.
`archflow/capabilities/opening_solver.py`: `WindowType` / `DoorType`
carry proportions only; `solve_window` / `solve_door` fill a void with
frame, glazing or leaves and a `HostedAssembly` at ENVELOPE maturity.
Levels never appear as numbers: every member binds `base_level` to the
storey datum and rides its seat height as the new `base_offset`
parameter (adapter, sandbox, producer contract). Boolean-difference
bounds stay analytic for a box base under a cutter strictly inside it on
any axis (a through-cut cannot remove a whole face); other bases still
fail closed. `required_assembly_roles(kind, maturity)`: ENVELOPE needs
void + frame + infill; FUNCTIONAL / FINE keep the full protocol set.
Zero typology constants: no default sill, width, frame or thickness.
Interface duties are declared, not coded: the wall HOSTS_VOID, the
window FILLS_VOID, exclusions are INTERSECTS refusals at solve time.
Tests: `tests/test_wall_window_families.py` (11): enumeration, negative paths,
exclusion refusal, void negotiation (a type that does not fit fails
typed), real-compiler acceptance with bounds on the datum.

Villa run-017, west wall band (`run_wall_elements.py`, report
`wall-elements.report.json`, receipt `wall-elements-receipt-adfd5867…`).
Run-016 modelled the band as 28 wall boxes + 50 frame / glass / leaf
pieces (78 operations, 61.8 kB inline geometry). Element form: 1 wall,
11 opening requests (2 ground windows, door, 2 principal windows,
mezzanine, 4 attic, the ground service passage as a bare void), 3 types
(two window types, one door type; catalog hit rate 8/11 = 0.727), 2.6 kB
of element inputs. Exclusions came from the structure seat's realized
portico roof and the detail seat's pediment through a compiled handover
(`seat-handover-2882f4e5…`); the wall solver refused attic windows b
and c behind the roof abutment — the defect class run-016 carried —
recorded as `opening-exclusion-refusal`. The envelope seat (owning the
six opening components) produced the program in one round with zero
issues: 60 operations, 50 realized objects, 8 hosted assemblies. Every
frame, pane and leaf lands on its run-016 extent (max deviation 0.0 m);
the cut wall stands on `level-ground` and reaches `level-eaves`
(project levels edition 2 adds both); the wall volume equals the
run-016 boxes plus the two refused voids to 4.5 litres of millimetre
rounding. Rhino export succeeded with read-back (50 objects, cut wall
present, consumed solid and tools absent, apertures hidden). Two
template candidates (`palladian-wall-with-openings`,
`palladian-window-frame`) are harvested into the run with single-case
ratio bands and left unpromoted under the two-vote rule.

Found on the way: the Rhino document tolerance merged a 1 cm tool
overshoot with the wall face (apertures and cut read 1 cm thick too
much); the tool now overshoots 5 cm. A tool sharing the wall's bottom
face (a door at sill 0) overshoots below it as well.

Left open: rotated walls fall back to fail-closed boolean bounds (the
box rule needs an axis-aligned profile); the other three sides and the
interior walls are not yet elements; storey-as-container in the
assembly template (P098 note) and type-to-instance propagation (P099).



## Completion

- Completed: 2026-09-02
- Evidence: tests/test_wall_window_families.py (11); villa run-017 wall-elements-receipt-adfd5867: west band as 1 wall + 11 openings + 3 types, members 0.0 m off run-016, attic b/c refused behind the portico roof, Rhino export read back
