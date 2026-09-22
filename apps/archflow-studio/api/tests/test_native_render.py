import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image
from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import make_project, PROJECT_ID

class NativeRenderTests(unittest.TestCase):
    def test_native_snapshot_result_restart_and_no_external_process(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); repo,_=make_project(root); before=repo.read_head()
            settings=StudioSettings(project_dir=root/PROJECT_ID,cad_export='off')
            data=(Path(__file__).resolve().parents[4]/'tests/fixtures/render-mesh.3dm').read_bytes()
            with patch('subprocess.Popen',side_effect=AssertionError('No external renderer')), patch('shutil.which',return_value=None):
                with TestClient(create_app(settings)) as c:
                    r=c.post('/api/visualization/source',json={'expectedRevision':0,'fileName':'mesh.3dm','contentBase64':base64.b64encode(data).decode()})
                    self.assertEqual(r.status_code,200,r.text)
                    state=r.json()['state'];state['renderSettings'].update(width=128,height=64)
                    self.assertEqual(c.put('/api/visualization',json={'expectedRevision':1,'state':state}).status_code,200)
                    request={'requestId':str(uuid4()),'revision':2}
                    r=c.post('/api/render/native-jobs',json=request);self.assertEqual(r.status_code,202,r.text)
                    job=r.json();job_id=job['jobId'];url='/api/render/native-jobs/'+job_id+'/complete'
                    self.assertEqual(c.post('/api/render/native-jobs',json=request).json()['jobId'],job_id)
                    self.assertEqual(c.get('/api/render/jobs/'+job_id).json()['status'],'running')
                    state['materials'][0]['baseColor']='#ff0000'
                    self.assertEqual(c.put('/api/visualization',json={'expectedRevision':2,'state':state}).status_code,200)
                    self.assertNotEqual(c.get('/api/render/jobs/'+job_id).json()['snapshot']['materials'],state['materials'])
                    def png(size,color):
                        out=BytesIO();Image.new('RGB',size,color).save(out,format='PNG');return base64.b64encode(out.getvalue()).decode()
                    result={'snapshotSha256':job['snapshotSha256'],'contentBase64':png((64,64),'blue')}
                    self.assertEqual(c.post(url,json=result).status_code,422)
                    result['contentBase64']=png((128,64),'blue')
                    self.assertEqual(c.post(url,json=dict(result,snapshotSha256='0'*64)).status_code,409)
                    done=c.post(url,json=result);self.assertEqual(done.status_code,200,done.text)
                    self.assertEqual(done.json()['status'],'succeeded')
                    self.assertEqual(c.post(url,json=result).status_code,200)
                    self.assertEqual(c.post(url,json=dict(result,contentBase64=png((128,64),'red'))).status_code,409)
                    pending=c.post('/api/render/native-jobs',json={'requestId':str(uuid4()),'revision':3}).json()['jobId']
                with TestClient(create_app(settings)) as c:
                    self.assertEqual(c.get('/api/render/jobs/'+job_id).json()['status'],'succeeded')
                    self.assertEqual(c.get('/api/render/jobs/'+pending).json()['status'],'interrupted')
                    self.assertEqual(c.get('/api/visualization/source').content,data)
                self.assertEqual(repo.read_head(),before)
