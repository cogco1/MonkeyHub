"""The IntentProvider seam, and the only grammar it speaks.

A round-1 utterance is not interpreted. It is parsed, against four exact forms
and one optional ``keep`` clause, and anything outside them becomes a question
for a human rather than a guess about a building. That is the whole point of
this module: an LLM here would be free to invent a coordinate, and the boundary
the plan draws is that nothing may reach a proposal that the record did not
already contain.

Two things follow from that and are worth saying out loud. The *field* is never
read out of prose — it is resolved against the selection the request carried
(``targetComponentId`` and an optional ``elementId``), and only against a scalar
number the record actually declares: a polyline vertex, a datum offset, or any
field outside ``params`` is not a target, because moving a coordinate by
sentence is exactly the silently invented number this seam exists to refuse. And
the *proposal* is never applied: it carries a real ``DecisionOperator`` with no
write authority, which Task 7 may run as a candidate and nothing here may
commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from archflow.contracts.canonical import canonical_json
from archflow.state.decision_operator import (
    ConditionComparator,
    DecisionOperator,
    StateCondition,
)
from archflow.state.operational_state import ParameterBinding, StateLock
from archflow.state.state_record import Parameter

from ..transport.errors import BlockedNeedsHuman, StudioError
from .impact import impact
from .projection import (
    RUNNER_RECORD_PATH,
    ProjectedElement,
    StateProjection,
)

# Who the proposal says it is, on the wire and in the operator. It is not an
# authority that can write: the id names a seat that only ever proposes.
AUTHORITY_ID = "studio:proposal-only"
INTENT_SOURCE_REF = "studio:intent"
PROTECTION_AUTHORITY_ID = "studio:user"

# The two typed actions this grammar can express, named so a later reader of a
# retained operator can tell which of the record's two number stores it touched.
ELEMENT_PARAM_CHANGE = "studio.element_param_change"
PARAMETER_CHANGE = "studio.parameter_change"

PROPOSED = "proposed"
CONFLICT = "conflict"

# Percentages are computed, so they need a stated precision. Six decimals is
# below any dimension a building is drawn to and above float noise.
ROUNDING = 6

# The four forms, verbatim, as ``acceptedForms`` repeats them back. Each one is
# a whole utterance somebody can type; the optional ``keep`` suffix is not, so
# it is explained in the question sentence rather than listed here as a fifth
# thing to try.
ACCEPTED_FORMS: tuple[str, ...] = (
    "set <field> to <number>[ <unit>]",
    "set <field> = <number>[ <unit>]",
    "increase <field> by <number> %",
    "decrease <field> by <number> %",
)

# How the question describes the suffix any of the four may carry.
KEEP_SENTENCE = (
    "Any of them may end with keep <ref>[, <ref>…] to name what the change "
    "must not disturb."
)

# A decimal, and nothing cleverer: no expressions, no ranges, no words.
_NUMBER = r"-?\d+(?:\.\d+)?"

# A unit is a bare word — ``m``, ``mm``, ``deg``. ``keep`` is a keyword of this
# grammar and never a unit, or ``set height to 2.2 keep`` would quietly parse as
# a change with no protected refs.
_UNIT = r"(?!keep\b)[A-Za-z]+"

# The field text, kept verbatim: it names a key in the record, and this grammar
# neither lowercases it nor splits it.
_FIELD = r"[^\s=]+"

_SET = re.compile(
    rf"^set\s+(?P<field>{_FIELD})\s*(?:=\s*|\s+to\s+)"
    rf"(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT})?$",
    re.IGNORECASE,
)
_PERCENT = re.compile(
    rf"^(?P<operation>increase|decrease)\s+(?P<field>{_FIELD})\s+by\s+"
    rf"(?P<number>{_NUMBER})\s*%$",
    re.IGNORECASE,
)
_KEEP = re.compile(r"\s+keep\s+", re.IGNORECASE)

SET = "set"
INCREASE = "increase"
DECREASE = "decrease"


@dataclass(frozen=True, slots=True)
class ParsedIntent:
    """One utterance, in the only shape the rest of the API will read.

    ``field`` and ``keep`` are still raw text here: resolving them is the
    record's job, not the grammar's, and a parser that resolved them could not
    be table-tested against the sentence alone.
    """

    operation: str
    field: str
    number: int | float
    unit: str | None
    keep: tuple[str, ...]


def parse_utterance(utterance: str) -> ParsedIntent | None:
    """The utterance as one of the four forms, or ``None`` — never a guess.

    ``None`` is the whole of the refusal. The question a human is asked is
    built where the record is known, because a useful question names this
    record's fields and not just the grammar's shapes.
    """

    if not isinstance(utterance, str):
        return None
    head, keep = _split_keep(utterance.strip())
    if keep is None:
        return None
    match = _SET.match(head)
    if match is not None:
        return ParsedIntent(
            operation=SET,
            field=match.group("field"),
            number=_number(match.group("number")),
            unit=match.group("unit"),
            keep=keep,
        )
    match = _PERCENT.match(head)
    if match is None:
        return None
    return ParsedIntent(
        operation=match.group("operation").lower(),
        field=match.group("field"),
        number=_number(match.group("number")),
        unit=None,
        keep=keep,
    )


def _split_keep(utterance: str) -> tuple[str, tuple[str, ...] | None]:
    """Split the change from the refs it must not disturb.

    A ``keep`` clause with an empty ref in it (``keep a,``) is a typo, not a
    shorter list: the whole utterance is refused rather than half-read.
    """

    parts = _KEEP.split(utterance, maxsplit=1)
    if len(parts) == 1:
        return utterance, ()
    refs = tuple(ref.strip() for ref in parts[1].split(","))
    if any(not ref for ref in refs):
        return utterance, None
    return parts[0], refs


def _number(text: str) -> int | float:
    """The number as it was written: ``3`` is an integer, ``3.0`` is not."""

    return float(text) if "." in text else int(text)


def accepted_forms() -> tuple[str, ...]:
    """The four forms a person can type, and nothing that is not one.

    ``acceptedForms`` is a list of things to try, so every entry has to be a
    complete utterance. The ``keep`` suffix is a modifier on all four rather
    than a fifth form, and it travels in the question sentence instead.
    """

    return ACCEPTED_FORMS


def _selection(context_refs: Sequence[str], prefix: str) -> str | None:
    """One ``<kind>:<value>`` context ref, or ``None`` when it was not given."""

    for ref in context_refs:
        if ref.startswith(prefix):
            return ref[len(prefix) :] or None
    return None


@dataclass(frozen=True, slots=True)
class _Target:
    """The one number in the record this utterance is about."""

    ref: str
    key: str
    # What the operator binds: an element's param is addressed as
    # ``params.<key>``, a record parameter by its bare key.
    binding_key: str
    decision_type: str
    old: int | float
    unit: str | None
    element_id: str | None


class DeterministicIntentProvider:
    """``ports.IntentProvider``, implemented by a grammar rather than a model.

    Constructed with the projection the request already took, so the record it
    answers from is the same one whose ``stateDigest`` the client was given.
    ``propose`` either returns the parts of one proposal or raises: there is no
    third outcome, and in particular no partially understood utterance.
    """

    def __init__(self, projection: StateProjection) -> None:
        self.projection = projection

    def propose(
        self,
        *,
        session_ref: str,
        message: str,
        context_refs: Sequence[str],
    ) -> Mapping[str, Any]:
        """One utterance against one selection: a proposal's parts, or a question.

        ``session_ref`` is recorded by the caller, not read here: round 1 has no
        session identity to trust, and nothing about which chat asked may change
        what the record answers.
        """

        projection = self.projection
        self._require_base(_selection(context_refs, "state:"))
        component_id = self._component(_selection(context_refs, "component:"))
        element = self._element(
            component_id, _selection(context_refs, "element:")
        )
        parsed = parse_utterance(message)
        if parsed is None:
            raise self._ungrammatical(message, element)
        target = self._target(parsed, element)
        new = self._new_value(parsed, target)
        protected = self._protected(parsed.keep)
        answer = impact(projection, target.ref, protected)
        proposal_id = f"studio-{uuid4().hex[:12]}"
        return {
            "proposal_id": proposal_id,
            # The status is read off the impact rather than recomputed beside
            # it: there is one definition of conflict, it lives in
            # ``impact.conflicts``, and a proposal cannot disagree with the
            # closure it is showing. A conflict is still a proposal — the user
            # resolves it, and the server neither drops the change nor quietly
            # overrides the protection.
            "status": CONFLICT if answer.conflicts else PROPOSED,
            "base_state_digest": projection.state_digest,
            "record_digest": projection.record_digest,
            "component_id": component_id,
            "element_id": target.element_id,
            "target_ref": target.ref,
            "key": target.key,
            "old": target.old,
            "new": new,
            "unit": target.unit,
            "protected": protected,
            "operator": self._operator(
                proposal_id, message, target, new, protected, answer.propagated
            ),
            "impact": answer,
            "utterance": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    # ---- the selection, which comes from the request and never from prose

    def _require_base(self, state_digest: str | None) -> None:
        """A proposal is against one exact state or against nothing."""

        if state_digest == self.projection.state_digest:
            return
        raise StudioError(
            409,
            "STALE_BASE",
            f"the proposal names state {state_digest}, but "
            f"{self.projection.project_id} is at "
            f"{self.projection.state_digest}. Read /api/state again and "
            "propose against the state that answers now.",
        )

    def _component(self, component_id: str | None) -> str:
        """The selected ``Component@1``, or a question naming what was sent."""

        declared = {
            entity.entity_id
            for entity in self.projection.record.entities_of("Component@1")
        }
        if component_id in declared:
            assert component_id is not None
            return component_id
        raise BlockedNeedsHuman(
            "the selection names no component of this record",
            question=(
                f"targetComponentId {component_id} is not a component this "
                f"record declares; it declares "
                f"{_listed(sorted(declared))}. Which did you mean?"
            ),
        )

    def _element(
        self, component_id: str, element_id: str | None
    ) -> ProjectedElement | None:
        """The selected ``Element@1``, checked against the component it claims.

        An element of another component is not a narrower selection, it is two
        selections that disagree, and guessing which one the user meant is
        exactly how a change lands on the wrong thing.
        """

        if element_id is None:
            return None
        for element in self.projection.elements:
            if element.element_id != element_id:
                continue
            if element.component_id == component_id:
                return element
            raise BlockedNeedsHuman(
                "the selected element and component disagree",
                question=(
                    f"element {element_id} belongs to component "
                    f"{element.component_id}, not {component_id}; which did "
                    "you mean?"
                ),
            )
        raise BlockedNeedsHuman(
            "the selection names no element of this record",
            question=(
                f"elementId {element_id} is not an Element@1 this record "
                f"declares; under {component_id} it declares "
                f"{_listed(self._element_ids(component_id))}. Which did you "
                "mean?"
            ),
        )

    def _element_ids(self, component_id: str) -> list[str]:
        return sorted(
            element.element_id
            for element in self.projection.elements
            if element.component_id == component_id
        )

    # ---- the field, resolved against the record and never invented

    def _target(
        self, parsed: ParsedIntent, element: ProjectedElement | None
    ) -> _Target:
        """Which number the field names: an element's param, or a parameter."""

        field = parsed.field
        if field.startswith("parameter:"):
            return self._parameter_target(field[len("parameter:") :], parsed)
        if field.startswith("params."):
            key = field[len("params.") :]
            if element is None:
                raise BlockedNeedsHuman(
                    "params.<key> needs an element to belong to",
                    question=(
                        f"{field} names a field of an element, and the "
                        "request selected no elementId. Which element did you "
                        "mean?"
                    ),
                )
            return self._element_target(element, key, parsed)
        if element is not None and field in element.numeric_fields:
            return self._element_target(element, field, parsed)
        if any(item.key == field for item in self.projection.parameters):
            return self._parameter_target(field, parsed)
        if element is not None:
            raise self._unknown_element_field(element, field)
        return self._parameter_target(field, parsed)

    def _element_target(
        self,
        element: ProjectedElement,
        key: str,
        parsed: ParsedIntent,
    ) -> _Target:
        """One scalar of ``fields["params"]``, which the record holds unit-less.

        A unit word here is a question, exactly as it is on a parameter whose
        unit the utterance disagrees with. The record states these numbers
        bare — ``height`` is metres because the producer reads metres, and
        nothing in the record says so — and ``set height to 2200 mm`` against
        a field holding ``0.6`` would propose two thousand two hundred metres.
        This seam converts nothing, so it asks rather than dropping the word
        that was the whole difference.
        """

        if key not in element.numeric_fields:
            raise self._unknown_element_field(element, key)
        if parsed.unit is not None:
            raise BlockedNeedsHuman(
                "the element field is a unit-less number",
                question=(
                    f"{key} on {element.element_id} is a bare number in the "
                    "record and this seam converts nothing; what is the value "
                    "in the record's own units?"
                ),
            )
        return _Target(
            ref=f"entity:{element.element_id}",
            key=key,
            binding_key=f"params.{key}",
            decision_type=ELEMENT_PARAM_CHANGE,
            old=element.numeric_fields[key],
            # Null because the record declares none, never because one was
            # said and discarded: an utterance that carried a unit was
            # refused above.
            unit=None,
            element_id=element.element_id,
        )

    def _unknown_element_field(
        self, element: ProjectedElement, field: str
    ) -> BlockedNeedsHuman:
        """A field the element does not declare, answered with the ones it does.

        Its lists and its ``references`` are deliberately not offered: they are
        not grammar targets, and naming them here would invite the coordinate
        this seam refuses to invent.
        """

        return BlockedNeedsHuman(
            "the element declares no such numeric field",
            question=(
                f"element {element.element_id} declares no numeric field "
                f"{field}; its numeric fields are "
                f"{_listed(sorted(element.numeric_fields))}. Which did you "
                "mean?"
            ),
        )

    def _parameter_target(self, key: str, parsed: ParsedIntent) -> _Target:
        """One ``Parameter`` of the record, if the record declares any at all."""

        parameters = self.projection.parameters
        if not parameters:
            raise BlockedNeedsHuman(
                "the record declares no parameters",
                question=(
                    f"the record declares 0 parameters; parameter intents "
                    f"need parameters authored into {RUNNER_RECORD_PATH} — "
                    "which element field did you mean?"
                ),
            )
        parameter = next(
            (item for item in parameters if item.key == key), None
        )
        if parameter is None:
            raise BlockedNeedsHuman(
                "the record declares no such parameter",
                question=(
                    f"the record declares no parameter {key}; it declares "
                    f"{_listed(sorted(item.key for item in parameters))}. "
                    "Which did you mean?"
                ),
            )
        self._require_unlocked(parameter)
        self._require_unit(parameter, parsed.unit)
        return _Target(
            ref=parameter.ref,
            key=parameter.key,
            binding_key=parameter.key,
            decision_type=PARAMETER_CHANGE,
            old=parameter.value,
            unit=parameter.unit or None,
            element_id=None,
        )

    def _require_unlocked(self, parameter: Parameter) -> None:
        """A locked quantity is somebody's commitment, not an obstacle to route around."""

        if parameter.lock_authority:
            raise BlockedNeedsHuman(
                "the parameter is locked",
                question=(
                    f"parameter {parameter.key} is locked by "
                    f"{parameter.lock_authority}; release it explicitly?"
                ),
            )

    def _require_unit(self, parameter: Parameter, unit: str | None) -> None:
        """A unit that disagrees with the record's is a question, not a conversion."""

        if unit is None or not parameter.unit or unit == parameter.unit:
            return
        raise BlockedNeedsHuman(
            "the utterance's unit is not the parameter's",
            question=(
                f"parameter {parameter.key} is declared in {parameter.unit}, "
                f"and the utterance says {unit}; this seam converts nothing. "
                f"What is the value in {parameter.unit}?"
            ),
        )

    # ---- the numbers

    def _new_value(self, parsed: ParsedIntent, target: _Target) -> int | float:
        """What the field would become; the record's own value is the base."""

        if parsed.operation == SET:
            return parsed.number
        if target.old == 0:
            raise BlockedNeedsHuman(
                "a percentage of zero is zero",
                question=(
                    f"{target.ref} has {target.key} = 0 right now, so a "
                    f"percentage leaves it at 0. What absolute value should it "
                    f"take (set {target.key} to <number>)?"
                ),
            )
        sign = 1 if parsed.operation == INCREASE else -1
        return round(target.old * (1 + sign * parsed.number / 100), ROUNDING)

    def _protected(self, keep: tuple[str, ...]) -> tuple[str, ...]:
        """The ``keep`` refs, prefixed and deduplicated, or a question."""

        return tuple(sorted({self._resolve_ref(ref) for ref in keep}))

    def _resolve_ref(self, ref: str) -> str:
        """One ``keep`` ref as the record's own prefixed ref.

        A bare token is accepted only when exactly one thing answers to it: a
        record that declared both an entity and a parameter called ``bay``
        would be asked which, rather than have one of them silently protected.
        """

        entities = {entity.entity_id for entity in self.projection.record.entities}
        parameters = {item.key for item in self.projection.parameters}
        if ref.startswith("entity:"):
            if ref[len("entity:") :] in entities:
                return ref
            raise self._unknown_ref(ref)
        if ref.startswith("parameter:"):
            if ref[len("parameter:") :] in parameters:
                return ref
            raise self._unknown_ref(ref)
        candidates = [
            prefixed
            for prefixed, declared in (
                (f"entity:{ref}", ref in entities),
                (f"parameter:{ref}", ref in parameters),
            )
            if declared
        ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise self._unknown_ref(ref)
        raise BlockedNeedsHuman(
            "the keep ref names two different things",
            question=(
                f"keep {ref} could mean {_listed(candidates)}; which did you "
                "mean?"
            ),
        )

    def _unknown_ref(self, ref: str) -> BlockedNeedsHuman:
        return BlockedNeedsHuman(
            "the keep ref names nothing in this record",
            question=(
                f"keep names {ref}, which this record declares neither as an "
                "entity nor as a parameter. Which did you mean?"
            ),
        )

    # ---- the operator

    def _operator(
        self,
        proposal_id: str,
        message: str,
        target: _Target,
        new: int | float,
        protected: tuple[str, ...],
        propagated: tuple[str, ...],
    ) -> DecisionOperator:
        """A real ``DecisionOperator@2``: exact base, typed, no write authority.

        The new value travels as the kernel's own canonical JSON because
        ``ParameterBinding.value`` is text. That is a serialization, not a
        rounding: ``2.2`` goes in and ``2.2`` comes back out. Any other
        complaint the kernel has about these values is surfaced as the
        question it asked, never worked around.
        """

        try:
            return DecisionOperator(
                decision_id=proposal_id,
                decision_type=target.decision_type,
                base_state_digest=self.projection.state_digest,
                authority_id=AUTHORITY_ID,
                intent=message,
                preconditions=(
                    StateCondition(
                        ref=target.ref,
                        comparator=ConditionComparator.EQUALS,
                        expected_value=target.old,
                    ),
                ),
                bindings=(
                    ParameterBinding(
                        key=target.binding_key,
                        value=canonical_json(new),
                        source_ref=INTENT_SOURCE_REF,
                    ),
                ),
                add_locks=tuple(
                    StateLock(
                        target_ref=ref,
                        authority_id=PROTECTION_AUTHORITY_ID,
                        source_ref=INTENT_SOURCE_REF,
                    )
                    for ref in protected
                ),
                invalidates=propagated,
                evidence_refs=(f"record:{self.projection.record_digest}",),
            )
        except (ValueError, TypeError) as exc:
            raise BlockedNeedsHuman(
                "the kernel refused this operator", question=str(exc)
            ) from exc

    # ---- the refusal that repeats the grammar

    def _ungrammatical(
        self, message: str, element: ProjectedElement | None
    ) -> BlockedNeedsHuman:
        """The one refusal that is about the sentence rather than the record.

        ``acceptedForms`` lists the four typeable forms; the ``keep`` suffix is
        described here, in the sentence, because it modifies all four rather
        than standing as one of them.
        """

        return BlockedNeedsHuman(
            "the utterance is not in the intent grammar",
            question=(
                f"I read four forms and nothing else, and {message!r} is not "
                f"one of them. {KEEP_SENTENCE} "
                f"{self._targets_sentence(element)} Which field, and to what "
                "number?"
            ),
            accepted_forms=accepted_forms(),
        )

    def _targets_sentence(self, element: ProjectedElement | None) -> str:
        """What this selection actually offers to change, named."""

        if element is not None:
            return (
                f"On element {element.element_id} the numeric fields are "
                f"{_listed(sorted(element.numeric_fields))}."
            )
        if self.projection.parameters:
            return (
                "The record's parameters are "
                f"{_listed(sorted(item.key for item in self.projection.parameters))}."
            )
        return (
            "The record declares 0 parameters, so select an element and name "
            "one of its numeric fields."
        )


def _listed(values: Sequence[str]) -> str:
    """A list a person can read, or a statement that there is nothing to list."""

    return ", ".join(values) if values else "none"
