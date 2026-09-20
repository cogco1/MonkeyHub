"""Sequential experimental allocation of a caller-selected, higher-is-better mean.

This module never samples, accepts candidates, generates geometry or persists state.
The caller charges every attempted sample (including warmup and failures) against
the remaining budget. Statistics describe successful independent observations;
replaying a seed is not a new observation. Costs are caller-supplied known positive
costs per attempt in one common unit, not measured elapsed-time predictions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, fsum, isclose, isfinite, log, sqrt
from numbers import Real
from random import Random
from typing import Protocol

from .evaluator import SampleStatistics


VERSION = "sequential-ocba-v1"


def _finite(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return number


def _integer(value: object, name: str, *, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class CandidateEstimate:
    candidate_id: str
    statistics: SampleStatistics
    per_sample_cost: float
    validity: str = "valid"
    deterministic: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id.strip():
            raise ValueError("candidate_id must be a nonempty string")
        if self.validity not in {"valid", "invalid", "unavailable"}:
            raise ValueError("validity must be valid, invalid or unavailable")
        if not isinstance(self.deterministic, bool):
            raise ValueError("deterministic must be an explicit bool")
        if _finite(self.per_sample_cost, "per_sample_cost", minimum=0) == 0:
            raise ValueError("per_sample_cost must be strictly positive")
        stats = self.statistics
        if not isinstance(stats, SampleStatistics):
            raise ValueError("statistics must be SampleStatistics")
        _integer(stats.count, "count", minimum=0)
        if stats.count == 0:
            if any(value is not None for value in (stats.mean, stats.variance, stats.standard_error)):
                raise ValueError("zero observations require unknown mean, variance and standard_error")
            return
        _finite(stats.mean, "mean")
        if stats.variance is None:
            if stats.count != 1 or self.deterministic or stats.standard_error is not None:
                raise ValueError("only one stochastic observation may have unknown variance and standard_error")
            return
        variance = _finite(stats.variance, "variance", minimum=0)
        standard_error = _finite(stats.standard_error, "standard_error", minimum=0)
        if stats.count == 1 and not self.deterministic:
            raise ValueError("one stochastic observation cannot estimate variance")
        if self.deterministic and (variance != 0 or standard_error != 0):
            raise ValueError("deterministic observations require zero variance and standard_error")
        if not isclose(standard_error, sqrt(variance / stats.count), rel_tol=1e-9, abs_tol=1e-15):
            raise ValueError("standard_error disagrees with variance/count")


@dataclass(frozen=True)
class EvaluationBudgetRequest:
    estimates: tuple[CandidateEstimate, ...]
    remaining_budget: float
    budget_unit: str = "samples"
    policy: str = "equal"
    warmup: int = 5
    step: int = 0
    seed: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.estimates, tuple) or any(not isinstance(item, CandidateEstimate) for item in self.estimates):
            raise ValueError("estimates must be a tuple of CandidateEstimate")
        if len({item.candidate_id for item in self.estimates}) != len(self.estimates):
            raise ValueError("candidate identities must be unique")
        budget = _finite(self.remaining_budget, "remaining_budget", minimum=0)
        if self.budget_unit not in {"samples", "cost"}:
            raise ValueError("budget_unit must be samples or cost")
        if self.budget_unit == "samples" and not budget.is_integer():
            raise ValueError("sample budgets must be whole attempts")
        if self.policy not in {"equal", "round_robin", "variance", "epsilon_greedy", "ocba", "cost_ocba"}:
            raise ValueError("unsupported allocation policy")
        if self.policy == "cost_ocba" and self.budget_unit != "cost":
            raise ValueError("cost_ocba requires a cost budget")
        _integer(self.warmup, "warmup", minimum=1)
        _integer(self.step, "step", minimum=0)
        _integer(self.seed, "seed")


@dataclass(frozen=True)
class AllocationDecision:
    candidate_id: str | None
    reason: str
    details: dict[str, float] = field(default_factory=dict)
    stop_reason: str | None = None


class EvaluationBudgetAllocator(Protocol):
    def allocate(self, request: EvaluationBudgetRequest) -> AllocationDecision: ...


def _cost(request: EvaluationBudgetRequest, item: CandidateEstimate) -> float:
    return 1.0 if request.budget_unit == "samples" else float(item.per_sample_cost)


class SequentialAllocator:
    """Choose one affordable next attempt; no randomness is shared with evaluators.

    Stable lexical candidate ordering supplies deterministic tie breaks. Equal
    allocation balances successful counts; round robin rotates by attempted step.
    Repeated evaluator failures therefore consume budget even without increasing
    the successful count. A deterministic value is sampled at most once.
    """

    def allocate(self, request: EvaluationBudgetRequest) -> AllocationDecision:
        candidates = tuple(sorted((item for item in request.estimates if item.validity == "valid"),
                                  key=lambda item: item.candidate_id))
        if not candidates:
            return AllocationDecision(None, "no candidate passed the hard validity filter", stop_reason="no_eligible_candidates")
        pending = tuple(item for item in candidates if not (item.deterministic and item.statistics.count))
        if not pending:
            return AllocationDecision(None, "all valid candidates have their deterministic value", stop_reason="all_deterministic_known")
        affordable = tuple(item for item in pending if _cost(request, item) <= request.remaining_budget)
        if not affordable:
            return AllocationDecision(None, "remaining budget cannot pay any eligible attempt", stop_reason="insufficient_budget")
        warmup = tuple(item for item in affordable
                       if item.statistics.count < (1 if item.deterministic else max(2, request.warmup)))
        if warmup:
            chosen = min(warmup, key=lambda item: (item.statistics.count, item.candidate_id))
            return AllocationDecision(chosen.candidate_id, "warmup: caller charges this attempt to the same budget",
                                      {"unit_cost": _cost(request, chosen)})
        if request.policy == "equal":
            chosen = min(affordable, key=lambda item: (item.statistics.count, item.candidate_id))
            return AllocationDecision(chosen.candidate_id, "least successful sample count", {"unit_cost": _cost(request, chosen)})
        if request.policy == "round_robin":
            # Rotate over the fixed valid set, then skip known or unaffordable entries.
            cycle = candidates[request.step % len(candidates):] + candidates[:request.step % len(candidates)]
            chosen = next(item for item in cycle if item in affordable)
            return AllocationDecision(chosen.candidate_id, "stable round robin over eligible affordable candidates",
                                      {"unit_cost": _cost(request, chosen)})
        if request.policy == "variance":
            chosen = max(affordable, key=lambda item: (item.statistics.variance / item.statistics.count,
                                                       -item.statistics.count, -affordable.index(item)))
            return AllocationDecision(chosen.candidate_id, "largest estimated variance of the sample mean; heuristic",
                                      {"unit_cost": _cost(request, chosen)})
        if request.policy == "epsilon_greedy":
            # A separate deterministic stream does not draw or replay evaluator observations.
            rng = Random(f"allocator:{request.seed}:{request.step}")
            epsilon = 0.1
            explore = rng.random() < epsilon
            chosen = rng.choice(affordable) if explore else max(affordable, key=lambda item: item.statistics.mean)
            return AllocationDecision(chosen.candidate_id, "epsilon-greedy exploration" if explore else "epsilon-greedy estimated-mean exploitation",
                                      {"unit_cost": _cost(request, chosen), "exploration_probability": epsilon})
        return self._ocba(request, candidates, affordable)

    def _ocba(self, request: EvaluationBudgetRequest, candidates: tuple[CandidateEstimate, ...],
              affordable: tuple[CandidateEstimate, ...]) -> AllocationDecision:
        """Chen et al. (2000) ratios; Chen & Lee (2011), Theorem 3.4 for cost.

        These are the classical continuous/asymptotic APCS approximations under
        independent normal observations, positive variances and a unique best.
        Sequential integer choices and estimated moments carry no finite PCS
        guarantee. Cost OCBA assumes known, constant, additive attempt costs.
        """
        if len(affordable) == 1:
            chosen = affordable[0]
            return AllocationDecision(chosen.candidate_id, "only one candidate still needs an affordable sample",
                                      {"unit_cost": _cost(request, chosen)})
        # A zero empirical variance is not evidence of determinism. Instead of
        # claiming a theorem for arbitrary variance/gap floors, gather balanced
        # additional observations in cases outside the ratio's assumptions.
        observed = tuple(item for item in candidates if item.statistics.count)
        best = max(observed, key=lambda item: item.statistics.mean)
        fallback = None
        if any(item.deterministic for item in observed):
            fallback = "known deterministic mixture"
        elif any(item.statistics.variance is None or item.statistics.variance <= 0 for item in observed):
            fallback = "unknown or zero empirical variance"
        elif any(item != best and item.statistics.mean == best.statistics.mean for item in observed):
            fallback = "tied estimated best means"
        if fallback:
            chosen = min(affordable, key=lambda item: (item.statistics.count, item.candidate_id))
            return AllocationDecision(chosen.candidate_id,
                                      f"balanced additional observations: {fallback}; explicit heuristic outside classical OCBA",
                                      {"unit_cost": _cost(request, chosen)})
        logs = {}
        for item in observed:
            if item == best:
                continue
            gap = best.statistics.mean - item.statistics.mean
            # Only opposite-sign, extreme finite means can overflow subtraction.
            if isfinite(gap):
                log_gap = log(gap)
            else:
                a, b = abs(best.statistics.mean), abs(item.statistics.mean)
                high, low = max(a, b), min(a, b)
                log_gap = log(high) + log(1 + low / high)
            logs[item.candidate_id] = log(item.statistics.variance) - 2 * log_gap
        balance_terms = [2 * logs[item.candidate_id] - log(item.statistics.variance)
                         + (log(item.per_sample_cost) - log(best.per_sample_cost)
                            if request.policy == "cost_ocba" else 0.0)
                         for item in observed if item != best]
        largest = max(balance_terms)
        logs[best.candidate_id] = 0.5 * (log(best.statistics.variance) + largest
                                       + log(fsum(exp(value - largest) for value in balance_terms)))
        # Costs do NOT divide the nonbest sample ratios. They enter the best
        # balance relation above and the total cost constraint below.
        largest = max(logs[item.candidate_id] for item in affordable)
        weights = {item.candidate_id: exp(logs[item.candidate_id] - largest) for item in affordable}
        targets = _retained_targets(request, affordable, weights)
        chosen = max(affordable, key=lambda item: targets[item.candidate_id] - item.statistics.count)
        weight_sum = fsum(weights.values())
        details = {"unit_cost": _cost(request, chosen)}
        details.update({f"share:{name}": weight / weight_sum for name, weight in weights.items()})
        details.update({f"target:{name}": target for name, target in targets.items()})
        reason = ("cost-budget classical OCBA ratios; constant additive costs; sequential approximation"
                  if request.policy == "cost_ocba" else "classical OCBA sample ratios; sequential approximation")
        if request.policy == "ocba" and request.budget_unit == "cost":
            reason += "; costs constrain feasibility only, unequal-cost optimality is not claimed"
        return AllocationDecision(chosen.candidate_id, reason, details)


def _retained_targets(request: EvaluationBudgetRequest, candidates: tuple[CandidateEstimate, ...],
                      weights: dict[str, float]) -> dict[str, float]:
    """Proportional final targets retaining existing observations as lower bounds.

    Once a target would fall below an existing count, freeze that candidate and
    redistribute only the remaining cost/sample budget among the others. No
    already-used observations or failed attempts are refunded by this operation.
    """
    unit_costs = {item.candidate_id: _cost(request, item) for item in candidates}
    scale = max(unit_costs.values())
    normalized_costs = {name: cost / scale for name, cost in unit_costs.items()}
    try:
        total = fsum([request.remaining_budget / scale,
                      *(normalized_costs[item.candidate_id] * item.statistics.count for item in candidates)])
    except (OverflowError, ValueError) as exc:
        raise ValueError("allocation targets exceed finite floating-point arithmetic") from exc
    if not isfinite(total):
        raise ValueError("allocation targets exceed finite floating-point arithmetic")
    active = list(candidates)
    targets = {}
    while active:
        denominator = fsum(normalized_costs[item.candidate_id] * weights[item.candidate_id] for item in active)
        if denominator == 0:
            # Extreme positive ratios can underflow to zero after normalization.
            # Give the residual budget equally in this exceptional numerical case.
            denominator = fsum(normalized_costs[item.candidate_id] for item in active)
            trial = {item.candidate_id: total / denominator for item in active}
        else:
            trial = {item.candidate_id: (total * weights[item.candidate_id]) / denominator for item in active}
        if any(not isfinite(value) for value in trial.values()):
            raise ValueError("allocation targets exceed finite floating-point arithmetic")
        frozen = [item for item in active if trial[item.candidate_id] < item.statistics.count]
        if not frozen:
            targets.update(trial)
            break
        for item in frozen:
            targets[item.candidate_id] = float(item.statistics.count)
            total -= normalized_costs[item.candidate_id] * item.statistics.count
            active.remove(item)
    return targets
