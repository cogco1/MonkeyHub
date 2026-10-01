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

from tools.dev import workspace
from tools.release import package_monkeyapps


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


class RetireWorktreeTests(unittest.TestCase):
    """A bare origin, a main checkout pushing to it and a development root, all temporary."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Workspace Test", "GIT_AUTHOR_EMAIL": "workspace@example.test",
            "GIT_COMMITTER_NAME": "Workspace Test", "GIT_COMMITTER_EMAIL": "workspace@example.test",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.origin, self.source = self.tmp / "origin.git", self.tmp / "source"
        self.root = self.tmp / "development space"
        self.worktrees = self.root / "workspace" / "worktrees"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.source)], check=True)
        (self.source / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        (self.source / "model.txt").write_text("committed model\n", encoding="utf-8")
        self.git("add", ".gitignore", "model.txt")
        self.git("commit", "-qm", "initial")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-q", "-u", "origin", "main")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return workspace.git(cwd or self.source, "-c", "commit.gpgsign=false", *args)

    def task(self, branch: str, *, merge: bool = True) -> Path:
        """A task worktree with one commit, merged into origin/main unless merge is false."""
        name = branch.replace("/", "-")
        path = workspace.create_worktree(self.source, self.worktrees / name, branch)
        (path / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        self.git("add", f"{name}.txt", cwd=path)
        self.git("commit", "-qm", name, cwd=path)
        if merge:
            self.git("merge", "-q", "--no-ff", "--no-edit", branch)
            self.git("push", "-q", "origin", "main")
        return path

    def retire(self, **options) -> dict:
        return workspace.retire_worktrees(self.source, self.root, **options)

    def cli(self, *args: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = workspace.main(["--source-root", str(self.source), *args])
        return code, json.loads(output.getvalue())

    @staticmethod
    def by_name(report: dict) -> dict[str, dict]:
        return {Path(entry["path"]).name: entry for entry in report["worktrees"]}

    def registered(self) -> set[Path]:
        lines = self.git("worktree", "list", "--porcelain").splitlines()
        return {Path(line.removeprefix("worktree ")).resolve() for line in lines if line.startswith("worktree ")}

    def branches(self) -> set[str]:
        return set(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())

    def snapshot(self, *directories: Path) -> tuple:
        files = sorted(
            path.relative_to(self.tmp).as_posix()
            for directory in (self.root, *directories) if directory.exists() for path in directory.rglob("*")
        )
        return (self.git("worktree", "list", "--porcelain"), self.git("for-each-ref", "refs/heads"),
                self.git("stash", "list"), files)

    def test_dry_run_reports_each_task_worktree_and_changes_nothing(self) -> None:
        self.task("codex/done")
        self.task("codex/open", merge=False)
        (self.task("codex/dirty") / "notes.txt").write_text("WIP\n", encoding="utf-8")
        self.git("worktree", "add", "-q", "--detach", str(self.worktrees / "review-detached"))
        self.git("branch", "codex/stale")
        elsewhere = workspace.create_worktree(self.source, self.tmp / "elsewhere" / "gone", "codex/elsewhere")
        elsewhere.rename(self.tmp / "elsewhere" / "renamed")
        before = self.snapshot()
        report = self.retire()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.root.glob("_TRASH_*")))
        self.assertEqual((report["fetch"], report["apply"]), ("ok", False))
        self.assertEqual(report["staleOutsideRoot"], [str(elsewhere)])
        entries = self.by_name(report)
        self.assertEqual({name: (entry["state"], entry["action"]) for name, entry in entries.items()}, {
            "codex-done": ("merged-clean", "retire"), "codex-open": ("unmerged", "keep"),
            "codex-dirty": ("local-changes", "keep"), "review-detached": ("detached", "keep"),
        })
        trash = Path(entries["codex-done"]["trash"])
        self.assertEqual((trash.parent.parent, trash.name), (self.root, "codex-done"))
        self.assertRegex(trash.parent.name, r"^_TRASH_\d{8}$")
        self.assertEqual(entries["codex-done"]["branchAction"], "delete")
        self.assertEqual([(item["branch"], item["action"]) for item in report["branches"]], [("codex/stale", "delete")])
        self.assertEqual({key: report["summary"][key] for key in ("retire", "deleteBranches", "outsideRoot")},
                         {"retire": 1, "deleteBranches": 2, "outsideRoot": 2})

    def test_apply_moves_a_merged_clean_worktree_and_its_ignored_files_into_the_trash(self) -> None:
        done = self.task("codex/done")
        (done / "node_modules").mkdir()
        (done / "node_modules" / "x").write_text("ignored\n", encoding="utf-8")
        report = self.retire(apply=True)
        entry = self.by_name(report)["codex-done"]
        self.assertEqual((entry["state"], entry["result"], entry["branchResult"]), ("merged-clean", "retired", "deleted"))
        trash = Path(entry["trash"])
        self.assertFalse(done.exists())
        self.assertEqual((trash / "codex-done.txt").read_text(encoding="utf-8"), "codex-done\n")
        self.assertEqual((trash / "node_modules" / "x").read_text(encoding="utf-8"), "ignored\n")
        self.assertTrue(self.registered().isdisjoint({done, trash}))
        self.assertEqual(self.branches(), {"main"})
        self.assertEqual({key: report["summary"][key] for key in ("retired", "deletedBranches", "failed")},
                         {"retired": 1, "deletedBranches": 1, "failed": 0})

    def test_an_existing_trash_name_gets_a_numeric_suffix(self) -> None:
        self.task("codex/done")
        planned = Path(self.by_name(self.retire())["codex-done"]["trash"])
        planned.mkdir(parents=True)
        (planned / "earlier.txt").write_text("earlier\n", encoding="utf-8")
        entry = self.by_name(self.retire(apply=True))["codex-done"]
        self.assertEqual((Path(entry["trash"]), entry["result"]), (planned.with_name("codex-done-2"), "retired"))
        self.assertEqual([path.name for path in planned.iterdir()], ["earlier.txt"])
        self.assertTrue((planned.with_name("codex-done-2") / "codex-done.txt").is_file())

    def test_unmerged_new_dirty_detached_locked_and_outside_worktrees_are_untouched(self) -> None:
        workspace.create_worktree(self.source, self.worktrees / "codex-new", "codex/new")
        self.task("codex/open", merge=False)
        (self.task("codex/untracked") / "notes.txt").write_text("WIP\n", encoding="utf-8")
        (self.task("codex/edited") / "model.txt").write_text("edited\n", encoding="utf-8")
        self.git("worktree", "add", "-q", "--detach", str(self.worktrees / "review-detached"))
        self.git("worktree", "lock", str(self.task("codex/locked")))
        workspace.create_worktree(self.source, self.worktrees / "release-candidate", "release-candidate")
        outside = self.tmp / "elsewhere" / "codex-outside"
        workspace.create_worktree(self.source, outside, "codex/outside")
        before = self.snapshot(outside.parent)
        report = self.retire(apply=True)
        self.assertEqual(self.snapshot(outside.parent), before)
        self.assertFalse(list(self.root.glob("_TRASH_*")))
        entries = self.by_name(report)
        self.assertEqual({name: entry["state"] for name, entry in entries.items()}, {
            "codex-open": "unmerged", "codex-untracked": "local-changes", "codex-edited": "local-changes",
            "review-detached": "detached", "codex-locked": "locked", "release-candidate": "merged-clean",
            "codex-new": "merged-clean",
        })
        self.assertEqual(entries["release-candidate"]["note"], "kept branch")
        # Just created from main by another session: no commits of its own, so nothing of it has merged.
        self.assertIn("no commits of its own", entries["codex-new"]["note"])
        self.assertTrue(all(entry["action"] == "keep" and "result" not in entry for entry in report["worktrees"]))
        self.assertEqual((report["branches"], report["summary"]["outsideRoot"]), ([], 2))

    def test_merged_branches_no_worktree_holds_are_deleted_except_kept_and_current(self) -> None:
        self.git("branch", "codex/merged")
        self.git("branch", "release-candidate")
        self.git("switch", "-q", "-c", "codex/unmerged")
        (self.source / "draft.txt").write_text("draft\n", encoding="utf-8")
        self.git("add", "draft.txt")
        self.git("commit", "-qm", "draft")
        self.git("switch", "-q", "-c", "codex/current", "main")
        dry = self.retire()
        self.assertEqual([(item["branch"], item["action"]) for item in dry["branches"]], [("codex/merged", "delete")])
        self.assertIn("codex/merged", self.branches())
        report = self.retire(apply=True)
        self.assertEqual([(item["branch"], item["result"]) for item in report["branches"]], [("codex/merged", "deleted")])
        self.assertEqual(self.branches(), {"main", "release-candidate", "codex/unmerged", "codex/current"})

    def test_a_registration_whose_directory_is_gone_is_pruned_and_its_merged_branch_deleted(self) -> None:
        gone = self.task("codex/gone")
        moved = self.tmp / "moved-elsewhere"
        gone.rename(moved)
        entry = self.by_name(self.retire(apply=True))["codex-gone"]
        self.assertEqual((entry["state"], entry["action"], entry["result"], entry["branchResult"]),
                         ("missing-directory", "prune", "pruned", "deleted"))
        self.assertNotIn(gone, self.registered())
        self.assertEqual(self.branches(), {"main"})
        self.assertEqual((moved / "codex-gone.txt").read_text(encoding="utf-8"), "codex-gone\n")

    def test_a_just_merged_pr_branch_waits_until_the_source_head_contains_it(self) -> None:
        task = self.task("codex/pr", merge=False)
        self.git("push", "-q", "-u", "origin", "codex/pr", cwd=task)
        hosting = self.tmp / "hosting"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(hosting)], check=True)
        self.git("merge", "-q", "--no-ff", "--no-edit", "origin/codex/pr", cwd=hosting)
        # The pull request merges and its head branch is deleted, as GitHub does.
        self.git("push", "-q", "origin", "main", ":codex/pr", cwd=hosting)
        entry = self.by_name(self.retire(apply=True))["codex-pr"]
        self.assertEqual((entry["state"], entry["result"], entry["branchAction"]), ("merged-clean", "retired", "keep"))
        self.assertIn("HEAD does not contain it", entry["branchNote"])
        self.assertFalse(task.exists())
        self.assertIn("codex/pr", self.branches())
        self.git("merge", "-q", "--ff-only", "origin/main")
        report = self.retire(apply=True)
        self.assertEqual([(item["branch"], item["result"]) for item in report["branches"]], [("codex/pr", "deleted")])
        self.assertEqual(self.branches(), {"main"})

    def test_a_failed_fetch_stops_apply(self) -> None:
        done = self.task("codex/done")
        workspace.configure_root(self.source, self.root)
        self.git("remote", "set-url", "origin", str(self.tmp / "missing.git"))
        code, report = self.cli("retire", "--apply")
        self.assertEqual((code, report["fetch"], report["summary"]["retire"]), (1, "failed", 1))
        self.assertIn("stopped", report)
        self.assertTrue((done / "codex-done.txt").is_file())
        self.assertIn("codex/done", self.branches())
        self.assertFalse(list(self.root.glob("_TRASH_*")))
        code, report = self.cli("retire", "--apply", "--no-fetch")
        self.assertEqual((code, report["fetch"], report["summary"]["retired"]), (0, "skipped", 1))
        self.assertFalse(done.exists())

    def test_the_source_checkout_is_never_moved(self) -> None:
        own = self.task("codex/own")
        report = workspace.retire_worktrees(own, self.root, apply=True)
        entry = self.by_name(report)["codex-own"]
        self.assertEqual((entry["state"], entry["action"], entry["note"]), ("merged-clean", "keep", "source checkout"))
        self.assertNotIn("result", entry)
        self.assertEqual((own / "codex-own.txt").read_text(encoding="utf-8"), "codex-own\n")
        self.assertIn(own, self.registered())
        self.assertEqual(self.branches(), {"main", "codex/own"})
        self.assertFalse(list(self.root.glob("_TRASH_*")))


if __name__ == "__main__":
    unittest.main()
