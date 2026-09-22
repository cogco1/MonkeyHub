"""Retained Blender results remain readable after retiring its submission path."""
import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from PIL import Image
from fastapi.testclient import TestClient
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB
from archflow_studio_api.application.artifacts import save_document
from archflow_studio_api.application.binding import bound_project
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import make_project, PROJECT_ID

class RenderTests(unittest.TestCase):
    def test_legacy_result_is_readable_but_blender_submission_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);repo,_=make_project(root)
            settings=StudioSettings(project_dir=root/PROJECT_ID,cad_export='off');app=create_app(settings)
            with TestClient(app) as c:
                self.assertEqual(c.post('/api/render/jobs',json={}).status_code,405)
                binding=bound_project(app.state);job_id='render-'+uuid4().hex
                run=repo.create_run(job_id);out=BytesIO();Image.new('RGB',(64,64),'white').save(out,format='PNG')
                doc=save_document(binding,job_id,'old-render.png','image/png',base64.b64encode(out.getvalue()).decode(),view_recipe={'kind':'render','presentation':{'samples':16},'receiptRef':'legacy'})
                repo.put_json(run=run,destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=job_id),record_kind=STUDIO_RENDER_JOB,payload={
                    'schema':'StudioRenderJob@1','jobId':job_id,'projectId':PROJECT_ID,'instance':'old-runtime','sequence':2,'status':'succeeded','createdAt':'2026-09-21T00:00:00+00:00','source':{'sha256':'a'*64},'fileName':'mesh.3dm','documentSha256':doc.asset_sha256})
            with TestClient(create_app(settings)) as c:
                job=c.get('/api/render/jobs/'+job_id).json()
                self.assertEqual(job['status'],'succeeded');self.assertIsNone(job['snapshot'])
                png=c.get('/api/documents/'+doc.asset_sha256+'/bytes',params={'runId':job_id,'download':'true'})
                self.assertEqual(png.content,out.getvalue())
                self.assertIn('attachment;',png.headers['content-disposition'])
