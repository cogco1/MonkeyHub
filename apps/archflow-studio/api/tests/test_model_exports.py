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

    def test_failure_retains_source_and_never_downloads(self):
        result = self.finish(self.submit(data=b"invalid"))
        self.assertEqual(result["status"],"failed")
        self.assertIn("signature",result["failureReason"])
        self.assertIsNone(result["outputArtifact"])
        source = self.repository.layout.resolve_relative(result["sourceArtifact"]["relative_path"])
        self.assertEqual(source.read_bytes(),b"invalid")
        self.assertEqual(self.client.get(f'/api/exports/{result["exportId"]}/bytes').status_code,409)

    def test_unavailable_skp_and_dwg_are_failed_jobs(self):
        for target in ("skp","dwg"):
            result = self.finish(self.submit(target))
            self.assertEqual(result["status"],"failed")
            self.assertIn("unavailable",result["failureReason"])

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
