from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from archive.archflow.adapters.web_evidence import fetch_web_evidence

from archive.archflow.research.index import (
    BasisIndexError,
    BranchBasisIndex,
    BranchRAGProgressStatus,
    build_branch_basis_index,
    compile_branch_decision_context,
    compile_next_branch_queries,
    compile_branch_rag_progress,
)
from archive.archflow.capabilities import branch_research as legacy_branch_research
from archive.archflow.research import branch as canonical_branch_research
from archive.archflow.research.branch import (
    BranchEvidenceSnapshot,
    BranchHardFeasibilityAssessment,
    BranchResearchError,
    BranchResearchProfile,
    BranchScorecard,
    BranchScoreCriterion,
    BranchSelectionDecision,
    BranchSelectionMode,
    BranchSelectionRules,
    BranchSelectionStatus,
    DecisionResearchNeed,
    HardFeasibilityStatus,
    _apply_branch_selection,
    bind_branch_snapshot,
    bind_branch_adoption,
    compile_branch_query,
    _compile_branch_research_scope,
    evaluate_branch_selection,
    require_branch_source_url,
)
from archive.archflow.capabilities.evidence_sufficiency import (
    ConflictDisposition,
    DecisionNode,
    DecisionUniverseRevision,
    EvidenceClaimBinding,
    EvidenceRule,
    EvidenceSufficiencyPolicy,
    ExpansionKind,
    FrontierStatus,
    ResolutionMode,
    UniverseExpansionCandidate,
    build_research_frontier,
    compile_decision_universe_closure,
    compile_evidence_sufficiency,
)
from archive.archflow.capabilities.precedent import PrecedentAdoption, PrecedentFact
from archive.archflow.capabilities.research import (
    ResearchError,
    parse_research_output,
    research_prompt,
)
from archive.archflow.capabilities.semantic_spatial_authoring import (
    SemanticSpatialAuthoringStatus,
    _author_semantic_spatial_option,
    author_semantic_spatial_option,
    semantic_spatial_authoring_output,
)
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archive.archflow.runtime.branch_portfolio import BranchPortfolioArchive
from archive.archflow.runtime.architectural_revision import (
    ArchitecturalRevisionError,
    _portfolio_uses_branch_research_selection,
    _require_branch_index_matches_selected_predecessor,
)
from archive.archflow.runtime.branch_research import (
    BranchResearchArchive,
    BranchResearchArchiveError,
)
from archive.archflow.runtime.state_reducer import canonical_state_to_dict
from archflow.state.design_portfolio import BranchLifecycle, SelectionPolicy, compile_selected_branch_handoff, initialize_design_portfolio, revise_branch
from archflow.state.operational_state import OperationalMarkovState
from archflow.state.model import initialize_canonical_project
from archflow.state.developed_design import SelectedSchematicInput
from archive.archflow.state.build_policy import (
    ConstructabilityTopic,
    PolicyConstraintStrength,
)
from tests.test_design_portfolio import (
    EVIDENCE,
    PROJECT_ID,
    _option,
    _option_set,
    _run,
)
from archive.tests.test_semantic_spatial_authoring import (
    _ScriptedProvider,
    _semantic_proposal,
)
from archive.tests.test_spatial_proposals import _inputs as _spatial_inputs


DECISION_REF = "decision:candidate-convergence"
ACTIVE_DECISIONS = (
    "declaration:column-order",
    "declaration:roof-form",
)


def _p079_inputs(
    scope,
    decision_refs,
    *,
    claim_families=("family:primary",),
    minimum_source_families=1,
    expansion=False,
    conflict=False,
):
    nodes = tuple(
        DecisionNode(ref, "project:decision") for ref in decision_refs
    )
    universe = DecisionUniverseRevision(
        universe_id="branch-universe",
        revision_id="branch-universe-001",
        scope_digest=scope.scope_digest,
        ontology_ref="project:ontology/branch-001",
        nodes=nodes,
        seed_refs=tuple(decision_refs),
    )
    policy = EvidenceSufficiencyPolicy(
        policy_id="branch-policy",
        rules=tuple(
            EvidenceRule(
                obligation_id=f"basis-{ref.replace(':', '-')}",
                target_ref=ref,
                minimum_source_families=minimum_source_families,
            )
            for ref in decision_refs
        ),
        allowed_conflict_authority_refs=("project:authority/human-owner",),
    )
    claims = []
    for ref in decision_refs:
        positions = ("position:a", "position:b") if conflict else ("position:a",)
        for index, family in enumerate(claim_families):
            position = positions[index % len(positions)]
            claims.append(
                EvidenceClaimBinding(
                    binding_id=f"binding-{ref.replace(':', '-')}-{index}",
                    obligation_id=f"basis-{ref.replace(':', '-')}",
                    target_ref=ref,
                    fact_ref=f"fact:{ref}:{index}",
                    source_ref=f"source:{ref}:{index}",
                    source_family_ref=family,
                    claim_key=f"claim:{ref}",
                    position_key=position,
                )
            )
    expansions = ()
    if expansion:
        expansions = (
            UniverseExpansionCandidate(
                candidate_id="candidate-universe-expansion",
                kind=ExpansionKind.NODE,
                proposed_ref="decision:new-dependency",
                predecessor_universe_digest=universe.universe_digest,
                scope_digest=scope.scope_digest,
                evidence_refs=("project:evidence/new-dependency",),
            ),
        )
    closure = compile_decision_universe_closure(
        universe,
        policy,
        expansions=expansions,
    )
    sufficiency = compile_evidence_sufficiency(
        universe,
        policy,
        claims=tuple(claims),
    )
    frontier = build_research_frontier(closure, sufficiency)
    return universe, policy, closure, sufficiency, frontier


def _record_sha256(payload) -> str:
    encoded = json.dumps(
        dict(payload),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256((encoded + "\n").encode("utf-8")).hexdigest()


def _record_ref(run, record_kind: str, payload, *, branch_id=None):
    digest = _record_sha256(payload)
    area = (
        f"branches/{branch_id}/records"
        if branch_id is not None
        else "records"
    )
    return ProjectRecordRef(
        project_id=run.project_id,
        relative_path=f"runs/{run.run_id}/{area}/{record_kind}-{digest}.json",
        sha256=digest,
    )


def _selection_ref(decision):
    run = RunRef(
        project_id=decision.project_id,
        run_id=decision.run_id,
        base=ProjectVersionRef(
            project_id=decision.project_id,
            version=0,
            state_sha256=decision.base_state_sha256,
        ),
    )
    return _record_ref(
        run,
        f"branch-selection-{decision.decision_digest[:16]}",
        decision.to_dict(),
    )


def _index_ref(index):
    return _record_ref(
        index.scope.run,
        f"branch-basis-{index.index_digest[:16]}",
        index.to_dict(),
        branch_id=index.scope.branch_id,
    )


def _source_refs(option_set, state):
    return (
        _record_ref(
            option_set.branch.run,
            (
                "branch-source-option-set-"
                f"{option_set.option_set_digest[:16]}"
            ),
            option_set.to_dict(),
        ),
        _record_ref(
            state.branch.run,
            f"branch-source-state-{state.state_digest[:16]}",
            state.to_dict(),
        ),
    )


def _candidate_setup(run=None, *, source_state=None):
    if source_state is not None:
        run = source_state.branch.run
    source = _option_set(run)
    if source_state is None:
        source_state = OperationalMarkovState(
            branch=source.branch,
            compiler_version="test.branch-rag",
            phase="site_resource_coordination",
            evidence_refs=(
                f"project://{source.project_id}/input/research.json",
            ),
        )
    source = replace(
        source,
        branch=source_state.branch,
        operational_state_digest=source_state.state_digest,
    )
    option_set = replace(
        source,
        options=(
            _option("classical", shape=0),
            _option("gothic", shape=4),
        ),
    )
    portfolio = initialize_design_portfolio(
        option_set,
        portfolio_id="style-portfolio",
        selection_policy=SelectionPolicy(
            authority_ids=("algorithm-selector", "human-owner"),
            source_refs=(EVIDENCE,),
        ),
        architect_id="algorithm-selector",
    )
    return option_set, source_state, portfolio


def _scorecard(
    portfolio,
    branch_id: str,
    score: float,
    *,
    hard_status: HardFeasibilityStatus | None = None,
) -> BranchScorecard:
    terms = (
        ("classical", "pantheon")
        if branch_id == "classical"
        else ("cathedral", "gothic")
    )
    excluded = (
        ("flying-buttress", "gothic")
        if branch_id == "classical"
        else ("classical", "pantheon")
    )
    revision_digest = portfolio.branch(branch_id).head.revision_digest
    hard_feasibility = (
        BranchHardFeasibilityAssessment(
            assessment_id=f"hard-feasibility-{branch_id}",
            branch_id=branch_id,
            branch_revision_digest=revision_digest,
            status=hard_status,
            evidence_refs=(EVIDENCE,),
            rationale=f"Hard project constraints for {branch_id}.",
        )
        if hard_status is not None
        else None
    )
    return BranchScorecard(
        branch_id=branch_id,
        branch_revision_digest=revision_digest,
        research_profile=BranchResearchProfile(
            branch_id=branch_id,
            branch_revision_digest=revision_digest,
            active_decision_refs=ACTIVE_DECISIONS,
            branch_search_terms=terms,
            excluded_search_terms=excluded,
            domain_allowlist=("example.org",),
            evidence_refs=(EVIDENCE,),
        ),
        criteria=(
            BranchScoreCriterion(
                criterion_id="evidence-fit",
                score=score,
                weight=0.7,
                evidence_refs=(EVIDENCE,),
                rationale="Fit to retained project evidence.",
            ),
            BranchScoreCriterion(
                criterion_id="program-fit",
                score=score,
                weight=0.3,
                evidence_refs=(EVIDENCE,),
                rationale="Fit to the current program obligations.",
            ),
        ),
        rationale=f"Evidence-backed score for {branch_id}.",
        hard_feasibility=hard_feasibility,
    )


def _scorecards(portfolio, *, classical=0.9, gothic=0.4):
    return (
        _scorecard(portfolio, "classical", classical),
        _scorecard(portfolio, "gothic", gothic),
    )


def _automatic_decision(portfolio):
    return evaluate_branch_selection(
        portfolio,
        decision_id="select-style",
        rules=BranchSelectionRules(
            mode=BranchSelectionMode.AUTOMATIC,
            minimum_score=0.7,
            minimum_margin=0.2,
            automatic_authority_id="algorithm-selector",
        ),
        scorecards=_scorecards(portfolio),
        decision_ref=DECISION_REF,
        evidence_refs=(EVIDENCE,),
        rationale="Continue only the evidence-leading candidate.",
    )


def _selected_scope(
    *,
    branch_id: str = "classical",
    source_state=None,
):
    option_set, state, portfolio = _candidate_setup(
        source_state=source_state
    )
    if branch_id == "classical":
        decision = _automatic_decision(portfolio)
    else:
        decision = evaluate_branch_selection(
            portfolio,
            decision_id="select-style-human",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.HUMAN_IN_THE_LOOP,
                minimum_score=0.0,
                minimum_margin=0.0,
            ),
            scorecards=_scorecards(portfolio),
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="Owner selected the alternate branch.",
            human_branch_id="gothic",
            human_authority_id="human-owner",
        )
    selection_record = _selection_ref(decision)
    selected = _apply_branch_selection(
        portfolio,
        decision,
        selection_record=selection_record,
    )
    source_option_set_ref, source_state_ref = _source_refs(option_set, state)
    scope = _compile_branch_research_scope(
        selected,
        decision,
        scope_id=f"{branch_id}-stage-01",
        selection_record=selection_record,
        source_option_set=option_set,
        source_option_set_ref=source_option_set_ref,
        source_state=state,
        source_state_ref=source_state_ref,
        context_refs=(EVIDENCE,),
    )
    return option_set, state, portfolio, decision, selected, scope


def _need(decision_ref="declaration:column-order"):
    return DecisionResearchNeed(
        decision_ref=decision_ref,
        query_id=f"query-{decision_ref.split(':')[-1]}",
        question=f"What evidence governs {decision_ref}?",
        search_terms=("dimensions", "precedent"),
    )


def _branch_record_uri(scope, name: str) -> str:
    return (
        f"project://{scope.run.project_id}/runs/{scope.run.run_id}/"
        f"branches/{scope.branch_id}/records/{name}"
    )


def _snapshot(
    query,
    name: str,
    *,
    text: str,
    url="https://example.org/source",
    requested_url="https://example.org/request",
):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (
        _branch_record_uri(query.scope, name),
        BranchEvidenceSnapshot(
            scope_digest=query.scope.scope_digest,
            branch_id=query.scope.branch_id,
            branch_revision_digest=query.scope.branch_revision_digest,
            query_id=query.query_id,
            query_digest=query.query_digest,
            requested_url=requested_url,
            final_url=url,
            retrieved_at="2026-08-29T00:00:00Z",
            content_sha256=hashlib.sha256(
                f"raw:{text}".encode("utf-8")
            ).hexdigest(),
            content_bytes=len(text.encode("utf-8")),
            text=text,
            text_sha256=digest,
        ).to_dict(),
    )


def _wrapped_adoption(query, snapshot_ref: str, statement: str):
    decision_ref = query.decision_refs[0]
    slug = decision_ref.split(":")[-1]
    quote = statement
    digest = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    fact = PrecedentFact(
        fact_id=f"{slug}-fact",
        statement=statement,
        quote=quote,
        quote_start=0,
        quote_end=len(quote),
        snapshot_ref=snapshot_ref,
        snapshot_text_sha256=digest,
        annotator="model:test",
        annotator_is_harness=False,
        topic=ConstructabilityTopic.SUPPORT,
        strength=PolicyConstraintStrength.SOFT,
        decision_refs=(decision_ref,),
    )
    adoption = PrecedentAdoption(
        adoption_id=f"{slug}-adoption",
        authority_id="human-owner",
        adopted_at="2026-08-29T00:01:00Z",
        facts=(fact,),
    )
    return bind_branch_adoption(query, adoption)


def _covered_branch_index(scope) -> BranchBasisIndex:
    records = []
    for decision_ref in ACTIVE_DECISIONS:
        query = compile_branch_query(scope, _need(decision_ref))
        slug = decision_ref.split(":")[-1]
        snapshot_name = f"branch-snapshot-{slug}.json"
        statement = f"Retained project evidence for {decision_ref}."
        adoption = _wrapped_adoption(
            query,
            _branch_record_uri(scope, snapshot_name),
            statement,
        )
        records.extend(
            (
                (
                    _branch_record_uri(scope, f"branch-query-{slug}.json"),
                    query.to_dict(),
                ),
                _snapshot(query, snapshot_name, text=statement),
                (
                    _branch_record_uri(scope, f"branch-adoption-{slug}.json"),
                    adoption.to_dict(),
                ),
            )
        )
    return build_branch_basis_index(tuple(records), scope=scope)


class BranchResearchOwnerMigrationTests(unittest.TestCase):
    def test_legacy_capability_is_identity_preserving_facade(self):
        for name in legacy_branch_research.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_branch_research, name),
                    getattr(canonical_branch_research, name),
                )
        self.assertIs(
            legacy_branch_research._apply_branch_selection,
            canonical_branch_research._apply_branch_selection,
        )
        self.assertIs(
            legacy_branch_research._compile_branch_research_scope,
            canonical_branch_research._compile_branch_research_scope,
        )

    def test_representative_schema_and_digests_remain_fixed(self):
        _, _, portfolio = _candidate_setup()
        decision = _automatic_decision(portfolio)
        _, _, _, _, _, scope = _selected_scope()
        query = compile_branch_query(scope, _need())
        adoption = _wrapped_adoption(
            query,
            _branch_record_uri(
                scope,
                "branch-snapshot-column-order.json",
            ),
            "Retained project evidence for declaration:column-order.",
        )

        self.assertEqual("BranchSelectionDecision@1", decision.SCHEMA)
        self.assertEqual(
            "9ea3c0a53e6bb3c3ba9e941421f4baa13f699280f713d16495ce1c1ad85f84c1",
            decision.decision_digest,
        )
        self.assertEqual("BranchResearchScope@1", scope.SCHEMA)
        self.assertEqual(
            "78b8494e3ddbc9ec6dd6bca0bb6851a261f0611c2073007a755120956149713a",
            scope.scope_digest,
        )
        self.assertEqual("BranchPrecedentQuery@1", query.SCHEMA)
        self.assertEqual(
            "5936f81ae1741d8a3d8d15af347e4ef74557ad210529cb35ad94ffb9e07801ac",
            query.query_digest,
        )
        self.assertEqual("BranchPrecedentAdoption@1", adoption.SCHEMA)
        self.assertEqual(
            "95c0cd615e2189083311278873f660796339442117219bef3adb8bfc79768688",
            adoption.adoption.adoption_digest,
        )


class BranchSelectionTests(unittest.TestCase):
    def test_automatic_selection_parks_losers_with_same_decision(self):
        _, _, portfolio = _candidate_setup()
        decision = _automatic_decision(portfolio)

        self.assertIs(decision.status, BranchSelectionStatus.SELECTED)
        self.assertEqual("classical", decision.selected_branch_id)
        self.assertEqual(("gothic",), decision.pruned_branch_ids)
        self.assertEqual(
            decision,
            BranchSelectionDecision.from_dict(decision.to_dict()),
        )
        selected_profile = next(
            card.research_profile
            for card in decision.scorecards
            if card.branch_id == decision.selected_branch_id
        )
        self.assertEqual(("classical", "pantheon"), selected_profile.branch_search_terms)
        selection_ref = _selection_ref(decision)
        selected = _apply_branch_selection(
            portfolio,
            decision,
            selection_record=selection_ref,
        )

        self.assertIs(
            selected.branch("classical").lifecycle,
            BranchLifecycle.SELECTED,
        )
        self.assertIs(
            selected.branch("gothic").lifecycle,
            BranchLifecycle.PARKED,
        )
        self.assertEqual(
            {selection_ref.uri},
            {item.decision_ref for item in selected.transitions},
        )
        self.assertEqual(1, len(selected.branch("gothic").revisions))

    def test_close_scores_require_human_without_mutating_portfolio(self):
        _, _, portfolio = _candidate_setup()
        decision = evaluate_branch_selection(
            portfolio,
            decision_id="ambiguous-style",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.AUTOMATIC,
                minimum_score=0.7,
                minimum_margin=0.2,
                automatic_authority_id="algorithm-selector",
            ),
            scorecards=_scorecards(portfolio, classical=0.82, gothic=0.75),
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="Compare close candidates.",
        )

        self.assertIs(
            decision.status,
            BranchSelectionStatus.HUMAN_REVIEW_REQUIRED,
        )
        self.assertIsNone(decision.selected_branch_id)
        self.assertEqual((), portfolio.transitions)
        with self.assertRaisesRegex(BranchResearchError, "unresolved"):
            _apply_branch_selection(
                portfolio,
                decision,
                selection_record=_selection_ref(decision),
            )

    def test_human_choice_can_override_scores_and_parks_alternatives(self):
        _, _, portfolio = _candidate_setup()
        decision = evaluate_branch_selection(
            portfolio,
            decision_id="human-style",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.HUMAN_IN_THE_LOOP,
                minimum_score=0.0,
                minimum_margin=0.0,
            ),
            scorecards=_scorecards(portfolio),
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="Owner selected Gothic despite the lower score.",
            human_branch_id="gothic",
            human_authority_id="human-owner",
        )
        selected = _apply_branch_selection(
            portfolio,
            decision,
            selection_record=_selection_ref(decision),
        )

        self.assertEqual("gothic", selected.selected_branch.branch_id)
        self.assertEqual(("classical",), decision.parked_branch_ids)
        self.assertIs(
            selected.branch("classical").lifecycle,
            BranchLifecycle.PARKED,
        )

    def test_hard_feasibility_eliminates_higher_soft_score(self):
        _, _, portfolio = _candidate_setup()
        scorecards = (
            _scorecard(
                portfolio,
                "classical",
                0.72,
                hard_status=HardFeasibilityStatus.SATISFIED,
            ),
            _scorecard(
                portfolio,
                "gothic",
                0.99,
                hard_status=HardFeasibilityStatus.VIOLATED,
            ),
        )

        decision = evaluate_branch_selection(
            portfolio,
            decision_id="hard-gated-style",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.AUTOMATIC,
                minimum_score=0.7,
                minimum_margin=0.2,
                automatic_authority_id="algorithm-selector",
                require_hard_feasibility=True,
            ),
            scorecards=scorecards,
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="Hard feasibility precedes weighted preference.",
        )

        self.assertEqual("classical", decision.selected_branch_id)
        self.assertEqual(
            scorecards,
            BranchSelectionDecision.from_dict(decision.to_dict()).scorecards,
        )

    def test_human_cannot_force_hard_violated_candidate(self):
        _, _, portfolio = _candidate_setup()
        scorecards = (
            _scorecard(
                portfolio,
                "classical",
                0.72,
                hard_status=HardFeasibilityStatus.SATISFIED,
            ),
            _scorecard(
                portfolio,
                "gothic",
                0.99,
                hard_status=HardFeasibilityStatus.VIOLATED,
            ),
        )

        with self.assertRaisesRegex(BranchResearchError, "hard feasibility"):
            evaluate_branch_selection(
                portfolio,
                decision_id="hard-gated-human-style",
                rules=BranchSelectionRules(
                    mode=BranchSelectionMode.HUMAN_IN_THE_LOOP,
                    minimum_score=0.0,
                    minimum_margin=0.0,
                    require_hard_feasibility=True,
                ),
                scorecards=scorecards,
                decision_ref=DECISION_REF,
                evidence_refs=(EVIDENCE,),
                rationale="A human choice remains inside hard constraints.",
                human_branch_id="gothic",
                human_authority_id="human-owner",
            )

    def test_legacy_scorecard_without_hard_assessment_remains_compatible(self):
        _, _, portfolio = _candidate_setup()
        payload = _scorecard(portfolio, "classical", 0.9).to_dict()
        payload.pop("hard_feasibility")

        restored = BranchScorecard.from_dict(payload)

        self.assertIsNone(restored.hard_feasibility)

    def test_required_hard_assessment_treats_missing_as_unresolved(self):
        _, _, portfolio = _candidate_setup()

        decision = evaluate_branch_selection(
            portfolio,
            decision_id="missing-hard-gate",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.AUTOMATIC,
                minimum_score=0.7,
                minimum_margin=0.2,
                automatic_authority_id="algorithm-selector",
                require_hard_feasibility=True,
            ),
            scorecards=_scorecards(portfolio),
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="Hard assessments are mandatory for this project.",
        )

        self.assertIs(
            decision.status,
            BranchSelectionStatus.HUMAN_REVIEW_REQUIRED,
        )
        self.assertIsNone(decision.selected_branch_id)
        self.assertIn(
            "no Candidate has a satisfied hard-feasibility assessment",
            decision.rationale,
        )

    def test_v1_selection_rules_reload_as_compatibility_without_hard_gate(self):
        payload = BranchSelectionRules(
            mode=BranchSelectionMode.AUTOMATIC,
            minimum_score=0.7,
            minimum_margin=0.2,
            automatic_authority_id="algorithm-selector",
        ).to_dict()
        payload["schema"] = "BranchSelectionRules@1"
        payload.pop("require_hard_feasibility")

        restored = BranchSelectionRules.from_dict(payload)

        self.assertFalse(restored.require_hard_feasibility)

    def test_stale_scorecard_and_unauthorised_algorithm_fail_closed(self):
        _, _, portfolio = _candidate_setup()
        original = _scorecard(portfolio, "classical", 0.9)
        stale = replace(
            original,
            branch_revision_digest="0" * 64,
            research_profile=replace(
                original.research_profile,
                branch_revision_digest="0" * 64,
            ),
        )
        with self.assertRaisesRegex(BranchResearchError, "stale"):
            evaluate_branch_selection(
                portfolio,
                decision_id="stale-style",
                rules=BranchSelectionRules(
                    mode=BranchSelectionMode.AUTOMATIC,
                    minimum_score=0.7,
                    minimum_margin=0.2,
                    automatic_authority_id="algorithm-selector",
                ),
                scorecards=(stale, _scorecard(portfolio, "gothic", 0.4)),
                decision_ref=DECISION_REF,
                evidence_refs=(EVIDENCE,),
                rationale="Reject stale score evidence.",
            )
        untrusted = replace(
            portfolio,
            selection_policy=SelectionPolicy(
                authority_ids=("human-owner",),
                source_refs=(EVIDENCE,),
            ),
        )
        with self.assertRaisesRegex(BranchResearchError, "not permitted"):
            _automatic_decision(untrusted)

    def test_tampered_automatic_winner_and_record_pairing_fail_closed(self):
        _, _, portfolio = _candidate_setup()
        automatic = _automatic_decision(portfolio)
        payload = automatic.to_dict()
        payload["selected_branch_id"] = "gothic"
        payload["pruned_branch_ids"] = ["classical"]
        payload["authority_id"] = "human-owner"
        with self.assertRaisesRegex(BranchResearchError, "replay"):
            BranchSelectionDecision.from_dict(payload)

        human = evaluate_branch_selection(
            portfolio,
            decision_id="human-style-record-binding",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.HUMAN_IN_THE_LOOP,
                minimum_score=0.0,
                minimum_margin=0.0,
            ),
            scorecards=_scorecards(portfolio),
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="Owner selected Gothic.",
            human_branch_id="gothic",
            human_authority_id="human-owner",
        )
        with self.assertRaisesRegex(BranchResearchError, "exact P036"):
            _apply_branch_selection(
                portfolio,
                human,
                selection_record=_selection_ref(automatic),
            )

    def test_exact_tie_requires_human_even_when_minimum_margin_is_zero(self):
        _, _, portfolio = _candidate_setup()
        decision = evaluate_branch_selection(
            portfolio,
            decision_id="tied-style",
            rules=BranchSelectionRules(
                mode=BranchSelectionMode.AUTOMATIC,
                minimum_score=0.7,
                minimum_margin=0.0,
                automatic_authority_id="algorithm-selector",
            ),
            scorecards=_scorecards(
                portfolio,
                classical=0.8,
                gothic=0.8,
            ),
            decision_ref=DECISION_REF,
            evidence_refs=(EVIDENCE,),
            rationale="A tie has no deterministic winner.",
        )

        self.assertIs(
            decision.status,
            BranchSelectionStatus.HUMAN_REVIEW_REQUIRED,
        )
        self.assertIsNone(decision.selected_branch_id)


class BranchScopeTests(unittest.TestCase):
    def test_query_carries_scope_and_cannot_reopen_pruned_vocabulary(self):
        *_, scope = _selected_scope()
        query = compile_branch_query(scope, _need())

        payload = query.to_dict()
        self.assertEqual("classical", payload["branch_scope"]["branch_identity"]["branch_id"])
        self.assertEqual(set(ACTIVE_DECISIONS), set(scope.active_decision_refs))
        self.assertIn("classical", query.search_terms)
        self.assertNotIn("gothic", query.search_terms)
        self.assertTrue(query.allows_url("https://archive.example.org/page"))
        self.assertFalse(query.allows_url("https://evil-example.org/page"))
        self.assertEqual(
            "https://archive.example.org/page",
            require_branch_source_url(
                query,
                "https://archive.example.org/page",
                field="requested_url",
            ),
        )
        with self.assertRaisesRegex(BranchResearchError, "allowlist"):
            require_branch_source_url(
                query,
                "https://evil-example.org/page",
                field="redirected_url",
            )
        with self.assertRaisesRegex(BranchResearchError, "universe"):
            compile_branch_query(
                scope,
                _need("declaration:flying-buttress"),
            )
        with self.assertRaisesRegex(BranchResearchError, "widened"):
            compile_branch_query(
                scope,
                replace(_need(), domain_allowlist=("another.org",)),
            )
        with self.assertRaisesRegex(BranchResearchError, "pruned branch term"):
            compile_branch_query(
                scope,
                replace(_need(), search_terms=("gothic architecture",)),
            )

    def test_branch_prompt_requires_query_and_scope_echo(self):
        *_, scope = _selected_scope()
        query = compile_branch_query(scope, _need())
        prompt = research_prompt(
            query,
            snapshot_ref="project://portfolio-project/snapshot.json",
            snapshot_text_sha256="a" * 64,
            windows=({"window_start": 0, "window_end": 8, "text": "evidence"},),
        )
        self.assertEqual("BranchPrecedentResearchPrompt@1", prompt["schema"])
        output = {
            "schema": "BranchPrecedentResearchOutput@1",
            "query_id": query.query_id,
            "query_digest": query.query_digest,
            "scope_digest": scope.scope_digest,
            "candidates": [
                {
                    "fact_id": "column-order-fact",
                    "statement": "Eight columns form the front row.",
                    "quote": "Eight columns",
                    "quote_start": 0,
                    "quote_end": 13,
                    "decision_refs": ["declaration:column-order"],
                    "topic": "support",
                    "strength": "soft",
                }
            ],
        }
        facts, _ = parse_research_output(
            output,
            query=query,
            snapshot_ref="project://portfolio-project/snapshot.json",
            snapshot_text="Eight columns form the front row.",
            snapshot_text_sha256="a" * 64,
            annotator="model:test",
        )
        self.assertEqual(1, len(facts))
        output["scope_digest"] = "0" * 64
        with self.assertRaisesRegex(ResearchError, "crossed branch"):
            parse_research_output(
                output,
                query=query,
                snapshot_ref="project://portfolio-project/snapshot.json",
                snapshot_text="Eight columns form the front row.",
                snapshot_text_sha256="a" * 64,
                annotator="model:test",
            )

    def test_scope_rejects_unrelated_operational_branch(self):
        option_set, state, portfolio = _candidate_setup()
        decision = _automatic_decision(portfolio)
        selection_ref = _selection_ref(decision)
        selected = _apply_branch_selection(
            portfolio,
            decision,
            selection_record=selection_ref,
        )
        wrong_state = replace(
            state,
            branch=replace(
                state.branch,
                branch_id="unrelated-source",
                epoch=state.branch.epoch + 1,
            ),
        )
        source_option_set_ref, _ = _source_refs(option_set, state)
        _, wrong_state_ref = _source_refs(option_set, wrong_state)
        with self.assertRaisesRegex(BranchResearchError, "disagree"):
            _compile_branch_research_scope(
                selected,
                decision,
                scope_id="forged-source",
                selection_record=selection_ref,
                source_option_set=option_set,
                source_option_set_ref=source_option_set_ref,
                source_state=wrong_state,
                source_state_ref=wrong_state_ref,
                context_refs=(EVIDENCE,),
            )

    def test_typed_snapshot_checks_requested_and_final_redirect_urls(self):
        *_, scope = _selected_scope()
        query = compile_branch_query(scope, _need())
        text = "Eight columns form the front row."
        payload = {
            "schema": "WebEvidenceSnapshot@1",
            "url": "https://another.org/final",
            "retrieved_at": "2026-08-29T00:00:00Z",
            "content_sha256": "a" * 64,
            "content_bytes": len(text),
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "adoption_authority": False,
            "prompt_injection_surface": False,
            "canonical_write_authority": False,
        }
        with self.assertRaisesRegex(BranchResearchError, "final_url"):
            bind_branch_snapshot(
                query,
                requested_url="https://example.org/start",
                snapshot=payload,
            )

        class RedirectedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return "https://archive.example.org/final"

            def read(self, _):
                return b"redirected evidence"

        with patch(
            "archive.archflow.adapters.web_evidence.urllib.request.urlopen",
            return_value=RedirectedResponse(),
        ):
            snapshot = fetch_web_evidence(
                "https://example.org/start",
                retrieved_at="2026-08-29T00:00:00Z",
            )
        self.assertEqual(
            "https://archive.example.org/final",
            snapshot.url,
        )


class BranchBasisTests(unittest.TestCase):
    def test_equal_decision_and_record_ids_do_not_mix_across_branches(self):
        *_, classical_scope = _selected_scope()
        *_, gothic_scope = _selected_scope(branch_id="gothic")
        classical_query = compile_branch_query(classical_scope, _need())
        gothic_query = compile_branch_query(gothic_scope, _need())
        snapshot_name = "branch-snapshot-shared.json"
        classical_text = "Classical columns follow the adopted order."
        gothic_text = "Gothic supports follow the adopted order."
        classical_adoption = _wrapped_adoption(
            classical_query,
            _branch_record_uri(classical_scope, snapshot_name),
            classical_text,
        )
        gothic_adoption = _wrapped_adoption(
            gothic_query,
            _branch_record_uri(gothic_scope, snapshot_name),
            gothic_text,
        )
        records = (
            (
                _branch_record_uri(classical_scope, "query.json"),
                classical_query.to_dict(),
            ),
            (
                _branch_record_uri(gothic_scope, "query.json"),
                gothic_query.to_dict(),
            ),
            _snapshot(
                classical_query,
                snapshot_name,
                text=classical_text,
            ),
            _snapshot(gothic_query, snapshot_name, text=gothic_text),
            (
                _branch_record_uri(classical_scope, "adoption.json"),
                classical_adoption.to_dict(),
            ),
            (
                _branch_record_uri(gothic_scope, "adoption.json"),
                gothic_adoption.to_dict(),
            ),
            (
                "legacy-query.json",
                {
                    "schema": "PrecedentQuery@1",
                    "query_id": "query-column-order",
                    "decision_refs": ["declaration:column-order"],
                },
            ),
        )

        index = build_branch_basis_index(records, scope=classical_scope)

        facts = index.decisions["declaration:column-order"]["facts"]
        self.assertEqual(1, len(facts))
        self.assertIn("Classical", facts[0]["statement"])
        self.assertNotIn("Gothic", str(index.to_dict()))
        self.assertEqual(("declaration:roof-form",), index.uncovered)
        column_inputs = _p079_inputs(
            classical_scope,
            ("declaration:column-order",),
        )
        context = compile_branch_decision_context(
            index,
            index_record=_index_ref(index),
            decision_refs=("declaration:column-order",),
            universe=column_inputs[0],
            policy=column_inputs[1],
            closure=column_inputs[2],
            sufficiency=column_inputs[3],
            frontier=column_inputs[4],
        )
        self.assertEqual(classical_scope.scope_digest, context.to_dict()["scope_digest"])
        roof_inputs = _p079_inputs(
            classical_scope,
            ("declaration:roof-form",),
        )
        with self.assertRaisesRegex(BasisIndexError, "uncovered"):
            compile_branch_decision_context(
                index,
                index_record=_index_ref(index),
                decision_refs=("declaration:roof-form",),
                universe=roof_inputs[0],
                policy=roof_inputs[1],
                closure=roof_inputs[2],
                sufficiency=roof_inputs[3],
                frontier=roof_inputs[4],
            )
        next_queries = compile_next_branch_queries(
            index,
            needs=(_need("declaration:roof-form"),),
        )
        self.assertEqual(
            ("declaration:roof-form",),
            next_queries[0].decision_refs,
        )
        self.assertEqual(classical_scope.scope_digest, next_queries[0].scope.scope_digest)
        progress_inputs = _p079_inputs(
            classical_scope,
            ACTIVE_DECISIONS,
            claim_families=(),
        )
        progress = compile_branch_rag_progress(
            index,
            next_queries=next_queries,
            universe=progress_inputs[0],
            policy=progress_inputs[1],
            closure=progress_inputs[2],
            sufficiency=progress_inputs[3],
            frontier=progress_inputs[4],
        )
        self.assertIs(progress.status, BranchRAGProgressStatus.CONTINUE)
        self.assertEqual(
            {
                "complete": 1,
                "total": 2,
            },
            progress.to_dict()["progress"],
        )

    def test_source_outside_branch_allowlist_is_rejected(self):
        *_, scope = _selected_scope()
        query = compile_branch_query(scope, _need())
        snapshot_name = "branch-snapshot-outside.json"
        text = "Out-of-scope source."
        adoption = _wrapped_adoption(
            query,
            _branch_record_uri(scope, snapshot_name),
            text,
        )
        with self.assertRaisesRegex(BasisIndexError, "allowlist"):
            build_branch_basis_index(
                (
                    (
                        _branch_record_uri(scope, "branch-query.json"),
                        query.to_dict(),
                    ),
                    _snapshot(
                        query,
                        snapshot_name,
                        text=text,
                        url="https://another.org/page",
                    ),
                    (
                        _branch_record_uri(scope, "branch-adoption.json"),
                        adoption.to_dict(),
                    ),
                ),
                scope=scope,
            )

    def test_same_filename_cannot_redirect_fact_to_another_branch(self):
        *_, classical_scope = _selected_scope()
        *_, gothic_scope = _selected_scope(branch_id="gothic")
        query = compile_branch_query(classical_scope, _need())
        snapshot_name = "branch-snapshot-same-name.json"
        text = "Classical columns follow the adopted order."
        poisoned = _wrapped_adoption(
            query,
            _branch_record_uri(gothic_scope, snapshot_name),
            text,
        )
        with self.assertRaisesRegex(BasisIndexError, "snapshot record is missing"):
            build_branch_basis_index(
                (
                    (
                        _branch_record_uri(classical_scope, "query.json"),
                        query.to_dict(),
                    ),
                    _snapshot(query, snapshot_name, text=text),
                    (
                        _branch_record_uri(classical_scope, "adoption.json"),
                        poisoned.to_dict(),
                    ),
                ),
                scope=classical_scope,
            )

    def test_raw_or_non_http_snapshot_cannot_enter_branch_index(self):
        *_, scope = _selected_scope()
        query = compile_branch_query(scope, _need())
        name = "branch-snapshot-raw.json"
        text = "Classical columns follow the adopted order."
        adoption = _wrapped_adoption(
            query,
            _branch_record_uri(scope, name),
            text,
        )
        raw = {
            "schema": "WebEvidenceSnapshot@1",
            "url": "https://example.org/source",
            "retrieved_at": "2026-08-29T00:00:00Z",
            "content_sha256": "a" * 64,
            "content_bytes": len(text),
            "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "adoption_authority": False,
            "prompt_injection_surface": False,
            "canonical_write_authority": False,
        }
        base_records = (
            (
                _branch_record_uri(scope, "query.json"),
                query.to_dict(),
            ),
            (
                _branch_record_uri(scope, "adoption.json"),
                adoption.to_dict(),
            ),
        )
        with self.assertRaisesRegex(BasisIndexError, "snapshot record is missing"):
            build_branch_basis_index(
                (
                    *base_records,
                    (_branch_record_uri(scope, name), raw),
                ),
                scope=scope,
            )
        with self.assertRaisesRegex(BasisIndexError, "HTTP"):
            build_branch_basis_index(
                (
                    *base_records,
                    _snapshot(
                        query,
                        name,
                        text=text,
                        url="ftp://example.org/source",
                    ),
                ),
                scope=scope,
            )


class BranchFrontierAcceptanceTests(unittest.TestCase):
    @staticmethod
    def _progress(index, inputs):
        next_queries = compile_next_branch_queries(
            index,
            needs=tuple(_need(ref) for ref in index.uncovered),
        )
        return compile_branch_rag_progress(
            index,
            next_queries=next_queries,
            universe=inputs[0],
            policy=inputs[1],
            closure=inputs[2],
            sufficiency=inputs[3],
            frontier=inputs[4],
        )

    def test_zero_uncovered_is_not_complete_when_evidence_is_insufficient(self):
        *_, scope = _selected_scope()
        index = _covered_branch_index(scope)
        inputs = _p079_inputs(
            scope,
            ACTIVE_DECISIONS,
            minimum_source_families=2,
        )

        progress = self._progress(index, inputs)

        self.assertEqual((), index.uncovered)
        self.assertIs(inputs[4].status, FrontierStatus.CONTINUE)
        self.assertIs(progress.status, BranchRAGProgressStatus.CONTINUE)

    def test_progress_maps_universe_expansion_and_human_review(self):
        *_, scope = _selected_scope()
        empty_index = build_branch_basis_index((), scope=scope)
        expansion_inputs = _p079_inputs(
            scope,
            ACTIVE_DECISIONS,
            claim_families=(),
            expansion=True,
        )
        self.assertIs(
            self._progress(empty_index, expansion_inputs).status,
            BranchRAGProgressStatus.UNIVERSE_EXPANSION_REQUIRED,
        )

        covered_index = _covered_branch_index(scope)
        conflict_inputs = _p079_inputs(
            scope,
            ACTIVE_DECISIONS,
            claim_families=("family:independent-a", "family:independent-b"),
            conflict=True,
        )
        self.assertIs(
            self._progress(covered_index, conflict_inputs).status,
            BranchRAGProgressStatus.HUMAN_REVIEW_REQUIRED,
        )

    def test_complete_frontier_gates_context_and_progress(self):
        *_, scope = _selected_scope()
        index = _covered_branch_index(scope)
        inputs = _p079_inputs(scope, ACTIVE_DECISIONS)

        progress = self._progress(index, inputs)
        context = compile_branch_decision_context(
            index,
            index_record=_index_ref(index),
            decision_refs=ACTIVE_DECISIONS,
            universe=inputs[0],
            policy=inputs[1],
            closure=inputs[2],
            sufficiency=inputs[3],
            frontier=inputs[4],
        )

        self.assertIs(progress.status, BranchRAGProgressStatus.COMPLETE)
        self.assertEqual(inputs[4].frontier_digest, context.frontier_digest)

    def test_foreign_universe_and_forged_empty_frontier_fail_closed(self):
        *_, scope = _selected_scope()
        index = _covered_branch_index(scope)
        valid = _p079_inputs(scope, ACTIVE_DECISIONS)
        foreign_universe = replace(valid[0], scope_digest="f" * 64)
        with self.assertRaisesRegex(BasisIndexError, "branch research scope"):
            compile_branch_rag_progress(
                index,
                next_queries=(),
                universe=foreign_universe,
                policy=valid[1],
                closure=valid[2],
                sufficiency=valid[3],
                frontier=valid[4],
            )

        insufficient = _p079_inputs(
            scope,
            ACTIVE_DECISIONS,
            minimum_source_families=2,
        )
        forged = replace(
            insufficient[4],
            work_items=(),
            status=FrontierStatus.COMPLETE,
        )
        with self.assertRaisesRegex(BasisIndexError, "does not recompile"):
            compile_branch_decision_context(
                index,
                index_record=_index_ref(index),
                decision_refs=ACTIVE_DECISIONS,
                universe=insufficient[0],
                policy=insufficient[1],
                closure=insufficient[2],
                sufficiency=insufficient[3],
                frontier=forged,
            )


class BranchPromptConsumptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_branch_context_reaches_authoring_prompt(self):
        brief, program, site, policy, state, maturity, gate = _spatial_inputs()
        *_, scope = _selected_scope(source_state=state)
        query = compile_branch_query(scope, _need())
        snapshot_name = "branch-snapshot-authoring.json"
        text = "Classical columns follow the adopted order."
        adoption = _wrapped_adoption(
            query,
            _branch_record_uri(scope, snapshot_name),
            text,
        )
        index = build_branch_basis_index(
            (
                (
                    _branch_record_uri(scope, "branch-query-authoring.json"),
                    query.to_dict(),
                ),
                _snapshot(query, snapshot_name, text=text),
                (
                    _branch_record_uri(
                        scope,
                        "branch-adoption-authoring.json",
                    ),
                    adoption.to_dict(),
                ),
            ),
            scope=scope,
        )
        index_ref = _index_ref(index)
        context_inputs = _p079_inputs(
            scope,
            ("declaration:column-order",),
        )
        context = compile_branch_decision_context(
            index,
            index_record=index_ref,
            decision_refs=("declaration:column-order",),
            universe=context_inputs[0],
            policy=context_inputs[1],
            closure=context_inputs[2],
            sufficiency=context_inputs[3],
            frontier=context_inputs[4],
        )
        proposal = _semantic_proposal(brief, program)
        provider = _ScriptedProvider(
            lambda request: semantic_spatial_authoring_output(request, proposal)
        )

        result = await _author_semantic_spatial_option(
            provider,
            request_id="branch-conditioned-authoring",
            state=state,
            maturity=maturity,
            phase_gate=gate,
            program=program,
            site_context=site,
            build_policy=policy,
            branch_research_context=context,
            expected_branch_scope_digest=scope.scope_digest,
        )

        self.assertIs(result.receipt.status, SemanticSpatialAuthoringStatus.ACCEPTED)
        prompt_context = provider.requests[0].payload["branch_research_context"]
        self.assertEqual(scope.scope_digest, prompt_context["scope_digest"])
        self.assertEqual(
            "classical",
            prompt_context["branch_scope"]["branch_identity"]["branch_id"],
        )
        self.assertNotIn("Gothic", str(prompt_context))
        with self.assertRaisesRegex(TypeError, "unexpected keyword"):
            await author_semantic_spatial_option(
                provider,
                request_id="forged-index-record",
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                branch_research_context=context,
            )
        *_, gothic_scope = _selected_scope(
            branch_id="gothic",
            source_state=state,
        )
        gothic_query = compile_branch_query(gothic_scope, _need())
        gothic_text = "Gothic supports follow the adopted order."
        gothic_snapshot_name = "branch-snapshot-authoring.json"
        gothic_adoption = _wrapped_adoption(
            gothic_query,
            _branch_record_uri(gothic_scope, gothic_snapshot_name),
            gothic_text,
        )
        gothic_index = build_branch_basis_index(
            (
                (
                    _branch_record_uri(gothic_scope, "query.json"),
                    gothic_query.to_dict(),
                ),
                _snapshot(
                    gothic_query,
                    gothic_snapshot_name,
                    text=gothic_text,
                ),
                (
                    _branch_record_uri(gothic_scope, "adoption.json"),
                    gothic_adoption.to_dict(),
                ),
            ),
            scope=gothic_scope,
        )
        gothic_inputs = _p079_inputs(
            gothic_scope,
            ("declaration:column-order",),
        )
        gothic_context = compile_branch_decision_context(
            gothic_index,
            index_record=_index_ref(gothic_index),
            decision_refs=("declaration:column-order",),
            universe=gothic_inputs[0],
            policy=gothic_inputs[1],
            closure=gothic_inputs[2],
            sufficiency=gothic_inputs[3],
            frontier=gothic_inputs[4],
        )
        with self.assertRaisesRegex(ValueError, "foreign or stale"):
            await _author_semantic_spatial_option(
                provider,
                request_id="cross-branch-authoring",
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                branch_research_context=gothic_context,
                expected_branch_scope_digest=scope.scope_digest,
            )


class BranchProductionBindingTests(unittest.TestCase):
    def test_valid_foreign_candidate_index_cannot_bind_to_predecessor(self):
        *_, classical_selected, classical_scope = _selected_scope()
        *_, gothic_scope = _selected_scope(branch_id="gothic")
        classical_index = build_branch_basis_index((), scope=classical_scope)
        gothic_index = build_branch_basis_index((), scope=gothic_scope)
        handoff = compile_selected_branch_handoff(
            classical_selected,
            expected_portfolio_digest=classical_selected.portfolio_digest,
            expected_revision_digest=(
                classical_selected.selected_branch.head.revision_digest
            ),
        )
        selected_schematic = SelectedSchematicInput.from_handoff(handoff)

        self.assertTrue(
            _portfolio_uses_branch_research_selection(classical_selected)
        )
        _require_branch_index_matches_selected_predecessor(
            branch_index=classical_index,
            portfolio=classical_selected,
            selected_schematic=selected_schematic,
            selection_ref=classical_scope.selection_record_ref,
            expected_scope_digest=classical_scope.scope_digest,
        )
        with self.assertRaisesRegex(
            ArchitecturalRevisionError,
            "selected predecessor Candidate",
        ):
            _require_branch_index_matches_selected_predecessor(
                branch_index=gothic_index,
                portfolio=classical_selected,
                selected_schematic=selected_schematic,
                selection_ref=classical_scope.selection_record_ref,
                expected_scope_digest=gothic_scope.scope_digest,
            )


class BranchPersistenceIntegrationTests(unittest.TestCase):
    def test_selection_research_adoption_and_index_reload_from_one_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / PROJECT_ID
            canonical = initialize_canonical_project(PROJECT_ID)
            repository = FilesystemProjectRepository.initialize(
                root,
                project_id=PROJECT_ID,
                initial_state=canonical_state_to_dict(canonical),
            )
            run = repository.create_run("run-001")
            option_set, state, portfolio = _candidate_setup(run)
            archive = BranchResearchArchive(repository, run=run)
            decision = _automatic_decision(portfolio)
            decision_record = archive.save_selection(decision)
            missing_decision_record = replace(
                decision_record,
                relative_path=(
                    f"runs/{run.run_id}/records/branch-selection-missing-"
                    f"{decision_record.sha256}.json"
                ),
            )
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "selection record is missing",
            ):
                archive.apply_selection(portfolio, missing_decision_record)
            persisted = archive.apply_selection(
                portfolio,
                decision_record,
            )
            selected = persisted.portfolio
            source_option_set_ref, source_state_ref = (
                archive.save_source_inputs(option_set, state)
            )
            missing_source_state_ref = replace(
                source_state_ref,
                relative_path=(
                    f"runs/{run.run_id}/records/branch-source-state-missing-"
                    f"{source_state_ref.sha256}.json"
                ),
            )
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "source records are missing",
            ):
                archive.create_scope(
                    scope_id="missing-source",
                    selection_ref=decision_record,
                    source_option_set_ref=source_option_set_ref,
                    source_state_ref=missing_source_state_ref,
                    context_refs=(EVIDENCE,),
                )
            scope, _ = archive.create_scope(
                scope_id="classical-stage-01",
                selection_ref=decision_record,
                source_option_set_ref=source_option_set_ref,
                source_state_ref=source_state_ref,
                context_refs=(EVIDENCE,),
            )
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "research profile",
            ):
                archive.save_scope(
                    replace(
                        scope,
                        branch_search_terms=("unrelated-style",),
                        domain_allowlist=("evil.example",),
                    )
                )
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "latest selected Candidate",
            ):
                archive.save_scope(
                    replace(
                        scope,
                        source_branch=replace(
                            scope.source_branch,
                            branch_id="unrelated-source",
                            epoch=scope.source_branch.epoch + 1,
                        ),
                    )
                )
            missing_state_ref = replace(
                scope.source_state_ref,
                relative_path=(
                    f"runs/{run.run_id}/records/branch-source-state-missing-"
                    f"{scope.source_state_ref.sha256}.json"
                ),
            )
            missing_context_refs = tuple(
                sorted(
                    {
                        *(
                            item
                            for item in scope.context_refs
                            if item != scope.source_state_ref.uri
                        ),
                        missing_state_ref.uri,
                    }
                )
            )
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "source records are missing",
            ):
                archive.save_scope(
                    replace(
                        scope,
                        source_state_ref=missing_state_ref,
                        context_refs=missing_context_refs,
                    )
                )
            first_frontier_inputs = _p079_inputs(
                scope,
                ACTIVE_DECISIONS,
                claim_families=(),
            )
            first_wave = archive.advance_feedback_wave(
                scope,
                needs=(
                    _need("declaration:column-order"),
                    _need("declaration:roof-form"),
                ),
                universe=first_frontier_inputs[0],
                policy=first_frontier_inputs[1],
                closure=first_frontier_inputs[2],
                sufficiency=first_frontier_inputs[3],
                frontier=first_frontier_inputs[4],
            )
            self.assertIs(
                first_wave.progress.status,
                BranchRAGProgressStatus.CONTINUE,
            )
            self.assertEqual(ACTIVE_DECISIONS, first_wave.index.uncovered)
            query = next(
                item
                for item in first_wave.next_queries
                if item.decision_refs == ("declaration:column-order",)
            )
            text = "Eight columns form the front row."
            text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            snapshot_payload = {
                "schema": "WebEvidenceSnapshot@1",
                "url": "https://archive.example.org/pantheon",
                "retrieved_at": "2026-08-29T00:00:00Z",
                "content_sha256": hashlib.sha256(
                    f"raw:{text}".encode("utf-8")
                ).hexdigest(),
                "content_bytes": len(text.encode("utf-8")),
                "text": text,
                "text_sha256": text_digest,
                "adoption_authority": False,
                "prompt_injection_surface": False,
                "canonical_write_authority": False,
            }
            snapshot_ref = archive.save_snapshot(
                query,
                snapshot_id="column-order",
                requested_url="https://archive.example.org/pantheon",
                payload=snapshot_payload,
            )
            output = {
                "schema": "BranchPrecedentResearchOutput@1",
                "query_id": query.query_id,
                "query_digest": query.query_digest,
                "scope_digest": scope.scope_digest,
                "candidates": [
                    {
                        "fact_id": "column-order-fact",
                        "statement": text,
                        "quote": text,
                        "quote_start": 0,
                        "quote_end": len(text),
                        "decision_refs": ["declaration:column-order"],
                        "topic": "support",
                        "strength": "soft",
                    }
                ],
            }
            facts, _ = parse_research_output(
                output,
                query=query,
                snapshot_ref=snapshot_ref.uri,
                snapshot_text=text,
                snapshot_text_sha256=text_digest,
                annotator="model:test",
            )
            adopted = PrecedentAdoption(
                adoption_id="column-order-adoption",
                authority_id="human-owner",
                adopted_at="2026-08-29T00:01:00Z",
                facts=facts,
            )
            archive.save_adoption(scope, bind_branch_adoption(query, adopted))
            second_frontier_inputs = _p079_inputs(
                scope,
                ACTIVE_DECISIONS,
                claim_families=(),
            )
            second_wave = archive.advance_feedback_wave(
                scope,
                needs=(_need("declaration:roof-form"),),
                universe=second_frontier_inputs[0],
                policy=second_frontier_inputs[1],
                closure=second_frontier_inputs[2],
                sufficiency=second_frontier_inputs[3],
                frontier=second_frontier_inputs[4],
            )
            index = second_wave.index
            index_ref = second_wave.index_ref
            self.assertEqual(
                index,
                archive.load_index(
                    index_ref,
                    expected_scope_digest=scope.scope_digest,
                ),
            )
            context_inputs = _p079_inputs(
                scope,
                ("declaration:column-order",),
            )
            decision_context = archive.load_decision_context(
                index_ref,
                expected_scope_digest=scope.scope_digest,
                decision_refs=("declaration:column-order",),
                universe=context_inputs[0],
                policy=context_inputs[1],
                closure=context_inputs[2],
                sufficiency=context_inputs[3],
                frontier=context_inputs[4],
            )
            self.assertEqual(
                context_inputs[4].frontier_digest,
                decision_context.frontier_digest,
            )
            forged_payload = json.loads(json.dumps(index.to_dict()))
            forged_payload["decisions"]["declaration:column-order"][
                "facts"
            ][0]["statement"] = "Invented after adoption."
            forged_ref = repository.put_json(
                run=run,
                destination=PersistenceDestination(
                    PersistenceArea.RUN_BRANCH,
                    run_id=run.run_id,
                    branch_id=scope.branch_id,
                ),
                record_kind="branch-basis-forged",
                payload=forged_payload,
            )
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "re-derive",
            ):
                archive.load_index(
                    forged_ref,
                    expected_scope_digest=scope.scope_digest,
                )
            progress_ref = second_wave.progress_ref

            self.assertTrue(
                all(
                    "/branches/classical/records/" in ref
                    for ref in (
                        snapshot_ref.uri,
                        index_ref.uri,
                        progress_ref.uri,
                        *(
                            item.uri
                            for item in first_wave.query_refs
                        ),
                    )
                )
            )
            reopened = FilesystemProjectRepository.open(root)
            loaded = BranchBasisIndex.from_dict(reopened.load_json(index_ref))
            self.assertEqual(index, loaded)
            self.assertEqual(("declaration:roof-form",), loaded.uncovered)
            self.assertEqual((), reopened.verify().orphan_paths)

            later = revise_branch(
                selected,
                expected_portfolio_digest=selected.portfolio_digest,
                branch_id="gothic",
                revision_id="gothic-research-revision",
                option=_option("gothic-v2", shape=6),
                authority_id="algorithm-selector",
                decision_ref="decision:revise-parked-candidate",
                rationale="Retain a newly revised parked alternative.",
                evidence_refs=(EVIDENCE,),
                transition_id="revise-gothic-after-scope",
            )
            BranchPortfolioArchive(repository, run=run).save(later)
            with self.assertRaisesRegex(
                BranchResearchArchiveError,
                "latest selected Candidate revision",
            ):
                archive.save_query(query)


if __name__ == "__main__":
    unittest.main()
