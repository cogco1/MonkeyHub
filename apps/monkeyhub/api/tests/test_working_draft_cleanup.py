"""Hub maintenance accounts for operation admission and retained chat sources."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

from archflow.project.repository import FilesystemProjectRepository
from monkeyhub_api.runtime import OperationManager, ProjectRuntimeManager


class WorkingDraftCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = FilesystemProjectRepository.initialize(Path(temporary.name) / "project", project_id="building", initial_state={})
        self.operations = OperationManager("building")
        self.runtime = SimpleNamespace(project_id="building", project_dir=str(self.repo.layout.root), operations=self.operations,
            binding=SimpleNamespace(repository=self.repo, run_ids=lambda: tuple(path.parent.name for path in self.repo.layout.runs.glob("*/run.json"))))
        self.sessions = {}
        self.chats = SimpleNamespace(list=lambda project_id, archived=False: [row for row in self.sessions.values() if row.archived == archived],
                                     get=lambda session_id: self.sessions[session_id])
        self.manager = ProjectRuntimeManager(SimpleNamespace(), self.chats)

    def automatic(self, name):
        self.repo.create_run(name)
        value, revision = self.repo.read_working_draft()
        value["runs"][name] = {"updatedAt": "2020-01-01T00:00:00+00:00", "sourceStageRef": None,
                               "branchId": None, "label": None, "automatic": True}
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)

    def test_admitted_proposal_without_enriched_source_defers_cleanup_and_interrupted_source_is_pinned(self):
        self.automatic("source")
        self.automatic("other")
        admission, fresh = self.operations.admit(str(uuid4()), "POST", "/api/proposals/p1/candidate", b"",
                                                 retained=None, source="studio", session_id=None)
        self.assertTrue(fresh)
        self.assertIsNone(admission.record.sourceRunId)
        self.assertEqual(self.manager._clean_working_draft(self.runtime), ())
        self.operations.interrupted(admission, "lost before proposal read")
        self.assertEqual(self.manager._clean_working_draft(self.runtime), ())
        self.operations.bind_proposal(admission, {"sourceRunId": "source", "baseStateDigest": "a" * 64, "recordDigest": "b" * 64}, 0)
        self.operations.interrupted(admission, "lost after source binding")
        self.assertEqual(self.manager._clean_working_draft(self.runtime), ("other",))
        self.assertTrue(self.repo.layout.run("source").root.exists())

    def test_archived_chat_document_and_candidate_refs_protect_exact_runs(self):
        for name in ("candidate", "document", "expired"):
            self.automatic(name)
        self.sessions["conversation"] = SimpleNamespace(id="conversation", archived=True, projectDir=str(self.repo.layout.root),
            messages=[SimpleNamespace(candidateId="candidate", documents=[SimpleNamespace(runId="document")])])
        self.assertEqual(self.manager._clean_working_draft(self.runtime), ("expired",))
        self.assertTrue(self.repo.layout.run("candidate").root.exists())
        self.assertTrue(self.repo.layout.run("document").root.exists())
