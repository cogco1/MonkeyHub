"""Project-runtime render queue. P036 owns every source, transition and result."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dataclasses import asdict
from io import BytesIO
import os
import re
import shutil
import threading
from uuid import uuid4

from archflow.adapters.blender_projection import (
    BlenderCamera, BlenderPresentation, execute_mesh_projection, mesh_projection_plan,
)
from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB, BLENDER_PROJECTION
from ..transport.artifacts import model_source_from, document_dto
from ..transport.errors import StudioError
from ..transport.rendering import RenderJobDto
from .artifacts import artifact_bytes, require_model_source, save_document, list_documents


def _now():
    return datetime.now(timezone.utc).isoformat()


class RenderJobs:
    def __init__(self):
        self.instance = uuid4().hex
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio-render")
        self.stopping = False

    def shutdown(self):
        with self.lock:
            self.stopping = True
        self.pool.shutdown(wait=True)

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

    def submit(self, binding, payload):
        if payload.project_id != binding.project_id:
            raise StudioError(403, "PROJECT_MISMATCH", "The render request names another project.")
        source_model = model_source_from(payload.model_source) if payload.model_source else None
        if source_model:
            require_model_source(binding, source_model)
            artifact, data = artifact_bytes(binding, source_model.asset_sha256, run_id=source_model.run_id)
            file_name = artifact.file_name
        else:
            try:
                data = base64.b64decode(payload.content_base64, validate=True)
            except ValueError as exc:
                raise StudioError(422, "RENDER_SOURCE_INVALID", "The model bytes are not valid base64.") from exc
            file_name = payload.file_name
        if not data or len(data) > 32 * 1024 * 1024:
            raise StudioError(413, "RENDER_SOURCE_SIZE", "Render inputs must be nonempty and at most 32 MiB.")
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        try:
            presentation = BlenderPresentation(resolution=payload.resolution, samples=payload.samples,
                camera=BlenderCamera(**payload.camera.model_dump()) if payload.camera else None)
            # Validate the entire mesh before retaining a job or starting Blender.
            mesh_projection_plan(data, {"sha256": digest}, presentation)
        except (ValueError, TypeError) as exc:
            raise StudioError(422, "RENDER_SOURCE_UNSUPPORTED", str(exc)) from exc
        executable = shutil.which(os.environ.get("ARCHFLOW_BLENDER_EXECUTABLE", "blender"))
        if not executable:
            raise StudioError(503, "BLENDER_UNAVAILABLE", "Configure ARCHFLOW_BLENDER_EXECUTABLE for the project runtime.")
        job_id = "render-" + payload.request_id.hex
        signature = canonical_json({"sha256": digest, "presentation": presentation.to_dict(),
            "fileName": file_name, "modelSource": source_model.to_dict() if source_model else None})
        with self.lock:
            if self.stopping:
                raise StudioError(503, "RENDER_STOPPING", "The runtime is shutting down.")
            if job_id in binding.run_ids():
                row = self._load(binding, job_id)
                if row["signature"] != signature:
                    raise StudioError(409, "RENDER_REQUEST_CONFLICT", "This render request ID already names different inputs.")
                return self.get(binding, job_id)
            run = binding.repository.create_run(job_id)
            source_ref = binding.repository.ingest(run=run,
                destination=PersistenceDestination(PersistenceArea.OBJECT), artifact_id="render-source",
                media_type="model/vnd.rhino.3dm", source=BytesIO(data))
            row = {"schema": "StudioRenderJob@1", "jobId": job_id, "projectId": binding.project_id,
                "instance": self.instance, "sequence": 0, "status": "queued", "createdAt": _now(),
                "fileName": file_name, "source": asdict(source_ref), "signature": signature,
                "presentation": presentation.to_dict(), "modelSource": source_model.to_dict() if source_model else None}
            self._record(binding, row)
            self.pool.submit(self._execute, binding, row, data, presentation, executable, source_model)
            return self.get(binding, job_id)

    def _execute(self, binding, initial, data, presentation, executable, source_model):
        row = dict(initial, sequence=1, status="running")
        try:
            with self.lock:
                self._record(binding, row)
            workspace = binding.repository.layout.run(row["jobId"]).workspaces / "render"
            workspace.mkdir(parents=True, exist_ok=False)
            receipt = execute_mesh_projection(data, row["source"], workspace=workspace,
                blender_executable=executable, presentation=presentation)
            receipt_ref = binding.repository.put_json(run=binding.load_run(row["jobId"]),
                destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=row["jobId"]),
                record_kind=BLENDER_PROJECTION, payload={**receipt,
                    "workspace": workspace.relative_to(binding.project_dir).as_posix()})
            if receipt["status"] != "succeeded":
                raise ValueError("; ".join(item["detail"] for item in receipt["failures"]))
            image = next(item for item in receipt["artifacts"] if item["format"] == "png")
            png = (workspace / image["relative_path"]).read_bytes()
            import hashlib
            if hashlib.sha256(png).hexdigest() != image["sha256"]:
                raise ValueError("Rendered image changed before registration.")
            document = save_document(binding, row["jobId"], row["fileName"][:-4] + "-render.png",
                "image/png", base64.b64encode(png).decode(), model_source=source_model,
                view_recipe={"kind": "render", "jobId": row["jobId"], "source": row["source"],
                    "presentation": presentation.to_dict(), "receiptRef": receipt_ref.uri},
                generated_at=_now())
            row.update(status="succeeded", documentSha256=document.asset_sha256)
        except Exception as exc:
            row.update(status="failed", error=str(exc)[:2000])
        finally:
            row.update(sequence=2, finishedAt=_now())
            with self.lock:
                self._record(binding, row)
