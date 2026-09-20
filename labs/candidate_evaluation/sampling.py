"""Controlled IID observations over frozen GH-123 results, not a physical solver.

The sampler owns observations and their statistics. Allocation sees only the
existing SampleStatistics value; neither layer generates or accepts candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from numbers import Real
from random import Random

from archflow.contracts.canonical import canonical_json
from .evaluator import EvaluationResult, Measurement, Objective, SampleStatistics, summarize


VERSION = "controlled-iid-v1"
OBJECTIVE = Objective("controlled_response", "experiment_utility", "maximize")


@dataclass(frozen=True)
class NoisePolicy:
    mean: float
    deviation: float
    cost: float = 1.0
    failure_probability: float = 0.0
    distribution: str = "gaussian"

    def __post_init__(self) -> None:
        if not all(isinstance(x, Real) and not isinstance(x, bool) and isfinite(x)
                   for x in (self.mean, self.deviation, self.cost, self.failure_probability)):
            raise ValueError("noise policy must be finite")
        if self.deviation < 0 or self.cost <= 0 or not 0 <= self.failure_probability < 1:
            raise ValueError("invalid deviation, cost or failure probability")
        if self.distribution not in ("gaussian", "student_t3"):
            raise ValueError("unsupported controlled distribution")
        if not isfinite(self.deviation * self.deviation):
            raise ValueError("population variance must be representable")


@dataclass(frozen=True)
class Observation:
    candidate_id: str
    index: int
    value: float | None
    cost: float
    failure: str | None


class ControlledSampler:
    """One independent stream per trial/candidate; seed replay is not more data.

    Failed attempts consume declared cost and an index, but are absent from
    statistical moments. The missingness fixture is independent of the value.
    Version/configuration and source hard checks remain fixed for this stream.
    """

    def __init__(self, source: EvaluationResult, policy: NoisePolicy, seed: int):
        if type(seed) is not int:
            raise ValueError("seed must be integer")
        self.source = source
        self.policy = policy
        self.seed = seed
        self._rng = Random(seed)
        self._values: list[float] = []
        self._attempts = 0
        self._cost = 0.0
        self._statistics = summarize(())
        self._configuration = canonical_json({
            "source_evaluator": source.evaluator_version,
            "source_configuration": source.configuration,
            "distribution": policy.distribution, "mean": policy.mean,
            "deviation": policy.deviation, "declared_cost_units_per_attempt": policy.cost,
            "failure_probability": policy.failure_probability,
            "seed": seed, "utility_policy": "caller-fixed-controlled-response-v1",
        })

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def cost(self) -> float:
        return self._cost

    @property
    def statistics(self) -> SampleStatistics:
        return self._statistics

    def sample(self) -> Observation:
        if self.source.validity != "valid":
            raise ValueError("invalid or unavailable source cannot receive soft samples")
        if self.policy.deviation == 0 and self._values:
            raise ValueError("known deterministic values must not be resampled")
        self._attempts += 1
        self._cost += self.policy.cost
        failed = self._rng.random() < self.policy.failure_probability
        # Draw even on failure: missingness does not shift the latent value stream.
        if self.policy.distribution == "gaussian":
            noise = self._rng.gauss(0.0, 1.0)
        else:
            # Z/sqrt(chi-square_3) has variance 1 (t_3 / sqrt(3)).
            chi_square = sum(self._rng.gauss(0.0, 1.0) ** 2 for _ in range(3))
            noise = self._rng.gauss(0.0, 1.0) / chi_square ** 0.5
        value = self.policy.mean + self.policy.deviation * noise
        failure = "controlled_missing_observation" if failed else None
        if not isfinite(value):
            failure = "nonfinite_observation"
        if failure is None:
            try:
                stats = summarize((*self._values, value), deterministic=self.policy.deviation == 0)
            except ValueError:
                failure = "unrepresentable_statistics"
            else:
                self._values.append(value)
                self._statistics = stats
        return Observation(self.source.run.run_id, self._attempts,
                           value if failure is None else None, self.policy.cost, failure)

    def evaluation(self) -> EvaluationResult:
        measured = Measurement(OBJECTIVE, self._statistics,
                               "deterministic" if self.policy.deviation == 0 else "sampling",
                               None if self._values else "no successful samples")
        return replace(self.source, evaluator_version=VERSION, configuration=self._configuration,
                       objectives=(measured,), limitations=(*self.source.limitations,
                       "Controlled artificial noise; no daylight, structure, preference or physical calibration.",
                       "Declared cost units are experiment costs, not measured time or money."))


def estimate_from_evaluation(result: EvaluationResult, *, per_sample_cost: float):
    """The allocator adapter consumes only the existing inspectable result fields."""
    from .allocation import CandidateEstimate
    if len(result.objectives) != 1 or result.objectives[0].objective != OBJECTIVE:
        raise ValueError("allocator experiment requires its explicitly named scalar response")
    measurement = result.objectives[0]
    return CandidateEstimate(result.run.run_id, measurement.statistics, per_sample_cost,
                             result.validity, measurement.uncertainty_kind == "deterministic")
