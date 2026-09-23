"""Provider selection and safe discovery; native/cloud doubles are NOT converters."""
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from archflow.adapters.local_cad_discovery import discover_local_cad, Discovery
from archflow.adapters.model_formats import GLB, Mesh, Scene, convert, ConversionError
from archflow.adapters.model_providers import (
    Capability, ConversionCoordinator, ConversionFailure, ConvertedFile, InProcessMeshProvider,
    LocalSoftwareProvider, NO_EXECUTOR, Validation, preview_policy,
)


def fixture():
    return GLB().write(Scene([Mesh("triangle", [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])], "Meters", []))


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"non-executable fixture: never run")
        return path

    def test_windows_detects_each_product_without_launching(self):
        self.file("SketchUp/SketchUp 2024/SketchUp.exe")
        self.file("Autodesk/AutoCAD 2025/acad.exe")
        self.file("Autodesk/AutoCAD 2025/accoreconsole.exe")
        with patch.object(subprocess, "Popen", side_effect=AssertionError("must not execute")), \
             patch("os.system", side_effect=AssertionError("must not execute")):
            discovered = discover_local_cad(system="Windows", program_roots=[self.root], registry_candidates=[])
        self.assertEqual({i.product for i in discovered.installations}, {"sketchup", "autocad", "autocad-core"})
        self.assertEqual({i.version_hint for i in discovered.installations}, {"2024", "2025"})
        self.assertTrue(all(i.public()["version"] is None and not i.public()["versionVerified"] for i in discovered.installations))
        self.assertNotIn(str(self.root), str([i.public() for i in discovered.installations]))

    def test_custom_app_path_deduplicates_and_rejects_missing_file(self):
        path = self.file("custom/SketchUp.exe")
        d = discover_local_cad(system="Windows", program_roots=[], registry_candidates=[
            ("sketchup",path,"windows-app-path"), ("sketchup",path,"windows-app-path"),
            ("autocad",self.root/"missing.exe","windows-app-path")])
        self.assertEqual(len(d.installations),1)
        self.assertIsNone(d.installations[0].version_hint)

    def test_missing_installation_stays_blocked(self):
        d = discover_local_cad(system="Windows",program_roots=[self.root],registry_candidates=[])
        provider = LocalSoftwareProvider("SketchUpDesktopProvider","sketchup","local desktop",d,("skp",),("skp",))
        self.assertFalse(provider.observation()["detected"])
        self.assertIn("not found",provider.capability("skp","skp").reason)
        self.assertFalse(provider.capability("skp","skp").available)

    def test_detected_old_or_unknown_version_never_enables_native_execution(self):
        self.file("SketchUp/SketchUp 2018/SketchUp.exe")
        d = discover_local_cad(system="Windows",program_roots=[self.root],registry_candidates=[])
        provider = LocalSoftwareProvider("SketchUpDesktopProvider","sketchup","local desktop",d,("skp",),("glb",))
        self.assertTrue(provider.observation()["detected"])
        self.assertEqual(provider.observation()["versionCompatibility"],"unverified")
        self.assertFalse(provider.capability("skp","glb").verified)
        with self.assertRaisesRegex(ConversionError,NO_EXECUTOR):
            provider.convert(b"original", "skp", "glb")
        self.assertFalse(provider.validate(b"original","skp","glb",ConvertedFile(b"fake")).passed)

    def test_autocad_desktop_does_not_imply_core_console(self):
        self.file("Autodesk/AutoCAD 2025/acad.exe")
        d = discover_local_cad(system="Windows",program_roots=[self.root],registry_candidates=[])
        self.assertEqual([i.product for i in d.installations],["autocad"])

    def test_mac_bundle_does_not_imply_windows_core_console(self):
        self.file("SketchUp 2024/SketchUp.app/Contents/MacOS/SketchUp")
        d = discover_local_cad(system="Darwin",application_roots=[self.root])
        self.assertEqual([i.product for i in d.installations],["sketchup"])

    def test_other_os_does_not_probe_windows_directories(self):
        with patch("pathlib.Path.glob", side_effect=AssertionError("unexpected scan")):
            d = discover_local_cad(system="Linux")
        self.assertFalse(d.installations)
        self.assertTrue(d.diagnostics)


class TaggedMesh(InProcessMeshProvider):
    """Real mesh converter with synthetic hosting metadata to test scheduling only."""
    def __init__(self, name, mode="headless", host="in-process", cap=None):
        self.name, self.execution_mode, self.execution_host, self.cap = name, mode, host, cap
        self.calls = 0

    def capability(self, source, target):
        return self.cap or super().capability(source,target)

    def convert(self,*args):
        self.calls += 1
        return super().convert(*args)


class ProviderTests(unittest.TestCase):
    def test_verified_local_preferred_to_mesh_sdk_and_cloud(self):
        local = TaggedMesh("native-double","local desktop","local-application")
        cloud = TaggedMesh("cloud-double","cloud","remote")
        sdk = TaggedMesh("sdk-double","SDK","in-process")
        output, info = convert(fixture(),"glb","3dm",providers=[cloud,sdk,InProcessMeshProvider(),local])
        self.assertTrue(output.startswith(b"3D Geometry File Format"))
        self.assertEqual(info["provider"],"native-double")
        self.assertEqual(info["executionMode"],"local desktop")
        self.assertEqual((local.calls,cloud.calls,sdk.calls),(1,0,0))

    def test_unverified_provider_is_never_called_even_when_it_claims_available(self):
        local = TaggedMesh("unverified","local desktop","local-application",Capability(True,False,"available-local-host","unqualified"))
        _, info = convert(fixture(),"glb","3dm",providers=[local,InProcessMeshProvider()])
        self.assertEqual(info["provider"],"InProcessMeshProvider")
        self.assertEqual(local.calls,0)

    def test_version_or_license_blocked_provider_allows_configured_alternative(self):
        for status,reason in (("blocked-runtime","incompatible version"),("blocked-license","license unavailable")):
            local = TaggedMesh("blocked","headless","local-application",Capability(False,True,status,reason))
            cloud = TaggedMesh("configured-cloud-double","cloud","remote")
            _, info = convert(fixture(),"glb","3dm",providers=[local,cloud])
            self.assertEqual(info["provider"],cloud.name)
            self.assertEqual(info["providerCandidates"][0]["status"],status)
            self.assertEqual(local.calls,0)

    def test_empty_provider_set_does_not_auto_create_or_call_cloud(self):
        with self.assertRaises(ConversionFailure) as raised:
            convert(b"source","dwg","glb",providers=[])
        self.assertEqual(str(raised.exception),NO_EXECUTOR)
        self.assertIsNone(raised.exception.report["provider"])
        self.assertEqual(raised.exception.report["outputValidation"]["status"],"not-run")

    def test_failed_optional_probe_does_not_block_verified_provider(self):
        native = TaggedMesh("probe-failure", "local desktop", "local-application")
        with patch.object(native, "capability", side_effect=OSError("private path")):
            _, report = convert(fixture(), "glb", "3dm", providers=[native, InProcessMeshProvider()])
        self.assertEqual(report['provider'], 'InProcessMeshProvider')
        self.assertEqual(report['providerCandidates'][0]['reason'], 'Capability probe failed: OSError')
        self.assertEqual(native.calls, 0)

    def test_no_provider_fallback_after_execution_failure(self):
        local = TaggedMesh("broken","headless","local-application")
        cloud = TaggedMesh("unused-cloud","cloud","remote")
        with patch.object(local,"convert",side_effect=RuntimeError("internal path")):
            with self.assertRaises(ConversionFailure) as raised:
                convert(fixture(),"glb","3dm",providers=[local,cloud])
        self.assertEqual(cloud.calls,0)
        self.assertEqual(raised.exception.report["provider"],"broken")
        self.assertNotIn("internal path",str(raised.exception))

    def test_false_validator_blocks_output_with_provider_provenance(self):
        provider = TaggedMesh("invalid")
        with patch.object(provider,"validate",return_value=Validation(False,reason="cold reopen failed")):
            with self.assertRaises(ConversionFailure) as raised:
                convert(fixture(),"glb","3dm",providers=[provider])
        self.assertEqual(raised.exception.report["outputValidation"]["status"],"failed")
        self.assertEqual(raised.exception.report["providerVersion"],provider.version)

    def test_empty_output_cannot_be_certified(self):
        provider = TaggedMesh("empty")
        with patch.object(provider,"convert",return_value=ConvertedFile(b"")):
            with self.assertRaisesRegex(ConversionFailure,"no output bytes"):
                convert(fixture(),"glb","3dm",providers=[provider])

    def test_runtime_missing_changes_capability_and_dispatch(self):
        with patch("archflow.adapters.model_providers.ThreeDM",side_effect=ConversionError("missing runtime")):
            coordinator = ConversionCoordinator([InProcessMeshProvider()])
            routes = coordinator.capabilities()
            self.assertFalse(any(row["available"] for row in routes))
            with self.assertRaisesRegex(ConversionFailure,NO_EXECUTOR):
                coordinator.convert(fixture(),"glb","3dm")

    def test_manifest_keeps_delivery_separate_from_nonexistent_previews(self):
        _, report = convert(fixture(),"glb","3dm")
        self.assertEqual(report["outputValidation"]["status"],"passed")
        self.assertEqual(report["previewArtifacts"],[])
        self.assertEqual(report["artifactRoles"]["outputArtifact"],"delivery")
        self.assertFalse(report["usedIntermediateFormats"])
        self.assertIn("materials",report["losses"])

    def test_2d_and_unknown_dwg_preview_never_requires_glb(self):
        for dimension in (None,2):
            policy = preview_policy("dwg",dimension)
            self.assertNotIn("glb",policy["suggestedFormats"])
            self.assertIn("pdf",policy["suggestedFormats"])
            self.assertFalse(policy["mayInvent3DGeometry"])

    def test_coordinator_refuses_dimensional_promotion_or_unknown_dwg(self):
        # Synthetic validator facts exercise the boundary; no DWG conversion claim.
        p = TaggedMesh("dimension-double")
        p.input_formats = ("dwg",)
        p.output_formats = ("glb",)
        p.cap = Capability(True,True,"available","test only")
        for source_dim in (2,None):
            with patch.object(p,"convert",return_value=ConvertedFile(b"synthetic")), \
                 patch.object(p,"validate",return_value=Validation(True,("synthetic-check",),source_dimension=source_dim,output_dimension=3)):
                with self.assertRaisesRegex(ConversionFailure,"2D|dimensionality"):
                    convert(b"original","dwg","glb",providers=[p])

    def test_intermediate_formats_are_reported_not_inferred(self):
        p = TaggedMesh("intermediate-double")
        original = p.convert
        def convert_with_intermediate(*args):
            return replace(original(*args), intermediate_formats=("test-intermediate",))
        with patch.object(p,"convert",side_effect=convert_with_intermediate):
            _, report = convert(fixture(),"glb","3dm",providers=[p])
        self.assertEqual(report["intermediateFormats"],["test-intermediate"])
        self.assertTrue(report["usedIntermediateFormats"])
