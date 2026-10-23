"""What was changed, step by step, along a line of runs: a reading of the retained changes, not a new record.

Every candidate run keeps the exact change it made (``StudioCandidateDelta@1``:
the operator and the record it was applied to). Applied again to that record,
the operator gives the run's own record, and the two records say what moved:

- an element or parameter added or removed;
- a numeric field set from one value to another, with its delta and direction
  (a wall's height, a slab's thickness, a parameter's value);
- any other field changed, named without numbers.

The log walks one run's line back to where it started, nearest first, one step
per run, and reads nothing it does not need: no projection, no model, no
write. ``by_target`` gathers the steps per element field or parameter, which
is what a rule asking "has this been made thinner three times running" reads.
What the words of a step were, when it was asked in words, travel with it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math

from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import ProjectRepositoryError
from archflow.state.state_record import (
    StateRecord, StateRecordError, StateRecordOperator, apply_state_record_operator, changed_refs,
)

from ..binding import ProjectBinding
from ..errors import StudioError

SET = "set"
ADD = "add"
REMOVE = "remove"
EDIT = "edit"

#: Two values closer than this, relative to the larger, are the same value.
_SAME = 1e-9


@dataclass(frozen=True, slots=True)
class FieldEdit:
    """One thing a step changed: an entity or parameter, and for a number, which field from what to what."""

    target_ref: str
    component_id: str | None
    op: str
    field: str | None = None
    before: float | None = None
    after: float | None = None

    @property
    def delta(self) -> float | None:
        return None if self.before is None or self.after is None else self.after - self.before

    @property
    def direction(self) -> int:
        delta = self.delta
        return 0 if delta is None or delta == 0 else (1 if delta > 0 else -1)

    def to_dict(self) -> dict:
        return {"targetRef": self.target_ref, "componentId": self.component_id, "op": self.op, "field": self.field,
                "before": self.before, "after": self.after, "delta": self.delta, "direction": self.direction}


@dataclass(frozen=True, slots=True)
class EditStep:
    """One run's change: what it continued, the operator's kind, the words it was asked in, and its edits."""

    run_id: str
    source_run_id: str
    kind: str
    request: str | None
    edits: tuple[FieldEdit, ...]

    def to_dict(self) -> dict:
        return {"runId": self.run_id, "sourceRunId": self.source_run_id, "kind": self.kind, "request": self.request,
                "edits": [edit.to_dict() for edit in self.edits]}


@dataclass(frozen=True, slots=True)
class TargetHistory:
    """One element field or parameter across the steps that changed it, nearest first.

    ``steps`` are the step positions in the log (0 is the line's head);
    ``directions`` the sign of each change there, 0 where it was not a number.
    """

    target_ref: str
    component_id: str | None
    field: str | None
    steps: tuple[int, ...]
    directions: tuple[int, ...]
    net: float | None

    def to_dict(self) -> dict:
        return {"targetRef": self.target_ref, "componentId": self.component_id, "field": self.field,
                "steps": list(self.steps), "directions": list(self.directions), "net": self.net}


def edits_between(before: StateRecord, after: StateRecord) -> tuple[FieldEdit, ...]:
    """What changed from one record to the next, per entity and parameter, numeric fields one by one."""

    edits: list[FieldEdit] = []
    old_entities = {entity.entity_id: entity for entity in before.entities}
    new_entities = {entity.entity_id: entity for entity in after.entities}
    old_parameters = {parameter.key: parameter for parameter in before.parameters}
    new_parameters = {parameter.key: parameter for parameter in after.parameters}
    for ref in changed_refs(before, after):
        kind, _, name = ref.partition(":")
        if kind == "entity":
            old, new = old_entities.get(name), new_entities.get(name)
            component = _component_of(new or old)
            if old is None or new is None:
                edits.append(FieldEdit(ref, component, ADD if old is None else REMOVE))
                continue
            if old == new:
                # Named only because a relation of it changed; the entity itself did not.
                continue
            numbers = list(_number_changes(old.fields, new.fields))
            edits.extend(FieldEdit(ref, component, SET, path, a, b) for path, a, b in numbers)
            if not numbers or _other_changes(old, new):
                edits.append(FieldEdit(ref, component, EDIT))
        elif kind == "parameter":
            old, new = old_parameters.get(name), new_parameters.get(name)
            if old is None or new is None:
                edits.append(FieldEdit(ref, None, ADD if old is None else REMOVE))
            elif _different(old.value, new.value):
                edits.append(FieldEdit(ref, None, SET, "value", float(old.value), float(new.value)))
            else:
                edits.append(FieldEdit(ref, None, EDIT))
    return tuple(edits)


def step_of(run_id: str, delta: Mapping, source: StateRecord) -> EditStep:
    """One run's retained change read against the record it was applied to."""

    operator = StateRecordOperator.from_dict(delta["operator"])
    after = apply_state_record_operator(source, operator)
    request = delta.get("request")
    return EditStep(run_id, delta["source_run_ref"]["run_id"], operator.kind.value,
                    request if isinstance(request, str) and request.strip() else None, edits_between(source, after))


def edit_log(binding: ProjectBinding, run_id: str, *, limit: int = 50) -> tuple[EditStep, ...]:
    """The changes along ``run_id``'s line, nearest first, at most ``limit`` steps.

    The line is each run's source in turn; results a run combined are their
    own lines. A run without a retained change (an imported or reference run,
    the start of a line) is where the log stops; a change that can no longer
    be read is refused rather than skipped.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise StudioError(422, "EDIT_LOG_LIMIT_INVALID", "limit is a whole number of steps from 1 to 500.")
    steps: list[EditStep] = []
    current, seen = run_id, set()
    while len(steps) < limit and current not in seen:
        seen.add(current)
        delta = binding.candidate_delta(current)
        if delta is None:
            break
        try:
            source_ref = delta.get("source_record_ref")
            payload = (binding.repository.load_json(ProjectRecordRef.from_dict(source_ref))
                       if source_ref is not None else delta["source_record"])
            step = step_of(current, delta, StateRecord.from_dict(payload))
        except (ProjectRepositoryError, StateRecordError, OSError, KeyError, TypeError, ValueError) as exc:
            raise StudioError(409, "EDIT_LOG_UNREADABLE",
                              f"The change retained by run {current} cannot be read again: {exc}") from exc
        steps.append(step)
        current = step.source_run_id
    return tuple(steps)


def by_target(steps: Sequence[EditStep]) -> tuple[TargetHistory, ...]:
    """Each element field or parameter the steps changed, with the step positions and directions, nearest first.

    Additions, removals and non-numeric edits of an entity are gathered under
    the entity itself (field None), so "added, then moved twice" stays one row.
    """

    gathered: dict[tuple[str, str | None], list] = {}
    for position, step in enumerate(steps):
        for edit in step.edits:
            row = gathered.setdefault((edit.target_ref, edit.field), [edit.component_id, [], [], 0.0, True])
            row[1].append(position)
            row[2].append(edit.direction)
            if edit.delta is None:
                row[4] = False
            else:
                row[3] += edit.delta
    return tuple(TargetHistory(target_ref, component_id, field, tuple(positions), tuple(directions),
                               net if numeric else None)
                 for (target_ref, field), (component_id, positions, directions, net, numeric) in gathered.items())


def _component_of(entity) -> str | None:
    if entity is None:
        return None
    if entity.schema == "Component@1":
        return entity.entity_id
    component = entity.fields.get("component_id") if isinstance(entity.fields, Mapping) else None
    return component if isinstance(component, str) and component else entity.parent_id


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _different(a, b) -> bool:
    return abs(a - b) > _SAME * max(1.0, abs(a), abs(b))


def _number_changes(old, new, path: str = "") -> Iterable[tuple[str, float, float]]:
    """Every numeric leaf present on both sides whose value moved, by its dotted path (list items by index)."""

    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for key in sorted(set(old) & set(new), key=str):
            yield from _number_changes(old[key], new[key], f"{path}.{key}" if path else str(key))
    elif isinstance(old, (list, tuple)) and isinstance(new, (list, tuple)) and len(old) == len(new):
        for index, (a, b) in enumerate(zip(old, new)):
            yield from _number_changes(a, b, f"{path}[{index}]")
    elif _number(old) and _number(new) and _different(old, new):
        yield path, float(old), float(new)


def _other_changes(old, new) -> bool:
    """Whether anything besides numeric leaves differs: a key, a text, a list's length, the parent, the basis."""

    def shape(value):
        if isinstance(value, Mapping):
            return {key: shape(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [shape(item) for item in value]
        return "#" if _number(value) else value

    return (shape(old.fields) != shape(new.fields) or old.parent_id != new.parent_id
            or old.schema != new.schema or old.basis_refs != new.basis_refs)


__all__ = [
    "ADD",
    "EDIT",
    "EditStep",
    "FieldEdit",
    "REMOVE",
    "SET",
    "TargetHistory",
    "by_target",
    "edit_log",
    "edits_between",
    "step_of",
]
