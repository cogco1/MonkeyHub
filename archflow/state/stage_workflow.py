"""Authority-free project stage workflows and exact run envelopes.

``ProjectStageWorkflow@1`` declares a finite, ordered stage sequence.  A
``StageRunEnvelope@1`` binds one run to exactly one stage in that workflow and,
after stage zero, to the exact retained envelope and retained SATISFIED exit
binding for the immediately previous stage.  These records are guards for
orchestration; they never select design, accept a stage, mutate geometry,
persist state, promote a candidate, or write canonical state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.contracts.authority import (
    DEFAULT_AUTHORITY_FIELDS,
    no_authority,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.project.refs import require_identifier
from archflow.state.design_maturity import DESIGN_PHASES, DesignPhase
from archflow.state.operational_state import (
    DesignObligation,
    ObligationStatus,
    require_local_id,
    require_logical_ref,
)


_AUTHORITY_FIELDS = DEFAULT_AUTHORITY_FIELDS
_AUTHORITY_KEYS = frozenset(_AUTHORITY_FIELDS)
_MAX_STAGES = 256
_MAX_REQUIREMENTS = 4096


class StageWorkflowError(ValueError):
    """A workflow or run envelope is incomplete, stale, or cross-scoped."""


class StageExitStatus(StrEnum):
    """Only a completed independent close may feed the next stage."""

    SATISFIED = "SATISFIED"


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(
    value: Mapping[str, object],
    expected: frozenset[str] | set[str],
    field: str,
) -> None:
    if set(value) != set(expected):
        raise StageWorkflowError(f"{field} schema drifted")


def _identifier_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_REQUIREMENTS:
        raise StageWorkflowError(f"{field} exceeds bounded item count")
    for item in value:
        require_local_id(item, field)
    if tuple(sorted(set(value))) != value:
        raise StageWorkflowError(f"{field} must be sorted and unique")
    return value


def _logical_ref_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(value) > _MAX_REQUIREMENTS:
        raise StageWorkflowError(f"{field} exceeds bounded item count")
    for item in value:
        require_logical_ref(item, field)
    if tuple(sorted(set(value))) != value:
        raise StageWorkflowError(f"{field} must be sorted and unique")
    return value


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _stage_index(value: object, field: str = "stage_index") -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise StageWorkflowError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ProjectStage:
    """One ordered workflow stage; this is nested, not a retained record."""

    stage_id: str
    stage_index: int
    phase: DesignPhase
    required_roles: tuple[str, ...]
    required_checks: tuple[str, ...]
    close_obligation_id: str

    RECORD_KEYS = frozenset(
        {
            "stage_id",
            "stage_index",
            "phase",
            "required_roles",
            "required_checks",
            "close_obligation_id",
        }
    )

    def __post_init__(self) -> None:
        require_identifier(self.stage_id, "stage_id")
        _stage_index(self.stage_index)
        if not isinstance(self.phase, DesignPhase):
            raise TypeError("phase must be a DesignPhase")
        _identifier_tuple(self.required_roles, "required_roles")
        _identifier_tuple(self.required_checks, "required_checks")
        require_local_id(self.close_obligation_id, "close_obligation_id")

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "phase": self.phase.value,
            "required_roles": list(self.required_roles),
            "required_checks": list(self.required_checks),
            "close_obligation_id": self.close_obligation_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProjectStage":
        payload = _mapping(value, "project stage")
        _exact(payload, cls.RECORD_KEYS, "project stage")
        return cls(
            stage_id=payload["stage_id"],
            stage_index=payload["stage_index"],
            phase=DesignPhase(payload["phase"]),
            required_roles=_string_list(
                payload["required_roles"], "required_roles"
            ),
            required_checks=_string_list(
                payload["required_checks"], "required_checks"
            ),
            close_obligation_id=payload["close_obligation_id"],
        )


@dataclass(frozen=True, slots=True)
class ProjectStageWorkflow:
    """The complete 0..N stage sequence for one project."""

    project_id: str
    workflow_id: str
    stages: tuple[ProjectStage, ...]
    basis_refs: tuple[str, ...] = ()

    SCHEMA = "ProjectStageWorkflow@1"
    RECORD_KEYS = frozenset(
        {
            "schema",
            "project_id",
            "workflow_id",
            "stages",
            "basis_refs",
        }
    ) | _AUTHORITY_KEYS

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.workflow_id, "workflow_id")
        if not isinstance(self.stages, tuple):
            raise TypeError("stages must be a tuple")
        if not self.stages:
            raise StageWorkflowError("workflow must contain at least stage 0")
        if len(self.stages) > _MAX_STAGES:
            raise StageWorkflowError("workflow exceeds bounded stage count")
        if any(not isinstance(item, ProjectStage) for item in self.stages):
            raise TypeError("stages must contain ProjectStage values")
        expected_indices = tuple(range(len(self.stages)))
        actual_indices = tuple(item.stage_index for item in self.stages)
        if actual_indices != expected_indices:
            raise StageWorkflowError(
                "workflow stages must be ordered contiguously from 0"
            )
        stage_ids = tuple(item.stage_id for item in self.stages)
        if len(stage_ids) != len(set(stage_ids)):
            raise StageWorkflowError("workflow stage_ids must be unique")
        close_ids = tuple(item.close_obligation_id for item in self.stages)
        if len(close_ids) != len(set(close_ids)):
            raise StageWorkflowError(
                "workflow close_obligation_ids must be unique"
            )
        phase_positions = tuple(
            DESIGN_PHASES.index(item.phase) for item in self.stages
        )
        if phase_positions != tuple(sorted(phase_positions)):
            raise StageWorkflowError(
                "workflow phases must be non-decreasing"
            )
        _logical_ref_tuple(self.basis_refs, "basis_refs")

    @property
    def workflow_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def stage_at(self, stage_index: int) -> ProjectStage:
        index = _stage_index(stage_index)
        if index >= len(self.stages):
            raise StageWorkflowError(
                f"stage_index {index} is outside workflow {self.workflow_id!r}"
            )
        return self.stages[index]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "stages": [item.to_dict() for item in self.stages],
            "basis_refs": list(self.basis_refs),
            **no_authority(_AUTHORITY_FIELDS),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProjectStageWorkflow":
        payload = _mapping(value, "project stage workflow")
        _exact(payload, cls.RECORD_KEYS, "project stage workflow")
        if payload["schema"] != cls.SCHEMA:
            raise StageWorkflowError("project stage workflow schema changed")
        stages = payload["stages"]
        if not isinstance(stages, list):
            raise TypeError("stages must be a list")
        return cls(
            project_id=payload["project_id"],
            workflow_id=payload["workflow_id"],
            stages=tuple(ProjectStage.from_dict(item) for item in stages),
            basis_refs=_string_list(payload["basis_refs"], "basis_refs"),
        )


@dataclass(frozen=True, slots=True)
class StageExitBinding:
    """Authority-free proof pointer for one independently satisfied close.

    The binding does not itself accept a stage.  It makes a successor name
    the exact retained closure and the exact predecessor envelope/state that
    closure evaluated.
    """

    project_id: str
    run_id: str
    base_version: int
    base_state_sha256: str
    branch_id: str
    branch_epoch: int
    stage_id: str
    stage_index: int
    workflow_ref: str
    workflow_digest: str
    envelope_ref: str
    envelope_digest: str
    close_obligation_id: str
    subject_ref: str
    state_digest: str
    closure_ref: str
    closure_digest: str
    status: StageExitStatus = StageExitStatus.SATISFIED

    SCHEMA = "StageExitBinding@1"
    RECORD_KEYS = frozenset(
        {
            "schema",
            "project_id",
            "run_id",
            "base",
            "branch_id",
            "branch_epoch",
            "stage_id",
            "stage_index",
            "workflow_ref",
            "workflow_digest",
            "envelope_ref",
            "envelope_digest",
            "close_obligation_id",
            "subject_ref",
            "state_digest",
            "closure_ref",
            "closure_digest",
            "status",
        }
    ) | _AUTHORITY_KEYS

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "exit project_id")
        require_identifier(self.run_id, "exit run_id")
        _stage_index(self.base_version, "exit base_version")
        object.__setattr__(
            self,
            "base_state_sha256",
            require_sha256(
                self.base_state_sha256,
                "exit base_state_sha256",
            ),
        )
        require_identifier(self.branch_id, "exit branch_id")
        _stage_index(self.branch_epoch, "exit branch_epoch")
        require_identifier(self.stage_id, "exit stage_id")
        _stage_index(self.stage_index, "exit stage_index")
        require_logical_ref(self.workflow_ref, "exit workflow_ref")
        require_logical_ref(self.envelope_ref, "exit envelope_ref")
        require_local_id(
            self.close_obligation_id,
            "exit close_obligation_id",
        )
        require_logical_ref(self.subject_ref, "exit subject_ref")
        require_logical_ref(self.closure_ref, "exit closure_ref")
        for field in (
            "workflow_digest",
            "envelope_digest",
            "state_digest",
            "closure_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), f"exit {field}"),
            )
        if self.status is not StageExitStatus.SATISFIED:
            raise StageWorkflowError(
                "stage exit binding must be SATISFIED"
            )

    @classmethod
    def bind(
        cls,
        envelope: "StageRunEnvelope",
        *,
        envelope_ref: str,
        closure_ref: str,
        closure_digest: str,
    ) -> "StageExitBinding":
        if not isinstance(envelope, StageRunEnvelope):
            raise TypeError("exit envelope must be a StageRunEnvelope")
        return cls(
            project_id=envelope.project_id,
            run_id=envelope.run_id,
            base_version=envelope.base_version,
            base_state_sha256=envelope.base_state_sha256,
            branch_id=envelope.branch_id,
            branch_epoch=envelope.branch_epoch,
            stage_id=envelope.stage_id,
            stage_index=envelope.stage_index,
            workflow_ref=envelope.workflow_ref,
            workflow_digest=envelope.workflow_digest,
            envelope_ref=envelope_ref,
            envelope_digest=envelope.envelope_digest,
            close_obligation_id=envelope.close_obligation.obligation_id,
            subject_ref=envelope.subject_ref,
            state_digest=envelope.state_digest,
            closure_ref=closure_ref,
            closure_digest=closure_digest,
        )

    @property
    def exit_digest(self) -> str:
        """Canonical digest of the retained ``StageExitBinding@1`` record."""

        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.project_id,
                "version": self.base_version,
                "state_sha256": self.base_state_sha256,
            },
            "branch_id": self.branch_id,
            "branch_epoch": self.branch_epoch,
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "workflow_ref": self.workflow_ref,
            "workflow_digest": self.workflow_digest,
            "envelope_ref": self.envelope_ref,
            "envelope_digest": self.envelope_digest,
            "close_obligation_id": self.close_obligation_id,
            "subject_ref": self.subject_ref,
            "state_digest": self.state_digest,
            "closure_ref": self.closure_ref,
            "closure_digest": self.closure_digest,
            "status": self.status.value,
            **no_authority(_AUTHORITY_FIELDS),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageExitBinding":
        payload = _mapping(value, "stage exit binding")
        _exact(payload, cls.RECORD_KEYS, "stage exit binding")
        if payload["schema"] != cls.SCHEMA:
            raise StageWorkflowError("stage exit binding schema changed")
        base = _mapping(payload["base"], "exit base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "exit base",
        )
        if base["project_id"] != payload["project_id"]:
            raise StageWorkflowError(
                "stage exit base belongs to another project"
            )
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base_version=base["version"],
            base_state_sha256=base["state_sha256"],
            branch_id=payload["branch_id"],
            branch_epoch=payload["branch_epoch"],
            stage_id=payload["stage_id"],
            stage_index=payload["stage_index"],
            workflow_ref=payload["workflow_ref"],
            workflow_digest=payload["workflow_digest"],
            envelope_ref=payload["envelope_ref"],
            envelope_digest=payload["envelope_digest"],
            close_obligation_id=payload["close_obligation_id"],
            subject_ref=payload["subject_ref"],
            state_digest=payload["state_digest"],
            closure_ref=payload["closure_ref"],
            closure_digest=payload["closure_digest"],
            status=StageExitStatus(payload["status"]),
        )


@dataclass(frozen=True, slots=True)
class StageRunPredecessor:
    """Exact retained predecessor envelope and exit bound into a successor."""

    project_id: str
    run_id: str
    base_version: int
    base_state_sha256: str
    branch_id: str
    branch_epoch: int
    stage_id: str
    stage_index: int
    envelope_ref: str
    envelope_digest: str
    workflow_ref: str
    workflow_digest: str
    close_obligation_id: str
    subject_ref: str
    state_digest: str
    exit_binding_ref: str
    exit_binding: StageExitBinding

    RECORD_KEYS = frozenset(
        {
            "project_id",
            "run_id",
            "base",
            "branch_id",
            "branch_epoch",
            "stage_id",
            "stage_index",
            "envelope_ref",
            "envelope_digest",
            "workflow_ref",
            "workflow_digest",
            "close_obligation_id",
            "subject_ref",
            "state_digest",
            "exit_binding_ref",
            "exit_binding",
        }
    )

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "predecessor project_id")
        require_identifier(self.run_id, "predecessor run_id")
        _stage_index(self.base_version, "predecessor base_version")
        object.__setattr__(
            self,
            "base_state_sha256",
            require_sha256(
                self.base_state_sha256,
                "predecessor base_state_sha256",
            ),
        )
        require_identifier(self.branch_id, "predecessor branch_id")
        _stage_index(self.branch_epoch, "predecessor branch_epoch")
        require_identifier(self.stage_id, "predecessor stage_id")
        _stage_index(self.stage_index, "predecessor stage_index")
        require_logical_ref(self.envelope_ref, "predecessor envelope_ref")
        require_logical_ref(self.workflow_ref, "predecessor workflow_ref")
        require_local_id(
            self.close_obligation_id,
            "predecessor close_obligation_id",
        )
        require_logical_ref(self.subject_ref, "predecessor subject_ref")
        require_logical_ref(
            self.exit_binding_ref,
            "predecessor exit_binding_ref",
        )
        object.__setattr__(
            self,
            "envelope_digest",
            require_sha256(
                self.envelope_digest, "predecessor envelope_digest"
            ),
        )
        object.__setattr__(
            self,
            "workflow_digest",
            require_sha256(
                self.workflow_digest, "predecessor workflow_digest"
            ),
        )
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "predecessor state_digest"),
        )
        if not isinstance(self.exit_binding, StageExitBinding):
            raise TypeError(
                "predecessor exit_binding must be a StageExitBinding"
            )
        expected_exit_identity = (
            self.project_id,
            self.run_id,
            self.base_version,
            self.base_state_sha256,
            self.branch_id,
            self.branch_epoch,
            self.stage_id,
            self.stage_index,
            self.workflow_ref,
            self.workflow_digest,
            self.envelope_ref,
            self.envelope_digest,
            self.close_obligation_id,
            self.subject_ref,
            self.state_digest,
        )
        actual_exit_identity = (
            self.exit_binding.project_id,
            self.exit_binding.run_id,
            self.exit_binding.base_version,
            self.exit_binding.base_state_sha256,
            self.exit_binding.branch_id,
            self.exit_binding.branch_epoch,
            self.exit_binding.stage_id,
            self.exit_binding.stage_index,
            self.exit_binding.workflow_ref,
            self.exit_binding.workflow_digest,
            self.exit_binding.envelope_ref,
            self.exit_binding.envelope_digest,
            self.exit_binding.close_obligation_id,
            self.exit_binding.subject_ref,
            self.exit_binding.state_digest,
        )
        if actual_exit_identity != expected_exit_identity:
            raise StageWorkflowError(
                "predecessor exit binding does not match the exact envelope"
            )

    @classmethod
    def bind(
        cls,
        envelope: "StageRunEnvelope",
        *,
        envelope_ref: str,
        exit_binding_ref: str,
        exit_binding: StageExitBinding,
    ) -> "StageRunPredecessor":
        if not isinstance(envelope, StageRunEnvelope):
            raise TypeError("predecessor envelope must be a StageRunEnvelope")
        require_stage_exit_binding(
            envelope,
            exit_binding,
            envelope_ref=envelope_ref,
        )
        require_logical_ref(
            exit_binding_ref,
            "predecessor exit_binding_ref",
        )
        return cls(
            project_id=envelope.project_id,
            run_id=envelope.run_id,
            base_version=envelope.base_version,
            base_state_sha256=envelope.base_state_sha256,
            branch_id=envelope.branch_id,
            branch_epoch=envelope.branch_epoch,
            stage_id=envelope.stage_id,
            stage_index=envelope.stage_index,
            envelope_ref=envelope_ref,
            envelope_digest=envelope.envelope_digest,
            workflow_ref=envelope.workflow_ref,
            workflow_digest=envelope.workflow_digest,
            close_obligation_id=envelope.close_obligation.obligation_id,
            subject_ref=envelope.subject_ref,
            state_digest=envelope.state_digest,
            exit_binding_ref=exit_binding_ref,
            exit_binding=exit_binding,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.project_id,
                "version": self.base_version,
                "state_sha256": self.base_state_sha256,
            },
            "branch_id": self.branch_id,
            "branch_epoch": self.branch_epoch,
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "envelope_ref": self.envelope_ref,
            "envelope_digest": self.envelope_digest,
            "workflow_ref": self.workflow_ref,
            "workflow_digest": self.workflow_digest,
            "close_obligation_id": self.close_obligation_id,
            "subject_ref": self.subject_ref,
            "state_digest": self.state_digest,
            "exit_binding_ref": self.exit_binding_ref,
            "exit_binding": self.exit_binding.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageRunPredecessor":
        payload = _mapping(value, "stage run predecessor")
        _exact(payload, cls.RECORD_KEYS, "stage run predecessor")
        base = _mapping(payload["base"], "predecessor base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "predecessor base",
        )
        if base["project_id"] != payload["project_id"]:
            raise StageWorkflowError(
                "predecessor base belongs to another project"
            )
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base_version=base["version"],
            base_state_sha256=base["state_sha256"],
            branch_id=payload["branch_id"],
            branch_epoch=payload["branch_epoch"],
            stage_id=payload["stage_id"],
            stage_index=payload["stage_index"],
            envelope_ref=payload["envelope_ref"],
            envelope_digest=payload["envelope_digest"],
            workflow_ref=payload["workflow_ref"],
            workflow_digest=payload["workflow_digest"],
            close_obligation_id=payload["close_obligation_id"],
            subject_ref=payload["subject_ref"],
            state_digest=payload["state_digest"],
            exit_binding_ref=payload["exit_binding_ref"],
            exit_binding=StageExitBinding.from_dict(
                payload["exit_binding"]
            ),
        )


@dataclass(frozen=True, slots=True)
class StageRunEnvelope:
    """One authority-free run bound to one exact workflow stage."""

    project_id: str
    run_id: str
    base_version: int
    base_state_sha256: str
    branch_id: str
    branch_epoch: int
    subject_ref: str
    state_digest: str
    workflow_ref: str
    workflow_digest: str
    stage_id: str
    stage_index: int
    phase: DesignPhase
    required_roles: tuple[str, ...]
    required_checks: tuple[str, ...]
    close_obligation: DesignObligation
    predecessor: StageRunPredecessor | None = None

    SCHEMA = "StageRunEnvelope@1"
    RECORD_KEYS = frozenset(
        {
            "schema",
            "project_id",
            "run_id",
            "base",
            "branch",
            "subject_ref",
            "state_digest",
            "workflow_ref",
            "workflow_digest",
            "stage",
            "required_roles",
            "required_checks",
            "close_obligation",
            "predecessor",
        }
    ) | _AUTHORITY_KEYS
    STAGE_KEYS = frozenset({"stage_id", "stage_index", "phase"})
    BRANCH_KEYS = frozenset({"branch_id", "epoch"})

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        _stage_index(self.base_version, "base_version")
        object.__setattr__(
            self,
            "base_state_sha256",
            require_sha256(
                self.base_state_sha256,
                "base_state_sha256",
            ),
        )
        require_identifier(self.branch_id, "branch_id")
        _stage_index(self.branch_epoch, "branch_epoch")
        require_logical_ref(self.subject_ref, "subject_ref")
        object.__setattr__(
            self,
            "state_digest",
            require_sha256(self.state_digest, "state_digest"),
        )
        require_logical_ref(self.workflow_ref, "workflow_ref")
        object.__setattr__(
            self,
            "workflow_digest",
            require_sha256(self.workflow_digest, "workflow_digest"),
        )
        require_identifier(self.stage_id, "stage_id")
        _stage_index(self.stage_index)
        if not isinstance(self.phase, DesignPhase):
            raise TypeError("phase must be a DesignPhase")
        _identifier_tuple(self.required_roles, "required_roles")
        _identifier_tuple(self.required_checks, "required_checks")
        if not isinstance(self.close_obligation, DesignObligation):
            raise TypeError(
                "close_obligation must be a DesignObligation"
            )
        if self.close_obligation.status is not ObligationStatus.OPEN:
            raise StageWorkflowError(
                "stage close_obligation must remain OPEN"
            )
        if self.stage_index == 0:
            if self.predecessor is not None:
                raise StageWorkflowError(
                    "stage 0 cannot have a predecessor"
                )
            return
        if not isinstance(self.predecessor, StageRunPredecessor):
            raise StageWorkflowError(
                "stage N must bind the exact Stage N-1 envelope"
            )
        if self.predecessor.project_id != self.project_id:
            raise StageWorkflowError(
                "predecessor belongs to another project"
            )
        if (
            self.predecessor.base_version != self.base_version
            or self.predecessor.base_state_sha256
            != self.base_state_sha256
        ):
            raise StageWorkflowError(
                "predecessor does not share the exact canonical base"
            )
        if self.predecessor.branch_id != self.branch_id:
            raise StageWorkflowError(
                "predecessor belongs to another semantic branch"
            )
        # Epoch identifies a mutable branch generation within one run.  A new
        # run may reinstantiate the same semantic branch at its own epoch, but
        # the predecessor and its exit remain bound to their exact old epoch.
        if (
            self.predecessor.run_id == self.run_id
            and self.predecessor.branch_epoch != self.branch_epoch
        ):
            raise StageWorkflowError(
                "same-run predecessor belongs to another branch epoch"
            )
        if self.predecessor.stage_index != self.stage_index - 1:
            raise StageWorkflowError(
                "predecessor is not the immediately previous stage"
            )
        if (
            self.predecessor.workflow_ref != self.workflow_ref
            or self.predecessor.workflow_digest != self.workflow_digest
        ):
            raise StageWorkflowError(
                "predecessor does not bind the exact workflow ref and digest"
            )

    @property
    def envelope_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": {
                "project_id": self.project_id,
                "version": self.base_version,
                "state_sha256": self.base_state_sha256,
            },
            "branch": {
                "branch_id": self.branch_id,
                "epoch": self.branch_epoch,
            },
            "subject_ref": self.subject_ref,
            "state_digest": self.state_digest,
            "workflow_ref": self.workflow_ref,
            "workflow_digest": self.workflow_digest,
            "stage": {
                "stage_id": self.stage_id,
                "stage_index": self.stage_index,
                "phase": self.phase.value,
            },
            "required_roles": list(self.required_roles),
            "required_checks": list(self.required_checks),
            "close_obligation": self.close_obligation.to_dict(),
            "predecessor": (
                None
                if self.predecessor is None
                else self.predecessor.to_dict()
            ),
            **no_authority(_AUTHORITY_FIELDS),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageRunEnvelope":
        payload = _mapping(value, "stage run envelope")
        _exact(payload, cls.RECORD_KEYS, "stage run envelope")
        if payload["schema"] != cls.SCHEMA:
            raise StageWorkflowError("stage run envelope schema changed")
        stage = _mapping(payload["stage"], "stage")
        _exact(stage, cls.STAGE_KEYS, "stage")
        base = _mapping(payload["base"], "base")
        _exact(
            base,
            {"project_id", "version", "state_sha256"},
            "base",
        )
        if base["project_id"] != payload["project_id"]:
            raise StageWorkflowError(
                "stage run base belongs to another project"
            )
        branch = _mapping(payload["branch"], "branch")
        _exact(branch, cls.BRANCH_KEYS, "branch")
        predecessor = payload["predecessor"]
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base_version=base["version"],
            base_state_sha256=base["state_sha256"],
            branch_id=branch["branch_id"],
            branch_epoch=branch["epoch"],
            subject_ref=payload["subject_ref"],
            state_digest=payload["state_digest"],
            workflow_ref=payload["workflow_ref"],
            workflow_digest=payload["workflow_digest"],
            stage_id=stage["stage_id"],
            stage_index=stage["stage_index"],
            phase=DesignPhase(stage["phase"]),
            required_roles=_string_list(
                payload["required_roles"], "required_roles"
            ),
            required_checks=_string_list(
                payload["required_checks"], "required_checks"
            ),
            close_obligation=DesignObligation.from_dict(
                payload["close_obligation"]
            ),
            predecessor=(
                None
                if predecessor is None
                else StageRunPredecessor.from_dict(predecessor)
            ),
        )


def _require_workflow_binding(
    workflow: ProjectStageWorkflow,
    envelope: StageRunEnvelope,
    *,
    workflow_ref: str,
) -> ProjectStage:
    if not isinstance(workflow, ProjectStageWorkflow):
        raise TypeError("workflow must be a ProjectStageWorkflow")
    if not isinstance(envelope, StageRunEnvelope):
        raise TypeError("envelope must be a StageRunEnvelope")
    require_logical_ref(workflow_ref, "workflow_ref")
    if envelope.project_id != workflow.project_id:
        raise StageWorkflowError("envelope belongs to another project")
    if (
        envelope.workflow_ref != workflow_ref
        or envelope.workflow_digest != workflow.workflow_digest
    ):
        raise StageWorkflowError(
            "envelope does not bind the exact workflow ref and digest"
        )
    stage = workflow.stage_at(envelope.stage_index)
    if (
        envelope.stage_id != stage.stage_id
        or envelope.phase is not stage.phase
        or envelope.required_roles != stage.required_roles
        or envelope.required_checks != stage.required_checks
        or envelope.close_obligation.obligation_id
        != stage.close_obligation_id
    ):
        raise StageWorkflowError(
            "envelope stage contract does not match the workflow"
        )
    return stage


def require_stage_exit_binding(
    envelope: StageRunEnvelope,
    exit_binding: StageExitBinding,
    *,
    envelope_ref: str,
) -> StageExitBinding:
    """Require a SATISFIED close for this exact envelope/state/branch."""

    if not isinstance(envelope, StageRunEnvelope):
        raise TypeError("envelope must be a StageRunEnvelope")
    if not isinstance(exit_binding, StageExitBinding):
        raise TypeError("exit_binding must be a StageExitBinding")
    require_logical_ref(envelope_ref, "envelope_ref")
    expected = StageExitBinding.bind(
        envelope,
        envelope_ref=envelope_ref,
        closure_ref=exit_binding.closure_ref,
        closure_digest=exit_binding.closure_digest,
    )
    if exit_binding != expected:
        raise StageWorkflowError(
            "stage exit binding is stale or cross-scoped"
        )
    return exit_binding


def require_stage_run_envelope(
    workflow: ProjectStageWorkflow,
    envelope: StageRunEnvelope,
    *,
    workflow_ref: str,
    predecessor: StageRunEnvelope | None = None,
    predecessor_ref: str | None = None,
    predecessor_exit: StageExitBinding | None = None,
    predecessor_exit_ref: str | None = None,
) -> StageRunEnvelope:
    """Fail closed unless an envelope binds this workflow and predecessor.

    The caller supplies the retained references.  For stage N, the embedded
    predecessor must equal a fresh digest/ref binding of the supplied Stage
    N-1 envelope and its retained exit record; naming only the previous index
    or embedding an unretained exit payload is insufficient.
    """

    _require_workflow_binding(
        workflow,
        envelope,
        workflow_ref=workflow_ref,
    )
    if envelope.stage_index == 0:
        if (
            predecessor is not None
            or predecessor_ref is not None
            or predecessor_exit is not None
            or predecessor_exit_ref is not None
        ):
            raise StageWorkflowError(
                "stage 0 validation cannot receive predecessor completion"
            )
        return envelope
    if (
        predecessor is None
        or predecessor_ref is None
        or predecessor_exit is None
        or predecessor_exit_ref is None
    ):
        raise StageWorkflowError(
            "stage N validation requires the exact Stage N-1 envelope/ref, "
            "retained exit ref, and SATISFIED exit"
        )
    _require_workflow_binding(
        workflow,
        predecessor,
        workflow_ref=workflow_ref,
    )
    if predecessor.stage_index != envelope.stage_index - 1:
        raise StageWorkflowError(
            "supplied predecessor is not the immediately previous stage"
        )
    expected = StageRunPredecessor.bind(
        predecessor,
        envelope_ref=predecessor_ref,
        exit_binding_ref=predecessor_exit_ref,
        exit_binding=predecessor_exit,
    )
    if envelope.predecessor != expected:
        raise StageWorkflowError(
            "embedded predecessor digest/ref is stale or not exact"
        )
    return envelope


def open_stage_run_envelope(
    workflow: ProjectStageWorkflow,
    *,
    workflow_ref: str,
    run_id: str,
    base_version: int,
    base_state_sha256: str,
    branch_id: str,
    branch_epoch: int,
    subject_ref: str,
    state_digest: str,
    stage_index: int,
    close_obligation: DesignObligation,
    predecessor: StageRunEnvelope | None = None,
    predecessor_ref: str | None = None,
    predecessor_exit: StageExitBinding | None = None,
    predecessor_exit_ref: str | None = None,
) -> StageRunEnvelope:
    """Construct and validate one exact, authority-free stage envelope."""

    if not isinstance(workflow, ProjectStageWorkflow):
        raise TypeError("workflow must be a ProjectStageWorkflow")
    require_logical_ref(workflow_ref, "workflow_ref")
    require_identifier(run_id, "run_id")
    _stage_index(base_version, "base_version")
    require_sha256(base_state_sha256, "base_state_sha256")
    require_identifier(branch_id, "branch_id")
    _stage_index(branch_epoch, "branch_epoch")
    require_logical_ref(subject_ref, "subject_ref")
    require_sha256(state_digest, "state_digest")
    stage = workflow.stage_at(stage_index)
    if stage.stage_index == 0:
        if (
            predecessor is not None
            or predecessor_ref is not None
            or predecessor_exit is not None
            or predecessor_exit_ref is not None
        ):
            raise StageWorkflowError(
                "stage 0 cannot have predecessor completion"
            )
        predecessor_binding = None
    else:
        if (
            predecessor is None
            or predecessor_ref is None
            or predecessor_exit is None
            or predecessor_exit_ref is None
        ):
            raise StageWorkflowError(
                "stage N requires the exact Stage N-1 envelope/ref, retained "
                "exit ref, and SATISFIED exit"
            )
        predecessor_binding = StageRunPredecessor.bind(
            predecessor,
            envelope_ref=predecessor_ref,
            exit_binding_ref=predecessor_exit_ref,
            exit_binding=predecessor_exit,
        )
    envelope = StageRunEnvelope(
        project_id=workflow.project_id,
        run_id=run_id,
        base_version=base_version,
        base_state_sha256=base_state_sha256,
        branch_id=branch_id,
        branch_epoch=branch_epoch,
        subject_ref=subject_ref,
        state_digest=state_digest,
        workflow_ref=workflow_ref,
        workflow_digest=workflow.workflow_digest,
        stage_id=stage.stage_id,
        stage_index=stage.stage_index,
        phase=stage.phase,
        required_roles=stage.required_roles,
        required_checks=stage.required_checks,
        close_obligation=close_obligation,
        predecessor=predecessor_binding,
    )
    return require_stage_run_envelope(
        workflow,
        envelope,
        workflow_ref=workflow_ref,
        predecessor=predecessor,
        predecessor_ref=predecessor_ref,
        predecessor_exit=predecessor_exit,
        predecessor_exit_ref=predecessor_exit_ref,
    )


__all__ = [
    "ProjectStage",
    "ProjectStageWorkflow",
    "StageExitBinding",
    "StageExitStatus",
    "StageRunEnvelope",
    "StageRunPredecessor",
    "StageWorkflowError",
    "open_stage_run_envelope",
    "require_stage_exit_binding",
    "require_stage_run_envelope",
]
