# Event-triggered observation triage (#204 / #205)

This opt-in lab measures whether a reasoning model needs to see each local
observation. It runs real exact geometry queries and real Claude calls on public
synthetic inputs. It does **not** change the product's wake policy, build a new
controller, revise a design, or accept a Stage. See [measured results](results.md).

## Existing mechanisms reused

The branch preserves the histories of these unmerged dependencies:

| Dependency | Exact revision | Reuse |
| --- | --- | --- |
| [#198](https://github.com/cogco1/MonkeyHub/pull/198) | `72f424800a4eadf71604648d2d93f8163f22b10e` | `Fixture`, P036 retained StateRecord/STEP, source-bound OCCT queries, same-source line PNGs, restricted `ClaudeConsumer` and Monitor usage |
| [#202](https://github.com/cogco1/MonkeyHub/pull/202) | `f4399f4f9db0cab3ceba6d2fd804d90bf0261afe` | Fixed portico project, retained-record readback, `source_fields_preserved`, existing `UsageLog`/TurnTrace projection |

The portico check really creates and reads the inherited public fixture. It is
not a rerun of #202's provider-driven courtyard revision loop. The event workload
uses #198's exact query boundary; geometry and binding checks remain with their
existing owners. This is an extension of lab consumers, not a new domain module.

## Fixed comparison

Each split has six tasks and fourteen checkpoints. Development uses scale 1;
holdout uses scale 2 and different Chinese/mixed-language wording. Both use the
same source-bound operations, so this is a small transfer check, not independent
architectural validation.

| Task | Checkpoints | Evidence and required disposition |
| --- | ---: | --- |
| Ordinary refresh | 4 | Repeated verified state/pair/dependency observations, unchanged: continue locally |
| Spatial relation | 1 | Cold STEP solid distance of U-shell/insert changes: review |
| Hard constraint | 2 | Twin distance crosses the declared `3.5 * scale` minimum, then recovers: revise, review |
| Stale identity | 1 | The actual exact-query adapter rejects the previous source: refresh |
| Visual judgment | 2 | Current-source top/front PNGs: retain a visual-review checkpoint |
| Semantic update | 4 | Two administrative updates, two uncertain/spatial-intent requests; Chinese and mixed language |

`fixtures.py` owns input definitions and scoring labels; `public_state` uses an
explicit allowlist that excludes labels, task IDs and gate decisions. The main
model receives identical instructions and checkpoint evidence in all conditions.
Distance/volume tolerances are fixed at 1e-6 m / 1e-9 m3, not fitted to labels.

- **A:** invoke Claude at every checkpoint.
- **B:** derive local events; skip only verified, unchanged observations with no
  semantic or visual request. Changes in identity, declared dependencies, exact
  distance/overlap and hard-constraint status wake the model. Unavailable or
  failed evidence also wakes it. Persistent constraint failure remains actionable.
- **C:** same as B, except *semantic-only* checkpoints can call Jev. Stale sources,
  hard constraints and required visual evidence cannot be suppressed by Jev.
  `ignore` can suppress only above the development-selected threshold. Unknown,
  low-confidence, malformed, timeout, HTTP failure and model mismatch fall back
  to Claude. No hidden repair, retry or result cache exists in this lab.

If Jev is available, evaluate fixed thresholds `.5, .75, .9, 1.000001` on
development responses only. Select the threshold giving most skips with zero
development misses, breaking ties toward the higher threshold; the last option
disables suppression. Freeze it before C holdout calls. Four development semantic
examples cannot establish calibration or a safe production threshold. C is
compared only with A/B on the same holdout task IDs, with calibration time/cost
included separately. No real responses means no selected threshold and no C run.

## What the measurements mean

Success means each checkpoint returns the correct **disposition** with the exact
source, and mandatory images actually reach the provider. Visual explanations
are retained; subjective architectural quality is unscored. These are triage
tasks, not completed building revisions. Final geometry errors, first candidate
time and human acceptance remain unknown/not applicable.

Report main wake count, underlying returned model identities, false wakes, gate
misses, failed meaningful dispositions, task/checkpoint completion, gate compute,
input bytes, exact-query counts/bytes, observation/render time, client waits,
provider usage (including cache buckets), failures, fallback and calibration.
An immediate wake has zero intervening checkpoints; intervention timing ends at
dispatch, while client wait ends at the returned disposition.

Fixture construction, cold readback, query and render preparation is measured
once and shared unchanged across alternatives. Replay wall time is measured
separately. Workload tables explicitly charge the whole common preparation to
each alternative within a split; **do not sum those preparation charges across
splits**. This does not simulate a live stream or prove an end-to-end design speedup.
Condition order rotates by task/repetition; seeds and pure inference timing are
unavailable from the inherited CLI. Internal provider retry counts are unknown.
CLI API-equivalent cost is not subscription billing. Jev cost uses known input
usage at the dated public rate; missing usage and local monetary cost stay unknown.

Monitor stores provider observations through the inherited `UsageLog`. Parent
links are added in a disposable `build_traces` projection over that journal; there
is no second project memory or event store. The provider has no tools, project
path or accepted-state writer. All project preparation still uses P036.

## Jev mechanism and limits

[TypeSafe's interface](https://docs.typesafe.ai/introduction) evaluates typed
questions over state. A single Choice is sufficient here: ignore, review or
uncertain. It avoids free-text decision parsing, but a valid answer is not proof
of a correct answer. Independent questions could share one request; this slice
has one decision per checkpoint and therefore does not add a batching framework.

[Intent routing](https://docs.typesafe.ai/patterns/intent-routing) supplies the
useful mechanism: a bounded classification followed by deterministic routing and
an explicit fallback. We apply it only after exact checks pass.
[Confidence](https://docs.typesafe.ai/confidence) describes concentration of the
answer distribution, not this project's probability of correctness.

The [model specification](https://docs.typesafe.ai/models), checked 2026-09-20,
lists pinned `jev-1.13.0`, text-only input and $0.042 per million input tokens;
output tokens are free. The thin adapter uses the documented
[`POST /v1/systemone`](https://docs.typesafe.ai/api) endpoint and validates the
actual returned model. It neither follows redirects nor discovers credentials.

[Documented jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13) includes
precise arithmetic, counting, multiple reasoning steps, irrelevant context and
adversarial or contradictory wording. Accordingly, identity, thresholds,
geometry, counting and permission checks stay in code. Jev does not see CAD
images and cannot replace the visual-review path. Low confidence and ambiguous
Chinese/mixed language must preserve the main-model intervention.

## Run

Use the existing Python environment with OCCT and Studio fixture dependencies.
No package installation or application restart is needed.

```powershell
python -m pytest labs/event_gating labs/spatial_observation tests/monkeymonitor/test_design_loop.py tests/monkeymonitor/test_trace.py tests/monkeymonitor/test_core.py -q
python tools/archcheck.py
python -m labs.event_gating.benchmark --output <new-absolute-external-diagnostic-root> --claude <existing-claude-executable>
```

`--task dev-harmless` runs a labelled smoke. `--repetitions 2` or `3` repeats
paired tasks. `--with-jev` opts into C using only an already configured
`TYPESAFE_API_KEY`. It never purchases access or prints the credential. Missing
access is retained in `calibration.json`; A/B still run. Unit-test transport
responses are explicitly synthetic, not measurements.

The explicit external output retains fixed P036 projects, query/source manifests,
PNG inputs, all provider responses, `trials.jsonl`, `summary.json`, `calibration.json`,
the Monitor journal, trace projection and a manifest with code hashes. Existing
output is never overwritten. Private projects or user documents are not inputs.
