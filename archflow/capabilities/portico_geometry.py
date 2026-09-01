"""Neutral portico geometry producer with no precedent-specific defaults."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from enum import StrEnum

from archflow.capabilities.geometry_producer import (
    GeometryAssemblyBuilder,
    ProducedAssembly,
    ProducerContext,
    YUpPlacement,
    authority_free_fields,
)
from archflow.contracts.canonical import canonical_digest
from archflow.contracts.fields import deterministic_refs
from archflow.project.refs import require_identifier


class PorticoGeometryError(ValueError):
    """A portico specification cannot be deterministically materialized."""


class PorticoMaturity(StrEnum):
    MASSING = "massing"
    RESERVATION = "reservation"
    DEVELOPED = "developed"
    DETAILED = "detailed"


@dataclass(frozen=True, slots=True)
class PorticoOperationIdentity:
    """Project-owned stable identifiers used by one portico instance."""

    facade_id: str
    massing_operation_id: str
    reservation_operation_id: str
    stair_reservation_operation_id: str

    def __post_init__(self) -> None:
        for field in fields(self):
            require_identifier(getattr(self, field.name), field.name)
        operation_ids = (
            self.massing_operation_id,
            self.reservation_operation_id,
            self.stair_reservation_operation_id,
        )
        if len(operation_ids) != len(set(operation_ids)):
            raise PorticoGeometryError(
                "portico reservation operation ids must be unique"
            )

    def to_dict(self) -> dict[str, str]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise PorticoGeometryError(f"{field} must be finite")
    return float(value)


def _positive(value: object, field: str) -> float:
    result = _finite(value, field)
    if result <= 0.0:
        raise PorticoGeometryError(f"{field} must be positive")
    return result


def _nonnegative(value: object, field: str) -> float:
    result = _finite(value, field)
    if result < 0.0:
        raise PorticoGeometryError(f"{field} must be non-negative")
    return result


@dataclass(frozen=True, slots=True)
class PorticoColumnSection:
    height_offset: float
    radius: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "height_offset",
            _nonnegative(self.height_offset, "column height_offset"),
        )
        object.__setattr__(self, "radius", _positive(self.radius, "column radius"))

    def to_dict(self) -> dict[str, float]:
        return {"height_offset": self.height_offset, "radius": self.radius}


@dataclass(frozen=True, slots=True)
class PorticoColumnArraySpec:
    count: int
    center_spacing: float
    front_inset: float
    radial_segments: int
    sections: tuple[PorticoColumnSection, ...]
    abacus_half_extent: float
    abacus_bottom_overlap: float
    abacus_thickness: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.count, int)
            or isinstance(self.count, bool)
            or self.count < 1
        ):
            raise PorticoGeometryError("column count must be positive")
        object.__setattr__(
            self,
            "center_spacing",
            _positive(self.center_spacing, "column center_spacing"),
        )
        object.__setattr__(
            self,
            "front_inset",
            _nonnegative(self.front_inset, "column front_inset"),
        )
        if (
            not isinstance(self.radial_segments, int)
            or isinstance(self.radial_segments, bool)
            or self.radial_segments < 3
        ):
            raise PorticoGeometryError("radial_segments must be at least three")
        if not isinstance(self.sections, tuple) or len(self.sections) < 2:
            raise PorticoGeometryError("column sections require at least two rows")
        if any(not isinstance(item, PorticoColumnSection) for item in self.sections):
            raise TypeError("column sections contains an invalid item")
        offsets = tuple(item.height_offset for item in self.sections)
        if offsets != tuple(sorted(set(offsets))) or offsets[0] != 0.0:
            raise PorticoGeometryError(
                "column sections require ascending offsets starting at zero"
            )
        for field in (
            "abacus_half_extent",
            "abacus_bottom_overlap",
            "abacus_thickness",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))

    @property
    def height(self) -> float:
        return self.sections[-1].height_offset

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "center_spacing": self.center_spacing,
            "front_inset": self.front_inset,
            "radial_segments": self.radial_segments,
            "sections": [item.to_dict() for item in self.sections],
            "abacus_half_extent": self.abacus_half_extent,
            "abacus_bottom_overlap": self.abacus_bottom_overlap,
            "abacus_thickness": self.abacus_thickness,
        }


@dataclass(frozen=True, slots=True)
class PorticoDetailCourse:
    bottom_offset: float
    height: float
    width_extra: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bottom_offset",
            _nonnegative(self.bottom_offset, "course bottom_offset"),
        )
        object.__setattr__(self, "height", _positive(self.height, "course height"))
        object.__setattr__(
            self,
            "width_extra",
            _nonnegative(self.width_extra, "course width_extra"),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "bottom_offset": self.bottom_offset,
            "height": self.height,
            "width_extra": self.width_extra,
        }


@dataclass(frozen=True, slots=True)
class PorticoEntablatureSpec:
    height: float
    front_width_extra: float
    front_depth: float
    return_center_extra: float
    return_width: float
    return_depth_extra: float
    detail_front_offset: float
    detail_depth: float
    detail_courses: tuple[PorticoDetailCourse, ...]

    def __post_init__(self) -> None:
        for field in ("height", "front_depth", "return_width", "detail_depth"):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        for field in (
            "front_width_extra",
            "return_center_extra",
            "return_depth_extra",
            "detail_front_offset",
        ):
            object.__setattr__(
                self, field, _nonnegative(getattr(self, field), field)
            )
        if not isinstance(self.detail_courses, tuple) or any(
            not isinstance(item, PorticoDetailCourse)
            for item in self.detail_courses
        ):
            raise TypeError("detail_courses must contain PorticoDetailCourse")

    def to_dict(self) -> dict[str, object]:
        return {
            "height": self.height,
            "front_width_extra": self.front_width_extra,
            "front_depth": self.front_depth,
            "return_center_extra": self.return_center_extra,
            "return_width": self.return_width,
            "return_depth_extra": self.return_depth_extra,
            "detail_front_offset": self.detail_front_offset,
            "detail_depth": self.detail_depth,
            "detail_courses": [item.to_dict() for item in self.detail_courses],
        }


@dataclass(frozen=True, slots=True)
class PorticoPedimentRoofSpec:
    pediment_front_extra: float
    pediment_half_width_extra: float
    pediment_rise: float
    pediment_thickness: float
    rear_inset: float
    surface_vertical_inset: float
    rear_rise: float
    roof_thickness: float
    abutment_depth: float

    def __post_init__(self) -> None:
        for field in (
            "pediment_front_extra",
            "pediment_half_width_extra",
            "rear_inset",
            "surface_vertical_inset",
            "rear_rise",
        ):
            object.__setattr__(
                self, field, _nonnegative(getattr(self, field), field)
            )
        for field in (
            "pediment_rise",
            "pediment_thickness",
            "roof_thickness",
            "abutment_depth",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))

    def to_dict(self) -> dict[str, float]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class PorticoLandingSpec:
    slab_thickness: float
    support_side_inset: float
    support_face_offset: float
    support_width: float
    support_depth: float
    support_top_clearance: float
    tread_overlap: float

    def __post_init__(self) -> None:
        for field in (
            "slab_thickness",
            "support_width",
            "support_depth",
            "support_top_clearance",
            "tread_overlap",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        for field in ("support_side_inset", "support_face_offset"):
            object.__setattr__(
                self, field, _nonnegative(getattr(self, field), field)
            )

    def to_dict(self) -> dict[str, float]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class PorticoTreadSpec:
    ordinal: int
    local_run_start: float
    run_depth: float
    width: float
    walking_datum: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
        ):
            raise PorticoGeometryError("tread ordinal must be non-negative")
        object.__setattr__(
            self,
            "local_run_start",
            _nonnegative(self.local_run_start, "tread local_run_start"),
        )
        for field in ("run_depth", "width", "walking_datum"):
            object.__setattr__(self, field, _positive(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class PorticoComponentIds:
    portico: str
    stair_reservation: str
    columns: str
    capitals: str
    entablature: str
    entablature_detail: str
    pediment: str
    roof: str
    roof_abutment: str
    stairs: str
    landing: str
    landing_supports: str

    def __post_init__(self) -> None:
        values = tuple(getattr(self, field.name) for field in fields(self))
        for field, value in zip(fields(self), values):
            require_identifier(value, field.name)
        if len(values) != len(set(values)):
            raise PorticoGeometryError("portico component ids must be unique")

    def to_dict(self) -> dict[str, str]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True, slots=True)
class PorticoGeometrySpec:
    assembly_id: str
    identity: PorticoOperationIdentity
    placement: YUpPlacement
    maturity: PorticoMaturity
    components: PorticoComponentIds
    host_face_offset: float
    width: float
    depth: float
    service_top: float
    stair_envelope_going: float
    stair_run_going: float
    columns: PorticoColumnArraySpec
    entablature: PorticoEntablatureSpec
    pediment_roof: PorticoPedimentRoofSpec
    landing: PorticoLandingSpec
    treads: tuple[PorticoTreadSpec, ...]
    knowledge_refs: tuple[str, ...]
    interface_refs: tuple[str, ...] = ()
    obligation_refs: tuple[str, ...] = ()

    SCHEMA = "PorticoGeometrySpec@1"

    def __post_init__(self) -> None:
        require_identifier(self.assembly_id, "assembly_id")
        if not isinstance(self.identity, PorticoOperationIdentity):
            raise TypeError("identity must be PorticoOperationIdentity")
        if not isinstance(self.placement, YUpPlacement):
            raise TypeError("placement must be YUpPlacement")
        if not isinstance(self.maturity, PorticoMaturity):
            raise TypeError("maturity must be PorticoMaturity")
        if not isinstance(self.components, PorticoComponentIds):
            raise TypeError("components must be PorticoComponentIds")
        object.__setattr__(
            self,
            "host_face_offset",
            _finite(self.host_face_offset, "host_face_offset"),
        )
        for field in (
            "width",
            "depth",
            "service_top",
            "stair_envelope_going",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        object.__setattr__(
            self,
            "stair_run_going",
            _nonnegative(self.stair_run_going, "stair_run_going"),
        )
        for field, expected in (
            ("columns", PorticoColumnArraySpec),
            ("entablature", PorticoEntablatureSpec),
            ("pediment_roof", PorticoPedimentRoofSpec),
            ("landing", PorticoLandingSpec),
        ):
            if not isinstance(getattr(self, field), expected):
                raise TypeError(f"{field} must be {expected.__name__}")
        if not isinstance(self.treads, tuple) or any(
            not isinstance(item, PorticoTreadSpec) for item in self.treads
        ):
            raise TypeError("treads must contain PorticoTreadSpec values")
        ordinals = tuple(item.ordinal for item in self.treads)
        if ordinals != tuple(sorted(set(ordinals))):
            raise PorticoGeometryError("tread ordinals must be sorted and unique")
        if self.maturity in {
            PorticoMaturity.DEVELOPED,
            PorticoMaturity.DETAILED,
        } and (not self.treads or self.stair_run_going <= 0.0):
            raise PorticoGeometryError(
                "developed porticos require a solved stair run and treads"
            )
        object.__setattr__(
            self,
            "knowledge_refs",
            deterministic_refs(self.knowledge_refs, "knowledge_refs"),
        )
        for field in ("interface_refs", "obligation_refs"):
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
            "assembly_id": self.assembly_id,
            "identity": self.identity.to_dict(),
            "placement": self.placement.to_dict(),
            "maturity": self.maturity.value,
            "components": self.components.to_dict(),
            "host_face_offset": self.host_face_offset,
            "width": self.width,
            "depth": self.depth,
            "service_top": self.service_top,
            "stair_envelope_going": self.stair_envelope_going,
            "stair_run_going": self.stair_run_going,
            "columns": self.columns.to_dict(),
            "entablature": self.entablature.to_dict(),
            "pediment_roof": self.pediment_roof.to_dict(),
            "landing": self.landing.to_dict(),
            "treads": [item.to_dict() for item in self.treads],
            "knowledge_refs": list(self.knowledge_refs),
            "interface_refs": list(self.interface_refs),
            "obligation_refs": list(self.obligation_refs),
        }

    @property
    def spec_digest(self) -> str:
        return canonical_digest(self._identity())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "spec_digest": self.spec_digest,
            **authority_free_fields(),
        }


def produce_portico_geometry(
    context: ProducerContext, spec: PorticoGeometrySpec
) -> ProducedAssembly:
    """Produce one explicitly placed portico with no hidden project defaults."""

    if not isinstance(context, ProducerContext):
        raise TypeError("context must be ProducerContext")
    if not isinstance(spec, PorticoGeometrySpec):
        raise TypeError("spec must be PorticoGeometrySpec")
    builder = GeometryAssemblyBuilder(context)
    component = spec.components
    identity = spec.identity
    placement = spec.placement

    if spec.maturity in {
        PorticoMaturity.MASSING,
        PorticoMaturity.RESERVATION,
    }:
        operation_id = (
            identity.massing_operation_id
            if spec.maturity is PorticoMaturity.MASSING
            else identity.reservation_operation_id
        )
        builder.rect_prism(
            op_id=operation_id,
            component_id=component.portico,
            center_x=0.0,
            center_z=spec.host_face_offset + spec.depth / 2.0,
            width=spec.width,
            depth=spec.depth,
            bottom=spec.service_top,
            height=spec.columns.height + spec.entablature.height,
            placement=placement,
        )
        builder.rect_prism(
            op_id=identity.stair_reservation_operation_id,
            component_id=component.stair_reservation,
            center_x=0.0,
            center_z=(
                spec.host_face_offset
                + spec.depth
                + spec.stair_envelope_going / 2.0
            ),
            width=spec.width,
            depth=spec.stair_envelope_going,
            bottom=0.0,
            height=spec.service_top,
            placement=placement,
        )
        return builder.finish(
            producer_id="portico-geometry",
            assembly_id=spec.assembly_id,
            spec_digest=spec.spec_digest,
            interface_refs=spec.interface_refs,
            obligation_refs=spec.obligation_refs,
            source_refs=spec.knowledge_refs,
        )

    facade_id = identity.facade_id
    front_z = spec.host_face_offset + spec.depth - spec.columns.front_inset
    center_offset = (spec.columns.count - 1) / 2.0
    centers = tuple(
        (index - center_offset) * spec.columns.center_spacing
        for index in range(spec.columns.count)
    )
    for index, center_x in enumerate(centers):
        rings = [
            [
                [
                    center_x
                    + section.radius
                    * math.cos(
                        2.0 * math.pi * radial_index / spec.columns.radial_segments
                    ),
                    spec.service_top + section.height_offset,
                    front_z
                    + section.radius
                    * math.sin(
                        2.0 * math.pi * radial_index / spec.columns.radial_segments
                    ),
                ]
                for radial_index in range(spec.columns.radial_segments)
            ]
            for section in spec.columns.sections
        ]
        builder.loft(
            op_id=f"column-{facade_id}-{index}",
            component_id=component.columns,
            profiles=rings,
            placement=placement,
            cap_ends=True,
            loft_type="normal",
        )
        abacus_bottom = (
            spec.service_top
            + spec.columns.height
            - spec.columns.abacus_bottom_overlap
        )
        half = spec.columns.abacus_half_extent
        abacus = [
            [center_x - half, abacus_bottom, front_z - half],
            [center_x + half, abacus_bottom, front_z - half],
            [center_x + half, abacus_bottom, front_z + half],
            [center_x - half, abacus_bottom, front_z + half],
        ]
        builder.extrusion(
            op_id=f"abacus-{facade_id}-{index}",
            component_id=component.capitals,
            profile=abacus,
            vector=[0.0, spec.columns.abacus_thickness, 0.0],
            placement=placement,
            vector_is_world_aligned=True,
        )

    entablature = spec.entablature
    entablature_bottom = spec.service_top + spec.columns.height
    builder.rect_prism(
        op_id=f"entablature-front-{facade_id}",
        component_id=component.entablature,
        center_x=0.0,
        center_z=front_z,
        width=spec.width + entablature.front_width_extra,
        depth=entablature.front_depth,
        bottom=entablature_bottom,
        height=entablature.height,
        placement=placement,
    )
    for side_sign, label in ((-1.0, "left"), (1.0, "right")):
        builder.rect_prism(
            op_id=f"entablature-return-{facade_id}-{label}",
            component_id=component.entablature,
            center_x=side_sign
            * (spec.width / 2.0 + entablature.return_center_extra),
            center_z=spec.host_face_offset + spec.depth / 2.0,
            width=entablature.return_width,
            depth=spec.depth + entablature.return_depth_extra,
            bottom=entablature_bottom,
            height=entablature.height,
            placement=placement,
        )

    roof = spec.pediment_roof
    pediment_front = (
        spec.host_face_offset + spec.depth + roof.pediment_front_extra
    )
    outer_x = spec.width / 2.0 + roof.pediment_half_width_extra
    pediment = [
        [-outer_x, entablature_bottom + entablature.height, pediment_front],
        [outer_x, entablature_bottom + entablature.height, pediment_front],
        [
            0.0,
            entablature_bottom + entablature.height + roof.pediment_rise,
            pediment_front,
        ],
    ]
    builder.extrusion(
        op_id=f"pediment-tympanum-{facade_id}",
        component_id=component.pediment,
        profile=pediment,
        vector=[0.0, 0.0, -roof.pediment_thickness],
        placement=placement,
    )

    rear_z = spec.host_face_offset - roof.rear_inset
    front_eave = (
        entablature_bottom + entablature.height - roof.surface_vertical_inset
    )
    front_ridge = front_eave + roof.pediment_rise
    rear_eave = front_eave + roof.rear_rise
    rear_ridge = front_ridge + roof.rear_rise
    roof_quads = (
        [
            [-outer_x, front_eave, pediment_front],
            [-outer_x, rear_eave, rear_z],
            [0.0, rear_ridge, rear_z],
            [0.0, front_ridge, pediment_front],
        ],
        [
            [0.0, front_ridge, pediment_front],
            [0.0, rear_ridge, rear_z],
            [outer_x, rear_eave, rear_z],
            [outer_x, front_eave, pediment_front],
        ],
    )
    for slope_index, bottom_profile in enumerate(roof_quads):
        top = [
            [point[0], point[1] + roof.roof_thickness, point[2]]
            for point in bottom_profile
        ]
        builder.loft(
            op_id=f"portico-roof-{facade_id}-{slope_index}",
            component_id=component.roof,
            profiles=[bottom_profile, top],
            placement=placement,
            cap_ends=True,
        )
    rear_abutment = [
        [-outer_x, rear_eave, rear_z],
        [outer_x, rear_eave, rear_z],
        [0.0, rear_ridge, rear_z],
    ]
    builder.extrusion(
        op_id=f"portico-roof-abutment-{facade_id}",
        component_id=component.roof_abutment,
        profile=rear_abutment,
        vector=[0.0, 0.0, roof.abutment_depth],
        placement=placement,
    )

    landing_front = spec.host_face_offset + spec.depth
    lower_z = landing_front + spec.stair_run_going
    for tread in spec.treads:
        builder.rect_prism(
            op_id=f"stair-{facade_id}-{tread.ordinal:02d}",
            component_id=component.stairs,
            center_x=0.0,
            center_z=lower_z - (tread.local_run_start + tread.run_depth / 2.0),
            width=tread.width,
            depth=tread.run_depth + spec.landing.tread_overlap,
            bottom=0.0,
            height=tread.walking_datum,
            placement=placement,
        )
    builder.rect_prism(
        op_id=f"landing-bridge-{facade_id}",
        component_id=component.landing,
        center_x=0.0,
        center_z=spec.host_face_offset + spec.depth / 2.0,
        width=spec.width,
        depth=spec.depth,
        bottom=spec.service_top - spec.landing.slab_thickness,
        height=spec.landing.slab_thickness,
        placement=placement,
    )
    pier_offset_x = spec.width / 2.0 - spec.landing.support_side_inset
    pier_z_values = (
        spec.host_face_offset + spec.landing.support_face_offset,
        landing_front - spec.landing.support_face_offset,
    )
    for side_sign, label in ((-1.0, "left"), (1.0, "right")):
        for z_index, z_value in enumerate(pier_z_values):
            builder.rect_prism(
                op_id=f"landing-support-{facade_id}-{label}-{z_index}",
                component_id=component.landing_supports,
                center_x=side_sign * pier_offset_x,
                center_z=z_value,
                width=spec.landing.support_width,
                depth=spec.landing.support_depth,
                bottom=0.0,
                height=spec.service_top - spec.landing.support_top_clearance,
                placement=placement,
            )

    if spec.maturity is PorticoMaturity.DETAILED:
        for course_index, course in enumerate(entablature.detail_courses):
            builder.rect_prism(
                op_id=f"entablature-course-{facade_id}-{course_index}",
                component_id=component.entablature_detail,
                center_x=0.0,
                center_z=front_z + entablature.detail_front_offset,
                width=(
                    spec.width
                    + entablature.front_width_extra
                    + 2.0 * course.width_extra
                ),
                depth=entablature.detail_depth,
                bottom=entablature_bottom + course.bottom_offset,
                height=course.height,
                placement=placement,
            )
    return builder.finish(
        producer_id="portico-geometry",
        assembly_id=spec.assembly_id,
        spec_digest=spec.spec_digest,
        interface_refs=spec.interface_refs,
        obligation_refs=spec.obligation_refs,
        source_refs=spec.knowledge_refs,
    )


class PorticoGeometryProducer:
    """Protocol-friendly stateless producer implementation."""

    producer_id = "portico-geometry"

    def produce(
        self, context: ProducerContext, spec: PorticoGeometrySpec
    ) -> ProducedAssembly:
        return produce_portico_geometry(context, spec)
