"""Reloadable reconciliation for non-atomic external world mutations.

The adapter owns speculative world calls.  This module verifies their receipt
chain and reports what a restart may safely conclude.  It never calls an
external tool, changes canonical state, or claims that compensation is atomic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.project import (
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    ProjectVersionRef,
    RecordSink,
    RunRef,
)
from archflow.project.refs import require_identifier
from archflow.state.operational_state import require_logical_ref
from archflow.contracts.canonical import canonical_digest, canonical_json


_HEX = frozenset("0123456789abcdef")
_MAX_PHASES = 64


class WorldRecoveryError(ValueError):
    """The world receipt or canonical binding is incomplete or inconsistent."""


class WorldMutationStatus(StrEnum):
    PREPARED = "prepared"
    WRITE_UNKNOWN = "write_unknown"
    WORLD_CHANGED_UNOBSERVED = "world_changed_unobserved"
    WORLD_CHANGE_OBSERVED = "world_change_observed"
    TRANSPORT_VALIDATED = "transport_validated"
    CANDIDATE_EVIDENCE_READY = "candidate_evidence_ready"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"
    MANUAL_RECONCILIATION_REQUIRED = "manual_reconciliation_required"


class RecoveryDisposition(StrEnum):
    NO_WORLD_WRITE = "no_world_write"
    CANDIDATE_PENDING_REVIEW = "candidate_pending_review"
    ORPHANED_CANDIDATE = "orphaned_candidate"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"
    MANUAL_RECONCILIATION = "manual_reconciliation"
    MANUALLY_RECONCILED = "manually_reconciled"
    RECONCILED_COMMITTED = "reconciled_committed"


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise WorldRecoveryError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldRecoveryError(f"{field} must be non-empty text")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise WorldRecoveryError(f"{label} schema drifted")


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.state_sha256,
    }


def _base_from_dict(value: object, field: str) -> ProjectVersionRef:
    payload = _mapping(value, field)
    _exact(payload, {"project_id", "version", "state_sha256"}, field)
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


@dataclass(frozen=True, slots=True)
class MutationPhaseReceipt:
    sequence: int
    phase: str
    outcome: str
    plan_sha256: str
    world_identity: dict[str, str | None]
    session_sha256: str
    prior_phase_sha256: str | None
    evidence: dict[str, Any]
    phase_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise WorldRecoveryError(
                "phase sequence must be a non-negative integer"
            )
        _text(self.phase, "phase")
        _text(self.outcome, "outcome")
        _sha(self.plan_sha256, "phase plan_sha256")
        _world_identity(self.world_identity)
        _sha(self.session_sha256, "phase session_sha256")
        if self.prior_phase_sha256 is not None:
            _sha(self.prior_phase_sha256, "prior_phase_sha256")
        if not isinstance(self.evidence, dict):
            raise TypeError("phase evidence must be an object")
        canonical_json(self.evidence)
        _sha(self.phase_sha256, "phase_sha256")
        if self.phase_sha256 != canonical_digest(self.body_dict()):
            raise WorldRecoveryError("phase receipt digest changed")

    def body_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "phase": self.phase,
            "outcome": self.outcome,
            "plan_sha256": self.plan_sha256,
            "world_identity": dict(self.world_identity),
            "session_sha256": self.session_sha256,
            "prior_phase_sha256": self.prior_phase_sha256,
            "evidence": dict(self.evidence),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.body_dict(), "phase_sha256": self.phase_sha256}

    @classmethod
    def from_dict(cls, value: object) -> MutationPhaseReceipt:
        payload = _mapping(value, "mutation phase")
        _exact(
            payload,
            {
                "sequence",
                "phase",
                "outcome",
                "plan_sha256",
                "world_identity",
                "session_sha256",
                "prior_phase_sha256",
                "evidence",
                "phase_sha256",
            },
            "mutation phase",
        )
        return cls(
            sequence=payload["sequence"],
            phase=payload["phase"],
            outcome=payload["outcome"],
            plan_sha256=payload["plan_sha256"],
            world_identity=dict(
                _mapping(payload["world_identity"], "world identity")
            ),
            session_sha256=payload["session_sha256"],
            prior_phase_sha256=payload["prior_phase_sha256"],
            evidence=dict(_mapping(payload["evidence"], "phase evidence")),
            phase_sha256=payload["phase_sha256"],
        )


def _world_identity(value: object) -> dict[str, str | None]:
    payload = _mapping(value, "world identity")
    _exact(payload, {"world_id", "dimension_id"}, "world identity")
    world_id = payload["world_id"]
    dimension_id = payload["dimension_id"]
    _text(world_id, "world_id")
    if dimension_id is not None:
        _text(dimension_id, "dimension_id")
    return {
        "world_id": world_id,
        "dimension_id": dimension_id,
    }


@dataclass(frozen=True, slots=True)
class WorldMutationTrace:
    mutation_id: str
    base: ProjectVersionRef
    workspace_id: str
    plan_sha256: str
    server: dict[str, str]
    world_identity: dict[str, str | None]
    session_sha256: str
    preview_plan_id: str
    status: WorldMutationStatus
    phases: tuple[MutationPhaseReceipt, ...]
    phase_head_sha256: str

    SCHEMA = "MinecraftMutationReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.mutation_id, "mutation_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        require_identifier(self.workspace_id, "workspace_id")
        _sha(self.plan_sha256, "plan_sha256")
        if not isinstance(self.server, dict):
            raise TypeError("server must be an object")
        canonical_json(self.server)
        _world_identity(self.world_identity)
        _sha(self.session_sha256, "session_sha256")
        _text(self.preview_plan_id, "preview_plan_id")
        if not isinstance(self.status, WorldMutationStatus):
            raise TypeError("status must be WorldMutationStatus")
        if (
            not isinstance(self.phases, tuple)
            or not self.phases
            or len(self.phases) > _MAX_PHASES
            or any(
                not isinstance(item, MutationPhaseReceipt)
                for item in self.phases
            )
        ):
            raise WorldRecoveryError(
                "mutation trace requires a bounded phase chain"
            )
        for index, phase in enumerate(self.phases):
            expected_prior = (
                self.phases[index - 1].phase_sha256
                if index
                else None
            )
            if (
                phase.sequence != index
                or phase.prior_phase_sha256 != expected_prior
                or phase.plan_sha256 != self.plan_sha256
                or phase.world_identity != self.world_identity
                or phase.session_sha256 != self.session_sha256
            ):
                raise WorldRecoveryError(
                    "mutation phase chain or exact binding changed"
                )
        _sha(self.phase_head_sha256, "phase_head_sha256")
        if self.phase_head_sha256 != self.phases[-1].phase_sha256:
            raise WorldRecoveryError("mutation phase head changed")
        _require_status_evidence(self.status, self.phases)

    @property
    def exact_base_proven(self) -> bool:
        return self.base.state_sha256 is not None

    @property
    def trace_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> WorldMutationTrace:
        payload = _mapping(value, "world mutation receipt")
        _exact(
            payload,
            {
                "schema",
                "mutation_id",
                "base_state",
                "workspace_id",
                "plan_sha256",
                "server",
                "world_identity",
                "session_sha256",
                "preview_plan_id",
                "status",
                "phases",
                "phase_head_sha256",
                "hard_usability_evaluated",
                "candidate_accepted",
                "canonical_state_mutated",
                "cross_system_atomicity_claimed",
            },
            "world mutation receipt",
        )
        phases = payload["phases"]
        if not isinstance(phases, list):
            raise TypeError("mutation phases must be a list")
        if (
            payload["schema"] != cls.SCHEMA
            or payload["hard_usability_evaluated"] is not False
            or payload["candidate_accepted"] is not False
            or payload["canonical_state_mutated"] is not False
            or payload["cross_system_atomicity_claimed"] is not False
        ):
            raise WorldRecoveryError(
                "mutation receipt acquired acceptance or atomicity authority"
            )
        return cls(
            mutation_id=payload["mutation_id"],
            base=_base_from_dict(payload["base_state"], "base_state"),
            workspace_id=payload["workspace_id"],
            plan_sha256=payload["plan_sha256"],
            server=dict(_mapping(payload["server"], "server")),
            world_identity=_world_identity(payload["world_identity"]),
            session_sha256=payload["session_sha256"],
            preview_plan_id=payload["preview_plan_id"],
            status=WorldMutationStatus(payload["status"]),
            phases=tuple(
                MutationPhaseReceipt.from_dict(item) for item in phases
            ),
            phase_head_sha256=payload["phase_head_sha256"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "mutation_id": self.mutation_id,
            "base_state": _base_to_dict(self.base),
            "workspace_id": self.workspace_id,
            "plan_sha256": self.plan_sha256,
            "server": dict(self.server),
            "world_identity": dict(self.world_identity),
            "session_sha256": self.session_sha256,
            "preview_plan_id": self.preview_plan_id,
            "status": self.status.value,
            "phases": [item.to_dict() for item in self.phases],
            "phase_head_sha256": self.phase_head_sha256,
            "hard_usability_evaluated": False,
            "candidate_accepted": False,
            "canonical_state_mutated": False,
            "cross_system_atomicity_claimed": False,
        }


def _require_status_evidence(
    status: WorldMutationStatus,
    phases: tuple[MutationPhaseReceipt, ...],
) -> None:
    pairs = {(item.phase, item.outcome) for item in phases}
    requirements = {
        WorldMutationStatus.CANDIDATE_EVIDENCE_READY: {
            ("prepare", "prepared"),
            ("execute", "acknowledged"),
            ("observe", "captured"),
            ("validate", "transport_evidence_bound"),
            ("finalize", "candidate_evidence_ready"),
        },
        WorldMutationStatus.COMPENSATED: {
            ("execute", "acknowledged"),
            ("compensate", "acknowledged"),
            ("finalize", "failed"),
        },
        WorldMutationStatus.COMPENSATION_FAILED: {
            ("execute", "acknowledged"),
            ("compensate", "failed"),
            ("finalize", "failed"),
        },
        WorldMutationStatus.MANUAL_RECONCILIATION_REQUIRED: {
            ("execute", "acknowledged"),
            ("compensate", "not_attempted"),
            ("finalize", "failed"),
        },
        WorldMutationStatus.WRITE_UNKNOWN: {
            ("execute", "started"),
        },
    }
    required = requirements.get(status)
    if required is not None and not required <= pairs:
        raise WorldRecoveryError(
            f"{status.value} lacks its required phase evidence"
        )


@dataclass(frozen=True, slots=True)
class CanonicalCommitEvidence:
    submission_id: str
    from_state: ProjectVersionRef
    to_state: ProjectVersionRef
    commit_receipt_ref: str

    SCHEMA = "CanonicalCommitEvidence@1"

    def __post_init__(self) -> None:
        require_identifier(self.submission_id, "submission_id")
        if not isinstance(self.from_state, ProjectVersionRef) or not isinstance(
            self.to_state,
            ProjectVersionRef,
        ):
            raise TypeError("commit states must be ProjectVersionRef values")
        self.from_state.require_digest()
        self.to_state.require_digest()
        if (
            self.from_state.project_id != self.to_state.project_id
            or self.to_state.version != self.from_state.version + 1
        ):
            raise WorldRecoveryError(
                "commit evidence must advance one project version"
            )
        require_logical_ref(
            self.commit_receipt_ref,
            "commit_receipt_ref",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "submission_id": self.submission_id,
            "from_state": _base_to_dict(self.from_state),
            "to_state": _base_to_dict(self.to_state),
            "commit_receipt_ref": self.commit_receipt_ref,
        }

    @classmethod
    def from_dict(cls, value: object) -> CanonicalCommitEvidence:
        payload = _mapping(value, "canonical commit evidence")
        _exact(
            payload,
            {
                "schema",
                "submission_id",
                "from_state",
                "to_state",
                "commit_receipt_ref",
            },
            "canonical commit evidence",
        )
        if payload["schema"] != cls.SCHEMA:
            raise WorldRecoveryError("commit evidence schema changed")
        return cls(
            submission_id=payload["submission_id"],
            from_state=_base_from_dict(payload["from_state"], "from_state"),
            to_state=_base_from_dict(payload["to_state"], "to_state"),
            commit_receipt_ref=payload["commit_receipt_ref"],
        )


@dataclass(frozen=True, slots=True)
class WorldRecoveryOutcome:
    trace_digest: str
    disposition: RecoveryDisposition
    checked_head: ProjectVersionRef
    candidate_record_ref: str | None
    candidate_submission_id: str | None
    candidate_plan_sha256: str | None
    commit_evidence: CanonicalCommitEvidence | None
    manual_resolution_ref: str | None
    reasons: tuple[str, ...]

    SCHEMA = "WorldRecoveryOutcome@1"

    def __post_init__(self) -> None:
        _sha(self.trace_digest, "trace_digest")
        if not isinstance(self.disposition, RecoveryDisposition):
            raise TypeError("disposition must be RecoveryDisposition")
        if not isinstance(self.checked_head, ProjectVersionRef):
            raise TypeError("checked_head must be ProjectVersionRef")
        self.checked_head.require_digest()
        for value, field in (
            (self.candidate_record_ref, "candidate_record_ref"),
            (self.manual_resolution_ref, "manual_resolution_ref"),
        ):
            if value is not None:
                require_logical_ref(value, field)
        if self.candidate_submission_id is not None:
            require_identifier(
                self.candidate_submission_id,
                "candidate_submission_id",
            )
        if self.candidate_plan_sha256 is not None:
            _sha(
                self.candidate_plan_sha256,
                "candidate_plan_sha256",
            )
        if self.commit_evidence is not None and not isinstance(
            self.commit_evidence,
            CanonicalCommitEvidence,
        ):
            raise TypeError(
                "commit_evidence must be CanonicalCommitEvidence or None"
            )
        if (
            not isinstance(self.reasons, tuple)
            or not self.reasons
            or any(not isinstance(item, str) or not item for item in self.reasons)
        ):
            raise WorldRecoveryError("recovery reasons must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "trace_digest": self.trace_digest,
            "disposition": self.disposition.value,
            "checked_head": _base_to_dict(self.checked_head),
            "candidate_record_ref": self.candidate_record_ref,
            "candidate_submission_id": self.candidate_submission_id,
            "candidate_plan_sha256": self.candidate_plan_sha256,
            "commit_evidence": (
                self.commit_evidence.to_dict()
                if self.commit_evidence is not None
                else None
            ),
            "manual_resolution_ref": self.manual_resolution_ref,
            "reasons": list(self.reasons),
            "canonical_advance_performed": False,
            "external_world_mutation_performed": False,
            "cross_system_atomicity_claimed": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> WorldRecoveryOutcome:
        payload = _mapping(value, "world recovery outcome")
        _exact(
            payload,
            {
                "schema",
                "trace_digest",
                "disposition",
                "checked_head",
                "candidate_record_ref",
                "candidate_submission_id",
                "candidate_plan_sha256",
                "commit_evidence",
                "manual_resolution_ref",
                "reasons",
                "canonical_advance_performed",
                "external_world_mutation_performed",
                "cross_system_atomicity_claimed",
            },
            "world recovery outcome",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["canonical_advance_performed"] is not False
            or payload["external_world_mutation_performed"] is not False
            or payload["cross_system_atomicity_claimed"] is not False
        ):
            raise WorldRecoveryError(
                "recovery outcome acquired mutation or atomicity authority"
            )
        commit = payload["commit_evidence"]
        reasons = payload["reasons"]
        if not isinstance(reasons, list):
            raise TypeError("recovery reasons must be a list")
        return cls(
            trace_digest=payload["trace_digest"],
            disposition=RecoveryDisposition(payload["disposition"]),
            checked_head=_base_from_dict(
                payload["checked_head"],
                "checked_head",
            ),
            candidate_record_ref=payload["candidate_record_ref"],
            candidate_submission_id=payload["candidate_submission_id"],
            candidate_plan_sha256=payload["candidate_plan_sha256"],
            commit_evidence=(
                CanonicalCommitEvidence.from_dict(commit)
                if commit is not None
                else None
            ),
            manual_resolution_ref=payload["manual_resolution_ref"],
            reasons=tuple(reasons),
        )


def reconcile_world_after_restart(
    trace: WorldMutationTrace,
    *,
    current_head: ProjectVersionRef,
    candidate_record_ref: str | None = None,
    candidate_submission_id: str | None = None,
    candidate_plan_sha256: str | None = None,
    commit_evidence: CanonicalCommitEvidence | None = None,
    manual_resolution_ref: str | None = None,
) -> WorldRecoveryOutcome:
    """Classify exact evidence; never repair the world or canonical store."""

    if not isinstance(trace, WorldMutationTrace):
        raise TypeError("trace must be WorldMutationTrace")
    if not isinstance(current_head, ProjectVersionRef):
        raise TypeError("current_head must be ProjectVersionRef")
    current_head.require_digest()
    if current_head.project_id != trace.base.project_id:
        raise WorldRecoveryError(
            "world trace and canonical head belong to different projects"
        )
    if candidate_record_ref is not None:
        require_logical_ref(candidate_record_ref, "candidate_record_ref")
    if candidate_submission_id is not None:
        require_identifier(
            candidate_submission_id,
            "candidate_submission_id",
        )
    if candidate_plan_sha256 is not None:
        _sha(candidate_plan_sha256, "candidate_plan_sha256")
    if manual_resolution_ref is not None:
        require_logical_ref(
            manual_resolution_ref,
            "manual_resolution_ref",
        )

    disposition = RecoveryDisposition.MANUAL_RECONCILIATION
    reasons: tuple[str, ...]
    if not trace.exact_base_proven:
        reasons = (
            "receipt lacks an exact canonical-state digest",
        )
    elif manual_resolution_ref is not None:
        disposition = RecoveryDisposition.MANUALLY_RECONCILED
        reasons = (
            "a separately authorized manual resolution receipt is present",
            "no automatic world or canonical mutation was performed",
        )
    elif trace.status is WorldMutationStatus.PREPARED:
        if current_head == trace.base:
            disposition = RecoveryDisposition.NO_WORLD_WRITE
            reasons = ("execution never started",)
        else:
            reasons = ("canonical head changed after preparation",)
    elif trace.status is WorldMutationStatus.COMPENSATED:
        if current_head == trace.base:
            disposition = RecoveryDisposition.COMPENSATED
            reasons = (
                "exact-token compensation was acknowledged",
                "atomic rollback is not claimed",
            )
        else:
            reasons = (
                "canonical head changed despite compensated world receipt",
            )
    elif trace.status is WorldMutationStatus.COMPENSATION_FAILED:
        disposition = RecoveryDisposition.COMPENSATION_FAILED
        reasons = (
            "world compensation failed",
            "canonical advancement is forbidden",
        )
    elif trace.status is WorldMutationStatus.CANDIDATE_EVIDENCE_READY:
        exact_candidate = (
            candidate_record_ref is not None
            and candidate_submission_id is not None
            and candidate_plan_sha256 == trace.plan_sha256
        )
        if (
            commit_evidence is not None
            and exact_candidate
            and commit_evidence.submission_id == candidate_submission_id
            and commit_evidence.from_state == trace.base
            and commit_evidence.to_state == current_head
        ):
            disposition = RecoveryDisposition.RECONCILED_COMMITTED
            reasons = (
                "world candidate and canonical commit share exact submission "
                "and base receipts",
                "cross-system atomicity is not claimed",
            )
        elif current_head != trace.base:
            reasons = (
                "canonical head advanced without an exact matching commit "
                "receipt",
            )
        elif candidate_record_ref is None:
            disposition = RecoveryDisposition.ORPHANED_CANDIDATE
            reasons = (
                "world evidence exists without a durable candidate record",
            )
        elif not exact_candidate:
            reasons = (
                "durable candidate lacks an exact submission or plan binding",
            )
        else:
            disposition = RecoveryDisposition.CANDIDATE_PENDING_REVIEW
            reasons = (
                "durable candidate evidence awaits independent review",
            )
    else:
        reasons = (
            f"world mutation remains {trace.status.value}",
            "canonical advancement is forbidden",
        )
    return WorldRecoveryOutcome(
        trace_digest=trace.trace_digest,
        disposition=disposition,
        checked_head=current_head,
        candidate_record_ref=candidate_record_ref,
        candidate_submission_id=candidate_submission_id,
        candidate_plan_sha256=candidate_plan_sha256,
        commit_evidence=commit_evidence,
        manual_resolution_ref=manual_resolution_ref,
        reasons=reasons,
    )


@dataclass(frozen=True, slots=True)
class WorldRecoveryArchive:
    trace: WorldMutationTrace
    outcome: WorldRecoveryOutcome

    SCHEMA = "WorldRecoveryArchive@1"

    def __post_init__(self) -> None:
        if not isinstance(self.trace, WorldMutationTrace):
            raise TypeError("trace must be WorldMutationTrace")
        if not isinstance(self.outcome, WorldRecoveryOutcome):
            raise TypeError("outcome must be WorldRecoveryOutcome")
        if self.outcome.trace_digest != self.trace.trace_digest:
            raise WorldRecoveryError(
                "recovery outcome belongs to another world trace"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "trace": self.trace.to_dict(),
            "outcome": self.outcome.to_dict(),
            "canonical_write_authority": False,
            "external_world_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> WorldRecoveryArchive:
        payload = _mapping(value, "world recovery archive")
        _exact(
            payload,
            {
                "schema",
                "trace",
                "outcome",
                "canonical_write_authority",
                "external_world_write_authority",
            },
            "world recovery archive",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["canonical_write_authority"] is not False
            or payload["external_world_write_authority"] is not False
        ):
            raise WorldRecoveryError(
                "world recovery archive acquired mutation authority"
            )
        return cls(
            trace=WorldMutationTrace.from_dict(payload["trace"]),
            outcome=WorldRecoveryOutcome.from_dict(payload["outcome"]),
        )


class WorldRecoveryLoader(Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


def persist_world_recovery(
    sink: RecordSink,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    archive: WorldRecoveryArchive,
) -> ProjectRecordRef:
    if (
        destination.area is not PersistenceArea.RUN_RECOVERY
        or destination.run_id != run.run_id
    ):
        raise WorldRecoveryError(
            "world recovery requires its run recovery destination"
        )
    if (
        archive.trace.base != run.base
        or archive.trace.base.project_id != run.project_id
    ):
        raise WorldRecoveryError(
            "world recovery archive belongs to another run base"
        )
    return sink.put_json(
        run=run,
        destination=destination,
        record_kind="world-recovery",
        payload=archive.to_dict(),
    )


def load_world_recovery(
    loader: WorldRecoveryLoader,
    ref: ProjectRecordRef,
) -> WorldRecoveryArchive:
    archive = WorldRecoveryArchive.from_dict(loader.load_json(ref))
    if archive.trace.base.project_id != ref.project_id:
        raise WorldRecoveryError(
            "world recovery record belongs to another project"
        )
    return archive
