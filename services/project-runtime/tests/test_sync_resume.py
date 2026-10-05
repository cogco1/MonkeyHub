"""Chunk resume and integrity remain outside project persistence until complete."""
import base64
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import archflow.project.repository as storage

from starlette.datastructures import State
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STATE_RECORD, DESIGN_STAGE
from archflow.project.repository import FilesystemProjectRepository
from project_runtime.main import create_app
from project_runtime.settings import StudioSettings
from project_runtime.synchronization import pull_shared_project, TeamSynchronization
from project_runtime.errors import StudioError
from project_runtime.binding import release_bound_project
from project_runtime.sync_cache import SyncDownloadCache

class ResumeTests(unittest.TestCase):
    def test_interrupted_clone_resumes_verified_chunks_and_preserves_no_partial_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = FilesystemProjectRepository.initialize(root / "owner" / "sample", project_id="sample", initial_state={})
            run = repo.create_run("retained")
            content = b"large-fixture" * (1024 * 1024)
            artifact = repo.ingest(run=run, destination=PersistenceDestination(PersistenceArea.OBJECT), artifact_id="large", media_type="application/octet-stream", source=io.BytesIO(content))
            repo.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=run.run_id), record_kind=STATE_RECORD,
                          payload={"schema": "StateRecord@1", "asset": {"project_id": "sample", "relative_path": artifact.relative_path, "sha256": artifact.sha256, "media_type": artifact.media_type}})
            manifest = repo.export_sync_transfer(all_candidates=True)
            class Client:
                project_id = "sample"
                def __init__(self):
                    self.fail = True
                    self.calls = []
                def manifest(self):
                    return {**manifest, "contents": {}}
                def request(self, method, path, payload=None, **kwargs):
                    self.calls.append(payload["files"])
                    if self.fail and len(self.calls) == 2:
                        raise StudioError(503, "SYNC_UNAVAILABLE", "interrupted fixture")
                    return {"files": [{**row, "content": base64.b64encode(repo.read_transfer_file(row["path"], row["sha256"])[row["offset"]:row["offset"] + row["length"]]).decode()} for row in payload["files"]]}
            client = Client()
            settings = StudioSettings(project_dir=root / "member" / "sample", cache_dir=root / "cache", cad_export="off")
            app = create_app(settings)
            try:
                with self.assertRaises(StudioError):
                    pull_shared_project(app.state, client=client)
                self.assertFalse((settings.project_dir / "HEAD").exists())
                offset = SyncDownloadCache(settings.cache_dir / "sync").offset(artifact.sha256, len(content))
                self.assertGreater(offset, 0)
                client.fail = False
                client.calls = []
                result = pull_shared_project(app.state, client=client)
                self.assertLess(result.bytes_transferred, len(content))
                first = next(row for batch in client.calls for row in batch if row["sha256"] == artifact.sha256)
                self.assertEqual(first["offset"], offset)
                clone = FilesystemProjectRepository.open(settings.project_dir)
                self.assertEqual(clone.read_transfer_file(artifact.relative_path, artifact.sha256), content)
                client.calls = []
                self.assertEqual(pull_shared_project(app.state, client=client).bytes_transferred, 0)
                self.assertEqual(client.calls, [])
            finally:
                release_bound_project(app.state)
                app.state.jobs.shutdown()

    def test_partial_initial_install_resumes_original_snapshot_after_stage_advances(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = FilesystemProjectRepository.initialize(root / "owner" / "sample", project_id="sample", initial_state={})
            def stage(name, parent=None):
                run = repo.create_run(name)
                return repo.put_json(run=run, destination=PersistenceDestination(PersistenceArea.RUN_REVIEW, run_id=name),
                    record_kind=DESIGN_STAGE, payload={"schema": "DesignStage@1", "candidate_id": name,
                    "parent_stage": parent.to_dict() if parent else None})
            first = stage("s0")
            branch = {"branch_id": "main", "parent_branch": None, "fork_stage": first.to_dict(), "head_stage": first.to_dict()}
            repo.compare_and_swap_design_branch(branch_id="main", expected_head=None, branch=branch)
            class Client:
                project_id = "sample"
                def manifest(self): return repo.export_transfer(include_contents=False)
                def request(self, method, path, payload=None, **kwargs):
                    if path.endswith("identity"):
                        return {"actorId": "member", "name": "Member", "role": "viewer"}
                    if path.endswith("lines"):
                        return {"lines": []}
                    return {"files": [{**row, "content": base64.b64encode(repo.read_transfer_file(row["path"], row["sha256"])[row["offset"]:row["offset"] + row["length"]]).decode()} for row in payload["files"]]}
            settings = StudioSettings(project_dir=root / "member" / "sample", cache_dir=root / "cache", cad_export="off")
            app = create_app(settings)
            original = storage._write_immutable
            def interrupted(path, data):
                if Path(path) == settings.project_dir / "HEAD":
                    raise OSError("synthetic process interruption before HEAD")
                return original(path, data)
            try:
                with patch.object(storage, "_write_immutable", side_effect=interrupted), self.assertRaises(OSError):
                    pull_shared_project(app.state, client=Client())
                self.assertTrue((settings.project_dir / "design/branches.json").exists())
                self.assertFalse((settings.project_dir / "HEAD").exists())
                # A different replica interrupted before the mutable branch file
                # was cached has no installation journal and may refresh its manifest.
                downloading_settings = StudioSettings(project_dir=root / "downloading" / "sample", cache_dir=root / "download-cache", cad_export="off")
                downloading = create_app(downloading_settings)
                class InterruptedDownload(Client):
                    def request(self, method, path, payload=None, **kwargs):
                        if any(row["path"] == "design/branches.json" for row in payload["files"]):
                            raise StudioError(503, "SYNC_UNAVAILABLE", "interrupted before branch bytes")
                        return super().request(method, path, payload, **kwargs)
                with self.assertRaises(StudioError):
                    pull_shared_project(downloading.state, client=InterruptedDownload())
                self.assertFalse((downloading_settings.cache_dir / "sync/initial-transfer.json").exists())
                second = stage("s1", first)
                repo.compare_and_swap_design_branch(branch_id="main", expected_head=first, branch={**branch, "head_stage": second.to_dict()})
                try:
                    pull_shared_project(downloading.state, client=Client())
                    refreshed = FilesystemProjectRepository.open(downloading_settings.project_dir)
                    self.assertEqual(refreshed.read_design_branches()["main"]["head_stage"], second.to_dict())
                finally:
                    release_bound_project(downloading.state)
                    downloading.state.jobs.shutdown()
                app.state.settings = replace(settings, sync_url="http://127.0.0.1:1", sync_token="synthetic-token",
                    sync_project_id="sample", team_actor_id="member", team_role="viewer", sync_automatic=True,
                    team_state_file=root / "membership.json")
                worker = TeamSynchronization(app.state)
                with patch("project_runtime.synchronization.SharedProjectClient", return_value=Client()):
                    worker.step()
                    clone = FilesystemProjectRepository.open(settings.project_dir)
                    self.assertEqual(clone.read_design_branches()["main"]["head_stage"], first.to_dict())
                    self.assertIsNone(worker._last_manifest)
                    self.assertFalse((settings.cache_dir / "sync/initial-transfer.json").exists())
                    worker.step()
                    self.assertEqual(clone.read_design_branches()["main"]["head_stage"], second.to_dict())
            finally:
                release_bound_project(app.state)
                lease = getattr(app.state, "replica_writer_lease", None)
                if lease is not None: lease.release()
                app.state.jobs.shutdown()

    def test_corrupt_complete_cache_is_discarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = SyncDownloadCache(Path(temporary))
            digest = hashlib.sha256(b"good").hexdigest()
            cache.append(digest, 0, b"evil", 4)
            self.assertEqual(cache.offset(digest, 4), 0)
            cache.append(digest, 0, b"good", 4)
            self.assertEqual(cache.encoded(digest, 4), base64.b64encode(b"good").decode())
            with self.assertRaises(ValueError):
                cache.append("../escape", 0, b"bad", 3)
