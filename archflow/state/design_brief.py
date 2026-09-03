"""Building-scoped brief state with no spatial-design authority."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.commitments import Commitment, CommitmentStatus
from archflow.state.operational_state import (
    DesignObligation,
    FactEpistemicStatus,
    FactValue,
    StateFact,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_json, require_sha256


_MAX_ITEMS = 256
_MAX_TEXT = 1_000


class BriefSlot(StrEnum):
    """Generic upstream questions, not answers for any building type."""

    USE = "use"
    SIZE = "size"
    OCCUPANCY = "occupancy"
    SPACE_PROGRAM = "space_program"
    REGULATIONS = "regulations"


class BriefClaimKind(StrEnum):
    USER_FACT = "user_fact"
    RETRIEVED_EVIDENCE = "retrieved_evidence"
    HYPOTHESIS = "hypothesis"
    PREFERENCE = "preference"
    PROHIBITION = "prohibition"


class BriefSlotStatus(StrEnum):
    SUPPORTED = "supported"
    PROPOSED = "proposed"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class BriefConstraintOperator(StrEnum):
    EXACT = "exact"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    TARGET = "target"


@dataclass(frozen=True, slots=True)
class BriefClaim:
    claim_id: str
    slot: BriefSlot
    kind: BriefClaimKind
    fact: StateFact
    authority_id: str
    source_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.claim_id, "claim_id")
        if not isinstance(self.slot, BriefSlot):
            raise TypeError("slot must be BriefSlot")
        if not isinstance(self.kind, BriefClaimKind):
            raise TypeError("kind must be BriefClaimKind")
        if not isinstance(self.fact, StateFact):
            raise TypeError("fact must be StateFact")
        _text(self.authority_id, "authority_id")
        _refs(self.source_refs, "source_refs")
        if self.fact.source_ref not in self.source_refs:
            raise ValueError("fact source_ref must be retained in source_refs")
        _text(self.compiler_id, "compiler_id")
        require_sha256(self.base_state_sha256, "base_state_sha256")
        allowed = {
            BriefClaimKind.USER_FACT: {
                FactEpistemicStatus.DECLARED,
                FactEpistemicStatus.OBSERVED,
            },
            BriefClaimKind.RETRIEVED_EVIDENCE: {
                FactEpistemicStatus.HYPOTHESIS,
            },
            BriefClaimKind.HYPOTHESIS: {
                FactEpistemicStatus.HYPOTHESIS,
            },
            BriefClaimKind.PREFERENCE: {
                FactEpistemicStatus.DECLARED,
            },
            BriefClaimKind.PROHIBITION: {
                FactEpistemicStatus.DECLARED,
            },
        }
        if self.fact.epistemic_status not in allowed[self.kind]:
            raise ValueError(
                "claim kind and fact epistemic status disagree"
            )

    @property
    def ref(self) -> str:
        return f"brief-claim:{self.claim_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "slot": self.slot.value,
            "kind": self.kind.value,
            "fact": self.fact.to_dict(),
            "authority_id": self.authority_id,
            "source_refs": list(self.source_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> BriefClaim:
        payload = _mapping(value, "brief claim")
        _exact(
            payload,
            {
                "claim_id",
                "slot",
                "kind",
                "fact",
                "authority_id",
                "source_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "brief claim",
        )
        return cls(
            claim_id=payload["claim_id"],
            slot=_enum(BriefSlot, payload["slot"], "slot"),
            kind=_enum(BriefClaimKind, payload["kind"], "kind"),
            fact=StateFact.from_dict(payload["fact"]),
            authority_id=payload["authority_id"],
            source_refs=_string_tuple(
                payload["source_refs"],
                "source_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class BriefConstraintProposal:
    proposal_id: str
    slot: BriefSlot
    parameter_key: str
    operator: BriefConstraintOperator
    value: FactValue | object
    unit: str | None
    commitment_id: str
    source_refs: tuple[str, ...]
    compiler_id: str
    base_state_sha256: str

    def __post_init__(self) -> None:
        require_local_id(self.proposal_id, "proposal_id")
        if not isinstance(self.slot, BriefSlot):
            raise TypeError("slot must be BriefSlot")
        require_local_id(self.parameter_key, "parameter_key")
        if not isinstance(self.operator, BriefConstraintOperator):
            raise TypeError("operator must be BriefConstraintOperator")
        object.__setattr__(self, "value", FactValue.from_value(self.value))
        if self.unit is not None:
            _text(self.unit, "unit")
        require_local_id(self.commitment_id, "commitment_id")
        _refs(self.source_refs, "source_refs")
        _text(self.compiler_id, "compiler_id")
        require_sha256(self.base_state_sha256, "base_state_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "slot": self.slot.value,
            "parameter_key": self.parameter_key,
            "operator": self.operator.value,
            "value": self.value.to_python(),
            "unit": self.unit,
            "commitment_id": self.commitment_id,
            "source_refs": list(self.source_refs),
            "compiler_id": self.compiler_id,
            "base_state_sha256": self.base_state_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> BriefConstraintProposal:
        payload = _mapping(value, "brief constraint proposal")
        _exact(
            payload,
            {
                "proposal_id",
                "slot",
                "parameter_key",
                "operator",
                "value",
                "unit",
                "commitment_id",
                "source_refs",
                "compiler_id",
                "base_state_sha256",
            },
            "brief constraint proposal",
        )
        return cls(
            proposal_id=payload["proposal_id"],
            slot=_enum(BriefSlot, payload["slot"], "slot"),
            parameter_key=payload["parameter_key"],
            operator=_enum(
                BriefConstraintOperator,
                payload["operator"],
                "operator",
            ),
            value=payload["value"],
            unit=payload["unit"],
            commitment_id=payload["commitment_id"],
            source_refs=_string_tuple(
                payload["source_refs"],
                "source_refs",
            ),
            compiler_id=payload["compiler_id"],
            base_state_sha256=payload["base_state_sha256"],
        )


@dataclass(frozen=True, slots=True)
class BriefSlotState:
    slot: BriefSlot
    status: BriefSlotStatus
    claim_refs: tuple[str, ...] = ()
    constraint_proposal_ids: tuple[str, ...] = ()
    obligation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.slot, BriefSlot):
            raise TypeError("slot must be BriefSlot")
        if not isinstance(self.status, BriefSlotStatus):
            raise TypeError("status must be BriefSlotStatus")
        _refs(self.claim_refs, "claim_refs", allow_empty=True)
        _ids(
            self.constraint_proposal_ids,
            "constraint_proposal_ids",
        )
        _ids(self.obligation_ids, "obligation_ids")
        if self.status is BriefSlotStatus.UNKNOWN:
            if (
                self.claim_refs
                or self.constraint_proposal_ids
                or not self.obligation_ids
            ):
                raise ValueError(
                    "unknown slot must contain only an open obligation"
                )
        elif self.status is BriefSlotStatus.SUPPORTED:
            if (
                not self.claim_refs
                or self.constraint_proposal_ids
                or self.obligation_ids
            ):
                raise ValueError(
                    "supported slot must cite claims only"
                )
        elif self.status is BriefSlotStatus.PROPOSED:
            if (
                not self.constraint_proposal_ids
                or not self.obligation_ids
            ):
                raise ValueError(
                    "proposed slot requires constraint and obligation"
                )
        elif (
            not (self.claim_refs or self.constraint_proposal_ids)
            or not self.obligation_ids
        ):
            raise ValueError(
                "ambiguous slot requires alternatives and obligation"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot.value,
            "status": self.status.value,
            "claim_refs": list(self.claim_refs),
            "constraint_proposal_ids": list(
                self.constraint_proposal_ids
            ),
            "obligation_ids": list(self.obligation_ids),
        }

    @classmethod
    def from_dict(cls, value: object) -> BriefSlotState:
        payload = _mapping(value, "brief slot state")
        _exact(
            payload,
            {
                "slot",
                "status",
                "claim_refs",
                "constraint_proposal_ids",
                "obligation_ids",
            },
            "brief slot state",
        )
        return cls(
            slot=_enum(BriefSlot, payload["slot"], "slot"),
            status=_enum(
                BriefSlotStatus,
                payload["status"],
                "status",
            ),
            claim_refs=_string_tuple(
                payload["claim_refs"],
                "claim_refs",
            ),
            constraint_proposal_ids=_string_tuple(
                payload["constraint_proposal_ids"],
                "constraint_proposal_ids",
            ),
            obligation_ids=_string_tuple(
                payload["obligation_ids"],
                "obligation_ids",
            ),
        )


@dataclass(frozen=True, slots=True)
class DesignBrief:
    """DesignBrief@1 is evidence-bound and has no geometry authority."""

    project_id: str
    run_id: str
    base: ProjectVersionRef
    compiler_id: str
    compiler_version: str
    raw_request_ref: str
    claims: tuple[BriefClaim, ...]
    constraint_proposals: tuple[BriefConstraintProposal, ...]
    proposed_commitments: tuple[Commitment, ...]
    obligations: tuple[DesignObligation, ...]
    slots: tuple[BriefSlotState, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "DesignBrief@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("brief and base belong to different projects")
        self.base.require_digest()
        _text(self.compiler_id, "compiler_id")
        _text(self.compiler_version, "compiler_version")
        require_logical_ref(self.raw_request_ref, "raw_request_ref")
        _typed_tuple(self.claims, BriefClaim, "claims")
        _typed_tuple(
            self.constraint_proposals,
            BriefConstraintProposal,
            "constraint_proposals",
        )
        _typed_tuple(
            self.proposed_commitments,
            Commitment,
            "proposed_commitments",
        )
        _typed_tuple(
            self.obligations,
            DesignObligation,
            "obligations",
        )
        _typed_tuple(self.slots, BriefSlotState, "slots")
        _refs(self.evidence_refs, "evidence_refs")
        if self.raw_request_ref not in self.evidence_refs:
            raise ValueError("raw request must remain an evidence ref")
        _unique(
            tuple(item.claim_id for item in self.claims),
            "claim ids",
        )
        _unique(
            tuple(item.proposal_id for item in self.constraint_proposals),
            "constraint proposal ids",
        )
        _unique(
            tuple(
                item.commitment_id
                for item in self.proposed_commitments
            ),
            "commitment ids",
        )
        _unique(
            tuple(item.obligation_id for item in self.obligations),
            "obligation ids",
        )
        _unique(
            tuple(item.slot.value for item in self.slots),
            "brief slots",
        )
        if {item.slot for item in self.slots} != set(BriefSlot):
            raise ValueError("brief must compile every generic slot")
        if any(
            item.status is not CommitmentStatus.PROPOSED
            for item in self.proposed_commitments
        ):
            raise ValueError(
                "brief cannot authorize proposed commitments"
            )
        proposal_commitments = {
            item.commitment_id
            for item in self.constraint_proposals
        }
        if proposal_commitments != {
            item.commitment_id for item in self.proposed_commitments
        }:
            raise ValueError(
                "constraint proposals and commitments disagree"
            )
        claim_refs = {item.ref for item in self.claims}
        claims_by_ref = {item.ref: item for item in self.claims}
        proposals_by_id = {
            item.proposal_id: item for item in self.constraint_proposals
        }
        proposal_ids = set(proposals_by_id)
        obligation_ids = {
            item.obligation_id for item in self.obligations
        }
        evidence_refs = set(self.evidence_refs)
        base_digest = self.base.require_digest()
        for claim in self.claims:
            if (
                claim.compiler_id != self.compiler_id
                or claim.base_state_sha256 != base_digest
            ):
                raise ValueError(
                    "claim provenance disagrees with brief compilation"
                )
            if not set(claim.source_refs) <= evidence_refs:
                raise ValueError("claim source is absent from brief evidence")
        for proposal in self.constraint_proposals:
            if (
                proposal.compiler_id != self.compiler_id
                or proposal.base_state_sha256 != base_digest
            ):
                raise ValueError(
                    "constraint provenance disagrees with brief compilation"
                )
            if not set(proposal.source_refs) <= evidence_refs:
                raise ValueError(
                    "constraint source is absent from brief evidence"
                )
        for item in self.slots:
            if not set(item.claim_refs) <= claim_refs:
                raise ValueError("slot cites an unknown claim")
            if not set(item.constraint_proposal_ids) <= proposal_ids:
                raise ValueError(
                    "slot cites an unknown constraint proposal"
                )
            if not set(item.obligation_ids) <= obligation_ids:
                raise ValueError("slot cites an unknown obligation")
            if any(
                claims_by_ref[ref].slot is not item.slot
                for ref in item.claim_refs
            ):
                raise ValueError("slot cites a claim from another slot")
            if any(
                proposals_by_id[proposal_id].slot is not item.slot
                for proposal_id in item.constraint_proposal_ids
            ):
                raise ValueError(
                    "slot cites a constraint from another slot"
                )

    @property
    def brief_digest(self) -> str:
        return hashlib.sha256(
            canonical_json(self._identity()).encode("utf-8")
        ).hexdigest()

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.base.project_id,
                "version": self.base.version,
                "state_sha256": self.base.require_digest(),
            },
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "raw_request_ref": self.raw_request_ref,
            "claims": [
                item.to_dict()
                for item in sorted(
                    self.claims,
                    key=lambda item: item.claim_id,
                )
            ],
            "constraint_proposals": [
                item.to_dict()
                for item in sorted(
                    self.constraint_proposals,
                    key=lambda item: item.proposal_id,
                )
            ],
            "proposed_commitments": [
                item.to_dict()
                for item in sorted(
                    self.proposed_commitments,
                    key=lambda item: item.commitment_id,
                )
            ],
            "obligations": [
                item.to_dict()
                for item in sorted(
                    self.obligations,
                    key=lambda item: item.obligation_id,
                )
            ],
            "slots": [
                item.to_dict()
                for item in sorted(
                    self.slots,
                    key=lambda item: item.slot.value,
                )
            ],
            "evidence_refs": sorted(self.evidence_refs),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "brief_digest": self.brief_digest,
            "generation_authority": False,
            "geometry_selected": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignBrief:
        payload = _mapping(value, "design brief")
        expected = {
            "schema",
            "project_id",
            "run_id",
            "base",
            "compiler_id",
            "compiler_version",
            "raw_request_ref",
            "claims",
            "constraint_proposals",
            "proposed_commitments",
            "obligations",
            "slots",
            "evidence_refs",
            "brief_digest",
            "generation_authority",
            "geometry_selected",
        }
        _exact(payload, expected, "design brief")
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("unsupported design brief schema")
        if payload["generation_authority"] is not False:
            raise ValueError("design brief cannot gain generation authority")
        if payload["geometry_selected"] is not False:
            raise ValueError("design brief cannot select geometry")
        base_payload = _mapping(payload["base"], "base")
        _exact(
            base_payload,
            {"project_id", "version", "state_sha256"},
            "base",
        )
        brief = cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=base_payload["project_id"],
                version=base_payload["version"],
                state_sha256=base_payload["state_sha256"],
            ),
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            raw_request_ref=payload["raw_request_ref"],
            claims=tuple(
                BriefClaim.from_dict(item)
                for item in _list(payload["claims"], "claims")
            ),
            constraint_proposals=tuple(
                BriefConstraintProposal.from_dict(item)
                for item in _list(
                    payload["constraint_proposals"],
                    "constraint_proposals",
                )
            ),
            proposed_commitments=tuple(
                Commitment.from_dict(item)
                for item in _list(
                    payload["proposed_commitments"],
                    "proposed_commitments",
                )
            ),
            obligations=tuple(
                DesignObligation.from_dict(item)
                for item in _list(
                    payload["obligations"],
                    "obligations",
                )
            ),
            slots=tuple(
                BriefSlotState.from_dict(item)
                for item in _list(payload["slots"], "slots")
            ),
            evidence_refs=_string_tuple(
                payload["evidence_refs"],
                "evidence_refs",
            ),
        )
        if payload["brief_digest"] != brief.brief_digest:
            raise ValueError("design brief digest mismatch")
        return brief


def _text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT
    ):
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


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


def _refs(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if (not allow_empty and not value) or len(value) > _MAX_ITEMS:
        requirement = "bounded tuple" if allow_empty else "bounded non-empty tuple"
        raise ValueError(f"{field} must be a {requirement}")
    for item in value:
        require_logical_ref(item, f"{field} item")
    _unique(value, field)


def _ids(value: object, field: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"{field} exceeds bounded item count")
    for item in value:
        require_local_id(item, f"{field} item")
    _unique(value, field)


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
