"""AI Render HTTP contract; results use the existing Board document identity."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifacts import SourceDocumentDto, ModelSourceDto


class RenderCameraDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid", allow_inf_nan=False)
    projection: Literal["perspective", "orthographic"]
    world_matrix: list[float] = Field(alias="worldMatrix", min_length=16, max_length=16)
    projection_matrix: list[float] = Field(alias="projectionMatrix", min_length=16, max_length=16)
    exposure: float = Field(gt=0)


class RenderViewSourceRequestDto(BaseModel):
    """Freeze the visible camera image against one exact retained model."""
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    model_source: ModelSourceDto = Field(alias="modelSource")
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    camera: RenderCameraDto
    screen_size: tuple[int, int] = Field(alias="screenSize")
    png_base64: str = Field(alias="pngBase64", min_length=1, max_length=44739244)

    @model_validator(mode="after")
    def valid_size(self):
        if any(value < 1 or value > 4096 for value in self.screen_size):
            raise ValueError("screenSize must contain two pixel dimensions between 1 and 4096")
        return self


class RenderPageRefDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    run_id: str = Field(alias="runId", min_length=1)
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[0-9a-f]{64}$")
    page_index: int = Field(alias="pageIndex", ge=0, strict=True)
    revision_ref: str | None = Field(alias="revisionRef", default=None, min_length=1)


class RenderOutputOptionsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    size: str = Field(default="1K", min_length=1, max_length=32)
    aspect_ratio: str = Field(alias="aspectRatio", default="source", min_length=1, max_length=32)


class RenderRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    request_id: UUID = Field(alias="requestId")
    provider_id: str = Field(alias="providerId", min_length=1, max_length=80)
    source: RenderPageRefDto
    references: list[RenderPageRefDto] = Field(default_factory=list, max_length=8)
    direction: str = Field(min_length=1, max_length=16000)
    output: RenderOutputOptionsDto = Field(default_factory=RenderOutputOptionsDto)


class RenderCapabilityDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    provider_id: str = Field(alias="providerId")
    label: str
    model: str | None
    available: bool
    unavailable_reason: str | None = Field(alias="unavailableReason")
    execution: Literal["server-image", "browser-native", "host"]
    sizes: list[str]
    aspect_ratios: list[str] = Field(alias="aspectRatios")
    max_references: int = Field(alias="maxReferences")


class RenderCapabilitiesDto(BaseModel):
    providers: list[RenderCapabilityDto]


class RenderJobDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    job_id: str = Field(alias="jobId")
    request_id: UUID = Field(alias="requestId")
    status: Literal["queued", "running", "succeeded", "failed", "unknown"]
    execution: Literal["server-image", "browser-native", "host"]
    provider_id: str = Field(alias="providerId")
    model: str | None
    request: RenderRequestDto | None
    created_at: str = Field(alias="createdAt")
    finished_at: str | None = Field(alias="finishedAt", default=None)
    error: str | None = None
    error_code: str | None = Field(alias="errorCode", default=None)
    source_state: Literal["current", "outdated", "unavailable"] = Field(alias="sourceState")
    source_state_reason: str | None = Field(alias="sourceStateReason", default=None)
    document: SourceDocumentDto | None = None
    result_available: bool = Field(alias="resultAvailable", default=False)
    provider_request_id: str | None = Field(alias="providerRequestId", default=None)
    input_tokens: int | None = Field(alias="inputTokens", default=None)
    output_tokens: int | None = Field(alias="outputTokens", default=None)
    cost_usd: float | None = Field(alias="costUsd", default=None)
    geometry_revision: str | None = Field(alias="geometryRevision", default=None)
    scene_revision: str | None = Field(alias="sceneRevision", default=None)


class RenderJobListDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)
    project_id: str = Field(alias="projectId")
    jobs: list[RenderJobDto]
