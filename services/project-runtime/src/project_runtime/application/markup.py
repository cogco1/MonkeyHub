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

A registered page is read as it was drawn (``read_page_marks``): a view
drawing by its retained SVG, a view sheet by each view it placed, a camera pass
by its object-ID image, and the record state the drawing was made from answers
for each object. An imported PDF or picture names no object.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain

from archflow.project.refs import record_ref_from_uri
from monkeydiagram.drawing_runs import read_model_axis_elevation
from monkeydiagram.rendering.marks import (
    FREEHAND, OUTLINE, POINT_ZONE, STROKE, TIP, MarkInk, ObjectInk, id_image_under_marks, ink_under_mark, page_ink,
)
from monkeydiagram.rendering.svg import DrawingSvgError, svg_object_marks
from monkeydiagram.sources import DrawingElevationError, object_semantics

from ..binding import ProjectBinding
from ..errors import StudioError
from . import artifacts, drawings
from .camera_passes import PASS_KIND
from .gestures import ARROW, CIRCLE, POLYLINE, TEXT, DocumentGesture, Gesture, GestureHit
from .pick import MODEL_VISIBLE_CATALOG_MISSING, RESOLVED, PickRequest, element_of_object, resolve_pick
from .projection import StateProjection, project_state

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

#: The zone of a stroke drawn on the model, read by the objects its samples fell on.
MODEL_ZONE = "model"

#: What a page's marks were read from (``PageMarks.source``): a view drawing's object-named lines, a view sheet's
#: placed views, a camera pass's object-ID image, or a page that names no object, where every mark reads NOTHING.
DRAWING_PAGE = "drawing"
SHEET_PAGE = "sheet"
ID_IMAGE_PAGE = "id-image"
PLAIN_PAGE = "page"
#: The sheet kind whose views are retained drawings placed where the request put them.
VIEW_SHEET = "view-sheet"

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


#: A stroke sample's hit to the record's subject, or None when the record does not vouch for it.
IdentifyHit = Callable[[GestureHit], "Subject | None"]


def hit_subjects_of(projection: StateProjection) -> IdentifyHit:
    """The record's answer for one object a model stroke fell on, by the rule a click is answered by (``pick``).

    A hit answers when its component resolves, with its element when one row
    produced it (a visible object whose element the catalogue lacks answers
    for its component); an unbound or unknown object answers for nothing.
    """

    def identify(hit: GestureHit) -> Subject | None:
        answer = resolve_pick(projection, PickRequest(state_digest=projection.state_digest,
                                                      user_strings=hit.user_strings, object_name=hit.object_name))
        if answer.status not in (RESOLVED, MODEL_VISIBLE_CATALOG_MISSING) or answer.component_id is None:
            return None
        return Subject(answer.component_id, answer.element_id)

    return identify


def read_model_marks(marks: Sequence[tuple[str, Gesture]], identify: IdentifyHit) -> tuple[MarkReading, ...]:
    """Each stroke drawn on the model (tracing paper over the 3D view) read by the objects its samples fell on.

    The viewer already casts every sample's ray (``Gesture.hits``); a stroke
    has no area on a page, so a subject is scored by its ``share`` of the
    samples alone (``coverage`` stays 0 and nothing is central) and the one
    holding DOMINANT of them is the target, as an arrow's is. Samples on
    objects the record does not vouch for count in the whole.
    """

    readings = []
    for mark_id, gesture in marks:
        if gesture.kind == TEXT:
            continue
        hits = [identify(hit) for hit in gesture.hits]
        if not hits:
            readings.append(MarkReading(mark_id, gesture.kind, MODEL_ZONE, (), NOTHING))
            continue
        counted: dict[Subject, list[str]] = {}
        for hit, subject in zip(gesture.hits, hits):
            if subject is not None:
                counted.setdefault(subject, []).append(hit.object_name or "")
        if not counted:
            readings.append(MarkReading(mark_id, gesture.kind, MODEL_ZONE, (), UNBOUND))
            continue
        scored = sorted(((subject, round(len(names) / len(hits), 4), names) for subject, names in counted.items()),
                        key=lambda row: (-row[1], row[0].component_id, row[0].element_id or ""))
        top = scored[0][0] if scored[0][1] >= DOMINANT else None
        found = tuple(MarkHit(subject.component_id, subject.element_id, tuple(sorted({name for name in names if name})),
                              0.0, share, subject == top) for subject, share, names in scored)
        readings.append(MarkReading(mark_id, gesture.kind, MODEL_ZONE, found, None if top is not None else PARTIAL))
    return tuple(readings)


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


@dataclass(frozen=True, slots=True)
class PageMarks:
    """One page's marks read, what they were read from, and the record state that answered for what they lie on."""

    source: str
    state_digest: str | None
    readings: tuple[MarkReading, ...]


def read_page_marks(binding: ProjectBinding, run_id: str, asset_sha256: str, page_index: int,
                    gestures: Sequence[DocumentGesture], *, revision_ref: str | None = None,
                    parts: Callable[[str], Mapping] | None = None) -> PageMarks:
    """Each mark on one registered page read into the components and elements it lies on (#300 3-2).

    The page is read as it was drawn, never drawn again: a view drawing (plan,
    section, elevation, section perspective) by its retained SVG's object-named
    lines; a view sheet by each view's, at its paper position; a camera pass by
    its object-ID image, which its line and depth images match pixel for pixel.
    The record state the drawing was made from answers for each object
    (``subjects_of``), so a drawing of a model from outside the project reads
    UNBOUND, unless ``parts`` reads that model (``model_parts``): a mark then
    names the part each object was read as, a floor or the cabinets. Another
    page names no object: every mark on it reads NOTHING. A view drawing is
    read by the revision the documents list names it by
    (DRAWING_REVISION_REQUIRED).
    """

    document, data = artifacts.document_bytes(binding, run_id, asset_sha256, revision_ref)
    if not 0 <= page_index < len(document.pages):
        raise StudioError(422, "DOCUMENT_PAGE_NOT_FOUND", "The page does not exist in this source document version.")
    if document.revision_ref is not None and revision_ref is None:
        raise StudioError(422, "DRAWING_REVISION_REQUIRED", "A view drawing's marks are read by its revisionRef, as "
                                                           "the documents list names it.")
    recipe = document.view_recipe or {}
    if document.revision_ref is not None:
        page, marks = _drawing_marks(binding, document.revision_ref)
        source, ink = DRAWING_PAGE, page_ink(marks)
    elif recipe.get("kind") == VIEW_SHEET and recipe.get("views"):
        page, source = tuple(recipe["paperSizeMm"]), SHEET_PAGE
        ink = tuple(chain.from_iterable(page_ink(_drawing_marks(binding, row["revisionRef"])[1],
                                                 place=tuple(row["placeMm"])) for row in recipe["views"]))
    elif recipe.get("kind") == PASS_KIND and (found := _id_image(binding, document, data)) is not None:
        id_document, png = found
        identify, state_digest = _drawn_state(binding, id_document, parts)
        return PageMarks(ID_IMAGE_PAGE, state_digest, read_id_image(
            gestures, png, (id_document.view_recipe or {}).get("legend") or (),
            _object_components(binding, id_document), identify))
    else:
        shown = document.pages[page_index]
        return PageMarks(PLAIN_PAGE, None, read_marks(gestures, (), (shown.width, shown.height), _nobody))
    identify, state_digest = _drawn_state(binding, document, parts)
    return PageMarks(source, state_digest, read_marks(gestures, ink, page, identify))


def _nobody(object_id: str, component: str | None) -> Subject | None:
    return None


def _drawing_marks(binding: ProjectBinding, revision_ref: str):
    """A retained view drawing's paper size and object-named marks, from the SVG its receipt keeps."""

    try:
        drawing = read_model_axis_elevation(binding.repository, record_ref_from_uri(revision_ref, binding.project_id))
        return svg_object_marks(drawing.svg)
    except (DrawingElevationError, DrawingSvgError, TypeError, ValueError) as exc:
        raise StudioError(409, "DOCUMENT_UNAVAILABLE", "The retained drawing revision cannot be read.") from exc


#: The run a model from outside the project is registered in, ``studio-model-<its sha256>``.
_ATTACHED_MODEL_RUN = "studio-model-"


def _drawn_state(binding: ProjectBinding, document, parts=None) -> tuple[Identify, str | None]:
    """Who answers for a drawn object: the record state the drawing was made from; for a model from outside, its
    parts when ``parts`` reads them, else no one."""

    if document.model_source is None and document.source_stage_ref is None:
        if parts is not None and document.run_id.startswith(_ATTACHED_MODEL_RUN):
            return snapshot_subjects(parts(document.run_id[len(_ATTACHED_MODEL_RUN):])), None
        return _nobody, None
    projection = project_state(binding, None if document.model_source is None else document.model_source.run_id,
                               source_stage_ref=document.source_stage_ref)
    return subjects_of(projection), projection.state_digest


def snapshot_subjects(reading: Mapping) -> Identify:
    """A model from outside the project answers for an object by the part it was read as (``model_parts``).

    An object is found by its id, a block member by its own GUID; a colour of
    an ID image grouped by layer by that layer's part. One read as other
    answers for nothing.
    """

    objects, layers = reading.get("objects") or {}, reading.get("layers") or {}

    def identify(object_id: str, component: str | None) -> Subject | None:
        row = objects.get(object_id) or objects.get(object_id.split("/")[-1])
        part = row["part"] if row is not None else layers.get(object_id)
        return None if part in (None, "other") else Subject(part)

    return identify


def _id_image(binding: ProjectBinding, document, data: bytes):
    """The camera pass's object-ID image and its bytes, from any of its three images; None when none is retained."""

    recipe = document.view_recipe or {}
    if recipe.get("pass") == "id":
        return document, data
    for row in reversed(artifacts.list_documents(binding, document.run_id)):
        other = row.view_recipe or {}
        if other.get("kind") == PASS_KIND and other.get("pass") == "id" and other.get("passId") == recipe.get("passId"):
            return artifacts.document_bytes(binding, row.run_id, row.asset_sha256)
    return None


def _object_components(binding: ProjectBinding, document) -> dict[str, str]:
    """Each object's component as the export a camera pass drew named it; none for a layer or material image."""

    if (document.view_recipe or {}).get("idBy") != "object" or (
            document.model_source is None and document.source_stage_ref is None):
        return {}
    receipt = drawings.drawing_receipt(binding, model_source=document.model_source,
                                       source_stage_ref=document.source_stage_ref)
    return {object_id: row["component"] for object_id, row in object_semantics(receipt).items() if "component" in row}


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
    "MODEL_ZONE",
    "MarkHit",
    "MarkReading",
    "NOTHING",
    "PARTIAL",
    "PageMarks",
    "Subject",
    "UNBOUND",
    "hit_subjects_of",
    "read_id_image",
    "read_model_marks",
    "read_marks",
    "read_page_marks",
    "snapshot_subjects",
    "subjects_of",
]
