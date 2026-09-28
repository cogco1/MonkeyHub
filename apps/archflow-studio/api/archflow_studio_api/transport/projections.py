"""Projection cache HTTP contract: a key's status, and immutable bytes by digest (ADR-008)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.artifacts import ModelSource
from ..application.projections import DONE, ProjectionStatus
from .artifacts import ModelSourceDto


class ProjectionStatusDto(BaseModel):
    """One projection key and where it stands. Pending or error: show a placeholder, do not cache it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "done", "error"]
    kind: str
    recipe: dict[str, Any]
    renderer: str
    input_sha256: str = Field(alias="inputSha256")
    source: ModelSourceDto | None = Field(default=None, description=(
        "The requester's own model source, checked for this request; absent when the key alone was asked for "
        "and for a projection drawn on demand."))
    blob_sha256: str | None = Field(alias="blobSha256", default=None)
    blob_url: str | None = Field(alias="blobUrl", default=None,
                                 description="Immutable PNG bytes; present only when status is done and the blob is a PNG.")
    attempts: int = Field(ge=0)
    error: str | None = None
    load_ms: int | None = Field(alias="loadMs", default=None)
    render_ms: int | None = Field(alias="renderMs", default=None)


def projection_status_dto(row: ProjectionStatus, source: ModelSource | None) -> ProjectionStatusDto:
    spec = row.spec
    done = row.status == DONE
    return ProjectionStatusDto(
        key=row.key, status=row.status, kind=spec.kind, recipe=dict(spec.recipe), renderer=spec.renderer,
        input_sha256=spec.input_sha256,
        source=None if source is None else ModelSourceDto(
            run_id=source.run_id, state_digest=source.state_digest, asset_sha256=source.asset_sha256),
        blob_sha256=row.blob_sha256 if done else None,
        blob_url=f"/api/projections/blobs/{row.blob_sha256}" if done and row.png else None,
        attempts=row.attempts, error=row.error, load_ms=row.load_ms, render_ms=row.render_ms,
    )
