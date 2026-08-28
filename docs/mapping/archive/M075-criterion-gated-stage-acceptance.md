# M075 — Criterion-gated stage acceptance

- Origin: Modify (live finding: the P074 symmetry criterion existed but
  was not on the acceptance path — a failing realization could still be
  archived ACCEPTED, so the FAIL only ever appeared as retrospective
  measurement)
- Status: Ready
- Depends on: P074

## Defect

Stage acceptance validated only artifact presence. The axial-symmetry
criterion ran as a standalone tool after the fact, so a model that
fails the criterion could still come into existence as an ACCEPTED
record. Under the rules, a failing realization must never pass: it may
be built in the workspace (measurement requires realization), but the
stage gate must refuse to accept it.

## Design

- The stage acceptance point measures the realized scene against the
  project's axial-symmetry criterion before any archive disposition is
  written. The gate record (findings, axis commitment provenance,
  tolerance) is persisted either way.
- On failure the stage writes a REJECTED sandbox archive citing the
  gate record and raises a typed stage-gate error — no ACCEPTED record
  exists for a failing realization, and the failure feeds the repair
  loop instead of the archive.
- On pass the ACCEPTED archive cites the gate record among its
  evidence, so acceptance provenance includes the criterion that
  guarded it.
- Build-to-measure is explicitly distinguished from pass-the-gate: the
  workspace realization is measurement substrate, never acceptance.

## Stop conditions

- Stop if the framework would own the axis value, subjects, or
  tolerance — they stay project-supplied at the gate site.
- Stop if a failing stage could still produce an ACCEPTED archive.

## Tests

Fail-closed: an off-axis scene at the gate yields REJECTED plus a typed
error and no ACCEPTED record; pass path cites the gate record;
architecture scope and discovery.


## Completion

- Completed: 2026-08-28
- Evidence: Symmetry criterion moved onto the acceptance path in _persist_stage: realized scene measured against project-supplied axis/subjects/tolerance BEFORE any archive disposition; gate record (AxialSymmetryGate@1) persisted on both outcomes. Fail-closed drill proven fast: off-axis stage-0 scene -> REJECTED archive citing the gate record + typed StageGateError, no ACCEPTED record exists (exact 0.5 m offset in the gate record); symmetric scene -> ACCEPTED archive citing the gate ref among evidence. tests/test_symmetry.py grew to 10 green; full fast suite 588 green; archcheck PASS. Prior probes keep their standalone measure records; the gate binds every future derivation.
