# GH-204 / GH-205 — Event Gating Lab Measurement Report

Date: 2026-09-20 (run 08:14:48Z → 08:20:12Z UTC)
Protocol and primary documentation: [README.md](README.md)

## What this is

One paired pass per split (dev and holdout) of a synthetic, read-only
checkpoint-triage workload. Condition A wakes the main model on every
checkpoint; condition B applies the conservative gate. This is **not** a
completed architectural revision and **not** a production improvement claim.

Timing is indicative only. Beyond overlap with local regression testing on the
same machine, the coordinator confirms that other tasks were making concurrent
real Claude implementation calls during this measurement window. The observed
wall-clock deltas therefore cannot all be attributed to gating. The count and
cost evidence is of a different kind: main wakes, false wakes, token buckets
and the API-equivalent USD derived from them are counted per trial and are not
affected by concurrent unrelated load.

Models: both conditions actually ran `claude-opus-5` (requested
`claude-opus-5[1m]`), CLI 2.1.272, low effort, 768 output cap, no exposed
seed. Harness retries: 0. Provider-internal retries: unknown (not reported by
the provider). Failed responses and retries, if any, are retained in the
artifact set; this run recorded 0 main-model failures and 0 fallbacks.

## A/B by split

| Split | Cond | Checkpoints | Main wakes | Correct dispositions | False wakes | Replay wall s | API-equiv USD |
|---|---|---|---|---|---|---|---|
| dev | A | 14 | 14 | 14 / 14 | 6 | 87.912 | 0.344924 |
| dev | B | 14 | 10 | 14 / 14 | 2 | 57.955 | 0.250244 |
| holdout | A | 14 | 14 | 14 / 14 | 6 | 82.931 | 0.346830 |
| holdout | B | 14 | 10 | 14 / 14 | 2 | 57.703 | 0.249653 |

Each split: 6 tasks, all 6 triage tasks completed, 0 missed events, 0 failed
meaningful dispositions, 0 fallbacks, 0 main failures, 0 design revisions.
Gate overhead per split: A 0.465 ms (dev) / 0.302 ms (holdout);
B 0.338 ms (dev) / 0.217 ms (holdout).

## Combined A vs B (both splits)

| Metric | A | B |
|---|---|---|
| Checkpoints | 28 | 28 |
| Main wakes | 28 | 20 |
| Correct dispositions | 28 / 28 | 28 / 28 |
| False wakes | 12 | 4 |
| Replay wall s | 170.843 | 115.658 |
| API-equivalent USD | 0.691754 | 0.499897 |

Shared preparation is a single fixed fixture charged once when the two splits
are combined: **37.549 s**. Per the summary's stated basis, the whole fixture
is charged to each alternative and must not be summed across splits. Combined
prepared workload (replay + preparation once):

- A: 170.843 + 37.549 = 208.392 s
- B: 115.658 + 37.549 = 153.207 s

Calibration seconds: 0 in all four cells.

## Token usage (aggregate buckets)

Raw usage reports `cache_read_input_tokens = 0` everywhere, so the input side
is dominated by cache-write. Input tokens alone are **not** total input.

| Cond | input | cache write | cache read | output |
|---|---|---|---|---|
| A (both splits) | 56 | 43500 | 0 | 8462 |
| B (both splits) | 40 | 32021 | 0 | 5894 |

Main input wire bytes: A 38396 (dev) / 38427 (holdout); B 29156 / 29187.

Checkpoint source queries: the 40 exact queries per split were materialized
**once** during preparation and reused by both A and B; they are not 40 fresh
queries per condition. Query payloads: 103872 B (dev) / 103970 B (holdout),
observation 9.903 s (dev) / 7.599 s (holdout), render 0.457 s / 0.177 s.

Additional baseline queries: 42 = 6 baselines x 3 x 2, plus a stale cached
baseline 3 x 2. Preparation made 122 exact-query attempts in total, including
stale rejects; this count excludes low-level portico/readback IO.

## Jev condition C — not measured

`TYPESAFE_API_KEY` was absent. Four local development access checks were made
and issued **zero network requests**. No threshold was selected, no C
measurement exists, and no Jev net-benefit claim is made. Jev calls, Jev
API-equivalent USD, and Jev client wait are 0 across A and B because Jev was
never engaged. The missing credential does not block delivery of B.

## Run integrity

- All fixture project heads were unchanged by preparation; the provider had
  no tools and no project writer.
- Visual observations used actual same-source top and front PNG renders.
  Aesthetic quality was not scored.
- Final conservative gate/metric fixes were reviewed during measurement. The
  raw historical source hashes are unchanged; the final reanalysis carries
  separate final-source hashes and verifies the same 56 decisions and the same
  scoring.
- The actual report-writing implementation ran through the Claude CLI and is
  separate from the provider measurement. Its call artifacts
  (`documentation-claude.json`, `documentation-claude-review.json`,
  `documentation-claude-final.json`) are development overhead and are excluded
  from the runtime A/B figures.
- Validation: 154 passed, 1 optional skipped (upstream embedding), 1
  pre-existing Starlette warning. Archcheck 427 PASS, now completed.
- Unmerged dependencies: #198 fixture @
  `72f424800a4eadf71604648d2d93f8163f22b10e` and #202 @
  `f4399f4f9db0cab3ceba6d2fd804d90bf0261afe`. For #202 only the portico
  readback was used; the courtyard loop was not rerun.
- Actual billed charge, local compute USD, end-geometry errors and
  first-candidate latency are null in the data and are not reported.

## Artifacts

Local only, not committed to Git:
`D:/MONKEYHUB_DEV/workspace/experiments/event-gating-public-204-205-paired`
(`manifest.json`, `fixtures.json`, `trials.jsonl`, `summary.json`,
`monitor/usage.jsonl`, `traces.json`, `calibration.json`, `reanalysis.json`).
A smoke sibling, `event-gating-public-204-205-smoke`, ran A4/B0 with 4/4 on
both at 0.093717 equivalent USD and is retained separately.

## Next step

Run the credential-enabled C holdout comparison, then a broader task proof.
Keep B as the baseline in the meantime.

## Limitations

Single paired pass per split, six synthetic read-only triage tasks each, one
model and one effort setting; the workload is a fixture replay rather than
live use. Cache-read was zero, so token costs reflect cold cache-write
behaviour, and provider-internal retries are unobservable. Equal
correct-disposition counts across A and B on 28 checkpoints cannot distinguish
a genuinely safe gate from a gate that is untested on harder event
distributions. All USD figures are API-equivalent estimates, not actual
charges. No statistical significance, confidence interval, or causal
performance claim should be read into the differences above.
