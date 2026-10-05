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


class CandidateTransferDto(ProjectTransferDto):
    retained_row: dict[str, Any] | None = Field(default=None, alias="retainedRow")


class SynchronizationDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    project_id: str = Field(alias="projectId")
    candidate_id: str | None = Field(default=None, alias="candidateId")
    files_transferred: int = Field(alias="filesTransferred")
    bytes_transferred: int = Field(alias="bytesTransferred")


class SyncChunkDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    offset: int = Field(default=0, ge=0)
    length: int = Field(default=1048576, ge=0, le=8388608)


class SyncFilesDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[SyncChunkDto] = Field(max_length=128)


class SyncUploadDto(SyncChunkDto):
    content: str = Field(max_length=11184812)


class SyncUploadBatchDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[SyncUploadDto] = Field(max_length=128)


class SyncLineDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str | None
    row: dict[str, Any] | None
    expected_revision: str | None = Field(default=None, alias="expectedRevision")
