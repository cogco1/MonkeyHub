"""Desktop patches use private version/runtime roots; no real shortcut is touched."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient
from apps.monkeyhub.installer.patch import REQUIRED_FILES, create_patch
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import HubFailure
from monkeyhub_api.updates import DesktopUpdates

BASE, TARGET = "a" * 40, "b" * 40


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-update-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "versions" / (BASE[:12] + "-desktop")
        self.target = self.root / "complete-target"
        for name in REQUIRED_FILES:
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.identity(self.base, BASE)
        shutil.copytree(self.base, self.target)
        (self.target / "MonkeyHub.exe").write_bytes(b"new desktop executable")
        self.identity(self.target, TARGET)
        self.zip = self.root / "update.zip"
        create_patch(self.base, self.target, self.zip)
        self.runtime = self.root / "private-runtime"
        self.reason = None
        self.updates = self.controller(self.base)
        self.addCleanup(self.updates.shutdown)

    @staticmethod
    def identity(root, commit):
        (root / "source-version.txt").write_text(commit)
        (root / "build-info.json").write_text(json.dumps({
            "sourceCommit": commit, "target": "windows-x64",
            "desktop": {"sourceCommit": commit,
                        "executableSha256": hashlib.sha256((root / "MonkeyHub.exe").read_bytes()).hexdigest()},
        }))

    def controller(self, source):
        return DesktopUpdates(source, self.runtime / "updates", managed=True, busy=lambda: self.reason)

    def prepare(self):
        uploaded = self.updates.begin_upload()
        uploaded.write_bytes(self.zip.read_bytes())
        self.updates.prepare(uploaded)
        self.updates.shutdown()
        self.assertEqual(self.updates.status().state, "ready", self.updates.status())
        return self.base.parent / (TARGET[:12] + "-desktop")

    def assert_failure(self, code, operation):
        with self.assertRaises(HubFailure) as result:
            operation()
        self.assertEqual(result.exception.error.code, code)

    def test_prepare_restart_commit_and_reopen_preserve_unrelated_runtime_data(self):
        transcript = self.runtime / "chats" / "retained.json"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("retained conversation and attachment references")
        target = self.prepare()
        # Preparation survives normal application shutdown and reopen.
        self.updates = self.controller(self.base)
        self.assertTrue(self.updates.status().canApply)
        status = self.updates.apply()
        self.assertEqual(status.state, "applying")
        self.assertEqual(self.updates.restart(), {"targetCommit": TARGET})
        with self.assertRaises(HubFailure):
            with self.updates.mutation():
                self.fail("new write admitted during restart")
        trial = self.controller(target)
        self.assertEqual(trial.status().state, "applying")
        with patch.object(trial, "_activate") as activate:
            self.assertEqual(trial.complete(BASE).state, "idle")
            self.assertEqual(trial.complete(BASE).state, "idle")
            activate.assert_called_once()
        reopened = self.controller(target)
        self.assertEqual(reopened.status().currentRevision, TARGET)
        self.assertEqual(reopened.status().state, "idle")
        with reopened.mutation():
            pass
        self.assertEqual(transcript.read_text(), "retained conversation and attachment references")

    def test_busy_work_or_inflight_request_prevents_restart_without_losing_preparation(self):
        self.prepare()
        self.reason = "A conversation is running."
        self.assertFalse(self.updates.status().canApply)
        self.assert_failure("UPDATE_WORK_RUNNING", self.updates.apply)
        self.reason = None
        with self.updates.mutation():
            self.assert_failure("UPDATE_WORK_RUNNING", self.updates.apply)
        self.assertTrue(self.updates.status().canApply)
        self.assertEqual(self.updates.apply().state, "applying")

    def test_retry_after_staged_version_survives_failed_state_save_and_reopen(self):
        target = self.base.parent / (TARGET[:12] + "-desktop")
        uploaded = self.updates.begin_upload()
        uploaded.write_bytes(self.zip.read_bytes())

        def failed_save(record):
            self.assertEqual(record["state"], "ready")
            # The real stage_patch has already renamed its complete directory.
            self.assertTrue(target.is_dir())
            self.assertEqual((target / "MonkeyHub.exe").read_bytes(),
                             (self.target / "MonkeyHub.exe").read_bytes())
            raise OSError("temporary failure saving update state")

        with patch.object(self.updates, "_save", side_effect=failed_save):
            self.updates.prepare(uploaded)
            self.updates.shutdown()
        self.assertEqual(self.updates.status().state, "failed")
        self.assertFalse(uploaded.parent.exists())
        self.assertFalse((self.updates.directory / "state.json").exists())
        staged_files = {path.relative_to(target): (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in target.rglob("*") if path.is_file()}
        staged_identity = target.stat().st_ino

        # No retained update transaction exists in the reopened old application.
        self.updates = self.controller(self.base)
        self.addCleanup(self.updates.shutdown)
        self.assertEqual(self.updates.status().state, "idle")
        retry = self.updates.begin_upload()
        retry.write_bytes(self.zip.read_bytes())
        self.updates.prepare(retry)
        self.updates.shutdown()
        self.assertEqual(self.updates.status().state, "ready", self.updates.status())
        self.assertTrue(self.updates.status().canApply)
        self.assertEqual(target.stat().st_ino, staged_identity)
        self.assertEqual(staged_files, {path.relative_to(target): (path.read_bytes(), path.stat().st_mtime_ns)
                                       for path in target.rglob("*") if path.is_file()})
        saved = json.loads((self.updates.directory / "state.json").read_text())
        self.assertEqual(saved["patch"], retry.parent.name)
        self.assertTrue(retry.is_file())
        self.assertEqual(self.updates.apply().state, "applying")
        self.assertEqual(self.updates.restart(), {"targetCommit": TARGET})

    def test_modified_prepared_version_is_refused_at_restart(self):
        target = self.prepare()
        (target / "MonkeyHub.exe").write_bytes(b"modified after preparation")
        self.assert_failure("UPDATE_REVALIDATION_FAILED", self.updates.apply)
        self.assertIsNone(self.updates.restart()["targetCommit"])
        with self.updates.mutation():
            pass

    def test_failed_startup_rolls_back_and_retry_clears_persisted_error(self):
        target = self.prepare()
        self.updates.apply()
        old = self.controller(self.base)
        self.assertEqual(old.status().error.code, "UPDATE_INCOMPLETE")
        with patch.object(old, "_activate") as activate:
            restored = old.rollback(BASE, TARGET)
            activate.assert_called_once()
        self.assertEqual(restored.error.code, "UPDATE_ROLLED_BACK")
        self.assertEqual(restored.state, "failed")
        with old.mutation():
            pass
        self.assertIsNone(old.restart()["targetCommit"])
        self.assertEqual(old.apply().state, "applying")
        trial = self.controller(target)
        with patch.object(trial, "_activate"):
            trial.complete(BASE)
        self.assertIsNone(self.controller(target).status().error)

    def test_activation_failure_leaves_trial_blocked_and_old_transaction_recoverable(self):
        target = self.prepare()
        self.updates.apply()
        trial = self.controller(target)
        with patch.object(trial, "_activate", side_effect=HubFailure(503, "UPDATE_ACTIVATION_FAILED", "fixture failure")):
            self.assert_failure("UPDATE_ACTIVATION_FAILED", lambda: trial.complete(BASE))
        self.assertEqual(trial.status().state, "applying")
        self.assert_failure("UPDATE_TRANSACTION_MISMATCH", lambda: trial.complete("c" * 40))
        old = self.controller(self.base)
        with patch.object(old, "_activate"):
            self.assertEqual(old.rollback(BASE, TARGET).state, "failed")

    def test_invalid_upload_cleanup_cannot_delete_an_unrelated_directory(self):
        protected = self.root / "other" / "private.txt"
        protected.parent.mkdir()
        protected.write_text("keep")
        self.updates.fail_upload(protected, "invalid")
        self.assertEqual(protected.read_text(), "keep")
        uploaded = self.updates.begin_upload()
        uploaded.write_bytes(b"not a zip")
        self.updates.prepare(uploaded)
        self.updates.shutdown()
        self.assertEqual(self.updates.status().error.code, "UPDATE_PATCH_INVALID")
        self.assertFalse(uploaded.parent.exists())
        self.assertEqual(list(self.base.parent.iterdir()), [self.base])

    def test_api_upload_origin_size_and_restart_admission(self):
        with patch.dict(os.environ, {"APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local")}):
            app = create_app(HubSettings(runtime_root=self.runtime, port=9123, managed_instance_id="test-update"), source_root=self.base)
        self.addCleanup(app.state.updates.shutdown)
        client = TestClient(app, base_url="http://127.0.0.1:9123")
        self.addCleanup(client.close)
        url = "/api/updates/prepare"
        data = self.zip.read_bytes()
        self.assertEqual(client.post(url, content=data).status_code, 422)
        self.assertEqual(client.post(url, content=data, headers={"origin": "https://outside.invalid"}).status_code, 403)
        headers = {"X-MonkeyHub-Local-Patch": "1", "Content-Type": "application/octet-stream"}
        with patch("monkeyhub_api.main.MAX_PATCH_BYTES", 1):
            self.assertEqual(client.post(url, content=data, headers=headers).status_code, 413)
        self.assertEqual(client.post(url, content=b"", headers=headers).status_code, 422)
        response = client.post(url, content=data, headers=headers)
        self.assertEqual(response.status_code, 202, response.text)
        app.state.updates.shutdown()
        self.assertEqual(client.get("/api/updates/status").json()["state"], "ready")
        with patch.object(app.state.chats, "list", return_value=[SimpleNamespace(status="running")]):
            self.assertEqual(client.post("/api/updates/apply", json={}).status_code, 409)
        with patch.object(app.state.runtimes, "snapshot", return_value=SimpleNamespace(projects=[
            SimpleNamespace(operations=[], retained=SimpleNamespace(jobs=[SimpleNamespace(status="running")]))
        ])):
            self.assertEqual(client.post("/api/updates/apply", json={}).status_code, 409)
        self.assertEqual(client.post("/api/updates/apply", json={}).status_code, 202)
        self.assertEqual(client.get("/api/updates/restart").json(), {"targetCommit": TARGET})
        self.assertEqual(client.post("/api/apps/monkeymonitor/start").json()["code"], "UPDATE_RESTARTING")
        self.assertEqual(client.get("/api/health").status_code, 200)

    def test_unmanaged_source_cannot_upload_and_corrupt_state_does_not_trigger_restart(self):
        self.assertEqual(self.controller(self.target).status().mode, "unsupported")
        unmanaged = DesktopUpdates(self.base, self.runtime / "updates", managed=False, busy=lambda: None)
        self.assertEqual(unmanaged.status().mode, "unsupported")
        self.assert_failure("UPDATE_UNSUPPORTED", unmanaged.begin_upload)
        self.updates.directory.mkdir(parents=True)
        (self.updates.directory / "state.json").write_text('{"targetCommit":"../../foreign"}')
        restarted = self.controller(self.base)
        self.assertIsNone(restarted.restart()["targetCommit"])
        self.assertEqual(restarted.status().error.code, "UPDATE_STATE_UNREADABLE")


if __name__ == "__main__":
    unittest.main()
