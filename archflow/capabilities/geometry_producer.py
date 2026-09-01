"""Pure, authority-free seams for architectural geometry producers.

Project knowledge owns component identities, dimensions, maturity choices, and
evidence. A producer only translates one exact project-authored specification
into neutral geometry operations. These transient values choose no persistence
destination and cannot validate, accept, promote, or commit a design.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, TypeVar

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import deterministic_refs
from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.geometry_program import (
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    LengthUnit,
    SemanticBinding,
)


class GeometryProducerError(ValueError):
    """A producer input or result violates the detached producer contract."""


def authority_free_fields() -> dict[str, bool]:
    """Common explicit denial of lifecycle and external-effect authority."""

    return {
        "design_authority": False,
        "verification_authority": False,
        "hard_gate_authority": False,
        "stage_acceptance_authority": False,
        "execution_authority": False,
        "promotion_authority": False,
        "persistence_authority": False,
        "canonical_write_authority": False,
    }


def _sorted_ids(
    values: tuple[str, ...], field: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    for value in values:
        require_identifier(value, field)
    if not allow_empty and not values:
        raise GeometryProducerError(f"{field} must be non-empty")
    if values != tuple(sorted(set(values))):
        raise GeometryProducerError(f"{field} must be sorted and unique")
    return values


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise GeometryProducerError(f"{field} must be finite")
    return float(value)


@dataclass(frozen=True, slots=True)
class YUpPlacement:
    """Rigid translation and Y-axis rotation for local architectural geometry."""

    origin_x: float
    origin_y: float
    origin_z: float
    rotation_degrees: float

    SCHEMA = "YUpGeometryPlacement@1"

    def __post_init__(self) -> None:
        for field in (
            "origin_x",
            "origin_y",
            "origin_z",
            "rotation_degrees",
        ):
            object.__setattr__(
                self,
                field,
                _finite(getattr(self, field), field),
            )

    def transform_point(self, point: list[float]) -> list[float]:
        if not isinstance(point, list) or len(point) != 3:
            raise GeometryProducerError("point must contain three coordinates")
        x, y, z = (_finite(item, "point coordinate") for item in point)
        theta = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(theta), math.sin(theta)
        return [
            x * cosine + z * sine + self.origin_x,
            y + self.origin_y,
            z * cosine - x * sine + self.origin_z,
        ]

    def transform_vector(self, vector: list[float]) -> list[float]:
        if not isinstance(vector, list) or len(vector) != 3:
            raise GeometryProducerError("vector must contain three coordinates")
        x, y, z = (_finite(item, "vector coordinate") for item in vector)
        theta = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(theta), math.sin(theta)
        return [x * cosine + z * sine, y, z * cosine - x * sine]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "origin": [self.origin_x, self.origin_y, self.origin_z],
            "rotation_degrees": self.rotation_degrees,
        }


@dataclass(frozen=True, slots=True)
class ProducerContext:
    """Exact project/state context supplied to a detached producer."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    design_state_digest: str
    frame_id: str
    length_unit: LengthUnit
    commitment_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "GeometryProducerContext@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise GeometryProducerError("producer context and base disagree")
        self.base.require_digest()
        require_sha256(self.design_state_digest, "design_state_digest")
        require_identifier(self.frame_id, "frame_id")
        if not isinstance(self.length_unit, LengthUnit):
            raise TypeError("length_unit must be LengthUnit")
        object.__setattr__(
            self,
            "commitment_refs",
            deterministic_refs(self.commitment_refs, "commitment_refs"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            deterministic_refs(self.evidence_refs, "evidence_refs"),
        )

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.base.project_id,
                "version": self.base.version,
                "state_sha256": self.base.require_digest(),
            },
            "design_state_digest": self.design_state_digest,
            "frame_id": self.frame_id,
            "length_unit": self.length_unit.value,
            "commitment_refs": list(self.commitment_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @property
    def context_digest(self) -> str:
        return canonical_digest(self._identity())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "context_digest": self.context_digest,
            **authority_free_fields(),
        }


@dataclass(frozen=True, slots=True)
class ProducedComponentGeometry:
    """Object identities emitted for one existing semantic component."""

    component_id: str
    binding_id: str
    object_ids: tuple[str, ...]

    SCHEMA = "ProducedComponentGeometry@1"

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "component_id")
        require_identifier(self.binding_id, "binding_id")
        _sorted_ids(self.object_ids, "object_ids")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_id": self.component_id,
            "binding_id": self.binding_id,
            "object_ids": list(self.object_ids),
        }


@dataclass(frozen=True, slots=True)
class ProducedAssembly:
    """Deterministic operations emitted for one project-owned assembly spec."""

    producer_id: str
    assembly_id: str
    context_digest: str
    spec_digest: str
    operations: tuple[GeometryOperation, ...]
    components: tuple[ProducedComponentGeometry, ...]
    interface_refs: tuple[str, ...] = ()
    obligation_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    SCHEMA = "ProducedGeometryAssembly@1"

    def __post_init__(self) -> None:
        require_identifier(self.producer_id, "producer_id")
        require_identifier(self.assembly_id, "assembly_id")
        require_sha256(self.context_digest, "context_digest")
        require_sha256(self.spec_digest, "spec_digest")
        if not isinstance(self.operations, tuple) or not self.operations:
            raise GeometryProducerError("operations must be a non-empty tuple")
        if any(not isinstance(item, GeometryOperation) for item in self.operations):
            raise TypeError("operations contains an invalid item")
        operation_ids = tuple(item.op_id for item in self.operations)
        if operation_ids != tuple(sorted(set(operation_ids))):
            raise GeometryProducerError("operations must have sorted unique ids")
        output_ids = tuple(
            object_id
            for operation in self.operations
            for object_id in operation.output_object_ids
        )
        if len(output_ids) != len(set(output_ids)):
            raise GeometryProducerError("producer emitted duplicate object ids")
        if not isinstance(self.components, tuple) or not self.components:
            raise GeometryProducerError("components must be a non-empty tuple")
        if any(
            not isinstance(item, ProducedComponentGeometry)
            for item in self.components
        ):
            raise TypeError("components contains an invalid item")
        binding_ids = tuple(item.binding_id for item in self.components)
        if binding_ids != tuple(sorted(set(binding_ids))):
            raise GeometryProducerError(
                "components must have sorted unique binding ids"
            )
        known_outputs = set(output_ids)
        covered_outputs = {
            object_id
            for component in self.components
            for object_id in component.object_ids
        }
        if covered_outputs != known_outputs:
            raise GeometryProducerError(
                "component geometry must cover exactly the produced objects"
            )
        known_bindings = set(binding_ids)
        if any(
            not set(operation.semantic_binding_ids) <= known_bindings
            for operation in self.operations
        ):
            raise GeometryProducerError(
                "operation cites a binding absent from the produced components"
            )
        expected_objects_by_binding: dict[str, set[str]] = {
            binding_id: set() for binding_id in binding_ids
        }
        for operation in self.operations:
            for binding_id in operation.semantic_binding_ids:
                expected_objects_by_binding[binding_id].update(
                    operation.output_object_ids
                )
        actual_objects_by_binding = {
            component.binding_id: set(component.object_ids)
            for component in self.components
        }
        if actual_objects_by_binding != expected_objects_by_binding:
            raise GeometryProducerError(
                "component geometry must exactly match operation bindings"
            )
        for field in ("interface_refs", "obligation_refs", "source_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field), field, allow_empty=True
                ),
            )

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "producer_id": self.producer_id,
            "assembly_id": self.assembly_id,
            "context_digest": self.context_digest,
            "spec_digest": self.spec_digest,
            "operations": [item.to_dict() for item in self.operations],
            "components": [item.to_dict() for item in self.components],
            "interface_refs": list(self.interface_refs),
            "obligation_refs": list(self.obligation_refs),
            "source_refs": list(self.source_refs),
        }

    @property
    def assembly_digest(self) -> str:
        return canonical_digest(self._identity())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "assembly_digest": self.assembly_digest,
            **authority_free_fields(),
        }


class GeometryAssemblyBuilder:
    """Collect neutral operations without persistence or acceptance authority."""

    def __init__(self, context: ProducerContext) -> None:
        if not isinstance(context, ProducerContext):
            raise TypeError("context must be ProducerContext")
        self.context = context
        self._operations: list[GeometryOperation] = []
        self._component_objects: dict[str, list[str]] = {}

    def _parameters(
        self,
        rows: tuple[
            tuple[str, GeometryParameterKind, object, LengthUnit | None], ...
        ],
    ) -> tuple[GeometryParameter, ...]:
        return tuple(
            sorted(
                (
                    GeometryParameter.create(
                        name=name,
                        kind=kind,
                        value=value,
                        unit=unit,
                    )
                    for name, kind, value, unit in rows
                ),
                key=lambda item: item.name,
            )
        )

    def add_operation(
        self,
        *,
        op_id: str,
        kind: GeometryOperationKind,
        component_id: str,
        parameters: tuple[
            tuple[str, GeometryParameterKind, object, LengthUnit | None], ...
        ],
    ) -> None:
        object_id = f"obj-{op_id}"
        binding_id = f"binding-{component_id}"
        self._operations.append(
            GeometryOperation(
                op_id=op_id,
                kind=kind,
                output_object_ids=(object_id,),
                input_object_ids=(),
                frame_id=self.context.frame_id,
                parameters=self._parameters(parameters),
                semantic_binding_ids=(binding_id,),
            )
        )
        self._component_objects.setdefault(component_id, []).append(object_id)

    def extrusion(
        self,
        *,
        op_id: str,
        component_id: str,
        profile: list[list[float]],
        vector: list[float],
        placement: YUpPlacement,
        vector_is_world_aligned: bool = False,
    ) -> None:
        if not isinstance(vector_is_world_aligned, bool):
            raise TypeError("vector_is_world_aligned must be boolean")
        self.add_operation(
            op_id=op_id,
            kind=GeometryOperationKind.EXTRUSION,
            component_id=component_id,
            parameters=(
                (
                    "profile",
                    GeometryParameterKind.POINTS3,
                    [placement.transform_point(point) for point in profile],
                    self.context.length_unit,
                ),
                (
                    "vector",
                    GeometryParameterKind.VECTOR3,
                    (
                        list(vector)
                        if vector_is_world_aligned
                        else placement.transform_vector(vector)
                    ),
                    self.context.length_unit,
                ),
            ),
        )

    def loft(
        self,
        *,
        op_id: str,
        component_id: str,
        profiles: list[list[list[float]]],
        placement: YUpPlacement,
        cap_ends: bool,
        loft_type: str = "straight",
    ) -> None:
        if not profiles or any(len(ring) != len(profiles[0]) for ring in profiles):
            raise GeometryProducerError(
                "loft profiles must be non-empty and have equal point counts"
            )
        flat = [
            placement.transform_point(point)
            for ring in profiles
            for point in ring
        ]
        self.add_operation(
            op_id=op_id,
            kind=GeometryOperationKind.LOFT,
            component_id=component_id,
            parameters=(
                ("cap_ends", GeometryParameterKind.BOOLEAN, cap_ends, None),
                ("loft_type", GeometryParameterKind.TEXT, loft_type, None),
                (
                    "profile_size",
                    GeometryParameterKind.INTEGER,
                    len(profiles[0]),
                    None,
                ),
                (
                    "profiles",
                    GeometryParameterKind.POINTS3,
                    flat,
                    self.context.length_unit,
                ),
            ),
        )

    def rect_prism(
        self,
        *,
        op_id: str,
        component_id: str,
        center_x: float,
        center_z: float,
        width: float,
        depth: float,
        bottom: float,
        height: float,
        placement: YUpPlacement,
    ) -> None:
        for field, value in (
            ("center_x", center_x),
            ("center_z", center_z),
            ("bottom", bottom),
        ):
            _finite(value, field)
        for field, value in (
            ("width", width),
            ("depth", depth),
            ("height", height),
        ):
            if _finite(value, field) <= 0.0:
                raise GeometryProducerError(f"{field} must be positive")
        profile = [
            [center_x - width / 2.0, bottom, center_z - depth / 2.0],
            [center_x + width / 2.0, bottom, center_z - depth / 2.0],
            [center_x + width / 2.0, bottom, center_z + depth / 2.0],
            [center_x - width / 2.0, bottom, center_z + depth / 2.0],
        ]
        self.extrusion(
            op_id=op_id,
            component_id=component_id,
            profile=profile,
            vector=[0.0, height, 0.0],
            placement=placement,
        )

    def finish(
        self,
        *,
        producer_id: str,
        assembly_id: str,
        spec_digest: str,
        interface_refs: tuple[str, ...] = (),
        obligation_refs: tuple[str, ...] = (),
        source_refs: tuple[str, ...] = (),
    ) -> ProducedAssembly:
        return ProducedAssembly(
            producer_id=producer_id,
            assembly_id=assembly_id,
            context_digest=self.context.context_digest,
            spec_digest=spec_digest,
            operations=tuple(sorted(self._operations, key=lambda item: item.op_id)),
            components=tuple(
                sorted(
                    (
                        ProducedComponentGeometry(
                            component_id=component_id,
                            binding_id=f"binding-{component_id}",
                            object_ids=tuple(sorted(object_ids)),
                        )
                        for component_id, object_ids in self._component_objects.items()
                    ),
                    key=lambda item: item.binding_id,
                )
            ),
            interface_refs=interface_refs,
            obligation_refs=obligation_refs,
            source_refs=source_refs,
        )


@dataclass(frozen=True, slots=True)
class GeometryProductionBundle:
    """Collision-checked merge of detached assemblies for proposal authoring."""

    context_digest: str
    assembly_digests: tuple[str, ...]
    operations: tuple[GeometryOperation, ...]
    semantic_bindings: tuple[SemanticBinding, ...]

    SCHEMA = "GeometryProductionBundle@1"

    def __post_init__(self) -> None:
        require_sha256(self.context_digest, "context_digest")
        if not isinstance(self.assembly_digests, tuple) or not self.assembly_digests:
            raise GeometryProducerError("assembly_digests must be non-empty")
        for value in self.assembly_digests:
            require_sha256(value, "assembly_digest")
        if self.assembly_digests != tuple(sorted(set(self.assembly_digests))):
            raise GeometryProducerError(
                "assembly_digests must be sorted and unique"
            )
        operation_ids = tuple(item.op_id for item in self.operations)
        if operation_ids != tuple(sorted(set(operation_ids))):
            raise GeometryProducerError("bundle operations must be sorted and unique")
        binding_ids = tuple(item.binding_id for item in self.semantic_bindings)
        if binding_ids != tuple(sorted(set(binding_ids))):
            raise GeometryProducerError(
                "bundle semantic bindings must be sorted and unique"
            )

    @property
    def bundle_digest(self) -> str:
        return canonical_digest(self._identity())

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "context_digest": self.context_digest,
            "assembly_digests": list(self.assembly_digests),
            "operations": [item.to_dict() for item in self.operations],
            "semantic_bindings": [
                item.to_dict() for item in self.semantic_bindings
            ],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "bundle_digest": self.bundle_digest,
            **authority_free_fields(),
        }


def merge_produced_assemblies(
    context: ProducerContext,
    assemblies: tuple[ProducedAssembly, ...],
) -> GeometryProductionBundle:
    """Merge producer results without granting proposal or write authority."""

    if not isinstance(context, ProducerContext):
        raise TypeError("context must be ProducerContext")
    if not isinstance(assemblies, tuple) or not assemblies:
        raise GeometryProducerError("assemblies must be a non-empty tuple")
    if any(not isinstance(item, ProducedAssembly) for item in assemblies):
        raise TypeError("assemblies contains an invalid item")
    if any(item.context_digest != context.context_digest for item in assemblies):
        raise GeometryProducerError("assembly crossed its exact producer context")
    assembly_ids = tuple(item.assembly_id for item in assemblies)
    if len(assembly_ids) != len(set(assembly_ids)):
        raise GeometryProducerError("assembly ids collide")

    operations = tuple(
        sorted(
            (
                operation
                for assembly in assemblies
                for operation in assembly.operations
            ),
            key=lambda item: item.op_id,
        )
    )
    operation_ids = tuple(item.op_id for item in operations)
    if len(operation_ids) != len(set(operation_ids)):
        raise GeometryProducerError("operation ids collide across assemblies")
    output_ids = tuple(
        object_id
        for operation in operations
        for object_id in operation.output_object_ids
    )
    if len(output_ids) != len(set(output_ids)):
        raise GeometryProducerError("object ids collide across assemblies")

    component_by_binding: dict[str, str] = {}
    objects_by_binding: dict[str, set[str]] = {}
    evidence_by_binding: dict[str, set[str]] = {}
    for assembly in assemblies:
        for component in assembly.components:
            previous = component_by_binding.setdefault(
                component.binding_id, component.component_id
            )
            if previous != component.component_id:
                raise GeometryProducerError(
                    "one binding id cannot name multiple semantic components"
                )
            objects_by_binding.setdefault(component.binding_id, set()).update(
                component.object_ids
            )
            evidence_by_binding.setdefault(component.binding_id, set()).update(
                (*context.evidence_refs, *assembly.source_refs)
            )

    semantic_bindings = tuple(
        SemanticBinding(
            binding_id=binding_id,
            component_id=component_by_binding[binding_id],
            object_ids=tuple(sorted(objects_by_binding[binding_id])),
            commitment_refs=context.commitment_refs,
            evidence_refs=tuple(sorted(evidence_by_binding[binding_id])),
        )
        for binding_id in sorted(component_by_binding)
    )
    return GeometryProductionBundle(
        context_digest=context.context_digest,
        assembly_digests=tuple(
            sorted(item.assembly_digest for item in assemblies)
        ),
        operations=operations,
        semantic_bindings=semantic_bindings,
    )


SpecT = TypeVar("SpecT", contravariant=True)


class GeometryProducer(Protocol[SpecT]):
    """Structural protocol implemented by project-neutral producers."""

    producer_id: str

    def produce(
        self, context: ProducerContext, spec: SpecT
    ) -> ProducedAssembly:
        """Translate one exact project-owned spec into detached geometry."""
