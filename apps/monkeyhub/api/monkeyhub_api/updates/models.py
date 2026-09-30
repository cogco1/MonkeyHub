"""The desktop update DTOs the Hub's update routes answer with and accept."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import HubError


class PreparedUpdate(BaseModel):
    targetVersion: str = Field(pattern=r"^[0-9a-f]{12}-desktop$")
    targetRevision: str = Field(pattern=r"^[0-9a-f]{40}$")
    changedBytes: int = Field(ge=0)
    changedFiles: int = Field(ge=0)
    removedFiles: int = Field(ge=0)
    reusedFiles: int = Field(ge=0)
    releaseVersion: str | None = None


class UpdateCheck(BaseModel):
    """The last automatic or requested check of the unsigned prerelease channel."""
    state: Literal["never", "checking", "up-to-date", "downloading", "ready", "needs-full-update", "error"] = "never"
    checkedAt: datetime | None = None
    latestVersion: str | None = None
    detail: str | None = None
    releaseUrl: str | None = None


class UpdateStatus(BaseModel):
    currentVersion: str
    currentRevision: str | None
    releaseVersion: str | None = None
    mode: Literal["local", "unsupported"]
    state: Literal["idle", "preparing", "ready", "applying", "failed"]
    prepared: PreparedUpdate | None = None
    canApply: bool = False
    message: str | None = None
    error: HubError | None = None
    channel: Literal["unsigned-prerelease"] | None = None
    autoUpdate: bool = False
    nextLaunch: bool = False
    check: UpdateCheck = Field(default_factory=UpdateCheck)


class CompleteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fromCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class RollbackUpdate(CompleteUpdate):
    targetCommit: str = Field(pattern=r"^[0-9a-f]{40}$")


class UpdateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autoUpdate: bool = Field(strict=True)
