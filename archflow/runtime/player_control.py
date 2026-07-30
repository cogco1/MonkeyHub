"""Player-facing candidate controls with no game UI or mutation authority.

The module joins existing exact-base contracts at the interaction boundary:
P043 authority evidence, P024 candidate assembly, P005 validation, P017
commitment monitoring, and M003 world-recovery receipts. It returns immutable
records only; adapters still own external writes and the committer remains the
only canonical writer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from archflow.interaction import (
    AuthorityDecisionReceipt,
    CandidateApprovalMode,
    CandidateApprovalPolicy,
    CandidateApprovalReceipt,
    CandidateApprovalSource,
    PlayerAuthorityError,
    parse_utc,
    validate_human_identity,
)
from archflow.project import ProjectVersionRef
from archflow.project.refs import require_identifier
from archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidatePolicyKind,
)
from archflow.runtime.clarification import (
    ClarificationResumeResult,
    resume_from_clarification,
)
from archflow.runtime.world_recovery import (
    WorldMutationStatus,
    WorldMutationTrace,
)
from archflow.state import BuildPolicy, Commitment, OperationalMarkovState
from archflow.state.candidate_program import (
    CandidateProgramValue,
    CandidateValueFacet,
)
from archflow.state.operational_state import require_logical_ref
from archflow.submission import CandidateSubmission
from archflow.validation.commitments import CommitmentMonitorReceipt
from archflow.validation.model import ValidationReceipt


_HEX = frozenset("0123456789abcdef")


class PlayerControlError(ValueError):
    """A control request is stale or lacks an exact, authorized boundary."""


class CandidateRevisionKind(StrEnum):
    MOVE = "move"
    ROTATE = "rotate"
    REVISE = "revise"


class CandidateControlStatus(StrEnum):
    PREVIEW_READY = "preview_ready"
    APPROVED = "approved"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    UNDO_REQUIRED = "undo_required"
    RESTORED = "restored"
    MANUAL_RECONCILIATION_REQUIRED = "manual_reconciliation_required"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise PlayerControlError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerControlError(f"{field} must be non-empty text")
    return value


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        requirement = "a tuple" if allow_empty else "a non-empty tuple"
        raise PlayerControlError(f"{field} must be {requirement}")
    for value in values:
        require_logical_ref(value, field)
    if len(values) != len(set(values)):
        raise PlayerControlError(f"{field} contains duplicates")
    return values


def _base_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _candidate_identity(assembly: CandidateAssembly) -> dict[str, object]:
    return {
        "candidate_assembly_digest": assembly.assembly_digest,
        "submission_id": assembly.submission.submission_id,
        "plan_digest": assembly.plan.plan_digest,
        "base": assembly.plan.base,
        "workspace_id": assembly.submission.workspace_id,
    }


def _candidate_policy_binding(
    assembly: CandidateAssembly,
    kind: CandidatePolicyKind,
):
    return next(item for item in assembly.policies if item.kind is kind)


def _require_approval_policy(
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
) -> None:
    if not isinstance(assembly, CandidateAssembly):
        raise TypeError("assembly must be CandidateAssembly")
    if not isinstance(policy, CandidateApprovalPolicy):
        raise TypeError("policy must be CandidateApprovalPolicy")
    binding = _candidate_policy_binding(
        assembly,
        CandidatePolicyKind.APPROVAL,
    )
    if (
        binding.policy_ref != policy.policy_ref
        or binding.policy_digest != policy.policy_digest
    ):
        raise PlayerControlError(
            "candidate does not bind the exact approval policy"
        )


@dataclass(frozen=True, slots=True)
class VoxelBounds:
    minimum: tuple[int, int, int]
    maximum: tuple[int, int, int]

    def __post_init__(self) -> None:
        for value, field in (
            (self.minimum, "minimum"),
            (self.maximum, "maximum"),
        ):
            if (
                not isinstance(value, tuple)
                or len(value) != 3
                or any(
                    not isinstance(item, int) or isinstance(item, bool)
                    for item in value
                )
            ):
                raise PlayerControlError(
                    f"{field} must be a three-integer tuple"
                )
        if any(high < low for low, high in zip(self.minimum, self.maximum)):
            raise PlayerControlError("maximum cannot precede minimum")

    def to_dict(self) -> dict[str, object]:
        return {
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
        }


@dataclass(frozen=True, slots=True)
class WorldTarget:
    server_sha256: str
    world_id: str
    dimension_id: str

    def __post_init__(self) -> None:
        _sha(self.server_sha256, "server_sha256")
        _text(self.world_id, "world_id")
        _text(self.dimension_id, "dimension_id")

    @classmethod
    def from_trace(cls, trace: WorldMutationTrace) -> WorldTarget:
        if not isinstance(trace, WorldMutationTrace):
            raise TypeError("trace must be WorldMutationTrace")
        world_id = trace.world_identity.get("world_id")
        dimension_id = trace.world_identity.get("dimension_id")
        if world_id is None or dimension_id is None:
            raise PlayerControlError(
                "world trace lacks a named world and dimension"
            )
        return cls(
            server_sha256=_digest(trace.server),
            world_id=world_id,
            dimension_id=dimension_id,
        )

    def matches(self, trace: WorldMutationTrace) -> bool:
        return (
            self.server_sha256 == _digest(trace.server)
            and self.world_id == trace.world_identity.get("world_id")
            and self.dimension_id
            == trace.world_identity.get("dimension_id")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "server_sha256": self.server_sha256,
            "world_id": self.world_id,
            "dimension_id": self.dimension_id,
        }


@dataclass(frozen=True, slots=True)
class MaterialImpact:
    resource_ref: str
    required: float
    available: float | None
    unit: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_logical_ref(self.resource_ref, "resource_ref")
        for value, field in (
            (self.required, "required"),
            (self.available, "available"),
        ):
            if value is None and field == "available":
                continue
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise PlayerControlError(
                    f"{field} must be a non-negative number or unknown"
                )
        _text(self.unit, "unit")
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def shortage(self) -> float | None:
        if self.available is None:
            return None
        return max(0.0, float(self.required) - float(self.available))

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_ref": self.resource_ref,
            "required": self.required,
            "available": self.available,
            "unit": self.unit,
            "shortage": self.shortage,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class CandidateImpactWarning:
    subject_ref: str
    code: str
    message: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_logical_ref(self.subject_ref, "subject_ref")
        _text(self.code, "code")
        _text(self.message, "message")
        _refs(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "subject_ref": self.subject_ref,
            "code": self.code,
            "message": self.message,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class CandidatePreviewReceipt:
    candidate_assembly_digest: str
    submission_id: str
    plan_digest: str
    base: ProjectVersionRef
    workspace_id: str
    world: WorldTarget
    bounds: VoxelBounds
    additions: int
    removals: int
    collisions: tuple[CandidateImpactWarning, ...]
    materials: tuple[MaterialImpact, ...]
    protected_objects: tuple[CandidateImpactWarning, ...]
    unresolved_obligation_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "CandidatePreviewReceipt@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.candidate_assembly_digest, "candidate_assembly_digest"),
            (self.plan_digest, "plan_digest"),
        ):
            _sha(value, field)
        require_identifier(self.submission_id, "submission_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        require_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.world, WorldTarget):
            raise TypeError("world must be WorldTarget")
        if not isinstance(self.bounds, VoxelBounds):
            raise TypeError("bounds must be VoxelBounds")
        for value, field in (
            (self.additions, "additions"),
            (self.removals, "removals"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise PlayerControlError(
                    f"{field} must be a non-negative integer"
                )
        for values, item_type, field in (
            (self.collisions, CandidateImpactWarning, "collisions"),
            (self.materials, MaterialImpact, "materials"),
            (
                self.protected_objects,
                CandidateImpactWarning,
                "protected_objects",
            ),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, item_type) for item in values
            ):
                raise TypeError(f"{field} contains an invalid item")
        _refs(
            self.unresolved_obligation_refs,
            "unresolved_obligation_refs",
            allow_empty=True,
        )
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def preview_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-preview:{self.preview_digest}"

    @property
    def has_blocking_warning(self) -> bool:
        return bool(
            self.collisions
            or self.protected_objects
            or self.unresolved_obligation_refs
            or any(
                item.available is None
                or (item.shortage is not None and item.shortage > 0)
                for item in self.materials
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "submission_id": self.submission_id,
            "plan_digest": self.plan_digest,
            "base": _base_dict(self.base),
            "workspace_id": self.workspace_id,
            "world": self.world.to_dict(),
            "bounds": self.bounds.to_dict(),
            "additions": self.additions,
            "removals": self.removals,
            "collisions": [item.to_dict() for item in self.collisions],
            "materials": [item.to_dict() for item in self.materials],
            "protected_objects": [
                item.to_dict() for item in self.protected_objects
            ],
            "unresolved_obligation_refs": list(
                self.unresolved_obligation_refs
            ),
            "evidence_refs": list(self.evidence_refs),
            "world_write_performed": False,
            "hard_usability_evaluated": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CandidateProgramInspection:
    """Exact candidate values selected for player review, not recompilation."""

    candidate_assembly_digest: str
    submission_id: str
    plan_digest: str
    base: ProjectVersionRef
    requested_facets: tuple[CandidateValueFacet, ...]
    values: tuple[CandidateProgramValue, ...]

    SCHEMA = "CandidateProgramInspection@1"

    def __post_init__(self) -> None:
        _sha(self.candidate_assembly_digest, "candidate_assembly_digest")
        _sha(self.plan_digest, "plan_digest")
        require_identifier(self.submission_id, "submission_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        if (
            not isinstance(self.requested_facets, tuple)
            or not self.requested_facets
            or any(
                not isinstance(item, CandidateValueFacet)
                for item in self.requested_facets
            )
            or len(self.requested_facets) != len(set(self.requested_facets))
        ):
            raise PlayerControlError(
                "requested_facets must be unique candidate facets"
            )
        if (
            not isinstance(self.values, tuple)
            or not self.values
            or any(
                not isinstance(item, CandidateProgramValue)
                for item in self.values
            )
            or any(
                item.facet not in self.requested_facets
                for item in self.values
            )
        ):
            raise PlayerControlError(
                "inspection values must match the requested facets"
            )

    @property
    def inspection_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-inspection:{self.inspection_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "submission_id": self.submission_id,
            "plan_digest": self.plan_digest,
            "base": _base_dict(self.base),
            "requested_facets": [
                item.value for item in self.requested_facets
            ],
            "values": [item.to_dict() for item in self.values],
            "generation_authority": False,
            "world_write_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CandidateRevisionProposal:
    source_candidate_digest: str
    revised_candidate_digest: str
    source_submission_id: str
    revised_submission_id: str
    source_plan_digest: str
    revised_plan_digest: str
    base: ProjectVersionRef
    kind: CandidateRevisionKind
    authority_id: str
    rationale: str
    invalidated_approval_ref: str | None
    evidence_refs: tuple[str, ...]

    SCHEMA = "CandidateRevisionProposal@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.source_candidate_digest, "source_candidate_digest"),
            (self.revised_candidate_digest, "revised_candidate_digest"),
            (self.source_plan_digest, "source_plan_digest"),
            (self.revised_plan_digest, "revised_plan_digest"),
        ):
            _sha(value, field)
        require_identifier(self.source_submission_id, "source_submission_id")
        require_identifier(self.revised_submission_id, "revised_submission_id")
        if self.source_submission_id == self.revised_submission_id:
            raise PlayerControlError(
                "revision requires a new submission identity"
            )
        if self.source_plan_digest == self.revised_plan_digest:
            raise PlayerControlError("revision requires a changed exact plan")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        if not isinstance(self.kind, CandidateRevisionKind):
            raise TypeError("kind must be CandidateRevisionKind")
        require_identifier(self.authority_id, "authority_id")
        _text(self.rationale, "rationale")
        if self.invalidated_approval_ref is not None:
            require_logical_ref(
                self.invalidated_approval_ref,
                "invalidated_approval_ref",
            )
        _refs(self.evidence_refs, "evidence_refs")

    @property
    def proposal_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-revision:{self.proposal_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_candidate_digest": self.source_candidate_digest,
            "revised_candidate_digest": self.revised_candidate_digest,
            "source_submission_id": self.source_submission_id,
            "revised_submission_id": self.revised_submission_id,
            "source_plan_digest": self.source_plan_digest,
            "revised_plan_digest": self.revised_plan_digest,
            "base": _base_dict(self.base),
            "kind": self.kind.value,
            "authority_id": self.authority_id,
            "rationale": self.rationale,
            "invalidated_approval_ref": self.invalidated_approval_ref,
            "evidence_refs": list(self.evidence_refs),
            "world_write_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CandidatePreferenceReceipt:
    candidate_assembly_digest: str
    submission_id: str
    plan_digest: str
    base: ProjectVersionRef
    authority_id: str
    selected_option_refs: tuple[str, ...]
    selected_material_refs: tuple[str, ...]
    aesthetic_observation_refs: tuple[str, ...]
    rationale: str
    authority_event_ref: str
    issued_at_utc: str

    SCHEMA = "CandidatePreferenceReceipt@1"

    def __post_init__(self) -> None:
        _sha(self.candidate_assembly_digest, "candidate_assembly_digest")
        _sha(self.plan_digest, "plan_digest")
        require_identifier(self.submission_id, "submission_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        require_identifier(self.authority_id, "authority_id")
        _refs(
            self.selected_option_refs,
            "selected_option_refs",
            allow_empty=True,
        )
        _refs(
            self.selected_material_refs,
            "selected_material_refs",
            allow_empty=True,
        )
        _refs(
            self.aesthetic_observation_refs,
            "aesthetic_observation_refs",
            allow_empty=True,
        )
        if not self.selected_option_refs and not self.selected_material_refs:
            raise PlayerControlError(
                "preference must select an option or material"
            )
        _text(self.rationale, "rationale")
        require_logical_ref(self.authority_event_ref, "authority_event_ref")
        parse_utc(self.issued_at_utc)

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-preference:{self.receipt_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "submission_id": self.submission_id,
            "plan_digest": self.plan_digest,
            "base": _base_dict(self.base),
            "authority_id": self.authority_id,
            "selected_option_refs": list(self.selected_option_refs),
            "selected_material_refs": list(self.selected_material_refs),
            "aesthetic_observation_refs": list(
                self.aesthetic_observation_refs
            ),
            "rationale": self.rationale,
            "authority_event_ref": self.authority_event_ref,
            "issued_at_utc": self.issued_at_utc,
            "hard_gate_waiver_authority": False,
            "commitment_waiver_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CandidateControlState:
    candidate_assembly_digest: str
    submission_id: str
    plan_digest: str
    base: ProjectVersionRef
    workspace_id: str
    world: WorldTarget
    status: CandidateControlStatus
    sequence: int
    preview_ref: str
    approval_ref: str | None = None
    mutation_trace_ref: str | None = None
    restore_evidence_ref: str | None = None
    reason: str | None = None
    paused_from: CandidateControlStatus | None = None

    SCHEMA = "CandidateControlState@2"

    def __post_init__(self) -> None:
        for value, field in (
            (self.candidate_assembly_digest, "candidate_assembly_digest"),
            (self.plan_digest, "plan_digest"),
        ):
            _sha(value, field)
        require_identifier(self.submission_id, "submission_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        self.base.require_digest()
        require_identifier(self.workspace_id, "workspace_id")
        if not isinstance(self.world, WorldTarget):
            raise TypeError("world must be WorldTarget")
        if not isinstance(self.status, CandidateControlStatus):
            raise TypeError("status must be CandidateControlStatus")
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise PlayerControlError(
                "sequence must be a non-negative integer"
            )
        require_logical_ref(self.preview_ref, "preview_ref")
        for value, field in (
            (self.approval_ref, "approval_ref"),
            (self.mutation_trace_ref, "mutation_trace_ref"),
            (self.restore_evidence_ref, "restore_evidence_ref"),
        ):
            if value is not None:
                require_logical_ref(value, field)
        if self.reason is not None:
            _text(self.reason, "reason")
        if (
            self.status is CandidateControlStatus.RESTORED
            and self.restore_evidence_ref is None
        ):
            raise PlayerControlError(
                "restored state requires exact restore evidence"
            )
        if self.status is CandidateControlStatus.PAUSED:
            if self.paused_from is None:
                raise PlayerControlError(
                    "paused state must record the status it paused from"
                )
            if not isinstance(self.paused_from, CandidateControlStatus):
                raise TypeError(
                    "paused_from must be CandidateControlStatus"
                )
            if self.paused_from in {
                CandidateControlStatus.PAUSED,
                CandidateControlStatus.CANCELLED,
                CandidateControlStatus.RESTORED,
            }:
                raise PlayerControlError(
                    "paused_from must name a resumable status"
                )
        elif self.paused_from is not None:
            raise PlayerControlError(
                "paused_from is only valid while paused"
            )

    @property
    def effective_status(self) -> CandidateControlStatus:
        """The status guards must judge; pausing never launders state."""

        if self.status is CandidateControlStatus.PAUSED:
            assert self.paused_from is not None
            return self.paused_from
        return self.status

    @property
    def state_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-control:{self.state_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "submission_id": self.submission_id,
            "plan_digest": self.plan_digest,
            "base": _base_dict(self.base),
            "workspace_id": self.workspace_id,
            "world": self.world.to_dict(),
            "status": self.status.value,
            "sequence": self.sequence,
            "preview_ref": self.preview_ref,
            "approval_ref": self.approval_ref,
            "mutation_trace_ref": self.mutation_trace_ref,
            "restore_evidence_ref": self.restore_evidence_ref,
            "reason": self.reason,
            "paused_from": (
                None if self.paused_from is None else self.paused_from.value
            ),
            "world_mutation_performed": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CandidatePromotionReadiness:
    candidate_assembly_digest: str
    source_submission_id: str
    submission_id: str
    approval_ref: str
    hard_validation_receipt_id: str
    commitment_monitor_receipt_id: str
    preference_ref: str | None
    ready: bool
    blockers: tuple[str, ...]

    SCHEMA = "CandidatePromotionReadiness@2"

    def __post_init__(self) -> None:
        _sha(self.candidate_assembly_digest, "candidate_assembly_digest")
        require_identifier(
            self.source_submission_id,
            "source_submission_id",
        )
        require_identifier(self.submission_id, "submission_id")
        require_logical_ref(self.approval_ref, "approval_ref")
        require_identifier(
            self.hard_validation_receipt_id,
            "hard_validation_receipt_id",
        )
        require_identifier(
            self.commitment_monitor_receipt_id,
            "commitment_monitor_receipt_id",
        )
        if self.preference_ref is not None:
            require_logical_ref(self.preference_ref, "preference_ref")
        if not isinstance(self.ready, bool):
            raise TypeError("ready must be bool")
        if not isinstance(self.blockers, tuple):
            raise TypeError("blockers must be tuple")
        if self.ready == bool(self.blockers):
            raise PlayerControlError(
                "ready must be true exactly when blockers are empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "candidate_assembly_digest": self.candidate_assembly_digest,
            "source_submission_id": self.source_submission_id,
            "submission_id": self.submission_id,
            "approval_ref": self.approval_ref,
            "hard_validation_receipt_id": (
                self.hard_validation_receipt_id
            ),
            "commitment_monitor_receipt_id": (
                self.commitment_monitor_receipt_id
            ),
            "preference_ref": self.preference_ref,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "canonical_write_authority": False,
        }


def create_candidate_preview(
    assembly: CandidateAssembly,
    *,
    world: WorldTarget,
    bounds: VoxelBounds,
    additions: int,
    removals: int,
    collisions: tuple[CandidateImpactWarning, ...] = (),
    materials: tuple[MaterialImpact, ...] = (),
    protected_objects: tuple[CandidateImpactWarning, ...] = (),
    unresolved_obligation_refs: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...],
) -> CandidatePreviewReceipt:
    """Detach complete pre-write impact; this is not an MCP success receipt."""

    if not isinstance(assembly, CandidateAssembly):
        raise TypeError("assembly must be CandidateAssembly")
    return CandidatePreviewReceipt(
        **_candidate_identity(assembly),
        world=world,
        bounds=bounds,
        additions=additions,
        removals=removals,
        collisions=collisions,
        materials=materials,
        protected_objects=protected_objects,
        unresolved_obligation_refs=unresolved_obligation_refs,
        evidence_refs=evidence_refs,
    )


def inspect_candidate_program(
    assembly: CandidateAssembly,
    *,
    facets: tuple[CandidateValueFacet, ...] = (
        CandidateValueFacet.FUNCTION,
        CandidateValueFacet.AREA,
    ),
) -> CandidateProgramInspection:
    """Expose project-derived values without inventing or changing them."""

    if not isinstance(assembly, CandidateAssembly):
        raise TypeError("assembly must be CandidateAssembly")
    if not isinstance(facets, tuple) or any(
        not isinstance(item, CandidateValueFacet) for item in facets
    ):
        raise TypeError("facets must contain CandidateValueFacet values")
    selected = tuple(
        item for item in assembly.projection.values if item.facet in facets
    )
    return CandidateProgramInspection(
        candidate_assembly_digest=assembly.assembly_digest,
        submission_id=assembly.submission.submission_id,
        plan_digest=assembly.plan.plan_digest,
        base=assembly.plan.base,
        requested_facets=facets,
        values=selected,
    )


def issue_human_candidate_approval(
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
    *,
    identity_receipt: AuthorityDecisionReceipt,
    authority_id: str,
    approval_event_ref: str,
    issued_at_utc: str,
    valid_until_utc: str,
) -> CandidateApprovalReceipt:
    """Create a distinct candidate approval from reusable P043 identity."""

    _require_approval_policy(assembly, policy)
    if policy.mode is not CandidateApprovalMode.HUMAN_REQUIRED:
        raise PlayerAuthorityError(
            "human approval requires a human-required policy"
        )
    if authority_id not in policy.authority_ids:
        raise PlayerAuthorityError(
            "authority is not named by the approval policy"
        )
    validate_human_identity(
        identity_receipt,
        project_id=assembly.plan.project_id,
        run_id=assembly.plan.run_id,
        base=assembly.plan.base,
        authority_id=authority_id,
        now_utc=issued_at_utc,
    )
    if approval_event_ref == identity_receipt.authority_event_ref:
        raise PlayerAuthorityError(
            "identity evidence cannot double as candidate approval event"
        )
    issued = parse_utc(issued_at_utc)
    valid_until = parse_utc(valid_until_utc)
    if (
        valid_until - issued
        > timedelta(seconds=policy.max_validity_seconds)
        or valid_until > parse_utc(identity_receipt.valid_until_utc)
    ):
        raise PlayerAuthorityError(
            "approval validity exceeds policy or identity evidence"
        )
    return CandidateApprovalReceipt(
        **_candidate_identity(assembly),
        policy_ref=policy.policy_ref,
        policy_digest=policy.policy_digest,
        authority_id=authority_id,
        source=CandidateApprovalSource.HUMAN_DECISION,
        authority_identity_receipt_ref=identity_receipt.ref,
        approval_event_ref=approval_event_ref,
        issued_at_utc=issued_at_utc,
        valid_until_utc=valid_until_utc,
    )


def issue_disposable_automation_approval(
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
    build_policy: BuildPolicy,
    *,
    build_policy_ref: str,
    authority_id: str,
    issued_at_utc: str,
    valid_until_utc: str,
) -> CandidateApprovalReceipt:
    """Honor automation only when both exact policies explicitly authorize it."""

    _require_approval_policy(assembly, policy)
    if not isinstance(build_policy, BuildPolicy):
        raise TypeError("build_policy must be BuildPolicy")
    if (
        policy.mode
        is not CandidateApprovalMode.PREAUTHORIZED_DISPOSABLE
        or not policy.allows_disposable_automation
        or not build_policy.disposable_sandbox
    ):
        raise PlayerAuthorityError(
            "disposable automation lacks explicit dual-policy authority"
        )
    if authority_id not in policy.authority_ids:
        raise PlayerAuthorityError(
            "authority is not named by the approval policy"
        )
    build_binding = _candidate_policy_binding(
        assembly,
        CandidatePolicyKind.BUILD,
    )
    if (
        build_binding.policy_ref != build_policy_ref
        or build_binding.policy_digest != build_policy.policy_digest
        or build_policy.project_id != assembly.plan.project_id
        or build_policy.run_id != assembly.plan.run_id
        or build_policy.base != assembly.plan.base
    ):
        raise PlayerControlError(
            "candidate does not bind the exact disposable build policy"
        )
    issued = parse_utc(issued_at_utc)
    valid_until = parse_utc(valid_until_utc)
    if (
        valid_until - issued
        > timedelta(seconds=policy.max_validity_seconds)
    ):
        raise PlayerAuthorityError(
            "automation approval exceeds policy validity"
        )
    return CandidateApprovalReceipt(
        **_candidate_identity(assembly),
        policy_ref=policy.policy_ref,
        policy_digest=policy.policy_digest,
        authority_id=authority_id,
        source=CandidateApprovalSource.PREAUTHORIZED_POLICY,
        authority_identity_receipt_ref=None,
        approval_event_ref=policy.authorization_event_ref,
        issued_at_utc=issued_at_utc,
        valid_until_utc=valid_until_utc,
    )


def validate_candidate_approval(
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
    receipt: CandidateApprovalReceipt,
    *,
    now_utc: str,
) -> None:
    _require_approval_policy(assembly, policy)
    if not isinstance(receipt, CandidateApprovalReceipt):
        raise TypeError("receipt must be CandidateApprovalReceipt")
    expected = _candidate_identity(assembly)
    if any(
        getattr(receipt, field) != value
        for field, value in expected.items()
    ):
        raise PlayerAuthorityError(
            "candidate revision or exact-base change invalidated approval"
        )
    if (
        receipt.policy_ref != policy.policy_ref
        or receipt.policy_digest != policy.policy_digest
        or receipt.authority_id not in policy.authority_ids
    ):
        raise PlayerAuthorityError(
            "approval receipt no longer matches approval policy"
        )
    if (
        policy.mode is CandidateApprovalMode.HUMAN_REQUIRED
        and receipt.source is not CandidateApprovalSource.HUMAN_DECISION
    ) or (
        policy.mode is CandidateApprovalMode.PREAUTHORIZED_DISPOSABLE
        and receipt.source
        is not CandidateApprovalSource.PREAUTHORIZED_POLICY
    ):
        raise PlayerAuthorityError("approval source does not match policy")
    now = parse_utc(now_utc)
    if (
        now < parse_utc(receipt.issued_at_utc)
        or now > parse_utc(receipt.valid_until_utc)
    ):
        raise PlayerAuthorityError("candidate approval is outside validity")


def create_revision_proposal(
    source: CandidateAssembly,
    revised: CandidateAssembly,
    *,
    kind: CandidateRevisionKind,
    authority_id: str,
    rationale: str,
    evidence_refs: tuple[str, ...],
    prior_approval: CandidateApprovalReceipt | None = None,
) -> CandidateRevisionProposal:
    """Bind a project-authored revised assembly; never invent the edit."""

    if not isinstance(source, CandidateAssembly) or not isinstance(
        revised,
        CandidateAssembly,
    ):
        raise TypeError("source and revised must be CandidateAssembly")
    if (
        source.plan.project_id != revised.plan.project_id
        or source.plan.run_id != revised.plan.run_id
        or source.plan.base != revised.plan.base
    ):
        raise PlayerControlError(
            "revision must remain on the exact project run and base"
        )
    if source.assembly_digest == revised.assembly_digest:
        raise PlayerControlError("revision cannot resubmit the same candidate")
    if prior_approval is not None and (
        prior_approval.candidate_assembly_digest
        != source.assembly_digest
        or prior_approval.plan_digest != source.plan.plan_digest
        or prior_approval.base != source.plan.base
    ):
        raise PlayerControlError(
            "approval to invalidate does not bind the source candidate"
        )
    return CandidateRevisionProposal(
        source_candidate_digest=source.assembly_digest,
        revised_candidate_digest=revised.assembly_digest,
        source_submission_id=source.submission.submission_id,
        revised_submission_id=revised.submission.submission_id,
        source_plan_digest=source.plan.plan_digest,
        revised_plan_digest=revised.plan.plan_digest,
        base=source.plan.base,
        kind=kind,
        authority_id=authority_id,
        rationale=rationale,
        invalidated_approval_ref=(
            prior_approval.ref if prior_approval is not None else None
        ),
        evidence_refs=evidence_refs,
    )


def record_candidate_preference(
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
    *,
    authority_id: str,
    selected_option_refs: tuple[str, ...] = (),
    selected_material_refs: tuple[str, ...] = (),
    aesthetic_observation_refs: tuple[str, ...] = (),
    rationale: str,
    authority_event_ref: str,
    issued_at_utc: str,
) -> CandidatePreferenceReceipt:
    _require_approval_policy(assembly, policy)
    if authority_id not in policy.authority_ids:
        raise PlayerAuthorityError(
            "preference authority is not named by the approval policy"
        )
    return CandidatePreferenceReceipt(
        candidate_assembly_digest=assembly.assembly_digest,
        submission_id=assembly.submission.submission_id,
        plan_digest=assembly.plan.plan_digest,
        base=assembly.plan.base,
        authority_id=authority_id,
        selected_option_refs=selected_option_refs,
        selected_material_refs=selected_material_refs,
        aesthetic_observation_refs=aesthetic_observation_refs,
        rationale=rationale,
        authority_event_ref=authority_event_ref,
        issued_at_utc=issued_at_utc,
    )


def open_candidate_control(
    preview: CandidatePreviewReceipt,
) -> CandidateControlState:
    if not isinstance(preview, CandidatePreviewReceipt):
        raise TypeError("preview must be CandidatePreviewReceipt")
    return CandidateControlState(
        candidate_assembly_digest=preview.candidate_assembly_digest,
        submission_id=preview.submission_id,
        plan_digest=preview.plan_digest,
        base=preview.base,
        workspace_id=preview.workspace_id,
        world=preview.world,
        status=CandidateControlStatus.PREVIEW_READY,
        sequence=0,
        preview_ref=preview.ref,
    )


def approve_candidate_control(
    state: CandidateControlState,
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
    approval: CandidateApprovalReceipt,
    *,
    now_utc: str,
) -> CandidateControlState:
    _require_control_candidate(state, assembly)
    if state.status not in {
        CandidateControlStatus.PREVIEW_READY,
        CandidateControlStatus.PAUSED,
    }:
        raise PlayerControlError(
            f"cannot approve candidate from {state.status.value}"
        )
    if state.effective_status not in {
        CandidateControlStatus.PREVIEW_READY,
        CandidateControlStatus.APPROVED,
    }:
        raise PlayerControlError(
            "cannot approve a candidate paused from "
            f"{state.effective_status.value}; a written candidate keeps "
            "its undo or reconciliation obligation"
        )
    validate_candidate_approval(
        assembly,
        policy,
        approval,
        now_utc=now_utc,
    )
    return replace(
        state,
        status=CandidateControlStatus.APPROVED,
        sequence=state.sequence + 1,
        approval_ref=approval.ref,
        reason=None,
        paused_from=None,
    )


def pause_candidate_control(
    state: CandidateControlState,
    *,
    reason: str,
) -> CandidateControlState:
    if state.status in {
        CandidateControlStatus.CANCELLED,
        CandidateControlStatus.RESTORED,
    }:
        raise PlayerControlError(
            f"cannot pause candidate from {state.status.value}"
        )
    return replace(
        state,
        status=CandidateControlStatus.PAUSED,
        sequence=state.sequence + 1,
        reason=_text(reason, "reason"),
        paused_from=(
            state.paused_from
            if state.status is CandidateControlStatus.PAUSED
            else state.status
        ),
    )


def cancel_candidate_control(
    state: CandidateControlState,
    *,
    reason: str,
) -> CandidateControlState:
    if state.effective_status in {
        CandidateControlStatus.EXECUTED,
        CandidateControlStatus.UNDO_REQUIRED,
        CandidateControlStatus.RESTORED,
        CandidateControlStatus.MANUAL_RECONCILIATION_REQUIRED,
    }:
        raise PlayerControlError(
            "a written candidate requires exact undo or reconciliation"
        )
    return replace(
        state,
        status=CandidateControlStatus.CANCELLED,
        sequence=state.sequence + 1,
        reason=_text(reason, "reason"),
        paused_from=None,
    )


def bind_world_mutation(
    state: CandidateControlState,
    assembly: CandidateAssembly,
    trace: WorldMutationTrace,
) -> CandidateControlState:
    """Record adapter evidence without claiming approval or usability."""

    _require_control_candidate(state, assembly)
    _require_trace_binding(state, trace)
    if state.status is not CandidateControlStatus.APPROVED:
        raise PlayerControlError(
            "world mutation evidence requires prior candidate approval"
        )
    written = trace.status in {
        WorldMutationStatus.WORLD_CHANGED_UNOBSERVED,
        WorldMutationStatus.WORLD_CHANGE_OBSERVED,
        WorldMutationStatus.TRANSPORT_VALIDATED,
        WorldMutationStatus.CANDIDATE_EVIDENCE_READY,
    }
    status = (
        CandidateControlStatus.EXECUTED
        if trace.status is WorldMutationStatus.CANDIDATE_EVIDENCE_READY
        else (
            CandidateControlStatus.UNDO_REQUIRED
            if written
            else CandidateControlStatus.MANUAL_RECONCILIATION_REQUIRED
        )
    )
    return replace(
        state,
        status=status,
        sequence=state.sequence + 1,
        mutation_trace_ref=f"world-mutation:{trace.trace_digest}",
        reason=f"world mutation status is {trace.status.value}",
    )


def record_exact_undo(
    state: CandidateControlState,
    trace: WorldMutationTrace,
) -> CandidateControlState:
    """Recognize M003 exact-token compensation; never perform undo here."""

    if state.effective_status not in {
        CandidateControlStatus.APPROVED,
        CandidateControlStatus.EXECUTED,
        CandidateControlStatus.UNDO_REQUIRED,
        CandidateControlStatus.MANUAL_RECONCILIATION_REQUIRED,
    }:
        raise PlayerControlError(
            f"cannot reconcile undo from {state.effective_status.value}"
        )
    _require_trace_binding(state, trace)
    if (
        trace.status is not WorldMutationStatus.COMPENSATED
        or not _exact_compensation_proven(trace)
    ):
        return replace(
            state,
            status=(
                CandidateControlStatus.MANUAL_RECONCILIATION_REQUIRED
            ),
            sequence=state.sequence + 1,
            mutation_trace_ref=f"world-mutation:{trace.trace_digest}",
            restore_evidence_ref=None,
            reason=(
                "exact-token compensation evidence is not proven; manual "
                "reconciliation is required"
            ),
            paused_from=None,
        )
    return replace(
        state,
        status=CandidateControlStatus.RESTORED,
        sequence=state.sequence + 1,
        mutation_trace_ref=f"world-mutation:{trace.trace_digest}",
        restore_evidence_ref=f"world-compensation:{trace.trace_digest}",
        reason=(
            "exact-token compensation was acknowledged; atomic rollback "
            "is not claimed"
        ),
        paused_from=None,
    )


def assess_candidate_promotion_readiness(
    assembly: CandidateAssembly,
    policy: CandidateApprovalPolicy,
    approval: CandidateApprovalReceipt,
    hard_validation: ValidationReceipt,
    commitment_monitor: CommitmentMonitorReceipt,
    *,
    now_utc: str,
    review_submission: CandidateSubmission | None = None,
    preference: CandidatePreferenceReceipt | None = None,
) -> CandidatePromotionReadiness:
    """Combine gates for handoff only; the returned value cannot commit."""

    validate_candidate_approval(
        assembly,
        policy,
        approval,
        now_utc=now_utc,
    )
    if not isinstance(hard_validation, ValidationReceipt):
        raise TypeError("hard_validation must be ValidationReceipt")
    if not isinstance(commitment_monitor, CommitmentMonitorReceipt):
        raise TypeError(
            "commitment_monitor must be CommitmentMonitorReceipt"
        )
    reviewed = (
        assembly.submission
        if review_submission is None
        else review_submission
    )
    if not isinstance(reviewed, CandidateSubmission):
        raise TypeError("review_submission must be CandidateSubmission")
    if (
        reviewed.base != assembly.submission.base
        or reviewed.workspace_id != assembly.submission.workspace_id
    ):
        raise PlayerControlError(
            "review submission targets another base or workspace"
        )
    if (
        hard_validation.submission_id != reviewed.submission_id
        or hard_validation.checked_state != assembly.plan.base
    ):
        raise PlayerControlError(
            "hard validation targets another candidate or base"
        )
    branch = commitment_monitor.branch
    if (
        commitment_monitor.candidate_id != reviewed.submission_id
        or branch.run.project_id != assembly.plan.project_id
        or branch.run.run_id != assembly.plan.run_id
        or branch.run.base != assembly.plan.base
    ):
        raise PlayerControlError(
            "commitment monitor targets another candidate branch or base"
        )
    if preference is not None and (
        preference.candidate_assembly_digest
        != assembly.assembly_digest
        or preference.submission_id != assembly.submission.submission_id
        or preference.plan_digest != assembly.plan.plan_digest
        or preference.base != assembly.plan.base
    ):
        raise PlayerControlError(
            "preference targets another candidate or plan"
        )
    blockers = []
    if not hard_validation.passed:
        blockers.append("hard_validation_failed")
    if not commitment_monitor.completion_boundary:
        blockers.append("commitment_monitor_not_at_completion_boundary")
    if not commitment_monitor.passed:
        blockers.append("commitment_monitor_failed")
    return CandidatePromotionReadiness(
        candidate_assembly_digest=assembly.assembly_digest,
        source_submission_id=assembly.submission.submission_id,
        submission_id=reviewed.submission_id,
        approval_ref=approval.ref,
        hard_validation_receipt_id=hard_validation.receipt_id,
        commitment_monitor_receipt_id=commitment_monitor.receipt_id,
        preference_ref=(preference.ref if preference is not None else None),
        ready=not blockers,
        blockers=tuple(blockers),
    )


def apply_player_clarification(
    request,
    state: OperationalMarkovState,
    *,
    now_utc: str,
    receipt: AuthorityDecisionReceipt,
    commitment_catalog: tuple[Commitment, ...] = (),
    consumed_request_ids: tuple[str, ...] = (),
) -> ClarificationResumeResult:
    """Reuse P043's authority-safe compiler instead of editing state directly."""

    return resume_from_clarification(
        request,
        state,
        now_utc=now_utc,
        receipt=receipt,
        commitment_catalog=commitment_catalog,
        consumed_request_ids=consumed_request_ids,
    )


def _require_control_candidate(
    state: CandidateControlState,
    assembly: CandidateAssembly,
) -> None:
    if not isinstance(state, CandidateControlState):
        raise TypeError("state must be CandidateControlState")
    if not isinstance(assembly, CandidateAssembly):
        raise TypeError("assembly must be CandidateAssembly")
    expected = _candidate_identity(assembly)
    if any(getattr(state, field) != value for field, value in expected.items()):
        raise PlayerControlError(
            "control state targets another candidate plan or base"
        )


def _require_trace_binding(
    state: CandidateControlState,
    trace: WorldMutationTrace,
) -> None:
    if not isinstance(state, CandidateControlState):
        raise TypeError("state must be CandidateControlState")
    if not isinstance(trace, WorldMutationTrace):
        raise TypeError("trace must be WorldMutationTrace")
    if (
        trace.base != state.base
        or trace.workspace_id != state.workspace_id
        or trace.plan_sha256 != state.plan_digest
        or not state.world.matches(trace)
    ):
        raise PlayerControlError(
            "world trace does not prove the exact candidate restore boundary"
        )


def _exact_compensation_proven(trace: WorldMutationTrace) -> bool:
    """Require the M003 token hash and acknowledgement, not a status label."""

    execute = next(
        (
            item
            for item in trace.phases
            if item.phase == "execute" and item.outcome == "acknowledged"
        ),
        None,
    )
    compensation = next(
        (
            item
            for item in trace.phases
            if item.phase == "compensate"
            and item.outcome == "acknowledged"
        ),
        None,
    )
    if execute is None or compensation is None:
        return False
    token_digest = compensation.evidence.get("undo_token_sha256")
    session_digest = compensation.evidence.get(
        "compensation_session_sha256"
    )
    return (
        execute.evidence.get("undo_token_available") is True
        and isinstance(token_digest, str)
        and len(token_digest) == 64
        and all(char in _HEX for char in token_digest.lower())
        and isinstance(session_digest, str)
        and len(session_digest) == 64
        and all(char in _HEX for char in session_digest.lower())
        and compensation.evidence.get("atomic_rollback_claimed") is False
    )
