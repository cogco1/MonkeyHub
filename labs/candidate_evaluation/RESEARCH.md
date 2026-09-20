# OCBA allocation: sources, implementation choice and limits

This experiment allocates observations among a fixed set of valid candidates.
It does not generate candidates, decide architectural preferences, accept a
project version or control production search. The local allocator is an original
implementation of the mathematical rules below; no third-party implementation
code has been copied. The existing evaluator statistics remain the statistical
interface. See [README.md](README.md) for evaluator scope and input semantics.

## Classical rule and its assumptions

Chen, H.-C., Chen, C.-H., Dai, L. and Yücesan, E. (1997), *New Development of
Optimal Computing Budget Allocation for Discrete Event Simulation*, Proceedings
of the Winter Simulation Conference, pp.334–341, presents a sequential
finite-difference/gradient approach in §3, pp.336–337, equation (3). It is an
earlier method, not the closed-form ratio baseline implemented here.
[Original proceedings PDF](https://www.informs-sim.org/wsc97papers/0334.PDF).

The baseline follows Chen, C.-H., Lin, J., Yücesan, E. and Chick, S. E. (2000),
*Simulation Budget Allocation for Further Enhancing the Efficiency of Ordinal
Optimization*, Discrete Event Dynamic Systems 10, pp.251–270. The inspected
author manuscript's §3, equations (9) and (12), Theorem 1 and the subsequent
sequential algorithm give the ratios and update procedure. Manuscript page
numbers differ from the journal pagination.
[Publisher/DOI](https://doi.org/10.1023/A:1008349927281),
[author-uploaded full manuscript](https://www.researchgate.net/publication/30845808_Simulation_Budget_Allocation_for_Further_Enhancing_the_Efficiency_of_Ordinal_Optimization).

For the local higher-is-better objective, let `b = argmax(mean_i)`,
`d_i = mean_b - mean_i`, and let `s_i²` be the sample variance. With positive
variances and a unique estimated best, the relative sample targets are

```text
r_i = s_i² / d_i²                         for i != b
r_b = s_b * sqrt(sum(r_i² / s_i², i != b))
N_i = T * r_i / sum(r_j)
```

The paper uses minimization; reversing the objective leaves the squared gaps
unchanged. Its normal posterior/APCS approximation, continuous allocation and
large-budget argument are not a finite-sample PCS guarantee. In particular, its
derivation simplifies equation (10) using `N_b >> N_i` before taking `T -> infinity`
and discarding logarithmic terms. Independent observations within and across
candidates, positive finite variances and separated means are the reference
setting. Estimated moments, integer sequential decisions, failures and fallback
rules add implementation conditions beyond that derivation.

## Known fixed costs

Chen, C.-H. and Lee, L. H., *Stochastic Simulation Optimization: An Optimal
Computing Budget Allocation*, World Scientific, ISBN 978-981-4282-64-2,
§3.5, equations (3.29)–(3.32), Theorem 3.4, printed pp.55–57, extends the budget
to positive per-replication costs. The inspected PDF has copyright 2011 and these
pages are PDF pages 74–76; bibliographic records also date the title to 2010.
[Book DOI](https://doi.org/10.1142/7437),
[inspected original book PDF](https://ndl.ethernet.edu.et/bitstream/123456789/44182/1/26.pdf).

For fixed, known `c_i > 0` in one common unit and `sum(c_i * N_i) = T`:

```text
r_i = s_i² / d_i²                                      for i != b
r_b = s_b * sqrt(sum(c_i * r_i² / (c_b * s_i²), i != b))
N_i = T * r_i / sum(c_j * r_j)
```

The nonbest **sample-count** ratio does not divide by cost. Substitution of
`T_i = c_i * N_i` changes the effective variance to `c_i * s_i²`; transforming
back gives the rule above. With two candidates it reduces to
`N_1 / N_2 = (s_1 / s_2) * sqrt(c_2 / c_1)`; with equal costs it reduces to the
classical rule. This remains an asymptotic APCS approximation, not a result for
random runtime, parallel makespan, correlated failures or finite-budget PCS.

Here costs are declared artificial credits per attempted observation. Measured
wall time is reported separately. Warmup and failed attempts consume the same
budget. `ocba` under a cost budget merely respects affordability; only
`cost_ocba` applies the unequal-cost best-balance equation.

## External implementation inspected

The established Java simulator jasima provides a real OCBA implementation and
simulation examples. The review pins
[`jasima-simulator/jasima-simcore`](https://github.com/jasima-simulator/jasima-simcore/tree/bd3ec5291207700cd26173bd7f691416119e7734)
at `bd3ec5291207700cd26173bd7f691416119e7734` (2025-03-24), rather than an
unversioned snippet. Its
[`pom.xml`](https://github.com/jasima-simulator/jasima-simcore/blob/bd3ec5291207700cd26173bd7f691416119e7734/pom.xml)
declares `io.github.jasima-simulator:jasima-main:3.0.0-RC4-SNAPSHOT`, Java 8 and
[Apache-2.0](https://github.com/jasima-simulator/jasima-simcore/blob/bd3ec5291207700cd26173bd7f691416119e7734/LICENSE).

| Boundary | Observed behavior at the pinned revision |
| --- | --- |
| Entry | `OCBAExperiment`: configure base experiment, factors, objective, problem type and seed; call `runExperiment()` |
| Allocation | Protected `ocba(int add_budget)` uses private experiment statistics, second-best-relative ratios, retained counts and integer rounding |
| Warmup | Default field `-1` becomes `max(3, availableProcessors)`; explicit setter requires at least 3. The class comment's default 5 is stale |
| Budget | `configuration_count * numReplicationsPerConfiguration` simulation runs; warmup is charged but not first clipped to this total; no per-candidate cost input |
| Output | Best configuration, means, run allocation and an internally estimated `pcs`; this is not Monte Carlo empirical PCS |

Source: [`OCBAExperiment.java`](https://github.com/jasima-simulator/jasima-simcore/blob/bd3ec5291207700cd26173bd7f691416119e7734/src/main/java/jasima/core/experiment/OCBAExperiment.java).

The outer experiment defaults to common random numbers. With CRN disabled,
`java.util.Random(initialSeed).nextLong()` supplies child seeds. The nested
`MultipleReplicationExperiment` disables CRN within its replications; the same
outer seed can nevertheless couple corresponding replications across candidates.
See [`AbstractMultiExperiment.java`](https://github.com/jasima-simulator/jasima-simcore/blob/bd3ec5291207700cd26173bd7f691416119e7734/src/main/java/jasima/core/experiment/AbstractMultiExperiment.java)
and [`MultipleReplicationExperiment.java`](https://github.com/jasima-simulator/jasima-simcore/blob/bd3ec5291207700cd26173bd7f691416119e7734/src/main/java/jasima/core/experiment/MultipleReplicationExperiment.java).
[`OCBATest.java`](https://github.com/jasima-simulator/jasima-simcore/blob/bd3ec5291207700cd26173bd7f691416119e7734/src/test/java/jasima/core/experiment/OCBATest.java)
contains a shop experiment and Gaussian examples; several repeated-trial tests
are marked `@Ignore`.

The selected mechanisms are the published relative allocation and retaining
observations already paid for. A runtime adapter is rejected for this experiment:
the Java experiment lifecycle and private statistics would require substantial
integration to preserve our evaluator, strict attempt budget and fixed-cost
semantics. That work would exceed a thin adapter. The license is not the reason
for rejection, and this decision is not a claim that jasima is generally unusable.

A separate static concern remains unverified: OCBA resets a retained replication
experiment to `INITIAL`, while its inherited `init()` resets the seed stream;
the inspected OCBA path does not advance `skipSeedCount`. This suggests possible
seed-prefix reuse across increments. No upstream Java execution was performed,
so this is a code-inspection concern, not a reproduced defect or a benchmark
result. `java`, `javac` and `jshell` were unavailable on the inspection shell's
PATH; no JVM or dependency stack was installed for this review.

## Formula cross-check and local numerical behavior

On 2026-09-20, an in-memory Python calculation evaluated the mathematical
second-best-relative expression inspected in jasima and compared its normalized
ratios with the local allocator. Each candidate started with five observations.
No third-party code was translated or executed. All three comparisons passed
an absolute tolerance of `1e-12`:

| Means (maximize) | Variances | Reference normalized sample ratios | Maximum absolute difference |
| --- | --- | --- | --- |
| `10, 8, 6` | `4, 9, 4` | `0.378216274651, 0.559605352815, 0.062178372535` | `5.56e-17` |
| `3, 2` | `1, 4` | `0.333333333333, 0.666666666667` | `0` |
| `10, 9.99, 9.5, 7` | `1, 25, 0.5, 4` | `0.166665308877, 0.833326543042, 0.000006666612, 0.000001481469` | `1.39e-16` |

This checks formula correspondence only. It does not establish equivalence with
jasima's integer batch allocations, RNG lifecycle or PCS output. The repeatable
[local tests](test_allocation.py) contain hand-derived classical and fixed-cost
targets, the equal-cost reduction, paid-observation retention, affordability and
degenerate-input cases. Monte Carlo allocation results belong to the benchmark,
not to these algebraic checks.

The local sequential scheduler retains existing observations as target lower
bounds and selects one affordable deficit at a time. It computes relative
weights in the log domain for near ties. Exact ties, zero empirical variances
and known deterministic mixtures use explicitly labeled balanced-sampling
fallbacks. An observed zero variance alone does not establish determinism.
Declared deterministic values are evaluated once and remain in final comparison.
These choices have no claimed classical OCBA guarantee.

Failure attempts remain in cost and attempt totals but do not invent numeric
observations. If failure depends on the latent value, successful-only statistics
estimate a conditional mean; they need not estimate the intended unconditional
objective. Synthetic failure fixtures therefore use a separately declared
Bernoulli draw independent of observation noise within each candidate stream. A same-seed replay verifies
reproducibility and is not an independent sample or Monte Carlo trial.

## Meaning of the benchmark

The benchmark keeps candidate generation fixed and reports empirical correct
selection frequency and regret over independent outer trials. Equal, round-robin
and classical OCBA use the same declared fixtures and budgets. Additional
heuristics and the fixed-cost rule are labeled separately. No internal APCS
estimate substitutes for empirical PCS.

The retained public massing fixture uses real production-owner/P036 creation
and readback. Its declared massing GFA is a deterministic measurement. Added
Gaussian noise and costs are artificial experimental inputs, not daylight,
structural response, CAD generality, usable area or architectural quality. Hard
invalid/unavailable candidates cannot become eligible through a favorable soft
value. Neither a favorable synthetic result nor the use of retained candidates
establishes production acceptance, a universal scalar utility or a physical
performance advantage.
