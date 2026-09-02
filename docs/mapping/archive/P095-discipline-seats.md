# P095 — Discipline seats: handover, projection, write scope, schedule

- Origin: Planning
- Status: Ready after M090
- Depends on: P090, P091, P093, M090

## Goal

Make the abstract's "each discipline agent reads only its branch, the
inherited global principles, and the interfaces other disciplines have
compiled" executable, modelled on how design offices actually divide
labour (stage x discipline, 提资 handover rounds, checkers separate from
authors). One provider protocol, several seats: a `SeatSpec` names the
component subtrees a seat may write, the phases and quadrants it may
act in, and the seats whose handovers it consumes. A deterministic
handover compiler turns another seat's committed state into constraints
(published interface datums, exclusion bounds, open obligations), never
into raw context. A seat context projection keeps only the owned subtree
plus ancestors, inherited principles, and handovers. The producer refuses,
typed, any semantic binding outside the seat's scope. A scheduler orders
seats within a phase by handover dependency into parallel rounds.
Reviewer seats stay authority-free and write nothing.

## Acceptance

- A handover compiled twice from the same committed state is
  byte-identical; it carries datums, exclusion bounds, and obligations
  with basis references and no authority flags.
- A seat context contains exactly the owned subtree and its ancestors,
  the inherited principles, and the consumed handovers; nothing from a
  sibling subtree leaks through.
- The producer refuses a proposal that binds a component outside the
  seat's owned subtree with a typed issue; an in-scope proposal that
  binds a handover datum compiles with the derived value.
- The schedule is a deterministic topological order; independent seats
  share a round; a cycle fails typed.
- A scripted three-seat demonstration (structure -> envelope -> detail)
  runs through the real producer: the structure seat publishes a
  bearing level, the envelope seat derives from it, the detail seat is
  refused when it reaches into the envelope subtree.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/capabilities/discipline_seats.py`
- `archflow/capabilities/geometry_proposal.py`
- `archflow/capabilities/__init__.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Handover determinism and content.
- Projection isolation.
- Producer scope refusal and datum derivation.
- Schedule order and cycle refusal.
- Three-seat scripted demonstration.
- Architecture firewall.

## Stop conditions

- Stop before any seat default names a building type, a discipline's
  building answer, or a typology constant inside the kernel.
- Stop before a reviewer seat gains write or acceptance authority.
- Stop before a handover carries raw provider text instead of compiled
  constraints.


## Completion

- Completed: 2026-09-01
- Evidence: SeatSpec@1 (owned subtrees, disciplines, phases, quadrants, consumed handovers, reviewer flag), owned_subtree/ancestors over the component tree, compile_handover -> SeatHandover@1 (published datums by owned components/objects, exclusion bounds for owned realized objects, OPEN obligations of the receiving disciplines; byte-identical on repeat; no authority flags), project_seat_context -> SeatAuthoringContext@1 (owned subtree + ancestors + inherited commitment refs + consumed handovers; wrong phase, foreign or stale handover fail typed), schedule_seats (topological parallel rounds, reviewers last, cycles and unknown seats fail typed). Producer gains seat_scope and refuses out-of-scope semantic bindings with the typed issue seat_scope_violation. Three-seat demonstration through the real producer: structure seat publishes bearing-level on its own object, handover carries exactly that datum plus its exclusion bounds, envelope seat derives sill_level=3.57 from it without restating, detail seat is refused when binding the envelope subtree. 11 tests; ARCHITECTURE PASS (295 files); full suite 1678 with two pre-existing Windows flakes (viewer HTTP socket abort, context-recovery hook against an uncommitted worktree) that pass in isolation.

## Live evidence (recorded 2026-09-01, after completion)

Villa Rotonda run-017, west portico, three seats through the real
producer and the Rhino COM export (records under
`runs/reconstruction-017/records/`: discipline-seat x4, seat-schedule,
seat-handover x2, seat-authoring-context x2, seat-geometry-program x3,
seat-rhino-execution x3, seat-3dm-inspection x3,
west-portico-seat-run-receipt 9b43b49d…, west-portico-seat-run-verification
cf33f22e…). Reference model: run-016 canonical (user ruling).

- Structure -> {envelope, detail} in the scheduler's rounds; the detail
  seat's overreach into `exterior-walls` refused on the record with
  `seat_scope_violation`; each handover carried 8 datums and 27
  constraints; sibling subtrees absent from the projected contexts.
- 49 objects realized, readback verified at 3 mm; 20/20 level and
  contact checks within 1 mm (RhinoCommon accurate boxes): every
  cross-seat interface derives from a datum (M096 `base_level`).
- Derived stack (alternative A, for the author's ruling): entablature
  top 11.335 fixed as the main-block interface; soffit 9.980; abacus
  9.798–9.980; shaft 3.570–9.798 (198 mm shorter than run-016).
- Residuals, all family-internal or already present in run-016: the
  entablature mouldings enter the entablature block (12 pairs; the
  handover carried the structure seat's exclusion bounds but nothing
  enforces them yet — next card), entablature front/returns overlap at
  the corners (2), door/window frame corner joinery (10).
