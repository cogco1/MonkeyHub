"""Single-source authority-flag blocks for record payloads (P088).

Every retained record declares that its producer holds no authority by
carrying a block of ``*_authority`` flags that are all exactly ``False``.
Before this module the block was hand-written at each write site, so a
typo could silently assert an authority that was never granted.  The
helper is now the single source: writers call :func:`no_authority`, and
a static scan in the test suite holds the count of hand-inlined blocks
at its recorded baseline.
"""

from __future__ import annotations

import re

DEFAULT_AUTHORITY_FIELDS: tuple[str, ...] = (
    "canonical_write_authority",
    "design_authority",
    "geometry_mutation_authority",
    "persistence_authority",
    "promotion_authority",
    "stage_acceptance_authority",
    "verification_authority",
)

_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*_authority$")


class AuthorityContractError(ValueError):
    """An authority block is malformed or asserts an authority."""


def _validated_fields(fields: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not fields:
        raise AuthorityContractError("authority field list must not be empty")
    seen: list[str] = []
    for name in fields:
        if not isinstance(name, str) or _FIELD_NAME.fullmatch(name) is None:
            raise AuthorityContractError(
                f"invalid authority field name: {name!r}"
            )
        if name in seen:
            raise AuthorityContractError(
                f"duplicate authority field name: {name!r}"
            )
        seen.append(name)
    return tuple(sorted(seen))


def no_authority(
    fields: tuple[str, ...] | list[str] = DEFAULT_AUTHORITY_FIELDS,
) -> dict[str, bool]:
    """Return the all-``False`` authority block for ``fields``.

    Field names must be lowercase identifiers ending in ``_authority``;
    the returned mapping is key-sorted so canonical serialization is
    stable regardless of caller order.
    """

    return {name: False for name in _validated_fields(fields)}
