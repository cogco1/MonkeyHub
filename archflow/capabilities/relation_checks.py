"""P102: run a State Record's relation validators against realized geometry.

A relation is a generation input (the producers build from it) *and* an
acceptance check: after geometry exists — analytic bounds from the
compiled program, or Rhino readback — every relation with a validator
binding is measured and the result recorded as ``RelationCheck@1``. No
healing: a violated relation is reported, never repaired here.

Bounds are in program coordinates (x, y-up, z-plan), per object id;
``objects_by_element`` maps an Element@1 entity to the object ids its
producer emitted.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from archflow.state.state_record import Relation, StateRecord

Bounds = tuple[Sequence[float], Sequence[float]]


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
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _extent(objects: Sequence[str], bounds: Mapping[str, Bounds], label: str) -> tuple[float, float]:
    missing = [o for o in objects if o not in bounds]
    if not objects or missing:
        raise RelationCheckError(f"{label}: no realized bounds for {missing or 'any object'}")
    return min(float(bounds[o][0][1]) for o in objects), max(float(bounds[o][1][1]) for o in objects)


def check_support_contact(relation: Relation, *, record: StateRecord, bounds: Mapping[str, Bounds], objects_by_element: Mapping[str, Sequence[str]],
                          datum_values: Mapping[str, float]) -> RelationCheck:
    """subject top == object bottom + declared engagement (and == the datum, when one is named)."""

    tolerance = relation.validator.tolerance if relation.validator and relation.validator.tolerance is not None else 0.001
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
    else:
        _, subject_top = _extent(objects_by_element.get(relation.subject, ()), bounds, f"{relation.relation_id} subject {relation.subject}")
        measured["subject_top"] = subject_top
        object_bottom, _ = _extent(objects_by_element.get(relation.object, ()), bounds, f"{relation.relation_id} object {relation.object}")
        measured["object_bottom"] = object_bottom
        gap = (object_bottom + engagement - rise) - subject_top
        measured["gap"] = gap
        if abs(gap) > tolerance:
            problems.append(f"object bottom {object_bottom:.4f} + engagement {engagement:.4f} is {gap:+.4f} from subject top {subject_top:.4f}")
    if relation.datum_role and relation.datum_role in datum_values:
        datum = float(datum_values[relation.datum_role])
        measured["datum_value"] = datum
        reference = measured["level_elevation"] if level is not None else measured["subject_top"]
        if abs(datum - reference) > tolerance:
            problems.append(f"datum {relation.datum_role}={datum:.4f} does not hold the measured face {reference:.4f}")
    status = "violated" if problems else "held"
    return RelationCheck(relation.relation_id, relation.kind, "support_contact", status, tolerance, measured, "; ".join(problems) or "contact by construction holds")


_CHECKERS = {"support_contact": check_support_contact}


def check_relations(record: StateRecord, *, bounds: Mapping[str, Bounds], objects_by_element: Mapping[str, Sequence[str]],
                    datum_values: Mapping[str, float] | None = None) -> RelationCheckReport:
    """Measure every relation that binds a validator; others are reported as unchecked."""

    checks: list[RelationCheck] = []
    for relation in record.relations:
        if relation.validator is None:
            checks.append(RelationCheck(relation.relation_id, relation.kind, "none", "unchecked", 0.0, {}, "no validator bound"))
            continue
        checker = _CHECKERS.get(relation.validator.check_kind)
        if checker is None:
            checks.append(RelationCheck(relation.relation_id, relation.kind, relation.validator.check_kind, "unchecked",
                                        relation.validator.tolerance or 0.0, {}, f"no checker for {relation.validator.check_kind} yet"))
            continue
        checks.append(checker(relation, record=record, bounds=bounds, objects_by_element=objects_by_element, datum_values=datum_values or {}))
    return RelationCheckReport(record.digest, tuple(checks))
