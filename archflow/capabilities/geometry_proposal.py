"""Record-driven, provider-neutral geometry proposal authoring.

The capability binds model-authored neutral geometry to an exact developed
design state and a persisted spatial option.  It may compile and retain proposal
rounds, but it has no hard-gate, acceptance, canonical-write, or platform
mutation authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.ports.model import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.project.ports import PersistenceArea, PersistenceDestination, RecordSink, require_destination
from archflow.project.record_kinds import (
    GEOMETRY_PROGRAM_PROPOSAL,
    GEOMETRY_PROPOSAL_DEFERRAL,
    GEOMETRY_PROPOSAL_ESCALATION,
    GEOMETRY_PROPOSAL_LINEAGE,
    geometry_proposal_completion,
    geometry_proposal_round,
)
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.contracts.authority import no_authority
from archflow.compilers.geometry import (
    AssetSubstitutionReceipt,
    CompiledGeometryObject,
    CompiledGeometryProgram,
    GeometryIssue,
    GeometryIssueCode,
    compile_geometry_program,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.stage_workflow import DesignPhase
from archflow.state.spatial import SpatialOptionProposal
from archflow.state.geometry_program import (
    ASSET_URI_PATTERN,
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    AssetReference,
    CoordinateFrame,
    DatumBinding,
    DetailMaturity,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    InterfaceDatum,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    ObjectRetirement,
    ObjectRevisionPrecondition,
    SemanticBinding,
    required_assembly_roles,
)
from archflow.state.operational_state import PORTABLE_LOGICAL_REF_PATTERN
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256
from archflow.contracts.fields import (
    mapping as _mapping,
)


_AUTHORING_OUTPUT_SCHEMA = "GeometryProposalAuthoringOutput@1"
_PROPOSAL_BODY_SCHEMA = "GeometryProgramProposalBody@1"
_EDIT_AUTHORING_OUTPUT_SCHEMA = "GeometryProgramEditAuthoringOutput@1"
_EDIT_BODY_SCHEMA = "GeometryProgramEditBody@1"
_PROPOSAL_RECORD_SCHEMA = "GeometryProgramProposalRecord@1"


def _function_parameter(
    name: str,
    kind: GeometryParameterKind,
    *,
    required: bool = True,
    unit: LengthUnit | None = None,
    allowed_values: tuple[object, ...] = (),
) -> dict[str, object]:
    return {
        "schema": "GeometryFunctionParameterContract@1",
        "name": name,
        "kind": kind.value,
        "required": required,
        "unit": None if unit is None else unit.value,
        "allowed_value_json": [
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            for value in allowed_values
        ],
    }


def _function_contract(
    *,
    minimum_inputs: int,
    maximum_inputs: int | None,
    parameters: tuple[dict[str, object], ...] = (),
    placement_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "schema": "GeometryFunctionContract@1",
        "input_arity": {
            "minimum": minimum_inputs,
            "maximum": maximum_inputs,
        },
        "parameters": list(parameters),
        "placement_fields": list(placement_fields),
    }


_METER = LengthUnit.METER
_GEOMETRY_COORDINATE_CONVENTION: dict[str, object] = {
    "schema": "GeometryCoordinateConvention@1",
    "handedness": "right-handed",
    "axis_order": ["x", "y", "z"],
    "axes": {
        "x": "horizontal width",
        "y": "vertical up and height",
        "z": "horizontal depth",
    },
    "footprint_cell_order": ["x", "z"],
    "spatial_bounds_order": ["x", "y", "z"],
    "vector_parameter_order": ["x", "y", "z"],
    "size_parameter_order": ["width_x", "height_y", "depth_z"],
}
_FUNCTION_CONTRACTS: dict[str, dict[str, object]] = {
    "array": _function_contract(
        minimum_inputs=1,
        maximum_inputs=1,
        parameters=(
            _function_parameter("count", GeometryParameterKind.INTEGER),
            _function_parameter(
                "step", GeometryParameterKind.VECTOR3, unit=_METER
            ),
        ),
    ),
    "radial_array": _function_contract(
        minimum_inputs=1,
        maximum_inputs=1,
        parameters=(
            _function_parameter(
                "angle_step_degrees", GeometryParameterKind.NUMBER
            ),
            _function_parameter(
                "axis", GeometryParameterKind.VECTOR3
            ),
            _function_parameter(
                "center", GeometryParameterKind.VECTOR3, unit=_METER
            ),
            _function_parameter("count", GeometryParameterKind.INTEGER),
            _function_parameter(
                "start_angle_degrees",
                GeometryParameterKind.NUMBER,
                required=False,
            ),
        ),
    ),
    "asset_instance": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        placement_fields=("asset_id", "asset_socket_id", "asset_scale"),
    ),
    "boolean_difference": _function_contract(
        minimum_inputs=2,
        maximum_inputs=None,
        parameters=(
            _function_parameter("base_index", GeometryParameterKind.INTEGER),
        ),
    ),
    "boolean_intersection": _function_contract(
        minimum_inputs=2,
        maximum_inputs=None,
        parameters=(
            # P092: an aperture volume is opening evidence, not material;
            # it may be exported hidden.
            _function_parameter(
                "hidden_for_inspection",
                GeometryParameterKind.BOOLEAN,
                required=False,
            ),
        ),
    ),
    "boolean_union": _function_contract(
        minimum_inputs=2,
        maximum_inputs=None,
    ),
    "curve": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        parameters=(
            _function_parameter(
                "basis",
                GeometryParameterKind.TEXT,
                required=False,
                allowed_values=("bezier", "polyline"),
            ),
            _function_parameter(
                "points", GeometryParameterKind.POINTS3, unit=_METER
            ),
            _function_parameter(
                "retain_for_inspection",
                GeometryParameterKind.BOOLEAN,
                required=False,
            ),
            _function_parameter(
                "hidden_for_inspection",
                GeometryParameterKind.BOOLEAN,
                required=False,
            ),
        ),
    ),
    "extrusion": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        parameters=(
            _function_parameter(
                "profile", GeometryParameterKind.POINTS3, unit=_METER
            ),
            _function_parameter(
                "vector", GeometryParameterKind.VECTOR3, unit=_METER
            ),
            # P092: an element's own seat height above its datum-bound
            # base_level (a sill, a frame seat); meaningless without one.
            _function_parameter(
                "base_offset",
                GeometryParameterKind.NUMBER,
                unit=_METER,
                required=False,
            ),
            _function_parameter(
                "hidden_for_inspection",
                GeometryParameterKind.BOOLEAN,
                required=False,
            ),
        ),
    ),
    "loft": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        parameters=(
            _function_parameter("cap_ends", GeometryParameterKind.BOOLEAN),
            _function_parameter(
                "closed_profile",
                GeometryParameterKind.BOOLEAN,
                required=False,
            ),
            _function_parameter(
                "loft_type",
                GeometryParameterKind.TEXT,
                required=False,
                allowed_values=("normal", "straight"),
            ),
            _function_parameter(
                "profile_basis",
                GeometryParameterKind.TEXT,
                required=False,
                allowed_values=("polyline", "interpolated"),
            ),
            _function_parameter(
                "profile_size", GeometryParameterKind.INTEGER
            ),
            _function_parameter(
                "profiles", GeometryParameterKind.POINTS3, unit=_METER
            ),
        ),
    ),
    "revolve": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        parameters=(
            _function_parameter(
                "axis_end", GeometryParameterKind.VECTOR3, unit=_METER
            ),
            _function_parameter(
                "axis_start", GeometryParameterKind.VECTOR3, unit=_METER
            ),
            _function_parameter(
                "end_radius", GeometryParameterKind.NUMBER, unit=_METER
            ),
            _function_parameter(
                "start_radius", GeometryParameterKind.NUMBER, unit=_METER
            ),
            _function_parameter(
                "base_offset", GeometryParameterKind.NUMBER, unit=_METER,
                required=False,
            ),
        ),
    ),
    "solid": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        parameters=(
            _function_parameter(
                "origin", GeometryParameterKind.VECTOR3, unit=_METER
            ),
            _function_parameter(
                "size", GeometryParameterKind.VECTOR3, unit=_METER
            ),
        ),
    ),
    "sweep": _function_contract(
        minimum_inputs=0,
        maximum_inputs=0,
        parameters=(
            _function_parameter("cap_ends", GeometryParameterKind.BOOLEAN),
            _function_parameter(
                "closed_profile", GeometryParameterKind.BOOLEAN
            ),
            _function_parameter(
                "frame_mode",
                GeometryParameterKind.TEXT,
                allowed_values=("fixed",),
            ),
            _function_parameter(
                "path", GeometryParameterKind.POINTS3, unit=_METER
            ),
            _function_parameter(
                "profile", GeometryParameterKind.POINTS3, unit=_METER
            ),
        ),
    ),
    "transform": _function_contract(
        minimum_inputs=1,
        maximum_inputs=1,
        parameters=(
            _function_parameter("matrix", GeometryParameterKind.MATRIX4),
        ),
    ),
}


def _strict_object(
    properties: Mapping[str, object],
    *,
    optional: tuple[str, ...] = (),
    description: str | None = None,
) -> dict[str, object]:
    contract: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": [name for name in properties if name not in optional],
        "properties": dict(properties),
    }
    if description is not None:
        contract["description"] = description
    return contract


def _array_contract(
    items: Mapping[str, object],
    *,
    minimum: int = 0,
    unique: bool = False,
    description: str | None = None,
) -> dict[str, object]:
    contract: dict[str, object] = {
        "type": "array",
        "items": dict(items),
        "minItems": minimum,
    }
    if unique:
        contract["uniqueItems"] = True
    if description is not None:
        contract["description"] = description
    return contract


def _relational_authoring_invariants() -> tuple[dict[str, str], ...]:
    """Publish compiler relations and their model guidance from one source."""

    return (
        {
            "id": "predecessor_matches_available_program",
            "field": "proposal_body.predecessor_program_digest",
            "relation": "equals",
            "target": "available_predecessor_program_digest",
            "instruction": (
                "Set proposal_body.predecessor_program_digest exactly to "
                "available_predecessor_program_digest; null means this is an "
                "initial proposal and no predecessor is available."
            ),
        },
        {
            "id": "assembly_host_is_not_a_member",
            "field": "proposal_body.assemblies[*].host_object_id",
            "relation": "not_member_of",
            "target": "proposal_body.assemblies[*].members[*].object_ids",
            "instruction": (
                "Keep each assembly host_object_id distinct from every object_id "
                "listed by that assembly's members."
            ),
        },
        {
            "id": "host_cut_depends_on_named_host",
            "field": (
                "proposal_body.assemblies[*].members[role=host_cut].object_ids[*]"
            ),
            "relation": "produced_by_operation_with_input",
            "target": "proposal_body.assemblies[*].host_object_id",
            "instruction": (
                "For every host_cut member object, its producing operation must "
                "include that assembly's host_object_id in input_object_ids."
            ),
        },
        {
            "id": "host_cut_is_aperture_volume",
            "field": (
                "proposal_body.assemblies[*].members[role=host_cut].object_ids[*]"
            ),
            "relation": "produced_by_boolean_intersection",
            "target": "named_host_intersected_with_explicit_cutter_volume",
            "instruction": (
                "Every host_cut member must be the boolean_intersection output "
                "of its named host and an explicit cutter: it represents the "
                "aperture volume used as opening evidence, never host-minus-cutter, "
                "cutter-minus-host, or a residual wall. Author a separate "
                "boolean_difference output when residual host material is needed."
            ),
        },
        {
            "id": "hosted_component_has_dedicated_binding_and_assembly",
            "field": "required_hosted_component_bindings[*]",
            "relation": "realized_by_exact_dedicated_binding_and_assembly",
            "target": (
                "proposal_body.semantic_bindings + proposal_body.assemblies + "
                "proposal_body.operations"
            ),
            "instruction": (
                "For every required_hosted_component_bindings item, create "
                "exactly one semantic binding whose component_id equals "
                "the required component_id, then exactly one assembly of assembly_kind "
                "whose semantic_binding_ids equals [that binding_id]. Keep member "
                "object ids disjoint between requirements; include each assembly "
                "host and member object in that binding.object_ids and in the "
                "semantic_binding_ids of its producing operation."
            ),
        },
    )


def _realization_authoring_contract(
    commitments: tuple[str, ...],
    supplied_requirements: tuple[dict[str, object], ...],
) -> dict[str, object]:
    instructions = [
        (
            "Treat every unconsumed non-reference non-curve output object as "
            "terminal physical geometry in deterministic realization."
        ),
        (
            "A terminal solid occupies its full origin-plus-size volume as "
            "material; it is not an abstract room or envelope. Consume solids "
            "through explicit boolean operations when the terminal result must "
            "contain usable void."
        ),
        (
            "Boolean results affect only their explicit output and inputs; a "
            "host_cut member does not automatically cut an unrelated terminal "
            "solid."
        ),
        (
            "The validation voxel resolution is supplied as an exact realization "
            "requirement. Physical geometry occupies every cell with positive-volume "
            "overlap, even when most of that cell is empty."
        ),
        (
            "Clear height is the integer Y-cell offset from each walkable cell to "
            "the nearest occupied cell above. A minimum N requires that nearest "
            "blocker offset to be at least N under positive-overlap occupancy."
        ),
        (
            "An exterior entrance is counted only where a host_cut aperture-volume "
            "cell is unoccupied, walkable, and lies on the minimum or maximum X or "
            "Z column of the derived occupied envelope."
        ),
    ]
    required_properties: list[dict[str, object]] = [
        dict(item) for item in supplied_requirements
    ]
    if "commitment:maintain-egress" in commitments:
        walkable_instruction = (
            "The supplied commitment:maintain-egress requires terminal geometry "
            "to realize at least one connected walkable region with occupied "
            "support below and clear space above; do not leave a full-envelope "
            "terminal solid filling the required use zones."
        )
        instructions.append(walkable_instruction)
        required_properties.append(
            {
                "schema": "GeometryRealizationPropertyRequirement@1",
                "source_ref": "commitment:maintain-egress",
                "property": "connected_walkable_region",
                "minimum_count": 1,
                "support": "occupied_material_below",
                "clearance": "empty_space_above",
            }
        )
    return {
        "schema": "GeometryRealizationAuthoringContract@1",
        "terminal_physical_rule": (
            "output_not_consumed_and_not_reference_and_not_curve"
        ),
        "terminal_solid_semantics": "occupied_material_volume",
        "boolean_scope": "explicit_inputs_and_result_only",
        "host_cut_scope": "aperture_volume_equals_host_intersection_cutter",
        "voxel_occupancy_rule": "positive_volume_overlap",
        "clear_height_rule": "nearest_occupied_positive_y_cell_offset",
        "exterior_opening_rule": (
            "host_cut_cell_and_unoccupied_and_walkable_and_envelope_xz_boundary"
        ),
        "required_properties": required_properties,
        "instructions": instructions,
        "proof_authority": "deterministic_runtime_realization_and_usability",
    }


def _realization_requirements(
    values: tuple[Mapping[str, object], ...],
) -> tuple[dict[str, object], ...]:
    if not isinstance(values, tuple):
        raise GeometryProposalProductionError(
            "realization_requirements must be a tuple"
        )
    normalized: list[dict[str, object]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "requirement_id",
            "source_refs",
            "property",
            "relation",
            "threshold_json",
            "unit",
        }:
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] schema drifted"
            )
        if value["schema"] != "GeometryRealizationRequirement@1":
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] schema changed"
            )
        requirement_id = value["requirement_id"]
        property_name = value["property"]
        relation = value["relation"]
        unit = value["unit"]
        if (
            not isinstance(requirement_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", requirement_id)
            is None
            or not isinstance(property_name, str)
            or not property_name
            or relation not in {"exact", "minimum", "required"}
            or not isinstance(unit, str)
            or not unit
        ):
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] contains invalid fields"
            )
        source_refs = value["source_refs"]
        if (
            not isinstance(source_refs, list)
            or not source_refs
            or source_refs != sorted(set(source_refs))
            or any(
                not isinstance(ref, str)
                or re.fullmatch(PORTABLE_LOGICAL_REF_PATTERN, ref) is None
                for ref in source_refs
            )
        ):
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] source_refs are invalid"
            )
        threshold_json = value["threshold_json"]
        if not isinstance(threshold_json, str):
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] threshold_json must be text"
            )
        try:
            threshold = json.loads(threshold_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] threshold_json is invalid"
            ) from exc
        if canonical_json(threshold) != threshold_json:
            raise GeometryProposalProductionError(
                f"realization_requirements[{index}] threshold_json is not canonical"
            )
        normalized.append(dict(value))
    normalized.sort(key=lambda item: str(item["requirement_id"]))
    ids = [str(item["requirement_id"]) for item in normalized]
    if len(ids) != len(set(ids)):
        raise GeometryProposalProductionError(
            "realization requirement ids must be unique"
        )
    return tuple(normalized)


def _authoring_output_contract(
    available_interface_refs: tuple[str, ...] | None = None,
    *,
    expected_predecessor_program_digest: str | None = None,
    required_hosted_component_bindings: tuple[dict[str, object], ...] = (),
    required_geometry_component_ids: tuple[str, ...] = (),
    predecessor_revision_contract: Mapping[str, object] | None = None,
    realization_contract: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Expose the exact generic parser topology without a building answer."""

    edit_mode = expected_predecessor_program_digest is not None

    text = {"type": "string", "minLength": 1}
    identifier = {
        **text,
        "description": "Portable identifier; use only identities authored in this proposal or supplied records.",
    }
    logical_ref = {
        **text,
        "pattern": PORTABLE_LOGICAL_REF_PATTERN,
        "description": (
            "Stable logical or project record reference supplied by the request "
            "in portable scheme:path form; bare identifiers, machine paths, "
            "and file: URIs are rejected."
        ),
    }
    interface_ref = dict(logical_ref)
    if available_interface_refs is not None:
        interface_ref["enum"] = list(available_interface_refs)
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    number = {"type": "number"}
    string_list = _array_contract(
        identifier,
        unique=True,
        description="Unique lexicographically sorted identifiers.",
    )
    ref_list = _array_contract(
        logical_ref,
        unique=True,
        description="Unique lexicographically sorted references.",
    )
    vector3 = _array_contract(number, minimum=3)
    vector3["maxItems"] = 3
    matrix4 = _array_contract(number, minimum=16)
    matrix4["maxItems"] = 16
    nullable_identifier = {
        "anyOf": [{"type": "null"}, identifier],
    }
    nullable_unit = {
        "anyOf": [
            {"type": "null"},
            {"type": "string", "enum": [item.value for item in LengthUnit]},
        ]
    }

    transform = _strict_object(
        {
            "schema": {"const": AffineTransform.SCHEMA},
            "matrix": matrix4,
        }
    )
    frame = _strict_object(
        {
            "schema": {"const": CoordinateFrame.SCHEMA},
            "frame_id": identifier,
            "parent_frame_id": nullable_identifier,
            "transform_from_parent": transform,
            "source_refs": _array_contract(logical_ref, minimum=1, unique=True),
        }
    )
    parameter = _strict_object(
        {
            "schema": {"const": GeometryParameter.SCHEMA},
            "name": identifier,
            "kind": {
                "type": "string",
                "enum": [item.value for item in GeometryParameterKind],
            },
            "value_json": {
                "type": "string",
                "description": (
                    "Canonical compact JSON text encoding the typed value; for example a vector is encoded as the string [x,y,z], not as a JSON array field."
                ),
            },
            "unit": nullable_unit,
        }
    )
    asset = _strict_object(
        {
            "schema": {"const": AssetReference.SCHEMA},
            "asset_id": identifier,
            "uri": {
                **text,
                "pattern": ASSET_URI_PATTERN,
                "description": (
                    "Stable scheme-qualified asset URI (scheme://path); "
                    "file:// URIs are rejected."
                ),
            },
            "media_type": text,
            "sha256": digest,
            "native_unit": {
                "type": "string",
                "enum": [item.value for item in LengthUnit],
            },
            "sockets": _array_contract(identifier, minimum=1, unique=True),
            "provenance_refs": _array_contract(logical_ref, minimum=1, unique=True),
        }
    )
    binding = _strict_object(
        {
            "schema": {"const": SemanticBinding.SCHEMA},
            "binding_id": identifier,
            "component_id": identifier,
            "object_ids": _array_contract(identifier, minimum=1, unique=True),
            "commitment_refs": ref_list,
            "evidence_refs": _array_contract(logical_ref, minimum=1, unique=True),
        }
    )
    operation = _strict_object(
        {
            "schema": {"const": GeometryOperation.SCHEMA},
            "op_id": identifier,
            "kind": {
                "type": "string",
                "enum": [item.value for item in GeometryOperationKind],
            },
            "output_object_ids": _array_contract(identifier, minimum=1, unique=True),
            "input_object_ids": _array_contract(
                identifier,
                unique=True,
                description=(
                    "Unique identifiers for objects consumed by this operation. "
                    "Keep empty when geometry_function_contracts[kind] allows zero inputs."
                ),
            ),
            "frame_id": identifier,
            "parameters": _array_contract(
                parameter,
                description=(
                    "Unique parameter name; order is canonicalized by name."
                ),
            ),
            "semantic_binding_ids": _array_contract(identifier, minimum=1, unique=True),
            "asset_id": nullable_identifier,
            "asset_socket_id": nullable_identifier,
            "asset_scale": {"anyOf": [{"type": "null"}, vector3]},
            "responds_to_object_ids": _array_contract(
                identifier,
                unique=True,
                description=(
                    "Semantic dependency subset of input_object_ids; never name "
                    "an object that this operation does not consume."
                ),
            ),
            "responds_to_frame_ids": string_list,
            "responds_to_binding_ids": _array_contract(
                identifier,
                unique=True,
                description=(
                    "Semantic dependency subset of semantic_binding_ids."
                ),
            ),
            "statements": {
                "type": "object",
                "propertyNames": dict(identifier),
                "additionalProperties": text,
                "description": (
                    "Optional declared facts about this operation that take no "
                    "part in its geometry and are exported as archflow:<key> "
                    "user strings. Values are already-formatted text. No "
                    "function contract names them, so they are never "
                    "parameters; omit the field when there are none."
                ),
            },
        },
        optional=("statements",),
        description=(
            "Use asset fields only for asset_instance. Parameter names and kinds must exactly follow geometry_function_contracts[kind]; statements are declared facts for the export, not parameters."
        ),
    )
    member = _strict_object(
        {
            "schema": {"const": AssemblyMember.SCHEMA},
            "role": {
                "type": "string",
                "enum": [item.value for item in AssemblyRole],
            },
            "object_ids": _array_contract(identifier, minimum=1, unique=True),
        }
    )
    assembly = _strict_object(
        {
            "schema": {"const": HostedAssembly.SCHEMA},
            "assembly_id": identifier,
            "kind": {
                "type": "string",
                "enum": [item.value for item in AssemblyKind],
            },
            "host_object_id": identifier,
            "host_socket_id": identifier,
            "members": _array_contract(
                member,
                minimum=1,
                description=(
                    "Unique roles; provider order carries no meaning and is "
                    "canonicalized lexicographically by role."
                ),
            ),
            "interface_refs": _array_contract(interface_ref, minimum=1, unique=True),
            "semantic_binding_ids": _array_contract(identifier, minimum=1, unique=True),
            "maturity": {
                "type": "string",
                "enum": [item.value for item in DetailMaturity],
            },
        },
        description=(
            "A hosted semantic component and its geometry are one typed assembly; "
            "bind it to the same semantic binding ids as its member objects and "
            "include every role listed by required_assembly_roles[kind]."
        ),
    )

    def lifecycle(schema: str) -> dict[str, object]:
        return _strict_object(
            {
                "schema": {"const": schema},
                "object_id": identifier,
                "expected_digest": digest,
                "reason_refs": _array_contract(logical_ref, minimum=1, unique=True),
            }
        )

    proposal_body = _strict_object(
        {
            "schema": {"const": _PROPOSAL_BODY_SCHEMA},
            "proposal_id": identifier,
            "predecessor_program_digest": {
                "const": expected_predecessor_program_digest,
                "description": (
                    "Exact predecessor published as "
                    "available_predecessor_program_digest; null only for an "
                    "initial proposal."
                ),
            },
            "length_unit": {
                "type": "string",
                "enum": [item.value for item in LengthUnit],
            },
            "tolerance": _strict_object(
                {
                    "schema": {"const": GeometryTolerance.SCHEMA},
                    "linear": {"type": "number", "exclusiveMinimum": 0},
                    "angular_radians": {"type": "number", "exclusiveMinimum": 0},
                }
            ),
            "frames": _array_contract(
                frame,
                minimum=1,
                description="Unique frame_id; order is canonicalized by frame_id.",
            ),
            "assets": _array_contract(
                asset,
                description="Unique asset_id; order is canonicalized by asset_id.",
            ),
            "semantic_bindings": _array_contract(
                binding,
                minimum=1,
                description="Unique binding_id; order is canonicalized by binding_id.",
            ),
            "operations": _array_contract(
                operation,
                minimum=1,
                description="Unique op_id; order is canonicalized by op_id.",
            ),
            "assemblies": _array_contract(
                assembly,
                description="Unique assembly_id; order is canonicalized by assembly_id.",
            ),
            "revisions": _array_contract(
                lifecycle(ObjectRevisionPrecondition.SCHEMA),
                description="Unique object_id; order is canonicalized by object_id.",
            ),
            "retirements": _array_contract(
                lifecycle(ObjectRetirement.SCHEMA),
                description="Unique object_id; order is canonicalized by object_id.",
            ),
        }
    )
    edit_body = _strict_object(
        {
            "schema": {"const": _EDIT_BODY_SCHEMA},
            "proposal_id": identifier,
            "predecessor_program_digest": {
                "const": expected_predecessor_program_digest,
                "description": (
                    "Exact predecessor published as "
                    "available_predecessor_program_digest."
                ),
            },
            "frame_upserts": _array_contract(
                frame,
                description=(
                    "Complete new or replacement CoordinateFrame values; "
                    "omitted predecessor frames remain byte-for-byte unchanged."
                ),
            ),
            "asset_upserts": _array_contract(
                asset,
                description=(
                    "Complete new or replacement AssetReference values; "
                    "omitted predecessor assets remain unchanged."
                ),
            ),
            "semantic_binding_upserts": _array_contract(
                binding,
                description=(
                    "Complete new or replacement GeometrySemanticBinding values; "
                    "omitted predecessor bindings remain unchanged."
                ),
            ),
            "operation_upserts": _array_contract(
                operation,
                description=(
                    "Complete new or replacement GeometryOperation values; "
                    "omitted predecessor operations remain unchanged."
                ),
            ),
            "assembly_upserts": _array_contract(
                assembly,
                description=(
                    "Complete new or replacement HostedAssembly values; "
                    "omitted predecessor assemblies remain unchanged."
                ),
            ),
            "remove_frame_ids": string_list,
            "remove_asset_ids": string_list,
            "remove_semantic_binding_ids": string_list,
            "remove_operation_ids": string_list,
            "remove_assembly_ids": string_list,
            "revisions": _array_contract(
                lifecycle(ObjectRevisionPrecondition.SCHEMA),
                description="Exact prior-object tokens for changed retained objects.",
            ),
            "retirements": _array_contract(
                lifecycle(ObjectRetirement.SCHEMA),
                description="Exact prior-object tokens for removed objects.",
            ),
        },
        description=(
            "A model-authored typed edit over one exact predecessor. The "
            "framework only merges identities deterministically; it does not "
            "invent operations, bindings, assemblies, revisions, or removals."
        ),
    )
    body_key = "edit_body" if edit_mode else "proposal_body"
    body_contract = edit_body if edit_mode else proposal_body
    output_schema = (
        _EDIT_AUTHORING_OUTPUT_SCHEMA if edit_mode else _AUTHORING_OUTPUT_SCHEMA
    )
    output = _strict_object(
        {
            "schema": {"const": output_schema},
            "selected_template_refs": _array_contract(
                logical_ref,
                unique=True,
                description=(
                    "Unique lexicographically sorted project URIs selected only from available_template_records."
                ),
            ),
            body_key: body_contract,
        }
    )
    item_prefix = "edit_body" if edit_mode else "proposal_body"
    operation_field = (
        f"{item_prefix}.operation_upserts[*]"
        if edit_mode
        else f"{item_prefix}.operations[*]"
    )
    assembly_field = (
        f"{item_prefix}.assembly_upserts[*]"
        if edit_mode
        else f"{item_prefix}.assemblies[*]"
    )
    relational_invariants = []
    for invariant in _relational_authoring_invariants():
        item = dict(invariant)
        if edit_mode:
            item["field"] = item["field"].replace(
                "proposal_body.operations[*]",
                "edit_body.operation_upserts[*]",
            ).replace(
                "proposal_body.assemblies[*]",
                "edit_body.assembly_upserts[*]",
            )
            item["target"] = item["target"].replace(
                "proposal_body.operations",
                "merged predecessor plus edit_body.operation_upserts",
            ).replace(
                "proposal_body.assemblies",
                "merged predecessor plus edit_body.assembly_upserts",
            ).replace(
                "proposal_body.semantic_bindings",
                "merged predecessor plus edit_body.semantic_binding_upserts",
            )
        relational_invariants.append(item)
    return {
        "schema": (
            "GeometryProgramEditAuthoringContract@1"
            if edit_mode
            else "GeometryProposalAuthoringContract@1"
        ),
        "json_schema": output,
        "authority": {
            "proposal_only": True,
            "hard_gate": False,
            "canonical_write": False,
            "platform_mutation": False,
        },
        "required_assembly_roles": {
            kind.value: [
                role.value for role in required_assembly_roles(kind)
            ]
            for kind in AssemblyKind
        },
        "required_hosted_component_bindings": [
            dict(item) for item in required_hosted_component_bindings
        ],
        "required_geometry_component_ids": list(
            required_geometry_component_ids
        ),
        "predecessor_revision_contract": (
            {}
            if predecessor_revision_contract is None
            else dict(predecessor_revision_contract)
        ),
        "realization_contract": (
            {} if realization_contract is None else dict(realization_contract)
        ),
        "coordinate_convention": _GEOMETRY_COORDINATE_CONVENTION,
        "cross_field_invariants": [
            {
                "field": f"{operation_field}.responds_to_object_ids",
                "relation": "subset_of",
                "target": f"{operation_field}.input_object_ids",
            },
            {
                "field": f"{operation_field}.responds_to_binding_ids",
                "relation": "subset_of",
                "target": f"{operation_field}.semantic_binding_ids",
            },
            {
                "field": f"{operation_field}.input_object_ids",
                "relation": "matches_function_input_arity",
                "target": "geometry_function_contracts[kind]",
            },
            {
                "field": f"{assembly_field}.members[*].role",
                "relation": "contains_all_unique",
                "target": "required_assembly_roles[kind]",
            },
            *relational_invariants,
        ],
    }


class GeometryProposalProductionError(ValueError):
    """Inputs, records, or provider output violate the producer contract."""


class GeometryProposalStatus(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    EXHAUSTED = "exhausted"


class GeometryProposalRoundStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class GeometryProposalProviderIdentity:
    provider_id: str
    model_id: str
    provider_version: str
    provider_fingerprint: str

    SCHEMA = "GeometryProposalProviderIdentity@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.provider_id, "provider_id"),
            (self.model_id, "model_id"),
            (self.provider_version, "provider_version"),
            (self.provider_fingerprint, "provider_fingerprint"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise GeometryProposalProductionError(
                    f"{field} must be non-empty text"
                )

    def matches(self, receipt: ModelInvocationReceipt) -> bool:
        return (
            receipt.provider_id == self.provider_id
            and receipt.model_id == self.model_id
            and receipt.provider_version == self.provider_version
            and receipt.provider_fingerprint == self.provider_fingerprint
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalProviderIdentity:
        payload = _mapping(value, "provider identity")
        _exact(
            payload,
            {
                "schema",
                "provider_id",
                "model_id",
                "provider_version",
                "provider_fingerprint",
            },
            "provider identity",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GeometryProposalProductionError(
                "provider identity schema changed"
            )
        return cls(
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            provider_version=payload["provider_version"],
            provider_fingerprint=payload["provider_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class GeometryProposalPolicy:
    """Round policy. Defaults are the experiment policy (one bounded budget,
    no completion, no partial acceptance). Production flags (P097) are
    opt-in and every use of them is recorded on the run.
    """

    maximum_rounds: int
    complete_bookkeeping: bool = False
    progress_budget: int | None = None
    partial_acceptance: bool = False
    escalate_on_stall: bool = False

    SCHEMA = "GeometryProposalPolicy@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_rounds, int)
            or isinstance(self.maximum_rounds, bool)
            or not 1 <= self.maximum_rounds <= 16
        ):
            raise GeometryProposalProductionError(
                "maximum_rounds must be between 1 and 16"
            )
        for name in ("complete_bookkeeping", "partial_acceptance", "escalate_on_stall"):
            if not isinstance(getattr(self, name), bool):
                raise GeometryProposalProductionError(f"{name} must be a bool")
        if self.progress_budget is not None and (
            not isinstance(self.progress_budget, int)
            or isinstance(self.progress_budget, bool)
            or not self.maximum_rounds <= self.progress_budget <= 32
        ):
            raise GeometryProposalProductionError(
                "progress_budget must be between maximum_rounds and 32"
            )

    @property
    def production(self) -> bool:
        return bool(
            self.complete_bookkeeping
            or self.progress_budget is not None
            or self.partial_acceptance
            or self.escalate_on_stall
        )

    @property
    def round_cap(self) -> int:
        return self.progress_budget or self.maximum_rounds

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "maximum_rounds": self.maximum_rounds,
            "complete_bookkeeping": self.complete_bookkeeping,
            "progress_budget": self.progress_budget,
            "partial_acceptance": self.partial_acceptance,
            "escalate_on_stall": self.escalate_on_stall,
        }


_PRODUCTION_AUTHORITY = (
    "canonical_write_authority",
    "design_authority",
    "stage_acceptance_authority",
)
_COMPLETABLE_CODES = frozenset({
    GeometryIssueCode.MISSING_REVISION_PRECONDITION,
    GeometryIssueCode.UNACKNOWLEDGED_SEMANTIC_CHANGE,
    GeometryIssueCode.UNACKNOWLEDGED_DEPENDENCY_CHANGE,
    GeometryIssueCode.UNACKNOWLEDGED_FRAME_CHANGE,
})
_DEFERRABLE_CODES = _COMPLETABLE_CODES | frozenset({
    GeometryIssueCode.UNKNOWN_BINDING,
    GeometryIssueCode.UNOWNED_OBJECT,
    GeometryIssueCode.AMBIGUOUS_OBJECT_OWNER,
    GeometryIssueCode.UNKNOWN_OBJECT,
    GeometryIssueCode.DUPLICATE_OBJECT,
    GeometryIssueCode.UNKNOWN_ASSET,
    GeometryIssueCode.MISSING_ASSET,
    GeometryIssueCode.INVALID_ASSET_SUBSTITUTION,
    GeometryIssueCode.UNKNOWN_DATUM,
    GeometryIssueCode.INVALID_DATUM_BINDING,
    GeometryIssueCode.RESTATED_DATUM_PARAMETER,
})


def complete_bookkeeping(
    proposal: GeometryProgramProposal,
    prior_program: CompiledGeometryProgram,
    issues: tuple[GeometryIssue, ...],
    *,
    reason_ref: str,
) -> tuple[GeometryProgramProposal, dict[str, object]] | None:
    """Fill derivable edit bookkeeping the model omitted (P097 knob 1).

    Only the four completable codes are handled and only when every
    issue is one of them. A revision precondition is the prior object's
    exact digest; an acknowledgement names the changed binding, input or
    frame the compiler already identified. Returns the completed proposal
    and a completion summary, or None when completion does not apply.
    """

    if not issues or any(item.code not in _COMPLETABLE_CODES for item in issues):
        return None
    prior_objects = {item.object_id: item.object_digest for item in prior_program.objects}
    revisions = {item.object_id: item for item in proposal.revisions}
    operations = {op.op_id: op for op in proposal.operations}
    added_revisions: list[str] = []
    acknowledgements: list[dict[str, str]] = []
    for issue in issues:
        if issue.code is GeometryIssueCode.MISSING_REVISION_PRECONDITION:
            object_id = issue.subject_id
            if object_id in revisions or object_id not in prior_objects:
                return None
            revisions[object_id] = ObjectRevisionPrecondition(
                object_id=object_id,
                expected_digest=prior_objects[object_id],
                reason_refs=(reason_ref,),
            )
            added_revisions.append(object_id)
            continue
        op = operations.get(issue.subject_id)
        if op is None:
            return None
        changed = issue.detail.rsplit(" ", 1)[-1]
        if issue.code is GeometryIssueCode.UNACKNOWLEDGED_SEMANTIC_CHANGE:
            op = replace(op, responds_to_binding_ids=tuple(sorted(set(op.responds_to_binding_ids) | {changed})))
            acknowledgements.append({"op_id": op.op_id, "binding_id": changed})
        elif issue.code is GeometryIssueCode.UNACKNOWLEDGED_DEPENDENCY_CHANGE:
            op = replace(op, responds_to_object_ids=tuple(sorted(set(op.responds_to_object_ids) | {changed})))
            acknowledgements.append({"op_id": op.op_id, "object_id": changed})
        else:
            op = replace(op, responds_to_frame_ids=tuple(sorted(set(op.responds_to_frame_ids) | {op.frame_id})))
            acknowledgements.append({"op_id": op.op_id, "frame_id": op.frame_id})
        operations[op.op_id] = op
    completed = replace(
        proposal,
        revisions=tuple(sorted(revisions.values(), key=lambda item: item.object_id)),
        operations=tuple(operations[op.op_id] for op in proposal.operations),
    )
    summary = {
        "schema": "ProtocolCompletion@1",
        "reason_ref": reason_ref,
        "revision_preconditions": sorted(added_revisions),
        "acknowledgements": acknowledgements,
        "authored_proposal_digest": proposal.proposal_digest,
        "completed_proposal_digest": completed.proposal_digest,
    }
    return completed, summary


def reduce_for_partial_acceptance(
    proposal: GeometryProgramProposal,
    prior_program: CompiledGeometryProgram | None,
    issues: tuple[GeometryIssue, ...],
    datum_bindings: tuple[DatumBinding, ...],
) -> tuple[GeometryProgramProposal, tuple[DatumBinding, ...], dict[str, object]] | None:
    """Drop the failing objects and their dependents (P097 knob 4).

    A failing op that exists in the prior program is restored from it
    (the change is deferred); a failing new op is removed together with
    every op consuming its outputs. Returns the reduced proposal, the
    surviving datum bindings and a deferral summary, or None when any
    issue is not object-scoped or not deferrable.
    """

    if not issues or any(item.code not in _DEFERRABLE_CODES for item in issues):
        return None
    ops = {op.op_id: op for op in proposal.operations}
    producer_of = {oid: op.op_id for op in proposal.operations for oid in op.output_object_ids}
    failing: set[str] = set()
    for item in issues:
        if item.subject_id in ops:
            failing.add(item.subject_id)
        elif item.subject_id in producer_of:
            failing.add(producer_of[item.subject_id])
        else:
            return None
    prior_ops = {} if prior_program is None else {op.op_id: op for op in prior_program.proposal.operations}
    restored: dict[str, object] = {}
    removed: set[str] = set()
    queue = sorted(failing)
    while queue:
        op_id = queue.pop()
        if op_id in restored or op_id in removed:
            continue
        if op_id in prior_ops:
            restored[op_id] = prior_ops[op_id]
            continue
        removed.add(op_id)
        outputs = set(ops[op_id].output_object_ids)
        for other in proposal.operations:
            if other.op_id not in removed and other.op_id not in restored and outputs & set(other.input_object_ids):
                queue.append(other.op_id)
    removed_objects = {oid for op_id in removed for oid in ops[op_id].output_object_ids}
    restored_objects = {oid for op_id in restored for oid in ops[op_id].output_object_ids}
    operations = tuple(restored.get(op.op_id, op) for op in proposal.operations if op.op_id not in removed)
    bindings = []
    for binding in proposal.semantic_bindings:
        kept = tuple(oid for oid in binding.object_ids if oid not in removed_objects)
        if kept:
            bindings.append(replace(binding, object_ids=kept) if kept != binding.object_ids else binding)
    revisions = tuple(item for item in proposal.revisions if item.object_id not in removed_objects | restored_objects)
    surviving = tuple(item for item in datum_bindings if item.op_id not in removed and item.op_id not in restored)
    reduced = replace(proposal, operations=operations, semantic_bindings=tuple(bindings), revisions=revisions)
    summary = {
        "schema": "PartialAcceptanceDeferral@1",
        "deferred_ops_removed": sorted(removed),
        "deferred_ops_restored_from_prior": sorted(restored),
        "deferred_object_ids": sorted(removed_objects | restored_objects),
        "issues": [{"code": item.code.value, "subject_id": item.subject_id, "detail": item.detail} for item in issues],
        "authored_proposal_digest": proposal.proposal_digest,
        "reduced_proposal_digest": reduced.proposal_digest,
    }
    return reduced, surviving, summary


@dataclass(frozen=True, slots=True)
class GeometryProposalIssue:
    code: str
    detail: str

    SCHEMA = "GeometryProposalIssue@1"

    def __post_init__(self) -> None:
        for value, field in ((self.code, "issue code"), (self.detail, "issue detail")):
            if not isinstance(value, str) or not value.strip():
                raise GeometryProposalProductionError(
                    f"{field} must be non-empty text"
                )

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "code": self.code, "detail": self.detail}

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalIssue:
        payload = _mapping(value, "geometry proposal issue")
        _exact(payload, {"schema", "code", "detail"}, "geometry proposal issue")
        if payload["schema"] != cls.SCHEMA:
            raise GeometryProposalProductionError("geometry issue schema changed")
        return cls(code=payload["code"], detail=payload["detail"])


@dataclass(frozen=True, slots=True)
class GeometryProposalRoundReceipt:
    round_id: str
    round_index: int
    status: GeometryProposalRoundStatus
    spatial_option_ref: ProjectRecordRef
    design_state_digest: str
    request: ModelInvocationRequest
    model_receipt: ModelInvocationReceipt
    selected_template_refs: tuple[str, ...]
    issues: tuple[GeometryProposalIssue, ...]
    proposal_digest: str | None
    compiler_receipt_json: str | None

    SCHEMA = "GeometryProposalRoundReceipt@2"

    def __post_init__(self) -> None:
        if not isinstance(self.round_id, str) or not self.round_id:
            raise GeometryProposalProductionError("round_id must be non-empty")
        if not isinstance(self.round_index, int) or isinstance(self.round_index, bool) or self.round_index < 1:
            raise GeometryProposalProductionError("round_index must be positive")
        if not isinstance(self.status, GeometryProposalRoundStatus):
            raise TypeError("status must be GeometryProposalRoundStatus")
        if not isinstance(self.spatial_option_ref, ProjectRecordRef):
            raise TypeError("spatial_option_ref must be ProjectRecordRef")
        require_sha256(self.design_state_digest, "design_state_digest")
        if not isinstance(self.request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        if not isinstance(self.model_receipt, ModelInvocationReceipt):
            raise TypeError("model_receipt must be ModelInvocationReceipt")
        _strings(self.selected_template_refs, "selected_template_refs", allow_empty=True)
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, GeometryProposalIssue) for item in self.issues
        ):
            raise TypeError("issues must contain GeometryProposalIssue values")
        if self.status is GeometryProposalRoundStatus.ACCEPTED:
            if self.issues or self.proposal_digest is None or self.compiler_receipt_json is None:
                raise GeometryProposalProductionError(
                    "accepted round requires proposal and compiler receipt without issues"
                )
        elif not self.issues:
            raise GeometryProposalProductionError(
                "rejected or refused round requires typed issues"
            )
        if self.proposal_digest is not None:
            require_sha256(self.proposal_digest, "proposal_digest")
        if self.compiler_receipt_json is not None:
            decoded = json.loads(self.compiler_receipt_json)
            if canonical_json(decoded) != self.compiler_receipt_json:
                raise GeometryProposalProductionError(
                    "compiler receipt JSON must be canonical"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "round_id": self.round_id,
            "round_index": self.round_index,
            "status": self.status.value,
            "spatial_option_ref": self.spatial_option_ref.to_dict(),
            "design_state_digest": self.design_state_digest,
            "request": self.request.to_dict(),
            "model_receipt": self.model_receipt.to_dict(),
            "selected_template_refs": list(self.selected_template_refs),
            "issues": [item.to_dict() for item in self.issues],
            "proposal_digest": self.proposal_digest,
            "compiler_receipt": (
                None if self.compiler_receipt_json is None else json.loads(self.compiler_receipt_json)
            ),
            "derivation_only": True,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalRoundReceipt:
        payload = _mapping(value, "geometry proposal round")
        _exact(
            payload,
            {
                "schema", "round_id", "round_index", "status",
                "spatial_option_ref", "design_state_digest", "request",
                "model_receipt", "selected_template_refs", "issues",
                "proposal_digest", "compiler_receipt", "derivation_only",
                "hard_gate_authority", "canonical_write_authority",
            },
            "geometry proposal round",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["derivation_only"] is not True
        ):
            raise GeometryProposalProductionError(
                "geometry proposal round acquired forbidden authority"
            )
        issues = payload["issues"]
        compiler_receipt = payload["compiler_receipt"]
        if not isinstance(issues, list):
            raise TypeError("round issues must be a list")
        return cls(
            round_id=payload["round_id"],
            round_index=payload["round_index"],
            status=GeometryProposalRoundStatus(payload["status"]),
            spatial_option_ref=ProjectRecordRef.from_dict(payload["spatial_option_ref"]),
            design_state_digest=payload["design_state_digest"],
            request=ModelInvocationRequest.from_dict(payload["request"]),
            model_receipt=ModelInvocationReceipt.from_dict(payload["model_receipt"]),
            selected_template_refs=_strings_from_json(
                payload["selected_template_refs"], "selected_template_refs"
            ),
            issues=tuple(GeometryProposalIssue.from_dict(item) for item in issues),
            proposal_digest=payload["proposal_digest"],
            compiler_receipt_json=(
                None if compiler_receipt is None else canonical_json(compiler_receipt)
            ),
        )


@dataclass(frozen=True, slots=True)
class GeometryProposalLineage:
    lineage_id: str
    status: GeometryProposalStatus
    project_id: str
    run_id: str
    base: ProjectVersionRef
    spatial_option_ref: ProjectRecordRef
    spatial_option_digest: str
    design_state_digest: str
    required_commitment_refs: tuple[str, ...]
    provider_identity: GeometryProposalProviderIdentity
    round_refs: tuple[ProjectRecordRef, ...]
    accepted_proposal_ref: ProjectRecordRef | None
    accepted_proposal_digest: str | None

    SCHEMA = "GeometryProposalLineage@2"

    def __post_init__(self) -> None:
        if not isinstance(self.lineage_id, str) or not self.lineage_id:
            raise GeometryProposalProductionError("lineage_id must be non-empty")
        if not isinstance(self.status, GeometryProposalStatus):
            raise TypeError("status must be GeometryProposalStatus")
        if self.base.project_id != self.project_id:
            raise GeometryProposalProductionError("lineage and base disagree")
        if self.spatial_option_ref.project_id != self.project_id:
            raise GeometryProposalProductionError("lineage source project disagrees")
        require_sha256(self.spatial_option_digest, "spatial_option_digest")
        require_sha256(self.design_state_digest, "design_state_digest")
        _strings(self.required_commitment_refs, "required_commitment_refs")
        if not isinstance(self.provider_identity, GeometryProposalProviderIdentity):
            raise TypeError("provider_identity is invalid")
        if not isinstance(self.round_refs, tuple) or not self.round_refs:
            raise GeometryProposalProductionError("lineage requires round refs")
        if any(item.project_id != self.project_id for item in self.round_refs):
            raise GeometryProposalProductionError("round refs cross project boundary")
        if self.status is GeometryProposalStatus.ACCEPTED:
            if self.accepted_proposal_ref is None or self.accepted_proposal_digest is None:
                raise GeometryProposalProductionError("accepted lineage requires proposal")
        elif self.accepted_proposal_ref is not None or self.accepted_proposal_digest is not None:
            raise GeometryProposalProductionError("failed lineage cannot carry proposal")
        if self.accepted_proposal_digest is not None:
            require_sha256(self.accepted_proposal_digest, "accepted_proposal_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "lineage_id": self.lineage_id,
            "status": self.status.value,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "spatial_option_ref": self.spatial_option_ref.to_dict(),
            "spatial_option_digest": self.spatial_option_digest,
            "design_state_digest": self.design_state_digest,
            "required_commitment_refs": list(self.required_commitment_refs),
            "provider_identity": self.provider_identity.to_dict(),
            "round_refs": [item.to_dict() for item in self.round_refs],
            "accepted_proposal_ref": (
                None if self.accepted_proposal_ref is None else self.accepted_proposal_ref.to_dict()
            ),
            "accepted_proposal_digest": self.accepted_proposal_digest,
            "proposal_only": True,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalLineage:
        payload = _mapping(value, "geometry proposal lineage")
        _exact(
            payload,
            {
                "schema", "lineage_id", "status", "project_id", "run_id",
                "base", "spatial_option_ref", "spatial_option_digest",
                "design_state_digest", "required_commitment_refs",
                "provider_identity", "round_refs", "accepted_proposal_ref",
                "accepted_proposal_digest", "proposal_only",
                "hard_gate_authority", "canonical_write_authority",
            },
            "geometry proposal lineage",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["proposal_only"] is not True
        ):
            raise GeometryProposalProductionError("lineage acquired forbidden authority")
        round_refs = payload["round_refs"]
        if not isinstance(round_refs, list):
            raise TypeError("round_refs must be a list")
        accepted_ref = payload["accepted_proposal_ref"]
        return cls(
            lineage_id=payload["lineage_id"],
            status=GeometryProposalStatus(payload["status"]),
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef.from_dict(payload["base"], "project base"),
            spatial_option_ref=ProjectRecordRef.from_dict(payload["spatial_option_ref"]),
            spatial_option_digest=payload["spatial_option_digest"],
            design_state_digest=payload["design_state_digest"],
            required_commitment_refs=_strings_from_json(
                payload["required_commitment_refs"], "required_commitment_refs"
            ),
            provider_identity=GeometryProposalProviderIdentity.from_dict(
                payload["provider_identity"]
            ),
            round_refs=tuple(ProjectRecordRef.from_dict(item) for item in round_refs),
            accepted_proposal_ref=(
                None if accepted_ref is None else ProjectRecordRef.from_dict(accepted_ref)
            ),
            accepted_proposal_digest=payload["accepted_proposal_digest"],
        )


@dataclass(frozen=True, slots=True)
class GeometryProposalProductionResult:
    status: GeometryProposalStatus
    lineage_ref: ProjectRecordRef
    round_refs: tuple[ProjectRecordRef, ...]
    proposal_ref: ProjectRecordRef | None
    proposal: GeometryProgramProposal | None
    program: CompiledGeometryProgram | None
    completion_refs: tuple[ProjectRecordRef, ...] = ()
    deferral_ref: ProjectRecordRef | None = None
    escalation_ref: ProjectRecordRef | None = None


@dataclass(frozen=True, slots=True)
class LoadedGeometryProposalLineage:
    lineage: GeometryProposalLineage
    rounds: tuple[GeometryProposalRoundReceipt, ...]
    proposal: GeometryProgramProposal | None


class GeometryProposalRepository(RecordSink, Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


def _as_developed_state(design_state, *, run: RunRef, phase: DesignPhase | None):
    """Accept the canonical ``StateRecord@1`` at the production entry (P102).

    Callers no longer author a ``DevelopedDesignState``; a record is
    forwarded through ``developed_design_view`` here, once, with lineage.
    A ``DevelopedDesignState`` is still accepted while the compiler reads
    the legacy shape, and it already states its own phase.

    ``phase`` is the phase of the run this production belongs to (ADR-007
    rule 1). A record states none, so a record arriving with no phase is
    refused here rather than projected in one this module chose.
    """

    from archflow.state.state_record import StateRecord, developed_design_view

    if isinstance(design_state, StateRecord):
        if not design_state.evidence_refs:
            raise GeometryProposalProductionError("a StateRecord at the production entry must carry at least one evidence ref")
        if phase is None:
            raise GeometryProposalProductionError(
                "a StateRecord at the production entry states no phase: pass the phase of "
                "the run's stage envelope (ADR-007 rule 1)"
            )
        return developed_design_view(design_state, run=run, evidence_ref=design_state.evidence_refs[0], phase=phase)
    return design_state


async def produce_geometry_program_proposal(
    repository: GeometryProposalRepository,
    provider: AsyncModelProvider,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    spatial_option_ref: ProjectRecordRef,
    design_state: DevelopedDesignState,
    required_commitment_refs: tuple[str, ...],
    provider_identity: GeometryProposalProviderIdentity,
    policy: GeometryProposalPolicy,
    template_refs: tuple[ProjectRecordRef, ...] = (),
    available_asset_digests: Mapping[str, str] | None = None,
    prior_program: CompiledGeometryProgram | None = None,
    required_geometry_component_ids: tuple[str, ...] = (),
    realization_requirements: tuple[Mapping[str, object], ...] = (),
    initial_repair_issues: tuple[GeometryProposalIssue, ...] = (),
    rejected_round_ref: ProjectRecordRef | None = None,
    interface_datums: tuple[InterfaceDatum, ...] = (),
    datum_bindings: tuple[DatumBinding, ...] = (),
    catalog_confrontation_ref: ProjectRecordRef | None = None,
    seat_scope: tuple[str, ...] | None = None,
    phase: DesignPhase | None = None,
) -> GeometryProposalProductionResult:
    """Author, compile, and persist bounded proposal rounds without fallback.

    ``seat_scope`` (P095) names the component ids one discipline seat may
    bind — its owned subtree, already expanded. A proposal that binds any
    other component is refused as a typed round issue.

    ``interface_datums``/``datum_bindings`` reach the compiler so bound
    parameters derive from published datums (P090 on the write path).
    ``catalog_confrontation_ref`` names a CatalogConfrontationReceipt@1
    record; when supplied, the model may select only templates that the
    confrontation selected (P093 on the write path) — a declined family
    cannot be quietly re-selected by the model.

    ``phase`` is read only when ``design_state`` is a ``StateRecord@1``,
    which states no phase of its own: it is then the phase of the run's
    stage envelope, and the record is refused without one. A
    ``DevelopedDesignState`` already carries the phase it was projected
    in, so a caller handing one states nothing twice.
    """

    destination = require_destination(destination, producer="geometry proposal producer")
    if not isinstance(interface_datums, tuple) or any(
        not isinstance(item, InterfaceDatum) for item in interface_datums
    ):
        raise TypeError("interface_datums contains an invalid item")
    if not isinstance(datum_bindings, tuple) or any(
        not isinstance(item, DatumBinding) for item in datum_bindings
    ):
        raise TypeError("datum_bindings contains an invalid item")
    if seat_scope is not None:
        if not isinstance(seat_scope, tuple) or not seat_scope or any(
            not isinstance(item, str) or not item for item in seat_scope
        ):
            raise TypeError("seat_scope must be a non-empty tuple of component ids")
        if tuple(sorted(set(seat_scope))) != seat_scope:
            raise TypeError("seat_scope must be sorted and unique")
    # the record binds the program; the projection answers the spatial-option and component questions (P102)
    state_binding = design_state
    design_state = _as_developed_state(design_state, run=run, phase=phase)
    _validate_inputs(
        run,
        destination,
        spatial_option_ref,
        design_state,
        required_commitment_refs,
        provider_identity,
        policy,
        template_refs,
        required_geometry_component_ids,
    )
    spatial_payload = repository.load_json(spatial_option_ref)
    spatial_option = SpatialOptionProposal.from_dict(spatial_payload)
    if (
        spatial_option
        != design_state.selected_schematic.option.proposal
    ):
        raise GeometryProposalProductionError(
            "developed design state does not select the supplied spatial option record"
        )
    template_payloads = tuple(
        {"ref": ref.to_dict(), "payload": repository.load_json(ref)}
        for ref in template_refs
    )
    allowed_template_uris = frozenset(ref.uri for ref in template_refs)
    if catalog_confrontation_ref is not None:
        if not isinstance(catalog_confrontation_ref, ProjectRecordRef):
            raise TypeError("catalog_confrontation_ref must be a ProjectRecordRef")
        confrontation = repository.load_json(catalog_confrontation_ref)
        if confrontation.get("schema") != "CatalogConfrontationReceipt@1":
            raise GeometryProposalProductionError(
                "catalog_confrontation_ref does not name a CatalogConfrontationReceipt@1"
            )
        confronted = frozenset(
            str(row["template_ref"])
            for row in confrontation.get("answers", ())
            if isinstance(row, Mapping) and row.get("answer") == "selected"
        )
        if not confronted <= allowed_template_uris:
            raise GeometryProposalProductionError(
                "catalog confrontation selected templates outside the supplied project records"
            )
        allowed_template_uris = confronted
    available_interface_refs = _available_interface_refs(
        spatial_option,
    )
    expected_predecessor_program_digest = (
        None if prior_program is None else prior_program.program_digest
    )
    required_hosted_component_bindings: tuple[dict[str, object], ...] = ()
    exact_realization_requirements = _realization_requirements(
        realization_requirements
    )
    if not isinstance(initial_repair_issues, tuple) or any(
        not isinstance(item, GeometryProposalIssue)
        for item in initial_repair_issues
    ):
        raise GeometryProposalProductionError(
            "initial_repair_issues must contain GeometryProposalIssue values"
        )
    if rejected_round_ref is not None and not isinstance(
        rejected_round_ref,
        ProjectRecordRef,
    ):
        raise TypeError("rejected_round_ref must be a ProjectRecordRef or None")
    if rejected_round_ref is not None and initial_repair_issues:
        raise GeometryProposalProductionError(
            "rejected_round_ref already owns the exact repair issues"
        )
    realization_contract = _realization_authoring_contract(
        required_commitment_refs,
        exact_realization_requirements,
    )
    round_refs: list[ProjectRecordRef] = []
    repair_issues = initial_repair_issues
    repair_context: dict[str, object] | None = None
    assets = {} if available_asset_digests is None else dict(available_asset_digests)

    if rejected_round_ref is not None:
        rejected_round = GeometryProposalRoundReceipt.from_dict(
            repository.load_json(rejected_round_ref)
        )
        _validate_rejected_round_resume(
            rejected_round_ref,
            rejected_round,
            spatial_option_ref=spatial_option_ref,
            design_state=design_state,
            required_commitment_refs=required_commitment_refs,
            provider_identity=provider_identity,
            expected_predecessor_program_digest=(
                expected_predecessor_program_digest
            ),
            required_geometry_component_ids=required_geometry_component_ids,
            realization_contract=realization_contract,
        )
        repair_issues = rejected_round.issues
        repair_context = _rejected_output_repair_context(
            rejected_round.model_receipt.output,
            rejected_round.proposal_digest,
            rejected_round.issues,
            rejected_round_ref=rejected_round_ref,
        )

    previous_issue_count: int | None = None
    last_proposal: GeometryProgramProposal | None = None
    last_compiler_issues: tuple[GeometryIssue, ...] = ()
    completion_refs: list[ProjectRecordRef] = []
    for round_index in range(1, policy.round_cap + 1):
        request_payload = _request_payload(
            spatial_option_ref,
            spatial_option,
            design_state,
            required_commitment_refs,
            template_payloads,
            repair_issues,
            repair_context,
            available_interface_refs,
            expected_predecessor_program_digest,
            required_hosted_component_bindings,
            required_geometry_component_ids,
            realization_contract,
            prior_program,
        )
        request = ModelInvocationRequest.create(
            request_id=f"geometry-proposal-{run.run_id}-{round_index:02d}",
            phase=ModelPhase.ACTION_PROPOSAL,
            checkpoint_digest=design_state.state_digest,
            context_digest=canonical_digest(request_payload),
            payload=request_payload,
        )
        receipt = await provider.invoke(request)
        issues: tuple[GeometryProposalIssue, ...]
        selected_templates: tuple[str, ...] = ()
        proposal: GeometryProgramProposal | None = None
        compiler_receipt: dict[str, object] | None = None
        round_status = GeometryProposalRoundStatus.REJECTED

        if receipt.request != request:
            issues = (
                GeometryProposalIssue(
                    "provider_request_mismatch",
                    "provider receipt does not bind the current request",
                ),
            )
            round_status = GeometryProposalRoundStatus.REFUSED
        elif not provider_identity.matches(receipt):
            issues = (
                GeometryProposalIssue(
                    "provider_identity_mismatch",
                    "provider or model identity changed; silent substitution rejected",
                ),
            )
            round_status = GeometryProposalRoundStatus.REFUSED
        elif receipt.status is not ModelInvocationStatus.SUCCESS:
            issues = (
                GeometryProposalIssue(
                    "provider_refused",
                    f"provider stopped with {receipt.status.value}: {receipt.error_code}",
                ),
            )
            round_status = GeometryProposalRoundStatus.REFUSED
        else:
            # Collect every independent validation failure of this output so
            # one bounded repair round reports all of them together.  Failures
            # that make later checks impossible (unreadable envelope, drifted
            # body keys) still cascade into a single issue, and any issue
            # keeps the round rejected.
            collected: list[GeometryProposalIssue] = []
            body: Mapping[str, Any] | None = None
            try:
                selected_templates, body = _authoring_output(
                    receipt.output,
                    prior_program,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                collected.append(
                    GeometryProposalIssue("malformed_model_output", str(exc))
                )
            if body is not None:
                if not set(selected_templates) <= allowed_template_uris:
                    collected.append(
                        GeometryProposalIssue(
                            "malformed_model_output",
                            "model selected a template outside the supplied project records",
                        )
                    )
                body_errors: list[Exception] = []
                try:
                    proposal = _proposal_from_body(
                        body,
                        design_state,
                        prior_program,
                        errors=body_errors,
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    proposal = None
                    body_errors.append(exc)
                collected.extend(
                    GeometryProposalIssue("malformed_model_output", str(item))
                    for item in body_errors
                )
            if proposal is not None:
                _validate_function_contracts(proposal, issues=collected)
                _validate_semantic_coverage(
                    proposal,
                    design_state,
                    required_commitment_refs,
                    spatial_option_ref,
                    available_interface_refs,
                    required_hosted_component_bindings,
                    required_geometry_component_ids,
                    prior_program,
                    issues=collected,
                    seat_scope=seat_scope,
                )
            if proposal is not None and not collected:
                try:
                    compilation = compile_geometry_program(
                        state_binding,
                        proposal,
                        active_commitment_refs=required_commitment_refs,
                        available_asset_digests=assets,
                        prior_program=prior_program,
                        interface_datums=interface_datums,
                        datum_bindings=datum_bindings,
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    collected.append(
                        GeometryProposalIssue("malformed_model_output", str(exc))
                    )
                else:
                    compiler_receipt = compilation.receipt.to_dict()
                    compiler_issues = compilation.receipt.issues
                    if compilation.program is None and policy.complete_bookkeeping and prior_program is not None:
                        # P097 knob 1: derivable bookkeeping is filled from records,
                        # recompiled without a model call, and recorded.
                        completed = complete_bookkeeping(
                            proposal,
                            prior_program,
                            compiler_issues,
                            reason_ref=f"protocol-completion:{run.run_id}:round-{round_index:02d}",
                        )
                        if completed is not None:
                            proposal, completion_summary = completed
                            compilation = compile_geometry_program(
                                state_binding,
                                proposal,
                                active_commitment_refs=required_commitment_refs,
                                available_asset_digests=assets,
                                prior_program=prior_program,
                                interface_datums=interface_datums,
                                datum_bindings=datum_bindings,
                            )
                            compiler_receipt = compilation.receipt.to_dict()
                            compiler_issues = compilation.receipt.issues
                            if compilation.program is not None:
                                completion_refs.append(
                                    repository.put_json(
                                        run=run,
                                        destination=destination,
                                        record_kind=geometry_proposal_completion(round_index),
                                        payload={
                                            **completion_summary,
                                            "round_index": round_index,
                                            **no_authority(_PRODUCTION_AUTHORITY),
                                        },
                                    )
                                )
                    if compilation.program is None:
                        last_compiler_issues = compiler_issues
                        collected.extend(
                            _compiler_repair_issue(
                                item,
                                prior_program,
                            )
                            for item in compiler_issues
                        )
                    else:
                        round_status = GeometryProposalRoundStatus.ACCEPTED
                        program = compilation.program
            issues = tuple(collected)

        round_receipt = GeometryProposalRoundReceipt(
            round_id=geometry_proposal_round(round_index),
            round_index=round_index,
            status=round_status,
            spatial_option_ref=spatial_option_ref,
            design_state_digest=design_state.state_digest,
            request=request,
            model_receipt=receipt,
            selected_template_refs=selected_templates,
            issues=issues,
            proposal_digest=None if proposal is None else proposal.proposal_digest,
            compiler_receipt_json=(
                None if compiler_receipt is None else canonical_json(compiler_receipt)
            ),
        )
        round_ref = repository.put_json(
            run=run,
            destination=destination,
            record_kind=geometry_proposal_round(round_index),
            payload=round_receipt.to_dict(),
        )
        round_refs.append(round_ref)

        if round_status is GeometryProposalRoundStatus.ACCEPTED:
            assert proposal is not None
            proposal_ref = repository.put_json(
                run=run,
                destination=destination,
                record_kind=GEOMETRY_PROGRAM_PROPOSAL,
                payload=_proposal_record(
                    proposal,
                    spatial_option_ref,
                    required_commitment_refs,
                    provider_identity,
                    selected_templates,
                    round_ref,
                ),
            )
            lineage_ref = _persist_lineage(
                repository,
                run,
                destination,
                GeometryProposalStatus.ACCEPTED,
                spatial_option_ref,
                spatial_option.proposal_digest,
                design_state,
                required_commitment_refs,
                provider_identity,
                tuple(round_refs),
                proposal_ref,
                proposal.proposal_digest,
            )
            return GeometryProposalProductionResult(
                GeometryProposalStatus.ACCEPTED,
                lineage_ref,
                tuple(round_refs),
                proposal_ref,
                proposal,
                program,
                tuple(completion_refs),
            )
        if round_status is GeometryProposalRoundStatus.REFUSED:
            lineage_ref = _persist_lineage(
                repository,
                run,
                destination,
                GeometryProposalStatus.REFUSED,
                spatial_option_ref,
                spatial_option.proposal_digest,
                design_state,
                required_commitment_refs,
                provider_identity,
                tuple(round_refs),
                None,
                None,
            )
            return GeometryProposalProductionResult(
                GeometryProposalStatus.REFUSED,
                lineage_ref,
                tuple(round_refs),
                None,
                None,
                None,
            )
        repair_context = _rejected_output_repair_context(
            receipt.output,
            None if proposal is None else proposal.proposal_digest,
            issues,
            rejected_round_ref=None,
        )
        repair_issues = issues
        if proposal is not None:
            last_proposal = proposal
        # P097 knob 2: beyond the bounded budget, continue only while the
        # issue set strictly shrinks; a stall ends the run.
        issue_count = len(issues)
        if round_index >= policy.maximum_rounds and (
            policy.progress_budget is None
            or (previous_issue_count is not None and issue_count >= previous_issue_count)
        ):
            break
        previous_issue_count = issue_count

    # P097 knob 4: accept the reduced program when every remaining issue is
    # object-scoped; the dropped objects become typed deferrals.
    if policy.partial_acceptance and last_proposal is not None and last_compiler_issues:
        reduced = reduce_for_partial_acceptance(
            last_proposal, prior_program, last_compiler_issues, datum_bindings
        )
        if reduced is not None:
            reduced_proposal, surviving_bindings, deferral_summary = reduced
            try:
                reduced_compilation = compile_geometry_program(
                    state_binding,
                    reduced_proposal,
                    active_commitment_refs=required_commitment_refs,
                    available_asset_digests=assets,
                    prior_program=prior_program,
                    interface_datums=interface_datums,
                    datum_bindings=surviving_bindings,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                reduced_compilation = None
            if reduced_compilation is not None and reduced_compilation.program is not None:
                deferral_ref = repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind=GEOMETRY_PROPOSAL_DEFERRAL,
                    payload={
                        **deferral_summary,
                        "round_refs": [item.uri for item in round_refs],
                        **no_authority(_PRODUCTION_AUTHORITY),
                    },
                )
                proposal_ref = repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind=GEOMETRY_PROGRAM_PROPOSAL,
                    payload=_proposal_record(
                        reduced_proposal,
                        spatial_option_ref,
                        required_commitment_refs,
                        provider_identity,
                        (),
                        round_refs[-1],
                    ),
                )
                lineage_ref = _persist_lineage(
                    repository,
                    run,
                    destination,
                    GeometryProposalStatus.ACCEPTED,
                    spatial_option_ref,
                    spatial_option.proposal_digest,
                    design_state,
                    required_commitment_refs,
                    provider_identity,
                    tuple(round_refs),
                    proposal_ref,
                    reduced_proposal.proposal_digest,
                )
                return GeometryProposalProductionResult(
                    GeometryProposalStatus.ACCEPTED,
                    lineage_ref,
                    tuple(round_refs),
                    proposal_ref,
                    reduced_proposal,
                    reduced_compilation.program,
                    tuple(completion_refs),
                    deferral_ref,
                    None,
                )
    # P097 knob 5: a stall hands over the exact issue list with the last
    # proposal kept, instead of a bare exhaustion.
    escalation_ref: ProjectRecordRef | None = None
    if policy.escalate_on_stall and round_refs:
        escalation_ref = repository.put_json(
            run=run,
            destination=destination,
            record_kind=GEOMETRY_PROPOSAL_ESCALATION,
            payload={
                "schema": "GeometryProposalEscalation@1",
                "spatial_option_ref": spatial_option_ref.uri,
                "design_state_digest": design_state.state_digest,
                "round_refs": [item.uri for item in round_refs],
                "last_round_ref": round_refs[-1].uri,
                "last_proposal_digest": None if last_proposal is None else last_proposal.proposal_digest,
                "issues": [{"code": item.code, "detail": item.detail} for item in repair_issues],
                "policy": policy.to_dict(),
                **no_authority(_PRODUCTION_AUTHORITY),
            },
        )
    lineage_ref = _persist_lineage(
        repository,
        run,
        destination,
        GeometryProposalStatus.EXHAUSTED,
        spatial_option_ref,
        spatial_option.proposal_digest,
        design_state,
        required_commitment_refs,
        provider_identity,
        tuple(round_refs),
        None,
        None,
    )
    return GeometryProposalProductionResult(
        GeometryProposalStatus.EXHAUSTED,
        lineage_ref,
        tuple(round_refs),
        None,
        None,
        None,
        tuple(completion_refs),
        None,
        escalation_ref,
    )


async def resume_geometry_program_proposal(
    repository: GeometryProposalRepository,
    provider: object,
    *,
    escalation_ref: ProjectRecordRef,
    run: RunRef,
    **kwargs: Any,
) -> GeometryProposalProductionResult:
    """Continue an escalated run from its last rejected round (P097 knob 3).

    The accepted chain (spatial option, design state) is not re-authored;
    the resumed round carries the exact repair issues of the escalation's
    last round through ``rejected_round_ref``.
    """

    payload = repository.load_json(escalation_ref)
    if payload.get("schema") != "GeometryProposalEscalation@1":
        raise GeometryProposalProductionError(
            "escalation_ref does not name a GeometryProposalEscalation@1"
        )
    uri = payload["last_round_ref"]
    relative = uri.split(f"project://{run.project_id}/", 1)[1]
    sha = relative.rsplit("-", 1)[1].split(".json")[0]
    rejected = ProjectRecordRef(
        project_id=run.project_id,
        relative_path=relative,
        sha256=sha,
        media_type="application/json",
    )
    return await produce_geometry_program_proposal(
        repository, provider, run=run, rejected_round_ref=rejected, **kwargs
    )


def load_geometry_proposal_lineage(
    repository: GeometryProposalRepository,
    lineage_ref: ProjectRecordRef,
) -> LoadedGeometryProposalLineage:
    lineage = GeometryProposalLineage.from_dict(repository.load_json(lineage_ref))
    rounds = tuple(
        GeometryProposalRoundReceipt.from_dict(repository.load_json(ref))
        for ref in lineage.round_refs
    )
    if tuple(item.round_index for item in rounds) != tuple(range(1, len(rounds) + 1)):
        raise GeometryProposalProductionError("proposal round lineage is not contiguous")
    if any(
        item.design_state_digest != lineage.design_state_digest
        or item.spatial_option_ref != lineage.spatial_option_ref
        for item in rounds
    ):
        raise GeometryProposalProductionError("proposal round lineage identity drifted")
    proposal = None
    if lineage.accepted_proposal_ref is not None:
        proposal_payload = repository.load_json(lineage.accepted_proposal_ref)
        proposal = _proposal_from_record(proposal_payload)
        if proposal.proposal_digest != lineage.accepted_proposal_digest:
            raise GeometryProposalProductionError("accepted proposal digest drifted")
        if proposal.design_state_digest != lineage.design_state_digest:
            raise GeometryProposalProductionError("accepted proposal base drifted")
    return LoadedGeometryProposalLineage(lineage, rounds, proposal)


def _validate_inputs(
    run: RunRef,
    destination: PersistenceDestination,
    spatial_option_ref: ProjectRecordRef,
    design_state: DevelopedDesignState,
    commitment_refs: tuple[str, ...],
    provider_identity: GeometryProposalProviderIdentity,
    policy: GeometryProposalPolicy,
    template_refs: tuple[ProjectRecordRef, ...],
    required_geometry_component_ids: tuple[str, ...],
) -> None:
    if not isinstance(run, RunRef) or not isinstance(
        design_state,
        DevelopedDesignState,
    ):
        raise TypeError("run and design_state must be typed values")
    if destination.area is not PersistenceArea.RUN_RECORD or destination.run_id != run.run_id:
        raise GeometryProposalProductionError(
            "geometry proposals require the assigned run-record destination"
        )
    if (
        design_state.project_id != run.project_id
        or design_state.run_id != run.run_id
        or design_state.base != run.base
    ):
        raise GeometryProposalProductionError(
            "design state and run are not exact-base peers"
        )
    if spatial_option_ref.project_id != run.project_id:
        raise GeometryProposalProductionError("spatial option crosses project boundary")
    _strings(commitment_refs, "required_commitment_refs")
    if not isinstance(provider_identity, GeometryProposalProviderIdentity):
        raise TypeError("provider_identity is invalid")
    if not isinstance(policy, GeometryProposalPolicy):
        raise TypeError("policy is invalid")
    if not isinstance(template_refs, tuple) or any(
        not isinstance(item, ProjectRecordRef) or item.project_id != run.project_id
        for item in template_refs
    ):
        raise GeometryProposalProductionError("template refs cross project boundary")
    _strings(
        required_geometry_component_ids,
        "required_geometry_component_ids",
        allow_empty=True,
    )
    component_ids = {
        item.component_id
        for item in design_state.selected_schematic.option.proposal.components
    }
    unavailable_components = sorted(
        set(required_geometry_component_ids) - component_ids
    )
    if unavailable_components:
        raise GeometryProposalProductionError(
            "required geometry components are absent from the selected design "
            f"state; unavailable={unavailable_components}"
        )
    uris = tuple(item.uri for item in template_refs)
    if uris != tuple(sorted(set(uris))):
        raise GeometryProposalProductionError("template refs must be deterministic")


def _request_payload(
    spatial_ref: ProjectRecordRef,
    spatial: SpatialOptionProposal,
    design_state: DevelopedDesignState,
    commitments: tuple[str, ...],
    templates: tuple[dict[str, object], ...],
    repair_issues: tuple[GeometryProposalIssue, ...],
    repair_context: Mapping[str, object] | None,
    available_interface_refs: tuple[str, ...],
    expected_predecessor_program_digest: str | None,
    required_hosted_component_bindings: tuple[dict[str, object], ...],
    required_geometry_component_ids: tuple[str, ...],
    realization_contract: Mapping[str, object],
    prior_program: CompiledGeometryProgram | None,
) -> dict[str, object]:
    predecessor_revision_contract = _predecessor_revision_contract(
        prior_program
    )
    edit_mode = prior_program is not None
    required_output_schema = (
        _EDIT_AUTHORING_OUTPUT_SCHEMA if edit_mode else _AUTHORING_OUTPUT_SCHEMA
    )
    return {
        "schema": "GeometryProposalAuthoringRequest@1",
        "spatial_option_record": {
            "ref": spatial_ref.to_dict(),
            "proposal_digest": spatial.proposal_digest,
            "proposal_path": (
                "developed_design_state.selected_schematic.option.proposal"
            ),
        },
        "developed_design_state": design_state.to_dict(),
        "available_predecessor_program_digest": (
            expected_predecessor_program_digest
        ),
        "available_predecessor_program": (
            None if prior_program is None else prior_program.to_dict()
        ),
        "required_hosted_component_bindings": [
            dict(item) for item in required_hosted_component_bindings
        ],
        "required_geometry_component_ids": list(
            required_geometry_component_ids
        ),
        "predecessor_revision_contract": predecessor_revision_contract,
        "realization_contract": dict(realization_contract),
        "required_commitment_refs": list(commitments),
        "available_template_records": list(templates),
        "available_interface_refs": {
            "refs": list(available_interface_refs),
            "pattern": PORTABLE_LOGICAL_REF_PATTERN,
            "description": (
                "Exact interface references already present in the supplied "
                "spatial-option connection records. Hosted assemblies may cite "
                "only these values."
            ),
        },
        "geometry_function_contracts": _FUNCTION_CONTRACTS,
        "geometry_coordinate_convention": _GEOMETRY_COORDINATE_CONVENTION,
        "repair_issues": [item.to_dict() for item in repair_issues],
        "repair_context": (
            None
            if repair_context is None
            else json.loads(canonical_json(repair_context))
        ),
        "required_output_schema": required_output_schema,
        "required_output_contract": _authoring_output_contract(
            available_interface_refs,
            expected_predecessor_program_digest=(
                expected_predecessor_program_digest
            ),
            required_hosted_component_bindings=(
                required_hosted_component_bindings
            ),
            required_geometry_component_ids=(
                required_geometry_component_ids
            ),
            predecessor_revision_contract=(
                predecessor_revision_contract
            ),
            realization_contract=realization_contract,
        ),
        "instructions": [
            "Author geometry only from the supplied design state and project records.",
            *(
                [
                    "repair_context.rejected_output is the exact previous model output, not authoritative state. Return a complete replacement output over the original predecessor, preserve every intended typed item not implicated by repair_context.issues, and fix every exact issue.",
                    "Do not emit a patch. The framework will not merge rejected and repaired model outputs, and repair_context grants no output, validation, acceptance, or canonical-write authority.",
                ]
                if repair_context is not None
                else []
            ),
            "The selected spatial proposal is supplied exactly once at spatial_option_record.proposal_path; spatial_option_record.proposal_digest binds that embedded value to the immutable P036 record without replaying a duplicate proposal.",
            *(
                [
                    "Return one GeometryProgramEditAuthoringOutput@1 over the exact available_predecessor_program. Omit every unchanged predecessor frame, asset, semantic binding, operation, and assembly: omitted identities are retained byte-for-byte by deterministic merge.",
                    "Put every new or changed item in its matching *_upserts array as a complete typed value. Remove an existing identity only through the matching remove_*_ids array. The framework will not infer an edit or author geometry.",
                    "Copy available_predecessor_program_digest exactly to edit_body.predecessor_program_digest. Supply exact revision preconditions for changed retained objects, exact retirements for removed objects, and dependency responses required by the compiler.",
                ]
                if edit_mode
                else [
                    "No predecessor is available; return one complete initial GeometryProgramProposalBody@1."
                ]
            ),
            "Return exactly the keys and nested field shapes in required_output_contract.json_schema; do not invent aliases such as geometry_nodes or geometry_functions.",
            "Use only declared geometry function kinds and explicit parameters.",
            "For every operation parameter, copy kind from geometry_function_contracts[kind].parameters[*].kind; semantic choices such as polyline, bezier, or fixed belong in canonical value_json and are never parameter kind values.",
            "Use geometry_coordinate_convention exactly: Y is vertical up, XZ is the horizontal footprint plane, every vector is [x,y,z], solid origin[1] is elevation, and solid size[1] is height. Never reinterpret Z as vertical.",
            "GeometryParameter.value_json is canonical compact JSON encoded as a string, not a nested JSON value.",
            "Bind every realized geometry object to exactly one supplied semantic component_id; never infer identity from labels or screenshots.",
            "Every component_id in required_geometry_component_ids changed in the exact semantic predecessor transition and must retain a dedicated semantic binding with at least one realized object; omitting it cannot satisfy repair or lifecycle compilation.",
            "When predecessor_revision_contract.predecessor_program_digest is non-null, copy expected_digest only from its exact object_revision_tokens when revising a retained object, and acknowledge every changed retained semantic binding through the producing operation responds_to_binding_ids; never guess a predecessor digest.",
            "Include the current spatial option record URI in every new or changed semantic binding evidence_refs. An exact unchanged binding copied from available_predecessor_program may retain its predecessor evidence because predecessor_program_digest supplies the immutable lineage proof.",
            "Every assembly interface_refs value must be selected exactly from available_interface_refs.refs and match available_interface_refs.pattern.",
            "When a supplied semantic component requires a hosted assembly, represent its semantic identity and geometry together through semantic_binding_ids and typed assembly members.",
            "For every hosted assembly include all roles named by required_output_contract.required_assembly_roles[kind]; missing or duplicate roles are invalid.",
            *(
                invariant["instruction"]
                for invariant in _relational_authoring_invariants()
            ),
            *realization_contract["instructions"],
            "Treat identifier and reference arrays as sets: never duplicate values; lexical order is canonicalized by the protocol and carries no design meaning.",
            "Every responds_to_object_ids value must also appear in the same operation input_object_ids, and every responds_to_binding_ids value must appear in semantic_binding_ids. A zero-input function therefore has empty responds_to_object_ids.",
            "Do not claim hard-gate, acceptance, canonical-write, or platform authority.",
            "No framework fallback or unstated building dimension will be supplied.",
        ],
    }


def _rejected_output_repair_context(
    rejected_output: Mapping[str, object] | None,
    rejected_proposal_digest: str | None,
    issues: tuple[GeometryProposalIssue, ...],
    *,
    rejected_round_ref: ProjectRecordRef | None,
) -> dict[str, object]:
    if rejected_output is None:
        raise GeometryProposalProductionError(
            "rejected repair context requires exact model output"
        )
    if not issues:
        raise GeometryProposalProductionError(
            "rejected repair context requires exact typed issues"
        )
    return {
        "schema": "GeometryProposalRepairContext@1",
        "rejected_round_ref": (
            None if rejected_round_ref is None else rejected_round_ref.uri
        ),
        "rejected_output": json.loads(canonical_json(rejected_output)),
        "rejected_output_digest": canonical_digest(rejected_output),
        "rejected_proposal_digest": rejected_proposal_digest,
        "issues": [item.to_dict() for item in issues],
        "instructions": (
            "Return one complete replacement authoring output over the "
            "original supplied predecessor. Preserve intended typed items "
            "from rejected_output that are not implicated by issues and "
            "fix every exact issue. The framework will neither merge nor "
            "patch model outputs across rounds."
        ),
        "output_patch_authority": False,
        "validation_authority": False,
    }


def _validate_rejected_round_resume(
    rejected_round_ref: ProjectRecordRef,
    rejected_round: GeometryProposalRoundReceipt,
    *,
    spatial_option_ref: ProjectRecordRef,
    design_state: DevelopedDesignState,
    required_commitment_refs: tuple[str, ...],
    provider_identity: GeometryProposalProviderIdentity,
    expected_predecessor_program_digest: str | None,
    required_geometry_component_ids: tuple[str, ...],
    realization_contract: Mapping[str, object],
) -> None:
    expected_prefix = (
        f"runs/{design_state.run_id}/records/"
    )
    if (
        rejected_round_ref.project_id != design_state.project_id
        or not rejected_round_ref.relative_path.startswith(expected_prefix)
    ):
        raise GeometryProposalProductionError(
            "rejected round resume source is outside the exact project run"
        )
    if rejected_round.status is not GeometryProposalRoundStatus.REJECTED:
        raise GeometryProposalProductionError(
            "only a deterministically rejected round can resume repair"
        )
    if (
        rejected_round.spatial_option_ref != spatial_option_ref
        or rejected_round.design_state_digest != design_state.state_digest
    ):
        raise GeometryProposalProductionError(
            "rejected round is stale for the current spatial design state"
        )
    request = rejected_round.request
    receipt = rejected_round.model_receipt
    if (
        receipt.request != request
        or request.phase is not ModelPhase.ACTION_PROPOSAL
        or request.checkpoint_digest != design_state.state_digest
        or request.context_digest != canonical_digest(request.payload)
        or receipt.status is not ModelInvocationStatus.SUCCESS
        or not provider_identity.matches(receipt)
    ):
        raise GeometryProposalProductionError(
            "rejected round lacks exact successful provider provenance"
        )
    payload = request.payload
    spatial_record = payload.get("spatial_option_record")
    if not isinstance(spatial_record, Mapping):
        raise GeometryProposalProductionError(
            "rejected round spatial record is malformed"
        )
    exact_fields = {
        "schema": "GeometryProposalAuthoringRequest@1",
        "developed_design_state": design_state.to_dict(),
        "available_predecessor_program_digest": (
            expected_predecessor_program_digest
        ),
        "required_commitment_refs": list(required_commitment_refs),
        "required_geometry_component_ids": list(
            required_geometry_component_ids
        ),
        "realization_contract": dict(realization_contract),
    }
    if any(payload.get(key) != value for key, value in exact_fields.items()):
        raise GeometryProposalProductionError(
            "rejected round request contract is stale for this repair"
        )
    if spatial_record.get("ref") != spatial_option_ref.to_dict():
        raise GeometryProposalProductionError(
            "rejected round names another spatial option record"
        )


def _predecessor_revision_contract(
    prior_program: CompiledGeometryProgram | None,
) -> dict[str, object]:
    if prior_program is None:
        return {
            "schema": "GeometryPredecessorRevisionContract@1",
            "predecessor_program_digest": None,
            "object_revision_tokens": [],
            "semantic_binding_tokens": [],
            "derivation_only": True,
            "canonical_write_authority": False,
        }
    binding_digests = dict(prior_program.semantic_binding_digests)
    return {
        "schema": "GeometryPredecessorRevisionContract@1",
        "predecessor_program_digest": prior_program.program_digest,
        "object_revision_tokens": [
            {
                "schema": "GeometryObjectRevisionToken@1",
                "object_id": item.object_id,
                "expected_digest": item.object_digest,
                "producer_op_id": item.producer_op_id,
            }
            for item in prior_program.objects
        ],
        "semantic_binding_tokens": [
            {
                "schema": "GeometrySemanticBindingResponseToken@1",
                "binding_id": binding.binding_id,
                "component_id": binding.component_id,
                "object_ids": list(binding.object_ids),
                "expected_digest": binding_digests[binding.binding_id],
            }
            for binding in prior_program.proposal.semantic_bindings
        ],
        "derivation_only": True,
        "canonical_write_authority": False,
    }


def _compiler_repair_issue(
    issue: GeometryIssue,
    prior_program: CompiledGeometryProgram | None,
) -> GeometryProposalIssue:
    detail = f"{issue.subject_id}: {issue.detail}"
    if (
        prior_program is not None
        and issue.code is GeometryIssueCode.MISSING_REVISION_PRECONDITION
    ):
        prior_object = next(
            (
                item
                for item in prior_program.objects
                if item.object_id == issue.subject_id
            ),
            None,
        )
        if prior_object is not None:
            detail += (
                "; exact_revision_token="
                + canonical_json(
                    {
                        "object_id": prior_object.object_id,
                        "expected_digest": prior_object.object_digest,
                        "producer_op_id": prior_object.producer_op_id,
                    }
                )
            )
    return GeometryProposalIssue(
        f"compiler.{issue.code.value}",
        detail,
    )


def _authoring_output(
    value: object,
    prior_program: CompiledGeometryProgram | None,
) -> tuple[tuple[str, ...], Mapping[str, Any]]:
    payload = _mapping(value, "geometry authoring output")
    edit_mode = prior_program is not None
    body_key = "edit_body" if edit_mode else "proposal_body"
    expected_schema = (
        _EDIT_AUTHORING_OUTPUT_SCHEMA if edit_mode else _AUTHORING_OUTPUT_SCHEMA
    )
    _exact(
        payload,
        {"schema", "selected_template_refs", body_key},
        "geometry authoring output",
    )
    if payload["schema"] != expected_schema:
        raise GeometryProposalProductionError("geometry authoring output schema changed")
    selected = _strings(
        _strings_from_json(
            payload["selected_template_refs"],
            "selected_template_refs",
        ),
        "selected_template_refs",
        allow_empty=True,
    )
    return selected, _mapping(payload[body_key], body_key)


def _validate_semantic_coverage(
    proposal: GeometryProgramProposal,
    design_state: DevelopedDesignState,
    commitments: tuple[str, ...],
    spatial_ref: ProjectRecordRef,
    available_interface_refs: tuple[str, ...],
    required_hosted_component_bindings: tuple[dict[str, object], ...],
    required_geometry_component_ids: tuple[str, ...],
    prior_program: CompiledGeometryProgram | None,
    *,
    issues: list[GeometryProposalIssue],
    seat_scope: tuple[str, ...] | None = None,
) -> None:
    """Append every independent coverage failure for the bounded round."""

    def report(detail: str) -> None:
        issues.append(GeometryProposalIssue("malformed_model_output", detail))

    component_ids = {
        item.component_id
        for item in design_state.selected_schematic.option.proposal.components
    }
    bound_component_ids = tuple(
        binding.component_id for binding in proposal.semantic_bindings
    )
    if seat_scope is not None:
        out_of_scope = sorted(set(bound_component_ids) - set(seat_scope))
        if out_of_scope:
            issues.append(
                GeometryProposalIssue(
                    "seat_scope_violation",
                    "semantic bindings reach outside the seat's owned subtree; "
                    f"out_of_scope={out_of_scope}",
                )
            )
    extra = sorted(set(bound_component_ids) - component_ids)
    if extra:
        report(
            "semantic bindings name components absent from the selected "
            f"design state; extra={extra}"
        )
    if len(bound_component_ids) != len(set(bound_component_ids)):
        report(
            "each semantic component may own at most one geometry binding"
        )
    missing_required = sorted(
        set(required_geometry_component_ids) - set(bound_component_ids)
    )
    if missing_required:
        report(
            "semantic bindings omit components requiring geometry response; "
            f"missing={missing_required}"
        )
    bound_commitments = {
        ref for binding in proposal.semantic_bindings for ref in binding.commitment_refs
    }
    if not set(commitments) <= bound_commitments:
        report("semantic bindings omit required active commitments")
    prior_bindings = (
        {}
        if prior_program is None
        else {
            item.binding_id: item
            for item in prior_program.proposal.semantic_bindings
        }
    )
    missing_current_evidence = tuple(
        binding.binding_id
        for binding in proposal.semantic_bindings
        if spatial_ref.uri not in binding.evidence_refs
        and prior_bindings.get(binding.binding_id) != binding
    )
    if missing_current_evidence:
        report(
            "every new or changed semantic binding must cite the current source "
            f"spatial option record; missing={missing_current_evidence}"
        )
    used_interface_refs = {
        ref for assembly in proposal.assemblies for ref in assembly.interface_refs
    }
    unavailable = sorted(used_interface_refs - set(available_interface_refs))
    if unavailable:
        report(
            "assembly interface_refs are absent from the supplied spatial "
            f"option; unavailable={unavailable}"
        )
    for validator in (
        lambda: _validate_host_cut_apertures(proposal),
        lambda: _validate_hosted_component_bindings(
            proposal,
            required_hosted_component_bindings,
        ),
    ):
        try:
            validator()
        except (KeyError, TypeError, ValueError) as exc:
            report(str(exc))


def _validate_function_contracts(
    proposal: GeometryProgramProposal,
    *,
    issues: list[GeometryProposalIssue],
) -> None:
    """Report every independent typed-but-unsupported function parameter.

    ``parameters`` are checked against the contract for the operation's
    kind: a required one missing, an unknown one present, a wrong kind,
    unit or value. ``statements`` are not parameters and no kind names
    them, so they never appear in ``missing`` or ``extra``; the contract
    asks only that each one says something, since a blank statement would
    export an ``archflow:<key>`` string with nothing in it.
    """

    def report(detail: str) -> None:
        issues.append(GeometryProposalIssue("malformed_model_output", detail))

    for operation in proposal.operations:
        for name, statement in operation.statements.items():
            if not statement.strip():
                report(
                    f"{operation.op_id}.{name}: a statement must say "
                    "something; received empty text"
                )
        contract = _FUNCTION_CONTRACTS[operation.kind.value]
        raw_parameters = contract["parameters"]
        assert isinstance(raw_parameters, list)
        expected = {str(item["name"]): item for item in raw_parameters}
        actual = {item.name: item for item in operation.parameters}
        missing = sorted(
            name
            for name, item in expected.items()
            if bool(item["required"]) and name not in actual
        )
        extra = sorted(set(actual) - set(expected))
        if missing or extra:
            report(
                f"{operation.op_id}: {operation.kind.value} parameters do not "
                f"match the function contract; missing={missing}, extra={extra}"
            )
        for name, parameter in actual.items():
            if name not in expected:
                continue
            parameter_contract = expected[name]
            expected_kind = str(parameter_contract["kind"])
            if parameter.kind.value != expected_kind:
                report(
                    f"{operation.op_id}.{name}: parameter kind must be "
                    f"{expected_kind}; received={parameter.kind.value}"
                )
            expected_unit = parameter_contract["unit"]
            actual_unit = None if parameter.unit is None else parameter.unit.value
            if actual_unit != expected_unit:
                report(
                    f"{operation.op_id}.{name}: parameter unit must be "
                    f"{expected_unit!r}; received={actual_unit!r}"
                )
            allowed = parameter_contract["allowed_value_json"]
            assert isinstance(allowed, list)
            if allowed and parameter.value_json not in allowed:
                report(
                    f"{operation.op_id}.{name}: value_json must be one of "
                    f"{allowed}; received={parameter.value_json!r}"
                )


def _validate_hosted_component_bindings(
    proposal: GeometryProgramProposal,
    requirements: tuple[dict[str, object], ...],
) -> None:
    producer_by_object = {
        object_id: operation
        for operation in proposal.operations
        for object_id in operation.output_object_ids
    }
    claimed_member_objects: set[str] = set()
    for requirement in requirements:
        component_id = str(requirement["component_id"])
        assembly_kind = AssemblyKind(str(requirement["assembly_kind"]))
        dedicated = [
            binding
            for binding in proposal.semantic_bindings
            if binding.component_id == component_id
        ]
        if len(dedicated) != 1:
            raise GeometryProposalProductionError(
                f"hosted component {component_id} requires exactly one "
                "dedicated semantic binding with that component_id"
            )
        binding = dedicated[0]
        assemblies = [
            assembly
            for assembly in proposal.assemblies
            if assembly.kind is assembly_kind
            and assembly.semantic_binding_ids == (binding.binding_id,)
        ]
        if len(assemblies) != 1:
            raise GeometryProposalProductionError(
                f"hosted component {component_id} requires exactly one "
                f"{assembly_kind.value} assembly bound only to {binding.binding_id}"
            )
        assembly = assemblies[0]
        member_objects = {
            object_id
            for member in assembly.members
            for object_id in member.object_ids
        }
        reused = sorted(claimed_member_objects & member_objects)
        if reused:
            raise GeometryProposalProductionError(
                "hosted component assemblies reuse member object identities; "
                f"reused={reused}"
            )
        claimed_member_objects.update(member_objects)
        assembly_objects = {assembly.host_object_id, *member_objects}
        uncovered = sorted(assembly_objects - set(binding.object_ids))
        if uncovered:
            raise GeometryProposalProductionError(
                f"hosted component {component_id} assembly objects escape its "
                f"dedicated binding; uncovered={uncovered}"
            )
        wrong_producers = sorted(
            object_id
            for object_id in assembly_objects
            if object_id not in producer_by_object
            or binding.binding_id
            not in producer_by_object[object_id].semantic_binding_ids
        )
        if wrong_producers:
            raise GeometryProposalProductionError(
                f"hosted component {component_id} assembly objects are not "
                "produced under its dedicated binding; "
                f"objects={wrong_producers}"
            )


def _validate_host_cut_apertures(
    proposal: GeometryProgramProposal,
) -> None:
    producer_by_object = {
        object_id: operation
        for operation in proposal.operations
        for object_id in operation.output_object_ids
    }
    for assembly in proposal.assemblies:
        invalid = sorted(
            object_id
            for object_id in assembly.objects_for(AssemblyRole.HOST_CUT)
            if object_id not in producer_by_object
            or producer_by_object[object_id].kind
            is not GeometryOperationKind.BOOLEAN_INTERSECTION
        )
        if invalid:
            raise GeometryProposalProductionError(
                f"assembly {assembly.assembly_id} host_cut must be a "
                "boolean_intersection aperture volume, not a residual boolean "
                f"result; objects={invalid}"
            )


def _available_interface_refs(
    spatial: SpatialOptionProposal,
) -> tuple[str, ...]:
    """Return only interface facts already present in supplied records."""

    return tuple(
        sorted(
            {
                *(
                    ref
                    for connection in spatial.connections
                    for ref in connection.relationship_refs
                ),
            }
        )
    )


def _proposal_from_body(
    value: Mapping[str, Any],
    design_state: DevelopedDesignState,
    prior_program: CompiledGeometryProgram | None,
    *,
    errors: list[Exception] | None = None,
) -> GeometryProgramProposal | None:
    if prior_program is not None:
        return _proposal_from_edit(
            value,
            design_state,
            prior_program,
            errors=errors,
        )
    _exact(
        value,
        {
            "schema", "proposal_id", "predecessor_program_digest", "length_unit",
            "tolerance", "frames", "assets", "semantic_bindings", "operations",
            "assemblies", "revisions", "retirements",
        },
        "geometry proposal body",
    )
    if value["schema"] != _PROPOSAL_BODY_SCHEMA:
        raise GeometryProposalProductionError("geometry proposal body schema changed")
    return _construct_proposal(
        value,
        design_state.project_id,
        design_state.run_id,
        design_state.base,
        design_state.state_digest,
        errors=errors,
    )


def _proposal_from_edit(
    value: Mapping[str, Any],
    design_state: DevelopedDesignState,
    prior_program: CompiledGeometryProgram,
    *,
    errors: list[Exception] | None = None,
) -> GeometryProgramProposal | None:
    """Expand one exact model-authored edit without inventing geometry."""

    _exact(
        value,
        {
            "schema",
            "proposal_id",
            "predecessor_program_digest",
            "frame_upserts",
            "asset_upserts",
            "semantic_binding_upserts",
            "operation_upserts",
            "assembly_upserts",
            "remove_frame_ids",
            "remove_asset_ids",
            "remove_semantic_binding_ids",
            "remove_operation_ids",
            "remove_assembly_ids",
            "revisions",
            "retirements",
        },
        "geometry program edit body",
    )
    if value["schema"] != _EDIT_BODY_SCHEMA:
        raise GeometryProposalProductionError(
            "geometry program edit body schema changed"
        )
    if value["predecessor_program_digest"] != prior_program.program_digest:
        raise GeometryProposalProductionError(
            "geometry program edit does not bind the exact predecessor"
        )

    failures: list[Exception] = []

    def attempt(decode: Any) -> Any:
        if errors is None:
            return decode()
        try:
            return decode()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            failures.append(exc)
            return None

    element_errors = None if errors is None else failures
    prior = prior_program.proposal
    frame_upserts = attempt(
        lambda: _decode_sorted_list(
            value["frame_upserts"],
            _frame,
            "frame_upserts",
            lambda item: item.frame_id,
            errors=element_errors,
        )
    )
    asset_upserts = attempt(
        lambda: _decode_sorted_list(
            value["asset_upserts"],
            _asset,
            "asset_upserts",
            lambda item: item.asset_id,
            errors=element_errors,
        )
    )
    binding_upserts = attempt(
        lambda: _decode_sorted_list(
            value["semantic_binding_upserts"],
            _binding,
            "semantic_binding_upserts",
            lambda item: item.binding_id,
            errors=element_errors,
        )
    )
    operation_upserts = attempt(
        lambda: _decode_sorted_list(
            value["operation_upserts"],
            _operation,
            "operation_upserts",
            lambda item: item.op_id,
            errors=element_errors,
        )
    )
    assembly_upserts = attempt(
        lambda: _decode_sorted_list(
            value["assembly_upserts"],
            _assembly,
            "assembly_upserts",
            lambda item: item.assembly_id,
            errors=element_errors,
        )
    )
    remove_frame_ids = attempt(
        lambda: _ordered_edit_ids(
            value["remove_frame_ids"],
            "remove_frame_ids",
        )
    )
    remove_asset_ids = attempt(
        lambda: _ordered_edit_ids(
            value["remove_asset_ids"],
            "remove_asset_ids",
        )
    )
    remove_binding_ids = attempt(
        lambda: _ordered_edit_ids(
            value["remove_semantic_binding_ids"],
            "remove_semantic_binding_ids",
        )
    )
    remove_operation_ids = attempt(
        lambda: _ordered_edit_ids(
            value["remove_operation_ids"],
            "remove_operation_ids",
        )
    )
    remove_assembly_ids = attempt(
        lambda: _ordered_edit_ids(
            value["remove_assembly_ids"],
            "remove_assembly_ids",
        )
    )
    revisions = attempt(
        lambda: _decode_sorted_list(
            value["revisions"],
            _revision,
            "revisions",
            lambda item: item.object_id,
            errors=element_errors,
        )
    )
    retirements = attempt(
        lambda: _decode_sorted_list(
            value["retirements"],
            _retirement,
            "retirements",
            lambda item: item.object_id,
            errors=element_errors,
        )
    )
    if failures:
        assert errors is not None
        errors.extend(failures)
        return None

    frames = attempt(
        lambda: _merge_edit_items(
            prior.frames,
            frame_upserts,
            remove_frame_ids,
            "frame_id",
            "frames",
        )
    )
    assets = attempt(
        lambda: _merge_edit_items(
            prior.assets,
            asset_upserts,
            remove_asset_ids,
            "asset_id",
            "assets",
        )
    )
    semantic_bindings = attempt(
        lambda: _merge_edit_items(
            prior.semantic_bindings,
            binding_upserts,
            remove_binding_ids,
            "binding_id",
            "semantic_bindings",
        )
    )
    operations = attempt(
        lambda: _merge_edit_items(
            prior.operations,
            operation_upserts,
            remove_operation_ids,
            "op_id",
            "operations",
        )
    )
    assemblies = attempt(
        lambda: _merge_edit_items(
            prior.assemblies,
            assembly_upserts,
            remove_assembly_ids,
            "assembly_id",
            "assemblies",
        )
    )
    if failures:
        assert errors is not None
        errors.extend(failures)
        return None

    try:
        return GeometryProgramProposal(
            proposal_id=value["proposal_id"],
            project_id=design_state.project_id,
            run_id=design_state.run_id,
            base=design_state.base,
            design_state_digest=design_state.state_digest,
            predecessor_program_digest=prior_program.program_digest,
            length_unit=prior.length_unit,
            tolerance=prior.tolerance,
            frames=frames,
            assets=assets,
            semantic_bindings=semantic_bindings,
            operations=operations,
            assemblies=assemblies,
            revisions=revisions,
            retirements=retirements,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if errors is None:
            raise
        errors.append(exc)
        return None


def _ordered_edit_ids(value: object, field: str) -> tuple[str, ...]:
    return _strings(
        _strings_from_json(value, field),
        field,
        allow_empty=True,
    )


def _merge_edit_items(
    prior_items: tuple[Any, ...],
    upserts: tuple[Any, ...],
    remove_ids: tuple[str, ...],
    identity_field: str,
    field: str,
) -> tuple[Any, ...]:
    current = {getattr(item, identity_field): item for item in prior_items}
    upsert_ids = {getattr(item, identity_field) for item in upserts}
    overlap = sorted(upsert_ids & set(remove_ids))
    if overlap:
        raise GeometryProposalProductionError(
            f"{field} edit both upserts and removes identities; overlap={overlap}"
        )
    unknown = sorted(set(remove_ids) - set(current))
    if unknown:
        raise GeometryProposalProductionError(
            f"{field} edit removes unknown predecessor identities; unknown={unknown}"
        )
    for identity in remove_ids:
        del current[identity]
    for item in upserts:
        current[getattr(item, identity_field)] = item
    return tuple(current[key] for key in sorted(current))


def _construct_proposal(
    value: Mapping[str, Any],
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    design_state_digest: str,
    *,
    errors: list[Exception] | None = None,
) -> GeometryProgramProposal | None:
    """Decode independent proposal fields while preserving raise-first loads."""

    failures: list[Exception] = []

    def attempt(decode: Any) -> Any:
        if errors is None:
            return decode()
        try:
            return decode()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            failures.append(exc)
            return None

    def decode_tolerance() -> GeometryTolerance:
        tolerance = _mapping(value["tolerance"], "geometry tolerance")
        _exact(
            tolerance,
            {"schema", "linear", "angular_radians"},
            "geometry tolerance",
        )
        if tolerance["schema"] != GeometryTolerance.SCHEMA:
            raise GeometryProposalProductionError(
                "geometry tolerance schema changed"
            )
        return GeometryTolerance(
            tolerance["linear"],
            tolerance["angular_radians"],
        )

    element_errors = None if errors is None else failures
    length_unit = attempt(lambda: LengthUnit(value["length_unit"]))
    tolerance = attempt(decode_tolerance)
    frames = attempt(
        lambda: _decode_sorted_list(
            value["frames"],
            _frame,
            "frames",
            lambda item: item.frame_id,
            errors=element_errors,
        )
    )
    assets = attempt(
        lambda: _decode_sorted_list(
            value["assets"],
            _asset,
            "assets",
            lambda item: item.asset_id,
            errors=element_errors,
        )
    )
    semantic_bindings = attempt(
        lambda: _decode_sorted_list(
            value["semantic_bindings"],
            _binding,
            "semantic_bindings",
            lambda item: item.binding_id,
            errors=element_errors,
        )
    )
    operations = attempt(
        lambda: _decode_sorted_list(
            value["operations"],
            _operation,
            "operations",
            lambda item: item.op_id,
            errors=element_errors,
        )
    )
    assemblies = attempt(
        lambda: _decode_sorted_list(
            value["assemblies"],
            _assembly,
            "assemblies",
            lambda item: item.assembly_id,
            errors=element_errors,
        )
    )
    revisions = attempt(
        lambda: _decode_sorted_list(
            value["revisions"],
            _revision,
            "revisions",
            lambda item: item.object_id,
            errors=element_errors,
        )
    )
    retirements = attempt(
        lambda: _decode_sorted_list(
            value["retirements"],
            _retirement,
            "retirements",
            lambda item: item.object_id,
            errors=element_errors,
        )
    )
    if failures:
        assert errors is not None
        errors.extend(failures)
        return None

    try:
        return GeometryProgramProposal(
            proposal_id=value["proposal_id"],
            project_id=project_id,
            run_id=run_id,
            base=base,
            design_state_digest=design_state_digest,
            predecessor_program_digest=value["predecessor_program_digest"],
            length_unit=length_unit,
            tolerance=tolerance,
            frames=frames,
            assets=assets,
            semantic_bindings=semantic_bindings,
            operations=operations,
            assemblies=assemblies,
            revisions=revisions,
            retirements=retirements,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if errors is None:
            raise
        errors.append(exc)
        return None


def _frame(value: object) -> CoordinateFrame:
    payload = _mapping(value, "coordinate frame")
    _exact(payload, {"schema", "frame_id", "parent_frame_id", "transform_from_parent", "source_refs"}, "coordinate frame")
    transform = _mapping(payload["transform_from_parent"], "affine transform")
    _exact(transform, {"schema", "matrix"}, "affine transform")
    if payload["schema"] != CoordinateFrame.SCHEMA or transform["schema"] != AffineTransform.SCHEMA:
        raise GeometryProposalProductionError("coordinate frame schema changed")
    return CoordinateFrame(
        frame_id=payload["frame_id"],
        parent_frame_id=payload["parent_frame_id"],
        transform_from_parent=AffineTransform(tuple(transform["matrix"])),
        source_refs=_strings_from_json(payload["source_refs"], "frame source_refs"),
    )


def _parameter(value: object) -> GeometryParameter:
    payload = _mapping(value, "geometry parameter")
    _exact(payload, {"schema", "name", "kind", "value_json", "unit"}, "geometry parameter")
    if payload["schema"] != GeometryParameter.SCHEMA:
        raise GeometryProposalProductionError("geometry parameter schema changed")
    value_json = payload["value_json"]
    if isinstance(value_json, str):
        try:
            decoded_value = json.loads(value_json)
        except json.JSONDecodeError:
            pass
        else:
            value_json = canonical_json(decoded_value)
    return GeometryParameter(
        name=payload["name"],
        kind=GeometryParameterKind(payload["kind"]),
        value_json=value_json,
        unit=None if payload["unit"] is None else LengthUnit(payload["unit"]),
    )


def _binding(value: object) -> SemanticBinding:
    payload = _mapping(value, "semantic binding")
    _exact(payload, {"schema", "binding_id", "component_id", "object_ids", "commitment_refs", "evidence_refs"}, "semantic binding")
    if payload["schema"] != SemanticBinding.SCHEMA:
        raise GeometryProposalProductionError("semantic binding schema changed")
    return SemanticBinding(
        binding_id=payload["binding_id"],
        component_id=payload["component_id"],
        object_ids=_strings_from_json(payload["object_ids"], "binding object_ids"),
        commitment_refs=_strings_from_json(payload["commitment_refs"], "commitment_refs"),
        evidence_refs=_strings_from_json(payload["evidence_refs"], "evidence_refs"),
    )


def _asset(value: object) -> AssetReference:
    payload = _mapping(value, "asset reference")
    _exact(payload, {"schema", "asset_id", "uri", "media_type", "sha256", "native_unit", "sockets", "provenance_refs"}, "asset reference")
    if payload["schema"] != AssetReference.SCHEMA:
        raise GeometryProposalProductionError("asset reference schema changed")
    return AssetReference(
        asset_id=payload["asset_id"], uri=payload["uri"], media_type=payload["media_type"],
        sha256=payload["sha256"], native_unit=LengthUnit(payload["native_unit"]),
        sockets=_strings_from_json(payload["sockets"], "asset sockets"),
        provenance_refs=_strings_from_json(payload["provenance_refs"], "asset provenance_refs"),
    )


def _statements(value: object) -> dict[str, str]:
    """One operation's declared statements: identifier keys, text values.

    Absent is empty — an operation that declares nothing writes no field,
    which is what keeps every program authored before statements existed
    byte-identical.
    """

    if value is None:
        return {}
    payload = _mapping(value, "operation statements")
    for key, item in payload.items():
        if not isinstance(item, str):
            raise GeometryProposalProductionError(
                f"operation statement {key!r} must be text"
            )
    return dict(payload)


def _operation(value: object) -> GeometryOperation:
    payload = _mapping(value, "geometry operation")
    _exact(payload, {"schema", "op_id", "kind", "output_object_ids", "input_object_ids", "frame_id", "parameters", "semantic_binding_ids", "asset_id", "asset_socket_id", "asset_scale", "responds_to_object_ids", "responds_to_frame_ids", "responds_to_binding_ids"}, "geometry operation", optional=frozenset({"statements"}))
    if payload["schema"] != GeometryOperation.SCHEMA:
        raise GeometryProposalProductionError("geometry operation schema changed")
    scale = payload["asset_scale"]
    return GeometryOperation(
        op_id=payload["op_id"], kind=GeometryOperationKind(payload["kind"]),
        output_object_ids=_strings_from_json(payload["output_object_ids"], "output_object_ids"),
        input_object_ids=_strings_from_json(payload["input_object_ids"], "input_object_ids"),
        frame_id=payload["frame_id"],
        parameters=_decode_sorted_list(
            payload["parameters"],
            _parameter,
            "parameters",
            lambda item: item.name,
        ),
        semantic_binding_ids=_strings_from_json(payload["semantic_binding_ids"], "semantic_binding_ids"),
        asset_id=payload["asset_id"], asset_socket_id=payload["asset_socket_id"],
        asset_scale=None if scale is None else tuple(scale),
        responds_to_object_ids=_strings_from_json(payload["responds_to_object_ids"], "responds_to_object_ids"),
        responds_to_frame_ids=_strings_from_json(payload["responds_to_frame_ids"], "responds_to_frame_ids"),
        responds_to_binding_ids=_strings_from_json(payload["responds_to_binding_ids"], "responds_to_binding_ids"),
        statements=_statements(payload.get("statements")),
    )


def _member(value: object) -> AssemblyMember:
    payload = _mapping(value, "assembly member")
    _exact(payload, {"schema", "role", "object_ids"}, "assembly member")
    if payload["schema"] != AssemblyMember.SCHEMA:
        raise GeometryProposalProductionError("assembly member schema changed")
    return AssemblyMember(AssemblyRole(payload["role"]), _strings_from_json(payload["object_ids"], "member object_ids"))


def _assembly(value: object) -> HostedAssembly:
    payload = _mapping(value, "hosted assembly")
    _exact(payload, {"schema", "assembly_id", "kind", "host_object_id", "host_socket_id", "members", "interface_refs", "semantic_binding_ids", "maturity"}, "hosted assembly")
    if payload["schema"] != HostedAssembly.SCHEMA:
        raise GeometryProposalProductionError("hosted assembly schema changed")
    return HostedAssembly(
        assembly_id=payload["assembly_id"], kind=AssemblyKind(payload["kind"]),
        host_object_id=payload["host_object_id"], host_socket_id=payload["host_socket_id"],
        members=_decode_sorted_list(
            payload["members"],
            _member,
            "members",
            lambda item: item.role.value,
        ),
        interface_refs=_strings_from_json(payload["interface_refs"], "interface_refs"),
        semantic_binding_ids=_strings_from_json(payload["semantic_binding_ids"], "semantic_binding_ids"),
        maturity=DetailMaturity(payload["maturity"]),
    )


def _revision(value: object) -> ObjectRevisionPrecondition:
    payload = _mapping(value, "object revision")
    _exact(payload, {"schema", "object_id", "expected_digest", "reason_refs"}, "object revision")
    if payload["schema"] != ObjectRevisionPrecondition.SCHEMA:
        raise GeometryProposalProductionError("object revision schema changed")
    return ObjectRevisionPrecondition(payload["object_id"], payload["expected_digest"], _strings_from_json(payload["reason_refs"], "revision reason_refs"))


def _retirement(value: object) -> ObjectRetirement:
    payload = _mapping(value, "object retirement")
    _exact(payload, {"schema", "object_id", "expected_digest", "reason_refs"}, "object retirement")
    if payload["schema"] != ObjectRetirement.SCHEMA:
        raise GeometryProposalProductionError("object retirement schema changed")
    return ObjectRetirement(payload["object_id"], payload["expected_digest"], _strings_from_json(payload["reason_refs"], "retirement reason_refs"))


def _proposal_body(proposal: GeometryProgramProposal) -> dict[str, object]:
    payload = proposal.to_dict()
    return {
        "schema": _PROPOSAL_BODY_SCHEMA,
        "proposal_id": payload["proposal_id"],
        "predecessor_program_digest": payload["predecessor_program_digest"],
        "length_unit": payload["length_unit"],
        "tolerance": payload["tolerance"],
        "frames": payload["frames"],
        "assets": payload["assets"],
        "semantic_bindings": payload["semantic_bindings"],
        "operations": payload["operations"],
        "assemblies": payload["assemblies"],
        "revisions": payload["revisions"],
        "retirements": payload["retirements"],
    }


def _proposal_record(
    proposal: GeometryProgramProposal,
    spatial_ref: ProjectRecordRef,
    commitments: tuple[str, ...],
    identity: GeometryProposalProviderIdentity,
    selected_templates: tuple[str, ...],
    round_ref: ProjectRecordRef,
) -> dict[str, object]:
    return {
        "schema": _PROPOSAL_RECORD_SCHEMA,
        "proposal": proposal.to_dict(),
        "source_spatial_option_ref": spatial_ref.to_dict(),
        "required_commitment_refs": list(commitments),
        "selected_template_refs": list(selected_templates),
        "provider_identity": identity.to_dict(),
        "accepted_round_ref": round_ref.to_dict(),
        "proposal_only": True,
        "hard_gate_authority": False,
        "canonical_write_authority": False,
    }


def _proposal_from_record(value: object) -> GeometryProgramProposal:
    payload = _mapping(value, "geometry proposal record")
    _exact(payload, {"schema", "proposal", "source_spatial_option_ref", "required_commitment_refs", "selected_template_refs", "provider_identity", "accepted_round_ref", "proposal_only", "hard_gate_authority", "canonical_write_authority"}, "geometry proposal record")
    if (
        payload["schema"] != _PROPOSAL_RECORD_SCHEMA
        or payload["proposal_only"] is not True
    ):
        raise GeometryProposalProductionError("geometry proposal record acquired forbidden authority")
    return _proposal_from_full(payload["proposal"])


def _proposal_from_full(value: object) -> GeometryProgramProposal:
    proposal = _mapping(value, "geometry proposal")
    _exact(proposal, {"schema", "proposal_id", "project_id", "run_id", "base", "design_state_digest", "predecessor_program_digest", "length_unit", "tolerance", "frames", "assets", "semantic_bindings", "operations", "assemblies", "revisions", "retirements", "generation_authority", "hard_gate_authority", "canonical_write_authority"}, "geometry proposal")
    if (
        proposal["schema"] != GeometryProgramProposal.SCHEMA
    ):
        raise GeometryProposalProductionError("geometry proposal acquired forbidden authority")
    body = dict(_proposal_body_from_full(proposal))
    return _construct_proposal(
        body,
        proposal["project_id"],
        proposal["run_id"],
        ProjectVersionRef.from_dict(proposal["base"], "project base"),
        proposal["design_state_digest"],
    )


_COMPILED_PROGRAM_BASE_KEYS = frozenset(
    {
        "schema", "proposal", "proposal_digest", "operation_order",
        "frame_digests", "component_digests", "semantic_binding_digests",
        "objects", "asset_substitutions", "execution_authority",
        "hard_gate_authority", "canonical_write_authority",
    }
)
_COMPILED_PROGRAM_SCHEMA_KEYS = {
    "CompiledGeometryProgram@2": _COMPILED_PROGRAM_BASE_KEYS,
    "CompiledGeometryProgram@3": _COMPILED_PROGRAM_BASE_KEYS
    | {"interface_datums", "datum_bindings"},
}
if set(_COMPILED_PROGRAM_SCHEMA_KEYS) != set(CompiledGeometryProgram.ACCEPTED_SCHEMAS):
    raise AssertionError(
        "compiled program loader key sets drifted from "
        "CompiledGeometryProgram.ACCEPTED_SCHEMAS"
    )


def load_compiled_geometry_program(value: object) -> CompiledGeometryProgram:
    """Reload one exact compiled program without granting execution authority.

    Accepts the current schema and the retained @2 generation, whose
    records predate interface datums (P090) and reload with empty datum
    fields.
    """

    payload = _mapping(value, "compiled geometry program")
    schema = payload.get("schema")
    if schema not in _COMPILED_PROGRAM_SCHEMA_KEYS:
        raise GeometryProposalProductionError(
            "compiled geometry program schema is unsupported"
        )
    _exact(
        payload,
        set(_COMPILED_PROGRAM_SCHEMA_KEYS[schema]),
        "compiled geometry program",
    )
    proposal = _proposal_from_full(payload["proposal"])
    if payload["proposal_digest"] != proposal.proposal_digest:
        raise GeometryProposalProductionError(
            "compiled geometry proposal digest changed"
        )
    interface_datums: tuple[InterfaceDatum, ...] = ()
    datum_bindings: tuple[DatumBinding, ...] = ()
    if schema == "CompiledGeometryProgram@3":
        interface_datums = _decode_list(
            payload["interface_datums"],
            InterfaceDatum.from_dict,
            "interface datums",
        )
        datum_bindings = _decode_list(
            payload["datum_bindings"],
            DatumBinding.from_dict,
            "datum bindings",
        )
    program = CompiledGeometryProgram(
        proposal=proposal,
        operation_order=_ordered_strings_from_json(
            payload["operation_order"], "operation_order"
        ),
        frame_digests=_compiled_digest_pairs(
            payload["frame_digests"], "frame_id", "frame_digests"
        ),
        component_digests=_compiled_digest_pairs(
            payload["component_digests"], "component_id", "component_digests"
        ),
        semantic_binding_digests=_compiled_digest_pairs(
            payload["semantic_binding_digests"],
            "binding_id",
            "semantic_binding_digests",
        ),
        objects=_decode_sorted_list(
            payload["objects"],
            _compiled_object,
            "compiled objects",
            lambda item: item.object_id,
        ),
        asset_substitutions=_decode_list(
            payload["asset_substitutions"],
            _asset_substitution_receipt,
            "asset substitutions",
        ),
        interface_datums=interface_datums,
        datum_bindings=datum_bindings,
    )
    rendered = program.to_dict()
    if schema == "CompiledGeometryProgram@2":
        rendered = {
            key: item
            for key, item in rendered.items()
            if key not in ("interface_datums", "datum_bindings")
        }
        rendered["schema"] = "CompiledGeometryProgram@2"
    if rendered != dict(payload):
        raise GeometryProposalProductionError(
            "compiled geometry program is not an exact canonical record"
        )
    return program


def _compiled_digest_pairs(
    value: object,
    identity_field: str,
    label: str,
) -> tuple[tuple[str, str], ...]:
    entries = _decode_list(value, lambda item: _mapping(item, label), label)
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        _exact(entry, {identity_field, "digest"}, label)
        pairs.append((entry[identity_field], entry["digest"]))
    return tuple(pairs)


def _ordered_strings_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if any(not isinstance(item, str) or not item for item in value):
        raise GeometryProposalProductionError(f"{field} contains invalid text")
    if len(value) != len(set(value)):
        raise GeometryProposalProductionError(f"{field} contains duplicate values")
    return tuple(value)


def _compiled_object(value: object) -> CompiledGeometryObject:
    payload = _mapping(value, "compiled geometry object")
    _exact(
        payload,
        {"schema", "object_id", "producer_op_id", "object_digest"},
        "compiled geometry object",
    )
    if payload["schema"] != CompiledGeometryObject.SCHEMA:
        raise GeometryProposalProductionError(
            "compiled geometry object schema changed"
        )
    return CompiledGeometryObject(
        object_id=payload["object_id"],
        producer_op_id=payload["producer_op_id"],
        object_digest=payload["object_digest"],
    )


def _asset_substitution_receipt(value: object) -> AssetSubstitutionReceipt:
    payload = _mapping(value, "asset substitution receipt")
    _exact(
        payload,
        {
            "schema", "requested_asset_id", "requested_sha256",
            "replacement_asset_id", "replacement_sha256", "loss_codes",
            "evidence_refs", "lossless", "canonical_write_authority",
        },
        "asset substitution receipt",
    )
    if (
        payload["schema"] != AssetSubstitutionReceipt.SCHEMA
        or payload["lossless"] is not False
    ):
        raise GeometryProposalProductionError(
            "asset substitution receipt authority changed"
        )
    return AssetSubstitutionReceipt(
        requested_asset_id=payload["requested_asset_id"],
        requested_sha256=payload["requested_sha256"],
        replacement_asset_id=payload["replacement_asset_id"],
        replacement_sha256=payload["replacement_sha256"],
        loss_codes=_strings_from_json(payload["loss_codes"], "loss_codes"),
        evidence_refs=_strings_from_json(
            payload["evidence_refs"], "substitution evidence_refs"
        ),
    )


def _proposal_body_from_full(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "schema": _PROPOSAL_BODY_SCHEMA,
        **{
            key: value[key]
            for key in (
                "proposal_id", "predecessor_program_digest", "length_unit",
                "tolerance", "frames", "assets", "semantic_bindings",
                "operations", "assemblies", "revisions", "retirements",
            )
        },
    }


def _persist_lineage(
    repository: GeometryProposalRepository,
    run: RunRef,
    destination: PersistenceDestination,
    status: GeometryProposalStatus,
    spatial_ref: ProjectRecordRef,
    spatial_digest: str,
    design_state: DevelopedDesignState,
    commitments: tuple[str, ...],
    identity: GeometryProposalProviderIdentity,
    round_refs: tuple[ProjectRecordRef, ...],
    proposal_ref: ProjectRecordRef | None,
    proposal_digest: str | None,
) -> ProjectRecordRef:
    lineage = GeometryProposalLineage(
        lineage_id=f"geometry-proposal-lineage-{design_state.state_digest[:20]}",
        status=status,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        spatial_option_ref=spatial_ref,
        spatial_option_digest=spatial_digest,
        design_state_digest=design_state.state_digest,
        required_commitment_refs=commitments,
        provider_identity=identity,
        round_refs=round_refs,
        accepted_proposal_ref=proposal_ref,
        accepted_proposal_digest=proposal_digest,
    )
    return repository.put_json(
        run=run,
        destination=destination,
        record_kind=GEOMETRY_PROPOSAL_LINEAGE,
        payload=lineage.to_dict(),
    )


def _exact(
    value: Mapping[str, Any],
    fields: set[str],
    label: str,
    *,
    optional: frozenset[str] = frozenset(),
) -> None:
    actual = set(value)
    if actual != fields | (actual & optional):
        missing = sorted(fields - actual)
        unexpected = sorted(actual - fields - optional)
        raise GeometryProposalProductionError(
            f"{label}: field mismatch; missing={missing}; "
            f"unexpected={unexpected}"
        )


def _strings(values: tuple[str, ...], field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise GeometryProposalProductionError(f"{field} must be a tuple")
    if any(not isinstance(item, str) or not item for item in values):
        raise GeometryProposalProductionError(f"{field} contains invalid text")
    if values != tuple(sorted(set(values))):
        raise GeometryProposalProductionError(f"{field} must be deterministic")
    return values


def _strings_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if any(not isinstance(item, str) or not item for item in value):
        raise GeometryProposalProductionError(
            f"{field} contains invalid text"
        )
    if len(value) != len(set(value)):
        raise GeometryProposalProductionError(
            f"{field} contains duplicate values"
        )
    return tuple(sorted(value))


def _decode_list(
    value: object,
    decoder: Any,
    field: str,
    *,
    errors: list[Exception] | None = None,
) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    decoded = []
    for index, item in enumerate(value):
        try:
            decoded.append(decoder(item))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            wrapped = GeometryProposalProductionError(f"{field}[{index}]: {exc}")
            if errors is None:
                raise wrapped from exc
            wrapped.__cause__ = exc
            errors.append(wrapped)
    return tuple(decoded)


def _decode_sorted_list(
    value: object,
    decoder: Any,
    field: str,
    key: Any,
    *,
    errors: list[Exception] | None = None,
) -> tuple[Any, ...]:
    return tuple(
        sorted(_decode_list(value, decoder, field, errors=errors), key=key)
    )


def _base_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {"project_id": base.project_id, "version": base.version, "state_sha256": base.require_digest()}


def proposal_authoring_output(
    proposal: GeometryProgramProposal,
    *,
    selected_template_refs: tuple[str, ...] = (),
) -> dict[str, object]:
    """Encode a typed proposal as the strict provider response contract."""

    _strings(selected_template_refs, "selected_template_refs", allow_empty=True)
    return {
        "schema": _AUTHORING_OUTPUT_SCHEMA,
        "selected_template_refs": list(selected_template_refs),
        "proposal_body": _proposal_body(proposal),
    }


def proposal_edit_authoring_output(
    prior_program: CompiledGeometryProgram,
    proposal: GeometryProgramProposal,
    *,
    selected_template_refs: tuple[str, ...] = (),
) -> dict[str, object]:
    """Encode only model-authored differences from one exact predecessor."""

    if not isinstance(prior_program, CompiledGeometryProgram):
        raise TypeError("prior_program must be CompiledGeometryProgram")
    if not isinstance(proposal, GeometryProgramProposal):
        raise TypeError("proposal must be GeometryProgramProposal")
    if proposal.predecessor_program_digest != prior_program.program_digest:
        raise GeometryProposalProductionError(
            "edit proposal does not bind the exact predecessor"
        )
    _strings(selected_template_refs, "selected_template_refs", allow_empty=True)
    prior = prior_program.proposal

    def differences(
        old_items: tuple[Any, ...],
        current_items: tuple[Any, ...],
        identity_field: str,
    ) -> tuple[list[dict[str, object]], list[str]]:
        old = {getattr(item, identity_field): item for item in old_items}
        current = {
            getattr(item, identity_field): item for item in current_items
        }
        upserts = [
            current[key].to_dict()
            for key in sorted(current)
            if old.get(key) != current[key]
        ]
        removals = sorted(set(old) - set(current))
        return upserts, removals

    frames, remove_frames = differences(prior.frames, proposal.frames, "frame_id")
    assets, remove_assets = differences(prior.assets, proposal.assets, "asset_id")
    bindings, remove_bindings = differences(
        prior.semantic_bindings,
        proposal.semantic_bindings,
        "binding_id",
    )
    operations, remove_operations = differences(
        prior.operations,
        proposal.operations,
        "op_id",
    )
    assemblies, remove_assemblies = differences(
        prior.assemblies,
        proposal.assemblies,
        "assembly_id",
    )
    return {
        "schema": _EDIT_AUTHORING_OUTPUT_SCHEMA,
        "selected_template_refs": list(selected_template_refs),
        "edit_body": {
            "schema": _EDIT_BODY_SCHEMA,
            "proposal_id": proposal.proposal_id,
            "predecessor_program_digest": prior_program.program_digest,
            "frame_upserts": frames,
            "asset_upserts": assets,
            "semantic_binding_upserts": bindings,
            "operation_upserts": operations,
            "assembly_upserts": assemblies,
            "remove_frame_ids": remove_frames,
            "remove_asset_ids": remove_assets,
            "remove_semantic_binding_ids": remove_bindings,
            "remove_operation_ids": remove_operations,
            "remove_assembly_ids": remove_assemblies,
            "revisions": [item.to_dict() for item in proposal.revisions],
            "retirements": [item.to_dict() for item in proposal.retirements],
        },
    }
