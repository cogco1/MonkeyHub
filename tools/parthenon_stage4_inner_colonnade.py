"""Evidence-bound Stage 4 correction for the Parthenon naos colonnade.

The existing Stage 4 program inherited the 23 two-tier U-colonnade stacks as
46 schematic ``fluted_column`` operations.  The lower and upper shafts meet
directly at one Z plane and the fixed east-window centres project through the
outer envelopes of the nearest naos shafts.  This module compiles a bounded
successor delta that fixes those two relations without changing the four
west-room Ionic axes or acquiring persistence/canonical authority.

Visual-region references authorize morphology and topology only.  Every
metric introduced here is therefore explicitly SOFT and
``metric_authority=False``.  The window location is not read from pixels: it is
recomputed from the side-aisle interval between the inner-column envelope and
the cella side-wall inner face.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence
from archflow.contracts.canonical import canonical_digest


BRANCH_ID = "idealized-periclean-original"
DELTA_SCHEMA = "ParthenonStage4InnerColonnadeDelta@1"
VALIDATION_SCHEMA = "ParthenonStage4InnerColonnadeValidation@1"
REFINEMENT_SCOPE = "inner-colonnade-and-east-window-correction"
MATERIAL_ID = "pentelic-marble"
MATERIAL_ROLE = "structural-pentelic-marble"
FLUTE_COUNT = 20
RADIAL_SEGMENTS = 120
ENTASIS_LOCATION_RATIO = 0.40
FLUTE_DEPTH_M = 0.030
_TOLERANCE = 1.0e-6

_U_ROW_COUNTS = {"north": 10, "south": 10, "west": 3}
_WINDOW_PIECE_LABELS = frozenset({"outer-pier", "inner-pier", "sill", "lintel"})
_IONIC_EXPECTED: Mapping[str, Mapping[str, object]] = {
    "west-room-ionic-0-0": {
        "center": [-3.0, -17.974999999999998, 1.35],
        "height": 11.7,
        "diameter": 1.05,
        "flutes": 24,
    },
    "west-room-ionic-0-1": {
        "center": [3.0, -17.974999999999998, 1.35],
        "height": 11.7,
        "diameter": 1.05,
        "flutes": 24,
    },
    "west-room-ionic-1-0": {
        "center": [-3.0, -13.174999999999999, 1.35],
        "height": 11.7,
        "diameter": 1.05,
        "flutes": 24,
    },
    "west-room-ionic-1-1": {
        "center": [3.0, -13.174999999999999, 1.35],
        "height": 11.7,
        "diameter": 1.05,
        "flutes": 24,
    },
}
_FORBIDDEN_BRANCH_MARKERS = (
    "nero",
    "readable-inscription",
    "shield-hole",
    "medieval",
    "byzantine",
    "modern-restoration",
    "construction-trace",
)


class InnerColonnadeError(RuntimeError):
    """The current successor or requested correction violates the contract."""


def _operation_fingerprint(operation: Mapping[str, object]) -> str:
    return canonical_digest(operation, ascii=False)


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _vec3(value: object, label: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 3
    ):
        raise ValueError(f"{label} must be a three-vector")
    return tuple(_finite(item, label) for item in value)  # type: ignore[return-value]


def _box_bounds(operation: Mapping[str, object]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    parameters = operation.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("operation parameters must be a mapping")
    origin = _vec3(parameters.get("origin"), "origin")
    size = _vec3(parameters.get("size"), "size")
    if any(item <= 0.0 for item in size):
        raise ValueError("box size must be positive")
    return origin, tuple(origin[index] + size[index] for index in range(3))


def _close(first: float, second: float) -> bool:
    return math.isclose(first, second, abs_tol=_TOLERANCE)


def _normalized_refs(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized = tuple(sorted({str(item).strip() for item in values if str(item).strip()}))
    if not normalized:
        raise InnerColonnadeError(f"{label} must not be empty")
    return normalized


def _soft_basis(
    statement: str,
    textual_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> dict[str, object]:
    return {
        "classification": "SOFT",
        "metric_authority": False,
        "statement": statement,
        "source_refs": list(textual_refs),
        "selected_visual_region_refs": list(visual_refs),
        "visual_role": "topology_or_morphology_only_not_exact_dimension",
    }


def _delta_operation(
    *,
    operation_id: str,
    component_id: str,
    kind: str,
    parameters: Mapping[str, object],
    textual_refs: Sequence[str],
    visual_refs: Sequence[str],
    basis_statement: str,
    host: Mapping[str, object],
    contact: Mapping[str, object] | Sequence[Mapping[str, object]],
    replaces_operation_id: str | None = None,
    refines_operation_id: str | None = None,
    decision_refs: Sequence[str] = (),
    material_role: str = MATERIAL_ROLE,
) -> dict[str, object]:
    if (replaces_operation_id is None) == (refines_operation_id is None):
        raise ValueError("a correction operation requires exactly one lineage id")
    result: dict[str, object] = {
        "operation_id": operation_id,
        "component_id": component_id,
        "kind": kind,
        "parameters": copy.deepcopy(dict(parameters)),
        "decision_refs": sorted(set(decision_refs)),
        "source_refs": list(textual_refs),
        "visual_region_refs": list(visual_refs),
        "parameter_basis": _soft_basis(
            basis_statement,
            textual_refs,
            visual_refs,
        ),
        "metric_authority": False,
        "branch_id": BRANCH_ID,
        "refinement_scope": REFINEMENT_SCOPE,
        "host": copy.deepcopy(dict(host)),
        "contact_contract": (
            copy.deepcopy(dict(contact))
            if isinstance(contact, Mapping)
            else [copy.deepcopy(dict(item)) for item in contact]
        ),
        "material_id": MATERIAL_ID,
        "material_role": material_role,
        "coordinate_system": "RhinoWorldXY_ZUp",
        "up_axis": "Z",
    }
    if replaces_operation_id is not None:
        result["replaces_operation_id"] = replaces_operation_id
    else:
        result["refines_operation_id"] = refines_operation_id
    return result


def _row_from_stack_id(stack_id: str) -> str:
    for row in _U_ROW_COUNTS:
        if stack_id.startswith(f"naos-{row}-"):
            return row
    raise InnerColonnadeError(f"unrecognized U-colonnade stack id: {stack_id}")


def _shaft_and_capital_operations(
    old: Mapping[str, object],
    *,
    textual_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> tuple[dict[str, object], ...]:
    operation_id = str(old["operation_id"])
    tier = "lower" if operation_id.endswith("-lower") else "upper"
    if not operation_id.endswith(("-lower", "-upper")):
        raise InnerColonnadeError(f"untyped naos tier operation: {operation_id}")
    stack_id = operation_id.rsplit("-", 1)[0]
    row = _row_from_stack_id(stack_id)
    parameters = old.get("parameters")
    if not isinstance(parameters, Mapping):
        raise InnerColonnadeError(f"{operation_id} has no parameter mapping")
    center = _vec3(parameters.get("center"), f"{operation_id}.center")
    total_height = _finite(parameters.get("height"), f"{operation_id}.height")
    lower_diameter = _finite(parameters.get("diameter"), f"{operation_id}.diameter")

    # The subdivisions are diameter-scaled reconstruction choices, not
    # measurements.  The inherited tier boundary and total top elevation stay
    # fixed, so adding the members cannot lengthen the building envelope.
    capital_height = lower_diameter * 0.34
    support_height = lower_diameter * 0.20 if tier == "lower" else 0.0
    neck_height = capital_height * 0.25
    echinus_height = capital_height * 0.45
    abacus_height = capital_height - neck_height - echinus_height
    shaft_height = total_height - capital_height - support_height
    if shaft_height <= 0.0:
        raise InnerColonnadeError(f"{operation_id} has no room for a typed capital")
    upper_diameter = lower_diameter * 0.86
    entasis = lower_diameter * 0.009
    shaft_top = center[2] + shaft_height

    geometry_contract = {
        "mode": "radial_vertex_displacement",
        "profile_generator": "sampled-twenty-flute-doric-shaft",
        "actual_flute_grooves": True,
        "metadata_only": False,
        "ring_count": 9,
        "radial_segments": RADIAL_SEGMENTS,
        "samples_per_flute": RADIAL_SEGMENTS // FLUTE_COUNT,
        "flute_depth_m": FLUTE_DEPTH_M,
    }
    shared_parameters = {
        "stack_id": stack_id,
        "u_row": row,
        "tier": tier,
        "axis": "Z",
        "original_center": list(center),
        "tier_total_height": total_height,
        "capital_height": capital_height,
        "intertier_support_height": support_height,
    }
    shaft = _delta_operation(
        operation_id=operation_id,
        component_id="interior-colonnade",
        kind="doric_shaft",
        parameters={
            **shared_parameters,
            "center": list(center),
            "shaft_height": shaft_height,
            "lower_diameter": lower_diameter,
            "upper_diameter": upper_diameter,
            "entasis_max": entasis,
            "entasis_location_ratio": ENTASIS_LOCATION_RATIO,
            "flutes": FLUTE_COUNT,
            "radial_segments": RADIAL_SEGMENTS,
            "flute_depth_m": FLUTE_DEPTH_M,
            "geometry_contract": geometry_contract,
        },
        textual_refs=textual_refs,
        visual_refs=visual_refs,
        basis_statement=(
            "Stage 3 tier axis, tier boundary, and U topology are inherited; "
            "20-flute Doric morphology is evidence-bound while taper, entasis, "
            "and diameter-scaled capital subdivision remain SOFT"
        ),
        host={
            "operation_id": (
                "crepidoma-step-2" if tier == "lower" else f"naos-u-architrave-{row}"
            ),
            "relation": "bears_on",
        },
        contact={
            "relation": "supports",
            "first_operation_id": (
                "crepidoma-step-2" if tier == "lower" else f"naos-u-architrave-{row}"
            ),
            "second_operation_id": operation_id,
            "intersection_policy": "touch_only",
            "host_operation_id": (
                "crepidoma-step-2" if tier == "lower" else f"naos-u-architrave-{row}"
            ),
            "lower_operation_id": (
                "crepidoma-step-2" if tier == "lower" else f"naos-u-architrave-{row}"
            ),
            "upper_operation_id": operation_id,
            "allowed_contact": "shared_z_face_only",
            "positive_volume_overlap_allowed": False,
        },
        replaces_operation_id=operation_id,
        decision_refs=("decision:interior-supports", "decision:interior-support-measures"),
    )

    neck_id = f"{operation_id}-neck"
    neck = _delta_operation(
        operation_id=neck_id,
        component_id="interior-colonnade",
        kind="doric_neck",
        parameters={
            **shared_parameters,
            "center": [center[0], center[1], shaft_top],
            "height": neck_height,
            "lower_diameter": upper_diameter,
            "upper_diameter": upper_diameter * 1.03,
            "host_shaft_id": operation_id,
            "axis": "Z",
        },
        textual_refs=textual_refs,
        visual_refs=visual_refs,
        basis_statement="Doric neck sequence is morphological evidence; scaled subdivision is SOFT",
        host={"operation_id": operation_id, "relation": "bears_on"},
        contact={
            "relation": "supports",
            "first_operation_id": operation_id,
            "second_operation_id": neck_id,
            "intersection_policy": "touch_only",
            "host_operation_id": operation_id,
            "lower_operation_id": operation_id,
            "upper_operation_id": neck_id,
            "allowed_contact": "shared_z_face_only",
            "positive_volume_overlap_allowed": False,
        },
        refines_operation_id=operation_id,
        decision_refs=("decision:interior-supports",),
    )
    echinus_id = f"{operation_id}-echinus"
    echinus_z = shaft_top + neck_height
    echinus = _delta_operation(
        operation_id=echinus_id,
        component_id="interior-colonnade",
        kind="doric_echinus",
        parameters={
            **shared_parameters,
            "center": [center[0], center[1], echinus_z],
            "height": echinus_height,
            "lower_diameter": upper_diameter * 1.03,
            "upper_diameter": lower_diameter * 1.05,
            "profile": "convex-doric",
            "host_shaft_id": operation_id,
            "axis": "Z",
        },
        textual_refs=textual_refs,
        visual_refs=visual_refs,
        basis_statement="Doric echinus presence and convex topology are supported; profile scale is SOFT",
        host={"operation_id": neck_id, "relation": "bears_on"},
        contact={
            "relation": "supports",
            "first_operation_id": neck_id,
            "second_operation_id": echinus_id,
            "intersection_policy": "touch_only",
            "host_operation_id": neck_id,
            "lower_operation_id": neck_id,
            "upper_operation_id": echinus_id,
            "allowed_contact": "shared_z_face_only",
            "positive_volume_overlap_allowed": False,
        },
        refines_operation_id=operation_id,
        decision_refs=("decision:interior-supports",),
    )
    abacus_id = f"{operation_id}-abacus"
    abacus_z = echinus_z + echinus_height
    abacus_width = lower_diameter * 1.25
    abacus = _delta_operation(
        operation_id=abacus_id,
        component_id="interior-colonnade",
        kind="doric_abacus",
        parameters={
            **shared_parameters,
            "origin": [
                center[0] - abacus_width / 2.0,
                center[1] - abacus_width / 2.0,
                abacus_z,
            ],
            "size": [abacus_width, abacus_width, abacus_height],
            "center_xy": [center[0], center[1]],
            "host_shaft_id": operation_id,
            "axis": "Z",
        },
        textual_refs=textual_refs,
        visual_refs=visual_refs,
        basis_statement="Square Doric abacus topology is supported; diameter-scaled size is SOFT",
        host={"operation_id": echinus_id, "relation": "bears_on"},
        contact={
            "relation": "supports",
            "first_operation_id": echinus_id,
            "second_operation_id": abacus_id,
            "intersection_policy": "touch_only",
            "host_operation_id": echinus_id,
            "lower_operation_id": echinus_id,
            "upper_operation_id": abacus_id,
            "allowed_contact": "shared_z_face_only",
            "positive_volume_overlap_allowed": False,
        },
        refines_operation_id=operation_id,
        decision_refs=("decision:interior-supports",),
    )
    return shaft, neck, echinus, abacus


def _architrave_operations(
    shaft_operations: Sequence[Mapping[str, object]],
    capital_operations: Sequence[Mapping[str, object]],
    *,
    textual_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> tuple[dict[str, object], ...]:
    lower_shafts = {
        str(item["parameters"]["stack_id"]): item
        for item in shaft_operations
        if item["parameters"]["tier"] == "lower"
    }
    upper_shafts = {
        str(item["parameters"]["stack_id"]): item
        for item in shaft_operations
        if item["parameters"]["tier"] == "upper"
    }
    lower_abaci = {
        str(item["parameters"]["stack_id"]): item
        for item in capital_operations
        if item["kind"] == "doric_abacus" and item["parameters"]["tier"] == "lower"
    }
    rows: defaultdict[str, list[str]] = defaultdict(list)
    for stack_id in sorted(lower_shafts):
        rows[_row_from_stack_id(stack_id)].append(stack_id)

    common_support_heights = {
        round(float(item["parameters"]["intertier_support_height"]), 9)
        for item in lower_shafts.values()
    }
    if len(common_support_heights) != 1:
        raise InnerColonnadeError("lower tier support heights are not uniform")
    support_height = next(iter(common_support_heights))
    upper_zs = {
        round(float(item["parameters"]["center"][2]), 9)
        for item in upper_shafts.values()
    }
    if len(upper_zs) != 1:
        raise InnerColonnadeError("upper tier starts are not coplanar")
    upper_z = next(iter(upper_zs))
    support_z = upper_z - support_height
    maximum_abacus_width = max(
        float(item["parameters"]["size"][0]) for item in lower_abaci.values()
    )
    half_width = maximum_abacus_width / 2.0

    centers = {
        stack_id: _vec3(item["parameters"]["center"], f"{stack_id}.center")
        for stack_id, item in lower_shafts.items()
    }
    positive_x = max(point[0] for point in centers.values())
    negative_x = min(point[0] for point in centers.values())
    west_y = min(point[1] for point in centers.values())
    long_y = max(point[1] for point in centers.values())
    segment_boxes = {
        "north": (
            [positive_x - half_width, west_y - half_width, support_z],
            [maximum_abacus_width, long_y - west_y + 2.0 * half_width, support_height],
        ),
        "south": (
            [negative_x - half_width, west_y - half_width, support_z],
            [maximum_abacus_width, long_y - west_y + 2.0 * half_width, support_height],
        ),
        # End exactly on the side runs: the three boxes form a U by shared
        # faces, not by positive-volume intersection.
        "west": (
            [negative_x + half_width, west_y - half_width, support_z],
            [positive_x - negative_x - 2.0 * half_width, maximum_abacus_width, support_height],
        ),
    }

    result: list[dict[str, object]] = []
    for row in ("north", "south", "west"):
        stack_ids = sorted(rows[row])
        origin, size = segment_boxes[row]
        operation_id = f"naos-u-architrave-{row}"
        lower_ids = [f"{stack_id}-lower-abacus" for stack_id in stack_ids]
        upper_ids = [f"{stack_id}-upper" for stack_id in stack_ids]
        result.append(
            _delta_operation(
                operation_id=operation_id,
                component_id="interior-colonnade",
                kind="bearing_block",
                parameters={
                    "origin": origin,
                    "size": size,
                    "u_row": row,
                    "semantic_role": "intertier-u-architrave",
                    "host_operation_ids": lower_ids,
                    "supports_operation_ids": upper_ids,
                    "axis": "Z",
                    "positive_volume_overlap_allowed": False,
                },
                textual_refs=textual_refs,
                visual_refs=visual_refs,
                basis_statement=(
                    "The inherited double-tier U topology requires a continuous "
                    "intertier bearing line; its diameter-scaled cross-section is SOFT"
                ),
                host={"operation_ids": lower_ids, "relation": "bears_on"},
                contact=(
                    *(
                        {
                            "first_operation_id": lower_id,
                            "second_operation_id": operation_id,
                            "relation": "supports",
                            "intersection_policy": "touch_only",
                            "host_operation_id": lower_id,
                            "allowed_contact": "shared_z_face_only",
                            "positive_volume_overlap_allowed": False,
                        }
                        for lower_id in lower_ids
                    ),
                    *(
                        {
                            "first_operation_id": operation_id,
                            "second_operation_id": upper_id,
                            "relation": "supports",
                            "intersection_policy": "touch_only",
                            "host_operation_id": operation_id,
                            "allowed_contact": "shared_z_face_only",
                            "positive_volume_overlap_allowed": False,
                        }
                        for upper_id in upper_ids
                    ),
                ),
                refines_operation_id=f"{stack_ids[0]}-lower",
                decision_refs=("decision:interior-supports", "decision:roof-system"),
            )
        )
    return tuple(result)


def _window_groups(
    operations: Iterable[Mapping[str, object]],
) -> dict[str, list[Mapping[str, object]]]:
    groups: defaultdict[str, list[Mapping[str, object]]] = defaultdict(list)
    for operation in operations:
        parameters = operation.get("parameters")
        if not isinstance(parameters, Mapping) or "window_clear" not in parameters:
            continue
        side = str(parameters.get("window_side", "")).strip()
        if side not in {"left", "right"}:
            raise InnerColonnadeError(f"window piece has invalid side: {operation.get('operation_id')}")
        groups[side].append(operation)
    return dict(groups)


def _wall_inner_faces(operations_by_id: Mapping[str, Mapping[str, object]]) -> tuple[float, float]:
    _, negative_max = _box_bounds(operations_by_id["cella-wall-south"])
    positive_min, _ = _box_bounds(operations_by_id["cella-wall-north"])
    if negative_max[0] >= 0.0 or positive_min[0] <= 0.0:
        raise InnerColonnadeError("cella side-wall inner faces do not straddle the axis")
    return negative_max[0], positive_min[0]


def _column_outer_envelopes(
    shaft_operations: Sequence[Mapping[str, object]],
) -> tuple[float, float]:
    lower = [
        item
        for item in shaft_operations
        if item["parameters"]["tier"] == "lower"
    ]
    negative: list[float] = []
    positive: list[float] = []
    for operation in lower:
        parameters = operation["parameters"]
        center = _vec3(parameters["center"], "shaft center")
        radius = float(parameters["lower_diameter"]) / 2.0 + float(parameters["entasis_max"])
        if center[0] < 0.0:
            negative.append(center[0] - radius)
        elif center[0] > 0.0:
            positive.append(center[0] + radius)
    if not negative or not positive:
        raise InnerColonnadeError("U colonnade has no mirrored side rows")
    return min(negative), max(positive)


def _window_replacements(
    current_window_operations: Sequence[Mapping[str, object]],
    operations_by_id: Mapping[str, Mapping[str, object]],
    shaft_operations: Sequence[Mapping[str, object]],
    *,
    textual_refs: Sequence[str],
    visual_refs: Sequence[str],
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    groups = _window_groups(current_window_operations)
    if set(groups) != {"left", "right"} or any(len(items) != 4 for items in groups.values()):
        raise InnerColonnadeError("expected four true-wall pieces for each east window")
    negative_wall_face, positive_wall_face = _wall_inner_faces(operations_by_id)
    negative_column_edge, positive_column_edge = _column_outer_envelopes(shaft_operations)
    if not _close(abs(negative_wall_face), positive_wall_face) or not _close(
        abs(negative_column_edge), positive_column_edge
    ):
        raise InnerColonnadeError("window relation inputs are not axis-mirrored")
    center_abs = (positive_column_edge + positive_wall_face) / 2.0

    result: list[dict[str, object]] = []
    derived: dict[str, object] = {
        "method": "side-aisle-midpoint-between-column-envelope-and-side-wall-inner-face",
        "metric_authority": False,
        "negative_column_outer_edge_m": negative_column_edge,
        "positive_column_outer_edge_m": positive_column_edge,
        "negative_side_wall_inner_face_m": negative_wall_face,
        "positive_side_wall_inner_face_m": positive_wall_face,
        "center_magnitude_m": center_abs,
    }
    for side, sign in (("left", -1.0), ("right", 1.0)):
        old_pieces = groups[side]
        old_bounds = [_box_bounds(item) for item in old_pieces]
        wall_min = tuple(min(bounds[0][axis] for bounds in old_bounds) for axis in range(3))
        wall_max = tuple(max(bounds[1][axis] for bounds in old_bounds) for axis in range(3))
        clear_values = {
            canonical_digest(item["parameters"]["window_clear"], ascii=False): item["parameters"]["window_clear"]
            for item in old_pieces
        }
        if len(clear_values) != 1:
            raise InnerColonnadeError(f"{side} window pieces disagree on aperture")
        old_clear = next(iter(clear_values.values()))
        if not isinstance(old_clear, Mapping):
            raise InnerColonnadeError(f"{side} window clear contract is malformed")
        width = _finite(old_clear.get("width"), "window width")
        sill_z = _finite(old_clear.get("sill_z"), "window sill")
        height = _finite(old_clear.get("height"), "window height")
        center_x = sign * center_abs
        window_x0 = center_x - width / 2.0
        window_x1 = center_x + width / 2.0
        window_z1 = sill_z + height
        if window_x0 <= wall_min[0] or window_x1 >= wall_max[0]:
            raise InnerColonnadeError(f"derived {side} window does not fit its Stage 3 host wall")
        if sill_z <= wall_min[2] or window_z1 >= wall_max[2]:
            raise InnerColonnadeError(f"derived {side} window exceeds its Stage 3 host wall vertically")
        nearest_depth = min(
            abs(wall_min[1] - float(item["parameters"]["center"][1]))
            for item in shaft_operations
            if item["parameters"]["tier"] == "lower"
        )
        piece_geometry = {
            "outer-pier": (
                [wall_min[0], wall_min[1], wall_min[2]],
                [window_x0 - wall_min[0], wall_max[1] - wall_min[1], wall_max[2] - wall_min[2]],
            ),
            "inner-pier": (
                [window_x1, wall_min[1], wall_min[2]],
                [wall_max[0] - window_x1, wall_max[1] - wall_min[1], wall_max[2] - wall_min[2]],
            ),
            "sill": (
                [window_x0, wall_min[1], wall_min[2]],
                [width, wall_max[1] - wall_min[1], sill_z - wall_min[2]],
            ),
            "lintel": (
                [window_x0, wall_min[1], window_z1],
                [width, wall_max[1] - wall_min[1], wall_max[2] - window_z1],
            ),
        }
        old_by_label = {
            next(
                label
                for label in _WINDOW_PIECE_LABELS
                if str(item["operation_id"]).endswith(f"-{label}")
            ): item
            for item in old_pieces
        }
        if set(old_by_label) != _WINDOW_PIECE_LABELS:
            raise InnerColonnadeError(f"{side} window does not have the four typed wall pieces")
        stage3_wall_id = f"cella-wall-east-{side}"
        for label in sorted(_WINDOW_PIECE_LABELS):
            old = old_by_label[label]
            origin, size = piece_geometry[label]
            operation_id = str(old["operation_id"])
            result.append(
                _delta_operation(
                    operation_id=operation_id,
                    component_id="cella",
                    kind="box",
                    parameters={
                        "origin": origin,
                        "size": size,
                        "window_side": side,
                        "window_piece_role": label,
                        "window_clear": {
                            "center_x": center_x,
                            "width": width,
                            "sill_z": sill_z,
                            "height": height,
                            "interior_target_depth": nearest_depth,
                        },
                        "void_contract": {
                            "realization": "decomposed-host-wall-void-not-surface-patch",
                            "stage3_host_wall_operation_id": stage3_wall_id,
                            "opening_is_empty_volume": True,
                            "mirror_partner_side": "right" if side == "left" else "left",
                        },
                        "relational_parameter_basis": derived,
                    },
                    textual_refs=textual_refs,
                    visual_refs=visual_refs,
                    basis_statement=(
                        "Window width, height, and true host-wall void are retained; "
                        "centre is a SOFT side-aisle midpoint derived from wall and column envelopes"
                    ),
                    host={
                        "operation_id": stage3_wall_id,
                        "relation": "decomposes_host_around_void",
                        "predecessor_window_piece_operation_id": operation_id,
                    },
                    contact={
                        "relation": "bounds_empty_aperture",
                        "window_side": side,
                        "positive_volume_overlap_allowed": False,
                    },
                    replaces_operation_id=operation_id,
                    decision_refs=("decision:cella-openings", "decision:room-connectivity"),
                )
            )
    return tuple(result), derived


def compile_inner_colonnade_delta(
    current_operations: Sequence[Mapping[str, object]],
    textual_evidence_refs: Sequence[str],
    selected_visual_refs: Sequence[str],
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Return a complete successor and deterministic typed lineage receipt.

    The input is the current 509-operation Stage 4 compilation, not Stage 3.
    No filesystem writes or run selection occur here.
    """

    textual_refs = _normalized_refs(textual_evidence_refs, "textual_evidence_refs")
    visual_refs = _normalized_refs(selected_visual_refs, "selected_visual_refs")
    copied = [copy.deepcopy(dict(item)) for item in current_operations]
    ids = [str(item.get("operation_id", "")) for item in copied]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise InnerColonnadeError("current operations need unique non-empty operation ids")
    by_id = {str(item["operation_id"]): item for item in copied}

    ionic = {
        operation_id: operation
        for operation_id, operation in by_id.items()
        if operation.get("component_id") == "interior-colonnade"
        and operation.get("kind") == "ionic_column"
    }
    if set(ionic) != set(_IONIC_EXPECTED):
        raise InnerColonnadeError("the four inherited west-room Ionic axes are not intact")
    ionic_fingerprints = {
        operation_id: _operation_fingerprint(operation)
        for operation_id, operation in sorted(ionic.items())
    }

    inherited_shafts = [
        operation
        for operation in copied
        if operation.get("component_id") == "interior-colonnade"
        and operation.get("kind") == "fluted_column"
        and str(operation.get("operation_id", "")).startswith("naos-")
    ]
    if len(inherited_shafts) != 46:
        raise InnerColonnadeError("expected the 46 inherited naos fluted shafts")
    tier_counts = Counter(
        str(item["operation_id"]).rsplit("-", 1)[-1] for item in inherited_shafts
    )
    if tier_counts != {"lower": 23, "upper": 23}:
        raise InnerColonnadeError("the inherited U colonnade is not 23 lower/upper pairs")

    shaft_and_capitals = tuple(
        item
        for old in sorted(inherited_shafts, key=lambda item: str(item["operation_id"]))
        for item in _shaft_and_capital_operations(
            old,
            textual_refs=textual_refs,
            visual_refs=visual_refs,
        )
    )
    shafts = tuple(item for item in shaft_and_capitals if item["kind"] == "doric_shaft")
    capitals = tuple(item for item in shaft_and_capitals if item["kind"] != "doric_shaft")
    architraves = _architrave_operations(
        shafts,
        capitals,
        textual_refs=textual_refs,
        visual_refs=visual_refs,
    )

    current_windows = tuple(
        item
        for item in copied
        if isinstance(item.get("parameters"), Mapping)
        and "window_clear" in item["parameters"]
    )
    if len(current_windows) != 8:
        raise InnerColonnadeError("expected eight existing east-window host pieces")
    windows, window_basis = _window_replacements(
        current_windows,
        by_id,
        shafts,
        textual_refs=textual_refs,
        visual_refs=visual_refs,
    )

    replaced_ids = {
        *(str(item["operation_id"]) for item in inherited_shafts),
        *(str(item["operation_id"]) for item in current_windows),
    }
    preserved = [item for item in copied if str(item["operation_id"]) not in replaced_ids]
    delta = [*shaft_and_capitals, *architraves, *windows]
    full = tuple(sorted([*preserved, *delta], key=lambda item: str(item["operation_id"])))
    full_ids = [str(item["operation_id"]) for item in full]
    if len(full_ids) != len(set(full_ids)):
        raise InnerColonnadeError("compiled correction produced duplicate operation ids")
    current_fingerprints = {
        str(item["operation_id"]): _operation_fingerprint(item)
        for item in copied
        if str(item["operation_id"]) not in replaced_ids
    }
    full_by_id = {str(item["operation_id"]): item for item in full}
    changed_preserved = [
        operation_id
        for operation_id, fingerprint in current_fingerprints.items()
        if _operation_fingerprint(full_by_id[operation_id]) != fingerprint
    ]
    if changed_preserved:
        raise InnerColonnadeError(f"non-target operations changed: {changed_preserved[:3]}")
    for operation_id, fingerprint in ionic_fingerprints.items():
        if _operation_fingerprint(full_by_id[operation_id]) != fingerprint:
            raise InnerColonnadeError(f"west-room Ionic operation changed: {operation_id}")

    # The immediate predecessor is the current 509-op Stage 4 program, while
    # the coverage denominator remains the exact 271-op Stage 3 program.  Keep
    # both levels so the integrating runner can fold additions back to their
    # exact Stage 3 roots instead of treating this module as a fake stage.
    ancestor_map: dict[str, dict[str, object]] = {}
    for operation in delta:
        operation_id = str(operation["operation_id"])
        relation_key = (
            "replaces_operation_id"
            if "replaces_operation_id" in operation
            else "refines_operation_id"
        )
        immediate_id = str(operation[relation_key])
        parameters = operation["parameters"]
        stage3_roots: set[str]
        if operation.get("component_id") == "cella":
            current_piece = by_id[operation_id]
            stage3_root = current_piece.get("refines_operation_id")
            if not stage3_root:
                raise InnerColonnadeError(
                    f"current east-window piece lacks its Stage 3 wall root: {operation_id}"
                )
            stage3_roots = {str(stage3_root)}
        elif operation.get("kind") == "bearing_block":
            assert isinstance(parameters, Mapping)
            lower_roots = {
                str(item).removesuffix("-abacus")
                for item in parameters.get("host_operation_ids", ())
            }
            upper_roots = {
                str(item) for item in parameters.get("supports_operation_ids", ())
            }
            stage3_roots = lower_roots | upper_roots
        else:
            # All 46 naos shafts are unchanged Stage 3 identities in the
            # current program; their new shaft/capital members refine that id.
            stage3_roots = {immediate_id}
        ancestor_map[operation_id] = {
            "lineage_relation": relation_key.removesuffix("_operation_id"),
            "immediate_current_stage4_operation_id": immediate_id,
            "exact_stage3_root_operation_ids": sorted(stage3_roots),
            "stage3_denominator_binding": "exact_operation_identity",
        }

    folded_stage3_roots = sorted(
        {
            root
            for binding in ancestor_map.values()
            for root in binding["exact_stage3_root_operation_ids"]
        }
    )

    validation = validate_inner_colonnade_operations(full)
    if not validation["passed"]:
        raise InnerColonnadeError(
            "compiled inner-colonnade correction failed validation: "
            + "; ".join(str(item) for item in validation["failures"][:3])
        )
    receipt: dict[str, object] = {
        "schema": DELTA_SCHEMA,
        "passed": True,
        "branch_id": BRANCH_ID,
        "predecessor_operation_count": len(copied),
        "current_operation_count": len(full),
        "preserved_operation_count": len(preserved),
        "preserved_operation_fingerprints": dict(sorted(current_fingerprints.items())),
        "superseded_operation_ids": sorted(replaced_ids),
        "replacement_operation_count": len(replaced_ids),
        "delta_operation_ids": sorted(str(item["operation_id"]) for item in delta),
        "delta_operation_count": len(delta),
        "added_operation_ids": sorted(
            str(item["operation_id"])
            for item in delta
            if str(item["operation_id"]) not in replaced_ids
        ),
        "added_operation_count": sum(
            str(item["operation_id"]) not in replaced_ids for item in delta
        ),
        "u_stack_count": 23,
        "shaft_replacement_count": 46,
        "capital_part_count": len(capitals),
        "intertier_architrave_count": len(architraves),
        "window_piece_replacement_count": len(windows),
        "transitive_ancestor_map": dict(sorted(ancestor_map.items())),
        "folded_stage3_root_operation_ids": folded_stage3_roots,
        "folded_stage3_root_operation_count": len(folded_stage3_roots),
        "west_room_ionic_fingerprints": ionic_fingerprints,
        "textual_evidence_refs": list(textual_refs),
        "selected_visual_refs": list(visual_refs),
        "parameter_basis": {
            "classification": "SOFT",
            "metric_authority": False,
            "window": window_basis,
            "visual_role": "topology_or_morphology_only_not_exact_dimension",
        },
        "validation_digest": canonical_digest(validation, ascii=False),
    }
    receipt["delta_digest"] = canonical_digest({key: value for key, value in receipt.items()}, ascii=False)
    return full, receipt


def _ring_minimum_count(parameters: Mapping[str, object]) -> int:
    segments = int(parameters.get("radial_segments", 0))
    flutes = int(parameters.get("flutes", 0))
    if segments <= 0 or flutes <= 0 or segments % flutes:
        return 0
    samples = segments // flutes
    outer = float(parameters.get("lower_diameter", 0.0)) / 2.0
    depth = float(parameters.get("flute_depth_m", 0.0))
    radii = [
        outer - depth * math.sin(math.pi * ((index % samples) / samples)) ** 2
        for index in range(segments)
    ]
    return sum(
        radii[index] < radii[index - 1] - 1.0e-12
        and radii[index] < radii[(index + 1) % segments] - 1.0e-12
        for index in range(segments)
    )


def _interval_overlap(first: tuple[float, float], second: tuple[float, float]) -> float:
    return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))


def _inner_operation_bounds(
    operation: Mapping[str, object],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    parameters = operation.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("operation parameters must be a mapping")
    kind = str(operation.get("kind", ""))
    if kind in {"doric_abacus", "bearing_block"}:
        return _box_bounds(operation)
    center = _vec3(parameters.get("center"), "center")
    if kind == "doric_shaft":
        height = _finite(parameters.get("shaft_height"), "shaft_height")
        radius = (
            _finite(parameters.get("lower_diameter"), "lower_diameter") / 2.0
            + _finite(parameters.get("entasis_max"), "entasis_max")
        )
    elif kind in {"doric_neck", "doric_echinus"}:
        height = _finite(parameters.get("height"), "height")
        radius = max(
            _finite(parameters.get("lower_diameter"), "lower_diameter"),
            _finite(parameters.get("upper_diameter"), "upper_diameter"),
        ) / 2.0
    else:
        raise ValueError(f"unsupported scoped interior kind: {kind}")
    if height <= 0.0 or radius <= 0.0:
        raise ValueError("bounded interior member dimensions must be positive")
    return (
        (center[0] - radius, center[1] - radius, center[2]),
        (center[0] + radius, center[1] + radius, center[2] + height),
    )


def validate_inner_colonnade_operations(
    full_operations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Validate geometry, lineage, evidence, window, and branch obligations."""

    failures: list[str] = []
    details: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    operations = [dict(item) for item in full_operations if isinstance(item, Mapping)]
    ids = [str(item.get("operation_id", "")) for item in operations]
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    if len(operations) != len(full_operations):
        failures.append("all operations must be mappings")
    if any(not item for item in ids) or duplicate_ids:
        failures.append("operation ids must be non-empty and unique")
    by_id = {str(item.get("operation_id", "")): item for item in operations}

    scoped = [item for item in operations if item.get("refinement_scope") == REFINEMENT_SCOPE]
    lineage_failures = 0
    evidence_failures = 0
    host_failures = 0
    branch_failures = 0
    for operation in scoped:
        operation_id = str(operation.get("operation_id", ""))
        lineage_count = sum(
            key in operation for key in ("replaces_operation_id", "refines_operation_id")
        )
        if lineage_count != 1:
            lineage_failures += 1
            details["lineage"].append({"operation_id": operation_id})
        basis = operation.get("parameter_basis")
        source_refs = operation.get("source_refs")
        visual_refs = operation.get("visual_region_refs")
        if (
            not isinstance(basis, Mapping)
            or basis.get("classification") != "SOFT"
            or not isinstance(source_refs, Sequence)
            or isinstance(source_refs, (str, bytes))
            or not source_refs
            or not isinstance(visual_refs, Sequence)
            or isinstance(visual_refs, (str, bytes))
            or not visual_refs
            or basis.get("visual_role") != "topology_or_morphology_only_not_exact_dimension"
        ):
            evidence_failures += 1
            details["evidence"].append({"operation_id": operation_id})
        contact_contract = operation.get("contact_contract")
        valid_contact_contract = isinstance(contact_contract, Mapping) or (
            isinstance(contact_contract, Sequence)
            and not isinstance(contact_contract, (str, bytes))
            and bool(contact_contract)
            and all(isinstance(item, Mapping) for item in contact_contract)
        )
        if (
            not isinstance(operation.get("host"), Mapping)
            or not valid_contact_contract
            or not str(operation.get("material_role", "")).strip()
            or not str(operation.get("material_id", "")).strip()
        ):
            host_failures += 1
            details["host_contact_material"].append({"operation_id": operation_id})
        branch_probe = json.dumps(
            {
                "operation_id": operation_id,
                "kind": operation.get("kind"),
                "parameters": operation.get("parameters"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        if operation.get("branch_id") != BRANCH_ID or any(
            marker in branch_probe for marker in _FORBIDDEN_BRANCH_MARKERS
        ):
            branch_failures += 1
            details["branch"].append({"operation_id": operation_id})
    if lineage_failures:
        failures.append(f"{lineage_failures} correction operations lack unique lineage")
    if evidence_failures:
        failures.append(f"{evidence_failures} correction operations lack SOFT evidence basis")
    if host_failures:
        failures.append(f"{host_failures} correction operations lack host/contact/material roles")
    if branch_failures:
        failures.append(f"{branch_failures} correction operations leak or misbind the branch")

    legacy_shafts = [
        item
        for item in operations
        if item.get("component_id") == "interior-colonnade"
        and item.get("kind") == "fluted_column"
        and str(item.get("operation_id", "")).startswith("naos-")
    ]
    legacy_by_stack: defaultdict[str, dict[str, Mapping[str, object]]] = defaultdict(dict)
    for shaft in legacy_shafts:
        operation_id = str(shaft["operation_id"])
        if operation_id.endswith(("-lower", "-upper")):
            stack_id, tier = operation_id.rsplit("-", 1)
            legacy_by_stack[stack_id][tier] = shaft
    legacy_direct_contacts = 0
    for tiers in legacy_by_stack.values():
        if set(tiers) != {"lower", "upper"}:
            continue
        lower_params = tiers["lower"].get("parameters", {})
        upper_params = tiers["upper"].get("parameters", {})
        try:
            lower_center = _vec3(lower_params.get("center"), "legacy lower center")
            upper_center = _vec3(upper_params.get("center"), "legacy upper center")
            lower_top = lower_center[2] + _finite(lower_params.get("height"), "legacy height")
            if _close(lower_top, upper_center[2]):
                legacy_direct_contacts += 1
        except ValueError:
            continue
    legacy_window_obstructions = 0
    if legacy_shafts:
        legacy_lower = [
            item
            for item in legacy_shafts
            if str(item["operation_id"]).endswith("-lower")
        ]
        positive_edges = []
        negative_edges = []
        for item in legacy_lower:
            params = item.get("parameters", {})
            try:
                center = _vec3(params.get("center"), "legacy center")
                radius = _finite(params.get("diameter"), "legacy diameter") / 2.0
            except ValueError:
                continue
            if center[0] > 0.0:
                positive_edges.append(center[0] + radius)
            elif center[0] < 0.0:
                negative_edges.append(center[0] - radius)
        for side, pieces in _window_groups(operations).items():
            if not pieces:
                continue
            clear = pieces[0].get("parameters", {}).get("window_clear", {})
            try:
                center = _finite(clear.get("center_x"), "legacy window center")
                width = _finite(clear.get("width"), "legacy window width")
            except ValueError:
                continue
            interval = (center - width / 2.0, center + width / 2.0)
            edge = (
                min(negative_edges) if side == "left" and negative_edges else
                max(positive_edges) if side == "right" and positive_edges else None
            )
            if edge is not None and interval[0] < edge < interval[1]:
                legacy_window_obstructions += 1
    if legacy_shafts:
        failures.append(
            f"{len(legacy_shafts)} inherited schematic naos shafts remain unrefined"
        )
    if legacy_direct_contacts:
        failures.append(
            f"{legacy_direct_contacts} inherited lower/upper shafts touch directly"
        )
    if legacy_window_obstructions:
        failures.append(
            f"{legacy_window_obstructions} inherited east windows project through naos shafts"
        )

    shafts = [
        item
        for item in operations
        if item.get("component_id") == "interior-colonnade"
        and item.get("kind") == "doric_shaft"
        and isinstance(item.get("parameters"), Mapping)
        and "stack_id" in item["parameters"]
    ]
    flute_failures = 0
    axis_failures = 0
    for shaft in shafts:
        parameters = shaft["parameters"]
        geometry = parameters.get("geometry_contract")
        try:
            _vec3(parameters.get("center"), "shaft center")
            valid_axis = (
                parameters.get("axis") == "Z"
                and shaft.get("up_axis") == "Z"
                and float(parameters.get("shaft_height", 0.0)) > 0.0
            )
        except (TypeError, ValueError):
            valid_axis = False
        if not valid_axis:
            axis_failures += 1
            details["axis"].append({"operation_id": shaft["operation_id"]})
        valid_flutes = (
            int(parameters.get("flutes", 0)) == FLUTE_COUNT
            and int(parameters.get("radial_segments", 0)) == RADIAL_SEGMENTS
            and isinstance(geometry, Mapping)
            and geometry.get("mode") == "radial_vertex_displacement"
            and geometry.get("actual_flute_grooves") is True
            and geometry.get("metadata_only") is False
            and int(geometry.get("samples_per_flute", 0)) == RADIAL_SEGMENTS // FLUTE_COUNT
            and _ring_minimum_count(parameters) == FLUTE_COUNT
        )
        if not valid_flutes:
            flute_failures += 1
            details["flutes"].append({"operation_id": shaft["operation_id"]})
    if len(shafts) != 46:
        failures.append(f"expected 46 corrected naos Doric shafts, found {len(shafts)}")
    if flute_failures:
        failures.append(f"{flute_failures} naos shafts lack 20 geometric flute grooves")
    if axis_failures:
        failures.append(f"{axis_failures} naos shafts violate the Z-up long-axis contract")

    shafts_by_stack: defaultdict[str, dict[str, Mapping[str, object]]] = defaultdict(dict)
    for shaft in shafts:
        parameters = shaft["parameters"]
        shafts_by_stack[str(parameters.get("stack_id", ""))][str(parameters.get("tier", ""))] = shaft
    row_counts = Counter()
    stack_failures = 0
    valid_stacks = 0
    direct_shaft_contacts = 0
    architraves = {
        str(item.get("parameters", {}).get("u_row")): item
        for item in operations
        if item.get("component_id") == "interior-colonnade"
        and item.get("kind") == "bearing_block"
        and isinstance(item.get("parameters"), Mapping)
        and item["parameters"].get("semantic_role") == "intertier-u-architrave"
    }
    for stack_id, tiers in sorted(shafts_by_stack.items()):
        try:
            row = _row_from_stack_id(stack_id)
        except InnerColonnadeError:
            row = "invalid"
        row_counts[row] += 1
        if set(tiers) != {"lower", "upper"}:
            stack_failures += 1
            details["stacks"].append({"stack_id": stack_id, "reason": "missing tier"})
            continue
        lower = tiers["lower"]
        upper = tiers["upper"]
        lower_params = lower["parameters"]
        upper_params = upper["parameters"]
        lower_center = _vec3(lower_params["center"], "lower center")
        upper_center = _vec3(upper_params["center"], "upper center")
        if not (_close(lower_center[0], upper_center[0]) and _close(lower_center[1], upper_center[1])):
            stack_failures += 1
            details["stacks"].append({"stack_id": stack_id, "reason": "axes differ"})
            continue
        lower_top = lower_center[2] + float(lower_params["shaft_height"])
        upper_bottom = upper_center[2]
        if _close(lower_top, upper_bottom):
            direct_shaft_contacts += 1
        capital_ok = True
        for tier_name, shaft in (
            ("lower", lower),
            ("upper", upper),
        ):
            params = shaft["parameters"]
            center = _vec3(params["center"], "shaft center")
            shaft_top = center[2] + float(params["shaft_height"])
            neck = by_id.get(f"{shaft['operation_id']}-neck")
            echinus = by_id.get(f"{shaft['operation_id']}-echinus")
            abacus = by_id.get(f"{shaft['operation_id']}-abacus")
            if not neck or not echinus or not abacus:
                capital_ok = False
                continue
            neck_params = neck.get("parameters", {})
            echinus_params = echinus.get("parameters", {})
            try:
                neck_z = _vec3(neck_params.get("center"), "neck center")[2]
                echinus_z = _vec3(echinus_params.get("center"), "echinus center")[2]
                abacus_min, abacus_max = _box_bounds(abacus)
                capital_ok = capital_ok and all(
                    (
                        _close(neck_z, shaft_top),
                        _close(echinus_z, neck_z + float(neck_params["height"])),
                        _close(abacus_min[2], echinus_z + float(echinus_params["height"])),
                    )
                )
                if tier_name == "upper":
                    inherited_top = center[2] + float(params["tier_total_height"])
                    capital_ok = capital_ok and _close(abacus_max[2], inherited_top)
            except (KeyError, TypeError, ValueError):
                capital_ok = False
        architrave = architraves.get(row)
        support_ok = False
        if architrave:
            try:
                arch_min, arch_max = _box_bounds(architrave)
                lower_abacus = by_id[f"{lower['operation_id']}-abacus"]
                _, lower_abacus_max = _box_bounds(lower_abacus)
                host_ids = architrave["parameters"].get("host_operation_ids", [])
                supported_ids = architrave["parameters"].get("supports_operation_ids", [])
                support_ok = (
                    _close(lower_abacus_max[2], arch_min[2])
                    and _close(arch_max[2], upper_bottom)
                    and f"{lower['operation_id']}-abacus" in host_ids
                    and str(upper["operation_id"]) in supported_ids
                )
            except (KeyError, TypeError, ValueError):
                support_ok = False
        if not capital_ok or not support_ok or lower_top >= upper_bottom - _TOLERANCE:
            stack_failures += 1
            details["stacks"].append(
                {
                    "stack_id": stack_id,
                    "reason": "capital/support chain incomplete",
                }
            )
            continue
        valid_stacks += 1
    if len(shafts_by_stack) != 23 or row_counts != Counter(_U_ROW_COUNTS):
        failures.append("the inherited 10/10/3 U-colonnade topology changed")
    if len(architraves) != 3 or set(architraves) != set(_U_ROW_COUNTS):
        failures.append("the three continuous U-architrave runs are incomplete")
    if direct_shaft_contacts:
        failures.append(f"{direct_shaft_contacts} lower/upper shafts still touch directly")
    if stack_failures:
        failures.append(f"{stack_failures} lower-capital-support-upper chains are invalid")

    scoped_inner = [
        item
        for item in scoped
        if item.get("component_id") == "interior-colonnade"
    ]
    bounded_inner: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
    pair_bound_failures = 0
    for operation in scoped_inner:
        try:
            bounded_inner[str(operation["operation_id"])] = _inner_operation_bounds(operation)
        except (KeyError, TypeError, ValueError) as exc:
            pair_bound_failures += 1
            details["inner_pairs"].append(
                {"operation_id": operation.get("operation_id"), "reason": str(exc)}
            )
    positive_overlap_count = 0
    pair_denominator = 0
    bounded_ids = sorted(bounded_inner)
    for first_index, first_id in enumerate(bounded_ids):
        first_min, first_max = bounded_inner[first_id]
        for second_id in bounded_ids[first_index + 1 :]:
            pair_denominator += 1
            second_min, second_max = bounded_inner[second_id]
            overlaps = tuple(
                min(first_max[axis], second_max[axis])
                - max(first_min[axis], second_min[axis])
                for axis in range(3)
            )
            if all(item > _TOLERANCE for item in overlaps):
                positive_overlap_count += 1
                details["inner_pairs"].append(
                    {
                        "first_operation_id": first_id,
                        "second_operation_id": second_id,
                        "positive_overlap_m3": math.prod(overlaps),
                    }
                )
    contact_reference_failures = 0
    for operation in scoped_inner:
        contact = operation.get("contact_contract")
        contact_entries = (
            [contact]
            if isinstance(contact, Mapping)
            else list(contact)
            if isinstance(contact, Sequence)
            and not isinstance(contact, (str, bytes))
            else []
        )
        referenced: list[str] = []
        for entry in contact_entries:
            if not isinstance(entry, Mapping):
                continue
            for key in (
                "first_operation_id",
                "second_operation_id",
                "lower_operation_id",
                "upper_operation_id",
            ):
                if entry.get(key):
                    referenced.append(str(entry[key]))
            for key in ("lower_operation_ids", "upper_operation_ids"):
                values = entry.get(key, ())
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                    referenced.extend(str(item) for item in values)
        missing_refs = sorted({item for item in referenced if item not in by_id})
        if missing_refs:
            contact_reference_failures += 1
            details["inner_pairs"].append(
                {
                    "operation_id": operation["operation_id"],
                    "missing_contact_operation_ids": missing_refs,
                }
            )
    if pair_bound_failures:
        failures.append(f"{pair_bound_failures} scoped inner members lack pair bounds")
    if positive_overlap_count:
        failures.append(
            f"{positive_overlap_count} scoped inner member pairs have positive-volume overlap"
        )
    if contact_reference_failures:
        failures.append(
            f"{contact_reference_failures} scoped inner members have dangling typed contacts"
        )

    ionic = {
        str(item.get("operation_id")): item
        for item in operations
        if item.get("component_id") == "interior-colonnade"
        and item.get("kind") == "ionic_column"
    }
    ionic_failures = 0
    if set(ionic) != set(_IONIC_EXPECTED):
        ionic_failures += len(set(ionic) ^ set(_IONIC_EXPECTED)) or 1
    for operation_id, expected in _IONIC_EXPECTED.items():
        operation = ionic.get(operation_id)
        if not operation or operation.get("parameters") != expected:
            ionic_failures += 1
            continue
        if operation.get("refinement_scope") == REFINEMENT_SCOPE:
            ionic_failures += 1
    if ionic_failures:
        failures.append("west-room Ionic axes or parked-detail boundary changed")

    window_groups = _window_groups(operations)
    window_failures = 0
    window_obstructions = 0
    derived_center_abs: float | None = None
    try:
        negative_wall, positive_wall = _wall_inner_faces(by_id)
        negative_column, positive_column = _column_outer_envelopes(shafts)
        derived_center_abs = (positive_column + positive_wall) / 2.0
        for side, sign in (("left", -1.0), ("right", 1.0)):
            pieces = window_groups.get(side, [])
            if len(pieces) != 4:
                window_failures += 1
                continue
            clears = [item["parameters"].get("window_clear") for item in pieces]
            if any(not isinstance(item, Mapping) for item in clears):
                window_failures += 1
                continue
            centers = {round(float(item["center_x"]), 9) for item in clears}
            widths = {round(float(item["width"]), 9) for item in clears}
            sills = {round(float(item["sill_z"]), 9) for item in clears}
            heights = {round(float(item["height"]), 9) for item in clears}
            if len(centers) != 1 or len(widths) != 1 or len(sills) != 1 or len(heights) != 1:
                window_failures += 1
                continue
            center = next(iter(centers))
            width = next(iter(widths))
            sill = next(iter(sills))
            height = next(iter(heights))
            if not _close(center, sign * derived_center_abs):
                window_failures += 1
            opening_x = (center - width / 2.0, center + width / 2.0)
            if side == "left":
                obstructed = opening_x[0] < negative_column < opening_x[1]
            else:
                obstructed = opening_x[0] < positive_column < opening_x[1]
            if obstructed:
                window_obstructions += 1
            aperture_y_min = min(_box_bounds(item)[0][1] for item in pieces)
            aperture_y_max = max(_box_bounds(item)[1][1] for item in pieces)
            aperture = (
                opening_x,
                (aperture_y_min, aperture_y_max),
                (sill, sill + height),
            )
            labels: set[str] = set()
            for piece in pieces:
                params = piece["parameters"]
                label = str(params.get("window_piece_role", ""))
                labels.add(label)
                void_contract = params.get("void_contract")
                if (
                    not isinstance(void_contract, Mapping)
                    or void_contract.get("realization")
                    != "decomposed-host-wall-void-not-surface-patch"
                    or void_contract.get("opening_is_empty_volume") is not True
                    or piece.get("host", {}).get("operation_id") != f"cella-wall-east-{side}"
                ):
                    window_failures += 1
                piece_min, piece_max = _box_bounds(piece)
                overlap_volume = math.prod(
                    _interval_overlap(
                        (piece_min[axis], piece_max[axis]),
                        aperture[axis],
                    )
                    for axis in range(3)
                )
                if overlap_volume > _TOLERANCE:
                    window_failures += 1
                    details["windows"].append(
                        {
                            "operation_id": piece["operation_id"],
                            "aperture_overlap_m3": overlap_volume,
                        }
                    )
            if labels != _WINDOW_PIECE_LABELS:
                window_failures += 1
        left_centers = {
            round(float(item["parameters"]["window_clear"]["center_x"]), 9)
            for item in window_groups.get("left", [])
        }
        right_centers = {
            round(float(item["parameters"]["window_clear"]["center_x"]), 9)
            for item in window_groups.get("right", [])
        }
        if len(left_centers) != 1 or len(right_centers) != 1 or not _close(
            abs(next(iter(left_centers))), next(iter(right_centers))
        ):
            window_failures += 1
    except (KeyError, TypeError, ValueError, InnerColonnadeError):
        window_failures += 1
    if window_obstructions:
        failures.append(f"{window_obstructions} east windows still project through inner shafts")
    if window_failures:
        failures.append(f"{window_failures} east-window host/void/derivation checks failed")

    checks = {
        "operation_identity": {
            "operation_denominator": len(full_operations),
            "unique_operation_count": len(set(ids)),
            "duplicate_operation_ids": duplicate_ids,
        },
        "scoped_lineage_evidence": {
            "scoped_operation_count": len(scoped),
            "lineage_failure_count": lineage_failures,
            "evidence_failure_count": evidence_failures,
            "host_contact_material_failure_count": host_failures,
        },
        "geometric_twenty_flute_shafts": {
            "shaft_denominator": len(shafts),
            "valid_geometric_flute_count": len(shafts) - flute_failures,
            "flutes_per_shaft": FLUTE_COUNT,
            "radial_segments_per_shaft": RADIAL_SEGMENTS,
            "metadata_only_allowed": False,
        },
        "inherited_schematic_diagnostic": {
            "legacy_naos_shaft_count": len(legacy_shafts),
            "legacy_direct_shaft_contact_count": legacy_direct_contacts,
            "legacy_window_projection_obstruction_count": legacy_window_obstructions,
        },
        "z_up_long_axis": {
            "shaft_denominator": len(shafts),
            "valid_z_axis_count": len(shafts) - axis_failures,
        },
        "u_colonnade_topology": {
            "stack_denominator": len(shafts_by_stack),
            "valid_stack_count": valid_stacks,
            "row_counts": dict(sorted(row_counts.items())),
            "expected_row_counts": dict(_U_ROW_COUNTS),
            "direct_shaft_contact_count": direct_shaft_contacts,
            "intertier_architrave_count": len(architraves),
        },
        "scoped_inner_pair_relations": {
            "bounded_operation_denominator": len(bounded_inner),
            "pair_denominator": pair_denominator,
            "positive_volume_overlap_count": positive_overlap_count,
            "bound_failure_count": pair_bound_failures,
            "dangling_typed_contact_count": contact_reference_failures,
            "positive_volume_overlap_allowlist": [],
        },
        "east_window_relation": {
            "window_denominator": len(window_groups),
            "window_piece_denominator": sum(len(items) for items in window_groups.values()),
            "derived_center_magnitude_m": derived_center_abs,
            "projection_obstruction_count": window_obstructions,
            "host_void_derivation_failure_count": window_failures,
            "method": "side-aisle-midpoint-between-column-envelope-and-side-wall-inner-face",
            "metric_authority": False,
        },
        "west_room_ionic_boundary": {
            "ionic_axis_denominator": len(ionic),
            "expected_ionic_axis_count": 4,
            "failure_count": ionic_failures,
            "fine_detail_disposition": "PARKED_UNCHANGED",
        },
        "branch_exclusion": {
            "branch_id": BRANCH_ID,
            "leakage_count": branch_failures,
        },
    }
    return {
        "schema": VALIDATION_SCHEMA,
        "passed": not failures,
        "status": "PASSED" if not failures else "FAILED",
        "checks": checks,
        "failure_count": len(failures),
        "failures": failures,
        "failure_details": {key: value[:50] for key, value in sorted(details.items())},
    }


__all__ = [
    "BRANCH_ID",
    "DELTA_SCHEMA",
    "InnerColonnadeError",
    "VALIDATION_SCHEMA",
    "compile_inner_colonnade_delta",
    "validate_inner_colonnade_operations",
]
