"""The four whole-model elevations supported by the existing drawing owner."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import ModelSourceDto


class ElevationRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")
    project_id: str = Field(alias="projectId", min_length=1)
    source_stage_ref: str | None = Field(alias="sourceStageRef", default=None)
    model_source: ModelSourceDto | None = Field(alias="modelSource", default=None)
    view: Literal["front", "back", "left", "right"] = "front"
    drawing_id: str | None = Field(alias="drawingId", default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    hidden_lines: bool = Field(alias="hiddenLines", default=False)
    scale_denominator: int = Field(alias="scaleDenominator", default=100, ge=1, le=10000)
