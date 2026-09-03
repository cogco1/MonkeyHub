"""Durable checkpoint adapter for developed-design coordination."""

from __future__ import annotations

from dataclasses import dataclass

from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archflow.project.refs import require_identifier
from archflow.state.developed_design import (
    DevelopedDesignError,
    DevelopedDesignState,
)


class DevelopmentControllerArchiveError(DevelopedDesignError):
    """A durable development checkpoint is missing or ambiguous."""


@dataclass(frozen=True, slots=True)
class DevelopmentControllerCheckpoint:
    checkpoint_index: int
    predecessor_state_digest: str | None
    state: DevelopedDesignState

    SCHEMA = "DevelopmentControllerCheckpoint@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.checkpoint_index, int)
            or isinstance(self.checkpoint_index, bool)
            or self.checkpoint_index < 0
        ):
            raise DevelopmentControllerArchiveError(
                "checkpoint_index must be non-negative"
            )
        if self.predecessor_state_digest is not None and (
            not isinstance(self.predecessor_state_digest, str)
            or len(self.predecessor_state_digest) != 64
            or any(
                char not in frozenset("0123456789abcdef")
                for char in self.predecessor_state_digest.lower()
            )
        ):
            raise DevelopmentControllerArchiveError(
                "predecessor_state_digest must be SHA-256 or None"
            )
        if self.checkpoint_index == 0:
            if self.predecessor_state_digest is not None:
                raise DevelopmentControllerArchiveError(
                    "initial checkpoint cannot name a predecessor"
                )
        elif self.predecessor_state_digest is None:
            raise DevelopmentControllerArchiveError(
                "later checkpoint requires predecessor state digest"
            )
        if not isinstance(self.state, DevelopedDesignState):
            raise TypeError("state must be DevelopedDesignState")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "checkpoint_index": self.checkpoint_index,
            "predecessor_state_digest": self.predecessor_state_digest,
            "state_digest": self.state.state_digest,
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> DevelopmentControllerCheckpoint:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "checkpoint_index",
            "predecessor_state_digest",
            "state_digest",
            "state",
        }:
            raise DevelopmentControllerArchiveError(
                "development checkpoint schema drifted"
            )
        if value["schema"] != cls.SCHEMA:
            raise DevelopmentControllerArchiveError(
                "development checkpoint schema changed"
            )
        state = DevelopedDesignState.from_dict(value["state"])
        if value["state_digest"] != state.state_digest:
            raise DevelopmentControllerArchiveError(
                "development checkpoint state digest changed"
            )
        return cls(
            checkpoint_index=value["checkpoint_index"],
            predecessor_state_digest=value[
                "predecessor_state_digest"
            ],
            state=state,
        )


@dataclass(frozen=True, slots=True)
class PersistedDevelopmentCheckpoint:
    record_ref: ProjectRecordRef
    checkpoint: DevelopmentControllerCheckpoint


class DevelopmentControllerArchive:
    RECORD_SCHEMA = "DevelopmentControllerCheckpointRecord@1"

    def __init__(
        self,
        repository: FilesystemProjectRepository,
        *,
        run: RunRef,
        coordination_id: str,
    ) -> None:
        if not isinstance(repository, FilesystemProjectRepository):
            raise TypeError(
                "repository must be FilesystemProjectRepository"
            )
        if not isinstance(run, RunRef):
            raise TypeError("run must be RunRef")
        require_identifier(coordination_id, "coordination_id")
        if repository.load_run(run.run_id) != run:
            raise DevelopmentControllerArchiveError(
                "archive run does not match project repository"
            )
        self._repository = repository
        self._run = run
        self._coordination_id = coordination_id
        self._destination = PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        )

    def _records(
        self,
    ) -> tuple[PersistedDevelopmentCheckpoint, ...]:
        records = []
        for ref in self._repository.list_json(
            run=self._run,
            destination=self._destination,
        ):
            payload = self._repository.load_json(ref)
            if (
                payload.get("schema") != self.RECORD_SCHEMA
                or payload.get("coordination_id")
                != self._coordination_id
            ):
                continue
            records.append(self.load(ref))
        return tuple(records)

    def save(
        self,
        checkpoint: DevelopmentControllerCheckpoint,
    ) -> PersistedDevelopmentCheckpoint:
        if not isinstance(
            checkpoint,
            DevelopmentControllerCheckpoint,
        ):
            raise TypeError(
                "checkpoint must be DevelopmentControllerCheckpoint"
            )
        state = checkpoint.state
        if (
            state.project_id != self._run.project_id
            or state.run_id != self._run.run_id
            or state.base != self._run.base
        ):
            raise DevelopmentControllerArchiveError(
                "checkpoint belongs to another exact-base run"
            )
        records = self._records()
        same_index = tuple(
            item
            for item in records
            if item.checkpoint.checkpoint_index
            == checkpoint.checkpoint_index
        )
        if same_index:
            matching = tuple(
                item
                for item in same_index
                if item.checkpoint == checkpoint
            )
            if len(matching) == 1 and len(same_index) == 1:
                return matching[0]
            raise DevelopmentControllerArchiveError(
                "checkpoint index already contains another state"
            )
        if checkpoint.checkpoint_index == 0:
            if records:
                raise DevelopmentControllerArchiveError(
                    "initial checkpoint must be the first record"
                )
        else:
            previous = tuple(
                item
                for item in records
                if item.checkpoint.checkpoint_index
                == checkpoint.checkpoint_index - 1
            )
            if (
                len(previous) != 1
                or previous[0].checkpoint.state.state_digest
                != checkpoint.predecessor_state_digest
            ):
                raise DevelopmentControllerArchiveError(
                    "checkpoint predecessor is missing or changed"
                )
        payload = {
            "schema": self.RECORD_SCHEMA,
            "project_id": state.project_id,
            "run_id": state.run_id,
            "coordination_id": self._coordination_id,
            "base": {
                "project_id": state.base.project_id,
                "version": state.base.version,
                "state_sha256": state.base.require_digest(),
            },
            "checkpoint": checkpoint.to_dict(),
        }
        ref = self._repository.put_json(
            run=self._run,
            destination=self._destination,
            record_kind=(
                f"development-{self._coordination_id}-"
                f"{checkpoint.checkpoint_index:06d}"
            ),
            payload=payload,
        )
        return PersistedDevelopmentCheckpoint(ref, checkpoint)

    def load(
        self,
        ref: ProjectRecordRef,
    ) -> PersistedDevelopmentCheckpoint:
        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        payload = self._repository.load_json(ref)
        if set(payload) != {
            "schema",
            "project_id",
            "run_id",
            "coordination_id",
            "base",
            "checkpoint",
        } or payload.get("schema") != self.RECORD_SCHEMA:
            raise DevelopmentControllerArchiveError(
                "development record schema drifted"
            )
        checkpoint = DevelopmentControllerCheckpoint.from_dict(
            payload["checkpoint"]
        )
        state = checkpoint.state
        if (
            payload["project_id"] != state.project_id
            or payload["run_id"] != state.run_id
            or payload["coordination_id"] != self._coordination_id
            or payload["base"]
            != {
                "project_id": state.base.project_id,
                "version": state.base.version,
                "state_sha256": state.base.require_digest(),
            }
            or state.project_id != self._run.project_id
            or state.run_id != self._run.run_id
            or state.base != self._run.base
        ):
            raise DevelopmentControllerArchiveError(
                "development record identity changed"
            )
        return PersistedDevelopmentCheckpoint(ref, checkpoint)

    def load_latest(self) -> PersistedDevelopmentCheckpoint:
        records = self._records()
        if not records:
            raise DevelopmentControllerArchiveError(
                "no development checkpoint found"
            )
        maximum = max(
            item.checkpoint.checkpoint_index for item in records
        )
        latest = tuple(
            item
            for item in records
            if item.checkpoint.checkpoint_index == maximum
        )
        if len(latest) != 1:
            raise DevelopmentControllerArchiveError(
                "latest development checkpoint is ambiguous"
            )
        return latest[0]
