"""Typed append-only design events with hash-chain verification.

The runtime owns the event contract, not a filesystem location. Production
callers must provide a project-owned store; unit tests may provide a disposable
store. Event payloads retain evidence and decision references, while raw model
reasoning and external tool traffic remain in separately referenced artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.project.refs import (
    ProjectVersionRef,
    require_identifier,
)
from archflow.state.operational_state import require_logical_ref
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256


_MAX_ITEMS = 4096
_MAX_DELTA_BYTES = 256_000
_HEX = frozenset("0123456789abcdef")


class EventLogError(ValueError):
    """An event or event chain is invalid."""


class EventDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    OBSERVED = "observed"


class EventRecordStore(Protocol):
    """Persistence seam; implementations own the physical destination."""

    def put_event(
        self,
        *,
        project_id: str,
        sequence: int,
        event_sha256: str,
        payload: Mapping[str, Any],
    ) -> None: ...

    def list_events(
        self,
        *,
        project_id: str,
    ) -> tuple[Mapping[str, Any], ...]: ...


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventLogError(f"{field} must be non-empty text")
    return value


def _refs(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise EventLogError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise EventLogError(f"{field} exceeds bounded item count")
    for item in value:
        require_logical_ref(item, field)
    if len(value) != len(set(value)):
        raise EventLogError(f"{field} contains duplicates")
    return value


def _ref_to_dict(ref: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "version": ref.version,
        "state_sha256": ref.require_digest(),
    }


def _ref_from_dict(
    value: object,
    field: str,
) -> ProjectVersionRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise EventLogError(f"{field} schema drifted")
    try:
        return ProjectVersionRef(
            project_id=value["project_id"],
            version=value["version"],
            state_sha256=value["state_sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise EventLogError(f"{field} is invalid") from exc


@dataclass(frozen=True, slots=True)
class DesignEvent:
    """One immutable event; ``event_sha256`` is derived from all fields."""

    sequence: int
    project_id: str
    event_type: str
    decision: EventDecision
    actor_id: str
    authority_id: str | None
    prior_event_sha256: str | None
    prior_state: ProjectVersionRef | None
    proposed_delta_json: str
    evidence_refs: tuple[str, ...]
    validation_receipt_refs: tuple[str, ...]
    commit_receipt_ref: str | None
    artifact_refs: tuple[str, ...]
    reducer_version: str
    resulting_state: ProjectVersionRef

    SCHEMA = "DesignEvent@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise EventLogError("sequence must be a non-negative integer")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.event_type, "event_type")
        require_identifier(self.actor_id, "actor_id")
        if not isinstance(self.decision, EventDecision):
            raise TypeError("decision must be an EventDecision")
        if self.authority_id is not None:
            require_identifier(self.authority_id, "authority_id")
        if self.prior_event_sha256 is not None:
            require_sha256(self.prior_event_sha256, "prior_event_sha256")
        if self.prior_state is not None:
            if not isinstance(self.prior_state, ProjectVersionRef):
                raise TypeError("prior_state must be a ProjectVersionRef")
            self.prior_state.require_digest()
            if self.prior_state.project_id != self.project_id:
                raise EventLogError("prior_state belongs to another project")
        _text(self.proposed_delta_json, "proposed_delta_json")
        try:
            decoded = json.loads(self.proposed_delta_json)
        except json.JSONDecodeError as exc:
            raise EventLogError("proposed_delta_json is invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise EventLogError("proposed delta must encode an object")
        canonical = canonical_json(decoded)
        if canonical != self.proposed_delta_json:
            raise EventLogError("proposed delta JSON must be canonical")
        if len(canonical.encode("utf-8")) > _MAX_DELTA_BYTES:
            raise EventLogError("proposed delta exceeds bounded size")
        _refs(self.evidence_refs, "evidence_refs")
        _refs(
            self.validation_receipt_refs,
            "validation_receipt_refs",
        )
        _refs(self.artifact_refs, "artifact_refs")
        if self.commit_receipt_ref is not None:
            require_logical_ref(
                self.commit_receipt_ref,
                "commit_receipt_ref",
            )
        _text(self.reducer_version, "reducer_version")
        if not isinstance(self.resulting_state, ProjectVersionRef):
            raise TypeError(
                "resulting_state must be a ProjectVersionRef"
            )
        self.resulting_state.require_digest()
        if self.resulting_state.project_id != self.project_id:
            raise EventLogError(
                "resulting_state belongs to another project"
            )
        if self.sequence == 0:
            if (
                self.prior_event_sha256 is not None
                or self.prior_state is not None
            ):
                raise EventLogError(
                    "initial event cannot have a prior event or state"
                )
        elif (
            self.prior_event_sha256 is None
            or self.prior_state is None
        ):
            raise EventLogError(
                "non-initial event requires prior event and state"
            )

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        project_id: str,
        event_type: str,
        decision: EventDecision,
        actor_id: str,
        authority_id: str | None,
        prior_event_sha256: str | None,
        prior_state: ProjectVersionRef | None,
        proposed_delta: Mapping[str, Any],
        evidence_refs: tuple[str, ...],
        validation_receipt_refs: tuple[str, ...],
        commit_receipt_ref: str | None,
        artifact_refs: tuple[str, ...],
        reducer_version: str,
        resulting_state: ProjectVersionRef,
    ) -> DesignEvent:
        if not isinstance(proposed_delta, Mapping):
            raise TypeError("proposed_delta must be a mapping")
        return cls(
            sequence=sequence,
            project_id=project_id,
            event_type=event_type,
            decision=decision,
            actor_id=actor_id,
            authority_id=authority_id,
            prior_event_sha256=prior_event_sha256,
            prior_state=prior_state,
            proposed_delta_json=canonical_json(dict(proposed_delta)),
            evidence_refs=evidence_refs,
            validation_receipt_refs=validation_receipt_refs,
            commit_receipt_ref=commit_receipt_ref,
            artifact_refs=artifact_refs,
            reducer_version=reducer_version,
            resulting_state=resulting_state,
        )

    @property
    def proposed_delta(self) -> dict[str, Any]:
        decoded = json.loads(self.proposed_delta_json)
        assert isinstance(decoded, dict)
        return decoded

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "sequence": self.sequence,
            "project_id": self.project_id,
            "event_type": self.event_type,
            "decision": self.decision.value,
            "actor_id": self.actor_id,
            "authority_id": self.authority_id,
            "prior_event_sha256": self.prior_event_sha256,
            "prior_state": (
                None
                if self.prior_state is None
                else _ref_to_dict(self.prior_state)
            ),
            "proposed_delta": self.proposed_delta,
            "evidence_refs": list(self.evidence_refs),
            "validation_receipt_refs": list(
                self.validation_receipt_refs
            ),
            "commit_receipt_ref": self.commit_receipt_ref,
            "artifact_refs": list(self.artifact_refs),
            "reducer_version": self.reducer_version,
            "resulting_state": _ref_to_dict(self.resulting_state),
        }

    @property
    def event_sha256(self) -> str:
        return canonical_digest(self.unsigned_dict())

    @property
    def event_id(self) -> str:
        return f"design-event:{self.event_sha256}"

    def to_dict(self) -> dict[str, object]:
        return {
            **self.unsigned_dict(),
            "event_sha256": self.event_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignEvent:
        if not isinstance(value, Mapping):
            raise EventLogError("event must be an object")
        expected = {
            "schema",
            "sequence",
            "project_id",
            "event_type",
            "decision",
            "actor_id",
            "authority_id",
            "prior_event_sha256",
            "prior_state",
            "proposed_delta",
            "evidence_refs",
            "validation_receipt_refs",
            "commit_receipt_ref",
            "artifact_refs",
            "reducer_version",
            "resulting_state",
            "event_sha256",
        }
        if set(value) != expected or value.get("schema") != cls.SCHEMA:
            raise EventLogError("event schema drifted")
        try:
            event = cls.create(
                sequence=value["sequence"],
                project_id=value["project_id"],
                event_type=value["event_type"],
                decision=EventDecision(value["decision"]),
                actor_id=value["actor_id"],
                authority_id=value["authority_id"],
                prior_event_sha256=value["prior_event_sha256"],
                prior_state=(
                    None
                    if value["prior_state"] is None
                    else _ref_from_dict(
                        value["prior_state"],
                        "prior_state",
                    )
                ),
                proposed_delta=value["proposed_delta"],
                evidence_refs=_tuple_from_json(
                    value["evidence_refs"],
                    "evidence_refs",
                ),
                validation_receipt_refs=_tuple_from_json(
                    value["validation_receipt_refs"],
                    "validation_receipt_refs",
                ),
                commit_receipt_ref=value["commit_receipt_ref"],
                artifact_refs=_tuple_from_json(
                    value["artifact_refs"],
                    "artifact_refs",
                ),
                reducer_version=value["reducer_version"],
                resulting_state=_ref_from_dict(
                    value["resulting_state"],
                    "resulting_state",
                ),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, EventLogError):
                raise
            raise EventLogError("event contains invalid values") from exc
        if event.event_sha256 != require_sha256(
            value["event_sha256"],
            "event_sha256",
        ):
            raise EventLogError("event content digest mismatch")
        return event


def _tuple_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise EventLogError(f"{field} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise EventLogError(f"{field} must contain text")
    return tuple(value)


def verify_event_chain(
    events: tuple[DesignEvent, ...],
) -> tuple[DesignEvent, ...]:
    if not isinstance(events, tuple):
        raise TypeError("events must be a tuple")
    if not events:
        raise EventLogError("event chain cannot be empty")
    project_id = events[0].project_id
    previous: DesignEvent | None = None
    for sequence, event in enumerate(events):
        if not isinstance(event, DesignEvent):
            raise TypeError("events must contain DesignEvent values")
        if event.sequence != sequence:
            raise EventLogError("event sequence is reordered or incomplete")
        if event.project_id != project_id:
            raise EventLogError("event chain crosses project identity")
        expected_previous = (
            None if previous is None else previous.event_sha256
        )
        if event.prior_event_sha256 != expected_previous:
            raise EventLogError("event hash linkage is broken")
        previous = event
    return events


class AppendOnlyEventLog:
    """Validates an explicit store before and after every append."""

    def __init__(
        self,
        store: EventRecordStore,
        *,
        project_id: str,
    ) -> None:
        require_identifier(project_id, "project_id")
        self._store = store
        self.project_id = project_id

    def records(self) -> tuple[DesignEvent, ...]:
        payloads = self._store.list_events(project_id=self.project_id)
        if not isinstance(payloads, tuple):
            raise EventLogError("event store must return a tuple")
        events = tuple(DesignEvent.from_dict(item) for item in payloads)
        if not events:
            return ()
        return verify_event_chain(events)

    def append(self, event: DesignEvent) -> DesignEvent:
        if not isinstance(event, DesignEvent):
            raise TypeError("event must be a DesignEvent")
        if event.project_id != self.project_id:
            raise EventLogError("event belongs to another project")
        current = self.records()
        expected_sequence = len(current)
        expected_prior = (
            None if not current else current[-1].event_sha256
        )
        if event.sequence != expected_sequence:
            raise EventLogError("append sequence is not next")
        if event.prior_event_sha256 != expected_prior:
            raise EventLogError("append does not extend current hash head")
        self._store.put_event(
            project_id=self.project_id,
            sequence=event.sequence,
            event_sha256=event.event_sha256,
            payload=event.to_dict(),
        )
        reloaded = self.records()
        if len(reloaded) != expected_sequence + 1:
            raise EventLogError("event store did not append exactly once")
        if reloaded[-1] != event:
            raise EventLogError("reloaded event differs from appended event")
        return event
