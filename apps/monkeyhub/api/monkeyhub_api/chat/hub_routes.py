"""The Hub's routes for conversation notes, wake-ups and messages between conversations.

The Notes page reads and edits the architect's instructions and the kept notes
(``notes``). The Agent's tools (``hub_tools``) reach the rest with their
conversation's presentation token, which ``_speaks_for`` checks, so a tool
speaks only for the conversation it was started for. The person's own routes
(the Notes page, search, a wake-up's cancel button) need no token: the Hub only
answers its own local page.
"""

from __future__ import annotations

import secrets
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from . import notes, relay
from .notes import (InstructionsRequest, MemoryNote, NoteChangeRequest, NoteRequest, NotesScope, NotesView, add_note,
                    read_scope, remove_note, restore_note, set_instructions)
from .wakeups import Wakeup, WakeupRequest
from ..models import HubError, HubFailure


def notes_router(chats) -> APIRouter:
    """The Notes page's routes: one scope for every conversation, one per project folder."""

    routes = APIRouter()
    runtime_root = chats.runtime_root
    errors = {404: {"model": HubError}, 409: {"model": HubError}, 422: {"model": HubError}, 503: {"model": HubError}}

    @routes.get("/api/notes", response_model=NotesView, response_model_by_alias=True, responses=errors)
    def read_notes(projectDir: str | None = None) -> NotesView:
        """Every conversation's instructions and notes, and this project folder's when projectDir names one."""
        return NotesView.model_validate({"global": read_scope(runtime_root, None),
                                         "project": read_scope(runtime_root, projectDir) if projectDir else None})

    @routes.put("/api/notes/instructions", response_model=NotesScope, responses=errors)
    def save_instructions(body: InstructionsRequest) -> NotesScope:
        """Replace one scope's instructions with the architect's text; every later turn reads it."""
        return set_instructions(runtime_root, body.projectDir, body.text)

    @routes.post("/api/notes/memories", response_model=MemoryNote, status_code=201, responses=errors)
    def add_memory(body: NoteRequest) -> MemoryNote:
        """Keep one note the architect wrote."""
        return add_note(runtime_root, body.projectDir, body.text, source="person")

    @routes.post("/api/notes/memories/{note_id}/remove", response_model=NotesScope, responses=errors)
    def remove_memory(note_id: str, body: NoteChangeRequest) -> NotesScope:
        """No turn reads this note any more; it stays, marked removed, so it can be put back."""
        return remove_note(runtime_root, body.projectDir, note_id)

    @routes.post("/api/notes/memories/{note_id}/restore", response_model=NotesScope, responses=errors)
    def restore_memory(note_id: str, body: NoteChangeRequest) -> NotesScope:
        """Put a removed note back."""
        return restore_note(runtime_root, body.projectDir, note_id)

    return routes


class AgentNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "remove"]
    text: str | None = Field(default=None, max_length=notes.NOTE_LIMIT)
    scope: Literal["project", "all"] = "project"
    id: str | None = Field(default=None, min_length=8)


def _speaks_for(chats, request: Request, chat_id: str) -> None:
    """Refuse a call that does not carry this conversation's own token."""

    authorization = request.headers.get("authorization", "")
    given = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if not given or not secrets.compare_digest(given, chats.presentation_token(chat_id)):
        raise HubFailure(403, "CHAT_TOOL_TOKEN", "This call does not carry the conversation's own token.")


def agent_router(chats, wakeups) -> APIRouter:
    routes = APIRouter()
    errors = {403: {"model": HubError}, 404: {"model": HubError}, 409: {"model": HubError},
              422: {"model": HubError}, 503: {"model": HubError}}

    @routes.get("/api/chat/search", response_model=list[relay.ChatSearchHit], responses=errors)
    def search_chats(q: str = Query(default="", max_length=200), archived: bool | None = None,
                     limit: int = Query(default=20, ge=1, le=relay.SEARCH_LIMIT)) -> list[relay.ChatSearchHit]:
        """Conversations whose title or messages hold every word of q, newest first; without q, the newest."""
        return relay.search(chats, q, archived=archived, limit=limit)

    @routes.get("/api/chat/sessions/{session_id}/recent", responses=errors)
    def read_recent(session_id: str, limit: int = Query(default=10, ge=1, le=30)) -> dict:
        """A conversation's last messages, as another conversation reads them."""
        return relay.recent(chats, session_id, limit)

    @routes.post("/api/chat/sessions/{session_id}/relay", response_model=relay.ChatRelayResult, responses=errors)
    def relay_message(request: Request, session_id: str, body: relay.ChatRelayRequest) -> relay.ChatRelayResult:
        """This conversation's Agent sends another conversation a message (chat_send)."""
        _speaks_for(chats, request, session_id)
        return relay.send(chats, session_id, body)

    @routes.post("/api/chat/sessions/{session_id}/notes", responses=errors)
    def note(request: Request, session_id: str, body: AgentNoteRequest) -> dict:
        """This conversation's Agent keeps or removes a note (memory_note)."""
        _speaks_for(chats, request, session_id)
        session = chats.get(session_id)
        folder = None if body.scope == "all" else session.projectDir
        if body.action == "add":
            if not body.text:
                raise HubFailure(422, "NOTES_EMPTY", "add takes the text to remember.")
            kept = notes.add_note(chats.runtime_root, folder, body.text, source="agent", chat_id=session_id)
            return {"kept": kept.model_dump(), "scope": body.scope}
        if not body.id:
            raise HubFailure(422, "CHAT_TOOL_INVALID", "remove takes the note's id.")
        try:
            notes.remove_note(chats.runtime_root, folder, body.id)
        except HubFailure:
            # The id may be in the other scope; the note says which in the turn's list, but an Agent may not.
            notes.remove_note(chats.runtime_root, session.projectDir if folder is None else None, body.id)
        return {"removed": body.id}

    @routes.get("/api/chat/sessions/{session_id}/wakeups", response_model=list[Wakeup], responses=errors)
    def read_wakeups(session_id: str) -> list[Wakeup]:
        """This conversation's wake-ups that are still to run, or paused."""
        chats.get(session_id)
        return wakeups.list(session_id)

    @routes.post("/api/chat/sessions/{session_id}/wakeups", response_model=Wakeup, status_code=201, responses=errors)
    def add_wakeup(request: Request, session_id: str, body: WakeupRequest) -> Wakeup:
        """Wake this conversation later; with the conversation's token it is its Agent's (chat_schedule)."""
        agent = bool(request.headers.get("authorization"))
        if agent:
            _speaks_for(chats, request, session_id)
        return wakeups.add(session_id, body, created_by="agent" if agent else "person")

    @routes.post("/api/chat/sessions/{session_id}/wakeups/{wakeup_id}/cancel", response_model=Wakeup, responses=errors)
    def cancel_wakeup(session_id: str, wakeup_id: str) -> Wakeup:
        """Cancel one wake-up of this conversation; the architect's button and the Agent's chat_schedule both use it."""
        return wakeups.cancel(wakeup_id, session_id)

    return routes
