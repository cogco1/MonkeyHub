"""studio.artifacts: explicit geometry selection, referencing existing retained bytes."""
from dataclasses import asdict
import base64,hashlib
from archflow.adapters.model_formats import GLB, ThreeDM
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectArtifactRef
from archflow.project.record_kinds import STUDIO_GEOMETRY_SELECTION
from . import model_exports
from .artifacts import artifact_bytes,register_model_asset
from ..transport.errors import StudioError

RUN = "studio-geometry"

def current_geometry(binding):
    if RUN not in binding.run_ids():
        return None
    rows=[binding.repository.load_json(r) for r in binding.record_refs(RUN,kind=STUDIO_GEOMETRY_SELECTION)]
    return max(rows,key=lambda r:r['sequence']) if rows else None

def geometry_bytes(binding, source):
    if source.get('artifact'):
        ref=ProjectArtifactRef(**source['artifact'])
        if ref.project_id != binding.project_id:
            raise StudioError(409,'GEOMETRY_PROJECT_MISMATCH','The geometry belongs to another project.')
        data=model_exports._object(binding,asdict(ref),role='source')
    else:
        _,data=artifact_bytes(binding,source['assetSha256'],run_id=source['runId'])
    if hashlib.sha256(data).hexdigest()!=source['geometryRevision']:
        raise StudioError(409,'GEOMETRY_CHANGED','The retained geometry failed its content check.')
    return data

def read_geometry(binding, source=None):
    source=source or current_geometry(binding)
    if not source:
        raise StudioError(409,'GEOMETRY_REQUIRED','Select a retained model or import a GLB first.')
    reader={'glb':GLB,'3dm':ThreeDM}[source['format']]()
    return reader.read(geometry_bytes(binding,source))

def select_geometry(binding, *, expected_revision, export_id=None, run_id=None, asset_sha256=None, event_sink=None):
    if bool(export_id)==bool(run_id and asset_sha256):
        raise StudioError(422,'GEOMETRY_SOURCE_REQUIRED','Choose one conversion source or registered model.')
    if export_id:
        export=model_exports.report(binding,export_id)
        if export['status']!='succeeded' or export['sourceFormat'] not in ('glb','3dm'):
            raise StudioError(409,'GEOMETRY_EXPORT_UNAVAILABLE','Choose a validated GLB/3DM conversion.')
        source={'runId':export_id,'artifact':export['sourceArtifact'],'geometryRevision':export['sourceSha256'],
                'format':export['sourceFormat'],'label':export.get('sourceFileName') or export_id,
                'previewExportId':export_id,'kind':'retained-import'}
    else:
        artifact,data=artifact_bytes(binding,asset_sha256,run_id=run_id)
        if artifact.format!='3dm':
            raise StudioError(422,'GEOMETRY_FORMAT_UNSUPPORTED','Select a registered 3DM representation.')
        source={'runId':run_id,'assetSha256':asset_sha256,'geometryRevision':asset_sha256,'format':'3dm',
                'label':artifact.file_name,'kind':'retained-model'}
    read_geometry(binding,source)
    with binding.repository.working_draft_guard():
        current=current_geometry(binding)
        if (current or {}).get('geometryRevision')!=expected_revision:
            raise StudioError(409,'GEOMETRY_REVISION_CONFLICT','Geometry changed. Refresh before replacing it.')
        if current and current['geometryRevision']==source['geometryRevision']:
            return current
        if export_id and export['targetFormat']=='3dm':
            # Register the production conversion as the existing Modeling viewer's
            # derivative. Drawings and renderers still read the retained original.
            _,preview=model_exports.download(binding,export_id)
            artifact=register_model_asset(binding,None,None,'Imported-model.3dm',base64.b64encode(preview).decode(),event_sink=event_sink)
            source['preview']={'runId':artifact.run_id,'assetSha256':artifact.sha256}
        run=binding.repository.create_run(RUN)
        row={'schema':'StudioGeometrySelection@1','projectId':binding.project_id,
             'sequence':(current or {}).get('sequence',0)+1,**source}
        binding.repository.put_json(run=run,destination=PersistenceDestination(PersistenceArea.RUN_RECORD,run_id=RUN),
                                    record_kind=STUDIO_GEOMETRY_SELECTION,payload=row)
        return row

def geometry_payload(binding):
    source=current_geometry(binding)
    scene=read_geometry(binding,source)
    return {'source':source,'metrics':scene.metrics(),'meshes':[asdict(m) for m in scene.meshes]}
