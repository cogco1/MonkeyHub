"""The routes of the Hub's applications and project runtimes.

``/api/apps`` starts and stops an application for a project, and
``/api/project/modeling`` prepares a project's modeling workspace by starting
MonkeyArch the same way; the Hub serves the two apart. ``/api/runtime`` reads
and follows the open project runtimes and forwards each project's Studio
requests to its own worker.
"""

import asyncio
import json
import queue
import time

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

from project_runtime.api.dto.project import ModelingInitializeDto, ModelingInitializeRequestDto

from .. import projects
from ..chat import preparation, transport
from ..models import AppId, AppStatus, HubError, HubFailure
from ..settings.store import read_application_settings
from .models import (HubRuntimeDto, OperationAcknowledgeRequest, OperationRecord, ProjectRuntimeDto,
                     OpenRuntimeRequest, RuntimeProjectRequest, RuntimeEvent)


def apps_and_modeling_routers(settings, applications, chats, runtimes) -> tuple[APIRouter, APIRouter]:
    """The routes of /api/apps, and /api/project/modeling, which starts MonkeyArch through start_app."""
    apps, modeling = APIRouter(), APIRouter()

    @apps.get("/api/apps", response_model=list[AppStatus])
    def list_apps(projectDir: str | None = None) -> list[AppStatus]:
        return applications.statuses(project_dir=projectDir)

    error_responses = {409: {"model": HubError}, 503: {"model": HubError}}

    @apps.post("/api/apps/{app_id}/start", response_model=AppStatus, status_code=202, responses=error_responses)
    def start_app(app_id: AppId, projectDir: str | None = None) -> AppStatus:
        runtime = None
        if app_id in {"monkeyarch", "monkeyboard"}:
            target = projectDir or read_application_settings(settings.runtime_root).project_dir
            if target:
                project_id, target = projects._project(target)
                runtime = runtimes.open(project_id, target)
        with chats.application_lifecycle(app_id, project_dir=projectDir):
            if runtime is not None and any(row.state == "crashed" for row in applications.worker_snapshots(project_dir=runtime.project_dir)):
                runtimes.recover(runtime)
                return applications.status(app_id, project_dir=runtime.project_dir)
            return applications.start(app_id, project_dir=projectDir)

    @apps.post("/api/apps/{app_id}/stop", response_model=AppStatus, status_code=202, responses=error_responses)
    def stop_app(app_id: AppId, projectDir: str | None = None) -> AppStatus:
        with chats.application_lifecycle(app_id, stopping=True, project_dir=projectDir):
            return applications.stop(app_id, project_dir=projectDir)

    @modeling.post("/api/project/modeling", response_model=ModelingInitializeDto, response_model_by_alias=True)
    def prepare_project_modeling(body: ModelingInitializeRequestDto, projectDir: str | None = None) -> dict:
        target = projectDir if projectDir is not None else read_application_settings(settings.runtime_root).project_dir
        if not target:
            raise HubFailure(409, "PROJECT_REQUIRED", "Choose a project before preparing its modeling workspace.")
        project_id, project_dir = projects._project(target)
        if project_id != body.project_id:
            raise HubFailure(409, "CHAT_PROJECT_MISMATCH", "The selected project changed before its workspace was prepared.")
        status = start_app("monkeyarch", projectDir=project_dir)
        deadline = time.monotonic() + 35
        while status.state == "starting" and time.monotonic() < deadline:
            time.sleep(0.1)
            status = applications.status("monkeyarch", project_dir=project_dir)
        if status.state != "running":
            if status.error is not None:
                raise HubFailure(503, status.error.code, status.error.detail)
            raise HubFailure(503, "CHAT_STUDIO_UNAVAILABLE", "The project workspace is not ready. Retry preparing this project.")
        base, binding = preparation._bound_studio(chats.hub_url, None, project_id=project_id, project_dir=project_dir)
        prepared = transport._request_json(base, "/api/project/modeling", "POST", {"projectId": binding["projectId"]})
        runtimes.open(project_id, project_dir)
        return prepared

    return apps, modeling


def runtime_router(runtimes) -> APIRouter:
    routes = APIRouter()

    @routes.get("/api/runtime", response_model=HubRuntimeDto)
    def read_runtime():
        runtimes.discover()
        return runtimes.snapshot()

    @routes.post("/api/runtime/projects/open", response_model=ProjectRuntimeDto)
    def open_runtime(body: OpenRuntimeRequest):
        runtime = runtimes.open(body.projectId, body.projectDir)
        return runtimes.project_snapshot(runtime)

    @routes.get("/api/runtime/projects/{runtime_id}", response_model=ProjectRuntimeDto)
    def read_project_runtime(runtime_id: str):
        return runtimes.project_snapshot(runtimes.get(runtime_id))

    @routes.post("/api/runtime/projects/{runtime_id}/recover", response_model=ProjectRuntimeDto, status_code=202)
    def recover_runtime(runtime_id: str, body: RuntimeProjectRequest):
        return runtimes.recover(runtimes.get(runtime_id, body.projectId))

    @routes.post("/api/runtime/projects/{runtime_id}/close", response_model=ProjectRuntimeDto, status_code=202)
    def close_runtime(runtime_id: str, body: RuntimeProjectRequest):
        return runtimes.close(runtimes.get(runtime_id, body.projectId))

    @routes.post("/api/runtime/operations/{operation_id}/acknowledge", response_model=OperationRecord)
    def acknowledge_operation(operation_id: str, body: OperationAcknowledgeRequest):
        # A dismissed notice, kept in the runtime's journal; the operation itself is unchanged.
        return runtimes.acknowledge(runtimes.get(body.runtimeId, body.projectId), operation_id)

    @routes.get("/api/runtime/events", response_class=StreamingResponse, response_model=RuntimeEvent,
                responses={200: {"description": "Runtime SSE; every attachment begins with a coherent snapshot.",
                                 "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    async def runtime_events(request: Request):
        await asyncio.to_thread(runtimes.discover)

        def relayed(value: dict, sequence: int, *, replay: bool = False) -> str:
            # A project worker's own events, relayed (#366): ``index`` says its project index moved
            # (or may have: no revision), ``studio`` is a Studio event with the worker ``stream`` it
            # was numbered on. Neither is part of the Hub's runtime snapshot. A replayed one carries
            # no id: it resumes nothing.
            name = "index" if "index" in value else "studio"
            body = {"serverId": runtimes.server_id, "sequence": sequence, "runtimeId": value.get("runtimeId"), name: value[name]}
            if name == "studio":
                body["stream"] = value.get("stream")
            if replay:
                body["replay"] = True
            head = "" if replay else f"id: {runtimes.server_id}:{sequence}\n"
            return f"{head}event: {name}\ndata: {json.dumps(body, separators=(',', ':'))}\n\n"

        async def stream():
            # Subscribe before reading the snapshot. Drop queued runtime events the
            # snapshot already includes, so no change can fall through a gap.
            with runtimes.events.subscribe() as (_, inbox):
                runtimes._clients += 1
                try:
                    # Taken after subscribing: an event kept here and also queued arrives twice,
                    # and the client knows it by its ``<stream>:<seq>``; none falls between the two.
                    replay = runtimes.studio_replay()
                    snapshot = await asyncio.to_thread(runtimes.snapshot)
                    sequence = snapshot.sequence
                    event = RuntimeEvent(serverId=runtimes.server_id, sequence=sequence, kind="runtime/snapshot", snapshot=snapshot)
                    yield f"id: {runtimes.server_id}:{sequence}\nevent: runtime\ndata: {event.model_dump_json()}\n\n"
                    # The projects' latest Studio events: a page's event panel opens with them.
                    for value in replay:
                        yield relayed(value, sequence, replay=True)
                    while not await request.is_disconnected() and not runtimes._closing.is_set():
                        try:
                            value = await asyncio.to_thread(inbox.get, True, 1)
                        except queue.Empty:
                            yield ": keepalive\n\n"
                            continue
                        if "index" in value or "studio" in value:
                            # Queued after subscribing and never in the snapshot: always sent.
                            sequence = max(sequence, value["seq"])
                            yield relayed(value, value["seq"])
                            continue
                        if value["seq"] <= sequence:
                            continue
                        sequence = value["seq"]
                        event = RuntimeEvent(serverId=runtimes.server_id, sequence=sequence, kind=value["kind"], runtimeId=value.get("runtimeId"))
                        yield f"id: {runtimes.server_id}:{sequence}\nevent: runtime\ndata: {event.model_dump_json()}\n\n"
                finally:
                    runtimes._clients -= 1

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @routes.api_route("/api/runtime/projects/{runtime_id}/studio/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE"], include_in_schema=False)
    async def studio_request(request: Request, runtime_id: str, path: str):
        runtime = await asyncio.to_thread(runtimes.get, runtime_id)
        target = "/" + path + (f"?{request.url.query}" if request.url.query else "")
        if path == "api/events" and request.method == "GET":
            # Superseded (#366): the Hub follows each worker's stream once and relays it on
            # /api/runtime/events, which every Hub page already holds. Never buffered through
            # the forward below, which would wait on a stream that does not end.
            raise HubFailure(404, "STUDIO_EVENTS_RELAYED", "Follow /api/runtime/events: it carries this project's events.")
        result = await asyncio.to_thread(runtimes.forward, runtime, target, request.method,
                                         await request.body(), dict(request.headers))
        return Response(result.body, status_code=result.status, headers=result.headers)

    return routes
