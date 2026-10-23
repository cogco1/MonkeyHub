"""The attention queue joined to the Hub: answers reach a project as the person, and producers hear the Hub's events.

``AttentionHub`` gives the executor its ports on the Hub's own objects: a
project request goes through ``ProjectRuntimeManager.forward`` without a chat
header, so the Runtime reads it as the person's click and the operation journal
admits it; a chat answer goes to the ``ChatStore``; an update to the Hub's
updater. It follows the Hub's event stream and, a moment after a project's
events settle, reconciles every producer's cards for that project from what the
Hub has: the operation journal, the project's conversations and, while its
Studio is running, its Worktree Graph. A design-loop turn that asks the
architect a question says so itself (``POST /api/attention/questions``): its
tools run in the conversation's own tool server, not in the Hub. A change to
the queue is published on the same stream as ``attention/changed`` under the
project's runtime id, so every page reads the queue again.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
import json
import queue
import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..models import ChatDetail, HubFailure
from ..runtime.workers import project_key
from . import producers
from .actions import AttentionExecutor
from .models import AttentionItem
from .store import AttentionStore, runtime_id

# How long a project's events must stay quiet before its cards are read again.
SETTLE_S = 0.5
# A suggestion only continues a recent turn: the newest conversations are enough.
RECENT_CHATS = 12
# The Hub's own events about the queue, which never prompt a read of it.
CHANGED = "attention/changed"


class AttentionHub:
    """The executor's ports on the Hub, and the producers run on the Hub's events."""

    def __init__(self, store: AttentionStore, runtimes, chats, updates=None, *,
                 clock: Callable[[], datetime] | None = None):
        self.store, self.runtimes, self.chats, self.updates = store, runtimes, chats, updates
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.executor = AttentionExecutor(store, studio=self.studio, chats=chats, hub=self, clock=self._clock)
        store.subscribe(self._changed)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # The executor's ports ------------------------------------------------------

    def studio(self, project_id: str, project_dir: str, method: str, path: str,
               body: Mapping[str, Any] | None = None, *, operation_id: str | None = None) -> dict:
        runtime = self.runtimes.open(project_id, project_dir)
        headers = {"content-type": "application/json"}
        if operation_id is not None:
            headers["idempotency-key"] = operation_id
        # A person's request all the same; the journal says it was a card's answer.
        result = self.runtimes.forward(runtime, path, method,
                                       b"" if body is None else json.dumps(body).encode("utf-8"), headers,
                                       source="attention")
        answer = result.json()
        if result.status >= 400:
            raise HubFailure(result.status, str(answer.get("code") or "STUDIO_REQUEST_FAILED"),
                             str(answer.get("detail") or f"The Studio answered HTTP {result.status}."))
        return answer

    def acknowledge_operation(self, project_id: str, project_dir: str, operation_id: str):
        return self.runtimes.acknowledge(self.runtimes.open(project_id, project_dir), operation_id)

    def apply_update(self):
        if self.updates is None:
            raise HubFailure(409, "UPDATE_NOT_READY", "This Hub has no update to apply.")
        return self.updates.apply()

    # Producers -----------------------------------------------------------------

    def refresh(self, runtime_key: str) -> None:
        """Reconcile every producer's cards for one open project from what the Hub has now."""

        try:
            runtime = self.runtimes.get(runtime_key)
        except HubFailure:
            return
        project_id, project_dir = runtime.project_id, runtime.project_dir
        self.store.reconcile(project_id, project_dir, producers.OPERATIONS,
                             producers.operations(runtime.operations.records()))
        sessions = self._sessions(project_id, project_dir)
        self.store.reconcile(project_id, project_dir, producers.PERMISSIONS, producers.permissions(sessions))
        self.store.reconcile(project_id, project_dir, producers.SUGGESTIONS, producers.suggestions(sessions))
        self._answered(project_id, project_dir, sessions)
        graph = self._graph(runtime)
        if graph is not None:
            self.store.reconcile(project_id, project_dir, producers.RESULTS, producers.results(graph, now=self._clock()))

    def ask(self, session_id: str, message_id: str, question: str, reason: str,
            refs: Iterable[str] = ()) -> AttentionItem:
        """A design-loop turn asked the architect: one card per user message, answered back into its conversation."""

        session = self.chats.get(session_id)
        if not any(row.id == message_id and row.role == "user" for row in session.messages):
            raise HubFailure(409, "ATTENTION_QUESTION_UNBOUND", "The question names no message the architect sent here.")
        draft, = producers.questions([producers.AskedQuestion(session_id, message_id, question, reason, tuple(refs))])
        return self.store.upsert(session.projectId, session.projectDir, draft)

    def _answered(self, project_id: str, project_dir: str, sessions: list[ChatDetail]) -> None:
        # A question is answered once its conversation has a newer message from the architect.
        latest = {session.id: next((row.id for row in reversed(session.messages) if row.role == "user"), None)
                  for session in sessions}
        for card in self.store.items(project_dir, states=["open", "later"]):
            asked = card.source.chatSessionId
            if (card.dedupeKey.startswith(producers.QUESTIONS) and asked in latest
                    and latest[asked] != card.source.chatMessageId):
                self.store.retire(project_id, project_dir, card.dedupeKey, note="The conversation moved on.")

    def _sessions(self, project_id: str, project_dir: str) -> list[ChatDetail]:
        key = project_key(project_dir)
        summaries = [row for row in self.chats.list(project_id)
                     if row.projectId == project_id and project_key(row.projectDir) == key]
        # Every conversation that waits on a permission, and the newest ones for their suggestions.
        wanted = [row for row in summaries if row.attention == "permission"]
        wanted += [row for row in summaries[:RECENT_CHATS] if row not in wanted]
        sessions = []
        for row in wanted:
            try:
                sessions.append(self.chats.get(row.id))
            except HubFailure:
                continue  # Archived or removed between the list and the read.
        return sessions

    def _graph(self, runtime) -> dict | None:
        # Only a running Studio is asked; a closed project keeps the cards it had.
        try:
            result = self.runtimes.forward(runtime, "/api/worktrees", "GET", b"", {})
        except HubFailure:
            return None
        return result.json() if result.status < 400 else None

    # Events --------------------------------------------------------------------

    def _changed(self, project_id: str, project_dir: str, ids: tuple[str, ...]) -> None:
        self.runtimes.events.publish(event={"kind": CHANGED, "runtimeId": runtime_id(project_id, project_dir)})

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._follow, daemon=True, name="hub-attention")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _follow(self) -> None:
        dirty: dict[str, float] = {}
        with self.runtimes.events.subscribe() as (_, inbox):
            while not self._stop.is_set():
                try:
                    event = inbox.get(True, SETTLE_S / 2)
                except queue.Empty:
                    event = None
                if event is not None and event.get("kind") != CHANGED and event.get("runtimeId"):
                    dirty[event["runtimeId"]] = time.monotonic() + SETTLE_S
                for key in [key for key, due in dirty.items() if due <= time.monotonic()]:
                    del dirty[key]
                    try:
                        self.refresh(key)
                    except HubFailure:
                        continue  # A card that cannot be saved now is written on the project's next event.


class AttentionQuestionRequest(BaseModel):
    """What a design-loop turn asked the architect (design_ask), for the user message it answers."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sessionId: str = Field(min_length=1)
    messageId: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    refs: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(default_factory=list, max_length=20)


def question_router(attention: AttentionHub) -> APIRouter:
    routes = APIRouter()

    @routes.post("/api/attention/questions", response_model=AttentionItem, status_code=201)
    def attention_question(body: AttentionQuestionRequest) -> AttentionItem:
        """Called by the conversation's tool server when design_ask records a question."""

        return attention.ask(body.sessionId, body.messageId, body.question, body.reason, body.refs)

    return routes
