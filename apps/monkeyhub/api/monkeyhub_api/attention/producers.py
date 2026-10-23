"""Producers: facts the Hub already has, written as attention cards.

Each producer is a pure function of what the Hub read (a project's Worktree
Graph, its chats, its operation journal, the update status) and returns that
source's whole set of cards under one prefix, for ``AttentionStore.reconcile``:
a card it no longer offers is superseded. Nothing here reads a project, a chat
or the clock by itself, and no producer answers for a person.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import re
from typing import Any

from ..models import ChatDetail
from ..runtime.models import OperationRecord
from ..updates.models import UpdateStatus
from .models import AttentionDraft, AttentionMedia, AttentionOption, AttentionSource, CommandRef

# Commands that only open something, named as the command tree names them.
OPEN_CONVERSATION = "chat.open_conversation"
COMPARE = "candidate.compare"

RESULTS = "result:"
PERMISSIONS = "perm:"
SUGGESTIONS = "sug:"
QUESTIONS = "dlq:"
OPERATIONS = "op:"
UPDATES = "update:"

# A result nobody has touched for this long is offered for archiving instead.
STALE_AFTER = timedelta(days=7)
LETTERS = "ABCDEF"


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _option(option_id: str, label: str, consequence: str, command: str, *, undoable: bool = False,
            **args: Any) -> AttentionOption:
    return AttentionOption(id=option_id, label=_clip(label, 20), consequence=_clip(consequence, 60),
                           action=CommandRef(commandId=command, args=args), undoable=undoable)


def _keep() -> AttentionOption:
    return _option("keep", "留着", "结果留在原处，这张卡收起", "attention.acknowledge")


def _archive(runs: list[str], label: str) -> AttentionOption:
    return _option("archive", label, "归档，以后可以找回", "candidate.review.archive", undoable=True, runIds=runs)


# Results ----------------------------------------------------------------------


def results(graph: Mapping[str, Any], *, now: datetime) -> list[AttentionDraft]:
    """Retained results nobody has decided on, from a project's Worktree Graph (GET /api/worktrees).

    Two or more results of one Study are one A/B card. A result on its own asks
    whether to switch to it, or to combine it when the combine rule accepts it
    with the head. Results untouched for a week are one card offering to archive
    them. An admitted, rejected, archived or superseded result asks nothing.
    """

    head = (graph.get("head") or {}).get("runId")
    open_lines = [line for line in graph.get("lines", ())
                  if line.get("kind") == "result" and line.get("runId") and line.get("status") == "ready"
                  and line.get("admission") == "none" and line.get("relation") != "superseded"
                  and line.get("review", "unreviewed") == "unreviewed"]
    stale = [line for line in open_lines
             if line.get("updatedAt") and datetime.fromisoformat(line["updatedAt"]) < now - STALE_AFTER]
    fresh = [line for line in open_lines if line not in stale]
    studies: dict[str, list[dict]] = {}
    for line in fresh:
        if line.get("studyId"):
            studies.setdefault(line["studyId"], []).append(line)
    drafts = [_compare(study, lines) for study, lines in studies.items() if len(lines) >= 2]
    grouped = {line["runId"] for lines in studies.values() if len(lines) >= 2 for line in lines}
    drafts += [_single(line, head) for line in fresh if line["runId"] not in grouped]
    if stale:
        runs = sorted(line["runId"] for line in stale)
        drafts.append(AttentionDraft(
            channel="decide", kind="choice", title=f"{len(runs)} 个结果放了一周多没人动，要归档吗",
            source=AttentionSource(runIds=runs),
            dedupeKey=RESULTS + "stale:" + hashlib.sha256("\n".join(runs).encode()).hexdigest()[:16],
            options=[_archive(runs, f"归档这 {len(runs)} 个"), _keep()]))
    return drafts


def _compare(study: str, lines: list[dict]) -> AttentionDraft:
    lines = sorted(lines, key=lambda line: line.get("updatedAt") or "")[:len(LETTERS)]
    runs = [line["runId"] for line in lines]
    named = zip(LETTERS, lines)
    return AttentionDraft(
        channel="decide", kind="ab", title=f"这 {len(lines)} 个结果，留哪个",
        summary=_clip("；".join(f"{letter}：{line.get('label') or line.get('detail') or line['runId']}"
                                for letter, line in zip(LETTERS, lines)), 400),
        source=AttentionSource(studyId=study, runIds=runs), dedupeKey=f"{RESULTS}study:{study}",
        media=[AttentionMedia(kind="model_view", runId=run) for run in runs],
        options=[*(_option(letter.lower(), f"采用 {letter}", f"当前方案换成 {letter}，原来那一版留在修改历史里",
                           "design.go_to_step", undoable=True, runId=line["runId"], branchId=line.get("branchId"))
                   for letter, line in named),
                 _archive(runs, "都不要")],
        opens=[CommandRef(commandId=COMPARE, args={"runIds": runs})])


def _single(line: dict, head: str | None) -> AttentionDraft:
    run, label = line["runId"], line.get("label") or "这个结果"
    combine = line.get("reconcile") == "can-combine" and head is not None and head != run
    summary = {"can-combine": "它和当前方案改的地方不冲突，可以合成一个。",
               "conflict": "它和当前方案改到了同一处，只能二选一。"}.get(line.get("reconcile"))
    options = [_option("combine", "并入当前", "和当前方案合成一个新结果，原来的都留着", "candidate.combine",
                       runIds=[head, run])] if combine else []
    options += [_option("switch", "换成它", "当前方案换成它，原来那一版留在修改历史里", "design.go_to_step",
                        undoable=True, runId=run, branchId=line.get("branchId")),
                _keep(), _archive([run], "归档")]
    return AttentionDraft(
        channel="decide", kind="choice", title=_clip(f"新结果「{label}」，要换上吗", 160),
        summary=_clip(" ".join(part for part in (summary, line.get("detail")) if part), 400) or None,
        source=AttentionSource(runIds=[run]), dedupeKey=f"{RESULTS}run:{run}",
        media=[AttentionMedia(kind="model_view", runId=run)],
        options=options, opens=[CommandRef(commandId=COMPARE, args={"runIds": [head, run]})] if head and head != run else [])


# Conversations ----------------------------------------------------------------

_PERMISSION_WORDS = {
    "allow_once": ("允许这一次", "只放行这一次请求"),
    "allow_always": ("一直允许", "以后同类请求不再问你"),
    "reject_once": ("拒绝", "这一次不放行，对话会接着往下走"),
    "reject_always": ("一直拒绝", "以后同类请求都不放行"),
}


def permissions(sessions: Iterable[ChatDetail]) -> list[AttentionDraft]:
    """Each permission a conversation waits on, with the conversation's own answers."""

    drafts = []
    for session in sessions:
        for message in session.messages:
            request = message.permission
            if request is None:
                continue
            options = []
            for index, choice in enumerate(request.options[:len(LETTERS)]):
                label, consequence = _PERMISSION_WORDS.get(choice.kind, (choice.name, f"按「{choice.name}」答复"))
                options.append(_option(f"o{index}", label, consequence, "chat.answer_permission",
                                       sessionId=session.id, permissionId=request.id, optionId=choice.optionId))
            drafts.append(AttentionDraft(
                channel="decide", kind="choice", title=_clip(f"「{session.title}」请求：{request.title}", 160),
                source=AttentionSource(chatSessionId=session.id, chatMessageId=message.id, permissionId=request.id),
                dedupeKey=f"{PERMISSIONS}{session.id}:{request.id}", options=options,
                opens=[CommandRef(commandId=OPEN_CONVERSATION, args={"sessionId": session.id})]))
    return drafts


def suggestions(sessions: Iterable[ChatDetail]) -> list[AttentionDraft]:
    """The suggestion an idle conversation ended its turn with, while choosing it would still continue it."""

    drafts = []
    for session in sessions:
        if session.status != "idle":
            continue
        # The same rule the chat applies when a suggestion is chosen (ChatStore.post).
        user = next((row for row in reversed(session.messages) if row.role == "user"), None)
        turn = (user.sourceTurnId or user.id) if user else None
        card = next((row for row in reversed(session.messages)
                     if row.role == "assistant" and row.suggestion is not None and row.sourceTurnId == turn), None)
        if card is None or card.status != "complete" or card.presentationRevision is None:
            continue
        if any(row.role == "user" and row.suggestionSelection and row.suggestionSelection.messageId == card.id
               for row in session.messages):
            continue
        suggestion = card.suggestion
        drafts.append(AttentionDraft(
            channel="guess", kind="suggestion", title=_clip(suggestion.title, 160),
            summary=_clip(suggestion.outcome, 400),
            source=AttentionSource(chatSessionId=session.id, chatMessageId=card.id),
            dedupeKey=f"{SUGGESTIONS}{card.id}",
            options=[_option("go", "就这样做", f"在原对话里接着做：{suggestion.title}", "attention.answer_guess",
                             sessionId=session.id, messageId=card.id, revision=card.presentationRevision)],
            opens=[CommandRef(commandId=OPEN_CONVERSATION, args={"sessionId": session.id})]))
    return drafts


@dataclass(frozen=True)
class AskedQuestion:
    """A design-loop turn that ended by asking the architect (design_ask), for one user message."""

    session_id: str
    message_id: str
    question: str
    reason: str
    refs: tuple[str, ...] = field(default_factory=tuple)


def questions(asked: Iterable[AskedQuestion]) -> list[AttentionDraft]:
    """Each question the design loop put to the architect; the answer goes back to its conversation."""

    return [AttentionDraft(
        channel="decide", kind="choice", title=_clip(row.question, 160), summary=_clip(row.reason, 400),
        source=AttentionSource(chatSessionId=row.session_id, chatMessageId=row.message_id),
        dedupeKey=f"{QUESTIONS}{row.session_id}:{row.message_id}",
        # The content is what the person writes on the card.
        options=[_option("answer", "回答", "把你的回答发回原对话，它接着改", "chat.send_message", sessionId=row.session_id)],
        opens=[CommandRef(commandId=OPEN_CONVERSATION, args={"sessionId": row.session_id})]) for row in asked]


# The Hub ----------------------------------------------------------------------

_KINDS = (
    (re.compile(r"^POST /api/revisions$"), "改模型"),
    (re.compile(r"^POST /api/proposals/[^/]+/candidate$|^candidate$"), "生成方案"),
    (re.compile(r"^POST /api/candidates/combine$"), "合并结果"),
    (re.compile(r"^POST /api/candidates/[^/]+/accept$"), "接受阶段"),
    (re.compile(r"^PUT /api/working-draft$"), "切换当前方案"),
    (re.compile(r"^POST /api/capabilities/[^/]+/run$"), "运行建模功能"),
    (re.compile(r"^POST /api/drawings"), "出图"),
)


def _what(kind: str) -> str:
    return next((words for pattern, words in _KINDS if pattern.search(kind)), "后台操作")


def operations(records: Iterable[OperationRecord]) -> list[AttentionDraft]:
    """Each operation that failed, went stale or cannot be recovered, until a person reads it.

    One the Hub can still recover asks nothing: reading retained results
    resolves it. A status the person already read is not asked about again.
    """

    drafts = []
    for record in records:
        if record.acknowledgedAt is not None:
            continue
        what = _what(record.kind)
        title = {"failed": f"{what}没有完成", "stale": f"{what}没有执行：它依据的版本已经变了"}.get(record.status)
        if record.status == "needs_recovery" and not record.recoverable:
            title = f"{what}的结果找不回来了"
        if title is None:
            continue
        drafts.append(AttentionDraft(
            channel="notice", kind="info", title=title, summary=_clip(record.reason, 400),
            source=AttentionSource(operationId=record.operationId, jobId=record.jobId,
                                   runIds=[record.candidateId] if record.candidateId else []),
            dedupeKey=f"{OPERATIONS}{record.operationId}:{record.status}",
            options=[_option("ok", "知道了", "收起这条提示，操作记录不变", "attention.acknowledge",
                             operationId=record.operationId)],
            opens=[CommandRef(commandId=OPEN_CONVERSATION, args={"sessionId": record.sessionId})]
            if record.sessionId else []))
    return drafts


def updates(status: UpdateStatus) -> list[AttentionDraft]:
    """A prepared update that can be applied now."""

    if status.state != "ready" or not status.canApply or status.prepared is None:
        return []
    version = status.prepared.releaseVersion or status.prepared.targetVersion
    return [AttentionDraft(
        channel="decide", kind="choice", title=f"新版本 {version} 已经准备好",
        summary=_clip(status.message, 400), dedupeKey=f"{UPDATES}{status.prepared.targetVersion}",
        options=[_option("restart", "现在重启更新", "Hub 关掉，换成新版本再打开", "hub.update_apply")])]
