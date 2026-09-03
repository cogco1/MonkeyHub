"""What the API is willing to claim about itself.

``readOnly`` and ``canonicalWriteAuthority`` are on the wire so the claim is
checkable by anyone: this service reads and orchestrates, and promotion to
canonical state stays a kernel concern.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

HEALTH_SCHEMA = "StudioHealth@3"


class StudioHealth(BaseModel):
    """The health answer, in wire names."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    schema_id: str = Field(default=HEALTH_SCHEMA, alias="schema")
    status: str
    service: str = "archflow-studio-api"
    read_only: bool = Field(default=True, alias="readOnly")
    canonical_write_authority: bool = Field(
        default=False,
        alias="canonicalWriteAuthority",
    )
    project_bound: bool = Field(alias="projectBound")
