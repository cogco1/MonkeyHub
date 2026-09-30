"""Compact voxel-building program carried by canonical state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar


class ProgramErrorCode(StrEnum):
    MISSING_FIELD = "program.missing_field"
    UNSUPPORTED_FIELD = "program.unsupported_field"
    INVALID_TYPE = "program.invalid_type"
    INVALID_DIMENSION = "program.invalid_dimension"
    INVALID_TOLERANCE = "program.invalid_tolerance"
    INVALID_CLEAR_HEIGHT = "program.invalid_clear_height"
    INVALID_ENTRANCE_COUNT = "program.invalid_entrance_count"
    INVALID_CIRCULATION_WIDTH = "program.invalid_circulation_width"
    EMPTY_REQUIRED_SPACES = "program.empty_required_spaces"
    DUPLICATE_VALUE = "program.duplicate_value"
    CLAUSE_CONFLICT = "program.clause_conflict"
    IMPOSSIBLE_CONSTRAINT = "program.impossible_constraint"


class BuildingProgramError(ValueError):
    """A deterministic program-contract failure."""

    def __init__(self, code: ProgramErrorCode, field: str, message: str) -> None:
        super().__init__(f"{code.value}: {field}: {message}")
        self.code = code
        self.field = field


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildingProgramError(
            ProgramErrorCode.INVALID_TYPE,
            field,
            "must be non-empty text",
        )
    return value.strip()


def _positive_int(
    value: object,
    field: str,
    code: ProgramErrorCode,
    *,
    minimum: int = 1,
) -> int:
    if type(value) is not int or value < minimum:
        raise BuildingProgramError(
            code,
            field,
            f"must be an integer greater than or equal to {minimum}",
        )
    return value


def _text_tuple(value: object, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise BuildingProgramError(
            ProgramErrorCode.INVALID_TYPE,
            field,
            "must be a tuple",
        )
    normalized = tuple(_text(item, field) for item in value)
    if not normalized and not allow_empty:
        raise BuildingProgramError(
            ProgramErrorCode.EMPTY_REQUIRED_SPACES,
            field,
            "must contain at least one value",
        )
    if len(normalized) != len(set(normalized)):
        raise BuildingProgramError(
            ProgramErrorCode.DUPLICATE_VALUE,
            field,
            "contains duplicate values",
        )
    return normalized


@dataclass(frozen=True, slots=True)
class FootprintTarget:
    width_blocks: int
    depth_blocks: int
    tolerance_blocks: int = 0

    def __post_init__(self) -> None:
        _positive_int(
            self.width_blocks,
            "width_blocks",
            ProgramErrorCode.INVALID_DIMENSION,
        )
        _positive_int(
            self.depth_blocks,
            "depth_blocks",
            ProgramErrorCode.INVALID_DIMENSION,
        )
        if type(self.tolerance_blocks) is not int or self.tolerance_blocks < 0:
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TOLERANCE,
                "tolerance_blocks",
                "must be a non-negative integer",
            )
        if self.tolerance_blocks >= min(self.width_blocks, self.depth_blocks):
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TOLERANCE,
                "tolerance_blocks",
                "cannot make a permitted footprint dimension non-positive",
            )


@dataclass(frozen=True, slots=True)
class BuildingProgram:
    """Decision-relevant use and usability requirements, not a design."""

    SCHEMA: ClassVar[str] = "BuildingProgram@1"

    use: str
    footprint: FootprintTarget
    required_spaces: tuple[str, ...]
    minimum_clear_height: int
    entrance_count: int
    circulation_min_width: int
    hard_requirements: tuple[str, ...] = ()
    soft_preferences: tuple[str, ...] = ()
    prohibitions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized_use = _text(self.use, "use")
        if not isinstance(self.footprint, FootprintTarget):
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TYPE,
                "footprint",
                "must be a FootprintTarget",
            )
        required_spaces = _text_tuple(
            self.required_spaces,
            "required_spaces",
            allow_empty=False,
        )
        hard = _text_tuple(
            self.hard_requirements,
            "hard_requirements",
            allow_empty=True,
        )
        soft = _text_tuple(
            self.soft_preferences,
            "soft_preferences",
            allow_empty=True,
        )
        prohibited = _text_tuple(
            self.prohibitions,
            "prohibitions",
            allow_empty=True,
        )
        _positive_int(
            self.minimum_clear_height,
            "minimum_clear_height",
            ProgramErrorCode.INVALID_CLEAR_HEIGHT,
            minimum=2,
        )
        _positive_int(
            self.entrance_count,
            "entrance_count",
            ProgramErrorCode.INVALID_ENTRANCE_COUNT,
        )
        circulation = _positive_int(
            self.circulation_min_width,
            "circulation_min_width",
            ProgramErrorCode.INVALID_CIRCULATION_WIDTH,
        )
        minimum_footprint_dimension = min(
            self.footprint.width_blocks - self.footprint.tolerance_blocks,
            self.footprint.depth_blocks - self.footprint.tolerance_blocks,
        )
        if circulation > minimum_footprint_dimension:
            raise BuildingProgramError(
                ProgramErrorCode.IMPOSSIBLE_CONSTRAINT,
                "circulation_min_width",
                "cannot exceed the smallest permitted footprint dimension",
            )
        conflict = (set(hard) | set(soft)) & set(prohibited)
        if conflict:
            raise BuildingProgramError(
                ProgramErrorCode.CLAUSE_CONFLICT,
                "prohibitions",
                f"conflicts with required or preferred clauses: {sorted(conflict)}",
            )
        # Validate normalized values without rewriting user-authored text.
        normalized_fields = (
            ("use", normalized_use, self.use),
            ("required_spaces", required_spaces, self.required_spaces),
            ("hard_requirements", hard, self.hard_requirements),
            ("soft_preferences", soft, self.soft_preferences),
            ("prohibitions", prohibited, self.prohibitions),
        )
        for field, normalized, original in normalized_fields:
            if normalized == original:
                continue
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TYPE,
                field,
                "values must not contain surrounding whitespace",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "use": self.use,
            "footprint": {
                "width_blocks": self.footprint.width_blocks,
                "depth_blocks": self.footprint.depth_blocks,
                "tolerance_blocks": self.footprint.tolerance_blocks,
            },
            "required_spaces": list(self.required_spaces),
            "minimum_clear_height": self.minimum_clear_height,
            "entrance_count": self.entrance_count,
            "circulation_min_width": self.circulation_min_width,
            "hard_requirements": list(self.hard_requirements),
            "soft_preferences": list(self.soft_preferences),
            "prohibitions": list(self.prohibitions),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BuildingProgram:
        if payload.get("schema") != cls.SCHEMA:
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TYPE,
                "schema",
                f"must equal {cls.SCHEMA}",
            )
        footprint = payload.get("footprint")
        if not isinstance(footprint, dict):
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TYPE,
                "footprint",
                "must be an object",
            )
        return cls(
            use=payload.get("use"),
            footprint=FootprintTarget(
                width_blocks=footprint.get("width_blocks"),
                depth_blocks=footprint.get("depth_blocks"),
                tolerance_blocks=footprint.get("tolerance_blocks", 0),
            ),
            required_spaces=_tuple_from_json(
                payload.get("required_spaces"),
                "required_spaces",
            ),
            minimum_clear_height=payload.get("minimum_clear_height"),
            entrance_count=payload.get("entrance_count"),
            circulation_min_width=payload.get("circulation_min_width"),
            hard_requirements=_tuple_from_json(
                payload.get("hard_requirements", []),
                "hard_requirements",
            ),
            soft_preferences=_tuple_from_json(
                payload.get("soft_preferences", []),
                "soft_preferences",
            ),
            prohibitions=_tuple_from_json(
                payload.get("prohibitions", []),
                "prohibitions",
            ),
        )

    @classmethod
    def from_json(cls, encoded: str) -> BuildingProgram:
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TYPE,
                "json",
                "must be valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise BuildingProgramError(
                ProgramErrorCode.INVALID_TYPE,
                "json",
                "must encode an object",
            )
        return cls.from_dict(payload)


def _tuple_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BuildingProgramError(
            ProgramErrorCode.INVALID_TYPE,
            field,
            "must be an array",
        )
    return tuple(value)
