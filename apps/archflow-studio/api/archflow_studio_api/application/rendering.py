"""Project-runtime render queue. P036 owns every source, transition and result."""
import base64
from datetime import datetime, timezone
from io import BytesIO
import re
import threading
from uuid import uuid4

from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB
from ..transport.artifacts import document_dto
from ..transport.errors import StudioError
from ..transport.rendering import RenderJobDto
from .artifacts import save_document, list_documents


def _now():
    return datetime.now(timezone.utc).isoformat()


class RenderJobRecords:
    def __init__(self):
        self.instance = uuid4().hex
        self.lock = threading.RLock()
        self.stopping = False

    def shutdown(self):
        with self.lock:
            self.stopping = True

    def _record(self, binding, row):
        binding.repository.put_json(run=binding.load_run(row["jobId"]),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=row["jobId"]),
            record_kind=STUDIO_RENDER_JOB, payload=row)

    def _load(self, binding, job_id):
        if not re.fullmatch(r"render-[0-9a-f]{32}", job_id) or job_id not in binding.run_ids():
            raise StudioError(404, "RENDER_NOT_FOUND", "The render task is not in this project.")
        rows = [binding.repository.load_json(ref) for ref in binding.record_refs(job_id)
                if ref.record_kind == STUDIO_RENDER_JOB]
        if not rows:
            raise StudioError(404, "RENDER_NOT_FOUND", "The render task has no retained request.")
        return max(rows, key=lambda row: row["sequence"])

    def get(self, binding, job_id):
        with self.lock:
            row = self._load(binding, job_id)
            status, error = row["status"], row.get("error")
            if status in ("queued", "running") and row["instance"] != self.instance:
                status, error = "interrupted", "The runtime stopped before this render completed. Start a new render to retry."
            document = None
            if status == "succeeded":
                document = next((item for item in list_documents(binding, job_id)
                                 if item.asset_sha256 == row["documentSha256"]), None)
                if document is None:
                    status, error = "failed", "The retained render result is unavailable."
            return RenderJobDto(jobId=job_id, projectId=binding.project_id, status=status,
                sourceSha256=row["source"]["sha256"], fileName=row["fileName"],
                createdAt=row["createdAt"], error=error, document=document_dto(document) if document else None)

    def list(self, binding):
        return [self.get(binding, run_id) for run_id in binding.run_ids()
                if re.fullmatch(r"render-[0-9a-f]{32}", run_id)
                and any(ref.record_kind == STUDIO_RENDER_JOB for ref in binding.record_refs(run_id))]

class NativeRenderJobs(RenderJobRecords):
    """Browser GPU worker; the runtime retains requests and verifies returned PNGs."""
    def __init__(self):
        self.instance=uuid4().hex
        self.lock=threading.RLock()
        self.stopping=False

    def shutdown(self):
        self.stopping=True

    def get(self,binding,job_id):
        import time
        with self.lock:
            row=self._load(binding,job_id)
            if row.get('renderer')!='monkeyhub-three-webgl2-v1':
                return super().get(binding,job_id)
            if row['status']=='running' and (row['instance']!=self.instance or time.time()>row['expiresAt']):
                row=dict(row,status='interrupted',sequence=row['sequence']+1,error='Native renderer disconnected or timed out. Start a new render to retry.')
            result=super().get(binding,job_id)
            changes={'snapshot':row['visualization'],'snapshot_sha256':row['snapshotSha256']}
            if row['status']=='interrupted': changes.update(status='interrupted',error=row['error'])
            return result.model_copy(update=changes)

    def submit_native(self,binding,payload):
        import hashlib,time
        from .visualization import read_visualization,source_bytes
        with self.lock,binding.repository.working_draft_guard():
            if self.stopping: raise StudioError(503,'RENDER_STOPPING','Runtime is stopping.')
            job_id='render-'+payload.request_id.hex
            if job_id in binding.run_ids():
                old=self._load(binding,job_id)
                if old.get('visualizationRevision')!=payload.revision or old.get('renderer')!='monkeyhub-three-webgl2-v1':
                    raise StudioError(409,'RENDER_REQUEST_CONFLICT','Request ID names another snapshot.')
                return self.get(binding,job_id)
            current=read_visualization(binding)
            if current['revision']!=payload.revision or not current['source']:
                raise StudioError(409,'VISUALIZATION_CONFLICT','Save the current visualization before rendering.')
            source_bytes(binding,current['source'])
            binding.repository.create_run(job_id)
            snapshot={'source':current['source'],'state':current['state']}
            digest=hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()
            row={'schema':'StudioRenderJob@1','jobId':job_id,'projectId':binding.project_id,
                'instance':self.instance,'sequence':0,'status':'running','createdAt':_now(),
                'source':current['source']['artifact'],'modelSource':current['source']['modelSource'],
                'fileName':current['source']['fileName'],'visualization':current['state'],
                'visualizationRevision':current['revision'],'snapshotSha256':digest,
                'renderer':'monkeyhub-three-webgl2-v1','expiresAt':time.time()+120}
            self._record(binding,row)
            return self.get(binding,job_id)

    def complete_native(self,binding,job_id,payload):
        import hashlib
        from PIL import Image
        from .artifacts import ModelSource
        try:
            data=base64.b64decode(payload.content_base64,validate=True)
            if len(data)>72*1024*1024: raise ValueError('Image is too large')
            with Image.open(BytesIO(data)) as image:
                if image.format!='PNG': raise ValueError('Expected PNG')
                size=image.size;image.verify()
        except Exception as exc: raise StudioError(422,'RENDER_IMAGE_INVALID','The native result must be a valid PNG.') from exc
        with self.lock,binding.repository.working_draft_guard():
            result=self.get(binding,job_id);row=self._load(binding,job_id)
            if row.get('renderer')!='monkeyhub-three-webgl2-v1' or payload.snapshot_sha256!=row.get('snapshotSha256'):
                raise StudioError(409,'RENDER_SNAPSHOT_MISMATCH','The result names a different render snapshot.')
            if result.status=='succeeded':
                if hashlib.sha256(data).hexdigest()!=row['documentSha256']:
                    raise StudioError(409,'RENDER_RESULT_CONFLICT','A different result already exists.')
                return result
            if result.status!='running': raise StudioError(409,'RENDER_FINISHED','This task no longer accepts results.')
            settings=row['visualization']['renderSettings']
            if size!=(settings['width'],settings['height']):
                raise StudioError(422,'RENDER_IMAGE_SIZE','PNG dimensions do not match the retained snapshot.')
            document=save_document(binding,job_id,row['fileName'][:-4]+'-native.png','image/png',payload.content_base64,
                model_source=ModelSource.from_dict(row['modelSource']) if row.get('modelSource') else None,
                view_recipe={'kind':'render','renderer':row['renderer'],'jobId':job_id,'source':row['source'],
                    'visualization':row['visualization'],'visualizationRevision':row['visualizationRevision'],
                    'snapshotSha256':row['snapshotSha256'],'validation':'PNG format, dimensions and exact request binding; browser GPU pixels'},
                generated_at=_now())
            row.update(sequence=row['sequence']+1,status='succeeded',finishedAt=_now(),documentSha256=document.asset_sha256)
            self._record(binding,row); return self.get(binding,job_id)

    def fail_native(self,binding,job_id,detail):
        with self.lock:
            result=self.get(binding,job_id);row=self._load(binding,job_id)
            if result.status=='running' and row.get('renderer')=='monkeyhub-three-webgl2-v1':
                row.update(sequence=row['sequence']+1,status='failed',error=detail,finishedAt=_now())
                self._record(binding,row)
            return self.get(binding,job_id)
