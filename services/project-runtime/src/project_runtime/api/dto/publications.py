"""Editable publication pages. Coordinates are points, independent of exports."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .boards import BoardExportPageDto


class PublicationElementDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["text", "image"]
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)
    width: float = Field(gt=0, le=2000)
    height: float = Field(gt=0, le=2000)
    text: str = Field(default="", max_length=6000)
    font_size: float = Field(default=24, alias="fontSize", ge=8, le=96)
    source: BoardExportPageDto | None = None
    frozen: bool = False
    crop: tuple[float, float, float, float] = (0, 0, 0, 0)

    @model_validator(mode="after")
    def valid_content(self):
        if (self.kind == "image") != (self.source is not None):
            raise ValueError("An image needs one exact project page; text has no image source.")
        if any(not 0 <= value < 1 for value in self.crop) or self.crop[0] + self.crop[2] >= 1 or self.crop[1] + self.crop[3] >= 1:
            raise ValueError("Crop margins must leave a visible image.")
        return self


class PublicationPageDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    elements: list[PublicationElementDto] = Field(max_length=60)


class PublicationSpecDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", allow_inf_nan=False)
    width: float = Field(default=960, ge=240, le=2000)
    height: float = Field(default=540, ge=240, le=2000)
    template: Literal["hero"] = "hero"


class PublicationRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    base_revision_sha256: str | None = Field(alias="baseRevisionSha256", pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
    spec: PublicationSpecDto = Field(default_factory=PublicationSpecDto)
    pages: list[PublicationPageDto] = Field(max_length=60)


class PublicationSourceStatusDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    element_id: str = Field(alias="elementId")
    status: Literal["current", "frozen", "stale", "missing"]
    detail: str = ""
    replacement: BoardExportPageDto | None = None


class PublicationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_id: str = Field(alias="projectId")
    revision_sha256: str | None = Field(alias="revisionSha256")
    title: str
    spec: PublicationSpecDto
    pages: list[PublicationPageDto]
    sources: list[PublicationSourceStatusDto]


class PublicationExportRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    project_id: str = Field(alias="projectId")
    revision_sha256: str = Field(alias="revisionSha256", pattern=r"^[0-9a-f]{64}$")
    format: Literal["pdf", "pptx"]


class PublicationBoardRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    project_id: str = Field(alias="projectId")
    base_revision_sha256: str | None = Field(alias="baseRevisionSha256", pattern=r"^[0-9a-f]{64}$")
    board_revision_sha256: str = Field(alias="boardRevisionSha256", pattern=r"^[0-9a-f]{64}$")
    element_ids: list[str] = Field(alias="elementIds", min_length=1, max_length=100)
