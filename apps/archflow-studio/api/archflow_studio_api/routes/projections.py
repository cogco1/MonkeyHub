"""Projection cache reads: request by source and recipe, poll by key, bytes by digest."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from starlette.requests import Request

from ..application.artifacts import ModelSource
from ..application.binding import bound_project
from ..application.projections import (
    MODEL_LINES, PNG_MEDIA_TYPE, ProjectionError, ProjectionQueue, projection_queue, projection_spec,
)
from ..transport.errors import StudioError
from ..transport.projections import ProjectionStatusDto, projection_status_dto

router = APIRouter(prefix="/projections", tags=["projections"])

# A status changes as the job runs; the bytes of one digest never do.
_STATUS_HEADERS = {"Cache-Control": "no-store"}
_BLOB_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable"}


def queue_of(request: Request) -> ProjectionQueue:
    state = request.app.state
    binding = bound_project(state)
    with state.projections_lock:
        if state.projections is None:
            state.projections = projection_queue(binding.settings)
        return state.projections


def _status(row) -> JSONResponse:
    return JSONResponse(projection_status_dto(row).model_dump(mode="json", by_alias=True), headers=_STATUS_HEADERS)


@router.get("", response_model=ProjectionStatusDto, response_model_by_alias=True)
def request_projection(
    request: Request,
    run_id: str = Query(alias="runId", min_length=1),
    state_digest: str = Query(alias="stateDigest", pattern=r"^[0-9a-f]{64}$"),
    asset_sha256: str = Query(alias="assetSha256", pattern=r"^[0-9a-f]{64}$"),
    kind: str = Query(default=MODEL_LINES),
    view: str | None = Query(default=None),
    size: int | None = Query(default=None),
    style: str | None = Query(default=None),
):
    """The status of one exact model's projection; a miss queues it and answers pending."""

    try:
        spec = projection_spec(ModelSource(run_id, state_digest, asset_sha256), kind,
                               {"view": view, "size": size, "style": style})
    except ProjectionError as exc:
        raise StudioError(422, "PROJECTION_RECIPE_INVALID", str(exc)) from exc
    return _status(queue_of(request).request(spec))


@router.get("/blobs/{sha256}", response_class=Response)
def read_projection_blob(request: Request, sha256: str):
    """Content-addressed PNG bytes, cached by the browser for good."""

    try:
        data = queue_of(request).blobs.read(sha256)
    except ProjectionError:
        data = None
    if data is None:
        raise StudioError(404, "PROJECTION_BLOB_NOT_FOUND", "No projection has these bytes; read its status again.")
    return Response(data, media_type=PNG_MEDIA_TYPE, headers=_BLOB_HEADERS)


@router.get("/{key}", response_model=ProjectionStatusDto, response_model_by_alias=True)
def read_projection(request: Request, key: str):
    """A known key's status; a done row whose blob vanished is queued again."""

    projections = queue_of(request)
    row = projections.store.get(key)
    if row is None:
        raise StudioError(404, "PROJECTION_UNKNOWN", "This key has not been requested; request it by source and recipe.")
    return _status(projections.request(row.spec))
