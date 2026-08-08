# P026 — Sandbox prompt-to-usable Gold slice

- Origin: Planning
- Status: Active after M002, M004, M024, M026, M027, M028, M029, M031, M032, P018, P024, P025, P029, P030, P047, and P048
- Depends on: M002, M004, M024, M026, M027, M028, M029, M031, M032, P018, P024, P025, P029, P030, P047, P048

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
- A malformed Architect concept or revision receives at most one exact-contract
  repair round. Both receipts, the predecessor reference, and the validation
  issue persist; the framework neither fills semantic fields nor permits a
  silent provider/model substitution.
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
- Bounded semantic-authoring repair, receipt lineage, and provider-identity
  retention regressions.
- Archive, design-state, event-log, and canonical-state reload.
- Static no-instance-default authority scan.

## Current evidence (2026-08-02)

- Scripted concept/revision runs prove reloadable semantic-component candidate
  values, dedicated hosted-assembly bindings, distinct member identities, and
  refusal persistence. The full deterministic suite passes 393 tests with two
  explicit external skips.
- Real Codex Agent CLI run `gold-agent-cli-002` was rejected during concept
  validation before invocation receipts were persisted; it did not change
  canonical state.
- Real Codex Agent CLI run `gold-agent-cli-003` persisted the successful model
  receipt, including one door, two windows, and one hosted storage component,
  then rejected it because the root `rationale` field was absent. It did not
  enter geometry production or change canonical state.
- The exact concept-output contract and pre-validation receipt persistence were
  added after those failures and passed deterministic tests. At that point they
  remained unproven against a fresh live provider invocation.
- Real Codex Agent CLI run `gold-agent-cli-004` passed the exact concept
  contract and persisted six semantic components, then exhausted two geometry
  rounds: the first on order-only semantic-binding ids and the second on a
  substantive object-response/input contradiction. It did not change
  canonical state. M027 owns the narrow protocol repair; no third geometry
  call was made.
- Real Codex Agent CLI run `gold-agent-cli-005` also passed the exact concept
  contract and persisted one entrance door, two daylight windows, and one
  integrated hosted storage component. Geometry round one authored a window
  with glazing only and was rejected for missing the required clearance,
  frame, hardware, and host-cut roles. Round two received that repair issue and
  authored a door with all five required unique roles, but listed the member
  records in a non-canonical role order; typed construction rejected it before
  compilation. The lineage is exhausted, provider identity is retained,
  authority flags remain false, and canonical HEAD remains version zero.
- P026 was blocked after repeated bounded real-provider geometry exhaustion.
  That block required an explicit choice among normalizing keyed object
  collections, strengthening provider guidance further, or authorizing a
  different bounded repair policy. No gate was weakened.
- Kevin explicitly resumed the blocked objective and authorized M028 to repair
  the hidden kind-specific assembly-role contract and non-semantic keyed-object
  ordering before one further bounded live proof.
- M028 now exposes kind-specific required assembly roles from the same typed
  state source used by validation and canonicalizes keyed provider collections
  without deduplicating or weakening rejection rules. Its formal verification
  passed before the next live run.
- Real Codex Agent CLI run `gold-agent-cli-006` retained the expected provider,
  model, version, and fingerprint, but its otherwise coherent five-component
  concept omitted `semantic_kind` from every component. Exact validation
  rejected it before geometry production; the invocation receipt persisted and
  canonical HEAD remains version zero.
- P026 now has a deterministic, two-round maximum Architect authoring protocol:
  a malformed concept or revision may receive one exact-schema repair request
  containing the untouched prior output and precise validation issue. Both
  receipts link and persist, and a provider/model identity change is rejected.
  This passes deterministic tests; run `gold-agent-cli-007` exercised the fresh
  concept path successfully without needing that repair.
- Real Codex Agent CLI run `gold-agent-cli-007` passed the exact concept
  contract in one call. Geometry round one was rejected for three host cuts
  that did not depend on their named hosts and an unavailable predecessor;
  round two repaired all four substantive findings, then was rejected because
  one numeric-vector `value_json` string contained non-canonical whitespace.
  Provider identity was retained and canonical HEAD remained version zero.
- M029 implements semantics-preserving parameter-JSON normalization. M030
  removed repeated AST walks while retaining the original three-second
  wall-clock architecture gate and every finding. Both cards passed formal
  focused, architecture, and full-suite verification and are archived.
- Offline replay of the exact round-two output now crosses the parameter-JSON
  failure and exposes a separate contract defect: assembly `interface_refs`
  use identifier-shaped values where typed state requires a portable logical
  reference. P026 is therefore still not Gold; no further live call was made.
- M031 selectively integrated Claude commits `942ae1d`, `b564a21`, and
  `0e31622` under a sandbox-only write scope. Floating occupancy now derives
  from persisted support relations, approvals use the durable policy issuance
  gate, and reload recomputes rejection semantics consistently. Focused,
  architecture, and full-suite verification passed; Claude's repository,
  design-state, geometry-contract, and CPU-timing commits remain unmerged.

## Stop conditions

- Stop if any test fixture supplies a live building answer.
- Stop if exact geometry-program/realization binding or exact-base cannot be
  proven.
- Stop before treating a platform export as acceptance evidence.
