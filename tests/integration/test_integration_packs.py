"""Integration status and qualification use existing CAD execution boundaries.

Controlled hosts exercise real backend validation without launching a CAD app.
Their saved fixture bytes are test witnesses, not native geometry acceptance.
"""
import ctypes
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request

from archflow.adapters import blender_cad, cad_backend
from archflow.adapters.cad_execution import CadExecutionError
from archflow.adapters.integration_packs import (
    IntegrationPack, IntegrationPackManager, IntegrationUnavailable,
    PackCapability, PackComponent, PackInstallation, PackWorkflow,
)
from archflow.adapters.local_cad_discovery import Discovery, Installation, SoftwareDiscoveryRegistry
from archflow.adapters.model_formats import ConversionError, Mesh, Scene
from tests.integration.test_blender_cad import _request as _blender_request
from tests.integration.test_cad_backend_contract import _controlled_rhino, _request as _rhino_request


class _Discovery:
    def __init__(self, *, system="Windows", rhino_version="8"):
        self.system, self.rhino_version, self.calls = system, rhino_version, []

    def discover(self, product):
        self.calls.append(product)
        return Discovery(self.system, (Installation(
            product, Path(tempfile.gettempdir()) / "private-user" / product / f"{product}.exe",
            self.rhino_version if product == "rhino" else None, "standard-install-directory",
        ),))


def _pack(status, pack_id):
    return next(row for row in status["packs"] if row["manifest"]["packId"] == pack_id)


@contextmanager
def _controlled_blender(*, mode="valid"):
    """Only replace host I/O; retain BlenderBackend's plan/readback validators."""
    calls = []

    def worker(executable, workspace, timeout, action, *arguments):
        calls.append(action)
        if action == "build":
            if mode != "exit-only":
                # A later inspect call must reopen the saved fixture bytes.
                Path(arguments[1]).write_bytes(Path(arguments[0]).read_bytes())
            return SimpleNamespace(returncode=0, stdout="")
        if mode == "missing-readback":
            return SimpleNamespace(returncode=0, stdout="process exited successfully")
        plan = json.loads(Path(arguments[0]).read_bytes())
        rows = []
        for row in plan["objects"]:
            vertices = row["vertices"]
            rows.append({
                "object_id": row["object_id"], "object_digest": row["object_digest"],
                "user_text": row["semantics"]["user_text"], "layer": row["semantics"]["layer"],
                "visible": row["semantics"].get("visible", True), "type": "MESH", "closed": True,
                "vertices": vertices, "faces": row["faces"], "volume": row["volume"],
                "bounds": {edge: [aggregate(point[i] for point in vertices) for i in range(3)]
                           for edge, aggregate in (("min", min), ("max", max))},
                "material": row.get("material"),
            })
        readback = {**plan, "objects": rows, "blender_version": "controlled-test-host",
                    "unit_scale": blender_cad.UNIT_SETTINGS[plan["length_unit"]][1]}
        if mode == "wrong-binding":
            readback["binding_json"] = "{}"
        return SimpleNamespace(returncode=0, stdout=blender_cad.READBACK_PREFIX + json.dumps(readback))

    with patch.object(blender_cad, "resolve_blender_executable", return_value="controlled-blender"), \
         patch.object(blender_cad, "_run_worker", side_effect=worker), \
         patch.object(subprocess, "Popen", side_effect=AssertionError("unexpected CAD launch")), \
         patch.object(subprocess, "run", side_effect=AssertionError("unexpected CAD launch")):
        yield calls


class IntegrationStatusTests(unittest.TestCase):
    def test_detected_software_is_never_qualification_and_status_does_not_rescan(self):
        discovery = _Discovery()
        manager = IntegrationPackManager(discovery=discovery)
        first = manager.status()
        self.assertEqual(len(discovery.calls), 3)
        for pack in first["packs"]:
            self.assertTrue(pack["software"]["detected"])
            self.assertFalse(any(row["status"] == "ready" for row in pack["capabilities"]))
            self.assertFalse(any(row["available"] for row in pack["workflows"]))
        self.assertEqual(manager.status(), first)
        self.assertEqual(len(discovery.calls), 3)
        manager.status(rescan=True)
        self.assertEqual(len(discovery.calls), 6)

    def test_missing_disabled_and_mismatched_pack_are_distinct_and_refuse_execution(self):
        installations = (
            ((), "not-installed"),
            ((PackInstallation("blender", "1", ("blender-worker",), enabled=False),), "disabled"),
            ((PackInstallation("blender", "old", ("blender-worker",)),), "blocked-version"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = _blender_request(Path(temporary))
            for installed, expected in installations:
                with self.subTest(status=expected):
                    manager = IntegrationPackManager(discovery=_Discovery(), installations=installed)
                    self.assertEqual(manager.capability_status("blender", "model.execute")["status"], expected)
                    with patch.object(manager.backend("blender"), "execute", side_effect=AssertionError("blocked host ran")), \
                         self.assertRaises(IntegrationUnavailable) as refused:
                        manager.qualify_cad("blender", request)
                    self.assertEqual(refused.exception.status, expected)

    def test_sketchup_bundled_reader_does_not_supply_missing_live_extension(self):
        manager = IntegrationPackManager(discovery=_Discovery())
        pack = _pack(manager.status(), "sketchup")
        self.assertEqual({row["id"]: row["status"] for row in pack["capabilities"]}, {
            "source.read": "not-qualified", "viewport.observe": "not-installed", "viewport.capture": "not-installed",
        })
        self.assertFalse(pack["workflows"][0]["available"])
        manager = IntegrationPackManager(discovery=_Discovery(), installations=(PackInstallation(
            "sketchup", "1", ("sketchup-sdk-reader", "sketchup-live-extension")),))
        self.assertEqual(manager.capability_status("sketchup", "viewport.observe")["status"], "unsupported")
        self.assertFalse(_pack(manager.status(), "sketchup")["workflows"][0]["available"])

    def test_unsupported_os_and_unknown_capability_are_explicit_but_folder_version_is_only_a_hint(self):
        manager = IntegrationPackManager(discovery=_Discovery(system="Linux"))
        self.assertEqual(manager.capability_status("rhino", "model.execute")["status"], "unsupported-os")
        self.assertEqual(manager.capability_status("blender", "model.execute")["status"], "not-qualified")
        old_rhino = IntegrationPackManager(discovery=_Discovery(rhino_version="7"))
        self.assertEqual(old_rhino.capability_status("rhino", "model.execute")["status"], "not-qualified")
        for product, capability in (("rhino", "viewport.capture"), ("revit", "model.execute")):
            with self.subTest(product=product), self.assertRaises(IntegrationUnavailable) as refused:
                manager.capability_status(product, capability)
            self.assertEqual(refused.exception.status, "unsupported")

    def test_status_has_no_machine_paths_or_execution_write_dll_or_network_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "Blender Foundation" / "Blender 4.3" / "blender.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"discovery-only fixture")
            manager = IntegrationPackManager(discovery=SoftwareDiscoveryRegistry(
                system="Windows", program_roots=(root,), registry_candidates=(), path_candidates=()))
            before = tuple(root.rglob("*"))
            with ExitStack() as guards:
                for owner, names in (
                    (subprocess, ("Popen", "run")), (ctypes, ("CDLL",)),
                    (socket, ("create_connection",)), (urllib.request, ("urlopen",)),
                    (Path, ("write_text", "write_bytes", "mkdir")),
                ):
                    for name in names:
                        guards.enter_context(patch.object(owner, name, side_effect=AssertionError(f"status used {name}")))
                if hasattr(ctypes, "WinDLL"):
                    guards.enter_context(patch.object(ctypes, "WinDLL", side_effect=AssertionError("status loaded DLL")))
                status = manager.status()
                self.assertEqual(manager.status(rescan=True), status)
            self.assertEqual(tuple(root.rglob("*")), before)
            self.assertEqual(executable.read_bytes(), b"discovery-only fixture")
            public = json.dumps(status)
            self.assertNotIn(root.name, public)
            self.assertTrue(_pack(status, "blender")["software"]["detected"])
            self.assertEqual(_pack(status, "blender")["capabilities"][0]["status"], "not-qualified")

    def test_future_revit_addin_contract_is_representable_without_registering_a_backend(self):
        before = tuple(cad_backend.CAD_BACKEND_REGISTRY)
        pack = IntegrationPack("revit", "Revit", "1", (
            PackComponent("revit-addin", "dotnet-addin", "optional-payload", "application-extension",
                          protocol="local-http", events=("document.changed",)),
        ), (PackCapability("document.observe", "revit-addin", None,
                           ("observe-enabled-application",), ("Windows",)),),
            (PackWorkflow("revit-document-to-board", ("document.observe",)),))
        manifest = pack.manifest()
        self.assertEqual(manifest["components"][0]["bridge"], "dotnet-addin")
        self.assertEqual(manifest["workflows"][0]["requires"], ["document.observe"])
        self.assertEqual(tuple(cad_backend.CAD_BACKEND_REGISTRY), before)
        self.assertNotIn("revit", IntegrationPackManager(discovery=_Discovery()).packs)


class IntegrationQualificationTests(unittest.TestCase):
    def test_blender_uses_registered_background_worker_and_qualifies_only_its_executed_capability(self):
        manager = IntegrationPackManager(discovery=_Discovery())
        self.assertIs(manager.backend("blender"), cad_backend.CAD_BACKEND_REGISTRY["blender"])
        pack = _pack(manager.status(), "blender")
        self.assertEqual(pack["installation"]["components"], ["blender-worker"])
        self.assertEqual(pack["manifest"]["components"][0]["installTarget"], "monkeyhub")
        with tempfile.TemporaryDirectory() as temporary, _controlled_blender() as calls:
            request = _blender_request(Path(temporary))
            result = manager.qualify_cad("blender", request)
            self.assertEqual(calls, ["build", "inspect"])
            self.assertTrue(result.readback_verified)
            self.assertTrue((request.speculative_workspace / result.artifacts[0].relative_path).read_bytes())
            result.validate(request, "blender")
        self.assertEqual(manager.capability_status("blender", "model.execute")["status"], "ready")
        self.assertEqual(manager.capability_status("blender", "visualization.project")["status"], "not-qualified")
        self.assertFalse(_pack(manager.status(), "blender")["workflows"][0]["available"])
        manager.rescan()
        self.assertEqual(manager.capability_status("blender", "model.execute")["status"], "not-qualified")

    def test_blender_process_exit_or_wrong_readback_never_qualifies_and_replaces_previous_success(self):
        manager = IntegrationPackManager(discovery=_Discovery())
        for mode in ("valid", "exit-only", "valid", "missing-readback", "valid", "wrong-binding"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary, _controlled_blender(mode=mode):
                result = manager.qualify_cad("blender", _blender_request(Path(temporary)))
                expected = "ready" if mode == "valid" else "failed"
                self.assertEqual(manager.capability_status("blender", "model.execute")["status"], expected)
                self.assertEqual(result.readback_verified, mode == "valid")

    def test_rescan_during_execution_does_not_restore_previous_snapshot_qualification(self):
        manager = IntegrationPackManager(discovery=_Discovery())
        backend = manager.backend("blender")
        execute = backend.execute

        def finish_previous_snapshot(request):
            result = execute(request)
            manager.rescan()
            return result

        with tempfile.TemporaryDirectory() as temporary, _controlled_blender(), \
             patch.object(backend, "execute", side_effect=finish_previous_snapshot):
            result = manager.qualify_cad("blender", _blender_request(Path(temporary)))
        self.assertTrue(result.readback_verified)
        current = manager.capability_status("blender", "model.execute")
        self.assertEqual(current["status"], "not-qualified")
        self.assertIsNone(current["checkedAt"])

    def test_invalid_backend_result_cannot_preserve_a_previous_success(self):
        manager = IntegrationPackManager(discovery=_Discovery())
        with tempfile.TemporaryDirectory() as temporary, _controlled_blender():
            request = _blender_request(Path(temporary))
            result = manager.qualify_cad("blender", request)
            invalid = replace(result, readback_verified=False)
            with patch.object(manager.backend("blender"), "execute", return_value=invalid), \
                 self.assertRaises(CadExecutionError):
                manager.qualify_cad("blender", request)
            self.assertEqual(manager.capability_status("blender", "model.execute")["status"], "failed")
            refreshed = replace(request, artifact_stem="second-fixture")
            manager.qualify_cad("blender", refreshed)
            private_detail = str(Path(temporary) / "private-project" / "model.blend")
            with patch.object(manager.backend("blender"), "execute", side_effect=OSError(private_detail)), \
                 self.assertRaises(OSError):
                manager.qualify_cad("blender", refreshed)
            self.assertNotIn(Path(temporary).name, json.dumps(manager.status()))
        self.assertEqual(manager.capability_status("blender", "model.execute")["status"], "failed")

    def test_rhino_uses_registered_backend_and_retains_existing_host_readback_requirements(self):
        # A standard Rhino 7 folder cannot rule out a separately installed COM8 host.
        manager = IntegrationPackManager(discovery=_Discovery(rhino_version="7"))
        self.assertIs(manager.backend("rhino"), cad_backend.CAD_BACKEND_REGISTRY["rhino"])
        for bad_inspection in (False, True):
            with self.subTest(bad_inspection=bad_inspection), tempfile.TemporaryDirectory() as temporary:
                root, plans = Path(temporary), []
                with _controlled_rhino(root, bad_inspection=bad_inspection, plans=plans) as options:
                    request = _rhino_request(root, backend_options=options)
                    result = manager.qualify_cad("rhino", request)
                    result.validate(request, "rhino")
                self.assertEqual(len(plans), 1)
                self.assertEqual(manager.capability_status("rhino", "model.execute")["status"],
                                 "failed" if bad_inspection else "ready")
                self.assertEqual(manager.capability_status("rhino", "model.patch")["status"], "not-qualified")

    def test_sketchup_reader_version_refusal_propagates_and_source_success_does_not_enable_live_capabilities(self):
        from archflow.adapters import sketchup_reader

        manager = IntegrationPackManager(discovery=_Discovery())
        data, sdk_path = b"controlled SKP source", "configured-SketchUpAPI.dll"
        scene = Scene([Mesh("triangle", [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])], "meter", [])
        with patch.object(sketchup_reader, "read_skp", return_value=scene) as read:
            self.assertIs(manager.qualify_sketchup_read(data, sdk_path=sdk_path), scene)
            read.assert_called_once_with(data, sdk_path=sdk_path)
        self.assertEqual(manager.capability_status("sketchup", "source.read")["status"], "ready")
        for capability in ("viewport.observe", "viewport.capture"):
            self.assertEqual(manager.capability_status("sketchup", capability)["status"], "not-installed")
        self.assertFalse(_pack(manager.status(), "sketchup")["workflows"][0]["available"])
        error = ConversionError("SKP reader requires SketchUp C API 9.0 or newer.")
        with patch.object(sketchup_reader, "read_skp", side_effect=error), \
             self.assertRaises(ConversionError) as refused:
            manager.qualify_sketchup_read(data, sdk_path=sdk_path)
        self.assertIs(refused.exception, error)
        self.assertEqual(manager.capability_status("sketchup", "source.read")["status"], "failed")

    def test_projection_wrapper_preserves_existing_failure_and_does_not_qualify_a_sibling(self):
        from archflow.adapters import blender_projection

        manager = IntegrationPackManager(discovery=_Discovery())
        request, source = object(), object()
        receipt = {"status": "failed", "artifacts": [],
                   "failures": [{"code": "blender.execution_failed", "detail": "controlled worker failure"}]}
        with patch.object(blender_projection, "execute_blender_projection", return_value=receipt) as execute, \
             patch.object(blender_projection, "verify_projection_artifacts", side_effect=AssertionError("failed projection validated")):
            result = manager.qualify_blender_projection(request, source, blender_executable="controlled-blender")
            execute.assert_called_once_with(request, source, blender_executable="controlled-blender")
        self.assertIs(result, receipt)
        self.assertEqual(manager.capability_status("blender", "visualization.project")["status"], "failed")
        self.assertEqual(manager.capability_status("blender", "model.execute")["status"], "not-qualified")
        self.assertFalse(_pack(manager.status(), "blender")["workflows"][0]["available"])


if __name__ == "__main__":
    unittest.main()
