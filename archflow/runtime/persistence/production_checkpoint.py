"""P036-owned immutable checkpoints for recoverable production runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef, require_identifier


class ProductionCheckpointError(RuntimeError):
    """A P036 production recovery chain is malformed or contradictory."""


class ProductionCheckpointPort(Protocol):
    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef: ...

    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...

    def list_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
    ) -> tuple[ProjectRecordRef, ...]: ...


@dataclass(frozen=True, slots=True)
class ProductionRunCheckpoint:
    project_id: str
    run_id: str
    sequence: int
    intent_digest: str
    transition_digest: str
    record_refs: tuple[ProjectRecordRef, ...]
    predecessor_ref: ProjectRecordRef | None = None

    SCHEMA = "ProductionRunCheckpoint@2"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        _sha(self.intent_digest, "intent_digest")
        _sha(self.transition_digest, "transition_digest")
        if not isinstance(self.record_refs, tuple) or not self.record_refs:
            raise ValueError("record_refs must be a non-empty tuple")
        if any(ref.project_id != self.project_id for ref in self.record_refs):
            raise ValueError("checkpoint records cross project boundary")
        if len(set(self.record_refs)) != len(self.record_refs):
            raise ValueError("checkpoint repeats a record")
        if self.predecessor_ref is not None and (
            self.predecessor_ref.project_id != self.project_id
        ):
            raise ValueError("checkpoint predecessor crosses project boundary")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "intent_digest": self.intent_digest,
            "transition_digest": self.transition_digest,
            "record_refs": [_checkpoint_ref(ref) for ref in self.record_refs],
            "predecessor_ref": (
                None
                if self.predecessor_ref is None
                else _checkpoint_ref(self.predecessor_ref)
            ),
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProductionRunCheckpoint":
        if not isinstance(value, Mapping):
            raise ProductionCheckpointError("checkpoint must be an object")
        expected = {
            "schema",
            "project_id",
            "run_id",
            "sequence",
            "intent_digest",
            "transition_digest",
            "record_refs",
            "predecessor_ref",
            "canonical_write_authority",
        }
        if (
            set(value) != expected
            or value["schema"] != cls.SCHEMA
            or value["canonical_write_authority"] is not False
        ):
            raise ProductionCheckpointError(
                "checkpoint schema or authority drifted"
            )
        refs = value["record_refs"]
        if not isinstance(refs, list):
            raise ProductionCheckpointError("record_refs must be a list")
        predecessor = value["predecessor_ref"]
        return cls(
            project_id=value["project_id"],
            run_id=value["run_id"],
            sequence=value["sequence"],
            intent_digest=value["intent_digest"],
            transition_digest=value["transition_digest"],
            record_refs=tuple(_checkpoint_ref_from(item) for item in refs),
            predecessor_ref=(
                None
                if predecessor is None
                else _checkpoint_ref_from(predecessor)
            ),
        )


def checkpoint_destination(run: RunRef) -> PersistenceDestination:
    return PersistenceDestination(
        PersistenceArea.RUN_RECOVERY,
        run_id=run.run_id,
    )


def load_production_checkpoint(
    repository: ProductionCheckpointPort,
    run: RunRef,
) -> tuple[ProjectRecordRef, ProductionRunCheckpoint] | None:
    chain = load_production_checkpoints(repository, run)
    return None if not chain else chain[-1]


def load_production_checkpoints(
    repository: ProductionCheckpointPort,
    run: RunRef,
) -> tuple[tuple[ProjectRecordRef, ProductionRunCheckpoint], ...]:
    refs = repository.list_json(
        run=run,
        destination=checkpoint_destination(run),
    )
    nodes = [
        (ref, ProductionRunCheckpoint.from_dict(repository.load_json(ref)))
        for ref in refs
    ]
    nodes = [
        (ref, item)
        for ref, item in nodes
        if item.project_id == run.project_id and item.run_id == run.run_id
    ]
    if not nodes:
        return ()
    by_ref = {ref: item for ref, item in nodes}
    children: dict[ProjectRecordRef | None, list[ProjectRecordRef]] = {}
    for ref, item in nodes:
        if (
            item.predecessor_ref is not None
            and item.predecessor_ref not in by_ref
        ):
            raise ProductionCheckpointError("checkpoint predecessor is missing")
        children.setdefault(item.predecessor_ref, []).append(ref)
    roots = children.get(None, [])
    if len(roots) != 1:
        raise ProductionCheckpointError("checkpoint chain has ambiguous roots")
    current = roots[0]
    expected_sequence = 0
    chain: list[tuple[ProjectRecordRef, ProductionRunCheckpoint]] = []
    while True:
        item = by_ref[current]
        if item.sequence != expected_sequence:
            raise ProductionCheckpointError(
                "checkpoint sequence is not contiguous"
            )
        chain.append((current, item))
        next_refs = children.get(current, [])
        if len(next_refs) > 1:
            raise ProductionCheckpointError("checkpoint chain forked")
        if not next_refs:
            return tuple(chain)
        current = next_refs[0]
        expected_sequence += 1


def find_production_checkpoint(
    repository: ProductionCheckpointPort,
    run: RunRef,
    intent_digest: str,
) -> tuple[ProjectRecordRef, ProductionRunCheckpoint] | None:
    """Find a completed intent before a provider or compiler is replayed."""

    _sha(intent_digest, "intent_digest")
    matches = tuple(
        item
        for item in load_production_checkpoints(repository, run)
        if item[1].intent_digest == intent_digest
    )
    if len(matches) > 1:
        raise ProductionCheckpointError("checkpoint chain repeats an intent")
    return None if not matches else matches[0]


def persist_production_checkpoint(
    repository: ProductionCheckpointPort,
    *,
    run: RunRef,
    intent_digest: str,
    transition_digest: str,
    record_refs: tuple[ProjectRecordRef, ...],
) -> tuple[ProjectRecordRef, ProductionRunCheckpoint, bool]:
    _sha(intent_digest, "intent_digest")
    _sha(transition_digest, "transition_digest")
    existing = find_production_checkpoint(repository, run, intent_digest)
    if existing is not None:
        if (
            existing[1].transition_digest != transition_digest
            or existing[1].record_refs != record_refs
        ):
            raise ProductionCheckpointError(
                "intent digest has contradictory transition records"
            )
        return existing[0], existing[1], True
    latest = load_production_checkpoint(repository, run)
    checkpoint = ProductionRunCheckpoint(
        project_id=run.project_id,
        run_id=run.run_id,
        sequence=0 if latest is None else latest[1].sequence + 1,
        intent_digest=intent_digest,
        transition_digest=transition_digest,
        record_refs=record_refs,
        predecessor_ref=None if latest is None else latest[0],
    )
    ref = repository.put_json(
        run=run,
        destination=checkpoint_destination(run),
        record_kind=f"production-checkpoint-{checkpoint.sequence:06d}",
        payload=checkpoint.to_dict(),
    )
    return ref, checkpoint, False


def _sha(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _checkpoint_ref(value: ProjectRecordRef) -> dict[str, str]:
    return {
        "project_id": value.project_id,
        "relative_path": value.relative_path,
        "sha256": value.sha256,
        "media_type": value.media_type,
    }


def _checkpoint_ref_from(value: object) -> ProjectRecordRef:
    expected = {"project_id", "relative_path", "sha256", "media_type"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ProductionCheckpointError("record reference is malformed")
    return ProjectRecordRef(
        value["project_id"],
        value["relative_path"],
        value["sha256"],
        value["media_type"],
    )
