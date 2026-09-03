from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.state.design_portfolio import AdviceDisposition, BranchLifecycle, DesignOptionPortfolio, DesignPortfolioError, ExpertAdviceResolution, ParetoBranchObservation, SelectionPolicy, attach_pareto_observation, combine_branches, compile_selected_branch_handoff, fork_branch, initialize_design_portfolio, park_branch, reject_branch, revise_branch, select_branch
from archflow.state.spatial import ComponentMaturity, ConstraintResponseStatus, DesignComponent, MassingVolume, SchematicOption, SchematicOptionSet, SpatialConstraintResponse, SpatialGridBasis, SpatialLevel, SpatialOptionProposal, SpatialZone
from archflow.state.stage_workflow import DesignPhase
from archflow.state.spatial import SiteBounds


PROJECT_ID = "portfolio-project"
RUN_ID = "run-001"
EVIDENCE = "project://portfolio-project/input/research.json"
REQUIREMENT_A = "commitment:public-access"
REQUIREMENT_B = "obligation:protected-site"
DECISION = "project://portfolio-project/runs/run-001/records/decision.json"


def _run(
    project_id: str = PROJECT_ID,
    *,
    digest_char: str = "a",
) -> RunRef:
    return RunRef(
        project_id=project_id,
        run_id=RUN_ID,
        base=ProjectVersionRef(
            project_id=project_id,
            version=0,
            state_sha256=digest_char * 64,
        ),
    )


def _option(
    option_id: str,
    *,
    requirement_refs: tuple[str, ...] = (
        REQUIREMENT_A,
        REQUIREMENT_B,
    ),
    evidence_ref: str = EVIDENCE,
    shape: int = 0,
) -> SchematicOption:
    level = SpatialLevel(
        level_id="ground",
        base_y=0,
        height=4,
        source_refs=(evidence_ref,),
    )
    volume = MassingVolume(
        volume_id="primary",
        bounds=SiteBounds(
            minimum=(shape, 0, 0),
            maximum=(shape + 1, 3, 1),
        ),
        level_ids=("ground",),
        source_refs=(evidence_ref,),
    )
    zone = SpatialZone(
        zone_id="main",
        program_node_refs=("program-node:main",),
        level_ids=("ground",),
        volume_ids=("primary",),
        source_refs=(evidence_ref,),
    )
    responses = tuple(
        SpatialConstraintResponse(
            response_id=f"response-{index}",
            constraint_ref=ref,
            status=ConstraintResponseStatus.SATISFIED,
            rationale=f"Option {option_id} addresses {ref}.",
            source_refs=(evidence_ref,),
        )
        for index, ref in enumerate(requirement_refs, start=1)
    )
    proposal = SpatialOptionProposal(
        option_id=option_id,
        label=f"Project-authored option {option_id}",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(
            horizontal_area_per_cell=1.0,
            area_unit="project_grid_unit",
            source_refs=(evidence_ref,),
        ),
        footprint_cells=((shape, 0), (shape + 1, 0)),
        levels=(level,),
        volumes=(volume,),
        zones=(zone,),
        components=(
            DesignComponent(
                component_id="building",
                parent_component_id=None,
                semantic_kind="building",
                intent="Own the selected schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=("primary",),
                unresolved_child_roles=(),
                source_refs=(evidence_ref,),
            ),
            DesignComponent(
                component_id="primary-support",
                parent_component_id="building",
                semantic_kind="structural-support",
                intent="Carry the selected schematic massing.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=(evidence_ref,),
            ),
            DesignComponent(
                component_id="primary-surface",
                parent_component_id="building",
                semantic_kind="enclosure-surface",
                intent="Resolve the selected schematic envelope.",
                maturity=ComponentMaturity.SCHEMATIC,
                revision=0,
                volume_ids=(),
                unresolved_child_roles=(),
                source_refs=(evidence_ref,),
            ),
        ),
        connections=(),
        constraint_responses=responses,
        typology_hypothesis=f"Project hypothesis {option_id}",
        palette_refs=(),
        rationale=f"Architect rationale for {option_id}.",
        responds_to_refs=requirement_refs,
        expert_advice_refs=(),
        evidence_refs=(evidence_ref,),
    )
    signature = hashlib.sha256(
        f"{option_id}:{shape}".encode("utf-8")
    ).hexdigest()
    return SchematicOption(
        proposal=proposal,
        footprint_area=2.0,
        topology_signature=signature,
    )


def _option_set(run: RunRef | None = None) -> SchematicOptionSet:
    run = run or _run()
    evidence_ref = (
        f"project://{run.project_id}/input/research.json"
    )
    options = tuple(
        sorted(
            (
                _option(
                    "branch-a",
                    evidence_ref=evidence_ref,
                    shape=0,
                ),
                _option(
                    "branch-b",
                    evidence_ref=evidence_ref,
                    shape=4,
                ),
                _option(
                    "branch-c",
                    evidence_ref=evidence_ref,
                    shape=8,
                ),
            ),
            key=lambda item: item.option_id,
        )
    )
    return SchematicOptionSet(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        branch=BranchRef(
            run=run,
            branch_id="schematic-source",
            epoch=1,
        ),
        operational_state_digest="b" * 64,
        input_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
        output_phase=DesignPhase.SCHEMATIC_DESIGN,
        program_digest="c" * 64,
        site_context_digest="d" * 64,
        build_policy_digest="e" * 64,
        phase_gate_receipt_ref=(
            f"project://{run.project_id}/runs/{run.run_id}/"
            "records/phase-gate.json"
        ),
        phase_gate_receipt_digest="f" * 64,
        compiler_id="spatial-compiler",
        compiler_version="1",
        options=options,
    )


def _policy(
    project_id: str = PROJECT_ID,
) -> SelectionPolicy:
    return SelectionPolicy(
        authority_ids=("architect-lead", "user-owner"),
        source_refs=(
            f"project://{project_id}/input/selection-authority.json",
        ),
    )


def _portfolio(run: RunRef | None = None) -> DesignOptionPortfolio:
    option_set = _option_set(run)
    return initialize_design_portfolio(
        option_set,
        portfolio_id="schematic-portfolio",
        selection_policy=_policy(option_set.project_id),
        architect_id="architect-lead",
    )


def _transition_kwargs(
    portfolio: DesignOptionPortfolio,
    *,
    transition_id: str,
    authority_id: str = "architect-lead",
) -> dict[str, object]:
    return {
        "expected_portfolio_digest": portfolio.portfolio_digest,
        "authority_id": authority_id,
        "decision_ref": DECISION,
        "rationale": f"Explicit rationale for {transition_id}.",
        "evidence_refs": (EVIDENCE,),
        "transition_id": transition_id,
    }


class DesignPortfolioTests(unittest.TestCase):
    def test_three_branch_lineage_round_trips_without_ranking(self) -> None:
        portfolio = _portfolio()

        reloaded = DesignOptionPortfolio.from_dict(portfolio.to_dict())

        self.assertEqual(reloaded, portfolio)
        self.assertEqual(
            tuple(item.branch_id for item in portfolio.branches),
            ("branch-a", "branch-b", "branch-c"),
        )
        self.assertIsNone(portfolio.selected_branch)
        self.assertFalse(portfolio.to_dict()["ranked"])
        self.assertFalse(portfolio.to_dict()["automatic_winner"])

    def test_fork_revise_combine_and_lifecycle_preserve_lineage(self) -> None:
        portfolio = _portfolio()
        advice = (
            ExpertAdviceResolution(
                advice_ref="expert-advice:structure:001",
                expert_id="structure-expert",
                disposition=AdviceDisposition.ADOPTED,
                rationale="Adopted the shorter span recommendation.",
                evidence_refs=(EVIDENCE,),
            ),
            ExpertAdviceResolution(
                advice_ref="expert-advice:circulation:001",
                expert_id="circulation-expert",
                disposition=AdviceDisposition.REJECTED,
                rationale=(
                    "Rejected the route because it conflicts with the "
                    "retained public-access commitment."
                ),
                evidence_refs=(EVIDENCE,),
            ),
        )
        portfolio = fork_branch(
            portfolio,
            parent_branch_id="branch-a",
            new_branch_id="branch-d",
            revision_id="branch-d-origin",
            option=_option("branch-d", shape=12),
            expert_resolutions=advice,
            **_transition_kwargs(portfolio, transition_id="fork-d"),
        )
        fork_head = portfolio.branch("branch-d").head
        self.assertEqual(
            fork_head.parent_revisions,
            (portfolio.branch("branch-a").head.ref,),
        )
        self.assertEqual(fork_head.expert_resolutions, advice)

        portfolio = revise_branch(
            portfolio,
            branch_id="branch-d",
            revision_id="branch-d-r1",
            option=_option("branch-d-r1", shape=13),
            expert_resolutions=advice,
            **_transition_kwargs(portfolio, transition_id="revise-d"),
        )
        self.assertEqual(len(portfolio.branch("branch-d").revisions), 2)

        portfolio = combine_branches(
            portfolio,
            parent_branch_ids=("branch-b", "branch-d"),
            new_branch_id="branch-e",
            revision_id="branch-e-origin",
            option=_option("branch-e", shape=16),
            expert_resolutions=advice,
            **_transition_kwargs(portfolio, transition_id="combine-e"),
        )
        combined = portfolio.branch("branch-e").head
        self.assertEqual(
            {item.branch_id for item in combined.parent_revisions},
            {"branch-b", "branch-d"},
        )
        self.assertEqual(
            set(combined.requirement_refs),
            {REQUIREMENT_A, REQUIREMENT_B},
        )
        self.assertIn(
            portfolio.branch("branch-b").head.option.ref,
            combined.derivation_refs,
        )
        self.assertIn(
            portfolio.branch("branch-d").head.option.ref,
            combined.derivation_refs,
        )

        portfolio = park_branch(
            portfolio,
            branch_id="branch-b",
            **_transition_kwargs(portfolio, transition_id="park-b"),
        )
        portfolio = reject_branch(
            portfolio,
            branch_id="branch-c",
            **_transition_kwargs(portfolio, transition_id="reject-c"),
        )
        with self.assertRaisesRegex(
            DesignPortfolioError,
            "not allowed to select",
        ):
            select_branch(
                portfolio,
                branch_id="branch-e",
                **_transition_kwargs(
                    portfolio,
                    transition_id="bad-select",
                    authority_id="untrusted-agent",
                ),
            )
        portfolio = select_branch(
            portfolio,
            branch_id="branch-e",
            **_transition_kwargs(
                portfolio,
                transition_id="select-e",
                authority_id="user-owner",
            ),
        )

        handoff = compile_selected_branch_handoff(
            portfolio,
            expected_portfolio_digest=portfolio.portfolio_digest,
            expected_revision_digest=(
                portfolio.branch("branch-e").head.revision_digest
            ),
        )
        self.assertEqual(handoff.branch_id, "branch-e")
        self.assertFalse(handoff.to_dict()["candidate_created"])
        self.assertIsNone(
            handoff.to_dict()["hard_usability_verdict"]
        )
        self.assertFalse(
            handoff.to_dict()["canonical_write_authority"]
        )
        self.assertEqual(
            DesignOptionPortfolio.from_dict(portfolio.to_dict()),
            portfolio,
        )

    def test_pareto_observation_cannot_choose_or_delete_branch(self) -> None:
        portfolio = _portfolio()
        statuses = tuple(
            (item.branch_id, item.lifecycle) for item in portfolio.branches
        )
        observation = ParetoBranchObservation(
            observation_ref="critic-observation:pareto:001",
            branch_revisions=tuple(
                item.head.ref for item in portfolio.branches[:2]
            ),
            objective_names=("coherence", "material-economy"),
            evidence_refs=(EVIDENCE,),
            summary="The two branches expose a non-dominating trade-off.",
        )
        portfolio = attach_pareto_observation(
            portfolio,
            observation=observation,
            **_transition_kwargs(
                portfolio,
                transition_id="observe-pareto",
            ),
        )

        self.assertIsNone(portfolio.selected_branch)
        self.assertEqual(
            tuple(
                (item.branch_id, item.lifecycle)
                for item in portfolio.branches
            ),
            statuses,
        )
        serialized = portfolio.observations[0].to_dict()
        self.assertTrue(serialized["read_only"])
        self.assertFalse(serialized["selection_authority"])
        self.assertFalse(serialized["deletion_authority"])

        observed_revision = observation.branch_revisions[0]
        portfolio = revise_branch(
            portfolio,
            branch_id="branch-a",
            revision_id="branch-a-after-observation",
            option=_option("branch-a-after-observation", shape=2),
            **_transition_kwargs(
                portfolio,
                transition_id="revise-after-observation",
            ),
        )
        self.assertEqual(
            portfolio.observations[0].branch_revisions[0],
            observed_revision,
        )
        self.assertNotEqual(
            portfolio.branch("branch-a").head.ref,
            observed_revision,
        )
        self.assertIsNone(portfolio.selected_branch)

    def test_stale_transition_and_lost_parent_requirement_fail_closed(
        self,
    ) -> None:
        portfolio = _portfolio()
        stale_digest = portfolio.portfolio_digest
        portfolio = park_branch(
            portfolio,
            branch_id="branch-a",
            **_transition_kwargs(portfolio, transition_id="park-a"),
        )
        with self.assertRaisesRegex(DesignPortfolioError, "stale base"):
            reject_branch(
                portfolio,
                expected_portfolio_digest=stale_digest,
                branch_id="branch-b",
                authority_id="architect-lead",
                decision_ref=DECISION,
                rationale="Stale decision.",
                evidence_refs=(EVIDENCE,),
                transition_id="stale-reject",
            )

        incomplete = _option(
            "branch-incomplete",
            requirement_refs=(REQUIREMENT_A,),
            shape=20,
        )
        with self.assertRaisesRegex(
            DesignPortfolioError,
            "lost parent requirement",
        ):
            fork_branch(
                portfolio,
                parent_branch_id="branch-b",
                new_branch_id="branch-incomplete",
                revision_id="branch-incomplete-origin",
                option=incomplete,
                **_transition_kwargs(
                    portfolio,
                    transition_id="invalid-fork",
                ),
            )

    def test_selected_branch_must_be_released_before_revision(self) -> None:
        portfolio = _portfolio()
        portfolio = select_branch(
            portfolio,
            branch_id="branch-a",
            **_transition_kwargs(
                portfolio,
                transition_id="select-a",
                authority_id="user-owner",
            ),
        )
        with self.assertRaisesRegex(
            DesignPortfolioError,
            "selected branch",
        ):
            revise_branch(
                portfolio,
                branch_id="branch-a",
                revision_id="branch-a-r1",
                option=_option("branch-a-r1", shape=2),
                **_transition_kwargs(
                    portfolio,
                    transition_id="revise-selected",
                ),
            )
        portfolio = park_branch(
            portfolio,
            branch_id="branch-a",
            **_transition_kwargs(
                portfolio,
                transition_id="release-a",
                authority_id="user-owner",
            ),
        )
        self.assertEqual(
            portfolio.branch("branch-a").lifecycle,
            BranchLifecycle.PARKED,
        )

    def test_cross_project_answers_remain_instance_scoped(self) -> None:
        first = _portfolio()
        other_run = _run("other-project", digest_char="9")
        second = _portfolio(other_run)

        self.assertNotEqual(
            first.source_option_set_digest,
            second.source_option_set_digest,
        )
        self.assertTrue(
            all(
                branch.head.option.proposal.evidence_refs[0].startswith(
                    "project://other-project/"
                )
                for branch in second.branches
            )
        )
        with self.assertRaisesRegex(
            DesignPortfolioError,
            "different projects",
        ):
            replace(
                second,
                base=first.base,
            )


if __name__ == "__main__":
    unittest.main()
