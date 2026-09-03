"""Explicit, authority-bound claim applicability contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
    require_same_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
    text,
)
from archflow.evidence.claims import EvidenceClaimBinding
from archflow.project.refs import BranchRef


class ApplicabilityTargetKind(StrEnum):
    DECISION = "decision"
    EDGE = "edge"
    COMPONENT = "component"
    OPERATION = "operation"
    CHECK = "check"


class ApplicabilityDisposition(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    PARKED = "parked"
    SUPERSEDED = "superseded"


class AllowedClaimUse(StrEnum):
    ELEMENT_EXISTENCE = "element_existence"
    TOPOLOGY = "topology"
    RELATIVE_POSITION = "relative_position"
    MORPHOLOGY = "morphology"
    METRIC = "metric"
    MATERIAL = "material"
    ASSET_SELECTION = "asset_selection"
    VALIDATION = "validation"


@dataclass(frozen=True, slots=True)
class ClaimApplicability:
    """A named decision about where one exact claim may be consumed."""

    applicability_id: str
    branch: BranchRef
    scope_digest: str
    claim_binding_id: str
    claim_binding_digest: str
    target_kind: ApplicabilityTargetKind
    target_ref: str
    disposition: ApplicabilityDisposition
    allowed_uses: tuple[AllowedClaimUse, ...]
    authority_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    rationale: str
    invalidates_on: tuple[str, ...]

    SCHEMA = "ClaimApplicability@1"

    def __post_init__(self) -> None:
        identifier(self.applicability_id, "applicability_id")
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        identifier(self.claim_binding_id, "claim_binding_id")
        object.__setattr__(
            self,
            "claim_binding_digest",
            require_sha256(
                self.claim_binding_digest,
                "claim_binding_digest",
            ),
        )
        if not isinstance(self.target_kind, ApplicabilityTargetKind):
            raise TypeError("target_kind must be ApplicabilityTargetKind")
        logical_ref(self.target_ref, "target_ref")
        if not isinstance(self.disposition, ApplicabilityDisposition):
            raise TypeError("disposition must be ApplicabilityDisposition")
        if not isinstance(self.allowed_uses, tuple) or any(
            not isinstance(item, AllowedClaimUse) for item in self.allowed_uses
        ):
            raise TypeError("allowed_uses must be an AllowedClaimUse tuple")
        if self.allowed_uses != tuple(
            sorted(set(self.allowed_uses), key=lambda item: item.value)
        ):
            raise ValueError("allowed_uses must be sorted and unique")
        if (
            self.disposition is ApplicabilityDisposition.APPLICABLE
            and not self.allowed_uses
        ):
            raise ValueError("applicable claim requires at least one allowed use")
        if (
            self.disposition is not ApplicabilityDisposition.APPLICABLE
            and self.allowed_uses
        ):
            raise ValueError("non-applicable claim cannot retain allowed uses")
        deterministic_refs(self.authority_refs, "authority_refs")
        deterministic_refs(self.source_refs, "source_refs")
        text(self.rationale, "rationale")
        deterministic_refs(self.invalidates_on, "invalidates_on")

    @property
    def applicability_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return (
            f"claim-applicability:{self.applicability_id}:"
            f"{self.applicability_digest}"
        )

    @property
    def claim_ref(self) -> str:
        return (
            f"evidence-claim-binding:{self.claim_binding_id}:"
            f"{self.claim_binding_digest}"
        )

    def require_claim(self, claim: EvidenceClaimBinding) -> None:
        if not isinstance(claim, EvidenceClaimBinding):
            raise TypeError("claim must be EvidenceClaimBinding")
        require_same_branch(self.branch, claim.branch, field="claim branch")
        if claim.scope_digest != self.scope_digest:
            raise ValueError("claim crossed its exact research scope")
        if (
            claim.binding_id != self.claim_binding_id
            or claim.binding_digest != self.claim_binding_digest
        ):
            raise ValueError("applicability names another claim binding")
        if claim.source_ref not in self.source_refs:
            raise ValueError("applicability omitted the claim source")
        if (
            claim.authority_ref is not None
            and claim.authority_ref not in self.authority_refs
        ):
            raise ValueError("applicability omitted the claim authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "applicability_id": self.applicability_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "claim_binding_id": self.claim_binding_id,
            "claim_binding_digest": self.claim_binding_digest,
            "target_kind": self.target_kind.value,
            "target_ref": self.target_ref,
            "disposition": self.disposition.value,
            "allowed_uses": [item.value for item in self.allowed_uses],
            "authority_refs": list(self.authority_refs),
            "source_refs": list(self.source_refs),
            "rationale": self.rationale,
            "invalidates_on": list(self.invalidates_on),
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_claim(
        cls,
        claim: EvidenceClaimBinding,
        *,
        applicability_id: str,
        target_kind: ApplicabilityTargetKind,
        target_ref: str,
        disposition: ApplicabilityDisposition,
        allowed_uses: tuple[AllowedClaimUse, ...],
        authority_refs: tuple[str, ...],
        source_refs: tuple[str, ...],
        rationale: str,
        invalidates_on: tuple[str, ...],
    ) -> "ClaimApplicability":
        if not isinstance(claim, EvidenceClaimBinding):
            raise TypeError("claim must be EvidenceClaimBinding")
        result = cls(
            applicability_id=applicability_id,
            branch=claim.branch,
            scope_digest=claim.scope_digest,
            claim_binding_id=claim.binding_id,
            claim_binding_digest=claim.binding_digest,
            target_kind=target_kind,
            target_ref=target_ref,
            disposition=disposition,
            allowed_uses=allowed_uses,
            authority_refs=authority_refs,
            source_refs=source_refs,
            rationale=rationale,
            invalidates_on=invalidates_on,
        )
        result.require_claim(claim)
        return result

    @classmethod
    def from_dict(cls, value: object) -> "ClaimApplicability":
        payload = exact_mapping(
            value,
            {
                "schema",
                "applicability_id",
                "branch",
                "scope_digest",
                "claim_binding_id",
                "claim_binding_digest",
                "target_kind",
                "target_ref",
                "disposition",
                "allowed_uses",
                "authority_refs",
                "source_refs",
                "rationale",
                "invalidates_on",
                "design_authority",
                "canonical_write_authority",
            },
            "claim applicability",
        )
        if (
            payload["schema"] != cls.SCHEMA
        ):
            raise ValueError("claim applicability schema or authority drifted")
        for field in (
            "allowed_uses",
            "authority_refs",
            "source_refs",
            "invalidates_on",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        return cls(
            applicability_id=payload["applicability_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            claim_binding_id=payload["claim_binding_id"],
            claim_binding_digest=payload["claim_binding_digest"],
            target_kind=ApplicabilityTargetKind(payload["target_kind"]),
            target_ref=payload["target_ref"],
            disposition=ApplicabilityDisposition(payload["disposition"]),
            allowed_uses=tuple(
                AllowedClaimUse(item) for item in payload["allowed_uses"]
            ),
            authority_refs=tuple(payload["authority_refs"]),
            source_refs=tuple(payload["source_refs"]),
            rationale=payload["rationale"],
            invalidates_on=tuple(payload["invalidates_on"]),
        )


__all__ = [
    "AllowedClaimUse",
    "ApplicabilityDisposition",
    "ApplicabilityTargetKind",
    "ClaimApplicability",
]
