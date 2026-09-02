# P089 — Record-driven reconstruction runner

- Origin: Planning
- Status: Active (first cut landed 2026-09-02; P069 dropped from the dependencies — the Pantheon card is codex's and the runner does not need it)
- Depends on: P083, P087, P088, P092, P095, P098

## Goal

Extract the reconstruction execution protocol — stage loop, record
writes, gate invocation, CAD execution, readback — that today exists as
three parallel per-building monoliths (`run_pantheon_reconstruction.py`
5,255 lines, `run_parthenon_reconstruction.py` plus its stage-4 variant
8,829 lines, and villa workspace authoring scripts; zero shared function
signatures between the pantheon and parthenon pair) into one
record-driven runner whose per-building input is a project record pack.
Record-ref loading verifies digests at the repository port, retiring
hand-pinned SHA constants (116 in villa authoring scripts alone). The
runner owns orchestration only: no derivation, gate, or authority
semantics change, and no building answer enters the framework.

## Acceptance

- One runner replays the pantheon, parthenon, and villa stage flows from
  their retained records, producing digest-equal stage packs wherever
  predecessors are unchanged.
- Per-building runner tools retire to archive stubs; building-specific
  behavior lives only in project records.
- Hand-pinned SHA constants and load-module-by-SHA patterns reach zero
  on the authoring path; loading is by verified record ref.
- Per-stage wall time is reported in the run summary.
- Before any seat write, require an exact retained workflow/envelope; Stage N
  also requires the exact prior state/exit/SATISFIED closure and base/branch.
- Phase, project Stage, and Seat remain distinct; seat outcome is not Stage acceptance.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `tools/`
- `archflow/runtime/`
- `archflow/state/`
- `archflow/capabilities/`
- `archflow/project/`
- `tests/`
- `probes/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Runner replay equivalence against retained pantheon, parthenon, and
  villa records.
- Record-ref digest verification fail-closed paths.
- Architecture firewall.

## Stop conditions

- Stop before changing any gate or acceptance semantics.
- Stop if any building-type answer must be hardcoded for the runner to
  proceed.
- Stop before touching `run_pantheon_reconstruction.py` while P069 holds
  it in an active write scope — sequencing is enforced by the dependency.

## First cut (2026-09-02)

`archflow/runtime/project_runner.py` + `tools/run_project.py`: a project
enters as record packs — `SchematicPack@1` (levels, volumes, zones,
connections, component tree, evidence) becomes a real
`SpatialOptionProposal` → `SchematicOption` → `SelectedSchematicInput` →
`DevelopedDesignState` through the state dataclasses' own validation
(the declared selection remains an authority-free stage input, not a
replacement for stage entry/closure);
`ElementPack@1` lists what each seat authors (wall with hosted openings
and types, prism, ring, loft, column-array, dome-cap, or a typed
declination with a reason) bound to project levels by id, heights as
level differences or the element's own dimensions; `SeatPack@1`,
`ProjectLevels@1`, `ProjectGrids@1`. The runner schedules seat rounds
(P095), produces, authors one proposal per seat through the real
producer (a recorded provider that returns the proposal and refuses a
second round), gates coverage (every owned leaf has an element or a
declination; unowned leaves are listed on the run receipt), checks seat
datums (P098), compiles handovers with realized bounds as exclusions for
consuming seats, optionally exports to Rhino with read-back, and writes
seat-round receipts with wall time and an authority-free run receipt.
Tests: `tests/test_project_runner.py` (5): pack → state, malformed
packs, two seats through the producer with handover datums, an earlier
seat's realized bounds refusing a later opening, coverage strict/relaxed.

Rocca Pisana ran from packs end to end (`rocca-pisana/make_packs.py`,
run `runner-002`): structure seat 9 objects, envelope seat 20 objects,
both exported and read back, 78 s wall time, 11 unowned leaves listed,
2 declinations with reasons. The three hand-written scripts it replaces
(`derive_south_portico.py`, `derive_main_block.py`, and the villa seat
scripts' orchestration) are 300–500 lines each.

Not yet (the card's acceptance stands): pantheon and parthenon replay
from retained records; villa replay beyond the west band; hand-pinned
SHA constants on the authoring path; the front of the pipeline (brief,
program, site, build policy) is still declared, not compiled from
evidence; the `portico_geometry` producer is not in the registry yet.

## Stage/seat authority correction (2026-09-02)

The first cut mislabeled discipline-seat proposal outcomes as Stage results.
The hardened runner now requires a retained ordered workflow and exact
stage-run envelope before any seat write. Stage N additionally binds the
exact project/base/semantic branch, Stage N-1 envelope/state, retained exit,
and SATISFIED composite closure. `SeatRoundReceipt@1` and
`RunnerRunReceipt@2.seat_execution_complete` carry no stage authority; the
current close obligation remains OPEN. Legacy `CaseVote@1` records migrate to
organisation-only evidence and cannot promote templates. Villa Rotonda's
project-owned Stage 0-5 workflow is frozen in P036 run `workflow-001`; all
older runs remain `LEGACY_UNQUALIFIED_BASIS`, with canonical HEAD at version 0.

## The runner reads the State Record (2026-09-02 night, Claude)

`run_project(repository, run=, stage_guard=, record: StateRecord, seats=, options=)`. One input: the
record's Component/MassingLevel/Volume/Space/Connection entities are the spatial option, its Level/GridAxis
entities the published datums, its Element@1 entities the rows the canonical producers read
(`element_producers.produce_rows` in `production_order`). Retired here: `SchematicPack`/`ElementPack` as runner
inputs, `ElementSpec`, `ProducerInputs`, the runner's private `PRODUCERS`/`produce_elements`, the `runner-schematic-pack`/
`runner-element-pack` records (`RunnerRunReceipt@3` carries `state_record_ref`). `SchematicPack` +
`bootstrap_developed_state` remain only as the bridge inside `developed_design_view` until the compiler reads the record.
Relations the producers build are checked against the compiled bounds per seat (`seat-relation-check`); a violated
relation is a typed failure. Export: artifact per program digest in the stage workspace; reuse / restamp / patch /
rebuild by receipt evidence (P103), `--patch-oracle` rebuilds beside every patch and compares.

Equivalence receipts (`tools/verify_state_record.py`, an equivalence-harness workflow frozen in the run, no stage
authority): villa `project://villa-rotonda-reconstruction/runs/equivalence-001/records/state-record-equivalence-6bcb52152c6766ba969b1f44c986066b39ff7a1ee9e5fbfa4715c124108f68de.json` (state digest identical to runner-002; 51 objects, worst 0.0 m); Rocca `project://rocca-pisana/runs/equivalence-002/records/state-record-equivalence-1cb2d33473614addcdd80254eb6ff8aa0ed8dbf1d1f12ebbc2db38683e6a5eda.json`
(27 objects, worst 0.0 m; state digest differs only because the massing levels had to be renamed — "piano-nobile",
"drum", "dome" were both component ids and massing/element ids in the old pack, and an entity id is one thing).

