"""Gestures on the model, read into facts the record can vouch for.

An architect circles an area, draws an arrow, marks something to keep. The
client records the stroke, the camera and the objects under it, and derives
nothing; this module turns those hits into the record's own names through the
pick resolver (element ids are resolved, never guessed) and writes what it
found as plain sentences. The sentences go on the record sheet the agent
reads, so the agent learns "an arrow on portico-base pointing up", not "there
is an arrow in the picture" - and the deterministic parts stay deterministic:
a keep mark becomes a keep clause, a circle becomes the selection.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .intent_agent import Selection
from .pick import PickRequest, resolve_pick
from .projection import StateProjection

CIRCLE = "circle"
ARROW = "arrow"
KEEP = "keep"
REMOVE = "remove"
KINDS = (CIRCLE, ARROW, KEEP, REMOVE)

Vector = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class GestureHit:
    """One object a stroke sample fell on, as the viewer read it off the file."""

    object_name: str | None
    user_strings: Mapping[str, str]
    world: Vector


@dataclass(frozen=True, slots=True)
class Gesture:
    """One stroke, bound to the camera it was drawn in and the objects it hit."""

    kind: str
    hits: tuple[GestureHit, ...]
    world_direction: Vector | None = None
    length_model_units: float | None = None


@dataclass(frozen=True, slots=True)
class GestureReading:
    """What the gestures say, in the record's names.

    ``facts`` are the sentences for the sheet, one or more per gesture, in
    order. ``keep_refs`` are the prefixed refs keep marks resolved to.
    ``target`` is the selection a circle implies (the component holding most
    of its hits, and the element when the circle held exactly one), or None.
    """

    facts: tuple[str, ...]
    keep_refs: tuple[str, ...]
    target: Selection | None


@dataclass(frozen=True, slots=True)
class _Resolved:
    object_name: str | None
    status: str
    component_id: str | None
    element_id: str | None


def read_gestures(
    projection: StateProjection, gestures: Sequence[Gesture]
) -> GestureReading:
    facts: list[str] = []
    keep_refs: list[str] = []
    target: Selection | None = None
    for gesture in gestures:
        resolved = [_resolve(projection, hit) for hit in gesture.hits]
        if gesture.kind == CIRCLE:
            fact, implied = _circle(resolved)
            facts.append(fact)
            if target is None and implied is not None:
                target = implied
        elif gesture.kind == ARROW:
            facts.append(_arrow(resolved, gesture))
        elif gesture.kind == KEEP:
            fact, refs = _keep(resolved)
            facts.append(fact)
            keep_refs.extend(ref for ref in refs if ref not in keep_refs)
        elif gesture.kind == REMOVE:
            facts.append(_remove(resolved))
        facts.extend(_unresolved(gesture.kind, resolved))
    return GestureReading(
        facts=tuple(facts), keep_refs=tuple(keep_refs), target=target
    )


def direction_words(direction: Vector | None) -> str:
    """A unit vector as the architect would say it: the dominant world axis.

    The export is Z-up (``archflow:up_axis``), so +Z is up and -Z is down; X
    and Y are named as axes because "left" and "right" depend on where the
    camera stands, and the camera is the client's, not the record's.
    """

    if direction is None:
        return "direction unknown"
    x, y, z = direction
    magnitude = max(abs(x), abs(y), abs(z))
    if magnitude == 0:
        return "direction unknown"
    axis, value = max((("X", x), ("Y", y), ("Z", z)), key=lambda item: abs(item[1]))
    sign = "+" if value > 0 else "-"
    word = {"+Z": " (up)", "-Z": " (down)"}.get(sign + axis, "")
    return f"world direction {sign}{axis}{word} ({x:.2f}, {y:.2f}, {z:.2f})"


def _resolve(projection: StateProjection, hit: GestureHit) -> _Resolved:
    resolution = resolve_pick(
        projection,
        PickRequest(
            state_digest=projection.state_digest,
            user_strings=hit.user_strings,
            document_user_strings=None,
            object_name=hit.object_name,
        ),
    )
    return _Resolved(
        object_name=hit.object_name,
        status=resolution.status,
        component_id=resolution.component_id,
        element_id=resolution.element_id,
    )


def _named(resolved: Sequence[_Resolved]) -> list[_Resolved]:
    return [row for row in resolved if row.component_id is not None]


def _subject(row: _Resolved) -> str:
    if row.element_id is not None:
        return f"{row.element_id} ({row.component_id})"
    return f"component {row.component_id}"


def _subjects(resolved: Sequence[_Resolved]) -> list[str]:
    """Distinct subjects, most-hit first, ties in first-seen order."""

    counts: Counter[str] = Counter()
    order: list[str] = []
    for row in _named(resolved):
        subject = _subject(row)
        if subject not in counts:
            order.append(subject)
        counts[subject] += 1
    return sorted(order, key=lambda subject: (-counts[subject], order.index(subject)))


def _circle(resolved: Sequence[_Resolved]) -> tuple[str, Selection | None]:
    named = _named(resolved)
    if not named:
        return "circle over nothing the record names", None
    by_component: Counter[str] = Counter(row.component_id for row in named)  # type: ignore[misc]
    parts: list[str] = []
    for component_id, count in by_component.most_common():
        elements = sorted(
            {
                row.element_id
                for row in named
                if row.component_id == component_id and row.element_id is not None
            }
        )
        if elements:
            noun = "element" if len(elements) == 1 else "elements"
            parts.append(
                f"{component_id} ({len(elements)} {noun}: {', '.join(elements)})"
            )
        else:
            parts.append(f"{component_id} ({count} objects, no element rows)")
    top, _ = by_component.most_common(1)[0]
    top_elements = sorted(
        {
            row.element_id
            for row in named
            if row.component_id == top and row.element_id is not None
        }
    )
    selection = Selection(
        component_id=top,
        element_id=top_elements[0] if len(top_elements) == 1 else None,
    )
    return "circle covering " + " · ".join(parts), selection


def _arrow(resolved: Sequence[_Resolved], gesture: Gesture) -> str:
    subjects = _subjects(resolved)
    where = (
        "arrow on " + ", ".join(subjects)
        if subjects
        else "arrow over nothing the record names"
    )
    words = direction_words(gesture.world_direction)
    length = (
        f"length ≈ {gesture.length_model_units:.3g} model units"
        if gesture.length_model_units is not None
        else "length unknown"
    )
    return f"{where} · {words} · {length}"


def _keep(resolved: Sequence[_Resolved]) -> tuple[str, tuple[str, ...]]:
    named = _named(resolved)
    if not named:
        return "keep mark on nothing the record names", ()
    elements = sorted({row.element_id for row in named if row.element_id is not None})
    if elements:
        refs = tuple(f"entity:{element_id}" for element_id in elements)
        return (
            "keep mark on " + ", ".join(elements) + " → keep " + ", ".join(refs),
            refs,
        )
    components = sorted({row.component_id for row in named})  # type: ignore[arg-type]
    return (
        "keep mark on component "
        + ", ".join(components)
        + " · no element resolved, so the grammar cannot protect it",
        (),
    )


def _remove(resolved: Sequence[_Resolved]) -> str:
    subjects = _subjects(resolved)
    where = ", ".join(subjects) if subjects else "nothing the record names"
    return (
        f"remove mark on {where} · the grammar has no form that removes; "
        "ask before proposing"
    )


# How many unresolved object names one fact lists before it counts the rest:
# a circle over a hundred untagged objects is one sentence, not a hundred.
UNRESOLVED_NAMES_SHOWN = 3


def _unresolved(kind: str, resolved: Sequence[_Resolved]) -> list[str]:
    """One fact per resolution status for the hits the record cannot name."""

    misses = [row for row in resolved if row.component_id is None]
    if not misses:
        return []
    by_status: dict[str, list[str]] = {}
    for row in misses:
        by_status.setdefault(row.status, []).append(
            row.object_name or "an unnamed object"
        )
    facts: list[str] = []
    for status, names in by_status.items():
        if len(names) == 1:
            facts.append(f"{kind} hit on {names[0]} did not resolve ({status})")
            continue
        shown = ", ".join(names[:UNRESOLVED_NAMES_SHOWN])
        rest = len(names) - UNRESOLVED_NAMES_SHOWN
        tail = f", +{rest} more" if rest > 0 else ""
        facts.append(
            f"{kind}: {len(names)} hits did not resolve ({status}) · {shown}{tail}"
        )
    return facts
