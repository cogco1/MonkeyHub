from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.inputs import load_program_sheet_file, write_program_sheet_file
from archflow.project.record_kinds import (
    DESIGN_STAGE, DRAWING_PROJECTION_RECEIPT, PROMOTION_DECISION, SEAT_OCCT_EXECUTION,
    STATE_RECORD, STUDIO_SOURCE_DOCUMENT,
)
from archflow.project.repository import (
    FilesystemProjectRepository, ProjectAlreadyExists, ProjectIntegrityError,
    StaleDesignBranch, StaleProjectHead,
)
from archflow.state.state_record import StateRecord


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

    def test_unbound_authored_input_preserves_bytes_and_follows_nested_run_refs(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                payload = StateRecord(project_id="building", run_id="authored", entities=()).to_dict()
                if nested:
                    run = self.shared.create_run("authored-dependency")
                    payload["source"] = run.to_dict()
                data = json.dumps(payload).encode("utf-8")
                self.shared.layout.authored_record.write_bytes(data)
                transfer = self.shared.export_transfer()
                self.assertNotIn("authored", transfer["run_ids"])
                self.assertEqual("authored-dependency" in transfer["run_ids"], nested)
                restored = FilesystemProjectRepository.bootstrap_transfer(
                    self.root / f"authored-{nested}", transfer, expected_project_id="building",
                )
                self.assertEqual(restored.layout.authored_record.read_bytes(), data)

        payload["source"]["run_id"] = "missing-retained-run"
        self.shared.layout.authored_record.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ProjectIntegrityError, "run.json"):
            self.shared.export_transfer()

        foreign = StateRecord(project_id="other-building", run_id="authored", entities=()).to_dict()
        self.shared.layout.authored_record.write_text(json.dumps(foreign), encoding="utf-8")
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_PROJECT_MISMATCH"):
            self.shared.export_transfer()

    def test_complete_snapshot_retains_unattached_runs_without_workspace_noise(self):
        candidate = self.stage(self.shared, "unaccepted")
        noise = self.shared.layout.run("unaccepted").workspaces / "credentials.txt"
        noise.write_bytes(b"synthetic runtime secret; must not travel")
        self.assertNotIn("unaccepted", self.shared.export_transfer()["run_ids"])
        transfer = self.shared.export_transfer(include_all_runs=True)
        self.assertIn("unaccepted", transfer["run_ids"])
        self.assertNotIn(noise.relative_to(self.shared.layout.root).as_posix(),
                         {row["path"] for row in transfer["files"]})
        restored = FilesystemProjectRepository.bootstrap_transfer(
            self.root / "complete", transfer, expected_project_id="building",
        )
        self.assertEqual(restored.read_head(), self.shared.read_head())
        self.assertEqual(restored.read_design_branches(), self.shared.read_design_branches())
        self.assertEqual(restored.load_json(candidate), self.shared.load_json(candidate))
        for row in transfer["files"]:
            self.assertEqual((restored.layout.root / row["path"]).read_bytes(),
                             (self.shared.layout.root / row["path"]).read_bytes())
        self.assertFalse((restored.layout.root / noise.relative_to(self.shared.layout.root)).exists())

    def test_snapshot_restores_authored_program_sheet_through_normal_reader(self):
        sheet = {"schema": "ProgramSheet@1", "project_id": "building", "spaces": []}
        written = write_program_sheet_file(self.shared, sheet)
        data = written.path.read_bytes()
        path = written.path.relative_to(self.shared.layout.root).as_posix()
        self.assertEqual(self.shared.read_transfer_file(path, written.sha256), data)
        restored = FilesystemProjectRepository.bootstrap_transfer(
            self.root / "program", self.shared.export_transfer(include_all_runs=True),
            expected_project_id="building",
        )
        read = load_program_sheet_file(restored)
        self.assertEqual(read.payload, sheet)
        self.assertEqual(read.path.read_bytes(), data)

    def test_generated_document_follows_drawing_receipt_without_an_uploaded_object(self):
        source = self.shared.load_run("source")
        drawing = self.shared.create_run("drawing")
        data = b"retained synthetic drawing PNG"
        png = self.shared.put_workspace_file(
            run=drawing, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=drawing.run_id),
            artifact_id="elevation", workspace_relative_path="documentation/elevation.png",
            media_type="image/png", source=io.BytesIO(data),
        )
        receipt = self.shared.put_json(
            run=drawing, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=drawing.run_id),
            record_kind=DRAWING_PROJECTION_RECEIPT,
            payload={"schema": "DrawingProjectionReceipt@1", "artifacts": {"png": {
                "relative_path": png.relative_path, "sha256": png.sha256, "media_type": png.media_type,
            }}},
        )
        document = self.shared.put_json(
            run=source, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=source.run_id),
            record_kind=STUDIO_SOURCE_DOCUMENT,
            payload={"schema": "StudioSourceDocument@1", "project_id": "building", "run_id": source.run_id,
                     "asset_sha256": png.sha256, "revisionRef": receipt.uri},
        )
        object_path = f"objects/sha256/{png.sha256[:2]}/{png.sha256}"
        self.assertFalse((self.shared.layout.root / object_path).exists())
        for run_id in (None, source.run_id):
            transfer = self.shared.export_transfer(run_id=run_id)
            self.assertIn(png.relative_path, {row["path"] for row in transfer["files"]})
            self.assertNotIn(object_path, {row["path"] for row in transfer["files"]})
        restored = self.clone("drawing-copy")
        self.assertEqual(restored.load_json(document), self.shared.load_json(document))
        self.assertEqual(restored.load_json(receipt), self.shared.load_json(receipt))
        self.assertEqual(restored.read_transfer_file(png.relative_path, png.sha256), data)

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

    # ---- exports/ is a root the closure passes through, never one it packs

    def _retained_export(self, name: str, body: bytes) -> tuple[str, str]:
        """Put a file under exports/ and have a retained receipt name it."""

        import hashlib as _hashlib

        path = self.shared.layout.root / "exports" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return f"exports/{name}", _hashlib.sha256(body).hexdigest()

    def _name_export_from_a_record(self, relative: str, digest: str) -> None:
        run = self.shared.load_run("source")
        self.shared.put_json(
            run=run,
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="source"),
            record_kind=DRAWING_PROJECTION_RECEIPT,
            payload={
                "schema": "DrawingProjectionReceipt@1",
                "run": run.to_dict(),
                "sheet": {
                    "project_id": run.project_id,
                    "relative_path": relative,
                    "sha256": digest,
                    "media_type": "application/json",
                },
            },
        )

    def test_a_retained_reference_carries_one_export_and_leaves_the_rest(self):
        """The closure follows a typed edge into exports/; it never walks it."""

        referenced, digest = self._retained_export("sheet.json", b'{"sheet": 1}\n')
        unreferenced, _ = self._retained_export("scratch.json", b'{"scratch": 1}\n')
        self._name_export_from_a_record(referenced, digest)

        transfer = self.shared.export_transfer(include_all_runs=True)
        carried = {row["path"] for row in transfer["files"]}

        self.assertIn(referenced, carried)
        self.assertNotIn(unreferenced, carried)
        restored = FilesystemProjectRepository.bootstrap_transfer(
            self.root / "with-export", transfer, expected_project_id="building",
        )
        self.assertEqual(
            (restored.layout.root / referenced).read_bytes(),
            (self.shared.layout.root / referenced).read_bytes(),
        )
        self.assertFalse((restored.layout.root / unreferenced).exists())

    def test_a_referenced_export_that_is_missing_still_fails_closed(self):
        referenced, digest = self._retained_export("gone.json", b'{"sheet": 2}\n')
        self._name_export_from_a_record(referenced, digest)
        (self.shared.layout.root / referenced).unlink()

        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_DEPENDENCY_MISSING"):
            self.shared.export_transfer(include_all_runs=True)

    def test_an_export_no_record_names_is_not_readable_as_a_transfer_file(self):
        unreferenced, digest = self._retained_export("private.json", b'{"private": 1}\n')
        with self.assertRaises(ProjectIntegrityError):
            self.shared.read_transfer_file(unreferenced, digest)

    def test_shared_workspace_artifact_survives_when_only_another_run_refers_to_it(self):
        # A retained run may hold the bytes while the only receipt naming them
        # lives in a different retained run. Export follows that reference by
        # its complete project-relative path, so every exported manifest row
        # must stay readable one file at a time; the archive writer reads the
        # whole manifest that way and cannot fall back to a rescan.
        holder = self.shared.create_run("holder")
        data = b"retained synthetic shared elevation PNG"
        png = self.shared.put_workspace_file(
            run=holder, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=holder.run_id),
            artifact_id="elevation", workspace_relative_path="documentation/elevation.png",
            media_type="image/png", source=io.BytesIO(data),
        )
        self.assertTrue(png.relative_path.startswith(f"runs/{holder.run_id}/workspaces/"))
        self.shared.put_json(
            run=self.shared.load_run("source"),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="source"),
            record_kind=DRAWING_PROJECTION_RECEIPT,
            payload={"schema": "DrawingProjectionReceipt@1", "artifacts": {"png": {
                "relative_path": png.relative_path, "sha256": png.sha256, "media_type": png.media_type,
            }}},
        )
        transfer = self.shared.export_transfer()
        self.assertIn(holder.run_id, transfer["run_ids"])
        self.assertIn(png.relative_path, {row["path"] for row in transfer["files"]})
        # The archive writer's contract: every manifest row, byte for byte.
        for row in transfer["files"]:
            with self.subTest(path=row["path"]):
                read = self.shared.read_transfer_file(row["path"], row["sha256"])
                self.assertEqual(read, (self.shared.layout.root / row["path"]).read_bytes())
                self.assertEqual(len(read), row["size"])
        self.assertEqual(self.shared.read_transfer_file(png.relative_path, png.sha256), data)
        restored = self.clone("shared-artifact")
        self.assertEqual((restored.layout.root / png.relative_path).read_bytes(), data)
        self.assertEqual(restored.read_transfer_file(png.relative_path, png.sha256), data)

        # Reaching the bytes from another run must not relax any other refusal.
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_DIGEST_MISMATCH|TRANSFER_FILE_UNAVAILABLE"):
            self.shared.read_transfer_file(png.relative_path, hashlib.sha256(b"other").hexdigest())
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_PATH_INVALID"):
            self.shared.read_transfer_file(f"runs/{holder.run_id}/workspaces/../../../outside.png", png.sha256)
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_PATH_INVALID"):
            self.shared.read_transfer_file("input/private.txt", png.sha256)
        neighbour = self.shared.layout.run(holder.run_id).workspaces / "documentation" / "local.log"
        neighbour.write_bytes(b"private log beside a shared artifact")
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_FILE_UNAVAILABLE"):
            self.shared.read_transfer_file(neighbour.relative_to(self.shared.layout.root).as_posix(),
                                           hashlib.sha256(neighbour.read_bytes()).hexdigest())
        unreferenced = self.shared.ingest(
            run=holder, destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id="unreferenced", media_type="application/octet-stream",
            source=io.BytesIO(b"unreferenced bytes"),
        )
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_FILE_UNAVAILABLE"):
            self.shared.read_transfer_file(unreferenced.relative_path, unreferenced.sha256)

        # A foreign project's reference authorizes nothing, from any run.
        foreign_data = b"bytes a foreign project claims"
        foreign = self.shared.put_workspace_file(
            run=holder, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=holder.run_id),
            artifact_id="foreign", workspace_relative_path="documentation/foreign.png",
            media_type="image/png", source=io.BytesIO(foreign_data),
        )
        self.shared.put_json(
            run=self.shared.load_run("source"),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id="source"),
            record_kind=DRAWING_PROJECTION_RECEIPT,
            payload={"schema": "DrawingProjectionReceipt@1", "artifacts": {"png": {
                "project_id": "other-building", "relative_path": foreign.relative_path,
                "sha256": foreign.sha256, "media_type": foreign.media_type,
            }}},
        )
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_FILE_UNAVAILABLE"):
            self.shared.read_transfer_file(foreign.relative_path, foreign.sha256)
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_PROJECT_MISMATCH"):
            self.shared.export_transfer()

    def test_cross_run_uri_is_exact_and_native_basename_stays_run_local(self):
        holder = self.shared.create_run("holder")
        data = b"shared native model"
        native = self.shared.put_workspace_file(
            run=holder, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=holder.run_id),
            artifact_id="shared", workspace_relative_path="native/shared.3dm",
            media_type="model/3dm", source=io.BytesIO(data),
        )
        source = self.shared.load_run("source")
        destination = PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=source.run_id)
        self.shared.put_json(
            run=source, destination=destination, record_kind=SEAT_OCCT_EXECUTION,
            payload={"schema": "OcctExecutionReceipt@1", "preview_artifact": {
                "relative_path": "shared.3dm", "sha256": native.sha256},
                "artifact_relative_path": "shared.3dm", "inspection": {"file_sha256": native.sha256}},
        )
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_FILE_UNAVAILABLE"):
            self.shared.read_transfer_file(native.relative_path, native.sha256)
        self.shared.put_json(
            run=source, destination=destination, record_kind=STATE_RECORD,
            payload={"schema": "StateRecord@1", "reference": f"project://building/{native.relative_path}"},
        )
        self.assertEqual(self.shared.read_transfer_file(native.relative_path, native.sha256), data)

    def workspace_json(self, name="dwg/living-ground-boundaries.json"):
        """A native export that happens to be JSON, named by a retained receipt."""
        run = self.shared.load_run("source")
        data = json.dumps({"schema": "DrawingBoundaries@1", "polylines": []}).encode("utf-8")
        drawing = self.shared.put_workspace_file(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE, run_id=run.run_id),
            artifact_id="boundaries", workspace_relative_path=name,
            media_type="application/json", source=io.BytesIO(data),
        )
        self.shared.put_json(
            run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id),
            record_kind=DRAWING_PROJECTION_RECEIPT,
            payload={"schema": "DrawingProjectionReceipt@1", "artifacts": {"boundaries": {
                "relative_path": drawing.relative_path, "sha256": drawing.sha256,
                "media_type": drawing.media_type}}},
        )
        return drawing, data

    def test_referenced_workspace_json_survives_export_archive_and_restore(self):
        # A run's workspace holds native exports and some of those are JSON.
        # They are artifacts, not records, so the content-addressed record
        # filename must not be demanded of them — otherwise the receiver
        # refuses an archive this same code just wrote.
        from tools.create_project import _restore_project_archive, _write_project_archive

        drawing, data = self.workspace_json()
        transfer = self.shared.export_transfer()
        self.assertIn(drawing.relative_path, {row["path"] for row in transfer["files"]})
        self.assertEqual(self.shared.read_transfer_file(drawing.relative_path, drawing.sha256), data)

        restored = self.clone("workspace-json")
        self.assertEqual((restored.layout.root / drawing.relative_path).read_bytes(), data)
        self.assertEqual(restored.read_transfer_file(drawing.relative_path, drawing.sha256), data)

        archive = self.root / "archives" / "building.monkeyhub.zip"
        _write_project_archive(self.shared, archive)
        home = self.root / "archive-home" / "building"
        home.parent.mkdir(parents=True, exist_ok=True)
        opened, manifest = _restore_project_archive(home, archive)
        self.assertIn(drawing.relative_path, {row["path"] for row in manifest["transfer"]["files"]})
        self.assertEqual((home / drawing.relative_path).read_bytes(), data)
        self.assertEqual(opened.read_head(), self.shared.read_head())
        for row in manifest["transfer"]["files"]:
            self.assertEqual((home / row["path"]).read_bytes(),
                             (self.shared.layout.root / row["path"]).read_bytes())

    def test_workspace_exemption_does_not_reach_records_canonical_events_or_digests(self):
        drawing, data = self.workspace_json()
        good = self.shared.export_transfer()
        record_row = next(row for row in good["files"]
                          if row["path"].startswith("runs/source/records/"))
        payload, other = b'{"schema": "NotARecord@1"}', hashlib.sha256(b"other").hexdigest()

        def mutated(path, blob, sha256=None):
            case = copy.deepcopy(good)
            case["files"] = [row for row in case["files"] if row["path"] != path]
            case["files"].append({"path": path, "sha256": sha256 or hashlib.sha256(blob).hexdigest(),
                                  "size": len(blob)})
            case["contents"][path] = base64.b64encode(blob).decode()
            return case

        cases = {
            # a plain name in a record area is still not a record
            "TRANSFER_INVALID": mutated("runs/source/records/plain-data.json", payload),
            # the same plain name under canonical/ and events/ stays refused
            "canonical": mutated("canonical/plain-data.json", payload),
            "events": mutated("events/plain-data.json", payload),
            # a record-shaped name whose embedded digest is not its content
            "record filename": mutated(f"runs/source/records/state-record-{other}.json", payload),
            # the workspace artifact itself still needs the digest it claims
            "workspace digest": mutated(drawing.relative_path, data, sha256=other),
        }
        for label, transfer in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ProjectIntegrityError):
                    FilesystemProjectRepository.bootstrap_transfer(
                        self.root / f"refused-{label.replace(' ', '-')}", transfer,
                        expected_project_id="building")
        # the real record row is untouched and the good transfer still installs
        self.assertEqual(next(row for row in good["files"]
                              if row["path"] == record_row["path"]), record_row)
        traversal = copy.deepcopy(good)
        traversal["files"][0]["path"] = "../outside.json"
        with self.assertRaisesRegex(ProjectIntegrityError, "TRANSFER_PATH_INVALID"):
            FilesystemProjectRepository.bootstrap_transfer(
                self.root / "refused-traversal", traversal, expected_project_id="building")
        self.assertTrue(self.clone("still-good").verify().head)

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
