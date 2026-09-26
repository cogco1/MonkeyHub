"""Cycles attempts share Render's history, Runtime execution and P036 artifact owner."""
import base64,json,os,shutil
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
from uuid import UUID
from dataclasses import asdict
from archflow.project.ports import PersistenceArea,PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB
from archflow.adapters.render_projection import scene_plan
from archflow.adapters.blender_projection import render_scene_projection
from .render_scene import latest_scene,image_bytes
from .geometry_sources import current_geometry,read_geometry
from .artifacts import save_document,list_documents,document_bytes
from ..transport.artifacts import document_dto
from ..transport.rendering import RenderJobDto
from ..transport.errors import StudioError

def executable():
    value=os.environ.get('ARCHFLOW_BLENDER_EXECUTABLE') or shutil.which('blender')
    return value if value and Path(value).is_file() else None

def record(binding,row):
    binding.repository.put_json(run=binding.load_run(row['jobId']),
        destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=row['jobId']),record_kind=STUDIO_RENDER_JOB,payload=row)

def job_dto(binding,row,jobs=None):
    state=latest_scene(binding);geometry=current_geometry(binding)
    fresh=bool(state and geometry and row['sceneRevision']==state['sceneRevision'] and row['geometryRevision']==geometry['geometryRevision'])
    status=row['status'];error=row.get('error')
    if status in ('queued','running') and jobs is not None:
        try:jobs.for_candidate(row['jobId'])
        except StudioError:status='unknown';error='The runtime restarted. This render is not automatically replayed.'
    document=next(iter(list_documents(binding,row['jobId'])),None)
    available=False
    if document:
        try:document_bytes(binding,document.run_id,document.asset_sha256,document.revision_ref);available=True
        except (StudioError,OSError,ValueError):pass
    return RenderJobDto(projectId=binding.project_id,jobId=row['jobId'],requestId=UUID(row['jobId'][7:]),
        status=status,execution='host',providerId='cycles',model='Blender/Cycles',request=None,
        createdAt=row['createdAt'],finishedAt=row.get('finishedAt'),error=error,
        sourceState='current' if fresh else 'outdated',sourceStateReason=None if fresh else 'Geometry or scene has changed since this render.',
        document=document_dto(document) if document else None,resultAvailable=available,
        geometryRevision=row['geometryRevision'],sceneRevision=row['sceneRevision'])

def submit(binding,jobs,payload):
    host=executable()
    if not host:raise StudioError(409,'CYCLES_UNAVAILABLE','Configure ARCHFLOW_BLENDER_EXECUTABLE on the project runtime host.')
    job_id='render-'+payload.requestId.hex
    with binding.repository.working_draft_guard():
        if job_id in binding.run_ids():
            rows=[binding.repository.load_json(r) for r in binding.record_refs(job_id,kind=STUDIO_RENDER_JOB)]
            old=max(rows,key=lambda r:r['sequence'])
            if old.get('sceneRevision')!=payload.sceneRevision or old.get('geometryRevision')!=payload.geometryRevision:
                raise StudioError(409,'RENDER_REQUEST_CONFLICT','This request ID already names another scene.')
            return job_dto(binding,old,jobs)
        state=latest_scene(binding);geometry=current_geometry(binding)
        if not state or not geometry or state['sceneRevision']!=payload.sceneRevision or geometry['geometryRevision']!=payload.geometryRevision or state['scene']['geometryRevision']!=payload.geometryRevision:
            raise StudioError(409,'RENDER_SCENE_STALE','Save the current scene before rendering it.')
        meshes=read_geometry(binding,state['geometry']);plan=scene_plan(meshes,state);images={}
        value=state['scene']
        for ref in [value['environment']['background'],value['environment']['environmentMap'],*[m[k] for m in value['materials'] for k in ('texture','normalMap')]]:
            if ref:images[ref['assetSha256']]=image_bytes(binding,ref)[0]
        run=binding.repository.create_run(job_id)
        row={'schema':'StudioRenderJob@3','jobId':job_id,'projectId':binding.project_id,'sequence':0,'status':'queued',
             'createdAt':datetime.now(timezone.utc).isoformat(),'geometryRevision':payload.geometryRevision,
             'sceneRevision':payload.sceneRevision,'scene':state,'error':None}
        record(binding,row)
    def retain_file(path):
        return asdict(binding.repository.put_workspace_file(run=run,destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE,run_id=run.run_id),
              artifact_id=path.name.replace('.','-'),workspace_relative_path='cycles/'+path.name,
              media_type='application/octet-stream',source=BytesIO(path.read_bytes())))
    def work():
        record(binding,row|{'sequence':1,'status':'running'})
        with TemporaryDirectory(prefix='monkeyhub-cycles-') as temporary:
            workspace=Path(temporary);artifacts=[]
            try:
                readback=render_scene_projection(plan,images,workspace,host)
                for name in ('scene.blend','scene-plan.json','readback.json','blender.log'):artifacts.append(retain_file(workspace/name))
                png=(workspace/'render.png').read_bytes()
                document=save_document(binding,job_id,'Cycles.png','image/png',base64.b64encode(png).decode(),
                    view_recipe={'kind':'cycles-render','geometryRevision':payload.geometryRevision,'sceneRevision':payload.sceneRevision,
                                 'geometry':state['geometry'],'jobId':job_id},generated_at=datetime.now(timezone.utc).isoformat())
                record(binding,row|{'sequence':2,'status':'succeeded','finishedAt':datetime.now(timezone.utc).isoformat(),
                       'documentSha256':document.asset_sha256,'artifacts':artifacts,'readback':readback})
            except Exception:
                if (workspace/'blender.log').exists():artifacts.append(retain_file(workspace/'blender.log'))
                record(binding,row|{'sequence':2,'status':'failed','finishedAt':datetime.now(timezone.utc).isoformat(),
                    'error':'Cycles projection failed. Inspect the retained job log.','artifacts':artifacts})
                raise
    try:jobs.submit(candidate_id=job_id,proposal_id=job_id,kind='render',work=work,exclusive=True)
    except Exception:
        record(binding,row|{'sequence':2,'status':'failed','error':'Runtime did not accept the render.'});raise
    return job_dto(binding,row,jobs)
