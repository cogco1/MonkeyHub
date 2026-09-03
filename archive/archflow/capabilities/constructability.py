"""Detached, obligation-driven constructability expert input."""

from __future__ import annotations

from dataclasses import dataclass

from archflow.project.refs import require_identifier
from archive.archflow.state.build_policy import BuildPolicy
from archflow.state.operational_state import (
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest, require_sha256


@dataclass(frozen=True, slots=True)
class ConstructabilityObligation:
    obligation_id: str
    topic: str
    statement: str
    source_ref: str

    def __post_init__(self) -> None:
        require_local_id(self.obligation_id, "obligation_id")
        require_local_id(self.topic, "topic")
        _text(self.statement, "statement")
        require_logical_ref(self.source_ref, "source_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "obligation_id": self.obligation_id,
            "topic": self.topic,
            "statement": self.statement,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class ConstructabilitySnapshot:
    project_id: str
    run_id: str
    base_state_sha256: str
    program_digest: str
    site_context_digest: str
    policy_digest: str
    resource_mode: str
    staging_mode: str
    obligations: tuple[ConstructabilityObligation, ...]
    resource_refs: tuple[str, ...]
    protected_rule_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "ConstructabilitySnapshot@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        for value, field in (
            (self.base_state_sha256, "base_state_sha256"),
            (self.program_digest, "program_digest"),
            (self.site_context_digest, "site_context_digest"),
            (self.policy_digest, "policy_digest"),
        ):
            require_sha256(value, field)
        require_local_id(self.resource_mode, "resource_mode")
        require_local_id(self.staging_mode, "staging_mode")
        _typed(
            self.obligations,
            ConstructabilityObligation,
            "obligations",
        )
        _refs(self.resource_refs, "resource_refs", allow_empty=True)
        _refs(
            self.protected_rule_refs,
            "protected_rule_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def snapshot_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_sha256": self.base_state_sha256,
            "program_digest": self.program_digest,
            "site_context_digest": self.site_context_digest,
            "policy_digest": self.policy_digest,
            "resource_mode": self.resource_mode,
            "staging_mode": self.staging_mode,
            "obligations": [
                item.to_dict() for item in self.obligations
            ],
            "resource_refs": list(self.resource_refs),
            "protected_rule_refs": list(self.protected_rule_refs),
            "evidence_refs": list(self.evidence_refs),
            "read_only": True,
            "generation_authority": False,
            "palette_authority": False,
            "world_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class ConstructabilityAdviceReceipt:
    advisor_id: str
    snapshot_digest: str
    proposal_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "ConstructabilityAdviceReceipt@1"

    def __post_init__(self) -> None:
        require_local_id(self.advisor_id, "advisor_id")
        require_sha256(self.snapshot_digest, "snapshot_digest")
        _refs(self.proposal_refs, "proposal_refs")
        _refs(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "advisor_id": self.advisor_id,
            "snapshot_digest": self.snapshot_digest,
            "proposal_refs": list(self.proposal_refs),
            "evidence_refs": list(self.evidence_refs),
            "read_only": True,
            "accepted": False,
        }


def build_constructability_snapshot(
    policy: BuildPolicy,
) -> ConstructabilitySnapshot:
    """Project the current policy and obligations without scheduling experts."""

    if not isinstance(policy, BuildPolicy):
        raise TypeError("policy must be BuildPolicy")
    obligations = tuple(
        ConstructabilityObligation(
            obligation_id=item.obligation_id,
            topic=_topic_for(item.obligation_id),
            statement=item.statement,
            source_ref=item.source_ref,
        )
        for item in policy.obligations
    )
    return ConstructabilitySnapshot(
        project_id=policy.project_id,
        run_id=policy.run_id,
        base_state_sha256=policy.base.require_digest(),
        program_digest=policy.program_digest,
        site_context_digest=policy.site_context_digest,
        policy_digest=policy.policy_digest,
        resource_mode=policy.resource_mode.value,
        staging_mode=policy.staging_mode.value,
        obligations=obligations,
        resource_refs=tuple(
            sorted(
                {
                    *(item.resource_ref for item in policy.availability),
                    *(item.resource_ref for item in policy.demands),
                }
            )
        ),
        protected_rule_refs=tuple(
            f"build-protection:{item.rule_id}"
            for item in policy.protected_rules
        ),
        evidence_refs=policy.evidence_refs,
    )


def _topic_for(obligation_id: str) -> str:
    if ".resource." in obligation_id:
        return "resource"
    if ".staging" in obligation_id:
        return "staging"
    if ".protected" in obligation_id:
        return "protection"
    if ".budget" in obligation_id:
        return "budget"
    return "constructability"


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


def _typed(value: object, item_type: type, field: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise TypeError(f"{field} contains the wrong item type")


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if not value and not allow_empty:
        raise ValueError(f"{field} cannot be empty")
    for item in value:
        require_logical_ref(item, field)
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


