"""Which failures pass by themselves, and which may be sent again unchanged: one table for the Hub and the Runtime.

Every failure is answered ``{code, detail}``. Its code alone says whether a
person has anything to do about it (#300 4-1, 4-3): a *transient* failure
passes by itself, and when it also did nothing, the same request may be sent
again as it was (``resend``); a *conflict* means the project moved, so the
caller reads it again before deciding. Every other code stays the caller's to
answer. ``failure_body`` adds the kind to a known code's answer, so a client
needs no copy of this table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FailureCategory = Literal["transient", "conflict"]


@dataclass(frozen=True, slots=True)
class FailureKind:
    """What a failure is: its category, whether nothing was done (the same request may go again), how long it lasts."""

    category: FailureCategory
    resend: bool
    after_s: float
    tries: int = 1


FAILURE_KINDS: dict[str, FailureKind] = {
    # The project's Studio is starting or restarting; the Hub dispatched nothing. One that crashed is
    # WORKER_UNAVAILABLE: it waits for a person's recovery, so it is not here.
    "WORKER_STARTING": FailureKind("transient", True, 1.0, 8),
    # A drawing worker stopped before its drawing was done; nothing was retained.
    "DRAWING_WORKER_LOST": FailureKind("transient", True, 0.5, 3),
    # The project index is loading, or applying the last write: answered to reads only.
    "INDEX_UNAVAILABLE": FailureKind("transient", True, 0.5, 6),
    "PROJECTION_INDEX_UNAVAILABLE": FailureKind("transient", True, 0.5, 6),
    # Another process holds the project's writer lease, as while a Runtime restarts; nothing was written.
    "PROJECT_WRITER_BUSY": FailureKind("transient", True, 1.0, 5),
    # The same operation is still running: wait for it and read its result; never send it again.
    "OPERATION_RUNNING": FailureKind("transient", False, 1.0),
    # The project moved under the request: read it again, then decide.
    "WORKING_DRAFT_STALE": FailureKind("conflict", False, 0.0),
    "RUNTIME_CHANGED": FailureKind("conflict", False, 0.0),
}

#: The longest wait between two sends of one request.
LONGEST_WAIT_S = 3.0


def failure_kind(code: object) -> FailureKind | None:
    """The kind a failure code is, or None for one a person or the caller answers."""

    return FAILURE_KINDS.get(code) if isinstance(code, str) else None


def resend_wait(kind: FailureKind, sends: int) -> float | None:
    """How long to wait before sending a request again after ``sends`` sends, or None when it is not sent again."""

    if not kind.resend or sends >= kind.tries:
        return None
    return min(kind.after_s * sends, LONGEST_WAIT_S)


def failure_body(code: str, detail: str) -> dict[str, object]:
    """A failure's answer: its code and sentence and, for a known code, its category and whether it did nothing."""

    body: dict[str, object] = {"code": code, "detail": detail}
    kind = failure_kind(code)
    if kind is not None:
        body.update(category=kind.category, resend=kind.resend)
    return body


__all__ = ["FAILURE_KINDS", "FailureCategory", "FailureKind", "LONGEST_WAIT_S", "failure_body", "failure_kind",
           "resend_wait"]
