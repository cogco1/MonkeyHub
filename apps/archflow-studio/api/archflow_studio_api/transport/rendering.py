"""Native Render requests name retained model content or explicit imported bytes."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from .artifacts import SourceDocumentDto


class RenderCameraDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True, allow_inf_nan=False)
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    projection: Literal["perspective", "orthographic"]
    near: float = Field(gt=0)
    far: float = Field(gt=0)
    aspect: float = Field(ge=.01, le=100)
    vertical_fov: float | None = Field(default=None, alias="verticalFov")
    orthographic_bounds: tuple[float, float, float, float] | None = Field(default=None, alias="orthographicBounds")


class RenderJobDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    job_id: str = Field(alias="jobId")
    project_id: str = Field(alias="projectId")
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    source_sha256: str = Field(alias="sourceSha256")
    file_name: str = Field(alias="fileName")
    created_at: str = Field(alias="createdAt")
    error: str | None = None
    document: SourceDocumentDto | None = None
    snapshot: dict | None = None
    snapshot_sha256: str | None = Field(default=None, alias='snapshotSha256')


class RenderJobListDto(BaseModel):
    jobs: list[RenderJobDto]


class NativeRenderRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')
    request_id: UUID = Field(alias='requestId')
    revision: int = Field(ge=1)


class NativeRenderCompleteDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')
    snapshot_sha256: str = Field(alias='snapshotSha256', pattern='^[a-f0-9]{64}$')
    content_base64: str = Field(alias='contentBase64', max_length=100663296)


class NativeRenderFailureDto(BaseModel):
    model_config = ConfigDict(extra='forbid')
    detail: str = Field(max_length=1000)
