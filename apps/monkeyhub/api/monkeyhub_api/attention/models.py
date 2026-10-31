"""The attention queue's records: a card, the choices on it, and how a person handled it.

A card is written by a producer, which reads facts the Hub already has (a
finished run, a permission request, a failed operation) and offers an
``AttentionDraft`` under a ``dedupeKey``: one thing to look at is one card. The
store turns a draft into an ``AttentionItem`` and keeps its state. Every button
on a card is one command of the command tree (``governance/module_registry.json``,
``capabilities[].ui.commands``) with its bound arguments; a card never carries a
request of its own.

Text a person reads comes in the command tree's two languages: the plain field
in English and its ``…Zh`` sibling in Chinese, which falls back to the English
one when a writer gives only that.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AttentionChannel = Literal["decide", "annotate", "guess", "notice"]
AttentionKind = Literal[
    "ab",            # two results side by side
    "choice",        # one question, a few answers: a permission, an archive, an update, a conflict
    "plan",          # a large change shown as a plan first
    "review_frame",  # this version waits for you on its Board frame
    "change_list",   # "I read your marks as these N changes"
    "ask",           # a question with at most three answers
    "suggestion",    # something the system would do, not done yet
    "auto_fixed",    # handled for you, can be undone
    "info",          # finished, failed and the like
]
AttentionState = Literal["open", "review", "later", "working", "done", "dismissed", "superseded", "expired"]
Actor = Literal["person", "machine", "bulk"]

CHANNEL_OF: dict[str, str] = {
    "ab": "decide", "choice": "decide", "plan": "decide",
    "review_frame": "annotate", "change_list": "annotate",
    "ask": "guess", "suggestion": "guess",
    "auto_fixed": "notice", "info": "notice",
}
# Counted in "N need you".
PENDING: frozenset[str] = frozenset({"open", "review"})
# Not handled yet: put off, or being carried out.
UNSETTLED: frozenset[str] = PENDING | {"later", "working"}
CLOSED: frozenset[str] = frozenset({"done", "dismissed", "superseded", "expired"})

# The command tree's id rule: lowercase, dotted, snake_case segments.
COMMAND_ID = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
ArgValue = str | int | float | bool | None | list[str]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CommandRef(_Record):
    """One command of the command tree and the arguments it runs with."""

    commandId: str = Field(pattern=COMMAND_ID, max_length=120)
    args: dict[str, ArgValue] = Field(default_factory=dict)


class AttentionOption(_Record):
    """One answer on a card: what it is called, what it will do, and the command that does it."""

    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$", max_length=40)
    label: str = Field(min_length=1, max_length=40)
    labelZh: str | None = Field(default=None, min_length=1, max_length=20)
    consequence: str = Field(min_length=1, max_length=120)
    consequenceZh: str | None = Field(default=None, min_length=1, max_length=60)
    action: CommandRef
    undoable: bool = False
    recommended: bool = False


class AttentionSource(_Record):
    """The facts a card was written from; every field may be empty."""

    runIds: list[str] = Field(default_factory=list)
    studyId: str | None = None
    chatSessionId: str | None = None
    chatMessageId: str | None = None
    permissionId: str | None = None
    operationId: str | None = None
    jobId: str | None = None
    boardRevision: str | None = None
    frameId: str | None = None
    markIds: list[str] = Field(default_factory=list)
    errorCode: str | None = None
    ruleId: str | None = None
    targetRef: str | None = None


class ModelSourceRef(_Record):
    """One exact model of a run, as the Studio names it (its ``ModelSourceDto``): what a thumbnail is drawn from."""

    runId: str = Field(min_length=1)
    stateDigest: str = Field(min_length=1)
    assetSha256: str = Field(min_length=1)


class AttentionMedia(_Record):
    """A picture a card shows: a model view of a run, a Board frame, or a crop of marks.

    A model view with a ``modelSource`` is drawn from the project's thumbnails; one with an ``optionId`` is that
    answer, so choosing the picture answers the card.
    """

    kind: Literal["model_view", "board_frame", "crop"]
    runId: str | None = None
    modelSource: ModelSourceRef | None = None
    optionId: str | None = None
    view: str | None = None
    frameId: str | None = None
    markIds: list[str] = Field(default_factory=list)
    caption: str | None = Field(default=None, max_length=160)
    captionZh: str | None = Field(default=None, max_length=160)


class EvidenceRow(_Record):
    """One line of evidence: what changed, what the checks say, what it costs."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    label: str = Field(min_length=1, max_length=40)
    labelZh: str | None = Field(default=None, min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=240)
    textZh: str | None = Field(default=None, min_length=1, max_length=240)
    status: Literal["ok", "warn", "unknown"] = "unknown"
    refs: list[str] = Field(default_factory=list)


class Recommendation(_Record):
    """The answer the card's writer recommends, and on what grounds."""

    optionId: str
    reason: str = Field(min_length=1, max_length=240)
    reasonZh: str | None = Field(default=None, min_length=1, max_length=240)
    basis: list[str] = Field(default_factory=list)


class UndoSpec(_Record):
    """The reverse of a handled option, run by the same executor while its precondition still holds."""

    action: CommandRef
    precondition: dict[str, ArgValue] = Field(default_factory=dict)


class Resolution(_Record):
    """What a person (or the machine, for a registered repair) chose, when, and how to take it back."""

    optionId: str | None = None
    by: Actor
    at: str
    note: str | None = Field(default=None, max_length=400)
    receipt: dict[str, ArgValue] = Field(default_factory=dict)
    undo: UndoSpec | None = None


class StateChange(_Record):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, populate_by_name=True)

    from_: AttentionState | None = Field(alias="from")
    to: AttentionState
    by: Actor
    at: str
    optionId: str | None = None
    note: str | None = Field(default=None, max_length=400)
    # What the executor saw while carrying the answer out, kept on its working
    # step so that an answer a restart cut short can be finished or put back.
    facts: dict[str, ArgValue] = Field(default_factory=dict)


#: At most this many items on one change list.
MAX_CHANGE_ITEMS = 30


class ChangeListItem(_Record):
    """One change as the agent understood it from the marks and the words."""

    n: int = Field(ge=1, le=MAX_CHANGE_ITEMS)
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
    items: list[ChangeListItem] = Field(min_length=1, max_length=MAX_CHANGE_ITEMS)

    @model_validator(mode="after")
    def _numbered(self):
        if [item.n for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Change list items are numbered 1, 2, 3 … in order.")
        return self


class AttentionDraft(_Record):
    """What a producer offers: a card's content, without a state or an id."""

    channel: AttentionChannel
    kind: AttentionKind
    title: str = Field(min_length=1, max_length=160)
    titleZh: str | None = Field(default=None, min_length=1, max_length=160)
    summary: str | None = Field(default=None, max_length=400)
    summaryZh: str | None = Field(default=None, max_length=400)
    source: AttentionSource = Field(default_factory=AttentionSource)
    dedupeKey: str = Field(min_length=1, max_length=240)
    media: list[AttentionMedia] = Field(default_factory=list)
    evidence: list[EvidenceRow] = Field(default_factory=list)
    options: list[AttentionOption] = Field(default_factory=list)
    # Commands that only open something (compare and decide, a Board frame, the
    # conversation): they change neither the card nor the project.
    opens: list[CommandRef] = Field(default_factory=list)
    recommendation: Recommendation | None = None
    # A change_list card's items (``change_list.py``), the architect's own words they were checked against, as the Hub
    # kept them with the marks, and the Runtime's reading of each mark (``MarkReading.to_dict``).
    changeList: ChangeList | None = None
    changeWords: list[str] = Field(default_factory=list, max_length=20)
    markReadings: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def _consistent(self):
        if CHANNEL_OF[self.kind] != self.channel:
            raise ValueError(f"A {self.kind} card belongs to the {CHANNEL_OF[self.kind]} channel, not {self.channel}.")
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("Each option on a card needs its own id.")
        flagged = [option.id for option in self.options if option.recommended]
        # No grounds, no recommendation and nothing preselected.
        expected = [self.recommendation.optionId] if self.recommendation else []
        if self.recommendation and self.recommendation.optionId not in ids:
            raise ValueError("The recommendation names an option this card does not have.")
        if flagged != expected:
            raise ValueError("Only the option the recommendation names is marked recommended.")
        if any(item.optionId is not None and item.optionId not in ids for item in self.media):
            raise ValueError("A picture answers with an option this card does not have.")
        return self


DRAFT_FIELDS: tuple[str, ...] = tuple(AttentionDraft.model_fields)


class AttentionItem(AttentionDraft):
    """One card in a project's queue and how it has been handled."""

    id: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    projectId: str
    projectDir: str
    state: AttentionState
    resolution: Resolution | None = None
    snoozedUntil: str | None = None
    createdAt: str
    updatedAt: str
    history: list[StateChange] = Field(default_factory=list)


class Suppression(_Record):
    """A rule a person asked not to hear from again about one target until a time."""

    ruleId: str
    targetRef: str
    until: str


class ProjectAttentionCount(BaseModel):
    projectId: str
    projectDir: str
    open: int = Field(description="Cards that need a person now: open, or a change list waiting for review.")
    later: int
    working: int


class AttentionSummary(BaseModel):
    projects: list[ProjectAttentionCount]
    hub: ProjectAttentionCount | None = Field(default=None, description=(
        "Cards about the Hub itself, such as a prepared update: counted in the totals and shown in the queue, "
        "never as a project row."))
    open: int
    later: int
    working: int
    invalid: list[str] = Field(default_factory=list, description=(
        "Runtime ids of projects whose saved cards could not be read; none of their cards are counted."))


class AttentionDismissRequest(BaseModel):
    """Set a card aside; with days, it comes back after them."""

    model_config = ConfigDict(extra="forbid", strict=True)
    days: int | None = Field(default=None, ge=1, le=365)
