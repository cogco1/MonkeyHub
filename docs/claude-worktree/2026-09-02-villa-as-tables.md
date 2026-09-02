# The villa as tables, not as 3,715 lines

**Question (2026-09-02):** how do we stop writing three thousand lines of
generation code per building? What would carry the Villa Rotonda instead?

## 1. What the 3,715 lines actually are

`reconstruction-012/workspaces/authoring/build_controlled_villa_rotonda.py`
(35 top-level definitions, 309 lines carrying decimal literals):

| Lines | What | Where it belongs |
| --- | --- | --- |
| 967 | `stage_checks`: 30 hand-coded bounding-box checks (column–capital contact, drum–dome contact, window voids, corridor clearance …) | Derived from the **relation table**: every row already names a check id; the check kind (contact / clearance / void / alignment) is generic |
| 460 | `_run_candidate`: stage loop, records, CAD, read-back | The runner (P089) |
| 265 | `build_program`: per-stage assembly of operations | The runner's seat rounds |
| 214 | `build_developed_design_state`: the state built by hand | `SchematicPack@1` → real state (done) |
| 190 + 100 + 88 + 77 + 56 + 56 + 54 + 52 + 43 | `add_portico`, `add_dome_and_lantern`, `add_window_or_door_detail`, `add_segmented_facade(_band)`, `add_spiral_stairs`, `add_main_roof`, `add_floor_system`, `add_central_hall_and_partitions`: coordinates computed inline from constants, one function per family, rotated per side | **Producers** (project-neutral, ~10) fed by **element rows** placed by reference |
| 153 | `ProgramBuilder`: op constructors | Already in the kernel (`GeometryOperation`) |
| 151 + 131 + 84 + 55 + 53 | read-back checks, visual inventory, research persistence, source ledger, materials | Runner + records |
| 73 + 71 | `component_tree`, `relation_manifest` | Already tables — the only part of the script that is data |
| 29 constants | `BODY_SIDE = 21.42`, `PORTICO_WIDTH = 10.71`, `COLUMN_HEIGHT = 6.426`, `STAIR_TREAD = STAIR_GOING / STAIR_RISERS` … | The **derivation table** |

So the script is one building's worth of *data* (constants, tree, relations)
wrapped in a general-purpose *program* (builders, checks, orchestration)
that was never separated from it. Revit separated these in 2000: the RVT is
an element database with a regeneration engine; the only code is the
platform's fixed set of generators.

## 2. The carrying structure: six record kinds, one evaluator, one regen

Every kind is a P036 record (content-addressed, authority-free). Truth stays
in the records; a disposable SQLite index serves queries and the viewers.

```
EvidenceReadings@1   what was read, where, with what confidence
DerivationTable@1    named quantities = expressions over readings (with basis refs)
ProjectLevels@1 / ProjectGrids@1   (P098, already in place)
TypeTable@1          families: ComponentTemplate / WindowType / DoorType + producer id
ElementTable@1       instances placed BY REFERENCE (grid, level, host, type), never by coordinate
RelationTable@1      the 25 predicates between elements/components, each naming its check kind
```

### 2.1 EvidenceReadings@1

Already exists for Rocca as `treatise-plate-reading`. Rows:
`{key, value, unit, confidence: legible|uncertain|measured|derived, source, note}`.

### 2.2 DerivationTable@1 — the 29 constants, as data

```json
{"schema": "DerivationTable@1", "unit": "metre",
 "quantities": [
   {"name": "body_side",      "expr": "21.42",                       "basis": ["reading:official-page-plan-side"]},
   {"name": "body_half",      "expr": "body_side / 2"},
   {"name": "wall_thickness", "expr": "0.42",                        "basis": ["reading:run-016-wall-band"]},
   {"name": "portico_width",  "expr": "10.71",                       "basis": ["reading:official-page-portico"]},
   {"name": "portico_depth",  "expr": "4.284"},
   {"name": "column_diameter","expr": "0.714"},
   {"name": "column_height",  "expr": "9 * column_diameter",         "basis": ["rule:ionic-nine-diameters"]},
   {"name": "axis_spacing",   "expr": "2.25 * column_diameter"},
   {"name": "entablature_h",  "expr": "1.33875"},
   {"name": "stair_riser",    "expr": "service_top / stair_risers"},
   {"name": "stair_tread",    "expr": "stair_going / stair_risers"}
 ]}
```

Evaluator: a forty-line safe arithmetic evaluator (`+ - * / ()`, names,
`min max round`), deterministic, no Python `eval`. P091 already stores
EXPRESSION parameters; this lifts the same idea to the project. Every
derived number in the model now has a name and a basis; the runner records
the evaluated table with digests, so "where does 6.426 come from" is
answerable (`column_height = 9 * column_diameter`, rule ref).

### 2.3 TypeTable@1 — families

Rows are references to templates already in the library plus a producer id:

```json
{"type_id": "villa-principal-window", "template_ref": "project://component-library/.../palladian-window-frame", "producer": "opening:window",
 "parameters": {"frame_width": 0.09, "frame_depth": 0.18, "frame_projection": 0.10, "glazing_thickness": 0.025, "glazing_offset": 0.01}}
{"type_id": "villa-ionic-portico", "template_ref": ".../palladian-hexastyle-portico", "producer": "portico",
 "parameters": {"columns": 6, "axis_spacing": "@axis_spacing", "column_diameter": "@column_diameter", "column_height": "@column_height", "entablature_h": "@entablature_h"}}
```

`@name` means "a quantity of the derivation table". A type carries no
project coordinate (P099 stop condition).

### 2.4 ElementTable@1 — placement by reference

This is the row that replaces `add_portico` and the wall boxes. A wall:

```json
{"element_id": "wall-west", "component_id": "exterior-walls", "producer": "wall",
 "type_id": "villa-exterior-wall",
 "placement": {"line": {"from": {"grid": ["axis-1", "axis-A"]}, "to": {"grid": ["axis-1", "axis-F"]}}, "face": "exterior", "offset": 0.0},
 "base_level": "level-ground", "top_level": "level-eaves",
 "openings": [
   {"opening_id": "window-left",  "type_id": "villa-principal-window", "along": {"grid": "axis-B"}, "width": "@principal_window_w",
    "sill_level": "level-piano-nobile", "sill_offset": "@principal_sill", "head_offset": "@principal_head"},
   {"opening_id": "door", "type_id": "villa-axial-door", "along": {"grid": "axis-S-mid"}, "width": "@door_w", "sill_level": "level-piano-nobile", "head_offset": "@door_h"}
 ]}
```

A portico is one row placed four times:

```json
{"element_id": "portico", "component_id": "porticos", "producer": "portico", "type_id": "villa-ionic-portico",
 "placements": [{"side": "west", "facade": {"grid": "axis-1"}, "outward": "-x", "base_level": "level-piano-nobile"},
                {"side": "east", "facade": {"grid": "axis-6"}, "outward": "+x", "base_level": "level-piano-nobile"},
                {"side": "north", "facade": {"grid": "axis-F"}, "outward": "+z", "base_level": "level-piano-nobile"},
                {"side": "south", "facade": {"grid": "axis-A"}, "outward": "-z", "base_level": "level-piano-nobile"}]}
```

No `rotated(ring, angle)`, no `front_z = BODY_HALF + PORTICO_DEPTH - 0.62`:
the producer resolves grid + level + outward into coordinates. That is
"compiling walls into coordinates" — exactly what Revit's location line +
level constraints do at regeneration.

### 2.5 RelationTable@1 — the manifest, now executable

The script's `relation_manifest` rows survive unchanged, plus a check kind:

```json
{"relation_id": "columns-support-capitals", "subject": "portico-columns", "predicate": "SUPPORTS", "object": "portico-capitals",
 "datum_role": "column-top", "check": "support_contact", "tolerance": "@contact_tol"}
{"relation_id": "walls-host-principal-windows", "subject": "exterior-walls", "predicate": "HOSTS_REAL_CUT", "object": "principal-windows",
 "check": "aperture_exists"}
{"relation_id": "corridor-hall-clearance", "subject": "axial-corridors", "predicate": "CLEARANCE", "object": "central-hall",
 "check": "clearance_interval", "interval_m": [0.0, "@clearance_max"]}
```

Five generic check kinds (`support_contact`, `meets`, `aperture_exists`,
`clearance_interval`, `alignment`) over `expected_object_bounds` and the
datum graph replace the 967 lines: the check runner walks the relation
table, not a script. P096's `RequiredCheck` is the same thing at template
level; this is the project level.

### 2.6 Regen — the dependency graph we already have

Element rows depend on levels, grids, types and their hosts; those are edges
(P098 datum bindings, P099 instance→edition edges, hosting). Change
`level-piano-nobile` and the closure is: every element with that base or
sill level, their openings, their assemblies, the programs they were
realized in. The runner reruns producers for exactly that set and retains
the rest by digest (P063 repair, already measured on the villa: locality
0.173). Revit calls this regeneration; we get receipts on top.

### 2.7 What stays code

- Producers, project-neutral, one per family: wall+openings (done), column
  array (done), prism / ring / loft / dome cap (done), stair (solver exists,
  bridge to rows pending), portico (exists in `portico_geometry.py`, unused
  — to be registered), roof, floor system, spiral stair. About ten.
- The evaluator (~40 lines), the reference resolver (grid/level/host →
  coordinates, ~100 lines), the five check kinds (~150 lines), the runner
  (exists, being bound to stage envelopes by codex).

Nothing else. A new building is six records; a new *family* is one producer
plus a template.

## 3. The villa in rows (estimate)

| Table | Rows | Replaces |
| --- | --- | --- |
| EvidenceReadings | ~25 | the SHA constants and the "soft proxy" notes |
| DerivationTable | ~30 | the 29 constants and the inline arithmetic (`BODY_HALF + PORTICO_DEPTH - 0.62`) |
| ProjectLevels / Grids | 6 + 12 | done (P098) |
| TypeTable | ~10 (exterior wall, principal / service / attic / mezzanine window, axial door, ionic portico, hipped roof, drum, dome, spiral stair) | the `add_*` functions' hard-wired proportions |
| ElementTable | ~45 (4 walls, 4 porticos as 1 row × 4 placements, hall, drum, dome, roof, floor system, 2 spiral stairs, 4 stairs, ~36 openings across four sides as rows under their walls) | 28 wall boxes × 4 sides + 50 loose pieces × 4 + `add_portico` × 4 |
| RelationTable | ~40 | `relation_manifest` (kept) + `stage_checks` (967 lines gone) |

About 170 rows of data and zero Python for the building. The run-016 model
is the acceptance instrument: the rows must realize object-equivalent
geometry (the west band already does at 0.0 m through the runner).

## 4. Why not just a database

A relational database is the *index*, not the truth: records are
content-addressed, append-only and cite their basis; a SQLite index rebuilt
from them (elements / relations / datums / evidence / receipts) answers
"which elements reference level L2" and feeds the Studio stage rail and the
state-tree viewer. Revit's RVT is the truth *and* the index in one binary;
we keep them apart because the truth must be auditable and the index must
be disposable (the viewers already follow that rule).

## 5. Order of work (ten-minute pacing)

1. `DerivationTable@1` + evaluator, and `@name` resolution in packs — 1 h.
2. Reference placement in `ElementPack@1` (grid/level/host → coordinates), portico producer registered — 2 h.
3. `RelationTable@1` + the five check kinds, run by the runner after each seat — 2 h.
4. Villa as rows: west side first (walls + openings + portico), equivalence against run-016; then the other three sides by placement rows — 2 h.
5. SQLite index over records for the viewers — 1 h.

Sequencing with codex: steps 1–3 touch `archflow/runtime/project_runner.py`
and `archflow/state/`, which codex is editing today (stage envelopes). They
wait for that landing; step 4's data can be drafted now against the current
pack schema and migrated.
