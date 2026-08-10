from __future__ import annotations

from dataclasses import replace
import unittest

from archflow.project.refs import ProjectVersionRef
from archflow.realization import realize_geometry
from archflow.runtime.candidate_assembly import CandidatePolicyKind
from archflow.runtime.geometry_compiler import compile_geometry_program
from archflow.runtime.staged_build import (
    BuildRunStatus,
    MaterialAccountReceipt,
    MaterialStatus,
    StageScope,
    StagedBuildCheckpoint,
    StagedBuildError,
    StagedBuildPlan,
    advance_build,
    cancel_build,
    compile_material_account,
    compile_staged_build_plan,
    create_build_checkpoint,
    pause_build,
    resume_build,
    set_build_speed,
    start_build,
)
from archflow.state.build_policy import (
    BuildAssumption,
    BuildPolicy,
    BuildStagingMode,
    PolicyProvenance,
    ResourceAvailability,
    ResourceDemand,
    ResourcePolicyMode,
    StagingAssumption,
)
from archflow.state.operational_state import FactEpistemicStatus
from tests.test_candidate_assembly import _assembly
from tests.test_design_development import EVIDENCE
from tests.test_sandbox_realization import COMMITMENT, compiled_room


def _candidate_for_base(base: ProjectVersionRef | None = None):
    candidate = _assembly()
    if base is None or base == candidate.design_state.base:
        return candidate
    state = replace(
        candidate.design_state,
        selected_schematic=replace(
            candidate.design_state.selected_schematic,
            base=base,
        ),
    )
    return replace(
        candidate,
        design_state=state,
        plan=replace(
            candidate.plan,
            base=base,
            design_state_digest=state.state_digest,
        ),
        submission=replace(candidate.submission, base=base),
    )


def _policy(
    *,
    minimum_available: float | None = 100,
    maximum_available: float | None = 120,
    minimum_required: float = 70,
    maximum_required: float = 90,
    unbounded: bool = False,
    base: ProjectVersionRef | None = None,
) -> BuildPolicy:
    state = _candidate_for_base(base).design_state
    compiler_id = "compiler.material-test"
    base_digest = state.base.require_digest()
    assumption = BuildAssumption(
        assumption_id="material-estimate",
        statement="Material demand remains a bounded upstream estimate.",
        authority_id="authority.test",
        source_refs=(EVIDENCE,),
        compiler_id=compiler_id,
        base_state_sha256=base_digest,
    )
    policy_provenance = PolicyProvenance(
        authority_id="authority.test",
        source_refs=(EVIDENCE,),
        assumption_refs=(),
        compiler_id=compiler_id,
        base_state_sha256=base_digest,
    )
    demand_provenance = replace(
        policy_provenance,
        assumption_refs=(assumption.ref,),
    )
    availability = ()
    if minimum_available is not None or maximum_available is not None:
        availability = (
            ResourceAvailability(
                resource_ref="resource:building-unit",
                minimum_available=minimum_available,
                maximum_available=maximum_available,
                unit="units",
                epistemic_status=FactEpistemicStatus.OBSERVED,
                provenance=policy_provenance,
            ),
        )
    return BuildPolicy(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        brief_digest="1" * 64,
        program_digest="2" * 64,
        site_context_digest="3" * 64,
        compiler_id=compiler_id,
        compiler_version="1.0",
        resource_mode=(
            ResourcePolicyMode.CREATIVE if unbounded else ResourcePolicyMode.SURVIVAL
        ),
        staging_mode=BuildStagingMode.SINGLE_PASS,
        disposable_sandbox=unbounded,
        unbounded_resources=unbounded,
        policy_provenance=policy_provenance,
        assumptions=(assumption,),
        availability=availability,
        demands=(
            ResourceDemand(
                demand_id="building-material",
                resource_ref="resource:building-unit",
                minimum_required=minimum_required,
                maximum_required=maximum_required,
                unit="units",
                provenance=demand_provenance,
            ),
        ),
        protected_rules=(),
        budget_limits=(),
        staging_assumptions=(),
        constraints=(),
        obligations=(),
        evidence_refs=(EVIDENCE,),
    )


def _scope() -> tuple[StageScope, ...]:
    return (
        StageScope(
            stage_id="main-stage",
            phase_id="primary-build",
            layer_index=0,
            predecessor_ids=(),
            component_ids=("building",),
            demand_ids=("building-material",),
            evidence_refs=(EVIDENCE,),
        ),
    )


def _bound_candidate(policy: BuildPolicy):
    candidate = _candidate_for_base(policy.base)
    return replace(
        candidate,
        policies=tuple(
            replace(item, policy_digest=policy.policy_digest)
            if item.kind is CandidatePolicyKind.BUILD
            else item
            for item in candidate.policies
        ),
    )


def staged_fixture(*, base: ProjectVersionRef | None = None):
    policy = _policy(base=base)
    scopes = _scope()
    account = compile_material_account(policy, scopes)
    candidate = _bound_candidate(policy)
    original_state, original_program, payloads = compiled_room()
    if base is None:
        state = original_state
        program = original_program
    else:
        state = candidate.design_state
        compiled = compile_geometry_program(
            state,
            replace(
                original_program.proposal,
                base=base,
                design_state_digest=state.state_digest,
            ),
            active_commitment_refs=(COMMITMENT,),
            available_asset_digests={
                item.asset_id: item.payload_digest for item in payloads
            },
        )
        if compiled.program is None:
            raise AssertionError(compiled.receipt.issues)
        program = compiled.program
    if state.state_digest != candidate.design_state.state_digest:
        raise AssertionError("test fixtures stopped sharing one design state")
    realized = realize_geometry(
        program,
        workspace_id="saved-build-workspace",
        asset_payloads=payloads,
    )
    if realized.scene is None:
        raise AssertionError(realized.receipt.issues)
    plan = compile_staged_build_plan(
        candidate=candidate,
        build_policy=policy,
        geometry_program=program,
        scene=realized.scene,
        material_account=account,
        scopes=scopes,
    )
    return candidate, policy, program, realized, account, plan


class MaterialAccountingTests(unittest.TestCase):
    def test_available_shortage_uncertain_unknown_and_unbounded_stay_distinct(self) -> None:
        cases = (
            (_policy(), MaterialStatus.AVAILABLE, (0.0, 0.0)),
            (
                _policy(
                    minimum_available=30,
                    maximum_available=50,
                    minimum_required=80,
                    maximum_required=100,
                ),
                MaterialStatus.SHORTAGE,
                (30.0, 70.0),
            ),
            (
                _policy(
                    minimum_available=80,
                    maximum_available=90,
                    minimum_required=70,
                    maximum_required=100,
                ),
                MaterialStatus.UNCERTAIN,
                (0.0, 20.0),
            ),
            (
                _policy(minimum_available=None, maximum_available=None),
                MaterialStatus.UNKNOWN,
                (None, None),
            ),
            (
                _policy(unbounded=True),
                MaterialStatus.UNBOUNDED,
                (None, None),
            ),
        )
        for policy, expected_status, expected_shortage in cases:
            with self.subTest(status=expected_status):
                receipt = compile_material_account(policy, _scope())
                total = receipt.totals[0]
                self.assertIs(total.status, expected_status)
                self.assertEqual(
                    (total.minimum_shortage, total.maximum_shortage),
                    expected_shortage,
                )
                self.assertEqual(receipt.needs_for_stage("main-stage")[0].status, expected_status)
                self.assertEqual(MaterialAccountReceipt.from_dict(receipt.to_dict()), receipt)

    def test_every_upstream_demand_must_be_assigned_once(self) -> None:
        scope = replace(_scope()[0], demand_ids=())
        with self.assertRaisesRegex(StagedBuildError, "every policy demand"):
            compile_material_account(_policy(), (scope,))

    def test_current_stage_availability_accounts_for_prior_stage_range(self) -> None:
        base_policy = _policy()
        first_demand = base_policy.demands[0]
        second_demand = replace(
            first_demand,
            demand_id="finish-material",
            minimum_required=20,
            maximum_required=40,
        )
        stage_provenance = first_demand.provenance
        policy = replace(
            base_policy,
            staging_mode=BuildStagingMode.STAGED,
            demands=(first_demand, second_demand),
            staging_assumptions=(
                StagingAssumption(
                    stage_id="shell-stage",
                    statement="Build the shell first.",
                    predecessor_ids=(),
                    provenance=stage_provenance,
                ),
                StagingAssumption(
                    stage_id="finish-stage",
                    statement="Install finishes after the shell.",
                    predecessor_ids=("shell-stage",),
                    provenance=stage_provenance,
                ),
            ),
        )
        scopes = (
            replace(
                _scope()[0],
                stage_id="shell-stage",
                phase_id="shell",
            ),
            replace(
                _scope()[0],
                stage_id="finish-stage",
                phase_id="finish",
                layer_index=1,
                predecessor_ids=("shell-stage",),
                demand_ids=("finish-material",),
            ),
        )
        receipt = compile_material_account(policy, scopes)
        finish = receipt.needs_for_stage("finish-stage")[0]
        self.assertEqual(
            (finish.minimum_available, finish.maximum_available),
            (10.0, 50.0),
        )
        self.assertEqual(
            (finish.minimum_shortage, finish.maximum_shortage),
            (0.0, 30.0),
        )
        self.assertIs(finish.status, MaterialStatus.UNCERTAIN)


class StagedBuildControlTests(unittest.TestCase):
    def test_plan_binds_semantics_geometry_material_and_exact_progress(self) -> None:
        candidate, policy, program, realized, account, plan = staged_fixture()
        assert realized.scene is not None
        self.assertEqual(plan.candidate_assembly_digest, candidate.assembly_digest)
        self.assertEqual(plan.build_policy_digest, policy.policy_digest)
        self.assertEqual(plan.geometry_program_digest, program.program_digest)
        self.assertEqual(plan.scene_digest, realized.scene.scene_digest)
        self.assertEqual(plan.material_account_digest, account.account_digest)
        self.assertEqual(StagedBuildPlan.from_dict(plan.to_dict()), plan)

        ready = create_build_checkpoint(plan, speed_units_per_tick=1)
        running = start_build(
            plan,
            ready,
            expected_checkpoint_digest=ready.checkpoint_digest,
        )
        faster = set_build_speed(
            plan,
            running,
            expected_checkpoint_digest=running.checkpoint_digest,
            speed_units_per_tick=2,
        )
        advanced = advance_build(
            plan,
            faster,
            expected_checkpoint_digest=faster.checkpoint_digest,
            ticks=2,
        )
        self.assertEqual(advanced.total_completed_units, 4)
        self.assertEqual(advanced.current_phase_id, "primary-build")
        self.assertEqual(advanced.current_layer_index, 0)
        paused = pause_build(
            plan,
            advanced,
            expected_checkpoint_digest=advanced.checkpoint_digest,
        )
        self.assertEqual(paused.total_completed_units, advanced.total_completed_units)
        self.assertEqual(paused.plan_digest, plan.plan_digest)
        reloaded = StagedBuildCheckpoint.from_dict(paused.to_dict())
        resumed = resume_build(
            plan,
            reloaded,
            expected_checkpoint_digest=reloaded.checkpoint_digest,
        )
        fastest = set_build_speed(
            plan,
            resumed,
            expected_checkpoint_digest=resumed.checkpoint_digest,
            speed_units_per_tick=plan.total_work_units,
        )
        completed = advance_build(
            plan,
            fastest,
            expected_checkpoint_digest=fastest.checkpoint_digest,
        )
        self.assertIs(completed.status, BuildRunStatus.COMPLETED)
        self.assertEqual(completed.total_completed_units, plan.total_work_units)

    def test_stale_control_and_cancel_do_not_retarget_plan(self) -> None:
        *_, plan = staged_fixture()
        ready = create_build_checkpoint(plan)
        running = start_build(
            plan,
            ready,
            expected_checkpoint_digest=ready.checkpoint_digest,
        )
        with self.assertRaisesRegex(StagedBuildError, "stale"):
            pause_build(
                plan,
                running,
                expected_checkpoint_digest=ready.checkpoint_digest,
            )
        cancelled = cancel_build(
            plan,
            running,
            expected_checkpoint_digest=running.checkpoint_digest,
        )
        self.assertIs(cancelled.status, BuildRunStatus.CANCELLED)
        self.assertEqual(cancelled.plan_digest, plan.plan_digest)
        self.assertEqual(cancelled.total_completed_units, 0)


if __name__ == "__main__":
    unittest.main()
