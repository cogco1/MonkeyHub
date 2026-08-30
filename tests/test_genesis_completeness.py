from __future__ import annotations

import copy
import hashlib
import inspect
import json
import unittest
from dataclasses import replace

from archflow.control.baseline import StageBaselineLevel
from archflow.control.genesis_completeness import (
    GenesisSemanticCompletenessError,
    GenesisSemanticDenominator,
    SemanticCompletenessReceipt,
    SemanticDenominatorSourceKind,
    SemanticSystemBasis,
    SemanticSystemDeclaration,
    SemanticSystemDisposition,
    SemanticSystemFindingStatus,
)
from archflow.control.profile import StageRequirementProfileBinding
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
)
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
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
from archflow.runtime.genesis_completeness import (
    GenesisSemanticCompletenessCompilationError,
    bridge_semantic_completeness_check,
    compile_genesis_semantic_completeness,
    compile_genesis_semantic_denominator,
    compile_semantic_denominator_inheritance,
)
from archflow.research.adoption import PrecedentAdoption, PrecedentFact
from archflow.research.branch import (
    BranchEvidenceSnapshot,
    BranchPrecedentAdoption,
    BranchResearchScope,
)
from archflow.state.design_maturity import DesignPhase
from archflow.state.operational_state import OperationalMarkovState
from archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)
from archflow.validation.contracts import CheckStatus


SYSTEMS = (
    "circulation",
    "enclosure",
    "load-bearing-system",
    "material",
    "opening",
    "roof",
)
GENESIS_STAGE_ID = DesignPhase.RESEARCH_BRIEF.value


def _branch(
    *,
    project_id: str = "genesis-fixture",
    run_id: str = "design-001",
    branch_id: str = "option-a",
    epoch: int = 0,
    base_digest: str = "a" * 64,
) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=run_id,
            base=ProjectVersionRef(
                project_id=project_id,
                version=0,
                state_sha256=base_digest,
            ),
        ),
        branch_id=branch_id,
        epoch=epoch,
    )


def _state(
    branch: BranchRef,
    *,
    phase: str = GENESIS_STAGE_ID,
) -> OperationalMarkovState:
    return OperationalMarkovState(
        branch=branch,
        compiler_version="genesis-test-v1",
        phase=phase,
    )


def _record(
    project_id: str,
    name: str,
    digest_char: str,
) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=project_id,
        relative_path=f"input/{name}.json",
        sha256=digest_char * 64,
    )


def _json_record_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256((encoded + "\n").encode("utf-8")).hexdigest()


def _branch_record(
    branch: BranchRef,
    name: str,
    digest_char: str,
) -> ProjectRecordRef:
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=(
            f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
            f"{name}.json"
        ),
        sha256=digest_char * 64,
    )


def _inventory(
    branch: BranchRef,
    *,
    stage_id: str,
    component_kinds: dict[str, str],
    inventory_id: str = "stage-subject-inventory",
) -> StageSubjectInventory:
    entries = [
        StageSubjectInventoryEntry(
            component_id="building-root",
            identity_ref="design-component:building-root",
            parent_component_id=None,
            semantic_kind="building",
            component_digest="1" * 64,
            geometry_object_ids=(),
            binding_ids=(),
            role_obligations=(),
        )
    ]
    for index, (component_id, semantic_kind) in enumerate(
        sorted(component_kinds.items()),
        start=2,
    ):
        digest_char = format(index % 16, "x")
        entries.append(
            StageSubjectInventoryEntry(
                component_id=component_id,
                identity_ref=f"design-component:{component_id}",
                parent_component_id="building-root",
                semantic_kind=semantic_kind,
                component_digest=digest_char * 64,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=(),
            )
        )
    return StageSubjectInventory(
        inventory_id=inventory_id,
        branch=branch,
        stage_id=stage_id,
        stage_subject_ref="candidate:building",
        stage_subject_digest="b" * 64,
        baseline_level=StageBaselineLevel.PRE_GEOMETRY,
        component_proposal_ref=_branch_record(
            branch,
            f"{stage_id}-component-proposal",
            "c",
        ),
        component_proposal_digest="d" * 64,
        component_index_ref=_branch_record(
            branch,
            f"{stage_id}-component-index",
            "e",
        ),
        component_index_digest="f" * 64,
        entries=tuple(entries),
    )


def _profile(
    state: OperationalMarkovState,
    *,
    stage_id: str,
    denominator_refs: tuple[str, ...],
    profile_id: str = "genesis-profile",
) -> StageRequirementProfile:
    authority_ref = _record(
        state.branch.run.project_id,
        "semantic-denominator-authority",
        "8",
    )
    return StageRequirementProfile(
        profile_id=profile_id,
        typology_id="explicit-typology",
        stage_id=stage_id,
        branch=state.branch,
        predecessor_state_digest=state.state_digest,
        scope_digest="9" * 64,
        stage_subject_ref="candidate:building",
        requirements=(
            StageCheckRequirement(
                requirement_id="semantic-system-completeness",
                checker_id="genesis-semantic-completeness",
                target_kind=RequirementTargetKind.STAGE,
                basis_mode=RequirementBasisMode.AUTHORITY_BOUND,
                denominator_refs=denominator_refs,
                required_authority_refs=(authority_ref.uri,),
                allow_not_applicable=True,
            ),
        ),
    )


def _binding(
    profile: StageRequirementProfile,
    inventory: StageSubjectInventory,
    *,
    extra_authority_refs: tuple[ProjectRecordRef, ...] = (),
) -> StageRequirementProfileBinding:
    branch = profile.branch
    prefix = (
        f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records"
    )
    return StageRequirementProfileBinding(
        binding_id=f"{profile.stage_id}-profile-binding",
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        branch=branch,
        stage_id=profile.stage_id,
        stage_subject_ref=profile.stage_subject_ref,
        subject_digest=inventory.stage_subject_digest,
        profile_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/{profile.stage_id}-profile.json",
            sha256=profile.profile_digest,
        ),
        stage_subject_inventory_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/{profile.stage_id}-inventory.json",
            sha256="0" * 64,
        ),
        stage_subject_inventory_digest=inventory.inventory_digest,
        authority_refs=(
            _record(
                branch.run.project_id,
                "semantic-denominator-authority",
                "8",
            ),
            *extra_authority_refs,
        ),
    )


def _basis(
    project_id: str,
    system_id: str,
    *,
    kind: SemanticDenominatorSourceKind = SemanticDenominatorSourceKind.BRIEF,
    semantic_kinds: tuple[str, ...] | None = None,
    suffix: str = "brief",
    research_scope: BranchResearchScope | None = None,
    adoption: BranchPrecedentAdoption | None = None,
    applicability: ClaimApplicability | None = None,
    evidence_snapshot: BranchEvidenceSnapshot | None = None,
    evidence_ref: ProjectRecordRef | None = None,
    authority_ref: ProjectRecordRef | None = None,
) -> SemanticSystemBasis:
    return SemanticSystemBasis(
        basis_id=f"{system_id}-{suffix}",
        system_id=system_id,
        system_ref=f"semantic-system:{system_id}",
        semantic_kinds=(system_id,) if semantic_kinds is None else semantic_kinds,
        source_kind=kind,
        evidence_ref=(
            _record(project_id, f"{system_id}-{suffix}", "2")
            if evidence_ref is None
            else evidence_ref
        ),
        authority_ref=(
            _record(
                project_id,
                f"{system_id}-{suffix}-authority",
                "3",
            )
            if authority_ref is None
            else authority_ref
        ),
        evidence_snapshot=evidence_snapshot,
        research_scope=research_scope,
        adoption=adoption,
        applicability=applicability,
    )


def _typed_rag_records(
    state: OperationalMarkovState,
    system_id: str,
    *,
    disposition: ApplicabilityDisposition = ApplicabilityDisposition.APPLICABLE,
    exact_source: bool = True,
    exact_authority: bool = True,
):
    project_id = state.branch.run.project_id
    authority_ref = _branch_record(
        state.branch,
        f"branch-adoption-{system_id}",
        "3",
    )
    selection_ref = ProjectRecordRef(
        project_id=project_id,
        relative_path=(
            f"runs/{state.branch.run.run_id}/records/"
            "branch-selection-rag.json"
        ),
        sha256="4" * 64,
    )
    option_set_ref = ProjectRecordRef(
        project_id=project_id,
        relative_path=(
            f"runs/{state.branch.run.run_id}/records/"
            "branch-source-option-set-rag.json"
        ),
        sha256="5" * 64,
    )
    state_ref = ProjectRecordRef(
        project_id=project_id,
        relative_path=(
            f"runs/{state.branch.run.run_id}/records/"
            "branch-source-state-rag.json"
        ),
        sha256="6" * 64,
    )
    scope = BranchResearchScope(
        scope_id=f"{system_id}-rag-scope",
        run=state.branch.run,
        portfolio_id="rag-portfolio",
        portfolio_digest="7" * 64,
        operational_state_digest=state.state_digest,
        source_branch=state.branch,
        branch_id=state.branch.branch_id,
        branch_revision_id="rag-revision",
        branch_revision_digest=state.state_digest,
        predecessor_scope_digest=None,
        selection_decision_digest="8" * 64,
        selection_record_ref=selection_ref,
        source_option_set_ref=option_set_ref,
        source_state_ref=state_ref,
        active_decision_refs=(f"semantic-system:{system_id}",),
        branch_search_terms=(system_id,),
        excluded_search_terms=(),
        domain_allowlist=("example.org",),
        context_refs=tuple(
            sorted(
                (
                    selection_ref.uri,
                    option_set_ref.uri,
                    state_ref.uri,
                )
            )
        ),
    )
    quote = f"Evidence requires {system_id}."
    text_digest = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    snapshot = BranchEvidenceSnapshot(
        scope_digest=scope.scope_digest,
        branch_id=scope.branch_id,
        branch_revision_digest=scope.branch_revision_digest,
        query_id=f"{system_id}-rag-query",
        query_digest="a" * 64,
        requested_url=f"https://example.org/{system_id}/request",
        final_url=f"https://example.org/{system_id}/source",
        retrieved_at="2026-08-30T00:00:00Z",
        content_sha256=hashlib.sha256(
            f"raw:{quote}".encode("utf-8")
        ).hexdigest(),
        content_bytes=len(quote.encode("utf-8")),
        text=quote,
        text_sha256=text_digest,
    )
    source_ref = ProjectRecordRef(
        project_id=project_id,
        relative_path=(
            f"runs/{state.branch.run.run_id}/branches/"
            f"{state.branch.branch_id}/records/"
            f"branch-snapshot-{system_id}.json"
        ),
        sha256=_json_record_digest(snapshot.to_dict()),
    )
    fact = PrecedentFact(
        fact_id=f"{system_id}-rag-fact",
        statement=quote,
        quote=quote,
        quote_start=0,
        quote_end=len(quote),
        snapshot_ref=source_ref.uri,
        snapshot_text_sha256=text_digest,
        annotator="test-harness",
        annotator_is_harness=True,
        topic=ConstructabilityTopic.SUPPORT,
        strength=PolicyConstraintStrength.HARD,
        decision_refs=(f"semantic-system:{system_id}",),
    )
    adoption = BranchPrecedentAdoption(
        query_id=f"{system_id}-rag-query",
        query_digest="a" * 64,
        scope_digest=scope.scope_digest,
        branch_id=scope.branch_id,
        branch_revision_digest=scope.branch_revision_digest,
        adoption=PrecedentAdoption(
            adoption_id=f"{system_id}-rag-adoption",
            authority_id=authority_ref.uri,
            adopted_at="2026-08-30T00:00:00Z",
            facts=(fact,),
        ),
    )
    adoption_ref = (
        f"precedent-adoption:{adoption.adoption_id}:"
        f"{adoption.adoption.adoption_digest}"
    )
    claim = EvidenceClaimBinding(
        binding_id=f"{system_id}-rag-claim",
        branch=state.branch,
        scope_digest=scope.scope_digest,
        obligation_id=f"{system_id}-existence",
        target_ref=f"semantic-system:{system_id}",
        fact_ref=f"precedent-fact:{fact.fact_id}",
        source_ref=source_ref.uri,
        source_family_ref="source-family:precedent",
        claim_key=f"claim:{system_id}-existence",
        position_key="position:required",
        authority_ref=authority_ref.uri,
    )
    allowed_uses = (
        (AllowedClaimUse.ELEMENT_EXISTENCE,)
        if disposition is ApplicabilityDisposition.APPLICABLE
        else ()
    )
    applicability = ClaimApplicability.from_claim(
        claim,
        applicability_id=f"{system_id}-rag-applicability",
        target_kind=ApplicabilityTargetKind.COMPONENT,
        target_ref=f"semantic-system:{system_id}",
        disposition=disposition,
        allowed_uses=allowed_uses,
        authority_refs=(
            authority_ref.uri
            if exact_authority
            else "project://genesis-fixture/input/unrelated-authority.json",
        ),
        source_refs=tuple(
            sorted(
                (
                    adoption_ref,
                    source_ref.uri
                    if exact_source
                    else "project://genesis-fixture/input/unrelated-source.json",
                )
            )
        ),
        rationale="Exact adopted applicability for semantic completeness.",
        invalidates_on=("state:phase",),
    )
    return (
        source_ref,
        authority_ref,
        scope,
        snapshot,
        adoption,
        applicability,
    )


def _fixture(
    *,
    systems: tuple[str, ...] = SYSTEMS,
    component_kinds: dict[str, str] | None = None,
):
    branch = _branch()
    state = _state(branch)
    refs = tuple(sorted(f"semantic-system:{item}" for item in systems))
    profile = _profile(
        state,
        stage_id=GENESIS_STAGE_ID,
        denominator_refs=refs,
    )
    inventory = _inventory(
        branch,
        stage_id=GENESIS_STAGE_ID,
        component_kinds=(
            {
                "circulation-core": "circulation",
                "enclosure-shell": "enclosure",
                "opening-set": "opening",
                "roof-system": "roof",
                "support-system": "load-bearing-system",
            }
            if component_kinds is None
            else component_kinds
        ),
    )
    bases = tuple(_basis(branch.run.project_id, item) for item in systems)
    na_evidence = _record(branch.run.project_id, "material-na-evidence", "6")
    na_authority = _record(branch.run.project_id, "material-na-authority", "7")
    evidence_policy = (
        {"semantic-system:material": (na_evidence,)}
        if "material" in systems
        else {}
    )
    authority_policy = (
        {"semantic-system:material": (na_authority,)}
        if "material" in systems
        else {}
    )
    profile_binding = _binding(
        profile,
        inventory,
        extra_authority_refs=(
            *(item.authority_ref for item in bases),
            *((na_authority,) if "material" in systems else ()),
        ),
    )
    denominator = compile_genesis_semantic_denominator(
        denominator_id="genesis-semantic-denominator",
        state=state,
        profile=profile,
        profile_binding=profile_binding,
        inventory=inventory,
        profile_requirement_id="semantic-system-completeness",
        bases=bases,
        not_applicable_evidence_refs=evidence_policy,
        not_applicable_authority_refs=authority_policy,
    )
    declarations = []
    component_by_kind = {
        entry.semantic_kind: entry.identity_ref for entry in inventory.entries
    }
    for system in systems:
        if system == "material":
            declarations.append(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:material",
                    disposition=SemanticSystemDisposition.NOT_APPLICABLE,
                    evidence_refs=(na_evidence,),
                    authority_refs=(na_authority,),
                )
            )
        elif system in component_by_kind:
            declarations.append(
                SemanticSystemDeclaration(
                    system_ref=f"semantic-system:{system}",
                    disposition=SemanticSystemDisposition.PRESENT,
                    component_refs=(component_by_kind[system],),
                )
            )
    return (
        state,
        profile,
        profile_binding,
        inventory,
        bases,
        denominator,
        tuple(declarations),
        na_evidence,
        na_authority,
    )


def _compile_passing_genesis():
    fixture = _fixture()
    (
        state,
        profile,
        profile_binding,
        inventory,
        _,
        denominator,
        declarations,
        _,
        _,
    ) = fixture
    receipt = compile_genesis_semantic_completeness(
        receipt_id="genesis-semantic-receipt",
        denominator=denominator,
        state=state,
        profile=profile,
        profile_binding=profile_binding,
        inventory=inventory,
        declarations=declarations,
    )
    if receipt.status is not CheckStatus.PASS:
        raise AssertionError("test fixture must compile passing genesis evidence")
    return (*fixture, receipt)


class GenesisSemanticCompletenessTests(unittest.TestCase):
    def test_explicit_merge_is_idempotent_round_trippable_and_authority_free(
        self,
    ) -> None:
        (
            state,
            profile,
            profile_binding,
            inventory,
            bases,
            denominator,
            declarations,
            na_evidence,
            na_authority,
        ) = _fixture()
        repeated = compile_genesis_semantic_denominator(
            denominator_id=denominator.denominator_id,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            profile_requirement_id=denominator.profile_requirement_id,
            bases=tuple(reversed(bases)),
            not_applicable_evidence_refs={
                "semantic-system:material": (na_evidence,)
            },
            not_applicable_authority_refs={
                "semantic-system:material": (na_authority,)
            },
        )
        self.assertEqual(denominator, repeated)
        self.assertEqual(
            denominator,
            GenesisSemanticDenominator.from_dict(denominator.to_dict()),
        )

        first = compile_genesis_semantic_completeness(
            receipt_id="genesis-semantic-receipt",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=declarations,
        )
        second = compile_genesis_semantic_completeness(
            receipt_id="genesis-semantic-receipt",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=tuple(reversed(declarations)),
        )
        self.assertEqual(first, second)
        self.assertEqual(first.receipt_digest, second.receipt_digest)
        self.assertEqual(CheckStatus.PASS, first.status)
        self.assertEqual(
            first,
            SemanticCompletenessReceipt.from_dict(first.to_dict()),
        )
        for field in (
            "design_authority",
            "stage_acceptance_authority",
            "persistence_authority",
            "canonical_write_authority",
        ):
            self.assertFalse(first.to_dict()[field])

    def test_exact_bridge_is_the_only_stage_closure_surface(self) -> None:
        (
            state,
            profile,
            profile_binding,
            inventory,
            _,
            denominator,
            declarations,
            na_evidence,
            na_authority,
        ) = _fixture()
        source = compile_genesis_semantic_completeness(
            receipt_id="genesis-semantic-source",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=declarations,
        )
        check = bridge_semantic_completeness_check(
            denominator=denominator,
            source_receipt=source,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
        )
        closure = compile_composite_stage_closure(
            profile,
            subject_digest=inventory.stage_subject_digest,
            check_receipts=(check,),
        )
        self.assertIs(check.status, CheckStatus.PASS)
        self.assertEqual(profile.requirements[0].requirement_id, check.check_id)
        self.assertEqual(denominator.system_refs, check.coverage_denominator)
        self.assertIn(na_evidence.uri, check.source_refs)
        self.assertIn(na_authority.uri, check.authority_refs)
        self.assertIs(closure.status, StageClosureStatus.SATISFIED)

        tampered_binding = replace(
            profile_binding,
            stage_subject_inventory_digest="1" * 64,
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "exact inputs",
        ):
            bridge_semantic_completeness_check(
                denominator=denominator,
                source_receipt=source,
                profile=profile,
                profile_binding=tampered_binding,
                inventory=inventory,
            )

    def test_raw_or_non_applicable_rag_cannot_populate_denominator(self) -> None:
        state, profile, _, inventory, *_ = _fixture(systems=("roof",))
        with self.assertRaisesRegex(TypeError, "research_scope"):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="raw-rag",
            )

        (
            na_evidence,
            na_authority,
            scope,
            snapshot,
            adoption,
            not_applicable,
        ) = _typed_rag_records(
            state,
            "roof",
            disposition=ApplicabilityDisposition.NOT_APPLICABLE,
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "does not authorize system existence",
        ):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="non-applicable-rag",
                evidence_snapshot=snapshot,
                research_scope=scope,
                adoption=adoption,
                applicability=not_applicable,
                evidence_ref=na_evidence,
                authority_ref=na_authority,
            )

        (
            rag_evidence,
            rag_authority,
            scope,
            snapshot,
            adoption,
            applicability,
        ) = _typed_rag_records(state, "roof")
        rag_profile = replace(profile, scope_digest=scope.scope_digest)
        adopted = _basis(
            state.branch.run.project_id,
            "roof",
            kind=SemanticDenominatorSourceKind.RAG,
            suffix="adopted-rag",
            evidence_snapshot=snapshot,
            research_scope=scope,
            adoption=adoption,
            applicability=applicability,
            evidence_ref=rag_evidence,
            authority_ref=rag_authority,
        )
        rag_binding = _binding(
            rag_profile,
            inventory,
            extra_authority_refs=(rag_authority,),
        )
        compiled = compile_genesis_semantic_denominator(
            denominator_id="adopted-rag-denominator",
            state=state,
            profile=rag_profile,
            profile_binding=rag_binding,
            inventory=inventory,
            profile_requirement_id="semantic-system-completeness",
            bases=(adopted,),
        )
        self.assertEqual(("semantic-system:roof",), compiled.system_refs)
        self.assertEqual(
            compiled,
            GenesisSemanticDenominator.from_dict(compiled.to_dict()),
        )
        source = compile_genesis_semantic_completeness(
            receipt_id="adopted-rag-source",
            denominator=compiled,
            state=state,
            profile=rag_profile,
            profile_binding=rag_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:roof",
                    disposition=SemanticSystemDisposition.PRESENT,
                    component_refs=("design-component:roof-system",),
                ),
            ),
        )
        check = bridge_semantic_completeness_check(
            denominator=compiled,
            source_receipt=source,
            profile=rag_profile,
            profile_binding=rag_binding,
            inventory=inventory,
        )
        self.assertIs(check.status, CheckStatus.PASS)
        self.assertEqual((applicability.ref,), check.applicability_refs)
        self.assertEqual((applicability.claim_ref,), check.claim_refs)

        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "exact retained snapshot",
        ):
            replace(
                adopted,
                evidence_ref=replace(rag_evidence, sha256="f" * 64),
            )

        unrelated_fact = replace(
            adoption.adoption.facts[0],
            snapshot_ref=(
                "project://genesis-fixture/input/unrelated-snapshot.json"
            ),
        )
        unrelated_adoption = replace(
            adoption,
            adoption=replace(
                adoption.adoption,
                facts=(unrelated_fact,),
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "exact evidence subject",
        ):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="unrelated-adoption-subject",
                evidence_snapshot=snapshot,
                research_scope=scope,
                adoption=unrelated_adoption,
                applicability=applicability,
                evidence_ref=rag_evidence,
                authority_ref=rag_authority,
            )

        unrelated_adoption_authority = replace(
            adoption,
            adoption=replace(
                adoption.adoption,
                authority_id=(
                    "project://genesis-fixture/input/"
                    "unrelated-adoption-authority.json"
                ),
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "omitted adoption, source, or authority",
        ):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="unrelated-adoption-authority",
                evidence_snapshot=snapshot,
                research_scope=scope,
                adoption=unrelated_adoption_authority,
                applicability=applicability,
                evidence_ref=rag_evidence,
                authority_ref=rag_authority,
            )

        raw_evidence = ProjectRecordRef(
            project_id=state.branch.run.project_id,
            relative_path="input/typed-but-unretained-rag-subject.json",
            sha256=_json_record_digest(snapshot.to_dict()),
        )
        raw_fact = replace(
            adoption.adoption.facts[0],
            snapshot_ref=raw_evidence.uri,
        )
        raw_adoption = replace(
            adoption,
            adoption=replace(adoption.adoption, facts=(raw_fact,)),
        )
        raw_adoption_ref = (
            f"precedent-adoption:{raw_adoption.adoption_id}:"
            f"{raw_adoption.adoption.adoption_digest}"
        )
        raw_applicability = replace(
            applicability,
            source_refs=tuple(
                sorted((raw_adoption_ref, raw_evidence.uri))
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "exact retained snapshot",
        ):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="typed-but-unretained-rag",
                evidence_snapshot=snapshot,
                research_scope=scope,
                adoption=raw_adoption,
                applicability=raw_applicability,
                evidence_ref=raw_evidence,
                authority_ref=rag_authority,
            )

        adoption_ref = adopted.rag_adoption_ref
        unrelated_source = replace(
            applicability,
            source_refs=tuple(
                sorted(
                    (
                        adoption_ref,
                        "project://genesis-fixture/input/unrelated-source.json",
                    )
                )
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "omitted adoption, source, or authority",
        ):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="unrelated-source-rag",
                evidence_snapshot=snapshot,
                research_scope=scope,
                adoption=adoption,
                applicability=unrelated_source,
                evidence_ref=rag_evidence,
                authority_ref=rag_authority,
            )
        unrelated_authority = replace(
            applicability,
            authority_refs=(
                "project://genesis-fixture/input/unrelated-authority.json",
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "omitted adoption, source, or authority",
        ):
            _basis(
                state.branch.run.project_id,
                "roof",
                kind=SemanticDenominatorSourceKind.RAG,
                suffix="unrelated-authority-rag",
                evidence_snapshot=snapshot,
                research_scope=scope,
                adoption=adoption,
                applicability=unrelated_authority,
                evidence_ref=rag_evidence,
                authority_ref=rag_authority,
            )

    def test_same_system_key_conflict_fails_closed(self) -> None:
        state, profile, profile_binding, inventory, *_ = _fixture(
            systems=("roof",)
        )
        brief = _basis(state.branch.run.project_id, "roof")
        conflicting = _basis(
            state.branch.run.project_id,
            "roof",
            kind=SemanticDenominatorSourceKind.TYPOLOGY,
            semantic_kinds=("canopy",),
            suffix="typology",
        )
        conflict_binding = _binding(
            profile,
            inventory,
            extra_authority_refs=(
                brief.authority_ref,
                conflicting.authority_ref,
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "conflicting typed mappings",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="conflicting-denominator",
                state=state,
                profile=profile,
                profile_binding=conflict_binding,
                inventory=inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=(brief, conflicting),
            )

    def test_typed_rag_scope_cannot_cross_run_base_branch_epoch_or_state(
        self,
    ) -> None:
        state, profile, profile_binding, inventory, *_ = _fixture(
            systems=("roof",)
        )
        variants = {
            "run": _state(_branch(run_id="other-run")),
            "base": _state(_branch(base_digest="b" * 64)),
            "branch": _state(_branch(branch_id="option-b")),
            "epoch": _state(_branch(epoch=1)),
            "state": _state(state.branch, phase="changed-phase"),
        }
        for name, source_state in variants.items():
            with self.subTest(identity=name):
                (
                    evidence,
                    authority,
                    scope,
                    snapshot,
                    adoption,
                    applicability,
                ) = (
                    _typed_rag_records(source_state, "roof")
                )
                basis = _basis(
                    state.branch.run.project_id,
                    "roof",
                    kind=SemanticDenominatorSourceKind.RAG,
                    suffix=f"cross-{name}-rag",
                    evidence_snapshot=snapshot,
                    research_scope=scope,
                    adoption=adoption,
                    applicability=applicability,
                    evidence_ref=evidence,
                    authority_ref=authority,
                )
                variant_binding = _binding(
                    profile,
                    inventory,
                    extra_authority_refs=(authority,),
                )
                with self.assertRaisesRegex(
                    GenesisSemanticCompletenessCompilationError,
                    "exact run/base/branch/epoch/state",
                ):
                    compile_genesis_semantic_denominator(
                        denominator_id=f"cross-{name}-denominator",
                        state=state,
                        profile=profile,
                        profile_binding=variant_binding,
                        inventory=inventory,
                        profile_requirement_id=(
                            "semantic-system-completeness"
                        ),
                        bases=(basis,),
                    )

    def test_missing_roof_is_open_and_missing_load_bearing_is_rejected(self) -> None:
        state, profile, profile_binding, inventory, _, denominator, _, _, _ = _fixture(
            systems=("load-bearing-system", "roof"),
            component_kinds={},
        )
        open_receipt = compile_genesis_semantic_completeness(
            receipt_id="missing-roof-open",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:load-bearing-system",
                    disposition=SemanticSystemDisposition.UNKNOWN,
                ),
            ),
        )
        self.assertEqual(CheckStatus.UNKNOWN, open_receipt.status)
        findings = {item.system_ref: item for item in open_receipt.findings}
        self.assertEqual(
            SemanticSystemFindingStatus.OPEN,
            findings["semantic-system:roof"].status,
        )

        rejected = compile_genesis_semantic_completeness(
            receipt_id="missing-load-bearing-rejected",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:load-bearing-system",
                    disposition=SemanticSystemDisposition.PRESENT,
                    component_refs=("design-component:missing-support",),
                ),
                SemanticSystemDeclaration(
                    system_ref="semantic-system:roof",
                    disposition=SemanticSystemDisposition.UNKNOWN,
                ),
            ),
        )
        self.assertEqual(CheckStatus.FAIL, rejected.status)
        finding = next(
            item
            for item in rejected.findings
            if item.system_ref == "semantic-system:load-bearing-system"
        )
        self.assertIn("component-ref-missing", finding.reason_codes)

    def test_component_name_never_substitutes_for_typed_semantic_kind(self) -> None:
        state, profile, profile_binding, inventory, _, denominator, _, _, _ = _fixture(
            systems=("roof",),
            component_kinds={"obvious-roof-name": "circulation"},
        )
        receipt = compile_genesis_semantic_completeness(
            receipt_id="no-name-guessing",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:roof",
                    disposition=SemanticSystemDisposition.PRESENT,
                    component_refs=(
                        "design-component:obvious-roof-name",
                    ),
                ),
            ),
        )
        self.assertEqual(CheckStatus.FAIL, receipt.status)
        self.assertIn("semantic-kind-mismatch", receipt.findings[0].reason_codes)

    def test_not_applicable_needs_independent_exact_evidence_and_authority(
        self,
    ) -> None:
        state, profile, profile_binding, inventory, bases, denominator, _, evidence, authority = (
            _fixture(systems=("material",), component_kinds={})
        )
        unauthorized = compile_genesis_semantic_completeness(
            receipt_id="unauthorized-na",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:material",
                    disposition=SemanticSystemDisposition.NOT_APPLICABLE,
                ),
            ),
        )
        self.assertEqual(
            CheckStatus.FAIL,
            unauthorized.status,
        )
        self.assertIn(
            "not-applicable-authority-unauthorized",
            unauthorized.findings[0].reason_codes,
        )

        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "must be independent",
        ):
            SemanticSystemDeclaration(
                system_ref="semantic-system:material",
                disposition=SemanticSystemDisposition.NOT_APPLICABLE,
                evidence_refs=(evidence,),
                authority_refs=(replace(evidence, sha256="f" * 64),),
            )
        same_path_authority = replace(evidence, sha256="f" * 64)
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "must be independent",
        ):
            overlapping_binding = _binding(
                profile,
                inventory,
                extra_authority_refs=(
                    bases[0].authority_ref,
                    same_path_authority,
                ),
            )
            compile_genesis_semantic_denominator(
                denominator_id="overlapping-na-policy",
                state=state,
                profile=profile,
                profile_binding=overlapping_binding,
                inventory=inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=bases,
                not_applicable_evidence_refs={
                    "semantic-system:material": (evidence,)
                },
                not_applicable_authority_refs={
                    "semantic-system:material": (same_path_authority,)
                },
            )

        authorized = compile_genesis_semantic_completeness(
            receipt_id="authorized-na",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:material",
                    disposition=SemanticSystemDisposition.NOT_APPLICABLE,
                    evidence_refs=(evidence,),
                    authority_refs=(authority,),
                ),
            ),
        )
        self.assertEqual(CheckStatus.PASS, authorized.status)
        self.assertEqual(
            SemanticSystemFindingStatus.NOT_APPLICABLE,
            authorized.findings[0].status,
        )

        tampered_evidence = replace(evidence, sha256="8" * 64)
        rejected = compile_genesis_semantic_completeness(
            receipt_id="tampered-na-evidence",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:material",
                    disposition=SemanticSystemDisposition.NOT_APPLICABLE,
                    evidence_refs=(tampered_evidence,),
                    authority_refs=(authority,),
                ),
            ),
        )
        self.assertEqual(CheckStatus.FAIL, rejected.status)

    def test_exact_branch_epoch_and_state_are_required(self) -> None:
        state, profile, profile_binding, inventory, bases, denominator, declarations, _, _ = (
            _fixture(systems=("roof",))
        )
        other_branch = _branch(branch_id="option-b")
        crossed_profile = _profile(
            _state(other_branch),
            stage_id=GENESIS_STAGE_ID,
            denominator_refs=("semantic-system:roof",),
        )
        crossed_inventory = _inventory(
            other_branch,
            stage_id=GENESIS_STAGE_ID,
            component_kinds={"roof-system": "roof"},
        )
        crossed_binding = _binding(crossed_profile, crossed_inventory)
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "exact operational branch",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="cross-branch",
                state=state,
                profile=crossed_profile,
                profile_binding=crossed_binding,
                inventory=crossed_inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=bases,
            )

        stale_profile = replace(
            profile,
            predecessor_state_digest="8" * 64,
        )
        stale_binding = _binding(stale_profile, inventory)
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "another operational state",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="cross-state",
                state=state,
                profile=stale_profile,
                profile_binding=stale_binding,
                inventory=inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=bases,
            )

        next_epoch_state = _state(
            replace(state.branch, epoch=state.branch.epoch + 1)
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "exact operational branch",
        ):
            compile_genesis_semantic_completeness(
                receipt_id="cross-epoch",
                denominator=denominator,
                state=next_epoch_state,
                profile=profile,
                profile_binding=profile_binding,
                inventory=inventory,
                declarations=declarations,
            )

    def test_profile_cannot_omit_an_explicit_genesis_system(self) -> None:
        state, _, _, inventory, bases, *_ = _fixture(
            systems=("load-bearing-system", "roof"),
        )
        shrunk = _profile(
            state,
            stage_id=GENESIS_STAGE_ID,
            denominator_refs=("semantic-system:load-bearing-system",),
        )
        shrunk_binding = _binding(
            shrunk,
            inventory,
            extra_authority_refs=tuple(
                item.authority_ref for item in bases
            ),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "exact semantic denominator",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="shrunk-genesis",
                state=state,
                profile=shrunk,
                profile_binding=shrunk_binding,
                inventory=inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=bases,
            )

    def test_later_stage_cannot_recompile_a_smaller_genesis_denominator(
        self,
    ) -> None:
        later_branch = _branch(epoch=4)
        later_stage_id = DesignPhase.SCHEMATIC_DESIGN.value
        later_state = _state(later_branch, phase=later_stage_id)
        later_profile = _profile(
            later_state,
            stage_id=later_stage_id,
            denominator_refs=("semantic-system:roof",),
            profile_id="later-shrunk-profile",
        )
        later_inventory = _inventory(
            later_branch,
            stage_id=later_stage_id,
            component_kinds={"roof-only": "roof"},
            inventory_id="later-shrunk-inventory",
        )
        later_basis = _basis(
            later_branch.run.project_id,
            "roof",
            suffix="later-reset-attempt",
        )
        later_binding = _binding(
            later_profile,
            later_inventory,
            extra_authority_refs=(later_basis.authority_ref,),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "research_brief Stage 0",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="later-smaller-reset",
                state=later_state,
                profile=later_profile,
                profile_binding=later_binding,
                inventory=later_inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=(later_basis,),
            )

        same_phase_state = _state(later_branch)
        same_phase_profile = _profile(
            same_phase_state,
            stage_id=GENESIS_STAGE_ID,
            denominator_refs=("semantic-system:roof",),
            profile_id="same-phase-reset-profile",
        )
        same_phase_inventory = _inventory(
            later_branch,
            stage_id=GENESIS_STAGE_ID,
            component_kinds={"roof-only": "roof"},
            inventory_id="same-phase-reset-inventory",
        )
        same_phase_binding = _binding(
            same_phase_profile,
            same_phase_inventory,
            extra_authority_refs=(later_basis.authority_ref,),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "branch epoch zero",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="same-phase-later-reset",
                state=same_phase_state,
                profile=same_phase_profile,
                profile_binding=same_phase_binding,
                inventory=same_phase_inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=(later_basis,),
            )

        forged_state = _state(_branch(), phase="genesis")
        forged_profile = _profile(
            forged_state,
            stage_id="genesis",
            denominator_refs=("semantic-system:roof",),
            profile_id="forged-genesis-profile",
        )
        forged_inventory = _inventory(
            forged_state.branch,
            stage_id="genesis",
            component_kinds={"roof-only": "roof"},
            inventory_id="forged-genesis-inventory",
        )
        forged_basis = _basis(
            forged_state.branch.run.project_id,
            "roof",
            suffix="forged-genesis",
        )
        forged_binding = _binding(
            forged_profile,
            forged_inventory,
            extra_authority_refs=(forged_basis.authority_ref,),
        )
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "research_brief Stage 0",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="forged-genesis-denominator",
                state=forged_state,
                profile=forged_profile,
                profile_binding=forged_binding,
                inventory=forged_inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=(forged_basis,),
            )

    def test_successor_inherits_exact_denominator_and_shrink_is_rejected(
        self,
    ) -> None:
        (
            _,
            _,
            _,
            _,
            _,
            denominator,
            _,
            na_evidence,
            na_authority,
            genesis_evidence,
        ) = _compile_passing_genesis()
        successor_branch = replace(denominator.branch, epoch=1)
        successor_stage_id = DesignPhase.SCHEMATIC_DESIGN.value
        successor_state = _state(
            successor_branch,
            phase=successor_stage_id,
        )
        successor_profile = _profile(
            successor_state,
            stage_id=successor_stage_id,
            denominator_refs=denominator.system_refs,
            profile_id="schematic-profile",
        )
        successor_inventory = _inventory(
            successor_branch,
            stage_id=successor_stage_id,
            component_kinds={
                "circulation-developed": "circulation",
                "enclosure-developed": "enclosure",
                "opening-developed": "opening",
                "roof-developed": "roof",
                "support-developed": "load-bearing-system",
            },
            inventory_id="schematic-subject-inventory",
        )
        denominator_authorities = tuple(
            {
                basis.authority_ref
                for system in denominator.systems
                for basis in system.bases
            }
            | {
                ref
                for system in denominator.systems
                for ref in system.not_applicable_authority_refs
            }
        )
        successor_binding = _binding(
            successor_profile,
            successor_inventory,
            extra_authority_refs=denominator_authorities,
        )
        declarations = tuple(
            SemanticSystemDeclaration(
                system_ref=f"semantic-system:{system}",
                disposition=SemanticSystemDisposition.PRESENT,
                component_refs=(
                    f"design-component:{component_id}",
                ),
            )
            for system, component_id in (
                ("circulation", "circulation-developed"),
                ("enclosure", "enclosure-developed"),
                ("load-bearing-system", "support-developed"),
                ("opening", "opening-developed"),
                ("roof", "roof-developed"),
            )
        ) + (
            SemanticSystemDeclaration(
                system_ref="semantic-system:material",
                disposition=SemanticSystemDisposition.NOT_APPLICABLE,
                evidence_refs=(na_evidence,),
                authority_refs=(na_authority,),
            ),
        )
        inherited = compile_semantic_denominator_inheritance(
            receipt_id="schematic-semantic-inheritance",
            denominator=denominator,
            genesis_evidence=genesis_evidence,
            state=successor_state,
            profile=successor_profile,
            profile_binding=successor_binding,
            inventory=successor_inventory,
            profile_requirement_id="semantic-system-completeness",
            declarations=declarations,
        )
        self.assertTrue(inherited.denominator_preserved)
        self.assertEqual(CheckStatus.PASS, inherited.status)

        shrunk_refs = tuple(
            item for item in denominator.system_refs if item != "semantic-system:roof"
        )
        shrunk_profile = _profile(
            successor_state,
            stage_id=successor_stage_id,
            denominator_refs=shrunk_refs,
            profile_id="schematic-profile-shrunk",
        )
        shrunk_binding = _binding(
            shrunk_profile,
            successor_inventory,
            extra_authority_refs=denominator_authorities,
        )
        shrunk = compile_semantic_denominator_inheritance(
            receipt_id="schematic-semantic-shrink",
            denominator=denominator,
            genesis_evidence=genesis_evidence,
            state=successor_state,
            profile=shrunk_profile,
            profile_binding=shrunk_binding,
            inventory=successor_inventory,
            profile_requirement_id="semantic-system-completeness",
            declarations=declarations,
        )
        self.assertFalse(shrunk.denominator_preserved)
        self.assertEqual(CheckStatus.FAIL, shrunk.status)
        self.assertEqual(("semantic-denominator-shrunk",), shrunk.reason_codes)

    def test_foreign_project_basis_and_all_serialized_identity_tampering_fail(
        self,
    ) -> None:
        state, profile, profile_binding, inventory, _, denominator, _, _, _ = _fixture(
            systems=("roof",)
        )
        foreign = _basis("foreign-project", "roof")
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessCompilationError,
            "crossed its project",
        ):
            compile_genesis_semantic_denominator(
                denominator_id="foreign-basis",
                state=state,
                profile=profile,
                profile_binding=profile_binding,
                inventory=inventory,
                profile_requirement_id="semantic-system-completeness",
                bases=(foreign,),
            )

        mutations = {
            "project": lambda payload: payload["branch"].__setitem__(
                "project_id", "foreign-project"
            ),
            "run": lambda payload: payload["branch"].__setitem__(
                "run_id", "other-run"
            ),
            "base": lambda payload: payload["branch"]["base"].__setitem__(
                "state_sha256", "8" * 64
            ),
            "branch": lambda payload: payload["branch"].__setitem__(
                "branch_id", "option-b"
            ),
            "epoch": lambda payload: payload["branch"].__setitem__(
                "epoch", 7
            ),
            "state": lambda payload: payload.__setitem__(
                "state_digest", "8" * 64
            ),
            "denominator": lambda payload: payload.__setitem__(
                "denominator_digest", "8" * 64
            ),
            "evidence": lambda payload: payload["systems"][0]["bases"][0][
                "evidence_ref"
            ].__setitem__("sha256", "8" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(identity=name):
                payload = copy.deepcopy(denominator.to_dict())
                mutate(payload)
                with self.assertRaises((ValueError, TypeError)):
                    GenesisSemanticDenominator.from_dict(payload)

    def test_compilers_have_no_path_writer_or_composite_closure_surface(self) -> None:
        for compiler in (
            compile_genesis_semantic_denominator,
            compile_genesis_semantic_completeness,
            compile_semantic_denominator_inheritance,
        ):
            parameters = inspect.signature(compiler).parameters
            self.assertFalse(
                any("path" in name or "repository" in name for name in parameters)
            )
        source = inspect.getsource(
            compile_semantic_denominator_inheritance
        )
        self.assertNotIn("CompositeStageClosure", source)
        self.assertNotIn("write", source)

    def test_unknown_never_counts_as_closure(self) -> None:
        state, profile, profile_binding, inventory, _, denominator, _, _, _ = _fixture(
            systems=("roof",)
        )
        receipt = compile_genesis_semantic_completeness(
            receipt_id="unknown-is-open",
            denominator=denominator,
            state=state,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
            declarations=(
                SemanticSystemDeclaration(
                    system_ref="semantic-system:roof",
                    disposition=SemanticSystemDisposition.UNKNOWN,
                ),
            ),
        )
        self.assertEqual(CheckStatus.UNKNOWN, receipt.status)
        self.assertNotEqual(CheckStatus.PASS, receipt.status)
        check = bridge_semantic_completeness_check(
            denominator=denominator,
            source_receipt=receipt,
            profile=profile,
            profile_binding=profile_binding,
            inventory=inventory,
        )
        closure = compile_composite_stage_closure(
            profile,
            subject_digest=inventory.stage_subject_digest,
            check_receipts=(check,),
        )
        self.assertIs(check.status, CheckStatus.UNKNOWN)
        self.assertIs(closure.status, StageClosureStatus.OPEN)

    def test_schema_and_authority_tampering_fail_closed(self) -> None:
        _, _, _, _, _, denominator, _, _, _ = _fixture(systems=("roof",))
        payload = denominator.to_dict()
        payload["stage_acceptance_authority"] = True
        with self.assertRaisesRegex(
            GenesisSemanticCompletenessError,
            "authority flags changed",
        ):
            GenesisSemanticDenominator.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
