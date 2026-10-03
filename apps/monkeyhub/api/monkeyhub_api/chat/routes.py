"""The Hub API's chat routes: the CLI connections, the projects chats are bound to, and the chats.

The three routers are served apart, each where its routes stood in the Hub's
route order.
"""

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response
from starlette.requests import Request

from . import media
from ..models import (
    HubError, HubFailure, ChatProvider, ChatProject, ChatProjectRequest, ChatSummary, ChatDetail, ChatCreateRequest,
    ChatModelRequest, ChatPostRequest, ChatUsageSource, ChatWorkspace, ChatPermissionRequest, ChatArchiveRequest,
    ChatAttachmentContent,
    ChatPresentationBindRequest, ChatPresentationBinding, ChatPresentationRequest,
)


def providers_router(chats) -> APIRouter:
    routes = APIRouter()
    error_responses = {409: {"model": HubError}, 503: {"model": HubError}}

    @routes.get("/api/chat/providers", response_model=list[ChatProvider])
    def chat_providers(refresh: bool = False):
        return chats.providers(refresh)

    @routes.post("/api/chat/providers/{provider_id}/login", status_code=202, responses=error_responses)
    def chat_provider_login(provider_id: Literal["codex", "claude"]):
        """#334: the CLI's own sign-in, in a console window of its own; Check again reads the result."""
        chats.open_login(provider_id)
        return {"started": True}

    return routes


def projects_router(chats) -> APIRouter:
    routes = APIRouter()

    @routes.get("/api/chat/projects", response_model=list[ChatProject])
    def chat_projects():
        return chats.projects()

    @routes.get("/api/chat/workspace", response_model=ChatWorkspace)
    def chat_workspace():
        return chats.workspace()

    @routes.post("/api/chat/projects", response_model=ChatProject, status_code=201)
    def create_chat_project(body: ChatProjectRequest):
        return chats.create_project(body)

    return routes


def sessions_router(chats) -> APIRouter:
    routes = APIRouter()

    @routes.get("/api/chat/sessions", response_model=list[ChatSummary])
    def chat_sessions(projectId: str | None = None, archived: bool = False):
        return chats.list(projectId, archived=archived)

    @routes.get("/api/chat/usage-sources", response_model=list[ChatUsageSource])
    def chat_usage_sources():
        return chats.usage_sources()

    @routes.post("/api/chat/sessions", response_model=ChatDetail, status_code=201)
    def create_chat(body: ChatCreateRequest):
        return chats.create(body)

    def local_presenter(request: Request) -> None:
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HubFailure(403, "LOCAL_PRESENTER_REQUIRED", "Presentation tools must connect from this machine.")

    @routes.post("/api/chat/presentation/bind", response_model=ChatPresentationBinding)
    def bind_chat_presentation(request: Request, body: ChatPresentationBindRequest):
        local_presenter(request)
        return chats.bind_presentation(body)

    @routes.post("/api/chat/sessions/{session_id}/presentation", response_model=ChatDetail)
    def present_chat(request: Request, session_id: str, body: ChatPresentationRequest):
        local_presenter(request)
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return chats.present(session_id, body, token)

    @routes.get("/api/chat/sessions/{session_id}/documents/{message_id}/{index}", response_class=Response)
    def read_chat_document(session_id: str, message_id: str, index: int, download: bool = False):
        document, data = chats.presentation_document(session_id, message_id, index)
        safe_inline = document.mime_type in media._IMAGE_MIMES | {"application/pdf"}
        disposition = "attachment" if download or not safe_inline else "inline"
        return Response(data, media_type=document.mime_type if safe_inline else "application/octet-stream",
                        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(document.file_name, safe='')}",
                                 "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                                 "ETag": f'"{document.asset_sha256}"'})

    @routes.get("/api/chat/sessions/{session_id}", response_model=ChatDetail)
    def read_chat(session_id: str):
        return chats.get(session_id)

    @routes.post("/api/chat/sessions/{session_id}/continue-native", response_model=ChatDetail)
    def continue_native_chat(session_id: str):
        return chats.continue_native(session_id)

    @routes.post("/api/chat/sessions/{session_id}/release-native", response_model=ChatDetail)
    def release_native_chat(session_id: str):
        return chats.release_native(session_id)

    @routes.post("/api/chat/sessions/{session_id}/messages", response_model=ChatDetail, status_code=202)
    def post_chat(session_id: str, body: ChatPostRequest):
        return chats.post(session_id, body)

    @routes.get("/api/chat/sessions/{session_id}/attachments/{attachment_id}", response_class=FileResponse)
    def read_chat_attachment(session_id: str, attachment_id: str, inline: bool = False):
        attachment, path = chats.attachment(session_id, attachment_id)
        if inline:
            if attachment.mimeType not in media._IMAGE_MIMES:
                raise HubFailure(422, "CHAT_IMAGE_INVALID", "Only supported raster images can be previewed inline.")
            media._verify_image(path.read_bytes(), attachment.mimeType)
        return FileResponse(path, media_type=attachment.mimeType if inline else "application/octet-stream", filename=attachment.name,
                            content_disposition_type="inline" if inline else "attachment",
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @routes.get("/api/chat/sessions/{session_id}/attachments/{attachment_id}/model-source")
    def read_chat_model_source(session_id: str, attachment_id: str):
        import base64
        attachment, path = chats.attachment(session_id, attachment_id)
        if path.suffix.lower() not in {".3dm", ".glb", ".skp", ".dwg"}:
            raise HubFailure(422, "CHAT_MODEL_SOURCE_INVALID", "Choose a 3DM, GLB, SKP or DWG attachment.")
        return {"fileName": attachment.name, "attachmentId": attachment.id,
                "contentBase64": base64.b64encode(path.read_bytes()).decode("ascii")}

    @routes.get("/api/chat/sessions/{session_id}/attachments/{attachment_id}/read", response_model=ChatAttachmentContent)
    def read_chat_attachment_content(session_id: str, attachment_id: str, offset: int = Query(0, ge=0),
                                     limit: int = Query(32768, ge=1, le=65536), page: int = Query(1, ge=1)):
        return chats.read_attachment(session_id, attachment_id, offset=offset, limit=limit, page=page)

    @routes.put("/api/chat/sessions/{session_id}/model", response_model=ChatDetail)
    def set_chat_model(session_id: str, body: ChatModelRequest):
        return chats.set_model(session_id, body.model)

    @routes.put("/api/chat/sessions/{session_id}/archive", response_model=ChatDetail)
    def set_chat_archived(session_id: str, body: ChatArchiveRequest):
        return chats.set_archived(session_id, body.archived)

    @routes.post("/api/chat/sessions/{session_id}/stop", response_model=ChatDetail)
    def stop_chat(session_id: str):
        return chats.stop(session_id)

    @routes.post("/api/chat/sessions/{session_id}/permissions/{permission_id}", response_model=ChatDetail)
    def resolve_chat_permission(session_id: str, permission_id: str, body: ChatPermissionRequest):
        return chats.resolve_permission(session_id, permission_id, body)

    return routes
