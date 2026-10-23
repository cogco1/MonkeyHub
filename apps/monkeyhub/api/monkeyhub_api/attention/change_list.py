""""I read your marks as these N changes": the list an agent proposes from marks on Board, before anything is built.

The architect circles part of a drawing and writes a sentence. The Runtime
reads each mark into the components it covers (``POST /api/markup/resolve``);
the agent writes one item per change it understood, citing the marks, the
architect's words, its reading of them and the components it will touch. The
list rides on one ``change_list`` card. The person unticks what they did not
mean and runs the rest; an item that is not sure is never run: it becomes its
own question, "which one did you mean?".

Whether an item is sure is not only the agent's word: an item citing a mark
the Runtime could not read clearly must say it is unsure, and a component no
cited mark lies on may be touched only by an item marked as an extra (the
skirting beside the circled floor), and only when the record holds it. An
item's note is the architect's only when it is whole clauses of the text the
Hub kept with those marks, exactly as written; a reworded note, or one cut out
of a clause, is refused.

This module holds the list, checks it against the readings it must agree with,
writes the question cards for unsure items and the approval that hands the
ticked items back. The approval is structured (the card and the item numbers);
the only words in it are the architect's own, so nothing the agent wrote
travels as the person's. It runs nothing itself.
"""

from collections.abc import Iterable, Mapping, Sequence
import re

from pydantic import Field, model_validator

from .models import AttentionDraft, AttentionMedia, AttentionOption, AttentionSource, CommandRef, _Record

#: At most this many items on one list, and this many components offered on an unsure item's question.
MAX_ITEMS = 30
MAX_CHOICES = 3
#: A list touching at most this many components, sure and borne out by its marks, may be applied first and
#: offered undo (decision 5 of the three-channel plan); anything larger is shown as A/B first.
SMALL_CHANGE = 5
#: The most raw language the design loop binds to one submission (CHAT_FEEDBACK_TOO_LONG beyond it).
RAW_LANGUAGE_LIMIT = 2000
#: What ends a clause of the architect's text; a note is whole clauses of it.
_ENDS = "。！？；，,.;!?\n\r"
_CLAUSE = re.compile(f"[^{re.escape(_ENDS)}]+")


class ChangeListItem(_Record):
    """One change as the agent understood it from the marks and the words."""

    n: int = Field(ge=1, le=MAX_ITEMS)
    markIds: list[str] = Field(default_factory=list, max_length=50)
    crop: AttentionMedia | None = None
    note: str = Field(min_length=1, max_length=400)
    understanding: str = Field(min_length=1, max_length=240)
    understandingZh: str | None = Field(default=None, min_length=1, max_length=240)
    targets: list[str] = Field(default_factory=list, max_length=100)
    protected: list[str] = Field(default_factory=list, max_length=100)
    unsure: bool = False
    extra: bool = False
    keep: bool = True

    @model_validator(mode="after")
    def _consistent(self):
        if self.crop is not None and self.crop.kind != "crop":
            raise ValueError("An item's picture is the crop of its own marks.")
        if not self.unsure and not self.targets:
            raise ValueError(f"Item {self.n} names no component; an item without one is unsure.")
        if set(self.targets) & set(self.protected):
            raise ValueError(f"Item {self.n} would both change and keep {sorted(set(self.targets) & set(self.protected))}.")
        return self


class ChangeList(_Record):
    """The items of one reading of one frame's marks, numbered from 1 in order."""

    frameId: str | None = None
    boardRevision: str | None = None
    runId: str | None = None
    items: list[ChangeListItem] = Field(min_length=1, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _numbered(self):
        if [item.n for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Change list items are numbered 1, 2, 3 … in order.")
        return self


def check_against(change_list: ChangeList, readings: Sequence[Mapping], words: Sequence[str],
                  catalog: Iterable[str] = ()) -> list[str]:
    """What in the list the marks, the architect's words and the record do not bear out; empty when all do.

    ``readings`` are the Runtime's mark readings (``MarkReading.to_dict``);
    ``words`` the architect's own text for this frame as the Hub keeps it (the
    text selected with the marks on Board, or the feedback they sent), never
    the agent's. Each item's note is whole clauses of them, as written. An item
    may cite only marks that were read. A sure item citing a mark read as
    ambiguous must be marked unsure. A component an item touches must be one
    its marks lie on, unless the item is an extra, which may name a component
    the record's catalogue holds.
    """

    read = {row["markId"]: row for row in readings}
    known = set(catalog)
    problems = []
    for item in change_list.items:
        if not _said(item.note, words):
            problems.append(f"Item {item.n}'s words are not the architect's as written with these marks: "
                            f"{item.note!r}.")
        unknown = [mark for mark in item.markIds if mark not in read]
        if unknown:
            problems.append(f"Item {item.n} cites marks that were not read: {', '.join(unknown)}.")
        unclear = [mark for mark in item.markIds if mark in read and read[mark].get("ambiguous")]
        if unclear and not item.unsure:
            problems.append(f"Item {item.n} cites {', '.join(unclear)}, which could not be read clearly; "
                            "mark the item unsure.")
        under = {ref for mark in item.markIds if mark in read for ref in _subjects(read[mark])}
        allowed = under | known if item.extra else under
        stray = [target for target in item.targets if target not in allowed]
        if stray:
            where = "no cited mark lies on and the record does not hold" if item.extra else (
                "no cited mark lies on; an item naming other components is an extra")
            problems.append(f"Item {item.n} touches {', '.join(stray)}, which {where}.")
    return problems


def unsure_cards(card_id: str, change_list: ChangeList, readings: Sequence[Mapping], *,
                 labels: Mapping[str, str] | None = None) -> list[AttentionDraft]:
    """One "which did you mean?" question per unsure item still ticked.

    Its answers are the subjects the item's marks lie on, targets and
    borderline ones first, then by share of the mark, named as the catalogue
    names them (``labels`` by id); the last answer lets the person say it in
    words instead, which is the only answer a mark over nothing offers.
    """

    read = {row["markId"]: row for row in readings}
    names = labels or {}
    cards = []
    for item in change_list.items:
        if not (item.unsure and item.keep):
            continue
        hits = [hit for mark in item.markIds if mark in read for hit in read[mark].get("hits", ())]
        hits.sort(key=lambda hit: (not hit.get("target"), not hit.get("borderline"), -float(hit.get("share", 0))))
        choices = list(dict.fromkeys(_subject(hit) for hit in hits))[:MAX_CHOICES]
        options = []
        for index, choice in enumerate(choices, start=1):
            name = names.get(choice, choice)
            options.append(AttentionOption(
                id=f"target-{index}", label=_short(name, 40), labelZh=_short(name, 20),
                consequence=_short(f"Item {item.n} changes {name}", 120),
                consequenceZh=_short(f"第 {item.n} 条改 {name}", 60),
                action=CommandRef(commandId="attention.change_list.choose_target",
                                  args={"cardId": card_id, "n": item.n, "target": choice})))
        options.append(AttentionOption(
            id="say", label="Say it in words", labelZh="用一句话说",
            consequence="Send what you write on this card to the conversation",
            consequenceZh="把你在卡上写的话发到对话里",
            action=CommandRef(commandId="chat.send_message")))
        cards.append(AttentionDraft(
            channel="decide", kind="choice",
            title=f"Which part did you mean in item {item.n}?",
            titleZh=f"第 {item.n} 条你指的是哪个构件？",
            summary=_short(f"You wrote: {item.note}", 400),
            summaryZh=_short(f"你写的：{item.note}", 400),
            source=AttentionSource(frameId=change_list.frameId, boardRevision=change_list.boardRevision,
                                   runIds=[change_list.runId] if change_list.runId else [], markIds=item.markIds),
            dedupeKey=f"unsure:{card_id}:{item.n}",
            media=[item.crop] if item.crop is not None else [],
            options=options,
        ))
    return cards


def applies_without_asking(change_list: ChangeList, readings: Sequence[Mapping], words: Sequence[str],
                           catalog: Iterable[str] = ()) -> bool:
    """Whether the ticked items may be applied first and offered undo.

    Only a list its marks, words and the record bear out (``check_against``),
    with nothing unsure ticked, touching at most SMALL_CHANGE components.
    """

    kept = [item for item in change_list.items if item.keep]
    touched = {target for item in kept for target in item.targets}
    return (bool(kept) and not any(item.unsure for item in kept) and len(touched) <= SMALL_CHANGE
            and not check_against(change_list, readings, words, catalog))


def approval(card_id: str, change_list: ChangeList, words: Sequence[str]) -> dict:
    """The person's approval of the ticked, sure items, as the message the Hub sends in their name.

    ``content`` is the message's text: the architect's own words from Board,
    one line per distinct sentence, as written, and nothing else; ``cardId``
    and ``items`` travel in the message's structured fields, and the agent
    reads what each item means from the card. The design loop binds a
    submission to this message and, through ``feedbackQuote``, to the words of
    the items it builds, so ``quotes`` groups the items by their words: one
    submission per group, quoting ``feedbackQuote``, which occurs exactly once
    in the text. Items that share their words share a group and one line. A
    line that also lies inside another (门 inside 前门加宽) is quoted with its
    line breaks, the whole line; only when even that is not unique is
    ``feedbackQuote`` None, binding the whole message, which the design loop
    takes up to RAW_LANGUAGE_LIMIT characters; a longer one is refused here.

    Approving the list is not an answer to any condition: a submission that
    comes back needing a question is still asked. A note that is not whole
    clauses of ``words`` as written (the architect's own text for this frame)
    is never sent as theirs: the approval is refused.
    """

    approved = [item for item in change_list.items if item.keep and not item.unsure]
    if not approved:
        raise ValueError("No ticked item is sure enough to build.")
    unsaid = [item.n for item in approved if not _said(item.note, words)]
    if unsaid:
        raise ValueError(f"Items {unsaid} carry words the architect did not write with these marks.")
    lines = list(dict.fromkeys(item.note.strip() for item in approved))
    content = "\n".join(lines)
    quotes = [{"items": [item.n for item in approved if item.note.strip() == line],
               "feedbackQuote": _quote(lines, index, content)} for index, line in enumerate(lines)]
    if any(row["feedbackQuote"] is None for row in quotes) and len(content) > RAW_LANGUAGE_LIMIT:
        raise ValueError(f"The approved words are {len(content)} characters and one item can only bind them all; "
                         f"approve fewer items at once (at most {RAW_LANGUAGE_LIMIT} characters).")
    return {"cardId": card_id, "items": [item.n for item in approved], "content": content, "quotes": quotes}


def _quote(lines: Sequence[str], index: int, content: str) -> str | None:
    """The shortest span naming this line alone: the line, else the line with its line breaks, else None."""

    line = lines[index]
    before = "\n" if index > 0 else ""
    after = "\n" if index < len(lines) - 1 else ""
    for candidate in dict.fromkeys((line, before + line + after)):
        if content.count(candidate) == 1:
            return candidate
    return None


def _said(note: str, words: Sequence[str]) -> bool:
    """Whether a note is the architect's: one of their clauses, or neighbouring clauses, exactly as written.

    A clause ends at 。！？；，,.;!? or a line break. A note that is only part of
    a clause is not theirs: 「龙骨和面层」 cut from 「龙骨和面层不要」 says the
    opposite. Runs of spaces count as one; the punctuation at either end of the
    note does not count.
    """

    wanted = _plain(note)
    return bool(wanted) and any(wanted == _plain(said[start:end])
                                for said in words for start, end in _runs(said))


def _runs(text: str) -> Iterable[tuple[int, int]]:
    """Every run of whole neighbouring clauses in a text, as (start, end) offsets."""

    clauses = [(match.start(), match.end()) for match in _CLAUSE.finditer(text) if match.group().strip()]
    for first, (start, _) in enumerate(clauses):
        for _, end in clauses[first:]:
            yield start, end


def _plain(text: str) -> str:
    return " ".join(text.split()).strip(_ENDS)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _subject(hit: Mapping) -> str:
    return hit.get("elementId") or hit["componentId"]


def _subjects(reading: Mapping) -> set[str]:
    return {ref for hit in reading.get("hits", ()) for ref in (hit.get("elementId"), hit.get("componentId")) if ref}


__all__ = [
    "ChangeList",
    "ChangeListItem",
    "MAX_ITEMS",
    "SMALL_CHANGE",
    "applies_without_asking",
    "approval",
    "check_against",
    "unsure_cards",
]
