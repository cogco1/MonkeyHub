"""Project-scoped human clarification records with no state-write authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.operational_state import (
    FactValue,
    StateDomain,
    require_local_id,
    require_logical_ref,
)


_MAX_ITEMS = 128
_MAX_TEXT = 1_000
_MAX_VALUE_BYTES = 8_000
_MAX_REQUEST_LIFETIME = timedelta(days=30)
_MAX_RECEIPT_LIFETIME = timedelta(days=7)
_CLARIFIABLE_FACT_DOMAINS = {
    StateDomain.BRIEF,
    StateDomain.SEMANTIC,
    StateDomain.PARAMETER,
    StateDomain.DECISION,
}
_FORBIDDEN_TARGET_PREFIXES = (
    "approval:",
    "candidate:",
    "hard-gate:",
    "promotion:",
    "world:",
)


class ClarificationDisposition(StrEnum):
    SELECTED = "selected"
    REVISED = "revised"
    DECLINED = "declined"
    UNRESOLVED = "unresolved"


class CommitmentClarificationAction(StrEnum):
    NONE = "none"
    AUTHORIZE = "authorize"
    REVISE = "revise"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class ClarifiedFactValue:
    domain: StateDomain
    key: str
    value: FactValue | object
    qualification: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.domain, StateDomain):
            raise TypeError("domain must be StateDomain")
        if self.domain not in _CLARIFIABLE_FACT_DOMAINS:
            raise ValueError(
                "clarification cannot write geometry evaluation deliverable "
                "or external-authority facts"
            )
        require_local_id(self.key, "key")
        object.__setattr__(self, "value", FactValue.from_value(self.value))
        if self.qualification is not None:
            _text(self.qualification, "qualification")
        _bounded_value(self.value.to_python())

    @property
    def ref(self) -> str:
        return f"fact:{self.domain.value}:{self.key}"

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain.value,
            "key": self.key,
            "value": self.value.to_python(),
            "qualification": self.qualification,
        }

    @classmethod
    def from_dict(cls, value: object) -> ClarifiedFactValue:
        payload = _mapping(value, "clarified fact")
        _exact(
            payload,
            {"domain", "key", "value", "qualification"},
            "clarified fact",
        )
        return cls(
            domain=_enum(StateDomain, payload["domain"], "domain"),
            key=payload["key"],
            value=payload["value"],
            qualification=payload["qualification"],
        )


@dataclass(frozen=True, slots=True)
class ClarificationEffect:
    fact_updates: tuple[ClarifiedFactValue, ...] = ()
    commitment_action: CommitmentClarificationAction = (
        CommitmentClarificationAction.NONE
    )
    commitment_id: str | None = None
    replacement_commitment_id: str | None = None

    def __post_init__(self) -> None:
        _typed_tuple(
            self.fact_updates,
            ClarifiedFactValue,
            "fact_updates",
        )
        _unique(
            tuple(item.ref for item in self.fact_updates),
            "fact update refs",
        )
        if not isinstance(
            self.commitment_action,
            CommitmentClarificationAction,
        ):
            raise TypeError(
                "commitment_action must be CommitmentClarificationAction"
            )
        if self.commitment_action is CommitmentClarificationAction.NONE:
            if self.commitment_id is not None or (
                self.replacement_commitment_id is not None
            ):
                raise ValueError("none action cannot name commitments")
        elif self.commitment_action in {
            CommitmentClarificationAction.AUTHORIZE,
            CommitmentClarificationAction.RELEASE,
        }:
            require_local_id(self.commitment_id, "commitment_id")
            if self.replacement_commitment_id is not None:
                raise ValueError(
                    "authorize/release cannot name a replacement"
                )
        else:
            require_local_id(self.commitment_id, "commitment_id")
            require_local_id(
                self.replacement_commitment_id,
                "replacement_commitment_id",
            )
            if self.commitment_id == self.replacement_commitment_id:
                raise ValueError("replacement commitment must be distinct")
        if (
            not self.fact_updates
            and self.commitment_action
            is CommitmentClarificationAction.NONE
        ):
            raise ValueError("clarification effect must have a typed effect")

    @property
    def target_refs(self) -> tuple[str, ...]:
        refs = [item.ref for item in self.fact_updates]
        if self.commitment_id is not None:
            refs.append(f"commitment:{self.commitment_id}")
        if self.replacement_commitment_id is not None:
            refs.append(
                f"commitment:{self.replacement_commitment_id}"
            )
        return tuple(sorted(refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "fact_updates": [
                item.to_dict() for item in self.fact_updates
            ],
            "commitment_action": self.commitment_action.value,
            "commitment_id": self.commitment_id,
            "replacement_commitment_id": self.replacement_commitment_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> ClarificationEffect:
        payload = _mapping(value, "clarification effect")
        _exact(
            payload,
            {
                "fact_updates",
                "commitment_action",
                "commitment_id",
                "replacement_commitment_id",
            },
            "clarification effect",
        )
        return cls(
            fact_updates=tuple(
                ClarifiedFactValue.from_dict(item)
                for item in _list(
                    payload["fact_updates"],
                    "fact_updates",
                )
            ),
            commitment_action=_enum(
                CommitmentClarificationAction,
                payload["commitment_action"],
                "commitment_action",
            ),
            commitment_id=payload["commitment_id"],
            replacement_commitment_id=payload[
                "replacement_commitment_id"
            ],
        )


@dataclass(frozen=True, slots=True)
class ClarificationAlternative:
    alternative_id: str
    label: str
    effect: ClarificationEffect

    def __post_init__(self) -> None:
        require_identifier(self.alternative_id, "alternative_id")
        _text(self.label, "label")
        if not isinstance(self.effect, ClarificationEffect):
            raise TypeError("effect must be ClarificationEffect")

    def to_dict(self) -> dict[str, object]:
        return {
            "alternative_id": self.alternative_id,
            "label": self.label,
            "effect": self.effect.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ClarificationAlternative:
        payload = _mapping(value, "clarification alternative")
        _exact(
            payload,
            {"alternative_id", "label", "effect"},
            "clarification alternative",
        )
        return cls(
            alternative_id=payload["alternative_id"],
            label=payload["label"],
            effect=ClarificationEffect.from_dict(payload["effect"]),
        )


@dataclass(frozen=True, slots=True)
class ClarificationRequest:
    branch: BranchRef
    operational_state_digest: str
    requesting_agent_id: str
    authority_ids: tuple[str, ...]
    obligation_id: str
    target_refs: tuple[str, ...]
    question: str
    blocked_reason: str
    alternatives: tuple[ClarificationAlternative, ...]
    allow_open_response: bool
    created_at_utc: str
    expires_at_utc: str

    SCHEMA = "ClarificationRequest@1"

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        _sha256(
            self.operational_state_digest,
            "operational_state_digest",
        )
        require_identifier(self.requesting_agent_id, "requesting_agent_id")
        _ids(self.authority_ids, "authority_ids", allow_empty=False)
        if self.requesting_agent_id in self.authority_ids:
            raise ValueError(
                "requesting Agent cannot answer its own clarification"
            )
        require_local_id(self.obligation_id, "obligation_id")
        _refs(self.target_refs, "target_refs")
        if any(
            ref.startswith(_FORBIDDEN_TARGET_PREFIXES)
            for ref in self.target_refs
        ):
            raise ValueError(
                "clarification cannot target candidate hard-gate approval "
                "promotion or world authority"
            )
        if f"obligation:{self.obligation_id}" not in self.target_refs:
            raise ValueError("request must target its unresolved obligation")
        _text(self.question, "question")
        _text(self.blocked_reason, "blocked_reason")
        _typed_tuple(
            self.alternatives,
            ClarificationAlternative,
            "alternatives",
        )
        _unique(
            tuple(item.alternative_id for item in self.alternatives),
            "alternative ids",
        )
        if not isinstance(self.allow_open_response, bool):
            raise TypeError("allow_open_response must be boolean")
        if not self.alternatives and not self.allow_open_response:
            raise ValueError("request has no permitted response channel")
        created = _utc(self.created_at_utc, "created_at_utc")
        expires = _utc(self.expires_at_utc, "expires_at_utc")
        if expires <= created or expires - created > _MAX_REQUEST_LIFETIME:
            raise ValueError("clarification request lifetime is invalid")

    @property
    def project_id(self) -> str:
        return self.branch.run.project_id

    @property
    def run_id(self) -> str:
        return self.branch.run.run_id

    @property
    def canonical_base(self) -> ProjectVersionRef:
        return self.branch.run.base

    @property
    def request_digest(self) -> str:
        return _digest(self._identity())

    @property
    def request_id(self) -> str:
        return f"clarification-{self.request_digest[:20]}"

    @property
    def ref(self) -> str:
        return f"clarification-request:{self.request_id}"

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "branch": _branch_dict(self.branch),
            "operational_state_digest": self.operational_state_digest,
            "requesting_agent_id": self.requesting_agent_id,
            "authority_ids": sorted(self.authority_ids),
            "obligation_id": self.obligation_id,
            "target_refs": sorted(self.target_refs),
            "question": self.question,
            "blocked_reason": self.blocked_reason,
            "alternatives": [
                item.to_dict()
                for item in sorted(
                    self.alternatives,
                    key=lambda item: item.alternative_id,
                )
            ],
            "allow_open_response": self.allow_open_response,
            "created_at_utc": self.created_at_utc,
            "expires_at_utc": self.expires_at_utc,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            "purpose": "design_clarification",
            "hard_gate_waiver_authority": False,
            "candidate_approval_authority": False,
            "world_mutation_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ClarificationRequest:
        payload = _mapping(value, "clarification request")
        expected = {
            "schema",
            "branch",
            "operational_state_digest",
            "requesting_agent_id",
            "authority_ids",
            "obligation_id",
            "target_refs",
            "question",
            "blocked_reason",
            "alternatives",
            "allow_open_response",
            "created_at_utc",
            "expires_at_utc",
            "request_id",
            "request_digest",
            "purpose",
            "hard_gate_waiver_authority",
            "candidate_approval_authority",
            "world_mutation_authority",
        }
        _exact(payload, expected, "clarification request")
        _deny_external_authority(payload, cls.SCHEMA)
        request = cls(
            branch=_branch_from_dict(payload["branch"]),
            operational_state_digest=payload[
                "operational_state_digest"
            ],
            requesting_agent_id=payload["requesting_agent_id"],
            authority_ids=_string_tuple(
                payload["authority_ids"],
                "authority_ids",
            ),
            obligation_id=payload["obligation_id"],
            target_refs=_string_tuple(
                payload["target_refs"],
                "target_refs",
            ),
            question=payload["question"],
            blocked_reason=payload["blocked_reason"],
            alternatives=tuple(
                ClarificationAlternative.from_dict(item)
                for item in _list(
                    payload["alternatives"],
                    "alternatives",
                )
            ),
            allow_open_response=payload["allow_open_response"],
            created_at_utc=payload["created_at_utc"],
            expires_at_utc=payload["expires_at_utc"],
        )
        if (
            payload["request_id"] != request.request_id
            or payload["request_digest"] != request.request_digest
        ):
            raise ValueError("clarification request digest mismatch")
        return request


@dataclass(frozen=True, slots=True)
class AuthorityDecisionReceipt:
    request_id: str
    request_digest: str
    branch: BranchRef
    operational_state_digest: str
    authority_id: str
    disposition: ClarificationDisposition
    selected_alternative_id: str | None
    revised_effect: ClarificationEffect | None
    authority_event_ref: str
    issued_at_utc: str
    valid_until_utc: str

    SCHEMA = "AuthorityDecisionReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.request_id, "request_id")
        _sha256(self.request_digest, "request_digest")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        _sha256(
            self.operational_state_digest,
            "operational_state_digest",
        )
        require_identifier(self.authority_id, "authority_id")
        if not isinstance(self.disposition, ClarificationDisposition):
            raise TypeError(
                "disposition must be ClarificationDisposition"
            )
        if self.disposition is ClarificationDisposition.SELECTED:
            require_identifier(
                self.selected_alternative_id,
                "selected_alternative_id",
            )
            if self.revised_effect is not None:
                raise ValueError("selected response cannot carry revision")
        elif self.disposition is ClarificationDisposition.REVISED:
            if self.selected_alternative_id is not None or not isinstance(
                self.revised_effect,
                ClarificationEffect,
            ):
                raise ValueError(
                    "revised response requires one typed open effect"
                )
        elif (
            self.selected_alternative_id is not None
            or self.revised_effect is not None
        ):
            raise ValueError(
                "declined/unresolved response cannot carry an effect"
            )
        require_logical_ref(
            self.authority_event_ref,
            "authority_event_ref",
        )
        issued = _utc(self.issued_at_utc, "issued_at_utc")
        valid_until = _utc(
            self.valid_until_utc,
            "valid_until_utc",
        )
        if (
            valid_until <= issued
            or valid_until - issued > _MAX_RECEIPT_LIFETIME
        ):
            raise ValueError("authority receipt lifetime is invalid")

    @property
    def receipt_digest(self) -> str:
        return _digest(self._identity())

    @property
    def receipt_id(self) -> str:
        return f"authority-{self.receipt_digest[:20]}"

    @property
    def ref(self) -> str:
        return f"authority-receipt:{self.receipt_id}"

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            "branch": _branch_dict(self.branch),
            "operational_state_digest": self.operational_state_digest,
            "authority_id": self.authority_id,
            "disposition": self.disposition.value,
            "selected_alternative_id": self.selected_alternative_id,
            "revised_effect": (
                self.revised_effect.to_dict()
                if self.revised_effect is not None
                else None
            ),
            "authority_event_ref": self.authority_event_ref,
            "issued_at_utc": self.issued_at_utc,
            "valid_until_utc": self.valid_until_utc,
            "max_uses": 1,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
            "purpose": "design_clarification",
            "hard_gate_waiver_authority": False,
            "candidate_approval_authority": False,
            "world_mutation_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> AuthorityDecisionReceipt:
        payload = _mapping(value, "authority decision receipt")
        expected = {
            "schema",
            "request_id",
            "request_digest",
            "branch",
            "operational_state_digest",
            "authority_id",
            "disposition",
            "selected_alternative_id",
            "revised_effect",
            "authority_event_ref",
            "issued_at_utc",
            "valid_until_utc",
            "max_uses",
            "receipt_id",
            "receipt_digest",
            "purpose",
            "hard_gate_waiver_authority",
            "candidate_approval_authority",
            "world_mutation_authority",
        }
        _exact(payload, expected, "authority decision receipt")
        _deny_external_authority(payload, cls.SCHEMA)
        if payload["max_uses"] != 1:
            raise ValueError("authority receipt must be single-use")
        effect = payload["revised_effect"]
        receipt = cls(
            request_id=payload["request_id"],
            request_digest=payload["request_digest"],
            branch=_branch_from_dict(payload["branch"]),
            operational_state_digest=payload[
                "operational_state_digest"
            ],
            authority_id=payload["authority_id"],
            disposition=_enum(
                ClarificationDisposition,
                payload["disposition"],
                "disposition",
            ),
            selected_alternative_id=payload[
                "selected_alternative_id"
            ],
            revised_effect=(
                None
                if effect is None
                else ClarificationEffect.from_dict(effect)
            ),
            authority_event_ref=payload["authority_event_ref"],
            issued_at_utc=payload["issued_at_utc"],
            valid_until_utc=payload["valid_until_utc"],
        )
        if (
            payload["receipt_id"] != receipt.receipt_id
            or payload["receipt_digest"] != receipt.receipt_digest
        ):
            raise ValueError("authority receipt digest mismatch")
        return receipt


def parse_utc(value: str) -> datetime:
    """Public deterministic parser used by the runtime validity checks."""

    return _utc(value, "timestamp")


def _deny_external_authority(
    payload: Mapping[str, Any],
    schema: str,
) -> None:
    if payload["schema"] != schema:
        raise ValueError("unsupported clarification schema")
    if payload["purpose"] != "design_clarification":
        raise ValueError("clarification purpose drifted")
    for field in (
        "hard_gate_waiver_authority",
        "candidate_approval_authority",
        "world_mutation_authority",
    ):
        if payload[field] is not False:
            raise ValueError(
                "clarification cannot gain downstream authority"
            )


def _branch_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
        "canonical_base": {
            "project_id": branch.run.base.project_id,
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.require_digest(),
        },
    }


def _branch_from_dict(value: object) -> BranchRef:
    payload = _mapping(value, "branch")
    _exact(
        payload,
        {
            "project_id",
            "run_id",
            "branch_id",
            "epoch",
            "canonical_base",
        },
        "branch",
    )
    base = _mapping(payload["canonical_base"], "canonical_base")
    _exact(
        base,
        {"project_id", "version", "state_sha256"},
        "canonical_base",
    )
    project_id = payload["project_id"]
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=base["project_id"],
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
        ),
        branch_id=payload["branch_id"],
        epoch=payload["epoch"],
    )


def _bounded_value(value: object) -> None:
    forbidden = {
        "chat_history",
        "document",
        "document_text",
        "full_text",
        "prompt_history",
        "raw_text",
        "transcript",
    }
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("clarification value must be finite JSON") from exc
    if len(encoded.encode("utf-8")) > _MAX_VALUE_BYTES:
        raise ValueError("clarification value exceeds bounded state size")

    def inspect(item: object) -> None:
        if isinstance(item, dict):
            if any(str(key).lower() in forbidden for key in item):
                raise ValueError(
                    "chat transcripts and source documents cannot enter "
                    "clarification state"
                )
            for nested in item.values():
                inspect(nested)
        elif isinstance(item, list):
            for nested in item:
                inspect(nested)

    inspect(value)


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be canonical UTC text") from exc
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace(
        "+00:00",
        "Z",
    ) != value:
        raise ValueError(f"{field} must be canonical UTC text")
    return parsed


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")


def _sha256(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value.lower())
    ):
        raise ValueError(f"{field} must be a SHA-256 digest")


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _typed_tuple(
    value: object,
    expected: type[object],
    field: str,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    if any(not isinstance(item, expected) for item in value):
        raise TypeError(f"{field} must contain {expected.__name__}")


def _ids(
    value: object,
    field: str,
    *,
    allow_empty: bool,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if (not allow_empty and not value) or len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} has invalid item count")
    for item in value:
        require_identifier(item, f"{field} item")
    _unique(value, field)


def _refs(value: object, field: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{field} must be a non-empty tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    for item in value:
        require_logical_ref(item, f"{field} item")
    _unique(value, field)


def _unique(value: tuple[str, ...], field: str) -> None:
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _exact(
    payload: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(payload) != expected:
        raise ValueError(f"{field} schema drifted")


def _enum(
    enum_type: type[StrEnum],
    value: object,
    field: str,
) -> Any:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field} has unsupported value") from exc
