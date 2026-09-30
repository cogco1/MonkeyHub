"""``GET /api/index`` and ``GET /api/index/{table}``: the project index, read only (ADR-008).

``GET /api/index?since=<revision>&epoch=<epoch>`` is how a client store keeps
up (#366): the changes since a revision it holds, or a whole snapshot when it
holds none the change log can answer from. The server keeps nothing about
any client; the answer depends on the index and the query alone.

Which state, which stage, which artifact: an agent asks the index instead of
calling every view or reading the project's files. One small interface:

- ``table`` is one of run, record, artifact, document, candidate, stage;
- the query names exact values of that table's columns, spelled in camelCase
  (``runId``, ``sha256``, ``kind``, ``assetSha256``, ``branchId``,
  ``candidateId``, ``stageRef``, ...), plus ``limit`` (default 1000);
- the answer carries the index's ``epoch`` and ``revision``.

It answers ``INDEX_UNAVAILABLE`` (503) while the index is not loaded, while it
has not yet applied this process's own last write, or when this process keeps
none; the other views still read the project itself. The
rows are never evidence: cite the P036 record a row names.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Header, Query
from starlette.requests import Request
from starlette.responses import Response

from archflow.project.index import QUERYABLE, IndexUnavailable

from ...binding import bound_project
from ...errors import StudioError
from ..dto.index import IndexChangesDto, IndexEntityDto, IndexRowsDto

router = APIRouter(tags=["index"])

_MAX_LIMIT = 10_000


def _column(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", lambda match: "_" + match.group(1).lower(), name)


def _unavailable(binding, exc: IndexUnavailable | None = None) -> StudioError:
    status = binding.index_status()
    reason = (str(exc) if exc is not None else "this process keeps no project index" if status is None
              else "it has not applied this process's last write yet" if status == "ready"
              else f"project index: {status}")
    return StudioError(503, "INDEX_UNAVAILABLE",
                       f"The project index cannot answer now ({reason}); every other view still reads "
                       "the project itself.")


@router.get("/index", response_model=IndexChangesDto, response_model_by_alias=True,
            responses={304: {"description": "The index still stands at the tag the client sent."}})
def read_index_changes(
    request: Request,
    response: Response,
    since: int | None = Query(default=None, ge=0, description="the revision the client holds"),
    epoch: str | None = Query(default=None, description="the epoch that revision belongs to"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> IndexChangesDto | Response:
    """A snapshot of the index, or the changes since the client's revision (#366).

    Same epoch and the current revision: an empty answer (``from`` equals
    ``to``). Same epoch and a revision the change log still holds: what
    changed since. Any other epoch, a revision ahead of the index or below the
    log's floor, or no ``since``: ``reset`` and every entity. Tagged
    ``"<epoch>:<revision>"``; a snapshot asked for with that tag answers 304.
    """

    binding = bound_project(request.app.state)
    # Waits briefly for this process's own writes to be applied; never projects.
    index = binding.index_reader()
    if index is None:
        raise _unavailable(binding)
    try:
        with index.snapshot() as snapshot:
            token = snapshot.token
            tag = f'"{token.epoch}:{token.revision}"'
            headers = {"ETag": tag, "Cache-Control": "no-cache"}
            if since is None and if_none_match is not None and tag in (value.strip() for value in if_none_match.split(",")):
                return Response(status_code=304, headers=headers)
            changed = snapshot.changes(since) if since is not None and epoch == token.epoch else None
            if changed is None:
                upserts, deletes, reset = snapshot.entities(), [], True
            else:
                (upserts, deletes), reset = changed, False
    except IndexUnavailable as exc:
        raise _unavailable(binding, exc) from exc
    response.headers.update(headers)
    return IndexChangesDto(project_id=binding.project_id, epoch=token.epoch, revision=token.revision, reset=reset,
                           from_revision=None if reset else since, to_revision=token.revision,
                           upserts=[IndexEntityDto(**entity) for entity in upserts], deletes=deletes)


@router.get("/index/{table}", response_model=IndexRowsDto, response_model_by_alias=True)
def read_index(request: Request, table: str, limit: int = Query(default=1000, ge=0, le=_MAX_LIMIT)) -> IndexRowsDto:
    """Rows of one table of the project index, filtered by exact column values."""

    if table not in QUERYABLE:
        raise StudioError(404, "INDEX_TABLE_NOT_FOUND",
                          f"The project index has no table {table!r}; it has {', '.join(QUERYABLE)}.")
    filters = {_column(name): value for name, value in request.query_params.items() if name != "limit"}
    allowed = QUERYABLE[table][0]
    unknown = sorted(set(filters) - set(allowed))
    if unknown:
        raise StudioError(422, "INDEX_FILTER_INVALID",
                          f"{table} rows are filtered by {', '.join(allowed)}, not {', '.join(unknown)}.")
    binding = bound_project(request.app.state)
    # Waits briefly for this process's own writes to be applied; never projects.
    index = binding.index_reader()
    if index is None:
        raise _unavailable(binding)
    try:
        with index.snapshot() as snapshot:
            rows = snapshot.rows(table, filters, limit=limit + 1)
            token = snapshot.token
    except IndexUnavailable as exc:
        raise _unavailable(binding, exc) from exc
    return IndexRowsDto(project_id=binding.project_id, epoch=token.epoch, revision=token.revision,
                        table=table, rows=rows[:limit], truncated=len(rows) > limit)
