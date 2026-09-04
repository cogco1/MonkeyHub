# W3-A — the re-index drafts stairs, wedges and shells: report

## Status

DONE_WITH_CONCERNS

`draft_stair`, `draft_wedge`, `draft_shell` and `seat_on_stairs` are in
`archflow/capabilities/element_reindex.py` with fourteen new tests in
`tests/test_element_reindex.py`. Everything the brief asks for is implemented and measured.
Two things are not as the brief pictured them and are stated under **Concerns**: the
producers' `_loft` currently **refuses** every wedge or dome shell whose base is an offset
from a level (a one-line defect in `element_producers.py`, which I may not touch), so that
case is drafted and lands as ERROR carrying the refusal; and a wedge's *sense* — which end
of the run is low — is not in the three strings the brief specifies, so it is a stated
convention, not a measurement.

## Commits

- `aa6481c` — Re-index: a flight of steps is a stair; a wedge and a shell need the export to
  say so (`archflow/capabilities/element_reindex.py`, `tests/test_element_reindex.py`)
- `HEAD` after this file — this report, a second commit only so it could name the hash of
  the first.

Branch `worktree-agent-a0dbc06fbce6cc3d8`, rebased onto `main` at `7387185` before starting
(it began at `6a4a31a`, older than the `6ca66a0` the brief requires). Nothing merged or
pushed; `governance/module_registry.json` untouched.

## Tests

```
cd D:\ARCHFLOW_V4\.claude\worktrees\agent-a0dbc06fbce6cc3d8

py -3.12 -m unittest tests.test_element_reindex tests.test_element_producers
Ran 43 tests in 0.109s
OK

py -3.12 -m unittest discover -s tests -t .
Ran 365 tests in 19.005s
OK

py -3.12 tools/archcheck.py
ARCHITECTURE PASS (189 files, 1.615s)
```

`tests/test_element_reindex.py` went from 12 tests to 26. The new ones, by class:

*StairTests* — a straight flight of 5 solid steps drafts a `stair` row with count 5, rise
0.4, going 0.6, width 3.0, thickness 0.0 and residual 0.0 m, and the landing prism above it
carries `base: {"datum": "main-block-stair-west-top"}` with the derived relation
`main-block-stair-west-supports-main-block-landing`; slab steps carry `thickness` 0.15 under
a 0.4 rise; a flight whose ends land on declared axes names the two intersections and drafts
no axis (confidence 0.9); a flight on no declared axis drafts its run axis and the successor
still round-trips; a rotating (spiral) family stays AMBIGUOUS with a note beginning
`collinear:`.

*WedgeTests* — a five-face solid with no strings stays AMBIGUOUS with exactly the brief's
note and nothing else in its notes; with `archflow:wedge_*` it drafts a `wedge` (depth 6.0,
low 0.3, high 1.78) with residual 0.0 m; `wedge_axis: across` sets `slope_across`; a
non-numeric `wedge_high` is named, not dropped; a wedge off a level is drafted and the
producer's refusal becomes the draft's ERROR note (see Concerns 1).

*ShellTests* — a `drum` with `archflow:shell_thickness` drafts a cylinder `shell` (outer
5.55, thickness 0.65, height 6.06) around `CENTRE-X` × the declared `B`, residual 0.0 m; a
drum with no string reads its thickness off `obj-drum-inner`; `archflow:shell_kind: dome`
drafts a dome and a `lantern-cap` without it stays AMBIGUOUS naming the string; a drum with
neither string nor inner object stays AMBIGUOUS with exactly the brief's note.

One existing assertion changed: `test_a_box_is_a_prism_with_its_footprint_and_a_wedge_is_ambiguous`
asserted the pediment tympanum's note was the generic "no producer carries this form"; it is
now the specific wedge note the brief specifies, so the assertion asserts that instead. No
other existing test changed.

## What the re-export has to carry

The CAD side needs five user strings, all optional, all read per object in `objects_of` and
kept as written so a value that is not a number is *named* rather than dropped:

| string | values | drafts |
|---|---|---|
| `archflow:wedge_low` | metres above the base datum, `>= 0` | the wedge's low edge |
| `archflow:wedge_high` | metres above the base datum, `> low` | the wedge's high edge (normally the box height) |
| `archflow:wedge_axis` | `along` \| `across` | whether the top plane tips along the run or across the depth (`slope_across`) |
| `archflow:shell_thickness` | metres, `0 < t < outer_radius` | the wall thickness a box cannot show |
| `archflow:shell_kind` | `cylinder` \| `dome` | which revolved form; without it only a `drum` family is read, as a cylinder |

All three wedge strings are required together — two of the three still leaves the family
AMBIGUOUS. For a shell, `shell_thickness` is preferred over the inner-surface object when
both exist.

**One string the brief does not list and the CAD side should add: the wedge's sense.**
`along`/`across` says which way the plane tips but not which end is low. The reindex takes
the low edge at the `from` end (the lower run coordinate) and says so in the note; a wedge
modelled the other way round produces a mirrored solid whose bounding box — and therefore
whose residual — is identical, so nothing downstream can catch it. An
`archflow:wedge_sense` of `from`|`to` (or signing `wedge_low`/`wedge_high` by end) would
close it. I did not invent the vocabulary because the brief named exactly three strings.

## The new notes, verbatim

Asserted character for character in the tests:

```
five-face solid: the top edge is not readable from a box; a wedge row needs low/high/axis — author it or re-export with archflow:wedge_* strings
```

```
a shell needs its thickness: no inner surface object; author `thickness` or re-export with archflow:shell_thickness
```

(The first carries an em dash, U+2014.) The other notes the new code can emit, in the same
spirit but not dictated by the brief:

- `collinear: the step centres do not advance along one plan axis (they spread <x> m in x and <y> m in y)`
- `going: the steps are not one going (<min>..<max> m along the run)`
- `going: the steps do not advance by one going along <run>; a flight has no gap between treads`
- `rise: the steps do not climb by one rise (base to base <min>..<max> m)`
- `rise: the steps are not one height (<min>..<max> m)`
- `rise: a step <h> m tall over a <r> m rise overlaps the one below; a flight's steps do not`
- `width: the steps are not one width (<min>..<max> m across the run)`
- `a flight is two or more 6-face boxes; this family has N object(s) of form [...]`
- `archflow:wedge_axis is '<v>'; a wedge slopes 'along' its run or 'across' it`
- `archflow:wedge_high is '<v>', not a number of metres`
- `archflow:wedge_high <h> m is not the box height <b> m; the string is used and the residual reports the difference`
- `a shell's kind is not readable from a box: only a drum is read as a cylinder wall; author the row or re-export with archflow:shell_kind`
- `a thickness of <t> m does not sit inside the outer radius <r> m (<source>)`
- `seated on <flight>-top rather than the level above: the flight publishes what it carries`
- `the ends are on no declared axis (2 mm); they are drafted as points along <role>, which the run line lies on`
- `run axis <role> drafted at <const>=<value>; the ends are points along it`

## Registrations needed

`governance/module_registry.json`, module `capabilities.element_reindex` — not edited
(common-brief rule 3). One `owns` line changes and one is added.

- Replace

  `"element drafts per (component, family, side): column-array, capitals, beam, prism from boxes; AMBIGUOUS for forms boxes cannot carry"`

  with

  `"element drafts per (component, family, side): column-array, capitals, beam, prism, ring from boxes; stair flights from step families; wedge and shell rows only from explicit archflow:wedge_* / archflow:shell_* strings; AMBIGUOUS for forms boxes cannot carry"`

- Add to `owns`

  `"re-seating a drafted prism onto a drafted flight's published <id>-top instead of a level"`

Optional, if the controller wants the constants the tests now import declared: add
`WEDGE_NOTE` and `SHELL_THICKNESS_NOTE` to that module's `public_api` (the tests import them
so the exact wording is asserted in one place rather than copied). `archcheck` passes
without this change. No new record kind, no new module, no `record_kinds.py` change, no
change to any other module's entry.

## Concerns

1. **`element_producers._loft` refuses every non-zero base offset — the case it was added
   for.** `GeometryOperation` requires its parameter names sorted (`geometry_program.py`
   line 479: `names != tuple(sorted(set(names)))` raises "parameters require unique
   deterministic names"). `_extrusion` therefore *inserts* `base_offset` at index 0; `_loft`
   *appends* it after `profiles`, which is out of order. So any `wedge`, or any `dome`
   shell, whose base is `{"offset_from": ...}` or an offset datum raises instead of
   producing — the exact silent-drop that W1-B's concern 2 set out to fix. My brief forbids
   touching the producers, so I did not; I drafted the row anyway and let the discipline hold:
   `measure` records ERROR with the refusal as the note, and
   `test_a_wedge_off_a_level_is_drafted_and_the_producers_refusal_becomes_the_error` pins
   that with a comment naming the fix. **The fix is one line** — in `_loft`, build the
   parameter list and `insert(0, ...)` the way `_extrusion` does. When it lands, tighten that
   test to DRAFT with residual ≤ 1 mm. Nothing else in the tree exercises the path, which is
   why the 349-test suite was green over it.

2. **A wedge's sense is a convention, not a measurement.** See *What the re-export has to
   carry*. The run direction is the longer plan extent (the reading `draft_beam` already
   makes of a box) and the depth the shorter; both trace to box extents. Which *end* is low
   traces to nothing, and the note says so in the row itself.

3. **The stair reading is gated on the family name** (`stair`, `step`, `flight`, `spiral`),
   not on geometry alone. A family of boxes that happens to climb by a constant rise — a
   stepped podium, a corbel course — would otherwise be silently reclassified from "one prism
   per box" into a flight, which is a guess about intent, and the module's rule is that
   nothing is guessed silently. The consequence is the honest one: a flight the export names
   something else is drafted as prisms, exactly as today. The names the brief cites
   (`stair-<side>-NN`, `spiral-*`) both match.

4. **A shell's inner-surface object keeps its identity and gets no row.** `obj-drum-inner`
   is read for the drum's thickness, but its own family (`drum-inner`) is excluded from the
   shell gate and falls through to AMBIGUOUS with the generic "no producer carries this
   form" note. Folding it into the drum's own draft would be right — the union box is
   unchanged, since it is strictly inside — but it would mean one object bound to two
   elements or a change to the grouping in `reindex`, and neither is in scope here. The
   catalog therefore shows the drum COVERED and the inner object identified but unbound.
   `archflow:shell_thickness` avoids the whole question and is the better re-export.

5. **The shell's segment count is the producer's default (24), which the note states.** A
   box cannot say how many segments the revolve used. 24 happens to be a multiple of four, so
   the polygon touches ±`outer_radius` on both axes and the residual is exactly 0; a
   re-export at, say, 30 segments would leave a small honest residual rather than a wrong
   radius.

6. **`AxisLine` gained `origin_at` and `dir_sign`.** An `axis_point`'s `along` is measured
   from the axis *origin* in the axis *direction*, not from the world origin, so a declared
   axis whose origin is off the world origin (or whose direction is negative) needed both to
   emit a correct `along`. Drafted axes keep the defaults `(0.0, +1)`, which is what
   `axis_entities` already writes, so nothing existing changes. This is the concrete form of
   W1-B's concern 1: the reference kind really is `{"axis_point": {"axis": ..., "along": ...}}`
   and that is what the re-index emits.

## Left out and why

- **`governance/module_registry.json`** — not edited; the two `owns` lines are under
  *Registrations needed* per common-brief rule 3.
- **`element_producers._loft`'s parameter order** — the defect in Concern 1. The brief says
  "Do not touch the producers"; I left it and documented the one-line fix instead of widening
  scope.
- **`_op_box` was not extended.** Brief item 4 asked me to check it. It already reads
  `base_offset` for both EXTRUSION and LOFT before it branches on the op kind, so the stair's
  per-step `base_offset + k · rise` and a loft's offset both come back correctly; the
  measurement side is fine and the producer side (Concern 1) is what is broken. The stair
  tests prove the extrusion path to 0.0 m residual; the loft path is proven only at offset 0
  until `_loft` is fixed.
- **A dome or lantern-cap drafted from geometry** — brief item 3 talks itself out of this
  mid-sentence and I agree: a dome's rise is no more readable from a box than a wedge's
  slope. Only `archflow:shell_kind` opens that branch.
- **Wedge rows from `mesh_face_count == 5` plus sibling evidence** — brief item 2 states the
  reindex cannot read a top edge from siblings today, and it still cannot; the strings are the
  only route.
- **Any Villa or `D:\PROJECTS` data** — none read, none written. All fixtures are synthetic
  and live in `tests/test_element_reindex.py`.
- **Anything under `apps/`, the runner, or `tools/reindex_project.py`** — untouched. The two
  files in the diff are the two the brief names.
