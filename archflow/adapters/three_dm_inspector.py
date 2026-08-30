"""Headless, read-only inspection of Rhino ``.3dm`` files.

The adapter imports ``rhino3dm`` lazily, reads one immutable byte snapshot,
and returns JSON-compatible metadata.  It never imports RhinoCommon, starts
Rhino, or chooses a persistence destination.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar


_MAX_ERROR_TEXT = 1_000
_EMPTY_UUID = "00000000-0000-0000-0000-000000000000"


class ThreeDmInspectionErrorCode(StrEnum):
    DEPENDENCY_UNAVAILABLE = "three_dm_inspection.dependency_unavailable"
    FILE_NOT_FOUND = "three_dm_inspection.file_not_found"
    PATH_NOT_FILE = "three_dm_inspection.path_not_file"
    FILE_UNREADABLE = "three_dm_inspection.file_unreadable"
    INVALID_FILE = "three_dm_inspection.invalid_file"


class ThreeDmInspectionError(RuntimeError):
    """Named fail-closed error suitable for a progress-panel boundary."""

    SCHEMA = "ThreeDmInspectionError@1"

    def __init__(
        self,
        code: ThreeDmInspectionErrorCode,
        message: str,
    ) -> None:
        if not isinstance(code, ThreeDmInspectionErrorCode):
            raise TypeError("code must be ThreeDmInspectionErrorCode")
        bounded_message = str(message)[:_MAX_ERROR_TEXT]
        super().__init__(f"{code.value}: {bounded_message}")
        self.code = code
        self.message = bounded_message

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.SCHEMA,
            "code": self.code.value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ThreeDmInspection:
    """Typed inspection result with one stable JSON serialization boundary."""

    file_sha256: str
    file_bytes: int
    three_dm_version: int
    archive_version: int
    units: dict[str, object]
    layers: tuple[dict[str, object], ...]
    object_count: int
    top_level_object_count: int
    instance_definition_member_count: int
    object_counts_by_type: dict[str, int]
    object_counts_by_layer: tuple[dict[str, object], ...]
    instance_definitions: tuple[dict[str, object], ...]
    instance_references: tuple[dict[str, object], ...]
    document_user_strings: tuple[dict[str, str], ...]
    object_user_strings: tuple[dict[str, object], ...]
    aggregate_bbox: dict[str, list[float]] | None
    bbox_contributing_geometry_count: int
    named_object_bboxes: tuple[dict[str, object], ...] = ()
    read_only: bool = True
    rhino_process_started: bool = False

    SCHEMA: ClassVar[str] = "ThreeDmInspectionSummary@1"

    def to_dict(self) -> dict[str, object]:
        payload = {
            "schema": self.SCHEMA,
            "file_sha256": self.file_sha256,
            "file_bytes": self.file_bytes,
            "three_dm_version": self.three_dm_version,
            "archive_version": self.archive_version,
            "units": self.units,
            "layers": self.layers,
            "object_count": self.object_count,
            "top_level_object_count": self.top_level_object_count,
            "instance_definition_member_count": (
                self.instance_definition_member_count
            ),
            "object_counts_by_type": self.object_counts_by_type,
            "object_counts_by_layer": self.object_counts_by_layer,
            "instance_definitions": self.instance_definitions,
            "instance_references": self.instance_references,
            "document_user_strings": self.document_user_strings,
            "object_user_strings": self.object_user_strings,
            "aggregate_bbox": self.aggregate_bbox,
            "bbox_contributing_geometry_count": (
                self.bbox_contributing_geometry_count
            ),
            "named_object_bboxes": self.named_object_bboxes,
            "read_only": self.read_only,
            "rhino_process_started": self.rhino_process_started,
        }
        return json.loads(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def inspect_three_dm(path: Path) -> ThreeDmInspection:
    """Return a deterministic JSON-compatible summary of one ``.3dm`` file.

    The SHA-256 digest and the decoded model are derived from the same byte
    snapshot, preventing path replacement between hashing and inspection.
    """

    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    file_bytes = _read_file(path)
    rhino3dm = _load_rhino3dm()

    try:
        model = rhino3dm.File3dm.FromByteArray(file_bytes)
    except Exception as exc:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.INVALID_FILE,
            "rhino3dm could not decode the file",
        ) from exc
    if model is None:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.INVALID_FILE,
            "rhino3dm rejected the file as invalid or damaged",
        )

    try:
        payload = _summarize_model(model)
    except ThreeDmInspectionError:
        raise
    except Exception as exc:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.INVALID_FILE,
            "the decoded 3dm model contains invalid inspection data",
        ) from exc

    return ThreeDmInspection(
        file_sha256=hashlib.sha256(file_bytes).hexdigest(),
        file_bytes=len(file_bytes),
        **payload,
    )


def _read_file(source: Path) -> bytes:
    try:
        exists = source.exists()
    except OSError as exc:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.FILE_UNREADABLE,
            "the 3dm path could not be inspected",
        ) from exc
    if not exists:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.FILE_NOT_FOUND,
            "the 3dm file does not exist",
        )
    if not source.is_file():
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.PATH_NOT_FILE,
            "the 3dm path is not a regular file",
        )
    try:
        return source.read_bytes()
    except OSError as exc:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.FILE_UNREADABLE,
            "the 3dm file could not be read",
        ) from exc


def _load_rhino3dm() -> Any:
    try:
        module = importlib.import_module("rhino3dm")
    except ImportError as exc:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
            "optional dependency 'rhino3dm' is unavailable; install it with "
            "'python -m pip install rhino3dm'; Rhino is not required or "
            "started",
        ) from exc
    file_type = getattr(module, "File3dm", None)
    if file_type is None or not hasattr(file_type, "FromByteArray"):
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
            "the installed 'rhino3dm' package does not expose the required "
            "File3dm.FromByteArray API",
        )
    return module


def _summarize_model(model: Any) -> dict[str, object]:
    objects = tuple(model.Objects)
    layers, layers_by_index = _layers(model, objects)
    object_rows, objects_by_id = _objects(objects, layers_by_index)
    definitions, definition_members = _definitions(model)
    references = _references(
        object_rows,
        definitions,
        layers_by_index,
    )
    reference_counts = Counter(
        item["definition_id"] for item in references
    )
    for item in definitions:
        item["reference_count"] = reference_counts.get(item["id"], 0)

    aggregate_bbox, bbox_geometry_count = _aggregate_bbox(
        object_rows,
        objects_by_id,
        definition_members,
    )
    by_type = Counter(item["type"] for item in object_rows)
    by_layer = _object_counts_by_layer(
        object_rows,
        layers_by_index,
    )
    archive_version = _integer(model.ArchiveVersion, "archive version")

    return {
        "three_dm_version": _three_dm_version(archive_version),
        "archive_version": archive_version,
        "units": _units(model.Settings.ModelUnitSystem),
        "layers": tuple(layers),
        "object_count": len(object_rows),
        "top_level_object_count": sum(
            not item["is_instance_definition_object"]
            for item in object_rows
        ),
        "instance_definition_member_count": sum(
            item["is_instance_definition_object"]
            for item in object_rows
        ),
        "object_counts_by_type": _sorted_counts(by_type),
        "object_counts_by_layer": tuple(by_layer),
        "instance_definitions": tuple(definitions),
        "instance_references": tuple(references),
        "document_user_strings": tuple(
            _document_user_strings(model.Strings)
        ),
        "object_user_strings": tuple(_object_user_strings(object_rows)),
        "aggregate_bbox": aggregate_bbox,
        "bbox_contributing_geometry_count": bbox_geometry_count,
        "named_object_bboxes": tuple(_named_object_bboxes(object_rows)),
    }


def _layers(
    model: Any,
    objects: tuple[Any, ...],
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    object_counts = Counter(
        _integer(item.Attributes.LayerIndex, "object layer index")
        for item in objects
    )
    rows: list[dict[str, object]] = []
    by_index: dict[int, dict[str, object]] = {}
    for layer in model.Layers:
        index = _integer(layer.Index, "layer index")
        if index in by_index:
            raise ValueError("duplicate layer index")
        layer_id = _identifier(layer.Id, "layer id")
        parent_id = _identifier(layer.ParentLayerId, "parent layer id")
        row: dict[str, object] = {
            "index": index,
            "id": layer_id,
            "name": _string(layer.Name, "layer name"),
            "full_path": _string(layer.FullPath, "layer full path"),
            "parent_id": None if parent_id == _EMPTY_UUID else parent_id,
            "visible": bool(layer.Visible),
            "locked": bool(layer.Locked),
            "object_count": object_counts.get(index, 0),
        }
        rows.append(row)
        by_index[index] = row
    rows.sort(key=lambda item: (item["index"], item["id"]))
    return rows, by_index


def _objects(
    objects: tuple[Any, ...],
    layers_by_index: dict[int, dict[str, object]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_id: dict[str, Any] = {}
    for item in objects:
        attributes = item.Attributes
        geometry = item.Geometry
        if geometry is None:
            raise ValueError("3dm object has no geometry")
        if hasattr(geometry, "IsValid") and not bool(geometry.IsValid):
            raise ValueError("3dm object contains invalid geometry")
        object_id = _identifier(attributes.Id, "object id")
        if object_id in by_id:
            raise ValueError("duplicate object id")
        layer_index = _integer(attributes.LayerIndex, "object layer index")
        layer = layers_by_index.get(layer_index)
        row = {
            "id": object_id,
            "name": _string(attributes.Name, "object name"),
            "type": _enum_name(geometry.ObjectType, "object type"),
            "layer_index": layer_index,
            "layer_id": None if layer is None else layer["id"],
            "layer_path": None if layer is None else layer["full_path"],
            "is_instance_definition_object": bool(
                attributes.IsInstanceDefinitionObject
            ),
            "attributes": attributes,
            "geometry": geometry,
            "source": item,
        }
        rows.append(row)
        by_id[object_id] = item
    rows.sort(key=lambda item: item["id"])
    return rows, by_id


def _named_object_bboxes(
    object_rows: list[dict[str, Any]],
) -> list[dict[str, object]]:
    """Read per-object world bounds for named, top-level geometry.

    This intentionally excludes instance-definition members and instance
    references: their world bounds require expansion and are already covered
    by the aggregate inspector.  P069 uses one named physical column as an
    independent saved-file axis witness.
    """

    rows: list[dict[str, object]] = []
    for item in object_rows:
        if item["is_instance_definition_object"] or not item["name"]:
            continue
        geometry = item["geometry"]
        if item["type"] == "InstanceReference":
            continue
        bbox = geometry.GetBoundingBox()
        if bbox is None or not bool(bbox.IsValid):
            continue
        rows.append(
            {
                "object_id": item["id"],
                "name": item["name"],
                "type": item["type"],
                "bbox": {
                    "min": list(_point(bbox.Min, "named object bbox minimum")),
                    "max": list(_point(bbox.Max, "named object bbox maximum")),
                },
            }
        )
    rows.sort(key=lambda item: (str(item["name"]), str(item["object_id"])))
    return rows


def _definitions(
    model: Any,
) -> tuple[list[dict[str, object]], dict[str, tuple[str, ...]]]:
    rows: list[dict[str, object]] = []
    members_by_id: dict[str, tuple[str, ...]] = {}
    for definition in model.InstanceDefinitions:
        definition_id = _identifier(definition.Id, "definition id")
        if definition_id in members_by_id:
            raise ValueError("duplicate instance definition id")
        members = tuple(
            sorted(
                _identifier(item, "definition object id")
                for item in definition.GetObjectIds()
            )
        )
        members_by_id[definition_id] = members
        rows.append(
            {
                "id": definition_id,
                "name": _string(definition.Name, "definition name"),
                "description": _string(
                    definition.Description,
                    "definition description",
                ),
                "update_type": _enum_name(
                    definition.UpdateType,
                    "definition update type",
                ),
                "is_linked": bool(definition.SourceArchive),
                "object_ids": list(members),
                "object_count": len(members),
                "user_strings": _user_strings(definition),
            }
        )
    rows.sort(key=lambda item: item["id"])
    return rows, members_by_id


def _references(
    object_rows: list[dict[str, Any]],
    definitions: list[dict[str, object]],
    layers_by_index: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    names = {item["id"]: item["name"] for item in definitions}
    rows: list[dict[str, object]] = []
    for item in object_rows:
        if item["type"] != "InstanceReference":
            continue
        geometry = item["geometry"]
        definition_id = _identifier(
            geometry.ParentIdefId,
            "reference definition id",
        )
        layer = layers_by_index.get(item["layer_index"])
        rows.append(
            {
                "object_id": item["id"],
                "definition_id": definition_id,
                "definition_name": names.get(definition_id),
                "layer_index": item["layer_index"],
                "layer_id": None if layer is None else layer["id"],
                "layer_path": (
                    None if layer is None else layer["full_path"]
                ),
                "is_instance_definition_object": item[
                    "is_instance_definition_object"
                ],
                "transform": _transform_matrix(geometry.Xform),
            }
        )
    rows.sort(key=lambda item: item["object_id"])
    return rows


def _object_counts_by_layer(
    object_rows: list[dict[str, Any]],
    layers_by_index: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    counters: dict[int, Counter[str]] = defaultdict(Counter)
    for item in object_rows:
        counters[item["layer_index"]][item["type"]] += 1
    indexes = sorted(set(layers_by_index).union(counters))
    rows: list[dict[str, object]] = []
    for index in indexes:
        layer = layers_by_index.get(index)
        counts = counters[index]
        rows.append(
            {
                "layer_index": index,
                "layer_id": None if layer is None else layer["id"],
                "layer_path": (
                    None if layer is None else layer["full_path"]
                ),
                "total": sum(counts.values()),
                "by_type": _sorted_counts(counts),
            }
        )
    return rows


def _document_user_strings(table: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(len(table)):
        item = table[index]
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("document user string has invalid shape")
        rows.append(
            {
                "key": _string(item[0], "document user string key"),
                "value": _string(
                    item[1],
                    "document user string value",
                ),
            }
        )
    rows.sort(key=lambda item: (item["key"], item["value"]))
    return rows


def _object_user_strings(
    object_rows: list[dict[str, Any]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in object_rows:
        attributes = _user_strings(item["attributes"])
        geometry = _user_strings(item["geometry"])
        if not attributes and not geometry:
            continue
        rows.append(
            {
                "object_id": item["id"],
                "attributes": attributes,
                "geometry": geometry,
            }
        )
    return rows


def _user_strings(value: Any) -> list[dict[str, str]]:
    getter = getattr(value, "GetUserStrings", None)
    if not callable(getter):
        return []
    raw = getter()
    if raw is None:
        return []
    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("object user string has invalid shape")
        rows.append(
            {
                "key": _string(item[0], "object user string key"),
                "value": _string(item[1], "object user string value"),
            }
        )
    rows.sort(key=lambda item: (item["key"], item["value"]))
    return rows


def _aggregate_bbox(
    object_rows: list[dict[str, Any]],
    objects_by_id: dict[str, Any],
    definition_members: dict[str, tuple[str, ...]],
) -> tuple[dict[str, list[float]] | None, int]:
    minimum: list[float] | None = None
    maximum: list[float] | None = None
    geometry_count = 0

    def visit(
        item: Any,
        transforms: tuple[Any, ...],
        definition_stack: tuple[str, ...],
    ) -> None:
        nonlocal minimum, maximum, geometry_count
        geometry = item.Geometry
        geometry_type = _enum_name(geometry.ObjectType, "object type")
        if geometry_type == "InstanceReference":
            definition_id = _identifier(
                geometry.ParentIdefId,
                "reference definition id",
            )
            if definition_id in definition_stack:
                raise ValueError("cyclic instance definition reference")
            members = definition_members.get(definition_id)
            if members is None:
                raise ValueError("instance reference has no definition")
            for member_id in members:
                member = objects_by_id.get(member_id)
                if member is None:
                    raise ValueError("instance definition member is missing")
                visit(
                    member,
                    (*transforms, geometry.Xform),
                    (*definition_stack, definition_id),
                )
            return

        bbox = geometry.GetBoundingBox()
        if bbox is None or not bool(bbox.IsValid):
            return
        corners = _bbox_corners(bbox)
        for transform in reversed(transforms):
            corners = [
                _transform_point(point, transform) for point in corners
            ]
        for point in corners:
            if minimum is None:
                minimum = list(point)
                maximum = list(point)
                continue
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
        geometry_count += 1

    for row in object_rows:
        if not row["is_instance_definition_object"]:
            visit(row["source"], (), ())

    if minimum is None or maximum is None:
        return None, geometry_count
    return {"min": minimum, "max": maximum}, geometry_count


def _bbox_corners(bbox: Any) -> list[tuple[float, float, float]]:
    minimum = _point(bbox.Min, "bounding-box minimum")
    maximum = _point(bbox.Max, "bounding-box maximum")
    return [
        (x, y, z)
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]


def _transform_point(
    point: tuple[float, float, float],
    transform: Any,
) -> tuple[float, float, float]:
    x, y, z = point
    values = (
        transform.M00 * x
        + transform.M01 * y
        + transform.M02 * z
        + transform.M03,
        transform.M10 * x
        + transform.M11 * y
        + transform.M12 * z
        + transform.M13,
        transform.M20 * x
        + transform.M21 * y
        + transform.M22 * z
        + transform.M23,
    )
    weight = (
        transform.M30 * x
        + transform.M31 * y
        + transform.M32 * z
        + transform.M33
    )
    weight = _number(weight, "transform weight")
    if weight == 0:
        raise ValueError("instance transform has zero homogeneous weight")
    return tuple(
        _number(value / weight, "transformed coordinate")
        for value in values
    )


def _transform_matrix(transform: Any) -> list[list[float]]:
    return [
        [
            _number(getattr(transform, f"M{row}{column}"), "transform")
            for column in range(4)
        ]
        for row in range(4)
    ]


def _point(value: Any, field: str) -> tuple[float, float, float]:
    return (
        _number(value.X, field),
        _number(value.Y, field),
        _number(value.Z, field),
    )


def _units(value: Any) -> dict[str, object]:
    return {
        "name": _enum_name(value, "unit system"),
        "code": _integer(value, "unit system"),
    }


def _three_dm_version(archive_version: int) -> int:
    if archive_version >= 50 and archive_version % 10 == 0:
        return archive_version // 10
    return archive_version


def _sorted_counts(counts: Counter[str]) -> dict[str, int]:
    return {key: counts[key] for key in sorted(counts)}


def _enum_name(value: Any, field: str) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    text = str(value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return _string(text, field)


def _identifier(value: Any, field: str) -> str:
    text = str(value)
    if not text:
        raise ValueError(f"{field} cannot be empty")
    return text.lower()


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return value


def _integer(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{field} must be an integer") from exc
    return result


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return 0.0 if result == 0 else result


__all__ = [
    "ThreeDmInspection",
    "ThreeDmInspectionError",
    "ThreeDmInspectionErrorCode",
    "inspect_three_dm",
]
