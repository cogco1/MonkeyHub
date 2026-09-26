"""studio.render scene revisions in the existing P036 repository, no geometry writes."""
from archflow.contracts.canonical import canonical_digest
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_SCENE
from .geometry_sources import current_geometry, read_geometry
from .artifacts import document_bytes
from ..transport.errors import StudioError
from ..transport.render_scene import Scene

RUN='studio-render-scene'

def latest_scene(binding):
    if RUN not in binding.run_ids():return None
    rows=[binding.repository.load_json(r) for r in binding.record_refs(RUN,kind=STUDIO_RENDER_SCENE)]
    return max(rows,key=lambda r:r['sequence']) if rows else None

def default_scene(binding):
    geometry=current_geometry(binding);mesh=read_geometry(binding,geometry)
    low,high=mesh.metrics()['boundsMetersZUp'];target=[(a+b)/2 for a,b in zip(low,high)]
    size=max(b-a for a,b in zip(low,high)) or 1
    value={'geometryRevision':geometry['geometryRevision'],'materials':[{'id':'body','name':'Neutral','baseColor':'#b9bec8'}],
        'assignments':{str(i):'body' for i in range(len(mesh.meshes))},
        'lights':[{'id':'key','type':'area','position':[target[0]+size*1.5,target[1]-size*2,target[2]+size*2],
                   'target':target,'intensity':120,'color':'#fff0dd','size':size*1.5},
                  {'id':'fill','type':'area','position':[target[0]-size*2,target[1]-size,target[2]+size],
                   'target':target,'intensity':55,'color':'#d5e8ff','size':size*2}],
        'camera':{'position':[target[0]+size*2.5,target[1]-size*3,target[2]+size*.6],'target':target,'orthoScale':size*1.3}}
    return Scene.model_validate(value).model_dump(mode='json')

def get_scene(binding):
    row=latest_scene(binding);geometry=current_geometry(binding)
    if not geometry:return {'scene':None,'sceneRevision':None,'geometry':None,'status':'unavailable'}
    if row is None:return {'scene':default_scene(binding),'sceneRevision':None,'geometry':geometry,'status':'unsaved'}
    return row|{'status':'current' if row['scene']['geometryRevision']==geometry['geometryRevision'] else 'stale',
               'currentGeometry':geometry}

def image_bytes(binding,ref):
    document,data=document_bytes(binding,ref['runId'],ref['assetSha256'],ref.get('revisionRef'))
    if document.mime_type not in ('image/png','image/jpeg'):
        raise StudioError(422,'SCENE_IMAGE_UNSUPPORTED','Choose a registered PNG/JPEG image.')
    return data,document.mime_type

def save_scene(binding,payload):
    value=payload.scene.model_dump(mode='json')
    with binding.repository.working_draft_guard():
        geometry=current_geometry(binding);old=latest_scene(binding)
        if (old or {}).get('sceneRevision')!=payload.expectedRevision:
            raise StudioError(409,'SCENE_REVISION_CONFLICT','Scene changed. Reload before saving your edits.')
        if not geometry or geometry['geometryRevision']!=value['geometryRevision']:
            raise StudioError(409,'SCENE_GEOMETRY_STALE','Geometry changed. Review assignments and rebind this scene.')
        mesh=read_geometry(binding,geometry)
        if set(value['assignments'])!={str(i) for i in range(len(mesh.meshes))} or any(r['mesh']>=len(mesh.meshes) for r in value['regions']):
            raise StudioError(422,'SCENE_ASSIGNMENT_INVALID','Assign every source mesh; unknown mesh references are refused.')
        assigned=[(int(i),material) for i,material in value['assignments'].items()]
        assigned.extend((r['mesh'],r['materialId']) for r in value['regions'])
        if any((m['texture'] or m['normalMap']) and mesh.meshes[i].texcoords is None
               for i,material in assigned for m in value['materials'] if m['id']==material):
            raise StudioError(422,'SCENE_UV_REQUIRED','This mesh has no UV coordinates. Use material regions or add UV in an explicit asset revision.')
        for ref in [value['environment']['background'],value['environment']['environmentMap'],
                    *[m[k] for m in value['materials'] for k in ('texture','normalMap')]]:
            if ref:image_bytes(binding,ref)
        digest=canonical_digest(value)
        if old and digest==old['sceneRevision']:return get_scene(binding)
        run=binding.repository.create_run(RUN)
        row={'schema':'StudioRenderScene@1','projectId':binding.project_id,'sceneRevision':digest,
             'sequence':(old or {}).get('sequence',0)+1,'geometry':geometry,'scene':value}
        binding.repository.put_json(run=run,destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=RUN),
                                    record_kind=STUDIO_RENDER_SCENE,payload=row)
        return row|{'status':'current'}
