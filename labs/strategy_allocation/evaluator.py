"""The external outcome of one rollout, judged against the hidden reference and never against tokens.

``evaluate`` replays the executed actions from the snapshot with the
environment's own dynamics, so a recorded trajectory is read back rather than
trusted, then reads the hidden reference. Its inputs have no place for tokens,
wall time, tool calls or cost: those are resources, recorded beside the outcome
by ``rollout.py`` and never folded into it (no ``quality - lambda * tokens``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from .environment import Environment, Step
from .reference import (
    ERRONEOUS_PRUNING,
    FORBIDDEN_DEPENDENCY,
    HARD_CONSTRAINT_BROKEN,
    PRECONDITION_UNMET,
    REFERENCE_VERSION,
    WRONG_TARGET,
    reference_table,
)
from .state_snapshot import StateSnapshot


EVALUATOR_VERSION = "strategy-allocation-evaluator@0"
FAILURE_ORDER = ("not_completed", PRECONDITION_UNMET, HARD_CONSTRAINT_BROKEN, WRONG_TARGET, FORBIDDEN_DEPENDENCY,
                 ERRONEOUS_PRUNING)


@dataclass(frozen=True)
class StepJudgment:
    index: int
    action: str
    legal: bool
    flags: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    """External task result. Every field is about the task; none is about the budget."""

    evaluator_version: str
    reference_version: str
    case_id: str
    snapshot_digest: str
    executed_actions: tuple[str, ...]
    completed: bool
    preconditions_satisfied: bool
    hard_constraints_kept: bool
    right_target: bool
    no_forbidden_dependency: bool
    no_erroneous_pruning: bool
    illegal_actions: int
    steps_executed: int
    reference_optimal_steps: int
    excess_steps: int | None
    first_action: str | None
    first_action_class: str | None
    first_action_extra_steps: int | None
    step_judgments: tuple[StepJudgment, ...]
    success: bool
    failure_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["executed_actions"] = list(self.executed_actions)
        value["step_judgments"] = [{**asdict(item), "flags": list(item.flags)} for item in self.step_judgments]
        value["failure_reasons"] = list(self.failure_reasons)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Outcome:
        data = dict(value)
        data["executed_actions"] = tuple(data["executed_actions"])
        data["step_judgments"] = tuple(StepJudgment(item["index"], item["action"], item["legal"], tuple(item["flags"]))
                                       for item in data["step_judgments"])
        data["failure_reasons"] = tuple(data["failure_reasons"])
        return cls(**data)


def evaluate(env: Environment, snapshot: StateSnapshot, executed_actions: Sequence[str],
             recorded_steps: Sequence[Step] | None = None) -> Outcome:
    """Judge the executed actions of one rollout from ``snapshot``.

    ``recorded_steps``, when given, must be exactly what the environment does
    with these actions; a trajectory that does not read back is refused.
    """
    initial = env.state_of(snapshot)
    table = reference_table(env, snapshot.case_id)
    optimal = table.optimal_steps(initial)
    if optimal is None:
        raise ValueError(f"{snapshot.case_id}: the snapshot state has no reference path to the goal")
    judgments, state = [], initial
    for index, action in enumerate(executed_actions):
        step = env.step(state, action, index)
        if recorded_steps is not None and (index >= len(recorded_steps) or recorded_steps[index] != step):
            raise ValueError(f"recorded step {index} does not read back from the snapshot")
        move = table.transition(state, action)
        judgments.append(StepJudgment(index, action, move.legal, tuple(sorted(move.flags))))
        state = step.state_after
    if recorded_steps is not None and len(recorded_steps) != len(executed_actions):
        raise ValueError("the recorded trajectory has steps that were not executed")
    flags = {flag for item in judgments for flag in item.flags}
    completed = table.is_goal(state)
    remaining = table.optimal_steps(state)
    first_class, first_extra = (table.classify(initial, executed_actions[0]) if executed_actions else (None, None))
    reasons = tuple(reason for reason in FAILURE_ORDER
                    if (reason == "not_completed" and not completed) or reason in flags)
    return Outcome(
        evaluator_version=EVALUATOR_VERSION,
        reference_version=REFERENCE_VERSION,
        case_id=snapshot.case_id,
        snapshot_digest=snapshot.digest,
        executed_actions=tuple(executed_actions),
        completed=completed,
        preconditions_satisfied=PRECONDITION_UNMET not in flags,
        hard_constraints_kept=HARD_CONSTRAINT_BROKEN not in flags,
        right_target=WRONG_TARGET not in flags,
        no_forbidden_dependency=FORBIDDEN_DEPENDENCY not in flags,
        no_erroneous_pruning=ERRONEOUS_PRUNING not in flags,
        illegal_actions=sum(not item.legal for item in judgments),
        steps_executed=len(judgments),
        reference_optimal_steps=optimal,
        excess_steps=None if remaining is None else len(judgments) + remaining - optimal,
        first_action=executed_actions[0] if executed_actions else None,
        first_action_class=first_class,
        first_action_extra_steps=first_extra,
        step_judgments=tuple(judgments),
        success=not reasons,
        failure_reasons=reasons,
    )


# The scalar an allocator may read. Each is a function of the Outcome alone,
# so no metric here can see tokens; add a new one here rather than in a rule.
OUTCOME_METRICS: Mapping[str, Callable[[Outcome], float]] = {
    "success": lambda outcome: 1.0 if outcome.success else 0.0,
    "first_action_optimal": lambda outcome: 1.0 if outcome.first_action_class == "optimal" else 0.0,
}
