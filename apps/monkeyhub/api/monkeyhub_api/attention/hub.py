"""The attention queue joined to the Hub: answers reach a project as the person, and producers hear the Hub's events.

``AttentionHub`` gives the executor its ports on the Hub's own objects: a
project request goes through ``ProjectRuntimeManager.forward`` without a chat
header, so the Runtime reads it as the person's click and the operation journal
admits it; a chat answer goes to the ``ChatStore``; an update to the Hub's
updater. It follows the Hub's event stream and, a moment after a project's
events settle, reconciles every producer's cards for that project from what the
Hub has: the operation journal, the project's conversations and, while its
Studio is running, its Worktree Graph and, for the results its cards weigh,
each run's retained model, objects and checks. A design-loop turn that asks the
architect a question says so itself (``POST /api/attention/questions``): its
tools run in the conversation's own tool server, not in the Hub. A change to
the queue is published on the same stream as ``attention/changed`` under the
project's runtime id, so every page reads the queue again.
"""

from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
import json
import queue
import secrets
import threading
import time
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from ..models import ChatCreateRequest, ChatDetail, HubFailure
from ..runtime.manager import index_moved
from ..runtime.workers import project_key
from ..settings.models import UserSettingsDto
from ..settings.store import SettingsError, read_user_settings
from . import producers
from .actions import AttentionExecutor, check_card
from .change_list import (
    ChangeList, ChangeListItem, applies_without_asking, approval, change_list_card, check_against, unsure_cards,
)
from .models import AttentionItem, CommandRef, Resolution, UndoSpec
from .store import HUB_PROJECT_ID, AttentionStore, runtime_id

# How long a project's events must stay quiet before its cards are read again.
SETTLE_S = 0.5
# A suggestion only continues a recent turn: the newest conversations are enough.
RECENT_CHATS = 12
# The Hub's own events about the queue, which never prompt a read of it.
CHANGED = "attention/changed"
# How often the Hub's own cards (a prepared update) are read again.
HUB_EVERY_S = 60.0
# The runs and pairs whose facts are kept: a run's retained records never change, so each is read once.
FACTS_KEPT = 256
# Metres in each length unit a model export names.
UNIT_METRES = {"meter": 1.0, "millimeter": .001, "centimeter": .01, "inch": .0254, "foot": .3048}


def run_facts(candidate: Mapping[str, Any]) -> producers.RunFacts:
    """A run's facts from its Studio readout (``GET /api/candidates/{id}``): its one complete model, its objects,
    their extent, its relation checks."""

    run, state = candidate.get("candidateId"), candidate.get("stateDigest")
    models = [row for row in candidate.get("artifacts") or () if (row.get("modelSource") or {}).get("runId") == run
              and (state is None or row["modelSource"].get("stateDigest") == state)
              and str(row.get("fileName") or "").lower().endswith(".3dm")]
    models = list({row["modelSource"]["assetSha256"]: row for row in models}.values())
    composed = [row for row in models if row.get("representation") == "composed"]
    model = (composed if len(composed) == 1 else models if len(models) == 1 else [None])[0]
    objects = candidate.get("objects")
    height = footprint = None
    boxes = [row for row in objects or () if row.get("bbox")]
    units = {UNIT_METRES.get(row.get("lengthUnit") or "") for row in boxes}
    # Only boxes in one known unit, Z up, measure the model.
    if boxes and len(units) == 1 and None not in units and all(row.get("upAxis") in (None, "Z-up") for row in boxes):
        scale, = units
        low = [min(row["bbox"]["min"][axis] for row in boxes) for axis in range(3)]
        high = [max(row["bbox"]["max"][axis] for row in boxes) for axis in range(3)]
        height = (high[2] - low[2]) * scale
        footprint = (high[0] - low[0]) * (high[1] - low[1]) * scale * scale
    checks = candidate.get("relationChecks") or None
    return producers.RunFacts(
        model=None if model is None else model["modelSource"], objects=None if objects is None else len(objects),
        height=height, footprint=footprint,
        checks=None if checks is None else (int(checks.get("held", 0)), int(checks.get("violated", 0)),
                                            int(checks.get("unchecked", 0))))


def pair_facts(compared: Mapping[str, Any]) -> producers.PairFacts:
    """A pair's comparison (``GET /api/candidates/{B}/compare?against=A``) as a card counts it.

    Changed is an object whose inspected box moved or changed size. One whose box stayed and only its geometry
    digest differs is counted as unchanged: re-exporting the same imported shapes changes their digests (S28: 446
    such of housing's 2663 after one triangle was added), and the card would name changes nobody made.
    """

    tolerance = float(compared.get("tolerance") or 0.0)
    moved = lambda row: any(abs(a - b) > tolerance for side in ("min", "max")
                            for a, b in zip(row["before"][side], row["after"][side]))
    changed = [row for row in compared.get("objects") or () if row.get("status") == "changed"]
    shifted = sum(1 for row in changed if not row.get("before") or not row.get("after") or moved(row))
    return producers.PairFacts(changed=shifted, added=int(compared.get("added") or 0),
                               removed=int(compared.get("removed") or 0),
                               unchanged=int(compared.get("unchanged") or 0) + len(changed) - shifted)


class AttentionHub:
    """The executor's ports on the Hub, and the producers run on the Hub's events."""

    def __init__(self, store: AttentionStore, runtimes, chats, updates=None, *, hub_dir: str | None = None,
                 user_settings: Callable[[], Any] = read_user_settings,
                 clock: Callable[[], datetime] | None = None):
        self.store, self.runtimes, self.chats, self.updates = store, runtimes, chats, updates
        self._user_settings = user_settings
        self._conversation_lock = threading.Lock()
        # The Hub's own scope for cards about itself: its runtime root.
        self.hub_dir = hub_dir
        # Every card the Hub keeps runs as written, or is refused before it is kept.
        if store.check is None:
            store.check = check_card
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.executor = AttentionExecutor(store, studio=self.studio, chats=chats, hub=self, clock=self._clock,
                                          language=lambda: getattr(self._settings(), "language", None))
        store.subscribe(self._changed)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # What result cards show, by (project, run) and (project, B, A); read on the attention thread only.
        self._facts: OrderedDict[tuple, producers.RunFacts] = OrderedDict()
        self._pairs: OrderedDict[tuple, producers.PairFacts] = OrderedDict()

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

    def conversation(self, project_dir: str) -> str:
        """The project's own attention conversation, opened the first time it is needed.

        The one kept for the project is answered while it still exists and is
        not archived; otherwise a new one is opened with the person's default
        provider and model, titled in their language, and kept. Opening it sends
        nothing: the Agent runs only on the first message. The chat store
        verifies the project folder when it opens one, and names its project.
        """

        with self._conversation_lock:
            kept = self.store.conversation(project_dir)
            if kept is not None:
                try:
                    if not self.chats.get(kept).archived:
                        return kept
                except HubFailure as exc:
                    # Only a conversation that is gone is replaced; any other failure is the caller's to read.
                    if exc.error.code != "CHAT_NOT_FOUND":
                        raise
            settings = self._settings()
            available = [row.id for row in self.chats.providers() if row.available]
            provider = settings.chat_provider if settings.chat_provider in available else next(iter(available), None)
            if provider is None:
                raise HubFailure(503, "CHAT_PROVIDER_UNAVAILABLE",
                                 "No conversation provider is available to open this project's attention conversation.")
            session = self.chats.create(ChatCreateRequest(
                projectDir=project_dir, provider=provider,
                model=settings.chat_model if provider == settings.chat_provider else None,
                title="Attention" if settings.language == "en" else "关注"))
            self.store.set_conversation(session.projectId, session.projectDir, session.id)
            return session.id

    def _settings(self):
        # The account's preferences, or their defaults when they cannot be read: no card's answer waits on them.
        try:
            return self._user_settings()
        except (SettingsError, OSError):
            return UserSettingsDto()

    def restart_project(self, project_id: str, project_dir: str):
        """Start the project's Studio again, as the project page's own recover does."""

        return self.runtimes.recover(self.runtimes.open(project_id, project_dir))

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
        self.store.reconcile(project_id, project_dir, producers.REPAIRS,
                             producers.repairs(getattr(runtime, "repairs", ()), project_id))
        sessions = self._sessions(project_id, project_dir)
        self.store.reconcile(project_id, project_dir, producers.PERMISSIONS, producers.permissions(sessions))
        self.store.reconcile(project_id, project_dir, producers.SUGGESTIONS, producers.suggestions(sessions))
        self._answered(project_id, project_dir, sessions)
        graph = self._graph(runtime)
        if graph is not None:
            # A result a person answered for on a card is not asked about again on its own.
            handled = {run for card in self.store.items(project_dir, states=["done", "dismissed"])
                       if card.dedupeKey.startswith(producers.RESULTS) for run in card.source.runIds}
            now = self._clock()
            facts, pairs = self._result_facts(runtime, producers.result_pairs(graph, now=now, handled=handled))
            self.store.reconcile(project_id, project_dir, producers.RESULTS,
                                 producers.results(graph, now=now, handled=handled, facts=facts, pairs=pairs))

    def refresh_hub(self) -> None:
        """The Hub's own cards: a prepared update, queued for every project's architect alike."""

        if self.updates is not None and self.hub_dir is not None:
            self.store.reconcile(HUB_PROJECT_ID, self.hub_dir, producers.UPDATES,
                                 producers.updates(self.updates.status()))

    def ask(self, session_id: str, message_id: str, question: str, reason: str,
            refs: Iterable[str] = (), *, token: str) -> AttentionItem:
        """A design-loop turn asked the architect: one card per user message, answered back into its conversation.

        Only the conversation's own tool server asks: it holds the token the
        Hub gave that conversation's CLI (``MONKEYHUB_PRESENTATION_TOKEN``), as
        a presentation tool does.
        """

        if not token or not secrets.compare_digest(self.chats.presentation_token(session_id), token):
            raise HubFailure(403, "ATTENTION_QUESTION_UNBOUND", "Only this conversation's own tools can ask its architect.")
        session = self.chats.get(session_id)
        if not any(row.id == message_id and row.role == "user" for row in session.messages):
            raise HubFailure(409, "ATTENTION_QUESTION_UNBOUND", "The question names no message the architect sent here.")
        draft, = producers.questions([producers.AskedQuestion(session_id, message_id, question, reason, tuple(refs))])
        return self.store.upsert(session.projectId, session.projectDir, draft)

    def propose_changes(self, session_id: str, message_id: str, question: str, items: Iterable[ChangeListItem], *,
                        token: str) -> "ChangeListAnswer":
        """A design-loop turn read the architect's marks as these changes: one change_list card, and a question per
        unsure item (#300 3-3).

        Only the conversation's own tool server proposes, as it asks. The list
        is checked here, not taken on the Agent's word: against the Runtime's
        reading of the marks of the message it answers, the architect's own
        words kept with them (that message, the page's comment and its text
        marks) and the working design's catalogue. A list they do not bear out is
        refused with what is wrong, and nothing is written.

        A small list (at most ``SMALL_CHANGE`` parts), sure in every item and
        taken from the message's own words, is not asked about (decision 5): it
        is answered for the architect at once, its card says "changed N parts"
        with an undo back to the working design as it stood, and the answer
        carries the approval the Agent builds in this same turn.
        """

        if not token or not secrets.compare_digest(self.chats.presentation_token(session_id), token):
            raise HubFailure(403, "ATTENTION_QUESTION_UNBOUND", "Only this conversation's own tools can ask its architect.")
        session = self.chats.get(session_id)
        message = next((row for row in session.messages if row.id == message_id and row.role == "user"), None)
        if message is None:
            raise HubFailure(409, "ATTENTION_QUESTION_UNBOUND", "The question names no message the architect sent here.")
        if message.markup is None:
            raise HubFailure(409, "CHANGE_LIST_UNBOUND", "This message names no marks; ask with a plain question instead.")
        page, readings = self.read_marks(session.projectId, session.projectDir, message.markup)
        words = [text for text in (message.content, page.get("comment") or "",
                                   *(row.get("label") or "" for row in page.get("annotations") or ()
                                     if row.get("kind") == "text")) if text.strip()]
        try:
            change_list = ChangeList(runId=message.markup.runId, items=list(items))
        except ValueError as exc:
            raise HubFailure(422, "CHANGE_LIST_REFUSED", str(exc)[:2000]) from exc
        catalog = self._catalog(session.projectId, session.projectDir)
        problems = check_against(change_list, readings["readings"], words, catalog)
        if problems:
            raise HubFailure(422, "CHANGE_LIST_REFUSED", " ".join(problems)[:2000])
        draft = change_list_card(session_id, message_id, change_list, readings["readings"], words, question)
        quick = (applies_without_asking(change_list, readings["readings"], words, catalog)
                 and not check_against(change_list, readings["readings"], [message.content], catalog))
        if quick:
            touched = len({target for item in change_list.items for target in item.targets})
            draft = draft.model_copy(update={"title": f"Changed {touched} part{'s' if touched != 1 else ''}",
                                             "titleZh": f"已改 {touched} 个构件"})
        card = self.store.upsert(session.projectId, session.projectDir, draft)
        if not quick:
            for unsure in unsure_cards(card.id, change_list, readings["readings"]):
                self.store.upsert(session.projectId, session.projectDir, unsure)
            return ChangeListAnswer(card=card)
        # Answered for the architect: the undo goes back to the working design as it stands before the Agent builds.
        position = self.studio(session.projectId, session.projectDir, "GET", "/api/working-draft").get("current") or {}
        applied = approval(card.id, change_list, [message.content])
        self.store.begin(card.id, "run", by="machine")
        undo = None if not position.get("runId") else UndoSpec(action=CommandRef(
            commandId="design.go_to_step.continue",
            args={"runId": position["runId"], "branchId": position.get("branchId")}))
        card = self.store.settle(card.id, Resolution(optionId="run", by="machine", at=self._clock().isoformat(),
                                                     receipt={"items": [str(n) for n in applied["items"]]}, undo=undo))
        return ChangeListAnswer(card=card, applied=applied)

    def read_marks(self, project_id: str, project_dir: str, markup) -> tuple[dict, dict]:
        """A message's page ink, from its exact saved revision, and the Runtime's reading of each mark."""

        query = {"projectId": project_id, "runId": markup.runId, "assetSha256": markup.assetSha256,
                 "pageIndex": markup.pageIndex, "revisionSha256": markup.revisionSha256,
                 **({"drawingRevisionRef": markup.drawingRevisionRef} if markup.drawingRevisionRef else {})}
        page = self.studio(project_id, project_dir, "GET", f"/api/document-annotations?{urlencode(query)}")
        target = {"runId": markup.runId, "assetSha256": markup.assetSha256, "pageIndex": markup.pageIndex,
                  **({"drawingRevisionRef": markup.drawingRevisionRef} if markup.drawingRevisionRef else {})}
        readings = self.studio(project_id, project_dir, "POST", "/api/markup/resolve", {
            "projectId": project_id, "page": target, "marks": page.get("annotations") or []})
        return page, readings

    def _catalog(self, project_id: str, project_dir: str) -> set[str]:
        # The working design's components and their elements: what an extra item may name.
        state = self.studio(project_id, project_dir, "GET", "/api/state")
        rows = (state.get("catalog") or {}).get("components") or ()
        return ({row.get("componentId") for row in rows} | {element for row in rows for element in row.get("elementIds") or ()}
                | {row.get("componentId") for row in state.get("componentTree") or ()}
                | {row.get("elementId") for row in state.get("elements") or ()}) - {None}

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

    def _result_facts(self, runtime, wanted: list[tuple[str, str]]):
        """Each run's facts and each pair's comparison the result cards show, from the Studio's retained records.

        A read the Studio cannot answer now leaves that picture or row out until the project's next event.
        """

        facts: dict[str, producers.RunFacts] = {}
        pairs: dict[tuple[str, str], producers.PairFacts] = {}
        for run in dict.fromkeys(run for pair in wanted for run in pair):
            key = (runtime.project_id, run)
            if key not in self._facts:
                candidate = self._read(runtime, f"/api/candidates/{quote(run, safe='')}")
                if candidate is None or candidate.get("candidateId") != run:
                    continue
                facts[run] = run_facts(candidate)
                # Only a finished run's records are final.
                if candidate.get("status") == "succeeded":
                    self._keep(self._facts, key, facts[run])
                continue
            facts[run] = self._facts[key]
            self._facts.move_to_end(key)
        for after, before in wanted:
            key = (runtime.project_id, after, before)
            if key not in self._pairs:
                compared = self._read(runtime, f"/api/candidates/{quote(after, safe='')}/compare?"
                                               f"{urlencode({'against': before})}")
                if compared is None or compared.get("candidateId") != after or compared.get("against") != before:
                    continue
                self._keep(self._pairs, key, pair_facts(compared))
            pairs[(after, before)] = self._pairs[key]
            self._pairs.move_to_end(key)
        return facts, pairs

    @staticmethod
    def _keep(kept: OrderedDict, key: tuple, value) -> None:
        kept[key] = value
        while len(kept) > FACTS_KEPT:
            kept.popitem(last=False)

    def _read(self, runtime, path: str) -> dict | None:
        try:
            result = self.runtimes.forward(runtime, path, "GET", b"", {}, wait=False)
        except HubFailure:
            return None
        return result.json() if result.status < 400 else None

    def _graph(self, runtime) -> dict | None:
        # Only a running Studio is asked, at once: a closed or starting project keeps the cards it had until its
        # next event, and this thread keeps hearing events meanwhile.
        return self._read(runtime, "/api/worktrees")

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
        hub_due = 0.0
        with self.runtimes.events.subscribe() as (earlier, inbox):
            # What the Hub did before the queue followed it, a project it reopened at start among it, is read too.
            for event in earlier:
                if _wakes(event):
                    dirty[event["runtimeId"]] = time.monotonic() + SETTLE_S
            while not self._stop.is_set():
                if time.monotonic() >= hub_due:
                    hub_due = time.monotonic() + HUB_EVERY_S
                    try:
                        self.refresh_hub()
                    except HubFailure:
                        pass  # Read again on the next round.
                try:
                    event = inbox.get(True, SETTLE_S / 2)
                except queue.Empty:
                    event = None
                if event is not None and _wakes(event):
                    dirty[event["runtimeId"]] = time.monotonic() + SETTLE_S
                for key in [key for key, due in dirty.items() if due <= time.monotonic()]:
                    del dirty[key]
                    try:
                        self.refresh(key)
                    except HubFailure:
                        continue  # A card that cannot be saved now is written on the project's next event.


def _wakes(event: Mapping[str, Any]) -> bool:
    """Whether an event can change a project's cards: the queue's own change and an index commit that moved
    nothing (a thumbnail drawn, the index loaded again; ``index_moved``) do not."""

    if event.get("kind") == CHANGED or not event.get("runtimeId"):
        return False
    return event.get("kind") != "index/committed" or index_moved(event.get("index"))


class AttentionConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projectDir: str = Field(min_length=1)


class AttentionConversation(BaseModel):
    sessionId: str


class AttentionQuestionRequest(BaseModel):
    """What a design-loop turn asked the architect (design_ask), for the user message it answers."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sessionId: str = Field(min_length=1)
    messageId: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    refs: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(default_factory=list, max_length=20)


class AttentionChangeListRequest(BaseModel):
    """The changes a design-loop turn read the architect's marks as (design_ask with changes), for the message they
    answer."""

    model_config = ConfigDict(extra="forbid", strict=True)
    sessionId: str = Field(min_length=1)
    messageId: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2000)
    changes: list[ChangeListItem] = Field(min_length=1, max_length=30)


class ChangeListAnswer(BaseModel):
    """The list's card, and, when it was small and sure enough to apply at once, the approval to build now."""

    card: AttentionItem
    applied: dict[str, Any] | None = Field(default=None, description=(
        "cardId, items, content and quotes, as a run card would send them; null while the list waits on its card."))


def hub_router(attention: AttentionHub) -> APIRouter:
    """The project's attention conversation, and the questions a conversation's own tools put to its architect."""

    routes = APIRouter()

    @routes.post("/api/attention/conversation", response_model=AttentionConversation)
    def attention_conversation(body: AttentionConversationRequest) -> AttentionConversation:
        """Where the new shell's input and answers that name no conversation go; opened on first need."""

        return AttentionConversation(sessionId=attention.conversation(body.projectDir))

    @routes.post("/api/attention/questions", response_model=AttentionItem, status_code=201)
    def attention_question(request: Request, body: AttentionQuestionRequest) -> AttentionItem:
        """Called by the conversation's tool server when design_ask records a question."""

        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HubFailure(403, "LOCAL_PRESENTER_REQUIRED", "The conversation's tools must connect from this machine.")
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return attention.ask(body.sessionId, body.messageId, body.question, body.reason, body.refs, token=token)

    @routes.post("/api/attention/change-lists", response_model=ChangeListAnswer, status_code=201)
    def attention_change_list(request: Request, body: AttentionChangeListRequest) -> ChangeListAnswer:
        """Called by the conversation's tool server when design_ask asks with a change list."""

        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HubFailure(403, "LOCAL_PRESENTER_REQUIRED", "The conversation's tools must connect from this machine.")
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return attention.propose_changes(body.sessionId, body.messageId, body.question, body.changes, token=token)

    return routes
