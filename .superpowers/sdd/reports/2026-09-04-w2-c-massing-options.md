# W2-C — Massing options with metrics, kept as candidates

## Status

DONE_WITH_CONCERNS — everything the brief asks for is built and tested, with one substitution the
brief could not have known about: there is no `runner-schematic-pack` record kind, it is retired
vocabulary the runner is asserted *not* to write (`tests/test_project_runner.py:249`), and
`record_kinds.py` is another worker's file this wave. Each option is retained under the existing
`selected-spatial-option` kind instead, as the kernel's own `SpatialOptionProposal@2` — the same
value the runner writes for the option a run executes. Details under **Left out and why**.

## Commits

- `6cfd350` — Massing options: several shapes on the table, measured, one of them run

## What was built

### Kernel

**`archflow/state/massing_metrics.py`** (new, pure, never writes).

- `massing_metrics(record, *, program_targets=None) -> MassingMetrics` with `footprint_m2`,
  `gross_floor_area_m2`, `floor_count`, `height_m`, `efficiency`, `per_level` and `honesty`.
- `envelope_check(record, envelope) -> tuple[EnvelopeFinding, ...]` with three finding codes and no
  fourth: `volume_outside_envelope`, `height_exceeded`, `far_exceeded`. Every envelope key is
  optional and an absent key makes no finding — an envelope that says nothing about height cannot
  be exceeded in height.
- `EnvelopeFinding` carries `measured` and `limit` as separate numbers, always in that order, so a
  reader never parses `detail` to learn by how much.

**Two functions extracted into `archflow/state/state_record.py`**, both because a second reader of
the same field would be a second answer:

- `volume_boxes_of(record)` — the one reader of the `Volume@1` box. `project_runner
  ._check_produced_relations` now calls it instead of reading `fields["min"] / ["max"]` inline
  (see **On the kernel owner's note** below).
- `schematic_pack_of(record, *, option_id=None, evidence_refs=None) -> SchematicPack | None` — the
  massing the record declares, as a pack, or `None` when it declares none.
  `developed_design_view` now builds its view from this instead of assembling the pack inline;
  the construction is byte-for-byte the code that was there, so no digest moved (whole kernel
  suite, `test_state_record` and `test_project_runner` included, is green).

### The frame convention the metrics rely on

Stated in the module docstring, in `volume_boxes_of`'s docstring, and in PROTOCOL §5.3, and taken
from the kernel rather than assumed:

- A `Volume@1` box is two coordinate triples in the voxel lattice `state.spatial.SiteBounds`
  defines: **x and z are plan, y is up**, and **both ends are inclusive cells** — `SiteBounds.volume`
  multiplies `max - min + 1` per axis, and `_coordinate` requires integers.
- **One plan cell is one square metre**, because `schematic_proposal` declares
  `SpatialGridBasis(horizontal_area_per_cell=1.0, area_unit="square_metres")` on every option it
  builds. `CELL_AREA_M2` is named in the module so a reading of it is traceable to that
  declaration and not to a bare `1` inside an expression.
- **A level's height in metres.** `SpatialLevel.top_y` is `base_y + height - 1` — the topmost cell a
  level *occupies*. A height is the distance to the face above that cell, so `height_m` measures
  `max(base_y + height) - min(base_y)`: one level of height 4 is 4 m, not 3. The brief says "max top
  − min base"; this is that, with `top` read as the top *face* rather than the top cell, and both
  conventions are written down beside each other in the docstring so a reader can see they are one
  lattice counted two ways.
- `footprint_m2` is the **union** of every volume's plan rectangle (an exact sweep over compressed
  x coordinates), so two volumes overlapping in plan cover their ground once.
  `gross_floor_area_m2` is the **sum of the per-level footprints**, so a plate on three levels
  counts three times — that is the number FAR is checked against.
- A box whose plan bounds are not whole cells is **not measured**: it becomes an `honesty` line, not
  a rounded area the kernel's own `SiteBounds` would refuse to build.

### Studio API

**`application/options.py`** (new) — `OptionStore` (in process, locked), the six closed transforms,
and `record_massing`/`baseline_pack` readers.

- `add_floor` — one more massing level on top, same height, carrying the volumes that stood on the
  old top level (their `max.y` is raised and the new level id appended to their `level_ids`), so
  the GFA rises by exactly one level's footprint.
- `remove_floor` — drops the topmost level, removes it from every volume's `level_ids`, clamps the
  remaining volumes' `max.y` to the new ceiling, drops volumes and zones left with nothing, and
  refuses (`LAST_FLOOR`, `NOTHING_LEFT`) rather than producing a value the kernel would reject.
- `shift_volume {volume_id, dx, dz}` — whole plan cells only.
- `scale_volume {volume_id, sx, sz}` — about the volume's own centre; the span is a whole number of
  cells and stays one, never below 1.
- `split_volume {volume_id, along, at}` — `at` is the first cell of the far part. The near part
  **keeps the volume's id** so every zone and component that named it still does; the far part is a
  new volume and is given to the same zone *and the same semantic owner*, because
  `SpatialOptionProposal` requires exactly one owner per volume and a half-building belonging to
  nobody is refused by the runner rather than here.
- `pack` — a whole payload the client sends, read by `SchematicPack.from_dict`. This is the socket a
  generative massing agent plugs into and the only transform that takes free-form geometry; every
  option made this way carries the honesty line *"this pack was sent by the client and validated by
  the kernel; the studio derived none of it"*.

Each option is measured on the **successor record selecting it would actually run** (via
`massing_successor`), not on an approximation of it, and `schematic_proposal(pack)` is called at
make time so a massing the kernel refuses arrives as a 422 about *this transform* rather than as a
failed run minutes later.

**`application/candidate.py`** (extended, `studio.candidate`):

- `massing_successor(record, pack)` — the studio's second successor operation. It replaces the four
  massing schemas and the declared `option`, carries an entity's own `basis_refs` and any field the
  pack does not carry when its id survives, preserves entity order, and writes `Component@1`
  `volume_ids` back from the pack's components (required by the one-owner-per-volume rule above).
  A pack that says what the record already said produces the same record.
- `execute_option_candidate(...)` — the same arrangement as `execute_candidate`, different
  successor. Both now go through `_seat_pack` (seat pack + base check) and `_run_successor` (create
  run, retain what belongs to it, bind, view, guard, `run_project`), extracted with no behaviour
  change.
- `describe(...)` takes `proposal: Proposal | None` plus `proposal_id`, so
  `GET /api/candidates/{id}` answers for an option's candidate (and for a candidate whose proposal
  a restart lost) instead of 404-ing about a proposal that never existed. Its honesty line says
  which case it is.

**Routes and DTOs.** `GET /api/state/volumes` (a *separate* resource from the frame — see Concerns),
`POST /api/options` → 201, `GET /api/options`, `POST /api/options/{id}/select` → 202 and a job.
DTOs in `transport/options.py`: `MassingOptionRequestDto`, `EnvelopeDto`, `MassingMetricsDto`,
`LevelFootprintDto`, `EnvelopeFindingDto`, `MassingOptionDto`, `OptionsDto`, `VolumeDto`,
`VolumesDto`. `GET /api/options` carries `transforms[]` so a client offers no button the server
would refuse.

### Web

`src/features/options/OptionsPanel.tsx` — the baseline card and one card per option with label,
transform, footprint, GFA, floors, height, program share, envelope findings and honesty lines; a
volume `<select>`; buttons for add floor / remove floor / shift x / shift z / widen / split; and
`select` per card, disabled with a reason when the option was measured against a state the project
has left. Mounted from `Stage.tsx` on the same toolbar row as the frame (the frame panel sits
right, this one left, so both can be open together). Client methods in `src/api/client.ts`;
state, loaders and the two callbacks in `App.tsx`. A selection joins the transcript as the same
candidate card every other run gets — a selected massing *is* a candidate run.

i18n keys (both catalogs, same keys): `options.open`, `options.openTitle`, `options.ariaLabel`,
`options.title`, `options.subtitle`, `options.loading`, `options.noMassing`, `options.none`,
`options.baseline`, `options.baselineNote`, `options.volume`, `options.addFloor`,
`options.removeFloor`, `options.shiftX`, `options.shiftZ`, `options.shiftTitle`, `options.widen`,
`options.split`, `options.splitTitle`, `options.select`, `options.selectTitle`,
`options.staleTitle`, `options.footprint`, `options.gfa`, `options.floors`, `options.height`,
`options.efficiency`, `options.noEfficiency`, `options.squareMetres`, `options.metres`,
`options.finding.outside`, `options.finding.height`, `options.finding.far`,
`options.finding.other`. Styles under `/* massing options */` in `styles.css`.

`docs/PROTOCOL.md`: four rows added (all **provisional**), the count line updated to twenty-nine /
fifteen stable / fourteen provisional, and a new **§5.3 Massing options** stating the transform
vocabulary, the lattice the numbers are in, and what is and is not retained.

## On the kernel owner's note (mid-task steer)

There is **no named helper** for the volume box: `project_runner._check_produced_relations` read
`volume.fields["min"] / ["max"]` inline at what was line 811-818, and it was the only reader in the
kernel (`grep -rn 'entities_of("Volume@1")' archflow/` returns exactly that one line, plus the pack
assembly inside `developed_design_view`). Rather than implement against it with a TODO, I extracted
it: `state.record.volume_boxes_of` is now the one owner — `state.record` already owns "the entity
schema vocabulary (… `MassingLevel@1`, `Volume@1` …)" per the registry — the runner calls it, and
`massing_metrics` calls it. Nothing was copied. The same reasoning produced `schematic_pack_of`:
the *pack* assembly was inline in `developed_design_view`, and an option store that rebuilt it
would have been a second reader of the same four schemas.

## Tests

Kernel, from the worktree root:

```
py -3.12 -m unittest tests.test_massing_metrics
Ran 20 tests in 0.001s — OK

py -3.12 -m unittest discover -s tests -t .
Ran 373 tests in 19.952s — OK
```

Studio API, from `apps/archflow-studio/api` with `PYTHONPATH` at the worktree root:

```
py -3.12 -m unittest tests.test_options
Ran 26 tests in 7.954s — OK

py -3.12 -m unittest discover -s tests -t .
Ran 420 tests in 111.990s — OK (skipped=2)
```

Architecture:

```
py -3.12 tools/archcheck.py
ARCHITECTURE PASS (200 files, 1.695s)
```

Web, from `apps/archflow-studio/web` (`npm ci` first in both `web` and `web/tools/openapi-ts`,
neither had `node_modules`):

```
npm run -s api:generate   ✓ 4 files
npm run -s api:check      api:check — 16 generated files match the current schema.
npm run -s typecheck      ✓
npm run -s build          ✓ built in 238ms
```

The regenerated `src/api/generated/*` is committed with the change.

**Browser smoke test.** A throwaway project with the massing fixture, the API on 8129 and vite on
5387: the panel renders, `add_floor` produces a card reading 24 m² / 72 m² / 3 floors / 12 m
against a baseline of 24 / 48 / 2 / 8, and `select` ran the candidate through to
`candidate.succeeded` with 2 objects and a `runner-run-receipt`. It caught one real defect — the
two shift buttons shared a `title`, so they had the same accessible name; each now names its axis.
The temp project and both servers are gone.

## Registrations needed

`state.massing_metrics` (new module):

```json
{
  "module_id": "state.massing_metrics",
  "owner_path": "archflow/state/massing_metrics.py",
  "purpose": "What a massing option measures — footprint, gross floor area, floor count, height, program share — and how it stands in a buildable envelope, read from the record's own Volume@1 / MassingLevel@1 entities in the kernel's voxel lattice.",
  "owns": [
    "the massing quantities and their frame (MassingMetrics, LevelFootprint, massing_metrics)",
    "CELL_AREA_M2: one plan cell is one square metre, as schematic_proposal's SpatialGridBasis declares",
    "the union-of-plan-rectangles footprint, counted once where volumes overlap",
    "the three envelope findings and their whole vocabulary (EnvelopeFinding, envelope_check, VOLUME_OUTSIDE_ENVELOPE, HEIGHT_EXCEEDED, FAR_EXCEEDED)",
    "saying what could not be measured rather than measuring zero (MassingMetrics.honesty)",
    "the typed refusal of a record or envelope that makes a measurement impossible (MassingMetricsError)"
  ],
  "does_not_own": [
    "reading a Volume@1 box (state.record.volume_boxes_of)",
    "the massing entity vocabulary (state.record)",
    "the SiteBounds/SpatialLevel shapes the lattice comes from (state.spatial)",
    "transforming a massing or holding options beside one another (studio.options)",
    "compiling geometry from a massing (compilers/geometry.py)"
  ],
  "inputs": ["StateRecord", "a program-sheet-shaped mapping of node id to target area", "an envelope mapping"],
  "outputs": ["MassingMetrics", "EnvelopeFinding tuple"],
  "public_api": [
    "CELL_AREA_M2", "EnvelopeFinding", "FAR_EXCEEDED", "FINDING_CODES", "HEIGHT_EXCEEDED",
    "LevelFootprint", "MassingMetrics", "MassingMetricsError", "VOLUME_OUTSIDE_ENVELOPE",
    "envelope_check", "massing_metrics"
  ],
  "depends_on": ["state.record"],
  "used_by": ["apps/archflow-studio/api/archflow_studio_api/application/options.py"],
  "invariants": [
    "never writes the filesystem and decides nothing",
    "a plan bound that is not a whole cell is reported in honesty, never rounded",
    "an absent envelope key makes no finding",
    "efficiency is null, never zero, when no program target was given or there is no floor area"
  ],
  "status": "canonical",
  "tests": ["tests/test_massing_metrics.py"]
}
```

`studio.options` (new module):

```json
{
  "module_id": "studio.options",
  "owner_path": "apps/archflow-studio/api/archflow_studio_api/application/options.py",
  "purpose": "Hold several massing options beside the record's own, each one deterministic transform of its SchematicPack@1, each measured, each retained in a run of its own.",
  "owns": [
    "the closed transform vocabulary (TRANSFORMS: add_floor, remove_floor, shift_volume, scale_volume, split_volume, pack)",
    "each transform's arithmetic on the pack and its typed refusals",
    "the in-process option table and its ids and run ids (OptionStore, MassingOption, OptionsTable, RUN_PREFIX, PERSISTENCE)",
    "retaining one option as selected-spatial-option in its own option-NNN run",
    "the record's volumes as the panel reads them (RecordMassing, RecordVolume, record_massing, baseline_pack)"
  ],
  "does_not_own": [
    "measuring a massing (state.massing_metrics)",
    "reading the record's massing as a pack (state.record.schematic_pack_of)",
    "the successor record a selection runs (studio.candidate.massing_successor)",
    "running a candidate (studio.candidate)",
    "the record kind vocabulary (project.record_kinds)"
  ],
  "public_api": [
    "ADD_FLOOR", "MassingOption", "OptionStore", "OptionsTable", "PACK", "PERSISTENCE",
    "RecordMassing", "RecordVolume", "REMOVE_FLOOR", "RUN_PREFIX", "SCALE_VOLUME",
    "SHIFT_VOLUME", "SPLIT_VOLUME", "TRANSFORMS", "baseline_pack", "make_option", "record_massing"
  ],
  "depends_on": [
    "state.massing_metrics", "state.record", "state.spatial", "project.ports",
    "project.record_kinds", "studio.candidate", "studio.binding"
  ],
  "invariants": [
    "never writes the authored record and never touches canonical",
    "an option is measured on the successor record selecting it would run, not on an approximation",
    "a massing the kernel would refuse is refused at make time, with the request that made it",
    "the metrics are this process's memory; only the pack is retained"
  ],
  "status": "provisional",
  "tests": ["apps/archflow-studio/api/tests/test_options.py"]
}
```

`state.record` — two `public_api` symbols and two `owns` lines:

- `public_api`: add `schematic_pack_of`, `volume_boxes_of`.
- `owns`: add *"the one reading of a `Volume@1` box in the kernel's voxel lattice — x and z plan, y
  up, inclusive cells (`volume_boxes_of`)"* and amend the schematic-pack line to *"the schematic
  pack read out of the record and the bootstrap to the developed-design projection
  (`SchematicPack`, `schematic_pack_of`, `schematic_proposal`, `bootstrap_developed_state`,
  `developed_design_view`)"*.
- `used_by`: add `archflow/state/massing_metrics.py`.
- `tests`: add `tests/test_massing_metrics.py`.

`studio.candidate` — additions:

- `owns`: *"the massing successor: the authored record with its four massing schemas and its
  declared option replaced by one option's pack (`massing_successor`)"*, and *"one arrangement for
  every kind of candidate — the base check, the run, the harness stage and the seats
  (`_seat_pack`, `_run_successor`)"*.
- `public_api`: add `execute_option_candidate`, `massing_successor`, `MASSING_SCHEMAS`.
- `depends_on`: add `state.spatial` is **not** needed; `state.record` already there.
- `tests`: add `apps/archflow-studio/api/tests/test_options.py`.

`runtime.project_runner` — `depends_on` unchanged (`state.record` already there); its relation check
now reads volume boxes through `state.record.volume_boxes_of`.

**I did edit one governance file** and want it seen rather than found:
`governance/architecture_policy.json`'s `allowed_write_sites` entry for
`application/candidate.py` named the function `execute_candidate`, and the export-workspace `mkdir`
now lives in the extracted `_run_successor`, which both candidate kinds reach. I changed that one
entry's `function` from `execute_candidate` to `_run_successor` and extended its `reason`. Without
it `archcheck` reports `UNOWNED_FILESYSTEM_WRITE`. I did **not** touch `governance/module_registry.json`
or `archflow/project/record_kinds.py`.

## Concerns

1. **`selected-spatial-option` now has two writers.** The runner writes it for the option a run
   executed; `studio.options` writes it for an option nobody has chosen yet. The *value* is
   identical in kind (`SpatialOptionProposal@2`, built by `schematic_proposal`) and the kind's note
   — "the component tree the record's selected option resolves to" — is true of both. If the
   controller would rather an unchosen option not use that kind, the alternative is a new kind
   (below) and `_retain` is the one function to change.

2. **`GET /api/state/volumes` is a separate resource, not a field of the frame.** Reasoned in both
   route docstrings: the frame is what an *element* is positioned against — a `Level@1`, a
   `GridAxis@1` — and no element carries a coordinate. A `Volume@1` is positioned against neither;
   it declares its own box. Folding volumes into `FrameDto` would have made "frame" mean two things.
   It is one small route on `routes/state.py` and can be merged into the frame later without a
   client change if that judgement goes the other way.

3. **`option.footprint_cells` is carried through a transform unchanged.** It is authored data, not
   derived: a shifted or scaled volume does not move the declared cells. Every affected option
   carries an honesty line saying so and pointing at `metrics.footprintM2`, which is measured.
   Making the transforms re-author `footprint_cells` would be the studio writing a field the
   architect declared.

4. **`Component@1` volume ownership is maintained, and that is load-bearing.**
   `SpatialOptionProposal` requires exactly one semantic owner per volume, so `split_volume` gives
   the new half to the original's owner and `remove_floor` takes dropped volumes out of their
   owner's `volume_ids`. This is the studio editing the component tree's `volume_ids` — narrowly,
   and only to keep a rule the kernel would otherwise refuse the run for. It is worth a look.

5. **The option table is this process's memory.** Options made against an older state stay on the
   table with their own `stateDigest` and the card greys its `select` rather than the server hiding
   the option: what an architect looked at an hour ago is not deleted because the record moved.

## Left out and why

- **`runner-schematic-pack` was not created.** Two reasons, either sufficient. It is not a
  registered kind, and `put_json` refuses an unregistered one; `record_kinds.py` is the program-sheet
  worker's file this wave and I was told not to touch it, so per common-brief rule 3 a new kind is a
  registration request, not an edit. And the name is *retired* vocabulary:
  `tests/test_project_runner.py:249` asserts the runner leaves no `runner-schematic-pack-*` record,
  so re-registering it would resurrect a lane's word the spine gave up. The option is retained under
  `selected-spatial-option` instead. **If the controller wants the kind anyway**, the registration is
  `RecordKind("runner-schematic-pack", "SchematicPack@1", _RUN_RECORD, "one massing option a studio
  process is holding, retained in its own run before anybody has chosen it")` and `_retain` in
  `application/options.py` is the only function to change.

- **The metrics are not retained inside the pack payload.** The brief allows this if the pack schema
  refuses extra keys. `SchematicPack.from_dict` would tolerate a `metrics` key, but what is retained
  is the `SpatialOptionProposal@2` the kind declares, and `SpatialOptionProposal.from_dict` is
  `_exact`-validated — an extra key is refused. There is no record kind whose payload is a set of
  measurements. So the metrics live on the option DTO and in the store, `persistence` on every
  option says *"the option itself is in-memory (not version history); its pack is retained in its
  own run"*, and PROTOCOL §5.3 says the same.

- **`select` writes no `selected-spatial-option` of its own into the candidate run.** The brief asks
  for one; the runner already writes exactly it, from the successor record's own massing
  (`developed_design_view` → `bootstrap_developed_state` → `schematic_proposal`). A second copy
  would be the studio restating the runner's fact. `test_options.py` asserts the candidate run holds
  exactly one, with three levels after an `add_floor`.

- **No generative agent, no geometry export, no write to the authored record.** As the brief says.
  `pack` is the socket; `test_the_authored_record_is_byte_identical_afterwards` asserts the file's
  bytes and the project HEAD are unchanged across a full select.
