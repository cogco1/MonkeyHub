"""Wire shapes; domain validation belongs to archflow.visualization."""
from pydantic import BaseModel, ConfigDict, Field
from .artifacts import ModelSourceDto
from .rendering import RenderCameraDto


class VisualizationSaveDto(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    expected_revision: int = Field(alias='expectedRevision', ge=0)
    state: dict


class VisualizationSourceDto(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    expected_revision: int = Field(alias='expectedRevision', ge=0)
    job_id: str | None = Field(default=None, alias='jobId')
    model_source: ModelSourceDto | None = Field(default=None, alias='modelSource')
    file_name: str | None = Field(default=None, alias='fileName', max_length=240)
    content_base64: str | None = Field(default=None, alias='contentBase64', max_length=44739244)
    camera: RenderCameraDto | None = None


class VisualizationDto(BaseModel):
    revision: int
    state: dict | None = None
    source: dict | None = None
