"""Read-side tolerant key validation (P088).

Write-side contracts stay exact: a producer may not emit a key its
schema does not declare.  Read-side consumers, however, outlive schema
revisions — a viewer pinned to an exact key set breaks on every newer
record even though it needs none of the new keys.  This module gives
readers one shared discipline: required keys missing is a hard error the
caller raises on; unknown or absent-optional keys degrade to typed
warning diagnostics and the read continues.
"""

from __future__ import annotations

from collections.abc import Mapping


def read_key_diagnostics(
    payload: Mapping[str, object],
    *,
    required: frozenset[str] | set[str],
    known: frozenset[str] | set[str],
    label: str,
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Return ``(missing_required, warning_diagnostics)`` for one read.

    ``missing_required`` lists required keys absent from the payload —
    the caller must treat a non-empty result as a hard failure because
    the read cannot proceed without them.  Warnings cover keys the
    payload carries that ``known`` does not declare, and declared
    optional keys the payload omits; both are survivable for a read-only
    consumer and must never be silently dropped.
    """

    if not required <= known:
        raise ValueError(f"{label}: required keys must be a subset of known")
    present = set(payload.keys())
    missing_required = tuple(sorted(set(required) - present))
    warnings: list[dict[str, str]] = []
    for key in sorted(present - set(known)):
        warnings.append(
            {
                "severity": "warning",
                "path": key,
                "message": (
                    f"key is not declared by {label}; "
                    "ignored by this read-only consumer"
                ),
            }
        )
    for key in sorted(set(known) - set(required) - present):
        warnings.append(
            {
                "severity": "warning",
                "path": key,
                "message": f"declared optional key absent from {label}",
            }
        )
    return missing_required, warnings
