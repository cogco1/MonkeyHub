"""studio.artifacts: revision-aware mesh drawings using existing document persistence."""
import base64
from datetime import datetime,timezone
from io import BytesIO
from dataclasses import asdict
from archflow.contracts.canonical import canonical_digest
from archflow.project.ports import PersistenceArea,PersistenceDestination
from monkeydiagram.mesh_views import mesh_view
from .geometry_sources import current_geometry,read_geometry
from .artifacts import list_documents,save_document
from ..transport.artifacts import document_dto
from ..transport.errors import StudioError

def drawing_status(binding,recipe):
    current=current_geometry(binding)
    if not current:return 'unavailable','No current geometry is selected.'
    if current['geometryRevision']!=recipe['geometryRevision']:
        return 'outdated','Geometry changed. Regenerate this view and recompute its measurements.'
    return 'current',None

def drawing_list(binding):
    rows=[]
    for doc in list_documents(binding):
        recipe=doc.view_recipe or {}
        if recipe.get('kind')!='mesh-orthographic':continue
        status,reason=drawing_status(binding,recipe)
        rows.append({'document':document_dto(doc).model_dump(mode='json',by_alias=True),'recipe':recipe,'status':status,'reason':reason})
    return sorted(rows,key=lambda r:r['document'].get('generatedAt') or '',reverse=True)

def generate(binding,payload):
    source=current_geometry(binding)
    if not source or source['geometryRevision']!=payload.geometryRevision:
        raise StudioError(409,'DRAWING_GEOMETRY_STALE','Geometry changed before the drawing request.')
    geometry=read_geometry(binding,source);results=[];previous=drawing_list(binding)
    for view in payload.views:
        # A routine regeneration must not silently discard an unresolved reference.
        prior=next((r for r in previous if r['recipe']['view']==view),None)
        features=sorted(set(payload.featureReferences)|{r['id'] for r in (prior or {}).get('recipe',{}).get('featureReferences',[])})
        recipe={'kind':'mesh-orthographic','view':view,'geometry':source,'geometryRevision':source['geometryRevision'],
                'scale':payload.scale,'featureReferences':[{'id':r,'status':'unresolved',
                'reason':'Imported meshes have no stable semantic feature IDs. Select a new reference explicitly.'} for r in features]}
        drawing_revision=canonical_digest(recipe);recipe['drawingRevision']=drawing_revision
        run=binding.repository.create_run('mesh-view-'+drawing_revision[:40])
        svg,png,projection=mesh_view(geometry.meshes,view,payload.scale);recipe.update(projection)
        ref=binding.repository.put_workspace_file(run=run,destination=PersistenceDestination(PersistenceArea.RUN_WORKSPACE,run_id=run.run_id),
             artifact_id='drawing-svg',workspace_relative_path='drawing.svg',media_type='image/svg+xml',source=BytesIO(svg))
        recipe['svg']=asdict(ref)
        document=save_document(binding,run.run_id,view+'.png','image/png',base64.b64encode(png).decode(),
              drawing_id='mesh-'+view,view_recipe=recipe,generated_at=datetime.now(timezone.utc).isoformat())
        results.append(document_dto(document).model_dump(mode='json',by_alias=True))
    return results
