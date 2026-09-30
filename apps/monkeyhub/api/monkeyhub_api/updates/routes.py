"""The desktop update routes: status, a local developer patch, the owned host's handshake and automatic checks."""

import asyncio

from fastapi import APIRouter
from starlette.requests import Request

from ..models import HubFailure
from .desktop_updates import MAX_PATCH_BYTES, DesktopUpdates
from .models import CompleteUpdate, RollbackUpdate, UpdateSettings, UpdateStatus


def updates_router(updates: DesktopUpdates) -> APIRouter:
    routes = APIRouter()

    @routes.get("/api/updates/status", response_model=UpdateStatus)
    def update_status() -> UpdateStatus:
        return updates.status()

    @routes.get("/api/updates/restart")
    def update_restart() -> dict:
        return updates.restart()

    @routes.post("/api/updates/prepare", response_model=UpdateStatus, status_code=202)
    async def prepare_update(request: Request) -> UpdateStatus:
        if request.headers.get("x-monkeyhub-local-patch") != "1":
            raise HubFailure(422, "LOCAL_PATCH_REQUIRED", "Choose a local developer patch explicitly.")
        path = updates.begin_upload()
        try:
            size = 0
            with path.open("xb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PATCH_BYTES:
                        raise HubFailure(413, "UPDATE_PATCH_TOO_LARGE", "A patch must be no larger than 256 MiB.")
                    output.write(chunk)
            if size == 0:
                raise HubFailure(422, "UPDATE_PATCH_EMPTY", "Choose a nonempty patch ZIP.")
        except (Exception, asyncio.CancelledError) as error:
            updates.fail_upload(path, str(error))
            raise
        updates.prepare(path)
        return updates.status()

    @routes.post("/api/updates/apply", response_model=UpdateStatus, status_code=202)
    def apply_update() -> UpdateStatus:
        return updates.apply()

    @routes.post("/api/updates/complete", response_model=UpdateStatus)
    def complete_update(body: CompleteUpdate) -> UpdateStatus:
        return updates.complete(body.fromCommit)

    @routes.post("/api/updates/rollback", response_model=UpdateStatus)
    def rollback_update(body: RollbackUpdate) -> UpdateStatus:
        return updates.rollback(body.fromCommit, body.targetCommit)

    @routes.post("/api/updates/check", response_model=UpdateStatus, status_code=202)
    def check_updates() -> UpdateStatus:
        return updates.check_now()

    @routes.put("/api/updates/settings", response_model=UpdateStatus)
    def update_settings(body: UpdateSettings) -> UpdateStatus:
        return updates.set_auto_update(body.autoUpdate)

    return routes
