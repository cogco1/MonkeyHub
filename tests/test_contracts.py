from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from archflow.capabilities import (
    CapabilityRegistry,
    CapabilitySpec,
    DuplicateCapabilityError,
)
from archflow.runtime import initial_state
from archflow.state import CanonicalState, GoalContract
from archflow.workspace import WorkspaceManager


class ContractTests(unittest.TestCase):
    def test_canonical_state_is_frozen_and_excludes_raw_history(self) -> None:
        state = initial_state("A test building", run_id="run-contract")
        with self.assertRaises(FrozenInstanceError):
            state.commitments = ("illegal",)  # type: ignore[misc]
        names = {item.name for item in fields(CanonicalState)}
        self.assertFalse(
            names
            & {
                "transcript",
                "tool_history",
                "tool_calls",
                "raw_history",
                "working_directory",
            }
        )

    def test_goal_contract_rejects_mutable_collections_and_conflicts(self) -> None:
        with self.assertRaises(TypeError):
            GoalContract(prompt="x", must=["a"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            GoalContract(prompt="x", must=("a",), forbid=("a",))

    def test_workspace_is_external_to_canonical_state(self) -> None:
        state = initial_state("A test building", run_id="run-workspace")
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = WorkspaceManager(Path(temp_dir)).fork(state)
            self.assertEqual(workspace.base, state.ref)
            self.assertTrue(workspace.root.is_dir())
            self.assertNotIn(str(workspace.root), repr(state))

    def test_capability_registry_is_open_metadata_not_a_schedule(self) -> None:
        registry = CapabilityRegistry()
        spec = CapabilitySpec(
            capability_id="voxel.build",
            kind="mcp",
            adapter="fake",
            description="Build a candidate artifact",
            side_effects=True,
        )
        registry.register(spec)
        self.assertEqual(registry.discover(kind="mcp"), (spec,))
        with self.assertRaises(DuplicateCapabilityError):
            registry.register(spec)


if __name__ == "__main__":
    unittest.main()
