"""Fail-closed vertical-circulation maturity validation.

The contract deliberately separates a reserved circulation volume from a
resolved walking path and a coordinated assembly.  A bounding box is accepted
only as negative evidence: it may prove that an endpoint lies outside a
reservation, but it can never prove that a walking path exists.

Project knowledge may supply adopted criterion ranges.  Lower and upper
geometry endpoints are instead bound to exact design-state relations, level
facts, and interface facts; this module never derives an endpoint from RAG.
The checker is read-only and returns a common validation receipt with no
design, mutation, persistence, promotion, or stage-acceptance authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archflow.validation.vaulted_underpass import (
    UNDERPASS_ASSEMBLY_CHECKER_ID,
    UnderpassConstructionForm,
    VaultedUnderpassAssemblyContract,
    check_vaulted_underpass_assembly,
)
from archflow.validation.walking_surface_continuity import (
    WalkingSurfaceContinuityProfile,
    WalkingSurfaceCriteria as GenericWalkingSurfaceCriteria,
    WalkingSurfaceEdge as GenericWalkingSurfaceEdge,
    WalkingSurfaceEdgeKind as GenericWalkingSurfaceEdgeKind,
    WalkingSurfaceNode as GenericWalkingSurfaceNode,
    WalkingSurfaceNodeRole as GenericWalkingSurfaceNodeRole,
    WalkingSurfacePathRequirement as GenericWalkingSurfacePathRequirement,
    check_walking_surface_continuity,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "promotion_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}
_EXACT_DATUM_TOLERANCE = 1.0e-9
VERTICAL_CIRCULATION_MATURITY_CHECKER_ID = (
    "vertical-circulation-stage-maturity-checker"
)
VERTICAL_CIRCULATION_SUPPORT_CHECKER_ID = (
    "vertical-circulation-support-checker"
)
VERTICAL_CIRCULATION_LOAD_PATH_CHECKER_ID = (
    "vertical-circulation-load-path-checker"
)
VERTICAL_CIRCULATION_HOST_CUT_CHECKER_ID = (
    "vertical-circulation-host-cut-checker"
)
VERTICAL_CIRCULATION_UNDERPASS_CHECKER_ID = (
    "vertical-circulation-underpass-clearance-checker"
)


class VerticalCirculationError(ValueError):
    """A vertical-circulation contract or witness is malformed."""


class VerticalCirculationMaturity(StrEnum):
    RESERVATION = "reservation"
    RESOLVED_PATH = "resolved_path"
    ASSEMBLY = "assembly"


class VerticalCirculationUpAxis(StrEnum):
    X = "X"
    Y = "Y"
    Z = "Z"


class VerticalCirculationInterfaceRole(StrEnum):
    LOWER = "lower"
    UPPER = "upper"


class VerticalCirculationInterfaceBasis(StrEnum):
    """Only exact design-state relations may locate geometry endpoints."""

    DESIGN_STATE_RELATION = "design_state_relation"


class WalkingSurfaceWitnessKind(StrEnum):
    """Positive surface witnesses; AABB is intentionally not a member."""

    CAD_BREP_FACE = "cad_brep_face"
    CAD_MESH_FACE = "cad_mesh_face"
    CAD_NATIVE_SURFACE = "cad_native_surface"


class LandingRole(StrEnum):
    LOWER = "lower"
    INTERMEDIATE = "intermediate"
    UPPER = "upper"


class LandingOwnership(StrEnum):
    """Identify the sole owner of a terminal walking surface."""

    STAIR_ASSEMBLY = "stair_assembly"
    ADJOINING_INTERFACE = "adjoining_interface"


class WalkingSurfaceSampleRole(StrEnum):
    LOWER_INTERFACE = "lower_interface"
    TREAD = "tread"
    INTERMEDIATE_LANDING = "intermediate_landing"
    UPPER_INTERFACE = "upper_interface"


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise VerticalCirculationError(f"{field} must be a finite number")
    return float(value)


def _optional_non_negative(value: object, field: str) -> float | None:
    if value is None:
        return None
    normalized = _finite(value, field)
    if normalized < 0.0:
        raise VerticalCirculationError(f"{field} must be non-negative or None")
    return normalized


def _optional_logical_ref(value: object, field: str) -> str | None:
    if value is None:
        return None
    return logical_ref(value, field)


def _optional_sha256(value: object, field: str) -> str | None:
    if value is None:
        return None
    return require_sha256(value, field)


def _optional_bool(value: object, field: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise TypeError(f"{field} must be bool or None")
    return value


def _xyz(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise VerticalCirculationError(f"{field} must be a finite XYZ tuple")
    return tuple(_finite(item, field) for item in value)  # type: ignore[return-value]


def _xyz_from_list(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise VerticalCirculationError(f"serialized {field} must be an XYZ list")
    return _xyz(tuple(value), field)


def _axis_index(axis: VerticalCirculationUpAxis) -> int:
    return {
        VerticalCirculationUpAxis.X: 0,
        VerticalCirculationUpAxis.Y: 1,
        VerticalCirculationUpAxis.Z: 2,
    }[axis]


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second)))


def _receipt_ref(receipt: CheckReceiptEnvelope) -> str:
    return f"check-receipt:{receipt.receipt_id}"


@dataclass(frozen=True, slots=True)
class VerticalCirculationInterface:
    role: VerticalCirculationInterfaceRole
    relation_ref: str
    interface_ref: str
    level_ref: str
    datum_fact_ref: str
    datum: float
    point: tuple[float, float, float]
    basis: VerticalCirculationInterfaceBasis = (
        VerticalCirculationInterfaceBasis.DESIGN_STATE_RELATION
    )

    SCHEMA: ClassVar[str] = "VerticalCirculationInterface@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, VerticalCirculationInterfaceRole):
            raise TypeError("role must be VerticalCirculationInterfaceRole")
        for field in ("relation_ref", "interface_ref", "level_ref", "datum_fact_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        object.__setattr__(self, "datum", _finite(self.datum, "datum"))
        object.__setattr__(self, "point", _xyz(self.point, "interface point"))
        if self.basis is not VerticalCirculationInterfaceBasis.DESIGN_STATE_RELATION:
            raise VerticalCirculationError(
                "interface geometry must be based on an exact design-state relation"
            )

    @property
    def subject_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                (
                    self.relation_ref,
                    self.interface_ref,
                    self.level_ref,
                    self.datum_fact_ref,
                )
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "relation_ref": self.relation_ref,
            "interface_ref": self.interface_ref,
            "level_ref": self.level_ref,
            "datum_fact_ref": self.datum_fact_ref,
            "datum": self.datum,
            "point": list(self.point),
            "basis": self.basis.value,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerticalCirculationInterface":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "relation_ref",
                "interface_ref",
                "level_ref",
                "datum_fact_ref",
                "datum",
                "point",
                "basis",
                *_AUTHORITY_FIELDS,
            },
            "vertical-circulation interface",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported interface schema")
        result = cls(
            role=VerticalCirculationInterfaceRole(payload["role"]),
            relation_ref=payload["relation_ref"],
            interface_ref=payload["interface_ref"],
            level_ref=payload["level_ref"],
            datum_fact_ref=payload["datum_fact_ref"],
            datum=payload["datum"],
            point=_xyz_from_list(payload["point"], "interface point"),
            basis=VerticalCirculationInterfaceBasis(payload["basis"]),
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("interface identity changed")
        return result


@dataclass(frozen=True, slots=True)
class VerticalCirculationObjectBinding:
    component_ref: str
    object_ref: str
    operation_ref: str

    SCHEMA: ClassVar[str] = "VerticalCirculationObjectBinding@1"

    def __post_init__(self) -> None:
        for field in ("component_ref", "object_ref", "operation_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))

    @property
    def ref(self) -> str:
        return f"circulation-binding:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_ref": self.component_ref,
            "object_ref": self.object_ref,
            "operation_ref": self.operation_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerticalCirculationObjectBinding":
        payload = exact_mapping(
            value,
            {"schema", "component_ref", "object_ref", "operation_ref", *_AUTHORITY_FIELDS},
            "vertical-circulation object binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported object-binding schema")
        result = cls(
            component_ref=payload["component_ref"],
            object_ref=payload["object_ref"],
            operation_ref=payload["operation_ref"],
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("object-binding identity changed")
        return result


@dataclass(frozen=True, slots=True)
class VerticalCirculationCriteria:
    endpoint_tolerance: float | None
    min_clear_width: float | None
    min_headroom: float | None
    min_landing_depth: float | None
    min_underpass_clearance: float | None = None
    max_horizontal_step: float | None = None
    max_vertical_step: float | None = None
    max_surface_gap: float | None = None
    evidence_refs: tuple[str, ...] = ()
    adoption_refs: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "VerticalCirculationCriteria@1"

    def __post_init__(self) -> None:
        for field in (
            "endpoint_tolerance",
            "min_clear_width",
            "min_headroom",
            "min_landing_depth",
            "min_underpass_clearance",
            "max_horizontal_step",
            "max_vertical_step",
            "max_surface_gap",
        ):
            object.__setattr__(
                self,
                field,
                _optional_non_negative(getattr(self, field), field),
            )
        for field in ("evidence_refs", "adoption_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field),
            )

    @property
    def core_values(self) -> tuple[tuple[str, float | None], ...]:
        return (
            ("endpoint_tolerance", self.endpoint_tolerance),
            ("min_clear_width", self.min_clear_width),
            ("min_headroom", self.min_headroom),
            ("min_landing_depth", self.min_landing_depth),
            ("max_horizontal_step", self.max_horizontal_step),
            ("max_vertical_step", self.max_vertical_step),
            ("max_surface_gap", self.max_surface_gap),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "endpoint_tolerance": self.endpoint_tolerance,
            "min_clear_width": self.min_clear_width,
            "min_headroom": self.min_headroom,
            "min_landing_depth": self.min_landing_depth,
            "min_underpass_clearance": self.min_underpass_clearance,
            "max_horizontal_step": self.max_horizontal_step,
            "max_vertical_step": self.max_vertical_step,
            "max_surface_gap": self.max_surface_gap,
            "evidence_refs": list(self.evidence_refs),
            "adoption_refs": list(self.adoption_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerticalCirculationCriteria":
        payload = exact_mapping(
            value,
            {
                "schema",
                "endpoint_tolerance",
                "min_clear_width",
                "min_headroom",
                "min_landing_depth",
                "min_underpass_clearance",
                "max_horizontal_step",
                "max_vertical_step",
                "max_surface_gap",
                "evidence_refs",
                "adoption_refs",
                *_AUTHORITY_FIELDS,
            },
            "vertical-circulation criteria",
        )
        for field in ("evidence_refs", "adoption_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            endpoint_tolerance=payload["endpoint_tolerance"],
            min_clear_width=payload["min_clear_width"],
            min_headroom=payload["min_headroom"],
            min_landing_depth=payload["min_landing_depth"],
            min_underpass_clearance=payload["min_underpass_clearance"],
            max_horizontal_step=payload["max_horizontal_step"],
            max_vertical_step=payload["max_vertical_step"],
            max_surface_gap=payload["max_surface_gap"],
            evidence_refs=tuple(payload["evidence_refs"]),
            adoption_refs=tuple(payload["adoption_refs"]),
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("criteria identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfaceSample:
    ordinal: int
    role: WalkingSurfaceSampleRole
    sample_ref: str
    surface_ref: str
    component_ref: str
    object_ref: str
    operation_ref: str
    point: tuple[float, float, float]
    clear_width: float | None
    headroom: float | None
    clear_width_witness_ref: str | None
    headroom_witness_ref: str | None
    solver_tread_ref: str | None = None
    interface_ref: str | None = None

    SCHEMA: ClassVar[str] = "WalkingSurfaceSample@1"

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise VerticalCirculationError("sample ordinal must be a non-negative integer")
        if not isinstance(self.role, WalkingSurfaceSampleRole):
            raise TypeError("sample role must be WalkingSurfaceSampleRole")
        for field in (
            "sample_ref",
            "surface_ref",
            "component_ref",
            "object_ref",
            "operation_ref",
        ):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        object.__setattr__(self, "point", _xyz(self.point, "sample point"))
        object.__setattr__(
            self, "clear_width", _optional_non_negative(self.clear_width, "clear_width")
        )
        object.__setattr__(self, "headroom", _optional_non_negative(self.headroom, "headroom"))
        for field in (
            "clear_width_witness_ref",
            "headroom_witness_ref",
            "solver_tread_ref",
        ):
            object.__setattr__(
                self,
                field,
                _optional_logical_ref(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "interface_ref",
            _optional_logical_ref(self.interface_ref, "sample interface_ref"),
        )

    @property
    def binding_tuple(self) -> tuple[str, str, str]:
        return self.component_ref, self.object_ref, self.operation_ref

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "ordinal": self.ordinal,
            "role": self.role.value,
            "sample_ref": self.sample_ref,
            "surface_ref": self.surface_ref,
            "component_ref": self.component_ref,
            "object_ref": self.object_ref,
            "operation_ref": self.operation_ref,
            "point": list(self.point),
            "clear_width": self.clear_width,
            "headroom": self.headroom,
            "clear_width_witness_ref": self.clear_width_witness_ref,
            "headroom_witness_ref": self.headroom_witness_ref,
            "solver_tread_ref": self.solver_tread_ref,
            "interface_ref": self.interface_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceSample":
        payload = exact_mapping(
            value,
            {
                "schema",
                "ordinal",
                "role",
                "sample_ref",
                "surface_ref",
                "component_ref",
                "object_ref",
                "operation_ref",
                "point",
                "clear_width",
                "headroom",
                "clear_width_witness_ref",
                "headroom_witness_ref",
                "solver_tread_ref",
                "interface_ref",
                *_AUTHORITY_FIELDS,
            },
            "walking-surface sample",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported walking-surface sample schema")
        result = cls(
            ordinal=payload["ordinal"],
            role=WalkingSurfaceSampleRole(payload["role"]),
            sample_ref=payload["sample_ref"],
            surface_ref=payload["surface_ref"],
            component_ref=payload["component_ref"],
            object_ref=payload["object_ref"],
            operation_ref=payload["operation_ref"],
            point=_xyz_from_list(payload["point"], "sample point"),
            clear_width=payload["clear_width"],
            headroom=payload["headroom"],
            clear_width_witness_ref=payload["clear_width_witness_ref"],
            headroom_witness_ref=payload["headroom_witness_ref"],
            solver_tread_ref=payload["solver_tread_ref"],
            interface_ref=payload["interface_ref"],
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("walking-surface sample identity changed")
        return result


@dataclass(frozen=True, slots=True)
class LandingWitness:
    role: LandingRole
    ownership: LandingOwnership
    landing_ref: str
    surface_ref: str
    component_ref: str
    object_ref: str
    operation_ref: str
    point: tuple[float, float, float]
    clear_depth: float | None
    clear_width: float | None
    clear_depth_witness_ref: str | None
    clear_width_witness_ref: str | None
    interface_ref: str | None = None

    SCHEMA: ClassVar[str] = "LandingWitness@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, LandingRole):
            raise TypeError("role must be LandingRole")
        if not isinstance(self.ownership, LandingOwnership):
            raise TypeError("ownership must be LandingOwnership")
        for field in (
            "landing_ref",
            "surface_ref",
            "component_ref",
            "object_ref",
            "operation_ref",
        ):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        object.__setattr__(self, "point", _xyz(self.point, "landing point"))
        object.__setattr__(
            self, "clear_depth", _optional_non_negative(self.clear_depth, "clear_depth")
        )
        object.__setattr__(
            self, "clear_width", _optional_non_negative(self.clear_width, "clear_width")
        )
        for field in ("clear_depth_witness_ref", "clear_width_witness_ref"):
            object.__setattr__(
                self,
                field,
                _optional_logical_ref(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "interface_ref",
            _optional_logical_ref(self.interface_ref, "landing interface_ref"),
        )

    @property
    def binding_tuple(self) -> tuple[str, str, str]:
        return self.component_ref, self.object_ref, self.operation_ref

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "ownership": self.ownership.value,
            "landing_ref": self.landing_ref,
            "surface_ref": self.surface_ref,
            "component_ref": self.component_ref,
            "object_ref": self.object_ref,
            "operation_ref": self.operation_ref,
            "point": list(self.point),
            "clear_depth": self.clear_depth,
            "clear_width": self.clear_width,
            "clear_depth_witness_ref": self.clear_depth_witness_ref,
            "clear_width_witness_ref": self.clear_width_witness_ref,
            "interface_ref": self.interface_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LandingWitness":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "ownership",
                "landing_ref",
                "surface_ref",
                "component_ref",
                "object_ref",
                "operation_ref",
                "point",
                "clear_depth",
                "clear_width",
                "clear_depth_witness_ref",
                "clear_width_witness_ref",
                "interface_ref",
                *_AUTHORITY_FIELDS,
            },
            "landing witness",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported landing-witness schema")
        result = cls(
            role=LandingRole(payload["role"]),
            ownership=LandingOwnership(payload["ownership"]),
            landing_ref=payload["landing_ref"],
            surface_ref=payload["surface_ref"],
            component_ref=payload["component_ref"],
            object_ref=payload["object_ref"],
            operation_ref=payload["operation_ref"],
            point=_xyz_from_list(payload["point"], "landing point"),
            clear_depth=payload["clear_depth"],
            clear_width=payload["clear_width"],
            clear_depth_witness_ref=payload["clear_depth_witness_ref"],
            clear_width_witness_ref=payload["clear_width_witness_ref"],
            interface_ref=payload["interface_ref"],
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("landing-witness identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfaceAdjacency:
    """Geometry-extracted adjacency between consecutive walking surfaces."""

    ordinal: int
    adjacency_ref: str
    from_sample_ref: str
    to_sample_ref: str
    from_surface_ref: str
    to_surface_ref: str
    surface_gap: float | None
    gap_witness_ref: str | None

    SCHEMA: ClassVar[str] = "WalkingSurfaceAdjacency@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
        ):
            raise VerticalCirculationError(
                "surface-adjacency ordinal must be a non-negative integer"
            )
        for field in (
            "adjacency_ref",
            "from_sample_ref",
            "to_sample_ref",
            "from_surface_ref",
            "to_surface_ref",
        ):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        object.__setattr__(
            self,
            "surface_gap",
            _optional_non_negative(self.surface_gap, "surface_gap"),
        )
        object.__setattr__(
            self,
            "gap_witness_ref",
            _optional_logical_ref(self.gap_witness_ref, "gap_witness_ref"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "ordinal": self.ordinal,
            "adjacency_ref": self.adjacency_ref,
            "from_sample_ref": self.from_sample_ref,
            "to_sample_ref": self.to_sample_ref,
            "from_surface_ref": self.from_surface_ref,
            "to_surface_ref": self.to_surface_ref,
            "surface_gap": self.surface_gap,
            "gap_witness_ref": self.gap_witness_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceAdjacency":
        payload = exact_mapping(
            value,
            {
                "schema",
                "ordinal",
                "adjacency_ref",
                "from_sample_ref",
                "to_sample_ref",
                "from_surface_ref",
                "to_surface_ref",
                "surface_gap",
                "gap_witness_ref",
                *_AUTHORITY_FIELDS,
            },
            "walking-surface adjacency",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported surface-adjacency schema")
        result = cls(
            ordinal=payload["ordinal"],
            adjacency_ref=payload["adjacency_ref"],
            from_sample_ref=payload["from_sample_ref"],
            to_sample_ref=payload["to_sample_ref"],
            from_surface_ref=payload["from_surface_ref"],
            to_surface_ref=payload["to_surface_ref"],
            surface_gap=payload["surface_gap"],
            gap_witness_ref=payload["gap_witness_ref"],
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("surface-adjacency identity changed")
        return result


@dataclass(frozen=True, slots=True)
class WalkingSurfaceGeometryWitness:
    witness_id: str
    branch: BranchRef
    scope_digest: str
    stage_id: str
    stage_subject_ref: str
    stage_subject_digest: str
    design_state_digest: str
    witness_kind: WalkingSurfaceWitnessKind
    extraction_ref: str
    extraction_digest: str
    artifact_sha256: str
    program_digest: str
    readback_digest: str
    length_unit_ref: str
    coordinate_frame_ref: str
    up_axis: VerticalCirculationUpAxis
    solver_result_digest: str
    solver_tread_refs: tuple[str, ...]
    object_bindings: tuple[VerticalCirculationObjectBinding, ...]
    path_samples: tuple[WalkingSurfaceSample, ...]
    surface_adjacencies: tuple[WalkingSurfaceAdjacency, ...]
    landings: tuple[LandingWitness, ...]

    SCHEMA: ClassVar[str] = "WalkingSurfaceGeometryWitness@2"

    def __post_init__(self) -> None:
        identifier(self.witness_id, "witness_id")
        require_exact_branch(self.branch)
        for field in ("scope_digest", "stage_subject_digest", "design_state_digest"):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "stage_subject_ref",
            logical_ref(self.stage_subject_ref, "stage_subject_ref"),
        )
        if not isinstance(self.witness_kind, WalkingSurfaceWitnessKind):
            raise TypeError("witness_kind must be WalkingSurfaceWitnessKind")
        object.__setattr__(self, "extraction_ref", logical_ref(self.extraction_ref, "extraction_ref"))
        for field in ("extraction_digest", "artifact_sha256", "program_digest", "readback_digest"):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        for field in ("length_unit_ref", "coordinate_frame_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        if not isinstance(self.up_axis, VerticalCirculationUpAxis):
            raise TypeError("up_axis must be VerticalCirculationUpAxis")
        object.__setattr__(
            self,
            "solver_result_digest",
            require_sha256(self.solver_result_digest, "solver_result_digest"),
        )
        object.__setattr__(
            self,
            "solver_tread_refs",
            deterministic_refs(self.solver_tread_refs, "solver_tread_refs"),
        )
        if not isinstance(self.object_bindings, tuple) or any(
            not isinstance(item, VerticalCirculationObjectBinding)
            for item in self.object_bindings
        ):
            raise TypeError("object_bindings must contain VerticalCirculationObjectBinding")
        ordered_bindings = tuple(
            sorted(
                self.object_bindings,
                key=lambda item: (item.component_ref, item.object_ref, item.operation_ref),
            )
        )
        keys = tuple(
            (item.component_ref, item.object_ref, item.operation_ref)
            for item in ordered_bindings
        )
        if not ordered_bindings or len(keys) != len(set(keys)):
            raise VerticalCirculationError("witness object bindings must be non-empty and unique")
        object.__setattr__(self, "object_bindings", ordered_bindings)
        if not isinstance(self.path_samples, tuple) or any(
            not isinstance(item, WalkingSurfaceSample) for item in self.path_samples
        ):
            raise TypeError("path_samples must contain WalkingSurfaceSample")
        ordinals = tuple(item.ordinal for item in self.path_samples)
        if ordinals != tuple(range(len(ordinals))):
            raise VerticalCirculationError("path sample ordinals must be contiguous from zero")
        if len({item.sample_ref for item in self.path_samples}) != len(self.path_samples):
            raise VerticalCirculationError("path sample refs must be unique")
        if not isinstance(self.surface_adjacencies, tuple) or any(
            not isinstance(item, WalkingSurfaceAdjacency)
            for item in self.surface_adjacencies
        ):
            raise TypeError(
                "surface_adjacencies must contain WalkingSurfaceAdjacency"
            )
        adjacency_ordinals = tuple(item.ordinal for item in self.surface_adjacencies)
        if adjacency_ordinals != tuple(range(len(adjacency_ordinals))):
            raise VerticalCirculationError(
                "surface-adjacency ordinals must be contiguous from zero"
            )
        if len(
            {item.adjacency_ref for item in self.surface_adjacencies}
        ) != len(self.surface_adjacencies):
            raise VerticalCirculationError("surface-adjacency refs must be unique")
        if not isinstance(self.landings, tuple) or any(
            not isinstance(item, LandingWitness) for item in self.landings
        ):
            raise TypeError("landings must contain LandingWitness")
        ordered_landings = tuple(
            sorted(self.landings, key=lambda item: (item.role.value, item.landing_ref))
        )
        if len({item.landing_ref for item in ordered_landings}) != len(ordered_landings):
            raise VerticalCirculationError("landing refs must be unique")
        object.__setattr__(self, "landings", ordered_landings)

    @property
    def ref(self) -> str:
        return f"walking-surface-witness:{self.witness_id}"

    @property
    def binding_tuples(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (item.component_ref, item.object_ref, item.operation_ref)
            for item in self.object_bindings
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "witness_id": self.witness_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_id": self.stage_id,
            "stage_subject_ref": self.stage_subject_ref,
            "stage_subject_digest": self.stage_subject_digest,
            "design_state_digest": self.design_state_digest,
            "witness_kind": self.witness_kind.value,
            "extraction_ref": self.extraction_ref,
            "extraction_digest": self.extraction_digest,
            "artifact_sha256": self.artifact_sha256,
            "program_digest": self.program_digest,
            "readback_digest": self.readback_digest,
            "length_unit_ref": self.length_unit_ref,
            "coordinate_frame_ref": self.coordinate_frame_ref,
            "up_axis": self.up_axis.value,
            "solver_result_digest": self.solver_result_digest,
            "solver_tread_refs": list(self.solver_tread_refs),
            "object_bindings": [item.to_dict() for item in self.object_bindings],
            "path_samples": [item.to_dict() for item in self.path_samples],
            "surface_adjacencies": [
                item.to_dict() for item in self.surface_adjacencies
            ],
            "landings": [item.to_dict() for item in self.landings],
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "WalkingSurfaceGeometryWitness":
        payload = exact_mapping(
            value,
            {
                "schema",
                "witness_id",
                "branch",
                "scope_digest",
                "stage_id",
                "stage_subject_ref",
                "stage_subject_digest",
                "design_state_digest",
                "witness_kind",
                "extraction_ref",
                "extraction_digest",
                "artifact_sha256",
                "program_digest",
                "readback_digest",
                "length_unit_ref",
                "coordinate_frame_ref",
                "up_axis",
                "solver_result_digest",
                "solver_tread_refs",
                "object_bindings",
                "path_samples",
                "surface_adjacencies",
                "landings",
                *_AUTHORITY_FIELDS,
            },
            "walking-surface geometry witness",
        )
        for field in (
            "solver_tread_refs",
            "object_bindings",
            "path_samples",
            "surface_adjacencies",
            "landings",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported geometry-witness schema")
        result = cls(
            witness_id=payload["witness_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_id=payload["stage_id"],
            stage_subject_ref=payload["stage_subject_ref"],
            stage_subject_digest=payload["stage_subject_digest"],
            design_state_digest=payload["design_state_digest"],
            witness_kind=WalkingSurfaceWitnessKind(payload["witness_kind"]),
            extraction_ref=payload["extraction_ref"],
            extraction_digest=payload["extraction_digest"],
            artifact_sha256=payload["artifact_sha256"],
            program_digest=payload["program_digest"],
            readback_digest=payload["readback_digest"],
            length_unit_ref=payload["length_unit_ref"],
            coordinate_frame_ref=payload["coordinate_frame_ref"],
            up_axis=VerticalCirculationUpAxis(payload["up_axis"]),
            solver_result_digest=payload["solver_result_digest"],
            solver_tread_refs=tuple(payload["solver_tread_refs"]),
            object_bindings=tuple(
                VerticalCirculationObjectBinding.from_dict(item)
                for item in payload["object_bindings"]
            ),
            path_samples=tuple(
                WalkingSurfaceSample.from_dict(item) for item in payload["path_samples"]
            ),
            surface_adjacencies=tuple(
                WalkingSurfaceAdjacency.from_dict(item)
                for item in payload["surface_adjacencies"]
            ),
            landings=tuple(LandingWitness.from_dict(item) for item in payload["landings"]),
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("geometry-witness identity changed")
        return result


@dataclass(frozen=True, slots=True)
class CirculationAabbNegativePrecheck:
    source_ref: str
    component_ref: str
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    SCHEMA: ClassVar[str] = "CirculationAabbNegativePrecheck@2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ref", logical_ref(self.source_ref, "AABB source_ref"))
        object.__setattr__(
            self,
            "component_ref",
            logical_ref(self.component_ref, "AABB component_ref"),
        )
        object.__setattr__(self, "minimum", _xyz(self.minimum, "AABB minimum"))
        object.__setattr__(self, "maximum", _xyz(self.maximum, "AABB maximum"))
        if any(
            high - low <= _EXACT_DATUM_TOLERANCE
            for low, high in zip(self.minimum, self.maximum)
        ):
            raise VerticalCirculationError(
                "AABB must have positive extent on all three axes"
            )

    def contains(self, point: tuple[float, float, float]) -> bool:
        return all(
            low - _EXACT_DATUM_TOLERANCE <= value <= high + _EXACT_DATUM_TOLERANCE
            for low, value, high in zip(self.minimum, point, self.maximum)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_ref": self.source_ref,
            "component_ref": self.component_ref,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CirculationAabbNegativePrecheck":
        payload = exact_mapping(
            value,
            {
                "schema",
                "source_ref",
                "component_ref",
                "minimum",
                "maximum",
                *_AUTHORITY_FIELDS,
            },
            "circulation AABB negative precheck",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported AABB-precheck schema")
        result = cls(
            source_ref=payload["source_ref"],
            component_ref=payload["component_ref"],
            minimum=_xyz_from_list(payload["minimum"], "AABB minimum"),
            maximum=_xyz_from_list(payload["maximum"], "AABB maximum"),
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("AABB-precheck identity changed")
        return result


@dataclass(frozen=True, slots=True)
class VerticalCirculationAssemblyWitness:
    witness_id: str
    branch: BranchRef
    scope_digest: str
    stage_id: str
    stage_subject_ref: str
    stage_subject_digest: str
    design_state_digest: str
    artifact_sha256: str
    program_digest: str
    readback_digest: str
    solver_result_digest: str
    support_receipt: CheckReceiptEnvelope | None
    load_path_receipt: CheckReceiptEnvelope | None
    host_cut_receipt: CheckReceiptEnvelope | None
    underpass_applicable: bool | None
    underpass_applicability_ref: str | None = None
    underpass_receipt: CheckReceiptEnvelope | None = None
    underpass_form: UnderpassConstructionForm = UnderpassConstructionForm.UNKNOWN
    underpass_form_evidence_refs: tuple[str, ...] = ()
    underpass_form_adoption_refs: tuple[str, ...] = ()
    underpass_assembly_contract: VaultedUnderpassAssemblyContract | None = None
    underpass_assembly_receipt: CheckReceiptEnvelope | None = None

    SCHEMA: ClassVar[str] = "VerticalCirculationAssemblyWitness@3"

    def __post_init__(self) -> None:
        identifier(self.witness_id, "assembly witness_id")
        require_exact_branch(self.branch)
        for field in (
            "scope_digest",
            "stage_subject_digest",
            "design_state_digest",
            "artifact_sha256",
            "program_digest",
            "readback_digest",
            "solver_result_digest",
        ):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "stage_subject_ref",
            logical_ref(self.stage_subject_ref, "stage_subject_ref"),
        )
        for field in (
            "support_receipt",
            "load_path_receipt",
            "host_cut_receipt",
            "underpass_receipt",
            "underpass_assembly_receipt",
        ):
            receipt = getattr(self, field)
            if receipt is not None and not isinstance(receipt, CheckReceiptEnvelope):
                raise TypeError(f"{field} must be CheckReceiptEnvelope or None")
        object.__setattr__(
            self,
            "underpass_applicable",
            _optional_bool(self.underpass_applicable, "underpass_applicable"),
        )
        object.__setattr__(
            self,
            "underpass_applicability_ref",
            _optional_logical_ref(
                self.underpass_applicability_ref,
                "underpass_applicability_ref",
            ),
        )
        if not isinstance(self.underpass_form, UnderpassConstructionForm):
            raise TypeError("underpass_form must be UnderpassConstructionForm")
        for field in (
            "underpass_form_evidence_refs",
            "underpass_form_adoption_refs",
        ):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    field,
                    allow_empty=True,
                ),
            )
        if self.underpass_assembly_contract is not None and not isinstance(
            self.underpass_assembly_contract,
            VaultedUnderpassAssemblyContract,
        ):
            raise TypeError(
                "underpass_assembly_contract must be "
                "VaultedUnderpassAssemblyContract or None"
            )

    @property
    def ref(self) -> str:
        return f"circulation-assembly-witness:{self.witness_id}"

    @property
    def underpass_assembly_denominator_refs(self) -> tuple[str, ...]:
        refs: set[str] = set()
        if self.underpass_assembly_contract is not None:
            refs.add(self.underpass_assembly_contract.ref)
            refs.update(self.underpass_assembly_contract.denominator_refs)
        if self.underpass_assembly_receipt is not None:
            refs.add(_receipt_ref(self.underpass_assembly_receipt))
            refs.update(self.underpass_assembly_receipt.coverage_denominator)
        return tuple(sorted(refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "witness_id": self.witness_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_id": self.stage_id,
            "stage_subject_ref": self.stage_subject_ref,
            "stage_subject_digest": self.stage_subject_digest,
            "design_state_digest": self.design_state_digest,
            "artifact_sha256": self.artifact_sha256,
            "program_digest": self.program_digest,
            "readback_digest": self.readback_digest,
            "solver_result_digest": self.solver_result_digest,
            "support_receipt": (
                self.support_receipt.to_dict()
                if self.support_receipt is not None
                else None
            ),
            "load_path_receipt": (
                self.load_path_receipt.to_dict()
                if self.load_path_receipt is not None
                else None
            ),
            "host_cut_receipt": (
                self.host_cut_receipt.to_dict()
                if self.host_cut_receipt is not None
                else None
            ),
            "underpass_applicable": self.underpass_applicable,
            "underpass_applicability_ref": self.underpass_applicability_ref,
            "underpass_receipt": (
                self.underpass_receipt.to_dict()
                if self.underpass_receipt is not None
                else None
            ),
            "underpass_form": self.underpass_form.value,
            "underpass_form_evidence_refs": list(
                self.underpass_form_evidence_refs
            ),
            "underpass_form_adoption_refs": list(
                self.underpass_form_adoption_refs
            ),
            "underpass_assembly_contract": (
                self.underpass_assembly_contract.to_dict()
                if self.underpass_assembly_contract is not None
                else None
            ),
            "underpass_assembly_receipt": (
                self.underpass_assembly_receipt.to_dict()
                if self.underpass_assembly_receipt is not None
                else None
            ),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerticalCirculationAssemblyWitness":
        payload = exact_mapping(
            value,
            {
                "schema",
                "witness_id",
                "branch",
                "scope_digest",
                "stage_id",
                "stage_subject_ref",
                "stage_subject_digest",
                "design_state_digest",
                "artifact_sha256",
                "program_digest",
                "readback_digest",
                "solver_result_digest",
                "support_receipt",
                "load_path_receipt",
                "host_cut_receipt",
                "underpass_applicable",
                "underpass_applicability_ref",
                "underpass_receipt",
                "underpass_form",
                "underpass_form_evidence_refs",
                "underpass_form_adoption_refs",
                "underpass_assembly_contract",
                "underpass_assembly_receipt",
                *_AUTHORITY_FIELDS,
            },
            "vertical-circulation assembly witness",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported assembly-witness schema")
        for field in (
            "underpass_form_evidence_refs",
            "underpass_form_adoption_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"assembly witness {field} must be a list")
        result = cls(
            witness_id=payload["witness_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_id=payload["stage_id"],
            stage_subject_ref=payload["stage_subject_ref"],
            stage_subject_digest=payload["stage_subject_digest"],
            design_state_digest=payload["design_state_digest"],
            artifact_sha256=payload["artifact_sha256"],
            program_digest=payload["program_digest"],
            readback_digest=payload["readback_digest"],
            solver_result_digest=payload["solver_result_digest"],
            support_receipt=(
                CheckReceiptEnvelope.from_dict(payload["support_receipt"])
                if payload["support_receipt"] is not None
                else None
            ),
            load_path_receipt=(
                CheckReceiptEnvelope.from_dict(payload["load_path_receipt"])
                if payload["load_path_receipt"] is not None
                else None
            ),
            host_cut_receipt=(
                CheckReceiptEnvelope.from_dict(payload["host_cut_receipt"])
                if payload["host_cut_receipt"] is not None
                else None
            ),
            underpass_applicable=payload["underpass_applicable"],
            underpass_applicability_ref=payload["underpass_applicability_ref"],
            underpass_receipt=(
                CheckReceiptEnvelope.from_dict(payload["underpass_receipt"])
                if payload["underpass_receipt"] is not None
                else None
            ),
            underpass_form=UnderpassConstructionForm(payload["underpass_form"]),
            underpass_form_evidence_refs=tuple(
                payload["underpass_form_evidence_refs"]
            ),
            underpass_form_adoption_refs=tuple(
                payload["underpass_form_adoption_refs"]
            ),
            underpass_assembly_contract=(
                VaultedUnderpassAssemblyContract.from_dict(
                    payload["underpass_assembly_contract"]
                )
                if payload["underpass_assembly_contract"] is not None
                else None
            ),
            underpass_assembly_receipt=(
                CheckReceiptEnvelope.from_dict(
                    payload["underpass_assembly_receipt"]
                )
                if payload["underpass_assembly_receipt"] is not None
                else None
            ),
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("assembly-witness identity changed")
        return result


@dataclass(frozen=True, slots=True)
class VerticalCirculationContract:
    contract_id: str
    branch: BranchRef
    scope_digest: str
    stage_id: str
    stage_subject_ref: str
    stage_subject_digest: str
    design_state_digest: str
    maturity: VerticalCirculationMaturity
    artifact_sha256: str | None
    program_digest: str | None
    readback_digest: str | None
    solver_result_digest: str
    solver_tread_refs: tuple[str, ...]
    component_refs: tuple[str, ...]
    object_bindings: tuple[VerticalCirculationObjectBinding, ...]
    interface_host_bindings: tuple[VerticalCirculationObjectBinding, ...]
    length_unit_ref: str
    coordinate_frame_ref: str
    up_axis: VerticalCirculationUpAxis
    lower_interface: VerticalCirculationInterface
    upper_interface: VerticalCirculationInterface
    lower_landing_ownership: LandingOwnership
    upper_landing_ownership: LandingOwnership
    criteria: VerticalCirculationCriteria
    geometry_witness: WalkingSurfaceGeometryWitness | None = None
    aabb_precheck: CirculationAabbNegativePrecheck | None = None
    assembly_witness: VerticalCirculationAssemblyWitness | None = None
    evidence_refs: tuple[str, ...] = ()
    solver_landing_refs: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "VerticalCirculationContract@2"

    def __post_init__(self) -> None:
        identifier(self.contract_id, "contract_id")
        require_exact_branch(self.branch)
        for field in (
            "scope_digest",
            "stage_subject_digest",
            "design_state_digest",
            "solver_result_digest",
        ):
            object.__setattr__(self, field, require_sha256(getattr(self, field), field))
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "stage_subject_ref",
            logical_ref(self.stage_subject_ref, "stage_subject_ref"),
        )
        if not isinstance(self.maturity, VerticalCirculationMaturity):
            raise TypeError("maturity must be VerticalCirculationMaturity")
        for field in ("artifact_sha256", "program_digest", "readback_digest"):
            value = getattr(self, field)
            if self.maturity is VerticalCirculationMaturity.RESERVATION:
                if value is not None:
                    raise VerticalCirculationError(
                        f"reservation cannot claim a realized {field}"
                    )
            else:
                object.__setattr__(
                    self,
                    field,
                    require_sha256(value, field),
                )
        object.__setattr__(
            self,
            "solver_tread_refs",
            deterministic_refs(
                self.solver_tread_refs,
                "solver_tread_refs",
                allow_empty=(
                    self.maturity is VerticalCirculationMaturity.RESERVATION
                ),
            ),
        )
        if (
            self.maturity is VerticalCirculationMaturity.RESERVATION
            and self.solver_tread_refs
        ):
            raise VerticalCirculationError(
                "reservation cannot claim a solver tread denominator"
            )
        object.__setattr__(
            self,
            "solver_landing_refs",
            deterministic_refs(
                self.solver_landing_refs,
                "solver_landing_refs",
                allow_empty=True,
            ),
        )
        if (
            self.maturity is VerticalCirculationMaturity.RESERVATION
            and self.solver_landing_refs
        ):
            raise VerticalCirculationError(
                "reservation cannot claim a solver landing denominator"
            )
        object.__setattr__(
            self,
            "component_refs",
            deterministic_refs(self.component_refs, "component_refs"),
        )
        if len(self.component_refs) != 1:
            raise VerticalCirculationError(
                "vertical-circulation contract must bind exactly one component"
            )
        if not isinstance(self.object_bindings, tuple) or any(
            not isinstance(item, VerticalCirculationObjectBinding)
            for item in self.object_bindings
        ):
            raise TypeError("object_bindings must contain VerticalCirculationObjectBinding")
        ordered_bindings = tuple(
            sorted(
                self.object_bindings,
                key=lambda item: (item.component_ref, item.object_ref, item.operation_ref),
            )
        )
        keys = tuple(
            (item.component_ref, item.object_ref, item.operation_ref)
            for item in ordered_bindings
        )
        if len(keys) != len(set(keys)) or (
            not ordered_bindings
            and self.maturity is not VerticalCirculationMaturity.RESERVATION
        ):
            raise VerticalCirculationError(
                "realized object bindings must be non-empty and unique"
            )
        if any(item.component_ref not in self.component_refs for item in ordered_bindings):
            raise VerticalCirculationError("object binding names an undeclared component")
        if len({item.object_ref for item in ordered_bindings}) != len(ordered_bindings):
            raise VerticalCirculationError("object_ref values must be one-to-one")
        if len({item.operation_ref for item in ordered_bindings}) != len(ordered_bindings):
            raise VerticalCirculationError("operation_ref values must be one-to-one")
        object.__setattr__(self, "object_bindings", ordered_bindings)
        if not isinstance(self.interface_host_bindings, tuple) or any(
            not isinstance(item, VerticalCirculationObjectBinding)
            for item in self.interface_host_bindings
        ):
            raise TypeError(
                "interface_host_bindings must contain "
                "VerticalCirculationObjectBinding"
            )
        ordered_hosts = tuple(
            sorted(
                self.interface_host_bindings,
                key=lambda item: (
                    item.component_ref,
                    item.object_ref,
                    item.operation_ref,
                ),
            )
        )
        host_keys = tuple(
            (item.component_ref, item.object_ref, item.operation_ref)
            for item in ordered_hosts
        )
        if len(host_keys) != len(set(host_keys)):
            raise VerticalCirculationError(
                "interface host bindings must be unique"
            )
        if any(item.component_ref in self.component_refs for item in ordered_hosts):
            raise VerticalCirculationError(
                "interface hosts must remain outside the circulation component denominator"
            )
        all_bindings = (*ordered_bindings, *ordered_hosts)
        if len({item.object_ref for item in all_bindings}) != len(all_bindings):
            raise VerticalCirculationError(
                "circulation and interface-host object refs must be one-to-one"
            )
        if len({item.operation_ref for item in all_bindings}) != len(all_bindings):
            raise VerticalCirculationError(
                "circulation and interface-host operation refs must be one-to-one"
            )
        object.__setattr__(self, "interface_host_bindings", ordered_hosts)
        if (
            self.maturity is VerticalCirculationMaturity.RESERVATION
            and (ordered_bindings or ordered_hosts)
        ):
            raise VerticalCirculationError(
                "reservation cannot claim realized object/operation bindings"
            )
        for field in ("length_unit_ref", "coordinate_frame_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        if not isinstance(self.up_axis, VerticalCirculationUpAxis):
            raise TypeError("up_axis must be VerticalCirculationUpAxis")
        if not isinstance(self.lower_interface, VerticalCirculationInterface):
            raise TypeError("lower_interface must be VerticalCirculationInterface")
        if self.lower_interface.role is not VerticalCirculationInterfaceRole.LOWER:
            raise VerticalCirculationError("lower_interface has the wrong role")
        if not isinstance(self.upper_interface, VerticalCirculationInterface):
            raise TypeError("upper_interface must be VerticalCirculationInterface")
        if self.upper_interface.role is not VerticalCirculationInterfaceRole.UPPER:
            raise VerticalCirculationError("upper_interface has the wrong role")
        for field in ("lower_landing_ownership", "upper_landing_ownership"):
            if not isinstance(getattr(self, field), LandingOwnership):
                raise TypeError(f"{field} must be LandingOwnership")
        if not isinstance(self.criteria, VerticalCirculationCriteria):
            raise TypeError("criteria must be VerticalCirculationCriteria")
        if self.geometry_witness is not None and not isinstance(
            self.geometry_witness, WalkingSurfaceGeometryWitness
        ):
            raise TypeError("geometry_witness must be WalkingSurfaceGeometryWitness or None")
        if self.aabb_precheck is not None and not isinstance(
            self.aabb_precheck, CirculationAabbNegativePrecheck
        ):
            raise TypeError("aabb_precheck must be CirculationAabbNegativePrecheck or None")
        if (
            self.aabb_precheck is not None
            and self.aabb_precheck.component_ref != self.component_refs[0]
        ):
            raise VerticalCirculationError(
                "AABB component_ref must equal the exact circulation component"
            )
        if self.assembly_witness is not None and not isinstance(
            self.assembly_witness, VerticalCirculationAssemblyWitness
        ):
            raise TypeError("assembly_witness must be VerticalCirculationAssemblyWitness or None")
        if self.maturity is VerticalCirculationMaturity.RESERVATION and (
            self.geometry_witness is not None or self.assembly_witness is not None
        ):
            raise VerticalCirculationError(
                "reservation cannot claim geometry or assembly witnesses"
            )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "evidence_refs", allow_empty=True),
        )

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"vertical-circulation-contract:{self.contract_id}"

    @property
    def binding_tuples(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(sorted(
            (*self.circulation_binding_tuples, *self.interface_host_binding_tuples)
        ))

    @property
    def circulation_binding_tuples(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (item.component_ref, item.object_ref, item.operation_ref)
            for item in self.object_bindings
        )

    @property
    def interface_host_binding_tuples(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (item.component_ref, item.object_ref, item.operation_ref)
            for item in self.interface_host_bindings
        )

    @property
    def surface_denominator_refs(self) -> tuple[str, ...]:
        """Exact geometry surfaces that a resolved-path check must cover."""

        if self.geometry_witness is None:
            return (self.ref,)
        refs = {
            self.ref,
            self.geometry_witness.ref,
            *self.solver_tread_refs,
            *self.solver_landing_refs,
            *(item.surface_ref for item in self.geometry_witness.path_samples),
            *(item.landing_ref for item in self.geometry_witness.landings),
            *(
                item.adjacency_ref
                for item in self.geometry_witness.surface_adjacencies
            ),
        }
        return tuple(sorted(refs))

    @property
    def subject_refs(self) -> tuple[str, ...]:
        refs = {
            self.ref,
            self.stage_subject_ref,
            *self.component_refs,
            *self.lower_interface.subject_refs,
            *self.upper_interface.subject_refs,
            *self.solver_tread_refs,
            *self.solver_landing_refs,
        }
        for binding in self.object_bindings:
            refs.update((binding.ref, binding.component_ref, binding.object_ref, binding.operation_ref))
        for binding in self.interface_host_bindings:
            refs.update(
                (
                    binding.ref,
                    binding.component_ref,
                    binding.object_ref,
                    binding.operation_ref,
                )
            )
        if self.geometry_witness is not None:
            refs.update((self.geometry_witness.ref, self.geometry_witness.extraction_ref))
            for sample in self.geometry_witness.path_samples:
                refs.update(
                    (
                        sample.sample_ref,
                        sample.surface_ref,
                        sample.component_ref,
                        sample.object_ref,
                        sample.operation_ref,
                    )
                )
                if sample.interface_ref is not None:
                    refs.add(sample.interface_ref)
                for value in (
                    sample.clear_width_witness_ref,
                    sample.headroom_witness_ref,
                    sample.solver_tread_ref,
                ):
                    if value is not None:
                        refs.add(value)
            refs.update(self.geometry_witness.solver_tread_refs)
            for adjacency in self.geometry_witness.surface_adjacencies:
                refs.update(
                    (
                        adjacency.adjacency_ref,
                        adjacency.from_sample_ref,
                        adjacency.to_sample_ref,
                        adjacency.from_surface_ref,
                        adjacency.to_surface_ref,
                    )
                )
                if adjacency.gap_witness_ref is not None:
                    refs.add(adjacency.gap_witness_ref)
            for landing in self.geometry_witness.landings:
                refs.update(
                    (
                        landing.landing_ref,
                        landing.surface_ref,
                        landing.component_ref,
                        landing.object_ref,
                        landing.operation_ref,
                    )
                )
                if landing.interface_ref is not None:
                    refs.add(landing.interface_ref)
                for value in (
                    landing.clear_depth_witness_ref,
                    landing.clear_width_witness_ref,
                ):
                    if value is not None:
                        refs.add(value)
        if self.aabb_precheck is not None:
            refs.add(self.aabb_precheck.source_ref)
        if self.assembly_witness is not None:
            assembly = self.assembly_witness
            refs.add(assembly.ref)
            if assembly.underpass_applicability_ref is not None:
                refs.add(assembly.underpass_applicability_ref)
            refs.update(assembly.underpass_assembly_denominator_refs)
            for receipt in (
                assembly.support_receipt,
                assembly.load_path_receipt,
                assembly.host_cut_receipt,
                assembly.underpass_receipt,
                assembly.underpass_assembly_receipt,
            ):
                if receipt is not None:
                    refs.add(_receipt_ref(receipt))
                    refs.update(receipt.subject_refs)
                    refs.update(receipt.coverage_denominator)
        return tuple(sorted(refs))

    @property
    def source_refs(self) -> tuple[str, ...]:
        refs = set(self.evidence_refs)
        refs.update(self.criteria.evidence_refs)
        if self.geometry_witness is not None:
            refs.add(self.geometry_witness.extraction_ref)
        if self.aabb_precheck is not None:
            refs.add(self.aabb_precheck.source_ref)
        if self.assembly_witness is not None:
            refs.update(self.assembly_witness.underpass_form_evidence_refs)
            for receipt in (
                self.assembly_witness.support_receipt,
                self.assembly_witness.load_path_receipt,
                self.assembly_witness.host_cut_receipt,
                self.assembly_witness.underpass_receipt,
                self.assembly_witness.underpass_assembly_receipt,
            ):
                if receipt is not None:
                    refs.update(receipt.source_refs)
        return tuple(sorted(refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "contract_id": self.contract_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_id": self.stage_id,
            "stage_subject_ref": self.stage_subject_ref,
            "stage_subject_digest": self.stage_subject_digest,
            "design_state_digest": self.design_state_digest,
            "maturity": self.maturity.value,
            "artifact_sha256": self.artifact_sha256,
            "program_digest": self.program_digest,
            "readback_digest": self.readback_digest,
            "solver_result_digest": self.solver_result_digest,
            "solver_tread_refs": list(self.solver_tread_refs),
            "solver_landing_refs": list(self.solver_landing_refs),
            "component_refs": list(self.component_refs),
            "object_bindings": [item.to_dict() for item in self.object_bindings],
            "interface_host_bindings": [
                item.to_dict() for item in self.interface_host_bindings
            ],
            "length_unit_ref": self.length_unit_ref,
            "coordinate_frame_ref": self.coordinate_frame_ref,
            "up_axis": self.up_axis.value,
            "lower_interface": self.lower_interface.to_dict(),
            "upper_interface": self.upper_interface.to_dict(),
            "lower_landing_ownership": self.lower_landing_ownership.value,
            "upper_landing_ownership": self.upper_landing_ownership.value,
            "criteria": self.criteria.to_dict(),
            "geometry_witness": (
                self.geometry_witness.to_dict() if self.geometry_witness is not None else None
            ),
            "aabb_precheck": (
                self.aabb_precheck.to_dict() if self.aabb_precheck is not None else None
            ),
            "assembly_witness": (
                self.assembly_witness.to_dict() if self.assembly_witness is not None else None
            ),
            "evidence_refs": list(self.evidence_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VerticalCirculationContract":
        payload = exact_mapping(
            value,
            {
                "schema",
                "contract_id",
                "branch",
                "scope_digest",
                "stage_id",
                "stage_subject_ref",
                "stage_subject_digest",
                "design_state_digest",
                "maturity",
                "artifact_sha256",
                "program_digest",
                "readback_digest",
                "solver_result_digest",
                "solver_tread_refs",
                "solver_landing_refs",
                "component_refs",
                "object_bindings",
                "interface_host_bindings",
                "length_unit_ref",
                "coordinate_frame_ref",
                "up_axis",
                "lower_interface",
                "upper_interface",
                "lower_landing_ownership",
                "upper_landing_ownership",
                "criteria",
                "geometry_witness",
                "aabb_precheck",
                "assembly_witness",
                "evidence_refs",
                *_AUTHORITY_FIELDS,
            },
            "vertical-circulation contract",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VerticalCirculationError("unsupported vertical-circulation contract schema")
        for field in (
            "solver_tread_refs",
            "solver_landing_refs",
            "component_refs",
            "object_bindings",
            "interface_host_bindings",
            "evidence_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        geometry = payload["geometry_witness"]
        aabb = payload["aabb_precheck"]
        assembly = payload["assembly_witness"]
        result = cls(
            contract_id=payload["contract_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_id=payload["stage_id"],
            stage_subject_ref=payload["stage_subject_ref"],
            stage_subject_digest=payload["stage_subject_digest"],
            design_state_digest=payload["design_state_digest"],
            maturity=VerticalCirculationMaturity(payload["maturity"]),
            artifact_sha256=payload["artifact_sha256"],
            program_digest=payload["program_digest"],
            readback_digest=payload["readback_digest"],
            solver_result_digest=payload["solver_result_digest"],
            solver_tread_refs=tuple(payload["solver_tread_refs"]),
            component_refs=tuple(payload["component_refs"]),
            object_bindings=tuple(
                VerticalCirculationObjectBinding.from_dict(item)
                for item in payload["object_bindings"]
            ),
            interface_host_bindings=tuple(
                VerticalCirculationObjectBinding.from_dict(item)
                for item in payload["interface_host_bindings"]
            ),
            length_unit_ref=payload["length_unit_ref"],
            coordinate_frame_ref=payload["coordinate_frame_ref"],
            up_axis=VerticalCirculationUpAxis(payload["up_axis"]),
            lower_interface=VerticalCirculationInterface.from_dict(payload["lower_interface"]),
            upper_interface=VerticalCirculationInterface.from_dict(payload["upper_interface"]),
            lower_landing_ownership=LandingOwnership(
                payload["lower_landing_ownership"]
            ),
            upper_landing_ownership=LandingOwnership(
                payload["upper_landing_ownership"]
            ),
            criteria=VerticalCirculationCriteria.from_dict(payload["criteria"]),
            geometry_witness=(
                WalkingSurfaceGeometryWitness.from_dict(geometry)
                if geometry is not None
                else None
            ),
            aabb_precheck=(
                CirculationAabbNegativePrecheck.from_dict(aabb) if aabb is not None else None
            ),
            assembly_witness=(
                VerticalCirculationAssemblyWitness.from_dict(assembly)
                if assembly is not None
                else None
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
            solver_landing_refs=tuple(payload["solver_landing_refs"]),
        )
        if result.to_dict() != payload:
            raise VerticalCirculationError("vertical-circulation contract identity changed")
        return result


def _finding(
    contract: VerticalCirculationContract,
    code: str,
    severity: FindingSeverity,
    message: str,
    *subject_refs: str,
) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=severity,
        message=message,
        subject_refs=tuple(sorted({contract.ref, *subject_refs})),
        evidence_refs=contract.source_refs,
    )


def _measurement(
    contract: VerticalCirculationContract,
    name: str,
    value: float | str,
    *,
    subject_ref: str | None = None,
    unit_ref: str | None = None,
) -> CheckMeasurement:
    target = subject_ref or contract.ref
    digest = canonical_digest(
        {"contract_ref": contract.ref, "subject_ref": target, "name": name, "value": value}
    )
    return CheckMeasurement(
        measurement_id=f"vertical-circulation-{digest[:24]}",
        subject_ref=target,
        name=name,
        value=value,
        unit_ref=unit_ref,
        evidence_refs=contract.source_refs,
    )


def _check_expected_interfaces(
    contract: VerticalCirculationContract,
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
) -> None:
    axis = _axis_index(contract.up_axis)
    lower = contract.lower_interface
    upper = contract.upper_interface
    for interface in (lower, upper):
        if not math.isclose(
            interface.point[axis],
            interface.datum,
            rel_tol=0.0,
            abs_tol=_EXACT_DATUM_TOLERANCE,
        ):
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-interface-datum-contradiction",
                    FindingSeverity.ERROR,
                    f"{interface.role.value} interface point disagrees with its exact datum fact",
                    interface.interface_ref,
                    interface.datum_fact_ref,
                )
            )
    if lower.datum >= upper.datum:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-interface-order-contradiction",
                FindingSeverity.ERROR,
                "lower interface datum must be below upper interface datum",
                lower.interface_ref,
                upper.interface_ref,
            )
        )
    measurements.append(
        _measurement(
            contract,
            "required_vertical_travel",
            upper.datum - lower.datum,
            unit_ref=contract.length_unit_ref,
        )
    )


def _check_aabb_negative_only(
    contract: VerticalCirculationContract,
    findings: list[CheckFinding],
) -> None:
    aabb = contract.aabb_precheck
    if aabb is None:
        return
    for interface in (contract.lower_interface, contract.upper_interface):
        if not aabb.contains(interface.point):
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-aabb-excludes-interface",
                    FindingSeverity.ERROR,
                    f"AABB excludes the exact {interface.role.value} interface point",
                    aabb.source_ref,
                    interface.interface_ref,
                )
            )


def _check_exact_witness_binding(
    contract: VerticalCirculationContract,
    witness: WalkingSurfaceGeometryWitness,
    findings: list[CheckFinding],
) -> None:
    comparisons: tuple[tuple[str, object, object], ...] = (
        ("branch", contract.branch, witness.branch),
        ("scope", contract.scope_digest, witness.scope_digest),
        ("stage", contract.stage_id, witness.stage_id),
        ("stage-subject-ref", contract.stage_subject_ref, witness.stage_subject_ref),
        (
            "stage-subject-digest",
            contract.stage_subject_digest,
            witness.stage_subject_digest,
        ),
        ("design-state", contract.design_state_digest, witness.design_state_digest),
        ("artifact", contract.artifact_sha256, witness.artifact_sha256),
        ("program", contract.program_digest, witness.program_digest),
        ("readback", contract.readback_digest, witness.readback_digest),
        ("solver-result", contract.solver_result_digest, witness.solver_result_digest),
        ("solver-tread-denominator", contract.solver_tread_refs, witness.solver_tread_refs),
        ("unit", contract.length_unit_ref, witness.length_unit_ref),
        ("coordinate-frame", contract.coordinate_frame_ref, witness.coordinate_frame_ref),
        ("up-axis", contract.up_axis, witness.up_axis),
        ("object-operation", contract.binding_tuples, witness.binding_tuples),
    )
    for label, expected, observed in comparisons:
        if expected != observed:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-binding-contradiction",
                    FindingSeverity.ERROR,
                    f"walking-surface witness crossed its exact {label} binding",
                    witness.ref,
                )
            )


def _check_sample_binding(
    contract: VerticalCirculationContract,
    sample: WalkingSurfaceSample,
    findings: list[CheckFinding],
) -> None:
    if sample.binding_tuple not in set(contract.binding_tuples):
        findings.append(
            _finding(
                contract,
                "vertical-circulation-sample-binding-contradiction",
                FindingSeverity.ERROR,
                "walking-surface sample names an object/operation outside the exact denominator",
                sample.sample_ref,
                sample.object_ref,
                sample.operation_ref,
            )
        )


def _check_observed_limit(
    contract: VerticalCirculationContract,
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
    *,
    subject_ref: str,
    name: str,
    observed: float | None,
    minimum: float | None,
    witness_ref: str | None,
) -> None:
    if observed is None:
        findings.append(
            _finding(
                contract,
                f"vertical-circulation-{name}-witness-unknown",
                FindingSeverity.UNKNOWN,
                f"{name} has no geometry-derived observation",
                subject_ref,
            )
        )
        return
    if witness_ref is None:
        findings.append(
            _finding(
                contract,
                f"vertical-circulation-{name}-derivation-witness-unknown",
                FindingSeverity.UNKNOWN,
                f"{name} is not bound to a geometry-derived observation",
                subject_ref,
            )
        )
        return
    measurements.append(
        _measurement(
            contract,
            name,
            observed,
            subject_ref=subject_ref,
            unit_ref=contract.length_unit_ref,
        )
    )
    if minimum is None:
        return
    if observed < minimum:
        findings.append(
            _finding(
                contract,
                f"vertical-circulation-{name}-contradiction",
                FindingSeverity.ERROR,
                f"observed {name} {observed} is below adopted minimum {minimum}",
                subject_ref,
                witness_ref,
            )
        )


def _check_observed_maximum(
    contract: VerticalCirculationContract,
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
    *,
    subject_ref: str,
    name: str,
    observed: float | None,
    maximum: float | None,
    witness_ref: str | None,
) -> None:
    if observed is None:
        findings.append(
            _finding(
                contract,
                f"vertical-circulation-{name}-witness-unknown",
                FindingSeverity.UNKNOWN,
                f"{name} has no geometry-derived observation",
                subject_ref,
            )
        )
        return
    if witness_ref is None:
        findings.append(
            _finding(
                contract,
                f"vertical-circulation-{name}-derivation-witness-unknown",
                FindingSeverity.UNKNOWN,
                f"{name} is not bound to a geometry-derived observation",
                subject_ref,
            )
        )
        return
    measurements.append(
        _measurement(
            contract,
            name,
            observed,
            subject_ref=subject_ref,
            unit_ref=contract.length_unit_ref,
        )
    )
    if maximum is not None and observed > maximum:
        findings.append(
            _finding(
                contract,
                f"vertical-circulation-{name}-contradiction",
                FindingSeverity.ERROR,
                f"observed {name} {observed} exceeds adopted maximum {maximum}",
                subject_ref,
                witness_ref,
            )
        )


def _check_generic_surface_continuity(
    contract: VerticalCirculationContract,
    witness: WalkingSurfaceGeometryWitness,
    findings: list[CheckFinding],
) -> None:
    """Replay the project-neutral continuity checker over actual CAD surfaces."""

    samples = witness.path_samples
    if len(samples) < 2:
        return
    surface_refs = tuple(item.surface_ref for item in samples)
    if len(surface_refs) != len(set(surface_refs)):
        findings.append(
            _finding(
                contract,
                "vertical-circulation-surface-denominator-duplicate",
                FindingSeverity.ERROR,
                "ordered walking path repeats an actual CAD surface",
                witness.ref,
                *surface_refs,
            )
        )
        return
    axis = _axis_index(contract.up_axis)
    nodes = tuple(
        GenericWalkingSurfaceNode(
            node_ref=sample.surface_ref,
            role=(
                GenericWalkingSurfaceNodeRole.EXTERIOR
                if ordinal == 0
                else GenericWalkingSurfaceNodeRole.INTERIOR
                if ordinal == len(samples) - 1
                else GenericWalkingSurfaceNodeRole.TRANSITION
            ),
            datum=sample.point[axis],
            evidence_refs=(witness.extraction_ref, sample.sample_ref),
        )
        for ordinal, sample in enumerate(samples)
    )
    edges = tuple(
        GenericWalkingSurfaceEdge(
            edge_ref=item.adjacency_ref,
            from_node_ref=item.from_surface_ref,
            to_node_ref=item.to_surface_ref,
            kind=GenericWalkingSurfaceEdgeKind.STEP,
            evidence_refs=tuple(
                sorted(
                    {
                        witness.extraction_ref,
                        *(
                            (item.gap_witness_ref,)
                            if item.gap_witness_ref is not None
                            else ()
                        ),
                    }
                )
            ),
        )
        for item in witness.surface_adjacencies
        if item.from_surface_ref != item.to_surface_ref
    )
    profile = WalkingSurfaceContinuityProfile(
        profile_id=f"{contract.contract_id}-resolved-path",
        nodes=nodes,
        edges=edges,
        paths=(
            GenericWalkingSurfacePathRequirement(
                path_id=f"{contract.contract_id}-surface-path",
                node_refs=surface_refs,
                evidence_refs=(witness.extraction_ref,),
            ),
        ),
        criteria=GenericWalkingSurfaceCriteria(
            max_continuous_delta=contract.criteria.max_surface_gap,
            max_step_rise=contract.criteria.max_vertical_step,
            max_ramp_slope=None,
            max_threshold_rise=None,
        ),
        length_unit_ref=contract.length_unit_ref,
    )
    receipt = check_walking_surface_continuity(
        profile,
        branch=contract.branch,
        scope_digest=contract.scope_digest,
        stage_subject_digest=contract.stage_subject_digest,
    )
    if receipt.status is CheckStatus.FAIL:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-generic-surface-continuity-contradiction",
                FindingSeverity.ERROR,
                "project-neutral walking-surface continuity replay failed",
                witness.ref,
                *surface_refs,
            )
        )
    elif receipt.status is not CheckStatus.PASS:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-generic-surface-continuity-unknown",
                FindingSeverity.UNKNOWN,
                "project-neutral walking-surface continuity replay is incomplete",
                witness.ref,
                *surface_refs,
            )
        )


def _check_geometry_witness(
    contract: VerticalCirculationContract,
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
) -> None:
    witness = contract.geometry_witness
    if witness is None:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-walking-surface-witness-unknown",
                FindingSeverity.UNKNOWN,
                "resolved circulation requires a non-AABB walking-surface witness",
            )
        )
        return
    _check_exact_witness_binding(contract, witness, findings)
    _check_generic_surface_continuity(contract, witness, findings)
    samples = witness.path_samples
    axis = _axis_index(contract.up_axis)
    tolerance = contract.criteria.endpoint_tolerance
    circulation_bindings = set(contract.circulation_binding_tuples)
    host_bindings = set(contract.interface_host_binding_tuples)

    if len(samples) < 3:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-path-witness-incomplete",
                FindingSeverity.UNKNOWN,
                "resolved path requires terminal surfaces and solver-tread samples",
                witness.ref,
            )
        )
    if samples:
        if samples[0].role is not WalkingSurfaceSampleRole.LOWER_INTERFACE:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-lower-path-role-contradiction",
                    FindingSeverity.ERROR,
                    "the first ordered walking surface is not the lower interface",
                    samples[0].sample_ref,
                )
            )
        if samples[-1].role is not WalkingSurfaceSampleRole.UPPER_INTERFACE:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-upper-path-role-contradiction",
                    FindingSeverity.ERROR,
                    "the last ordered walking surface is not the upper interface",
                    samples[-1].sample_ref,
                )
            )

    endpoint_samples: dict[LandingRole, WalkingSurfaceSample] = {}
    for role, sample_role, interface in (
        (
            LandingRole.LOWER,
            WalkingSurfaceSampleRole.LOWER_INTERFACE,
            contract.lower_interface,
        ),
        (
            LandingRole.UPPER,
            WalkingSurfaceSampleRole.UPPER_INTERFACE,
            contract.upper_interface,
        ),
    ):
        candidates = [item for item in samples if item.role is sample_role]
        if not candidates:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-interface-surface-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    f"{role.value} interface has no ordered surface sample",
                    interface.interface_ref,
                    witness.ref,
                )
            )
            continue
        if len(candidates) > 1:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-interface-surface-ambiguous",
                    FindingSeverity.ERROR,
                    f"{role.value} interface occurs more than once in the path",
                    interface.interface_ref,
                    *(item.sample_ref for item in candidates),
                )
            )
            continue
        sample = candidates[0]
        endpoint_samples[role] = sample
        if sample.interface_ref is None:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-endpoint-interface-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    f"{role.value} walking surface lacks an interface witness",
                    sample.sample_ref,
                    interface.interface_ref,
                )
            )
        elif sample.interface_ref != interface.interface_ref:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-endpoint-interface-contradiction",
                    FindingSeverity.ERROR,
                    f"{role.value} walking surface binds the wrong interface",
                    sample.sample_ref,
                    sample.interface_ref,
                    interface.interface_ref,
                )
            )
        if tolerance is not None and _distance(sample.point, interface.point) > tolerance:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-endpoint-position-contradiction",
                    FindingSeverity.ERROR,
                    f"{role.value} walking surface misses its exact interface",
                    sample.sample_ref,
                    interface.interface_ref,
                )
            )

    allowed_interfaces = {
        contract.lower_interface.interface_ref,
        contract.upper_interface.interface_ref,
    }
    for sample in samples:
        _check_sample_binding(contract, sample, findings)
        if sample.interface_ref is not None and sample.interface_ref not in allowed_interfaces:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-foreign-interface-contradiction",
                    FindingSeverity.ERROR,
                    "walking-surface sample binds an undeclared interface",
                    sample.sample_ref,
                    sample.interface_ref,
                )
            )
        if (
            sample.role is WalkingSurfaceSampleRole.TREAD
            and sample.binding_tuple not in circulation_bindings
        ):
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-tread-owner-contradiction",
                    FindingSeverity.ERROR,
                    "a solver tread is not owned by the stair assembly",
                    sample.sample_ref,
                )
            )
        if sample.role is WalkingSurfaceSampleRole.TREAD:
            if sample.solver_tread_ref is None:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-solver-tread-binding-unknown",
                        FindingSeverity.UNKNOWN,
                        "tread surface is not bound to a solver tread",
                        sample.sample_ref,
                    )
                )
        elif sample.solver_tread_ref is not None:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-non-tread-solver-binding-contradiction",
                    FindingSeverity.ERROR,
                    "a non-tread walking surface claims a solver tread",
                    sample.sample_ref,
                    sample.solver_tread_ref,
                )
            )
        _check_observed_limit(
            contract,
            findings,
            measurements,
            subject_ref=sample.sample_ref,
            name="clear_width",
            observed=sample.clear_width,
            minimum=contract.criteria.min_clear_width,
            witness_ref=sample.clear_width_witness_ref,
        )
        _check_observed_limit(
            contract,
            findings,
            measurements,
            subject_ref=sample.sample_ref,
            name="headroom",
            observed=sample.headroom,
            minimum=contract.criteria.min_headroom,
            witness_ref=sample.headroom_witness_ref,
        )

    observed_treads = tuple(
        item.solver_tread_ref
        for item in samples
        if item.solver_tread_ref is not None
    )
    if len(observed_treads) != len(set(observed_treads)):
        findings.append(
            _finding(
                contract,
                "vertical-circulation-solver-tread-duplicate",
                FindingSeverity.ERROR,
                "a solver tread is covered more than once",
                witness.ref,
                *observed_treads,
            )
        )
    missing_treads = set(witness.solver_tread_refs) - set(observed_treads)
    foreign_treads = set(observed_treads) - set(witness.solver_tread_refs)
    if missing_treads:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-solver-tread-coverage-unknown",
                FindingSeverity.UNKNOWN,
                "one or more solver treads lack a geometry-derived surface sample",
                witness.ref,
                *missing_treads,
            )
        )
    if foreign_treads:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-foreign-solver-tread-contradiction",
                FindingSeverity.ERROR,
                "walking-surface witness names a tread outside the bound solver result",
                witness.ref,
                *foreign_treads,
            )
        )

    observed_landing_refs = {
        item.landing_ref for item in witness.landings
    }
    missing_solver_landings = (
        set(contract.solver_landing_refs) - observed_landing_refs
    )
    if missing_solver_landings:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-solver-landing-coverage-unknown",
                FindingSeverity.UNKNOWN,
                "one or more solver-owned landings lack a geometry-derived "
                "landing witness",
                witness.ref,
                *missing_solver_landings,
            )
        )

    expected_adjacency_count = max(0, len(samples) - 1)
    if len(witness.surface_adjacencies) < expected_adjacency_count:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-surface-adjacency-witness-unknown",
                FindingSeverity.UNKNOWN,
                "ordered walking surfaces lack a complete adjacency chain",
                witness.ref,
            )
        )
    elif len(witness.surface_adjacencies) > expected_adjacency_count:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-surface-adjacency-ambiguous",
                FindingSeverity.ERROR,
                "walking-surface adjacency chain has extra edges",
                witness.ref,
            )
        )
    for ordinal, (previous, current) in enumerate(zip(samples, samples[1:])):
        if ordinal >= len(witness.surface_adjacencies):
            break
        adjacency = witness.surface_adjacencies[ordinal]
        expected_edge = (
            previous.sample_ref,
            current.sample_ref,
            previous.surface_ref,
            current.surface_ref,
        )
        observed_edge = (
            adjacency.from_sample_ref,
            adjacency.to_sample_ref,
            adjacency.from_surface_ref,
            adjacency.to_surface_ref,
        )
        if observed_edge != expected_edge:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-surface-adjacency-contradiction",
                    FindingSeverity.ERROR,
                    "surface adjacency does not follow the ordered path",
                    adjacency.adjacency_ref,
                    previous.sample_ref,
                    current.sample_ref,
                )
            )
        _check_observed_maximum(
            contract,
            findings,
            measurements,
            subject_ref=adjacency.adjacency_ref,
            name="surface_gap",
            observed=adjacency.surface_gap,
            maximum=contract.criteria.max_surface_gap,
            witness_ref=adjacency.gap_witness_ref,
        )
        vertical_step = abs(current.point[axis] - previous.point[axis])
        horizontal_step = math.sqrt(
            sum(
                (current.point[index] - previous.point[index]) ** 2
                for index in range(3)
                if index != axis
            )
        )
        _check_observed_maximum(
            contract,
            findings,
            measurements,
            subject_ref=adjacency.adjacency_ref,
            name="horizontal_step",
            observed=horizontal_step,
            maximum=contract.criteria.max_horizontal_step,
            witness_ref=witness.extraction_ref,
        )
        _check_observed_maximum(
            contract,
            findings,
            measurements,
            subject_ref=adjacency.adjacency_ref,
            name="vertical_step",
            observed=vertical_step,
            maximum=contract.criteria.max_vertical_step,
            witness_ref=witness.extraction_ref,
        )
        if current.point[axis] + (tolerance or 0.0) < previous.point[axis]:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-path-direction-contradiction",
                    FindingSeverity.ERROR,
                    "lower-to-upper walking-surface witness reverses vertical direction",
                    previous.sample_ref,
                    current.sample_ref,
                )
            )

    by_role: dict[LandingRole, list[LandingWitness]] = {}
    for landing in witness.landings:
        by_role.setdefault(landing.role, []).append(landing)
        expected_bindings = (
            circulation_bindings
            if landing.ownership is LandingOwnership.STAIR_ASSEMBLY
            else host_bindings
        )
        if landing.binding_tuple not in expected_bindings:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-landing-owner-binding-contradiction",
                    FindingSeverity.ERROR,
                    "landing geometry is not bound to its declared sole owner",
                    landing.landing_ref,
                    landing.object_ref,
                    landing.operation_ref,
                )
            )
        if (
            landing.role is LandingRole.INTERMEDIATE
            and landing.ownership is not LandingOwnership.STAIR_ASSEMBLY
        ):
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-intermediate-landing-owner-contradiction",
                    FindingSeverity.ERROR,
                    "an intermediate landing cannot be owned by an endpoint host",
                    landing.landing_ref,
                )
            )
        _check_observed_limit(
            contract,
            findings,
            measurements,
            subject_ref=landing.landing_ref,
            name="landing_depth",
            observed=landing.clear_depth,
            minimum=contract.criteria.min_landing_depth,
            witness_ref=landing.clear_depth_witness_ref,
        )
        _check_observed_limit(
            contract,
            findings,
            measurements,
            subject_ref=landing.landing_ref,
            name="landing_width",
            observed=landing.clear_width,
            minimum=contract.criteria.min_clear_width,
            witness_ref=landing.clear_width_witness_ref,
        )

    for role, interface, expected_ownership in (
        (
            LandingRole.LOWER,
            contract.lower_interface,
            contract.lower_landing_ownership,
        ),
        (
            LandingRole.UPPER,
            contract.upper_interface,
            contract.upper_landing_ownership,
        ),
    ):
        candidates = by_role.get(role, [])
        if not candidates:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-interface-landing-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    f"{role.value} interface has no landing witness",
                    interface.interface_ref,
                    witness.ref,
                )
            )
            continue
        if len(candidates) > 1:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-interface-landing-ambiguous",
                    FindingSeverity.ERROR,
                    f"{role.value} interface has multiple landing witnesses",
                    interface.interface_ref,
                    *(item.landing_ref for item in candidates),
                )
            )
            continue
        landing = candidates[0]
        if landing.ownership is not expected_ownership:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-landing-ownership-contradiction",
                    FindingSeverity.ERROR,
                    f"{role.value} landing contradicts the contract ownership",
                    landing.landing_ref,
                    interface.interface_ref,
                )
            )
        if landing.interface_ref is None:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-landing-interface-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    f"{role.value} landing lacks an interface witness",
                    landing.landing_ref,
                    interface.interface_ref,
                )
            )
        elif landing.interface_ref != interface.interface_ref:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-landing-interface-contradiction",
                    FindingSeverity.ERROR,
                    f"{role.value} landing binds the wrong interface",
                    landing.landing_ref,
                    landing.interface_ref,
                    interface.interface_ref,
                )
            )
        if tolerance is not None and _distance(landing.point, interface.point) > tolerance:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-landing-position-contradiction",
                    FindingSeverity.ERROR,
                    f"{role.value} landing misses its exact interface",
                    landing.landing_ref,
                    interface.interface_ref,
                )
            )
        endpoint_sample = endpoint_samples.get(role)
        if endpoint_sample is not None and (
            endpoint_sample.surface_ref != landing.surface_ref
            or endpoint_sample.binding_tuple != landing.binding_tuple
            or (
                tolerance is not None
                and _distance(endpoint_sample.point, landing.point) > tolerance
            )
        ):
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-interface-continuity-contradiction",
                    FindingSeverity.ERROR,
                    f"{role.value} path endpoint and landing are not the same surface",
                    endpoint_sample.sample_ref,
                    landing.landing_ref,
                    interface.interface_ref,
                )
            )


def _check_assembly(
    contract: VerticalCirculationContract,
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
) -> None:
    witness = contract.assembly_witness
    if witness is None:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-assembly-witness-unknown",
                FindingSeverity.UNKNOWN,
                "assembly maturity requires support, load-path, and host-cut witnesses",
            )
        )
        return
    context_comparisons: tuple[tuple[str, object, object], ...] = (
        ("branch", contract.branch, witness.branch),
        ("scope", contract.scope_digest, witness.scope_digest),
        ("stage", contract.stage_id, witness.stage_id),
        ("stage-subject-ref", contract.stage_subject_ref, witness.stage_subject_ref),
        (
            "stage-subject-digest",
            contract.stage_subject_digest,
            witness.stage_subject_digest,
        ),
        ("design-state", contract.design_state_digest, witness.design_state_digest),
        ("artifact", contract.artifact_sha256, witness.artifact_sha256),
        ("program", contract.program_digest, witness.program_digest),
        ("readback", contract.readback_digest, witness.readback_digest),
        ("solver-result", contract.solver_result_digest, witness.solver_result_digest),
    )
    for label, expected, observed in context_comparisons:
        if expected != observed:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-assembly-{label}-binding-contradiction",
                    FindingSeverity.ERROR,
                    f"assembly witness crossed its exact {label} binding",
                    witness.ref,
                )
            )
    required_receipts = (
        (
            "support",
            witness.support_receipt,
            VERTICAL_CIRCULATION_SUPPORT_CHECKER_ID,
        ),
        (
            "load_path",
            witness.load_path_receipt,
            VERTICAL_CIRCULATION_LOAD_PATH_CHECKER_ID,
        ),
        (
            "host_cut",
            witness.host_cut_receipt,
            VERTICAL_CIRCULATION_HOST_CUT_CHECKER_ID,
        ),
    )
    for label, receipt, expected_checker_id in required_receipts:
        if receipt is None:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-receipt-unknown",
                    FindingSeverity.UNKNOWN,
                    f"assembly {label} lacks a typed replayable check receipt",
                    witness.ref,
                )
            )
            continue
        # Parsing the canonical form replays the common receipt invariants:
        # status/findings consistency, exact coverage, and zero authority.
        replayed = CheckReceiptEnvelope.from_dict(receipt.to_dict())
        if replayed != receipt:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-receipt-identity-contradiction",
                    FindingSeverity.ERROR,
                    f"assembly {label} receipt changed during typed replay",
                    _receipt_ref(receipt),
                )
            )
        comparisons: tuple[tuple[str, object, object], ...] = (
            ("checker", expected_checker_id, receipt.checker_id),
            ("branch", contract.branch, receipt.branch),
            ("scope", contract.scope_digest, receipt.scope_digest),
            ("subject", contract.stage_subject_digest, receipt.subject_digest),
        )
        for binding_label, expected, observed in comparisons:
            if expected != observed:
                findings.append(
                    _finding(
                        contract,
                        f"vertical-circulation-{label}-receipt-{binding_label}-contradiction",
                        FindingSeverity.ERROR,
                        f"assembly {label} receipt crossed its exact {binding_label} binding",
                        _receipt_ref(receipt),
                    )
                )
        required_subjects = {
            contract.stage_subject_ref,
            *contract.component_refs,
            *(set(contract.surface_denominator_refs) - {contract.ref}),
        }
        if not required_subjects <= set(receipt.subject_refs):
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-surface-denominator-contradiction",
                    FindingSeverity.ERROR,
                    f"assembly {label} receipt is not bound to the actual walking-surface denominator",
                    _receipt_ref(receipt),
                    *required_subjects,
                )
            )
        required_surface_denominator = set(contract.surface_denominator_refs) - {
            contract.ref
        }
        if not required_surface_denominator <= set(receipt.coverage_denominator):
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-surface-coverage-contradiction",
                    FindingSeverity.ERROR,
                    f"assembly {label} receipt does not cover the actual walking surfaces",
                    _receipt_ref(receipt),
                    *required_surface_denominator,
                )
            )
        if not receipt.coverage_denominator:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-denominator-unknown",
                    FindingSeverity.UNKNOWN,
                    f"assembly {label} receipt has no checked denominator",
                    _receipt_ref(receipt),
                )
            )
        if receipt.status is CheckStatus.FAIL:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-contradiction",
                    FindingSeverity.ERROR,
                    f"typed assembly {label} receipt failed",
                    _receipt_ref(receipt),
                )
            )
        elif receipt.status is not CheckStatus.PASS:
            findings.append(
                _finding(
                    contract,
                    f"vertical-circulation-{label}-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    f"typed assembly {label} receipt is not passing",
                    _receipt_ref(receipt),
                )
            )

    if witness.underpass_applicable is None:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-underpass-applicability-unknown",
                FindingSeverity.UNKNOWN,
                "underpass applicability must be resolved for assembly maturity",
                witness.ref,
            )
        )
    elif witness.underpass_applicability_ref is None:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-underpass-applicability-binding-unknown",
                FindingSeverity.UNKNOWN,
                "underpass applicability lacks an exact state/policy binding",
                witness.ref,
            )
        )
    elif witness.underpass_applicable:
        minimum = contract.criteria.min_underpass_clearance
        if minimum is None:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-underpass-criterion-unknown",
                    FindingSeverity.UNKNOWN,
                    "applicable underpass has no adopted clearance criterion",
                    witness.underpass_applicability_ref,
                )
            )
        receipt = witness.underpass_receipt
        if receipt is None:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-underpass-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    "applicable underpass lacks a typed replayable clearance receipt",
                    witness.underpass_applicability_ref,
                )
            )
        else:
            replayed = CheckReceiptEnvelope.from_dict(receipt.to_dict())
            if replayed != receipt:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-receipt-identity-contradiction",
                        FindingSeverity.ERROR,
                        "underpass receipt changed during typed replay",
                        _receipt_ref(receipt),
                    )
                )
            for label, expected, observed in (
                ("checker", VERTICAL_CIRCULATION_UNDERPASS_CHECKER_ID, receipt.checker_id),
                ("branch", contract.branch, receipt.branch),
                ("scope", contract.scope_digest, receipt.scope_digest),
                ("subject", contract.stage_subject_digest, receipt.subject_digest),
            ):
                if expected != observed:
                    findings.append(
                        _finding(
                            contract,
                            f"vertical-circulation-underpass-receipt-{label}-contradiction",
                            FindingSeverity.ERROR,
                            f"underpass receipt crossed its exact {label} binding",
                            _receipt_ref(receipt),
                        )
                    )
            required_subjects = {
                contract.stage_subject_ref,
                *contract.component_refs,
                *(set(contract.surface_denominator_refs) - {contract.ref}),
            }
            if not required_subjects <= set(receipt.subject_refs):
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-surface-denominator-contradiction",
                        FindingSeverity.ERROR,
                        "underpass receipt is not bound to the actual walking-surface denominator",
                        _receipt_ref(receipt),
                        *required_subjects,
                    )
                )
            required_surface_denominator = set(contract.surface_denominator_refs) - {
                contract.ref
            }
            if not required_surface_denominator <= set(
                receipt.coverage_denominator
            ):
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-surface-coverage-contradiction",
                        FindingSeverity.ERROR,
                        "underpass receipt does not cover the actual walking surfaces",
                        _receipt_ref(receipt),
                        *required_surface_denominator,
                    )
                )
            if receipt.status is CheckStatus.FAIL:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-check-contradiction",
                        FindingSeverity.ERROR,
                        "typed underpass clearance receipt failed",
                        _receipt_ref(receipt),
                    )
                )
            elif receipt.status is not CheckStatus.PASS:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-check-unknown",
                        FindingSeverity.UNKNOWN,
                        "typed underpass clearance receipt is not passing",
                        _receipt_ref(receipt),
                    )
                )
            clearance_measurements = tuple(
                item
                for item in receipt.measurements
                if item.name == "underpass_clearance"
            )
            if len(clearance_measurements) != 1:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-clearance-witness-unknown",
                        FindingSeverity.UNKNOWN,
                        "underpass receipt must carry exactly one clearance measurement",
                        _receipt_ref(receipt),
                    )
                )
            else:
                clearance = clearance_measurements[0]
                observed = clearance.value
                if (
                    not isinstance(observed, (int, float))
                    or isinstance(observed, bool)
                ):
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-clearance-type-contradiction",
                            FindingSeverity.ERROR,
                            "underpass clearance measurement is not numeric",
                            _receipt_ref(receipt),
                            clearance.subject_ref,
                        )
                    )
                else:
                    _check_observed_limit(
                        contract,
                        findings,
                        measurements,
                        subject_ref=_receipt_ref(receipt),
                        name="underpass_clearance",
                        observed=float(observed),
                        minimum=minimum,
                        witness_ref=clearance.subject_ref,
                    )

        if witness.underpass_form is UnderpassConstructionForm.UNKNOWN:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-underpass-form-unknown",
                    FindingSeverity.UNKNOWN,
                    "clearance does not resolve the underpass construction form",
                    witness.underpass_applicability_ref,
                )
            )
        elif witness.underpass_form is UnderpassConstructionForm.VAULTED:
            if not witness.underpass_form_evidence_refs:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-form-evidence-unknown",
                        FindingSeverity.UNKNOWN,
                        "selected vaulted form lacks retained evidence",
                        witness.underpass_applicability_ref,
                    )
                )
            if not witness.underpass_form_adoption_refs:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-form-adoption-unknown",
                        FindingSeverity.UNKNOWN,
                        "selected vaulted form lacks an adoption decision",
                        witness.underpass_applicability_ref,
                    )
                )
            underpass_contract = witness.underpass_assembly_contract
            assembly_receipt = witness.underpass_assembly_receipt
            if underpass_contract is None or assembly_receipt is None:
                findings.append(
                    _finding(
                        contract,
                        "vertical-circulation-underpass-assembly-unknown",
                        FindingSeverity.UNKNOWN,
                        "selected vault lacks a replayable contract and receipt",
                        witness.underpass_applicability_ref,
                    )
                )
            else:
                replayed_receipt = check_vaulted_underpass_assembly(
                    underpass_contract
                )
                if (
                    underpass_contract.construction_form
                    is not witness.underpass_form
                    or underpass_contract.form_evidence_refs
                    != witness.underpass_form_evidence_refs
                    or underpass_contract.form_adoption_refs
                    != witness.underpass_form_adoption_refs
                ):
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-contract-selection-contradiction",
                            FindingSeverity.ERROR,
                            "vaulted contract differs from the selected form evidence",
                            underpass_contract.ref,
                        )
                    )
                readback_profile = underpass_contract.cad_readback_profile
                readback_snapshot = underpass_contract.cad_readback_snapshot
                if readback_profile is not None and (
                    readback_profile.stage_id != contract.stage_id
                    or readback_profile.program_digest != contract.program_digest
                ):
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-readback-profile-contradiction",
                            FindingSeverity.ERROR,
                            "underpass CAD profile crossed current stage or program",
                            underpass_contract.ref,
                            readback_profile.ref,
                        )
                    )
                if readback_snapshot is not None and (
                    readback_snapshot.stage_id != contract.stage_id
                    or readback_snapshot.program_digest != contract.program_digest
                ):
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-readback-snapshot-contradiction",
                            FindingSeverity.ERROR,
                            "underpass CAD snapshot crossed current stage or program",
                            underpass_contract.ref,
                        )
                    )
                if replayed_receipt.receipt_digest != assembly_receipt.receipt_digest:
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-assembly-replay-contradiction",
                            FindingSeverity.ERROR,
                            "supplied receipt differs from deterministic contract replay",
                            _receipt_ref(assembly_receipt),
                            underpass_contract.ref,
                        )
                    )
                for label, expected, observed in (
                    (
                        "checker",
                        UNDERPASS_ASSEMBLY_CHECKER_ID,
                        assembly_receipt.checker_id,
                    ),
                    ("branch", contract.branch, assembly_receipt.branch),
                    ("scope", contract.scope_digest, assembly_receipt.scope_digest),
                    (
                        "subject",
                        contract.stage_subject_digest,
                        assembly_receipt.subject_digest,
                    ),
                ):
                    if expected != observed:
                        findings.append(
                            _finding(
                                contract,
                                "vertical-circulation-underpass-assembly-"
                                f"{label}-contradiction",
                                FindingSeverity.ERROR,
                                "vaulted-underpass receipt crossed its exact "
                                f"{label} binding",
                                _receipt_ref(assembly_receipt),
                            )
                        )
                required_context = {
                    witness.underpass_applicability_ref,
                    *(
                        set(contract.surface_denominator_refs)
                        - {contract.ref}
                    ),
                }
                if set(underpass_contract.context_refs) != required_context:
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-context-contradiction",
                            FindingSeverity.ERROR,
                            "vaulted contract does not exactly bind current circulation context",
                            underpass_contract.ref,
                            *required_context,
                        )
                    )
                if not required_context <= set(
                    assembly_receipt.subject_refs
                ) or not required_context <= set(
                    assembly_receipt.coverage_denominator
                ):
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-context-coverage-contradiction",
                            FindingSeverity.ERROR,
                            "vaulted receipt omits applicability or walking surfaces",
                            _receipt_ref(assembly_receipt),
                            *required_context,
                        )
                    )
                if not set(witness.underpass_form_evidence_refs) <= set(
                    assembly_receipt.source_refs
                ) or not set(witness.underpass_form_adoption_refs) <= set(
                    assembly_receipt.adoption_refs
                ):
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-selection-binding-contradiction",
                            FindingSeverity.ERROR,
                            "vaulted receipt omits selected evidence or adoption",
                            _receipt_ref(assembly_receipt),
                        )
                    )
                if replayed_receipt.status is CheckStatus.FAIL:
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-assembly-contradiction",
                            FindingSeverity.ERROR,
                            "typed vaulted-underpass materialization check failed",
                            _receipt_ref(assembly_receipt),
                        )
                    )
                elif replayed_receipt.status is not CheckStatus.PASS:
                    findings.append(
                        _finding(
                            contract,
                            "vertical-circulation-underpass-assembly-witness-unknown",
                            FindingSeverity.UNKNOWN,
                            "typed vaulted-underpass materialization check is not passing",
                            _receipt_ref(assembly_receipt),
                        )
                    )
        else:
            findings.append(
                _finding(
                    contract,
                    "vertical-circulation-underpass-form-checker-unknown",
                    FindingSeverity.UNKNOWN,
                    "selected underpass form has no bound materialization checker",
                    witness.underpass_applicability_ref,
                )
            )
    elif any(
        (
            witness.underpass_receipt is not None,
            witness.underpass_form is not UnderpassConstructionForm.UNKNOWN,
            bool(witness.underpass_form_evidence_refs),
            bool(witness.underpass_form_adoption_refs),
            witness.underpass_assembly_contract is not None,
            witness.underpass_assembly_receipt is not None,
        )
    ):
        findings.append(
            _finding(
                contract,
                "vertical-circulation-underpass-nonapplicable-receipt-contradiction",
                FindingSeverity.ERROR,
                "non-applicable underpass unexpectedly carries form or check evidence",
                witness.ref,
            )
        )
def check_vertical_circulation(
    contract: VerticalCirculationContract,
) -> CheckReceiptEnvelope:
    """Validate one exact vertical-circulation maturity contract.

    ``RESERVATION`` always remains ``UNKNOWN`` for walkability.  Only a
    geometry-derived, non-AABB surface witness can satisfy
    ``RESOLVED_PATH``; ``ASSEMBLY`` additionally requires independent support,
    load-path, host-cut, and applicable underpass evidence.
    """

    if not isinstance(contract, VerticalCirculationContract):
        raise TypeError("contract must be VerticalCirculationContract")
    findings: list[CheckFinding] = []
    measurements: list[CheckMeasurement] = []
    _check_expected_interfaces(contract, findings, measurements)
    _check_aabb_negative_only(contract, findings)
    if contract.aabb_precheck is None:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-reservation-envelope-witness-unknown",
                FindingSeverity.UNKNOWN,
                "vertical circulation requires a typed reservation envelope/AABB",
            )
        )

    if contract.maturity is VerticalCirculationMaturity.RESERVATION:
        findings.append(
            _finding(
                contract,
                "vertical-circulation-reservation-not-walkable",
                FindingSeverity.UNKNOWN,
                "reservation proves space allocation only and cannot pass walkability",
            )
        )
    else:
        for name, value in contract.criteria.core_values:
            if value is None:
                findings.append(
                    _finding(
                        contract,
                        f"vertical-circulation-{name}-criterion-unknown",
                        FindingSeverity.UNKNOWN,
                        f"resolved path requires an adopted {name} criterion",
                    )
                )
        _check_geometry_witness(contract, findings, measurements)
        if contract.maturity is VerticalCirculationMaturity.ASSEMBLY:
            _check_assembly(contract, findings, measurements)

    if any(item.severity is FindingSeverity.ERROR for item in findings):
        status = CheckStatus.FAIL
    elif any(item.severity is FindingSeverity.UNKNOWN for item in findings):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    findings_tuple = tuple(
        sorted(findings, key=lambda item: (item.code, item.subject_refs, item.message))
    )
    measurements_tuple = tuple(
        sorted(measurements, key=lambda item: item.measurement_id)
    )
    if contract.maturity is VerticalCirculationMaturity.RESERVATION:
        denominator = (contract.ref,)
    else:
        denominator_refs = set(contract.surface_denominator_refs)
        if contract.assembly_witness is not None:
            denominator_refs.update(
                contract.assembly_witness.underpass_assembly_denominator_refs
            )
        denominator = tuple(sorted(denominator_refs))
    covered = denominator if status in {CheckStatus.PASS, CheckStatus.FAIL} else ()
    return CheckReceiptEnvelope(
        check_id=f"vertical-circulation-{contract.contract_digest[:24]}",
        checker_id="vertical-circulation-contract-checker",
        checker_version="1.0.0",
        branch=contract.branch,
        scope_digest=contract.scope_digest,
        subject_refs=contract.subject_refs,
        subject_digest=contract.stage_subject_digest,
        status=status,
        adoption_refs=tuple(
            sorted(
                {
                    *contract.criteria.adoption_refs,
                    *(
                        contract.assembly_witness.underpass_form_adoption_refs
                        if contract.assembly_witness is not None
                        else ()
                    ),
                }
            )
        ),
        source_refs=contract.source_refs,
        findings=findings_tuple,
        measurements=measurements_tuple,
        coverage_denominator=denominator,
        covered_refs=covered,
    )


validate_vertical_circulation = check_vertical_circulation


def vertical_circulation_maturity_check_id(
    contract: VerticalCirculationContract,
    required_maturity: VerticalCirculationMaturity,
) -> str:
    """Return the stable stage requirement identity for one typed source."""

    if not isinstance(contract, VerticalCirculationContract):
        raise TypeError("contract must be VerticalCirculationContract")
    if not isinstance(required_maturity, VerticalCirculationMaturity):
        raise TypeError("required_maturity must be VerticalCirculationMaturity")
    maturity_token = required_maturity.value.replace("_", "-")
    return f"vertical-circulation-{contract.contract_id}-{maturity_token}"


def check_vertical_circulation_maturity(
    contract: VerticalCirculationContract,
    *,
    required_maturity: VerticalCirculationMaturity,
) -> CheckReceiptEnvelope:
    """Bridge the detailed check into an unambiguous stage-maturity result.

    A reservation may pass this *maturity* check when its exact endpoint and
    negative-envelope bindings are internally consistent.  That result is
    intentionally emitted by a separate checker and retains an INFO finding
    stating that it grants no walkability claim.  Resolved-path and assembly
    maturity require the detailed checker itself to pass.
    """

    if not isinstance(contract, VerticalCirculationContract):
        raise TypeError("contract must be VerticalCirculationContract")
    if not isinstance(required_maturity, VerticalCirculationMaturity):
        raise TypeError("required_maturity must be VerticalCirculationMaturity")
    detailed = check_vertical_circulation(contract)
    ranks = {
        VerticalCirculationMaturity.RESERVATION: 0,
        VerticalCirculationMaturity.RESOLVED_PATH: 1,
        VerticalCirculationMaturity.ASSEMBLY: 2,
    }
    findings: tuple[CheckFinding, ...]
    if ranks[contract.maturity] < ranks[required_maturity]:
        status = CheckStatus.FAIL
        findings = (
            CheckFinding(
                code="vertical-circulation-stage-maturity-insufficient",
                severity=FindingSeverity.ERROR,
                message=(
                    f"stage requires {required_maturity.value}, contract declares "
                    f"{contract.maturity.value}"
                ),
                subject_refs=(contract.ref,),
            ),
        )
    elif (
        required_maturity is VerticalCirculationMaturity.RESERVATION
        and contract.maturity is VerticalCirculationMaturity.RESERVATION
        and detailed.status is CheckStatus.UNKNOWN
        and {item.code for item in detailed.findings}
        == {"vertical-circulation-reservation-not-walkable"}
    ):
        status = CheckStatus.PASS
        findings = (
            CheckFinding(
                code="vertical-circulation-reservation-satisfied-not-walkable",
                severity=FindingSeverity.INFO,
                message=(
                    "exact reservation bindings are consistent; this maturity "
                    "receipt does not establish a walkable path"
                ),
                subject_refs=(contract.ref,),
            ),
        )
    elif detailed.status is CheckStatus.PASS:
        status = CheckStatus.PASS
        findings = ()
    elif detailed.status is CheckStatus.FAIL:
        status = CheckStatus.FAIL
        findings = (
            CheckFinding(
                code="vertical-circulation-stage-validation-failed",
                severity=FindingSeverity.ERROR,
                message=(
                    "typed vertical-circulation validation contains a "
                    "geometry or binding contradiction"
                ),
                subject_refs=(contract.ref,),
            ),
        )
    else:
        status = CheckStatus.UNKNOWN
        findings = (
            CheckFinding(
                code="vertical-circulation-stage-validation-unknown",
                severity=FindingSeverity.UNKNOWN,
                message=(
                    "typed vertical-circulation validation lacks a mandatory "
                    "criterion or geometry witness"
                ),
                subject_refs=(contract.ref,),
            ),
        )
    denominator = (contract.ref,)
    return CheckReceiptEnvelope(
        check_id=vertical_circulation_maturity_check_id(
            contract,
            required_maturity,
        ),
        checker_id=VERTICAL_CIRCULATION_MATURITY_CHECKER_ID,
        checker_version="1.0.0",
        branch=contract.branch,
        scope_digest=contract.scope_digest,
        subject_refs=denominator,
        subject_digest=contract.stage_subject_digest,
        status=status,
        adoption_refs=contract.criteria.adoption_refs,
        source_refs=contract.source_refs,
        # The retained adoption is the project authority for using these
        # otherwise project-agnostic criteria at this stage boundary.
        authority_refs=contract.criteria.adoption_refs,
        findings=findings,
        coverage_denominator=denominator,
        covered_refs=(denominator if status is CheckStatus.PASS else ()),
    )


__all__ = [
    "CirculationAabbNegativePrecheck",
    "LandingOwnership",
    "LandingRole",
    "LandingWitness",
    "VerticalCirculationAssemblyWitness",
    "VerticalCirculationContract",
    "VerticalCirculationCriteria",
    "VerticalCirculationError",
    "VerticalCirculationInterface",
    "VerticalCirculationInterfaceBasis",
    "VerticalCirculationInterfaceRole",
    "VerticalCirculationMaturity",
    "VerticalCirculationObjectBinding",
    "VerticalCirculationUpAxis",
    "VERTICAL_CIRCULATION_MATURITY_CHECKER_ID",
    "VERTICAL_CIRCULATION_SUPPORT_CHECKER_ID",
    "VERTICAL_CIRCULATION_LOAD_PATH_CHECKER_ID",
    "VERTICAL_CIRCULATION_HOST_CUT_CHECKER_ID",
    "VERTICAL_CIRCULATION_UNDERPASS_CHECKER_ID",
    "WalkingSurfaceAdjacency",
    "WalkingSurfaceGeometryWitness",
    "WalkingSurfaceSample",
    "WalkingSurfaceSampleRole",
    "WalkingSurfaceWitnessKind",
    "check_vertical_circulation",
    "check_vertical_circulation_maturity",
    "validate_vertical_circulation",
    "vertical_circulation_maturity_check_id",
]
