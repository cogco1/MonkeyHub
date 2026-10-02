"""The project trash (#575): a run leaves ``runs/`` whole, comes back exactly, and is purged after 30 days.

Nothing is copied: a run is renamed into ``trash/runs/<run_id>`` beside its
manifest and renamed back. Whatever interrupts a move, the run stands whole in
exactly one place, and the next trash, restore or purge settles what was left.
The project index forgets a trashed run and sees a restored one again.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from archflow.project import repository as repository_module
from archflow.project.index import IndexKeeper, ProjectIndex, RecordRow, RunRows, TreeRows, manifest_stamp
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import AUDIT_EVENT, PROMOTION_DECISION, STATE_RECORD, STUDIO_LOCAL_DRAFT, STUDIO_MODEL_ASSET
from archflow.project.repository import (
    TRASH_ENTRY_SCHEMA,
    FilesystemProjectRepository,
    RunNotRestored,
    RunNotTrashed,
    TrashEntryNotFound,
)
from archflow.project.watch import watch_layout

PROJECT_ID = "building"
TRASHED = "2026-10-01T09:00:00+00:00"


def later(stamp: str, **delta: float) -> str:
    return (datetime.fromisoformat(stamp) + timedelta(**delta)).isoformat()


class _Crash(BaseException):
    """A process that stops mid-move: nothing it would have done afterwards happens."""


class _TrashCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        self.temporary = temporary
        self.root = temporary / PROJECT_ID
        self.repo = FilesystemProjectRepository.initialize(self.root, project_id=PROJECT_ID, initial_state={})

    def draft(self, run_id: str, *, automatic: bool = True, label: str | None = None, row: bool = True) -> None:
        """A finished run with records, a model file and, unless told otherwise, its automatic working row."""

        run = self.repo.create_run(run_id)
        self.put(run_id, STATE_RECORD, {"run_id": run_id, "entities": []})
        self.put(run_id, STUDIO_MODEL_ASSET, {"schema": "StudioModelAsset@1", "runId": run_id})
        self.repo.put_workspace_file(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run_id),
            artifact_id="model", workspace_relative_path="models/model.3dm", media_type="model/vnd.3dm",
            source=io.BytesIO(b"3dm bytes of " + run_id.encode()),
        )
        if row:
            value, revision = self.repo.read_working_draft()
            value["runs"][run_id] = {"updatedAt": TRASHED, "sourceStageRef": None, "branchId": None,
                                     "label": label, "automatic": automatic}
            self.repo.compare_and_swap_working_draft(expected_revision=revision, value=value)

    def put(self, run_id: str, kind: str, payload: dict, area: PersistenceArea = PersistenceArea.RUN_RECORD):
        return self.repo.put_json(run=self.repo.load_run(run_id), record_kind=kind, payload=payload,
                                  destination=PersistenceDestination(area, run_id=run_id))

    def tree(self, directory: Path) -> dict[str, str]:
        """Every file below ``directory`` by relative path, with the digest of its bytes."""

        return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*")) if path.is_file()}

    def row(self, run_id: str) -> dict | None:
        return self.repo.read_working_draft()[0]["runs"].get(run_id)

    def trash(self, run_id: str, **extra) -> object:
        return self.repo.trash_run(run_id, now=extra.pop("now", TRASHED), rule=extra.pop("rule", "superseded"),
                                   reason=extra.pop("reason", "Built from B, where the line moved on through C."), **extra)

    def in_one_place(self, run_id: str, files: dict[str, str]) -> str:
        """Where the run stands: in ``runs`` or in the trash, whole, and never in both."""

        places = [place for place, directory in (("runs", self.repo.layout.run(run_id).root),
                                                 ("trash", self.repo.layout.trashed_run(run_id)))
                  if directory.exists()]
        self.assertEqual(len(places), 1, f"{run_id} stands in {places}")
        directory = self.repo.layout.run(run_id).root if places[0] == "runs" else self.repo.layout.trashed_run(run_id)
        self.assertEqual(self.tree(directory), files, f"{run_id} is not whole in {places[0]}")
        return places[0]


class TrashRoundTripTests(_TrashCase):
    def test_a_run_moves_whole_with_its_row_and_comes_back_exactly(self) -> None:
        self.draft("draft")
        self.draft("other")
        files = self.tree(self.repo.layout.run("draft").root)
        row = self.row("draft")

        entry = self.trash("draft", state_digest="a" * 64, superseded_by="step-c", base_run_id="step-b",
                           label="Give the ribs their materials")
        self.assertEqual((entry.run_id, entry.trashed_at, entry.rule, entry.superseded_by, entry.base_run_id,
                          entry.label, entry.state_digest, entry.working_row),
                         ("draft", TRASHED, "superseded", "step-c", "step-b", "Give the ribs their materials",
                          "a" * 64, row))
        self.assertEqual(self.in_one_place("draft", files), "trash")
        self.assertNotIn("draft", self.repo.run_ids())
        self.assertIsNone(self.row("draft"), "its row went with it")
        self.assertIsNotNone(self.row("other"))
        manifest = json.loads(self.repo.layout.trash_manifest("draft").read_text(encoding="utf-8"))
        self.assertEqual((manifest["schema"], manifest["projectId"], manifest["runId"], manifest["trashedAt"],
                          manifest["reason"], manifest["workingRow"]),
                         (TRASH_ENTRY_SCHEMA, PROJECT_ID, "draft", TRASHED,
                          "Built from B, where the line moved on through C.", row))
        self.assertEqual(self.repo.trash_entries(), (entry,))
        # The project reopens: nothing it verifies stood on the run.
        reopened = FilesystemProjectRepository.open(self.root)
        self.assertEqual(reopened.trash_entries(), (entry,))

        restored = self.repo.restore_trashed_run("draft")
        self.assertEqual(restored, entry)
        self.assertEqual(self.in_one_place("draft", files), "runs")
        self.assertEqual(self.row("draft"), row)
        self.assertIn("draft", self.repo.run_ids())
        self.assertEqual(self.repo.trash_entries(), ())
        self.assertFalse(self.repo.layout.trash_manifest("draft").exists())
        self.assertEqual(self.repo.load_run("draft").run_id, "draft")
        FilesystemProjectRepository.open(self.root)

    def test_a_run_without_a_row_moves_and_returns_without_one(self) -> None:
        self.draft("attempt", row=False)
        files = self.tree(self.repo.layout.run("attempt").root)
        entry = self.trash("attempt", rule="failed-attempt", reason="Its run never finished.")
        self.assertIsNone(entry.working_row)
        self.assertEqual(self.in_one_place("attempt", files), "trash")
        self.repo.restore_trashed_run("attempt")
        self.assertEqual(self.in_one_place("attempt", files), "runs")
        self.assertIsNone(self.row("attempt"))
        self.assertIsNone(self.repo.read_working_draft()[1], "no working position was ever written")

    def test_purge_deletes_only_entries_older_than_thirty_days(self) -> None:
        self.draft("old")
        self.draft("young")
        self.trash("old")
        self.trash("young", now=later(TRASHED, days=10))
        self.assertEqual(self.repo.purge_trash(now=later(TRASHED, days=30)), (), "exactly 30 days is still restorable")
        self.assertEqual(self.repo.purge_trash(now=later(TRASHED, days=30, seconds=1)), ("old",))
        self.assertEqual([entry.run_id for entry in self.repo.trash_entries()], ["young"])
        self.assertFalse(self.repo.layout.trashed_run("old").exists())
        self.assertFalse(self.repo.layout.trash_manifest("old").exists())
        self.assertEqual([path.name for path in (self.repo.layout.trash / "runs").iterdir()], ["young"])
        with self.assertRaises(TrashEntryNotFound):
            self.repo.restore_trashed_run("old")
        self.assertEqual(self.repo.purge_trash(now=later(TRASHED, days=41)), ("young",))
        self.assertEqual(self.repo.trash_entries(), ())
        FilesystemProjectRepository.open(self.root)

    def test_purging_an_empty_trash_writes_nothing(self) -> None:
        # A project opened with nothing in its trash is left as it was: not even a lock file appears.
        # (A fresh project: no design write has taken the design lock yet.)
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(self.repo.purge_trash(now=later(TRASHED, days=90)), ())
        self.assertEqual(sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*")), before)

    def test_a_restore_never_replaces_a_run(self) -> None:
        self.draft("draft")
        self.trash("draft")
        self.repo.create_run("draft")
        with self.assertRaises(RunNotRestored):
            self.repo.restore_trashed_run("draft")
        self.assertEqual([entry.run_id for entry in self.repo.trash_entries()], ["draft"])
        with self.assertRaises(TrashEntryNotFound):
            self.repo.restore_trashed_run("nothing-here")

    def test_run_mentions_names_whole_ids_outside_the_run_itself(self) -> None:
        self.draft("draft")
        self.draft("draft-2")
        self.draft("child")
        self.put("child", STATE_RECORD, {"source": f"project://{PROJECT_ID}/runs/draft/records/x.json"})
        self.put("draft", STATE_RECORD, {"mentions": ["draft", "draft-2"]})
        mentions = self.repo.run_mentions(["draft", "draft-2", "lonely"])
        self.assertEqual(mentions["draft"], frozenset({"run:child", "design/working.json"}),
                         "its own records and a longer id that begins with it are not mentions")
        self.assertEqual(mentions["draft-2"], frozenset({"run:draft", "design/working.json"}))
        self.assertEqual(mentions["lonely"], frozenset())
        # The trash is not searched: a trashed run mentions nothing.
        self.trash("child")
        self.assertEqual(self.repo.run_mentions(["draft"])["draft"], frozenset({"design/working.json"}))


class TrashRefusalTests(_TrashCase):
    def assert_stays(self, run_id: str, words: str) -> None:
        files = self.tree(self.repo.layout.run(run_id).root)
        row = self.row(run_id)
        with self.assertRaises(RunNotTrashed) as refused:
            self.trash(run_id)
        self.assertIn(words, str(refused.exception))
        self.assertEqual(self.in_one_place(run_id, files), "runs")
        self.assertEqual(self.row(run_id), row)
        self.assertFalse(self.repo.layout.trash_manifest(run_id).exists())

    def test_what_the_repository_holds_is_never_moved(self) -> None:
        self.draft("head")
        value, revision = self.repo.read_working_draft()
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value={**value, "current": "head"})
        self.assert_stays("head", "Working Head")

        self.draft("saved", label="V3")
        self.assert_stays("saved", "saved version")
        self.draft("chosen", automatic=False)
        self.assert_stays("chosen", "chose it")

        self.draft("running")
        self.draft("source")
        self.repo.protect_working_run("running", "source")
        self.assert_stays("running", "execution")
        self.assert_stays("source", "execution")
        self.repo.release_working_run("running")

        self.draft("continued")
        self.put("continued", AUDIT_EVENT, {"schema": "AuditEvent@1", "action": "design.continued",
                                            "targetRunId": "continued"}, PersistenceArea.RUN_REVIEW)
        self.assert_stays("continued", "review")

        self.draft("recovered")
        self.draft("drafts", row=False)
        local = self.put("drafts", STUDIO_LOCAL_DRAFT, {
            "schema": "StudioLocalDraft@1", "projectId": PROJECT_ID, "updatedAt": TRASHED,
            "draft": {"source": {"projectId": PROJECT_ID, "sourceRunId": "recovered", "sourceStageRef": None,
                                 "stateDigest": "a" * 64}, "commands": [], "attempt": None}}, PersistenceArea.RUN_RECOVERY)
        value, revision = self.repo.read_working_draft()
        self.repo.compare_and_swap_working_draft(expected_revision=revision, value={**value, "localDraftRef": local.to_dict()})
        self.assert_stays("recovered", "local recovery was made from it")
        self.assert_stays("drafts", "local recovery")

        with self.assertRaises(RunNotTrashed):
            self.trash("never-made")

    def test_the_published_history_is_never_moved(self) -> None:
        base = self.repo.read_head()
        run = self.repo.create_run("issued", base=base)
        decision = self.repo.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id="issued"),
            record_kind=PROMOTION_DECISION,
            payload={"schema": "PromotionDecision@1", "status": "accepted", "project_id": PROJECT_ID, "run_id": "issued",
                     "checked_state": {"project_id": PROJECT_ID, "version": base.version, "state_sha256": base.require_digest()},
                     "candidate_ref": f"project://{PROJECT_ID}/runs/issued"})
        prepared = self.repo.prepare_transition(run=run, expected=base, replacement_state={"phase": "issued"},
                                                decision_receipt=decision)
        self.repo.compare_and_swap(expected=prepared.expected, event=prepared.event, replacement=prepared.replacement)
        self.assertIn("issued", self.repo._published_runs())
        self.assert_stays("issued", "stays")


class InterruptedMoveTests(_TrashCase):
    """Wherever a move stops, the run is whole in one place, and the next write settles what it left."""

    def setUp(self) -> None:
        super().setUp()
        self.draft("draft")
        self.files = self.tree(self.repo.layout.run("draft").root)
        self.before = self.row("draft")

    def settled(self) -> None:
        """The run is back as it was before anything began: in ``runs``, with its row, and no manifest."""

        self.assertEqual(self.in_one_place("draft", self.files), "runs")
        self.assertEqual(self.row("draft"), self.before)
        self.assertFalse(self.repo.layout.trash_manifest("draft").exists())
        self.assertEqual(self.repo.trash_entries(), ())
        FilesystemProjectRepository.open(self.root)

    def test_a_crash_before_the_rename_leaves_the_run_in_place_and_its_row_comes_back(self) -> None:
        with mock.patch.object(repository_module, "_rename_directory", side_effect=_Crash):
            with self.assertRaises(_Crash):
                self.trash("draft")
        # The process stopped with its row dropped and the manifest written; the run never moved.
        self.assertEqual(self.in_one_place("draft", self.files), "runs")
        self.assertIsNone(self.row("draft"))
        self.assertTrue(self.repo.layout.trash_manifest("draft").exists())
        self.assertEqual(self.repo.trash_entries(), (), "a manifest without its run is no entry")
        FilesystemProjectRepository.open(self.root)
        self.assertEqual(self.repo.purge_trash(now=later(TRASHED, days=90)), ())
        self.settled()

    def test_a_busy_run_stays_where_it_is(self) -> None:
        with mock.patch.object(repository_module, "_rename_directory",
                               side_effect=repository_module._DirectoryBusy("held open by a scanner")):
            with self.assertRaises(RunNotTrashed) as refused:
                self.trash("draft")
        self.assertIn("left where it is", str(refused.exception))
        self.settled()

    def test_a_crash_in_a_restore_after_the_rename_finishes_the_restore(self) -> None:
        self.trash("draft")
        with mock.patch.object(FilesystemProjectRepository, "compare_and_swap_working_draft", side_effect=_Crash):
            with self.assertRaises(_Crash):
                self.repo.restore_trashed_run("draft")
        self.assertEqual(self.in_one_place("draft", self.files), "runs")
        self.assertIsNone(self.row("draft"))
        FilesystemProjectRepository.open(self.root)
        self.assertEqual(self.repo.purge_trash(now=TRASHED), ())
        self.settled()

    def test_a_crash_in_a_purge_leaves_nothing_restorable_and_the_next_purge_removes_the_rest(self) -> None:
        self.trash("draft")
        original = repository_module._remove_directory
        with mock.patch.object(repository_module, "_remove_directory", side_effect=_Crash):
            with self.assertRaises(_Crash):
                self.repo.purge_trash(now=later(TRASHED, days=31))
        self.assertFalse(self.repo.layout.trashed_run("draft").exists())
        self.assertFalse(self.repo.layout.run("draft").root.exists())
        self.assertEqual(self.repo.trash_entries(), ())
        leftover = [path.name for path in (self.repo.layout.trash / "runs").iterdir()]
        self.assertEqual(len(leftover), 1)
        self.assertTrue(leftover[0].startswith(".") and leftover[0].endswith(".purge"))
        with mock.patch.object(repository_module, "_remove_directory", wraps=original):
            self.assertEqual(self.repo.purge_trash(now=later(TRASHED, days=31)), ())
        self.assertEqual(list((self.repo.layout.trash / "runs").iterdir()), [])
        self.assertIsNone(self.row("draft"), "a purged run's row never comes back")
        FilesystemProjectRepository.open(self.root)


class _Projector:
    """The rows of each run directory, as the repository lists them."""

    version = "trash-projector@1"

    def __init__(self, repository: FilesystemProjectRepository) -> None:
        self.repository = repository

    def run_ids(self) -> tuple[str, ...]:
        return self.repository.run_ids()

    def project_run(self, run_id: str) -> RunRows:
        try:
            refs = self.repository.list_json(run=self.repository.load_run(run_id),
                                             destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run_id))
        except Exception:  # noqa: BLE001 - a run that moved away while it was read
            return RunRows(run_id, {"unreadable": True})
        return RunRows(run_id, {"records": len(refs)}, tuple(RecordRow(ref.uri, ref.record_kind, ref.sha256) for ref in refs))

    def project_tree(self) -> TreeRows:
        return TreeRows({})

    def project_working(self):
        return None


class TrashIndexTests(_TrashCase):
    def test_the_index_forgets_a_trashed_run_and_sees_it_restored(self) -> None:
        self.draft("draft")
        self.draft("kept")
        projector = _Projector(self.repo)
        index = ProjectIndex(self.temporary / "cache", projector=projector,
                             stamp=lambda: manifest_stamp(self.root, projector_version=projector.version, project_id=PROJECT_ID))
        keeper = IndexKeeper(index, watch_layout(self.root, notify=False), name="trash")
        keeper.start()
        self.addCleanup(keeper.stop)
        self.assertIsNotNone(keeper.wait_loaded(30), keeper.failure)

        def runs() -> list[str]:
            self.assertIsNotNone(keeper.wait_readable(10), "the keeper applies this process's writes")
            return [row["run_id"] for row in keeper.index.query("run")]

        self.assertEqual(runs(), ["draft", "kept"])
        self.trash("draft")
        self.assertEqual(runs(), ["kept"])
        self.assertEqual(keeper.index.query("record", {"run_id": "draft"}), [])
        self.repo.restore_trashed_run("draft")
        self.assertEqual(runs(), ["draft", "kept"])
        self.assertEqual(len(keeper.index.query("record", {"run_id": "draft"})), 2)


if __name__ == "__main__":
    unittest.main()
