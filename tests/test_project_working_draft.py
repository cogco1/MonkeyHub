"""Current work and bounded local recovery survive reopen; P036 cleanup never removes a run."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from archflow.project.archive import restore_project_archive, write_project_archive
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_LOCAL_DRAFT, STUDIO_MODEL_ASSET
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

    def snapshot(self, drafts, source, *, project="building", updated=OLD):
        return self.put(drafts, STUDIO_LOCAL_DRAFT, {"schema": "StudioLocalDraft@1", "projectId": project,
            "updatedAt": updated, "draft": {"source": {"projectId": project, "sourceRunId": source,
            "sourceStageRef": None, "stateDigest": "a" * 64}, "commands": [{"id": source}], "attempt": None}},
            PersistenceArea.RUN_RECOVERY)

    def test_legacy_read_is_nonmutating_and_compare_swap_rejects_second_window(self):
        value, revision = self.repo.read_working_draft()
        self.assertIsNone(revision)
        self.assertFalse(self.repo.layout.working_draft.exists())
        self.create("a")
        with self.assertRaises(StaleWorkingDraft):
            self.repo.compare_and_swap_working_draft(expected_revision=None, value=value)
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.read_working_draft(), self.repo.read_working_draft())

    def test_protect_and_release_do_not_move_the_position_revision(self):
        # GH-293: a candidate's execution is a ledger beside the position. Its
        # start and its end leave the revision that position writers compare, so
        # a write that read the position just before either one still lands.
        self.create("source")
        value, revision = self.repo.read_working_draft()
        self.repo.protect_working_run("running", "source")
        protected, unchanged = self.repo.read_working_draft()
        self.assertEqual((protected["active"], unchanged), ({"running": ["source"]}, revision))
        value["current"] = "source"
        written, moved = self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        self.assertNotEqual(moved, revision, "a position write still moves the revision")
        self.assertEqual(written["active"], {"running": ["source"]})
        self.repo.release_working_run("running")
        released, still = self.repo.read_working_draft()
        self.assertEqual((released["current"], released["active"], still), ("source", {}, moved))
        value["current"] = None
        cleared, _ = self.repo.compare_and_swap_working_draft(expected_revision=moved, value=value)
        self.assertEqual((cleared["current"], cleared["active"]), (None, {}))

    def test_a_position_write_never_drops_an_active_execution(self):
        # A position writer's value may carry a ledger read before the run
        # started. The execution it never saw stays recorded; the ledger stays in
        # the same retained document, in the same bytes, and reopens unchanged.
        self.create("source")
        value, _ = self.repo.read_working_draft()
        self.repo.protect_working_run("running", "source")
        value["current"] = "source"
        self.repo.compare_and_swap_working_draft(expected_revision=self.repo.read_working_draft()[1], value=value)
        data = self.repo.layout.working_draft.read_bytes()
        kept = json.loads(data)
        self.assertEqual((kept["current"], kept["active"]), ("source", {"running": ["source"]}))
        self.assertEqual(data, (json.dumps(kept, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.read_working_draft(), self.repo.read_working_draft())

    def test_cleanup_keeps_expired_unreferenced_automatic_candidates_and_their_models(self):
        # GH-234 Q3: these automatic candidates are days old and nothing refers
        # to them, yet they stay until the architect explicitly rejects or
        # archives them. No run leaves the project on a timer.
        self.create("expired")
        self.put(self.create("generated-model"), STUDIO_MODEL_ASSET, {"schema": "StudioModelAsset@1",
            "origin": "generated", "representation": "external", "modelSource": None, "projectId": "building"})
        self.create("saved", label="My version")
        self.repo.create_run("legacy")
        working = self.repo.read_working_draft()
        self.assertEqual(self.repo.prune_working_draft(now=NOW), ())
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        for name in ("expired", "generated-model", "saved", "legacy"):
            self.assertEqual(reopened.load_run(name).run_id, name)
        self.assertEqual(reopened.read_working_draft(), working)
        reopened.verify()

    def test_only_superseded_local_snapshots_expire_and_the_current_one_is_kept_indefinitely(self):
        self.create("source")
        self.create("obsolete")
        drafts = self.repo.create_run("studio-working-draft")
        old = self.snapshot(drafts, "obsolete")
        recent = self.snapshot(drafts, "obsolete", updated="2026-01-02T12:00:00+00:00")
        current = self.snapshot(drafts, "source")
        value, revision = self.repo.read_working_draft()
        value["localDraftRef"] = current.to_dict()
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        self.assertEqual(self.repo.prune_working_draft(now=NOW), (old.relative_path,))
        self.assertFalse((self.repo.layout.root / old.relative_path).exists())
        for kept in (recent, current):
            self.assertTrue((self.repo.layout.root / kept.relative_path).exists())
        # The expired snapshot's source is an expired, unreferenced automatic
        # candidate as well, and it stays.
        for name in ("source", "obsolete"):
            self.assertEqual(self.repo.load_run(name).run_id, name)
        self.repo.verify()

    def test_an_inconsistent_local_snapshot_refuses_expiry_and_removes_nothing(self):
        self.create("expired")
        drafts = self.repo.create_run("studio-working-draft")
        snapshots = (self.snapshot(drafts, "expired"), self.snapshot(drafts, "expired", project="elsewhere"))
        with self.assertRaises(ProjectIntegrityError):
            self.repo.prune_working_draft(now=NOW)
        for ref in snapshots:
            self.assertTrue((self.repo.layout.root / ref.relative_path).exists())
        self.assertEqual(self.repo.load_run("expired").run_id, "expired")

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

    def test_cleanup_waits_for_the_cross_process_guard_and_keeps_the_expired_candidate(self):
        source = self.create("source")
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
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "()")
        self.assertTrue(self.repo.layout.run("source").root.exists())

    def test_a_cross_process_write_still_reaches_an_expired_candidate_after_cleanup(self):
        self.create("expired")
        code = """import sys
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea,PersistenceDestination
from archflow.project.record_kinds import STUDIO_BOARD_SCENE
r=FilesystemProjectRepository.open(sys.argv[1]); run=r.load_run('expired')
print('read',flush=True)
r.put_json(run=run,destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id='expired'),record_kind=STUDIO_BOARD_SCENE,payload={'schema':'StudioBoardScene@1'})
print('saved')
"""
        with self.repo.working_draft_guard():
            child = subprocess.Popen([sys.executable, "-u", "-c", code, str(self.repo.layout.root)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), "read")
            self.assertEqual(self.repo.prune_working_draft(now=NOW), ())
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "saved")
        self.assertTrue(self.repo.layout.run("expired").root.exists())
