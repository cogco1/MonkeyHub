# P027 — Uneven-terrain adaptation slice

- Origin: Planning
- Status: Done
- Depends on: P026

## Goal

Extend the accepted sandbox slice to uneven site geometry without encoding a
fixed grading, relocation, pier, or foundation answer in the framework.
Platform deployment remains downstream.

## Acceptance

- Site observations enter `D_v,k` as exact-base evidence and obligations.
- Site/foundation experts present bounded alternatives; the Architect chooses.
- Grading, relocation, foundation, or unresolved outcomes remain explicit
  building-run decisions.
- Terrain competence is not inferred from renderer or platform execution
  success.
- A rejected environmental proposal and accepted adaptation both reload.

## Write scope

- `archflow/runtime/`
- `archflow/capabilities/`
- `tests/integration/`
- `probes/p027-terrain-adaptation/`
- `docs/mapping/`

## Tests

- Uneven-site offline alternatives.
- Neutral terrain-geometry adaptation and optional downstream smoke.
- Repeated-driver-defect and unchanged-plan stop receipts.
- Sandbox and canonical preservation on rejected adaptation.

## Stop conditions

- Stop before modifying any valued external model or world.
- Stop if an optional platform adapter cannot prove observation order around
  external writes.


## Completion

- Completed: 2026-08-10
- Evidence: Typed exact-base terrain alternatives, explicit Architect selection, contact validation, and unchanged-plan stop receipts are implemented in archflow/runtime/terrain_adaptation.py and archflow/capabilities/terrain.py.
- Evidence: probes/p027-terrain-adaptation reloads observed uneven terrain, rejected grading and relocation, selected elevated support, one semantic-geometry tree, an exact neutral scene, eight passing contacts, and one failed unchanged plan.
- Evidence: python tools/devctl.py verify P027 passed ARCHITECTURE PASS for 104 files and 461 unittest cases with 3 skips.
