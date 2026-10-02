"""Persistence ports; P035 intentionally supplies no filesystem writer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ContextManager, Any, BinaryIO, Iterable, Mapping, Protocol

from archflow.project.manifest import ProjectManifest
from archflow.project.refs import (
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)


class PersistenceDestinationRequired(RuntimeError):
    """A producer attempted persistence without an assigned project owner."""


class PersistenceArea(StrEnum):
    INPUT = "input"
    OBJECT = "object"
    EVENT = "event"
    CANONICAL = "canonical"
    RUN_RECORD = "run_record"
    RUN_BRANCH = "run_branch"
    RUN_CANDIDATE = "run_candidate"
    RUN_REVIEW = "run_review"
    RUN_WORKSPACE = "run_workspace"
    RUN_RECOVERY = "run_recovery"
    EXPORT = "export"


@dataclass(frozen=True, slots=True)
class PersistenceDestination:
    area: PersistenceArea
    run_id: str | None = None
    branch_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.area, PersistenceArea):
            raise TypeError("area must be a PersistenceArea")
        run_scoped = self.area.value.startswith("run_")
        if run_scoped and self.run_id is None:
            raise ValueError("run-scoped destination requires run_id")
        if not run_scoped and self.run_id is not None:
            raise ValueError("project-scoped destination cannot carry run_id")
        if self.run_id is not None:
            require_identifier(self.run_id, "run_id")
        if self.branch_id is not None and self.area is not PersistenceArea.RUN_BRANCH:
            raise ValueError("branch_id belongs only to a run-branch destination")
        if self.area is PersistenceArea.RUN_BRANCH and self.branch_id is None:
            raise ValueError("run-branch destination requires branch_id")
        if self.branch_id is not None:
            require_identifier(self.branch_id, "branch_id")


def require_destination(
    destination: PersistenceDestination | None,
    *,
    producer: str,
) -> PersistenceDestination:
    if destination is None:
        raise PersistenceDestinationRequired(
            f"{producer} has no assigned project persistence destination; "
            "stop before writing and ask the project owner"
        )
    if not isinstance(destination, PersistenceDestination):
        raise TypeError("destination must be a PersistenceDestination")
    return destination


class RecordSink(Protocol):
    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef: ...


class WorkspaceSink(Protocol):
    def put_workspace_file(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        artifact_id: str,
        workspace_relative_path: str,
        media_type: str,
        source: BinaryIO,
    ) -> ProjectArtifactRef: ...


class DesignBranchStore(Protocol):
    """Project-scoped positions in retained design history."""

    def read_design_branches(self) -> dict[str, dict[str, Any]]: ...

    def compare_and_swap_design_branch(
        self,
        *,
        branch_id: str,
        expected_head: ProjectRecordRef | None,
        branch: Mapping[str, Any],
    ) -> dict[str, Any]: ...


class WorkingDraftStore(Protocol):
    """P036 owns mutable working positions and expiry of superseded local recovery.

    No run is ever removed here, however old or unreferenced. A run leaves
    ``runs/`` only through the project trash (``RunTrash``), whole and
    restorable, under rules its caller owns.
    """

    def working_draft_guard(self) -> ContextManager[None]: ...

    def read_working_draft(self) -> tuple[dict[str, Any], str | None]: ...

    def compare_and_swap_working_draft(
        self, *, expected_revision: str | None, value: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]: ...

    def protect_working_run(self, run_id: str, source_run_id: str | None, *, dependencies: tuple[str, ...] = ()) -> None: ...

    def release_working_run(self, run_id: str) -> None: ...

    def prune_working_draft(self, *, now: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class TrashEntry:
    """One run in the project trash (#575), as its manifest states it.

    ``rule`` and ``reason`` are the caller's: which retention rule moved it and
    why, in a sentence. ``state_digest``, ``superseded_by``, ``base_run_id``
    and ``label`` are what the caller knew of the run when it moved it, kept
    so the trash can be read without reading the run. ``working_row`` is the
    working-draft row the run took with it and gets back when it is restored.
    """

    run_id: str
    trashed_at: str
    rule: str
    reason: str
    state_digest: str | None = None
    superseded_by: str | None = None
    base_run_id: str | None = None
    label: str | None = None
    working_row: Mapping[str, Any] | None = None


class RunTrash(Protocol):
    """Runs move out of ``runs/`` only here: whole, with a manifest, restorable until purged (#575).

    The store decides nothing about which run may go. It refuses a run that
    something it owns still holds - the Working Head, an execution, a saved
    or chosen working row, the local recovery, a review or Stage kept in the
    run, the published history - and a run it cannot rename whole stays where
    it is. ``run_mentions`` is the read a caller asks before choosing.
    """

    def trash_run(
        self, run_id: str, *, now: str, rule: str, reason: str, state_digest: str | None = None,
        superseded_by: str | None = None, base_run_id: str | None = None, label: str | None = None,
    ) -> TrashEntry: ...

    def trash_entries(self) -> tuple[TrashEntry, ...]: ...

    def restore_trashed_run(self, run_id: str) -> TrashEntry: ...

    def purge_trash(self, *, now: str) -> tuple[str, ...]: ...

    def run_mentions(self, run_ids: Iterable[str]) -> dict[str, frozenset[str]]: ...


class ProjectTransferStore(Protocol):
    """Explicit project snapshot reads and candidate imports; no issue port."""

    def export_transfer(
        self, *, run_id: str | None = None,
        known_files: Mapping[str, str] | None = None,
        include_contents: bool = True,
        include_all_runs: bool = False,
    ) -> dict[str, Any]: ...

    def read_transfer_file(self, path: str, sha256: str) -> bytes: ...

    def import_candidate_transfer(self, transfer: Mapping[str, Any]) -> None: ...

    def pull_transfer(
        self, transfer: Mapping[str, Any], *, expected_head: ProjectVersionRef,
        expected_branches: Mapping[str, Any],
    ) -> None: ...
