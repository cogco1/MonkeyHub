"""Current work, bounded recovery and conservative P036 collection survive reopen."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from archflow.project.archive import restore_project_archive, write_project_archive
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_BOARD_SCENE, STUDIO_CANDIDATE_DELTA, STUDIO_LOCAL_DRAFT, STUDIO_MODEL_ASSET, STUDIO_SOURCE_DOCUMENT
from archflow.project.repository import FilesystemProjectRepository, ProjectIntegrityError, StaleWorkingDraft


OLD = "2026-01-01T00:00:00+00:00"
NOW = "2026-01-03T00:00:00+00:00"


class WorkingDraftRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = FilesystemProjectRepository.initialize(self.root / "project", project_id="building", initial_state={})

    def create(self, name, *, automatic=True, label=None, updated=OLD):
        run = self.repo.create_run(name)
        value, revision = self.repo.read_working_draft()
        value["runs"][name] = {"automatic": automatic, "label": label, "updatedAt": updated,
                               "sourceStageRef": None, "branchId": None}
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        return run

    def put(self, run, kind, payload, area=PersistenceArea.RUN_RECORD):
        return self.repo.put_json(run=run, destination=PersistenceDestination(area, run_id=run.run_id),
                                  record_kind=kind, payload=payload)

    def test_legacy_read_is_nonmutating_and_compare_swap_rejects_second_window(self):
        value, revision = self.repo.read_working_draft()
        self.assertIsNone(revision)
        self.assertFalse(self.repo.layout.working_draft.exists())
        self.create("a")
        with self.assertRaises(StaleWorkingDraft):
            self.repo.compare_and_swap_working_draft(expected_revision=None, value=value)
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.read_working_draft(), self.repo.read_working_draft())

    def test_gc_keeps_current_saved_legacy_recent_ancestry_board_and_documents(self):
        for name in ("current", "parent", "combined", "saved", "expired", "board-page", "document", "external"):
            self.create(name, label="My version" if name == "saved" else None)
        self.repo.create_run("legacy")
        self.create("recent", updated="2026-01-02T12:00:00+00:00")
        self.put(self.repo.load_run("current"), STUDIO_CANDIDATE_DELTA,
                 {"schema": "StudioCandidateDelta@1", "source_run_ref": self.repo.load_run("parent").to_dict(),
                  "combined_candidate_ids": ["combined"]})
        board = self.repo.create_run("studio-board")
        self.put(board, STUDIO_BOARD_SCENE, {"schema": "StudioBoardScene@1", "projectId": "building",
            "elements": [{"customData": {"sourceDocument": {"runId": "board-page"}}}]})
        self.put(self.repo.load_run("document"), STUDIO_SOURCE_DOCUMENT,
                 {"schema": "StudioSourceDocument@1", "project_id": "building"})
        value, revision = self.repo.read_working_draft()
        value["current"] = "current"
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        self.assertEqual(self.repo.prune_working_draft(now=NOW, protected_run_ids=("external",)), ("expired",))
        self.assertFalse(self.repo.layout.run("expired").root.exists())
        for name in ("current", "parent", "combined", "saved", "board-page", "document", "legacy", "recent", "external"):
            self.assertEqual(self.repo.load_run(name).run_id, name)
        self.assertNotIn("expired", FilesystemProjectRepository.open(self.repo.layout.root).read_working_draft()[0]["runs"])

    def test_original_model_imports_survive_expired_draft_collection_and_reopen(self):
        for name, schema in (("original", "StudioModelAsset@1"), ("legacy-original", "StudioExternalModelAsset@1")):
            run = self.create(name)
            self.put(run, STUDIO_MODEL_ASSET, {"schema": schema, "origin": "uploaded",
                "representation": "external", "modelSource": None, "projectId": "building"})
        self.create("expired")
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.prune_working_draft(now=NOW), ("expired",))
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        for name in ("original", "legacy-original"):
            self.assertEqual(reopened.load_run(name).run_id, name)
        reopened.verify()

    def test_current_local_snapshot_protects_exact_source_indefinitely_and_old_snapshots_expire(self):
        self.create("source")
        self.create("obsolete")
        drafts = self.repo.create_run("studio-working-draft")
        def snapshot(source):
            return self.put(drafts, STUDIO_LOCAL_DRAFT, {"schema": "StudioLocalDraft@1", "projectId": "building",
                "updatedAt": OLD, "draft": {"source": {"projectId": "building", "sourceRunId": source,
                "sourceStageRef": None, "stateDigest": "a" * 64}, "commands": [{"id": source}], "attempt": None}},
                PersistenceArea.RUN_RECOVERY)
        old, current = snapshot("obsolete"), snapshot("source")
        value, revision = self.repo.read_working_draft()
        value["localDraftRef"] = current.to_dict()
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        self.assertEqual(self.repo.prune_working_draft(now=NOW), ("obsolete",))
        self.assertTrue((self.repo.layout.root / current.relative_path).exists())
        self.assertFalse((self.repo.layout.root / old.relative_path).exists())
        self.repo.verify()

    def test_active_and_interrupted_inputs_are_pinned_and_bad_graph_refuses_deletion(self):
        self.create("source")
        self.create("combined")
        self.repo.protect_working_run("executing", "source", dependencies=("combined",))
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.prune_working_draft(now=NOW), ())
        reopened.release_working_run("executing")
        broken = self.repo.layout.run("source").records / "corrupt.json"
        broken.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ProjectIntegrityError):
            reopened.prune_working_draft(now=NOW)
        self.assertTrue(self.repo.layout.run("source").root.exists())

    def test_archive_restores_current_saved_and_local_commands_but_candidate_sync_does_not_override_local(self):
        source = self.create("source", label="Keep me")
        drafts = self.repo.create_run("studio-working-draft")
        ref = self.put(drafts, STUDIO_LOCAL_DRAFT, {"schema": "StudioLocalDraft@1", "projectId": "building",
            "updatedAt": OLD, "draft": {"source": {"projectId": "building", "sourceRunId": "source",
            "sourceStageRef": None, "stateDigest": "a" * 64}, "commands": [{"id": "move"}],
            "attempt": {"pending": {"attempt": {"requestId": "stable-request"}}}}}, PersistenceArea.RUN_RECOVERY)
        value, revision = self.repo.read_working_draft()
        value.update(current="source", localDraftRef=ref.to_dict())
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        archive = self.root / "project.zip"
        write_project_archive(self.repo, archive)
        restore_project_archive(self.root / "restore" / "building", archive)
        reopened = FilesystemProjectRepository.open(self.root / "restore" / "building")
        self.assertEqual(reopened.read_working_draft()[0], value)
        self.assertEqual(reopened.load_json(ref), self.repo.load_json(ref))
        self.assertIn("design/working.json", reopened.verify().reachable_paths)
        transfer = self.repo.export_transfer(run_id=source.run_id)
        self.assertNotIn("design/working.json", [row["path"] for row in transfer["files"]])
        self.assertNotIn(ref.relative_path, [row["path"] for row in transfer["files"]])
        reopened.import_candidate_transfer(transfer)
        self.assertEqual(reopened.read_working_draft()[0], value)

    def test_cleanup_waits_for_cross_process_source_validation_and_reference_write(self):
        source = self.create("source")
        board = self.repo.create_run("studio-board")
        code = """import sys
from archflow.project.repository import FilesystemProjectRepository
r=FilesystemProjectRepository.open(sys.argv[1])
print('ready',flush=True)
print(r.prune_working_draft(now=sys.argv[2]),flush=True)
"""
        with self.repo.working_draft_guard():
            self.repo.load_run(source.run_id)
            child = subprocess.Popen([sys.executable, "-u", "-c", code, str(self.repo.layout.root), NOW],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(subprocess.TimeoutExpired):
                child.wait(timeout=0.2)
            self.put(board, STUDIO_BOARD_SCENE, {"schema": "StudioBoardScene@1", "source": {"runId": "source"}})
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "()")
        self.assertTrue(self.repo.layout.run("source").root.exists())

    def test_cross_process_write_cannot_resurrect_a_run_after_cleanup(self):
        self.create("expired")
        code = """import sys
from archflow.project.repository import FilesystemProjectRepository,ProjectRepositoryError
from archflow.project.ports import PersistenceArea,PersistenceDestination
from archflow.project.record_kinds import STUDIO_BOARD_SCENE
r=FilesystemProjectRepository.open(sys.argv[1]); run=r.load_run('expired')
print('read',flush=True)
try:
 r.put_json(run=run,destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id='expired'),record_kind=STUDIO_BOARD_SCENE,payload={'schema':'StudioBoardScene@1'})
 print('saved')
except ProjectRepositoryError:
 print('refused')
"""
        with self.repo.working_draft_guard():
            child = subprocess.Popen([sys.executable, "-u", "-c", code, str(self.repo.layout.root)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), "read")
            self.assertEqual(self.repo.prune_working_draft(now=NOW), ("expired",))
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "refused")
        self.assertFalse(self.repo.layout.run("expired").root.exists())
