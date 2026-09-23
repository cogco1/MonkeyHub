"""Headless, read-only inspection of Rhino ``.3dm`` files.

The adapter imports ``rhino3dm`` lazily, reads one immutable byte snapshot,
and returns JSON-compatible metadata.  It never imports RhinoCommon, starts
Rhino, or chooses a persistence destination.
"""

from __future__ import annotations

import hashlib
import importlib
import json

from archflow.contracts.canonical import canonical_digest
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar
from archflow.project.version_refs import (
    register as _register_version_refs,
    register_derived as _register_derived_fields,
)


_MAX_ERROR_TEXT = 1_000
_EMPTY_UUID = "00000000-0000-0000-0000-000000000000"


class ThreeDmInspectionErrorCode(StrEnum):
    DEPENDENCY_UNAVAILABLE = "three_dm_inspection.dependency_unavailable"
    FILE_NOT_FOUND = "three_dm_inspection.file_not_found"
    PATH_NOT_FILE = "three_dm_inspection.path_not_file"
    FILE_UNREADABLE = "three_dm_inspection.file_unreadable"
    INVALID_FILE = "three_dm_inspection.invalid_file"
    VISIBLE_BOUNDS_UNAVAILABLE = (
        "three_dm_inspection.visible_bounds_unavailable"
    )


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
    visible_bounds_witnesses: tuple[dict[str, object], ...] = ()
    materials: tuple[dict[str, object], ...] = ()
    render_materials: tuple[dict[str, object], ...] = ()
    object_material_bindings: tuple[dict[str, object], ...] = ()
    object_geometry_sha256: tuple[dict[str, object], ...] = ()
    object_geometry_analysis: tuple[dict[str, object], ...] = ()
    read_only: bool = True
    rhino_process_started: bool = False
    invalid_geometry_object_ids: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "ThreeDmInspectionSummary@4"

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
            "visible_bounds_witnesses": self.visible_bounds_witnesses,
            "materials": self.materials,
            "render_materials": self.render_materials,
            "object_material_bindings": self.object_material_bindings,
            "object_geometry_sha256": self.object_geometry_sha256,
            "object_geometry_analysis": self.object_geometry_analysis,
            "read_only": self.read_only,
            "rhino_process_started": self.rhino_process_started,
        }
        if self.invalid_geometry_object_ids:
            payload["invalid_geometry_object_ids"] = list(self.invalid_geometry_object_ids)
        return json.loads(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


# Single-source schema key sets (P088).  Consumers import these instead
# of hand-copying key lists; the @1 core is exactly the field set every
# retained version shares and every read-only renderer requires.
_V1_INSPECTION_KEYS = frozenset(
    {
        "schema",
        "file_sha256",
        "file_bytes",
        "three_dm_version",
        "archive_version",
        "units",
        "layers",
        "object_count",
        "top_level_object_count",
        "instance_definition_member_count",
        "object_counts_by_type",
        "object_counts_by_layer",
        "instance_definitions",
        "instance_references",
        "document_user_strings",
        "object_user_strings",
        "aggregate_bbox",
        "bbox_contributing_geometry_count",
        "read_only",
        "rhino_process_started",
    }
)
_V3_ADDED_KEYS = frozenset(
    {
        "named_object_bboxes",
        "visible_bounds_witnesses",
        "materials",
        "render_materials",
        "object_material_bindings",
        "object_geometry_sha256",
    }
)
_V4_ADDED_KEYS = frozenset({"object_geometry_analysis"})

REQUIRED_INSPECTION_KEYS = _V1_INSPECTION_KEYS
INSPECTION_SCHEMA_KEY_SETS: dict[str, frozenset[str]] = {
    "ThreeDmInspectionSummary@1": _V1_INSPECTION_KEYS,
    "ThreeDmInspectionSummary@3": _V1_INSPECTION_KEYS | _V3_ADDED_KEYS,
    "ThreeDmInspectionSummary@4": (
        _V1_INSPECTION_KEYS | _V3_ADDED_KEYS | _V4_ADDED_KEYS
    ),
}
SUPPORTED_INSPECTION_SCHEMAS = frozenset(INSPECTION_SCHEMA_KEY_SETS)


def inspect_three_dm(path: Path) -> ThreeDmInspection:
    """Return a deterministic JSON-compatible summary of one ``.3dm`` file.

    The SHA-256 digest and the decoded model are derived from the same byte
    snapshot, preventing path replacement between hashing and inspection.
    """

    if not isinstance(path, Path):
        raise TypeError("path must be pathlib.Path")
    file_bytes = _read_file(path)
    return _inspect_bytes(file_bytes, include_geometry=True)


def inspect_three_dm_contents(data: bytes) -> ThreeDmInspection:
    """Read original container contents without certifying geometry or requiring render meshes.

    Imported assets can contain invalid hidden Breps beside valid display meshes.
    Their original geometry, attributes, definitions and materials remain inspectable;
    bounds and geometric analysis are deliberately absent, and invalid ids are named.
    Ordinary export inspection continues to use ``inspect_three_dm`` unchanged.
    """

    if not isinstance(data, bytes):
        raise TypeError("3dm contents must be bytes")
    return _inspect_bytes(data, include_geometry=False)


def inspect_three_dm_index(data: bytes) -> dict[str, object]:
    """Read native identities and relations without inspecting geometry content.

    This index includes unnamed objects and block definition members as saved.
    Names, layers and user strings are source metadata, not inferred semantics;
    visibility and material source are the native object attributes, not resolved
    display properties. Geometry validity, bounds, encoding and morphology are
    deliberately outside this inexpensive lookup path.
    """

    if not isinstance(data, bytes):
        raise TypeError("3dm contents must be bytes")
    rhino3dm = _load_rhino3dm()
    model = _decode_model(data, rhino3dm)
    try:
        objects = tuple(model.Objects)
        layers, layers_by_index = _layers(model, objects)
        rows: list[dict[str, Any]] = []
        object_ids: set[str] = set()
        for item in objects:
            attributes = item.Attributes
            geometry = item.Geometry
            if geometry is None:
                raise ValueError("3dm object has no geometry")
            object_id = _identifier(attributes.Id, "object id")
            if object_id in object_ids:
                raise ValueError("duplicate object id")
            object_ids.add(object_id)
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
                "visible": bool(attributes.Visible),
                "material_index": _integer(
                    attributes.MaterialIndex, "object material index"
                ),
                "material_source": _enum_name(
                    attributes.MaterialSource, "object material source"
                ),
                "attributes": attributes,
                "geometry": geometry,
            }
            mode = getattr(attributes, "Mode", None)
            if mode is not None:
                row["mode"] = _enum_name(mode, "object mode")
            rows.append(row)
        rows.sort(key=lambda item: item["id"])
        definitions, _ = _definitions(model)
        references = _references(rows, definitions, layers_by_index)
        reference_counts = Counter(item["definition_id"] for item in references)
        for definition in definitions:
            definition["reference_count"] = reference_counts.get(definition["id"], 0)
        for row in rows:
            row["object_id"] = row.pop("id")
            row["attributes"] = _user_strings(row["attributes"])
            row["geometry"] = _user_strings(row["geometry"])
        return {
            "file_sha256": hashlib.sha256(data).hexdigest(),
            "file_bytes": len(data),
            "archive_version": _integer(model.ArchiveVersion, "archive version"),
            "units": _units(model.Settings.ModelUnitSystem),
            "layers": layers,
            "objects": rows,
            "instance_definitions": definitions,
            "instance_references": references,
        }
    except ThreeDmInspectionError:
        raise
    except Exception as exc:
        raise ThreeDmInspectionError(
            ThreeDmInspectionErrorCode.INVALID_FILE,
            "the decoded 3dm model contains invalid index data",
        ) from exc


def _decode_model(file_bytes: bytes, rhino3dm: Any) -> Any:

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
    return model


def _inspect_bytes(file_bytes: bytes, *, include_geometry: bool) -> ThreeDmInspection:
    rhino3dm = _load_rhino3dm()
    model = _decode_model(file_bytes, rhino3dm)

    try:
        payload = _summarize_model(model, rhino3dm, include_geometry=include_geometry)
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


def _summarize_model(model: Any, rhino3dm: Any, *, include_geometry: bool = True) -> dict[str, object]:
    archive_objects = tuple(model.Objects)
    explicit_witnesses, witness_object_ids = _explicit_visible_witnesses(
        archive_objects,
        rhino3dm,
    )
    objects = tuple(
        item
        for item in archive_objects
        if _identifier(item.Attributes.Id, "object id")
        not in witness_object_ids
    )
    layers, layers_by_index = _layers(model, objects)
    object_rows, objects_by_id = _objects(objects, layers_by_index, require_valid_geometry=include_geometry)
    materials, materials_by_index = _materials(model)
    render_materials, render_materials_by_id = _render_materials(model)
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
        rhino3dm,
        explicit_witnesses,
    ) if include_geometry else (None, 0)
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
        "named_object_bboxes": tuple(
            _named_object_bboxes(
                object_rows,
                rhino3dm,
                explicit_witnesses,
            )
        ) if include_geometry else (),
        "visible_bounds_witnesses": tuple(
            _visible_bounds_witnesses(
                object_rows,
                rhino3dm,
                explicit_witnesses,
            )
        ) if include_geometry else (),
        "materials": tuple(materials),
        "render_materials": tuple(render_materials),
        "object_material_bindings": tuple(
            _object_material_bindings(
                object_rows,
                materials_by_index,
                render_materials_by_id,
            )
        ),
        "object_geometry_sha256": tuple(
            _object_geometry_sha256(object_rows)
        ),
        "object_geometry_analysis": tuple(_object_geometry_analysis(object_rows)) if include_geometry else (),
        "invalid_geometry_object_ids": tuple(item["id"] for item in object_rows if not item["is_valid"]),
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
            "color_rgba": _rgba(layer.Color, "layer color"),
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
    *, require_valid_geometry: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_id: dict[str, Any] = {}
    for item in objects:
        attributes = item.Attributes
        geometry = item.Geometry
        if geometry is None:
            raise ValueError("3dm object has no geometry")
        is_valid = not hasattr(geometry, "IsValid") or bool(geometry.IsValid)
        if require_valid_geometry and not is_valid:
            raise ValueError("3dm object contains invalid geometry")
        object_id = _identifier(attributes.Id, "object id")
        if object_id in by_id:
            raise ValueError("duplicate object id")
        layer_index = _integer(attributes.LayerIndex, "object layer index")
        layer = layers_by_index.get(layer_index)
        row = {
            "id": object_id,
            "is_valid": is_valid,
            "name": _string(attributes.Name, "object name"),
            "type": _enum_name(geometry.ObjectType, "object type"),
            # Some rhino3dm geometry encoders include transient ordering state.
            # Encode exactly once and reuse the digest everywhere in this
            # inspection so independent controller views cannot contradict.
            "geometry_sha256": _encoded_geometry_sha256(geometry),
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


def _materials(
    model: Any,
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    """Read the native openNURBS material table without renderer inference."""

    rows: list[dict[str, object]] = []
    by_index: dict[int, dict[str, object]] = {}
    for index, material in enumerate(model.Materials):
        material_id = _identifier(material.Id, "material id")
        render_material_id = _identifier(
            material.RenderMaterialInstanceId,
            "render material instance id",
        )
        physically_based = bool(material.PhysicallyBased.Supported)
        row: dict[str, object] = {
            "index": index,
            "id": material_id,
            "name": _string(material.Name, "material name"),
            "diffuse_color_rgba": _rgba(
                material.DiffuseColor,
                "material diffuse color",
            ),
            "transparency": _unit_interval(
                material.Transparency,
                "material transparency",
            ),
            "render_material_instance_id": (
                None if render_material_id == _EMPTY_UUID else render_material_id
            ),
            "physically_based": physically_based,
            "physically_based_base_color": (
                _color4f(
                    material.PhysicallyBased.BaseColor,
                    "physically based material base color",
                )
                if physically_based
                else None
            ),
            "physically_based_metallic": (
                _unit_interval(
                    material.PhysicallyBased.Metallic,
                    "physically based material metallic",
                )
                if physically_based
                else None
            ),
            "physically_based_roughness": (
                _unit_interval(
                    material.PhysicallyBased.Roughness,
                    "physically based material roughness",
                )
                if physically_based
                else None
            ),
            "user_strings": _user_strings(material),
        }
        rows.append(row)
        by_index[index] = row
    return rows, by_index


def _render_materials(
    model: Any,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    """Read persisted top-level render materials and their stable identities."""

    rows: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    for render_content in model.RenderContent:
        kind = _enum_name(render_content.Kind, "render content kind")
        if kind.lower() != "material":
            continue
        render_material_id = _identifier(
            render_content.Id,
            "render material id",
        )
        if render_material_id in by_id:
            raise ValueError("duplicate render material id")
        row = {
            "id": render_material_id,
            "name": _string(render_content.Name, "render material name"),
            "kind": kind,
            "type_name": _string(
                render_content.TypeName,
                "render material type name",
            ),
            "type_id": _identifier(
                render_content.TypeId,
                "render material type id",
            ),
            "render_engine_id": _identifier(
                render_content.RenderEngineId,
                "render material engine id",
            ),
            "plug_in_id": _identifier(
                render_content.PlugInId,
                "render material plug-in id",
            ),
        }
        rows.append(row)
        by_id[render_material_id] = row
    rows.sort(key=lambda item: (str(item["name"]), str(item["id"])))
    return rows, by_id


def _object_material_bindings(
    object_rows: list[dict[str, Any]],
    materials_by_index: dict[int, dict[str, object]],
    render_materials_by_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Resolve each saved object's native material source and table links."""

    rows: list[dict[str, object]] = []
    for item in object_rows:
        attributes = item["attributes"]
        material_index = _integer(
            attributes.MaterialIndex,
            "object material index",
        )
        material = materials_by_index.get(material_index)
        render_material_id = (
            None
            if material is None
            else material["render_material_instance_id"]
        )
        render_material = (
            render_materials_by_id.get(str(render_material_id))
            if render_material_id is not None
            else None
        )
        material_user_strings = (
            {}
            if material is None
            else {
                row["key"]: row["value"]
                for row in material["user_strings"]
            }
        )
        rows.append(
            {
                "object_id": item["id"],
                "name": item["name"],
                "layer_path": item["layer_path"],
                "is_instance_definition_object": item[
                    "is_instance_definition_object"
                ],
                "material_source": _enum_name(
                    attributes.MaterialSource,
                    "object material source",
                ),
                "material_source_code": _integer(
                    attributes.MaterialSource,
                    "object material source",
                ),
                "material_index": material_index,
                "material_id": None if material is None else material["id"],
                "material_name": (
                    None if material is None else material["name"]
                ),
                "material_diffuse_color_rgba": (
                    None
                    if material is None
                    else material["diffuse_color_rgba"]
                ),
                "material_transparency": (
                    None if material is None else material["transparency"]
                ),
                "archflow_material_id": material_user_strings.get(
                    "archflow:material_id"
                ),
                "render_material_instance_id": render_material_id,
                "render_material_name": (
                    None
                    if render_material is None
                    else render_material["name"]
                ),
            }
        )
    rows.sort(key=lambda item: str(item["object_id"]))
    return rows


def _object_geometry_sha256(
    object_rows: list[dict[str, Any]],
) -> list[dict[str, object]]:
    """Hash encoded geometry only; object attributes cannot affect the digest."""

    rows: list[dict[str, object]] = []
    for item in object_rows:
        rows.append(
            {
                "object_id": item["id"],
                "name": item["name"],
                "type": item["type"],
                "layer_path": item["layer_path"],
                "is_instance_definition_object": item[
                    "is_instance_definition_object"
                ],
                "geometry_sha256": item["geometry_sha256"],
            }
        )
    rows.sort(key=lambda item: str(item["object_id"]))
    return rows


_GEOMETRY_ANALYSIS_PLANAR_TOLERANCE = 1.0e-9
_GEOMETRY_ANALYSIS_CURVE_SAMPLE_COUNT = 17


def _encoded_geometry_sha256(geometry: Any) -> str:
    encoded = geometry.Encode()
    if not isinstance(encoded, dict):
        raise TypeError("encoded 3dm geometry must be a mapping")
    return canonical_digest(encoded)


def _surface_planarity_counts(
    surfaces: tuple[Any, ...],
) -> tuple[int, int]:
    planar = 0
    curved = 0
    for surface in surfaces:
        is_planar = getattr(surface, "IsPlanar", None)
        if not callable(is_planar):
            raise TypeError("surface does not expose deterministic planarity")
        if bool(is_planar(_GEOMETRY_ANALYSIS_PLANAR_TOLERANCE)):
            planar += 1
        else:
            curved += 1
    return planar, curved


def _curve_analysis(geometry: Any) -> dict[str, object]:
    domain = geometry.Domain
    lower = _number(domain.T0, "curve-domain lower bound")
    upper = _number(domain.T1, "curve-domain upper bound")
    if not upper > lower:
        raise ValueError("curve domain must have positive extent")
    samples = [
        _point(
            geometry.PointAt(
                lower
                + (upper - lower)
                * index
                / (_GEOMETRY_ANALYSIS_CURVE_SAMPLE_COUNT - 1)
            ),
            "curve analysis sample",
        )
        for index in range(_GEOMETRY_ANALYSIS_CURVE_SAMPLE_COUNT)
    ]
    result = {
        "curve_start": list(_point(geometry.PointAtStart, "curve start")),
        "curve_end": list(_point(geometry.PointAtEnd, "curve end")),
        "curve_closed": bool(geometry.IsClosed),
        "curve_degree": _integer(geometry.Degree, "curve degree"),
        "curve_span_count": _integer(
            geometry.SpanCount,
            "curve span count",
        ),
        "curve_samples": [list(item) for item in samples],
    }
    polyline = geometry.TryGetPolyline()
    # Drawn profiles contain at most 512 points. Larger imported paths retain
    # the bounded samples above instead of enlarging every inspection payload.
    if polyline is not None and len(polyline) <= 512:
        points = [list(_point(point, "polyline vertex")) for point in polyline]
        result.update(curve_points=points, curve_length=sum(math.dist(a, b) for a, b in zip(points, points[1:])))
    return result


def _object_geometry_analysis(
    object_rows: list[dict[str, Any]],
) -> list[dict[str, object]]:
    """Read morphology facts from encoded geometry, never object metadata.

    The analysis is intentionally narrow.  It exposes only the facts needed by
    generic morphology/continuity gates: planar versus non-planar surface
    denominators, solid/closed/manifold state, and a fixed complete curve
    sample from exact curve endpoints.  User strings cannot affect any value.
    """

    rows: list[dict[str, object]] = []
    for item in object_rows:
        geometry = item["geometry"]
        geometry_type = str(item["type"])
        face_count: int | None = None
        planar_face_count: int | None = None
        curved_face_count: int | None = None
        is_closed: bool | None = None
        is_solid: bool | None = None
        is_manifold: bool | None = None
        curve_values: dict[str, object] = {
            "curve_start": None,
            "curve_end": None,
            "curve_closed": None,
            "curve_degree": None,
            "curve_span_count": None,
            "curve_samples": [],
        }

        surface_geometry: Any | None = None
        if geometry_type == "Brep":
            surface_geometry = geometry
        elif geometry_type == "Extrusion":
            surface_geometry = geometry.ToBrep(True)
            if surface_geometry is None:
                raise ValueError("extrusion could not be converted to a Brep")

        if surface_geometry is not None:
            faces = tuple(surface_geometry.Faces)
            planar_face_count, curved_face_count = _surface_planarity_counts(
                tuple(face.UnderlyingSurface() for face in faces)
            )
            face_count = len(faces)
            is_solid = bool(surface_geometry.IsSolid)
            is_closed = is_solid
            is_manifold = bool(surface_geometry.IsManifold)
        elif geometry_type == "Surface":
            planar_face_count, curved_face_count = _surface_planarity_counts(
                (geometry,)
            )
            face_count = 1
            is_solid = False
            is_closed = False
        elif geometry_type == "Mesh":
            face_count = len(tuple(geometry.Faces))
            planar_face_count = face_count
            curved_face_count = 0
            is_closed = bool(geometry.IsClosed)
            is_solid = is_closed
            manifold = geometry.IsManifold(True)
            if not isinstance(manifold, tuple) or len(manifold) != 3:
                raise TypeError("mesh manifold analysis changed shape")
            is_manifold = bool(manifold[0])
        elif geometry_type == "Curve":
            curve_values = _curve_analysis(geometry)

        rows.append(
            {
                "object_id": item["id"],
                "name": item["name"],
                "type": geometry_type,
                "layer_path": item["layer_path"],
                "is_instance_definition_object": item[
                    "is_instance_definition_object"
                ],
                "geometry_sha256": item["geometry_sha256"],
                "face_count": face_count,
                "planar_face_count": planar_face_count,
                "curved_face_count": curved_face_count,
                "is_closed": is_closed,
                "is_solid": is_solid,
                "is_manifold": is_manifold,
                **curve_values,
            }
        )
    rows.sort(key=lambda item: str(item["object_id"]))
    return rows


def _named_object_bboxes(
    object_rows: list[dict[str, Any]],
    rhino3dm: Any,
    explicit_witnesses: dict[str, dict[str, object]],
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
        points, witness = _visible_geometry_points(
            geometry,
            str(item["type"]),
            rhino3dm,
            object_id=str(item["id"]),
            explicit_witnesses=explicit_witnesses,
        )
        rows.append(
            {
                "object_id": item["id"],
                "name": item["name"],
                "type": item["type"],
                "layer_path": item["layer_path"],
                "bbox": _points_bbox(points),
                "bbox_source": witness["source"],
                "mesh_face_count": witness["mesh_face_count"],
                "mesh_vertex_count": witness["mesh_vertex_count"],
            }
        )
    rows.sort(key=lambda item: (str(item["name"]), str(item["object_id"])))
    return rows


def _visible_bounds_witnesses(
    object_rows: list[dict[str, Any]],
    rhino3dm: Any,
    explicit_witnesses: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Describe the exact saved-geometry source used for each leaf bound."""

    rows: list[dict[str, object]] = []
    for item in object_rows:
        if item["type"] == "InstanceReference":
            continue
        _, witness = _visible_geometry_points(
            item["geometry"],
            str(item["type"]),
            rhino3dm,
            object_id=str(item["id"]),
            explicit_witnesses=explicit_witnesses,
        )
        rows.append(
            {
                "object_id": item["id"],
                "name": item["name"],
                "type": item["type"],
                **witness,
            }
        )
    rows.sort(key=lambda item: str(item["object_id"]))
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
                "name": item["name"],
                "layer_path": item["layer_path"],
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


def _explicit_visible_witnesses(
    objects: tuple[Any, ...],
    rhino3dm: Any,
) -> tuple[dict[str, dict[str, object]], frozenset[str]]:
    """Load hidden mesh objects that witness trimmed saved geometry.

    RhinoDoc render-mesh caches are not serialized as ``BrepFace`` meshes in
    every 3DM write path.  The exporter therefore stores those same trimmed
    meshes as hidden geometry with an exact source-object mapping.  The mesh
    coordinates remain independently readable; user strings provide identity,
    never bounds or pass/fail authority.
    """

    objects_by_id = {
        _identifier(item.Attributes.Id, "object id"): item for item in objects
    }
    grouped: dict[str, list[tuple[int, int, str, Any]]] = defaultdict(list)
    witness_object_ids: set[str] = set()
    for item in objects:
        object_id = _identifier(item.Attributes.Id, "object id")
        attributes = {row["key"]: row["value"] for row in _user_strings(item.Attributes)}
        source_id = attributes.get("archflow:visible_bounds_witness_for")
        if source_id is None:
            continue
        source_id = _identifier(source_id, "visible-bounds witness source id")
        index_text = attributes.get("archflow:visible_bounds_witness_index")
        count_text = attributes.get("archflow:visible_bounds_witness_count")
        if index_text is None or count_text is None:
            raise ValueError("visible-bounds witness metadata is incomplete")
        try:
            index = int(index_text)
            count = int(count_text)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("visible-bounds witness index/count is invalid") from exc
        if index < 0 or count <= 0 or str(index) != index_text or str(count) != count_text:
            raise ValueError("visible-bounds witness index/count is non-canonical")
        expected_name = f"__archflow_visible_bounds__:{source_id}:{index:04d}"
        if _string(item.Attributes.Name, "witness object name") != expected_name:
            raise ValueError("visible-bounds witness name does not bind its source")
        if bool(item.Attributes.Visible):
            raise ValueError("visible-bounds witness must remain hidden")
        geometry = item.Geometry
        if _enum_name(geometry.ObjectType, "witness object type") != "Mesh":
            raise ValueError("visible-bounds witness must be mesh geometry")
        if hasattr(geometry, "IsValid") and not bool(geometry.IsValid):
            raise ValueError("visible-bounds witness mesh is invalid")
        vertices = tuple(geometry.Vertices)
        if not vertices or len(tuple(geometry.Faces)) == 0:
            raise ValueError("visible-bounds witness mesh is empty")
        if object_id == source_id or source_id not in objects_by_id:
            raise ValueError("visible-bounds witness source object is missing")
        grouped[source_id].append((index, count, object_id, geometry))
        witness_object_ids.add(object_id)

    result: dict[str, dict[str, object]] = {}
    for source_id, rows in grouped.items():
        rows.sort(key=lambda item: item[0])
        counts = {row[1] for row in rows}
        if len(counts) != 1:
            raise ValueError("visible-bounds witness count is inconsistent")
        count = next(iter(counts))
        if [row[0] for row in rows] != list(range(count)):
            raise ValueError("visible-bounds witness face denominator is incomplete")
        source_geometry = objects_by_id[source_id].Geometry
        source_type = _enum_name(source_geometry.ObjectType, "source object type")
        if source_type == "Brep":
            if len(tuple(source_geometry.Faces)) != count:
                raise ValueError("Brep witness count differs from source face count")
        elif source_type == "Extrusion":
            if count != 1:
                raise ValueError("extrusion requires exactly one witness mesh")
        else:
            raise ValueError("visible-bounds witness source is not meshable geometry")
        points = tuple(
            _point(vertex, "explicit visible-bounds witness vertex")
            for _, _, _, mesh in rows
            for vertex in mesh.Vertices
        )
        result[source_id] = {
            "points": points,
            "mesh_face_count": count,
            "mesh_vertex_count": len(points),
        }
    return result, frozenset(witness_object_ids)


def _aggregate_bbox(
    object_rows: list[dict[str, Any]],
    objects_by_id: dict[str, Any],
    definition_members: dict[str, tuple[str, ...]],
    rhino3dm: Any,
    explicit_witnesses: dict[str, dict[str, object]] | None = None,
) -> tuple[dict[str, list[float]] | None, int]:
    explicit_witnesses = explicit_witnesses or {}
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

        corners, _ = _visible_geometry_points(
            geometry,
            geometry_type,
            rhino3dm,
            object_id=_identifier(item.Attributes.Id, "object id"),
            explicit_witnesses=explicit_witnesses,
        )
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


def _visible_geometry_points(
    geometry: Any,
    geometry_type: str,
    rhino3dm: Any,
    *,
    object_id: str,
    explicit_witnesses: dict[str, dict[str, object]] | None = None,
) -> tuple[list[tuple[float, float, float]], dict[str, object]]:
    """Return points that bound saved visible geometry, never Brep controls.

    A Brep's ordinary openNURBS bounding box can include its untrimmed NURBS
    control surface.  Render meshes are face-owned, trimmed, and retained in
    the 3DM, so every face must provide one.  Missing, empty, or invalid mesh
    data is a named hard failure; there is deliberately no Brep fallback.
    """

    explicit = (explicit_witnesses or {}).get(object_id)
    if geometry_type in {"Brep", "Extrusion"} and explicit is not None:
        return list(explicit["points"]), {
            "source": "explicit_trimmed_render_mesh_witnesses",
            "mesh_face_count": explicit["mesh_face_count"],
            "mesh_vertex_count": explicit["mesh_vertex_count"],
        }

    if geometry_type == "Brep":
        faces = tuple(geometry.Faces)
        if not faces:
            _visible_bounds_error(object_id, "Brep has no faces")
        points: list[tuple[float, float, float]] = []
        for face_index, face in enumerate(faces):
            mesh = face.GetMesh(rhino3dm.MeshType.Render)
            if mesh is None:
                _visible_bounds_error(
                    object_id,
                    f"Brep face {face_index} has no retained render mesh",
                )
            if hasattr(mesh, "IsValid") and not bool(mesh.IsValid):
                _visible_bounds_error(
                    object_id,
                    f"Brep face {face_index} render mesh is invalid",
                )
            vertices = tuple(mesh.Vertices)
            if not vertices or len(tuple(mesh.Faces)) == 0:
                _visible_bounds_error(
                    object_id,
                    f"Brep face {face_index} render mesh is empty",
                )
            points.extend(
                _point(vertex, "retained render-mesh vertex")
                for vertex in vertices
            )
        return points, {
            "source": "brep_face_render_mesh_vertices",
            "mesh_face_count": len(faces),
            "mesh_vertex_count": len(points),
        }

    if geometry_type == "Extrusion":
        mesh = geometry.GetMesh(rhino3dm.MeshType.Render)
        if mesh is None:
            _visible_bounds_error(
                object_id,
                "Extrusion has no retained render mesh",
            )
        vertices = tuple(mesh.Vertices)
        if (
            (hasattr(mesh, "IsValid") and not bool(mesh.IsValid))
            or not vertices
            or len(tuple(mesh.Faces)) == 0
        ):
            _visible_bounds_error(object_id, "Extrusion render mesh is invalid")
        return [
            _point(vertex, "retained render-mesh vertex")
            for vertex in vertices
        ], {
            "source": "extrusion_render_mesh_vertices",
            "mesh_face_count": 1,
            "mesh_vertex_count": len(vertices),
        }

    if geometry_type == "Mesh":
        vertices = tuple(geometry.Vertices)
        if not vertices:
            _visible_bounds_error(object_id, "Mesh has no vertices")
        return [
            _point(vertex, "mesh vertex") for vertex in vertices
        ], {
            "source": "mesh_vertices",
            "mesh_face_count": 1,
            "mesh_vertex_count": len(vertices),
        }

    tight_getter = getattr(geometry, "GetTightBoundingBox", None)
    bbox = tight_getter() if callable(tight_getter) else geometry.GetBoundingBox()
    if bbox is None or not bool(bbox.IsValid):
        _visible_bounds_error(object_id, "geometry has no valid tight bounds")
    points = _bbox_corners(bbox)
    return points, {
        "source": "tight_geometry_bbox",
        "mesh_face_count": 0,
        "mesh_vertex_count": 0,
    }


def _visible_bounds_error(object_id: str, detail: str) -> None:
    raise ThreeDmInspectionError(
        ThreeDmInspectionErrorCode.VISIBLE_BOUNDS_UNAVAILABLE,
        f"object {object_id}: {detail}",
    )


def _points_bbox(
    points: list[tuple[float, float, float]],
) -> dict[str, list[float]]:
    if not points:
        raise ValueError("visible bounds require at least one point")
    return {
        "min": [min(point[axis] for point in points) for axis in range(3)],
        "max": [max(point[axis] for point in points) for axis in range(3)],
    }


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


def _rgba(value: Any, field: str) -> list[int]:
    if not isinstance(value, tuple) or len(value) != 4:
        raise TypeError(f"{field} must be a four-channel tuple")
    channels: list[int] = []
    for channel in value:
        if isinstance(channel, bool) or not isinstance(channel, int):
            raise TypeError(f"{field} channels must be integers")
        if channel < 0 or channel > 255:
            raise ValueError(f"{field} channels must be between 0 and 255")
        channels.append(channel)
    return channels


def _color4f(value: Any, field: str) -> list[float]:
    if not isinstance(value, tuple) or len(value) != 4:
        raise TypeError(f"{field} must be a four-channel tuple")
    channels = [_number(channel, field) for channel in value]
    if any(channel < 0.0 or channel > 1.0 for channel in channels):
        raise ValueError(f"{field} channels must be between 0 and 1")
    return channels


def _unit_interval(value: Any, field: str) -> float:
    number = _number(value, field)
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return number


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


# A CAD document carries the canonical base it was exported from as one of its
# user strings, not as a field named for a version: the row is
# ``{"key": "archflow:base_state_sha256", "value": <digest>}`` and its position
# moves with the sorted table, so the declaration names the row by its key.
# Without this a migrated model still reports the version it was built at.
VERSION_REF_POINTERS = {
    "ThreeDmInspectionSummary@4": (
        "/document_user_strings[key=archflow:base_state_sha256]/value",
    ),
}

_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("ThreeDmInspectionSummary@4", ())
