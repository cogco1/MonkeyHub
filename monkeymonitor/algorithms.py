"""A pure algorithm seam: recommend one caller-owned action, never execute it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .pricing import _decimal
from .usage import _count


@dataclass(frozen=True, slots=True)
class Budget:
    """Remaining caller limits. None means the caller did not specify a limit."""

    cost_usd: str | None = None
    tokens: int | None = None
    time_ms: int | None = None

    def __post_init__(self) -> None:
        if self.cost_usd is not None:
            _decimal(self.cost_usd, "cost_usd")
        _count(self.tokens, "tokens")
        _count(self.time_ms, "time_ms")


@dataclass(frozen=True, slots=True)
class Action:
    """An available action with caller-supplied estimates, not token predictions."""

    action_id: str
    estimated_cost_usd: str | None = None
    estimated_tokens: int | None = None
    estimated_time_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or not self.action_id.strip():
            raise ValueError("action_id must be non-empty text")
        if self.estimated_cost_usd is not None:
            _decimal(self.estimated_cost_usd, "estimated_cost_usd")
        _count(self.estimated_tokens, "estimated_tokens")
        _count(self.estimated_time_ms, "estimated_time_ms")


@dataclass(frozen=True, slots=True)
class SelectionContext:
    available_actions: tuple[Action, ...]
    remaining_budget: Budget
    observations: Mapping[str, object] = field(default_factory=dict)
    objectives: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.remaining_budget, Budget):
            raise TypeError("remaining_budget must be Budget")
        if any(not isinstance(action, Action) for action in self.available_actions):
            raise TypeError("available_actions must contain Action values")
        object.__setattr__(self, "available_actions", tuple(self.available_actions))
        ids = [action.action_id for action in self.available_actions]
        if len(ids) != len(set(ids)):
            raise ValueError("available action ids must be unique")


@dataclass(frozen=True, slots=True)
class Decision:
    action_id: str | None
    reason: str


class Algorithm(Protocol):
    def choose(self, context: SelectionContext) -> Decision: ...


def _fits(action: Action, budget: Budget) -> bool:
    for estimate, limit in (
        (action.estimated_tokens, budget.tokens),
        (action.estimated_time_ms, budget.time_ms),
    ):
        if limit is not None and (estimate is None or estimate > limit):
            return False
    if budget.cost_usd is not None:
        if action.estimated_cost_usd is None:
            return False
        if _decimal(action.estimated_cost_usd, "estimated_cost_usd") > _decimal(budget.cost_usd, "cost_usd"):
            return False
    return True


def choose_action(policy: Algorithm, context: SelectionContext) -> Decision:
    """Invoke a policy and validate its recommendation against stated limits.

    Limits apply to estimates; the host must reconcile actual usage and supply
    the next remaining budget. This function reserves nothing and performs no
    model call, design acceptance or project write.
    """
    available = {action.action_id: action for action in context.available_actions}
    budget = context.remaining_budget
    decision = policy.choose(context)
    if not isinstance(decision, Decision):
        raise TypeError("algorithm must return Decision")
    if decision.action_id is None:
        return decision
    action = available.get(decision.action_id)
    if action is None:
        raise ValueError("algorithm selected an unavailable action")
    if not _fits(action, budget):
        raise ValueError("algorithm selected an action without a fitting budget estimate")
    return decision


class FirstAvailablePolicy:
    """Deterministic baseline: first affordable action in caller priority order."""

    def choose(self, context: SelectionContext) -> Decision:
        for action in context.available_actions:
            if _fits(action, context.remaining_budget):
                return Decision(action.action_id, "First available action within stated estimates")
        return Decision(None, "No available action fits the stated budget estimates")
