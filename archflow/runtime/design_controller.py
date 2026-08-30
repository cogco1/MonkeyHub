"""Pure coordinator for one nested, phase-aware Architect turn.

The controller compiles bounded context, discovers read-only experts, validates
the Architect's selected consultation order and rationale, then delegates the
actual state transition to deterministic nested-state closure. It has no model,
MCP, canonical writer, hard-gate waiver, or implicit persistence path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.capabilities.experts import (
    ExpertEvidence,
    ExpertObligation,
    ExpertReceipt,
    ExpertReceiptStatus,
    ExpertRegistry,
    ExpertSnapshot,
)
from archflow.capabilities.phase_gates import (
    PhaseExpertMetadata,
    discover_phase_experts,
    validate_architect_selected_expert_order,
)
from archflow.interaction.clarification import (
    AuthorityDecisionReceipt,
    ClarificationRequest,
)
from archflow.project.manifest import ProjectManifest
from archflow.project.ports import (
    PersistenceArea,
    PersistenceDestination,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.runtime.commitment_compiler import (
    CommitmentReplacementRequiresRevision,
    IntentCompilation,
    IntentCompilationStatus,
    IntentObservation,
    LockedCommitment,
    compile_intent,
)
from archflow.runtime.clarification import (
    ClarificationResumeStatus,
    resume_from_clarification,
)
from archflow.runtime.event_log import (
    AppendOnlyEventLog,
    DesignEvent,
    EventDecision,
)
from archflow.runtime.state_reducer import rebuild_canonical_state
from archflow.state.commitments import (
    CommitmentStatus,
    CommitmentStrength,
)
from archflow.state import (
    BackwardRevisionRequest,
    BackwardRevisionResult,
    Commitment,
    ContextSlice,
    ContextSliceCompiler,
    DecisionCompilationError,
    DecisionOperator,
    DesignMaturityState,
    DesignStateTree,
    NestedStateTransition,
    PhaseGateReceipt,
    PhaseTreeTransition,
    compile_backward_revision,
    compile_nested_decision,
    compile_tree_phase_change,
    require_current_phase_gate,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DesignObligation,
    ObligationCondition,
    ObligationStatus,
    require_logical_ref,
)


_MAX_ITEMS = 4096
_HEX = frozenset("0123456789abcdef")


class DesignControllerError(ValueError):
    """A controller input is stale, ungrounded, or structurally invalid."""


class ControllerStatus(StrEnum):
    READY = "ready"
    PAUSED_AUTHORITY = "paused_authority"
    STOPPED = "stopped"


class ControllerOutcome(StrEnum):
    TRANSITIONED = "transitioned"
    PHASE_ADVANCED = "phase_advanced"
    PHASE_REVISED = "phase_revised"
    STOPPED_STALE_BASE = "stopped_stale_base"
    STOPPED_NO_PROGRESS = "stopped_no_progress"
    STOPPED_REPEATED_ACTION = "stopped_repeated_action"
    STOPPED_BUDGET = "stopped_budget"
    PAUSED_AUTHORITY = "paused_authority"
    RESUMED_AUTHORITY = "resumed_authority"


class MidRunRequirementStatus(StrEnum):
    PROPOSED = "proposed"
    AMBIGUOUS = "ambiguous"
    ALREADY_LOCKED = "already_locked"
    UNKNOWN = "unknown"
    REVISION_REQUIRED = "revision_required"


class ControllerRecordSink(Protocol):
    """P036 surface required by the controller archive adapter."""

    def load_manifest(self) -> ProjectManifest: ...

    def load_run(self, run_id: str) -> RunRef: ...

    def put_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
        record_kind: str,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef: ...

    def list_json(
        self,
        *,
        run: RunRef,
        destination: PersistenceDestination,
    ) -> tuple[ProjectRecordRef, ...]: ...

    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DesignControllerError(f"{field} must be non-empty text")
    return value


def _tuple(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise DesignControllerError(f"{field} exceeds bounded item count")
    return value


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise DesignControllerError(f"{field} contains duplicates")


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    digest = value.lower()
    if len(digest) != 64 or any(char not in _HEX for char in digest):
        raise DesignControllerError(f"{field} must be a SHA-256 digest")
    return digest


def _same_branch(left: object, right: object) -> bool:
    return (
        getattr(left, "run", None) == getattr(right, "run", None)
        and getattr(left, "branch_id", None)
        == getattr(right, "branch_id", None)
    )


@dataclass(frozen=True, slots=True)
class DesignControllerCheckpoint:
    tree: DesignStateTree
    target_node_ref: str
    maturity: DesignMaturityState
    status: ControllerStatus
    iteration: int
    max_iterations: int
    history_event_refs: tuple[str, ...]
    decision_context_refs: tuple[str, ...] = ()
    recent_action_digests: tuple[str, ...] = ()
    reopened_node_refs: tuple[str, ...] = ()
    pending_clarification: ClarificationRequest | None = None
    stop_reason: str | None = None

    SCHEMA = "DesignControllerCheckpoint@1"

    def __post_init__(self) -> None:
        if not isinstance(self.tree, DesignStateTree):
            raise TypeError("tree must be a DesignStateTree")
        require_logical_ref(self.target_node_ref, "target_node_ref")
        target = self.tree.node(self.target_node_ref)
        if not isinstance(self.maturity, DesignMaturityState):
            raise TypeError("maturity must be a DesignMaturityState")
        if not _same_branch(self.maturity.branch, self.tree.branch):
            raise DesignControllerError(
                "maturity belongs to another run or branch"
            )
        if (
            self.maturity.operational_state_digest
            != target.operational_state.state_digest
        ):
            raise DesignControllerError(
                "maturity is not bound to target operational state"
            )
        if (
            self.maturity.phase.value
            != target.operational_state.phase
        ):
            raise DesignControllerError(
                "maturity phase disagrees with target state"
            )
        known_deliverables = set(
            self.maturity.deliverable_refs
        )
        for node in self.tree.nodes:
            unknown = set(node.phase_deliverable_refs) - (
                known_deliverables
            )
            if unknown:
                raise DesignControllerError(
                    "node names deliverables absent from maturity: "
                    f"{sorted(unknown)}"
                )
        if not isinstance(self.status, ControllerStatus):
            raise TypeError("status must be a ControllerStatus")
        for field, value in (
            ("iteration", self.iteration),
            ("max_iterations", self.max_iterations),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise DesignControllerError(
                    f"{field} must be a non-negative integer"
                )
        if self.max_iterations < 1:
            raise DesignControllerError("max_iterations must be positive")
        if self.iteration > self.max_iterations:
            raise DesignControllerError(
                "iteration cannot exceed max_iterations"
            )
        for field, values in (
            ("history_event_refs", self.history_event_refs),
            ("decision_context_refs", self.decision_context_refs),
            ("recent_action_digests", self.recent_action_digests),
            ("reopened_node_refs", self.reopened_node_refs),
        ):
            _tuple(values, field)
            _unique(values, field)
        if not self.history_event_refs:
            raise DesignControllerError(
                "checkpoint requires at least one P018 history event ref"
            )
        for ref in (
            *self.history_event_refs,
            *self.decision_context_refs,
            *self.reopened_node_refs,
        ):
            require_logical_ref(ref, "checkpoint reference")
        for digest in self.recent_action_digests:
            _sha256(digest, "recent action digest")
        if self.status is ControllerStatus.PAUSED_AUTHORITY:
            if not isinstance(
                self.pending_clarification,
                ClarificationRequest,
            ):
                raise DesignControllerError(
                    "authority pause requires a clarification request"
                )
            if self.stop_reason is not None:
                raise DesignControllerError(
                    "paused checkpoint cannot carry stop_reason"
                )
        elif self.pending_clarification is not None:
            raise DesignControllerError(
                "only authority pause may carry clarification"
            )
        if self.status is ControllerStatus.STOPPED:
            _text(self.stop_reason, "stop_reason")
        elif self.stop_reason is not None:
            raise DesignControllerError(
                "non-stopped checkpoint cannot carry stop_reason"
            )

    @property
    def checkpoint_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "tree": self.tree.to_dict(),
            "target_node_ref": self.target_node_ref,
            "maturity": self.maturity.to_dict(),
            "status": self.status.value,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "history_event_refs": list(self.history_event_refs),
            "decision_context_refs": list(
                self.decision_context_refs
            ),
            "recent_action_digests": list(
                self.recent_action_digests
            ),
            "reopened_node_refs": list(self.reopened_node_refs),
            "pending_clarification": (
                None
                if self.pending_clarification is None
                else self.pending_clarification.to_dict()
            ),
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> DesignControllerCheckpoint:
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "tree",
            "target_node_ref",
            "maturity",
            "status",
            "iteration",
            "max_iterations",
            "history_event_refs",
            "decision_context_refs",
            "recent_action_digests",
            "reopened_node_refs",
            "pending_clarification",
            "stop_reason",
        }:
            raise DesignControllerError("checkpoint schema drifted")
        if value["schema"] != cls.SCHEMA:
            raise DesignControllerError(
                "unsupported checkpoint schema"
            )
        tuple_values: dict[str, tuple[str, ...]] = {}
        for field in (
            "history_event_refs",
            "decision_context_refs",
            "recent_action_digests",
            "reopened_node_refs",
        ):
            raw = value[field]
            if not isinstance(raw, list) or any(
                not isinstance(item, str) for item in raw
            ):
                raise TypeError(f"{field} must be a string list")
            tuple_values[field] = tuple(raw)
        clarification = value["pending_clarification"]
        return cls(
            tree=DesignStateTree.from_dict(value["tree"]),
            target_node_ref=value["target_node_ref"],
            maturity=DesignMaturityState.from_dict(value["maturity"]),
            status=ControllerStatus(value["status"]),
            iteration=value["iteration"],
            max_iterations=value["max_iterations"],
            history_event_refs=tuple_values["history_event_refs"],
            decision_context_refs=tuple_values[
                "decision_context_refs"
            ],
            recent_action_digests=tuple_values[
                "recent_action_digests"
            ],
            reopened_node_refs=tuple_values["reopened_node_refs"],
            pending_clarification=(
                None
                if clarification is None
                else ClarificationRequest.from_dict(clarification)
            ),
            stop_reason=value["stop_reason"],
        )


@dataclass(frozen=True, slots=True)
class DurableControllerResume:
    record_ref: ProjectRecordRef
    checkpoint: DesignControllerCheckpoint
    event_chain: tuple[DesignEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.record_ref, ProjectRecordRef):
            raise TypeError("record_ref must be a ProjectRecordRef")
        if not isinstance(
            self.checkpoint,
            DesignControllerCheckpoint,
        ):
            raise TypeError(
                "checkpoint must be a DesignControllerCheckpoint"
            )
        if (
            not isinstance(self.event_chain, tuple)
            or not self.event_chain
            or any(
                not isinstance(item, DesignEvent)
                for item in self.event_chain
            )
        ):
            raise TypeError(
                "event_chain must contain DesignEvent values"
            )


def _project_version_payload(
    ref: ProjectVersionRef,
) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "version": ref.version,
        "state_sha256": ref.require_digest(),
    }


class ProjectControllerArchiveAdapter:
    """Bind P018 events and derived checkpoints to one P036 branch area."""

    EVENT_SCHEMA = DesignEvent.SCHEMA
    CHECKPOINT_SCHEMA = "DesignControllerCheckpointRecord@1"
    CHECKPOINT_FIELDS = frozenset(
        {
            "schema",
            "project_id",
            "run_id",
            "branch_id",
            "branch_epoch",
            "run_base",
            "checkpoint_digest",
            "event_count",
            "event_head_sha256",
            "checkpoint",
        }
    )

    def __init__(
        self,
        repository: ControllerRecordSink,
        *,
        branch: BranchRef,
    ) -> None:
        if not isinstance(branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        manifest = repository.load_manifest()
        if manifest.project_id != branch.run.project_id:
            raise DesignControllerError(
                "repository belongs to another project"
            )
        if manifest.format_version < 2:
            raise DesignControllerError(
                "durable controller resume requires project format version 2"
            )
        durable_run = repository.load_run(branch.run.run_id)
        if durable_run != branch.run:
            raise DesignControllerError(
                "controller branch does not match durable run exact base"
            )
        self._repository = repository
        self.branch = branch
        self._destination = PersistenceDestination(
            PersistenceArea.RUN_BRANCH,
            run_id=branch.run.run_id,
            branch_id=branch.branch_id,
        )

    @property
    def event_log(self) -> AppendOnlyEventLog:
        return AppendOnlyEventLog(
            self,
            project_id=self.branch.run.project_id,
        )

    def put_event(
        self,
        *,
        project_id: str,
        sequence: int,
        event_sha256: str,
        payload: Mapping[str, Any],
    ) -> None:
        if project_id != self.branch.run.project_id:
            raise DesignControllerError(
                "event belongs to another project"
            )
        event = DesignEvent.from_dict(payload)
        if (
            event.project_id != project_id
            or event.sequence != sequence
            or event.event_sha256 != event_sha256
        ):
            raise DesignControllerError(
                "event store arguments disagree with event payload"
            )
        self._repository.put_json(
            run=self.branch.run,
            destination=self._destination,
            record_kind=(
                f"design-event-{sequence:06d}-"
                f"{event_sha256[:20]}"
            ),
            payload=event.to_dict(),
        )

    def list_events(
        self,
        *,
        project_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        if project_id != self.branch.run.project_id:
            raise DesignControllerError(
                "event query belongs to another project"
            )
        events: list[DesignEvent] = []
        for ref in self._repository.list_json(
            run=self.branch.run,
            destination=self._destination,
        ):
            payload = self._repository.load_json(ref)
            if payload.get("schema") != self.EVENT_SCHEMA:
                continue
            events.append(DesignEvent.from_dict(payload))
        events.sort(key=lambda item: item.sequence)
        sequences = tuple(item.sequence for item in events)
        if len(sequences) != len(set(sequences)):
            raise DesignControllerError(
                "branch archive contains duplicate event sequences"
            )
        return tuple(item.to_dict() for item in events)

    def save_checkpoint(
        self,
        checkpoint: DesignControllerCheckpoint,
    ) -> ProjectRecordRef:
        if not isinstance(checkpoint, DesignControllerCheckpoint):
            raise TypeError(
                "checkpoint must be a DesignControllerCheckpoint"
            )
        if checkpoint.tree.branch != self.branch:
            raise DesignControllerError(
                "checkpoint belongs to another branch"
            )
        events = self.event_log.records()
        if not events:
            raise DesignControllerError(
                "durable checkpoint requires a P018 event chain"
            )
        event_refs = tuple(item.event_id for item in events)
        if checkpoint.history_event_refs != event_refs:
            raise DesignControllerError(
                "checkpoint history is not the exact durable event chain"
            )
        if events[-1].resulting_state != self.branch.run.base:
            raise DesignControllerError(
                "event chain does not reach the durable run exact base"
            )
        payload = {
            "schema": self.CHECKPOINT_SCHEMA,
            "project_id": self.branch.run.project_id,
            "run_id": self.branch.run.run_id,
            "branch_id": self.branch.branch_id,
            "branch_epoch": self.branch.epoch,
            "run_base": _project_version_payload(
                self.branch.run.base
            ),
            "checkpoint_digest": checkpoint.checkpoint_digest,
            "event_count": len(events),
            "event_head_sha256": events[-1].event_sha256,
            "checkpoint": checkpoint.to_dict(),
        }
        return self._repository.put_json(
            run=self.branch.run,
            destination=self._destination,
            record_kind=(
                f"design-controller-{len(events):06d}-"
                f"{checkpoint.iteration:06d}"
            ),
            payload=payload,
        )

    def load_checkpoint(
        self,
        ref: ProjectRecordRef,
    ) -> DurableControllerResume:
        self._require_branch_record(ref)
        payload = self._repository.load_json(ref)
        if (
            set(payload) != self.CHECKPOINT_FIELDS
            or payload.get("schema") != self.CHECKPOINT_SCHEMA
        ):
            raise DesignControllerError(
                "controller checkpoint record schema drifted"
            )
        if (
            payload["project_id"] != self.branch.run.project_id
            or payload["run_id"] != self.branch.run.run_id
            or payload["branch_id"] != self.branch.branch_id
            or payload["branch_epoch"] != self.branch.epoch
            or payload["run_base"]
            != _project_version_payload(self.branch.run.base)
        ):
            raise DesignControllerError(
                "controller checkpoint record identity changed"
            )
        checkpoint = DesignControllerCheckpoint.from_dict(
            payload["checkpoint"]
        )
        if (
            checkpoint.tree.branch != self.branch
            or checkpoint.checkpoint_digest
            != payload["checkpoint_digest"]
        ):
            raise DesignControllerError(
                "controller checkpoint content digest disagrees"
            )
        event_count = payload["event_count"]
        if (
            not isinstance(event_count, int)
            or isinstance(event_count, bool)
            or event_count < 1
        ):
            raise DesignControllerError(
                "controller checkpoint event_count is invalid"
            )
        all_events = self.event_log.records()
        if event_count > len(all_events):
            raise DesignControllerError(
                "controller checkpoint event prefix is incomplete"
            )
        events = all_events[:event_count]
        if (
            payload["event_head_sha256"]
            != events[-1].event_sha256
            or checkpoint.history_event_refs
            != tuple(item.event_id for item in events)
            or events[-1].resulting_state != self.branch.run.base
        ):
            raise DesignControllerError(
                "controller checkpoint event binding disagrees"
            )
        return DurableControllerResume(
            record_ref=ref,
            checkpoint=checkpoint,
            event_chain=events,
        )

    def load_latest_checkpoint(self) -> DurableControllerResume:
        candidates: list[
            tuple[tuple[int, int], DurableControllerResume]
        ] = []
        for ref in self._repository.list_json(
            run=self.branch.run,
            destination=self._destination,
        ):
            payload = self._repository.load_json(ref)
            if payload.get("schema") != self.CHECKPOINT_SCHEMA:
                continue
            if (
                payload.get("project_id")
                != self.branch.run.project_id
                or payload.get("run_id") != self.branch.run.run_id
                or payload.get("branch_id") != self.branch.branch_id
            ):
                raise DesignControllerError(
                    "controller checkpoint record identity changed"
                )
            branch_epoch = payload.get("branch_epoch")
            if (
                not isinstance(branch_epoch, int)
                or isinstance(branch_epoch, bool)
                or branch_epoch < 0
            ):
                raise DesignControllerError(
                    "controller checkpoint branch_epoch is invalid"
                )
            if branch_epoch != self.branch.epoch:
                continue
            if set(payload) != self.CHECKPOINT_FIELDS:
                raise DesignControllerError(
                    "controller checkpoint record schema drifted"
                )
            if payload["run_base"] != _project_version_payload(
                self.branch.run.base
            ):
                raise DesignControllerError(
                    "controller checkpoint record identity changed"
                )
            resumed = self.load_checkpoint(ref)
            candidates.append(
                (
                    (
                        len(resumed.event_chain),
                        resumed.checkpoint.iteration,
                    ),
                    resumed,
                )
            )
        if not candidates:
            raise DesignControllerError(
                "branch has no durable controller checkpoint"
            )
        latest_key = max(key for key, _ in candidates)
        latest = tuple(
            resumed
            for key, resumed in candidates
            if key == latest_key
        )
        digests = {
            item.checkpoint.checkpoint_digest for item in latest
        }
        if len(digests) != 1:
            raise DesignControllerError(
                "branch has ambiguous latest checkpoint lineage"
            )
        return latest[0]

    def _require_branch_record(
        self,
        ref: ProjectRecordRef,
    ) -> None:
        if not isinstance(ref, ProjectRecordRef):
            raise TypeError("ref must be a ProjectRecordRef")
        expected_prefix = (
            f"runs/{self.branch.run.run_id}/branches/"
            f"{self.branch.branch_id}/records/"
        )
        if (
            ref.project_id != self.branch.run.project_id
            or not ref.relative_path.startswith(expected_prefix)
        ):
            raise DesignControllerError(
                "controller record belongs to another branch"
            )


@dataclass(frozen=True, slots=True)
class PreparedDesignTurn:
    checkpoint_digest: str
    context: ContextSlice
    snapshot: ExpertSnapshot
    discovered_expert_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _sha256(self.checkpoint_digest, "checkpoint_digest")
        if not isinstance(self.context, ContextSlice):
            raise TypeError("context must be a ContextSlice")
        if not isinstance(self.snapshot, ExpertSnapshot):
            raise TypeError("snapshot must be an ExpertSnapshot")
        _tuple(self.discovered_expert_ids, "discovered_expert_ids")
        _unique(self.discovered_expert_ids, "discovered_expert_ids")


@dataclass(frozen=True, slots=True)
class ExpertConsultation:
    checkpoint_digest: str
    context_digest: str
    selected_expert_ids: tuple[str, ...]
    receipts: tuple[ExpertReceipt, ...]

    def __post_init__(self) -> None:
        _sha256(self.checkpoint_digest, "checkpoint_digest")
        _sha256(self.context_digest, "context_digest")
        _tuple(self.selected_expert_ids, "selected_expert_ids")
        _unique(self.selected_expert_ids, "selected_expert_ids")
        _tuple(self.receipts, "receipts")
        if any(
            not isinstance(item, ExpertReceipt)
            for item in self.receipts
        ):
            raise TypeError("receipts must contain ExpertReceipt")
        if tuple(item.expert_id for item in self.receipts) != (
            self.selected_expert_ids
        ):
            raise DesignControllerError(
                "expert receipts do not preserve selected order"
            )

    @property
    def advice_refs(self) -> tuple[str, ...]:
        return tuple(
            f"expert-receipt:{item.receipt_id}"
            for item in self.receipts
            if item.status is ExpertReceiptStatus.ADVICE
        )


@dataclass(frozen=True, slots=True)
class GroundedArchitectAction:
    action_id: str
    checkpoint_digest: str
    context_digest: str
    operator: DecisionOperator
    responds_to_refs: tuple[str, ...]
    selected_expert_ids: tuple[str, ...]
    adopted_advice_refs: tuple[str, ...]
    rejected_advice_refs: tuple[str, ...]
    tradeoff_rationale: str

    def __post_init__(self) -> None:
        _text(self.action_id, "action_id")
        _sha256(self.checkpoint_digest, "checkpoint_digest")
        _sha256(self.context_digest, "context_digest")
        if not isinstance(self.operator, DecisionOperator):
            raise TypeError("operator must be a DecisionOperator")
        for field, values in (
            ("responds_to_refs", self.responds_to_refs),
            ("selected_expert_ids", self.selected_expert_ids),
            ("adopted_advice_refs", self.adopted_advice_refs),
            ("rejected_advice_refs", self.rejected_advice_refs),
        ):
            _tuple(values, field)
            _unique(values, field)
        if not self.responds_to_refs:
            raise DesignControllerError(
                "Architect action requires responds_to_refs"
            )
        for ref in (
            *self.responds_to_refs,
            *self.adopted_advice_refs,
            *self.rejected_advice_refs,
        ):
            require_logical_ref(ref, "Architect action reference")
        if set(self.adopted_advice_refs) & set(
            self.rejected_advice_refs
        ):
            raise DesignControllerError(
                "adopted and rejected advice overlap"
            )
        _text(self.tradeoff_rationale, "tradeoff_rationale")

    @property
    def action_digest(self) -> str:
        return _digest(
            {
                "action_id": self.action_id,
                "checkpoint_digest": self.checkpoint_digest,
                "context_digest": self.context_digest,
                "operator": self.operator.to_dict(),
                "responds_to_refs": self.responds_to_refs,
                "selected_expert_ids": self.selected_expert_ids,
                "adopted_advice_refs": self.adopted_advice_refs,
                "rejected_advice_refs": self.rejected_advice_refs,
                "tradeoff_rationale": self.tradeoff_rationale,
            }
        )

    @property
    def plan_digest(self) -> str:
        """Detect a repeated substantive plan across fresh exact bases."""

        operator = self.operator.to_dict()
        operator.pop("decision_id", None)
        operator.pop("base_state_digest", None)
        return _digest(
            {
                "operator": operator,
                "responds_to_refs": self.responds_to_refs,
                "selected_expert_ids": self.selected_expert_ids,
                "adopted_advice_refs": self.adopted_advice_refs,
                "rejected_advice_refs": self.rejected_advice_refs,
            }
        )


@dataclass(frozen=True, slots=True)
class ControllerTurnReceipt:
    receipt_id: str
    outcome: ControllerOutcome
    previous_checkpoint_digest: str
    next_checkpoint_digest: str
    action_digest: str | None
    responds_to_refs: tuple[str, ...]
    adopted_advice_refs: tuple[str, ...]
    rejected_advice_refs: tuple[str, ...]
    changed_node_ref: str | None
    invalidated_node_refs: tuple[str, ...]
    revalidation_node_refs: tuple[str, ...]
    history_event_ref: str
    reason: str

    def __post_init__(self) -> None:
        _text(self.receipt_id, "receipt_id")
        if not isinstance(self.outcome, ControllerOutcome):
            raise TypeError("outcome must be a ControllerOutcome")
        _sha256(
            self.previous_checkpoint_digest,
            "previous_checkpoint_digest",
        )
        _sha256(
            self.next_checkpoint_digest,
            "next_checkpoint_digest",
        )
        if self.action_digest is not None:
            _sha256(self.action_digest, "action_digest")
        require_logical_ref(self.history_event_ref, "history_event_ref")
        _text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class ControllerTurnResult:
    checkpoint: DesignControllerCheckpoint
    receipt: ControllerTurnReceipt
    transition: NestedStateTransition | None = None
    phase_transition: PhaseTreeTransition | None = None
    phase_gate: PhaseGateReceipt | None = None
    backward_revision: BackwardRevisionResult | None = None


@dataclass(frozen=True, slots=True)
class MidRunRequirementResult:
    checkpoint: DesignControllerCheckpoint
    status: MidRunRequirementStatus
    event_ref: str
    compilation: IntentCompilation | None
    transition: NestedStateTransition | None
    conflicting_commitment_id: str | None = None
    affected_node_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(
            self.checkpoint,
            DesignControllerCheckpoint,
        ):
            raise TypeError(
                "checkpoint must be a DesignControllerCheckpoint"
            )
        if not isinstance(self.status, MidRunRequirementStatus):
            raise TypeError(
                "status must be a MidRunRequirementStatus"
            )
        require_logical_ref(self.event_ref, "event_ref")
        _tuple(self.affected_node_refs, "affected_node_refs")
        _unique(self.affected_node_refs, "affected_node_refs")
        for ref in self.affected_node_refs:
            require_logical_ref(ref, "affected_node_ref")
        if (
            self.status
            is MidRunRequirementStatus.REVISION_REQUIRED
        ):
            _text(
                self.conflicting_commitment_id,
                "conflicting_commitment_id",
            )
        elif self.conflicting_commitment_id is not None:
            raise DesignControllerError(
                "only revision-required result names a conflict"
            )


def prepare_design_turn(
    checkpoint: DesignControllerCheckpoint,
    registry: ExpertRegistry,
    *,
    phase_metadata: Mapping[str, PhaseExpertMetadata],
    obligation_topics: Mapping[str, str],
    evidence: tuple[ExpertEvidence, ...] = (),
) -> PreparedDesignTurn:
    if not isinstance(checkpoint, DesignControllerCheckpoint):
        raise TypeError(
            "checkpoint must be a DesignControllerCheckpoint"
        )
    if checkpoint.status is not ControllerStatus.READY:
        raise DesignControllerError(
            "only a ready checkpoint can prepare a turn"
        )
    if checkpoint.iteration >= checkpoint.max_iterations:
        raise DesignControllerError(
            "iteration budget exhausted; create a stopped checkpoint"
        )
    context = ContextSliceCompiler().compile(
        checkpoint.tree,
        target_node_ref=checkpoint.target_node_ref,
    )
    obligation_ids = {
        item.obligation_id for item in context.obligations
    }
    if set(obligation_topics) != obligation_ids:
        raise DesignControllerError(
            "obligation topics must exactly cover current context"
        )
    snapshot = ExpertSnapshot(
        base_state=(
            checkpoint.tree.node(
                checkpoint.target_node_ref
            ).operational_state.branch.run.base
        ),
        program_json=None,
        obligations=tuple(
            ExpertObligation(
                obligation_id=item.obligation_id,
                topic=obligation_topics[item.obligation_id],
                statement=item.statement,
                source_ref=item.source_ref,
            )
            for item in context.obligations
        ),
        evidence=evidence,
    )
    discovered = discover_phase_experts(
        registry,
        snapshot,
        phase=checkpoint.maturity.phase,
        metadata=phase_metadata,
    )
    return PreparedDesignTurn(
        checkpoint_digest=checkpoint.checkpoint_digest,
        context=context,
        snapshot=snapshot,
        discovered_expert_ids=tuple(
            item.expert_id for item in discovered
        ),
    )


def consult_selected_experts(
    prepared: PreparedDesignTurn,
    registry: ExpertRegistry,
    selected_expert_ids: tuple[str, ...],
) -> ExpertConsultation:
    if not isinstance(prepared, PreparedDesignTurn):
        raise TypeError("prepared must be a PreparedDesignTurn")
    discovered = tuple(
        registry.get(expert_id)
        for expert_id in prepared.discovered_expert_ids
    )
    selected = validate_architect_selected_expert_order(
        selected_expert_ids,
        discovered,
    )
    receipts = tuple(
        registry.invoke(expert_id, prepared.snapshot)
        for expert_id in selected
    )
    return ExpertConsultation(
        checkpoint_digest=prepared.checkpoint_digest,
        context_digest=prepared.context.context_digest,
        selected_expert_ids=selected,
        receipts=receipts,
    )


def apply_architect_action(
    checkpoint: DesignControllerCheckpoint,
    prepared: PreparedDesignTurn,
    consultation: ExpertConsultation,
    action: GroundedArchitectAction,
    *,
    history_event_ref: str,
) -> ControllerTurnResult:
    require_logical_ref(history_event_ref, "history_event_ref")
    if (
        prepared.checkpoint_digest != checkpoint.checkpoint_digest
        or action.checkpoint_digest != checkpoint.checkpoint_digest
        or consultation.checkpoint_digest
        != checkpoint.checkpoint_digest
    ):
        raise DesignControllerError(
            "turn input is stale against checkpoint"
        )
    if (
        action.context_digest != prepared.context.context_digest
        or consultation.context_digest
        != prepared.context.context_digest
    ):
        raise DesignControllerError("turn context digest is stale")
    if action.selected_expert_ids != consultation.selected_expert_ids:
        raise DesignControllerError(
            "action expert order differs from consultation"
        )
    advice_refs = set(consultation.advice_refs)
    accounted = set(action.adopted_advice_refs) | set(
        action.rejected_advice_refs
    )
    if accounted != advice_refs:
        raise DesignControllerError(
            "Architect must adopt or reject every advice receipt"
        )
    available_response_refs = {
        *(f"obligation:{item.obligation_id}" for item in prepared.context.obligations),
        *(f"commitment:{item.commitment_id}" for item in prepared.context.commitments),
        *(item.ref for item in prepared.context.interfaces),
        f"phase-task:{checkpoint.maturity.phase.value}",
        *checkpoint.decision_context_refs,
    }
    unknown_responses = set(action.responds_to_refs) - (
        available_response_refs
    )
    if unknown_responses:
        raise DesignControllerError(
            f"action responds to unavailable refs: {sorted(unknown_responses)}"
        )
    governing_commitment_refs = {
        f"commitment:{item.commitment_id}"
        for item in checkpoint.tree.root.operational_state.commitments
        if item.strength is CommitmentStrength.HARD
        and item.status
        in {
            CommitmentStatus.ACTIVE,
            CommitmentStatus.VIOLATED,
        }
    }
    missing_governing_refs = governing_commitment_refs - set(
        action.responds_to_refs
    )
    if missing_governing_refs:
        raise DesignControllerError(
            "action omits governing global commitments: "
            f"{sorted(missing_governing_refs)}"
        )
    if action.plan_digest in checkpoint.recent_action_digests:
        stopped = replace(
            checkpoint,
            status=ControllerStatus.STOPPED,
            pending_clarification=None,
            stop_reason="repeated Architect action",
            history_event_refs=(
                *checkpoint.history_event_refs,
                history_event_ref,
            ),
        )
        return _result(
            previous=checkpoint,
            checkpoint=stopped,
            outcome=ControllerOutcome.STOPPED_REPEATED_ACTION,
            history_event_ref=history_event_ref,
            reason="repeated Architect action stopped before compilation",
            action=action,
        )
    try:
        nested = compile_nested_decision(
            checkpoint.tree,
            target_node_ref=checkpoint.target_node_ref,
            operator=action.operator,
        )
    except DecisionCompilationError as exc:
        message = str(exc)
        if message == "decision operator exact base is stale":
            outcome = ControllerOutcome.STOPPED_STALE_BASE
            stop_reason = "stale decision operator base"
        elif message == "decision operator has no state effect":
            outcome = ControllerOutcome.STOPPED_NO_PROGRESS
            stop_reason = "Architect action has no state effect"
        else:
            raise
        stopped = replace(
            checkpoint,
            status=ControllerStatus.STOPPED,
            stop_reason=stop_reason,
            history_event_refs=(
                *checkpoint.history_event_refs,
                history_event_ref,
            ),
        )
        return _result(
            previous=checkpoint,
            checkpoint=stopped,
            outcome=outcome,
            history_event_ref=history_event_ref,
            reason=stop_reason,
            action=action,
        )
    next_maturity = replace(
        checkpoint.maturity,
        branch=nested.tree.branch,
        operational_state_digest=(
            nested.tree.node(
                checkpoint.target_node_ref
            ).operational_state.state_digest
        ),
    )
    next_iteration = checkpoint.iteration + 1
    exhausted = next_iteration >= checkpoint.max_iterations
    next_checkpoint = DesignControllerCheckpoint(
        tree=nested.tree,
        target_node_ref=checkpoint.target_node_ref,
        maturity=next_maturity,
        status=(
            ControllerStatus.STOPPED
            if exhausted
            else ControllerStatus.READY
        ),
        iteration=next_iteration,
        max_iterations=checkpoint.max_iterations,
        history_event_refs=(
            *checkpoint.history_event_refs,
            history_event_ref,
        ),
        decision_context_refs=checkpoint.decision_context_refs,
        recent_action_digests=(
            *checkpoint.recent_action_digests[-7:],
            action.plan_digest,
        ),
        reopened_node_refs=tuple(
            sorted(
                {
                    *nested.invalidated_node_refs,
                    *nested.revalidation_node_refs,
                }
            )
        ),
        stop_reason=(
            "iteration budget exhausted" if exhausted else None
        ),
    )
    return _result(
        previous=checkpoint,
        checkpoint=next_checkpoint,
        outcome=(
            ControllerOutcome.STOPPED_BUDGET
            if exhausted
            else ControllerOutcome.TRANSITIONED
        ),
        history_event_ref=history_event_ref,
        reason=(
            "transition compiled; iteration budget exhausted"
            if exhausted
            else "grounded Architect action compiled"
        ),
        action=action,
        nested=nested,
    )


def advance_design_phase(
    checkpoint: DesignControllerCheckpoint,
    receipt: PhaseGateReceipt,
    *,
    history_event_ref: str,
) -> ControllerTurnResult:
    """Advance only after obligations close and P039 revalidates the gate."""

    require_logical_ref(history_event_ref, "history_event_ref")
    if checkpoint.status is not ControllerStatus.READY:
        raise DesignControllerError(
            "only a ready checkpoint can advance phase"
        )
    active = tuple(
        sorted(
            item.obligation_id
            for node in checkpoint.tree.nodes
            for item in node.operational_state.obligations
            if item.status
            in {ObligationStatus.OPEN, ObligationStatus.BLOCKED}
        )
    )
    if active:
        raise DesignControllerError(
            f"phase cannot advance with active obligations: {active}"
        )
    require_current_phase_gate(checkpoint.maturity, receipt)
    phase_transition = compile_tree_phase_change(
        checkpoint.tree,
        next_phase=receipt.to_phase.value,
    )
    next_target_ref = phase_transition.remap(
        checkpoint.target_node_ref
    )
    next_target = phase_transition.tree.node(next_target_ref)
    next_maturity = replace(
        checkpoint.maturity,
        branch=phase_transition.tree.branch,
        operational_state_digest=(
            next_target.operational_state.state_digest
        ),
        phase=receipt.to_phase,
        invalidated_refs=(),
        revalidation_required_refs=(),
    )
    next_checkpoint = DesignControllerCheckpoint(
        tree=phase_transition.tree,
        target_node_ref=next_target_ref,
        maturity=next_maturity,
        status=ControllerStatus.READY,
        iteration=checkpoint.iteration,
        max_iterations=checkpoint.max_iterations,
        history_event_refs=(
            *checkpoint.history_event_refs,
            history_event_ref,
        ),
        decision_context_refs=checkpoint.decision_context_refs,
        recent_action_digests=checkpoint.recent_action_digests,
        reopened_node_refs=(),
    )
    return _result(
        previous=checkpoint,
        checkpoint=next_checkpoint,
        outcome=ControllerOutcome.PHASE_ADVANCED,
        history_event_ref=history_event_ref,
        reason=(
            f"deterministic phase gate advanced "
            f"{receipt.from_phase.value} to {receipt.to_phase.value}"
        ),
        phase_transition=phase_transition,
        phase_gate=receipt,
    )


def revise_design_phase(
    checkpoint: DesignControllerCheckpoint,
    request: BackwardRevisionRequest,
    dependencies: tuple[DependencyEdge, ...],
    *,
    history_event_ref: str,
) -> ControllerTurnResult:
    """Regress phase and attach only dependency-local repair obligations."""

    require_logical_ref(history_event_ref, "history_event_ref")
    if checkpoint.status is not ControllerStatus.READY:
        raise DesignControllerError(
            "only a ready checkpoint can revise phase"
        )
    revision = compile_backward_revision(
        checkpoint.maturity,
        request,
        dependencies,
    )
    phase_transition = compile_tree_phase_change(
        checkpoint.tree,
        next_phase=revision.to_phase.value,
        obligation_target_ref=checkpoint.target_node_ref,
        add_obligations=revision.spawned_obligations,
    )
    next_target_ref = phase_transition.remap(
        checkpoint.target_node_ref
    )
    next_target = phase_transition.tree.node(next_target_ref)
    invalidated = tuple(
        sorted(
            {
                *checkpoint.maturity.invalidated_refs,
                *revision.invalidated_deliverable_refs,
            }
        )
    )
    revalidation = tuple(
        sorted(
            (
                {
                    *checkpoint.maturity.revalidation_required_refs,
                    *revision.revalidation_required_refs,
                }
                - set(invalidated)
            )
        )
    )
    next_maturity = replace(
        checkpoint.maturity,
        branch=phase_transition.tree.branch,
        operational_state_digest=(
            next_target.operational_state.state_digest
        ),
        phase=revision.to_phase,
        invalidated_refs=invalidated,
        revalidation_required_refs=revalidation,
    )
    next_checkpoint = DesignControllerCheckpoint(
        tree=phase_transition.tree,
        target_node_ref=next_target_ref,
        maturity=next_maturity,
        status=ControllerStatus.READY,
        iteration=checkpoint.iteration,
        max_iterations=checkpoint.max_iterations,
        history_event_refs=(
            *checkpoint.history_event_refs,
            history_event_ref,
        ),
        decision_context_refs=checkpoint.decision_context_refs,
        recent_action_digests=checkpoint.recent_action_digests,
        reopened_node_refs=(next_target_ref,),
    )
    return _result(
        previous=checkpoint,
        checkpoint=next_checkpoint,
        outcome=ControllerOutcome.PHASE_REVISED,
        history_event_ref=history_event_ref,
        reason=(
            f"dependency-local revision regressed "
            f"{revision.from_phase.value} to "
            f"{revision.to_phase.value}"
        ),
        phase_transition=phase_transition,
        backward_revision=revision,
    )


def compile_mid_run_requirement(
    checkpoint: DesignControllerCheckpoint,
    *,
    event_chain: tuple[DesignEvent, ...],
    observation: IntentObservation,
    locked: tuple[LockedCommitment, ...] = (),
) -> MidRunRequirementResult:
    """Compile one event-backed addition without replaying the transcript."""

    if checkpoint.status is not ControllerStatus.READY:
        raise DesignControllerError(
            "only a ready checkpoint can accept a requirement event"
        )
    if not isinstance(event_chain, tuple) or not event_chain:
        raise DesignControllerError(
            "mid-run requirement needs a P018 event chain"
        )
    rebuilt = rebuild_canonical_state(event_chain)
    event = event_chain[-1]
    if event.decision is not EventDecision.OBSERVED:
        raise DesignControllerError(
            "requirement input must be an observed P018 event"
        )
    run = checkpoint.tree.branch.run
    if (
        event.project_id != run.project_id
        or rebuilt.state.ref != run.base
    ):
        raise DesignControllerError(
            "requirement event is stale or belongs to another project"
        )
    if (
        observation.source_event_ref != event.event_id
        or observation.authority_id != event.authority_id
    ):
        raise DesignControllerError(
            "intent observation is not bound to event authority"
        )
    available_evidence = {
        event.event_id,
        *event.evidence_refs,
        *event.artifact_refs,
    }
    if not set(observation.evidence_refs) <= available_evidence:
        raise DesignControllerError(
            "intent evidence is not carried by the P018 event"
        )
    if event.event_id in checkpoint.history_event_refs:
        raise DesignControllerError(
            "requirement event was already consumed"
        )
    target = checkpoint.tree.node(checkpoint.target_node_ref)
    context = ContextSliceCompiler().compile(
        checkpoint.tree,
        target_node_ref=checkpoint.target_node_ref,
    )
    current_commitments = {
        item.commitment_id: item for item in context.commitments
    }
    for item in locked:
        if (
            current_commitments.get(item.commitment.commitment_id)
            != item.commitment
        ):
            raise DesignControllerError(
                "locked commitment is absent from current context"
            )
    try:
        compilation = compile_intent(
            observation,
            locked=locked,
        )
    except CommitmentReplacementRequiresRevision:
        parameter_key = observation.interpretations[0].parameter_key
        conflicts = tuple(
            item
            for item in locked
            if item.term.parameter_key == parameter_key
        )
        if len(conflicts) != 1:
            raise DesignControllerError(
                "locked conflict cannot be resolved to one commitment"
            )
        conflict = conflicts[0]
        obligation = DesignObligation(
            obligation_id=(
                f"require-revision-{event.event_sha256[:20]}"
            ),
            statement=(
                "Obtain authorized revision before replacing locked "
                f"commitment {conflict.commitment.commitment_id}."
            ),
            source_ref=event.event_id,
            status=ObligationStatus.BLOCKED,
            condition=ObligationCondition(
                ref=(
                    "authority://commitment-revision/"
                    f"{conflict.commitment.commitment_id}"
                ),
                expected_value="authorized",
            ),
            subject_refs=tuple(
                sorted(
                    {
                        (
                            "commitment:"
                            f"{conflict.commitment.commitment_id}"
                        ),
                        *conflict.commitment.scope_refs,
                        *(
                            item.ref
                            for item in observation.interpretations
                        ),
                    }
                )
            ),
        )
        operator = DecisionOperator(
            decision_id=(
                f"compile-revision-{event.event_sha256[:20]}"
            ),
            decision_type="mid-run-requirement-conflict",
            base_state_digest=target.operational_state.state_digest,
            authority_id=observation.authority_id,
            intent="Record an authority-blocked commitment conflict.",
            spawn_obligations=(obligation,),
            evidence_refs=tuple(sorted(available_evidence)),
        )
        nested = compile_nested_decision(
            checkpoint.tree,
            target_node_ref=checkpoint.target_node_ref,
            operator=operator,
        )
        next_checkpoint = _checkpoint_after_nested_event(
            checkpoint,
            nested,
            event_ref=event.event_id,
        )
        return MidRunRequirementResult(
            checkpoint=next_checkpoint,
            status=MidRunRequirementStatus.REVISION_REQUIRED,
            event_ref=event.event_id,
            compilation=None,
            transition=nested,
            conflicting_commitment_id=(
                conflict.commitment.commitment_id
            ),
            affected_node_refs=tuple(
                sorted(
                    {
                        checkpoint.target_node_ref,
                        *nested.invalidated_node_refs,
                        *nested.revalidation_node_refs,
                    }
                )
            ),
        )

    status_by_compilation = {
        IntentCompilationStatus.PROPOSED: (
            MidRunRequirementStatus.PROPOSED
        ),
        IntentCompilationStatus.AMBIGUOUS: (
            MidRunRequirementStatus.AMBIGUOUS
        ),
        IntentCompilationStatus.ALREADY_LOCKED: (
            MidRunRequirementStatus.ALREADY_LOCKED
        ),
        IntentCompilationStatus.UNKNOWN: (
            MidRunRequirementStatus.UNKNOWN
        ),
    }
    status = status_by_compilation[compilation.status]
    if not compilation.proposals:
        next_checkpoint = replace(
            checkpoint,
            history_event_refs=(
                *checkpoint.history_event_refs,
                event.event_id,
            ),
        )
        return MidRunRequirementResult(
            checkpoint=next_checkpoint,
            status=status,
            event_ref=event.event_id,
            compilation=compilation,
            transition=None,
        )

    obligation = DesignObligation(
        obligation_id=f"confirm-{compilation.compilation_id}",
        statement=(
            "Confirm one proposed requirement interpretation."
            if compilation.status is IntentCompilationStatus.AMBIGUOUS
            else "Confirm or reject the proposed requirement."
        ),
        source_ref=event.event_id,
        status=(
            ObligationStatus.BLOCKED
            if compilation.status is IntentCompilationStatus.AMBIGUOUS
            else ObligationStatus.OPEN
        ),
        condition=(
            ObligationCondition(
                ref=(
                    "authority://intent-selection/"
                    f"{compilation.compilation_id}"
                ),
                expected_value="selected",
            )
            if compilation.status is IntentCompilationStatus.AMBIGUOUS
            else None
        ),
        subject_refs=tuple(
            f"commitment:{item.commitment.commitment_id}"
            for item in compilation.proposals
        ),
    )
    operator = DecisionOperator(
        decision_id=f"compile-{compilation.compilation_id}",
        decision_type="mid-run-requirement",
        base_state_digest=target.operational_state.state_digest,
        authority_id=observation.authority_id,
        intent="Record event-backed proposed commitments.",
        spawn_commitments=tuple(
            item.commitment for item in compilation.proposals
        ),
        spawn_obligations=(obligation,),
        evidence_refs=tuple(sorted(available_evidence)),
    )
    nested = compile_nested_decision(
        checkpoint.tree,
        target_node_ref=checkpoint.target_node_ref,
        operator=operator,
    )
    next_checkpoint = _checkpoint_after_nested_event(
        checkpoint,
        nested,
        event_ref=event.event_id,
    )
    return MidRunRequirementResult(
        checkpoint=next_checkpoint,
        status=status,
        event_ref=event.event_id,
        compilation=compilation,
        transition=nested,
        affected_node_refs=tuple(
            sorted(
                {
                    checkpoint.target_node_ref,
                    *nested.invalidated_node_refs,
                    *nested.revalidation_node_refs,
                }
            )
        ),
    )


def pause_for_clarification(
    checkpoint: DesignControllerCheckpoint,
    request: ClarificationRequest,
    *,
    history_event_ref: str,
) -> ControllerTurnResult:
    require_logical_ref(history_event_ref, "history_event_ref")
    target_state = checkpoint.tree.node(
        checkpoint.target_node_ref
    ).operational_state
    if (
        not _same_branch(request.branch, target_state.branch)
        or request.operational_state_digest != target_state.state_digest
    ):
        raise DesignControllerError(
            "clarification request is stale or cross-branch"
        )
    paused = replace(
        checkpoint,
        status=ControllerStatus.PAUSED_AUTHORITY,
        pending_clarification=request,
        history_event_refs=(
            *checkpoint.history_event_refs,
            history_event_ref,
        ),
    )
    return _result(
        previous=checkpoint,
        checkpoint=paused,
        outcome=ControllerOutcome.PAUSED_AUTHORITY,
        history_event_ref=history_event_ref,
        reason="waiting for exact-base named authority",
    )


def resume_authority_pause(
    checkpoint: DesignControllerCheckpoint,
    *,
    now_utc: str,
    history_event_ref: str,
    receipt: AuthorityDecisionReceipt | None = None,
    commitment_catalog: tuple[Commitment, ...] = (),
    consumed_request_ids: tuple[str, ...] = (),
) -> ControllerTurnResult:
    if (
        checkpoint.status is not ControllerStatus.PAUSED_AUTHORITY
        or checkpoint.pending_clarification is None
    ):
        raise DesignControllerError(
            "checkpoint is not paused for authority"
        )
    request = checkpoint.pending_clarification
    target_state = checkpoint.tree.node(
        checkpoint.target_node_ref
    ).operational_state
    resumed = resume_from_clarification(
        request,
        target_state,
        now_utc=now_utc,
        receipt=receipt,
        commitment_catalog=commitment_catalog,
        consumed_request_ids=consumed_request_ids,
    )
    if resumed.status is ClarificationResumeStatus.BLOCKED:
        return _result(
            previous=checkpoint,
            checkpoint=checkpoint,
            outcome=ControllerOutcome.PAUSED_AUTHORITY,
            history_event_ref=history_event_ref,
            reason=resumed.reason,
        )
    assert resumed.operator is not None
    assert receipt is not None
    nested = compile_nested_decision(
        checkpoint.tree,
        target_node_ref=checkpoint.target_node_ref,
        operator=resumed.operator,
    )
    next_maturity = replace(
        checkpoint.maturity,
        branch=nested.tree.branch,
        operational_state_digest=(
            nested.tree.node(
                checkpoint.target_node_ref
            ).operational_state.state_digest
        ),
    )
    next_checkpoint = DesignControllerCheckpoint(
        tree=nested.tree,
        target_node_ref=checkpoint.target_node_ref,
        maturity=next_maturity,
        status=ControllerStatus.READY,
        iteration=checkpoint.iteration,
        max_iterations=checkpoint.max_iterations,
        history_event_refs=(
            *checkpoint.history_event_refs,
            history_event_ref,
        ),
        decision_context_refs=tuple(
            sorted(
                {
                    *checkpoint.decision_context_refs,
                    receipt.ref,
                }
            )
        ),
        recent_action_digests=checkpoint.recent_action_digests,
        reopened_node_refs=tuple(
            sorted(
                {
                    *nested.invalidated_node_refs,
                    *nested.revalidation_node_refs,
                }
            )
        ),
    )
    return _result(
        previous=checkpoint,
        checkpoint=next_checkpoint,
        outcome=ControllerOutcome.RESUMED_AUTHORITY,
        history_event_ref=history_event_ref,
        reason=resumed.reason,
        nested=nested,
    )


def _checkpoint_after_nested_event(
    checkpoint: DesignControllerCheckpoint,
    nested: NestedStateTransition,
    *,
    event_ref: str,
) -> DesignControllerCheckpoint:
    return DesignControllerCheckpoint(
        tree=nested.tree,
        target_node_ref=checkpoint.target_node_ref,
        maturity=replace(
            checkpoint.maturity,
            branch=nested.tree.branch,
            operational_state_digest=(
                nested.tree.node(
                    checkpoint.target_node_ref
                ).operational_state.state_digest
            ),
        ),
        status=ControllerStatus.READY,
        iteration=checkpoint.iteration,
        max_iterations=checkpoint.max_iterations,
        history_event_refs=(
            *checkpoint.history_event_refs,
            event_ref,
        ),
        decision_context_refs=checkpoint.decision_context_refs,
        recent_action_digests=checkpoint.recent_action_digests,
        reopened_node_refs=tuple(
            sorted(
                {
                    *nested.invalidated_node_refs,
                    *nested.revalidation_node_refs,
                }
            )
        ),
    )


def _result(
    *,
    previous: DesignControllerCheckpoint,
    checkpoint: DesignControllerCheckpoint,
    outcome: ControllerOutcome,
    history_event_ref: str,
    reason: str,
    action: GroundedArchitectAction | None = None,
    nested: NestedStateTransition | None = None,
    phase_transition: PhaseTreeTransition | None = None,
    phase_gate: PhaseGateReceipt | None = None,
    backward_revision: BackwardRevisionResult | None = None,
) -> ControllerTurnResult:
    payload = {
        "outcome": outcome.value,
        "previous": previous.checkpoint_digest,
        "next": checkpoint.checkpoint_digest,
        "action": None if action is None else action.action_digest,
        "history_event_ref": history_event_ref,
        "reason": reason,
    }
    return ControllerTurnResult(
        checkpoint=checkpoint,
        receipt=ControllerTurnReceipt(
            receipt_id=f"controller-turn-{_digest(payload)[:24]}",
            outcome=outcome,
            previous_checkpoint_digest=previous.checkpoint_digest,
            next_checkpoint_digest=checkpoint.checkpoint_digest,
            action_digest=(
                None if action is None else action.action_digest
            ),
            responds_to_refs=(
                () if action is None else action.responds_to_refs
            ),
            adopted_advice_refs=(
                () if action is None else action.adopted_advice_refs
            ),
            rejected_advice_refs=(
                () if action is None else action.rejected_advice_refs
            ),
            changed_node_ref=(
                None if nested is None else nested.changed_node_ref
            ),
            invalidated_node_refs=(
                ()
                if nested is None
                else nested.invalidated_node_refs
            ),
            revalidation_node_refs=(
                ()
                if nested is None
                else nested.revalidation_node_refs
            ),
            history_event_ref=history_event_ref,
            reason=reason,
        ),
        transition=nested,
        phase_transition=phase_transition,
        phase_gate=phase_gate,
        backward_revision=backward_revision,
    )
