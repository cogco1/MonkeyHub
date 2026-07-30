"""One-way, no-default adapter from a candidate projection to legacy gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from typing import Any, Mapping

from archflow.project.refs import require_identifier
from archflow.state.candidate_program import (
    CandidateProgramError,
    CandidateProgramProjection,
)
from archflow.state.program import BuildingProgram, FootprintTarget


class LegacyProgramProjectionError(ValueError):
    """An explicit legacy field is missing or has an incompatible type."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyProgramFieldMap:
    """Explicit references for every legacy field, including empty values."""

    use: str
    footprint_width_blocks: str
    footprint_depth_blocks: str
    footprint_tolerance_blocks: str
    required_spaces: str
    minimum_clear_height: str
    entrance_count: str
    circulation_min_width: str
    hard_requirements: str
    soft_preferences: str
    prohibitions: str

    SCHEMA = "LegacyProgramFieldMap@1"

    def __post_init__(self) -> None:
        for item in fields(self):
            require_identifier(getattr(self, item.name), item.name)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            **{
                item.name: getattr(self, item.name)
                for item in fields(self)
            },
        }

    @classmethod
    def from_dict(cls, value: object) -> LegacyProgramFieldMap:
        if not isinstance(value, Mapping):
            raise TypeError("legacy field map must be an object")
        names = {item.name for item in fields(cls)}
        if set(value) != {"schema", *names}:
            raise LegacyProgramProjectionError(
                "legacy field map schema drifted"
            )
        if value["schema"] != cls.SCHEMA:
            raise LegacyProgramProjectionError(
                "legacy field map schema changed"
            )
        return cls(**{name: value[name] for name in names})


@dataclass(frozen=True, slots=True)
class LegacyProgramProjection:
    """Validator-only compatibility view with an auditable one-way mapping."""

    candidate_program_digest: str
    field_map: LegacyProgramFieldMap
    program: BuildingProgram

    SCHEMA = "LegacyProgramProjection@1"

    @property
    def projection_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_program_digest": self.candidate_program_digest,
            "field_map": self.field_map.to_dict(),
            "program": self.program.to_dict(),
            "validator_only": True,
            "generation_authority": False,
            "reverse_compiler_available": False,
        }


def _value(
    projection: CandidateProgramProjection,
    value_id: str,
) -> object:
    try:
        return projection.value(value_id).decoded_value
    except CandidateProgramError as exc:
        raise LegacyProgramProjectionError(
            f"legacy program input is missing: {value_id}"
        ) from exc


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LegacyProgramProjectionError(
            f"{field} must resolve to non-empty text"
        )
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LegacyProgramProjectionError(
            f"{field} must resolve to an integer"
        )
    return value


def _text_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise LegacyProgramProjectionError(
            f"{field} must resolve to a text list"
        )
    return tuple(value)


def project_legacy_building_program(
    projection: CandidateProgramProjection,
    field_map: LegacyProgramFieldMap,
) -> LegacyProgramProjection:
    """Compile only named candidate values; no value is inferred or defaulted."""

    if not isinstance(projection, CandidateProgramProjection):
        raise TypeError("projection must be CandidateProgramProjection")
    if not isinstance(field_map, LegacyProgramFieldMap):
        raise TypeError("field_map must be LegacyProgramFieldMap")
    program = BuildingProgram(
        use=_text(_value(projection, field_map.use), "use"),
        footprint=FootprintTarget(
            width_blocks=_integer(
                _value(projection, field_map.footprint_width_blocks),
                "footprint_width_blocks",
            ),
            depth_blocks=_integer(
                _value(projection, field_map.footprint_depth_blocks),
                "footprint_depth_blocks",
            ),
            tolerance_blocks=_integer(
                _value(projection, field_map.footprint_tolerance_blocks),
                "footprint_tolerance_blocks",
            ),
        ),
        required_spaces=_text_tuple(
            _value(projection, field_map.required_spaces),
            "required_spaces",
        ),
        minimum_clear_height=_integer(
            _value(projection, field_map.minimum_clear_height),
            "minimum_clear_height",
        ),
        entrance_count=_integer(
            _value(projection, field_map.entrance_count),
            "entrance_count",
        ),
        circulation_min_width=_integer(
            _value(projection, field_map.circulation_min_width),
            "circulation_min_width",
        ),
        hard_requirements=_text_tuple(
            _value(projection, field_map.hard_requirements),
            "hard_requirements",
        ),
        soft_preferences=_text_tuple(
            _value(projection, field_map.soft_preferences),
            "soft_preferences",
        ),
        prohibitions=_text_tuple(
            _value(projection, field_map.prohibitions),
            "prohibitions",
        ),
    )
    return LegacyProgramProjection(
        candidate_program_digest=projection.projection_digest,
        field_map=field_map,
        program=program,
    )
