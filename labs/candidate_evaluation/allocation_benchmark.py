"""Frozen-candidate Monte Carlo comparison; caller supplies an output directory.

python -m labs.candidate_evaluation.allocation_benchmark --output <external-dir>
All attempted observations and updated statistics are retained in trials.jsonl.gz.
This CLI neither launches applications nor loads private project files.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import gzip
import json
from math import sqrt
from pathlib import Path
import platform
from statistics import fmean, stdev
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from archflow.contracts.canonical import canonical_digest
from .allocation import EvaluationBudgetRequest, SequentialAllocator, VERSION as ALLOCATOR_VERSION
from .benchmark import ENVELOPE, candidate, request_for
from .evaluator import ConstraintResult, EvaluationResult, MassingEvaluator
from .sampling import ControlledSampler, NoisePolicy, VERSION, estimate_from_evaluation


POLICIES = ("equal", "round_robin", "variance", "epsilon_greedy", "ocba", "cost_ocba")
TRACE_COLUMNS = ("candidate_index", "attempt_index", "value", "failure", "successful_count",
                 "mean", "variance", "standard_error", "allocation_reason")


@dataclass(frozen=True)
class Fixture:
    name: str
    sources: tuple[EvaluationResult, ...]
    noises: tuple[NoisePolicy, ...]
    description: str
    candidate_inputs: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if not self.sources or len(self.sources) != len(self.noises):
            raise ValueError("one fixed noise policy per candidate is required")
        if len({s.run.run_id for s in self.sources}) != len(self.sources):
            raise ValueError("fixture candidate IDs must be unique")

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "sources": [source.to_dict() for source in self.sources],
                "noise_policies": [asdict(noise) for noise in self.noises],
                "candidate_inputs": self.candidate_inputs}


def synthetic_fixtures() -> tuple[Fixture, ...]:
    evaluator = MassingEvaluator(ENVELOPE)
    records = tuple(candidate(f"arm-{i:02d}") for i in range(30))
    sources = tuple(evaluator.evaluate(request_for(record)) for record in records)

    def fixture(name, means, deviations, *, costs=None, failure=0., distribution="gaussian", description=""):
        n = len(means)
        noises = tuple(NoisePolicy(mean, deviation, (costs or [1.] * n)[i], failure, distribution)
                       for i, (mean, deviation) in enumerate(zip(means, deviations)))
        return Fixture(name, sources[:n], noises, description,
                       tuple(record.to_dict() for record in records[:n]))

    close = [1., .95, .7, .6, .5, .4, .3, .2, .1, 0.]
    result = [
        fixture("easy", [2. - i * .3 for i in range(10)], [.35] * 10,
                description="Ten Gaussian alternatives, clearly separated means; no architectural interpretation."),
        fixture("close_top_two", close, [.5] * 10,
                description="Top means differ by 0.05; finite-budget discrimination remains difficult."),
        fixture("heterogeneous_variance", close, [.2, 1.5, .1, .8, .3, 1.2, .2, .7, .15, .4],
                description="Known Gaussian variances differ by 225 times."),
        fixture("heterogeneous_cost", close, [.5, .8, .4, .6, .5, .3, .7, .2, .4, .5],
                costs=[20., 10., 1., 5., 1., 1., 1., 1., 1., 1.],
                description="Known fixed per-attempt costs 1 to 20; warmup and failures use the same units."),
        fixture("many_inferior", [1., .9] + [-1. - i * .05 for i in range(28)], [.5] * 30,
                description="Two close leaders and 28 inferior candidates; low budget can end during warmup."),
        fixture("deterministic_mixture", close, [0., .8, 0., .5, 0., .4, .3, .2, .5, .2],
                description="Three declared deterministic responses sampled once; zero observed variance alone is not deterministic."),
        fixture("missing_samples", close, [.5] * 10, failure=.15,
                description="Independent 15 percent missing observations consume cost; an invalid high-mean candidate is excluded."),
        fixture("heavy_tail_t3", close, [.5] * 10, distribution="student_t3",
                description="IID Student t with 3 degrees of freedom scaled to stated variance; outside Gaussian OCBA assumptions."),
    ]
    failed = replace(sources[10], constraints=(*sources[10].constraints,
                     ConstraintResult("fixture_hard_rule", "fail", "deliberate invalid candidate")))
    result[6] = replace(result[6], sources=(*result[6].sources, failed),
                        noises=(*result[6].noises, NoisePolicy(1000., 1.)),
                        candidate_inputs=(*result[6].candidate_inputs, records[10].to_dict()))
    return tuple(result)


def production_fixture(project_root: Path) -> Fixture:
    from .retained_fixture import create_public_massing_fixture
    candidates = create_public_massing_fixture(project_root)
    # One named objective, deliberately declared as this experiment's preference.
    # The complete four-objective source vectors remain in the fixture output.
    noises = tuple(NoisePolicy(
        next(m.value for m in item.result.objectives if m.objective.name == "gross_floor_area_m2") / 100.,
        .2 + (i % 4) * .1, 1. + (i % 3),
    ) for i, item in enumerate(candidates))
    return Fixture("monkeyhub_fixed_massing", tuple(item.result for item in candidates), noises,
                   "24 publicly authored massings transformed by the existing MonkeyHub option owner and reopened through P036. "
                   "Controlled Gaussian response mean = measured GFA/100, noise and costs are artificial. "
                   "Generation is frozen before allocation; no CAD/physical/design-quality claim.",
                   tuple(item.request.record.to_dict() for item in candidates))


def stream_seed(master: int, fixture: str, repetition: int, candidate_index: int) -> int:
    # Domain-separated streams across candidates/trials. Methods and budgets
    # deliberately share each candidate's stream for a paired comparison.
    return int(canonical_digest(["allocation-stream-v1", master, fixture, repetition, candidate_index])[:16], 16)


def run_trial(fixture: Fixture, *, policy: str, budget: int, budget_unit: str,
              repetition: int, master_seed: int, warmup: int = 5) -> dict:
    if type(budget) is not int or budget <= 0:
        raise ValueError("budget must be a positive integer")
    seeds = [stream_seed(master_seed, fixture.name, repetition, i) for i in range(len(fixture.sources))]
    samplers = [ControlledSampler(source, noise, seed)
                for source, noise, seed in zip(fixture.sources, fixture.noises, seeds)]
    estimates = [estimate_from_evaluation(s.evaluation(), per_sample_cost=s.policy.cost) for s in samplers]
    index_by_id = {estimate.candidate_id: i for i, estimate in enumerate(estimates)}
    trace = []
    remaining = float(budget)
    allocator = SequentialAllocator()
    started = perf_counter()
    while True:
        request = EvaluationBudgetRequest(tuple(estimates), remaining, budget_unit, policy, warmup,
                                          len(trace), stream_seed(master_seed, fixture.name, repetition, -1))
        decision = allocator.allocate(request)
        if decision.candidate_id is None:
            break
        i = index_by_id[decision.candidate_id]
        sample = samplers[i].sample()
        charged = 1. if budget_unit == "samples" else sample.cost
        if charged > remaining:
            raise AssertionError("allocator exceeded budget")
        remaining -= charged
        estimates[i] = estimate_from_evaluation(samplers[i].evaluation(), per_sample_cost=samplers[i].policy.cost)
        stats = estimates[i].statistics
        trace.append((i, sample.index, sample.value, sample.failure, stats.count,
                      stats.mean, stats.variance, stats.standard_error, decision.reason))
    eligible = [i for i, source in enumerate(fixture.sources) if source.validity == "valid"]
    sampled = [i for i in eligible if estimates[i].statistics.mean is not None]
    # Never claim a full-set selection when even one valid arm was unobserved.
    selected = min(sampled, key=lambda i: (-estimates[i].statistics.mean, estimates[i].candidate_id)) \
        if len(sampled) == len(eligible) and sampled else None
    best_mean = max(fixture.noises[i].mean for i in eligible) if eligible else None
    best = [i for i in eligible if fixture.noises[i].mean == best_mean]
    regret = None if selected is None else best_mean - fixture.noises[selected].mean
    return {
        "fixture": fixture.name, "policy": policy, "budget": budget, "budget_unit": budget_unit,
        "repetition": repetition, "master_seed": master_seed, "candidate_seeds": seeds, "warmup": warmup,
        "warmup_complete": all(estimates[i].statistics.count >= (1 if estimates[i].deterministic else max(2, warmup))
                               for i in eligible),
        "selected_candidate": None if selected is None else estimates[selected].candidate_id,
        "true_best_candidates": [estimates[i].candidate_id for i in best],
        "correct_selection": selected in best, "simple_regret": regret,
        "stop_reason": decision.stop_reason, "remaining_budget": remaining,
        "attempts": len(trace), "successful_samples": sum(s.statistics.count for s in samplers),
        "failures": sum(s.attempts - s.statistics.count for s in samplers),
        "normalized_compute_cost": sum(s.cost for s in samplers),
        "attempts_by_candidate": [s.attempts for s in samplers],
        "final_estimates": [asdict(estimate) for estimate in estimates],
        "elapsed_seconds": perf_counter() - started, "trace": trace,
    }


def wilson(successes: int, total: int) -> tuple[float, float]:
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0., center - radius), min(1., center + radius)


def aggregate(trials: list[dict]) -> dict:
    first = trials[0]
    n = len(trials)
    successes = sum(row["correct_selection"] for row in trials)
    regrets = [row["simple_regret"] for row in trials if row["simple_regret"] is not None]
    low, high = wilson(successes, n)
    return {**{key: first[key] for key in ("fixture", "policy", "budget", "budget_unit")},
            "repetitions": n, "pcs": successes / n, "pcs_ci95_low": low, "pcs_ci95_high": high,
            "mean_simple_regret": fmean(regrets) if regrets else None,
            "regret_standard_error": stdev(regrets) / sqrt(len(regrets)) if len(regrets) > 1 else None,
            "unselected_trials": n - len(regrets),
            "warmup_incomplete_trials": sum(not row["warmup_complete"] for row in trials),
            "mean_attempts": fmean(row["attempts"] for row in trials),
            "mean_successful_samples": fmean(row["successful_samples"] for row in trials),
            "mean_failures": fmean(row["failures"] for row in trials),
            "mean_compute_cost": fmean(row["normalized_compute_cost"] for row in trials),
            "mean_seconds": fmean(row["elapsed_seconds"] for row in trials),
            "mean_attempts_by_candidate": [fmean(row["attempts_by_candidate"][i] for row in trials)
                                           for i in range(len(first["attempts_by_candidate"]))]}


def _trial_job(job: tuple) -> dict:
    fixture, policy, budget, unit, repetition, seed = job
    return run_trial(fixture, policy=policy, budget=budget, budget_unit=unit,
                     repetition=repetition, master_seed=seed)


def run_benchmark(output: Path, *, repetitions: int, master_seed: int, budgets: tuple[int, ...],
                  include_production: bool = True, workers: int = 1) -> dict:
    if repetitions < 2 or not budgets or any(b <= 0 for b in budgets) or workers < 1:
        raise ValueError("at least two repetitions and positive budgets are required")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    outputs = [output / name for name in ("trials.jsonl.gz", "summary.json", "summary.csv")]
    if any(path.exists() for path in outputs):
        raise FileExistsError("choose a fresh output directory; existing benchmark results are not overwritten")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "labs/candidate_evaluation"],
                                capture_output=True, text=True, check=True).stdout.strip())
    started = datetime.now(timezone.utc).isoformat()
    with TemporaryDirectory(prefix="monkeyhub-evaluation-") as project_dir:
        fixtures = synthetic_fixtures()
        if include_production:
            fixtures = (*fixtures, production_fixture(Path(project_dir) / "project"))
        rows = []
        pool_context = ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext(None)
        with gzip.open(outputs[0], "wt", encoding="utf-8") as raw, pool_context as pool:
            for fixture in fixtures:
                units = ("samples", "cost") if fixture.name in ("heterogeneous_cost", "monkeyhub_fixed_massing") else ("samples",)
                for unit in units:
                    for nominal_budget in budgets:
                        budget = nominal_budget * (5 if unit == "cost" else 1)
                        for policy in POLICIES:
                            if policy == "cost_ocba" and unit != "cost":
                                continue
                            trials = []
                            jobs = [(fixture, policy, budget, unit, repetition, master_seed)
                                    for repetition in range(repetitions)]
                            iterator = pool.map(_trial_job, jobs, chunksize=8) if pool else map(_trial_job, jobs)
                            for trial in iterator:
                                raw.write(json.dumps(trial, allow_nan=False, separators=(",", ":")) + "\n")
                                trials.append({key: value for key, value in trial.items() if key != "trace"})
                            rows.append(aggregate(trials))
                        print(f"{fixture.name} {unit} {budget}: {repetitions} independent repetitions per policy", file=sys.stderr, flush=True)
        summary = {
            "format": "allocation-benchmark-v1", "sampler_version": VERSION,
            "allocator_version": ALLOCATOR_VERSION,
            "code_revision": revision, "lab_has_uncommitted_changes": dirty,
            "python_version": platform.python_version(), "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "repetitions": repetitions, "master_seed": master_seed, "sample_budgets": budgets,
            "workers": workers,
            "warmup_successes": 5, "trace_columns": TRACE_COLUMNS,
            "fixtures": [fixture.to_dict() for fixture in fixtures], "results": rows,
            "limitations": [
                "No physical simulation, architectural acceptance, search or candidate generation by the allocator.",
                "Cost units are known synthetic fixed per-attempt costs; wall time is measured separately.",
                "Distinct trial/candidate streams; policies and budgets share streams for paired comparisons, not extra independent trials.",
                "PCS is a Monte Carlo estimate with Wilson 95 percent interval, not the allocator's confidence guarantee.",
                "Regret is conditional on selecting; unselected trials are separately counted and PCS counts them as incorrect.",
                "Only fixed-budget stopping. Target PCS crossing can be read from aggregate intervals, not an online certificate.",
            ],
        }
    outputs[1].write_text(json.dumps(summary, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    with outputs[2].open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260920)
    parser.add_argument("--budgets", type=int, nargs="+", default=[100, 300, 900])
    parser.add_argument("--synthetic-only", action="store_true")
    parser.add_argument("--workers", type=int, default=1,
                        help="independent Monte Carlo processes; does not simulate batch allocation")
    args = parser.parse_args()
    summary = run_benchmark(args.output, repetitions=args.repetitions, master_seed=args.seed,
                            budgets=tuple(args.budgets), include_production=not args.synthetic_only, workers=args.workers)
    print(json.dumps({"rows": len(summary["results"]), "code_revision": summary["code_revision"],
                      "lab_has_uncommitted_changes": summary["lab_has_uncommitted_changes"]}))


if __name__ == "__main__":
    main()
