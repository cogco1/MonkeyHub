# M079 — Trimmed CAD bounds readback parity

- Origin: Modify
- Status: Done
- Depends on: M078

## Goal

Make independent 3DM geometry readback compare the visible trimmed result of
the exact CAD translation, rather than the untrimmed NURBS control-surface
envelope returned by the current headless inspector.

## Acceptance

- Brep readback derives bounds from retained trimmed/render geometry or another
  independently reproducible tight witness; it cannot silently use the larger
  untrimmed control-surface envelope.
- CAD execution fails when the trimmed result differs from the analytic program
  denominator, while valid extrusion, revolve, loft, and boolean results pass
  within the project-supplied tolerance.
- Missing or stale mesh/bounds witnesses remain fail-closed and never gain
  design, stage-acceptance, promotion, persistence, or canonical-write authority.
- A real Rhino smoke includes curved and lofted geometry and finishes with
  independent readback plus exact-host cleanup.

## Stop conditions

- Stop before widening tolerance to hide a translation/readback mismatch.
- Stop before trusting Rhino-authored document user text as independent geometry
  verification.
- Stop if the solution would accept an unbounded conservative envelope as an
  exact visible-geometry witness.

## Tests

- CAD program, CAD execution, and 3DM inspector target suites.
- Architecture check and compileall.


## Completion

- Completed: 2026-08-30
- Evidence: 54 target tests passed; repository verification PASS; real Rhino curved+loft+instance smoke succeeded with independent explicit trimmed-mesh readback, cleanup confirmed, and no tolerance widening.
