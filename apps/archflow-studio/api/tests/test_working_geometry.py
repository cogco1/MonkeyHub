"""Real OCCT Working Head projected into existing scene/drawing consumers."""
import base64
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from archflow.adapters.occt_backend import occt_available
from archflow.project.repository import FilesystemProjectRepository
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .collaboration_support import seed_collaboration_project
from .test_candidate import CandidateTestCase
from .test_working_source import adopt
from .test_render_scene import source

@unittest.skipUnless(occt_available(), "real OCCT required")
class WorkingGeometryTests(CandidateTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);project=self.root/'demo-project'
        seeded=seed_collaboration_project(project)
        self.repository=FilesystemProjectRepository.open(project)
        self.settings=StudioSettings(project_dir=project,reference_run=seeded['referenceRun'],cad_export='occt')
        self.app=create_app(self.settings);self.client=TestClient(self.app)
        self.addCleanup(self.client.close);self.addCleanup(self.app.state.jobs.shutdown);self.addCleanup(self.app.state.render_jobs.shutdown)
        self.state_digest=seeded['modelSource']['stateDigest']

    def test_native_parameter_revision_changes_scene_and_drawings_without_geometry_selection(self):
        head=self.repository.read_head()
        before_files={str(p.relative_to(self.repository.layout.root)):p.read_bytes() for p in self.repository.layout.root.rglob('*') if p.is_file() and p.resolve() not in {lock.resolve() for lock in self.repository.lock_paths()}}
        reply=self.client.get('/api/render/geometry');self.assertEqual(reply.status_code,200,reply.text)
        first=reply.json();self.assertEqual(first['source']['kind'],'working-head')
        self.assertEqual(before_files,{str(p.relative_to(self.repository.layout.root)):p.read_bytes() for p in self.repository.layout.root.rglob('*') if p.is_file() and p.resolve() not in {lock.resolve() for lock in self.repository.lock_paths()}},'derived read writes no selection or project data')
        scene=self.client.get('/api/render/scene').json()['scene']
        saved=self.client.put('/api/render/scene',json={'scene':scene});self.assertEqual(saved.status_code,200,saved.text)
        drawn=self.client.post('/api/render/drawings',json={'geometryRevision':first['source']['geometryRevision']});self.assertEqual(drawn.status_code,200,drawn.text)
        old=self.client.get('/api/render/drawings').json()
        started,job=self.run_candidate('set height to 1.1',elementId='portico-base')
        self.assertEqual(job['status'],'succeeded',job)
        pending=self.client.get('/api/render/geometry').json()
        self.assertEqual(pending['source']['modelSource'],first['source']['modelSource'],'uncontinued candidate never takes over')
        self.assertEqual(pending['source']['geometryRevision'],first['source']['geometryRevision'])
        self.assertEqual(pending['meshes'],first['meshes'])
        adopt(self.client,started['candidateId'])
        current=self.client.get('/api/render/geometry').json()
        self.assertEqual(current['source']['runId'],started['candidateId'])
        self.assertNotEqual(current['source']['geometryRevision'],first['source']['geometryRevision'])
        self.assertNotEqual(current['metrics']['boundsMetersZUp'],first['metrics']['boundsMetersZUp'])
        self.assertEqual(self.client.get('/api/render/scene').json()['status'],'stale')
        self.assertTrue(all(d['status']=='outdated' for d in self.client.get('/api/render/drawings').json()))
        stale=self.client.post('/api/render/drawings',json={'geometryRevision':first['source']['geometryRevision']});self.assertEqual(stale.status_code,409)
        update=self.client.post('/api/render/drawings',json={'geometryRevision':current['source']['geometryRevision']});self.assertEqual(update.status_code,200,update.text)
        with TestClient(create_app(self.settings)) as cold:
            self.assertEqual(cold.get('/api/render/geometry').json(),current)
            rows=cold.get('/api/render/drawings').json()
            self.assertEqual(sum(d['status']=='current' for d in rows),4)
            self.assertEqual(sum(d['status']=='outdated' for d in rows),4)
            front=next(d for d in rows if d['status']=='current' and d['recipe']['view']=='front')
            measured=front['recipe']['dimensions'][1]['valueMeters']
            self.assertAlmostEqual(measured,1.4,places=6)
            self.assertEqual(measured,current['metrics']['boundsMetersZUp'][1][2]-current['metrics']['boundsMetersZUp'][0][2])
            self.assertEqual(cold.get('/api/render/scene').json()['scene'],scene,'old appearance retained for explicit review')
        self.assertEqual(self.repository.read_head(),head,'no canonical issue/HEAD write')
        if os.environ.get('WORKING_GEOMETRY_EVIDENCE'):
            Path(os.environ['WORKING_GEOMETRY_EVIDENCE']).write_text(json.dumps({'before':first['source'],'after':current['source'],'boundsBefore':first['metrics']['boundsMetersZUp'],'boundsAfter':current['metrics']['boundsMetersZUp'],'oldDrawings':old,'reopenedDrawings':rows},indent=2),encoding='utf-8')

    def test_explicit_penguin_import_stays_pinned_when_native_head_moves(self):
        native=self.client.get('/api/render/geometry');self.assertEqual(native.status_code,200,native.text)
        data=Path(os.environ['PENGUIN_TEST_GLB']).read_bytes() if os.environ.get('PENGUIN_TEST_GLB') else source()
        response=self.client.post('/api/exports',json={'targetFormat':'3dm','upload':{'fileName':'sample.glb','contentBase64':base64.b64encode(data).decode()}})
        self.assertEqual(response.status_code,202,response.text)
        for _ in range(1000):
            converted=self.client.get(response.json()['statusPath']).json()
            if converted['status'] not in ('queued','running'):break
            time.sleep(.02)
        self.assertEqual(converted['status'],'succeeded',converted)
        selected=self.client.put('/api/render/geometry',json={'exportId':converted['exportId'],'expectedRevision':native.json()['source']['geometryRevision']})
        self.assertEqual(selected.status_code,200,selected.text)
        imported=self.client.get('/api/render/geometry').json()
        started,job=self.run_candidate('set height to 1.1',elementId='portico-base');self.assertEqual(job['status'],'succeeded',job)
        adopt(self.client,started['candidateId'])
        with TestClient(create_app(self.settings)) as cold:self.assertEqual(cold.get('/api/render/geometry').json(),imported)
