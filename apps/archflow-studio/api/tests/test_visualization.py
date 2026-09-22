import base64
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import make_project, PROJECT_ID

class VisualizationTests(unittest.TestCase):
    def test_source_state_conflict_restart_and_geometry_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); repo,_=make_project(root); before=repo.read_head()
            settings=StudioSettings(project_dir=root/PROJECT_ID,cad_export='off')
            data=(Path(__file__).resolve().parents[4]/'tests/fixtures/render-mesh.3dm').read_bytes()
            with TestClient(create_app(settings)) as c:
                self.assertEqual(c.get('/api/visualization').json()['revision'],0)
                r=c.post('/api/visualization/source',json={'expectedRevision':0,'fileName':'mesh.3dm','contentBase64':base64.b64encode(data).decode()})
                self.assertEqual(r.status_code,200,r.text); state=r.json()['state']
                self.assertEqual(c.get('/api/visualization/source').content,data)
                state['materials'][0]['baseColor']='#cc7733'; state['lights'][0]['intensity']=4
                save={'expectedRevision':1,'state':state}
                r=c.put('/api/visualization',json=save); self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(c.put('/api/visualization',json=save).status_code,409)
                self.assertEqual(repo.read_head(),before)
            with TestClient(create_app(settings)) as c:
                saved=c.get('/api/visualization').json()
                self.assertEqual(saved['revision'],2); self.assertEqual(saved['state'],state)
                self.assertEqual(c.get('/api/visualization/source').content,data)

    def test_explicit_same_model_handoff_preserves_look_but_updates_framing(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);repo,_=make_project(root)
            data=(Path(__file__).resolve().parents[4]/'tests/fixtures/render-mesh.3dm').read_bytes()
            body={'expectedRevision':0,'fileName':'mesh.3dm','contentBase64':base64.b64encode(data).decode()}
            with TestClient(create_app(StudioSettings(project_dir=root/PROJECT_ID,cad_export='off'))) as c:
                state=c.post('/api/visualization/source',json=body).json()['state']
                state['materials'][0]['baseColor']='#123456'
                c.put('/api/visualization',json={'expectedRevision':1,'state':state})
                body.update(expectedRevision=2,camera={'position':[0,-3,0],'target':[0,0,0],'up':[0,0,1],
                    'projection':'orthographic','near':.01,'far':100,'aspect':2/3,'orthographicBounds':[1,5,4,-2]})
                response=c.post('/api/visualization/source',json=body)
                self.assertEqual(response.status_code,200,response.text);state=response.json()['state']
                self.assertEqual(state['materials'][0]['baseColor'],'#123456')
                self.assertEqual(state['camera']['position'],[3,-3,1]);self.assertEqual(state['camera']['target'],[3,0,1])
                self.assertEqual(state['camera']['orthoHeight'],6)
                self.assertEqual(state['renderSettings']['width'],683);self.assertEqual(state['renderSettings']['height'],1024)
