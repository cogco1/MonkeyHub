"""Canonical finite-JSON serialization and SHA-256 helpers.

This is a semantic content digest.  It deliberately omits the trailing newline
used by the P036 filesystem record writer, whose byte digest remains owned by
``archflow.project``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence


_HEX = frozenset("0123456789abcdef")


class CanonicalValueError(ValueError):
    """A value cannot be represented as canonical finite JSON."""


def _require_json_value(value: object, *, path: str, active: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalValueError(f"{path} must be finite")
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise CanonicalValueError(f"{path} contains a cycle")
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalValueError(
                        f"{path} mapping keys must be strings"
                    )
                _require_json_value(
                    item,
                    path=f"{path}.{key}",
                    active=active,
                )
        finally:
            active.remove(identity)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        identity = id(value)
        if identity in active:
            raise CanonicalValueError(f"{path} contains a cycle")
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _require_json_value(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                )
        finally:
            active.remove(identity)
        return
    raise CanonicalValueError(
        f"{path} has unsupported canonical type {type(value).__name__}"
    )


def canonical_json(value: object, *, ascii: bool = True) -> str:
    """Return deterministic finite JSON with stable key ordering.

    ``ascii=False`` keeps non-ASCII text as UTF-8 instead of escaping it; the
    two forms digest differently, so a record keeps the form it was written in.
    """

    _require_json_value(value, path="$", active=set())
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=ascii,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:  # defensive after validation
        raise CanonicalValueError("value is not canonical JSON") from exc


def canonical_json_bytes(value: object, *, ascii: bool = True) -> bytes:
    """Return UTF-8 bytes for :func:`canonical_json`."""

    return canonical_json(value, ascii=ascii).encode("utf-8")


def canonical_digest(value: object, *, ascii: bool = True) -> str:
    """Return the lowercase SHA-256 of canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value, ascii=ascii)).hexdigest()


def require_sha256(value: object, field: str) -> str:
    """Validate and normalize one lowercase SHA-256 digest."""

    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    digest = value.lower()
    if len(digest) != 64 or any(char not in _HEX for char in digest):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return digest


__all__ = [
    "CanonicalValueError",
    "canonical_digest",
    "canonical_json",
    "canonical_json_bytes",
    "require_sha256",
]
