from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import package_monkeyapps, workspace


class DevelopmentWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.work = self.root / "development space"
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Workspace Test", "GIT_AUTHOR_EMAIL": "workspace@example.test",
            "GIT_COMMITTER_NAME": "Workspace Test", "GIT_COMMITTER_EMAIL": "workspace@example.test",
        })
        environment.start()
        self.addCleanup(environment.stop)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.source)], check=True)
        (self.source / "model.txt").write_text("committed model\n", encoding="utf-8")
        workspace.git(self.source, "add", "model.txt")
        workspace.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qm", "initial")
        self.head = workspace.git(self.source, "rev-parse", "HEAD")
        self.call("configure", "--root", str(self.work))

    def call(self, *args: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(workspace.main(["--source-root", str(self.source), *args]), 0)
        return json.loads(output.getvalue())

    def test_create_and_reopen_in_new_process_preserve_both_worktrees(self) -> None:
        (self.source / "model.txt").write_text("parent WIP\n", encoding="utf-8")
        paths = self.call("create", "--branch", "codex/window-edit")
        target = Path(paths["worktreeDir"])
        self.assertEqual(target, self.work / "workspace/worktrees/codex-window-edit")
        self.assertEqual(workspace.git(target, "rev-parse", "HEAD"), self.head)
        self.assertEqual((target / "model.txt").read_text(encoding="utf-8"), "committed model\n")
        (target / "model.txt").write_text("task WIP\n", encoding="utf-8")
        result = subprocess.run([
            sys.executable, "-X", "utf8", str(Path(workspace.__file__).resolve()),
            "--source-root", str(self.source), "create", "--branch", "codex/window-edit",
        ], check=True, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(result.stdout), paths)
        self.assertEqual((target / "model.txt").read_text(encoding="utf-8"), "task WIP\n")
        self.assertEqual((self.source / "model.txt").read_text(encoding="utf-8"), "parent WIP\n")
        self.assertEqual(workspace.git(self.source, "branch", "--show-current"), "main")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(package_monkeyapps.main(["--source-root", str(target), "--show-paths"]), 0)
        packaged = json.loads(output.getvalue())
        for key in ("workspaceRoot", "task", "stagingDir", "outputDir", "cacheDir"):
            self.assertEqual(packaged[key], paths[key])
        self.assertFalse((self.work / "workspace/projects").exists())

    def test_existing_branch_and_explicit_new_base_are_preserved(self) -> None:
        workspace.git(self.source, "branch", "codex/retained")
        (self.source / "model.txt").write_text("second model\n", encoding="utf-8")
        workspace.git(self.source, "-c", "commit.gpgsign=false", "commit", "-qam", "second")
        retained = self.call("create", "--branch", "codex/retained")
        self.assertEqual(workspace.git(Path(retained["worktreeDir"]), "rev-parse", "HEAD"), self.head)
        old_base = self.call("create", "--branch", "codex/old-base", "--base", self.head)
        self.assertEqual(workspace.git(Path(old_base["worktreeDir"]), "rev-parse", "HEAD"), self.head)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call("create", "--branch", "codex/retained", "--base", "main")
        self.assertEqual(workspace.git(Path(retained["worktreeDir"]), "rev-parse", "HEAD"), self.head)

    def test_existing_external_branch_and_unknown_directory_are_not_modified(self) -> None:
        external = self.root / "existing-task"
        workspace.git(self.source, "worktree", "add", "-b", "codex/existing", str(external))
        (external / "model.txt").write_text("external WIP\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call("create", "--branch", "codex/existing")
        self.assertFalse(self.work.exists())
        self.assertEqual((external / "model.txt").read_text(encoding="utf-8"), "external WIP\n")
        target = self.work / "workspace/worktrees/codex-occupied"
        target.mkdir(parents=True)
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call("create", "--branch", "codex/occupied")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_invalid_destinations_and_refs_fail_before_writing(self) -> None:
        for args in (
            ("--branch", "codex/ok", "--task", "../projects"),
            ("--branch", "codex/ok", "--task", "NUL"),
            ("--branch", "codex/ok", "--task", "name."),
            ("--branch", "bad..ref"),
            ("--branch", "codex/ok", "--base", "missing-ref"),
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call("create", *args)
            self.assertFalse(self.work.exists())
        configured = (self.root / "gitconfig").read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call("configure", "--root", str(self.source / "nested"))
        self.assertEqual((self.root / "gitconfig").read_bytes(), configured)


if __name__ == "__main__":
    unittest.main()
