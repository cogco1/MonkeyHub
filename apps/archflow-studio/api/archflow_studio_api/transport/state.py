"""The wire form of one StateRecord projection.

Two digests travel here and they answer different questions: ``recordDigest``
is the record's content identity, and ``stateDigest`` is that content bound to
a run — the number a runner receipt cites. Naming them apart is the whole
point; a client that confused them would compare a project against itself.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


from ..application.catalog import Catalog
from ..application.frame import ClosureAnswer, RecordFrame
from ..application.projection import StateProjection
from .project import (
    ProjectVersionDto,
    ReferenceRunDto,
    project_version_dto,
    reference_run_dto,
)


class ReferenceReceiptDto(BaseModel):
    """What the reference run's own receipt says it executed."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    run_id: str = Field(alias="runId")
    # Which runner wrote the receipt, read off the record on disk. Named
    # ``receiptSchema`` rather than ``schema`` so it cannot be mistaken for a
    # schema tag on this payload: the API stamps none.
    receipt_schema: str | None = Field(alias="receiptSchema")
    design_state_digest: str | None = Field(alias="designStateDigest")


class CountsDto(BaseModel):
    """How much the record declares, before anyone asks what it means."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    entities: int
    components: int
    parameters: int
    relations: int
    obligations: int
    dependency_edges: int = Field(alias="dependencyEdges")


class ComponentNodeDto(BaseModel):
    """One node of the kernel's component tree."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    parent_component_id: str | None = Field(alias="parentComponentId")
    semantic_kind: str = Field(alias="semanticKind")
    intent: str
    maturity: str
    revision: int


class ElementDto(BaseModel):
    """One element row and the scalars an intent can target."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    element_id: str = Field(alias="elementId")
    component_id: str = Field(alias="componentId")
    producer: str
    # Authored producer params keep the type they were written with; a count
    # that was authored as 4 must not come back as 4.0.
    numeric_fields: dict[str, int | float] = Field(alias="numericFields")


class ParameterDto(BaseModel):
    """One declared parameter, with its lock and where its value comes from."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    key: str
    # Authored quantities keep the type they were written with, like
    # ``numericFields``: a parameter authored as 4 must not come back as 4.0.
    value: int | float
    unit: str
    expr: str | None
    inputs: list[str]
    epistemic_status: str = Field(alias="epistemicStatus")
    lock_authority: str | None = Field(alias="lockAuthority")


class DependencyEdgeDto(BaseModel):
    """One kernel dependency edge, with the refs still prefixed."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    upstream_ref: str = Field(alias="upstreamRef")
    downstream_ref: str = Field(alias="downstreamRef")
    relation: str
    effect: str


class CapabilityDto(BaseModel):
    """One number a change can move: its value and whether, and from where."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    capability_id: str = Field(alias="capabilityId")
    element_id: str = Field(alias="elementId")
    key: str
    value: int | float
    value_type: str = Field(alias="valueType", description="integer or number")
    unit: str | None
    bounds: tuple[float, float] | None
    source: str = Field(description="authored, derived or reindexed")
    confidence: float
    status: str = Field(description="editable, locked, derived or representation")
    validator_refs: list[str] = Field(alias="validatorRefs")


class CatalogElementDto(BaseModel):
    """One realization: the row, its capabilities and the exported objects it names."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    element_id: str = Field(alias="elementId")
    component_id: str = Field(alias="componentId")
    producer: str
    capabilities: list[CapabilityDto]
    object_names: list[str] = Field(alias="objectNames")


class ObjectBindingDto(BaseModel):
    """One exported object and the element the catalog can name for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    name: str
    component_id: str | None = Field(alias="componentId")
    producer_op: str | None = Field(alias="producerOp")
    element_id: str | None = Field(alias="elementId")
    status: str = Field(
        description="bound, MODEL_VISIBLE_CATALOG_MISSING, AMBIGUOUS or UNKNOWN_COMPONENT"
    )
    detail: str


class CatalogComponentDto(BaseModel):
    """One component of the tree with what can be asked of it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    component_id: str = Field(alias="componentId")
    parent_id: str | None = Field(alias="parentId")
    children: list[str]
    element_ids: list[str] = Field(alias="elementIds")
    descendant_element_ids: list[str] = Field(alias="descendantElementIds")
    capability_count: int = Field(alias="capabilityCount")
    states: list[str] = Field(description="editable, locked, derived, missing")
    object_count: int = Field(alias="objectCount")
    unbound_object_count: int = Field(alias="unboundObjectCount")
    closure: list[str]


class CoverageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    objects: int
    bound: int
    unbound: int
    ambiguous: int
    unknown_component: int = Field(alias="unknownComponent")


class CatalogDto(BaseModel):
    """The component catalog: derived from the record and the reference run's inspection."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    components: list[CatalogComponentDto]
    elements: list[CatalogElementDto]
    objects: list[ObjectBindingDto]
    coverage: CoverageDto
    inspection_run: str | None = Field(
        alias="inspectionRun",
        description="the run whose inspection records the objects came from; null when none",
    )
    honesty: list[str]


class StateProjectionDto(BaseModel):
    """The wire form of ``GET /api/state``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    published: ProjectVersionDto = Field(
        description="the issued design this projection was read against",
    )
    reference_run: ReferenceRunDto = Field(alias="referenceRun")
    reference_run_source: str = Field(alias="referenceRunSource")
    reference_receipt: ReferenceReceiptDto | None = Field(
        alias="referenceReceipt"
    )
    matches_reference_receipt: bool | None = Field(
        alias="matchesReferenceReceipt"
    )
    record_source: str = Field(alias="recordSource")
    record_digest: str = Field(
        alias="recordDigest",
        description="content identity: invariant under binding",
    )
    state_digest: str | None = Field(
        alias="stateDigest",
        description="binding identity: the digest runner receipts carry; null "
        "when the kernel refused to build this record's bound view, since "
        "nothing then produced a number a receipt could be compared against",
    )
    active_phase: str | None = Field(
        alias="activePhase",
        description="null for the same reason as stateDigest: the phase is "
        "read off the bound view",
    )
    counts: CountsDto
    component_tree: list[ComponentNodeDto] | None = Field(
        alias="componentTree"
    )
    component_tree_error: str | None = Field(alias="componentTreeError")
    elements: list[ElementDto]
    parameters: list[ParameterDto]
    dependency_edges: list[DependencyEdgeDto] = Field(alias="dependencyEdges")
    honesty: list[str]
    catalog: CatalogDto | None = Field(
        default=None,
        description="the component catalog; null when the record could not be viewed",
    )


def catalog_dto(catalog: Catalog) -> CatalogDto:
    return CatalogDto(
        components=[
            CatalogComponentDto(
                component_id=item.component_id,
                parent_id=item.parent_id,
                children=list(item.children),
                element_ids=list(item.element_ids),
                descendant_element_ids=list(item.descendant_element_ids),
                capability_count=item.capability_count,
                states=list(item.states),
                object_count=item.object_count,
                unbound_object_count=item.unbound_object_count,
                closure=list(item.closure),
            )
            for item in catalog.components
        ],
        elements=[
            CatalogElementDto(
                element_id=item.element_id,
                component_id=item.component_id,
                producer=item.producer,
                capabilities=[
                    CapabilityDto(
                        capability_id=cap.capability_id,
                        element_id=cap.element_id,
                        key=cap.key,
                        value=cap.value,
                        value_type=cap.value_type,
                        unit=cap.unit,
                        bounds=cap.bounds,
                        source=cap.source,
                        confidence=cap.confidence,
                        status=cap.status,
                        validator_refs=list(cap.validator_refs),
                    )
                    for cap in item.capabilities
                ],
                object_names=list(item.object_names),
            )
            for item in catalog.elements
        ],
        objects=[
            ObjectBindingDto(
                name=item.name,
                component_id=item.component_id,
                producer_op=item.producer_op,
                element_id=item.element_id,
                status=item.status,
                detail=item.detail,
            )
            for item in catalog.objects
        ],
        coverage=CoverageDto(
            objects=catalog.coverage.objects,
            bound=catalog.coverage.bound,
            unbound=catalog.coverage.unbound,
            ambiguous=catalog.coverage.ambiguous,
            unknown_component=catalog.coverage.unknown_component,
        ),
        inspection_run=catalog.inspection_run,
        honesty=list(catalog.honesty),
    )


def to_dto(projection: StateProjection, catalog: Catalog | None = None) -> StateProjectionDto:
    """Shape one projection for the wire; every value is already the kernel's."""

    record = projection.record
    return StateProjectionDto(
        project_id=projection.project_id,
        published=project_version_dto(projection.head),
        reference_run=reference_run_dto(projection.reference),
        reference_run_source=projection.reference.source,
        reference_receipt=_receipt_dto(
            projection.reference_receipt,
            found_in=projection.reference.run.run_id,
        ),
        matches_reference_receipt=projection.matches_reference_receipt,
        record_source=projection.record_source,
        record_digest=projection.record_digest,
        state_digest=projection.state_digest,
        active_phase=(
            None
            if projection.state is None
            else projection.state.active_phase.value
        ),
        counts=CountsDto(
            entities=len(record.entities),
            # The kernel's tree is the count when it resolved; the raw entities
            # answer only when there is no tree to count.
            components=(
                len(record.entities_of("Component@1"))
                if projection.components is None
                else len(projection.components)
            ),
            parameters=len(record.parameters),
            relations=len(record.relations),
            obligations=len(record.obligations),
            dependency_edges=len(projection.edges),
        ),
        component_tree=(
            None
            if projection.components is None
            else [
                ComponentNodeDto(
                    component_id=component.component_id,
                    parent_component_id=component.parent_component_id,
                    semantic_kind=component.semantic_kind,
                    intent=component.intent,
                    maturity=component.maturity.value,
                    revision=component.revision,
                )
                for component in projection.components
            ]
        ),
        component_tree_error=projection.component_tree_error,
        elements=[
            ElementDto(
                element_id=element.element_id,
                component_id=element.component_id,
                producer=element.producer,
                numeric_fields=dict(element.numeric_fields),
            )
            for element in projection.elements
        ],
        parameters=[
            ParameterDto(
                key=parameter.key,
                value=parameter.value,
                unit=parameter.unit,
                expr=parameter.expr,
                inputs=list(parameter.inputs),
                epistemic_status=parameter.epistemic_status,
                lock_authority=parameter.lock_authority,
            )
            for parameter in projection.parameters
        ],
        dependency_edges=[
            DependencyEdgeDto(
                upstream_ref=edge.upstream_ref,
                downstream_ref=edge.downstream_ref,
                relation=edge.relation,
                effect=edge.effect.value,
            )
            for edge in projection.edges
        ],
        honesty=list(projection.honesty),
        catalog=None if catalog is None else catalog_dto(catalog),
    )


# ---- the frame: levels and axes, and what changing one would move


class FrameLevelDto(BaseModel):
    """One ``Level@1`` row of the frame."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    level_id: str = Field(alias="levelId")
    role: str
    elevation: float = Field(description="metres, as the record declares it")
    elements_on: list[str] = Field(
        alias="elementsOn",
        description="elements whose own references name this level",
    )
    closure: list[str] = Field(
        description="what changing this level would reach, refs still prefixed",
    )


class FrameAxisDto(BaseModel):
    """One ``GridAxis@1`` row of the frame, read as a plan line where it is one."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    axis_id: str = Field(alias="axisId")
    role: str = Field(description="the name an element's reference uses")
    const: str | None = Field(
        description="x or y when the axis is parallel to a world axis; null "
        "when it is parallel to neither and has no single constant",
    )
    value: float | None = Field(
        description="the constant, in metres; null with const",
    )
    origin: tuple[float, float, float]
    direction: tuple[float, float, float]
    elements_on: list[str] = Field(alias="elementsOn")
    closure: list[str]


class FrameDto(BaseModel):
    """The wire form of ``GET /api/state/frame``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    levels: list[FrameLevelDto]
    axes: list[FrameAxisDto]
    honesty: list[str]


class ClosureRequestDto(BaseModel):
    """Ask what changing these refs would move, against the state that answers."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(alias="stateDigest", min_length=1)
    changed_refs: list[str] = Field(
        alias="changedRefs",
        min_length=1,
        description="entity:<entityId> or parameter:<key>, as GET /api/state "
        "and GET /api/state/frame name them",
    )


class ClosureDto(BaseModel):
    """One closure and the edges that carried it.

    The edges are ``DependencyEdgeDto`` — the same shape ``GET /api/state``
    already puts a kernel ``DependencyEdge`` on the wire in. A second edge
    vocabulary for the same kernel value would give a client two names for one
    thing, so this reuses the one that exists.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    closure: list[str]
    edges: list[DependencyEdgeDto]


def frame_dto(frame: RecordFrame) -> FrameDto:
    return FrameDto(
        levels=[
            FrameLevelDto(
                level_id=level.level_id,
                role=level.role,
                elevation=level.elevation,
                elements_on=list(level.elements_on),
                closure=list(level.closure),
            )
            for level in frame.levels
        ],
        axes=[
            FrameAxisDto(
                axis_id=axis.axis_id,
                role=axis.role,
                const=axis.const,
                value=axis.value,
                origin=axis.origin,
                direction=axis.direction,
                elements_on=list(axis.elements_on),
                closure=list(axis.closure),
            )
            for axis in frame.axes
        ],
        honesty=list(frame.honesty),
    )


def closure_dto(answer: ClosureAnswer) -> ClosureDto:
    return ClosureDto(
        closure=list(answer.closure),
        edges=[
            DependencyEdgeDto(
                upstream_ref=edge.upstream_ref,
                downstream_ref=edge.downstream_ref,
                relation=edge.relation,
                effect=edge.effect.value,
            )
            for edge in answer.edges
        ],
    )


def _receipt_dto(
    receipt: Mapping[str, Any] | None,
    *,
    found_in: str,
) -> ReferenceReceiptDto | None:
    """The receipt's own claims; ``found_in`` names the run it was read from."""

    if receipt is None:
        return None
    claimed_run = receipt.get("run_id")
    return ReferenceReceiptDto(
        run_id=claimed_run if isinstance(claimed_run, str) else found_in,
        receipt_schema=receipt.get("schema"),
        design_state_digest=receipt.get("design_state_digest"),
    )
