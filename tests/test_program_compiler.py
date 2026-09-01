from __future__ import annotations

import json
import unittest

import archflow.compilers as compilers_api
import archflow.compilers.program as canonical_program
import archflow.runtime.program_compiler as legacy_program
from archflow.capabilities.programming import build_programming_snapshot
from archflow.compilers.program import (
    ProgramAssumptionProposal,
    ProgramCompilationError,
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
    ProgramMetricKind,
    ProgramNodeKind,
    ProgramRelationshipKind,
    ProgramRelationshipStrength,
)
from archflow.state.geometry_program import digest_value


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
    def test_legacy_facade_and_package_export_canonical_objects(self) -> None:
        for name in legacy_program.__all__:
            with self.subTest(name=name):
                canonical = getattr(canonical_program, name)
                self.assertIs(canonical, getattr(legacy_program, name))
                self.assertIs(canonical, getattr(compilers_api, name))

    def test_reference_program_preserves_schema_digests_and_behavior(self) -> None:
        result = compile_design_program(
            brief=_known_brief(
                "case-alpha",
                run_id="program-001",
                use_value="alpha activity system",
                digest_char="a",
            ),
            proposals=_bundle(
                prefix="alpha",
                source_ref=_request_ref("case-alpha"),
                node_count=3,
                relationship_kinds=tuple(ProgramRelationshipKind),
            ),
        )

        self.assertEqual("DesignProgram@1", result.program.SCHEMA)
        self.assertEqual(
            "f2f28e909bc97369d6bd463cdf2acb9611dfab4dee6c4719dfba3a8bb490c3b7",
            result.program.program_digest,
        )
        self.assertEqual("ProgramCompilationReceipt@1", result.receipt.SCHEMA)
        self.assertEqual(
            "program-compilation.df4b3ff09ca21c3705f26527",
            result.receipt.compilation_id,
        )
        self.assertEqual(
            "41d00519c1e9bb1921c29223b51c643acc8a92a93fddd4cb89a836147225eb95",
            digest_value(result.receipt.to_dict()),
        )
        self.assertEqual(14, len(result.receipt.proposal_ids))
        self.assertIs(result.receipt.to_dict()["generation_authority"], False)

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

    def test_schema_contains_no_spatial_or_material_defaults(self) -> None:
        brief = compile_design_brief(
            project_id="case-empty",
            run_id="program-001",
            base=_base("case-empty", "f"),
            raw_request_ref=_request_ref("case-empty"),
        ).brief
        payload = compile_design_program(
            brief=brief,
            proposals=ProgramProposalBundle(),
        ).program.to_dict()
        serialized = json.dumps(payload, sort_keys=True)

        for forbidden in (
            '"coordinates"',
            '"dimensions"',
            '"materials"',
            '"palette"',
            '"topology"',
            '"room_list"',
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
