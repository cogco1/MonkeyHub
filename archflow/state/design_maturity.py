"""Deterministic design-maturity contracts for operational design states.

The graph encodes architectural precedence and typed deliverable roles.  It
does not encode a building answer, a mandatory expert list, or a controller.
P022 binds these records to each branch-local ``D_v,k`` and persists the
result through the project repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DependencyEffect,
    DesignObligation,
    ObligationStatus,
    OperationalMarkovState,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.canonical import canonical_digest, canonical_json, require_sha256
from archflow.contracts.fields import (
    tuple_of as _tuple,
    unique as _unique,
)


_MAX_ITEMS = 4096
_HEX = frozenset("0123456789abcdef")


class DesignMaturityError(ValueError):
    """A maturity transition is not valid for its exact operational base."""


class DesignPhase(StrEnum):
    RESEARCH_BRIEF = "research_brief"
    PROGRAMMING = "programming"
    SITE_RESOURCE_COORDINATION = "site_resource_coordination"
    SCHEMATIC_DESIGN = "schematic_design"
    DESIGN_DEVELOPMENT = "design_development"
    CANDIDATE_COORDINATION = "candidate_coordination"
    EXECUTION_READY = "execution_ready"


DESIGN_PHASES: tuple[DesignPhase, ...] = tuple(DesignPhase)


class DeliverableRole(StrEnum):
    """Generic evidence roles required at design-maturity boundaries."""

    RESEARCH_BRIEF = "research_brief"
    DESIGN_PROGRAM = "design_program"
    SITE_CONTEXT = "site_context"
    BUILD_POLICY = "build_policy"
    SCHEMATIC_OPTIONS = "schematic_options"
    SCHEMATIC_SELECTION = "schematic_selection"
    DESIGN_DEVELOPMENT_PACKAGE = "design_development_package"
    COORDINATED_CANDIDATE = "coordinated_candidate"
    HARD_USABILITY_GATE_RECEIPT = "hard_usability_gate_receipt"
    COMMIT_AUTHORIZATION = "commit_authorization"


PHASE_DELIVERABLE_ROLES: Mapping[
    DesignPhase,
    frozenset[DeliverableRole],
] = {
    DesignPhase.RESEARCH_BRIEF: frozenset(
        {DeliverableRole.RESEARCH_BRIEF}
    ),
    DesignPhase.PROGRAMMING: frozenset(
        {DeliverableRole.DESIGN_PROGRAM}
    ),
    DesignPhase.SITE_RESOURCE_COORDINATION: frozenset(
        {
            DeliverableRole.SITE_CONTEXT,
            DeliverableRole.BUILD_POLICY,
        }
    ),
    DesignPhase.SCHEMATIC_DESIGN: frozenset(
        {
            DeliverableRole.SCHEMATIC_OPTIONS,
            DeliverableRole.SCHEMATIC_SELECTION,
        }
    ),
    DesignPhase.DESIGN_DEVELOPMENT: frozenset(
        {DeliverableRole.DESIGN_DEVELOPMENT_PACKAGE}
    ),
    DesignPhase.CANDIDATE_COORDINATION: frozenset(
        {
            DeliverableRole.COORDINATED_CANDIDATE,
            DeliverableRole.HARD_USABILITY_GATE_RECEIPT,
            DeliverableRole.COMMIT_AUTHORIZATION,
        }
    ),
    DesignPhase.EXECUTION_READY: frozenset(),
}

DELIVERABLE_ROLE_PHASE: Mapping[DeliverableRole, DesignPhase] = {
    role: phase
    for phase, roles in PHASE_DELIVERABLE_ROLES.items()
    for role in roles
}


class GateCertificationSource(StrEnum):
    DETERMINISTIC_COMPILER = "deterministic_compiler"
    EXPERT_ASSERTION = "expert_assertion"


class PhaseGateStatus(StrEnum):
    PASSED = "passed"


class RevisionImpact(StrEnum):
    INVALIDATED = "invalidated"
    REVALIDATION_REQUIRED = "revalidation_required"


def _branch_to_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.state_sha256,
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _branch_from_dict(value: object) -> BranchRef:
    if not isinstance(value, Mapping):
        raise TypeError("branch must be an object")
    if set(value) != {
        "project_id",
        "run_id",
        "base",
        "branch_id",
        "epoch",
    }:
        raise ValueError("branch schema drifted")
    base = value["base"]
    if not isinstance(base, Mapping) or set(base) != {
        "version",
        "state_sha256",
    }:
        raise ValueError("branch base schema drifted")
    project_id = value["project_id"]
    return BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=value["run_id"],
            base=ProjectVersionRef(
                project_id=project_id,
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
        ),
        branch_id=value["branch_id"],
        epoch=value["epoch"],
    )


def _same_branch(left: BranchRef, right: BranchRef) -> bool:
    return (
        left.run == right.run
        and left.branch_id == right.branch_id
    )


def next_design_phase(phase: DesignPhase) -> DesignPhase | None:
    if not isinstance(phase, DesignPhase):
        raise TypeError("phase must be a DesignPhase")
    position = DESIGN_PHASES.index(phase)
    if position == len(DESIGN_PHASES) - 1:
        return None
    return DESIGN_PHASES[position + 1]


@dataclass(frozen=True, slots=True)
class PhaseDeliverable:
    """A typed project artifact bound to the state that produced it."""

    deliverable_id: str
    role: DeliverableRole
    produced_phase: DesignPhase
    branch: BranchRef
    base_state_digest: str
    artifact_ref: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_local_id(self.deliverable_id, "deliverable_id")
        if not isinstance(self.role, DeliverableRole):
            raise TypeError("role must be a DeliverableRole")
        if not isinstance(self.produced_phase, DesignPhase):
            raise TypeError("produced_phase must be a DesignPhase")
        if self.role not in PHASE_DELIVERABLE_ROLES[self.produced_phase]:
            raise ValueError(
                "deliverable role does not belong to produced_phase"
            )
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        require_logical_ref(self.artifact_ref, "artifact_ref")
        _tuple(self.evidence_refs, "evidence_refs")
        for ref in self.evidence_refs:
            require_logical_ref(ref, "evidence_ref")
        _unique(self.evidence_refs, "evidence_refs")

    @property
    def ref(self) -> str:
        return f"deliverable:{self.deliverable_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "deliverable_id": self.deliverable_id,
            "role": self.role.value,
            "produced_phase": self.produced_phase.value,
            "branch": _branch_to_dict(self.branch),
            "base_state_digest": self.base_state_digest,
            "artifact_ref": self.artifact_ref,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> PhaseDeliverable:
        if not isinstance(value, Mapping):
            raise TypeError("phase deliverable must be an object")
        if set(value) != {
            "deliverable_id",
            "role",
            "produced_phase",
            "branch",
            "base_state_digest",
            "artifact_ref",
            "evidence_refs",
        }:
            raise ValueError("phase deliverable schema drifted")
        evidence_refs = value["evidence_refs"]
        if not isinstance(evidence_refs, list) or any(
            not isinstance(item, str) for item in evidence_refs
        ):
            raise TypeError("evidence_refs must be a string list")
        return cls(
            deliverable_id=value["deliverable_id"],
            role=DeliverableRole(value["role"]),
            produced_phase=DesignPhase(value["produced_phase"]),
            branch=_branch_from_dict(value["branch"]),
            base_state_digest=value["base_state_digest"],
            artifact_ref=value["artifact_ref"],
            evidence_refs=tuple(evidence_refs),
        )


@dataclass(frozen=True, slots=True)
class DesignMaturityState:
    """Phase and exact deliverable projection paired with one ``D_v,k``."""

    branch: BranchRef
    operational_state_digest: str
    phase: DesignPhase
    deliverables: tuple[PhaseDeliverable, ...] = ()
    invalidated_refs: tuple[str, ...] = ()
    revalidation_required_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "operational_state_digest",
            require_sha256(
                self.operational_state_digest,
                "operational_state_digest",
            ),
        )
        if not isinstance(self.phase, DesignPhase):
            raise TypeError("phase must be a DesignPhase")
        _tuple(self.deliverables, "deliverables")
        if any(
            not isinstance(item, PhaseDeliverable)
            for item in self.deliverables
        ):
            raise TypeError("deliverables must contain PhaseDeliverable")
        refs = tuple(item.ref for item in self.deliverables)
        _unique(refs, "deliverable refs")
        for item in self.deliverables:
            if not _same_branch(item.branch, self.branch):
                raise ValueError(
                    "deliverable belongs to a different run or branch"
                )
            if item.branch.epoch > self.branch.epoch:
                raise ValueError("deliverable cannot originate in the future")
        for field, values in (
            ("invalidated_refs", self.invalidated_refs),
            (
                "revalidation_required_refs",
                self.revalidation_required_refs,
            ),
        ):
            _tuple(values, field)
            for ref in values:
                require_logical_ref(ref, field)
            _unique(values, field)
            if not set(values) <= set(refs):
                raise ValueError(f"{field} must name known deliverables")
        if set(self.invalidated_refs) & set(
            self.revalidation_required_refs
        ):
            raise ValueError(
                "a deliverable cannot be both invalidated and awaiting "
                "revalidation"
            )

    @classmethod
    def from_operational_state(
        cls,
        state: OperationalMarkovState,
        *,
        deliverables: tuple[PhaseDeliverable, ...] = (),
        invalidated_refs: tuple[str, ...] = (),
        revalidation_required_refs: tuple[str, ...] = (),
    ) -> DesignMaturityState:
        if not isinstance(state, OperationalMarkovState):
            raise TypeError("state must be an OperationalMarkovState")
        try:
            phase = DesignPhase(state.phase)
        except ValueError as exc:
            raise DesignMaturityError(
                "operational state names an unsupported design phase"
            ) from exc
        return cls(
            branch=state.branch,
            operational_state_digest=state.state_digest,
            phase=phase,
            deliverables=deliverables,
            invalidated_refs=invalidated_refs,
            revalidation_required_refs=revalidation_required_refs,
        )

    @property
    def deliverable_refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.deliverables)

    @property
    def state_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def require_exact_operational_state(
        self,
        state: OperationalMarkovState,
    ) -> None:
        if not isinstance(state, OperationalMarkovState):
            raise TypeError("state must be an OperationalMarkovState")
        if (
            self.branch != state.branch
            or self.operational_state_digest != state.state_digest
            or self.phase.value != state.phase
        ):
            raise DesignMaturityError(
                "maturity state is stale or cross-branch"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "DesignMaturityState@1",
            "branch": _branch_to_dict(self.branch),
            "operational_state_digest": self.operational_state_digest,
            "phase": self.phase.value,
            "deliverables": [
                item.to_dict() for item in self.deliverables
            ],
            "invalidated_refs": list(self.invalidated_refs),
            "revalidation_required_refs": list(
                self.revalidation_required_refs
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> DesignMaturityState:
        if not isinstance(value, Mapping):
            raise TypeError("design maturity state must be an object")
        if set(value) != {
            "schema",
            "branch",
            "operational_state_digest",
            "phase",
            "deliverables",
            "invalidated_refs",
            "revalidation_required_refs",
        } or value["schema"] != "DesignMaturityState@1":
            raise ValueError("design maturity state schema drifted")
        deliverables = value["deliverables"]
        invalidated = value["invalidated_refs"]
        revalidation = value["revalidation_required_refs"]
        if not isinstance(deliverables, list):
            raise TypeError("deliverables must be a list")
        if not isinstance(invalidated, list) or any(
            not isinstance(item, str) for item in invalidated
        ):
            raise TypeError("invalidated_refs must be a string list")
        if not isinstance(revalidation, list) or any(
            not isinstance(item, str) for item in revalidation
        ):
            raise TypeError(
                "revalidation_required_refs must be a string list"
            )
        return cls(
            branch=_branch_from_dict(value["branch"]),
            operational_state_digest=value["operational_state_digest"],
            phase=DesignPhase(value["phase"]),
            deliverables=tuple(
                PhaseDeliverable.from_dict(item)
                for item in deliverables
            ),
            invalidated_refs=tuple(invalidated),
            revalidation_required_refs=tuple(revalidation),
        )


@dataclass(frozen=True, slots=True)
class PhaseGateRequest:
    request_id: str
    branch: BranchRef
    base_state_digest: str
    from_phase: DesignPhase
    to_phase: DesignPhase
    deliverable_refs: tuple[str, ...]
    certification_source: GateCertificationSource = (
        GateCertificationSource.DETERMINISTIC_COMPILER
    )

    def __post_init__(self) -> None:
        require_local_id(self.request_id, "request_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        if not isinstance(self.from_phase, DesignPhase):
            raise TypeError("from_phase must be a DesignPhase")
        if not isinstance(self.to_phase, DesignPhase):
            raise TypeError("to_phase must be a DesignPhase")
        _tuple(self.deliverable_refs, "deliverable_refs")
        for ref in self.deliverable_refs:
            require_logical_ref(ref, "deliverable_ref")
        _unique(self.deliverable_refs, "deliverable_refs")
        if not isinstance(
            self.certification_source,
            GateCertificationSource,
        ):
            raise TypeError(
                "certification_source must be a GateCertificationSource"
            )


@dataclass(frozen=True, slots=True)
class PhaseGateReceipt:
    receipt_id: str
    request_id: str
    branch: BranchRef
    base_state_digest: str
    from_phase: DesignPhase
    to_phase: DesignPhase
    required_roles: tuple[DeliverableRole, ...]
    accepted_deliverable_refs: tuple[str, ...]
    status: PhaseGateStatus = PhaseGateStatus.PASSED
    authority: GateCertificationSource = (
        GateCertificationSource.DETERMINISTIC_COMPILER
    )

    def __post_init__(self) -> None:
        require_local_id(self.receipt_id, "receipt_id")
        require_local_id(self.request_id, "request_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        if not isinstance(self.from_phase, DesignPhase) or not isinstance(
            self.to_phase,
            DesignPhase,
        ):
            raise TypeError("receipt phases must be DesignPhase values")
        if self.to_phase is not next_design_phase(self.from_phase):
            raise ValueError(
                "phase gate receipt must name the immediate next phase"
            )
        _tuple(self.required_roles, "required_roles")
        if any(
            not isinstance(item, DeliverableRole)
            for item in self.required_roles
        ):
            raise TypeError("required_roles must contain DeliverableRole")
        if set(self.required_roles) != set(
            PHASE_DELIVERABLE_ROLES[self.from_phase]
        ):
            raise ValueError(
                "phase gate receipt required_roles do not match phase"
            )
        _tuple(
            self.accepted_deliverable_refs,
            "accepted_deliverable_refs",
        )
        for ref in self.accepted_deliverable_refs:
            require_logical_ref(ref, "accepted deliverable ref")
        _unique(
            self.accepted_deliverable_refs,
            "accepted_deliverable_refs",
        )
        if self.status is not PhaseGateStatus.PASSED:
            raise ValueError("only passed phase receipts are authoritative")
        if (
            self.authority
            is not GateCertificationSource.DETERMINISTIC_COMPILER
        ):
            raise ValueError(
                "phase gate receipts require deterministic authority"
            )

    @property
    def ref(self) -> str:
        return f"phase-gate:{self.receipt_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "PhaseGateReceipt@1",
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "branch": _branch_to_dict(self.branch),
            "base_state_digest": self.base_state_digest,
            "from_phase": self.from_phase.value,
            "to_phase": self.to_phase.value,
            "required_roles": [
                item.value for item in self.required_roles
            ],
            "accepted_deliverable_refs": list(
                self.accepted_deliverable_refs
            ),
            "status": self.status.value,
            "authority": self.authority.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> PhaseGateReceipt:
        if not isinstance(value, Mapping):
            raise TypeError("phase gate receipt must be an object")
        if set(value) != {
            "schema",
            "receipt_id",
            "request_id",
            "branch",
            "base_state_digest",
            "from_phase",
            "to_phase",
            "required_roles",
            "accepted_deliverable_refs",
            "status",
            "authority",
        } or value["schema"] != "PhaseGateReceipt@1":
            raise ValueError("phase gate receipt schema drifted")
        roles = value["required_roles"]
        refs = value["accepted_deliverable_refs"]
        if not isinstance(roles, list):
            raise TypeError("required_roles must be a list")
        if not isinstance(refs, list) or any(
            not isinstance(item, str) for item in refs
        ):
            raise TypeError(
                "accepted_deliverable_refs must be a string list"
            )
        return cls(
            receipt_id=value["receipt_id"],
            request_id=value["request_id"],
            branch=_branch_from_dict(value["branch"]),
            base_state_digest=value["base_state_digest"],
            from_phase=DesignPhase(value["from_phase"]),
            to_phase=DesignPhase(value["to_phase"]),
            required_roles=tuple(
                DeliverableRole(item) for item in roles
            ),
            accepted_deliverable_refs=tuple(refs),
            status=PhaseGateStatus(value["status"]),
            authority=GateCertificationSource(value["authority"]),
        )


@dataclass(frozen=True, slots=True)
class StageEntryProof:
    """Durable proof that one exact predecessor gate entered a new phase.

    The proof binds the in-memory deterministic phase-gate receipt to the
    P036 checkpoint record that retains the independently admitted stage-exit
    proof.  It grants no geometry, persistence, or canonical-write authority;
    downstream producers use it only as a mandatory stage-entry guard.
    """

    phase_gate: PhaseGateReceipt
    stage_exit_checkpoint_ref: ProjectRecordRef
    stage_exit_proof_digest: str
    predecessor_checkpoint_digest: str
    successor_checkpoint_digest: str
    successor_branch: BranchRef

    SCHEMA = "StageEntryProof@1"

    def __post_init__(self) -> None:
        if not isinstance(self.phase_gate, PhaseGateReceipt):
            raise TypeError("phase_gate must be a PhaseGateReceipt")
        if not isinstance(self.stage_exit_checkpoint_ref, ProjectRecordRef):
            raise TypeError(
                "stage_exit_checkpoint_ref must be a ProjectRecordRef"
            )
        for field in (
            "stage_exit_proof_digest",
            "predecessor_checkpoint_digest",
            "successor_checkpoint_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        if not isinstance(self.successor_branch, BranchRef):
            raise TypeError("successor_branch must be a BranchRef")
        predecessor_branch = self.phase_gate.branch
        if (
            self.successor_branch.run != predecessor_branch.run
            or self.successor_branch.branch_id
            != predecessor_branch.branch_id
            or self.successor_branch.epoch != predecessor_branch.epoch + 1
        ):
            raise DesignMaturityError(
                "stage-entry proof does not bind one exact successor epoch"
            )
        if (
            self.stage_exit_checkpoint_ref.project_id
            != self.successor_branch.run.project_id
        ):
            raise DesignMaturityError(
                "stage-entry checkpoint belongs to another project"
            )
        expected_prefix = (
            f"runs/{self.successor_branch.run.run_id}/branches/"
            f"{self.successor_branch.branch_id}/records/"
        )
        if not self.stage_exit_checkpoint_ref.relative_path.startswith(
            expected_prefix
        ):
            raise DesignMaturityError(
                "stage-entry checkpoint is not retained on the exact branch"
            )
        if self.stage_exit_checkpoint_ref.media_type != "application/json":
            raise DesignMaturityError(
                "stage-entry checkpoint must be a P036 JSON record"
            )

    @property
    def proof_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def ref(self) -> str:
        return f"stage-entry-proof:{self.proof_digest}"

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "phase_gate": self.phase_gate.to_dict(),
            "stage_exit_checkpoint_ref": (
                self.stage_exit_checkpoint_ref.to_dict()
            ),
            "stage_exit_proof_digest": self.stage_exit_proof_digest,
            "predecessor_checkpoint_digest": (
                self.predecessor_checkpoint_digest
            ),
            "successor_checkpoint_digest": self.successor_checkpoint_digest,
            "successor_branch": _branch_to_dict(self.successor_branch),
            "stage_acceptance_authority": False,
            "geometry_mutation_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "proof_digest": self.proof_digest}

    @classmethod
    def from_dict(cls, value: object) -> "StageEntryProof":
        if not isinstance(value, Mapping) or set(value) != {
            "schema",
            "phase_gate",
            "stage_exit_checkpoint_ref",
            "stage_exit_proof_digest",
            "predecessor_checkpoint_digest",
            "successor_checkpoint_digest",
            "successor_branch",
            "stage_acceptance_authority",
            "geometry_mutation_authority",
            "persistence_authority",
            "canonical_write_authority",
            "proof_digest",
        }:
            raise DesignMaturityError("stage-entry proof schema drifted")
        if value["schema"] != cls.SCHEMA:
            raise DesignMaturityError("stage-entry proof acquired authority")
        record = value["stage_exit_checkpoint_ref"]
        if not isinstance(record, Mapping) or set(record) != {
            "project_id",
            "relative_path",
            "sha256",
            "media_type",
        }:
            raise DesignMaturityError(
                "stage-entry checkpoint reference schema drifted"
            )
        result = cls(
            phase_gate=PhaseGateReceipt.from_dict(value["phase_gate"]),
            stage_exit_checkpoint_ref=ProjectRecordRef(
                project_id=record["project_id"],
                relative_path=record["relative_path"],
                sha256=record["sha256"],
                media_type=record["media_type"],
            ),
            stage_exit_proof_digest=value["stage_exit_proof_digest"],
            predecessor_checkpoint_digest=value[
                "predecessor_checkpoint_digest"
            ],
            successor_checkpoint_digest=value[
                "successor_checkpoint_digest"
            ],
            successor_branch=_branch_from_dict(value["successor_branch"]),
        )
        if result.to_dict() != value:
            raise DesignMaturityError("stage-entry proof digest changed")
        return result


def require_stage_entry_proof(
    proof: StageEntryProof,
    *,
    successor_branch: BranchRef,
    from_phase: DesignPhase,
    to_phase: DesignPhase,
) -> StageEntryProof:
    """Reject absent, stale, cross-branch, or wrong-phase entry evidence."""

    if not isinstance(proof, StageEntryProof):
        raise TypeError("proof must be a StageEntryProof")
    if not isinstance(successor_branch, BranchRef):
        raise TypeError("successor_branch must be a BranchRef")
    if not isinstance(from_phase, DesignPhase) or not isinstance(
        to_phase,
        DesignPhase,
    ):
        raise TypeError("stage-entry phases must be DesignPhase values")
    if (
        proof.successor_branch != successor_branch
        or proof.phase_gate.from_phase is not from_phase
        or proof.phase_gate.to_phase is not to_phase
    ):
        raise DesignMaturityError(
            "stage-entry proof is absent, stale, cross-branch, or wrong-phase"
        )
    return proof


def evaluate_forward_phase_gate(
    maturity: DesignMaturityState,
    request: PhaseGateRequest,
) -> PhaseGateReceipt:
    """Fail closed unless the immediately prior phase is deterministically met."""

    if not isinstance(maturity, DesignMaturityState):
        raise TypeError("maturity must be a DesignMaturityState")
    if not isinstance(request, PhaseGateRequest):
        raise TypeError("request must be a PhaseGateRequest")
    if (
        request.certification_source
        is not GateCertificationSource.DETERMINISTIC_COMPILER
    ):
        raise DesignMaturityError(
            "expert assertions cannot certify phase completion"
        )
    if (
        request.branch != maturity.branch
        or request.base_state_digest
        != maturity.operational_state_digest
    ):
        raise DesignMaturityError("phase gate request is stale or cross-branch")
    if request.from_phase is not maturity.phase:
        raise DesignMaturityError("phase gate source does not match current phase")
    expected_next = next_design_phase(maturity.phase)
    if expected_next is None:
        raise DesignMaturityError("execution_ready is terminal")
    if request.to_phase is not expected_next:
        raise DesignMaturityError("forward phase transitions cannot skip")

    deliverables = {item.ref: item for item in maturity.deliverables}
    unknown = set(request.deliverable_refs) - set(deliverables)
    if unknown:
        raise DesignMaturityError(
            f"phase gate names unknown deliverables: {sorted(unknown)}"
        )
    stale = set(request.deliverable_refs) & (
        set(maturity.invalidated_refs)
        | set(maturity.revalidation_required_refs)
    )
    if stale:
        raise DesignMaturityError(
            f"phase gate names stale deliverables: {sorted(stale)}"
        )
    supplied_roles = {
        deliverables[ref].role for ref in request.deliverable_refs
    }
    required = PHASE_DELIVERABLE_ROLES[maturity.phase]
    missing = required - supplied_roles
    if missing:
        raise DesignMaturityError(
            "phase gate is missing required roles: "
            f"{sorted(item.value for item in missing)}"
        )
    wrong_phase = tuple(
        sorted(
            ref
            for ref in request.deliverable_refs
            if deliverables[ref].role in required
            and deliverables[ref].produced_phase is not maturity.phase
        )
    )
    if wrong_phase:
        raise DesignMaturityError(
            f"required deliverables came from the wrong phase: {wrong_phase}"
        )

    receipt_payload = {
        "request_id": request.request_id,
        "branch": _branch_to_dict(request.branch),
        "base_state_digest": request.base_state_digest,
        "from_phase": request.from_phase.value,
        "to_phase": request.to_phase.value,
        "required_roles": sorted(item.value for item in required),
        "accepted_deliverable_refs": sorted(request.deliverable_refs),
        "authority": request.certification_source.value,
    }
    return PhaseGateReceipt(
        receipt_id=f"pgr-{canonical_digest(receipt_payload)[:24]}",
        request_id=request.request_id,
        branch=request.branch,
        base_state_digest=request.base_state_digest,
        from_phase=request.from_phase,
        to_phase=request.to_phase,
        required_roles=tuple(sorted(required, key=lambda item: item.value)),
        accepted_deliverable_refs=tuple(
            sorted(request.deliverable_refs)
        ),
    )


def require_current_phase_gate(
    maturity: DesignMaturityState,
    receipt: PhaseGateReceipt,
) -> None:
    """Reject a once-valid receipt after the branch base has advanced."""

    if not isinstance(receipt, PhaseGateReceipt):
        raise TypeError("receipt must be a PhaseGateReceipt")
    if (
        receipt.branch != maturity.branch
        or receipt.base_state_digest != maturity.operational_state_digest
        or receipt.from_phase is not maturity.phase
        or receipt.to_phase is not next_design_phase(maturity.phase)
    ):
        raise DesignMaturityError("phase gate receipt is stale or cross-branch")
    expected = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id=receipt.request_id,
            branch=receipt.branch,
            base_state_digest=receipt.base_state_digest,
            from_phase=receipt.from_phase,
            to_phase=receipt.to_phase,
            deliverable_refs=receipt.accepted_deliverable_refs,
        ),
    )
    if receipt != expected:
        raise DesignMaturityError(
            "phase gate receipt does not match deterministic evaluation"
        )


@dataclass(frozen=True, slots=True)
class BackwardRevisionRequest:
    revision_id: str
    branch: BranchRef
    base_state_digest: str
    from_phase: DesignPhase
    to_phase: DesignPhase
    changed_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        require_local_id(self.revision_id, "revision_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        if not isinstance(self.from_phase, DesignPhase) or not isinstance(
            self.to_phase,
            DesignPhase,
        ):
            raise TypeError("revision phases must be DesignPhase values")
        _tuple(self.changed_refs, "changed_refs")
        if not self.changed_refs:
            raise ValueError("changed_refs cannot be empty")
        for ref in self.changed_refs:
            require_logical_ref(ref, "changed_ref")
        _unique(self.changed_refs, "changed_refs")


@dataclass(frozen=True, slots=True)
class BackwardRevisionResult:
    revision_id: str
    branch: BranchRef
    base_state_digest: str
    from_phase: DesignPhase
    to_phase: DesignPhase
    changed_refs: tuple[str, ...]
    invalidated_deliverable_refs: tuple[str, ...]
    revalidation_required_refs: tuple[str, ...]
    spawned_obligations: tuple[DesignObligation, ...]

    def __post_init__(self) -> None:
        require_local_id(self.revision_id, "revision_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        object.__setattr__(
            self,
            "base_state_digest",
            require_sha256(self.base_state_digest, "base_state_digest"),
        )
        if not isinstance(self.from_phase, DesignPhase) or not isinstance(
            self.to_phase,
            DesignPhase,
        ):
            raise TypeError("result phases must be DesignPhase values")
        for field, values in (
            ("changed_refs", self.changed_refs),
            (
                "invalidated_deliverable_refs",
                self.invalidated_deliverable_refs,
            ),
            (
                "revalidation_required_refs",
                self.revalidation_required_refs,
            ),
        ):
            _tuple(values, field)
            for ref in values:
                require_logical_ref(ref, field)
            _unique(values, field)
        if set(self.invalidated_deliverable_refs) & set(
            self.revalidation_required_refs
        ):
            raise ValueError("revision impact sets must be disjoint")
        _tuple(self.spawned_obligations, "spawned_obligations")
        if any(
            not isinstance(item, DesignObligation)
            for item in self.spawned_obligations
        ):
            raise TypeError(
                "spawned_obligations must contain DesignObligation"
            )

    @property
    def ref(self) -> str:
        return f"phase-revision:{self.revision_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "BackwardRevisionResult@1",
            "revision_id": self.revision_id,
            "branch": _branch_to_dict(self.branch),
            "base_state_digest": self.base_state_digest,
            "from_phase": self.from_phase.value,
            "to_phase": self.to_phase.value,
            "changed_refs": list(self.changed_refs),
            "invalidated_deliverable_refs": list(
                self.invalidated_deliverable_refs
            ),
            "revalidation_required_refs": list(
                self.revalidation_required_refs
            ),
            "spawned_obligations": [
                item.to_dict() for item in self.spawned_obligations
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> BackwardRevisionResult:
        if not isinstance(value, Mapping):
            raise TypeError("backward revision result must be an object")
        if set(value) != {
            "schema",
            "revision_id",
            "branch",
            "base_state_digest",
            "from_phase",
            "to_phase",
            "changed_refs",
            "invalidated_deliverable_refs",
            "revalidation_required_refs",
            "spawned_obligations",
        } or value["schema"] != "BackwardRevisionResult@1":
            raise ValueError("backward revision result schema drifted")

        def refs(field: str) -> tuple[str, ...]:
            items = value[field]
            if not isinstance(items, list) or any(
                not isinstance(item, str) for item in items
            ):
                raise TypeError(f"{field} must be a string list")
            return tuple(items)

        obligations = value["spawned_obligations"]
        if not isinstance(obligations, list):
            raise TypeError("spawned_obligations must be a list")
        return cls(
            revision_id=value["revision_id"],
            branch=_branch_from_dict(value["branch"]),
            base_state_digest=value["base_state_digest"],
            from_phase=DesignPhase(value["from_phase"]),
            to_phase=DesignPhase(value["to_phase"]),
            changed_refs=refs("changed_refs"),
            invalidated_deliverable_refs=refs(
                "invalidated_deliverable_refs"
            ),
            revalidation_required_refs=refs(
                "revalidation_required_refs"
            ),
            spawned_obligations=tuple(
                DesignObligation.from_dict(item)
                for item in obligations
            ),
        )


def compile_backward_revision(
    maturity: DesignMaturityState,
    request: BackwardRevisionRequest,
    dependencies: tuple[DependencyEdge, ...],
) -> BackwardRevisionResult:
    """Compile local downstream impact without treating all edges alike."""

    if not isinstance(maturity, DesignMaturityState):
        raise TypeError("maturity must be a DesignMaturityState")
    if not isinstance(request, BackwardRevisionRequest):
        raise TypeError("request must be a BackwardRevisionRequest")
    _tuple(dependencies, "dependencies")
    if any(not isinstance(item, DependencyEdge) for item in dependencies):
        raise TypeError("dependencies must contain DependencyEdge")
    if (
        request.branch != maturity.branch
        or request.base_state_digest
        != maturity.operational_state_digest
        or request.from_phase is not maturity.phase
    ):
        raise DesignMaturityError(
            "backward revision request is stale or cross-branch"
        )
    if DESIGN_PHASES.index(request.to_phase) >= DESIGN_PHASES.index(
        request.from_phase
    ):
        raise DesignMaturityError(
            "backward revision must target an earlier design phase"
        )

    impact_by_ref: dict[str, RevisionImpact] = {
        ref: RevisionImpact.INVALIDATED for ref in request.changed_refs
    }
    adjacency: dict[str, list[DependencyEdge]] = {}
    for edge in dependencies:
        if edge.effect not in {
            DependencyEffect.INVALIDATES,
            DependencyEffect.REQUIRES_REVALIDATION,
        }:
            continue
        adjacency.setdefault(edge.upstream_ref, []).append(edge)

    queue = list(sorted(request.changed_refs))
    while queue:
        upstream = queue.pop(0)
        upstream_impact = impact_by_ref[upstream]
        for edge in sorted(
            adjacency.get(upstream, ()),
            key=lambda item: item.identity,
        ):
            edge_impact = (
                RevisionImpact.INVALIDATED
                if (
                    upstream_impact is RevisionImpact.INVALIDATED
                    and edge.effect is DependencyEffect.INVALIDATES
                )
                else RevisionImpact.REVALIDATION_REQUIRED
            )
            existing = impact_by_ref.get(edge.downstream_ref)
            merged = (
                RevisionImpact.INVALIDATED
                if RevisionImpact.INVALIDATED
                in {existing, edge_impact}
                else RevisionImpact.REVALIDATION_REQUIRED
            )
            if existing is merged:
                continue
            impact_by_ref[edge.downstream_ref] = merged
            if len(impact_by_ref) > _MAX_ITEMS:
                raise DesignMaturityError(
                    "backward dependency closure exceeds bounded item count"
                )
            queue.append(edge.downstream_ref)

    known_deliverables = set(maturity.deliverable_refs)
    invalidated = tuple(
        sorted(
            ref
            for ref, impact in impact_by_ref.items()
            if ref in known_deliverables
            and impact is RevisionImpact.INVALIDATED
        )
    )
    revalidation = tuple(
        sorted(
            ref
            for ref, impact in impact_by_ref.items()
            if ref in known_deliverables
            and impact is RevisionImpact.REVALIDATION_REQUIRED
            and ref not in invalidated
        )
    )
    impacted = invalidated + revalidation
    if not impacted:
        raise DesignMaturityError(
            "backward revision must explicitly affect downstream deliverables"
        )

    obligations = tuple(
        DesignObligation(
            obligation_id=(
                f"phase-revision-{canonical_digest((request.revision_id, ref))[:20]}"
            ),
            statement=(
                f"Recompile {ref} after backward design revision "
                f"{request.revision_id}."
            ),
            source_ref=f"phase-revision:{request.revision_id}",
            status=ObligationStatus.OPEN,
            subject_refs=(ref,),
        )
        for ref in sorted(impacted)
    )
    return BackwardRevisionResult(
        revision_id=request.revision_id,
        branch=request.branch,
        base_state_digest=request.base_state_digest,
        from_phase=request.from_phase,
        to_phase=request.to_phase,
        changed_refs=tuple(sorted(request.changed_refs)),
        invalidated_deliverable_refs=invalidated,
        revalidation_required_refs=revalidation,
        spawned_obligations=obligations,
    )
