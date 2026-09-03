"""Material-only mutation of an existing Rhino 3DM candidate.

The adapter reads one exact source artifact, appends native openNURBS/PBR
materials, changes only object material attributes, and writes a new file in a
caller-supplied speculative workspace.  Independent readback proves that
object identities, encoded geometry, user text, block topology, and bounds did
not change.  It never overwrites the source or claims canonical authority.
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import ClassVar, Mapping

from archflow.adapters.three_dm_inspector import (
    ThreeDmInspection,
    ThreeDmInspectionError,
    inspect_three_dm,
)
from archflow.project.refs import require_identifier
from archflow.state.geometry_program import require_sha256
from archflow.contracts.canonical import canonical_digest


NATIVE_MATERIAL_PREFIX = "archflow-material:"
_FLOAT_TOLERANCE = 1e-6


class ThreeDmMaterializationError(ValueError):
    """The requested material-only 3DM transition is unsafe or ambiguous."""


class ThreeDmMaterializationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ThreeDmMaterialSpec:
    """One explicit native PBR material; no texture or renderer inference."""

    material_id: str
    base_color_rgb: tuple[int, int, int]
    metallic: float = 0.0
    roughness: float = 0.5

    SCHEMA: ClassVar[str] = "ThreeDmMaterialSpec@1"

    def __post_init__(self) -> None:
        require_identifier(self.material_id, "material_id")
        object.__setattr__(
            self,
            "base_color_rgb",
            _rgb_bytes(self.base_color_rgb, "base_color_rgb"),
        )
        object.__setattr__(
            self,
            "metallic",
            _unit_interval(self.metallic, "metallic"),
        )
        object.__setattr__(
            self,
            "roughness",
            _unit_interval(self.roughness, "roughness"),
        )

    @property
    def native_name(self) -> str:
        return f"{NATIVE_MATERIAL_PREFIX}{self.material_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "material_id": self.material_id,
            "native_name": self.native_name,
            "base_color_rgb": list(self.base_color_rgb),
            "metallic": self.metallic,
            "roughness": self.roughness,
        }


@dataclass(frozen=True, slots=True)
class ThreeDmMaterializationPlan:
    """Immutable source binding and exact object/material denominator."""

    source_path: Path
    workspace: Path
    output_path: Path
    source_sha256: str
    source_three_dm_version: int
    source_geometry_sha256: str
    source_identity_sha256: str
    source_bounds_sha256: str
    source_object_ids: tuple[str, ...]
    source_material_count: int
    source_render_material_count: int
    materials: tuple[ThreeDmMaterialSpec, ...]
    object_material_assignments: tuple[tuple[str, str], ...]
    parent_material_object_ids: tuple[str, ...]
    validation_denominator_sha256: str

    SCHEMA: ClassVar[str] = "ThreeDmMaterializationPlan@1"

    def __post_init__(self) -> None:
        for field in ("source_path", "workspace", "output_path"):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"{field} must be pathlib.Path")
        _validate_paths(self, require_source=True)
        object.__setattr__(
            self,
            "source_sha256",
            require_sha256(self.source_sha256, "source_sha256"),
        )
        for field in (
            "source_geometry_sha256",
            "source_identity_sha256",
            "source_bounds_sha256",
            "validation_denominator_sha256",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        if self.source_three_dm_version < 8:
            raise ThreeDmMaterializationError(
                "native PBR materialization requires a Rhino 8 3DM source"
            )
        if self.source_object_ids != tuple(sorted(set(self.source_object_ids))):
            raise ThreeDmMaterializationError(
                "source_object_ids must be sorted and unique"
            )
        if self.source_material_count < 0 or self.source_render_material_count < 0:
            raise ThreeDmMaterializationError(
                "source material counts cannot be negative"
            )
        material_ids = tuple(item.material_id for item in self.materials)
        if material_ids != tuple(sorted(set(material_ids))) or not material_ids:
            raise ThreeDmMaterializationError(
                "materials must be non-empty, sorted, and unique"
            )
        if self.object_material_assignments != tuple(
            sorted(set(self.object_material_assignments))
        ) or not self.object_material_assignments:
            raise ThreeDmMaterializationError(
                "object material assignments must be non-empty, sorted, and unique"
            )
        assigned_object_ids: set[str] = set()
        assigned_material_ids: set[str] = set()
        for object_id, material_id in self.object_material_assignments:
            if object_id in assigned_object_ids:
                raise ThreeDmMaterializationError(
                    "each saved object may receive only one material"
                )
            assigned_object_ids.add(object_id)
            assigned_material_ids.add(material_id)
        if not assigned_object_ids.issubset(self.source_object_ids):
            raise ThreeDmMaterializationError(
                "material assignments include unknown source objects"
            )
        if assigned_material_ids != set(material_ids):
            raise ThreeDmMaterializationError(
                "material specs and assignment denominator differ"
            )
        if self.parent_material_object_ids != tuple(
            sorted(set(self.parent_material_object_ids))
        ):
            raise ThreeDmMaterializationError(
                "parent material object ids must be sorted and unique"
            )
        if not set(self.parent_material_object_ids).issubset(
            self.source_object_ids
        ) or set(self.parent_material_object_ids) & assigned_object_ids:
            raise ThreeDmMaterializationError(
                "block inheritance objects are invalid or directly assigned"
            )
        if _plan_denominator_digest(self) != self.validation_denominator_sha256:
            raise ThreeDmMaterializationError(
                "validation denominator does not bind the exact materialization plan"
            )

    @property
    def output_relative_path(self) -> str:
        return self.output_path.relative_to(self.workspace).as_posix()

    @property
    def plan_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_artifact": {
                "name": self.source_path.name,
                "sha256": self.source_sha256,
                "three_dm_version": self.source_three_dm_version,
            },
            "output_relative_path": self.output_relative_path,
            "source_geometry_sha256": self.source_geometry_sha256,
            "source_identity_sha256": self.source_identity_sha256,
            "source_bounds_sha256": self.source_bounds_sha256,
            "source_object_ids": list(self.source_object_ids),
            "source_material_count": self.source_material_count,
            "source_render_material_count": self.source_render_material_count,
            "materials": [item.to_dict() for item in self.materials],
            "object_material_assignments": [
                {"object_id": object_id, "material_id": material_id}
                for object_id, material_id in self.object_material_assignments
            ],
            "parent_material_object_ids": list(
                self.parent_material_object_ids
            ),
            "validation_denominator_sha256": (
                self.validation_denominator_sha256
            ),
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ThreeDmMaterializationReceipt:
    """Independent output readback and source-lock evidence."""

    status: ThreeDmMaterializationStatus
    plan_digest: str
    source_sha256: str
    output_relative_path: str
    output_sha256: str | None
    source_geometry_sha256: str
    output_geometry_sha256: str | None
    source_identity_sha256: str
    output_identity_sha256: str | None
    source_bounds_sha256: str
    output_bounds_sha256: str | None
    inspection: dict[str, object] | None
    failures: tuple[dict[str, str], ...]

    SCHEMA: ClassVar[str] = "ThreeDmMaterializationReceipt@1"

    @property
    def readback_verified(self) -> bool:
        return (
            self.status is ThreeDmMaterializationStatus.SUCCEEDED
            and not self.failures
            and self.inspection is not None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "status": self.status.value,
            "plan_digest": self.plan_digest,
            "source_sha256": self.source_sha256,
            "output_relative_path": self.output_relative_path,
            "output_sha256": self.output_sha256,
            "source_geometry_sha256": self.source_geometry_sha256,
            "output_geometry_sha256": self.output_geometry_sha256,
            "source_identity_sha256": self.source_identity_sha256,
            "output_identity_sha256": self.output_identity_sha256,
            "source_bounds_sha256": self.source_bounds_sha256,
            "output_bounds_sha256": self.output_bounds_sha256,
            "inspection": self.inspection,
            "failures": list(self.failures),
            "readback_verified": self.readback_verified,
            "geometry_mutation_authority": False,
            "canonical_write_authority": False,
        }


def prepare_three_dm_materialization(
    *,
    source_model_path: Path,
    source_sha256: str,
    speculative_workspace: Path,
    artifact_name: str,
    materials: tuple[ThreeDmMaterialSpec, ...],
    material_by_component: Mapping[str, str] | None = None,
    material_by_object_id: Mapping[str, str] | None = None,
) -> ThreeDmMaterializationPlan:
    """Bind one exact existing 3DM and expand selectors to saved object UUIDs."""

    source = _strict_source(source_model_path)
    workspace = _strict_workspace(speculative_workspace)
    output = workspace / _artifact_name(artifact_name)
    _strict_child(workspace, output, require_exists=False)
    if output.exists():
        raise ThreeDmMaterializationError(
            f"speculative output already exists: {output.name}"
        )
    if output.resolve(strict=False) == source:
        raise ThreeDmMaterializationError("source 3DM cannot be overwritten")
    expected_source_sha256 = require_sha256(source_sha256, "source_sha256")
    inspection = inspect_three_dm(source)
    if inspection.file_sha256 != expected_source_sha256:
        raise ThreeDmMaterializationError(
            "source 3DM SHA-256 differs from the requested predecessor"
        )
    if inspection.three_dm_version < 8:
        raise ThreeDmMaterializationError(
            "native PBR materialization requires a Rhino 8 3DM source"
        )
    if any(
        str(row.get("name", "")).startswith(NATIVE_MATERIAL_PREFIX)
        for row in (*inspection.materials, *inspection.render_materials)
    ):
        raise ThreeDmMaterializationError(
            "source already owns the reserved ArchFlow material namespace"
        )

    ordered_materials = tuple(sorted(materials, key=lambda item: item.material_id))
    if any(not isinstance(item, ThreeDmMaterialSpec) for item in ordered_materials):
        raise TypeError("materials must contain ThreeDmMaterialSpec values")
    assignments = _resolve_assignments(
        inspection,
        material_by_component=material_by_component,
        material_by_object_id=material_by_object_id,
    )
    material_ids = {item.material_id for item in ordered_materials}
    assigned_material_ids = {material_id for _, material_id in assignments}
    if material_ids != assigned_material_ids:
        raise ThreeDmMaterializationError(
            "material specs must exactly match the materials used by assignments"
        )
    parent_material_object_ids = _parent_material_object_ids(
        inspection,
        assignments,
    )
    source_object_ids = tuple(
        sorted(row["object_id"] for row in inspection.object_geometry_sha256)
    )
    source_geometry_sha256 = _geometry_digest(inspection)
    source_identity_sha256 = _identity_digest(inspection)
    source_bounds_sha256 = _bounds_digest(inspection)
    source_material_count = len(inspection.materials)
    source_render_material_count = len(inspection.render_materials)
    denominator = _denominator_digest(
        source_sha256=inspection.file_sha256,
        source_three_dm_version=inspection.three_dm_version,
        source_geometry_sha256=source_geometry_sha256,
        source_identity_sha256=source_identity_sha256,
        source_bounds_sha256=source_bounds_sha256,
        source_object_ids=source_object_ids,
        source_material_count=source_material_count,
        source_render_material_count=source_render_material_count,
        materials=ordered_materials,
        object_material_assignments=assignments,
        parent_material_object_ids=parent_material_object_ids,
        output_relative_path=output.relative_to(workspace).as_posix(),
    )
    return ThreeDmMaterializationPlan(
        source_path=source,
        workspace=workspace,
        output_path=output,
        source_sha256=inspection.file_sha256,
        source_three_dm_version=inspection.three_dm_version,
        source_geometry_sha256=source_geometry_sha256,
        source_identity_sha256=source_identity_sha256,
        source_bounds_sha256=source_bounds_sha256,
        source_object_ids=source_object_ids,
        source_material_count=source_material_count,
        source_render_material_count=source_render_material_count,
        materials=ordered_materials,
        object_material_assignments=assignments,
        parent_material_object_ids=parent_material_object_ids,
        validation_denominator_sha256=denominator,
    )


def execute_three_dm_materialization(
    plan: ThreeDmMaterializationPlan,
) -> ThreeDmMaterializationReceipt:
    """Apply material metadata only, then independently reopen and verify."""

    if not isinstance(plan, ThreeDmMaterializationPlan):
        raise TypeError("plan must be ThreeDmMaterializationPlan")
    try:
        _validate_paths(plan, require_source=True)
        if _plan_denominator_digest(plan) != plan.validation_denominator_sha256:
            raise ThreeDmMaterializationError(
                "validation denominator changed after plan preparation"
            )
        if plan.output_path.exists():
            raise ThreeDmMaterializationError(
                "speculative output appeared before materialization"
            )
        source_inspection = inspect_three_dm(plan.source_path)
        _validate_source_inspection(plan, source_inspection)
        source_bytes = plan.source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != plan.source_sha256:
            raise ThreeDmMaterializationError(
                "source 3DM changed between inspection and decode"
            )
        rhino3dm = _load_rhino3dm()
        model = rhino3dm.File3dm.FromByteArray(source_bytes)
        if model is None:
            raise ThreeDmMaterializationError("source 3DM could not be decoded")
        _apply_materials(model, rhino3dm, plan)
        _write_exclusive(model, plan)
        output_inspection = inspect_three_dm(plan.output_path)
        receipt = verify_three_dm_materialization_readback(
            plan,
            source_inspection,
            output_inspection,
        )
        if hashlib.sha256(plan.source_path.read_bytes()).hexdigest() != (
            plan.source_sha256
        ):
            return _failed_receipt(
                plan,
                "three_dm_materialization.source_changed",
                "source 3DM changed during materialization",
                output_inspection=output_inspection,
            )
        return receipt
    except (
        OSError,
        ThreeDmInspectionError,
        ThreeDmMaterializationError,
    ) as exc:
        return _failed_receipt(
            plan,
            "three_dm_materialization.execution_failed",
            str(exc),
        )


def verify_three_dm_materialization_readback(
    plan: ThreeDmMaterializationPlan,
    source_inspection: ThreeDmInspection,
    output_inspection: ThreeDmInspection,
) -> ThreeDmMaterializationReceipt:
    """Verify the exact allowed metadata delta from two independent reads."""

    if not isinstance(plan, ThreeDmMaterializationPlan):
        raise TypeError("plan must be ThreeDmMaterializationPlan")
    if not isinstance(source_inspection, ThreeDmInspection) or not isinstance(
        output_inspection,
        ThreeDmInspection,
    ):
        raise TypeError("source and output inspections must be ThreeDmInspection")
    failures: list[dict[str, str]] = []
    try:
        _validate_source_inspection(plan, source_inspection)
    except ThreeDmMaterializationError as exc:
        failures.append(
            _failure(
                "three_dm_materialization.source_mismatch",
                str(exc),
            )
        )

    output_geometry_sha256 = _geometry_digest(output_inspection)
    output_identity_sha256 = _identity_digest(output_inspection)
    output_bounds_sha256 = _bounds_digest(output_inspection)
    if output_geometry_sha256 != plan.source_geometry_sha256:
        failures.append(
            _failure(
                "three_dm_materialization.geometry_changed",
                "per-object encoded geometry SHA-256 set changed",
            )
        )
    if output_identity_sha256 != plan.source_identity_sha256:
        failures.append(
            _failure(
                "three_dm_materialization.object_identity_changed",
                "object UUID/name/type/layer, user text, or block topology changed",
            )
        )
    if output_bounds_sha256 != plan.source_bounds_sha256:
        failures.append(
            _failure(
                "three_dm_materialization.bounds_changed",
                "aggregate or per-object visible bounds changed",
            )
        )
    if output_inspection.render_materials != source_inspection.render_materials:
        failures.append(
            _failure(
                "three_dm_materialization.render_content_changed",
                "headless materialization must not invent renderer-specific content",
            )
        )
    if len(output_inspection.materials) != (
        plan.source_material_count + len(plan.materials)
    ):
        failures.append(
            _failure(
                "three_dm_materialization.material_count_mismatch",
                "native material-table count differs from the exact delta",
            )
        )
    if tuple(output_inspection.materials[: plan.source_material_count]) != (
        source_inspection.materials
    ):
        failures.append(
            _failure(
                "three_dm_materialization.predecessor_material_changed",
                "pre-existing native material rows changed",
            )
        )

    expected_specs = {item.native_name: item for item in plan.materials}
    actual_new_materials: dict[str, list[dict[str, object]]] = {}
    for row in output_inspection.materials:
        name = str(row.get("name", ""))
        if name.startswith(NATIVE_MATERIAL_PREFIX):
            actual_new_materials.setdefault(name, []).append(row)
    if set(actual_new_materials) != set(expected_specs) or any(
        len(rows) != 1 for rows in actual_new_materials.values()
    ):
        failures.append(
            _failure(
                "three_dm_materialization.material_set_mismatch",
                "native ArchFlow PBR material set has missing, duplicate, or "
                "extra rows",
            )
        )
    for name, spec in expected_specs.items():
        rows = actual_new_materials.get(name, [])
        if len(rows) != 1:
            continue
        row = rows[0]
        user_strings = {
            item["key"]: item["value"] for item in row.get("user_strings", ())
        }
        expected_base_color = [
            channel / 255.0 for channel in (*spec.base_color_rgb, 255)
        ]
        if (
            row.get("diffuse_color_rgba") != [*spec.base_color_rgb, 255]
            or not row.get("physically_based")
            or not _float_sequence_close(
                row.get("physically_based_base_color"),
                expected_base_color,
            )
            or not _float_close(
                row.get("physically_based_metallic"),
                spec.metallic,
            )
            or not _float_close(
                row.get("physically_based_roughness"),
                spec.roughness,
            )
            or user_strings.get("archflow:material_id") != spec.material_id
            or row.get("render_material_instance_id") is not None
        ):
            failures.append(
                _failure(
                    "three_dm_materialization.material_value_mismatch",
                    f"native PBR material {spec.material_id} differs from its spec",
                )
            )

    source_bindings = {
        row["object_id"]: row
        for row in source_inspection.object_material_bindings
    }
    output_bindings = {
        row["object_id"]: row
        for row in output_inspection.object_material_bindings
    }
    if set(source_bindings) != set(output_bindings):
        failures.append(
            _failure(
                "three_dm_materialization.material_binding_denominator_changed",
                "saved object UUID set changed while reading material bindings",
            )
        )
    assignments = dict(plan.object_material_assignments)
    parent_ids = set(plan.parent_material_object_ids)
    untouched_ids = set(source_bindings) - set(assignments) - parent_ids
    if any(
        source_bindings[object_id] != output_bindings.get(object_id)
        for object_id in untouched_ids
    ):
        failures.append(
            _failure(
                "three_dm_materialization.unassigned_material_changed",
                "an unassigned object's native material binding changed",
            )
        )
    specs_by_id = {item.material_id: item for item in plan.materials}
    for object_id, material_id in assignments.items():
        row = output_bindings.get(object_id)
        spec = specs_by_id[material_id]
        if (
            row is None
            or row.get("material_source") != "MaterialFromObject"
            or row.get("material_name") != spec.native_name
            or row.get("material_diffuse_color_rgba")
            != [*spec.base_color_rgb, 255]
            or row.get("archflow_material_id") != material_id
        ):
            failures.append(
                _failure(
                    "three_dm_materialization.object_attachment_mismatch",
                    f"object {object_id} does not carry material {material_id}",
                )
            )
    for object_id in parent_ids:
        row = output_bindings.get(object_id)
        if (
            row is None
            or row.get("material_source") != "MaterialFromParent"
            or row.get("material_index") != -1
        ):
            failures.append(
                _failure(
                    "three_dm_materialization.block_inheritance_mismatch",
                    f"definition member {object_id} does not inherit from its instance",
                )
            )

    return ThreeDmMaterializationReceipt(
        status=(
            ThreeDmMaterializationStatus.FAILED
            if failures
            else ThreeDmMaterializationStatus.SUCCEEDED
        ),
        plan_digest=plan.plan_digest,
        source_sha256=plan.source_sha256,
        output_relative_path=plan.output_relative_path,
        output_sha256=output_inspection.file_sha256,
        source_geometry_sha256=plan.source_geometry_sha256,
        output_geometry_sha256=output_geometry_sha256,
        source_identity_sha256=plan.source_identity_sha256,
        output_identity_sha256=output_identity_sha256,
        source_bounds_sha256=plan.source_bounds_sha256,
        output_bounds_sha256=output_bounds_sha256,
        inspection=output_inspection.to_dict(),
        failures=tuple(failures),
    )


def _resolve_assignments(
    inspection: ThreeDmInspection,
    *,
    material_by_component: Mapping[str, str] | None,
    material_by_object_id: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    if (material_by_component is None) == (material_by_object_id is None):
        raise ThreeDmMaterializationError(
            "provide exactly one of material_by_component or material_by_object_id"
        )
    top_level_ids = {
        row["object_id"]
        for row in inspection.object_geometry_sha256
        if not row["is_instance_definition_object"]
    }
    if material_by_object_id is not None:
        normalized: dict[str, str] = {}
        for raw_object_id, material_id in material_by_object_id.items():
            object_id = str(raw_object_id).lower()
            if object_id in normalized:
                raise ThreeDmMaterializationError(
                    "material_by_object_id contains duplicate UUIDs"
                )
            if object_id not in top_level_ids:
                raise ThreeDmMaterializationError(
                    f"material assignment object is not top-level: {object_id}"
                )
            require_identifier(material_id, "material assignment material id")
            normalized[object_id] = material_id
        if not normalized:
            raise ThreeDmMaterializationError(
                "material_by_object_id cannot be empty"
            )
        return tuple(sorted(normalized.items()))

    component_map = dict(material_by_component or {})
    if not component_map:
        raise ThreeDmMaterializationError(
            "material_by_component cannot be empty"
        )
    for component_id, material_id in component_map.items():
        require_identifier(component_id, "material component id")
        require_identifier(material_id, "material assignment material id")
    matched_components: set[str] = set()
    assignments: list[tuple[str, str]] = []
    definition_members = {
        object_id
        for definition in inspection.instance_definitions
        for object_id in definition["object_ids"]
    }
    for row in inspection.object_user_strings:
        object_id = row["object_id"]
        if object_id in definition_members or object_id not in top_level_ids:
            continue
        attributes = {
            item["key"]: item["value"] for item in row.get("attributes", ())
        }
        component_text = attributes.get("archflow:component")
        if component_text is None:
            continue
        components = tuple(item for item in component_text.split("+") if item)
        material_ids = {
            component_map[component]
            for component in components
            if component in component_map
        }
        matched_components.update(
            component for component in components if component in component_map
        )
        if len(material_ids) > 1:
            raise ThreeDmMaterializationError(
                f"object {object_id} resolves to more than one material"
            )
        if material_ids:
            assignments.append((object_id, next(iter(material_ids))))
    unmatched = set(component_map) - matched_components
    if unmatched:
        raise ThreeDmMaterializationError(
            "material components have no saved top-level objects: "
            + ", ".join(sorted(unmatched))
        )
    if not assignments:
        raise ThreeDmMaterializationError(
            "component material map resolved no saved objects"
        )
    return tuple(sorted(assignments))


def _parent_material_object_ids(
    inspection: ThreeDmInspection,
    assignments: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    reference_definition = {
        row["object_id"]: row["definition_id"]
        for row in inspection.instance_references
    }
    definitions = {
        row["id"]: tuple(row["object_ids"])
        for row in inspection.instance_definitions
    }
    result: set[str] = set()
    for object_id, _ in assignments:
        definition_id = reference_definition.get(object_id)
        if definition_id is None:
            continue
        members = definitions.get(definition_id)
        if members is None:
            raise ThreeDmMaterializationError(
                f"instance {object_id} has no saved definition"
            )
        result.update(members)
    return tuple(sorted(result))


def _apply_materials(
    model: object,
    rhino3dm: object,
    plan: ThreeDmMaterializationPlan,
) -> None:
    if any(
        str(material.Name).startswith(NATIVE_MATERIAL_PREFIX)
        for material in model.Materials
    ):
        raise ThreeDmMaterializationError(
            "decoded source already owns the reserved material namespace"
        )
    material_indices: dict[str, int] = {}
    for spec in plan.materials:
        material = rhino3dm.Material()
        material.Name = spec.native_name
        material.DiffuseColor = (*spec.base_color_rgb, 255)
        material.ToPhysicallyBased()
        physically_based = material.PhysicallyBased
        physically_based.BaseColor = tuple(
            channel / 255.0 for channel in (*spec.base_color_rgb, 255)
        )
        physically_based.Metallic = spec.metallic
        physically_based.Roughness = spec.roughness
        if not material.SetUserString("archflow:material_id", spec.material_id):
            raise ThreeDmMaterializationError(
                f"could not tag native material {spec.material_id}"
            )
        index = int(model.Materials.Add(material))
        if index < 0:
            raise ThreeDmMaterializationError(
                f"could not append native material {spec.material_id}"
            )
        material_indices[spec.material_id] = index

    objects_by_id = {
        str(item.Attributes.Id).lower(): item for item in model.Objects
    }
    for object_id, material_id in plan.object_material_assignments:
        item = objects_by_id.get(object_id)
        if item is None:
            raise ThreeDmMaterializationError(
                f"assigned source object disappeared: {object_id}"
            )
        item.Attributes.MaterialIndex = material_indices[material_id]
        item.Attributes.MaterialSource = (
            rhino3dm.ObjectMaterialSource.MaterialFromObject
        )
    for object_id in plan.parent_material_object_ids:
        item = objects_by_id.get(object_id)
        if item is None:
            raise ThreeDmMaterializationError(
                f"definition member disappeared: {object_id}"
            )
        item.Attributes.MaterialIndex = -1
        item.Attributes.MaterialSource = (
            rhino3dm.ObjectMaterialSource.MaterialFromParent
        )


def _write_exclusive(model: object, plan: ThreeDmMaterializationPlan) -> None:
    with tempfile.TemporaryDirectory(
        prefix=".archflow-material-",
        dir=plan.workspace,
    ) as temporary_directory:
        temporary_path = Path(temporary_directory) / "candidate.3dm"
        if not model.Write(str(temporary_path), plan.source_three_dm_version):
            raise ThreeDmMaterializationError("native 3DM material write failed")
        try:
            os.link(temporary_path, plan.output_path)
        except FileExistsError as exc:
            raise ThreeDmMaterializationError(
                "speculative output appeared before exclusive creation"
            ) from exc


def _validate_source_inspection(
    plan: ThreeDmMaterializationPlan,
    inspection: ThreeDmInspection,
) -> None:
    if inspection.file_sha256 != plan.source_sha256:
        raise ThreeDmMaterializationError("source SHA-256 no longer matches plan")
    if inspection.three_dm_version != plan.source_three_dm_version:
        raise ThreeDmMaterializationError("source 3DM version no longer matches plan")
    if _geometry_digest(inspection) != plan.source_geometry_sha256:
        raise ThreeDmMaterializationError(
            "source geometry digest no longer matches plan"
        )
    if _identity_digest(inspection) != plan.source_identity_sha256:
        raise ThreeDmMaterializationError(
            "source object identity no longer matches plan"
        )
    if _bounds_digest(inspection) != plan.source_bounds_sha256:
        raise ThreeDmMaterializationError("source bounds no longer match plan")
    if len(inspection.materials) != plan.source_material_count or len(
        inspection.render_materials
    ) != plan.source_render_material_count:
        raise ThreeDmMaterializationError("source material tables no longer match plan")


def _geometry_digest(inspection: ThreeDmInspection) -> str:
    return canonical_digest(
        [
            {
                "object_id": row["object_id"],
                "geometry_sha256": row["geometry_sha256"],
            }
            for row in inspection.object_geometry_sha256
        ]
    )


def _identity_digest(inspection: ThreeDmInspection) -> str:
    return canonical_digest(
        {
            "units": inspection.units,
            "layers": inspection.layers,
            "object_count": inspection.object_count,
            "top_level_object_count": inspection.top_level_object_count,
            "instance_definition_member_count": (
                inspection.instance_definition_member_count
            ),
            "objects": [
                {
                    key: row[key]
                    for key in (
                        "object_id",
                        "name",
                        "type",
                        "layer_path",
                        "is_instance_definition_object",
                    )
                }
                for row in inspection.object_geometry_sha256
            ],
            "document_user_strings": inspection.document_user_strings,
            "object_user_strings": inspection.object_user_strings,
            "instance_definitions": inspection.instance_definitions,
            "instance_references": inspection.instance_references,
        }
    )


def _bounds_digest(inspection: ThreeDmInspection) -> str:
    return canonical_digest(
        {
            "aggregate_bbox": inspection.aggregate_bbox,
            "bbox_contributing_geometry_count": (
                inspection.bbox_contributing_geometry_count
            ),
            "named_object_bboxes": inspection.named_object_bboxes,
            "visible_bounds_witnesses": inspection.visible_bounds_witnesses,
        }
    )


def _plan_denominator_digest(plan: ThreeDmMaterializationPlan) -> str:
    return _denominator_digest(
        source_sha256=plan.source_sha256,
        source_three_dm_version=plan.source_three_dm_version,
        source_geometry_sha256=plan.source_geometry_sha256,
        source_identity_sha256=plan.source_identity_sha256,
        source_bounds_sha256=plan.source_bounds_sha256,
        source_object_ids=plan.source_object_ids,
        source_material_count=plan.source_material_count,
        source_render_material_count=plan.source_render_material_count,
        materials=plan.materials,
        object_material_assignments=plan.object_material_assignments,
        parent_material_object_ids=plan.parent_material_object_ids,
        output_relative_path=plan.output_relative_path,
    )


def _denominator_digest(
    *,
    source_sha256: str,
    source_three_dm_version: int,
    source_geometry_sha256: str,
    source_identity_sha256: str,
    source_bounds_sha256: str,
    source_object_ids: tuple[str, ...],
    source_material_count: int,
    source_render_material_count: int,
    materials: tuple[ThreeDmMaterialSpec, ...],
    object_material_assignments: tuple[tuple[str, str], ...],
    parent_material_object_ids: tuple[str, ...],
    output_relative_path: str,
) -> str:
    return canonical_digest(
        {
            "schema": "ThreeDmMaterializationDenominator@1",
            "source_sha256": source_sha256,
            "source_three_dm_version": source_three_dm_version,
            "source_geometry_sha256": source_geometry_sha256,
            "source_identity_sha256": source_identity_sha256,
            "source_bounds_sha256": source_bounds_sha256,
            "source_object_ids": list(source_object_ids),
            "source_material_count": source_material_count,
            "source_render_material_count": source_render_material_count,
            "materials": [item.to_dict() for item in materials],
            "object_material_assignments": [
                {"object_id": object_id, "material_id": material_id}
                for object_id, material_id in object_material_assignments
            ],
            "parent_material_object_ids": list(parent_material_object_ids),
            "output_relative_path": output_relative_path,
        }
    )


def _failed_receipt(
    plan: ThreeDmMaterializationPlan,
    code: str,
    detail: str,
    *,
    output_inspection: ThreeDmInspection | None = None,
) -> ThreeDmMaterializationReceipt:
    return ThreeDmMaterializationReceipt(
        status=ThreeDmMaterializationStatus.FAILED,
        plan_digest=plan.plan_digest,
        source_sha256=plan.source_sha256,
        output_relative_path=plan.output_relative_path,
        output_sha256=(
            None if output_inspection is None else output_inspection.file_sha256
        ),
        source_geometry_sha256=plan.source_geometry_sha256,
        output_geometry_sha256=(
            None
            if output_inspection is None
            else _geometry_digest(output_inspection)
        ),
        source_identity_sha256=plan.source_identity_sha256,
        output_identity_sha256=(
            None
            if output_inspection is None
            else _identity_digest(output_inspection)
        ),
        source_bounds_sha256=plan.source_bounds_sha256,
        output_bounds_sha256=(
            None
            if output_inspection is None
            else _bounds_digest(output_inspection)
        ),
        inspection=(
            None if output_inspection is None else output_inspection.to_dict()
        ),
        failures=(_failure(code, detail),),
    )


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": str(detail)[:1_000]}


def _load_rhino3dm() -> object:
    try:
        module = importlib.import_module("rhino3dm")
    except ImportError as exc:
        raise ThreeDmMaterializationError(
            "optional dependency 'rhino3dm' is unavailable"
        ) from exc
    required = (
        "File3dm",
        "Material",
        "ObjectMaterialSource",
    )
    if any(not hasattr(module, name) for name in required):
        raise ThreeDmMaterializationError(
            "installed rhino3dm lacks native material APIs"
        )
    return module


def _strict_source(value: Path) -> Path:
    if not isinstance(value, Path):
        raise TypeError("source_model_path must be pathlib.Path")
    if not value.exists() or not value.is_file() or value.is_symlink():
        raise ThreeDmMaterializationError(
            "source_model_path must be an existing regular non-symlink file"
        )
    return value.resolve(strict=True)


def _strict_workspace(value: Path) -> Path:
    if not isinstance(value, Path):
        raise TypeError("speculative_workspace must be pathlib.Path")
    if not value.exists() or not value.is_dir() or value.is_symlink():
        raise ThreeDmMaterializationError(
            "speculative_workspace must be an existing non-symlink directory"
        )
    return value.resolve(strict=True)


def _strict_child(workspace: Path, value: Path, *, require_exists: bool) -> Path:
    resolved = value.resolve(strict=require_exists)
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ThreeDmMaterializationError(
            "materialized output must remain inside speculative_workspace"
        ) from exc
    if resolved == workspace:
        raise ThreeDmMaterializationError("output cannot be the workspace root")
    if require_exists and (not resolved.is_file() or resolved.is_symlink()):
        raise ThreeDmMaterializationError(
            "materialized output must be a regular non-symlink file"
        )
    return resolved


def _validate_paths(
    plan: ThreeDmMaterializationPlan,
    *,
    require_source: bool,
) -> None:
    source = _strict_source(plan.source_path) if require_source else plan.source_path
    workspace = _strict_workspace(plan.workspace)
    output = _strict_child(workspace, plan.output_path, require_exists=False)
    if (
        source != plan.source_path
        or workspace != plan.workspace
        or output != plan.output_path
    ):
        raise ThreeDmMaterializationError("materialization paths are not normalized")
    if output == source:
        raise ThreeDmMaterializationError("source 3DM cannot be overwritten")


def _artifact_name(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ThreeDmMaterializationError("artifact_name must be non-empty text")
    if value.lower() != value or not value.endswith(".3dm"):
        raise ThreeDmMaterializationError(
            "artifact_name must be a lowercase .3dm filename"
        )
    if (
        PurePosixPath(value).name != value
        or PureWindowsPath(value).name != value
        or value in {".", ".."}
    ):
        raise ThreeDmMaterializationError(
            "artifact_name must not contain path separators"
        )
    return value


def _rgb_bytes(value: object, field: str) -> tuple[int, int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ThreeDmMaterializationError(f"{field} must be an RGB triplet")
    channels: list[int] = []
    for channel in value:
        if (
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or channel < 0
            or channel > 255
        ):
            raise ThreeDmMaterializationError(f"{field} must contain RGB bytes")
        channels.append(channel)
    return channels[0], channels[1], channels[2]


def _unit_interval(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ThreeDmMaterializationError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ThreeDmMaterializationError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise ThreeDmMaterializationError(
            f"{field} must be finite and between 0 and 1"
        )
    return 0.0 if result == 0.0 else result


def _float_close(actual: object, expected: float) -> bool:
    return (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and math.isfinite(float(actual))
        and abs(float(actual) - expected) <= _FLOAT_TOLERANCE
    )


def _float_sequence_close(actual: object, expected: list[float]) -> bool:
    return isinstance(actual, (tuple, list)) and len(actual) == len(expected) and all(
        _float_close(left, right) for left, right in zip(actual, expected)
    )


__all__ = [
    "NATIVE_MATERIAL_PREFIX",
    "ThreeDmMaterialSpec",
    "ThreeDmMaterializationError",
    "ThreeDmMaterializationPlan",
    "ThreeDmMaterializationReceipt",
    "ThreeDmMaterializationStatus",
    "execute_three_dm_materialization",
    "prepare_three_dm_materialization",
    "verify_three_dm_materialization_readback",
]
