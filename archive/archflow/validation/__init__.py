"""Read-only deterministic validation.

Relation verification is exposed lazily because the relation traversal
contracts themselves import ``archflow.validation.contracts``.  Eagerly
importing the bridge here would turn that valid dependency into a package
initialization cycle.
"""

from typing import TYPE_CHECKING

from archive.archflow.validation.assembly import (
    AssemblyProfile,
    AssemblySubject,
    AssemblyValidationError,
    GeometryBoundsBasis,
    RelationshipKind,
    RelationshipRequirement,
    check_assembly,
    validate_assembly,
)
from archflow.validation.engine import (
    ArtifactPresentValidator,
    AuthorizedCommitmentClaimsValidator,
    ObligationDischargeValidator,
    RequiredClaimsValidator,
    Validator,
    validate_submission,
)
from archive.archflow.compilers.voxel_program import compile_building_program
from archflow.validation.model import Finding, Severity, ValidationReceipt
from archive.archflow.validation.interface_continuity import (
    InterfaceBoundarySegment,
    InterfaceBoundarySupportSet,
    InterfaceContinuityError,
    check_interface_boundary_continuity,
)
from archive.archflow.validation.vaulted_passage_current_state import (
    VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID,
    VaultedPassageCurrentStateContract,
    VaultedPassageFacadeRecord,
    VaultedPassageFacadeState,
    VaultedPassageMorphologyCriteria,
    VaultedPassageMorphologyRole,
    VaultedPassagePathBinding,
    VaultedPassageRoleBinding,
    VaultedPassageStateBasis,
    check_vaulted_passage_current_state,
)

if TYPE_CHECKING:
    from archive.archflow.validation.relation_verification import (
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
    from archive.archflow.validation import relation_verification

    return getattr(relation_verification, name)

__all__ = [
    "AssemblyProfile",
    "AssemblySubject",
    "AssemblyValidationError",
    "ArtifactPresentValidator",
    "AuthorizedCommitmentClaimsValidator",
    "Finding",
    "GeometryBoundsBasis",
    "InterfaceBoundarySegment",
    "InterfaceBoundarySupportSet",
    "InterfaceContinuityError",
    "ObligationDischargeValidator",
    "RequiredClaimsValidator",
    "RelationshipKind",
    "RelationshipRequirement",
    "RelationQuestionVerificationProfile",
    "RelationVerificationBinding",
    "RelationVerificationError",
    "Severity",
    "ValidationReceipt",
    "Validator",
    "VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID",
    "VaultedPassageCurrentStateContract",
    "VaultedPassageFacadeRecord",
    "VaultedPassageFacadeState",
    "VaultedPassageMorphologyCriteria",
    "VaultedPassageMorphologyRole",
    "VaultedPassagePathBinding",
    "VaultedPassageRoleBinding",
    "VaultedPassageStateBasis",
    "compile_building_program",
    "check_assembly",
    "check_interface_boundary_continuity",
    "check_vaulted_passage_current_state",
    "compile_relation_question_verification",
    "independent_check_receipt_ref",
    "validate_assembly",
    "validate_submission",
]
