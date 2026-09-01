"""Pure compilers for genesis semantic completeness and inheritance.

These functions consume typed state, profile, subject-inventory, evidence, and
authority values.  They never accept filesystem paths, persist records,
invent components, infer component types from names, or accept a design stage.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from archflow.control.genesis_completeness import (
    GenesisSemanticCompletenessError,
    GenesisSemanticDenominator,
    SemanticCompletenessReceipt,
    SemanticDenominatorSourceKind,
    SemanticSystemBasis,
    SemanticSystemDeclaration,
    SemanticSystemDisposition,
    SemanticSystemFinding,
    SemanticSystemFindingStatus,
    SemanticSystemRequirement,
)
from archflow.control.profile import StageRequirementProfileBinding
from archflow.control.requirements import (
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_subjects import StageSubjectInventory
from archflow.project.refs import BranchRef, ProjectRecordRef
from archflow.state.design_maturity import DesignPhase
from archflow.state.operational_state import OperationalMarkovState
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


class GenesisSemanticCompletenessCompilationError(
    GenesisSemanticCompletenessError
):
    """Typed sources cannot compile an exact semantic completeness result."""


def _same_lineage(left: BranchRef, right: BranchRef) -> bool:
    return left.run == right.run and left.branch_id == right.branch_id


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


def _optional_profile_requirement(
    profile: StageRequirementProfile,
    requirement_id: str,
) -> StageCheckRequirement | None:
    return next(
        (
            item
            for item in profile.requirements
            if item.requirement_id == requirement_id
        ),
        None,
    )


def _require_bound_denominator_authorities(
    denominator: GenesisSemanticDenominator,
    profile_binding: StageRequirementProfileBinding,
) -> None:
    required = {
        basis.authority_ref
        for system in denominator.systems
        for basis in system.bases
    } | {
        ref
        for system in denominator.systems
        for ref in system.not_applicable_authority_refs
    }
    if not required <= set(profile_binding.authority_refs):
        raise GenesisSemanticCompletenessCompilationError(
            "profile binding omitted semantic denominator authority"
        )


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


def _require_genesis_context(
    *,
    denominator: GenesisSemanticDenominator,
    state: OperationalMarkovState,
    profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    inventory: StageSubjectInventory,
) -> None:
    if not isinstance(denominator, GenesisSemanticDenominator):
        raise TypeError("denominator must be GenesisSemanticDenominator")
    _require_exact_stage_context(
        state=state,
        profile=profile,
        profile_binding=profile_binding,
        inventory=inventory,
    )
    if state.branch != denominator.branch:
        raise GenesisSemanticCompletenessCompilationError(
            "genesis denominator crossed its exact branch"
        )
    if state.state_digest != denominator.state_digest:
        raise GenesisSemanticCompletenessCompilationError(
            "genesis denominator crossed its exact operational state"
        )
    if (
        profile.stage_id != denominator.stage_id
        or profile.profile_id != denominator.profile_id
        or profile.profile_digest != denominator.profile_digest
        or profile_binding.binding_id != denominator.profile_binding_id
        or profile_binding.binding_digest
        != denominator.profile_binding_digest
        or profile.stage_subject_ref != denominator.stage_subject_ref
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "genesis denominator crossed its exact profile"
        )
    if (
        inventory.stage_subject_digest != denominator.stage_subject_digest
        or inventory.stage_subject_ref != denominator.stage_subject_ref
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "genesis denominator crossed its exact stage subject"
        )
    requirement = _require_profile_requirement(
        profile,
        denominator.profile_requirement_id,
    )
    if requirement.denominator_refs != denominator.system_refs:
        raise GenesisSemanticCompletenessCompilationError(
            "genesis profile semantic denominator changed"
        )
    _require_bound_denominator_authorities(denominator, profile_binding)


def _declarations_by_system(
    declarations: tuple[SemanticSystemDeclaration, ...],
    denominator: GenesisSemanticDenominator,
) -> dict[str, SemanticSystemDeclaration]:
    if not isinstance(declarations, tuple) or any(
        not isinstance(item, SemanticSystemDeclaration)
        for item in declarations
    ):
        raise TypeError(
            "declarations must contain SemanticSystemDeclaration values"
        )
    refs = tuple(item.system_ref for item in declarations)
    if len(refs) != len(set(refs)):
        raise GenesisSemanticCompletenessCompilationError(
            "semantic system declarations contain duplicate keys"
        )
    if not set(refs).issubset(denominator.system_refs):
        raise GenesisSemanticCompletenessCompilationError(
            "semantic system declaration is outside the denominator"
        )
    return {item.system_ref: item for item in declarations}


def _assess_findings(
    *,
    denominator: GenesisSemanticDenominator,
    inventory: StageSubjectInventory,
    declarations: tuple[SemanticSystemDeclaration, ...],
) -> tuple[SemanticSystemFinding, ...]:
    declared = _declarations_by_system(declarations, denominator)
    entries_by_ref = {
        item.identity_ref: item for item in inventory.entries
    }
    project_id = inventory.branch.run.project_id
    findings: list[SemanticSystemFinding] = []
    for requirement in denominator.systems:
        declaration = declared.get(requirement.system_ref)
        if declaration is None:
            findings.append(
                SemanticSystemFinding(
                    system_ref=requirement.system_ref,
                    declared_disposition=None,
                    status=SemanticSystemFindingStatus.OPEN,
                    component_refs=(),
                    evidence_refs=(),
                    authority_refs=(),
                    reason_codes=("missing-declaration",),
                )
            )
            continue
        if declaration.disposition is SemanticSystemDisposition.UNKNOWN:
            findings.append(
                SemanticSystemFinding(
                    system_ref=requirement.system_ref,
                    declared_disposition=declaration.disposition,
                    status=SemanticSystemFindingStatus.OPEN,
                    component_refs=(),
                    evidence_refs=(),
                    authority_refs=(),
                    reason_codes=("unknown-declaration",),
                )
            )
            continue

        eligible_refs = {
            item.identity_ref
            for item in inventory.entries
            if item.semantic_kind in requirement.semantic_kinds
        }
        if declaration.disposition is SemanticSystemDisposition.PRESENT:
            reasons: set[str] = set()
            if not declaration.component_refs:
                reasons.add("present-without-component")
            missing_refs = set(declaration.component_refs) - set(entries_by_ref)
            if missing_refs:
                reasons.add("component-ref-missing")
            if set(declaration.component_refs) - eligible_refs:
                reasons.add("semantic-kind-mismatch")
            status = (
                SemanticSystemFindingStatus.REJECTED
                if reasons
                else SemanticSystemFindingStatus.PRESENT
            )
            findings.append(
                SemanticSystemFinding(
                    system_ref=requirement.system_ref,
                    declared_disposition=declaration.disposition,
                    status=status,
                    component_refs=declaration.component_refs,
                    evidence_refs=(),
                    authority_refs=(),
                    reason_codes=(
                        tuple(sorted(reasons))
                        if reasons
                        else ("typed-component-present",)
                    ),
                )
            )
            continue

        reasons = set()
        if eligible_refs:
            reasons.add("not-applicable-contradicts-inventory")
        allowed_evidence = set(requirement.not_applicable_evidence_refs)
        allowed_authority = set(requirement.not_applicable_authority_refs)
        if (
            not declaration.evidence_refs
            or not set(declaration.evidence_refs).issubset(allowed_evidence)
        ):
            reasons.add("not-applicable-evidence-unauthorized")
        if (
            not declaration.authority_refs
            or not set(declaration.authority_refs).issubset(allowed_authority)
        ):
            reasons.add("not-applicable-authority-unauthorized")
        if any(
            item.project_id != project_id
            for item in (
                *declaration.evidence_refs,
                *declaration.authority_refs,
            )
        ):
            reasons.add("not-applicable-cross-project")
        status = (
            SemanticSystemFindingStatus.REJECTED
            if reasons
            else SemanticSystemFindingStatus.NOT_APPLICABLE
        )
        findings.append(
            SemanticSystemFinding(
                system_ref=requirement.system_ref,
                declared_disposition=declaration.disposition,
                status=status,
                component_refs=(),
                evidence_refs=declaration.evidence_refs,
                authority_refs=declaration.authority_refs,
                reason_codes=(
                    tuple(sorted(reasons))
                    if reasons
                    else ("authorized-not-applicable",)
                ),
            )
        )
    return tuple(findings)


def _status(
    *,
    denominator_preserved: bool,
    findings: tuple[SemanticSystemFinding, ...],
) -> CheckStatus:
    if not denominator_preserved or any(
        item.status is SemanticSystemFindingStatus.REJECTED
        for item in findings
    ):
        return CheckStatus.FAIL
    if any(
        item.status is SemanticSystemFindingStatus.OPEN for item in findings
    ):
        return CheckStatus.UNKNOWN
    return CheckStatus.PASS


def compile_genesis_semantic_completeness(
    *,
    receipt_id: str,
    denominator: GenesisSemanticDenominator,
    state: OperationalMarkovState,
    profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    inventory: StageSubjectInventory,
    declarations: tuple[SemanticSystemDeclaration, ...],
) -> SemanticCompletenessReceipt:
    """Assess Stage-0 declarations without accepting or persisting the stage."""

    _require_genesis_context(
        denominator=denominator,
        state=state,
        profile=profile,
        profile_binding=profile_binding,
        inventory=inventory,
    )
    findings = _assess_findings(
        denominator=denominator,
        inventory=inventory,
        declarations=declarations,
    )
    return SemanticCompletenessReceipt(
        receipt_id=receipt_id,
        denominator_id=denominator.denominator_id,
        denominator_digest=denominator.denominator_digest,
        origin_branch=denominator.branch,
        origin_state_digest=denominator.state_digest,
        assessed_branch=state.branch,
        assessed_state_digest=state.state_digest,
        stage_id=profile.stage_id,
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        profile_binding_id=profile_binding.binding_id,
        profile_binding_digest=profile_binding.binding_digest,
        inventory_id=inventory.inventory_id,
        inventory_digest=inventory.inventory_digest,
        profile_requirement_id=denominator.profile_requirement_id,
        inherited_from_receipt_digest=None,
        denominator_preserved=True,
        findings=findings,
        reason_codes=("genesis-denominator-exact",),
        status=_status(
            denominator_preserved=True,
            findings=findings,
        ),
    )


def compile_semantic_denominator_inheritance(
    *,
    receipt_id: str,
    denominator: GenesisSemanticDenominator,
    genesis_evidence: SemanticCompletenessReceipt,
    state: OperationalMarkovState,
    profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    inventory: StageSubjectInventory,
    profile_requirement_id: str,
    declarations: tuple[SemanticSystemDeclaration, ...],
) -> SemanticCompletenessReceipt:
    """Assess a later stage against the exact checked genesis denominator.

    The successor profile may not synchronously shrink or substitute the
    denominator.  This compiler is intentionally separate from composite stage
    closure and grants no stage-acceptance authority.
    """

    if not isinstance(genesis_evidence, SemanticCompletenessReceipt):
        raise TypeError("genesis_evidence must be SemanticCompletenessReceipt")
    if genesis_evidence.status is not CheckStatus.PASS:
        raise GenesisSemanticCompletenessCompilationError(
            "semantic denominator inheritance needs passing genesis evidence"
        )
    if (
        genesis_evidence.denominator_id != denominator.denominator_id
        or genesis_evidence.denominator_digest
        != denominator.denominator_digest
        or genesis_evidence.origin_branch != denominator.branch
        or genesis_evidence.origin_state_digest != denominator.state_digest
        or genesis_evidence.system_refs != denominator.system_refs
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "genesis evidence changed denominator identity"
        )
    _require_exact_stage_context(
        state=state,
        profile=profile,
        profile_binding=profile_binding,
        inventory=inventory,
    )
    _require_bound_denominator_authorities(denominator, profile_binding)
    if not _same_lineage(denominator.branch, state.branch):
        raise GenesisSemanticCompletenessCompilationError(
            "successor semantic denominator crossed branch lineage"
        )
    if state.branch.epoch <= denominator.branch.epoch:
        raise GenesisSemanticCompletenessCompilationError(
            "successor semantic denominator did not advance beyond genesis"
        )
    requirement = _optional_profile_requirement(
        profile,
        profile_requirement_id,
    )
    successor_refs = (
        requirement.denominator_refs if requirement is not None else ()
    )
    denominator_preserved = successor_refs == denominator.system_refs
    if denominator_preserved:
        reason_codes = ("semantic-denominator-inherited",)
    elif set(successor_refs) < set(denominator.system_refs):
        reason_codes = ("semantic-denominator-shrunk",)
    else:
        reason_codes = ("semantic-denominator-changed",)
    findings = _assess_findings(
        denominator=denominator,
        inventory=inventory,
        declarations=declarations,
    )
    return SemanticCompletenessReceipt(
        receipt_id=receipt_id,
        denominator_id=denominator.denominator_id,
        denominator_digest=denominator.denominator_digest,
        origin_branch=denominator.branch,
        origin_state_digest=denominator.state_digest,
        assessed_branch=state.branch,
        assessed_state_digest=state.state_digest,
        stage_id=profile.stage_id,
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        profile_binding_id=profile_binding.binding_id,
        profile_binding_digest=profile_binding.binding_digest,
        inventory_id=inventory.inventory_id,
        inventory_digest=inventory.inventory_digest,
        profile_requirement_id=profile_requirement_id,
        inherited_from_receipt_digest=genesis_evidence.receipt_digest,
        denominator_preserved=denominator_preserved,
        findings=findings,
        reason_codes=reason_codes,
        status=_status(
            denominator_preserved=denominator_preserved,
            findings=findings,
        ),
    )


def bridge_semantic_completeness_check(
    *,
    denominator: GenesisSemanticDenominator,
    source_receipt: SemanticCompletenessReceipt,
    profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    inventory: StageSubjectInventory,
) -> CheckReceiptEnvelope:
    """Bridge internal evidence into the existing stage-closure check envelope.

    This is the only stage-gate surface produced here.  The returned envelope
    still requires ``compile_composite_stage_closure`` and the existing
    controller path to block or advance a stage; this function does neither.
    """

    if not isinstance(denominator, GenesisSemanticDenominator):
        raise TypeError("denominator must be GenesisSemanticDenominator")
    if not isinstance(source_receipt, SemanticCompletenessReceipt):
        raise TypeError("source_receipt must be SemanticCompletenessReceipt")
    if not isinstance(profile, StageRequirementProfile):
        raise TypeError("profile must be StageRequirementProfile")
    if not isinstance(profile_binding, StageRequirementProfileBinding):
        raise TypeError(
            "profile_binding must be StageRequirementProfileBinding"
        )
    if not isinstance(inventory, StageSubjectInventory):
        raise TypeError("inventory must be StageSubjectInventory")
    if (
        profile_binding.is_legacy_read_only
        or profile_binding.branch != profile.branch
        or profile_binding.profile_id != profile.profile_id
        or profile_binding.profile_digest != profile.profile_digest
        or profile_binding.stage_id != profile.stage_id
        or profile_binding.stage_subject_ref != profile.stage_subject_ref
        or profile_binding.subject_digest != inventory.stage_subject_digest
        or profile_binding.stage_subject_inventory_digest
        != inventory.inventory_digest
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "check bridge profile binding does not identify exact inputs"
        )
    if (
        source_receipt.denominator_id != denominator.denominator_id
        or source_receipt.denominator_digest != denominator.denominator_digest
        or source_receipt.assessed_branch != profile.branch
        or source_receipt.assessed_state_digest
        != profile.predecessor_state_digest
        or source_receipt.stage_id != profile.stage_id
        or source_receipt.profile_id != profile.profile_id
        or source_receipt.profile_digest != profile.profile_digest
        or source_receipt.profile_binding_id != profile_binding.binding_id
        or source_receipt.profile_binding_digest
        != profile_binding.binding_digest
        or source_receipt.inventory_id != inventory.inventory_id
        or source_receipt.inventory_digest != inventory.inventory_digest
        or source_receipt.system_refs != denominator.system_refs
    ):
        raise GenesisSemanticCompletenessCompilationError(
            "check bridge source evidence crossed exact bound inputs"
        )
    _require_bound_denominator_authorities(denominator, profile_binding)
    requirement = _require_profile_requirement(
        profile,
        source_receipt.profile_requirement_id,
    )
    if requirement.denominator_refs != denominator.system_refs:
        raise GenesisSemanticCompletenessCompilationError(
            "check bridge profile changed the semantic denominator"
        )

    check_findings: list[CheckFinding] = []
    for finding in source_receipt.findings:
        if finding.status is SemanticSystemFindingStatus.REJECTED:
            severity = FindingSeverity.ERROR
            code = "semantic-system-rejected"
            message = "Semantic-system declaration failed exact evidence checks."
        elif finding.status is SemanticSystemFindingStatus.OPEN:
            severity = FindingSeverity.UNKNOWN
            code = "semantic-system-open"
            message = "Semantic-system declaration remains unknown or missing."
        else:
            continue
        check_findings.append(
            CheckFinding(
                code=code,
                severity=severity,
                message=message,
                subject_refs=(finding.system_ref,),
                evidence_refs=tuple(
                    sorted(
                        {
                            item.uri
                            for item in (
                                *finding.evidence_refs,
                                *finding.authority_refs,
                            )
                        }
                    )
                ),
            )
        )
    if not source_receipt.denominator_preserved:
        check_findings.append(
            CheckFinding(
                code="semantic-denominator-not-preserved",
                severity=FindingSeverity.ERROR,
                message="Successor stage changed the exact genesis denominator.",
                subject_refs=(denominator.system_refs[0],),
                evidence_refs=(),
            )
        )
    check_findings_tuple = tuple(
        sorted(
            check_findings,
            key=lambda item: (item.code, item.subject_refs, item.message),
        )
    )

    rag_bases = tuple(
        basis
        for system in denominator.systems
        for basis in system.bases
        if basis.source_kind is SemanticDenominatorSourceKind.RAG
    )
    applicabilities = tuple(
        sorted(
            (
                basis.applicability
                for basis in rag_bases
                if basis.applicability is not None
            ),
            key=lambda item: item.ref,
        )
    )
    claim_refs = tuple(sorted({item.claim_ref for item in applicabilities}))
    applicability_refs = tuple(sorted({item.ref for item in applicabilities}))
    adoption_refs = tuple(
        sorted({basis.rag_adoption_ref for basis in rag_bases})
    )
    source_refs = tuple(
        sorted(
            {
                basis.evidence_ref.uri
                for system in denominator.systems
                for basis in system.bases
            }
            | {
                ref
                for item in applicabilities
                for ref in item.source_refs
            }
            | {
                ref.uri
                for finding in source_receipt.findings
                for ref in finding.evidence_refs
            }
        )
    )
    authority_refs = tuple(
        sorted(
            {
                basis.authority_ref.uri
                for system in denominator.systems
                for basis in system.bases
            }
            | {
                ref
                for item in applicabilities
                for ref in item.authority_refs
            }
            | {
                ref.uri
                for finding in source_receipt.findings
                for ref in finding.authority_refs
            }
            | {item.uri for item in profile_binding.authority_refs}
        )
    )
    anchor = denominator.system_refs[0]
    result = CheckReceiptEnvelope(
        check_id=requirement.requirement_id,
        checker_id="genesis-semantic-completeness",
        checker_version="1.0.0",
        branch=profile.branch,
        scope_digest=profile.scope_digest,
        subject_refs=requirement.denominator_refs,
        subject_digest=inventory.stage_subject_digest,
        status=source_receipt.status,
        claim_refs=claim_refs,
        applicability_refs=applicability_refs,
        adoption_refs=adoption_refs,
        source_refs=source_refs,
        authority_refs=authority_refs,
        findings=check_findings_tuple,
        measurements=(
            CheckMeasurement(
                measurement_id="denominator-digest",
                subject_ref=anchor,
                name="denominator-digest",
                value=denominator.denominator_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="profile-binding-digest",
                subject_ref=anchor,
                name="profile-binding-digest",
                value=profile_binding.binding_digest,
                unit_ref=None,
            ),
            CheckMeasurement(
                measurement_id="source-receipt-digest",
                subject_ref=anchor,
                name="source-receipt-digest",
                value=source_receipt.receipt_digest,
                unit_ref=None,
            ),
        ),
        coverage_denominator=requirement.denominator_refs,
        covered_refs=requirement.denominator_refs,
    )
    if applicabilities:
        result.require_applicabilities(applicabilities)
    return result


__all__ = [
    "GenesisSemanticCompletenessCompilationError",
    "bridge_semantic_completeness_check",
    "compile_genesis_semantic_completeness",
    "compile_genesis_semantic_denominator",
    "compile_semantic_denominator_inheritance",
]
