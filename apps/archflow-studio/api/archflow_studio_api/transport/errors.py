"""One wire shape for every failure the Studio API can return.

A client that has to branch on several error shapes eventually stops checking.
``StudioError@1`` is the only shape, so a refusal is always as readable as a
success, and ``code`` stays stable while ``detail`` stays human.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

ERROR_SCHEMA = "StudioError@1"


class StudioError(Exception):
    """A failure the API can state on the wire with a stable code."""

    status: int
    code: str
    detail: str

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


class NotBound(StudioError):
    """No project is bound, so the request has nothing to answer about."""

    def __init__(self, detail: str) -> None:
        super().__init__(503, "PROJECT_NOT_BOUND", detail)


class NotFound(StudioError):
    """A named thing does not exist; the caller supplies the specific code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(404, code, detail)


class StaleBase(StudioError):
    """The request was written against a base the project has moved past."""

    def __init__(self, detail: str) -> None:
        super().__init__(409, "STALE_BASE", detail)


class ProjectMismatch(StudioError):
    """The request names a project other than the bound one."""

    def __init__(self, detail: str) -> None:
        super().__init__(403, "PROJECT_MISMATCH", detail)


class DigestMismatch(StudioError):
    """The artifact on disk is not the artifact the request expected."""

    def __init__(self, detail: str) -> None:
        super().__init__(409, "ARTIFACT_DIGEST_MISMATCH", detail)


class BlockedNeedsHuman(StudioError):
    """The work cannot proceed until a human answers a specific question."""

    def __init__(
        self,
        detail: str,
        question: str,
        accepted_forms: tuple[str, ...] = (),
    ) -> None:
        super().__init__(422, "BLOCKED_NEEDS_HUMAN", detail)
        self.question = question
        self.accepted_forms = accepted_forms


class StudioErrorDto(BaseModel):
    """The wire form of a refusal, declared so it appears in the schema."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    schema_id: str = Field(default=ERROR_SCHEMA, alias="schema")
    code: str
    detail: str
    question: str | None = None
    accepted_forms: list[str] | None = Field(
        default=None,
        alias="acceptedForms",
    )
