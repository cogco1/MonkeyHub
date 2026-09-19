"""Known two-point distribution for testing statistics, never architectural quality."""

from dataclasses import dataclass
from math import isfinite
from random import Random

from archflow.contracts.canonical import canonical_json
from .evaluator import (
    EvaluationRequest, EvaluationResult, Measurement, Objective, binding_check,
    summarize, unavailable,
)


@dataclass(frozen=True)
class SyntheticEvaluator:
    """IID equal-probability values mean +/- deviation; population variance = deviation**2."""

    mean: float
    deviation: float
    sample_count: int
    seed: int

    def __post_init__(self) -> None:
        if not isfinite(self.mean) or not isfinite(self.deviation) or self.deviation < 0:
            raise ValueError("mean must be finite and deviation non-negative finite")
        if not isfinite(self.mean + self.deviation) or not isfinite(self.mean - self.deviation):
            raise ValueError("distribution endpoints must be finite")
        if type(self.sample_count) is not int or self.sample_count < 0:
            raise ValueError("sample_count must be a non-negative integer")
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")

    def samples(self) -> tuple[float, ...]:
        rng = Random(self.seed)
        return tuple(self.mean + (self.deviation if rng.getrandbits(1) else -self.deviation)
                     for _ in range(self.sample_count))

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        check = binding_check(request)
        objective = Objective("synthetic_response", "synthetic_unit", "maximize")
        if check.status != "pass":
            measured = unavailable(objective, check.reason)
        else:
            samples = self.samples()
            try:
                measured = Measurement(objective, summarize(samples), "sampling",
                                       "no samples requested" if not samples else None)
            except ValueError as exc:
                measured = unavailable(objective, f"sample statistics unavailable: {exc}")
        return EvaluationResult(
            request.expected_run, request.expected_content_digest,
            None if request.record.base is None else request.record.run_ref, request.record.digest, "synthetic-two-point-v1",
            canonical_json({"distribution": "equal_probability_two_point", "mean": self.mean,
                            "deviation": self.deviation, "sample_count": self.sample_count, "seed": self.seed}),
            request.context_refs, ("fixture:synthetic-two-point-v1",), (check,), (measured,),
            ("Synthetic response is independent of geometry; only exact binding is checked.",
             "Sampling standard error estimates IID mean precision, not model bias or architectural quality.",
             "Repeated calls with the same seed replay the same batch; independent batches need distinct seeds."),
        )
