# Observation pilot protocol — fixed before measured trials

2026-09-20. Questions and scoring in `benchmark.py` are fixed before the pilot.
This is an engineering-audited synthetic experiment, not a human architectural
preference study. Independent analytic expectations and saved-STEP/production-PNG
checks are in `test_fixture.py`. No private design, user conversation or filesystem
path is sent to the model. Pilot outcomes do not validate architectural judgment.

Hypothesis H195: for these exact, declared facts, adding source-bound queries to
the same current line PNGs recovers correct identity, solid measurement and declared
impact at a measurable extra cost. A is a strong structured-information comparator.
Refutation includes D0 failing to recover facts, or adding cost without improving
matched correctness over A. B0's absence of metric scale, labels and dependencies is
an information constraint, not evidence that a VLM cannot reason about geometry.

H170 is separate: at fixed JSON observation, a selector can reduce context while
preserving audited critical evidence. Its protocol, held-out queries and algorithm
configuration live in `selection_protocol.md`; no representation change is credited
to selection. Neither hypothesis assumes learned embeddings will help.

## Fixed design

* A: snapshot JSON only. B0: the actual production five-view orthographic line PNGs
  only. D0: exactly those PNG bytes plus requested state, dependency, solid-pair,
  point, shape or whole-scene HLR facts. These are not RGB, depth or normal maps.
* Common information: task text, requested source binding, meter/CAD Z-up convention,
  output policy, round number, and each attached view's name and pixel size.
  B0 does not secretly receive the view crop in meters, labels, or snapshot.
  The corrected protocol explicitly supplies the same production camera axes to
  every condition (front looks CAD +Y, back -Y, left +X, right -X, top -Z).
* One base model, one predeclared edit variant, and scale 2.0. Three repetitions per
  A/B0/D0 condition. Additional base front-only/top-only B0/D0 trials, three repeats
  each. A's view sensitivity is not applicable. Total 39 trials, 11 questions each.
* Same installed Claude CLI 2.1.272, explicit model `claude-opus-5[1m]`, effort low;
  retain actual returned model identity. The provider gives no dated immutable
  weight revision or sampling-seed control: repeated calls are not seeded replay.
* Each trial: three model calls, maximum 4,096 output tokens per call, 180 seconds
  per call, CLI API-equivalent budget cap USD 2 per call. All conditions receive
  identical three-round review instructions without gold feedback. D0 can request
  at most 12 exact queries over the first two rounds. Failed/time-out/invalid JSON
  calls terminate that trial and remain in its denominator; no success-only retry.
* Before final calls, every provisional answer remains in context. Correction is
  first-wrong→final-right; regression is first-right→final-wrong. The explicit wrong
  prior about overlapping bounds is a separate fixed correction task.
  Raw mechanical nonmatches include abstentions. The report therefore separates
  resolving an initial abstention from correcting an actual wrong assertion;
  an unknown→known transition is not called a reasoning-error correction.
* Numeric answers use absolute error and tolerance max(1e-6, |gold|×1e-6); identity,
  visibility, containment and complete declared closure require exact equality.
  Missing/duplicate answers fail. Unknown role and stale source are explicitly
  correct refusals; abstentions on answerable facts are reported separately.
* The pilot is descriptive: show each repetition and ranges, no statistical
  significance or general superiority. No post-hoc task tuning or selective
  additional sampling. A separate follow-up may target an observed failure.

Protocol amendment: the first 39-trial execution supplied CAD Z-up and view names
but omitted the front/back axis definitions from A's prompt. That makes its
visibility answers convention-dependent. Preserve the **entire** initial run as
`orientation-unspecified-v1`, with every response and cost. Run the complete
unchanged 39-trial design again with explicit camera axes for all three conditions;
use only this `explicit-camera-v2` run for the primary comparison. This is a
whole-protocol repair, not success-selective resampling. No geometry, task, gold,
model or budget changes. Smoke and both full runs are accounted separately.

Answerability is evaluated separately from mechanical gold match. A and D0 have
the information needed for the nine exact-fact questions. B0 has no stable-ID
mapping, metric scale or declared dependency/role data: its abstention on those
questions is appropriate. Report correct supported assertions, wrong assertions,
appropriate abstentions and abstentions despite available facts separately.
There is a weaker visual inference on the bounds-correction question: when top
is supplied, recognizing the U silhouette and the interior square permits a
disjoint-footprint inference without metric scale. Report a correct such answer
separately as visually supported **conditional on the unverified name-to-shape
correspondence**, not as either exact-ID verification or a reasoning failure.
The stale-source task is answerable from common metadata in every condition.
Unknown-role gold matches from B0 do not verify that a named object's role is
absent; they are calibrated lack-of-information responses. HLR visibility is
line visibility, not whole-object pixel occlusion. Front/top-only D0 conditions
restrict the initial images, not subsequent named visibility queries.

## Source and measurements

The fixture authoring has one StateRecord and one compiled program per variant.
P036 retains them and the export; query operations cold-verify that retained STEP.
No acceptance operation runs. HEAD bytes are compared before/after each trial.
CAD Z-up is explicitly mapped to Hub Y-up `(x,z,y)`. An overlapping-bounds U-shell
example has positive exact clearance, independently checked analytically.

Save synthetic input, image bytes, source refs, prompts, every text answer and exact
query response, errors, CLI usage, and code hashes. Exclude CLI initialization
account/session/path metadata. `UsageLog` from MonkeyMonitor holds normalized
per-model counters, including auxiliary model calls; it is not a second telemetry
system. Native assistant usage and final CLI per-model totals remain distinguishable.

Separate fixture generation/export/verification, rendering, image base64 packing,
JSON serialization, exact-query time and end-to-end CLI wait. Transport, GPU copy,
provider inference and first-token timing are unknown where not exposed; CLI
`duration_api_ms` is preserved but not renamed pure inference. First correct answer
is assessed at completed response boundaries, not fabricated token timestamps.
Model input/cache/output counts and CLI list-price equivalents are retained.
Actual subscription charge and quota consumption are unknown. Cost includes all
repetitions, unsuccessful calls, auxiliary usage and query rounds. Report equal
budget ceilings and also the actual cost to first correct/full answer where reached.

## Scope and reuse

The stopped ec46 `observation.py` WIP was read-only reviewed: explicit source checks,
bounds-as-selection-only and exact-pair delegation are useful mechanisms. It lacked
retained-source loading, separately controlled baselines, audited held-out trials
and provider comparison. This lab extends the existing production owners by
composition. It does not copy that parallel Scene/Binding layer or private inputs.
Its segment-ray experiment is outside this task's minimum.

External project data and full diagnostics stay in an explicitly chosen temporary
experiment root. Public synthetic evidence is promoted to
`probes/spatial-observation-v1/`; it is not accepted project state. Production
chat, viewport, runtime lifecycle, canonical state and authority remain unchanged.
