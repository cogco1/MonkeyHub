"""Exact P060 failure feedback and bounded semantic-geometry revision.

The module translates project-authored architectural criteria into model
context and neutral geometry requirements.  It never authors a building
answer, patches a component, or grants a validation result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Protocol

from archive.archflow.capabilities.design_development import (
    invalidate_developed_design,
    resume_development_after_selection,
)
from archive.archflow.research.index import BranchBasisIndex
from archive.archflow.capabilities.evidence_sufficiency import (
    DecisionUniverseClosure,
    DecisionUniverseRevision,
    EvidenceSufficiencyPolicy,
    EvidenceSufficiencyReceipt,
    ResearchFrontier,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalIssue,
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
    GeometryProposalStatus,
    load_compiled_geometry_program,
    produce_geometry_program_proposal,
)
from archive.archflow.capabilities.semantic_spatial_authoring import (
    SemanticSpatialAuthoringStatus,
    _author_semantic_spatial_option,
    semantic_spatial_repair_feedback,
)
from archflow.production.provider_runtime import AuthorizedAsyncModelProvider, InvocationEvidenceCollector
from archflow.production.responsibility import InvocationEnvelope
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archive.archflow.runtime.branch_research import BranchResearchArchive
from archflow.contracts.canonical import canonical_digest
from archive.archflow.realization.sandbox import RealizationStatus, realize_geometry
from archive.archflow.runtime.production_runtime import (
    CompiledProductionStep,
    ProductionAuthoringContext,
    ProductionStepCompilationFailed,
)
from archflow.compilers.geometry import CompiledGeometryProgram
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleStatus,
    compile_semantic_geometry_lifecycle,
)
from archflow.state.design_portfolio import (
    DesignOptionPortfolio,
    SelectionPolicy,
    compile_selected_branch_handoff,
    initialize_design_portfolio,
    park_branch,
    revise_branch,
    select_branch,
)
from archflow.state.developed_design import (
    DevelopedDesignState,
    SelectedSchematicInput,
)
from archflow.state.geometry_program import digest_value
from archflow.state.spatial import (
    ComponentTransitionReceipt,
    SchematicOptionSet,
    SpatialOptionProposal,
    compile_component_transition,
)
from archive.archflow.validation.architectural import (
    ArchitecturalCriterion,
    ArchitecturalFinding,
    ArchitecturalUsabilityContract,
    ArchitecturalUsabilityReceipt,
    ArchitecturalUsabilityStatus,
    CriterionFindingStatus,
    CriterionOperator,
)
from archflow.contracts.canonical import require_sha256


class ArchitecturalRevisionError(ValueError):
    """P060 feedback is stale, unsupported, or over-authoritative."""


class ArchitecturalRevisionCompilationError(ProductionStepCompilationFailed):
    """A bounded architectural revision could not compile a successor."""


@dataclass(frozen=True, slots=True)
class ArchitecturalRevisionFeedback:
    contract_digest: str
    receipt_digest: str
    predecessor_proposal_digest: str
    failures: tuple[tuple[ArchitecturalCriterion, ArchitecturalFinding], ...]
    geometry_issues: tuple[GeometryProposalIssue, ...]
    realization_requirements: tuple[dict[str, object], ...]

    SCHEMA = "ArchitecturalRevisionFeedback@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.contract_digest, "contract_digest"),
            (self.receipt_digest, "receipt_digest"),
            (self.predecessor_proposal_digest, "predecessor_proposal_digest"),
        ):
            require_sha256(value, field)
        if not self.failures:
            raise ArchitecturalRevisionError("revision feedback needs a blocker")
        ids = tuple(criterion.criterion_id for criterion, _ in self.failures)
        if ids != tuple(sorted(set(ids))):
            raise ArchitecturalRevisionError(
                "revision failures must be sorted and unique"
            )
        if len(self.geometry_issues) != len(self.failures):
            raise ArchitecturalRevisionError(
                "every failed criterion requires a geometry issue"
            )

    def semantic_revision_context(
        self,
        predecessor: SpatialOptionProposal,
    ) -> dict[str, object]:
        if not isinstance(predecessor, SpatialOptionProposal):
            raise TypeError("predecessor must be SpatialOptionProposal")
        if predecessor.proposal_digest != self.predecessor_proposal_digest:
            raise ArchitecturalRevisionError("predecessor proposal is stale")
        return {
            "schema": "SpatialArchitecturalRevisionContext@1",
            "predecessor_proposal": predecessor.to_dict(),
            "predecessor_proposal_digest": predecessor.proposal_digest,
            "architectural_contract_digest": self.contract_digest,
            "architectural_receipt_digest": self.receipt_digest,
            "failed_mandatory_findings": [
                {
                    "schema": "ArchitecturalRevisionFailure@1",
                    "criterion": criterion.to_dict(),
                    "finding": finding.to_dict(),
                }
                for criterion, finding in self.failures
            ],
            "instructions": (
                "Return one complete successor SpatialOptionProposal, not a "
                "patch. Preserve the option_id and all genuinely stable "
                "component identities. Revise the semantic component tree and "
                "coarse massing together wherever the exact failed criteria "
                "require it. Increment revisions for changed retained "
                "components, and add an explicit semantic component only when "
                "the architectural requirement needs one. Do not claim that "
                "the successor passes validation."
            ),
            "complete_successor_required": True,
            "preserve_option_identity": True,
            "component_patch_authority": False,
            "geometry_patch_authority": False,
            "selection_authority": False,
            "validation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "contract_digest": self.contract_digest,
            "receipt_digest": self.receipt_digest,
            "predecessor_proposal_digest": self.predecessor_proposal_digest,
            "failures": [
                {
                    "schema": "ArchitecturalRevisionFailure@1",
                    "criterion": criterion.to_dict(),
                    "finding": finding.to_dict(),
                }
                for criterion, finding in self.failures
            ],
            "geometry_issues": [item.to_dict() for item in self.geometry_issues],
            "realization_requirements": [
                dict(item) for item in self.realization_requirements
            ],
            "building_answer_authored_by_framework": False,
            "component_patch_authority": False,
            "geometry_patch_authority": False,
            "validation_authority": False,
            "canonical_write_authority": False,
        }


def compile_architectural_revision_feedback(
    *,
    contract: ArchitecturalUsabilityContract,
    receipt: ArchitecturalUsabilityReceipt,
    predecessor_proposal: SpatialOptionProposal,
) -> ArchitecturalRevisionFeedback:
    """Translate one exact failed P060 receipt without inventing a remedy."""

    if not isinstance(contract, ArchitecturalUsabilityContract):
        raise TypeError("contract must be ArchitecturalUsabilityContract")
    if not isinstance(receipt, ArchitecturalUsabilityReceipt):
        raise TypeError("receipt must be ArchitecturalUsabilityReceipt")
    if not isinstance(predecessor_proposal, SpatialOptionProposal):
        raise TypeError("predecessor_proposal must be SpatialOptionProposal")
    context = contract.context
    if receipt.status is not ArchitecturalUsabilityStatus.FAILED:
        raise ArchitecturalRevisionError(
            "only a failed architectural receipt can request revision"
        )
    if (
        receipt.contract_digest != contract.contract_digest
        or receipt.project_id != context.project_id
        or receipt.run_id != context.run_id
        or receipt.base != context.base
        or receipt.design_state_digest != context.design_state_digest
        or receipt.geometry_program_digest != context.geometry_program_digest
        or receipt.realization_receipt_digest
        != context.realization_receipt_digest
        or receipt.scene_digest != context.scene_digest
        or receipt.artifact_ref != context.artifact_ref
    ):
        raise ArchitecturalRevisionError(
            "architectural receipt is stale for its exact contract"
        )
    if predecessor_proposal.proposal_digest != context.component_tree_digest:
        raise ArchitecturalRevisionError(
            "architectural contract is stale for the predecessor component tree"
        )
    criteria = {item.criterion_id: item for item in contract.criteria}
    failures: list[tuple[ArchitecturalCriterion, ArchitecturalFinding]] = []
    issues: list[GeometryProposalIssue] = []
    requirements: list[dict[str, object]] = []
    for finding in receipt.findings:
        if not (
            finding.mandatory
            and finding.status is CriterionFindingStatus.FAIL
        ):
            continue
        criterion = criteria.get(finding.criterion_id)
        if (
            criterion is None
            or not criterion.mandatory
            or criterion.expected_json != finding.expected_json
            or criterion.unit != finding.unit
            or criterion.source_refs != finding.source_refs
            or criterion.component_ids != finding.component_ids
            or criterion.geometry_object_ids != finding.geometry_object_ids
            or criterion.obligation_refs != finding.obligation_refs
        ):
            raise ArchitecturalRevisionError(
                "failed finding no longer matches its criterion"
            )
        failures.append((criterion, finding))
        issues.append(
            GeometryProposalIssue(
                code=f"architectural_usability.{criterion.criterion_id}",
                detail=(
                    f"Exact mandatory criterion failed: {finding.detail}; "
                    f"expected={finding.expected_json}; "
                    f"observed={finding.observed_json}."
                ),
            )
        )
        relation = {
            CriterionOperator.EQUAL: "exact",
            CriterionOperator.MINIMUM: "minimum",
        }.get(criterion.operator)
        if relation is not None:
            requirements.append(
                {
                    "schema": "GeometryRealizationRequirement@1",
                    "requirement_id": (
                        f"architectural-{criterion.criterion_id}"
                    ),
                    "source_refs": list(
                        sorted(
                            set(criterion.source_refs)
                            | set(finding.evidence_refs)
                        )
                    ),
                    "property": criterion.measurement_key,
                    "relation": relation,
                    "threshold_json": criterion.expected_json,
                    "unit": criterion.unit or "unitless",
                }
            )
    if not failures:
        raise ArchitecturalRevisionError(
            "failed receipt has no failed mandatory finding"
        )
    return ArchitecturalRevisionFeedback(
        contract_digest=contract.contract_digest,
        receipt_digest=receipt.receipt_digest,
        predecessor_proposal_digest=predecessor_proposal.proposal_digest,
        failures=tuple(failures),
        geometry_issues=tuple(issues),
        realization_requirements=tuple(requirements),
    )


def _require_branch_index_matches_selected_predecessor(
    *,
    branch_index: BranchBasisIndex,
    portfolio: DesignOptionPortfolio,
    selected_schematic: SelectedSchematicInput,
    selection_ref: ProjectRecordRef,
    expected_scope_digest: str,
) -> None:
    """Reject a valid index when it belongs to another selected Candidate."""

    selected = portfolio.selected_branch
    scope = branch_index.scope
    if (
        selected is None
        or scope.run != portfolio.run
        or scope.portfolio_id != portfolio.portfolio_id
        or scope.portfolio_digest != portfolio.portfolio_digest
        or scope.operational_state_digest != portfolio.operational_state_digest
        or scope.branch_id != selected.branch_id
        or scope.branch_revision_id != selected.head.revision_id
        or scope.branch_revision_digest != selected.head.revision_digest
        or scope.selection_record_ref != selection_ref
        or selected_schematic.option.option_id != selected.branch_id
        or selected_schematic.selection_decision_ref
        != scope.selection_record_ref.uri
        or expected_scope_digest != scope.scope_digest
    ):
        raise ArchitecturalRevisionError(
            "branch RAG index does not match the selected predecessor Candidate"
        )


def _portfolio_uses_branch_research_selection(
    portfolio: DesignOptionPortfolio,
) -> bool:
    """Detect P078 lineage from retained transitions, not caller-supplied refs."""

    if not isinstance(portfolio, DesignOptionPortfolio):
        raise TypeError("portfolio must be DesignOptionPortfolio")
    fragment = (
        f"/runs/{portfolio.run_id}/records/branch-selection-"
    )
    return any(fragment in item.decision_ref for item in portfolio.transitions)


class ArchitecturalRevisionRepository(Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, object]: ...

    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, object],
    ) -> ProjectRecordRef: ...


@dataclass(frozen=True, slots=True)
class ArchitecturalRevisionCompiler:
    repository: ArchitecturalRevisionRepository
    context_ref: ProjectRecordRef
    context: ProductionAuthoringContext
    predecessor_state_ref: ProjectRecordRef
    predecessor_program_ref: ProjectRecordRef
    option_set_ref: ProjectRecordRef
    selection_ref: ProjectRecordRef
    predecessor_spatial_ref: ProjectRecordRef
    architectural_contract_ref: ProjectRecordRef
    architectural_receipt_ref: ProjectRecordRef
    provider: AuthorizedAsyncModelProvider
    evidence_collector: InvocationEvidenceCollector
    geometry_provider_identity: GeometryProposalProviderIdentity
    geometry_policy: GeometryProposalPolicy = GeometryProposalPolicy(3)
    template_refs: tuple[ProjectRecordRef, ...] = ()
    predecessor_portfolio_ref: ProjectRecordRef | None = None
    branch_basis_index_ref: ProjectRecordRef | None = None
    expected_branch_scope_digest: str | None = None
    branch_decision_refs: tuple[str, ...] = ()
    branch_decision_universe: DecisionUniverseRevision | None = None
    branch_evidence_policy: EvidenceSufficiencyPolicy | None = None
    branch_universe_closure: DecisionUniverseClosure | None = None
    branch_evidence_sufficiency: EvidenceSufficiencyReceipt | None = None
    branch_research_frontier: ResearchFrontier | None = None

    def __post_init__(self) -> None:
        for method in ("load_json", "put_json"):
            if not callable(getattr(self.repository, method, None)):
                raise TypeError(f"repository must implement {method}")
        if not isinstance(self.context, ProductionAuthoringContext):
            raise TypeError("context must be ProductionAuthoringContext")
        if not isinstance(self.provider, AuthorizedAsyncModelProvider):
            raise TypeError("provider must be P053-authorized")
        if not isinstance(self.evidence_collector, InvocationEvidenceCollector):
            raise TypeError("evidence_collector must be InvocationEvidenceCollector")
        if not isinstance(
            self.geometry_provider_identity, GeometryProposalProviderIdentity
        ):
            raise TypeError("geometry_provider_identity is invalid")
        if not isinstance(self.geometry_policy, GeometryProposalPolicy):
            raise TypeError("geometry_policy is invalid")
        branch_configured = (
            self.branch_basis_index_ref is not None,
            self.expected_branch_scope_digest is not None,
            bool(self.branch_decision_refs),
            self.branch_decision_universe is not None,
            self.branch_evidence_policy is not None,
            self.branch_universe_closure is not None,
            self.branch_evidence_sufficiency is not None,
            self.branch_research_frontier is not None,
        )
        if any(branch_configured) and not all(branch_configured):
            raise ArchitecturalRevisionError(
                "branch index, scope, decisions, and exact P079 acceptance "
                "values are one input"
            )
        branch_selection_prefix = (
            f"runs/{self.context.run.run_id}/records/branch-selection-"
        )
        if (
            self.selection_ref.relative_path.startswith(branch_selection_prefix)
            and not all(branch_configured)
        ):
            raise ArchitecturalRevisionError(
                "branch-selected predecessor requires its persisted RAG index"
            )
        if self.expected_branch_scope_digest is not None:
            require_sha256(
                self.expected_branch_scope_digest,
                "expected_branch_scope_digest",
            )
            branch_types = (
                (
                    self.branch_decision_universe,
                    DecisionUniverseRevision,
                    "branch_decision_universe",
                ),
                (
                    self.branch_evidence_policy,
                    EvidenceSufficiencyPolicy,
                    "branch_evidence_policy",
                ),
                (
                    self.branch_universe_closure,
                    DecisionUniverseClosure,
                    "branch_universe_closure",
                ),
                (
                    self.branch_evidence_sufficiency,
                    EvidenceSufficiencyReceipt,
                    "branch_evidence_sufficiency",
                ),
                (
                    self.branch_research_frontier,
                    ResearchFrontier,
                    "branch_research_frontier",
                ),
            )
            for value, expected_type, field in branch_types:
                if not isinstance(value, expected_type):
                    raise TypeError(f"{field} must be {expected_type.__name__}")
            assert isinstance(
                self.branch_decision_universe,
                DecisionUniverseRevision,
            )
            if (
                self.branch_decision_universe.scope_digest
                != self.expected_branch_scope_digest
            ):
                raise ArchitecturalRevisionError(
                    "branch decision universe crosses the expected research scope"
                )
        if self.branch_decision_refs != tuple(
            sorted(set(self.branch_decision_refs))
        ):
            raise ArchitecturalRevisionError(
                "branch decision refs must be sorted and unique"
            )
        refs = self.intent_record_refs
        if any(item.project_id != self.context.run.project_id for item in refs):
            raise ArchitecturalRevisionError(
                "architectural revision inputs cross project boundary"
            )

    @property
    def intent_record_refs(self) -> tuple[ProjectRecordRef, ...]:
        refs = (
            self.context_ref,
            self.predecessor_state_ref,
            self.predecessor_program_ref,
            self.option_set_ref,
            self.selection_ref,
            self.predecessor_spatial_ref,
            self.architectural_contract_ref,
            self.architectural_receipt_ref,
            *((self.predecessor_portfolio_ref,) if self.predecessor_portfolio_ref else ()),
            *((self.branch_basis_index_ref,) if self.branch_basis_index_ref else ()),
            *self.template_refs,
        )
        if any(not isinstance(item, ProjectRecordRef) for item in refs):
            raise TypeError("architectural revision refs are invalid")
        return tuple(sorted(set(refs), key=lambda item: item.uri))

    def invocation_evidence_cursor(self) -> int:
        return self.evidence_collector.cursor()

    def invocation_evidence_since(
        self,
        cursor: int,
    ) -> tuple[InvocationEnvelope, ...]:
        return self.evidence_collector.since(cursor)

    async def compile(
        self,
        *,
        run: RunRef,
        raw_request: ProjectRecordRef,
        prompt: str,
    ) -> CompiledProductionStep:
        del prompt
        self.context.require_run(run)
        destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )
        cursor = self.evidence_collector.cursor()
        try:
            context = ProductionAuthoringContext.from_dict(
                self.repository.load_json(self.context_ref)
            )
            if context != self.context:
                raise ArchitecturalRevisionError(
                    "production authoring context record changed"
                )
            predecessor_state = DevelopedDesignState.from_dict(
                _production_record_content(
                    self.repository.load_json(self.predecessor_state_ref),
                    run=run,
                    expected_role="design-state",
                )
            )
            predecessor_program = load_compiled_geometry_program(
                _production_record_content(
                    self.repository.load_json(self.predecessor_program_ref),
                    run=run,
                    expected_role="geometry-program",
                )
            )
            option_set = SchematicOptionSet.from_dict(
                self.repository.load_json(self.option_set_ref)
            )
            predecessor_spatial = SpatialOptionProposal.from_dict(
                self.repository.load_json(self.predecessor_spatial_ref)
            )
            contract = ArchitecturalUsabilityContract.from_dict(
                self.repository.load_json(self.architectural_contract_ref)
            )
            receipt = ArchitecturalUsabilityReceipt.from_dict(
                self.repository.load_json(self.architectural_receipt_ref)
            )
            self._validate_exact_inputs(
                run,
                predecessor_state,
                predecessor_program,
                option_set,
                predecessor_spatial,
                contract,
                receipt,
            )
            portfolio = self._reconstruct_predecessor_portfolio(
                run=run,
                raw_request=raw_request,
                option_set=option_set,
                predecessor_state=predecessor_state,
            )
            feedback = compile_architectural_revision_feedback(
                contract=contract,
                receipt=receipt,
                predecessor_proposal=predecessor_spatial,
            )
            feedback_ref = self.repository.put_json(
                run=run,
                destination=destination,
                record_kind="architectural-revision-feedback",
                payload=feedback.to_dict(),
            )
            revision_context = feedback.semantic_revision_context(
                predecessor_spatial
            )
            branch_index = None
            branch_context = None
            branch_scope_digest = None
            predecessor_is_branch_conditioned = (
                _portfolio_uses_branch_research_selection(portfolio)
            )
            if (
                predecessor_is_branch_conditioned
                and self.branch_basis_index_ref is None
            ):
                raise ArchitecturalRevisionError(
                    "branch-selected predecessor requires its persisted RAG index"
                )
            if self.branch_basis_index_ref is not None:
                if not isinstance(
                    self.repository,
                    FilesystemProjectRepository,
                ):
                    raise ArchitecturalRevisionError(
                        "branch research requires the P036 project repository"
                )
                assert self.expected_branch_scope_digest is not None
                assert isinstance(
                    self.branch_decision_universe,
                    DecisionUniverseRevision,
                )
                assert isinstance(
                    self.branch_evidence_policy,
                    EvidenceSufficiencyPolicy,
                )
                assert isinstance(
                    self.branch_universe_closure,
                    DecisionUniverseClosure,
                )
                assert isinstance(
                    self.branch_evidence_sufficiency,
                    EvidenceSufficiencyReceipt,
                )
                assert isinstance(
                    self.branch_research_frontier,
                    ResearchFrontier,
                )
                branch_archive = BranchResearchArchive(
                    self.repository,
                    run=run,
                )
                branch_index = branch_archive.load_index(
                    self.branch_basis_index_ref,
                    expected_scope_digest=(
                        self.expected_branch_scope_digest
                    ),
                )
                _require_branch_index_matches_selected_predecessor(
                    branch_index=branch_index,
                    portfolio=portfolio,
                    selected_schematic=predecessor_state.selected_schematic,
                    selection_ref=self.selection_ref,
                    expected_scope_digest=(
                        self.expected_branch_scope_digest
                    ),
                )
                branch_scope_digest = branch_index.scope.scope_digest
                branch_context = branch_archive.load_decision_context(
                    self.branch_basis_index_ref,
                    expected_scope_digest=branch_scope_digest,
                    decision_refs=self.branch_decision_refs,
                    universe=self.branch_decision_universe,
                    policy=self.branch_evidence_policy,
                    closure=self.branch_universe_closure,
                    sufficiency=self.branch_evidence_sufficiency,
                    frontier=self.branch_research_frontier,
                )
            authored = None
            authoring_refs: list[ProjectRecordRef] = []
            repair_feedback = None
            for attempt_index in range(2):
                authored = await _author_semantic_spatial_option(
                    self.provider,
                    request_id=(
                        f"architectural-revision-{run.run_id}-"
                        f"{receipt.receipt_digest[:12]}-{attempt_index:02d}"
                    ),
                    state=self.context.state,
                    maturity=self.context.maturity,
                    phase_gate=self.context.phase_gate,
                    program=self.context.program,
                    site_context=self.context.site_context,
                    build_policy=self.context.build_policy,
                    repair_feedback=repair_feedback,
                    revision_context=revision_context,
                    branch_research_context=branch_context,
                    expected_branch_scope_digest=(
                        branch_scope_digest
                    ),
                )
                authoring_ref = self.repository.put_json(
                    run=run,
                    destination=destination,
                    record_kind=(
                        "architectural-semantic-revision-authoring-"
                        f"{attempt_index:02d}"
                    ),
                    payload=authored.receipt.to_dict(),
                )
                authoring_refs.append(authoring_ref)
                if (
                    authored.receipt.status
                    is SemanticSpatialAuthoringStatus.ACCEPTED
                    and authored.proposal is not None
                    and authored.option is not None
                ):
                    break
                if attempt_index == 0:
                    try:
                        repair_feedback = semantic_spatial_repair_feedback(
                            authored
                        )
                    except (TypeError, ValueError):
                        repair_feedback = None
                    if repair_feedback is not None:
                        continue
                raise ArchitecturalRevisionCompilationError(
                    "semantic architectural revision did not produce a valid "
                    f"successor: {authored.receipt.error_code}; "
                    f"{authored.receipt.message}",
                    error_code=(
                        authored.receipt.error_code
                        or "architectural_revision.semantic_rejected"
                    ),
                )
            assert authored is not None
            assert authored.proposal is not None
            assert authored.option is not None
            if authored.proposal.option_id != predecessor_spatial.option_id:
                raise ArchitecturalRevisionCompilationError(
                    "semantic successor changed the selected option identity",
                    error_code="architectural_revision.option_identity_changed",
                )
            if authored.proposal == predecessor_spatial:
                raise ArchitecturalRevisionCompilationError(
                    "semantic successor is identical to the failed predecessor",
                    error_code="architectural_revision.no_semantic_change",
                )
            revised_spatial_ref = self.repository.put_json(
                run=run,
                destination=destination,
                record_kind="architectural-revised-spatial-option",
                payload=authored.proposal.to_dict(),
            )
            evidence_refs = tuple(
                sorted(
                    {
                        feedback_ref.uri,
                        self.architectural_contract_ref.uri,
                        self.architectural_receipt_ref.uri,
                        revised_spatial_ref.uri,
                        *(item.uri for item in authoring_refs),
                    }
                )
            )
            suffix = receipt.receipt_digest[:16]
            portfolio = park_branch(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                branch_id=predecessor_spatial.option_id,
                authority_id=self.context.architect_id,
                decision_ref=self.architectural_receipt_ref.uri,
                rationale="Release the failed selected revision for bounded correction.",
                evidence_refs=evidence_refs,
                transition_id=f"release-architectural-{suffix}",
            )
            portfolio = revise_branch(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                branch_id=predecessor_spatial.option_id,
                revision_id=f"architectural-revision-{suffix}",
                option=authored.option,
                authority_id=self.context.architect_id,
                decision_ref=self.architectural_receipt_ref.uri,
                rationale=(
                    "Adopt the complete model-authored semantic successor for "
                    "exact predecessor compilation and renewed validation."
                ),
                evidence_refs=evidence_refs,
                transition_id=f"revise-architectural-{suffix}",
            )
            portfolio = select_branch(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                branch_id=predecessor_spatial.option_id,
                authority_id=self.context.architect_id,
                decision_ref=self.architectural_receipt_ref.uri,
                rationale=(
                    "Select the bounded successor only for compilation; P060 "
                    "must independently evaluate the new artifact."
                ),
                evidence_refs=evidence_refs,
                transition_id=f"reselect-architectural-{suffix}",
            )
            handoff = compile_selected_branch_handoff(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                expected_revision_digest=(
                    portfolio.selected_branch.head.revision_digest
                ),
            )
            current_state = _replace_selected_schematic(
                predecessor_state,
                handoff,
                evidence_refs=evidence_refs,
                receipt_suffix=suffix,
            )
            self.repository.put_json(
                run=run,
                destination=destination,
                record_kind="architectural-revision-portfolio",
                payload=portfolio.to_dict(),
            )
            try:
                component_transition = compile_component_transition(
                    predecessor_spatial,
                    authored.proposal,
                )
            except (TypeError, ValueError) as exc:
                raise ArchitecturalRevisionCompilationError(
                    "model-authored semantic successor does not form an exact "
                    f"component transition: {type(exc).__name__}: {exc}",
                    error_code=(
                        "architectural_revision.component_transition_rejected"
                    ),
                ) from exc
            geometry = await produce_geometry_program_proposal(
                self.repository,
                self.provider,
                run=run,
                destination=destination,
                spatial_option_ref=revised_spatial_ref,
                design_state=current_state,
                required_commitment_refs=(
                    self.context.required_commitment_refs
                ),
                provider_identity=self.geometry_provider_identity,
                policy=self.geometry_policy,
                template_refs=self.template_refs,
                prior_program=predecessor_program,
                required_geometry_component_ids=(
                    component_transition.changed_component_ids
                ),
                realization_requirements=feedback.realization_requirements,
                initial_repair_issues=feedback.geometry_issues,
            )
            if (
                geometry.status is not GeometryProposalStatus.ACCEPTED
                or geometry.proposal is None
                or geometry.program is None
                or geometry.proposal_ref is None
            ):
                issue_details: list[str] = []
                for round_ref in geometry.round_refs:
                    round_payload = self.repository.load_json(round_ref)
                    round_issues = round_payload.get("issues", [])
                    if isinstance(round_issues, list):
                        issue_details.extend(
                            f"{item.get('code', 'unknown')}:"
                            f"{item.get('detail', '')}"
                            for item in round_issues
                            if isinstance(item, Mapping)
                        )
                raise ArchitecturalRevisionCompilationError(
                    "geometry revision exhausted without an accepted exact "
                    f"predecessor program: {tuple(issue_details)}",
                    error_code="architectural_revision.geometry_rejected",
                )
            lifecycle = compile_semantic_geometry_lifecycle(
                transaction_id=f"architectural-revision-{suffix}",
                predecessor_state=predecessor_state,
                current_state=current_state,
                predecessor_proposal=predecessor_spatial,
                current_proposal=authored.proposal,
                prior_program=predecessor_program,
                geometry_proposal=geometry.proposal,
                active_commitment_refs=self.context.required_commitment_refs,
                revalidated_component_ids=_unchanged_invalidated_descendants(
                    component_transition,
                    predecessor_spatial,
                    authored.proposal,
                    predecessor_program,
                    geometry.program,
                ),
            )
            if (
                lifecycle.receipt.status
                is not SemanticGeometryLifecycleStatus.COMPILED
                or lifecycle.geometry_program is None
            ):
                details = tuple(
                    f"{item.code.value}:{item.subject_id}"
                    for item in lifecycle.receipt.issues
                )
                raise ArchitecturalRevisionCompilationError(
                    f"semantic-geometry successor was rejected: {details}",
                    error_code="architectural_revision.lifecycle_rejected",
                )
            realization = realize_geometry(
                lifecycle.geometry_program,
                workspace_id=f"sandbox-{run.run_id}-revision-{suffix}",
            )
            if (
                realization.receipt.status is not RealizationStatus.REALIZED
                or realization.scene is None
            ):
                raise ArchitecturalRevisionCompilationError(
                    "revised neutral geometry could not be realized",
                    error_code="architectural_revision.realization_failed",
                )
            self.repository.put_json(
                run=run,
                destination=destination,
                record_kind="architectural-revision-sandbox-scene",
                payload=realization.scene.to_dict(),
            )
            self.repository.put_json(
                run=run,
                destination=destination,
                record_kind="architectural-revision-sandbox-realization",
                payload=realization.receipt.to_dict(),
            )
        except ArchitecturalRevisionCompilationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchitecturalRevisionCompilationError(
                f"architectural revision inputs or successor are invalid: "
                f"{type(exc).__name__}: {exc}",
                error_code="architectural_revision.invalid",
            ) from exc
        envelopes = self.evidence_collector.since(cursor)
        if not envelopes:
            raise ArchitecturalRevisionCompilationError(
                "P053 produced no validated invocation evidence",
                error_code="architectural_revision.missing_provider_evidence",
            )
        return CompiledProductionStep(
            current_design_state=current_state,
            lifecycle=lifecycle,
            invocation_envelopes=envelopes,
        )

    def _validate_exact_inputs(
        self,
        run: RunRef,
        predecessor_state: DevelopedDesignState,
        predecessor_program: object,
        option_set: SchematicOptionSet,
        predecessor_spatial: SpatialOptionProposal,
        contract: ArchitecturalUsabilityContract,
        receipt: ArchitecturalUsabilityReceipt,
    ) -> None:
        exact = (run.project_id, run.run_id, run.base)
        if (
            (predecessor_state.project_id, predecessor_state.run_id, predecessor_state.base)
            != exact
            or (option_set.project_id, option_set.run_id, option_set.base) != exact
        ):
            raise ArchitecturalRevisionError(
                "architectural revision records do not share one exact run base"
            )
        if predecessor_state.selected_schematic.option.proposal != predecessor_spatial:
            raise ArchitecturalRevisionError(
                "predecessor developed state does not select the supplied spatial tree"
            )
        if (
            predecessor_program.proposal.design_state_digest
            != predecessor_state.state_digest
            or predecessor_program.program_digest
            != contract.context.geometry_program_digest
            or receipt.geometry_program_digest != predecessor_program.program_digest
        ):
            raise ArchitecturalRevisionError(
                "predecessor geometry is stale for the failed P060 receipt"
            )

    def _reconstruct_predecessor_portfolio(
        self,
        *,
        run: RunRef,
        raw_request: ProjectRecordRef,
        option_set: SchematicOptionSet,
        predecessor_state: DevelopedDesignState,
    ):
        if self.predecessor_portfolio_ref is not None:
            portfolio = DesignOptionPortfolio.from_dict(
                self.repository.load_json(self.predecessor_portfolio_ref)
            )
            selected = portfolio.selected_branch
            if (
                portfolio.run != run
                or portfolio.portfolio_id
                != predecessor_state.selected_schematic.portfolio_id
                or portfolio.source_option_set_digest
                != option_set.option_set_digest
                or portfolio.operational_state_digest
                != self.context.state.state_digest
                or selected is None
                or selected.branch_id
                != predecessor_state.selected_schematic.option.option_id
            ):
                raise ArchitecturalRevisionError(
                    "retained predecessor portfolio is stale or names another branch"
                )
            handoff = compile_selected_branch_handoff(
                portfolio,
                expected_portfolio_digest=portfolio.portfolio_digest,
                expected_revision_digest=selected.head.revision_digest,
            )
            if (
                SelectedSchematicInput.from_handoff(handoff)
                != predecessor_state.selected_schematic
            ):
                raise ArchitecturalRevisionError(
                    "retained predecessor portfolio does not reproduce the current selection"
                )
            return portfolio

        selection = _selected_receipt_fields(
            self.repository.load_json(self.selection_ref),
            option_set=option_set,
            expected_option_id=(
                predecessor_state.selected_schematic.option.option_id
            ),
        )
        if predecessor_state.selected_schematic.selection_decision_ref != self.selection_ref.uri:
            raise ArchitecturalRevisionError(
                "selected schematic does not bind the supplied selection record"
            )
        portfolio = initialize_design_portfolio(
            option_set,
            portfolio_id=predecessor_state.selected_schematic.portfolio_id,
            selection_policy=SelectionPolicy(
                authority_ids=(self.context.architect_id,),
                source_refs=tuple(sorted((raw_request.uri, self.context_ref.uri))),
            ),
            architect_id=self.context.architect_id,
        )
        portfolio = select_branch(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            branch_id=selection[0],
            authority_id=self.context.architect_id,
            decision_ref=self.selection_ref.uri,
            rationale=selection[1],
            evidence_refs=(self.selection_ref.uri,),
            transition_id=(
                predecessor_state.selected_schematic.selection_transition_id
            ),
        )
        handoff = compile_selected_branch_handoff(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            expected_revision_digest=portfolio.selected_branch.head.revision_digest,
        )
        if SelectedSchematicInput.from_handoff(handoff) != predecessor_state.selected_schematic:
            raise ArchitecturalRevisionError(
                "predecessor portfolio reconstruction is not exact"
            )
        return portfolio


def _unchanged_invalidated_descendants(
    transition: ComponentTransitionReceipt,
    predecessor_proposal: SpatialOptionProposal,
    current_proposal: SpatialOptionProposal,
    predecessor_program: CompiledGeometryProgram,
    current_program: CompiledGeometryProgram,
) -> tuple[str, ...]:
    """Revalidate only untouched descendants invalidated by an ancestor edit."""

    candidates = (
        set(transition.invalidated_component_ids)
        - set(transition.changed_component_ids)
    )
    before = _owned_object_digests(predecessor_program)
    after = _owned_object_digests(current_program)
    revalidated = {
        component_id
        for component_id in candidates
        if _subtree_object_digests(
            component_id,
            predecessor_proposal,
            before,
        )
        == _subtree_object_digests(
            component_id,
            current_proposal,
            after,
        )
    }
    return tuple(sorted(revalidated))


def _owned_object_digests(
    program: CompiledGeometryProgram,
) -> dict[str, dict[str, str]]:
    object_digests = {
        item.object_id: item.object_digest for item in program.objects
    }
    owned: dict[str, dict[str, str]] = {}
    for binding in program.proposal.semantic_bindings:
        component_objects = owned.setdefault(binding.component_id, {})
        for object_id in binding.object_ids:
            component_objects[object_id] = object_digests[object_id]
    return owned


def _subtree_object_digests(
    component_id: str,
    proposal: SpatialOptionProposal,
    direct: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    components = {item.component_id: item for item in proposal.components}
    if component_id not in components:
        return {}
    descendants = {component_id}
    changed = True
    while changed:
        changed = False
        for item in components.values():
            if (
                item.parent_component_id in descendants
                and item.component_id not in descendants
            ):
                descendants.add(item.component_id)
                changed = True
    return {
        object_id: digest
        for current_id in sorted(descendants)
        for object_id, digest in direct.get(current_id, {}).items()
    }


def _replace_selected_schematic(
    state: DevelopedDesignState,
    handoff: object,
    *,
    evidence_refs: tuple[str, ...],
    receipt_suffix: str,
) -> DevelopedDesignState:
    replacement = SelectedSchematicInput.from_handoff(handoff)
    prior_refs = tuple(
        sorted((state.selected_schematic.ref, state.selected_schematic.option.ref))
    )
    impacted = tuple(
        item for item in state.dependencies if item.source_ref in set(prior_refs)
    )
    if impacted:
        invalidated = invalidate_developed_design(
            state,
            changed_schematic_refs=prior_refs,
            receipt_id=f"architectural-invalidation-{receipt_suffix}",
            evidence_refs=evidence_refs,
        )
        return resume_development_after_selection(
            invalidated,
            replacement_handoff=handoff,
        )
    if state.components or state.dependencies:
        raise ArchitecturalRevisionError(
            "developed detail lacks an explicit schematic dependency for revision"
        )
    rebind = {
        state.selected_schematic.ref: replacement.ref,
        state.selected_schematic.option.ref: replacement.option.ref,
    }
    obligations = tuple(
        replace(
            item,
            dependency_refs=tuple(
                rebind.get(ref, ref) for ref in item.dependency_refs
            ),
        )
        for item in state.obligations
    )
    return replace(
        state,
        selected_schematic=replacement,
        obligations=obligations,
    )


def _selected_receipt_fields(
    value: object,
    *,
    option_set: SchematicOptionSet,
    expected_option_id: str,
) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("selection receipt must be an object")
    expected = {
        "schema", "selection_id", "status", "request", "model_receipt",
        "option_set_digest", "selected_option_id", "rationale", "error_code",
        "proposal_only", "selection_authority", "persistence_authority",
        "canonical_write_authority",
    }
    if set(value) != expected or value.get("schema") != "SchematicSelectionReceipt@1":
        raise ArchitecturalRevisionError("selection receipt schema drifted")
    if (
        value.get("status") != "selected"
        or value.get("option_set_digest") != option_set.option_set_digest
        or value.get("selected_option_id") != expected_option_id
        or value.get("error_code") is not None
        or value.get("proposal_only") is not True
    ):
        raise ArchitecturalRevisionError("selection receipt is stale or authoritative")
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ArchitecturalRevisionError("selection rationale is missing")
    return expected_option_id, rationale


def _production_record_content(
    value: object,
    *,
    run: RunRef,
    expected_role: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("production value must be an object")
    if value.get("schema") != "ProductionTransitionRecord@1":
        return value
    expected = {
        "schema", "project_id", "run_id", "base", "role",
        "semantic_digest", "content_sha256", "content",
        "canonical_write_authority",
    }
    base = {
        "project_id": run.base.project_id,
        "version": run.base.version,
        "state_sha256": run.base.require_digest(),
    }
    if (
        set(value) != expected
        or value.get("project_id") != run.project_id
        or value.get("run_id") != run.run_id
        or value.get("base") != base
        or value.get("role") != expected_role
    ):
        raise ArchitecturalRevisionError(
            "production transition record identity or authority drifted"
        )
    content = value.get("content")
    if not isinstance(content, Mapping):
        raise TypeError("production transition content must be an object")
    if (
        canonical_digest(content) != value.get("content_sha256")
        or digest_value(content) != value.get("semantic_digest")
    ):
        raise ArchitecturalRevisionError(
            "production transition record content digest drifted"
        )
    return content


def architectural_revision_feedback_digest(
    feedback: ArchitecturalRevisionFeedback,
) -> str:
    """Expose deterministic identity for persisted feedback records."""

    if not isinstance(feedback, ArchitecturalRevisionFeedback):
        raise TypeError("feedback must be ArchitecturalRevisionFeedback")
    return canonical_digest(feedback.to_dict())
