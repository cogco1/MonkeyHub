"""The health answer."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StudioHealth(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    status: str
    service: str = "archflow-studio-api"
    project_bound: bool = Field(alias="projectBound")
    project_id: str | None = Field(default=None, alias="projectId")
    project_dir: str | None = Field(default=None, alias="projectDir")
    server_version: str | None = Field(default=None, alias="serverVersion")
    managed_instance_id: str | None = Field(default=None, alias="managedInstanceId")
    process_id: int | None = Field(default=None, alias="processId")
    parent_process_id: int | None = Field(default=None, alias="parentProcessId")
    source_revision: str | None = Field(default=None, alias="sourceRevision")
