"""First-class, exact-branch evidence-to-claim binding contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_identifiers,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef


class EvidenceModality(StrEnum):
    TEXT_FACT = "text_fact"
    VISUAL_REGION = "visual_region"
    DRAWING_OBSERVATION = "drawing_observation"
    MEASUREMENT = "measurement"
    DERIVATION = "derivation"


class EpistemicRole(StrEnum):
    OBSERVATION = "observation"
    AUTHOR_DECLARATION = "author_declaration"
    HYPOTHESIS = "hypothesis"
    DERIVATION = "derivation"


@dataclass(frozen=True, slots=True)
class EvidenceClaimBinding:
    """One adopted or candidate claim bound to an exact research target."""

    binding_id: str
    branch: BranchRef
    scope_digest: str
    obligation_id: str
    target_ref: str
    fact_ref: str
    source_ref: str
    source_family_ref: str
    claim_key: str
    position_key: str
    modality: EvidenceModality = EvidenceModality.TEXT_FACT
    epistemic_role: EpistemicRole = EpistemicRole.OBSERVATION
    authority_ref: str | None = None
    qualifiers: tuple[str, ...] = ()

    SCHEMA = "EvidenceClaimBinding@1"

    def __post_init__(self) -> None:
        identifier(self.binding_id, "binding_id")
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        identifier(self.obligation_id, "obligation_id")
        for value, field in (
            (self.target_ref, "target_ref"),
            (self.fact_ref, "fact_ref"),
            (self.source_ref, "source_ref"),
            (self.source_family_ref, "source_family_ref"),
            (self.claim_key, "claim_key"),
            (self.position_key, "position_key"),
        ):
            logical_ref(value, field)
        if not isinstance(self.modality, EvidenceModality):
            raise TypeError("modality must be EvidenceModality")
        if not isinstance(self.epistemic_role, EpistemicRole):
            raise TypeError("epistemic_role must be EpistemicRole")
        if self.authority_ref is not None:
            logical_ref(self.authority_ref, "authority_ref")
        if (
            self.epistemic_role is EpistemicRole.AUTHOR_DECLARATION
            and self.authority_ref is None
        ):
            raise ValueError("author declaration requires authority_ref")
        deterministic_identifiers(
            self.qualifiers,
            "qualifiers",
            allow_empty=True,
        )

    @property
    def binding_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return (
            f"evidence-claim-binding:{self.binding_id}:"
            f"{self.binding_digest}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "binding_id": self.binding_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "obligation_id": self.obligation_id,
            "target_ref": self.target_ref,
            "fact_ref": self.fact_ref,
            "source_ref": self.source_ref,
            "source_family_ref": self.source_family_ref,
            "claim_key": self.claim_key,
            "position_key": self.position_key,
            "modality": self.modality.value,
            "epistemic_role": self.epistemic_role.value,
            "authority_ref": self.authority_ref,
            "qualifiers": list(self.qualifiers),
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceClaimBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "binding_id",
                "branch",
                "scope_digest",
                "obligation_id",
                "target_ref",
                "fact_ref",
                "source_ref",
                "source_family_ref",
                "claim_key",
                "position_key",
                "modality",
                "epistemic_role",
                "authority_ref",
                "qualifiers",
                "design_authority",
                "canonical_write_authority",
            },
            "evidence claim binding",
        )
        if (
            payload["schema"] != cls.SCHEMA
        ):
            raise ValueError("evidence claim binding schema or authority drifted")
        qualifiers = payload["qualifiers"]
        if not isinstance(qualifiers, list):
            raise TypeError("qualifiers must be a list")
        authority_ref = payload["authority_ref"]
        if authority_ref is not None and not isinstance(authority_ref, str):
            raise TypeError("authority_ref must be text or None")
        return cls(
            binding_id=payload["binding_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            obligation_id=payload["obligation_id"],
            target_ref=payload["target_ref"],
            fact_ref=payload["fact_ref"],
            source_ref=payload["source_ref"],
            source_family_ref=payload["source_family_ref"],
            claim_key=payload["claim_key"],
            position_key=payload["position_key"],
            modality=EvidenceModality(payload["modality"]),
            epistemic_role=EpistemicRole(payload["epistemic_role"]),
            authority_ref=authority_ref,
            qualifiers=tuple(qualifiers),
        )


__all__ = [
    "EpistemicRole",
    "EvidenceClaimBinding",
    "EvidenceModality",
]
