"""Exact, authority-free proposal compiler for hierarchical design search.

This module does not attach search policy output to the production design
controller. It compiles one exact :class:`SearchPolicyRequest`, dispatches an
explicitly registered policy, and returns a proposal-only outcome. It owns no
path discovery, persistence adapter, ``DecisionOperator``, stage acceptance,
commit, reopen, or ``HEAD`` authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_identifiers,
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
)
from archflow.control.baseline import (
    StageBaselineCoverageReceipt,
    StageBaselineSourceSet,
    StageBaselineStatus,
    baseline_level_for_design_phase,
    compile_stage_baseline_coverage,
)
from archflow.control.convergence import (
    StageConvergenceOutcome,
    StageConvergencePotential,
    StageConvergenceReceipt,
    StageTransitionKind,
)
from archflow.control.requirements import StageRequirementProfile
from archflow.control.search_policy import (
    DecisionSpaceDescriptor,
    DecisionSpaceKind,
    SearchAction,
    SearchBudget,
    SearchBudgetAllocation,
    SearchCandidateEvaluation,
    SearchDirective,
    SearchPolicyDescriptor,
    SearchPolicyError,
    SearchPolicyRequest,
    validate_search_directive,
)
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.control.stage_subjects import StageSubjectInventory
from archflow.evidence.applicability import (
    ApplicabilityDisposition,
    ClaimApplicability,
)
from archflow.evidence.claims import EvidenceClaimBinding
from archflow.project.refs import BranchRef, ProjectRecordRef, RunRef
from archflow.research.adoption import PrecedentFact
from archflow.research.branch import BranchPrecedentAdoption
from archflow.runtime.branch_portfolio import PersistedDesignPortfolio
from archflow.runtime.search_policy import SearchPolicyRegistry
from archflow.state.design_portfolio import (
    BranchLifecycle,
    BranchRevisionRef,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.operational_state import OperationalMarkovState
from archflow.validation.contracts import (
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)

if TYPE_CHECKING:
    from archflow.runtime.design_controller import (
        ProjectControllerArchiveAdapter,
    )


_WORK_ACTIONS = frozenset(
    {
        SearchAction.RESEARCH,
        SearchAction.EXPAND,
        SearchAction.DEEPEN,
        SearchAction.RESAMPLE,
    }
)
_OUTCOME_AUTHORITY_FIELDS = {
    "proposal_only": True,
    "controller_flow_attached": False,
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
    "decision_operator": None,
    "stage_acceptance": None,
    "commit_transition": None,
    "reopen_transition": None,
    "canonical_write": None,
    "head_write": None,
    "human_decision": None,
    "human_authority_required": True,
    "hard_gate_override": False,
    "closure_override": False,
    "convergence_override": False,
}


class HierarchicalSearchProposalError(SearchPolicyError):
    """A proposal input is stale, misbound, or exceeds policy authority."""


def exact_record_ref(ref: ProjectRecordRef) -> str:
    """Return a logical record ref that retains its immutable byte digest."""

    if not isinstance(ref, ProjectRecordRef):
        raise TypeError("ref must be ProjectRecordRef")
    return logical_ref(
        f"{ref.uri}#sha256={ref.sha256}",
        "exact project record ref",
    )


def _record_ref_dict(ref: ProjectRecordRef) -> dict[str, object]:
    if not isinstance(ref, ProjectRecordRef):
        raise TypeError("ref must be ProjectRecordRef")
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_ref_from_dict(value: object, field_name: str) -> ProjectRecordRef:
    payload = exact_mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field_name,
    )
    return ProjectRecordRef(
        project_id=payload["project_id"],
        relative_path=payload["relative_path"],
        sha256=payload["sha256"],
        media_type=payload["media_type"],
    )


def _convergence_potential_from_dict(
    value: object,
) -> StageConvergencePotential:
    fields = (
        "hard_gate_failure_refs",
        "conflict_refs",
        "tolerance_failure_refs",
        "missing_mandatory_obligation_refs",
        "blocked_mandatory_obligation_refs",
        "open_mandatory_obligation_refs",
        "invalidated_refs",
        "revalidation_refs",
    )
    payload = exact_mapping(
        value,
        {"schema", *fields, "vector"},
        "stage convergence potential",
    )
    if payload["schema"] != StageConvergencePotential.SCHEMA:
        raise HierarchicalSearchProposalError(
            "unsupported stage convergence potential schema"
        )
    if any(not isinstance(payload[name], list) for name in (*fields, "vector")):
        raise TypeError("stage convergence potential refs must be lists")
    result = StageConvergencePotential(
        **{name: tuple(payload[name]) for name in fields}
    )
    if result.to_dict() != payload:
        raise HierarchicalSearchProposalError(
            "stage convergence potential identity changed"
        )
    return result


def _convergence_receipt_from_dict(value: object) -> StageConvergenceReceipt:
    payload = exact_mapping(
        value,
        {
            "schema",
            "receipt_id",
            "outcome",
            "request_id",
            "stage",
            "transition_kind",
            "branch",
            "policy_digest",
            "parent_state_digest",
            "child_state_digest",
            "parent_sufficient_digest",
            "child_sufficient_digest",
            "parent_evidence_digest",
            "child_evidence_digest",
            "potential_before",
            "potential_after",
            "protected_refs",
            "changed_protected_refs",
            "mandatory_obligation_ids",
            "added_mandatory_obligation_ids",
            "dependency_closure",
            "authorization_ref",
            "reason_codes",
            "stage_ready",
            "canonical_write_authority",
        },
        "stage convergence receipt",
    )
    if (
        payload["schema"] != StageConvergenceReceipt.SCHEMA
        or payload["canonical_write_authority"] is not False
    ):
        raise HierarchicalSearchProposalError(
            "unsupported or authoritative stage convergence receipt"
        )
    list_fields = (
        "protected_refs",
        "changed_protected_refs",
        "mandatory_obligation_ids",
        "added_mandatory_obligation_ids",
        "dependency_closure",
        "reason_codes",
    )
    if any(not isinstance(payload[name], list) for name in list_fields):
        raise TypeError("stage convergence receipt refs must be lists")
    result = StageConvergenceReceipt(
        receipt_id=payload["receipt_id"],
        outcome=StageConvergenceOutcome(payload["outcome"]),
        request_id=payload["request_id"],
        stage=payload["stage"],
        transition_kind=StageTransitionKind(payload["transition_kind"]),
        branch=branch_ref_from_dict(payload["branch"]),
        policy_digest=payload["policy_digest"],
        parent_state_digest=payload["parent_state_digest"],
        child_state_digest=payload["child_state_digest"],
        parent_sufficient_digest=payload["parent_sufficient_digest"],
        child_sufficient_digest=payload["child_sufficient_digest"],
        parent_evidence_digest=payload["parent_evidence_digest"],
        child_evidence_digest=payload["child_evidence_digest"],
        potential_before=_convergence_potential_from_dict(
            payload["potential_before"]
        ),
        potential_after=_convergence_potential_from_dict(
            payload["potential_after"]
        ),
        protected_refs=tuple(payload["protected_refs"]),
        changed_protected_refs=tuple(payload["changed_protected_refs"]),
        mandatory_obligation_ids=tuple(payload["mandatory_obligation_ids"]),
        added_mandatory_obligation_ids=tuple(
            payload["added_mandatory_obligation_ids"]
        ),
        dependency_closure=tuple(payload["dependency_closure"]),
        authorization_ref=payload["authorization_ref"],
        reason_codes=tuple(payload["reason_codes"]),
    )
    if result.to_dict() != payload:
        raise HierarchicalSearchProposalError(
            "stage convergence receipt identity changed"
        )
    return result


def _require_run_record(
    ref: ProjectRecordRef,
    run: RunRef,
    field: str,
) -> str:
    if not isinstance(ref, ProjectRecordRef):
        raise TypeError(f"{field} must be ProjectRecordRef")
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    prefix = f"runs/{run.run_id}/records/"
    if ref.project_id != run.project_id or not ref.relative_path.startswith(
        prefix
    ):
        raise HierarchicalSearchProposalError(
            f"{field} is not an exact record in the requested run"
        )
    return exact_record_ref(ref)


def _require_branch_record(
    ref: ProjectRecordRef,
    branch: BranchRef,
    field: str,
) -> str:
    if not isinstance(ref, ProjectRecordRef):
        raise TypeError(f"{field} must be ProjectRecordRef")
    if not isinstance(branch, BranchRef):
        raise TypeError("branch must be BranchRef")
    prefix = (
        f"runs/{branch.run.run_id}/branches/"
        f"{branch.branch_id}/records/"
    )
    if (
        ref.project_id != branch.run.project_id
        or not ref.relative_path.startswith(prefix)
    ):
        raise HierarchicalSearchProposalError(
            f"{field} is not an exact record in the requested branch"
        )
    return exact_record_ref(ref)


def portfolio_candidate_ref(
    portfolio_id: str,
    revision: BranchRevisionRef,
) -> str:
    """Return the stable logical ref for one exact portfolio branch head."""

    identifier(portfolio_id, "portfolio_id")
    if not isinstance(revision, BranchRevisionRef):
        raise TypeError("revision must be BranchRevisionRef")
    return (
        f"portfolio:{portfolio_id}/branches/{revision.branch_id}/"
        f"revisions/{revision.revision_id}"
    )


def _precedent_fact_ref(fact: PrecedentFact) -> str:
    if not isinstance(fact, PrecedentFact):
        raise TypeError("fact must be PrecedentFact")
    return (
        f"precedent-fact:{fact.fact_id}:"
        f"{canonical_digest(fact.to_dict())}"
    )


def _branch_adoption_ref(adoption: BranchPrecedentAdoption) -> str:
    if not isinstance(adoption, BranchPrecedentAdoption):
        raise TypeError("adoption must be BranchPrecedentAdoption")
    return (
        f"branch-precedent-adoption:{adoption.adoption_id}:"
        f"{canonical_digest(adoption.to_dict())}"
    )


@dataclass(frozen=True, slots=True)
class RetainedCheckReceipt:
    """One typed check receipt and its exact immutable run record."""

    record_ref: ProjectRecordRef
    receipt: CheckReceiptEnvelope

    def __post_init__(self) -> None:
        if not isinstance(self.record_ref, ProjectRecordRef):
            raise TypeError("record_ref must be ProjectRecordRef")
        if not isinstance(self.receipt, CheckReceiptEnvelope):
            raise TypeError("receipt must be CheckReceiptEnvelope")

    def require_run(self, run: RunRef, field: str) -> str:
        return _require_run_record(self.record_ref, run, field)

    @property
    def ref(self) -> str:
        return exact_record_ref(self.record_ref)


@dataclass(frozen=True, slots=True)
class BoundCandidateEvaluatorReceipt:
    """Candidate statistics with exact revision and hard-check coverage."""

    revision: BranchRevisionRef
    evaluation_record_ref: ProjectRecordRef
    evaluation: SearchCandidateEvaluation
    hard_check: RetainedCheckReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.revision, BranchRevisionRef):
            raise TypeError("revision must be BranchRevisionRef")
        if not isinstance(self.evaluation_record_ref, ProjectRecordRef):
            raise TypeError(
                "evaluation_record_ref must be ProjectRecordRef"
            )
        if not isinstance(self.evaluation, SearchCandidateEvaluation):
            raise TypeError(
                "evaluation must be SearchCandidateEvaluation"
            )
        if not isinstance(self.hard_check, RetainedCheckReceipt):
            raise TypeError("hard_check must be RetainedCheckReceipt")
        if self.evaluation.candidate_digest != self.revision.revision_digest:
            raise HierarchicalSearchProposalError(
                "evaluator candidate digest changed from its exact revision"
            )


@dataclass(frozen=True, slots=True)
class AdoptedApplicableSearchEvidence:
    """One existing adopted claim plus its applicability decision.

    Raw retrieval is intentionally absent. A policy receives only this
    derived unit ref after the typed claim, branch adoption, and applicability
    records have been replayed against each other.
    """

    claim_record_ref: ProjectRecordRef
    claim: EvidenceClaimBinding
    adoption_record_ref: ProjectRecordRef
    adoption: BranchPrecedentAdoption
    applicability_record_ref: ProjectRecordRef
    applicability: ClaimApplicability

    def __post_init__(self) -> None:
        if not isinstance(self.claim, EvidenceClaimBinding):
            raise TypeError("claim must be EvidenceClaimBinding")
        if not isinstance(self.adoption, BranchPrecedentAdoption):
            raise TypeError("adoption must be BranchPrecedentAdoption")
        if not isinstance(self.applicability, ClaimApplicability):
            raise TypeError("applicability must be ClaimApplicability")
        for field in (
            "claim_record_ref",
            "adoption_record_ref",
            "applicability_record_ref",
        ):
            if not isinstance(getattr(self, field), ProjectRecordRef):
                raise TypeError(f"{field} must be ProjectRecordRef")
        self.applicability.require_claim(self.claim)
        if (
            self.applicability.disposition
            is not ApplicabilityDisposition.APPLICABLE
        ):
            raise HierarchicalSearchProposalError(
                "non-applicable evidence cannot enter search"
            )
        if (
            self.adoption.scope_digest != self.claim.scope_digest
            or self.adoption.branch_id != self.claim.branch.branch_id
        ):
            raise HierarchicalSearchProposalError(
                "adoption crossed the exact claim scope or branch"
            )
        adoption_ref = _branch_adoption_ref(self.adoption)
        if self.claim.source_ref != adoption_ref:
            raise HierarchicalSearchProposalError(
                "claim is not sourced from the supplied typed adoption"
            )
        adopted_fact_refs = {
            _precedent_fact_ref(fact)
            for fact in self.adoption.adoption.facts
        }
        if self.claim.fact_ref not in adopted_fact_refs:
            raise HierarchicalSearchProposalError(
                "claim fact is absent from the supplied typed adoption"
            )
        run = self.claim.branch.run
        for field in (
            "claim_record_ref",
            "adoption_record_ref",
            "applicability_record_ref",
        ):
            _require_run_record(getattr(self, field), run, field)

    @property
    def evidence_digest(self) -> str:
        return canonical_digest(
            {
                "claim_record_ref": exact_record_ref(
                    self.claim_record_ref
                ),
                "claim": self.claim.to_dict(),
                "adoption_record_ref": exact_record_ref(
                    self.adoption_record_ref
                ),
                "adoption": self.adoption.to_dict(),
                "applicability_record_ref": exact_record_ref(
                    self.applicability_record_ref
                ),
                "applicability": self.applicability.to_dict(),
            }
        )

    @property
    def ref(self) -> str:
        return f"adopted-applicable-evidence:{self.evidence_digest}"


@dataclass(frozen=True, slots=True)
class SearchGovernanceEvidence:
    """Existing typed closure and convergence evidence; no caller booleans.

    The exact convergence record is the retained upstream authority boundary.
    This proposal compiler binds its child identity and status, but does not
    replay or acquire the controller's transition authority.
    """

    profile_record_ref: ProjectRecordRef
    profile: StageRequirementProfile
    closure_checks: tuple[RetainedCheckReceipt, ...]
    closure_record_ref: ProjectRecordRef
    closure: CompositeStageClosureReceipt
    convergence_record_ref: ProjectRecordRef
    convergence: StageConvergenceReceipt
    baseline_sources_record_ref: ProjectRecordRef | None = None
    baseline_sources: StageBaselineSourceSet | None = None
    stage_subject_inventory_record_ref: ProjectRecordRef | None = None
    stage_subject_inventory: StageSubjectInventory | None = None
    baseline_coverage_record_ref: ProjectRecordRef | None = None
    baseline_coverage: StageBaselineCoverageReceipt | None = None
    _legacy_read_only: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )

    SCHEMA = "SearchGovernanceEvidence@2"
    LEGACY_SCHEMA = "SearchGovernanceEvidence@1"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, StageRequirementProfile):
            raise TypeError("profile must be StageRequirementProfile")
        if not isinstance(self.closure, CompositeStageClosureReceipt):
            raise TypeError(
                "closure must be CompositeStageClosureReceipt"
            )
        if not isinstance(self.convergence, StageConvergenceReceipt):
            raise TypeError("convergence must be StageConvergenceReceipt")
        for field in (
            "profile_record_ref",
            "closure_record_ref",
            "convergence_record_ref",
        ):
            if not isinstance(getattr(self, field), ProjectRecordRef):
                raise TypeError(f"{field} must be ProjectRecordRef")
        if (
            not isinstance(self.closure_checks, tuple)
            or not self.closure_checks
            or any(
                not isinstance(item, RetainedCheckReceipt)
                for item in self.closure_checks
            )
        ):
            raise TypeError(
                "closure_checks must contain RetainedCheckReceipt values"
            )
        current_values = (
            self.baseline_sources_record_ref,
            self.baseline_sources,
            self.stage_subject_inventory_record_ref,
            self.stage_subject_inventory,
            self.baseline_coverage_record_ref,
            self.baseline_coverage,
        )
        if self._legacy_read_only:
            if any(value is not None for value in current_values):
                raise HierarchicalSearchProposalError(
                    "legacy governance evidence cannot carry current baseline fields"
                )
            return
        for field_name in (
            "baseline_sources_record_ref",
            "stage_subject_inventory_record_ref",
            "baseline_coverage_record_ref",
        ):
            if not isinstance(getattr(self, field_name), ProjectRecordRef):
                raise TypeError(
                    f"current governance {field_name} must be ProjectRecordRef"
                )
        if not isinstance(self.baseline_sources, StageBaselineSourceSet):
            raise TypeError(
                "current governance requires exact StageBaselineSourceSet"
            )
        if self.baseline_sources.is_legacy_read_only:
            raise HierarchicalSearchProposalError(
                "current governance cannot author a legacy baseline source set"
            )
        if not isinstance(self.stage_subject_inventory, StageSubjectInventory):
            raise TypeError(
                "current governance requires exact StageSubjectInventory"
            )
        if not isinstance(
            self.baseline_coverage,
            StageBaselineCoverageReceipt,
        ):
            raise TypeError(
                "current governance requires exact StageBaselineCoverageReceipt"
            )
        if self.baseline_coverage.stage_subject_inventory_digest is None:
            raise HierarchicalSearchProposalError(
                "current governance cannot author legacy baseline coverage"
            )

    @property
    def is_legacy_read_only(self) -> bool:
        return self._legacy_read_only

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": (
                self.LEGACY_SCHEMA if self._legacy_read_only else self.SCHEMA
            ),
            "profile_record_ref": _record_ref_dict(self.profile_record_ref),
            "profile": self.profile.to_dict(),
            "closure_checks": [
                {
                    "record_ref": _record_ref_dict(item.record_ref),
                    "receipt": item.receipt.to_dict(),
                }
                for item in self.closure_checks
            ],
            "closure_record_ref": _record_ref_dict(self.closure_record_ref),
            "closure": self.closure.to_dict(),
            "convergence_record_ref": _record_ref_dict(
                self.convergence_record_ref
            ),
            "convergence": self.convergence.to_dict(),
        }
        if not self._legacy_read_only:
            assert isinstance(self.baseline_sources_record_ref, ProjectRecordRef)
            assert isinstance(self.baseline_sources, StageBaselineSourceSet)
            assert isinstance(
                self.stage_subject_inventory_record_ref,
                ProjectRecordRef,
            )
            assert isinstance(
                self.stage_subject_inventory,
                StageSubjectInventory,
            )
            assert isinstance(self.baseline_coverage_record_ref, ProjectRecordRef)
            assert isinstance(
                self.baseline_coverage,
                StageBaselineCoverageReceipt,
            )
            payload.update(
                {
                    "baseline_sources_record_ref": _record_ref_dict(
                        self.baseline_sources_record_ref
                    ),
                    "baseline_sources": self.baseline_sources.to_dict(),
                    "stage_subject_inventory_record_ref": _record_ref_dict(
                        self.stage_subject_inventory_record_ref
                    ),
                    "stage_subject_inventory": (
                        self.stage_subject_inventory.to_dict()
                    ),
                    "baseline_coverage_record_ref": _record_ref_dict(
                        self.baseline_coverage_record_ref
                    ),
                    "baseline_coverage": self.baseline_coverage.to_dict(),
                }
            )
        return payload

    @classmethod
    def from_dict(cls, value: object) -> "SearchGovernanceEvidence":
        if not isinstance(value, dict):
            raise TypeError("search governance evidence must be a mapping")
        schema = value.get("schema")
        common = {
            "schema",
            "profile_record_ref",
            "profile",
            "closure_checks",
            "closure_record_ref",
            "closure",
            "convergence_record_ref",
            "convergence",
        }
        current = {
            "baseline_sources_record_ref",
            "baseline_sources",
            "stage_subject_inventory_record_ref",
            "stage_subject_inventory",
            "baseline_coverage_record_ref",
            "baseline_coverage",
        }
        if schema == cls.SCHEMA:
            expected = common | current
            legacy = False
        elif schema == cls.LEGACY_SCHEMA:
            expected = common
            legacy = True
        else:
            raise HierarchicalSearchProposalError(
                "unsupported search governance evidence schema"
            )
        payload = exact_mapping(value, expected, "search governance evidence")
        if not isinstance(payload["closure_checks"], list):
            raise TypeError("closure_checks must be a list")
        closure_checks: list[RetainedCheckReceipt] = []
        for item in payload["closure_checks"]:
            retained = exact_mapping(
                item,
                {"record_ref", "receipt"},
                "retained closure check",
            )
            closure_checks.append(
                RetainedCheckReceipt(
                    record_ref=_record_ref_from_dict(
                        retained["record_ref"],
                        "retained closure check record_ref",
                    ),
                    receipt=CheckReceiptEnvelope.from_dict(
                        retained["receipt"]
                    ),
                )
            )
        values = {
            "profile_record_ref": _record_ref_from_dict(
                payload["profile_record_ref"],
                "profile_record_ref",
            ),
            "profile": StageRequirementProfile.from_dict(payload["profile"]),
            "closure_checks": tuple(closure_checks),
            "closure_record_ref": _record_ref_from_dict(
                payload["closure_record_ref"],
                "closure_record_ref",
            ),
            "closure": CompositeStageClosureReceipt.from_dict(
                payload["closure"]
            ),
            "convergence_record_ref": _record_ref_from_dict(
                payload["convergence_record_ref"],
                "convergence_record_ref",
            ),
            "convergence": _convergence_receipt_from_dict(
                payload["convergence"]
            ),
        }
        if legacy:
            result = object.__new__(cls)
            for field_name, field_value in values.items():
                object.__setattr__(result, field_name, field_value)
            for field_name in (
                "baseline_sources_record_ref",
                "baseline_sources",
                "stage_subject_inventory_record_ref",
                "stage_subject_inventory",
                "baseline_coverage_record_ref",
                "baseline_coverage",
            ):
                object.__setattr__(result, field_name, None)
            object.__setattr__(result, "_legacy_read_only", True)
            result.__post_init__()
        else:
            result = cls(
                **values,
                baseline_sources_record_ref=_record_ref_from_dict(
                    payload["baseline_sources_record_ref"],
                    "baseline_sources_record_ref",
                ),
                baseline_sources=StageBaselineSourceSet.from_dict(
                    payload["baseline_sources"]
                ),
                stage_subject_inventory_record_ref=_record_ref_from_dict(
                    payload["stage_subject_inventory_record_ref"],
                    "stage_subject_inventory_record_ref",
                ),
                stage_subject_inventory=StageSubjectInventory.from_dict(
                    payload["stage_subject_inventory"]
                ),
                baseline_coverage_record_ref=_record_ref_from_dict(
                    payload["baseline_coverage_record_ref"],
                    "baseline_coverage_record_ref",
                ),
                baseline_coverage=StageBaselineCoverageReceipt.from_dict(
                    payload["baseline_coverage"]
                ),
            )
        if result.to_dict() != payload:
            raise HierarchicalSearchProposalError(
                "search governance evidence identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SearchReopenEnvelope:
    """Decisions a policy may request, but never execute, to reopen."""

    decision_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_refs",
            deterministic_refs(
                self.decision_refs,
                "reopen decision_refs",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class HierarchicalSearchCompileInput:
    """Typed inputs for one exact hierarchical search policy request."""

    request_id: str
    policy: SearchPolicyDescriptor
    stage_id: str
    state: OperationalMarkovState
    decision_space_id: str
    decision_space_kind: DecisionSpaceKind
    decision_refs: tuple[str, ...]
    objective_refs: tuple[str, ...]
    hard_constraint_refs: tuple[str, ...]
    portfolio: PersistedDesignPortfolio
    expected_portfolio_digest: str
    evaluator_receipts: tuple[BoundCandidateEvaluatorReceipt, ...]
    evidence: tuple[AdoptedApplicableSearchEvidence, ...]
    governance: SearchGovernanceEvidence
    budget: SearchBudget
    allowed_actions: tuple[SearchAction, ...]
    reopen_envelope: SearchReopenEnvelope
    governance_archive: ProjectControllerArchiveAdapter | None = None

    def __post_init__(self) -> None:
        identifier(self.request_id, "request_id")
        if not isinstance(self.policy, SearchPolicyDescriptor):
            raise TypeError("policy must be SearchPolicyDescriptor")
        identifier(self.stage_id, "stage_id")
        if not isinstance(self.state, OperationalMarkovState):
            raise TypeError("state must be OperationalMarkovState@3")
        identifier(self.decision_space_id, "decision_space_id")
        if not isinstance(self.decision_space_kind, DecisionSpaceKind):
            raise TypeError("decision_space_kind must be DecisionSpaceKind")
        object.__setattr__(
            self,
            "decision_refs",
            deterministic_refs(self.decision_refs, "decision_refs"),
        )
        object.__setattr__(
            self,
            "objective_refs",
            deterministic_refs(self.objective_refs, "objective_refs"),
        )
        object.__setattr__(
            self,
            "hard_constraint_refs",
            deterministic_refs(
                self.hard_constraint_refs,
                "hard_constraint_refs",
            ),
        )
        if not isinstance(self.portfolio, PersistedDesignPortfolio):
            raise TypeError("portfolio must be PersistedDesignPortfolio")
        object.__setattr__(
            self,
            "expected_portfolio_digest",
            require_sha256(
                self.expected_portfolio_digest,
                "expected_portfolio_digest",
            ),
        )
        if (
            not isinstance(self.evaluator_receipts, tuple)
            or not self.evaluator_receipts
            or any(
                not isinstance(item, BoundCandidateEvaluatorReceipt)
                for item in self.evaluator_receipts
            )
        ):
            raise TypeError(
                "evaluator_receipts must contain bound evaluator receipts"
            )
        if (
            not isinstance(self.evidence, tuple)
            or not self.evidence
            or any(
                not isinstance(item, AdoptedApplicableSearchEvidence)
                for item in self.evidence
            )
        ):
            raise TypeError(
                "evidence must contain adopted applicable evidence units"
            )
        if not isinstance(self.governance, SearchGovernanceEvidence):
            raise TypeError("governance must be SearchGovernanceEvidence")
        if not isinstance(self.budget, SearchBudget):
            raise TypeError("budget must be SearchBudget")
        if (
            not isinstance(self.allowed_actions, tuple)
            or not self.allowed_actions
            or any(
                not isinstance(item, SearchAction)
                for item in self.allowed_actions
            )
        ):
            raise TypeError(
                "allowed_actions must contain explicit SearchAction values"
            )
        if not isinstance(self.reopen_envelope, SearchReopenEnvelope):
            raise TypeError("reopen_envelope must be SearchReopenEnvelope")
        from archflow.runtime.design_controller import (
            ProjectControllerArchiveAdapter,
        )

        if (
            self.governance_archive is not None
            and type(self.governance_archive)
            is not ProjectControllerArchiveAdapter
        ):
            raise TypeError(
                "governance_archive must be the concrete durable controller "
                "archive adapter"
            )


def _require_budget_action_match(
    budget: SearchBudget,
    allowed_actions: tuple[SearchAction, ...],
) -> None:
    permits_work = bool(set(allowed_actions) & _WORK_ACTIONS)
    has_budget = any(
        (
            budget.evaluation_units,
            budget.compute_millis,
            budget.model_tokens,
        )
    )
    if permits_work != has_budget:
        raise HierarchicalSearchProposalError(
            "search budget and allowed work actions are mismatched"
        )


def _replay_search_relation_predecessors(
    sources: StageBaselineSourceSet,
    archive: ProjectControllerArchiveAdapter | None,
) -> tuple[str, ...]:
    from archflow.runtime.design_controller import (
        ProjectControllerArchiveAdapter,
    )

    inheritance_sources = sources.relation_inheritance
    if not inheritance_sources:
        return ()
    if type(archive) is not ProjectControllerArchiveAdapter:
        raise HierarchicalSearchProposalError(
            "relation inheritance search requires durable P036 predecessor "
            "replay"
        )
    try:
        replayed_refs = archive.replay_accepted_relation_predecessors(sources)
    except Exception as exc:
        raise HierarchicalSearchProposalError(
            "relation inheritance predecessor did not replay from exact P036 "
            "records"
        ) from exc
    if (
        not isinstance(replayed_refs, tuple)
        or any(not isinstance(ref, ProjectRecordRef) for ref in replayed_refs)
    ):
        raise HierarchicalSearchProposalError(
            "relation predecessor replay returned invalid record refs"
        )
    expected_refs = {
        ref
        for source in inheritance_sources
        for ref in (
            source.predecessor.predecessor_checkpoint_ref,
            source.predecessor.stage_exit_anchor_ref,
            source.predecessor.baseline_sources_ref,
            source.predecessor.baseline_coverage_ref,
        )
    }
    if set(replayed_refs) != expected_refs:
        raise HierarchicalSearchProposalError(
            "relation predecessor replay did not cover the exact accepted "
            "record set"
        )
    predecessor_branch = inheritance_sources[0].predecessor.graph.branch
    return tuple(
        sorted(
            {
                _require_branch_record(
                    ref,
                    predecessor_branch,
                    "replayed relation predecessor record",
                )
                for ref in replayed_refs
            }
        )
    )


def _replay_current_search_governance(
    governance: SearchGovernanceEvidence,
    archive: ProjectControllerArchiveAdapter | None,
) -> tuple[str, ...]:
    from archflow.runtime.design_controller import (
        ProjectControllerArchiveAdapter,
    )

    if type(archive) is not ProjectControllerArchiveAdapter:
        raise HierarchicalSearchProposalError(
            "current search governance requires durable P036 archive replay"
        )
    assert governance.baseline_sources_record_ref is not None
    assert governance.baseline_sources is not None
    assert governance.stage_subject_inventory_record_ref is not None
    assert governance.stage_subject_inventory is not None
    assert governance.baseline_coverage_record_ref is not None
    assert governance.baseline_coverage is not None
    records = (
        (governance.profile_record_ref, governance.profile.to_dict()),
        *(
            (item.record_ref, item.receipt.to_dict())
            for item in governance.closure_checks
        ),
        (governance.closure_record_ref, governance.closure.to_dict()),
        (
            governance.convergence_record_ref,
            governance.convergence.to_dict(),
        ),
        (
            governance.baseline_sources_record_ref,
            governance.baseline_sources.to_dict(),
        ),
        (
            governance.stage_subject_inventory_record_ref,
            governance.stage_subject_inventory.to_dict(),
        ),
        (
            governance.baseline_coverage_record_ref,
            governance.baseline_coverage.to_dict(),
        ),
    )
    try:
        replayed_refs = archive.replay_exact_branch_json_records(records)
    except Exception as exc:
        raise HierarchicalSearchProposalError(
            "current search governance did not replay from exact P036 records"
        ) from exc
    expected_refs = {item[0] for item in records}
    if set(replayed_refs) != expected_refs:
        raise HierarchicalSearchProposalError(
            "current search governance replay omitted an exact branch record"
        )
    return tuple(sorted(exact_record_ref(ref) for ref in replayed_refs))


def _validate_governance(
    context: HierarchicalSearchCompileInput,
) -> tuple[str, ...]:
    state = context.state
    run = state.branch.run
    governance = context.governance
    if governance.is_legacy_read_only:
        raise HierarchicalSearchProposalError(
            "legacy governance evidence is read-only and cannot author a new "
            "search request"
        )
    profile = governance.profile
    closure = governance.closure
    convergence = governance.convergence
    refs = list(
        (
            _require_branch_record(
                governance.profile_record_ref,
                state.branch,
                "stage requirement profile record",
            ),
            _require_branch_record(
                governance.closure_record_ref,
                state.branch,
                "stage closure record",
            ),
            _require_branch_record(
                governance.convergence_record_ref,
                state.branch,
                "stage convergence record",
            ),
        )
    )
    if (
        profile.branch != state.branch
        or profile.stage_id != context.stage_id
        or profile.predecessor_state_digest != state.state_digest
    ):
        raise HierarchicalSearchProposalError(
            "stage requirement profile crossed branch, epoch, stage, or state"
        )
    if (
        closure.branch != state.branch
        or closure.stage_id != context.stage_id
        or closure.subject_digest != state.state_digest
        or closure.profile_digest != profile.profile_digest
    ):
        raise HierarchicalSearchProposalError(
            "stage closure crossed the exact operational state"
        )
    checks = tuple(item.receipt for item in governance.closure_checks)
    for item in governance.closure_checks:
        refs.append(
            _require_branch_record(
                item.record_ref,
                state.branch,
                "stage closure check record",
            )
        )
        receipt = item.receipt
        if (
            receipt.branch != state.branch
            or receipt.scope_digest != profile.scope_digest
            or receipt.subject_digest != state.state_digest
        ):
            raise HierarchicalSearchProposalError(
                "stage closure check crossed the exact operational state"
            )
    recomputed = compile_composite_stage_closure(
        profile,
        subject_digest=state.state_digest,
        check_receipts=checks,
    )
    if recomputed != closure:
        raise HierarchicalSearchProposalError(
            "stage closure does not replay from its exact typed inputs"
        )
    assert isinstance(
        governance.baseline_sources_record_ref,
        ProjectRecordRef,
    )
    assert isinstance(governance.baseline_sources, StageBaselineSourceSet)
    assert isinstance(
        governance.stage_subject_inventory_record_ref,
        ProjectRecordRef,
    )
    assert isinstance(
        governance.stage_subject_inventory,
        StageSubjectInventory,
    )
    assert isinstance(
        governance.baseline_coverage_record_ref,
        ProjectRecordRef,
    )
    assert isinstance(
        governance.baseline_coverage,
        StageBaselineCoverageReceipt,
    )
    refs.extend(
        (
            _require_branch_record(
                governance.baseline_sources_record_ref,
                state.branch,
                "stage baseline sources record",
            ),
            _require_branch_record(
                governance.stage_subject_inventory_record_ref,
                state.branch,
                "stage subject inventory record",
            ),
            _require_branch_record(
                governance.baseline_coverage_record_ref,
                state.branch,
                "stage baseline coverage record",
            ),
        )
    )
    refs.extend(
        _replay_search_relation_predecessors(
            governance.baseline_sources,
            context.governance_archive,
        )
    )
    try:
        expected_level = baseline_level_for_design_phase(
            DesignPhase(state.phase)
        )
    except (TypeError, ValueError) as exc:
        raise HierarchicalSearchProposalError(
            "search state has no exact stage baseline level"
        ) from exc
    inventory = governance.stage_subject_inventory
    baseline = governance.baseline_coverage
    if (
        inventory.branch != state.branch
        or inventory.stage_id != context.stage_id
        or inventory.stage_subject_ref != profile.stage_subject_ref
        or inventory.stage_subject_digest != state.state_digest
        or inventory.baseline_level is not expected_level
        or baseline.profile_id != profile.profile_id
        or baseline.profile_digest != profile.profile_digest
        or baseline.branch != state.branch
        or baseline.stage_id != context.stage_id
        or baseline.level is not expected_level
        or baseline.stage_subject_inventory_digest != inventory.inventory_digest
    ):
        raise HierarchicalSearchProposalError(
            "stage baseline evidence crossed profile, branch, stage, or subject"
        )
    try:
        recomputed_baseline = compile_stage_baseline_coverage(
            profile,
            level=expected_level,
            sources=governance.baseline_sources,
            subject_digest=state.state_digest,
            subject_inventory=inventory,
            check_receipts=checks,
        )
    except (TypeError, ValueError) as exc:
        raise HierarchicalSearchProposalError(
            "stage baseline does not replay from its exact typed inputs"
        ) from exc
    if (
        recomputed_baseline != baseline
        or baseline.status is not StageBaselineStatus.SATISFIED
    ):
        raise HierarchicalSearchProposalError(
            "stage baseline coverage is sparse, open, or does not replay"
        )
    if (
        convergence.branch != state.branch
        or convergence.stage != context.stage_id
        or convergence.child_state_digest != state.state_digest
        or convergence.child_sufficient_digest != state.sufficient_digest
    ):
        raise HierarchicalSearchProposalError(
            "stage convergence crossed the exact operational state"
        )
    refs.extend(
        _replay_current_search_governance(
            governance,
            context.governance_archive,
        )
    )
    return tuple(sorted(set(refs)))


def _validate_candidate_receipt(
    context: HierarchicalSearchCompileInput,
    receipt: BoundCandidateEvaluatorReceipt,
    evidence_refs: tuple[str, ...],
) -> tuple[str, str]:
    state = context.state
    portfolio = context.portfolio.portfolio
    run = state.branch.run
    _require_run_record(
        context.portfolio.record_ref,
        run,
        "portfolio record",
    )
    if (
        context.expected_portfolio_digest
        != portfolio.portfolio_digest
    ):
        raise HierarchicalSearchProposalError(
            "portfolio digest drifted from the retained portfolio"
        )
    try:
        branch = portfolio.branch(receipt.revision.branch_id)
    except ValueError as exc:
        raise HierarchicalSearchProposalError(
            "evaluator revision names a branch outside the portfolio"
        ) from exc
    if branch.lifecycle is BranchLifecycle.REJECTED:
        raise HierarchicalSearchProposalError(
            "a rejected branch cannot be a search candidate"
        )
    if branch.head.ref != receipt.revision:
        raise HierarchicalSearchProposalError(
            "evaluator receipt is stale for the exact branch head"
        )
    candidate_ref = portfolio_candidate_ref(
        portfolio.portfolio_id,
        receipt.revision,
    )
    evaluation_ref = _require_run_record(
        receipt.evaluation_record_ref,
        run,
        "candidate evaluation record",
    )
    if (
        receipt.evaluation.candidate_ref != candidate_ref
        or receipt.evaluation.evaluation_ref != evaluation_ref
    ):
        raise HierarchicalSearchProposalError(
            "candidate evaluation ref or record digest changed"
        )
    if any(
        estimate.source_ref not in {evaluation_ref, *evidence_refs}
        for estimate in receipt.evaluation.objectives
    ):
        raise HierarchicalSearchProposalError(
            "evaluator statistics cite raw or unbound evidence"
        )
    hard_ref = receipt.hard_check.require_run(
        run,
        "candidate hard-check record",
    )
    hard_check = receipt.hard_check.receipt
    hard_refs = context.hard_constraint_refs
    if (
        hard_check.branch != state.branch
        or hard_check.scope_digest != state.state_digest
        or hard_check.subject_digest != receipt.revision.revision_digest
        or hard_check.subject_refs != hard_refs
        or hard_check.coverage_denominator != hard_refs
        or hard_check.covered_refs != hard_refs
    ):
        raise HierarchicalSearchProposalError(
            "candidate hard check lacks exact state and denominator coverage"
        )
    if hard_check.status is CheckStatus.NOT_APPLICABLE:
        raise HierarchicalSearchProposalError(
            "declared hard constraints cannot be not-applicable"
        )
    failure_refs = tuple(
        sorted(
            {
                subject_ref
                for finding in hard_check.findings
                if finding.severity is FindingSeverity.ERROR
                for subject_ref in finding.subject_refs
            }
        )
    )
    if receipt.evaluation.hard_failure_refs != failure_refs:
        raise HierarchicalSearchProposalError(
            "candidate hard failures disagree with the typed hard check"
        )
    return candidate_ref, hard_ref


def _candidate_is_hard_feasible(
    receipt: BoundCandidateEvaluatorReceipt,
) -> bool:
    check = receipt.hard_check.receipt
    return (
        check.status is CheckStatus.PASS
        and not check.revalidation_refs
        and not receipt.evaluation.hard_failure_refs
    )


def _validate_context(
    context: HierarchicalSearchCompileInput,
) -> tuple[
    tuple[str, ...],
    tuple[SearchCandidateEvaluation, ...],
    tuple[str, ...],
    dict[str, BoundCandidateEvaluatorReceipt],
]:
    state = context.state
    portfolio = context.portfolio.portfolio
    if portfolio.run != state.branch.run:
        raise HierarchicalSearchProposalError(
            "portfolio crossed project, run, or canonical base"
        )
    if portfolio.operational_state_digest != state.state_digest:
        raise HierarchicalSearchProposalError(
            "portfolio is stale for the exact operational state"
        )
    _require_budget_action_match(context.budget, context.allowed_actions)
    if not set(context.reopen_envelope.decision_refs).issubset(
        context.decision_refs
    ):
        raise HierarchicalSearchProposalError(
            "reopen envelope names a decision outside the decision space"
        )
    if (
        SearchAction.REQUEST_REOPEN in context.allowed_actions
        and not context.reopen_envelope.decision_refs
    ):
        raise HierarchicalSearchProposalError(
            "request_reopen requires an explicit non-empty envelope"
        )

    governance_refs = _validate_governance(context)
    evidence_units = tuple(
        sorted(context.evidence, key=lambda item: item.ref)
    )
    evidence_refs = tuple(item.ref for item in evidence_units)
    if len(evidence_refs) != len(set(evidence_refs)):
        raise HierarchicalSearchProposalError(
            "adopted applicable evidence units contain duplicates"
        )
    try:
        current_branch = portfolio.branch(state.branch.branch_id)
    except ValueError as exc:
        raise HierarchicalSearchProposalError(
            "operational branch is absent from the exact portfolio"
        ) from exc
    for item in evidence_units:
        if item.claim.branch != state.branch:
            raise HierarchicalSearchProposalError(
                "evidence claim crossed branch, epoch, run, or base"
            )
        if (
            item.adoption.branch_revision_digest
            != current_branch.head.revision_digest
        ):
            raise HierarchicalSearchProposalError(
                "evidence adoption is stale for the exact portfolio branch"
            )

    receipts = tuple(
        sorted(
            context.evaluator_receipts,
            key=lambda item: item.evaluation.candidate_ref,
        )
    )
    candidate_refs: list[str] = []
    evaluations: list[SearchCandidateEvaluation] = []
    hard_refs: list[str] = []
    by_candidate: dict[str, BoundCandidateEvaluatorReceipt] = {}
    for receipt in receipts:
        candidate_ref, hard_ref = _validate_candidate_receipt(
            context,
            receipt,
            evidence_refs,
        )
        if candidate_ref in by_candidate:
            raise HierarchicalSearchProposalError(
                "evaluator receipts repeat an exact portfolio candidate"
            )
        by_candidate[candidate_ref] = receipt
        candidate_refs.append(candidate_ref)
        evaluations.append(receipt.evaluation)
        hard_refs.append(hard_ref)
    eligible_targets = {
        *context.decision_refs,
        *context.hard_constraint_refs,
        *candidate_refs,
    }
    if any(
        item.applicability.target_ref not in eligible_targets
        for item in evidence_units
    ):
        raise HierarchicalSearchProposalError(
            "applicability targets a ref outside the exact search space"
        )
    if SearchAction.REQUEST_COMMIT in context.allowed_actions:
        if (
            context.governance.closure.status
            is not StageClosureStatus.SATISFIED
            or not context.governance.convergence.stage_ready
        ):
            raise HierarchicalSearchProposalError(
                "typed closure and convergence do not permit commit request"
            )
        if not any(_candidate_is_hard_feasible(item) for item in receipts):
            raise HierarchicalSearchProposalError(
                "no candidate has complete passing hard-constraint coverage"
            )
    retained_refs = tuple(
        sorted(
            {
                *governance_refs,
                *evidence_refs,
                *hard_refs,
            }
        )
    )
    return (
        tuple(sorted(candidate_refs)),
        tuple(evaluations),
        retained_refs,
        by_candidate,
    )


def compile_search_policy_request(
    context: HierarchicalSearchCompileInput,
) -> SearchPolicyRequest:
    """Compile one exact request; no policy or persistence side effect occurs."""

    if not isinstance(context, HierarchicalSearchCompileInput):
        raise TypeError("context must be HierarchicalSearchCompileInput")
    candidate_refs, evaluations, evidence_refs, _ = _validate_context(
        context
    )
    state = context.state
    decision_space = DecisionSpaceDescriptor(
        descriptor_id=context.decision_space_id,
        stage_id=context.stage_id,
        branch=state.branch,
        state_digest=state.state_digest,
        kind=context.decision_space_kind,
        decision_refs=context.decision_refs,
        reopenable_decision_refs=context.reopen_envelope.decision_refs,
        candidate_refs=candidate_refs,
        objective_refs=context.objective_refs,
        hard_constraint_refs=context.hard_constraint_refs,
    )
    return SearchPolicyRequest(
        request_id=context.request_id,
        policy=context.policy,
        branch=state.branch,
        stage_id=context.stage_id,
        state_digest=state.state_digest,
        decision_space=decision_space,
        portfolio_ref=exact_record_ref(context.portfolio.record_ref),
        portfolio_digest=context.portfolio.portfolio.portfolio_digest,
        evaluations=evaluations,
        budget=context.budget,
        allowed_actions=context.allowed_actions,
        evidence_refs=evidence_refs,
    )


@dataclass(frozen=True, slots=True)
class HierarchicalSearchProposal:
    """Validated policy suggestion with no transition or write authority."""

    outcome_id: str
    request_digest: str
    directive_id: str
    directive_digest: str
    policy: SearchPolicyDescriptor
    branch: BranchRef
    stage_id: str
    state_digest: str
    portfolio_ref: str
    portfolio_digest: str
    action: SearchAction
    target_refs: tuple[str, ...]
    allocations: tuple[SearchBudgetAllocation, ...]
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    retained_context_refs: tuple[str, ...]
    commit_request_refs: tuple[str, ...]
    reopen_request_refs: tuple[str, ...]

    SCHEMA = "HierarchicalSearchProposal@1"

    def __post_init__(self) -> None:
        identifier(self.outcome_id, "outcome_id")
        object.__setattr__(
            self,
            "request_digest",
            require_sha256(self.request_digest, "request_digest"),
        )
        identifier(self.directive_id, "directive_id")
        object.__setattr__(
            self,
            "directive_digest",
            require_sha256(self.directive_digest, "directive_digest"),
        )
        if not isinstance(self.policy, SearchPolicyDescriptor):
            raise TypeError("policy must be SearchPolicyDescriptor")
        branch_ref_to_dict(self.branch)
        identifier(self.stage_id, "stage_id")
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "state_digest"),
        )
        object.__setattr__(
            self,
            "portfolio_ref",
            logical_ref(self.portfolio_ref, "portfolio_ref"),
        )
        object.__setattr__(
            self,
            "portfolio_digest",
            require_sha256(self.portfolio_digest, "portfolio_digest"),
        )
        if not isinstance(self.action, SearchAction):
            raise TypeError("action must be SearchAction")
        for field in (
            "target_refs",
            "evidence_refs",
            "retained_context_refs",
            "commit_request_refs",
            "reopen_request_refs",
        ):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    field,
                    allow_empty=field
                    in {
                        "target_refs",
                        "commit_request_refs",
                        "reopen_request_refs",
                    },
                ),
            )
        if (
            not isinstance(self.allocations, tuple)
            or any(
                not isinstance(item, SearchBudgetAllocation)
                for item in self.allocations
            )
        ):
            raise TypeError(
                "allocations must contain SearchBudgetAllocation values"
            )
        allocation_refs = tuple(item.target_ref for item in self.allocations)
        if allocation_refs != tuple(sorted(set(allocation_refs))):
            raise HierarchicalSearchProposalError(
                "allocations must target each exact ref once"
            )
        object.__setattr__(
            self,
            "reason_codes",
            deterministic_identifiers(self.reason_codes, "reason_codes"),
        )
        expected_commit = (
            self.target_refs
            if self.action is SearchAction.REQUEST_COMMIT
            else ()
        )
        expected_reopen = (
            self.target_refs
            if self.action is SearchAction.REQUEST_REOPEN
            else ()
        )
        if self.commit_request_refs != expected_commit:
            raise HierarchicalSearchProposalError(
                "commit request refs do not match the proposal action"
            )
        if self.reopen_request_refs != expected_reopen:
            raise HierarchicalSearchProposalError(
                "reopen request refs do not match the proposal action"
            )

    @property
    def policy_descriptor_digest(self) -> str:
        return self.policy.descriptor_digest

    @property
    def proposal_only(self) -> bool:
        return True

    @property
    def decision_operator(self) -> None:
        return None

    @property
    def stage_acceptance(self) -> None:
        return None

    @property
    def canonical_write(self) -> None:
        return None

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "outcome_id": self.outcome_id,
            "request_digest": self.request_digest,
            "directive_id": self.directive_id,
            "directive_digest": self.directive_digest,
            "policy": self.policy.to_dict(),
            "policy_descriptor_digest": self.policy_descriptor_digest,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "state_digest": self.state_digest,
            "portfolio_ref": self.portfolio_ref,
            "portfolio_digest": self.portfolio_digest,
            "action": self.action.value,
            "target_refs": list(self.target_refs),
            "allocations": [item.to_dict() for item in self.allocations],
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": list(self.reason_codes),
            "retained_context_refs": list(self.retained_context_refs),
            "commit_request_refs": list(self.commit_request_refs),
            "reopen_request_refs": list(self.reopen_request_refs),
            **_OUTCOME_AUTHORITY_FIELDS,
        }

    @property
    def outcome_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "outcome_digest": self.outcome_digest}

    @classmethod
    def from_dict(cls, value: object) -> "HierarchicalSearchProposal":
        payload = exact_mapping(
            value,
            {
                "schema",
                "outcome_id",
                "request_digest",
                "directive_id",
                "directive_digest",
                "policy",
                "policy_descriptor_digest",
                "branch",
                "stage_id",
                "state_digest",
                "portfolio_ref",
                "portfolio_digest",
                "action",
                "target_refs",
                "allocations",
                "evidence_refs",
                "reason_codes",
                "retained_context_refs",
                "commit_request_refs",
                "reopen_request_refs",
                "outcome_digest",
                *_OUTCOME_AUTHORITY_FIELDS,
            },
            "hierarchical search proposal",
        )
        if payload["schema"] != cls.SCHEMA or any(
            payload[field] is not expected
            for field, expected in _OUTCOME_AUTHORITY_FIELDS.items()
        ):
            raise HierarchicalSearchProposalError(
                "hierarchical search proposal acquired forbidden authority"
            )
        for field in (
            "target_refs",
            "allocations",
            "evidence_refs",
            "reason_codes",
            "retained_context_refs",
            "commit_request_refs",
            "reopen_request_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        policy = SearchPolicyDescriptor.from_dict(payload["policy"])
        if payload["policy_descriptor_digest"] != policy.descriptor_digest:
            raise HierarchicalSearchProposalError(
                "policy identity or version changed in proposal"
            )
        result = cls(
            outcome_id=payload["outcome_id"],
            request_digest=payload["request_digest"],
            directive_id=payload["directive_id"],
            directive_digest=payload["directive_digest"],
            policy=policy,
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            state_digest=payload["state_digest"],
            portfolio_ref=payload["portfolio_ref"],
            portfolio_digest=payload["portfolio_digest"],
            action=SearchAction(payload["action"]),
            target_refs=tuple(payload["target_refs"]),
            allocations=tuple(
                SearchBudgetAllocation.from_dict(item)
                for item in payload["allocations"]
            ),
            evidence_refs=tuple(payload["evidence_refs"]),
            reason_codes=tuple(payload["reason_codes"]),
            retained_context_refs=tuple(
                payload["retained_context_refs"]
            ),
            commit_request_refs=tuple(payload["commit_request_refs"]),
            reopen_request_refs=tuple(payload["reopen_request_refs"]),
        )
        if result.to_dict() != dict(payload):
            raise HierarchicalSearchProposalError(
                "hierarchical search proposal digest changed"
            )
        return result


def compile_search_proposal(
    request: SearchPolicyRequest,
    directive: SearchDirective,
    *,
    context: HierarchicalSearchCompileInput,
) -> HierarchicalSearchProposal:
    """Revalidate one directive and compile it into a detached proposal."""

    if not isinstance(request, SearchPolicyRequest):
        raise TypeError("request must be SearchPolicyRequest")
    if not isinstance(directive, SearchDirective):
        raise TypeError("directive must be SearchDirective")
    if not isinstance(context, HierarchicalSearchCompileInput):
        raise TypeError("context must be HierarchicalSearchCompileInput")
    current_request = compile_search_policy_request(context)
    if current_request.request_digest != request.request_digest:
        raise HierarchicalSearchProposalError(
            "search context changed after request compilation"
        )
    validated = validate_search_directive(request, directive)
    _, _, retained_refs, candidates = _validate_context(context)
    if validated.action is SearchAction.REQUEST_COMMIT:
        if (
            context.governance.closure.status
            is not StageClosureStatus.SATISFIED
            or not context.governance.convergence.stage_ready
        ):
            raise HierarchicalSearchProposalError(
                "closure and convergence dominate the commit request"
            )
        if any(
            target not in candidates
            or not _candidate_is_hard_feasible(candidates[target])
            for target in validated.target_refs
        ):
            raise HierarchicalSearchProposalError(
                "commit request lacks complete passing hard-check coverage"
            )
    commit_refs = (
        validated.target_refs
        if validated.action is SearchAction.REQUEST_COMMIT
        else ()
    )
    reopen_refs = (
        validated.target_refs
        if validated.action is SearchAction.REQUEST_REOPEN
        else ()
    )
    return HierarchicalSearchProposal(
        outcome_id=f"search-proposal-{validated.directive_digest[:16]}",
        request_digest=request.request_digest,
        directive_id=validated.directive_id,
        directive_digest=validated.directive_digest,
        policy=request.policy,
        branch=request.branch,
        stage_id=request.stage_id,
        state_digest=request.state_digest,
        portfolio_ref=request.portfolio_ref,
        portfolio_digest=request.portfolio_digest,
        action=validated.action,
        target_refs=validated.target_refs,
        allocations=validated.allocations,
        evidence_refs=validated.evidence_refs,
        reason_codes=validated.reason_codes,
        retained_context_refs=retained_refs,
        commit_request_refs=commit_refs,
        reopen_request_refs=reopen_refs,
    )


class HierarchicalSearchProposalCompiler:
    """Exact registry dispatch ending at a detached proposal artifact."""

    def __init__(self, registry: SearchPolicyRegistry) -> None:
        if not isinstance(registry, SearchPolicyRegistry):
            raise TypeError("registry must be SearchPolicyRegistry")
        self._registry = registry

    async def propose(
        self,
        context: HierarchicalSearchCompileInput,
    ) -> HierarchicalSearchProposal:
        request = compile_search_policy_request(context)
        directive = await self._registry.decide(request)
        return compile_search_proposal(
            request,
            directive,
            context=context,
        )


__all__ = [
    "AdoptedApplicableSearchEvidence",
    "BoundCandidateEvaluatorReceipt",
    "HierarchicalSearchCompileInput",
    "HierarchicalSearchProposal",
    "HierarchicalSearchProposalCompiler",
    "HierarchicalSearchProposalError",
    "RetainedCheckReceipt",
    "SearchGovernanceEvidence",
    "SearchReopenEnvelope",
    "compile_search_policy_request",
    "compile_search_proposal",
    "exact_record_ref",
    "portfolio_candidate_ref",
]
