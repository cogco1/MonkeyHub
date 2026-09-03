"""The wire form of one StateRecord projection.

Three digests travel here and they are three different numbers:
``authoredRecordDigest`` is the record as written (stable across runs),
``recordDigest`` is that record bound to a run, and ``stateDigest`` is the
developed-design view a runner receipt cites. Naming them apart is the whole
point — a client that confused them would compare a project against itself.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ..application.projection import RUNNER_RECORD_PATH, StateProjection
from .project import HeadDto, ReferenceRunDto, head_dto, reference_run_dto


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
    value: float
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


class StageBindingDto(BaseModel):
    """Which stage the record is bound to; the nulls are the answer."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    workflow_ref: str | None = Field(alias="workflowRef")
    envelope_ref: str | None = Field(alias="envelopeRef")
    stage_id: str | None = Field(alias="stageId")


class StateProjectionDto(BaseModel):
    """The wire form of ``GET /api/state``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    head: HeadDto
    reference_run: ReferenceRunDto = Field(alias="referenceRun")
    reference_run_source: str = Field(alias="referenceRunSource")
    reference_receipt: ReferenceReceiptDto | None = Field(
        alias="referenceReceipt"
    )
    matches_reference_receipt: bool | None = Field(
        alias="matchesReferenceReceipt"
    )
    record_source: str = Field(alias="recordSource")
    authored_record_digest: str = Field(alias="authoredRecordDigest")
    record_digest: str = Field(alias="recordDigest")
    state_digest: str = Field(alias="stateDigest")
    active_phase: str = Field(alias="activePhase")
    counts: CountsDto
    component_tree: list[ComponentNodeDto] | None = Field(
        alias="componentTree"
    )
    component_tree_error: str | None = Field(alias="componentTreeError")
    elements: list[ElementDto]
    parameters: list[ParameterDto]
    dependency_edges: list[DependencyEdgeDto] = Field(alias="dependencyEdges")
    stage_binding: StageBindingDto = Field(alias="stageBinding")
    honesty: list[str]


def to_dto(projection: StateProjection) -> StateProjectionDto:
    """Shape one projection for the wire; every value is already the kernel's."""

    record = projection.record
    return StateProjectionDto(
        project_id=projection.project_id,
        head=head_dto(projection.head),
        reference_run=reference_run_dto(projection.reference),
        reference_run_source=projection.reference.source,
        reference_receipt=_receipt_dto(
            projection.reference_receipt,
            found_in=projection.reference.run.run_id,
        ),
        matches_reference_receipt=projection.matches_reference_receipt,
        record_source=RUNNER_RECORD_PATH,
        authored_record_digest=projection.authored_record_digest,
        record_digest=projection.record_digest,
        state_digest=projection.state_digest,
        active_phase=projection.state.active_phase.value,
        counts=CountsDto(
            entities=len(record.entities),
            components=len(record.entities_of("Component@1")),
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
        stage_binding=StageBindingDto(
            workflow_ref=projection.stage.workflow_ref,
            envelope_ref=projection.stage.envelope_ref,
            stage_id=projection.stage.stage_id,
        ),
        honesty=list(projection.honesty),
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
