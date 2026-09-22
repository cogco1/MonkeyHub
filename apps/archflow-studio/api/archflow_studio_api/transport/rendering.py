"""Native Render requests name retained model content or explicit imported bytes."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .artifacts import ModelSourceDto, SourceDocumentDto


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


class RenderRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    project_id: str = Field(alias="projectId")
    request_id: UUID = Field(alias="requestId")
    model_source: ModelSourceDto | None = Field(default=None, alias="modelSource")
    file_name: str | None = Field(default=None, alias="fileName", max_length=240)
    content_base64: str | None = Field(default=None, alias="contentBase64", max_length=44739244)
    camera: RenderCameraDto | None = None
    resolution: int = Field(default=768, ge=64, le=2048, strict=True)
    samples: int = Field(default=16, ge=1, le=128, strict=True)

    @model_validator(mode="after")
    def one_source(self):
        if (self.model_source is None) == (self.content_base64 is None):
            raise ValueError("Choose an exact retained model or the opened local model bytes.")
        if self.content_base64 is not None and (not self.file_name or not self.file_name.lower().endswith(".3dm")
                or any(c in self.file_name for c in "/\\\r\n\0")):
            raise ValueError("A local model requires a .3dm filename, not a server path.")
        return self


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


class RenderJobListDto(BaseModel):
    jobs: list[RenderJobDto]
