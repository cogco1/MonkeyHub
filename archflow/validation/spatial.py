"""Pure, deterministic spatial hard-gate validation for building geometry.

The validator consumes typed axis-aligned solids and clear regions.  It has no
filesystem, provider, or project-state authority.  A receipt's pass state is
derived exclusively from its checks; callers cannot supply a hard-gate result.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256

from archflow.contracts.fields import exact_mapping
from archflow.project.refs import require_identifier


class SpatialValidationError(ValueError):
    """Spatial validation input or receipt structure is invalid."""


class SpatialElementKind(StrEnum):
    COLUMN = "column"
    WALL = "wall"
    DOOR = "door"
    OTHER = "other"


class SpatialCheckKind(StrEnum):
    REQUIRED_COMPONENT_COVERAGE = "required_component_coverage"
    ELEMENT_WITHIN_HOST = "element_within_host"
    FORBIDDEN_INTERSECTION_VOLUME = "forbidden_intersection_volume"
    OPENING_CLEAR_OF_WALL = "opening_clear_of_wall"
    MINIMUM_CLEARANCE = "minimum_clearance"


class SpatialCheckComparator(StrEnum):
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


class SpatialCheckStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class SpatialValidationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SpatialValidationError(f"{field_name} must be finite")
    return 0.0 if result == 0.0 else result


def _non_negative(value: object, field_name: str) -> float:
    result = _number(value, field_name)
    if result < 0.0:
        raise SpatialValidationError(f"{field_name} must be non-negative")
    return result


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpatialValidationError(f"{field_name} must be non-empty text")
    return value


def _require_digest(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SpatialValidationError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return value


@dataclass(frozen=True, slots=True)
class AABB:
    """Positive-volume axis-aligned bounds in one declared length unit."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    SCHEMA = "SpatialAABB@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.minimum, tuple)
            or not isinstance(self.maximum, tuple)
            or len(self.minimum) != 3
            or len(self.maximum) != 3
        ):
            raise SpatialValidationError("AABB requires two three-vectors")
        minimum = tuple(
            _number(value, "AABB minimum") for value in self.minimum
        )
        maximum = tuple(
            _number(value, "AABB maximum") for value in self.maximum
        )
        if any(
            lower >= upper
            for lower, upper in zip(minimum, maximum, strict=True)
        ):
            raise SpatialValidationError("AABB must have positive extent")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def intersection_volume(self, other: AABB) -> float:
        if not isinstance(other, AABB):
            raise TypeError("other must be an AABB")
        extents = tuple(
            max(
                0.0,
                min(self.maximum[index], other.maximum[index])
                - max(self.minimum[index], other.minimum[index]),
            )
            for index in range(3)
        )
        volume = extents[0] * extents[1] * extents[2]
        return 0.0 if volume == 0.0 else volume

    def distance_to(self, other: AABB) -> float:
        """Return Euclidean solid-to-solid clearance; overlap/touch is zero."""

        if not isinstance(other, AABB):
            raise TypeError("other must be an AABB")
        gaps = tuple(
            max(
                other.minimum[index] - self.maximum[index],
                self.minimum[index] - other.maximum[index],
                0.0,
            )
            for index in range(3)
        )
        result = math.sqrt(sum(gap * gap for gap in gaps))
        return 0.0 if result == 0.0 else result

    def outside_distance(self, inner: AABB) -> float:
        """Return the largest amount by which ``inner`` exceeds these bounds."""

        if not isinstance(inner, AABB):
            raise TypeError("inner must be an AABB")
        result = max(
            0.0,
            *(
                max(
                    self.minimum[index] - inner.minimum[index],
                    inner.maximum[index] - self.maximum[index],
                    0.0,
                )
                for index in range(3)
            ),
        )
        return 0.0 if result == 0.0 else result

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AABB":
        payload = exact_mapping(
            value,
            {"schema", "minimum", "maximum"},
            "spatial AABB",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SpatialValidationError("unsupported spatial AABB schema")
        if not isinstance(payload["minimum"], list) or not isinstance(
            payload["maximum"], list
        ):
            raise TypeError("AABB vectors must be lists")
        result = cls(
            minimum=tuple(payload["minimum"]),
            maximum=tuple(payload["maximum"]),
        )
        if result.to_dict() != payload:
            raise SpatialValidationError("spatial AABB is not canonical")
        return result


@dataclass(frozen=True, slots=True)
class HostRegion:
    region_id: str
    bounds: AABB

    SCHEMA = "SpatialHostRegion@1"

    def __post_init__(self) -> None:
        require_identifier(self.region_id, "region_id")
        if not isinstance(self.bounds, AABB):
            raise TypeError("bounds must be an AABB")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "region_id": self.region_id,
            "bounds": self.bounds.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "HostRegion":
        payload = exact_mapping(
            value,
            {"schema", "region_id", "bounds"},
            "spatial host region",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SpatialValidationError(
                "unsupported spatial host region schema"
            )
        result = cls(
            region_id=payload["region_id"],
            bounds=AABB.from_dict(payload["bounds"]),
        )
        if result.to_dict() != payload:
            raise SpatialValidationError(
                "spatial host region is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class SpatialElement:
    element_id: str
    component_id: str
    kind: SpatialElementKind
    bounds: AABB
    host_region_id: str

    SCHEMA = "SpatialValidationElement@1"

    def __post_init__(self) -> None:
        require_identifier(self.element_id, "element_id")
        require_identifier(self.component_id, "component_id")
        require_identifier(self.host_region_id, "host_region_id")
        if not isinstance(self.kind, SpatialElementKind):
            raise TypeError("kind must be a SpatialElementKind")
        if not isinstance(self.bounds, AABB):
            raise TypeError("bounds must be an AABB")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "element_id": self.element_id,
            "component_id": self.component_id,
            "kind": self.kind.value,
            "bounds": self.bounds.to_dict(),
            "host_region_id": self.host_region_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SpatialElement":
        payload = exact_mapping(
            value,
            {
                "schema",
                "element_id",
                "component_id",
                "kind",
                "bounds",
                "host_region_id",
            },
            "spatial validation element",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SpatialValidationError(
                "unsupported spatial validation element schema"
            )
        result = cls(
            element_id=payload["element_id"],
            component_id=payload["component_id"],
            kind=SpatialElementKind(payload["kind"]),
            bounds=AABB.from_dict(payload["bounds"]),
            host_region_id=payload["host_region_id"],
        )
        if result.to_dict() != payload:
            raise SpatialValidationError(
                "spatial validation element is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class OpeningClearRegion:
    region_id: str
    component_id: str
    bounds: AABB

    SCHEMA = "OpeningClearRegion@1"

    def __post_init__(self) -> None:
        require_identifier(self.region_id, "region_id")
        require_identifier(self.component_id, "component_id")
        if not isinstance(self.bounds, AABB):
            raise TypeError("bounds must be an AABB")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "region_id": self.region_id,
            "component_id": self.component_id,
            "bounds": self.bounds.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "OpeningClearRegion":
        payload = exact_mapping(
            value,
            {"schema", "region_id", "component_id", "bounds"},
            "opening clear region",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SpatialValidationError(
                "unsupported opening clear region schema"
            )
        result = cls(
            region_id=payload["region_id"],
            component_id=payload["component_id"],
            bounds=AABB.from_dict(payload["bounds"]),
        )
        if result.to_dict() != payload:
            raise SpatialValidationError(
                "opening clear region is not canonical"
            )
        return result


@dataclass(frozen=True, slots=True)
class SpatialValidationCheck:
    check_id: str
    kind: SpatialCheckKind
    status: SpatialCheckStatus
    comparator: SpatialCheckComparator
    subject_refs: tuple[str, ...]
    observed_value: float | None
    threshold_value: float
    unit: str
    message: str

    SCHEMA = "SpatialValidationCheck@1"

    def __post_init__(self) -> None:
        require_identifier(self.check_id, "check_id")
        if not isinstance(self.kind, SpatialCheckKind):
            raise TypeError("kind must be a SpatialCheckKind")
        if not isinstance(self.status, SpatialCheckStatus):
            raise TypeError("status must be a SpatialCheckStatus")
        if not isinstance(self.comparator, SpatialCheckComparator):
            raise TypeError("comparator must be a SpatialCheckComparator")
        if (
            not isinstance(self.subject_refs, tuple)
            or not self.subject_refs
            or len(self.subject_refs) != len(set(self.subject_refs))
            or any(not isinstance(item, str) or not item for item in self.subject_refs)
        ):
            raise SpatialValidationError(
                "subject_refs must be a non-empty unique string tuple"
            )
        if self.observed_value is not None:
            object.__setattr__(
                self,
                "observed_value",
                _non_negative(self.observed_value, "observed_value"),
            )
        elif self.status is not SpatialCheckStatus.FAILED:
            raise SpatialValidationError(
                "only a failed check may have no observed value"
            )
        object.__setattr__(
            self,
            "threshold_value",
            _non_negative(self.threshold_value, "threshold_value"),
        )
        _text(self.unit, "unit")
        _text(self.message, "message")

    @property
    def passed(self) -> bool:
        return self.status is SpatialCheckStatus.PASSED

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "check_id": self.check_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "comparator": self.comparator.value,
            "subject_refs": list(self.subject_refs),
            "observed_value": self.observed_value,
            "threshold_value": self.threshold_value,
            "unit": self.unit,
            "message": self.message,
        }


def _check_sort_key(
    check: SpatialValidationCheck,
) -> tuple[str, tuple[str, ...], str]:
    return check.kind.value, check.subject_refs, check.check_id


@dataclass(frozen=True, slots=True)
class SpatialValidationReceipt:
    input_digest: str
    checks: tuple[SpatialValidationCheck, ...]
    receipt_id: str = field(init=False)
    status: SpatialValidationStatus = field(init=False)
    canonical_write_authority: bool = field(default=False, init=False)

    SCHEMA = "SpatialValidationReceipt@1"

    def __post_init__(self) -> None:
        _require_digest(self.input_digest, "input_digest")
        if (
            not isinstance(self.checks, tuple)
            or not self.checks
            or any(
                not isinstance(item, SpatialValidationCheck)
                for item in self.checks
            )
        ):
            raise TypeError("checks must be a non-empty check tuple")
        if self.checks != tuple(sorted(self.checks, key=_check_sort_key)):
            raise SpatialValidationError("checks must use deterministic order")
        check_ids = tuple(item.check_id for item in self.checks)
        if len(check_ids) != len(set(check_ids)):
            raise SpatialValidationError("check IDs must be unique")
        status = (
            SpatialValidationStatus.PASSED
            if all(item.passed for item in self.checks)
            else SpatialValidationStatus.FAILED
        )
        object.__setattr__(self, "status", status)
        identity = {
            "schema": self.SCHEMA,
            "input_digest": self.input_digest,
            "checks": [item.to_dict() for item in self.checks],
        }
        object.__setattr__(
            self,
            "receipt_id",
            f"spatial-validation-{_digest(identity)[:24]}",
        )

    @property
    def hard_gates_passed(self) -> bool:
        return self.status is SpatialValidationStatus.PASSED

    @property
    def failed_checks(self) -> tuple[SpatialValidationCheck, ...]:
        return tuple(item for item in self.checks if not item.passed)

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "receipt_id": self.receipt_id,
            "input_digest": self.input_digest,
            "status": self.status.value,
            "hard_gates_passed": self.hard_gates_passed,
            "checks": [item.to_dict() for item in self.checks],
            "canonical_write_authority": self.canonical_write_authority,
        }


def _typed_unique(
    values: object,
    *,
    item_type: type,
    identity_field: str,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[object, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if not values and not allow_empty:
        raise SpatialValidationError(f"{field_name} cannot be empty")
    if any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{field_name} contains the wrong item type")
    ordered = tuple(sorted(values, key=lambda item: getattr(item, identity_field)))
    identities = tuple(getattr(item, identity_field) for item in ordered)
    if len(identities) != len(set(identities)):
        raise SpatialValidationError(f"{field_name} contains duplicate IDs")
    return ordered


def _check_id(kind: SpatialCheckKind, subject_refs: tuple[str, ...]) -> str:
    suffix = _digest(
        {"kind": kind.value, "subject_refs": list(subject_refs)}
    )[:20]
    return f"spatial-check-{suffix}"


def _make_check(
    *,
    kind: SpatialCheckKind,
    subject_refs: tuple[str, ...],
    observed_value: float | int | None,
    threshold_value: float | int,
    comparator: SpatialCheckComparator,
    unit: str,
    message: str,
    passed: bool,
) -> SpatialValidationCheck:
    return SpatialValidationCheck(
        check_id=_check_id(kind, subject_refs),
        kind=kind,
        status=(
            SpatialCheckStatus.PASSED
            if passed
            else SpatialCheckStatus.FAILED
        ),
        comparator=comparator,
        subject_refs=subject_refs,
        observed_value=(
            None
            if observed_value is None
            else _non_negative(observed_value, "observed_value")
        ),
        threshold_value=_non_negative(threshold_value, "threshold_value"),
        unit=unit,
        message=message,
    )


def _format_number(value: float) -> str:
    return f"{value:.12g}"


@dataclass(frozen=True, slots=True)
class SpatialValidationInput:
    """Canonical complete input consumed by the spatial validator."""

    elements: tuple[SpatialElement, ...]
    host_regions: tuple[HostRegion, ...]
    required_component_ids: tuple[str, ...]
    opening_clear_regions: tuple[OpeningClearRegion, ...]
    minimum_column_wall_clearance: float
    linear_tolerance: float
    intersection_volume_tolerance: float
    length_unit: str

    SCHEMA = "SpatialValidationInput@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "elements",
            _typed_unique(
                self.elements,
                item_type=SpatialElement,
                identity_field="element_id",
                field_name="elements",
            ),
        )
        object.__setattr__(
            self,
            "host_regions",
            _typed_unique(
                self.host_regions,
                item_type=HostRegion,
                identity_field="region_id",
                field_name="host_regions",
            ),
        )
        object.__setattr__(
            self,
            "opening_clear_regions",
            _typed_unique(
                self.opening_clear_regions,
                item_type=OpeningClearRegion,
                identity_field="region_id",
                field_name="opening_clear_regions",
                allow_empty=True,
            ),
        )
        if (
            not isinstance(self.required_component_ids, tuple)
            or not self.required_component_ids
        ):
            raise SpatialValidationError(
                "required_component_ids must be a non-empty tuple"
            )
        for component_id in self.required_component_ids:
            require_identifier(component_id, "required_component_id")
        if len(self.required_component_ids) != len(
            set(self.required_component_ids)
        ):
            raise SpatialValidationError(
                "required_component_ids contains duplicates"
            )
        object.__setattr__(
            self,
            "required_component_ids",
            tuple(sorted(self.required_component_ids)),
        )
        for field_name in (
            "minimum_column_wall_clearance",
            "linear_tolerance",
            "intersection_volume_tolerance",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_negative(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "length_unit",
            _text(self.length_unit, "length_unit"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "elements": [item.to_dict() for item in self.elements],
            "host_regions": [item.to_dict() for item in self.host_regions],
            "required_component_ids": list(self.required_component_ids),
            "opening_clear_regions": [
                item.to_dict() for item in self.opening_clear_regions
            ],
            "minimum_column_wall_clearance": (
                self.minimum_column_wall_clearance
            ),
            "linear_tolerance": self.linear_tolerance,
            "intersection_volume_tolerance": (
                self.intersection_volume_tolerance
            ),
            "length_unit": self.length_unit,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SpatialValidationInput":
        payload = exact_mapping(
            value,
            {
                "schema",
                "elements",
                "host_regions",
                "required_component_ids",
                "opening_clear_regions",
                "minimum_column_wall_clearance",
                "linear_tolerance",
                "intersection_volume_tolerance",
                "length_unit",
            },
            "spatial validation input",
        )
        if payload["schema"] != cls.SCHEMA:
            raise SpatialValidationError(
                "unsupported spatial validation input schema"
            )
        for field_name in (
            "elements",
            "host_regions",
            "required_component_ids",
            "opening_clear_regions",
        ):
            if not isinstance(payload[field_name], list):
                raise TypeError(f"{field_name} must be a list")
        result = cls(
            elements=tuple(
                SpatialElement.from_dict(item)
                for item in payload["elements"]
            ),
            host_regions=tuple(
                HostRegion.from_dict(item)
                for item in payload["host_regions"]
            ),
            required_component_ids=tuple(
                payload["required_component_ids"]
            ),
            opening_clear_regions=tuple(
                OpeningClearRegion.from_dict(item)
                for item in payload["opening_clear_regions"]
            ),
            minimum_column_wall_clearance=payload[
                "minimum_column_wall_clearance"
            ],
            linear_tolerance=payload["linear_tolerance"],
            intersection_volume_tolerance=payload[
                "intersection_volume_tolerance"
            ],
            length_unit=payload["length_unit"],
        )
        if result.to_dict() != payload:
            raise SpatialValidationError(
                "spatial validation input is not canonical"
            )
        return result

    @property
    def input_digest(self) -> str:
        return _digest(self.to_dict())


def normalize_spatial_validation_input(
    *,
    elements: tuple[SpatialElement, ...],
    host_regions: tuple[HostRegion, ...],
    required_component_ids: tuple[str, ...],
    opening_clear_regions: tuple[OpeningClearRegion, ...] = (),
    minimum_column_wall_clearance: float = 0.0,
    linear_tolerance: float = 0.0,
    intersection_volume_tolerance: float = 0.0,
    length_unit: str = "model_unit",
) -> SpatialValidationInput:
    ordered_elements = _typed_unique(
        elements,
        item_type=SpatialElement,
        identity_field="element_id",
        field_name="elements",
    )
    ordered_hosts = _typed_unique(
        host_regions,
        item_type=HostRegion,
        identity_field="region_id",
        field_name="host_regions",
    )
    ordered_openings = _typed_unique(
        opening_clear_regions,
        item_type=OpeningClearRegion,
        identity_field="region_id",
        field_name="opening_clear_regions",
        allow_empty=True,
    )
    if not isinstance(required_component_ids, tuple) or not required_component_ids:
        raise SpatialValidationError(
            "required_component_ids must be a non-empty tuple"
        )
    for component_id in required_component_ids:
        require_identifier(component_id, "required_component_id")
    if len(required_component_ids) != len(set(required_component_ids)):
        raise SpatialValidationError(
            "required_component_ids contains duplicates"
        )
    return SpatialValidationInput(
        elements=ordered_elements,
        host_regions=ordered_hosts,
        required_component_ids=tuple(sorted(required_component_ids)),
        opening_clear_regions=ordered_openings,
        minimum_column_wall_clearance=_non_negative(
            minimum_column_wall_clearance,
            "minimum_column_wall_clearance",
        ),
        linear_tolerance=_non_negative(
            linear_tolerance,
            "linear_tolerance",
        ),
        intersection_volume_tolerance=_non_negative(
            intersection_volume_tolerance,
            "intersection_volume_tolerance",
        ),
        length_unit=_text(length_unit, "length_unit"),
    )


def spatial_validation_input_digest(
    *,
    elements: tuple[SpatialElement, ...],
    host_regions: tuple[HostRegion, ...],
    required_component_ids: tuple[str, ...],
    opening_clear_regions: tuple[OpeningClearRegion, ...] = (),
    minimum_column_wall_clearance: float = 0.0,
    linear_tolerance: float = 0.0,
    intersection_volume_tolerance: float = 0.0,
    length_unit: str = "model_unit",
) -> str:
    """Digest the exact normalized validator input before validation runs."""

    return normalize_spatial_validation_input(
        elements=elements,
        host_regions=host_regions,
        required_component_ids=required_component_ids,
        opening_clear_regions=opening_clear_regions,
        minimum_column_wall_clearance=minimum_column_wall_clearance,
        linear_tolerance=linear_tolerance,
        intersection_volume_tolerance=intersection_volume_tolerance,
        length_unit=length_unit,
    ).input_digest


def validate_spatial_layout(
    *,
    elements: tuple[SpatialElement, ...],
    host_regions: tuple[HostRegion, ...],
    required_component_ids: tuple[str, ...],
    opening_clear_regions: tuple[OpeningClearRegion, ...] = (),
    minimum_column_wall_clearance: float = 0.0,
    linear_tolerance: float = 0.0,
    intersection_volume_tolerance: float = 0.0,
    length_unit: str = "model_unit",
) -> SpatialValidationReceipt:
    """Evaluate geometry without mutation, I/O, or caller-provided pass state.

    Required-component coverage is based on actual ``SpatialElement`` values;
    an opening clear region does not stand in for a missing door element.
    Positive column-wall overlap and positive opening-clear/wall overlap are
    forbidden subject to the explicit volume tolerance.  Column-wall clearance
    uses exact AABB solid distance.
    """

    normalized = normalize_spatial_validation_input(
        elements=elements,
        host_regions=host_regions,
        required_component_ids=required_component_ids,
        opening_clear_regions=opening_clear_regions,
        minimum_column_wall_clearance=minimum_column_wall_clearance,
        linear_tolerance=linear_tolerance,
        intersection_volume_tolerance=intersection_volume_tolerance,
        length_unit=length_unit,
    )
    ordered_elements = normalized.elements
    ordered_hosts = normalized.host_regions
    ordered_required = normalized.required_component_ids
    ordered_openings = normalized.opening_clear_regions
    clearance = normalized.minimum_column_wall_clearance
    linear_slack = normalized.linear_tolerance
    volume_slack = normalized.intersection_volume_tolerance
    unit = normalized.length_unit
    input_digest = normalized.input_digest
    checks: list[SpatialValidationCheck] = []

    for component_id in ordered_required:
        count = sum(
            item.component_id == component_id for item in ordered_elements
        )
        subject_refs = (f"component:{component_id}",)
        checks.append(
            _make_check(
                kind=SpatialCheckKind.REQUIRED_COMPONENT_COVERAGE,
                subject_refs=subject_refs,
                observed_value=count,
                threshold_value=1,
                comparator=SpatialCheckComparator.AT_LEAST,
                unit="count",
                passed=count >= 1,
                message=(
                    f"component {component_id} has {count} spatial element(s)"
                ),
            )
        )

    hosts_by_id = {item.region_id: item for item in ordered_hosts}
    for element in ordered_elements:
        host = hosts_by_id.get(element.host_region_id)
        subject_refs = (
            f"element:{element.element_id}",
            f"host:{element.host_region_id}",
        )
        if host is None:
            checks.append(
                _make_check(
                    kind=SpatialCheckKind.ELEMENT_WITHIN_HOST,
                    subject_refs=subject_refs,
                    observed_value=None,
                    threshold_value=linear_slack,
                    comparator=SpatialCheckComparator.AT_MOST,
                    unit=unit,
                    passed=False,
                    message=(
                        f"host region {element.host_region_id} is absent"
                    ),
                )
            )
            continue
        outside = host.bounds.outside_distance(element.bounds)
        checks.append(
            _make_check(
                kind=SpatialCheckKind.ELEMENT_WITHIN_HOST,
                subject_refs=subject_refs,
                observed_value=outside,
                threshold_value=linear_slack,
                comparator=SpatialCheckComparator.AT_MOST,
                unit=unit,
                passed=outside <= linear_slack,
                message=(
                    f"maximum host exceedance is {_format_number(outside)} {unit}"
                ),
            )
        )

    columns = tuple(
        item
        for item in ordered_elements
        if item.kind is SpatialElementKind.COLUMN
    )
    walls = tuple(
        item
        for item in ordered_elements
        if item.kind is SpatialElementKind.WALL
    )
    volume_unit = f"{unit}^3"
    for column in columns:
        for wall in walls:
            subject_refs = (
                f"column:{column.element_id}",
                f"wall:{wall.element_id}",
            )
            overlap = column.bounds.intersection_volume(wall.bounds)
            checks.append(
                _make_check(
                    kind=SpatialCheckKind.FORBIDDEN_INTERSECTION_VOLUME,
                    subject_refs=subject_refs,
                    observed_value=overlap,
                    threshold_value=volume_slack,
                    comparator=SpatialCheckComparator.AT_MOST,
                    unit=volume_unit,
                    passed=overlap <= volume_slack,
                    message=(
                        "column-wall intersection volume is "
                        f"{_format_number(overlap)} {volume_unit}"
                    ),
                )
            )
            measured_clearance = column.bounds.distance_to(wall.bounds)
            checks.append(
                _make_check(
                    kind=SpatialCheckKind.MINIMUM_CLEARANCE,
                    subject_refs=subject_refs,
                    observed_value=measured_clearance,
                    threshold_value=clearance,
                    comparator=SpatialCheckComparator.AT_LEAST,
                    unit=unit,
                    passed=measured_clearance + linear_slack >= clearance,
                    message=(
                        "column-wall clearance is "
                        f"{_format_number(measured_clearance)} {unit}"
                    ),
                )
            )

    for opening in ordered_openings:
        for wall in walls:
            subject_refs = (
                f"opening:{opening.region_id}",
                f"wall:{wall.element_id}",
            )
            overlap = opening.bounds.intersection_volume(wall.bounds)
            checks.append(
                _make_check(
                    kind=SpatialCheckKind.OPENING_CLEAR_OF_WALL,
                    subject_refs=subject_refs,
                    observed_value=overlap,
                    threshold_value=volume_slack,
                    comparator=SpatialCheckComparator.AT_MOST,
                    unit=volume_unit,
                    passed=overlap <= volume_slack,
                    message=(
                        "opening-clear/wall intersection volume is "
                        f"{_format_number(overlap)} {volume_unit}"
                    ),
                )
            )

    return SpatialValidationReceipt(
        input_digest=input_digest,
        checks=tuple(sorted(checks, key=_check_sort_key)),
    )


__all__ = [
    "AABB",
    "HostRegion",
    "OpeningClearRegion",
    "SpatialCheckComparator",
    "SpatialCheckKind",
    "SpatialCheckStatus",
    "SpatialElement",
    "SpatialElementKind",
    "SpatialValidationCheck",
    "SpatialValidationError",
    "SpatialValidationInput",
    "SpatialValidationReceipt",
    "SpatialValidationStatus",
    "normalize_spatial_validation_input",
    "spatial_validation_input_digest",
    "validate_spatial_layout",
]
