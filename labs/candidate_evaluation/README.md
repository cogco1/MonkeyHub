# Candidate evaluation experiment — GH-123 first slice

This callable research slice measures declared massing candidates, reports failed
and unavailable checks, and compares complete deterministic objective vectors. It
implements part of [#123](https://github.com/cogco1/MonkeyHub/issues/123) within
the boundaries of [#119](https://github.com/cogco1/MonkeyHub/issues/119) and
[#176](https://github.com/cogco1/MonkeyHub/issues/176). It is not a product API or
an architectural preference model. The issue remains open.

## Run and call

From the repository root, with Python 3.12 and no additional dependencies:

```sh
python -m unittest labs.candidate_evaluation.test_evaluator tests.test_massing_metrics
python -m labs.candidate_evaluation.benchmark
```

The second command prints JSON to stdout, including fixed inputs, all failures,
raw synthetic samples, exact content/run/base refs, evaluator configuration,
code revision, dirty-lab flag, Python version, UTC interval and measured costs.
It writes no files. A caller retaining this output chooses an explicit external
experiment destination; retaining real project evidence still belongs to P036.
Run a committed checkout for a report whose revision identifies the code fully.

The benchmark is the first real consumer of `CandidateEvaluator.evaluate`.
Both implemented evaluators accept the same request and return the same value:

```python
from labs.candidate_evaluation.benchmark import ENVELOPE, candidate, request_for
from labs.candidate_evaluation.evaluator import MassingEvaluator, evaluate_timed

request = request_for(candidate("example"))  # Synthetic fixture; no project write.
run = evaluate_timed(MassingEvaluator(ENVELOPE), request)
print(run.result.validity)
print([(item.objective.name, item.value) for item in run.result.objectives])
print(run.elapsed_seconds, run.seconds_per_sample)
```

For an existing candidate, supply its `StateRecord`, independently expected
`RunRef` and content digest, and explicit context evidence refs in
`EvaluationRequest`. `request_for` is only a fixture convenience: deriving both
expected and actual values from one record does not prove it matches a live HEAD.
There is no filesystem lookup, automatic acceptance, or new persistence owner.

## Owners and limits

- **Reused:** `state.record` owns `StateRecord`, binding/content identities and
  `volume_boxes_of`; `state.massing_metrics` owns all four measurements and the
  existing `envelope_check` arithmetic. No formulas are copied into a second
  geometry implementation. The adapter checks input availability and finiteness
  before calling them, and never rounds an unsupported plan coordinate.
- **Existing consumer:** `studio.options` already calls these owners to measure
  candidate options. This lab does not change that application path.
- **Existing hard validation:** `validation.engine` validates a different input
  (`CanonicalState` plus `CandidateSubmission`) and remains the production gate.
  This lab does not construct or claim its receipts, Stage checks or acceptance.
- **New research code:** no registered owner provides generic sample statistics,
  objective comparison and this experiment caller. These live together in the
  lab under the existing [lab rules](../README.md); no production owner or public
  product contract changes. `CandidateEvaluator` is implemented here by massing
  and synthetic evaluators, not a plugin-discovery mechanism. OCBA, FEA and
  learned evaluators remain future consumers, not implementations implied by a
  protocol name.

The input domain is the existing voxel massing representation: `Volume@1` boxes
use inclusive bounds, x/z are plan, y is up, a plan cell is 1 m², and the declared
`MassingLevel@1` height is in metres. Levels and boxes must have finite inputs;
level height must be positive; the entire projection must be readable. The
adapter withholds all four objectives when the owner reports a partial
projection. This deliberately conservative choice avoids ranking partial sums.
It does not infer floors from CAD, verify volume/level physical correspondence,
or turn generic `Element@1` geometry into a massing. Such input is unavailable.

| Objective | Unit | Experiment direction | Definition |
| --- | --- | --- | --- |
| `footprint_m2` | m² | minimize | union of declared plan rectangles |
| `gross_floor_area_m2` | m² | maximize | sum of the footprints assigned to declared massing levels |
| `floor_count` | count | maximize | count of declared massing levels |
| `height_m` | m | minimize | top level face minus lowest level base |

Directions are fixed, inspectable choices for this experiment. They are not a
claim that fewer metres or more floors are always better architecture. GFA is
not usable area, and no program-efficiency estimate is invented.

## Result semantics

- `validity` concerns **only declared hard checks**: a known failure wins over
  unavailable checks; otherwise a missing required check means `unavailable`;
  all declared checks passing means `valid` within that scope. A valid synthetic
  binding with zero samples can still have an unavailable objective.
- Each objective separately carries unit, direction, `statistics.mean` (or
  `null`), sample count, uncertainty kind and an unavailable reason. `null` is
  never replaced with zero. Missing samples or inputs cannot enter Pareto
  comparison even if all hard checks passed.
- `run` and `candidate_digest` describe the **requested** binding;
  `observed_run` and `observed_candidate_digest` record the source actually
  inspected. A stale run, project, base or content is invalid, and measurements
  are withheld. An unbound record is unavailable. Source evidence refs remain
  attached to their observed input, including on rejected requests.
- The default required envelope checks are site bounds, maximum height and FAR.
  Missing site area makes FAR unavailable, rather than reusing the owner's
  optional-check behavior as a pass. Thresholds are fixture assumptions, not
  building regulations. Hard failures cannot be overridden by a high objective.
- `massing-v1` and canonical JSON configuration identify the algorithm and actual
  policy. The returned domain value creates neither a receipt nor a new digest.
  Export formatting, timing and code provenance belong to the experiment caller.
- Preferences are not implemented. Model discrepancy, physical applicability,
  epistemic uncertainty and human preference uncertainty remain unquantified,
  as stated in the result limitations. Deterministic sampling variance of zero
  says nothing about those errors.

`pareto_front`/`dominates` require valid, complete deterministic vectors under
the same exact base, version, configuration, context refs, units and directions.
They raise on mixed contexts or failed/unavailable entries instead of dropping
them. Equal vectors do not dominate each other. Stochastic means are not treated
as exact rankings. `normalize` uses caller-declared fixed bounds, orients values
so larger means better, and does not clip values outside the bounds. No weighted
utility or implicit normalization from the current candidate population is added.

## Statistical control and cost

`SyntheticEvaluator` produces equal-probability values `mean ± deviation`,
independent of geometry. Its population mean is `mean`, variance is
`deviation²`. It exists to test statistical plumbing for #124, not to score a
building. The reference run uses eight seeds, 2,048 draws each, mean 10 and
variance 4. Reusing the same seed replays a batch; it must not be counted as an
independent new batch by an allocator.

`summarize` uses the standard library's unbiased sample variance and reports
`sqrt(sample_variance / count)` as the IID mean's standard error. One stochastic
sample has an unknown variance and standard error; zero samples also has an
unknown mean. Non-finite or unrepresentable moments are refused. See the
[Python statistics documentation](https://docs.python.org/3.12/library/statistics.html#statistics.variance)
and [random reproducibility notes](https://docs.python.org/3.12/library/random.html#notes-on-reproducibility).
No third-party algorithm code, datasets or new dependencies are incorporated.

`evaluate_timed` measures elapsed wall time around one actual evaluator call.
`seconds_per_sample` is amortized elapsed time including evaluator overhead,
available only when all returned objectives share a positive sample count.
It is not separately measured solver time or per-draw latency. Monetary cost is
unknown (`null`). JSON export and benchmark housekeeping are outside this timer.
The pure deterministic result is reproducible; elapsed time is expected to vary.

## Checked small experiment

On Python 3.12, the fixture gives:

- Eight validity cases: **1 valid, 4 invalid, 3 unavailable**. Invalid cases
  independently violate site bounds, height, FAR or exact-base binding.
  Unavailable cases omit massing, use off-lattice bounds or omit required site area.
- The 4 × 3 m, two-storey, 3 m/storey reference measures **12 m² footprint,
  24 m² GFA, 2 floors, 6 m height**. An overlapping-box check measures the union,
  and translation leaves area/height unchanged.
- Twelve width/floor/height variants produce **six non-dominated variants**.
  For each fixed width/floor count, the 3 m storey dominates the 4 m storey under
  these declared objectives. A separate single-objective check orders GFA
  **12, 18, 24 m²**.
- Across eight synthetic batches, the mean of means is **9.984130859375** and
  mean sample variance is **3.99840610878725**, against known values 10 and 4.
  These bounded correctness checks do not establish model calibration or an
  advantage for any allocation/search algorithm. Per-call latency is printed
  by each run rather than treated as a portable performance claim.

Tests also exercise wrong project/run/content/base, malformed constraints,
missing evidence, non-finite coordinates including an unselected height metric,
unit/version/context mismatches, empty and single-sample statistics, and raw
sample/readback consistency. Lab tests are invoked explicitly and are not added
to the product suite.

## Concrete next inputs

**#124:** provide the existing OCBA implementation's repository/package, license,
exact revision, callable signature, required warm-up sample count, budget/cost
units, and seed/batch assumptions. Its adapter can then consume each objective's
count/mean/sample variance plus measured amortized cost through this result.
Start with uniform allocation and the known synthetic distributions. Incremental
sample accumulation, PCS/regret, cost-aware allocation, utility policy and
candidate × evaluator × fidelity selection are not implemented here.

**#125:** freeze one independently checkable structural case with connectivity,
member/section dimensions, sourced physical material values and units, supports,
loads, solver/version/license, applicable parameter range and reference checks
(reaction balance plus a hand or independent solver solution). A separate solver
adapter can return physical measurements and declared error information through
the same result without depending on OCBA. The current massing adapter supplies
neither an FEA model nor evidence of structural safety.

Production candidate ingestion, P036 retention/reopen, external core hard-check
evidence, usable-area metrics, calibrated uncertainty, preference/learned scores,
weighted utility and actual OCBA/FEA integrations remain outside this slice.
