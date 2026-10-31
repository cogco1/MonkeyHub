"""Producers: facts the Hub already has, written as attention cards.

Each producer is a pure function of what the Hub read (a project's Worktree
Graph, its chats, its operation journal, the update status) and returns that
source's whole set of cards under one prefix, for ``AttentionStore.reconcile``:
a card it no longer offers is superseded. Nothing here reads a project, a chat
or the clock by itself, and no producer answers for a person. Every sentence
is written in English and Chinese, as the command tree's labels are.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import hashlib
import re
from typing import Any

from ..models import ChatDetail
from ..runtime.models import OperationRecord, RepairRecord
from ..updates.models import UpdateStatus
from .models import (AttentionDraft, AttentionMedia, AttentionOption, AttentionSource, CommandRef, EvidenceRow,
                     ModelSourceRef)

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


@dataclass(frozen=True)
class RunFacts:
    """What the Studio retained about one run, for a decision card's picture and its rows.

    ``model`` is the run's exact model (a ``ModelSourceDto``); ``objects`` the
    exported objects its inspection records name; ``height`` and ``footprint``
    the extent of their boxes together, in metres and square metres; ``checks``
    its relation checks as held, violated, unchecked. Anything the records do
    not give is None and its row says nothing.
    """

    model: Mapping[str, str] | None = None
    objects: int | None = None
    height: float | None = None
    footprint: float | None = None
    checks: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class PairFacts:
    """The Studio's object comparison of a result (B) against another run (A): counts by what happened, changed
    meaning moved or resized (``hub.pair_facts``)."""

    changed: int
    added: int
    removed: int
    unchanged: int


def result_pairs(graph: Mapping[str, Any], *, now: datetime, handled: Iterable[str] = ()) -> list[tuple[str, str]]:
    """The (B, A) runs whose facts the result cards show: each lone result against the current design, and the
    two results of a Study shown side by side. What the Hub reads before ``results``."""

    compared, singles, _ = _open_results(graph, now, frozenset(handled))
    head = (graph.get("head") or {}).get("runId")
    pairs = [(line["runId"], head) for line in singles if head and head != line["runId"]]
    for lines in compared.values():
        shown = _shown(lines)
        if len(shown) == 2:
            pairs.append((shown[1]["runId"], shown[0]["runId"]))
    return pairs


def _open_results(graph: Mapping[str, Any], now: datetime, handled: frozenset[str]):
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
    grouped = {line["runId"] for lines in compared.values() for line in lines}
    singles = [line for line in fresh if line["runId"] not in grouped | handled]
    return compared, singles, stale


def _shown(lines: list[dict]) -> list[dict]:
    # The newest ones side by side, oldest first.
    return sorted(sorted(lines, key=lambda line: line.get("updatedAt") or "", reverse=True)[:len(LETTERS)],
                  key=lambda line: line.get("updatedAt") or "")


def _number(value: float) -> str:
    text = f"{value:.0f}" if abs(value) >= 1000 else f"{value:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _from_to(before: float, after: float, unit: str) -> str:
    """One value with its unit, or both when they differ: each number carries the unit."""
    if _number(before) == _number(after):
        return f"{_number(after)} {unit}"
    return f"{_number(before)} {unit} → {_number(after)} {unit}"


def evidence(a: RunFacts | None, b: RunFacts | None, pair: PairFacts | None) -> list[EvidenceRow]:
    """The three rows a decision card weighs B against A by: how many objects, how big, what the checks say.

    A row whose facts the records do not give on both sides is left out.
    """

    rows = []
    if a and b and a.objects is not None and b.objects is not None:
        counts = f"{a.objects} → {b.objects}" if a.objects != b.objects else f"{a.objects}"
        if pair and (pair.changed or pair.added or pair.removed):
            parts = [(pair.changed, "changed", "改了"), (pair.added, "added", "加了"), (pair.removed, "removed", "去掉")]
            en = ", ".join(f"{count} {word}" for count, word, _ in parts if count)
            zh = "、".join(f"{word} {count} 个" for count, _, word in parts if count)
            text, text_zh = f"{counts} objects: {en}", f"{counts} 个构件，{zh}"
        elif pair:
            text, text_zh = f"{counts} objects, none changed", f"{counts} 个构件，没有改动"
        else:
            text, text_zh = f"{counts} objects", f"{counts} 个构件"
        rows.append(EvidenceRow(key="quantity", label="Quantity", labelZh="数量", text=text, textZh=text_zh,
                                status="ok"))
    if a and b and a.height is not None and b.height is not None and a.footprint is not None and b.footprint is not None:
        height, area = _from_to(a.height, b.height, "m"), _from_to(a.footprint, b.footprint, "m²")
        same = _number(a.height) == _number(b.height) and _number(a.footprint) == _number(b.footprint)
        tail = (" — the same", "，两版一样") if same else ("", "")
        rows.append(EvidenceRow(key="geometry", label="Geometry", labelZh="几何",
                                text=f"Height {height}, footprint {area}{tail[0]}",
                                textZh=f"总高 {height}，占地 {area}{tail[1]}", status="ok"))
    if a and b and a.checks is not None and b.checks is not None:
        (held_a, violated_a, unchecked_a), (held_b, violated_b, unchecked_b) = a.checks, b.checks
        if not any(a.checks) and not any(b.checks):
            text, text_zh, status = "Neither declares relations to check", "两版都没有要检查的关系", "unknown"
        elif a.checks == b.checks:
            text = f"Both: {held_b} relation checks hold, {violated_b} violated"
            text_zh = f"两版都是 {held_b} 项关系检查成立、{violated_b} 项违反"
            if unchecked_b:
                text, text_zh = f"{text}, {unchecked_b} unchecked", f"{text_zh}、{unchecked_b} 项没查"
            status = "warn" if violated_b else "ok"
        else:
            text = f"Holds {held_a} → {held_b}, violated {violated_a} → {violated_b}"
            text_zh = f"成立 {held_a} → {held_b} 项，违反 {violated_a} → {violated_b} 项"
            if unchecked_a or unchecked_b:
                text, text_zh = f"{text}, unchecked {unchecked_a} → {unchecked_b}", f"{text_zh}，没查 {unchecked_a} → {unchecked_b} 项"
            status = "warn" if violated_b > violated_a else "ok"
        rows.append(EvidenceRow(key="checks", label="Checks", labelZh="检查", text=text, textZh=text_zh, status=status))
    return rows


def _picture(run: str, facts: RunFacts | None, option: str | None, caption: Words) -> AttentionMedia:
    model = (ModelSourceRef(**{key: facts.model.get(key) for key in ("runId", "stateDigest", "assetSha256")})
             if facts and facts.model else None)
    return AttentionMedia(kind="model_view", runId=run, modelSource=model, optionId=option,
                          caption=_clip(caption[0], 160), captionZh=_clip(caption[1], 160))


def results(graph: Mapping[str, Any], *, now: datetime, handled: Iterable[str] = (),
            facts: Mapping[str, RunFacts] | None = None,
            pairs: Mapping[tuple[str, str], PairFacts] | None = None) -> list[AttentionDraft]:
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

    With what the Hub read for ``result_pairs`` (``facts`` by run, ``pairs``
    by (B, A)), a lone result is shown as B beside A, the current design, and
    two results of a Study as A and B: each picture is its run's exact model and
    answers the card for it, and the rows weigh B against A.
    """

    compared, singles, stale = _open_results(graph, now, frozenset(handled))
    head = dict(graph.get("head") or {})
    facts = dict(facts or {})
    if head.get("runId") and head.get("modelSource") and not (facts.get(head["runId"]) or RunFacts()).model:
        # The current design's model is the graph's own word for it.
        facts[head["runId"]] = replace(facts.get(head["runId"]) or RunFacts(), model=head["modelSource"])
    pairs = pairs or {}
    drafts = [_compare(study, lines, facts, pairs) for study, lines in compared.items()]
    drafts += [_single(line, head, facts, pairs) for line in singles]
    if stale:
        runs = sorted(line["runId"] for line in stale)
        drafts.append(_card(
            (f"{len(runs)} results have sat untouched for over a week. Archive them?",
             f"{len(runs)} 个结果放了一周多没人动，要归档吗"),
            channel="decide", kind="choice", source=AttentionSource(runIds=runs),
            dedupeKey=RESULTS + "stale:" + hashlib.sha256("\n".join(runs).encode()).hexdigest()[:16],
            options=[_archive(runs, (f"Archive these {len(runs)}", f"归档这 {len(runs)} 个")), _keep()]))
    return drafts


def _compare(study: str, lines: list[dict], facts: Mapping[str, RunFacts],
             pairs: Mapping[tuple[str, str], PairFacts]) -> AttentionDraft:
    every = [line["runId"] for line in lines]
    # The newest ones side by side; "neither" archives every one of them.
    shown = _shown(lines)
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
        media=[_picture(line["runId"], facts.get(line["runId"]), letter.lower(),
                        (f"{letter} · {line.get('label') or 'Result'}", f"{letter} · {line.get('label') or '结果'}"))
               for letter, line in zip(LETTERS, shown)],
        # Two side by side are weighed row by row, B against A.
        evidence=evidence(facts.get(runs[0]), facts.get(runs[1]), pairs.get((runs[1], runs[0]))) if len(runs) == 2 else [],
        options=[*(_option(letter.lower(), (f"Adopt {letter}", f"采用 {letter}"),
                           (f"The current design becomes {letter}; the one before stays in the history",
                            f"当前方案换成 {letter}，原来那一版留在修改历史里"),
                           "design.go_to_step.continue", undoable=True, runId=line["runId"], branchId=line.get("branchId"))
                   for letter, line in zip(LETTERS, shown)),
                 _archive(every, ("Neither", "都不要"))],
        opens=[CommandRef(commandId=COMPARE, args={"runIds": runs})])


# The Studio's sentences about a result line (status.py), in the words a Chinese reader reads.
_DETAIL_ZH = {"This result already continues the current head.": "它是接着当前方案改出来的。"}


def _step_name(label: str | None) -> str | None:
    """A step as the history names it: "V16" of "V16 - floor junction with …"; another label clipped."""

    if not label:
        return None
    short = re.match(r"^\s*(V\d+)(?![\w.])", label)
    return short.group(1) if short else _clip(label, 24)


def _single(line: dict, current: Mapping[str, Any], facts: Mapping[str, RunFacts],
            pairs: Mapping[tuple[str, str], PairFacts]) -> AttentionDraft:
    run = line["runId"]
    head = current.get("runId")
    label = line.get("label")
    combine = line.get("reconcile") == "can-combine" and head is not None and head != run
    summary = {"can-combine": ("It does not clash with the current design; the two can be combined.",
                               "它和当前方案改的地方不冲突，可以合成一个。"),
               "conflict": ("It changes the same part as the current design; only one can stay.",
                            "它和当前方案改到了同一处，只能二选一。")}.get(line.get("reconcile"))
    detail = line.get("detail")
    if detail:
        zh = _DETAIL_ZH.get(detail, detail)
        summary = (f"{summary[0]} {detail}", f"{summary[1]}{zh}") if summary else (detail, zh)
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
    # A, the current design, beside B, this result: choosing a picture keeps the one or switches to the other.
    named = _step_name(current.get("label"))
    after = _picture(run, facts.get(run), "switch", (f"B · {label}" if label else "B · New result",
                                                     f"B · 新结果「{label}」" if label else "B · 新结果"))
    media, rows = [after], []
    if head and head != run:
        media = [_picture(head, facts.get(head), "keep", (f"A · Current design ({named})" if named else "A · Current design",
                                                          f"A · 当前方案（{named}）" if named else "A · 当前方案")), after]
        rows = evidence(facts.get(head), facts.get(run), pairs.get((run, head)))
    return _card(
        (f"New result “{label}”: switch to it?" if label else "A new result: switch to it?",
         f"新结果「{label}」，要换上吗" if label else "有一个新结果，要换上吗"),
        summary, channel="decide", kind="choice", source=AttentionSource(runIds=[run]),
        dedupeKey=f"{RESULTS}run:{run}", media=media, evidence=rows, options=options,
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
    if "is not a valid shape" in (record.reason or ""):
        return "这个形状在当前视角下画不出体块：换到轴测视图（顶部「更多」里）再画一次。"
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
