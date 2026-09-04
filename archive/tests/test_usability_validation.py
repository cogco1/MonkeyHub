from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archive.archflow.adapters.voxel_observation import (
    VoxelObservation,
    VoxelObservationExtractor,
)
from archflow.state.model import ArtifactRef, StateRef
from archive.archflow.compilers.voxel_program import compile_building_program
from archive.archflow.validation.usability import (
    UseZoneEvidence,
    UsabilityGate,
    validate_usability,
)
from archive.archflow.workspace.manager import WorkspaceRef


FIXTURES = Path(__file__).parent / "fixtures" / "voxel"
ARTIFACT_DIGEST = "a" * 64
BASE_BRIEF = {
    "use": "test_room",
    "width_blocks": 5,
    "depth_blocks": 5,
    "required_spaces": ["main_room"],
    "minimum_clear_height": 2,
    "entrance_count": 1,
    "circulation_min_width": 1,
}


class UsabilityHarness:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.base = StateRef("run-usability", 0)
        self.workspace = WorkspaceRef("__WORKSPACE__", self.base, self.root)
        self.artifact = ArtifactRef(
            artifact_id="artifact-usability",
            uri="file:///fixture/minecraft-voxel-artifact.json",
            media_type="application/vnd.archflow.minecraft-voxel+json",
            sha256=ARTIFACT_DIGEST,
        )

    def observation(self, fixture: str) -> VoxelObservation:
        payload = json.loads((FIXTURES / fixture).read_text())
        payload["base_state"] = {
            "run_id": self.base.run_id,
            "version": self.base.version,
        }
        path = self.root / fixture
        path.write_text(json.dumps(payload))
        return VoxelObservationExtractor().extract(
            source_artifact=self.artifact,
            base_state=self.base,
            workspace=self.workspace,
            scan_path=path,
        )

    def close(self) -> None:
        self._temporary.cleanup()


def program_with(**overrides: object):
    brief = dict(BASE_BRIEF)
    brief.update(overrides)
    return compile_building_program(brief)


def zone() -> tuple[UseZoneEvidence, ...]:
    return (
        UseZoneEvidence(
            space="main_room",
            region_id="region-001",
            evidence_refs=("voxel-region:region-001",),
        ),
    )


class UsabilityValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = UsabilityHarness()
        self.addCleanup(self.harness.close)

    def test_gold_fixture_passes_all_six_hard_gates(self) -> None:
        observation = self.harness.observation("small_room.json")
        before = observation

        receipt = validate_usability(
            program_with(),
            observation,
            use_zones=zone(),
        )

        self.assertTrue(receipt.passed)
        self.assertFalse(receipt.findings)
        self.assertIs(observation, before)

    def test_each_red_fixture_fails_only_its_primary_gate(self) -> None:
        fixture_cases = json.loads(
            (FIXTURES / "usability_cases.json").read_text()
        )
        self.assertEqual(fixture_cases["schema"], "UsabilityFixtureCases@1")
        for case in fixture_cases["cases"]:
            with self.subTest(case=case["id"]):
                observation = self.harness.observation(case["scan"])
                overrides = case.get("observation_overrides", {})
                if "openings" in overrides:
                    observation = replace(
                        observation,
                        openings=tuple(
                            tuple(item) for item in overrides["openings"]
                        ),
                    )
                if "unsupported_cells" in overrides:
                    observation = replace(
                        observation,
                        unsupported_cells=tuple(
                            tuple(item)
                            for item in overrides["unsupported_cells"]
                        ),
                    )
                receipt = validate_usability(
                    program_with(**case.get("program_overrides", {})),
                    observation,
                    use_zones=() if case.get("omit_use_zone") else zone(),
                )

                self.assertFalse(receipt.passed)
                self.assertEqual(
                    tuple(item.code for item in receipt.findings),
                    (case["expected_code"],),
                )
                finding = receipt.findings[0]
                self.assertTrue(finding.measured)
                self.assertTrue(finding.threshold)

    def test_unknown_geometry_fails_only_geometry_dependent_gates(self) -> None:
        observation = self.harness.observation("small_room.json")
        observation = replace(observation, unknown_count=3)

        receipt = validate_usability(
            program_with(),
            observation,
            use_zones=zone(),
        )

        self.assertEqual(
            {item.gate for item in receipt.findings},
            {
                UsabilityGate.SIZE,
                UsabilityGate.CLEAR_HEIGHT,
                UsabilityGate.ENTRANCE,
                UsabilityGate.CONNECTIVITY,
                UsabilityGate.SUPPORT,
            },
        )
        self.assertNotIn(
            UsabilityGate.USE_ZONES,
            {item.gate for item in receipt.findings},
        )

    def test_soft_evaluation_and_mutation_have_no_input_channel(self) -> None:
        parameters = inspect.signature(validate_usability).parameters

        self.assertNotIn("evaluator", parameters)
        self.assertNotIn("score", parameters)
        self.assertNotIn("mcp_client", parameters)
        self.assertNotIn("workspace", parameters)
        self.assertNotIn("committer", parameters)

    def test_receipt_is_deterministic(self) -> None:
        observation = self.harness.observation("small_room.json")
        program = program_with(width_blocks=6)

        first = validate_usability(program, observation, use_zones=zone())
        second = validate_usability(program, observation, use_zones=zone())

        self.assertEqual(first, second)
        self.assertEqual(first.receipt_id, second.receipt_id)


if __name__ == "__main__":
    unittest.main()
