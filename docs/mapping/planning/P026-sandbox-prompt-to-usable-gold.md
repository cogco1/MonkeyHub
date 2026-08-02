# P026 — Sandbox prompt-to-usable Gold slice

- Origin: Planning
- Status: Active after M002, M004, M024, M026, M027, P018, P024, P025, P029, P030, P047, and P048
- Depends on: M002, M004, M024, M026, M027, P018, P024, P025, P029, P030, P047, P048

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
- Every model-authored semantic component becomes a candidate value. Each
  hosted component has one dedicated semantic binding to one typed assembly,
  and separate components do not reuse member-object identities.
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

## Current evidence (2026-08-02)

- Scripted concept/revision runs prove reloadable semantic-component candidate
  values, dedicated hosted-assembly bindings, distinct member identities, and
  refusal persistence. The full deterministic suite passes 384 tests with two
  explicit external skips.
- Real Codex Agent CLI run `gold-agent-cli-002` was rejected during concept
  validation before invocation receipts were persisted; it did not change
  canonical state.
- Real Codex Agent CLI run `gold-agent-cli-003` persisted the successful model
  receipt, including one door, two windows, and one hosted storage component,
  then rejected it because the root `rationale` field was absent. It did not
  enter geometry production or change canonical state.
- The exact concept-output contract and pre-validation receipt persistence were
  added after those failures and pass deterministic tests, but remain unproven
  against a fresh live provider invocation. P026 is therefore not Gold.
- Real Codex Agent CLI run `gold-agent-cli-004` passed the exact concept
  contract and persisted six semantic components, then exhausted two geometry
  rounds: the first on order-only semantic-binding ids and the second on a
  substantive object-response/input contradiction. It did not change
  canonical state. M027 owns the narrow protocol repair; no third geometry
  call was made.

## Stop conditions

- Stop if any test fixture supplies a live building answer.
- Stop if exact geometry-program/realization binding or exact-base cannot be
  proven.
- Stop before treating a platform export as acceptance evidence.
