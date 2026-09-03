# P089 — Record-driven reconstruction runner

- Origin: Planning
- Status: Done (first cut landed 2026-09-02; P069 dropped from the dependencies — the Pantheon card is codex's and the runner does not need it)
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

- One runner drives a whole building from its retained records, with the
  building's behaviour in the record rather than in a per-building tool.
  Proven on two: villa west band and Rocca Pisana reproduce their prior runs
  object by object at 0.0 m (`equivalence-003` in both projects).
- The monuments (pantheon, parthenon) are **not** replayed here, and the
  per-building monoliths are **not** retired here. See "Why the monuments are
  not in this card" below; the work is carded as P105 and P106.
- Hand-pinned SHA constants and load-module-by-SHA patterns are zero on the
  authoring path: load-module-by-SHA is absent from the repository, and every
  remaining pinned digest was audited (see below) and is either an external
  evidence digest, a canonical-HEAD expectation, a frozen historical record
  body, or a default selecting one of several records of one kind.
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

## Why the monuments are not in this card (2026-09-02, audited)

The first cut promised that one runner would replay the pantheon and the parthenon and that the per-building tools
would retire to stubs. Both promises were made before anyone measured what those tools hold. Measured now:

**The monuments' geometry is written in a vocabulary the canonical producers do not have.** The parthenon's
retained stage-4 program is 687 operations across 23 operation kinds, 20 of them a classical-order vocabulary:
`doric_shaft`, `doric_echinus`, `doric_abacus`, `doric_neck`, `triglyph`, `eave_geison`, `eave_sima`,
`pediment_raking_geison`, `pediment_raking_sima`, `pediment_tympanum`, `marble_cover_tile_field`,
`marble_pan_tile_field`, `marble_eave_terminal`, `marble_ridge_terminal`, `timber_rafter_field`,
`timber_bearing_beam`, `timber_ridge_beam`, `bearing_block`, `acroterion_seat`, `ionic_column`. The pantheon's uses
`revolve`, `solid` and `boolean_difference` chains. The canonical set is ten generic producers. Replaying either
building means writing that vocabulary as producers (real work, and worth doing) or flattening 687 semantic
operations into anonymous prisms and lofts, which would discard exactly what made the records worth keeping.

**The monoliths are not only geometry.** `run_pantheon_reconstruction.py` alone carries stage plans, stage
contracts, stage-closure gates, detail enrichment, STL ingestion, Rhino overlay scripts, symmetry findings,
realized declaration values and candidate structure issues, none of which is record-driven and none of which the
runner replaces. Retiring it to a stub today would delete working capability, not a parallel abstraction.

So the two promises are moved, not quietly dropped: **P105** gives the producers that vocabulary, **P106** does the
replay and the retirement on top of it. What this card actually delivered stands on its own: a record-driven,
stage-guarded runner proven end to end on two buildings.

## Pinned-digest audit (2026-09-02)

Thirty 64-hex constants remain in `tools/`. None of them is a record loaded by a hand-pinned digest where the
repository port could have resolved it:

| Where | What it pins | Verdict |
|---|---|---|
| `run_monument_fidelity.py`, `run_inverse_derivation.py` | the external `pantheon.schem` golden file | correct as pinned: external evidence must be digest-bound |
| `run_parthenon_stage4_reconstruction.py` | the canonical HEAD version 0 state digest | correct as pinned: the P087 exact-base binding is the point |
| `parthenon_stage4_correction_records.py` | stage-3 and failed-stage-4 outputs, inside record bodies | correct as pinned: the module is path-neutral by design and these are frozen historical assertions |
| `refine_parthenon_stage4_visual_regions.py` | one of two `visual-candidate-manifest` records in `research-005` | correct as pinned: kind alone is ambiguous there, and naming the successor is the decision |

A `resolve_json(run, destination, record_kind)` port method was written for this item and then reverted: it had no
caller, and adding an abstraction that retires nothing is what the one-canonical-in rule forbids.


## Completion

- Completed: 2026-09-02
- Evidence: Record-driven runner: run_project takes one StateRecord@1; the pack inputs, ElementSpec, ProducerInputs and the runner's private producers retired; RunnerRunReceipt@3
- Evidence: Stage guard requires an exact retained workflow/envelope and, for Stage N, the predecessor exit and SATISFIED closure; seat outcome is never stage acceptance
- Evidence: Proven on two buildings at 0.0 m (villa and Rocca equivalence-003); the monuments and the monolith retirement moved to P105 and P106 with the measurements that made them separate work
- Evidence: Pinned-digest audit on the card: no record is loaded by a hand-pinned digest the port could resolve; load-module-by-SHA is absent
