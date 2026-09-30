"""Project transfers preserve the existing P036 values on the wire."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TransferFileDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class ProjectTransferDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    format_version: int
    mode: Literal["snapshot", "candidate"]
    head: dict[str, Any]
    branches: dict[str, Any]
    root_run_id: str | None
    run_ids: list[str]
    files: list[TransferFileDto]
    contents: dict[str, str] = Field(default_factory=dict)


class SynchronizationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_id: str = Field(alias="projectId")
    candidate_id: str | None = Field(default=None, alias="candidateId")
    files_transferred: int = Field(alias="filesTransferred")
    bytes_transferred: int = Field(alias="bytesTransferred")
