"""Persistence ports; P035 intentionally supplies no filesystem writer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, BinaryIO, Mapping, Protocol

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


