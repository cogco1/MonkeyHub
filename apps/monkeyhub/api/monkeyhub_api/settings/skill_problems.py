"""The installed skills that cannot be used right now, and why, as the settings page says it (S-1).

A skill whose folder cannot be read, whose script does not compile, or whose instructions name a file the
folder does not carry is not given to any chat and runs nothing (``skill_library.installed``). The person
never sees a code for that: the page names the skill, says what is wrong in a line, and says who can fix it.
Reading is from the skill folders alone, so no project is involved and nothing is cached: a fixed folder is
well again at the next read.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from project_runtime.application.skill_library import installed

PROBLEM_KINDS = ("unreadable", "name_differs", "entry_syntax", "missing_file")


class SkillProblemDto(BaseModel):
    """One installed skill that cannot be used: which, where it is, what is wrong (``kind``), and the technical line."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source: Literal["bundled", "user"]
    folder: str
    kind: Literal["unreadable", "name_differs", "entry_syntax", "missing_file"]
    # The file the problem is in or names, and the line of a script's mistake.
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    detail: str


def problems() -> list[SkillProblemDto]:
    """The skills a chat would be given if they were well and are not, one per name (the one a run would use)."""

    rows = []
    for row in installed().current():
        if row.content is not None or row.error is None:
            continue
        kind = row.error.get("kind")
        rows.append(SkillProblemDto(
            name=row.name, source=row.source, folder=str(row.folder),
            kind=kind if kind in PROBLEM_KINDS else "unreadable", file=row.error.get("file"),
            line=row.error.get("line"), detail=str(row.error.get("detail") or "")))
    return rows
