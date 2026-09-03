"""Bridge independent checker receipts into relation-question receipts.

Agent-authored architectural relations remain hypotheses.  This module does
not inspect names or infer geometry.  A controller supplies an exact mapping
from every hypothesis relation in one question to requirements already
covered by an independent checker receipt.  Only that retained receipt can
produce the question-level receipt consumed by relation promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from archflow.contracts.canonical import canonical_digest
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
)
from archive.archflow.relations.authoring import RelationDerivationQuestion
from archflow.relations.contracts import (
    ArchitecturalRelationGraph,
    RelationEpistemicStatus,
)
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "promotion_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "geometry_mutation_authority": False,
    "canonical_write_authority": False,
}


class RelationVerificationError(ValueError):
    """A verification mapping or base receipt changed its exact identity."""


def independent_check_receipt_ref(receipt: CheckReceiptEnvelope) -> str:
    if not isinstance(receipt, CheckReceiptEnvelope):
        raise TypeError("receipt must be CheckReceiptEnvelope")
    return f"check-receipt:{receipt.receipt_digest}"


@dataclass(frozen=True, slots=True)
class RelationVerificationBinding:
    relation_ref: str
    checker_requirement_refs: tuple[str, ...]
    checker_subject_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "RelationVerificationBinding@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relation_ref",
            logical_ref(self.relation_ref, "relation verification relation_ref"),
        )
        object.__setattr__(
            self,
            "checker_requirement_refs",
            deterministic_refs(
                self.checker_requirement_refs,
                "relation verification checker_requirement_refs",
            ),
        )
        object.__setattr__(
            self,
            "checker_subject_refs",
            deterministic_refs(
                self.checker_subject_refs,
                "relation verification checker_subject_refs",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "relation_ref": self.relation_ref,
            "checker_requirement_refs": list(self.checker_requirement_refs),
            "checker_subject_refs": list(self.checker_subject_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationVerificationBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "relation_ref",
                "checker_requirement_refs",
                "checker_subject_refs",
                *_AUTHORITY_FIELDS,
            },
            "relation verification binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise RelationVerificationError(
                "unsupported relation verification binding schema"
            )
        if not isinstance(payload["checker_requirement_refs"], list) or not isinstance(
            payload["checker_subject_refs"], list
        ):
            raise TypeError(
                "checker requirement and subject refs must be lists"
            )
        result = cls(
            relation_ref=payload["relation_ref"],
            checker_requirement_refs=tuple(
                payload["checker_requirement_refs"]
            ),
            checker_subject_refs=tuple(payload["checker_subject_refs"]),
        )
        if result.to_dict() != payload:
            raise RelationVerificationError(
                "relation verification binding identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationQuestionVerificationProfile:
    profile_id: str
    question: RelationDerivationQuestion
    proposal_graph: ArchitecturalRelationGraph
    subject_inventory_ref: str
    output_checker_id: str
    base_checker_id: str
    bindings: tuple[RelationVerificationBinding, ...]

    SCHEMA: ClassVar[str] = "RelationQuestionVerificationProfile@1"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "relation verification profile_id")
        if not isinstance(self.question, RelationDerivationQuestion):
            raise TypeError("question must be RelationDerivationQuestion")
        if not isinstance(self.proposal_graph, ArchitecturalRelationGraph):
            raise TypeError("proposal_graph must be ArchitecturalRelationGraph")
        if any(
            relation.epistemic_status is not RelationEpistemicStatus.HYPOTHESIS
            for relation in self.proposal_graph.relations
        ):
            raise RelationVerificationError(
                "verification profile requires a HYPOTHESIS proposal graph"
            )
        object.__setattr__(
            self,
            "subject_inventory_ref",
            logical_ref(self.subject_inventory_ref, "subject_inventory_ref"),
        )
        identifier(self.output_checker_id, "output_checker_id")
        identifier(self.base_checker_id, "base_checker_id")
        if not isinstance(self.bindings, tuple) or not self.bindings or any(
            not isinstance(item, RelationVerificationBinding)
            for item in self.bindings
        ):
            raise TypeError(
                "bindings must contain RelationVerificationBinding values"
            )
        bindings = tuple(sorted(self.bindings, key=lambda item: item.relation_ref))
        relation_refs = tuple(item.relation_ref for item in bindings)
        if len(relation_refs) != len(set(relation_refs)):
            raise RelationVerificationError(
                "verification profile repeats a relation binding"
            )
        expected_relation_refs = tuple(
            sorted(
                relation.ref
                for relation in self.proposal_graph.relations
                if self.question.ref in relation.source_refs
            )
        )
        if relation_refs != expected_relation_refs:
            raise RelationVerificationError(
                "verification bindings do not equal the question denominator"
            )
        relations_by_ref = {
            relation.ref: relation for relation in self.proposal_graph.relations
        }
        for binding in bindings:
            participant_refs = {
                item.node_ref
                for item in relations_by_ref[binding.relation_ref].participants
            }
            if not participant_refs <= set(binding.checker_subject_refs):
                raise RelationVerificationError(
                    "checker subjects omit a hypothesis relation endpoint"
                )
        object.__setattr__(self, "bindings", bindings)

    @property
    def checker_requirement_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    ref
                    for binding in self.bindings
                    for ref in binding.checker_requirement_refs
                }
            )
        )

    @property
    def relation_refs(self) -> tuple[str, ...]:
        return tuple(item.relation_ref for item in self.bindings)

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"relation-verification-profile:{self.profile_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "question": self.question.to_dict(),
            "proposal_graph": self.proposal_graph.to_dict(),
            "subject_inventory_ref": self.subject_inventory_ref,
            "output_checker_id": self.output_checker_id,
            "base_checker_id": self.base_checker_id,
            "bindings": [item.to_dict() for item in self.bindings],
            "independent_check_required": True,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RelationQuestionVerificationProfile":
        payload = exact_mapping(
            value,
            {
                "schema",
                "profile_id",
                "question",
                "proposal_graph",
                "subject_inventory_ref",
                "output_checker_id",
                "base_checker_id",
                "bindings",
                "independent_check_required",
                *_AUTHORITY_FIELDS,
            },
            "relation question verification profile",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["independent_check_required"] is not True
        ):
            raise RelationVerificationError(
                "unsupported relation verification profile schema"
            )
        if not isinstance(payload["bindings"], list):
            raise TypeError("bindings must be a list")
        result = cls(
            profile_id=payload["profile_id"],
            question=RelationDerivationQuestion.from_dict(payload["question"]),
            proposal_graph=ArchitecturalRelationGraph.from_dict(
                payload["proposal_graph"]
            ),
            subject_inventory_ref=payload["subject_inventory_ref"],
            output_checker_id=payload["output_checker_id"],
            base_checker_id=payload["base_checker_id"],
            bindings=tuple(
                RelationVerificationBinding.from_dict(item)
                for item in payload["bindings"]
            ),
        )
        if result.to_dict() != payload:
            raise RelationVerificationError(
                "relation verification profile identity changed"
            )
        return result


def compile_relation_question_verification(
    profile: RelationQuestionVerificationProfile,
    base_receipt: CheckReceiptEnvelope,
) -> CheckReceiptEnvelope:
    """Compile one question receipt from one exact independent check."""

    if not isinstance(profile, RelationQuestionVerificationProfile):
        raise TypeError("profile must be RelationQuestionVerificationProfile")
    if not isinstance(base_receipt, CheckReceiptEnvelope):
        raise TypeError("base_receipt must be CheckReceiptEnvelope")
    graph = profile.proposal_graph
    required_checker_refs = set(profile.checker_requirement_refs)
    if (
        base_receipt.checker_id != profile.base_checker_id
        or base_receipt.branch != graph.branch
        or base_receipt.scope_digest != graph.scope_digest
        or base_receipt.subject_digest != graph.stage_subject_digest
        or not required_checker_refs <= set(base_receipt.subject_refs)
        or not required_checker_refs <= set(base_receipt.coverage_denominator)
        or base_receipt.revalidation_refs
    ):
        raise RelationVerificationError(
            "base receipt crossed checker, branch, scope, subject, or denominator"
        )
    if base_receipt.status is CheckStatus.NOT_APPLICABLE:
        raise RelationVerificationError(
            "relation verification cannot inherit not-applicable"
        )

    relations_by_ref = {
        item.ref: item for item in graph.relations
    }
    independent_ref = independent_check_receipt_ref(base_receipt)
    source_refs = tuple(
        sorted(
            {
                profile.ref,
                graph.ref,
                profile.question.ref,
                profile.subject_inventory_ref,
                independent_ref,
                *base_receipt.source_refs,
                *(
                    ref
                    for relation_ref in profile.relation_refs
                    for ref in relations_by_ref[relation_ref].evidence_refs
                ),
            }
        )
    )
    authority_refs = tuple(
        sorted(
            {
                *base_receipt.authority_refs,
                *(
                    ref
                    for relation_ref in profile.relation_refs
                    for ref in relations_by_ref[relation_ref].authority_refs
                ),
            }
        )
    )
    findings: tuple[CheckFinding, ...] = ()
    if base_receipt.status is CheckStatus.FAIL:
        findings = (
            CheckFinding(
                code="relation-independent-check-failed",
                severity=FindingSeverity.ERROR,
                message=(
                    "The exact independent checker failed one or more "
                    "requirements bound to this relation question."
                ),
                subject_refs=profile.relation_refs,
                evidence_refs=(independent_ref,),
            ),
        )
    elif base_receipt.status is CheckStatus.UNKNOWN:
        findings = (
            CheckFinding(
                code="relation-independent-check-unknown",
                severity=FindingSeverity.UNKNOWN,
                message=(
                    "The exact independent checker could not verify one or "
                    "more requirements bound to this relation question."
                ),
                subject_refs=profile.relation_refs,
                evidence_refs=(independent_ref,),
            ),
        )
    measurements = (
        CheckMeasurement(
            measurement_id="relation-verification-base-receipt-digest",
            subject_ref=profile.relation_refs[0],
            name="base_check_receipt_digest",
            value=base_receipt.receipt_digest,
            unit_ref=None,
            evidence_refs=(independent_ref,),
        ),
        CheckMeasurement(
            measurement_id="relation-verification-profile-digest",
            subject_ref=profile.relation_refs[0],
            name="relation_verification_profile_digest",
            value=profile.profile_digest,
            unit_ref=None,
            evidence_refs=(independent_ref,),
        ),
    )
    return CheckReceiptEnvelope(
        check_id=f"relation-verification-{profile.question.question_id}",
        checker_id=profile.output_checker_id,
        checker_version="1.0.0",
        branch=graph.branch,
        scope_digest=graph.scope_digest,
        subject_refs=profile.relation_refs,
        subject_digest=graph.stage_subject_digest,
        status=base_receipt.status,
        source_refs=source_refs,
        authority_refs=authority_refs,
        findings=findings,
        measurements=measurements,
        coverage_denominator=profile.relation_refs,
        covered_refs=profile.relation_refs,
    )


__all__ = [
    "RelationQuestionVerificationProfile",
    "RelationVerificationBinding",
    "RelationVerificationError",
    "compile_relation_question_verification",
    "independent_check_receipt_ref",
]
