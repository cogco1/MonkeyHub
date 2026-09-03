"""Typed contracts for an authority-bound genesis semantic denominator.

The contracts make the semantic-system denominator explicit before later
stages can refine geometry.  They carry no design, stage-acceptance,
persistence, or canonical-write authority.  Its receipt reuses the existing
``CheckStatus`` vocabulary and is only source evidence for an exact
``CheckReceiptEnvelope`` bridge; it is not permission to accept or advance a
design stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_identifiers,
    deterministic_refs,
    identifier,
    logical_ref,
)
from archflow.evidence.applicability import (
    AllowedClaimUse,
    ApplicabilityDisposition,
    ClaimApplicability,
)
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.research.branch import (
    BranchEvidenceSnapshot,
    BranchPrecedentAdoption,
    BranchResearchScope,
    require_record_payload,
)
from archflow.state.design_maturity import DesignPhase
from archflow.validation.contracts import CheckStatus


class GenesisSemanticCompletenessError(ValueError):
    """A semantic denominator, declaration, or receipt is malformed."""


class SemanticSystemDisposition(StrEnum):
    """Caller declaration for one required semantic-system question."""

    PRESENT = "PRESENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class SemanticSystemFindingStatus(StrEnum):
    """Compiler result for one denominator member."""

    PRESENT = "PRESENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OPEN = "OPEN"
    REJECTED = "REJECTED"


class SemanticDenominatorSourceKind(StrEnum):
    """Explicit authority channels permitted to populate the denominator."""

    BRIEF = "BRIEF"
    TYPOLOGY = "TYPOLOGY"
    RAG = "RAG"
    HUMAN_AUTHORITY = "HUMAN_AUTHORITY"


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


def _exact_mapping(
    value: object,
    expected: set[str],
    field: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise GenesisSemanticCompletenessError(f"{field} schema drifted")
    return value


def _require_false_authority(payload: Mapping[str, object]) -> None:
    if any(
        payload.get(field) is not expected
        for field, expected in _AUTHORITY_FIELDS.items()
    ):
        raise GenesisSemanticCompletenessError(
            "semantic completeness authority flags changed"
        )


def _record_to_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = _exact_mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    try:
        return ProjectRecordRef(
            project_id=payload["project_id"],
            relative_path=payload["relative_path"],
            sha256=payload["sha256"],
            media_type=payload["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise GenesisSemanticCompletenessError(f"{field} is invalid") from exc


def _record_refs(
    values: object,
    field: str,
    *,
    allow_empty: bool,
) -> tuple[ProjectRecordRef, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, ProjectRecordRef) for item in values
    ):
        raise TypeError(f"{field} must contain ProjectRecordRef values")
    if not values and not allow_empty:
        raise GenesisSemanticCompletenessError(f"{field} must not be empty")
    identities = tuple(
        (item.uri, item.sha256, item.media_type) for item in values
    )
    if len(identities) != len(set(identities)):
        raise GenesisSemanticCompletenessError(f"{field} contains duplicates")
    locations = tuple(
        (item.project_id, item.relative_path) for item in values
    )
    if len(locations) != len(set(locations)):
        raise GenesisSemanticCompletenessError(
            f"{field} contains conflicting digests for one record path"
        )
    return tuple(
        sorted(
            values,
            key=lambda item: (item.uri, item.sha256, item.media_type),
        )
    )


def _record_refs_from_list(value: object, field: str) -> tuple[ProjectRecordRef, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return tuple(_record_from_dict(item, field) for item in value)


def _record_locations(
    values: tuple[ProjectRecordRef, ...],
) -> set[tuple[str, str]]:
    return {(item.project_id, item.relative_path) for item in values}


@dataclass(frozen=True, slots=True)
class SemanticSystemBasis:
    """One explicit source contribution to a semantic-system denominator.

    RAG evidence must carry the existing exact ``BranchResearchScope``,
    ``BranchEvidenceSnapshot``, ``BranchPrecedentAdoption``, and
    ``ClaimApplicability`` contracts.  The snapshot is replayed against its
    retained P036 record SHA; no caller-authored adoption/applicability
    booleans are accepted.
    """

    basis_id: str
    system_id: str
    system_ref: str
    semantic_kinds: tuple[str, ...]
    source_kind: SemanticDenominatorSourceKind
    evidence_ref: ProjectRecordRef
    authority_ref: ProjectRecordRef
    evidence_snapshot: BranchEvidenceSnapshot | None = None
    research_scope: BranchResearchScope | None = None
    adoption: BranchPrecedentAdoption | None = None
    applicability: ClaimApplicability | None = None

    SCHEMA = "SemanticSystemBasis@1"

    def __post_init__(self) -> None:
        identifier(self.basis_id, "semantic system basis_id")
        identifier(self.system_id, "semantic system_id")
        object.__setattr__(
            self,
            "system_ref",
            logical_ref(self.system_ref, "semantic system_ref"),
        )
        if self.system_ref != f"semantic-system:{self.system_id}":
            raise GenesisSemanticCompletenessError(
                "semantic system basis changed system identity"
            )
        object.__setattr__(
            self,
            "semantic_kinds",
            deterministic_identifiers(
                self.semantic_kinds,
                "semantic system basis semantic_kinds",
            ),
        )
        if not isinstance(self.source_kind, SemanticDenominatorSourceKind):
            raise TypeError(
                "source_kind must be SemanticDenominatorSourceKind"
            )
        if not isinstance(self.evidence_ref, ProjectRecordRef):
            raise TypeError("evidence_ref must be a ProjectRecordRef")
        if not isinstance(self.authority_ref, ProjectRecordRef):
            raise TypeError("authority_ref must be a ProjectRecordRef")
        if self.source_kind is SemanticDenominatorSourceKind.RAG:
            if not isinstance(self.research_scope, BranchResearchScope):
                raise TypeError("RAG research_scope must be BranchResearchScope")
            if not isinstance(self.adoption, BranchPrecedentAdoption):
                raise TypeError("RAG adoption must be BranchPrecedentAdoption")
            if not isinstance(self.applicability, ClaimApplicability):
                raise TypeError("RAG applicability must be ClaimApplicability")
            if not isinstance(self.evidence_snapshot, BranchEvidenceSnapshot):
                raise TypeError(
                    "RAG evidence_snapshot must be BranchEvidenceSnapshot"
                )
            if (
                self.adoption.scope_digest != self.research_scope.scope_digest
                or self.adoption.branch_id != self.research_scope.branch_id
                or self.adoption.branch_revision_digest
                != self.research_scope.branch_revision_digest
            ):
                raise GenesisSemanticCompletenessError(
                    "RAG adoption crossed its exact research scope"
                )
            snapshot = self.evidence_snapshot
            if (
                snapshot.scope_digest != self.research_scope.scope_digest
                or snapshot.branch_id != self.research_scope.branch_id
                or snapshot.branch_revision_digest
                != self.research_scope.branch_revision_digest
                or snapshot.query_id != self.adoption.query_id
                or snapshot.query_digest != self.adoption.query_digest
            ):
                raise GenesisSemanticCompletenessError(
                    "RAG snapshot crossed its exact query or branch revision"
                )
            try:
                require_record_payload(
                    self.evidence_ref,
                    snapshot.to_dict(),
                    run=self.research_scope.run,
                    area_prefix=(
                        f"branches/{self.research_scope.branch_id}/records"
                    ),
                    field="RAG evidence_ref",
                )
            except (TypeError, ValueError) as exc:
                raise GenesisSemanticCompletenessError(
                    "RAG evidence_ref does not bind the exact retained snapshot"
                ) from exc
            if (
                self.applicability.scope_digest
                != self.research_scope.scope_digest
                or self.applicability.target_ref != self.system_ref
                or self.applicability.disposition
                is not ApplicabilityDisposition.APPLICABLE
                or AllowedClaimUse.ELEMENT_EXISTENCE
                not in self.applicability.allowed_uses
            ):
                raise GenesisSemanticCompletenessError(
                    "RAG applicability does not authorize system existence"
                )
            matching_facts = tuple(
                fact
                for fact in self.adoption.adoption.facts
                if fact.snapshot_ref == self.evidence_ref.uri
                and self.system_ref in fact.decision_refs
            )
            if not matching_facts:
                raise GenesisSemanticCompletenessError(
                    "RAG adoption does not adopt the exact evidence subject"
                )
            for fact in matching_facts:
                if fact.snapshot_text_sha256 != snapshot.text_sha256:
                    raise GenesisSemanticCompletenessError(
                        "RAG adopted snapshot text digest changed"
                    )
                try:
                    fact.require_quote_in(snapshot.text)
                except (TypeError, ValueError) as exc:
                    raise GenesisSemanticCompletenessError(
                        "RAG adopted quote is absent from the exact snapshot"
                    ) from exc
            if (
                self.rag_adoption_ref not in self.applicability.source_refs
                or self.evidence_ref.uri
                not in self.applicability.source_refs
                or self.authority_ref.uri
                != self.adoption.adoption.authority_id
                or self.authority_ref.uri
                not in self.applicability.authority_refs
            ):
                raise GenesisSemanticCompletenessError(
                    "RAG applicability omitted adoption, source, or authority"
                )
        elif any(
            value is not None
            for value in (
                self.research_scope,
                self.adoption,
                self.applicability,
                self.evidence_snapshot,
            )
        ):
            raise GenesisSemanticCompletenessError(
                "non-RAG basis cannot carry research adoption records"
            )

    @property
    def is_admissible(self) -> bool:
        return self.source_kind is not SemanticDenominatorSourceKind.RAG or (
            self.research_scope is not None
            and self.adoption is not None
            and self.applicability is not None
            and self.evidence_snapshot is not None
        )

    @property
    def rag_adoption_ref(self) -> str:
        if self.adoption is None:
            raise GenesisSemanticCompletenessError(
                "non-RAG basis has no adoption ref"
            )
        return (
            f"precedent-adoption:{self.adoption.adoption_id}:"
            f"{self.adoption.adoption.adoption_digest}"
        )

    @property
    def basis_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "basis_id": self.basis_id,
            "system_id": self.system_id,
            "system_ref": self.system_ref,
            "semantic_kinds": list(self.semantic_kinds),
            "source_kind": self.source_kind.value,
            "evidence_ref": _record_to_dict(self.evidence_ref),
            "authority_ref": _record_to_dict(self.authority_ref),
            "evidence_snapshot": (
                self.evidence_snapshot.to_dict()
                if self.evidence_snapshot is not None
                else None
            ),
            "research_scope": (
                self.research_scope.to_dict()
                if self.research_scope is not None
                else None
            ),
            "adoption": (
                self.adoption.to_dict() if self.adoption is not None else None
            ),
            "applicability": (
                self.applicability.to_dict()
                if self.applicability is not None
                else None
            ),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "basis_digest": self.basis_digest}

    @classmethod
    def from_dict(cls, value: object) -> "SemanticSystemBasis":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "basis_id",
                "system_id",
                "system_ref",
                "semantic_kinds",
                "source_kind",
                "evidence_ref",
                "authority_ref",
                "evidence_snapshot",
                "research_scope",
                "adoption",
                "applicability",
                "basis_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic system basis",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GenesisSemanticCompletenessError(
                "unsupported semantic system basis schema"
            )
        _require_false_authority(payload)
        if not isinstance(payload["semantic_kinds"], list):
            raise TypeError("semantic_kinds must be a list")
        raw_scope = payload["research_scope"]
        raw_adoption = payload["adoption"]
        raw_applicability = payload["applicability"]
        raw_snapshot = payload["evidence_snapshot"]
        result = cls(
            basis_id=payload["basis_id"],
            system_id=payload["system_id"],
            system_ref=payload["system_ref"],
            semantic_kinds=tuple(payload["semantic_kinds"]),
            source_kind=SemanticDenominatorSourceKind(payload["source_kind"]),
            evidence_ref=_record_from_dict(
                payload["evidence_ref"],
                "evidence_ref",
            ),
            authority_ref=_record_from_dict(
                payload["authority_ref"],
                "authority_ref",
            ),
            evidence_snapshot=(
                BranchEvidenceSnapshot.from_dict(raw_snapshot)
                if raw_snapshot is not None
                else None
            ),
            research_scope=(
                BranchResearchScope.from_dict(raw_scope)
                if raw_scope is not None
                else None
            ),
            adoption=(
                BranchPrecedentAdoption.from_dict(raw_adoption)
                if raw_adoption is not None
                else None
            ),
            applicability=(
                ClaimApplicability.from_dict(raw_applicability)
                if raw_applicability is not None
                else None
            ),
        )
        if result.to_dict() != dict(payload):
            raise GenesisSemanticCompletenessError(
                "semantic system basis digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SemanticSystemRequirement:
    """One explicitly sourced member of the required system denominator.

    ``semantic_kinds`` is an explicit typed mapping supplied by the
    denominator authority.  Runtime compilation compares it only with the
    inventory's typed ``semantic_kind`` field and never guesses from component
    names or identifiers.
    """

    system_id: str
    system_ref: str
    semantic_kinds: tuple[str, ...]
    bases: tuple[SemanticSystemBasis, ...]
    not_applicable_evidence_refs: tuple[ProjectRecordRef, ...] = ()
    not_applicable_authority_refs: tuple[ProjectRecordRef, ...] = ()

    SCHEMA = "SemanticSystemRequirement@1"

    def __post_init__(self) -> None:
        identifier(self.system_id, "semantic system_id")
        object.__setattr__(
            self,
            "system_ref",
            logical_ref(self.system_ref, "semantic system_ref"),
        )
        if self.system_ref != f"semantic-system:{self.system_id}":
            raise GenesisSemanticCompletenessError(
                "semantic system_ref changed system identity"
            )
        object.__setattr__(
            self,
            "semantic_kinds",
            deterministic_identifiers(
                self.semantic_kinds,
                "semantic_kinds",
            ),
        )
        if not isinstance(self.bases, tuple) or not self.bases or any(
            not isinstance(item, SemanticSystemBasis) for item in self.bases
        ):
            raise TypeError("bases must contain SemanticSystemBasis values")
        bases = tuple(sorted(self.bases, key=lambda item: item.basis_id))
        basis_ids = tuple(item.basis_id for item in bases)
        if len(basis_ids) != len(set(basis_ids)):
            raise GenesisSemanticCompletenessError(
                "semantic system requirement duplicates a basis"
            )
        if any(
            item.system_id != self.system_id
            or item.system_ref != self.system_ref
            or item.semantic_kinds != self.semantic_kinds
            for item in bases
        ):
            raise GenesisSemanticCompletenessError(
                "semantic system bases disagree with their merged requirement"
            )
        if any(not item.is_admissible for item in bases):
            raise GenesisSemanticCompletenessError(
                "raw or non-applicable RAG cannot enter the denominator"
            )
        object.__setattr__(self, "bases", bases)
        object.__setattr__(
            self,
            "not_applicable_evidence_refs",
            _record_refs(
                self.not_applicable_evidence_refs,
                "not_applicable_evidence_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "not_applicable_authority_refs",
            _record_refs(
                self.not_applicable_authority_refs,
                "not_applicable_authority_refs",
                allow_empty=True,
            ),
        )
        if bool(self.not_applicable_evidence_refs) != bool(
            self.not_applicable_authority_refs
        ):
            raise GenesisSemanticCompletenessError(
                "not-applicable policy needs both evidence and authority refs"
            )
        if _record_locations(
            self.not_applicable_evidence_refs
        ) & _record_locations(self.not_applicable_authority_refs):
            raise GenesisSemanticCompletenessError(
                "not-applicable evidence and authority refs must be independent"
            )

    @property
    def requirement_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "system_id": self.system_id,
            "system_ref": self.system_ref,
            "semantic_kinds": list(self.semantic_kinds),
            "bases": [item.to_dict() for item in self.bases],
            "not_applicable_evidence_refs": [
                _record_to_dict(item)
                for item in self.not_applicable_evidence_refs
            ],
            "not_applicable_authority_refs": [
                _record_to_dict(item)
                for item in self.not_applicable_authority_refs
            ],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "requirement_digest": self.requirement_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticSystemRequirement":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "system_id",
                "system_ref",
                "semantic_kinds",
                "bases",
                "not_applicable_evidence_refs",
                "not_applicable_authority_refs",
                "requirement_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic system requirement",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GenesisSemanticCompletenessError(
                "unsupported semantic system requirement schema"
            )
        _require_false_authority(payload)
        for field in ("semantic_kinds", "bases"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            system_id=payload["system_id"],
            system_ref=payload["system_ref"],
            semantic_kinds=tuple(payload["semantic_kinds"]),
            bases=tuple(
                SemanticSystemBasis.from_dict(item)
                for item in payload["bases"]
            ),
            not_applicable_evidence_refs=_record_refs_from_list(
                payload["not_applicable_evidence_refs"],
                "not_applicable_evidence_refs",
            ),
            not_applicable_authority_refs=_record_refs_from_list(
                payload["not_applicable_authority_refs"],
                "not_applicable_authority_refs",
            ),
        )
        if result.to_dict() != dict(payload):
            raise GenesisSemanticCompletenessError(
                "semantic system requirement digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class GenesisSemanticDenominator:
    """Exact Stage-0 system denominator bound to state/profile/inventory."""

    denominator_id: str
    branch: BranchRef
    state_digest: str
    stage_id: str
    stage_subject_ref: str
    stage_subject_digest: str
    profile_id: str
    profile_digest: str
    profile_binding_id: str
    profile_binding_digest: str
    profile_requirement_id: str
    systems: tuple[SemanticSystemRequirement, ...]

    SCHEMA = "GenesisSemanticDenominator@1"

    def __post_init__(self) -> None:
        identifier(self.denominator_id, "semantic denominator_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        self.branch.run.base.require_digest()
        if self.branch.epoch != 0:
            raise GenesisSemanticCompletenessError(
                "semantic denominator origin must be branch epoch zero"
            )
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "state_digest"),
        )
        identifier(self.stage_id, "semantic denominator stage_id")
        if self.stage_id != DesignPhase.RESEARCH_BRIEF.value:
            raise GenesisSemanticCompletenessError(
                "semantic denominator is restricted to research_brief Stage 0"
            )
        object.__setattr__(
            self,
            "stage_subject_ref",
            logical_ref(self.stage_subject_ref, "stage_subject_ref"),
        )
        object.__setattr__(
            self,
            "stage_subject_digest",
            require_sha256(self.stage_subject_digest, "stage_subject_digest"),
        )
        identifier(self.profile_id, "semantic denominator profile_id")
        object.__setattr__(
            self,
            "profile_digest",
            require_sha256(self.profile_digest, "profile_digest"),
        )
        identifier(
            self.profile_binding_id,
            "semantic denominator profile_binding_id",
        )
        object.__setattr__(
            self,
            "profile_binding_digest",
            require_sha256(
                self.profile_binding_digest,
                "profile_binding_digest",
            ),
        )
        identifier(
            self.profile_requirement_id,
            "semantic denominator profile_requirement_id",
        )
        if not isinstance(self.systems, tuple) or not self.systems or any(
            not isinstance(item, SemanticSystemRequirement)
            for item in self.systems
        ):
            raise TypeError(
                "systems must contain SemanticSystemRequirement values"
            )
        systems = tuple(sorted(self.systems, key=lambda item: item.system_ref))
        system_refs = tuple(item.system_ref for item in systems)
        if len(system_refs) != len(set(system_refs)):
            raise GenesisSemanticCompletenessError(
                "semantic denominator contains duplicate systems"
            )
        project_id = self.branch.run.project_id
        source_refs = (
            ref
            for system in systems
            for refs in (
                tuple(item.evidence_ref for item in system.bases),
                tuple(item.authority_ref for item in system.bases),
                system.not_applicable_evidence_refs,
                system.not_applicable_authority_refs,
            )
            for ref in refs
        )
        if any(ref.project_id != project_id for ref in source_refs):
            raise GenesisSemanticCompletenessError(
                "semantic denominator evidence crossed its project"
            )
        object.__setattr__(self, "systems", systems)

    @property
    def system_refs(self) -> tuple[str, ...]:
        return tuple(item.system_ref for item in self.systems)

    @property
    def denominator_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "denominator_id": self.denominator_id,
            "branch": branch_ref_to_dict(self.branch),
            "state_digest": self.state_digest,
            "stage_id": self.stage_id,
            "stage_subject_ref": self.stage_subject_ref,
            "stage_subject_digest": self.stage_subject_digest,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "profile_binding_id": self.profile_binding_id,
            "profile_binding_digest": self.profile_binding_digest,
            "profile_requirement_id": self.profile_requirement_id,
            "systems": [item.to_dict() for item in self.systems],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "denominator_digest": self.denominator_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "GenesisSemanticDenominator":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "denominator_id",
                "branch",
                "state_digest",
                "stage_id",
                "stage_subject_ref",
                "stage_subject_digest",
                "profile_id",
                "profile_digest",
                "profile_binding_id",
                "profile_binding_digest",
                "profile_requirement_id",
                "systems",
                "denominator_digest",
                *_AUTHORITY_FIELDS,
            },
            "genesis semantic denominator",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GenesisSemanticCompletenessError(
                "unsupported genesis semantic denominator schema"
            )
        _require_false_authority(payload)
        if not isinstance(payload["systems"], list):
            raise TypeError("systems must be a list")
        result = cls(
            denominator_id=payload["denominator_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            state_digest=payload["state_digest"],
            stage_id=payload["stage_id"],
            stage_subject_ref=payload["stage_subject_ref"],
            stage_subject_digest=payload["stage_subject_digest"],
            profile_id=payload["profile_id"],
            profile_digest=payload["profile_digest"],
            profile_binding_id=payload["profile_binding_id"],
            profile_binding_digest=payload["profile_binding_digest"],
            profile_requirement_id=payload["profile_requirement_id"],
            systems=tuple(
                SemanticSystemRequirement.from_dict(item)
                for item in payload["systems"]
            ),
        )
        if result.to_dict() != dict(payload):
            raise GenesisSemanticCompletenessError(
                "genesis semantic denominator digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SemanticSystemDeclaration:
    """One proposal-side declaration; validity is decided by the compiler."""

    system_ref: str
    disposition: SemanticSystemDisposition
    component_refs: tuple[str, ...] = ()
    evidence_refs: tuple[ProjectRecordRef, ...] = ()
    authority_refs: tuple[ProjectRecordRef, ...] = ()

    SCHEMA = "SemanticSystemDeclaration@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "system_ref",
            logical_ref(self.system_ref, "declaration system_ref"),
        )
        if not isinstance(self.disposition, SemanticSystemDisposition):
            raise TypeError("disposition must be SemanticSystemDisposition")
        object.__setattr__(
            self,
            "component_refs",
            deterministic_refs(
                self.component_refs,
                "declaration component_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _record_refs(
                self.evidence_refs,
                "declaration evidence_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            _record_refs(
                self.authority_refs,
                "declaration authority_refs",
                allow_empty=True,
            ),
        )
        if self.disposition is SemanticSystemDisposition.PRESENT and (
            self.evidence_refs or self.authority_refs
        ):
            raise GenesisSemanticCompletenessError(
                "present declaration is proven only by exact inventory components"
            )
        if self.disposition is SemanticSystemDisposition.NOT_APPLICABLE and (
            self.component_refs
        ):
            raise GenesisSemanticCompletenessError(
                "not-applicable declaration cannot name components"
            )
        if _record_locations(self.evidence_refs) & _record_locations(
            self.authority_refs
        ):
            raise GenesisSemanticCompletenessError(
                "declaration evidence and authority refs must be independent"
            )
        if self.disposition is SemanticSystemDisposition.UNKNOWN and (
            self.component_refs or self.evidence_refs or self.authority_refs
        ):
            raise GenesisSemanticCompletenessError(
                "unknown declaration cannot claim evidence or components"
            )

    @property
    def declaration_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "system_ref": self.system_ref,
            "disposition": self.disposition.value,
            "component_refs": list(self.component_refs),
            "evidence_refs": [
                _record_to_dict(item) for item in self.evidence_refs
            ],
            "authority_refs": [
                _record_to_dict(item) for item in self.authority_refs
            ],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "declaration_digest": self.declaration_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticSystemDeclaration":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "system_ref",
                "disposition",
                "component_refs",
                "evidence_refs",
                "authority_refs",
                "declaration_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic system declaration",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GenesisSemanticCompletenessError(
                "unsupported semantic system declaration schema"
            )
        _require_false_authority(payload)
        if not isinstance(payload["component_refs"], list):
            raise TypeError("component_refs must be a list")
        result = cls(
            system_ref=payload["system_ref"],
            disposition=SemanticSystemDisposition(payload["disposition"]),
            component_refs=tuple(payload["component_refs"]),
            evidence_refs=_record_refs_from_list(
                payload["evidence_refs"],
                "evidence_refs",
            ),
            authority_refs=_record_refs_from_list(
                payload["authority_refs"],
                "authority_refs",
            ),
        )
        if result.to_dict() != dict(payload):
            raise GenesisSemanticCompletenessError(
                "semantic system declaration digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SemanticSystemFinding:
    """Typed evidence for one denominator member's assessed disposition."""

    system_ref: str
    declared_disposition: SemanticSystemDisposition | None
    status: SemanticSystemFindingStatus
    component_refs: tuple[str, ...]
    evidence_refs: tuple[ProjectRecordRef, ...]
    authority_refs: tuple[ProjectRecordRef, ...]
    reason_codes: tuple[str, ...]

    SCHEMA = "SemanticSystemFinding@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "system_ref",
            logical_ref(self.system_ref, "finding system_ref"),
        )
        if self.declared_disposition is not None and not isinstance(
            self.declared_disposition,
            SemanticSystemDisposition,
        ):
            raise TypeError(
                "declared_disposition must be SemanticSystemDisposition or None"
            )
        if not isinstance(self.status, SemanticSystemFindingStatus):
            raise TypeError("status must be SemanticSystemFindingStatus")
        object.__setattr__(
            self,
            "component_refs",
            deterministic_refs(
                self.component_refs,
                "finding component_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _record_refs(
                self.evidence_refs,
                "finding evidence_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "authority_refs",
            _record_refs(
                self.authority_refs,
                "finding authority_refs",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "reason_codes",
            deterministic_identifiers(
                self.reason_codes,
                "finding reason_codes",
            ),
        )
        if self.status is SemanticSystemFindingStatus.PRESENT:
            if (
                self.declared_disposition
                is not SemanticSystemDisposition.PRESENT
                or not self.component_refs
                or self.evidence_refs
                or self.authority_refs
            ):
                raise GenesisSemanticCompletenessError(
                    "present finding lacks exact inventory component evidence"
                )
        elif self.status is SemanticSystemFindingStatus.NOT_APPLICABLE:
            if (
                self.declared_disposition
                is not SemanticSystemDisposition.NOT_APPLICABLE
                or self.component_refs
                or not self.evidence_refs
                or not self.authority_refs
            ):
                raise GenesisSemanticCompletenessError(
                    "not-applicable finding lacks evidence and authority"
                )
        elif self.status is SemanticSystemFindingStatus.OPEN:
            if self.declared_disposition not in (
                None,
                SemanticSystemDisposition.UNKNOWN,
            ):
                raise GenesisSemanticCompletenessError(
                    "open finding has a conclusive declaration"
                )
        elif self.declared_disposition is None:
            raise GenesisSemanticCompletenessError(
                "rejected finding must identify the rejected declaration"
            )

    @property
    def finding_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "system_ref": self.system_ref,
            "declared_disposition": (
                self.declared_disposition.value
                if self.declared_disposition is not None
                else None
            ),
            "status": self.status.value,
            "component_refs": list(self.component_refs),
            "evidence_refs": [
                _record_to_dict(item) for item in self.evidence_refs
            ],
            "authority_refs": [
                _record_to_dict(item) for item in self.authority_refs
            ],
            "reason_codes": list(self.reason_codes),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "finding_digest": self.finding_digest}

    @classmethod
    def from_dict(cls, value: object) -> "SemanticSystemFinding":
        payload = _exact_mapping(
            value,
            {
                "schema",
                "system_ref",
                "declared_disposition",
                "status",
                "component_refs",
                "evidence_refs",
                "authority_refs",
                "reason_codes",
                "finding_digest",
                *_AUTHORITY_FIELDS,
            },
            "semantic system finding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GenesisSemanticCompletenessError(
                "unsupported semantic system finding schema"
            )
        _require_false_authority(payload)
        for field in ("component_refs", "reason_codes"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        raw_disposition = payload["declared_disposition"]
        result = cls(
            system_ref=payload["system_ref"],
            declared_disposition=(
                SemanticSystemDisposition(raw_disposition)
                if raw_disposition is not None
                else None
            ),
            status=SemanticSystemFindingStatus(payload["status"]),
            component_refs=tuple(payload["component_refs"]),
            evidence_refs=_record_refs_from_list(
                payload["evidence_refs"],
                "evidence_refs",
            ),
            authority_refs=_record_refs_from_list(
                payload["authority_refs"],
                "authority_refs",
            ),
            reason_codes=tuple(payload["reason_codes"]),
        )
        if result.to_dict() != dict(payload):
            raise GenesisSemanticCompletenessError(
                "semantic system finding digest changed"
            )
        return result


__all__ = [
    "GenesisSemanticCompletenessError",
    "GenesisSemanticDenominator",
    "SemanticDenominatorSourceKind",
    "SemanticSystemBasis",
    "SemanticSystemDeclaration",
    "SemanticSystemDisposition",
    "SemanticSystemFinding",
    "SemanticSystemFindingStatus",
    "SemanticSystemRequirement",
]
