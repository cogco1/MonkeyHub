"""Deterministic successor-wide relation checks for Parthenon Stage 4.

The Stage 4 reconstruction script deliberately owns compilation and persistence.
This module owns neither: it accepts operation mappings (and, optionally, a 3DM
path for read-only identity inspection) and returns a JSON-compatible receipt.

The geometric test is intentionally bounded.  Axis-aligned boxes can be proven
to collide from their AABBs.  Curved and sloped objects only produce broad-phase
candidates; rhino3dm does not expose pairwise boolean/intersection queries, so
those candidates fail closed and name the required narrow-phase references.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence


RECEIPT_SCHEMA = "ParthenonStage4SuccessorRelationReceipt@1"
_TOLERANCE = 1.0e-9
_DETAIL_LIMIT = 50

# Classification is based on semantic kind plus component id, never operation-id
# spelling.  In particular, the preserved naos shafts do not start with any of
# the Stage 4 exterior-column prefixes.
SHAFT_KIND_TYPES: Mapping[str, str] = {
    "column": "shaft",
    "column_shaft": "shaft",
    "doric_shaft": "shaft",
    "fluted_column": "shaft",
    "ionic_column": "shaft",
    "tapered_column": "shaft",
}

COLUMN_PART_KIND_TYPES: Mapping[str, str] = {
    **SHAFT_KIND_TYPES,
    "capital": "capital_or_support",
    "column_capital": "capital_or_support",
    "column_support": "capital_or_support",
    "doric_abacus": "capital_or_support",
    "doric_echinus": "capital_or_support",
    "doric_neck": "capital_or_support",
    "impost": "capital_or_support",
    "bearing_block": "capital_or_support",
}

COLUMN_COMPONENT_ROLES: Mapping[str, str] = {
    "interior-colonnade": "interior",
    "peristyle": "exterior",
    "porches": "porch",
}

STACK_BRIDGE_KINDS = frozenset(
    {
        "bearing_block",
        "capital",
        "column_capital",
        "column_support",
        "doric_abacus",
        "doric_echinus",
        "doric_neck",
        "impost",
    }
)

INTERSECTION_POLICIES = frozenset(
    {
        "analytic_disjoint",
        "bounded_embedded_finish",
        "bounded_structural_union",
        "touch_only",
    }
)
_BOUNDED_OVERLAP_POLICIES = frozenset(
    {"bounded_embedded_finish", "bounded_structural_union"}
)
_WILDCARD_TOKENS = ("*", "?", "[", "]")


@dataclass(frozen=True)
class _Bounds:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]
    exact_solid: bool

    def midpoint(self, axis: int) -> float:
        return (self.minimum[axis] + self.maximum[axis]) / 2.0


@dataclass(frozen=True)
class _AxialBody:
    center_xy: tuple[float, float]
    radius: float
    z_minimum: float
    z_maximum: float


@dataclass(frozen=True)
class _Operation:
    operation_id: str
    component_id: str
    kind: str
    parameters: Mapping[str, object]
    lineage_role: str
    bounds: _Bounds | None
    axial_body: _AxialBody | None
    column_part_type: str | None
    column_component_role: str | None
    raw: Mapping[str, object]


@dataclass(frozen=True)
class _ContactRule:
    lower_component_id: str
    lower_kind: str
    upper_component_id: str
    upper_kind: str
    relation: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "lower_component_id": self.lower_component_id,
            "lower_kind": self.lower_kind,
            "upper_component_id": self.upper_component_id,
            "upper_kind": self.upper_kind,
            "relation": self.relation,
        }


@dataclass(frozen=True)
class _RelationContract:
    first_operation_id: str
    second_operation_id: str
    relation: str
    intersection_policy: str
    max_overlap_m3: float | None
    basis_kind: str
    basis_refs: tuple[str, ...]
    source: str

    @property
    def pair(self) -> tuple[str, str]:
        return tuple(sorted((self.first_operation_id, self.second_operation_id)))  # type: ignore[return-value]

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "first_operation_id": self.first_operation_id,
            "second_operation_id": self.second_operation_id,
            "relation": self.relation,
            "intersection_policy": self.intersection_policy,
            "basis_kind": self.basis_kind,
            "basis_refs": list(self.basis_refs),
            "source": self.source,
        }
        if self.max_overlap_m3 is not None:
            result["max_overlap_m3"] = self.max_overlap_m3
        return result


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _vector3(value: object, label: str) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError(f"{label} must be a three-vector")
    return tuple(_finite_number(item, label) for item in value)  # type: ignore[return-value]


def _box_bounds(
    origin_value: object,
    size_value: object,
    *,
    exact_solid: bool,
) -> _Bounds:
    origin = _vector3(origin_value, "origin")
    size = _vector3(size_value, "size")
    if any(item <= 0.0 for item in size):
        raise ValueError("size must have positive extent")
    return _Bounds(
        minimum=origin,
        maximum=tuple(origin[axis] + size[axis] for axis in range(3)),
        exact_solid=exact_solid,
    )


def _operation_bounds(kind: str, parameters: Mapping[str, object]) -> _Bounds:
    if "origin" in parameters and "size" in parameters:
        # These operation kinds are realized as axis-aligned Brep boxes by the
        # current adapters.  Unknown origin/size kinds remain conservative.
        exact = kind in {
            "box",
            "capital",
            "column_capital",
            "column_support",
            "doric_abacus",
            "entablature_layer",
            "impost",
            "bearing_block",
            "triglyph",
        }
        return _box_bounds(parameters["origin"], parameters["size"], exact_solid=exact)

    if kind in SHAFT_KIND_TYPES:
        center = _vector3(parameters.get("center"), "center")
        if kind == "doric_shaft":
            height = _finite_number(parameters.get("shaft_height"), "shaft_height")
            radius = (
                _finite_number(parameters.get("lower_diameter"), "lower_diameter") / 2.0
                + _finite_number(parameters.get("entasis_max", 0.0), "entasis_max")
            )
        else:
            height = _finite_number(parameters.get("height"), "height")
            radius = _finite_number(parameters.get("diameter"), "diameter") / 2.0
        if height <= 0.0 or radius <= 0.0:
            raise ValueError("column height and radius must be positive")
        return _Bounds(
            minimum=(center[0] - radius, center[1] - radius, center[2]),
            maximum=(center[0] + radius, center[1] + radius, center[2] + height),
            exact_solid=False,
        )

    if kind in {"doric_neck", "doric_echinus"}:
        center = _vector3(parameters.get("center"), "center")
        height = _finite_number(parameters.get("height"), "height")
        radius = max(
            _finite_number(parameters.get("lower_diameter"), "lower_diameter"),
            _finite_number(parameters.get("upper_diameter"), "upper_diameter"),
        ) / 2.0
        if height <= 0.0 or radius <= 0.0:
            raise ValueError("capital-part height and radius must be positive")
        return _Bounds(
            minimum=(center[0] - radius, center[1] - radius, center[2]),
            maximum=(center[0] + radius, center[1] + radius, center[2] + height),
            exact_solid=False,
        )

    if kind == "roof_prism":
        width = _finite_number(parameters.get("width"), "width")
        length = _finite_number(parameters.get("length"), "length")
        eave = _finite_number(parameters.get("eave_z"), "eave_z")
        ridge = _finite_number(parameters.get("ridge_z"), "ridge_z")
        y_center = _finite_number(parameters.get("y_center"), "y_center")
        if width <= 0.0 or length <= 0.0 or ridge <= eave:
            raise ValueError("roof prism dimensions are invalid")
        return _Bounds(
            minimum=(-width / 2.0, y_center - length / 2.0, eave),
            maximum=(width / 2.0, y_center + length / 2.0, ridge),
            exact_solid=False,
        )

    if kind == "gable_panel":
        width = _finite_number(parameters.get("width"), "width")
        depth = _finite_number(parameters.get("depth"), "depth")
        eave = _finite_number(parameters.get("eave_z"), "eave_z")
        ridge = _finite_number(parameters.get("ridge_z"), "ridge_z")
        y_center = _finite_number(parameters.get("y_center"), "y_center")
        if width <= 0.0 or depth <= 0.0 or ridge <= eave:
            raise ValueError("gable dimensions are invalid")
        return _Bounds(
            minimum=(-width / 2.0, y_center - depth / 2.0, eave),
            maximum=(width / 2.0, y_center + depth / 2.0, ridge),
            exact_solid=False,
        )

    host_envelope = parameters.get("inherited_host_envelope")
    if kind in {"eave_geison", "eave_sima"}:
        if not isinstance(host_envelope, Mapping):
            raise ValueError(f"{kind} needs inherited_host_envelope")
        origin = list(_vector3(host_envelope.get("origin"), "host origin"))
        size = list(_vector3(host_envelope.get("size"), "host size"))
        geison_height = size[2] * 0.62
        if kind == "eave_geison":
            size[2] = geison_height
        else:
            origin[2] += geison_height
            size[2] -= geison_height
        return _box_bounds(origin, size, exact_solid=True)

    if kind == "pediment_horizontal_geison":
        if not isinstance(host_envelope, Mapping):
            raise ValueError("pediment_horizontal_geison needs inherited_host_envelope")
        return _box_bounds(
            host_envelope.get("origin"),
            host_envelope.get("size"),
            exact_solid=True,
        )

    pediment_envelope = parameters.get("inherited_pediment_envelope")
    if kind in {
        "acroterion_seat",
        "pediment_raking_geison",
        "pediment_raking_sima",
        "pediment_tympanum",
    }:
        if not isinstance(pediment_envelope, Mapping):
            raise ValueError(f"{kind} needs inherited_pediment_envelope")
        width = _finite_number(pediment_envelope.get("width"), "pediment width")
        depth = _finite_number(pediment_envelope.get("depth"), "pediment depth")
        eave = _finite_number(pediment_envelope.get("eave_z"), "pediment eave_z")
        ridge = _finite_number(pediment_envelope.get("ridge_z"), "pediment ridge_z")
        y_center = _finite_number(
            pediment_envelope.get("y_center"), "pediment y_center"
        )
        if kind == "pediment_tympanum":
            return _Bounds(
                minimum=(-width / 2.0, y_center - depth / 2.0, eave),
                maximum=(width / 2.0, y_center + depth / 2.0, ridge),
                exact_solid=False,
            )
        if kind == "acroterion_seat":
            seat_width = min(0.60, width * 0.03)
            position = str(parameters.get("position", ""))
            if position == "apex":
                x, z = -seat_width / 2.0, ridge - 0.12
            elif position == "left":
                x, z = -width / 2.0, eave + 0.08
            elif position == "right":
                x, z = width / 2.0 - seat_width, eave + 0.08
            else:
                raise ValueError("acroterion_seat position is invalid")
            return _box_bounds(
                (x, y_center - depth / 2.0, z),
                (seat_width, depth, 0.12),
                exact_solid=True,
            )
        half = "left" if str(parameters.get("hand", "")) == "left" else "right"
        base_offset, ridge_offset, height = (
            (0.03, 0.30, 0.16)
            if kind == "pediment_raking_geison"
            else (0.20, 0.12, 0.10)
        )
        x0, x1 = (-width / 2.0, 0.0) if half == "left" else (0.0, width / 2.0)
        z0 = eave + base_offset if half == "left" else ridge - ridge_offset
        z1 = ridge - ridge_offset if half == "left" else eave + base_offset
        return _Bounds(
            minimum=(min(x0, x1), y_center - depth / 2.0, min(z0, z1)),
            maximum=(max(x0, x1), y_center + depth / 2.0, max(z0, z1) + height),
            exact_solid=False,
        )

    roof_envelope = parameters.get("inherited_roof_envelope")
    roof_kinds = {
        "marble_cover_tile_field",
        "marble_eave_terminal",
        "marble_pan_tile_field",
        "marble_ridge_terminal",
        "timber_bearing_beam",
        "timber_rafter_field",
        "timber_ridge_beam",
    }
    if kind in roof_kinds:
        if not isinstance(roof_envelope, Mapping):
            raise ValueError(f"{kind} needs inherited_roof_envelope")
        width = _finite_number(roof_envelope.get("width"), "roof width")
        length = _finite_number(roof_envelope.get("length"), "roof length")
        eave = _finite_number(roof_envelope.get("eave_z"), "roof eave_z")
        ridge = _finite_number(roof_envelope.get("ridge_z"), "roof ridge_z")
        y_center = _finite_number(roof_envelope.get("y_center"), "roof y_center")
        y0 = y_center - length / 2.0
        topology = str(parameters.get("topology", ""))
        side = "north" if "north" in topology else "south" if "south" in topology else None
        if kind in {
            "timber_rafter_field",
            "marble_pan_tile_field",
            "marble_cover_tile_field",
        }:
            if side is None:
                raise ValueError(f"{kind} topology lacks north/south side")
            lower_offset, ridge_offset, thickness = {
                "timber_rafter_field": (0.05, 0.42, 0.10),
                "marble_pan_tile_field": (0.18, 0.28, 0.08),
                "marble_cover_tile_field": (0.29, 0.15, 0.06),
            }[kind]
            x_eave = width / 2.0 if side == "north" else -width / 2.0
            return _Bounds(
                minimum=(min(0.0, x_eave), y0, eave + lower_offset),
                maximum=(
                    max(0.0, x_eave),
                    y0 + length,
                    ridge - ridge_offset + thickness,
                ),
                exact_solid=False,
            )
        if kind in {"timber_bearing_beam", "marble_eave_terminal"}:
            if side is None:
                raise ValueError(f"{kind} topology lacks north/south side")
            x_center = width / 2.0 - 0.16 if side == "north" else -width / 2.0 + 0.16
            cross = 0.24 if kind == "timber_bearing_beam" else 0.18
            z = eave + (0.02 if kind == "timber_bearing_beam" else 0.31)
            return _box_bounds(
                (x_center - cross / 2.0, y0, z),
                (cross, length, cross),
                exact_solid=True,
            )
        cross = 0.24 if kind == "timber_ridge_beam" else 0.18
        z = ridge - (0.42 if kind == "timber_ridge_beam" else 0.18)
        return _box_bounds(
            (-cross / 2.0, y0, z),
            (cross, length, cross),
            exact_solid=True,
        )

    raise ValueError(f"kind {kind!r} has no bounded Stage 4 AABB contract")


def _operation_axial_body(
    kind: str,
    parameters: Mapping[str, object],
    bounds: _Bounds,
) -> _AxialBody | None:
    """Return a conservative analytic envelope for a vertical body of revolution."""

    if kind not in {*SHAFT_KIND_TYPES, "doric_neck", "doric_echinus"}:
        return None
    center = _vector3(parameters.get("center"), "center")
    return _AxialBody(
        center_xy=(center[0], center[1]),
        radius=(bounds.maximum[0] - bounds.minimum[0]) / 2.0,
        z_minimum=bounds.minimum[2],
        z_maximum=bounds.maximum[2],
    )


def _overlap_lengths(first: _Bounds, second: _Bounds) -> tuple[float, float, float]:
    return tuple(
        min(first.maximum[axis], second.maximum[axis])
        - max(first.minimum[axis], second.minimum[axis])
        for axis in range(3)
    )  # type: ignore[return-value]


def _intersection_volume(first: _Bounds, second: _Bounds) -> float:
    overlap = _overlap_lengths(first, second)
    if any(item <= 0.0 for item in overlap):
        return 0.0
    return overlap[0] * overlap[1] * overlap[2]


def _rounded(value: float) -> float:
    rounded = round(value, 9)
    return 0.0 if rounded == -0.0 else rounded


def _parse_operations(
    operations: Sequence[Mapping[str, object]],
) -> tuple[list[_Operation], list[str], list[dict[str, object]]]:
    failures: list[str] = []
    malformed: list[dict[str, object]] = []
    parsed: list[_Operation] = []
    seen: Counter[str] = Counter()

    for index, raw in enumerate(operations):
        if not isinstance(raw, Mapping):
            malformed.append({"index": index, "reason": "operation is not a mapping"})
            continue
        operation_id = str(raw.get("operation_id", "")).strip()
        component_id = str(raw.get("component_id", "")).strip()
        kind = str(raw.get("kind", "")).strip()
        parameters = raw.get("parameters")
        if not operation_id or not component_id or not kind or not isinstance(parameters, Mapping):
            malformed.append(
                {
                    "index": index,
                    "operation_id": operation_id or None,
                    "reason": "operation_id, component_id, kind, and parameter mapping are required",
                }
            )
            continue
        seen[operation_id] += 1
        bounds: _Bounds | None
        try:
            bounds = _operation_bounds(kind, parameters)
        except ValueError as exc:
            bounds = None
            malformed.append(
                {
                    "operation_id": operation_id,
                    "reason": str(exc),
                }
            )
        axial_body: _AxialBody | None = None
        if bounds is not None:
            try:
                axial_body = _operation_axial_body(kind, parameters, bounds)
            except ValueError as exc:
                malformed.append(
                    {
                        "operation_id": operation_id,
                        "reason": f"analytic axial-body contract: {exc}",
                    }
                )
        part_type = COLUMN_PART_KIND_TYPES.get(kind)
        component_role = COLUMN_COMPONENT_ROLES.get(component_id) if part_type else None
        parsed.append(
            _Operation(
                operation_id=operation_id,
                component_id=component_id,
                kind=kind,
                parameters=parameters,
                lineage_role=(
                    "delta"
                    if "replaces_operation_id" in raw or "refines_operation_id" in raw
                    else "preserved"
                ),
                bounds=bounds,
                axial_body=axial_body,
                column_part_type=part_type,
                column_component_role=component_role,
                raw=raw,
            )
        )

    duplicate_ids = sorted(operation_id for operation_id, count in seen.items() if count > 1)
    if duplicate_ids:
        failures.append(
            f"operation ids are not unique ({len(duplicate_ids)} duplicate ids)"
        )
    if malformed:
        failures.append(
            f"operation/AABB schema is incomplete ({len(malformed)} malformed operations)"
        )
    parsed.sort(key=lambda item: (item.operation_id, item.component_id, item.kind))
    return parsed, failures, sorted(
        malformed,
        key=lambda item: (str(item.get("operation_id", "")), int(item.get("index", -1))),
    )


def _parse_contact_rules(
    raw_rules: Sequence[Mapping[str, str]],
) -> tuple[tuple[_ContactRule, ...], list[str]]:
    rules: list[_ContactRule] = []
    failures: list[str] = []
    required = (
        "lower_component_id",
        "lower_kind",
        "upper_component_id",
        "upper_kind",
        "relation",
    )
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, Mapping) or any(not str(raw.get(key, "")).strip() for key in required):
            failures.append(f"typed contact allowlist entry {index} is incomplete")
            continue
        rule = _ContactRule(*(str(raw[key]).strip() for key in required))
        if rule.relation not in {"bears_on", "joins", "supports"}:
            failures.append(
                f"typed contact allowlist entry {index} has unsupported relation {rule.relation!r}"
            )
            continue
        rules.append(rule)
    unique = sorted(
        set(rules),
        key=lambda item: (
            item.lower_component_id,
            item.lower_kind,
            item.upper_component_id,
            item.upper_kind,
            item.relation,
        ),
    )
    return tuple(unique), failures


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _exact_identifier(value: object, label: str) -> str:
    text = _required_text(value, label)
    if any(token in text for token in _WILDCARD_TOKENS):
        raise ValueError(f"{label} must be exact; wildcards are prohibited")
    return text


def _host_operation_id(value: object) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("operation_id", value.get("host_operation_id"))
    else:
        candidate = value
    if candidate is None:
        return None
    return _exact_identifier(candidate, "host operation_id")


def _contract_basis(
    raw: Mapping[str, object],
) -> tuple[str, tuple[str, ...]]:
    evidence_values: list[str] = []
    for key in ("evidence_ref", "ref"):
        if raw.get(key) is not None:
            evidence_values.append(_required_text(raw[key], key))
    evidence_refs = raw.get("evidence_refs")
    if evidence_refs is not None:
        if not isinstance(evidence_refs, Sequence) or isinstance(evidence_refs, (str, bytes)):
            raise ValueError("evidence_refs must be a non-empty sequence")
        evidence_values.extend(
            _required_text(item, "evidence_refs item") for item in evidence_refs
        )
    if evidence_values:
        return "evidence_ref", tuple(sorted(set(evidence_values)))

    for key in ("host_operation_id", "host"):
        if raw.get(key) is not None:
            host_id = _host_operation_id(raw[key])
            assert host_id is not None
            return "host_operation_id", (host_id,)
    raise ValueError("relation contract needs an evidence/ref or exact host basis")


def _parse_relation_contract(
    raw: Mapping[str, object],
    *,
    source: str,
) -> _RelationContract:
    first_id = _exact_identifier(raw.get("first_operation_id"), "first_operation_id")
    second_id = _exact_identifier(raw.get("second_operation_id"), "second_operation_id")
    if first_id == second_id:
        raise ValueError("relation contract endpoints must be different operations")
    relation = _required_text(raw.get("relation"), "relation")
    policy = _required_text(
        raw.get("intersection_policy", raw.get("contact_policy")),
        "intersection_policy",
    )
    if policy not in INTERSECTION_POLICIES:
        raise ValueError(
            "intersection_policy must be one of " + ", ".join(sorted(INTERSECTION_POLICIES))
        )
    max_value = raw.get("max_overlap_m3")
    max_overlap: float | None = None
    if policy in _BOUNDED_OVERLAP_POLICIES:
        max_overlap = _finite_number(max_value, "max_overlap_m3")
        if max_overlap <= 0.0:
            raise ValueError("bounded positive-volume policy needs max_overlap_m3 > 0")
    elif max_value is not None:
        raise ValueError(f"{policy} must not declare a positive-volume overlap bound")
    basis_kind, basis_refs = _contract_basis(raw)
    if basis_kind == "host_operation_id" and basis_refs[0] not in {first_id, second_id}:
        raise ValueError("host basis must name one exact contract endpoint")
    return _RelationContract(
        first_operation_id=first_id,
        second_operation_id=second_id,
        relation=relation,
        intersection_policy=policy,
        max_overlap_m3=max_overlap,
        basis_kind=basis_kind,
        basis_refs=basis_refs,
        source=source,
    )


def _operation_metadata(operation: _Operation, key: str) -> object:
    if key in operation.raw:
        return operation.raw[key]
    return operation.parameters.get(key)


def _operation_relation_contracts(
    operation: _Operation,
) -> tuple[list[_RelationContract], list[str]]:
    host_value = _operation_metadata(operation, "host")
    if host_value is None:
        host_value = _operation_metadata(operation, "host_id")
    contract_value = _operation_metadata(operation, "contact_contract")
    policy_value = _operation_metadata(operation, "contact_policy")
    if contract_value is None and policy_value is None:
        return [], []
    if (
        contract_value is None
        and isinstance(policy_value, Mapping)
        and policy_value.get("intersection_policy") is None
    ):
        # Existing roof contact_policy is assembly intent (host/co-host graph),
        # not yet an exact solid-pair contract.  The semantic contract compiler
        # consumes it before geometry confirmation; the validator must not
        # promote it directly merely because a collision_policy string exists.
        return [], []

    policy_defaults: dict[str, object] = {}
    if isinstance(policy_value, str):
        policy_defaults["intersection_policy"] = policy_value
    elif isinstance(policy_value, Mapping):
        policy_defaults.update(policy_value)
        collision_policy = str(policy_value.get("collision_policy", ""))
        if collision_policy == "touch_only_no_volume":
            policy_defaults["intersection_policy"] = "touch_only"
    elif policy_value is not None:
        return [], [f"operation {operation.operation_id} contact_policy is malformed"]

    raw_contracts: list[Mapping[str, object]]
    if contract_value is None:
        raw_contracts = [{}]
    elif isinstance(contract_value, Mapping):
        raw_contracts = [contract_value]
    elif isinstance(contract_value, Sequence) and not isinstance(
        contract_value, (str, bytes)
    ):
        raw_contracts = [item for item in contract_value if isinstance(item, Mapping)]
        if len(raw_contracts) != len(contract_value):
            return [], [
                f"operation {operation.operation_id} contact_contract entries must be mappings"
            ]
    else:
        return [], [f"operation {operation.operation_id} contact_contract is malformed"]

    def exact_ids(value: object, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError(f"{label} must be an exact operation-id sequence")
        return [_exact_identifier(item, label) for item in value]

    host_id: str | None = None
    host_ids: list[str] = []
    try:
        if isinstance(host_value, Mapping) and host_value.get("operation_ids") is not None:
            host_ids = exact_ids(host_value.get("operation_ids"), "host operation_ids")
        else:
            host_id = _host_operation_id(host_value) if host_value is not None else None
            if host_id is not None:
                host_ids = [host_id]
    except ValueError as exc:
        return [], [f"operation {operation.operation_id} relation contract: {exc}"]

    entries: list[dict[str, object]] = []
    try:
        for raw_entry in raw_contracts:
            entry = {**policy_defaults, **dict(raw_entry)}
            if "intersection_policy" not in entry:
                allowed_contact = str(entry.get("allowed_contact", ""))
                if allowed_contact.startswith("shared_"):
                    entry["intersection_policy"] = "touch_only"
            if (
                "first_operation_id" in entry
                and "second_operation_id" in entry
            ):
                entries.append(entry)
                continue
            lower_id = entry.get("lower_operation_id")
            upper_id = entry.get("upper_operation_id")
            if lower_id is not None and upper_id is not None:
                entries.append(
                    {
                        **entry,
                        "first_operation_id": lower_id,
                        "second_operation_id": upper_id,
                        "host_operation_id": lower_id,
                    }
                )
                continue
            lower_ids = exact_ids(
                entry.get("lower_operation_ids"), "lower_operation_ids"
            )
            upper_ids = exact_ids(
                entry.get("upper_operation_ids"), "upper_operation_ids"
            )
            if lower_ids or upper_ids:
                for lower in lower_ids:
                    entries.append(
                        {
                            **entry,
                            "first_operation_id": lower,
                            "second_operation_id": operation.operation_id,
                            "host_operation_id": lower,
                        }
                    )
                for upper in upper_ids:
                    entries.append(
                        {
                            **entry,
                            "first_operation_id": operation.operation_id,
                            "second_operation_id": upper,
                            "host_operation_id": operation.operation_id,
                        }
                    )
                continue

            co_host_ids = exact_ids(entry.get("co_host_ids"), "co_host_ids")
            candidate_hosts = [*host_ids, *co_host_ids]
            if candidate_hosts and entry.get("intersection_policy") is not None:
                for candidate_host in candidate_hosts:
                    entries.append(
                        {
                            **entry,
                            "first_operation_id": candidate_host,
                            "second_operation_id": operation.operation_id,
                            "host_operation_id": candidate_host,
                        }
                    )
                continue
            # A window's bounds_empty_aperture metadata is a semantic void
            # declaration, not a solid-pair contact authorization.
            if entry.get("intersection_policy") is None:
                continue
            entries.append(entry)
    except ValueError as exc:
        return [], [f"operation {operation.operation_id} relation contract: {exc}"]

    contracts: list[_RelationContract] = []
    failures: list[str] = []
    for index, entry in enumerate(entries):
        normalized: dict[str, object] = dict(entry)
        if normalized.get("relation") in {"bears_on", "rests_on"}:
            # Exact pairs are stored host/support first and supported member
            # second.  Canonicalize inverse wording so the two members cannot
            # publish contradictory contracts for the same physical joint.
            normalized["relation"] = "supports"
        if not any(
            key in normalized
            for key in ("evidence_ref", "evidence_refs", "ref", "host", "host_operation_id")
        ):
            normalized["host_operation_id"] = normalized.get("first_operation_id")
        source = f"operation:{operation.operation_id}:contact_contract:{index}"
        try:
            parsed_contract = _parse_relation_contract(normalized, source=source)
            contracts.append(
                _RelationContract(
                    first_operation_id=parsed_contract.first_operation_id,
                    second_operation_id=parsed_contract.second_operation_id,
                    relation=parsed_contract.relation,
                    intersection_policy=parsed_contract.intersection_policy,
                    max_overlap_m3=parsed_contract.max_overlap_m3,
                    basis_kind=parsed_contract.basis_kind,
                    basis_refs=parsed_contract.basis_refs,
                    source=(
                        f"operation:{operation.operation_id}:contact_contract:"
                        + "::".join(parsed_contract.pair)
                    ),
                )
            )
        except ValueError as exc:
            failures.append(f"{source}: {exc}")
    return contracts, failures


def _contract_semantics(contract: _RelationContract) -> tuple[object, ...]:
    return (
        contract.first_operation_id,
        contract.second_operation_id,
        contract.relation,
        contract.intersection_policy,
        contract.max_overlap_m3,
        contract.basis_kind,
        contract.basis_refs,
    )


def _normalize_relation_contracts(
    operations: Sequence[_Operation],
    explicit_contracts: Sequence[Mapping[str, object]],
) -> tuple[dict[tuple[str, str], _RelationContract], list[str], dict[str, int]]:
    failures: list[str] = []
    operation_contracts: list[_RelationContract] = []
    for operation in operations:
        contracts, item_failures = _operation_relation_contracts(operation)
        operation_contracts.extend(contracts)
        failures.extend(item_failures)

    argument_contracts: list[_RelationContract] = []
    for index, raw in enumerate(explicit_contracts):
        source = f"argument:typed_relation_contracts:{index}"
        if not isinstance(raw, Mapping):
            failures.append(f"{source}: relation contract must be a mapping")
            continue
        try:
            parsed_contract = _parse_relation_contract(raw, source=source)
            argument_contracts.append(
                _RelationContract(
                    first_operation_id=parsed_contract.first_operation_id,
                    second_operation_id=parsed_contract.second_operation_id,
                    relation=parsed_contract.relation,
                    intersection_policy=parsed_contract.intersection_policy,
                    max_overlap_m3=parsed_contract.max_overlap_m3,
                    basis_kind=parsed_contract.basis_kind,
                    basis_refs=parsed_contract.basis_refs,
                    source=(
                        "argument:typed_relation_contracts:"
                        + "::".join(parsed_contract.pair)
                    ),
                )
            )
        except ValueError as exc:
            failures.append(f"{source}: {exc}")

    by_pair: dict[tuple[str, str], _RelationContract] = {}
    source_counts: Counter[str] = Counter()
    for contract in (*operation_contracts, *argument_contracts):
        existing = by_pair.get(contract.pair)
        if existing is None:
            by_pair[contract.pair] = contract
            source_counts[contract.source.split(":", 1)[0]] += 1
            continue
        if _contract_semantics(existing) != _contract_semantics(contract):
            failures.append(
                "conflicting exact relation contracts for " + "::".join(contract.pair)
            )

    operation_ids = {operation.operation_id for operation in operations}
    for pair, contract in sorted(by_pair.items()):
        missing = sorted(set(pair) - operation_ids)
        if missing:
            failures.append(
                f"exact relation contract {'::'.join(pair)} references missing operations: "
                + ", ".join(missing)
            )
    return by_pair, failures, {
        "operation_metadata": source_counts.get("operation", 0),
        "argument": source_counts.get("argument", 0),
    }


def _vertical_order(
    first: _Operation,
    second: _Operation,
) -> tuple[_Operation, _Operation] | None:
    assert first.bounds is not None and second.bounds is not None
    if math.isclose(first.bounds.maximum[2], second.bounds.minimum[2], abs_tol=_TOLERANCE):
        return first, second
    if math.isclose(second.bounds.maximum[2], first.bounds.minimum[2], abs_tol=_TOLERANCE):
        return second, first
    return None


def _allowed_contact(
    first: _Operation,
    second: _Operation,
    rules: Sequence[_ContactRule],
) -> _ContactRule | None:
    ordered = _vertical_order(first, second)
    if ordered is None:
        return None
    lower, upper = ordered
    for rule in rules:
        if (
            lower.component_id == rule.lower_component_id
            and lower.kind == rule.lower_kind
            and upper.component_id == rule.upper_component_id
            and upper.kind == rule.upper_kind
        ):
            return rule
    return None


def _pair_key(first: _Operation, second: _Operation) -> str:
    return f"{first.operation_id}::{second.operation_id}"


def _analytic_axial_separation(
    first: _Operation,
    second: _Operation,
) -> dict[str, object] | None:
    if first.axial_body is None or second.axial_body is None:
        return None
    first_body = first.axial_body
    second_body = second.axial_body
    z_gap = max(
        first_body.z_minimum - second_body.z_maximum,
        second_body.z_minimum - first_body.z_maximum,
    )
    if z_gap > _TOLERANCE:
        return {
            "proof": "disjoint_z_intervals",
            "z_clearance_m": _rounded(z_gap),
        }
    center_distance = math.hypot(
        first_body.center_xy[0] - second_body.center_xy[0],
        first_body.center_xy[1] - second_body.center_xy[1],
    )
    radial_clearance = center_distance - first_body.radius - second_body.radius
    if radial_clearance > _TOLERANCE:
        return {
            "proof": "disjoint_xy_radius_envelopes",
            "center_distance_m": _rounded(center_distance),
            "radius_sum_m": _rounded(first_body.radius + second_body.radius),
            "radial_clearance_m": _rounded(radial_clearance),
        }
    return None


def _broad_phase_receipt(
    operations: Sequence[_Operation],
    legacy_rules: Sequence[_ContactRule],
    contracts: Mapping[tuple[str, str], _RelationContract],
) -> tuple[dict[str, object], list[str], list[dict[str, object]], tuple[str, ...]]:
    pair_denominator = len(operations) * (len(operations) - 1) // 2
    pair_roles: Counter[str] = Counter()
    separated_count = 0
    raw_contact_count = 0
    raw_positive_overlap_count = 0
    exact_contact_count = 0
    bounded_overlap_count = 0
    analytic_disjoint_count = 0
    legacy_match_ignored_count = 0
    unexpected_contacts: list[dict[str, object]] = []
    exact_collisions: list[dict[str, object]] = []
    narrow_candidates: list[dict[str, object]] = []
    contract_failures: list[dict[str, object]] = []
    contract_outcomes: dict[tuple[str, str], dict[str, object]] = {}
    evaluated_pair_count = 0

    for left_index, first in enumerate(operations):
        for second in operations[left_index + 1 :]:
            role_key = "_".join(sorted((first.lineage_role, second.lineage_role)))
            pair_roles[role_key] += 1
            pair = tuple(sorted((first.operation_id, second.operation_id)))
            contract = contracts.get(pair)
            if first.bounds is None or second.bounds is None:
                if contract is not None:
                    contract_outcomes[pair] = {
                        "pair": "::".join(pair),
                        "status": "UNRESOLVED",
                        "reason": "operation AABB unavailable",
                    }
                continue
            evaluated_pair_count += 1
            overlaps = _overlap_lengths(first.bounds, second.bounds)
            if all(item > _TOLERANCE for item in overlaps):
                raw_positive_overlap_count += 1
                detail = {
                    "pair": _pair_key(first, second),
                    "first_operation_id": first.operation_id,
                    "second_operation_id": second.operation_id,
                    "lineage_pair": role_key,
                    "overlap_lengths_m": [_rounded(item) for item in overlaps],
                    "aabb_overlap_volume_m3": _rounded(
                        overlaps[0] * overlaps[1] * overlaps[2]
                    ),
                }
                analytic_proof = _analytic_axial_separation(first, second)
                if analytic_proof is not None:
                    analytic_disjoint_count += 1
                    separated_count += 1
                    detail.update(analytic_proof)
                    detail["classification"] = "analytically_proven_disjoint"
                    if contract is not None:
                        if contract.intersection_policy == "analytic_disjoint":
                            contract_outcomes[pair] = {
                                **detail,
                                "status": "SATISFIED",
                                "contract": contract.to_mapping(),
                            }
                        else:
                            failure = {
                                **detail,
                                "classification": "exact_contract_relation_not_realized",
                                "contract": contract.to_mapping(),
                            }
                            contract_failures.append(failure)
                            contract_outcomes[pair] = {**failure, "status": "FAILED"}
                    continue

                overlap_volume = overlaps[0] * overlaps[1] * overlaps[2]
                if (
                    contract is not None
                    and contract.intersection_policy in _BOUNDED_OVERLAP_POLICIES
                    and contract.max_overlap_m3 is not None
                    and overlap_volume <= contract.max_overlap_m3 + _TOLERANCE
                ):
                    bounded_overlap_count += 1
                    outcome = {
                        **detail,
                        "classification": "allowed_exact_bounded_overlap",
                        "status": "SATISFIED",
                        "overlap_bound_m3": contract.max_overlap_m3,
                        "overlap_bound_basis": (
                            "exact_axis_aligned_box_volume"
                            if first.bounds.exact_solid and second.bounds.exact_solid
                            else "conservative_aabb_upper_bound"
                        ),
                        "contract": contract.to_mapping(),
                    }
                    contract_outcomes[pair] = outcome
                    continue

                if contract is not None:
                    if contract.intersection_policy in _BOUNDED_OVERLAP_POLICIES:
                        classification = (
                            "exact_contract_overlap_exceeds_bound"
                            if first.bounds.exact_solid and second.bounds.exact_solid
                            else "bounded_overlap_not_proven_within_limit"
                        )
                    elif contract.intersection_policy == "touch_only":
                        classification = "touch_only_contract_has_positive_volume"
                    else:
                        classification = "analytic_disjoint_contract_not_proven"
                    failure = {
                        **detail,
                        "classification": classification,
                        "contract": contract.to_mapping(),
                    }
                    contract_failures.append(failure)
                    contract_outcomes[pair] = {**failure, "status": "FAILED"}
                if first.bounds.exact_solid and second.bounds.exact_solid:
                    collision = dict(detail if contract is None else contract_failures[-1])
                    collision["classification"] = (
                        "confirmed_axis_aligned_box_collision"
                        if contract is None
                        else str(contract_failures[-1]["classification"])
                    )
                    exact_collisions.append(collision)
                else:
                    candidate = dict(detail if contract is None else contract_failures[-1])
                    candidate["classification"] = (
                        "broad_phase_candidate"
                        if contract is None
                        else str(contract_failures[-1]["classification"])
                    )
                    candidate["narrow_phase_ref_required"] = True
                    narrow_candidates.append(candidate)
                continue

            if all(item >= -_TOLERANCE for item in overlaps):
                raw_contact_count += 1
                detail = {
                    "pair": _pair_key(first, second),
                    "first_operation_id": first.operation_id,
                    "second_operation_id": second.operation_id,
                    "lineage_pair": role_key,
                    "overlap_lengths_m": [_rounded(item) for item in overlaps],
                }
                analytic_proof = _analytic_axial_separation(first, second)
                if analytic_proof is not None:
                    analytic_disjoint_count += 1
                    separated_count += 1
                    detail.update(analytic_proof)
                    detail["classification"] = "analytically_proven_disjoint"
                    if contract is not None:
                        if contract.intersection_policy == "analytic_disjoint":
                            contract_outcomes[pair] = {
                                **detail,
                                "status": "SATISFIED",
                                "contract": contract.to_mapping(),
                            }
                        else:
                            failure = {
                                **detail,
                                "classification": "exact_contract_relation_not_realized",
                                "contract": contract.to_mapping(),
                            }
                            contract_failures.append(failure)
                            contract_outcomes[pair] = {**failure, "status": "FAILED"}
                    continue

                legacy_match = _allowed_contact(first, second, legacy_rules)
                if legacy_match is not None:
                    legacy_match_ignored_count += 1
                if contract is None:
                    detail["classification"] = "unexpected_contact_without_exact_contract"
                    if legacy_match is not None:
                        detail["legacy_broad_rule_ignored"] = legacy_match.to_mapping()
                    unexpected_contacts.append(detail)
                elif contract.intersection_policy == "analytic_disjoint":
                    failure = {
                        **detail,
                        "classification": "analytic_disjoint_contract_not_proven",
                        "contract": contract.to_mapping(),
                    }
                    contract_failures.append(failure)
                    contract_outcomes[pair] = {**failure, "status": "FAILED"}
                else:
                    exact_contact_count += 1
                    contract_outcomes[pair] = {
                        **detail,
                        "classification": "allowed_exact_contract_contact",
                        "status": "SATISFIED",
                        "contract": contract.to_mapping(),
                    }
                continue
            separated_count += 1
            if contract is not None:
                if contract.intersection_policy == "analytic_disjoint":
                    contract_outcomes[pair] = {
                        "pair": _pair_key(first, second),
                        "classification": "aabb_proven_disjoint",
                        "status": "SATISFIED",
                        "contract": contract.to_mapping(),
                    }
                else:
                    failure = {
                        "pair": _pair_key(first, second),
                        "classification": "exact_contract_relation_not_realized",
                        "overlap_lengths_m": [_rounded(item) for item in overlaps],
                        "contract": contract.to_mapping(),
                    }
                    contract_failures.append(failure)
                    contract_outcomes[pair] = {**failure, "status": "FAILED"}

    for pair, contract in sorted(contracts.items()):
        if pair in contract_outcomes:
            continue
        failure = {
            "pair": "::".join(pair),
            "classification": "exact_contract_not_evaluated",
            "contract": contract.to_mapping(),
        }
        contract_failures.append(failure)
        contract_outcomes[pair] = {**failure, "status": "FAILED"}

    failures: list[str] = []
    if exact_collisions:
        failures.append(
            "successor AABB audit found "
            f"{len(exact_collisions)} confirmed positive-volume box collisions"
        )
    if narrow_candidates:
        failures.append(
            "successor AABB audit found "
            f"{len(narrow_candidates)} curved/sloped broad-phase candidates requiring narrow-phase refs"
        )
    if unexpected_contacts:
        failures.append(
            "successor AABB audit found "
            f"{len(unexpected_contacts)} contacts absent from exact-operation contracts"
        )
    if contract_failures:
        failures.append(
            f"{len(contract_failures)} exact-operation relation contracts failed"
        )

    all_details = sorted(
        (*exact_collisions, *narrow_candidates, *unexpected_contacts, *contract_failures),
        key=lambda item: (str(item["pair"]), str(item["classification"])),
    )
    required_refs = tuple(
        str(item["pair"])
        for item in sorted(narrow_candidates, key=lambda item: str(item["pair"]))
    )
    return (
        {
            "pair_denominator": pair_denominator,
            "evaluated_aabb_pair_count": evaluated_pair_count,
            "unevaluated_pair_count": pair_denominator - evaluated_pair_count,
            "lineage_pair_denominators": {
                key: pair_roles.get(key, 0)
                for key in ("delta_delta", "delta_preserved", "preserved_preserved")
            },
            "separated_pair_count": separated_count,
            "contact_count": raw_contact_count,
            "allowed_typed_contact_count": exact_contact_count,
            "allowed_exact_contact_count": exact_contact_count,
            "allowed_bounded_overlap_count": bounded_overlap_count,
            "analytic_disjoint_pair_count": analytic_disjoint_count,
            "legacy_broad_match_ignored_count": legacy_match_ignored_count,
            "unexpected_contact_count": len(unexpected_contacts),
            "positive_volume_overlap_count": raw_positive_overlap_count,
            "unresolved_positive_volume_overlap_count": len(exact_collisions)
            + len(narrow_candidates),
            "confirmed_box_collision_count": len(exact_collisions),
            "broad_phase_narrow_ref_required_count": len(narrow_candidates),
            "exact_relation_contract_denominator": len(contracts),
            "satisfied_exact_relation_contract_count": sum(
                item.get("status") == "SATISFIED" for item in contract_outcomes.values()
            ),
            "failed_exact_relation_contract_count": sum(
                item.get("status") == "FAILED" for item in contract_outcomes.values()
            ),
            "exact_relation_contract_outcomes": [
                contract_outcomes[pair] for pair in sorted(contract_outcomes)
            ],
            "method": "all-successor-pairs-aabb-broad-phase",
            "positive_volume_requires_exact_bounded_contract": True,
            "contact_requires_exact_operation_contract": True,
            "legacy_component_kind_allowlist_authorizes_contact": False,
        },
        failures,
        all_details[:_DETAIL_LIMIT],
        required_refs,
    )


def _union_bounds(items: Iterable[_Bounds]) -> _Bounds:
    bounds = tuple(items)
    return _Bounds(
        minimum=tuple(min(item.minimum[axis] for item in bounds) for axis in range(3)),
        maximum=tuple(max(item.maximum[axis] for item in bounds) for axis in range(3)),
        exact_solid=False,
    )


def _window_specs(
    operations: Sequence[_Operation],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    grouped: defaultdict[
        tuple[str, float, float, float, float], list[_Operation]
    ] = defaultdict(list)
    malformed: list[dict[str, object]] = []
    for operation in operations:
        window_clear = operation.parameters.get("window_clear")
        if window_clear is None:
            continue
        if not isinstance(window_clear, Mapping):
            malformed.append(
                {
                    "operation_id": operation.operation_id,
                    "reason": "window_clear is not a mapping",
                }
            )
            continue
        try:
            center_x = _finite_number(window_clear.get("center_x"), "window center_x")
            width = _finite_number(window_clear.get("width"), "window width")
            sill_z = _finite_number(window_clear.get("sill_z"), "window sill_z")
            height = _finite_number(window_clear.get("height"), "window height")
            if width <= 0.0 or height <= 0.0:
                raise ValueError("window width and height must be positive")
        except ValueError as exc:
            malformed.append(
                {"operation_id": operation.operation_id, "reason": str(exc)}
            )
            continue
        side = str(operation.parameters.get("window_side", "")).strip()
        if not side:
            side = "left" if center_x < 0.0 else "right"
        grouped[(side, center_x, width, sill_z, height)].append(operation)

    specs: list[dict[str, object]] = []
    for (side, center_x, width, sill_z, height), hosts in sorted(grouped.items()):
        host_bounds = [item.bounds for item in hosts if item.bounds is not None]
        if not host_bounds:
            malformed.append(
                {
                    "window_id": f"east-{side}-window",
                    "reason": "window host pieces have no AABB",
                }
            )
            continue
        host = _union_bounds(item for item in host_bounds if item is not None)
        extents = [host.maximum[axis] - host.minimum[axis] for axis in range(3)]
        normal_axis = min(range(3), key=lambda axis: (extents[axis], axis))
        if normal_axis != 1:
            malformed.append(
                {
                    "window_id": f"east-{side}-window",
                    "reason": "window_clear contract requires a thin east-wall Y host",
                }
            )
            continue
        explicit_depths: set[float] = set()
        try:
            explicit_depths = {
                _finite_number(
                    item.parameters["window_clear"].get("interior_target_depth"),
                    "interior_target_depth",
                )
                for item in hosts
                if isinstance(item.parameters.get("window_clear"), Mapping)
                and item.parameters["window_clear"].get("interior_target_depth")
                is not None
            }
        except ValueError as exc:
            malformed.append(
                {
                    "window_id": f"east-{side}-window",
                    "reason": str(exc),
                }
            )
            continue
        if len(explicit_depths) > 1:
            malformed.append(
                {
                    "window_id": f"east-{side}-window",
                    "reason": "window host pieces disagree on interior_target_depth",
                }
            )
            continue
        specs.append(
            {
                "window_id": f"east-{side}-window",
                "center_x": center_x,
                "width": width,
                "sill_z": sill_z,
                "height": height,
                "host": host,
                "normal_axis": normal_axis,
                "explicit_target_depth": (
                    next(iter(explicit_depths)) if explicit_depths else None
                ),
                "host_operation_ids": sorted(item.operation_id for item in hosts),
            }
        )
    return specs, sorted(
        malformed,
        key=lambda item: (str(item.get("window_id", "")), str(item.get("operation_id", ""))),
    )


def _window_projection_receipt(
    operations: Sequence[_Operation],
) -> tuple[dict[str, object], list[str], list[dict[str, object]]]:
    windows, malformed = _window_specs(operations)
    interior_shafts = [
        item
        for item in operations
        if item.column_part_type == "shaft"
        and item.column_component_role == "interior"
        and item.bounds is not None
    ]
    failures: list[str] = []
    if not windows:
        failures.append("no typed east-window host-local aperture definitions were found")
    if malformed:
        failures.append(f"{len(malformed)} window aperture definitions are malformed")

    obstructions: list[dict[str, object]] = []
    evaluated_pairs = 0
    target_failures = 0
    window_summaries: list[dict[str, object]] = []
    for window in windows:
        host = window["host"]
        assert isinstance(host, _Bounds)
        normal_axis = int(window["normal_axis"])
        host_center = host.midpoint(normal_axis)
        signed_offsets = [
            column.bounds.midpoint(normal_axis) - host_center
            for column in interior_shafts
            if column.bounds is not None
        ]
        signed_sum = sum(signed_offsets)
        if not signed_offsets or math.isclose(signed_sum, 0.0, abs_tol=_TOLERANCE):
            target_failures += 1
            window_summaries.append(
                {
                    "window_id": window["window_id"],
                    "target_status": "interior_direction_unresolved",
                    "evaluated_projection_pair_count": 0,
                }
            )
            continue
        direction = 1 if signed_sum > 0.0 else -1
        face = host.maximum[normal_axis] if direction > 0 else host.minimum[normal_axis]
        depth_by_column: list[tuple[float, _Operation]] = []
        for column in interior_shafts:
            assert column.bounds is not None
            center_depth = direction * (column.bounds.midpoint(normal_axis) - face)
            if center_depth > _TOLERANCE:
                depth_by_column.append((center_depth, column))
        if not depth_by_column:
            target_failures += 1
            window_summaries.append(
                {
                    "window_id": window["window_id"],
                    "target_status": "no_inward_interior_column",
                    "evaluated_projection_pair_count": 0,
                }
            )
            continue
        explicit_depth = window["explicit_target_depth"]
        target_depth = (
            float(explicit_depth)
            if explicit_depth is not None
            else min(item[0] for item in depth_by_column)
        )
        if target_depth <= 0.0:
            target_failures += 1
            window_summaries.append(
                {
                    "window_id": window["window_id"],
                    "target_status": "nonpositive_target_depth",
                    "evaluated_projection_pair_count": 0,
                }
            )
            continue

        window_bounds = _Bounds(
            minimum=(
                float(window["center_x"]) - float(window["width"]) / 2.0,
                host.minimum[1],
                float(window["sill_z"]),
            ),
            maximum=(
                float(window["center_x"]) + float(window["width"]) / 2.0,
                host.maximum[1],
                float(window["sill_z"]) + float(window["height"]),
            ),
            exact_solid=False,
        )
        local_evaluated = 0
        for center_depth, column in sorted(
            depth_by_column, key=lambda item: (item[0], item[1].operation_id)
        ):
            assert column.bounds is not None
            near_depth = (
                column.bounds.minimum[normal_axis] - face
                if direction > 0
                else face - column.bounds.maximum[normal_axis]
            )
            far_depth = (
                column.bounds.maximum[normal_axis] - face
                if direction > 0
                else face - column.bounds.minimum[normal_axis]
            )
            if far_depth < -_TOLERANCE or near_depth > target_depth + _TOLERANCE:
                continue
            local_evaluated += 1
            evaluated_pairs += 1
            horizontal_overlap = (
                min(window_bounds.maximum[0], column.bounds.maximum[0])
                - max(window_bounds.minimum[0], column.bounds.minimum[0])
            )
            vertical_overlap = (
                min(window_bounds.maximum[2], column.bounds.maximum[2])
                - max(window_bounds.minimum[2], column.bounds.minimum[2])
            )
            if horizontal_overlap <= _TOLERANCE or vertical_overlap <= _TOLERANCE:
                continue
            obstructions.append(
                {
                    "window_id": window["window_id"],
                    "column_operation_id": column.operation_id,
                    "column_kind": column.kind,
                    "column_component_id": column.component_id,
                    "horizontal_projection_overlap_m": _rounded(horizontal_overlap),
                    "vertical_projection_overlap_m": _rounded(vertical_overlap),
                    "projected_overlap_area_m2": _rounded(
                        horizontal_overlap * vertical_overlap
                    ),
                    "normal_near_depth_m": _rounded(near_depth),
                    "interior_target_depth_m": _rounded(target_depth),
                    "host_volume_intersection_m3": _rounded(
                        _intersection_volume(window_bounds, column.bounds)
                    ),
                    "classification": "host_local_clear_cone_obstruction",
                }
            )
        window_summaries.append(
            {
                "window_id": window["window_id"],
                "normal_axis": "y",
                "normal_direction": direction,
                "interior_target_depth_m": _rounded(target_depth),
                "target_depth_basis": (
                    "explicit_window_clear_contract"
                    if explicit_depth is not None
                    else "nearest_interior_column_center"
                ),
                "evaluated_projection_pair_count": local_evaluated,
            }
        )

    if target_failures:
        failures.append(
            f"{target_failures} window clear-cone targets could not be resolved"
        )
    if obstructions:
        failures.append(
            f"window host-local projection found {len(obstructions)} column obstructions"
        )
    obstructions.sort(
        key=lambda item: (str(item["window_id"]), str(item["column_operation_id"]))
    )
    return (
        {
            "window_denominator": len(windows),
            "interior_shaft_denominator": len(interior_shafts),
            "projection_pair_denominator": len(windows) * len(interior_shafts),
            "evaluated_projection_pair_count": evaluated_pairs,
            "obstruction_count": len(obstructions),
            "target_resolution_failure_count": target_failures,
            "windows": window_summaries,
            "method": "host-local-aperture-projection-to-interior-target-depth",
            "ordinary_volume_collision_substitute": False,
        },
        failures,
        [*malformed, *obstructions[:_DETAIL_LIMIT]],
    )


def _xy_overlap(first: _Bounds, second: _Bounds) -> bool:
    return all(
        min(first.maximum[axis], second.maximum[axis])
        - max(first.minimum[axis], second.minimum[axis])
        > _TOLERANCE
        for axis in (0, 1)
    )


def _bridge_path(
    lower: _Operation,
    upper: _Operation,
    operations: Sequence[_Operation],
) -> list[_Operation] | None:
    assert lower.bounds is not None and upper.bounds is not None
    candidates = [
        item
        for item in operations
        if item.bounds is not None
        and item.component_id == lower.component_id == upper.component_id
        and item.kind in STACK_BRIDGE_KINDS
        and _xy_overlap(lower.bounds, item.bounds)
        and _xy_overlap(item.bounds, upper.bounds)
        and item.bounds.minimum[2] >= lower.bounds.maximum[2] - _TOLERANCE
        and item.bounds.maximum[2] <= upper.bounds.minimum[2] + _TOLERANCE
    ]
    cursor = lower.bounds.maximum[2]
    path: list[_Operation] = []
    remaining = sorted(
        candidates,
        key=lambda item: (item.bounds.minimum[2], item.bounds.maximum[2], item.operation_id),  # type: ignore[union-attr]
    )
    while cursor < upper.bounds.minimum[2] - _TOLERANCE:
        next_items = [
            item
            for item in remaining
            if item.bounds is not None
            and math.isclose(item.bounds.minimum[2], cursor, abs_tol=_TOLERANCE)
        ]
        if not next_items:
            return None
        selected = max(
            next_items,
            key=lambda item: (item.bounds.maximum[2], item.operation_id),  # type: ignore[union-attr]
        )
        assert selected.bounds is not None
        if selected.bounds.maximum[2] <= cursor + _TOLERANCE:
            return None
        path.append(selected)
        cursor = selected.bounds.maximum[2]
        remaining.remove(selected)
    if not math.isclose(cursor, upper.bounds.minimum[2], abs_tol=_TOLERANCE):
        return None
    return path


def _stack_receipt(
    operations: Sequence[_Operation],
) -> tuple[dict[str, object], list[str], list[dict[str, object]]]:
    shafts = [
        item
        for item in operations
        if item.column_part_type == "shaft"
        and item.column_component_role == "interior"
        and item.bounds is not None
    ]
    groups: defaultdict[tuple[float, float], list[_Operation]] = defaultdict(list)
    for shaft in shafts:
        assert shaft.bounds is not None
        groups[
            (
                round(shaft.bounds.midpoint(0), 6),
                round(shaft.bounds.midpoint(1), 6),
            )
        ].append(shaft)

    denominator = 0
    valid_count = 0
    direct_count = 0
    missing_bridge_count = 0
    overlap_count = 0
    details: list[dict[str, object]] = []
    for center, members in sorted(groups.items()):
        ordered = sorted(
            members,
            key=lambda item: (item.bounds.minimum[2], item.operation_id),  # type: ignore[union-attr]
        )
        for lower, upper in zip(ordered, ordered[1:]):
            assert lower.bounds is not None and upper.bounds is not None
            denominator += 1
            gap = upper.bounds.minimum[2] - lower.bounds.maximum[2]
            detail = {
                "center_xy_m": [_rounded(center[0]), _rounded(center[1])],
                "lower_operation_id": lower.operation_id,
                "upper_operation_id": upper.operation_id,
                "vertical_gap_m": _rounded(gap),
            }
            if gap < -_TOLERANCE:
                overlap_count += 1
                detail["classification"] = "shaft_stack_overlap"
                details.append(detail)
                continue
            if math.isclose(gap, 0.0, abs_tol=_TOLERANCE):
                direct_count += 1
                detail["classification"] = "direct_shaft_to_shaft_contact"
                details.append(detail)
                continue
            path = _bridge_path(lower, upper, operations)
            if path is None:
                missing_bridge_count += 1
                detail["classification"] = "missing_capital_or_support_bridge"
                details.append(detail)
                continue
            valid_count += 1
            detail["classification"] = "lower_capital_or_support_upper"
            detail["bridge_operation_ids"] = [item.operation_id for item in path]
            details.append(detail)

    failures: list[str] = []
    if overlap_count:
        failures.append(f"{overlap_count} interior column stacks have shaft overlap")
    if direct_count:
        failures.append(
            f"{direct_count} interior column stacks use direct shaft-to-shaft contact"
        )
    if missing_bridge_count:
        failures.append(
            f"{missing_bridge_count} interior column stacks lack a typed capital/support bridge"
        )
    details.sort(
        key=lambda item: (
            tuple(item["center_xy_m"]),
            str(item["lower_operation_id"]),
            str(item["upper_operation_id"]),
        )
    )
    return (
        {
            "stack_pair_denominator": denominator,
            "valid_lower_support_upper_count": valid_count,
            "direct_shaft_contact_count": direct_count,
            "missing_bridge_count": missing_bridge_count,
            "shaft_overlap_count": overlap_count,
            "method": "typed-interior-column-vertical-stack",
        },
        failures,
        details[:_DETAIL_LIMIT],
    )


def _model_boundary_receipt(
    model_path: str | Path | None,
    operations: Sequence[_Operation],
    required_narrow_phase_refs: Sequence[str],
) -> tuple[dict[str, object], list[str]]:
    failures: list[str] = []
    base: dict[str, object] = {
        "model_supplied": model_path is not None,
        "narrow_phase_intersection_available": False,
        "narrow_phase_ref_required": bool(required_narrow_phase_refs),
        "required_narrow_phase_ref_count": len(required_narrow_phase_refs),
        "required_narrow_phase_refs": list(required_narrow_phase_refs[:_DETAIL_LIMIT]),
        "boundary": (
            "rhino3dm exposes object bounding boxes but no pairwise Brep/Mesh "
            "boolean-intersection API; curved/sloped broad-phase candidates cannot be cleared here"
        ),
    }
    if model_path is None:
        base.update(
            {
                "status": "not_requested",
                "object_denominator": 0,
                "mapped_operation_count": 0,
            }
        )
        return base, failures

    path = Path(model_path)
    if not path.is_file():
        base.update(
            {
                "status": "unreadable",
                "object_denominator": 0,
                "mapped_operation_count": 0,
            }
        )
        failures.append("optional 3DM path is not a readable file")
        return base, failures
    try:
        import rhino3dm  # type: ignore

        model = rhino3dm.File3dm.Read(str(path))
    except Exception as exc:  # pragma: no cover - package/read failures vary by host
        base.update(
            {
                "status": "unreadable",
                "object_denominator": 0,
                "mapped_operation_count": 0,
                "read_error_type": type(exc).__name__,
            }
        )
        failures.append("optional 3DM could not be read")
        return base, failures
    if model is None:
        base.update(
            {
                "status": "unreadable",
                "object_denominator": 0,
                "mapped_operation_count": 0,
            }
        )
        failures.append("optional 3DM could not be read")
        return base, failures

    mapped_ids: list[str] = []
    for item in model.Objects:
        strings = dict(item.Attributes.GetUserStrings() or ())
        operation_id = str(strings.get("archflow:operation_id", "")).strip()
        if operation_id:
            mapped_ids.append(operation_id)
    expected_ids = {item.operation_id for item in operations}
    mapped_set = set(mapped_ids)
    duplicate_count = len(mapped_ids) - len(mapped_set)
    missing = sorted(expected_ids - mapped_set)
    extra = sorted(mapped_set - expected_ids)
    if duplicate_count or missing or extra:
        failures.append("optional 3DM operation identity does not bijectively match the successor")
    base.update(
        {
            "status": "identity_checked_aabb_only",
            "object_denominator": len(model.Objects),
            "mapped_operation_count": len(mapped_ids),
            "duplicate_operation_mapping_count": duplicate_count,
            "missing_operation_mapping_count": len(missing),
            "extra_operation_mapping_count": len(extra),
            "missing_operation_mapping_examples": missing[:_DETAIL_LIMIT],
            "extra_operation_mapping_examples": extra[:_DETAIL_LIMIT],
        }
    )
    return base, failures


def validate_stage4_successor_relations(
    operations: Sequence[Mapping[str, object]],
    *,
    model_path: str | Path | None = None,
    typed_contact_allowlist: Sequence[Mapping[str, str]] = (),
    typed_relation_contracts: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Validate relational obligations across the complete Stage 4 successor.

    ``typed_contact_allowlist`` remains accepted for receipt compatibility, but
    its broad component/kind rules do not authorize geometry.  Contact and
    bounded overlap require an exact-operation contract, supplied either by
    operation ``host``/``contact_contract``/``contact_policy`` metadata or by
    ``typed_relation_contracts``.
    """

    parsed, parse_failures, malformed = _parse_operations(operations)
    contact_rules, contact_rule_failures = _parse_contact_rules(typed_contact_allowlist)
    relation_contracts, relation_contract_failures, contract_source_counts = (
        _normalize_relation_contracts(parsed, typed_relation_contracts)
    )

    broad_phase, broad_failures, broad_details, narrow_refs = _broad_phase_receipt(
        parsed, contact_rules, relation_contracts
    )
    window_projection, window_failures, window_details = _window_projection_receipt(parsed)
    stacks, stack_failures, stack_details = _stack_receipt(parsed)
    model_boundary, model_failures = _model_boundary_receipt(
        model_path, parsed, narrow_refs
    )

    column_parts = [item for item in parsed if item.column_part_type is not None]
    unregistered_column_components = sorted(
        {
            item.component_id
            for item in column_parts
            if item.column_component_role is None
        }
    )
    classification_failures: list[str] = []
    if unregistered_column_components:
        classification_failures.append(
            "typed column kinds use unregistered component ids: "
            + ", ".join(unregistered_column_components)
        )

    failures = [
        *parse_failures,
        *contact_rule_failures,
        *relation_contract_failures,
        *classification_failures,
        *broad_failures,
        *window_failures,
        *stack_failures,
        *model_failures,
    ]
    passed = not failures
    return {
        "schema": RECEIPT_SCHEMA,
        "passed": passed,
        "status": "PASSED" if passed else "FAILED",
        "checks": {
            "operation_schema": {
                "operation_denominator": len(operations),
                "parsed_operation_count": len(parsed),
                "aabb_operation_count": sum(item.bounds is not None for item in parsed),
                "malformed_operation_count": len(malformed),
            },
            "column_classification": {
                "operation_denominator": len(parsed),
                "typed_column_part_count": len(column_parts),
                "typed_shaft_count": sum(
                    item.column_part_type == "shaft" for item in column_parts
                ),
                "kind_counts": dict(
                    sorted(Counter(item.kind for item in column_parts).items())
                ),
                "component_role_counts": dict(
                    sorted(
                        Counter(
                            item.column_component_role or "unregistered"
                            for item in column_parts
                        ).items()
                    )
                ),
                "unregistered_component_ids": unregistered_column_components,
                "classification_basis": "operation.kind + operation.component_id",
                "operation_id_prefix_used": False,
            },
            "successor_pair_broad_phase": broad_phase,
            "window_projection": window_projection,
            "interior_column_stacks": stacks,
            "model_narrow_phase_boundary": model_boundary,
            "typed_contact_allowlist": {
                "allowlist_denominator": len(typed_contact_allowlist),
                "valid_rule_count": len(contact_rules),
                "rules": [rule.to_mapping() for rule in contact_rules],
                "authorization_effect": "audit_only_legacy_compatibility",
                "authorizes_geometry": False,
            },
            "exact_relation_contracts": {
                "argument_denominator": len(typed_relation_contracts),
                "normalized_exact_pair_count": len(relation_contracts),
                "source_counts": contract_source_counts,
                "wildcards_permitted": False,
                "contracts": [
                    relation_contracts[pair].to_mapping()
                    for pair in sorted(relation_contracts)
                ],
            },
        },
        "failure_details": {
            "operation_schema": malformed[:_DETAIL_LIMIT],
            "successor_pair_broad_phase": broad_details,
            "window_projection": window_details,
            "interior_column_stacks": stack_details,
        },
        "failure_count": len(failures),
        "failures": failures,
    }


# A shorter name is useful to callers that already bind the Stage 4 context.
validate_successor_relations = validate_stage4_successor_relations


__all__ = [
    "COLUMN_COMPONENT_ROLES",
    "COLUMN_PART_KIND_TYPES",
    "RECEIPT_SCHEMA",
    "SHAFT_KIND_TYPES",
    "STACK_BRIDGE_KINDS",
    "validate_stage4_successor_relations",
    "validate_successor_relations",
]
