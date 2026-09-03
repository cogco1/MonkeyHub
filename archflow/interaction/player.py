"""UI-neutral player authority contracts for candidate-stage decisions.

These values describe authority and preference.  They never mutate a world,
waive a validator, or advance canonical project state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Mapping

from archflow.interaction.clarification import (
    AuthorityDecisionReceipt,
    parse_utc,
)
from archflow.project import ProjectVersionRef
from archflow.project.refs import require_identifier
from archflow.state.operational_state import require_logical_ref
from archflow.contracts.canonical import canonical_digest, canonical_json


_HEX = frozenset("0123456789abcdef")
_MAX_VALIDITY_SECONDS = 86_400


class PlayerAuthorityError(ValueError):
    """A player decision is stale, ambiguous, or outside named authority."""


class CandidateApprovalMode(StrEnum):
    HUMAN_REQUIRED = "human_required"
    PREAUTHORIZED_DISPOSABLE = "preauthorized_disposable"


class CandidateApprovalSource(StrEnum):
    HUMAN_DECISION = "human_decision"
    PREAUTHORIZED_POLICY = "preauthorized_policy"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerAuthorityError(f"{field} must be non-empty text")
    return value


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise PlayerAuthorityError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _refs(values: object, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise PlayerAuthorityError(f"{field} must be a non-empty tuple")
    for value in values:
        require_logical_ref(value, field)
    if len(values) != len(set(values)):
        raise PlayerAuthorityError(f"{field} contains duplicates")
    return values


def _base_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "version",
        "state_sha256",
    }:
        raise PlayerAuthorityError("base schema drifted")
    return ProjectVersionRef(
        project_id=value["project_id"],
        version=value["version"],
        state_sha256=value["state_sha256"],
    )


@dataclass(frozen=True, slots=True)
class CandidateApprovalPolicy:
    """Project-supplied policy; no default authority is inferred by ArchFlow."""

    policy_ref: str
    authority_ids: tuple[str, ...]
    mode: CandidateApprovalMode
    max_validity_seconds: int
    allows_disposable_automation: bool
    authorization_event_ref: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "CandidateApprovalPolicy@1"

    def __post_init__(self) -> None:
        require_logical_ref(self.policy_ref, "policy_ref")
        if not isinstance(self.authority_ids, tuple) or not self.authority_ids:
            raise PlayerAuthorityError(
                "approval policy requires named authority ids"
            )
        for value in self.authority_ids:
            require_identifier(value, "authority_id")
        if len(self.authority_ids) != len(set(self.authority_ids)):
            raise PlayerAuthorityError("authority_ids contains duplicates")
        if not isinstance(self.mode, CandidateApprovalMode):
            raise TypeError("mode must be CandidateApprovalMode")
        if (
            not isinstance(self.max_validity_seconds, int)
            or isinstance(self.max_validity_seconds, bool)
            or not 1 <= self.max_validity_seconds <= _MAX_VALIDITY_SECONDS
        ):
            raise PlayerAuthorityError(
                "max_validity_seconds must be inside [1, 86400]"
            )
        if not isinstance(self.allows_disposable_automation, bool):
            raise TypeError("allows_disposable_automation must be bool")
        if (
            self.mode is CandidateApprovalMode.HUMAN_REQUIRED
            and self.allows_disposable_automation
        ):
            raise PlayerAuthorityError(
                "human-required policy cannot authorize automation"
            )
        if (
            self.mode is CandidateApprovalMode.PREAUTHORIZED_DISPOSABLE
            and not self.allows_disposable_automation
        ):
            raise PlayerAuthorityError(
                "preauthorized mode must explicitly allow automation"
            )
        require_logical_ref(
            self.authorization_event_ref,
            "authorization_event_ref",
        )
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def policy_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_ref": self.policy_ref,
            "authority_ids": list(self.authority_ids),
            "mode": self.mode.value,
            "max_validity_seconds": self.max_validity_seconds,
            "allows_disposable_automation": (
                self.allows_disposable_automation
            ),
            "authorization_event_ref": self.authorization_event_ref,
            "evidence_refs": list(self.evidence_refs),
            "hard_gate_waiver_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateApprovalPolicy:
        if not isinstance(value, Mapping):
            raise TypeError("approval policy must be an object")
        expected = {
            "schema",
            "policy_ref",
            "authority_ids",
            "mode",
            "max_validity_seconds",
            "allows_disposable_automation",
            "authorization_event_ref",
            "evidence_refs",
            "hard_gate_waiver_authority",
            "canonical_write_authority",
        }
        if set(value) != expected or value["schema"] != cls.SCHEMA:
            raise PlayerAuthorityError("approval policy schema drifted")
        if (
            value["hard_gate_waiver_authority"] is not False
            or value["canonical_write_authority"] is not False
        ):
            raise PlayerAuthorityError(
                "approval policy acquired downstream authority"
            )
        authorities = value["authority_ids"]
        evidence = value["evidence_refs"]
        if not isinstance(authorities, list) or not isinstance(evidence, list):
            raise TypeError("approval policy lists are invalid")
        return cls(
            policy_ref=value["policy_ref"],
            authority_ids=tuple(authorities),
            mode=CandidateApprovalMode(value["mode"]),
            max_validity_seconds=value["max_validity_seconds"],
            allows_disposable_automation=value[
                "allows_disposable_automation"
            ],
            authorization_event_ref=value["authorization_event_ref"],
            evidence_refs=tuple(evidence),
        )


@dataclass(frozen=True, slots=True)
class CandidateApprovalReceipt:
    """Approval of one exact candidate/plan/base, never a validation result."""

    candidate_assembly_digest: str
    submission_id: str
    plan_digest: str
    base: ProjectVersionRef
    workspace_id: str
    policy_ref: str
    policy_digest: str
    authority_id: str
    source: CandidateApprovalSource
    authority_identity_receipt_ref: str | None
    approval_event_ref: str
    issued_at_utc: str
    valid_until_utc: str

    SCHEMA = "CandidateApprovalReceipt@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.candidate_assembly_digest, "candidate_assembly_digest"),
            (self.plan_digest, "plan_digest"),
            (self.policy_digest, "policy_digest"),
        ):
            _sha(value, field)
        require_identifier(self.submission_id, "submission_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        require_identifier(self.workspace_id, "workspace_id")
        require_logical_ref(self.policy_ref, "policy_ref")
        require_identifier(self.authority_id, "authority_id")
        if not isinstance(self.source, CandidateApprovalSource):
            raise TypeError("source must be CandidateApprovalSource")
        if self.source is CandidateApprovalSource.HUMAN_DECISION:
            if self.authority_identity_receipt_ref is None:
                raise PlayerAuthorityError(
                    "human approval requires an authority identity receipt"
                )
            require_logical_ref(
                self.authority_identity_receipt_ref,
                "authority_identity_receipt_ref",
            )
        elif self.authority_identity_receipt_ref is not None:
            raise PlayerAuthorityError(
                "preauthorization cannot impersonate a human receipt"
            )
        require_logical_ref(self.approval_event_ref, "approval_event_ref")
        issued = parse_utc(self.issued_at_utc)
        valid_until = parse_utc(self.valid_until_utc)
        if valid_until <= issued:
            raise PlayerAuthorityError(
                "approval validity must end after issuance"
            )
        if (
            valid_until - issued
            > timedelta(seconds=_MAX_VALIDITY_SECONDS)
        ):
            raise PlayerAuthorityError("approval lifetime exceeds one day")

    @property
    def approval_digest(self) -> str:
        return canonical_digest(self._identity())

    @property
    def approval_id(self) -> str:
        return f"candidate-approval-{self.approval_digest[:20]}"

    @property
    def ref(self) -> str:
        return f"candidate-approval:{self.approval_id}"

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "submission_id": self.submission_id,
            "plan_digest": self.plan_digest,
            "base": _base_dict(self.base),
            "workspace_id": self.workspace_id,
            "policy_ref": self.policy_ref,
            "policy_digest": self.policy_digest,
            "authority_id": self.authority_id,
            "source": self.source.value,
            "authority_identity_receipt_ref": (
                self.authority_identity_receipt_ref
            ),
            "approval_event_ref": self.approval_event_ref,
            "issued_at_utc": self.issued_at_utc,
            "valid_until_utc": self.valid_until_utc,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "approval_id": self.approval_id,
            "approval_digest": self.approval_digest,
            "hard_gate_waiver_authority": False,
            "commitment_waiver_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateApprovalReceipt:
        if not isinstance(value, Mapping):
            raise TypeError("candidate approval receipt must be an object")
        expected = {
            "schema",
            "candidate_assembly_digest",
            "submission_id",
            "plan_digest",
            "base",
            "workspace_id",
            "policy_ref",
            "policy_digest",
            "authority_id",
            "source",
            "authority_identity_receipt_ref",
            "approval_event_ref",
            "issued_at_utc",
            "valid_until_utc",
            "approval_id",
            "approval_digest",
            "hard_gate_waiver_authority",
            "commitment_waiver_authority",
            "canonical_write_authority",
        }
        if set(value) != expected or value["schema"] != cls.SCHEMA:
            raise PlayerAuthorityError(
                "candidate approval receipt schema drifted"
            )
        if any(
            value[field] is not False
            for field in (
                "hard_gate_waiver_authority",
                "commitment_waiver_authority",
                "canonical_write_authority",
            )
        ):
            raise PlayerAuthorityError(
                "candidate approval acquired forbidden authority"
            )
        receipt = cls(
            candidate_assembly_digest=value[
                "candidate_assembly_digest"
            ],
            submission_id=value["submission_id"],
            plan_digest=value["plan_digest"],
            base=_base_from_dict(value["base"]),
            workspace_id=value["workspace_id"],
            policy_ref=value["policy_ref"],
            policy_digest=value["policy_digest"],
            authority_id=value["authority_id"],
            source=CandidateApprovalSource(value["source"]),
            authority_identity_receipt_ref=value[
                "authority_identity_receipt_ref"
            ],
            approval_event_ref=value["approval_event_ref"],
            issued_at_utc=value["issued_at_utc"],
            valid_until_utc=value["valid_until_utc"],
        )
        if (
            value["approval_id"] != receipt.approval_id
            or value["approval_digest"] != receipt.approval_digest
        ):
            raise PlayerAuthorityError("candidate approval digest mismatch")
        return receipt


def validate_human_identity(
    receipt: AuthorityDecisionReceipt,
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    authority_id: str,
    now_utc: str,
) -> None:
    """Use P043 evidence only as identity; it still grants no approval power."""

    if not isinstance(receipt, AuthorityDecisionReceipt):
        raise TypeError("receipt must be AuthorityDecisionReceipt")
    if (
        receipt.branch.run.project_id != project_id
        or receipt.branch.run.run_id != run_id
        or receipt.branch.run.base != base
        or receipt.authority_id != authority_id
    ):
        raise PlayerAuthorityError(
            "clarification authority identity is not exact for this candidate"
        )
    now = parse_utc(now_utc)
    if (
        now < parse_utc(receipt.issued_at_utc)
        or now > parse_utc(receipt.valid_until_utc)
    ):
        raise PlayerAuthorityError(
            "clarification authority identity is outside validity"
        )
