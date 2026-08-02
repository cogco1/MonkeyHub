# P026 — Sandbox prompt-to-usable Gold slice

- Origin: Planning
- Status: Ready after M002, M004, M024, P018, P024, P025, P029, P030, P047, and P048
- Depends on: M002, M004, M024, P018, P024, P025, P029, P030, P047, P048

## Goal

Prove one true raw-request-to-usable-building run as a reloadable accepted
platform-neutral sandbox artifact. Minecraft, Revit, Rhino, `.schem`, and other
platform outputs remain downstream adapters and are not proof authorities.

## Acceptance

- Input describes use and qualitative scale but does not provide geometry
  operations, a footprint, room list, topology, palette, or platform script.
- Building-scoped retrieval/derivation produces functions, capacities, areas,
  relations, topology, and dimensions with receipts.
- The Architect drives state-responsive expert selection and design transitions.
- Architect semantics compile into a typed neutral geometry program whose
  generic kernel knows operations, units, tolerances, references, and failures,
  but no building-type answers.
- Parametric geometry, typed door/window assemblies, and provenance-bound
  detail assets coexist in one hybrid building artifact.
- A deterministic sandbox realization supplies spatial validation views and
  paper-ready drawings without requiring an external platform.
- At least one rejected/revised candidate and one accepted candidate reload.
- Realization exactness, P005/P030 hard gates, commitment monitor, P007
  read-only aesthetics, player approval, and single-writer commit all remain
  distinct.
- The accepted artifact, `C_v`, `D_v,k` chain, and evidence trace reload after
  restart.

## Write scope

- `archflow/runtime/`
- `tests/integration/`
- `probes/p026-sandbox-gold/`
- `docs/mapping/`

## Tests

- Deterministic neutral-geometry and sandbox realization Gold.
- Assembly, asset, geometry-operation, and fixture regressions.
- Archive, design-state, event-log, and canonical-state reload.
- Static no-instance-default authority scan.

## Stop conditions

- Stop if any test fixture supplies a live building answer.
- Stop if exact geometry-program/realization binding or exact-base cannot be
  proven.
- Stop before treating a platform export as acceptance evidence.
