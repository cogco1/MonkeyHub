"""Current work and bounded local recovery survive reopen; P036 cleanup never removes a run."""
from __future__ import annotations

import json
import os
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
# A second process imports the kernel this suite belongs to, not an installed one.
CHILD_ENV = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}


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
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=CHILD_ENV)
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
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=CHILD_ENV)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), "read")
            self.assertEqual(self.repo.prune_working_draft(now=NOW), ())
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), "saved")
        self.assertTrue(self.repo.layout.run("expired").root.exists())


class ActorWorkingDraftRepositoryTests(unittest.TestCase):
    setUp = WorkingDraftRepositoryTests.setUp
    create = WorkingDraftRepositoryTests.create
    put = WorkingDraftRepositoryTests.put
    snapshot = WorkingDraftRepositoryTests.snapshot

    def select_actor(self, actor, run_id, *, owner="studio:explicit-user-action", local=None):
        value, revision = self.repo.read_actor_working_draft(actor, owner_actor_id=owner)
        value.update(current=run_id, localDraftRef=local)
        return self.repo.compare_and_swap_actor_working_draft(
            actor, expected_revision=revision, value=value, owner_actor_id=owner)

    def test_legacy_position_is_only_the_configured_owner_and_reads_do_not_migrate(self):
        self.create("legacy")
        value, revision = self.repo.read_working_draft()
        value["current"] = "legacy"
        value["runs"]["legacy"]["branchId"] = "owner-line"
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)
        before = self.repo.layout.working_draft.read_bytes()
        owner, owner_revision = self.repo.read_actor_working_draft("alice", owner_actor_id="alice")
        other, other_revision = self.repo.read_actor_working_draft("bob", owner_actor_id="alice")
        self.assertEqual(owner["current"], "legacy")
        self.assertIsNotNone(owner_revision)
        self.assertIsNone(other["current"])
        self.assertIsNone(other_revision)
        self.assertEqual(self.repo.layout.working_draft.read_bytes(), before)
        self.select_actor("bob", "legacy", owner="alice")
        full, _ = self.repo.read_working_draft()
        self.assertEqual(full["schema"], "ProjectWorkingDraft@2")
        self.assertEqual(full["ownerActorId"], "alice")
        self.assertEqual(full["positions"]["alice"]["current"], "legacy")
        self.assertEqual(full["positions"]["alice"]["branchId"], "owner-line")
        self.assertEqual(self.repo.read_actor_working_draft("alice")[0]["runs"]["legacy"]["branchId"], "owner-line")
        self.assertEqual(self.repo.read_actor_working_draft("alice")[1], owner_revision)
        self.assertIsNone(self.repo.read_actor_working_draft("studio:explicit-user-action")[0]["current"])

    def test_other_actor_moves_do_not_stale_position_and_same_actor_moves_do(self):
        self.create("a")
        self.create("b")
        before, revision = self.repo.read_actor_working_draft("alice")
        before["current"] = "a"
        self.select_actor("bob", "b")
        self.assertEqual(self.repo.read_actor_working_draft("alice")[1], revision)
        written, first = self.repo.compare_and_swap_actor_working_draft(
            "alice", expected_revision=revision, value=before)
        self.assertEqual(written["current"], "a")
        self.select_actor("bob", "a")
        self.assertEqual(self.repo.read_actor_working_draft("alice")[1], first)
        with self.assertRaises(StaleWorkingDraft):
            self.repo.compare_and_swap_actor_working_draft("alice", expected_revision=revision, value=before)
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.read_actor_working_draft("alice"), (written, first))
        self.assertNotEqual(reopened.read_actor_working_draft("bob")[1], first)

    def test_first_nonowner_write_does_not_create_an_owner_position(self):
        owner, revision = self.repo.read_actor_working_draft("studio:explicit-user-action")
        self.select_actor("bob", None)
        self.assertEqual(self.repo.read_actor_working_draft("studio:explicit-user-action")[1], revision)
        self.repo.compare_and_swap_actor_working_draft(
            "studio:explicit-user-action", expected_revision=revision, value=owner)
        full, _ = self.repo.read_working_draft()
        self.assertEqual(set(full["positions"]), {"bob", "studio:explicit-user-action"})

    def test_actor_write_merges_new_runs_and_preserves_global_rename_and_execution(self):
        self.create("a")
        stale, revision = self.repo.read_actor_working_draft("alice")
        self.create("b", label="Shared name")
        full, global_revision = self.repo.read_working_draft()
        full["runs"]["a"]["label"] = "Renamed"
        self.repo.compare_and_swap_working_draft(expected_revision=global_revision, value=full)
        self.repo.protect_working_run("running", "a")
        self.repo.create_run("c")
        stale["runs"]["c"] = dict(stale["runs"]["a"])
        stale["current"] = "c"
        written, _ = self.repo.compare_and_swap_actor_working_draft(
            "alice", expected_revision=revision, value=stale)
        self.assertEqual(set(written["runs"]), {"a", "b", "c"})
        self.assertEqual(written["runs"]["a"]["label"], "Renamed")
        self.assertEqual(written["runs"]["b"]["label"], "Shared name")
        self.assertEqual(written["active"], {"running": ["a"]})
        self.repo.release_working_run("running")
        self.assertEqual(self.repo.read_actor_working_draft("alice")[0]["current"], "c")

    def test_legacy_full_document_write_cannot_move_or_drop_any_actor_position(self):
        self.create("a")
        self.create("b")
        self.select_actor("studio:explicit-user-action", "a")
        self.select_actor("bob", "b")
        before, revision = self.repo.read_working_draft()
        legacy = {key: item for key, item in before.items() if key not in ("positions", "ownerActorId")}
        legacy.update(schema="ProjectWorkingDraft@1", current="b")
        legacy["runs"]["a"]["label"] = "Shared rename"
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value=legacy)
        after, _ = self.repo.read_working_draft()
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(after["current"], "a")
        self.assertEqual(after["runs"]["a"]["label"], "Shared rename")

    def test_all_actor_recovery_survives_prune_verify_archive_and_trash_checks(self):
        from archflow.project.repository import RunNotTrashed
        self.create("a")
        self.create("b")
        self.create("unused")
        drafts = self.repo.create_run("studio-working-draft")
        a, b, old = (self.snapshot(drafts, name) for name in ("a", "b", "unused"))
        self.select_actor("alice", "a", local=a.to_dict())
        self.select_actor("bob", "b", local=b.to_dict())
        self.assertEqual(self.repo.prune_working_draft(now=NOW), (old.relative_path,))
        report = self.repo.verify()
        self.assertIn(a.relative_path, report.reachable_paths)
        self.assertIn(b.relative_path, report.reachable_paths)
        for name in ("a", "b"):
            with self.assertRaises(RunNotTrashed):
                self.repo.trash_run(name, now=NOW, rule="test", reason="Actor head stays")
        # Recovery source also holds a run even after this actor clears its head.
        self.select_actor("bob", None, local=b.to_dict())
        with self.assertRaises(RunNotTrashed):
            self.repo.trash_run("b", now=NOW, rule="test", reason="Recovery source stays")
        archive = self.root / "actors.zip"
        write_project_archive(self.repo, archive)
        restore_project_archive(self.root / "restored" / "building", archive)
        reopened = FilesystemProjectRepository.open(self.root / "restored" / "building")
        self.assertEqual(reopened.read_working_draft(), self.repo.read_working_draft())
        self.assertEqual(reopened.load_json(a), self.repo.load_json(a))
        self.assertEqual(reopened.load_json(b), self.repo.load_json(b))
        reopened.verify()

    def test_actor_ids_and_owner_alias_integrity_are_checked(self):
        for actor in ("", "../alice", "studio:arbitrary", "a/b", "a" * 129):
            with self.assertRaises(ProjectIntegrityError):
                self.repo.read_actor_working_draft(actor)
        self.create("a")
        self.select_actor("alice@example.com", "a")
        full, _ = self.repo.read_working_draft()
        full["current"] = "a"
        self.repo.layout.working_draft.write_text(json.dumps(full))
        with self.assertRaises(ProjectIntegrityError):
            self.repo.read_working_draft()

    def test_cross_process_actor_write_preserves_the_other_process_position(self):
        self.create("a")
        self.create("b")
        alice, revision = self.repo.read_actor_working_draft("alice")
        code = """import sys
from archflow.project.repository import FilesystemProjectRepository
r = FilesystemProjectRepository.open(sys.argv[1])
v, rev = r.read_actor_working_draft('bob')
v['current'] = 'b'
r.compare_and_swap_actor_working_draft('bob', expected_revision=rev, value=v)
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.repo.layout.root)],
                               capture_output=True, text=True, env=CHILD_ENV, timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)
        alice["current"] = "a"
        self.repo.compare_and_swap_actor_working_draft("alice", expected_revision=revision, value=alice)
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.read_actor_working_draft("alice")[0]["current"], "a")
        self.assertEqual(reopened.read_actor_working_draft("bob")[0]["current"], "b")

    def test_trash_and_restore_shared_row_preserve_positions_and_refuse_stale_resurrection(self):
        self.create("a")
        self.create("b")
        self.create("unused")
        self.select_actor("alice", "a")
        self.select_actor("bob", "b")
        before, _ = self.repo.read_working_draft()
        stale, revision = self.repo.read_actor_working_draft("alice")
        self.repo.trash_run("unused", now=NOW, rule="test", reason="Unused candidate")
        with self.assertRaises(ProjectIntegrityError):
            self.repo.compare_and_swap_actor_working_draft("alice", expected_revision=revision, value=stale)
        self.assertNotIn("unused", self.repo.read_working_draft()[0]["runs"])
        self.repo.restore_trashed_run("unused")
        after, _ = self.repo.read_working_draft()
        self.assertEqual(after, before)
        self.repo.verify()

    def test_two_actors_keep_different_branch_contexts_for_the_same_run(self):
        self.create("shared")
        alice, revision = self.repo.read_actor_working_draft("alice")
        alice["current"] = "shared"
        alice["runs"]["shared"]["branchId"] = "alice-line"
        _, alice_revision = self.repo.compare_and_swap_actor_working_draft(
            "alice", expected_revision=revision, value=alice)
        bob, revision = self.repo.read_actor_working_draft("bob")
        bob["current"] = "shared"
        bob["runs"]["shared"]["branchId"] = "bob-line"
        self.repo.compare_and_swap_actor_working_draft("bob", expected_revision=revision, value=bob)
        reopened = FilesystemProjectRepository.open(self.repo.layout.root)
        self.assertEqual(reopened.read_actor_working_draft("alice")[1], alice_revision)
        self.assertEqual(reopened.read_actor_working_draft("alice")[0]["runs"]["shared"]["branchId"], "alice-line")
        self.assertEqual(reopened.read_actor_working_draft("bob")[0]["runs"]["shared"]["branchId"], "bob-line")
        self.assertIsNone(reopened.read_working_draft()[0]["runs"]["shared"]["branchId"])
        alice, revision = reopened.read_actor_working_draft("alice")
        alice["runs"]["shared"]["branchId"] = "another-line"
        _, changed = reopened.compare_and_swap_actor_working_draft("alice", expected_revision=revision, value=alice)
        self.assertNotEqual(changed, revision)
        self.assertEqual(reopened.read_actor_working_draft("bob")[0]["runs"]["shared"]["branchId"], "bob-line")
        self.select_actor("alice", None)
        self.assertIsNone(self.repo.read_working_draft()[0]["positions"]["alice"]["branchId"])
