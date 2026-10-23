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
skirting beside the circled floor), and only when the record holds it.

This module holds the list, checks it against the readings it must agree with,
writes the question cards for unsure items and the approval that hands the
ticked items back. The approval is structured (the card and the item numbers);
the only words in it are the architect's own, so nothing the agent wrote
travels as the person's. It runs nothing itself.
"""

from collections.abc import Iterable, Mapping, Sequence

from pydantic import Field, model_validator

from .models import AttentionDraft, AttentionMedia, AttentionOption, AttentionSource, CommandRef, _Record

#: At most this many items on one list, and this many components offered on an unsure item's question.
MAX_ITEMS = 30
MAX_CHOICES = 3
#: A list touching at most this many components, sure and borne out by its marks, may be applied first and
#: offered undo (decision 5 of the three-channel plan); anything larger is shown as A/B first.
SMALL_CHANGE = 5


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


def check_against(change_list: ChangeList, readings: Sequence[Mapping],
                  catalog: Iterable[str] = ()) -> list[str]:
    """What in the list the marks and the record do not bear out; empty when everything does.

    ``readings`` are the Runtime's mark readings (``MarkReading.to_dict``).
    An item may cite only marks that were read. A sure item citing a mark read
    as ambiguous must be marked unsure. A component an item touches must be one
    its marks lie on, unless the item is an extra, which may name a component
    the record's catalogue holds.
    """

    read = {row["markId"]: row for row in readings}
    known = set(catalog)
    problems = []
    for item in change_list.items:
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


def applies_without_asking(change_list: ChangeList, readings: Sequence[Mapping], catalog: Iterable[str] = ()) -> bool:
    """Whether the ticked items may be applied first and offered undo.

    Only a list its marks and the record bear out (``check_against``), with
    nothing unsure ticked, touching at most SMALL_CHANGE components.
    """

    kept = [item for item in change_list.items if item.keep]
    touched = {target for item in kept for target in item.targets}
    return (bool(kept) and not any(item.unsure for item in kept) and len(touched) <= SMALL_CHANGE
            and not check_against(change_list, readings, catalog))


def approval(card_id: str, change_list: ChangeList) -> dict:
    """The person's approval of the ticked, sure items: the card, the item numbers and their own words.

    The agent reads what each item means from the card; the approval carries
    no text the agent wrote, so the architect's words are the only language
    bound to the change.
    """

    approved = [item for item in change_list.items if item.keep and not item.unsure]
    if not approved:
        raise ValueError("No ticked item is sure enough to build.")
    return {"cardId": card_id, "items": [item.n for item in approved],
            "rawLanguage": list(dict.fromkeys(item.note for item in approved))}


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
