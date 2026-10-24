"""Carrying out the answer a person chose on a card, as that person, and taking it back.

Every answer is one command of the command tree, and the executor knows a fixed
set of them (``COMMANDS``), each with whether it can be taken back; a command
that only opens something is one of ``OPENS``. ``check_card`` holds every card
to that before the store keeps it, whoever wrote the card. A project change goes through the Hub's own forward without a chat
header, so the Runtime reads it as the person's click and the operation journal
admits it like any other. Chat answers go to the chat store, Hub actions to the
Hub. The machine never answers for a person here: "all as recommended" runs only
what a person asked for in one click, and only answers that can be taken back.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..models import ChatPermissionRequest, ChatPostRequest, ChatSuggestionSelection, HubFailure
from .models import (
    CLOSED, UNSETTLED, Actor, ArgValue, AttentionDraft, AttentionItem, AttentionOption, AttentionSource, CommandRef,
    Resolution, UndoSpec,
)
from .store import AttentionStore


class StudioPort(Protocol):
    """One request to a project's Studio as the person (no chat header); the JSON answer, or HubFailure."""

    def __call__(self, project_id: str, project_dir: str, method: str, path: str,
                 body: Mapping[str, Any] | None = None, *, operation_id: str | None = None) -> dict: ...


class ChatPort(Protocol):
    def resolve_permission(self, session_id: str, permission_id: str, request: ChatPermissionRequest) -> object: ...

    def post(self, session_id: str, request: ChatPostRequest) -> object: ...


class HubPort(Protocol):
    def acknowledge_operation(self, project_id: str, project_dir: str, operation_id: str) -> object: ...

    def conversation(self, project_dir: str) -> str: ...

    def apply_update(self) -> object: ...


class AttentionResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    optionId: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=400)


class AttentionBulkRequest(BaseModel):
    """"All as recommended" over the cards a person had in view."""

    model_config = ConfigDict(extra="forbid", strict=True)
    ids: list[str] = Field(min_length=1, max_length=50)
    mode: Literal["recommended"] = "recommended"


class AttentionActionResult(BaseModel):
    item: AttentionItem
    followUp: AttentionItem | None = Field(default=None, description=(
        "A new card the action raised instead of guessing: an undo whose precondition no longer holds asks "
        "whether to go back anyway."))


class AttentionSkipped(BaseModel):
    id: str
    code: str
    detail: str


class AttentionBulkResult(BaseModel):
    bulkId: str
    items: list[AttentionItem]
    skipped: list[AttentionSkipped]


class _Moved(Exception):
    """An undo's precondition no longer holds: the project moved on since the answer."""

    def __init__(self, current: str | None):
        super().__init__(current)
        self.current = current


@dataclass
class _Run:
    """One command being carried out for one card."""

    executor: "AttentionExecutor"
    item: AttentionItem
    key: str
    precondition: Mapping[str, ArgValue] = field(default_factory=dict)
    # What the person wrote on the card with this answer.
    note: str | None = None
    # Taking an answer back rather than carrying one out.
    undoing: bool = False
    sent: int = 0

    def studio(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict:
        operation_id = None
        if method != "GET":
            # A UUID the operation journal admits: the same card, answer and attempt name the same request.
            self.sent += 1
            operation_id = str(uuid5(NAMESPACE_URL, f"{self.key}:{self.sent}"))
        return self.executor.studio(self.item.projectId, self.item.projectDir, method, path, body,
                                    operation_id=operation_id)


@dataclass(frozen=True)
class _Done:
    receipt: dict[str, ArgValue] = field(default_factory=dict)
    undo: UndoSpec | None = None


def _text(args: Mapping[str, ArgValue], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise HubFailure(422, "ATTENTION_ACTION_INVALID", f"This answer names no {name}.")
    return value


def _runs(args: Mapping[str, ArgValue]) -> list[str]:
    value = args.get("runIds")
    if not isinstance(value, list) or not value or not all(isinstance(run, str) and run for run in value):
        raise HubFailure(422, "ATTENTION_ACTION_INVALID", "This answer names no runs.")
    return value


def _continued(previous: str | None, branch: object, target: str) -> _Done:
    # A head that no entry named is not put back: returning there is another act.
    undo = None if not previous or previous == target else UndoSpec(
        action=CommandRef(commandId="design.go_to_step", args={"runId": previous, "branchId": branch}),
        precondition={"currentRunId": target})
    return _Done({"runId": target, "previousRunId": previous}, undo)


def _go_to_step(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    """Continue onto an exact run, compare-and-swapped against the position read just before (continueUndo.ts).

    Current already on the run means an earlier attempt landed before the Hub
    could record it: it is done, with the undo that attempt saw, and nothing is
    written again.
    """

    target, branch = _text(args, "runId"), args.get("branchId")
    for attempt in range(1, 4):
        position = run.studio("GET", "/api/working-draft")
        current = position.get("current") or {}
        if current.get("runId") == target:
            if run.undoing:
                return _Done()
            seen = next((step.facts for step in reversed(run.item.history)
                         if step.to == "working" and step.facts.get("targetRunId") == target), {})
            return _continued(seen.get("previousRunId"), seen.get("previousBranchId"), target)
        if "currentRunId" in run.precondition and current.get("runId") != run.precondition["currentRunId"]:
            raise _Moved(current.get("runId"))
        if position.get("localDraft"):
            raise HubFailure(409, "DESIGN_TREE_UNSYNCED_EDITS",
                             "This project has model edits that are not recorded yet; record them, then choose again.")
        if not run.undoing:
            run.executor.store.progress(run.item.id, {"targetRunId": target, "previousRunId": current.get("runId"),
                                                      "previousBranchId": current.get("branchId")})
        try:
            run.studio("PUT", "/api/working-draft", {
                "projectId": run.item.projectId, "runId": target,
                "baseRevisionSha256": position.get("revisionSha256"), "branchId": branch})
        except HubFailure as exc:
            # Another window or an autosave moved the position between the read and this write.
            if exc.error.code != "WORKING_DRAFT_STALE" or attempt == 3:
                raise
            continue
        return _continued(current.get("runId"), current.get("branchId"), target)
    raise AssertionError("unreachable")


def _review(action: Literal["archive", "restore"], reverse: str) -> Callable[[_Run, Mapping[str, ArgValue]], _Done]:
    def review(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
        runs, done = _runs(args), []
        try:
            for run_id in runs:
                run.studio("POST", "/api/candidate-reviews", {
                    "projectId": run.item.projectId, "subjectKind": "candidate", "subjectRef": run_id, "action": action})
                done.append(run_id)
        except HubFailure as exc:
            # All of them or none: put back the ones already judged before reporting.
            stuck = []
            for run_id in done:
                try:
                    run.studio("POST", "/api/candidate-reviews", {
                        "projectId": run.item.projectId, "subjectKind": "candidate", "subjectRef": run_id,
                        "action": "restore" if action == "archive" else "archive"})
                except HubFailure:
                    stuck.append(run_id)
            if stuck:
                run.executor.partly_done(run.item, stuck, archived=action == "archive")
                raise HubFailure(409, "ATTENTION_PARTLY_DONE", (
                    f"{exc.error.code}: {exc.error.detail} These could not be put back and stay "
                    f"{'archived' if action == 'archive' else 'restored'}: {', '.join(stuck)}.")[:1200]) from exc
            raise
        return _Done({"runIds": runs}, UndoSpec(action=CommandRef(commandId=reverse, args={"runIds": runs})))
    return review


def _combine(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    answer = run.studio("POST", "/api/candidates/combine", {"projectId": run.item.projectId, "candidateIds": _runs(args)})
    # A new run, never an undoable move: it gets a card of its own when it finishes.
    return _Done({"candidateId": answer.get("candidateId")})


def _answer_permission(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    option = args.get("optionId")
    run.executor.chats.resolve_permission(_text(args, "sessionId"), _text(args, "permissionId"), ChatPermissionRequest(
        projectId=run.item.projectId, optionId=option if isinstance(option, str) else None))
    return _Done({"optionId": option if isinstance(option, str) else None})


def _answer_guess(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    revision = args.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise HubFailure(422, "ATTENTION_ACTION_INVALID", "This answer names no suggestion revision.")
    run.executor.chats.post(_text(args, "sessionId"), ChatPostRequest(
        projectId=run.item.projectId,
        suggestionSelection=ChatSuggestionSelection(messageId=_text(args, "messageId"), revision=revision)))
    return _Done({"messageId": args["messageId"]})


def _send_message(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    # A fixed sentence on the card, or the answer the person wrote on it.
    content = args.get("content") if isinstance(args.get("content"), str) and args.get("content") else run.note
    if not content or not content.strip():
        raise HubFailure(422, "ATTENTION_ACTION_INVALID", "Write the answer to send first.")
    # A card that names no conversation answers into the project's own attention conversation.
    session = args.get("sessionId") if isinstance(args.get("sessionId"), str) and args.get("sessionId") \
        else run.executor.hub.conversation(run.item.projectDir)
    run.executor.chats.post(session, ChatPostRequest(projectId=run.item.projectId, content=content))
    return _Done({"sessionId": session})


def _update_apply(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    run.executor.hub.apply_update()
    return _Done()


def _acknowledge(run: _Run, args: Mapping[str, ArgValue]) -> _Done:
    operation = args.get("operationId")
    if isinstance(operation, str) and operation:
        run.executor.hub.acknowledge_operation(run.item.projectId, run.item.projectDir, operation)
        return _Done({"operationId": operation})
    # Read and put away: taking it back only brings the card back.
    return _Done(undo=UndoSpec(action=CommandRef(commandId="attention.queue.reopen")))


@dataclass(frozen=True)
class Command:
    run: Callable[[_Run, Mapping[str, ArgValue]], _Done]
    # Whether the executor itself can take it back: only these may be marked
    # undoable on a card, and only these run in "all as recommended".
    undoable: bool = False
    # The arguments a card may bind, and those it must: a card that names
    # another, or leaves one out, is refused when it is written, not clicked.
    takes: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()


# The answers a card can carry, by command id. Every one is a command of the
# command tree; the Hub refuses a card's answer that is not here.
COMMANDS: dict[str, Command] = {
    # Continue onto runId (on branchId, or the line the run was recorded on).
    "design.go_to_step": Command(_go_to_step, undoable=True, takes=("runId", "branchId"), needs=("runId",)),
    "candidate.review.archive": Command(_review("archive", "candidate.restore"), undoable=True,
                                        takes=("runIds",), needs=("runIds",)),
    "candidate.restore": Command(_review("restore", "candidate.review.archive"), undoable=True,
                                 takes=("runIds",), needs=("runIds",)),
    # A new run from runIds (the head first, then the result).
    "candidate.combine": Command(_combine, takes=("runIds",), needs=("runIds",)),
    # optionId null cancels the request, as the conversation's own Cancel does.
    "chat.answer_permission": Command(_answer_permission, takes=("sessionId", "permissionId", "optionId"),
                                      needs=("sessionId", "permissionId")),
    "attention.queue.answer_guess": Command(_answer_guess, takes=("sessionId", "messageId", "revision"),
                                      needs=("sessionId", "messageId", "revision")),
    # content, or what the person writes on the card, is sent as their message to sessionId, or
    # without one to the project's attention conversation.
    "chat.send_message": Command(_send_message, takes=("sessionId", "content")),
    "hub.update_apply": Command(_update_apply),
    # With operationId, the operation journal's acknowledge; without, the card is only put away.
    "attention.queue.acknowledge": Command(_acknowledge, takes=("operationId",)),
}
@dataclass(frozen=True)
class Opening:
    """A command a card may offer to open, and the arguments it takes and needs."""

    takes: tuple[str, ...]
    needs: tuple[str, ...]


# Commands a card may offer to open: they show something and change nothing.
OPENS: dict[str, Opening] = {
    "chat.open_conversation": Opening(takes=("sessionId",), needs=("sessionId",)),
    # Compare and decide (2-5): the runs side by side, from the same camera.
    "candidate.compare.decide": Opening(takes=("runIds",), needs=("runIds",)),
    "board.open_frame": Opening(takes=("frameId",), needs=("frameId",)),
}


def refuse_unknown(option: AttentionOption) -> None:
    if option.action.commandId not in COMMANDS:
        raise HubFailure(422, "ATTENTION_ACTION_UNKNOWN",
                         f"{option.action.commandId} is not an answer a card can carry.")


def check_card(draft: AttentionDraft) -> None:
    """Refuse a card whose buttons would do other than they say: the store's check, for every writer."""

    for option in draft.options:
        command = COMMANDS.get(option.action.commandId)
        if command is None:
            raise HubFailure(422, "ATTENTION_CARD_INVALID",
                             f"{option.action.commandId} is not an answer a card can carry.")
        if option.undoable and not command.undoable:
            raise HubFailure(422, "ATTENTION_CARD_INVALID",
                             f"{option.action.commandId} cannot be taken back, so its answer is not undoable.")
        _bound(option.action, command.takes, command.needs)
    for command in draft.opens:
        opening = OPENS.get(command.commandId)
        if opening is None:
            raise HubFailure(422, "ATTENTION_CARD_INVALID", f"{command.commandId} does more than open something.")
        _bound(command, opening.takes, opening.needs)


def _bound(command: CommandRef, takes: tuple[str, ...], needs: tuple[str, ...]) -> None:
    unknown = sorted(set(command.args) - set(takes))
    missing = [name for name in needs if command.args.get(name) in (None, "", [])]
    if unknown or missing:
        raise HubFailure(422, "ATTENTION_CARD_INVALID", f"{command.commandId} takes "
                         f"{', '.join(takes) or 'no arguments'} and needs {', '.join(needs) or 'none'}; this card "
                         f"{'names ' + ', '.join(unknown) if unknown else 'leaves out ' + ', '.join(missing)}.")


class AttentionExecutor:
    """Runs a card's answers as the person who chose them, and their undo."""

    def __init__(self, store: AttentionStore, *, studio: StudioPort, chats: ChatPort, hub: HubPort,
                 clock: Callable[[], datetime] | None = None):
        self.store, self.studio, self.chats, self.hub = store, studio, chats, hub
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._busy: set[str] = set()

    def _claim(self, item_id: str) -> None:
        with self._lock:
            if item_id in self._busy:
                raise HubFailure(409, "ATTENTION_BUSY", "This card is being handled; wait for that to finish.")
            self._busy.add(item_id)

    def _free(self, item_id: str) -> None:
        with self._lock:
            self._busy.discard(item_id)

    def resolve(self, item_id: str, option_id: str, *, note: str | None = None, by: Actor = "person",
                receipt: Mapping[str, ArgValue] | None = None) -> AttentionActionResult:
        """Carry out one answer: the card is working meanwhile, done after, and back where it was if it failed."""

        self._claim(item_id)
        try:
            item = self.store.get(item_id)
            option = next((row for row in item.options if row.id == option_id), None)
            if option is None:
                raise HubFailure(422, "ATTENTION_OPTION_UNKNOWN", "This card has no answer with that id.")
            refuse_unknown(option)
            started = self.store.begin(item_id, option_id, by=by)
            attempt = sum(row.to == "working" for row in started.history)
            run = _Run(self, started, f"att:{item_id}:{option_id}:{attempt}", note=note)
            try:
                done = COMMANDS[option.action.commandId].run(run, option.action.args)
            except HubFailure as exc:
                self.store.release(item_id, f"{exc.error.code}: {exc.error.detail}"[:400])
                raise
            except Exception:
                self.store.release(item_id, "The answer stopped before it finished; nothing more was tried.")
                raise
            resolution = Resolution(optionId=option_id, by=by, at=self._clock().isoformat(), note=note,
                                    receipt={**done.receipt, **(receipt or {})}, undo=done.undo)
            return AttentionActionResult(item=self.store.settle(item_id, resolution))
        finally:
            self._free(item_id)

    def undo(self, item_id: str, *, by: Actor = "person") -> AttentionActionResult:
        """Take back the last handling of a card.

        Put off or set aside only brings it back. An answer is reversed by its
        own undo, and only while the project still stands where the answer left
        it; otherwise nothing is forced, and a new card asks instead.
        """

        self._claim(item_id)
        try:
            item = self.store.get(item_id)
            if item.state in {"later", "dismissed"}:
                return AttentionActionResult(item=self.store.reopen(item_id))
            undo = item.resolution.undo if item.state == "done" and item.resolution else None
            if undo is None:
                raise HubFailure(409, "ATTENTION_NOT_UNDOABLE", "This card's handling cannot be taken back.")
            if undo.action.commandId == "attention.queue.reopen":
                return AttentionActionResult(item=self.store.undone(item_id, by=by))
            if undo.action.commandId not in COMMANDS:
                raise HubFailure(409, "ATTENTION_NOT_UNDOABLE", "This card's handling cannot be taken back.")
            attempt = sum(row.from_ == "done" for row in item.history) + 1
            run = _Run(self, item, f"att:{item_id}:undo:{attempt}", precondition=undo.precondition, undoing=True)
            try:
                COMMANDS[undo.action.commandId].run(run, undo.action.args)
            except _Moved as moved:
                return AttentionActionResult(item=item, followUp=self._ask_back(item, undo, moved.current))
            return AttentionActionResult(item=self.store.undone(item_id, by=by, note="Taken back."))
        finally:
            self._free(item_id)

    def _ask_back(self, item: AttentionItem, undo: UndoSpec, current: str | None) -> AttentionItem:
        previous = undo.action.args.get("runId")
        # Each undo asks on its own card: an earlier one may already be answered.
        asked = sum(card.dedupeKey.startswith(f"undo:{item.id}:") for card in self.store.items(
            item.projectDir, states=UNSETTLED | CLOSED))
        return self.store.upsert(item.projectId, item.projectDir, AttentionDraft(
            channel="decide", kind="choice",
            title="The current design has changed again. Go back to the earlier version anyway?",
            titleZh="当前方案已经又变了，还要回到之前那一版吗？",
            summary="When you undid this, the current design was no longer the version this card switched to, "
                    "so nothing was undone.",
            summaryZh="撤销时当前方案已经不是这张卡换上的那一版，所以没有直接撤。",
            source=AttentionSource(runIds=[run for run in (previous, current) if isinstance(run, str)]),
            dedupeKey=f"undo:{item.id}:{asked + 1}",
            options=[
                AttentionOption(id="back", label="Go back", labelZh="回到之前那一版",
                                consequence="The current design becomes the version before this card",
                                consequenceZh="当前方案换回这张卡处理之前的那一版", action=undo.action, undoable=True),
                AttentionOption(id="keep", label="Keep it", labelZh="不用了",
                                consequence="The current design stays as it is", consequenceZh="当前方案保持现在这样",
                                action=CommandRef(commandId="attention.queue.acknowledge")),
            ]))

    def partly_done(self, item: AttentionItem, runs: list[str], *, archived: bool) -> AttentionItem:
        """An all-or-none review that could not be put back: a card lists what was left, and offers to finish it."""

        reverse = "candidate.restore" if archived else "candidate.review.archive"
        state, state_zh = ("archived", "归档") if archived else ("restored", "找回")
        return self.store.upsert(item.projectId, item.projectDir, AttentionDraft(
            channel="decide", kind="choice",
            title=f"{len(runs)} results stayed {state} although the card was not carried out",
            titleZh=f"卡片没有做成，但这 {len(runs)} 个结果已经{state_zh}了",
            summary=", ".join(runs)[:400], source=AttentionSource(runIds=runs),
            dedupeKey=f"partly:{item.id}:{','.join(sorted(runs))}",
            options=[
                AttentionOption(id="back", label="Put them back", labelZh="放回去",
                                consequence=f"These results are {'restored' if archived else 'archived'} again",
                                consequenceZh=f"这几个结果{'找回' if archived else '归档'}，回到原样",
                                action=CommandRef(commandId=reverse, args={"runIds": runs}), undoable=True),
                AttentionOption(id="keep", label="Leave them", labelZh="就这样",
                                consequence=f"They stay {state}", consequenceZh=f"它们保持{state_zh}",
                                action=CommandRef(commandId="attention.queue.acknowledge")),
            ]))

    def bulk(self, ids: list[str]) -> AttentionBulkResult:
        """All as recommended: each card that recommends an answer that can be taken back, in order; one undo for all."""

        bulk_id = f"bulk_{uuid4().hex}"
        items, skipped = [], []
        for item_id in dict.fromkeys(ids):
            try:
                item = self.store.get(item_id)
                option = next((row for row in item.options if item.recommendation
                               and row.id == item.recommendation.optionId), None)
                if option is None:
                    raise HubFailure(409, "ATTENTION_NO_RECOMMENDATION", "This card recommends nothing.")
                # The executor's own table says what can be taken back, never the card.
                command = COMMANDS.get(option.action.commandId)
                if command is None or not command.undoable or not option.undoable:
                    raise HubFailure(409, "ATTENTION_NOT_UNDOABLE", "Its recommended answer cannot be taken back.")
                items.append(self.resolve(item_id, option.id, by="bulk", receipt={"bulkId": bulk_id}).item)
            except HubFailure as exc:
                skipped.append(AttentionSkipped(id=item_id, code=exc.error.code, detail=exc.error.detail))
        return AttentionBulkResult(bulkId=bulk_id, items=items, skipped=skipped)

    def undo_bulk(self, bulk_id: str) -> AttentionBulkResult:
        """Take back one "all as recommended", newest answer first."""

        handled = sorted((item for item in self.store.items(states=["done"])
                          if item.resolution and item.resolution.receipt.get("bulkId") == bulk_id),
                         key=lambda item: item.resolution.at, reverse=True)
        if not handled:
            raise HubFailure(404, "ATTENTION_BULK_NOT_FOUND", "Nothing handled under that id is left to take back.")
        items, skipped = [], []
        for item in handled:
            try:
                result = self.undo(item.id, by="bulk")
                if result.followUp is not None:
                    items.append(result.followUp)
                    skipped.append(AttentionSkipped(id=item.id, code="ATTENTION_UNDO_MOVED",
                                                    detail="The project moved on; a new card asks instead."))
                else:
                    items.append(result.item)
            except HubFailure as exc:
                skipped.append(AttentionSkipped(id=item.id, code=exc.error.code, detail=exc.error.detail))
        return AttentionBulkResult(bulkId=bulk_id, items=items, skipped=skipped)


def action_router(executor: AttentionExecutor) -> APIRouter:
    """POST /api/attention/{id}/resolve and /undo, and "all as recommended" with its undo."""

    routes = APIRouter()

    @routes.post("/api/attention/bulk", response_model=AttentionBulkResult)
    def attention_bulk(body: AttentionBulkRequest) -> AttentionBulkResult:
        return executor.bulk(body.ids)

    @routes.post("/api/attention/bulk/{bulk_id}/undo", response_model=AttentionBulkResult)
    def attention_bulk_undo(bulk_id: str) -> AttentionBulkResult:
        return executor.undo_bulk(bulk_id)

    @routes.post("/api/attention/{item_id}/resolve", response_model=AttentionActionResult)
    def attention_resolve(item_id: str, body: AttentionResolveRequest) -> AttentionActionResult:
        return executor.resolve(item_id, body.optionId, note=body.note)

    @routes.post("/api/attention/{item_id}/undo", response_model=AttentionActionResult)
    def attention_undo(item_id: str) -> AttentionActionResult:
        return executor.undo(item_id)

    return routes
