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
the script, with what each component's facets unlock and the doors and windows
it hosts. ``hosted_opening_proposal`` is the first capability that meaning
unlocks (Stage C, spec §3.5): a door or a window on a component whose facets
say ``architectural.role = wall``; a block is realised as a wall in place under
the same element id, so it keeps its delivered object and its published top.
``design_proposal`` makes one proposal of the in-app agent's answer: a script
with its parameters, or parameters alone, then facets on what that leaves,
with what the answer keeps judged on the whole of it. ``kept_refs`` is what a
keep protects: a geometry id keeps its parts, never a shape a script placed
under it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
import math
import re
from typing import Any, Mapping, NamedTuple, Sequence

from archflow.semantics.facets import FACET_KEYS, suggest_facet_key
from archflow.state.state_record import Entity, StateRecord, apply_state_record_operator, component_facets
from monkeyarch.capabilities.element_producers import ElementProducerError, wall_along_line, wall_fields_from_block
from monkeyarch.capabilities.opening_solver import DoorType, WindowType
from monkeyarch.construction import (
    ConstructionError, ConstructionResult, compile_construction_script, geometry_view, made_by_construction,
)

from ..adapters.seats import SeatsError, load_seat_pack, seats_of
from ..transport.errors import StudioError
from .binding import ProjectBinding
from .intent import buildable_components, component_edit_proposal
from .projection import StateProjection, project_proposed_record
from .proposals import Proposal, continue_proposal, operator_of, proposal_from

# The component a fresh project models under (binding.py writes it with its seat).
MODEL_ROOT = "model"

# What a facet unlocks (spec §3.5): the capability is listed on an entity only
# once its facets say so. Task C4 serves the hosted-opening route.
_UNLOCKED: tuple[tuple[str, str, Mapping[str, Any]], ...] = (
    ("architectural.role", "wall",
     {"id": "hosted-opening", "route": "POST /api/proposals/hosted-opening", "needs": {}}),
)
_HOSTED_OPENING = "hosted-opening"

# Runtime sentences an agent reads whole in construction words, then the words
# of the layer rule (``LAYER_RULE_TOKENS``) a runtime sentence may still use:
# realisation names first, then the rest, so no token of the rule remains. An
# id is never rewritten: a word directly beside a hyphen or a word character is
# part of something else.
_CONSTRUCTION_SENTENCES: tuple[tuple[str, str], ...] = (
    ("only a prism, a capped loft or a wall can host voids", "only a solid or a capped loft solid can have cutters"),
    # The opening solver measures a door or window against its host.
    ("sill below the wall base", "sill is below the host's base"),
    ("lies outside the wall length", "lies outside the host's length"),
    ("head is above the wall top", "head is above the host's top"),
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
    """A script's proposal, and what the script reported (its shapes, and what it printed).

    ``result`` is ``None`` for a proposal no script made (``design_proposal``
    with facets or parameters alone).
    """

    proposal: Proposal
    result: ConstructionResult | None


class EnrichmentRequired(StudioError):
    """409 ``ENRICHMENT_REQUIRED``: a capability asked of geometry whose facets do not unlock it yet.

    The body is ``{code, detail, message, entity, facet, value}``: the facet to
    add (``POST /api/proposals/facets``) beside the ``{code, detail}`` every
    Studio refusal has, ``message`` being the same sentence as ``detail``.
    """

    def __init__(self, entity: str, facet: str, value: str, detail: str) -> None:
        super().__init__(409, "ENRICHMENT_REQUIRED", detail)
        self.entity = entity
        self.facet = facet
        self.value = value

    def body(self) -> dict[str, object]:
        return {**super().body(), "message": self.detail, "entity": self.entity, "facet": self.facet,
                "value": self.value}


def modelling_root(binding: ProjectBinding, projection: StateProjection) -> str:
    """Where new geometry goes: ``model`` when a seat builds it, else the first component a seat builds.

    A shape a script made (``made_by_construction``) is geometry, not where
    other geometry goes, so it is passed over while another buildable
    component remains. New geometry under a component no seat builds would
    be carried by the record and built by nobody, so a project whose seats
    build nothing is refused here, before anything runs.
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
    parts = _parts_by_component(projection.record)
    containers = [identifier for identifier in buildable if not made_by_construction(
        identifier, [element.entity_id for element in parts.get(identifier, ())])]
    return MODEL_ROOT if MODEL_ROOT in buildable else (containers or buildable)[0]


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
    is about the first geometry the script makes, changes or removes, by line,
    else the modelling root. It is not remembered and carries no source run;
    the caller places it.
    """

    root = modelling_root(binding, projection)
    named = _parameters_named(parameters)
    result = script_result(projection, script, root=root, parameters=parameters, summary=summary)
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
        proposal = proposal_from(component_edit_proposal(
            projection, edit, utterance=said, component_id=_first_geometry(projection.record, result) or root,
            keep_refs=keep_refs,
        ))
    except StudioError as exc:
        refused = _refused_at_line(exc, result, script)
        if refused is None:
            raise
        raise refused from exc
    return ConstructionProposal(proposal, result)


def script_result(
    projection: StateProjection,
    script: str,
    *,
    root: str,
    parameters: Sequence[Mapping[str, Any]] = (),
    summary: str | None = None,
) -> ConstructionResult:
    """``script`` compiled against ``projection``'s record under ``root``, the ``parameters`` staged first.

    What the script makes, changes and removes, as rows, before any proposal
    exists: ``param(key)`` binds a parameter the same request introduces. A
    refused script raises ``ConstructionRefused`` with its line, and parameters
    the record refuses raise the component edit's own refusal.
    """

    record = projection.record
    if parameters:
        said = summary or _parameters_named(parameters)
        staged = component_edit_proposal(projection, _edit(said, parameters=parameters), utterance=said)
        record = apply_state_record_operator(record, staged["state_record_operator"])
    try:
        return compile_construction_script(script, record, root_component_id=root)
    except ConstructionError as exc:
        raise ConstructionRefused(exc) from exc


def kept_refs(record: StateRecord, refs: Sequence[str]) -> tuple[str, ...]:
    """What keeping ``refs`` protects: each ref, and for a geometry id its parts and its child components'.

    The record protects what a change reaches, and a geometry id's form
    changes in its parts, never in its own row: a keep naming only the
    geometry id would let every part of it change. A shape a script made is
    its own geometry wherever it is parented (``made_by_construction``;
    lowering places them all under the modelling root), so a keep on the
    component above it never reaches it. A part id and a parameter ref are
    kept as they are; a ref the record does not declare is left for the
    record to refuse.
    """

    parts = _parts_by_component(record)
    children: dict[str, list[str]] = {}
    for component in record.entities_of("Component@1"):
        own = [element.entity_id for element in parts.get(component.entity_id, ())]
        if component.parent_id and not made_by_construction(component.entity_id, own):
            children.setdefault(component.parent_id, []).append(component.entity_id)
    kept = list(refs)
    seen: set[str] = set()
    pending = [ref.removeprefix("entity:") for ref in refs if ref.startswith("entity:")]
    while pending:
        identifier = pending.pop(0)
        if identifier in seen:
            continue
        seen.add(identifier)
        kept.extend(f"entity:{element.entity_id}" for element in parts.get(identifier, ()))
        kept.extend(f"entity:{child}" for child in children.get(identifier, ()))
        pending.extend(children.get(identifier, ()))
    return tuple(dict.fromkeys(kept))


def design_proposal(
    binding: ProjectBinding,
    projection: StateProjection,
    *,
    script: str | None = None,
    parameters: Sequence[Mapping[str, Any]] = (),
    facets: Sequence[Mapping[str, Any]] = (),
    summary: str | None = None,
    keep_refs: Sequence[str] = (),
    component_id: str | None = None,
) -> ConstructionProposal:
    """An agent's construction answer as one proposal against ``projection``.

    A script goes through ``construction_proposal`` with its parameters, and
    parameters without a script are a parameters-only component edit about
    ``component_id``, the geometry the request is about. Facets come last, so
    they may name geometry the same script makes: they are read against the
    design the first part would make (``facets_proposal``) and folded onto the
    same exact base (``continue_proposal``), so everything they do not name
    stays as that part left it. ``keep_refs`` are judged on the whole answer
    on that base: a part that reaches one makes the proposal a reviewable
    ``conflict`` naming it, as a script alone would. ``result`` is the
    script's report, ``None`` without a script.
    """

    # With facets the parts are made without the keep, which is judged once, on the whole answer.
    keep = () if facets else keep_refs
    made: ConstructionProposal | None = None
    if script is not None:
        made = construction_proposal(binding, projection, script, parameters=parameters, summary=summary,
                                     keep_refs=keep)
    elif parameters:
        said = summary or _parameters_named(parameters)
        made = ConstructionProposal(proposal_from(component_edit_proposal(
            projection, _edit(said, parameters=parameters, kept=keep), utterance=said, component_id=component_id,
            keep_refs=keep,
        )), None)
    if not facets:
        if made is None:
            raise StudioError(422, "CONSTRUCTION_INVALID",
                              "the answer carries no script, parameters or facets, so there is nothing to propose")
        return made
    if made is None:
        return ConstructionProposal(facets_proposal(projection, facets, summary=summary, keep_refs=keep_refs), None)
    successor = apply_state_record_operator(projection.record, operator_of(made.proposal, projection.record))
    faceted = facets_proposal(project_proposed_record(projection, successor), facets, summary=summary)
    said = summary or "; ".join(str((proposal.semantic_edit or {}).get("summary") or proposal.utterance)
                                for proposal in (made.proposal, faceted))
    return ConstructionProposal(continue_proposal(projection, made.proposal, replace(faceted, utterance=said),
                                                  keep_refs=keep_refs), made.result)


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


def hosted_opening_proposal(
    projection: StateProjection,
    host: str,
    *,
    kind: str,
    along: float,
    width: float,
    sill: float,
    head: float,
    shape: str | None = None,
    spring_height: float | None = None,
    family: Mapping[str, Any] | None = None,
    interface_ref: str | None = None,
    summary: str | None = None,
    keep_refs: Sequence[str] = (),
) -> Proposal:
    """A door or a window on ``host`` as one proposal (spec §3.5): the capability ``architectural.role = wall`` unlocks.

    ``host`` is a component with one element whose facets unlock
    ``hosted-opening``; without them the answer is ``EnrichmentRequired`` naming
    the facet to add. An element already realised as a wall gets the opening
    appended; a block is realised as a wall in place first
    (``wall_fields_from_block``: same element id, same solid, same
    ``obj-<element>`` and ``<element>-top``) or refused ``HOST_NOT_WALL_SHAPED``
    with the reason. The opening is ``opening-<n>``, n one more than the
    openings there are, skipping ids in use; a ``family`` becomes the type
    ``<opening>-type``, checked by constructing it (``FAMILY_INVALID``). An
    ``interface_ref`` must be a relationship ref one of the record's
    connections declares (``INTERFACE_UNKNOWN`` names the ones it does); left
    out, the opening serves no connection and cites none. The edit goes through
    the component-edit path every design edit takes; the record's refusal of it
    (an opening outside the host, above its top, a family that does not fit)
    answers ``OPENING_INVALID`` in construction words.
    """

    element = _opening_host(projection.record, host)
    fields = dict(element.fields)
    if fields.get("producer") != "wall":
        try:
            fields = wall_fields_from_block(fields, element.entity_id)
        except ElementProducerError as exc:
            reason = str(exc).removeprefix(f"{element.entity_id}: ")
            raise StudioError(422, "HOST_NOT_WALL_SHAPED",
                              f"{host} cannot take a door or window as it is drawn: {reason}") from exc
    params = dict(fields.get("params") or {})
    openings = [dict(opening) for opening in params.get("openings") or ()]
    types = [dict(item) for item in params.get("types") or ()]
    taken = {opening.get("opening_id") for opening in openings} | {item.get("type_id") for item in types}
    number = len(openings) + 1
    while f"opening-{number}" in taken or f"opening-{number}-type" in taken:
        number += 1
    opening_id = f"opening-{number}"
    opening: dict[str, Any] = {"opening_id": opening_id, "kind": kind, "along": along, "width": width,
                               "sill": sill, "head": head}
    if shape is not None and shape != "rectangular":
        opening["shape"] = shape
    if spring_height is not None:
        opening["spring_height"] = spring_height
    if interface_ref is not None:
        declared = _declared_interfaces(projection.record)
        if interface_ref not in declared:
            raise StudioError(422, "INTERFACE_UNKNOWN", f"no connection of this project declares {interface_ref}; "
                              + (f"its connections declare {', '.join(declared)}" if declared else
                                 "it declares no connection between spaces yet"))
        opening["interface_ref"] = interface_ref
    if family is not None:
        filling = _opening_family(kind, f"{opening_id}-type", family)
        types.append(filling.to_dict())
        opening["type_id"] = filling.type_id
    params["openings"] = [*openings, opening]
    if types:
        params["types"] = types
    said = summary or f"hosted opening: {kind} {opening_id} in {host}"
    edit = _edit(said, entities=[{"entity_id": element.entity_id, "schema": "Element@1",
                                  "parent_id": element.parent_id, "fields": {**fields, "params": params}}],
                 kept=keep_refs)
    try:
        return proposal_from(component_edit_proposal(projection, edit, utterance=said, component_id=host,
                                                     keep_refs=keep_refs))
    except StudioError as exc:
        if exc.code == "SEMANTIC_EDIT_INVALID":
            said = in_construction_words(exc.detail.removeprefix(f"{element.entity_id}: "))
            raise StudioError(422, "OPENING_INVALID", f"{host} cannot take this {kind}: {said}") from exc
        raise


def construction_model(projection: StateProjection) -> dict[str, Any]:
    """The record in construction terms, keyed as the wire has it.

    ``entities`` is one row per component with geometry (``geometry_view``:
    id, form, bounds, cuts, cutBy, hidden, and parts, ``None`` unless it has
    several) plus its ``facets``, the ``capabilities`` they unlock, the
    ``openings`` a component of one element hosts, and the ``alongLine`` a
    door's or window's ``along`` is measured on, when it can take one.
    ``levels`` and ``parameters`` are what ``level(id)`` and ``param(key)`` can name.
    """

    record = projection.record
    components = {entity.entity_id: entity for entity in record.entities_of("Component@1")}
    parts_of = _parts_by_component(record)
    entities = []
    for row in geometry_view(record):
        facets = component_facets(components[row["id"]])
        capabilities = capabilities_of(facets)
        parts = parts_of.get(row["id"], [])
        element = parts[0] if len(parts) == 1 else None
        hosts = element is not None and any(capability["id"] == _HOSTED_OPENING for capability in capabilities)
        entities.append({**row, "parts": row.get("parts"), "facets": facets, "capabilities": capabilities,
                         "openings": [] if element is None else _openings_of(element),
                         "alongLine": _along_line_of(element) if hosts else None})
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


def _first_geometry(record: StateRecord, result: ConstructionResult) -> str | None:
    """The component of the first shape the script made, changed or removed, by line; ``None`` for none.

    A part of a geometry id with several is named by the component it
    belongs to, since a proposal is about a component.
    """

    first = min(result.report, key=lambda row: row["line"], default=None)
    if first is None:
        return None
    identifier = str(first["id"])
    entity = next((item for item in record.entities if item.entity_id == identifier), None)
    if entity is not None and entity.schema == "Element@1":
        return str(entity.fields.get("component_id") or entity.parent_id)
    return identifier


def _parameters_named(parameters: Sequence[Mapping[str, Any]]) -> str:
    """What a change of these parameters says when nothing else names it."""

    return "parameters: " + ", ".join(dict.fromkeys(str(parameter.get("key")) for parameter in parameters))


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


def _parts_by_component(record: StateRecord) -> dict[str, list[Entity]]:
    """Each component's elements, in record order: those naming it as their component, else as their parent."""

    parts: dict[str, list[Entity]] = {}
    for element in record.entities_of("Element@1"):
        parts.setdefault(str(element.fields.get("component_id") or element.parent_id), []).append(element)
    return parts


def _opening_host(record: StateRecord, host: str) -> Entity:
    """The one element a door or window goes into, once ``host``'s facets unlock ``hosted-opening``.

    Refused by name: an id the record lacks (404 ``ENTITY_UNKNOWN``), anything
    but a component of one element (422 ``HOST_INVALID``), a component without
    the facet (409 ``ENRICHMENT_REQUIRED``) and a shape that cuts another (422
    ``HOST_INVALID``): a cutter is kept hidden inside what it cuts, so an
    opening in it would open nothing.
    """

    entity = next((item for item in record.entities if item.entity_id == host), None)
    if entity is None:
        raise StudioError(404, "ENTITY_UNKNOWN", f"{host} is not in this project; GET /api/construction/model lists its ids")
    if entity.schema != "Component@1":
        owner = entity.fields.get("component_id") or entity.parent_id if entity.schema == "Element@1" else None
        raise StudioError(422, "HOST_INVALID",
                          f"{host} is {entity.schema}; a door or window is asked of a geometry id (a component)"
                          + (f", here {owner}" if owner else ""))
    parts = _parts_by_component(record).get(host, [])
    if len(parts) != 1:
        raise StudioError(422, "HOST_INVALID", f"{host} has " + (
            "no geometry" if not parts else "several parts (" + ", ".join(part.entity_id for part in parts) + ")")
            + "; a door or window goes into geometry of one part")
    facet, value, _ = next(item for item in _UNLOCKED if item[2]["id"] == _HOSTED_OPENING)
    if component_facets(entity).get(facet) != value:
        raise EnrichmentRequired(host, facet, value, f"{host} needs {facet} = {value} before it can host a door or "
                                                     "window; add it with POST /api/proposals/facets")
    [element] = parts
    cut = sorted({str(other.fields.get("component_id") or other.parent_id) for other in record.entities_of("Element@1")
                  if element.entity_id in _voids_named(other)})
    if cut:
        raise StudioError(422, "HOST_INVALID", f"{host} cuts {', '.join(cut)}; a door or window goes into geometry "
                                               "that is delivered, so uncut it first")
    return element


def _declared_interfaces(record: StateRecord) -> list[str]:
    """The relationship refs the record's connections declare: the interfaces an opening may serve."""

    return sorted({str(ref) for connection in record.entities_of("Connection@1")
                   for ref in (connection.fields.get("relationship_refs") or ())})


def _voids_named(element: Entity) -> tuple[str, ...]:
    references = element.fields.get("references")
    voids = references.get("voids") if isinstance(references, Mapping) else None
    return tuple(void for void in voids if isinstance(void, str)) if isinstance(voids, (list, tuple)) else ()


def _opening_family(kind: str, type_id: str, family: Mapping[str, Any]) -> DoorType | WindowType:
    """The family that fills a door or window, checked by constructing the type the opening solver fills it with."""

    kind_type = DoorType if kind == "door" else WindowType
    takes = [item.name for item in dataclasses.fields(kind_type) if item.name != "type_id"]
    unknown, missing = sorted(set(family) - set(takes)), [name for name in takes if name not in family]
    if unknown or missing:
        raise StudioError(422, "FAMILY_INVALID", f"a {kind} family takes {', '.join(takes)}"
                          + (f"; not {', '.join(unknown)}" if unknown else "")
                          + (f"; {', '.join(missing)} missing" if missing else ""))
    try:
        return kind_type(type_id=type_id, **family)
    except (TypeError, ValueError) as exc:
        raise StudioError(422, "FAMILY_INVALID", f"the {kind} family: {exc}") from exc


def _openings_of(element: Entity) -> list[dict[str, Any]]:
    """The doors and windows an element realised as a wall hosts, as asked for; never raises."""

    params = element.fields.get("params")
    openings = params.get("openings") if isinstance(params, Mapping) and element.fields.get("producer") == "wall" else None
    if not isinstance(openings, (list, tuple)):
        return []
    return [{"id": opening["opening_id"], "kind": opening["kind"],
             **{key: float(opening[key]) if _finite(opening.get(key)) else None for key in ("along", "width", "sill", "head")}}
            for opening in openings if isinstance(opening, Mapping)
            and isinstance(opening.get("opening_id"), str) and isinstance(opening.get("kind"), str)]


def _along_line_of(element: Entity) -> dict[str, list[float]] | None:
    """Where ``along`` is measured on an element that is, or can be realised as, a wall; ``None`` when it cannot."""

    fields = element.fields
    try:
        if fields.get("producer") != "wall":
            fields = wall_fields_from_block(fields, element.entity_id)
        start, end = wall_along_line(fields["references"]["line"])
    except (ElementProducerError, KeyError, TypeError):
        return None
    return {"start": list(start), "end": list(end)}
