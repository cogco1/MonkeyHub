from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path

from archflow.adapters.voxel_observation import (
    FORBIDDEN_WRITE_TOOLS,
    READ_ONLY_TOOL_ALLOWLIST,
    VoxelObservationError,
    VoxelObservationErrorCode,
    VoxelObservationExtractor,
)
from archflow.state import ArtifactRef, StateRef
from archflow.workspace import WorkspaceRef


FIXTURES = Path(__file__).parent / "fixtures" / "voxel"
ARTIFACT_DIGEST = "a" * 64


def artifact(digest: str = ARTIFACT_DIGEST) -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-fixture",
        uri="file:///fixture/minecraft-voxel-artifact.json",
        media_type="application/vnd.archflow.minecraft-voxel+json",
        sha256=digest,
    )


class ObservationHarness:
    def __init__(self, fixture: str) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.base = StateRef("run-fixture", 0)
        self.workspace = WorkspaceRef("__WORKSPACE__", self.base, self.root)
        self.scan_path = self.root / fixture
        self.scan_path.write_bytes((FIXTURES / fixture).read_bytes())

    def close(self) -> None:
        self._temporary.cleanup()


class VoxelObservationTests(unittest.TestCase):
    def test_small_room_extracts_deterministic_grounded_observation(self) -> None:
        harness = ObservationHarness("small_room.json")
        self.addCleanup(harness.close)
        extractor = VoxelObservationExtractor()

        first = extractor.extract(
            source_artifact=artifact(),
            base_state=harness.base,
            workspace=harness.workspace,
            scan_path=harness.scan_path,
        )
        second = extractor.extract(
            source_artifact=artifact(),
            base_state=harness.base,
            workspace=harness.workspace,
            scan_path=harness.scan_path,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first.envelope.minimum, (0, 0, 0))
        self.assertEqual(first.envelope.maximum, (4, 3, 4))
        self.assertEqual(len(first.openings), 2)
        self.assertEqual(len(first.walkable_cells), 10)
        self.assertEqual(len(first.connected_regions), 1)
        self.assertTrue(first.connected_regions[0].touches_opening)
        self.assertTrue(first.support_relations)
        self.assertFalse(first.unsupported_cells)
        self.assertEqual(first.unknown_count, 0)

    def test_disconnected_fixture_reports_two_regions(self) -> None:
        harness = ObservationHarness("disconnected_rooms.json")
        self.addCleanup(harness.close)

        observation = VoxelObservationExtractor().extract(
            source_artifact=artifact(),
            base_state=harness.base,
            workspace=harness.workspace,
            scan_path=harness.scan_path,
        )

        self.assertEqual(len(observation.connected_regions), 2)
        self.assertEqual(
            tuple(len(region.cells) for region in observation.connected_regions),
            (9, 9),
        )
        self.assertFalse(any(region.touches_opening for region in observation.connected_regions))

    def test_source_digest_mismatch_is_rejected(self) -> None:
        harness = ObservationHarness("small_room.json")
        self.addCleanup(harness.close)

        with self.assertRaises(VoxelObservationError) as caught:
            VoxelObservationExtractor().extract(
                source_artifact=artifact("b" * 64),
                base_state=harness.base,
                workspace=harness.workspace,
                scan_path=harness.scan_path,
            )

        self.assertEqual(
            caught.exception.code,
            VoxelObservationErrorCode.SOURCE_DIGEST_MISMATCH,
        )

    def test_incomplete_scan_reports_bounded_unknowns(self) -> None:
        harness = ObservationHarness("small_room.json")
        self.addCleanup(harness.close)
        payload = json.loads(harness.scan_path.read_text())
        payload["default_kind"] = "unknown"
        payload["runs"] = [
            {"kind": "solid", "from": [0, 0, 0], "to": [4, 0, 4]},
        ]
        payload["cells"] = []
        harness.scan_path.write_text(json.dumps(payload))

        observation = VoxelObservationExtractor(max_unknown_samples=5).extract(
            source_artifact=artifact(),
            base_state=harness.base,
            workspace=harness.workspace,
            scan_path=harness.scan_path,
        )

        self.assertEqual(observation.unknown_count, 75)
        self.assertEqual(len(observation.unknowns), 5)
        self.assertFalse(observation.walkable_cells)

    def test_malformed_and_oversized_scans_fail_boundedly(self) -> None:
        harness = ObservationHarness("small_room.json")
        self.addCleanup(harness.close)
        harness.scan_path.write_text("{")

        with self.assertRaises(VoxelObservationError) as malformed:
            VoxelObservationExtractor().extract(
                source_artifact=artifact(),
                base_state=harness.base,
                workspace=harness.workspace,
                scan_path=harness.scan_path,
            )
        self.assertEqual(
            malformed.exception.code,
            VoxelObservationErrorCode.MALFORMED_JSON,
        )
        self.assertLess(len(str(malformed.exception)), 1200)

        harness.scan_path.write_text("x" * 20)
        with self.assertRaises(VoxelObservationError) as oversized:
            VoxelObservationExtractor(max_scan_bytes=10).extract(
                source_artifact=artifact(),
                base_state=harness.base,
                workspace=harness.workspace,
                scan_path=harness.scan_path,
            )
        self.assertEqual(
            oversized.exception.code,
            VoxelObservationErrorCode.OVERSIZED_SCAN,
        )

    def test_extractor_has_no_world_write_capability(self) -> None:
        self.assertFalse(READ_ONLY_TOOL_ALLOWLIST & FORBIDDEN_WRITE_TOOLS)
        parameters = inspect.signature(VoxelObservationExtractor.extract).parameters
        self.assertNotIn("mcp_client", parameters)
        self.assertNotIn("committer", parameters)
        self.assertNotIn("store", parameters)


if __name__ == "__main__":
    unittest.main()
