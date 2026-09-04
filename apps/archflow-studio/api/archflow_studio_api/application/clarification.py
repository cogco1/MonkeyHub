"""One short pending intent, and the four answers a request may end in.

The chat log is not the truth about what the architect asked. What is true is a
single **pending intent**: the utterance that started the exchange, the target
resolved so far, the slots still missing, the candidates offered, the candidates
the architect has already rejected, and one ``continuationToken`` the next
request carries back. It lives in this process, keyed by that token and bound to
one ``stateDigest``; nothing about it is version history and nothing about it
survives a restart.

Around it there are exactly four answers, and a request ends in one of them:

``COMPILED``
    the sentence became a proposal, and the pending intent is closed.
``NEEDS_CLARIFICATION``
    something only the architect can settle is missing, and the answer names
    which slot, with concrete options rather than an internal identifier.
``MISSING_EDITABLE_CONTROL``
    the thing named exists in the model and has no editable control in the
    record. This is *terminal*: the system says what it lacks, and offers an
    authored-control draft. It never asks the architect for an ``elementId``.
``UNSUPPORTED``
    no action in the grammar can express the request, or the exchange stopped
    advancing.

**The one rule that ends the loop.** Every reply must either shrink the missing
slots, correct the target, narrow the candidates, compile, or terminate. A round
that changes none of those is not asked again: it terminates as ``UNSUPPORTED``
with ``CLARIFICATION_MADE_NO_PROGRESS``, which says what the system is missing
rather than repeating the question the architect has already answered twice.

Nothing here writes. ``AuthoredControlDraft`` is a *value*: it names a control
somebody would have to author, where the suggestion was read from and how
confident that reading is. No route retains it, and no record kind exists for
it.
"""

from __future__ import annotations

import math

from dataclasses import dataclass
import re
from typing import Mapping, Sequence
from uuid import uuid4

from ..transport.errors import StudioError
from .intent import ACCEPTED_FORMS, parse_utterance
from .intent_agent import Compilation, Selection
from .projection import ProjectedElement, StateProjection

# ---- the closed set of answers ---------------------------------------------

COMPILED = "COMPILED"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
MISSING_EDITABLE_CONTROL = "MISSING_EDITABLE_CONTROL"
UNSUPPORTED = "UNSUPPORTED"
OUTCOMES: tuple[str, ...] = (
    COMPILED,
    NEEDS_CLARIFICATION,
    MISSING_EDITABLE_CONTROL,
    UNSUPPORTED,
)

# The four things a request can be asking for. "Supply the field" is not a
# value change and never enters the scalar grammar.
CHANGE_EXISTING_VALUE = "change_existing_value"
DECLARE_MISSING_CONTROL = "declare_missing_control"
CLARIFY = "clarify"
UNSUPPORTED_ACTION = "unsupported"
ACTION_KINDS: tuple[str, ...] = (
    CHANGE_EXISTING_VALUE,
    DECLARE_MISSING_CONTROL,
    CLARIFY,
    UNSUPPORTED_ACTION,
)

# What a clarification can still be missing. Only these four; a slot outside
# them would be a question nobody could answer in a sentence.
SLOT_TARGET = "target"
SLOT_PROPERTY = "property"
SLOT_VALUE = "value"
SLOT_ORIENTATION = "orientation"

# Why an answer is what it is. A client may branch on these; they are part of
# the wire and never rewritten for display.
COMPILED_CLEANLY = "COMPILED_CLEANLY"
COMPONENT_HAS_NO_EDITABLE_ELEMENT = "COMPONENT_HAS_NO_EDITABLE_ELEMENT"
COMPONENT_HAS_NO_SUCH_CONTROL = "COMPONENT_HAS_NO_SUCH_CONTROL"
CONTROL_MUST_BE_AUTHORED = "CONTROL_MUST_BE_AUTHORED"
TARGET_UNRESOLVED = "TARGET_UNRESOLVED"
TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
VALUE_UNRESOLVED = "VALUE_UNRESOLVED"
AGENT_ASKED = "AGENT_ASKED"
CLARIFICATION_MADE_NO_PROGRESS = "CLARIFICATION_MADE_NO_PROGRESS"
REQUEST_NOT_EXPRESSIBLE = "REQUEST_NOT_EXPRESSIBLE"

STALE_CLARIFICATION = "STALE_CLARIFICATION"


# ---- the words a request is read with --------------------------------------

# One noun, one kind of thing. This table is the only place a word becomes a
# kind, and it is bilingual because the architect is. It exists so that "the
# columns" can never resolve to a roof, an abutment or a wall: a candidate whose
# kinds do not include the kind the request named is not a candidate at all,
# whatever its fields happen to be called.
_KIND_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("colonnade", ("colonnade", "portico", "porch", "柱廊", "门廊")),
    ("column", ("columns", "column", "pillar", "pilaster", "柱子", "柱列", "立柱")),
    ("roof", ("roofs", "roof", "pediment", "屋顶", "屋面", "山花")),
    ("abutment", ("abutments", "abutment", "端墩", "支墩")),
    ("wall", ("walls", "wall", "墙体", "墙")),
    ("stair", ("stairs", "stair", "steps", "step", "楼梯", "台阶")),
    ("beam", ("architrave", "entablature", "cornice", "beams", "beam", "檐口", "额枋", "梁")),
    ("floor", ("floors", "floor", "slab", "base", "楼板", "地板", "基座")),
    ("dome", ("cupola", "dome", "穹顶", "圆顶")),
    ("door", ("doors", "door", "门")),
    ("window", ("windows", "window", "窗户", "窗")),
)

# What quality of a thing the request is about. The record's fields are named
# in English; the architect is not obliged to be.
_PROPERTY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "height",
        ("height", "taller", "higher", "raise", "lift", "高度", "提高", "抬高", "加高", "升高"),
    ),
    ("thickness", ("thickness", "thicker", "厚度", "加厚")),
    ("width", ("width", "wider", "宽度", "加宽")),
    ("length", ("length", "longer", "长度", "加长")),
    ("depth", ("depth", "deeper", "深度", "加深")),
    ("radius", ("radius", "半径")),
    ("diameter", ("diameter", "直径")),
    ("elevation", ("elevation", "标高")),
)

# A compass word is an identity the record can carry. A viewer-relative word is
# not: left of what? Without a camera in the request nothing in the record says
# which way the reader is facing, so "left" is a slot the architect still has to
# fill and it narrows nothing.
_COMPASS_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("west", ("western", "west", "西侧", "西边", "西")),
    ("east", ("eastern", "east", "东侧", "东边", "东")),
    ("north", ("northern", "north", "北侧", "北边", "北")),
    ("south", ("southern", "south", "南侧", "南边", "南")),
)
_VIEWER_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("left", ("left", "左侧", "左边", "左")),
    ("right", ("right", "右侧", "右边", "右")),
    ("front", ("front", "前面", "前方", "前")),
    ("back", ("rear", "back", "后面", "后方", "后")),
)

# "No, the columns" — the architect is refusing what was offered, not adding to
# it. Whatever was on the table goes into rejectedCandidates and never comes
# back inside this pending intent.
_NEGATIONS: tuple[str, ...] = (
    "不是",
    "不对",
    "错了",
    "而是",
    "应该是",
    "我是说",
    "no, ",
    "not that",
    "not the",
    "i meant",
    "wrong",
)

# "Supply the field" is a request to author a control, not to move a number.
_DECLARE_ZH = re.compile(
    r"(补充|补齐|添加|增加|新增|建立|加上|加个|创建)[^。；;]{0,40}?(字段|控制|参数|属性)"
)
_DECLARE_EN = re.compile(
    r"\b(add|declare|create|define|author)\b[^.]{0,24}?"
    r"\b(field|fields|control|controls|parameter|parameters|property)\b"
)


def _longest_first(
    table: Sequence[tuple[str, tuple[str, ...]]]
) -> tuple[tuple[str, str], ...]:
    """(word, name) pairs, longest word first, so 门廊 is read before 门."""

    pairs = [(word, name) for name, words in table for word in words]
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return tuple(pairs)


_KINDS_BY_WORD = _longest_first(_KIND_WORDS)
_PROPERTIES_BY_WORD = _longest_first(_PROPERTY_WORDS)
_COMPASS_BY_WORD = _longest_first(_COMPASS_WORDS)
_VIEWER_BY_WORD = _longest_first(_VIEWER_WORDS)

_SEPARATORS = re.compile("[^0-9a-z一-鿿]+")


def _normalised(text: str) -> str:
    """Lowercased, with every separator a space: ``portico-columns`` is two words."""

    return f" {_SEPARATORS.sub(' ', text.lower()).strip()} "


def _named(text: str, table: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """Every name this text carries, each word consumed once, longest first.

    Consuming the match is what keeps 门廊 (a colonnade) from also reading as
    门 (a door): the longer word wins the characters it covers, and the shorter
    one never sees them.
    """

    haystack = _normalised(text)
    found: list[str] = []
    for word, name in table:
        if word.isascii():
            pattern = rf"(?<![0-9a-z]){re.escape(word)}(?![0-9a-z])"
            if re.search(pattern, haystack) is None:
                continue
            haystack = re.sub(pattern, " ", haystack)
        else:
            if word not in haystack:
                continue
            haystack = haystack.replace(word, " ")
        if name not in found:
            found.append(name)
    return tuple(found)


def kinds_in(text: str) -> frozenset[str]:
    """Which kinds of thing this text names — of a request or of an identifier."""

    return frozenset(_named(text, _KINDS_BY_WORD))


def property_in(text: str) -> str | None:
    """Which quality this text is about, or ``None`` when it names none."""

    names = _named(text, _PROPERTIES_BY_WORD)
    return names[0] if names else None


def compass_in(text: str) -> str | None:
    """The compass identity this text carries, or ``None``."""

    names = _named(text, _COMPASS_BY_WORD)
    return names[0] if names else None


def viewer_side_in(text: str) -> str | None:
    """The viewer-relative side this text names, or ``None``."""

    names = _named(text, _VIEWER_BY_WORD)
    return names[0] if names else None


def compass_side(
    relative: str, camera: Mapping[str, object] | None, compass: Mapping[str, Sequence[float]] | None
) -> str | None:
    """Which compass side "left" (etc.) is from where the camera stands.

    Needs both the request's camera and the project's compass (PROJECT.md: which
    world direction is north). Without either the word cannot be read and stays
    a slot the architect fills.
    """

    if camera is None or compass is None:
        return None
    try:
        position = camera["position"]  # type: ignore[index]
        target = camera["target"]  # type: ignore[index]
        view = (float(target[0]) - float(position[0]), float(target[1]) - float(position[1]))  # type: ignore[index]
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    length = math.hypot(*view)
    if length == 0:
        return None
    view = (view[0] / length, view[1] / length)
    vectors = {
        "front": view,
        "back": (-view[0], -view[1]),
        "left": (-view[1], view[0]),
        "right": (view[1], -view[0]),
    }
    wanted = vectors.get(relative)
    if wanted is None:
        return None
    best: tuple[float, str] | None = None
    for side in ("north", "east", "south", "west"):
        axis = compass.get(side)
        if axis is None:
            continue
        try:
            ax = (float(axis[0]), float(axis[1]))
        except (TypeError, ValueError, IndexError):
            continue
        norm = math.hypot(*ax)
        if norm == 0:
            continue
        score = (ax[0] * wanted[0] + ax[1] * wanted[1]) / norm
        if best is None or score > best[0]:
            best = (score, side)
    return best[1] if best is not None and best[0] > 0.5 else None


def is_correction(text: str) -> bool:
    """Whether this reply refuses what was offered rather than adding to it."""

    haystack = f" {text.lower().strip()} "
    return any(marker in haystack for marker in _NEGATIONS)


def declares_a_control(text: str) -> bool:
    """Whether the request asks for a control to exist, not for a number to move."""

    lowered = text.lower()
    return _DECLARE_ZH.search(lowered) is not None or _DECLARE_EN.search(lowered) is not None


# ---- the pending intent -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateOption:
    """One thing the architect could have meant, as the record has it.

    ``current_value`` and ``orientation`` are here because a list of internal
    identifiers is not a choice a person can make: the option says what the
    number is now and where the thing is.
    """

    component_id: str
    element_id: str | None
    key: str | None
    current_value: int | float | None
    unit: str | None
    orientation: str | None
    label: str

    @property
    def ref(self) -> str:
        """How a rejected candidate is written down, and compared later."""

        if self.element_id is None:
            return f"component:{self.component_id}"
        if self.key is None:
            return f"element:{self.element_id}"
        return f"element:{self.element_id}.{self.key}"


@dataclass(frozen=True, slots=True)
class AuthoredControlDraft:
    """A control somebody would have to author, and where it was read from.

    It is a value and nothing retains it. A later confirm step — not this task —
    would turn it into an edit of the authored record; until then it invents no
    number, names no coordinate, and touches no .3dm.
    """

    target_component_id: str
    suggested_element_id: str
    semantic_property: str | None
    producer: str | None
    binding: str | None
    unit: str | None
    provenance: tuple[str, ...]
    confidence: str
    dependency_requirements: tuple[str, ...]
    suggested_action: str
    # What the catalog (GET /api/state.catalog) says about the component: MODEL_VISIBLE_CATALOG_MISSING
    # when the model shows objects of it that no Element@1 row produced, DECLARED_ONLY when it has
    # no objects either; the objects are named so the declaration has its provenance.
    catalog_status: str | None = None
    object_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingIntent:
    """The one short-term structure a clarification chain is carried in.

    Bound to a ``stateDigest``: a pending intent whose state has moved is void
    and is never applied to the state that answers now.
    """

    request_id: str
    state_digest: str
    original_utterance: str
    action_kind: str
    target_component_id: str | None
    element_id: str | None
    requested_semantic_property: str | None
    known_slots: Mapping[str, str]
    missing_slots: tuple[str, ...]
    candidates: tuple[CandidateOption, ...]
    rejected_candidates: tuple[str, ...]
    reason_code: str
    continuation_token: str | None
    turn: int

    @property
    def advance_key(self) -> tuple[object, ...]:
        """What has to change for a reply to count as progress.

        Kaiwen's five ways to advance, as one comparable value: the target, the
        element, the slots still missing and the candidates still on the table.
        Two consecutive rounds with the same key are the loop, and the loop is
        what this module exists to end.
        """

        return (
            self.target_component_id,
            self.element_id,
            frozenset(self.missing_slots),
            tuple(option.ref for option in self.candidates),
            frozenset(self.rejected_candidates),
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """One request's answer, before the route turns it into a status code.

    A deterministic value, not a receipt: nothing crossed a boundary to produce
    it. ``selection`` is what the compiler is handed when there is still a
    sentence to compile, and ``None`` on every terminal answer.
    """

    outcome: str
    pending: PendingIntent
    selection: Selection | None
    question: str | None
    detail: str
    accepted_forms: tuple[str, ...] = ()
    draft: AuthoredControlDraft | None = None

    @property
    def terminal(self) -> bool:
        return self.outcome in (MISSING_EDITABLE_CONTROL, UNSUPPORTED)


class PendingIntentStore:
    """The pending intents this process is holding, keyed by continuation token.

    A dictionary in one process, like the proposal store beside it: not version
    history, not written to the project, lost on restart. A clarification chain
    that outlived the process starts again rather than resuming against a state
    nobody checked.
    """

    def __init__(self) -> None:
        self._by_token: dict[str, PendingIntent] = {}

    def open(self, pending: PendingIntent) -> PendingIntent:
        if pending.continuation_token is not None:
            self._by_token[pending.continuation_token] = pending
        return pending

    def resume(self, token: str, state_digest: str | None) -> PendingIntent:
        """The pending intent this token names, if it is still about this state."""

        pending = self._by_token.get(token)
        if pending is None:
            raise StudioError(
                409,
                STALE_CLARIFICATION,
                f"no pending clarification {token} in this process. A pending "
                "intent is held in memory and is lost on restart; say the "
                "request again rather than continuing one nothing here "
                "remembers.",
            )
        if pending.state_digest != state_digest:
            del self._by_token[token]
            raise StudioError(
                409,
                STALE_CLARIFICATION,
                f"the pending clarification was opened against state "
                f"{pending.state_digest} and the project is at {state_digest}. "
                "It is void: an answer given about the old state is not "
                "applied to the new one. Read /api/state and say the request "
                "again.",
            )
        return pending

    def close(self, token: str | None) -> None:
        if token is not None:
            self._by_token.pop(token, None)


# ---- what the record says about a component --------------------------------


def _component_parents(projection: StateProjection) -> dict[str, str | None]:
    """Every component the record declares, and the component above it.

    The kernel's tree when it built one; the record's own ``Component@1``
    entities when it did not — the same ids either way, and never a name from
    anywhere else. This is the same fallback the record sheet already makes.
    """

    if projection.components is not None:
        return {
            component.component_id: component.parent_component_id
            for component in projection.components
        }
    return {
        entity.entity_id: entity.parent_id
        for entity in projection.record.entities_of("Component@1")
    }


def _descendants(parents: Mapping[str, str | None], component_id: str) -> frozenset[str]:
    """The component and everything under it, however deep."""

    found = {component_id}
    changed = True
    while changed:
        changed = False
        for child, parent in parents.items():
            if parent in found and child not in found:
                found.add(child)
                changed = True
    return frozenset(found)


def editable_descendants(
    projection: StateProjection, component_id: str
) -> tuple[ProjectedElement, ...]:
    """Every element under this component that declares a number to change.

    "Editable" is exactly what the intent grammar can target: an ``Element@1``
    with at least one scalar producer param. An element with only a profile and
    references is in the model and is not a control.
    """

    subtree = _descendants(_component_parents(projection), component_id)
    return tuple(
        element
        for element in projection.elements
        if element.component_id in subtree and element.numeric_fields
    )


def _option(element: ProjectedElement, semantic_property: str | None) -> CandidateOption:
    """One editable element as a choice, with the number it holds now."""

    key = (
        semantic_property
        if semantic_property in element.numeric_fields
        else next(iter(sorted(element.numeric_fields)), None)
    )
    value = element.numeric_fields.get(key) if key is not None else None
    orientation = compass_in(element.element_id)
    where = f" · {orientation}" if orientation else ""
    now = "" if key is None else f" · {key} = {value}"
    return CandidateOption(
        component_id=element.component_id,
        element_id=element.element_id,
        key=key,
        current_value=value,
        unit=None,
        orientation=orientation,
        label=f"{element.element_id}{now}{where}",
    )


def _component_option(
    projection: StateProjection, component_id: str
) -> CandidateOption:
    """A component with nothing editable under it, as a choice that says so."""

    return CandidateOption(
        component_id=component_id,
        element_id=None,
        key=None,
        current_value=None,
        unit=None,
        orientation=compass_in(component_id),
        label=component_id,
    )


def _component_kinds(
    projection: StateProjection, component_id: str
) -> frozenset[str]:
    """Every kind this component's identity and its declared intent name."""

    words = [component_id]
    if projection.components is not None:
        for component in projection.components:
            if component.component_id == component_id:
                words += [component.semantic_kind, component.intent]
                break
    else:
        for entity in projection.record.entities_of("Component@1"):
            if entity.entity_id == component_id:
                words += [
                    str(entity.fields.get("semantic_kind") or ""),
                    str(entity.fields.get("intent") or ""),
                ]
                break
    return kinds_in(" ".join(words))


# ---- the target resolver ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Target:
    component_id: str | None
    element_id: str | None
    how: str
    candidates: tuple[str, ...]


def _refused(element_id: str | None, rejected: Sequence[str]) -> bool:
    """Whether this element is one the architect has already ruled out."""

    if element_id is None:
        return False
    return any(ref.startswith(f"element:{element_id}") for ref in rejected)


def _match_by_id(
    projection: StateProjection, utterance: str, aliases: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Components the request names outright - by identifier, or by a name the
    project's PROJECT.md declares for one - longest identifier first."""

    haystack = _normalised(utterance)
    parents = _component_parents(projection)
    named = [
        component_id
        for component_id in parents
        if f" {_normalised(component_id).strip()} " in haystack
    ]
    lowered = utterance.lower()
    for phrase, component_id in (aliases or {}).items():
        if component_id in parents and component_id not in named and phrase and phrase.lower() in lowered:
            named.append(component_id)
    named.sort(key=len, reverse=True)
    return tuple(named)


def _match_by_kind(
    projection: StateProjection, utterance: str, rejected: Sequence[str]
) -> tuple[str, ...]:
    """Components whose identity carries a kind the request named.

    A request that names no kind matches nothing here: silence is not a match,
    and a resolver that guessed from the nearest string is the whole bug this
    module replaces.
    """

    wanted = kinds_in(utterance)
    if not wanted:
        return ()
    parents = _component_parents(projection)
    matched = [
        component_id
        for component_id in parents
        if wanted & _component_kinds(projection, component_id)
        and f"component:{component_id}" not in rejected
    ]
    if len(matched) <= 1:
        return tuple(matched)
    # The record's own name for a thing outranks a description that merely
    # mentions it. "The columns" matches portico-columns by its identifier and
    # portico-capitals only through the prose of its intent; the first is what
    # the word names and the second is what the word was used about.
    named = [
        component_id
        for component_id in matched
        if wanted & kinds_in(component_id)
    ]
    if len(named) == 1:
        return tuple(named)
    matched = named or matched
    # 柱廊 names the portico, not one of its parts: when one match is above all
    # the others, it is the one the word means.
    for component_id in matched:
        if set(matched) <= _descendants(parents, component_id):
            return (component_id,)
    return tuple(sorted(matched))


def _resolve_target(
    projection: StateProjection,
    *,
    utterance: str,
    selection: Selection,
    picked: Selection | None,
    pending: PendingIntent | None,
    rejected: Sequence[str],
    aliases: Mapping[str, str] | None = None,
) -> _Target:
    """Which thing the request is about, from the tree and an explicit choice.

    The priority, and the last string that happened to look similar is nowhere
    in it:

    1. an explicit ``elementId`` — a pick, or a choice out of the picker;
    2. the object→element binding a gesture resolved through the record;
    3. a component the words name outright, by identifier;
    4. the current selection, when the words do not contradict its kind;
    5. one component in the tree whose kind the words name;
    6. the corrected target of the pending intent;
    7. several such components, which is a question with real options;
    8. nothing, which is a question and never a guess.

    A correction jumps the queue. "No — the columns" exists precisely to
    override what the last round settled on, so the words win outright and the
    thing they replaced is written into ``rejectedCandidates``.
    """

    named_ids = _match_by_id(projection, utterance, aliases)
    by_kind = _match_by_kind(projection, utterance, rejected)
    correcting = pending is not None and is_correction(utterance)

    if correcting and named_ids:
        return _Target(named_ids[0], None, "named", named_ids)
    if correcting and by_kind:
        return _Target(
            by_kind[0] if len(by_kind) == 1 else None, None, "kind", by_kind
        )

    if selection.element_id is not None and not _refused(selection.element_id, rejected):
        # A pick outranks everything except a correction — but not a pick the
        # architect has already refused. A client whose selection is still on
        # the old thing (because the last answer resolved no target) must not
        # be able to put it back on the table.
        return _Target(selection.component_id, selection.element_id, "picked", ())
    if (
        picked is not None
        and picked.component_id is not None
        and not _refused(picked.element_id, rejected)
    ):
        return _Target(picked.component_id, picked.element_id, "gesture", ())
    if named_ids:
        return _Target(named_ids[0], None, "named", named_ids)
    if selection.component_id is not None:
        # A selection is an explicit choice and outranks a match in the tree —
        # but only where the words do not contradict it. The architect who says
        # "the columns" with the roofs selected means the columns, and a
        # resolver that kept the roofs is the bug this module replaces.
        wanted = kinds_in(utterance)
        carried = _component_kinds(projection, selection.component_id)
        if (
            not wanted or wanted & carried
        ) and f"component:{selection.component_id}" not in rejected:
            return _Target(selection.component_id, None, "selection", ())
    if len(by_kind) == 1:
        return _Target(by_kind[0], None, "kind", by_kind)
    if (
        pending is not None
        and pending.target_component_id is not None
        and f"component:{pending.target_component_id}" not in rejected
    ):
        return _Target(pending.target_component_id, pending.element_id, "pending", ())
    if by_kind:
        return _Target(None, None, "kind", by_kind)
    return _Target(None, None, "none", ())


# ---- the answers ------------------------------------------------------------


def _slots(
    *,
    utterance: str,
    semantic_property: str | None,
    has_camera: bool,
    camera: Mapping[str, object] | None = None,
    compass_axes: Mapping[str, Sequence[float]] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """What the request already says, and what it still leaves open."""

    known: dict[str, str] = {}
    missing: list[str] = []
    if semantic_property is not None:
        known[SLOT_PROPERTY] = semantic_property
    compass = compass_in(utterance)
    side = viewer_side_in(utterance)
    if compass is not None:
        known[SLOT_ORIENTATION] = compass
    elif side is not None and camera is not None and compass_axes is not None:
        # "Left" read from where the camera stands, against the project's compass:
        # it becomes a side the record can carry. Undecidable (the camera looks
        # along the axis) stays a slot.
        cardinal = compass_side(side, camera, compass_axes)
        if cardinal is not None:
            known[SLOT_ORIENTATION] = cardinal
        else:
            missing.append(SLOT_ORIENTATION)
    elif side is not None and has_camera:
        known[SLOT_ORIENTATION] = side
    elif side is not None:
        # "Left" is relative to a viewpoint and the record holds none. It is a
        # slot the architect fills, and it narrows nothing until they do.
        missing.append(SLOT_ORIENTATION)
    return known, missing


def _pending(
    *,
    state_digest: str | None,
    original_utterance: str,
    action_kind: str,
    target_component_id: str | None,
    element_id: str | None,
    semantic_property: str | None,
    known: Mapping[str, str],
    missing: Sequence[str],
    candidates: Sequence[CandidateOption],
    rejected: Sequence[str],
    reason_code: str,
    terminal: bool,
    previous: PendingIntent | None,
) -> PendingIntent:
    return PendingIntent(
        request_id=(
            previous.request_id if previous is not None else f"intent-{uuid4().hex[:12]}"
        ),
        state_digest=state_digest or "",
        original_utterance=original_utterance,
        action_kind=action_kind,
        target_component_id=target_component_id,
        element_id=element_id,
        requested_semantic_property=semantic_property,
        known_slots=dict(known),
        missing_slots=tuple(missing),
        candidates=tuple(candidates),
        rejected_candidates=tuple(rejected),
        reason_code=reason_code,
        continuation_token=None if terminal else f"pi-{uuid4().hex}",
        turn=1 if previous is None else previous.turn + 1,
    )


def _draft(
    projection: StateProjection,
    *,
    component_id: str,
    semantic_property: str | None,
    catalog_status: str | None = None,
    object_names: tuple[str, ...] = (),
) -> AuthoredControlDraft:
    """A control this component would need, read off the elements around it.

    The producer, the binding and the unit are taken from the elements the
    record already declares nearest this component — its siblings under the same
    parent, else anything with the same property. ``provenance`` names exactly
    which ones were read, and ``confidence`` says how much agreement there was,
    so nobody mistakes a suggestion for a measurement.
    """

    parents = _component_parents(projection)
    parent = parents.get(component_id)
    siblings = (
        frozenset(
            child for child, above in parents.items() if above == parent and child != component_id
        )
        if parent is not None
        else frozenset()
    )
    nearby = [
        element
        for element in projection.elements
        if element.component_id in siblings
        or (parent is not None and element.component_id == parent)
    ]
    with_property = [
        element
        for element in nearby
        if semantic_property is not None and semantic_property in element.numeric_fields
    ]
    read_from = with_property or nearby
    producers = sorted({element.producer for element in read_from})
    provenance = tuple(
        f"element:{element.element_id} (producer {element.producer})"
        for element in read_from
    ) + tuple(f"object:{name}" for name in object_names[:12])
    requirements: list[str] = [
        f"an Element@1 under component {component_id}, with a producer and a base reference",
    ]
    if semantic_property is None:
        requirements.append(
            "the semantic property this control stands for, which the request "
            "did not name"
        )
    if not read_from:
        requirements.append(
            "a producer and a base reference, which no element near this "
            "component declares for the studio to read"
        )
    confidence = (
        "high"
        if len(with_property) >= 2 and len(producers) == 1
        else "medium"
        if with_property
        else "low"
    )
    return AuthoredControlDraft(
        target_component_id=component_id,
        suggested_element_id=f"{component_id}-control",
        semantic_property=semantic_property,
        producer=producers[0] if len(producers) == 1 else None,
        binding=(
            f"the base reference {read_from[0].element_id} declares"
            if read_from
            else None
        ),
        # Element params are bare numbers in the record: this seam converts
        # nothing and states no unit the record does not.
        unit=None,
        provenance=provenance,
        confidence=confidence,
        dependency_requirements=tuple(requirements),
        suggested_action=(
            f"author a control for {component_id} from the model that already "
            "exists, then confirm it; nothing is written until somebody does"
        ),
        catalog_status=catalog_status,
        object_names=object_names,
    )


_INCREASE_WORDS = ("提高", "升高", "加高", "抬高", "增高", "加大", "增加", "raise", "increase", "taller", "higher", "up by", "longer", "wider", "thicker")
_DECREASE_WORDS = ("降低", "减低", "压低", "缩短", "减小", "减少", "lower", "decrease", "shorter", "reduce", "down by", "thinner", "narrower")
_DELTA = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mm|cm|m|毫米|厘米|米)(?![a-z])", re.I)
_UNIT_TO_M = {"mm": 0.001, "毫米": 0.001, "cm": 0.01, "厘米": 0.01, "m": 1.0, "米": 1.0}


def grammar_sentence_for(utterance: str, *, resolution: Resolution, projection: StateProjection) -> str | None:
    """The grammar sentence an absolute delta means, when the request already resolved to one
    element and one numeric field: "提高 0.1m" on a height of 9.798 is ``set height to 9.898``.

    Only a number with a length unit and a direction word is read; "略微" or a bare "提高" is
    not a number and stays the value slot the architect fills. Element params are bare numbers
    in metres (the record's unit), so mm and cm are converted and a bare "m" is taken as is.
    """

    pending = resolution.pending
    element_id = pending.element_id
    key = pending.requested_semantic_property
    if element_id is None or key is None:
        return None
    match = _DELTA.search(utterance)
    if match is None:
        return None
    lowered = utterance.lower()
    if any(w in lowered for w in _INCREASE_WORDS):
        sign = 1.0
    elif any(w in lowered for w in _DECREASE_WORDS):
        sign = -1.0
    else:
        return None
    element = next((e for e in projection.elements if e.element_id == element_id), None)
    if element is None or key not in element.numeric_fields:
        return None
    old = element.numeric_fields[key]
    delta = float(match.group(1)) * _UNIT_TO_M[match.group(2).lower()]
    new = round(float(old) + sign * delta, 6)
    return f"set {key} to {new}"


def _editable_by_catalog(projection: StateProjection, component_id: str, catalog: object | None):
    """The editable descendants of a component: the catalog's when one is given (the one
    directory), else the projection's own reading."""

    if catalog is None:
        return editable_descendants(projection, component_id)
    try:
        ids = {element.element_id for element in catalog.editable_descendants(component_id)}  # type: ignore[attr-defined]
    except AttributeError:
        return editable_descendants(projection, component_id)
    return [element for element in projection.elements if element.element_id in ids]


def _catalog_says(catalog: object | None, component_id: str) -> tuple[str | None, tuple[str, ...]]:
    """What the catalog shows for a component with no editable element: the objects the model
    has of it (MODEL_VISIBLE_CATALOG_MISSING) or nothing at all (DECLARED_ONLY)."""

    if catalog is None:
        return None, ()
    try:
        component = catalog.component(component_id)  # type: ignore[attr-defined]
        if component is None:
            return None, ()
        subtree = {component_id}
        frontier = list(component.children)
        while frontier:
            child = frontier.pop()
            if child in subtree:
                continue
            subtree.add(child)
            node = catalog.component(child)  # type: ignore[attr-defined]
            if node is not None:
                frontier.extend(node.children)
        names = tuple(sorted(o.name for o in catalog.objects if o.component_id in subtree))  # type: ignore[attr-defined]
    except AttributeError:
        return None, ()
    if names:
        return "MODEL_VISIBLE_CATALOG_MISSING", names
    return "DECLARED_ONLY", ()


def _missing_control(
    projection: StateProjection,
    *,
    component_id: str,
    semantic_property: str | None,
    action_kind: str,
    reason_code: str,
    utterance: str,
    known: Mapping[str, str],
    rejected: Sequence[str],
    previous: PendingIntent | None,
    state_digest: str | None,
    neighbour: str | None,
    catalog: object | None = None,
) -> Resolution:
    """The terminal answer: it is in the model and it has no control.

    The sentence says what is missing — a binding the system does not have —
    and, where a neighbouring element carries a field of the same name, says out
    loud that it is *not* a substitute. That neighbour is exactly what a
    resolver guessing from the nearest string would have proposed.
    """

    property_said = semantic_property or "the property the request named"
    if reason_code == CONTROL_MUST_BE_AUTHORED:
        detail = (
            f"declaring a control on {component_id} is not a change of value "
            "and does not go through the intent grammar. What comes back is a "
            "draft of the control somebody would have to author; nothing is "
            "written, and no number is invented."
        )
    else:
        detail = (
            f"component {component_id} is declared by the record and has no "
            f"editable control under it: no Element@1 beneath it declares a "
            f"numeric field, so there is no {property_said} to change. This is "
            "a missing system binding, not a missing answer from you."
        )
    if neighbour is not None:
        detail += (
            f" {neighbour} carries a field of that name and belongs to another "
            "component; it is not a substitute and this seam will not offer it."
        )
    catalog_status, object_names = _catalog_says(catalog, component_id)
    if catalog_status == "MODEL_VISIBLE_CATALOG_MISSING":
        detail += (
            f" The model shows {len(object_names)} object(s) of {component_id} that no "
            "Element@1 row produced (MODEL_VISIBLE_CATALOG_MISSING): re-index the model "
            "or author the control."
        )
    pending = _pending(
        state_digest=state_digest,
        original_utterance=utterance,
        action_kind=action_kind,
        target_component_id=component_id,
        element_id=None,
        semantic_property=semantic_property,
        known=known,
        missing=(),
        candidates=(),
        rejected=rejected,
        reason_code=reason_code,
        terminal=True,
        previous=previous,
    )
    return Resolution(
        outcome=MISSING_EDITABLE_CONTROL,
        pending=pending,
        selection=None,
        question=None,
        detail=detail,
        draft=_draft(
            projection,
            component_id=component_id,
            semantic_property=semantic_property,
            catalog_status=catalog_status,
            object_names=object_names,
        ),
    )


def _neighbour_with(
    projection: StateProjection,
    *,
    component_id: str,
    semantic_property: str | None,
    rejected: Sequence[str] = (),
) -> str | None:
    """The element somewhere else that carries a field of the same name.

    Named only to be refused. A resolver that scored on field names would have
    changed this one, so the one worth naming is the nearest: an element the
    architect has already rejected, else one under a sibling component, else
    any. This is the sentence that says the abutment's ``height`` is not the
    colonnade's.
    """

    if semantic_property is None:
        return None
    parents = _component_parents(projection)
    subtree = _descendants(parents, component_id)
    parent = parents.get(component_id)
    siblings = frozenset(
        child for child, above in parents.items() if above == parent and child != component_id
    )
    outside = [
        element
        for element in projection.elements
        if element.component_id not in subtree
        and semantic_property in element.numeric_fields
    ]
    if not outside:
        return None
    ranked = sorted(
        outside,
        key=lambda element: (
            0
            if any(
                ref.startswith(f"element:{element.element_id}") for ref in rejected
            )
            else 1 if element.component_id in siblings else 2,
            element.element_id,
        ),
    )
    return f"element {ranked[0].element_id}.{semantic_property}"


def _rejected_after(
    pending: PendingIntent | None, utterance: str
) -> tuple[str, ...]:
    """What the architect has refused, kept for the life of this pending intent.

    A correction refuses the target that was on the table *and* every candidate
    that was offered with it. Nothing ever leaves this list while the pending
    intent lives, which is what stops the same wrong answer coming back.
    """

    if pending is None:
        return ()
    rejected = list(pending.rejected_candidates)
    if not is_correction(utterance):
        return tuple(rejected)
    refused: list[str] = []
    if pending.target_component_id is not None:
        refused.append(f"component:{pending.target_component_id}")
    refused += [option.ref for option in pending.candidates]
    if pending.element_id is not None:
        refused.append(f"element:{pending.element_id}")
    for ref in refused:
        if ref not in rejected:
            rejected.append(ref)
    return tuple(rejected)


def resolve(
    projection: StateProjection,
    *,
    utterance: str,
    selection: Selection,
    picked: Selection | None = None,
    has_camera: bool = False,
    pending: PendingIntent | None = None,
    camera: Mapping[str, object] | None = None,
    compass: Mapping[str, Sequence[float]] | None = None,
    aliases: Mapping[str, str] | None = None,
    catalog: object | None = None,
) -> Resolution:
    """What this request is, before anyone tries to compile it.

    ``camera`` (the request's) and ``compass`` (PROJECT.md's) read a viewer word
    into a side; ``aliases`` (PROJECT.md's names) name components outright;
    ``catalog`` (the studio's derived catalog) is the one directory of editable
    elements and of what the model shows without a row.

    Three of the four answers are reached here, without a model: an action no
    grammar expresses, a target the record cannot resolve, and a component whose
    control does not exist. Only a request that survives all three is handed to
    a compiler, and it is handed the *resolved* target — which is what makes a
    correction atomic rather than a suggestion the next round may ignore.
    """

    rejected = _rejected_after(pending, utterance)
    semantic_property = property_in(utterance) or (
        pending.requested_semantic_property if pending is not None else None
    )
    known, missing = _slots(
        utterance=utterance,
        semantic_property=semantic_property,
        has_camera=has_camera or camera is not None,
        camera=camera,
        compass_axes=compass,
    )
    target = _resolve_target(
        projection,
        utterance=utterance,
        selection=selection,
        picked=picked,
        pending=pending,
        rejected=rejected,
        aliases=aliases,
    )
    declaring = declares_a_control(utterance) or (
        pending is not None and pending.action_kind == DECLARE_MISSING_CONTROL
    )
    action_kind = (
        DECLARE_MISSING_CONTROL
        if declaring
        else CHANGE_EXISTING_VALUE
        if parse_utterance(utterance) is not None
        else CLARIFY
    )

    if target.component_id is None:
        return _unresolved(
            projection,
            utterance=utterance,
            action_kind=action_kind,
            semantic_property=semantic_property,
            known=known,
            missing=missing,
            candidates=target.candidates,
            rejected=rejected,
            previous=pending,
            state_digest=projection.state_digest,
        )

    # "Supply the field" is never a value change and never enters the scalar
    # grammar: it is a request for a control to exist, and the only thing it can
    # produce is a draft somebody confirms.
    if declaring:
        return _missing_control(
            projection,
            component_id=target.component_id,
            semantic_property=semantic_property,
            action_kind=DECLARE_MISSING_CONTROL,
            reason_code=CONTROL_MUST_BE_AUTHORED,
            utterance=(
                pending.original_utterance if pending is not None else utterance
            ),
            known=known,
            rejected=rejected,
            previous=pending,
            state_digest=projection.state_digest,
            neighbour=_neighbour_with(
                projection,
                component_id=target.component_id,
                semantic_property=semantic_property,
                rejected=rejected,
            ),
            catalog=catalog,
        )

    editable = _editable_by_catalog(projection, target.component_id, catalog)
    if not editable:
        return _advance_or(
            pending,
            _missing_control(
                projection,
                component_id=target.component_id,
                semantic_property=semantic_property,
                action_kind=action_kind,
                reason_code=COMPONENT_HAS_NO_EDITABLE_ELEMENT,
                utterance=(
                    pending.original_utterance if pending is not None else utterance
                ),
                known=known,
                rejected=rejected,
                previous=pending,
                state_digest=projection.state_digest,
                neighbour=_neighbour_with(
                    projection,
                    component_id=target.component_id,
                    semantic_property=semantic_property,
                    rejected=rejected,
                ),
                catalog=catalog,
            ),
        )

    allowed = [
        element
        for element in editable
        if f"element:{element.element_id}" not in rejected
        and not any(
            ref.startswith(f"element:{element.element_id}.") for ref in rejected
        )
    ]
    # An orientation the request settled narrows the candidates to the elements
    # that carry that side in their own identity; it never introduces one.
    wanted_side = known.get(SLOT_ORIENTATION)
    if wanted_side in ("north", "east", "south", "west") and len(allowed) > 1:
        sided = [element for element in allowed if compass_in(element.element_id) == wanted_side]
        if sided:
            allowed = sided
    if not allowed:
        return _advance_or(
            pending,
            _missing_control(
                projection,
                component_id=target.component_id,
                semantic_property=semantic_property,
                action_kind=action_kind,
                reason_code=COMPONENT_HAS_NO_SUCH_CONTROL,
                utterance=(
                    pending.original_utterance if pending is not None else utterance
                ),
                known=known,
                rejected=rejected,
                previous=pending,
                state_digest=projection.state_digest,
                neighbour=None,
                catalog=catalog,
            ),
        )

    element_id = target.element_id
    component_id = target.component_id
    if element_id is None and len(allowed) == 1:
        # One legal editable descendant is not a choice: it is the answer. The
        # component moves with it — the element belongs to whatever component
        # declares it, which may be a level or two down, and a proposal whose
        # component and element disagree is refused by the grammar as two
        # selections rather than one narrower one.
        element_id = allowed[0].element_id
        component_id = allowed[0].component_id
    candidates = tuple(_option(element, semantic_property) for element in allowed)
    pending_now = _pending(
        state_digest=projection.state_digest,
        original_utterance=(
            pending.original_utterance if pending is not None else utterance
        ),
        action_kind=action_kind,
        target_component_id=component_id,
        element_id=element_id,
        semantic_property=semantic_property,
        known=known,
        missing=missing,
        candidates=candidates,
        rejected=rejected,
        reason_code=COMPILED_CLEANLY,
        terminal=True,
        previous=pending,
    )
    return Resolution(
        outcome=COMPILED,
        pending=pending_now,
        selection=Selection(
            component_id=component_id,
            element_id=element_id,
            gestures=selection.gestures,
        ),
        question=None,
        detail="",
    )


def _unresolved(
    projection: StateProjection,
    *,
    utterance: str,
    action_kind: str,
    semantic_property: str | None,
    known: Mapping[str, str],
    missing: Sequence[str],
    candidates: Sequence[str],
    rejected: Sequence[str],
    previous: PendingIntent | None,
    state_digest: str | None,
) -> Resolution:
    """Nothing in the record answers to the words: a question with real options.

    The options are components and their current numbers, never an internal
    identifier the architect is asked to supply. A studio that asks a person for
    an ``elementId`` is asking them to do its own resolution.
    """

    options = [_component_option(projection, component_id) for component_id in candidates]
    slots = list(missing)
    if SLOT_TARGET not in slots:
        slots.insert(0, SLOT_TARGET)
    reason = TARGET_AMBIGUOUS if len(options) > 1 else TARGET_UNRESOLVED
    if options:
        question = (
            "Which one did you mean? " + ", ".join(option.label for option in options)
        )
    else:
        named = ", ".join(sorted(_component_parents(projection))[:8])
        question = (
            "Which part of the building did you mean? The record declares "
            f"{named}."
        )
    pending = _pending(
        state_digest=state_digest,
        original_utterance=(
            previous.original_utterance if previous is not None else utterance
        ),
        action_kind=action_kind,
        target_component_id=None,
        element_id=None,
        semantic_property=semantic_property,
        known=known,
        missing=slots,
        candidates=options,
        rejected=rejected,
        reason_code=reason,
        terminal=False,
        previous=previous,
    )
    return _advance_or(
        previous,
        Resolution(
            outcome=NEEDS_CLARIFICATION,
            pending=pending,
            selection=None,
            question=question,
            detail=(
                "the request names nothing this record declares, and the studio "
                "will not resolve it by the nearest similar name"
            ),
        ),
    )


def clarify(
    resolution: Resolution,
    *,
    pending: PendingIntent | None,
    reason_code: str,
    question: str,
    detail: str,
    accepted_forms: Sequence[str] = (),
    missing: Sequence[str] = (SLOT_VALUE,),
    target_component_id: str | None = None,
    element_id: str | None = None,
) -> Resolution:
    """The compiler could not finish: ask, or stop if asking has stopped working.

    The slots the resolver already found open travel with the new one. A round
    that answered the value and left the orientation still open has advanced;
    a question that quietly forgot the orientation would look like progress and
    would come back around.
    """

    resolved = resolution.pending
    still_open = [slot for slot in resolved.missing_slots if slot not in missing]
    missing = tuple(missing) + tuple(still_open)
    asked = _pending(
        state_digest=resolved.state_digest,
        original_utterance=resolved.original_utterance,
        action_kind=resolved.action_kind,
        target_component_id=(
            target_component_id
            if target_component_id is not None
            else resolved.target_component_id
        ),
        element_id=element_id if element_id is not None else resolved.element_id,
        semantic_property=resolved.requested_semantic_property,
        known=resolved.known_slots,
        missing=missing,
        candidates=resolved.candidates,
        rejected=resolved.rejected_candidates,
        reason_code=reason_code,
        terminal=False,
        previous=pending,
    )
    return _advance_or(
        pending,
        Resolution(
            outcome=NEEDS_CLARIFICATION,
            pending=asked,
            selection=None,
            question=question,
            detail=detail,
            accepted_forms=tuple(accepted_forms),
        ),
    )


def _advance_or(previous: PendingIntent | None, answer: Resolution) -> Resolution:
    """The one rule that ends the loop.

    A reply that leaves the target, the missing slots and the candidates exactly
    where they were has not advanced anything, and asking it again is the loop.
    So the exchange terminates instead, saying what the system is missing rather
    than repeating a question the architect has already answered.
    """

    if previous is None or answer.terminal:
        return answer
    if answer.pending.advance_key != previous.advance_key:
        return answer
    stalled = PendingIntent(
        request_id=answer.pending.request_id,
        state_digest=answer.pending.state_digest,
        original_utterance=answer.pending.original_utterance,
        action_kind=UNSUPPORTED_ACTION,
        target_component_id=answer.pending.target_component_id,
        element_id=answer.pending.element_id,
        requested_semantic_property=answer.pending.requested_semantic_property,
        known_slots=dict(answer.pending.known_slots),
        missing_slots=answer.pending.missing_slots,
        candidates=answer.pending.candidates,
        rejected_candidates=answer.pending.rejected_candidates,
        reason_code=CLARIFICATION_MADE_NO_PROGRESS,
        continuation_token=None,
        turn=answer.pending.turn,
    )
    still = ", ".join(stalled.missing_slots) or "the request itself"
    return Resolution(
        outcome=UNSUPPORTED,
        pending=stalled,
        selection=None,
        question=None,
        detail=(
            f"this reply left the request exactly where the last one did: {still} "
            "is still open, the target has not changed and no candidate was "
            "ruled out. Rather than ask the same question a third time: the "
            "studio cannot get from these words to a change this record can "
            "type. What it is missing is "
            + (
                f"a value for {stalled.requested_semantic_property}"
                if SLOT_VALUE in stalled.missing_slots
                and stalled.requested_semantic_property
                else "a target it can name in the record"
                if SLOT_TARGET in stalled.missing_slots
                else "an action its grammar can express"
            )
            + "."
        ),
    )


def read_compilation(
    projection: StateProjection,
    *,
    compilation: Compilation,
    resolution: Resolution,
    pending: PendingIntent | None,
) -> Resolution:
    """What the compiler's answer means, in the same four outcomes.

    The three things this fixes, all of which the old ``require_grammatical``
    threw away: an agent that asks still *names* a target, and that target is
    kept; an agent that names a component with no editable control gets the
    terminal answer rather than a question the architect cannot answer; and a
    round that changed nothing terminates instead of asking again.
    """

    component_id = compilation.component_id or resolution.pending.target_component_id
    element_id = (
        compilation.element_id
        if compilation.component_id is not None
        else resolution.pending.element_id
    )
    if (
        component_id is not None
        and component_id in _component_parents(projection)
        and not editable_descendants(projection, component_id)
    ):
        return _missing_control(
            projection,
            component_id=component_id,
            semantic_property=resolution.pending.requested_semantic_property,
            action_kind=resolution.pending.action_kind,
            reason_code=COMPONENT_HAS_NO_EDITABLE_ELEMENT,
            utterance=resolution.pending.original_utterance,
            known=resolution.pending.known_slots,
            rejected=resolution.pending.rejected_candidates,
            previous=pending,
            state_digest=projection.state_digest,
            neighbour=_neighbour_with(
                projection,
                component_id=component_id,
                semantic_property=resolution.pending.requested_semantic_property,
                rejected=resolution.pending.rejected_candidates,
            ),
        )
    if compilation.status != "compiled":
        return clarify(
            resolution,
            pending=pending,
            reason_code=AGENT_ASKED,
            question=(
                compilation.question or "the agent asked a question it did not state"
            ),
            detail=(
                f"the {compilation.provider} agent asked instead of compiling"
                + (f": {compilation.why}" if compilation.why else "")
            ),
            missing=(SLOT_VALUE,),
            target_component_id=component_id,
            element_id=element_id,
        )
    assert compilation.utterance is not None
    if parse_utterance(compilation.utterance) is None:
        return clarify(
            resolution,
            pending=pending,
            reason_code=VALUE_UNRESOLVED,
            question=(
                "The agent's sentence could not be typed. Say the change in one "
                "of the four forms, or rephrase the request."
            ),
            detail=(
                f"the {compilation.provider} agent compiled "
                f"{compilation.utterance!r}, which is not in the grammar"
                + (f"; it said: {compilation.why}" if compilation.why else "")
            ),
            accepted_forms=ACCEPTED_FORMS,
            missing=(SLOT_VALUE,),
            target_component_id=component_id,
            element_id=element_id,
        )
    return Resolution(
        outcome=COMPILED,
        pending=resolution.pending,
        selection=resolution.selection,
        question=None,
        detail="",
    )


def blocked(
    detail: str,
    *,
    question: str,
    resolution: Resolution,
    pending: PendingIntent | None,
    accepted_forms: Sequence[str] = (),
) -> Resolution:
    """A refusal the grammar raised, given the pending intent it belongs to.

    The grammar refuses for reasons only it knows — a locked parameter, a unit
    it will not convert, an element of another component. Those sentences are
    the record's and travel verbatim; what they were missing was the structure
    saying which exchange they belong to and whether it is still advancing.
    """

    return clarify(
        resolution,
        pending=pending,
        reason_code=REQUEST_NOT_EXPRESSIBLE,
        question=question,
        detail=detail,
        accepted_forms=accepted_forms,
        missing=(SLOT_VALUE,),
    )
