# P062 — Multi-building experiments and ablations

- Origin: Planning
- Status: Done
- Depends on: P059, P060, P061

## Goal

Run a preregistered, reproducible comparison across multiple non-isomorphic
building projects and bounded ablations, using the real production provider
and current semantic–geometry mechanisms without reviving a deprecated path or
writing unrun outcomes as evidence.

## Write scope

- `archflow/evaluation/experiment.py`
- `archflow/evaluation/__init__.py`
- `archflow/evaluation/README.md`
- `tools/run_experiment.py`
- `tests/test_experiment_protocol.py`
- `tests/integration/test_multi_building_experiments.py`
- `probes/p062-clinic-case/`
- `probes/p062-workshop-case/`
- `probes/p062-courtyard-case/`
- `probes/p062-experiment-study/`
- `docs/EXPERIMENT_PROTOCOL.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A typed preregistration fixes case identity, condition, provider/model/version,
  request digest, wall-clock bound, attempt bound, metrics, stopping rules, and
  code/contract identity before any result exists. Planned, running, failed,
  and completed outcomes remain distinct and reload exactly.
- At least three non-isomorphic building briefs live in separate P036 project
  envelopes. Concrete prompts, evidence, parameters, component trees, geometry,
  and derivation traces remain project/probe data; the framework owns only the
  generic experiment and metric contracts.
- The full condition executes through P053 provider authority and the current
  P056/P054/P055/P060/P061 chain. Provider failures, timeouts, malformed output,
  and rejected buildings are retained as outcomes with no fallback or hidden
  retry.
- At least one generation-context ablation and one validation ablation are
  isolated experiment conditions. They may withhold a named input or evaluator
  but cannot replace, disable, or reintroduce an old production route.
- Conditions use comparable provider configuration and project-derived metrics
  for completion, semantic–geometry consistency, architectural usability,
  repair/retry count, and wall-clock. Missing or non-comparable measurements
  remain typed unknowns rather than favorable results.
- A P036-retained study manifest and result index bind every claim to exact run
  receipts. The paper evidence table is generated only from completed records
  and states the sample-size and generalization limits honestly.

## Tests

- Preregistration immutability, exact condition identity, no-result-before-run,
  stale receipt, duplicate attempt, timeout, failure, and no-fallback tests.
- Metric comparability, missing-value, rejected-run, and ablation-isolation
  tests using deterministic fixtures.
- Real multi-building result reload, exact provider receipts, separate P036
  project ownership, generated-table reconciliation, and no unrun-result scan.
- Architecture firewall, V3 boundary, scope, diff, compileall, and full
  discovery.

## Stop conditions

- Stop before placing a building prompt, architectural answer, parameter set,
  topology, or family instance in `archflow/`.
- Stop if an ablation mutates the production chain, restores a quarantined
  provider/generator path, bypasses P053/P036, falls back, or shares hidden
  state across cases.
- Stop if a planned or failed run is reported as a result, if conditions use
  incomparable provider settings without disclosure, or if small-sample
  evidence is described as broad statistical generalization.
- Stop before paper prose turns a deterministic contract test into a measured
  model outcome; P062 retains facts for P063 but does not write the paper.

## Current progress — 2026-08-18

- The generic preregistration, intent, receipt, outcome, result-index, and
  detached-source persistence boundaries are implemented and tested through
  P036. Completed terminal chains cross-check P056, sandbox, P060, and P061
  digests; an empty family no-op cannot count as completion.
- Three pairwise-distinct, non-synthetic P036 case projects now retain exact
  raw requests, compiled briefs/programs, neutral site records, build policies,
  production authoring contexts, evaluation briefs, and answer-free family
  selection rules. A repository test verifies that they contain no experiment
  result schemas and no preselected geometry or family identity.
- Each case now also retains a typed generation-context ablation that removes
  only `DesignProgram.relationships`, rebinds the dependent build-policy digest,
  preserves the full source context unchanged, and records that P053/P056 and
  provider configuration are not replaced.
- Exact condition-to-context selection is implemented: full and generation
  assignments resolve distinct retained P036 records, while validation
  ablations are prevented from invoking a provider.
- A separate non-synthetic P036 study project now freezes the three-by-three
  matrix, exact provider configuration, thirteen metrics, code/contract content
  identities, one-attempt rule, and four-hour study ceiling. Its derivable index
  records all nine assignments as planned, zero as comparable, and the evidence
  table as not ready.
- The preregistration itself made no run claim; the later preflight intent is
  tracked separately from live provider execution, terminal evidence, outcomes,
  and any empirical table.
- Subsequent execution preflight found that the first clinic context omitted
  P056's required current commitment. The intent and zero-provider-call
  rejection diagnostic are retained, but no model was invoked and no attempt
  outcome is claimed. Three corrected project-derived contexts now bind one
  brief/evaluation-backed hard commitment each, recompute the exact maturity
  and phase-gate digests, and retain matching generation ablations.
- `study-002` is now that successor: it passed a disposable rehearsal, freezes
  only the corrected refs, and started one clinic/full assignment through P053.
  Codex CLI process creation failed after 14 ms with Windows access denied, so
  the exact offline receipt and thirteen unknown metrics are retained without
  fallback or retry. The index is one failed/eight planned with sample size
  zero. `study-001` remains immutably superseded-before-provider; neither run
  claims a model or building result.
- Process-only diagnosis shows that Windows resolves bare `codex` to an
  unusable extensionless npm shim, while `codex.cmd --version` succeeds. No
  model call was made by that diagnosis; a corrected executable requires a new
  frozen provider profile rather than a retry inside `study-002`.
- `study-003` froze the corrected `codex.cmd` profile and reached the real CLI.
  Its sole clinic/full call hit the configured 120 s model deadline; retained
  wall-clock was 167,109 ms after Windows cleanup. The frozen binder then
  exposed a protocol defect by rejecting that valid timeout duration. The
  rejection and provider evidence are retained without an experiment outcome,
  fallback, retry, or building claim.
- The binder now treats the configured deadline and observed cleanup-inclusive
  duration as separate facts. Timeout receipts may exceed the deadline only
  within their enclosing attempt wall-clock; non-timeout receipts may not.
  `study-004` freezes the corrected code/contract and unchanged provider as a
  planned-only successor. It has not invoked a provider.
- The user authorized bounded live execution for the current Goal. `study-005`
  therefore froze the 300 s profile and invoked clinic/full once. P053 returned
  successfully, but deterministic spatial authoring rejected the candidate.
  The historical runtime then exposed a second defect: it only knew how to
  archive failed provider receipts, so it could not retain a successful P053
  envelope followed by pipeline rejection. The lost envelope is not rebuilt
  from console output and the run claims neither a building nor an empirical
  result.
- M053 repairs that recovery boundary. Successful-provider deterministic
  rejection is now a reloadable failed production attempt with no checkpoint;
  P062 may close it as `pipeline_rejected` with zero terminal evidence and
  typed-unknown metrics. `study-005` retains a diagnostic-only incident record,
  while `study-006` freezes the new code/contract identity and the unchanged
  authorized 300 s provider profile as nine planned assignments.
- `study-006` then executed clinic/full once. Its one P053 call succeeded in
  182,672 ms, but the candidate was deterministically rejected and is retained
  as `pipeline_rejected`; the result index is one failed/eight planned with
  sample size zero. Replay of the retained output identifies the exact cause:
  the model used `hard_usability_verdict=false` because the machine-facing
  contract described downstream flags collectively as false, while the parser
  requires the unevaluated verdict to be `null`. M054 publishes all five exact
  literals and preserves the original typed rejection message. The historical
  study remains failed; it is not repaired or counted as a building.
- `study-007` froze those literal and diagnostic changes, then issued one new
  clinic/full call. P053 again succeeded, this time in 177,609 ms, and the
  provider correctly returned the unevaluated verdict as `null`. The candidate
  still failed before building production because nested proposal shapes were
  absent from the model contract; the durable exact reason is `spatial grid
  basis schema drifted`. The study is one failed/eight planned, sample size
  zero, with no terminal building claim. M055 therefore publishes the complete
  recursive generic proposal structure as `SemanticSpatialAuthoringContract@2`
  without changing the parser or embedding a project answer.
- `study-008` froze the full recursive contract digest and made one new
  clinic/full call. Its P053 receipt succeeded in 187,155 ms and the proposal
  passed recursive parsing, then failed current-context validation with the
  exact durable code `spatial_authoring.source_rejected`: it cited program-node
  and relationship refs as proposal evidence, while those refs are valid
  response targets rather than provenance evidence. The run is one failed/eight
  planned, sample size zero. M056 now compiles exact allowed evidence, allowed
  and required response, and expert-advice sets once for both prompt and
  validator; no project ref is hardcoded and unknown refs remain rejected.
- `study-009` froze the shared reference contract and issued one new clinic/full
  call. Its P053 receipt succeeded in 194,890 ms; recursive parsing and exact
  reference validation both passed. Deterministic geometry consistency then
  rejected the candidate because inclusive X/Z volume projections were not a
  subset of its 48 declared footprint cells. The study is one failed/eight
  planned with sample size zero. M057 publishes the unchanged generic gate's
  inclusive bounds, cell-area, envelope, level, zoning, relationship, and
  required-response rules plus current derived facts; no clinic answer or
  post-hoc repair enters framework code.
- `study-010` froze the validation contract and crossed the semantic-spatial
  gate twice: two distinct options were accepted, then a third P053 call chose
  a valid option. All three receipts succeeded over 545,969 ms total. The
  selection output used `selected_proposal_digest` where the parser requires
  `exact_option_set_digest`, so the attempt is still honestly
  `pipeline_rejected` with no terminal building and sample size zero. M058 now
  publishes the exact four-field selection contract and stable missing/extra
  diagnostics; the accepted options and failed historical selection are not
  rewritten.
- `study-011` froze the exact selection contract. Two new semantic-spatial
  responses succeeded and again produced accepted alternatives, but the third
  P053 selection call timed out: its retained provider duration is 342,157 ms
  against a 300-second configured deadline, after 185,139 ms and 258,984 ms
  successful calls. The study is honestly `timed_out`, with eight assignments
  still planned, no terminal building, and sample size zero. M059 replaces the
  selector's full recursive option replay with an identity-bound decision
  projection; on the retained option set this removes 51.538 percent of option
  bytes without changing the authoritative options or selection output contract.
- `study-012` froze the decision projection but did not reach it. The first P053
  response succeeded in 199,500 ms and was then rejected by unchanged current-
  state validation with `spatial_authoring.source_rejected`: the proposal cited
  evidence absent from current design state. Its 199,662 ms attempt is retained
  as `pipeline_rejected`, with eight planned assignments and sample size zero.
  M060 adds one exact-feedback, complete model-authored replacement at this
  boundary. It retains both receipts and never normalizes a rejected proposal.
- `study-013` froze the single-repair policy. No repair was needed: two provider
  calls were individually accepted in 189,469 ms and 196,312 ms, but both named
  their option `clinic-linear-sequence-01`. Historical option-set construction
  then raised an untyped duplicate-id error outside the failure archive. The two
  semantic authoring records are durable, but P053 envelopes are not, so the
  run is diagnostic-only and has no reconstructed experiment receipt. M061
  publishes exact prior-option exclusions to the second author and types any
  remaining cross-option conflict before envelope loss.
- `study-014` crossed the complete production root. Five exact P053 calls all
  succeeded in 821,072 ms, including one bounded complete semantic-spatial
  repair; two distinct options were accepted, one was selected, neutral geometry
  compiled, and the exact sandbox realization persisted. P060 then measured
  four project-derived architectural criteria: program-component coverage,
  required-relationship coverage, and public-service separation passed, while
  usable-main-entry failed because the realized scene contains zero opening
  objects against a minimum of one. P061 independently compiled and realized a
  parameterized family bound to the retained aggregate consultation component;
  coverage is one of five semantic components and no enumerated room instances
  are claimed. The attempt is therefore honestly `pipeline_rejected`, not a
  usable building. All thirteen preregistered metrics are retained, making this
  the first comparable outcome: one failed assignment, eight planned, sample
  size one, and no evidence-table or aggregate-winner claim.
- Studies 015–023 form a separately preregistered architectural-revision
  subseries over that exact failed clinic artifact. They retain compiler
  rejections, timeouts, and failed P060 outcomes without retrying an assignment
  or rewriting history. M062 studies 025–026 finally prove the generic recovery
  mechanism: one exact rejected geometry round is resumed through P036, the
  model-authored door assembly is preserved, P055 checkpoints the successor,
  and all four recomputed clinic P060 criteria pass. Those maintenance studies
  are mechanism evidence, not additional P062 assignment results, and they do
  not claim a P061 family terminal.
- `study-027` returns to the original three-case by three-condition experiment
  matrix after that repair closure. It freezes the current dirty-worktree
  content identities, the extended semantic/geometry repair contract, the
  unchanged Agent CLI profile, a corrected four-hour study ceiling, and the
  one-attempt rule. Its clinic/full intent is durable and the result index is
  one running/eight planned. No provider has yet been invoked for this study,
  so it currently contributes no outcome or empirical claim.


## Completion

- Completed: 2026-08-28
- Evidence: study-028 executed the full preregistered 3x3 matrix through the live codex-agent-cli provider: 9/9 exact outcomes (3 completed incl. first fully-usable courtyard candidate, 6 pipeline_rejected with typed failures), first table-ready result index, ablation degradation measured, no fallback or hidden retry; 24 experiment tests pass
