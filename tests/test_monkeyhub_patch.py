from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock_patch
import warnings
import zipfile

from apps.monkeyhub.installer import patch
from tools import package_monkeyapps as builder


class DesktopPatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mh-patch-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.versions = self.root / "versions"
        self.versions.mkdir()
        self.base = self.versions / ("a" * 12 + "-desktop")
        self.target = self.root / "complete-target"
        for name in patch.REQUIRED_FILES:
            self.write(self.base / name, (name + "\n").encode())
        self.write(self.base / "old-only.txt", b"removed\n")
        self.write(self.base / "app.txt", b"old app\n")
        self.write(self.base / "_runtime/dependency.dat", os.urandom(65536))
        self.identity(self.base, "a" * 40)
        shutil.copytree(self.base, self.target)
        self.write(self.target / "app.txt", b"updated app\n")
        self.write(self.target / "new-only.txt", b"new file\n")
        (self.target / "old-only.txt").unlink()
        self.write(self.target / "MonkeyHub.exe", b"new desktop exe\n")
        self.identity(self.target, "b" * 40)
        self.output = self.root / "update.zip"
        self.summary = patch.create_patch(self.base, self.target, self.output)
        self.before = self.snapshot(self.base)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def identity(root, commit):
        (root / "source-version.txt").write_text(commit, encoding="utf-8")
        (root / "build-info.json").write_text(json.dumps({
            "sourceCommit": commit, "target": "windows-x64", "channel": "candidate",
            "desktop": {"sourceCommit": commit, "version": "0.1.0",
                        "executableSha256": hashlib.sha256((root / "MonkeyHub.exe").read_bytes()).hexdigest()},
        }), encoding="utf-8")

    @staticmethod
    def snapshot(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def rewrite(self, change_document=None, change_entries=None):
        with zipfile.ZipFile(self.output) as archive:
            entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
        result = []
        for entry, data in entries:
            if entry.filename == patch.MANIFEST_NAME and change_document:
                document = json.loads(data)
                change_document(document)
                data = json.dumps(document).encode()
            result.append((entry, data))
        if change_entries:
            result = change_entries(result)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.output, "w", zipfile.ZIP_DEFLATED) as archive:
                for entry, data in result:
                    archive.writestr(entry, data)

    def assert_refused(self, text):
        with self.assertRaisesRegex(patch.PatchError, text):
            patch.stage_patch(self.output, self.base, self.versions)
        self.assertEqual(self.snapshot(self.base), self.before)
        self.assertEqual(list(self.versions.iterdir()), [self.base])

    def test_small_delta_reconstructs_complete_version_and_leaves_base_unchanged(self):
        self.assertEqual(self.summary, patch.inspect_patch(self.output, self.base))
        self.assertEqual(self.summary["baseCommit"], "a" * 40)
        self.assertEqual(self.summary["targetCommit"], "b" * 40)
        self.assertEqual(self.summary["changedFiles"], 5)
        self.assertEqual(self.summary["removedFiles"], 1)
        self.assertGreater(self.summary["reusedFiles"], 0)
        self.assertLess(self.output.stat().st_size, self.summary["targetBytes"] // 2)
        with zipfile.ZipFile(self.output) as archive:
            self.assertNotIn("payload/_runtime/dependency.dat", archive.namelist())
        installed = patch.stage_patch(self.output, self.base, self.versions)
        self.assertEqual(installed, self.versions / ("b" * 12 + "-desktop"))
        self.assertEqual(self.snapshot(installed), self.snapshot(self.target))
        self.assertEqual(self.snapshot(self.base), self.before)
        self.assertEqual(patch.verify_target(self.output, installed), self.summary)
        # Copies are independent, not hard links to the currently executing tree.
        (installed / "_runtime/dependency.dat").write_bytes(b"change the prepared copy")
        self.assertEqual(self.snapshot(self.base), self.before)
        with self.assertRaisesRegex(patch.PatchError, "Prepared version files changed"):
            patch.verify_target(self.output, installed)

    def test_runtime_python_caches_are_neither_distributed_nor_reused(self):
        self.write(self.base / "apps/monkeyhub/__pycache__/run.cpython-313.pyc", b"runtime cache")
        self.write(self.base / "old.pyc", b"runtime cache")
        self.write(self.target / "apps/monkeyhub/__pycache__/run.cpython-313.pyc", b"target cache")
        other = self.root / "with-cache.zip"
        patch.create_patch(self.base, self.target, other)
        installed = patch.stage_patch(other, self.base, self.versions)
        self.assertFalse(list(installed.rglob("*.pyc")))

    def test_wrong_base_commit_and_modified_unchanged_file_are_refused(self):
        self.rewrite(lambda document: document.update(baseCommit="c" * 40))
        self.assert_refused("Base source commit")
        self.output.unlink()
        patch.create_patch(self.base, self.target, self.output)
        (self.base / "_runtime/dependency.dat").write_bytes(b"changed base dependency")
        self.before = self.snapshot(self.base)
        self.assert_refused("Base installation files differ")

    def test_removed_base_file_is_verified_before_it_can_be_omitted(self):
        (self.base / "old-only.txt").write_bytes(b"user changed this old file")
        self.before = self.snapshot(self.base)
        self.assert_refused("Base installation files differ")

    def test_unlisted_base_file_is_not_silently_lost(self):
        self.write(self.base / "unknown-user-file.txt", b"preserve")
        self.before = self.snapshot(self.base)
        self.assert_refused("Base installation files differ")

    def test_missing_corrupt_and_unexpected_payload_are_refused(self):
        original = self.output.read_bytes()
        mutations = (
            (lambda rows: [row for row in rows if row[0].filename != "payload/app.txt"], "missing or unexpected"),
            (lambda rows: [(entry, b"x" * len(data) if entry.filename == "payload/app.txt" else data)
                           for entry, data in rows], "Corrupt payload"),
            (lambda rows: [*rows, (zipfile.ZipInfo("payload/extra.txt"), b"extra")], "missing or unexpected"),
        )
        for mutation, error in mutations:
            with self.subTest(error=error):
                self.output.write_bytes(original)
                self.rewrite(change_entries=mutation)
                self.assert_refused(error)

    def test_broken_deflate_stream_is_reported_without_staging_files(self):
        with zipfile.ZipFile(self.output) as archive:
            entry = archive.getinfo("payload/app.txt")
            self.assertEqual(entry.compress_type, zipfile.ZIP_DEFLATED)
        content = bytearray(self.output.read_bytes())
        header = entry.header_offset
        name_length = int.from_bytes(content[header + 26:header + 28], "little")
        extra_length = int.from_bytes(content[header + 28:header + 30], "little")
        position = header + 30 + name_length + extra_length
        content[position] |= 7  # The first deflate block has a reserved block type.
        self.output.write_bytes(content)
        self.assert_refused("Cannot stage patch")

    def test_duplicate_case_collision_traversal_and_links_are_refused(self):
        original = self.output.read_bytes()
        linked = zipfile.ZipInfo("payload/link")
        linked.create_system = 3
        linked.external_attr = (stat.S_IFLNK | 0o777) << 16
        mutations = (
            (lambda rows: [*rows, rows[0]], "Duplicate"),
            (lambda rows: [*rows, (zipfile.ZipInfo("PAYLOAD/extra.txt"), b"extra")], "Case-colliding"),
            (lambda rows: [*rows, (zipfile.ZipInfo("../outside.txt"), b"bad")], "Unsafe patch path"),
            (lambda rows: [*rows, (zipfile.ZipInfo("payload/C:/outside.txt"), b"bad")], "Unsafe patch path"),
            (lambda rows: [*rows, (linked, b"../outside")], "Unsupported ZIP entry"),
        )
        for mutation, error in mutations:
            with self.subTest(error=error):
                self.output.write_bytes(original)
                self.rewrite(change_entries=mutation)
                self.assert_refused(error)

    def test_manifest_paths_and_change_lists_cannot_override_closed_tables(self):
        original = self.output.read_bytes()
        for name in ("../outside", "C:/outside", "\\outside", "a/../b", "a//b", "a./b", "NUL", "a/CON.txt"):
            with self.subTest(name=name):
                self.output.write_bytes(original)
                self.rewrite(lambda doc: doc["targetFiles"].update({name: {"size": 0, "sha256": "a" * 64}}))
                self.assert_refused("Unsafe patch path")
        self.output.write_bytes(original)
        self.rewrite(lambda document: document.update(removed=[]))
        self.assert_refused("changes do not match")

    def test_manifest_duplicate_keys_and_target_identity_mismatch_are_refused(self):
        self.rewrite(change_entries=lambda rows: [
            (entry, data.replace(b'"trust":', b'"trust":"ignored","trust":', 1)
             if entry.filename == patch.MANIFEST_NAME else data) for entry, data in rows])
        self.assert_refused("Duplicate JSON key")
        self.output.unlink()
        patch.create_patch(self.base, self.target, self.output)
        self.rewrite(lambda document: document.update(targetCommit="c" * 40, targetVersion="c" * 12 + "-desktop"))
        self.assert_refused("Target build metadata")

    def test_partial_copy_failure_cleans_only_its_own_temporary_directory(self):
        with mock_patch.object(patch.shutil, "copyfile", side_effect=OSError("disk failed")):
            self.assert_refused("disk failed")

    def test_existing_target_and_destination_inside_base_are_not_modified(self):
        existing = self.versions / ("b" * 12 + "-desktop")
        self.write(existing / "user.txt", b"keep this")
        with self.assertRaisesRegex(patch.PatchError, "Existing target does not match"):
            patch.stage_patch(self.output, self.base, self.versions)
        self.assertEqual(self.snapshot(existing), {"user.txt": b"keep this"})
        with self.assertRaisesRegex(patch.PatchError, "outside the current version"):
            patch.stage_patch(self.output, self.base, self.base)
        self.assertEqual(self.snapshot(self.base), self.before)

    def test_identical_existing_target_is_verified_and_reused_without_writing(self):
        existing = patch.stage_patch(self.output, self.base, self.versions)
        self.write(existing / "apps/monkeyhub/__pycache__/run.cpython-313.pyc", b"runtime cache")
        before = self.snapshot(existing)
        file_stats = {path.relative_to(existing): (path.stat().st_ino, path.stat().st_mtime_ns)
                      for path in existing.rglob("*") if path.is_file()}
        identity = existing.stat().st_ino
        self.assertEqual(patch.stage_patch(self.output, self.base, self.versions), existing)
        self.assertEqual(existing.stat().st_ino, identity)
        self.assertEqual(self.snapshot(existing), before)
        self.assertEqual(file_stats, {path.relative_to(existing): (path.stat().st_ino, path.stat().st_mtime_ns)
                                     for path in existing.rglob("*") if path.is_file()})
        self.assertEqual(self.snapshot(self.base), self.before)
        self.assertEqual(set(self.versions.iterdir()), {self.base, existing})

    def test_incomplete_modified_or_unknown_existing_target_is_preserved_and_refused(self):
        cases = {
            "incomplete": lambda root: (root / "MonkeyHub.exe").unlink(),
            "modified": lambda root: (root / "_runtime/dependency.dat").write_bytes(b"user modification"),
            "unknown": lambda root: (root / "user.txt").write_bytes(b"unknown file"),
            "identity": lambda root: (root / "source-version.txt").write_text("c" * 40),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                parent = self.root / name
                existing = parent / ("b" * 12 + "-desktop")
                shutil.copytree(self.target, existing)
                change(existing)
                before = self.snapshot(existing)
                with self.assertRaisesRegex(patch.PatchError, "Existing target does not match"):
                    patch.stage_patch(self.output, self.base, parent)
                self.assertEqual(self.snapshot(existing), before)
                self.assertEqual(list(parent.iterdir()), [existing])
        self.assertEqual(self.snapshot(self.base), self.before)

    def test_existing_target_does_not_bypass_base_and_payload_verification(self):
        existing = patch.stage_patch(self.output, self.base, self.versions)
        before = self.snapshot(existing)
        original = (self.base / "app.txt").read_bytes()
        (self.base / "app.txt").write_bytes(b"modified old version")
        with self.assertRaisesRegex(patch.PatchError, "Base installation files differ"):
            patch.stage_patch(self.output, self.base, self.versions)
        (self.base / "app.txt").write_bytes(original)
        self.rewrite(change_entries=lambda rows: [
            (entry, b"x" * len(data) if entry.filename == "payload/app.txt" else data)
            for entry, data in rows])
        with self.assertRaisesRegex(patch.PatchError, "Corrupt payload"):
            patch.stage_patch(self.output, self.base, self.versions)
        self.assertEqual(self.snapshot(existing), before)
        self.assertEqual(self.snapshot(self.base), self.before)

    def test_symbolic_link_in_base_is_refused(self):
        linked = self.base / "linked-file"
        try:
            linked.symlink_to(self.target / "app.txt")
        except OSError:
            self.skipTest("symlink privilege unavailable; Windows junction coverage is separate")
        with self.assertRaisesRegex(patch.PatchError, "Links and reparse"):
            patch.inspect_patch(self.output, self.base)
        linked.unlink()

    @unittest.skipUnless(os.name == "nt", "Windows junction behaviour")
    def test_windows_junction_in_base_is_refused(self):
        junction = self.base / "linked-directory"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.target)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            with self.assertRaisesRegex(patch.PatchError, "Links and reparse"):
                patch.inspect_patch(self.output, self.base)
        finally:
            junction.rmdir()  # Only the newly-created junction, never its target.

    @unittest.skipUnless(os.name == "nt", "Windows junction behaviour")
    def test_existing_target_junction_is_refused_even_when_all_files_match(self):
        junction = self.versions / ("b" * 12 + "-desktop")
        before = self.snapshot(self.target)
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.target)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            with self.assertRaisesRegex(patch.PatchError, "Links and reparse"):
                patch.stage_patch(self.output, self.base, self.versions)
            self.assertEqual(self.snapshot(self.target), before)
            self.assertEqual(self.snapshot(self.base), self.before)
            self.assertTrue(junction.is_dir())
        finally:
            junction.rmdir()  # Only this fixture junction, never the complete target.

    def test_builder_cli_uses_existing_complete_bundles_without_build_tools(self):
        destination = self.root / "cli-update.zip"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(builder.main([
                "--patch-from", str(self.base), "--patch-to", str(self.target),
                "--patch-output", str(destination), "--node", str(self.root / "missing-node.exe"),
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["targetCommit"], "b" * 40)
        self.assertEqual(patch.inspect_patch(destination, self.base), self.summary)


if __name__ == "__main__":
    unittest.main()
