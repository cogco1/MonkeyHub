"""Undeclared relations made explicit: geometric (I1) and role-rule (I2) inference, kept as candidate obligations.

Both layers are deterministic harness additions; the kernel infers nothing.

- **I1.** Reads the start model's cold-read STEP. For every pair of elements no
  declared relation covers, OCCT measures the pairwise solid distance (pairs are
  pre-filtered by box gap). A distance of at most 0.001 m is a contact: a
  support when one element's top meets the other's bottom over a shared plan,
  otherwise an abutment. A distance of at most 0.05 m is a near contact. Two
  elements whose tops (or non-ground bottoms) share a level without touching
  are aligned.
- **I2.** Reads the record. Each element gets a role from its producer and id
  (``ROLES``); a frozen rule table (``RULES``) proposes the relations those
  roles should have. A proposal some declared relation already covers is
  dropped.

Two narrowing rules, frozen with the role table, apply before anything is
measured (``narrow``): an alignment both of whose faces the record binds to the
same declared datum is not added (inference adds only what the state does not
say), and a role rule's non-interference covers only elements whose boxes are
within ``ROLE_REACH_M`` (1.0 m). What they remove is reported, not discarded.

Each inferred relation becomes a ``DesignObligation`` with
``validator:inferred-<kind>``, its two elements as subjects and its geometric
basis in the statement; it stays open and unconfirmed. ``check_inferred``
measures them on a candidate: a contact that opens or turns into penetration,
a support whose upper element no longer rests on the lower one's top, a near
contact that penetrates, a non-interference that is violated, or an alignment
that breaks is a broken inference, and the decision on it is ``ask``. A role
proposal is kept only if it holds in this sense on the start: a roof that
touches a wall's side does not rest on it.

Before execution, ``predict_inferred`` measures the same relations on the
compiler's analytic per-object bounds (``expected_object_bounds``: compile
only, no OCCT). A relation whose boxes cannot settle it (an element with no
analytic bounds, or a box test that already fails on the start, such as a
non-interference whose boxes overlap) falls back to ``crossing``: one element
inside the change's closure and one outside. Each result names the method that
decided it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any

from archflow.state.operational_state import DesignObligation
from archflow.state.state_record import StateRecord

from labs.revision_tasks import pipeline

CONTACT_M = 0.001
NEAR_M = 0.05
ROLE_REACH_M = 1.0       # narrowing rule 2: a role rule's non-interference covers elements this close (box gap)
PREFIX = "validator:inferred-"

# Element roles, by producer and (where one producer serves several roles) by id. Frozen with the rules.
ROLES: list[tuple[str, str | None, str]] = [
    ("pediment", None, "roof"),
    ("beam", "entablature", "entablature"),
    ("column-array", None, "column"),
    ("prism", "stylobate", "base"),
    ("prism", "lintel", "lintel"),
    ("prism", "slab", "floor"),
    ("prism", "transition-roof", "roof"),
    ("prism", "transition-body", "block"),
    ("wall", None, "wall"),
    ("ring", "wall", "wall"),
    ("ring", "cornice", "cornice"),
    ("shell", "drum", "drum"),
    ("shell", "dome", "dome"),
    ("stair", None, "stair"),
]
# (lower or first role, upper or second role, kind): what elements of these roles should have.
RULES: list[tuple[str, str, str]] = [
    ("entablature", "roof", "support"),      # a pediment or roof rests on an entablature ...
    ("wall", "roof", "support"),             # ... or on a wall
    ("block", "roof", "support"),
    ("wall", "lintel", "support"),           # a lintel bears on its wall
    ("base", "column", "support"),           # columns stand on a base ...
    ("column", "entablature", "support"),    # ... and carry the entablature
    ("floor", "stair", "abutment"),          # a stair meets the floor it reaches
    ("wall", "stair", "non_interference"),   # a stair does not run into a wall
    ("wall", "cornice", "support"),
    ("cornice", "drum", "support"),
    ("drum", "dome", "support"),
]


@dataclass(frozen=True)
class Inferred:
    layer: str          # I1 | I2
    kind: str           # support | abutment | near | same_level | non_interference
    a: str              # lower / first element
    b: str              # upper / second element
    basis: dict[str, Any]

    def obligation(self) -> DesignObligation:
        tag = hashlib.sha256(f"{self.layer}|{self.kind}|{self.a}|{self.b}".encode()).hexdigest()[:10]
        basis = ", ".join(f"{k}={v}" for k, v in sorted(self.basis.items()))
        return DesignObligation(f"inferred-{self.layer.lower()}-{tag}",
                                f"Inferred ({self.layer}, unconfirmed): {self.a} / {self.b} {self.kind}; basis {basis}.",
                                f"inference:{self.layer.lower()}", subject_refs=(f"entity:{self.a}", f"entity:{self.b}"),
                                validator_ref=f"{PREFIX}{self.kind.replace('_', '-')}")


def role_of(record: StateRecord) -> dict[str, str]:
    roles = {}
    for entity in record.entities_of("Element@1"):
        producer = entity.fields.get("producer")
        for rule_producer, id_part, role in ROLES:
            if producer == rule_producer and (id_part is None or id_part in entity.entity_id):
                roles[entity.entity_id] = role
                break
    return roles


def _declared_pairs(record: StateRecord) -> set[frozenset[str]]:
    return {frozenset((r.subject, r.object)) for r in record.relations}


def _boxes(realization: pipeline.Realization) -> dict[str, tuple[list[float], list[float]]]:
    """Each element's union box over its visible objects, in the model frame (x, plan, up)."""

    visible = set(pipeline.visible_objects(realization))
    boxes: dict[str, tuple[list[float], list[float]]] = {}
    for element, objects in realization.objects_by_element.items():
        rows = [realization.receipt.readback[o]["bbox"] for o in objects if o in visible]
        if rows:
            boxes[element] = ([min(r["min"][i] for r in rows) for i in range(3)], [max(r["max"][i] for r in rows) for i in range(3)])
    return boxes


def _gap(a, b) -> float:
    return max(0.0, max(max(a[0][i] - b[1][i], b[0][i] - a[1][i]) for i in range(3)))


def _plan_overlap(a, b) -> bool:
    return all(a[0][i] < b[1][i] - 1e-6 and b[0][i] < a[1][i] - 1e-6 for i in (0, 1))


def _pair_measure(realization: pipeline.Realization, a: str, b: str, entries) -> tuple[float | None, float]:
    """Minimum OCCT distance and total common volume between the visible objects of two elements."""

    from monkeycad.backends.occt.measure import measure_occt_solid_pairs

    visible = set(pipeline.visible_objects(realization))
    pairs = [(x, y) for x in realization.objects_by_element[a] if x in visible for y in realization.objects_by_element[b] if y in visible]
    measured = measure_occt_solid_pairs(entries, object_pairs=pairs, length_unit="meter") if pairs else {}
    distances = [row["distance_m"] for row in measured.values() if row.get("distance_m") is not None]
    volume = sum(row["common_volume_m3"] or 0.0 for row in measured.values() if row.get("common_volume_m3") is not None)
    return (min(distances) if distances else None), float(volume)


def infer_geometric(record: StateRecord, realization: pipeline.Realization) -> list[Inferred]:
    from monkeycad.backends.occt.step import read_step

    boxes = _boxes(realization)
    declared = _declared_pairs(record)
    entries = read_step(realization.step_path, length_unit="meter")
    names = sorted(boxes)
    found: list[Inferred] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if frozenset((a, b)) in declared:
                continue
            ba, bb = boxes[a], boxes[b]
            if _gap(ba, bb) <= NEAR_M:
                distance, volume = _pair_measure(realization, a, b, entries)
                if distance is not None and volume <= 1e-9 and distance <= NEAR_M:
                    if distance <= CONTACT_M:
                        lower, upper = (a, b) if abs(ba[1][2] - bb[0][2]) <= CONTACT_M else (b, a) if abs(bb[1][2] - ba[0][2]) <= CONTACT_M else (None, None)
                        if lower is not None and _plan_overlap(boxes[lower], boxes[upper]):
                            found.append(Inferred("I1", "support", lower, upper, {"distance_m": round(distance, 6)}))
                        else:
                            found.append(Inferred("I1", "abutment", a, b, {"distance_m": round(distance, 6)}))
                    else:
                        found.append(Inferred("I1", "near", a, b, {"distance_m": round(distance, 6)}))
                    continue
            for side, label in ((1, "top"), (0, "bottom")):
                level = ba[side][2]
                if abs(level - bb[side][2]) <= CONTACT_M and not (side == 0 and abs(level) <= CONTACT_M):
                    found.append(Inferred("I1", "same_level", a, b, {"level_m": round(level, 6), "face": label}))
                    break
    return found


def infer_roles(record: StateRecord) -> list[Inferred]:
    roles = role_of(record)
    declared = _declared_pairs(record)
    by_role: dict[str, list[str]] = {}
    for element, role in sorted(roles.items()):
        by_role.setdefault(role, []).append(element)
    found = []
    for first, second, kind in RULES:
        for a in by_role.get(first, []):
            for b in by_role.get(second, []):
                if a != b and frozenset((a, b)) not in declared:
                    found.append(Inferred("I2", kind, a, b, {"rule": f"{first}-{second}-{kind}"}))
    return found


def with_inferred(record: StateRecord, inferred: list[Inferred]) -> StateRecord:
    """The record with each distinct inferred relation added as an open candidate obligation."""

    seen, obligations = set(), []
    for item in inferred:
        obligation = item.obligation()
        if obligation.obligation_id not in seen:
            seen.add(obligation.obligation_id)
            obligations.append(obligation)
    return replace(record, obligations=tuple(record.obligations) + tuple(obligations))


def _parse(obligation: DesignObligation) -> tuple[str, str, str] | None:
    if not (obligation.validator_ref or "").startswith(PREFIX):
        return None
    kind = obligation.validator_ref.removeprefix(PREFIX).replace("-", "_")
    a, b = (ref.removeprefix("entity:") for ref in obligation.subject_refs)
    return kind, a, b


Box = tuple[list[float], list[float]]


def predicted_boxes(record: StateRecord) -> dict[str, Box | None]:
    """Each element's union box over its visible objects as the compiler predicts it, in the model frame; ``None`` where
    an object has no analytic bounds. Compiles only: no OCCT."""

    from archflow.state.geometry_program import GeometryBoundsError, delivered_object_ids, expected_object_bounds, operation_parameters

    program, objects_by_element, _ = pipeline.compile_record(record)
    try:
        bounds = expected_object_bounds(program)
    except GeometryBoundsError:
        return {element: None for element in objects_by_element}
    delivered = set(delivered_object_ids(program.proposal))
    hidden = {op.output_object_ids[0] for op in program.proposal.operations if operation_parameters(op).get("hidden_for_inspection")}
    boxes: dict[str, Box | None] = {}
    for element, objects in objects_by_element.items():
        rows = [bounds.get(o) for o in objects if o in delivered and o not in hidden]
        if not rows or any(r is None for r in rows):
            boxes[element] = None
            continue
        lo = [min(r["bbox_min"][i] for r in rows) for i in range(3)]
        hi = [max(r["bbox_max"][i] for r in rows) for i in range(3)]
        boxes[element] = ([lo[0], lo[2], lo[1]], [hi[0], hi[2], hi[1]])      # program (x, up, plan) -> model (x, plan, up)
    return boxes


def _rests_on(lower: Box, upper: Box) -> bool:
    return abs(lower[1][2] - upper[0][2]) <= CONTACT_M and _plan_overlap(lower, upper)


def _box_holds(kind: str, statement: str, a: Box, b: Box) -> bool:
    if kind == "same_level":
        match = re.search(r"face=(\w+)", statement)
        side = 1 if match and match.group(1) == "top" else 0
        return abs(a[side][2] - b[side][2]) <= CONTACT_M
    if kind == "support":
        return _rests_on(a, b)
    if kind == "abutment":
        return _gap(a, b) <= CONTACT_M
    if kind == "near":
        return _gap(a, b) <= NEAR_M
    return _gap(a, b) > 0.0                             # non_interference: boxes apart cannot meet


def _box_status(kind: str, statement: str, a: Box, b: Box, a0: Box, b0: Box) -> str | None:
    """held / broken from boxes, or None when boxes cannot settle it: a box test that fails on the start says nothing."""

    if a == a0 and b == b0:
        return "held"                                   # neither element moves
    if not _box_holds(kind, statement, a0, b0):
        return None
    return "held" if _box_holds(kind, statement, a, b) else "broken"


def predict_inferred(start: StateRecord, successor: StateRecord, affected: set[str]) -> list[dict[str, Any]]:
    """Each inferred relation of ``start`` judged before execution on predicted boxes, else by the closure boundary."""

    before, after = predicted_boxes(start), predicted_boxes(successor)
    results = []
    for obligation in start.obligations:
        parsed = _parse(obligation)
        if parsed is None:
            continue
        kind, a, b = parsed
        row = {"obligation_id": obligation.obligation_id, "kind": kind, "a": a, "b": b}
        if a not in after or b not in after:
            results.append({**row, "status": "broken", "method": "predicted_box", "detail": "an element is gone"})
            continue
        boxes = (after[a], after[b], before.get(a), before.get(b))
        status = None if None in boxes else _box_status(kind, obligation.statement, *boxes)
        if status is not None:
            results.append({**row, "status": status, "method": "predicted_box"})
        else:
            results.append({**row, "status": "broken" if (a in affected) != (b in affected) else "held", "method": "closure_boundary"})
    return results


def crossing(record: StateRecord, affected: set[str]) -> list[str]:
    """Inferred relations with exactly one element inside the change's closure: what to ask about before executing."""

    out = []
    for obligation in record.obligations:
        parsed = _parse(obligation)
        if parsed and ((parsed[1] in affected) != (parsed[2] in affected)):
            out.append(obligation.obligation_id)
    return sorted(out)


def check_inferred(record: StateRecord, realization: pipeline.Realization) -> list[dict[str, Any]]:
    """Each inferred relation measured on ``realization``: held or broken."""

    from monkeycad.backends.occt.step import read_step

    boxes = _boxes(realization)
    entries = None
    results = []
    for obligation in record.obligations:
        parsed = _parse(obligation)
        if parsed is None:
            continue
        kind, a, b = parsed
        if a not in boxes or b not in boxes:
            results.append({"obligation_id": obligation.obligation_id, "kind": kind, "a": a, "b": b, "status": "broken", "detail": "an element is gone"})
            continue
        if kind == "same_level":
            match = re.search(r"face=(\w+)", obligation.statement)
            side = 1 if match and match.group(1) == "top" else 0
            held = abs(boxes[a][side][2] - boxes[b][side][2]) <= CONTACT_M
            results.append({"obligation_id": obligation.obligation_id, "kind": kind, "a": a, "b": b, "status": "held" if held else "broken"})
            continue
        if entries is None:
            entries = read_step(realization.step_path, length_unit="meter")
        distance, volume = _pair_measure(realization, a, b, entries)
        if kind == "support":                   # a contact, and the upper element's bottom on the lower one's top
            held = distance is not None and distance <= CONTACT_M and volume <= 1e-9 and _rests_on(boxes[a], boxes[b])
        elif kind == "abutment":
            held = distance is not None and distance <= CONTACT_M and volume <= 1e-9
        elif kind == "near":
            held = distance is not None and distance <= NEAR_M and volume <= 1e-9
        else:                                   # non_interference
            held = volume <= 1e-9
        results.append({"obligation_id": obligation.obligation_id, "kind": kind, "a": a, "b": b, "status": "held" if held else "broken",
                        "distance_m": None if distance is None else round(distance, 6), "common_volume_m3": round(volume, 6)})
    return results


def _datum_key(reference: Any) -> tuple[str, str, str] | None:
    """The datum a base or top reference binds to, with its offset as written; ``None`` for an absolute elevation."""

    if not isinstance(reference, dict):
        return None
    if "level" in reference:
        return ("level", str(reference["level"]), "0")
    if "datum" in reference:
        return ("datum", str(reference["datum"]), str(reference.get("offset", 0)))
    inner = reference.get("offset_from")
    if isinstance(inner, dict) and "level" in inner:
        return ("level", str(inner["level"]), str(inner.get("offset", 0)))
    return None


def _bindings(record: StateRecord) -> dict[str, tuple[Any, Any]]:
    """Each element's declared bottom and top bindings. A top with no reference of its own is bound by its base and its
    declared height (the same base datum and the same height expression put two tops at one level by declaration)."""

    out = {}
    for e in record.entities_of("Element@1"):
        references, params = e.fields.get("references", {}), e.fields.get("params", {})
        bottom = _datum_key(references.get("base"))
        top = _datum_key(references.get("top"))
        if top is None and bottom is not None and "height" in params:
            top = (bottom, "height", str(params["height"]))
        out[e.entity_id] = (bottom, top)
    return out


def narrow(record: StateRecord, inferred: list[Inferred], start: pipeline.Realization) -> tuple[list[Inferred], list[dict[str, Any]]]:
    """The two narrowing rules (frozen with the role table), applied before anything is measured:

    1. an alignment the record already explains, both faces bound to one declared datum (the same level or datum,
       with the same offset), is not an inferred relation: inference adds only what the state does not say;
    2. a role rule's ``non_interference`` applies only to elements whose boxes are within ``ROLE_REACH_M``.

    Returns (kept, removed with the rule that removed each).
    """

    bindings, boxes = _bindings(record), _boxes(start)
    kept, removed = [], []
    for item in inferred:
        if item.kind == "same_level":
            side = 1 if item.basis.get("face") == "top" else 0
            a, b = bindings.get(item.a, (None, None))[side], bindings.get(item.b, (None, None))[side]
            if a is not None and a == b:
                removed.append({"item": item, "rule": "declared common datum"})
                continue
        if item.layer == "I2" and item.kind == "non_interference":
            if item.a not in boxes or item.b not in boxes or _gap(boxes[item.a], boxes[item.b]) > ROLE_REACH_M:
                removed.append({"item": item, "rule": f"farther apart than {ROLE_REACH_M} m"})
                continue
        kept.append(item)
    return kept, removed


def infer_all(record: StateRecord, start: pipeline.Realization) -> tuple[list[Inferred], list[Inferred], list[dict[str, Any]]]:
    """I1 and I2 on the start, narrowed, then each kept only if it holds on the start geometry.

    Returns (kept, dropped because they do not hold on the start, removed by a narrowing rule).
    """

    candidates, narrowed = narrow(record, infer_geometric(record, start) + infer_roles(record), start)
    measured = {r["obligation_id"]: r["status"] for r in check_inferred(with_inferred(record, candidates), start)} if candidates else {}
    kept = [c for c in candidates if measured.get(c.obligation().obligation_id) == "held"]
    dropped = [c for c in candidates if measured.get(c.obligation().obligation_id) != "held"]
    return kept, dropped, narrowed
