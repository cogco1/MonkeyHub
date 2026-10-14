"""Independent geometry oracle: judge an exported model against a task's frozen expectations.

Run it as its own process on files, never in the harness's interpreter:

    python -I oracle.py --task TASK.json --candidate CANDIDATE.3dm --start START.3dm --out REPORT.json

It imports nothing from this repository (only ``rhino3dm`` and ``numpy``), so
it shares no code with the producers, the compiler, the CAD exporter or the
system checks it is judging. It reads each object's name (identity) and mesh
geometry and nothing else: no user strings, no document strings, no layers,
no state record. Every expected number comes from the task file's
``protected_relations[].measure`` blocks, which a person wrote from the
fixture's physical definition.

Frame: the model file's own, x along, y plan depth, z up, metres.
A relation is ``held``, ``violated`` or ``unchecked`` (named objects absent or
the measurement not possible). The verdict is ``violated`` if any relation is,
otherwise ``unchecked`` if any is, otherwise ``held``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

ORACLE_ID = "geometry-readback-oracle"
ORACLE_VERSION = "1"

Mesh = tuple[np.ndarray, np.ndarray]          # vertices (n, 3) float64, triangles (m, 3) int64


class OracleInputError(ValueError):
    """The model file cannot be read as named meshes."""


def load_meshes(path: Path) -> dict[str, Mesh]:
    """Every mesh object by its name; a duplicated or empty name is an input error."""

    import rhino3dm

    model = rhino3dm.File3dm.Read(str(path))
    if model is None:
        raise OracleInputError(f"{path.name}: not a readable 3dm file")
    meshes: dict[str, Mesh] = {}
    for obj in model.Objects:
        geometry = obj.Geometry
        if not isinstance(geometry, rhino3dm.Mesh):
            continue
        name = obj.Attributes.Name
        if not name:
            raise OracleInputError("an unnamed mesh object has no identity")
        if name in meshes:
            raise OracleInputError(f"mesh name {name!r} occurs twice")
        vertices = np.array([[geometry.Vertices[i].X, geometry.Vertices[i].Y, geometry.Vertices[i].Z]
                             for i in range(len(geometry.Vertices))], dtype=np.float64)
        triangles: list[list[int]] = []
        for i in range(geometry.Faces.Count):
            a, b, c, d = geometry.Faces[i]
            triangles.append([a, b, c])
            if d != c:
                triangles.append([a, c, d])
        meshes[name] = (vertices, np.array(triangles, dtype=np.int64).reshape(-1, 3))
    return meshes


# ---------------------------------------------------------------- measurements

def volume(mesh: Mesh) -> float:
    v, f = mesh
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def bbox(meshes: dict[str, Mesh], names: list[str]) -> tuple[np.ndarray, np.ndarray]:
    stacked = np.vstack([meshes[n][0] for n in names])
    return stacked.min(axis=0), stacked.max(axis=0)


def segment_hits(mesh: Mesh, start: np.ndarray, end: np.ndarray) -> bool:
    """Does the open segment start->end cross any triangle (Moller-Trumbore)?"""

    v, f = mesh
    direction = end - start
    tri = v[f]
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    h = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(det) > 1e-12
    if not np.any(ok):
        return False
    inv = 1.0 / det[ok]
    s = start - tri[ok, 0]
    u = inv * np.einsum("ij,ij->i", s, h[ok])
    q = np.cross(s, e1[ok])
    w = inv * (q @ direction)
    t = inv * np.einsum("ij,ij->i", e2[ok], q)
    return bool(np.any((u >= 0) & (w >= 0) & (u + w <= 1) & (t > 1e-9) & (t < 1 - 1e-9)))


_PARITY_DIRECTION = np.array([0.5773, 0.5774, 0.5775]) / np.linalg.norm([0.5773, 0.5774, 0.5775])


def inside(mesh: Mesh, points: np.ndarray) -> np.ndarray:
    """Point-in-closed-mesh by ray parity along one fixed skew direction (no axis-aligned edge grazing)."""

    v, f = mesh
    tri = v[f]
    e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    h = np.cross(_PARITY_DIRECTION, e2)
    det = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(det) > 1e-12
    tri0, e1, e2, h, inv = tri[ok, 0], e1[ok], e2[ok], h[ok], 1.0 / det[ok]
    result = np.zeros(len(points), dtype=bool)
    for k, p in enumerate(points):
        s = p - tri0
        u = inv * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        w = inv * (q @ _PARITY_DIRECTION)
        t = inv * np.einsum("ij,ij->i", e2, q)
        result[k] = int(np.count_nonzero((u >= 0) & (w >= 0) & (u + w <= 1) & (t > 1e-9))) % 2 == 1
    return result


def surface_area(mesh: Mesh) -> float:
    v, f = mesh
    return float(np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1).sum() / 2.0)


def signature(mesh: Mesh) -> dict[str, Any]:
    """What identifies a solid's shape independently of its triangulation: bounds, enclosed volume, surface area.

    An unchanged B-rep can come back with a different diagonal across a planar
    quad, or (reused from a STEP file) a curved face meshed with a few more
    triangles; these four numbers stay put. Rounded to 1e-6.
    """

    v = mesh[0]
    return {"min": [round(float(x), 6) + 0.0 for x in v.min(axis=0)], "max": [round(float(x), 6) + 0.0 for x in v.max(axis=0)],
            "volume": round(volume(mesh), 6) + 0.0, "area": round(surface_area(mesh), 6) + 0.0}


def same_shape(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Signatures equal within 1e-5 m on bounds and 1e-6 relative on volume and area."""

    bounds = all(abs(p - q) <= 1e-5 for key in ("min", "max") for p, q in zip(a[key], b[key]))
    return bounds and all(abs(a[k] - b[k]) <= max(1e-5, 1e-6 * abs(a[k])) for k in ("volume", "area"))


def _plan_overlap(lo_a, hi_a, lo_b, hi_b, eps: float = 1e-6) -> bool:
    return all(lo_a[i] < hi_b[i] - eps and lo_b[i] < hi_a[i] - eps for i in (0, 1))


# ---------------------------------------------------------------- checks (one per measure.check)

def _require(meshes: dict[str, Mesh], names: list[str]) -> list[str]:
    return [n for n in names if n not in meshes]


def check_support_contact(m, meshes, start):
    """Bottom of upper == top of lower within tolerance, with plan overlap.

    ``each: "upper"`` (default) requires every upper object to sit on some lower
    one; ``each: "lower"`` requires every lower object to carry some upper one.
    """

    missing = _require(meshes, m["lower"] + m["upper"])
    if missing:
        return "unchecked", {"missing": missing}
    tol = float(m.get("tolerance_m", 0.001))
    each_lower = m.get("each", "upper") == "lower"
    worst = 0.0
    unseated = []
    for member in (m["lower"] if each_lower else m["upper"]):
        mlo, mhi = bbox(meshes, [member])
        gaps = []
        for other in (m["upper"] if each_lower else m["lower"]):
            olo, ohi = bbox(meshes, [other])
            if _plan_overlap(mlo, mhi, olo, ohi):
                gaps.append(abs(float(olo[2] - mhi[2])) if each_lower else abs(float(mlo[2] - ohi[2])))
        gap = min(gaps) if gaps else math.inf
        worst = max(worst, gap)
        if gap > tol:
            unseated.append(member)
    return ("violated" if unseated else "held"), {"worst_seam_gap_m": round(worst, 6) if math.isfinite(worst) else None, "unseated": unseated}


def check_vertical_clearance(m, meshes, start):
    """Clear height from the lower objects' top to the upper objects' bottom, where their plans overlap."""

    missing = _require(meshes, m["lower"] + m["upper"])
    if missing:
        return "unchecked", {"missing": missing}
    llo, lhi = bbox(meshes, m["lower"])
    ulo, uhi = bbox(meshes, m["upper"])
    if not _plan_overlap(llo, lhi, ulo, uhi):
        return "unchecked", {"detail": "plans do not overlap; vertical clearance is undefined"}
    clear = float(ulo[2] - lhi[2])
    return ("held" if clear >= float(m["min_m"]) - 1e-6 else "violated"), {"clear_m": round(clear, 6), "min_m": m["min_m"]}


def check_non_interference(m, meshes, start):
    """No common volume: sample the two bounding boxes' intersection and test containment in both."""

    missing = _require(meshes, m["a"] + m["b"])
    if missing:
        return "unchecked", {"missing": missing}
    n = int(m.get("samples_per_axis", 14))
    common_points = 0
    common_volume = 0.0
    for a in m["a"]:
        for b in m["b"]:
            alo, ahi = bbox(meshes, [a])
            blo, bhi = bbox(meshes, [b])
            lo, hi = np.maximum(alo, blo), np.minimum(ahi, bhi)
            if np.any(hi - lo <= 1e-6):
                continue
            axes = [lo[i] + (np.arange(n) + 0.5) * (hi[i] - lo[i]) / n for i in range(3)]
            grid = np.array(np.meshgrid(*axes, indexing="ij")).reshape(3, -1).T
            both = inside(meshes[a], grid) & inside(meshes[b], grid)
            k = int(np.count_nonzero(both))
            common_points += k
            common_volume += k * float(np.prod(hi - lo)) / n ** 3
    held = common_volume <= float(m.get("tolerance_m3", 1e-6))
    return ("held" if held else "violated"), {"common_volume_estimate_m3": round(common_volume, 6), "common_sample_points": common_points}


def check_aperture(m, meshes, start):
    """A through opening in the host, measured from the mesh: x extent, sill and head, and rays through it."""

    host = m["host"]
    if host not in meshes:
        return "unchecked", {"missing": [host]}
    v, _ = meshes[host]
    lo, hi = v.min(axis=0), v.max(axis=0)
    interior = v[(v[:, 0] > lo[0] + 1e-6) & (v[:, 0] < hi[0] - 1e-6)]
    if len(interior) == 0:
        return "violated", {"detail": "the host has no interior edge: no opening"}
    measured = {"x_min": float(interior[:, 0].min()), "x_max": float(interior[:, 0].max()),
                "z_min": float(interior[:, 2].min()), "z_max": float(interior[:, 2].max())}
    tol = float(m.get("tolerance_m", 0.002))
    problems = []
    for key, expected in (("x_min", m["x"][0]), ("x_max", m["x"][1]), ("z_min", m["z"][0]), ("z_max", m["z"][1])):
        if abs(measured[key] - expected) > tol:
            problems.append(f"{key} {measured[key]:.4f} != {expected:.4f}")
    y0, y1 = lo[1] - 0.5, hi[1] + 0.5
    x0, x1 = m["x"]
    z0, z1 = m["z"]
    probes = {
        "open_centre": ((x0 + x1) / 2, (z0 + z1) / 2, False),
        "open_near_left_low": (x0 + 0.02, z0 + 0.05, False),
        "open_near_right_high": (x1 - 0.02, z1 - 0.05, False),
        "blocked_left": (x0 - 0.03, (z0 + z1) / 2, True),
        "blocked_right": (x1 + 0.03, (z0 + z1) / 2, True),
        "blocked_above": ((x0 + x1) / 2, z1 + 0.03, True),
    }
    rays = {}
    for label, (x, z, should_hit) in probes.items():
        hit = segment_hits(meshes[host], np.array([x, y0, z]), np.array([x, y1, z]))
        rays[label] = hit
        if hit != should_hit:
            problems.append(f"ray {label}: {'hit' if hit else 'passed'}")
    measured = {k: round(val, 6) for k, val in measured.items()}
    return ("violated" if problems else "held"), {**measured, "rays_hit": rays, "problems": problems}


def check_bbox(m, meshes, start):
    """An object's bounding box on each stated axis bound (null = not checked)."""

    name = m["object"]
    if name not in meshes:
        return "unchecked", {"missing": [name]}
    lo, hi = bbox(meshes, [name])
    tol = float(m.get("tolerance_m", 0.002))
    problems = []
    for side, values, actual in (("min", m.get("min", [None] * 3), lo), ("max", m.get("max", [None] * 3), hi)):
        for axis, expected in enumerate(values):
            if expected is not None and abs(float(actual[axis]) - float(expected)) > tol:
                problems.append(f"{side}[{'xyz'[axis]}] {float(actual[axis]):.4f} != {float(expected):.4f}")
    return ("violated" if problems else "held"), {"min": [round(float(x), 6) for x in lo], "max": [round(float(x), 6) for x in hi],
                                                  "problems": problems}


def check_within(m, meshes, start):
    """An object's bounding box lies inside a stated region."""

    name = m["object"]
    if name not in meshes:
        return "unchecked", {"missing": [name]}
    lo, hi = bbox(meshes, [name])
    tol = float(m.get("tolerance_m", 0.002))
    region_lo, region_hi = np.array(m["region_min"], dtype=float), np.array(m["region_max"], dtype=float)
    outside = bool(np.any(lo < region_lo - tol) or np.any(hi > region_hi + tol))
    return ("violated" if outside else "held"), {"min": [round(float(x), 6) for x in lo], "max": [round(float(x), 6) for x in hi]}


def check_geometry_unchanged(m, meshes, start):
    """The named objects keep the start model's shape (``same_shape`` on their signatures)."""

    if start is None:
        return "unchecked", {"detail": "no start model"}
    missing = _require(meshes, m["objects"]) + [f"start:{n}" for n in _require(start, m["objects"])]
    if missing:
        return "unchecked", {"missing": missing}
    changed = [n for n in m["objects"] if not same_shape(signature(meshes[n]), signature(start[n]))]
    return ("violated" if changed else "held"), {"changed": changed}


def check_column_row(m, meshes, start):
    """Columns named ``prefix-0..`` in a row along x: count, end positions, equal spacing, clear spacing."""

    names = sorted((n for n in meshes if n.startswith(m["prefix"]) and n[len(m["prefix"]):].isdigit()), key=lambda n: int(n[len(m["prefix"]):]))
    centres = []
    diameters = []
    for n in names:
        lo, hi = bbox(meshes, [n])
        centres.append(float((lo[0] + hi[0]) / 2))
        diameters.append(float(hi[0] - lo[0]))
    centres.sort()
    measured: dict[str, Any] = {"count": len(names), "centres_x": [round(c, 6) for c in centres]}
    problems = []
    if len(names) != int(m["count"]):
        problems.append(f"count {len(names)} != {m['count']}")
    tol = float(m.get("tolerance_m", 0.002))
    if centres:
        if "first_x" in m and abs(centres[0] - m["first_x"]) > tol:
            problems.append(f"first column at {centres[0]:.4f} != {m['first_x']}")
        if "last_x" in m and abs(centres[-1] - m["last_x"]) > tol:
            problems.append(f"last column at {centres[-1]:.4f} != {m['last_x']}")
    if len(centres) >= 2:
        gaps = np.diff(centres)
        clear = gaps - max(diameters)
        measured.update({"spacing_min": round(float(gaps.min()), 6), "spacing_max": round(float(gaps.max()), 6),
                         "clear_spacing_max": round(float(clear.max()), 6)})
        if float(gaps.max() - gaps.min()) > float(m.get("equal_spacing_tolerance_m", 0.005)):
            problems.append("spacing is not equal")
        if "max_clear_spacing_m" in m and float(clear.max()) > float(m["max_clear_spacing_m"]) + 1e-6:
            problems.append(f"clear spacing {float(clear.max()):.4f} > {m['max_clear_spacing_m']}")
    measured["problems"] = problems
    return ("violated" if problems else "held"), measured


def check_stair(m, meshes, start):
    """A straight flight along +x: rises, goings, top level, landing contact with the slab, headroom under the slab."""

    stair, slab = m["object"], m["slab"]
    missing = _require(meshes, [stair, slab])
    if missing:
        return "unchecked", {"missing": missing}
    v, _ = meshes[stair]
    lo, hi = v.min(axis=0), v.max(axis=0)
    levels = np.unique(np.round(v[:, 2], 6))
    stations = np.unique(np.round(v[:, 0], 6))
    rises = np.diff(levels)
    goings = np.diff(stations)
    measured: dict[str, Any] = {"risers": int(len(rises)), "rise_max": round(float(rises.max()), 6), "going_min": round(float(goings.min()), 6),
                                "bottom_z": round(float(lo[2]), 6), "top_z": round(float(hi[2]), 6)}
    tol = float(m.get("tolerance_m", 0.002))
    problems = []
    if abs(lo[2] - m["base_z"]) > tol:
        problems.append("flight does not start on its base level")
    if abs(hi[2] - m["top_z"]) > tol:
        problems.append(f"flight top {hi[2]:.4f} != {m['top_z']}")
    if float(rises.max()) > float(m["max_rise_m"]) + 1e-6:
        problems.append(f"riser {float(rises.max()):.4f} > {m['max_rise_m']}")
    if float(goings.min()) < float(m["min_going_m"]) - 1e-6:
        problems.append(f"going {float(goings.min()):.4f} < {m['min_going_m']}")
    probe = np.array([[hi[0] + 0.01, (lo[1] + hi[1]) / 2, hi[2] - 0.01]])
    landing = bool(inside(meshes[slab], probe)[0])
    measured["landing_contacts_slab"] = landing
    if not landing:
        problems.append("no slab directly beyond the top step")
    for k in range(len(stations) - 1):
        # the tread over [s_k, s_k+1] is the top of the riser standing at s_k
        tread_top = float(v[np.abs(v[:, 0] - stations[k]) < 1e-6][:, 2].max())
        short = False
        for x in (stations[k] + 0.01, (stations[k] + stations[k + 1]) / 2, stations[k + 1] - 0.01):
            for y in (lo[1] + 0.05, (lo[1] + hi[1]) / 2, hi[1] - 0.05):
                ceiling = np.array([x, y, tread_top + float(m["min_headroom_m"])])
                if segment_hits(meshes[slab], np.array([x, y, tread_top + 1e-4]), ceiling):
                    short = True
        if short:
            problems.append(f"headroom below {m['min_headroom_m']} m over the tread at x={stations[k]:.3f}..{stations[k + 1]:.3f}")
    measured["headroom_ok"] = not any(p.startswith("headroom") for p in problems)
    measured["problems"] = problems
    return ("violated" if problems else "held"), measured


CHECKS: dict[str, Callable[[dict, dict, dict | None], tuple[str, dict]]] = {
    "support_contact": check_support_contact,
    "vertical_clearance": check_vertical_clearance,
    "non_interference": check_non_interference,
    "aperture": check_aperture,
    "bbox": check_bbox,
    "within": check_within,
    "geometry_unchanged": check_geometry_unchanged,
    "column_row": check_column_row,
    "stair": check_stair,
}


def judge(task: dict[str, Any], candidate: Path, start: Path | None) -> dict[str, Any]:
    """The oracle report for one candidate model; pure in its three inputs."""

    try:
        meshes = load_meshes(candidate)
        start_meshes = load_meshes(start) if start is not None else None
    except OracleInputError as exc:
        return {"oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION, "task_id": task["task_id"], "reads_metadata": False,
                "verdict": "unchecked", "relations": [], "error": str(exc)}
    return judge_meshes(task, meshes, start_meshes)


def judge_meshes(task: dict[str, Any], meshes: dict[str, Mesh], start_meshes: dict[str, Mesh] | None) -> dict[str, Any]:
    """``judge`` on meshes already read: every protected relation's measure, then the verdict."""

    report: dict[str, Any] = {"oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION, "task_id": task["task_id"], "reads_metadata": False}
    relations = []
    for relation in task["protected_relations"]:
        measure = relation.get("measure")
        if not measure:
            relations.append({"id": relation["id"], "status": "unchecked", "measured": {"detail": "no oracle measure"}})
            continue
        status, measured = CHECKS[measure["check"]](measure, meshes, start_meshes)
        relations.append({"id": relation["id"], "status": status, "measured": measured})
    statuses = {r["status"] for r in relations}
    report["relations"] = relations
    report["object_signatures"] = {name: signature(mesh) for name, mesh in sorted(meshes.items())}
    report["verdict"] = "violated" if "violated" in statuses else "unchecked" if "unchecked" in statuses else "held"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--start", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    task = json.loads(args.task.read_text(encoding="utf-8"))
    report = judge(task, args.candidate, args.start)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
