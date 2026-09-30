"""Shared semantic digests for canonical project-state content."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from archflow.contracts.canonical import canonical_digest


_HEX = frozenset("0123456789abcdef")


def project_state_sha256(state: Mapping[str, Any]) -> str:
    """Digest canonical state content, excluding its declared digest field."""

    if not isinstance(state, Mapping):
        raise TypeError("project state must be a mapping")
    content = dict(state)
    declared = content.pop("state_sha256", None)
    digest = canonical_digest(content)
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
