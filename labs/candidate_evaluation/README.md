# Candidate evaluation and allocation experiments — GH-123 / GH-124

This callable research slice measures declared massing candidates, reports failed
and unavailable checks, and compares complete deterministic objective vectors. It
implements the deterministic/statistical slice of [#123](https://github.com/cogco1/MonkeyHub/issues/123) within
the boundaries of [#119](https://github.com/cogco1/MonkeyHub/issues/119) and
[#176](https://github.com/cogco1/MonkeyHub/issues/176). It is not a product API or
an architectural preference model. The independent sequential allocation
experiment for [#124](https://github.com/cogco1/MonkeyHub/issues/124) consumes the
same statistical values. See [research and implementation decisions](RESEARCH.md).

## 汇报用说明：现在可调用什么

- **输入：** 一个 `StateRecord`、独立确认的 project/run/base 与内容摘要、
  上下文证据引用，以及明确的 envelope / objective 配置。绑定不符会拒绝测量。
- **硬有效性：** 分别返回绑定、体量可测性、边界、限高和 FAR 的检查结果。
  已知违规为 `invalid`；必要证据或输入缺失为 `unavailable`；`valid` 只代表
  这组已声明检查通过，不代表真实建筑、Stage 或规范已验收。
- **客观指标：** 可读出占地、总楼面面积、层数和高度；在相同 base、上下文和
  评价配置下，对完整的确定性结果调用 Pareto 比较与固定范围归一化。
  缺值不补零，无效候选不能用高指标进入比较。
- **不确定性与成本：** 合成双点分布可返回样本数、均值、无偏样本方差、标准误
  和实际调用的摊销墙钟成本。统计量溢出时保留已经抽取的样本数和成本，
  数值保持 `null` 并说明原因；这与没有抽样、候选无效是不同情况。
- **仍不可判断：** 可用面积、日照/结构性能、空间质量、偏好和设计优劣。
  确定性指标方差为零不代表现实误差为零；合成噪声也没有建筑意义。

现有 benchmark 的完整 JSON 包含可重开的候选输入和独立记录的请求绑定。
这些候选是公开的合成体量 fixture；回放成功只验证接口、计算和来源绑定，
不是实际 CAD 成功或真实设计质量的证据。新增只读 P036 适配可读取已留存的
`state-record`，保持调用前保存的预期绑定。另有 24 个经现有 MonkeyHub option
变换生成、保存并重开的公开固定候选，用于比较预算分配；人为高斯噪声、费用
与原始四项目标分别保存。它们仍是声明的体量，不代表任意 CAD 或物理性能。

## Run and call

From the repository root, with Python 3.12 and no additional dependencies for
the original massing/synthetic benchmark:

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
  and synthetic evaluators, not a plugin-discovery mechanism. Sequential OCBA
  is now an independent experimental consumer; FEA and learned evaluation are
  still separate future capabilities.

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
Finite bounds alone do not guarantee representable arithmetic: overflowing spans,
offsets or normalized results raise `ValueError`, never a spurious zero or infinity.

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
`synthetic-two-point-v2` preserves the completed draw count if moment computation
fails; the moments stay unknown with an explicit failure reason. The distribution
fixture remains `synthetic-two-point-v1` because its sampling rule has not changed.

`evaluate_timed` measures elapsed wall time around one actual evaluator call.
`seconds_per_sample` is amortized elapsed time including evaluator overhead,
available only when all returned objectives share a positive sample count.
It is not separately measured solver time or per-draw latency. Monetary cost is
unknown (`null`). JSON export and benchmark housekeeping are outside this timer.
The pure deterministic result is reproducible; elapsed time is expected to vary.
Completed draws still incur cost when their moments are unavailable; a binding
rejection or zero requested draws incurs no sample count or per-sample estimate.

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

## Retained inputs and a frozen MonkeyHub candidate set

`retained.load_retained_request` uses the existing P036 integrity reader and
StateRecord parser; `evaluate_retained` adds the retained URI to source evidence.
Supply `repository`, `record_ref`, `expected_run`, `expected_content_digest`,
`context_refs`, and `evaluator=MassingEvaluator(...)`. Expected values must come
from the caller's previously saved binding. They are never reconstructed from
the newly loaded record to make a mismatch pass. A caller mismatch returns
`invalid`; missing/corrupt/unsupported retained input raises
`RetainedEvaluationUnavailable`, with no fabricated observed record or score.
This checks the explicit base, not whether it is today's HEAD; current-HEAD
requirements must be established by the caller. There is no project writer in
this adapter.

`retained_fixture.create_public_massing_fixture(empty_project_root)` authors a
public 6 × 4 cell study, calls the existing `studio.options.make_option`
`scale_volume` / `add_floor` transforms for 24 fixed variants (width 4–7,
depth 3–5, one/two floors), retains them via P036 and reopens them. HEAD does not
advance. Repeating on a fresh root yields identical content and binding IDs.
It requires the existing Studio Python dependencies, including FastAPI/Pydantic;
no new package or dependency pin is introduced. Benchmark temporary projects are
disposable; retained public inputs and results are included in benchmark output.

## Sequential allocation experiment

`allocation.EvaluationBudgetAllocator.allocate(EvaluationBudgetRequest)` returns
one candidate or an explicit stop reason. `SequentialAllocator` implements equal,
round-robin, variance-proportional sampling (largest variance/count), ε-greedy,
classical OCBA ratios, and fixed-cost OCBA ratios. It consumes
`CandidateEstimate(candidate_id, SampleStatistics, per_sample_cost, validity,
deterministic)` only; it does not inspect geometry, draw samples or persist state.
Only valid candidates are eligible. Unavailable statistics are never numeric
defaults. Evaluator/context identity is fixed by the experiment caller and
retained separately from this deliberately small mathematical input.

The caller supplies remaining **attempt** or **cost** budget and pays for every
attempt, including warmup and failures. Stochastic warmup is five successful
observations per candidate here; a declared deterministic response needs one.
Failed calls do not become zero observations. Equal balances successful counts;
round-robin rotates by attempted step, so failure fixtures distinguish them.
Cost OCBA requires a cost budget. Costs are positive known fixed experiment
units; measured wall time remains a separate field, and no monetary claim is made.

Exact ties, zero empirical variance and deterministic mixtures use an explicit
balanced-sampling fallback. A zero sample variance does not certify determinism.
The ratio calculation uses log arithmetic; retained counts are lower bounds,
and sequential integer decisions use the largest remaining target deficit.
These engineering choices carry no finite-budget PCS guarantee. See the
[equations and assumptions](RESEARCH.md), including why non-best ratios are
not simply divided by cost and why a jasima runtime adapter was rejected.

`sampling.ControlledSampler` holds one frozen source result, noise policy and
trial/candidate RNG stream. It uses the existing `summarize` function for every
successful update; `estimate_from_evaluation` passes the resulting statistics
to the allocator. The original objective vector remains in fixture metadata.
Synthetic experiments use known Gaussian or Student-t distributions. For the
24 MonkeyHub options only, the artificial response mean is **GFA/100**, with
explicit Gaussian noise and artificial costs. This single-objective choice is
an experiment preference, not a replacement for the objective vector or a
daylight/structure/composition evaluator.

```sh
python -m unittest labs.candidate_evaluation.test_evaluator labs.candidate_evaluation.test_retained labs.candidate_evaluation.test_allocation labs.candidate_evaluation.test_sampling tests.test_massing_metrics
python -m labs.candidate_evaluation.allocation_benchmark --output <new-external-directory> --repetitions 200 --budgets 100 300 900 --workers 4
python -m labs.candidate_evaluation.plot_benchmark <same-external-directory>
```

The output directory must be explicit and existing results are not overwritten.
`summary.json` retains exact public candidate inputs, evaluation vectors,
configuration, provenance and aggregate metrics; `summary.csv` is the flat
comparison. `trials.jsonl.gz` retains every repetition's seeds, selected candidate,
stop reason, costs, failures, final statistics and **all** observation/allocation
traces. `trace_columns` defines each trace row; the preceding rows reconstruct
every candidate estimate used for the next decision. `--workers` only parallelizes
independent Monte Carlo trials, not within-trial allocation.

Each trial/candidate has a distinct seeded stream. Policies and budget conditions
share that stream for a paired comparison; they are not extra independent trials.
PCS uses independent outer repetitions and Wilson 95% intervals. Mean simple
regret is conditional on a selection; unselected trials are counted explicitly
and contribute a PCS failure. Warmup-incomplete trials are also reported.
The optional plot command needs matplotlib and writes PCS and one allocation/estimate
trajectory as PNG/SVG in the same explicit directory.
Fixed-budget exhaustion is the stopping policy. A target-PCS budget may be read
from aggregate intervals; no per-trial confidence stopping certificate is claimed.

The fixture suite includes easy separation, close leaders, heterogeneous variance,
costs up to 20×, many inferior alternatives, deterministic mixtures, independent
missing observations and heavy tails. Correlated noise, biased model responses,
non-stationary versions and output-dependent failures are outside this IID
experiment's guarantees; such responses require a different statistical model.
Batch scheduling and Pareto-front identification remain optional later work.

## Concrete next inputs

**#125:** freeze one independently checkable structural case with connectivity,
member/section dimensions, sourced physical material values and units, supports,
loads, solver/version/license, applicable parameter range and reference checks
(reaction balance plus a hand or independent solver solution). A separate solver
adapter can return physical measurements and declared error information through
the same result without depending on OCBA. The current massing adapter supplies
neither an FEA model nor evidence of structural safety.

External core hard-check evidence, usable-area metrics, calibrated physical
uncertainty, preference/learned scores and FEA remain outside this slice. The
allocation lab does not become a production search controller or acceptance gate.
