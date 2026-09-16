"""Authority-free project stage workflows and exact run envelopes.

``ProjectStageWorkflow@1`` declares a finite, ordered stage sequence.  A
``StageRunEnvelope@1`` binds one run to exactly one stage in that workflow and,
after stage zero, to the exact retained envelope and retained SATISFIED exit
binding for the immediately previous stage.  These records are guards for
orchestration; they never select design, accept a stage, mutate geometry,
persist state, issue a run as the published design, or write canonical state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.authority import (
    DEFAULT_AUTHORITY_FIELDS,
    no_authority,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.project.refs import BranchRef, require_identifier
from archflow.state.operational_state import (
    DesignObligation,
    ObligationStatus,
    require_local_id,
    require_logical_ref,
)
from archflow.contracts.fields import (
    mapping as _mapping,
    string_tuple,
)
from archflow.contracts.fields import (
    exact_mapping as _exact,
)


_AUTHORITY_FIELDS = DEFAULT_AUTHORITY_FIELDS
_AUTHORITY_KEYS = frozenset(_AUTHORITY_FIELDS)
_MAX_STAGES = 256
_MAX_REQUIREMENTS = 4096


# ---------------------------------------------------------------- the phase ladder
class DesignPhase(StrEnum):
    RESEARCH_BRIEF = "research_brief"
    PROGRAMMING = "programming"
    SITE_RESOURCE_COORDINATION = "site_resource_coordination"
    SCHEMATIC_DESIGN = "schematic_design"
    DESIGN_DEVELOPMENT = "design_development"
    CANDIDATE_COORDINATION = "candidate_coordination"
    EXECUTION_READY = "execution_ready"


DESIGN_PHASES: tuple[DesignPhase, ...] = tuple(DesignPhase)


@dataclass(frozen=True, slots=True)
class PhaseLadder:
    """What one of our phases is called in the three industry ladders.

    Two axes, both borrowed rather than invented. The coarse axis is the
    phase: RIBA Plan of Work 2020, the AIA phases, and the Chinese design
    stages of 《建筑工程设计文件编制深度规定》. The fine axis is the BIMForum
    Level of Development, which is the industry's own answer to "our stages
    are finer than SD/DD": LOD says how resolved the model is inside a phase,
    so a phase carries a range and a stage picks a level in it.
    """

    riba_stage: str
    aia: str
    cn: str
    lod_range: tuple[int, int] | None


# BIMForum's levels. 500 is field-verified as-built and belongs to no design
# phase: it is the reconstruction case, the evidence a monument's record
# already is, from which our stages work down to 100 and back up.
LOD_LEVELS: tuple[int, ...] = (100, 200, 300, 350, 400, 500)


PHASE_LADDER: Mapping[DesignPhase, PhaseLadder] = {
    DesignPhase.RESEARCH_BRIEF: PhaseLadder(
        riba_stage="0 Strategic Definition",
        aia="pre-design",
        cn="前期调研 / 项目建议书",
        lod_range=None,
    ),
    DesignPhase.PROGRAMMING: PhaseLadder(
        riba_stage="1 Preparation and Briefing",
        aia="programming",
        cn="任务书 / 策划",
        lod_range=None,
    ),
    DesignPhase.SITE_RESOURCE_COORDINATION: PhaseLadder(
        riba_stage="1 Preparation and Briefing (site information)",
        aia="pre-design",
        cn="场地 / 资源条件",
        lod_range=None,
    ),
    DesignPhase.SCHEMATIC_DESIGN: PhaseLadder(
        riba_stage="2 Concept Design",
        aia="Schematic Design",
        cn="方案设计",
        lod_range=(100, 200),
    ),
    DesignPhase.DESIGN_DEVELOPMENT: PhaseLadder(
        riba_stage="3 Spatial Coordination",
        aia="Design Development",
        cn="初步设计(扩初)",
        lod_range=(200, 300),
    ),
    DesignPhase.CANDIDATE_COORDINATION: PhaseLadder(
        riba_stage="3 Spatial Coordination (coordination of alternatives)",
        aia="DD coordination",
        cn="扩初深化 / 专业配合",
        lod_range=(300, 350),
    ),
    DesignPhase.EXECUTION_READY: PhaseLadder(
        riba_stage="4 Technical Design",
        aia="Construction Documents",
        cn="施工图设计",
        lod_range=(350, 400),
    ),
}


def _require_lod(value: object, phase: DesignPhase) -> int:
    """One level of development, inside the range its phase admits."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise StageWorkflowError("lod must be an integer level of development")
    if value not in LOD_LEVELS:
        raise StageWorkflowError(
            f"lod {value} is not one of {LOD_LEVELS}"
        )
    admitted = PHASE_LADDER[phase].lod_range
    if admitted is None:
        raise StageWorkflowError(
            f"phase {phase.value!r} resolves no model and admits no lod"
        )
    low, high = admitted
    if not low <= value <= high:
        raise StageWorkflowError(
            f"lod {value} is outside phase {phase.value!r} range "
            f"{low}-{high}"
        )
    return value


class StageWorkflowError(ValueError):
    """A workflow or run envelope is incomplete, stale, or cross-scoped."""


# The two harness workflows of ADR-007 rule 4: shared containers for
# coordination and review. They open a stage zero of their own so that a run
# has an envelope at all, they require no check, and they never become a
# project's workflow, close a project stage, or answer as its reference run.
# The Studio's reference-run rule and the runner's receipt read this one set.
HARNESS_WORKFLOW_IDS: frozenset[str] = frozenset(
    {"equivalence-harness", "studio-candidate-harness"}
)


class StageExitStatus(StrEnum):
    """Only a completed independent close may feed the next stage."""

    SATISFIED = "SATISFIED"


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


def _stage_index(value: object, field: str = "stage_index") -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise StageWorkflowError(f"{field} must be a non-negative integer")
    return value

from archflow.contracts.fields import text as _text
from archflow.contracts.fields import mapping
from archflow.project.version_refs import (
    register as _register_version_refs,
    register_content_digest as _register_version_ref_digest,
    register_derived as _register_derived_fields,
    register_reader as _register_version_ref_reader,
)


# ---------------------------------------------------------------- the stage-exit record
class StageClosureError(ValueError):
    """A composite closure request or retained receipt is malformed."""

class StageClosureStatus(StrEnum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"

class StageClosureFindingCode(StrEnum):
    MISSING_CHECK = "missing_check"
    DUPLICATE_CHECK = "duplicate_check"
    UNEXPECTED_CHECK = "unexpected_check"
    CHECKER_MISMATCH = "checker_mismatch"
    BRANCH_MISMATCH = "branch_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    SUBJECT_DIGEST_MISMATCH = "subject_digest_mismatch"
    DENOMINATOR_MISMATCH = "denominator_mismatch"
    CLAIM_BINDING_MISSING = "claim_binding_missing"
    APPLICABILITY_BINDING_MISSING = "applicability_binding_missing"
    ADOPTION_BINDING_MISSING = "adoption_binding_missing"
    SOURCE_BINDING_MISSING = "source_binding_missing"
    AUTHORITY_BINDING_MISSING = "authority_binding_missing"
    UNIVERSAL_BASIS_CONTAMINATED = "universal_basis_contaminated"
    CHECK_FAILED = "check_failed"
    CHECK_UNKNOWN = "check_unknown"
    NOT_APPLICABLE_FORBIDDEN = "not_applicable_forbidden"
    REVALIDATION_OPEN = "revalidation_open"
    # A seat that did not finish its round is a reason a stage did not close,
    # and none of the codes above says it: the rest of this vocabulary is
    # about checks and their bindings, not about who was still working.
    SEAT_INCOMPLETE = "seat_incomplete"

@dataclass(frozen=True, slots=True)
class StageClosureFinding:
    code: StageClosureFindingCode
    requirement_id: str | None = None
    receipt_id: str | None = None
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, StageClosureFindingCode):
            raise TypeError("code must be StageClosureFindingCode")
        if self.requirement_id is not None:
            _text(self.requirement_id, "requirement_id")
        if self.receipt_id is not None:
            _text(self.receipt_id, "receipt_id")
        if not isinstance(self.refs, tuple):
            raise TypeError("refs must be a tuple")
        normalized = tuple(sorted(_text(item, "finding ref") for item in self.refs))
        if len(normalized) != len(set(normalized)):
            raise StageClosureError("finding refs contain duplicates")
        object.__setattr__(self, "refs", normalized)

    @property
    def identity(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            self.code.value,
            self.requirement_id or "",
            self.receipt_id or "",
            self.refs,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "requirement_id": self.requirement_id,
            "receipt_id": self.receipt_id,
            "refs": list(self.refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageClosureFinding":
        payload = mapping(value, "finding")
        if set(payload) != {"code", "requirement_id", "receipt_id", "refs"}:
            raise StageClosureError("stage closure finding schema drifted")
        raw_refs = payload.get("refs", [])
        if not isinstance(raw_refs, list):
            raise TypeError("finding refs must be a list")
        return cls(
            code=StageClosureFindingCode(payload.get("code")),
            requirement_id=payload.get("requirement_id"),
            receipt_id=payload.get("receipt_id"),
            refs=tuple(raw_refs),
        )

@dataclass(frozen=True, slots=True)
class CompositeStageClosureReceipt:
    profile_id: str
    profile_digest: str
    stage_id: str
    branch: BranchRef
    stage_subject_ref: str
    subject_digest: str
    check_receipt_digests: tuple[str, ...]
    findings: tuple[StageClosureFinding, ...]
    status: StageClosureStatus

    SCHEMA = "CompositeStageClosureReceipt@1"

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        object.__setattr__(
            self,
            "profile_digest",
            require_sha256(self.profile_digest, "profile_digest"),
        )
        _text(self.stage_id, "stage_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        _text(self.stage_subject_ref, "stage_subject_ref")
        object.__setattr__(
            self,
            "subject_digest",
            require_sha256(self.subject_digest, "subject_digest"),
        )
        if not isinstance(self.check_receipt_digests, tuple):
            raise TypeError("check_receipt_digests must be a tuple")
        digests = tuple(
            sorted(
                require_sha256(item, "check_receipt_digest")
                for item in self.check_receipt_digests
            )
        )
        if len(digests) != len(set(digests)):
            raise StageClosureError("check receipt digests contain duplicates")
        object.__setattr__(self, "check_receipt_digests", digests)
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, StageClosureFinding) for item in self.findings
        ):
            raise TypeError("findings must contain StageClosureFinding")
        ordered = tuple(sorted(self.findings, key=lambda item: item.identity))
        if len(ordered) != len(set(item.identity for item in ordered)):
            raise StageClosureError("findings contain duplicates")
        object.__setattr__(self, "findings", ordered)
        if not isinstance(self.status, StageClosureStatus):
            raise TypeError("status must be StageClosureStatus")
        expected = (
            StageClosureStatus.SATISFIED
            if not self.findings
            else StageClosureStatus.OPEN
        )
        if self.status is not expected:
            raise StageClosureError("status disagrees with closure findings")

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "stage_id": self.stage_id,
            "branch": self.branch.to_dict(),
            "stage_subject_ref": self.stage_subject_ref,
            "subject_digest": self.subject_digest,
            "check_receipt_digests": list(self.check_receipt_digests),
            "findings": [item.to_dict() for item in self.findings],
            "status": self.status.value,
            "stage_acceptance_authority": False,
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self._content_dict())

    @property
    def receipt_id(self) -> str:
        return f"composite-stage-closure-{self.receipt_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CompositeStageClosureReceipt":
        payload = mapping(value, "stage closure receipt")
        expected = {
            "schema",
            "profile_id",
            "profile_digest",
            "stage_id",
            "branch",
            "stage_subject_ref",
            "subject_digest",
            "check_receipt_digests",
            "findings",
            "status",
            "stage_acceptance_authority",
            "design_authority",
            "canonical_write_authority",
            "receipt_id",
            "receipt_digest",
        }
        if set(payload) != expected or payload.get("schema") != cls.SCHEMA:
            raise StageClosureError("unsupported stage closure schema")
        raw_digests = payload.get("check_receipt_digests")
        raw_findings = payload.get("findings")
        if not isinstance(raw_digests, list):
            raise TypeError("check_receipt_digests must be a list")
        if not isinstance(raw_findings, list):
            raise TypeError("findings must be a list")
        receipt = cls(
            profile_id=_text(payload.get("profile_id"), "profile_id"),
            profile_digest=require_sha256(
                payload.get("profile_digest"), "profile_digest"
            ),
            stage_id=_text(payload.get("stage_id"), "stage_id"),
            branch=BranchRef.from_dict(payload.get("branch")),
            stage_subject_ref=_text(
                payload.get("stage_subject_ref"), "stage_subject_ref"
            ),
            subject_digest=require_sha256(
                payload.get("subject_digest"), "subject_digest"
            ),
            check_receipt_digests=tuple(raw_digests),
            findings=tuple(
                StageClosureFinding.from_dict(item) for item in raw_findings
            ),
            status=StageClosureStatus(payload.get("status")),
        )
        if payload.get("receipt_id") != receipt.receipt_id:
            raise StageClosureError("stage closure receipt identity changed")
        if payload.get("receipt_digest") != receipt.receipt_digest:
            raise StageClosureError("stage closure receipt digest changed")
        return receipt


@dataclass(frozen=True, slots=True)
class ProjectStage:
    """One ordered workflow stage; this is nested, not a retained record."""

    stage_id: str
    stage_index: int
    phase: DesignPhase
    required_roles: tuple[str, ...]
    required_checks: tuple[str, ...]
    close_obligation_id: str
    lod: int | None = None

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
    # ``lod`` is written only when a stage states one, so a stage authored
    # before the ladder serialises exactly as it did (ADR-004).
    OPTIONAL_RECORD_KEYS = frozenset({"lod"})

    def __post_init__(self) -> None:
        require_identifier(self.stage_id, "stage_id")
        _stage_index(self.stage_index)
        if not isinstance(self.phase, DesignPhase):
            raise TypeError("phase must be a DesignPhase")
        _identifier_tuple(self.required_roles, "required_roles")
        _identifier_tuple(self.required_checks, "required_checks")
        require_local_id(self.close_obligation_id, "close_obligation_id")
        if self.lod is not None:
            _require_lod(self.lod, self.phase)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "phase": self.phase.value,
            "required_roles": list(self.required_roles),
            "required_checks": list(self.required_checks),
            "close_obligation_id": self.close_obligation_id,
        }
        if self.lod is not None:
            payload["lod"] = self.lod
        return payload

    @classmethod
    def from_dict(cls, value: object) -> "ProjectStage":
        payload = _mapping(value, "project stage")
        required = {
            key: item
            for key, item in payload.items()
            if key not in cls.OPTIONAL_RECORD_KEYS
        }
        _exact(required, cls.RECORD_KEYS, "project stage")
        return cls(
            stage_id=payload["stage_id"],
            stage_index=payload["stage_index"],
            phase=DesignPhase(payload["phase"]),
            required_roles=string_tuple(
                payload["required_roles"], "required_roles"
            ),
            required_checks=string_tuple(
                payload["required_checks"], "required_checks"
            ),
            close_obligation_id=payload["close_obligation_id"],
            lod=payload.get("lod"),
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
        # Resolution only ever goes up. A stage that states no level does not
        # reset the ladder, so the stated levels are read in stage order.
        stated = tuple(
            item.lod for item in self.stages if item.lod is not None
        )
        if stated != tuple(sorted(stated)):
            raise StageWorkflowError(
                "workflow lod must be non-decreasing"
            )
        _logical_ref_tuple(self.basis_refs, "basis_refs")

    @property
    def workflow_digest(self) -> str:
        """The digest of the workflow as serialised.

        A stage that states no ``lod`` writes no ``lod`` key, so every
        workflow frozen before the ladder digests to exactly what it did; only
        a workflow that carries a level has a new digest (ADR-004).
        """

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
            basis_refs=string_tuple(payload["basis_refs"], "basis_refs"),
        )


def require_measurable(workflow: ProjectStageWorkflow) -> ProjectStageWorkflow:
    """A workflow may require only checks the spine can measure (ADR-007 r3).

    ``required_checks`` name ids in ``state_record.CHECK_KINDS`` — the ids
    ``capabilities.relation_checks.CHECKERS`` is keyed by — because the runner
    writes the closure from its own measurements: a required kind nothing can
    measure is a stage that never closes, and the closure could only ever say
    ``missing_check`` about it. So the workflow is refused where it is frozen
    and where a stage is opened against it, before either writes anything.

    This is deliberately not on ``from_dict``. Retained workflows — the two
    harnesses' own, the villa's frozen v1 — name free identifiers, and a
    retained record is read, not re-validated (ADR-004): a reader that refused
    them would make old runs unloadable without making any of them closable.
    """

    if not isinstance(workflow, ProjectStageWorkflow):
        raise TypeError("workflow must be a ProjectStageWorkflow")
    # state_record imports DesignPhase from this module, so the measurable set
    # is taken when a workflow is checked rather than when this module loads.
    from archflow.state.state_record import CHECK_KINDS

    for stage in workflow.stages:
        for check in stage.required_checks:
            if check not in CHECK_KINDS:
                raise StageWorkflowError(
                    f"workflow {workflow.workflow_id!r} stage "
                    f"{stage.stage_id!r} requires check {check!r}, which the "
                    "spine cannot measure; the registered check kinds are "
                    f"{', '.join(sorted(CHECK_KINDS))}"
                )
    return workflow


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
            required_roles=string_tuple(
                payload["required_roles"], "required_roles"
            ),
            required_checks=string_tuple(
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

    This is the one gate both ``open_stage_run_envelope`` and the runner's
    ``StageExecutionGuard`` pass through, so it is where a workflow that names
    an unmeasurable check is refused before any record of the run is written.
    """

    require_measurable(workflow)
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
    "HARNESS_WORKFLOW_IDS",
    "ProjectStage",
    "ProjectStageWorkflow",
    "StageExitBinding",
    "StageExitStatus",
    "StageRunEnvelope",
    "StageRunPredecessor",
    "StageWorkflowError",
    "open_stage_run_envelope",
    "require_measurable",
    "require_stage_exit_binding",
    "require_stage_run_envelope",
]


# A stage envelope and its exit binding both restate the run's canonical base,
# and the composite closure reaches one through the design branch it names.
# All three also serialise a digest of their own contents, so each says which
# fields those are and how to rebuild them: an exit binding cites the
# envelope's digest and the closure's, and a runner receipt cites all of them.
# Without the rebuild a migrated record keeps a digest of its pre-migration
# self, and its own ``from_dict`` refuses it.
VERSION_REF_POINTERS = {
    "StageRunEnvelope@1": ("/base",),
    "StageExitBinding@1": ("/base",),
}

_CLOSURE_DERIVED = ("receipt_id", "receipt_digest")


def _rebuild_stage_closure(payload):
    """Restate a closure receipt's own identity from its restated contents."""

    content = {
        key: value for key, value in payload.items() if key not in _CLOSURE_DERIVED
    }
    digest = canonical_digest(content)
    return {
        **content,
        "receipt_id": f"composite-stage-closure-{digest}",
        "receipt_digest": digest,
    }


_register_version_refs(VERSION_REF_POINTERS)
_register_derived_fields("StageRunEnvelope@1", ())
_register_derived_fields("StageExitBinding@1", ())
_register_derived_fields(
    "CompositeStageClosureReceipt@1", _CLOSURE_DERIVED, _rebuild_stage_closure,
)
_register_version_ref_digest(
    "StageRunEnvelope@1", lambda payload: StageRunEnvelope.from_dict(payload).envelope_digest,
)
_register_version_ref_digest(
    "StageExitBinding@1", lambda payload: StageExitBinding.from_dict(payload).exit_digest,
)
# Through the reader, not through the rebuild: a rebuild that vouches for
# itself proves nothing, and this is the reader every later stage uses.
_register_version_ref_digest(
    "CompositeStageClosureReceipt@1",
    lambda payload: CompositeStageClosureReceipt.from_dict(payload).receipt_digest,
)
_register_version_ref_reader(
    "CompositeStageClosureReceipt@1", CompositeStageClosureReceipt.from_dict,
)
_register_version_ref_reader("StageRunEnvelope@1", StageRunEnvelope.from_dict)
_register_version_ref_reader("StageExitBinding@1", StageExitBinding.from_dict)
