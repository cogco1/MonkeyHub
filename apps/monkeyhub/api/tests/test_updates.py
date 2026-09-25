"""Desktop patches use private version/runtime roots; no real shortcut is touched.

Automatic updates read GitHub through a fake opener; nothing here reaches the network.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import urllib.request

ROOT = Path(__file__).resolve().parents[4]
for directory in (ROOT, ROOT / "apps/archflow-studio/api", ROOT / "apps/monkeyhub/api"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from fastapi.testclient import TestClient
from apps.monkeyhub.installer.patch import REQUIRED_FILES, create_patch
from monkeyhub_api.main import HubSettings, create_app
from monkeyhub_api.models import HubFailure
from monkeyhub_api import updates as updates_module
from monkeyhub_api.updates import DesktopUpdates, ReleaseFeed, recover_failed_start

BASE, TARGET = "a" * 40, "b" * 40


class MemoryPreference:
    """自动更新 without touching the account's settings file."""

    def __init__(self, enabled=True):
        self.value, self.writes = enabled, []

    def enabled(self):
        return self.value

    def set(self, enabled):
        self.value = enabled
        self.writes.append(enabled)


class DesktopUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-update-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
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
        self.preference = MemoryPreference()
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

    def controller(self, source, **options):
        options.setdefault("preference", self.preference)
        return DesktopUpdates(source, self.runtime / "updates", managed=True, busy=lambda: self.reason, **options)

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

    @unittest.skipUnless(os.name == "nt", "Windows desktop source paths")
    def test_activation_command_preserves_drive_and_unc_locations(self):
        for source, expected in (
            (r"\\?\E:\MonkeyHub 安装", r"E:\MonkeyHub 安装"),
            (r"\\?\UNC\server\share\MonkeyHub", r"\\server\share\MonkeyHub"),
            (r"E:\MonkeyHub 安装", r"E:\MonkeyHub 安装"),
            (r"\\server\share\MonkeyHub", r"\\server\share\MonkeyHub"),
        ):
            with self.subTest(source=source):
                self.updates.source_root = Path(source)
                with patch("monkeyhub_api.updates.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
                    self.updates._activate()
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("-File") + 1],
                                 str(Path(expected) / "apps/monkeyhub/installer/install.ps1"))
                self.assertEqual(self.updates.source_root, Path(source), "the source identity is not rewritten")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell activation")
    def test_complete_and_rollback_activate_extended_path_with_real_powershell(self):
        # Use the actual installer and Windows PowerShell -File in a private
        # version tree. Only the shortcut destination is redirected for safety.
        versions = self.root / "desktop 安装 \U0001f333 path" / "versions"
        versions.mkdir(parents=True)
        self.base = Path(shutil.move(self.base, versions / self.base.name))
        for root in (self.base, self.target):
            script = root / "apps/monkeyhub/installer/install.ps1"
            script.parent.mkdir(parents=True, exist_ok=True)
            installer = (ROOT / "apps/monkeyhub/installer/install.ps1").read_text(encoding="ascii")
            # Preserve the real activation path while locating any host-specific
            # failure that the installer's outer catch would otherwise obscure.
            refusal = '    Write-Host "Refused: $($_.Exception.Message)"'
            self.assertEqual(installer.count(refusal), 1)
            script.write_text(installer.replace(refusal,
                '    Write-Host ("Activation fixture failure: " + $_.ScriptStackTrace + "`n" + $_.InvocationInfo.PositionMessage)\n' + refusal),
                encoding="ascii")
        self.zip.unlink()
        create_patch(self.base, self.target, self.zip)
        self.updates = self.controller(Path("\\\\?\\" + str(self.base)))
        target = self.prepare()
        self.updates.apply()
        before = {path.relative_to(versions): path.read_bytes()
                  for path in versions.rglob("*") if path.is_file()}
        desktop = self.root / "private-desktop 桌面 \U0001f333"
        desktop.mkdir()
        actual_run = subprocess.run
        activations = []

        def activate_in_fixture(command, **kwargs):
            self.assertEqual(command[0], "powershell.exe")
            result = actual_run([*command, "-DesktopDirectory", str(desktop)], **kwargs)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            link = actual_run([
                "powershell.exe", "-NoProfile", "-Command",
                "$folder = (New-Object -ComObject Shell.Application).NameSpace((Split-Path -Parent $env:MONKEYHUB_TEST_LINK)); "
                "$link = $folder.ParseName('MonkeyHub.lnk').GetLink; "
                "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($link.Path))",
            ], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
                env={**os.environ, "MONKEYHUB_TEST_LINK": str(desktop / "MonkeyHub.lnk")})
            self.assertEqual(link.returncode, 0, link.stderr)
            expected = (target if not activations else self.base) / "MonkeyHub.exe"
            self.assertEqual(Path(base64.b64decode(link.stdout.strip()).decode("utf-8")), expected)
            activations.append(result.stdout)
            return result

        trial = self.controller(Path("\\\\?\\" + str(target)))
        old = self.controller(Path("\\\\?\\" + str(self.base)))
        with patch("monkeyhub_api.updates.subprocess.run", side_effect=activate_in_fixture):
            self.assertEqual(trial.complete(BASE).state, "idle")
            self.assertEqual(old.rollback(BASE, TARGET).error.code, "UPDATE_ROLLED_BACK")
        self.assertEqual(trial.source_root, Path("\\\\?\\" + str(target)))
        self.assertEqual(old.source_root, Path("\\\\?\\" + str(self.base)))
        self.assertEqual(len(activations), 2)
        self.assertIn("Activated installed MonkeyHub source " + TARGET, activations[0])
        self.assertIn("Activated installed MonkeyHub source " + BASE, activations[1])
        self.assertTrue((desktop / "MonkeyHub.lnk").is_file())
        self.assertEqual(before, {path.relative_to(versions): path.read_bytes()
                                  for path in versions.rglob("*") if path.is_file()})

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
        # Status reads 自动更新 from the account settings, so keep them private.
        environment = patch.dict(os.environ, {"APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local")})
        environment.start()
        self.addCleanup(environment.stop)
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


INSTALLER = "apps/monkeyhub/installer/install.ps1"
OTHER = "c" * 40
VERSION, TAG = "0.1.6", "v0.1.6"


class FakeResponse(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


class FakeGitHub:
    """A ReleaseFeed opener: exact URLs answer fixed bytes; every request is kept."""

    def __init__(self):
        self.routes, self.seen = {}, []

    @property
    def requests(self):
        return [url for url, _ in self.seen]

    def open(self, request, timeout=None):
        self.seen.append((request.full_url, dict(request.header_items())))
        route = self.routes.get(request.full_url)
        if route is None:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        return route(request) if callable(route) else FakeResponse(route)


def listed(version, assets, *, prerelease=True, draft=False):
    return {"tag_name": f"v{version}", "prerelease": prerelease, "draft": draft,
            "assets": [{"name": name, "size": len(data)} for name, data in assets.items()]}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def wait_until(condition, message, timeout=20):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(message)
        time.sleep(0.02)


class AutomaticUpdateTests(unittest.TestCase):
    """The unsigned prerelease channel, from the check to the next start."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hub-auto-update-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.versions = self.root / "versions"
        self.base = self.versions / (BASE[:12] + "-desktop")
        complete = self.root / "complete-target"
        for name in (*REQUIRED_FILES, INSTALLER):
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.identity(self.base, BASE, "0.1.4")
        shutil.copytree(self.base, complete)
        (complete / "MonkeyHub.exe").write_bytes(b"desktop 0.1.6")
        self.identity(complete, TARGET, VERSION)
        self.build_info = (complete / "build-info.json").read_bytes()
        self.patch_file = self.root / "from-base.zip"
        create_patch(self.base, complete, self.patch_file)
        self.target = self.versions / (TARGET[:12] + "-desktop")
        self.runtime = self.root / "runtime"
        self.preference = MemoryPreference()
        self.github = FakeGitHub()
        self.publish()
        self.processes, self.activation_code = [], 0

        def run(command, **options):
            self.processes.append(command)
            if command[0] == "powershell.exe":
                return SimpleNamespace(returncode=self.activation_code, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="usage: run.py [-h] [--runtime-root RUNTIME_ROOT]", stderr="")

        for patcher in (patch("monkeyhub_api.updates.subprocess.run", side_effect=run),
                        patch.object(updates_module, "FIRST_CHECK_SECONDS", 0.05),
                        patch.object(updates_module, "TRIAL_GRACE_SECONDS", 0.2)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def identity(root, commit, version):
        (root / "source-version.txt").write_text(commit)
        (root / "build-info.json").write_text(json.dumps({
            "sourceCommit": commit, "target": "windows-x64", "releaseVersion": version,
            "desktop": {"sourceCommit": commit, "version": version,
                        "executableSha256": hashlib.sha256((root / "MonkeyHub.exe").read_bytes()).hexdigest()},
        }))

    def publish(self, *, patches=None, patch_bytes=None, index=True):
        """Release v0.1.6 as the release workflow does, beside older and unusable releases."""
        data = self.patch_file.read_bytes() if patch_bytes is None else patch_bytes
        mine, other = f"MonkeyHub-{VERSION}-from-0.1.4.patch.zip", f"MonkeyHub-{VERSION}-from-0.1.5.patch.zip"
        manifest_name = f"MonkeyHub-{VERSION}-windows-x64-candidate.zip.release-manifest.json"
        manifest = json.dumps({"schema": "ReleaseManifest@1",
                               "release": {"version": VERSION, "channel": "candidate", "target": "windows-x64",
                                           "sourceCommit": TARGET},
                               "buildInfo": {"sha256": sha256(self.build_info)}}).encode()
        rows = [{"name": other, "size": 3, "sha256": sha256(b"old"), "baseVersion": "0.1.5", "baseCommit": OTHER},
                {"name": mine, "size": len(self.patch_file.read_bytes()), "sha256": sha256(self.patch_file.read_bytes()),
                 "baseVersion": "0.1.4", "baseCommit": BASE}]
        document = {"schema": "MonkeyHubUpdateIndex@1", "channel": "unsigned-prerelease", "version": VERSION,
                    "sourceCommit": "d" * 40, "releaseCommit": TARGET,
                    "releaseManifest": {"name": manifest_name, "size": len(manifest), "sha256": sha256(manifest)},
                    "patches": rows if patches is None else [row for row in rows if row["baseCommit"] in patches]}
        assets = {manifest_name: manifest, mine: data, other: b"old"}
        if index:
            assets[f"MonkeyHub-{VERSION}-update-index.json"] = json.dumps(document).encode()
        releases = json.dumps([
            listed("0.1.9", {}, draft=True), listed("0.1.8", {}, prerelease=False), listed(VERSION, assets),
            listed("0.1.5", {"MonkeyHub-0.1.5-update-index.json": b"{}"}), listed("0.1.3", {}),
        ]).encode()

        def api(request):
            if request.get_header("If-none-match") == '"listing"':
                raise HTTPError(request.full_url, 304, "Not Modified", {}, None)
            return FakeResponse(releases, {"ETag": '"listing"'})

        self.github.routes = {ReleaseFeed.API: api}
        for name, content in assets.items():
            self.github.routes[ReleaseFeed.url(TAG, name)] = content

    def controller(self, source=None, **options):
        options.setdefault("preference", self.preference)
        options.setdefault("feed", ReleaseFeed("MonkeyHub-test", opener=self.github))
        updates = DesktopUpdates(source or self.base, self.runtime / "updates", managed=True, busy=lambda: None, **options)
        self.addCleanup(updates.shutdown)
        return updates

    def record(self):
        # Read only while no update thread writes: Windows refuses to replace
        # a file another reader holds open, as it would for a second Hub.
        return json.loads((self.runtime / "updates/state.json").read_text(encoding="utf-8"))

    def activation(self, root):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / INSTALLER),
                "-ActivateInstalled", "-CreateDesktopShortcut"]

    def activated(self):
        """What the previous version does: prepare automatically, then quit normally."""
        updates = self.controller()
        updates._run_check(explicit=False)
        updates.shutdown()
        updates.activate_on_quit()
        self.assertEqual(self.record()["state"], "activated")
        self.processes.clear()

    def test_newest_greater_prerelease_for_this_base_is_downloaded_verified_and_prepared(self):
        updates = self.controller()
        self.assertEqual(updates.status().check.state, "never")
        updates._run_check(explicit=False)
        status = updates.status()
        self.assertEqual(status.state, "ready", status)
        self.assertEqual((status.check.state, status.check.latestVersion), ("ready", VERSION))
        self.assertEqual((status.prepared.targetRevision, status.prepared.releaseVersion), (TARGET, VERSION))
        self.assertEqual((status.channel, status.autoUpdate, status.nextLaunch, status.releaseVersion),
                         ("unsigned-prerelease", True, True, "0.1.4"))
        downloads = [url.rsplit("/", 1)[-1] for url in self.github.requests if "/releases/download/" in url]
        self.assertEqual(downloads, [f"MonkeyHub-{VERSION}-update-index.json",
                                     f"MonkeyHub-{VERSION}-windows-x64-candidate.zip.release-manifest.json",
                                     f"MonkeyHub-{VERSION}-from-0.1.4.patch.zip"])
        self.assertTrue(all(url.startswith("https://") for url in self.github.requests))
        self.assertEqual(self.processes, [[str(self.target / "_runtime/python/python.exe"), "-B",
                                           str(self.target / "apps/monkeyhub/run.py"), "--help"]])
        record = self.record()
        self.assertEqual((record["state"], record["source"], record["preflight"]), ("ready", "auto", "passed"))
        self.assertTrue((self.target / "MonkeyHub.exe").is_file())
        # The next check reads the listing with its ETag and downloads nothing again.
        seen = len(self.github.seen)
        updates._run_check(explicit=True)
        self.assertEqual(updates.status().check.state, "ready")
        self.assertEqual(self.github.seen[seen][1].get("If-none-match"), '"listing"')
        self.assertFalse(any(url.endswith(".patch.zip") for url in self.github.requests[seen:]))

    def test_a_patch_that_does_not_match_its_sha256_is_refused_before_staging(self):
        changed = bytearray(self.patch_file.read_bytes())
        changed[-1] ^= 1
        self.publish(patch_bytes=bytes(changed))
        updates = self.controller()
        updates._run_check(explicit=False)
        status = updates.status()
        self.assertEqual((status.state, status.error.code, status.check.state), ("failed", "UPDATE_DOWNLOAD_REFUSED", "error"))
        self.assertIn("SHA-256", status.error.detail)
        self.assertEqual(list(self.versions.iterdir()), [self.base])
        self.assertFalse(any(path.name.startswith("incoming-") for path in (self.runtime / "updates").iterdir()))
        self.assertFalse((self.runtime / "updates/state.json").exists())
        self.assertEqual(self.processes, [])

    def test_no_patch_from_this_version_reports_a_needed_full_update(self):
        for label, options in (("other base only", {"patches": [OTHER]}), ("no update index", {"index": False})):
            with self.subTest(label):
                self.publish(**options)
                shutil.rmtree(self.runtime, ignore_errors=True)
                updates = self.controller()
                updates._run_check(explicit=False)
                status = updates.status()
                self.assertEqual((status.state, status.check.state, status.check.latestVersion),
                                 ("idle", "needs-full-update", VERSION))
                self.assertEqual(status.check.releaseUrl, f"https://github.com/cogco1/MonkeyHub/releases/tag/{TAG}")
                self.assertFalse(any(url.endswith(".patch.zip") for url in self.github.requests))
                self.assertEqual(list(self.versions.iterdir()), [self.base])

    def test_the_newest_installed_release_is_up_to_date(self):
        self.identity(self.base, BASE, "0.1.7")
        updates = self.controller()
        updates._run_check(explicit=False)
        self.assertEqual((updates.status().check.state, updates.status().check.latestVersion), ("up-to-date", VERSION))
        self.assertEqual(self.github.requests, [ReleaseFeed.API])

    def test_no_check_runs_while_an_update_is_preparing_or_applying(self):
        updates = self.controller()
        updates._preparing = True
        updates._run_check(explicit=True)
        with self.assertRaises(HubFailure) as refused:
            updates.check_now()
        self.assertEqual((refused.exception.error.code, self.github.requests), ("UPDATE_BUSY", []))
        updates._preparing = False
        updates._run_check(explicit=False)
        self.assertEqual(updates.apply().state, "applying")
        seen = len(self.github.requests)
        updates._run_check(explicit=True)
        with self.assertRaises(HubFailure):
            updates.check_now()
        self.assertEqual(len(self.github.requests), seen)

    def test_an_automatic_check_skipped_while_busy_runs_again_soon(self):
        updates = self.controller()
        updates._preparing = True
        with patch.object(updates_module, "BUSY_RETRY_SECONDS", 0.1):
            updates.start(9, "instance")
            time.sleep(0.3)
            self.assertEqual(self.github.requests, [], "no check while a transaction is busy")
            updates._preparing = False
            wait_until(lambda: updates.status().check.state == "ready", "the check did not run again")

    def test_the_automatic_setting_is_honoured(self):
        self.preference.value = False
        updates = self.controller()
        updates.start(9, "instance")
        time.sleep(0.3)
        self.assertEqual(self.github.requests, [], "no automatic check while it is off")
        self.assertFalse(updates.status().autoUpdate)
        updates.check_now()
        wait_until(lambda: updates.status().check.state == "ready", "an explicit check still prepares")
        self.assertFalse(updates.status().nextLaunch)
        updates.activate_on_quit()
        self.assertFalse(any(command[0] == "powershell.exe" for command in self.processes))
        self.assertEqual(self.record()["state"], "ready", "only restart-now applies it while off")
        self.assertTrue(updates.set_auto_update(True).nextLaunch)
        self.assertEqual(self.preference.writes, [True])

    def test_a_normal_quit_switches_the_entry_through_the_installer(self):
        updates = self.controller()
        updates._run_check(explicit=False)
        self.processes.clear()
        updates.shutdown()
        updates.activate_on_quit()
        self.assertEqual(self.processes, [self.activation(self.target)])
        record = self.record()
        self.assertEqual((record["state"], record["launchAttempts"]), ("activated", 0))
        # A version that is already switched in is not switched again.
        self.processes.clear()
        updates.activate_on_quit()
        self.assertEqual(self.processes, [])

    def test_a_chosen_patch_runs_its_preflight_and_a_changed_one_is_not_switched_in(self):
        updates = self.controller()
        uploaded = updates.begin_upload()
        uploaded.write_bytes(self.patch_file.read_bytes())
        updates.prepare(uploaded)
        updates._worker.join()
        self.assertEqual(updates.status().state, "ready")
        self.activation_code = 1
        with self.assertLogs("monkeyhub_api.updates", "WARNING"):
            updates.activate_on_quit()
        self.assertEqual([command[0] for command in self.processes],
                         [str(self.target / "_runtime/python/python.exe"), "powershell.exe"])
        self.assertEqual(self.record()["state"], "ready")
        self.assertIn("desktop entry", updates.status().message)
        self.processes.clear()
        self.activation_code = 0
        (self.target / INSTALLER).write_bytes(b"changed installer")
        with self.assertLogs("monkeyhub_api.updates", "WARNING") as logged:
            updates.activate_on_quit()
        self.assertIn("Prepared version file", logged.output[0])
        self.assertEqual(self.processes, [], "a changed installer is never run")
        self.assertEqual(self.record()["state"], "ready")

    def test_the_next_start_finishes_its_own_activation_without_refusing_writes(self):
        self.activated()
        started = self.controller(self.target)
        self.assertEqual(self.record()["launchAttempts"], 1)
        answered = threading.Event()
        with patch.object(started, "_own_health", side_effect=lambda port, instance: answered.wait(10)):
            started.start(9123, "instance")
            with started.mutation():
                pass
            self.assertIn("Finishing", started.status().message)
            with self.assertRaises(HubFailure):
                started.check_now()
            answered.set()
            wait_until(lambda: started._record.get("state") == "applied", "the new version did not finish")
        wait_until(lambda: not started._finishing, "finishing did not end")
        self.assertEqual(self.processes, [self.activation(self.target)])
        self.assertEqual(self.record()["launchAttempts"], 0)
        self.assertIsNone(started.status().error)

    def test_a_failed_health_check_restores_the_previous_entry(self):
        self.activated()
        started = self.controller(self.target)
        with patch.object(started, "_own_health", return_value=False):
            started.start(9123, "instance")
            wait_until(lambda: started._record.get("state") == "failed", "the previous entry was not restored")
        wait_until(lambda: not started._finishing, "finishing did not end")
        self.assertEqual(self.processes, [self.activation(self.base)])
        self.assertEqual(started.status().error.code, "UPDATE_ROLLED_BACK")
        with started.mutation():
            pass
        previous = self.controller()
        self.assertEqual(previous.status().error.code, "UPDATE_ROLLED_BACK")
        seen = len(self.github.requests)
        previous._run_check(explicit=False)
        self.assertEqual(previous.status().check.state, "error")
        self.assertFalse(any(url.endswith(".patch.zip") for url in self.github.requests[seen:]),
                         "a release that failed here is not downloaded again automatically")

    def test_a_restart_now_start_left_applying_finishes_or_rolls_back_itself(self):
        for healthy in (True, False):
            with self.subTest(healthy=healthy):
                shutil.rmtree(self.runtime, ignore_errors=True)
                self.processes.clear()
                updates = self.controller()
                updates._run_check(explicit=False)
                updates.apply()
                started = self.controller(self.target)
                with self.assertRaises(HubFailure):
                    with started.mutation():
                        pass
                with patch.object(started, "_own_health", return_value=healthy):
                    started.start(9123, "instance")
                    wait_until(lambda: started._record.get("state") in {"applied", "failed"}, "the start stayed applying")
                wait_until(lambda: not started._finishing, "finishing did not end")
                with started.mutation():
                    pass
                self.assertEqual(self.record()["state"], "applied" if healthy else "failed")

    def test_the_previous_version_undoes_a_start_that_never_finished(self):
        self.activated()
        previous = self.controller()
        previous.start(9123, "instance")
        time.sleep(0.2)
        self.assertEqual(self.record()["state"], "activated", "a start through another entry is not a failure")
        self.assertTrue(previous.status().nextLaunch)
        previous.shutdown()
        self.controller(self.target)  # Counts one start of the new version, which then dies.
        self.assertEqual(self.record()["launchAttempts"], 1)
        previous = self.controller()
        previous.start(9123, "instance")
        wait_until(lambda: previous._record.get("state") == "failed", "the unfinished start was not undone")
        self.assertEqual(self.processes, [self.activation(self.base)])
        wait_until(lambda: previous.status().error is not None, "no error recorded")
        self.assertEqual(previous.status().error.code, "UPDATE_ROLLED_BACK")

    def test_a_start_that_fails_before_serving_restores_the_previous_entry(self):
        self.activated()
        recover_failed_start(self.target, self.runtime, managed=True, reason="ImportError: fixture")
        record = self.record()
        self.assertEqual((record["state"], record["error"]["code"]), ("failed", "UPDATE_ROLLED_BACK"))
        self.assertIn("ImportError: fixture", record["error"]["detail"])
        self.assertEqual(self.processes, [self.activation(self.base)])

    def test_own_health_accepts_only_this_process_revision_and_instance(self):
        updates = self.controller()
        answer = {}

        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        answer.update(status="ok", service="monkeyhub-api", processId=os.getpid(), sourceRevision=BASE,
                      managedInstanceId="mine")
        self.assertIs(updates._own_health(port, "mine"), True)
        self.assertIs(updates._own_health(port, "another"), False)
        answer.update(sourceRevision=TARGET)
        self.assertIs(updates._own_health(port, "mine"), False)
        answer.update(sourceRevision=BASE, processId=os.getpid() + 1)
        self.assertIs(updates._own_health(port, "mine"), False)

    def test_redirects_stay_on_https(self):
        handler = updates_module._HttpsRedirects()
        request = urllib.request.Request(ReleaseFeed.url(TAG, "asset.zip"))
        with self.assertRaises(HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, "http://objects.githubusercontent.com/asset")
        self.assertEqual(handler.redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/a").full_url,
                         "https://release-assets.githubusercontent.com/a")

    def test_check_and_settings_routes(self):
        environment = patch.dict(os.environ, {"APPDATA": str(self.root / "roaming"), "LOCALAPPDATA": str(self.root / "local")})
        environment.start()
        self.addCleanup(environment.stop)
        app = create_app(HubSettings(runtime_root=self.runtime, port=9124, managed_instance_id="auto-update"), source_root=self.base)
        app.state.updates._feed = ReleaseFeed("MonkeyHub-test", opener=self.github)
        self.addCleanup(app.state.updates.shutdown)
        client = TestClient(app, base_url="http://127.0.0.1:9124")
        self.addCleanup(client.close)
        status = client.get("/api/updates/status").json()
        self.assertEqual((status["channel"], status["autoUpdate"], status["check"]["state"], status["releaseVersion"]),
                         ("unsigned-prerelease", True, "never", "0.1.4"))
        self.assertEqual(client.put("/api/updates/settings", json={"autoUpdate": "off"}).status_code, 422)
        self.assertIs(client.put("/api/updates/settings", json={"autoUpdate": False}).json()["autoUpdate"], False)
        saved = json.loads((self.root / "roaming/MonkeyArch/settings.json").read_text(encoding="utf-8"))
        self.assertIs(saved["autoUpdate"], False)
        self.assertIs(client.get("/api/settings/user").json()["autoUpdate"], False)
        response = client.post("/api/updates/check")
        self.assertEqual(response.status_code, 202, response.text)
        wait_until(lambda: client.get("/api/updates/status").json()["check"]["state"] == "ready", "check did not finish")
        status = client.get("/api/updates/status").json()
        self.assertEqual((status["state"], status["nextLaunch"]), ("ready", False))
        # Turning it back on removes the field, so older versions still read the file.
        self.assertIs(client.put("/api/updates/settings", json={"autoUpdate": True}).json()["nextLaunch"], True)
        saved = json.loads((self.root / "roaming/MonkeyArch/settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("autoUpdate", saved)


if __name__ == "__main__":
    unittest.main()
