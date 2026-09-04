"""The pending intent: what a clarification keeps between the question and
the answer, so the next sentence continues the same intent.

A conversation transcript is not state. When the studio has to ask - which
element, how much, which side - it keeps a small typed record of where the
resolution got to: the digests it was resolved against, the sentence, the
target it settled on (or the candidates it could not choose between), the
property asked for, the slots still missing, the candidates the architect
rejected and the reason code. The record travels on the 422 body under
``pending`` with a continuation token; the reply comes back with the token
and the studio merges it into the same intent. A rejected target is never
carried forward: "not the roof, the columns" replaces the target and puts
the roof on the rejected list.

Held in process, like the proposal store: it is not history, and a restart
drops it. A token that no longer resolves is answered as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import threading
from typing import Any, Mapping
from uuid import uuid4

from ..transport.errors import StudioError

PERSISTENCE = "in-memory (not version history)"

# ---- reason codes a pending intent may carry

AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
MISSING_AMOUNT = "MISSING_AMOUNT"
MISSING_PROPERTY = "MISSING_PROPERTY"
MISSING_TARGET = "MISSING_TARGET"
MODEL_VISIBLE_CATALOG_MISSING = "MODEL_VISIBLE_CATALOG_MISSING"
MISSING_ELEMENT_DECLARATION = "MISSING_ELEMENT_DECLARATION"
UNSUPPORTED_ADD_FIELD = "UNSUPPORTED_ADD_FIELD"
UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"

# The codes after which asking again would only loop: the record itself
# cannot take what was asked for until an authored control exists.
TERMINAL_CODES = frozenset({MODEL_VISIBLE_CATALOG_MISSING, MISSING_ELEMENT_DECLARATION, UNSUPPORTED_ADD_FIELD, UNSUPPORTED_ACTION})


@dataclass(frozen=True, slots=True)
class PendingIntent:
    token: str
    state_digest: str
    artifact_digest: str | None
    original_utterance: str
    utterances: tuple[str, ...]
    target_component_id: str | None
    target_element_id: str | None
    requested_property: str | None
    missing_slots: tuple[str, ...]
    candidates: tuple[str, ...]
    rejected_candidates: tuple[str, ...]
    reason_code: str
    question: str
    created_at: str
    # Whatever the resolver already settled that a reply must not lose:
    # the amount, the direction, the keep refs.
    slots: Mapping[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.reason_code in TERMINAL_CODES

    def to_dict(self) -> dict[str, Any]:
        return {
            "continuationToken": self.token,
            "stateDigest": self.state_digest,
            "artifactDigest": self.artifact_digest,
            "originalUtterance": self.original_utterance,
            "utterances": list(self.utterances),
            "targetComponentId": self.target_component_id,
            "targetElementId": self.target_element_id,
            "requestedProperty": self.requested_property,
            "missingSlots": list(self.missing_slots),
            "candidates": list(self.candidates),
            "rejectedCandidates": list(self.rejected_candidates),
            "reasonCode": self.reason_code,
            "terminal": self.terminal,
            "question": self.question,
            "slots": dict(self.slots),
        }


class PendingIntentStore:
    """The intents this process is still asking about, by continuation token."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PendingIntent] = {}

    def open(
        self,
        *,
        state_digest: str,
        artifact_digest: str | None,
        utterance: str,
        target_component_id: str | None,
        target_element_id: str | None,
        requested_property: str | None,
        missing_slots: tuple[str, ...],
        candidates: tuple[str, ...],
        reason_code: str,
        question: str,
        rejected_candidates: tuple[str, ...] = (),
        slots: Mapping[str, Any] | None = None,
        continue_from: PendingIntent | None = None,
    ) -> PendingIntent:
        """A new pending intent, or the continuation of one with the new facts merged."""

        utterances = (*(continue_from.utterances if continue_from else ()), utterance)
        original = continue_from.original_utterance if continue_from else utterance
        rejected = tuple(
            dict.fromkeys(
                (*(continue_from.rejected_candidates if continue_from else ()), *rejected_candidates)
            )
        )
        merged_slots = {**(continue_from.slots if continue_from else {}), **(slots or {})}
        pending = PendingIntent(
            token=continue_from.token if continue_from else f"pi-{uuid4().hex[:12]}",
            state_digest=state_digest,
            artifact_digest=artifact_digest,
            original_utterance=original,
            utterances=utterances,
            target_component_id=target_component_id,
            target_element_id=target_element_id,
            requested_property=requested_property,
            missing_slots=missing_slots,
            candidates=candidates,
            rejected_candidates=rejected,
            reason_code=reason_code,
            question=question,
            created_at=_now(),
            slots=merged_slots,
        )
        with self._lock:
            self._items[pending.token] = pending
        return pending

    def get(self, token: str) -> PendingIntent:
        with self._lock:
            pending = self._items.get(token)
        if pending is None:
            raise StudioError(
                404,
                "PENDING_INTENT_NOT_FOUND",
                f"no pending intent {token} in this process. Pending intents are "
                f"held {PERSISTENCE}; ask again from the sentence.",
            )
        return pending

    def close(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)

    def reject(self, pending: PendingIntent, rejected: tuple[str, ...]) -> PendingIntent:
        """The same intent with these candidates ruled out, and the target dropped if it was one of them."""

        updated = replace(
            pending,
            rejected_candidates=tuple(dict.fromkeys((*pending.rejected_candidates, *rejected))),
            target_component_id=(
                None if pending.target_component_id in rejected else pending.target_component_id
            ),
            target_element_id=(
                None if pending.target_element_id in rejected else pending.target_element_id
            ),
            candidates=tuple(item for item in pending.candidates if item not in rejected),
        )
        with self._lock:
            self._items[updated.token] = updated
        return updated


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
