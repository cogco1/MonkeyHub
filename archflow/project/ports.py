"""Persistence ports; P035 intentionally supplies no filesystem writer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ContextManager, Any, BinaryIO, Mapping, Protocol

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
    """P036 owns mutable working positions and cleanup of explicitly automatic runs."""

    def working_draft_guard(self) -> ContextManager[None]: ...

    def read_working_draft(self) -> tuple[dict[str, Any], str | None]: ...

    def compare_and_swap_working_draft(
        self, *, expected_revision: str | None, value: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]: ...

    def protect_working_run(self, run_id: str, source_run_id: str | None, *, dependencies: tuple[str, ...] = ()) -> None: ...

    def release_working_run(self, run_id: str) -> None: ...

    def prune_working_draft(self, *, now: str, protected_run_ids: tuple[str, ...] = ()) -> tuple[str, ...]: ...


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
