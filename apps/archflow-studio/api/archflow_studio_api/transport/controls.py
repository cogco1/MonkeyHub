"""The wire form of ``POST /api/controls`` and ``GET /api/controls/{id}``: a
declared control - an authored control proposed for a component the model
shows and the catalog lacks - with its provenance, never a row in the record.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..application.controls import DeclaredControl


class DeclareControlRequestDto(BaseModel):
    """Which pending intent the declaration answers."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    continuation_token: str = Field(
        alias="continuationToken",
        min_length=1,
        description="the pending intent whose reason was MODEL_VISIBLE_CATALOG_MISSING, "
        "MISSING_ELEMENT_DECLARATION or UNSUPPORTED_ADD_FIELD",
    )
    project_id: str | None = Field(alias="projectId", default=None, min_length=1)


class DeclaredControlDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    control_id: str = Field(alias="controlId")
    status: str = Field(description="proposed: held in this process, written nowhere")
    state_digest: str = Field(alias="stateDigest")
    component_id: str = Field(alias="componentId")
    requested_property: str | None = Field(alias="requestedProperty")
    utterance: str
    reason_code: str = Field(alias="reasonCode")
    object_names: list[str] = Field(alias="objectNames")
    inspection_run: str | None = Field(alias="inspectionRun")
    draft: dict[str, Any] | None = Field(
        description="the Element@1 row shape the re-index would author, with what it reads; null when nothing can be drafted"
    )
    confidence: float
    provenance: list[str]
    created_at: str = Field(alias="createdAt")
    honesty: list[str]
    persistence: str


def control_dto(control: DeclaredControl) -> DeclaredControlDto:
    payload = control.to_dict()
    return DeclaredControlDto(
        control_id=payload["controlId"],
        status=payload["status"],
        state_digest=payload["stateDigest"],
        component_id=payload["componentId"],
        requested_property=payload["requestedProperty"],
        utterance=payload["utterance"],
        reason_code=payload["reasonCode"],
        object_names=payload["objectNames"],
        inspection_run=payload["inspectionRun"],
        draft=payload["draft"],
        confidence=payload["confidence"],
        provenance=payload["provenance"],
        created_at=payload["createdAt"],
        honesty=payload["honesty"],
        persistence=payload["persistence"],
    )
