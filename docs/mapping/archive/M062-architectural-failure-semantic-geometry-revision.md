# M062 — Architectural failure semantic–geometry revision

- Origin: Maintenance
- Status: Done
- Depends on: P060, P061, M061

## Goal

Feed exact failed mandatory P060 findings into one bounded model-authored
semantic-and-geometry successor, compile it against the exact predecessor, and
retain the P053/P036 evidence without framework patching or a second ownership
tree.

## Write scope

- `archflow/capabilities/semantic_spatial_authoring.py`
- `archflow/capabilities/geometry_proposal.py`
- `archflow/runtime/architectural_revision.py`
- `archflow/runtime/__init__.py`
- `tests/test_semantic_spatial_authoring.py`
- `tests/test_geometry_proposal_producer.py`
- `tests/test_architectural_revision.py`
- `tests/integration/test_multi_building_experiments.py`
- `probes/p062-clinic-case/`
- `probes/p062-experiment-study/`
- `docs/EXPERIMENT_PROTOCOL.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A failed mandatory P060 finding compiles to typed, exact-receipt-bound
  semantic revision context, geometry repair issues, and realization
  requirements; passed or stale receipts cannot request revision.
- The provider authors one complete successor SpatialOptionProposal. Stable
  component identities and revisions remain model output; framework code does
  not insert an entrance, rename a component, or patch geometry.
- The successor is revalidated against the unchanged project program/site
  context, revised through the existing portfolio lineage, and compiled with
  P055 against the exact predecessor component tree and geometry program.
- Geometry authoring receives the exact predecessor program and P060-derived
  requirements. P053 envelopes and P036 records survive both success and
  deterministic rejection without fallback.
- If a typed geometry edit reaches deterministic compilation but is rejected,
  the next bounded model round receives that exact rejected output, its digest,
  and the exact issues. It must return one complete replacement over the
  original predecessor; framework code never merges or patches model rounds.
- A new sandbox realization must be evaluated again by P060; only a passed
  architectural receipt can satisfy the experiment terminal role.

## Tests

- P060 feedback identity, authority, stale/pass rejection, and generic mapping.
- Complete semantic successor validation and no framework field patching.
- Exact compiled-geometry reload and predecessor publication.
- P055 atomic lifecycle, P053 evidence, P036 resume, and promoted-study reload.
- Architecture firewall, V3 boundary, scope, diff, and compileall.

## Stop conditions

- Stop if framework code authors the missing entrance or any project answer.
- Stop if geometry changes without a model-authored component transition.
- Stop if the failed P060 receipt is upgraded to pass or reused on another
  design/program/scene digest.
- Stop if revision bypasses P053, P055, P036, or the subsequent P060 gate.

## Retained result boundary

Studies 016–023 executed the architectural-revision path against the frozen
clinic predecessor with the real Agent CLI provider. The framework now
requires every model-changed component to have geometry, publishes exact
predecessor revision tokens, revalidates only byte-for-byte unchanged
descendants, and carries the selected spatial proposal only once in the model
request.

Studies 021 and 022 proved that a typed geometry edit can reach production and
sandbox realization, but both exact P060 evaluations passed only three of four
criteria and failed `usable-main-entry` with zero realized opening objects.
Study 022 is the decisive diagnostic: its first geometry round authored a
typed door assembly and host cut, but deterministic compilation rejected
missing predecessor revision tokens. The following model round received only
the issue list, fixed the tokens, and discarded the door assembly. The accepted
edit therefore remained architecturally unusable.

`GeometryProposalRepairContext@1` now carries the exact rejected complete model
output, output/proposal digests, and exact issues into the next round while
granting no patch, validation, or canonical-write authority. Focused tests
prove this for malformed output and for a parsed typed edit rejected by the
compiler. Study 023 preregistered that mechanism, but its first semantic call
timed out at the configured 300-second provider boundary (338,561 ms including
post-timeout cleanup), so the new geometry repair context was not exercised.
Study 024 contains only a registration-refusal record: the provider contract
rejects a requested 600-second timeout, and changing that completed P059
boundary is outside M062 scope.

Study 025 then resumed the exact rejected study-022 geometry round from its
P036 record without replaying semantic generation. One real Agent CLI call
(`gpt-5.6-sol`, 181,125 ms) received
`GeometryProposalRepairContext@1`, including the rejected output digest, its
ten compiler issues, and the immutable rejected-round URI. The model returned
one complete replacement over the original predecessor, retained the typed
door assembly, added the required revision acknowledgements, and passed
deterministic compilation. A fresh sandbox realization contains all thirteen
operations and one opening object. This is positive evidence for persisted
model-authored geometry repair and sandbox realization only: study 025 did not
run P055 lifecycle promotion or the subsequent P060 usability gate, so it
claims neither a usable building nor a family terminal.

Study 026 closes those two deterministic boundaries without another provider
call. It compiles the study-025 replacement with P055 against study 022's exact
predecessor semantic tree and geometry program, persists the compiled lifecycle
and original P053 envelope under P036 checkpoint
`production-checkpoint-000003`, and freshly realizes the checkpointed geometry.
Measurements are recomputed from the current DesignProgram, current proposal,
semantic bindings, realized bounds, and `HybridScene.opening_object_ids`; no
prior verdict is copied. All four mandatory project-derived criteria pass:
program coverage `1.0`, relationship coverage `1.0`, public/service separation
`true`, and usable entry count `1`. The retained outcome therefore satisfies
M062's P055-plus-P060 terminal. It does not claim a P061 family terminal or a
multi-building P062 result.


## Completion

- Completed: 2026-08-18
- Evidence: project://p062-experiment-study/runs/study-026/records/m062-lifecycle-p060-closure-outcome-07cf9dde95ea72a0b019bd426506dd96a594d3eb71d9693f7e0b6a0a31a3f995.json
