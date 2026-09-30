"""Name-agnostic check of the ``stage-a-massing`` benchmark result (#419).

Every path under comparison (a native modeller, the Hub's producer path, the
Hub's construction path) ends with an exact STEP file. The check reads that
file cold and asks only what the task asked for: the total material, the
overall extent, and whether sample points fall in material or in the four
recesses. Object names, counts and how the solids were made do not matter.

Coordinates are the CAD frame of the STEP file: X and Y are the two plan
axes, Z is up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

GROUND = 12.0 * 8.0 * 3.2
RECESSES = 4 * 1.2 * 1.5 * 0.3
UPPER = 10.0 * 8.0 * 3.0
ROOF = 11.0 * 9.0 * 0.3
EXPECTED_VOLUME = GROUND - RECESSES + UPPER + ROOF
EXPECTED_BOUNDS = ((19.5, -0.5, 0.0), (32.0, 8.5, 6.5))
# Points in material: between two recesses, deep in the ground block, in the
# upper block, in the roof overhang.
INSIDE = ((23.6, 0.15, 1.65), (21.0, 4.0, 1.6), (25.0, 4.0, 4.7), (19.7, -0.3, 6.35))
# Points in the four recesses, and beside the upper block's short east face.
OUTSIDE = ((22.1, 0.15, 1.65), (25.1, 0.15, 1.65), (28.1, 0.15, 1.65), (31.1, 0.15, 1.65), (31.0, 4.0, 4.7))
# The Studio fixture's own objects stand west of x = 10; the study is east of x = 15.
REGION_MIN_X = 15.0
VOLUME_TOLERANCE = 0.05
BOUNDS_TOLERANCE = 0.005


def check_massing(step_path: Path, *, hidden_names: Iterable[str] = (), length_unit: str = "meter") -> dict:
    """Measure the study's visible solids in ``step_path`` against the task."""

    from monkeycad import occt_backend

    hidden = set(hidden_names)
    solids = []
    for entry in occt_backend.read_step(Path(step_path), length_unit=length_unit):
        if entry.name in hidden:
            continue
        measure = occt_backend.measure_shape(entry.shape)
        if measure.bbox_min is None or measure.bbox_min[0] < REGION_MIN_X:
            continue
        solids.append((entry, measure))
    if not solids:
        return {"ok": False, "solids": 0, "reason": "no visible solid east of x = 15"}
    volume = sum(measure.volume or 0.0 for _, measure in solids)
    low = [min(measure.bbox_min[axis] for _, measure in solids) for axis in range(3)]
    high = [max(measure.bbox_max[axis] for _, measure in solids) for axis in range(3)]

    def classify(point):
        return [occt_backend.classify_point(entry.shape, point) for entry, _ in solids]

    inside = {str(point): "inside" in classify(point) for point in INSIDE}
    outside = {str(point): all(state == "outside" for state in classify(point)) for point in OUTSIDE}
    volume_ok = abs(volume - EXPECTED_VOLUME) <= VOLUME_TOLERANCE
    bounds_ok = all(abs(actual - expected) <= BOUNDS_TOLERANCE
                    for actual, expected in zip((*low, *high), (*EXPECTED_BOUNDS[0], *EXPECTED_BOUNDS[1])))
    return {
        "ok": volume_ok and bounds_ok and all(inside.values()) and all(outside.values()),
        "solids": len(solids),
        "volume": round(volume, 6),
        "expected_volume": round(EXPECTED_VOLUME, 6),
        "volume_ok": volume_ok,
        "bounds": [low, high],
        "bounds_ok": bounds_ok,
        "inside": inside,
        "outside": outside,
    }


def study_step_and_hidden(project: Path, run_id: str) -> tuple[Path | None, tuple[str, ...]]:
    """The candidate's exact STEP file and the names its receipt keeps hidden."""

    import json

    run_dir = Path(project) / "runs" / run_id
    steps = sorted(run_dir.rglob("*.step"))
    hidden: set[str] = set()
    for path in run_dir.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == "OcctExecutionReceipt@1":
            objects = (payload.get("expected_semantics") or {}).get("objects", {})
            hidden.update(name for name, row in objects.items() if isinstance(row, dict) and row.get("visible") is False)
    return (steps[0] if steps else None), tuple(sorted(hidden))
