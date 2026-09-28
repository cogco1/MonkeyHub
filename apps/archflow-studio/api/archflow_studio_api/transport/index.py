"""The wire form of ``GET /api/index/{table}``: project index rows, read only."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IndexRowsDto(BaseModel):
    """Rows of one table of the project index (ADR-008 phase 1b).

    The rows are derived from the project's P036 records and can be rebuilt
    from them at any time. A row or a key is never evidence: each row names
    the record it was read from (``uri``, ``receiptRef``, ``stageRef`` and the
    like), and that record is.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    epoch: str = Field(description="changes whenever the index is rebuilt")
    revision: int = Field(description="moves once for every change the index committed")
    table: str
    rows: list[dict[str, Any]] = Field(
        description="each row's columns, its body as the projector stated it, and rev: "
        "the revision that last changed it",
    )
    truncated: bool = Field(description="more rows matched than the limit returned")
