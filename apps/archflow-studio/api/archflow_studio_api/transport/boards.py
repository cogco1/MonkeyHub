"""The persisted board scene, excluding viewport state and image bytes."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..application.boards import BoardScene


class BoardRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid")

    project_id: str = Field(alias="projectId", min_length=1)
    base_revision_sha256: str | None = Field(alias="baseRevisionSha256", pattern=r"^[0-9a-f]{64}$")
    title: str = Field(default="MonkeyBoard", min_length=1, max_length=200)
    elements: list[dict[str, Any]]
    seen_documents: list[str] = Field(alias="seenDocuments")


class BoardDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    title: str
    elements: list[dict[str, Any]]
    seen_documents: list[str] = Field(alias="seenDocuments")
    revision_sha256: str | None = Field(alias="revisionSha256")


def board_dto(scene: BoardScene) -> BoardDto:
    return BoardDto(project_id=scene.project_id, title=scene.title,
                    elements=[dict(row) for row in scene.elements], seen_documents=list(scene.seen_documents),
                    revision_sha256=scene.revision_sha256)
