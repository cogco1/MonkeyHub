"""P102: run a State Record's relation validators against realized geometry.

A relation is a generation input (the producers build from it) *and* an
acceptance check: after geometry exists — analytic bounds from the
compiled program, or Rhino readback — every relation with a validator
binding is measured and the result recorded as ``RelationCheck@1``. No
healing: a violated relation is reported, never repaired here.

Bounds are in program coordinates (x, y-up, z-plan), per object id;
``objects_by_element`` maps an Element@1 entity to the object ids its
producer emitted. Plan therefore means the x and z axes and vertical means y.

``CHECKERS`` is the accepted vocabulary made measurable: it is keyed by the
ids of ``state_record.CHECK_KINDS`` and the two sets must agree, so a record
cannot declare a check that nothing here measures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from archflow.contracts.canonical import canonical_digest
from archflow.state.state_record import CHECK_KINDS, Relation, StateRecord

Bounds = tuple[Sequence[float], Sequence[float]]
Box = tuple[tuple[float, float, float], tuple[float, float, float]]

_PLAN_AXES = ((0, "x"), (2, "z"))       # y is up in program coordinates
_VERTICAL_AXIS = 1
_DEFAULT_TOLERANCE = 0.001


class RelationCheckError(ValueError):
    """A check cannot be run: the record or the bounds do not name what it needs."""


@dataclass(frozen=True)
class RelationCheck:
    relation_id: str
    kind: str
    check_kind: str
    status: str                       # held | violated | unchecked
    tolerance: float
    measured: dict[str, float]
    detail: str

    SCHEMA = "RelationCheck@1"

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.SCHEMA, "relation_id": self.relation_id, "kind": self.kind, "check_kind": self.check_kind, "status": self.status,
                "tolerance": self.tolerance, "measured": dict(sorted(self.measured.items())), "detail": self.detail}


@dataclass(frozen=True)
class RelationCheckReport:
    record_digest: str
    checks: tuple[RelationCheck, ...]

    SCHEMA = "RelationCheckReport@1"

    @property
    def held(self) -> bool:
        """No relation is violated. An unchecked relation does NOT count against this —
        display code must never render ``held`` alone as a green light; pair it with
        ``fully_checked`` and show held / violated / unchecked as three states."""

        return all(c.status != "violated" for c in self.checks)

    @property
    def fully_checked(self) -> bool:
        return all(c.status != "unchecked" for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        counts = {status: sum(1 for c in self.checks if c.status == status) for status in ("held", "violated", "unchecked")}
        return {"schema": self.SCHEMA, "record_digest": self.record_digest, "counts": counts, "held": self.held,
                "fully_checked": self.fully_checked, "checks": [c.to_dict() for c in self.checks]}

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def _extent(objects: Sequence[str], bounds: Mapping[str, Bounds], label: str) -> tuple[float, float]:
    missing = [o for o in objects if o not in bounds]
    if not objects or missing:
        raise RelationCheckError(f"{label}: no realized bounds for {missing or 'any object'}")
    return min(float(bounds[o][0][1]) for o in objects), max(float(bounds[o][1][1]) for o in objects)


def _union(objects: Sequence[str], bounds: Mapping[str, Bounds]) -> Box | None:
    """The axis-aligned union of the objects' finite bounds; None when there is nothing to measure."""

    boxes: list[tuple[list[float], list[float]]] = []
    for object_id in objects:
        box = bounds.get(object_id)
        if box is None:
            continue
        low, high = [float(v) for v in box[0]], [float(v) for v in box[1]]
        if len(low) != 3 or len(high) != 3 or not all(math.isfinite(v) for v in low + high):
            continue
        boxes.append((low, high))
    if not boxes:
        return None
    return (tuple(min(b[0][i] for b in boxes) for i in range(3)), tuple(max(b[1][i] for b in boxes) for i in range(3)))


def _axis_gap(a: Box, b: Box) -> float:
    """The largest per-axis separation between two boxes; 0 when they overlap on every axis."""

    return max(0.0, max(max(a[0][i] - b[1][i], b[0][i] - a[1][i]) for i in range(3)))


def _plan_gap(a: Box, b: Box) -> float:
    """The largest plan-axis (x, z) separation between two boxes; 0 when their plan footprints overlap or touch."""

    return max(0.0, max(max(a[0][i] - b[1][i], b[0][i] - a[1][i]) for i, _ in _PLAN_AXES))


def _box(object_id: str, bounds: Mapping[str, Bounds]) -> Box:
    low, high = bounds[object_id]
    return (tuple(float(v) for v in low), tuple(float(v) for v in high))


def _seat_members(subject_objects: Sequence[str], object_objects: Sequence[str], bounds: Mapping[str, Bounds], seat_offset: float,
                  tolerance: float) -> tuple[list[tuple[str, str, float, float]], float, float]:
    """Each object member against every subject member, one bounding box at a time.

    A member is *seated* when one and the same subject member both overlaps it in
    plan (x, z) and meets it vertically: subject top == member bottom + declared
    engagement - declared rise, within tolerance. Plan overlap on one member and
    a vertical match on another do not add up to a seat, and the group's union is
    never consulted, so a member moved out from over its support, or lifted off
    it, is not hidden by the members that stayed. A subject member that carries
    nothing is not a failure: a beam may span some of its supports and overhang.

    Returns the unseated members as (object member, nearest subject member, plan
    gap, seam) plus the largest plan gap and seam among the members' best
    candidates. Bounding boxes only: a seat here is a candidate contact, not a
    measured face contact and not a bearing check.
    """

    unseated: list[tuple[str, str, float, float]] = []
    worst_plan, worst_seam = 0.0, 0.0
    for member in object_objects:
        member_box = _box(member, bounds)
        best: tuple[float, str, float, float] | None = None
        for support in subject_objects:
            support_box = _box(support, bounds)
            plan = _plan_gap(support_box, member_box)
            seam = (member_box[0][_VERTICAL_AXIS] + seat_offset) - support_box[1][_VERTICAL_AXIS]
            candidate = (max(plan, abs(seam)), support, plan, seam)
            if best is None or candidate[0] < best[0]:
                best = candidate
        assert best is not None                       # _extent already refused an empty subject
        _, support, plan, seam = best
        worst_plan, worst_seam = max(worst_plan, plan), max(worst_seam, abs(seam))
        if plan > tolerance or abs(seam) > tolerance:
            unseated.append((member, support, plan, seam))
    return unseated, worst_plan, worst_seam


def check_support_contact(relation: Relation, *, record: StateRecord, bounds: Mapping[str, Bounds], objects_by_element: Mapping[str, Sequence[str]],
                          datum_values: Mapping[str, float]) -> RelationCheck:
    """subject top == object bottom + declared engagement - declared rise (and == a named datum).

    Between two elements the seam is also required member by member in plan:
    every object member must have one subject member under it (plan overlap
    and vertical seam on the same candidate), so a support that is nowhere
    beneath what it claims to carry is violated even when the heights agree.
    On a level nothing is required in plan: a level is an unbounded datum, and
    the element's lowest member is what stands on it (a stair's later steps
    rise above it by construction).

    Everything here is measured on axis-aligned bounding boxes. A held check
    says the boxes are in candidate contact at the declared seam; it does not
    say two solid faces touch, and it is not a bearing or capacity check.
    """

    tolerance = relation.validator.tolerance if relation.validator and relation.validator.tolerance is not None else _DEFAULT_TOLERANCE
    measured: dict[str, float] = {}
    problems: list[str] = []
    engagement = float(relation.parameters.get("engagement_depth", 0.0) or 0.0)
    rise = float(relation.parameters.get("rise", 0.0) or 0.0)
    measured["engagement_depth"] = engagement
    if rise:
        measured["rise"] = rise
    subject_entity, object_entity = record.entity(relation.subject), record.entity(relation.object)
    level = next((e for e in (subject_entity, object_entity) if e.schema == "Level@1"), None)
    if level is not None:
        # standing on a level (either orientation): the element's bottom sits at the level elevation
        element = relation.object if subject_entity is level else relation.subject
        element_bottom, _ = _extent(objects_by_element.get(element, ()), bounds, f"{relation.relation_id} element {element}")
        elevation = float(level.fields["elevation"])
        measured["element_bottom"] = element_bottom
        measured["level_elevation"] = elevation
        gap = element_bottom - (elevation - engagement + rise)
        measured["gap"] = gap
        if abs(gap) > tolerance:
            problems.append(f"element bottom {element_bottom:.4f} is {gap:+.4f} from level {level.entity_id} at {elevation:.4f} (declared engagement {engagement:.4f}, rise {rise:.4f})")
        held_detail = (f"element {element} lowest bound matches level {level.entity_id} with declared engagement {engagement:.4f} m "
                       f"and rise {rise:.4f} m within {tolerance:.4f} m (datum alignment only; no finite support or bearing measured)")
    else:
        subject_objects = objects_by_element.get(relation.subject, ())
        object_objects = objects_by_element.get(relation.object, ())
        _, subject_top = _extent(subject_objects, bounds, f"{relation.relation_id} subject {relation.subject}")
        measured["subject_top"] = subject_top
        object_bottom, _ = _extent(object_objects, bounds, f"{relation.relation_id} object {relation.object}")
        measured["object_bottom"] = object_bottom
        gap = (object_bottom + engagement - rise) - subject_top
        measured["gap"] = gap
        if abs(gap) > tolerance:
            problems.append(f"object bottom {object_bottom:.4f} + engagement {engagement:.4f} - rise {rise:.4f} is {gap:+.4f} from subject top {subject_top:.4f}")
        unseated, worst_plan, worst_seam = _seat_members(subject_objects, object_objects, bounds, engagement - rise, tolerance)
        measured["object_members"] = float(len(object_objects))
        measured["unseated_members"] = float(len(unseated))
        measured["seat_plan_gap_max"] = worst_plan
        measured["seat_seam_max"] = worst_seam
        if unseated:
            named = "; ".join(f"{member} nearest {support} is {plan:.4f} m away in plan (x/z) and {seam:+.4f} m off its top" for member, support, plan, seam in unseated[:3])
            more = f" (+{len(unseated) - 3} more)" if len(unseated) > 3 else ""
            problems.append(f"{len(unseated)} of {len(object_objects)} object members of {relation.object} have no subject member of {relation.subject} "
                            f"under them in plan at the seam: {named}{more}")
        held_detail = (f"all {len(object_objects)} object members of {relation.object} have a subject member of {relation.subject} under them within {tolerance:.4f} m: "
                       f"plan (x/z) overlap and vertical seam on the same member (bounding-box candidate contact, not face contact or bearing)")
    if relation.datum_role and relation.datum_role in datum_values:
        datum = float(datum_values[relation.datum_role])
        measured["datum_value"] = datum
        reference = measured["level_elevation"] if level is not None else measured["subject_top"]
        if abs(datum - reference) > tolerance:
            problems.append(f"datum {relation.datum_role}={datum:.4f} does not hold the measured face {reference:.4f}")
    status = "violated" if problems else "held"
    return RelationCheck(relation.relation_id, relation.kind, "support_contact", status, tolerance, measured, "; ".join(problems) or held_detail)


def check_clearance_interval(relation: Relation, *, record: StateRecord, bounds: Mapping[str, Bounds], objects_by_element: Mapping[str, Sequence[str]],
                             datum_values: Mapping[str, float]) -> RelationCheck:
    """The axis-aligned gap between subject and object lies in the declared interval_m."""

    interval = relation.validator.interval_m if relation.validator else None
    if interval is None:
        raise RelationCheckError(f"{relation.relation_id}: clearance_interval needs an interval_m")
    low, high = float(interval[0]), float(interval[1])
    measured: dict[str, float] = {"interval_low": low, "interval_high": high}
    subject_box = _union(objects_by_element.get(relation.subject, ()), bounds)
    object_box = _union(objects_by_element.get(relation.object, ()), bounds)
    unrealized = [element for element, box in ((relation.subject, subject_box), (relation.object, object_box)) if box is None]
    if unrealized:
        return RelationCheck(relation.relation_id, relation.kind, "clearance_interval", "unchecked", 0.0, measured,
                             f"no realized bounds for element {' and '.join(unrealized)}")
    gap = _axis_gap(subject_box, object_box)
    measured["gap"] = gap
    inside = low <= gap <= high
    detail = (f"gap {gap:.4f} m lies in [{low:.4f}, {high:.4f}] m" if inside
              else f"gap {gap:.4f} m is outside [{low:.4f}, {high:.4f}] m")
    return RelationCheck(relation.relation_id, relation.kind, "clearance_interval", "held" if inside else "violated", 0.0, measured, detail)


def check_aperture_exists(relation: Relation, *, record: StateRecord, bounds: Mapping[str, Bounds], objects_by_element: Mapping[str, Sequence[str]],
                          datum_values: Mapping[str, float]) -> RelationCheck:
    """The object opening has geometry and lies inside the subject host: in plan within tolerance, vertically not beyond it."""

    tolerance = relation.validator.tolerance if relation.validator and relation.validator.tolerance is not None else _DEFAULT_TOLERANCE
    host_box = _union(objects_by_element.get(relation.subject, ()), bounds)
    if host_box is None:
        return RelationCheck(relation.relation_id, relation.kind, "aperture_exists", "unchecked", tolerance, {},
                             f"no realized bounds for host element {relation.subject}")
    opening_box = _union(objects_by_element.get(relation.object, ()), bounds)
    if opening_box is None:
        return RelationCheck(relation.relation_id, relation.kind, "aperture_exists", "violated", tolerance, {},
                             f"opening {relation.object} has no object with finite bounds: the aperture does not exist")
    measured: dict[str, float] = {}
    problems: list[str] = []
    for axis, name in (*_PLAN_AXES, (_VERTICAL_AXIS, "y")):
        below = host_box[0][axis] - opening_box[0][axis]
        above = opening_box[1][axis] - host_box[1][axis]
        measured[f"{name}_low_overrun"], measured[f"{name}_high_overrun"] = below, above
        where = "in plan" if axis != _VERTICAL_AXIS else "vertically"
        if below > tolerance:
            problems.append(f"opening starts {below:.4f} m below the host on {name} ({where})")
        if above > tolerance:
            problems.append(f"opening reaches {above:.4f} m beyond the host on {name} ({where})")
    status = "violated" if problems else "held"
    return RelationCheck(relation.relation_id, relation.kind, "aperture_exists", status, tolerance, measured,
                         "; ".join(problems) or f"opening {relation.object} lies within host {relation.subject}")


class Checker(Protocol):
    """One measurement of one declared check kind against realized bounds."""

    def __call__(self, relation: Relation, *, record: StateRecord, bounds: Mapping[str, Bounds],
                 objects_by_element: Mapping[str, Sequence[str]], datum_values: Mapping[str, float]) -> RelationCheck:
        ...


CHECKERS: Mapping[str, Checker] = MappingProxyType({
    "support_contact": check_support_contact,
    "clearance_interval": check_clearance_interval,
    "aperture_exists": check_aperture_exists,
})

if set(CHECKERS) != set(CHECK_KINDS):                                   # the accepted vocabulary IS the checker table
    raise RelationCheckError(f"CHECKERS {sorted(CHECKERS)} does not match CHECK_KINDS {sorted(CHECK_KINDS)}")


def check_relations(record: StateRecord, *, bounds: Mapping[str, Bounds], objects_by_element: Mapping[str, Sequence[str]],
                    datum_values: Mapping[str, float] | None = None, relations: Sequence[Relation] | None = None) -> RelationCheckReport:
    """Measure every relation that binds a validator; a relation without one is reported as unchecked.

    ``relations`` defaults to the record's own; a caller that materialised more (the runner's
    producers) passes the full set, and the report still cites the record that was retained.

    ``objects_by_element`` is keyed by whatever has extent to measure, not only by ``Element@1``:
    the runner also puts each ``Space@1`` zone in it under its own entity id, whose "objects" are
    the ``Volume@1`` entities its ``volume_ids`` name, with those volumes' declared ``min`` /
    ``max`` in ``bounds``. A zone relation — corridor to hall clearance, a portico's voids — is
    therefore measured exactly like an element relation, by the same checkers.

    Every kind a ``ValidatorBinding`` can name is in ``CHECKERS``, so a bound
    relation is always measured: ``unchecked`` now means no validator, or no
    realized geometry to measure — never a check nobody implemented.
    """

    checks: list[RelationCheck] = []
    for relation in (record.relations if relations is None else relations):
        if relation.validator is None:
            checks.append(RelationCheck(relation.relation_id, relation.kind, "none", "unchecked", 0.0, {}, "no validator bound"))
            continue
        checker = CHECKERS[relation.validator.check_kind]
        checks.append(checker(relation, record=record, bounds=bounds, objects_by_element=objects_by_element, datum_values=datum_values or {}))
    return RelationCheckReport(record.digest, tuple(checks))
