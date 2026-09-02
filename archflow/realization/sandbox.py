"""Pure deterministic realization of a bounded neutral geometry program.

The scene is authoritative for this sandbox layer.  Voxel cells and render
views are derived evidence and cannot replace the retained analytic or mesh
geometry.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.adapters.voxel_observation import (
    ConnectedRegion,
    ObservationUnknown,
    SupportRelation,
    VoxelBounds,
    VoxelObservation,
)
from archflow.project import ProjectVersionRef
from archflow.compilers.geometry import (
    CompiledGeometryProgram,
    GeometryCompileStatus,
)
from archflow.state import ArtifactRef, StateRef
from archflow.state.geometry_program import (
    AssemblyKind,
    AssemblyRole,
    GeometryOperation,
    GeometryOperationKind,
)


_HEX = frozenset("0123456789abcdef")
_MAX_ARRAY_COUNT = 256
_SUPPORTED = frozenset(
    {
        GeometryOperationKind.CURVE,
        GeometryOperationKind.SOLID,
        GeometryOperationKind.TRANSFORM,
        GeometryOperationKind.EXTRUSION,
        GeometryOperationKind.REVOLVE,
        GeometryOperationKind.LOFT,
        GeometryOperationKind.SWEEP,
        GeometryOperationKind.ARRAY,
        GeometryOperationKind.RADIAL_ARRAY,
        GeometryOperationKind.BOOLEAN_UNION,
        GeometryOperationKind.BOOLEAN_DIFFERENCE,
        GeometryOperationKind.BOOLEAN_INTERSECTION,
        GeometryOperationKind.ASSET_INSTANCE,
    }
)


class SandboxRealizationError(ValueError):
    """A scene, policy, or derived view is structurally invalid."""


class SceneRepresentation(StrEnum):
    ANALYTIC = "analytic"
    CURVE = "curve"
    MESH = "mesh"


class RealizationStatus(StrEnum):
    REALIZED = "realized"
    REJECTED = "rejected"


class SandboxArchiveDisposition(StrEnum):
    REJECTED = "rejected"
    REPAIRED = "repaired"
    ACCEPTED = "accepted"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value.lower())
    ):
        raise SandboxRealizationError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise SandboxRealizationError(f"{field} must be finite")
    return float(value)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(
        character.isspace() for character in value
    ):
        raise SandboxRealizationError(f"{field} must be portable non-empty text")
    return value


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise SandboxRealizationError("scene base schema drifted")
    return ProjectVersionRef(
        project_id=value["project_id"],
        version=value["version"],
        state_sha256=value["state_sha256"],
    )


@dataclass(frozen=True, slots=True)
class AxisAlignedBounds:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    SCHEMA = "AxisAlignedBounds@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.minimum, tuple)
            or not isinstance(self.maximum, tuple)
            or len(self.minimum) != 3
            or len(self.maximum) != 3
        ):
            raise SandboxRealizationError("bounds require two 3-vectors")
        minimum = tuple(_finite(item, "bounds minimum") for item in self.minimum)
        maximum = tuple(_finite(item, "bounds maximum") for item in self.maximum)
        if any(left >= right for left, right in zip(minimum, maximum, strict=True)):
            raise SandboxRealizationError("bounds must have positive extent")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    @classmethod
    def from_origin_size(
        cls,
        origin: tuple[float, float, float],
        size: tuple[float, float, float],
    ) -> AxisAlignedBounds:
        return cls(
            origin,
            tuple(
                origin[index] + size[index] for index in range(3)
            ),
        )

    def union(self, other: AxisAlignedBounds) -> AxisAlignedBounds:
        return AxisAlignedBounds(
            tuple(min(self.minimum[i], other.minimum[i]) for i in range(3)),
            tuple(max(self.maximum[i], other.maximum[i]) for i in range(3)),
        )

    def intersection(
        self,
        other: AxisAlignedBounds,
    ) -> AxisAlignedBounds | None:
        minimum = tuple(
            max(self.minimum[i], other.minimum[i]) for i in range(3)
        )
        maximum = tuple(
            min(self.maximum[i], other.maximum[i]) for i in range(3)
        )
        if any(left >= right for left, right in zip(minimum, maximum, strict=True)):
            return None
        return AxisAlignedBounds(minimum, maximum)

    def contains_point(self, point: tuple[float, float, float]) -> bool:
        return all(
            self.minimum[index] <= point[index] < self.maximum[index]
            for index in range(3)
        )

    def corners(self) -> tuple[tuple[float, float, float], ...]:
        return tuple(
            (x, y, z)
            for x in (self.minimum[0], self.maximum[0])
            for y in (self.minimum[1], self.maximum[1])
            for z in (self.minimum[2], self.maximum[2])
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
        }

    @classmethod
    def from_dict(cls, value: object) -> AxisAlignedBounds:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "minimum",
            "maximum",
        }:
            raise SandboxRealizationError("bounds schema drifted")
        if value["schema"] != cls.SCHEMA:
            raise SandboxRealizationError("bounds schema changed")
        return cls(tuple(value["minimum"]), tuple(value["maximum"]))


@dataclass(frozen=True, slots=True)
class SandboxAssetPayload:
    """Decoded immutable asset supplied by the caller, never loaded by URI."""

    asset_id: str
    vertices: tuple[tuple[float, float, float], ...]
    faces: tuple[tuple[int, ...], ...]

    SCHEMA = "SandboxAssetPayload@1"

    # Voxel occupancy samples every mesh triangle per cell, so caller and
    # model supplied assets must stay explicitly bounded.
    MAX_VERTICES = 20_000
    MAX_FACES = 20_000

    def __post_init__(self) -> None:
        _identifier(self.asset_id, "asset_id")
        if not isinstance(self.vertices, tuple) or len(self.vertices) < 3:
            raise SandboxRealizationError("mesh asset needs at least 3 vertices")
        if len(self.vertices) > self.MAX_VERTICES:
            raise SandboxRealizationError(
                "mesh asset exceeds the explicit vertex bound"
            )
        if isinstance(self.faces, tuple) and len(self.faces) > self.MAX_FACES:
            raise SandboxRealizationError(
                "mesh asset exceeds the explicit face bound"
            )
        normalized_vertices = []
        for vertex in self.vertices:
            if not isinstance(vertex, tuple) or len(vertex) != 3:
                raise SandboxRealizationError("mesh vertex must be a 3-vector")
            normalized_vertices.append(
                tuple(_finite(item, "mesh vertex") for item in vertex)
            )
        if not isinstance(self.faces, tuple) or not self.faces:
            raise SandboxRealizationError("mesh asset needs faces")
        normalized_faces = []
        for face in self.faces:
            if not isinstance(face, tuple) or len(face) < 3:
                raise SandboxRealizationError(
                    "mesh face needs at least 3 indices"
                )
            if any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(normalized_vertices)
                for index in face
            ):
                raise SandboxRealizationError("mesh face index is invalid")
            normalized_faces.append(face)
        object.__setattr__(self, "vertices", tuple(normalized_vertices))
        object.__setattr__(self, "faces", tuple(normalized_faces))

    @property
    def payload_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def bounds(self) -> AxisAlignedBounds:
        return AxisAlignedBounds(
            tuple(min(vertex[i] for vertex in self.vertices) for i in range(3)),
            tuple(max(vertex[i] for vertex in self.vertices) for i in range(3)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "asset_id": self.asset_id,
            "vertices": [list(item) for item in self.vertices],
            "faces": [list(item) for item in self.faces],
        }


@dataclass(frozen=True, slots=True)
class SandboxRealizationPolicy:
    """Explicit caller policy; the runtime carries no hidden tolerance."""

    maximum_objects: int = 16_384

    SCHEMA = "SandboxRealizationPolicy@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_objects, int)
            or isinstance(self.maximum_objects, bool)
            or self.maximum_objects <= 0
        ):
            raise SandboxRealizationError("maximum_objects must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "maximum_objects": self.maximum_objects,
        }


@dataclass(frozen=True, slots=True)
class SceneObject:
    object_id: str
    producer_op_id: str
    source_object_digest: str
    representation: SceneRepresentation
    geometry_json: str
    bounds: AxisAlignedBounds
    semantic_binding_ids: tuple[str, ...]
    physical: bool

    SCHEMA = "HybridSceneObject@1"

    def __post_init__(self) -> None:
        _identifier(self.object_id, "scene object_id")
        _identifier(self.producer_op_id, "producer_op_id")
        object.__setattr__(
            self,
            "source_object_digest",
            _sha(self.source_object_digest, "source_object_digest"),
        )
        if not isinstance(self.representation, SceneRepresentation):
            raise TypeError("representation must be SceneRepresentation")
        if not isinstance(self.geometry_json, str):
            raise TypeError("geometry_json must be text")
        try:
            decoded = json.loads(self.geometry_json)
        except json.JSONDecodeError as exc:
            raise SandboxRealizationError(
                "geometry_json must contain JSON"
            ) from exc
        if _canonical_json(decoded) != self.geometry_json:
            raise SandboxRealizationError("geometry_json must be canonical")
        if not isinstance(self.bounds, AxisAlignedBounds):
            raise TypeError("bounds must be AxisAlignedBounds")
        if (
            not isinstance(self.semantic_binding_ids, tuple)
            or not self.semantic_binding_ids
            or self.semantic_binding_ids
            != tuple(sorted(set(self.semantic_binding_ids)))
        ):
            raise SandboxRealizationError(
                "semantic bindings require deterministic ids"
            )
        if not isinstance(self.physical, bool):
            raise TypeError("physical must be boolean")

    @property
    def geometry(self) -> dict[str, Any]:
        value = json.loads(self.geometry_json)
        if not isinstance(value, dict):
            raise AssertionError("validated geometry stopped being an object")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "object_id": self.object_id,
            "producer_op_id": self.producer_op_id,
            "source_object_digest": self.source_object_digest,
            "representation": self.representation.value,
            "geometry_json": self.geometry_json,
            "bounds": self.bounds.to_dict(),
            "semantic_binding_ids": list(self.semantic_binding_ids),
            "physical": self.physical,
        }

    @classmethod
    def from_dict(cls, value: object) -> SceneObject:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "object_id",
            "producer_op_id",
            "source_object_digest",
            "representation",
            "geometry_json",
            "bounds",
            "semantic_binding_ids",
            "physical",
        }:
            raise SandboxRealizationError("scene object schema drifted")
        if value["schema"] != cls.SCHEMA:
            raise SandboxRealizationError("scene object schema changed")
        return cls(
            object_id=value["object_id"],
            producer_op_id=value["producer_op_id"],
            source_object_digest=value["source_object_digest"],
            representation=SceneRepresentation(value["representation"]),
            geometry_json=value["geometry_json"],
            bounds=AxisAlignedBounds.from_dict(value["bounds"]),
            semantic_binding_ids=tuple(value["semantic_binding_ids"]),
            physical=value["physical"],
        )


@dataclass(frozen=True, slots=True)
class HybridScene:
    project_id: str
    run_id: str
    workspace_id: str
    base: ProjectVersionRef
    geometry_program_digest: str
    semantic_binding_digests: tuple[tuple[str, str], ...]
    objects: tuple[SceneObject, ...]
    opening_object_ids: tuple[str, ...]

    SCHEMA = "HybridSandboxScene@1"

    def __post_init__(self) -> None:
        _identifier(self.project_id, "project_id")
        _identifier(self.run_id, "run_id")
        _identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise SandboxRealizationError("scene and base disagree")
        object.__setattr__(
            self,
            "geometry_program_digest",
            _sha(self.geometry_program_digest, "geometry_program_digest"),
        )
        binding_ids: list[str] = []
        for binding_id, digest in self.semantic_binding_digests:
            _identifier(binding_id, "semantic binding_id")
            _sha(digest, "semantic binding digest")
            binding_ids.append(binding_id)
        if tuple(binding_ids) != tuple(sorted(set(binding_ids))):
            raise SandboxRealizationError(
                "semantic binding digests require deterministic ids"
            )
        if not isinstance(self.objects, tuple) or not self.objects:
            raise SandboxRealizationError("scene objects must be non-empty")
        if any(not isinstance(item, SceneObject) for item in self.objects):
            raise TypeError("objects contains an invalid item")
        object_ids = tuple(item.object_id for item in self.objects)
        if object_ids != tuple(sorted(set(object_ids))):
            raise SandboxRealizationError(
                "scene objects require deterministic identities"
            )
        if (
            not isinstance(self.opening_object_ids, tuple)
            or self.opening_object_ids
            != tuple(sorted(set(self.opening_object_ids)))
            or not set(self.opening_object_ids) <= set(object_ids)
        ):
            raise SandboxRealizationError(
                "opening ids must be deterministic scene objects"
            )

    @property
    def scene_digest(self) -> str:
        return _digest(self.to_dict())

    def object(self, object_id: str) -> SceneObject:
        for item in self.objects:
            if item.object_id == object_id:
                return item
        raise SandboxRealizationError(f"unknown scene object: {object_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "base": _base_to_dict(self.base),
            "geometry_program_digest": self.geometry_program_digest,
            "semantic_binding_digests": [
                {"binding_id": key, "digest": value}
                for key, value in self.semantic_binding_digests
            ],
            "objects": [item.to_dict() for item in self.objects],
            "opening_object_ids": list(self.opening_object_ids),
            "voxel_is_canonical": False,
            "external_platform_required": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> HybridScene:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "project_id",
            "run_id",
            "workspace_id",
            "base",
            "geometry_program_digest",
            "semantic_binding_digests",
            "objects",
            "opening_object_ids",
            "voxel_is_canonical",
            "external_platform_required",
        }:
            raise SandboxRealizationError("hybrid scene schema drifted")
        if (
            value["schema"] != cls.SCHEMA
            or value["voxel_is_canonical"] is not False
            or value["external_platform_required"] is not False
        ):
            raise SandboxRealizationError("hybrid scene authority drifted")
        bindings = value["semantic_binding_digests"]
        if not isinstance(bindings, list):
            raise TypeError("semantic_binding_digests must be a list")
        return cls(
            project_id=value["project_id"],
            run_id=value["run_id"],
            workspace_id=value["workspace_id"],
            base=_base_from_dict(value["base"]),
            geometry_program_digest=value["geometry_program_digest"],
            semantic_binding_digests=tuple(
                (item["binding_id"], item["digest"]) for item in bindings
            ),
            objects=tuple(
                SceneObject.from_dict(item) for item in value["objects"]
            ),
            opening_object_ids=tuple(value["opening_object_ids"]),
        )


@dataclass(frozen=True, slots=True)
class RealizationIssue:
    code: str
    operation_id: str
    detail: str

    SCHEMA = "SandboxRealizationIssue@1"

    def __post_init__(self) -> None:
        _identifier(self.code, "issue code")
        _identifier(self.operation_id, "issue operation_id")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise SandboxRealizationError("issue detail must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "code": self.code,
            "operation_id": self.operation_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SandboxRealizationReceipt:
    geometry_program_digest: str
    workspace_id: str
    policy_digest: str
    status: RealizationStatus
    scene_digest: str | None
    realized_operation_ids: tuple[str, ...]
    issues: tuple[RealizationIssue, ...]

    SCHEMA = "SandboxRealizationReceipt@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "geometry_program_digest",
            _sha(self.geometry_program_digest, "geometry_program_digest"),
        )
        _identifier(self.workspace_id, "workspace_id")
        object.__setattr__(
            self,
            "policy_digest",
            _sha(self.policy_digest, "policy_digest"),
        )
        if not isinstance(self.status, RealizationStatus):
            raise TypeError("status must be RealizationStatus")
        if self.scene_digest is not None:
            object.__setattr__(
                self,
                "scene_digest",
                _sha(self.scene_digest, "scene_digest"),
            )
        if (
            not isinstance(self.realized_operation_ids, tuple)
            or self.realized_operation_ids
            != tuple(dict.fromkeys(self.realized_operation_ids))
        ):
            raise SandboxRealizationError(
                "realized operations require deterministic order"
            )
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, RealizationIssue) for item in self.issues
        ):
            raise TypeError("issues contains an invalid item")
        if self.status is RealizationStatus.REALIZED:
            if self.scene_digest is None or self.issues:
                raise SandboxRealizationError(
                    "realized receipt cannot contain unresolved issues"
                )
        elif self.scene_digest is not None or not self.issues:
            raise SandboxRealizationError(
                "rejected receipt requires issues and no scene"
            )

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "geometry_program_digest": self.geometry_program_digest,
            "workspace_id": self.workspace_id,
            "policy_digest": self.policy_digest,
            "status": self.status.value,
            "scene_digest": self.scene_digest,
            "realized_operation_ids": list(self.realized_operation_ids),
            "issues": [item.to_dict() for item in self.issues],
            "execution_external": False,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SandboxRealizationReceipt:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "geometry_program_digest",
            "workspace_id",
            "policy_digest",
            "status",
            "scene_digest",
            "realized_operation_ids",
            "issues",
            "execution_external",
            "hard_gate_authority",
            "canonical_write_authority",
        }:
            raise SandboxRealizationError("realization receipt schema drifted")
        if (
            value["schema"] != cls.SCHEMA
            or value["execution_external"] is not False
            or value["hard_gate_authority"] is not False
            or value["canonical_write_authority"] is not False
        ):
            raise SandboxRealizationError(
                "realization receipt authority drifted"
            )
        return cls(
            geometry_program_digest=value["geometry_program_digest"],
            workspace_id=value["workspace_id"],
            policy_digest=value["policy_digest"],
            status=RealizationStatus(value["status"]),
            scene_digest=value["scene_digest"],
            realized_operation_ids=tuple(value["realized_operation_ids"]),
            issues=tuple(
                RealizationIssue(
                    code=item["code"],
                    operation_id=item["operation_id"],
                    detail=item["detail"],
                )
                for item in value["issues"]
            ),
        )


@dataclass(frozen=True, slots=True)
class SandboxRealizationResult:
    scene: HybridScene | None
    receipt: SandboxRealizationReceipt

    def __post_init__(self) -> None:
        if self.scene is None:
            if self.receipt.status is not RealizationStatus.REJECTED:
                raise SandboxRealizationError(
                    "missing scene requires rejected receipt"
                )
        elif (
            self.receipt.status is not RealizationStatus.REALIZED
            or self.receipt.scene_digest != self.scene.scene_digest
        ):
            raise SandboxRealizationError("scene and receipt disagree")


@dataclass(frozen=True, slots=True)
class SandboxArchiveRecord:
    """Read-only downstream disposition bound to an exact realization."""

    archive_id: str
    disposition: SandboxArchiveDisposition
    geometry_program_digest: str
    realization_receipt_digest: str
    scene_digest: str | None
    decision_receipt_digest: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "SandboxRealizationArchiveRecord@1"

    def __post_init__(self) -> None:
        _identifier(self.archive_id, "archive_id")
        if not isinstance(self.disposition, SandboxArchiveDisposition):
            raise TypeError("disposition must be SandboxArchiveDisposition")
        for field in (
            "geometry_program_digest",
            "realization_receipt_digest",
            "decision_receipt_digest",
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), field))
        if self.scene_digest is not None:
            object.__setattr__(
                self,
                "scene_digest",
                _sha(self.scene_digest, "scene_digest"),
            )
        if (
            self.disposition
            in {
                SandboxArchiveDisposition.REPAIRED,
                SandboxArchiveDisposition.ACCEPTED,
            }
            and self.scene_digest is None
        ):
            raise SandboxRealizationError(
                "repaired or accepted archive requires a scene"
            )
        if (
            not isinstance(self.evidence_refs, tuple)
            or not self.evidence_refs
            or self.evidence_refs
            != tuple(sorted(set(self.evidence_refs)))
        ):
            raise SandboxRealizationError(
                "archive evidence requires deterministic references"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "archive_id": self.archive_id,
            "disposition": self.disposition.value,
            "geometry_program_digest": self.geometry_program_digest,
            "realization_receipt_digest": (
                self.realization_receipt_digest
            ),
            "scene_digest": self.scene_digest,
            "decision_receipt_digest": self.decision_receipt_digest,
            "evidence_refs": list(self.evidence_refs),
            "decision_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> SandboxArchiveRecord:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "archive_id",
            "disposition",
            "geometry_program_digest",
            "realization_receipt_digest",
            "scene_digest",
            "decision_receipt_digest",
            "evidence_refs",
            "decision_authority",
            "canonical_write_authority",
        }:
            raise SandboxRealizationError("sandbox archive schema drifted")
        if (
            value["schema"] != cls.SCHEMA
            or value["decision_authority"] is not False
            or value["canonical_write_authority"] is not False
        ):
            raise SandboxRealizationError(
                "sandbox archive authority drifted"
            )
        return cls(
            archive_id=value["archive_id"],
            disposition=SandboxArchiveDisposition(value["disposition"]),
            geometry_program_digest=value["geometry_program_digest"],
            realization_receipt_digest=value[
                "realization_receipt_digest"
            ],
            scene_digest=value["scene_digest"],
            decision_receipt_digest=value["decision_receipt_digest"],
            evidence_refs=tuple(value["evidence_refs"]),
        )


def _parameters(operation: GeometryOperation) -> dict[str, object]:
    return {
        parameter.name: json.loads(parameter.value_json)
        for parameter in operation.parameters
    }


def _vector3(
    parameters: Mapping[str, object],
    name: str,
    operation_id: str,
) -> tuple[float, float, float]:
    value = parameters.get(name)
    if not isinstance(value, list) or len(value) != 3:
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} must be a 3-vector"
        )
    return tuple(_finite(item, name) for item in value)


def _frame_matrices(
    program: CompiledGeometryProgram,
) -> dict[str, tuple[float, ...]]:
    frames = {item.frame_id: item for item in program.proposal.frames}
    result: dict[str, tuple[float, ...]] = {}

    def resolve(frame_id: str) -> tuple[float, ...]:
        if frame_id in result:
            return result[frame_id]
        frame = frames[frame_id]
        local = frame.transform_from_parent.matrix
        if frame.parent_frame_id is None:
            result[frame_id] = local
        else:
            result[frame_id] = _matrix_multiply(
                resolve(frame.parent_frame_id),
                local,
            )
        return result[frame_id]

    for frame_id in sorted(frames):
        resolve(frame_id)
    return result


def _matrix_multiply(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> tuple[float, ...]:
    return tuple(
        sum(left[row * 4 + k] * right[k * 4 + column] for k in range(4))
        for row in range(4)
        for column in range(4)
    )


def _transform_point(
    matrix: tuple[float, ...],
    point: tuple[float, float, float],
) -> tuple[float, float, float]:
    value = (*point, 1.0)
    return tuple(
        sum(matrix[row * 4 + column] * value[column] for column in range(4))
        for row in range(3)
    )


def _transform_vector(
    matrix: tuple[float, ...],
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row * 4 + column] * vector[column] for column in range(3))
        for row in range(3)
    )


def _subtract(
    left: Sequence[float],
    right: Sequence[float],
) -> tuple[float, float, float]:
    return tuple(left[index] - right[index] for index in range(3))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(
    left: Sequence[float],
    right: Sequence[float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(vector: Sequence[float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _number(
    parameters: Mapping[str, object],
    name: str,
    operation_id: str,
) -> float:
    if name not in parameters:
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} is required"
        )
    return _finite(parameters[name], name)


def _integer(
    parameters: Mapping[str, object],
    name: str,
    operation_id: str,
) -> int:
    value = parameters.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} must be an integer"
        )
    return value


def _boolean(
    parameters: Mapping[str, object],
    name: str,
    operation_id: str,
) -> bool:
    value = parameters.get(name)
    if not isinstance(value, bool):
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} must be boolean"
        )
    return value


def _matrix4(
    parameters: Mapping[str, object],
    name: str,
    operation_id: str,
) -> tuple[float, ...]:
    raw = parameters.get(name)
    if not isinstance(raw, list) or len(raw) != 16:
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} must be a 4x4 matrix"
        )
    matrix = tuple(_finite(item, name) for item in raw)
    if matrix[12:] != (0.0, 0.0, 0.0, 1.0):
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} must be affine"
        )
    return matrix



def _lift_to_base_level(
    points: tuple[tuple[float, float, float], ...],
    parameters: Mapping[str, object],
    operation_id: str,
) -> tuple[tuple[float, float, float], ...]:
    """Apply an optional datum-bound ``base_level`` (program Y) to points."""

    if "base_level" not in parameters:
        if "base_offset" in parameters:
            raise SandboxRealizationError(
                f"{operation_id}: base_offset without a datum-bound base_level"
            )
        return points
    raw = parameters["base_level"]
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
    ):
        raise SandboxRealizationError(
            f"{operation_id}: base_level must be a finite number"
        )
    offset = parameters.get("base_offset", 0.0)
    if (
        isinstance(offset, bool)
        or not isinstance(offset, (int, float))
        or not math.isfinite(offset)
    ):
        raise SandboxRealizationError(
            f"{operation_id}: base_offset must be a finite number"
        )
    if not points:
        return points
    shift = float(raw) + float(offset) - min(point[1] for point in points)
    return tuple((p[0], p[1] + shift, p[2]) for p in points)


def _points3(
    parameters: Mapping[str, object],
    name: str,
    operation_id: str,
    *,
    minimum: int,
) -> tuple[tuple[float, float, float], ...]:
    raw = parameters.get(name)
    if not isinstance(raw, list) or len(raw) < minimum:
        raise SandboxRealizationError(
            f"{operation_id}: parameter {name} needs at least {minimum} points"
        )
    points = tuple(
        tuple(_finite(item, name) for item in point)
        for point in raw
        if isinstance(point, list) and len(point) == 3
    )
    if len(points) != len(raw):
        raise SandboxRealizationError(
            f"{operation_id}: every {name} point must be a 3-vector"
        )
    return points


def _uniform_frame_scale(
    matrix: tuple[float, ...],
    operation_id: str,
    tolerance: float,
) -> float:
    axes = tuple(
        _transform_vector(
            matrix,
            tuple(1.0 if index == axis else 0.0 for index in range(3)),
        )
        for axis in range(3)
    )
    lengths = tuple(_length(axis) for axis in axes)
    scale = sum(lengths) / 3.0
    if scale <= 0 or any(abs(value - scale) > tolerance for value in lengths):
        raise SandboxRealizationError(
            f"{operation_id}: analytic revolve requires uniform frame scale"
        )
    if any(
        abs(_dot(axes[first], axes[second])) > tolerance
        for first in range(3)
        for second in range(first + 1, 3)
    ):
        raise SandboxRealizationError(
            f"{operation_id}: analytic revolve rejects frame shear"
        )
    return scale


def _point_line_distance(
    point: Sequence[float],
    first: Sequence[float],
    last: Sequence[float],
) -> float:
    chord = _subtract(last, first)
    denominator = _dot(chord, chord)
    if denominator == 0:
        return _length(_subtract(point, first))
    offset = _subtract(point, first)
    parameter = max(0.0, min(1.0, _dot(offset, chord) / denominator))
    projected = tuple(first[i] + parameter * chord[i] for i in range(3))
    return _length(_subtract(point, projected))


def _split_bezier(
    points: tuple[tuple[float, float, float], ...],
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float, float], ...],
]:
    levels = [points]
    while len(levels[-1]) > 1:
        previous = levels[-1]
        levels.append(
            tuple(
                tuple((previous[i][axis] + previous[i + 1][axis]) / 2.0 for axis in range(3))
                for i in range(len(previous) - 1)
            )
        )
    return (
        tuple(level[0] for level in levels),
        tuple(level[-1] for level in reversed(levels)),
    )


def _sample_bezier(
    points: tuple[tuple[float, float, float], ...],
    tolerance: float,
    *,
    depth: int = 0,
) -> tuple[tuple[float, float, float], ...]:
    flatness = max(
        (_point_line_distance(point, points[0], points[-1]) for point in points[1:-1]),
        default=0.0,
    )
    if flatness <= tolerance or depth >= 16:
        return (points[0], points[-1])
    left, right = _split_bezier(points)
    return (
        *_sample_bezier(left, tolerance, depth=depth + 1)[:-1],
        *_sample_bezier(right, tolerance, depth=depth + 1),
    )


def _transform_bounds(
    matrix: tuple[float, ...],
    bounds: AxisAlignedBounds,
) -> AxisAlignedBounds:
    points = tuple(_transform_point(matrix, point) for point in bounds.corners())
    return AxisAlignedBounds(
        tuple(min(point[i] for point in points) for i in range(3)),
        tuple(max(point[i] for point in points) for i in range(3)),
    )


def _inverse_affine(
    matrix: tuple[float, ...],
    operation_id: str,
) -> tuple[float, ...]:
    a, b, c = matrix[0:3]
    d, e, f = matrix[4:7]
    g, h, i = matrix[8:11]
    determinant = (
        a * (e * i - f * h)
        - b * (d * i - f * g)
        + c * (d * h - e * g)
    )
    if abs(determinant) <= 1e-12:
        raise SandboxRealizationError(
            f"{operation_id}: affine transform must be invertible"
        )
    inverse_linear = (
        (e * i - f * h) / determinant,
        (c * h - b * i) / determinant,
        (b * f - c * e) / determinant,
        (f * g - d * i) / determinant,
        (a * i - c * g) / determinant,
        (c * d - a * f) / determinant,
        (d * h - e * g) / determinant,
        (b * g - a * h) / determinant,
        (a * e - b * d) / determinant,
    )
    translation = (matrix[3], matrix[7], matrix[11])
    inverse_translation = tuple(
        -sum(
            inverse_linear[row * 3 + column] * translation[column]
            for column in range(3)
        )
        for row in range(3)
    )
    return (
        inverse_linear[0],
        inverse_linear[1],
        inverse_linear[2],
        inverse_translation[0],
        inverse_linear[3],
        inverse_linear[4],
        inverse_linear[5],
        inverse_translation[1],
        inverse_linear[6],
        inverse_linear[7],
        inverse_linear[8],
        inverse_translation[2],
        0.0,
        0.0,
        0.0,
        1.0,
    )


def _translated_bounds(
    bounds: AxisAlignedBounds,
    offset: Sequence[float],
) -> AxisAlignedBounds:
    return AxisAlignedBounds(
        tuple(bounds.minimum[i] + offset[i] for i in range(3)),
        tuple(bounds.maximum[i] + offset[i] for i in range(3)),
    )


def _rotate_about_axis(
    point: tuple[float, float, float],
    center: Sequence[float],
    axis: Sequence[float],
    angle_degrees: float,
) -> tuple[float, float, float]:
    """Rodrigues rotation of one point about a unit axis through a center."""

    theta = math.radians(angle_degrees)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    local = _subtract(point, center)
    crossed = _cross(axis, local)
    dotted = _dot(axis, local)
    rotated = tuple(
        local[i] * cos_t
        + crossed[i] * sin_t
        + axis[i] * dotted * (1.0 - cos_t)
        for i in range(3)
    )
    return tuple(rotated[i] + center[i] for i in range(3))


def _rotated_bounds(
    bounds: AxisAlignedBounds,
    center: Sequence[float],
    axis: Sequence[float],
    angle_degrees: float,
) -> AxisAlignedBounds:
    """Exact axis-aligned bounds of a rotated axis-aligned box."""

    corners = [
        (
            bounds.minimum[0] if x == 0 else bounds.maximum[0],
            bounds.minimum[1] if y == 0 else bounds.maximum[1],
            bounds.minimum[2] if z == 0 else bounds.maximum[2],
        )
        for x in (0, 1)
        for y in (0, 1)
        for z in (0, 1)
    ]
    rotated = [
        _rotate_about_axis(corner, center, axis, angle_degrees)
        for corner in corners
    ]
    return AxisAlignedBounds(
        tuple(min(point[i] for point in rotated) for i in range(3)),
        tuple(max(point[i] for point in rotated) for i in range(3)),
    )


def _mesh_from_sections(
    sections: tuple[tuple[tuple[float, float, float], ...], ...],
    *,
    closed_profile: bool,
    cap_ends: bool,
    operation_id: str,
    source_kind: str,
    loss_code: str,
    tolerance: float,
) -> tuple[dict[str, object], AxisAlignedBounds]:
    profile_size = len(sections[0])
    minimum_size = 3 if closed_profile else 2
    if profile_size < minimum_size or any(
        len(section) != profile_size for section in sections
    ):
        raise SandboxRealizationError(
            f"{operation_id}: mesh sections have inconsistent profile sizes"
        )
    if len(sections) < 2:
        raise SandboxRealizationError(
            f"{operation_id}: mesh construction needs at least two sections"
        )
    if any(
        max(
            _length(_subtract(second[index], first[index]))
            for index in range(profile_size)
        )
        <= tolerance
        for first, second in zip(sections, sections[1:])
    ):
        raise SandboxRealizationError(
            f"{operation_id}: adjacent mesh sections must be distinct"
        )
    if cap_ends and not closed_profile:
        raise SandboxRealizationError(
            f"{operation_id}: end caps require a closed profile"
        )
    edge_count = profile_size if closed_profile else profile_size - 1
    for section in sections:
        if any(
            _length(_subtract(section[(index + 1) % profile_size], section[index]))
            <= tolerance
            for index in range(edge_count)
        ):
            raise SandboxRealizationError(
                f"{operation_id}: profile contains a degenerate edge"
            )
    vertices = tuple(point for section in sections for point in section)
    if len(vertices) > SandboxAssetPayload.MAX_VERTICES:
        raise SandboxRealizationError(
            f"{operation_id}: tessellation exceeds the explicit vertex bound"
        )
    faces: list[tuple[int, int, int]] = []
    for section_index in range(len(sections) - 1):
        first_base = section_index * profile_size
        second_base = first_base + profile_size
        for point_index in range(edge_count):
            next_index = (point_index + 1) % profile_size
            first = first_base + point_index
            first_next = first_base + next_index
            second = second_base + point_index
            second_next = second_base + next_index
            faces.extend(
                ((first, first_next, second_next), (first, second_next, second))
            )
    if cap_ends:
        last_base = (len(sections) - 1) * profile_size
        faces.extend(
            (0, index + 1, index)
            for index in range(1, profile_size - 1)
        )
        faces.extend(
            (last_base, last_base + index, last_base + index + 1)
            for index in range(1, profile_size - 1)
        )
    if len(faces) > SandboxAssetPayload.MAX_FACES:
        raise SandboxRealizationError(
            f"{operation_id}: tessellation exceeds the explicit face bound"
        )
    bounds = AxisAlignedBounds(
        tuple(min(point[i] for point in vertices) for i in range(3)),
        tuple(max(point[i] for point in vertices) for i in range(3)),
    )
    return (
        {
            "kind": "mesh",
            "source_kind": source_kind,
            "closed_profile": closed_profile,
            "cap_ends": cap_ends,
            "vertices": [list(item) for item in vertices],
            "faces": [list(item) for item in faces],
            "loss_codes": [loss_code],
        },
        bounds,
    )


def _resolved_asset_id(
    program: CompiledGeometryProgram,
    requested_id: str,
) -> str:
    for receipt in program.asset_substitutions:
        if receipt.requested_asset_id == requested_id:
            return receipt.replacement_asset_id
    return requested_id


def _operation_geometry(
    operation: GeometryOperation,
    objects: Mapping[str, SceneObject],
    frames: Mapping[str, tuple[float, ...]],
    assets: Mapping[str, SandboxAssetPayload],
    program: CompiledGeometryProgram,
) -> tuple[SceneRepresentation, dict[str, object], AxisAlignedBounds]:
    parameters = _parameters(operation)
    matrix = frames[operation.frame_id]
    if operation.kind is GeometryOperationKind.SOLID:
        origin = _vector3(parameters, "origin", operation.op_id)
        size = _vector3(parameters, "size", operation.op_id)
        if any(value <= 0 for value in size):
            raise SandboxRealizationError(
                f"{operation.op_id}: solid size must be positive"
            )
        bounds = _transform_bounds(
            matrix,
            AxisAlignedBounds.from_origin_size(origin, size),
        )
        return (
            SceneRepresentation.ANALYTIC,
            {"kind": "aabb", "bounds": bounds.to_dict()},
            bounds,
        )
    if operation.kind is GeometryOperationKind.CURVE:
        control_points = tuple(
            _transform_point(matrix, point)
            for point in _points3(
                parameters,
                "points",
                operation.op_id,
                minimum=2,
            )
        )
        basis = parameters.get("basis", "polyline")
        if basis not in {"polyline", "bezier"}:
            raise SandboxRealizationError(
                f"{operation.op_id}: curve basis must be polyline or bezier"
            )
        epsilon = program.proposal.tolerance.linear
        points = (
            control_points
            if basis == "polyline"
            else _sample_bezier(control_points, epsilon)
        )
        bounds = AxisAlignedBounds(
            tuple(min(point[i] for point in points) - epsilon for i in range(3)),
            tuple(max(point[i] for point in points) + epsilon for i in range(3)),
        )
        return (
            SceneRepresentation.CURVE,
            {
                "kind": "polyline",
                "basis": basis,
                "control_points": [list(item) for item in control_points],
                "points": [list(item) for item in points],
                "sampling_tolerance": epsilon,
            },
            bounds,
        )
    if operation.kind is GeometryOperationKind.REVOLVE:
        axis_start = _transform_point(
            matrix,
            _vector3(parameters, "axis_start", operation.op_id),
        )
        axis_end = _transform_point(
            matrix,
            _vector3(parameters, "axis_end", operation.op_id),
        )
        axis = _subtract(axis_end, axis_start)
        axis_length = _length(axis)
        if axis_length <= program.proposal.tolerance.linear:
            raise SandboxRealizationError(
                f"{operation.op_id}: revolve axis must have positive length"
            )
        scale = _uniform_frame_scale(
            matrix,
            operation.op_id,
            program.proposal.tolerance.linear,
        )
        start_radius = _number(
            parameters, "start_radius", operation.op_id
        ) * scale
        end_radius = _number(
            parameters, "end_radius", operation.op_id
        ) * scale
        if start_radius < 0 or end_radius < 0 or max(
            start_radius, end_radius
        ) <= 0:
            raise SandboxRealizationError(
                f"{operation.op_id}: revolve radii must be non-negative with one positive"
            )
        unit = tuple(value / axis_length for value in axis)
        radial_factors = tuple(
            math.sqrt(max(0.0, 1.0 - value * value)) for value in unit
        )
        minimum = tuple(
            min(
                axis_start[i] - radial_factors[i] * start_radius,
                axis_end[i] - radial_factors[i] * end_radius,
            )
            for i in range(3)
        )
        maximum = tuple(
            max(
                axis_start[i] + radial_factors[i] * start_radius,
                axis_end[i] + radial_factors[i] * end_radius,
            )
            for i in range(3)
        )
        bounds = AxisAlignedBounds(minimum, maximum)
        return (
            SceneRepresentation.ANALYTIC,
            {
                "kind": "revolve_linear",
                "axis_start": list(axis_start),
                "axis_end": list(axis_end),
                "start_radius": start_radius,
                "end_radius": end_radius,
                "tolerance": program.proposal.tolerance.linear,
            },
            bounds,
        )
    if operation.kind is GeometryOperationKind.EXTRUSION:
        profile = tuple(
            _transform_point(matrix, point)
            for point in _lift_to_base_level(
                _points3(
                    parameters,
                    "profile",
                    operation.op_id,
                    minimum=3,
                ),
                parameters,
                operation.op_id,
            )
        )
        vector = _transform_vector(
            matrix,
            _vector3(parameters, "vector", operation.op_id),
        )
        tolerance = program.proposal.tolerance.linear
        if _length(vector) <= tolerance:
            raise SandboxRealizationError(
                f"{operation.op_id}: extrusion vector must have positive length"
            )
        normal = None
        for index in range(1, len(profile) - 1):
            candidate = _cross(
                _subtract(profile[index], profile[0]),
                _subtract(profile[index + 1], profile[0]),
            )
            if _length(candidate) > tolerance:
                normal = candidate
                break
        if normal is None:
            raise SandboxRealizationError(
                f"{operation.op_id}: extrusion profile is degenerate"
            )
        normal_length = _length(normal)
        unit_normal = tuple(value / normal_length for value in normal)
        if any(
            abs(_dot(_subtract(point, profile[0]), unit_normal)) > tolerance
            for point in profile
        ):
            raise SandboxRealizationError(
                f"{operation.op_id}: extrusion profile must be planar"
            )
        if abs(_dot(vector, unit_normal)) <= tolerance:
            raise SandboxRealizationError(
                f"{operation.op_id}: extrusion vector must leave the profile plane"
            )
        vertices = (*profile, *(tuple(point[i] + vector[i] for i in range(3)) for point in profile))
        bounds = AxisAlignedBounds(
            tuple(min(point[i] for point in vertices) for i in range(3)),
            tuple(max(point[i] for point in vertices) for i in range(3)),
        )
        return (
            SceneRepresentation.ANALYTIC,
            {
                "kind": "prism",
                "profile": [list(item) for item in profile],
                "vector": list(vector),
                "normal": list(unit_normal),
                "tolerance": tolerance,
            },
            bounds,
        )
    if operation.kind is GeometryOperationKind.LOFT:
        points = _lift_to_base_level(
            _points3(
                parameters,
                "profiles",
                operation.op_id,
                minimum=4,
            ),
            parameters,
            operation.op_id,
        )
        profile_size = _integer(
            parameters,
            "profile_size",
            operation.op_id,
        )
        if profile_size < 2 or len(points) % profile_size != 0:
            raise SandboxRealizationError(
                f"{operation.op_id}: profiles must divide into equal sections"
            )
        sections = tuple(
            tuple(
                _transform_point(matrix, point)
                for point in points[start : start + profile_size]
            )
            for start in range(0, len(points), profile_size)
        )
        geometry, bounds = _mesh_from_sections(
            sections,
            closed_profile=_boolean(
                parameters,
                "closed_profile",
                operation.op_id,
            ),
            cap_ends=_boolean(
                parameters,
                "cap_ends",
                operation.op_id,
            ),
            operation_id=operation.op_id,
            source_kind="loft",
            loss_code="loft_tessellated_mesh",
            tolerance=program.proposal.tolerance.linear,
        )
        geometry["profile_size"] = profile_size
        geometry["section_count"] = len(sections)
        return SceneRepresentation.MESH, geometry, bounds
    if operation.kind is GeometryOperationKind.SWEEP:
        frame_mode = parameters.get("frame_mode")
        if frame_mode != "fixed":
            raise SandboxRealizationError(
                f"{operation.op_id}: sweep frame_mode must explicitly be fixed"
            )
        profile = tuple(
            _transform_point(matrix, point)
            for point in _points3(
                parameters,
                "profile",
                operation.op_id,
                minimum=2,
            )
        )
        path = tuple(
            _transform_point(matrix, point)
            for point in _points3(
                parameters,
                "path",
                operation.op_id,
                minimum=2,
            )
        )
        tolerance = program.proposal.tolerance.linear
        if any(
            _length(_subtract(second, first)) <= tolerance
            for first, second in zip(path, path[1:])
        ):
            raise SandboxRealizationError(
                f"{operation.op_id}: sweep path contains a degenerate segment"
            )
        sections = tuple(
            tuple(
                tuple(
                    point[axis] + path_point[axis] - path[0][axis]
                    for axis in range(3)
                )
                for point in profile
            )
            for path_point in path
        )
        geometry, bounds = _mesh_from_sections(
            sections,
            closed_profile=_boolean(
                parameters,
                "closed_profile",
                operation.op_id,
            ),
            cap_ends=_boolean(
                parameters,
                "cap_ends",
                operation.op_id,
            ),
            operation_id=operation.op_id,
            source_kind="sweep",
            loss_code="sweep_fixed_frame_tessellated_mesh",
            tolerance=tolerance,
        )
        geometry["frame_mode"] = frame_mode
        geometry["profile_size"] = len(profile)
        geometry["section_count"] = len(sections)
        return SceneRepresentation.MESH, geometry, bounds
    if operation.kind is GeometryOperationKind.TRANSFORM:
        if len(operation.input_object_ids) != 1:
            raise SandboxRealizationError(
                f"{operation.op_id}: transform requires exactly one input"
            )
        source_id = operation.input_object_ids[0]
        source = objects[source_id]
        local_matrix = _matrix4(
            parameters,
            "matrix",
            operation.op_id,
        )
        frame_inverse = _inverse_affine(matrix, operation.op_id)
        effective_matrix = _matrix_multiply(
            _matrix_multiply(matrix, local_matrix),
            frame_inverse,
        )
        inverse_matrix = _inverse_affine(
            effective_matrix,
            operation.op_id,
        )
        geometry = {
            "kind": "transform",
            "input": source_id,
            "matrix": list(effective_matrix),
            "inverse_matrix": list(inverse_matrix),
        }
        inherited_loss_codes = source.geometry.get("loss_codes")
        if inherited_loss_codes:
            geometry["loss_codes"] = inherited_loss_codes
        return (
            source.representation,
            geometry,
            _transform_bounds(effective_matrix, source.bounds),
        )
    if operation.kind is GeometryOperationKind.ARRAY:
        if len(operation.input_object_ids) != 1:
            raise SandboxRealizationError(
                f"{operation.op_id}: array requires exactly one input"
            )
        count = _integer(parameters, "count", operation.op_id)
        if count < 1 or count > _MAX_ARRAY_COUNT:
            raise SandboxRealizationError(
                f"{operation.op_id}: array count must be between 1 and {_MAX_ARRAY_COUNT}"
            )
        source_id = operation.input_object_ids[0]
        source = objects[source_id]
        step = _transform_vector(
            matrix,
            _vector3(parameters, "step", operation.op_id),
        )
        if count > 1 and _length(step) <= program.proposal.tolerance.linear:
            raise SandboxRealizationError(
                f"{operation.op_id}: replicated array needs a non-zero step"
            )
        offsets = tuple(
            tuple(index * step[axis] for axis in range(3))
            for index in range(count)
        )
        bounds = _translated_bounds(source.bounds, offsets[0])
        for offset in offsets[1:]:
            bounds = bounds.union(_translated_bounds(source.bounds, offset))
        geometry = {
            "kind": "array",
            "input": source_id,
            "count": count,
            "offsets": [list(item) for item in offsets],
        }
        inherited_loss_codes = source.geometry.get("loss_codes")
        if inherited_loss_codes:
            geometry["loss_codes"] = inherited_loss_codes
        return source.representation, geometry, bounds
    if operation.kind is GeometryOperationKind.RADIAL_ARRAY:
        if len(operation.input_object_ids) != 1:
            raise SandboxRealizationError(
                f"{operation.op_id}: radial array requires exactly one input"
            )
        count = _integer(parameters, "count", operation.op_id)
        if count < 1 or count > _MAX_ARRAY_COUNT:
            raise SandboxRealizationError(
                f"{operation.op_id}: radial array count must be between 1 "
                f"and {_MAX_ARRAY_COUNT}"
            )
        source_id = operation.input_object_ids[0]
        source = objects[source_id]
        center = _transform_point(
            matrix,
            _vector3(parameters, "center", operation.op_id),
        )
        axis = _transform_vector(
            matrix,
            _vector3(parameters, "axis", operation.op_id),
        )
        axis_length = _length(axis)
        if axis_length <= program.proposal.tolerance.linear:
            raise SandboxRealizationError(
                f"{operation.op_id}: radial array needs a non-zero axis"
            )
        axis = tuple(item / axis_length for item in axis)
        angle_step = _number(parameters, "angle_step_degrees", operation.op_id)
        if count > 1 and abs(angle_step) <= 1e-9:
            raise SandboxRealizationError(
                f"{operation.op_id}: replicated radial array needs a "
                "non-zero angle step"
            )
        start_angle = (
            _number(parameters, "start_angle_degrees", operation.op_id)
            if "start_angle_degrees" in parameters
            else 0.0
        )
        angles = tuple(
            start_angle + index * angle_step for index in range(count)
        )
        bounds = _rotated_bounds(source.bounds, center, axis, angles[0])
        for angle in angles[1:]:
            bounds = bounds.union(
                _rotated_bounds(source.bounds, center, axis, angle)
            )
        geometry = {
            "kind": "radial_array",
            "input": source_id,
            "count": count,
            "center": list(center),
            "axis": list(axis),
            "angles_degrees": list(angles),
        }
        inherited_loss_codes = source.geometry.get("loss_codes")
        if inherited_loss_codes:
            geometry["loss_codes"] = inherited_loss_codes
        return source.representation, geometry, bounds
    if operation.kind in {
        GeometryOperationKind.BOOLEAN_UNION,
        GeometryOperationKind.BOOLEAN_DIFFERENCE,
        GeometryOperationKind.BOOLEAN_INTERSECTION,
    }:
        inputs = tuple(objects[item] for item in operation.input_object_ids)
        if len(inputs) < 2:
            raise SandboxRealizationError(
                f"{operation.op_id}: boolean requires at least two inputs"
            )
        if operation.kind is GeometryOperationKind.BOOLEAN_INTERSECTION:
            bounds: AxisAlignedBounds | None = inputs[0].bounds
            for item in inputs[1:]:
                if bounds is None:
                    break
                bounds = bounds.intersection(item.bounds)
            if bounds is None:
                raise SandboxRealizationError(
                    f"{operation.op_id}: boolean intersection is empty"
                )
            token = "intersection"
        elif operation.kind is GeometryOperationKind.BOOLEAN_UNION:
            bounds = inputs[0].bounds
            for item in inputs[1:]:
                bounds = bounds.union(item.bounds)
            token = "union"
        else:
            raw_base_index = parameters.get("base_index")
            if (
                not isinstance(raw_base_index, int)
                or isinstance(raw_base_index, bool)
                or raw_base_index < 0
                or raw_base_index >= len(inputs)
            ):
                raise SandboxRealizationError(
                    f"{operation.op_id}: difference requires base_index"
                )
            base_id = operation.input_object_ids[raw_base_index]
            subtractors = tuple(
                object_id
                for index, object_id in enumerate(operation.input_object_ids)
                if index != raw_base_index
            )
            bounds = inputs[raw_base_index].bounds
            return (
                SceneRepresentation.ANALYTIC,
                {
                    "kind": "difference",
                    "base": base_id,
                    "subtractors": list(subtractors),
                },
                bounds,
            )
        return (
            SceneRepresentation.ANALYTIC,
            {
                "kind": token,
                "inputs": list(operation.input_object_ids),
            },
            bounds,
        )
    if operation.kind is GeometryOperationKind.ASSET_INSTANCE:
        if operation.asset_id is None or operation.asset_scale is None:
            raise SandboxRealizationError(
                f"{operation.op_id}: asset placement is incomplete"
            )
        resolved_id = _resolved_asset_id(program, operation.asset_id)
        payload = assets.get(resolved_id)
        if payload is None:
            raise SandboxRealizationError(
                f"{operation.op_id}: decoded asset payload is unavailable"
            )
        asset_ref = next(
            (
                item
                for item in program.proposal.assets
                if item.asset_id == resolved_id
            ),
            None,
        )
        if asset_ref is None or asset_ref.sha256 != payload.payload_digest:
            raise SandboxRealizationError(
                f"{operation.op_id}: asset payload digest does not match reference"
            )
        scaled_vertices = tuple(
            tuple(
                vertex[index] * operation.asset_scale[index]
                for index in range(3)
            )
            for vertex in payload.vertices
        )
        vertices = tuple(
            _transform_point(matrix, vertex) for vertex in scaled_vertices
        )
        bounds = AxisAlignedBounds(
            tuple(min(vertex[i] for vertex in vertices) for i in range(3)),
            tuple(max(vertex[i] for vertex in vertices) for i in range(3)),
        )
        return (
            SceneRepresentation.MESH,
            {
                "kind": "mesh",
                "requested_asset_id": operation.asset_id,
                "resolved_asset_id": resolved_id,
                "asset_payload_digest": payload.payload_digest,
                "socket_id": operation.asset_socket_id,
                "vertices": [list(item) for item in vertices],
                "faces": [list(item) for item in payload.faces],
            },
            bounds,
        )
    raise SandboxRealizationError(
        f"{operation.op_id}: unsupported operation {operation.kind.value}"
    )


def realize_geometry(
    program: CompiledGeometryProgram,
    *,
    workspace_id: str,
    policy: SandboxRealizationPolicy = SandboxRealizationPolicy(),
    asset_payloads: tuple[SandboxAssetPayload, ...] = (),
) -> SandboxRealizationResult:
    """Realize a compiled program without filesystem or external-tool access."""

    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be CompiledGeometryProgram")
    _identifier(workspace_id, "workspace_id")
    if not isinstance(policy, SandboxRealizationPolicy):
        raise TypeError("policy must be SandboxRealizationPolicy")
    if not isinstance(asset_payloads, tuple) or any(
        not isinstance(item, SandboxAssetPayload) for item in asset_payloads
    ):
        raise TypeError("asset_payloads contains an invalid item")
    asset_ids = tuple(item.asset_id for item in asset_payloads)
    if asset_ids != tuple(sorted(set(asset_ids))):
        raise SandboxRealizationError(
            "asset payloads require deterministic identities"
        )
    policy_digest = _digest(policy.to_dict())
    issues: list[RealizationIssue] = []
    if len(program.objects) > policy.maximum_objects:
        issues.append(
            RealizationIssue(
                "sandbox.object_budget",
                program.operation_order[0],
                "compiled object count exceeds explicit policy",
            )
        )
    unsupported = tuple(
        operation
        for operation in program.proposal.operations
        if operation.kind not in _SUPPORTED
    )
    issues.extend(
        RealizationIssue(
            "sandbox.operation_unsupported",
            operation.op_id,
            f"operation kind {operation.kind.value} has no sandbox evaluator",
        )
        for operation in unsupported
    )
    if issues:
        receipt = SandboxRealizationReceipt(
            geometry_program_digest=program.program_digest,
            workspace_id=workspace_id,
            policy_digest=policy_digest,
            status=RealizationStatus.REJECTED,
            scene_digest=None,
            realized_operation_ids=(),
            issues=tuple(
                sorted(issues, key=lambda item: (item.code, item.operation_id))
            ),
        )
        return SandboxRealizationResult(None, receipt)

    operations = {
        item.op_id: item for item in program.proposal.operations
    }
    compiled_objects = {
        item.object_id: item for item in program.objects
    }
    frames = _frame_matrices(program)
    assets = {item.asset_id: item for item in asset_payloads}
    scene_objects: dict[str, SceneObject] = {}
    consumed = {
        object_id
        for operation in program.proposal.operations
        for object_id in operation.input_object_ids
    }
    reference_ids = {
        object_id
        for assembly in program.proposal.assemblies
        for role in (AssemblyRole.HOST_CUT, AssemblyRole.CLEARANCE)
        for object_id in assembly.objects_for(role)
    }
    reference_ids.update(
        object_id
        for assembly in program.proposal.assemblies
        if assembly.kind is AssemblyKind.DOOR
        for object_id in assembly.objects_for(AssemblyRole.LEAF)
    )
    opening_ids = {
        object_id
        for assembly in program.proposal.assemblies
        for object_id in assembly.objects_for(AssemblyRole.HOST_CUT)
    }
    realized_ops: list[str] = []
    for op_id in program.operation_order:
        operation = operations[op_id]
        try:
            representation, geometry, bounds = _operation_geometry(
                operation,
                scene_objects,
                frames,
                assets,
                program,
            )
        except SandboxRealizationError as exc:
            issues.append(
                RealizationIssue(
                    "sandbox.operation_failed",
                    op_id,
                    str(exc),
                )
            )
            break
        for object_id in operation.output_object_ids:
            source = compiled_objects[object_id]
            scene_objects[object_id] = SceneObject(
                object_id=object_id,
                producer_op_id=op_id,
                source_object_digest=source.object_digest,
                representation=representation,
                geometry_json=_canonical_json(geometry),
                bounds=bounds,
                semantic_binding_ids=operation.semantic_binding_ids,
                physical=(
                    object_id not in consumed
                    and object_id not in reference_ids
                    and representation is not SceneRepresentation.CURVE
                ),
            )
        realized_ops.append(op_id)
    if issues:
        receipt = SandboxRealizationReceipt(
            geometry_program_digest=program.program_digest,
            workspace_id=workspace_id,
            policy_digest=policy_digest,
            status=RealizationStatus.REJECTED,
            scene_digest=None,
            realized_operation_ids=tuple(realized_ops),
            issues=tuple(issues),
        )
        return SandboxRealizationResult(None, receipt)
    scene = HybridScene(
        project_id=program.proposal.project_id,
        run_id=program.proposal.run_id,
        workspace_id=workspace_id,
        base=program.proposal.base,
        geometry_program_digest=program.program_digest,
        semantic_binding_digests=program.semantic_binding_digests,
        objects=tuple(sorted(scene_objects.values(), key=lambda item: item.object_id)),
        opening_object_ids=tuple(sorted(opening_ids)),
    )
    receipt = SandboxRealizationReceipt(
        geometry_program_digest=program.program_digest,
        workspace_id=workspace_id,
        policy_digest=policy_digest,
        status=RealizationStatus.REALIZED,
        scene_digest=scene.scene_digest,
        realized_operation_ids=program.operation_order,
        issues=(),
    )
    return SandboxRealizationResult(scene, receipt)


@dataclass(frozen=True, slots=True)
class VoxelizationPolicy:
    default_resolution: float
    local_resolutions: tuple[tuple[str, float], ...] = ()
    maximum_cells: int = 262_144

    SCHEMA = "SandboxVoxelizationPolicy@1"

    def __post_init__(self) -> None:
        default = _finite(self.default_resolution, "default_resolution")
        if default <= 0:
            raise SandboxRealizationError(
                "default_resolution must be positive"
            )
        object.__setattr__(self, "default_resolution", default)
        ids: list[str] = []
        normalized: list[tuple[str, float]] = []
        for object_id, resolution in self.local_resolutions:
            _identifier(object_id, "local resolution object_id")
            value = _finite(resolution, "local resolution")
            if value <= 0:
                raise SandboxRealizationError(
                    "local resolution must be positive"
                )
            ids.append(object_id)
            normalized.append((object_id, value))
        if tuple(ids) != tuple(sorted(set(ids))):
            raise SandboxRealizationError(
                "local resolutions require deterministic object ids"
            )
        object.__setattr__(self, "local_resolutions", tuple(normalized))
        if (
            not isinstance(self.maximum_cells, int)
            or isinstance(self.maximum_cells, bool)
            or self.maximum_cells <= 0
        ):
            raise SandboxRealizationError("maximum_cells must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "default_resolution": self.default_resolution,
            "local_resolutions": [
                {"object_id": key, "resolution": value}
                for key, value in self.local_resolutions
            ],
            "maximum_cells": self.maximum_cells,
        }


@dataclass(frozen=True, slots=True)
class LocalDetailSample:
    object_id: str
    resolution: float
    occupied_sample_count: int
    sample_digest: str

    SCHEMA = "LocalDetailVoxelSample@1"

    def __post_init__(self) -> None:
        _identifier(self.object_id, "detail object_id")
        value = _finite(self.resolution, "detail resolution")
        if value <= 0:
            raise SandboxRealizationError("detail resolution must be positive")
        object.__setattr__(self, "resolution", value)
        if (
            not isinstance(self.occupied_sample_count, int)
            or isinstance(self.occupied_sample_count, bool)
            or self.occupied_sample_count < 0
        ):
            raise SandboxRealizationError(
                "occupied_sample_count must be non-negative"
            )
        object.__setattr__(
            self,
            "sample_digest",
            _sha(self.sample_digest, "sample_digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "object_id": self.object_id,
            "resolution": self.resolution,
            "occupied_sample_count": self.occupied_sample_count,
            "sample_digest": self.sample_digest,
        }


@dataclass(frozen=True, slots=True)
class DerivedVoxelView:
    scene_digest: str
    realization_receipt_digest: str
    policy_digest: str
    resolution: float
    bounds: VoxelBounds
    occupied_cells: tuple[tuple[int, int, int], ...]
    walkable_cells: tuple[tuple[int, int, int], ...]
    opening_cells: tuple[tuple[int, int, int], ...]
    connected_regions: tuple[ConnectedRegion, ...]
    support_relations: tuple[SupportRelation, ...]
    local_detail_samples: tuple[LocalDetailSample, ...]
    loss_codes: tuple[str, ...]
    project_id: str
    version: int
    workspace_id: str

    SCHEMA = "DerivedSandboxVoxelView@1"

    def __post_init__(self) -> None:
        for field in (
            "scene_digest",
            "realization_receipt_digest",
            "policy_digest",
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), field))
        value = _finite(self.resolution, "resolution")
        if value <= 0:
            raise SandboxRealizationError("resolution must be positive")
        object.__setattr__(self, "resolution", value)
        if not isinstance(self.bounds, VoxelBounds):
            raise TypeError("bounds must be VoxelBounds")
        for field in (
            "occupied_cells",
            "walkable_cells",
            "opening_cells",
        ):
            values = getattr(self, field)
            if not isinstance(values, tuple) or values != tuple(sorted(set(values))):
                raise SandboxRealizationError(
                    f"{field} requires deterministic cells"
                )
        if not isinstance(self.connected_regions, tuple):
            raise TypeError("connected_regions must be a tuple")
        if not isinstance(self.support_relations, tuple):
            raise TypeError("support_relations must be a tuple")
        if not isinstance(self.local_detail_samples, tuple):
            raise TypeError("local_detail_samples must be a tuple")
        if self.loss_codes != tuple(sorted(set(self.loss_codes))):
            raise SandboxRealizationError(
                "loss_codes require deterministic values"
            )
        _identifier(self.project_id, "project_id")
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 0
        ):
            raise SandboxRealizationError("version must be non-negative")
        _identifier(self.workspace_id, "workspace_id")

    @property
    def view_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def artifact(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=f"sandbox-voxel-{self.view_digest[:20]}",
            uri=f"sandbox://voxel/{self.view_digest}",
            media_type="application/vnd.archflow.sandbox-voxel+json",
            sha256=self.view_digest,
        )

    def to_observation(self) -> VoxelObservation:
        artifact = self.artifact
        observation_payload = {
            "view_digest": self.view_digest,
            "artifact_id": artifact.artifact_id,
            "workspace_id": self.workspace_id,
        }
        return VoxelObservation(
            observation_id=(
                f"sandbox-observation-{_digest(observation_payload)[:20]}"
            ),
            source_artifact_id=artifact.artifact_id,
            source_artifact_sha256=artifact.sha256,
            source_scan_sha256=self.view_digest,
            workspace_id=self.workspace_id,
            base_state=StateRef(self.project_id, self.version),
            envelope=self.bounds,
            occupied_cells=self.occupied_cells,
            walkable_cells=self.walkable_cells,
            openings=self.opening_cells,
            connected_regions=self.connected_regions,
            support_relations=self.support_relations,
            unsupported_cells=_unsupported_occupied_cells(
                self.occupied_cells,
                self.support_relations,
                self.bounds.minimum[1],
            ),
            unknown_count=0,
            unknowns=(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "scene_digest": self.scene_digest,
            "realization_receipt_digest": self.realization_receipt_digest,
            "policy_digest": self.policy_digest,
            "resolution": self.resolution,
            "bounds": {
                "minimum": list(self.bounds.minimum),
                "maximum": list(self.bounds.maximum),
            },
            "occupied_cells": [list(item) for item in self.occupied_cells],
            "walkable_cells": [list(item) for item in self.walkable_cells],
            "opening_cells": [list(item) for item in self.opening_cells],
            "connected_regions": [
                {
                    "region_id": item.region_id,
                    "bounds": {
                        "minimum": list(item.bounds.minimum),
                        "maximum": list(item.bounds.maximum),
                    },
                    "cells": [list(cell) for cell in item.cells],
                    "touches_opening": item.touches_opening,
                }
                for item in self.connected_regions
            ],
            "support_relations": [
                {
                    "first": list(item.first),
                    "second": list(item.second),
                    "kind": item.kind,
                }
                for item in self.support_relations
            ],
            "local_detail_samples": [
                item.to_dict() for item in self.local_detail_samples
            ],
            "loss_codes": list(self.loss_codes),
            "project_id": self.project_id,
            "version": self.version,
            "workspace_id": self.workspace_id,
            "canonical_geometry_retained": True,
            "hard_gate_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> DerivedVoxelView:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "scene_digest",
            "realization_receipt_digest",
            "policy_digest",
            "resolution",
            "bounds",
            "occupied_cells",
            "walkable_cells",
            "opening_cells",
            "connected_regions",
            "support_relations",
            "local_detail_samples",
            "loss_codes",
            "project_id",
            "version",
            "workspace_id",
            "canonical_geometry_retained",
            "hard_gate_authority",
        }:
            raise SandboxRealizationError("derived voxel view schema drifted")
        if (
            value["schema"] != cls.SCHEMA
            or value["canonical_geometry_retained"] is not True
            or value["hard_gate_authority"] is not False
        ):
            raise SandboxRealizationError(
                "derived voxel view authority drifted"
            )
        raw_bounds = value["bounds"]
        if not isinstance(raw_bounds, Mapping):
            raise TypeError("voxel bounds must be an object")
        regions = []
        for item in value["connected_regions"]:
            region_bounds = item["bounds"]
            regions.append(
                ConnectedRegion(
                    region_id=item["region_id"],
                    bounds=VoxelBounds(
                        tuple(region_bounds["minimum"]),
                        tuple(region_bounds["maximum"]),
                    ),
                    cells=tuple(tuple(cell) for cell in item["cells"]),
                    touches_opening=item["touches_opening"],
                )
            )
        return cls(
            scene_digest=value["scene_digest"],
            realization_receipt_digest=value[
                "realization_receipt_digest"
            ],
            policy_digest=value["policy_digest"],
            resolution=value["resolution"],
            bounds=VoxelBounds(
                tuple(raw_bounds["minimum"]),
                tuple(raw_bounds["maximum"]),
            ),
            occupied_cells=tuple(
                tuple(item) for item in value["occupied_cells"]
            ),
            walkable_cells=tuple(
                tuple(item) for item in value["walkable_cells"]
            ),
            opening_cells=tuple(
                tuple(item) for item in value["opening_cells"]
            ),
            connected_regions=tuple(regions),
            support_relations=tuple(
                SupportRelation(
                    tuple(item["first"]),
                    tuple(item["second"]),
                    item["kind"],
                )
                for item in value["support_relations"]
            ),
            local_detail_samples=tuple(
                LocalDetailSample(
                    object_id=item["object_id"],
                    resolution=item["resolution"],
                    occupied_sample_count=item[
                        "occupied_sample_count"
                    ],
                    sample_digest=item["sample_digest"],
                )
                for item in value["local_detail_samples"]
            ),
            loss_codes=tuple(value["loss_codes"]),
            project_id=value["project_id"],
            version=value["version"],
            workspace_id=value["workspace_id"],
        )


# One fixed generic ray direction keeps the mesh parity test deterministic
# while avoiding axis-aligned edge and face coincidences.
_MESH_RAY_DIRECTION = (1.0, 0.7548776662466927, 0.5698402909980532)
_MESH_RAY_EPSILON = 1e-12


def _ray_hits_triangle(
    origin: tuple[float, float, float],
    a: tuple[float, ...],
    b: tuple[float, ...],
    c: tuple[float, ...],
) -> bool:
    direction = _MESH_RAY_DIRECTION
    edge1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    edge2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    pvec = (
        direction[1] * edge2[2] - direction[2] * edge2[1],
        direction[2] * edge2[0] - direction[0] * edge2[2],
        direction[0] * edge2[1] - direction[1] * edge2[0],
    )
    det = edge1[0] * pvec[0] + edge1[1] * pvec[1] + edge1[2] * pvec[2]
    if abs(det) < _MESH_RAY_EPSILON:
        return False
    inverse = 1.0 / det
    tvec = (origin[0] - a[0], origin[1] - a[1], origin[2] - a[2])
    u = (
        tvec[0] * pvec[0] + tvec[1] * pvec[1] + tvec[2] * pvec[2]
    ) * inverse
    if u < 0.0 or u > 1.0:
        return False
    qvec = (
        tvec[1] * edge1[2] - tvec[2] * edge1[1],
        tvec[2] * edge1[0] - tvec[0] * edge1[2],
        tvec[0] * edge1[1] - tvec[1] * edge1[0],
    )
    v = (
        direction[0] * qvec[0]
        + direction[1] * qvec[1]
        + direction[2] * qvec[2]
    ) * inverse
    if v < 0.0 or u + v > 1.0:
        return False
    t = (
        edge2[0] * qvec[0] + edge2[1] * qvec[1] + edge2[2] * qvec[2]
    ) * inverse
    return t > _MESH_RAY_EPSILON


def _point_in_mesh(
    point: tuple[float, float, float],
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
) -> bool:
    """Bounded parity sampling of the actual mesh surface.

    This is the sampling the mesh_validation_uses_bounded_sampling loss code
    declares: exact at sample points for watertight meshes, approximate for
    sub-resolution features and open surfaces.
    """

    crossings = 0
    for face in faces:
        for index in range(1, len(face) - 1):
            if _ray_hits_triangle(
                point,
                vertices[face[0]],
                vertices[face[index]],
                vertices[face[index + 1]],
            ):
                crossings += 1
    return crossings % 2 == 1


def _point_on_segment_2d(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
    tolerance: float,
) -> bool:
    cross = (
        (point[0] - first[0]) * (second[1] - first[1])
        - (point[1] - first[1]) * (second[0] - first[0])
    )
    if abs(cross) > tolerance:
        return False
    return (
        min(first[0], second[0]) - tolerance
        <= point[0]
        <= max(first[0], second[0]) + tolerance
        and min(first[1], second[1]) - tolerance
        <= point[1]
        <= max(first[1], second[1]) + tolerance
    )


def _point_in_planar_polygon(
    point: tuple[float, float, float],
    profile: Sequence[Sequence[float]],
    normal: Sequence[float],
    tolerance: float,
) -> bool:
    drop = max(range(3), key=lambda index: abs(normal[index]))
    axes = tuple(index for index in range(3) if index != drop)
    target = (point[axes[0]], point[axes[1]])
    polygon = tuple((item[axes[0]], item[axes[1]]) for item in profile)
    inside = False
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        if _point_on_segment_2d(target, first, second, tolerance):
            return True
        if (first[1] > target[1]) != (second[1] > target[1]):
            crossing = (
                (second[0] - first[0])
                * (target[1] - first[1])
                / (second[1] - first[1])
                + first[0]
            )
            if target[0] < crossing:
                inside = not inside
    return inside


def _contains(
    object_id: str,
    point: tuple[float, float, float],
    objects: Mapping[str, SceneObject],
) -> bool:
    item = objects[object_id]
    geometry = item.geometry
    kind = geometry["kind"]
    if kind == "aabb":
        return item.bounds.contains_point(point)
    if kind == "mesh":
        if not item.bounds.contains_point(point):
            return False
        return _point_in_mesh(
            point,
            geometry["vertices"],
            geometry["faces"],
        )
    if kind == "revolve_linear":
        axis_start = tuple(geometry["axis_start"])
        axis = _subtract(geometry["axis_end"], axis_start)
        axis_length_squared = _dot(axis, axis)
        parameter = _dot(_subtract(point, axis_start), axis) / axis_length_squared
        tolerance = geometry["tolerance"]
        if parameter < -tolerance or parameter > 1.0 + tolerance:
            return False
        parameter = max(0.0, min(1.0, parameter))
        center = tuple(axis_start[i] + parameter * axis[i] for i in range(3))
        radius = (
            geometry["start_radius"]
            + parameter
            * (geometry["end_radius"] - geometry["start_radius"])
        )
        return _length(_subtract(point, center)) <= radius + tolerance
    if kind == "prism":
        profile = tuple(tuple(item) for item in geometry["profile"])
        vector = tuple(geometry["vector"])
        normal = tuple(geometry["normal"])
        denominator = _dot(vector, normal)
        parameter = _dot(_subtract(point, profile[0]), normal) / denominator
        tolerance = geometry["tolerance"]
        if parameter < -tolerance or parameter > 1.0 + tolerance:
            return False
        base_point = tuple(point[i] - parameter * vector[i] for i in range(3))
        return _point_in_planar_polygon(
            base_point,
            profile,
            normal,
            tolerance,
        )
    if kind == "transform":
        local_point = _transform_point(
            tuple(geometry["inverse_matrix"]),
            point,
        )
        return _contains(geometry["input"], local_point, objects)
    if kind == "array":
        return any(
            _contains(
                geometry["input"],
                tuple(point[index] - offset[index] for index in range(3)),
                objects,
            )
            for offset in geometry["offsets"]
        )
    if kind == "radial_array":
        center = tuple(geometry["center"])
        axis = tuple(geometry["axis"])
        return any(
            _contains(
                geometry["input"],
                _rotate_about_axis(point, center, axis, -angle),
                objects,
            )
            for angle in geometry["angles_degrees"]
        )
    if kind == "difference":
        return _contains(geometry["base"], point, objects) and not any(
            _contains(value, point, objects)
            for value in geometry["subtractors"]
        )
    inputs = tuple(geometry["inputs"])
    if kind == "union":
        return any(_contains(value, point, objects) for value in inputs)
    if kind == "intersection":
        return all(_contains(value, point, objects) for value in inputs)
    return False


def _cell_ranges(
    bounds: AxisAlignedBounds,
    resolution: float,
) -> tuple[range, range, range]:
    minimum = tuple(math.floor(value / resolution) for value in bounds.minimum)
    maximum = tuple(math.ceil(value / resolution) - 1 for value in bounds.maximum)
    return tuple(
        range(minimum[index], maximum[index] + 1) for index in range(3)
    )


def _sample_point(
    cell: tuple[int, int, int],
    resolution: float,
) -> tuple[float, float, float]:
    return tuple((value + 0.5) * resolution for value in cell)


def _intersects_cell(
    object_id: str,
    cell: tuple[int, int, int],
    resolution: float,
    objects: Mapping[str, SceneObject],
) -> bool:
    """Conservatively sample positive-volume geometry/cell overlap."""

    item = objects[object_id]
    cell_minimum = tuple(value * resolution for value in cell)
    cell_maximum = tuple(value + resolution for value in cell_minimum)
    overlap_minimum = tuple(
        max(item.bounds.minimum[index], cell_minimum[index])
        for index in range(3)
    )
    overlap_maximum = tuple(
        min(item.bounds.maximum[index], cell_maximum[index])
        for index in range(3)
    )
    if any(
        overlap_maximum[index] <= overlap_minimum[index]
        for index in range(3)
    ):
        return False
    if item.geometry["kind"] == "aabb":
        return True

    samples: list[tuple[float, ...]] = []
    for index in range(3):
        minimum = overlap_minimum[index]
        maximum = overlap_maximum[index]
        span = maximum - minimum
        margin = min(span / 4.0, resolution * 1e-6)
        samples.append(
            tuple(
                sorted(
                    {
                        minimum + margin,
                        (minimum + maximum) / 2.0,
                        maximum - margin,
                    }
                )
            )
        )
    return any(
        _contains(object_id, (x, y, z), objects)
        for x in samples[0]
        for y in samples[1]
        for z in samples[2]
    )


def _regions(
    walkable: set[tuple[int, int, int]],
    openings: set[tuple[int, int, int]],
) -> tuple[ConnectedRegion, ...]:
    remaining = set(walkable)
    groups: list[tuple[tuple[int, int, int], ...]] = []
    while remaining:
        seed = min(remaining)
        pending = deque([seed])
        remaining.remove(seed)
        group = {seed}
        while pending:
            x, y, z = pending.popleft()
            for neighbor in (
                (x - 1, y, z),
                (x + 1, y, z),
                (x, y, z - 1),
                (x, y, z + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    group.add(neighbor)
                    pending.append(neighbor)
        groups.append(tuple(sorted(group)))
    groups.sort(key=lambda cells: cells[0])
    return tuple(
        ConnectedRegion(
            region_id=f"region-{index:03d}",
            bounds=VoxelBounds(
                tuple(min(cell[i] for cell in cells) for i in range(3)),
                tuple(max(cell[i] for cell in cells) for i in range(3)),
            ),
            cells=cells,
            touches_opening=bool(set(cells) & openings),
        )
        for index, cells in enumerate(groups, start=1)
    )


def _support_relations(
    occupied: set[tuple[int, int, int]],
) -> tuple[SupportRelation, ...]:
    relations = []
    for cell in sorted(occupied):
        for neighbor in (
            (cell[0] + 1, cell[1], cell[2]),
            (cell[0], cell[1] + 1, cell[2]),
            (cell[0], cell[1], cell[2] + 1),
        ):
            if neighbor in occupied:
                relations.append(SupportRelation(cell, neighbor))
    return tuple(relations)


def _unsupported_occupied_cells(
    occupied: tuple[tuple[int, int, int], ...],
    support_relations: tuple[SupportRelation, ...],
    ground_y: int,
) -> tuple[tuple[int, int, int], ...]:
    """Derive floating occupied components from the stored support graph.

    Support propagates from every occupied cell on the ground layer of the
    stored bounds along the stored face-adjacency support relations.  Any
    occupied cell that no ground-connected component reaches is unsupported,
    mirroring the scan-path semantics of
    ``archflow.adapters.voxel_observation._support_graph``.
    """

    occupied_set = set(occupied)
    adjacency: dict[
        tuple[int, int, int],
        list[tuple[int, int, int]],
    ] = {}
    for relation in support_relations:
        if (
            relation.first not in occupied_set
            or relation.second not in occupied_set
        ):
            continue
        adjacency.setdefault(relation.first, []).append(relation.second)
        adjacency.setdefault(relation.second, []).append(relation.first)
    grounded = sorted(
        cell for cell in occupied_set if cell[1] == ground_y
    )
    supported = set(grounded)
    pending = deque(grounded)
    while pending:
        current = pending.popleft()
        for neighbor in adjacency.get(current, ()):
            if neighbor not in supported:
                supported.add(neighbor)
                pending.append(neighbor)
    return tuple(sorted(occupied_set - supported))


def _local_sample(
    item: SceneObject,
    resolution: float,
    objects: Mapping[str, SceneObject],
    maximum_cells: int,
) -> LocalDetailSample:
    ranges = _cell_ranges(item.bounds, resolution)
    count = math.prod(len(value) for value in ranges)
    if count > maximum_cells:
        raise SandboxRealizationError(
            f"local detail {item.object_id} exceeds voxel budget"
        )
    occupied = tuple(
        cell
        for x in ranges[0]
        for y in ranges[1]
        for z in ranges[2]
        if _contains(
            item.object_id,
            _sample_point((x, y, z), resolution),
            objects,
        )
        for cell in ((x, y, z),)
    )
    return LocalDetailSample(
        object_id=item.object_id,
        resolution=resolution,
        occupied_sample_count=len(occupied),
        sample_digest=_digest(
            {
                "object_id": item.object_id,
                "source_object_digest": item.source_object_digest,
                "resolution": resolution,
                "occupied": occupied,
            }
        ),
    )


def derive_voxel_view(
    scene: HybridScene,
    receipt: SandboxRealizationReceipt,
    *,
    policy: VoxelizationPolicy,
) -> DerivedVoxelView:
    """Derive a bounded validation view while retaining the exact scene."""

    if not isinstance(scene, HybridScene):
        raise TypeError("scene must be HybridScene")
    if not isinstance(receipt, SandboxRealizationReceipt):
        raise TypeError("receipt must be SandboxRealizationReceipt")
    if not isinstance(policy, VoxelizationPolicy):
        raise TypeError("policy must be VoxelizationPolicy")
    if (
        receipt.status is not RealizationStatus.REALIZED
        or receipt.scene_digest != scene.scene_digest
        or receipt.geometry_program_digest != scene.geometry_program_digest
        or receipt.workspace_id != scene.workspace_id
    ):
        raise SandboxRealizationError(
            "voxel view requires the exact realized scene receipt"
        )
    objects = {item.object_id: item for item in scene.objects}
    physical = tuple(item for item in scene.objects if item.physical)
    if not physical:
        raise SandboxRealizationError("scene has no physical terminal geometry")
    overall = physical[0].bounds
    for item in physical[1:]:
        overall = overall.union(item.bounds)
    ranges = _cell_ranges(overall, policy.default_resolution)
    cell_count = math.prod(len(value) for value in ranges)
    if cell_count > policy.maximum_cells:
        raise SandboxRealizationError(
            "derived voxel view exceeds explicit maximum_cells"
        )
    occupied = {
        (x, y, z)
        for x in ranges[0]
        for y in ranges[1]
        for z in ranges[2]
        if any(
            _intersects_cell(
                item.object_id,
                (x, y, z),
                policy.default_resolution,
                objects,
            )
            for item in physical
        )
    }
    all_cells = {
        (x, y, z)
        for x in ranges[0]
        for y in ranges[1]
        for z in ranges[2]
    }
    walkable = {
        cell
        for cell in all_cells - occupied
        if (cell[0], cell[1] - 1, cell[2]) in occupied
        and (cell[0], cell[1] + 1, cell[2]) in all_cells - occupied
    }
    openings = {
        cell
        for object_id in scene.opening_object_ids
        for cell in all_cells
        if _intersects_cell(
            object_id,
            cell,
            policy.default_resolution,
            objects,
        )
        and cell not in occupied
        and cell in walkable
    }
    local_samples = tuple(
        _local_sample(
            objects[object_id],
            resolution,
            objects,
            policy.maximum_cells,
        )
        for object_id, resolution in policy.local_resolutions
        if object_id in objects
    )
    if len(local_samples) != len(policy.local_resolutions):
        missing = sorted(
            set(key for key, _ in policy.local_resolutions) - set(objects)
        )
        raise SandboxRealizationError(
            f"local resolutions name unknown objects: {missing}"
        )
    loss_code_set: set[str] = set()
    for item in physical:
        if item.representation is SceneRepresentation.MESH:
            loss_code_set.add("mesh_validation_uses_bounded_sampling")
        declared = item.geometry.get("loss_codes", [])
        if not isinstance(declared, list) or any(
            not isinstance(code, str) or not code for code in declared
        ):
            raise SandboxRealizationError(
                f"{item.object_id}: geometry loss codes are malformed"
            )
        loss_code_set.update(declared)
    loss_codes = tuple(sorted(loss_code_set))
    integer_bounds = VoxelBounds(
        tuple(value.start for value in ranges),
        tuple(value.stop - 1 for value in ranges),
    )
    return DerivedVoxelView(
        scene_digest=scene.scene_digest,
        realization_receipt_digest=receipt.receipt_digest,
        policy_digest=_digest(policy.to_dict()),
        resolution=policy.default_resolution,
        bounds=integer_bounds,
        occupied_cells=tuple(sorted(occupied)),
        walkable_cells=tuple(sorted(walkable)),
        opening_cells=tuple(sorted(openings)),
        connected_regions=_regions(walkable, openings),
        support_relations=_support_relations(occupied),
        local_detail_samples=local_samples,
        loss_codes=loss_codes,
        project_id=scene.project_id,
        version=scene.base.version,
        workspace_id=scene.workspace_id,
    )
