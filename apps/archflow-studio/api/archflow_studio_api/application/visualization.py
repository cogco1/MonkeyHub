"""P036-owned visualization revisions. No design-state or geometry writer."""
import base64
import math
from dataclasses import asdict
from io import BytesIO
from archflow.visualization import ProjectVisualizationState, default_visualization
from archflow.adapters.three_dm_inspector import inspect_three_dm_contents
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_VISUALIZATION, STUDIO_RENDER_JOB
from ..transport.errors import StudioError
from ..transport.artifacts import model_source_from
from .artifacts import artifact_bytes, require_model_source

RUN = 'studio-visualization'


def read_visualization(binding):
    if RUN not in binding.run_ids():
        return {'revision':0, 'state':None, 'source':None}
    rows=[binding.repository.load_json(ref) for ref in binding.record_refs(RUN)
          if ref.record_kind == STUDIO_VISUALIZATION]
    return max(rows, key=lambda r:r['revision']) if rows else {'revision':0,'state':None,'source':None}


def _check(binding, expected):
    current=read_visualization(binding)
    if current['revision'] != expected:
        raise StudioError(409,'VISUALIZATION_CONFLICT','Visualization changed in another window. Reload before saving.')
    return current


def _write(binding, current, state, source):
    row={'schema':'ProjectVisualizationState@1','revision':current['revision']+1,'state':state,'source':source}
    binding.repository.put_json(run=binding.load_run(RUN),
        destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=RUN),
        record_kind=STUDIO_VISUALIZATION,payload=row)
    return row


def save_visualization(binding, payload):
    try:
        value=ProjectVisualizationState.from_dict(payload.state).to_dict()
    except (ValueError,TypeError,KeyError) as exc:
        raise StudioError(422,'VISUALIZATION_INVALID',str(exc)) from exc
    with binding.repository.working_draft_guard():
        current=_check(binding,payload.expected_revision)
        if not current['source']: raise StudioError(409,'MODEL_REQUIRED','Open a model first.')
        if current['state']==value: return current
        return _write(binding,current,value,current['source'])


def source_bytes(binding, source=None):
    source=source or read_visualization(binding)['source']
    if not source: raise StudioError(404,'MODEL_REQUIRED','No visualization model is saved.')
    return binding.repository.read_transfer_file(source['artifact']['relative_path'],source['artifact']['sha256'])


def set_source(binding,payload):
    choices=[payload.job_id is not None,payload.model_source is not None,payload.content_base64 is not None]
    if sum(choices)!=1: raise StudioError(422,'MODEL_REQUIRED','Choose one exact model source.')
    model_source=None; old_camera=None
    if payload.job_id:
        if payload.job_id not in binding.run_ids(): raise StudioError(404,'SOURCE_NOT_FOUND','Unknown render source.')
        rows=[binding.repository.load_json(ref) for ref in binding.record_refs(payload.job_id) if ref.record_kind==STUDIO_RENDER_JOB]
        if not rows: raise StudioError(404,'SOURCE_NOT_FOUND','No retained render input.')
        row=max(rows,key=lambda r:r['sequence']); ref=row['source']
        data=binding.repository.read_transfer_file(ref['relative_path'],ref['sha256'])
        name=row['fileName']; model_source=row.get('modelSource')
        old_camera=row.get('presentation',{}).get('camera')
    elif payload.model_source:
        value=model_source_from(payload.model_source); require_model_source(binding,value)
        artifact,data=artifact_bytes(binding,value.asset_sha256,run_id=value.run_id)
        name=artifact.file_name; model_source=value.to_dict()
    else:
        name=payload.file_name
        if not name or not name.lower().endswith('.3dm') or any(c in name for c in '/\\\r\n\0'):
            raise StudioError(422,'MODEL_NAME_INVALID','Choose a .3dm model filename.')
        try: data=base64.b64decode(payload.content_base64,validate=True)
        except ValueError as exc: raise StudioError(422,'MODEL_INVALID','Invalid model encoding.') from exc
    if not data or len(data)>32*1024*1024: raise StudioError(413,'MODEL_SIZE','Model must be at most 32 MiB.')
    try: inspect_three_dm_contents(data)
    except Exception as exc: raise StudioError(422,'MODEL_INVALID','The 3DM model could not be read.') from exc
    state=default_visualization()
    c=payload.camera.model_dump() if payload.camera else old_camera
    if c:
        bounds=c.get('orthographic_bounds')
        state['camera']={k:c[k] for k in ('position','target','up','projection','near','far')}
        state['camera'].update(fov=c.get('vertical_fov') or 38,orthoHeight=bounds[2]-bounds[3] if bounds else 2)
        if bounds:
            # Recenter asymmetric orthographic bounds without changing the visible framing.
            def cross(a,b): return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
            def unit(v):
                length=math.sqrt(sum(x*x for x in v))
                if length<1e-10: raise StudioError(422,'CAMERA_INVALID','Camera frame is degenerate.')
                return [x/length for x in v]
            direction=unit([b-a for a,b in zip(c['position'],c['target'])])
            right=unit(cross(direction,c['up'])); up=cross(right,direction)
            shift=[right[i]*(bounds[0]+bounds[1])/2+up[i]*(bounds[2]+bounds[3])/2 for i in range(3)]
            for key in ('position','target'): state['camera'][key]=[a+b for a,b in zip(c[key],shift)]
        aspect=c.get('aspect',1)
        state['renderSettings'].update(width=max(64,round(1024*min(aspect,1))),height=max(64,round(1024/max(aspect,1))))
        try: state=ProjectVisualizationState.from_dict(state).to_dict()
        except (ValueError,TypeError,KeyError) as exc: raise StudioError(422,'CAMERA_INVALID',str(exc)) from exc
    with binding.repository.working_draft_guard():
        current=_check(binding,payload.expected_revision)
        run=binding.load_run(RUN) if RUN in binding.run_ids() else binding.repository.create_run(RUN)
        artifact=binding.repository.ingest(run=run,destination=PersistenceDestination(PersistenceArea.OBJECT),
            artifact_id='visualization-source',media_type='model/vnd.rhino.3dm',source=BytesIO(data))
        source={'artifact':asdict(artifact),'fileName':name,'modelSource':model_source}
        if current['source'] and current['source']['artifact']['sha256']==artifact.sha256:
            retained=current['state']
            if payload.camera:
                retained={**retained,'camera':state['camera'],'renderSettings':{**retained['renderSettings'],'width':state['renderSettings']['width'],'height':state['renderSettings']['height']}}
            state=retained  # Preserve look development; an explicit viewport transfer updates framing.
        return _write(binding,current,state,source)
