# W2-A — The program sheet: a record, two-way with the spaces

## Status

DONE_WITH_CONCERNS

The sheet derives from a record and applies to one, the two studio routes work,
the panel is in the shell, and everything is tested. Two things are not clean
and are written out in full below: a kernel constraint refuses a program-only
zone in a record that already draws massing (so the brief's `volume_ids = []`
cannot always be applied, and the studio refuses it up front instead of
failing a job), and a candidate made from a sheet is not readable through
`GET /api/candidates/{id}` because it has no proposal.

The coordinator's two corrections were applied in full: **no record kind** and
**no edit to `archflow/project/record_kinds.py`** — the sheet is a
work-in-progress input at `input/runner/program-sheet.json` owned by
`project.inputs` — and `saveInput` is honoured only in local mode, answering
`409 WIP_WRITE_REMOTE` on a remote server while still making the candidate.

## Commits

- `6bd8ecd` — the kernel: `state.program_sheet`, the layout path, the inputs
  reader and writer, and their tests
- `0c9e97e` — the studio: `/api/program`, `/api/semantics`, the successor
  extraction in `studio.candidate`, the web panel, the regenerated client,
  PROTOCOL.md, and this report

(Exact hashes are in the final message; both are on `worktree-agent-aee5dda3ab99d7000`.)

## Tests

Kernel, from the worktree root:

```
py -3.12 -m unittest tests.test_program_sheet tests.test_project_inputs
Ran 46 tests in 1.302s — OK

py -3.12 -m unittest discover -s tests -t .
Ran 387 tests in 21.742s — OK
```

Studio API, from `apps/archflow-studio/api` with `PYTHONPATH` = worktree root:

```
py -3.12 -m unittest tests.test_program tests.test_candidate tests.test_protocol
Ran 66 tests in 25.669s — OK (skipped=1)

py -3.12 -m unittest discover -s tests -t .
Ran 414 tests in 114.112s — OK (skipped=2)
```

Web, from `apps/archflow-studio/web` (`npm ci` was needed in both
`web/` and `web/tools/openapi-ts/`):

```
npm run -s api:generate && npm run -s api:check   →  16 generated files match the current schema.
npm run -s typecheck                              →  clean
npm run -s build                                  →  ✓ built in 276ms
```

The regenerated `src/api/generated/*` is committed.

**Live check.** A throwaway project (the massing fixture) was served by the API
on a free port with the dev server beside it, and the panel was driven in the
browser: the table renders with the department, the space row, the totals
(target 24 m² / drawn 24 m²), the adjacency section and the three honesty
lines; the function dropdown is populated from `GET /api/semantics`; "apply as
candidate" queued a run that succeeded, the transcript carried the three
honesty lines verbatim, and `input/runner/state-record.json` was untouched. Two
display bugs were found and fixed this way (below). The fixture project and
both servers were removed afterwards; nothing under `D:\PROJECTS` was touched
and `.claude/launch.json` is unmodified.

## What was built

### Kernel — `archflow/state/program_sheet.py` (new, pure, no I/O)

`sheet_from_record(record, *, state_digest=None) -> dict` and
`apply_sheet(record, sheet) -> StateRecord`, plus `totals_of(sheet)` (the
studio recomputes totals rather than trusting a client's) and
`validate_sheet(sheet)`. `PROGRAM_SHEET_SCHEMA = "ProgramSheet@1"`;
`ProgramSheetError(ValueError)` is the typed refusal.

**Kernel kinds used for adjacency.** Only two of the four requirements have a
kind in `ArchitecturalRelationKind`, and no kind was invented for the other
two:

| requirement | kernel kind | why |
| --- | --- | --- |
| `adjacent` | `adjacent` | the adjacency kind itself |
| `apart` | `clearance` | the kernel's word for free space that must remain between two zones; the record's zone relations already use it for exactly this ("corridor to hall clearance", `project_runner._check_produced_relations`) |
| `near` | **none** | the vocabulary has no kind for "close but not touching"; `clearance` says the opposite |
| `visual` | **none** | `role.view` is a semantic role of a component, not a relation between two zones |

`near` and `visual` are accepted *in a sheet* and refused on apply, with all 25
kernel kinds named in the message. Tested both ways.

**Conventions this module fixes**, because the record had none:

- A zone says which sheet row it is by `program_node_refs` entry
  `program:<department_id>/<space_id>`; both halves are portable identifiers, so
  the ref splits on its one slash. A zone naming none falls into department
  `unassigned` under its own entity id.
- Area is the **union** of the plan rectangles of the zone's `Volume@1` boxes
  that stand on the zone's own levels, at 1 m² per cell — the record's own basis
  (`schematic_proposal` builds `SpatialGridBasis(horizontal_area_per_cell=1.0)`),
  counted inclusively the way `SiteBounds.volume` counts. Union, not sum: two
  boxes stacked on one zone are one footprint. A zone with no readable box has
  `null`, never `0`, and an honesty line names it.
- `clear_height_m` is always `null` in a derived sheet, with an honesty line: a
  `MassingLevel@1` height is the level's full height, not a clear height.
- A row's `function` is the `semantic_kind` of the component the zone names —
  by `fields["component_id"]` or by `parent_id`, the two ways an `Element@1`
  names one — else its first role id, else `null`.
- A derived sheet's `name` is the id: the record has nowhere to keep a display
  name, and inventing one would put a second name for one space on the wire.

**One field added to the brief's payload:** each space carries
`mapped_area_m2` beside `target_area_m2`. The brief's `totals` asks for both a
target and a mapped sum, which cannot be computed without a per-row mapped
value; it is never authored and is recomputed from the record on every read.

`apply_sheet` only ever adds. A sheet space with no `zone_id` becomes a
`Space@1` (`program_node_refs` = the one ref, `level_ids` from the sheet,
`volume_ids = []`); one with a `zone_id` gets the program ref appended if it
lacks it and nothing else. An adjacency with no `relation_id` becomes one
`Relation` (`program-<requirement>-<subject>-<object>`) plus the `Connection@1`
that carries it (`connection-<relation id>`). Every collision — an id that names
an existing entity or relation, a level the record does not hold, an adjacency
end that is neither sheet space nor zone — is a named refusal.

### Kernel — the input file

- `archflow/project/layout.py`: `PROGRAM_SHEET_PATH = "input/runner/program-sheet.json"`
  and `ProjectLayout.program_sheet`, beside `authored_record` and `seat_pack`.
- `archflow/project/inputs.py`: `load_program_sheet_file(repository)` mirroring
  `load_seat_pack_file` (typed `ProgramSheetMissing` / `ProgramSheetInvalid`,
  each naming the path), and `write_program_sheet_file(repository, payload)` —
  the single writer. It refuses a payload whose schema literal is not
  `ProgramSheet@1` **before** touching the file, writes UTF-8 bytes with LF and
  sorted keys (so two writes of one sheet are byte-identical and the `sha256`
  means something), and touches nothing outside `input/`.

### Studio API

- `application/program.py` — `read_program` (input sheet when the file exists,
  else the derivation; `honesty` says which, and whether the input sheet's
  `state_digest` still matches), `successor_for`, `save_input_sheet`,
  `closure_of_sheet`, `candidate_run_id`, `candidate_honesty`,
  `semantic_terms`.
- `transport/program.py` — the DTOs and the two mappings between the kernel's
  `snake_case` payload and the wire's `camelCase`.
- `transport/semantics.py`, and `GET /api/semantics` in `routes/program.py` —
  nothing else exposed the registry, so the panel's dropdown would otherwise
  have been a copy of two Python tables the record can refuse.
- `routes/program.py` — `GET /api/program`, `POST /api/program`,
  `GET /api/semantics`.
- `routes/__init__.py` — one import and one `include_router`.
- `protocol.py` — `program` added to `BASE_CAPABILITIES`.
- `application/candidate.py` — **extracted** `run_successor(binding, settings,
  successor, run_id, *, retain_before_run=None)` out of `execute_candidate`.
  Everything below "the successor record" is now one function, so a second way
  of *producing* a successor (a sheet, a massing option) is not a second way of
  *running* one. `execute_candidate`'s behaviour is unchanged — its
  `INTENT_COMPILATION` retention is the `retain_before_run` callback — and
  `tests/test_candidate.py` passes untouched.

**DTOs.** `ProgramSpaceDto` (`spaceId`, `name`, `function`, `targetAreaM2`,
`count`, `clearHeightM`, `levelIds`, `zoneId`, `mappedAreaM2`),
`ProgramDepartmentDto`, `ProgramAdjacencyDto` (`fromSpaceId`, `toSpaceId`,
`requirement`, `relationId`), `ProgramTotalsDto` (`targetAreaM2`,
`mappedAreaM2`, `unmappedSpaces`), `ProgramSheetDto` (`schema`, `projectId`,
`stateDigest`, `departments`, `adjacencies`, `totals`, `honesty`), `ProgramDto`
(`source`, `sheet`, `stateDigest`), `ProgramApplyRequestDto` (`stateDigest`,
`sheet`, `saveInput`), `ProgramCandidateDto` (`jobId`, `candidateId`, `status`,
`totals`, `savedInput`, `honesty`), `SemanticTermDto`, `SemanticsDto`.

**Refusal codes added:** `PROGRAM_SHEET_INVALID` (422),
`PROGRAM_SHEET_NOT_APPLICABLE` (422), `WIP_WRITE_REMOTE` (409). `STALE_BASE`
(409) is the existing one, reused.

### Web

`src/features/program/ProgramPanel.tsx` (new feature folder), mounted over the
stage beside the frame panel with its own toolbar button. `App.tsx` holds the
server's answer and the tab's edited copy as two values, reads on open and on
`recordDigest` change, and appends a **system** transcript line on apply rather
than a candidate card (a card polls `GET /api/candidates/{id}`, which a sheet
candidate has no proposal for). `Stage.tsx` takes `programPanel` / `programOpen`
/ `onToggleProgram` exactly as it takes the frame's three.

**i18n keys** (`program.*`, both tables, same keys): `open`, `openTitle`,
`ariaLabel`, `title`, `subtitle`, `loading`, `empty`, `sourceInput`,
`sourceDerived`, `space`, `function`, `target`, `count`, `clearHeight`,
`mapped`, `zone`, `noNumber`, `unmapped`, `functionUnset`, `functionAsWritten`,
`nameOf`, `functionOf`, `targetOf`, `countOf`, `clearHeightOf`, `totalTarget`,
`totalMapped`, `totalUnmapped`, `adjacencies`, `noAdjacencies`,
`requirement.adjacent`, `requirement.near`, `requirement.apart`,
`requirement.visual`, `willDeclare`, `apply`, `applying`, `applyTitle`,
`keepSheet`, `reread`. No literal UI string in the TSX.

Styles under `/* program sheet */` in `styles.css`.

Two bugs the browser check caught and fixed: a derived department (and space)
printed its id twice, since name == id there; and a `function` that is a
registered *alias or compound phrase* rather than a role id (`controlled-entry`)
fell out of the dropdown and displayed as "not stated" — it is now offered as
its own option and shows what the sheet actually says.

### PROTOCOL.md

Three provisional rows (`GET /api/program`, `POST /api/program`,
`GET /api/semantics`), the count line updated to twenty-eight / thirteen
provisional, `program` added to the capability example and to §10.3, a
**The program sheet** paragraph naming every refusal, and a paragraph in §10.1
saying that writing an authored work-in-progress file is a local-mode act until
per-user authoring lands.

## Registrations needed

The controller applies all of these. I edited neither
`governance/module_registry.json` nor `governance/architecture_policy.json`.

### 1. New module: `state.program_sheet`

```
module_id:   state.program_sheet
owner_path:  archflow/state/program_sheet.py
purpose:     The architect's program sheet and the two directions between it and a
             record's spatial option: one sheet row per Space@1, and a new record
             carrying what a sheet adds.
owns:
  - the ProgramSheet@1 payload shape and its refusals (ProgramSheetError)
  - reading a sheet out of a record: department and space id from a zone's
    program:<department>/<space> ref, area from the union of its Volume@1 plan
    boxes on its own levels, function from the component the zone names,
    adjacency rows from Connection@1 plus the relation kind
  - the four program requirements and the kernel relation kind each becomes
    (adjacent -> adjacent, apart -> clearance; near and visual have none and are
    refused with the vocabulary named)
  - applying a sheet as additions only: a Space@1 per unmapped space, a Relation
    plus its Connection@1 per undeclared adjacency, and a program_node_refs entry
    on a mapped zone that lacks one
  - the sheet's totals (target x count, mapped, unmapped ids)
does_not_own:
  - the relation kind vocabulary (relations.contracts.ArchitecturalRelationKind)
  - the semantic vocabulary a function names (semantics.registry, semantics.roles)
  - the entity schemas and their referential integrity (state.record)
  - where the sheet is kept (project.layout) or its reading and writing (project.inputs)
  - deciding which run applies it (studio.*); it creates no run and writes nothing
inputs:      StateRecord, ProgramSheet@1 mapping, optional state digest
outputs:     ProgramSheet@1 dict, StateRecord
public_api:  sheet_from_record, apply_sheet, totals_of, validate_sheet,
             PROGRAM_SHEET_SCHEMA, REQUIREMENT_KINDS, REQUIREMENTS,
             PROGRAM_REF_PREFIX, ProgramSheetError
depends_on:  state.record, relations.contracts, semantics.registry, project.refs
used_by:     archflow/project/inputs.py,
             apps/archflow-studio/api/archflow_studio_api/application/program.py,
             apps/archflow-studio/api/archflow_studio_api/routes/program.py
invariants:
  - pure: no I/O, no run, no record written
  - never deletes an entity or a relation, and changes no field of an existing
    entity except a program_node_refs entry it lacks
  - no area is invented: a zone with no readable Volume@1 box has null, not zero
  - no relation kind is invented: a requirement the kernel has no kind for is
    refused with the whole vocabulary named
  - a function is resolved by semantics.registry or refused with the nearest ids
status:      canonical
tests:       tests/test_program_sheet.py
```

### 2. `project.inputs` — it becomes the single WIP writer for the program sheet

`owns` — add:

```
  - reading the program sheet at the layout's program_sheet path, and the schema
    literal check that says a file at that path is a ProgramSheet@1 at all
  - writing that one file: the single writer of input/runner/program-sheet.json,
    UTF-8, LF, sorted keys, refusing a payload of another schema before it writes
```

`does_not_own` — add:

```
  - the meaning of the sheet it read or wrote (state.program_sheet)
  - writing the authored record or the seat pack: those are a person's files
  - deciding when a sheet may be written (studio: local mode only, on request)
```

`public_api` — add: `ProgramSheetFile`, `load_program_sheet_file`,
`write_program_sheet_file`, `ProgramSheetMissing`, `ProgramSheetInvalid`.

`depends_on` — add `state.program_sheet` (this is the
`REGISTRY_DEPENDS_ON_DRIFT` finding archcheck reports).

`outputs` — add `ProgramSheetFile`. `inputs` — add
`input/runner/program-sheet.json bytes`. `used_by` — add
`apps/archflow-studio/api/archflow_studio_api/application/program.py`.

`invariants` — the first line must change. It currently reads:

```
  - never writes: the work-in-progress container is read, and nothing about it is
    retained until a run uses it
```

Replace with:

```
  - writes exactly one of the three authored files, the program sheet, and only
    where a caller asks; the record and the seat pack are read and never written
  - a work-in-progress write touches nothing under runs/, canonical/ or objects/,
    and nothing about the file is retained until a run uses it
  - the schema literal is checked on the way in and on the way out, so a file at
    that path always claims to be what its path says
```

`purpose` — mention the third file.

### 3. `project.layout`

`owns` — the last line becomes: "the names of the three authored
work-in-progress files (`input/runner/state-record.json`,
`input/runner/seats.json`, `input/runner/program-sheet.json`)".
`public_api` — add `PROGRAM_SHEET_PATH`.

### 4. `studio.*`

`studio.candidate` — `public_api` gains `run_successor`; `owns` gains "running
one successor record as a detached candidate, whatever produced the successor
(seats, harness stage, binding, export workspaces, run_project)"; `used_by`
gains `apps/archflow-studio/api/archflow_studio_api/routes/program.py`.

New module `studio.program`:

```
module_id:   studio.program
owner_path:  apps/archflow-studio/api/archflow_studio_api/application/program.py
purpose:     Serve the program sheet both ways: which sheet a reader gets, and how
             one becomes a candidate run without touching the authored record.
owns:
  - choosing between the architect's input sheet and the record's derivation, and
    saying which, and whether the input sheet's state digest still matches
  - the pre-flight design view of the successor, so a sheet the kernel will not
    view is a refusal now rather than a failed job later
  - the local-mode-only rule for writing the authored sheet (WIP_WRITE_REMOTE)
  - the sheet's queue closure and its candidate run id
does_not_own:
  - deriving or applying a sheet (state.program_sheet)
  - reading or writing the input file (project.inputs)
  - running the successor (studio.candidate.run_successor)
public_api:  ProgramView, ProgramCandidate, read_program, successor_for,
             save_input_sheet, closure_of_sheet, candidate_run_id,
             candidate_honesty, semantic_terms
depends_on:  state.program_sheet, project.inputs, project.layout, semantics.roles,
             semantics.conditions, studio.binding, studio.candidate
tests:       apps/archflow-studio/api/tests/test_program.py
status:      provisional
```

`studio.shell` — `tests` gains `apps/archflow-studio/api/tests/test_program.py`
if that entry lists route tests.

### 5. `governance/architecture_policy.json` — `allowed_write_sites`

Two changes. `py -3.12 tools/archcheck.py` reports exactly these until they land
(plus the `depends_on` drift above); nothing else in the run is mine.

**(a) New site** — the program sheet's writer:

```json
{
  "path": "archflow/project/inputs.py",
  "function": "write_program_sheet_file",
  "operations": ["mkdir", "write_bytes"],
  "kind": "wip_input",
  "owner": "project.inputs",
  "reason": "writes input/runner/program-sheet.json only when POST /api/program carries saveInput=true in local mode; never writes state-record.json"
}
```

`wip_input` is a new `kind` value; the four existing kinds are
`project_repository`, `adapter_workspace`, `cli_workspace`,
`provider_tempdir`, `rendered_docs`, and none of them describes a
work-in-progress authored file.

**(b) One field on the existing studio site.** The per-seat export-workspace
`mkdir` moved from `execute_candidate` into `run_successor` when the successor
run was extracted. The entry at
`apps/archflow-studio/api/archflow_studio_api/application/candidate.py` must
change `"function": "execute_candidate"` to `"function": "run_successor"`;
path, operations (`mkdir`), kind (`cli_workspace`), owner (`studio.candidate`)
and reason are unchanged.

## Concerns

1. **A program-only zone cannot enter a record that draws massing.** The brief
   says a sheet space with no zone becomes a `Space@1` with `volume_ids = []`.
   The record accepts that, but `SpatialZone` refuses an empty `volume_ids`
   (`spatial.py:_ids(..., allow_empty=False)`), so
   `developed_design_view` — which every candidate builds as its first act —
   refuses the successor whenever the record has `Volume@1` **and** `Space@1`
   **and** `MassingLevel@1`. The kernel's position is coherent: a zone is an
   occupied region, and a room in the brief that nothing has been drawn for yet
   is not one.

   I implemented the brief as written and made the consequence visible rather
   than working around it: `successor_for` builds the design view before the run
   is queued, so this arrives as `422 PROGRAM_SHEET_NOT_APPLICABLE` with the
   kernel's own sentence and no run directory, instead of a job that fails a
   minute later. `MassingRecordTests.test_a_program_only_space_is_refused_before_any_run_is_made`
   asserts it. Adding a space still works on a record with no massing, and
   mapping and editing existing zones works everywhere.

   The design question for the controller: either a program-only room is not a
   `Space@1` (and needs its own place in the record), or `SpatialZone` should
   allow an empty `volume_ids`. Both are kernel decisions and neither is mine.

2. **A candidate made from a sheet is not readable through
   `GET /api/candidates/{id}`.** That route resolves `job.proposal_id` in the
   proposal store and 404s `PROPOSAL_NOT_FOUND` for a sheet. The brief allowed
   "the smallest proposal object that names the sheet as its origin", but a
   `Proposal` requires `key`, `old`, `new`, a `DecisionOperator` and an
   `Impact`: every one of those would be a false statement about a scalar edit
   that did not happen, and `candidate._honesty` would then print a sentence
   about a value nobody changed. I chose the visible gap instead. The 202's
   `honesty` says it verbatim, and so does PROTOCOL.md.

   The fix is small and I did not take it because it changes a shared DTO the
   other W2 worker also touches: make `describe`'s `proposal` argument
   `Proposal | None` and `CandidateDto.proposalId` nullable, and have
   `read_candidate` pass `None` when the store has no proposal — the way
   `compare_candidate` already tolerates a missing one. Worth scheduling once
   the wave's DTO churn has settled, since the massing-options worker's
   candidates have the same shape.

3. **`saveInput` on a remote server answers 409 after queueing the run.** That
   is what the correction specified — "the candidate is still made" — so the
   refusal names the candidate and the job in its detail, and the client keeps
   the run it asked for. It is a 409 whose side effect succeeded, which is
   unusual; it is documented in PROTOCOL.md §10.1 and in the route's docstring
   so nobody meets it as a surprise.

4. **The 1 m² grid cell.** `HORIZONTAL_AREA_PER_CELL_M2 = 1.0` is written in
   `program_sheet.py` with a comment pointing at `schematic_proposal`, which
   hard-codes the same basis when it builds the record's spatial option. It is
   the record's own basis, not an assumption of mine, but it is stated in two
   places now and every derived area carries an honesty line naming it. If the
   basis ever becomes authored, both sites move together.

5. **`sheet_from_record` takes a keyword-only `state_digest`.** The brief's
   signature is `sheet_from_record(record)`, which still works: with no digest
   given, the record's own is taken where it is bound and is `None` — with an
   honesty line — where it is not. The studio passes the projection's, so the
   sheet cites the digest the client will send back.

## Left out and why

- **No `program-sheet` record kind, and `record_kinds.py` is untouched** — the
  coordinator's correction. The sheet is an input file; the successor record is
  retained as that run's ordinary `state-record`, and no separate sheet record
  is written into the candidate run. The brief's item 5 assertion "the candidate
  run holds a `program-sheet` record" is replaced by
  `ApplyTests.test_the_candidate_run_holds_the_record_the_sheet_made`, which
  reads the retained `state-record` and finds the added `Space@1`, the added
  `Connection@1` and the `adjacent` relation in it.
- **`near` and `visual` cannot be applied** — the kernel vocabulary has no kind
  for either, and the brief forbids inventing one. A sheet may state them and
  applying one is refused by name.
- **`GET /api/program` derives on the portico fixture, but applying is tested on
  the demo fixture** — `make_portico_project`'s seat pack owns a component with
  no element, which the runner refuses, so nothing in that fixture can run a
  candidate at all. `DeriveTests` reads the portico record (the brief's "derive
  on the portico fixture"); the apply tests use the demo fixture with one zone
  added, which is the one the existing candidate tests actually run.
- **No `/api/projects/{projectId}/program` scoped form** — no other resource in
  this wave has one either; the unscoped default-project path is what §10.2
  describes.
