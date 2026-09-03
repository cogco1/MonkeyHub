from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from archive.archflow.adapters.minecraft_mcp import MinecraftMcpAdapter, MinecraftMcpConfig, MinecraftMcpFailure
from archive.archflow.adapters.mcp_stdio import McpClientError
from archflow.project.refs import ProjectVersionRef
from archive.archflow.runtime.walking_skeleton import initial_state
from archive.archflow.runtime.world_recovery import (
    WorldMutationStatus,
    WorldMutationTrace,
)
from archive.archflow.workspace.manager import WorkspaceManager


FIXTURE = Path(__file__).with_name("fake_minecraft_mcp.py").resolve()
PLAN = {
    "version": 2,
    "summary": "A five by five test floor",
    "cuboids": [
        {
            "name": "floor",
            "block": "minecraft:stone",
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 4, "y": 0, "z": 4},
        }
    ],
}


def adapter(
    mode: str = "success",
    *,
    allow_world_write: bool = False,
    allow_compensation: bool = False,
) -> MinecraftMcpAdapter:
    return MinecraftMcpAdapter(
        MinecraftMcpConfig(
            command=(sys.executable, str(FIXTURE), mode),
            timeout_seconds=2,
            allow_world_write=allow_world_write,
            allow_compensation=allow_compensation,
        )
    )


def _result(payload):
    return {"structuredContent": payload}


class _ScriptedClient:
    def __init__(self, *, capture_error=None, undo_error=None) -> None:
        self.capture_error = capture_error
        self.undo_error = undo_error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def initialize(self):
        return SimpleNamespace(
            name="scripted-minecraft",
            version="1",
            protocol_version="2025-03-26",
        )

    def list_tools(self):
        return (
            "minecraft_session",
            "minecraft_preview_build_plan",
            "minecraft_execute_build_plan",
            "minecraft_capture_view",
            "minecraft_undo_last_batch",
        )

    def call_tool(self, name, arguments):
        if name == "minecraft_session":
            return _result(
                {
                    "worldId": "disposable-world",
                    "dimensionId": "minecraft:overworld",
                    "player": "ArchFlowBot",
                }
            )
        if name == "minecraft_preview_build_plan":
            return _result(
                {
                    "planId": "plan-scripted-001",
                    "sourcePlan": arguments,
                    "issues": [],
                }
            )
        if name == "minecraft_execute_build_plan":
            return _result(
                {
                    "planId": "plan-scripted-001",
                    "changedBlocks": 25,
                    "undoAvailable": True,
                    "undoToken": "undo-scripted-001",
                }
            )
        if name == "minecraft_capture_view":
            raise self.capture_error or AssertionError(
                "capture was not expected"
            )
        if name == "minecraft_undo_last_batch":
            if self.undo_error is not None:
                raise self.undo_error
            self.assert_exact_undo(arguments)
            return _result({"undone": True, "changedBlocks": 25})
        raise AssertionError(name)

    @staticmethod
    def assert_exact_undo(arguments):
        if arguments != {"undoToken": "undo-scripted-001"}:
            raise AssertionError("compensation did not bind exact undo token")


def _exact_state(project_id: str):
    state = initial_state("A test building", run_id=project_id)
    return replace(
        state,
        ref=ProjectVersionRef(
            project_id=project_id,
            version=0,
            state_sha256="a" * 64,
        ),
    )


class MinecraftMcpAdapterTests(unittest.TestCase):
    def test_preview_is_artifact_not_usability_approval(self) -> None:
        state = initial_state("A usable test building", run_id="run-preview")
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            artifact = adapter().preview(state, workspace, PLAN)
            payload = json.loads(
                (workspace.root / "minecraft-voxel-artifact.json").read_text()
            )

        self.assertEqual(payload["schema"], "MinecraftVoxelArtifact@1")
        self.assertTrue(payload["voxel_summary"]["loadable"])
        self.assertTrue(payload["voxel_summary"]["previewed"])
        self.assertFalse(payload["voxel_summary"]["executed"])
        self.assertNotIn("usable", payload["voxel_summary"])
        self.assertEqual(payload["base_state"]["version"], state.ref.version)
        self.assertEqual(len(artifact.sha256), 64)

    def test_build_executes_exact_preview_and_saves_visual_evidence(self) -> None:
        state = initial_state("A usable test building", run_id="run-build")
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            before = state
            adapter(allow_world_write=True).build(state, workspace, PLAN)
            payload = json.loads(
                (workspace.root / "minecraft-voxel-artifact.json").read_text()
            )
            mutation = WorldMutationTrace.from_dict(
                json.loads(
                    (
                        workspace.root
                        / "minecraft-mutation-receipt.json"
                    ).read_text()
                )
            )
            self.assertTrue((workspace.root / "minecraft-view.png").is_file())

        self.assertIs(state, before)
        self.assertEqual(payload["preview"]["planId"], "plan-archflow-001")
        self.assertEqual(payload["execution"]["planId"], "plan-archflow-001")
        self.assertTrue(payload["voxel_summary"]["executed"])
        self.assertEqual(
            mutation.status,
            WorldMutationStatus.CANDIDATE_EVIDENCE_READY,
        )
        self.assertEqual(
            tuple(item.phase for item in mutation.phases),
            (
                "prepare",
                "execute",
                "execute",
                "observe",
                "observe",
                "validate",
                "finalize",
            ),
        )
        self.assertFalse(
            payload["architectural_usability_proven"]
        )
        self.assertFalse(payload["canonical_write_authority"])

    def test_world_write_requires_explicit_authority(self) -> None:
        state = initial_state("A test building", run_id="run-no-write")
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            with self.assertRaises(MinecraftMcpFailure) as caught:
                adapter().build(state, workspace, PLAN)
            receipt = json.loads(caught.exception.receipt_path.read_text())

        self.assertEqual(caught.exception.code, "WORLD_WRITE_DISABLED")
        self.assertFalse(receipt["canonical_state_mutated"])
        self.assertFalse(receipt["world_may_have_changed"])

    def test_external_failure_is_bounded_and_state_stays_unchanged(self) -> None:
        state = initial_state("A test building", run_id="run-failure")
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            before = state
            with self.assertRaises(MinecraftMcpFailure) as caught:
                adapter("preview-failure").preview(state, workspace, PLAN)
            receipt_path = caught.exception.receipt_path
            receipt = json.loads(receipt_path.read_text())
            receipt_size = receipt_path.stat().st_size

        self.assertIs(state, before)
        self.assertEqual(caught.exception.code, "BRIDGE_UNAVAILABLE")
        self.assertLess(receipt_size, 10_000)
        self.assertFalse(receipt["canonical_state_mutated"])

    def test_missing_transaction_tool_fails_before_world_write(self) -> None:
        state = initial_state("A test building", run_id="run-missing-tool")
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            with self.assertRaises(MinecraftMcpFailure) as caught:
                adapter(
                    "missing-execute",
                    allow_world_write=True,
                ).build(state, workspace, PLAN)
            receipt = json.loads(caught.exception.receipt_path.read_text())

        self.assertEqual(caught.exception.code, "MCP_CAPABILITY_MISSING")
        self.assertFalse(receipt["world_may_have_changed"])

    def test_capture_failure_compensates_with_exact_token(self) -> None:
        state = _exact_state("compensation-success")
        build_client = _ScriptedClient(
            capture_error=McpClientError("capture failed")
        )
        compensation_client = _ScriptedClient()
        boundary = adapter(
            allow_world_write=True,
            allow_compensation=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            with patch.object(
                boundary,
                "_client",
                side_effect=(build_client, compensation_client),
            ):
                with self.assertRaises(MinecraftMcpFailure) as caught:
                    boundary.build(state, workspace, PLAN)
            failure = json.loads(caught.exception.receipt_path.read_text())
            trace = WorldMutationTrace.from_dict(
                json.loads(
                    (
                        workspace.root
                        / "minecraft-mutation-receipt.json"
                    ).read_text()
                )
            )

        self.assertEqual(
            trace.status,
            WorldMutationStatus.COMPENSATED,
        )
        self.assertIn(
            ("compensate", "acknowledged"),
            {(item.phase, item.outcome) for item in trace.phases},
        )
        self.assertEqual(failure["world_mutation_status"], "compensated")
        self.assertTrue(failure["world_may_have_changed"])
        self.assertFalse(
            next(
                item
                for item in trace.phases
                if item.phase == "compensate"
            ).evidence["atomic_rollback_claimed"]
        )

    def test_capture_failure_and_failed_compensation_remain_distinct(
        self,
    ) -> None:
        state = _exact_state("compensation-failure")
        build_client = _ScriptedClient(
            capture_error=McpClientError("capture failed")
        )
        compensation_client = _ScriptedClient(
            undo_error=McpClientError("undo failed")
        )
        boundary = adapter(
            allow_world_write=True,
            allow_compensation=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            with patch.object(
                boundary,
                "_client",
                side_effect=(build_client, compensation_client),
            ):
                with self.assertRaises(MinecraftMcpFailure):
                    boundary.build(state, workspace, PLAN)
            trace = WorldMutationTrace.from_dict(
                json.loads(
                    (
                        workspace.root
                        / "minecraft-mutation-receipt.json"
                    ).read_text()
                )
            )

        self.assertEqual(
            trace.status,
            WorldMutationStatus.COMPENSATION_FAILED,
        )
        self.assertIn(
            ("compensate", "failed"),
            {(item.phase, item.outcome) for item in trace.phases},
        )


if __name__ == "__main__":
    unittest.main()
