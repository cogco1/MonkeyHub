"""The wire form of ``POST /api/controls`` and ``GET /api/controls/{id}``: a declared control -
the authored-control draft a terminal clarification returned, confirmed and kept, with its
provenance - never a row in the record."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..application.clarification import AuthoredControlDraft
from ..application.controls import DeclaredControl
from .intent import AuthoredControlDraftDto


class DeclareControlRequestDto(BaseModel):
    """The draft the architect confirms, against the state it was drafted for."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(alias="stateDigest", min_length=1)
    utterance: str = Field(min_length=1, description="the request that ended in MISSING_EDITABLE_CONTROL")
    draft: AuthoredControlDraftDto
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)


class DeclaredControlDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    control_id: str = Field(alias="controlId")
    status: str = Field(description="proposed: held in this process, written nowhere")
    state_digest: str = Field(alias="stateDigest")
    component_id: str = Field(alias="componentId")
    requested_property: str | None = Field(alias="requestedProperty")
    utterance: str
    suggested_element_id: str = Field(alias="suggestedElementId")
    producer: str | None
    binding: str | None
    unit: str | None
    provenance: list[str]
    confidence: str
    dependency_requirements: list[str] = Field(alias="dependencyRequirements")
    suggested_action: str = Field(alias="suggestedAction")
    catalog_status: str | None = Field(alias="catalogStatus")
    object_names: list[str] = Field(alias="objectNames")
    created_at: str = Field(alias="createdAt")
    honesty: list[str]
    persistence: str


def draft_from_dto(dto: AuthoredControlDraftDto) -> AuthoredControlDraft:
    return AuthoredControlDraft(
        target_component_id=dto.target_component_id,
        suggested_element_id=dto.suggested_element_id,
        semantic_property=dto.semantic_property,
        producer=dto.producer,
        binding=dto.binding,
        unit=dto.unit,
        provenance=tuple(dto.provenance),
        confidence=dto.confidence,
        dependency_requirements=tuple(dto.dependency_requirements),
        suggested_action=dto.suggested_action,
        catalog_status=dto.catalog_status,
        object_names=tuple(dto.object_names),
    )


def control_dto(control: DeclaredControl) -> DeclaredControlDto:
    payload = control.to_dict()
    return DeclaredControlDto(
        control_id=payload["controlId"],
        status=payload["status"],
        state_digest=payload["stateDigest"],
        component_id=payload["componentId"],
        requested_property=payload["requestedProperty"],
        utterance=payload["utterance"],
        suggested_element_id=payload["suggestedElementId"],
        producer=payload["producer"],
        binding=payload["binding"],
        unit=payload["unit"],
        provenance=payload["provenance"],
        confidence=payload["confidence"],
        dependency_requirements=payload["dependencyRequirements"],
        suggested_action=payload["suggestedAction"],
        catalog_status=payload["catalogStatus"],
        object_names=payload["objectNames"],
        created_at=payload["createdAt"],
        honesty=payload["honesty"],
        persistence=payload["persistence"],
    )
