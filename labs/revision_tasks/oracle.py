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
    """Every mesh object by its name, from a .3dm, a .glb, an .ifc or a .usda; a duplicated or empty name is an input error."""

    if Path(path).suffix.lower() == ".glb":
        return _load_glb(Path(path))
    if Path(path).suffix.lower() == ".ifc":
        return _load_ifc(Path(path))
    if Path(path).suffix.lower() == ".usda":
        return _load_usda(Path(path))
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


_COMPONENTS = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "<u1"}
_WIDTH = {"SCALAR": 1, "VEC3": 3}
# glTF is y-up; the oracle's frame is the model file's, z-up: (x, y, z) = (x_g, -z_g, y_g), a proper rotation.
_GLTF_TO_MODEL = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])


def _load_glb(path: Path) -> dict[str, Mesh]:
    """Named triangle meshes from a glTF 2.0 binary, read with struct and numpy only; node transforms are refused."""

    import struct

    data = path.read_bytes()
    if len(data) < 20 or struct.unpack_from("<III", data, 0) != (0x46546C67, 2, len(data)):
        raise OracleInputError(f"{path.name}: not a glTF 2.0 binary")
    chunks, offset = [], 12
    while offset < len(data):
        n, kind = struct.unpack_from("<II", data, offset)
        chunks.append((kind, data[offset + 8: offset + 8 + n]))
        offset += 8 + n
    document = json.loads(chunks[0][1].decode("utf-8").rstrip(" \0"))
    binary = chunks[1][1] if len(chunks) > 1 else b""

    def accessor(index: int) -> np.ndarray:
        a = document["accessors"][index]
        view = document["bufferViews"][a["bufferView"]]
        dtype, width = np.dtype(_COMPONENTS[a["componentType"]]), _WIDTH[a["type"]]
        if view.get("byteStride") not in (None, dtype.itemsize * width):
            raise OracleInputError(f"{path.name}: strided accessors are not read")
        array = np.frombuffer(binary, dtype=dtype, count=a["count"] * width, offset=view.get("byteOffset", 0) + a.get("byteOffset", 0))
        return array.reshape(a["count"], width) if width > 1 else array

    meshes: dict[str, Mesh] = {}
    for node in document.get("nodes", []):
        if "mesh" not in node:
            continue
        name = node.get("name")
        if not name:
            raise OracleInputError("an unnamed mesh node has no identity")
        if name in meshes:
            raise OracleInputError(f"mesh name {name!r} occurs twice")
        if any(key in node for key in ("matrix", "translation", "rotation", "scale")):
            raise OracleInputError(f"node {name!r} carries a transform")
        vertices, triangles = [], []
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            if primitive.get("mode", 4) != 4:
                raise OracleInputError(f"{name}: only triangle primitives are read")
            v = accessor(primitive["attributes"]["POSITION"]).astype(np.float64) @ _GLTF_TO_MODEL.T
            f = accessor(primitive["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in primitive else np.arange(len(v)).reshape(-1, 3)
            triangles.append(f + sum(len(x) for x in vertices))
            vertices.append(v)
        meshes[name] = (np.vstack(vertices), np.vstack(triangles))
    return meshes


def _step_args(text: str) -> list[str]:
    """Top-level arguments of one STEP entity, split at commas outside parentheses and quoted strings."""

    parts, depth, quoted, current, i = [], 0, False, [], 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if quoted and text[i + 1: i + 2] == "'":       # an escaped quote inside a string
                current.append("''")
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current, i = [], i + 1
                continue
        current.append(ch)
        i += 1
    parts.append("".join(current).strip())
    return parts


def _load_ifc(path: Path) -> dict[str, Mesh]:
    """Named triangle meshes from an IFC4 STEP file, read as text: products whose body is an ``IfcTriangulatedFaceSet``
    under identity placements in metres. Anything else (another unit, a moved placement, another body) is refused."""

    import re

    entities: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="ascii", errors="strict").splitlines():
        match = re.match(r"^(#\d+)\s*=\s*([A-Z0-9]+)\((.*)\);\s*$", line)
        if match:
            entities[match.group(1)] = (match.group(2), match.group(3))
    if not entities:
        raise OracleInputError(f"{path.name}: not a STEP file")
    units = [args for kind, args in entities.values() if kind == "IFCSIUNIT" and ".LENGTHUNIT." in args]
    if units != ["*,.LENGTHUNIT.,$,.METRE."]:
        raise OracleInputError(f"{path.name}: the length unit is not plain metres")
    number = r"[-+]?\d+\.?\d*(?:[Ee][-+]?\d+)?"

    def identity_placement(ref: str) -> bool:
        while ref != "$":
            kind, args = entities[ref]
            if kind != "IFCLOCALPLACEMENT":
                return False
            parent, relative = _step_args(args)
            kind, args = entities[relative]
            location, *axes = _step_args(args)
            if kind != "IFCAXIS2PLACEMENT3D" or any(a != "$" for a in axes):
                return False
            if any(float(v) != 0.0 for v in re.findall(number, entities[location][1])):
                return False
            ref = parent
        return True

    meshes: dict[str, Mesh] = {}
    for kind, args in entities.values():
        if kind != "IFCBUILDINGELEMENTPROXY":
            continue
        fields = _step_args(args)
        name = fields[2][1:-1].replace("''", "'") if fields[2].startswith("'") else ""
        if not name:
            raise OracleInputError("an unnamed product has no identity")
        if name in meshes:
            raise OracleInputError(f"product name {name!r} occurs twice")
        if not identity_placement(fields[5]):
            raise OracleInputError(f"{name}: placement is not the identity")
        vertices, triangles = [], []
        for representation in re.findall(r"#\d+", _step_args(entities[fields[6]][1])[2]):
            for item in re.findall(r"#\d+", _step_args(entities[representation][1])[3]):
                item_kind, item_args = entities[item]
                if item_kind != "IFCTRIANGULATEDFACESET":
                    raise OracleInputError(f"{name}: body {item_kind} is not read")
                coordinates, _, _, index, _ = _step_args(item_args)
                v = np.array([float(x) for x in re.findall(number, entities[coordinates][1])], dtype=np.float64).reshape(-1, 3)
                f = np.array([int(x) for x in re.findall(r"\d+", index)], dtype=np.int64).reshape(-1, 3) - 1
                triangles.append(f + sum(len(x) for x in vertices))
                vertices.append(v)
        meshes[name] = (np.vstack(vertices), np.vstack(triangles))
    return meshes


def _load_usda(path: Path) -> dict[str, Mesh]:
    """Named meshes from a USD text layer, read as text: each ``def Mesh`` block's ``objectName`` string (its identity:
    a prim name cannot hold a hyphen), points and faces. A layer that is not z-up in metres, or a mesh with a transform
    op, is refused."""

    import re

    text = path.read_text(encoding="utf-8")
    header = text.split("def ", 1)[0]
    if 'upAxis = "Z"' not in header or not re.search(r"metersPerUnit = 1(\.0)?\b", header):
        raise OracleInputError(f"{path.name}: not z-up in metres")
    meshes: dict[str, Mesh] = {}
    for block in re.split(r'\n\s*def Mesh "', text)[1:]:
        body = block.split("\n    }", 1)[0]
        if "xformOp" in body:
            raise OracleInputError(f"{path.name}: a mesh carries a transform")
        name = re.search(r'custom string objectName = "([^"]*)"', body)
        counts = re.search(r"int\[\] faceVertexCounts = \[([^\]]*)\]", body)
        indices = re.search(r"int\[\] faceVertexIndices = \[([^\]]*)\]", body)
        points = re.search(r"point3f\[\] points = \[(.*?)\](?:\n|$)", body, re.S)
        if not (name and counts and indices and points):
            raise OracleInputError(f"{path.name}: a mesh without a name, faces or points")
        if name.group(1) in meshes:
            raise OracleInputError(f"mesh name {name.group(1)!r} occurs twice")
        number = r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?"
        v = np.array([float(x) for x in re.findall(number, points.group(1))], dtype=np.float64).reshape(-1, 3)
        flat = [int(x) for x in re.findall(r"\d+", indices.group(1))]
        triangles, k = [], 0
        for n in (int(x) for x in re.findall(r"\d+", counts.group(1))):
            face = flat[k:k + n]
            k += n
            triangles += [[face[0], face[i], face[i + 1]] for i in range(1, n - 1)]
        meshes[name.group(1)] = (v, np.array(triangles, dtype=np.int64).reshape(-1, 3))
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
        measured["clear_spacing_min"] = round(float(clear.min()), 6)
        if "min_clear_spacing_m" in m and float(clear.min()) < float(m["min_clear_spacing_m"]) - 1e-6:
            problems.append(f"clear spacing {float(clear.min()):.4f} < {m['min_clear_spacing_m']}")
        if "diameter_m" in m and any(abs(d - float(m["diameter_m"])) > tol for d in diameters):
            problems.append(f"column diameter differs from {m['diameter_m']}")
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


# ---------------------------------------------------------------- building checks (X3): openings, lintels, flights

def _is_cavity(name: str) -> bool:
    """A void, not material: an opening's cavity or a stair hole, recognised by its name (identity, not metadata)."""

    return "-aperture-" in name or name.startswith("obj-hole-")


def first_hit(meshes: dict[str, Mesh], start: np.ndarray, direction: np.ndarray, reach: float, skip: set[str]) -> tuple[float, str | None]:
    """Distance along a unit ray to the nearest material surface within ``reach``, and whose it is."""

    best, owner = math.inf, None
    end = start + direction * reach
    lo_ray, hi_ray = np.minimum(start, end), np.maximum(start, end)
    for name, (v, f) in meshes.items():
        if name in skip or _is_cavity(name):
            continue
        lo, hi = v.min(axis=0), v.max(axis=0)
        if np.any(hi < lo_ray - 1e-9) or np.any(lo > hi_ray + 1e-9):
            continue
        tri = v[f]
        e1, e2 = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        h = np.cross(direction, e2)
        det = np.einsum("ij,ij->i", e1, h)
        ok = np.abs(det) > 1e-12
        if not np.any(ok):
            continue
        inv = 1.0 / det[ok]
        s = start - tri[ok, 0]
        u = inv * np.einsum("ij,ij->i", s, h[ok])
        q = np.cross(s, e1[ok])
        w = inv * (q @ direction)
        t = inv * np.einsum("ij,ij->i", e2[ok], q)
        hits = t[(u >= -1e-9) & (w >= -1e-9) & (u + w <= 1 + 1e-9) & (t > 1e-7) & (t <= reach)]
        if len(hits) and float(hits.min()) < best:
            best, owner = float(hits.min()), name
    return best, owner


def wall_openings(mesh: Mesh) -> tuple[int, float, float, list[tuple[float, float, float, float]]]:
    """A straight wall's run axis (0 x, 1 y), its run extent, and each through opening as (u0, u1, z0, z1).

    Breakpoints come from the mesh's own vertices; each cell between them is probed with one ray through the
    wall's thickness, so an opening's edges are exact.
    """

    v, _ = mesh
    lo, hi = v.min(axis=0), v.max(axis=0)
    axis = 0 if hi[0] - lo[0] >= hi[1] - lo[1] else 1
    across = 1 - axis
    us, zs = np.unique(np.round(v[:, axis], 6)), np.unique(np.round(v[:, 2], 6))
    open_cells = []
    for u0, u1 in zip(us, us[1:]):
        for z0, z1 in zip(zs, zs[1:]):
            start, end = np.zeros(3), np.zeros(3)
            start[axis] = end[axis] = (u0 + u1) / 2
            start[2] = end[2] = (z0 + z1) / 2
            start[across], end[across] = lo[across] - 0.5, hi[across] + 0.5
            if not segment_hits(mesh, start, end):
                open_cells.append((float(u0), float(u1), float(z0), float(z1)))
    openings: list[list[float]] = []
    for u0, u1, z0, z1 in sorted(open_cells):
        if openings and abs(openings[-1][1] - u0) < 1e-6 or openings and abs(openings[-1][0] - u0) < 1e-6:
            last = openings[-1]
            last[1], last[2], last[3] = max(last[1], u1), min(last[2], z0), max(last[3], z1)
        else:
            openings.append([u0, u1, z0, z1])
    return axis, float(lo[axis]), float(hi[axis]), [tuple(o) for o in openings]


def check_wall_piers(m, meshes, start):
    """K3: wall left beside every opening, along the wall to the next opening or the wall's end, at least ``min_m``."""

    walls = sorted(n for n in meshes if n.startswith(m.get("walls_prefix", "obj-wall-")) and not _is_cavity(n))
    worst, where, below = math.inf, None, []
    for wall in walls:
        _, start_u, end_u, openings = wall_openings(meshes[wall])
        edges = [start_u] + [x for o in openings for x in (o[0], o[1])] + [end_u]
        for k, o in enumerate(openings):
            left, right = o[0] - edges[2 * k], edges[2 * k + 3] - o[1]
            for side, pier in (("left", left), ("right", right)):
                if pier < worst:
                    worst, where = pier, f"{wall} opening {o[0]:.3f}-{o[1]:.3f} {side}"
                if pier < float(m["min_m"]) - 1e-6:
                    below.append({"wall": wall, "opening": [round(o[0], 6), round(o[1], 6)], "side": side, "pier_m": round(pier, 6)})
    return ("violated" if below else "held"), {"min_pier_m": round(worst, 6) if math.isfinite(worst) else None, "at": where, "below": below}


def check_wall_above_lintels(m, meshes, start):
    """K4: each lintel's host wall (the wall holding its plan) reaches at least ``min_m`` above the lintel's top."""

    walls = [n for n in meshes if n.startswith(m.get("walls_prefix", "obj-wall-")) and not _is_cavity(n)]
    worst, where, below, orphans = math.inf, None, [], []
    for lintel in sorted(n for n in meshes if n.startswith(m.get("lintels_prefix", "obj-lintel-"))):
        llo, lhi = bbox(meshes, [lintel])
        hosts = []
        for wall in walls:
            wlo, whi = bbox(meshes, [wall])
            if all(wlo[i] - 1e-6 <= llo[i] and lhi[i] <= whi[i] + 1e-6 for i in (0, 1)) and wlo[2] <= llo[2] <= whi[2]:
                hosts.append((float(whi[2] - lhi[2]), wall))
        if not hosts:
            orphans.append(lintel)
            continue
        above, wall = min(hosts)
        if above < worst:
            worst, where = above, f"{lintel} in {wall}"
        if above < float(m["min_m"]) - 1e-6:
            below.append({"lintel": lintel, "wall": wall, "wall_above_m": round(above, 6)})
    status = "violated" if below else "unchecked" if orphans else "held"
    return status, {"min_wall_above_m": round(worst, 6) if math.isfinite(worst) else None, "at": where, "below": below, "no_host": orphans}


def _flight(mesh: Mesh) -> dict[str, Any]:
    """A straight stepped flight: its run axis, treads (z, u0, u1) in climbing order, plan extents."""

    v, f = mesh
    lo, hi = v.min(axis=0), v.max(axis=0)
    axis = 0 if hi[0] - lo[0] >= hi[1] - lo[1] else 1
    tri = v[f]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    up = (normal[:, 2] > 0.999 * length) & (length > 1e-12)
    treads: dict[float, list[float]] = {}
    for t in tri[up]:
        z = round(float(t[:, 2].mean()), 6)
        span = treads.setdefault(z, [math.inf, -math.inf])
        span[0], span[1] = min(span[0], float(t[:, axis].min())), max(span[1], float(t[:, axis].max()))
    rows = sorted((z, s[0], s[1]) for z, s in treads.items())
    climbing = 1.0 if len(rows) < 2 or (rows[-1][1] + rows[-1][2]) >= (rows[0][1] + rows[0][2]) else -1.0
    return {"axis": axis, "treads": rows, "lo": lo, "hi": hi, "sense": climbing}


def check_flight(m, meshes, start):
    """R7 and K6 for one flight: risers, rise and going limits, and headroom over the usable part of every tread.

    Headroom is measured straight up from points on each tread to the nearest other material (cavities and the
    flight itself left out). A point buried in other material is not usable and is not measured.
    """

    name = m["object"]
    if name not in meshes:
        return "unchecked", {"missing": [name]}
    flight = _flight(meshes[name])
    rows, axis = flight["treads"], flight["axis"]
    across = 1 - axis
    rises = np.diff([flight["lo"][2]] + [z for z, _, _ in rows])
    goings = [u1 - u0 for _, u0, u1 in rows]
    measured: dict[str, Any] = {"risers": len(rows), "rise_max": round(float(rises.max()), 6), "going_min": round(float(min(goings)), 6),
                                "top_z": round(float(rows[-1][0]), 6)}
    problems = []
    if "risers" in m and len(rows) != int(m["risers"]):
        problems.append(f"{len(rows)} risers != {m['risers']}")
    if float(rises.max()) > float(m["max_rise_m"]) + 1e-6:
        problems.append(f"riser {float(rises.max()):.5f} > {m['max_rise_m']}")
    if float(min(goings)) < float(m["min_going_m"]) - 1e-6:
        problems.append(f"going {float(min(goings)):.5f} < {m['min_going_m']}")
    others = {k: mesh for k, mesh in meshes.items() if k != name and not _is_cavity(k)}
    headroom, low_at = math.inf, None
    a0, a1 = float(flight["lo"][across]), float(flight["hi"][across])
    for z, u0, u1 in rows:
        for fu in (0.1, 0.5, 0.9):
            for fa in (0.05, 0.25, 0.5, 0.75, 0.95):
                point = np.zeros(3)
                point[axis], point[across], point[2] = u0 + fu * (u1 - u0), a0 + fa * (a1 - a0), z + 1e-4
                if any(inside(mesh, point[None, :])[0] for mesh in others.values() if np.all(mesh[0].min(axis=0) <= point) and np.all(mesh[0].max(axis=0) >= point)):
                    continue                       # buried: not a usable part of the tread
                clear, _ = first_hit(others, point, np.array([0.0, 0.0, 1.0]), 50.0, set())
                if clear < headroom:
                    headroom, low_at = clear, round(z, 6)
    measured["headroom_min_m"] = round(headroom, 6) if math.isfinite(headroom) else None
    measured["headroom_lowest_at_tread_z"] = low_at
    if "min_headroom_m" in m and headroom < float(m["min_headroom_m"]) - 1e-6:
        problems.append(f"headroom {headroom:.4f} < {m['min_headroom_m']}")
    measured["problems"] = problems
    return ("violated" if problems else "held"), measured


def check_landing_depth(m, meshes, start):
    """R8: from each end of a flight, along its run, the clear depth to the facing material at ``probe_height_m`` above the landing."""

    name = m["object"]
    if name not in meshes:
        return "unchecked", {"missing": [name]}
    flight = _flight(meshes[name])
    axis, across, rows = flight["axis"], 1 - flight["axis"], flight["treads"]
    mid = float(flight["lo"][across] + flight["hi"][across]) / 2
    sense = flight["sense"]
    ends = {"low": (min(rows[0][1], rows[0][2]) if sense > 0 else max(rows[0][1], rows[0][2]), float(flight["lo"][2]), -sense),
            "high": (max(rows[-1][1], rows[-1][2]) if sense > 0 else min(rows[-1][1], rows[-1][2]), float(rows[-1][0]), sense)}
    depths = {}
    for label, (u, z, direction) in ends.items():
        point, ray = np.zeros(3), np.zeros(3)
        point[axis], point[across], point[2] = u + direction * 1e-4, mid, z + float(m.get("probe_height_m", 0.3))
        ray[axis] = direction
        depth, owner = first_hit(meshes, point, ray, 50.0, {name})
        depth += 1e-4                                       # the probe starts that far beyond the flight's end
        depths[label] = {"depth_m": round(depth, 6) if math.isfinite(depth) else None, "facing": owner}
    short = [k for k, d in depths.items() if d["depth_m"] is None or d["depth_m"] < float(m["min_m"]) - 1e-6]
    return ("violated" if short else "held"), {"ends": depths, "short": short}


def check_door_beside_flight(m, meshes, start):
    """R9: along the host wall, each door opening does not overlap the flight beside it (gap at least ``min_gap_m``)."""

    gaps, problems = [], []
    for pair in m["pairs"]:
        wall, flight = pair["wall"], pair["flight"]
        missing = _require(meshes, [wall, flight])
        if missing:
            return "unchecked", {"missing": missing}
        axis, _, _, openings = wall_openings(meshes[wall])
        doors = [o for o in openings if o[2] <= float(bbox(meshes, [wall])[0][2]) + 0.05]   # openings from the floor up
        flo, fhi = bbox(meshes, [flight])
        for o in doors:
            gap = max(float(flo[axis]) - o[1], o[0] - float(fhi[axis]))
            gaps.append({"wall": wall, "flight": flight, "door": [round(o[0], 6), round(o[1], 6)], "gap_m": round(gap, 6)})
            if gap < float(m.get("min_gap_m", 0.0)) - 1e-6:
                problems.append(f"{wall} door {o[0]:.3f}-{o[1]:.3f} overlaps {flight} by {-gap:.3f}")
    return ("violated" if problems else "held"), {"pairs": gaps, "problems": problems}


def surface_distance(mesh: Mesh, points: np.ndarray, chunk: int = 500_000) -> np.ndarray:
    """Each point's distance to the mesh's surface: the nearest of its triangles, each by its plane where the point's
    projection falls inside it, else by its nearest edge (degenerate triangles by their edges)."""

    v, f = mesh
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    normal = np.cross(b - a, c - a)
    length = np.linalg.norm(normal, axis=1)
    flat = length > 1e-15
    unit = np.zeros_like(normal)
    unit[flat] = normal[flat] / length[flat, None]
    edges = ((a, b), (b, c), (c, a))
    out = np.empty(len(points))
    step = max(1, chunk // max(1, len(f)))
    for start in range(0, len(points), step):
        p = points[start:start + step][:, None, :]                       # (P, 1, 3) against (T, 3)
        best = np.full((p.shape[0], len(f)), np.inf)
        for e0, e1 in edges:
            d = e1 - e0
            t = np.clip(np.einsum("ptk,tk->pt", p - e0, d) / np.maximum(np.einsum("tk,tk->t", d, d), 1e-30), 0.0, 1.0)
            best = np.minimum(best, np.linalg.norm(p - (e0 + t[..., None] * d), axis=2))
        height = np.einsum("ptk,tk->pt", p - a, unit)
        q = p - height[..., None] * unit
        inside_face = flat & np.all([np.einsum("ptk,tk->pt", np.cross(e1 - e0, q - e0), unit) >= -1e-12 for e0, e1 in edges], axis=0)
        best = np.where(inside_face, np.minimum(best, np.abs(height)), best)
        out[start:start + step] = best.min(axis=1)
    return out


def check_collisions(m, meshes, start):
    """Every pair of material solids sharing volume, largest first (sampled like ``non_interference``); pairs whose
    names match an ``allowed`` prefix pair (an embedding the drawing convention accepts) are listed apart.

    Optional, off unless ``seams`` is given: for a pair matching a ``seams`` prefix pair (matched as ``allowed`` is: two
    curved faces declared to meet there), a sample point inside both solids counts only if its depth, the smaller of
    its distances to the two surfaces, exceeds ``seam_depth_m``. The points that do not are reported per pair under
    ``seam_points_within_tolerance`` (with the volume they would have counted), never dropped silently. Without
    ``seams`` the check and its result are as before."""

    names = sorted(n for n in meshes if not _is_cavity(n))
    allowed = [tuple(p) for p in m.get("allowed", [])]
    seams = [tuple(p) for p in m.get("seams", [])]
    seam_depth = float(m.get("seam_depth_m", 0.0))
    n = int(m.get("samples_per_axis", 14))
    boxes = {k: bbox(meshes, [k]) for k in names}
    found, accepted, within = [], 0, []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            lo, hi = np.maximum(boxes[a][0], boxes[b][0]), np.minimum(boxes[a][1], boxes[b][1])
            if np.any(hi - lo <= 1e-4):
                continue
            if any((a.startswith(p) and b.startswith(q)) or (a.startswith(q) and b.startswith(p)) for p, q in allowed):
                accepted += 1
                continue
            axes = [lo[k] + (np.arange(n) + 0.5) * (hi[k] - lo[k]) / n for k in range(3)]
            grid = np.array(np.meshgrid(*axes, indexing="ij")).reshape(3, -1).T
            common = inside(meshes[a], grid) & inside(meshes[b], grid)
            both = int(np.count_nonzero(common))
            cell = float(np.prod(hi - lo)) / n ** 3
            if both and any((a.startswith(p) and b.startswith(q)) or (a.startswith(q) and b.startswith(p)) for p, q in seams):
                points = grid[common]
                depth = np.minimum(surface_distance(meshes[a], points), surface_distance(meshes[b], points))
                shallow = int(np.count_nonzero(depth <= seam_depth))
                if shallow:
                    within.append({"a": a, "b": b, "points": shallow, "max_depth_m": round(float(depth[depth <= seam_depth].max()), 6),
                                   "volume_estimate_m3": round(shallow * cell, 6)})
                both -= shallow
            if both:
                found.append({"a": a, "b": b, "common_volume_estimate_m3": round(both * float(np.prod(hi - lo)) / n ** 3, 6)})
    found.sort(key=lambda row: -row["common_volume_estimate_m3"])
    tolerance = float(m.get("tolerance_m3", 1e-6))
    held = all(row["common_volume_estimate_m3"] <= tolerance for row in found)
    result = {"collisions": found, "allowed_embeddings": accepted}
    if seams:
        result.update(seam_depth_m=seam_depth, seam_points_within_tolerance=sorted(within, key=lambda row: -row["volume_estimate_m3"]))
    return ("held" if held else "violated"), result


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
    "wall_piers": check_wall_piers,
    "wall_above_lintels": check_wall_above_lintels,
    "flight": check_flight,
    "landing_depth": check_landing_depth,
    "door_beside_flight": check_door_beside_flight,
    "collisions": check_collisions,
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
