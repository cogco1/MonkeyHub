"""Real EXE handoff with private fake-Hub HTTP endpoints, never real shortcuts.

Provide MONKEYHUB_DESKTOP_EXE and MONKEYHUB_UPDATE_TARGET_EXE compiled from the
same sources with different full ARCHFLOW_SOURCE_REVISION values. Each test
uses disposable sibling installations and its own runtime/WebView directory.
The Hub fixture records complete/rollback instead of calling the installer.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from test_runtime import WindowsProcesses, wait_for


EXE = os.environ.get("MONKEYHUB_DESKTOP_EXE")
TARGET_EXE = os.environ.get("MONKEYHUB_UPDATE_TARGET_EXE")


@unittest.skipUnless(os.name == "nt" and EXE and TARGET_EXE, "Supply both exact-version Windows EXEs")
class DesktopUpdateHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="MonkeyHub native update ", delete=False)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.retained = self.runtime / "retained-user-data.txt"
        self.retained.write_bytes(b"unchanged user data")
        self.native = WindowsProcesses()
        self.children = []
        self.base_commit = self.revision(EXE)
        self.target_commit = self.revision(TARGET_EXE)
        self.assertNotEqual(self.base_commit, self.target_commit)
        self.base = self.bundle(EXE, self.base_commit)
        self.target = self.bundle(TARGET_EXE, self.target_commit)
        self.environment = dict(os.environ, PYTHONHOME=sys.base_prefix, PYTHONUTF8="1")

    @staticmethod
    def revision(exe):
        return json.loads(subprocess.check_output([exe, "--version"], creationflags=subprocess.CREATE_NO_WINDOW))["sourceRevision"]

    def bundle(self, exe, commit):
        directory = self.root / "versions" / (commit[:12] + "-desktop")
        (directory / "apps/monkeyhub/web/dist").mkdir(parents=True)
        (directory / "_runtime/python").mkdir(parents=True)
        shutil.copy2(exe, directory / "MonkeyHub.exe")
        shutil.copy2(Path(__file__).with_name("fake_hub.py"), directory / "apps/monkeyhub/run.py")
        (directory / "apps/monkeyhub/web/dist/index.html").write_text("<html>Fixture</html>")
        (directory / "source-version.txt").write_text(commit)
        (directory / "fixture.json").write_text("{}")
        # No installation or user Python is modified. PYTHONHOME locates only
        # the existing standard library; these executable bytes are disposable.
        shutil.copy2(sys.executable, directory / "_runtime/python/python.exe")
        for dll in Path(sys.executable).parent.glob("*.dll"):
            shutil.copy2(dll, directory / "_runtime/python" / dll.name)
        return directory

    def launch(self, command, *, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL):
        child = subprocess.Popen(command, env=self.environment, stdin=stdin, stdout=stdout,
                                 stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        self.children.append(child)
        return child

    def handoff(self):
        old = self.launch([str(self.base / "MonkeyHub.exe"), "--runtime-root", str(self.runtime),
                           "--startup-timeout-seconds", "3", "--update-trial"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        wait_for(lambda: any("state=verifying_update" in p.read_text(errors="replace")
                            for p in self.runtime.glob("logs/*.log")), "Old fixture did not become ready")
        ready = json.loads(old.stdout.readline())
        self.native.track(ready["processId"])
        self.native.track_webviews(old.pid)
        old.stdin.write(b"stop\n")
        old.stdin.flush()
        old.stdin.close()
        old.wait(timeout=15)
        old.stdout.close()
        self.assertTrue(self.native.exited(ready["processId"]))
        helper = self.launch([str(self.base / "MonkeyHub.exe"), "--complete-update", self.target_commit,
                              "--runtime-root", str(self.runtime), "--wait-for-desktop", str(old.pid),
                              "--startup-timeout-seconds", "3"])
        helper.wait(timeout=30)
        self.assertEqual(helper.returncode, 0)
        record = json.loads((self.runtime / "activation.json").read_text())
        self.native.track(record["desktopPid"])
        self.native.track(record["hubPid"])
        self.native.track_webviews(record["desktopPid"])
        wait_for(lambda: any(title == "MonkeyHub" for _, title in self.native.windows(record["desktopPid"])),
                 "Chosen desktop did not open after helper commit")
        self.assertEqual(self.retained.read_bytes(), b"unchanged user data")
        return record

    def test_healthy_new_desktop_completes_before_project_ui_opens(self):
        record = self.handoff()
        self.assertEqual(record["path"], "/api/updates/complete")
        self.assertEqual(record["payload"], {"fromCommit": self.base_commit})
        self.assertEqual(record["sourceRevision"], self.target_commit)

    def test_unstartable_target_reopens_old_desktop_and_restores_entry(self):
        (self.target / "MonkeyHub.exe").write_bytes(b"not an executable")
        record = self.handoff()
        self.assertEqual(record["path"], "/api/updates/rollback")
        self.assertEqual(record["payload"], {"fromCommit": self.base_commit, "targetCommit": self.target_commit})
        self.assertEqual(record["sourceRevision"], self.base_commit)

    def test_mismatched_target_binary_exits_without_a_blocking_dialog_and_rolls_back(self):
        shutil.copy2(self.base / "MonkeyHub.exe", self.target / "MonkeyHub.exe")
        record = self.handoff()
        self.assertEqual(record["path"], "/api/updates/rollback")
        self.assertEqual(record["sourceRevision"], self.base_commit)

    def test_activation_failure_drains_target_before_old_desktop_reopens(self):
        (self.target / "fixture.json").write_text('{"activation_failure": true, "drain_seconds": 0.3}')
        record = self.handoff()
        self.assertEqual(record["path"], "/api/updates/rollback")
        self.assertEqual(record["sourceRevision"], self.base_commit)
        text = "\n".join(path.read_text(errors="replace") for path in self.runtime.glob("logs/*.log"))
        self.assertIn("event=stop-request", text)
        self.assertIn("state=rolled-back", text)

    def tearDown(self):
        # WindowsProcesses holds handles only to this test's known children.
        for pid in list(self.native.handles):
            self.native.close_window(pid)
        for child in self.children:
            if child.poll() is None:
                self.native.track_webviews(child.pid)
                self.native.close_window(child.pid)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
        self.native.cleanup()
        deadline = time.monotonic() + 10
        while True:
            try:
                self.temporary.cleanup()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
