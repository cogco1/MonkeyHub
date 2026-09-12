from __future__ import annotations

import base64
import copy
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import (
    DESIGN_STAGE, PROMOTION_DECISION, SEAT_OCCT_EXECUTION, STATE_RECORD,
)
from archflow.project.repository import (
    FilesystemProjectRepository, ProjectAlreadyExists, ProjectIntegrityError,
    StaleDesignBranch, StaleProjectHead,
)


class ProjectTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.shared = FilesystemProjectRepository.initialize(
            self.root / "shared", project_id="building", initial_state={"phase": "design"},
            authored_record={"schema": "StateRecord@1", "draft": "initial"},
            seat_pack={"schema": "SeatPack@1", "seats": []},
        )
        self.s0 = self.stage(self.shared, "source")
        self.shared.compare_and_swap_design_branch(
            branch_id="main", expected_head=None,
            branch=self.branch(self.s0, self.s0),
        )

    def branch(self, fork, head):
        return {"branch_id": "main", "parent_branch": None,
                "fork_stage": fork.to_dict(), "head_stage": head.to_dict()}

    def stage(self, repository, name, parent=None):
        run = repository.create_run(name)
        artifact = repository.ingest(
            run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id=name, media_type="model/3dm", source=io.BytesIO(f"model-{name}".encode()),
        )
        record = repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=name),
            record_kind=STATE_RECORD,
            payload={"schema": "StateRecord@1", "run": run.to_dict(), "model": {
                "project_id": run.project_id, "relative_path": artifact.relative_path,
                "sha256": artifact.sha256, "media_type": artifact.media_type}},
        )
        return repository.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=name),
            record_kind=DESIGN_STAGE,
            payload={"schema": "DesignStage@1", "candidate_id": name,
                     "parent_stage": None if parent is None else parent.to_dict(),
                     "record": record.to_dict()},
        )

    def clone(self, name):
        return FilesystemProjectRepository.bootstrap_transfer(
            self.root / name, self.shared.export_transfer(), expected_project_id="building",
        )

    def advance_published(self):
        run = self.shared.create_run("issue")
        decision = self.shared.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=run.run_id),
            record_kind=PROMOTION_DECISION,
            payload={"schema": "PromotionDecision@1", "status": "accepted",
                     "project_id": run.project_id, "run_id": run.run_id,
                     "checked_state": run.base.to_dict(),
                     "candidate_ref": f"project://{run.project_id}/runs/{run.run_id}"},
        )
        prepared = self.shared.prepare_transition(
            run=run, expected=run.base, replacement_state={"phase": "issued"}, decision_receipt=decision,
        )
        self.shared.compare_and_swap(expected=prepared.expected, event=prepared.event,
                                     replacement=prepared.replacement)

    def test_bootstrap_preserves_identity_history_and_named_model_only(self):
        s1 = self.stage(self.shared, "accepted", self.s0)
        self.shared.compare_and_swap_design_branch(
            branch_id="main", expected_head=self.s0, branch=self.branch(self.s0, s1),
        )
        self.stage(self.shared, "private-candidate", self.s0)
        private = self.shared.layout.run("source").workspaces / "credentials.txt"
        private.write_text("do not transfer", encoding="utf-8")
        (self.shared.layout.inputs / "private.txt").write_text("private", encoding="utf-8")
        transfer = self.shared.export_transfer()
        paths = {row["path"] for row in transfer["files"]}
        self.assertEqual(transfer["run_ids"], ["accepted", "source"])
        self.assertNotIn(private.relative_to(self.shared.layout.root).as_posix(), paths)
        self.assertFalse(any("private" in path or path.endswith(".lock") for path in paths))
        local = self.clone("local")
        self.assertEqual(local.read_head(), self.shared.read_head())
        self.assertEqual(local.load_manifest(), self.shared.load_manifest())
        self.assertEqual(local.read_design_branches(), self.shared.read_design_branches())
        self.assertEqual(local.load_json(s1), self.shared.load_json(s1))
        self.assertEqual(local.verify().head, self.shared.verify().head)
        for row in transfer["files"]:
            self.assertEqual((local.layout.root / row["path"]).read_bytes(),
                             (self.shared.layout.root / row["path"]).read_bytes())
        with self.assertRaises(ProjectAlreadyExists):
            FilesystemProjectRepository.bootstrap_transfer(local.layout.root, transfer,
                                                           expected_project_id="building")

    def test_two_local_roots_upload_pull_continue_and_keep_local_work(self):
        a, b = self.clone("a"), self.clone("b")
        a_stage = self.stage(a, "candidate-a", self.s0)
        b_stage = self.stage(b, "candidate-b", self.s0)
        b.layout.authored_record.write_text('{"draft":"local work"}', encoding="utf-8")
        before_head = self.shared.read_head()
        before_branches = self.shared.read_design_branches()
        upload = a.export_transfer(run_id="candidate-a")
        self.shared.import_candidate_transfer(upload)
        self.shared.import_candidate_transfer(upload)
        self.assertEqual(self.shared.read_head(), before_head)
        self.assertEqual(self.shared.read_design_branches(), before_branches)
        self.shared.compare_and_swap_design_branch(
            branch_id="main", expected_head=self.s0, branch=self.branch(self.s0, a_stage),
        )
        known = {row["path"]: row["sha256"] for row in b.export_transfer()["files"]}
        download = self.shared.export_transfer(known_files=known)
        self.assertLess(len(download["contents"]), len(download["files"]))
        b.pull_transfer(download, expected_head=b.read_head(), expected_branches=b.read_design_branches())
        reopened = FilesystemProjectRepository.open(b.layout.root)
        self.assertEqual(reopened.read_design_branches(), self.shared.read_design_branches())
        self.assertEqual(reopened.load_json(b_stage), b.load_json(b_stage))
        self.assertEqual(reopened.layout.authored_record.read_text(), '{"draft":"local work"}')
        continued = self.stage(reopened, "continued-from-a", a_stage)
        self.assertEqual(reopened.load_json(continued)["parent_stage"], a_stage.to_dict())
        self.assertEqual(reopened.read_head(), before_head)

    def test_rejects_bad_project_digest_path_and_missing_dependencies_before_write(self):
        a = self.clone("a")
        self.stage(a, "candidate-a", self.s0)
        good = a.export_transfer(run_id="candidate-a")
        cases = []
        wrong_project = copy.deepcopy(good)
        wrong_project["project_id"] = "other"
        cases.append(wrong_project)
        damaged = copy.deepcopy(good)
        object_path = next(row["path"] for row in damaged["files"] if row["path"].startswith("objects/"))
        damaged["contents"][object_path] = base64.b64encode(b"damaged").decode()
        cases.append(damaged)
        missing = copy.deepcopy(good)
        del missing["contents"][object_path]
        missing["files"] = [row for row in missing["files"] if row["path"] != object_path]
        cases.append(missing)
        traversal = copy.deepcopy(good)
        traversal["files"][0]["path"] = "../outside"
        cases.append(traversal)
        extra = copy.deepcopy(good)
        path, data = "runs/candidate-a/recovery/token.txt", b"secret"
        extra["files"].append({"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        extra["contents"][path] = base64.b64encode(data).decode()
        cases.append(extra)
        before = {p.relative_to(self.shared.layout.root).as_posix(): p.read_bytes()
                  for p in self.shared.layout.root.rglob("*") if p.is_file()}
        for transfer in cases:
            with self.subTest(transfer=cases.index(transfer)):
                with self.assertRaises((ProjectIntegrityError, FileNotFoundError)):
                    self.shared.import_candidate_transfer(transfer)
                after = {p.relative_to(self.shared.layout.root).as_posix(): p.read_bytes()
                         for p in self.shared.layout.root.rglob("*") if p.is_file()}
                self.assertEqual(after, before)

    def test_failed_install_can_retry_and_cannot_advance_positions(self):
        a = self.clone("a")
        candidate = self.stage(a, "candidate-a", self.s0)
        transfer = a.export_transfer(run_id="candidate-a")
        before = self.shared.read_design_branches()
        from archflow.project.repository import _write_immutable
        writes = 0

        def fail_after_one(path, data):
            nonlocal writes
            if str(path).startswith(str(self.shared.layout.root)):
                writes += 1
                if writes == 2:
                    raise OSError("interrupted install")
            return _write_immutable(path, data)

        with patch("archflow.project.repository._write_immutable", side_effect=fail_after_one):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.shared.import_candidate_transfer(transfer)
        self.assertEqual(self.shared.read_design_branches(), before)
        self.shared.import_candidate_transfer(transfer)
        self.assertEqual(self.shared.load_json(candidate), a.load_json(candidate))
        self.assertEqual(self.shared.read_design_branches(), before)

    def test_interrupted_bootstrap_retries_matching_partial_files_only(self):
        transfer = self.shared.export_transfer()
        target = self.root / "bootstrap"
        from archflow.project.repository import _write_immutable
        writes = 0

        def interrupted(path, data):
            nonlocal writes
            if str(path).startswith(str(target)):
                writes += 1
                if writes == 2:
                    raise OSError("bootstrap interrupted")
            return _write_immutable(path, data)

        with patch("archflow.project.repository._write_immutable", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "bootstrap interrupted"):
                FilesystemProjectRepository.bootstrap_transfer(target, transfer, expected_project_id="building")
        self.assertFalse((target / "HEAD").exists())
        unknown = target / "unrelated.txt"
        unknown.write_text("retain me")
        with self.assertRaises(ProjectAlreadyExists):
            FilesystemProjectRepository.bootstrap_transfer(target, transfer, expected_project_id="building")
        self.assertEqual(unknown.read_text(), "retain me")
        unknown.unlink()
        local = FilesystemProjectRepository.bootstrap_transfer(target, transfer, expected_project_id="building")
        self.assertEqual(local.read_head(), self.shared.read_head())
        self.assertEqual(local.read_design_branches(), self.shared.read_design_branches())

    def test_pull_refuses_stale_branch_or_changed_published_head(self):
        local = self.clone("local")
        old = self.shared.export_transfer()
        s1 = self.stage(self.shared, "accepted", self.s0)
        self.shared.compare_and_swap_design_branch(
            branch_id="main", expected_head=self.s0, branch=self.branch(self.s0, s1),
        )
        previous = local.read_design_branches()
        local.pull_transfer(self.shared.export_transfer(), expected_head=local.read_head(),
                            expected_branches=previous)
        with self.assertRaises(StaleDesignBranch):
            local.pull_transfer(old, expected_head=local.read_head(), expected_branches=local.read_design_branches())
        with self.assertRaises(StaleDesignBranch):
            local.pull_transfer(self.shared.export_transfer(), expected_head=local.read_head(),
                                expected_branches=previous)
        self.advance_published()
        with self.assertRaisesRegex(StaleProjectHead, "TRANSFER_PUBLISHED_HEAD_CHANGED"):
            local.pull_transfer(self.shared.export_transfer(), expected_head=local.read_head(),
                                expected_branches=local.read_design_branches())
        self.assertEqual(local.read_head().version, 0)

    def test_native_workspace_receipt_preserves_filename_and_rejects_missing_model(self):
        run = self.shared.load_run("source")
        data = b"native-model"
        export = self.shared.layout.run("source").workspaces / "native" / "stage@123.3dm"
        export.parent.mkdir()
        export.write_bytes(data)
        self.shared.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="source"),
            record_kind=SEAT_OCCT_EXECUTION,
            payload={"schema": "OcctExecutionReceipt@1", "preview_artifact": {
                "relative_path": "stage@123.3dm", "sha256": hashlib.sha256(data).hexdigest()}},
        )
        local = self.clone("local")
        path = export.relative_to(self.shared.layout.root).as_posix()
        self.assertEqual((local.layout.root / path).read_bytes(), data)
        with patch.object(self.shared, "export_transfer", side_effect=AssertionError("must not rescan all artifacts")):
            self.assertEqual(self.shared.read_transfer_file(path, hashlib.sha256(data).hexdigest()), data)
        secret = export.parent / "local.log"
        secret.write_bytes(b"private log")
        with self.assertRaises(ProjectIntegrityError):
            self.shared.read_transfer_file(secret.relative_to(self.shared.layout.root).as_posix(),
                                           hashlib.sha256(secret.read_bytes()).hexdigest())
        with self.assertRaises(ProjectIntegrityError):
            self.shared.read_transfer_file("HEAD.lock", hashlib.sha256(b"").hexdigest())
        unreferenced = self.shared.ingest(
            run=run, destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id="unreferenced", media_type="application/octet-stream",
            source=io.BytesIO(b"unreferenced bytes"),
        )
        with self.assertRaises(ProjectIntegrityError):
            self.shared.read_transfer_file(unreferenced.relative_path, unreferenced.sha256)
        export.unlink()
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_DEPENDENCY_MISSING"):
            self.shared.export_transfer()

    def test_only_cad_evidence_metadata_splits_a_comma_separated_reference_list(self):
        run = self.shared.load_run("source")
        evidence = f"{self.s0.uri},{self.s0.uri}"
        self.shared.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=SEAT_OCCT_EXECUTION,
            payload={"schema": "OcctExecutionReceipt@1",
                     "expected_semantics": {"objects": {"model": {"user_text": {"archflow:evidence": evidence}}}},
                     "preview_inspection": {"object_user_strings": [{"attributes": [
                         {"key": "archflow:evidence", "value": evidence}]}]}},
        )
        local = self.clone("local")
        self.assertEqual(local.load_json(self.s0), self.shared.load_json(self.s0))
        self.shared.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=STATE_RECORD, payload={"schema": "StateRecord@1", "source": evidence},
        )
        with self.assertRaises(ProjectIntegrityError):
            self.shared.export_transfer()


if __name__ == "__main__":
    unittest.main()
