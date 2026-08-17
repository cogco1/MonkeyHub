# P054 — Typed semantic-spatial authoring

- Origin: Planning
- Status: Ready
- Depends on: M049, P029, P051

## Goal

Add a provider-neutral authoring capability that turns bounded project state
and Architect output into one validated `SpatialOptionProposal`: its
`DesignComponent` tree and stage-appropriate coarse massing geometry are born
together, with every volume owned by exactly one component from the first
generated state.

## Write scope

- `archflow/adapters/model_provider.py`
- `archflow/capabilities/`
- `archflow/runtime/`
- `archflow/state/`
- `tests/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A typed producer accepts only a bounded authoring request and a provider
  interface; it does not receive a filesystem writer or choose a project path.
- The result contains one rooted component tree, coarse volumes, topology,
  intent, maturity, unresolved child roles, and source references in the
  existing P051 schema rather than a parallel semantic schema.
- Every generated volume has exactly one component owner at validation time;
  a semantic-only label or unowned coarse volume fails closed.
- Provider responses are parsed through a deterministic schema boundary with
  named malformed, stale-base, ownership, and topology failures.
- Neutral scripted providers make the authoring contract testable while the
  Agent CLI and later API providers remain replaceable implementations.

## Tests

- Coarse dome and portico authored with stable component-volume ownership.
- Malformed provider output, missing parent, cycle, duplicate owner, unowned
  volume, unknown source, and stale-base rejection.
- Provider-neutral deterministic replay and architecture firewall.

## Stop conditions

- Stop if the producer writes project state directly.
- Stop if it introduces a second building-composition tree or embeds Pantheon
  facts in framework code.


## Completion

- Completed: 2026-08-16
- Evidence: Implemented a bounded SemanticSpatialAuthoringPrompt and provider-neutral AsyncModelProvider turn that returns the existing SpatialOptionProposal@2; DesignComponent semantics and coarse MassingVolume geometry are authored in one object with no writer or path capability.
- Evidence: Deterministic validation now rejects stale state/context, mismatched provider receipts, malformed envelopes, missing or cyclic parents, duplicate or absent volume ownership, and unknown sources with named receipts; 6 focused tests, 479 full tests with 3 explicit skips, compileall, the 110-file architecture firewall, and the 110-production-file V3 boundary passed.
