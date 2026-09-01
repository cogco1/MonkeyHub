"""Read-only deterministic validation.

Relation verification is exposed lazily because the relation traversal
contracts themselves import ``archflow.validation.contracts``.  Eagerly
importing the bridge here would turn that valid dependency into a package
initialization cycle.
"""

from typing import TYPE_CHECKING

from archflow.validation.engine import (
    ArtifactPresentValidator,
    AuthorizedCommitmentClaimsValidator,
    ObligationDischargeValidator,
    RequiredClaimsValidator,
    Validator,
    validate_submission,
)
from archflow.validation.model import Finding, Severity, ValidationReceipt
from archflow.validation.program import compile_building_program

if TYPE_CHECKING:
    from archflow.validation.relation_verification import (
        RelationQuestionVerificationProfile,
        RelationVerificationBinding,
        RelationVerificationError,
        compile_relation_question_verification,
        independent_check_receipt_ref,
    )


_RELATION_VERIFICATION_EXPORTS = frozenset(
    {
        "RelationQuestionVerificationProfile",
        "RelationVerificationBinding",
        "RelationVerificationError",
        "compile_relation_question_verification",
        "independent_check_receipt_ref",
    }
)


def __getattr__(name: str):
    if name not in _RELATION_VERIFICATION_EXPORTS:
        raise AttributeError(name)
    from archflow.validation import relation_verification

    return getattr(relation_verification, name)

__all__ = [
    "ArtifactPresentValidator",
    "AuthorizedCommitmentClaimsValidator",
    "Finding",
    "ObligationDischargeValidator",
    "RequiredClaimsValidator",
    "RelationQuestionVerificationProfile",
    "RelationVerificationBinding",
    "RelationVerificationError",
    "Severity",
    "ValidationReceipt",
    "Validator",
    "compile_building_program",
    "compile_relation_question_verification",
    "independent_check_receipt_ref",
    "validate_submission",
]
