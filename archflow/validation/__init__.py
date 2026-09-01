"""Read-only deterministic validation."""

from archflow.validation.assembly import (
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
from archflow.compilers.voxel_program import compile_building_program
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
    "validate_assembly",
    "validate_submission",
]
