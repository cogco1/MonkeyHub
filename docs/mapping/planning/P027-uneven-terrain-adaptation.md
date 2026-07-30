# P027 — Uneven-terrain adaptation slice

- Origin: Planning
- Status: Ready after P026
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
