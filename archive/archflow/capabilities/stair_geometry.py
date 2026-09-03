"""Compile a solved semantic stair into neutral geometry-program solids.

The stair solver uses a local right-handed ``(run, side, up)`` basis with
local Z as elevation.  Geometry programs use a right-handed world coordinate
system with Y as elevation.  This bridge owns that conversion: it bakes every
stair tread and stair-owned landing into world/Y-up extrusion parameters on an
identity frame.  CAD adapters therefore receive ordinary neutral geometry and
need no stair-specific coordinate exception.

The bridge is deliberately authority-free and persistence-free.  It creates an
initial proposal for one stair semantic component.  A predecessor edit still
belongs to the generic exact-predecessor merge/revision machinery because only
that machinery has the prior object revision tokens needed to revise safely.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archive.archflow.capabilities.stair_solver import (
    StairAssembly,
    StairInterface,
    StairInterfaceRole,
    StairObligationKind,
    StairPlanEnvelope,
    StairSolveRequest,
    StairSolveResult,
    StairSolveStatus,
    StairTerminalLandingOwnership,
    require_solved_stair_assembly,
    solve_stair,
)
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
from archflow.project.refs import require_identifier
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentDiscipline,
)
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    SemanticBinding,
    canonical_json,
    digest_value,
)
from archflow.state.operational_state import require_logical_ref


_ORTHONORMAL_TOLERANCE = 1.0e-9
STAIR_DESIGN_STATE_ATTRIBUTE_KEY = "stair_design_binding"
_BINDING_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "promotion_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class StairGeometryError(ValueError):
    """A stair cannot be represented by the neutral geometry contract."""


class StairMaterializationMaturity(StrEnum):
    """Whether a stair is only reserved or may become developed geometry."""

    RESERVATION = "reservation"
    DEVELOPED = "developed"


class StairObligationDisposition(StrEnum):
    """Project-authored disposition of one exact solver obligation."""

    OPEN = "OPEN"
    SATISFIED = "SATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_MATERIALIZATION_BLOCKING_OBLIGATIONS = frozenset(
    {
        StairObligationKind.HOST_OPENING,
        StairObligationKind.SITE_SUPPORT,
        StairObligationKind.LOAD_PATH,
    }
)


@dataclass(frozen=True, slots=True)
class StairObligationResolution:
    """Evidence-bound disposition of one exact downstream obligation.

    ``SATISFIED`` and ``NOT_APPLICABLE`` are project claims and therefore
    require both evidence and adoption authority.  The value has no validation
    or stage-acceptance authority; it only controls whether the neutral stair
    producer is allowed to emit developed solids.
    """

    obligation_ref: str
    kind: StairObligationKind
    disposition: StairObligationDisposition
    evidence_refs: tuple[str, ...] = ()
    authority_refs: tuple[str, ...] = ()

    SCHEMA: ClassVar[str] = "StairObligationResolution@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_ref",
            logical_ref(self.obligation_ref, "obligation_ref"),
        )
        if not isinstance(self.kind, StairObligationKind):
            raise TypeError("kind must be StairObligationKind")
        if not isinstance(self.disposition, StairObligationDisposition):
            raise TypeError(
                "disposition must be StairObligationDisposition"
            )
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    field,
                    allow_empty=True,
                ),
            )
        if self.disposition is StairObligationDisposition.OPEN:
            if self.authority_refs:
                raise StairGeometryError(
                    "an OPEN stair obligation cannot claim resolution authority"
                )
        elif not self.evidence_refs or not self.authority_refs:
            raise StairGeometryError(
                "a closed stair obligation requires evidence and authority refs"
            )

    @property
    def ref(self) -> str:
        return f"stair-obligation-resolution:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "obligation_ref": self.obligation_ref,
            "kind": self.kind.value,
            "disposition": self.disposition.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_BINDING_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairObligationResolution":
        payload = exact_mapping(
            value,
            {
                "schema",
                "obligation_ref",
                "kind",
                "disposition",
                "evidence_refs",
                "authority_refs",
                *_BINDING_AUTHORITY_FIELDS,
            },
            "stair obligation resolution",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StairGeometryError(
                "unsupported stair-obligation resolution schema"
            )
        for field in ("evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            obligation_ref=payload["obligation_ref"],
            kind=StairObligationKind(payload["kind"]),
            disposition=StairObligationDisposition(payload["disposition"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != payload:
            raise StairGeometryError(
                "stair-obligation resolution identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class StairMaterializationGate:
    """Exact no-authority gate between a solve result and physical solids."""

    solve_result_digest: str
    maturity: StairMaterializationMaturity
    obligation_resolutions: tuple[StairObligationResolution, ...]

    SCHEMA: ClassVar[str] = "StairMaterializationGate@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "solve_result_digest",
            require_sha256(self.solve_result_digest, "solve_result_digest"),
        )
        if not isinstance(self.maturity, StairMaterializationMaturity):
            raise TypeError("maturity must be StairMaterializationMaturity")
        if not isinstance(self.obligation_resolutions, tuple) or any(
            not isinstance(item, StairObligationResolution)
            for item in self.obligation_resolutions
        ):
            raise TypeError(
                "obligation_resolutions must contain StairObligationResolution"
            )
        expected_order = tuple(
            sorted(
                self.obligation_resolutions,
                key=lambda item: item.kind.value,
            )
        )
        if expected_order != self.obligation_resolutions:
            raise StairGeometryError(
                "stair obligation resolutions must be in canonical kind order"
            )
        kinds = tuple(item.kind for item in self.obligation_resolutions)
        if len(kinds) != len(set(kinds)) or set(kinds) != set(
            StairObligationKind
        ):
            raise StairGeometryError(
                "materialization gate must disposition every stair obligation once"
            )
        if (
            self.maturity is StairMaterializationMaturity.DEVELOPED
            and self.blocking_open_kinds
        ):
            raise StairGeometryError(
                "developed stair gate retains OPEN materialization obligations: "
                f"{tuple(item.value for item in self.blocking_open_kinds)}"
            )

    @property
    def blocking_open_kinds(self) -> tuple[StairObligationKind, ...]:
        return tuple(
            sorted(
                (
                    item.kind
                    for item in self.obligation_resolutions
                    if item.kind in _MATERIALIZATION_BLOCKING_OBLIGATIONS
                    and item.disposition is StairObligationDisposition.OPEN
                ),
                key=lambda item: item.value,
            )
        )

    @property
    def gate_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def ref(self) -> str:
        return f"stair-materialization-gate:{self.gate_digest}"

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "solve_result_digest": self.solve_result_digest,
            "maturity": self.maturity.value,
            "obligation_resolutions": [
                item.to_dict() for item in self.obligation_resolutions
            ],
            **_BINDING_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "gate_digest": self.gate_digest}

    @classmethod
    def from_dict(cls, value: object) -> "StairMaterializationGate":
        payload = exact_mapping(
            value,
            {
                "schema",
                "solve_result_digest",
                "maturity",
                "obligation_resolutions",
                "gate_digest",
                *_BINDING_AUTHORITY_FIELDS,
            },
            "stair materialization gate",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StairGeometryError(
                "unsupported stair-materialization gate schema"
            )
        if not isinstance(payload["obligation_resolutions"], list):
            raise TypeError("obligation_resolutions must be a list")
        result = cls(
            solve_result_digest=payload["solve_result_digest"],
            maturity=StairMaterializationMaturity(payload["maturity"]),
            obligation_resolutions=tuple(
                StairObligationResolution.from_dict(item)
                for item in payload["obligation_resolutions"]
            ),
        )
        if result.to_dict() != payload:
            raise StairGeometryError(
                "stair-materialization gate identity changed"
            )
        return result


def _finite_vector3(
    value: object,
    field: str,
) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise TypeError(f"{field} must be a three-value tuple")
    if any(
        not isinstance(item, (int, float)) or isinstance(item, bool)
        for item in value
    ):
        raise TypeError(f"{field} must contain numeric values")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise StairGeometryError(f"{field} must contain finite values")
    return result  # type: ignore[return-value]


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(value: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(value, value))


def _require_refs(values: object, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise StairGeometryError(f"{field} must be a non-empty tuple")
    for value in values:
        require_logical_ref(value, field)
    if values != tuple(sorted(set(values))):
        raise StairGeometryError(f"{field} must be sorted and unique")
    return values


@dataclass(frozen=True, slots=True)
class StairPlacement:
    """Rigid placement from solver-local Z-up into world/Y-up coordinates.

    ``run_basis x side_basis == up_basis`` is required.  Every basis vector is
    unit length and mutually orthogonal, so scale, reflection, and shear cannot
    enter through placement.  Stair elevation semantics additionally require
    local up to map to world +Y.
    """

    origin: tuple[float, float, float]
    run_basis: tuple[float, float, float]
    side_basis: tuple[float, float, float]
    up_basis: tuple[float, float, float]

    SCHEMA: ClassVar[str] = "StairPlacement@1"

    def __post_init__(self) -> None:
        for field in ("origin", "run_basis", "side_basis", "up_basis"):
            object.__setattr__(
                self,
                field,
                _finite_vector3(getattr(self, field), field),
            )
        bases = (self.run_basis, self.side_basis, self.up_basis)
        if any(
            abs(_length(value) - 1.0) > _ORTHONORMAL_TOLERANCE
            for value in bases
        ):
            raise StairGeometryError(
                "stair placement basis vectors must have unit length"
            )
        if any(
            abs(_dot(left, right)) > _ORTHONORMAL_TOLERANCE
            for left, right in (
                (self.run_basis, self.side_basis),
                (self.run_basis, self.up_basis),
                (self.side_basis, self.up_basis),
            )
        ):
            raise StairGeometryError(
                "stair placement basis vectors must be orthogonal"
            )
        handed_up = _cross(self.run_basis, self.side_basis)
        if any(
            abs(actual - expected) > _ORTHONORMAL_TOLERANCE
            for actual, expected in zip(
                handed_up,
                self.up_basis,
                strict=True,
            )
        ):
            raise StairGeometryError(
                "stair placement must be right-handed and cannot reflect"
            )
        if any(
            abs(actual - expected) > _ORTHONORMAL_TOLERANCE
            for actual, expected in zip(
                self.up_basis,
                (0.0, 1.0, 0.0),
                strict=True,
            )
        ):
            raise StairGeometryError(
                "stair placement up_basis must equal world +Y"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "origin": list(self.origin),
            "run_basis": list(self.run_basis),
            "side_basis": list(self.side_basis),
            "up_basis": list(self.up_basis),
            "scale_authority": False,
            "shear_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairPlacement":
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "origin",
            "run_basis",
            "side_basis",
            "up_basis",
            "scale_authority",
            "shear_authority",
            "canonical_write_authority",
        }:
            raise StairGeometryError("stair placement schema drifted")
        if (
            value["schema"] != cls.SCHEMA
        ):
            raise StairGeometryError("stair placement acquired authority")
        for field in ("origin", "run_basis", "side_basis", "up_basis"):
            if not isinstance(value[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            origin=tuple(value["origin"]),
            run_basis=tuple(value["run_basis"]),
            side_basis=tuple(value["side_basis"]),
            up_basis=tuple(value["up_basis"]),
        )
        if result.to_dict() != value:
            raise StairGeometryError("stair placement identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairInterfaceDesignBinding:
    """Exact design-state relation that owns one stair endpoint."""

    interface: StairInterface
    point: tuple[float, float, float]
    relation_record_digest: str
    length_unit: LengthUnit
    coordinate_frame_ref: str
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairInterfaceDesignBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.interface, StairInterface):
            raise TypeError("interface must be StairInterface")
        object.__setattr__(
            self,
            "point",
            _finite_vector3(self.point, "interface point"),
        )
        object.__setattr__(
            self,
            "relation_record_digest",
            require_sha256(
                self.relation_record_digest,
                "relation_record_digest",
            ),
        )
        if not isinstance(self.length_unit, LengthUnit):
            raise TypeError("length_unit must be LengthUnit")
        object.__setattr__(
            self,
            "coordinate_frame_ref",
            logical_ref(self.coordinate_frame_ref, "coordinate_frame_ref"),
        )
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field),
            )
        if not math.isclose(
            self.point[1],
            self.interface.datum,
            rel_tol=0.0,
            abs_tol=_ORTHONORMAL_TOLERANCE,
        ):
            raise StairGeometryError(
                "interface point elevation disagrees with its exact datum"
            )

    @property
    def interface_digest(self) -> str:
        return canonical_digest(self.interface.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "interface": self.interface.to_dict(),
            "interface_digest": self.interface_digest,
            "point": list(self.point),
            "relation_record_digest": self.relation_record_digest,
            "length_unit": self.length_unit.value,
            "coordinate_frame_ref": self.coordinate_frame_ref,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_BINDING_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairInterfaceDesignBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "interface",
                "interface_digest",
                "point",
                "relation_record_digest",
                "length_unit",
                "coordinate_frame_ref",
                "evidence_refs",
                "authority_refs",
                *_BINDING_AUTHORITY_FIELDS,
            },
            "stair interface design binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StairGeometryError(
                "unsupported stair-interface binding schema"
            )
        for field in ("point", "evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            interface=StairInterface.from_dict(payload["interface"]),
            point=tuple(payload["point"]),
            relation_record_digest=payload["relation_record_digest"],
            length_unit=LengthUnit(payload["length_unit"]),
            coordinate_frame_ref=payload["coordinate_frame_ref"],
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != payload:
            raise StairGeometryError(
                "stair-interface design binding identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class StairPlanEnvelopeDesignBinding:
    """Exact world placement relation for the solver's local plan envelope."""

    envelope: StairPlanEnvelope
    minimum_world_point: tuple[float, float, float]
    run_basis: tuple[float, float, float]
    side_basis: tuple[float, float, float]
    up_basis: tuple[float, float, float]
    relation_record_digest: str
    length_unit: LengthUnit
    coordinate_frame_ref: str
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairPlanEnvelopeDesignBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, StairPlanEnvelope):
            raise TypeError("envelope must be StairPlanEnvelope")
        object.__setattr__(
            self,
            "minimum_world_point",
            _finite_vector3(
                self.minimum_world_point,
                "minimum_world_point",
            ),
        )
        for field in ("run_basis", "side_basis", "up_basis"):
            object.__setattr__(
                self,
                field,
                _finite_vector3(getattr(self, field), field),
            )
        # Reuse the rigid-placement invariant without accepting a free origin.
        StairPlacement(
            origin=(0.0, 0.0, 0.0),
            run_basis=self.run_basis,
            side_basis=self.side_basis,
            up_basis=self.up_basis,
        )
        object.__setattr__(
            self,
            "relation_record_digest",
            require_sha256(
                self.relation_record_digest,
                "relation_record_digest",
            ),
        )
        if not isinstance(self.length_unit, LengthUnit):
            raise TypeError("length_unit must be LengthUnit")
        object.__setattr__(
            self,
            "coordinate_frame_ref",
            logical_ref(self.coordinate_frame_ref, "coordinate_frame_ref"),
        )
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field),
            )

    @property
    def envelope_digest(self) -> str:
        return canonical_digest(self.envelope.to_dict())

    def derive_placement(self) -> StairPlacement:
        minimum_x, minimum_y = self.envelope.minimum
        origin = tuple(
            self.minimum_world_point[index]
            - minimum_x * self.run_basis[index]
            - minimum_y * self.side_basis[index]
            for index in range(3)
        )
        return StairPlacement(
            origin=origin,
            run_basis=self.run_basis,
            side_basis=self.side_basis,
            up_basis=self.up_basis,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "envelope": self.envelope.to_dict(),
            "envelope_digest": self.envelope_digest,
            "minimum_world_point": list(self.minimum_world_point),
            "run_basis": list(self.run_basis),
            "side_basis": list(self.side_basis),
            "up_basis": list(self.up_basis),
            "relation_record_digest": self.relation_record_digest,
            "length_unit": self.length_unit.value,
            "coordinate_frame_ref": self.coordinate_frame_ref,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_BINDING_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairPlanEnvelopeDesignBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "envelope",
                "envelope_digest",
                "minimum_world_point",
                "run_basis",
                "side_basis",
                "up_basis",
                "relation_record_digest",
                "length_unit",
                "coordinate_frame_ref",
                "evidence_refs",
                "authority_refs",
                *_BINDING_AUTHORITY_FIELDS,
            },
            "stair plan-envelope design binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StairGeometryError(
                "unsupported stair plan-envelope binding schema"
            )
        for field in (
            "minimum_world_point",
            "run_basis",
            "side_basis",
            "up_basis",
            "evidence_refs",
            "authority_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            envelope=StairPlanEnvelope.from_dict(payload["envelope"]),
            minimum_world_point=tuple(payload["minimum_world_point"]),
            run_basis=tuple(payload["run_basis"]),
            side_basis=tuple(payload["side_basis"]),
            up_basis=tuple(payload["up_basis"]),
            relation_record_digest=payload["relation_record_digest"],
            length_unit=LengthUnit(payload["length_unit"]),
            coordinate_frame_ref=payload["coordinate_frame_ref"],
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != payload:
            raise StairGeometryError(
                "stair plan-envelope design binding identity changed"
            )
        return result


def _is_stair_semantic_kind(value: str) -> bool:
    normalized = "-".join(value.casefold().replace("_", " ").split())
    tokens = tuple(token for token in normalized.split("-") if token)
    return (
        any(
            token in {"stair", "stairs", "staircase", "stairwell"}
            for token in tokens
        )
        or any(
            left == "vertical" and right == "circulation"
            for left, right in zip(tokens, tokens[1:])
        )
    )


def _is_stair_reservation_semantic_kind(value: str) -> bool:
    """Return whether a stair kind names negative-space/preview intent."""

    normalized = "-".join(value.casefold().replace("_", " ").split())
    tokens = frozenset(token for token in normalized.split("-") if token)
    return bool(tokens & {"envelope", "preview", "reservation"})


@dataclass(frozen=True, slots=True)
class StairDesignBinding:
    """Exact state/relation binding from which stair geometry is placed."""

    binding_id: str
    branch: BranchRef
    scope_digest: str
    stage_id: str
    design_state_digest: str
    stage_subject_inventory_digest: str
    component_id: str
    component_identity_ref: str
    component_semantic_kind: str
    solve_request_digest: str
    materialization_gate: StairMaterializationGate
    length_unit: LengthUnit
    coordinate_frame_ref: str
    lower_interface: StairInterfaceDesignBinding
    upper_interface: StairInterfaceDesignBinding
    plan_envelope: StairPlanEnvelopeDesignBinding
    lower_landing_ownership: StairTerminalLandingOwnership
    upper_landing_ownership: StairTerminalLandingOwnership
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairDesignBinding@2"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "binding_id",
            identifier(self.binding_id, "binding_id"),
        )
        require_exact_branch(self.branch)
        for field in (
            "scope_digest",
            "design_state_digest",
            "stage_subject_inventory_digest",
            "solve_request_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        object.__setattr__(
            self,
            "stage_id",
            identifier(self.stage_id, "stage_id"),
        )
        require_identifier(self.component_id, "component_id")
        object.__setattr__(
            self,
            "component_identity_ref",
            logical_ref(
                self.component_identity_ref,
                "component_identity_ref",
            ),
        )
        if self.component_identity_ref != f"design-component:{self.component_id}":
            raise StairGeometryError(
                "component identity ref does not match component_id"
            )
        require_identifier(
            self.component_semantic_kind,
            "component_semantic_kind",
        )
        if not _is_stair_semantic_kind(self.component_semantic_kind):
            raise StairGeometryError(
                "stair design binding requires a stair semantic component"
            )
        if not isinstance(
            self.materialization_gate,
            StairMaterializationGate,
        ):
            raise TypeError(
                "materialization_gate must be StairMaterializationGate"
            )
        if not isinstance(self.length_unit, LengthUnit):
            raise TypeError("length_unit must be LengthUnit")
        object.__setattr__(
            self,
            "coordinate_frame_ref",
            logical_ref(self.coordinate_frame_ref, "coordinate_frame_ref"),
        )
        if (
            not isinstance(self.lower_interface, StairInterfaceDesignBinding)
            or self.lower_interface.interface.role is not StairInterfaceRole.LOWER
        ):
            raise StairGeometryError(
                "lower_interface must bind the lower stair interface"
            )
        if (
            not isinstance(self.upper_interface, StairInterfaceDesignBinding)
            or self.upper_interface.interface.role is not StairInterfaceRole.UPPER
        ):
            raise StairGeometryError(
                "upper_interface must bind the upper stair interface"
            )
        if not isinstance(self.plan_envelope, StairPlanEnvelopeDesignBinding):
            raise TypeError(
                "plan_envelope must be StairPlanEnvelopeDesignBinding"
            )
        nested_units = {
            self.lower_interface.length_unit,
            self.upper_interface.length_unit,
            self.plan_envelope.length_unit,
        }
        if nested_units != {self.length_unit}:
            raise StairGeometryError(
                "stair design binding crossed its exact length unit"
            )
        nested_frames = {
            self.lower_interface.coordinate_frame_ref,
            self.upper_interface.coordinate_frame_ref,
            self.plan_envelope.coordinate_frame_ref,
        }
        if nested_frames != {self.coordinate_frame_ref}:
            raise StairGeometryError(
                "stair design binding crossed its exact coordinate frame"
            )
        if not math.isclose(
            self.plan_envelope.minimum_world_point[1],
            self.lower_interface.interface.datum,
            rel_tol=0.0,
            abs_tol=_ORTHONORMAL_TOLERANCE,
        ):
            raise StairGeometryError(
                "plan envelope is not located on the exact lower datum"
            )
        if (
            self.lower_interface.interface.datum
            >= self.upper_interface.interface.datum
        ):
            raise StairGeometryError(
                "lower stair interface must be below the upper interface"
            )
        for field in (
            "lower_landing_ownership",
            "upper_landing_ownership",
        ):
            if not isinstance(
                getattr(self, field),
                StairTerminalLandingOwnership,
            ):
                raise TypeError(
                    f"{field} must be StairTerminalLandingOwnership"
                )
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field),
            )
        gate_evidence = {
            ref
            for resolution in self.materialization_gate.obligation_resolutions
            for ref in resolution.evidence_refs
        }
        gate_authority = {
            ref
            for resolution in self.materialization_gate.obligation_resolutions
            for ref in resolution.authority_refs
        }
        if not gate_evidence.issubset(self.evidence_refs):
            raise StairGeometryError(
                "stair design binding omits materialization-gate evidence"
            )
        if not gate_authority.issubset(self.authority_refs):
            raise StairGeometryError(
                "stair design binding omits materialization-gate authority"
            )

    @property
    def ref(self) -> str:
        return f"stair-design-binding:{self.binding_id}:{canonical_digest(self.to_dict())}"

    def state_attribute_payload(self) -> dict[str, object]:
        """Return the non-recursive content that the exact state must own.

        ``design_state_digest`` is deliberately absent because the developed
        state contains this payload and therefore cannot also contain its own
        final digest.  The outer binding supplies that digest after the state
        has been compiled.
        """

        return {
            "schema": "StairDesignStateAttribute@3",
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_id": self.stage_id,
            "stage_subject_inventory_digest": (
                self.stage_subject_inventory_digest
            ),
            "component_id": self.component_id,
            "component_identity_ref": self.component_identity_ref,
            "component_semantic_kind": self.component_semantic_kind,
            "solve_request_digest": self.solve_request_digest,
            "materialization_gate_digest": (
                self.materialization_gate.gate_digest
            ),
            "materialization_maturity": (
                self.materialization_gate.maturity.value
            ),
            "length_unit": self.length_unit.value,
            "coordinate_frame_ref": self.coordinate_frame_ref,
            "lower_interface_binding_digest": canonical_digest(
                self.lower_interface.to_dict()
            ),
            "upper_interface_binding_digest": canonical_digest(
                self.upper_interface.to_dict()
            ),
            "plan_envelope_binding_digest": canonical_digest(
                self.plan_envelope.to_dict()
            ),
            "lower_landing_ownership": self.lower_landing_ownership.value,
            "upper_landing_ownership": self.upper_landing_ownership.value,
            "basis_digest": canonical_digest(
                {
                    "evidence_refs": list(self.evidence_refs),
                    "authority_refs": list(self.authority_refs),
                    **_BINDING_AUTHORITY_FIELDS,
                }
            ),
        }

    @property
    def state_attribute_json(self) -> str:
        return canonical_json(self.state_attribute_payload())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding_id": self.binding_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_id": self.stage_id,
            "design_state_digest": self.design_state_digest,
            "stage_subject_inventory_digest": (
                self.stage_subject_inventory_digest
            ),
            "component_id": self.component_id,
            "component_identity_ref": self.component_identity_ref,
            "component_semantic_kind": self.component_semantic_kind,
            "solve_request_digest": self.solve_request_digest,
            "materialization_gate": self.materialization_gate.to_dict(),
            "length_unit": self.length_unit.value,
            "coordinate_frame_ref": self.coordinate_frame_ref,
            "lower_interface": self.lower_interface.to_dict(),
            "upper_interface": self.upper_interface.to_dict(),
            "plan_envelope": self.plan_envelope.to_dict(),
            "lower_landing_ownership": self.lower_landing_ownership.value,
            "upper_landing_ownership": self.upper_landing_ownership.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_BINDING_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairDesignBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "binding_id",
                "branch",
                "scope_digest",
                "stage_id",
                "design_state_digest",
                "stage_subject_inventory_digest",
                "component_id",
                "component_identity_ref",
                "component_semantic_kind",
                "solve_request_digest",
                "materialization_gate",
                "length_unit",
                "coordinate_frame_ref",
                "lower_interface",
                "upper_interface",
                "plan_envelope",
                "lower_landing_ownership",
                "upper_landing_ownership",
                "evidence_refs",
                "authority_refs",
                *_BINDING_AUTHORITY_FIELDS,
            },
            "stair design binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StairGeometryError("unsupported stair design binding schema")
        for field in ("evidence_refs", "authority_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            binding_id=payload["binding_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_id=payload["stage_id"],
            design_state_digest=payload["design_state_digest"],
            stage_subject_inventory_digest=(
                payload["stage_subject_inventory_digest"]
            ),
            component_id=payload["component_id"],
            component_identity_ref=payload["component_identity_ref"],
            component_semantic_kind=payload["component_semantic_kind"],
            solve_request_digest=payload["solve_request_digest"],
            materialization_gate=StairMaterializationGate.from_dict(
                payload["materialization_gate"]
            ),
            length_unit=LengthUnit(payload["length_unit"]),
            coordinate_frame_ref=payload["coordinate_frame_ref"],
            lower_interface=StairInterfaceDesignBinding.from_dict(
                payload["lower_interface"]
            ),
            upper_interface=StairInterfaceDesignBinding.from_dict(
                payload["upper_interface"]
            ),
            plan_envelope=StairPlanEnvelopeDesignBinding.from_dict(
                payload["plan_envelope"]
            ),
            lower_landing_ownership=StairTerminalLandingOwnership(
                payload["lower_landing_ownership"]
            ),
            upper_landing_ownership=StairTerminalLandingOwnership(
                payload["upper_landing_ownership"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
        )
        if result.to_dict() != payload:
            raise StairGeometryError("stair design binding identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairRealizationProfile:
    """Explicit material thicknesses for solver walking surfaces."""

    tread_thickness: float
    landing_thickness: float | None

    SCHEMA: ClassVar[str] = "StairRealizationProfile@1"

    def __post_init__(self) -> None:
        value = self.tread_thickness
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            raise StairGeometryError(
                "tread_thickness must be finite and positive"
            )
        object.__setattr__(self, "tread_thickness", float(value))
        landing = self.landing_thickness
        if landing is None:
            return
        if (
            not isinstance(landing, (int, float))
            or isinstance(landing, bool)
            or not math.isfinite(float(landing))
            or float(landing) <= 0.0
        ):
            raise StairGeometryError(
                "landing_thickness must be None or finite and positive"
            )
        object.__setattr__(self, "landing_thickness", float(landing))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "tread_thickness": self.tread_thickness,
            "landing_thickness": self.landing_thickness,
            "structural_support_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairRealizationProfile":
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "tread_thickness",
            "landing_thickness",
            "structural_support_authority",
            "canonical_write_authority",
        }:
            raise StairGeometryError("stair realization profile schema drifted")
        if (
            value["schema"] != cls.SCHEMA
        ):
            raise StairGeometryError(
                "stair realization profile acquired authority"
            )
        result = cls(
            tread_thickness=value["tread_thickness"],
            landing_thickness=value["landing_thickness"],
        )
        if result.to_dict() != value:
            raise StairGeometryError(
                "stair realization profile identity changed"
            )
        return result


def _world_point(
    placement: StairPlacement,
    local: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(
        placement.origin[index]
        + local[0] * placement.run_basis[index]
        + local[1] * placement.side_basis[index]
        + local[2] * placement.up_basis[index]
        for index in range(3)
    )  # type: ignore[return-value]


def _points_close(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
    tolerance: float,
) -> bool:
    return all(
        math.isclose(
            actual,
            expected,
            rel_tol=0.0,
            abs_tol=tolerance,
        )
        for actual, expected in zip(left, right, strict=True)
    )


def _vector_parameter(
    name: str,
    value: tuple[float, float, float],
    unit: LengthUnit,
) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=GeometryParameterKind.VECTOR3,
        value=list(value),
        unit=unit,
    )


def _profile_parameter(
    points: tuple[tuple[float, float, float], ...],
    unit: LengthUnit,
) -> GeometryParameter:
    return GeometryParameter.create(
        name="profile",
        kind=GeometryParameterKind.POINTS3,
        value=[list(point) for point in points],
        unit=unit,
    )


def _member_operation(
    *,
    member_kind: str,
    ordinal: int,
    local_origin: tuple[float, float, float],
    size: tuple[float, float],
    thickness: float,
    placement: StairPlacement,
    length_unit: LengthUnit,
    identity_prefix: str,
    binding_id: str,
) -> GeometryOperation:
    require_identifier(member_kind, "stair member kind")
    bottom = (
        local_origin[0],
        local_origin[1],
        local_origin[2] - thickness,
    )
    local_profile = (
        bottom,
        (bottom[0] + size[0], bottom[1], bottom[2]),
        (bottom[0] + size[0], bottom[1] + size[1], bottom[2]),
        (bottom[0], bottom[1] + size[1], bottom[2]),
    )
    world_profile = tuple(
        _world_point(placement, point) for point in local_profile
    )
    world_vector = tuple(
        thickness * value for value in placement.up_basis
    )
    suffix = f"{member_kind}-{ordinal:04d}"
    object_id = f"stair-{identity_prefix}-{suffix}"
    return GeometryOperation(
        op_id=f"op-{object_id}",
        kind=GeometryOperationKind.EXTRUSION,
        output_object_ids=(object_id,),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            _profile_parameter(world_profile, length_unit),
            _vector_parameter("vector", world_vector, length_unit),
        ),
        semantic_binding_ids=(binding_id,),
    )


def _require_developed_materialization_gate(
    request: StairSolveRequest,
    assembly: StairAssembly,
    gate: StairMaterializationGate,
) -> StairSolveResult:
    """Replay the solve and reject reservation/open prerequisite disguises."""

    result = solve_stair(request)
    if (
        result.status is not StairSolveStatus.SOLVED
        or result.assembly is None
        or result.assembly != assembly
    ):
        raise StairGeometryError(
            "stair geometry does not match one exact solved result"
        )
    result_digest = canonical_digest(result.to_dict())
    if gate.solve_result_digest != result_digest:
        raise StairGeometryError(
            "stair materialization gate crossed its exact solve result"
        )
    expected = tuple(
        sorted(
            ((item.kind, item.ref) for item in result.obligations),
            key=lambda item: item[0].value,
        )
    )
    actual = tuple(
        (item.kind, item.obligation_ref)
        for item in gate.obligation_resolutions
    )
    if actual != expected:
        raise StairGeometryError(
            "stair materialization gate does not disposition the exact "
            "solver obligations"
        )
    if gate.maturity is not StairMaterializationMaturity.DEVELOPED:
        raise StairGeometryError(
            "reservation-only stair gate cannot emit developed geometry"
        )
    if gate.blocking_open_kinds:
        # The constructor already rejects this state.  Retain the boundary
        # check so no future schema loader can weaken materialization.
        raise StairGeometryError(
            "OPEN stair materialization obligations block developed geometry"
        )
    return result


def compile_stair_geometry_proposal(
    *,
    state: DevelopedDesignState,
    request: StairSolveRequest,
    assembly: StairAssembly,
    design_binding: StairDesignBinding,
    profile: StairRealizationProfile,
    commitment_refs: tuple[str, ...],
    tolerance: GeometryTolerance,
) -> GeometryProgramProposal:
    """Return one deterministic, initial neutral proposal for a solved stair.

    The result owns no predecessor-revision policy.  Callers integrating the
    objects into an existing program must use the generic edit compiler with
    exact predecessor tokens instead of assigning a predecessor digest here.
    """

    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if not isinstance(request, StairSolveRequest):
        raise TypeError("request must be StairSolveRequest")
    if not isinstance(assembly, StairAssembly):
        raise TypeError("assembly must be StairAssembly")
    if not isinstance(design_binding, StairDesignBinding):
        raise TypeError("design_binding must be StairDesignBinding")
    if not isinstance(profile, StairRealizationProfile):
        raise TypeError("profile must be StairRealizationProfile")
    if not isinstance(tolerance, GeometryTolerance):
        raise TypeError("tolerance must be GeometryTolerance")
    require_solved_stair_assembly(request, assembly)
    solve_result = _require_developed_materialization_gate(
        request,
        assembly,
        design_binding.materialization_gate,
    )
    if design_binding.solve_request_digest != request.digest:
        raise StairGeometryError(
            "stair design binding crossed its exact solve request"
        )
    if design_binding.length_unit is not request.length_unit:
        raise StairGeometryError(
            "stair design binding crossed the solve-request length unit"
        )
    if design_binding.lower_interface.interface != request.lower_interface:
        raise StairGeometryError(
            "stair design binding crossed the lower interface relation"
        )
    if design_binding.upper_interface.interface != request.upper_interface:
        raise StairGeometryError(
            "stair design binding crossed the upper interface relation"
        )
    if design_binding.plan_envelope.envelope != request.plan_envelope:
        raise StairGeometryError(
            "stair design binding crossed the plan-envelope relation"
        )
    if (
        design_binding.lower_landing_ownership
        is not request.lower_landing_ownership
        or design_binding.upper_landing_ownership
        is not request.upper_landing_ownership
    ):
        raise StairGeometryError(
            "stair design binding crossed terminal-landing ownership"
        )
    bound_run = design_binding.branch.run
    if (
        bound_run.project_id != state.project_id
        or bound_run.run_id != state.run_id
        or bound_run.base != state.base
    ):
        raise StairGeometryError(
            "stair design binding crossed the exact project/run/base"
        )
    if design_binding.design_state_digest != state.state_digest:
        raise StairGeometryError(
            "stair design binding crossed the exact design state"
        )
    component_id = design_binding.component_id
    component = next(
        (
            item
            for item in state.selected_schematic.option.proposal.components
            if item.component_id == component_id
        ),
        None,
    )
    if component is None:
        raise StairGeometryError(
            "stair component is absent from the exact developed design state"
        )
    if (
        component.identity_ref != design_binding.component_identity_ref
        or component.semantic_kind
        != design_binding.component_semantic_kind
        or not _is_stair_semantic_kind(component.semantic_kind)
    ):
        raise StairGeometryError(
            "design binding does not name the exact stair semantic component"
        )
    if _is_stair_reservation_semantic_kind(component.semantic_kind):
        raise StairGeometryError(
            "stair reservation/envelope semantics cannot masquerade as "
            "developed geometry"
        )
    developed_component = next(
        (
            item
            for item in state.components
            if item.component_id == component_id
        ),
        None,
    )
    if developed_component is None:
        raise StairGeometryError(
            "stair component is absent from developed component state"
        )
    if developed_component.discipline is not DevelopmentDiscipline.CIRCULATION:
        raise StairGeometryError(
            "stair developed component must belong to circulation"
        )
    state_attributes = tuple(
        item
        for item in developed_component.attributes
        if item.key == STAIR_DESIGN_STATE_ATTRIBUTE_KEY
    )
    if len(state_attributes) != 1:
        raise StairGeometryError(
            "exact developed state must own one stair design binding attribute"
        )
    state_attribute = state_attributes[0]
    if not state_attribute.source_claim_refs:
        raise StairGeometryError(
            "stair design binding attribute lacks an adopted source claim"
        )
    try:
        state_owned_payload = json.loads(state_attribute.value_json)
    except json.JSONDecodeError as exc:  # pragma: no cover - state validates JSON
        raise StairGeometryError(
            "stair design binding state attribute is invalid JSON"
        ) from exc
    if state_owned_payload != design_binding.state_attribute_payload():
        raise StairGeometryError(
            "stair placement or interface binding is not owned by the exact design state"
        )
    binding_evidence = {
        *design_binding.evidence_refs,
        *design_binding.lower_interface.evidence_refs,
        *design_binding.upper_interface.evidence_refs,
        *design_binding.plan_envelope.evidence_refs,
    }
    if not binding_evidence.issubset(state_attribute.evidence_refs):
        raise StairGeometryError(
            "stair state attribute does not retain the binding evidence denominator"
        )
    placement = design_binding.plan_envelope.derive_placement()
    endpoint_tolerance = max(
        _ORTHONORMAL_TOLERANCE,
        tolerance.linear,
    )
    lower_point = _world_point(placement, assembly.runs[0].start_point)
    upper_point = _world_point(placement, assembly.runs[-1].end_point)
    if not _points_close(
        lower_point,
        design_binding.lower_interface.point,
        endpoint_tolerance,
    ):
        raise StairGeometryError(
            "derived stair placement misses the exact lower interface"
        )
    if not _points_close(
        upper_point,
        design_binding.upper_interface.point,
        endpoint_tolerance,
    ):
        raise StairGeometryError(
            "derived stair placement misses the exact upper interface"
        )
    length_unit = design_binding.length_unit
    if not isinstance(commitment_refs, tuple):
        raise TypeError("commitment_refs must be a tuple")
    for value in commitment_refs:
        require_logical_ref(value, "commitment_refs")
    if commitment_refs != tuple(sorted(set(commitment_refs))):
        raise StairGeometryError("commitment_refs must be sorted and unique")
    if profile.tread_thickness <= tolerance.linear:
        raise StairGeometryError(
            "tread thickness must exceed the geometry linear tolerance"
        )
    landing_thickness = profile.landing_thickness
    if assembly.landings:
        if landing_thickness is None:
            raise StairGeometryError(
                "stair-owned landings require an explicit landing thickness"
            )
        if landing_thickness <= tolerance.linear:
            raise StairGeometryError(
                "landing thickness must exceed the geometry linear tolerance"
            )
    elif landing_thickness is not None:
        raise StairGeometryError(
            "landing thickness must be absent when the stair owns no landings"
        )
    if not assembly.treads and not assembly.landings:
        raise StairGeometryError(
            "stair assembly contains no realizable tread or landing members"
        )

    identity_prefix = hashlib.sha256(
        component_id.encode("utf-8")
    ).hexdigest()[:16]
    binding_id = f"stair-binding-{identity_prefix}"
    landing_operations = (
        ()
        if landing_thickness is None
        else tuple(
            _member_operation(
                member_kind="landing",
                ordinal=item.ordinal,
                # The placement origin is already located on the lower
                # interface datum.  Normalize the member's absolute design
                # datum into that placement-local elevation exactly once.
                local_origin=(
                    item.local_origin[0],
                    item.local_origin[1],
                    item.datum - request.lower_interface.datum,
                ),
                size=item.size,
                thickness=landing_thickness,
                placement=placement,
                length_unit=length_unit,
                identity_prefix=identity_prefix,
                binding_id=binding_id,
            )
            for item in assembly.landings
        )
    )
    operations = tuple(
        sorted(
            (
                *landing_operations,
                *(
                    _member_operation(
                        member_kind="tread",
                        ordinal=item.ordinal,
                        # walking_datum is absolute in the design frame;
                        # placement.origin already contributes the lower
                        # datum in world coordinates.
                        local_origin=(
                            item.local_origin[0],
                            item.local_origin[1],
                            item.walking_datum
                            - request.lower_interface.datum,
                        ),
                        size=item.size,
                        thickness=profile.tread_thickness,
                        placement=placement,
                        length_unit=length_unit,
                        identity_prefix=identity_prefix,
                        binding_id=binding_id,
                    )
                    for item in assembly.treads
                ),
            ),
            key=lambda item: item.op_id,
        )
    )
    object_ids = tuple(
        sorted(
            object_id
            for operation in operations
            for object_id in operation.output_object_ids
        )
    )
    source_refs = tuple(
        sorted(
            {
                design_binding.ref,
                design_binding.materialization_gate.ref,
                solve_result.ref,
                assembly.ref,
                *(
                    item.ref
                    for item in design_binding.materialization_gate.obligation_resolutions
                ),
                *design_binding.evidence_refs,
                *design_binding.lower_interface.evidence_refs,
                *design_binding.upper_interface.evidence_refs,
                *design_binding.plan_envelope.evidence_refs,
            }
        )
    )
    semantic_binding = SemanticBinding(
        binding_id=binding_id,
        component_id=component_id,
        object_ids=object_ids,
        commitment_refs=commitment_refs,
        evidence_refs=source_refs,
    )
    identity = digest_value(
        {
            "state_digest": state.state_digest,
            "component_id": component_id,
            "request_digest": request.digest,
            "solve_result": solve_result.to_dict(),
            "assembly": assembly.to_dict(),
            "design_binding": design_binding.to_dict(),
            "profile": profile.to_dict(),
            "commitment_refs": commitment_refs,
            "length_unit": length_unit.value,
            "tolerance": tolerance.to_dict(),
        }
    )
    proposal = GeometryProgramProposal(
        proposal_id=f"stair-proposal-{identity[:20]}",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=None,
        length_unit=length_unit,
        tolerance=tolerance,
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=source_refs,
            ),
        ),
        assets=(),
        semantic_bindings=(semantic_binding,),
        operations=operations,
        assemblies=(),
    )
    # This assertion also prevents accidental insertion of non-canonical values
    # into identifier derivation or geometry parameters.
    canonical_json(proposal.to_dict())
    return proposal


__all__ = [
    "STAIR_DESIGN_STATE_ATTRIBUTE_KEY",
    "StairDesignBinding",
    "StairGeometryError",
    "StairInterfaceDesignBinding",
    "StairMaterializationGate",
    "StairMaterializationMaturity",
    "StairObligationDisposition",
    "StairObligationResolution",
    "StairPlacement",
    "StairPlanEnvelopeDesignBinding",
    "StairRealizationProfile",
    "compile_stair_geometry_proposal",
]
