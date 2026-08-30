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
    DESIGN_PHASES,
    DesignPhase,
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
    next_design_phase,
    require_current_phase_gate,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DesignObligation,
    ObligationCondition,
    ObligationStatus,
    require_logical_ref,
)
from archflow.control.convergence import (
    StageConvergenceOutcome,
    StageConvergenceReceipt,
    StageTransitionKind,
)
from archflow.control.baseline import (
    StageBaselineCoverageReceipt,
    StageBaselineSourceSet,
    StageBaselineStatus,
    baseline_level_for_design_phase,
    compile_stage_baseline_coverage,
)
from archflow.control.requirements import StageRequirementProfile
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.control.profile import StageRequirementProfileBinding
from archflow.control.stage_subjects import StageSubjectInventory
from archflow.runtime.component_index import ComponentIndex
from archflow.runtime.stage_subject_inventory import (
    compile_stage_subject_inventory,
)
from archflow.state.spatial import SpatialOptionProposal
from archflow.validation.contracts import CheckReceiptEnvelope


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
    REOPENED_REFS_CLOSED = "reopened_refs_closed"
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


def _record_ref_payload(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_ref_from_payload(
    value: object,
    *,
    field: str,
) -> ProjectRecordRef:
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "relative_path",
        "sha256",
        "media_type",
    }:
        raise DesignControllerError(f"{field} schema drifted")
    try:
        return ProjectRecordRef(
            project_id=value["project_id"],
            relative_path=value["relative_path"],
            sha256=value["sha256"],
            media_type=value["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise DesignControllerError(f"{field} is invalid") from exc


@dataclass(frozen=True, slots=True)
class StageExitArchiveBundle:
    """Exact P036 records required to authorize one durable stage exit."""

    predecessor_checkpoint_ref: ProjectRecordRef
    profile_binding_ref: ProjectRecordRef
    profile_ref: ProjectRecordRef
    stage_subject_inventory_ref: ProjectRecordRef
    component_proposal_ref: ProjectRecordRef
    component_index_ref: ProjectRecordRef
    closure_ref: ProjectRecordRef
    baseline_sources_ref: ProjectRecordRef
    baseline_coverage_ref: ProjectRecordRef
    check_receipt_refs: tuple[ProjectRecordRef, ...]
    requirement_basis_refs: tuple[ProjectRecordRef, ...] = ()

    SCHEMA = "StageExitArchiveBundle@3"
    LEGACY_SCHEMA = "StageExitArchiveBundle@2"

    def __post_init__(self) -> None:
        for field in (
            "predecessor_checkpoint_ref",
            "profile_binding_ref",
            "profile_ref",
            "stage_subject_inventory_ref",
            "component_proposal_ref",
            "component_index_ref",
            "closure_ref",
            "baseline_sources_ref",
            "baseline_coverage_ref",
        ):
            if not isinstance(getattr(self, field), ProjectRecordRef):
                raise TypeError(f"{field} must be a ProjectRecordRef")
        if (
            not isinstance(self.check_receipt_refs, tuple)
            or not self.check_receipt_refs
            or any(
                not isinstance(item, ProjectRecordRef)
                for item in self.check_receipt_refs
            )
        ):
            raise TypeError(
                "check_receipt_refs must contain ProjectRecordRef values"
            )
        if not isinstance(self.requirement_basis_refs, tuple) or any(
            not isinstance(item, ProjectRecordRef)
            for item in self.requirement_basis_refs
        ):
            raise TypeError(
                "requirement_basis_refs must contain ProjectRecordRef values"
            )
        all_refs = (
            self.predecessor_checkpoint_ref,
            self.profile_binding_ref,
            self.profile_ref,
            self.stage_subject_inventory_ref,
            self.component_proposal_ref,
            self.component_index_ref,
            self.closure_ref,
            self.baseline_sources_ref,
            self.baseline_coverage_ref,
            *self.check_receipt_refs,
            *self.requirement_basis_refs,
        )
        project_ids = {item.project_id for item in all_refs}
        if len(project_ids) != 1:
            raise DesignControllerError(
                "stage-exit archive refs cross project identity"
            )
        identities = tuple(
            (item.uri, item.sha256, item.media_type)
            for item in self.check_receipt_refs
        )
        if len(identities) != len(set(identities)):
            raise DesignControllerError(
                "check_receipt_refs contain duplicates"
            )
        object.__setattr__(
            self,
            "check_receipt_refs",
            tuple(
                sorted(
                    self.check_receipt_refs,
                    key=lambda item: (
                        item.uri,
                        item.sha256,
                        item.media_type,
                    ),
                )
            ),
        )
        basis_identities = tuple(
            (item.uri, item.sha256, item.media_type)
            for item in self.requirement_basis_refs
        )
        if len(basis_identities) != len(set(basis_identities)):
            raise DesignControllerError(
                "requirement_basis_refs contain duplicates"
            )
        object.__setattr__(
            self,
            "requirement_basis_refs",
            tuple(
                sorted(
                    self.requirement_basis_refs,
                    key=lambda item: (
                        item.uri,
                        item.sha256,
                        item.media_type,
                    ),
                )
            ),
        )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_checkpoint_ref": _record_ref_payload(
                self.predecessor_checkpoint_ref
            ),
            "profile_binding_ref": _record_ref_payload(
                self.profile_binding_ref
            ),
            "profile_ref": _record_ref_payload(self.profile_ref),
            "stage_subject_inventory_ref": _record_ref_payload(
                self.stage_subject_inventory_ref
            ),
            "component_proposal_ref": _record_ref_payload(
                self.component_proposal_ref
            ),
            "component_index_ref": _record_ref_payload(
                self.component_index_ref
            ),
            "closure_ref": _record_ref_payload(self.closure_ref),
            "baseline_sources_ref": _record_ref_payload(
                self.baseline_sources_ref
            ),
            "baseline_coverage_ref": _record_ref_payload(
                self.baseline_coverage_ref
            ),
            "check_receipt_refs": [
                _record_ref_payload(item)
                for item in self.check_receipt_refs
            ],
            "requirement_basis_refs": [
                _record_ref_payload(item)
                for item in self.requirement_basis_refs
            ],
        }

    @property
    def bundle_digest(self) -> str:
        return _digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "bundle_digest": self.bundle_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageExitArchiveBundle":
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "predecessor_checkpoint_ref",
            "profile_binding_ref",
            "profile_ref",
            "stage_subject_inventory_ref",
            "component_proposal_ref",
            "component_index_ref",
            "closure_ref",
            "baseline_sources_ref",
            "baseline_coverage_ref",
            "check_receipt_refs",
            "requirement_basis_refs",
            "bundle_digest",
        }:
            raise DesignControllerError(
                "stage-exit archive bundle schema drifted"
            )
        if value.get("schema") != cls.SCHEMA:
            raise DesignControllerError(
                "unsupported stage-exit archive bundle schema"
            )
        raw_checks = value.get("check_receipt_refs")
        if not isinstance(raw_checks, list):
            raise TypeError("check_receipt_refs must be a list")
        raw_basis = value.get("requirement_basis_refs")
        if not isinstance(raw_basis, list):
            raise TypeError("requirement_basis_refs must be a list")
        bundle = cls(
            predecessor_checkpoint_ref=_record_ref_from_payload(
                value.get("predecessor_checkpoint_ref"),
                field="predecessor_checkpoint_ref",
            ),
            profile_binding_ref=_record_ref_from_payload(
                value.get("profile_binding_ref"),
                field="profile_binding_ref",
            ),
            profile_ref=_record_ref_from_payload(
                value.get("profile_ref"),
                field="profile_ref",
            ),
            stage_subject_inventory_ref=_record_ref_from_payload(
                value.get("stage_subject_inventory_ref"),
                field="stage_subject_inventory_ref",
            ),
            component_proposal_ref=_record_ref_from_payload(
                value.get("component_proposal_ref"),
                field="component_proposal_ref",
            ),
            component_index_ref=_record_ref_from_payload(
                value.get("component_index_ref"),
                field="component_index_ref",
            ),
            closure_ref=_record_ref_from_payload(
                value.get("closure_ref"),
                field="closure_ref",
            ),
            baseline_sources_ref=_record_ref_from_payload(
                value.get("baseline_sources_ref"),
                field="baseline_sources_ref",
            ),
            baseline_coverage_ref=_record_ref_from_payload(
                value.get("baseline_coverage_ref"),
                field="baseline_coverage_ref",
            ),
            check_receipt_refs=tuple(
                _record_ref_from_payload(item, field="check_receipt_ref")
                for item in raw_checks
            ),
            requirement_basis_refs=tuple(
                _record_ref_from_payload(
                    item,
                    field="requirement_basis_ref",
                )
                for item in raw_basis
            ),
        )
        if value.get("bundle_digest") != bundle.bundle_digest:
            raise DesignControllerError(
                "stage-exit archive bundle digest changed"
            )
        return bundle


@dataclass(frozen=True, slots=True)
class _LegacyStageExitArchiveBundle:
    """Strict read-only parser for pre-inventory stage-exit bundles."""

    predecessor_checkpoint_ref: ProjectRecordRef
    profile_binding_ref: ProjectRecordRef
    profile_ref: ProjectRecordRef
    closure_ref: ProjectRecordRef
    baseline_sources_ref: ProjectRecordRef
    baseline_coverage_ref: ProjectRecordRef
    check_receipt_refs: tuple[ProjectRecordRef, ...]
    requirement_basis_refs: tuple[ProjectRecordRef, ...] = ()

    SCHEMA = StageExitArchiveBundle.LEGACY_SCHEMA

    def __post_init__(self) -> None:
        fixed_refs = (
            self.predecessor_checkpoint_ref,
            self.profile_binding_ref,
            self.profile_ref,
            self.closure_ref,
            self.baseline_sources_ref,
            self.baseline_coverage_ref,
        )
        if any(not isinstance(item, ProjectRecordRef) for item in fixed_refs):
            raise TypeError(
                "legacy stage-exit bundle fields must be ProjectRecordRef"
            )
        if (
            not isinstance(self.check_receipt_refs, tuple)
            or not self.check_receipt_refs
            or any(
                not isinstance(item, ProjectRecordRef)
                for item in self.check_receipt_refs
            )
        ):
            raise TypeError(
                "check_receipt_refs must contain ProjectRecordRef values"
            )
        if not isinstance(self.requirement_basis_refs, tuple) or any(
            not isinstance(item, ProjectRecordRef)
            for item in self.requirement_basis_refs
        ):
            raise TypeError(
                "requirement_basis_refs must contain ProjectRecordRef values"
            )
        all_refs = (
            *fixed_refs,
            *self.check_receipt_refs,
            *self.requirement_basis_refs,
        )
        if len({item.project_id for item in all_refs}) != 1:
            raise DesignControllerError(
                "legacy stage-exit archive refs cross project identity"
            )
        for field in ("check_receipt_refs", "requirement_basis_refs"):
            values = getattr(self, field)
            identities = tuple(
                (item.uri, item.sha256, item.media_type)
                for item in values
            )
            if len(identities) != len(set(identities)):
                raise DesignControllerError(
                    f"legacy {field} contain duplicates"
                )
            object.__setattr__(
                self,
                field,
                tuple(
                    sorted(
                        values,
                        key=lambda item: (
                            item.uri,
                            item.sha256,
                            item.media_type,
                        ),
                    )
                ),
            )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor_checkpoint_ref": _record_ref_payload(
                self.predecessor_checkpoint_ref
            ),
            "profile_binding_ref": _record_ref_payload(
                self.profile_binding_ref
            ),
            "profile_ref": _record_ref_payload(self.profile_ref),
            "closure_ref": _record_ref_payload(self.closure_ref),
            "baseline_sources_ref": _record_ref_payload(
                self.baseline_sources_ref
            ),
            "baseline_coverage_ref": _record_ref_payload(
                self.baseline_coverage_ref
            ),
            "check_receipt_refs": [
                _record_ref_payload(item)
                for item in self.check_receipt_refs
            ],
            "requirement_basis_refs": [
                _record_ref_payload(item)
                for item in self.requirement_basis_refs
            ],
        }

    @property
    def bundle_digest(self) -> str:
        return _digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "bundle_digest": self.bundle_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "_LegacyStageExitArchiveBundle":
        expected = {
            "schema",
            "predecessor_checkpoint_ref",
            "profile_binding_ref",
            "profile_ref",
            "closure_ref",
            "baseline_sources_ref",
            "baseline_coverage_ref",
            "check_receipt_refs",
            "requirement_basis_refs",
            "bundle_digest",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or value.get("schema") != cls.SCHEMA
        ):
            raise DesignControllerError(
                "legacy stage-exit archive bundle schema drifted"
            )
        raw_checks = value.get("check_receipt_refs")
        raw_basis = value.get("requirement_basis_refs")
        if not isinstance(raw_checks, list):
            raise TypeError("check_receipt_refs must be a list")
        if not isinstance(raw_basis, list):
            raise TypeError("requirement_basis_refs must be a list")
        bundle = cls(
            predecessor_checkpoint_ref=_record_ref_from_payload(
                value.get("predecessor_checkpoint_ref"),
                field="predecessor_checkpoint_ref",
            ),
            profile_binding_ref=_record_ref_from_payload(
                value.get("profile_binding_ref"),
                field="profile_binding_ref",
            ),
            profile_ref=_record_ref_from_payload(
                value.get("profile_ref"),
                field="profile_ref",
            ),
            closure_ref=_record_ref_from_payload(
                value.get("closure_ref"),
                field="closure_ref",
            ),
            baseline_sources_ref=_record_ref_from_payload(
                value.get("baseline_sources_ref"),
                field="baseline_sources_ref",
            ),
            baseline_coverage_ref=_record_ref_from_payload(
                value.get("baseline_coverage_ref"),
                field="baseline_coverage_ref",
            ),
            check_receipt_refs=tuple(
                _record_ref_from_payload(item, field="check_receipt_ref")
                for item in raw_checks
            ),
            requirement_basis_refs=tuple(
                _record_ref_from_payload(
                    item,
                    field="requirement_basis_ref",
                )
                for item in raw_basis
            ),
        )
        if value.get("bundle_digest") != bundle.bundle_digest:
            raise DesignControllerError(
                "legacy stage-exit archive bundle digest changed"
            )
        return bundle


class ProjectControllerArchiveAdapter:
    """Bind P018 events and derived checkpoints to one P036 branch area."""

    EVENT_SCHEMA = DesignEvent.SCHEMA
    LEGACY_CHECKPOINT_SCHEMA = "DesignControllerCheckpointRecord@1"
    PROOF_CHECKPOINT_SCHEMA = "DesignControllerCheckpointRecord@2"
    CHECKPOINT_SCHEMA = "DesignControllerCheckpointRecord@3"
    LEGACY_STAGE_EXIT_PROOF_SCHEMA = "StageExitArchiveProof@2"
    STAGE_EXIT_PROOF_SCHEMA = "StageExitArchiveProof@3"

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
        *,
        stage_exit_bundle: StageExitArchiveBundle | None = None,
    ) -> ProjectRecordRef:
        if not isinstance(checkpoint, DesignControllerCheckpoint):
            raise TypeError(
                "checkpoint must be a DesignControllerCheckpoint"
            )
        if checkpoint.tree.branch != self.branch:
            raise DesignControllerError(
                "checkpoint belongs to another branch"
            )
        if stage_exit_bundle is not None and not isinstance(
            stage_exit_bundle,
            StageExitArchiveBundle,
        ):
            raise TypeError(
                "stage_exit_bundle must be a StageExitArchiveBundle"
            )

        current = self._latest_checkpoint_or_none()
        if (
            current is not None
            and current.checkpoint.checkpoint_digest
            == checkpoint.checkpoint_digest
        ):
            if stage_exit_bundle is not None:
                archived = self._stage_exit_proof_from_record(
                    current.record_ref
                )
                if archived is None or archived["bundle"] != (
                    stage_exit_bundle.to_dict()
                ):
                    raise DesignControllerError(
                        "idempotent checkpoint stage-exit proof changed"
                    )
            return current.record_ref

        phase_advanced = False
        predecessor: DesignControllerCheckpoint | None = None
        stage_exit_anchor_ref: ProjectRecordRef | None = None
        previous_checkpoint_ref: ProjectRecordRef | None = None
        previous_checkpoint_digest: str | None = None
        if current is None:
            if stage_exit_bundle is not None:
                predecessor = self._load_stage_exit_predecessor(
                    stage_exit_bundle,
                    require_writable_anchor=True,
                )
                phase_advanced = True
            elif DESIGN_PHASES.index(
                checkpoint.maturity.phase
            ) > DESIGN_PHASES.index(DesignPhase.SCHEMATIC_DESIGN):
                raise DesignControllerError(
                    "first durable checkpoint exceeds bootstrap maturity; "
                    "phase-advanced checkpoint requires a P036 stage-exit bundle"
                )
        else:
            if stage_exit_bundle is not None:
                raise DesignControllerError(
                    "stage-exit bundle belongs on the first checkpoint of a "
                    "new branch epoch"
                )
            if (
                current.checkpoint.maturity.phase
                is not checkpoint.maturity.phase
            ):
                raise DesignControllerError(
                    "phase-changing checkpoint must increment branch epoch"
                )
            if (
                checkpoint.history_event_refs[
                    : len(current.checkpoint.history_event_refs)
                ]
                != current.checkpoint.history_event_refs
            ):
                raise DesignControllerError(
                    "checkpoint history does not extend the exact predecessor"
                )
            current_key = (
                len(current.checkpoint.history_event_refs),
                current.checkpoint.iteration,
            )
            checkpoint_key = (
                len(checkpoint.history_event_refs),
                checkpoint.iteration,
            )
            if checkpoint_key <= current_key:
                raise DesignControllerError(
                    "checkpoint lineage does not advance"
                )
            current_payload = self._repository.load_json(
                current.record_ref
            )
            if current_payload.get("schema") != self.CHECKPOINT_SCHEMA:
                raise DesignControllerError(
                    "legacy controller checkpoint is read-only"
                )
            stage_exit_anchor_ref = self._checkpoint_anchor_ref(
                current.record_ref,
                current_payload,
            )
            if (
                self._phase_requires_stage_exit_anchor(
                    checkpoint.maturity.phase
                )
                and stage_exit_anchor_ref is None
            ):
                raise DesignControllerError(
                    "high-stage checkpoint has no stage-exit proof anchor"
                )
            previous_checkpoint_ref = current.record_ref
            previous_checkpoint_digest = (
                current.checkpoint.checkpoint_digest
            )
        if phase_advanced and stage_exit_bundle is None:
            raise DesignControllerError(
                "phase-advanced checkpoint requires a P036 stage-exit bundle"
            )
        if not phase_advanced and stage_exit_bundle is not None:
            raise DesignControllerError(
                "stage-exit bundle is valid only for a forward phase change"
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
        stage_exit_proof: dict[str, object] | None = None
        if phase_advanced:
            assert predecessor is not None
            assert stage_exit_bundle is not None
            stage_exit_proof = self._admit_stage_exit(
                predecessor,
                checkpoint,
                stage_exit_bundle,
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
            "stage_exit_proof": stage_exit_proof,
            "stage_exit_anchor_ref": (
                None
                if stage_exit_anchor_ref is None
                else _record_ref_payload(stage_exit_anchor_ref)
            ),
            "previous_checkpoint_ref": (
                None
                if previous_checkpoint_ref is None
                else _record_ref_payload(previous_checkpoint_ref)
            ),
            "previous_checkpoint_digest": previous_checkpoint_digest,
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
        return self._load_checkpoint(ref, ancestry=())

    def _load_checkpoint(
        self,
        ref: ProjectRecordRef,
        *,
        ancestry: tuple[tuple[str, str], ...],
    ) -> DurableControllerResume:
        self._require_branch_record(ref)
        identity = (ref.uri, ref.sha256)
        if identity in ancestry:
            raise DesignControllerError(
                "controller checkpoint lineage contains a cycle"
            )
        ancestry = (*ancestry, identity)
        payload = self._repository.load_json(ref)
        base_fields = {
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
        schema = payload.get("schema")
        if schema == self.LEGACY_CHECKPOINT_SCHEMA:
            expected_fields = base_fields
        elif schema == self.PROOF_CHECKPOINT_SCHEMA:
            expected_fields = {*base_fields, "stage_exit_proof"}
        elif schema == self.CHECKPOINT_SCHEMA:
            expected_fields = {
                *base_fields,
                "stage_exit_proof",
                "stage_exit_anchor_ref",
                "previous_checkpoint_ref",
                "previous_checkpoint_digest",
            }
        else:
            expected_fields = set()
        if (
            schema
            not in {
                self.LEGACY_CHECKPOINT_SCHEMA,
                self.PROOF_CHECKPOINT_SCHEMA,
                self.CHECKPOINT_SCHEMA,
            }
            or set(payload) != expected_fields
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
        if schema == self.PROOF_CHECKPOINT_SCHEMA:
            stage_exit_proof = payload["stage_exit_proof"]
            if stage_exit_proof is not None:
                self._verify_archived_stage_exit_proof(
                    checkpoint,
                    stage_exit_proof,
                )
            elif self._phase_requires_stage_exit_anchor(
                checkpoint.maturity.phase
            ):
                raise DesignControllerError(
                    "high-stage checkpoint has no stage-exit proof anchor"
                )
        elif schema == self.CHECKPOINT_SCHEMA:
            self._verify_checkpoint_lineage(
                ref,
                payload,
                checkpoint,
                ancestry=ancestry,
            )
        elif self._phase_requires_stage_exit_anchor(
            checkpoint.maturity.phase
        ):
            raise DesignControllerError(
                "legacy high-stage checkpoint has no stage-exit proof anchor"
            )
        return DurableControllerResume(
            record_ref=ref,
            checkpoint=checkpoint,
            event_chain=events,
        )

    def _verify_checkpoint_lineage(
        self,
        ref: ProjectRecordRef,
        payload: Mapping[str, Any],
        checkpoint: DesignControllerCheckpoint,
        *,
        ancestry: tuple[tuple[str, str], ...],
    ) -> None:
        proof = payload["stage_exit_proof"]
        raw_anchor = payload["stage_exit_anchor_ref"]
        raw_previous = payload["previous_checkpoint_ref"]
        raw_previous_digest = payload["previous_checkpoint_digest"]
        anchor_ref = (
            None
            if raw_anchor is None
            else _record_ref_from_payload(
                raw_anchor,
                field="stage_exit_anchor_ref",
            )
        )
        previous_ref = (
            None
            if raw_previous is None
            else _record_ref_from_payload(
                raw_previous,
                field="previous_checkpoint_ref",
            )
        )

        if proof is not None:
            if (
                anchor_ref is not None
                or previous_ref is not None
                or raw_previous_digest is not None
            ):
                raise DesignControllerError(
                    "stage-exit proof anchor cannot name an in-epoch predecessor"
                )
            self._verify_archived_stage_exit_proof(checkpoint, proof)
            return

        if previous_ref is None:
            if anchor_ref is not None or raw_previous_digest is not None:
                raise DesignControllerError(
                    "first checkpoint lineage fields are inconsistent"
                )
            if self._phase_requires_stage_exit_anchor(
                checkpoint.maturity.phase
            ):
                raise DesignControllerError(
                    "high-stage checkpoint has no stage-exit proof anchor"
                )
            return

        previous_digest = _sha256(
            raw_previous_digest,
            "previous_checkpoint_digest",
        )
        previous = self._load_checkpoint(
            previous_ref,
            ancestry=ancestry,
        )
        if previous.checkpoint.checkpoint_digest != previous_digest:
            raise DesignControllerError(
                "previous checkpoint digest disagrees with exact ref"
            )
        if (
            previous.checkpoint.tree.branch != checkpoint.tree.branch
            or previous.checkpoint.maturity.phase
            is not checkpoint.maturity.phase
        ):
            raise DesignControllerError(
                "checkpoint lineage crosses branch, epoch, run, or phase"
            )
        previous_history = previous.checkpoint.history_event_refs
        if (
            checkpoint.history_event_refs[: len(previous_history)]
            != previous_history
        ):
            raise DesignControllerError(
                "checkpoint history does not extend the exact predecessor"
            )
        previous_key = (
            len(previous_history),
            previous.checkpoint.iteration,
        )
        current_key = (
            len(checkpoint.history_event_refs),
            checkpoint.iteration,
        )
        if current_key <= previous_key:
            raise DesignControllerError(
                "checkpoint lineage does not advance"
            )

        previous_payload = self._repository.load_json(previous_ref)
        expected_anchor = self._checkpoint_anchor_ref(
            previous_ref,
            previous_payload,
        )
        if anchor_ref != expected_anchor:
            raise DesignControllerError(
                "checkpoint changed or dropped its stage-exit proof anchor"
            )
        if self._phase_requires_stage_exit_anchor(
            checkpoint.maturity.phase
        ) and anchor_ref is None:
            raise DesignControllerError(
                "high-stage checkpoint has no stage-exit proof anchor"
            )
        if anchor_ref is None:
            return

        anchor = self._load_checkpoint(
            anchor_ref,
            ancestry=ancestry,
        )
        anchor_payload = self._repository.load_json(anchor_ref)
        anchor_proof = anchor_payload.get("stage_exit_proof")
        if (
            anchor_payload.get("schema") != self.CHECKPOINT_SCHEMA
            or not isinstance(anchor_proof, Mapping)
            or anchor_proof.get("schema")
            != self.STAGE_EXIT_PROOF_SCHEMA
            or anchor.checkpoint.tree.branch != checkpoint.tree.branch
            or anchor.checkpoint.maturity.phase
            is not checkpoint.maturity.phase
            or checkpoint.history_event_refs[
                : len(anchor.checkpoint.history_event_refs)
            ]
            != anchor.checkpoint.history_event_refs
        ):
            raise DesignControllerError(
                "stage-exit anchor is not the exact in-epoch proof checkpoint"
            )

    @staticmethod
    def _phase_requires_stage_exit_anchor(phase: DesignPhase) -> bool:
        if not isinstance(phase, DesignPhase):
            raise TypeError("phase must be DesignPhase")
        return DESIGN_PHASES.index(phase) > DESIGN_PHASES.index(
            DesignPhase.SCHEMATIC_DESIGN
        )

    def _checkpoint_anchor_ref(
        self,
        ref: ProjectRecordRef,
        payload: Mapping[str, Any],
    ) -> ProjectRecordRef | None:
        schema = payload.get("schema")
        if schema in {
            self.LEGACY_CHECKPOINT_SCHEMA,
            self.PROOF_CHECKPOINT_SCHEMA,
        }:
            return None
        if schema != self.CHECKPOINT_SCHEMA:
            raise DesignControllerError(
                "controller checkpoint record schema drifted"
            )
        proof = payload.get("stage_exit_proof")
        if proof is not None:
            if (
                isinstance(proof, Mapping)
                and proof.get("schema") == self.STAGE_EXIT_PROOF_SCHEMA
            ):
                return ref
            return None
        raw_anchor = payload.get("stage_exit_anchor_ref")
        if raw_anchor is None:
            return None
        return _record_ref_from_payload(
            raw_anchor,
            field="stage_exit_anchor_ref",
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
            if payload.get("schema") not in {
                self.LEGACY_CHECKPOINT_SCHEMA,
                self.PROOF_CHECKPOINT_SCHEMA,
                self.CHECKPOINT_SCHEMA,
            }:
                continue
            if (
                payload.get("project_id")
                != self.branch.run.project_id
                or payload.get("run_id")
                != self.branch.run.run_id
                or payload.get("branch_id")
                != self.branch.branch_id
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
            if payload.get("run_base") != _project_version_payload(
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
        if len(latest) != 1:
            raise DesignControllerError(
                "branch has ambiguous latest checkpoint lineage"
            )
        selected = latest[0]
        selected_payload = self._repository.load_json(
            selected.record_ref
        )
        if selected_payload.get("schema") == self.CHECKPOINT_SCHEMA:
            raw_previous = selected_payload.get("previous_checkpoint_ref")
            prior = tuple(
                (key, resumed)
                for key, resumed in candidates
                if key < latest_key
            )
            if raw_previous is None:
                if prior:
                    raise DesignControllerError(
                        "latest checkpoint omits its exact predecessor"
                    )
            else:
                previous_ref = _record_ref_from_payload(
                    raw_previous,
                    field="previous_checkpoint_ref",
                )
                if not prior:
                    raise DesignControllerError(
                        "latest checkpoint names a nonexistent predecessor"
                    )
                prior_key = max(key for key, _resumed in prior)
                immediate = tuple(
                    resumed
                    for key, resumed in prior
                    if key == prior_key
                )
                if (
                    len(immediate) != 1
                    or immediate[0].record_ref != previous_ref
                ):
                    raise DesignControllerError(
                        "latest checkpoint does not name the exact predecessor"
                    )
        return selected

    def _latest_checkpoint_or_none(self) -> DurableControllerResume | None:
        try:
            return self.load_latest_checkpoint()
        except DesignControllerError as exc:
            if str(exc) == "branch has no durable controller checkpoint":
                return None
            raise

    def _load_exact_branch_json(
        self,
        ref: ProjectRecordRef,
        *,
        field: str,
    ) -> dict[str, Any]:
        self._require_branch_record(ref)
        if ref.media_type != "application/json":
            raise DesignControllerError(
                f"{field} is not a JSON project record"
            )
        try:
            return self._repository.load_json(ref)
        except Exception as exc:
            raise DesignControllerError(
                f"{field} P036 readback failed"
            ) from exc

    def _load_same_project_json(
        self,
        ref: ProjectRecordRef,
        *,
        field: str,
    ) -> dict[str, Any]:
        if not isinstance(ref, ProjectRecordRef):
            raise TypeError(f"{field} must be a ProjectRecordRef")
        if ref.project_id != self.branch.run.project_id:
            raise DesignControllerError(
                f"{field} belongs to another project"
            )
        if ref.media_type != "application/json":
            raise DesignControllerError(
                f"{field} is not a JSON project record"
            )
        try:
            return self._repository.load_json(ref)
        except Exception as exc:
            raise DesignControllerError(
                f"{field} P036 readback failed"
            ) from exc

    def _read_stage_exit_bundle(
        self,
        bundle: StageExitArchiveBundle,
        *,
        expected_branch: BranchRef,
    ) -> tuple[
        StageRequirementProfileBinding,
        StageRequirementProfile,
        StageSubjectInventory,
        CompositeStageClosureReceipt,
        tuple[CheckReceiptEnvelope, ...],
        StageBaselineSourceSet,
        StageBaselineCoverageReceipt,
    ]:
        if not isinstance(bundle, StageExitArchiveBundle):
            raise TypeError(
                "stage_exit_bundle must be a StageExitArchiveBundle"
            )
        try:
            binding = StageRequirementProfileBinding.from_dict(
                self._load_exact_branch_json(
                    bundle.profile_binding_ref,
                    field="profile_binding_ref",
                )
            )
            profile = StageRequirementProfile.from_dict(
                self._load_exact_branch_json(
                    bundle.profile_ref,
                    field="profile_ref",
                )
            )
            subject_inventory = StageSubjectInventory.from_dict(
                self._load_exact_branch_json(
                    bundle.stage_subject_inventory_ref,
                    field="stage_subject_inventory_ref",
                )
            )
            component_proposal = SpatialOptionProposal.from_dict(
                self._load_exact_branch_json(
                    bundle.component_proposal_ref,
                    field="component_proposal_ref",
                )
            )
            component_index = ComponentIndex.from_dict(
                self._load_exact_branch_json(
                    bundle.component_index_ref,
                    field="component_index_ref",
                )
            )
            closure = CompositeStageClosureReceipt.from_dict(
                self._load_exact_branch_json(
                    bundle.closure_ref,
                    field="closure_ref",
                )
            )
            receipts = tuple(
                CheckReceiptEnvelope.from_dict(
                    self._load_exact_branch_json(
                        ref,
                        field="check_receipt_ref",
                    )
                )
                for ref in bundle.check_receipt_refs
            )
            baseline_sources = StageBaselineSourceSet.from_dict(
                self._load_exact_branch_json(
                    bundle.baseline_sources_ref,
                    field="baseline_sources_ref",
                )
            )
            baseline_coverage = StageBaselineCoverageReceipt.from_dict(
                self._load_exact_branch_json(
                    bundle.baseline_coverage_ref,
                    field="baseline_coverage_ref",
                )
            )
        except DesignControllerError:
            raise
        except (TypeError, ValueError) as exc:
            raise DesignControllerError(
                "stage-exit P036 record schema is invalid"
            ) from exc

        if any(
            item != expected_branch
            for item in (
                binding.branch,
                profile.branch,
                subject_inventory.branch,
                closure.branch,
                baseline_coverage.branch,
                *(receipt.branch for receipt in receipts),
                *(
                    source.profile.branch
                    for source in baseline_sources.component_lineage
                ),
                *(
                    source.profile.branch
                    for source in baseline_sources.spatial_layout
                ),
                *(
                    source.profile.branch
                    for source in baseline_sources.material_binding
                ),
                *(
                    source.profile.branch
                    for source in baseline_sources.cad_readback
                ),
            )
        ):
            raise DesignControllerError(
                "stage-exit proof is cross-branch, cross-run, or stale epoch"
            )
        if (
            binding.profile_ref.project_id
            != bundle.profile_ref.project_id
            or binding.profile_ref.relative_path
            != bundle.profile_ref.relative_path
            or binding.profile_ref.media_type
            != bundle.profile_ref.media_type
        ):
            raise DesignControllerError(
                "profile binding does not identify the exact P036 profile path"
            )
        if (
            binding.stage_subject_inventory_ref
            != bundle.stage_subject_inventory_ref
            or binding.stage_subject_inventory_digest
            != subject_inventory.inventory_digest
            or subject_inventory.component_proposal_ref
            != bundle.component_proposal_ref
            or subject_inventory.component_index_ref
            != bundle.component_index_ref
        ):
            raise DesignControllerError(
                "profile binding does not identify the exact stage subject inventory"
            )
        try:
            rebuilt_inventory = compile_stage_subject_inventory(
                inventory_id=subject_inventory.inventory_id,
                branch=subject_inventory.branch,
                stage_id=subject_inventory.stage_id,
                stage_subject_ref=subject_inventory.stage_subject_ref,
                stage_subject_digest=(
                    subject_inventory.stage_subject_digest
                ),
                baseline_level=subject_inventory.baseline_level,
                component_proposal=component_proposal,
                component_proposal_ref=bundle.component_proposal_ref,
                component_index=component_index,
                component_index_ref=bundle.component_index_ref,
                role_obligations={
                    entry.component_id: entry.role_obligations
                    for entry in subject_inventory.entries
                },
            )
        except (TypeError, ValueError) as exc:
            raise DesignControllerError(
                "stage subject inventory cannot be rebuilt from exact sources"
            ) from exc
        if rebuilt_inventory != subject_inventory:
            raise DesignControllerError(
                "stage subject inventory differs from exact proposal/index replay"
            )
        if (
            profile.profile_id != binding.profile_id
            or profile.profile_id != closure.profile_id
            or profile.profile_digest != binding.profile_digest
            or profile.profile_digest != closure.profile_digest
            or binding.profile_ref.sha256 != profile.profile_digest
        ):
            raise DesignControllerError(
                "stage-exit profile identity or digest disagrees"
            )
        if (
            profile.stage_id != binding.stage_id
            or profile.stage_id != closure.stage_id
            or profile.stage_subject_ref != binding.stage_subject_ref
            or profile.stage_subject_ref != closure.stage_subject_ref
            or profile.stage_id != subject_inventory.stage_id
            or profile.stage_subject_ref
            != subject_inventory.stage_subject_ref
            or binding.subject_digest != closure.subject_digest
            or binding.subject_digest
            != subject_inventory.stage_subject_digest
            or profile.predecessor_state_digest != closure.subject_digest
        ):
            raise DesignControllerError(
                "stage-exit stage or subject binding disagrees"
            )

        receipt_digests = tuple(
            sorted(item.receipt_digest for item in receipts)
        )
        if receipt_digests != closure.check_receipt_digests:
            raise DesignControllerError(
                "stage-exit check receipt digests disagree with closure"
            )
        recomputed = compile_composite_stage_closure(
            profile,
            subject_digest=closure.subject_digest,
            check_receipts=receipts,
        )
        if (
            recomputed != closure
            or closure.status is not StageClosureStatus.SATISFIED
            or closure.findings
        ):
            raise DesignControllerError(
                "stage-exit closure is not satisfied after P036 readback"
            )
        for authority_ref in binding.authority_refs:
            self._load_same_project_json(
                authority_ref,
                field="profile authority_ref",
            )
        profile_basis_uris = {
            ref
            for requirement in profile.requirements
            for ref in (
                *requirement.required_claim_refs,
                *requirement.required_applicability_refs,
                *requirement.required_adoption_refs,
                *requirement.required_source_refs,
                *requirement.required_authority_refs,
            )
        }
        inventory_basis_uris = {
            ref
            for entry in subject_inventory.entries
            for obligation in entry.role_obligations
            for ref in (
                *obligation.evidence_refs,
                *obligation.authority_refs,
            )
        }
        inventory_authority_uris = {
            ref
            for entry in subject_inventory.entries
            for obligation in entry.role_obligations
            for ref in obligation.authority_refs
        }
        binding_authority_uris = {
            ref.uri for ref in binding.authority_refs
        }
        if not inventory_authority_uris.issubset(
            binding_authority_uris
        ):
            raise DesignControllerError(
                "stage subject role obligations are not authorized by the "
                "exact profile binding"
            )
        required_basis_uris = tuple(
            sorted(profile_basis_uris | inventory_basis_uris)
        )
        supplied_basis_uris = tuple(
            sorted(ref.uri for ref in bundle.requirement_basis_refs)
        )
        if supplied_basis_uris != required_basis_uris:
            raise DesignControllerError(
                "stage-exit requirement basis refs are not exact P036 records"
            )
        for basis_ref in bundle.requirement_basis_refs:
            self._load_same_project_json(
                basis_ref,
                field="requirement basis_ref",
            )
        return (
            binding,
            profile,
            subject_inventory,
            closure,
            receipts,
            baseline_sources,
            baseline_coverage,
        )

    def _read_legacy_stage_exit_bundle(
        self,
        bundle: _LegacyStageExitArchiveBundle,
        *,
        expected_branch: BranchRef,
    ) -> tuple[
        StageRequirementProfileBinding,
        StageRequirementProfile,
        CompositeStageClosureReceipt,
        tuple[CheckReceiptEnvelope, ...],
        StageBaselineSourceSet,
        StageBaselineCoverageReceipt,
    ]:
        """Read and validate pre-inventory proof records without upgrading them."""

        try:
            binding = StageRequirementProfileBinding.from_dict(
                self._load_exact_branch_json(
                    bundle.profile_binding_ref,
                    field="profile_binding_ref",
                )
            )
            profile = StageRequirementProfile.from_dict(
                self._load_exact_branch_json(
                    bundle.profile_ref,
                    field="profile_ref",
                )
            )
            closure = CompositeStageClosureReceipt.from_dict(
                self._load_exact_branch_json(
                    bundle.closure_ref,
                    field="closure_ref",
                )
            )
            receipts = tuple(
                CheckReceiptEnvelope.from_dict(
                    self._load_exact_branch_json(
                        ref,
                        field="check_receipt_ref",
                    )
                )
                for ref in bundle.check_receipt_refs
            )
            baseline_sources = StageBaselineSourceSet.from_dict(
                self._load_exact_branch_json(
                    bundle.baseline_sources_ref,
                    field="baseline_sources_ref",
                )
            )
            baseline_coverage = StageBaselineCoverageReceipt.from_dict(
                self._load_exact_branch_json(
                    bundle.baseline_coverage_ref,
                    field="baseline_coverage_ref",
                )
            )
        except DesignControllerError:
            raise
        except (TypeError, ValueError) as exc:
            raise DesignControllerError(
                "legacy stage-exit P036 record schema is invalid"
            ) from exc

        if not binding.is_legacy_read_only:
            raise DesignControllerError(
                "legacy stage-exit proof requires a read-only profile binding"
            )
        if baseline_coverage.stage_subject_inventory_digest is not None:
            raise DesignControllerError(
                "legacy stage-exit proof cannot carry subject inventory coverage"
            )
        if any(
            item != expected_branch
            for item in (
                binding.branch,
                profile.branch,
                closure.branch,
                baseline_coverage.branch,
                *(receipt.branch for receipt in receipts),
                *(
                    source.profile.branch
                    for source in baseline_sources.component_lineage
                ),
                *(
                    source.profile.branch
                    for source in baseline_sources.spatial_layout
                ),
                *(
                    source.profile.branch
                    for source in baseline_sources.material_binding
                ),
                *(
                    source.profile.branch
                    for source in baseline_sources.cad_readback
                ),
            )
        ):
            raise DesignControllerError(
                "legacy stage-exit proof is cross-branch, cross-run, or stale epoch"
            )
        if (
            binding.profile_ref.project_id
            != bundle.profile_ref.project_id
            or binding.profile_ref.relative_path
            != bundle.profile_ref.relative_path
            or binding.profile_ref.media_type
            != bundle.profile_ref.media_type
        ):
            raise DesignControllerError(
                "legacy profile binding does not identify the exact P036 profile"
            )
        if (
            profile.profile_id != binding.profile_id
            or profile.profile_id != closure.profile_id
            or profile.profile_id != baseline_coverage.profile_id
            or profile.profile_digest != binding.profile_digest
            or profile.profile_digest != closure.profile_digest
            or profile.profile_digest != baseline_coverage.profile_digest
            or binding.profile_ref.sha256 != profile.profile_digest
            or profile.stage_id != binding.stage_id
            or profile.stage_id != closure.stage_id
            or profile.stage_id != baseline_coverage.stage_id
            or profile.stage_subject_ref != binding.stage_subject_ref
            or profile.stage_subject_ref != closure.stage_subject_ref
            or binding.subject_digest != closure.subject_digest
            or profile.predecessor_state_digest != closure.subject_digest
        ):
            raise DesignControllerError(
                "legacy stage-exit profile, stage, or subject binding disagrees"
            )
        receipt_digests = tuple(
            sorted(item.receipt_digest for item in receipts)
        )
        if receipt_digests != closure.check_receipt_digests:
            raise DesignControllerError(
                "legacy stage-exit check receipts disagree with closure"
            )
        recomputed = compile_composite_stage_closure(
            profile,
            subject_digest=closure.subject_digest,
            check_receipts=receipts,
        )
        if (
            recomputed != closure
            or closure.status is not StageClosureStatus.SATISFIED
            or closure.findings
            or baseline_coverage.status
            is not StageBaselineStatus.SATISFIED
        ):
            raise DesignControllerError(
                "legacy stage-exit closure or baseline is not satisfied"
            )
        for authority_ref in binding.authority_refs:
            self._load_same_project_json(
                authority_ref,
                field="legacy profile authority_ref",
            )
        required_basis_uris = tuple(
            sorted(
                {
                    ref
                    for requirement in profile.requirements
                    for ref in (
                        *requirement.required_claim_refs,
                        *requirement.required_applicability_refs,
                        *requirement.required_adoption_refs,
                        *requirement.required_source_refs,
                        *requirement.required_authority_refs,
                    )
                }
            )
        )
        supplied_basis_uris = tuple(
            sorted(ref.uri for ref in bundle.requirement_basis_refs)
        )
        if supplied_basis_uris != required_basis_uris:
            raise DesignControllerError(
                "legacy stage-exit requirement basis refs are not exact P036 records"
            )
        for basis_ref in bundle.requirement_basis_refs:
            self._load_same_project_json(
                basis_ref,
                field="legacy requirement basis_ref",
            )
        return (
            binding,
            profile,
            closure,
            receipts,
            baseline_sources,
            baseline_coverage,
        )

    def _admit_stage_exit(
        self,
        previous: DesignControllerCheckpoint,
        checkpoint: DesignControllerCheckpoint,
        bundle: StageExitArchiveBundle,
    ) -> dict[str, object]:
        (
            binding,
            profile,
            subject_inventory,
            closure,
            receipts,
            baseline_sources,
            baseline_coverage,
        ) = self._read_stage_exit_bundle(
            bundle,
            expected_branch=previous.tree.branch,
        )
        if (
            not _same_branch(previous.tree.branch, self.branch)
            or previous.tree.branch.epoch + 1 != self.branch.epoch
            or checkpoint.tree.branch != self.branch
            or checkpoint.maturity.phase
            is not next_design_phase(previous.maturity.phase)
        ):
            raise DesignControllerError(
                "stage-exit archive does not bind one exact forward phase"
            )
        if (
            len(checkpoint.history_event_refs)
            != len(previous.history_event_refs) + 1
            or checkpoint.history_event_refs[:-1]
            != previous.history_event_refs
        ):
            raise DesignControllerError(
                "phase-advanced checkpoint does not extend exact history"
            )
        target = previous.tree.node(previous.target_node_ref)
        current_digest = target.operational_state.state_digest
        deliverables = {
            item.ref: item for item in previous.maturity.deliverables
        }
        subject = deliverables.get(closure.stage_subject_ref)
        if subject is None:
            raise DesignControllerError(
                "stage-exit subject is not a prior maturity deliverable"
            )
        if (
            closure.stage_id != previous.maturity.phase.value
            or closure.subject_digest != current_digest
            or closure.subject_digest
            != previous.maturity.operational_state_digest
            or subject.base_state_digest != closure.subject_digest
            or profile.predecessor_state_digest != current_digest
        ):
            raise DesignControllerError(
                "stage-exit proof is stale against the archived predecessor"
            )
        if bundle.component_proposal_ref.uri not in {
            subject.artifact_ref,
            *subject.evidence_refs,
        }:
            raise DesignControllerError(
                "stage subject does not retain the exact component proposal "
                "record as predecessor evidence"
            )
        recomputed_baseline = compile_stage_baseline_coverage(
            profile,
            level=baseline_level_for_design_phase(
                previous.maturity.phase
            ),
            sources=baseline_sources,
            subject_digest=closure.subject_digest,
            subject_inventory=subject_inventory,
            check_receipts=receipts,
        )
        if (
            recomputed_baseline != baseline_coverage
            or baseline_coverage.status
            is not StageBaselineStatus.SATISFIED
            or baseline_coverage.stage_subject_inventory_digest
            != subject_inventory.inventory_digest
        ):
            raise DesignControllerError(
                "stage-exit baseline is not satisfied after P036 replay"
            )
        content = {
            "schema": self.STAGE_EXIT_PROOF_SCHEMA,
            "bundle": bundle.to_dict(),
            "previous_checkpoint_digest": previous.checkpoint_digest,
            "next_checkpoint_digest": checkpoint.checkpoint_digest,
            "stage_id": closure.stage_id,
            "stage_subject_ref": closure.stage_subject_ref,
            "subject_digest": closure.subject_digest,
            "profile_digest": profile.profile_digest,
            "profile_binding_digest": binding.binding_digest,
            "stage_subject_inventory_digest": (
                subject_inventory.inventory_digest
            ),
            "closure_digest": closure.receipt_digest,
            "baseline_sources_digest": (
                baseline_sources.source_set_digest
            ),
            "baseline_coverage_digest": (
                baseline_coverage.receipt_digest
            ),
            "check_receipt_digests": [
                item.receipt_digest
                for item in sorted(
                    receipts,
                    key=lambda item: item.receipt_digest,
                )
            ],
        }
        return {**content, "proof_digest": _digest(content)}

    def _parse_stage_exit_proof(
        self,
        value: object,
    ) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise DesignControllerError(
                "stage-exit archive proof schema drifted"
            )
        schema = value.get("schema")
        common_fields = {
            "schema",
            "bundle",
            "previous_checkpoint_digest",
            "next_checkpoint_digest",
            "stage_id",
            "stage_subject_ref",
            "subject_digest",
            "profile_digest",
            "profile_binding_digest",
            "closure_digest",
            "baseline_sources_digest",
            "baseline_coverage_digest",
            "check_receipt_digests",
            "proof_digest",
        }
        if schema == self.STAGE_EXIT_PROOF_SCHEMA:
            expected = common_fields | {
                "stage_subject_inventory_digest"
            }
            bundle: (
                StageExitArchiveBundle | _LegacyStageExitArchiveBundle
            ) = StageExitArchiveBundle.from_dict(value.get("bundle"))
        elif schema == self.LEGACY_STAGE_EXIT_PROOF_SCHEMA:
            expected = common_fields
            bundle = _LegacyStageExitArchiveBundle.from_dict(
                value.get("bundle")
            )
        else:
            raise DesignControllerError(
                "stage-exit archive proof schema drifted"
            )
        if set(value) != expected:
            raise DesignControllerError(
                "stage-exit archive proof schema drifted"
            )
        raw_checks = value.get("check_receipt_digests")
        if not isinstance(raw_checks, list):
            raise TypeError("check_receipt_digests must be a list")
        checks = tuple(
            sorted(
                _sha256(item, "check_receipt_digest")
                for item in raw_checks
            )
        )
        if not checks or len(checks) != len(set(checks)):
            raise DesignControllerError(
                "stage-exit check receipt digests are empty or duplicated"
            )
        content = {
            "schema": schema,
            "bundle": bundle.to_dict(),
            "previous_checkpoint_digest": _sha256(
                value.get("previous_checkpoint_digest"),
                "previous_checkpoint_digest",
            ),
            "next_checkpoint_digest": _sha256(
                value.get("next_checkpoint_digest"),
                "next_checkpoint_digest",
            ),
            "stage_id": _text(value.get("stage_id"), "stage_id"),
            "stage_subject_ref": value.get("stage_subject_ref"),
            "subject_digest": _sha256(
                value.get("subject_digest"),
                "subject_digest",
            ),
            "profile_digest": _sha256(
                value.get("profile_digest"),
                "profile_digest",
            ),
            "profile_binding_digest": _sha256(
                value.get("profile_binding_digest"),
                "profile_binding_digest",
            ),
            "closure_digest": _sha256(
                value.get("closure_digest"),
                "closure_digest",
            ),
            "baseline_sources_digest": _sha256(
                value.get("baseline_sources_digest"),
                "baseline_sources_digest",
            ),
            "baseline_coverage_digest": _sha256(
                value.get("baseline_coverage_digest"),
                "baseline_coverage_digest",
            ),
            "check_receipt_digests": list(checks),
        }
        if schema == self.STAGE_EXIT_PROOF_SCHEMA:
            content["stage_subject_inventory_digest"] = _sha256(
                value.get("stage_subject_inventory_digest"),
                "stage_subject_inventory_digest",
            )
        require_logical_ref(
            content["stage_subject_ref"],
            "stage_subject_ref",
        )
        if value.get("proof_digest") != _digest(content):
            raise DesignControllerError(
                "stage-exit archive proof digest changed"
            )
        return {**content, "proof_digest": value["proof_digest"]}

    def _verify_archived_stage_exit_proof(
        self,
        checkpoint: DesignControllerCheckpoint,
        value: object,
    ) -> None:
        proof = self._parse_stage_exit_proof(value)
        if proof["schema"] == self.LEGACY_STAGE_EXIT_PROOF_SCHEMA:
            self._verify_legacy_archived_stage_exit_proof(
                checkpoint,
                proof,
            )
            return
        bundle = StageExitArchiveBundle.from_dict(proof["bundle"])
        predecessor = self._load_stage_exit_predecessor(bundle)
        expected = self._admit_stage_exit(
            predecessor,
            checkpoint,
            bundle,
        )
        if proof != expected:
            raise DesignControllerError(
                "archived stage-exit proof disagrees with checkpoint"
            )

    def _verify_legacy_archived_stage_exit_proof(
        self,
        checkpoint: DesignControllerCheckpoint,
        proof: Mapping[str, object],
    ) -> None:
        bundle = _LegacyStageExitArchiveBundle.from_dict(proof["bundle"])
        predecessor = self._load_stage_exit_predecessor(bundle)
        (
            binding,
            profile,
            closure,
            receipts,
            baseline_sources,
            baseline_coverage,
        ) = self._read_legacy_stage_exit_bundle(
            bundle,
            expected_branch=predecessor.tree.branch,
        )
        if (
            not _same_branch(predecessor.tree.branch, self.branch)
            or predecessor.tree.branch.epoch + 1 != self.branch.epoch
            or checkpoint.tree.branch != self.branch
            or checkpoint.maturity.phase
            is not next_design_phase(predecessor.maturity.phase)
        ):
            raise DesignControllerError(
                "legacy stage-exit proof does not bind one exact forward phase"
            )
        if (
            len(checkpoint.history_event_refs)
            != len(predecessor.history_event_refs) + 1
            or checkpoint.history_event_refs[:-1]
            != predecessor.history_event_refs
        ):
            raise DesignControllerError(
                "legacy phase-advanced checkpoint does not extend exact history"
            )
        target = predecessor.tree.node(predecessor.target_node_ref)
        current_digest = target.operational_state.state_digest
        deliverables = {
            item.ref: item for item in predecessor.maturity.deliverables
        }
        subject = deliverables.get(closure.stage_subject_ref)
        if subject is None:
            raise DesignControllerError(
                "legacy stage-exit subject is not a predecessor deliverable"
            )
        if (
            closure.stage_id != predecessor.maturity.phase.value
            or closure.subject_digest != current_digest
            or closure.subject_digest
            != predecessor.maturity.operational_state_digest
            or subject.base_state_digest != closure.subject_digest
            or profile.predecessor_state_digest != current_digest
            or baseline_coverage.level
            != baseline_level_for_design_phase(predecessor.maturity.phase)
        ):
            raise DesignControllerError(
                "legacy stage-exit proof is stale against its predecessor"
            )
        content = {
            "schema": self.LEGACY_STAGE_EXIT_PROOF_SCHEMA,
            "bundle": bundle.to_dict(),
            "previous_checkpoint_digest": predecessor.checkpoint_digest,
            "next_checkpoint_digest": checkpoint.checkpoint_digest,
            "stage_id": closure.stage_id,
            "stage_subject_ref": closure.stage_subject_ref,
            "subject_digest": closure.subject_digest,
            "profile_digest": profile.profile_digest,
            "profile_binding_digest": binding.binding_digest,
            "closure_digest": closure.receipt_digest,
            "baseline_sources_digest": baseline_sources.source_set_digest,
            "baseline_coverage_digest": baseline_coverage.receipt_digest,
            "check_receipt_digests": [
                item.receipt_digest
                for item in sorted(
                    receipts,
                    key=lambda item: item.receipt_digest,
                )
            ],
        }
        expected = {**content, "proof_digest": _digest(content)}
        if dict(proof) != expected:
            raise DesignControllerError(
                "legacy archived stage-exit proof disagrees with checkpoint"
            )

    def _load_stage_exit_predecessor(
        self,
        bundle: StageExitArchiveBundle | _LegacyStageExitArchiveBundle,
        *,
        require_writable_anchor: bool = False,
    ) -> DesignControllerCheckpoint:
        if self.branch.epoch < 1:
            raise DesignControllerError(
                "stage-exit checkpoint has no predecessor branch epoch"
            )
        ref = bundle.predecessor_checkpoint_ref
        payload = self._load_exact_branch_json(
            ref,
            field="predecessor_checkpoint_ref",
        )
        if (
            payload.get("schema")
            not in {
                self.LEGACY_CHECKPOINT_SCHEMA,
                self.PROOF_CHECKPOINT_SCHEMA,
                self.CHECKPOINT_SCHEMA,
            }
            or payload.get("project_id") != self.branch.run.project_id
            or payload.get("run_id") != self.branch.run.run_id
            or payload.get("branch_id") != self.branch.branch_id
            or payload.get("branch_epoch") != self.branch.epoch - 1
            or payload.get("run_base")
            != _project_version_payload(self.branch.run.base)
        ):
            raise DesignControllerError(
                "stage-exit predecessor is not the exact prior branch epoch"
            )
        predecessor_branch = replace(
            self.branch,
            epoch=self.branch.epoch - 1,
        )
        predecessor_adapter = ProjectControllerArchiveAdapter(
            self._repository,
            branch=predecessor_branch,
        )
        resumed = predecessor_adapter.load_checkpoint(ref)
        if require_writable_anchor:
            if resumed.checkpoint.maturity.phase is DesignPhase.SCHEMATIC_DESIGN:
                if payload.get("schema") not in {
                    self.LEGACY_CHECKPOINT_SCHEMA,
                    self.CHECKPOINT_SCHEMA,
                }:
                    raise DesignControllerError(
                        "read-only checkpoint cannot authorize a new branch epoch"
                    )
            else:
                anchor_ref = predecessor_adapter._checkpoint_anchor_ref(
                    ref,
                    payload,
                )
                if anchor_ref is None:
                    raise DesignControllerError(
                        "read-only stage-exit proof cannot authorize a new branch epoch"
                    )
                anchor_payload = self._repository.load_json(anchor_ref)
                anchor_proof = anchor_payload.get("stage_exit_proof")
                if (
                    anchor_payload.get("schema") != self.CHECKPOINT_SCHEMA
                    or not isinstance(anchor_proof, Mapping)
                    or anchor_proof.get("schema")
                    != self.STAGE_EXIT_PROOF_SCHEMA
                ):
                    raise DesignControllerError(
                        "read-only stage-exit proof cannot authorize a new branch epoch"
                    )
        return resumed.checkpoint

    def _stage_exit_proof_from_record(
        self,
        ref: ProjectRecordRef,
    ) -> dict[str, object] | None:
        self._require_branch_record(ref)
        payload = self._repository.load_json(ref)
        if payload.get("schema") == self.LEGACY_CHECKPOINT_SCHEMA:
            return None
        if payload.get("schema") not in {
            self.PROOF_CHECKPOINT_SCHEMA,
            self.CHECKPOINT_SCHEMA,
        }:
            raise DesignControllerError(
                "controller checkpoint record schema drifted"
            )
        value = payload.get("stage_exit_proof")
        if value is None:
            return None
        return self._parse_stage_exit_proof(value)

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
    stage_profile_binding_digest: str | None = None
    stage_baseline_coverage_digest: str | None = None

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
        if self.stage_profile_binding_digest is not None:
            _sha256(
                self.stage_profile_binding_digest,
                "stage_profile_binding_digest",
            )
        if self.stage_baseline_coverage_digest is not None:
            _sha256(
                self.stage_baseline_coverage_digest,
                "stage_baseline_coverage_digest",
            )
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
    stage_convergence: StageConvergenceReceipt | None = None
    stage_closure: CompositeStageClosureReceipt | None = None
    stage_profile_binding: StageRequirementProfileBinding | None = None
    stage_baseline_coverage: StageBaselineCoverageReceipt | None = None


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
                    *checkpoint.reopened_node_refs,
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


def _require_exact_current_stage_convergence(
    checkpoint: DesignControllerCheckpoint,
    receipt: StageConvergenceReceipt,
) -> None:
    """Require one non-rejected P080 receipt for the exact current state."""

    if not isinstance(receipt, StageConvergenceReceipt):
        raise TypeError(
            "convergence_receipt must be a StageConvergenceReceipt"
        )
    target = checkpoint.tree.node(checkpoint.target_node_ref)
    state = target.operational_state
    if (
        receipt.branch != state.branch
        or receipt.branch != checkpoint.tree.branch
        or receipt.branch != checkpoint.maturity.branch
    ):
        raise DesignControllerError(
            "stage convergence receipt is cross-branch or stale"
        )
    if (
        receipt.child_state_digest != state.state_digest
        or receipt.child_state_digest
        != checkpoint.maturity.operational_state_digest
        or receipt.child_sufficient_digest != state.sufficient_digest
    ):
        raise DesignControllerError(
            "stage convergence receipt is stale against current state"
        )
    if receipt.outcome is StageConvergenceOutcome.REJECTED:
        raise DesignControllerError(
            "stage convergence receipt was rejected"
        )


def _require_stage_exit_convergence(
    checkpoint: DesignControllerCheckpoint,
    receipt: StageConvergenceReceipt,
) -> None:
    """Add the closed-potential requirement used only at stage exit."""

    _require_exact_current_stage_convergence(checkpoint, receipt)
    if not receipt.potential_after.is_zero:
        raise DesignControllerError(
            "stage convergence potential is not closed"
        )
    if not receipt.stage_ready:
        raise DesignControllerError(
            "stage convergence receipt cannot support stage exit"
        )


def _require_exact_current_stage_closure(
    checkpoint: DesignControllerCheckpoint,
    phase_gate: PhaseGateReceipt,
    requirement_profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    receipt: CompositeStageClosureReceipt,
    baseline_sources: StageBaselineSourceSet,
    subject_inventory: StageSubjectInventory,
    check_receipts: tuple[CheckReceiptEnvelope, ...],
) -> StageBaselineCoverageReceipt:
    """Require a satisfied composite closure for one accepted deliverable."""

    if not isinstance(requirement_profile, StageRequirementProfile):
        raise TypeError(
            "requirement_profile must be a StageRequirementProfile"
        )
    if not isinstance(profile_binding, StageRequirementProfileBinding):
        raise TypeError(
            "profile_binding must be a StageRequirementProfileBinding"
        )
    if not isinstance(receipt, CompositeStageClosureReceipt):
        raise TypeError(
            "closure_receipt must be a CompositeStageClosureReceipt"
        )
    if not isinstance(baseline_sources, StageBaselineSourceSet):
        raise TypeError("baseline_sources must be a StageBaselineSourceSet")
    if not isinstance(subject_inventory, StageSubjectInventory):
        raise TypeError("subject_inventory must be a StageSubjectInventory")
    if not isinstance(check_receipts, tuple) or any(
        not isinstance(item, CheckReceiptEnvelope) for item in check_receipts
    ):
        raise TypeError(
            "check_receipts must contain CheckReceiptEnvelope values"
        )
    if (
        receipt.status is not StageClosureStatus.SATISFIED
        or receipt.findings
    ):
        raise DesignControllerError(
            "composite stage closure is not satisfied"
        )
    target = checkpoint.tree.node(checkpoint.target_node_ref)
    state = target.operational_state
    if (
        requirement_profile.branch != state.branch
        or requirement_profile.branch != checkpoint.tree.branch
        or requirement_profile.branch != checkpoint.maturity.branch
        or requirement_profile.branch != phase_gate.branch
    ):
        raise DesignControllerError(
            "stage requirement profile is cross-branch or stale"
        )
    if (
        profile_binding.branch != state.branch
        or profile_binding.branch != checkpoint.tree.branch
        or profile_binding.branch != checkpoint.maturity.branch
        or profile_binding.branch != phase_gate.branch
    ):
        raise DesignControllerError(
            "stage requirement profile binding is cross-branch or stale"
        )
    if (
        receipt.branch != state.branch
        or receipt.branch != checkpoint.tree.branch
        or receipt.branch != checkpoint.maturity.branch
        or receipt.branch != phase_gate.branch
    ):
        raise DesignControllerError(
            "composite stage closure is cross-branch or stale"
        )
    if receipt.stage_id != checkpoint.maturity.phase.value:
        raise DesignControllerError(
            "composite stage closure does not match the current phase"
        )
    if profile_binding.stage_id != checkpoint.maturity.phase.value:
        raise DesignControllerError(
            "stage requirement profile binding does not match the current phase"
        )
    if requirement_profile.stage_id != checkpoint.maturity.phase.value:
        raise DesignControllerError(
            "stage requirement profile does not match the current phase"
        )
    if (
        requirement_profile.profile_id != profile_binding.profile_id
        or requirement_profile.profile_digest != profile_binding.profile_digest
        or requirement_profile.stage_subject_ref
        != profile_binding.stage_subject_ref
        or profile_binding.stage_subject_inventory_digest
        != subject_inventory.inventory_digest
    ):
        raise DesignControllerError(
            "stage requirement profile does not match the authorized profile "
            "binding"
        )
    inventory_authority_refs = {
        ref
        for entry in subject_inventory.entries
        for obligation in entry.role_obligations
        for ref in obligation.authority_refs
    }
    binding_authority_refs = {
        ref.uri for ref in profile_binding.authority_refs
    }
    if not inventory_authority_refs.issubset(binding_authority_refs):
        raise DesignControllerError(
            "stage subject role obligations are not authorized by the "
            "exact profile binding"
        )
    if (
        receipt.profile_id != profile_binding.profile_id
        or receipt.profile_digest != profile_binding.profile_digest
    ):
        raise DesignControllerError(
            "composite stage closure does not use the authorized profile"
        )
    if (
        receipt.profile_id != requirement_profile.profile_id
        or receipt.profile_digest != requirement_profile.profile_digest
        or receipt.stage_subject_ref
        != requirement_profile.stage_subject_ref
    ):
        raise DesignControllerError(
            "composite stage closure does not use the supplied exact profile"
        )
    if (
        receipt.stage_subject_ref != profile_binding.stage_subject_ref
        or receipt.subject_digest != profile_binding.subject_digest
        or subject_inventory.branch != checkpoint.tree.branch
        or subject_inventory.stage_id != receipt.stage_id
        or subject_inventory.stage_subject_ref
        != receipt.stage_subject_ref
        or subject_inventory.stage_subject_digest
        != receipt.subject_digest
        or subject_inventory.baseline_level
        != baseline_level_for_design_phase(checkpoint.maturity.phase)
    ):
        raise DesignControllerError(
            "composite stage closure does not match the authorized subject"
        )
    deliverables = {
        item.ref: item for item in checkpoint.maturity.deliverables
    }
    subject = deliverables.get(receipt.stage_subject_ref)
    if subject is None:
        raise DesignControllerError(
            "composite stage closure subject is not a known maturity "
            "deliverable"
        )
    if subject_inventory.component_proposal_ref.uri not in {
        subject.artifact_ref,
        *subject.evidence_refs,
    }:
        raise DesignControllerError(
            "stage subject does not retain the exact component proposal "
            "record as predecessor evidence"
        )
    if receipt.stage_subject_ref not in phase_gate.accepted_deliverable_refs:
        raise DesignControllerError(
            "composite stage closure subject was not accepted by the "
            "current phase gate"
        )
    current_digest = state.state_digest
    if (
        receipt.subject_digest != subject.base_state_digest
        or receipt.subject_digest != current_digest
        or current_digest
        != checkpoint.maturity.operational_state_digest
    ):
        raise DesignControllerError(
            "composite stage closure subject digest is stale against the "
            "current operational state"
        )
    if requirement_profile.predecessor_state_digest != current_digest:
        raise DesignControllerError(
            "stage requirement profile predecessor is stale against the "
            "current operational state"
        )
    recomputed_closure = compile_composite_stage_closure(
        requirement_profile,
        subject_digest=current_digest,
        check_receipts=check_receipts,
    )
    if recomputed_closure != receipt:
        raise DesignControllerError(
            "composite stage closure was not recomputed from the supplied "
            "exact check receipts"
        )
    baseline = compile_stage_baseline_coverage(
        requirement_profile,
        level=baseline_level_for_design_phase(checkpoint.maturity.phase),
        sources=baseline_sources,
        subject_digest=current_digest,
        subject_inventory=subject_inventory,
        check_receipts=check_receipts,
    )
    if baseline.status is not StageBaselineStatus.SATISFIED:
        raise DesignControllerError(
            "stage requirement profile omits framework baseline roles: "
            f"{tuple(item.value for item in baseline.missing_roles)}"
        )
    return baseline


def close_reopened_nodes(
    checkpoint: DesignControllerCheckpoint,
    convergence_receipt: StageConvergenceReceipt,
    *,
    resolved_node_refs: tuple[str, ...],
    history_event_ref: str,
) -> ControllerTurnResult:
    """Close only explicitly named reopened nodes after exact repair proof."""

    require_logical_ref(history_event_ref, "history_event_ref")
    if checkpoint.status is not ControllerStatus.READY:
        raise DesignControllerError(
            "only a ready checkpoint can close reopened nodes"
        )
    _tuple(resolved_node_refs, "resolved_node_refs")
    if not resolved_node_refs:
        raise DesignControllerError(
            "resolved_node_refs must name a dependency-local scope"
        )
    for ref in resolved_node_refs:
        require_logical_ref(ref, "resolved_node_ref")
    _unique(resolved_node_refs, "resolved_node_refs")
    unknown = set(resolved_node_refs) - set(
        checkpoint.reopened_node_refs
    )
    if unknown:
        raise DesignControllerError(
            "cannot close refs outside the current reopened set: "
            f"{sorted(unknown)}"
        )
    _require_exact_current_stage_convergence(
        checkpoint,
        convergence_receipt,
    )
    if (
        convergence_receipt.outcome
        is not StageConvergenceOutcome.REPAIR
        or convergence_receipt.transition_kind
        is not StageTransitionKind.REPAIR
    ):
        raise DesignControllerError(
            "closing reopened nodes requires an exact repair receipt"
        )
    before_deficits = set(
        convergence_receipt.potential_before.deficit_refs
    )
    after_deficits = set(
        convergence_receipt.potential_after.deficit_refs
    )
    unresolved_by_receipt = set(resolved_node_refs) - before_deficits
    still_open_by_receipt = set(resolved_node_refs) & after_deficits
    if unresolved_by_receipt or still_open_by_receipt:
        raise DesignControllerError(
            "repair receipt does not prove the named reopened refs closed: "
            f"not_in_before={sorted(unresolved_by_receipt)}, "
            f"still_in_after={sorted(still_open_by_receipt)}"
        )
    remaining = tuple(
        sorted(
            set(checkpoint.reopened_node_refs) - set(resolved_node_refs)
        )
    )
    next_checkpoint = replace(
        checkpoint,
        history_event_refs=(
            *checkpoint.history_event_refs,
            history_event_ref,
        ),
        reopened_node_refs=remaining,
    )
    return _result(
        previous=checkpoint,
        checkpoint=next_checkpoint,
        outcome=ControllerOutcome.REOPENED_REFS_CLOSED,
        history_event_ref=history_event_ref,
        reason=(
            "exact repair receipt closed explicitly named reopened nodes"
        ),
        stage_convergence=convergence_receipt,
    )


def advance_design_phase(
    checkpoint: DesignControllerCheckpoint,
    receipt: PhaseGateReceipt,
    *,
    convergence_receipt: StageConvergenceReceipt,
    requirement_profile: StageRequirementProfile,
    profile_binding: StageRequirementProfileBinding,
    closure_receipt: CompositeStageClosureReceipt,
    baseline_sources: StageBaselineSourceSet,
    subject_inventory: StageSubjectInventory,
    check_receipts: tuple[CheckReceiptEnvelope, ...],
    history_event_ref: str,
) -> ControllerTurnResult:
    """Advance only after P039, P080, an authorized profile, and closure."""

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
    if checkpoint.reopened_node_refs:
        raise DesignControllerError(
            "phase cannot advance with reopened nodes: "
            f"{checkpoint.reopened_node_refs}"
        )
    _require_stage_exit_convergence(
        checkpoint,
        convergence_receipt,
    )
    require_current_phase_gate(checkpoint.maturity, receipt)
    baseline_coverage = _require_exact_current_stage_closure(
        checkpoint,
        receipt,
        requirement_profile,
        profile_binding,
        closure_receipt,
        baseline_sources,
        subject_inventory,
        check_receipts,
    )
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
        stage_convergence=convergence_receipt,
        stage_closure=closure_receipt,
        stage_profile_binding=profile_binding,
        stage_baseline_coverage=baseline_coverage,
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
                    *checkpoint.reopened_node_refs,
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
                    *checkpoint.reopened_node_refs,
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
    stage_convergence: StageConvergenceReceipt | None = None,
    stage_closure: CompositeStageClosureReceipt | None = None,
    stage_profile_binding: StageRequirementProfileBinding | None = None,
    stage_baseline_coverage: StageBaselineCoverageReceipt | None = None,
) -> ControllerTurnResult:
    payload = {
        "outcome": outcome.value,
        "previous": previous.checkpoint_digest,
        "next": checkpoint.checkpoint_digest,
        "action": None if action is None else action.action_digest,
        "history_event_ref": history_event_ref,
        "reason": reason,
        "stage_convergence": (
            None
            if stage_convergence is None
            else stage_convergence.receipt_id
        ),
        "stage_closure": (
            None if stage_closure is None else stage_closure.receipt_id
        ),
        "stage_profile_binding": (
            None
            if stage_profile_binding is None
            else stage_profile_binding.binding_digest
        ),
        "stage_baseline_coverage": (
            None
            if stage_baseline_coverage is None
            else stage_baseline_coverage.receipt_digest
        ),
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
            stage_profile_binding_digest=(
                None
                if stage_profile_binding is None
                else stage_profile_binding.binding_digest
            ),
            stage_baseline_coverage_digest=(
                None
                if stage_baseline_coverage is None
                else stage_baseline_coverage.receipt_digest
            ),
        ),
        transition=nested,
        phase_transition=phase_transition,
        phase_gate=phase_gate,
        backward_revision=backward_revision,
        stage_convergence=stage_convergence,
        stage_closure=stage_closure,
        stage_profile_binding=stage_profile_binding,
        stage_baseline_coverage=stage_baseline_coverage,
    )
