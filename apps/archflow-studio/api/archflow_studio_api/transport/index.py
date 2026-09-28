"""The wire form of ``GET /api/index`` and ``GET /api/index/{table}``: the project index, read only."""

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


class IndexEntityDto(BaseModel):
    """One entity a client keeps of the index: a run, the tree, the working position, or another area.

    ``id`` is ``run:<runId>``, ``tree``, ``working`` or ``area:<name>``;
    ``domain`` is the part before the colon; ``rev`` the revision that last
    changed it. ``working`` is the working position a head is read from
    (``current``, ``runs``, ``active``) without the local recovery it may
    name: saving that recovery moves ``area:working`` alone. Like a
    row, an entity is never evidence: its body names the records it was read from.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    domain: str = Field(description="run | tree | working | area")
    rev: int
    body: dict[str, Any]


class IndexChangesDto(BaseModel):
    """``GET /api/index``: a whole snapshot of the index, or the changes since a revision (#366).

    With ``since`` and ``epoch`` naming this index's epoch and a revision the
    change log still holds, the answer holds only what changed after it:
    ``from`` is that revision, ``to`` the current one, ``upserts`` every entity
    changed since (as it is now) and ``deletes`` the ids removed since. Equal
    ``from`` and ``to`` means nothing changed. Otherwise - no ``since``,
    another epoch (a rebuild), a revision ahead of the index or older than the
    log - ``reset`` is true and ``upserts`` is every entity: replace what you
    kept. Tagged ``"<epoch>:<revision>"``.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    project_id: str = Field(alias="projectId")
    epoch: str
    revision: int
    reset: bool
    from_revision: int | None = Field(alias="from", default=None)
    to_revision: int = Field(alias="to")
    upserts: list[IndexEntityDto]
    deletes: list[str]


class IndexEventDto(BaseModel):
    """``index.committed`` and ``stream.reset`` on ``GET /api/events`` (#366).

    ``index.committed`` is a hint that carries no rows: the index now stands
    at ``epoch``/``revision`` and moved the named ``domains`` (``reset`` after
    a rebuild or a first load); read ``GET /api/index?since=`` to catch up.
    Nothing may depend on receiving it. ``stream.reset`` opens a reconnection
    that cannot resume (``reason`` restart or overflow): whatever the client
    kept from this stream may be missing events, so it reads again.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    seq: int
    at: str
    type: str
    epoch: str | None = None
    revision: int | None = None
    domains: list[str] | None = None
    reason: str | None = None
