"""P036 persistence for one compiled semantic-and-geometry transition.

The lifecycle compiler remains a pure value producer.  This module assigns its
successful values to immutable run records, then publishes one recovery
checkpoint containing only logical record references.  Orphan records are not
completion evidence until that checkpoint exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.production.responsibility import InvocationEnvelope
from archflow.project.digests import canonical_json_sha256
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.production_checkpoint import (
    ProductionCheckpointPort,
    ProductionRunCheckpoint,
    find_production_checkpoint,
    persist_production_checkpoint,
)
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.runtime.semantic_geometry_lifecycle import (
    InitialSemanticGeometryResult,
    SemanticGeometryLifecycleResult,
    SemanticGeometryLifecycleStatus,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import digest_value
from archflow.state.spatial import SpatialOptionProposal


class ProductionTransitionError(RuntimeError):
    """A compiled transition cannot be assigned to this P036 run."""


class ProductionRecordRole(StrEnum):
    DESIGN_STATE = "design-state"
    COMPONENT_PROPOSAL = "component-proposal"
    GEOMETRY_PROGRAM = "geometry-program"
    LIFECYCLE_RECEIPT = "lifecycle-receipt"
    PROVIDER_INVOCATION = "provider-invocation"


_REQUIRED_ROLES = (
    ProductionRecordRole.DESIGN_STATE,
    ProductionRecordRole.COMPONENT_PROPOSAL,
    ProductionRecordRole.GEOMETRY_PROGRAM,
    ProductionRecordRole.LIFECYCLE_RECEIPT,
)


class ProductionTransitionPort(ProductionCheckpointPort, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class ArchivedProductionRecord:
    role: ProductionRecordRole
    ref: ProjectRecordRef
    semantic_digest: str
    content: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.role, ProductionRecordRole):
            raise TypeError("role must be ProductionRecordRole")
        if not isinstance(self.ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        _sha(self.semantic_digest, "semantic_digest")
        if not isinstance(self.content, Mapping):
            raise TypeError("content must be a mapping")


@dataclass(frozen=True, slots=True)
class ProductionTransitionArchive:
    run: RunRef
    intent_digest: str
    transition_digest: str
    checkpoint_ref: ProjectRecordRef
    checkpoint: ProductionRunCheckpoint
    records: tuple[ArchivedProductionRecord, ...]
    resumed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.run, RunRef):
            raise TypeError("run must be RunRef")
        _sha(self.intent_digest, "intent_digest")
        _sha(self.transition_digest, "transition_digest")
        if not isinstance(self.checkpoint_ref, ProjectRecordRef):
            raise TypeError("checkpoint_ref must be ProjectRecordRef")
        if not isinstance(self.checkpoint, ProductionRunCheckpoint):
            raise TypeError("checkpoint must be ProductionRunCheckpoint")
        if self.checkpoint.intent_digest != self.intent_digest:
            raise ProductionTransitionError("archive intent and checkpoint disagree")
        if self.checkpoint.transition_digest != self.transition_digest:
            raise ProductionTransitionError(
                "archive transition and checkpoint disagree"
            )
        if tuple(item.ref for item in self.records) != self.checkpoint.record_refs:
            raise ProductionTransitionError(
                "archive records and checkpoint references disagree"
            )
        if not isinstance(self.resumed, bool):
            raise TypeError("resumed must be bool")


def production_intent_digest(
    run: RunRef,
    *,
    intent: Mapping[str, Any],
) -> str:
    """Create the pre-provider idempotency key for one exact-base step."""

    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    if not isinstance(intent, Mapping):
        raise TypeError("intent must be a mapping")
    return canonical_json_sha256(
        {
            "schema": "ProductionTransitionIntent@1",
            "project_id": run.project_id,
            "run_id": run.run_id,
            "base": _base(run.base),
            "intent": dict(intent),
        }
    )


def load_production_transition(
    repository: ProductionTransitionPort,
    *,
    run: RunRef,
    intent_digest: str,
) -> ProductionTransitionArchive | None:
    """Return a durable completion before model or compiler replay."""

    completed = find_production_checkpoint(repository, run, intent_digest)
    if completed is None:
        return None
    checkpoint_ref, checkpoint = completed
    records = _load_records(repository, run, checkpoint)
    return ProductionTransitionArchive(
        run=run,
        intent_digest=intent_digest,
        transition_digest=checkpoint.transition_digest,
        checkpoint_ref=checkpoint_ref,
        checkpoint=checkpoint,
        records=records,
        resumed=True,
    )


def persist_compiled_production_transition(
    repository: ProductionTransitionPort,
    *,
    run: RunRef,
    intent_digest: str,
    current_design_state: DevelopedDesignState,
    result: SemanticGeometryLifecycleResult | InitialSemanticGeometryResult,
    invocation_envelopes: tuple[InvocationEnvelope, ...] = (),
) -> ProductionTransitionArchive:
    """Persist all successful successor values, then checkpoint their refs."""

    _sha(intent_digest, "intent_digest")
    _validate_success(run, current_design_state, result)
    if not isinstance(invocation_envelopes, tuple) or any(
        not isinstance(item, InvocationEnvelope) for item in invocation_envelopes
    ):
        raise TypeError("invocation_envelopes contains an invalid item")
    completed = load_production_transition(
        repository,
        run=run,
        intent_digest=intent_digest,
    )
    if completed is not None:
        if completed.transition_digest != result.receipt.receipt_digest:
            raise ProductionTransitionError(
                "completed intent has a contradictory lifecycle transition"
            )
        return completed

    assert result.component_proposal is not None
    assert result.geometry_program is not None
    contents: list[tuple[ProductionRecordRole, Mapping[str, Any], str]] = [
        (
            ProductionRecordRole.DESIGN_STATE,
            current_design_state.to_dict(),
            current_design_state.state_digest,
        ),
        (
            ProductionRecordRole.COMPONENT_PROPOSAL,
            result.component_proposal.to_dict(),
            result.component_proposal.proposal_digest,
        ),
        (
            ProductionRecordRole.GEOMETRY_PROGRAM,
            result.geometry_program.to_dict(),
            result.geometry_program.program_digest,
        ),
        (
            ProductionRecordRole.LIFECYCLE_RECEIPT,
            result.receipt.to_dict(),
            result.receipt.receipt_digest,
        ),
    ]
    contents.extend(
        (
            ProductionRecordRole.PROVIDER_INVOCATION,
            envelope.to_dict(),
            digest_value(envelope.to_dict()),
        )
        for envelope in invocation_envelopes
    )
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    refs: list[ProjectRecordRef] = []
    role_counts: dict[ProductionRecordRole, int] = {}
    for role, content, semantic_digest in contents:
        index = role_counts.get(role, 0)
        role_counts[role] = index + 1
        payload = _record_payload(
            run,
            role=role,
            semantic_digest=semantic_digest,
            content=content,
        )
        refs.append(
            repository.put_json(
                run=run,
                destination=destination,
                record_kind=f"production-{role.value}-{index:03d}",
                payload=payload,
            )
        )

    checkpoint_ref, checkpoint, resumed = persist_production_checkpoint(
        repository,
        run=run,
        intent_digest=intent_digest,
        transition_digest=result.receipt.receipt_digest,
        record_refs=tuple(refs),
    )
    records = _load_records(repository, run, checkpoint)
    return ProductionTransitionArchive(
        run=run,
        intent_digest=intent_digest,
        transition_digest=result.receipt.receipt_digest,
        checkpoint_ref=checkpoint_ref,
        checkpoint=checkpoint,
        records=records,
        resumed=resumed,
    )


def _validate_success(
    run: RunRef,
    current_design_state: DevelopedDesignState,
    result: SemanticGeometryLifecycleResult | InitialSemanticGeometryResult,
) -> None:
    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    if not isinstance(current_design_state, DevelopedDesignState):
        raise TypeError("current_design_state must be DevelopedDesignState")
    if not isinstance(
        result,
        (SemanticGeometryLifecycleResult, InitialSemanticGeometryResult),
    ):
        raise TypeError("result must be a semantic-geometry result")
    if isinstance(result, SemanticGeometryLifecycleResult):
        if result.receipt.status is not SemanticGeometryLifecycleStatus.COMPILED:
            raise ProductionTransitionError("rejected lifecycle cannot be persisted")
        if result.component_proposal is None or result.geometry_program is None:
            raise ProductionTransitionError(
                "compiled lifecycle successors are missing"
            )
    if (
        current_design_state.project_id != run.project_id
        or current_design_state.run_id != run.run_id
        or current_design_state.base != run.base
    ):
        raise ProductionTransitionError("design state does not bind the exact run base")
    proposal = result.geometry_program.proposal
    if (
        proposal.project_id != run.project_id
        or proposal.run_id != run.run_id
        or proposal.base != run.base
    ):
        raise ProductionTransitionError(
            "geometry program does not bind the exact run base"
        )
    if proposal.design_state_digest != current_design_state.state_digest:
        raise ProductionTransitionError(
            "geometry program does not bind the current design state"
        )
    if isinstance(result, SemanticGeometryLifecycleResult):
        if (
            result.receipt.current_design_state_digest
            != current_design_state.state_digest
        ):
            raise ProductionTransitionError(
                "lifecycle receipt does not bind the current design state"
            )
    elif result.receipt.design_state_digest != current_design_state.state_digest:
        raise ProductionTransitionError(
            "initial receipt does not bind the current design state"
        )


def _record_payload(
    run: RunRef,
    *,
    role: ProductionRecordRole,
    semantic_digest: str,
    content: Mapping[str, Any],
) -> dict[str, object]:
    _sha(semantic_digest, "semantic_digest")
    material = dict(content)
    return {
        "schema": "ProductionTransitionRecord@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": _base(run.base),
        "role": role.value,
        "semantic_digest": semantic_digest,
        "content_sha256": canonical_json_sha256(material),
        "content": material,
        "canonical_write_authority": False,
    }


def _load_records(
    repository: ProductionTransitionPort,
    run: RunRef,
    checkpoint: ProductionRunCheckpoint,
) -> tuple[ArchivedProductionRecord, ...]:
    records = tuple(
        _load_record(repository, run, ref) for ref in checkpoint.record_refs
    )
    for role in _REQUIRED_ROLES:
        if sum(item.role is role for item in records) != 1:
            raise ProductionTransitionError(
                f"checkpoint requires exactly one {role.value} record"
            )
    lifecycle = next(
        item for item in records if item.role is ProductionRecordRole.LIFECYCLE_RECEIPT
    )
    if lifecycle.semantic_digest != checkpoint.transition_digest:
        raise ProductionTransitionError(
            "checkpoint transition does not name its lifecycle receipt"
        )
    return records


def _load_record(
    repository: ProductionTransitionPort,
    run: RunRef,
    ref: ProjectRecordRef,
) -> ArchivedProductionRecord:
    payload = repository.load_json(ref)
    expected = {
        "schema",
        "project_id",
        "run_id",
        "base",
        "role",
        "semantic_digest",
        "content_sha256",
        "content",
        "canonical_write_authority",
    }
    if set(payload) != expected or payload.get("schema") != "ProductionTransitionRecord@1":
        raise ProductionTransitionError("production record schema drifted")
    if (
        payload.get("project_id") != run.project_id
        or payload.get("run_id") != run.run_id
        or payload.get("base") != _base(run.base)
        or payload.get("canonical_write_authority") is not False
    ):
        raise ProductionTransitionError("production record identity or authority drifted")
    try:
        role = ProductionRecordRole(payload["role"])
    except (TypeError, ValueError) as exc:
        raise ProductionTransitionError("production record role is invalid") from exc
    content = payload["content"]
    if not isinstance(content, Mapping):
        raise ProductionTransitionError("production record content is not an object")
    semantic_digest = payload["semantic_digest"]
    _sha(semantic_digest, "semantic_digest")
    if canonical_json_sha256(content) != payload["content_sha256"]:
        raise ProductionTransitionError("production record content digest drifted")
    if digest_value(content) != semantic_digest:
        raise ProductionTransitionError("production record semantic digest drifted")
    _validate_content(role, content, run)
    return ArchivedProductionRecord(role, ref, semantic_digest, dict(content))


def _validate_content(
    role: ProductionRecordRole,
    content: Mapping[str, Any],
    run: RunRef,
) -> None:
    if role is ProductionRecordRole.DESIGN_STATE:
        value = DevelopedDesignState.from_dict(content)
        if (
            value.project_id != run.project_id
            or value.run_id != run.run_id
            or value.base != run.base
        ):
            raise ProductionTransitionError("archived design state is stale")
    elif role is ProductionRecordRole.COMPONENT_PROPOSAL:
        SpatialOptionProposal.from_dict(content)
    elif role is ProductionRecordRole.GEOMETRY_PROGRAM:
        proposal = content.get("proposal")
        if not isinstance(proposal, Mapping) or (
            proposal.get("project_id") != run.project_id
            or proposal.get("run_id") != run.run_id
            or proposal.get("base") != _base(run.base)
        ):
            raise ProductionTransitionError("archived geometry program is stale")
    elif role is ProductionRecordRole.LIFECYCLE_RECEIPT:
        if content.get("schema") not in {
            "SemanticGeometryLifecycleReceipt@1",
            "InitialSemanticGeometryReceipt@1",
        }:
            raise ProductionTransitionError("archived lifecycle receipt is malformed")
    elif role is ProductionRecordRole.PROVIDER_INVOCATION:
        if content.get("schema") != "ProductionInvocationEnvelope@2":
            raise ProductionTransitionError("archived provider envelope is malformed")


def _base(value: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": value.project_id,
        "version": value.version,
        "state_sha256": value.require_digest(),
    }


def _sha(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
