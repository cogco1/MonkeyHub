"""Explicit opt-in read-only live observation scenario smoke."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from archive.archflow.adapters.minecraft_volume import (
    MinecraftVolumeAdapter,
    MinecraftVolumeBounds,
    MinecraftVolumeHttpTransport,
)
from archive.archflow.adapters.voxel_observation import VoxelObservationExtractor
from archflow.state.model import ArtifactRef, StateRef
from archive.archflow.compilers.voxel_program import compile_building_program
from archive.archflow.validation.use_scenarios import (
    UseScenarioStatus,
    evaluate_use_scenarios,
)
from archive.archflow.validation.usability import UseZoneEvidence
from archive.archflow.workspace.manager import WorkspaceRef


class LiveUseScenarioObservationSmoke(unittest.TestCase):
    def test_explicit_read_only_observation(self) -> None:
        raw_config = os.environ.get(
            "ARCHFLOW_LIVE_USE_SCENARIO_CONFIG_JSON"
        )
        if raw_config is None:
            self.skipTest(
                "set ARCHFLOW_LIVE_USE_SCENARIO_CONFIG_JSON after a "
                "read-only Minecraft scan is captured"
            )
        config = json.loads(raw_config)
        required = {
            "scan_path",
            "artifact_id",
            "artifact_sha256",
            "workspace_id",
            "run_id",
            "version",
            "program",
            "use_zones",
        }
        self.assertIn(set(config), (required, required | {"live_capture"}))
        scan_path = Path(config["scan_path"]).resolve()
        base = StateRef(config["run_id"], config["version"])
        workspace = WorkspaceRef(
            config["workspace_id"],
            base,
            scan_path.parent,
        )
        artifact = ArtifactRef(
            artifact_id=config["artifact_id"],
            uri=scan_path.as_uri(),
            media_type=(
                "application/vnd.archflow.minecraft-voxel+json"
            ),
            sha256=config["artifact_sha256"],
        )
        live_capture = config.get("live_capture")
        if live_capture is not None:
            self.assertEqual(
                set(live_capture),
                {"bridge_url", "bounds"},
            )
            raw_bounds = live_capture["bounds"]
            self.assertEqual(set(raw_bounds), {"min", "max"})
            bounds = MinecraftVolumeBounds(
                tuple(raw_bounds["min"]),
                tuple(raw_bounds["max"]),
            )
            capture = MinecraftVolumeAdapter().capture(
                source_artifact=artifact,
                base_state=base,
                workspace=workspace,
                bounds=bounds,
                transport=MinecraftVolumeHttpTransport(
                    bridge_url=live_capture["bridge_url"],
                    bearer_token=os.environ.get(
                        "ARCHFLOW_MINECRAFT_BRIDGE_TOKEN",
                        "",
                    ),
                ),
            )
            scan_path.parent.mkdir(parents=True, exist_ok=True)
            scan_path.write_bytes(capture.scan_bytes)
        observation = VoxelObservationExtractor().extract(
            source_artifact=artifact,
            base_state=base,
            workspace=workspace,
            scan_path=scan_path,
        )
        zones = tuple(
            UseZoneEvidence(
                space=item["space"],
                region_id=item["region_id"],
                evidence_refs=tuple(item["evidence_refs"]),
            )
            for item in config["use_zones"]
        )
        results, findings = evaluate_use_scenarios(
            compile_building_program(config["program"]),
            observation,
            use_zones=zones,
        )

        self.assertFalse(findings)
        self.assertTrue(
            all(
                result.status is UseScenarioStatus.PASSED
                for result in results
            )
        )
