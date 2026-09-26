"""Actual conversion, P036 revisions, drawing invalidation and cold Runtime reads."""
import base64,os,tempfile,time,unittest
from uuid import uuid4
from pathlib import Path
from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow.adapters.model_formats import GLB,Mesh,Scene
from .support import make_project,PROJECT_ID

def source(scale=1):
    return GLB().write(Scene([Mesh('Pyramid',[(0,0,0),(1,0,0),(0,1,0),(0,0,scale)],[(0,2,1),(0,1,3),(1,2,3),(2,0,3)])],'Meters',[]))

class RenderSceneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repository,_=make_project(Path(self.temp.name));self.head=self.repository.read_head()
        self.settings=StudioSettings(project_dir=Path(self.temp.name)/PROJECT_ID,cad_export='off')
        self.app=create_app(self.settings);self.client=TestClient(self.app)
        self.addCleanup(self.client.close);self.addCleanup(self.app.state.jobs.shutdown);self.addCleanup(self.app.state.render_jobs.shutdown)

    def select(self,scale=1,expected=None):
        reply=self.client.post('/api/exports',json={'targetFormat':'3dm','upload':{'fileName':'penguin.glb','contentBase64':base64.b64encode(source(scale)).decode()}})
        self.assertEqual(reply.status_code,202,reply.text)
        for _ in range(500):
            result=self.client.get(reply.json()['statusPath']).json()
            if result['status'] not in ('queued','running'):break
            time.sleep(.01)
        self.assertEqual(result['status'],'succeeded',result)
        chosen=self.client.put('/api/render/geometry',json={'exportId':result['exportId'],'expectedRevision':expected})
        self.assertEqual(chosen.status_code,200,chosen.text)
        snapshot=self.client.get('/api/runtime').json()
        self.assertNotIn(result['exportId'],[r['candidateId'] for r in snapshot['candidates']])
        return chosen.json()

    def test_one_scene_revision_conflict_and_cold_persistence(self):
        selected=self.select();default=self.client.get('/api/render/scene').json()
        self.assertEqual(default['scene']['geometryRevision'],selected['geometryRevision'])
        saved=self.client.put('/api/render/scene',json={'expectedRevision':None,'scene':default['scene']})
        self.assertEqual(saved.status_code,200,saved.text)
        old=saved.json();scene=old['scene'];scene['materials'][0]['baseColor']='#122333'
        new=self.client.put('/api/render/scene',json={'expectedRevision':old['sceneRevision'],'scene':scene})
        self.assertEqual(new.status_code,200,new.text);self.assertNotEqual(old['sceneRevision'],new.json()['sceneRevision'])
        conflict=self.client.put('/api/render/scene',json={'expectedRevision':old['sceneRevision'],'scene':scene})
        self.assertEqual(conflict.status_code,409)
        with TestClient(create_app(self.settings)) as cold:
            state=cold.get('/api/render/scene').json()
            self.assertEqual(state['sceneRevision'],new.json()['sceneRevision'])
            self.assertEqual(state['scene']['materials'][0]['baseColor'],'#122333')
            self.assertEqual(cold.get('/api/render/geometry').json()['meshes'][0]['triangles'],[[0,2,1],[0,1,3],[1,2,3],[2,0,3]])
        self.assertEqual(self.repository.read_head(),self.head)

    def test_geometry_change_marks_drawings_stale_and_recomputes_extent(self):
        selected=self.select()
        result=self.client.post('/api/render/drawings',json={'geometryRevision':selected['geometryRevision'],'featureReferences':['triangle-17']})
        self.assertEqual(result.status_code,200,result.text)
        original=self.client.get('/api/render/drawings').json();self.assertEqual(len(original),4)
        self.assertTrue(all(r['status']=='current' for r in original))
        self.assertTrue(all(r['recipe']['featureReferences'][0]['status']=='unresolved' for r in original))
        updated=self.select(1.25,selected['geometryRevision'])
        with TestClient(create_app(self.settings)) as cold:
            self.assertTrue(all(r['status']=='outdated' for r in cold.get('/api/render/drawings').json()))
            result=cold.post('/api/render/drawings',json={'geometryRevision':updated['geometryRevision']})
            self.assertEqual(result.status_code,200,result.text)
        with TestClient(create_app(self.settings)) as cold:
            rows=cold.get('/api/render/drawings').json()
            self.assertEqual(len([r for r in rows if r['status']=='current']),4)
            front=next(r for r in rows if r['status']=='current' and r['recipe']['view']=='front')
            self.assertEqual(front['recipe']['dimensions'][1]['valueMeters'],1.25)
            self.assertEqual(front['recipe']['featureReferences'][0]['id'],'triangle-17')
            self.assertEqual(front['recipe']['featureReferences'][0]['status'],'unresolved')
            self.assertTrue(any(r['status']=='outdated' for r in rows))

    def test_old_regions_and_invalid_camera_cannot_be_silently_rebound(self):
        first=self.select();scene=self.client.get('/api/render/scene').json()['scene']
        scene['regions']=[{'id':'belly','name':'Belly','materialId':'body','mesh':0,'shape':'box','center':[0,0,0],
                           'radius':[1,1,1],'geometryRevision':first['geometryRevision']}]
        self.assertEqual(self.client.put('/api/render/scene',json={'scene':scene}).status_code,200)
        newer=self.select(2,first['geometryRevision'])
        self.assertEqual(self.client.get('/api/render/scene').json()['status'],'stale')
        scene['geometryRevision']=newer['geometryRevision']
        refused=self.client.put('/api/render/scene',json={'scene':scene})
        self.assertEqual(refused.status_code,422)

    @unittest.skipUnless(os.environ.get('ARCHFLOW_BLENDER_EXECUTABLE'),'Requires an installed Blender for real host acceptance')
    def test_real_cycles_pins_scene_and_keeps_old_result_outdated_after_edit(self):
        selected=self.select();scene=self.client.get('/api/render/scene').json()['scene']
        scene['settings'].update(width=96,height=128,samples=4)
        saved=self.client.put('/api/render/scene',json={'scene':scene}).json()
        request={'requestId':str(uuid4()),'sceneRevision':saved['sceneRevision'],'geometryRevision':selected['geometryRevision']}
        response=self.client.post('/api/render/cycles',json=request)
        self.assertEqual(response.status_code,202,response.text);job=response.json()
        scene['materials'][0]['roughness']=.23
        edited=self.client.put('/api/render/scene',json={'expectedRevision':saved['sceneRevision'],'scene':scene}).json()
        for _ in range(600):
            job=self.client.get('/api/render/jobs/'+job['jobId']).json()
            if job['status'] not in ('queued','running'):break
            time.sleep(.1)
        self.assertEqual(job['status'],'succeeded',job)
        self.assertTrue(job['resultAvailable']);self.assertEqual(job['sourceState'],'outdated')
        snapshot=self.client.get('/api/runtime').json()
        self.assertNotIn(job['jobId'],[r['candidateId'] for r in snapshot['candidates']])
        self.assertEqual(job['sceneRevision'],saved['sceneRevision'])
        self.assertEqual(job['geometryRevision'],selected['geometryRevision'])
        document=job['document']
        self.assertEqual(document['viewRecipe']['sceneRevision'],saved['sceneRevision'])
        self.assertEqual(self.client.get('/api/render/scene').json()['sceneRevision'],edited['sceneRevision'])
        self.assertEqual(self.client.post('/api/render/cycles',json=request).json()['jobId'],job['jobId'])
        request['sceneRevision']=edited['sceneRevision']
        self.assertEqual(self.client.post('/api/render/cycles',json=request).status_code,409)
        with TestClient(create_app(self.settings)) as cold:
            self.assertEqual(cold.get('/api/render/jobs/'+job['jobId']).json()['sourceState'],'outdated')
            self.assertEqual(cold.get('/api/render/scene').json()['sceneRevision'],edited['sceneRevision'])
        self.assertEqual(self.repository.read_head(),self.head)
