"""Projection cache reads: request by source and recipe, poll by key, bytes by digest."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response
from starlette.requests import Request

from archflow.project.index import IndexUnavailable

from ..application.artifacts import ModelSource
from ..application.binding import bound_project
from ..application.projections import (
    MODEL_LINES, PNG_MEDIA_TYPE, ProjectionError, ProjectionQueue, open_projections, projection_spec,
)
from ..transport.errors import StudioError
from ..transport.projections import ProjectionStatusDto, projection_status_dto

router = APIRouter(prefix="/projections", tags=["projections"])

# A status changes as the job runs; the bytes of one digest never do.
_STATUS_HEADERS = {"Cache-Control": "no-store"}
_BLOB_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable"}


def projections_of(state) -> ProjectionQueue:
    """The process's projection queue, opened with the binding's project index on first use.

    Raises ``IndexUnavailable`` when no index answers (a runtime started
    without the Hub's cache keeps none) or the application is shutting down.
    """

    binding = bound_project(state)
    with state.projections_lock:
        if getattr(state, "projections_closed", False):
            raise IndexUnavailable("the projection queue has stopped")
        if state.projections is None:
            state.projections = open_projections(binding)
        return state.projections


def queue_of(request: Request) -> ProjectionQueue:
    try:
        return projections_of(request.app.state)
    except IndexUnavailable as exc:
        raise _unavailable(exc) from exc


def _unavailable(exc: IndexUnavailable) -> StudioError:
    return StudioError(503, "PROJECTION_INDEX_UNAVAILABLE",
                       f"Projections are kept in the project index, which cannot answer now ({exc}); "
                       "show the placeholder and ask again later.")


def _status(row, source: ModelSource | None) -> JSONResponse:
    return JSONResponse(projection_status_dto(row, source).model_dump(mode="json", by_alias=True),
                        headers=_STATUS_HEADERS)


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
    """The status of one exact model's projection; a miss queues it and answers pending.

    The source is checked on every request, done or not (409 when it names no
    retained model), so a stale request is refused and never leaves a row
    for other runs, nor reads another run's.
    """

    source = ModelSource(run_id, state_digest, asset_sha256)
    try:
        spec = projection_spec(source, kind, {"view": view, "size": size, "style": style})
    except ProjectionError as exc:
        raise StudioError(422, "PROJECTION_RECIPE_INVALID", str(exc)) from exc
    projections = queue_of(request)
    try:
        return _status(projections.request(spec), source)
    except IndexUnavailable as exc:
        raise _unavailable(exc) from exc


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
    """A known key's status; a done row whose blob vanished, or a due retry, is queued again once its source checks."""

    try:
        row = queue_of(request).read(key)
    except IndexUnavailable as exc:
        raise _unavailable(exc) from exc
    if row is None:
        raise StudioError(404, "PROJECTION_UNKNOWN", "This key has not been requested; request it by source and recipe.")
    return _status(row, None)
