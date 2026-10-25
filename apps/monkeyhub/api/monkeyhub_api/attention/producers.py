"""Producers: facts the Hub already has, written as attention cards.

Each producer is a pure function of what the Hub read (a project's Worktree
Graph, its chats, its operation journal, the update status) and returns that
source's whole set of cards under one prefix, for ``AttentionStore.reconcile``:
a card it no longer offers is superseded. Nothing here reads a project, a chat
or the clock by itself, and no producer answers for a person. Every sentence
is written in English and Chinese, as the command tree's labels are.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import re
from typing import Any

from ..models import ChatDetail
from ..runtime.models import OperationRecord, RepairRecord
from ..updates.models import UpdateStatus
from .models import AttentionDraft, AttentionMedia, AttentionOption, AttentionSource, CommandRef

# Commands that only open something, named as the command tree names them.
OPEN_CONVERSATION = "chat.open_conversation"
COMPARE = "candidate.compare.decide"

RESULTS = "result:"
PERMISSIONS = "perm:"
SUGGESTIONS = "sug:"
QUESTIONS = "dlq:"
OPERATIONS = "op:"
UPDATES = "update:"
REPAIRS = "repair:"

# A result nobody has touched for this long is offered for archiving instead.
STALE_AFTER = timedelta(days=7)
LETTERS = "ABCDEF"

Words = tuple[str, str]  # English, Chinese


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _option(option_id: str, label: Words, consequence: Words, command: str, *, undoable: bool = False,
            **args: Any) -> AttentionOption:
    return AttentionOption(id=option_id, label=_clip(label[0], 40), labelZh=_clip(label[1], 20),
                           consequence=_clip(consequence[0], 120), consequenceZh=_clip(consequence[1], 60),
                           action=CommandRef(commandId=command, args=args), undoable=undoable)


def _card(title: Words, summary: Words | None = None, **fields: Any) -> AttentionDraft:
    return AttentionDraft(title=_clip(title[0], 160), titleZh=_clip(title[1], 160),
                          summary=_clip(summary[0], 400) if summary else None,
                          summaryZh=_clip(summary[1], 400) if summary else None, **fields)


def _keep() -> AttentionOption:
    return _option("keep", ("Keep", "留着"), ("The result stays where it is; this card is put away",
                                              "结果留在原处，这张卡收起"), "attention.queue.acknowledge")


def _archive(runs: list[str], label: Words) -> AttentionOption:
    return _option("archive", label, ("Archived; it can be found again", "归档，以后可以找回"),
                   "candidate.review.archive", undoable=True, runIds=runs)


# Results ----------------------------------------------------------------------


def results(graph: Mapping[str, Any], *, now: datetime, handled: Iterable[str] = ()) -> list[AttentionDraft]:
    """Retained results nobody has decided on, from a project's Worktree Graph (GET /api/worktrees).

    Only an admitted Candidate has a Study and can be archived (a run nobody
    admitted is not in the candidate pool). Two or more Candidates of one Study
    are one A/B card. Any other result on its own asks whether to switch to it,
    or to combine it when the combine rule accepts it with the head, and a
    Candidate also offers to archive it. Candidates untouched for a week are one
    card offering to archive them; a run nobody admitted that old asks nothing
    and is left to the project's cleaning. A rejected, superseded or archived
    result asks nothing, nor does one a person already answered for on a card
    (``handled``: the results an answered card listed), unless it is compared
    with a new one.
    """

    handled = frozenset(handled)
    head = (graph.get("head") or {}).get("runId")
    open_lines = [line for line in graph.get("lines", ())
                  if line.get("kind") == "result" and line.get("runId") and line.get("status") == "ready"
                  and line.get("admission") in {"admitted", "none"} and line.get("relation") != "superseded"
                  and line.get("review", "unreviewed") == "unreviewed"]
    old = [line for line in open_lines
           if line.get("updatedAt") and datetime.fromisoformat(line["updatedAt"]) < now - STALE_AFTER]
    stale = [line for line in old if line["admission"] == "admitted"]
    fresh = [line for line in open_lines if line not in old]
    studies: dict[str, list[dict]] = {}
    for line in fresh:
        if line.get("studyId") and line["admission"] == "admitted":
            studies.setdefault(line["studyId"], []).append(line)
    compared = {study: lines for study, lines in studies.items()
                if len(lines) >= 2 and not {line["runId"] for line in lines} <= handled}
    drafts = [_compare(study, lines) for study, lines in compared.items()]
    grouped = {line["runId"] for lines in compared.values() for line in lines}
    drafts += [_single(line, head) for line in fresh if line["runId"] not in grouped | handled]
    if stale:
        runs = sorted(line["runId"] for line in stale)
        drafts.append(_card(
            (f"{len(runs)} results have sat untouched for over a week. Archive them?",
             f"{len(runs)} 个结果放了一周多没人动，要归档吗"),
            channel="decide", kind="choice", source=AttentionSource(runIds=runs),
            dedupeKey=RESULTS + "stale:" + hashlib.sha256("\n".join(runs).encode()).hexdigest()[:16],
            options=[_archive(runs, (f"Archive these {len(runs)}", f"归档这 {len(runs)} 个")), _keep()]))
    return drafts


def _compare(study: str, lines: list[dict]) -> AttentionDraft:
    every = [line["runId"] for line in lines]
    # The newest ones side by side; "neither" archives every one of them.
    shown = sorted(sorted(lines, key=lambda line: line.get("updatedAt") or "", reverse=True)[:len(LETTERS)],
                   key=lambda line: line.get("updatedAt") or "")
    runs = [line["runId"] for line in shown]
    newest = len(shown) < len(lines)
    names = "; ".join(f"{letter}: {line.get('label') or line.get('detail') or line['runId']}"
                      for letter, line in zip(LETTERS, shown))
    return _card(
        (f"The newest {len(shown)} of {len(lines)} results: which one to keep?" if newest
         else f"{len(shown)} results: which one to keep?",
         f"最新的 {len(shown)} 个结果（共 {len(lines)} 个），留哪个" if newest else f"这 {len(shown)} 个结果，留哪个"),
        (names, names.replace("; ", "；").replace(": ", "：")),
        channel="decide", kind="ab", source=AttentionSource(studyId=study, runIds=every),
        dedupeKey=f"{RESULTS}study:{study}",
        media=[AttentionMedia(kind="model_view", runId=run) for run in runs],
        options=[*(_option(letter.lower(), (f"Adopt {letter}", f"采用 {letter}"),
                           (f"The current design becomes {letter}; the one before stays in the history",
                            f"当前方案换成 {letter}，原来那一版留在修改历史里"),
                           "design.go_to_step.continue", undoable=True, runId=line["runId"], branchId=line.get("branchId"))
                   for letter, line in zip(LETTERS, shown)),
                 _archive(every, ("Neither", "都不要"))],
        opens=[CommandRef(commandId=COMPARE, args={"runIds": runs})])


def _single(line: dict, head: str | None) -> AttentionDraft:
    run = line["runId"]
    label = line.get("label")
    combine = line.get("reconcile") == "can-combine" and head is not None and head != run
    summary = {"can-combine": ("It does not clash with the current design; the two can be combined.",
                               "它和当前方案改的地方不冲突，可以合成一个。"),
               "conflict": ("It changes the same part as the current design; only one can stay.",
                            "它和当前方案改到了同一处，只能二选一。")}.get(line.get("reconcile"))
    detail = line.get("detail")
    if detail:
        summary = (f"{summary[0]} {detail}", f"{summary[1]}{detail}") if summary else (detail, detail)
    options = [_option("combine", ("Combine with current", "并入当前"),
                       ("A new result made from both; the two stay as they are", "和当前方案合成一个新结果，原来的都留着"),
                       "design.go_to_step.combine", runIds=[head, run])] if combine else []
    options += [_option("switch", ("Switch to it", "换成它"),
                        ("The current design becomes this result; the one before stays in the history",
                         "当前方案换成它，原来那一版留在修改历史里"),
                        "design.go_to_step.continue", undoable=True, runId=run, branchId=line.get("branchId")),
                _keep()]
    if line.get("admission") == "admitted":
        options.append(_archive([run], ("Archive", "归档")))
    return _card(
        (f"New result “{label}”: switch to it?" if label else "A new result: switch to it?",
         f"新结果「{label}」，要换上吗" if label else "有一个新结果，要换上吗"),
        summary, channel="decide", kind="choice", source=AttentionSource(runIds=[run]),
        dedupeKey=f"{RESULTS}run:{run}", media=[AttentionMedia(kind="model_view", runId=run)], options=options,
        opens=[CommandRef(commandId=COMPARE, args={"runIds": [head, run]})] if head and head != run else [])


# Conversations ----------------------------------------------------------------

_PERMISSION_WORDS: dict[str, tuple[Words, Words]] = {
    "allow_once": (("Allow once", "允许这一次"), ("Only this request goes ahead", "只放行这一次请求")),
    "allow_always": (("Always allow", "一直允许"), ("Requests like this are not asked about again",
                                                   "以后同类请求不再问你")),
    "reject_once": (("Reject", "拒绝"), ("This request does not go ahead; the conversation carries on",
                                       "这一次不放行，对话会接着往下走")),
    "reject_always": (("Always reject", "一直拒绝"), ("Requests like this never go ahead", "以后同类请求都不放行")),
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
                label, consequence = _PERMISSION_WORDS.get(choice.kind, (
                    (choice.name, choice.name), (f"Answer “{choice.name}”", f"按「{choice.name}」答复")))
                options.append(_option(f"o{index}", label, consequence, "attention.queue.answer_permission",
                                       sessionId=session.id, permissionId=request.id, optionId=choice.optionId))
            drafts.append(_card(
                (f"“{session.title}” asks: {request.title}", f"「{session.title}」请求：{request.title}"),
                channel="decide", kind="choice",
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
        # The suggestion is in the conversation's own words, one language.
        drafts.append(_card(
            (suggestion.title, suggestion.title), (suggestion.outcome, suggestion.outcome),
            channel="guess", kind="suggestion",
            source=AttentionSource(chatSessionId=session.id, chatMessageId=card.id),
            dedupeKey=f"{SUGGESTIONS}{card.id}",
            options=[_option("go", ("Do this", "就这样做"),
                             (f"The conversation carries on: {suggestion.title}", f"在原对话里接着做：{suggestion.title}"),
                             "attention.queue.answer_guess", sessionId=session.id, messageId=card.id,
                             revision=card.presentationRevision)],
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

    return [_card(
        (row.question, row.question), (row.reason, row.reason), channel="decide", kind="choice",
        source=AttentionSource(chatSessionId=row.session_id, chatMessageId=row.message_id),
        dedupeKey=f"{QUESTIONS}{row.session_id}:{row.message_id}",
        # The content is what the person writes on the card.
        options=[_option("answer", ("Answer", "回答"),
                         ("Your answer goes back to the conversation, which carries on",
                          "把你的回答发回原对话，它接着改"), "chat.composer.send", sessionId=row.session_id)],
        opens=[CommandRef(commandId=OPEN_CONVERSATION, args={"sessionId": row.session_id})]) for row in asked]


# The Hub ----------------------------------------------------------------------

_KINDS: tuple[tuple[re.Pattern[str], Words], ...] = (
    (re.compile(r"^POST /api/revisions$"), ("The model change", "改模型")),
    (re.compile(r"^POST /api/proposals/[^/]+/candidate$|^candidate$"), ("The design run", "生成方案")),
    (re.compile(r"^POST /api/candidates/combine$"), ("Combining results", "合并结果")),
    (re.compile(r"^POST /api/candidates/[^/]+/accept$"), ("Accepting the stage", "接受阶段")),
    (re.compile(r"^PUT /api/working-draft$"), ("Switching the current design", "切换当前方案")),
    (re.compile(r"^POST /api/capabilities/[^/]+/run$"), ("The modelling step", "运行建模功能")),
    (re.compile(r"^POST /api/drawings"), ("The drawing", "出图")),
)


def _what(kind: str) -> Words:
    return next((words for pattern, words in _KINDS if pattern.search(kind)), ("A background task", "后台操作"))


def operations(records: Iterable[OperationRecord]) -> list[AttentionDraft]:
    """Each operation that failed, went stale or cannot be recovered, until a person reads it.

    One the Hub can still recover asks nothing: reading retained results
    resolves it. A status the person already read is not asked about again.
    """

    drafts = []
    for record in records:
        if record.acknowledgedAt is not None:
            continue
        what, zh = _what(record.kind)
        title = {"failed": (f"{what} did not finish", f"{zh}没有完成"),
                 "stale": (f"{what} did not run: the version it was based on has changed",
                           f"{zh}没有执行：它依据的版本已经变了")}.get(record.status)
        if record.status == "needs_recovery" and not record.recoverable:
            title = (f"The result of {what.lower()} cannot be found again", f"{zh}的结果找不回来了")
        if title is None:
            continue
        drafts.append(_card(
            title, (record.reason, _reason_zh(record)) if record.reason else None, channel="notice", kind="info",
            source=AttentionSource(operationId=record.operationId, jobId=record.jobId,
                                   runIds=[record.candidateId] if record.candidateId else []),
            dedupeKey=f"{OPERATIONS}{record.operationId}:{record.status}",
            options=[*([_option("ai", ("Ask the AI to handle it", "交给 AI 处理"),
                                ("The project's conversation is told what failed and asked to deal with it",
                                 "把出错的情况发给项目对话，让 AI 处理"),
                                "chat.composer.send", content=_failure_words(record, what, zh))]
                       if record.status == "failed" else []),
                     _option("ok", ("Got it", "知道了"), ("The notice is put away; the record stays",
                                                         "收起这条提示，操作记录不变"),
                             "attention.queue.acknowledge", operationId=record.operationId)],
            opens=[CommandRef(commandId=OPEN_CONVERSATION, args={"sessionId": record.sessionId})]
            if record.sessionId else []))
    return drafts


_CJK = re.compile(r"[一-鿿]")


def _reason_zh(record: OperationRecord) -> str:
    """The reason in words a Chinese reader reads (S15): the runner's own English sentence named run ids and
    receipts on the attention page; it stays in English for the AI the card can hand it to."""

    if _CJK.search(record.reason or ""):
        return record.reason or ""
    if record.status == "needs_recovery":
        return "这一步没有留下能读回来的结果，项目里已有的内容不受影响；需要的话重新做一次。"
    if record.status == "stale":
        return "它依据的版本已经变了，没有执行；在现在的版本上重新做一次。"
    return "原因只有英文记录，可以点「交给 AI 处理」让 AI 去查。"


def _failure_words(record: OperationRecord, what: str, zh: str) -> str:
    # What the AI is told, in both languages: what failed, how many times it was sent, its code and its sentence.
    tries = "" if record.attempts <= 1 else f" after {record.attempts} tries / 试了 {record.attempts} 次"
    code = f" [{record.errorCode}]" if record.errorCode else ""
    return (f"{what} did not finish{tries}{code}: {record.reason or 'no reason was given'}. Please find out why and "
            f"deal with it. / {zh}没有完成{code}：{record.reason or '没有给出原因'}。请查明原因并处理。")[:1900]


def repairs(records: Iterable[RepairRecord], project: str = "") -> list[AttentionDraft]:
    """What the Hub put right by itself in a project, each once; one it would not put right again asks for a person.

    ``project`` names the project in the words a person reads: a service that
    stays down says which project's, and its card restarts it in one click.
    """

    named = f"{project}: " if project else ""
    drafts = []
    for record in records:
        if record.kind == "studio-restarted":
            drafts.append(_card(("The project service stopped and was started again", "项目服务意外停了一次，已经自动重启"),
                                (f"Nothing to do. It had stopped: {record.detail}", f"不用处理。停止时：{record.detail}"),
                                channel="notice", kind="auto_fixed", dedupeKey=f"{REPAIRS}{record.repairId}"))
        elif record.kind == "studio-start-retried":
            drafts.append(_card((f"{named}the project service did not start in time and was started again",
                                 f"{named}项目服务没按时启动，已经自动再启动一次"),
                                ("Nothing to do.", "不用处理。"),
                                channel="notice", kind="auto_fixed", dedupeKey=f"{REPAIRS}{record.repairId}"))
        elif record.kind == "studio-restart-held":
            drafts.append(_card(
                (f"{named}the project service is down after three automatic restarts",
                 f"{named}项目服务停着，自动重启了三次还是不行"),
                (f"Skills, drawings and changes in this project wait until it runs. Restart it, or ask the AI why. "
                 f"{record.detail}", f"这个项目的 skill、出图和修改都要等它起来。点「重新启动」再试一次，或交给 AI 查原因。"
                 f"{record.detail}"),
                channel="notice", kind="info", dedupeKey=f"{REPAIRS}{record.repairId}",
                options=[_option("restart", ("Restart it", "重新启动"),
                                 ("The project service is started again now", "现在再启动一次项目服务"),
                                 "attention.queue.restart_service"),
                         _option("ai", ("Ask the AI to handle it", "交给 AI 处理"),
                                 ("The project's conversation is told it keeps going down and asked to find out why",
                                  "把情况发给项目对话，让 AI 查原因"), "chat.composer.send",
                                 content=(f"The project service went down three times in ten minutes and was not "
                                          f"started again: {record.detail}. Please find out why. / 项目服务十分钟内"
                                          f"停了三次，没有再自动重启：{record.detail}。请查明原因。")[:1900])]))
    return drafts


def updates(status: UpdateStatus) -> list[AttentionDraft]:
    """A prepared update that can be applied now; it belongs to the Hub, not to a project."""

    if status.state != "ready" or not status.canApply or status.prepared is None:
        return []
    version = status.prepared.releaseVersion or status.prepared.targetVersion
    return [_card(
        (f"Version {version} is ready", f"新版本 {version} 已经准备好"),
        (status.message, status.message) if status.message else None, channel="decide", kind="choice",
        dedupeKey=f"{UPDATES}{status.prepared.targetVersion}",
        options=[_option("restart", ("Restart and update", "现在重启更新"),
                         ("The Hub closes and opens again on the new version", "Hub 关掉，换成新版本再打开"),
                         "hub.update_apply")])]
