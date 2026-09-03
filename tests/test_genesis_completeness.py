from __future__ import annotations

import copy
import hashlib
import json
import unittest

from archive.archflow.control.baseline import StageBaselineLevel
from archive.archflow.control.genesis_completeness import (
    GenesisSemanticDenominator,
    SemanticDenominatorSourceKind,
    SemanticSystemBasis,
    SemanticSystemDeclaration,
    SemanticSystemDisposition,
)
from archive.archflow.control.profile import StageRequirementProfileBinding
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archive.archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
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
from archive.archflow.runtime.genesis_completeness import (
    GenesisSemanticCompletenessCompilationError,
    compile_genesis_semantic_denominator,
)
from archive.archflow.research.adoption import PrecedentAdoption, PrecedentFact
from archive.archflow.research.branch import (
    BranchEvidenceSnapshot,
    BranchPrecedentAdoption,
    BranchResearchScope,
)
from archflow.state.stage_workflow import DesignPhase
from archflow.state.operational_state import OperationalMarkovState
from archive.archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)


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


class GenesisSemanticCompletenessTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
