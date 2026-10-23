"""Marks on a page read into the record's components: which ones each mark means, and when it is unclear.

An architect circles part of a drawing on Board and writes a sentence. Board
feedback already carries every mark onto its page as ``DocumentGesture`` ink
(page fractions, x right and y down); this module says which of the record's
components and elements that ink lies on, so a change list can name them
instead of the agent guessing from a picture.

What lies under a mark is measured on the page as drawn
(``monkeydiagram.rendering.marks``): a drawing's own object-named marks, or a
model view's object-ID image. A circle, a closed polyline and a freehand
stroke that returns to its start enclose an area; another line covers the ink
within its pen's reach; an arrow points with its tip. Each object is then
answered for by the record (``subjects_of``: the component its drawing names,
and the element among that component's own rows), and two numbers score each
component or element:

- ``coverage``: how much of it within the mark's frame (the smallest
  rectangle round the mark) lies inside the mark. A layer running through a
  circle counts as circled although it continues far beyond it; an object
  whose corner the circle grazes does not.
- ``share``: how much of what is inside the mark is its own (for an arrow, how
  much nearer the tip it is than the others).

A subject circled for most of it within the frame, or holding most of the
mark, is a target; one the mark half covers is borderline. A mark with no
target, or with a borderline subject beside its targets, is ambiguous and says
why: nothing is drawn there, what is drawn names no component of this record,
it only touches several things, or something else is half in it. Which of
several things the architect meant is their question to answer, not this
module's. No model call and no write; the same page and marks give the same
reading.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from monkeydiagram.rendering.marks import (
    FREEHAND, OUTLINE, POINT_ZONE, STROKE, TIP, MarkInk, ObjectInk, id_image_under_marks, ink_under_mark,
)

from .gestures import ARROW, CIRCLE, POLYLINE, TEXT, DocumentGesture
from .pick import element_of_object
from .projection import StateProjection

#: Coverage at or above which a subject counts as circled.
ENCLOSED = 0.6
#: Coverage at or above which a subject not circled is borderline, when it also holds BORDERLINE_SHARE of the mark.
BORDERLINE = 0.3
BORDERLINE_SHARE = 0.2
#: Share of a mark at or above which a subject counts as what the mark is on.
DOMINANT = 0.6
#: An open stroke reaches this many of its own widths, and at least MIN_REACH of the page's shorter side.
STROKE_REACH = 3.0
MIN_REACH = 0.01
#: An arrow's tip reaches twice as far as a stroke: it points near a thing, not onto its line.
POINT_REACH = 2.0

NOTHING = "nothing"
UNBOUND = "unbound"
PARTIAL = "partial"
BORDERLINE_HIT = "borderline"


@dataclass(frozen=True, slots=True)
class Subject:
    """What a drawn object stands for in the record: its component, and its element when one row produced it."""

    component_id: str
    element_id: str | None = None


#: An object id and the component its drawing names (None when it names none) to the record's subject, or None
#: when the record does not vouch for it.
Identify = Callable[[str, "str | None"], "Subject | None"]


@dataclass(frozen=True, slots=True)
class MarkHit:
    """One subject a mark lies on; ``target`` when the mark clearly means it, ``borderline`` when it may."""

    component_id: str
    element_id: str | None
    object_ids: tuple[str, ...]
    coverage: float
    share: float
    target: bool
    borderline: bool = False

    def to_dict(self) -> dict:
        return {"componentId": self.component_id, "elementId": self.element_id, "objectIds": list(self.object_ids),
                "coverage": self.coverage, "share": self.share, "target": self.target, "borderline": self.borderline}


@dataclass(frozen=True, slots=True)
class MarkReading:
    """One mark's subjects, targets first, and why it needs the architect to say what they meant, if it does."""

    mark_id: str
    kind: str
    zone: str
    hits: tuple[MarkHit, ...]
    reason: str | None

    @property
    def targets(self) -> tuple[MarkHit, ...]:
        return tuple(hit for hit in self.hits if hit.target)

    @property
    def ambiguous(self) -> bool:
        return self.reason is not None

    def to_dict(self) -> dict:
        return {"markId": self.mark_id, "kind": self.kind, "zone": self.zone,
                "hits": [hit.to_dict() for hit in self.hits], "ambiguous": self.ambiguous, "reason": self.reason}


def subjects_of(projection: StateProjection) -> Identify:
    """The record's answer for a drawn object: its named component when the record declares it, and the element.

    The same rule a click is answered by (``pick``): an object is vouched for
    only through the component its export named, and its element only among
    that component's own rows; a name alone never binds it.
    """

    components = frozenset(entity.entity_id for entity in projection.record.entities_of("Component@1"))

    def identify(object_id: str, component: str | None) -> Subject | None:
        if component is None or component not in components:
            return None
        return Subject(component, element_of_object(projection, object_id, component))

    return identify


def read_marks(gestures: Sequence[DocumentGesture], ink: Sequence[ObjectInk], page_size: tuple[float, float],
               identify: Identify) -> tuple[MarkReading, ...]:
    """Each mark's subjects on a drawn page of ``page_size`` (the ink's units); text is the sentence, not a mark."""

    width, height = _page(page_size)
    readings = []
    for gesture in _marks(gestures):
        points, tool, reach = _placed(gesture, width, height)
        readings.append(_reading(gesture, ink_under_mark(points, tool, reach, ink), identify))
    return tuple(readings)


def read_id_image(gestures: Sequence[DocumentGesture], id_png: bytes, legend: Sequence[Mapping],
                  object_components: Mapping[str, str], identify: Identify) -> tuple[MarkReading, ...]:
    """Each mark's subjects on a model view's object-ID image, the page being the image.

    ``legend`` and ``object_components`` are the camera pass's legend and each
    object's component as its export named it; a colour standing for a layer
    or a material counts as drawn but answers for no subject.
    """

    from PIL import Image
    from io import BytesIO

    with Image.open(BytesIO(id_png)) as image:
        width, height = image.size
    marks = _marks(gestures)
    found = id_image_under_marks([_placed(gesture, float(width), float(height)) for gesture in marks],
                                 id_png, legend, object_components)
    return tuple(_reading(gesture, ink, identify) for gesture, ink in zip(marks, found))


def _page(page_size: tuple[float, float]) -> tuple[float, float]:
    width, height = page_size
    if not (width > 0 and height > 0):
        raise ValueError("a page has a positive width and height")
    return float(width), float(height)


def _marks(gestures: Sequence[DocumentGesture]) -> list[DocumentGesture]:
    return [gesture for gesture in gestures if gesture.kind != TEXT]


def _placed(gesture: DocumentGesture, width: float, height: float):
    """The mark's points in page units, the tool its kind draws with, and its pen's reach."""

    points = [(x * width, y * height) for x, y in gesture.points]
    reach = max(STROKE_REACH * gesture.line_width, MIN_REACH) * min(width, height)
    if gesture.kind == ARROW:
        return points, TIP, POINT_REACH * reach
    if gesture.kind == CIRCLE or (gesture.kind == POLYLINE and gesture.closed):
        return points, OUTLINE, reach
    return points, FREEHAND if gesture.kind == "freehand" else STROKE, reach


def _reading(gesture: DocumentGesture, ink: MarkInk, identify: Identify) -> MarkReading:
    """The ink under one mark gathered per subject, scored, and the targets chosen.

    An arrow scores by nearness only and chooses at most the one subject most
    of the weight is on. Any other mark also takes every subject circled for
    ENCLOSED of it within the mark's frame; a subject's coverage is by the
    area it bounds when it bounds any inside the mark, else by its lines.
    """

    if not ink.objects:
        return MarkReading(gesture.id, gesture.kind, ink.zone, (), NOTHING)
    pointing = ink.zone == POINT_ZONE
    line_in_all = sum(row.line_in for row in ink.objects)
    nearness_all = sum(row.nearness for row in ink.objects)
    gathered: dict[Subject, list] = {}
    for row in ink.objects:
        subject = None if row.object_id is None else identify(row.object_id, row.component)
        if subject is None:
            continue
        sums = gathered.setdefault(subject, [[], 0.0, 0.0, 0.0, 0.0, 0.0])
        sums[0].append(row.object_id)
        for index, value in enumerate((row.line_in, row.line_framed, row.area_in, row.area_framed, row.nearness), 1):
            sums[index] += value
    if not gathered:
        return MarkReading(gesture.id, gesture.kind, ink.zone, (), UNBOUND)
    scored = []
    for subject, (object_ids, line_in, line_framed, area_in, area_framed, nearness) in gathered.items():
        if area_in > 0:
            coverage = area_in / area_framed if area_framed else 1.0
        else:
            coverage = line_in / line_framed if line_framed else 0.0
        if pointing:
            share = nearness / nearness_all if nearness_all else 0.0
        else:
            share = max(line_in / line_in_all if line_in_all else 0.0,
                        area_in / ink.zone_area if ink.zone_area else 0.0)
        scored.append((subject, tuple(sorted(object_ids)), round(min(coverage, 1.0), 4), round(min(share, 1.0), 4)))
    scored.sort(key=lambda row: (-row[3], -row[2], row[0].component_id, row[0].element_id or ""))
    top = scored[0][0] if scored[0][3] >= DOMINANT else None
    hits = []
    for subject, object_ids, coverage, share in scored:
        target = subject == top or (not pointing and coverage >= ENCLOSED)
        borderline = not target and not pointing and coverage >= BORDERLINE and share >= BORDERLINE_SHARE
        hits.append(MarkHit(subject.component_id, subject.element_id, object_ids, coverage, share, target, borderline))
    hits.sort(key=lambda hit: (not hit.target, not hit.borderline))
    if not any(hit.target for hit in hits):
        reason = PARTIAL
    elif any(hit.borderline for hit in hits):
        reason = BORDERLINE_HIT
    else:
        reason = None
    return MarkReading(gesture.id, gesture.kind, ink.zone, tuple(hits), reason)


__all__ = [
    "BORDERLINE_HIT",
    "DOMINANT",
    "ENCLOSED",
    "MarkHit",
    "MarkReading",
    "NOTHING",
    "PARTIAL",
    "Subject",
    "UNBOUND",
    "read_id_image",
    "read_marks",
    "subjects_of",
]
