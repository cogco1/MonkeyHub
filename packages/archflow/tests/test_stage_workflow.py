from __future__ import annotations

import unittest
from dataclasses import replace

from archflow.contracts.authority import DEFAULT_AUTHORITY_FIELDS, no_authority
from archflow.state.stage_workflow import DESIGN_PHASES, DesignPhase
from archflow.state.operational_state import DesignObligation, ObligationStatus
from archflow.state.state_record import CHECK_KINDS
from archflow.state.stage_workflow import (
    LOD_LEVELS,
    PHASE_LADDER,
    PhaseLadder,
    ProjectStage,
    ProjectStageWorkflow,
    StageExitBinding,
    StageExitStatus,
    StageRunEnvelope,
    StageWorkflowError,
    open_stage_run_envelope,
    require_measurable,
    require_stage_exit_binding,
    require_stage_run_envelope,
)


WORKFLOW_REF = "project://demo/runs/workflow-001/records/stage-workflow.json"
BASE_SHA = "a" * 64


def stage(
    index: int,
    *,
    stage_id: str | None = None,
    phase: DesignPhase = DesignPhase.DESIGN_DEVELOPMENT,
    close_obligation_id: str | None = None,
    lod: int | None = None,
) -> ProjectStage:
    return ProjectStage(
        stage_id=stage_id or f"stage-{index}",
        stage_index=index,
        phase=phase,
        required_roles=(f"role-{index}-a", f"role-{index}-b"),
        # Checks the spine can measure: a workflow may require nothing else,
        # and every envelope opened below goes through require_measurable.
        required_checks=("aperture_exists", "support_contact"),
        close_obligation_id=(
            close_obligation_id or f"close-stage-{index}"
        ),
        lod=lod,
    )


def workflow() -> ProjectStageWorkflow:
    return ProjectStageWorkflow(
        project_id="demo",
        workflow_id="villa-reconstruction",
        stages=(
            stage(0, phase=DesignPhase.SCHEMATIC_DESIGN),
            stage(1),
            stage(2, phase=DesignPhase.CANDIDATE_COORDINATION),
        ),
        basis_refs=("decision:stage-sequence",),
    )


def close_obligation(
    index: int,
    *,
    status: ObligationStatus = ObligationStatus.OPEN,
) -> DesignObligation:
    return DesignObligation(
        obligation_id=f"close-stage-{index}",
        statement=f"Close stage {index} only through its independent gate.",
        source_ref=f"workflow:villa-reconstruction/stage-{index}",
        status=status,
        subject_refs=(f"stage:stage-{index}",),
        validator_ref=f"validator:stage-{index}",
    )


def open_envelope(
    value: ProjectStageWorkflow,
    index: int,
    *,
    run_id: str = "workflow-run-001",
    base_version: int = 3,
    base_state_sha256: str = BASE_SHA,
    branch_id: str = "option-a",
    branch_epoch: int = 7,
    predecessor: StageRunEnvelope | None = None,
    predecessor_ref: str | None = None,
    predecessor_exit: StageExitBinding | None = None,
    predecessor_exit_ref: str | None = None,
) -> StageRunEnvelope:
    return open_stage_run_envelope(
        value,
        workflow_ref=WORKFLOW_REF,
        run_id=run_id,
        base_version=base_version,
        base_state_sha256=base_state_sha256,
        branch_id=branch_id,
        branch_epoch=branch_epoch,
        subject_ref=f"design-state:stage-{index}",
        state_digest=f"{index + 1:x}" * 64,
        stage_index=index,
        close_obligation=close_obligation(index),
        predecessor=predecessor,
        predecessor_ref=predecessor_ref,
        predecessor_exit=predecessor_exit,
        predecessor_exit_ref=predecessor_exit_ref,
    )


def exit_for(
    envelope: StageRunEnvelope,
    *,
    envelope_ref: str,
    closure_digest: str = "c" * 64,
) -> StageExitBinding:
    return StageExitBinding.bind(
        envelope,
        envelope_ref=envelope_ref,
        closure_ref=(
            f"project://demo/runs/{envelope.run_id}/records/"
            f"stage-{envelope.stage_index}-closure.json"
        ),
        closure_digest=closure_digest,
    )


def exit_ref_for(envelope: StageRunEnvelope) -> str:
    return (
        f"project://demo/runs/{envelope.run_id}/records/"
        f"stage-{envelope.stage_index}-exit-binding.json"
    )


class ProjectStageWorkflowTests(unittest.TestCase):
    def test_indices_must_be_exactly_ordered_zero_through_n(self) -> None:
        for stages in ((stage(0), stage(2)), (stage(1), stage(0))):
            with self.subTest(stages=stages), self.assertRaisesRegex(
                StageWorkflowError,
                "ordered contiguously from 0",
            ):
                ProjectStageWorkflow(
                    project_id="demo",
                    workflow_id="bad-order",
                    stages=stages,
                )

    def test_phase_may_repeat_but_cannot_regress(self) -> None:
        repeated = ProjectStageWorkflow(
            project_id="demo",
            workflow_id="repeated-phase",
            stages=(stage(0), stage(1), stage(2)),
        )
        self.assertEqual(len(repeated.stages), 3)

        with self.assertRaisesRegex(
            StageWorkflowError,
            "phases must be non-decreasing",
        ):
            ProjectStageWorkflow(
                project_id="demo",
                workflow_id="phase-regression",
                stages=(
                    stage(0, phase=DesignPhase.DESIGN_DEVELOPMENT),
                    stage(1, phase=DesignPhase.SCHEMATIC_DESIGN),
                ),
            )

    def test_requirements_are_deterministic_and_close_ids_unique(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "required_roles must be sorted and unique",
        ):
            ProjectStage(
                stage_id="stage-0",
                stage_index=0,
                phase=DesignPhase.SCHEMATIC_DESIGN,
                required_roles=("z", "a"),
                required_checks=(),
                close_obligation_id="close-stage-0",
            )

        with self.assertRaisesRegex(
            StageWorkflowError,
            "close_obligation_ids must be unique",
        ):
            ProjectStageWorkflow(
                project_id="demo",
                workflow_id="duplicate-close",
                stages=(
                    stage(0, close_obligation_id="same-close"),
                    stage(1, close_obligation_id="same-close"),
                ),
            )


class MeasurableRequirementTests(unittest.TestCase):
    """A workflow may require only checks the spine can measure (ADR-007 r3)."""

    def unmeasurable(self) -> ProjectStageWorkflow:
        return ProjectStageWorkflow(
            project_id="demo",
            workflow_id="villa-rotonda-as-built-stage-0-5-v1",
            stages=(
                replace(
                    stage(0),
                    required_checks=("frame-glazing-separation",),
                ),
            ),
        )

    def test_a_required_check_outside_the_checker_table_is_refused(
        self,
    ) -> None:
        with self.assertRaises(StageWorkflowError) as caught:
            require_measurable(self.unmeasurable())
        message = str(caught.exception)
        self.assertIn("'frame-glazing-separation'", message)
        for registered in CHECK_KINDS:
            self.assertIn(registered, message)

    def test_every_registered_check_kind_is_accepted(self) -> None:
        for kind in CHECK_KINDS:
            with self.subTest(kind=kind):
                accepted = ProjectStageWorkflow(
                    project_id="demo",
                    workflow_id="measurable",
                    stages=(replace(stage(0), required_checks=(kind,)),),
                )
                self.assertIs(require_measurable(accepted), accepted)

    def test_opening_a_stage_against_it_is_refused_too(self) -> None:
        with self.assertRaisesRegex(StageWorkflowError, "cannot measure"):
            open_envelope(self.unmeasurable(), 0)

    def test_a_retained_workflow_with_an_old_check_id_still_loads(self) -> None:
        """Reading is not re-validating (ADR-004).

        The harnesses and the villa's frozen v1 retained free check ids before
        the rule existed. ``from_dict`` is the path the runner's guard and the
        Studio read a retained workflow through, so it must still return them;
        the refusal belongs where a workflow is frozen or a stage is opened.
        """

        retained = {
            "schema": "ProjectStageWorkflow@1",
            "project_id": "demo",
            "workflow_id": "equivalence-harness",
            "stages": [
                {
                    "stage_id": "equivalence-check",
                    "stage_index": 0,
                    "phase": "design_development",
                    "required_roles": ["geometry-program"],
                    "required_checks": ["state-record-equivalence"],
                    "close_obligation_id": "close-equivalence-check",
                }
            ],
            "basis_refs": ["decision:state-record-equivalence-harness"],
            **no_authority(DEFAULT_AUTHORITY_FIELDS),
        }

        loaded = ProjectStageWorkflow.from_dict(retained)

        self.assertEqual(
            loaded.stages[0].required_checks,
            ("state-record-equivalence",),
        )
        self.assertEqual(loaded.to_dict(), retained)
        with self.assertRaises(StageWorkflowError):
            require_measurable(loaded)


class StageRunEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = workflow()
        self.stage0 = open_envelope(self.workflow, 0)
        self.stage0_ref = (
            "project://demo/runs/workflow-run-001/records/stage-0-envelope.json"
        )
        self.stage0_exit = exit_for(
            self.stage0,
            envelope_ref=self.stage0_ref,
        )
        self.stage0_exit_ref = exit_ref_for(self.stage0)

    def test_stage_zero_has_no_predecessor_and_round_trips(self) -> None:
        payload = self.stage0.to_dict()
        self.assertEqual(payload["schema"], "StageRunEnvelope@1")
        self.assertIsNone(payload["predecessor"])
        self.assertEqual(payload["base"]["version"], 3)
        self.assertEqual(payload["branch"], {"branch_id": "option-a", "epoch": 7})
        for field in DEFAULT_AUTHORITY_FIELDS:
            self.assertIs(payload[field], False)

        loaded = StageRunEnvelope.from_dict(payload)
        self.assertEqual(loaded, self.stage0)
        self.assertEqual(loaded.envelope_digest, self.stage0.envelope_digest)
        self.assertIs(
            require_stage_run_envelope(
                self.workflow,
                loaded,
                workflow_ref=WORKFLOW_REF,
            ),
            loaded,
        )

    def test_stage_zero_forbids_predecessor_and_completion(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "stage 0 cannot have predecessor completion",
        ):
            open_envelope(
                self.workflow,
                0,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

    def test_open_predecessor_without_satisfied_exit_cannot_start_next(self) -> None:
        self.assertIs(
            self.stage0.close_obligation.status,
            ObligationStatus.OPEN,
        )
        with self.assertRaisesRegex(
            StageWorkflowError,
            "SATISFIED exit",
        ):
            open_envelope(
                self.workflow,
                1,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
            )
        with self.assertRaisesRegex(
            StageWorkflowError,
            "SATISFIED exit",
        ):
            open_envelope(
                self.workflow,
                1,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
            )

        stage1 = open_envelope(
            self.workflow,
            1,
            predecessor=self.stage0,
            predecessor_ref=self.stage0_ref,
            predecessor_exit=self.stage0_exit,
            predecessor_exit_ref=self.stage0_exit_ref,
        )
        self.assertIs(
            stage1.predecessor.exit_binding.status,
            StageExitStatus.SATISFIED,
        )
        self.assertEqual(
            stage1.predecessor.envelope_digest,
            self.stage0.envelope_digest,
        )
        self.assertEqual(
            stage1.predecessor.exit_binding_ref,
            self.stage0_exit_ref,
        )
        self.assertEqual(StageRunEnvelope.from_dict(stage1.to_dict()), stage1)

    def test_exit_binding_ref_is_part_of_exact_predecessor_identity(self) -> None:
        stage1 = open_envelope(
            self.workflow,
            1,
            predecessor=self.stage0,
            predecessor_ref=self.stage0_ref,
            predecessor_exit=self.stage0_exit,
            predecessor_exit_ref=self.stage0_exit_ref,
        )
        with self.assertRaisesRegex(
            StageWorkflowError,
            "stale or not exact",
        ):
            require_stage_run_envelope(
                self.workflow,
                stage1,
                workflow_ref=WORKFLOW_REF,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=(
                    "project://demo/runs/workflow-run-001/records/"
                    "another-exit-binding.json"
                ),
            )

    def test_same_run_and_cross_run_handoffs_are_both_supported(self) -> None:
        same_run = open_envelope(
            self.workflow,
            1,
            predecessor=self.stage0,
            predecessor_ref=self.stage0_ref,
            predecessor_exit=self.stage0_exit,
            predecessor_exit_ref=self.stage0_exit_ref,
        )
        self.assertEqual(same_run.run_id, self.stage0.run_id)

        cross_run = open_envelope(
            self.workflow,
            1,
            run_id="workflow-run-002",
            branch_epoch=1,
            predecessor=self.stage0,
            predecessor_ref=self.stage0_ref,
            predecessor_exit=self.stage0_exit,
            predecessor_exit_ref=self.stage0_exit_ref,
        )
        self.assertNotEqual(cross_run.run_id, self.stage0.run_id)
        self.assertEqual(cross_run.predecessor.run_id, self.stage0.run_id)
        self.assertEqual(cross_run.predecessor.branch_epoch, 7)

    def test_cross_base_or_semantic_branch_fails(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "exact canonical base",
        ):
            open_envelope(
                self.workflow,
                1,
                run_id="workflow-run-002",
                base_state_sha256="b" * 64,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

        with self.assertRaisesRegex(
            StageWorkflowError,
            "semantic branch",
        ):
            open_envelope(
                self.workflow,
                1,
                run_id="workflow-run-002",
                branch_id="option-b",
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

    def test_same_run_cannot_drift_branch_epoch(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "same-run predecessor.*epoch",
        ):
            open_envelope(
                self.workflow,
                1,
                branch_epoch=8,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

    def test_stale_closure_digest_fails(self) -> None:
        stage1 = open_envelope(
            self.workflow,
            1,
            predecessor=self.stage0,
            predecessor_ref=self.stage0_ref,
            predecessor_exit=self.stage0_exit,
            predecessor_exit_ref=self.stage0_exit_ref,
        )
        payload = stage1.to_dict()
        payload["predecessor"]["exit_binding"]["closure_digest"] = "d" * 64
        stale = StageRunEnvelope.from_dict(payload)
        with self.assertRaisesRegex(
            StageWorkflowError,
            "stale or not exact",
        ):
            require_stage_run_envelope(
                self.workflow,
                stale,
                workflow_ref=WORKFLOW_REF,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

    def test_exit_cannot_drift_from_predecessor_identity(self) -> None:
        for exit_binding in (
            replace(self.stage0_exit, run_id="workflow-run-009"),
            replace(self.stage0_exit, branch_id="option-b"),
        ):
            with self.subTest(exit_binding=exit_binding), self.assertRaisesRegex(
                StageWorkflowError,
                "stale or cross-scoped|does not match the exact envelope",
            ):
                open_envelope(
                    self.workflow,
                    1,
                    run_id="workflow-run-002",
                    branch_epoch=1,
                    predecessor=self.stage0,
                    predecessor_ref=self.stage0_ref,
                    predecessor_exit=exit_binding,
                    predecessor_exit_ref=self.stage0_exit_ref,
                )

        wrong_stage = replace(self.stage0_exit, stage_id="stage-9")
        with self.assertRaisesRegex(
            StageWorkflowError,
            "stale or cross-scoped|does not match the exact envelope",
        ):
            open_envelope(
                self.workflow,
                1,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=wrong_stage,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

    def test_same_workflow_but_wrong_predecessor_stage_fails(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "immediately previous stage",
        ):
            open_envelope(
                self.workflow,
                2,
                predecessor=self.stage0,
                predecessor_ref=self.stage0_ref,
                predecessor_exit=self.stage0_exit,
                predecessor_exit_ref=self.stage0_exit_ref,
            )

    def test_close_obligation_must_be_open_and_exactly_named(self) -> None:
        with self.assertRaisesRegex(StageWorkflowError, "must remain OPEN"):
            open_stage_run_envelope(
                self.workflow,
                workflow_ref=WORKFLOW_REF,
                run_id="workflow-run-001",
                base_version=3,
                base_state_sha256=BASE_SHA,
                branch_id="option-a",
                branch_epoch=7,
                subject_ref="design-state:stage-0",
                state_digest="1" * 64,
                stage_index=0,
                close_obligation=close_obligation(
                    0,
                    status=ObligationStatus.SATISFIED,
                ),
            )

        wrong_open = replace(
            close_obligation(0),
            obligation_id="close-another-stage",
        )
        with self.assertRaisesRegex(
            StageWorkflowError,
            "does not match the workflow",
        ):
            open_stage_run_envelope(
                self.workflow,
                workflow_ref=WORKFLOW_REF,
                run_id="workflow-run-001",
                base_version=3,
                base_state_sha256=BASE_SHA,
                branch_id="option-a",
                branch_epoch=7,
                subject_ref="design-state:stage-0",
                state_digest="1" * 64,
                stage_index=0,
                close_obligation=wrong_open,
            )

    def test_exact_workflow_ref_digest_and_stage_contract_are_required(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "exact workflow ref and digest",
        ):
            require_stage_run_envelope(
                self.workflow,
                self.stage0,
                workflow_ref="project://demo/records/other-workflow.json",
            )

        payload = self.stage0.to_dict()
        payload["required_checks"] = ["check-0-a"]
        drifted = StageRunEnvelope.from_dict(payload)
        with self.assertRaisesRegex(
            StageWorkflowError,
            "does not match the workflow",
        ):
            require_stage_run_envelope(
                self.workflow,
                drifted,
                workflow_ref=WORKFLOW_REF,
            )

    def test_exit_round_trip_is_exact_and_authority_free(self) -> None:
        payload = self.stage0_exit.to_dict()
        self.assertEqual(payload["schema"], "StageExitBinding@1")
        self.assertEqual(payload["status"], "SATISFIED")
        self.assertEqual(payload["base"]["state_sha256"], BASE_SHA)
        for field in DEFAULT_AUTHORITY_FIELDS:
            self.assertIs(payload[field], False)
        loaded = StageExitBinding.from_dict(payload)
        self.assertEqual(loaded, self.stage0_exit)
        self.assertEqual(loaded.exit_digest, self.stage0_exit.exit_digest)
        self.assertIs(
            require_stage_exit_binding(
                self.stage0,
                loaded,
                envelope_ref=self.stage0_ref,
            ),
            loaded,
        )


class StageLadderTests(unittest.TestCase):
    """The ladder is industry vocabulary; lod is optional and only climbs."""

    def test_every_phase_is_placed_on_all_three_ladders(self) -> None:
        self.assertEqual(tuple(PHASE_LADDER), DESIGN_PHASES)
        for phase, entry in PHASE_LADDER.items():
            with self.subTest(phase=phase):
                self.assertIsInstance(entry, PhaseLadder)
                self.assertTrue(entry.riba_stage and entry.aia and entry.cn)

    def test_a_phase_range_is_two_declared_levels_that_do_not_regress(
        self,
    ) -> None:
        for phase, entry in PHASE_LADDER.items():
            if entry.lod_range is None:
                continue
            low, high = entry.lod_range
            with self.subTest(phase=phase):
                self.assertIn(low, LOD_LEVELS)
                self.assertIn(high, LOD_LEVELS)
                self.assertLessEqual(low, high)

    def test_the_ranges_climb_with_the_phases(self) -> None:
        placed = [
            PHASE_LADDER[phase].lod_range
            for phase in DESIGN_PHASES
            if PHASE_LADDER[phase].lod_range is not None
        ]
        self.assertEqual(placed, sorted(placed))

    def test_a_workflow_without_lod_serialises_exactly_as_before(self) -> None:
        self.assertEqual(
            workflow().to_dict(),
            {
                "schema": "ProjectStageWorkflow@1",
                "project_id": "demo",
                "workflow_id": "villa-reconstruction",
                "stages": [
                    {
                        "stage_id": "stage-0",
                        "stage_index": 0,
                        "phase": "schematic_design",
                        "required_roles": ["role-0-a", "role-0-b"],
                        "required_checks": ["aperture_exists", "support_contact"],
                        "close_obligation_id": "close-stage-0",
                    },
                    {
                        "stage_id": "stage-1",
                        "stage_index": 1,
                        "phase": "design_development",
                        "required_roles": ["role-1-a", "role-1-b"],
                        "required_checks": ["aperture_exists", "support_contact"],
                        "close_obligation_id": "close-stage-1",
                    },
                    {
                        "stage_id": "stage-2",
                        "stage_index": 2,
                        "phase": "candidate_coordination",
                        "required_roles": ["role-2-a", "role-2-b"],
                        "required_checks": ["aperture_exists", "support_contact"],
                        "close_obligation_id": "close-stage-2",
                    },
                ],
                "basis_refs": ["decision:stage-sequence"],
                **no_authority(DEFAULT_AUTHORITY_FIELDS),
            },
        )

    def test_a_stated_lod_round_trips_and_an_absent_one_writes_nothing(
        self,
    ) -> None:
        stated = stage(0, phase=DesignPhase.SCHEMATIC_DESIGN, lod=200)
        self.assertEqual(stated.to_dict()["lod"], 200)
        self.assertEqual(ProjectStage.from_dict(stated.to_dict()), stated)

        silent = stage(0, phase=DesignPhase.SCHEMATIC_DESIGN)
        self.assertNotIn("lod", silent.to_dict())
        self.assertIsNone(ProjectStage.from_dict(silent.to_dict()).lod)

    def test_a_lod_bearing_workflow_round_trips(self) -> None:
        carried = ProjectStageWorkflow(
            project_id="demo",
            workflow_id="villa-lod",
            stages=(
                stage(0, phase=DesignPhase.SCHEMATIC_DESIGN, lod=100),
                stage(1, phase=DesignPhase.DESIGN_DEVELOPMENT, lod=300),
                stage(2, phase=DesignPhase.CANDIDATE_COORDINATION, lod=350),
            ),
        )
        self.assertEqual(
            ProjectStageWorkflow.from_dict(carried.to_dict()), carried
        )

    def test_only_a_lod_bearing_workflow_gets_a_new_digest(self) -> None:
        without = workflow()
        with_lod = ProjectStageWorkflow(
            project_id=without.project_id,
            workflow_id=without.workflow_id,
            stages=(
                replace(without.stages[0], lod=200),
                *without.stages[1:],
            ),
            basis_refs=without.basis_refs,
        )
        self.assertNotEqual(without.workflow_digest, with_lod.workflow_digest)
        self.assertEqual(
            without.workflow_digest,
            ProjectStageWorkflow.from_dict(without.to_dict()).workflow_digest,
        )

    def test_lod_must_be_one_of_the_declared_levels(self) -> None:
        with self.assertRaisesRegex(StageWorkflowError, "is not one of"):
            stage(0, phase=DesignPhase.SCHEMATIC_DESIGN, lod=150)
        with self.assertRaisesRegex(
            StageWorkflowError, "level of development"
        ):
            stage(0, phase=DesignPhase.SCHEMATIC_DESIGN, lod="200")

    def test_lod_must_lie_inside_the_range_its_phase_admits(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "outside phase 'schematic_design' range 100-200",
        ):
            stage(0, phase=DesignPhase.SCHEMATIC_DESIGN, lod=350)

    def test_a_research_phase_refuses_a_lod_and_says_why(self) -> None:
        for phase in (
            DesignPhase.RESEARCH_BRIEF,
            DesignPhase.PROGRAMMING,
            DesignPhase.SITE_RESOURCE_COORDINATION,
        ):
            with self.subTest(phase=phase), self.assertRaisesRegex(
                StageWorkflowError,
                "resolves no model and admits no lod",
            ):
                stage(0, phase=phase, lod=100)

    def test_lod_may_repeat_but_cannot_regress_across_stages(self) -> None:
        repeated = ProjectStageWorkflow(
            project_id="demo",
            workflow_id="repeated-lod",
            stages=(
                stage(0, phase=DesignPhase.DESIGN_DEVELOPMENT, lod=200),
                stage(1, phase=DesignPhase.DESIGN_DEVELOPMENT, lod=200),
            ),
        )
        self.assertEqual(len(repeated.stages), 2)

        with self.assertRaisesRegex(
            StageWorkflowError,
            "lod must be non-decreasing",
        ):
            ProjectStageWorkflow(
                project_id="demo",
                workflow_id="lod-regression",
                stages=(
                    stage(0, phase=DesignPhase.CANDIDATE_COORDINATION, lod=350),
                    stage(1, phase=DesignPhase.EXECUTION_READY, lod=400),
                    stage(2, phase=DesignPhase.EXECUTION_READY, lod=350),
                ),
            )

    def test_a_stage_without_lod_does_not_reset_the_ladder(self) -> None:
        with self.assertRaisesRegex(
            StageWorkflowError,
            "lod must be non-decreasing",
        ):
            ProjectStageWorkflow(
                project_id="demo",
                workflow_id="silent-then-lower",
                stages=(
                    stage(0, phase=DesignPhase.CANDIDATE_COORDINATION, lod=350),
                    stage(1, phase=DesignPhase.CANDIDATE_COORDINATION),
                    stage(2, phase=DesignPhase.CANDIDATE_COORDINATION, lod=300),
                ),
            )


if __name__ == "__main__":
    unittest.main()
