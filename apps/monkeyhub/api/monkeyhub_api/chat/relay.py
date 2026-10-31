"""Conversations finding, reading and messaging each other, and the architect searching them.

A conversation sends another one a message as a colleague would: it arrives as
a user-role message marked with where it came from (``origins``), starts a turn
there or joins the running one as an interjection, and the answer stays in the
receiving conversation until it sends one back. A chain of such messages that no
person started stops at ``RELAY_DEPTH``, so two conversations cannot keep each
other running unattended.

Search reads the transcripts the Hub already holds in memory: titles and what
the architect and the Agents said, never tool rows. Every word of the query must
occur (case- and width-folded); the newest conversations come first.
"""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from ..models import ChatMessageOrigin, ChatPostRequest, HubFailure

RELAY_DEPTH = 3
SNIPPET = 160
SEARCH_LIMIT = 50


class ChatSearchMatch(BaseModel):
    messageId: str
    role: str
    createdAt: str
    snippet: str


class ChatSearchHit(BaseModel):
    chatId: str
    title: str
    projectId: str
    projectDir: str
    status: str
    archived: bool
    updatedAt: str
    matches: list[ChatSearchMatch] = Field(default_factory=list)


class ChatRelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(description="the receiving conversation's chatId")
    text: str = Field(min_length=1, max_length=8000)


class ChatRelayResult(BaseModel):
    chatId: str
    messageId: str | None
    started: bool = Field(description="false when the receiving conversation was running and takes it as an interjection")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _snippet(text: str, at: int, length: int) -> str:
    start = max(0, at - SNIPPET // 3)
    end = min(len(text), start + SNIPPET)
    piece = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def search(chats, query: str, *, archived: bool | None = None, limit: int = 20) -> list[ChatSearchHit]:
    """Conversations whose title or messages hold every word of ``query``; an empty query lists the newest."""

    words = [_fold(word) for word in query.split() if word.strip()]
    limit = max(1, min(limit, SEARCH_LIMIT))
    hits: list[ChatSearchHit] = []
    with chats._lock:
        chats._load()
        rows = sorted(chats._sessions.values(), key=lambda row: row.updatedAt, reverse=True)
        for row in rows:
            if archived is not None and row.archived != archived:
                continue
            title = _fold(row.title)
            spoken = [message for message in row.messages if message.role in {"user", "assistant"} and message.content]
            folded = [_fold(message.content) for message in spoken]
            if words and not all(word in title or any(word in text for text in folded) for word in words):
                continue
            matches = []
            for message, text in zip(reversed(spoken), reversed(folded)):
                found = [text.find(word) for word in words if word in text]
                if words and not found:
                    continue
                matches.append(ChatSearchMatch(messageId=message.id, role=message.role, createdAt=message.createdAt,
                                               snippet=_snippet(message.content, min(found) if found else 0, len(text))))
                if len(matches) == 3 or not words:
                    break
            hits.append(ChatSearchHit(chatId=row.id, title=row.title, projectId=row.projectId, projectDir=row.projectDir,
                                      status=row.status, archived=row.archived, updatedAt=row.updatedAt, matches=matches))
            if len(hits) >= limit:
                break
    return hits


def recent(chats, chat_id: str, limit: int = 10) -> dict:
    """The last things said in a conversation, for another one to read: no tool rows, each message cut short."""

    detail = chats.get(chat_id)
    spoken = [row for row in detail.messages if row.role in {"user", "assistant"} and row.content]
    return {"chatId": detail.id, "title": detail.title, "projectId": detail.projectId, "status": detail.status,
            "messages": [{"role": row.role, "createdAt": row.createdAt, "content": row.content[:2000],
                          **({"from": row.origin.kind} if row.origin else {})}
                         for row in spoken[-max(1, min(limit, 30)):]]}


def send(chats, from_chat: str, request: ChatRelayRequest) -> ChatRelayResult:
    """Deliver one message from ``from_chat`` to ``request.to``, as a colleague's request."""

    if request.to == from_chat:
        raise HubFailure(422, "CHAT_RELAY_SELF", "A conversation cannot send a message to itself.")
    sender = chats.get(from_chat)
    # The message that started the sender's current turn says how deep in an unattended chain it is.
    asked = next((row for row in reversed(sender.messages) if row.role == "user" and row.interjection is None), None)
    depth = asked.origin.depth + 1 if asked is not None and asked.origin is not None and asked.origin.kind == "chat" else 1
    if depth > RELAY_DEPTH:
        raise HubFailure(409, "CHAT_RELAY_DEPTH", f"This would be message {depth} in a chain between conversations "
                         "that no person started. Stop here and tell the architect what is needed instead.")
    receiver = chats.get(request.to)
    origin = ChatMessageOrigin(kind="chat", chatId=sender.id, title=sender.title[:200], depth=depth)
    running = receiver.status == "running"
    detail = chats.post(request.to, ChatPostRequest(projectId=receiver.projectId, content=request.text), origin=origin)
    message = next((row for row in reversed(detail.messages) if row.role == "user" and row.origin == origin), None)
    return ChatRelayResult(chatId=detail.id, messageId=message.id if message else None, started=not running)
