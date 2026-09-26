"""A deterministic simulator of the four finite, synthetic GH-268 V0 cases.

This module holds the *public* dynamics only: the facts a strategy may see,
which actions are legal in a state, what a legal action changes and the
observation it returns. Nothing here says which outcome is good. The hidden
reference (goals, per-step judgments, optimal plans) is ``reference.py``, and
only ``evaluator.py`` imports it.

A state is a small frozen mapping of strings and booleans, so every state is
hashable and every transition is a pure function; the same plan from the same
state always yields the same trajectory. The fixtures describe no real project:
every name, value and document is invented for the experiment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from archflow.contracts.canonical import canonical_digest

from .state_snapshot import StateSnapshot, snapshot_of


ENVIRONMENT_VERSION = "strategy-allocation-env@0"
MAX_PREVIEW_DEPTH = 2

Value = str | bool


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    description: str
    asks_human: bool = False


@dataclass(frozen=True)
class SourceDocument:
    """A synthetic source with an exact identity. Only a reading action reveals its text."""

    document_id: str
    revision: str
    text: str

    @property
    def digest(self) -> str:
        return canonical_digest({"document": self.document_id, "revision": self.revision, "text": self.text})

    def ref(self, case_id: str) -> str:
        return (f"synthetic://strategy-allocation/{case_id}/{self.document_id}@{self.revision}"
                f"#sha256={self.digest}")


@dataclass(frozen=True)
class Fact:
    fact_id: str
    text: str
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"id": self.fact_id, "text": self.text, "refs": list(self.refs)}


@dataclass(frozen=True)
class EnvState:
    """One environment state; the values are the whole state, and all of it is visible."""

    case_id: str
    values: tuple[tuple[str, Value], ...]

    @classmethod
    def of(cls, case_id: str, values: Mapping[str, Value]) -> EnvState:
        for name, value in values.items():
            if not isinstance(name, str) or not name or not isinstance(value, (str, bool)):
                raise ValueError(f"state value {name!r} must be a string or a bool")
        return cls(case_id, tuple(sorted(values.items())))

    def __getitem__(self, name: str) -> Value:
        for key, value in self.values:
            if key == name:
                return value
        raise KeyError(name)

    def as_dict(self) -> dict[str, Value]:
        return dict(self.values)

    def replace(self, changes: Mapping[str, Value]) -> EnvState:
        current = self.as_dict()
        unknown = set(changes) - set(current)
        if unknown:
            raise ValueError(f"unknown state variables {sorted(unknown)}")
        for name, value in changes.items():
            if type(value) is not type(current[name]):
                raise ValueError(f"state variable {name!r} keeps its type")
        return EnvState.of(self.case_id, {**current, **changes})

    @property
    def digest(self) -> str:
        return canonical_digest({"caseId": self.case_id, "state": self.as_dict()})


@dataclass(frozen=True)
class Step:
    index: int
    action: str
    legal: bool
    refusal: str | None
    observation: str
    state_after: EnvState

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "action": self.action, "legal": self.legal, "refusal": self.refusal,
                "observation": self.observation, "stateAfter": self.state_after.as_dict()}

    @classmethod
    def from_dict(cls, case_id: str, value: Mapping[str, object]) -> Step:
        return cls(value["index"], value["action"], value["legal"], value["refusal"], value["observation"],
                   EnvState.of(case_id, value["stateAfter"]))


@dataclass(frozen=True)
class Trajectory:
    """What the environment did with one proposal: every attempted step, in order."""

    case_id: str
    initial: EnvState
    proposed: tuple[str, ...]
    horizon: int
    steps: tuple[Step, ...]

    @property
    def final(self) -> EnvState:
        return self.steps[-1].state_after if self.steps else self.initial

    @property
    def executed(self) -> tuple[str, ...]:
        return tuple(step.action for step in self.steps)

    @property
    def truncated(self) -> bool:
        return len(self.proposed) > self.horizon


@dataclass(frozen=True)
class Case:
    """One finite case. ``refusal`` and ``effect`` are the only dynamics; both are public."""

    case_id: str
    version: str
    title: str
    task: str
    actions: tuple[ActionSpec, ...]
    initial: tuple[tuple[str, Value], ...]
    sources: tuple[SourceDocument, ...]
    facts: Callable[[EnvState], tuple[Fact, ...]]
    refusal: Callable[[EnvState, str], str | None]
    effect: Callable[[EnvState, str], tuple[Mapping[str, Value], str]]

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in self.actions)

    def source_refs(self) -> tuple[str, ...]:
        return tuple(source.ref(self.case_id) for source in self.sources)


def _always_legal(state: EnvState, action: str) -> str | None:
    return None


def _repeat(already: bool, text: str) -> str:
    return "The same passages as before; nothing new." if already else text


# --- Case 1: an authoritative source is provided ---------------------------------

_RQ_R3 = SourceDocument("requirements-RQ", "r3",
                        "§4.2 A corridor serving more than 50 occupants keeps a clear width of at least 1.8 m.")
_RQ_R1 = SourceDocument("requirements-RQ", "r1", "§4.2 A corridor keeps a clear width of at least 1.5 m.")
_RQ_R2 = SourceDocument("requirements-RQ", "r2", "§4.2 A corridor keeps a clear width of at least 1.6 m.")
_WIDTHS = {"r3": "1.8 m (from RQ r3 §4.2)", "r1": "1.5 m (from RQ r1 §4.2)",
           "unsourced": "1.2 m (the template default; no RQ passage was read)"}


def _provided_facts(state: EnvState) -> tuple[Fact, ...]:
    case = "provided-source"
    r3, r1, r2 = (source.ref(case) for source in (_RQ_R3, _RQ_R1, _RQ_R2))
    evidence = []
    if state["read_r3"]:
        evidence.append(f"RQ r3 §4.2 was read: \"{_RQ_R3.text}\"")
    if state["read_r1"]:
        evidence.append(f"RQ r1 §4.2 was read through the index: \"{_RQ_R1.text}\"")
    if state["rag_done"]:
        evidence.append("General-corpus passages were retrieved: corridor widths between 1.2 m and 2.0 m, "
                        "none of them from RQ.")
    if state["asked"]:
        evidence.append("The architect answered that the governing value is in the provided RQ r3, §4.2.")
    model = state["model"]
    return (
        Fact("source.provided", "Requirement document RQ revision r3 was provided with this task as the "
             "governing source. Its §4.2 governs corridor clear width and has "
             + ("been read." if state["read_r3"] else "not been read."), (r3,)),
        Fact("source.index", "The project's document index holds RQ revisions r1 and r2, both superseded by "
             "r3. The provided r3 is not in the index.", (r1, r2)),
        Fact("source.corpus", "A general reference corpus of other projects' documents is available for "
             "semantic retrieval."),
        Fact("evidence", " ".join(evidence) if evidence else "No evidence has been read yet."),
        Fact("candidate", "Corridor C1 has not been modeled yet." if model == "none"
             else f"Corridor C1 is modeled at a clear width of {_WIDTHS[model]}."),
    )


def _provided_effect(state: EnvState, action: str) -> tuple[Mapping[str, Value], str]:
    if action == "ReadProvidedSource":
        if state["read_r3"]:
            return {}, "RQ r3 §4.2 was already read; nothing new."
        return {"read_r3": True}, f"RQ r3 §4.2 reads: \"{_RQ_R3.text}\""
    if action == "BM25":
        return ({"read_r1": True},
                f"Best index match: RQ r1 §4.2 (superseded by r3) reads: \"{_RQ_R1.text}\"")
    if action == "RAG":
        return {"rag_done": True}, _repeat(state["rag_done"],
                                           "Similar passages from other projects' documents give corridor "
                                           "clear widths between 1.2 m and 2.0 m; none comes from RQ.")
    if action == "Model":
        model = "r3" if state["read_r3"] else "r1" if state["read_r1"] else "unsourced"
        if model == state["model"]:
            return {}, f"Corridor C1 is already modeled at {_WIDTHS[model]}; nothing changed."
        return {"model": model}, f"Corridor C1 modeled at a clear width of {_WIDTHS[model]}."
    return ({"asked": True},
            "The architect answers: the governing value is in the provided RQ r3, §4.2.")


PROVIDED_SOURCE = Case(
    case_id="provided-source",
    version="v0",
    title="An authoritative source is provided",
    task="Model corridor C1 of the candidate plan at the clear width that the governing requirement sets.",
    actions=(
        ActionSpec("ReadProvidedSource", "Read a passage of the source provided with the task, by its exact "
                   "reference."),
        ActionSpec("BM25", "Keyword search over the project's document index; returns the best-matching "
                   "passage."),
        ActionSpec("RAG", "Semantic retrieval over the general reference corpus of other projects' documents."),
        ActionSpec("Model", "Model the corridor from the evidence read so far."),
        ActionSpec("AskHuman", "Ask the architect a question and wait for the answer.", asks_human=True),
    ),
    initial=EnvState.of("provided-source", {"read_r3": False, "read_r1": False, "rag_done": False,
                                            "asked": False, "model": "none"}).values,
    sources=(_RQ_R3, _RQ_R1, _RQ_R2),
    facts=_provided_facts,
    refusal=_always_legal,
    effect=_provided_effect,
)


# --- Case 2: a protected dependency ------------------------------------------------

_R7 = SourceDocument("relation-R-7", "1",
                     "R-7 (protected): the west jamb of opening W1 stays at least 600 mm from grid line B. "
                     "It is 600 mm now, so widening W1 toward the east keeps R-7.")
_OPENINGS = {"original": "W1 is 1.2 m wide, unchanged.",
             "widened-east": "W1 is 1.5 m wide, widened toward the east.",
             "widened-centred": "W1 is 1.5 m wide, widened about its centre."}


def _protected_facts(state: EnvState) -> tuple[Fact, ...]:
    ref = _R7.ref("protected-dependency")
    relation = (f"R-7 was read: \"{_R7.text}\"" if state["dep_read"] else
                "W1 takes part in relation R-7, which is marked protected. The dependency facts of R-7 are "
                "available and have not been read.")
    check = ("R-7 holds in the current design." if state["r7"] == "kept" else
             "R-7 check after the last change: violated; the west jamb of W1 is 450 mm from grid line B.")
    facts = [
        Fact("target", "W1 is an editable opening in wall A. The requested change widens it by 300 mm."),
        Fact("relation", relation, (ref,)),
        Fact("opening", _OPENINGS[state["opening"]]),
        Fact("relation.check", check),
    ]
    if state["rag_done"]:
        facts.append(Fact("evidence", "General guidance on widening openings in walls was retrieved; it says "
                          "nothing about this project's relations."))
    return tuple(facts)


def _protected_refusal(state: EnvState, action: str) -> str | None:
    if action == "Repair" and state["r7"] != "violated":
        return "there is no reported violation to repair"
    return None


def _protected_effect(state: EnvState, action: str) -> tuple[Mapping[str, Value], str]:
    if action == "InspectDependency":
        if state["dep_read"]:
            return {}, "R-7 was already read; nothing new."
        return {"dep_read": True}, f"R-7 dependency facts: \"{_R7.text}\""
    if action == "Model":
        if state["opening"] != "original":
            return {}, "W1 is already widened; nothing changed."
        if state["dep_read"]:
            return ({"opening": "widened-east"},
                    "W1 widened by 300 mm toward the east, as R-7 requires. R-7 check: kept.")
        return ({"opening": "widened-centred", "r7": "violated"},
                "W1 widened by 300 mm about its centre. R-7 check: violated; its west jamb is now 450 mm "
                "from grid line B.")
    if action == "Repair":
        return ({"opening": "original", "r7": "kept"},
                "W1 restored to its last valid state, 1.2 m wide. R-7 check: kept.")
    return {"rag_done": True}, _repeat(state["rag_done"],
                                       "General guidance on widening openings in walls; nothing about this "
                                       "project's relations.")


PROTECTED_DEPENDENCY = Case(
    case_id="protected-dependency",
    version="v0",
    title="A protected dependency",
    task="Widen opening W1 in wall A by 300 mm.",
    actions=(
        ActionSpec("InspectDependency", "Read the dependency facts of a relation the target takes part in."),
        ActionSpec("Model", "Apply the requested change to the design."),
        ActionSpec("Repair", "Repair a reported violation by restoring the affected element to its last valid "
                   "state."),
        ActionSpec("RAG", "Semantic retrieval over the general reference corpus of other projects' documents."),
    ),
    initial=EnvState.of("protected-dependency", {"dep_read": False, "opening": "original", "r7": "kept",
                                                 "rag_done": False}).values,
    sources=(_R7,),
    facts=_protected_facts,
    refusal=_protected_refusal,
    effect=_protected_effect,
)


# --- Case 3: an undecided design direction -----------------------------------------

_BRIEF = SourceDocument("brief-roof", "1",
                        "The brief leaves the roof open between R1 north-light sawtooth, R2 planted terrace and "
                        "R3 flat roof with lanterns.")
_STUDY = ("all three are valid; R1 gives the most even daylight, R2 the lowest roof height and R3 the lowest "
          "cost.")


def _direction_facts(state: EnvState) -> tuple[Fact, ...]:
    ref = _BRIEF.ref("open-direction")
    if state["open"] == "R1,R2,R3":
        directions = ("Roof directions still open: R1 north-light sawtooth, R2 planted terrace, R3 flat roof "
                      "with lanterns. None has been ruled out.")
    else:
        directions = "Roof direction still open: R1 north-light sawtooth. R2 and R3 were set aside."
    decision = state["decision"]
    facts = [
        Fact("preconditions", "Technical preconditions for the roof are complete: site, programme and "
             "structural grid are confirmed."),
        Fact("directions", directions, (ref,)),
        Fact("default", "The template's default roof direction is R1."),
        Fact("decision", "The architect has not chosen a roof direction." if decision == "none"
             else f"The architect chose {decision}."),
        Fact("model", "No roof direction has been modeled." if state["modeled"] == "none"
             else f"A coarse model of {state['modeled']} exists."),
    ]
    if state["studied"]:
        facts.append(Fact("study", "A local study compared the open directions: " + _STUDY))
    return tuple(facts)


def _direction_effect(state: EnvState, action: str) -> tuple[Mapping[str, Value], str]:
    if action == "LocalStudy":
        if state["studied"]:
            return {}, "The local study is already done; nothing new."
        if state["open"] != "R1,R2,R3":
            return {"studied": True}, "Local study of R1 alone: valid; there is nothing left to compare it with."
        return {"studied": True}, "Local study of R1, R2 and R3: " + _STUDY
    if action == "AskHuman":
        if state["decision"] != "none":
            return {}, f"The architect confirms the earlier choice, {state['decision']}."
        if state["open"] != "R1,R2,R3":
            return {"decision": "R1"}, "Only R1 is still open; the architect keeps R1 for now."
        if not state["studied"]:
            return {}, "The architect asks for a comparison of the open directions before choosing."
        return {"decision": "R2"}, "The architect compared the local study and chose R2, the planted terrace."
    if action == "CoarseModel":
        if state["decision"] != "none":
            if state["modeled"] == state["decision"]:
                return {}, f"A coarse model of {state['decision']} already exists; nothing changed."
            return {"modeled": state["decision"]}, f"Coarse model of {state['decision']} built."
        if state["modeled"] != "none":
            return {}, "A coarse model of R1 already exists; nothing changed."
        return ({"modeled": "R1", "open": "R1"},
                "Coarse model of R1, the template default, built. Design work continues from R1; R2 and R3 "
                "are set aside.")
    direction = "undecided" if state["decision"] == "none" else state["decision"]
    return {}, f"No open technical issues. The roof direction is {direction}."


OPEN_DIRECTION = Case(
    case_id="open-direction",
    version="v0",
    title="An undecided design direction",
    task="Take the roof design one step forward. The brief leaves the roof direction open.",
    actions=(
        ActionSpec("LocalStudy", "Compare the open directions in a local study, without committing to one of "
                   "them."),
        ActionSpec("CoarseModel", "Build a coarse model of one roof direction: the architect's choice if there "
                   "is one, otherwise the template default. Design work then continues from the modeled "
                   "direction."),
        ActionSpec("AskHuman", "Ask the architect a question and wait for the answer.", asks_human=True),
        ActionSpec("Inspect", "Inspect the current design state and report open technical issues."),
    ),
    initial=EnvState.of("open-direction", {"studied": False, "decision": "none", "modeled": "none",
                                           "open": "R1,R2,R3"}).values,
    sources=(_BRIEF,),
    facts=_direction_facts,
    refusal=_always_legal,
    effect=_direction_effect,
)


# --- Case 4: a local conflict --------------------------------------------------------

_CLASH = SourceDocument("clash-report-C-1", "1",
                        "C-1: the stair S1 landing overlaps beam B3 by 150 mm at level 2. B3 is fixed; the S1 "
                        "landing height is adjustable.")


def _conflict_facts(state: EnvState) -> tuple[Fact, ...]:
    ref = _CLASH.ref("local-conflict")
    if state["candidate"] == "K":
        candidate = ("Candidate K is the accepted base. Its elements other than the clash are accepted and stay "
                     "as they are.")
    else:
        candidate = ("Candidate K was regenerated from the brief as K2. C-1 no longer occurs; 37 elements "
                     "accepted in K changed.")
    if state["clash"] == "open":
        clash = ("One clash is known: C-1, the stair S1 landing overlaps beam B3 by 150 mm at level 2. It is "
                 "bounded to level 2 and involves only S1 and B3. B3 is fixed; the S1 landing height is "
                 "adjustable.")
    elif state["candidate"] == "K":
        clash = "Clash C-1 is resolved: the S1 landing was lowered by 150 mm."
    else:
        clash = "Clash C-1 does not occur in the regenerated candidate."
    facts = [Fact("candidate", candidate), Fact("clash", clash, (ref,))]
    if state["inspected"]:
        facts.append(Fact("inspection", "C-1 was inspected: lowering the S1 landing by 150 mm clears B3 and "
                          "keeps the required headroom."))
    if state["rag_done"]:
        facts.append(Fact("evidence", "General guidance on stair and beam clearances was retrieved; it says "
                          "nothing about C-1."))
    return tuple(facts)


def _conflict_refusal(state: EnvState, action: str) -> str | None:
    if action == "Repair" and state["clash"] != "open":
        return "there is no open clash to repair"
    return None


def _conflict_effect(state: EnvState, action: str) -> tuple[Mapping[str, Value], str]:
    if action == "Repair":
        return {"clash": "resolved"}, "S1 landing lowered by 150 mm; C-1 resolved; no other element changed."
    if action == "ReModel":
        if state["candidate"] != "K":
            return {}, "The candidate was regenerated again; it is the same as K2."
        return ({"candidate": "K2", "clash": "resolved"},
                "Candidate regenerated from the brief as K2: C-1 no longer occurs; 37 elements accepted in K "
                "changed.")
    if action == "Inspect":
        if state["clash"] != "open":
            return {"inspected": True}, "No open clash."
        return ({"inspected": True},
                "C-1: the S1 landing overlaps the B3 soffit by 150 mm at level 2; lowering the landing by "
                "150 mm clears B3 and keeps the required headroom.")
    return {"rag_done": True}, _repeat(state["rag_done"],
                                       "General guidance on stair and beam clearances; nothing about C-1.")


LOCAL_CONFLICT = Case(
    case_id="local-conflict",
    version="v0",
    title="A local conflict",
    task="Resolve the known clash in candidate K.",
    actions=(
        ActionSpec("Repair", "Apply a local repair to the known clash."),
        ActionSpec("ReModel", "Regenerate the whole candidate from the brief."),
        ActionSpec("RAG", "Semantic retrieval over the general reference corpus of other projects' documents."),
        ActionSpec("Inspect", "Inspect the known clash and report its details."),
    ),
    initial=EnvState.of("local-conflict", {"clash": "open", "inspected": False, "candidate": "K",
                                           "rag_done": False}).values,
    sources=(_CLASH,),
    facts=_conflict_facts,
    refusal=_conflict_refusal,
    effect=_conflict_effect,
)


CASES: tuple[Case, ...] = (PROVIDED_SOURCE, PROTECTED_DEPENDENCY, OPEN_DIRECTION, LOCAL_CONFLICT)


class Environment:
    """The one interface every strategy sees. It holds no reference and judges nothing."""

    version = ENVIRONMENT_VERSION

    def __init__(self, cases: Iterable[Case] = CASES) -> None:
        self._cases = {case.case_id: case for case in cases}
        if not self._cases:
            raise ValueError("an environment needs at least one case")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(self._cases)

    def case(self, case_id: str) -> Case:
        try:
            return self._cases[case_id]
        except KeyError:
            raise ValueError(f"unknown case {case_id!r}; known: {sorted(self._cases)}") from None

    def initial_state(self, case_id: str) -> EnvState:
        case = self.case(case_id)
        return EnvState(case.case_id, case.initial)

    def reset(self, case_id: str) -> StateSnapshot:
        """The exact starting snapshot of a case. The same case always gives the same digest."""
        return snapshot_of(self, self.initial_state(case_id))

    def snapshot(self, state: EnvState) -> StateSnapshot:
        return snapshot_of(self, state)

    def state_of(self, snapshot: StateSnapshot) -> EnvState:
        """Rebuild the state a snapshot names, refusing a snapshot this environment would not produce."""
        state = EnvState.of(snapshot.case_id, dict(snapshot.state))
        if snapshot_of(self, state).digest != snapshot.digest:
            raise ValueError("the snapshot does not match this environment's projection of its state")
        return state

    def facts(self, state: EnvState) -> tuple[Fact, ...]:
        return self.case(state.case_id).facts(state)

    def refusal(self, state: EnvState, action: str) -> str | None:
        case = self.case(state.case_id)
        if action not in case.action_ids:
            raise ValueError(f"{action!r} is not in the action set of {case.case_id}")
        return case.refusal(state, action)

    def legal_actions(self, state: EnvState) -> tuple[str, ...]:
        return tuple(action for action in self.case(state.case_id).action_ids if self.refusal(state, action) is None)

    def step(self, state: EnvState, action: str, index: int = 0) -> Step:
        refusal = self.refusal(state, action)
        if refusal is not None:
            return Step(index, action, False, refusal, f"Refused: {refusal}.", state)
        changes, observation = self.case(state.case_id).effect(state, action)
        return Step(index, action, True, None, observation, state.replace(changes))

    def rollout(self, state: EnvState, plan: Iterable[str], *, horizon: int) -> Trajectory:
        """Execute a proposal from ``state``: at most ``horizon`` attempts; a refused one keeps the state."""
        if type(horizon) is not int or horizon < 1:
            raise ValueError("horizon must be a positive integer")
        proposed = tuple(plan)
        steps = []
        current = state
        for index, action in enumerate(proposed[:horizon]):
            step = self.step(current, action, index)
            steps.append(step)
            current = step.state_after
        return Trajectory(state.case_id, state, proposed, horizon, tuple(steps))

    def preview(self, state: EnvState, depth: int = 1) -> tuple[dict[str, object], ...]:
        """What each action would report from ``state``, from the public dynamics alone.

        A preview executes nothing: it is the observation and the changed facts a
        simulated step returns. It carries no judgment of good or bad outcomes.
        """
        if type(depth) is not int or not 1 <= depth <= MAX_PREVIEW_DEPTH:
            raise ValueError(f"preview depth must be between 1 and {MAX_PREVIEW_DEPTH}")
        before = {fact.fact_id: fact for fact in self.facts(state)}
        entries = []
        for action in self.case(state.case_id).action_ids:
            step = self.step(state, action)
            entry: dict[str, object] = {"action": action, "legal": step.legal}
            if not step.legal:
                entry["refusal"] = step.refusal
            else:
                entry["observation"] = step.observation
                entry["factsChanged"] = [fact.text for fact in self.facts(step.state_after)
                                         if before.get(fact.fact_id) != fact]
                if depth > 1:
                    entry["then"] = list(self.preview(step.state_after, depth - 1))
            entries.append(entry)
        return tuple(entries)
