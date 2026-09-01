"""Deterministic compilation of a compressed brief into BuildingProgram@1."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from archflow.state.program import (
    BuildingProgram,
    BuildingProgramError,
    FootprintTarget,
    ProgramErrorCode,
)


REQUIRED_FIELDS = frozenset(
    {
        "use",
        "width_blocks",
        "depth_blocks",
        "required_spaces",
    }
)
OPTIONAL_FIELDS = frozenset(
    {
        "dimension_tolerance_blocks",
        "minimum_clear_height",
        "entrance_count",
        "circulation_min_width",
        "hard_requirements",
        "soft_preferences",
        "prohibitions",
    }
)
SUPPORTED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


def compile_building_program(brief: Mapping[str, Any]) -> BuildingProgram:
    """Compile explicit brief fields without making design decisions."""

    if not isinstance(brief, Mapping):
        raise BuildingProgramError(
            ProgramErrorCode.INVALID_TYPE,
            "brief",
            "must be a mapping",
        )
    missing = sorted(REQUIRED_FIELDS - set(brief))
    if missing:
        raise BuildingProgramError(
            ProgramErrorCode.MISSING_FIELD,
            missing[0],
            f"required fields are missing: {missing}",
        )
    unsupported = sorted(set(brief) - SUPPORTED_FIELDS)
    if unsupported:
        raise BuildingProgramError(
            ProgramErrorCode.UNSUPPORTED_FIELD,
            unsupported[0],
            f"unsupported fields are not canonical program data: {unsupported}",
        )
    return BuildingProgram(
        use=brief["use"],
        footprint=FootprintTarget(
            width_blocks=brief["width_blocks"],
            depth_blocks=brief["depth_blocks"],
            tolerance_blocks=brief.get("dimension_tolerance_blocks", 0),
        ),
        required_spaces=_tuple_field(brief["required_spaces"], "required_spaces"),
        minimum_clear_height=brief.get("minimum_clear_height", 3),
        entrance_count=brief.get("entrance_count", 1),
        circulation_min_width=brief.get("circulation_min_width", 1),
        hard_requirements=_tuple_field(
            brief.get("hard_requirements", ()),
            "hard_requirements",
        ),
        soft_preferences=_tuple_field(
            brief.get("soft_preferences", ()),
            "soft_preferences",
        ),
        prohibitions=_tuple_field(
            brief.get("prohibitions", ()),
            "prohibitions",
        ),
    )


def _tuple_field(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, tuple):
        return value
    raise BuildingProgramError(
        ProgramErrorCode.INVALID_TYPE,
        field,
        "must be a list or tuple",
    )


__all__ = ["compile_building_program"]
