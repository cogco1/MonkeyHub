"""Parthenon Stage 4 principal-door assembly compiler and validator.

The Stage 3 program represented each principal doorway with two independent
parameter families: the masonry opening was 4.92 x 9.84 metres, while the
single door leaf was 4.20 x 7.00 metres.  The generic spatial validator proved
that the opening was clear of wall solids and that a door component existed;
it did not prove that the door occupied the opening.

This project-specific module makes the dependency explicit.  The masonry
opening, stone reveal/frame, and two closed leaves are all derived from one
host-local ``ParthenonDoorApertureContract@1``.  Visual regions remain
topology/morphology evidence only and never acquire metric authority.

The module has no filesystem or persistence authority.  It returns operation
deltas and deterministic validation receipts for a caller to retain through
the normal project ports.
"""

from __future__ import annotations

import copy
import hashlib
import math
from enum import StrEnum
from typing import Mapping, Sequence
from archflow.contracts.canonical import canonical_json


BRANCH_ID = "idealized-periclean-original"
TARGET_ROUGH_OPENING_WIDTH_M = 4.96
FRAME_JAMB_REVEAL_M = 0.025
FRAME_HEADER_REVEAL_M = 0.030
FRAME_THRESHOLD_REVEAL_M = 0.030
LEAF_PERIMETER_GAP_M = 0.010
LEAF_MEETING_GAP_M = 0.020
LEAF_THICKNESS_M = 0.120
LINEAR_TOLERANCE_M = 1.0e-6
VOLUME_TOLERANCE_M3 = 1.0e-9

_EXPECTED_ROLES = (
    "frame-header",
    "frame-jamb-left",
    "frame-jamb-right",
    "frame-threshold",
    "leaf-left",
    "leaf-right",
)
_FORBIDDEN_BRANCH_TOKENS = (
    "byzantine",
    "medieval",
    "modern-restoration",
    "nero",
    "readable-inscription",
    "roman-imperial",
)


class ParthenonDoorAssemblyError(ValueError):
    """The current IR cannot produce an evidence-bound door assembly."""


class DoorAssemblyResolution(StrEnum):
    """Explicit human-resolution boundary for unknown door reconstruction."""

    PARK_DOOR_LEAVES = "PARK_DOOR_LEAVES"
    AUTHORIZED_CLOSED_DOUBLE_LEAF = "AUTHORIZED_CLOSED_DOUBLE_LEAF"


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParthenonDoorAssemblyError(f"{field_name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ParthenonDoorAssemblyError(f"{field_name} must be finite")
    return result


def _refs(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or not values
        or any(not isinstance(item, str) or not item.strip() for item in values)
    ):
        raise ParthenonDoorAssemblyError(
            f"{field_name} must contain non-empty evidence references"
        )
    return tuple(sorted(set(values)))


def _resolution(value: DoorAssemblyResolution | str) -> DoorAssemblyResolution:
    try:
        return DoorAssemblyResolution(value)
    except (TypeError, ValueError) as exc:
        raise ParthenonDoorAssemblyError(
            "resolution must be PARK_DOOR_LEAVES or AUTHORIZED_CLOSED_DOUBLE_LEAF"
        ) from exc


def _authorization_ref(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not (
            value.startswith("human-authorization:")
            or value.startswith("decision:human-authorized-")
            or value.startswith("project://")
        )
    ):
        raise ParthenonDoorAssemblyError(
            "AUTHORIZED_CLOSED_DOUBLE_LEAF requires a non-empty typed "
            "human decision or persisted project record reference"
        )
    return value


def _operation_geometry_fingerprint(operation: Mapping[str, object]) -> str:
    payload = {
        "operation_id": operation.get("operation_id"),
        "component_id": operation.get("component_id"),
        "kind": operation.get("kind"),
        "parameters": operation.get("parameters"),
        "material_id": operation.get("material_id"),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _box_bounds(
    operation: Mapping[str, object],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if operation.get("kind") != "box":
        raise ParthenonDoorAssemblyError(
            f"operation {operation.get('operation_id')} must be a box"
        )
    parameters = operation.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ParthenonDoorAssemblyError(
            f"operation {operation.get('operation_id')} lacks parameters"
        )
    origin = parameters.get("origin")
    size = parameters.get("size")
    if (
        not isinstance(origin, Sequence)
        or isinstance(origin, (str, bytes))
        or len(origin) != 3
        or not isinstance(size, Sequence)
        or isinstance(size, (str, bytes))
        or len(size) != 3
    ):
        raise ParthenonDoorAssemblyError(
            f"operation {operation.get('operation_id')} lacks box vectors"
        )
    lower = tuple(_number(value, "box origin") for value in origin)
    extents = tuple(_number(value, "box size") for value in size)
    if any(value <= 0.0 for value in extents):
        raise ParthenonDoorAssemblyError(
            f"operation {operation.get('operation_id')} has non-positive size"
        )
    upper = tuple(lower[index] + extents[index] for index in range(3))
    return lower, upper


def _intersection_volume(
    first: tuple[tuple[float, float, float], tuple[float, float, float]],
    second: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> float:
    extents = tuple(
        max(
            0.0,
            min(first[1][axis], second[1][axis])
            - max(first[0][axis], second[0][axis]),
        )
        for axis in range(3)
    )
    return extents[0] * extents[1] * extents[2]


def _operations_by_id(
    operations: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ParthenonDoorAssemblyError("operations must be mappings")
        operation_id = operation.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise ParthenonDoorAssemblyError("each operation needs an operation_id")
        if operation_id in result:
            raise ParthenonDoorAssemblyError(
                f"duplicate operation id: {operation_id}"
            )
        result[operation_id] = operation
    return result


def _host_members(
    operations: Sequence[Mapping[str, object]],
    side: str,
) -> tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
    prefix = f"cella-wall-{side}"
    candidates = tuple(
        operation
        for operation in operations
        if operation.get("component_id") == "cella"
        and str(operation.get("operation_id", "")).startswith(prefix)
        and operation.get("kind") == "box"
    )
    lintels = tuple(
        operation
        for operation in candidates
        if operation.get("operation_id") == f"{prefix}-lintel"
    )
    if len(lintels) != 1:
        raise ParthenonDoorAssemblyError(
            f"{side} principal door needs one cella lintel host"
        )
    lintel = lintels[0]
    lintel_bottom = _box_bounds(lintel)[0][2]
    full_height = tuple(
        operation
        for operation in candidates
        if operation is not lintel
        and _box_bounds(operation)[1][2] >= lintel_bottom - LINEAR_TOLERANCE_M
    )
    negative = tuple(
        operation
        for operation in full_height
        if _box_bounds(operation)[1][0] <= LINEAR_TOLERANCE_M
    )
    positive = tuple(
        operation
        for operation in full_height
        if _box_bounds(operation)[0][0] >= -LINEAR_TOLERANCE_M
    )
    if not negative or not positive:
        raise ParthenonDoorAssemblyError(
            f"{side} principal door lacks two axial wall hosts"
        )
    left = max(negative, key=lambda item: _box_bounds(item)[1][0])
    right = min(positive, key=lambda item: _box_bounds(item)[0][0])
    return left, right, lintel


def _legacy_leaf(
    operations: Sequence[Mapping[str, object]], side: str
) -> Mapping[str, object]:
    matches = tuple(
        operation
        for operation in operations
        if operation.get("operation_id") == f"cella-door-{side}"
        and operation.get("component_id") == f"door-{side}"
    )
    if len(matches) != 1:
        raise ParthenonDoorAssemblyError(
            f"{side} correction requires exactly one inherited cella-door-{side}"
        )
    return matches[0]


def _measure_opening_side(
    operations: Sequence[Mapping[str, object]], side: str
) -> dict[str, object]:
    left, right, lintel = _host_members(operations, side)
    left_bounds = _box_bounds(left)
    right_bounds = _box_bounds(right)
    lintel_bounds = _box_bounds(lintel)
    opening_min_x = left_bounds[1][0]
    opening_max_x = right_bounds[0][0]
    base_z = min(left_bounds[0][2], right_bounds[0][2])
    opening_top_z = lintel_bounds[0][2]
    return {
        "side": side,
        "host_operation_ids": [
            str(left["operation_id"]),
            str(right["operation_id"]),
            str(lintel["operation_id"]),
        ],
        "host_geometry_fingerprints": {
            str(operation["operation_id"]): _operation_geometry_fingerprint(operation)
            for operation in (left, right, lintel)
        },
        "opening_min_x_m": opening_min_x,
        "opening_max_x_m": opening_max_x,
        "opening_width_m": opening_max_x - opening_min_x,
        "opening_height_m": opening_top_z - base_z,
        "wall_bounds_y_m": [left_bounds[0][1], left_bounds[1][1]],
        "base_z_m": base_z,
        "opening_top_z_m": opening_top_z,
    }


def _measure_side(
    operations: Sequence[Mapping[str, object]], side: str
) -> dict[str, object]:
    opening = _measure_opening_side(operations, side)
    leaf = _legacy_leaf(operations, side)
    leaf_bounds = _box_bounds(leaf)
    opening_min_x = float(opening["opening_min_x_m"])
    opening_max_x = float(opening["opening_max_x_m"])
    base_z = float(opening["base_z_m"])
    opening_top_z = float(opening["opening_top_z_m"])
    opening_width = float(opening["opening_width_m"])
    opening_height = float(opening["opening_height_m"])
    leaf_width = leaf_bounds[1][0] - leaf_bounds[0][0]
    leaf_height = leaf_bounds[1][2] - leaf_bounds[0][2]
    return {
        **opening,
        "opening_width_m": opening_width,
        "opening_height_m": opening_height,
        "leaf_width_m": leaf_width,
        "leaf_height_m": leaf_height,
        "left_gap_m": leaf_bounds[0][0] - opening_min_x,
        "right_gap_m": opening_max_x - leaf_bounds[1][0],
        "bottom_gap_m": leaf_bounds[0][2] - base_z,
        "top_gap_m": opening_top_z - leaf_bounds[1][2],
        "width_coverage_ratio": leaf_width / opening_width,
        "height_coverage_ratio": leaf_height / opening_height,
        "shared_aperture_contract": isinstance(
            leaf.get("parameters"), Mapping
        )
        and "aperture_contract" in leaf["parameters"],
    }


def measure_door_dependency_state(
    operations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Quantify the inherited independent-constant dependency defect."""

    metrics = tuple(_measure_side(operations, side) for side in ("east", "west"))
    return {
        "schema": "ParthenonDoorDependencyDiagnosis@1",
        "passed": all(
            bool(item["shared_aperture_contract"])
            and float(item["left_gap_m"]) <= LEAF_PERIMETER_GAP_M
            and float(item["right_gap_m"]) <= LEAF_PERIMETER_GAP_M
            and float(item["top_gap_m"]) <= LEAF_PERIMETER_GAP_M
            for item in metrics
        ),
        "metrics": list(metrics),
        "root_causes": [
            "masonry opening and door leaf were compiled from independent constants",
            "the prior spatial gate checked wall-clear opening volume and component presence, not leaf-to-opening coverage",
            "the inherited door operation had no typed host-local aperture contract",
        ],
    }


def _basis(
    textual_evidence_refs: tuple[str, ...],
    selected_visual_refs: tuple[str, ...],
    statement: str,
) -> dict[str, object]:
    return {
        "classification": "SOFT",
        "statement": statement,
        "source_refs": list(textual_evidence_refs),
        "visual_region_refs": list(selected_visual_refs),
        "visual_role": "topology_or_morphology_only_not_exact_dimension",
        "metric_authority": False,
    }


def _delta_operation(
    *,
    operation_id: str,
    component_id: str,
    parameters: Mapping[str, object],
    material_id: str,
    textual_evidence_refs: tuple[str, ...],
    selected_visual_refs: tuple[str, ...],
    statement: str,
    replaces_operation_id: str | None = None,
    refines_operation_id: str | None = None,
) -> dict[str, object]:
    if (replaces_operation_id is None) == (refines_operation_id is None):
        raise ParthenonDoorAssemblyError(
            "each door delta operation needs exactly one replaces/refines id"
        )
    result: dict[str, object] = {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": "box",
        "parameters": copy.deepcopy(dict(parameters)),
        "decision_refs": ["decision:cella-openings", "decision:room-connectivity"],
        "source_refs": list(textual_evidence_refs),
        "visual_region_refs": list(selected_visual_refs),
        "parameter_basis": _basis(
            textual_evidence_refs,
            selected_visual_refs,
            statement,
        ),
        "material_id": material_id,
    }
    if replaces_operation_id is not None:
        result["replaces_operation_id"] = replaces_operation_id
    else:
        result["refines_operation_id"] = refines_operation_id
    return result


def _replacement_host(
    operation: Mapping[str, object],
    *,
    origin: Sequence[float],
    size: Sequence[float],
    side: str,
    role: str,
    aperture_id: str,
    textual_evidence_refs: tuple[str, ...],
    selected_visual_refs: tuple[str, ...],
) -> dict[str, object]:
    operation_id = str(operation["operation_id"])
    parameters = copy.deepcopy(dict(operation["parameters"]))
    parameters["origin"] = list(origin)
    parameters["size"] = list(size)
    parameters["door_aperture_binding"] = {
        "schema": "ParthenonDoorHostBinding@1",
        "aperture_id": aperture_id,
        "side": side,
        "role": role,
        "door_component_id": f"door-{side}",
    }
    statement = (
        "host wall edge is derived from the shared principal-door aperture; "
        "the 4.96 m corrective width is SOFT within the approximately 4.94-5.00 m target"
    )
    result = _delta_operation(
        operation_id=operation_id,
        component_id="cella",
        parameters=parameters,
        material_id=str(operation.get("material_id", "pentelic-marble")),
        textual_evidence_refs=tuple(
            sorted(set(textual_evidence_refs) | set(operation.get("source_refs", ())))
        ),
        selected_visual_refs=tuple(
            sorted(
                set(selected_visual_refs)
                | set(operation.get("visual_region_refs", ()))
            )
        ),
        statement=statement,
        replaces_operation_id=operation_id,
    )
    # A principal-door edge and an east-window pier can be the same physical
    # wall member.  Replacing that member for the door must therefore carry
    # forward the already-validated window host/contact relation instead of
    # silently turning the window into an unrelated patch.  These fields are
    # relational dependencies, not decorative metadata.
    for key in (
        "host",
        "contact_contract",
        "material_role",
        "branch_id",
        "refinement_scope",
        "metric_authority",
        "coordinate_system",
        "up_axis",
    ):
        if key in operation:
            result[key] = copy.deepcopy(operation[key])
    return result


def _contract(
    *,
    side: str,
    host_ids: Sequence[str],
    wall_y: Sequence[float],
    base_z: float,
    top_z: float,
    inherited_opening_width: float,
    human_authorization_ref: str,
) -> dict[str, object]:
    half_width = TARGET_ROUGH_OPENING_WIDTH_M / 2.0
    clear_min_x = -half_width + FRAME_JAMB_REVEAL_M
    clear_max_x = half_width - FRAME_JAMB_REVEAL_M
    clear_min_z = base_z + FRAME_THRESHOLD_REVEAL_M
    clear_max_z = top_z - FRAME_HEADER_REVEAL_M
    return {
        "schema": "ParthenonDoorApertureContract@1",
        "aperture_id": f"principal-door-{side}",
        "branch_id": BRANCH_ID,
        "side": side,
        "host_component_id": "cella",
        "host_wall_member_ids": list(host_ids),
        "axis_x_m": 0.0,
        "wall_bounds_y_m": [float(wall_y[0]), float(wall_y[1])],
        "rough_opening": {
            "min_x_m": -half_width,
            "max_x_m": half_width,
            "min_z_m": base_z,
            "max_z_m": top_z,
            "width_m": TARGET_ROUGH_OPENING_WIDTH_M,
            "height_m": top_z - base_z,
        },
        "clear_opening": {
            "min_x_m": clear_min_x,
            "max_x_m": clear_max_x,
            "min_z_m": clear_min_z,
            "max_z_m": clear_max_z,
            "width_m": clear_max_x - clear_min_x,
            "height_m": clear_max_z - clear_min_z,
        },
        "frame_reveal_m": {
            "jamb": FRAME_JAMB_REVEAL_M,
            "header": FRAME_HEADER_REVEAL_M,
            "threshold": FRAME_THRESHOLD_REVEAL_M,
        },
        "leaf_tolerances_m": {
            "maximum_perimeter_gap": LEAF_PERIMETER_GAP_M,
            "maximum_meeting_gap": LEAF_MEETING_GAP_M,
        },
        "inherited_opening_width_m": inherited_opening_width,
        "opening_strategy": "closed-symmetric-double-leaf",
        "basis_classification": "SOFT",
        "metric_authority": False,
        "candidate_status": "AUTHORIZED_CANDIDATE_NOT_HISTORICAL_FACT",
        "human_authorization_ref": human_authorization_ref,
    }


def _compile_side(
    operations: Sequence[Mapping[str, object]],
    side: str,
    textual_evidence_refs: tuple[str, ...],
    selected_visual_refs: tuple[str, ...],
    human_authorization_ref: str,
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...], dict[str, object]]:
    left, right, lintel = _host_members(operations, side)
    legacy_leaf = _legacy_leaf(operations, side)
    left_bounds = _box_bounds(left)
    right_bounds = _box_bounds(right)
    lintel_bounds = _box_bounds(lintel)
    if any(
        not math.isclose(left_bounds[index][1], right_bounds[index][1], abs_tol=LINEAR_TOLERANCE_M)
        for index in (0, 1)
    ):
        raise ParthenonDoorAssemblyError(f"{side} wall hosts disagree on depth")
    inherited_width = right_bounds[0][0] - left_bounds[1][0]
    if not 4.88 <= inherited_width <= 5.02:
        raise ParthenonDoorAssemblyError(
            f"{side} inherited principal-door width escaped its evidence range"
        )
    base_z = min(left_bounds[0][2], right_bounds[0][2])
    top_z = lintel_bounds[0][2]
    if top_z - base_z <= 0.0:
        raise ParthenonDoorAssemblyError(f"{side} principal-door height is invalid")
    wall_y = (left_bounds[0][1], left_bounds[1][1])
    if not all(
        math.isclose(value, expected, abs_tol=LINEAR_TOLERANCE_M)
        for value, expected in zip(
            (right_bounds[0][1], right_bounds[1][1]), wall_y, strict=True
        )
    ):
        raise ParthenonDoorAssemblyError(f"{side} wall hosts are not coplanar")
    host_ids = (
        str(left["operation_id"]),
        str(right["operation_id"]),
        str(lintel["operation_id"]),
    )
    contract = _contract(
        side=side,
        host_ids=host_ids,
        wall_y=wall_y,
        base_z=base_z,
        top_z=top_z,
        inherited_opening_width=inherited_width,
        human_authorization_ref=human_authorization_ref,
    )
    aperture_id = str(contract["aperture_id"])
    rough = contract["rough_opening"]
    clear = contract["clear_opening"]
    assert isinstance(rough, Mapping) and isinstance(clear, Mapping)

    new_left_max_x = float(rough["min_x_m"])
    new_right_min_x = float(rough["max_x_m"])
    replacements = [
        _replacement_host(
            left,
            origin=left_bounds[0],
            size=(
                new_left_max_x - left_bounds[0][0],
                left_bounds[1][1] - left_bounds[0][1],
                left_bounds[1][2] - left_bounds[0][2],
            ),
            side=side,
            role="left-wall-edge",
            aperture_id=aperture_id,
            textual_evidence_refs=textual_evidence_refs,
            selected_visual_refs=selected_visual_refs,
        ),
        _replacement_host(
            right,
            origin=(new_right_min_x, right_bounds[0][1], right_bounds[0][2]),
            size=(
                right_bounds[1][0] - new_right_min_x,
                right_bounds[1][1] - right_bounds[0][1],
                right_bounds[1][2] - right_bounds[0][2],
            ),
            side=side,
            role="right-wall-edge",
            aperture_id=aperture_id,
            textual_evidence_refs=textual_evidence_refs,
            selected_visual_refs=selected_visual_refs,
        ),
        _replacement_host(
            lintel,
            origin=(new_left_max_x, lintel_bounds[0][1], lintel_bounds[0][2]),
            size=(
                TARGET_ROUGH_OPENING_WIDTH_M,
                lintel_bounds[1][1] - lintel_bounds[0][1],
                lintel_bounds[1][2] - lintel_bounds[0][2],
            ),
            side=side,
            role="lintel",
            aperture_id=aperture_id,
            textual_evidence_refs=textual_evidence_refs,
            selected_visual_refs=selected_visual_refs,
        ),
    ]

    wall_depth = wall_y[1] - wall_y[0]
    leaf_y = wall_y[0] + (wall_depth - LEAF_THICKNESS_M) / 2.0
    clear_min_x = float(clear["min_x_m"])
    clear_max_x = float(clear["max_x_m"])
    clear_min_z = float(clear["min_z_m"])
    clear_max_z = float(clear["max_z_m"])
    leaf_min_x = clear_min_x + LEAF_PERIMETER_GAP_M
    leaf_max_x = clear_max_x - LEAF_PERIMETER_GAP_M
    half_meeting = LEAF_MEETING_GAP_M / 2.0
    leaf_min_z = clear_min_z + LEAF_PERIMETER_GAP_M
    leaf_max_z = clear_max_z - LEAF_PERIMETER_GAP_M
    assembly_statement = (
        "door reveal and closed double-leaf topology are SOFT; all dimensions "
        "derive from the typed host-local aperture and explicit construction gaps"
    )

    def assembly(
        role: str,
        origin: Sequence[float],
        size: Sequence[float],
        material_id: str,
        material_role: str,
        *,
        replace: bool = False,
    ) -> dict[str, object]:
        return _delta_operation(
            operation_id=f"principal-door-{side}-{role}",
            component_id=f"door-{side}",
            parameters={
                "origin": list(origin),
                "size": list(size),
                "assembly_role": role,
                "material_role": material_role,
                "host_operation_ids": list(host_ids),
                "aperture_contract": copy.deepcopy(contract),
            },
            material_id=material_id,
            textual_evidence_refs=textual_evidence_refs,
            selected_visual_refs=selected_visual_refs,
            statement=assembly_statement,
            replaces_operation_id=(
                str(legacy_leaf["operation_id"]) if replace else None
            ),
            refines_operation_id=(
                None if replace else str(legacy_leaf["operation_id"])
            ),
        )

    replacements.extend(
        (
            assembly(
                "frame-jamb-left",
                (float(rough["min_x_m"]), wall_y[0], clear_min_z),
                (FRAME_JAMB_REVEAL_M, wall_depth, clear_max_z - clear_min_z),
                "pentelic-marble-structural",
                "stone-door-reveal",
            ),
            assembly(
                "frame-jamb-right",
                (clear_max_x, wall_y[0], clear_min_z),
                (FRAME_JAMB_REVEAL_M, wall_depth, clear_max_z - clear_min_z),
                "pentelic-marble-structural",
                "stone-door-reveal",
            ),
            assembly(
                "frame-header",
                (float(rough["min_x_m"]), wall_y[0], clear_max_z),
                (TARGET_ROUGH_OPENING_WIDTH_M, wall_depth, FRAME_HEADER_REVEAL_M),
                "pentelic-marble-structural",
                "stone-door-reveal",
            ),
            assembly(
                "frame-threshold",
                (float(rough["min_x_m"]), wall_y[0], base_z),
                (
                    TARGET_ROUGH_OPENING_WIDTH_M,
                    wall_depth,
                    FRAME_THRESHOLD_REVEAL_M,
                ),
                "pentelic-marble-structural",
                "stone-door-threshold",
            ),
            assembly(
                "leaf-left",
                (leaf_min_x, leaf_y, leaf_min_z),
                (
                    -half_meeting - leaf_min_x,
                    LEAF_THICKNESS_M,
                    leaf_max_z - leaf_min_z,
                ),
                "door-timber-candidate",
                "door-leaf-timber-candidate",
                replace=True,
            ),
            assembly(
                "leaf-right",
                (half_meeting, leaf_y, leaf_min_z),
                (
                    leaf_max_x - half_meeting,
                    LEAF_THICKNESS_M,
                    leaf_max_z - leaf_min_z,
                ),
                "door-timber-candidate",
                "door-leaf-timber-candidate",
            ),
        )
    )
    return (
        tuple(replacements),
        tuple((*host_ids, str(legacy_leaf["operation_id"]))),
        contract,
    )


def compile_door_assembly_delta(
    current_operations: Sequence[Mapping[str, object]],
    textual_evidence_refs: Sequence[str],
    selected_visual_refs: Sequence[str],
    *,
    resolution: DoorAssemblyResolution | str = DoorAssemblyResolution.PARK_DOOR_LEAVES,
    human_authorization_ref: str | None = None,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Resolve unknown principal-door geometry without inventing authority.

    ``PARK_DOOR_LEAVES`` is the default.  It removes the inherited undersized
    leaf proxies, preserves the exact 4.92 metre stone openings, emits no door
    frame/leaf geometry, and returns a blocking parked receipt.

    ``AUTHORIZED_CLOSED_DOUBLE_LEAF`` compiles the earlier SOFT candidate only
    when a typed human-decision reference (or its persisted ``project://``
    record URI) is supplied.  Authorization permits candidate generation; it
    does not promote visual evidence to metric authority or turn the
    reconstruction into a historical fact.
    """

    current_by_id = _operations_by_id(current_operations)
    sources = _refs(textual_evidence_refs, "textual_evidence_refs")
    visuals = _refs(selected_visual_refs, "selected_visual_refs")
    diagnosis = measure_door_dependency_state(current_operations)
    chosen = _resolution(resolution)
    if chosen is DoorAssemblyResolution.PARK_DOOR_LEAVES:
        if human_authorization_ref is not None:
            raise ParthenonDoorAssemblyError(
                "PARK_DOOR_LEAVES must not carry a human authorization reference"
            )
        opening_snapshots = [
            _measure_opening_side(current_operations, side)
            for side in ("east", "west")
        ]
        for snapshot in opening_snapshots:
            if (
                not math.isclose(
                    float(snapshot["opening_width_m"]),
                    4.92,
                    abs_tol=LINEAR_TOLERANCE_M,
                )
                or not math.isclose(
                    float(snapshot["opening_height_m"]),
                    9.84,
                    abs_tol=LINEAR_TOLERANCE_M,
                )
            ):
                raise ParthenonDoorAssemblyError(
                    "PARK_DOOR_LEAVES may only preserve the exact inherited "
                    "4.92 x 9.84 metre stone openings"
                )
        parked_lineage = []
        door_assembly_ancestry = []
        for side in ("east", "west"):
            current_door_id = f"cella-door-{side}"
            current = current_by_id.get(current_door_id)
            if current is None:
                raise ParthenonDoorAssemblyError(
                    f"PARK_DOOR_LEAVES cannot resolve absent {current_door_id}"
                )
            if current.get("replaces_operation_id"):
                root_id = str(current["replaces_operation_id"])
                relation = "REPLACES"
            elif current.get("refines_operation_id"):
                root_id = str(current["refines_operation_id"])
                relation = "REFINES"
            else:
                root_id = current_door_id
                relation = "IDENTICAL_PRESERVED"
            parked_lineage.append(
                {
                    "exact_stage3_root_operation_id": root_id,
                    "current_operation_id": current_door_id,
                    "final_operation_id": None,
                    "current_to_stage3_relation": relation,
                    "final_to_current_relation": "PARKED_WITH_REASON",
                    "root_to_final_path": (
                        [root_id]
                        if root_id == current_door_id
                        else [root_id, current_door_id]
                    ),
                    "disposition": "PARKED_WITH_REASON",
                    "blocking": True,
                    "parked_reason": (
                        "the inherited 4.20 x 7.00 metre leaf is not derived "
                        "from the 4.92 x 9.84 metre stone aperture, while the "
                        "ancient frame, leaf count, leaf extents, and articulation "
                        "remain unauthorized reconstruction choices"
                    ),
                }
            )
            door_assembly_ancestry.append(
                {
                    "current_door_operation_id": current_door_id,
                    "exact_stage3_door_root_operation_id": root_id,
                    "final_assembly_operation_ids": [],
                    "root_to_final_paths": [],
                    "disposition": "PARKED_WITH_REASON",
                    "blocking": True,
                }
            )
        receipt = {
            "schema": "ParthenonDoorAssemblyDelta@1",
            "branch_id": BRANCH_ID,
            "resolution": chosen.value,
            "resolution_status": "PARKED_BLOCKING_HUMAN_DECISION",
            "human_authorization_ref": None,
            "blocking": True,
            "authorization_gate_satisfied": False,
            "stage_gate_satisfied": False,
            "delta_operation_count": 0,
            "delta_operation_ids": [],
            "superseded_operation_ids": [
                "cella-door-east",
                "cella-door-west",
            ],
            "contracts": [],
            "preserved_stone_openings": opening_snapshots,
            "exact_stage3_door_root_operation_ids": [
                "cella-door-east",
                "cella-door-west",
            ],
            "door_assembly_ancestry": door_assembly_ancestry,
            "transitive_lineage": parked_lineage,
            "predecessor_diagnosis": diagnosis,
            "parked_reason": (
                "No historically authorized frame/leaf assembly is available. "
                "The invalid undersized proxy leaves are removed; the exact "
                "inherited stone apertures and solid internal partition remain."
            ),
            "human_resolution_required": (
                "A human must authorize a named door reconstruction strategy "
                "before any frame or leaf geometry may enter the candidate."
            ),
            "parameter_basis": {
                "classification": "PARKED",
                "metric_authority": False,
                "textual_evidence_refs": list(sources),
                "selected_visual_refs": list(visuals),
                "statement": (
                    "visual regions identify a principal doorway but do not "
                    "authorize ancient frame or leaf geometry"
                ),
            },
        }
        return (), receipt

    authorization = _authorization_ref(human_authorization_ref)
    delta: list[dict[str, object]] = []
    superseded: list[str] = []
    contracts: list[dict[str, object]] = []
    for side in ("east", "west"):
        side_delta, side_superseded, contract = _compile_side(
            current_operations,
            side,
            sources,
            visuals,
            authorization,
        )
        delta.extend(side_delta)
        superseded.extend(side_superseded)
        contracts.append(contract)
    delta_tuple = tuple(sorted(delta, key=lambda item: str(item["operation_id"])))
    ids = tuple(str(item["operation_id"]) for item in delta_tuple)
    if len(ids) != len(set(ids)):
        raise ParthenonDoorAssemblyError("door delta operation ids are not unique")
    transitive_lineage: list[dict[str, object]] = []
    for successor in delta_tuple:
        if successor.get("replaces_operation_id"):
            current_id = str(successor["replaces_operation_id"])
            successor_relation = "REPLACES"
        else:
            current_id = str(successor["refines_operation_id"])
            successor_relation = "REFINES"
        current = current_by_id.get(current_id)
        if current is None:
            raise ParthenonDoorAssemblyError(
                f"door successor {successor['operation_id']} has no current predecessor"
            )
        if current.get("replaces_operation_id"):
            stage3_root_id = str(current["replaces_operation_id"])
            current_relation = "REPLACES"
        elif current.get("refines_operation_id"):
            stage3_root_id = str(current["refines_operation_id"])
            current_relation = "REFINES"
        else:
            stage3_root_id = current_id
            current_relation = "IDENTICAL_PRESERVED"
        path = [stage3_root_id]
        if current_id != stage3_root_id:
            path.append(current_id)
        path.append(str(successor["operation_id"]))
        transitive_lineage.append(
            {
                "exact_stage3_root_operation_id": stage3_root_id,
                "current_operation_id": current_id,
                "final_operation_id": str(successor["operation_id"]),
                "current_to_stage3_relation": current_relation,
                "final_to_current_relation": successor_relation,
                "root_to_final_path": path,
            }
        )
    door_assembly_ancestry = []
    for side in ("east", "west"):
        current_door_id = f"cella-door-{side}"
        final_ids = sorted(
            str(operation["operation_id"])
            for operation in delta_tuple
            if operation.get("component_id") == f"door-{side}"
        )
        door_assembly_ancestry.append(
            {
                "current_door_operation_id": current_door_id,
                "exact_stage3_door_root_operation_id": current_door_id,
                "final_assembly_operation_ids": final_ids,
                "root_to_final_paths": [
                    item["root_to_final_path"]
                    for item in transitive_lineage
                    if item["current_operation_id"] == current_door_id
                ],
            }
        )
    receipt = {
        "schema": "ParthenonDoorAssemblyDelta@1",
        "branch_id": BRANCH_ID,
        "resolution": chosen.value,
        "resolution_status": "AUTHORIZED_SOFT_CANDIDATE",
        "human_authorization_ref": authorization,
        "blocking": False,
        "authorization_gate_satisfied": True,
        "stage_gate_satisfied": False,
        "delta_operation_count": len(delta_tuple),
        "delta_operation_ids": list(ids),
        "superseded_operation_ids": sorted(set(superseded)),
        "contracts": contracts,
        "exact_stage3_door_root_operation_ids": [
            "cella-door-east",
            "cella-door-west",
        ],
        "door_assembly_ancestry": door_assembly_ancestry,
        "transitive_lineage": transitive_lineage,
        "predecessor_diagnosis": diagnosis,
        "parameter_basis": {
            "classification": "SOFT",
            "metric_authority": False,
            "textual_evidence_refs": list(sources),
            "selected_visual_refs": list(visuals),
            "statement": (
                "human authorization permits this candidate strategy; frame, "
                "double-leaf, and construction gaps remain explicit SOFT choices, "
                "not established ancient dimensions"
            ),
        },
    }
    return delta_tuple, receipt


def apply_door_assembly_delta(
    current_operations: Sequence[Mapping[str, object]],
    delta_operations: Sequence[Mapping[str, object]],
    delta_receipt: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """Mechanically apply a compiled delta without choosing persistence paths."""

    if delta_receipt.get("schema") != "ParthenonDoorAssemblyDelta@1":
        raise ParthenonDoorAssemblyError("door delta receipt schema drifted")
    current_by_id = _operations_by_id(current_operations)
    delta_by_id = _operations_by_id(delta_operations)
    listed_delta = tuple(str(item) for item in delta_receipt.get("delta_operation_ids", ()))
    if set(listed_delta) != set(delta_by_id):
        raise ParthenonDoorAssemblyError("door delta ids disagree with its receipt")
    superseded = tuple(
        str(item) for item in delta_receipt.get("superseded_operation_ids", ())
    )
    missing = sorted(set(superseded) - set(current_by_id))
    if missing:
        raise ParthenonDoorAssemblyError(
            f"door delta superseded ids are absent: {missing}"
        )
    result = [
        copy.deepcopy(dict(operation))
        for operation_id, operation in current_by_id.items()
        if operation_id not in set(superseded)
    ]
    result.extend(copy.deepcopy(dict(operation)) for operation in delta_operations)
    result_tuple = tuple(sorted(result, key=lambda item: str(item["operation_id"])))
    _operations_by_id(result_tuple)
    return result_tuple


def _parameter_contract(operation: Mapping[str, object]) -> Mapping[str, object]:
    parameters = operation.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ParthenonDoorAssemblyError(
            f"operation {operation.get('operation_id')} lacks parameters"
        )
    contract = parameters.get("aperture_contract")
    if not isinstance(contract, Mapping):
        raise ParthenonDoorAssemblyError(
            f"operation {operation.get('operation_id')} lacks an aperture contract"
        )
    return contract


def _close(first: float, second: float) -> bool:
    return math.isclose(first, second, abs_tol=LINEAR_TOLERANCE_M)


def _validate_parked_resolution(
    full_operations: Sequence[Mapping[str, object]],
    resolution_receipt: Mapping[str, object] | None,
) -> dict[str, object]:
    policy_failures: list[str] = []
    checks = {
        "unique_operation_ids": True,
        "no_door_leaf_or_frame_geometry": True,
        "inherited_stone_openings_preserved": True,
        "solid_internal_partition_preserved": True,
        "window_role_separation": True,
        "blocking_park_receipt": True,
        "stage3_door_roots_parked": True,
        "no_human_authorization_claim": True,
    }
    try:
        _operations_by_id(full_operations)
    except ParthenonDoorAssemblyError as exc:
        checks["unique_operation_ids"] = False
        policy_failures.append(str(exc))
    door_geometry = tuple(
        operation
        for operation in full_operations
        if operation.get("component_id") in {"door-east", "door-west"}
        or str(operation.get("operation_id", "")).startswith("principal-door-")
        or str(operation.get("operation_id", "")).startswith("cella-door-")
    )
    if door_geometry:
        checks["no_door_leaf_or_frame_geometry"] = False
        policy_failures.append(
            "PARK_DOOR_LEAVES contains door leaf/frame geometry: "
            + ", ".join(str(item.get("operation_id")) for item in door_geometry)
        )

    opening_metrics: dict[str, object] = {}
    for side in ("east", "west"):
        try:
            snapshot = _measure_opening_side(full_operations, side)
        except ParthenonDoorAssemblyError as exc:
            checks["inherited_stone_openings_preserved"] = False
            policy_failures.append(str(exc))
            continue
        opening_metrics[side] = snapshot
        if (
            not _close(float(snapshot["opening_width_m"]), 4.92)
            or not _close(float(snapshot["opening_height_m"]), 9.84)
        ):
            checks["inherited_stone_openings_preserved"] = False
            policy_failures.append(
                f"{side} PARK mode changed the inherited 4.92 x 9.84 metre stone opening"
            )

    partitions = tuple(
        operation
        for operation in full_operations
        if operation.get("operation_id") == "cella-partition"
        and operation.get("component_id") == "cella"
        and operation.get("kind") == "box"
    )
    partition_fragments = tuple(
        operation
        for operation in full_operations
        if str(operation.get("operation_id", "")).startswith("cella-partition-")
    )
    if len(partitions) != 1 or partition_fragments:
        checks["solid_internal_partition_preserved"] = False
        policy_failures.append("PARK mode altered or opened the internal partition")

    for operation in full_operations:
        parameters = operation.get("parameters")
        if isinstance(parameters, Mapping) and (
            "window_clear" in parameters or "window_side" in parameters
        ) and operation.get("component_id") != "cella":
            checks["window_role_separation"] = False
            policy_failures.append(
                f"window host {operation.get('operation_id')} escaped the cella component"
            )

    receipt = resolution_receipt
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema") != "ParthenonDoorAssemblyDelta@1"
        or receipt.get("resolution")
        != DoorAssemblyResolution.PARK_DOOR_LEAVES.value
        or receipt.get("resolution_status")
        != "PARKED_BLOCKING_HUMAN_DECISION"
        or receipt.get("blocking") is not True
        or receipt.get("authorization_gate_satisfied") is not False
        or receipt.get("stage_gate_satisfied") is not False
        or receipt.get("delta_operation_count") != 0
        or receipt.get("delta_operation_ids") != []
        or not receipt.get("parked_reason")
        or not receipt.get("human_resolution_required")
    ):
        checks["blocking_park_receipt"] = False
        policy_failures.append("PARK mode lacks its explicit blocking/parked receipt")
    else:
        if receipt.get("human_authorization_ref") is not None:
            checks["no_human_authorization_claim"] = False
            policy_failures.append("PARK mode falsely claims human authorization")
        if set(receipt.get("superseded_operation_ids", ())) != {
            "cella-door-east",
            "cella-door-west",
        }:
            checks["blocking_park_receipt"] = False
            policy_failures.append("PARK receipt did not remove exactly the invalid leaves")
        expected_roots = {"cella-door-east", "cella-door-west"}
        lineage = receipt.get("transitive_lineage")
        if (
            not isinstance(lineage, Sequence)
            or isinstance(lineage, (str, bytes))
            or {
                str(item.get("exact_stage3_root_operation_id"))
                for item in lineage
                if isinstance(item, Mapping)
            }
            != expected_roots
            or any(
                not isinstance(item, Mapping)
                or item.get("disposition") != "PARKED_WITH_REASON"
                or item.get("blocking") is not True
                or not item.get("parked_reason")
                or item.get("final_operation_id") is not None
                for item in lineage
            )
        ):
            checks["stage3_door_roots_parked"] = False
            policy_failures.append(
                "PARK receipt lacks blocking PARKED_WITH_REASON lineage to both Stage 3 door roots"
            )
        retained = receipt.get("preserved_stone_openings")
        if not isinstance(retained, Sequence) or isinstance(retained, (str, bytes)):
            checks["inherited_stone_openings_preserved"] = False
            policy_failures.append("PARK receipt lacks stone-opening snapshots")
        else:
            retained_by_side = {
                str(item.get("side")): item
                for item in retained
                if isinstance(item, Mapping)
            }
            for side, observed in opening_metrics.items():
                expected = retained_by_side.get(side)
                if not isinstance(expected, Mapping) or (
                    expected.get("host_geometry_fingerprints")
                    != observed.get("host_geometry_fingerprints")
                ):
                    checks["inherited_stone_openings_preserved"] = False
                    policy_failures.append(
                        f"{side} stone-opening host fingerprint changed after PARK"
                    )

    policy_compliant = not policy_failures and all(checks.values())
    blocking_reason = (
        "principal door leaf/frame geometry is PARKED pending an explicit typed "
        "human authorization and therefore cannot satisfy the Stage 4 door gate"
    )
    return {
        "schema": "ParthenonDoorAssemblyValidation@1",
        "resolution": DoorAssemblyResolution.PARK_DOOR_LEAVES.value,
        "passed": False,
        "policy_compliant": policy_compliant,
        "blocking": True,
        "stage_gate_satisfied": False,
        "checks": checks,
        "metrics": opening_metrics,
        "failures": policy_failures,
        "blocking_reasons": [blocking_reason],
        "branch_id": BRANCH_ID,
        "measurement_authority": {
            "stone_opening": "inherited",
            "frame_and_leaf_detail": "PARKED",
            "visual_regions": "topology_or_morphology_only_not_exact_dimension",
            "metric_authority": False,
        },
    }


def validate_door_assembly_operations(
    full_operations: Sequence[Mapping[str, object]],
    *,
    resolution: DoorAssemblyResolution | str = DoorAssemblyResolution.PARK_DOOR_LEAVES,
    resolution_receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Fail closed on door host, coverage, collision, evidence, and branch drift."""

    chosen = _resolution(resolution)
    if chosen is DoorAssemblyResolution.PARK_DOOR_LEAVES:
        return _validate_parked_resolution(full_operations, resolution_receipt)

    failures: list[str] = []
    metrics: dict[str, object] = {}
    checks = {
        "unique_operation_ids": True,
        "east_west_assemblies": True,
        "shared_host_local_contract": True,
        "host_chain": True,
        "coaxial_and_mirrored": True,
        "clear_opening_coverage": True,
        "frame_leaf_noncollision": True,
        "wall_noncollision": True,
        "no_internal_door": True,
        "window_role_separation": True,
        "material_roles": True,
        "evidence_and_lineage": True,
        "branch_scope": True,
        "human_authorization": True,
    }
    authorized_ref: str | None = None
    if (
        not isinstance(resolution_receipt, Mapping)
        or resolution_receipt.get("schema") != "ParthenonDoorAssemblyDelta@1"
        or resolution_receipt.get("resolution")
        != DoorAssemblyResolution.AUTHORIZED_CLOSED_DOUBLE_LEAF.value
        or resolution_receipt.get("resolution_status")
        != "AUTHORIZED_SOFT_CANDIDATE"
        or resolution_receipt.get("blocking") is not False
        or resolution_receipt.get("authorization_gate_satisfied") is not True
        or resolution_receipt.get("stage_gate_satisfied") is not False
    ):
        checks["human_authorization"] = False
        failures.append(
            "closed-double-leaf candidate lacks its typed authorization receipt"
        )
    else:
        try:
            authorized_ref = _authorization_ref(
                resolution_receipt.get("human_authorization_ref")
            )
        except ParthenonDoorAssemblyError as exc:
            checks["human_authorization"] = False
            failures.append(str(exc))
    try:
        by_id = _operations_by_id(full_operations)
    except (ParthenonDoorAssemblyError, TypeError) as exc:
        checks["unique_operation_ids"] = False
        return {
            "schema": "ParthenonDoorAssemblyValidation@1",
            "passed": False,
            "checks": checks,
            "metrics": metrics,
            "failures": [str(exc)],
        }

    unexpected_components = sorted(
        {
            str(operation.get("component_id"))
            for operation in full_operations
            if str(operation.get("component_id", "")).startswith("door-")
            and operation.get("component_id") not in {"door-east", "door-west"}
        }
    )
    if unexpected_components:
        checks["no_internal_door"] = False
        failures.append(f"unexpected/internal door components: {unexpected_components}")
    partitions = tuple(
        operation
        for operation in full_operations
        if operation.get("operation_id") == "cella-partition"
        and operation.get("component_id") == "cella"
        and operation.get("kind") == "box"
    )
    partition_fragments = tuple(
        operation
        for operation in full_operations
        if str(operation.get("operation_id", "")).startswith("cella-partition-")
    )
    if len(partitions) != 1 or partition_fragments:
        checks["no_internal_door"] = False
        failures.append("the internal cella partition is absent, fragmented, or opened")

    side_contracts: dict[str, Mapping[str, object]] = {}
    side_operations: dict[str, tuple[Mapping[str, object], ...]] = {}
    for side in ("east", "west"):
        component_id = f"door-{side}"
        assembly = tuple(
            operation
            for operation in full_operations
            if operation.get("component_id") == component_id
        )
        side_operations[side] = assembly
        roles: dict[str, Mapping[str, object]] = {}
        for operation in assembly:
            parameters = operation.get("parameters")
            role = parameters.get("assembly_role") if isinstance(parameters, Mapping) else None
            if not isinstance(role, str) or role in roles:
                checks["east_west_assemblies"] = False
                failures.append(f"{side} door has a missing or duplicate assembly role")
                continue
            roles[role] = operation
        if set(roles) != set(_EXPECTED_ROLES):
            checks["east_west_assemblies"] = False
            failures.append(
                f"{side} door roles are {sorted(roles)}, expected {list(_EXPECTED_ROLES)}"
            )
            continue
        try:
            contracts = tuple(_parameter_contract(operation) for operation in assembly)
        except ParthenonDoorAssemblyError as exc:
            checks["shared_host_local_contract"] = False
            failures.append(str(exc))
            continue
        canonical_contracts = {canonical_json(contract) for contract in contracts}
        if len(canonical_contracts) != 1:
            checks["shared_host_local_contract"] = False
            failures.append(f"{side} door members do not share one aperture contract")
            continue
        contract = contracts[0]
        side_contracts[side] = contract
        if (
            contract.get("schema") != "ParthenonDoorApertureContract@1"
            or contract.get("branch_id") != BRANCH_ID
            or contract.get("side") != side
            or contract.get("host_component_id") != "cella"
            or contract.get("metric_authority") is not False
            or contract.get("basis_classification") != "SOFT"
            or contract.get("candidate_status")
            != "AUTHORIZED_CANDIDATE_NOT_HISTORICAL_FACT"
            or contract.get("human_authorization_ref") != authorized_ref
        ):
            checks["shared_host_local_contract"] = False
            failures.append(f"{side} aperture contract identity or authority drifted")
            continue
        rough = contract.get("rough_opening")
        clear = contract.get("clear_opening")
        host_ids = contract.get("host_wall_member_ids")
        if (
            not isinstance(rough, Mapping)
            or not isinstance(clear, Mapping)
            or not isinstance(host_ids, Sequence)
            or isinstance(host_ids, (str, bytes))
            or len(host_ids) != 3
        ):
            checks["host_chain"] = False
            failures.append(f"{side} aperture contract lacks typed host/opening fields")
            continue
        host_members = tuple(by_id.get(str(operation_id)) for operation_id in host_ids)
        if any(
            operation is None or operation.get("component_id") != "cella"
            for operation in host_members
        ):
            checks["host_chain"] = False
            failures.append(f"{side} aperture host chain does not resolve to cella walls")
            continue
        bindings = []
        for host in host_members:
            assert host is not None
            parameters = host.get("parameters")
            binding = parameters.get("door_aperture_binding") if isinstance(parameters, Mapping) else None
            bindings.append(binding)
        if any(
            not isinstance(binding, Mapping)
            or binding.get("aperture_id") != contract.get("aperture_id")
            or binding.get("side") != side
            for binding in bindings
        ):
            checks["host_chain"] = False
            failures.append(f"{side} host members lack the matching door binding")

        try:
            rough_min_x = _number(rough["min_x_m"], "rough min x")
            rough_max_x = _number(rough["max_x_m"], "rough max x")
            rough_min_z = _number(rough["min_z_m"], "rough min z")
            rough_max_z = _number(rough["max_z_m"], "rough max z")
            clear_min_x = _number(clear["min_x_m"], "clear min x")
            clear_max_x = _number(clear["max_x_m"], "clear max x")
            clear_min_z = _number(clear["min_z_m"], "clear min z")
            clear_max_z = _number(clear["max_z_m"], "clear max z")
            rough_width = _number(rough["width_m"], "rough width")
            axis_x = _number(contract["axis_x_m"], "axis x")
        except (KeyError, ParthenonDoorAssemblyError) as exc:
            checks["shared_host_local_contract"] = False
            failures.append(f"{side} aperture numeric fields are invalid: {exc}")
            continue
        if (
            not 4.94 <= rough_width <= 5.00
            or not _close(rough_width, rough_max_x - rough_min_x)
            or not _close(axis_x, 0.0)
            or not _close(rough_min_x, -rough_max_x)
        ):
            checks["coaxial_and_mirrored"] = False
            failures.append(f"{side} principal opening width/axis drifted")

        left_host, right_host, lintel_host = host_members
        assert left_host is not None and right_host is not None and lintel_host is not None
        try:
            left_bounds = _box_bounds(left_host)
            right_bounds = _box_bounds(right_host)
            lintel_bounds = _box_bounds(lintel_host)
        except ParthenonDoorAssemblyError as exc:
            checks["host_chain"] = False
            failures.append(str(exc))
            continue
        if not (
            _close(left_bounds[1][0], rough_min_x)
            and _close(right_bounds[0][0], rough_max_x)
            and _close(lintel_bounds[0][0], rough_min_x)
            and _close(lintel_bounds[1][0], rough_max_x)
            and _close(lintel_bounds[0][2], rough_max_z)
        ):
            checks["host_chain"] = False
            failures.append(f"{side} masonry hosts do not derive from rough opening")

        leaf_left = _box_bounds(roles["leaf-left"])
        leaf_right = _box_bounds(roles["leaf-right"])
        if leaf_left[0][0] > leaf_right[0][0]:
            leaf_left, leaf_right = leaf_right, leaf_left
        perimeter_gaps = {
            "left": leaf_left[0][0] - clear_min_x,
            "right": clear_max_x - leaf_right[1][0],
            "bottom_left": leaf_left[0][2] - clear_min_z,
            "bottom_right": leaf_right[0][2] - clear_min_z,
            "top_left": clear_max_z - leaf_left[1][2],
            "top_right": clear_max_z - leaf_right[1][2],
        }
        meeting_gap = leaf_right[0][0] - leaf_left[1][0]
        tolerances = contract.get("leaf_tolerances_m")
        if not isinstance(tolerances, Mapping):
            checks["clear_opening_coverage"] = False
            failures.append(f"{side} leaf tolerances are absent")
        else:
            try:
                max_perimeter = _number(
                    tolerances["maximum_perimeter_gap"], "perimeter tolerance"
                )
                max_meeting = _number(
                    tolerances["maximum_meeting_gap"], "meeting tolerance"
                )
            except (KeyError, ParthenonDoorAssemblyError) as exc:
                checks["clear_opening_coverage"] = False
                failures.append(f"{side} leaf tolerances are invalid: {exc}")
            else:
                if (
                    any(gap < -LINEAR_TOLERANCE_M or gap > max_perimeter + LINEAR_TOLERANCE_M for gap in perimeter_gaps.values())
                    or meeting_gap < -LINEAR_TOLERANCE_M
                    or meeting_gap > max_meeting + LINEAR_TOLERANCE_M
                ):
                    checks["clear_opening_coverage"] = False
                    failures.append(
                        f"{side} leaves do not cover the clear opening within typed gaps"
                    )
        metrics[side] = {
            "rough_opening_width_m": rough_width,
            "rough_opening_height_m": rough_max_z - rough_min_z,
            "clear_opening_width_m": clear_max_x - clear_min_x,
            "clear_opening_height_m": clear_max_z - clear_min_z,
            "leaf_envelope_width_m": leaf_right[1][0] - leaf_left[0][0],
            "leaf_height_m": min(
                leaf_left[1][2] - leaf_left[0][2],
                leaf_right[1][2] - leaf_right[0][2],
            ),
            "perimeter_gaps_m": perimeter_gaps,
            "meeting_gap_m": meeting_gap,
            "wall_bounds_y_m": list(contract.get("wall_bounds_y_m", ())),
        }

        door_bounds = {role: _box_bounds(operation) for role, operation in roles.items()}
        for first_index, first_role in enumerate(_EXPECTED_ROLES):
            for second_role in _EXPECTED_ROLES[first_index + 1 :]:
                overlap = _intersection_volume(
                    door_bounds[first_role], door_bounds[second_role]
                )
                if overlap > VOLUME_TOLERANCE_M3:
                    checks["frame_leaf_noncollision"] = False
                    failures.append(
                        f"{side} door detail collision: {first_role}/{second_role}={overlap:.9g} m^3"
                    )

        walls = tuple(
            operation
            for operation in full_operations
            if operation.get("component_id") == "cella"
            and (
                str(operation.get("operation_id", "")).startswith("cella-wall-")
                or operation.get("operation_id") == "cella-partition"
            )
            and operation.get("kind") == "box"
        )
        for role, bounds in door_bounds.items():
            for wall in walls:
                try:
                    overlap = _intersection_volume(bounds, _box_bounds(wall))
                except ParthenonDoorAssemblyError as exc:
                    checks["wall_noncollision"] = False
                    failures.append(str(exc))
                    continue
                if overlap > VOLUME_TOLERANCE_M3:
                    checks["wall_noncollision"] = False
                    failures.append(
                        f"{side} {role} intersects {wall['operation_id']} by {overlap:.9g} m^3"
                    )

        for role, operation in roles.items():
            parameters = operation["parameters"]
            assert isinstance(parameters, Mapping)
            material_role = parameters.get("material_role")
            if role.startswith("leaf-"):
                expected_material = "door-timber-candidate"
                expected_role = "door-leaf-timber-candidate"
            else:
                expected_material = "pentelic-marble-structural"
                expected_role = (
                    "stone-door-threshold"
                    if role == "frame-threshold"
                    else "stone-door-reveal"
                )
            if (
                operation.get("material_id") != expected_material
                or material_role != expected_role
            ):
                checks["material_roles"] = False
                failures.append(f"{side} {role} material role drifted")

    if set(side_contracts) == {"east", "west"}:
        east = side_contracts["east"]
        west = side_contracts["west"]
        east_y = east.get("wall_bounds_y_m")
        west_y = west.get("wall_bounds_y_m")
        if (
            east.get("opening_strategy") != west.get("opening_strategy")
            or east.get("rough_opening") != west.get("rough_opening")
            or east.get("clear_opening") != west.get("clear_opening")
            or not isinstance(east_y, Sequence)
            or not isinstance(west_y, Sequence)
            or len(east_y) != 2
            or len(west_y) != 2
            or not _close(_number(east_y[0], "east y"), -_number(west_y[1], "west y"))
            or not _close(_number(east_y[1], "east y"), -_number(west_y[0], "west y"))
        ):
            checks["coaxial_and_mirrored"] = False
            failures.append("east/west principal-door strategies are not mirrored")
    else:
        checks["east_west_assemblies"] = False

    door_or_host_delta = tuple(
        operation
        for operation in full_operations
        if operation.get("component_id") in {"door-east", "door-west"}
        or (
            isinstance(operation.get("parameters"), Mapping)
            and "door_aperture_binding" in operation["parameters"]
        )
    )
    for operation in door_or_host_delta:
        lineage_keys = {
            key
            for key in ("replaces_operation_id", "refines_operation_id")
            if operation.get(key)
        }
        basis = operation.get("parameter_basis")
        if (
            len(lineage_keys) != 1
            or not operation.get("source_refs")
            or not operation.get("visual_region_refs")
            or not isinstance(basis, Mapping)
            or basis.get("classification") != "SOFT"
            or basis.get("metric_authority") is not False
            or basis.get("visual_role")
            != "topology_or_morphology_only_not_exact_dimension"
        ):
            checks["evidence_and_lineage"] = False
            failures.append(
                f"{operation.get('operation_id')} lacks SOFT evidence/lineage authority"
            )
        branch_surface = canonical_json(
            {
                "operation_id": operation.get("operation_id"),
                "material_id": operation.get("material_id"),
                "parameters": operation.get("parameters"),
            }
        ).lower()
        if any(token in branch_surface for token in _FORBIDDEN_BRANCH_TOKENS):
            checks["branch_scope"] = False
            failures.append(f"branch leakage in {operation.get('operation_id')}")
        parameters = operation.get("parameters")
        if (
            operation.get("component_id") in {"door-east", "door-west"}
            and isinstance(parameters, Mapping)
            and ("window_clear" in parameters or "window_side" in parameters)
        ):
            checks["window_role_separation"] = False
            failures.append(
                f"east-window metadata was misclassified as door geometry: {operation.get('operation_id')}"
            )

    for operation in full_operations:
        parameters = operation.get("parameters")
        if isinstance(parameters, Mapping) and (
            "window_clear" in parameters or "window_side" in parameters
        ) and operation.get("component_id") != "cella":
            checks["window_role_separation"] = False
            failures.append(
                f"window host {operation.get('operation_id')} escaped the cella component"
            )

    return {
        "schema": "ParthenonDoorAssemblyValidation@1",
        "resolution": chosen.value,
        "passed": not failures and all(checks.values()),
        "policy_compliant": not failures and all(checks.values()),
        "blocking": False,
        "stage_gate_satisfied": not failures and all(checks.values()),
        "human_authorization_ref": authorized_ref,
        "checks": checks,
        "metrics": metrics,
        "failures": failures,
        "branch_id": BRANCH_ID,
        "measurement_authority": {
            "textual_principal_opening": "inherited/cross-checked",
            "frame_and_leaf_detail": "SOFT",
            "visual_regions": "topology_or_morphology_only_not_exact_dimension",
            "metric_authority": False,
        },
    }


__all__ = [
    "BRANCH_ID",
    "DoorAssemblyResolution",
    "ParthenonDoorAssemblyError",
    "apply_door_assembly_delta",
    "compile_door_assembly_delta",
    "measure_door_dependency_state",
    "validate_door_assembly_operations",
]
