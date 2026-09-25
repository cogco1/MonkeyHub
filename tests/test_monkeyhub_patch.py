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

    def test_summary_names_the_build_info_a_release_manifest_binds(self):
        self.assertEqual(self.summary["targetBuildInfoSha256"],
                         hashlib.sha256((self.target / "build-info.json").read_bytes()).hexdigest())
        self.assertEqual(patch.describe_patch(self.output), self.summary)

    def test_stopping_staging_removes_only_its_own_temporary_copy(self):
        for allowed in (3, len(patch.REQUIRED_FILES) + 12):
            calls = []

            def cancelled():
                calls.append(1)
                return len(calls) > allowed

            with self.subTest(allowed=allowed), self.assertRaises(patch.PatchCancelled):
                patch.stage_patch(self.output, self.base, self.versions, cancelled=cancelled)
            self.assertEqual(list(self.versions.iterdir()), [self.base])
            self.assertEqual(self.snapshot(self.base), self.before)
        installed = patch.stage_patch(self.output, self.base, self.versions)
        with self.assertRaises(patch.PatchCancelled):
            patch.verify_target(self.output, installed, cancelled=lambda: True)

    def test_layout_recheck_reads_identity_files_and_named_scripts_only(self):
        installed = patch.stage_patch(self.output, self.base, self.versions)
        script = "apps/monkeyhub/run.py"
        self.assertEqual(patch.verify_target_layout(self.output, installed, exact=(script,)), self.summary)
        # Same size, other bytes: a layout recheck states that it does not
        # rehash every file; verify_target does.
        dependency = installed / "_runtime/dependency.dat"
        dependency.write_bytes(bytes(byte ^ 1 for byte in dependency.read_bytes()))
        patch.verify_target_layout(self.output, installed)
        with self.assertRaisesRegex(patch.PatchError, "Prepared version files changed"):
            patch.verify_target(self.output, installed)
        (installed / script).write_bytes(bytes(byte ^ 1 for byte in (installed / script).read_bytes()))
        with self.assertRaisesRegex(patch.PatchError, "run.py"):
            patch.verify_target_layout(self.output, installed, exact=(script,))
        changes = {
            "an added file": lambda root: self.write(root / "extra.txt", b"new"),
            "a resized file": lambda root: (root / "app.txt").write_bytes(b"different size\n"),
            "a same-size executable": lambda root: (root / "MonkeyHub.exe").write_bytes(b"new desktop exf\n"),
        }
        for number, (label, change) in enumerate(changes.items()):
            with self.subTest(label):
                copy = self.root / f"layout-{number}"
                shutil.copytree(self.target, copy)
                patch.verify_target_layout(self.output, copy)
                change(copy)
                with self.assertRaises(patch.PatchError):
                    patch.verify_target_layout(self.output, copy)

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


class ReleaseUpdateIndexTests(unittest.TestCase):
    """The release path's delta patches and index, from small published fixtures."""

    RELEASES = (("0.1.1", "a" * 40), ("0.1.2", "c" * 40), ("0.1.3", "b" * 40))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mh-index-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.published = {version: self.publish(version, commit) for version, commit in self.RELEASES}
        self.output, self.work = self.root / "updates", self.root / "work"

    def publish(self, version, commit):
        """One complete desktop bundle, released the way the workflow does it."""
        bundle = self.root / "bundles" / version
        for name in patch.REQUIRED_FILES:
            DesktopPatchTests.write(bundle / name, (name + "\n").encode())
        DesktopPatchTests.write(bundle / "MonkeyHub.exe", f"desktop {version}\n".encode())
        DesktopPatchTests.write(bundle / "_runtime/dependency.dat", b"shared dependency\n" * 4096)
        DesktopPatchTests.write(bundle / "apps/monkeyhub/api/monkeyhub_api/updates.py", f"# {version}\n".encode())
        (bundle / "source-version.txt").write_text(commit, encoding="utf-8")
        build_info = {
            "sourceCommit": commit, "target": "windows-x64", "channel": "candidate", "releaseVersion": version,
            "desktop": {"sourceCommit": commit, "version": version,
                        "executableSha256": hashlib.sha256((bundle / "MonkeyHub.exe").read_bytes()).hexdigest()},
            "pythonVersion": builder.PYTHON_VERSION, "pythonUrl": builder.PYTHON_URL, "pythonSha256": builder.PYTHON_SHA256,
            "runtimeInventory": {"nodeVersion": "v24.14.0", "acpAdapter": {"name": "fixture", "version": "1"},
                                 "pythonRequirements": {"path": "_runtime/requirements-lock.txt", "sha256": "0" * 64}},
        }
        info = bundle / "build-info.json"
        info.write_text(json.dumps(build_info), encoding="utf-8")
        (bundle / builder.SBOM_NAME).write_text(json.dumps({"fixture": version}), encoding="utf-8")
        prefix = f"MonkeyHub-{version}-windows-x64"
        directory = self.root / "published" / version
        directory.mkdir(parents=True)
        archive = directory / f"{prefix}-candidate.zip"
        with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as opened:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    opened.write(path, f"{prefix}/{path.relative_to(bundle).as_posix()}")
        checksum = directory / f"{archive.name}.sha256"
        checksum.write_text(f"{builder.sha256(archive)}  {archive.name}\n", encoding="utf-8")
        sbom = directory / f"{prefix}.cyclonedx.json"
        shutil.copy2(bundle / builder.SBOM_NAME, sbom)
        manifest = directory / f"{archive.name}.release-manifest.json"
        manifest.write_text(json.dumps(builder.release_manifest(
            build_info, version, prefix, info, archive, (archive, checksum, sbom), sbom)), encoding="utf-8")
        self.assertEqual(builder.verify_release(manifest), [])
        return manifest

    def build(self, *bases):
        return builder.update_index(self.published["0.1.3"], [self.published[v].parent for v in bases],
                                    self.output, self.work, "d" * 40)

    def test_each_published_base_gets_a_patch_that_rebuilds_the_new_release(self):
        index = self.build("0.1.2", "0.1.1")
        self.assertEqual(index.name, "MonkeyHub-0.1.3-update-index.json")
        document = json.loads(index.read_text(encoding="utf-8"))
        self.assertEqual((document["schema"], document["channel"], document["version"]),
                         ("MonkeyHubUpdateIndex@1", "unsigned-prerelease", "0.1.3"))
        self.assertEqual((document["sourceCommit"], document["releaseCommit"]), ("d" * 40, "b" * 40))
        self.assertIs(document["trust"]["signed"], False)
        manifest = self.published["0.1.3"]
        self.assertEqual(document["releaseManifest"], {
            "name": manifest.name, "size": manifest.stat().st_size, "sha256": builder.sha256(manifest)})
        full = manifest.parent / "MonkeyHub-0.1.3-windows-x64-candidate.zip"
        self.assertEqual(document["full"], {"name": full.name, "size": full.stat().st_size, "sha256": builder.sha256(full)})
        self.assertEqual([(row["baseVersion"], row["baseCommit"]) for row in document["patches"]],
                         [("0.1.2", "c" * 40), ("0.1.1", "a" * 40)])
        self.assertEqual({path.name for path in self.output.iterdir()},
                         {index.name, *(row["name"] for row in document["patches"])})
        self.assertFalse(any(self.work.iterdir()), "extracted releases are removed")
        self.assertEqual(builder.verify_update_index(index, manifest.parent), [])
        # The published base, installed as its ZIP extracts, is rebuilt into
        # exactly the files the new release's ZIP carries.
        with zipfile.ZipFile(full) as opened:
            expected = {name.split("/", 1)[1]: opened.read(name) for name in opened.namelist()}
        for row in document["patches"]:
            with self.subTest(base=row["baseVersion"]):
                self.assertEqual((self.output / row["name"]).stat().st_size, row["size"])
                self.assertEqual(builder.sha256(self.output / row["name"]), row["sha256"])
                versions = self.root / "installed" / row["baseVersion"] / "versions"
                base = versions / (row["baseCommit"][:12] + "-desktop")
                with zipfile.ZipFile(self.published[row["baseVersion"]].parent /
                                     f"MonkeyHub-{row['baseVersion']}-windows-x64-candidate.zip") as opened:
                    for name in opened.namelist():
                        DesktopPatchTests.write(base / name.split("/", 1)[1], opened.read(name))
                staged = patch.stage_patch(self.output / row["name"], base, versions)
                self.assertEqual(staged.name, "b" * 12 + "-desktop")
                self.assertEqual({path.relative_to(staged).as_posix(): path.read_bytes()
                                  for path in staged.rglob("*") if path.is_file()}, expected)

    def test_changed_unlisted_or_misnamed_files_fail_verification(self):
        index = self.build("0.1.2")
        release_dir = self.published["0.1.3"].parent
        document = json.loads(index.read_text(encoding="utf-8"))
        patch_file = self.output / document["patches"][0]["name"]
        original = patch_file.read_bytes()
        patch_file.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        self.assertTrue(any("differs from the index" in row for row in builder.verify_update_index(index, release_dir)))
        patch_file.write_bytes(original)
        stray = self.output / "MonkeyHub-0.1.3-from-0.1.0.patch.zip"
        stray.write_bytes(original)
        self.assertTrue(any("does not list" in row for row in builder.verify_update_index(index, release_dir)))
        stray.unlink()
        document["patches"][0]["baseVersion"] = "0.1.4"
        index.write_text(json.dumps(document), encoding="utf-8")
        problems = builder.verify_update_index(index, release_dir)
        self.assertTrue(any("not older" in row for row in problems), problems)

    def test_a_base_that_is_not_older_or_does_not_verify_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not older"):
            builder.update_index(self.published["0.1.3"], [self.published["0.1.3"].parent], self.output, self.work)
        archive = self.published["0.1.1"].parent / "MonkeyHub-0.1.1-windows-x64-candidate.zip"
        archive.write_bytes(archive.read_bytes() + b"x")
        with self.assertRaisesRegex(ValueError, "does not verify"):
            self.build("0.1.1")
        self.assertFalse((self.output / "MonkeyHub-0.1.3-update-index.json").exists())

    def test_builder_cli_writes_and_verifies_the_index_without_build_tools(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(builder.main([
                "--update-index", str(self.published["0.1.3"]), "--update-base", str(self.published["0.1.1"].parent),
                "--update-output", str(self.output), "--update-work", str(self.work),
                "--promoted-commit", "d" * 40, "--node", str(self.root / "missing-node.exe"),
            ]), 0)
        self.assertEqual(json.loads(output.getvalue())["patches"][0]["baseVersion"], "0.1.1")
        index = self.output / "MonkeyHub-0.1.3-update-index.json"
        with contextlib.redirect_stdout(io.StringIO()) as verified:
            self.assertEqual(builder.main(["--verify-update-index", str(index),
                                           "--release-dir", str(self.published["0.1.3"].parent)]), 0)
        self.assertIn("PASS", verified.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as refused:
            self.assertEqual(builder.main(["--verify-update-index", str(index)]), 1)
        self.assertIn("not present", refused.getvalue())


if __name__ == "__main__":
    unittest.main()
