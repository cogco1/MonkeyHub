"""Scale closed meshes and cut them into a fixed, axis-aligned print grid."""

from dataclasses import dataclass
from math import ceil, floor, fsum, isclose, isfinite

import manifold3d
import numpy as np
import trimesh


class GeometryError(ValueError):
    """The input or a cut cannot produce closed, positive-volume print parts."""


@dataclass(frozen=True)
class PreparedPart:
    mesh: trimesh.Trimesh
    assembly_offset_mm: tuple[float, float, float]
    grid_index: tuple[int, int, int]


def _require_valid(solid: manifold3d.Manifold) -> None:
    if solid.status() != manifold3d.Error.NoError:
        raise GeometryError(f"Manifold rejected the geometry: {solid.status().name}")


def _grid_parts(solid: manifold3d.Manifold, size: np.ndarray):
    pieces = [((0, 0, 0), solid)]
    for axis, width in enumerate(size):
        next_pieces = []
        normal = tuple(1.0 if coordinate == axis else 0.0 for coordinate in range(3))
        for index, remainder in pieces:
            bounds = remainder.bounding_box()
            first = max(0, floor(bounds[axis] / width))
            last = max(first, ceil(bounds[axis + 3] / width) - 1)
            for cell in range(first, last + 1):
                if cell < last:
                    remainder, piece = remainder.split_by_plane(normal, (cell + 1) * width)
                    _require_valid(remainder)
                    _require_valid(piece)
                else:
                    piece = remainder
                if not piece.is_empty():
                    cell_index = list(index)
                    cell_index[axis] = cell
                    next_pieces.append((tuple(cell_index), piece))
        pieces = next_pieces
    return pieces


def prepare_mesh(
    mesh: trimesh.Trimesh,
    scale_factor: float,
    build_volume_mm: tuple[float, float, float],
) -> tuple[PreparedPart, ...]:
    """Return capped print parts, without changing the input or writing files.

    ``scale_factor`` includes the source-unit conversion and uniform scale.
    The XYZ grid starts at the scaled model's minimum corner. Each returned
    mesh starts at its own minimum of (0, 0, 0); adding assembly_offset_mm to
    its vertices restores its position in the scaled source coordinate frame.
    Several disconnected pieces may occupy the same grid cell. Bodies are cut
    separately, even when they overlap: no union or deduplication is performed.

    Open meshes, inconsistent winding, non-positive bodies and disconnected
    inward cavity shells are refused. Through-holes in a connected closed body
    are supported. Nothing fills holes, repairs normals or rotates the model.
    """
    try:
        factor = float(scale_factor)
        size = np.asarray(build_volume_mm, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GeometryError("Scale and build volume must be finite positive numbers") from exc
    if not isfinite(factor) or factor <= 0:
        raise GeometryError("scale_factor must be finite and greater than zero")
    if size.shape != (3,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise GeometryError("build_volume_mm must contain three finite positive dimensions")
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise GeometryError("Input must be a non-empty triangular mesh")
    if not np.isfinite(mesh.vertices).all():
        raise GeometryError("Mesh vertices must be finite")
    if np.any(mesh.faces < 0) or np.any(mesh.faces >= len(mesh.vertices)):
        raise GeometryError("Mesh faces reference missing vertices")
    if not mesh.is_watertight:
        raise GeometryError("Mesh must be closed and watertight; open meshes are not repaired")
    if not mesh.is_winding_consistent:
        raise GeometryError("Mesh face winding must be consistent; normals are not repaired")

    with np.errstate(over="ignore", invalid="ignore"):
        vertices = np.asarray(mesh.vertices, dtype=np.float64) * factor
    if not np.isfinite(vertices).all():
        raise GeometryError("Scaling produces non-finite coordinates")
    # Keep the kernel near the origin, while preserving the original assembly frame.
    origin = vertices.min(axis=0)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        vertices = vertices - origin
        cell_counts = vertices.max(axis=0) / size
    if not np.isfinite(vertices).all() or not np.isfinite(cell_counts).all():
        raise GeometryError("Scaled geometry or print grid exceeds numeric range")
    source = trimesh.Trimesh(vertices=vertices, faces=mesh.faces.copy(), process=False)
    source_volume = float(source.volume)
    if not isfinite(source_volume) or source_volume <= 0:
        raise GeometryError("Mesh must have a finite positive volume and outward normals")
    solid = manifold3d.Manifold(
        manifold3d.Mesh64(
            vert_properties=np.ascontiguousarray(vertices, dtype=np.float64),
            tri_verts=np.ascontiguousarray(source.faces, dtype=np.uint64),
        )
    )
    _require_valid(solid)
    bodies = solid.decompose()
    if not bodies or any(not isfinite(body.volume()) or body.volume() <= 0 for body in bodies):
        raise GeometryError("Every body must have positive volume; inward or enclosed cavity shells are unsupported")

    parts = []
    for body in bodies:
        for grid_index, cut in _grid_parts(body, size):
            for piece in cut.decompose():
                _require_valid(piece)
                data = piece.to_mesh64()
                part = trimesh.Trimesh(
                    vertices=np.asarray(data.vert_properties[:, :3], dtype=np.float64),
                    faces=np.asarray(data.tri_verts, dtype=np.int64),
                    process=False,
                )
                if part.is_empty or not np.isfinite(part.vertices).all() or not part.is_volume:
                    raise GeometryError("A cut did not produce a closed, outward, positive-volume part")
                offset = part.bounds[0].copy()
                part.apply_translation(-offset)
                tolerance = np.maximum(size, 1.0) * 1e-9
                if np.any(part.extents > size + tolerance):
                    raise GeometryError("A cut exceeds the selected print volume")
                parts.append(PreparedPart(part, tuple(float(x) for x in origin + offset), grid_index))

    if not parts or not isclose(fsum(float(part.mesh.volume) for part in parts), source_volume, rel_tol=1e-8):
        raise GeometryError("Cutting changed the total volume; no parts were returned")
    return tuple(parts)
