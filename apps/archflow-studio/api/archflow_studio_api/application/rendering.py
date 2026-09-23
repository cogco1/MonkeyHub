"""Persistent image attempts in the existing Project Runtime and P036.

RenderJobRecords' run-record storage, sequence readback and project history
derive from YNNAP-HelloWorld's 6f39e67116a2716c2dac70bb4ee3cf1b369afd9d
(PR #235). Server image execution is independent of the browser-native lane.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import re
import threading
from time import perf_counter
from uuid import uuid4

from archflow.contracts.canonical import canonical_json
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_RENDER_JOB
from archflow.project.refs import ProjectRecordRef
from archflow.project.repository import ProjectRepositoryError
from monkeymonitor.usage import TokenUsage

from ..transport.artifacts import document_dto
from ..transport.errors import StudioError
from ..transport.rendering import RenderCapabilityDto, RenderJobDto, RenderRequestDto
from .artifacts import document_bytes, list_documents, save_document, require_model_source, ModelSource
from .render_contract import (
    ImageRenderAdapter, RenderImage, RenderInput, RenderOutputOptions,
    RenderPageRef, RenderProviderError,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _page_key(page):
    return (page.run_id, page.asset_sha256, page.revision_ref, page.page_index)


def _resolve_page(binding, ref):
    # document_bytes' optional revision argument supports legacy wildcard reads;
    # Render requires null to mean exactly the registration with no revision.
    document = next((row for row in list_documents(binding, ref.run_id) if
                     (row.asset_sha256, row.revision_ref) == (ref.asset_sha256, ref.revision_ref)), None)
    if document is None or not any(page.page_index == ref.page_index for page in document.pages):
        raise StudioError(422, "RENDER_SOURCE_INVALID", "Select an exact registered image page in this project.")
    if document.mime_type not in ("image/png", "image/jpeg"):
        raise StudioError(422, "RENDER_SOURCE_UNSUPPORTED", "AI Render currently accepts registered PNG or JPEG images.")
    _, data = document_bytes(binding, ref.run_id, ref.asset_sha256, ref.revision_ref)
    return document, RenderImage(RenderPageRef(**ref.model_dump()), data, document.mime_type)


def _snapshot(document):
    return {
        "modelSource": document.model_source.to_dict() if document.model_source else None,
        "sourceStageRef": document.source_stage_ref,
        "viewRecipe": document.view_recipe,
    }


def _freshness(binding, request, snapshots):
    try:
        pages = [request.source, *request.references]
        for page in pages:
            _resolve_page(binding, page)
        documents = list_documents(binding)
        replaced = {_page_key(page) for doc in documents for page in doc.replaces_pages}
        if any(_page_key(page) in replaced for page in pages):
            return "outdated", "A source or reference page has a newer registered replacement."
        for snapshot in snapshots:
            if snapshot["modelSource"]:
                require_model_source(binding, ModelSource.from_dict(snapshot["modelSource"]))
            if snapshot["sourceStageRef"]:
                from .design_history import stage_ref_from

                ref = stage_ref_from(binding, snapshot["sourceStageRef"])
                stage = binding.design_stage(ref)
                branch = binding.repository.read_design_branches().get(stage.branch_id)
                if branch is None:
                    return "unavailable", "The source model's design branch is unavailable."
                if ProjectRecordRef.from_dict(branch["head_stage"]) != ref:
                    return "outdated", "The source model's design branch has advanced."
        return "current", None
    except (StudioError, ProjectRepositoryError, OSError, ValueError):
        return "unavailable", "An exact source image, reference or declared model can no longer be resolved."


class RenderJobRecords:
    """One per-project image executor; retained attempts never replay on reads."""

    def __init__(self, adapter: ImageRenderAdapter | None = None, *, monitor=None):
        self.instance = uuid4().hex
        self.lock = threading.RLock()
        self.stopping = False
        self.adapter = adapter
        self.monitor = monitor
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="render-image")

    def stop_accepting(self):
        with self.lock:
            self.stopping = True

    def shutdown(self):
        self.stop_accepting()
        # An accepted call finishes once, including its output registration.
        self.executor.shutdown(wait=True)

    def capabilities(self):
        if self.adapter is None:
            return []
        return [RenderCapabilityDto(**asdict(self.adapter.capability()))]

    def _record(self, binding, row):
        binding.repository.put_json(
            run=binding.load_run(row["jobId"]),
            destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=row["jobId"]),
            record_kind=STUDIO_RENDER_JOB, payload=row,
        )

    def _load(self, binding, job_id):
        if not re.fullmatch(r"render-[0-9a-f]{32}", job_id) or job_id not in binding.run_ids():
            raise StudioError(404, "RENDER_NOT_FOUND", "The render task is not in this project.")
        rows = [binding.repository.load_json(ref) for ref in binding.record_refs(job_id)
                if ref.record_kind == STUDIO_RENDER_JOB]
        if not rows:
            raise StudioError(409, "RENDER_REQUEST_UNCERTAIN", "This request has an incomplete retained attempt; it cannot be replayed.")
        row = max(rows, key=lambda value: value["sequence"])
        if (row.get("projectId"), row.get("jobId"), row.get("schema")) != (binding.project_id, job_id, "StudioRenderJob@2"):
            raise StudioError(409, "RENDER_RECORD_INVALID", "The retained render task has a different binding or unsupported schema.")
        return row

    def get(self, binding, job_id):
        with self.lock:
            row = self._load(binding, job_id)
        status, error = row["status"], row.get("error")
        if status in ("queued", "running") and row["instance"] != self.instance:
            status, error = "unknown", "The runtime stopped before confirming this attempt. Refresh never resends it."
        document = None
        result_available = False
        # A crash between save_document and the completion transition must not
        # hide an already retained image or dispatch the paid request again.
        for item in list_documents(binding, job_id):
            recipe = item.view_recipe or {}
            if recipe.get("jobId") == job_id and recipe.get("request") == row["request"]:
                document = item
                if status in ("queued", "running", "unknown"):
                    status, error = "succeeded", None
                try:
                    document_bytes(binding, job_id, item.asset_sha256, item.revision_ref)
                    result_available = True
                except (StudioError, ProjectRepositoryError, OSError, ValueError):
                    error = "The retained result bytes are unavailable; this attempt will not be replayed."
                break
        if status == "succeeded" and document is None:
            error = "The retained result registration is unavailable; this attempt will not be replayed."
        request = RenderRequestDto.model_validate(row["request"])
        source_state, reason = _freshness(binding, request, row["sourceSnapshots"])
        return RenderJobDto(
            projectId=binding.project_id, jobId=job_id, requestId=request.request_id,
            status=status, execution=row["execution"], providerId=row["providerId"], model=row["model"],
            request=request, createdAt=row["createdAt"], finishedAt=row.get("finishedAt"), error=error,
            errorCode=row.get("errorCode") or ("runtime_interrupted" if status == "unknown" else None),
            sourceState=source_state, sourceStateReason=reason,
            document=document_dto(document) if document else None, resultAvailable=result_available,
            providerRequestId=row.get("providerRequestId"), inputTokens=row.get("inputTokens"),
            outputTokens=row.get("outputTokens"), costUsd=row.get("costUsd"),
        )

    def list(self, binding):
        jobs = [self.get(binding, run_id) for run_id in binding.run_ids()
                if re.fullmatch(r"render-[0-9a-f]{32}", run_id)
                and any(ref.record_kind == STUDIO_RENDER_JOB for ref in binding.record_refs(run_id))]
        return sorted(jobs, key=lambda job: (job.created_at, job.job_id), reverse=True)

    def submit(self, binding, payload):
        if payload.project_id != binding.project_id:
            raise StudioError(403, "PROJECT_MISMATCH", "The render request names another project.")
        request = payload.model_dump(mode="json", by_alias=True)
        job_id = "render-" + payload.request_id.hex
        with self.lock, binding.repository.working_draft_guard():
            if job_id in binding.run_ids():
                old = self._load(binding, job_id)
                if canonical_json(old["request"]) != canonical_json(request):
                    raise StudioError(409, "RENDER_REQUEST_CONFLICT", "This request ID already names different render parameters.")
                return self.get(binding, job_id)
            if self.stopping:
                raise StudioError(503, "RENDER_STOPPING", "The runtime is stopping.")
            capability = self.adapter.capability() if self.adapter else None
            if capability is None or not capability.available or capability.provider_id != payload.provider_id:
                raise StudioError(503, "RENDER_UNAVAILABLE", "Configure the selected image provider before generating.")
            if capability.execution != "server-image":
                raise StudioError(422, "RENDER_EXECUTION_UNSUPPORTED", "This endpoint dispatches server image adapters only.")
            if (payload.output.size not in capability.sizes or payload.output.aspect_ratio not in capability.aspect_ratios
                    or len(payload.references) > capability.max_references or not payload.direction.strip()):
                raise StudioError(422, "RENDER_OPTIONS_UNSUPPORTED", "The direction, image options or reference count is unsupported.")
            snapshots = []
            for ref in [payload.source, *payload.references]:
                document, _ = _resolve_page(binding, ref)
                if document.model_source:
                    require_model_source(binding, document.model_source)
                snapshots.append(_snapshot(document))
            binding.repository.create_run(job_id)
            row = {
                "schema": "StudioRenderJob@2", "projectId": binding.project_id, "jobId": job_id,
                "instance": self.instance, "sequence": 0, "status": "queued", "createdAt": _now(),
                "request": request, "sourceSnapshots": snapshots,
                "providerId": capability.provider_id, "model": capability.model, "execution": capability.execution,
            }
            self._record(binding, row)
            observer = self.monitor.observer(project_id=binding.project_id, run_id=job_id) if self.monitor else None
            self.executor.submit(self._execute, binding, row, observer)
            return self.get(binding, job_id)

    def _transition(self, binding, row, **values):
        with self.lock, binding.repository.working_draft_guard():
            updated = dict(row, sequence=row["sequence"] + 1, **values)
            self._record(binding, updated)
            row.update(updated)

    def _execute(self, binding, row, observer):
        payload = RenderRequestDto.model_validate(row["request"])
        started_at, started = _now(), perf_counter()
        dispatched = False
        status = "failed"
        output = None
        try:
            _, source = _resolve_page(binding, payload.source)
            references = tuple(_resolve_page(binding, ref)[1] for ref in payload.references)
            self._transition(binding, row, status="running")
            dispatched = True
            output = self.adapter.generate(RenderInput(
                str(payload.request_id), source, references, payload.direction,
                RenderOutputOptions(**payload.output.model_dump()),
            ))
            if output.mime_type not in ("image/png", "image/jpeg"):
                raise StudioError(422, "RENDER_RESULT_INVALID", "The provider returned an unsupported image format.")
            snapshot = row["sourceSnapshots"][0]
            with binding.repository.working_draft_guard():
                document = save_document(
                    binding, row["jobId"], "render.jpg" if output.mime_type == "image/jpeg" else "render.png",
                    output.mime_type, base64.b64encode(output.data).decode("ascii"),
                    model_source=ModelSource.from_dict(snapshot["modelSource"]) if snapshot["modelSource"] else None,
                    source_stage_ref=snapshot["sourceStageRef"],
                    view_recipe={"kind": "ai-render", "jobId": row["jobId"], "request": row["request"],
                                 "sourceSnapshots": row["sourceSnapshots"], "providerId": row["providerId"], "model": row["model"]},
                    generated_at=_now(),
                )
            status = "succeeded"
            self._transition(binding, row, status=status, finishedAt=_now(), documentSha256=document.asset_sha256,
                             providerRequestId=output.provider_request_id, inputTokens=output.input_tokens,
                             outputTokens=output.output_tokens, costUsd=output.cost_usd)
        except RenderProviderError as exc:
            status = exc.outcome
            messages = {
                "not_configured": "The image provider is not configured.",
                "unsupported_input": "The image provider does not support these images or options.",
                "provider_rejected": "The image provider rejected this request.",
                "timeout": "The provider timed out; this attempt will not be resent automatically.",
                "transport_unknown": "The provider connection ended without a confirmed result.",
                "invalid_output": "The provider did not return a usable final image.",
            }
            self._transition(binding, row, status=status, finishedAt=_now(), errorCode=exc.code,
                             error=messages.get(exc.code, "The provider could not confirm a usable image result."))
        except Exception:
            status = "unknown" if dispatched else "failed"
            # Never retain an exception string: provider/library errors can
            # contain credentials, image data or private request text.
            self._transition(binding, row, status=status, finishedAt=_now(),
                             errorCode="runtime_uncertain" if dispatched else "source_unavailable", error=(
                "The image attempt could not be confirmed. Refresh never resends it." if dispatched else
                "The exact source could not be prepared; no provider call was sent."
            ))
        finally:
            if observer:
                observer(dict(phase="image_render", status=status, started_at=started_at, ended_at=_now(),
                              duration_ms=round((perf_counter() - started) * 1000), model_call=dispatched,
                              provider=row["providerId"], model=row["model"] or "unknown", billing_mode="unknown",
                              tokens=TokenUsage(input_tokens=output.input_tokens, output_tokens=output.output_tokens) if output else None,
                              details={"execution_path": "server-image", "retry_attempt": 0,
                                       "input_identity": {"asset_sha256": payload.source.asset_sha256},
                                       "output_refs": ["run:" + row["jobId"]] if status == "succeeded" else []}))
