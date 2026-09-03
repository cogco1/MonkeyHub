"""Compile exact-pair relation contracts for the complete Parthenon Stage 4.

Rules in this module start from architectural assembly semantics (host graph,
component role, member kind, material role, and door aperture role).  Geometry
is consulted only after a semantic rule has selected a candidate pair.  The
compiler never consumes validator failures and never emits wildcard endpoints.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
from typing import Mapping, Sequence

try:  # Package import in tests; direct import when executed by the runner.
    from archive.tools import parthenon_stage4_relations as relations
except ModuleNotFoundError:  # pragma: no cover - direct CLI import path
    import parthenon_stage4_relations as relations


SCHEMA = "ParthenonStage4ExactRelationContractSet@1"
EXPECTED_FULL_OPERATION_COUNT = 687
_BOUND_RELATIVE_TOLERANCE = 1.0e-6
_BOUND_ABSOLUTE_TOLERANCE_M3 = 1.0e-9


@dataclass(frozen=True)
class _AssemblyRule:
    rule_id: str
    relation: str
    positive_policy: str
    rationale: str


@dataclass(frozen=True)
class _RoofSignature:
    group: str
    family: str
    side: str | None


_CELLA_MASONRY_JOINTS = {
    frozenset(pair)
    for pair in (
        ("cella-partition", "cella-wall-north"),
        ("cella-partition", "cella-wall-south"),
        (
            "cella-wall-east-left-window-inner-pier",
            "cella-wall-east-left-window-lintel",
        ),
        (
            "cella-wall-east-left-window-inner-pier",
            "cella-wall-east-left-window-sill",
        ),
        ("cella-wall-east-left-window-inner-pier", "cella-wall-east-lintel"),
        (
            "cella-wall-east-left-window-lintel",
            "cella-wall-east-left-window-outer-pier",
        ),
        (
            "cella-wall-east-left-window-outer-pier",
            "cella-wall-east-left-window-sill",
        ),
        ("cella-wall-east-left-window-outer-pier", "cella-wall-south"),
        ("cella-wall-east-lintel", "cella-wall-east-right-window-outer-pier"),
        (
            "cella-wall-east-right-window-inner-pier",
            "cella-wall-east-right-window-lintel",
        ),
        (
            "cella-wall-east-right-window-inner-pier",
            "cella-wall-east-right-window-sill",
        ),
        ("cella-wall-east-right-window-inner-pier", "cella-wall-north"),
        (
            "cella-wall-east-right-window-lintel",
            "cella-wall-east-right-window-outer-pier",
        ),
        (
            "cella-wall-east-right-window-outer-pier",
            "cella-wall-east-right-window-sill",
        ),
        ("cella-wall-north", "cella-wall-west-right"),
        ("cella-wall-south", "cella-wall-west-left"),
        ("cella-wall-west-left", "cella-wall-west-lintel"),
        ("cella-wall-west-lintel", "cella-wall-west-right"),
    )
}

_INTERIOR_U_JOINTS = {
    frozenset(pair)
    for pair in (
        ("naos-north-00-lower-abacus", "naos-u-architrave-west"),
        ("naos-south-00-lower-abacus", "naos-u-architrave-west"),
        ("naos-u-architrave-north", "naos-u-architrave-west"),
        ("naos-u-architrave-south", "naos-u-architrave-west"),
    )
}

_PEDIMENT_MEMBER_PAIRS = {
    frozenset(pair)
    for pair in (
        ("acroterion-seat-apex", "raking-sima-left"),
        ("acroterion-seat-apex", "raking-sima-right"),
        ("acroterion-seat-apex", "tympanum"),
        ("acroterion-seat-left", "raking-geison-left"),
        ("acroterion-seat-left", "raking-sima-left"),
        ("acroterion-seat-left", "tympanum"),
        ("acroterion-seat-right", "raking-geison-right"),
        ("acroterion-seat-right", "raking-sima-right"),
        ("acroterion-seat-right", "tympanum"),
        ("horizontal-geison", "tympanum"),
        ("raking-geison-left", "raking-geison-right"),
        ("raking-geison-left", "raking-sima-left"),
        ("raking-geison-left", "raking-sima-right"),
        ("raking-geison-left", "tympanum"),
        ("raking-geison-right", "raking-sima-left"),
        ("raking-geison-right", "raking-sima-right"),
        ("raking-geison-right", "tympanum"),
        ("raking-sima-left", "raking-sima-right"),
        ("raking-sima-left", "tympanum"),
        ("raking-sima-right", "tympanum"),
    )
}

_ROOF_SAME_SLOPE_FAMILY_PAIRS = {
    frozenset(pair)
    for pair in (
        ("cover-tile-field", "eave-terminal"),
        ("cover-tile-field", "pan-tile-field"),
        ("cover-tile-field", "rafter-field"),
        ("eave-terminal", "pan-tile-field"),
        ("eave-terminal", "rafter-field"),
        ("pan-tile-field", "rafter-field"),
        ("pan-tile-field", "timber-bearing"),
        ("rafter-field", "timber-bearing"),
    )
}

_PEDIMENT_ROOF_FAMILIES = {
    "acroterion-seat-apex": {"cover-tile-field", "ridge-terminal"},
    "acroterion-seat-left": {"pan-tile-field", "rafter-field"},
    "acroterion-seat-right": {"pan-tile-field", "rafter-field"},
    "raking-geison-left": {
        "cover-tile-field",
        "pan-tile-field",
        "rafter-field",
        "ridge-terminal",
        "timber-ridge",
    },
    "raking-geison-right": {
        "cover-tile-field",
        "pan-tile-field",
        "rafter-field",
        "ridge-terminal",
        "timber-ridge",
    },
    "raking-sima-left": {
        "cover-tile-field",
        "pan-tile-field",
        "rafter-field",
        "ridge-terminal",
        "timber-ridge",
    },
    "raking-sima-right": {
        "cover-tile-field",
        "pan-tile-field",
        "rafter-field",
        "ridge-terminal",
        "timber-ridge",
    },
    "tympanum": {
        "cover-tile-field",
        "pan-tile-field",
        "rafter-field",
        "ridge-terminal",
        "timber-ridge",
    },
}


def _raw(operation: object) -> Mapping[str, object]:
    value = getattr(operation, "raw", None)
    if not isinstance(value, Mapping):
        raise TypeError("parsed operation lacks its source mapping")
    return value


def _parameters(operation: object) -> Mapping[str, object]:
    value = getattr(operation, "parameters", None)
    if not isinstance(value, Mapping):
        raise TypeError("parsed operation lacks parameters")
    return value


def _component(operation: object) -> str:
    return str(getattr(operation, "component_id"))


def _kind(operation: object) -> str:
    return str(getattr(operation, "kind"))


def _operation_id(operation: object) -> str:
    return str(getattr(operation, "operation_id"))


def _material_role(operation: object) -> str:
    return str(_raw(operation).get("material_role", ""))


def _decision_refs(operation: object) -> set[str]:
    value = _raw(operation).get("decision_refs", ())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {str(item) for item in value}


def _roof_scoped(operation: object) -> bool:
    return (
        _raw(operation).get("delta_scope")
        == "parthenon-stage4-roof-eaves-pediment"
    )


def _door_role(operation: object) -> str:
    return str(_parameters(operation).get("assembly_role", ""))


def _pair_ids(first: object, second: object) -> frozenset[str]:
    return frozenset((_operation_id(first), _operation_id(second)))


def _cella_member_sides(operation: object) -> frozenset[str] | None:
    if _component(operation) != "cella" or _kind(operation) != "box":
        return None
    operation_id = _operation_id(operation)
    fixed = {
        "cella-partition": frozenset(("north", "south")),
        "cella-wall-north": frozenset(("north", "east", "west")),
        "cella-wall-south": frozenset(("south", "east", "west")),
        "cella-wall-east-lintel": frozenset(("east",)),
        "cella-wall-west-left": frozenset(("west", "south")),
        "cella-wall-west-lintel": frozenset(("west",)),
        "cella-wall-west-right": frozenset(("west", "north")),
    }
    if operation_id in fixed:
        return fixed[operation_id]
    match = re.fullmatch(
        r"cella-wall-east-(left|right)-window-(inner-pier|outer-pier|lintel|sill)",
        operation_id,
    )
    if match is None:
        return None
    sides = {"east"}
    if match.group(1) == "left" and match.group(2) == "outer-pier":
        sides.add("south")
    if match.group(1) == "right" and match.group(2) == "inner-pier":
        sides.add("north")
    return frozenset(sides)


def _doric_decoration_signature(
    operation: object,
) -> tuple[str, str, int, int] | None:
    match = re.fullmatch(
        r"(metope|triglyph)-(east|north|south|west)-(\d{2})",
        _operation_id(operation),
    )
    if match is None or _component(operation) != "decoration":
        return None
    family, side, index_text = match.groups()
    expected_kind = "box" if family == "metope" else "triglyph"
    if _kind(operation) != expected_kind:
        return None
    maximum = 13 if family == "metope" and side in {"east", "west"} else None
    if family == "metope" and side in {"north", "south"}:
        maximum = 31
    if family == "triglyph" and side in {"east", "west"}:
        maximum = 14
    if family == "triglyph" and side in {"north", "south"}:
        maximum = 32
    assert maximum is not None
    index = int(index_text)
    if index > maximum:
        return None
    return family, side, index, maximum


def _member_meets_side(
    signature: tuple[str, str, int, int], host_side: str
) -> bool:
    _, side, index, maximum = signature
    if side == host_side:
        return True
    if index == 0:
        adjacent = "south" if side in {"east", "west"} else "west"
        if host_side == adjacent:
            return True
    if index == maximum:
        adjacent = "north" if side in {"east", "west"} else "east"
        if host_side == adjacent:
            return True
    return False


def _entablature_signature(operation: object) -> tuple[str, str] | None:
    if _component(operation) != "entablature" or _kind(operation) != "entablature_layer":
        return None
    parameters = _parameters(operation)
    side = str(parameters.get("side", ""))
    layer = str(parameters.get("layer", ""))
    if side not in {"east", "north", "south", "west"} or layer not in {
        "architrave",
        "frieze",
    }:
        return None
    if _operation_id(operation) != f"entablature-{side}-{layer}":
        return None
    return side, layer


def _ionic_frieze_side(operation: object) -> str | None:
    match = re.fullmatch(
        r"ionic-frieze-(east|north|south|west)", _operation_id(operation)
    )
    if (
        match is None
        or _component(operation) != "decoration"
        or _kind(operation) != "box"
        or "decision:decorative-layout" not in _decision_refs(operation)
    ):
        return None
    return match.group(1)


def _orthogonal_end_sides(first: str, second: str) -> bool:
    return frozenset((first, second)) in {
        frozenset(("east", "north")),
        frozenset(("east", "south")),
        frozenset(("west", "north")),
        frozenset(("west", "south")),
    }


def _recognized_doric_root(operation_id: str, component_id: str) -> bool:
    match = re.fullmatch(r"peristyle-(east|north|south|west)-(\d{2})", operation_id)
    if match is not None and component_id == "peristyle":
        side, index_text = match.groups()
        index = int(index_text)
        return index <= 7 if side in {"east", "west"} else 1 <= index <= 15
    match = re.fullmatch(r"porch-(east|west)-(\d{2})", operation_id)
    if match is not None and component_id == "porches":
        return int(match.group(2)) <= 5
    match = re.fullmatch(r"naos-(north|south|west)-(\d{2})-(lower|upper)", operation_id)
    if match is not None and component_id == "interior-colonnade":
        row, index_text, _ = match.groups()
        index = int(index_text)
        return index <= (2 if row == "west" else 9)
    return False


def _peristyle_abacus_side(operation: object) -> tuple[str, int] | None:
    if _component(operation) != "peristyle" or _kind(operation) != "doric_abacus":
        return None
    operation_id = _operation_id(operation)
    if not operation_id.endswith("-abacus"):
        return None
    root = operation_id.removesuffix("-abacus")
    if not _recognized_doric_root(root, "peristyle"):
        return None
    match = re.fullmatch(r"peristyle-(east|north|south|west)-(\d{2})", root)
    assert match is not None
    return match.group(1), int(match.group(2))


def _peristyle_abacus_meets_side(
    signature: tuple[str, int], host_side: str
) -> bool:
    side, index = signature
    if side == host_side:
        return True
    if side in {"east", "west"} and index == 0 and host_side == "south":
        return True
    return side in {"east", "west"} and index == 7 and host_side == "north"


def _door_signature(operation: object) -> tuple[str, str] | None:
    side = _component(operation).removeprefix("door-")
    role = _door_role(operation)
    if side not in {"east", "west"} or role not in {
        "frame-header",
        "frame-jamb-left",
        "frame-jamb-right",
        "frame-threshold",
        "leaf-left",
        "leaf-right",
    }:
        return None
    if _operation_id(operation) != f"principal-door-{side}-{role}":
        return None
    return side, role


def _roof_contact_neighbors(operation: object) -> tuple[str, ...]:
    values: list[str] = []
    host_id = _raw(operation).get("host_id")
    if host_id is not None:
        values.append(str(host_id))
    policy = _raw(operation).get("contact_policy")
    if isinstance(policy, Mapping):
        cohosts = policy.get("co_host_ids", ())
        if isinstance(cohosts, Sequence) and not isinstance(cohosts, (str, bytes)):
            values.extend(str(item) for item in cohosts)
    return tuple(values)


def _roof_signature(operation: object) -> _RoofSignature | None:
    if not _roof_scoped(operation):
        return None
    operation_id = _operation_id(operation)
    component_id = _component(operation)
    kind = _kind(operation)
    role = _material_role(operation)
    parameters = _parameters(operation)
    expected_host: str
    expected_cohosts: tuple[str, ...] = ()

    match = re.fullmatch(r"eave-(north|south)-(geison|sima)", operation_id)
    if match is not None:
        side, family = match.groups()
        expected_kind = "eave_geison" if family == "geison" else "eave_sima"
        expected_host = (
            f"entablature-{side}-frieze"
            if family == "geison"
            else f"eave-{side}-geison"
        )
        if (
            component_id != "entablature"
            or kind != expected_kind
            or role != "structural_marble"
            or parameters.get("side") != side
        ):
            return None
        signature = _RoofSignature("eave", family, side)
    else:
        match = re.fullmatch(
            r"pediment-(east|west)-(horizontal-geison|tympanum|raking-geison-(?:left|right)|raking-sima-(?:left|right)|acroterion-seat-(?:apex|left|right))",
            operation_id,
        )
        if match is not None:
            side, family = match.groups()
            if family == "horizontal-geison":
                expected_kind = "pediment_horizontal_geison"
                expected_host = f"entablature-{side}-frieze"
            elif family == "tympanum":
                expected_kind = "pediment_tympanum"
                expected_host = f"pediment-{side}-horizontal-geison"
            elif family.startswith("raking-geison-"):
                expected_kind = "pediment_raking_geison"
                expected_host = f"pediment-{side}-tympanum"
            elif family.startswith("raking-sima-"):
                flank = family.rsplit("-", 1)[1]
                expected_kind = "pediment_raking_sima"
                expected_host = f"pediment-{side}-raking-geison-{flank}"
            else:
                seat = family.rsplit("-", 1)[1]
                expected_kind = "acroterion_seat"
                flank = "left" if seat == "apex" else seat
                expected_host = f"pediment-{side}-raking-sima-{flank}"
                if seat == "apex":
                    expected_cohosts = (f"pediment-{side}-raking-sima-right",)
            if (
                component_id != "pediments"
                or kind != expected_kind
                or role != "structural_marble"
                or parameters.get("side") != side
            ):
                return None
            signature = _RoofSignature("pediment", family, side)
        else:
            match = re.fullmatch(
                r"roof-(cover-tile-field|pan-tile-field|rafter-field|eave-terminal|timber-bearing)-(north|south)",
                operation_id,
            )
            if match is not None:
                family, side = match.groups()
                kind_role_host = {
                    "cover-tile-field": (
                        "marble_cover_tile_field",
                        "roof_tile",
                        f"roof-pan-tile-field-{side}",
                    ),
                    "pan-tile-field": (
                        "marble_pan_tile_field",
                        "roof_tile",
                        f"roof-rafter-field-{side}",
                    ),
                    "rafter-field": (
                        "timber_rafter_field",
                        "timber_frame",
                        f"roof-timber-bearing-{side}",
                    ),
                    "eave-terminal": (
                        "marble_eave_terminal",
                        "roof_tile",
                        f"roof-cover-tile-field-{side}",
                    ),
                    "timber-bearing": (
                        "timber_bearing_beam",
                        "timber_frame",
                        f"eave-{side}-geison",
                    ),
                }
                expected_kind, expected_role, expected_host = kind_role_host[family]
                if family == "rafter-field":
                    expected_cohosts = ("roof-timber-ridge",)
                elif family == "eave-terminal":
                    expected_cohosts = (f"eave-{side}-sima",)
                if component_id != "roof" or kind != expected_kind or role != expected_role:
                    return None
                signature = _RoofSignature("roof", family, side)
            elif operation_id == "roof-ridge-terminal":
                expected_host = "roof-cover-tile-field-north"
                expected_cohosts = ("roof-cover-tile-field-south",)
                if component_id != "roof" or kind != "marble_ridge_terminal" or role != "roof_tile":
                    return None
                signature = _RoofSignature("roof", "ridge-terminal", None)
            elif operation_id == "roof-timber-ridge":
                expected_host = "roof-timber-bearing-north"
                expected_cohosts = ("roof-timber-bearing-south",)
                if component_id != "roof" or kind != "timber_ridge_beam" or role != "timber_frame":
                    return None
                signature = _RoofSignature("roof", "timber-ridge", None)
            else:
                return None

    neighbors = _roof_contact_neighbors(operation)
    if not neighbors or neighbors[0] != expected_host:
        return None
    if tuple(neighbors[1:]) != expected_cohosts:
        return None
    return signature


def _roof_policy_rule(first: object, second: object) -> _AssemblyRule | None:
    first_signature = _roof_signature(first)
    second_signature = _roof_signature(second)
    if first_signature is None or second_signature is None:
        return None

    signatures = (first_signature, second_signature)
    groups = {item.group for item in signatures}
    allowed = False
    if groups == {"eave"}:
        allowed = (
            first_signature.side == second_signature.side
            and {first_signature.family, second_signature.family} == {"geison", "sima"}
        )
    elif groups == {"eave", "pediment"}:
        pediment = (
            first_signature if first_signature.group == "pediment" else second_signature
        )
        allowed = pediment.family in {"horizontal-geison", "tympanum"}
    elif groups == {"pediment"}:
        allowed = (
            first_signature.side == second_signature.side
            and frozenset((first_signature.family, second_signature.family))
            in _PEDIMENT_MEMBER_PAIRS
        )
    elif groups == {"roof"}:
        first_family = first_signature.family
        second_family = second_signature.family
        if first_signature.side == second_signature.side and first_signature.side is not None:
            allowed = (
                frozenset((first_family, second_family))
                in _ROOF_SAME_SLOPE_FAMILY_PAIRS
            )
        elif first_signature.side is not None and second_signature.side is not None:
            allowed = {first_family, second_family}.issubset(
                {"cover-tile-field", "pan-tile-field", "rafter-field"}
            )
        else:
            family_pair = frozenset((first_family, second_family))
            allowed = family_pair in {
                frozenset(("cover-tile-field", "ridge-terminal")),
                frozenset(("cover-tile-field", "timber-ridge")),
                frozenset(("pan-tile-field", "timber-ridge")),
                frozenset(("rafter-field", "timber-ridge")),
                frozenset(("ridge-terminal", "timber-ridge")),
            }
    elif groups == {"pediment", "roof"}:
        pediment = (
            first_signature if first_signature.group == "pediment" else second_signature
        )
        roof_member = (
            first_signature if first_signature.group == "roof" else second_signature
        )
        allowed = roof_member.family in _PEDIMENT_ROOF_FAMILIES.get(
            pediment.family, set()
        )
        if pediment.family == "acroterion-seat-left" and roof_member.side not in {
            "south",
            None,
        }:
            allowed = False
        if pediment.family == "acroterion-seat-right" and roof_member.side not in {
            "north",
            None,
        }:
            allowed = False

    if not allowed:
        return None
    if "roof_tile" in {_material_role(first), _material_role(second)}:
        return _AssemblyRule(
            "roof-layer-or-terminal-interface",
            "embedded_in",
            "bounded_embedded_finish",
            "exact roof host topology binds the tile layer or terminal to this endpoint pair",
        )
    return _AssemblyRule(
        "roof-pediment-structural-joint",
        "joins",
        "bounded_structural_union",
        "exact roof host topology and end/slope roles bind this structural endpoint pair",
    )


def _column_root(operation: object) -> str | None:
    parameters = _parameters(operation)
    if _kind(operation) == "doric_shaft":
        return _operation_id(operation)
    host = parameters.get("host_shaft_id")
    return str(host) if host is not None else None


def _column_sequence_rule(first: object, second: object) -> _AssemblyRule | None:
    column_components = {"interior-colonnade", "peristyle", "porches"}
    if _component(first) not in column_components or _component(second) != _component(first):
        return None
    first_root = _column_root(first)
    second_root = _column_root(second)
    component_id = _component(first)
    if (
        first_root is None
        or first_root != second_root
        or not _recognized_doric_root(first_root, component_id)
    ):
        return None
    expected_ids = {
        "doric_shaft": first_root,
        "doric_neck": f"{first_root}-neck",
        "doric_echinus": f"{first_root}-echinus",
        "doric_abacus": f"{first_root}-abacus",
    }
    if any(
        expected_ids.get(_kind(operation)) != _operation_id(operation)
        for operation in (first, second)
    ):
        return None
    pair = frozenset((_kind(first), _kind(second)))
    if pair in {
        frozenset(("doric_shaft", "doric_neck")),
        frozenset(("doric_neck", "doric_echinus")),
        frozenset(("doric_echinus", "doric_abacus")),
    }:
        return _AssemblyRule(
            "doric-column-vertical-sequence",
            "supports",
            "bounded_structural_union",
            "shaft, neck, echinus, and abacus form one typed vertical support",
        )
    return None


def _semantic_rule(first: object, second: object) -> _AssemblyRule | None:
    component_pair = frozenset((_component(first), _component(second)))
    kind_pair = frozenset((_kind(first), _kind(second)))

    column_rule = _column_sequence_rule(first, second)
    if column_rule is not None:
        return column_rule

    if (
        component_pair == {"base"}
        and kind_pair == {"box"}
        and _pair_ids(first, second)
        in {
            frozenset(("crepidoma-step-0", "crepidoma-step-1")),
            frozenset(("crepidoma-step-1", "crepidoma-step-2")),
        }
    ):
        return _AssemblyRule(
            "crepidoma-step-stack",
            "supports",
            "bounded_structural_union",
            "successive crepidoma courses bear on the course below",
        )

    if "base" in component_pair:
        base = first if _component(first) == "base" else second
        other = second if _component(first) == "base" else first
        other_component = _component(other)
        other_kind = _kind(other)
        recognized_member = (
            _cella_member_sides(other) is not None
            or (
                other_kind == "doric_shaft"
                and _recognized_doric_root(_operation_id(other), other_component)
            )
            or (
                other_component == "interior-colonnade"
                and other_kind == "ionic_column"
                and re.fullmatch(r"west-room-ionic-[01]-[01]", _operation_id(other))
                is not None
            )
            or _door_signature(other)
            in {("east", "frame-threshold"), ("west", "frame-threshold")}
        )
        if (
            _operation_id(base) == "crepidoma-step-2"
            and _kind(base) == "box"
            and recognized_member
        ):
            return _AssemblyRule(
                "stylobate-supported-member",
                "supports",
                "bounded_structural_union",
                "wall, column, or threshold bears on the top crepidoma course",
            )

    if (
        component_pair == {"cella"}
        and kind_pair == {"box"}
        and _pair_ids(first, second) in _CELLA_MASONRY_JOINTS
    ):
        return _AssemblyRule(
            "cella-masonry-joint",
            "joins",
            "bounded_structural_union",
            "typed cella wall, partition, lintel, sill, and pier members form masonry joints",
        )

    if component_pair == {"cella", "decoration"} and "box" in kind_pair:
        decoration = first if _component(first) == "decoration" else second
        cella = second if decoration is first else first
        frieze_side = _ionic_frieze_side(decoration)
        cella_sides = _cella_member_sides(cella)
        if (
            frieze_side is not None
            and cella_sides is not None
            and frieze_side in cella_sides
        ):
            return _AssemblyRule(
                "ionic-frieze-cella-host",
                "embedded_in",
                "bounded_embedded_finish",
                "the Ionic frieze is an applied decorative band on the cella host",
            )

    if component_pair == {"decoration"} and kind_pair == {"box"}:
        first_side = _ionic_frieze_side(first)
        second_side = _ionic_frieze_side(second)
        if (
            first_side is None
            or second_side is None
            or not _orthogonal_end_sides(first_side, second_side)
        ):
            return None
        return _AssemblyRule(
            "ionic-frieze-corner-return",
            "embedded_in",
            "bounded_embedded_finish",
            "orthogonal Ionic-frieze bands return through a bounded corner joint",
        )

    if component_pair == {"decoration", "entablature"}:
        decoration = first if _component(first) == "decoration" else second
        entablature = second if decoration is first else first
        decoration_signature = _doric_decoration_signature(decoration)
        entablature_signature = _entablature_signature(entablature)
        roof_signature = _roof_signature(entablature)
        host_side = (
            entablature_signature[0]
            if entablature_signature is not None
            else roof_signature.side
            if roof_signature is not None
            and roof_signature.group == "eave"
            and roof_signature.family == "geison"
            else None
        )
        if (
            decoration_signature is not None
            and host_side is not None
            and _member_meets_side(decoration_signature, host_side)
        ):
            return _AssemblyRule(
                "doric-frieze-decoration-host",
                "embedded_in",
                "bounded_embedded_finish",
                "metopes and triglyphs are hosted by the Doric frieze/eave assembly",
            )

    if component_pair == {"decoration", "pediments"}:
        decoration = first if _component(first) == "decoration" else second
        pediment = second if decoration is first else first
        decoration_signature = _doric_decoration_signature(decoration)
        pediment_signature = _roof_signature(pediment)
        if (
            decoration_signature is not None
            and pediment_signature is not None
            and pediment_signature.group == "pediment"
            and pediment_signature.family == "horizontal-geison"
            and pediment_signature.side is not None
            and _member_meets_side(decoration_signature, pediment_signature.side)
        ):
            return _AssemblyRule(
                "pediment-frieze-end-joint",
                "joins",
                "bounded_structural_union",
                "the horizontal pediment frame closes the decorated end frieze",
            )

    if component_pair == {"decoration"} and kind_pair == {"triglyph"}:
        first_signature = _doric_decoration_signature(first)
        second_signature = _doric_decoration_signature(second)
        if (
            first_signature is None
            or second_signature is None
            or first_signature[0] != "triglyph"
            or second_signature[0] != "triglyph"
            or first_signature[1] == second_signature[1]
            or not _member_meets_side(first_signature, second_signature[1])
            or not _member_meets_side(second_signature, first_signature[1])
        ):
            return None
        return _AssemblyRule(
            "corner-triglyph-return",
            "joins",
            "bounded_structural_union",
            "orthogonal corner triglyphs share an exact end condition",
        )

    if component_pair == {"entablature"} and kind_pair == {"entablature_layer"}:
        first_signature = _entablature_signature(first)
        second_signature = _entablature_signature(second)
        if first_signature is None or second_signature is None:
            return None
        first_side, first_layer = first_signature
        second_side, second_layer = second_signature
        if not (
            (first_side == second_side and first_layer != second_layer)
            or _orthogonal_end_sides(first_side, second_side)
        ):
            return None
        return _AssemblyRule(
            "entablature-course-and-corner-joint",
            "joins",
            "bounded_structural_union",
            "typed entablature courses stack vertically and return at corners",
        )

    if component_pair == {"entablature", "peristyle"} and "doric_abacus" in kind_pair:
        abacus = first if _component(first) == "peristyle" else second
        entablature = second if abacus is first else first
        abacus_signature = _peristyle_abacus_side(abacus)
        entablature_signature = _entablature_signature(entablature)
        if (
            abacus_signature is None
            or entablature_signature is None
            or entablature_signature[1] != "architrave"
            or not _peristyle_abacus_meets_side(
                abacus_signature, entablature_signature[0]
            )
        ):
            return None
        return _AssemblyRule(
            "peristyle-abacus-entablature-bearing",
            "supports",
            "bounded_structural_union",
            "the peristyle abacus supports the entablature architrave",
        )

    if (
        component_pair == {"interior-colonnade"}
        and "bearing_block" in kind_pair
        and _pair_ids(first, second) in _INTERIOR_U_JOINTS
    ):
        return _AssemblyRule(
            "interior-u-bearing-continuity",
            "supports",
            "bounded_structural_union",
            "the U-shaped intertier bearing line joins and bears on its typed capitals",
        )

    if component_pair in ({"cella", "door-east"}, {"cella", "door-west"}):
        door = first if _component(first).startswith("door-") else second
        wall = second if door is first else first
        door_signature = _door_signature(door)
        host_ids = _parameters(door).get("host_operation_ids", ())
        if (
            door_signature is not None
            and door_signature[1].startswith("frame-")
            and _cella_member_sides(wall) is not None
            and isinstance(host_ids, Sequence)
            and not isinstance(host_ids, (str, bytes))
            and _operation_id(wall) in {str(item) for item in host_ids}
        ):
            return _AssemblyRule(
                "door-frame-shared-wall-host",
                "embedded_in",
                "bounded_embedded_finish",
                "the exact frame member is derived from and hosted by the shared wall aperture",
            )

    if _component(first) == _component(second) and _component(first) in {
        "door-east",
        "door-west",
    }:
        first_signature = _door_signature(first)
        second_signature = _door_signature(second)
        if (
            first_signature is not None
            and second_signature is not None
            and first_signature[0] == second_signature[0]
            and first_signature[1].startswith("frame-")
            and second_signature[1].startswith("frame-")
        ):
            return _AssemblyRule(
                "door-frame-member-joint",
                "joins",
                "bounded_structural_union",
                "jamb, header, and threshold are exact members of one frame assembly",
            )

    if _roof_scoped(first) or _roof_scoped(second):
        roof_rule = _roof_policy_rule(first, second)
        if roof_rule is not None:
            return roof_rule
        roof_member = first if _roof_scoped(first) else second
        legacy = second if roof_member is first else first
        roof_signature = _roof_signature(roof_member)
        entablature_signature = _entablature_signature(legacy)
        allowed_transition = False
        if roof_signature is not None and entablature_signature is not None:
            legacy_side, legacy_layer = entablature_signature
            if legacy_layer == "frieze" and roof_signature.group == "eave":
                allowed_transition = (
                    roof_signature.family == "geison"
                    and roof_signature.side is not None
                    and (
                        legacy_side == roof_signature.side
                        or legacy_side in {"east", "west"}
                    )
                )
            elif legacy_layer == "frieze" and roof_signature.group == "pediment":
                allowed_transition = (
                    roof_signature.family == "horizontal-geison"
                    and roof_signature.side is not None
                    and (
                        legacy_side == roof_signature.side
                        or legacy_side in {"north", "south"}
                    )
                )
        if allowed_transition:
            return _AssemblyRule(
                "roof-entablature-transition",
                "joins",
                "bounded_structural_union",
                "exact eave/end side topology closes onto the inherited frieze return",
            )

    return None


def _strict_overlap_bound(overlap_volume_m3: float) -> float:
    margin = max(
        _BOUND_ABSOLUTE_TOLERANCE_M3,
        overlap_volume_m3 * _BOUND_RELATIVE_TOLERANCE,
    )
    return math.ceil((overlap_volume_m3 + margin) * 1.0e12) / 1.0e12


def _contract_from_rule(
    first: object,
    second: object,
    rule: _AssemblyRule,
    overlaps: Sequence[float],
) -> dict[str, object]:
    positive = all(value > relations._TOLERANCE for value in overlaps)
    result: dict[str, object] = {
        "first_operation_id": _operation_id(first),
        "second_operation_id": _operation_id(second),
        "relation": rule.relation,
        "intersection_policy": rule.positive_policy if positive else "touch_only",
        "evidence_ref": f"assembly://parthenon-stage4/{rule.rule_id}",
        "semantic_rule": rule.rule_id,
        "semantic_rationale": rule.rationale,
        "component_ids": [_component(first), _component(second)],
        "member_kinds": [_kind(first), _kind(second)],
        "material_roles": [_material_role(first), _material_role(second)],
    }
    if positive:
        volume = float(overlaps[0] * overlaps[1] * overlaps[2])
        result["max_overlap_m3"] = _strict_overlap_bound(volume)
        result["overlap_bound_basis"] = (
            "exact_constructed_box_intersection_plus_relative_tolerance"
            if bool(getattr(first, "bounds").exact_solid)
            and bool(getattr(second, "bounds").exact_solid)
            else "constructed_aabb_upper_bound_plus_relative_tolerance"
        )
        result["observed_aabb_overlap_m3"] = round(volume, 12)
        result["bound_relative_tolerance"] = _BOUND_RELATIVE_TOLERANCE
    return result


def _metadata_contract_mapping(contract: object) -> dict[str, object]:
    """Return a validator-input mapping without losing the exact basis.

    ``relations._RelationContract.to_mapping()`` is receipt-oriented and uses
    the normalized ``basis_kind`` / ``basis_refs`` representation.  Public
    validator input intentionally accepts the authored evidence/host fields,
    so translate the normalized value back to that contract surface here.
    """

    result: dict[str, object] = {
        "first_operation_id": str(getattr(contract, "first_operation_id")),
        "second_operation_id": str(getattr(contract, "second_operation_id")),
        "relation": str(getattr(contract, "relation")),
        "intersection_policy": str(getattr(contract, "intersection_policy")),
        "metadata_source": str(getattr(contract, "source")),
    }
    basis_kind = str(getattr(contract, "basis_kind"))
    basis_refs = tuple(str(item) for item in getattr(contract, "basis_refs"))
    if basis_kind == "host_operation_id":
        result["host_operation_id"] = basis_refs[0]
    elif len(basis_refs) == 1:
        result["evidence_ref"] = basis_refs[0]
    else:
        result["evidence_refs"] = list(basis_refs)
    max_overlap_m3 = getattr(contract, "max_overlap_m3")
    if max_overlap_m3 is not None:
        result["max_overlap_m3"] = float(max_overlap_m3)
    return result


def compile_full_building_relation_contracts(
    operations: Sequence[Mapping[str, object]],
    *,
    expected_operation_count: int = EXPECTED_FULL_OPERATION_COUNT,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Compile exact operation-pair contracts and a deterministic audit receipt."""

    parsed, parse_failures, malformed = relations._parse_operations(operations)
    failures = list(parse_failures)
    if len(operations) != expected_operation_count:
        failures.append(
            f"full-building operation denominator is {len(operations)}, expected {expected_operation_count}"
        )
    if malformed or any(item.bounds is None for item in parsed):
        failures.append("full-building relation compilation requires an AABB for every operation")

    metadata_contracts, metadata_failures, _ = relations._normalize_relation_contracts(
        parsed, ()
    )
    failures.extend(metadata_failures)

    contracts: dict[tuple[str, str], dict[str, object]] = {}
    semantic_rule_counts: Counter[str] = Counter()
    policy_counts: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    unrecognized: list[dict[str, object]] = []
    physical_pair_count = 0
    positive_pair_count = 0
    touch_pair_count = 0
    evaluated_pairs: set[tuple[str, str]] = set()

    for first_index, first in enumerate(parsed):
        for second in parsed[first_index + 1 :]:
            rule = _semantic_rule(first, second)
            if first.bounds is None or second.bounds is None:
                continue
            overlaps = relations._overlap_lengths(first.bounds, second.bounds)
            positive = all(value > relations._TOLERANCE for value in overlaps)
            touching = all(value >= -relations._TOLERANCE for value in overlaps)
            if not positive and not touching:
                continue
            physical_pair_count += 1
            positive_pair_count += int(positive)
            touch_pair_count += int(not positive)
            pair = tuple(sorted((_operation_id(first), _operation_id(second))))
            evaluated_pairs.add(pair)

            metadata_contract = metadata_contracts.get(pair)
            if metadata_contract is not None:
                if positive and metadata_contract.intersection_policy == "touch_only":
                    failures.append(
                        f"exact metadata touch contract has positive AABB volume: {'::'.join(pair)}"
                    )
                    continue
                mapping = _metadata_contract_mapping(metadata_contract)
                mapping["semantic_rule"] = "operation-exact-contact-metadata"
                mapping["semantic_rationale"] = (
                    "operation host/contact metadata already names both exact endpoints"
                )
                contracts[pair] = mapping
                semantic_rule_counts["operation-exact-contact-metadata"] += 1
                policy_counts[metadata_contract.intersection_policy] += 1
                relation_counts[metadata_contract.relation] += 1
                continue

            if rule is None:
                unrecognized.append(
                    {
                        "pair": "::".join(pair),
                        "component_ids": [_component(first), _component(second)],
                        "member_kinds": [_kind(first), _kind(second)],
                        "material_roles": [_material_role(first), _material_role(second)],
                        "overlap_lengths_m": [round(value, 9) for value in overlaps],
                        "classification": "positive_volume" if positive else "touch",
                    }
                )
                continue

            contract = _contract_from_rule(first, second, rule, overlaps)
            contracts[pair] = contract
            semantic_rule_counts[rule.rule_id] += 1
            policy_counts[str(contract["intersection_policy"])] += 1
            relation_counts[rule.relation] += 1

    unrealized_metadata = sorted(set(metadata_contracts) - evaluated_pairs)
    if unrealized_metadata:
        failures.append(
            f"{len(unrealized_metadata)} exact operation-metadata contracts have no AABB contact"
        )
    if unrecognized:
        failures.append(
            f"{len(unrecognized)} touching/overlapping pairs have no assembly-semantic rule"
        )

    contract_tuple = tuple(contracts[pair] for pair in sorted(contracts))
    wildcard_count = sum(
        any(
            token in str(contract[key])
            for token in ("*", "?", "[", "]")
        )
        for contract in contract_tuple
        for key in ("first_operation_id", "second_operation_id")
    )
    if wildcard_count:
        failures.append("compiled exact relation contracts contain wildcard endpoints")

    passed = not failures
    receipt = {
        "schema": SCHEMA,
        "passed": passed,
        "status": "PASSED" if passed else "FAILED",
        "checks": {
            "operation_denominator": len(operations),
            "expected_operation_count": expected_operation_count,
            "aabb_operation_count": sum(item.bounds is not None for item in parsed),
            "pair_denominator": len(parsed) * (len(parsed) - 1) // 2,
            "physical_relation_pair_count": physical_pair_count,
            "positive_volume_pair_count": positive_pair_count,
            "touch_pair_count": touch_pair_count,
            "contract_count": len(contract_tuple),
            "metadata_contract_denominator": len(metadata_contracts),
            "unrealized_metadata_contract_count": len(unrealized_metadata),
            "unrecognized_pair_count": len(unrecognized),
            "wildcard_endpoint_count": wildcard_count,
            "policy_counts": dict(sorted(policy_counts.items())),
            "relation_counts": dict(sorted(relation_counts.items())),
            "semantic_rule_counts": dict(sorted(semantic_rule_counts.items())),
            "bound_relative_tolerance": _BOUND_RELATIVE_TOLERANCE,
            "contract_source": "assembly-semantics-then-aabb-confirmation",
            "validator_failure_list_consumed": False,
        },
        "failure_details": {
            "malformed_operations": malformed[:50],
            "unrecognized_pairs": sorted(unrecognized, key=lambda item: str(item["pair"]))[:100],
            "unrealized_metadata_pairs": ["::".join(pair) for pair in unrealized_metadata[:100]],
        },
        "failure_count": len(failures),
        "failures": failures,
    }
    return contract_tuple, receipt


__all__ = [
    "EXPECTED_FULL_OPERATION_COUNT",
    "SCHEMA",
    "compile_full_building_relation_contracts",
]
