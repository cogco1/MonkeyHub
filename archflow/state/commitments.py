"""Typed normative commitments stored in canonical state.

Commitments retain decision-relevant authority and lifecycle state. They do
not contain prompt transcripts, executable predicates, findings, obligations,
or event-log payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar, Mapping

_MAX_TEXT = 1_000
_MAX_REFS = 64
_MAX_JSON_BYTES = 64_000


class CommitmentKind(StrEnum):
    ACHIEVEMENT = "achievement"
    MAINTENANCE = "maintenance"


class CommitmentStrength(StrEnum):
    HARD = "hard"
    NEGOTIABLE = "negotiable"
    PREFERENCE = "preference"
    HYPOTHESIS = "hypothesis"


class CommitmentStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    RELEASED = "released"
    REVISED = "revised"
    SUPERSEDED = "superseded"


class RevisionPolicy(StrEnum):
    OWNER_ONLY = "owner_only"
    NAMED_AUTHORITIES = "named_authorities"
    IMMUTABLE = "immutable"


class CommitmentAuthorityError(PermissionError):
    """The named actor cannot perform a commitment transition."""


class CommitmentTransitionError(ValueError):
    """The requested lifecycle transition is invalid."""


@dataclass(frozen=True, slots=True)
class CriterionRef:
    """Declarative reference to a named validator or monitor."""

    criterion_id: str
    provider_id: str
    subject_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.criterion_id, "criterion_id")
        _text(self.provider_id, "provider_id")
        _refs(self.subject_refs, "subject_refs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "provider_id": self.provider_id,
            "subject_refs": list(self.subject_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> CriterionRef:
        payload = _mapping(value, "criterion")
        _exact_keys(
            payload,
            {"criterion_id", "provider_id", "subject_refs"},
            "criterion",
        )
        return cls(
            criterion_id=_string(payload.get("criterion_id"), "criterion_id"),
            provider_id=_string(payload.get("provider_id"), "provider_id"),
            subject_refs=_string_tuple(
                payload.get("subject_refs"), "subject_refs"
            ),
        )


@dataclass(frozen=True, slots=True)
class Commitment:
    """One bounded normative object, distinct from findings and obligations."""

    SCHEMA: ClassVar[str] = "Commitment@1"

    commitment_id: str
    kind: CommitmentKind
    strength: CommitmentStrength
    status: CommitmentStatus
    authority_id: str
    source_event_ref: str
    satisfaction_criterion: CriterionRef
    evidence_refs: tuple[str, ...] = ()
    scope_refs: tuple[str, ...] = ()
    activation_criterion: CriterionRef | None = None
    revision_policy: RevisionPolicy = RevisionPolicy.OWNER_ONLY
    permitted_authority_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    predecessor_id: str | None = None
    successor_ids: tuple[str, ...] = ()
    monitor_state_ref: str | None = None
    authorized_by: str | None = None

    def __post_init__(self) -> None:
        for value, field in (
            (self.commitment_id, "commitment_id"),
            (self.authority_id, "authority_id"),
            (self.source_event_ref, "source_event_ref"),
        ):
            _text(value, field)
        for value, enum_type, field in (
            (self.kind, CommitmentKind, "kind"),
            (self.strength, CommitmentStrength, "strength"),
            (self.status, CommitmentStatus, "status"),
            (self.revision_policy, RevisionPolicy, "revision_policy"),
        ):
            if not isinstance(value, enum_type):
                raise TypeError(f"{field} must be {enum_type.__name__}")
        if not isinstance(self.satisfaction_criterion, CriterionRef):
            raise TypeError(
                "satisfaction_criterion must be a CriterionRef"
            )
        if (
            self.activation_criterion is not None
            and not isinstance(self.activation_criterion, CriterionRef)
        ):
            raise TypeError(
                "activation_criterion must be a CriterionRef or None"
            )
        for value, field in (
            (self.evidence_refs, "evidence_refs"),
            (self.scope_refs, "scope_refs"),
            (self.permitted_authority_ids, "permitted_authority_ids"),
            (self.dependency_ids, "dependency_ids"),
            (self.successor_ids, "successor_ids"),
        ):
            _refs(value, field)
        _optional_text(self.predecessor_id, "predecessor_id")
        _optional_text(self.monitor_state_ref, "monitor_state_ref")
        _optional_text(self.authorized_by, "authorized_by")

        if self.commitment_id in self.dependency_ids:
            raise ValueError("commitment cannot depend on itself")
        if self.commitment_id in self.successor_ids:
            raise ValueError("commitment cannot succeed itself")
        if self.predecessor_id == self.commitment_id:
            raise ValueError("commitment cannot be its own predecessor")
        if (
            self.revision_policy is RevisionPolicy.NAMED_AUTHORITIES
            and not self.permitted_authority_ids
        ):
            raise ValueError(
                "named-authorities policy requires permitted authorities"
            )
        if (
            self.revision_policy is not RevisionPolicy.NAMED_AUTHORITIES
            and self.permitted_authority_ids
        ):
            raise ValueError(
                "permitted authorities require named-authorities policy"
            )

        authorized_statuses = {
            CommitmentStatus.ACCEPTED,
            CommitmentStatus.ACTIVE,
            CommitmentStatus.SATISFIED,
            CommitmentStatus.VIOLATED,
            CommitmentStatus.REVISED,
        }
        if self.status in authorized_statuses:
            if self.authorized_by is None:
                raise ValueError(
                    f"{self.status.value} commitment lacks authorization"
                )
            if self.authorized_by not in self._authority_set:
                raise ValueError(
                    "authorized_by is not a recognized commitment authority"
                )

    @property
    def _authority_set(self) -> frozenset[str]:
        return frozenset(
            (self.authority_id, *self.permitted_authority_ids)
        )

    @property
    def has_hard_gate_authority(self) -> bool:
        """Whether this object may be consumed by a future hard monitor."""

        return (
            self.strength is CommitmentStrength.HARD
            and self.status
            in {CommitmentStatus.ACCEPTED, CommitmentStatus.ACTIVE}
            and self.authorized_by in self._authority_set
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "commitment_id": self.commitment_id,
            "kind": self.kind.value,
            "strength": self.strength.value,
            "status": self.status.value,
            "authority_id": self.authority_id,
            "authorized_by": self.authorized_by,
            "source_event_ref": self.source_event_ref,
            "evidence_refs": list(self.evidence_refs),
            "scope_refs": list(self.scope_refs),
            "activation_criterion": (
                self.activation_criterion.to_dict()
                if self.activation_criterion is not None
                else None
            ),
            "satisfaction_criterion": self.satisfaction_criterion.to_dict(),
            "revision_policy": self.revision_policy.value,
            "permitted_authority_ids": list(
                self.permitted_authority_ids
            ),
            "dependency_ids": list(self.dependency_ids),
            "predecessor_id": self.predecessor_id,
            "successor_ids": list(self.successor_ids),
            "monitor_state_ref": self.monitor_state_ref,
        }

    def to_json(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("commitment serialization exceeds bounded size")
        return encoded

    @classmethod
    def from_dict(cls, value: object) -> Commitment:
        payload = _mapping(value, "commitment")
        expected = {
            "schema",
            "commitment_id",
            "kind",
            "strength",
            "status",
            "authority_id",
            "authorized_by",
            "source_event_ref",
            "evidence_refs",
            "scope_refs",
            "activation_criterion",
            "satisfaction_criterion",
            "revision_policy",
            "permitted_authority_ids",
            "dependency_ids",
            "predecessor_id",
            "successor_ids",
            "monitor_state_ref",
        }
        _exact_keys(payload, expected, "commitment")
        if payload.get("schema") != cls.SCHEMA:
            raise ValueError("unsupported commitment schema")
        activation = payload.get("activation_criterion")
        return cls(
            commitment_id=_string(
                payload.get("commitment_id"), "commitment_id"
            ),
            kind=_enum(
                CommitmentKind, payload.get("kind"), "kind"
            ),
            strength=_enum(
                CommitmentStrength, payload.get("strength"), "strength"
            ),
            status=_enum(
                CommitmentStatus, payload.get("status"), "status"
            ),
            authority_id=_string(
                payload.get("authority_id"), "authority_id"
            ),
            authorized_by=_optional_string(
                payload.get("authorized_by"), "authorized_by"
            ),
            source_event_ref=_string(
                payload.get("source_event_ref"), "source_event_ref"
            ),
            evidence_refs=_string_tuple(
                payload.get("evidence_refs"), "evidence_refs"
            ),
            scope_refs=_string_tuple(
                payload.get("scope_refs"), "scope_refs"
            ),
            activation_criterion=(
                None if activation is None else CriterionRef.from_dict(activation)
            ),
            satisfaction_criterion=CriterionRef.from_dict(
                payload.get("satisfaction_criterion")
            ),
            revision_policy=_enum(
                RevisionPolicy,
                payload.get("revision_policy"),
                "revision_policy",
            ),
            permitted_authority_ids=_string_tuple(
                payload.get("permitted_authority_ids"),
                "permitted_authority_ids",
            ),
            dependency_ids=_string_tuple(
                payload.get("dependency_ids"), "dependency_ids"
            ),
            predecessor_id=_optional_string(
                payload.get("predecessor_id"), "predecessor_id"
            ),
            successor_ids=_string_tuple(
                payload.get("successor_ids"), "successor_ids"
            ),
            monitor_state_ref=_optional_string(
                payload.get("monitor_state_ref"), "monitor_state_ref"
            ),
        )

    @classmethod
    def from_json(cls, encoded: str) -> Commitment:
        if not isinstance(encoded, str):
            raise TypeError("encoded commitment must be text")
        if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("commitment JSON exceeds bounded size")
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError("commitment JSON is invalid") from exc
        return cls.from_dict(payload)


_TRANSITIONS: dict[CommitmentStatus, frozenset[CommitmentStatus]] = {
    CommitmentStatus.PROPOSED: frozenset(
        {
            CommitmentStatus.ACCEPTED,
            CommitmentStatus.RELEASED,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.ACCEPTED: frozenset(
        {
            CommitmentStatus.ACTIVE,
            CommitmentStatus.RELEASED,
            CommitmentStatus.REVISED,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.ACTIVE: frozenset(
        {
            CommitmentStatus.SATISFIED,
            CommitmentStatus.VIOLATED,
            CommitmentStatus.RELEASED,
            CommitmentStatus.REVISED,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.VIOLATED: frozenset(
        {
            CommitmentStatus.ACTIVE,
            CommitmentStatus.SATISFIED,
            CommitmentStatus.RELEASED,
            CommitmentStatus.REVISED,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.SATISFIED: frozenset(
        {
            CommitmentStatus.RELEASED,
            CommitmentStatus.REVISED,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.RELEASED: frozenset(),
    CommitmentStatus.REVISED: frozenset(),
    CommitmentStatus.SUPERSEDED: frozenset(),
}


def transition_commitment(
    commitment: Commitment,
    to_status: CommitmentStatus,
    *,
    actor_authority_id: str,
    successor_id: str | None = None,
    monitor_state_ref: str | None = None,
) -> Commitment:
    """Apply one authorized lifecycle transition without mutating history."""

    if not isinstance(commitment, Commitment):
        raise TypeError("commitment must be a Commitment")
    if not isinstance(to_status, CommitmentStatus):
        raise TypeError("to_status must be a CommitmentStatus")
    _text(actor_authority_id, "actor_authority_id")
    _optional_text(successor_id, "successor_id")
    _optional_text(monitor_state_ref, "monitor_state_ref")

    if to_status not in _TRANSITIONS[commitment.status]:
        raise CommitmentTransitionError(
            f"cannot transition {commitment.status.value} to {to_status.value}"
        )
    if (
        commitment.kind is CommitmentKind.MAINTENANCE
        and to_status is CommitmentStatus.SATISFIED
    ):
        raise CommitmentTransitionError(
            "maintenance commitments remain active until released; "
            "repair a violation by returning it to active"
        )
    if actor_authority_id not in commitment._authority_set:
        raise CommitmentAuthorityError(
            "actor is not a recognized commitment authority"
        )

    revision_statuses = {
        CommitmentStatus.RELEASED,
        CommitmentStatus.REVISED,
        CommitmentStatus.SUPERSEDED,
    }
    if to_status in revision_statuses:
        _require_revision_authority(commitment, actor_authority_id)
    if to_status in {
        CommitmentStatus.REVISED,
        CommitmentStatus.SUPERSEDED,
    }:
        if successor_id is None:
            raise CommitmentTransitionError(
                f"{to_status.value} transition requires a successor"
            )
        if successor_id == commitment.commitment_id:
            raise CommitmentTransitionError(
                "successor must have another commitment id"
            )
    elif successor_id is not None:
        raise CommitmentTransitionError(
            "successor is allowed only for revision or supersession"
        )

    authorized_by = commitment.authorized_by
    if to_status is CommitmentStatus.ACCEPTED:
        authorized_by = actor_authority_id
    successors = commitment.successor_ids
    if successor_id is not None:
        successors = tuple(dict.fromkeys((*successors, successor_id)))

    return replace(
        commitment,
        status=to_status,
        authorized_by=authorized_by,
        successor_ids=successors,
        monitor_state_ref=(
            monitor_state_ref
            if monitor_state_ref is not None
            else commitment.monitor_state_ref
        ),
    )


def _require_revision_authority(
    commitment: Commitment,
    actor_authority_id: str,
) -> None:
    if commitment.revision_policy is RevisionPolicy.IMMUTABLE:
        raise CommitmentAuthorityError("commitment revision policy is immutable")
    if commitment.revision_policy is RevisionPolicy.OWNER_ONLY:
        allowed = actor_authority_id == commitment.authority_id
    else:
        allowed = actor_authority_id in commitment.permitted_authority_ids
    if not allowed:
        raise CommitmentAuthorityError(
            "actor is not authorized by the commitment revision policy"
        )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return dict(value)


def _exact_keys(
    payload: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"{field} fields drifted; missing={missing}, unknown={unknown}"
        )


def _text(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")


def _string(value: object, field: str) -> str:
    _text(value, field)
    return value  # type: ignore[return-value]


def _optional_text(value: object, field: str) -> None:
    if value is not None:
        _text(value, field)


def _optional_string(value: object, field: str) -> str | None:
    _optional_text(value, field)
    return value  # type: ignore[return-value]


def _refs(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_REFS:
        raise ValueError(f"{field} exceeds bounded item count")
    for item in value:
        _text(item, f"{field} item")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a JSON array")
    result = tuple(value)
    _refs(result, field)
    return result  # type: ignore[return-value]


def _enum(enum_type, value: object, field: str):
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc
