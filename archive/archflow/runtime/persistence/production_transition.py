"""P036 persistence for one compiled semantic-and-geometry transition.

The lifecycle compiler remains a pure value producer.  This module assigns its
successful values to immutable run records, then publishes one recovery
checkpoint containing only logical record references.  Orphan records are not
completion evidence until that checkpoint exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.ports.model import ModelInvocationReceipt
from archflow.production.responsibility import InvocationEnvelope
from archflow.contracts.canonical import canonical_digest
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archive.archflow.runtime.persistence.production_checkpoint import (
    ProductionCheckpointPort,
    ProductionRunCheckpoint,
    find_production_checkpoint,
    persist_production_checkpoint,
)
from archflow.project.refs import (
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    InitialSemanticGeometryResult,
    SemanticGeometryLifecycleResult,
    SemanticGeometryLifecycleStatus,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.contracts.canonical import canonical_digest
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


@dataclass(frozen=True, slots=True)
class ProductionFailedAttemptReceipt:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    step_id: str
    intent_digest: str
    attempt_index: int
    attempt_id: str
    error_code: str
    message: str
    invocation_envelopes: tuple[Mapping[str, Any], ...]
    retry_of_ref: str | None = None

    SCHEMA = "ProductionFailedAttemptReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.step_id, "step_id")
        require_identifier(self.attempt_id, "attempt_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ProductionTransitionError("failed attempt base crosses project")
        _sha(self.intent_digest, "intent_digest")
        if type(self.attempt_index) is not int or self.attempt_index < 0:
            raise ValueError("attempt_index must be non-negative")
        for value, field, maximum in (
            (self.error_code, "error_code", 500),
            (self.message, "message", 2_000),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must be bounded non-empty text")
        if not isinstance(self.invocation_envelopes, tuple) or not self.invocation_envelopes:
            raise ValueError("failed attempt requires P053 invocation evidence")
        statuses = tuple(
            _validate_failed_envelope(envelope)
            for envelope in self.invocation_envelopes
        )
        if self.retry_of_ref is not None and (
            not isinstance(self.retry_of_ref, str)
            or not self.retry_of_ref.startswith(f"project://{self.project_id}/")
        ):
            raise ValueError("retry_of_ref must be a project-local logical ref")

    @property
    def failure_class(self) -> str:
        """Distinguish provider failure from post-provider rejection."""

        statuses = tuple(
            _validate_failed_envelope(envelope)
            for envelope in self.invocation_envelopes
        )
        return (
            "pipeline_rejected"
            if all(status == "success" for status in statuses)
            else "provider_failed"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base(self.base),
            "step_id": self.step_id,
            "intent_digest": self.intent_digest,
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id,
            "status": "failed",
            "error_code": self.error_code,
            "message": self.message,
            "invocation_envelopes": [dict(item) for item in self.invocation_envelopes],
            "retry_of_ref": self.retry_of_ref,
            "transition_checkpoint_ref": None,
            "lifecycle_successor": False,
            "fallback_used": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProductionFailedAttemptReceipt":
        if not isinstance(value, Mapping):
            raise ProductionTransitionError("failed attempt must be an object")
        expected = {
            "schema", "project_id", "run_id", "base", "step_id",
            "intent_digest", "attempt_index", "attempt_id", "status",
            "error_code", "message", "invocation_envelopes", "retry_of_ref",
            "transition_checkpoint_ref", "lifecycle_successor", "fallback_used",
            "persistence_authority", "canonical_write_authority",
        }
        if set(value) != expected or value.get("schema") != cls.SCHEMA:
            raise ProductionTransitionError("failed attempt schema drifted")
        if (
            value.get("status") != "failed"
            or value.get("transition_checkpoint_ref") is not None
            or value.get("lifecycle_successor") is not False
            or value.get("fallback_used") is not False
        ):
            raise ProductionTransitionError("failed attempt authority or status drifted")
        base = value.get("base")
        if not isinstance(base, Mapping):
            raise ProductionTransitionError("failed attempt base is malformed")
        envelopes = value.get("invocation_envelopes")
        if not isinstance(envelopes, list) or any(
            not isinstance(item, Mapping) for item in envelopes
        ):
            raise ProductionTransitionError("failed attempt envelopes are malformed")
        return cls(
            project_id=value["project_id"],
            run_id=value["run_id"],
            base=ProjectVersionRef(
                project_id=base.get("project_id"),
                version=base.get("version"),
                state_sha256=base.get("state_sha256"),
            ),
            step_id=value["step_id"],
            intent_digest=value["intent_digest"],
            attempt_index=value["attempt_index"],
            attempt_id=value["attempt_id"],
            error_code=value["error_code"],
            message=value["message"],
            invocation_envelopes=tuple(dict(item) for item in envelopes),
            retry_of_ref=value["retry_of_ref"],
        )


@dataclass(frozen=True, slots=True)
class ArchivedFailedProductionAttempt:
    ref: ProjectRecordRef
    receipt: ProductionFailedAttemptReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ProjectRecordRef):
            raise TypeError("ref must be ProjectRecordRef")
        if not isinstance(self.receipt, ProductionFailedAttemptReceipt):
            raise TypeError("receipt must be ProductionFailedAttemptReceipt")
        if self.ref.project_id != self.receipt.project_id:
            raise ProductionTransitionError("failed attempt ref crosses project")


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
    return canonical_digest(
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


def load_failed_production_attempts(
    repository: ProductionTransitionPort,
    *,
    run: RunRef,
    intent_digest: str,
    step_id: str,
) -> tuple[ArchivedFailedProductionAttempt, ...]:
    """Reload the ordered P036 failure history for one incomplete intent."""

    _sha(intent_digest, "intent_digest")
    require_identifier(step_id, "step_id")
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    attempts: list[ArchivedFailedProductionAttempt] = []
    for ref in repository.list_json(run=run, destination=destination):
        payload = repository.load_json(ref)
        if payload.get("schema") != ProductionFailedAttemptReceipt.SCHEMA:
            continue
        receipt = ProductionFailedAttemptReceipt.from_dict(payload)
        if (
            receipt.project_id == run.project_id
            and receipt.run_id == run.run_id
            and receipt.base == run.base
            and receipt.intent_digest == intent_digest
            and receipt.step_id == step_id
        ):
            attempts.append(ArchivedFailedProductionAttempt(ref, receipt))
    attempts.sort(key=lambda item: item.receipt.attempt_index)
    for index, attempt in enumerate(attempts):
        expected_retry = None if index == 0 else attempts[index - 1].ref.uri
        if (
            attempt.receipt.attempt_index != index
            or attempt.receipt.retry_of_ref != expected_retry
        ):
            raise ProductionTransitionError(
                "failed production attempt chain is non-contiguous"
            )
    return tuple(attempts)


def persist_failed_production_attempt(
    repository: ProductionTransitionPort,
    *,
    run: RunRef,
    intent_digest: str,
    step_id: str,
    error_code: str,
    message: str,
    invocation_envelopes: tuple[InvocationEnvelope, ...],
) -> ArchivedFailedProductionAttempt:
    """Persist provider failure or post-provider rejection without a checkpoint."""

    if not isinstance(invocation_envelopes, tuple) or not invocation_envelopes:
        raise ValueError("failed attempt requires invocation_envelopes")
    if any(not isinstance(item, InvocationEnvelope) for item in invocation_envelopes):
        raise TypeError("invocation_envelopes contains an invalid item")
    if load_production_transition(
        repository,
        run=run,
        intent_digest=intent_digest,
    ) is not None:
        raise ProductionTransitionError(
            "completed production intent cannot gain a failed attempt"
        )
    prior = load_failed_production_attempts(
        repository,
        run=run,
        intent_digest=intent_digest,
        step_id=step_id,
    )
    index = len(prior)
    receipt = ProductionFailedAttemptReceipt(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        step_id=step_id,
        intent_digest=intent_digest,
        attempt_index=index,
        attempt_id=f"attempt-{intent_digest[:16]}-{index:03d}",
        error_code=error_code,
        message=message,
        invocation_envelopes=tuple(item.to_dict() for item in invocation_envelopes),
        retry_of_ref=None if not prior else prior[-1].ref.uri,
    )
    ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind=f"production-failed-attempt-{index:03d}",
        payload=receipt.to_dict(),
    )
    return ArchivedFailedProductionAttempt(ref, receipt)


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
            canonical_digest(envelope.to_dict()),
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
        "content_sha256": canonical_digest(material),
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
    if canonical_digest(content) != payload["content_sha256"]:
        raise ProductionTransitionError("production record content digest drifted")
    if canonical_digest(content) != semantic_digest:
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


def _validate_failed_envelope(value: Mapping[str, Any]) -> str:
    expected = {
        "schema", "authority", "provider_receipt_json",
        "provider_receipt_digest", "canonical_write_authority",
        "envelope_signature",
    }
    if set(value) != expected or value.get("schema") != "ProductionInvocationEnvelope@2":
        raise ProductionTransitionError("failed attempt envelope schema drifted")
    authority = value.get("authority")
    if not isinstance(authority, Mapping) or authority.get("production_authority") is not True:
        raise ProductionTransitionError("failed attempt envelope lacks P053 authority")
    encoded = value.get("provider_receipt_json")
    if not isinstance(encoded, str):
        raise ProductionTransitionError("failed attempt provider receipt is not text")
    try:
        receipt = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ProductionTransitionError("failed attempt provider receipt is invalid") from exc
    if not isinstance(receipt, Mapping):
        raise ProductionTransitionError("failed attempt model receipt is malformed")
    if canonical_digest(receipt) != value.get("provider_receipt_digest"):
        raise ProductionTransitionError("failed attempt provider digest drifted")
    signature = value.get("envelope_signature")
    _sha(signature, "envelope_signature")
    try:
        model_receipt = ModelInvocationReceipt.from_dict(receipt)
    except (TypeError, ValueError) as exc:
        raise ProductionTransitionError(
            "failed attempt model receipt schema drifted"
        ) from exc
    return model_receipt.status.value


def _sha(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
