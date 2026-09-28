"""Construction first (#419, spec §3.2-§3.5): scripts, facets and the model as the Studio serves them.

An agent authors geometry with one construction script. ``construction_proposal``
compiles it against the exact record (``monkeyarch.construction``) under the
project's modelling root and hands the rows it lowered to the one component-edit
path every other design edit takes; how each shape is realised was chosen by the
compiler, never by the caller. A refusal names the script line that caused it:
the compiler's own, or the record's refusal of the rows the script produced,
answered at the line that made the shape it names and said in construction
words (the layer rule).

``facets_proposal`` sets or removes meaning on components and changes nothing
else (D-419-0). ``construction_model`` reads a record back in the same words as
the script, with what each component's facets unlock.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, NamedTuple, Sequence

from archflow.semantics.facets import FACET_KEYS, suggest_facet_key
from archflow.state.state_record import apply_state_record_operator, component_facets
from monkeyarch.construction import ConstructionError, ConstructionResult, compile_construction_script, geometry_view

from ..adapters.seats import SeatsError, load_seat_pack, seats_of
from ..transport.errors import StudioError
from .binding import ProjectBinding
from .intent import buildable_components, component_edit_proposal
from .projection import StateProjection
from .proposals import Proposal, proposal_from

# The component a fresh project models under (binding.py writes it with its seat).
MODEL_ROOT = "model"

# What a facet unlocks (spec §3.5): the capability is listed on an entity only
# once its facets say so. Task C4 serves the hosted-opening route.
_UNLOCKED: tuple[tuple[str, str, Mapping[str, Any]], ...] = (
    ("architectural.role", "wall",
     {"id": "hosted-opening", "route": "POST /api/proposals/hosted-opening", "needs": {}}),
)

# Runtime sentences an agent reads whole in construction words, then the words
# of the layer rule (``LAYER_RULE_TOKENS``) a runtime sentence may still use:
# realisation names first, then the rest, so no token of the rule remains. An
# id is never rewritten: a word directly beside a hyphen or a word character is
# part of something else.
_CONSTRUCTION_SENTENCES: tuple[tuple[str, str], ...] = (
    ("only a prism, a capped loft or a wall can host voids", "only a solid or a capped loft solid can have cutters"),
)
_CONSTRUCTION_WORDS = tuple(
    (re.compile(rf"(?<![\w-]){re.escape(name)}(s?)(?![\w-])", re.IGNORECASE), one, several)
    for name, one, several in (
        ("planar-surface", "face", "faces"),
        ("prism", "solid", "solids"),
        ("curve", "path", "paths"),
        ("loft", "loft solid", "loft solids"),
        ("wall", "solid with doors or windows", "solids with doors or windows"),
        ("column-array", "column row", "column rows"),
        ("producer", "realisation", "realisations"),
        ("semantic_kind", "kind", "kinds"),
        ("semantickind", "kind", "kinds"),
        ("boolean", "cut", "cuts"),
        ("aperture", "opening", "openings"),
        ("topology", "shape", "shapes"),
        ("occt", "kernel", "kernels"),
    )
)


class ConstructionRefused(StudioError):
    """A refused construction script: 422 ``CONSTRUCTION_INVALID`` with its line, column and source line.

    The body is ``{code, detail, line, column, sourceLine, message}``: the
    compiler's own refusal shape (``ConstructionError.to_dict``) beside the
    ``{code, detail}`` every Studio refusal has, ``detail`` being the sentence.
    """

    def __init__(self, error: ConstructionError) -> None:
        super().__init__(422, "CONSTRUCTION_INVALID", error.message)
        self.error = error

    @property
    def line(self) -> int | None:
        return self.error.line

    @property
    def column(self) -> int | None:
        return self.error.column

    @property
    def source_line(self) -> str | None:
        return self.error.source_line

    def body(self) -> dict[str, object]:
        return {**super().body(), **self.error.to_dict()}


class ConstructionProposal(NamedTuple):
    """A script's proposal, and what the script reported (its shapes, and what it printed)."""

    proposal: Proposal
    result: ConstructionResult


def modelling_root(binding: ProjectBinding, projection: StateProjection) -> str:
    """Where new geometry goes: ``model`` when a seat builds it, else the first component a seat builds.

    New geometry under a component no seat builds would be carried by the
    record and built by nobody, so a project whose seats build nothing is
    refused here, before anything runs.
    """

    try:
        seats = seats_of(load_seat_pack(binding.repository))
    except SeatsError as exc:
        raise StudioError(422, "SEATS_UNAVAILABLE", str(exc)) from exc
    components = sorted(entity.entity_id for entity in projection.record.entities_of("Component@1"))
    buildable = [identifier for identifier in buildable_components(projection, seats) if identifier in components]
    if not buildable:
        raise StudioError(
            422, "COMPONENT_NOT_BUILT",
            f"no seat builds any of {components}, so new geometry would be built by nobody: have the project's "
            "seat pack own one of them. Nothing was run.",
        )
    return MODEL_ROOT if MODEL_ROOT in buildable else buildable[0]


def construction_proposal(
    binding: ProjectBinding,
    projection: StateProjection,
    script: str,
    *,
    parameters: Sequence[Mapping[str, Any]] = (),
    summary: str | None = None,
    keep_refs: Sequence[str] = (),
) -> ConstructionProposal:
    """One construction script as a proposal against ``projection``, with what the script reported.

    ``parameters`` (the record's parameter shape) are added or changed with the
    script, and the script is read against a record that already has them, so
    ``param(key)`` binds a parameter the same request introduces. The proposal
    is not remembered and carries no source run; the caller places it.
    """

    root = modelling_root(binding, projection)
    record = projection.record
    named = "parameters: " + ", ".join(dict.fromkeys(str(parameter.get("key")) for parameter in parameters))
    if parameters:
        staged = component_edit_proposal(projection, _edit(summary or named, parameters=parameters),
                                         utterance=summary or named)
        record = apply_state_record_operator(record, staged["state_record_operator"])
    try:
        result = compile_construction_script(script, record, root_component_id=root)
    except ConstructionError as exc:
        raise ConstructionRefused(exc) from exc
    shaped = bool(result.entities or result.remove_entity_ids)
    if not shaped and not parameters:
        raise ConstructionRefused(ConstructionError(
            "the script makes, changes and removes no shape, so there is nothing to propose; "
            "GET /api/construction/model reads the shapes there are"))
    # Parameters sent with a script that leaves no shape are the change, and say so.
    said = summary or (result.summary if shaped else named)
    edit = _edit(said, entities=result.entities, parameters=parameters,
                 remove_entity_ids=result.remove_entity_ids, kept=keep_refs)
    try:
        proposal = proposal_from(component_edit_proposal(projection, edit, utterance=said, keep_refs=keep_refs))
    except StudioError as exc:
        refused = _refused_at_line(exc, result, script)
        if refused is None:
            raise
        raise refused from exc
    return ConstructionProposal(proposal, result)


def in_construction_words(message: str) -> str:
    """A runtime sentence in construction words: no realisation name or other layer-rule word remains.

    Ids stay as they are, so an id an agent chose with such a word in it is the
    only way one can still appear.
    """

    said: list[str] = []
    for runtime, words in _CONSTRUCTION_SENTENCES:
        if runtime in message:
            # Held aside while words are translated: a sentence said in construction words is final.
            said.append(words)
            message = message.replace(runtime, f"\0{len(said) - 1}\0")
    for pattern, one, several in _CONSTRUCTION_WORDS:
        message = pattern.sub(lambda found: several if found.group(1) else one, message)
    for index, words in enumerate(said):
        message = message.replace(f"\0{index}\0", words)
    return message


def facets_proposal(
    projection: StateProjection,
    targets: Sequence[Mapping[str, Any]],
    *,
    summary: str | None = None,
    keep_refs: Sequence[str] = (),
) -> Proposal:
    """Facets set on, or taken from, components (``[{id, set: {key: value}, remove: [key]}]``) as one proposal.

    Each target is upserted as its ``Component@1`` with every other field as it
    was, so its elements, objects, datums and dependency edges stay
    byte-identical. Text values lose surrounding whitespace and may not be
    blank. A map emptied by removals is written as ``{}``: the component-edit
    path merges an upsert's fields over the existing ones by key, so a field
    left out would come back; a component that never had facets gains none.
    An edit that would leave every target as it is proposes nothing and is refused.
    """

    existing = {entity.entity_id: entity for entity in projection.record.entities}
    rows: list[dict[str, Any]] = []
    said: list[str] = []
    for target in targets:
        identifier = target.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise _facets_invalid("every target names the geometry id it is about")
        if any(row["entity_id"] == identifier for row in rows):
            raise _facets_invalid(f"{identifier} is named twice; say everything about it in one target")
        entity = existing.get(identifier)
        if entity is None:
            raise StudioError(404, "ENTITY_UNKNOWN",
                              f"{identifier} is not in this project; GET /api/construction/model lists its ids")
        if entity.schema != "Component@1":
            owner = entity.fields.get("component_id") or entity.parent_id if entity.schema == "Element@1" else None
            raise StudioError(
                422, "FACETS_TARGET_INVALID",
                f"{identifier} is {entity.schema}; facets belong to a geometry id (a component)"
                + (f", here {owner}" if owner else ""),
            )
        current = component_facets(entity)
        values, removed = _facet_changes(identifier, target.get("set"), target.get("remove"), current)
        facets = {key: value for key, value in current.items() if key not in removed}
        facets.update(values)
        fields = dict(entity.fields)
        if facets or "facets" in fields:
            fields["facets"] = facets
        rows.append({"entity_id": identifier, "schema": "Component@1", "parent_id": entity.parent_id,
                     "fields": fields})
        said.append(f"{identifier} " + ", ".join([f"+{key}={value}" for key, value in values.items()]
                                                  + [f"-{key}" for key in removed]))
    if not rows:
        raise _facets_invalid("name at least one target")
    if all(row["fields"] == existing[row["entity_id"]].fields for row in rows):
        raise _facets_invalid(f"the facets of {', '.join(row['entity_id'] for row in rows)} already say that, "
                              "so there is nothing to propose")
    summary = summary or "facets: " + "; ".join(said)
    try:
        return proposal_from(component_edit_proposal(
            projection, _edit(summary, entities=rows, kept=keep_refs), utterance=summary,
            component_id=rows[0]["entity_id"], keep_refs=keep_refs,
        ))
    except StudioError as exc:
        if exc.code == "SEMANTIC_EDIT_INVALID":
            # The record refuses an unknown key or value, naming the nearest key or the allowed values.
            raise _facets_invalid(exc.detail) from exc
        raise


def capabilities_of(facets: Mapping[str, str]) -> list[dict[str, Any]]:
    """What these facets unlock, each with the route that uses it; nothing without them."""

    return [{**capability, "needs": dict(capability["needs"])}
            for key, value, capability in _UNLOCKED if facets.get(key) == value]


def construction_model(projection: StateProjection) -> dict[str, Any]:
    """The record in construction terms, keyed as the wire has it.

    ``entities`` is one row per component with geometry (``geometry_view``:
    id, form, bounds, cuts, cutBy, hidden, and parts, ``None`` unless it has
    several) plus its ``facets`` and the ``capabilities`` they unlock.
    ``levels`` and ``parameters`` are what ``level(id)`` and ``param(key)`` can name.
    """

    record = projection.record
    components = {entity.entity_id: entity for entity in record.entities_of("Component@1")}
    entities = []
    for row in geometry_view(record):
        facets = component_facets(components[row["id"]])
        entities.append({**row, "parts": row.get("parts"), "facets": facets, "capabilities": capabilities_of(facets)})
    return {
        "projectId": projection.project_id,
        "stateDigest": projection.state_digest,
        "recordDigest": projection.record_digest,
        "levels": [{"id": level.entity_id, "elevation": float(level.fields["elevation"])}
                   for level in record.entities_of("Level@1") if _finite(level.fields.get("elevation"))],
        "parameters": [{"key": parameter.key, "value": parameter.value, "unit": parameter.unit}
                       for parameter in record.parameters],
        "entities": entities,
    }


def _edit(summary: str, *, entities: Sequence[Mapping[str, Any]] = (), parameters: Sequence[Mapping[str, Any]] = (),
          remove_entity_ids: Sequence[str] = (), kept: Sequence[str] = ()) -> dict[str, Any]:
    """A component edit in the shape ``component_edit_proposal`` takes."""

    return {
        "summary": summary, "entities": [dict(entity) for entity in entities],
        "parameters": [dict(parameter) for parameter in parameters], "relations": [],
        "removeEntityIds": list(remove_entity_ids), "removeParameterKeys": [], "removeRelationIds": [],
        "protected": [], "kept": list(kept),
    }


def _refused_at_line(exc: StudioError, result: ConstructionResult, script: str) -> ConstructionRefused | None:
    """The record's refusal of a script's rows, answered at the script line that made a shape it names.

    Only the edit's own refusal is mapped, and only when it names one of the
    script's shapes (a geometry id, its element, or that element's top): the
    first such shape in script order gives the line. Anything else (a keep ref,
    a parameter) is not a script line and passes through unchanged.
    """

    if exc.code != "SEMANTIC_EDIT_INVALID":
        return None
    named = [line for identifier, line in result.line_of.items()
             if line is not None and re.search(rf"(?<![\w-]){re.escape(identifier)}(?:-top)?(?![\w-])", exc.detail)]
    if not named:
        return None
    line = min(named)
    lines = script.splitlines()
    text = lines[line - 1] if 1 <= line <= len(lines) else None
    return ConstructionRefused(ConstructionError(
        in_construction_words(exc.detail), line=line,
        column=None if text is None else len(text) - len(text.lstrip()) + 1,
        source_line=None if text is None else text.strip(),
    ))


def _facet_changes(identifier: str, values: object, removed: object,
                   current: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """One target's facets to set (text, stripped, not blank) and keys to remove, checked."""

    values = {} if values is None else values
    removed = [] if removed is None else removed
    if not isinstance(values, Mapping) or not isinstance(removed, (list, tuple)):
        raise _facets_invalid(f"{identifier}: set is a map of facet keys to text and remove a list of facet keys")
    if not values and not removed:
        raise _facets_invalid(f"{identifier}: say which facets to set or remove")
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise _facets_invalid(f"{identifier}: facet {key} needs a value that is text and not blank")
        cleaned[key] = value.strip()
    for key in removed:
        if not isinstance(key, str) or (key not in FACET_KEYS and key not in current):
            near = ", ".join(suggest_facet_key(str(key))) or "none close"
            raise _facets_invalid(f"{identifier}: facet {key!r} is not registered; nearest: {near}")
    both = sorted(set(cleaned) & set(removed))
    if both:
        raise _facets_invalid(f"{identifier}: {', '.join(both)} is both set and removed; say one of them")
    return cleaned, list(dict.fromkeys(removed))


def _facets_invalid(detail: str) -> StudioError:
    return StudioError(422, "FACETS_INVALID", detail)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
