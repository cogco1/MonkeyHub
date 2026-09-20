"""Pure polygon observations for the existing Study evidence owner.

Inputs are caller-selected confirmed traces and page extents. Values describe
2D drawings only; no filesystem, project state, source acceptance or design
preference belongs here.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


def _rounded(value: float) -> float:
    return round(float(value), 6)


def polygon_observations(
    polygons: list[Mapping[str, Any]], *, page_width: float, page_height: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Measure actual polygons in a resolution-free, aspect-correct page frame.

    These are drawing observations, not building performance or author intent.
    Shapely is already the drawing service's pinned geometry dependency. Invalid
    or self-crossing polygons are refused; buffer(0) would silently change the
    person's corrected evidence and is deliberately not a repair strategy here.
    """
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in (page_width, page_height)):
        raise ValueError("Page dimensions must be positive finite numbers.")
    if len({row["evidence_id"] for row in polygons}) != len(polygons):
        raise ValueError("Polygon ids must be unique.")
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    longest = max(page_width, page_height)
    sx, sy = page_width / longest, page_height / longest
    shapes = {}
    measurements = []
    for row in polygons:
        polygon = Polygon([(x * sx, y * sy) for x, y in row["points"]])
        if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
            raise ValueError(f"Trace {row['evidence_id']!r} is not a valid simple polygon.")
        shapes[row["evidence_id"]] = polygon
        measurements.append({
            "evidence_id": row["evidence_id"], "category": "computed",
            "method": "aspect-correct-polygon@1", "area": _rounded(polygon.area),
            "perimeter": _rounded(polygon.length),
            "centroid_x": _rounded(polygon.centroid.x), "centroid_y": _rounded(polygon.centroid.y),
        })
    masses = unary_union([shapes[row["evidence_id"]] for row in polygons if row["kind"] == "mass"])
    void_ids = {row["evidence_id"] for row in polygons if row["kind"] == "void"}
    for measurement in measurements:
        if measurement["evidence_id"] not in void_ids:
            continue
        clear = shapes[measurement["evidence_id"]].difference(masses)
        components = ([clear] if clear.geom_type == "Polygon" else list(getattr(clear, "geoms", ())))
        measurement.update({
            "clear_region_method": "declared-void-minus-confirmed-mass@1",
            "clear_area": _rounded(clear.area),
            "clear_components": sum(part.geom_type == "Polygon" and part.area > 1e-9 for part in components),
            "passability_established": False,
        })
    relations = []
    ids = sorted(shapes)
    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            a, b = shapes[left], shapes[right]
            area = a.intersection(b).area
            shared = a.boundary.intersection(b.boundary).length
            distance = a.distance(b)
            facts = [("distance", distance, left, right)]
            if area > 1e-9:
                facts.append(("intersection_area", area, left, right))
            if shared > 1e-9:
                facts.append(("shared_boundary", shared, left, right))
            if a.covers(b) and not a.equals(b):
                facts.append(("contains", None, left, right))
            elif b.covers(a) and not b.equals(a):
                facts.append(("contains", None, right, left))
            if a.touches(b):
                facts.append(("touches", None, left, right))
            for kind, value, subject, target in facts:
                relations.append({
                    "relation_id": f"{kind}:{subject}:{target}", "kind": kind,
                    "subject_evidence_id": subject, "object_evidence_id": target,
                    "value": None if value is None else _rounded(value),
                    "category": "computed", "method": "aspect-correct-polygon@1",
                })
    return measurements, sorted(relations, key=lambda row: row["relation_id"])
