"""Axonometric line views from triangulated shapes: a depth buffer, not an exact hidden-line solve.

The exact solve (``drawing_elevation.project_model_axis_elevation`` through
``HLRBRep_Algo``) grows much faster than the number of objects: on a synthetic
600-object model it takes about 4 s of a 7 s model view. A view that only has
to be looked at - the model view's axonometric, the Design Tree thumbnail -
does not need that precision. Here every shape is triangulated at half a
pixel, the triangles fill a depth buffer at twice the output resolution, and
the mesh's feature edges (creases, open boundaries and silhouettes) are drawn
where the buffer does not hide them.

Everything is deterministic: the same shapes, frame and size give the same
bytes. The PNG carries only the text chunks the caller names (no time, no
random id). Nothing is written; retained drawings stay with
``drawing_elevation``. numpy comes with the drawing requirements (shapely).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import metadata
import inspect
from io import BytesIO
import math
from typing import Any, Mapping, Sequence

from monkeycad.cad_execution import StepEntry
from monkeycad.backends.occt.errors import OcctBackendError
from monkeycad.backends.occt.preview import _clean, tessellate_shape

RENDERER_NAME = "mesh-lines"
#: Adjacent triangles meeting at more than this angle draw their shared edge.
CREASE_DEGREES = 30.0
#: The angular deflection each face is triangulated with, in radians.
ANGULAR_DEFLECTION = 0.5
#: The largest output side. The full-frame buffers take about 40 bytes per
#: supersampled pixel: some 170 MB at 1024 px, 670 MB at 2048 px.
MAX_SIZE_PX = 2048
_SUPERSAMPLE = 2
# Vertices closer than this share of an object's extent are one vertex.
_WELD = 1e-9
# An edge sample is hidden only behind a surface nearer by more than this many fine pixels.
_DEPTH_TOLERANCE = 1.5
_PNG_COMPRESS_LEVEL = 6
# Candidate pixels (and edge samples) handled at once. Each takes about ten
# eight-byte temporaries, so a chunk stays near 100 MB however large the
# triangles are: a triangle whose rows would exceed it is split into bands.
_CHUNK_PIXELS = 1_000_000
_LIBRARIES = ("cadquery-ocp", "numpy", "pillow")


class MeshViewError(ValueError):
    """The shapes cannot be drawn in this frame."""


@dataclass(frozen=True, slots=True)
class MeshLineView:
    """One drawn view: the PNG, its size and what was drawn."""

    png: bytes
    width: int
    height: int
    triangles: int
    edges_drawn: int


@dataclass(frozen=True, slots=True)
class ObjectMesh:
    """One object's triangles in the model frame and unit."""

    object_id: str
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]


def _renderer_version() -> str:
    """The renderer's name and a digest of the source of everything that reads or draws the shapes.

    Derived, never bumped by hand (#367): editing this module's drawing
    functions, the tessellation (``tessellate_shape``) or the model reader
    (``read_elevation_source``) gives every projection a new key.
    """

    from monkeydiagram import drawing_elevation

    functions = (tessellate_shape, drawing_elevation.read_elevation_source, drawing_elevation._read_native_source,
                 triangulate, _clean, _welded, _feature_edges, _expand, _depth_buffer, mesh_line_view)
    digest = hashlib.sha256()
    for function in functions:
        digest.update(inspect.getsource(function).encode("utf-8"))
    return f"{RENDERER_NAME}-{digest.hexdigest()[:12]}"


def mesh_pipeline() -> dict[str, Any]:
    """Every setting of this module and library that changes the pixels drawn for the same input.

    A cache key that holds this (and the caller's own frame and tolerance)
    changes whenever an upgrade or an edited constant would draw differently.
    """

    def version(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "absent"

    return {"renderer": RENDERER_VERSION, "creaseDegrees": CREASE_DEGREES, "angularDeflection": ANGULAR_DEFLECTION,
            "supersample": _SUPERSAMPLE, "weld": _WELD, "depthTolerance": _DEPTH_TOLERANCE,
            "pngCompressLevel": _PNG_COMPRESS_LEVEL, "libraries": {name: version(name) for name in _LIBRARIES}}


def pixel_size(crop_uv: Sequence[float], size_px: int) -> float:
    """Model units per output pixel when the longer side of ``crop_uv`` spans ``size_px``."""

    u0, v0, u1, v1 = crop_uv
    return max(u1 - u0, v1 - v0) / size_px


def triangulate(entries: Sequence[StepEntry], object_ids: Sequence[str], *, linear_deflection: float,
                ) -> tuple[tuple[ObjectMesh, ...], tuple[str, ...]]:
    """Triangulate the named shapes; an object without faces (a curve) is skipped and named."""

    wanted = set(object_ids)
    meshes, skipped = [], []
    for entry in sorted((entry for entry in entries if entry.name in wanted), key=lambda entry: entry.name):
        try:
            _clean(entry.shape)
            vertices, triangles = tessellate_shape(entry.shape, linear_deflection=linear_deflection,
                                                   angular_deflection=ANGULAR_DEFLECTION)
        except OcctBackendError:
            skipped.append(entry.name)
            continue
        meshes.append(ObjectMesh(entry.name, tuple(vertices), tuple(triangles)))
    return tuple(meshes), tuple(skipped)


def _welded(np, vertices, triangles, tolerance):
    # Faces are triangulated separately; the shared edge discretisation puts
    # their boundary nodes at the same points, so rounding joins them.
    keys = np.round(vertices / tolerance).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    return vertices[first], inverse.reshape(-1)[triangles]


def _feature_edges(np, vertices, triangles, look, crease_cos):
    """Vertex pairs of the edges one object draws: creases, boundaries and silhouettes."""

    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    normals = np.cross(b - a, c - a)
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 0
    triangles, normals = triangles[keep], normals[keep] / lengths[keep, None]
    if not len(triangles):
        return np.zeros((0, 2), dtype=np.int64)
    pairs = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    faces = np.tile(np.arange(len(triangles)), 3)
    pairs.sort(axis=1)
    order = np.lexsort((faces, pairs[:, 1], pairs[:, 0]))
    pairs, faces = pairs[order], faces[order]
    starts = np.flatnonzero(np.r_[True, np.any(pairs[1:] != pairs[:-1], axis=1)])
    counts = np.diff(np.r_[starts, len(pairs)])
    draw = counts != 2  # open boundaries and non-manifold junctions
    two = np.flatnonzero(counts == 2)
    first, second = normals[faces[starts[two]]], normals[faces[starts[two] + 1]]
    facing = np.sign(first @ look) != np.sign(second @ look)
    draw[two] = (np.einsum("ij,ij->i", first, second) < crease_cos) | facing
    return pairs[starts[draw]]


def _expand(np, counts):
    """For runs of the given lengths: each item's run index and its position within the run."""

    owner = np.repeat(np.arange(len(counts)), counts)
    return owner, np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)


def _depth_buffer(np, x, y, z, triangles, width, height):
    """The nearest depth under each pixel centre, scanline by scanline; ``inf`` where nothing is."""

    depth = np.full(width * height, np.inf)
    tx, ty, tz = x[triangles], y[triangles], z[triangles]
    area = (tx[:, 1] - tx[:, 0]) * (ty[:, 2] - ty[:, 0]) - (tx[:, 2] - tx[:, 0]) * (ty[:, 1] - ty[:, 0])
    usable = np.abs(area) > 1e-12
    tx, ty, tz, area = tx[usable], ty[usable], tz[usable], area[usable]
    # Depth is a plane over each triangle: z = gx * x + gy * y + z0.
    dx1, dx2 = tx[:, 1] - tx[:, 0], tx[:, 2] - tx[:, 0]
    dy1, dy2 = ty[:, 1] - ty[:, 0], ty[:, 2] - ty[:, 0]
    dz1, dz2 = tz[:, 1] - tz[:, 0], tz[:, 2] - tz[:, 0]
    gx = (dz1 * dy2 - dz2 * dy1) / area
    gy = (dz2 * dx1 - dz1 * dx2) / area
    z0 = tz[:, 0] - gx * tx[:, 0] - gy * ty[:, 0]
    # Rows whose centre line the triangle crosses, and the most pixels one of
    # them can cover: the triangle's own width, clipped to the image.
    first = np.maximum(np.ceil(ty.min(axis=1) - 0.5), 0).astype(np.int64)
    last = np.minimum(np.floor(ty.max(axis=1) - 0.5), height - 1).astype(np.int64)
    rows = np.maximum(last - first + 1, 0)
    wide = np.floor(tx.max(axis=1) - 0.5) - np.ceil(tx.min(axis=1) - 0.5) + 1
    span = np.clip(wide, 1, width).astype(np.int64)
    # Bands of at most ``band`` rows, so that no band holds more than a chunk.
    band = np.maximum(_CHUNK_PIXELS // span, 1)
    owner, index = _expand(np, -(-rows // band))
    band_first = first[owner] + index * band[owner]
    band_rows = np.minimum(band[owner], rows[owner] - index * band[owner])
    budget = np.cumsum(band_rows * span[owner])
    begin = 0
    while begin < len(owner):
        base = budget[begin - 1] if begin else 0
        end = max(begin + 1, int(np.searchsorted(budget, base + _CHUNK_PIXELS, side="right")))
        chunk = slice(begin, end)
        begin = end
        row_owner, offset = _expand(np, band_rows[chunk])
        tri = owner[chunk][row_owner]
        cy = band_first[chunk][row_owner] + offset + 0.5
        left = np.full(len(tri), np.inf)
        right = np.full(len(tri), -np.inf)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            ya, yb, xa, xb = ty[tri, i], ty[tri, j], tx[tri, i], tx[tri, j]
            crosses = (np.minimum(ya, yb) <= cy) & (cy <= np.maximum(ya, yb)) & (ya != yb)
            at = xa + (cy - ya) * (xb - xa) / np.where(ya != yb, yb - ya, 1.0)
            left = np.where(crosses, np.minimum(left, at), left)
            right = np.where(crosses, np.maximum(right, at), right)
        start = np.maximum(np.ceil(left - 0.5), 0)
        stop = np.minimum(np.floor(right - 0.5), width - 1)
        valid = np.isfinite(left) & (stop >= start)
        tri, cy, start = tri[valid], cy[valid], start[valid].astype(np.int64)
        count = stop[valid].astype(np.int64) - start + 1
        span, step = _expand(np, count)
        px = start[span] + step
        py = (cy[span] - 0.5).astype(np.int64)
        which = tri[span]
        values = gx[which] * (px + 0.5) + gy[which] * cy[span] + z0[which]
        np.minimum.at(depth, py * width + px, values)
    return depth.reshape(height, width)


def mesh_line_view(
    meshes: Sequence[ObjectMesh], *, right: Sequence[float], up: Sequence[float],
    crop_uv: Sequence[float], size_px: int, text: Mapping[str, str] | None = None,
) -> MeshLineView:
    """Draw the visible feature edges of ``meshes`` in the orthographic frame ``right``/``up``.

    ``crop_uv`` is the drawn window ``(u_min, v_min, u_max, v_max)`` with
    ``u = dot(p, right)``, ``v = dot(p, up)``; its longer side spans
    ``size_px`` pixels. The look direction is ``-(right x up)``. The PNG is
    greyscale, black lines on white, with ``text`` as its tEXt chunks.
    """

    import numpy as np
    from PIL import Image, PngImagePlugin

    if isinstance(size_px, bool) or not isinstance(size_px, int) or not 16 <= size_px <= MAX_SIZE_PX:
        raise MeshViewError(f"size_px must be a whole number of pixels from 16 to {MAX_SIZE_PX}")
    u0, v0, u1, v1 = (float(value) for value in crop_uv)
    if not all(math.isfinite(value) for value in (u0, v0, u1, v1)) or not (u0 < u1 and v0 < v1):
        raise MeshViewError("crop_uv must be finite with u_min < u_max and v_min < v_max")
    right_v, up_v = np.asarray(right, dtype=float), np.asarray(up, dtype=float)
    look = -np.cross(right_v, up_v)
    unit = pixel_size((u0, v0, u1, v1), size_px)
    width = max(1, min(size_px, math.floor((u1 - u0) / unit + 1e-9)))
    height = max(1, min(size_px, math.floor((v1 - v0) / unit + 1e-9)))
    fine = unit / _SUPERSAMPLE
    big_w, big_h = width * _SUPERSAMPLE, height * _SUPERSAMPLE
    crease_cos = math.cos(math.radians(CREASE_DEGREES))

    xs, ys, zs, tris, edges = [], [], [], [], []
    base = 0
    for mesh in meshes:
        vertices = np.asarray(mesh.vertices, dtype=float).reshape(-1, 3)
        triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
        if not len(vertices) or not len(triangles):
            continue
        extent = float(np.ptp(vertices, axis=0).max()) or 1.0
        vertices, triangles = _welded(np, vertices, triangles, extent * _WELD)
        edges.append(_feature_edges(np, vertices, triangles, look, crease_cos) + base)
        xs.append((vertices @ right_v - u0) / fine)
        ys.append((v1 - vertices @ up_v) / fine)
        zs.append(vertices @ look)
        tris.append(triangles + base)
        base += len(vertices)
    image = np.full((big_h, big_w), 255, dtype=np.uint8)
    drawn = 0
    triangle_count = sum(len(item) for item in tris)
    if base:
        x, y, z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
        depth = _depth_buffer(np, x, y, z, np.concatenate(tris), big_w, big_h)
        # The farthest depth around each pixel: an edge lies on the border of
        # the faces it bounds, so its own pixel may sample the face beside it.
        padded = np.pad(depth, 1, mode="edge")
        rows = np.maximum(np.maximum(padded[:-2], padded[1:-1]), padded[2:])
        farthest = np.maximum(np.maximum(rows[:, :-2], rows[:, 1:-1]), rows[:, 2:])
        del padded, rows
        pairs = np.concatenate(edges)
        drawn = len(pairs)
        ax, ay, az = x[pairs[:, 0]], y[pairs[:, 0]], z[pairs[:, 0]]
        bx, by, bz = x[pairs[:, 1]], y[pairs[:, 1]], z[pairs[:, 1]]
        steps = np.ceil(np.maximum(np.abs(bx - ax), np.abs(by - ay))).astype(np.int64) + 1
        steps = np.minimum(steps, 4 * (big_w + big_h))
        budget = np.cumsum(steps)
        begin = 0
        while begin < drawn:
            # Edges in chunks of at most _CHUNK_PIXELS samples (or one edge).
            base = budget[begin - 1] if begin else 0
            end = max(begin + 1, int(np.searchsorted(budget, base + _CHUNK_PIXELS, side="right")))
            chunk = slice(begin, end)
            begin = end
            owner, step = _expand(np, steps[chunk])
            edge = owner + chunk.start
            t = step / np.maximum(steps[edge] - 1, 1)
            px = np.floor(ax[edge] + (bx - ax)[edge] * t).astype(np.int64)
            py = np.floor(ay[edge] + (by - ay)[edge] * t).astype(np.int64)
            sz = az[edge] + (bz - az)[edge] * t
            inside = (px >= 0) & (px < big_w) & (py >= 0) & (py < big_h)
            px, py, sz = px[inside], py[inside], sz[inside]
            # Depth tolerance: one and a half fine pixels in model units, the
            # most a face sloped at 55 degrees moves within one pixel's reach.
            seen = sz <= farthest[py, px] + _DEPTH_TOLERANCE * fine
            px, py = px[seen], py[seen]
            # Two fine pixels wide, so the reduced line is one full pixel.
            image[py, px] = 0
            image[py, np.minimum(px + 1, big_w - 1)] = 0
            image[np.minimum(py + 1, big_h - 1), px] = 0
    picture = Image.fromarray(image).reduce(_SUPERSAMPLE)
    info = PngImagePlugin.PngInfo()
    for key, value in sorted((text or {}).items()):
        info.add_text(key, value)
    buffer = BytesIO()
    picture.save(buffer, format="PNG", pnginfo=info, compress_level=_PNG_COMPRESS_LEVEL)
    return MeshLineView(buffer.getvalue(), width, height, triangle_count, drawn)


#: Changes whenever the pixels this module draws for the same input change: derived from the source.
RENDERER_VERSION = _renderer_version()
