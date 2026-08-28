"""Deterministic IFC4 export of a compiled neutral geometry program.

Semantics are first-class IFC constructs: one element per physical
object named by its object id, typed through a caller-supplied component
mapping (proxy fallback — the framework ships no building vocabulary),
property sets retaining exactly the binding ids, component, commitment
and evidence refs, and producer op the program states, and repetition as
one mapped representation reused under N transformed ``IfcMappedItem``
instances.

Geometry maps parametrically where IFC expresses it exactly: solids and
constant-radius revolves become extruded area solids, and booleans over
parametric operands become ``IfcBooleanResult`` chains. Lofts become
faceted breps built from the exact ring vertices, and a boolean whose
operand is faceted keeps its base representation only — both recorded as
typed representation notes, never silent. GUIDs derive from stable
content keys and no timestamps are written, so the same program always
yields the same file.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from typing import Mapping

import ifcopenshell
import ifcopenshell.guid

_PARAMETRIC = "parametric"
_FACETED = "faceted"


class IfcExportError(ValueError):
    """The program contains a construct the exporter cannot express."""


@dataclass(frozen=True, slots=True)
class IfcExportResult:
    element_count: int
    mapped_instance_total: int
    representation_notes: tuple[dict, ...]
    file_text: str

    @property
    def file_sha256(self) -> str:
        return hashlib.sha256(self.file_text.encode("utf-8")).hexdigest()


def _params(operation) -> dict[str, object]:
    return {
        parameter.name: json.loads(parameter.value_json)
        for parameter in operation.parameters
    }


def _guid(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).digest()[:16]
    return ifcopenshell.guid.compress(uuid.UUID(bytes=digest).hex)


def _point(f, x, y, z):
    return f.create_entity(
        "IfcCartesianPoint",
        Coordinates=(float(x), float(y), float(z)),
    )


def _dir(f, x, y, z):
    return f.create_entity(
        "IfcDirection", DirectionRatios=(float(x), float(y), float(z))
    )


def _placement3d(f, origin=(0.0, 0.0, 0.0)):
    return f.create_entity(
        "IfcAxis2Placement3D", Location=_point(f, *origin)
    )


def _to_ifc(point):
    """Program frame (x, y-up, z) to IFC frame (x, y, z-up)."""

    return (float(point[0]), float(point[2]), float(point[1]))


class _Builder:
    def __init__(self, f, context):
        self.f = f
        self.context = context
        self.items: dict[str, object] = {}
        self.kinds: dict[str, str] = {}
        self.notes: list[dict] = []

    def note(self, code, op_id, detail):
        self.notes.append(
            {"code": code, "op_id": op_id, "detail": detail}
        )

    def solid(self, op_id, out, params):
        origin, size = params["origin"], params["size"]
        profile = self.f.create_entity(
            "IfcRectangleProfileDef",
            ProfileType="AREA",
            Position=self.f.create_entity(
                "IfcAxis2Placement2D",
                Location=self.f.create_entity(
                    "IfcCartesianPoint",
                    Coordinates=(
                        float(origin[0]) + float(size[0]) / 2.0,
                        float(origin[2]) + float(size[2]) / 2.0,
                    ),
                ),
            ),
            XDim=float(size[0]),
            YDim=float(size[2]),
        )
        item = self.f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=profile,
            Position=_placement3d(
                self.f, (0.0, 0.0, float(origin[1]))
            ),
            ExtrudedDirection=_dir(self.f, 0, 0, 1),
            Depth=float(size[1]),
        )
        self.items[out] = item
        self.kinds[out] = _PARAMETRIC

    def revolve(self, op_id, out, params):
        a0, a1 = params["axis_start"], params["axis_end"]
        r0 = max(float(params["start_radius"]), 0.01)
        r1 = max(float(params["end_radius"]), 0.01)
        vertical = (
            abs(float(a0[0]) - float(a1[0])) < 1e-9
            and abs(float(a0[2]) - float(a1[2])) < 1e-9
        )
        if not vertical or abs(r0 - r1) > 1e-9:
            rings = []
            for level, radius in ((a0, r0), (a1, r1)):
                rings.append(
                    [
                        (
                            float(level[0]) + radius * math.cos(step),
                            float(level[1]),
                            float(level[2]) + radius * math.sin(step),
                        )
                        for step in (
                            index * 2.0 * math.pi / 24.0
                            for index in range(24)
                        )
                    ]
                )
            self.note(
                "ifc.revolve_faceted",
                op_id,
                "tapered or tilted revolve exported as faceted brep",
            )
            self._faceted(out, rings)
            return
        profile = self.f.create_entity(
            "IfcCircleProfileDef",
            ProfileType="AREA",
            Position=self.f.create_entity(
                "IfcAxis2Placement2D",
                Location=self.f.create_entity(
                    "IfcCartesianPoint",
                    Coordinates=(float(a0[0]), float(a0[2])),
                ),
            ),
            Radius=r0,
        )
        item = self.f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=profile,
            Position=_placement3d(self.f, (0.0, 0.0, float(a0[1]))),
            ExtrudedDirection=_dir(self.f, 0, 0, 1),
            Depth=float(a1[1]) - float(a0[1]),
        )
        self.items[out] = item
        self.kinds[out] = _PARAMETRIC

    def extrusion(self, op_id, out, params):
        vector = params["vector"]
        profile_points = params["profile"]
        if abs(float(vector[0])) > 1e-9 or abs(float(vector[2])) > 1e-9:
            rings = [
                [tuple(map(float, point)) for point in profile_points],
                [
                    (
                        float(point[0]) + float(vector[0]),
                        float(point[1]) + float(vector[1]),
                        float(point[2]) + float(vector[2]),
                    )
                    for point in profile_points
                ],
            ]
            self.note(
                "ifc.extrusion_faceted",
                op_id,
                "non-vertical extrusion exported as faceted brep",
            )
            self._faceted(out, rings)
            return
        base = float(profile_points[0][1])
        polyline = self.f.create_entity(
            "IfcPolyline",
            Points=[
                self.f.create_entity(
                    "IfcCartesianPoint",
                    Coordinates=(float(point[0]), float(point[2])),
                )
                for point in [*profile_points, profile_points[0]]
            ],
        )
        profile = self.f.create_entity(
            "IfcArbitraryClosedProfileDef",
            ProfileType="AREA",
            OuterCurve=polyline,
        )
        item = self.f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=profile,
            Position=_placement3d(self.f, (0.0, 0.0, base)),
            ExtrudedDirection=_dir(self.f, 0, 0, 1),
            Depth=float(vector[1]),
        )
        self.items[out] = item
        self.kinds[out] = _PARAMETRIC

    def loft(self, op_id, out, params):
        size = int(params["profile_size"])
        profiles = params["profiles"]
        rings = [
            [tuple(map(float, point)) for point in profiles[i : i + size]]
            for i in range(0, len(profiles), size)
        ]
        self.note(
            "ifc.loft_faceted",
            op_id,
            "loft exported as faceted brep from exact ring vertices",
        )
        self._faceted(out, rings)

    def _faceted(self, out, rings):
        f = self.f
        points = [
            [_point(f, *_to_ifc(point)) for point in ring]
            for ring in rings
        ]
        faces = []

        def face(loop_points, reverse=False):
            ordered = list(reversed(loop_points)) if reverse else loop_points
            loop = f.create_entity("IfcPolyLoop", Polygon=ordered)
            bound = f.create_entity(
                "IfcFaceOuterBound", Bound=loop, Orientation=True
            )
            faces.append(f.create_entity("IfcFace", Bounds=[bound]))

        face(points[0], reverse=True)
        for lower, upper in zip(points, points[1:], strict=False):
            count = len(lower)
            for index in range(count):
                nxt = (index + 1) % count
                face(
                    [
                        lower[index],
                        lower[nxt],
                        upper[nxt],
                        upper[index],
                    ]
                )
        face(points[-1])
        shell = f.create_entity("IfcClosedShell", CfsFaces=faces)
        self.items[out] = f.create_entity("IfcFacetedBrep", Outer=shell)
        self.kinds[out] = _FACETED

    def boolean(self, op_id, kind, out, ins, params):
        operator = {
            "boolean_union": "UNION",
            "boolean_difference": "DIFFERENCE",
            "boolean_intersection": "INTERSECTION",
        }[kind]
        if kind == "boolean_difference":
            base_id = sorted(ins)[int(params.get("base_index", 0))]
        else:
            base_id = ins[0]
        others = [item for item in ins if item != base_id]
        result = self.items[base_id]
        applied_kind = self.kinds[base_id]
        if applied_kind == _FACETED:
            self.note(
                "ifc.boolean_partial_representation",
                op_id,
                f"faceted base operand; unapplied operands={sorted(others)}",
            )
            self.items[out] = result
            self.kinds[out] = _FACETED
            return
        for other in others:
            if self.kinds[other] == _FACETED:
                self.note(
                    "ifc.boolean_partial_representation",
                    op_id,
                    f"faceted operand {other} not applied",
                )
                continue
            result = self.f.create_entity(
                "IfcBooleanResult",
                Operator=operator,
                FirstOperand=result,
                SecondOperand=self.items[other],
            )
        self.items[out] = result
        self.kinds[out] = _PARAMETRIC


def _rotation_operator(f, center, degrees):
    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)
    cx, cy = float(center[0]), float(center[2])
    origin = (cx - (c * cx + s * cy), cy - (-s * cx + c * cy), 0.0)
    return f.create_entity(
        "IfcCartesianTransformationOperator3D",
        Axis1=_dir(f, c, -s, 0.0),
        Axis2=_dir(f, s, c, 0.0),
        LocalOrigin=_point(f, *origin),
        Axis3=_dir(f, 0.0, 0.0, 1.0),
    )


def _translation_operator(f, offset):
    return f.create_entity(
        "IfcCartesianTransformationOperator3D",
        LocalOrigin=_point(
            f, float(offset[0]), float(offset[2]), float(offset[1])
        ),
    )


def export_program_to_ifc(
    program,
    *,
    project_id: str,
    run_id: str,
    class_by_component: Mapping[str, str] | None = None,
    provenance: Mapping[str, str] | None = None,
) -> IfcExportResult:
    """Author one deterministic IFC4 file for the compiled program."""

    proposal = program.proposal
    operations = {op.op_id: op for op in proposal.operations}
    bindings = {
        binding.binding_id: binding
        for binding in getattr(proposal, "semantic_bindings", ())
    }
    consumed: set[str] = set()
    producers: dict[str, object] = {}
    for op in proposal.operations:
        consumed.update(op.input_object_ids)
        for object_id in op.output_object_ids:
            producers[object_id] = op
    physical = sorted(
        object_id
        for op in proposal.operations
        for object_id in op.output_object_ids
        if object_id not in consumed and op.kind.value != "curve"
    )

    f = ifcopenshell.file(schema="IFC4")
    length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    units = f.create_entity("IfcUnitAssignment", Units=[length])
    context = f.create_entity(
        "IfcGeometricRepresentationContext",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1e-5,
        WorldCoordinateSystem=_placement3d(f),
    )
    project = f.create_entity(
        "IfcProject",
        GlobalId=_guid(f"{project_id}:project"),
        Name=project_id,
        RepresentationContexts=[context],
        UnitsInContext=units,
    )
    site = f.create_entity(
        "IfcSite",
        GlobalId=_guid(f"{project_id}:site"),
        Name=f"{project_id}-site",
    )
    building = f.create_entity(
        "IfcBuilding",
        GlobalId=_guid(f"{project_id}:building"),
        Name=run_id,
    )
    f.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid(f"{project_id}:project-site"),
        RelatingObject=project,
        RelatedObjects=[site],
    )
    f.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid(f"{project_id}:site-building"),
        RelatingObject=site,
        RelatedObjects=[building],
    )

    builder = _Builder(f, context)
    mapped: dict[str, tuple[object, tuple, str]] = {}
    for op_id in program.operation_order:
        operation = operations[op_id]
        kind = operation.kind.value
        if kind == "curve":
            builder.note(
                "ifc.curve_reference_only", op_id, "reference curve omitted"
            )
            continue
        params = _params(operation)
        out = operation.output_object_ids[0]
        ins = list(operation.input_object_ids)
        if kind == "solid":
            builder.solid(op_id, out, params)
        elif kind == "revolve":
            builder.revolve(op_id, out, params)
        elif kind == "extrusion":
            builder.extrusion(op_id, out, params)
        elif kind == "loft":
            builder.loft(op_id, out, params)
        elif kind in (
            "boolean_union",
            "boolean_difference",
            "boolean_intersection",
        ):
            builder.boolean(op_id, kind, out, ins, params)
        elif kind == "array":
            count = int(params["count"])
            step = params["step"]
            operators = tuple(
                _translation_operator(
                    f,
                    (
                        float(step[0]) * index,
                        float(step[1]) * index,
                        float(step[2]) * index,
                    ),
                )
                for index in range(count)
            )
            mapped[out] = (builder.items[ins[0]], operators,
                          builder.kinds[ins[0]])
            builder.kinds[out] = builder.kinds[ins[0]]
        elif kind == "radial_array":
            count = int(params["count"])
            center = params["center"]
            angle = float(params["angle_step_degrees"])
            start = float(params.get("start_angle_degrees", 0.0))
            operators = tuple(
                _rotation_operator(f, center, start + index * angle)
                for index in range(count)
            )
            mapped[out] = (builder.items[ins[0]], operators,
                          builder.kinds[ins[0]])
            builder.kinds[out] = builder.kinds[ins[0]]
        elif kind == "transform":
            builder.note(
                "ifc.transform_copy_only", op_id, "transform kept as copy"
            )
            builder.items[out] = builder.items[ins[0]]
            builder.kinds[out] = builder.kinds[ins[0]]
        else:
            raise IfcExportError(f"unsupported operation kind: {kind}")

    class_map = dict(class_by_component or {})
    elements = []
    mapped_total = 0
    for object_id in physical:
        operation = producers[object_id]
        binding_ids = tuple(
            sorted(getattr(operation, "semantic_binding_ids", ()) or ())
        )
        components = sorted(
            {
                bindings[item].component_id
                for item in binding_ids
                if item in bindings
            }
        )
        component = "+".join(components)
        ifc_class = class_map.get(component, "IfcBuildingElementProxy")
        if object_id in mapped:
            source_item, operators, source_kind = mapped[object_id]
            representation_type = (
                "SweptSolid" if source_kind == _PARAMETRIC else "Brep"
            )
            source_representation = f.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Body",
                RepresentationType=representation_type,
                Items=[source_item],
            )
            representation_map = f.create_entity(
                "IfcRepresentationMap",
                MappingOrigin=_placement3d(f),
                MappedRepresentation=source_representation,
            )
            items = [
                f.create_entity(
                    "IfcMappedItem",
                    MappingSource=representation_map,
                    MappingTarget=operator,
                )
                for operator in operators
            ]
            mapped_total += len(items)
            shape = f.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Body",
                RepresentationType="MappedRepresentation",
                Items=items,
            )
        else:
            item = builder.items[object_id]
            representation_type = (
                "Brep"
                if builder.kinds[object_id] == _FACETED
                else (
                    "CSG"
                    if item.is_a("IfcBooleanResult")
                    else "SweptSolid"
                )
            )
            shape = f.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=context,
                RepresentationIdentifier="Body",
                RepresentationType=representation_type,
                Items=[item],
            )
        element = f.create_entity(
            ifc_class,
            GlobalId=_guid(f"{project_id}:{object_id}"),
            Name=object_id,
            ObjectType=component or None,
            ObjectPlacement=f.create_entity(
                "IfcLocalPlacement",
                RelativePlacement=_placement3d(f),
            ),
            Representation=f.create_entity(
                "IfcProductDefinitionShape", Representations=[shape]
            ),
        )
        elements.append(element)
        properties = {
            "archflow:producer_op": operation.op_id,
            "archflow:bindings": ",".join(binding_ids),
            "archflow:component": component,
            "archflow:commitments": ",".join(
                sorted(
                    {
                        ref
                        for item in binding_ids
                        if item in bindings
                        for ref in bindings[item].commitment_refs
                    }
                )
            ),
            "archflow:evidence": ",".join(
                sorted(
                    {
                        ref
                        for item in binding_ids
                        if item in bindings
                        for ref in bindings[item].evidence_refs
                    }
                )
            ),
            **{
                f"archflow:{key}": str(value)
                for key, value in sorted((provenance or {}).items())
            },
        }
        pset = f.create_entity(
            "IfcPropertySet",
            GlobalId=_guid(f"{project_id}:{object_id}:pset"),
            Name="Archflow_Semantics",
            HasProperties=[
                f.create_entity(
                    "IfcPropertySingleValue",
                    Name=name,
                    NominalValue=f.create_entity("IfcText", value),
                )
                for name, value in sorted(properties.items())
                if value
            ],
        )
        f.create_entity(
            "IfcRelDefinesByProperties",
            GlobalId=_guid(f"{project_id}:{object_id}:pset-rel"),
            RelatedObjects=[element],
            RelatingPropertyDefinition=pset,
        )
    f.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=_guid(f"{project_id}:containment"),
        RelatingStructure=building,
        RelatedElements=elements,
    )
    return IfcExportResult(
        element_count=len(elements),
        mapped_instance_total=mapped_total,
        representation_notes=tuple(builder.notes),
        file_text=f.to_string(),
    )
