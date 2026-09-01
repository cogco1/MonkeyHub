"""Load one retained authoring context for a new web-precedent run."""

from __future__ import annotations

from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
)
from archflow.project.refs import RunRef
from archflow.runtime.production_runtime import ProductionAuthoringContext
from tools.projects.monument_common.context import rebase_authoring_context


class WebPrecedentSupportError(RuntimeError):
    """The source run does not identify one exact authoring context."""


def _context_refs(
    repository: FilesystemProjectRepository,
    run: RunRef,
) -> tuple[ProjectRecordRef, ...]:
    records = repository.list_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
    )
    return tuple(
        item
        for item in records
        if item.relative_path.rsplit("/", maxsplit=1)[-1].startswith(
            "production-authoring-context-"
        )
    )


def load_rebased_authoring_context(
    repository: FilesystemProjectRepository,
    *,
    source_run_id: str,
    target_run: RunRef,
) -> tuple[ProductionAuthoringContext, ProjectRecordRef]:
    """Load exactly one retained context and bind it to ``target_run``."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError("repository must be FilesystemProjectRepository")
    if not isinstance(source_run_id, str) or not source_run_id.strip():
        raise WebPrecedentSupportError("source_run_id must be non-empty text")
    if not isinstance(target_run, RunRef):
        raise TypeError("target_run must be RunRef")
    source_run = repository.load_run(source_run_id)
    refs = _context_refs(repository, source_run)
    if len(refs) != 1:
        raise WebPrecedentSupportError(
            "source run must retain exactly one production authoring context; "
            f"found {len(refs)} in {source_run_id!r}"
        )
    source_ref = refs[0]
    context = ProductionAuthoringContext.from_dict(
        repository.load_json(source_ref)
    )
    return rebase_authoring_context(context, target_run), source_ref


__all__ = [
    "WebPrecedentSupportError",
    "load_rebased_authoring_context",
]
