"""The wire form of one pick: what the viewer saw in, what the record says out.

The request carries only what the loaded `.3dm` already contains, key for key.
The viewer neither filters nor renames the strings it forwards, so a pick that
cannot be resolved is a fact about the file rather than about the client, and
the answer can say which.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..application.pick import PickRequest, PickResolution

# The digest as it travels: 64 lowercase hex, the form the kernel writes. A
# wrongly shaped one never reaches the resolver — it is a malformed request,
# not a stale base, and the two must not arrive as the same answer.
STATE_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class PickRequestDto(BaseModel):
    """One clicked object, as the viewer read it off the loaded file."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    state_digest: str = Field(
        alias="stateDigest",
        pattern=STATE_DIGEST_PATTERN,
        description="the stateDigest /api/state answered with; a pick against "
        "any other state is refused as STALE_BASE",
    )
    user_strings: dict[str, str] = Field(
        alias="userStrings",
        description="the picked object's user strings, verbatim",
    )
    document_user_strings: dict[str, str] | None = Field(
        alias="documentUserStrings",
        default=None,
        description="the document's own user strings, when the loader exposes "
        "them; they say which file this is, not what was picked",
    )
    object_name: str | None = Field(
        alias="objectName",
        default=None,
        description="the object's name, used only when it carries no "
        "archflow:object_ref",
    )

    def to_request(self) -> PickRequest:
        """The same pick, in the terms the resolver reads."""

        return PickRequest(
            state_digest=self.state_digest,
            user_strings=self.user_strings,
            document_user_strings=self.document_user_strings,
            object_name=self.object_name,
        )


class PickResolutionDto(BaseModel):
    """The wire form of ``POST /api/pick/resolve``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    # The three answers are closed, and closed on the wire: a fourth would fail
    # here rather than reach a client that has no branch for it.
    status: Literal["resolved", "MODEL_VISIBLE_CATALOG_MISSING", "unbound", "unknown_component"] = Field(
        description="resolved, MODEL_VISIBLE_CATALOG_MISSING (the component answers, no "
        "Element@1 row produced the object), unbound, or unknown_component; the last two are "
        "answers about the object, not failures of the request",
    )
    component_id: str | None = Field(alias="componentId")
    element_id: str | None = Field(
        alias="elementId",
        description="the Element@1 row that produced the object; null when the "
        "object is one an operation produced under the component",
    )
    operation_id: str | None = Field(alias="operationId")
    source_state: Literal["current", "stale", "unknown"] = Field(
        alias="sourceState",
        description="current, stale or unknown: whether the file the object "
        "came from was exported from the state answering now; unknown is not "
        "current",
    )
    source_run: str | None = Field(alias="sourceRun")
    source_program_digest: str | None = Field(alias="sourceProgramDigest")
    detail: str | None = Field(
        description="why nothing was resolved, when nothing was",
    )


def to_dto(resolution: PickResolution) -> PickResolutionDto:
    """Shape one resolution for the wire; every id in it is the record's."""

    return PickResolutionDto(
        status=resolution.status,
        component_id=resolution.component_id,
        element_id=resolution.element_id,
        operation_id=resolution.operation_id,
        source_state=resolution.source_state,
        source_run=resolution.source_run,
        source_program_digest=resolution.source_program_digest,
        detail=resolution.detail,
    )
