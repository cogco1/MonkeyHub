# P100 — Derivation table and semantic references (construction by relation)

- Origin: Planning (from the 2026-09-02 architecture review and semantic reference audit)
- Status: Done (Claude, 2026-09-02); every acceptance item met, evidence in the Completion block
- Depends on: P089 (first cut), P092, P098
- Retires: the villa monolith's 29 dimension constants and inline coordinate arithmetic (as data, in migration steps); `make_packs.py`-style coordinate computation in project pack generators (replaced by references lowered through one resolver); per-side `rotated(profile, angle)` placement

## Goal

Two records and two small pieces of code so that a building's numbers have
names and bases, and its elements are placed by reference rather than by
coordinate:

- `DerivationTable@1`: named quantities as expressions over evidence
  readings and other quantities, each with basis refs and an epistemic
  status; evaluated deterministically by a safe evaluator (no `eval`),
  cycles and unknown names fail typed; the evaluated table is a record
  whose values are DERIVED facts with their inputs listed, so invalidation
  can flow through them.
- Semantic references on elements: `LevelRef`, `OffsetFrom(ref, d)`,
  `GridRef(axis)`, `GridIntersection(a, b)`, `AxisPoint(axis, along)`,
  `HostAlong(host, along)`; one canonical resolver turns plan references
  into plan coordinates from the project grids; elevation references stay
  symbolic (`base_level` / `base_offset`) for the compiler (P090/M096).
- Until the runner consumes references natively (codex is binding the
  runner to stage envelopes), a lowering step produces the current
  `ElementPack@1` from a reference pack; the lowered pack must be
  coordinate-equal to the hand-computed pack (equivalence receipt). The
  lowering adapter is itself scheduled for retirement when the runner
  reads references.

## Acceptance

- Evaluator: arithmetic, names, `min/max/abs/round/sqrt`, unary minus;
  unknown name, cycle, division by zero, non-finite result fail typed;
  evaluation order is topological and deterministic; digest stable.
- Villa west side: the 29 constants become a derivation table whose
  evaluated values equal the script's constants; the west band and portico
  element pack written with references lowers to the coordinates the
  current pack carries (max deviation 0).
- Rocca: the plate readings → derivation table → reference pack lowers to
  the pack `make_packs.py` computed (max deviation 0).
- No coordinate literal for a level or a grid position remains in a
  reference pack; every `@name` resolves to the derivation table.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/state/derivation.py` (new)
- `archflow/capabilities/reference_resolver.py` (new)
- `tests/`
- `docs/mapping/`
- project pack generators in the runtime workspace

## Stop conditions

- Stop before the evaluator gains control flow, string operations or
  access to anything but the table and readings.
- Stop before a reference is resolved into a stored coordinate that the
  element record keeps (a lowered pack is an adapter output, never the
  authoritative record).
- Stop before elevation references are resolved outside the compiler.

## Evidence (2026-09-02, villa reference-001)

`V4_RUNTIME/.../villa-rotonda-reconstruction/runs/reference-001/workspaces/authoring/run_portico_by_reference.py`
→ report record `project://villa-rotonda-reconstruction/runs/reference-001/records/portico-by-reference-report-7d6c705bc857db4ebf7e88ba49e4788dca9344a5af01106df1757ccc01b53cf1.json`.

- 13 readings taken from run-016's saved model (column diameter 1.08, height 6.426, abacus 1.12 × 0.16 above the
  column with 0.02 engagement, entablature 0.92 deep / 11.43 long with 0.16 engagement over the abacus, tympanum
  11.056 × 1.765 × 0.42, facade axis x = −14.374, tympanum face x = −15.094); 5 derived quantities
  (radius, abacus half, overhang (11.43 − 8.032)/2 = 1.699, tympanum start/end).
- Rows place columns at A–F × W, abaci on `columns-west-top`, the entablature from A×W to F×W on
  `capitals-west-top` with its top bound to `level-main-cornice`, the tympanum on `entablature-west-top` along Wf.
- The only `base_offset` parameters in the program are the two declared engagements (0.02, 0.16).
- Rhino readback vs run-016 named bounds: all 14 objects within **0.25 mm** (worst 0.00025 m); analytic vs
  Rhino 0.0. The first run landed 20 mm high on abaci and entablature because the abacus reading took the total
  extent (0.18) as its height above the column; the convention slip was visible in one number and fixed in the
  reading, not with an offset.

## Retirement ledger (2026-09-02 night)

- **Retired: authored coordinates in the villa runner packs.** `run-017 authoring/make_runner_packs.py` now
  authors `element-references.json` (wall on the exterior-face grid line WF between the body-edge lines SE/NE,
  openings `along` their host, sills/heads as offsets from the wall's base level; the three lines are run-016
  readings published as grid axes). `element-pack.json` is *derived* by `lower_element_references` and the
  receipt `element-pack.lowering.json` proves it equals the pack it replaced: 71 numbers, max delta 1.8e-15.
  The runner keeps consuming the derived pack until it reads references itself (codex's `project_runner.py`).
- **Retired: inline arithmetic and authored coordinates in the Rocca runner packs.** `rocca-pisana/make_packs.py`
  now holds one `DerivationTable@1` (37 named quantities, each `round((piedi expr) * piede, 6)` so the old `ft()`
  rounding is reproduced) over 17 plate/elevation readings; walls run between the face lines S/N/W/E and the
  loggia jamb lines LW/LE, the colonnade sits on the facade line, prisms/ring/dome take dimensions by name; the
  only shape functions left are the octagon and the square's eight-point outline, fed by table values.
  `element-pack.json` is derived and proven equal to the pack it replaced: 154 numbers, max delta 0.0
  (`element-pack.lowering.json`, `derivations.json`).
- **Retired: the resolver's own grid scan** — `ReferenceContext.axis` asks `ProjectGrids.axis` and only adds the
  id lookup.
- **Retired: the record's free-string relation vocabulary** — `StateRecord.Relation.kind` must be a kernel
  `ArchitecturalRelationKind` value (P102).
- **Retired: the lowering adapter and every coordinate pack.** The runner reads references directly through the
  canonical producers (`element_producers` now also carries prism, ring, loft, dome cap, declined, wall types and
  exclusions); `lower_element_references` and `element-pack.json` are gone. Villa and Rocca generators emit one
  `state-record.json`; the runner-002 programs are reproduced object by object (villa 51, Rocca 27, worst 0.0 m):
  `project://villa-rotonda-reconstruction/runs/equivalence-001/records/state-record-equivalence-6bcb52152c6766ba969b1f44c986066b39ff7a1ee9e5fbfa4715c124108f68de.json`, `project://rocca-pisana/runs/equivalence-002/records/state-record-equivalence-1cb2d33473614addcdd80254eb6ff8aa0ed8dbf1d1f12ebbc2db38683e6a5eda.json`.
- **Retired: inline arithmetic in Rocca** — one `DerivationTable@1` of 37 quantities over 17 readings, recorded as
  record parameters with expr/inputs; `--override reading=value` is a declared what-if, never a silent edit.
- **Done (was pending on codex's runner):** `project_runner.PRODUCERS` beside `element_producers.PRODUCERS`; the
  runner's full-rebuild-only `_export` beside the patch path — both retired on 2026-09-02 night (the runner reads the
  record, exports by digest, patches or restamps on a prior export). Still open: `DevelopedDesignState` as the
  *compiler's* input; the production entry and the runner accept `StateRecord@1` and forward it once.


## Completion

- Completed: 2026-09-02
- Evidence: DerivationTable@1 + safe evaluator + semantic reference resolver; tests.test_derivations_and_references 9, tests.test_element_producers 4
- Evidence: Villa reference-001: 13 readings from run-016, 14 objects read back within 0.25 mm; only base offsets in the program are the two declared engagements
- Evidence: Villa and Rocca runner inputs authored as references/derivations; the lowering adapter and every coordinate pack retired once the runner read references
