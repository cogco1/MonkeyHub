"""Cross-record references carry exactly one digest (P088).

The audit of 2026-09-01 found predecessors pinned by up to six parallel
digests — a byte hash and a semantic digest for the same logical object,
copied by hand into authoring scripts.  The contract going forward:
a reference names its target through one semantic digest; byte hashes
remain internal to storage and transfer layers and never travel in
reference payloads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_DIGEST_KEY = re.compile(r"(^|_)(sha256|digest)$")


def digest_keys(reference: Mapping[str, object]) -> tuple[str, ...]:
    """Return the digest-bearing keys of ``reference``, sorted."""

    return tuple(
        sorted(
            key
            for key in reference
            if isinstance(key, str) and _DIGEST_KEY.search(key) is not None
        )
    )


def require_single_digest(
    reference: Mapping[str, object],
    *,
    label: str = "reference",
) -> str:
    """Fail closed unless ``reference`` carries exactly one digest key.

    Returns the single digest key name so callers can read it without
    re-deriving the match.
    """

    if not isinstance(reference, Mapping):
        raise ValueError(f"{label} must be a mapping")
    keys = digest_keys(reference)
    if len(keys) != 1:
        raise ValueError(
            f"{label} must carry exactly one digest key, found "
            f"{list(keys) if keys else 'none'}"
        )
    return keys[0]
