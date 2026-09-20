# Fixed-candidate allocation benchmark

Executed 2026-09-20 from clean source commit `85e0f948fb3544b2196aaab79422ca0322d595c6`, Python 3.12.10.
Allocator `sequential-ocba-v2`; sampler `controlled-iid-v1`; master seed `20260920`.

This is a controlled ranking-and-selection experiment over a frozen set. The 24 MonkeyHub options were made by the existing production option owner, retained through P036, and reopened. Their GFA is measured; Gaussian noise and per-attempt cost units are artificial. No CAD, daylight, structural or architectural-quality conclusion follows.

## Run and retained data

```sh
python -m labs.candidate_evaluation.allocation_benchmark --output <fresh-external-directory> --repetitions 200 --budgets 100 300 900 --workers 8
python -m labs.candidate_evaluation.plot_benchmark <same-directory>
python -m labs.candidate_evaluation.benchmark
```

There are **171 conditions**, with **200 independent outer repetitions per condition** (34,200 executions). Methods and budget levels deliberately share each trial/candidate stream for paired comparison; 34,200 is not a count of mutually independent worlds. Warmup consumes the same budget. Trials use sample budgets 100/300/900, and cost budgets 500/1500/4500 on the two unequal-cost fixtures.

The checked-in [aggregate CSV](benchmark_results.csv) includes every condition, PCS Wilson 95% intervals, mean regret and its standard error, attempted/successful/failed counts, mean normalized cost, measured elapsed time and per-candidate allocation. The external run output retains `summary.json` (all exact public inputs, source vectors and configuration), `trials.jsonl.gz` (every raw observation, updated estimate, allocation reason, seed and stopping reason), `evaluation.json`, and PCS/allocation PNG/SVG plots. Large raw trajectories and project records are not checked into this lab. Regeneration uses the command above; elapsed times and UTC timestamps are not replay invariants.

## Highest-budget comparisons

Each cell is **PCS / mean simple regret**. Sample and cost rows are different experiments; sample-count comparisons do not imply equal cost. Full budget curves and uncertainty are in the CSV. A dash means fixed-cost OCBA is not defined for the sample-budget condition.

| Fixture / budget unit | Budget | Equal | Round robin | Variance | Epsilon greedy | OCBA | Cost OCBA |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| easy / samples | 900 | 1.000 / 0.00000 | 1.000 / 0.00000 | 1.000 / 0.00000 | 1.000 / 0.00000 | 1.000 / 0.00000 | - |
| close_top_two / samples | 900 | 0.750 / 0.01250 | 0.750 / 0.01250 | 0.725 / 0.01500 | 0.725 / 0.01375 | 0.935 / 0.00325 | - |
| heterogeneous_variance / samples | 900 | 0.585 / 0.02075 | 0.585 / 0.02075 | 0.635 / 0.02000 | 0.915 / 0.00425 | 0.760 / 0.01200 | - |
| heterogeneous_cost / samples | 900 | 0.740 / 0.01300 | 0.740 / 0.01300 | 0.755 / 0.01225 | 0.785 / 0.01075 | 0.870 / 0.00650 | - |
| heterogeneous_cost / cost | 4500 | 0.760 / 0.01200 | 0.760 / 0.01200 | 0.745 / 0.01275 | 0.765 / 0.01175 | 0.780 / 0.01100 | 0.770 / 0.01150 |
| many_inferior / samples | 900 | 0.775 / 0.02250 | 0.775 / 0.02250 | 0.785 / 0.02150 | 0.785 / 0.02150 | 0.995 / 0.00050 | - |
| deterministic_mixture / samples | 900 | 0.795 / 0.01025 | 0.795 / 0.01025 | 0.850 / 0.00750 | 0.940 / 0.00300 | 0.795 / 0.01025 | - |
| missing_samples / samples | 900 | 0.740 / 0.01300 | 0.750 / 0.01250 | 0.745 / 0.01275 | 0.705 / 0.01475 | 0.905 / 0.00475 | - |
| heavy_tail_t3 / samples | 900 | 0.780 / 0.01100 | 0.780 / 0.01100 | 0.750 / 0.01250 | 0.735 / 0.01325 | 0.920 / 0.00400 | - |
| monkeyhub_fixed_massing / samples | 900 | 0.720 / 0.03760 | 0.720 / 0.03760 | 0.800 / 0.02490 | 0.620 / 0.04420 | 0.910 / 0.01050 | - |
| monkeyhub_fixed_massing / cost | 4500 | 0.920 / 0.01010 | 0.920 / 0.01010 | 0.950 / 0.00520 | 0.660 / 0.04050 | 0.955 / 0.00590 | 0.970 / 0.00340 |

## Interpretation

OCBA is not a universal winner. At 900 samples, the close-leader fixture gives equal PCS 0.750 (95% CI 0.686-0.805) and OCBA 0.935 (0.892-0.962). On heterogeneous variance, OCBA reaches 0.760 (0.696-0.814), while epsilon greedy reaches 0.915 (0.868-0.946). These are results for the stated distributions and budget grid, not architecture or general algorithm guarantees. The Wilson intervals describe each policy separately; they are not a paired-policy difference significance test, and no multiplicity-adjusted winner claim is made.

Classic and fixed-cost OCBA use the published APCS ratio approximations and a sequential integer scheduler. Costs are known, fixed and additive. Ties, zero empirical variance and declared deterministic mixtures use an explicit balanced-sampling fallback; therefore the mixed fixture is not evidence for a new OCBA theorem. Heavy-tailed observations likewise violate the classical Gaussian assumptions. See [primary sources, exact upstream revision and implementation decisions](RESEARCH.md).

| Heterogeneous cost: same 900 samples | Mean consumed declared cost units | PCS |
| --- | ---: | ---: |
| equal | 3780.00 | 0.740 |
| ocba | 11578.60 | 0.870 |

The cost-budget rows above are the appropriate comparison when total declared cost, rather than number of attempts, is fixed.

At cost 4,500 in the heterogeneous-cost fixture, equal/classic/cost OCBA PCS is 0.760/0.780/0.770: the cost-aware rule does not show a clear advantage here. On the fixed MonkeyHub options, the corresponding figures are 0.920/0.955/0.970; cost OCBA's marginal interval is 0.936–0.986 and equal's is 0.874–0.950. The point improvement remains an artificial-noise result, not a physical or architectural improvement.

For a descriptive 0.95 PCS threshold, the first tested budgets whose marginal Wilson lower bound exceeds 0.95 are: easy fixture at 100 samples for epsilon greedy/OCBA, 300 for equal/round-robin/variance; many-inferior fixture at 900 for OCBA. No other condition crosses this conservative display threshold. This is a coarse-grid observation, not a confidence-based stopping rule or a multiple-comparison guarantee.

## Budget, failures and replay

- Total attempted observations: 16,530,916; failures: 196,696. Failed attempts consume budget and have no fabricated numeric value.
- Unselected executions: 0. Regret is conditional on selection; an unselected execution counts as a PCS failure.
- Warmup-incomplete executions: 2000. With 30 or 24 alternatives, 100 attempts cannot provide five successful observations per alternative; those low-budget conditions contain no adaptive phase. Selection is still possible once every valid candidate has at least one observation, so incomplete warmup does not by itself mean no selection. They remain in the report rather than being silently given free warmup.
- Known deterministic responses are sampled once. Invalid/unavailable source candidates cannot be sampled; the missing-data fixture includes an invalid alternative with deliberately enormous nominal mean.
- Every draw has an attempt index; every trajectory retains the post-attempt statistics and allocation reason. Pre-decision statistics follow from earlier rows. Same-seed replay reproduces these values, not additional evidence.
- The complete gzip stream passed its integrity read and contained exactly 34,200 executions. The last fixed-candidate/cost-OCBA trial was independently regenerated; all saved values and its full trajectory matched except measured elapsed time. This replay did not increase the Monte Carlo repetition count.
- Stopping is fixed-budget exhaustion (or no affordable eligible attempt). There is no online PCS certificate, non-stationary model update or human acceptance decision.

## Evaluation correctness and acceptance mapping

The unchanged GH-123 small benchmark produced {'valid': 1, 'invalid': 4, 'unavailable': 3}, 12 massing variants and 6 Pareto candidates. The two-point mean of means was 9.984130859375, and mean sample variance 3.998406108787, against reference 10 and 4. Mean massing call time was 0.245 ms on this concurrently loaded host; it is not a portable performance claim.

| Issue requirement | Evidence / scope |
| --- | --- |
| #123 hard validity, four deterministic objectives, units/direction/failure | Existing `evaluator.py` and tests are unchanged; no missing score becomes zero. |
| #123 vectors, normalization, Pareto and reproducibility | Existing 12-variant benchmark plus 24 fixed retained options; deterministic results and bindings replay. |
| #123 stochastic statistics, cost and provenance | Existing two-point evaluator and `summarize`; controlled sampler retains successful moments separately from paid attempts, seeds and fixed configuration. |
| #123 exact retained candidate input and stable consumer | `retained.py` loads caller-selected P036 refs against previously saved RunRef/content/context; missing/corrupt/unsupported inputs are unavailable. Allocator adapter consumes existing result fields without geometry internals. |
| #124 independent allocator and common baselines | One protocol, six policies, identical frozen fixtures; sequential requests never alter candidates or project state. |
| #124 close means, unequal variance/cost, multiple budgets, PCS/regret | 171 aggregate rows with 200 independent repetitions each, raw trajectories retained externally. |
| #124 failures, invalid filtering and deterministic mixture | Explicit failure/attempt accounting, invalid high-response arm, one-shot deterministic observations and labeled fallback. |
| #124 fixed MonkeyHub candidate set | 24 public options through actual `make_option` and P036 write/reopen; underlying GFA vectors retained independently of artificial noise. |
| #124 assumptions, failure regimes, replay | Original literature, fixed-cost definition, external implementation review, statistical boundaries and complete trace format are documented. |

Validation: **92 focused tests passed**; architecture and submitted write-scope checks passed. Five GitHub CI jobs passed on the implementation commit. No production API/DTO/web change is present. Reviewer acceptance and merge remain separate.

External core Stage/protected-relation validation, usable area, calibrated physics, learned preference, batch scheduling and Pareto-set identification are not provided by this experiment. Their availability is not implied by passing the declared massing checks; learned, batch and Pareto extensions are optional later Issue work. No Issue is automatically closed by this report.
