"""Read-only GH-123 research values and a massing adapter; no project writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite, sqrt
from statistics import fmean, variance
from time import perf_counter
from typing import Literal, Mapping, Protocol, Sequence

from archflow.contracts.canonical import canonical_json
from archflow.project.refs import RunRef
from archflow.state.state_record import StateRecord, volume_boxes_of
from monkeyarch.capabilities.massing_metrics import (
    FAR_EXCEEDED,
    HEIGHT_EXCEEDED,
    VOLUME_OUTSIDE_ENVELOPE,
    envelope_check,
    massing_metrics,
)


@dataclass(frozen=True)
class Objective:
    name: str
    unit: str
    direction: Literal["minimize", "maximize"]


# Directions are an explicit experiment policy, not architectural preferences.
MASSING_OBJECTIVES = (
    Objective("footprint_m2", "m2", "minimize"),
    Objective("gross_floor_area_m2", "m2", "maximize"),
    Objective("floor_count", "count", "maximize"),
    Objective("height_m", "m", "minimize"),
)


@dataclass(frozen=True)
class SampleStatistics:
    count: int
    mean: float | None
    variance: float | None
    standard_error: float | None


def summarize(samples: Sequence[float], *, deterministic: bool = False) -> SampleStatistics:
    """Unbiased sample variance; one stochastic sample cannot estimate variance."""
    values = tuple(float(value) for value in samples)
    if any(not isfinite(value) for value in values):
        raise ValueError("samples must be finite")
    if deterministic and len(set(values)) > 1:
        raise ValueError("deterministic samples disagree")
    if not values:
        return SampleStatistics(0, None, None, None)
    try:
        mean = fmean(values)
        sample_variance = 0.0 if deterministic else variance(values) if len(values) > 1 else None
    except OverflowError as exc:
        raise ValueError("sample moments exceed the finite floating-point domain") from exc
    if not isfinite(mean) or (sample_variance is not None and not isfinite(sample_variance)):
        raise ValueError("sample moments must be finite")
    return SampleStatistics(
        len(values), mean, sample_variance,
        None if sample_variance is None else sqrt(sample_variance / len(values)),
    )


@dataclass(frozen=True)
class Measurement:
    objective: Objective
    statistics: SampleStatistics
    uncertainty_kind: Literal["deterministic", "sampling", "unknown"]
    unavailable_reason: str | None = None

    @property
    def value(self) -> float | None:
        return self.statistics.mean


@dataclass(frozen=True)
class ConstraintResult:
    name: str
    status: Literal["pass", "fail", "unavailable"]
    reason: str


@dataclass(frozen=True)
class EvaluationRequest:
    record: StateRecord
    expected_run: RunRef
    expected_content_digest: str
    context_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        self.expected_run.base.require_digest()
        digest = self.expected_content_digest
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("expected_content_digest must be a lowercase SHA-256")
        if not self.context_refs or any(not ref.strip() for ref in self.context_refs):
            raise ValueError("evaluation requires explicit context evidence refs")


def binding_check(request: EvaluationRequest) -> ConstraintResult:
    record = request.record
    if record.base is None or record.base.state_sha256 is None:
        return ConstraintResult("exact_binding", "unavailable", "record has no exact base")
    if record.run_ref != request.expected_run or record.digest != request.expected_content_digest:
        return ConstraintResult("exact_binding", "fail", "candidate content, project, run or base differs from request")
    return ConstraintResult("exact_binding", "pass", "candidate content and run match the supplied exact context")


@dataclass(frozen=True)
class EvaluationResult:
    """Requested binding plus the observed source, including on rejected requests.

    Validity describes hard checks only. Inspect each objective's value/reason
    separately before ranking; a valid binding does not supply a missing sample.
    """

    run: RunRef
    candidate_digest: str
    observed_run: RunRef | None
    observed_candidate_digest: str
    evaluator_version: str
    configuration: str  # canonical JSON of the actual policy, not another identity
    context_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    constraints: tuple[ConstraintResult, ...]
    objectives: tuple[Measurement, ...]
    limitations: tuple[str, ...]

    @property
    def validity(self) -> Literal["valid", "invalid", "unavailable"]:
        if any(check.status == "fail" for check in self.constraints):
            return "invalid"
        if not self.constraints or any(check.status == "unavailable" for check in self.constraints):
            return "unavailable"
        return "valid"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["run"] = self.run.to_dict()
        # asdict preserves incomplete observed refs, including a missing base hash.
        # Only the requested ref above is required to be a durable exact ref.
        result["validity"] = self.validity
        return result


class CandidateEvaluator(Protocol):
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


@dataclass(frozen=True)
class TimedEvaluation:
    result: EvaluationResult
    elapsed_seconds: float
    # Monetary cost is not measured by this in-process caller.
    monetary_cost: float | None = None

    @property
    def seconds_per_sample(self) -> float | None:
        """Amortized wall time including evaluator overhead, not solver-only time."""
        counts = {item.statistics.count for item in self.result.objectives}
        if len(counts) != 1 or 0 in counts:
            return None
        return self.elapsed_seconds / next(iter(counts))

    def to_dict(self) -> dict[str, object]:
        return {"result": self.result.to_dict(), "elapsed_seconds": self.elapsed_seconds,
                "seconds_per_sample": self.seconds_per_sample, "monetary_cost": self.monetary_cost}


def evaluate_timed(evaluator: CandidateEvaluator, request: EvaluationRequest) -> TimedEvaluation:
    started = perf_counter()
    result = evaluator.evaluate(request)
    return TimedEvaluation(result, perf_counter() - started)


def unavailable(objective: Objective, reason: str) -> Measurement:
    return Measurement(objective, summarize(()), "unknown", reason)


@dataclass(frozen=True)
class MassingEvaluator:
    """Adapt the existing inclusive-cell owner, requiring its complete projection.

    Whole x/z plan cells are 1 m2; y is up. Missing/partial projections are
    unavailable. This does not validate meshes, Stage locks or protected relations.
    """

    envelope: Mapping[str, object] = field(default_factory=dict)
    required_constraints: tuple[str, ...] = ("site_bounds", "height_limit", "far_limit")
    objective_names: tuple[str, ...] = tuple(item.name for item in MASSING_OBJECTIVES)

    def __post_init__(self) -> None:
        known = {"site_bounds", "height_limit", "far_limit"}
        if set(self.required_constraints) - known or len(set(self.required_constraints)) != len(self.required_constraints):
            raise ValueError("hard constraint names must be unique and supported")
        names = {item.name for item in MASSING_OBJECTIVES}
        if not self.objective_names or set(self.objective_names) - names or len(set(self.objective_names)) != len(self.objective_names):
            raise ValueError("objective names must be nonempty, unique and supported")
        if set(self.envelope) - {"min", "max", "max_height_m", "far", "site_area_m2"}:
            raise ValueError("unsupported envelope keys")
        # Snapshot caller-owned lists as tuples. Invalid numbers remain inspectable
        # evaluation failures; they never reach a comparison as NaN or Infinity.
        object.__setattr__(self, "envelope", {
            key: tuple(value) if isinstance(value, (list, tuple)) else value
            for key, value in self.envelope.items()
        })
        canonical_json(self.configuration)

    @property
    def configuration(self) -> dict[str, object]:
        return {"envelope": dict(self.envelope), "required_constraints": self.required_constraints,
                "objective_names": self.objective_names}

    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        objectives = tuple(next(item for item in MASSING_OBJECTIVES if item.name == name) for name in self.objective_names)
        binding = binding_check(request)
        checks = [binding]
        reason = None if binding.status == "pass" else binding.reason
        if reason is None and not request.record.evidence_refs:
            reason = "record has no source evidence refs"
            checks.append(ConstraintResult("source_evidence", "unavailable", reason))
        metrics = None
        if reason is None:
            try:
                for volume_id, (low, high) in volume_boxes_of(request.record).items():
                    if len(low) != 3 or len(high) != 3 or any(not isfinite(value) for value in (*low, *high)):
                        raise ValueError(f"volume {volume_id}: bounds must be finite triples")
                for level in request.record.entities_of("MassingLevel@1"):
                    if any(not isfinite(float(level.fields[key])) for key in ("base_y", "height")):
                        raise ValueError(f"level {level.entity_id}: base_y and height must be finite")
                metrics = massing_metrics(request.record)
                if metrics.honesty:
                    reason = "; ".join(metrics.honesty)
                elif any(not isfinite(float(getattr(metrics, item.name))) for item in MASSING_OBJECTIVES):
                    reason = "massing measurement is non-finite"
                elif any(level.height <= 0 for level in metrics.per_level):
                    reason = "massing level height must be positive"
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                reason = f"massing projection unavailable: {exc}"
            checks.append(ConstraintResult("massing_projection", "unavailable" if reason else "pass",
                                           reason or "complete measurable massing projection"))
        for name in self.required_constraints:
            checks.append(self._constraint(name, request.record, reason))
        measured = tuple(
            unavailable(item, reason) if reason else Measurement(
                item, summarize((float(getattr(metrics, item.name)),), deterministic=True), "deterministic"
            ) for item in objectives
        )
        return EvaluationResult(
            request.expected_run, request.expected_content_digest,
            None if request.record.base is None else request.record.run_ref, request.record.digest, "massing-v1",
            canonical_json(self.configuration), request.context_refs, request.record.evidence_refs,
            tuple(checks), measured,
            ("Validity covers only the declared checks against caller-supplied context; no HEAD lookup or acceptance.",
             "Declared massing cells/levels only; no CAD, usable-area, structural, Stage or protected-relation validation.",
             "Zero deterministic sampling variance does not quantify model error or architectural preference."),
        )

    def _constraint(self, name: str, record: StateRecord, projection_error: str | None) -> ConstraintResult:
        if projection_error:
            return ConstraintResult(name, "unavailable", projection_error)
        fields, code = {
            "site_bounds": (("min", "max"), VOLUME_OUTSIDE_ENVELOPE),
            "height_limit": (("max_height_m",), HEIGHT_EXCEEDED),
            "far_limit": (("far", "site_area_m2"), FAR_EXCEEDED),
        }[name]
        if any(self.envelope.get(key) is None for key in fields):
            return ConstraintResult(name, "unavailable", "required envelope fields: " + ", ".join(fields))
        selected = {key: self.envelope[key] for key in fields}
        try:
            if name == "site_bounds":
                low, high = selected["min"], selected["max"]
                if len(low) != 3 or len(high) != 3 or any(  # type: ignore[arg-type]
                    not isfinite(float(a)) or not isfinite(float(b)) or float(a) > float(b)
                    for a, b in zip(low, high)  # type: ignore[arg-type]
                ):
                    raise ValueError("bounds must be ordered finite triples")
            elif any(not isfinite(float(value)) or float(value) < 0 for value in selected.values()):
                raise ValueError("limits must be non-negative finite numbers")
            if name == "far_limit" and float(selected["site_area_m2"]) <= 0:
                raise ValueError("site area must be positive")
            findings = tuple(item for item in envelope_check(record, selected) if item.code == code)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return ConstraintResult(name, "unavailable", f"invalid envelope input: {exc}")
        return ConstraintResult(name, "fail" if findings else "pass",
                                "; ".join(item.detail for item in findings) if findings else "within declared envelope")


def _comparable(left: EvaluationResult, right: EvaluationResult) -> None:
    if left.validity != "valid" or right.validity != "valid":
        raise ValueError("comparison requires valid candidates; unavailable is not feasible")
    if (left.run.base, left.evaluator_version, left.configuration, left.context_refs) != (
        right.run.base, right.evaluator_version, right.configuration, right.context_refs
    ):
        raise ValueError("comparison requires the same exact base and evaluation policy/context")
    if tuple(item.objective for item in left.objectives) != tuple(item.objective for item in right.objectives):
        raise ValueError("objective vectors, units and directions differ")
    if not left.objectives or any(item.value is None or not isfinite(item.value)
                                  or item.uncertainty_kind != "deterministic"
                                  for item in (*left.objectives, *right.objectives)):
        raise ValueError("exact Pareto comparison requires complete deterministic measurements")


def dominates(left: EvaluationResult, right: EvaluationResult) -> bool:
    """Exact deterministic Pareto dominance, without utility or implicit tolerances."""
    _comparable(left, right)
    differences = [(a.value - b.value) * (1 if a.objective.direction == "maximize" else -1)
                   for a, b in zip(left.objectives, right.objectives)]
    return all(value >= 0 for value in differences) and any(value > 0 for value in differences)


def pareto_front(results: Sequence[EvaluationResult]) -> tuple[EvaluationResult, ...]:
    """Refuse invalid/missing entries rather than silently dropping failures."""
    for result in results:
        _comparable(result, results[0])
    return tuple(result for result in results if not any(dominates(other, result) for other in results))


def normalize(result: EvaluationResult, bounds: Mapping[str, tuple[float, float]]) -> dict[str, float]:
    """Caller-fixed min/max bounds, oriented higher-is-better; no clipping."""
    _comparable(result, result)
    if set(bounds) != {item.objective.name for item in result.objectives}:
        raise ValueError("normalization bounds must name the exact objective vector")
    normalized = {}
    for item in result.objectives:
        low, high = bounds[item.objective.name]
        if not isfinite(low) or not isfinite(high) or high <= low:
            raise ValueError("normalization bounds must be finite and strictly increasing")
        value = (item.value - low) / (high - low)
        normalized[item.objective.name] = value if item.objective.direction == "maximize" else 1 - value
    return normalized
