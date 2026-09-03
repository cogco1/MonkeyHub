"""Pure compiler for the genesis semantic denominator.

This function consumes typed state, profile, subject-inventory, evidence, and
authority values.  It never accepts filesystem paths, persists records,
invents components, infers component types from names, or accepts a design
stage.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from archive.archflow.control.genesis_completeness import (
    GenesisSemanticCompletenessError,
    GenesisSemanticDenominator,
    SemanticDenominatorSourceKind,
    SemanticSystemBasis,
    SemanticSystemRequirement,
)
from archive.archflow.control.profile import StageRequirementProfileBinding
from archflow.control.requirements import (
    StageCheckRequirement,
    StageRequirementProfile,
)
from archive.archflow.control.stage_subjects import StageSubjectInventory
from archflow.project.refs import ProjectRecordRef
from archflow.state.stage_workflow import DesignPhase
from archflow.state.operational_state import OperationalMarkovState


class GenesisSemanticCompletenessCompilationError(
    GenesisSemanticCompletenessError
):
    """Typed sources cannot compile an exact semantic completeness result."""


def _require_exact_stage_context(
    *,
    state: OperationalMarkovState,
    profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    inventory: StageSubjectInventory,
) -> None:
    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be an OperationalMarkovState")
    if not isinstance(profile, StageRequirementProfile):
        raise TypeError("profile must be a StageRequirementProfile")
    if not isinstance(profile_binding, StageRequirementProfileBinding):
        raise TypeError(
            "profile_binding must be a StageRequirementProfileBinding"
        )
    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be a StageSubjectInventory")
    if profile.branch != state.branch:
        raise GenesisSemanticCompletenessCompilationError(
            "stage profile crossed the exact operational branch"
        )
    if inventory.branch != state.branch:
        raise GenesisSemanticCompletenessCompilationError(
            "stage inventory crossed the exact operational branch"
        )
    if profile.predecessor_state_digest != state.state_digest:
        raise GenesisSemanticCompletenessCompilationError(
            "stage profile names another operational state"
        )
    if profile.stage_id != state.phase:
        raise GenesisSemanticCompletenessCompilationError(
            "stage profile does not match the operational phase"
        )
    if inventory.stage_id != profile.stage_id:
        raise GenesisSemanticCompletenessCompilationError(
            "stage inventory and profile stage ids differ"
        )
    if inventory.stage_subject_ref != profile.stage_subject_ref:
        raise GenesisSemanticCompletenessCompilationError(
            "stage inventory and profile subject refs differ"
        )
    if profile_binding.is_legacy_read_only:
        raise GenesisSemanticCompletenessCompilationError(
            "legacy profile binding cannot authorize a current stage check"
        )
    if (
        profile_binding.branch != profile.branch
        or profile_binding.profile_id != profile.profile_id
        or profile_binding.profile_digest != profile.profile_digest
        or profile_binding.stage_id != profile.stage_id
        or profile_binding.stage_subject_ref != profile.stage_subject_ref
        or profile_binding.subject_digest != inventory.stage_subject_digest
        or profile_binding.stage_subject_inventory_digest
        != inventory.inventory_digest
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "profile binding does not identify the exact profile and inventory"
        )


def _require_profile_requirement(
    profile: StageRequirementProfile,
    requirement_id: str,
) -> StageCheckRequirement:
    matches = tuple(
        item
        for item in profile.requirements
        if item.requirement_id == requirement_id
    )
    if len(matches) != 1:
        raise GenesisSemanticCompletenessCompilationError(
            "semantic denominator profile requirement is missing"
        )
    return matches[0]


def _policy_mapping(
    value: Mapping[str, tuple[ProjectRecordRef, ...]] | None,
    field: str,
) -> dict[str, tuple[ProjectRecordRef, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    result: dict[str, tuple[ProjectRecordRef, ...]] = {}
    for system_ref, refs in value.items():
        if not isinstance(system_ref, str):
            raise TypeError(f"{field} keys must be semantic-system refs")
        if not isinstance(refs, tuple) or any(
            not isinstance(item, ProjectRecordRef) for item in refs
        ):
            raise TypeError(f"{field} values must contain ProjectRecordRef")
        result[system_ref] = refs
    return result


def compile_genesis_semantic_denominator(
    *,
    denominator_id: str,
    state: OperationalMarkovState,
    profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    inventory: StageSubjectInventory,
    profile_requirement_id: str,
    bases: tuple[SemanticSystemBasis, ...],
    not_applicable_evidence_refs: Mapping[
        str,
        tuple[ProjectRecordRef, ...],
    ]
    | None = None,
    not_applicable_authority_refs: Mapping[
        str,
        tuple[ProjectRecordRef, ...],
    ]
    | None = None,
) -> GenesisSemanticDenominator:
    """Merge only explicit authorized sources into the Stage-0 denominator.

    Same-key sources may reinforce one another only when they declare the
    exact same typed semantic-kind mapping.  Raw or non-applicable RAG,
    conflicting mappings, foreign project refs, and profile shrinkage fail
    closed.
    """

    _require_exact_stage_context(
        state=state,
        profile=profile,
        profile_binding=profile_binding,
        inventory=inventory,
    )
    if profile.stage_id != DesignPhase.RESEARCH_BRIEF.value:
        raise GenesisSemanticCompletenessCompilationError(
            "semantic denominator can only originate at research_brief Stage 0"
        )
    if state.branch.epoch != 0:
        raise GenesisSemanticCompletenessCompilationError(
            "semantic denominator can only originate at branch epoch zero"
        )
    if not isinstance(bases, tuple) or not bases or any(
        not isinstance(item, SemanticSystemBasis) for item in bases
    ):
        raise TypeError("bases must contain SemanticSystemBasis values")
    basis_ids = tuple(item.basis_id for item in bases)
    if len(basis_ids) != len(set(basis_ids)):
        raise GenesisSemanticCompletenessCompilationError(
            "semantic denominator basis ids are duplicated"
        )
    project_id = state.branch.run.project_id
    for basis in bases:
        if any(
            ref.project_id != project_id
            for ref in (basis.evidence_ref, basis.authority_ref)
        ):
            raise GenesisSemanticCompletenessCompilationError(
                "semantic denominator basis crossed its project"
            )
        if basis.authority_ref not in profile_binding.authority_refs:
            raise GenesisSemanticCompletenessCompilationError(
                "semantic denominator basis lacks bound profile authority"
            )
        if not basis.is_admissible:
            raise GenesisSemanticCompletenessCompilationError(
                "raw or non-applicable RAG cannot enter the denominator"
            )
        if basis.source_kind is SemanticDenominatorSourceKind.RAG:
            assert basis.research_scope is not None
            assert basis.adoption is not None
            assert basis.applicability is not None
            scope = basis.research_scope
            applicability = basis.applicability
            expected_prefix = (
                f"runs/{scope.run.run_id}/branches/"
                f"{scope.branch_id}/records/"
            )
            if (
                scope.run != state.branch.run
                or scope.source_branch != state.branch
                or scope.operational_state_digest != state.state_digest
                or scope.scope_digest != profile.scope_digest
                or applicability.branch != state.branch
                or applicability.scope_digest != profile.scope_digest
            ):
                raise GenesisSemanticCompletenessCompilationError(
                    "RAG basis crossed exact run/base/branch/epoch/state scope"
                )
            if any(
                not ref.relative_path.startswith(expected_prefix)
                for ref in (basis.evidence_ref, basis.authority_ref)
            ):
                raise GenesisSemanticCompletenessCompilationError(
                    "RAG evidence and authority refs left exact P036 branch records"
                )

    grouped: dict[str, list[SemanticSystemBasis]] = defaultdict(list)
    for basis in bases:
        grouped[basis.system_ref].append(basis)
    evidence_policy = _policy_mapping(
        not_applicable_evidence_refs,
        "not_applicable_evidence_refs",
    )
    authority_policy = _policy_mapping(
        not_applicable_authority_refs,
        "not_applicable_authority_refs",
    )
    system_refs = tuple(sorted(grouped))
    if set(evidence_policy) != set(authority_policy):
        raise GenesisSemanticCompletenessCompilationError(
            "not-applicable policy needs matching evidence and authority keys"
        )
    if not set(evidence_policy).issubset(system_refs):
        raise GenesisSemanticCompletenessCompilationError(
            "not-applicable policy names a system outside the denominator"
        )
    if any(
        not refs
        for policy in (evidence_policy, authority_policy)
        for refs in policy.values()
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "not-applicable policy refs must not be empty"
        )
    if any(
        ref.project_id != project_id
        for policy in (evidence_policy, authority_policy)
        for refs in policy.values()
        for ref in refs
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "not-applicable policy crossed its project"
        )
    if any(
        ref not in profile_binding.authority_refs
        for refs in authority_policy.values()
        for ref in refs
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "not-applicable policy lacks bound profile authority"
        )

    systems: list[SemanticSystemRequirement] = []
    for system_ref in system_refs:
        contributions = tuple(grouped[system_ref])
        first = contributions[0]
        if any(
            item.system_id != first.system_id
            or item.semantic_kinds != first.semantic_kinds
            for item in contributions[1:]
        ):
            raise GenesisSemanticCompletenessCompilationError(
                "same semantic-system key has conflicting typed mappings"
            )
        systems.append(
            SemanticSystemRequirement(
                system_id=first.system_id,
                system_ref=first.system_ref,
                semantic_kinds=first.semantic_kinds,
                bases=contributions,
                not_applicable_evidence_refs=evidence_policy.get(
                    system_ref,
                    (),
                ),
                not_applicable_authority_refs=authority_policy.get(
                    system_ref,
                    (),
                ),
            )
        )

    profile_requirement = _require_profile_requirement(
        profile,
        profile_requirement_id,
    )
    if profile_requirement.denominator_refs != system_refs:
        raise GenesisSemanticCompletenessCompilationError(
            "stage profile does not name the exact semantic denominator"
        )
    return GenesisSemanticDenominator(
        denominator_id=denominator_id,
        branch=state.branch,
        state_digest=state.state_digest,
        stage_id=profile.stage_id,
        stage_subject_ref=profile.stage_subject_ref,
        stage_subject_digest=inventory.stage_subject_digest,
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        profile_binding_id=profile_binding.binding_id,
        profile_binding_digest=profile_binding.binding_digest,
        profile_requirement_id=profile_requirement_id,
        systems=tuple(systems),
    )


__all__ = [
    "GenesisSemanticCompletenessCompilationError",
    "compile_genesis_semantic_denominator",
]
