"""Explicit saved-geometry witnesses for headless 3DM inspection.

openNURBS does not reliably persist ``BrepFace`` render-mesh caches.  Exporters
that create an axis-aligned box Brep can nevertheless persist an independent,
hidden mesh for each of its six trimmed faces.  The inspector excludes these
objects from model denominators and uses their vertices only as a visible-bound
witness for the exact source object id.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


_WITNESS_SOURCE_KEY = "archflow:visible_bounds_witness_for"
_WITNESS_INDEX_KEY = "archflow:visible_bounds_witness_index"
_WITNESS_COUNT_KEY = "archflow:visible_bounds_witness_count"


def is_visible_bounds_witness_object(item: Any) -> bool:
    """Return whether a File3dm object is an inspector-only witness."""

    attributes = getattr(item, "Attributes", None)
    getter = getattr(attributes, "GetUserString", None)
    return callable(getter) and getter(_WITNESS_SOURCE_KEY) not in (None, "")


def primary_three_dm_objects(model: Any) -> tuple[Any, ...]:
    """Return semantic model objects, excluding hidden inspector witnesses."""

    objects = getattr(model, "Objects", None)
    if objects is None:
        raise TypeError("model must expose a File3dm object table")
    return tuple(
        item for item in objects if not is_visible_bounds_witness_object(item)
    )


def add_axis_aligned_box_brep_witnesses(
    model: Any,
    *,
    source_object_id: object,
    brep: Any,
    layer_index: int,
    tolerance: float = 1e-9,
) -> tuple[str, ...]:
    """Persist six hidden face meshes for one proven box Brep.

    The function refuses generic or trimmed Breps.  It accepts only a solid,
    six-planar-face, eight-corner Brep whose vertices are exactly the corners
    of its own tight bounding box.  This keeps the witness contract honest and
    avoids treating an untrimmed control-surface box as visible geometry.
    """

    import rhino3dm

    source_id = str(UUID(str(source_object_id))).lower()
    if not isinstance(layer_index, int) or isinstance(layer_index, bool):
        raise TypeError("layer_index must be an integer")
    if not isinstance(tolerance, (int, float)) or tolerance <= 0:
        raise ValueError("tolerance must be positive")
    faces = tuple(brep.Faces)
    if not bool(brep.IsSolid) or len(faces) != 6 or any(
        not bool(face.IsPlanar(tolerance)) for face in faces
    ):
        raise ValueError("visible-bounds witness source must be a solid box Brep")
    bbox = brep.GetTightBoundingBox()
    if bbox is None or not bool(bbox.IsValid):
        raise ValueError("box Brep has no valid tight bounding box")
    minimum = (float(bbox.Min.X), float(bbox.Min.Y), float(bbox.Min.Z))
    maximum = (float(bbox.Max.X), float(bbox.Max.Y), float(bbox.Max.Z))
    if any(maximum[axis] - minimum[axis] <= tolerance for axis in range(3)):
        raise ValueError("box Brep has a degenerate extent")
    corners = tuple(
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    )
    unmatched = list(corners)
    vertices = tuple(
        (float(item.Location.X), float(item.Location.Y), float(item.Location.Z))
        for item in brep.Vertices
    )
    if len(vertices) != 8:
        raise ValueError("box Brep must retain exactly eight corner vertices")
    for vertex in vertices:
        match = next(
            (
                index
                for index, corner in enumerate(unmatched)
                if all(
                    abs(vertex[axis] - corner[axis]) <= tolerance
                    for axis in range(3)
                )
            ),
            None,
        )
        if match is None:
            raise ValueError("Brep vertices do not match its bounding-box corners")
        unmatched.pop(match)
    if unmatched:
        raise ValueError("Brep does not cover every bounding-box corner")

    x0, y0, z0 = minimum
    x1, y1, z1 = maximum
    face_points = (
        ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)),
        ((x0, y0, z1), (x0, y1, z1), (x1, y1, z1), (x1, y0, z1)),
        ((x0, y0, z0), (x0, y0, z1), (x1, y0, z1), (x1, y0, z0)),
        ((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)),
        ((x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (x0, y1, z0)),
        ((x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)),
    )
    witness_ids: list[str] = []
    count = len(face_points)
    for index, points in enumerate(face_points):
        mesh = rhino3dm.Mesh()
        for point in points:
            mesh.Vertices.Add(*point)
        mesh.Faces.AddFace(0, 1, 2, 3)
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        attributes = rhino3dm.ObjectAttributes()
        attributes.Name = (
            f"__archflow_visible_bounds__:{source_id}:{index:04d}"
        )
        attributes.LayerIndex = layer_index
        attributes.Visible = False
        attributes.SetUserString(_WITNESS_SOURCE_KEY, source_id)
        attributes.SetUserString(_WITNESS_INDEX_KEY, str(index))
        attributes.SetUserString(_WITNESS_COUNT_KEY, str(count))
        witness_ids.append(str(model.Objects.AddMesh(mesh, attributes)).lower())
    return tuple(witness_ids)


__all__ = [
    "add_axis_aligned_box_brep_witnesses",
    "is_visible_bounds_witness_object",
    "primary_three_dm_objects",
]
