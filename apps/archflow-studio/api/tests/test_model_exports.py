"""Generated, non-private mesh fixtures and real P036/job/API integration."""
import base64
import json
import math
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow.adapters.model_formats import GLB, ThreeDM, Mesh, Scene, convert, ConversionError
from .support import make_project, PROJECT_ID, REFERENCE_RUN_ID


def fixture():
    return Scene([Mesh("Offset triangle", [(1, 2, 3), (2, 2, 3), (1, 3, 3)], [(0, 1, 2)], "Structure")], "Meters", [])


class ExportJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository, _ = make_project(Path(self.temp.name))
        self.settings = StudioSettings(project_dir=Path(self.temp.name)/PROJECT_ID, cad_export="off")
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.jobs.shutdown)
        self.head = self.repository.read_head()

    def submit(self, target="3dm", data=None, name="source.glb", **extra):
        return self.client.post("/api/exports", json={"targetFormat":target, "upload":{
            "fileName":name,"contentBase64":base64.b64encode(data if data is not None else GLB().write(fixture())).decode(),
            "attachmentId":"original-chat-attachment"}, **extra})

    def finish(self, response):
        self.assertEqual(response.status_code,202,response.text)
        path = response.json()["statusPath"]
        for _ in range(200):
            result = self.client.get(path)
            self.assertEqual(result.status_code,200,result.text)
            if result.json()["status"] in ("succeeded","failed"):
                return result.json()
            time.sleep(.01)
        self.fail("Job did not finish")

    def test_success_download_provenance_and_cold_read(self):
        result = self.finish(self.submit())
        self.assertEqual(result["status"],"succeeded",result)
        self.assertEqual(result["sourceAttachmentId"],"original-chat-attachment")
        self.assertEqual(result["sourceFormat"],"glb")
        self.assertTrue(result["outputArtifact"])
        self.assertEqual(self.repository.read_head(),self.head)
        download = self.client.get(result["downloadPath"])
        self.assertEqual(download.status_code,200)
        self.assertTrue(download.content.startswith(b"3D Geometry File Format"))
        with TestClient(create_app(self.settings)) as cold:
            self.assertEqual(cold.get(result["downloadPath"]).content,download.content)
            # The retained record still says what the display state is made of after restart.
            self.assertEqual(cold.get(f'/api/exports/{result["exportId"]}').json()["display"], result["display"])
        self.assertEqual(result["display"]["normals"]["computed"], 1)
        self.assertEqual(result["display"]["material"]["defaulted"], 1)
        reopened = ThreeDM().read(download.content).meshes[0]
        self.assertEqual(reopened.normals, [(0, 0, 1)] * 3)
        self.assertGreater(sum(reopened.material.color[:3]), 3 * 128)

    def test_failure_retains_source_and_never_downloads(self):
        result = self.finish(self.submit(data=b"invalid"))
        self.assertEqual(result["status"],"failed")
        self.assertIn("signature",result["failureReason"])
        self.assertIsNone(result["outputArtifact"])
        source = self.repository.layout.resolve_relative(result["sourceArtifact"]["relative_path"])
        self.assertEqual(source.read_bytes(),b"invalid")
        self.assertEqual(self.client.get(f'/api/exports/{result["exportId"]}/bytes').status_code,409)

    def test_provider_report_survives_restart(self):
        result = self.finish(self.submit())
        self.assertEqual(result['provider'], 'InProcessMeshProvider')
        self.assertTrue(result['providerVersion'])
        self.assertEqual(result['executionMode'], 'headless')
        self.assertFalse(result['usedIntermediateFormats'])
        self.assertEqual(result['outputValidation']['status'], 'passed')
        self.assertIn('layers', result['losses'])
        self.assertEqual(result['previewArtifacts'], [])
        self.assertIsNone(result['nativeOutputArtifact'])
        with TestClient(create_app(self.settings)) as cold:
            self.assertEqual(cold.get('/api/exports/' + result['exportId']).json(), result)

    def test_provider_validation_failure_persists_and_blocks_download(self):
        from archflow.adapters.model_providers import InProcessMeshProvider, Validation
        with patch.object(InProcessMeshProvider, 'validate', return_value=Validation(False, reason='Bounds mismatch')):
            result = self.finish(self.submit())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['provider'], 'InProcessMeshProvider')
        self.assertEqual(result['failureCode'], 'VALIDATION_FAILED')
        self.assertEqual(result['outputValidation']['status'], 'failed')
        self.assertIsNone(result['outputArtifact'])
        self.assertEqual(self.client.get('/api/exports/' + result['exportId'] + '/bytes').status_code, 409)

    def test_dwg_original_retained_without_invented_preview(self):
        data = b'non-executable DWG placeholder; no native conversion claimed'
        result = self.finish(self.submit('glb', data=data, name='drawing.dwg'))
        self.assertEqual(result['failureCode'], 'NO_CONFIGURED_EXECUTOR')
        self.assertNotIn('glb', result['previewPolicy']['suggestedFormats'])
        self.assertEqual(result['previewArtifacts'], [])
        self.assertEqual(self.repository.layout.resolve_relative(result['sourceArtifact']['relative_path']).read_bytes(), data)

    def test_capability_endpoint_does_not_enable_detected_software(self):
        rows = self.client.get('/api/exports/capabilities').json()
        self.assertEqual(len(rows), 12)
        for row in rows:
            if 'skp' in (row['sourceFormat'], row['targetFormat']) or 'dwg' in (row['sourceFormat'], row['targetFormat']):
                self.assertFalse(row['available'])
                self.assertEqual(row['reason'], '当前没有配置可用的执行器')
                self.assertTrue(any('discovery' in provider for provider in row['providers']))

    def test_unavailable_skp_and_dwg_are_failed_jobs(self):
        for target in ("skp","dwg"):
            result = self.finish(self.submit(target))
            self.assertEqual(result["status"],"failed")
            self.assertEqual(result["failureReason"], "当前没有配置可用的执行器")
            self.assertEqual(result["failureCode"], "NO_CONFIGURED_EXECUTOR")
            self.assertIsNone(result["provider"])
            self.assertEqual(result["outputValidation"]["status"], "not-run")
            self.assertTrue(result["providerCandidates"])

    def test_converter_runtime_failure_is_retained(self):
        with patch('archflow_studio_api.application.model_exports.convert', side_effect=RuntimeError('converter stopped')):
            result = self.finish(self.submit())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['failureReason'],'converter stopped')
        self.assertIsNone(result['outputArtifact'])

    def test_partial_native_project_export_is_refused(self):
        from .support import retain_rhino_receipt
        state = self.client.get('/api/state',params={'runId':REFERENCE_RUN_ID}).json()
        with patch('tests.support.RHINO_DESIGN_STATE_DIGEST', state['stateDigest']):
            retain_rhino_receipt(self.repository,self.repository.load_run(REFERENCE_RUN_ID),
                stage_id='partial',file_name='partial.3dm',payload_bytes=ThreeDM().write(fixture()))
        response = self.client.post('/api/exports',json={'targetFormat':'glb','projectRevision':{
            'runId':REFERENCE_RUN_ID,'stateDigest':state['stateDigest']}})
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(response.json()['code'],'EXPORT_PROJECT_SOURCE_UNAVAILABLE')

    def test_missing_and_conflicting_sources_request_clarification(self):
        r = self.client.post("/api/exports",json={"targetFormat":"glb"})
        self.assertEqual(r.status_code,422)
        self.assertEqual(r.json()["code"],"EXPORT_SOURCE_AMBIGUOUS")
        r = self.submit(projectRevision={"runId":REFERENCE_RUN_ID,"stateDigest":"0"*64})
        self.assertEqual(r.status_code,422)
        self.assertEqual(r.json()["code"],"EXPORT_SOURCE_AMBIGUOUS")

    def test_stale_project_revision_is_not_replaced_by_upload(self):
        r = self.client.post("/api/exports",json={"targetFormat":"glb", "projectRevision":{
            "runId":REFERENCE_RUN_ID,"stateDigest":"0"*64}})
        self.assertEqual(r.status_code,409)
        self.assertEqual(r.json()["code"],"EXPORT_REVISION_MISMATCH")

    def test_exact_composed_project_export_preserves_state(self):
        state = self.client.get('/api/state', params={'runId':REFERENCE_RUN_ID}).json()
        digest = state['stateDigest']
        data = ThreeDM().write(fixture())
        registered = self.client.post('/api/model-assets', json={
            'projectId':PROJECT_ID, 'runId':REFERENCE_RUN_ID, 'stateDigest':digest,
            'fileName':'complete.3dm', 'contentBase64':base64.b64encode(data).decode()})
        self.assertEqual(registered.status_code,201,registered.text)
        before = self.client.get('/api/state', params={'runId':REFERENCE_RUN_ID}).json()
        result = self.finish(self.client.post('/api/exports',json={'targetFormat':'glb',
            'projectRevision':{'runId':REFERENCE_RUN_ID,'stateDigest':digest}}))
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['sourceProjectRevision']['stateDigest'],digest)
        self.assertEqual(self.client.get('/api/state',params={'runId':REFERENCE_RUN_ID}).json(),before)
        self.assertEqual(self.repository.read_head(),self.head)

    def test_retained_source_artifact_can_be_selected_again(self):
        first = self.finish(self.submit())
        second = self.finish(self.client.post('/api/exports',json={'targetFormat':'glb',
            'sourceArtifactId':first['sourceArtifact']['sha256']}))
        self.assertEqual(second['status'],'succeeded',second)
        self.assertEqual(second['sourceSha256'],first['sourceSha256'])
        self.assertFalse(second['converted'])

    def test_tampered_output_cannot_be_downloaded(self):
        result = self.finish(self.submit())
        self.repository.layout.resolve_relative(result["outputArtifact"]["relative_path"]).write_bytes(b"tampered")
        self.assertEqual(self.client.get(result["downloadPath"]).status_code,409)
