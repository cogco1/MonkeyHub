from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from archflow.project.refs import BranchRef, ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.state.design_portfolio import (
    BranchRevisionRef, DesignBranch, DesignOptionPortfolio, DesignPortfolioError,
    DesignStage, SelectedBranchHandoff, SelectionPolicy, advance_branch,
    fork_branch, initialize_branch,
)
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


def _retained_portfolio_payload() -> dict[str, object]:
    """The old stored shape, built without the retired lifecycle writers."""
    options = _option_set()
    return {
        "schema": "DesignOptionPortfolio@1", "portfolio_id": "schematic-portfolio",
        "project_id": options.project_id, "run_id": options.run_id,
        "base": options.base.to_dict(), "source_option_set_digest": options.option_set_digest,
        "operational_state_digest": options.operational_state_digest,
        "selection_policy": _policy().to_dict(),
        "branches": [
            {"schema": "DesignBranch@1", "branch_id": option.option_id,
             "lifecycle": "active", "lifecycle_evidence_refs": [], "revisions": [
                 {"schema": "DesignBranchRevision@1", "revision_id": f"origin-{option.option_id}",
                  "branch_id": option.option_id, "index": 0, "kind": "origin",
                  "option": option.to_dict(), "parent_revisions": [],
                  "requirement_refs": list(option.proposal.responds_to_refs),
                  "derivation_refs": [options.ref, option.proposal.ref],
                  "evidence_refs": list(option.proposal.evidence_refs), "expert_resolutions": [],
                  "tradeoff_rationale": option.proposal.rationale, "author_id": "architect-lead"}
             ]}
            for option in options.options
        ],
        "observations": [], "transitions": [], "ranked": False, "automatic_winner": False,
        "hard_usability_verdict": None, "canonical_write_authority": False,
        "candidate_assembly_authority": False,
    }


def _ref(name: str, *, project_id: str = PROJECT_ID) -> ProjectRecordRef:
    digest = hashlib.sha256(name.encode()).hexdigest()
    return ProjectRecordRef(project_id, f"runs/{RUN_ID}/records/{name}-{digest}.json", digest)


def _stage(parent: ProjectRecordRef | None, *, branch_id: str = "main", candidate_id: str = "cabinet-a") -> DesignStage:
    return DesignStage(
        parent_stage=parent, record_ref=_ref(f"state-{candidate_id}"),
        model_ref=_ref(f"model-{candidate_id}"), model_sha256="b" * 64,
        runner_ref=_ref(f"runner-{candidate_id}"), candidate_id=candidate_id,
        branch_id=branch_id, label="Stage 1", accepted_by="Architect",
    )


class DesignHistoryTests(unittest.TestCase):
    def test_accepting_candidates_continues_one_branch_and_preserves_base(self) -> None:
        s0, s1, s2 = _ref("s0"), _ref("s1"), _ref("s2")
        main = initialize_branch("main", s0)
        cabinet = _stage(s0)
        next_main = advance_branch(main, expected_head=s0, candidate_base=s0, stage_ref=s1, stage=cabinet)
        hood = _stage(s1, candidate_id="hood-b")
        final_main = advance_branch(next_main, expected_head=s1, candidate_base=s1, stage_ref=s2, stage=hood)
        self.assertEqual((main.head_stage, next_main.head_stage, final_main.head_stage), (s0, s1, s2))
        self.assertEqual(final_main.branch_id, "main")
        self.assertEqual(final_main.fork_stage, s0)
        self.assertIsNone(final_main.parent_branch)

    def test_sibling_candidate_cannot_advance_a_changed_head(self) -> None:
        s0, s1 = _ref("s0"), _ref("s1")
        main = advance_branch(initialize_branch("main", s0), expected_head=s0,
                              candidate_base=s0, stage_ref=s1, stage=_stage(s0))
        with self.assertRaisesRegex(DesignPortfolioError, "head changed"):
            advance_branch(main, expected_head=s0, candidate_base=s0,
                           stage_ref=_ref("sibling"), stage=_stage(s0, candidate_id="cabinet-b"))
        with self.assertRaisesRegex(DesignPortfolioError, "candidate base"):
            advance_branch(main, expected_head=s1, candidate_base=s0,
                           stage_ref=_ref("sibling"), stage=_stage(s1, candidate_id="cabinet-b"))

    def test_fork_from_old_stage_preserves_main_and_can_continue(self) -> None:
        s0, s1 = _ref("s0"), _ref("s1")
        main = advance_branch(initialize_branch("main", s0), expected_head=s0,
                              candidate_base=s0, stage_ref=s1, stage=_stage(s0))
        other = fork_branch(main, new_branch_id="alternative", stage_ref=s0)
        continued = advance_branch(other, expected_head=s0, candidate_base=s0, stage_ref=_ref("other-s1"),
                                   stage=_stage(s0, branch_id="alternative", candidate_id="cabinet-b"))
        self.assertEqual(main.head_stage, s1)
        self.assertEqual(other.head_stage, s0)
        self.assertEqual(continued.parent_branch, "main")
        self.assertEqual(continued.fork_stage, s0)
        self.assertNotEqual(continued.head_stage, main.head_stage)

    def test_stage_keeps_exact_model_and_runner_sources_on_round_trip(self) -> None:
        stage = _stage(_ref("s0"))
        self.assertEqual(DesignStage.from_dict(stage.to_dict()), stage)
        branch = initialize_branch("main", _ref("s0"))
        self.assertEqual(DesignBranch.from_dict(branch.to_dict()), branch)
        other_runner = replace(stage, runner_ref=_ref("runner-other"))
        self.assertNotEqual(other_runner.runner_ref, stage.runner_ref)

    def test_stage_cannot_mix_projects_or_lose_its_model_identity(self) -> None:
        stage = _stage(_ref("s0"))
        for field in ("parent_stage", "record_ref", "model_ref", "runner_ref"):
            with self.subTest(field=field), self.assertRaisesRegex(DesignPortfolioError, "another project"):
                replace(stage, **{field: _ref(field, project_id="other-project")})
        with self.assertRaises(ValueError):
            replace(stage, model_sha256="not-a-digest")
        with self.assertRaises(ValueError):
            replace(stage, accepted_by="")

    def test_advance_refuses_changed_parent_branch_or_project(self) -> None:
        s0, s1 = _ref("s0"), _ref("s1")
        main = initialize_branch("main", s0)
        wrong_stages = (
            replace(_stage(s0), parent_stage=_ref("other-parent")),
            replace(_stage(s0), branch_id="other-branch"),
        )
        for stage in wrong_stages:
            with self.subTest(stage=stage), self.assertRaises(DesignPortfolioError):
                advance_branch(main, expected_head=s0, candidate_base=s0, stage_ref=s1, stage=stage)
        with self.assertRaisesRegex(DesignPortfolioError, "another project"):
            advance_branch(main, expected_head=s0, candidate_base=s0,
                           stage_ref=_ref("s1", project_id="other-project"), stage=_stage(s0))
        with self.assertRaisesRegex(DesignPortfolioError, "new Stage"):
            advance_branch(main, expected_head=s0, candidate_base=s0, stage_ref=s0, stage=_stage(s0))

    def test_fork_refuses_self_and_other_project(self) -> None:
        main = initialize_branch("main", _ref("s0"))
        with self.assertRaisesRegex(DesignPortfolioError, "itself"):
            fork_branch(main, new_branch_id="main", stage_ref=main.head_stage)
        with self.assertRaisesRegex(DesignPortfolioError, "another project"):
            fork_branch(main, new_branch_id="alternative", stage_ref=_ref("s0", project_id="other-project"))


class RetainedPortfolioCompatibilityTests(unittest.TestCase):
    def test_saved_portfolio_preserves_its_record_and_digest(self) -> None:
        payload = _retained_portfolio_payload()
        retained = DesignOptionPortfolio.from_dict(payload)
        self.assertEqual(retained.to_dict(), payload)
        self.assertEqual(retained.portfolio_digest, "c7036e26bd5b99eb6e372fc10d002303f658f8606e14c532d72f621ac72903fc")

    def test_compiler_selection_handoff_keeps_its_retained_identity(self) -> None:
        from archflow.state.developed_design import SelectedSchematicInput
        handoff = SelectedBranchHandoff(
            portfolio_id="legacy", portfolio_digest="c" * 64,
            project_id=PROJECT_ID, run_id=RUN_ID, base=_run().base,
            branch_id="legacy-branch", revision=BranchRevisionRef("legacy-branch", "r0", "d" * 64),
            option=_option("legacy"), selection_transition_id="declared",
            selection_decision_ref="decision:declared",
        )
        selected = SelectedSchematicInput.from_handoff(handoff)
        self.assertEqual(SelectedSchematicInput.from_dict(selected.to_dict()), selected)
        self.assertEqual(selected.revision, handoff.revision)
        self.assertEqual(handoff.to_dict()["schema"], "SelectedSchematicBranch@1")


if __name__ == "__main__":
    unittest.main()
