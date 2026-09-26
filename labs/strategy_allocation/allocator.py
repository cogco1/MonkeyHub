"""The collaborator-facing allocation interface: ``AllocationState -> NextAllocation``.

A rule sees only per-strategy sample statistics of one external outcome and the
remaining budget, and answers how many *additional independent rollouts* each
strategy gets next. It never changes a rollout's token cap, horizon or model,
and it never sees a transcript, a proposal or a token count.

The V0 baselines, equal and round robin, delegate every single decision to
``SequentialAllocator`` from ``labs/candidate_evaluation`` (unchanged). This
slice adds no OCBA-style rule and claims nothing about one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import sqrt
from typing import Protocol, Sequence

from labs.candidate_evaluation.allocation import (
    VERSION as SEQUENTIAL_ALLOCATOR_VERSION,
    CandidateEstimate,
    EvaluationBudgetRequest,
    SequentialAllocator,
)
from labs.candidate_evaluation.evaluator import SampleStatistics, summarize


BASELINE_POLICIES = ("equal", "round_robin")


@dataclass(frozen=True)
class StrategyStatistics:
    """One strategy's retained outcomes: every attempted rollout, failures included, is one sample."""

    strategy_id: str
    n: int
    sample_mean: float | None
    sample_variance: float | None

    @classmethod
    def from_outcomes(cls, strategy_id: str, outcomes: Sequence[float]) -> StrategyStatistics:
        stats = summarize(outcomes)  # unbiased variance; one sample has no variance
        return cls(strategy_id, stats.count, stats.mean, stats.variance)


@dataclass(frozen=True)
class AllocationState:
    strategies: tuple[StrategyStatistics, ...]
    remaining_budget: int
    fixed_sample_cost: float = 1.0
    parallel_capacity: int = 1
    attempts_so_far: int = 0
    round_index: int = 0
    warmup: int = 2
    seed: int = 0

    def __post_init__(self) -> None:
        ids = [item.strategy_id for item in self.strategies]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("strategy ids must be present and unique")
        for value, name in ((self.remaining_budget, "remaining_budget"), (self.attempts_so_far, "attempts_so_far"),
                            (self.round_index, "round_index")):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for value, name in ((self.parallel_capacity, "parallel_capacity"), (self.warmup, "warmup")):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not self.fixed_sample_cost > 0:
            raise ValueError("fixed_sample_cost must be positive")


@dataclass(frozen=True)
class NextAllocation:
    allocations: tuple[tuple[str, int], ...]
    stopping_reason: str | None
    diagnostics: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(count for _, count in self.allocations)

    def as_dict(self) -> dict[str, int]:
        return dict(self.allocations)


class AllocationRule(Protocol):
    """The algorithm-side task: given the statistics and the remaining budget, return the next allocation."""

    name: str

    def next_allocation(self, state: AllocationState) -> NextAllocation: ...


def _estimate(item: StrategyStatistics, pending: int, cost: float) -> CandidateEstimate:
    """The SequentialAllocator input for one strategy, counting rollouts already scheduled this round.

    Equal and round robin read only counts and the attempt step, so a scheduled
    rollout is counted as if observed and its moments are placeholders. A rule
    that reads means or variances (OCBA) must batch on its own terms instead.
    """
    count = item.n + pending
    if count == 0:
        return CandidateEstimate(item.strategy_id, SampleStatistics(0, None, None, None), cost)
    mean = 0.0 if item.sample_mean is None else item.sample_mean
    if count == 1:
        return CandidateEstimate(item.strategy_id, SampleStatistics(1, mean, None, None), cost)
    variance = 0.0 if item.sample_variance is None else item.sample_variance
    return CandidateEstimate(item.strategy_id, SampleStatistics(count, mean, variance, sqrt(variance / count)), cost)


class SequentialBaseline:
    """Equal or round-robin allocation of whole rollouts, one SequentialAllocator decision per slot."""

    def __init__(self, policy: str) -> None:
        if policy not in BASELINE_POLICIES:
            raise ValueError(f"V0 baselines are {BASELINE_POLICIES}; {policy!r} is not in this slice")
        self.name = policy
        self._allocator = SequentialAllocator()

    def next_allocation(self, state: AllocationState) -> NextAllocation:
        pending: Counter[str] = Counter()
        order: list[str] = []
        notes: list[str] = []
        remaining, stop = state.remaining_budget, None
        for slot in range(state.parallel_capacity):
            request = EvaluationBudgetRequest(
                estimates=tuple(_estimate(item, pending[item.strategy_id], state.fixed_sample_cost)
                                for item in state.strategies),
                remaining_budget=remaining, budget_unit="samples", policy=self.name, warmup=state.warmup,
                step=state.attempts_so_far + slot, seed=state.seed)
            decision = self._allocator.allocate(request)
            if decision.candidate_id is None:
                stop = decision.stop_reason
                break
            if decision.candidate_id not in pending:
                order.append(decision.candidate_id)
            pending[decision.candidate_id] += 1
            remaining -= 1
            notes.append(f"{decision.candidate_id}: {decision.reason}")
        allocations = tuple((strategy_id, pending[strategy_id]) for strategy_id in order)
        return NextAllocation(allocations, None if allocations else stop,
                              (f"{SEQUENTIAL_ALLOCATOR_VERSION}/{self.name}", *notes))


RULES = {policy: (lambda policy=policy: SequentialBaseline(policy)) for policy in BASELINE_POLICIES}


def make_rule(name: str) -> AllocationRule:
    try:
        return RULES[name]()
    except KeyError:
        raise ValueError(f"unknown allocation rule {name!r}; known: {sorted(RULES)}") from None
