"""Compile validator profiles into exact stage-check requirements.

These adapters are requirement-first: they derive the denominator and basis
from an immutable validator profile before the validator runs.  They do not
inspect a receipt and therefore cannot legitimize an incomplete check after
the fact.
"""

from __future__ import annotations

from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
)
from archflow.materials.binding import MaterialBindingProfile
from archflow.validation.assembly import AssemblyProfile
from archflow.validation.cad_readback import CadReadbackProfile
from archflow.validation.check_bridges import (
    ComponentLineageCheckProfile,
    SpatialLayoutCheckProfile,
)


def assembly_stage_requirement(
    profile: AssemblyProfile,
) -> StageCheckRequirement:
    """Require the exact assembly denominator and its explicit authorities."""

    if not isinstance(profile, AssemblyProfile):
        raise TypeError("profile must be an AssemblyProfile")
    authority_refs = tuple(
        sorted(
            {
                ref
                for requirement in profile.requirements
                for ref in requirement.authority_refs
            }
            | {
                ref
                for obligation in profile.coverage_manifest.obligations
                for ref in obligation.authority_refs
            }
            | {
                ref
                for candidate in profile.coverage_manifest.relation_candidates
                for ref in candidate.authority_refs
            }
        )
    )
    source_refs = tuple(
        sorted(
            {
                ref
                for requirement in profile.requirements
                for ref in requirement.evidence_refs
            }
            | {
                ref
                for obligation in profile.coverage_manifest.obligations
                for ref in obligation.evidence_refs
            }
            | {
                ref
                for candidate in profile.coverage_manifest.relation_candidates
                for ref in candidate.evidence_refs
            }
        )
    )
    return StageCheckRequirement(
        requirement_id=f"assembly-{profile.profile_digest[:24]}",
        checker_id="assembly-relationship-checker",
        target_kind=RequirementTargetKind.ASSEMBLY,
        basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
        denominator_refs=profile.check_denominator,
        required_source_refs=source_refs,
        required_authority_refs=authority_refs,
    )


def material_binding_stage_requirement(
    profile: MaterialBindingProfile,
) -> StageCheckRequirement:
    """Require exact semantic-to-ledger-to-geometry material bindings."""

    if not isinstance(profile, MaterialBindingProfile):
        raise TypeError("profile must be a MaterialBindingProfile")
    return StageCheckRequirement(
        requirement_id="material-binding",
        checker_id="material-binding-validator",
        target_kind=RequirementTargetKind.MATERIAL,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.check_denominator,
    )


def cad_readback_stage_requirement(
    profile: CadReadbackProfile,
) -> StageCheckRequirement:
    """Require exact operation/object correspondence after CAD readback."""

    if not isinstance(profile, CadReadbackProfile):
        raise TypeError("profile must be a CadReadbackProfile")
    return StageCheckRequirement(
        requirement_id="cad-readback",
        checker_id="cad-readback-validator",
        target_kind=RequirementTargetKind.ARTIFACT,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.check_denominator,
    )


def component_lineage_stage_requirement(
    profile: ComponentLineageCheckProfile,
) -> StageCheckRequirement:
    """Require the exact predecessor lineage input and declared subjects."""

    if not isinstance(profile, ComponentLineageCheckProfile):
        raise TypeError("profile must be a ComponentLineageCheckProfile")
    return StageCheckRequirement(
        requirement_id=profile.check_id,
        checker_id="component-lineage-validator",
        target_kind=RequirementTargetKind.COMPONENT,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.denominator_refs,
    )


def spatial_layout_stage_requirement(
    profile: SpatialLayoutCheckProfile,
) -> StageCheckRequirement:
    """Require one exact normalized spatial input and declared subjects."""

    if not isinstance(profile, SpatialLayoutCheckProfile):
        raise TypeError("profile must be a SpatialLayoutCheckProfile")
    return StageCheckRequirement(
        requirement_id=profile.check_id,
        checker_id="spatial-layout-validator",
        target_kind=RequirementTargetKind.ASSEMBLY,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=profile.denominator_refs,
    )


__all__ = [
    "assembly_stage_requirement",
    "cad_readback_stage_requirement",
    "component_lineage_stage_requirement",
    "material_binding_stage_requirement",
    "spatial_layout_stage_requirement",
]
