"""Hub maintenance never removes a run; it only expires superseded local recovery."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_LOCAL_DRAFT
from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.runtime import ProjectRuntimeManager


OLD = "2020-01-01T00:00:00+00:00"


class WorkingDraftCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = FilesystemProjectRepository.initialize(Path(temporary.name) / "project", project_id="building", initial_state={})
        self.runtime = SimpleNamespace(binding=SimpleNamespace(repository=self.repo))
        self.manager = ProjectRuntimeManager(SimpleNamespace(), SimpleNamespace())

    def automatic(self, name):
        self.repo.create_run(name)
        value, revision = self.repo.read_working_draft()
        value["runs"][name] = {"updatedAt": OLD, "sourceStageRef": None,
                               "branchId": None, "label": None, "automatic": True}
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)

    def test_expired_unreferenced_automatic_candidates_stay_after_scheduled_cleanup(self):
        # GH-234 Q3: no operation is running and no conversation mentions these
        # candidates, yet they stay until the architect explicitly rejects or
        # archives them.
        for name in ("modeling-candidate", "agent-candidate"):
            self.automatic(name)
        working = self.repo.read_working_draft()
        self.assertEqual(self.manager._clean_working_draft(self.runtime), ())
        for name in ("modeling-candidate", "agent-candidate"):
            self.assertTrue(self.repo.layout.run(name).root.is_dir())
        self.assertEqual(self.repo.read_working_draft(), working)

    def test_scheduled_cleanup_expires_only_superseded_local_recovery(self):
        self.automatic("source")
        drafts = self.repo.create_run("studio-working-draft")

        def snapshot(commands):
            return self.repo.put_json(run=drafts, destination=PersistenceDestination(PersistenceArea.RUN_RECOVERY, run_id=drafts.run_id),
                record_kind=STUDIO_LOCAL_DRAFT, payload={"schema": "StudioLocalDraft@1", "projectId": "building", "updatedAt": OLD,
                "draft": {"source": {"projectId": "building", "sourceRunId": "source", "sourceStageRef": None, "stateDigest": "a" * 64},
                          "commands": commands, "attempt": None}})

        superseded, current = snapshot([{"id": "move"}]), snapshot([{"id": "move"}, {"id": "lift"}])
        value, revision = self.repo.read_working_draft()
        value["localDraftRef"] = current.to_dict()
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        self.assertEqual(self.manager._clean_working_draft(self.runtime), (superseded.relative_path,))
        self.assertFalse((self.repo.layout.root / superseded.relative_path).exists())
        self.assertTrue((self.repo.layout.root / current.relative_path).is_file())
        self.assertTrue(self.repo.layout.run("source").root.is_dir())
