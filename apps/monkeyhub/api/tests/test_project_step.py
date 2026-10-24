"""The project list says which step each project's design stands on, in the words the history page uses.

A step is named by what a person or the AI called it, else by the Stage accepted on it, else by the words its change
was asked in; a project nothing names says nothing. The answer is kept until the files it was read from move.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.dev import source_roots  # noqa: E402 - this checkout's tools, found above

source_roots.put_first(ROOT)

from archflow.project.ports import PersistenceArea, PersistenceDestination  # noqa: E402
from archflow.project.record_kinds import STUDIO_CANDIDATE_DELTA  # noqa: E402
from archflow.project.repository import FilesystemProjectRepository  # noqa: E402

from monkeyhub_api import projects  # noqa: E402

PROJECT_ID = "step-demo"
RUN_ID = "hub-cand-0001"


class CurrentStepTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub step ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / PROJECT_ID
        self.repository = FilesystemProjectRepository.initialize(
            self.root, project_id=PROJECT_ID, initial_state={"project_id": PROJECT_ID, "version": 0})
        projects._STEPS.clear()

    def stand_on(self, label: str | None, request: str | None = None) -> None:
        """Put the working position on one run, named or not, whose change was asked in ``request``."""

        run = self.repository.create_run(RUN_ID)
        if request is not None:
            self.repository.put_json(
                run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=RUN_ID),
                record_kind=STUDIO_CANDIDATE_DELTA,
                payload={"schema": "StudioCandidateDelta@1", "project_id": PROJECT_ID, "run_id": RUN_ID, "request": request},
            )
        value, revision = self.repository.read_working_draft()
        value["current"] = RUN_ID
        value["runs"][RUN_ID] = {"updatedAt": "2026-10-24T20:00:00Z", "sourceStageRef": None, "branchId": None,
                                 "label": label, "automatic": False}
        self.repository.compare_and_swap_working_draft(expected_revision=revision, value=value, ledger=True)

    def test_a_project_whose_head_never_moved_has_no_step(self):
        self.assertIsNone(projects.current_step(str(self.root)))

    def test_the_name_somebody_gave_the_step_is_its_name(self):
        self.stand_on("  V16   去掉斜撑 ", request="remove the braces")
        self.assertEqual(projects.current_step(str(self.root)), "V16 去掉斜撑")

    def test_an_unnamed_step_is_named_by_the_words_it_was_asked_in_and_a_long_one_is_cut(self):
        self.stand_on(None, request="把楼板改成只留一层胶合板，" * 6 + "再把入口加宽一点点")
        step = projects.current_step(str(self.root))
        self.assertTrue(step.startswith("把楼板改成只留一层胶合板"))
        self.assertEqual(len(step), projects.STEP_NAME_LIMIT)
        self.assertTrue(step.endswith("…"))

    def test_a_step_nothing_names_says_nothing(self):
        self.stand_on(None)
        self.assertIsNone(projects.current_step(str(self.root)))

    def test_an_unreadable_project_says_nothing_and_an_unchanged_one_is_not_read_again(self):
        self.assertIsNone(projects.current_step(str(self.root.parent / "missing")))
        self.stand_on("V16")
        with patch.object(projects, "_read_step", wraps=projects._read_step) as reads:
            self.assertEqual(projects.current_step(str(self.root)), "V16")
            self.assertEqual(projects.current_step(str(self.root)), "V16")
            self.assertEqual(reads.call_count, 1, "the second look finds the files as they were")


if __name__ == "__main__":
    unittest.main()
