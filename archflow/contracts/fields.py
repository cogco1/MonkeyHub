"""Strict scalar and deterministic-tuple validation used by new contracts."""

from __future__ import annotations

import math
import re
from enum import Enum

from archflow.project.refs import require_identifier


_LOGICAL_REF = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*:[^\s\\]{1,1023}$")
_MAX_ITEMS = 4_096


def text(value: object, field: str, *, maximum: int = 2_000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be bounded non-empty trimmed text")
    return value


def identifier(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return require_identifier(value, field)


def logical_ref(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    if (
        len(value) > 1_100
        or value.lower().startswith("file:")
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or _LOGICAL_REF.fullmatch(value) is None
    ):
        raise ValueError(
            f"{field} must be bounded scheme-qualified logical-ref text"
        )
    return value


def deterministic_refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise ValueError(f"{field} must not be empty")
    normalized = tuple(logical_ref(value, field) for value in values)
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError(f"{field} must be sorted and unique")
    return normalized


def deterministic_identifiers(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise ValueError(f"{field} must not be empty")
    normalized = tuple(identifier(value, field) for value in values)
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError(f"{field} must be sorted and unique")
    return normalized


def enum_value(value: object, enum_type: type[Enum], field: str) -> Enum:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field} must be {enum_type.__name__}")
    return value


def finite_number(value: object, field: str) -> int | float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{field} must be a finite number")
    return value


def exact_mapping(
    value: object,
    expected: set[str],
    field: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{field} schema drifted")
    return value


__all__ = [
    "deterministic_identifiers",
    "deterministic_refs",
    "enum_value",
    "exact_mapping",
    "finite_number",
    "identifier",
    "logical_ref",
    "text",
]
