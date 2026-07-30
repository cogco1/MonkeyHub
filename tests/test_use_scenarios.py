from __future__ import annotations

import inspect
import unittest
from dataclasses import replace

from archflow.runtime import initial_state
from archflow.submission import CandidateDelta, CandidateSubmission
from archflow.validation import ArtifactPresentValidator, validate_submission
from archflow.validation.use_scenarios import (
    ScenarioObservationBinding,
    ScenarioObservationSource,
    UseScenarioKind,
    UseScenarioStatus,
    UseScenarioValidator,
    compile_use_scenarios,
    evaluate_use_scenarios,
)
from archflow.validation.usability import UseZoneEvidence
from tests.test_usability_validation import UsabilityHarness, program_with


def _zone(
    space: str,
    region_id: str = "region-001",
) -> UseZoneEvidence:
    return UseZoneEvidence(
        space=space,
        region_id=region_id,
        evidence_refs=(f"voxel-region:{region_id}",),
    )


def _binding(program, observation) -> ScenarioObservationBinding:
    return ScenarioObservationBinding.from_sandbox_realization(
        binding_id="sandbox-observation-binding",
        program=program,
        observation=observation,
        candidate_program_digest="c" * 64,
        geometry_program_digest="d" * 64,
        realization_receipt_digest="e" * 64,
        evidence_refs=("receipt:sandbox-realization",),
    )


def _validator(program, observation, zones) -> UseScenarioValidator:
    return UseScenarioValidator(
        program,
        observation,
        zones,
        observation_binding=_binding(program, observation),
        candidate_program_digest="c" * 64,
        geometry_program_digest="d" * 64,
        realization_receipt_digest="e" * 64,
    )


class UseScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = UsabilityHarness()
        self.addCleanup(self.harness.close)
        self.observation = self.harness.observation("small_room.json")
        self.state = initial_state(
            "Qualitative test use",
            run_id=self.harness.base.run_id,
        )
        self.submission = CandidateSubmission(
            submission_id="candidate-use-scenario",
            base=self.state.ref,
            workspace_id=self.harness.workspace.workspace_id,
            intent="Validate the observed candidate only.",
            delta=CandidateDelta(
                artifacts_add=(self.harness.artifact,)
            ),
            claims=(),
            evidence_refs=(self.harness.artifact.artifact_id,),
        )

    def test_gold_entrance_and_inter_zone_routes_are_derived(self) -> None:
        program = program_with(
            required_spaces=["main_room", "service"],
        )
        zones = (_zone("main_room"), _zone("service"))

        scenarios = compile_use_scenarios(
            program,
            use_zones=zones,
        )
        results, findings = evaluate_use_scenarios(
            program,
            self.observation,
            use_zones=zones,
        )

        self.assertEqual(len(scenarios), 3)
        self.assertEqual(
            {item.kind for item in scenarios},
            {
                UseScenarioKind.ENTRANCE_TO_ZONE,
                UseScenarioKind.INTER_ZONE,
            },
        )
        self.assertFalse(findings)
        self.assertTrue(
            all(
                item.status is UseScenarioStatus.PASSED
                for item in results
            )
        )
        self.assertTrue(all(item.route for item in results))

    def test_blocked_route_and_unreachable_zone_are_hard_findings(
        self,
    ) -> None:
        observation = self.harness.observation(
            "disconnected_with_entrances.json"
        )
        observation = replace(
            observation,
            openings=tuple(
                cell for cell in observation.openings if cell[0] == 2
            ),
        )
        validator = _validator(
            program_with(),
            observation,
            (_zone("main_room", "region-002"),),
        )

        receipt = validate_submission(
            self.state,
            self.submission,
            (ArtifactPresentValidator(), validator),
        )

        self.assertFalse(receipt.passed)
        self.assertIn(
            "use_scenario.entrance_to_zone.failed",
            {item.code for item in receipt.findings},
        )
        self.assertTrue(
            any(
                "no route" in item.message
                for item in receipt.findings
            )
        )

    def test_low_headroom_route_is_rejected_with_measurement(self) -> None:
        program = program_with(minimum_clear_height=3)
        validator = _validator(
            program,
            self.observation,
            (_zone("main_room"),),
        )

        receipt = validate_submission(
            self.state,
            self.submission,
            (validator,),
        )

        self.assertFalse(receipt.passed)
        finding = receipt.findings[0]
        self.assertEqual(
            finding.code,
            "use_scenario.entrance_to_zone.failed",
        )
        self.assertIn("headroom", finding.message)
        self.assertTrue(finding.evidence_refs)

    def test_unknown_scan_does_not_fail_a_proven_route(self) -> None:
        observation = replace(
            self.observation,
            unknown_count=1,
        )
        results, _ = evaluate_use_scenarios(
            program_with(),
            observation,
            use_zones=(_zone("main_room"),),
        )

        self.assertIs(
            results[0].status,
            UseScenarioStatus.PASSED,
        )

    def test_exact_candidate_binding_prevents_mcp_success_bypass(
        self,
    ) -> None:
        wrong_submission = replace(
            self.submission,
            delta=CandidateDelta(),
        )
        validator = _validator(
            program_with(),
            self.observation,
            (_zone("main_room"),),
        )

        receipt = validate_submission(
            self.state,
            wrong_submission,
            (validator,),
        )

        self.assertFalse(receipt.passed)
        self.assertEqual(
            receipt.findings[0].code,
            "use_scenario.candidate_binding_mismatch",
        )

    def test_primary_validation_requires_exact_sandbox_source(self) -> None:
        program = program_with()
        binding = replace(
            _binding(program, self.observation),
            source=ScenarioObservationSource.EXTERNAL_COMPARISON,
        )
        external = UseScenarioValidator(
            program,
            self.observation,
            (_zone("main_room"),),
            observation_binding=binding,
            candidate_program_digest="c" * 64,
            geometry_program_digest="d" * 64,
            realization_receipt_digest="e" * 64,
        )
        external_receipt = validate_submission(
            self.state,
            self.submission,
            (external,),
        )
        self.assertFalse(external_receipt.passed)
        self.assertEqual(
            external_receipt.findings[0].code,
            "use_scenario.primary_source_not_sandbox",
        )

        stale = UseScenarioValidator(
            program,
            self.observation,
            (_zone("main_room"),),
            observation_binding=replace(
                _binding(program, self.observation),
                geometry_program_digest="f" * 64,
                observation_digest="0" * 64,
            ),
            candidate_program_digest="c" * 64,
            geometry_program_digest="d" * 64,
            realization_receipt_digest="e" * 64,
        )
        stale_receipt = validate_submission(
            self.state,
            self.submission,
            (stale,),
        )
        self.assertFalse(stale_receipt.passed)
        self.assertEqual(
            stale_receipt.findings[0].code,
            "use_scenario.sandbox_binding_mismatch",
        )

    def test_validator_has_no_mutation_or_soft_evaluation_channel(
        self,
    ) -> None:
        parameters = inspect.signature(
            UseScenarioValidator.validate
        ).parameters
        for forbidden in (
            "mcp_client",
            "workspace",
            "committer",
            "evaluator",
            "score",
        ):
            self.assertNotIn(forbidden, parameters)


if __name__ == "__main__":
    unittest.main()
