"""Read-only deterministic validation."""

from archflow.validation.engine import (
    ArtifactPresentValidator,
    AuthorizedCommitmentClaimsValidator,
    ObligationDischargeValidator,
    RequiredClaimsValidator,
    Validator,
    validate_submission,
)
from archflow.validation.model import Finding, Severity, ValidationReceipt
from archflow.validation.interface_continuity import (
    InterfaceBoundarySegment,
    InterfaceBoundarySupportSet,
    InterfaceContinuityError,
    check_interface_boundary_continuity,
)
from archflow.validation.vaulted_passage_current_state import (
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
from archflow.validation.program import compile_building_program

__all__ = [
    "ArtifactPresentValidator",
    "AuthorizedCommitmentClaimsValidator",
    "Finding",
    "InterfaceBoundarySegment",
    "InterfaceBoundarySupportSet",
    "InterfaceContinuityError",
    "ObligationDischargeValidator",
    "RequiredClaimsValidator",
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
    "check_interface_boundary_continuity",
    "check_vaulted_passage_current_state",
    "validate_submission",
]
