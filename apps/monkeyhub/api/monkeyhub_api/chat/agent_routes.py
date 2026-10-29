"""What a conversation's agent may use: the person's other MCP servers, and each conversation's permission level."""

from fastapi import APIRouter

from . import mcp_servers
from ..models import ChatDetail, ChatPermissionModeRequest, HubError


def agent_router(chats) -> APIRouter:
    routes = APIRouter()
    error_responses = {409: {"model": HubError}, 422: {"model": HubError}}

    @routes.get("/api/chat/mcp-servers", response_model=mcp_servers.McpServersView, responses=error_responses)
    def chat_mcp_servers():
        """The MCP servers a conversation may use beside MonkeyHub's own; never their keys."""
        return mcp_servers.public(chats.runtime_root)

    @routes.put("/api/chat/mcp-servers", response_model=mcp_servers.McpServersView, responses=error_responses)
    def update_chat_mcp_servers(body: mcp_servers.McpServersUpdate):
        """Use Claude Code's servers or not, turn single servers off, or replace the Hub's own list; next turn on."""
        return mcp_servers.update(chats.runtime_root, body)

    @routes.put("/api/chat/sessions/{session_id}/permission-mode", response_model=ChatDetail, responses=error_responses)
    def chat_permission_mode(session_id: str, body: ChatPermissionModeRequest):
        """Read only, ask before changing anything, or anything: this conversation's next turns."""
        return chats.set_permission_mode(session_id, body.permissionMode)

    return routes
