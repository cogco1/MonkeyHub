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
and the element among that component's own rows), and each component or
element is scored:

- ``central``: it reaches the mark's core, the middle of the mark no layer
  only grazing its rim reaches. A layer running through the middle of a circle
  is central although it continues far beyond it; one crossing only the rim,
  or an object whose corner the circle grazes, is not.
- ``coverage``: how much of all of it on the page lies inside the mark
  (circled whole is 1).
- ``share``: how much of what is inside the mark is its own (for an arrow, how
  much nearer the tip it is than the others).

A central subject, or one circled almost whole, is a target; one only in the
mark's rim is borderline when it holds a fair part of the mark. A mark with no
target, or with a borderline subject beside its targets, is ambiguous and says
why: nothing is drawn there, what is drawn names no component of this record,
it only touches several things, or something else lies at its rim. Which of
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

#: Coverage at or above which a subject counts as circled whole, wherever in the mark it lies.
ENCLOSED = 0.8
#: A subject is central when this share of the core's area is its own, or its lines run this many core radii in it.
CORE_AREA = 0.02
CORE_LINE = 0.2
#: A subject in the mark that is not a target is borderline with this share of the mark, or this coverage.
BORDERLINE_SHARE = 0.1
BORDERLINE = 0.3
#: Share of an arrow's weight at or above which a subject counts as what it points at.
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
    central: bool = False

    def to_dict(self) -> dict:
        return {"componentId": self.component_id, "elementId": self.element_id, "objectIds": list(self.object_ids),
                "coverage": self.coverage, "share": self.share, "central": self.central, "target": self.target,
                "borderline": self.borderline}


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
    of the weight is on. Any other mark takes every subject that reaches its
    core or that it circles whole; a subject's coverage is by the area it
    bounds when it bounds any inside the mark, else by its lines.
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
        sums = gathered.setdefault(subject, [[], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        sums[0].append(row.object_id)
        for index, value in enumerate((row.line_in, row.line_core, row.line_total, row.area_in, row.area_core,
                                       row.area_total, row.nearness), 1):
            sums[index] += value
    if not gathered:
        return MarkReading(gesture.id, gesture.kind, ink.zone, (), UNBOUND)
    scored = []
    for subject, (object_ids, line_in, line_core, line_total, area_in, area_core, area_total,
                  nearness) in gathered.items():
        if area_in > 0:
            coverage = area_in / area_total if area_total else 1.0
        else:
            coverage = line_in / line_total if line_total else 0.0
        central = not pointing and ink.core_area > 0 and (
            area_core >= CORE_AREA * ink.core_area or line_core >= CORE_LINE * ink.core_radius)
        if pointing:
            share = nearness / nearness_all if nearness_all else 0.0
        else:
            share = max(line_in / line_in_all if line_in_all else 0.0,
                        area_in / ink.zone_area if ink.zone_area else 0.0)
        scored.append((subject, tuple(sorted(object_ids)), round(min(coverage, 1.0), 4), round(min(share, 1.0), 4),
                       central))
    scored.sort(key=lambda row: (-row[3], -row[2], row[0].component_id, row[0].element_id or ""))
    top = scored[0][0] if pointing and scored[0][3] >= DOMINANT else None
    hits = []
    for subject, object_ids, coverage, share, central in scored:
        target = subject == top or (not pointing and (central or coverage >= ENCLOSED))
        borderline = not target and not pointing and (share >= BORDERLINE_SHARE or coverage >= BORDERLINE)
        hits.append(MarkHit(subject.component_id, subject.element_id, object_ids, coverage, share, target,
                            borderline, central))
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
    "CORE_AREA",
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
