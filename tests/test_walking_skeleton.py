from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from archflow.adapters import FakeVoxelAdapter
from archflow.commit import InMemoryStateStore
from archflow.runtime import FakeArchitect, RunStatus, initial_state, run_once
from archflow.runtime.walking_skeleton import build_parser
from archflow.workspace import WorkspaceManager


class WalkingSkeletonTests(unittest.TestCase):
    def test_cli_requires_explicit_project_or_disposable_workspace(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["A test building"])

        args = build_parser().parse_args(
            [
                "A test building",
                "--workspace-root",
                "probes/test_case/runs/run-001/workspaces",
            ]
        )
        self.assertEqual(
            args.workspace_root,
            Path("probes/test_case/runs/run-001/workspaces"),
        )

    def test_accepted_run_commits_and_discharges_goal(self) -> None:
        state = initial_state("A test building", run_id="run-accepted")
        store = InMemoryStateStore(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            outcome = run_once(
                store,
                WorkspaceManager(Path(temp_dir)),
                FakeArchitect(FakeVoxelAdapter()),
            )
            self.assertEqual(outcome.status, RunStatus.COMMITTED)
            self.assertEqual(store.read().ref.version, 1)
            self.assertFalse(store.read().open_obligations)
            artifact_uri = store.read().artifacts[0].uri
            self.assertTrue(Path(artifact_uri.removeprefix("file:///")).exists())

    def test_rejected_run_preserves_canonical_state(self) -> None:
        state = initial_state("A test building", run_id="run-rejected")
        store = InMemoryStateStore(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            outcome = run_once(
                store,
                WorkspaceManager(Path(temp_dir)),
                FakeArchitect(
                    FakeVoxelAdapter(),
                    omit_claims=frozenset({"size.within_target"}),
                ),
            )
            self.assertEqual(outcome.status, RunStatus.REJECTED)
            self.assertEqual(store.read(), state)
            codes = {finding.code for finding in outcome.validation.findings}
            self.assertIn("goal.required_claim_missing", codes)


if __name__ == "__main__":
    unittest.main()
