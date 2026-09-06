"""Type scalar sentences and semantic design edits into reviewable proposals.

Existing numeric controls use four exact grammar forms and an optional keep
clause. Component edits use the agent's named Entity, Parameter and Relation
data, checked by the StateRecord operator and advertised producer contracts.
Both paths calculate impact against the selected state and retain protection
conflicts for review. Neither writes project state: candidate execution uses
the existing runner, and formal commit remains a separate decision.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from archflow.contracts.canonical import canonical_json
from archflow.project.layout import AUTHORED_RECORD_PATH
from archflow.state.decision_operator import (
    ConditionComparator,
    DecisionOperator,
    StateCondition,
)
from archflow.state.operational_state import ParameterBinding, StateLock
from archflow.state.state_record import (
    Entity,
    Parameter,
    Relation,
    StateRecordError,
    apply_state_record_operator,
    compile_component_edit,
)

from ..transport.errors import BlockedNeedsHuman, StudioError
from .impact import impact
from .projection import ProjectedElement, StateProjection

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


def merge_keep(utterance: str, refs: Sequence[str]) -> str:
    """The utterance with these refs added to its keep clause.

    A sentence without a keep clause gains one; a sentence with one keeps its
    refs and gains the missing ones, in order. A malformed keep clause is
    returned untouched: the grammar will refuse it with the right question.
    """

    if not refs:
        return utterance
    head, existing = _split_keep(utterance.strip())
    if existing is None:
        return utterance
    merged = list(existing)
    for ref in refs:
        if ref not in merged:
            merged.append(ref)
    return f"{head} keep {', '.join(merged)}"


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


def component_edit_proposal(
    projection: StateProjection,
    edit: Mapping[str, Any],
    *,
    utterance: str,
    component_id: str | None = None,
    keep_refs: Sequence[str] = (),
) -> Mapping[str, Any]:
    """Type the agent's design data and describe the exact successor it proposes.

    The agent supplies named entities, parameters and relations. The kernel
    derives the successor, including reference integrity and dependencies;
    neither the transport nor the model supplies a geometry program.
    """

    from archflow.capabilities.element_producers import producer_signatures, validate_element_contract

    allowed = {
        "summary", "entities", "parameters", "relations", "removeEntityIds",
        "removeParameterKeys", "removeRelationIds", "protected", "kept",
    }
    try:
        if not isinstance(edit, Mapping) or set(edit) != allowed:
            raise ValueError("semantic edit must contain the declared design fields")
        summary = edit["summary"]
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("semantic edit must describe its proposed change")
        for key in allowed - {"summary"}:
            if not isinstance(edit[key], (list, tuple)):
                raise ValueError(f"semantic edit {key} must be a list")
        for key in ("protected", "kept"):
            if any(not isinstance(item, str) or not item.strip() for item in edit[key]):
                raise ValueError(f"semantic edit {key} must contain nonempty strings")
        record = projection.record
        existing = {entity.entity_id: entity for entity in record.entities}
        entities = []
        for payload in edit["entities"]:
            if not isinstance(payload, Mapping) or set(payload) - {
                "entity_id", "schema", "parent_id", "fields", "basis_refs"
            }:
                raise ValueError("an entity edit contains undeclared fields")
            previous = existing.get(payload.get("entity_id"))
            value = dict(payload)
            if previous is not None:
                value = {
                    **previous.to_dict(), **value,
                    "fields": {**previous.fields, **value.get("fields", {})},
                }
            entity = Entity.from_dict(value)
            entities.append(entity)
        parameters = []
        current_parameters = {parameter.key: parameter for parameter in record.parameters}
        for payload in edit["parameters"]:
            if not isinstance(payload, Mapping) or set(payload) - {
                "key", "value", "unit", "expr", "inputs", "epistemic_status", "source_ref"
            }:
                raise ValueError("a parameter edit contains undeclared fields")
            previous = current_parameters.get(payload.get("key"))
            parameters.append(Parameter.from_dict({
                **({} if previous is None else previous.to_dict()), **payload,
            }))
        relations = []
        current_relations = {relation.relation_id: relation for relation in record.relations}
        for payload in edit["relations"]:
            if not isinstance(payload, Mapping) or set(payload) - {
                "relation_id", "kind", "subject", "object", "datum_role", "propagation",
                "validator", "parameters", "epistemic_status", "basis_refs",
            }:
                raise ValueError("a relation edit contains undeclared fields")
            previous = current_relations.get(payload.get("relation_id"))
            relations.append(Relation.from_dict({
                **({} if previous is None else previous.to_dict()), **payload,
            }))
        removed = {}
        for wire, internal in (
            ("removeEntityIds", "remove_entity_ids"),
            ("removeParameterKeys", "remove_parameter_keys"),
            ("removeRelationIds", "remove_relation_ids"),
        ):
            if any(not isinstance(item, str) or not item for item in edit[wire]):
                raise ValueError(f"{wire} must name existing design items")
            removed[internal] = tuple(edit[wire])
        provider = DeterministicIntentProvider(projection)
        protected = provider._protected(tuple(edit["protected"]) + tuple(keep_refs))
        operator = compile_component_edit(
            record, entities=tuple(entities), parameters=tuple(parameters),
            relations=tuple(relations), **removed,
        )
        # Conflicting protections remain reviewable, just as scalar proposals
        # do. The worker applies the final protected operator and refuses them.
        successor = apply_state_record_operator(record, operator)
        changed_inputs = tuple(
            [entity.ref for entity in entities]
            + [parameter.ref for parameter in parameters]
        )
        affected = set(successor.closure(changed_inputs))
        # A newly supplied element cannot bypass the advertised authoring
        # vocabulary merely by naming another executable legacy producer.
        authored_ids = {entity.entity_id for entity in entities if entity.schema == "Element@1"}
        advertised = producer_signatures()
        validate_element_contract(successor, tuple(sorted(authored_ids | {
            entity.entity_id for entity in successor.entities_of("Element@1")
            if entity.ref in affected and entity.fields.get("producer") in advertised
        })))
        operator = replace(operator, protected=protected)
    except (KeyError, TypeError, ValueError, StateRecordError) as exc:
        raise StudioError(422, "SEMANTIC_EDIT_INVALID", str(exc)) from exc

    before_entities = {entity.entity_id: entity for entity in record.entities}
    after_entities = {entity.entity_id: entity for entity in successor.entities}
    direct: set[str] = set()
    changes: list[dict[str, str]] = []
    for entity_id in sorted(before_entities.keys() | after_entities.keys()):
        before, after = before_entities.get(entity_id), after_entities.get(entity_id)
        if before == after:
            continue
        direct.add(f"entity:{entity_id}")
        entity = after or before
        assert entity is not None
        action = "add" if before is None else "remove" if after is None else "update"
        producer = str(entity.fields.get("producer") or entity.schema.removesuffix("@1"))
        changed_fields = sorted(
            key for key in set(before.fields if before else ()) | set(after.fields if after else ())
            if (before.fields.get(key) if before else None) != (after.fields.get(key) if after else None)
        )
        label = str(entity.fields.get("label") or entity.fields.get("name") or entity_id)
        changes.append({
            "action": action, "entityId": entity_id, "label": label,
            "description": producer + (": " + ", ".join(changed_fields) if changed_fields else ""),
        })
    before_parameters = {parameter.key: parameter for parameter in record.parameters}
    after_parameters = {parameter.key: parameter for parameter in successor.parameters}
    for key in sorted(before_parameters.keys() | after_parameters.keys()):
        before, after = before_parameters.get(key), after_parameters.get(key)
        if before == after:
            continue
        direct.add(f"parameter:{key}")
        changes.append({
            "action": "add" if before is None else "remove" if after is None else "update",
            "entityId": f"parameter:{key}", "label": key,
            "description": f"{None if before is None else before.value} → {None if after is None else after.value}",
        })
    before_relations = {relation.relation_id: relation for relation in record.relations}
    after_relations = {relation.relation_id: relation for relation in successor.relations}
    for key in sorted(before_relations.keys() | after_relations.keys()):
        before, after = before_relations.get(key), after_relations.get(key)
        if before == after:
            continue
        for relation in (before, after):
            if relation is not None:
                direct.update((f"entity:{relation.subject}", f"entity:{relation.object}"))
        relation = after or before
        assert relation is not None
        changes.append({
            "action": "add" if before is None else "remove" if after is None else "update",
            "entityId": f"relation:{key}", "label": key,
            "description": f"{relation.subject} · {relation.kind} · {relation.object}",
        })
    answer = impact(projection, tuple(sorted(direct)), protected, successor=successor)
    components = {entity.entity_id for entity in successor.entities_of("Component@1")}
    changed_elements = [entity for entity in entities if entity.schema == "Element@1"]
    changed_components = {
        entity.fields.get("component_id") or entity.parent_id for entity in changed_elements
    }
    if component_id not in components or (changed_components and component_id not in changed_components):
        component_id = next((
            str(entity.fields.get("component_id") or entity.parent_id)
            for entity in changed_elements
            if (entity.fields.get("component_id") or entity.parent_id) in components
        ), next(iter(sorted(components)), None))
    if component_id is None:
        raise StudioError(422, "SEMANTIC_EDIT_INVALID", "the edit has no declared building component")
    kept = edit["kept"]
    if any(not isinstance(item, str) for item in kept):
        raise StudioError(422, "SEMANTIC_EDIT_INVALID", "kept conditions must be text")
    return {
        "proposal_id": f"studio-{uuid4().hex[:12]}",
        "status": CONFLICT if answer.conflicts else PROPOSED,
        "base_state_digest": projection.state_digest, "record_digest": projection.record_digest,
        "component_id": component_id,
        "element_id": changed_elements[0].entity_id if len(changed_elements) == 1 else None,
        "target_ref": next(iter(sorted(direct)), f"entity:{component_id}"),
        "key": None, "old": None, "new": None, "unit": None,
        "protected": protected, "operator": None, "state_record_operator": operator,
        "impact": answer, "utterance": utterance,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "semantic_edit": {
            "summary": summary.strip(), "changes": changes, "kept": list(kept),
            "edits": {
                "entities": [
                    {**entity.to_dict(), "fields": dict(payload["fields"])}
                    for entity, payload in zip(entities, edit["entities"])
                ],
                "parameters": [parameter.to_dict() for parameter in parameters],
                "relations": [relation.to_dict() for relation in relations],
                "removeEntityIds": list(removed["remove_entity_ids"]),
                "removeParameterKeys": list(removed["remove_parameter_keys"]),
                "removeRelationIds": list(removed["remove_relation_ids"]),
            },
        },
    }


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
        bound_to = element.bindings.get(key)
        if bound_to is not None:
            # The row does not own this number: it reads the parameter, and
            # the kernel refuses a scalar written over a binding. The question
            # names the control that does own it, before any job is started.
            parameter = next(
                (item for item in self.projection.parameters if item.key == bound_to),
                None,
            )
            raise BlockedNeedsHuman(
                "the element field is bound to a parameter",
                question=(
                    f"{key} on {element.element_id} is bound to parameter "
                    f"{bound_to}"
                    + (
                        f" (= {_shown(element.numeric_fields[key])}"
                        + (f" {parameter.unit}" if parameter is not None and parameter.unit else "")
                        + ")"
                    )
                    + "; "
                    + (
                        self._source_sentence(parameter)
                        if parameter is not None
                        else f"set {bound_to} instead."
                    )
                ),
            )
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
                    f"need parameters authored into {AUTHORED_RECORD_PATH} — "
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
        self._require_source(parameter)
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

    def _require_source(self, parameter: Parameter) -> None:
        """A derived quantity is not a control: its value is its expression's.

        The kernel refuses a scalar written over an expression as a
        conflicting declaration (``apply_state_record_operator``), so a
        candidate started on such a proposal is doomed before it runs. The
        refusal is made here instead, and it is actionable: it names the
        parameters the expression reads, with their values and locks, and
        the file a re-declaration would go into. Nothing is guessed: a source
        that is itself derived is said to be, not silently walked past.
        """

        if parameter.expr is None:
            return
        raise BlockedNeedsHuman(
            "the parameter is derived, not a control",
            question=(
                f"parameter {parameter.key} is derived by {parameter.expr!r}; "
                f"{self._source_sentence(parameter)}"
            ),
        )

    def _source_sentence(self, parameter: Parameter) -> str:
        """What to set instead of ``parameter``: its declared sources, each with its value and lock."""

        if parameter.expr is None:
            lock = f" (locked by {parameter.lock_authority})" if parameter.lock_authority else ""
            return f"set {parameter.key}{lock} instead."
        by_key = {item.key: item for item in self.projection.parameters}
        sources = parameter.reads()
        named = []
        for key in sources:
            source = by_key.get(key)
            if source is None:
                named.append(f"{key} (undeclared)")
                continue
            notes = []
            if source.expr is not None:
                notes.append(f"itself derived by {source.expr!r}")
            if source.lock_authority:
                notes.append(f"locked by {source.lock_authority}")
            unit = f" {source.unit}" if source.unit else ""
            named.append(
                f"{key} (= {_shown(source.value)}{unit}"
                + (", " + ", ".join(notes) if notes else "")
                + ")"
            )
        verb = "set" if len(sources) == 1 else "set one of"
        return (
            f"its value follows {_listed(list(sources))}. {verb} "
            f"{', '.join(named)} instead, or re-declare {parameter.key} "
            f"without an expression in {AUTHORED_RECORD_PATH}."
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


def _shown(value: int | float) -> str:
    """A number as the record holds it: ``3`` stays ``3``, ``2.4`` stays ``2.4``."""

    return str(value)
