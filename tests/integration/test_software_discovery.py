"""Installation evidence and existing selection paths; never launch a host."""
import ctypes
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from archflow.adapters import cad_execution
from archflow.adapters import local_cad_discovery as discovery


class SoftwareDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"discovery fixture, not an executable")
        return path.resolve()

    def registry(self, **options):
        return discovery.SoftwareDiscoveryRegistry(**{
            "system": "Windows", "program_roots": (self.root,),
            "registry_candidates": (), "path_candidates": (), **options,
        })

    def test_known_windows_locations_are_normalized_unverified_evidence(self):
        paths = {
            "rhino": self.file("Rhino 8/System/Rhino.exe"),
            "blender": self.file("Blender Foundation/Blender 4.5/blender.exe"),
            "sketchup": self.file("SketchUp/SketchUp 2026/SketchUp.exe"),
            "autocad": self.file("Autodesk/AutoCAD 2025/acad.exe"),
            "autocad-core": self.file("Autodesk/AutoCAD 2025/accoreconsole.exe"),
        }
        hints = dict(zip(paths, ("8", "4.5", "2026", "2025", "2025")))
        registry = self.registry()
        for product, path in paths.items():
            with self.subTest(product=product):
                result = registry.discover(product)
                self.assertEqual(len(result.installations), 1)
                row = result.installations[0]
                self.assertEqual((row.product, row.product_id, row.executable), (product, product, path))
                self.assertEqual(row.version_hint, hints[product])
                self.assertEqual(row.architecture, "unknown")
                public = row.public()
                self.assertEqual(public["productId"], product)
                self.assertEqual(public["architecture"], "unknown")
                self.assertFalse(public["versionVerified"])
                self.assertIsNone(public["version"])
                self.assertNotIn(str(self.root), str(public))
                self.assertNotIn("executable", public)

    def test_only_requested_product_is_scanned_without_external_effects(self):
        self.file("Rhino 9/System/Rhino.exe")
        original_glob = Path.glob
        patterns = []

        def bounded_glob(path, pattern):
            self.assertNotIn("**", pattern)
            self.assertTrue(pattern.startswith("Rhino "), pattern)
            patterns.append(pattern)
            return original_glob(path, pattern)

        no_effect = AssertionError("discovery must remain read-only and bounded")
        with patch.object(Path, "glob", bounded_glob), \
             patch.object(Path, "rglob", side_effect=no_effect), \
             patch("os.walk", side_effect=no_effect), \
             patch("os.system", side_effect=no_effect), \
             patch.object(subprocess, "Popen", side_effect=no_effect), \
             patch.object(subprocess, "run", side_effect=no_effect), \
             patch.object(ctypes, "CDLL", side_effect=no_effect), \
             patch.object(socket, "create_connection", side_effect=no_effect), \
             patch.object(discovery.shutil, "which", side_effect=no_effect), \
             patch.object(discovery, "_app_paths", return_value=[]) as app_paths:
            registry = self.registry(registry_candidates=None)
            result = registry.discover("rhino")
        self.assertEqual(len(patterns), 4)
        self.assertEqual(result.installations[0].version_hint, "9")
        if discovery.os.name == "nt":
            self.assertEqual(app_paths.call_args.args[1], ("rhino",))

    def test_registry_candidates_filter_products_normalize_and_deduplicate(self):
        path = self.file("custom/Rhino.exe")
        alternate = path.parent / "nested" / ".." / path.name
        (path.parent / "nested").mkdir()
        registry = self.registry(program_roots=[], registry_candidates=[
            ("rhino", path, "windows-app-path"),
            ("rhino", alternate, "windows-app-path"),
            ("blender", self.file("custom/blender.exe"), "windows-app-path"),
            ("rhino", self.root / "missing.exe", "windows-app-path"),
            ("rhino", Path("relative/Rhino.exe"), "windows-app-path"),
        ])
        rows = registry.discover("rhino").installations
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].executable, path)
        self.assertIsNone(rows[0].version_hint)
        self.assertEqual(rows[0].evidence, "windows-app-path")

    def test_unknown_product_and_os_do_not_probe_the_machine(self):
        with patch.object(Path, "glob", side_effect=AssertionError("unexpected scan")), \
             patch.object(discovery.shutil, "which", side_effect=AssertionError("unexpected PATH lookup")), \
             patch.object(discovery, "_app_paths", side_effect=AssertionError("unexpected registry lookup")):
            for product in ("missing", "Rhino", "revit", None, []):
                with self.subTest(product=product), self.assertRaisesRegex(ValueError, "Unknown software product"):
                    self.registry().discover(product)
            result = self.registry(system="UnknownOS").discover("blender")
        self.assertFalse(result.installations)
        self.assertTrue(result.diagnostics)

    def test_mac_bundle_and_linux_path_do_not_probe_windows(self):
        path = self.file("Blender.app/Contents/MacOS/Blender")
        with patch.object(discovery, "_app_paths", side_effect=AssertionError("unexpected registry lookup")):
            mac = self.registry(system="Darwin", application_roots=[self.root]).discover("blender")
            linux = self.registry(system="Linux", path_candidates=[path]).discover("blender")
        self.assertEqual(mac.installations[0].executable, path)
        self.assertEqual(mac.installations[0].evidence, "application-bundle")
        self.assertEqual(linux.installations[0].executable, path)
        self.assertEqual(linux.installations[0].evidence, "path")

    def test_rhino_wrapper_keeps_newest_version_preference(self):
        expected = tuple(self.file(f"Rhino {version}/System/Rhino.exe") for version in (9, 8, 7, 6))
        registry = self.registry()
        with patch.object(cad_execution, "SoftwareDiscoveryRegistry", return_value=registry):
            self.assertEqual(cad_execution.discover_rhino_executables(), expected)

    def test_rhino_wrapper_reports_only_the_windows_com_host(self):
        # A Rhino for Mac bundle is software evidence, not the supervised COM host.
        self.file("Rhino 8.app/Contents/MacOS/Rhinoceros")
        mac = self.registry(system="Darwin", application_roots=(self.root,))
        self.assertEqual(len(mac.discover("rhino").installations), 1)
        with patch.object(cad_execution, "SoftwareDiscoveryRegistry", return_value=mac):
            self.assertEqual(cad_execution.discover_rhino_executables(), ())

    def test_blender_path_precedes_standard_installs_then_newest_version(self):
        command = self.file("commands/blender.exe")
        older = self.file("Blender Foundation/Blender 4.5/blender.exe")
        newest = self.file("Blender Foundation/Blender 4.5.1/blender.exe")
        registry = self.registry(path_candidates=[command])
        self.assertEqual(tuple(row.executable for row in registry.discover("blender").installations),
                         (command, newest, older))
        with patch.object(discovery, "SoftwareDiscoveryRegistry", return_value=registry):
            self.assertEqual(discovery.resolve_blender_executable(), str(command))
        fallback = self.registry()
        with patch.object(discovery, "SoftwareDiscoveryRegistry", return_value=fallback):
            self.assertEqual(discovery.resolve_blender_executable(), str(newest))

    def test_explicit_blender_selection_preserves_path_lookup_and_missing_refusal(self):
        explicit = self.root / "custom" / "blender.exe"
        with patch.object(discovery, "SoftwareDiscoveryRegistry", side_effect=AssertionError("explicit choice must not fall back")), \
             patch.object(discovery.shutil, "which", side_effect=[str(explicit), "command-result", None]) as lookup:
            self.assertEqual(discovery.resolve_blender_executable(explicit), str(explicit))
            self.assertEqual(discovery.resolve_blender_executable("selected-blender"), "command-result")
            self.assertIsNone(discovery.resolve_blender_executable("missing-blender"))
        self.assertEqual([call.args for call in lookup.call_args_list],
                         [(str(explicit),), ("selected-blender",), ("missing-blender",)])

    def test_blender_path_lookup_normalizes_relative_path_entries(self):
        command = self.file("commands/blender.exe")
        try:
            relative = os.path.relpath(command)
        except ValueError:
            self.skipTest("temporary directory and checkout are on different drives")
        with patch.object(discovery.shutil, "which", return_value=relative) as lookup:
            result = self.registry(program_roots=[], path_candidates=None).discover("blender")
        lookup.assert_called_once_with("blender")
        self.assertEqual(result.installations[0].executable, command)

    def test_blender_path_command_keeps_the_name_path_gives_it(self):
        # snap links /snap/bin/blender to /usr/bin/snap and dispatches on
        # argv[0], so the resolved target is not a runnable Blender.
        launcher = self.file("usr/bin/snap")
        command = self.root / "snap" / "bin" / "blender"
        command.parent.mkdir(parents=True)
        try:
            command.symlink_to(launcher)
        except (OSError, NotImplementedError):
            self.skipTest("this host cannot create symbolic links")
        registry = self.registry(system="Linux", path_candidates=[command])
        row, = registry.discover("blender").installations
        self.assertEqual((row.executable, row.evidence), (command, "path"))
        self.assertEqual(row.public()["executableName"], "blender")
        with patch.object(discovery, "SoftwareDiscoveryRegistry", return_value=registry):
            self.assertEqual(discovery.resolve_blender_executable(), str(command))

    def test_blender_backend_uses_shared_selection_before_its_worker(self):
        from archflow.adapters import blender_cad
        from tests.integration.test_blender_cad import _request

        selected = "configured-blender"
        command = str(self.root / "selected" / "blender.exe")
        request = _request(self.root, backend_options={"blender_executable": selected})
        with patch.object(blender_cad, "resolve_blender_executable", return_value=command) as resolve, \
             patch.object(blender_cad, "_run_worker", side_effect=subprocess.TimeoutExpired(command, 1)) as worker:
            result = blender_cad.BlenderBackend().execute(request)
        resolve.assert_called_once_with(selected)
        self.assertEqual(worker.call_args.args[0], command)
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.artifacts)

    def test_legacy_discovery_keeps_original_products_and_public_fields(self):
        self.file("Rhino 8/System/Rhino.exe")
        self.file("Blender Foundation/Blender 4.5/blender.exe")
        self.file("SketchUp/SketchUp 2024/SketchUp.exe")
        self.file("Autodesk/AutoCAD 2025/acad.exe")
        self.file("Autodesk/AutoCAD 2025/accoreconsole.exe")
        result = discovery.discover_local_cad(system="Windows", program_roots=[self.root], registry_candidates=[])
        self.assertEqual([row.product for row in result.installations], ["autocad", "autocad-core", "sketchup"])
        for row in result.installations:
            self.assertEqual(row.public()["product"], row.product)
            self.assertEqual(row.public()["executableName"], row.executable.name)


if __name__ == "__main__":
    unittest.main()
