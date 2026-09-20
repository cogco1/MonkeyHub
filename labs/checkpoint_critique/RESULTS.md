# GH-173 results: a successful harness, no observed quality gain

**All four arms completed 3/3 live trials. No live trial introduced the labeled
entry-side error, needed a repair, or produced an unsupported objection.** This
fixture therefore provides no real error sample with which to estimate a timing
or separate-Critic benefit in propagation/rework. The scripted controls establish
that the harness can measure those effects; they do not supply missing model
evidence. The current evidence does not justify asynchronous Shadow Critic work.

## Live comparison

The [retained plan](../../probes/checkpoint-critique/runs/live-v1-plan/workspaces/checkpoint-critique/preregistered-plan.json)
was written before the first scheduled call. The fixed protocol source is
`afcc091152a95568e64b6d25d850d9dc9dbc2029` (see the plan for the authoritative full
revision), based on main `3a92b41460b52c04963278a3300a29c34744c43e`. No source
behavior changed during this batch. Later changes add offline batch verification
and documentation. The [batch result](../../probes/checkpoint-critique/runs/live-v1-plan/workspaces/checkpoint-critique/batch-result.json)
contains the exact report references, raw values, sample standard deviations and
all 12 scheduled outcomes.

The primary model was `claude-opus-5`, effort low, CLI 2.1.272; every call also
reported auxiliary `claude-haiku-4-5-20251001` usage. All arms used the same fixture,
operator set, stage rules and final checks, and the same **10 CLI calls / 900 s /
$3 CLI-estimated API-equivalent ceiling per trial**. Seed control was unavailable.
Three repetition blocks ran concurrently, with orders ABCD / BCDA / CDAB. Neither
concurrency nor the repetition index establishes statistical independence.

| Arm | Completed / planned | CLI calls per trial | Mean elapsed ± sample SD (s) | Mean CLI API-equivalent cost ± sample SD (USD) | Mean inclusive input / output tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| A: self-review after | 3 / 3 | 4 | 44.694 ± 2.548 | 0.345721 ± 0.008517 | 47,249.7 / 2,753.0 |
| B: self-review during | 3 / 3 | 6 | 68.140 ± 4.088 | 0.707821 ± 0.014456 | 96,337.0 / 4,972.3 |
| C: separate Critic after | 3 / 3 | 4 | 41.917 ± 4.075 | 0.326891 ± 0.004792 | 44,788.7 / 2,640.3 |
| D: separate Critic during | 3 / 3 | 6 | 67.709 ± 2.872 | 0.613970 ± 0.015138 | 82,116.7 / 4,841.3 |

Every arm had zero failures, timeouts, stalls, repair calls, late revisions,
discarded operations, false objections, unsupported hard-block requests, false
hard blocks and human interventions in this live batch. Applicable final fixture
checks passed in all 12 trials. Each final state still reports structural analysis
unavailable, separately from failure; generic/unknown detail is permitted and
does not certify any structural/BIM semantics. Each arm produced one distinct
final design-content digest across its three repetitions. That is not a claim of
architectural alternative diversity.

The denominator for live originating errors is **0**, so live propagation depth
is **N/A (null)**, not a successful zero-depth interception. No feasible repaired
alternative was observed. There is no blinded human preference or independently
validated architectural-quality score in this experiment.

At the observed fixed target (complete declared schematic task), B used about
2.05× A's estimated cost and 1.52× its trial time; D used about 1.88× C's cost and
1.62× its time. C used less context than A, and D less than B, as specified by the
role intervention. These are descriptive observations on three repetitions, not
causal generalizations about latency or evidence of a better design policy.
Review frequency and realized computation differ even though the ceilings match.

All 60 CLI calls are retained: **36 proposal + 24 review + 0 repair**. Their
reported total API-equivalent cost was **$5.9832075**, inclusive input 811,476 and
output 45,621 tokens. The concurrent batch elapsed 228.177 s; summed trial elapsed
was 667.379 s. Those quantities should not be substituted for one another.

| CLI-reported model | Inclusive input | Output | Cache read (input subset) | Cache creation (input subset) | API-equivalent USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| claude-opus-5 | 472,786 | 44,568 | 21,285 | 451,381 | 5.6392525 |
| claude-haiku-4-5-20251001 | 338,690 | 1,053 | 0 | 0 | 0.3439550 |

These are CLI-reported values, not independently audited provider billing. Actual
account charges, subscription quota and exact API request count remain unknown.
CLI agent turns are not API requests. The requested 4096 output-token cap is not
proven for every internal request, and this is not exact total-token matching.

## Every scheduled live trial

Each report retains proposal/challenge/check/decision/usage references, the exact
candidate trajectory, public response summaries and the final independent result.
All rows below have protocol status `complete`, no unresolved completion
obligations, structural analysis unavailable, no originating error, no repair and
zero unsupported objections. Failed-run count is 0/12; no scheduled slot was
excluded or replaced.

| Trial report | CLI calls | Elapsed s | API-equivalent USD | Input / output tokens |
| --- | ---: | ---: | ---: | ---: |
| [r01-A](../../probes/checkpoint-critique/runs/live-v1-r01-a/workspaces/checkpoint-critique/report.json) | 4 | 46.861 | 0.3488460 | 47,156 / 2,745 |
| [r01-B](../../probes/checkpoint-critique/runs/live-v1-r01-b/workspaces/checkpoint-critique/report.json) | 6 | 65.750 | 0.6923555 | 95,056 / 4,751 |
| [r01-C](../../probes/checkpoint-critique/runs/live-v1-r01-c/workspaces/checkpoint-critique/report.json) | 4 | 41.598 | 0.3256325 | 45,000 / 2,623 |
| [r01-D](../../probes/checkpoint-critique/runs/live-v1-r01-d/workspaces/checkpoint-critique/report.json) | 6 | 69.551 | 0.6238915 | 82,572 / 5,142 |
| [r02-B](../../probes/checkpoint-critique/runs/live-v1-r02-b/workspaces/checkpoint-critique/report.json) | 6 | 72.860 | 0.7209940 | 97,197 / 5,117 |
| [r02-C](../../probes/checkpoint-critique/runs/live-v1-r02-c/workspaces/checkpoint-critique/report.json) | 4 | 38.010 | 0.3228535 | 44,669 / 2,581 |
| [r02-D](../../probes/checkpoint-critique/runs/live-v1-r02-d/workspaces/checkpoint-critique/report.json) | 6 | 64.400 | 0.5965455 | 81,036 / 4,401 |
| [r02-A](../../probes/checkpoint-critique/runs/live-v1-r02-a/workspaces/checkpoint-critique/report.json) | 4 | 41.888 | 0.3522345 | 47,899 / 2,928 |
| [r03-C](../../probes/checkpoint-critique/runs/live-v1-r03-c/workspaces/checkpoint-critique/report.json) | 4 | 46.142 | 0.3321870 | 44,697 / 2,717 |
| [r03-D](../../probes/checkpoint-critique/runs/live-v1-r03-d/workspaces/checkpoint-critique/report.json) | 6 | 69.177 | 0.6214725 | 82,742 / 4,981 |
| [r03-A](../../probes/checkpoint-critique/runs/live-v1-r03-a/workspaces/checkpoint-critique/report.json) | 4 | 45.332 | 0.3360825 | 46,694 / 2,586 |
| [r03-B](../../probes/checkpoint-critique/runs/live-v1-r03-b/workspaces/checkpoint-critique/report.json) | 6 | 65.810 | 0.7101125 | 96,758 / 5,049 |

## Deterministic mechanism and failure controls

The [four-arm control](../../probes/checkpoint-critique/runs/control-v4-plan/workspaces/checkpoint-critique/batch-result.json)
injects an east-entry error and uses a scripted reviewer. It is not an LLM run.

| Arm | Complete | Errors introduced / review-detected | Depth at detection | Existing dependent values repaired | Prior decision operations affected |
| --- | --- | ---: | ---: | ---: | ---: |
| A | yes | 1 / 1 | 3 | 3 | 2 |
| B | yes | 1 / 1 | 0 | 0 | 0 |
| C | yes | 1 / 1 | 3 | 3 | 2 |
| D | yes | 1 / 1 | 0 | 0 | 0 |

These differences follow the real retained `StateRecord` dependency chain and
actual old values changed by its existing successor operator. They demonstrate
measurement sensitivity to timing, not an observed model advantage or agent-count
effect. The root edit itself changes one value in both timings. No geometry-box
measurement is used as evidence of error propagation.

The [boundary control results](../../probes/checkpoint-critique/runs/boundary-control-plan/workspaces/checkpoint-critique/results.json)
retain all 10 declared cases and cold-replay all ten:

| Case | Retained outcome | Consequence |
| --- | --- | --- |
| early unknown | complete | Permitted missing gallery/access at early stages and unknown semantics are not blocked |
| unsupported early veto | complete | False objections are counted; a model cannot veto pending work |
| protected courtyard reduction | round_exhausted | Both attempted invariant violations refused by existing locks; building nothing is not success |
| changed delta in criticism | stale_criticism | No stale criticism is applied |
| timeout | timeout | Error-bearing interrupted trial is censored, depth null |
| missing model output | malformed | Missing response is a failure, not a correct answer |
| omitted gallery/access | round_exhausted | Final obligations remain unresolved |
| missed east entry | incomplete | Independent final check fails; missed depth null, observed lower bound 3 |
| ineffective repair | round_exhausted | One allowed repair is consumed; detected but unfixed error stays failed |
| exhausted call budget | budget_exhausted | No unreviewed completion or automatic promotion |

Separate tests also cover exact base/run/source/candidate/delta mismatches,
artifact/call-report tampering, false capability vetoes, whole-trial wall deadlines,
budget overshoots, provider exceptions, batch infrastructure failures and genuine
content identity across run bindings. Unknown usage is not replaced by zero.

## Verification and retained development failures

The final suite contains 45 tests: 15 fixture, 8 provider and 22 harness. The
retained live batch verifies all 12 planned slots, candidate operator trajectories,
exact review bindings/adjudication, public call artifacts and the all-trial
aggregate. P036 HEAD remains version 0 with the original empty-state digest;
design branches remain empty and no Stage or canonical version was issued.

```powershell
python -m unittest labs.checkpoint_critique.test_fixture labs.checkpoint_critique.test_provider labs.checkpoint_critique.test_harness
python -m labs.checkpoint_critique.benchmark --project probes/checkpoint-critique --batch live-v1 --replay
python tools/archcheck.py
```

Earlier deterministic development output is retained, not substituted into the
live comparison: `control-v1` stopped before trials because `ProjectArtifactRef`
does not expose `to_dict`; `control-v2` ran its four scripted trials but its batch
replay failed because transfer reads require a retained production artifact link.
Both were fixed using existing P036 dataclass references and verified JSON reads.
`control-v3` and `control-v4` completed/replayed all four scripted arms. Their
`lab_dirty` flags identify development runs; only `live-v1` is the preregistered
real comparison. No paid live sample was rerun to replace a failure.

One separate [exploratory provider smoke](../../probes/checkpoint-critique/runs/provider-smoke/workspaces/checkpoint-critique/observation.json)
cost $0.014883 API-equivalent and used 2,113 input / 105 output tokens. Its original
prompt/schema were not retained, so its public observation is labeled non-replayable
and excluded from the fixed fixture comparison. It is not an extra selected success.

The source/fixture is public and synthetic; no private project or native reasoning
transcript is included. Retention verifies evidence integrity, not architectural
correctness outside these declared predicates. Original sources and rejected
upstream mechanisms are documented in [RESEARCH.md](RESEARCH.md).
