""""I read your marks as these N changes": the list an agent proposes from marks on Board, before anything is built.

The architect circles part of a drawing and writes a sentence. The Runtime
reads each mark into the components it covers (``POST /api/markup/resolve``);
the agent writes one item per change it understood, citing the marks, the
architect's words, its reading of them and the components it will touch. The
list rides on one ``change_list`` card. The person unticks what they did not
mean and runs the rest; an item the agent is not sure of is never run: it
becomes its own question, "which one did you mean?".

This module holds the list, checks it against the readings it must agree with,
writes the question cards for unsure items and the message that hands the
ticked items back to the conversation. It runs nothing itself.
"""

from collections.abc import Iterable, Mapping, Sequence

from pydantic import Field, model_validator

from .models import AttentionDraft, AttentionMedia, AttentionOption, AttentionSource, CommandRef, _Record

#: At most this many items on one list, and this many answers on an unsure item's question.
MAX_ITEMS = 30
MAX_CHOICES = 3
#: A list touching at most this many components, with nothing unsure, may be applied first and offered undo
#: (decision 5 of the three-channel plan); anything larger is shown as A/B first.
SMALL_CHANGE = 5


class ChangeListItem(_Record):
    """One change as the agent understood it from the marks and the words."""

    n: int = Field(ge=1, le=MAX_ITEMS)
    markIds: list[str] = Field(default_factory=list, max_length=50)
    crop: AttentionMedia | None = None
    note: str = Field(min_length=1, max_length=400)
    understanding: str = Field(min_length=1, max_length=240)
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
    An item may cite only marks that were read. A component it touches must be
    one its marks lie on, or one the record's catalogue holds (an extra the
    agent adds by name, such as the skirting beside the circled floor).
    """

    read = {row["markId"]: row for row in readings}
    known = set(catalog)
    problems = []
    for item in change_list.items:
        unknown = [mark for mark in item.markIds if mark not in read]
        if unknown:
            problems.append(f"Item {item.n} cites marks that were not read: {', '.join(unknown)}.")
        under = {ref for mark in item.markIds if mark in read for ref in _subjects(read[mark])}
        stray = [target for target in item.targets if target not in under and target not in known]
        if stray:
            problems.append(f"Item {item.n} touches {', '.join(stray)}, which no cited mark lies on and the record "
                            "does not hold.")
    return problems


def unsure_cards(card_id: str, change_list: ChangeList, readings: Sequence[Mapping]) -> list[AttentionDraft]:
    """One "which did you mean?" question per unsure item still ticked, its answers the subjects under its marks.

    The answers are the subjects the item's marks lie on, targets and borderline
    ones first, then by share of the mark; a mark over nothing offers none, and
    the question still stands so the person can say it in words.
    """

    read = {row["markId"]: row for row in readings}
    cards = []
    for item in change_list.items:
        if not (item.unsure and item.keep):
            continue
        hits = [hit for mark in item.markIds if mark in read for hit in read[mark].get("hits", ())]
        hits.sort(key=lambda hit: (not hit.get("target"), not hit.get("borderline"), -float(hit.get("share", 0))))
        choices = list(dict.fromkeys(_subject(hit) for hit in hits))[:MAX_CHOICES]
        cards.append(AttentionDraft(
            channel="decide", kind="choice",
            title=f"第 {item.n} 条你指的是哪个构件？"[:160],
            summary=f"你写的：{item.note}"[:400],
            source=AttentionSource(frameId=change_list.frameId, boardRevision=change_list.boardRevision,
                                   runIds=[change_list.runId] if change_list.runId else [], markIds=item.markIds),
            dedupeKey=f"unsure:{card_id}:{item.n}",
            media=[item.crop] if item.crop is not None else [],
            options=[AttentionOption(
                id=f"target-{index}", label=_short(choice, 20), consequence=_short(f"第 {item.n} 条改 {choice}", 60),
                action=CommandRef(commandId="attention.change_list.choose_target",
                                  args={"cardId": card_id, "n": item.n, "target": choice}))
                for index, choice in enumerate(choices, start=1)],
        ))
    return cards


def applies_without_asking(change_list: ChangeList) -> bool:
    """Whether the ticked items are small enough to apply first and offer undo: nothing unsure, few components."""

    kept = [item for item in change_list.items if item.keep]
    touched = {target for item in kept for target in item.targets}
    return bool(kept) and not any(item.unsure for item in kept) and len(touched) <= SMALL_CHANGE


def handoff_message(change_list: ChangeList) -> str:
    """The person's message that hands the ticked, sure items back to the conversation to be built.

    Each item keeps the architect's own words beside the agent's reading, so
    the words stay bound to the change; unsure and unticked items are left out.
    """

    lines = ["按这份清单改（只改勾选的条目）："]
    for item in change_list.items:
        if not item.keep or item.unsure:
            continue
        line = f"{item.n}. {item.understanding}（构件：{'、'.join(item.targets)}"
        if item.protected:
            line += f"；不动：{'、'.join(item.protected)}"
        lines.append(line + f"）。原话：「{item.note}」")
    if len(lines) == 1:
        raise ValueError("No ticked item is sure enough to build.")
    return "\n".join(lines)


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
    "check_against",
    "handoff_message",
    "unsure_cards",
]
