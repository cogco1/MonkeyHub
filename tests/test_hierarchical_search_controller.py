from __future__ import annotations

import asyncio
import hashlib
import json
import math
import unittest
from dataclasses import replace

from archflow.contracts.canonical import canonical_digest
from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    StageBaselineSourceSet,
    StageBaselineStatus,
    baseline_level_for_design_phase,
    compile_stage_baseline_coverage,
)
from archflow.control.convergence import (
    StageConvergenceEvidence,
    StageConvergencePolicy,
    StageTransitionKind,
    StageTransitionRequest,
    evaluate_stage_convergence,
)
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.search_policy import (
    DecisionSpaceKind,
    ObjectiveDirection,
    SearchAction,
    SearchBudget,
    SearchBudgetAllocation,
    SearchCandidateEvaluation,
    SearchDirective,
    SearchObjectiveEstimate,
    SearchPolicyDescriptor,
    SearchPolicyRequest,
)
from archflow.control.stage_closure import compile_composite_stage_closure
from archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectRoleObligation,
)
from archflow.evidence.applicability import (
    AllowedClaimUse,
    ApplicabilityDisposition,
    ApplicabilityTargetKind,
    ClaimApplicability,
)
from archflow.evidence.claims import EvidenceClaimBinding
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.project.manifest import ProjectManifest
from archflow.research.adoption import PrecedentAdoption, PrecedentFact
from archflow.research.branch import BranchPrecedentAdoption
from archflow.runtime.hierarchical_search import (
    AdoptedApplicableSearchEvidence,
    BoundCandidateEvaluatorReceipt,
    HierarchicalSearchCompileInput,
    HierarchicalSearchProposal,
    HierarchicalSearchProposalCompiler,
    HierarchicalSearchProposalError,
    RetainedCheckReceipt,
    SearchGovernanceEvidence,
    SearchReopenEnvelope,
    compile_search_policy_request,
    compile_search_proposal,
    exact_record_ref,
    portfolio_candidate_ref,
)
from archflow.runtime.branch_portfolio import PersistedDesignPortfolio
from archflow.runtime.design_controller import ProjectControllerArchiveAdapter
from archflow.runtime.search_policy import SearchPolicyRegistry
from archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.operational_state import (
    DesignObligation,
    ObligationStatus,
    OperationalMarkovState,
)
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from tests.test_design_portfolio import _portfolio


HARD_REFS = ("constraint:fire", "constraint:structure")
OTHER_DIGEST = "f" * 64


def _record(
    run: RunRef,
    name: str,
    digest_char: str,
    *,
    run_id: str | None = None,
) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=run.project_id,
        relative_path=(
            f"runs/{run_id or run.run_id}/records/{name}.json"
        ),
        sha256=digest_char * 64,
    )


def _branch_record(
    branch: BranchRef,
    name: str,
    digest_char: str | None = None,
    *,
    payload: dict[str, object] | None = None,
) -> ProjectRecordRef:
    if payload is not None:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sha256 = hashlib.sha256(
            (encoded + "\n").encode("utf-8")
        ).hexdigest()
    elif digest_char is not None:
        sha256 = digest_char * 64
    else:
        raise ValueError("branch record requires a payload or digest char")
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
            f"{name}.json"
        ),
        sha256=sha256,
    )


def _record_payload(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


class _SearchGovernanceRepository:
    def __init__(
        self,
        run: RunRef,
        records: dict[ProjectRecordRef, dict[str, object]],
    ) -> None:
        self.run = run
        self.records = records

    def load_manifest(self) -> ProjectManifest:
        return ProjectManifest(self.run.project_id, format_version=2)

    def load_run(self, run_id: str) -> RunRef:
        if run_id != self.run.run_id:
            raise ValueError("unknown run")
        return self.run

    def load_json(self, ref: ProjectRecordRef) -> dict[str, object]:
        try:
            return dict(self.records[ref])
        except KeyError as exc:
            raise ValueError("record ref or digest is not retained") from exc

    def list_json(self, **_kwargs: object) -> tuple[ProjectRecordRef, ...]:
        return tuple(self.records)

    def put_json(self, **_kwargs: object) -> ProjectRecordRef:
        raise AssertionError("search governance replay is read-only")


def _governance_archive(
    governance: SearchGovernanceEvidence,
    branch: BranchRef,
) -> ProjectControllerArchiveAdapter:
    records = {
        governance.profile_record_ref: governance.profile.to_dict(),
        governance.closure_record_ref: governance.closure.to_dict(),
        governance.convergence_record_ref: governance.convergence.to_dict(),
        **{
            item.record_ref: item.receipt.to_dict()
            for item in governance.closure_checks
        },
    }
    if not governance.is_legacy_read_only:
        assert governance.baseline_sources_record_ref is not None
        assert governance.baseline_sources is not None
        assert governance.stage_subject_inventory_record_ref is not None
        assert governance.stage_subject_inventory is not None
        assert governance.baseline_coverage_record_ref is not None
        assert governance.baseline_coverage is not None
        records.update(
            {
                governance.baseline_sources_record_ref: (
                    governance.baseline_sources.to_dict()
                ),
                governance.stage_subject_inventory_record_ref: (
                    governance.stage_subject_inventory.to_dict()
                ),
                governance.baseline_coverage_record_ref: (
                    governance.baseline_coverage.to_dict()
                ),
            }
        )
    return ProjectControllerArchiveAdapter(
        _SearchGovernanceRepository(branch.run, records),
        branch=branch,
    )


def _obligation(status: ObligationStatus) -> DesignObligation:
    return DesignObligation(
        obligation_id="close-stage",
        statement="Resolve the current stage package.",
        source_ref="requirement:close-stage",
        status=status,
        subject_refs=("deliverable:stage-3",),
    )


def _states(
    run: RunRef,
    *,
    phase: str = DesignPhase.SCHEMATIC_DESIGN.value,
) -> tuple[OperationalMarkovState, OperationalMarkovState]:
    parent = OperationalMarkovState(
        branch=BranchRef(run=run, branch_id="branch-a", epoch=3),
        compiler_version="hierarchical-search-test",
        phase=phase,
        obligations=(_obligation(ObligationStatus.OPEN),),
    )
    child = OperationalMarkovState(
        branch=BranchRef(run=run, branch_id="branch-a", epoch=4),
        compiler_version="hierarchical-search-test",
        phase=phase,
        obligations=(_obligation(ObligationStatus.SATISFIED),),
    )
    return parent, child


def _policy() -> SearchPolicyDescriptor:
    return SearchPolicyDescriptor(
        policy_id="hierarchical-test-policy",
        policy_family="bounded-search",
        policy_version="2.4.1",
        implementation_digest="d" * 64,
        supported_space_kinds=(DecisionSpaceKind.CONTINUOUS,),
        supported_actions=(
            SearchAction.DEEPEN,
            SearchAction.HOLD,
            SearchAction.PRUNE,
            SearchAction.REQUEST_COMMIT,
            SearchAction.REQUEST_REOPEN,
            SearchAction.STOP,
        ),
        supports_multiobjective=True,
    )


def _snapshot(
    portfolio,
    state: OperationalMarkovState,
) -> PersistedDesignPortfolio:
    exact = replace(
        portfolio,
        operational_state_digest=state.state_digest,
    )
    return PersistedDesignPortfolio(
        record_ref=_record(exact.run, "portfolio", "9"),
        portfolio=exact,
    )


def _stage_governance(
    parent: OperationalMarkovState,
    state: OperationalMarkovState,
    *,
    closure_passed: bool = True,
    convergence_ready: bool = True,
    current_baseline: bool = False,
) -> SearchGovernanceEvidence:
    requirement = StageCheckRequirement(
        requirement_id="stage-integrity",
        checker_id="archflow.stage-integrity",
        target_kind=RequirementTargetKind.ASSEMBLY,
        basis_mode=RequirementBasisMode.UNIVERSAL,
        denominator_refs=("constraint:stage-integrity",),
    )
    profile = StageRequirementProfile(
        profile_id="stage-3-profile",
        typology_id="generic-building",
        stage_id="stage-3",
        branch=state.branch,
        predecessor_state_digest=state.state_digest,
        scope_digest="c" * 64,
        stage_subject_ref="deliverable:stage-3",
        requirements=(requirement,),
    )
    findings = (
        ()
        if closure_passed
        else (
            CheckFinding(
                code="stage_open",
                severity=FindingSeverity.ERROR,
                message="Stage integrity remains open.",
                subject_refs=requirement.denominator_refs,
            ),
        )
    )
    status = CheckStatus.PASS if closure_passed else CheckStatus.FAIL
    closure_check = CheckReceiptEnvelope(
        check_id=requirement.requirement_id,
        checker_id=requirement.checker_id,
        checker_version="1.0.0",
        branch=state.branch,
        scope_digest=profile.scope_digest,
        subject_refs=requirement.denominator_refs,
        subject_digest=state.state_digest,
        status=status,
        findings=findings,
        coverage_denominator=requirement.denominator_refs,
        covered_refs=requirement.denominator_refs,
    )
    closure = compile_composite_stage_closure(
        profile,
        subject_digest=state.state_digest,
        check_receipts=(closure_check,),
    )

    convergence_policy = StageConvergencePolicy(
        policy_id="stage-3-convergence",
        stage="stage-3",
        mandatory_obligation_ids=("close-stage",),
    )
    convergence_request = StageTransitionRequest(
        request_id="resolve-stage-3",
        stage="stage-3",
        kind=StageTransitionKind.RESOLVE,
        authority_id="authority.architect",
        parent_state_digest=parent.state_digest,
        child_state_digest=state.state_digest,
    )
    convergence = evaluate_stage_convergence(
        convergence_policy,
        convergence_request,
        parent,
        state,
        parent_evidence=StageConvergenceEvidence(
            state_digest=parent.state_digest,
        ),
        child_evidence=StageConvergenceEvidence(
            state_digest=state.state_digest,
            hard_gate_failure_refs=(
                ()
                if convergence_ready
                else ("gate-failure:stage-3",)
            ),
        ),
    )
    run = state.branch.run
    profile_record_ref = _branch_record(
        state.branch,
        "stage-profile",
        payload=profile.to_dict(),
    )
    retained_check = RetainedCheckReceipt(
        record_ref=_branch_record(
            state.branch,
            "stage-check",
            payload=closure_check.to_dict(),
        ),
        receipt=closure_check,
    )
    closure_record_ref = _branch_record(
        state.branch,
        "stage-closure",
        payload=closure.to_dict(),
    )
    convergence_record_ref = _branch_record(
        state.branch,
        "stage-convergence",
        payload=convergence.to_dict(),
    )
    if not current_baseline:
        return SearchGovernanceEvidence.from_dict(
            {
                "schema": SearchGovernanceEvidence.LEGACY_SCHEMA,
                "profile_record_ref": _record_payload(profile_record_ref),
                "profile": profile.to_dict(),
                "closure_checks": [
                    {
                        "record_ref": _record_payload(
                            retained_check.record_ref
                        ),
                        "receipt": retained_check.receipt.to_dict(),
                    }
                ],
                "closure_record_ref": _record_payload(closure_record_ref),
                "closure": closure.to_dict(),
                "convergence_record_ref": _record_payload(
                    convergence_record_ref
                ),
                "convergence": convergence.to_dict(),
            }
        )

    level = baseline_level_for_design_phase(DesignPhase(state.phase))
    record_prefix = (
        f"runs/{run.run_id}/branches/{state.branch.branch_id}/records"
    )
    role_obligations = tuple(
        StageSubjectRoleObligation(
            role=role,
            disposition=StageSubjectDisposition.REQUIRED,
            target_refs=("design-component:stage-root",),
            evidence_refs=(f"evidence:{role.value}",),
            authority_refs=(f"authority:{role.value}",),
        )
        for role in sorted(BASELINE_LEVEL_ROLES[level])
    )
    inventory = StageSubjectInventory(
        inventory_id="stage-3-search-subjects",
        branch=state.branch,
        stage_id="stage-3",
        stage_subject_ref=profile.stage_subject_ref,
        stage_subject_digest=state.state_digest,
        baseline_level=level,
        component_proposal_ref=ProjectRecordRef(
            project_id=run.project_id,
            relative_path=f"{record_prefix}/component-proposal.json",
            sha256="5" * 64,
        ),
        component_proposal_digest="5" * 64,
        component_index_ref=ProjectRecordRef(
            project_id=run.project_id,
            relative_path=f"{record_prefix}/component-index.json",
            sha256="6" * 64,
        ),
        component_index_digest="6" * 64,
        entries=(
            StageSubjectInventoryEntry(
                component_id="stage-root",
                identity_ref="design-component:stage-root",
                parent_component_id=None,
                semantic_kind="stage-root",
                component_digest=state.state_digest,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=role_obligations,
            ),
        ),
    )
    sources = StageBaselineSourceSet()
    coverage = compile_stage_baseline_coverage(
        profile,
        level=level,
        sources=sources,
        subject_digest=state.state_digest,
        subject_inventory=inventory,
        check_receipts=(closure_check,),
    )
    return SearchGovernanceEvidence(
        profile_record_ref=profile_record_ref,
        profile=profile,
        closure_checks=(retained_check,),
        closure_record_ref=closure_record_ref,
        closure=closure,
        convergence_record_ref=convergence_record_ref,
        convergence=convergence,
        baseline_sources_record_ref=_branch_record(
            state.branch,
            "stage-baseline-sources",
            payload=sources.to_dict(),
        ),
        baseline_sources=sources,
        stage_subject_inventory_record_ref=_branch_record(
            state.branch,
            "stage-subject-inventory",
            payload=inventory.to_dict(),
        ),
        stage_subject_inventory=inventory,
        baseline_coverage_record_ref=_branch_record(
            state.branch,
            "stage-baseline-coverage",
            payload=coverage.to_dict(),
        ),
        baseline_coverage=coverage,
    )


def _replace_governance(
    governance: SearchGovernanceEvidence,
    **changes: object,
) -> SearchGovernanceEvidence:
    if not governance.is_legacy_read_only:
        return replace(governance, **changes)
    payload = governance.to_dict()
    serializers = {
        "profile": lambda value: value.to_dict(),
        "closure_checks": lambda values: [
            {
                "record_ref": _record_payload(item.record_ref),
                "receipt": item.receipt.to_dict(),
            }
            for item in values
        ],
        "closure": lambda value: value.to_dict(),
        "convergence": lambda value: value.to_dict(),
    }
    for field_name, value in changes.items():
        payload[field_name] = serializers[field_name](value)
    return SearchGovernanceEvidence.from_dict(payload)


def _hard_check(
    state: OperationalMarkovState,
    revision: BranchRevisionRef,
    branch_id: str,
    *,
    status: CheckStatus = CheckStatus.PASS,
    covered_refs: tuple[str, ...] = HARD_REFS,
) -> RetainedCheckReceipt:
    if status is CheckStatus.FAIL:
        findings = (
            CheckFinding(
                code="structural_failure",
                severity=FindingSeverity.ERROR,
                message="The structural hard constraint failed.",
                subject_refs=("constraint:structure",),
            ),
        )
    elif status is CheckStatus.UNKNOWN:
        findings = (
            CheckFinding(
                code="fire_unknown",
                severity=FindingSeverity.UNKNOWN,
                message="Fire compliance remains unknown.",
                subject_refs=("constraint:fire",),
            ),
        )
    else:
        findings = ()
    receipt = CheckReceiptEnvelope(
        check_id=f"hard-{branch_id}",
        checker_id="archflow.hard-constraints",
        checker_version="1.0.0",
        branch=state.branch,
        scope_digest=state.state_digest,
        subject_refs=HARD_REFS,
        subject_digest=revision.revision_digest,
        status=status,
        findings=findings,
        coverage_denominator=HARD_REFS,
        covered_refs=covered_refs,
    )
    return RetainedCheckReceipt(
        record_ref=_record(
            state.branch.run,
            f"hard-check-{branch_id}",
            "5" if branch_id == "branch-a" else "6",
        ),
        receipt=receipt,
    )


def _candidate_receipt(
    snapshot: PersistedDesignPortfolio,
    state: OperationalMarkovState,
    branch_id: str,
    *,
    hard_status: CheckStatus = CheckStatus.PASS,
    covered_refs: tuple[str, ...] = HARD_REFS,
    source_ref: str | None = None,
) -> BoundCandidateEvaluatorReceipt:
    revision = snapshot.portfolio.branch(branch_id).head.ref
    evaluation_record = _record(
        state.branch.run,
        f"evaluation-{branch_id}",
        "7" if branch_id == "branch-a" else "8",
    )
    failure_refs = (
        ("constraint:structure",)
        if hard_status is CheckStatus.FAIL
        else ()
    )
    evaluation = SearchCandidateEvaluation(
        candidate_ref=portfolio_candidate_ref(
            snapshot.portfolio.portfolio_id,
            revision,
        ),
        candidate_digest=revision.revision_digest,
        evaluation_ref=exact_record_ref(evaluation_record),
        hard_failure_refs=failure_refs,
        objectives=(
            SearchObjectiveEstimate(
                objective_ref="objective:embodied-carbon",
                direction=ObjectiveDirection.MINIMIZE,
                mean=8.0 if branch_id == "branch-a" else 10.0,
                variance=0.25,
                sample_count=4,
                source_ref=source_ref
                or exact_record_ref(evaluation_record),
            ),
        ),
    )
    return BoundCandidateEvaluatorReceipt(
        revision=revision,
        evaluation_record_ref=evaluation_record,
        evaluation=evaluation,
        hard_check=_hard_check(
            state,
            revision,
            branch_id,
            status=hard_status,
            covered_refs=covered_refs,
        ),
    )


def _adopted_evidence(
    snapshot: PersistedDesignPortfolio,
    state: OperationalMarkovState,
) -> AdoptedApplicableSearchEvidence:
    statement = "The adopted precedent supports the current core decision."
    fact = PrecedentFact(
        fact_id="core-precedent",
        statement=statement,
        quote=statement,
        quote_start=0,
        quote_end=len(statement),
        snapshot_ref="project-record:retained-raw-snapshot",
        snapshot_text_sha256=hashlib.sha256(
            statement.encode("utf-8")
        ).hexdigest(),
        annotator="model:test",
        annotator_is_harness=False,
        topic=ConstructabilityTopic.SUPPORT,
        strength=PolicyConstraintStrength.SOFT,
        decision_refs=("decision:core",),
    )
    adoption = BranchPrecedentAdoption(
        query_id="core-query",
        query_digest="a" * 64,
        scope_digest="b" * 64,
        branch_id=state.branch.branch_id,
        branch_revision_digest=(
            snapshot.portfolio.branch(state.branch.branch_id)
            .head.revision_digest
        ),
        adoption=PrecedentAdoption(
            adoption_id="core-adoption",
            authority_id="human-owner",
            adopted_at="2026-08-30T00:00:00Z",
            facts=(fact,),
        ),
    )
    adoption_ref = (
        f"branch-precedent-adoption:{adoption.adoption_id}:"
        f"{canonical_digest(adoption.to_dict())}"
    )
    fact_ref = (
        f"precedent-fact:{fact.fact_id}:"
        f"{canonical_digest(fact.to_dict())}"
    )
    claim = EvidenceClaimBinding(
        binding_id="core-adopted-claim",
        branch=state.branch,
        scope_digest=adoption.scope_digest,
        obligation_id="core-evidence",
        target_ref="decision:core",
        fact_ref=fact_ref,
        source_ref=adoption_ref,
        source_family_ref="source-family:precedent",
        claim_key="claim:core-precedent",
        position_key="position:core",
        authority_ref="authority:human-owner",
    )
    applicability = ClaimApplicability.from_claim(
        claim,
        applicability_id="core-search-applicability",
        target_kind=ApplicabilityTargetKind.DECISION,
        target_ref="decision:core",
        disposition=ApplicabilityDisposition.APPLICABLE,
        allowed_uses=(AllowedClaimUse.TOPOLOGY,),
        authority_refs=("authority:human-owner",),
        source_refs=(adoption_ref,),
        rationale="The adopted fact applies to this exact search decision.",
        invalidates_on=("change:branch-revision",),
    )
    run = state.branch.run
    return AdoptedApplicableSearchEvidence(
        claim_record_ref=_record(run, "claim", "a"),
        claim=claim,
        adoption_record_ref=_record(run, "adoption", "b"),
        adoption=adoption,
        applicability_record_ref=_record(run, "applicability", "c"),
        applicability=applicability,
    )


def _context(
    *,
    allowed_actions: tuple[SearchAction, ...] | None = None,
    budget: SearchBudget | None = None,
    closure_passed: bool = True,
    convergence_ready: bool = True,
    hard_a: CheckStatus = CheckStatus.PASS,
    hard_b: CheckStatus = CheckStatus.PASS,
    covered_a: tuple[str, ...] = HARD_REFS,
    phase: str = DesignPhase.PROGRAMMING.value,
    current_baseline: bool = True,
) -> tuple[
    HierarchicalSearchCompileInput,
    OperationalMarkovState,
]:
    source_portfolio = _portfolio()
    parent, state = _states(source_portfolio.run, phase=phase)
    snapshot = _snapshot(source_portfolio, state)
    governance = _stage_governance(
        parent,
        state,
        closure_passed=closure_passed,
        convergence_ready=convergence_ready,
        current_baseline=current_baseline,
    )
    context = HierarchicalSearchCompileInput(
        request_id="hierarchical-request-001",
        policy=_policy(),
        stage_id="stage-3",
        state=state,
        decision_space_id="stage-3-hierarchical-space",
        decision_space_kind=DecisionSpaceKind.CONTINUOUS,
        decision_refs=("decision:core", "decision:typology"),
        objective_refs=("objective:embodied-carbon",),
        hard_constraint_refs=HARD_REFS,
        portfolio=snapshot,
        expected_portfolio_digest=snapshot.portfolio.portfolio_digest,
        evaluator_receipts=(
            _candidate_receipt(
                snapshot,
                state,
                "branch-a",
                hard_status=hard_a,
                covered_refs=covered_a,
            ),
            _candidate_receipt(
                snapshot,
                state,
                "branch-b",
                hard_status=hard_b,
            ),
        ),
        evidence=(_adopted_evidence(snapshot, state),),
        governance=governance,
        budget=budget
        or SearchBudget(
            evaluation_units=2,
            compute_millis=1_000,
            model_tokens=200,
        ),
        allowed_actions=allowed_actions
        or (
            SearchAction.DEEPEN,
            SearchAction.HOLD,
            SearchAction.PRUNE,
            SearchAction.REQUEST_COMMIT,
            SearchAction.REQUEST_REOPEN,
            SearchAction.STOP,
        ),
        reopen_envelope=SearchReopenEnvelope(
            ("decision:typology",)
        ),
        governance_archive=_governance_archive(
            governance,
            state.branch,
        ),
    )
    return context, parent


def _directive(
    request: SearchPolicyRequest,
    action: SearchAction,
    *,
    target_refs: tuple[str, ...] | None = None,
    allocation_units: int = 1,
) -> SearchDirective:
    if target_refs is None:
        if action is SearchAction.REQUEST_REOPEN:
            target_refs = ("decision:typology",)
        elif action in {SearchAction.HOLD, SearchAction.STOP}:
            target_refs = ()
        else:
            target_refs = (request.decision_space.candidate_refs[0],)
    allocations = (
        (
            SearchBudgetAllocation(
                target_ref=target_refs[0],
                evaluation_units=allocation_units,
                compute_millis=100,
                model_tokens=50,
            ),
        )
        if action is SearchAction.DEEPEN
        else ()
    )
    return SearchDirective(
        directive_id="hierarchical-directive-001",
        request_digest=request.request_digest,
        policy_descriptor_digest=request.policy.descriptor_digest,
        branch=request.branch,
        stage_id=request.stage_id,
        state_digest=request.state_digest,
        action=action,
        target_refs=target_refs,
        allocations=allocations,
        evidence_refs=(request.evaluations[0].evaluation_ref,),
        reason_codes=("policy_proposal",),
    )


class _Policy:
    def __init__(
        self,
        descriptor: SearchPolicyDescriptor,
        action: SearchAction,
        *,
        allocation_units: int = 1,
    ) -> None:
        self._descriptor = descriptor
        self.action = action
        self.allocation_units = allocation_units

    @property
    def descriptor(self) -> SearchPolicyDescriptor:
        return self._descriptor

    async def decide(self, request: SearchPolicyRequest) -> SearchDirective:
        return _directive(
            request,
            self.action,
            allocation_units=self.allocation_units,
        )


class HierarchicalSearchRequestCompilerTests(unittest.TestCase):
    def test_current_governance_replays_exact_branch_json_stage_baseline(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        governance = context.governance

        request = compile_search_policy_request(context)

        self.assertFalse(governance.is_legacy_read_only)
        self.assertIs(
            governance.baseline_coverage.status,
            StageBaselineStatus.SATISFIED,
        )
        self.assertIn(
            f"/branches/{context.state.branch.branch_id}/records/",
            governance.baseline_sources_record_ref.relative_path,
        )
        self.assertIn(
            exact_record_ref(governance.baseline_sources_record_ref),
            request.evidence_refs,
        )
        self.assertIn(
            exact_record_ref(governance.stage_subject_inventory_record_ref),
            request.evidence_refs,
        )
        self.assertIn(
            exact_record_ref(governance.baseline_coverage_record_ref),
            request.evidence_refs,
        )
        governance_refs = (
            governance.profile_record_ref,
            *(item.record_ref for item in governance.closure_checks),
            governance.closure_record_ref,
            governance.convergence_record_ref,
            governance.baseline_sources_record_ref,
            governance.stage_subject_inventory_record_ref,
            governance.baseline_coverage_record_ref,
        )
        expected_prefix = (
            f"runs/{context.state.branch.run.run_id}/branches/"
            f"{context.state.branch.branch_id}/records/"
        )
        for ref in governance_refs:
            assert isinstance(ref, ProjectRecordRef)
            self.assertTrue(ref.relative_path.startswith(expected_prefix))
            self.assertEqual(ref.media_type, "application/json")
            self.assertIn(exact_record_ref(ref), request.evidence_refs)
        self.assertEqual(
            SearchGovernanceEvidence.from_dict(governance.to_dict()),
            governance,
        )

    def test_current_governance_rejects_run_level_stage_records(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        governance = context.governance
        fields = (
            "profile_record_ref",
            "closure_record_ref",
            "convergence_record_ref",
            "baseline_sources_record_ref",
            "stage_subject_inventory_record_ref",
            "baseline_coverage_record_ref",
        )
        for index, field in enumerate(fields):
            with self.subTest(field=field), self.assertRaisesRegex(
                HierarchicalSearchProposalError,
                "requested branch",
            ):
                compile_search_policy_request(
                    replace(
                        context,
                        governance=replace(
                            governance,
                            **{
                                field: _record(
                                    context.state.branch.run,
                                    field,
                                    str(index + 1),
                                )
                            },
                        ),
                    )
                )

        retained = governance.closure_checks[0]
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "requested branch",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=replace(
                        governance,
                        closure_checks=(
                            replace(
                                retained,
                                record_ref=_record(
                                    context.state.branch.run,
                                    "closure-check",
                                    "7",
                                ),
                            ),
                        ),
                    ),
                )
            )

    def test_current_governance_rejects_cross_branch_and_cross_run_records(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        governance = context.governance
        original = governance.profile_record_ref
        other_branches = (
            replace(context.state.branch, branch_id="branch-b"),
            BranchRef(
                run=replace(
                    context.state.branch.run,
                    run_id="other-run",
                ),
                branch_id=context.state.branch.branch_id,
                epoch=context.state.branch.epoch,
            ),
        )
        for branch in other_branches:
            crossed = replace(
                original,
                relative_path=(
                    f"runs/{branch.run.run_id}/branches/{branch.branch_id}/"
                    "records/stage-profile.json"
                ),
            )
            with self.subTest(branch=branch), self.assertRaisesRegex(
                HierarchicalSearchProposalError,
                "requested branch",
            ):
                compile_search_policy_request(
                    replace(
                        context,
                        governance=replace(
                            governance,
                            profile_record_ref=crossed,
                        ),
                    )
                )

    def test_current_governance_rejects_non_json_and_stale_epoch_scope(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        governance = context.governance
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "not a JSON record",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=replace(
                        governance,
                        profile_record_ref=replace(
                            governance.profile_record_ref,
                            media_type="application/octet-stream",
                        ),
                    ),
                )
            )

        stale_branch = replace(
            context.state.branch,
            epoch=context.state.branch.epoch + 1,
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "profile crossed branch, epoch, stage, or state",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=replace(
                        governance,
                        profile=replace(
                            governance.profile,
                            branch=stale_branch,
                        ),
                    ),
                )
            )

    def test_current_governance_rejects_same_path_fake_record_digests(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        governance = context.governance
        fields = (
            "profile_record_ref",
            "closure_record_ref",
            "convergence_record_ref",
            "baseline_sources_record_ref",
            "stage_subject_inventory_record_ref",
            "baseline_coverage_record_ref",
        )
        for field in fields:
            with self.subTest(field=field):
                ref = getattr(governance, field)
                assert isinstance(ref, ProjectRecordRef)
                forged = replace(
                    governance,
                    **{field: replace(ref, sha256="f" * 64)},
                )
                with self.assertRaisesRegex(
                    HierarchicalSearchProposalError,
                    "did not replay from exact P036 records",
                ):
                    compile_search_policy_request(
                        replace(context, governance=forged)
                    )

        retained = governance.closure_checks[0]
        forged_check = replace(
            retained,
            record_ref=replace(
                retained.record_ref,
                sha256="f" * 64,
            ),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "did not replay from exact P036 records",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=replace(
                        governance,
                        closure_checks=(forged_check,),
                    ),
                )
            )

    def test_structural_fake_cannot_replace_durable_governance_archive(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )

        class NoIOReplay:
            def replay_exact_branch_json_records(self, records):
                return tuple(item[0] for item in records)

            def replay_accepted_relation_predecessors(self, sources):
                return tuple(
                    {
                        ref
                        for source in sources.relation_inheritance
                        for ref in (
                            source.predecessor.predecessor_checkpoint_ref,
                            source.predecessor.stage_exit_anchor_ref,
                            source.predecessor.baseline_sources_ref,
                            source.predecessor.baseline_coverage_ref,
                        )
                    }
                )

        with self.assertRaisesRegex(
            TypeError,
            "concrete durable controller archive adapter",
        ):
            replace(context, governance_archive=NoIOReplay())

    def test_concrete_archive_recomputes_p036_json_byte_digest(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        forged = replace(
            context.governance,
            profile_record_ref=replace(
                context.governance.profile_record_ref,
                sha256="f" * 64,
            ),
        )
        caller_built_archive = _governance_archive(
            forged,
            context.state.branch,
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "did not replay from exact P036 records",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=forged,
                    governance_archive=caller_built_archive,
                )
            )

    def test_current_governance_rejects_sparse_stage_profile(self):
        context, _ = _context(
            phase=DesignPhase.SCHEMATIC_DESIGN.value,
            current_baseline=True,
        )
        self.assertIs(
            context.governance.baseline_coverage.status,
            StageBaselineStatus.OPEN,
        )

        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "sparse, open",
        ):
            compile_search_policy_request(context)

    def test_current_governance_rejects_tampered_baseline_receipt(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        governance = context.governance
        tampered = replace(
            governance.baseline_coverage,
            profile_id="tampered-profile",
        )

        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "baseline evidence crossed",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=replace(
                        governance,
                        baseline_coverage=tampered,
                    ),
                )
            )

    def test_current_governance_requires_all_exact_baseline_inputs(self):
        context, _ = _context(
            phase=DesignPhase.PROGRAMMING.value,
            current_baseline=True,
        )
        for field_name in (
            "baseline_sources",
            "stage_subject_inventory",
            "baseline_coverage",
        ):
            with self.subTest(field=field_name), self.assertRaises(TypeError):
                replace(
                    context.governance,
                    **{field_name: None},
                )

    def test_legacy_governance_is_explicit_read_only_replay(self):
        context, _ = _context(current_baseline=False)
        governance = context.governance

        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "legacy governance evidence is read-only",
        ):
            compile_search_policy_request(context)

        self.assertTrue(governance.is_legacy_read_only)
        self.assertEqual(
            governance.to_dict()["schema"],
            SearchGovernanceEvidence.LEGACY_SCHEMA,
        )
        self.assertEqual(
            SearchGovernanceEvidence.from_dict(governance.to_dict()),
            governance,
        )
        with self.assertRaises(TypeError):
            replace(governance)

    def test_request_identity_is_derived_from_exact_operational_state(self):
        context, _ = _context()

        request = compile_search_policy_request(context)

        self.assertEqual(request.branch, context.state.branch)
        self.assertEqual(request.state_digest, context.state.state_digest)
        self.assertEqual(
            request.portfolio_ref,
            exact_record_ref(context.portfolio.record_ref),
        )
        self.assertEqual(
            request.portfolio_digest,
            context.portfolio.portfolio.portfolio_digest,
        )
        self.assertNotIn(
            "state_digest",
            HierarchicalSearchCompileInput.__dataclass_fields__,
        )
        self.assertNotIn(
            "branch",
            HierarchicalSearchCompileInput.__dataclass_fields__,
        )
        self.assertEqual(
            SearchPolicyRequest.from_dict(request.to_dict()),
            request,
        )

    def test_only_adopted_applicable_unit_enters_policy_evidence(self):
        context, _ = _context()
        unit = context.evidence[0]

        request = compile_search_policy_request(context)

        self.assertIn(unit.ref, request.evidence_refs)
        self.assertNotIn(unit.claim.source_ref, request.evidence_refs)
        self.assertNotIn(unit.claim.fact_ref, request.evidence_refs)
        self.assertNotIn(
            unit.adoption.adoption.facts[0].snapshot_ref,
            request.evidence_refs,
        )

        not_applicable = replace(
            unit.applicability,
            disposition=ApplicabilityDisposition.NOT_APPLICABLE,
            allowed_uses=(),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "non-applicable",
        ):
            replace(unit, applicability=not_applicable)

        raw_claim = replace(
            unit.claim,
            source_ref=unit.adoption.adoption.facts[0].snapshot_ref,
        )
        raw_applicability = ClaimApplicability.from_claim(
            raw_claim,
            applicability_id="raw-applicability",
            target_kind=ApplicabilityTargetKind.DECISION,
            target_ref="decision:core",
            disposition=ApplicabilityDisposition.APPLICABLE,
            allowed_uses=(AllowedClaimUse.TOPOLOGY,),
            authority_refs=("authority:human-owner",),
            source_refs=(raw_claim.source_ref,),
            rationale="This raw source must still be rejected.",
            invalidates_on=("change:branch-revision",),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "not sourced",
        ):
            replace(
                unit,
                claim=raw_claim,
                applicability=raw_applicability,
            )

    def test_closure_replays_and_convergence_matches_exact_child_state(self):
        context, _ = _context()
        request = compile_search_policy_request(context)
        self.assertIn(
            exact_record_ref(context.governance.convergence_record_ref),
            request.evidence_refs,
        )

        closure_check = context.governance.closure_checks[0]
        stale_check = replace(
            closure_check,
            receipt=replace(
                closure_check.receipt,
                subject_digest=OTHER_DIGEST,
            ),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "closure check crossed",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=_replace_governance(
                        context.governance,
                        closure_checks=(stale_check,),
                    ),
                )
            )

        stale_profile = replace(
            context.governance.profile,
            predecessor_state_digest=OTHER_DIGEST,
        )
        stale_closure = compile_composite_stage_closure(
            stale_profile,
            subject_digest=context.state.state_digest,
            check_receipts=tuple(
                item.receipt
                for item in context.governance.closure_checks
            ),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "profile crossed",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=_replace_governance(
                        context.governance,
                        profile=stale_profile,
                        closure=stale_closure,
                    ),
                )
            )

        stale_convergence = replace(
            context.governance.convergence,
            child_state_digest=OTHER_DIGEST,
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "convergence crossed",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    governance=_replace_governance(
                        context.governance,
                        convergence=stale_convergence,
                    ),
                )
            )

    def test_exact_run_record_and_state_identity_tampering_fails_closed(self):
        context, _ = _context()
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "portfolio digest drifted",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    expected_portfolio_digest=OTHER_DIGEST,
                )
            )

        wrong_record = replace(
            context.portfolio.record_ref,
            relative_path="runs/other-run/records/portfolio.json",
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "requested run",
        ):
            compile_search_policy_request(
                replace(
                    context,
                    portfolio=replace(
                        context.portfolio,
                        record_ref=wrong_record,
                    ),
                )
            )

        run = context.state.branch.run
        state_branches = (
            BranchRef(
                RunRef(
                    "other-project",
                    run.run_id,
                    ProjectVersionRef(
                        "other-project",
                        run.base.version,
                        run.base.require_digest(),
                    ),
                ),
                context.state.branch.branch_id,
                context.state.branch.epoch,
            ),
            replace(context.state.branch, branch_id="branch-b"),
            replace(
                context.state.branch,
                epoch=context.state.branch.epoch + 1,
            ),
            BranchRef(
                replace(run, run_id="other-run"),
                context.state.branch.branch_id,
                context.state.branch.epoch,
            ),
            BranchRef(
                replace(
                    run,
                    base=ProjectVersionRef(
                        run.project_id,
                        run.base.version + 1,
                        OTHER_DIGEST,
                    ),
                ),
                context.state.branch.branch_id,
                context.state.branch.epoch,
            ),
        )
        for branch in state_branches:
            with self.subTest(branch=branch), self.assertRaises(
                HierarchicalSearchProposalError
            ):
                compile_search_policy_request(
                    replace(
                        context,
                        state=replace(context.state, branch=branch),
                    )
                )

    def test_hard_constraints_require_complete_typed_denominator_coverage(self):
        context, _ = _context(
            hard_a=CheckStatus.UNKNOWN,
            covered_a=("constraint:fire",),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "denominator coverage",
        ):
            compile_search_policy_request(context)

        valid, _ = _context()
        request = compile_search_policy_request(valid)
        self.assertEqual(
            valid.evaluator_receipts[0]
            .hard_check.receipt.coverage_denominator,
            HARD_REFS,
        )
        self.assertEqual(
            valid.evaluator_receipts[0].hard_check.receipt.covered_refs,
            HARD_REFS,
        )
        self.assertEqual(request.evaluations[0].hard_failure_refs, ())

    def test_typed_closure_and_convergence_dominate_commit_permission(self):
        for field, value in (
            ("closure_passed", False),
            ("convergence_ready", False),
        ):
            context, _ = _context(**{field: value})
            with self.subTest(field=field), self.assertRaisesRegex(
                HierarchicalSearchProposalError,
                "closure and convergence",
            ):
                compile_search_policy_request(context)

        open_context, _ = _context(
            closure_passed=False,
            allowed_actions=(SearchAction.DEEPEN, SearchAction.HOLD),
        )
        compile_search_policy_request(open_context)

    def test_budget_action_mismatch_and_invalid_numeric_budget_fail(self):
        context, _ = _context(
            allowed_actions=(SearchAction.DEEPEN,),
            budget=SearchBudget(0, 0, 0),
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "budget and allowed",
        ):
            compile_search_policy_request(context)

        for value, field in (
            (-1, "evaluation_units"),
            (math.nan, "compute_millis"),
            (math.inf, "model_tokens"),
        ):
            values = {
                "evaluation_units": 0,
                "compute_millis": 0,
                "model_tokens": 0,
            }
            values[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                SearchBudget(**values)


class HierarchicalSearchProposalCompilerTests(unittest.TestCase):
    def test_commit_remains_auditable_detached_request_only(self):
        context, _ = _context()
        compiler = HierarchicalSearchProposalCompiler(
            SearchPolicyRegistry(
                (
                    _Policy(
                        context.policy,
                        SearchAction.REQUEST_COMMIT,
                    ),
                )
            )
        )

        proposal = asyncio.run(compiler.propose(context))

        self.assertEqual(proposal.commit_request_refs, proposal.target_refs)
        self.assertEqual(proposal.reopen_request_refs, ())
        self.assertTrue(proposal.proposal_only)
        self.assertIsNone(proposal.decision_operator)
        self.assertIsNone(proposal.stage_acceptance)
        self.assertIsNone(proposal.canonical_write)
        payload = proposal.to_dict()
        self.assertIs(payload["controller_flow_attached"], False)
        self.assertIs(payload["human_authority_required"], True)
        for field in (
            "decision_operator",
            "stage_acceptance",
            "commit_transition",
            "reopen_transition",
            "canonical_write",
            "head_write",
            "human_decision",
        ):
            self.assertIsNone(payload[field])
        for field in (
            "design_authority",
            "stage_acceptance_authority",
            "persistence_authority",
            "canonical_write_authority",
            "hard_gate_override",
            "closure_override",
            "convergence_override",
        ):
            self.assertIs(payload[field], False)
        self.assertEqual(payload["policy"]["policy_version"], "2.4.1")
        self.assertEqual(
            HierarchicalSearchProposal.from_dict(payload),
            proposal,
        )

        tampered = dict(payload)
        tampered["head_write"] = {"ref": "HEAD"}
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "forbidden authority",
        ):
            HierarchicalSearchProposal.from_dict(tampered)

    def test_unknown_hard_check_cannot_become_commit_request(self):
        context, _ = _context(hard_a=CheckStatus.UNKNOWN)
        request = compile_search_policy_request(context)
        directive = _directive(request, SearchAction.REQUEST_COMMIT)

        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "hard-check coverage",
        ):
            compile_search_proposal(
                request,
                directive,
                context=context,
            )

    def test_failed_hard_check_and_over_budget_directive_are_rejected(self):
        failed_context, _ = _context(hard_a=CheckStatus.FAIL)
        with self.assertRaisesRegex(ValueError, "hard-feasible"):
            asyncio.run(
                HierarchicalSearchProposalCompiler(
                    SearchPolicyRegistry(
                        (
                            _Policy(
                                failed_context.policy,
                                SearchAction.REQUEST_COMMIT,
                            ),
                        )
                    )
                ).propose(failed_context)
            )

        context, _ = _context()
        with self.assertRaisesRegex(ValueError, "exceeds"):
            asyncio.run(
                HierarchicalSearchProposalCompiler(
                    SearchPolicyRegistry(
                        (
                            _Policy(
                                context.policy,
                                SearchAction.DEEPEN,
                                allocation_units=3,
                            ),
                        )
                    )
                ).propose(context)
            )

    def test_reopen_is_only_a_request_and_context_drift_is_rejected(self):
        context, _ = _context()
        proposal = asyncio.run(
            HierarchicalSearchProposalCompiler(
                SearchPolicyRegistry(
                    (
                        _Policy(
                            context.policy,
                            SearchAction.REQUEST_REOPEN,
                        ),
                    )
                )
            ).propose(context)
        )
        self.assertEqual(
            proposal.reopen_request_refs,
            ("decision:typology",),
        )
        self.assertIsNone(proposal.to_dict()["reopen_transition"])

        request = compile_search_policy_request(context)
        directive = _directive(request, SearchAction.DEEPEN)
        changed_portfolio = replace(
            context.portfolio.portfolio,
            source_option_set_digest=OTHER_DIGEST,
        )
        changed_snapshot = PersistedDesignPortfolio(
            record_ref=context.portfolio.record_ref,
            portfolio=changed_portfolio,
        )
        with self.assertRaisesRegex(
            HierarchicalSearchProposalError,
            "context changed",
        ):
            compile_search_proposal(
                request,
                directive,
                context=replace(
                    context,
                    portfolio=changed_snapshot,
                    expected_portfolio_digest=(
                        changed_portfolio.portfolio_digest
                    ),
                ),
            )

    def test_duplicate_target_and_unknown_action_fail_before_proposal(self):
        context, _ = _context()
        request = compile_search_policy_request(context)
        target = request.decision_space.candidate_refs[0]
        with self.assertRaises(ValueError):
            _directive(
                request,
                SearchAction.PRUNE,
                target_refs=(target, target),
            )
        with self.assertRaises(TypeError):
            replace(
                _directive(request, SearchAction.PRUNE),
                action="commit",
            )


if __name__ == "__main__":
    unittest.main()
