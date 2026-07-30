"""Shared semantic digests for canonical project-state content."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


_HEX = frozenset("0123456789abcdef")


def canonical_json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("project state must be finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def project_state_sha256(state: Mapping[str, Any]) -> str:
    """Digest canonical state content, excluding its declared digest field."""

    if not isinstance(state, Mapping):
        raise TypeError("project state must be a mapping")
    content = dict(state)
    declared = content.pop("state_sha256", None)
    digest = canonical_json_sha256(content)
    if declared is None:
        return digest
    if (
        not isinstance(declared, str)
        or len(declared) != 64
        or any(character not in _HEX for character in declared.lower())
    ):
        raise ValueError("declared state_sha256 is invalid")
    if declared.lower() != digest:
        raise ValueError("declared state_sha256 disagrees with state content")
    return digest
