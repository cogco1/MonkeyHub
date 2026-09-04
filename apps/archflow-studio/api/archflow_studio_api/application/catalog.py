"""The component catalog: what the tree can be asked, derived from the record
and the reference run's inspection — never from the conversation.

The State Record is the truth of every value: an ``Element@1`` row is a
realization, its ``params`` are the numbers a change can move, its
``references`` and the declared relations are the edges. What the record does
not say about itself is *how far the tree covers the model on screen*: which
exported objects belong to which element, which components have a realization
at all, and which objects an architect can see and point at that no element
answers for. That is what this catalog adds, and it adds it as derived facts
with a source and a confidence, so a card can say "editable" or "visible in the
model, missing from the catalog" without either word being a guess.

Sources, in the record's own terms:

- elements and their scalar params: the authored record (``source=authored``,
  confidence 1.0);
- objects: the reference run's ``seat-3dm-inspection`` records, joined to
  elements by the export's own naming (``obj-<elementId>[-…]`` under the
  claimed ``archflow:component``), through the same rule ``POST /api/pick/resolve``
  applies to a click;
- edges: ``StateRecord.dependency_edges`` and ``closure`` — the one rule.

Nothing here invents an element for an object that has none, and nothing here
recommends a neighbouring element's field in its place: an unbound object is
reported as ``MODEL_VISIBLE_CATALOG_MISSING`` and stays that until an authored
control exists.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from archflow.state.state_record import StateRecord

from ..transport.errors import StudioError
from .binding import ProjectBinding
from .compare import Shape, shapes_of
from .pick import element_of_object
from .projection import StateProjection

# ---- the words a card may use; the catalog decides them, the shell prints them

AUTHORED = "authored"
DERIVED = "derived"

EDITABLE = "editable"
LOCKED = "locked"
DERIVED_STATUS = "derived"
MISSING = "missing"

BOUND = "bound"
MODEL_VISIBLE_CATALOG_MISSING = "MODEL_VISIBLE_CATALOG_MISSING"
UNKNOWN_COMPONENT = "UNKNOWN_COMPONENT"
AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class Capability:
    """One number a change can move, with where it came from and whether it may move."""

    element_id: str
    key: str
    value: int | float
    value_type: str  # integer | number
    unit: str | None
    bounds: tuple[float, float] | None
    source: str
    confidence: float
    status: str
    validator_refs: tuple[str, ...]

    @property
    def capability_id(self) -> str:
        return f"entity:{self.element_id}#params.{self.key}"


@dataclass(frozen=True, slots=True)
class CatalogElement:
    element_id: str
    component_id: str
    producer: str
    capabilities: tuple[Capability, ...]
    object_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObjectBinding:
    """One exported object and the element the catalog can name for it."""

    name: str
    component_id: str | None
    producer_op: str | None
    element_id: str | None
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class CatalogComponent:
    component_id: str
    parent_id: str | None
    children: tuple[str, ...]
    element_ids: tuple[str, ...]
    descendant_element_ids: tuple[str, ...]
    capability_count: int
    states: tuple[str, ...]
    object_count: int
    unbound_object_count: int
    closure: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Coverage:
    objects: int
    bound: int
    unbound: int
    ambiguous: int
    unknown_component: int


@dataclass(frozen=True, slots=True)
class Catalog:
    components: tuple[CatalogComponent, ...]
    elements: tuple[CatalogElement, ...]
    objects: tuple[ObjectBinding, ...]
    coverage: Coverage
    inspection_run: str | None
    honesty: tuple[str, ...]

    def component(self, component_id: str) -> CatalogComponent | None:
        for item in self.components:
            if item.component_id == component_id:
                return item
        return None

    def element(self, element_id: str) -> CatalogElement | None:
        for item in self.elements:
            if item.element_id == element_id:
                return item
        return None

    def editable_descendants(self, component_id: str) -> tuple[CatalogElement, ...]:
        """The elements under a component that have at least one editable capability."""

        node = self.component(component_id)
        if node is None:
            return ()
        wanted = set(node.descendant_element_ids)
        return tuple(
            element
            for element in self.elements
            if element.element_id in wanted
            and any(cap.status == EDITABLE for cap in element.capabilities)
        )

    def capability(self, capability_id: str) -> Capability | None:
        for element in self.elements:
            for cap in element.capabilities:
                if cap.capability_id == capability_id:
                    return cap
        return None


def catalog_of(binding: ProjectBinding, projection: StateProjection) -> Catalog:
    """The catalog for one projection, with the reference run's objects when it has them.

    A reference run without exports leaves object coverage unknown; the catalog
    says so in its honesty lines and every component reports zero objects
    rather than a guessed count.
    """

    run_id = projection.reference.run.run_id
    shapes: tuple[Shape, ...] | None
    honesty: list[str] = []
    try:
        shapes = shapes_of(binding, run_id)
    except StudioError as exc:
        shapes = None
        honesty.append(
            f"object coverage unknown: reference run {run_id} has no inspection to "
            f"join ({exc.code})"
        )
    return build_catalog(projection, shapes, inspection_run=run_id if shapes else None, honesty=honesty)


def build_catalog(
    projection: StateProjection,
    shapes: Sequence[Shape] | None,
    *,
    inspection_run: str | None,
    honesty: Sequence[str] = (),
) -> Catalog:
    record = projection.record
    validators = _validators_by_element(record)
    elements = tuple(
        CatalogElement(
            element_id=row.element_id,
            component_id=row.component_id,
            producer=row.producer,
            capabilities=tuple(
                Capability(
                    element_id=row.element_id,
                    key=key,
                    value=value,
                    value_type="integer" if isinstance(value, int) else "number",
                    unit=None,
                    bounds=None,
                    source=AUTHORED,
                    confidence=1.0,
                    status=EDITABLE,
                    validator_refs=validators.get(row.element_id, ()),
                )
                for key, value in row.numeric_fields.items()
            ),
            object_names=(),
        )
        for row in projection.elements
    )
    objects = tuple(_bind_objects(projection, shapes)) if shapes is not None else ()
    names_by_element: dict[str, list[str]] = defaultdict(list)
    for binding_ in objects:
        if binding_.element_id is not None:
            names_by_element[binding_.element_id].append(binding_.name)
    elements = tuple(
        CatalogElement(
            element_id=item.element_id,
            component_id=item.component_id,
            producer=item.producer,
            capabilities=item.capabilities,
            object_names=tuple(sorted(names_by_element.get(item.element_id, ()))),
        )
        for item in elements
    )
    components = _components(record, elements, objects)
    lines = list(honesty)
    unbound = [item for item in objects if item.status == MODEL_VISIBLE_CATALOG_MISSING]
    if unbound:
        lines.append(
            f"{len(unbound)} objects are visible in the model and missing from the "
            "catalog: no Element@1 row answers for them, so they cannot be edited "
            "until an authored control exists"
        )
    without = [c for c in components if not c.descendant_element_ids]
    if without:
        lines.append(
            f"{len(without)} of {len(components)} components have no realization "
            "(no Element@1 row under them)"
        )
    return Catalog(
        components=components,
        elements=elements,
        objects=objects,
        coverage=Coverage(
            objects=len(objects),
            bound=sum(1 for item in objects if item.status == BOUND),
            unbound=len(unbound),
            ambiguous=sum(1 for item in objects if item.status == AMBIGUOUS),
            unknown_component=sum(1 for item in objects if item.status == UNKNOWN_COMPONENT),
        ),
        inspection_run=inspection_run,
        honesty=tuple(lines),
    )


def _validators_by_element(record: StateRecord) -> Mapping[str, tuple[str, ...]]:
    found: dict[str, list[str]] = defaultdict(list)
    for relation in record.relations:
        for end in (relation.subject, relation.object):
            found[end].append(f"relation:{relation.relation_id}")
    return {key: tuple(sorted(set(value))) for key, value in found.items()}


def _bind_objects(projection: StateProjection, shapes: Sequence[Shape]) -> list[ObjectBinding]:
    declared = {entity.entity_id for entity in projection.record.entities_of("Component@1")}
    elements_by_component: dict[str, list[str]] = defaultdict(list)
    for row in projection.elements:
        elements_by_component[row.component_id].append(row.element_id)
    out: list[ObjectBinding] = []
    for shape in shapes:
        component_id = shape.component_id
        if component_id is None or component_id not in declared:
            out.append(
                ObjectBinding(
                    name=shape.name,
                    component_id=component_id,
                    producer_op=shape.producer_op,
                    element_id=None,
                    status=UNKNOWN_COMPONENT,
                    detail=(
                        "the object names no component the record declares"
                        if component_id is None
                        else f"the object names component {component_id!r}, which the record does not declare"
                    ),
                )
            )
            continue
        element_id = element_of_object(projection, shape.name, component_id)
        if element_id is not None:
            out.append(
                ObjectBinding(
                    name=shape.name,
                    component_id=component_id,
                    producer_op=shape.producer_op,
                    element_id=element_id,
                    status=BOUND,
                    detail=f"obj-{element_id} under {component_id}",
                )
            )
            continue
        siblings = elements_by_component.get(component_id, [])
        out.append(
            ObjectBinding(
                name=shape.name,
                component_id=component_id,
                producer_op=shape.producer_op,
                element_id=None,
                status=MODEL_VISIBLE_CATALOG_MISSING,
                detail=(
                    f"visible in the model under {component_id}, but no Element@1 row "
                    f"names it"
                    + (
                        f" (the component's elements are {', '.join(sorted(siblings))}, none of which produced it)"
                        if siblings
                        else " (the component has no Element@1 row at all)"
                    )
                ),
            )
        )
    return out


def _components(
    record: StateRecord,
    elements: Sequence[CatalogElement],
    objects: Sequence[ObjectBinding],
) -> tuple[CatalogComponent, ...]:
    nodes = record.entities_of("Component@1")
    children: dict[str | None, list[str]] = defaultdict(list)
    for node in nodes:
        children[node.parent_id].append(node.entity_id)
    direct: dict[str, list[str]] = defaultdict(list)
    for element in elements:
        direct[element.component_id].append(element.element_id)
    objects_by_component: dict[str, list[ObjectBinding]] = defaultdict(list)
    for item in objects:
        if item.component_id is not None:
            objects_by_component[item.component_id].append(item)
    capabilities_by_element = {element.element_id: element.capabilities for element in elements}

    def descendants(component_id: str) -> list[str]:
        found: list[str] = []
        stack = [component_id]
        while stack:
            current = stack.pop()
            found.extend(direct.get(current, ()))
            stack.extend(children.get(current, ()))
        return sorted(found)

    def subtree_objects(component_id: str) -> list[ObjectBinding]:
        found: list[ObjectBinding] = []
        stack = [component_id]
        while stack:
            current = stack.pop()
            found.extend(objects_by_component.get(current, ()))
            stack.extend(children.get(current, ()))
        return found

    out: list[CatalogComponent] = []
    for node in nodes:
        element_ids = descendants(node.entity_id)
        caps = [cap for eid in element_ids for cap in capabilities_by_element.get(eid, ())]
        states: list[str] = []
        if any(cap.status == EDITABLE for cap in caps):
            states.append(EDITABLE)
        if any(cap.status == LOCKED for cap in caps):
            states.append(LOCKED)
        if any(cap.status == DERIVED_STATUS for cap in caps):
            states.append(DERIVED_STATUS)
        subtree = subtree_objects(node.entity_id)
        unbound = [item for item in subtree if item.status != BOUND]
        if not element_ids or (subtree and unbound):
            states.append(MISSING)
        out.append(
            CatalogComponent(
                component_id=node.entity_id,
                parent_id=node.parent_id,
                children=tuple(sorted(children.get(node.entity_id, ()))),
                element_ids=tuple(sorted(direct.get(node.entity_id, ()))),
                descendant_element_ids=tuple(element_ids),
                capability_count=len(caps),
                states=tuple(states),
                object_count=len(subtree),
                unbound_object_count=len(unbound),
                closure=record.closure(tuple(f"entity:{eid}" for eid in element_ids)) if element_ids else (),
            )
        )
    return tuple(out)
