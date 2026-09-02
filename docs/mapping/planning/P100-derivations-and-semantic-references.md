# P100 — Derivation table and semantic references (construction by relation)

- Origin: Planning (from the 2026-09-02 architecture review and semantic reference audit)
- Status: Active (Claude, 2026-09-02); registry entry deferred until codex's stage-envelope edits land
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
