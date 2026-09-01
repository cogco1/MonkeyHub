"""Evidence-bound roof, eaves, and pediment delta for Parthenon Stage 4.

This module is intentionally side-effect free.  It compiles a typed proxy
surface/support delta from the already inherited Stage 4 operations and
validates that delta at the IR boundary.  It does not select project paths,
write a 3DM, or claim exact tile/member dimensions that the retained evidence
does not establish.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


SCOPE = "parthenon-stage4-roof-eaves-pediment"

COARSE_HOST_IDS = (
    "main-gabled-roof",
    "pediment-east",
    "pediment-west",
    "entablature-east-cornice",
    "entablature-north-cornice",
    "entablature-south-cornice",
    "entablature-west-cornice",
)

ROOT_SUPPORT_IDS = (
    "entablature-east-frieze",
    "entablature-north-frieze",
    "entablature-south-frieze",
    "entablature-west-frieze",
)

MATERIAL_BY_ROLE = {
    "structural_marble": "pentelic-marble-structural",
    "roof_tile": "pentelic-marble-roof-tile",
    "timber_frame": "roof-timber",
}

TIMBER_KINDS = {
    "timber_bearing_beam",
    "timber_rafter_field",
    "timber_ridge_beam",
}
ROOF_TILE_KINDS = {
    "marble_cover_tile_field",
    "marble_eave_terminal",
    "marble_pan_tile_field",
    "marble_ridge_terminal",
}
STRUCTURAL_MARBLE_KINDS = {
    "acroterion_seat",
    "eave_geison",
    "eave_sima",
    "pediment_horizontal_geison",
    "pediment_raking_geison",
    "pediment_raking_sima",
    "pediment_tympanum",
}

BANNED_ASSET_TOKENS = (
    "ut-austin-east-pediment-cast",
    "zenodo-south-metope-iii",
    "zenodo-parthenon-photogrammetry",
    "sketchfab-doric-column",
)
BANNED_SCULPTURE_TOKENS = (
    "figure",
    "figurative",
    "sculpture",
    "statue",
    "zeus",
    "athena",
    "pediment-cast",
    "relief-figure",
)


def _expected_host_specs() -> dict[str, tuple[str, tuple[str, ...], str, str]]:
    specs: dict[str, tuple[str, tuple[str, ...], str, str]] = {
        "eave-north-geison": (
            "entablature-north-frieze",
            (),
            "rests_on",
            "surface",
        ),
        "eave-north-sima": ("eave-north-geison", (), "rests_on", "surface"),
        "eave-south-geison": (
            "entablature-south-frieze",
            (),
            "rests_on",
            "surface",
        ),
        "eave-south-sima": ("eave-south-geison", (), "rests_on", "surface"),
        "pediment-east-horizontal-geison": (
            "entablature-east-frieze",
            (),
            "rests_on",
            "surface",
        ),
        "pediment-west-horizontal-geison": (
            "entablature-west-frieze",
            (),
            "rests_on",
            "surface",
        ),
        "roof-timber-bearing-north": (
            "eave-north-geison",
            (),
            "bears_on",
            "surface",
        ),
        "roof-timber-bearing-south": (
            "eave-south-geison",
            (),
            "bears_on",
            "surface",
        ),
        "roof-timber-ridge": (
            "roof-timber-bearing-north",
            ("roof-timber-bearing-south",),
            "spans_between",
            "surface",
        ),
        "roof-rafter-field-north": (
            "roof-timber-bearing-north",
            ("roof-timber-ridge",),
            "spans_between",
            "surface",
        ),
        "roof-rafter-field-south": (
            "roof-timber-bearing-south",
            ("roof-timber-ridge",),
            "spans_between",
            "surface",
        ),
        "roof-pan-tile-field-north": (
            "roof-rafter-field-north",
            (),
            "rests_on",
            "surface",
        ),
        "roof-pan-tile-field-south": (
            "roof-rafter-field-south",
            (),
            "rests_on",
            "surface",
        ),
        "roof-cover-tile-field-north": (
            "roof-pan-tile-field-north",
            (),
            "covers",
            "surface",
        ),
        "roof-cover-tile-field-south": (
            "roof-pan-tile-field-south",
            (),
            "covers",
            "surface",
        ),
        "roof-ridge-terminal": (
            "roof-cover-tile-field-north",
            ("roof-cover-tile-field-south",),
            "closes_joint",
            "edge",
        ),
        "roof-eave-terminal-north": (
            "roof-cover-tile-field-north",
            ("eave-north-sima",),
            "terminates_at",
            "edge",
        ),
        "roof-eave-terminal-south": (
            "roof-cover-tile-field-south",
            ("eave-south-sima",),
            "terminates_at",
            "edge",
        ),
    }
    for side in ("east", "west"):
        specs[f"pediment-{side}-tympanum"] = (
            f"pediment-{side}-horizontal-geison",
            (),
            "rests_on",
            "surface",
        )
        for hand in ("left", "right"):
            specs[f"pediment-{side}-raking-geison-{hand}"] = (
                f"pediment-{side}-tympanum",
                (),
                "frames",
                "edge",
            )
            specs[f"pediment-{side}-raking-sima-{hand}"] = (
                f"pediment-{side}-raking-geison-{hand}",
                (),
                "rests_on",
                "surface",
            )
        specs[f"pediment-{side}-acroterion-seat-left"] = (
            f"pediment-{side}-raking-sima-left",
            (),
            "rests_on",
            "surface",
        )
        specs[f"pediment-{side}-acroterion-seat-right"] = (
            f"pediment-{side}-raking-sima-right",
            (),
            "rests_on",
            "surface",
        )
        specs[f"pediment-{side}-acroterion-seat-apex"] = (
            f"pediment-{side}-raking-sima-left",
            (f"pediment-{side}-raking-sima-right",),
            "closes_joint",
            "edge",
        )
    return specs


EXPECTED_HOST_SPECS = _expected_host_specs()
REQUIRED_DELTA_IDS = tuple(sorted(EXPECTED_HOST_SPECS))

REPLACEMENT_TARGETS = {
    "eave-north-geison": "entablature-north-cornice",
    "eave-south-geison": "entablature-south-cornice",
    "pediment-east-horizontal-geison": "entablature-east-cornice",
    "pediment-west-horizontal-geison": "entablature-west-cornice",
    "pediment-east-tympanum": "pediment-east",
    "pediment-west-tympanum": "pediment-west",
    "roof-pan-tile-field-north": "main-gabled-roof",
}


class ParthenonStage4RoofError(ValueError):
    """The isolated roof/eaves/pediment IR contract failed closed."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _operation_fingerprint(operation: Mapping[str, object]) -> str:
    return _digest(dict(operation))


def _normalise_refs(
    refs: Sequence[str] | Mapping[str, Sequence[str] | str],
    *,
    label: str,
) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(refs, Mapping):
        candidates: Sequence[Sequence[str] | str] = tuple(refs.values())
    elif isinstance(refs, str):
        candidates = (refs,)
    else:
        candidates = refs
    for candidate in candidates:
        if isinstance(candidate, str):
            values.append(candidate)
        else:
            values.extend(str(item) for item in candidate)
    result = tuple(sorted({item.strip() for item in values if item.strip()}))
    if not result:
        raise ParthenonStage4RoofError(f"{label} must contain at least one evidence ref")
    leaked = [
        ref
        for ref in result
        if any(token in ref.lower() for token in BANNED_ASSET_TOKENS)
    ]
    if leaked:
        raise ParthenonStage4RoofError(f"{label} contains a PARKED asset ref")
    return result


def _operation_index(
    operations: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for operation in operations:
        operation_id = str(operation.get("operation_id", ""))
        if not operation_id:
            raise ParthenonStage4RoofError("operation lacks operation_id")
        if operation_id in result:
            raise ParthenonStage4RoofError(f"duplicate operation_id: {operation_id}")
        result[operation_id] = operation
    return result


def _finite_float(value: object, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ParthenonStage4RoofError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ParthenonStage4RoofError(f"{label} must be finite")
    return number


def _positive_float(value: object, *, label: str) -> float:
    number = _finite_float(value, label=label)
    if number <= 0.0:
        raise ParthenonStage4RoofError(f"{label} must be positive")
    return number


def _vector3(value: object, *, label: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ParthenonStage4RoofError(f"{label} must be a three-number vector")
    return [_finite_float(item, label=label) for item in value]


def _scaled_range(value: float, low_ratio: float, high_ratio: float) -> list[float]:
    low = round(value * low_ratio, 9)
    high = round(value * high_ratio, 9)
    if not 0.0 < low < high:
        raise ParthenonStage4RoofError("invalid derived SOFT parameter range")
    return [low, high]


def _soft_basis(
    *,
    statement: str,
    ranges: Mapping[str, Sequence[float]],
    hard_invariants: Sequence[str],
) -> dict[str, object]:
    return {
        "classification": "SOFT",
        "metric_authority": False,
        "pixel_measurement_authority": False,
        "statement": statement,
        "hard_invariants": list(hard_invariants),
        "soft_parameter_ranges": {
            key: [float(value[0]), float(value[1])]
            for key, value in sorted(ranges.items())
        },
    }


def _contact_policy(
    *,
    relation: str,
    co_host_ids: Sequence[str],
    contact_dimension: str,
) -> dict[str, object]:
    return {
        "relation": relation,
        "required": True,
        "co_host_ids": list(co_host_ids),
        "contact_dimension": contact_dimension,
        "collision_policy": "touch_only_no_volume",
    }


def _new_operation(
    *,
    operation_id: str,
    component_id: str,
    semantic_role: str,
    kind: str,
    material_role: str,
    host_id: str,
    co_host_ids: Sequence[str],
    relation: str,
    contact_dimension: str,
    parameters: Mapping[str, object],
    basis: Mapping[str, object],
    textual_refs: Sequence[str],
    visual_refs: Sequence[str],
    lineage_target: str,
    replaces: bool,
) -> dict[str, object]:
    operation: dict[str, object] = {
        "operation_id": operation_id,
        "component_id": component_id,
        "semantic_role": semantic_role,
        "kind": kind,
        "material_id": MATERIAL_BY_ROLE[material_role],
        "material_role": material_role,
        "host_id": host_id,
        "contact_policy": _contact_policy(
            relation=relation,
            co_host_ids=co_host_ids,
            contact_dimension=contact_dimension,
        ),
        "parameters": {
            **copy.deepcopy(dict(parameters)),
            "occupancy_key": operation_id,
        },
        "parameter_basis": copy.deepcopy(dict(basis)),
        "source_refs": list(textual_refs),
        "visual_region_refs": list(visual_refs),
        "decision_refs": [
            "decision:roof-system"
            if component_id == "roof"
            else "decision:entablature-pediment"
        ],
        "delta_scope": SCOPE,
        "canonical_write_authority": False,
    }
    operation[
        "replaces_operation_id" if replaces else "refines_operation_id"
    ] = lineage_target
    return operation


def _roof_parameters(operation: Mapping[str, object]) -> dict[str, float]:
    if operation.get("kind") != "roof_prism":
        raise ParthenonStage4RoofError("main-gabled-roof must be the inherited roof_prism")
    parameters = operation.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ParthenonStage4RoofError("main-gabled-roof lacks parameters")
    result = {
        "width": _positive_float(parameters.get("width"), label="roof width"),
        "length": _positive_float(parameters.get("length"), label="roof length"),
        "eave_z": _finite_float(parameters.get("eave_z"), label="roof eave_z"),
        "ridge_z": _finite_float(parameters.get("ridge_z"), label="roof ridge_z"),
        "y_center": _finite_float(parameters.get("y_center"), label="roof y_center"),
    }
    if result["ridge_z"] <= result["eave_z"]:
        raise ParthenonStage4RoofError("roof ridge must be above its eaves")
    return result


def _pediment_parameters(operation: Mapping[str, object], *, side: str) -> dict[str, float]:
    if operation.get("kind") != "gable_panel":
        raise ParthenonStage4RoofError(f"pediment-{side} must be the inherited gable_panel")
    parameters = operation.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ParthenonStage4RoofError(f"pediment-{side} lacks parameters")
    result = {
        "width": _positive_float(parameters.get("width"), label=f"{side} pediment width"),
        "depth": _positive_float(parameters.get("depth"), label=f"{side} pediment depth"),
        "eave_z": _finite_float(parameters.get("eave_z"), label=f"{side} pediment eave_z"),
        "ridge_z": _finite_float(parameters.get("ridge_z"), label=f"{side} pediment ridge_z"),
        "y_center": _finite_float(parameters.get("y_center"), label=f"{side} pediment y_center"),
    }
    if result["ridge_z"] <= result["eave_z"]:
        raise ParthenonStage4RoofError(f"pediment-{side} ridge must be above its eave")
    return result


def _cornice_parameters(operation: Mapping[str, object], *, side: str) -> dict[str, object]:
    parameters = operation.get("parameters")
    if operation.get("kind") != "entablature_layer" or not isinstance(parameters, Mapping):
        raise ParthenonStage4RoofError(f"entablature-{side}-cornice is not an inherited layer")
    if str(parameters.get("layer")) != "cornice" or str(parameters.get("side")) != side:
        raise ParthenonStage4RoofError(f"entablature-{side}-cornice identity drifted")
    origin = _vector3(parameters.get("origin"), label=f"{side} cornice origin")
    size = _vector3(parameters.get("size"), label=f"{side} cornice size")
    if any(value <= 0.0 for value in size):
        raise ParthenonStage4RoofError(f"entablature-{side}-cornice size must be positive")
    return {"origin": origin, "size": size, "side": side, "layer": "cornice"}


def compile_roof_eaves_pediment_delta(
    current_operations: Sequence[Mapping[str, object]],
    *,
    textual_evidence_refs: Sequence[str] | Mapping[str, Sequence[str] | str],
    selected_visual_refs: Sequence[str] | Mapping[str, Sequence[str] | str],
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Replace the seven coarse hosts and return ``(full_operations, lineage)``.

    The generated roof members are proxy surfaces and typed support fields.
    Every metric range introduced by this compiler remains explicitly SOFT;
    inherited roof/pediment extents are carried as HARD invariants rather than
    re-measured from visual pixels.
    """

    textual_refs = _normalise_refs(textual_evidence_refs, label="textual_evidence_refs")
    visual_refs = _normalise_refs(selected_visual_refs, label="selected_visual_refs")
    current_by_id = _operation_index(current_operations)
    missing = sorted(set(COARSE_HOST_IDS + ROOT_SUPPORT_IDS).difference(current_by_id))
    if missing:
        raise ParthenonStage4RoofError(
            "current operations lack required inherited hosts: " + ", ".join(missing)
        )

    roof = _roof_parameters(current_by_id["main-gabled-roof"])
    pediments = {
        side: _pediment_parameters(current_by_id[f"pediment-{side}"], side=side)
        for side in ("east", "west")
    }
    cornices = {
        side: _cornice_parameters(
            current_by_id[f"entablature-{side}-cornice"],
            side=side,
        )
        for side in ("east", "north", "south", "west")
    }

    roof_rise = roof["ridge_z"] - roof["eave_z"]
    roof_ranges = {
        "rafter_spacing_m": _scaled_range(roof["length"], 0.01, 0.05),
        "tile_module_length_m": _scaled_range(roof["length"], 0.006, 0.02),
        "tile_module_width_m": _scaled_range(roof["width"], 0.006, 0.03),
        "tile_thickness_m": _scaled_range(roof_rise, 0.003, 0.02),
        "timber_section_depth_m": _scaled_range(roof_rise, 0.02, 0.12),
        "timber_section_width_m": _scaled_range(roof["width"], 0.003, 0.02),
    }
    roof_basis = _soft_basis(
        statement=(
            "SOFT procedural support/tile topology inside the inherited gabled envelope; "
            "no exact tile module, timber section, or spacing is asserted"
        ),
        ranges=roof_ranges,
        hard_invariants=(
            "inherited gabled roof envelope",
            "text evidence: marble tile covering",
            "text evidence: timber beam frame",
            "no visual-pixel metric inference",
        ),
    )

    delta: list[dict[str, object]] = []

    def add(
        operation_id: str,
        *,
        component_id: str,
        semantic_role: str,
        kind: str,
        material_role: str,
        parameters: Mapping[str, object],
        basis: Mapping[str, object],
        lineage_target: str,
    ) -> None:
        host_id, co_hosts, relation, dimension = EXPECTED_HOST_SPECS[operation_id]
        delta.append(
            _new_operation(
                operation_id=operation_id,
                component_id=component_id,
                semantic_role=semantic_role,
                kind=kind,
                material_role=material_role,
                host_id=host_id,
                co_host_ids=co_hosts,
                relation=relation,
                contact_dimension=dimension,
                parameters=parameters,
                basis=basis,
                textual_refs=textual_refs,
                visual_refs=visual_refs,
                lineage_target=lineage_target,
                replaces=operation_id in REPLACEMENT_TARGETS,
            )
        )

    for side in ("north", "south"):
        cornice = cornices[side]
        height = float(cornice["size"][2])
        depth = min(float(cornice["size"][0]), float(cornice["size"][1]))
        eave_basis = _soft_basis(
            statement=(
                "SOFT geison/sima articulation constrained to the inherited cornice host; "
                "profile dimensions remain unresolved"
            ),
            ranges={
                "profile_height_m": _scaled_range(height, 0.05, 0.25),
                "profile_projection_m": _scaled_range(depth, 0.05, 0.30),
            },
            hard_invariants=(
                f"inherited entablature-{side}-cornice envelope",
                "Doric cornice/member identity",
                "no visual-pixel metric inference",
            ),
        )
        add(
            f"eave-{side}-geison",
            component_id="entablature",
            semantic_role="eaves",
            kind="eave_geison",
            material_role="structural_marble",
            parameters={"side": side, "inherited_host_envelope": cornice},
            basis=eave_basis,
            lineage_target=f"entablature-{side}-cornice",
        )
        add(
            f"eave-{side}-sima",
            component_id="entablature",
            semantic_role="eaves",
            kind="eave_sima",
            material_role="structural_marble",
            parameters={"side": side, "inherited_host_envelope": cornice},
            basis=eave_basis,
            lineage_target=f"entablature-{side}-cornice",
        )

    for operation_id, kind, material_role, topology in (
        ("roof-timber-bearing-north", "timber_bearing_beam", "timber_frame", "north_eave_bearing"),
        ("roof-timber-bearing-south", "timber_bearing_beam", "timber_frame", "south_eave_bearing"),
        ("roof-timber-ridge", "timber_ridge_beam", "timber_frame", "ridge_spine"),
        ("roof-rafter-field-north", "timber_rafter_field", "timber_frame", "north_slope_support"),
        ("roof-rafter-field-south", "timber_rafter_field", "timber_frame", "south_slope_support"),
        ("roof-pan-tile-field-north", "marble_pan_tile_field", "roof_tile", "north_slope_pan_field"),
        ("roof-pan-tile-field-south", "marble_pan_tile_field", "roof_tile", "south_slope_pan_field"),
        ("roof-cover-tile-field-north", "marble_cover_tile_field", "roof_tile", "north_slope_cover_field"),
        ("roof-cover-tile-field-south", "marble_cover_tile_field", "roof_tile", "south_slope_cover_field"),
        ("roof-ridge-terminal", "marble_ridge_terminal", "roof_tile", "ridge_closure"),
        ("roof-eave-terminal-north", "marble_eave_terminal", "roof_tile", "north_eave_closure"),
        ("roof-eave-terminal-south", "marble_eave_terminal", "roof_tile", "south_eave_closure"),
    ):
        add(
            operation_id,
            component_id="roof",
            semantic_role="roof_system",
            kind=kind,
            material_role=material_role,
            parameters={
                "topology": topology,
                "inherited_roof_envelope": roof,
                "exact_member_count": None,
                "exact_module_m": None,
                "fit_policy": "inside_inherited_roof_envelope",
            },
            basis=roof_basis,
            lineage_target="main-gabled-roof",
        )

    for side in ("east", "west"):
        pediment = pediments[side]
        cornice = cornices[side]
        rise = pediment["ridge_z"] - pediment["eave_z"]
        pediment_basis = _soft_basis(
            statement=(
                "SOFT blank architectural pediment frame constrained to the inherited "
                "pediment/cornice envelopes; sculptural bodies remain PARKED"
            ),
            ranges={
                "acroterion_seat_width_m": _scaled_range(pediment["width"], 0.005, 0.03),
                "frame_profile_depth_m": _scaled_range(pediment["depth"], 0.25, 1.0),
                "frame_profile_height_m": _scaled_range(rise, 0.02, 0.12),
            },
            hard_invariants=(
                f"inherited pediment-{side} envelope",
                f"inherited entablature-{side}-cornice envelope",
                "blank tympanum and attachment seats only",
                "no visual-pixel metric inference",
            ),
        )
        add(
            f"pediment-{side}-horizontal-geison",
            component_id="pediments",
            semantic_role="pediment_frame",
            kind="pediment_horizontal_geison",
            material_role="structural_marble",
            parameters={"side": side, "inherited_host_envelope": cornice},
            basis=pediment_basis,
            lineage_target=f"entablature-{side}-cornice",
        )
        add(
            f"pediment-{side}-tympanum",
            component_id="pediments",
            semantic_role="blank_tympanum",
            kind="pediment_tympanum",
            material_role="structural_marble",
            parameters={
                "side": side,
                "inherited_pediment_envelope": pediment,
                "surface_policy": "blank_attachment_zone_no_sculptural_body",
            },
            basis=pediment_basis,
            lineage_target=f"pediment-{side}",
        )
        for hand in ("left", "right"):
            add(
                f"pediment-{side}-raking-geison-{hand}",
                component_id="pediments",
                semantic_role="pediment_frame",
                kind="pediment_raking_geison",
                material_role="structural_marble",
                parameters={
                    "side": side,
                    "hand": hand,
                    "inherited_pediment_envelope": pediment,
                },
                basis=pediment_basis,
                lineage_target=f"pediment-{side}",
            )
            add(
                f"pediment-{side}-raking-sima-{hand}",
                component_id="pediments",
                semantic_role="pediment_frame",
                kind="pediment_raking_sima",
                material_role="structural_marble",
                parameters={
                    "side": side,
                    "hand": hand,
                    "inherited_pediment_envelope": pediment,
                },
                basis=pediment_basis,
                lineage_target=f"pediment-{side}",
            )
        for position in ("left", "apex", "right"):
            add(
                f"pediment-{side}-acroterion-seat-{position}",
                component_id="pediments",
                semantic_role="architectural_attachment_seat",
                kind="acroterion_seat",
                material_role="structural_marble",
                parameters={
                    "side": side,
                    "position": position,
                    "ornament_body_policy": "PARKED_NO_GEOMETRY",
                    "inherited_pediment_envelope": pediment,
                },
                basis=pediment_basis,
                lineage_target=f"pediment-{side}",
            )

    delta_by_id = _operation_index(delta)
    if set(delta_by_id) != set(REQUIRED_DELTA_IDS):
        raise ParthenonStage4RoofError("compiler generated an incomplete roof delta")

    preserved = {
        operation_id: _operation_fingerprint(operation)
        for operation_id, operation in sorted(current_by_id.items())
        if operation_id not in COARSE_HOST_IDS
    }
    full_operations = tuple(
        sorted(
            (
                *(
                    copy.deepcopy(dict(operation))
                    for operation_id, operation in current_by_id.items()
                    if operation_id not in COARSE_HOST_IDS
                ),
                *(copy.deepcopy(operation) for operation in delta),
            ),
            key=lambda item: str(item["operation_id"]),
        )
    )
    lineage: dict[str, object] = {
        "schema": "ParthenonStage4RoofEavesPedimentDelta@1",
        "scope": SCOPE,
        "input_operation_count": len(current_operations),
        "successor_operation_count": len(full_operations),
        "delta_operation_count": len(delta),
        "replaced_operation_ids": list(COARSE_HOST_IDS),
        "replacement_map": {
            target: sorted(
                operation_id
                for operation_id, operation in delta_by_id.items()
                if operation.get("replaces_operation_id") == target
                or operation.get("refines_operation_id") == target
            )
            for target in COARSE_HOST_IDS
        },
        "delta_operation_ids": list(REQUIRED_DELTA_IDS),
        "preserved_operation_count": len(preserved),
        "preserved_operation_fingerprints": preserved,
        "parameter_policy": "SOFT_TOPOLOGY_NO_PIXEL_METRICS",
        "sculpture_policy": "PARKED_NO_GEOMETRY",
        "textual_evidence_refs": list(textual_refs),
        "selected_visual_refs": list(visual_refs),
        "canonical_write_authority": False,
    }
    lineage["receipt_digest"] = _digest(lineage)
    return full_operations, lineage


def _material_role_for_kind(kind: str) -> tuple[str, str] | None:
    if kind in TIMBER_KINDS:
        return "timber_frame", MATERIAL_BY_ROLE["timber_frame"]
    if kind in ROOF_TILE_KINDS:
        return "roof_tile", MATERIAL_BY_ROLE["roof_tile"]
    if kind in STRUCTURAL_MARBLE_KINDS:
        return "structural_marble", MATERIAL_BY_ROLE["structural_marble"]
    return None


def _soft_ranges_valid(basis: Mapping[str, object]) -> bool:
    ranges = basis.get("soft_parameter_ranges")
    if not isinstance(ranges, Mapping) or not ranges:
        return False
    for value in ranges.values():
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
            return False
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return False
        if not math.isfinite(low) or not math.isfinite(high) or not 0.0 < low < high:
            return False
    return True


def _host_graph_has_cycle(delta_by_id: Mapping[str, Mapping[str, object]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(operation_id: str) -> bool:
        if operation_id in visiting:
            return True
        if operation_id in visited:
            return False
        visiting.add(operation_id)
        operation = delta_by_id[operation_id]
        hosts = [str(operation.get("host_id", ""))]
        policy = operation.get("contact_policy")
        if isinstance(policy, Mapping):
            co_hosts = policy.get("co_host_ids", ())
            if isinstance(co_hosts, Sequence) and not isinstance(co_hosts, (str, bytes)):
                hosts.extend(str(item) for item in co_hosts)
        for host_id in hosts:
            if host_id in delta_by_id and visit(host_id):
                return True
        visiting.remove(operation_id)
        visited.add(operation_id)
        return False

    return any(visit(operation_id) for operation_id in delta_by_id)


def validate_roof_eaves_pediment_operations(
    full_operations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Return a deterministic fail-closed IR validation receipt."""

    failures: list[str] = []
    try:
        by_id = _operation_index(full_operations)
    except ParthenonStage4RoofError as exc:
        return {
            "schema": "ParthenonStage4RoofEavesPedimentValidation@1",
            "passed": False,
            "status": "FAILED",
            "checks": {},
            "failures": [str(exc)],
            "canonical_write_authority": False,
        }

    coarse_ids_present = sorted(set(COARSE_HOST_IDS).intersection(by_id))
    if coarse_ids_present:
        failures.append("coarse roof/pediment/cornice hosts remain: " + ", ".join(coarse_ids_present))

    coarse_kinds_present = sorted(
        operation_id
        for operation_id, operation in by_id.items()
        if (
            operation.get("kind") == "roof_prism"
            and operation.get("component_id") == "roof"
        )
        or (
            operation.get("kind") == "gable_panel"
            and operation.get("component_id") == "pediments"
        )
        or (
            operation.get("kind") == "entablature_layer"
            and isinstance(operation.get("parameters"), Mapping)
            and operation["parameters"].get("layer") == "cornice"
            and operation["parameters"].get("side") in {"east", "north", "south", "west"}
        )
    )
    if coarse_kinds_present:
        failures.append("coarse 6v/5f proxy kinds remain under renamed ids")

    missing_delta_ids = sorted(set(REQUIRED_DELTA_IDS).difference(by_id))
    if missing_delta_ids:
        failures.append("required roof delta operations are missing: " + ", ".join(missing_delta_ids))
    missing_roots = sorted(set(ROOT_SUPPORT_IDS).difference(by_id))
    if missing_roots:
        failures.append("root entablature support hosts are missing: " + ", ".join(missing_roots))

    delta_by_id = {
        operation_id: operation
        for operation_id, operation in by_id.items()
        if operation.get("delta_scope") == SCOPE
    }
    unexpected_delta_ids = sorted(set(delta_by_id).difference(REQUIRED_DELTA_IDS))
    if unexpected_delta_ids:
        failures.append("unexpected operations entered the isolated roof delta")
    if set(delta_by_id) != set(REQUIRED_DELTA_IDS):
        failures.append("isolated roof delta identity is incomplete")

    occupancy_keys: list[str] = []
    evidence_complete = True
    lineage_complete = True
    material_roles_valid = True
    contact_policies_valid = True
    host_chain_valid = True
    soft_parameter_policy_valid = True
    sculpture_leakage: list[str] = []

    for operation_id, operation in sorted(delta_by_id.items()):
        kind = str(operation.get("kind", ""))
        expected_material = _material_role_for_kind(kind)
        if expected_material is None or (
            operation.get("material_role"), operation.get("material_id")
        ) != expected_material:
            material_roles_valid = False

        source_refs = operation.get("source_refs")
        visual_refs = operation.get("visual_region_refs")
        if (
            not isinstance(source_refs, Sequence)
            or isinstance(source_refs, (str, bytes))
            or not source_refs
            or not isinstance(visual_refs, Sequence)
            or isinstance(visual_refs, (str, bytes))
            or not visual_refs
        ):
            evidence_complete = False
        else:
            joined_refs = " ".join(str(item).lower() for item in (*source_refs, *visual_refs))
            if any(token in joined_refs for token in BANNED_ASSET_TOKENS):
                sculpture_leakage.append(operation_id)

        lineage_fields = {
            key
            for key in ("replaces_operation_id", "refines_operation_id")
            if key in operation
        }
        expected_target = REPLACEMENT_TARGETS.get(operation_id)
        if expected_target is not None:
            lineage_complete &= (
                lineage_fields == {"replaces_operation_id"}
                and operation.get("replaces_operation_id") == expected_target
            )
        else:
            lineage_complete &= lineage_fields == {"refines_operation_id"}

        basis = operation.get("parameter_basis")
        if not isinstance(basis, Mapping):
            soft_parameter_policy_valid = False
        else:
            soft_parameter_policy_valid &= (
                basis.get("classification") == "SOFT"
                and basis.get("metric_authority") is False
                and basis.get("pixel_measurement_authority") is False
                and _soft_ranges_valid(basis)
            )
        parameters = operation.get("parameters")
        if not isinstance(parameters, Mapping):
            contact_policies_valid = False
        else:
            occupancy_keys.append(str(parameters.get("occupancy_key", "")))
            unsupported_exact_keys = {
                "tile_module_m",
                "member_spacing_m",
                "section_width_m",
                "section_height_m",
            }.intersection(parameters)
            if unsupported_exact_keys:
                soft_parameter_policy_valid = False
            declared_collisions = parameters.get("declared_collision_with", ())
            if declared_collisions:
                contact_policies_valid = False

        policy = operation.get("contact_policy")
        expected_spec = EXPECTED_HOST_SPECS.get(operation_id)
        if not isinstance(policy, Mapping) or expected_spec is None:
            contact_policies_valid = False
            host_chain_valid = False
            continue
        host_id, co_hosts, relation, dimension = expected_spec
        actual_co_hosts = policy.get("co_host_ids")
        if not isinstance(actual_co_hosts, Sequence) or isinstance(actual_co_hosts, (str, bytes)):
            actual_co_hosts = ()
        policy_valid = (
            operation.get("host_id") == host_id
            and tuple(actual_co_hosts) == co_hosts
            and policy.get("relation") == relation
            and policy.get("required") is True
            and policy.get("contact_dimension") == dimension
            and policy.get("collision_policy") == "touch_only_no_volume"
        )
        contact_policies_valid &= policy_valid
        all_hosts = (host_id, *co_hosts)
        host_chain_valid &= all(host in by_id for host in all_hosts)

        leakage_text = " ".join(
            (
                operation_id.lower(),
                kind.lower(),
                str(operation.get("material_id", "")).lower(),
            )
        )
        if any(token in leakage_text for token in BANNED_SCULPTURE_TOKENS):
            sculpture_leakage.append(operation_id)

    if len(occupancy_keys) != len(set(occupancy_keys)) or any(not key for key in occupancy_keys):
        contact_policies_valid = False
    if delta_by_id and _host_graph_has_cycle(delta_by_id):
        host_chain_valid = False

    if not evidence_complete:
        failures.append("roof delta evidence bindings are incomplete")
    if not lineage_complete:
        failures.append("roof delta replacement/refinement lineage is incomplete")
    if not material_roles_valid:
        failures.append("roof delta material roles do not match member kinds")
    if not contact_policies_valid:
        failures.append("roof delta declares a floating, duplicate, or volumetric contact")
    if not host_chain_valid:
        failures.append("roof/eaves/pediment host chain is missing or cyclic")
    if not soft_parameter_policy_valid:
        failures.append("unsupported exact roof metrics were asserted as authoritative")
    if sculpture_leakage:
        failures.append("sculpture or PARKED asset geometry leaked into the roof delta")

    checks = {
        "coarse_hosts_removed": not coarse_ids_present and not coarse_kinds_present,
        "required_delta_complete": not missing_delta_ids and not unexpected_delta_ids,
        "root_supports_present": not missing_roots,
        "typed_host_chain": host_chain_valid,
        "declared_nonvolumetric_contacts": contact_policies_valid,
        "material_roles": material_roles_valid,
        "evidence_bindings": evidence_complete,
        "soft_metric_policy": soft_parameter_policy_valid,
        "replacement_lineage": lineage_complete,
        "sculpture_excluded": not sculpture_leakage,
        "delta_operation_count": len(delta_by_id),
    }
    receipt: dict[str, Any] = {
        "schema": "ParthenonStage4RoofEavesPedimentValidation@1",
        "passed": not failures,
        "status": "PASSED" if not failures else "FAILED",
        "checks": checks,
        "failures": failures,
        "validated_operation_count": len(full_operations),
        "validated_delta_ids": sorted(delta_by_id),
        "canonical_write_authority": False,
    }
    receipt["receipt_digest"] = _digest(receipt)
    return receipt


__all__ = [
    "COARSE_HOST_IDS",
    "ParthenonStage4RoofError",
    "REQUIRED_DELTA_IDS",
    "compile_roof_eaves_pediment_delta",
    "validate_roof_eaves_pediment_operations",
]
