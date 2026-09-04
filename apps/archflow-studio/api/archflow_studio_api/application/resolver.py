"""The two-stage intent resolver: the target first, the action second, one
closed result.

Stage one answers *what* the sentence is about, in this order and never by
guessing across it: the element the request names explicitly; the object it
picked or circled, through the catalog's binding; the component it chose,
through the catalog's editable descendants (one lands, several are named with
their values and sides, none is a named gap); a semantic alias the record or
the registry declares; and last a direction word - a cardinal word matches an
element's own directional identity, "left" and "right" only when the request
recorded a camera and the project declared its compass. String similarity
across components is not in this list: "columns" never becomes an abutment.

Stage two answers *which act*: change an existing value, declare a missing
control, clarify, or say it is unsupported. The words are read by the grammar
first and an agent second, and whatever an agent answers is re-validated here
against the catalog: a capability it names must exist and be editable, and a
qualitative amount ("a little", "略微") is a question, never a percentage
somebody assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Mapping, Sequence

from archflow.semantics.registry import resolve_semantic_kind
from archflow.semantics.roles import ROLES

from .catalog import (
    EDITABLE,
    MODEL_VISIBLE_CATALOG_MISSING,
    Catalog,
    CatalogElement,
)
from .intent import parse_utterance
from .intent_agent import Selection
from .pending import (
    AMBIGUOUS_TARGET,
    MISSING_AMOUNT,
    MISSING_ELEMENT_DECLARATION,
    MISSING_PROPERTY,
    MISSING_TARGET,
    UNSUPPORTED_ACTION,
    UNSUPPORTED_ADD_FIELD,
)

# ---- target vocabulary

RESOLVED = "resolved"
CANDIDATES = "candidates"
MISSING = "missing"
NONE = "none"

SOURCE_ELEMENT = "element"
SOURCE_OBJECT = "object"
SOURCE_GESTURE = "gesture"
SOURCE_COMPONENT = "component"
SOURCE_ALIAS = "alias"
SOURCE_DIRECTION = "direction"

# ---- action vocabulary (closed)

CHANGE_EXISTING_VALUE = "change_existing_value"
DECLARE_MISSING_CONTROL = "declare_missing_control"
CLARIFY = "clarify"
UNSUPPORTED = "unsupported"

SIDES = ("west", "east", "north", "south")
_CARDINAL_WORDS: dict[str, tuple[str, ...]] = {
    "west": ("west", "西", "西侧", "西面", "西边"),
    "east": ("east", "东", "东侧", "东面", "东边"),
    "north": ("north", "北", "北侧", "北面", "北边"),
    "south": ("south", "南", "南侧", "南面", "南边"),
}
_RELATIVE_WORDS: dict[str, tuple[str, ...]] = {
    "left": ("left", "左", "左侧", "左边", "左面"),
    "right": ("right", "右", "右侧", "右边", "右面"),
    "front": ("front", "前", "前面", "前侧"),
    "back": ("back", "behind", "后", "后面", "后侧"),
}
_QUALITATIVE = (
    "略微", "稍微", "稍稍", "一点", "一点点", "一些", "少许", "微微", "略",
    "a little", "a bit", "slightly", "somewhat", "a touch", "a tad", "little",
)
_INCREASE = ("提高", "抬高", "加高", "升高", "增高", "增加", "加大", "放大", "拉高", "raise", "taller", "higher", "increase", "more", "bigger", "larger", "up")
_DECREASE = ("降低", "压低", "减小", "缩小", "减少", "矮", "lower", "shorter", "decrease", "less", "smaller", "reduce", "down")
_PROPERTY_WORDS: dict[str, tuple[str, ...]] = {
    "height": ("height", "tall", "taller", "high", "higher", "高", "高度", "提高", "抬高", "加高", "升高", "增高", "压低", "降低", "矮"),
    "thickness": ("thickness", "thick", "thicker", "thin", "厚", "厚度", "加厚", "减薄"),
    "radius": ("radius", "半径", "粗", "粗细", "diameter", "直径"),
    "depth": ("depth", "深", "深度"),
    "rise": ("rise", "起拱", "坡"),
}
_UNITS = {"m": "m", "米": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m", "mm": "mm", "毫米": "mm", "cm": "cm", "厘米": "cm", "%": "%", "percent": "%", "％": "%"}


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    element_id: str
    component_id: str
    keys: tuple[str, ...]
    values: Mapping[str, int | float]
    side: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "elementId": self.element_id,
            "componentId": self.component_id,
            "keys": list(self.keys),
            "values": dict(self.values),
            "side": self.side,
        }


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    status: str
    component_id: str | None
    element_id: str | None
    candidates: tuple[TargetCandidate, ...]
    reason_code: str | None
    detail: str
    source: str | None


@dataclass(frozen=True, slots=True)
class IntentResult:
    """One closed answer about the sentence; the shell renders it, never re-reads it."""

    kind: str  # CHANGE_EXISTING_VALUE | DECLARE_MISSING_CONTROL | CLARIFY | UNSUPPORTED
    target: ResolvedTarget
    # change_existing_value: the sentence in the grammar the record will type
    utterance: str | None = None
    capability_id: str | None = None
    # clarify: what is still missing and what may be chosen from
    missing_slots: tuple[str, ...] = ()
    reason_code: str | None = None
    question: str | None = None
    # declare_missing_control
    requested_property: str | None = None
    why: str = ""
    slots: Mapping[str, Any] = field(default_factory=dict)


# ---- stage one: the target


def side_of(text: str) -> str | None:
    """The cardinal side an id or object name carries as its own identity."""

    lowered = text.lower()
    for side in SIDES:
        if re.search(rf"(^|[-_ ]){side}([-_ ]|$)", lowered):
            return side
    return None


def cardinal_in(utterance: str) -> str | None:
    lowered = utterance.lower()
    for side, words in _CARDINAL_WORDS.items():
        if any(_word_in(word, lowered) for word in words):
            return side
    return None


def relative_in(utterance: str) -> str | None:
    lowered = utterance.lower()
    for word_side, words in _RELATIVE_WORDS.items():
        if any(_word_in(word, lowered) for word in words):
            return word_side
    return None


def _word_in(word: str, lowered: str) -> bool:
    if re.search(r"[一-鿿]", word):
        return word in lowered
    return re.search(rf"(^|[^a-z]){re.escape(word)}([^a-z]|$)", lowered) is not None


def compass_side(relative: str, camera: Mapping[str, Any] | None, compass: Mapping[str, Sequence[float]] | None) -> str | None:
    """Which cardinal side 'left' (etc.) is from where the camera stands.

    Needs both a camera and the project's compass (which world direction is
    north). Without either the word cannot be read, and the resolver asks.
    """

    if camera is None or compass is None:
        return None
    try:
        position = camera["position"]
        target = camera["target"]
        view = (float(target[0]) - float(position[0]), float(target[1]) - float(position[1]))
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
    wanted = vectors[relative]
    best: tuple[float, str] | None = None
    for side in SIDES:
        axis = compass.get(side)
        if axis is None:
            continue
        ax = (float(axis[0]), float(axis[1]))
        norm = math.hypot(*ax)
        if norm == 0:
            continue
        score = (ax[0] * wanted[0] + ax[1] * wanted[1]) / norm
        if best is None or score > best[0]:
            best = (score, side)
    return best[1] if best is not None and best[0] > 0.5 else None


def _candidate(catalog: Catalog, element: CatalogElement) -> TargetCandidate:
    editable = [cap for cap in element.capabilities if cap.status == EDITABLE]
    side = side_of(element.element_id)
    if side is None:
        for name in element.object_names:
            side = side_of(name)
            if side is not None:
                break
    return TargetCandidate(
        element_id=element.element_id,
        component_id=element.component_id,
        keys=tuple(cap.key for cap in editable),
        values={cap.key: cap.value for cap in editable},
        side=side,
    )


def side_in(utterance: str, camera: Mapping[str, Any] | None, compass: Mapping[str, Sequence[float]] | None) -> str | None:
    """The side a sentence names: a cardinal word, or a relative word read from the camera."""

    wanted = cardinal_in(utterance)
    if wanted is not None:
        return wanted
    relative = relative_in(utterance)
    if relative is None:
        return None
    return compass_side(relative, camera, compass)


def _component_target(
    catalog: Catalog,
    component_id: str,
    *,
    utterance: str,
    camera: Mapping[str, Any] | None,
    compass: Mapping[str, Sequence[float]] | None,
    rejected: Sequence[str],
    source: str,
    side_hint: str | None = None,
) -> ResolvedTarget:
    node = catalog.component(component_id)
    if node is None:
        return ResolvedTarget(NONE, None, None, (), MISSING_TARGET, f"{component_id} is not a component of this record", None)
    candidates = [
        _candidate(catalog, element)
        for element in catalog.editable_descendants(component_id)
        if element.element_id not in rejected
    ]
    if len(candidates) == 1:
        one = candidates[0]
        return ResolvedTarget(RESOLVED, one.component_id, one.element_id, (one,), None, f"{component_id} has one editable element, {one.element_id}", source)
    if len(candidates) > 1:
        wanted = cardinal_in(utterance)
        how = SOURCE_DIRECTION
        if wanted is None:
            relative = relative_in(utterance)
            if relative is not None:
                wanted = compass_side(relative, camera, compass)
                if wanted is None:
                    return ResolvedTarget(
                        CANDIDATES, component_id, None, tuple(candidates), AMBIGUOUS_TARGET,
                        f"'{relative}' needs a camera and the project's compass to read; {component_id} has {len(candidates)} editable elements",
                        source,
                    )
        if wanted is None:
            wanted = side_hint
        if wanted is not None:
            sided = [item for item in candidates if item.side == wanted]
            if len(sided) == 1:
                return ResolvedTarget(RESOLVED, sided[0].component_id, sided[0].element_id, (sided[0],), None, f"the {wanted} one of {component_id}: {sided[0].element_id}", how)
            if len(sided) > 1:
                candidates = sided
        return ResolvedTarget(
            CANDIDATES, component_id, None, tuple(candidates), AMBIGUOUS_TARGET,
            f"{component_id} has {len(candidates)} editable elements; which one?",
            source,
        )
    if node.unbound_object_count > 0:
        return ResolvedTarget(
            MISSING, component_id, None, (), MODEL_VISIBLE_CATALOG_MISSING,
            f"{component_id} is visible in the model ({node.unbound_object_count} objects) and has no editable element in the catalog",
            source,
        )
    return ResolvedTarget(
        MISSING, component_id, None, (), MISSING_ELEMENT_DECLARATION,
        f"{component_id} declares no Element@1 row and the model shows nothing of it to bind",
        source,
    )


def alias_components(catalog: Catalog, utterance: str, aliases: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Components the sentence names by id, by a project alias, or by a registry alias of their role."""

    lowered = utterance.lower()
    found: list[str] = []
    for phrase, component_id in (aliases or {}).items():
        if phrase and _word_in(phrase.lower(), lowered) and catalog.component(component_id) is not None:
            found.append(component_id)
    for node in catalog.components:
        cid = node.component_id
        if _word_in(cid.lower(), lowered):
            found.append(cid)
            continue
        parts = [part for part in cid.split("-") if len(part) > 3]
        if parts and all(_word_in(part, lowered) for part in parts):
            found.append(cid)
    return tuple(dict.fromkeys(found))


def resolve_target(
    catalog: Catalog,
    *,
    selection: Selection,
    utterance: str,
    picked_object: str | None = None,
    gesture_target: Selection | None = None,
    camera: Mapping[str, Any] | None = None,
    compass: Mapping[str, Sequence[float]] | None = None,
    aliases: Mapping[str, str] | None = None,
    rejected: Sequence[str] = (),
    side_hint: str | None = None,
) -> ResolvedTarget:
    # 1. the element the request names
    if selection.element_id is not None and selection.element_id not in rejected:
        element = catalog.element(selection.element_id)
        if element is not None:
            return ResolvedTarget(RESOLVED, element.component_id, element.element_id, (_candidate(catalog, element),), None, f"the request names {element.element_id}", SOURCE_ELEMENT)
    # 2. the picked object's binding
    if picked_object is not None:
        for binding in catalog.objects:
            if binding.name != picked_object:
                continue
            if binding.element_id is not None and binding.element_id not in rejected:
                element = catalog.element(binding.element_id)
                if element is not None:
                    return ResolvedTarget(RESOLVED, element.component_id, element.element_id, (_candidate(catalog, element),), None, f"{picked_object} is {element.element_id}", SOURCE_OBJECT)
            if binding.status == MODEL_VISIBLE_CATALOG_MISSING:
                return ResolvedTarget(MISSING, binding.component_id, None, (), MODEL_VISIBLE_CATALOG_MISSING, f"{picked_object}: {binding.detail}", SOURCE_OBJECT)
    # 3. what was circled
    if gesture_target is not None:
        if gesture_target.element_id is not None and gesture_target.element_id not in rejected:
            element = catalog.element(gesture_target.element_id)
            if element is not None:
                return ResolvedTarget(RESOLVED, element.component_id, element.element_id, (_candidate(catalog, element),), None, f"the circle named {element.element_id}", SOURCE_GESTURE)
        if gesture_target.component_id is not None and gesture_target.component_id not in rejected:
            return _component_target(catalog, gesture_target.component_id, utterance=utterance, camera=camera, compass=compass, rejected=rejected, source=SOURCE_GESTURE, side_hint=side_hint)
    # 4. the component chosen
    if selection.component_id is not None and selection.component_id not in rejected:
        return _component_target(catalog, selection.component_id, utterance=utterance, camera=camera, compass=compass, rejected=rejected, source=SOURCE_COMPONENT, side_hint=side_hint)
    # 5. a name in the sentence
    named = [cid for cid in alias_components(catalog, utterance, aliases) if cid not in rejected]
    if len(named) == 1:
        return _component_target(catalog, named[0], utterance=utterance, camera=camera, compass=compass, rejected=rejected, source=SOURCE_ALIAS, side_hint=side_hint)
    if len(named) > 1:
        # Several components named: the more specific (deeper) one wins only
        # when it is a descendant of the others; otherwise it is a question.
        deepest = _deepest_if_nested(catalog, named)
        if deepest is not None:
            return _component_target(catalog, deepest, utterance=utterance, camera=camera, compass=compass, rejected=rejected, source=SOURCE_ALIAS, side_hint=side_hint)
        return ResolvedTarget(CANDIDATES, None, None, tuple(_candidate(catalog, e) for cid in named for e in catalog.editable_descendants(cid)), AMBIGUOUS_TARGET, f"the sentence names {', '.join(named)}; which one?", SOURCE_ALIAS)
    return ResolvedTarget(NONE, None, None, (), MISSING_TARGET, "the sentence names nothing the record declares, and nothing was picked", None)


_NEGATIONS = (
    re.compile(r"不是\s*([^，,。.;；]+?)(?:[，,。.;；]|$|\s+(?:是|而是))"),
    re.compile(r"\bnot\s+(?:the\s+|a\s+)?([a-z][a-z\- ]*?)(?:[,.;]|$|\s+(?:but|it's|its|it is)\b)", re.IGNORECASE),
)


def negations_in(catalog: Catalog, utterance: str, aliases: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The components a sentence rules out: "不是屋顶" / "not the roof".

    Read before the target, so a corrected sentence never resolves back to
    what it just rejected. Only names the catalog holds count.
    """

    rejected: list[str] = []
    for pattern in _NEGATIONS:
        for match in pattern.finditer(utterance):
            phrase = match.group(1).strip()
            for cid in alias_components(catalog, phrase, aliases):
                rejected.append(cid)
                node = catalog.component(cid)
                if node is not None:
                    rejected.extend(node.descendant_element_ids)
    return tuple(dict.fromkeys(rejected))


def affirmed_after_negation(utterance: str) -> str:
    """The part of a corrected sentence after the negation: "不是屋顶，是柱子" -> "柱子"."""

    for marker in ("而是", "，是", ",是", "是", "but the", "but", "it's the", "it is the"):
        if marker in utterance:
            head, _, tail = utterance.partition(marker)
            if "不是" in head or "not" in head.lower():
                return tail.strip(" ,，。.")
    return utterance


def _deepest_if_nested(catalog: Catalog, ids: Sequence[str]) -> str | None:
    def ancestors(cid: str) -> set[str]:
        out: set[str] = set()
        node = catalog.component(cid)
        while node is not None and node.parent_id is not None:
            out.add(node.parent_id)
            node = catalog.component(node.parent_id)
        return out

    for cid in ids:
        others = set(ids) - {cid}
        if others and others <= ancestors(cid):
            return cid
    return None


# ---- stage two: the action


def amount_in(utterance: str) -> tuple[float, str | None] | None:
    """The one number the sentence carries, with its unit word when it has one."""

    match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*(%|％|mm|cm|m\b|米|毫米|厘米|meters?|metres?|percent)?", utterance)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    unit = _UNITS.get((match.group(2) or "").lower()) if match.group(2) else None
    return value, unit


def qualitative(utterance: str) -> bool:
    lowered = utterance.lower()
    return any(_word_in(word, lowered) for word in _QUALITATIVE)


def direction_in(utterance: str) -> str | None:
    lowered = utterance.lower()
    if any(_word_in(word, lowered) for word in _DECREASE):
        return "decrease"
    if any(_word_in(word, lowered) for word in _INCREASE):
        return "increase"
    return None


def property_in(utterance: str, keys: Sequence[str]) -> str | None:
    lowered = utterance.lower()
    for key in keys:
        if _word_in(key.lower(), lowered):
            return key
    for key, words in _PROPERTY_WORDS.items():
        if key in keys and any(_word_in(word, lowered) for word in words):
            return key
    return None


def resolve_action(
    catalog: Catalog,
    *,
    utterance: str,
    target: ResolvedTarget,
    length_unit: str | None = None,
) -> IntentResult:
    """The deterministic reading of the sentence against the resolved target.

    Everything decidable from the record is decided here; the agent is asked
    only for the words this cannot read, and its answer goes through
    ``validate_agent_result`` before it counts.
    """

    parsed = parse_utterance(utterance)
    if target.status == MISSING:
        return IntentResult(
            kind=DECLARE_MISSING_CONTROL,
            target=target,
            reason_code=target.reason_code,
            requested_property=property_in(utterance, list(_PROPERTY_WORDS)) or ("height" if direction_in(utterance) else None),
            question=(
                f"{target.component_id} cannot be edited yet: {target.detail}. "
                "An authored control (an Element@1 row for it) has to exist first."
            ),
            why=target.detail,
        )
    if target.status == NONE:
        return IntentResult(kind=CLARIFY, target=target, missing_slots=("target",), reason_code=MISSING_TARGET, question="Which element or component do you mean? Pick it in the model, choose it from the tree, or name it.", why=target.detail)
    if target.status == CANDIDATES:
        listed = "; ".join(
            f"{item.element_id}" + (f" ({item.side})" if item.side else "") + " · " + ", ".join(f"{k} {v}" for k, v in item.values.items())
            for item in target.candidates
        )
        return IntentResult(
            kind=CLARIFY, target=target, missing_slots=("target",), reason_code=AMBIGUOUS_TARGET,
            question=f"{target.detail} {listed}", why=target.detail,
            slots=_slots(utterance, length_unit),
        )
    assert target.status == RESOLVED and target.element_id is not None
    element = catalog.element(target.element_id)
    assert element is not None
    keys = [cap.key for cap in element.capabilities if cap.status == EDITABLE]
    if not keys:
        return IntentResult(
            kind=DECLARE_MISSING_CONTROL, target=target, reason_code=UNSUPPORTED_ADD_FIELD,
            requested_property=property_in(utterance, list(_PROPERTY_WORDS)),
            question=f"{element.element_id} has no editable field; the record has to declare one first.",
            why="no editable capability on the element",
        )
    if parsed is not None:
        field_name = parsed.field[len("params.") :] if parsed.field.startswith("params.") else parsed.field
        if field_name in keys:
            return IntentResult(kind=CHANGE_EXISTING_VALUE, target=target, utterance=utterance, capability_id=f"entity:{element.element_id}#params.{field_name}", why="typed in the grammar")
        return IntentResult(
            kind=CLARIFY, target=target, missing_slots=("property",), reason_code=MISSING_PROPERTY,
            question=f"{element.element_id} has no field {field_name}; it has {', '.join(keys)}. Which one?", why="the sentence names a field the element does not have",
        )
    key = property_in(utterance, keys)
    if key is None and len(keys) == 1:
        key = keys[0]
    if key is None:
        return IntentResult(
            kind=CLARIFY, target=target, missing_slots=("property",), reason_code=MISSING_PROPERTY,
            question=f"Which of {element.element_id}'s fields: {', '.join(keys)}?", why="the sentence names no field", slots=_slots(utterance, length_unit),
        )
    capability_id = f"entity:{element.element_id}#params.{key}"
    change = direction_in(utterance)
    amount = amount_in(utterance)
    if amount is None:
        if qualitative(utterance) or change is not None:
            return IntentResult(
                kind=CLARIFY, target=target, missing_slots=("amount",), reason_code=MISSING_AMOUNT,
                capability_id=capability_id, requested_property=key,
                question=f"By how much? {element.element_id} {key} is {element_value(element, key)} now; say a number (in the record's units) or a percentage.",
                why="a qualitative amount is not a number the record can type",
                slots={"direction": change} if change else {},
            )
        return IntentResult(kind=CLARIFY, target=target, missing_slots=("amount", "direction"), reason_code=MISSING_AMOUNT, capability_id=capability_id, requested_property=key, question=f"What should {element.element_id} {key} become? It is {element_value(element, key)} now.", why="no number and no direction in the sentence")
    value, unit = amount
    old = element_value(element, key)
    if unit == "%":
        if change is None:
            return IntentResult(kind=CLARIFY, target=target, missing_slots=("direction",), reason_code=MISSING_AMOUNT, capability_id=capability_id, requested_property=key, question=f"{value} % more or less on {element.element_id} {key}?", why="a percentage without a direction")
        sentence = f"{change} {key} by {_number(value)} %"
        return IntentResult(kind=CHANGE_EXISTING_VALUE, target=target, utterance=sentence, capability_id=capability_id, why=f"{change} by {_number(value)} % as said")
    if unit is not None and length_unit is not None and unit != length_unit:
        return IntentResult(kind=CLARIFY, target=target, missing_slots=("amount",), reason_code=MISSING_AMOUNT, capability_id=capability_id, requested_property=key, question=f"The record keeps {key} in {length_unit}; what is the value in {length_unit}? (this seam converts nothing)", why=f"the sentence says {unit}, the record is in {length_unit}")
    if unit is not None and length_unit is None:
        return IntentResult(kind=CLARIFY, target=target, missing_slots=("amount",), reason_code=MISSING_AMOUNT, capability_id=capability_id, requested_property=key, question=f"{key} on {element.element_id} is a bare number in the record and nothing says its unit; what is the value in the record's own units?", why="the record declares no unit for this field")
    if change is not None and not _looks_absolute(utterance):
        new = old + value if change == "increase" else old - value
        sentence = f"set {key} to {_number(round(new, 6))}"
        return IntentResult(kind=CHANGE_EXISTING_VALUE, target=target, utterance=sentence, capability_id=capability_id, why=f"{change} {key} by {_number(value)} from {_number(old)}")
    sentence = f"set {key} to {_number(value)}"
    return IntentResult(kind=CHANGE_EXISTING_VALUE, target=target, utterance=sentence, capability_id=capability_id, why="set as said")


def element_value(element: CatalogElement, key: str) -> int | float:
    for cap in element.capabilities:
        if cap.key == key:
            return cap.value
    raise KeyError(key)


def _looks_absolute(utterance: str) -> bool:
    lowered = utterance.lower()
    return any(_word_in(word, lowered) for word in ("set", "to", "改为", "改成", "设为", "设置为", "变成", "调到", "="))


def _slots(utterance: str, length_unit: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    amount = amount_in(utterance)
    if amount is not None:
        out["amount"] = amount[0]
        if amount[1] is not None:
            out["unit"] = amount[1]
    change = direction_in(utterance)
    if change is not None:
        out["direction"] = change
    return out


def _number(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---- the agent's closed answer, validated

AGENT_KINDS = ("command", "clarify", "declare_control", "unsupported")


def validate_agent_result(
    catalog: Catalog,
    *,
    utterance: str,
    target: ResolvedTarget,
    answer: Mapping[str, Any],
    length_unit: str | None = None,
) -> IntentResult:
    """An agent's answer, admitted only where the catalog agrees with it.

    A command must name a capability the catalog holds and marks editable; a
    relative command on a sentence with no number is downgraded to a question
    about the amount, whatever the agent assumed; a clarify keeps the agent's
    question but the candidates are the catalog's.
    """

    kind = answer.get("kind")
    why = str(answer.get("why") or "")
    if kind == "command":
        capability_id = answer.get("capabilityId")
        cap = catalog.capability(str(capability_id)) if isinstance(capability_id, str) else None
        if cap is None or cap.status != EDITABLE:
            return IntentResult(kind=CLARIFY, target=target, missing_slots=("target",), reason_code=AMBIGUOUS_TARGET, question=f"The agent named {capability_id!r}, which the catalog does not hold as an editable field. Which element and field do you mean?", why=why)
        if target.status in (MISSING, NONE):
            # The sentence's own subject has no catalog entry; a command on
            # some other element is the substitution this resolver forbids.
            return IntentResult(kind=DECLARE_MISSING_CONTROL if target.status == MISSING else CLARIFY, target=target, missing_slots=() if target.status == MISSING else ("target",), reason_code=target.reason_code, question=f"The agent proposed {cap.element_id} {cap.key}, but the sentence is about {target.component_id or 'something the record does not name'}: {target.detail}", why=f"refused the agent's substitute: {why}")
        allowed = {item.element_id for item in target.candidates}
        if allowed and cap.element_id not in allowed:
            return IntentResult(kind=CLARIFY, target=target, missing_slots=("target",), reason_code=AMBIGUOUS_TARGET, question=f"The agent named {cap.element_id}, which is not among {', '.join(sorted(allowed))}. Which one?", why=f"refused the agent's substitute: {why}")
        op = answer.get("op")
        value = answer.get("value")
        if amount_in(utterance) is None and op in ("increase", "decrease"):
            element = catalog.element(cap.element_id)
            return IntentResult(kind=CLARIFY, target=target, missing_slots=("amount",), reason_code=MISSING_AMOUNT, capability_id=cap.capability_id, requested_property=cap.key, question=f"By how much? {cap.element_id} {cap.key} is {cap.value} now; the sentence gave no number.", why=f"the agent assumed an amount ({why}); a qualitative word is a question", slots={"direction": op})
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return IntentResult(kind=CLARIFY, target=target, missing_slots=("amount",), reason_code=MISSING_AMOUNT, capability_id=cap.capability_id, requested_property=cap.key, question=f"What should {cap.element_id} {cap.key} become? It is {cap.value} now.", why=why)
        if op == "set":
            sentence = f"set {cap.key} to {_number(value)}"
        elif op in ("increase", "decrease"):
            sentence = f"{op} {cap.key} by {_number(value)} %"
        else:
            return IntentResult(kind=UNSUPPORTED, target=target, reason_code=UNSUPPORTED_ACTION, question=f"The agent answered op {op!r}; only set, increase and decrease exist.", why=why)
        keep = answer.get("keep")
        if isinstance(keep, list) and keep:
            sentence += " keep " + ", ".join(str(item) for item in keep)
        resolved = ResolvedTarget(RESOLVED, cap.element_id and catalog.element(cap.element_id).component_id, cap.element_id, target.candidates, None, f"the agent named {cap.capability_id}", target.source)  # type: ignore[union-attr]
        return IntentResult(kind=CHANGE_EXISTING_VALUE, target=resolved, utterance=sentence, capability_id=cap.capability_id, why=why)
    if kind == "clarify":
        slots = answer.get("missingSlots")
        missing = tuple(str(item) for item in slots) if isinstance(slots, list) and slots else ("target",)
        return IntentResult(kind=CLARIFY, target=target, missing_slots=missing, reason_code=AMBIGUOUS_TARGET if "target" in missing else MISSING_AMOUNT if "amount" in missing else MISSING_PROPERTY, question=str(answer.get("question") or "The agent asked without stating its question."), why=why, slots=_slots(utterance, length_unit))
    if kind == "declare_control":
        component_id = answer.get("componentId")
        node = catalog.component(str(component_id)) if isinstance(component_id, str) else None
        if node is None:
            return IntentResult(kind=CLARIFY, target=target, missing_slots=("target",), reason_code=MISSING_TARGET, question=f"The agent proposed a control on {component_id!r}, which the record does not declare. Which component?", why=why)
        prop = answer.get("property")
        return IntentResult(kind=DECLARE_MISSING_CONTROL, target=ResolvedTarget(MISSING, node.component_id, None, (), MODEL_VISIBLE_CATALOG_MISSING if node.unbound_object_count else MISSING_ELEMENT_DECLARATION, f"{node.component_id} has no editable element", target.source), reason_code=MODEL_VISIBLE_CATALOG_MISSING if node.unbound_object_count else MISSING_ELEMENT_DECLARATION, requested_property=str(prop) if isinstance(prop, str) else None, question=f"{node.component_id} cannot be edited yet; an authored control has to exist first.", why=why)
    if kind == "unsupported":
        return IntentResult(kind=UNSUPPORTED, target=target, reason_code=str(answer.get("reasonCode") or UNSUPPORTED_ACTION), question=str(answer.get("question") or "The request is outside what the record can take."), why=why)
    return IntentResult(kind=UNSUPPORTED, target=target, reason_code=UNSUPPORTED_ACTION, question=f"The agent answered kind {kind!r}; only command, clarify, declare_control and unsupported exist.", why=why)


def sides_declared(catalog: Catalog) -> tuple[str, ...]:
    """Which cardinal identities the record's elements carry, for the sheet."""

    return tuple(sorted({s for e in catalog.elements for s in [side_of(e.element_id)] if s}))


def roles_of(component_kind: str | None) -> tuple[str, ...]:
    """Registry aliases for a component's semantic kind, for the agent's sheet."""

    if not component_kind:
        return ()
    resolution = resolve_semantic_kind(component_kind)
    if resolution is None:
        return ()
    words: list[str] = []
    for role in ROLES:
        if role.id in resolution.roles:
            words.extend(role.aliases)
    return tuple(words)
