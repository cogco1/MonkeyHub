"""Axonometric line views from triangulated shapes: a depth buffer, not an exact hidden-line solve.

The exact solve (``views.project_model_axis_elevation`` through
``HLRBRep_Algo``) grows much faster than the number of objects: on a synthetic
600-object model it takes about 4 s of a 7 s model view. A view that only has
to be looked at - the model view's axonometric, the Design Tree thumbnail -
does not need that precision. Here every shape is triangulated at half a
pixel, the triangles fill a depth buffer at twice the output resolution, and
the mesh's feature edges (creases, open boundaries and silhouettes) are drawn
where the buffer does not hide them.

Given a colour for each object, the same buffer also says which object is
nearest under each pixel, and that object's visible surface is filled flat
with its colour, without light, under the same edges: the model view's
material display (#580). An object without a colour is drawn plainly as
unknown, grey under a hatch, never in a colour it was not given.

Everything is deterministic: the same shapes, frame and size give the same
bytes. The PNG carries only the text chunks the caller names (no time, no
random id). Nothing is written; retained drawings stay with
``drawing_runs``. numpy comes with the drawing requirements (shapely).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import metadata
import inspect
from io import BytesIO
import math
from typing import Any, Mapping, Sequence

from monkeycad.backends.occt.step import StepEntry
from monkeycad.backends.occt.errors import OcctBackendError
from monkeycad.backends.occt.preview import clear_shape_triangulation, tessellate_shape

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
# Surfaces of two objects nearer than this share of the frame's longer side
# are at one depth: a filled view gives the pixel to the lower label, so
# coplanar faces of two objects never speckle. Real layers lie far apart.
_TIE = 1e-6
_PNG_COMPRESS_LEVEL = 6
# Candidate pixels (and edge samples) handled at once. Each takes about ten
# eight-byte temporaries, so a chunk stays near 100 MB however large the
# triangles are: a triangle whose rows would exceed it is split into bands.
_CHUNK_PIXELS = 1_000_000
_LIBRARIES = ("cadquery-ocp", "numpy", "pillow")
#: A filled view's paper and lines, as sRGB triples.
FILL_BACKGROUND = (255, 255, 255)
FILL_EDGE = (38, 38, 38)
#: An object given no colour: a neutral grey under 45-degree hatch lines,
#: HATCH_WIDTH of every HATCH_SPACING output pixels, so it never reads as a
#: material colour.
UNKNOWN_FILL = (200, 200, 200)
UNKNOWN_HATCH = (120, 120, 120)
HATCH_SPACING = 8
HATCH_WIDTH = 2


class MeshViewError(ValueError):
    """The shapes cannot be drawn in this frame."""


@dataclass(frozen=True, slots=True)
class MeshLineView:
    """One drawn view: the PNG, its size and what was drawn.

    ``seen`` names, in a filled view, each object whose surface shows in at
    least one pixel; a line view leaves it empty.
    """

    png: bytes
    width: int
    height: int
    triangles: int
    edges_drawn: int
    seen: tuple[str, ...] = ()


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

    from monkeydiagram import sources

    functions = (tessellate_shape, sources.read_elevation_source, sources.read_native_source,
                 triangulate, clear_shape_triangulation, _welded, _feature_edges, _expand, _coverage, _depth_buffer, _front_objects,
                 mesh_line_view)
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
            "supersample": _SUPERSAMPLE, "weld": _WELD, "depthTolerance": _DEPTH_TOLERANCE, "tie": _TIE,
            "pngCompressLevel": _PNG_COMPRESS_LEVEL, "libraries": {name: version(name) for name in _LIBRARIES},
            "fill": {"background": list(FILL_BACKGROUND), "edge": list(FILL_EDGE), "unknown": list(UNKNOWN_FILL),
                     "hatch": list(UNKNOWN_HATCH), "hatchSpacing": HATCH_SPACING, "hatchWidth": HATCH_WIDTH}}


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
            clear_shape_triangulation(entry.shape)
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


def _coverage(np, x, y, z, triangles, width, height):
    """Every pixel centre each triangle covers, scanline by scanline, one bounded chunk at a time.

    Yields ``(pixel, triangle, depth)`` arrays: the flat pixel index, the row
    of ``triangles`` covering it and that triangle's depth there. The same
    input yields the same chunks with the same depths, so a second pass can
    find the triangles a first pass found nearest.
    """

    tx, ty, tz = x[triangles], y[triangles], z[triangles]
    area = (tx[:, 1] - tx[:, 0]) * (ty[:, 2] - ty[:, 0]) - (tx[:, 2] - tx[:, 0]) * (ty[:, 1] - ty[:, 0])
    usable = np.flatnonzero(np.abs(area) > 1e-12)
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
        yield py * width + px, usable[which], values


def _depth_buffer(np, x, y, z, triangles, width, height):
    """The nearest depth under each pixel centre, scanline by scanline; ``inf`` where nothing is."""

    depth = np.full(width * height, np.inf)
    for pixels, _, values in _coverage(np, x, y, z, triangles, width, height):
        np.minimum.at(depth, pixels, values)
    return depth.reshape(height, width)


def _front_objects(np, x, y, z, triangles, labels, depth, nothing, tie):
    """The object nearest the eye under each pixel centre, ``nothing`` where no surface is.

    A second pass over the same coverage: a triangle is in front where its
    depth is within ``tie`` of the buffer's nearest, and of the objects
    meeting there (coplanar faces) the lowest label wins, so the answer never
    depends on the order of the pass or on rounding.
    """

    height, width = depth.shape
    nearest = depth.reshape(-1)
    front = np.full(width * height, nothing, dtype=np.int32)
    for pixels, which, values in _coverage(np, x, y, z, triangles, width, height):
        shown = values <= nearest[pixels] + tie
        np.minimum.at(front, pixels[shown], labels[which[shown]])
    return front.reshape(height, width)


def _fill_colour(object_id: str, colour) -> tuple[int, int, int] | None:
    if colour is None:
        return None
    try:
        channels = tuple(colour)
    except TypeError:
        channels = ()
    if len(channels) != 3 or not all(type(value) is int and 0 <= value <= 255 for value in channels):
        raise MeshViewError(f"the fill of {object_id} must be three whole sRGB channels from 0 to 255, or None")
    return channels


def mesh_line_view(
    meshes: Sequence[ObjectMesh], *, right: Sequence[float], up: Sequence[float],
    crop_uv: Sequence[float], size_px: int, text: Mapping[str, str] | None = None,
    fills: Mapping[str, Sequence[int] | None] | None = None,
) -> MeshLineView:
    """Draw the visible feature edges of ``meshes`` in the orthographic frame ``right``/``up``.

    ``crop_uv`` is the drawn window ``(u_min, v_min, u_max, v_max)`` with
    ``u = dot(p, right)``, ``v = dot(p, up)``; its longer side spans
    ``size_px`` pixels. The look direction is ``-(right x up)``. The PNG is
    greyscale, black lines on white, with ``text`` as its tEXt chunks.

    With ``fills`` (object id to an sRGB triple, or None) the PNG is RGB:
    each object's visible surface is filled flat with its triple, with no
    light, so a pixel inside one of its faces is exactly that colour; an
    object whose fill is None or absent is UNKNOWN_FILL under the
    UNKNOWN_HATCH lines. The edges are drawn over the fills in FILL_EDGE,
    on FILL_BACKGROUND, and ``seen`` names the objects that show.
    """

    import numpy as np
    from PIL import Image, PngImagePlugin

    if isinstance(size_px, bool) or not isinstance(size_px, int) or not 16 <= size_px <= MAX_SIZE_PX:
        raise MeshViewError(f"size_px must be a whole number of pixels from 16 to {MAX_SIZE_PX}")
    colours = None if fills is None else [_fill_colour(mesh.object_id, fills.get(mesh.object_id)) for mesh in meshes]
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

    xs, ys, zs, tris, edges, labels = [], [], [], [], [], []
    base = 0
    for label, mesh in enumerate(meshes):
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
        labels.append(np.full(len(triangles), label, dtype=np.int32))
        base += len(vertices)
    if colours is None:
        image, ink = np.full((big_h, big_w), 255, dtype=np.uint8), 0
    else:
        # Index len(meshes) is no surface: the paper.
        palette = np.array([UNKNOWN_FILL if colour is None else colour for colour in colours] + [FILL_BACKGROUND],
                           dtype=np.uint8)
        image, ink = np.empty((big_h, big_w, 3), dtype=np.uint8), np.array(FILL_EDGE, dtype=np.uint8)
        image[:] = FILL_BACKGROUND
    showing: tuple[str, ...] = ()
    drawn = 0
    triangle_count = sum(len(item) for item in tris)
    if base:
        x, y, z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
        depth = _depth_buffer(np, x, y, z, np.concatenate(tris), big_w, big_h)
        if colours is not None:
            front = _front_objects(np, x, y, z, np.concatenate(tris), np.concatenate(labels), depth, len(meshes),
                                   _TIE * max(u1 - u0, v1 - v0))
            image = palette[front]
            # The hatch is laid in output pixels, so it reads the same on a small part and a large one.
            period, width_fine = HATCH_SPACING * _SUPERSAMPLE, HATCH_WIDTH * _SUPERSAMPLE
            stripes = (np.arange(big_h, dtype=np.int32)[:, None] + np.arange(big_w, dtype=np.int32)[None, :]) % period
            unknown = np.array([colour is None for colour in colours] + [False])
            image[unknown[front] & (stripes < width_fine)] = UNKNOWN_HATCH
            shown = np.bincount(front.reshape(-1), minlength=len(meshes) + 1)[:len(meshes)]
            showing = tuple(sorted(meshes[label].object_id for label in np.flatnonzero(shown)))
            del front, stripes
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
            image[py, px] = ink
            image[py, np.minimum(px + 1, big_w - 1)] = ink
            image[np.minimum(py + 1, big_h - 1), px] = ink
    picture = Image.fromarray(image).reduce(_SUPERSAMPLE)
    info = PngImagePlugin.PngInfo()
    for key, value in sorted((text or {}).items()):
        info.add_text(key, value)
    buffer = BytesIO()
    picture.save(buffer, format="PNG", pnginfo=info, compress_level=_PNG_COMPRESS_LEVEL)
    return MeshLineView(buffer.getvalue(), width, height, triangle_count, drawn, showing)


# ---- perspective camera passes (#19) ------------------------------------------------
#
# The same depth buffer seen through a pinhole camera: an object-ID image, the
# visible feature edges, and the distance of the nearest surface, each pixel of
# the three read from one buffer, so they line up exactly. The buffer holds
# -1/distance along the view axis, which is affine in screen space, so the
# per-triangle plane interpolation of ``_coverage`` stays exact under
# perspective; triangles crossing the near plane are cut there.

#: The longest edge of a camera pass. Drawn without supersampling, its buffers
#: take about 40 bytes a pixel: some 380 MB at 4096 x 2304.
MAX_CAMERA_EDGE_PX = 4096
#: A pass whose longer edge is at most this is drawn at twice its size, so its lines are smoothed.
CAMERA_SUPERSAMPLE_EDGE_PX = 2048
#: The ID image's paper: no surface.
ID_BACKGROUND = (255, 255, 255)
#: The depth image is 16-bit: the nearest visible surface DEPTH_NEAREST, the farthest 1, no surface 0.
DEPTH_NEAREST = 65535


@dataclass(frozen=True, slots=True)
class PerspectiveCamera:
    """A pinhole camera in the model frame and unit: where it stands, what it looks at, its vertical field of view."""

    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    fov_deg: float

    @classmethod
    def from_matrices(cls, world: Sequence[float], projection: Sequence[float]) -> "PerspectiveCamera":
        """The camera a saved model view records: three.js column-major world and projection matrices."""

        values = [float(value) for value in world]
        project = [float(value) for value in projection]
        if len(values) != 16 or len(project) != 16 or not all(math.isfinite(value) for value in values + project):
            raise MeshViewError("a camera needs 16 finite world and 16 finite projection values")
        if project[11] != -1.0 or project[5] <= 0:
            raise MeshViewError("the saved camera is not a perspective camera")
        position = tuple(values[12:15])
        back = values[8:11]
        target = tuple(position[i] - back[i] for i in range(3))
        return cls(position, target, tuple(values[4:7]), math.degrees(2 * math.atan(1 / project[5])))

    def basis(self, np):
        eye = np.asarray(self.position, dtype=float)
        forward = np.asarray(self.target, dtype=float) - eye
        up = np.asarray(self.up, dtype=float)
        if not (np.all(np.isfinite(eye)) and np.all(np.isfinite(forward)) and np.all(np.isfinite(up))):
            raise MeshViewError("the camera must be finite")
        if not 1.0 <= float(self.fov_deg) <= 170.0:
            raise MeshViewError("the camera's vertical field of view must be from 1 to 170 degrees")
        length = float(np.linalg.norm(forward))
        if length == 0:
            raise MeshViewError("the camera's target must differ from its position")
        forward = forward / length
        right = np.cross(forward, up)
        if float(np.linalg.norm(right)) < 1e-9:
            raise MeshViewError("the camera's up must not point along its view")
        right = right / np.linalg.norm(right)
        return eye, right, np.cross(right, forward), forward

    def to_dict(self) -> dict[str, Any]:
        return {"position": list(self.position), "target": list(self.target), "up": list(self.up),
                "fovDeg": float(self.fov_deg)}

    def check(self) -> None:
        """Refuse a camera that cannot look: the same checks ``camera_passes`` makes before drawing."""

        import numpy as np

        self.basis(np)

    def pixel_footprint(self, height_px: int, boxes: Sequence[Mapping[str, Sequence[float]]] = ()) -> float:
        """Model units one pixel spans at the depth of the middle of ``boxes`` ({min, max}), or of the target."""

        import numpy as np

        eye, _, _, forward = self.basis(np)
        centre = np.asarray(self.target, dtype=float) if not boxes else (
            np.min([box["min"] for box in boxes], axis=0) + np.max([box["max"] for box in boxes], axis=0)) / 2
        distance = max(float((centre - eye) @ forward), 1e-6)
        return 2 * distance * math.tan(math.radians(self.fov_deg) / 2) / height_px


@dataclass(frozen=True, slots=True)
class CameraPasses:
    """Three aligned images of one camera's view and what they show.

    ``legend`` holds one row per ID colour: ``{key, color, objects, pixels}``.
    ``near`` and ``far`` are the distances along the view axis, in the model
    unit, of the nearest and farthest visible surface the depth image spans.
    """

    id_png: bytes
    line_png: bytes
    depth_png: bytes
    width: int
    height: int
    legend: tuple[dict[str, Any], ...]
    seen: tuple[str, ...]
    near: float | None
    far: float | None
    triangles: int
    edges_drawn: int


def _id_colour(index: int) -> tuple[int, int, int]:
    """A distinct, deterministic colour for the index-th ID key: golden-ratio hues in three bands, never white or black."""

    import colorsys

    hue = (index * 0.6180339887498949) % 1.0
    saturation = (0.85, 0.6, 0.95)[index % 3]
    value = (0.95, 0.75, 0.55)[(index // 3) % 3]
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return int(round(red * 255)), int(round(green * 255)), int(round(blue * 255))


def _id_palette(count: int) -> list[tuple[int, int, int]]:
    """``count`` different colours for the ID keys, none of them ID_BACKGROUND, in a fixed order.

    The golden-ratio hues are taken in turn and a colour already given (two
    hues can round to one sRGB triple) is skipped; past the few thousand they
    yield, the rest are the remaining triples in order, so no two keys ever
    share a colour.
    """

    seen = {ID_BACKGROUND}
    palette = []
    index = 0
    while len(palette) < count and index < 20 * count + 100:
        colour = _id_colour(index)
        index += 1
        if colour not in seen:
            seen.add(colour)
            palette.append(colour)
    value = 0
    while len(palette) < count:
        colour = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
        value += 1
        if colour not in seen:
            seen.add(colour)
            palette.append(colour)
    return palette


def _perspective_feature_edges(np, vertices, triangles, eye, crease_cos):
    """``_feature_edges`` for an eye at a point: a silhouette is where the two faces turn opposite ways to it."""

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
    draw = counts != 2
    two = np.flatnonzero(counts == 2)
    first, second = normals[faces[starts[two]]], normals[faces[starts[two] + 1]]
    view = vertices[pairs[starts[two], 0]] - eye
    facing = np.sign(np.einsum("ij,ij->i", first, view)) != np.sign(np.einsum("ij,ij->i", second, view))
    draw[two] = (np.einsum("ij,ij->i", first, second) < crease_cos) | facing
    return pairs[starts[draw]]


def _clip_near(np, x, y, z, triangles, labels, near):
    """Triangles cut at the plane ``z = near``: wholly behind it dropped, crossing it cut into one or two.

    New vertices are appended after the given ones, so the given indices keep
    their meaning; the arrays and the triangles' labels come back extended.
    """

    behind = z[triangles] < near
    count = behind.sum(axis=1)
    kept, kept_labels = [triangles[count == 0]], [labels[count == 0]]
    xs, ys, zs = [x], [y], [z]
    base = len(x)

    def cut(p, q):
        t = (near - z[p]) / (z[q] - z[p])
        return x[p] + t * (x[q] - x[p]), y[p] + t * (y[q] - y[p]), np.full(len(p), near)

    for lonely in (1, 2):
        rows = np.flatnonzero(count == lonely)
        if not len(rows):
            continue
        tri, flag = triangles[rows], behind[rows]
        # The odd vertex out first: the one behind (one behind) or the one in front (two behind).
        odd = np.argmax(flag if lonely == 1 else ~flag, axis=1)
        first = tri[np.arange(len(tri)), odd]
        second = tri[np.arange(len(tri)), (odd + 1) % 3]
        third = tri[np.arange(len(tri)), (odd + 2) % 3]
        a = cut(first, second) if lonely == 1 else cut(second, first)
        b = cut(first, third) if lonely == 1 else cut(third, first)
        n = len(rows)
        ia, ib = base + np.arange(n), base + n + np.arange(n)
        for values, source in ((xs, 0), (ys, 1), (zs, 2)):
            values.append(np.concatenate([a[source], b[source]]))
        base += 2 * n
        if lonely == 1:
            kept.append(np.stack([ia, second, third], axis=1))
            kept.append(np.stack([ia, third, ib], axis=1))
            kept_labels += [labels[rows], labels[rows]]
        else:
            kept.append(np.stack([first, ia, ib], axis=1))
            kept_labels.append(labels[rows])
    return (np.concatenate(xs), np.concatenate(ys), np.concatenate(zs), np.concatenate(kept).astype(np.int64),
            np.concatenate(kept_labels))


def _clip_to_frame(np, ax, ay, av, bx, by, bv, width, height, margin=2.0):
    """Segments cut to the frame (with a margin) by Liang-Barsky; ``av``/``bv`` follow linearly, as -1/z does."""

    dx, dy = bx - ax, by - ay
    t0, t1 = np.zeros(len(ax)), np.ones(len(ax))
    inside = np.ones(len(ax), dtype=bool)
    for p, q in ((-dx, ax + margin), (dx, width + margin - ax), (-dy, ay + margin), (dy, height + margin - ay)):
        parallel = p == 0
        inside &= ~(parallel & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
        entering, leaving = (p < 0) & ~parallel, (p > 0) & ~parallel
        t0 = np.where(entering, np.maximum(t0, r), t0)
        t1 = np.where(leaving, np.minimum(t1, r), t1)
    inside &= t0 <= t1
    dv = bv - av
    return (ax + t0 * dx, ay + t0 * dy, av + t0 * dv, ax + t1 * dx, ay + t1 * dy, av + t1 * dv, inside)


def _png(image, text):
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for key, value in sorted((text or {}).items()):
        info.add_text(key, value)
    buffer = BytesIO()
    picture = image if isinstance(image, Image.Image) else Image.fromarray(image)
    picture.save(buffer, format="PNG", pnginfo=info, compress_level=_PNG_COMPRESS_LEVEL)
    return buffer.getvalue()


def _clip_section(np, x, y, z, triangles, labels, pairs, plane):
    """The part of the model on the kept side of a live section plane (G5): what lies beyond it dropped or cut.

    ``plane`` is ``(a, b, c, e)`` in camera coordinates, kept where
    ``a x + b y + c z + e >= 0``. A triangle crossing it is cut into one or
    two, an edge crossing it is cut at it, and the segments where the plane
    crosses the triangles come back as new edges: the section's outline. New
    vertices are appended, so the given indices keep their meaning. The cut
    faces are not filled.
    """

    a, b, c, e = plane
    d = a * x + b * y + c * z + e
    removed = d[triangles] < 0
    count = removed.sum(axis=1)
    kept, kept_labels = [triangles[count == 0]], [labels[count == 0]]
    xs, ys, zs = [x], [y], [z]
    base = len(x)
    outline = []

    def cut(p, q):
        t = d[p] / (d[p] - d[q])
        return x[p] + t * (x[q] - x[p]), y[p] + t * (y[q] - y[p]), z[p] + t * (z[q] - z[p])

    for lonely in (1, 2):
        rows = np.flatnonzero(count == lonely)
        if not len(rows):
            continue
        tri, flag = triangles[rows], removed[rows]
        # The odd vertex out first: the one removed (one removed) or the one kept (two removed).
        odd = np.argmax(flag if lonely == 1 else ~flag, axis=1)
        first = tri[np.arange(len(tri)), odd]
        second = tri[np.arange(len(tri)), (odd + 1) % 3]
        third = tri[np.arange(len(tri)), (odd + 2) % 3]
        meet_a, meet_b = cut(first, second), cut(first, third)
        n = len(rows)
        ia, ib = base + np.arange(n), base + n + np.arange(n)
        for values, source in ((xs, 0), (ys, 1), (zs, 2)):
            values.append(np.concatenate([meet_a[source], meet_b[source]]))
        base += 2 * n
        if lonely == 1:
            kept.append(np.stack([ia, second, third], axis=1))
            kept.append(np.stack([ia, third, ib], axis=1))
            kept_labels += [labels[rows], labels[rows]]
        else:
            kept.append(np.stack([first, ia, ib], axis=1))
            kept_labels.append(labels[rows])
        outline.append(np.stack([ia, ib], axis=1))
    dp, dq = d[pairs[:, 0]], d[pairs[:, 1]]
    pairs = pairs[(dp >= 0) | (dq >= 0)].copy()
    crossing = np.flatnonzero((d[pairs[:, 0]] < 0) | (d[pairs[:, 1]] < 0))
    if len(crossing):
        p, q = pairs[crossing, 0], pairs[crossing, 1]
        meet = cut(p, q)
        at = base + np.arange(len(crossing))
        for values, source in ((xs, 0), (ys, 1), (zs, 2)):
            values.append(meet[source])
        base += len(crossing)
        first_removed = d[p] < 0
        pairs[crossing[first_removed], 0] = at[first_removed]
        pairs[crossing[~first_removed], 1] = at[~first_removed]
    return (np.concatenate(xs), np.concatenate(ys), np.concatenate(zs), np.concatenate(kept).astype(np.int64),
            np.concatenate(kept_labels), np.concatenate([pairs, *outline]).astype(np.int64))


def camera_passes(
    meshes: Sequence[ObjectMesh], *, camera: PerspectiveCamera, width: int, height: int,
    groups: Mapping[str, str] | None = None, text: Mapping[str, str] | None = None,
    clip: tuple[Sequence[float], Sequence[float]] | None = None,
) -> CameraPasses:
    """Draw ``meshes`` through ``camera`` into an ID image, a line image and a depth image of ``width`` x ``height``.

    The vertical field of view spans ``height``; pixels are square. ``groups``
    maps an object id to the key its ID colour stands for (its layer, its
    material, or the object itself when absent); each key gets one colour,
    exact in every pixel the key's nearest surface covers (no smoothing), on
    ID_BACKGROUND. The line image is black visible feature edges on white.
    The depth image is 16-bit grey: DEPTH_NEAREST at the nearest visible
    surface, 1 at the farthest, linear in distance along the view axis
    between them, 0 where no surface is. ``clip`` (a point and the normal
    toward the kept side, in the model's frame) cuts the model at a live
    section plane first, as the Modeling viewport does; its outline is drawn
    as lines and its cut faces are not filled.
    """

    import numpy as np
    from PIL import Image

    for name, value in (("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int) or not 16 <= value <= MAX_CAMERA_EDGE_PX:
            raise MeshViewError(f"{name} must be a whole number of pixels from 16 to {MAX_CAMERA_EDGE_PX}")
    eye, right, true_up, forward = camera.basis(np)
    sample = 2 if max(width, height) <= CAMERA_SUPERSAMPLE_EDGE_PX else 1
    big_w, big_h = width * sample, height * sample
    focal = (big_h / 2) / math.tan(math.radians(camera.fov_deg) / 2)
    crease_cos = math.cos(math.radians(CREASE_DEGREES))
    group_of = groups or {}

    xs, ys, zs, tris, edges, labels = [], [], [], [], [], []
    base = 0
    kept_meshes = []
    for mesh in meshes:
        vertices = np.asarray(mesh.vertices, dtype=float).reshape(-1, 3)
        triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
        if not len(vertices) or not len(triangles):
            continue
        label = len(kept_meshes)
        kept_meshes.append(mesh)
        extent = float(np.ptp(vertices, axis=0).max()) or 1.0
        vertices, triangles = _welded(np, vertices, triangles, extent * _WELD)
        edges.append(_perspective_feature_edges(np, vertices, triangles, eye, crease_cos) + base)
        offset = vertices - eye
        xs.append(offset @ right)
        ys.append(offset @ true_up)
        zs.append(offset @ forward)
        tris.append(triangles + base)
        labels.append(np.full(len(triangles), label, dtype=np.int32))
        base += len(vertices)
    keys = sorted({group_of.get(mesh.object_id, mesh.object_id) for mesh in kept_meshes})
    key_index = {key: index for index, key in enumerate(keys)}
    palette = np.array(_id_palette(len(keys)) + [ID_BACKGROUND], dtype=np.uint8)
    key_of_label = np.array([key_index[group_of.get(mesh.object_id, mesh.object_id)] for mesh in kept_meshes]
                            + [len(keys)], dtype=np.int64)

    line = np.full((big_h, big_w), 255, dtype=np.uint8)
    ids = np.full((height, width), len(kept_meshes), dtype=np.int64)
    depth_out = np.zeros((height, width), dtype=np.uint16)
    near_seen = far_seen = None
    drawn = 0
    triangle_count = sum(len(item) for item in tris)
    if base:
        x, y, z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
        cut_tris, cut_labels, pairs = np.concatenate(tris), np.concatenate(labels), np.concatenate(edges)
        if clip is not None:
            normal = np.asarray(clip[1], dtype=float)
            length = float(np.linalg.norm(normal))
            if not math.isfinite(length) or length == 0:
                raise MeshViewError("a section plane needs a normal that is not zero")
            normal = normal / length
            # The plane in camera coordinates: offset = x right + y up + z forward from the eye.
            plane = (float(normal @ right), float(normal @ true_up), float(normal @ forward),
                     float(normal @ (eye - np.asarray(clip[0], dtype=float))))
            x, y, z, cut_tris, cut_labels, pairs = _clip_section(np, x, y, z, cut_tris, cut_labels, pairs, plane)
        reach = float(np.abs(z).max()) or 1.0
        near = max(reach * 1e-6, 1e-9)
        cx, cy, cz, all_tris, all_labels = _clip_near(np, x, y, z, cut_tris, cut_labels, near)
        safe = np.maximum(cz, near)
        sx = big_w / 2 + focal * cx / safe
        sy = big_h / 2 - focal * cy / safe
        inverse = -1.0 / safe
        depth = _depth_buffer(np, sx, sy, inverse, all_tris, big_w, big_h)
        finite = np.isfinite(depth)
        if finite.any():
            tie = 1e-7 / float((-1.0 / depth[finite]).min())
            front = _front_objects(np, sx, sy, inverse, all_tris, all_labels, depth, len(kept_meshes), tie)
            at = slice(sample // 2, None, sample)
            ids = front[at, at][:height, :width]
            nearest = depth[at, at][:height, :width]
            shown = np.isfinite(nearest)
            if shown.any():
                distance = -1.0 / nearest[shown]
                near_seen, far_seen = float(distance.min()), float(distance.max())
                span = far_seen - near_seen
                scale = (far_seen - distance) / span if span > 0 else np.ones_like(distance)
                depth_out[shown] = (1 + np.round(scale * (DEPTH_NEAREST - 1))).astype(np.uint16)
            del front
            # Visible feature edges, as the model view draws them, with the
            # hiding tolerance taken at each sample's own distance.
            padded = np.pad(depth, 1, mode="edge")
            rows = np.maximum(np.maximum(padded[:-2], padded[1:-1]), padded[2:])
            farthest = np.maximum(np.maximum(rows[:, :-2], rows[:, 1:-1]), rows[:, 2:])
            del padded, rows
            za, zb = z[pairs[:, 0]], z[pairs[:, 1]]
            pairs = pairs[(za >= near) | (zb >= near)]
            ax, ay, az = x[pairs[:, 0]].copy(), y[pairs[:, 0]].copy(), z[pairs[:, 0]].copy()
            bx, by, bz = x[pairs[:, 1]].copy(), y[pairs[:, 1]].copy(), z[pairs[:, 1]].copy()
            for (px_, py_, pz_), (qx, qy, qz) in (((ax, ay, az), (bx, by, bz)), ((bx, by, bz), (ax, ay, az))):
                cut = pz_ < near
                t = (near - pz_[cut]) / (qz[cut] - pz_[cut])
                px_[cut] = px_[cut] + t * (qx[cut] - px_[cut])
                py_[cut] = py_[cut] + t * (qy[cut] - py_[cut])
                pz_[cut] = near
            ax, ay, av = big_w / 2 + focal * ax / az, big_h / 2 - focal * ay / az, -1.0 / az
            bx, by, bv = big_w / 2 + focal * bx / bz, big_h / 2 - focal * by / bz, -1.0 / bz
            # Only the part of an edge inside the frame is sampled: an edge
            # near the eye can project far beyond it.
            ax, ay, av, bx, by, bv, inside = _clip_to_frame(np, ax, ay, av, bx, by, bv, big_w, big_h)
            ax, ay, av, bx, by, bv = ax[inside], ay[inside], av[inside], bx[inside], by[inside], bv[inside]
            drawn = len(ax)
            steps = np.ceil(np.maximum(np.abs(bx - ax), np.abs(by - ay))).astype(np.int64) + 1
            steps = np.minimum(steps, 4 * (big_w + big_h))
            budget = np.cumsum(steps)
            begin = 0
            while begin < drawn:
                start = budget[begin - 1] if begin else 0
                end = max(begin + 1, int(np.searchsorted(budget, start + _CHUNK_PIXELS, side="right")))
                chunk = slice(begin, end)
                begin = end
                owner, step = _expand(np, steps[chunk])
                edge = owner + chunk.start
                t = step / np.maximum(steps[edge] - 1, 1)
                px = np.floor(ax[edge] + (bx - ax)[edge] * t).astype(np.int64)
                py = np.floor(ay[edge] + (by - ay)[edge] * t).astype(np.int64)
                sv = av[edge] + (bv - av)[edge] * t
                inside = (px >= 0) & (px < big_w) & (py >= 0) & (py < big_h)
                px, py, sv = px[inside], py[inside], sv[inside]
                # One and a half fine pixels at the sample's own distance z,
                # in the buffer's -1/z: dz / z^2 with dz = 1.5 z / focal.
                tolerance = _DEPTH_TOLERANCE * -sv / focal
                seen = sv <= farthest[py, px] + tolerance
                px, py = px[seen], py[seen]
                line[py, px] = 0
                line[py, np.minimum(px + 1, big_w - 1)] = 0
                line[np.minimum(py + 1, big_h - 1), px] = 0
    pixels = np.bincount(key_of_label[ids].reshape(-1), minlength=len(keys) + 1)[:len(keys)]
    objects = np.bincount(key_of_label[:-1], minlength=len(keys))
    legend = tuple({"key": key, "color": "#%02X%02X%02X" % tuple(int(c) for c in palette[index]),
                    "objects": int(objects[index]), "pixels": int(pixels[index])}
                   for index, key in enumerate(keys))
    seen_labels = np.flatnonzero(np.bincount(ids.reshape(-1), minlength=len(kept_meshes) + 1)[:len(kept_meshes)])
    line_image = Image.fromarray(line).reduce(sample) if sample > 1 else Image.fromarray(line)
    return CameraPasses(
        id_png=_png(palette[key_of_label[ids]], text), line_png=_png(line_image, text),
        depth_png=_png(depth_out, text), width=width, height=height, legend=legend,
        seen=tuple(sorted(kept_meshes[label].object_id for label in seen_labels)), near=near_seen, far=far_seen,
        triangles=triangle_count, edges_drawn=drawn)


def _camera_renderer_version() -> str:
    """``RENDERER_VERSION`` for the camera passes: the source of what they read and draw, apart from the model view's."""

    from monkeydiagram import sources

    functions = (tessellate_shape, sources.read_elevation_source, sources.read_native_source, triangulate,
                 clear_shape_triangulation, _welded, _expand, _coverage, _depth_buffer, _front_objects,
                 _perspective_feature_edges, _clip_near, _clip_to_frame, _id_colour, _id_palette, _png, camera_passes)
    digest = hashlib.sha256()
    for function in functions:
        digest.update(inspect.getsource(function).encode("utf-8"))
    return f"camera-passes-{digest.hexdigest()[:12]}"


#: Changes whenever the pixels this module draws for the same input change: derived from the source.
RENDERER_VERSION = _renderer_version()
#: The camera passes' own version, derived the same way.
CAMERA_RENDERER_VERSION = _camera_renderer_version()


# ---- hidden lines that only a flush face hides (S22) ---------------------------------
#
# The polygonal hidden-line solve (``project_occt_mesh_lines``) decides an
# edge lying in an occluding face's plane - a mullion flush with a wall's end
# face, the two touching - by rounding, and often calls it hidden, where the
# exact solve and a drafter draw it. Here each hidden line is found on its
# object's 3D edges again, and a line is drawn visible when, at every sample
# along it, no triangle covers it nearer than ``depth_tolerance``.

#: Samples taken along each hidden line; all of them must be uncovered.
FLUSH_SAMPLES = 3


def _closest_on_segments(np, points, a, b):
    """For each 2D point, the parameter and distance of its nearest point on every segment a->b (points x segments)."""

    d = b - a
    length = np.einsum("ij,ij->i", d, d)
    t = np.clip(np.einsum("pij,ij->pi", points[:, None, :] - a[None], d) / np.where(length > 0, length, 1.0), 0.0, 1.0)
    nearest = a[None] + t[..., None] * d[None]
    return t, np.linalg.norm(points[:, None, :] - nearest, axis=2)


def show_flush_hidden_lines(lines, meshes: Sequence[ObjectMesh], edges: Mapping[str, Sequence[Sequence[Sequence[float]]]], *,
                            origin: Sequence[float], right: Sequence[float], up: Sequence[float],
                            match_tolerance: float, depth_tolerance: float):
    """``lines`` with every hidden line that no surface covers by more than ``depth_tolerance`` made visible.

    ``lines`` are ``OcctDrawingPolyline`` values in the frame ``origin``,
    ``right``, ``up`` (look ``-(right x up)``); ``meshes`` are every drawn
    object's triangles and ``edges`` its 3D edges, both in the model frame.
    A hidden line is matched to its object's nearest edges, within
    ``match_tolerance`` in the drawing plane, to know its depth; one that matches no edge, or that
    any triangle covers nearer at one sample, stays hidden. Returns the lines
    and how many changed.
    """

    import numpy as np
    from monkeycad.backends.occt.projection import OcctDrawingPolyline

    origin_v, right_v, up_v = (np.asarray(value, dtype=float) for value in (origin, right, up))
    look = -np.cross(right_v, up_v)

    def project(points):
        relative = np.asarray(points, dtype=float).reshape(-1, 3) - origin_v
        return relative @ right_v, relative @ up_v, relative @ look

    # Every sample of a hidden line, with each depth an edge of its object gives it there.
    rows_line, rows_u, rows_v, rows_w = [], [], [], []
    hidden = [index for index, line in enumerate(lines) if line.kind == "hidden"]
    by_object: dict[str, list[int]] = {}
    for index in hidden:
        by_object.setdefault(lines[index].object_id, []).append(index)
    visible_by_object: dict[str, list[tuple[Any, Any]]] = {}
    for line in lines:
        if line.kind == "visible":
            visible_by_object.setdefault(line.object_id, []).extend(zip(line.points, line.points[1:]))
    for object_id, indices in by_object.items():
        segments = [(polyline[i], polyline[i + 1]) for polyline in edges.get(object_id, ()) for i in range(len(polyline) - 1)]
        if not segments:
            continue
        au, av, aw = project([a for a, _ in segments])
        bu, bv, bw = project([b for _, b in segments])
        a2, b2 = np.stack([au, av], axis=1), np.stack([bu, bv], axis=1)
        own = visible_by_object.get(object_id, [])
        own_a = np.asarray([a for a, _ in own], dtype=float).reshape(-1, 2)
        own_b = np.asarray([b for _, b in own], dtype=float).reshape(-1, 2)
        for index in indices:
            points = np.asarray(lines[index].points, dtype=float)
            steps = np.cumsum(np.r_[0.0, np.linalg.norm(np.diff(points, axis=0), axis=1)])
            if steps[-1] <= 0.0:
                continue
            at = steps[-1] * (np.arange(FLUSH_SAMPLES) + 0.5) / FLUSH_SAMPLES
            samples = np.stack([np.interp(at, steps, points[:, 0]), np.interp(at, steps, points[:, 1])], axis=1)
            # Under the object's own visible line it is the edge behind that one (a box's back edge): hidden.
            if len(own_a) and (_closest_on_segments(np, samples, own_a, own_b)[1].min(axis=1) <= match_tolerance).all():
                continue
            t, distance = _closest_on_segments(np, samples, a2, b2)
            depth = aw[None] + t * (bw - aw)[None]
            for sample in range(FLUSH_SAMPLES):
                # The nearest edges only: another edge a little aside (a closure's other arris) has another depth.
                nearest = distance[sample].min()
                if nearest > match_tolerance:
                    continue
                matched = np.flatnonzero(distance[sample] <= nearest + depth_tolerance)
                for w in np.unique(depth[sample, matched]):
                    rows_line.append((index, sample)); rows_u.append(samples[sample, 0]); rows_v.append(samples[sample, 1])
                    rows_w.append(w)
    if not rows_line:
        return tuple(lines), 0
    pu, pv, pw = (np.asarray(values, dtype=float) for values in (rows_u, rows_v, rows_w))
    covered = np.zeros(len(pu), dtype=bool)
    for mesh in meshes:
        triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
        if not len(triangles):
            continue
        u, v, w = project(mesh.vertices)
        tu, tv, tw = u[triangles], v[triangles], w[triangles]
        active = np.flatnonzero(~covered & (pu >= tu.min()) & (pu <= tu.max()) & (pv >= tv.min()) & (pv <= tv.max())
                                & (pw > tw.min() + depth_tolerance))
        if not len(active):
            continue
        area = (tu[:, 1] - tu[:, 0]) * (tv[:, 2] - tv[:, 0]) - (tu[:, 2] - tu[:, 0]) * (tv[:, 1] - tv[:, 0])
        usable = np.abs(area) > 1e-15
        tu, tv, tw, area = tu[usable], tv[usable], tw[usable], area[usable]
        if not len(area):
            continue
        # How far below zero each weight may go: a sample within depth_tolerance of a triangle's border is
        # on it (an edge along a nearer face's outline stays hidden, as the exact solve keeps it).
        sides = np.stack([np.hypot(tu[:, 2] - tu[:, 1], tv[:, 2] - tv[:, 1]), np.hypot(tu[:, 0] - tu[:, 2], tv[:, 0] - tv[:, 2]),
                          np.hypot(tu[:, 1] - tu[:, 0], tv[:, 1] - tv[:, 0])], axis=1)
        slack = depth_tolerance * sides / np.abs(area)[:, None]
        chunk = max(1, 2_000_000 // len(area))
        for start in range(0, len(active), chunk):
            rows = active[start:start + chunk]
            x, y = pu[rows, None], pv[rows, None]
            # Barycentric weights of each sample in each triangle.
            l1 = ((tu[None, :, 1] - x) * (tv[None, :, 2] - y) - (tu[None, :, 2] - x) * (tv[None, :, 1] - y)) / area[None]
            l2 = ((tu[None, :, 2] - x) * (tv[None, :, 0] - y) - (tu[None, :, 0] - x) * (tv[None, :, 2] - y)) / area[None]
            l3 = 1.0 - l1 - l2
            inside = (l1 >= -slack[None, :, 0]) & (l2 >= -slack[None, :, 1]) & (l3 >= -slack[None, :, 2])
            nearer = l1 * tw[None, :, 0] + l2 * tw[None, :, 1] + l3 * tw[None, :, 2] < pw[rows, None] - depth_tolerance
            covered[rows] = (inside & nearer).any(axis=1)
    # A sample is seen when one of its depths is uncovered; a line when all its samples are.
    seen: dict[tuple[int, int], bool] = {}
    for key, hit in zip(rows_line, covered):
        seen[key] = seen.get(key, False) or not hit
    shown = {index for index in hidden if all(seen.get((index, sample), False) for sample in range(FLUSH_SAMPLES))}
    result = tuple(OcctDrawingPolyline(line.object_id, "visible", line.points) if index in shown else line
                   for index, line in enumerate(lines))
    return tuple(sorted(set(result), key=lambda line: (line.object_id, line.kind, line.points))), len(shown)
