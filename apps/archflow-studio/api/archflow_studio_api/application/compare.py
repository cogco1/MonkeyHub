"""Before / After / Why: two runs' exports compared object by object, from
the inspection records the runner retained - kernel facts, no browser
inference.

Every export leaves a ``seat-3dm-inspection`` record beside its receipt:
each object's name, its layer, its bounding box and the digest of its
geometry, with the ``archflow:*`` strings the exporter wrote on it. Comparing
two runs is therefore a join on object names: an object in both with the
same geometry digest is unchanged; a different digest (or a box that moved)
is changed; one side only is added or removed. The change is grouped by
component - the architect's unit - and counted, so the card can say
"changed because <utterance> · affected 4 · unchanged 27" from numbers the
record wrote, not numbers the page estimated.

Nothing here reads the 3dm files. What it cannot find in the records it says
so about: a run without inspection records has no shapes to compare, and
answers 404 by name.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from archflow.project.refs import ProjectRecordRef

from ..transport.errors import StudioError
from .binding import RUNNER_RECEIPT_KIND, ProjectBinding, record_kind

INSPECTION_KIND = "seat-3dm-inspection"

UNCHANGED = "unchanged"
CHANGED = "changed"
ADDED = "added"
REMOVED = "removed"

# Two boxes closer than this on every coordinate are the same box. The
# export is in metres; a tenth of a millimetre is below anything Rhino's
# render-mesh witnesses resolve.
BBOX_TOLERANCE = 1e-4

Vector = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Shape:
    """One exported object as its inspection record describes it."""

    name: str
    seat_id: str
    component_id: str | None
    producer_op: str | None
    geometry_sha256: str | None
    bbox_min: Vector | None
    bbox_max: Vector | None


@dataclass(frozen=True, slots=True)
class ObjectChange:
    name: str
    seat_id: str
    component_id: str | None
    producer_op: str | None
    status: str
    before_min: Vector | None
    before_max: Vector | None
    after_min: Vector | None
    after_max: Vector | None


@dataclass(frozen=True, slots=True)
class ComponentChange:
    component_id: str
    changed: int
    unchanged: int
    added: int
    removed: int


@dataclass(frozen=True, slots=True)
class Comparison:
    candidate_id: str
    against: str
    why: str | None
    why_source: str
    tolerance: float
    changed: int
    unchanged: int
    added: int
    removed: int
    components: tuple[ComponentChange, ...]
    objects: tuple[ObjectChange, ...]


def shapes_of(binding: ProjectBinding, run_id: str) -> tuple[Shape, ...]:
    """Every object the run's exports left, from its inspection records.

    The receipt names each seat's inspection record; the record is found
    among the run's retained JSON by that digest and read through the
    repository, so what is compared is what P036 verified.
    """

    newest = binding.newest_runner_receipt(run_id)
    if newest is None:
        raise StudioError(
            404,
            "RUN_NOT_FOUND",
            f"{binding.project_id}: run {run_id} retained no "
            f"{RUNNER_RECEIPT_KIND}, so there is nothing of it to compare.",
        )
    _, receipt = newest
    by_sha: dict[str, ProjectRecordRef] = {
        ref.sha256: ref
        for ref in binding.record_refs(run_id)
        if record_kind(ref) == INSPECTION_KIND
    }
    shapes: list[Shape] = []
    seats_with_inspection = 0
    for row in _rows(receipt.get("seat_results")):
        seat_id = str(row.get("seat_id"))
        cad = row.get("cad")
        uri = cad.get("inspection_ref") if isinstance(cad, Mapping) else None
        if not isinstance(uri, str):
            continue
        ref = by_sha.get(_sha_of(uri))
        if ref is None:
            raise StudioError(
                404,
                "INSPECTION_NOT_FOUND",
                f"{binding.project_id}: run {run_id} names {uri} for seat "
                f"{seat_id}, but no such {INSPECTION_KIND} record is retained.",
            )
        seats_with_inspection += 1
        shapes.extend(_shapes_in(binding.repository.load_json(ref), seat_id))
    if seats_with_inspection == 0:
        raise StudioError(
            404,
            "INSPECTION_NOT_FOUND",
            f"{binding.project_id}: run {run_id} exported nothing that was "
            "inspected, so there are no shapes to compare. A run without "
            "Rhino export leaves no inspection record.",
        )
    return tuple(shapes)


def compare_runs(
    binding: ProjectBinding,
    *,
    candidate_id: str,
    against: str,
    why: str | None,
    why_source: str,
) -> Comparison:
    """The candidate's objects against another run's, joined by name."""

    after = {shape.name: shape for shape in shapes_of(binding, candidate_id)}
    before = {shape.name: shape for shape in shapes_of(binding, against)}
    objects: list[ObjectChange] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old is None and new is not None:
            status = ADDED
        elif new is None and old is not None:
            status = REMOVED
        else:
            assert old is not None and new is not None
            status = CHANGED if _differs(old, new) else UNCHANGED
        shape = new or old
        assert shape is not None
        objects.append(
            ObjectChange(
                name=name,
                seat_id=shape.seat_id,
                component_id=shape.component_id,
                producer_op=shape.producer_op,
                status=status,
                before_min=old.bbox_min if old else None,
                before_max=old.bbox_max if old else None,
                after_min=new.bbox_min if new else None,
                after_max=new.bbox_max if new else None,
            )
        )
    per_component: dict[str, Counter[str]] = {}
    for change in objects:
        key = change.component_id or "(no component tag)"
        per_component.setdefault(key, Counter())[change.status] += 1
    components = tuple(
        ComponentChange(
            component_id=component_id,
            changed=counts[CHANGED],
            unchanged=counts[UNCHANGED],
            added=counts[ADDED],
            removed=counts[REMOVED],
        )
        for component_id, counts in sorted(
            per_component.items(),
            # The components with something to say first, most first.
            key=lambda item: (
                -(item[1][CHANGED] + item[1][ADDED] + item[1][REMOVED]),
                item[0],
            ),
        )
    )
    totals = Counter(change.status for change in objects)
    return Comparison(
        candidate_id=candidate_id,
        against=against,
        why=why,
        why_source=why_source,
        tolerance=BBOX_TOLERANCE,
        changed=totals[CHANGED],
        unchanged=totals[UNCHANGED],
        added=totals[ADDED],
        removed=totals[REMOVED],
        components=components,
        objects=tuple(objects),
    )


def _differs(old: Shape, new: Shape) -> bool:
    if old.geometry_sha256 is not None and new.geometry_sha256 is not None:
        if old.geometry_sha256 != new.geometry_sha256:
            return True
        # Same geometry digest, but a box that moved is a moved object: the
        # digest is of the shape, the box is of where it stands.
        return _box_moved(old, new)
    return _box_moved(old, new)


def _box_moved(old: Shape, new: Shape) -> bool:
    if old.bbox_min is None or new.bbox_min is None:
        return old.bbox_min != new.bbox_min
    assert old.bbox_max is not None and new.bbox_max is not None
    for a, b in ((old.bbox_min, new.bbox_min), (old.bbox_max, new.bbox_max)):
        if any(abs(x - y) > BBOX_TOLERANCE for x, y in zip(a, b)):
            return True
    return False


def _shapes_in(record: Mapping[str, Any], seat_id: str) -> list[Shape]:
    payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else record
    boxes: dict[str, tuple[Vector, Vector]] = {}
    for row in _rows(payload.get("named_object_bboxes")):
        name = row.get("name")
        bbox = row.get("bbox")
        if isinstance(name, str) and isinstance(bbox, Mapping):
            lo, hi = _vector(bbox.get("min")), _vector(bbox.get("max"))
            if lo is not None and hi is not None:
                boxes[name] = (lo, hi)
    digests: dict[str, str] = {}
    for row in _rows(payload.get("object_geometry_sha256")):
        name, sha = row.get("name"), row.get("geometry_sha256")
        if isinstance(name, str) and isinstance(sha, str):
            digests[name] = sha
    strings: dict[str, dict[str, str]] = {}
    names: list[str] = []
    for row in _rows(payload.get("object_user_strings")):
        name = row.get("name")
        if not isinstance(name, str):
            continue
        names.append(name)
        attributes = {}
        for item in _rows(row.get("attributes")):
            key, value = item.get("key"), item.get("value")
            if isinstance(key, str) and isinstance(value, str):
                attributes[key] = value
        strings[name] = attributes
    for name in list(boxes) + list(digests):
        if name not in strings:
            names.append(name)
            strings[name] = {}
    seen: set[str] = set()
    shapes: list[Shape] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        box = boxes.get(name)
        shapes.append(
            Shape(
                name=name,
                seat_id=seat_id,
                component_id=strings[name].get("archflow:component"),
                producer_op=strings[name].get("archflow:producer_op"),
                geometry_sha256=digests.get(name),
                bbox_min=box[0] if box else None,
                bbox_max=box[1] if box else None,
            )
        )
    return shapes


def _rows(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _vector(value: object) -> Vector | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _sha_of(uri: str) -> str:
    name = uri.rsplit("/", 1)[-1]
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return stem.rsplit("-", 1)[-1]
