"""``GET /api/artifacts``: what the run receipts certify, and its bytes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import Response
from starlette.requests import Request

from ..application.artifacts import (
    artifact_bytes,
    export_rhino_work_model,
    bind_document_model_source,
    DocumentPageReplacement,
    document_bytes,
    list_artifacts,
    list_documents,
    open_document_work_copy,
    save_document,
    register_model_asset,
    save_viewport_capture,
)
from ..application.binding import bound_project
from ..transport.artifacts import (
    ArtifactListDto,
    DocumentWorkCopyDto,
    DocumentWorkCopyRequestDto,
    ModelAssetRequestDto,
    DocumentModelSourceRequestDto,
    ProjectArtifactDto,
    RhinoWorkExportRequestDto,
    SourceDocumentDto,
    SourceDocumentListDto,
    SourceDocumentRequestDto,
    ViewportCaptureDto,
    ViewportCaptureRequestDto,
    capture_dto,
    artifact_dto,
    model_source_from,
    document_dto,
    to_dto,
    work_copy_dto,
)
from ..transport.errors import StudioError

router = APIRouter(tags=["artifacts"])


@router.post("/model-assets", response_model=ProjectArtifactDto, response_model_by_alias=True, status_code=201)
def create_model_asset(request: Request, payload: ModelAssetRequestDto) -> ProjectArtifactDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The model asset names another project.")
    return artifact_dto(register_model_asset(binding, payload.run_id, payload.state_digest, payload.file_name,
                                           payload.content_base64, event_sink=request.app.state.events))


@router.post("/documents/{asset_sha256}/model-source", response_model=SourceDocumentDto, response_model_by_alias=True)
def associate_document_model_source(request: Request, asset_sha256: str, payload: DocumentModelSourceRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The source document names another project.")
    return document_dto(bind_document_model_source(binding, payload.run_id, asset_sha256, model_source_from(payload.model_source)))


@router.post("/documents", response_model=SourceDocumentDto, response_model_by_alias=True, status_code=201)
def create_document(request: Request, payload: SourceDocumentRequestDto) -> SourceDocumentDto:
    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The source document names another project.")
    return document_dto(save_document(binding, payload.run_id, payload.file_name, payload.mime_type, payload.content_base64,
                                     model_source_from(payload.model_source) if payload.model_source else None,
                                     tuple(DocumentPageReplacement(**page.model_dump()) for page in payload.replaces_pages)))


@router.get("/documents", response_model=SourceDocumentListDto, response_model_by_alias=True)
def read_documents(request: Request, run_id: str | None = Query(default=None, alias="runId", min_length=1)) -> SourceDocumentListDto:
    binding = bound_project(request.app.state)
    return SourceDocumentListDto(
        project_id=binding.project_id, run_id=run_id,
        documents=[document_dto(document) for document in list_documents(binding, run_id)],
    )


@router.get("/documents/{asset_sha256}/bytes", response_class=Response)
def read_document_bytes(request: Request, asset_sha256: str, run_id: str = Query(alias="runId", min_length=1),
                        revision_ref: str | None = Query(default=None, alias="revisionRef")) -> Response:
    document, data = document_bytes(bound_project(request.app.state), run_id, asset_sha256, revision_ref)
    return Response(
        content=data, media_type=document.mime_type,
        headers={
            "ETag": f'"{document.asset_sha256}"',
            "Content-Disposition": _content_disposition(document.file_name, asset_sha256).replace("attachment;", "inline;", 1),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/documents/{asset_sha256}/work-copy",
    response_model=DocumentWorkCopyDto,
    response_model_by_alias=True,
    status_code=201,
)
def create_document_work_copy(
    request: Request, asset_sha256: str, payload: DocumentWorkCopyRequestDto
) -> DocumentWorkCopyDto:
    """Give this registered image page an editable file on disk, once.

    Explicitly asked for, like the Rhino work-model export beside it: the run
    and revision are named in the body because the same registered bytes can
    belong to more than one registration. Asking again answers with the copy
    already made, edits and all. Only the project-relative path comes back —
    no request here or anywhere selects a place on this machine's disk.
    """

    binding = bound_project(request.app.state)
    if payload.project_id != binding.project_id:
        raise StudioError(403, "PROJECT_MISMATCH", "The source document names another project.")
    return work_copy_dto(
        open_document_work_copy(binding, payload.run_id, asset_sha256, revision_ref=payload.revision_ref)
    )


@router.get(
    "/artifacts",
    response_model=ArtifactListDto,
    response_model_by_alias=True,
)
def read_artifacts(request: Request) -> ArtifactListDto:
    """List the exported models, each one still answered for by its receipt."""

    return to_dto(list_artifacts(bound_project(request.app.state), include_candidate_sources=True))


@router.post(
    "/captures",
    response_model=ViewportCaptureDto,
    response_model_by_alias=True,
    status_code=201,
)
def create_viewport_capture(
    request: Request,
    payload: ViewportCaptureRequestDto,
) -> ViewportCaptureDto:
    """Save one viewport PNG in the explicitly named run's workspace."""

    return capture_dto(
        save_viewport_capture(
            bound_project(request.app.state),
            payload.run_id,
            payload.png_base64,
        )
    )


@router.post(
    "/artifacts/{sha256}/rhino-export",
    response_model=ProjectArtifactDto,
    response_model_by_alias=True,
    status_code=201,
)
def export_artifact_work_model(
    request: Request, sha256: str, payload: RhinoWorkExportRequestDto
) -> ProjectArtifactDto:
    """Make this run's exact STEP into an editable ``.3dm`` through Rhino.

    Ordinary and blocking: it drives this machine's Rhino once, one export at
    a time, and answers with the work model's own artifact row. The run is
    named in the body because the same bytes can be exported by more than one
    run, and this export belongs to the one that was asked for. Asking again
    for the same source answers with the model already made.
    """

    return artifact_dto(
        export_rhino_work_model(
            bound_project(request.app.state),
            request.app.state.settings,
            run_id=payload.run_id,
            sha256=sha256,
        )
    )


# The one route that does not answer with a DTO: its body is the exported file
# itself, served only after those bytes hash to the digest in the path.
@router.get("/artifacts/{sha256}/bytes", response_class=Response)
def read_artifact_bytes(request: Request, sha256: str) -> Response:
    """The certified bytes, under the digest that identifies them."""

    record, data = artifact_bytes(bound_project(request.app.state), sha256)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            # The digest is the identity, so it is the entity tag.
            "ETag": f'"{record.sha256}"',
            "Content-Disposition": _content_disposition(
                record.file_name, sha256
            ),
            # Never cached: the next request must verify the file again.
            "Cache-Control": "no-store",
        },
    )


def _content_disposition(file_name: str, sha256: str) -> str:
    """The save-as name, in the two forms RFC 6266 asks for.

    Header values go out as latin-1, so a model called ``别墅.3dm`` — or any
    name with an accent in it — cannot travel as itself. RFC 6266 answers this
    exactly: an ASCII ``filename`` every client can read, and a
    percent-encoded UTF-8 ``filename*`` that carries the real name for those
    that can. The ASCII form also drops the quote and backslash a receipt on
    disk would otherwise use to write this response's headers.
    """

    name = file_name or f"{sha256[:8]}.3dm"
    fallback = "".join(
        character
        if character.isascii()
        and character.isprintable()
        and character not in '"\\'
        else "_"
        for character in name
    )
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )
