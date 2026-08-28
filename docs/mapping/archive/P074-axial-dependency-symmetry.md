# P074 — Axial dependency and symmetry self-check

- Origin: Planning (live defect finding: asymmetric front elevation in
  the monument derivation; colonnade layout literals unbound to the axis)
- Status: Ready
- Depends on: P065, P072

## Defect

The monument program's colonnade rows are authored as free literals
(seed origin, count, step) with empty `responds_to`: the column rows sit
0.25 m off the committed axis and the beam row 0.90 m off, overhanging
the portico roof on one side. No criterion measures symmetry, so the
defect passes every gate. This is the missing decision-dependency edge:
a dependent layout must be derived from the axis it depends on, and the
self-check must be compiled from that same basis.

## Design

- The primary axis becomes a named commitment; the axial front
  components (portico, colonnade, entry) bind to it, so revising the
  axis reopens exactly the dependent geometry through the existing
  binding-response closure.
- The colonnade row origins are derived center-out from the axis
  (`origin = axis − ((count−1)·step + width)/2`), never free literals.
- A generic axial-symmetry measurement over realized scene bounds
  (`archflow/evaluation/symmetry.py`) reports each named group's center
  offset from the axis plane; subjects, axis value, and tolerance come
  from the project, never the framework.
- Paired evidence: the measurement runs over the retained asymmetric
  stage scenes (typed FAIL findings persisted beside them) and over the
  re-derived symmetric scenes (zero offsets), with the CAD realization
  rebuilt from the corrected program.

## Stop conditions

- Stop if the framework would own an axis value or a symmetry subject.
- Stop if the old records would be rewritten instead of measured.

## Tests

Measurement math (zero for symmetric groups, exact offsets for
asymmetric ones, typed empty-group failure); derived-origin centering;
architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: Defect quantified on retained scenes and persisted as typed FAIL findings beside them (colonnade +0.65 m, rotunda plinth +0.50 m, beams +0.90 m off the committed axis; three stages). Fix: commitment:primary-axis-center added as a typed HARD commitment the runtime enforces (required-commitment-absent rejection observed live); all bindings answer it; colonnade/cap/beam/plinth origins derived center-out from the axis. Re-derivation p074-monument-symmetric/monument-002: 4 stages, 308 instances, axial_group_offsets exactly 0.000 for all five groups at stages 1-3 (PASS records retained). CAD rebuild EQUIVALENT (max dev 0.346 m) with semantics verified; IFC re-export VERIFIED; symmetric front elevation captured. tests/test_symmetry.py (8) green; archcheck PASS.
