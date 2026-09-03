"""Strict scalar and deterministic-tuple validation used by new contracts."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
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


def mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def list_of(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def tuple_of(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def typed_tuple(
    value: object,
    item_type: type,
    field: str,
) -> tuple[object, ...]:
    items = tuple_of(value, field)
    if any(not isinstance(item, item_type) for item in items):
        raise TypeError(f"{field} contains an invalid item")
    return items


def string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def unique(values: tuple[object, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


def number(value: object, field: str) -> float:
    return float(finite_number(value, field))


def positive(value: object, field: str) -> float:
    result = number(value, field)
    if result <= 0.0:
        raise ValueError(f"{field} must be positive")
    return result


def refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise ValueError(f"{field} has an invalid item count")
    normalized = tuple(logical_ref(value, field) for value in values)
    unique(normalized, field)
    return normalized


def ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
    sorted_required: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise ValueError(f"{field} has an invalid item count")
    normalized = tuple(identifier(value, field) for value in values)
    unique(normalized, field)
    if sorted_required and normalized != tuple(sorted(normalized)):
        raise ValueError(f"{field} must use stable id order")
    return normalized


__all__ = [
    "deterministic_identifiers",
    "deterministic_refs",
    "enum_value",
    "exact_mapping",
    "finite_number",
    "identifier",
    "ids",
    "list_of",
    "logical_ref",
    "mapping",
    "number",
    "positive",
    "refs",
    "string_tuple",
    "text",
    "tuple_of",
    "typed_tuple",
    "unique",
]
