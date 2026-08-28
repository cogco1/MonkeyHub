"""Auditable relation coverage over realized components.

Dimension two of the research program asks whether the bases of a
design have been found completely. Values are covered by the basis
index; relations need their own ledger. This module enumerates
candidate dependency edges mechanically — component pairs whose
realized bounds touch or overlap within a caller tolerance — detects
edges the compiled program already encodes (one component consuming or
responding to another's objects or bindings), joins caller-declared
edges with provenance, and reports everything left as a typed
``uncovered_relation``. The framework names no relation vocabulary, no
tolerance default, and resolves nothing on its own; silence is not a
legal state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


class RelationCoverageError(ValueError):
    """A coverage request or payload is malformed."""


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True, slots=True)
class CandidateRelation:
    """One mechanically enumerated component contact."""

    components: tuple[str, str]
    contact: str
    min_gap: float

    def to_dict(self) -> dict[str, object]:
        return {
            "components": list(self.components),
            "contact": self.contact,
            "min_gap": self.min_gap,
        }


def _component_bounds(scene_objects, bindings) -> dict[str, list]:
    component_of: dict[str, str] = {}
    for binding in bindings:
        for object_id in binding["object_ids"]:
            component_of[object_id] = binding["component_id"]
    boxes: dict[str, list] = {}
    for item in scene_objects:
        if not item.get("physical", True):
            continue
        component = component_of.get(item.get("object_id"))
        if component is None:
            binding_ids = set(item.get("semantic_binding_ids", ()) or ())
            matches = {
                b["component_id"]
                for b in bindings
                if b["binding_id"] in binding_ids
            }
            if len(matches) != 1:
                continue
            component = next(iter(matches))
        bounds = item.get("bounds")
        if not isinstance(bounds, Mapping):
            raise RelationCoverageError(
                f"object {item.get('object_id')!r} carries no bounds"
            )
        boxes.setdefault(component, []).append(
            (tuple(map(float, bounds["minimum"])),
             tuple(map(float, bounds["maximum"])))
        )
    return boxes


def _box_gap(a, b) -> float:
    gap = 0.0
    for axis in range(3):
        low = max(a[0][axis], b[0][axis])
        high = min(a[1][axis], b[1][axis])
        if low > high:
            gap = max(gap, low - high)
    return gap


def enumerate_candidate_relations(
    scene_objects: Iterable[Mapping[str, object]],
    bindings: Sequence[Mapping[str, object]],
    *,
    tolerance: float,
) -> tuple[CandidateRelation, ...]:
    """Component pairs whose realized bounds meet within ``tolerance``."""

    if not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise RelationCoverageError("tolerance must be non-negative")
    boxes = _component_bounds(list(scene_objects), list(bindings))
    names = sorted(boxes)
    candidates = []
    for index, a in enumerate(names):
        for b in names[index + 1 :]:
            best = None
            for box_a in boxes[a]:
                for box_b in boxes[b]:
                    gap = _box_gap(box_a, box_b)
                    best = gap if best is None else min(best, gap)
            if best is not None and best <= float(tolerance):
                candidates.append(
                    CandidateRelation(
                        components=_pair(a, b),
                        contact="overlap" if best == 0.0 else "touch",
                        min_gap=round(best, 9),
                    )
                )
    return tuple(candidates)


def detect_program_edges(
    operations: Iterable[Mapping[str, object]],
    bindings: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str], dict]:
    """Edges the compiled program already encodes, with provenance.

    A component whose operation consumes another component's object, or
    responds to another component's binding, depends on it — detected,
    not declared.
    """

    component_of_binding = {
        b["binding_id"]: b["component_id"] for b in bindings
    }
    component_of_object: dict[str, str] = {}
    for binding in bindings:
        for object_id in binding["object_ids"]:
            component_of_object[object_id] = binding["component_id"]
    edges: dict[tuple[str, str], dict] = {}

    def note(a, b, op_id, via):
        if a == b or a is None or b is None:
            return
        key = _pair(a, b)
        entry = edges.setdefault(
            key, {"kind": "derived", "detected_from": []}
        )
        entry["detected_from"].append({"op_id": op_id, "via": via})

    for op in operations:
        own = {
            component_of_binding.get(item)
            for item in op.get("semantic_binding_ids", ())
        } - {None}
        for consumed in op.get("input_object_ids", ()):
            other = component_of_object.get(consumed)
            for mine in own:
                note(mine, other, op["op_id"], f"consumes:{consumed}")
        for responded in op.get("responds_to_binding_ids", ()):
            other = component_of_binding.get(responded)
            for mine in own:
                note(mine, other, op["op_id"], f"responds_to:{responded}")
    for entry in edges.values():
        entry["detected_from"] = sorted(
            entry["detected_from"],
            key=lambda item: (item["op_id"], item["via"]),
        )
    return edges


_DECLARED_KINDS = {"derived", "constraint", "independent"}


def relation_coverage_ledger(
    candidates: Sequence[CandidateRelation],
    *,
    detected: Mapping[tuple[str, str], Mapping[str, object]],
    declared: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Join candidates against detections and declarations, fail-open never.

    Declarations carry ``components`` (pair), ``kind`` in
    derived/constraint/independent, and non-empty ``refs``. Every
    candidate resolves to exactly one entry; unresolved pairs are typed
    ``uncovered_relation`` rows carrying the contact geometry.
    """

    declared_by_pair: dict[tuple[str, str], dict] = {}
    for item in declared:
        components = item.get("components")
        if (
            not isinstance(components, (list, tuple))
            or len(components) != 2
        ):
            raise RelationCoverageError(
                "declared edge requires a component pair"
            )
        kind = item.get("kind")
        if kind not in _DECLARED_KINDS:
            raise RelationCoverageError(
                f"declared edge kind must be one of {sorted(_DECLARED_KINDS)}"
            )
        refs = item.get("refs")
        if not isinstance(refs, (list, tuple)) or not refs:
            raise RelationCoverageError(
                "declared edge requires non-empty refs"
            )
        declared_by_pair[_pair(*map(str, components))] = {
            "kind": kind,
            "refs": sorted(str(ref) for ref in refs),
        }
    rows = []
    uncovered = []
    for candidate in candidates:
        key = candidate.components
        if key in detected:
            resolution = {
                "status": "resolved",
                "kind": "derived",
                "source": "program",
                "detected_from": detected[key]["detected_from"],
            }
        elif key in declared_by_pair:
            resolution = {
                "status": "resolved",
                "source": "declaration",
                **declared_by_pair[key],
            }
        else:
            resolution = {"status": "uncovered_relation"}
            uncovered.append(list(key))
        rows.append({**candidate.to_dict(), "resolution": resolution})
    resolved = len(rows) - len(uncovered)
    return {
        "schema": "RelationCoverageLedger@1",
        "candidates": rows,
        "candidate_count": len(rows),
        "resolved_count": resolved,
        "uncovered": sorted(uncovered),
        "coverage_ratio": (
            round(resolved / len(rows), 6) if rows else 1.0
        ),
        "authority": False,
    }
