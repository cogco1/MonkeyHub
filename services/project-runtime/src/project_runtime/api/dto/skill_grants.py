"""The wire form of ``skill-grants.json``: what a person allowed skills to do (S-1).

The Hub writes this file and a Project Runtime reads it
(``application.skill_access``). An access is kept in the form a person allows
it, each item as the Runtime compares it (``skill_access.asked``), so what the
file says is what a run is checked against.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...application import skill_access

GRANTS_SCHEMA = skill_access.GRANTS_SCHEMA
GRANTS_ENV = skill_access.GRANTS_ENV


class SkillRefDto(BaseModel):
    """A skill as a grant names it: its name and the folder it is found in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source: Literal["bundled", "user", "shared", "scratch"]


class SkillAccessDto(BaseModel):
    """Network and the five lists a person allows (S23: reading folders outside the project is one)."""

    model_config = ConfigDict(extra="forbid")

    network: bool = False
    programs: list[str] = Field(default_factory=list, max_length=64)
    ssh: list[str] = Field(default_factory=list, max_length=64)
    read: list[str] | None = Field(default=None, max_length=64)
    write: list[str] = Field(default_factory=list, max_length=64)
    packages: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def _compared_form(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        form = skill_access.asked(value)
        # A grant that reads nothing outside the project is written as before S23, so a Hub from before reads the file.
        return form | {"read": form["read"] or None}

    def empty(self) -> bool:
        return skill_access.empty(self.model_dump())

    def broad(self) -> dict[str, list[str]]:
        """The items that allow anything, which are never allowed for every skill."""

        return skill_access.broad(self.model_dump())

    def each_time(self) -> dict[str, Any]:
        """The items only a grant for one run allows (S23): the network, ssh, writing outside."""

        return skill_access.each_time(self.model_dump())

    @staticmethod
    def describe(access: dict[str, Any]) -> str:
        return skill_access.describe_added(access)


class SkillGrantDto(BaseModel):
    """One answer a person gave, for every skill, one skill or one run (``once``)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    grant_id: str = Field(alias="grantId", min_length=1, max_length=64)
    access: SkillAccessDto
    skill: SkillRefDto | None = None
    # One run: {"projectId", "codeSha256", "expiresAt"}.
    once: dict[str, str] | None = None
    granted_at: str = Field(alias="grantedAt")
    method: Literal["card", "settings", "migrated"]
    # The card a person answered, or the project grant a migration carried over.
    ref: str | None = None
    migrated_from: dict[str, Any] | None = Field(default=None, alias="migratedFrom")
    revoked_at: str | None = Field(default=None, alias="revokedAt")
    revoked_by: str | None = Field(default=None, alias="revokedBy")


class SkillGrantsDto(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["MonkeyHubSkillGrants@1"] = Field(default=GRANTS_SCHEMA, alias="schema")
    grants: list[SkillGrantDto] = Field(default_factory=list)
