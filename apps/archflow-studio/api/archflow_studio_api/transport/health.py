"""The health answer."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StudioHealth(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    status: str
    service: str = "archflow-studio-api"
    project_bound: bool = Field(alias="projectBound")
