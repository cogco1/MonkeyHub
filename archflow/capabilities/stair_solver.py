"""Deterministic, evidence-bound semantic stair solving.

The solver is deliberately narrower than a CAD stair command.  A project
supplies exact endpoint datums, an explicit length unit, an available local
plan envelope, one layout, adopted dimension bands, and an adopted or
explicitly not-applicable combined ``2R + T`` decision.  This module enumerates
integer riser counts and flight distributions, then returns a geometry-neutral semantic assembly.  It
does not read or write project state, choose code defaults, verify realized
geometry, accept a stage, or mutate a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.state.geometry_program import LengthUnit


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "promotion_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}
_EPSILON = 1.0e-9


class StairSolverError(ValueError):
    """A stair-solver contract is malformed."""


class StairLayout(StrEnum):
    STRAIGHT = "straight"
    QUARTER_TURN = "quarter_turn"
    HALF_TURN = "half_turn"


class StairInterfaceRole(StrEnum):
    LOWER = "lower"
    UPPER = "upper"


class StairPreferenceOrder(StrEnum):
    """Project-selected tie-break for fitting soft plan preferences."""

    LANDING_THEN_TREAD = "landing_then_tread"
    TREAD_THEN_LANDING = "tread_then_landing"


class StairSolveStatus(StrEnum):
    SOLVED = "solved"
    UNKNOWN = "unknown"
    UNSAT = "unsat"


class StairLandingRole(StrEnum):
    LOWER = "lower"
    INTERMEDIATE = "intermediate"
    UPPER = "upper"


class StairTerminalLandingOwnership(StrEnum):
    """Identify whether a terminal landing is stair-owned or host-provided."""

    STAIR_ASSEMBLY = "stair_assembly"
    ADJOINING_INTERFACE = "adjoining_interface"


class StairRiserTreadRuleMode(StrEnum):
    """Whether the project adopted a combined ``2R + T`` constraint."""

    ADOPTED_BAND = "adopted_band"
    NOT_APPLICABLE = "not_applicable"


class StairObligationKind(StrEnum):
    HOST_OPENING = "host_opening"
    SITE_SUPPORT = "site_support"
    LOAD_PATH = "load_path"
    HEADROOM = "headroom"


def _require_no_authority(payload: dict[str, object]) -> None:
    if any(payload.get(key) is not value for key, value in _AUTHORITY_FIELDS.items()):
        raise StairSolverError("stair-solver authority flags changed")


def _finite(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise StairSolverError(f"{field} must be a finite number")
    return float(value)


def _positive(value: object, field: str) -> float:
    result = _finite(value, field)
    if result <= 0.0:
        raise StairSolverError(f"{field} must be positive")
    return result


def _pair(value: object, field: str) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise StairSolverError(f"{field} must be a finite XY tuple")
    return (_finite(value[0], field), _finite(value[1], field))


def _pair_from_list(value: object, field: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise StairSolverError(f"serialized {field} must be an XY list")
    return _pair(tuple(value), field)


def _xyz(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise StairSolverError(f"{field} must be a finite XYZ tuple")
    return tuple(_finite(item, field) for item in value)  # type: ignore[return-value]


def _xyz_from_list(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise StairSolverError(f"serialized {field} must be an XYZ list")
    return _xyz(tuple(value), field)


def _refs_from_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return tuple(value)  # validated by the receiving dataclass


@dataclass(frozen=True, slots=True)
class StairInterface:
    role: StairInterfaceRole
    interface_ref: str
    relation_ref: str
    level_ref: str
    datum_fact_ref: str
    datum: float

    SCHEMA: ClassVar[str] = "StairInterface@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, StairInterfaceRole):
            raise TypeError("role must be StairInterfaceRole")
        for field in ("interface_ref", "relation_ref", "level_ref", "datum_fact_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        object.__setattr__(self, "datum", _finite(self.datum, "datum"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "interface_ref": self.interface_ref,
            "relation_ref": self.relation_ref,
            "level_ref": self.level_ref,
            "datum_fact_ref": self.datum_fact_ref,
            "datum": self.datum,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairInterface":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "interface_ref",
                "relation_ref",
                "level_ref",
                "datum_fact_ref",
                "datum",
                *_AUTHORITY_FIELDS,
            },
            "stair interface",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-interface schema")
        result = cls(
            role=StairInterfaceRole(payload["role"]),
            interface_ref=payload["interface_ref"],
            relation_ref=payload["relation_ref"],
            level_ref=payload["level_ref"],
            datum_fact_ref=payload["datum_fact_ref"],
            datum=payload["datum"],
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-interface identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairDimensionValue:
    value: float
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairDimensionValue@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _positive(self.value, "dimension value"))
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(self, field, deterministic_refs(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "value": self.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairDimensionValue":
        payload = exact_mapping(
            value,
            {"schema", "value", "evidence_refs", "authority_refs", *_AUTHORITY_FIELDS},
            "stair dimension value",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported dimension-value schema")
        result = cls(
            value=payload["value"],
            evidence_refs=_refs_from_list(payload["evidence_refs"], "evidence_refs"),
            authority_refs=_refs_from_list(payload["authority_refs"], "authority_refs"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("dimension-value identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairDimensionBand:
    minimum: float
    maximum: float
    preferred: float
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairDimensionBand@1"

    def __post_init__(self) -> None:
        for field in ("minimum", "maximum", "preferred"):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        if self.minimum > self.maximum:
            raise StairSolverError("dimension minimum exceeds maximum")
        if not self.minimum <= self.preferred <= self.maximum:
            raise StairSolverError("dimension preferred must lie inside the adopted band")
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(self, field, deterministic_refs(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "preferred": self.preferred,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairDimensionBand":
        payload = exact_mapping(
            value,
            {
                "schema",
                "minimum",
                "maximum",
                "preferred",
                "evidence_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "stair dimension band",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported dimension-band schema")
        result = cls(
            minimum=payload["minimum"],
            maximum=payload["maximum"],
            preferred=payload["preferred"],
            evidence_refs=_refs_from_list(payload["evidence_refs"], "evidence_refs"),
            authority_refs=_refs_from_list(payload["authority_refs"], "authority_refs"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("dimension-band identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairRiserTreadRule:
    """Evidence-bound disposition for the common ``2 * riser + tread`` rule.

    The framework deliberately owns no universal numerical default.  A project
    either supplies an adopted band or retains an explicit, evidenced
    not-applicable decision (for example for a documented historic stair).
    Omitting the decision entirely leaves the solve request unresolved.
    """

    mode: StairRiserTreadRuleMode
    minimum: float | None
    maximum: float | None
    preferred: float | None
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairRiserTreadRule@1"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, StairRiserTreadRuleMode):
            raise TypeError("mode must be StairRiserTreadRuleMode")
        values = (self.minimum, self.maximum, self.preferred)
        if self.mode is StairRiserTreadRuleMode.ADOPTED_BAND:
            if any(value is None for value in values):
                raise StairSolverError(
                    "an adopted 2R+T rule requires minimum, maximum, and preferred"
                )
            normalized = tuple(
                _positive(value, "2R+T rule value") for value in values
            )
            object.__setattr__(self, "minimum", normalized[0])
            object.__setattr__(self, "maximum", normalized[1])
            object.__setattr__(self, "preferred", normalized[2])
            if normalized[0] > normalized[1]:
                raise StairSolverError("2R+T rule minimum exceeds maximum")
            if not normalized[0] <= normalized[2] <= normalized[1]:
                raise StairSolverError(
                    "2R+T rule preferred must lie inside the adopted band"
                )
        elif any(value is not None for value in values):
            raise StairSolverError(
                "a not-applicable 2R+T rule cannot carry numerical limits"
            )
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field),
            )

    def accepts(self, riser: float, tread: float) -> bool:
        if self.mode is StairRiserTreadRuleMode.NOT_APPLICABLE:
            return True
        assert self.minimum is not None and self.maximum is not None
        value = 2.0 * riser + tread
        return self.minimum - _EPSILON <= value <= self.maximum + _EPSILON

    def preference_distance(self, riser: float, tread: float) -> float:
        if self.mode is StairRiserTreadRuleMode.NOT_APPLICABLE:
            return 0.0
        assert self.preferred is not None
        return abs((2.0 * riser + tread) - self.preferred)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "mode": self.mode.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "preferred": self.preferred,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairRiserTreadRule":
        payload = exact_mapping(
            value,
            {
                "schema",
                "mode",
                "minimum",
                "maximum",
                "preferred",
                "evidence_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "stair riser-tread rule",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported riser-tread-rule schema")
        result = cls(
            mode=StairRiserTreadRuleMode(payload["mode"]),
            minimum=payload["minimum"],
            maximum=payload["maximum"],
            preferred=payload["preferred"],
            evidence_refs=_refs_from_list(
                payload["evidence_refs"], "evidence_refs"
            ),
            authority_refs=_refs_from_list(
                payload["authority_refs"], "authority_refs"
            ),
        )
        if result.to_dict() != payload:
            raise StairSolverError("riser-tread-rule identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairFlightConstraint:
    minimum_risers: int
    maximum_risers: int
    preferred_risers: int
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairFlightConstraint@1"

    def __post_init__(self) -> None:
        for field in ("minimum_risers", "maximum_risers", "preferred_risers"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise StairSolverError(f"{field} must be a positive integer")
        if self.minimum_risers > self.maximum_risers:
            raise StairSolverError("minimum_risers exceeds maximum_risers")
        if not (
            self.minimum_risers
            <= self.preferred_risers
            <= self.maximum_risers
        ):
            raise StairSolverError("preferred_risers must lie inside the adopted band")
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "minimum_risers": self.minimum_risers,
            "maximum_risers": self.maximum_risers,
            "preferred_risers": self.preferred_risers,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairFlightConstraint":
        payload = exact_mapping(
            value,
            {
                "schema",
                "minimum_risers",
                "maximum_risers",
                "preferred_risers",
                "evidence_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "stair flight constraint",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported flight-constraint schema")
        result = cls(
            minimum_risers=payload["minimum_risers"],
            maximum_risers=payload["maximum_risers"],
            preferred_risers=payload["preferred_risers"],
            evidence_refs=_refs_from_list(payload["evidence_refs"], "evidence_refs"),
            authority_refs=_refs_from_list(payload["authority_refs"], "authority_refs"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("flight-constraint identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairPlanEnvelope:
    envelope_ref: str
    relation_ref: str
    minimum: tuple[float, float]
    maximum: tuple[float, float]
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "StairPlanEnvelope@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "envelope_ref", logical_ref(self.envelope_ref, "envelope_ref"))
        object.__setattr__(self, "relation_ref", logical_ref(self.relation_ref, "relation_ref"))
        object.__setattr__(self, "minimum", _pair(self.minimum, "envelope minimum"))
        object.__setattr__(self, "maximum", _pair(self.maximum, "envelope maximum"))
        if any(low >= high for low, high in zip(self.minimum, self.maximum)):
            raise StairSolverError("plan envelope must have positive dimensions")
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(self, field, deterministic_refs(getattr(self, field), field))

    @property
    def size(self) -> tuple[float, float]:
        return (self.maximum[0] - self.minimum[0], self.maximum[1] - self.minimum[1])

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "envelope_ref": self.envelope_ref,
            "relation_ref": self.relation_ref,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairPlanEnvelope":
        payload = exact_mapping(
            value,
            {
                "schema",
                "envelope_ref",
                "relation_ref",
                "minimum",
                "maximum",
                "evidence_refs",
                "authority_refs",
                *_AUTHORITY_FIELDS,
            },
            "stair plan envelope",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported plan-envelope schema")
        result = cls(
            envelope_ref=payload["envelope_ref"],
            relation_ref=payload["relation_ref"],
            minimum=_pair_from_list(payload["minimum"], "envelope minimum"),
            maximum=_pair_from_list(payload["maximum"], "envelope maximum"),
            evidence_refs=_refs_from_list(payload["evidence_refs"], "evidence_refs"),
            authority_refs=_refs_from_list(payload["authority_refs"], "authority_refs"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("plan-envelope identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairSolveRequest:
    request_id: str
    length_unit: LengthUnit
    lower_interface: StairInterface
    upper_interface: StairInterface
    plan_envelope: StairPlanEnvelope
    layout: StairLayout
    width: StairDimensionValue | None
    riser_height: StairDimensionBand | None
    tread_depth: StairDimensionBand | None
    riser_tread_rule: StairRiserTreadRule | None
    landing_depth: StairDimensionBand | None
    flight_risers: StairFlightConstraint | None
    preference_order: StairPreferenceOrder | None
    lower_landing_ownership: StairTerminalLandingOwnership
    upper_landing_ownership: StairTerminalLandingOwnership

    # @3 adds an explicit length unit plus an evidence-bound combined
    # riser/tread decision.  Older requests remain distinct retained evidence.
    SCHEMA: ClassVar[str] = "StairSolveRequest@3"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            identifier(self.request_id, "request_id"),
        )
        if not isinstance(self.length_unit, LengthUnit):
            raise TypeError("length_unit must be LengthUnit")
        if (
            not isinstance(self.lower_interface, StairInterface)
            or self.lower_interface.role is not StairInterfaceRole.LOWER
        ):
            raise StairSolverError("lower_interface must have the lower role")
        if (
            not isinstance(self.upper_interface, StairInterface)
            or self.upper_interface.role is not StairInterfaceRole.UPPER
        ):
            raise StairSolverError("upper_interface must have the upper role")
        if not isinstance(self.plan_envelope, StairPlanEnvelope):
            raise TypeError("plan_envelope must be StairPlanEnvelope")
        if not isinstance(self.layout, StairLayout):
            raise TypeError("layout must be StairLayout")
        expected = {
            "width": StairDimensionValue,
            "riser_height": StairDimensionBand,
            "tread_depth": StairDimensionBand,
            "riser_tread_rule": StairRiserTreadRule,
            "landing_depth": StairDimensionBand,
            "flight_risers": StairFlightConstraint,
            "preference_order": StairPreferenceOrder,
        }
        for field, kind in expected.items():
            item = getattr(self, field)
            if item is not None and not isinstance(item, kind):
                raise TypeError(f"{field} must be {kind.__name__} or None")
        for field in ("lower_landing_ownership", "upper_landing_ownership"):
            if not isinstance(getattr(self, field), StairTerminalLandingOwnership):
                raise TypeError(
                    f"{field} must be StairTerminalLandingOwnership"
                )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "length_unit": self.length_unit.value,
            "lower_interface": self.lower_interface.to_dict(),
            "upper_interface": self.upper_interface.to_dict(),
            "plan_envelope": self.plan_envelope.to_dict(),
            "layout": self.layout.value,
            "width": None if self.width is None else self.width.to_dict(),
            "riser_height": (
                None
                if self.riser_height is None
                else self.riser_height.to_dict()
            ),
            "tread_depth": (
                None
                if self.tread_depth is None
                else self.tread_depth.to_dict()
            ),
            "riser_tread_rule": (
                None
                if self.riser_tread_rule is None
                else self.riser_tread_rule.to_dict()
            ),
            "landing_depth": (
                None
                if self.landing_depth is None
                else self.landing_depth.to_dict()
            ),
            "flight_risers": (
                None
                if self.flight_risers is None
                else self.flight_risers.to_dict()
            ),
            "preference_order": (
                None
                if self.preference_order is None
                else self.preference_order.value
            ),
            "lower_landing_ownership": self.lower_landing_ownership.value,
            "upper_landing_ownership": self.upper_landing_ownership.value,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairSolveRequest":
        payload = exact_mapping(
            value,
            {
                "schema",
                "request_id",
                "length_unit",
                "lower_interface",
                "upper_interface",
                "plan_envelope",
                "layout",
                "width",
                "riser_height",
                "tread_depth",
                "riser_tread_rule",
                "landing_depth",
                "flight_risers",
                "preference_order",
                "lower_landing_ownership",
                "upper_landing_ownership",
                *_AUTHORITY_FIELDS,
            },
            "stair solve request",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-solve request schema")
        result = cls(
            request_id=payload["request_id"],
            length_unit=LengthUnit(payload["length_unit"]),
            lower_interface=StairInterface.from_dict(payload["lower_interface"]),
            upper_interface=StairInterface.from_dict(payload["upper_interface"]),
            plan_envelope=StairPlanEnvelope.from_dict(payload["plan_envelope"]),
            layout=StairLayout(payload["layout"]),
            width=(
                None
                if payload["width"] is None
                else StairDimensionValue.from_dict(payload["width"])
            ),
            riser_height=(
                None
                if payload["riser_height"] is None
                else StairDimensionBand.from_dict(payload["riser_height"])
            ),
            tread_depth=(
                None
                if payload["tread_depth"] is None
                else StairDimensionBand.from_dict(payload["tread_depth"])
            ),
            riser_tread_rule=(
                None
                if payload["riser_tread_rule"] is None
                else StairRiserTreadRule.from_dict(
                    payload["riser_tread_rule"]
                )
            ),
            landing_depth=(
                None
                if payload["landing_depth"] is None
                else StairDimensionBand.from_dict(payload["landing_depth"])
            ),
            flight_risers=(
                None
                if payload["flight_risers"] is None
                else StairFlightConstraint.from_dict(payload["flight_risers"])
            ),
            preference_order=(
                None
                if payload["preference_order"] is None
                else StairPreferenceOrder(payload["preference_order"])
            ),
            lower_landing_ownership=StairTerminalLandingOwnership(
                payload["lower_landing_ownership"]
            ),
            upper_landing_ownership=StairTerminalLandingOwnership(
                payload["upper_landing_ownership"]
            ),
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-solve request identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairRun:
    ordinal: int
    riser_count: int
    tread_count: int
    start_datum: float
    end_datum: float
    rise: float
    going: float
    width: float
    start_point: tuple[float, float, float]
    end_point: tuple[float, float, float]
    direction: tuple[float, float]

    SCHEMA: ClassVar[str] = "StairRun@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
        ):
            raise StairSolverError("run ordinal must be a non-negative integer")
        if (
            not isinstance(self.riser_count, int)
            or isinstance(self.riser_count, bool)
            or self.riser_count < 1
        ):
            raise StairSolverError("run riser_count must be positive")
        if self.tread_count != self.riser_count - 1:
            raise StairSolverError("run tread_count must equal riser_count minus one")
        for field in ("start_datum", "end_datum", "rise", "going"):
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        object.__setattr__(self, "width", _positive(self.width, "run width"))
        object.__setattr__(self, "start_point", _xyz(self.start_point, "run start_point"))
        object.__setattr__(self, "end_point", _xyz(self.end_point, "run end_point"))
        object.__setattr__(self, "direction", _pair(self.direction, "run direction"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "ordinal": self.ordinal, "riser_count": self.riser_count,
            "tread_count": self.tread_count, "start_datum": self.start_datum, "end_datum": self.end_datum,
            "rise": self.rise, "going": self.going, "width": self.width,
            "start_point": list(self.start_point), "end_point": list(self.end_point),
            "direction": list(self.direction), **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairRun":
        keys = {
            "schema",
            "ordinal",
            "riser_count",
            "tread_count",
            "start_datum",
            "end_datum",
            "rise",
            "going",
            "width",
            "start_point",
            "end_point",
            "direction",
            *_AUTHORITY_FIELDS,
        }
        payload = exact_mapping(value, keys, "stair run")
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-run schema")
        result = cls(
            ordinal=payload["ordinal"],
            riser_count=payload["riser_count"],
            tread_count=payload["tread_count"],
            start_datum=payload["start_datum"],
            end_datum=payload["end_datum"],
            rise=payload["rise"],
            going=payload["going"],
            width=payload["width"],
            start_point=_xyz_from_list(payload["start_point"], "run start_point"),
            end_point=_xyz_from_list(payload["end_point"], "run end_point"),
            direction=_pair_from_list(payload["direction"], "run direction"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-run identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairLanding:
    ordinal: int
    role: StairLandingRole
    datum: float
    local_origin: tuple[float, float, float]
    size: tuple[float, float]

    SCHEMA: ClassVar[str] = "StairLanding@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
        ):
            raise StairSolverError("landing ordinal must be non-negative")
        if not isinstance(self.role, StairLandingRole):
            raise TypeError("role must be StairLandingRole")
        object.__setattr__(self, "datum", _finite(self.datum, "landing datum"))
        object.__setattr__(self, "local_origin", _xyz(self.local_origin, "landing local_origin"))
        object.__setattr__(self, "size", _pair(self.size, "landing size"))
        if any(item <= 0.0 for item in self.size):
            raise StairSolverError("landing size must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "ordinal": self.ordinal,
            "role": self.role.value,
            "datum": self.datum,
            "local_origin": list(self.local_origin),
            "size": list(self.size),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairLanding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "ordinal",
                "role",
                "datum",
                "local_origin",
                "size",
                *_AUTHORITY_FIELDS,
            },
            "stair landing",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-landing schema")
        result = cls(
            ordinal=payload["ordinal"],
            role=StairLandingRole(payload["role"]),
            datum=payload["datum"],
            local_origin=_xyz_from_list(
                payload["local_origin"],
                "landing local_origin",
            ),
            size=_pair_from_list(payload["size"], "landing size"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-landing identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairTread:
    ordinal: int
    run_ordinal: int
    index_in_run: int
    walking_datum: float
    local_origin: tuple[float, float, float]
    size: tuple[float, float]

    SCHEMA: ClassVar[str] = "StairTread@1"

    def __post_init__(self) -> None:
        for field in ("ordinal", "run_ordinal", "index_in_run"):
            item = getattr(self, field)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise StairSolverError(f"{field} must be a non-negative integer")
        object.__setattr__(self, "walking_datum", _finite(self.walking_datum, "walking_datum"))
        object.__setattr__(self, "local_origin", _xyz(self.local_origin, "tread local_origin"))
        object.__setattr__(self, "size", _pair(self.size, "tread size"))
        if any(item <= 0.0 for item in self.size):
            raise StairSolverError("tread size must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "ordinal": self.ordinal,
            "run_ordinal": self.run_ordinal,
            "index_in_run": self.index_in_run,
            "walking_datum": self.walking_datum,
            "local_origin": list(self.local_origin),
            "size": list(self.size),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairTread":
        payload = exact_mapping(
            value,
            {
                "schema",
                "ordinal",
                "run_ordinal",
                "index_in_run",
                "walking_datum",
                "local_origin",
                "size",
                *_AUTHORITY_FIELDS,
            },
            "stair tread",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-tread schema")
        result = cls(
            ordinal=payload["ordinal"],
            run_ordinal=payload["run_ordinal"],
            index_in_run=payload["index_in_run"],
            walking_datum=payload["walking_datum"],
            local_origin=_xyz_from_list(
                payload["local_origin"],
                "tread local_origin",
            ),
            size=_pair_from_list(payload["size"], "tread size"),
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-tread identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairAssembly:
    source_request_digest: str
    layout: StairLayout
    lower_interface_ref: str
    upper_interface_ref: str
    total_rise: float
    total_risers: int
    actual_riser_height: float
    actual_tread_depth: float
    actual_landing_depth: float | None
    width: float
    plan_bounds_minimum: tuple[float, float]
    plan_bounds_maximum: tuple[float, float]
    runs: tuple[StairRun, ...]
    landings: tuple[StairLanding, ...]
    treads: tuple[StairTread, ...]

    SCHEMA: ClassVar[str] = "StairSemanticAssembly@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_request_digest",
            require_sha256(
                self.source_request_digest,
                "source_request_digest",
            ),
        )
        if not isinstance(self.layout, StairLayout):
            raise TypeError("layout must be StairLayout")
        for field in ("lower_interface_ref", "upper_interface_ref"):
            object.__setattr__(self, field, logical_ref(getattr(self, field), field))
        for field in (
            "total_rise",
            "actual_riser_height",
            "actual_tread_depth",
            "width",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        if self.actual_landing_depth is not None:
            object.__setattr__(
                self,
                "actual_landing_depth",
                _positive(self.actual_landing_depth, "actual_landing_depth"),
            )
        if (
            not isinstance(self.total_risers, int)
            or isinstance(self.total_risers, bool)
            or self.total_risers < 1
        ):
            raise StairSolverError("total_risers must be positive")
        object.__setattr__(
            self,
            "plan_bounds_minimum",
            _pair(self.plan_bounds_minimum, "plan_bounds_minimum"),
        )
        object.__setattr__(
            self,
            "plan_bounds_maximum",
            _pair(self.plan_bounds_maximum, "plan_bounds_maximum"),
        )
        if any(low >= high for low, high in zip(self.plan_bounds_minimum, self.plan_bounds_maximum)):
            raise StairSolverError("assembly plan bounds must be positive")
        if not self.runs:
            raise StairSolverError("assembly must contain at least one run")
        if (
            any(not isinstance(item, StairRun) for item in self.runs)
            or any(not isinstance(item, StairLanding) for item in self.landings)
            or any(not isinstance(item, StairTread) for item in self.treads)
        ):
            raise TypeError("assembly members have the wrong type")
        if (self.actual_landing_depth is None) != (not self.landings):
            raise StairSolverError(
                "actual_landing_depth must exist exactly when the assembly owns landings"
            )
        if tuple(item.ordinal for item in self.runs) != tuple(range(len(self.runs))):
            raise StairSolverError("run ordinals must be contiguous")
        if tuple(item.ordinal for item in self.landings) != tuple(range(len(self.landings))):
            raise StairSolverError("landing ordinals must be contiguous")
        if tuple(item.ordinal for item in self.treads) != tuple(range(len(self.treads))):
            raise StairSolverError("tread ordinals must be contiguous")
        if sum(item.riser_count for item in self.runs) != self.total_risers:
            raise StairSolverError("run riser counts do not close the total rise")
        if not math.isclose(
            self.total_rise,
            self.total_risers * self.actual_riser_height,
            rel_tol=0.0,
            abs_tol=_EPSILON,
        ):
            raise StairSolverError(
                "total rise does not close the actual integer risers"
            )
        lower_datum = self.runs[0].start_datum
        for ordinal, run in enumerate(self.runs):
            if run.ordinal != ordinal:
                raise StairSolverError("run ordinals do not match run order")
            if run.rise <= 0.0 or run.going < 0.0:
                raise StairSolverError("run rise/going have invalid signs")
            if not math.isclose(
                run.end_datum - run.start_datum,
                run.rise,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError("run datum endpoints do not close its rise")
            if not math.isclose(
                run.rise,
                run.riser_count * self.actual_riser_height,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError("run rise crossed the actual riser height")
            if not math.isclose(
                run.going,
                run.tread_count * self.actual_tread_depth,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError("run going crossed the actual tread depth")
            if not math.isclose(
                run.width,
                self.width,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError("run width crossed the stair width")
            if not math.isclose(
                math.hypot(*run.direction),
                1.0,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError("run direction must be unit length")
            for index in (0, 1):
                if not math.isclose(
                    run.end_point[index] - run.start_point[index],
                    run.direction[index] * run.going,
                    rel_tol=0.0,
                    abs_tol=_EPSILON,
                ):
                    raise StairSolverError(
                        "run endpoints do not follow direction and going"
                    )
            if not math.isclose(
                run.start_point[2],
                run.start_datum - lower_datum,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ) or not math.isclose(
                run.end_point[2],
                run.end_datum - lower_datum,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError(
                    "run endpoints crossed their local datum elevations"
                )
            if ordinal and not math.isclose(
                self.runs[ordinal - 1].end_datum,
                run.start_datum,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError("adjacent runs do not share a datum")
        if not math.isclose(
            self.runs[-1].end_datum - lower_datum,
            self.total_rise,
            rel_tol=0.0,
            abs_tol=_EPSILON,
        ):
            raise StairSolverError("run chain does not close the total rise")
        if len(self.treads) != sum(item.tread_count for item in self.runs):
            raise StairSolverError("tread count does not cover every run")
        treads_by_run: dict[int, list[StairTread]] = {
            run.ordinal: [] for run in self.runs
        }
        for tread in self.treads:
            if tread.run_ordinal not in treads_by_run:
                raise StairSolverError("tread names an unknown run")
            treads_by_run[tread.run_ordinal].append(tread)
        for run in self.runs:
            run_treads = treads_by_run[run.ordinal]
            if tuple(item.index_in_run for item in run_treads) != tuple(
                range(run.tread_count)
            ):
                raise StairSolverError(
                    "treads do not form a contiguous sequence within the run"
                )
            expected_size = (
                (self.actual_tread_depth, self.width)
                if abs(run.direction[0]) > abs(run.direction[1])
                else (self.width, self.actual_tread_depth)
            )
            for tread in run_treads:
                expected_datum = (
                    run.start_datum
                    + (tread.index_in_run + 1) * self.actual_riser_height
                )
                if not math.isclose(
                    tread.walking_datum,
                    expected_datum,
                    rel_tol=0.0,
                    abs_tol=_EPSILON,
                ):
                    raise StairSolverError(
                        "tread walking datum crossed the run riser sequence"
                    )
                if not math.isclose(
                    tread.local_origin[2],
                    tread.walking_datum - lower_datum,
                    rel_tol=0.0,
                    abs_tol=_EPSILON,
                ):
                    raise StairSolverError(
                        "tread local elevation crossed its walking datum"
                    )
                if any(
                    not math.isclose(
                        actual,
                        expected,
                        rel_tol=0.0,
                        abs_tol=_EPSILON,
                    )
                    for actual, expected in zip(
                        tread.size,
                        expected_size,
                        strict=True,
                    )
                ):
                    raise StairSolverError(
                        "tread size crossed run width/depth semantics"
                    )
        landing_roles = tuple(item.role for item in self.landings)
        if len(landing_roles) != len(set(landing_roles)):
            raise StairSolverError("landing roles must be unique")
        intermediate = tuple(
            item
            for item in self.landings
            if item.role is StairLandingRole.INTERMEDIATE
        )
        expected_intermediate_count = (
            0 if self.layout is StairLayout.STRAIGHT else 1
        )
        if len(intermediate) != expected_intermediate_count:
            raise StairSolverError(
                "landing sequence does not match the stair layout"
            )
        for landing in self.landings:
            if not math.isclose(
                landing.local_origin[2],
                landing.datum - lower_datum,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            ):
                raise StairSolverError(
                    "landing local elevation crossed its datum"
                )
            if (
                landing.role is StairLandingRole.LOWER
                and not math.isclose(
                    landing.datum,
                    lower_datum,
                    rel_tol=0.0,
                    abs_tol=_EPSILON,
                )
            ):
                raise StairSolverError("lower landing crossed the lower datum")
            if (
                landing.role is StairLandingRole.UPPER
                and not math.isclose(
                    landing.datum,
                    self.runs[-1].end_datum,
                    rel_tol=0.0,
                    abs_tol=_EPSILON,
                )
            ):
                raise StairSolverError("upper landing crossed the upper datum")
        if intermediate and (
            len(self.runs) != 2
            or not math.isclose(
                intermediate[0].datum,
                self.runs[0].end_datum,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            )
            or not math.isclose(
                intermediate[0].datum,
                self.runs[1].start_datum,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            )
        ):
            raise StairSolverError(
                "intermediate landing does not join adjacent runs"
            )
        rectangles = (
            *(
                (
                    item.local_origin[0],
                    item.local_origin[1],
                    item.local_origin[0] + item.size[0],
                    item.local_origin[1] + item.size[1],
                )
                for item in self.treads
            ),
            *(
                (
                    item.local_origin[0],
                    item.local_origin[1],
                    item.local_origin[0] + item.size[0],
                    item.local_origin[1] + item.size[1],
                )
                for item in self.landings
            ),
        )
        if not rectangles:
            raise StairSolverError("stair assembly has no walking surfaces")
        derived_minimum = (
            min(item[0] for item in rectangles),
            min(item[1] for item in rectangles),
        )
        derived_maximum = (
            max(item[2] for item in rectangles),
            max(item[3] for item in rectangles),
        )
        if any(
            not math.isclose(
                actual,
                expected,
                rel_tol=0.0,
                abs_tol=_EPSILON,
            )
            for actual, expected in (
                *zip(
                    self.plan_bounds_minimum,
                    derived_minimum,
                    strict=True,
                ),
                *zip(
                    self.plan_bounds_maximum,
                    derived_maximum,
                    strict=True,
                ),
            )
        ):
            raise StairSolverError(
                "assembly plan bounds do not enclose exact walking surfaces"
            )

    @property
    def ref(self) -> str:
        return f"stair-assembly:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "source_request_digest": self.source_request_digest,
            "layout": self.layout.value, "lower_interface_ref": self.lower_interface_ref,
            "upper_interface_ref": self.upper_interface_ref, "total_rise": self.total_rise,
            "total_risers": self.total_risers, "actual_riser_height": self.actual_riser_height,
            "actual_tread_depth": self.actual_tread_depth, "actual_landing_depth": self.actual_landing_depth,
            "width": self.width, "plan_bounds_minimum": list(self.plan_bounds_minimum),
            "plan_bounds_maximum": list(self.plan_bounds_maximum),
            "runs": [item.to_dict() for item in self.runs],
            "landings": [item.to_dict() for item in self.landings],
            "treads": [item.to_dict() for item in self.treads], **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairAssembly":
        keys = {
            "schema",
            "source_request_digest",
            "layout",
            "lower_interface_ref",
            "upper_interface_ref",
            "total_rise",
            "total_risers",
            "actual_riser_height",
            "actual_tread_depth",
            "actual_landing_depth",
            "width",
            "plan_bounds_minimum",
            "plan_bounds_maximum",
            "runs",
            "landings",
            "treads",
            *_AUTHORITY_FIELDS,
        }
        payload = exact_mapping(value, keys, "stair semantic assembly")
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-assembly schema")
        for field in ("runs", "landings", "treads"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            source_request_digest=payload["source_request_digest"], layout=StairLayout(payload["layout"]),
            lower_interface_ref=payload["lower_interface_ref"],
            upper_interface_ref=payload["upper_interface_ref"],
            total_rise=payload["total_rise"],
            total_risers=payload["total_risers"],
            actual_riser_height=payload["actual_riser_height"],
            actual_tread_depth=payload["actual_tread_depth"],
            actual_landing_depth=payload["actual_landing_depth"],
            width=payload["width"],
            plan_bounds_minimum=_pair_from_list(payload["plan_bounds_minimum"], "plan_bounds_minimum"),
            plan_bounds_maximum=_pair_from_list(payload["plan_bounds_maximum"], "plan_bounds_maximum"),
            runs=tuple(StairRun.from_dict(item) for item in payload["runs"]),
            landings=tuple(StairLanding.from_dict(item) for item in payload["landings"]),
            treads=tuple(StairTread.from_dict(item) for item in payload["treads"]),
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-assembly identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairObligation:
    kind: StairObligationKind
    subject_refs: tuple[str, ...]
    statement: str
    status: str = "OPEN"

    SCHEMA: ClassVar[str] = "StairDownstreamObligation@1"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StairObligationKind):
            raise TypeError("kind must be StairObligationKind")
        object.__setattr__(self, "subject_refs", deterministic_refs(self.subject_refs, "subject_refs"))
        if (
            not isinstance(self.statement, str)
            or not self.statement.strip()
            or self.statement != self.statement.strip()
        ):
            raise StairSolverError("obligation statement must be non-empty trimmed text")
        if self.status != "OPEN":
            raise StairSolverError("solver obligations must remain OPEN")

    @property
    def ref(self) -> str:
        return f"stair-obligation:{canonical_digest(self.to_dict())}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "kind": self.kind.value,
            "subject_refs": list(self.subject_refs),
            "statement": self.statement,
            "status": self.status,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairObligation":
        payload = exact_mapping(
            value,
            {
                "schema",
                "kind",
                "subject_refs",
                "statement",
                "status",
                *_AUTHORITY_FIELDS,
            },
            "stair obligation",
        )
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-obligation schema")
        result = cls(
            kind=StairObligationKind(payload["kind"]),
            subject_refs=_refs_from_list(
                payload["subject_refs"],
                "subject_refs",
            ),
            statement=payload["statement"],
            status=payload["status"],
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-obligation identity changed")
        return result


@dataclass(frozen=True, slots=True)
class StairSolveResult:
    request_digest: str
    status: StairSolveStatus
    reason_code: str
    missing_fields: tuple[str, ...]
    integer_riser_counts_considered: int
    flight_distributions_considered: int
    assembly: StairAssembly | None
    obligations: tuple[StairObligation, ...]

    SCHEMA: ClassVar[str] = "StairSolveResult@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_digest", require_sha256(self.request_digest, "request_digest"))
        if not isinstance(self.status, StairSolveStatus):
            raise TypeError("status must be StairSolveStatus")
        object.__setattr__(self, "reason_code", identifier(self.reason_code, "reason_code"))
        if (
            not isinstance(self.missing_fields, tuple)
            or self.missing_fields != tuple(sorted(set(self.missing_fields)))
        ):
            raise StairSolverError("missing_fields must be a sorted unique tuple")
        for field in ("integer_riser_counts_considered", "flight_distributions_considered"):
            item = getattr(self, field)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise StairSolverError(f"{field} must be a non-negative integer")
        if self.status is StairSolveStatus.SOLVED and self.assembly is None:
            raise StairSolverError("SOLVED result requires an assembly")
        if self.status is not StairSolveStatus.SOLVED and self.assembly is not None:
            raise StairSolverError("UNKNOWN/UNSAT result cannot carry an assembly")
        if (
            self.assembly is not None
            and self.assembly.source_request_digest != self.request_digest
        ):
            raise StairSolverError(
                "solved assembly crossed its exact request digest"
            )
        if self.status is StairSolveStatus.UNKNOWN and not self.missing_fields:
            raise StairSolverError("UNKNOWN result requires missing_fields")
        if self.status is not StairSolveStatus.UNKNOWN and self.missing_fields:
            raise StairSolverError("only UNKNOWN result may carry missing_fields")
        if any(not isinstance(item, StairObligation) for item in self.obligations):
            raise TypeError("obligations must contain StairObligation")
        kinds = tuple(item.kind for item in self.obligations)
        if kinds != tuple(StairObligationKind):
            raise StairSolverError("result must carry every downstream obligation in canonical order")

    @property
    def ref(self) -> str:
        return f"stair-solve-result:{canonical_digest(self.to_dict())}"

    @property
    def tread_refs(self) -> tuple[str, ...]:
        """Content-addressed identities for the exact solved tread denominator."""

        if self.assembly is None:
            return ()
        return tuple(
            f"{self.ref}:tread:{item.ordinal:04d}:"
            f"{canonical_digest(item.to_dict())}"
            for item in self.assembly.treads
        )

    @property
    def landing_refs(self) -> tuple[str, ...]:
        """Content-addressed identities for solver-owned landings.

        Host-provided terminal landings are intentionally absent from the
        assembly, so this denominator contains only intermediate landings and
        terminal landings that the solver owns.
        """

        if self.assembly is None:
            return ()
        return tuple(
            f"{self.ref}:landing:{item.ordinal:04d}:"
            f"{canonical_digest(item.to_dict())}"
            for item in self.assembly.landings
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "request_digest": self.request_digest, "status": self.status.value,
            "reason_code": self.reason_code, "missing_fields": list(self.missing_fields),
            "integer_riser_counts_considered": self.integer_riser_counts_considered,
            "flight_distributions_considered": self.flight_distributions_considered,
            "assembly": None if self.assembly is None else self.assembly.to_dict(),
            "obligations": [item.to_dict() for item in self.obligations], **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StairSolveResult":
        keys = {
            "schema",
            "request_digest",
            "status",
            "reason_code",
            "missing_fields",
            "integer_riser_counts_considered",
            "flight_distributions_considered",
            "assembly",
            "obligations",
            *_AUTHORITY_FIELDS,
        }
        payload = exact_mapping(value, keys, "stair solve result")
        _require_no_authority(payload)
        if payload["schema"] != cls.SCHEMA:
            raise StairSolverError("unsupported stair-solve result schema")
        if not isinstance(payload["missing_fields"], list) or not isinstance(payload["obligations"], list):
            raise TypeError("serialized missing_fields and obligations must be lists")
        result = cls(
            request_digest=payload["request_digest"],
            status=StairSolveStatus(payload["status"]),
            reason_code=payload["reason_code"],
            missing_fields=tuple(payload["missing_fields"]),
            integer_riser_counts_considered=payload[
                "integer_riser_counts_considered"
            ],
            flight_distributions_considered=payload[
                "flight_distributions_considered"
            ],
            assembly=(
                None
                if payload["assembly"] is None
                else StairAssembly.from_dict(payload["assembly"])
            ),
            obligations=tuple(StairObligation.from_dict(item) for item in payload["obligations"]),
        )
        if result.to_dict() != payload:
            raise StairSolverError("stair-solve result identity changed")
        return result


def _obligations(request: StairSolveRequest) -> tuple[StairObligation, ...]:
    subjects = tuple(
        sorted(
            {
                request.lower_interface.interface_ref,
                request.upper_interface.interface_ref,
                request.plan_envelope.envelope_ref,
            }
        )
    )
    statements = {
        StairObligationKind.HOST_OPENING: "Verify any required host opening against realized host geometry.",
        StairObligationKind.SITE_SUPPORT: (
            "Verify lower landing and stair support against the adopted site "
            "or floor surface."
        ),
        StairObligationKind.LOAD_PATH: "Verify a continuous structural load path for runs and landings.",
        StairObligationKind.HEADROOM: "Verify headroom along the realized walking line and below crossings.",
    }
    return tuple(
        StairObligation(
            kind=kind,
            subject_refs=subjects,
            statement=statements[kind],
        )
        for kind in StairObligationKind
    )


def _flight_distributions(
    total: int,
    layout: StairLayout,
    constraint: StairFlightConstraint,
) -> tuple[tuple[int, ...], ...]:
    if layout is StairLayout.STRAIGHT:
        return (
            ((total,),)
            if constraint.minimum_risers <= total <= constraint.maximum_risers
            else ()
        )
    rows = []
    for first in range(constraint.minimum_risers, constraint.maximum_risers + 1):
        second = total - first
        if constraint.minimum_risers <= second <= constraint.maximum_risers:
            rows.append((first, second))
    return tuple(rows)


def _plan_extents(
    layout: StairLayout,
    distribution: tuple[int, ...],
    tread: float,
    landing: float,
    width: float,
    lower_landing_owned: bool,
    upper_landing_owned: bool,
) -> tuple[float, float]:
    goings = tuple((count - 1) * tread for count in distribution)
    lower_depth = landing if lower_landing_owned else 0.0
    upper_depth = landing if upper_landing_owned else 0.0
    if layout is StairLayout.STRAIGHT:
        return (lower_depth + goings[0] + upper_depth, width)
    if layout is StairLayout.QUARTER_TURN:
        turn = max(width, landing)
        return (
            lower_depth + goings[0] + turn,
            turn + goings[1] + upper_depth,
        )
    turn = max(width, landing)
    first_going, second_going = goings
    upper_landing_end = lower_depth + first_going - second_going
    raw_minimum_y = min(0.0, upper_landing_end - upper_depth)
    raw_maximum_y = lower_depth + first_going + turn
    return (2.0 * width, raw_maximum_y - raw_minimum_y)


def _fits(
    layout: StairLayout,
    distribution: tuple[int, ...],
    tread: float,
    landing: float,
    width: float,
    envelope: tuple[float, float],
    lower_landing_owned: bool,
    upper_landing_owned: bool,
) -> bool:
    extents = _plan_extents(
        layout,
        distribution,
        tread,
        landing,
        width,
        lower_landing_owned,
        upper_landing_owned,
    )
    return (
        extents[0] <= envelope[0]
        and extents[1] <= envelope[1]
    )


def _largest_feasible(low: float, high: float, predicate) -> float | None:
    if not predicate(low):
        return None
    if predicate(high):
        return high
    left, right = low, high
    for _ in range(80):
        middle = (left + right) / 2.0
        if predicate(middle):
            left = middle
        else:
            right = middle
    return left


def _fit_plan_dimensions(
    request: StairSolveRequest,
    distribution: tuple[int, ...],
) -> tuple[float, float | None] | None:
    assert request.width is not None
    assert request.tread_depth is not None
    assert request.preference_order is not None
    width = request.width.value
    tread = request.tread_depth
    landing = request.landing_depth
    envelope = request.plan_envelope.size
    lower_owned = (
        request.lower_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
    )
    upper_owned = (
        request.upper_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
    )
    landing_required = (
        request.layout is not StairLayout.STRAIGHT or lower_owned or upper_owned
    )
    if not landing_required:
        max_tread = _largest_feasible(
            tread.minimum,
            tread.maximum,
            lambda value: _fits(
                request.layout,
                distribution,
                value,
                0.0,
                width,
                envelope,
                False,
                False,
            ),
        )
        if max_tread is None:
            return None
        return (min(tread.preferred, max_tread), None)
    if landing is None:
        raise StairSolverError("landing depth is required by this stair assembly")
    if request.preference_order is StairPreferenceOrder.LANDING_THEN_TREAD:
        max_landing = _largest_feasible(
            landing.minimum,
            landing.maximum,
            lambda value: _fits(
                request.layout,
                distribution,
                tread.minimum,
                value,
                width,
                envelope,
                lower_owned,
                upper_owned,
            ),
        )
        if max_landing is None:
            return None
        actual_landing = min(landing.preferred, max_landing)
        max_tread = _largest_feasible(
            tread.minimum,
            tread.maximum,
            lambda value: _fits(
                request.layout,
                distribution,
                value,
                actual_landing,
                width,
                envelope,
                lower_owned,
                upper_owned,
            ),
        )
        if max_tread is None:
            return None
        return (min(tread.preferred, max_tread), actual_landing)
    max_tread = _largest_feasible(
        tread.minimum,
        tread.maximum,
        lambda value: _fits(
            request.layout,
            distribution,
            value,
            landing.minimum,
            width,
            envelope,
            lower_owned,
            upper_owned,
        ),
    )
    if max_tread is None:
        return None
    actual_tread = min(tread.preferred, max_tread)
    max_landing = _largest_feasible(
        landing.minimum,
        landing.maximum,
        lambda value: _fits(
            request.layout,
            distribution,
            actual_tread,
            value,
            width,
            envelope,
            lower_owned,
            upper_owned,
        ),
    )
    if max_landing is None:
        return None
    return (actual_tread, min(landing.preferred, max_landing))


def _translate_xyz(point: tuple[float, float, float], dx: float, dy: float) -> tuple[float, float, float]:
    return (point[0] + dx, point[1] + dy, point[2])


def _build_assembly(request: StairSolveRequest, distribution: tuple[int, ...], riser: float, tread: float, landing: float | None) -> StairAssembly:
    assert request.width is not None
    width = request.width.value
    lower = request.lower_interface.datum
    total_rise = request.upper_interface.datum - lower
    runs: list[StairRun] = []
    landings: list[StairLanding] = []
    treads: list[StairTread] = []
    rectangles: list[tuple[float, float, float, float]] = []
    lower_owned = (
        request.lower_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
    )
    upper_owned = (
        request.upper_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
    )
    if request.layout is not StairLayout.STRAIGHT or lower_owned or upper_owned:
        if landing is None:
            raise StairSolverError("landing depth is required by this stair assembly")
    effective_landing = 0.0 if landing is None else landing
    lower_depth = effective_landing if lower_owned else 0.0
    upper_depth = effective_landing if upper_owned else 0.0

    def add_landing(role: StairLandingRole, datum: float, x: float, y: float, sx: float, sy: float) -> None:
        landings.append(StairLanding(len(landings), role, datum, (x, y, datum - lower), (sx, sy)))
        rectangles.append((x, y, x + sx, y + sy))

    def add_tread(run_index: int, index: int, datum: float, x: float, y: float, sx: float, sy: float) -> None:
        treads.append(StairTread(len(treads), run_index, index, datum, (x, y, datum - lower), (sx, sy)))
        rectangles.append((x, y, x + sx, y + sy))

    if request.layout is StairLayout.STRAIGHT:
        count = distribution[0]
        going = (count - 1) * tread
        if lower_owned:
            add_landing(
                StairLandingRole.LOWER,
                lower,
                0.0,
                0.0,
                lower_depth,
                width,
            )
        for index in range(count - 1):
            add_tread(
                0,
                index,
                lower + (index + 1) * riser,
                lower_depth + index * tread,
                0.0,
                tread,
                width,
            )
        upper = lower + count * riser
        if upper_owned:
            add_landing(
                StairLandingRole.UPPER,
                upper,
                lower_depth + going,
                0.0,
                upper_depth,
                width,
            )
        runs.append(StairRun(0, count, count - 1, lower, upper, count * riser, going, width, (lower_depth, width / 2.0, 0.0), (lower_depth + going, width / 2.0, count * riser), (1.0, 0.0)))
    elif request.layout is StairLayout.QUARTER_TURN:
        first, second = distribution
        going1, going2 = (first - 1) * tread, (second - 1) * tread
        turn = max(width, effective_landing)
        middle = lower + first * riser
        upper = middle + second * riser
        if lower_owned:
            add_landing(
                StairLandingRole.LOWER,
                lower,
                0.0,
                0.0,
                lower_depth,
                width,
            )
        for index in range(first - 1):
            add_tread(0, index, lower + (index + 1) * riser, lower_depth + index * tread, 0.0, tread, width)
        add_landing(StairLandingRole.INTERMEDIATE, middle, lower_depth + going1, 0.0, turn, turn)
        x2 = lower_depth + going1 + turn - width
        for index in range(second - 1):
            add_tread(1, index, middle + (index + 1) * riser, x2, turn + index * tread, width, tread)
        if upper_owned:
            add_landing(StairLandingRole.UPPER, upper, x2, turn + going2, width, upper_depth)
        runs.extend((
            StairRun(0, first, first - 1, lower, middle, first * riser, going1, width, (lower_depth, width / 2.0, 0.0), (lower_depth + going1, width / 2.0, first * riser), (1.0, 0.0)),
            StairRun(1, second, second - 1, middle, upper, second * riser, going2, width, (x2 + width / 2.0, turn, first * riser), (x2 + width / 2.0, turn + going2, total_rise), (0.0, 1.0)),
        ))
    else:
        first, second = distribution
        going1, going2 = (first - 1) * tread, (second - 1) * tread
        turn = max(width, effective_landing)
        middle = lower + first * riser
        upper = middle + second * riser
        if lower_owned:
            add_landing(
                StairLandingRole.LOWER,
                lower,
                0.0,
                0.0,
                width,
                lower_depth,
            )
        for index in range(first - 1):
            add_tread(0, index, lower + (index + 1) * riser, 0.0, lower_depth + index * tread, width, tread)
        add_landing(StairLandingRole.INTERMEDIATE, middle, 0.0, lower_depth + going1, 2.0 * width, turn)
        endpoint_y = lower_depth + going1 - going2
        for index in range(second - 1):
            y = lower_depth + going1 - (index + 1) * tread
            add_tread(1, index, middle + (index + 1) * riser, width, y, width, tread)
        if upper_owned:
            add_landing(StairLandingRole.UPPER, upper, width, endpoint_y - upper_depth, width, upper_depth)
        runs.extend((
            StairRun(0, first, first - 1, lower, middle, first * riser, going1, width, (width / 2.0, lower_depth, 0.0), (width / 2.0, lower_depth + going1, first * riser), (0.0, 1.0)),
            StairRun(1, second, second - 1, middle, upper, second * riser, going2, width, (1.5 * width, lower_depth + going1, first * riser), (1.5 * width, endpoint_y, total_rise), (0.0, -1.0)),
        ))

    min_x = min(item[0] for item in rectangles)
    min_y = min(item[1] for item in rectangles)
    max_x = max(item[2] for item in rectangles)
    max_y = max(item[3] for item in rectangles)
    dx = request.plan_envelope.minimum[0] - min_x
    dy = request.plan_envelope.minimum[1] - min_y
    if dx or dy:
        runs = [StairRun(item.ordinal, item.riser_count, item.tread_count, item.start_datum, item.end_datum, item.rise, item.going, item.width, _translate_xyz(item.start_point, dx, dy), _translate_xyz(item.end_point, dx, dy), item.direction) for item in runs]
        landings = [StairLanding(item.ordinal, item.role, item.datum, _translate_xyz(item.local_origin, dx, dy), item.size) for item in landings]
        treads = [StairTread(item.ordinal, item.run_ordinal, item.index_in_run, item.walking_datum, _translate_xyz(item.local_origin, dx, dy), item.size) for item in treads]
    bounds_minimum = (min_x + dx, min_y + dy)
    bounds_maximum = (max_x + dx, max_y + dy)
    return StairAssembly(
        source_request_digest=request.digest, layout=request.layout,
        lower_interface_ref=request.lower_interface.interface_ref, upper_interface_ref=request.upper_interface.interface_ref,
        total_rise=total_rise, total_risers=sum(distribution), actual_riser_height=riser,
        actual_tread_depth=tread, actual_landing_depth=landing, width=width,
        plan_bounds_minimum=bounds_minimum, plan_bounds_maximum=bounds_maximum,
        runs=tuple(runs), landings=tuple(landings), treads=tuple(treads),
    )


def require_solved_stair_assembly(
    request: StairSolveRequest,
    assembly: StairAssembly,
) -> StairAssembly:
    """Fail closed unless ``assembly`` is the deterministic solve of request.

    ``StairAssembly`` is a serializable semantic value, so schema validity by
    itself cannot establish that all run, landing, tread, datum, and envelope
    fields still represent the source request.  Geometry compilation uses this
    exact recomputation boundary before accepting an assembly.
    """

    if not isinstance(request, StairSolveRequest):
        raise TypeError("request must be StairSolveRequest")
    if not isinstance(assembly, StairAssembly):
        raise TypeError("assembly must be StairAssembly")
    if assembly.source_request_digest != request.digest:
        raise StairSolverError("stair assembly crossed its exact request")
    required = (
        request.width,
        request.riser_height,
        request.tread_depth,
        request.riser_tread_rule,
        request.flight_risers,
        request.preference_order,
    )
    if any(item is None for item in required):
        raise StairSolverError(
            "a solved assembly cannot originate from an unresolved request"
        )
    assert request.width is not None
    assert request.riser_height is not None
    assert request.tread_depth is not None
    assert request.riser_tread_rule is not None
    assert request.flight_risers is not None
    if assembly.layout is not request.layout:
        raise StairSolverError("stair assembly layout crossed its request")
    if assembly.lower_interface_ref != request.lower_interface.interface_ref:
        raise StairSolverError("stair assembly crossed its lower interface")
    if assembly.upper_interface_ref != request.upper_interface.interface_ref:
        raise StairSolverError("stair assembly crossed its upper interface")
    expected_rise = request.upper_interface.datum - request.lower_interface.datum
    if not math.isclose(
        assembly.total_rise,
        expected_rise,
        rel_tol=0.0,
        abs_tol=_EPSILON,
    ):
        raise StairSolverError("stair assembly total rise crossed its request")
    if not math.isclose(
        assembly.width,
        request.width.value,
        rel_tol=0.0,
        abs_tol=_EPSILON,
    ):
        raise StairSolverError("stair assembly width crossed its request")
    if not (
        request.riser_height.minimum - _EPSILON
        <= assembly.actual_riser_height
        <= request.riser_height.maximum + _EPSILON
    ):
        raise StairSolverError("stair assembly riser escaped its adopted band")
    if not (
        request.tread_depth.minimum - _EPSILON
        <= assembly.actual_tread_depth
        <= request.tread_depth.maximum + _EPSILON
    ):
        raise StairSolverError("stair assembly tread escaped its adopted band")
    if not request.riser_tread_rule.accepts(
        assembly.actual_riser_height,
        assembly.actual_tread_depth,
    ):
        raise StairSolverError(
            "stair assembly escaped its adopted 2R+T rule"
        )
    landing_required = (
        request.layout is not StairLayout.STRAIGHT
        or request.lower_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
        or request.upper_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
    )
    if landing_required:
        if request.landing_depth is None or assembly.actual_landing_depth is None:
            raise StairSolverError(
                "stair assembly lacks its adopted landing depth"
            )
        if not (
            request.landing_depth.minimum - _EPSILON
            <= assembly.actual_landing_depth
            <= request.landing_depth.maximum + _EPSILON
        ):
            raise StairSolverError(
                "stair assembly landing escaped its adopted band"
            )
    elif assembly.actual_landing_depth is not None:
        raise StairSolverError(
            "interface-owned terminal landings entered the stair assembly"
        )
    distribution = tuple(run.riser_count for run in assembly.runs)
    if distribution not in _flight_distributions(
        assembly.total_risers,
        request.layout,
        request.flight_risers,
    ):
        raise StairSolverError(
            "stair assembly has no legal integer flight distribution"
        )
    landing_for_fit = assembly.actual_landing_depth or 0.0
    if not _fits(
        request.layout,
        distribution,
        assembly.actual_tread_depth,
        landing_for_fit,
        assembly.width,
        request.plan_envelope.size,
        request.lower_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY,
        request.upper_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY,
    ):
        raise StairSolverError("stair assembly escaped its exact plan envelope")
    expected = _build_assembly(
        request,
        distribution,
        assembly.actual_riser_height,
        assembly.actual_tread_depth,
        assembly.actual_landing_depth,
    )
    if assembly != expected:
        raise StairSolverError(
            "stair assembly members do not match the deterministic solve"
        )
    return assembly


def solve_stair(request: StairSolveRequest) -> StairSolveResult:
    """Solve one evidence-bound stair request without asserting realization."""

    if not isinstance(request, StairSolveRequest):
        raise TypeError("request must be StairSolveRequest")
    obligations = _obligations(request)
    required_fields = [
        "width",
        "riser_height",
        "tread_depth",
        "riser_tread_rule",
        "flight_risers",
        "preference_order",
    ]
    if (
        request.layout is not StairLayout.STRAIGHT
        or request.lower_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
        or request.upper_landing_ownership
        is StairTerminalLandingOwnership.STAIR_ASSEMBLY
    ):
        required_fields.append("landing_depth")
    missing = tuple(
        sorted(field for field in required_fields if getattr(request, field) is None)
    )
    if missing:
        return StairSolveResult(request.digest, StairSolveStatus.UNKNOWN, "missing_project_constraints", missing, 0, 0, None, obligations)
    assert request.width is not None
    assert request.riser_height is not None
    assert request.tread_depth is not None
    assert request.riser_tread_rule is not None
    assert request.flight_risers is not None

    rise = request.upper_interface.datum - request.lower_interface.datum
    if rise <= 0.0:
        return StairSolveResult(request.digest, StairSolveStatus.UNSAT, "non_positive_rise", (), 0, 0, None, obligations)
    low_count = max(1, math.ceil(rise / request.riser_height.maximum - _EPSILON))
    high_count = math.floor(rise / request.riser_height.minimum + _EPSILON)
    integer_counts = []
    for count in range(low_count, high_count + 1):
        actual = rise / count
        if request.riser_height.minimum - _EPSILON <= actual <= request.riser_height.maximum + _EPSILON:
            integer_counts.append((count, actual))
    if not integer_counts:
        return StairSolveResult(request.digest, StairSolveStatus.UNSAT, "no_integer_riser_count", (), 0, 0, None, obligations)

    distribution_count = 0
    candidates: list[
        tuple[tuple[object, ...], tuple[int, ...], float, float, float | None]
    ] = []
    plan_candidate_seen = False
    memberless_candidate_seen = False
    for total, actual_riser in integer_counts:
        distributions = _flight_distributions(total, request.layout, request.flight_risers)
        distribution_count += len(distributions)
        for distribution in distributions:
            owns_terminal_surface = (
                request.lower_landing_ownership
                is StairTerminalLandingOwnership.STAIR_ASSEMBLY
                or request.upper_landing_ownership
                is StairTerminalLandingOwnership.STAIR_ASSEMBLY
            )
            if sum(count - 1 for count in distribution) == 0 and not owns_terminal_surface:
                memberless_candidate_seen = True
                continue
            dimensions = _fit_plan_dimensions(request, distribution)
            if dimensions is None:
                continue
            plan_candidate_seen = True
            actual_tread, actual_landing = dimensions
            if not request.riser_tread_rule.accepts(
                actual_riser,
                actual_tread,
            ):
                continue
            score = (
                abs(actual_riser - request.riser_height.preferred),
                request.riser_tread_rule.preference_distance(
                    actual_riser,
                    actual_tread,
                ),
                sum(abs(item - request.flight_risers.preferred_risers) for item in distribution),
                max(distribution) - min(distribution),
                (
                    1
                    if request.layout is StairLayout.HALF_TURN
                    and distribution[0] < distribution[1]
                    else 0
                ),
                (
                    0.0
                    if actual_landing is None
                    else abs(actual_landing - request.landing_depth.preferred)
                ),
                abs(actual_tread - request.tread_depth.preferred),
                total,
                distribution,
            )
            candidates.append((score, distribution, actual_riser, actual_tread, actual_landing))
    if distribution_count == 0:
        return StairSolveResult(request.digest, StairSolveStatus.UNSAT, "no_flight_distribution", (), len(integer_counts), 0, None, obligations)
    if not candidates:
        return StairSolveResult(
            request.digest,
            StairSolveStatus.UNSAT,
            (
                "no_walking_surface_members"
                if memberless_candidate_seen and not plan_candidate_seen
                else (
                    "riser_tread_rule_unsatisfied"
                    if plan_candidate_seen
                    else "plan_envelope_too_small"
                )
            ),
            (),
            len(integer_counts),
            distribution_count,
            None,
            obligations,
        )
    _, distribution, riser, tread, landing = min(candidates, key=lambda item: item[0])
    assembly = _build_assembly(request, distribution, riser, tread, landing)
    if any(low < env_low - _EPSILON or high > env_high + _EPSILON for low, high, env_low, env_high in zip(assembly.plan_bounds_minimum, assembly.plan_bounds_maximum, request.plan_envelope.minimum, request.plan_envelope.maximum)):
        raise StairSolverError("internal stair assembly escaped the supplied plan envelope")
    require_solved_stair_assembly(request, assembly)
    return StairSolveResult(request.digest, StairSolveStatus.SOLVED, "semantic_assembly_generated", (), len(integer_counts), distribution_count, assembly, obligations)


__all__ = [
    "StairAssembly", "StairDimensionBand", "StairDimensionValue", "StairFlightConstraint",
    "StairInterface", "StairInterfaceRole", "StairLanding", "StairLandingRole", "StairLayout",
    "StairObligation", "StairObligationKind", "StairPlanEnvelope", "StairPreferenceOrder", "StairRun",
    "StairRiserTreadRule", "StairRiserTreadRuleMode",
    "StairSolveRequest", "StairSolveResult", "StairSolveStatus", "StairSolverError", "StairTread", "solve_stair",
    "require_solved_stair_assembly",
    "StairTerminalLandingOwnership",
]
