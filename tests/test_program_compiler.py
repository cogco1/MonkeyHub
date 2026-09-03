from __future__ import annotations

from dataclasses import replace
import unittest

from archflow.capabilities.programming import build_programming_snapshot
from archflow.compilers.program import (
    ProgramAssumptionProposal,
    ProgramCompilationError,
    ProgramMetricApplicabilityBinding,
    ProgramNodeProposal,
    ProgramProposalBundle,
    ProgramRangeProposal,
    ProgramRelationshipProposal,
    ProgramScenarioProposal,
    compile_design_program,
    maximum_footprint_constraint_refs,
)
from archflow.project import ProjectVersionRef
from archflow.compilers.brief import (
    BriefIntentObservation,
    BriefObservation,
    compile_design_brief,
)
from archflow.compilers.commitments import (
    IntentObservation,
    IntentOperator,
    IntentTerm,
)
from archflow.state import (
    BriefClaimKind,
    BriefSlot,
    DesignProgram,
    FactEpistemicStatus,
    ObligationStatus,
    ProgramMetricApplicability,
    ProgramMetricApplicabilityDecision,
    ProgramMetricKind,
    ProgramNodeKind,
    ProgramRelationshipKind,
    ProgramRelationshipStrength,
)


def _request_ref(project_id: str) -> str:
    return f"project://{project_id}/input/raw-request.json"


def _base(project_id: str, digest_char: str = "a") -> ProjectVersionRef:
    return ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256=digest_char * 64,
    )


def _known_brief(
    project_id: str,
    *,
    run_id: str,
    use_value: str,
    digest_char: str,
):
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
            ("requested-use", BriefSlot.USE, "requested-use", use_value),
            (
                "scale-evidence",
                BriefSlot.SIZE,
                "scale-evidence",
                "scale evidence supplied by the project",
            ),
            (
                "occupancy-evidence",
                BriefSlot.OCCUPANCY,
                "occupancy-evidence",
                "occupancy evidence supplied by the project",
            ),
            (
                "program-evidence",
                BriefSlot.SPACE_PROGRAM,
                "program-evidence",
                "activity evidence supplied by the project",
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


def _bundle(
    *,
    prefix: str,
    source_ref: str,
    node_count: int,
    relationship_kinds: tuple[ProgramRelationshipKind, ...],
) -> ProgramProposalBundle:
    assumption_id = f"{prefix}-evidence-bound"
    nodes = tuple(
        ProgramNodeProposal(
            node_id=f"{prefix}-function-{index}",
            kind=ProgramNodeKind.FUNCTION,
            label=f"{prefix} project function {index}",
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for index in range(1, node_count + 1)
    )
    metric_specs = (
        (ProgramMetricKind.CAPACITY, 20.0, 40.0, "people"),
        (ProgramMetricKind.NET_AREA, 200.0, 320.0, "square_metres"),
        (ProgramMetricKind.GROSS_ALLOWANCE, 0.18, 0.32, "ratio"),
        (ProgramMetricKind.FOOTPRINT, 170.0, 290.0, "square_metres"),
        (
            ProgramMetricKind.TOTAL_FLOOR_AREA,
            240.0,
            430.0,
            "square_metres",
        ),
    )
    ranges = tuple(
        ProgramRangeProposal(
            range_id=f"{prefix}-{metric.value}",
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
        for metric, minimum, maximum, unit in metric_specs
    )
    relationships = tuple(
        ProgramRelationshipProposal(
            relationship_id=f"{prefix}-relation-{index}",
            kind=kind,
            source_node_id=nodes[0].node_id,
            target_node_id=nodes[
                1 + (index % (len(nodes) - 1))
            ].node_id,
            strength=(
                ProgramRelationshipStrength.AVOID
                if kind
                in {
                    ProgramRelationshipKind.SEPARATION,
                    ProgramRelationshipKind.NOISE,
                }
                else ProgramRelationshipStrength.PREFERRED
            ),
            directed=kind
            in {
                ProgramRelationshipKind.PUBLIC_PRIVATE,
                ProgramRelationshipKind.CIRCULATION,
            },
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for index, kind in enumerate(relationship_kinds)
    )
    return ProgramProposalBundle(
        assumptions=(
            ProgramAssumptionProposal(
                assumption_id=assumption_id,
                statement=(
                    "The submitted evidence supports testing these ranges "
                    "as hypotheses, not fixed design values."
                ),
                source_refs=(source_ref,),
            ),
        ),
        nodes=nodes,
        ranges=ranges,
        relationships=relationships,
    )


class ProgramCompilerTests(unittest.TestCase):
    def test_non_building_metrics_can_be_explicitly_not_applicable(self) -> None:
        project_id = "case-sectional-episode"
        run_id = "program-001"
        request_ref = _request_ref(project_id)
        decision_refs = tuple(
            (
                metric,
                f"project://{project_id}/runs/{run_id}/records/"
                f"metric-{metric.value}.json",
            )
            for metric in ProgramMetricKind
        )
        outside_run_ref = (
            f"project://{project_id}/runs/program-002/records/"
            "metric-net-area.json"
        )
        base = _base(project_id, "9")
        brief = compile_design_brief(
            project_id=project_id,
            run_id=run_id,
            base=base,
            raw_request_ref=request_ref,
            observations=(
                BriefObservation(
                    observation_id="sectional-episode",
                    slot=BriefSlot.USE,
                    kind=BriefClaimKind.USER_FACT,
                    key="requested-use",
                    value="a non-building sectional spatial episode",
                    epistemic_status=FactEpistemicStatus.DECLARED,
                    authority_id="authority.user",
                    source_refs=(
                        request_ref,
                        *(ref for _, ref in decision_refs),
                        outside_run_ref,
                    ),
                    resolves_slot=True,
                ),
                BriefObservation(
                    observation_id="representation-scale",
                    slot=BriefSlot.SIZE,
                    kind=BriefClaimKind.USER_FACT,
                    key="representation-scale",
                    value="one quarter inch equals one foot",
                    epistemic_status=FactEpistemicStatus.DECLARED,
                    authority_id="authority.user",
                    source_refs=(request_ref,),
                    resolves_slot=True,
                ),
            ),
        ).brief
        decisions = tuple(
            ProgramMetricApplicabilityBinding(
                decision=ProgramMetricApplicabilityDecision(
                    decision_id=f"waive-{metric.value}",
                    project_id=project_id,
                    run_id=run_id,
                    base=base,
                    metric=metric,
                    applicability=(
                        ProgramMetricApplicability.NOT_APPLICABLE
                    ),
                    rationale=(
                        "The deliverable is a sectional episode, not a "
                        "building program measured by this metric."
                    ),
                    authority_id="authority.user",
                    source_refs=(request_ref,),
                ),
                decision_ref=decision_ref,
            )
            for metric, decision_ref in decision_refs
        )
        function = ProgramNodeProposal(
            node_id="trace-event",
            kind=ProgramNodeKind.FUNCTION,
            label="trace one sectional event",
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(request_ref,),
            assumption_ids=("sectional-event-function",),
        )
        activity = ProgramNodeProposal(
            node_id="move-through-section",
            kind=ProgramNodeKind.ACTIVITY,
            label="move through the sectional event",
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(request_ref,),
            assumption_ids=("sectional-event-function",),
        )
        assumption = ProgramAssumptionProposal(
            assumption_id="sectional-event-function",
            statement=(
                "Test the declared sectional episode as a function rather "
                "than a building use."
            ),
            source_refs=(request_ref,),
        )
        result = compile_design_program(
            brief=brief,
            proposals=ProgramProposalBundle(
                assumptions=(assumption,),
                nodes=(function, activity),
                relationships=(
                    ProgramRelationshipProposal(
                        relationship_id="event-to-event",
                        kind=ProgramRelationshipKind.CIRCULATION,
                        source_node_id=function.node_id,
                        target_node_id=activity.node_id,
                        strength=ProgramRelationshipStrength.REQUIRED,
                        directed=True,
                        epistemic_status=FactEpistemicStatus.HYPOTHESIS,
                        source_refs=(request_ref,),
                        assumption_ids=(assumption.assumption_id,),
                    ),
                ),
                metric_applicability=decisions,
            ),
        )

        waived = {
            item.subject_refs[0]: item
            for item in result.program.obligations
            if item.status is ObligationStatus.WAIVED
        }
        self.assertEqual(
            set(waived),
            {
                f"program-metric:{metric.value}"
                for metric in ProgramMetricKind
            },
        )
        self.assertTrue(
            all(
                item.source_ref == item.validator_ref
                for item in waived.values()
            )
        )
        self.assertEqual(
            {item.source_ref for item in waived.values()},
            {decision_ref for _, decision_ref in decision_refs},
        )
        self.assertTrue(
            {decision_ref for _, decision_ref in decision_refs}
            <= set(result.program.evidence_refs)
        )
        self.assertEqual(result.program.ranges, ())
        self.assertEqual(result.receipt.open_obligation_ids, ())
        self.assertEqual(
            {
                value
                for value in result.receipt.proposal_ids
                if value.startswith("metric-applicability:")
            },
            {
                f"metric-applicability:{item.decision.decision_id}"
                for item in decisions
            },
        )
        self.assertEqual(
            DesignProgram.from_dict(result.program.to_dict()),
            result.program,
        )
        self.assertFalse(
            decisions[0].decision.to_dict()["hard_gate_waiver_authority"]
        )
        self.assertEqual(
            ProgramMetricApplicabilityDecision.from_dict(
                decisions[0].decision.to_dict()
            ),
            decisions[0].decision,
        )

        contradictory_range = ProgramRangeProposal(
            range_id="invented-net-area",
            metric=ProgramMetricKind.NET_AREA,
            applies_to_node_id=function.node_id,
            minimum=10.0,
            maximum=20.0,
            unit="square_metres",
            scenario_id=None,
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(request_ref,),
            assumption_ids=("unsupported-area",),
        )
        with self.assertRaisesRegex(
            ProgramCompilationError,
            "not-applicable metric",
        ):
            compile_design_program(
                brief=brief,
                proposals=ProgramProposalBundle(
                    assumptions=(
                        assumption,
                        ProgramAssumptionProposal(
                            assumption_id="unsupported-area",
                            statement="Test a deliberately conflicting area.",
                            source_refs=(request_ref,),
                        ),
                    ),
                    nodes=(function,),
                    ranges=(contradictory_range,),
                    metric_applicability=decisions,
                ),
            )

        valid = decisions[0]
        invalid_cases = (
            (
                "not exact-base",
                replace(
                    valid,
                    decision=replace(
                        valid.decision,
                        base=ProjectVersionRef(
                            project_id=project_id,
                            version=1,
                            state_sha256="8" * 64,
                        ),
                    ),
                ),
            ),
            (
                "not exact-base",
                replace(
                    valid,
                    decision=replace(valid.decision, run_id="program-002"),
                ),
            ),
            (
                "absent from the exact-base brief",
                replace(
                    valid,
                    decision_ref=(
                        f"project://{project_id}/runs/{run_id}/records/"
                        "missing-metric.json"
                    ),
                ),
            ),
            (
                "outside the run",
                replace(valid, decision_ref=outside_run_ref),
            ),
            (
                "source is absent from the exact-base brief",
                replace(
                    valid,
                    decision=replace(
                        valid.decision,
                        source_refs=(
                            f"project://{project_id}/input/unbound.json",
                        ),
                    ),
                ),
            ),
        )
        for message, invalid in invalid_cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ProgramCompilationError,
                message,
            ):
                compile_design_program(
                    brief=brief,
                    proposals=ProgramProposalBundle(
                        metric_applicability=(invalid,),
                    ),
                )

    def test_non_isomorphic_requests_do_not_share_project_answers(self) -> None:
        first_brief = _known_brief(
            "case-alpha",
            run_id="program-001",
            use_value="alpha activity system",
            digest_char="a",
        )
        second_brief = _known_brief(
            "case-beta",
            run_id="program-001",
            use_value="beta activity system",
            digest_char="b",
        )
        first = compile_design_program(
            brief=first_brief,
            proposals=_bundle(
                prefix="alpha",
                source_ref=_request_ref("case-alpha"),
                node_count=3,
                relationship_kinds=tuple(ProgramRelationshipKind),
            ),
        ).program
        second = compile_design_program(
            brief=second_brief,
            proposals=_bundle(
                prefix="beta",
                source_ref=_request_ref("case-beta"),
                node_count=5,
                relationship_kinds=(
                    ProgramRelationshipKind.SEPARATION,
                    ProgramRelationshipKind.CIRCULATION,
                ),
            ),
        ).program

        self.assertTrue(
            {item.label for item in first.nodes}.isdisjoint(
                item.label for item in second.nodes
            )
        )
        self.assertNotEqual(len(first.nodes), len(second.nodes))
        self.assertNotEqual(
            len(first.relationships),
            len(second.relationships),
        )
        self.assertNotEqual(first.program_digest, second.program_digest)
        self.assertEqual(
            {item.metric for item in first.ranges},
            set(ProgramMetricKind),
        )
        self.assertEqual(
            {item.kind for item in first.relationships},
            set(ProgramRelationshipKind),
        )
        self.assertEqual(
            DesignProgram.from_dict(first.to_dict()),
            first,
        )

        with self.assertRaises(ProgramCompilationError):
            compile_design_program(
                brief=first_brief,
                proposals=_bundle(
                    prefix="foreign",
                    source_ref=_request_ref("case-beta"),
                    node_count=3,
                    relationship_kinds=(
                        ProgramRelationshipKind.ADJACENCY,
                    ),
                ),
            )

    def test_missing_scale_requires_multiple_bounded_scenarios(self) -> None:
        brief = compile_design_brief(
            project_id="case-scale",
            run_id="program-001",
            base=_base("case-scale", "c"),
            raw_request_ref=_request_ref("case-scale"),
            observations=(
                BriefObservation(
                    observation_id="requested-use",
                    slot=BriefSlot.USE,
                    kind=BriefClaimKind.USER_FACT,
                    key="requested-use",
                    value="a project with no supplied scale",
                    epistemic_status=FactEpistemicStatus.DECLARED,
                    authority_id="authority.user",
                    source_refs=(_request_ref("case-scale"),),
                    resolves_slot=True,
                ),
            ),
        ).brief
        assumptions = tuple(
            ProgramAssumptionProposal(
                assumption_id=f"scale-{suffix}",
                statement=f"Test the {suffix} evidence-bounded scale case.",
                source_refs=(_request_ref("case-scale"),),
            )
            for suffix in ("lower", "upper")
        )
        ranges = tuple(
            ProgramRangeProposal(
                range_id=f"{suffix}-total-area",
                metric=ProgramMetricKind.TOTAL_FLOOR_AREA,
                applies_to_node_id=None,
                minimum=minimum,
                maximum=maximum,
                unit="square_metres",
                scenario_id=f"scenario-{suffix}",
                epistemic_status=FactEpistemicStatus.HYPOTHESIS,
                source_refs=(_request_ref("case-scale"),),
                assumption_ids=(f"scale-{suffix}",),
            )
            for suffix, minimum, maximum in (
                ("lower", 300.0, 500.0),
                ("upper", 700.0, 1_100.0),
            )
        )
        scenarios = tuple(
            ProgramScenarioProposal(
                scenario_id=f"scenario-{suffix}",
                label=f"{suffix} bounded scale",
                range_ids=(f"{suffix}-total-area",),
                source_refs=(_request_ref("case-scale"),),
                assumption_ids=(f"scale-{suffix}",),
            )
            for suffix in ("lower", "upper")
        )
        result = compile_design_program(
            brief=brief,
            proposals=ProgramProposalBundle(
                assumptions=assumptions,
                ranges=ranges,
                scenarios=scenarios,
            ),
        )

        self.assertEqual(len(result.program.scenarios), 2)
        self.assertTrue(
            all(item.maximum > item.minimum for item in result.program.ranges)
        )
        self.assertFalse(result.program.to_dict()["footprint_selected"])

        with self.assertRaisesRegex(
            ProgramCompilationError,
            "multiple named bounded scenarios",
        ):
            compile_design_program(
                brief=brief,
                proposals=ProgramProposalBundle(
                    assumptions=(assumptions[0],),
                    ranges=(ranges[0],),
                    scenarios=(scenarios[0],),
                ),
            )

        unresolved = compile_design_program(
            brief=brief,
            proposals=ProgramProposalBundle(),
        ).program
        self.assertIn(
            "resolve.program.scale-scenarios",
            {item.obligation_id for item in unresolved.obligations},
        )
        self.assertEqual(unresolved.ranges, ())

    def test_explicit_maximum_footprint_remains_external_constraint(self) -> None:
        request_ref = _request_ref("case-limit")
        brief = compile_design_brief(
            project_id="case-limit",
            run_id="program-001",
            base=_base("case-limit", "d"),
            raw_request_ref=request_ref,
            intent_observations=(
                BriefIntentObservation(
                    slot=BriefSlot.SIZE,
                    observation=IntentObservation(
                        observation_id="maximum-footprint",
                        raw_text="The project has an explicit maximum footprint.",
                        source_event_ref=(
                            "project://case-limit/events/request.json"
                        ),
                        authority_id="authority.user",
                        scope_ref="project://case-limit",
                        interpretations=(
                            IntentTerm(
                                parameter_key="building.maximum_footprint",
                                operator=IntentOperator.MAXIMUM,
                                value=900,
                                unit="square_metres",
                            ),
                        ),
                        evidence_refs=(request_ref,),
                    ),
                ),
            ),
        ).brief
        result = compile_design_program(
            brief=brief,
            proposals=ProgramProposalBundle(),
        ).program

        expected = maximum_footprint_constraint_refs(brief)
        self.assertEqual(len(expected), 1)
        self.assertIn(expected[0], result.external_constraint_refs)
        self.assertFalse(
            any(
                item.metric is ProgramMetricKind.FOOTPRINT
                for item in result.ranges
            )
        )

    def test_programming_snapshot_is_read_only_and_state_responsive(self) -> None:
        brief = _known_brief(
            "case-snapshot",
            run_id="program-001",
            use_value="fully supplied synthetic activity system",
            digest_char="e",
        )
        snapshot = build_programming_snapshot(brief)
        payload = snapshot.to_dict()

        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["generation_authority"])
        self.assertEqual(
            set(snapshot.obligation_topics),
            {
                "program.functions",
                "program.relationships",
                "program.capacity",
                "program.area",
            },
        )
        resumed = build_programming_snapshot(
            brief,
            obligation_topics=("program.relationships",),
        )
        self.assertEqual(
            resumed.obligation_topics,
            ("program.relationships",),
        )


if __name__ == "__main__":
    unittest.main()
