"""Typed, platform-neutral geometry-program contracts.

Architectural meaning is carried by semantic bindings and hosted assemblies.
The operation vocabulary below remains generic and has no project, style, or
external-tool routing authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.project import ProjectVersionRef
from archflow.project.refs import require_identifier
from archflow.state.operational_state import require_logical_ref


_HEX = frozenset("0123456789abcdef")
#: Stable ``scheme://`` asset-URI prefix used by both typed validation and
#: provider-facing JSON Schema publication.
ASSET_URI_PATTERN = r"^(?![Ff][Ii][Ll][Ee]://)[A-Za-z][A-Za-z0-9+.-]*://"

_URI_SCHEME = re.compile(ASSET_URI_PATTERN)
_MAX_ITEMS = 16_384


class GeometryProgramError(ValueError):
    """A neutral geometry contract is malformed or under-specified."""


class LengthUnit(StrEnum):
    MILLIMETER = "millimeter"
    METER = "meter"
    INCH = "inch"
    FOOT = "foot"


class GeometryOperationKind(StrEnum):
    CURVE = "curve"
    SOLID = "solid"
    TRANSFORM = "transform"
    EXTRUSION = "extrusion"
    REVOLVE = "revolve"
    LOFT = "loft"
    SWEEP = "sweep"
    ARRAY = "array"
    RADIAL_ARRAY = "radial_array"
    BOOLEAN_UNION = "boolean_union"
    BOOLEAN_DIFFERENCE = "boolean_difference"
    BOOLEAN_INTERSECTION = "boolean_intersection"
    ASSET_INSTANCE = "asset_instance"


class GeometryParameterKind(StrEnum):
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TEXT = "text"
    VECTOR3 = "vector3"
    POINTS3 = "points3"
    MATRIX4 = "matrix4"


class AssemblyKind(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    GENERIC_HOSTED = "generic_hosted"


class AssemblyRole(StrEnum):
    HOST_CUT = "host_cut"
    FRAME = "frame"
    LEAF = "leaf"
    GLAZING = "glazing"
    HARDWARE = "hardware"
    CLEARANCE = "clearance"


class DetailMaturity(StrEnum):
    ENVELOPE = "envelope"
    FUNCTIONAL = "functional"
    FINE = "fine"


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise GeometryProgramError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise GeometryProgramError(f"{field} must be finite")
    return float(value)


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise GeometryProgramError(f"{field} has an invalid item count")
    for value in values:
        require_identifier(value, field)
    if values != tuple(sorted(set(values))):
        raise GeometryProgramError(
            f"{field} requires unique deterministic identifiers"
        )
    return values


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise GeometryProgramError(f"{field} has an invalid item count")
    for value in values:
        require_logical_ref(value, field)
    if values != tuple(sorted(set(values))):
        raise GeometryProgramError(
            f"{field} requires unique deterministic references"
        )
    return values


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


@dataclass(frozen=True, slots=True)
class GeometryTolerance:
    linear: float
    angular_radians: float

    SCHEMA = "GeometryTolerance@1"

    def __post_init__(self) -> None:
        linear = _finite(self.linear, "linear tolerance")
        if linear <= 0:
            raise GeometryProgramError("linear tolerance must be positive")
        angular = _finite(self.angular_radians, "angular tolerance")
        if angular <= 0 or angular >= math.pi:
            raise GeometryProgramError(
                "angular tolerance must be between zero and pi"
            )
        object.__setattr__(self, "linear", linear)
        object.__setattr__(self, "angular_radians", angular)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "linear": self.linear,
            "angular_radians": self.angular_radians,
        }


@dataclass(frozen=True, slots=True)
class AffineTransform:
    """A row-major affine 4x4 transform."""

    matrix: tuple[float, ...]

    SCHEMA = "AffineTransform@1"

    def __post_init__(self) -> None:
        if not isinstance(self.matrix, tuple) or len(self.matrix) != 16:
            raise GeometryProgramError("transform matrix must contain 16 values")
        normalized = tuple(
            _finite(item, "transform matrix value") for item in self.matrix
        )
        if normalized[12:] != (0.0, 0.0, 0.0, 1.0):
            raise GeometryProgramError("transform matrix must be affine")
        object.__setattr__(self, "matrix", normalized)

    @classmethod
    def identity(cls) -> AffineTransform:
        return cls(
            (
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "matrix": list(self.matrix)}


@dataclass(frozen=True, slots=True)
class CoordinateFrame:
    frame_id: str
    parent_frame_id: str | None
    transform_from_parent: AffineTransform
    source_refs: tuple[str, ...]

    SCHEMA = "CoordinateFrame@1"

    def __post_init__(self) -> None:
        require_identifier(self.frame_id, "frame_id")
        if self.parent_frame_id is not None:
            require_identifier(self.parent_frame_id, "parent_frame_id")
            if self.parent_frame_id == self.frame_id:
                raise GeometryProgramError("a frame cannot parent itself")
        if not isinstance(self.transform_from_parent, AffineTransform):
            raise TypeError("transform_from_parent must be AffineTransform")
        _refs(self.source_refs, "frame source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "frame_id": self.frame_id,
            "parent_frame_id": self.parent_frame_id,
            "transform_from_parent": self.transform_from_parent.to_dict(),
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True, slots=True)
class GeometryParameter:
    name: str
    kind: GeometryParameterKind
    value_json: str
    unit: LengthUnit | None = None

    SCHEMA = "GeometryParameter@1"

    def __post_init__(self) -> None:
        require_identifier(self.name, "geometry parameter name")
        if not isinstance(self.kind, GeometryParameterKind):
            raise TypeError("kind must be GeometryParameterKind")
        if not isinstance(self.value_json, str):
            raise TypeError("value_json must be text")
        try:
            value = json.loads(self.value_json)
        except json.JSONDecodeError as exc:
            raise GeometryProgramError(
                "geometry parameter must contain JSON"
            ) from exc
        if canonical_json(value) != self.value_json:
            raise GeometryProgramError(
                "geometry parameter JSON must be canonical"
            )
        if self.unit is not None and not isinstance(self.unit, LengthUnit):
            raise TypeError("unit must be LengthUnit or None")
        self._validate_value(value)
        if self.kind is GeometryParameterKind.NUMBER:
            value = float(value)
        elif self.kind is GeometryParameterKind.MATRIX4:
            value = [float(item) for item in value]
        elif self.kind is GeometryParameterKind.VECTOR3:
            value = [float(item) for item in value]
        elif self.kind is GeometryParameterKind.POINTS3:
            value = [
                [float(coordinate) for coordinate in point]
                for point in value
            ]
        object.__setattr__(self, "value_json", canonical_json(value))

    @classmethod
    def create(
        cls,
        *,
        name: str,
        kind: GeometryParameterKind,
        value: object,
        unit: LengthUnit | None = None,
    ) -> GeometryParameter:
        return cls(
            name=name,
            kind=kind,
            value_json=canonical_json(value),
            unit=unit,
        )

    def _validate_value(self, value: object) -> None:
        if self.kind is GeometryParameterKind.NUMBER:
            _finite(value, self.name)
        elif self.kind is GeometryParameterKind.INTEGER:
            if not isinstance(value, int) or isinstance(value, bool):
                raise GeometryProgramError(f"{self.name} must be an integer")
        elif self.kind is GeometryParameterKind.BOOLEAN:
            if not isinstance(value, bool):
                raise GeometryProgramError(f"{self.name} must be a boolean")
        elif self.kind is GeometryParameterKind.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise GeometryProgramError(
                    f"{self.name} must be non-empty text"
                )
        elif self.kind is GeometryParameterKind.VECTOR3:
            self._validate_vector(value)
        elif self.kind is GeometryParameterKind.POINTS3:
            if not isinstance(value, list) or not value:
                raise GeometryProgramError(
                    f"{self.name} must be a non-empty point list"
                )
            for point in value:
                self._validate_vector(point)
        elif self.kind is GeometryParameterKind.MATRIX4:
            if not isinstance(value, list) or len(value) != 16:
                raise GeometryProgramError(
                    f"{self.name} must contain 16 matrix values"
                )
            for item in value:
                _finite(item, self.name)
            if tuple(float(item) for item in value[12:]) != (
                0.0,
                0.0,
                0.0,
                1.0,
            ):
                raise GeometryProgramError(
                    f"{self.name} must be an affine matrix"
                )

    def _validate_vector(self, value: object) -> None:
        if not isinstance(value, list) or len(value) != 3:
            raise GeometryProgramError(f"{self.name} must be a 3-vector")
        for item in value:
            _finite(item, self.name)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "name": self.name,
            "kind": self.kind.value,
            "value_json": self.value_json,
            "unit": self.unit.value if self.unit is not None else None,
        }


@dataclass(frozen=True, slots=True)
class SemanticBinding:
    binding_id: str
    component_id: str
    object_ids: tuple[str, ...]
    commitment_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "GeometrySemanticBinding@2"

    def __post_init__(self) -> None:
        require_identifier(self.binding_id, "binding_id")
        require_identifier(self.component_id, "component_id")
        _ids(self.object_ids, "binding object_ids")
        _refs(
            self.commitment_refs,
            "binding commitment_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "binding evidence_refs")
        if not self.commitment_refs and not self.evidence_refs:
            raise GeometryProgramError(
                "semantic binding needs commitment or evidence authority"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding_id": self.binding_id,
            "component_id": self.component_id,
            "object_ids": list(self.object_ids),
            "commitment_refs": list(self.commitment_refs),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class AssetReference:
    asset_id: str
    uri: str
    media_type: str
    sha256: str
    native_unit: LengthUnit
    sockets: tuple[str, ...]
    provenance_refs: tuple[str, ...]

    SCHEMA = "GeometryAssetReference@1"

    def __post_init__(self) -> None:
        require_identifier(self.asset_id, "asset_id")
        if (
            not isinstance(self.uri, str)
            or _URI_SCHEME.match(self.uri) is None
            or self.uri.lower().startswith("file://")
        ):
            raise GeometryProgramError(
                "asset uri must be stable scheme-qualified text"
            )
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise GeometryProgramError("asset media_type must be non-empty")
        object.__setattr__(
            self,
            "sha256",
            require_sha256(self.sha256, "asset sha256"),
        )
        if not isinstance(self.native_unit, LengthUnit):
            raise TypeError("native_unit must be LengthUnit")
        _ids(self.sockets, "asset sockets")
        _refs(self.provenance_refs, "asset provenance_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "asset_id": self.asset_id,
            "uri": self.uri,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "native_unit": self.native_unit.value,
            "sockets": list(self.sockets),
            "provenance_refs": list(self.provenance_refs),
        }


@dataclass(frozen=True, slots=True)
class GeometryOperation:
    op_id: str
    kind: GeometryOperationKind
    output_object_ids: tuple[str, ...]
    input_object_ids: tuple[str, ...]
    frame_id: str
    parameters: tuple[GeometryParameter, ...]
    semantic_binding_ids: tuple[str, ...]
    asset_id: str | None = None
    asset_socket_id: str | None = None
    asset_scale: tuple[float, float, float] | None = None
    responds_to_object_ids: tuple[str, ...] = ()
    responds_to_frame_ids: tuple[str, ...] = ()
    responds_to_binding_ids: tuple[str, ...] = ()

    SCHEMA = "GeometryOperation@1"

    def __post_init__(self) -> None:
        require_identifier(self.op_id, "op_id")
        if not isinstance(self.kind, GeometryOperationKind):
            raise TypeError("kind must be GeometryOperationKind")
        _ids(self.output_object_ids, "operation output_object_ids")
        _ids(
            self.input_object_ids,
            "operation input_object_ids",
            allow_empty=True,
        )
        if set(self.output_object_ids) & set(self.input_object_ids):
            raise GeometryProgramError(
                "an operation cannot consume its own output identity"
            )
        require_identifier(self.frame_id, "operation frame_id")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(item, GeometryParameter)
            for item in self.parameters
        ):
            raise TypeError("parameters contains an invalid item")
        names = tuple(item.name for item in self.parameters)
        if names != tuple(sorted(set(names))):
            raise GeometryProgramError(
                "parameters require unique deterministic names"
            )
        _ids(self.semantic_binding_ids, "operation semantic_binding_ids")
        _ids(
            self.responds_to_object_ids,
            "responds_to_object_ids",
            allow_empty=True,
        )
        _ids(
            self.responds_to_frame_ids,
            "responds_to_frame_ids",
            allow_empty=True,
        )
        _ids(
            self.responds_to_binding_ids,
            "responds_to_binding_ids",
            allow_empty=True,
        )
        if not set(self.responds_to_object_ids) <= set(self.input_object_ids):
            raise GeometryProgramError(
                "object responses must name operation inputs"
            )
        if not set(self.responds_to_binding_ids) <= set(
            self.semantic_binding_ids
        ):
            raise GeometryProgramError(
                "binding responses must name operation semantic bindings"
            )
        if self.kind is GeometryOperationKind.ASSET_INSTANCE:
            if self.asset_id is None or self.asset_socket_id is None:
                raise GeometryProgramError(
                    "asset instance operation requires asset and socket ids"
                )
            require_identifier(self.asset_id, "operation asset_id")
            require_identifier(
                self.asset_socket_id,
                "operation asset_socket_id",
            )
            if (
                not isinstance(self.asset_scale, tuple)
                or len(self.asset_scale) != 3
            ):
                raise GeometryProgramError(
                    "asset instance operation requires explicit 3-axis scale"
                )
            scale = tuple(
                _finite(item, "asset scale") for item in self.asset_scale
            )
            if any(item <= 0 for item in scale):
                raise GeometryProgramError("asset scale must be positive")
            object.__setattr__(self, "asset_scale", scale)
        elif (
            self.asset_id is not None
            or self.asset_socket_id is not None
            or self.asset_scale is not None
        ):
            raise GeometryProgramError(
                "only an asset instance operation may name asset placement"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "op_id": self.op_id,
            "kind": self.kind.value,
            "output_object_ids": list(self.output_object_ids),
            "input_object_ids": list(self.input_object_ids),
            "frame_id": self.frame_id,
            "parameters": [item.to_dict() for item in self.parameters],
            "semantic_binding_ids": list(self.semantic_binding_ids),
            "asset_id": self.asset_id,
            "asset_socket_id": self.asset_socket_id,
            "asset_scale": (
                list(self.asset_scale)
                if self.asset_scale is not None
                else None
            ),
            "responds_to_object_ids": list(self.responds_to_object_ids),
            "responds_to_frame_ids": list(self.responds_to_frame_ids),
            "responds_to_binding_ids": list(self.responds_to_binding_ids),
        }


@dataclass(frozen=True, slots=True)
class AssemblyMember:
    role: AssemblyRole
    object_ids: tuple[str, ...]

    SCHEMA = "AssemblyMember@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, AssemblyRole):
            raise TypeError("role must be AssemblyRole")
        _ids(self.object_ids, "assembly member object_ids")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "object_ids": list(self.object_ids),
        }


_REQUIRED_ASSEMBLY_ROLES = {
    AssemblyKind.DOOR: frozenset(
        {
            AssemblyRole.HOST_CUT,
            AssemblyRole.FRAME,
            AssemblyRole.LEAF,
            AssemblyRole.HARDWARE,
            AssemblyRole.CLEARANCE,
        }
    ),
    AssemblyKind.WINDOW: frozenset(
        {
            AssemblyRole.HOST_CUT,
            AssemblyRole.FRAME,
            AssemblyRole.GLAZING,
            AssemblyRole.HARDWARE,
            AssemblyRole.CLEARANCE,
        }
    ),
}


def required_assembly_roles(
    kind: AssemblyKind,
) -> tuple[AssemblyRole, ...]:
    """Return the canonical protocol roles required by one assembly kind."""

    if not isinstance(kind, AssemblyKind):
        raise TypeError("kind must be AssemblyKind")
    return tuple(
        sorted(
            _REQUIRED_ASSEMBLY_ROLES.get(kind, frozenset()),
            key=lambda item: item.value,
        )
    )


@dataclass(frozen=True, slots=True)
class HostedAssembly:
    assembly_id: str
    kind: AssemblyKind
    host_object_id: str
    host_socket_id: str
    members: tuple[AssemblyMember, ...]
    interface_refs: tuple[str, ...]
    semantic_binding_ids: tuple[str, ...]
    maturity: DetailMaturity

    SCHEMA = "HostedAssembly@1"

    def __post_init__(self) -> None:
        require_identifier(self.assembly_id, "assembly_id")
        if not isinstance(self.kind, AssemblyKind):
            raise TypeError("kind must be AssemblyKind")
        require_identifier(self.host_object_id, "host_object_id")
        require_identifier(self.host_socket_id, "host_socket_id")
        if not isinstance(self.members, tuple) or not self.members:
            raise GeometryProgramError("assembly members must be non-empty")
        if any(not isinstance(item, AssemblyMember) for item in self.members):
            raise TypeError("members contains an invalid item")
        roles = tuple(item.role for item in self.members)
        if roles != tuple(sorted(set(roles), key=lambda item: item.value)):
            raise GeometryProgramError(
                "assembly members require unique deterministic roles"
            )
        required = set(required_assembly_roles(self.kind))
        missing = required - set(roles)
        if missing:
            raise GeometryProgramError(
                "hosted assembly lacks required roles: "
                + ", ".join(sorted(item.value for item in missing))
            )
        member_objects = {
            object_id for member in self.members for object_id in member.object_ids
        }
        if self.host_object_id in member_objects:
            raise GeometryProgramError(
                "host object must remain distinct from assembly members"
            )
        _refs(self.interface_refs, "assembly interface_refs")
        _ids(self.semantic_binding_ids, "assembly semantic_binding_ids")
        if not isinstance(self.maturity, DetailMaturity):
            raise TypeError("maturity must be DetailMaturity")

    def objects_for(self, role: AssemblyRole) -> tuple[str, ...]:
        for member in self.members:
            if member.role is role:
                return member.object_ids
        return ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assembly_id": self.assembly_id,
            "kind": self.kind.value,
            "host_object_id": self.host_object_id,
            "host_socket_id": self.host_socket_id,
            "members": [item.to_dict() for item in self.members],
            "interface_refs": list(self.interface_refs),
            "semantic_binding_ids": list(self.semantic_binding_ids),
            "maturity": self.maturity.value,
        }


@dataclass(frozen=True, slots=True)
class ObjectRevisionPrecondition:
    object_id: str
    expected_digest: str
    reason_refs: tuple[str, ...]

    SCHEMA = "ObjectRevisionPrecondition@1"

    def __post_init__(self) -> None:
        require_identifier(self.object_id, "revision object_id")
        object.__setattr__(
            self,
            "expected_digest",
            require_sha256(
                self.expected_digest,
                "revision expected_digest",
            ),
        )
        _refs(self.reason_refs, "revision reason_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "object_id": self.object_id,
            "expected_digest": self.expected_digest,
            "reason_refs": list(self.reason_refs),
        }


@dataclass(frozen=True, slots=True)
class ObjectRetirement:
    object_id: str
    expected_digest: str
    reason_refs: tuple[str, ...]

    SCHEMA = "ObjectRetirement@1"

    def __post_init__(self) -> None:
        require_identifier(self.object_id, "retirement object_id")
        object.__setattr__(
            self,
            "expected_digest",
            require_sha256(
                self.expected_digest,
                "retirement expected_digest",
            ),
        )
        _refs(self.reason_refs, "retirement reason_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "object_id": self.object_id,
            "expected_digest": self.expected_digest,
            "reason_refs": list(self.reason_refs),
        }


class InterfaceDatumKind(StrEnum):
    LEVEL = "level"
    PLANE = "plane"
    SERIES = "series"


@dataclass(frozen=True, slots=True)
class InterfaceDatum:
    """One published interface value dependents co-derive from (P090).

    A host component publishes a named datum — an elevation, a plane, or
    an arithmetic series — and dependent operations bind parameters to
    the same value node instead of restating coordinates.  Contact then
    holds by construction and is verified on the derivation graph, not
    by measuring realized geometry for positive overlap.
    """

    datum_id: str
    kind: InterfaceDatumKind
    published_by: str
    value_json: str
    unit: LengthUnit | None = None
    basis_refs: tuple[str, ...] = ()

    SCHEMA = "InterfaceDatum@1"

    def __post_init__(self) -> None:
        require_identifier(self.datum_id, "datum_id")
        if not isinstance(self.kind, InterfaceDatumKind):
            raise TypeError("kind must be InterfaceDatumKind")
        require_identifier(self.published_by, "datum published_by")
        if not isinstance(self.value_json, str):
            raise TypeError("datum value_json must be text")
        try:
            value = json.loads(self.value_json)
        except json.JSONDecodeError as exc:
            raise GeometryProgramError(
                "datum value must contain JSON"
            ) from exc
        if canonical_json(value) != self.value_json:
            raise GeometryProgramError("datum value JSON must be canonical")
        if self.unit is not None and not isinstance(self.unit, LengthUnit):
            raise TypeError("datum unit must be LengthUnit or None")
        object.__setattr__(
            self,
            "basis_refs",
            _refs(self.basis_refs, "datum basis_refs", allow_empty=True),
        )
        normalized = self._validated_value(value)
        object.__setattr__(self, "value_json", canonical_json(normalized))

    @classmethod
    def create(
        cls,
        *,
        datum_id: str,
        kind: InterfaceDatumKind,
        published_by: str,
        value: object,
        unit: LengthUnit | None = None,
        basis_refs: tuple[str, ...] = (),
    ) -> InterfaceDatum:
        return cls(
            datum_id=datum_id,
            kind=kind,
            published_by=published_by,
            value_json=canonical_json(value),
            unit=unit,
            basis_refs=basis_refs,
        )

    def _validated_value(self, value: object) -> object:
        if self.kind is InterfaceDatumKind.LEVEL:
            return _finite(value, f"datum {self.datum_id} level")
        if self.kind is InterfaceDatumKind.PLANE:
            if not isinstance(value, dict) or set(value) != {
                "origin",
                "normal",
            }:
                raise GeometryProgramError(
                    f"datum {self.datum_id} plane requires origin and normal"
                )
            origin = self._vector(value["origin"], "plane origin")
            normal = self._vector(value["normal"], "plane normal")
            if all(item == 0.0 for item in normal):
                raise GeometryProgramError(
                    f"datum {self.datum_id} plane normal must be nonzero"
                )
            return {"normal": normal, "origin": origin}
        if self.kind is InterfaceDatumKind.SERIES:
            if not isinstance(value, dict) or set(value) != {
                "start",
                "step",
                "count",
            }:
                raise GeometryProgramError(
                    f"datum {self.datum_id} series requires start, step, count"
                )
            count = value["count"]
            if isinstance(count, bool) or not isinstance(count, int):
                raise GeometryProgramError(
                    f"datum {self.datum_id} series count must be an integer"
                )
            if count < 1:
                raise GeometryProgramError(
                    f"datum {self.datum_id} series count must be positive"
                )
            return {
                "count": count,
                "start": _finite(value["start"], "series start"),
                "step": _finite(value["step"], "series step"),
            }
        raise GeometryProgramError(
            f"datum {self.datum_id} has an unsupported kind"
        )

    def _vector(self, value: object, field: str) -> list[float]:
        if not isinstance(value, list) or len(value) != 3:
            raise GeometryProgramError(
                f"datum {self.datum_id} {field} must be a 3-vector"
            )
        return [_finite(item, field) for item in value]

    def resolve(
        self,
        component: str | int | None,
    ) -> tuple[GeometryParameterKind, object]:
        """Return the parameter kind and value one binding derives.

        LEVEL takes no component; PLANE takes ``origin`` or ``normal``;
        SERIES takes an index inside its count.  Anything else fails
        closed — a binding may only consume what the datum publishes.
        """

        value = json.loads(self.value_json)
        if self.kind is InterfaceDatumKind.LEVEL:
            if component is not None:
                raise GeometryProgramError(
                    f"datum {self.datum_id} level takes no component"
                )
            return GeometryParameterKind.NUMBER, value
        if self.kind is InterfaceDatumKind.PLANE:
            if component not in ("origin", "normal"):
                raise GeometryProgramError(
                    f"datum {self.datum_id} plane component must be "
                    "origin or normal"
                )
            return GeometryParameterKind.VECTOR3, value[component]
        if isinstance(component, bool) or not isinstance(component, int):
            raise GeometryProgramError(
                f"datum {self.datum_id} series requires an integer index"
            )
        if not 0 <= component < value["count"]:
            raise GeometryProgramError(
                f"datum {self.datum_id} series index {component} is outside "
                f"count {value['count']}"
            )
        return (
            GeometryParameterKind.NUMBER,
            value["start"] + value["step"] * component,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "datum_id": self.datum_id,
            "kind": self.kind.value,
            "published_by": self.published_by,
            "value_json": self.value_json,
            "unit": self.unit.value if self.unit is not None else None,
            "basis_refs": list(self.basis_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> InterfaceDatum:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "datum_id",
            "kind",
            "published_by",
            "value_json",
            "unit",
            "basis_refs",
        }:
            raise GeometryProgramError("interface datum payload malformed")
        if value["schema"] != cls.SCHEMA:
            raise GeometryProgramError("interface datum schema changed")
        return cls(
            datum_id=value["datum_id"],
            kind=InterfaceDatumKind(value["kind"]),
            published_by=value["published_by"],
            value_json=value["value_json"],
            unit=(
                LengthUnit(value["unit"]) if value["unit"] is not None else None
            ),
            basis_refs=tuple(value["basis_refs"]),
        )


@dataclass(frozen=True, slots=True)
class DatumBinding:
    """Bind one operation parameter to a published interface datum.

    The bound parameter must not also appear as a literal on the
    operation — derived, not restated, lowered to the coordinate level.
    """

    binding_id: str
    datum_id: str
    op_id: str
    parameter_name: str
    component: str | int | None = None

    SCHEMA = "DatumBinding@1"

    def __post_init__(self) -> None:
        require_identifier(self.binding_id, "datum binding_id")
        require_identifier(self.datum_id, "binding datum_id")
        require_identifier(self.op_id, "binding op_id")
        require_identifier(self.parameter_name, "binding parameter_name")
        if self.component is None:
            return
        if isinstance(self.component, bool):
            raise TypeError("binding component must not be a boolean")
        if isinstance(self.component, int):
            if self.component < 0:
                raise GeometryProgramError(
                    "binding component index must be non-negative"
                )
            return
        if not isinstance(self.component, str) or not self.component:
            raise TypeError(
                "binding component must be None, an index, or non-empty text"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding_id": self.binding_id,
            "datum_id": self.datum_id,
            "op_id": self.op_id,
            "parameter_name": self.parameter_name,
            "component": self.component,
        }

    @classmethod
    def from_dict(cls, value: object) -> DatumBinding:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "binding_id",
            "datum_id",
            "op_id",
            "parameter_name",
            "component",
        }:
            raise GeometryProgramError("datum binding payload malformed")
        if value["schema"] != cls.SCHEMA:
            raise GeometryProgramError("datum binding schema changed")
        return cls(
            binding_id=value["binding_id"],
            datum_id=value["datum_id"],
            op_id=value["op_id"],
            parameter_name=value["parameter_name"],
            component=value["component"],
        )


def verify_datum_directions(
    datums: tuple[InterfaceDatum, ...],
    bindings: tuple[DatumBinding, ...],
    operations: tuple[GeometryOperation, ...],
    permitted_edges: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    """Check publication/consumption direction against declared edges.

    ``permitted_edges`` are ``(publisher_object_id, consumer_object_id)``
    pairs taken from declared SUPPORT/HOST-family relations.  A binding
    is in direction when the datum publisher is one of the consuming
    operation's own outputs (a component may consume its own datum) or a
    declared edge runs from the publisher to one consumed output.
    Returns sorted violation messages; empty means in direction.
    """

    datum_by_id = {item.datum_id: item for item in datums}
    op_by_id = {item.op_id: item for item in operations}
    edges = set()
    for edge in permitted_edges:
        publisher, consumer = edge
        require_identifier(publisher, "edge publisher")
        require_identifier(consumer, "edge consumer")
        edges.add((publisher, consumer))
    violations: list[str] = []
    for binding in bindings:
        datum = datum_by_id.get(binding.datum_id)
        if datum is None:
            violations.append(
                f"{binding.binding_id}: unknown datum {binding.datum_id}"
            )
            continue
        operation = op_by_id.get(binding.op_id)
        if operation is None:
            violations.append(
                f"{binding.binding_id}: unknown operation {binding.op_id}"
            )
            continue
        consumers = set(operation.output_object_ids)
        if datum.published_by in consumers:
            continue
        if any(
            (datum.published_by, consumer) in edges for consumer in consumers
        ):
            continue
        violations.append(
            f"{binding.binding_id}: no declared edge from "
            f"{datum.published_by} to any of "
            f"{', '.join(sorted(consumers))}"
        )
    return tuple(sorted(violations))


@dataclass(frozen=True, slots=True)
class GeometryProgramProposal:
    proposal_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    design_state_digest: str
    predecessor_program_digest: str | None
    length_unit: LengthUnit
    tolerance: GeometryTolerance
    frames: tuple[CoordinateFrame, ...]
    assets: tuple[AssetReference, ...]
    semantic_bindings: tuple[SemanticBinding, ...]
    operations: tuple[GeometryOperation, ...]
    assemblies: tuple[HostedAssembly, ...]
    revisions: tuple[ObjectRevisionPrecondition, ...] = ()
    retirements: tuple[ObjectRetirement, ...] = ()

    SCHEMA = "GeometryProgramProposal@2"

    def __post_init__(self) -> None:
        require_identifier(self.proposal_id, "proposal_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise GeometryProgramError("proposal and base disagree")
        object.__setattr__(
            self,
            "design_state_digest",
            require_sha256(
                self.design_state_digest,
                "design_state_digest",
            ),
        )
        if self.predecessor_program_digest is not None:
            object.__setattr__(
                self,
                "predecessor_program_digest",
                require_sha256(
                    self.predecessor_program_digest,
                    "predecessor_program_digest",
                ),
            )
        if not isinstance(self.length_unit, LengthUnit):
            raise TypeError("length_unit must be LengthUnit")
        if not isinstance(self.tolerance, GeometryTolerance):
            raise TypeError("tolerance must be GeometryTolerance")
        self._ordered_items(self.frames, CoordinateFrame, "frames", "frame_id")
        self._ordered_items(
            self.assets,
            AssetReference,
            "assets",
            "asset_id",
            allow_empty=True,
        )
        self._ordered_items(
            self.semantic_bindings,
            SemanticBinding,
            "semantic_bindings",
            "binding_id",
        )
        self._ordered_items(
            self.operations,
            GeometryOperation,
            "operations",
            "op_id",
        )
        self._ordered_items(
            self.assemblies,
            HostedAssembly,
            "assemblies",
            "assembly_id",
            allow_empty=True,
        )
        self._ordered_items(
            self.revisions,
            ObjectRevisionPrecondition,
            "revisions",
            "object_id",
            allow_empty=True,
        )
        self._ordered_items(
            self.retirements,
            ObjectRetirement,
            "retirements",
            "object_id",
            allow_empty=True,
        )
        if set(item.object_id for item in self.revisions) & set(
            item.object_id for item in self.retirements
        ):
            raise GeometryProgramError(
                "an object cannot be revised and retired together"
            )

    @staticmethod
    def _ordered_items(
        values: object,
        item_type: type,
        field: str,
        id_field: str,
        *,
        allow_empty: bool = False,
    ) -> None:
        if not isinstance(values, tuple) or (
            not values and not allow_empty
        ):
            raise GeometryProgramError(f"{field} must be a non-empty tuple")
        if any(not isinstance(item, item_type) for item in values):
            raise TypeError(f"{field} contains an invalid item")
        ids = tuple(getattr(item, id_field) for item in values)
        if ids != tuple(sorted(set(ids))):
            raise GeometryProgramError(
                f"{field} requires unique deterministic identities"
            )

    @property
    def proposal_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "proposal_id": self.proposal_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "design_state_digest": self.design_state_digest,
            "predecessor_program_digest": self.predecessor_program_digest,
            "length_unit": self.length_unit.value,
            "tolerance": self.tolerance.to_dict(),
            "frames": [item.to_dict() for item in self.frames],
            "assets": [item.to_dict() for item in self.assets],
            "semantic_bindings": [
                item.to_dict() for item in self.semantic_bindings
            ],
            "operations": [item.to_dict() for item in self.operations],
            "assemblies": [item.to_dict() for item in self.assemblies],
            "revisions": [item.to_dict() for item in self.revisions],
            "retirements": [item.to_dict() for item in self.retirements],
            "generation_authority": False,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }


# ---------------------------------------------------------------- P098
# Project levels and grids: the storeys and axes every element of one
# building references. Published once per run by the coordination seat;
# seats derive their LEVEL / PLANE datums from them instead of restating
# elevations per object and per side. A level carries its evidence: the
# kernel never defaults one.


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryProgramError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryProgramError(f"{field} must be finite")
    return number


def _basis(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise GeometryProgramError(f"{field} requires at least one basis ref")
    if tuple(sorted(set(value))) != value:
        raise GeometryProgramError(f"{field} basis_refs must be sorted and unique")
    return value


def _plan_vector(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise GeometryProgramError(f"{field} must be a 3-vector")
    vector = tuple(_finite_float(item, field) for item in value)
    return (vector[0], vector[1], vector[2])


@dataclass(frozen=True, slots=True)
class ProjectLevel:
    """One storey or building datum: an elevation with its evidence."""

    level_id: str
    role: str
    elevation: float
    basis_refs: tuple[str, ...]

    SCHEMA = "ProjectLevel@1"

    def __post_init__(self) -> None:
        require_identifier(self.level_id, "level_id")
        require_identifier(self.role, "level role")
        object.__setattr__(self, "elevation", _finite_float(self.elevation, f"level {self.level_id} elevation"))
        _basis(self.basis_refs, f"level {self.level_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "level_id": self.level_id,
            "role": self.role,
            "elevation": self.elevation,
            "basis_refs": list(self.basis_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectLevel:
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise GeometryProgramError("project level payload malformed")
        return cls(
            level_id=value["level_id"],
            role=value["role"],
            elevation=value["elevation"],
            basis_refs=tuple(value["basis_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ProjectGridAxis:
    """One plan axis: a vertical plane through a line in plan."""

    axis_id: str
    role: str
    origin: tuple[float, float, float]
    direction: tuple[float, float, float]
    basis_refs: tuple[str, ...]

    SCHEMA = "ProjectGridAxis@1"

    def __post_init__(self) -> None:
        require_identifier(self.axis_id, "axis_id")
        require_identifier(self.role, "axis role")
        object.__setattr__(self, "origin", _plan_vector(self.origin, f"axis {self.axis_id} origin"))
        direction = _plan_vector(self.direction, f"axis {self.axis_id} direction")
        if direction[1] != 0.0:
            raise GeometryProgramError(f"axis {self.axis_id} direction must lie in plan (Y is up)")
        if direction[0] == 0.0 and direction[2] == 0.0:
            raise GeometryProgramError(f"axis {self.axis_id} direction must be nonzero")
        object.__setattr__(self, "direction", direction)
        _basis(self.basis_refs, f"axis {self.axis_id}")

    @property
    def normal(self) -> tuple[float, float, float]:
        """Unit normal of the vertical plane containing the axis."""

        dx, _, dz = self.direction
        length = math.hypot(dx, dz)
        return (dz / length, 0.0, -dx / length)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "axis_id": self.axis_id,
            "role": self.role,
            "origin": list(self.origin),
            "direction": list(self.direction),
            "basis_refs": list(self.basis_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectGridAxis:
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise GeometryProgramError("project grid axis payload malformed")
        return cls(
            axis_id=value["axis_id"],
            role=value["role"],
            origin=tuple(value["origin"]),
            direction=tuple(value["direction"]),
            basis_refs=tuple(value["basis_refs"]),
        )


def _unique_roles(items, id_field: str, label: str) -> None:
    ids = [getattr(item, id_field) for item in items]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise GeometryProgramError(f"{label} must be sorted by id and unique")
    roles = [item.role for item in items]
    if len(set(roles)) != len(roles):
        raise GeometryProgramError(f"{label} roles must be unique")


@dataclass(frozen=True, slots=True)
class ProjectLevels:
    """The storeys of one run, published once by the coordination seat."""

    project_id: str
    published_by: str
    levels: tuple[ProjectLevel, ...]

    SCHEMA = "ProjectLevels@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.published_by, "levels published_by")
        if not isinstance(self.levels, tuple) or not self.levels or any(
            not isinstance(item, ProjectLevel) for item in self.levels
        ):
            raise GeometryProgramError("levels must be a non-empty tuple of ProjectLevel")
        _unique_roles(self.levels, "level_id", "project levels")

    def level(self, role: str) -> ProjectLevel:
        for item in self.levels:
            if item.role == role:
                return item
        raise GeometryProgramError(f"project has no level with role {role!r}")

    @property
    def datum_ids(self) -> tuple[str, ...]:
        return tuple(item.level_id for item in self.levels)

    def datum(self, role: str) -> InterfaceDatum:
        """The LEVEL datum every consumer of this storey binds to."""

        item = self.level(role)
        return InterfaceDatum.create(
            datum_id=item.level_id,
            kind=InterfaceDatumKind.LEVEL,
            published_by=self.published_by,
            value=item.elevation,
            unit=LengthUnit.METER,
            basis_refs=item.basis_refs,
        )

    def datums(self) -> tuple[InterfaceDatum, ...]:
        return tuple(self.datum(item.role) for item in self.levels)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "published_by": self.published_by,
            "levels": [item.to_dict() for item in self.levels],
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectLevels:
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise GeometryProgramError("project levels payload malformed")
        return cls(
            project_id=value["project_id"],
            published_by=value["published_by"],
            levels=tuple(ProjectLevel.from_dict(item) for item in value["levels"]),
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectGrids:
    """The plan axes of one run, published once by the coordination seat."""

    project_id: str
    published_by: str
    axes: tuple[ProjectGridAxis, ...]

    SCHEMA = "ProjectGrids@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.published_by, "grids published_by")
        if not isinstance(self.axes, tuple) or not self.axes or any(
            not isinstance(item, ProjectGridAxis) for item in self.axes
        ):
            raise GeometryProgramError("axes must be a non-empty tuple of ProjectGridAxis")
        _unique_roles(self.axes, "axis_id", "project grid axes")

    def axis(self, role: str) -> ProjectGridAxis:
        for item in self.axes:
            if item.role == role:
                return item
        raise GeometryProgramError(f"project has no grid axis with role {role!r}")

    @property
    def datum_ids(self) -> tuple[str, ...]:
        return tuple(item.axis_id for item in self.axes)

    def datum(self, role: str) -> InterfaceDatum:
        item = self.axis(role)
        return InterfaceDatum.create(
            datum_id=item.axis_id,
            kind=InterfaceDatumKind.PLANE,
            published_by=self.published_by,
            value={"origin": list(item.origin), "normal": list(item.normal)},
            unit=LengthUnit.METER,
            basis_refs=item.basis_refs,
        )

    def datums(self) -> tuple[InterfaceDatum, ...]:
        return tuple(self.datum(item.role) for item in self.axes)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "published_by": self.published_by,
            "axes": [item.to_dict() for item in self.axes],
        }

    @classmethod
    def from_dict(cls, value: object) -> ProjectGrids:
        if not isinstance(value, dict) or value.get("schema") != cls.SCHEMA:
            raise GeometryProgramError("project grids payload malformed")
        return cls(
            project_id=value["project_id"],
            published_by=value["published_by"],
            axes=tuple(ProjectGridAxis.from_dict(item) for item in value["axes"]),
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def verify_project_datums(
    datums: tuple[InterfaceDatum, ...],
    levels: ProjectLevels | None = None,
    grids: ProjectGrids | None = None,
) -> tuple[str, ...]:
    """Check seat-published datums against the project levels and grids.

    A seat datum that names a project level or axis id must be that
    datum: same kind, same value, same publisher. Anything else is a
    seat overwriting a project datum. Returns sorted violation messages;
    empty means every named level or axis is derived, not restated.
    """

    expected: dict[str, InterfaceDatum] = {}
    for record in (levels, grids):
        if record is None:
            continue
        for item in record.datums():
            expected[item.datum_id] = item
    violations: list[str] = []
    for datum in datums:
        if not isinstance(datum, InterfaceDatum):
            raise TypeError("datums must be InterfaceDatum items")
        reference = expected.get(datum.datum_id)
        if reference is None:
            continue
        if datum.kind is not reference.kind:
            violations.append(
                f"datum {datum.datum_id} restates a project {reference.kind.value} as a {datum.kind.value}"
            )
        elif datum.value_json != reference.value_json or datum.unit is not reference.unit:
            violations.append(
                f"datum {datum.datum_id} restates project value {reference.value_json} as {datum.value_json}"
            )
        elif datum.published_by != reference.published_by:
            violations.append(
                f"datum {datum.datum_id} is published by {datum.published_by}, not by {reference.published_by}"
            )
    return tuple(sorted(violations))

