"""``GET /api/index/{table}``: the project index, read only, for agents (ADR-008 phase 1b).

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

from fastapi import APIRouter, Query
from starlette.requests import Request

from archflow.project.index import QUERYABLE, IndexUnavailable

from ..application.binding import bound_project
from ..transport.errors import StudioError
from ..transport.index import IndexRowsDto

router = APIRouter(tags=["index"])

_MAX_LIMIT = 10_000


def _column(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", lambda match: "_" + match.group(1).lower(), name)


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
    try:
        if index is None:
            status = binding.index_status()
            raise IndexUnavailable("this process keeps no project index" if status is None
                                   else "it has not applied this process's last write yet" if status == "ready"
                                   else f"project index: {status}")
        with index.snapshot() as snapshot:
            rows = snapshot.rows(table, filters, limit=limit + 1)
            token = snapshot.token
    except IndexUnavailable as exc:
        raise StudioError(503, "INDEX_UNAVAILABLE",
                          f"The project index cannot answer now ({exc}); every other view still reads "
                          "the project itself.") from exc
    return IndexRowsDto(project_id=binding.project_id, epoch=token.epoch, revision=token.revision,
                        table=table, rows=rows[:limit], truncated=len(rows) > limit)
