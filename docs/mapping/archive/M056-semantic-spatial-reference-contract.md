# M056 — Semantic-spatial reference contract

- Origin: Maintenance
- Status: Done
- Depends on: P054, P056, P059

## Goal

Publish the exact current-project reference sets that a semantic-spatial model
may use as proposal evidence, responses, nested sources, and expert advice, then
validate against the same compiled contract without allowing self-authorized
references.

## Write scope

- `archflow/capabilities/spatial.py`
- `archflow/capabilities/semantic_spatial_authoring.py`
- `tests/test_spatial_proposals.py`
- `tests/test_semantic_spatial_authoring.py`
- `tests/integration/test_multi_building_experiments.py`
- `probes/p062-clinic-case/`
- `probes/p062-experiment-study/`
- `docs/EXPERIMENT_PROTOCOL.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- One typed, deterministic contract exposes allowed proposal evidence refs,
  allowed and required response refs, and allowed expert-advice refs from only
  the current state, phase gate, program, site, and build policy.
- The exact same compiled sets drive deterministic validation; the prompt and
  validator cannot drift into separate reference rules.
- Nested source refs remain a subset of proposal evidence, and the model cannot
  make a reference valid merely by listing it as expert advice.
- No project reference is stored in framework defaults; concrete lists exist
  only inside the project invocation and retained provider receipt.
- `study-008` remains an immutable failed result and any successor uses a new
  source and contract identity.

## Tests

- Reference-contract determinism and prompt publication.
- Allowed, unknown, required-response, nested-source, and self-authorized
  expert-advice negatives.
- P062 historical reload, architecture firewall, V3 boundary, scope, and diff.

## Stop conditions

- Stop if a project-specific ref is hardcoded in `archflow/`.
- Stop if prompt lists and validation sets are computed independently.
- Stop if an unknown ref is normalized, repaired, or accepted.

## Current evidence — 2026-08-18

- `study-008` proves the recursive JSON contract is effective: parsing reached
  deterministic context validation. Its exact retained failure is now
  `spatial_authoring.source_rejected` because the model copied program-node and
  relationship refs into proposal evidence even though only provenance refs
  in current state/program/site/policy evidence are admissible.
- `SpatialAuthoringReferenceContract@1` compiles allowed evidence, allowed and
  required responses, and explicit expert-advice refs once. The same object is
  placed in the provider prompt and consumed by deterministic validation.
- Expert advice can no longer authorize itself by appearing in both
  `expert_advice_refs` and `evidence_refs`; unknown-reference policy remains
  reject and no normalization authority was introduced.
- Twenty-six focused proposal, provider-boundary, and promoted-study tests pass.


## Completion

- Completed: 2026-08-18
- Evidence: Shared SpatialAuthoringReferenceContract compiled once for prompt and validator; self-authorized expert refs rejected; 26 focused tests and archcheck passed; study-008 retained unchanged
