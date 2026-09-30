"""Derivations kept by what they were read from (GH-365, ADR-008 phase 1a).

A State Record is parsed once per content digest and shared; a run's artifact
rows are kept under the run's stamp. Neither may outlive what it was read from:
a review, a new run or a changed workspace file is in the very next answer.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from archflow.project import watch
from archflow.project.record_kinds import STATE_RECORD
from archflow.project.repository import ProjectIntegrityError
from project_runtime.application.artifacts import list_artifacts
from project_runtime.binding import ProjectBinding
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings

from .support import PROJECT_ID, REFERENCE_RUN_ID, add_later_run, make_project, retain_rhino_receipt
from .test_conditional_reads import settle
from .test_design_history import DesignHistoryFixture

MODEL_BYTES = b"derivation-memo-3dm"
OTHER_BYTES = b"derivation-memo-3dm-changed"


class _ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.repository, _ = make_project(temporary)
        self.root = self.repository.layout.root
        self.settings = StudioSettings(project_dir=self.root)

    def binding(self) -> ProjectBinding:
        return ProjectBinding.open(self.settings)

    def state_record_ref(self, binding: ProjectBinding, run_id: str):
        return next(iter(binding.record_refs(run_id, kind=STATE_RECORD)))


class StateRecordTests(_ProjectCase):
    def test_one_digest_is_one_shared_record_and_another_digest_another(self) -> None:
        add_later_run(self.repository, run_id="run-later")
        settle(self.root)
        binding, other = self.binding(), self.binding()
        first_ref = self.state_record_ref(binding, REFERENCE_RUN_ID)
        later_ref = self.state_record_ref(binding, "run-later")
        self.assertNotEqual(first_ref.sha256, later_ref.sha256)

        record = binding.state_record(first_ref)
        self.assertIs(binding.state_record(first_ref), record)
        self.assertIs(other.state_record(first_ref), record, "parsed once per digest, for every binding")
        self.assertIsNot(binding.state_record(later_ref), record)
        self.assertEqual(binding.state_record(later_ref).run_id, "run-later")
        self.assertEqual(record.digest, record.digest)
        self.assertIs(record.dependency_edges(), record.dependency_edges())

    def test_the_shared_record_still_needs_its_file(self) -> None:
        settle(self.root)
        binding = self.binding()
        ref = self.state_record_ref(binding, REFERENCE_RUN_ID)
        binding.state_record(ref)

        self.repository.layout.resolve_record(ref).unlink()
        with self.assertRaisesRegex(ProjectIntegrityError, "cannot read project record"):
            binding.state_record(ref)


class ArtifactListingTests(_ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        retain_rhino_receipt(self.repository, self.repository.load_run(REFERENCE_RUN_ID),
                             stage_id="memo-stage", file_name="model.3dm", payload_bytes=MODEL_BYTES)

    def rows(self, binding: ProjectBinding, run_id: str | None = None) -> dict[str, bool]:
        return {(row.run_id, row.file_name): row.available for row in list_artifacts(binding, run_id=run_id).artifacts}

    def test_a_new_run_with_an_artifact_is_listed_at_once(self) -> None:
        settle(self.root)
        with TestClient(create_app(self.settings)) as client:
            before = client.get("/api/artifacts")
            self.assertEqual(before.status_code, 200, before.text)
            self.assertEqual([row["runId"] for row in before.json()["artifacts"]], [REFERENCE_RUN_ID])

            later = self.repository.create_run("run-later")
            retain_rhino_receipt(self.repository, later, stage_id="memo-stage", file_name="later.3dm",
                                 payload_bytes=b"later-3dm")
            after = client.get("/api/artifacts")

        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual({(row["runId"], row["fileName"], row["available"]) for row in after.json()["artifacts"]},
                         {(REFERENCE_RUN_ID, "model.3dm", True), ("run-later", "later.3dm", True)})

    def test_a_changed_workspace_file_flips_available(self) -> None:
        settle(self.root)
        binding = self.binding()
        self.assertEqual(self.rows(binding), {(REFERENCE_RUN_ID, "model.3dm"): True})
        self.assertEqual(self.rows(binding), {(REFERENCE_RUN_ID, "model.3dm"): True})
        model = self.repository.layout.run(REFERENCE_RUN_ID).workspaces / "cad-memo-stage" / "model.3dm"

        model.write_bytes(OTHER_BYTES)
        self.assertEqual(self.rows(binding), {(REFERENCE_RUN_ID, "model.3dm"): False})
        self.assertEqual(self.rows(binding, REFERENCE_RUN_ID), {(REFERENCE_RUN_ID, "model.3dm"): False})

        model.write_bytes(MODEL_BYTES)
        settle(self.root)
        self.assertEqual(self.rows(binding), {(REFERENCE_RUN_ID, "model.3dm"): True})
        model.unlink()
        self.assertEqual(self.rows(binding), {(REFERENCE_RUN_ID, "model.3dm"): False})


class ReadTokenTests(_ProjectCase):
    def test_a_read_token_is_read_never_walked_and_a_write_moves_it_at_once(self) -> None:
        binding = self.binding()
        self.addCleanup(binding.close)
        first = binding.read_token()  # the first read waits for the watch's first walk
        self.assertFalse(first.stable, "a project written just now has not settled")
        walked: list[str] = []
        original = watch._Tree._visit_one

        def visit(tree, *args):
            walked.append(threading.current_thread().name)
            return original(tree, *args)

        with patch.object(watch._Tree, "_visit_one", visit):
            for _ in range(50):
                again = binding.read_token()
                self.assertEqual((again.serial, again.fingerprint), (first.serial, first.fingerprint))
            self.repository.create_run("run-later")
            after = binding.read_token()
            self.assertGreater(after.serial, first.serial)
            self.assertFalse(after.stable)
            binding.layout_watch().sync()
            seen = binding.read_token()
        self.assertTrue(walked, "the watch never read the project")
        self.assertEqual({name for name in walked if not name.startswith("layout-watch:")}, set(),
                         "a reader's thread walked the project")
        self.assertEqual(seen.serial, after.serial)
        self.assertNotEqual(seen.fingerprint, first.fingerprint)


class DesignHistoryReviewTests(DesignHistoryFixture):
    def test_a_review_judgement_is_in_the_next_design_history(self) -> None:
        stage = self.initialize()
        settle(self.root / PROJECT_ID)
        before = self.history()
        candidate = next(row for row in before["candidates"] if row["candidateId"] == stage["candidateId"])
        self.assertIsNone(candidate["review"])

        response = self.client.post("/api/candidate-reviews", json={
            "projectId": PROJECT_ID, "subjectKind": "candidate", "subjectRef": stage["candidateId"],
            "action": "endorse", "reason": "develop this one",
        })
        self.assertEqual(response.status_code, 201, response.text)
        after = self.history()

        reviewed = next(row for row in after["candidates"] if row["candidateId"] == stage["candidateId"])
        self.assertEqual(reviewed["review"], response.json())
        self.assertEqual(after["stages"], before["stages"])


if __name__ == "__main__":
    unittest.main()
