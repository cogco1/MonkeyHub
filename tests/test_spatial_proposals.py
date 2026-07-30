from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.adapters.site_observation import (
    SiteObservationAuthorization,
    authorize_site_observation,
)
from archflow.capabilities.spatial import (
    SpatialCompilationError,
    compile_spatial_options,
)
from archflow.project import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.runtime.brief_compiler import (
    BriefObservation,
    compile_design_brief,
)
from archflow.runtime.program_compiler import (
    ProgramAssumptionProposal,
    ProgramNodeProposal,
    ProgramProposalBundle,
    ProgramRangeProposal,
    ProgramRelationshipProposal,
    compile_design_program,
)
from archflow.runtime.resource_compiler import (
    BuildPolicyProposal,
    ConstructabilityConstraintProposal,
    compile_build_policy,
)
from archflow.runtime.site_compiler import compile_site_context
from archflow.state import (
    BriefClaimKind,
    BriefSlot,
    BuildStagingMode,
    ConstraintResponseStatus,
    ConstructabilityTopic,
    DeliverableRole,
    DesignMaturityState,
    DesignPhase,
    FactEpistemicStatus,
    MassingVolume,
    OperationalMarkovState,
    PhaseDeliverable,
    PhaseGateRequest,
    ProgramMetricKind,
    ProgramNodeKind,
    ProgramRelationshipKind,
    ProgramRelationshipStrength,
    PolicyConstraintStrength,
    ResourcePolicyMode,
    SchematicOptionSet,
    SiteBounds,
    SpatialConnection,
    SpatialConstraintResponse,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialProposalError,
    SpatialZone,
    evaluate_forward_phase_gate,
)


_SITE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "site"
    / "authorized_superflat.json"
)


def _request_ref(project_id: str) -> str:
    return f"project://{project_id}/input/raw-request.json"


def _base(project_id: str, digest_char: str) -> ProjectVersionRef:
    return ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256=digest_char * 64,
    )


def _brief(project_id: str, run_id: str, digest_char: str):
    request_ref = _request_ref(project_id)
    observations = tuple(
        BriefObservation(
            observation_id=observation_id,
            slot=slot,
            kind=BriefClaimKind.USER_FACT,
            key=key,
            value=value,
            epistemic_status=FactEpistemicStatus.DECLARED,
            authority_id="authority.user",
            source_refs=(request_ref,),
            resolves_slot=True,
        )
        for observation_id, slot, key, value in (
            ("use", BriefSlot.USE, "use", f"{project_id} use"),
            (
                "size",
                BriefSlot.SIZE,
                "size",
                f"{project_id} scale evidence",
            ),
            (
                "occupancy",
                BriefSlot.OCCUPANCY,
                "occupancy",
                f"{project_id} occupancy evidence",
            ),
            (
                "program",
                BriefSlot.SPACE_PROGRAM,
                "program",
                f"{project_id} function evidence",
            ),
        )
    )
    return compile_design_brief(
        project_id=project_id,
        run_id=run_id,
        base=_base(project_id, digest_char),
        raw_request_ref=request_ref,
        observations=observations,
    ).brief


def _program(brief, *, function_count: int):
    source_ref = brief.raw_request_ref
    assumption_id = "evidence-bound-program"
    nodes = tuple(
        ProgramNodeProposal(
            node_id=f"function-{index}",
            kind=ProgramNodeKind.FUNCTION,
            label=f"{brief.project_id} function {index}",
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for index in range(1, function_count + 1)
    )
    range_specs = (
        (ProgramMetricKind.CAPACITY, 4.0, 40.0, "people"),
        (ProgramMetricKind.NET_AREA, 8.0, 30.0, "square_cells"),
        (ProgramMetricKind.GROSS_ALLOWANCE, 0.1, 0.4, "ratio"),
        (ProgramMetricKind.FOOTPRINT, 8.0, 30.0, "square_cells"),
        (
            ProgramMetricKind.TOTAL_FLOOR_AREA,
            8.0,
            60.0,
            "square_cells",
        ),
    )
    ranges = tuple(
        ProgramRangeProposal(
            range_id=f"range-{metric.value}",
            metric=metric,
            applies_to_node_id=nodes[0].node_id,
            minimum=minimum,
            maximum=maximum,
            unit=unit,
            scenario_id=None,
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for metric, minimum, maximum, unit in range_specs
    )
    relationships = tuple(
        ProgramRelationshipProposal(
            relationship_id=f"required-link-{index}",
            kind=ProgramRelationshipKind.ADJACENCY,
            source_node_id=nodes[index].node_id,
            target_node_id=nodes[index + 1].node_id,
            strength=ProgramRelationshipStrength.REQUIRED,
            directed=False,
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for index in range(function_count - 1)
    )
    return compile_design_program(
        brief=brief,
        proposals=ProgramProposalBundle(
            assumptions=(
                ProgramAssumptionProposal(
                    assumption_id=assumption_id,
                    statement=(
                        "The test program remains an evidence-bound "
                        "project hypothesis."
                    ),
                    source_refs=(source_ref,),
                ),
            ),
            nodes=nodes,
            ranges=ranges,
            relationships=relationships,
        ),
    ).program


def _site(brief):
    template = _SITE_FIXTURE.read_text(encoding="utf-8")
    payload = json.loads(
        template.replace("site-flat", brief.project_id)
        .replace("site-001", brief.run_id)
        .replace("a" * 64, brief.base.require_digest())
        .replace(
            "fixture-flat-world",
            f"fixture-{brief.project_id}-world",
        )
    )
    authorization = SiteObservationAuthorization(
        project_id=brief.project_id,
        run_id=brief.run_id,
        base=brief.base,
        world_id=f"fixture-{brief.project_id}-world",
        dimension_id="minecraft:overworld",
        authorized_envelope=SiteBounds(
            minimum=(0, 60, 0),
            maximum=(31, 90, 31),
        ),
        anchor=(16, 65, 16),
        authority_id="authority.user",
        authorization_ref=(
            f"project://{brief.project_id}/input/"
            "site-authorization.json"
        ),
    )
    observation = authorize_site_observation(
        payload,
        authorization=authorization,
    )
    return compile_site_context(
        brief=brief,
        observation=observation,
    ).context


def _inputs(
    project_id: str = "spatial-alpha",
    *,
    digest_char: str = "a",
    function_count: int = 3,
    hard_constraint: bool = False,
):
    run_id = "spatial-run"
    brief = _brief(project_id, run_id, digest_char)
    program = _program(brief, function_count=function_count)
    site = _site(brief)
    policy = compile_build_policy(
        brief=brief,
        program=program,
        site_context=site,
        proposal=BuildPolicyProposal(
            resource_mode=ResourcePolicyMode.CREATIVE,
            staging_mode=BuildStagingMode.SINGLE_PASS,
            disposable_sandbox=True,
            unbounded_resources=True,
            authority_id="authority.user",
            source_refs=(brief.raw_request_ref,),
            constraints=(
                (
                    ConstructabilityConstraintProposal(
                        constraint_id="support-boundary",
                        topic=ConstructabilityTopic.SUPPORT,
                        strength=PolicyConstraintStrength.HARD,
                        statement=(
                            "The schematic option must identify the current "
                            "support risk without resolving structure."
                        ),
                        subject_refs=(
                            "program-node:function-1",
                        ),
                        authority_id="authority.user",
                        source_refs=(brief.raw_request_ref,),
                    ),
                )
                if hard_constraint
                else ()
            ),
        ),
    ).policy
    branch = BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=run_id,
            base=brief.base,
        ),
        branch_id="schematic-main",
        epoch=0,
    )
    state = OperationalMarkovState(
        branch=branch,
        compiler_version="test.spatial-state",
        phase=DesignPhase.SITE_RESOURCE_COORDINATION.value,
        evidence_refs=tuple(
            sorted(
                {
                    *program.evidence_refs,
                    *site.evidence_refs,
                    *policy.evidence_refs,
                }
            )
        ),
    )
    deliverables = (
        PhaseDeliverable(
            deliverable_id="site-context",
            role=DeliverableRole.SITE_CONTEXT,
            produced_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            branch=branch,
            base_state_digest=state.state_digest,
            artifact_ref=f"site-context:{site.context_digest}",
            evidence_refs=site.evidence_refs,
        ),
        PhaseDeliverable(
            deliverable_id="build-policy",
            role=DeliverableRole.BUILD_POLICY,
            produced_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            branch=branch,
            base_state_digest=state.state_digest,
            artifact_ref=f"build-policy:{policy.policy_digest}",
            evidence_refs=policy.evidence_refs,
        ),
    )
    maturity = DesignMaturityState.from_operational_state(
        state,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id="enter-schematic",
            branch=branch,
            base_state_digest=state.state_digest,
            from_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            to_phase=DesignPhase.SCHEMATIC_DESIGN,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    return brief, program, site, policy, state, maturity, gate


def _proposal(
    *,
    option_id: str,
    program,
    brief,
    two_levels: bool,
) -> SpatialOptionProposal:
    functions = tuple(
        item for item in program.nodes if item.kind is ProgramNodeKind.FUNCTION
    )
    relationships = tuple(program.relationships)
    evidence_refs = (brief.raw_request_ref,)
    if two_levels:
        footprint = tuple(
            (x, z) for x in range(4) for z in range(3)
        )
        levels = (
            SpatialLevel("lower", 64, 4, evidence_refs),
            SpatialLevel("upper", 68, 4, evidence_refs),
        )
        volumes = (
            MassingVolume(
                "west",
                SiteBounds((0, 64, 0), (1, 67, 2)),
                ("lower",),
                evidence_refs,
            ),
            MassingVolume(
                "east",
                SiteBounds((2, 68, 0), (3, 71, 2)),
                ("upper",),
                evidence_refs,
            ),
        )
        zones = tuple(
            SpatialZone(
                zone_id=f"zone-{index}",
                program_node_refs=(function.ref,),
                level_ids=("lower",) if index % 2 else ("upper",),
                volume_ids=("west",) if index % 2 else ("east",),
                source_refs=evidence_refs,
            )
            for index, function in enumerate(functions, start=1)
        )
        typology = "project-derived split-level hypothesis"
        palette_ref = "material-intent:split-level"
    else:
        footprint = tuple(
            (x, z) for x in range(3) for z in range(3)
        )
        levels = (SpatialLevel("ground", 64, 4, evidence_refs),)
        volumes = (
            MassingVolume(
                "single-volume",
                SiteBounds((0, 64, 0), (2, 67, 2)),
                ("ground",),
                evidence_refs,
            ),
        )
        zones = tuple(
            SpatialZone(
                zone_id=f"zone-{index}",
                program_node_refs=(function.ref,),
                level_ids=("ground",),
                volume_ids=("single-volume",),
                source_refs=evidence_refs,
            )
            for index, function in enumerate(functions, start=1)
        )
        typology = "project-derived single-level hypothesis"
        palette_ref = "material-intent:single-level"
    zone_for_node = {
        zone.program_node_refs[0]: zone.zone_id for zone in zones
    }
    connections = tuple(
        SpatialConnection(
            connection_id=f"connection-{index}",
            source_zone_id=zone_for_node[relationship.source_node_ref],
            target_zone_id=zone_for_node[relationship.target_node_ref],
            relationship_refs=(relationship.ref,),
            directed=relationship.directed,
            source_refs=evidence_refs,
        )
        for index, relationship in enumerate(relationships, start=1)
    )
    return SpatialOptionProposal(
        option_id=option_id,
        label=f"{brief.project_id} {option_id}",
        program_scenario_ref=None,
        footprint_range_ref=next(
            item.ref
            for item in program.ranges
            if item.metric is ProgramMetricKind.FOOTPRINT
        ),
        grid_basis=SpatialGridBasis(
            horizontal_area_per_cell=1.0,
            area_unit="square_cells",
            source_refs=evidence_refs,
        ),
        footprint_cells=footprint,
        levels=levels,
        volumes=volumes,
        zones=zones,
        connections=connections,
        constraint_responses=(),
        typology_hypothesis=typology,
        palette_refs=(palette_ref,),
        rationale=(
            "The Architect authored this alternative from the current "
            "program, site, and build-policy evidence."
        ),
        responds_to_refs=tuple(item.ref for item in relationships),
        expert_advice_refs=(),
        evidence_refs=evidence_refs,
    )


class SpatialProposalTests(unittest.TestCase):
    def test_multiple_options_compile_without_order_or_selection_authority(
        self,
    ) -> None:
        brief, program, site, policy, state, maturity, gate = _inputs()
        compact = _proposal(
            option_id="compact",
            program=program,
            brief=brief,
            two_levels=False,
        )
        split = _proposal(
            option_id="split",
            program=program,
            brief=brief,
            two_levels=True,
        )

        forward = compile_spatial_options(
            state=state,
            maturity=maturity,
            phase_gate=gate,
            program=program,
            site_context=site,
            build_policy=policy,
            proposals=(split, compact),
        )
        reversed_result = compile_spatial_options(
            state=state,
            maturity=maturity,
            phase_gate=gate,
            program=program,
            site_context=site,
            build_policy=policy,
            proposals=(compact, split),
        )

        self.assertEqual(
            forward.option_set.to_dict(),
            reversed_result.option_set.to_dict(),
        )
        self.assertEqual(
            tuple(item.option_id for item in forward.option_set.options),
            ("compact", "split"),
        )
        self.assertEqual(
            [len(item.proposal.levels) for item in forward.option_set.options],
            [1, 2],
        )
        self.assertEqual(
            SchematicOptionSet.from_dict(forward.option_set.to_dict()),
            forward.option_set,
        )
        output = forward.option_set.to_dict()
        self.assertEqual(output["output_phase"], "schematic_design")
        self.assertIsNone(output["selected_option_id"])
        self.assertFalse(output["ranked"])
        self.assertFalse(output["canonical_write_authority"])
        self.assertIsNone(output["hard_usability_verdict"])
        self.assertFalse(output["design_development_complete"])
        self.assertFalse(output["execution_ready"])

    def test_site_envelope_area_and_stale_inputs_fail_closed(self) -> None:
        brief, program, site, policy, state, maturity, gate = _inputs()
        compact = _proposal(
            option_id="compact",
            program=program,
            brief=brief,
            two_levels=False,
        )
        split = _proposal(
            option_id="split",
            program=program,
            brief=brief,
            two_levels=True,
        )

        outside = replace(
            compact,
            footprint_cells=(*compact.footprint_cells, (40, 40)),
        )
        with self.assertRaisesRegex(
            SpatialCompilationError,
            "footprint exceeds",
        ):
            compile_spatial_options(
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=(outside, split),
            )

        oversized = replace(
            compact,
            grid_basis=replace(
                compact.grid_basis,
                horizontal_area_per_cell=10.0,
            ),
        )
        with self.assertRaisesRegex(
            SpatialCompilationError,
            "outside its cited range",
        ):
            compile_spatial_options(
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=(oversized, split),
            )

        _, _, other_site, _, _, _, _ = _inputs(
            "spatial-other",
            digest_char="b",
        )
        with self.assertRaisesRegex(
            SpatialCompilationError,
            "another run",
        ):
            compile_spatial_options(
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=other_site,
                build_policy=policy,
                proposals=(compact, split),
            )

    def test_wrong_phase_and_stale_gate_are_rejected(self) -> None:
        brief, program, site, policy, state, maturity, gate = _inputs()
        proposals = (
            _proposal(
                option_id="compact",
                program=program,
                brief=brief,
                two_levels=False,
            ),
            _proposal(
                option_id="split",
                program=program,
                brief=brief,
                two_levels=True,
            ),
        )
        wrong_state = replace(
            state,
            phase=DesignPhase.PROGRAMMING.value,
        )
        wrong_maturity = DesignMaturityState.from_operational_state(
            wrong_state,
        )
        with self.assertRaisesRegex(
            SpatialCompilationError,
            "site_resource_coordination",
        ):
            compile_spatial_options(
                state=wrong_state,
                maturity=wrong_maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=proposals,
            )
        stale_state = replace(
            state,
            branch=replace(state.branch, epoch=1),
        )
        with self.assertRaisesRegex(
            ValueError,
            "stale or cross-branch",
        ):
            compile_spatial_options(
                state=stale_state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=proposals,
            )

    def test_hard_input_constraint_requires_explicit_schematic_response(
        self,
    ) -> None:
        (
            brief,
            program,
            site,
            policy,
            state,
            maturity,
            gate,
        ) = _inputs(hard_constraint=True)
        compact = _proposal(
            option_id="compact",
            program=program,
            brief=brief,
            two_levels=False,
        )
        split = _proposal(
            option_id="split",
            program=program,
            brief=brief,
            two_levels=True,
        )
        with self.assertRaisesRegex(
            SpatialCompilationError,
            "unaddressed",
        ):
            compile_spatial_options(
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=(compact, split),
            )

        def with_risk(proposal):
            response = SpatialConstraintResponse(
                response_id=f"{proposal.option_id}-support-risk",
                constraint_ref="build-constraint:support-boundary",
                status=ConstraintResponseStatus.RISK,
                rationale=(
                    "The schematic massing records a support risk for "
                    "discipline coordination in P040."
                ),
                source_refs=(brief.raw_request_ref,),
            )
            return replace(
                proposal,
                constraint_responses=(response,),
                responds_to_refs=(
                    *proposal.responds_to_refs,
                    response.constraint_ref,
                ),
            )

        compiled = compile_spatial_options(
            state=state,
            maturity=maturity,
            phase_gate=gate,
            program=program,
            site_context=site,
            build_policy=policy,
            proposals=(with_risk(compact), with_risk(split)),
        )
        self.assertTrue(
            all(
                option.proposal.constraint_responses[0].status
                is ConstraintResponseStatus.RISK
                for option in compiled.option_set.options
            )
        )
        self.assertIsNone(
            compiled.receipt.to_dict()["hard_usability_verdict"]
        )

    def test_nonisomorphic_projects_do_not_share_answers(self) -> None:
        alpha = _inputs("spatial-alpha", function_count=3)
        beta = _inputs(
            "spatial-beta",
            digest_char="b",
            function_count=4,
        )

        def compile_case(inputs):
            brief, program, site, policy, state, maturity, gate = inputs
            return compile_spatial_options(
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=(
                    _proposal(
                        option_id="compact",
                        program=program,
                        brief=brief,
                        two_levels=False,
                    ),
                    _proposal(
                        option_id="split",
                        program=program,
                        brief=brief,
                        two_levels=True,
                    ),
                ),
            ).option_set

        alpha_set = compile_case(alpha)
        beta_set = compile_case(beta)
        self.assertNotEqual(
            alpha_set.option_set_digest,
            beta_set.option_set_digest,
        )
        self.assertEqual(
            len(alpha_set.options[0].proposal.zones),
            3,
        )
        self.assertEqual(
            len(beta_set.options[0].proposal.zones),
            4,
        )
        self.assertNotIn(
            "spatial-alpha",
            json.dumps(beta_set.to_dict(), ensure_ascii=False),
        )

    def test_renamed_duplicate_and_authority_tampering_fail_closed(self) -> None:
        brief, program, site, policy, state, maturity, gate = _inputs()
        compact = _proposal(
            option_id="compact",
            program=program,
            brief=brief,
            two_levels=False,
        )
        renamed = replace(compact, option_id="renamed")
        with self.assertRaisesRegex(
            SpatialProposalError,
            "spatial signatures",
        ):
            compile_spatial_options(
                state=state,
                maturity=maturity,
                phase_gate=gate,
                program=program,
                site_context=site,
                build_policy=policy,
                proposals=(compact, renamed),
            )

        split = _proposal(
            option_id="split",
            program=program,
            brief=brief,
            two_levels=True,
        )
        result = compile_spatial_options(
            state=state,
            maturity=maturity,
            phase_gate=gate,
            program=program,
            site_context=site,
            build_policy=policy,
            proposals=(compact, split),
        )
        tampered = result.option_set.to_dict()
        tampered["selected_option_id"] = "compact"
        with self.assertRaisesRegex(
            SpatialProposalError,
            "forbidden authority",
        ):
            SchematicOptionSet.from_dict(tampered)

        proposal_payload = compact.to_dict()
        proposal_payload["hard_usability_verdict"] = "passed"
        with self.assertRaisesRegex(
            SpatialProposalError,
            "forbidden authority",
        ):
            SpatialOptionProposal.from_dict(proposal_payload)

        risk = SpatialConstraintResponse(
            response_id="risk",
            constraint_ref="build-constraint:unresolved",
            status=ConstraintResponseStatus.RISK,
            rationale="A later discipline must resolve this risk.",
            source_refs=(brief.raw_request_ref,),
        )
        self.assertEqual(risk.status.value, "risk")


if __name__ == "__main__":
    unittest.main()
