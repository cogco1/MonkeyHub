"""The routes for working in the Rhino document the architect has open."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from starlette.requests import Request

from ...application import live_rhino
from ...binding import bound_project
from ...errors import StudioError
from ...settings import LOCAL_MODE
from ..dto.artifacts import artifact_dto
from ..dto.live_rhino import (LiveRhinoInstancesDto, LiveRhinoOpenDto, LiveRhinoOpenRequest, LiveRhinoSnapshotDto,
                              LiveRhinoSnapshotRequest, LiveRhinoWriteDto, LiveRhinoWriteRequest)


def _this_machine_only(request: Request) -> None:
    # These routes drive a program on the machine the runtime runs on; a shared
    # service answering other people must never reach its host's Rhino.
    if request.app.state.settings.mode != LOCAL_MODE:
        raise StudioError(403, "LIVE_RHINO_LOCAL_ONLY", "Only a local runtime works in a Rhino on its own machine.")


router = APIRouter(tags=["live-rhino"], dependencies=[Depends(_this_machine_only)])


@router.get("/live-rhino/instances", response_model=LiveRhinoInstancesDto, response_model_by_alias=True)
def read_live_rhino_instances() -> dict:
    """List the running Rhinos RhinoCode can reach, with the document each has open.

    A Rhino is listed once StartScriptServer has been run in it.
    """

    return {"instances": [row.to_dict() for row in live_rhino.list_instances()],
            "note": "A running Rhino is listed once its script server is on: opened by POST /api/live-rhino/open, "
                    "or StartScriptServer typed in it once."}


@router.post("/live-rhino/open", response_model=LiveRhinoOpenDto, response_model_by_alias=True)
def open_live_rhino(payload: LiveRhinoOpenRequest) -> dict:
    """Open Rhino for the person with its script server already on, and return it once it answers.

    Ask the person first and send confirmed true only when they said yes. A Rhino already open on the named
    document is returned as it is; another Rhino running without a script server refuses (the person types
    StartScriptServer in it: a second Rhino would not have its unsaved work). Waits up to 90 seconds.
    """

    if not payload.confirmed:
        raise StudioError(422, "LIVE_RHINO_NOT_CONFIRMED", "Ask the person whether MonkeyHub may open Rhino for them, then send confirmed true.")
    instance, opened = live_rhino.open_connected(document_path=payload.document_path)
    return {"rhino": instance.to_dict(), "opened": opened}


@router.get("/live-rhino/document")
def read_live_rhino_document(
    rhino_id: str | None = Query(default=None, alias="rhinoId"),
    document_path: str | None = Query(default=None, alias="documentPath"),
    layer_prefix: str | None = Query(default=None, alias="layerPrefix", description="only objects on this layer or below it"),
    ids: str | None = Query(default=None, description="comma-separated object ids"),
    include_geometry: bool = Query(default=False, alias="includeGeometry", description="points, vertices and text"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    """Read the open document as it is now, unsaved edits included: layers and objects."""

    return live_rhino.read_document(
        rhino_id=rhino_id, document_path=document_path, layer_prefix=layer_prefix,
        ids=[part.strip() for part in ids.split(",") if part.strip()] if ids else None,
        include_geometry=include_geometry, offset=offset, limit=limit,
    )


@router.post("/live-rhino/write", response_model=LiveRhinoWriteDto, response_model_by_alias=True)
def write_live_rhino_document(request: Request, payload: LiveRhinoWriteRequest) -> dict:
    """Add or change objects in the open document as one undoable Rhino command.

    Created objects carry monkeyhub.owner=agent user text. Transform, delete and
    attributes refuse the architect's own objects unless allowUserObjects is
    true. The document is not saved.
    """

    body = payload.model_dump(by_alias=True, exclude_none=True)
    return live_rhino.write_document(
        label=payload.label, operations=body["operations"], project_id=bound_project(request.app.state).project_id,
        rhino_id=payload.rhino_id, document_path=payload.document_path,
        allow_user_objects=payload.allow_user_objects,
    )


@router.post("/live-rhino/snapshot", status_code=201, response_model=LiveRhinoSnapshotDto, response_model_by_alias=True)
def snapshot_live_rhino_document(request: Request, payload: LiveRhinoSnapshotRequest) -> dict:
    """Register a copy of the open document, unsaved edits included, as an external model asset.

    The drawing routes take the returned artifact as sourceAsset {runId, assetSha256}.
    The document in Rhino is not saved and keeps its own path.
    """

    answer = live_rhino.snapshot_document(
        bound_project(request.app.state), rhino_id=payload.rhino_id, document_path=payload.document_path,
        file_name=payload.file_name, drawable_only=payload.drawable_only, layer_prefixes=payload.layer_prefixes,
        event_sink=request.app.state.events, monitor=request.app.state.monitor,
    )
    answer["artifact"] = artifact_dto(answer["artifact"]).model_dump(by_alias=True)
    return answer


@router.get("/live-rhino/capture", response_class=Response, responses={200: {"content": {"image/png": {}}}})
def capture_live_rhino_view(
    rhino_id: str | None = Query(default=None, alias="rhinoId"),
    document_path: str | None = Query(default=None, alias="documentPath"),
    view: str | None = Query(default=None, description="a view name; the active view when omitted"),
    width: int = Query(default=1600, ge=200, le=4000),
    height: int = Query(default=1000, ge=200, le=4000),
) -> Response:
    """A PNG of one Rhino view as the architect sees it; the view is not moved."""

    png = live_rhino.capture_view(rhino_id=rhino_id, document_path=document_path, view=view, width=width, height=height)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})
