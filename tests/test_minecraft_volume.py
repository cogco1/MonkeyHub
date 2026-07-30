from __future__ import annotations

import json
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from archflow.adapters.minecraft_volume import (
    MinecraftVolumeAdapter,
    MinecraftVolumeBounds,
    MinecraftVolumeError,
    MinecraftVolumeErrorCode,
    MinecraftVolumeHttpTransport,
)
from archflow.adapters.voxel_observation import VoxelObservationExtractor
from archflow.state import ArtifactRef, StateRef
from archflow.validation import compile_building_program
from archflow.validation.use_scenarios import (
    UseScenarioStatus,
    evaluate_use_scenarios,
)
from archflow.validation.usability import UseZoneEvidence
from archflow.workspace import WorkspaceRef


ARTIFACT_SHA256 = "a" * 64


class FakeTransport:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[MinecraftVolumeBounds] = []

    def scan_volume(self, bounds: MinecraftVolumeBounds) -> object:
        self.calls.append(bounds)
        return deepcopy(self.response)


def response(
    bounds: MinecraftVolumeBounds,
    *,
    unknown: set[tuple[int, int, int]] | None = None,
) -> dict[str, object]:
    unknown = unknown or set()
    cells: list[dict[str, object]] = []
    known_count = 0
    unknown_count = 0
    for x in range(bounds.minimum[0], bounds.maximum[0] + 1):
        for y in range(bounds.minimum[1], bounds.maximum[1] + 1):
            for z in range(bounds.minimum[2], bounds.maximum[2] + 1):
                at = (x, y, z)
                if at in unknown:
                    kind = "unknown"
                    block_id = ""
                    unknown_count += 1
                elif y == bounds.minimum[1]:
                    kind = "solid"
                    block_id = "minecraft:stone"
                    known_count += 1
                elif at == (1, bounds.minimum[1] + 1, 0):
                    kind = "opening"
                    block_id = "minecraft:oak_door"
                    known_count += 1
                else:
                    kind = "air"
                    block_id = "minecraft:air"
                    known_count += 1
                cells.append(
                    {
                        "x": x,
                        "y": y,
                        "z": z,
                        "kind": kind,
                        "blockId": block_id,
                        "state": "",
                    }
                )
    return {
        "schema": "MinecraftVolumeScan@1",
        "dimension": "minecraft:overworld",
        "bounds": bounds.to_dict(),
        "volume": bounds.volume,
        "complete": unknown_count == 0,
        "knownCount": known_count,
        "unknownCount": unknown_count,
        "cells": cells,
    }


def room_response(bounds: MinecraftVolumeBounds) -> dict[str, object]:
    payload = response(bounds)
    cells = payload["cells"]
    assert isinstance(cells, list)
    known_count = 0
    for cell in cells:
        assert isinstance(cell, dict)
        x, y, z = cell["x"], cell["y"], cell["z"]
        boundary = (
            y in {bounds.minimum[1], bounds.maximum[1]}
            or x in {bounds.minimum[0], bounds.maximum[0]}
            or z in {bounds.minimum[2], bounds.maximum[2]}
        )
        doorway = (
            x == 2
            and z == bounds.minimum[2]
            and y in {bounds.minimum[1] + 1, bounds.minimum[1] + 2}
        )
        if doorway:
            cell["kind"] = "opening"
            cell["blockId"] = "minecraft:oak_door"
            cell["state"] = "open=true"
        elif boundary:
            cell["kind"] = "solid"
            cell["blockId"] = "minecraft:stone"
            cell["state"] = ""
        else:
            cell["kind"] = "air"
            cell["blockId"] = "minecraft:air"
            cell["state"] = ""
        known_count += 1
    payload["knownCount"] = known_count
    payload["unknownCount"] = 0
    payload["complete"] = True
    return payload


class SandboxVolumeHandler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def do_POST(self) -> None:
        if (
            self.path != "/v1/tools/volume"
            or self.headers.get("Authorization") != "Bearer sandbox-token"
        ):
            self.send_error(401)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        self.server.request_payload = request
        encoded = json.dumps(
            self.server.response_payload,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class MinecraftVolumeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = StateRef("run-volume", 0)
        self.workspace = WorkspaceRef("workspace-volume", self.base, self.root)
        self.artifact = ArtifactRef(
            artifact_id="artifact-volume",
            uri="file:///candidate.json",
            media_type="application/vnd.archflow.minecraft-voxel+json",
            sha256=ARTIFACT_SHA256,
        )
        self.bounds = MinecraftVolumeBounds((0, 0, 0), (2, 2, 2))

    def capture(
        self,
        raw_response: object,
    ):
        return MinecraftVolumeAdapter().capture(
            source_artifact=self.artifact,
            base_state=self.base,
            workspace=self.workspace,
            bounds=self.bounds,
            transport=FakeTransport(raw_response),
        )

    def test_complete_volume_becomes_deterministic_voxel_observation(self) -> None:
        capture = self.capture(response(self.bounds))
        scan_path = self.root / "volume-scan.json"
        scan_path.write_bytes(capture.scan_bytes)
        first = capture.scan_bytes
        self.assertEqual(capture.known_count, self.bounds.volume)
        self.assertEqual(capture.unknown_count, 0)

        observation = VoxelObservationExtractor().extract(
            source_artifact=self.artifact,
            base_state=self.base,
            workspace=self.workspace,
            scan_path=scan_path,
        )

        self.assertEqual(observation.unknown_count, 0)
        self.assertIn((1, 1, 0), observation.openings)
        self.assertTrue(observation.walkable_cells)
        payload = json.loads(first)
        self.assertEqual(payload["schema"], "VoxelScan@1")
        self.assertEqual(payload["provider_observation"]["complete"], True)

    def test_loopback_protocol_sandbox_reaches_p030_gold(self) -> None:
        bounds = MinecraftVolumeBounds((0, 0, 0), (4, 3, 4))
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            SandboxVolumeHandler,
        )
        server.response_payload = room_response(bounds)
        server.request_payload = None
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        thread.start()
        try:
            capture = MinecraftVolumeAdapter().capture(
                source_artifact=self.artifact,
                base_state=self.base,
                workspace=self.workspace,
                bounds=bounds,
                transport=MinecraftVolumeHttpTransport(
                    bridge_url=(
                        f"http://127.0.0.1:{server.server_port}"
                    ),
                    bearer_token="sandbox-token",
                ),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(
            server.request_payload,
            {
                "minX": 0,
                "minY": 0,
                "minZ": 0,
                "maxX": 4,
                "maxY": 3,
                "maxZ": 4,
            },
        )
        scan_path = self.root / "sandbox-room-scan.json"
        scan_path.write_bytes(capture.scan_bytes)
        observation = VoxelObservationExtractor().extract(
            source_artifact=self.artifact,
            base_state=self.base,
            workspace=self.workspace,
            scan_path=scan_path,
        )
        program = compile_building_program(
            {
                "use": "sandbox_room",
                "width_blocks": 5,
                "depth_blocks": 5,
                "required_spaces": ["main_room"],
                "minimum_clear_height": 2,
                "entrance_count": 1,
                "circulation_min_width": 1,
            }
        )
        results, findings = evaluate_use_scenarios(
            program,
            observation,
            use_zones=(
                UseZoneEvidence(
                    space="main_room",
                    region_id="region-001",
                    evidence_refs=(
                        "comparison:voxel-region:region-001",
                    ),
                ),
            ),
        )

        self.assertFalse(findings)
        self.assertTrue(
            all(
                result.status is UseScenarioStatus.PASSED
                for result in results
            )
        )
        self.assertEqual(capture.unknown_count, 0)
        self.assertEqual(len(observation.openings), 2)
        self.assertEqual(len(observation.connected_regions), 1)

    def test_unloaded_cell_remains_explicit_unknown(self) -> None:
        capture = self.capture(response(self.bounds, unknown={(2, 2, 2)}))
        scan_path = self.root / "unknown-volume-scan.json"
        scan_path.write_bytes(capture.scan_bytes)
        observation = VoxelObservationExtractor().extract(
            source_artifact=self.artifact,
            base_state=self.base,
            workspace=self.workspace,
            scan_path=scan_path,
        )

        self.assertEqual(capture.unknown_count, 1)
        self.assertEqual(observation.unknown_count, 1)
        self.assertEqual(observation.unknowns[0].coordinate, (2, 2, 2))

    def test_wrong_bounds_duplicate_and_missing_cells_are_rejected(self) -> None:
        cases: list[tuple[MinecraftVolumeErrorCode, dict[str, object]]] = []
        wrong = response(self.bounds)
        wrong["bounds"] = {"min": [0, 0, 0], "max": [3, 2, 2]}
        cases.append((MinecraftVolumeErrorCode.BOUNDS_MISMATCH, wrong))

        duplicate = response(self.bounds)
        duplicate["cells"][-1] = deepcopy(duplicate["cells"][0])
        cases.append((MinecraftVolumeErrorCode.DUPLICATE_CELL, duplicate))

        missing = response(self.bounds)
        missing["cells"].pop()
        cases.append((MinecraftVolumeErrorCode.MISSING_CELL, missing))

        for index, (code, raw) in enumerate(cases):
            with self.subTest(code=code):
                with self.assertRaises(MinecraftVolumeError) as caught:
                    self.capture(raw)
                self.assertEqual(caught.exception.code, code)

    def test_oversized_and_wrong_base_requests_are_rejected(self) -> None:
        oversized_bounds = MinecraftVolumeBounds((0, 0, 0), (9, 9, 9))
        with self.assertRaises(MinecraftVolumeError) as oversized:
            MinecraftVolumeAdapter(max_cells=999).capture(
                source_artifact=self.artifact,
                base_state=self.base,
                workspace=self.workspace,
                bounds=oversized_bounds,
                transport=FakeTransport(response(oversized_bounds)),
            )
        self.assertEqual(
            oversized.exception.code,
            MinecraftVolumeErrorCode.OVERSIZED,
        )

        wrong_workspace = WorkspaceRef(
            self.workspace.workspace_id,
            StateRef("other-run", 0),
            self.root,
        )
        with self.assertRaises(MinecraftVolumeError) as escaped:
            MinecraftVolumeAdapter().capture(
                source_artifact=self.artifact,
                base_state=self.base,
                workspace=wrong_workspace,
                bounds=self.bounds,
                transport=FakeTransport(response(self.bounds)),
            )
        self.assertEqual(
            escaped.exception.code,
            MinecraftVolumeErrorCode.BASE_STATE_MISMATCH,
        )

    def test_invalid_kind_and_declared_count_drift_are_rejected(self) -> None:
        invalid = response(self.bounds)
        invalid["cells"][0]["kind"] = "water"
        with self.assertRaises(MinecraftVolumeError) as invalid_error:
            self.capture(invalid)
        self.assertEqual(
            invalid_error.exception.code,
            MinecraftVolumeErrorCode.INVALID_CELL,
        )

        drifted = response(self.bounds)
        drifted["knownCount"] -= 1
        drifted["unknownCount"] += 1
        drifted["complete"] = False
        with self.assertRaises(MinecraftVolumeError) as drift_error:
            self.capture(drifted)
        self.assertEqual(
            drift_error.exception.code,
            MinecraftVolumeErrorCode.COUNT_MISMATCH,
        )


if __name__ == "__main__":
    unittest.main()
