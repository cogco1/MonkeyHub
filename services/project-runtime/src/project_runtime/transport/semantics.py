"""The wire form of the record's semantic vocabulary.

Two tables and nothing derived from them: what a component may say it *does*
(``role.*``) and what spatial condition it may say it *forms* (``condition.*``),
each with the one line of meaning the table carries and the aliases a person or
a model may write instead.

This resource exists so that no client has to carry a copy. The record refuses
a semantic string the registry does not know (ADR-006), so a dropdown built
from a hard-coded list would offer choices the server would reject — and the
two would drift the first time a term was added.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SemanticTermDto(BaseModel):
    """One registered term: its id, what it means, and what may stand for it."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    id: str = Field(description="the canonical id canonical state carries")
    meaning: str
    aliases: list[str] = Field(
        description="what a person or a model may write instead; an alias "
        "resolves to the id and never enters canonical state itself",
    )


class SemanticsDto(BaseModel):
    """The wire form of ``GET /api/semantics``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    roles: list[SemanticTermDto] = Field(
        description="what a component does for the building",
    )
    conditions: list[SemanticTermDto] = Field(
        description="the spatial condition a component or connection forms",
    )


def semantics_dto(
    terms: tuple[tuple[str, str, str, tuple[str, ...]], ...],
) -> SemanticsDto:
    rows = {"role": [], "condition": []}
    for table, identifier, meaning, aliases in terms:
        rows[table].append(
            SemanticTermDto(id=identifier, meaning=meaning, aliases=list(aliases))
        )
    return SemanticsDto(roles=rows["role"], conditions=rows["condition"])
