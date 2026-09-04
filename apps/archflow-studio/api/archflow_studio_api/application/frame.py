"""The record's frame — its levels and axes — and what changing one would move.

Every position in an ArchFlow record is a reference to a ``Level@1`` (role,
elevation) or a ``GridAxis@1`` (role, origin, direction); no element carries a
coordinate of its own. That is why one axis or one level can move a whole side
of a building, and why an architect must be able to see *what would move*
before touching one.

Nothing here decides any of it. ``StateRecord.closure`` computes what a change
reaches, over ``StateRecord.dependency_edges``; the plan reading of an axis is
``capabilities.element_reindex.axis_lines_of``'s and not a second one; what a
reference names is read by ``capabilities.reference_resolver.parse_reference``,
the record's own reader. This module arranges those answers per level and per
axis and says out loud what the record does not carry.

Read-only: nothing here writes, and neither route over it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from archflow.capabilities.element_reindex import axis_lines_of
from archflow.capabilities.reference_resolver import (
    AxisPoint,
    GridIntersection,
    GridRef,
    LevelRef,
    OffsetFrom,
    parse_reference,
)
from archflow.state.operational_state import DependencyEdge, DependencyEffect
from archflow.state.state_record import StateRecord

from ..transport.errors import StudioError

# The two effects ``StateRecord.closure`` walks. Named here so the edges this
# module reports back are the ones that actually carried the closure and not
# every edge that happens to touch it.
PROPAGATING = (
    DependencyEffect.INVALIDATES,
    DependencyEffect.REQUIRES_REVALIDATION,
)

# The flat ``Element@1`` fields that name a ``Level@1`` outright. The record's
# own reader treats these the same way (``base_level`` / ``top_level`` /
# ``sill_level``); a nested reference says which datum it means in its value,
# so its key can be anything and is not listed here.
LEVEL_FIELDS = ("base_level", "top_level", "sill_level")


@dataclass(frozen=True, slots=True)
class FrameLevel:
    """One ``Level@1``: what it is, what sits on it, and what it would move."""

    level_id: str
    role: str
    elevation: float
    elements_on: tuple[str, ...]
    closure: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrameAxis:
    """One ``GridAxis@1``, read as a plan line where it is one.

    ``const`` and ``value`` are ``axis_lines_of``'s reading: an axis whose
    direction is parallel to neither world axis is a line this studio cannot
    put a single number on, and both are ``None`` rather than a guess.
    ``origin`` and ``direction`` travel regardless, so what the record declares
    is on the wire either way.
    """

    axis_id: str
    role: str
    const: str | None
    value: float | None
    origin: tuple[float, float, float]
    direction: tuple[float, float, float]
    elements_on: tuple[str, ...]
    closure: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecordFrame:
    """The frame of one record: its levels, its axes, and what it cannot say."""

    levels: tuple[FrameLevel, ...]
    axes: tuple[FrameAxis, ...]
    honesty: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClosureAnswer:
    """One closure and the edges that carried it."""

    closure: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]


def frame_of(record: StateRecord) -> RecordFrame:
    """The record's levels and axes, each with what stands on it and its closure."""

    on_level, on_axis_role = _membership(record)
    lines = {line.axis_id: line for line in axis_lines_of(record)}
    levels = tuple(
        FrameLevel(
            level_id=entity.entity_id,
            role=str(entity.fields["role"]),
            elevation=float(entity.fields["elevation"]),
            elements_on=tuple(sorted(on_level.get(entity.entity_id, ()))),
            closure=record.closure((f"entity:{entity.entity_id}",)),
        )
        for entity in record.entities_of("Level@1")
    )
    axes = tuple(
        _axis(record, entity, lines.get(entity.entity_id), on_axis_role)
        for entity in record.entities_of("GridAxis@1")
    )
    return RecordFrame(levels, axes, _honesty(levels, axes))


def closure_of_refs(
    record: StateRecord,
    changed_refs: tuple[str, ...],
) -> ClosureAnswer:
    """What changing ``changed_refs`` would reach, and the edges it travelled.

    The closure is the kernel's, unedited — it includes the changed refs
    themselves, because a ref is trivially downstream of itself and hiding
    that would make the answer smaller than the record's own. The edges are
    the propagating ones whose two ends both lie inside it: the subgraph the
    walk actually used, not every edge that touches the result.
    """

    _require_known(record, changed_refs)
    closure = record.closure(tuple(sorted(set(changed_refs))))
    inside = set(closure)
    edges = tuple(
        edge
        for edge in record.dependency_edges()
        if edge.effect in PROPAGATING
        and edge.upstream_ref in inside
        and edge.downstream_ref in inside
    )
    return ClosureAnswer(closure, edges)


def _axis(
    record: StateRecord,
    entity: Any,
    line: Any,
    on_axis_role: Mapping[str, set[str]],
) -> FrameAxis:
    role = str(entity.fields["role"])
    return FrameAxis(
        axis_id=entity.entity_id,
        role=role,
        const=None if line is None else line.const,
        value=None if line is None else float(line.value),
        origin=_triple(entity.fields["origin"]),
        direction=_triple(entity.fields["direction"]),
        # An axis is named by its *role*, never by its entity id: that is how
        # ``{"grid": ["A", "front"]}`` reaches this row.
        elements_on=tuple(sorted(on_axis_role.get(role, ()))),
        closure=record.closure((f"entity:{entity.entity_id}",)),
    )


def _membership(
    record: StateRecord,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Which elements name which level, and which name which grid axis role."""

    on_level: dict[str, set[str]] = {}
    on_axis_role: dict[str, set[str]] = {}
    for element in record.entities_of("Element@1"):
        for field in LEVEL_FIELDS:
            named = element.fields.get(field)
            if isinstance(named, str):
                on_level.setdefault(named, set()).add(element.entity_id)
        references = element.fields.get("references") or {}
        if not isinstance(references, Mapping):
            continue
        for value in references.values():
            for level_id in _levels_named(value):
                on_level.setdefault(level_id, set()).add(element.entity_id)
            for role in _axis_roles_named(value):
                on_axis_role.setdefault(role, set()).add(element.entity_id)
    return on_level, on_axis_role


def _read(value: object) -> object | None:
    """One reference in the record's own reading, or ``None`` if it is not one.

    A reference the resolver does not know is not an error here: ``{"datum":
    "<element>-top"}`` names another element's published datum, and this
    module is asking about levels and axes only. It is skipped, not refused.
    """

    try:
        return parse_reference(value)
    except (ValueError, KeyError, TypeError):
        return None


def _levels_named(value: object) -> Iterable[str]:
    reference = _read(value)
    if isinstance(reference, LevelRef):
        return (reference.level,)
    if isinstance(reference, OffsetFrom):
        return (reference.level,)
    return ()


def _axis_roles_named(value: object) -> Iterable[str]:
    reference = _read(value)
    if isinstance(reference, GridRef):
        return (reference.axis,)
    if isinstance(reference, GridIntersection):
        return (reference.axis_a, reference.axis_b)
    if isinstance(reference, AxisPoint):
        return (reference.axis,)
    return ()


def _triple(value: object) -> tuple[float, float, float]:
    numbers = tuple(float(item) for item in value)  # type: ignore[union-attr]
    if len(numbers) != 3:
        raise StudioError(
            422,
            "STATE_RECORD_INVALID",
            f"a grid axis origin and direction are three numbers, got {value!r}",
        )
    return numbers  # type: ignore[return-value]


def _require_known(record: StateRecord, changed_refs: tuple[str, ...]) -> None:
    """Refuse a ref the record does not carry rather than answer about it.

    ``StateRecord.closure`` seeds itself with whatever it is given, so an id
    with a typo in it comes back as its own one-item closure — which reads as
    "this exists and nothing depends on it" and is a different, far more
    confident statement than "there is no such thing here".
    """

    if not changed_refs:
        raise StudioError(
            422,
            "UNKNOWN_REF",
            "changedRefs must name at least one ref of the record",
        )
    entities = {entity.entity_id for entity in record.entities}
    parameters = {parameter.key for parameter in record.parameters}
    unknown = [
        ref
        for ref in changed_refs
        if not (
            (ref.startswith("entity:") and ref[len("entity:"):] in entities)
            or (
                ref.startswith("parameter:")
                and ref[len("parameter:"):] in parameters
            )
        )
    ]
    if unknown:
        raise StudioError(
            422,
            "UNKNOWN_REF",
            "the record carries no "
            + ", ".join(sorted(unknown))
            + ". A ref is entity:<entityId> or parameter:<key>, and it must "
            "name something GET /api/state already showed you.",
        )


def _honesty(
    levels: tuple[FrameLevel, ...],
    axes: tuple[FrameAxis, ...],
) -> tuple[str, ...]:
    """What this frame cannot tell you, in the record's own terms."""

    lines: list[str] = []
    if not levels:
        lines.append(
            "no Level@1 in the record: nothing here places an element in "
            "elevation"
        )
    if not axes:
        lines.append(
            "no GridAxis@1 in the record: nothing here places an element in "
            "plan"
        )
    for axis in axes:
        if axis.const is None:
            lines.append(
                f"axis {axis.axis_id} ({axis.role}) is parallel to neither "
                "world axis: it has no single constant this panel can show"
            )
    if axes and not any(axis.elements_on for axis in axes):
        lines.append(
            "no element references a grid axis role: the axes are declared "
            "and nothing is placed against them"
        )
    return tuple(lines)
