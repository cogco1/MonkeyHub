"""The hidden reference of the V0 cases. Only ``evaluator.py`` imports this module.

For each case it states the goal a completed rollout reaches and the judgment
of each executed step (the flags below). From those and the public dynamics,
an exhaustive search of the finite state space gives the reference-optimal
number of steps to the goal from every reachable state; a step's "extra steps"
and a trajectory's distance to the optimum are measured against it.

Strategies never see any of this. Prompts are built from the snapshot and the
public dynamics alone, and ``test_isolation.py`` checks that neither the
strategy code nor any prompt reaches this module or its wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Mapping

from .environment import Case, Environment, EnvState


# @1: open-direction is complete once the open directions are compared and none
# was set aside, as its task ("one step forward") and #268 define it. @0 also
# required the architect's choice, which the visible task never asked for.
REFERENCE_VERSION = "strategy-allocation-reference@1"
MAX_STATES = 10_000

PRECONDITION_UNMET = "precondition_unmet"
HARD_CONSTRAINT_BROKEN = "hard_constraint_broken"
WRONG_TARGET = "wrong_target"
FORBIDDEN_DEPENDENCY = "forbidden_dependency"
ERRONEOUS_PRUNING = "erroneous_pruning"
FLAGS = (PRECONDITION_UNMET, HARD_CONSTRAINT_BROKEN, WRONG_TARGET, FORBIDDEN_DEPENDENCY, ERRONEOUS_PRUNING)

Judge = Callable[[EnvState, str, EnvState], frozenset[str]]


@dataclass(frozen=True)
class CaseReference:
    case_id: str
    goal_text: str
    goal: Callable[[EnvState], bool]
    judge: Judge


def _provided_judge(before: EnvState, action: str, after: EnvState) -> frozenset[str]:
    flags = set()
    if action == "BM25":
        flags.add(WRONG_TARGET)  # the index only holds superseded revisions of the governing source
    if action == "Model" and not before["read_r3"]:
        flags.add(PRECONDITION_UNMET)  # modeled before the governing passage was read
    return frozenset(flags)


def _protected_judge(before: EnvState, action: str, after: EnvState) -> frozenset[str]:
    if action == "Model" and before["opening"] == "original" and not before["dep_read"]:
        return frozenset({PRECONDITION_UNMET, FORBIDDEN_DEPENDENCY, HARD_CONSTRAINT_BROKEN})
    return frozenset()


def _direction_judge(before: EnvState, action: str, after: EnvState) -> frozenset[str]:
    if action == "CoarseModel" and before["decision"] == "none" and before["modeled"] == "none":
        return frozenset({ERRONEOUS_PRUNING})  # two valid directions dropped without the architect
    return frozenset()


def _conflict_judge(before: EnvState, action: str, after: EnvState) -> frozenset[str]:
    if action == "ReModel" and before["candidate"] == "K":
        return frozenset({WRONG_TARGET, HARD_CONSTRAINT_BROKEN})  # whole candidate for a bounded clash
    return frozenset()


REFERENCES: Mapping[str, CaseReference] = {
    reference.case_id: reference
    for reference in (
        CaseReference("provided-source", "Corridor C1 is modeled from the provided RQ r3 §4.2 (1.8 m).",
                      lambda state: state["model"] == "r3", _provided_judge),
        CaseReference("protected-dependency", "W1 is widened toward the east and R-7 holds.",
                      lambda state: state["opening"] == "widened-east" and state["r7"] == "kept",
                      _protected_judge),
        CaseReference("open-direction", "The open directions were compared and none was set aside before the "
                      "architect chose.", lambda state: state["studied"] and state["open"] == "R1,R2,R3",
                      _direction_judge),
        CaseReference("local-conflict", "C-1 is resolved locally and every other accepted element of K is "
                      "unchanged.", lambda state: state["clash"] == "resolved" and state["candidate"] == "K",
                      _conflict_judge),
    )
}


@dataclass(frozen=True)
class Transition:
    legal: bool
    after: EnvState
    flags: frozenset[str]


@dataclass(frozen=True)
class ReferenceTable:
    """Every reachable state of one case, its transitions and its reference cost to go."""

    case_id: str
    initial: EnvState
    action_ids: tuple[str, ...]
    transitions: Mapping[tuple[EnvState, str], Transition]
    cost_to_go: Mapping[EnvState, int | None]
    reference: CaseReference

    def is_goal(self, state: EnvState) -> bool:
        return self.reference.goal(state)

    def optimal_steps(self, state: EnvState | None = None) -> int | None:
        return self.cost_to_go[self.initial if state is None else state]

    def transition(self, state: EnvState, action: str) -> Transition:
        return self.transitions[(state, action)]

    def classify(self, state: EnvState, action: str) -> tuple[str, int | None]:
        """optimal / detour (clean, with extra steps) / harmful / illegal / unrecoverable."""
        base = self.cost_to_go[state]
        move = self.transitions[(state, action)]
        if not move.legal:
            return "illegal", None
        if base is None:
            return "unrecoverable", None
        after = self.cost_to_go[move.after]
        if move.flags or after is None:
            return "harmful", None
        extra = 1 + after - base
        return ("optimal", 0) if extra == 0 else ("detour", extra)

    def optimal_plan(self, state: EnvState | None = None) -> tuple[str, ...]:
        """One shortest clean plan, taking the first optimal action in the case's action order."""
        current = self.initial if state is None else state
        if self.cost_to_go[current] is None:
            return ()
        plan = []
        while not self.is_goal(current):
            action = next(action for action in self.action_ids if self.classify(current, action)[0] == "optimal")
            plan.append(action)
            current = self.transitions[(current, action)].after
        return tuple(plan)


def build_table(env: Environment, case_id: str) -> ReferenceTable:
    reference = REFERENCES[case_id]
    initial = env.initial_state(case_id)
    action_ids = env.case(case_id).action_ids
    transitions: dict[tuple[EnvState, str], Transition] = {}
    frontier, seen = [initial], {initial}
    while frontier:
        state = frontier.pop()
        for action in action_ids:
            step = env.step(state, action)
            flags = reference.judge(state, action, step.state_after) if step.legal else frozenset()
            transitions[(state, action)] = Transition(step.legal, step.state_after, flags)
            if step.state_after not in seen:
                if len(seen) >= MAX_STATES:
                    raise ValueError(f"{case_id} has more than {MAX_STATES} reachable states")
                seen.add(step.state_after)
                frontier.append(step.state_after)
    # Shortest clean path to a goal, by relaxation over the finite graph.
    cost: dict[EnvState, int | None] = {state: 0 if reference.goal(state) else None for state in seen}
    changed = True
    while changed:
        changed = False
        for (state, _), move in transitions.items():
            if not move.legal or move.flags or cost[move.after] is None:
                continue
            candidate = 1 + cost[move.after]
            if cost[state] is None or candidate < cost[state]:
                cost[state] = candidate
                changed = True
    return ReferenceTable(case_id, initial, action_ids, transitions, cost, reference)


@lru_cache(maxsize=None)
def _table(case: Case) -> ReferenceTable:
    return build_table(Environment((case,)), case.case_id)


def reference_table(env: Environment, case_id: str) -> ReferenceTable:
    """The evaluator's table for one case, built once per case definition and process."""
    return _table(env.case(case_id))
