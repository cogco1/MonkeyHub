"""Canonical immutable, branch-bound evidence bundles for one design stage.

``StageEvidencePack`` is a compact audit index.  It does not copy retained
research, select a design branch, accept a stage, or write canonical project
state.  Instead it binds the exact P036 records and model artifacts that a
progress panel or later compiler must inspect together.

The pack deliberately separates three questions:

* were the stage materials compiled;
* did the cited sufficiency/convergence records close their denominators; and
* does the retained model artifact still match the geometry program.

Only an independently persisted review/promotion receipt can answer whether a
stage is accepted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Mapping

from archflow.project.refs import (
    BranchRef,
    ProjectArtifactRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)


_HEX = frozenset("0123456789abcdef")


class StageEvidencePackError(ValueError):
    """A stage pack drifted, crossed scope, or claimed unsupported closure."""


class StagePackCompilationStatus(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


class StageEvidenceRole(StrEnum):
    BRANCH_SELECTION = "branch_selection"
    BRANCH_SCOPE = "branch_scope"
    DECISION_UNIVERSE = "decision_universe"
    EVIDENCE_POLICY = "evidence_policy"
    QUERY = "query"
    SNAPSHOT = "snapshot"
    ADOPTION = "adoption"
    CALIBRATION = "calibration"
    BASIS_INDEX = "basis_index"
    DEPENDENCY_LEDGER = "dependency_ledger"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"
    DESIGN_STATE = "design_state"
    GEOMETRY_PROGRAM = "geometry_program"
    MODEL_INSPECTION = "model_inspection"
    STAGE_GATE = "stage_gate"
    STAGE_CONVERGENCE = "stage_convergence"
    REVIEW = "review"


class StageArtifactRole(StrEnum):
    CAD_MODEL = "cad_model"
    IFC_MODEL = "ifc_model"
    GEOMETRY_SCRIPT = "geometry_script"
    PREVIEW = "preview"
    REPORT = "report"


class StageEvidenceGapKind(StrEnum):
    SCOPE_UNBOUND = "scope_unbound"
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICT = "conflict"
    AUTHORITY_PENDING = "authority_pending"
    DEPENDENCY_OPEN = "dependency_open"
    GATE_FAILED = "gate_failed"
    ARTIFACT_MISSING = "artifact_missing"
    DIGEST_MISMATCH = "digest_mismatch"
    UNSUPPORTED = "unsupported"


class StageEvidenceGapSeverity(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_sha256(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    candidate = value.lower()
    if len(candidate) != 64 or any(char not in _HEX for char in candidate):
        raise StageEvidencePackError(f"{field} must be a SHA-256 digest")
    return candidate


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageEvidencePackError(f"{field} must be non-empty text")
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be boolean")
    return value


def _exact(payload: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(payload) != expected:
        raise StageEvidencePackError(f"{field} schema drifted")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _record_ref_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_ref_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = _mapping(value, field)
    _exact(payload, {"project_id", "relative_path", "sha256", "media_type"}, field)
    return ProjectRecordRef(
        project_id=str(payload["project_id"]),
        relative_path=str(payload["relative_path"]),
        sha256=str(payload["sha256"]),
        media_type=str(payload["media_type"]),
    )


def _artifact_ref_dict(ref: ProjectArtifactRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "artifact_id": ref.artifact_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _artifact_ref_from_dict(value: object, field: str) -> ProjectArtifactRef:
    payload = _mapping(value, field)
    _exact(
        payload,
        {"project_id", "artifact_id", "relative_path", "sha256", "media_type"},
        field,
    )
    return ProjectArtifactRef(
        project_id=str(payload["project_id"]),
        artifact_id=str(payload["artifact_id"]),
        relative_path=str(payload["relative_path"]),
        sha256=str(payload["sha256"]),
        media_type=str(payload["media_type"]),
    )


def _base_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=str(payload["project_id"]),
        version=payload["version"],
        state_sha256=str(payload["state_sha256"]),
    )


def _branch_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _lineage_branch_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "run": {
            "project_id": branch.run.project_id,
            "run_id": branch.run.run_id,
            "base": _base_dict(branch.run.base),
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _lineage_branch_from_dict(value: object) -> BranchRef:
    payload = _mapping(value, "predecessor branch")
    _exact(payload, {"run", "branch_id", "epoch"}, "predecessor branch")
    run_payload = _mapping(payload["run"], "predecessor run")
    _exact(
        run_payload,
        {"project_id", "run_id", "base"},
        "predecessor run",
    )
    run = RunRef(
        project_id=str(run_payload["project_id"]),
        run_id=str(run_payload["run_id"]),
        base=_base_from_dict(run_payload["base"]),
    )
    return BranchRef(
        run=run,
        branch_id=str(payload["branch_id"]),
        epoch=payload["epoch"],
    )


@dataclass(frozen=True, slots=True)
class StageEvidenceBinding:
    role: StageEvidenceRole
    ref: ProjectRecordRef

    SCHEMA = "StageEvidenceBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, StageEvidenceRole):
            raise TypeError("role must be a StageEvidenceRole")
        if not isinstance(self.ref, ProjectRecordRef):
            raise TypeError("ref must be a ProjectRecordRef")

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.role.value, self.ref.relative_path, self.ref.sha256

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "ref": _record_ref_dict(self.ref),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageEvidenceBinding":
        payload = _mapping(value, "stage evidence binding")
        _exact(payload, {"schema", "role", "ref"}, "stage evidence binding")
        if payload["schema"] != cls.SCHEMA:
            raise StageEvidencePackError("stage evidence binding schema changed")
        return cls(
            role=StageEvidenceRole(str(payload["role"])),
            ref=_record_ref_from_dict(payload["ref"], "binding ref"),
        )


@dataclass(frozen=True, slots=True)
class StageArtifactBinding:
    role: StageArtifactRole
    ref: ProjectArtifactRef
    program_digest: str

    SCHEMA = "StageArtifactBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, StageArtifactRole):
            raise TypeError("role must be a StageArtifactRole")
        if not isinstance(self.ref, ProjectArtifactRef):
            raise TypeError("ref must be a ProjectArtifactRef")
        object.__setattr__(
            self,
            "program_digest",
            _require_sha256(self.program_digest, "artifact program_digest"),
        )

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.role.value, self.ref.relative_path, self.ref.sha256

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "ref": _artifact_ref_dict(self.ref),
            "program_digest": self.program_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageArtifactBinding":
        payload = _mapping(value, "stage artifact binding")
        _exact(
            payload,
            {"schema", "role", "ref", "program_digest"},
            "stage artifact binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageEvidencePackError("stage artifact binding schema changed")
        return cls(
            role=StageArtifactRole(str(payload["role"])),
            ref=_artifact_ref_from_dict(payload["ref"], "artifact ref"),
            program_digest=str(payload["program_digest"]),
        )


@dataclass(frozen=True, slots=True)
class StageEvidenceGap:
    gap_id: str
    kind: StageEvidenceGapKind
    severity: StageEvidenceGapSeverity
    description: str
    decision_refs: tuple[str, ...] = ()
    remediation: str = ""

    SCHEMA = "StageEvidenceGap@1"

    def __post_init__(self) -> None:
        require_identifier(self.gap_id, "gap_id")
        if not isinstance(self.kind, StageEvidenceGapKind):
            raise TypeError("kind must be a StageEvidenceGapKind")
        if not isinstance(self.severity, StageEvidenceGapSeverity):
            raise TypeError("severity must be a StageEvidenceGapSeverity")
        _text(self.description, "gap description")
        if not isinstance(self.decision_refs, tuple):
            raise TypeError("decision_refs must be a tuple")
        normalized = tuple(sorted(set(self.decision_refs)))
        if normalized != self.decision_refs:
            raise StageEvidencePackError(
                "decision_refs must be sorted and unique"
            )
        for ref in self.decision_refs:
            _text(ref, "decision ref")
        if not isinstance(self.remediation, str):
            raise TypeError("remediation must be text")

    @property
    def identity(self) -> tuple[str, str]:
        return self.severity.value, self.gap_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "gap_id": self.gap_id,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "description": self.description,
            "decision_refs": list(self.decision_refs),
            "remediation": self.remediation,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageEvidenceGap":
        payload = _mapping(value, "stage evidence gap")
        _exact(
            payload,
            {
                "schema",
                "gap_id",
                "kind",
                "severity",
                "description",
                "decision_refs",
                "remediation",
            },
            "stage evidence gap",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageEvidencePackError("stage evidence gap schema changed")
        refs = payload["decision_refs"]
        if not isinstance(refs, list):
            raise TypeError("gap decision_refs must be a list")
        return cls(
            gap_id=str(payload["gap_id"]),
            kind=StageEvidenceGapKind(str(payload["kind"])),
            severity=StageEvidenceGapSeverity(str(payload["severity"])),
            description=str(payload["description"]),
            decision_refs=tuple(str(item) for item in refs),
            remediation=str(payload["remediation"]),
        )


@dataclass(frozen=True, slots=True)
class StageClosureSummary:
    evidence_sufficient: bool
    dependencies_closed: bool
    hard_gates_passed: bool
    stage_ready: bool
    model_artifact_current: bool

    SCHEMA = "StageClosureSummary@1"

    def __post_init__(self) -> None:
        for field in (
            "evidence_sufficient",
            "dependencies_closed",
            "hard_gates_passed",
            "stage_ready",
            "model_artifact_current",
        ):
            _bool(getattr(self, field), field)

    @property
    def closed(self) -> bool:
        return all(
            (
                self.evidence_sufficient,
                self.dependencies_closed,
                self.hard_gates_passed,
                self.stage_ready,
                self.model_artifact_current,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "evidence_sufficient": self.evidence_sufficient,
            "dependencies_closed": self.dependencies_closed,
            "hard_gates_passed": self.hard_gates_passed,
            "stage_ready": self.stage_ready,
            "model_artifact_current": self.model_artifact_current,
            "closed": self.closed,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageClosureSummary":
        payload = _mapping(value, "stage closure summary")
        _exact(
            payload,
            {
                "schema",
                "evidence_sufficient",
                "dependencies_closed",
                "hard_gates_passed",
                "stage_ready",
                "model_artifact_current",
                "closed",
            },
            "stage closure summary",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageEvidencePackError("stage closure summary schema changed")
        result = cls(
            evidence_sufficient=_bool(
                payload["evidence_sufficient"], "evidence_sufficient"
            ),
            dependencies_closed=_bool(
                payload["dependencies_closed"], "dependencies_closed"
            ),
            hard_gates_passed=_bool(
                payload["hard_gates_passed"], "hard_gates_passed"
            ),
            stage_ready=_bool(payload["stage_ready"], "stage_ready"),
            model_artifact_current=_bool(
                payload["model_artifact_current"], "model_artifact_current"
            ),
        )
        if payload["closed"] is not result.closed:
            raise StageEvidencePackError("stage closure summary closed flag drifted")
        return result


@dataclass(frozen=True, slots=True)
class StagePackPredecessor:
    stage_id: str
    stage_index: int
    pack_ref: ProjectRecordRef
    program_digest: str

    SCHEMA = "StagePackPredecessor@1"

    def __post_init__(self) -> None:
        require_identifier(self.stage_id, "predecessor stage_id")
        if (
            not isinstance(self.stage_index, int)
            or isinstance(self.stage_index, bool)
            or self.stage_index < 0
        ):
            raise StageEvidencePackError(
                "predecessor stage_index must be a non-negative integer"
            )
        if not isinstance(self.pack_ref, ProjectRecordRef):
            raise TypeError("predecessor pack_ref must be a ProjectRecordRef")
        object.__setattr__(
            self,
            "program_digest",
            _require_sha256(self.program_digest, "predecessor program_digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "pack_ref": _record_ref_dict(self.pack_ref),
            "program_digest": self.program_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StagePackPredecessor":
        payload = _mapping(value, "stage pack predecessor")
        _exact(
            payload,
            {"schema", "stage_id", "stage_index", "pack_ref", "program_digest"},
            "stage pack predecessor",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageEvidencePackError("stage pack predecessor schema changed")
        return cls(
            stage_id=str(payload["stage_id"]),
            stage_index=payload["stage_index"],
            pack_ref=_record_ref_from_dict(payload["pack_ref"], "predecessor ref"),
            program_digest=str(payload["program_digest"]),
        )


@dataclass(frozen=True, slots=True)
class CrossRunStagePackPredecessor:
    stage_id: str
    stage_index: int
    branch: BranchRef
    pack_ref: ProjectRecordRef
    program_digest: str

    SCHEMA = "CrossRunStagePackPredecessor@1"

    def __post_init__(self) -> None:
        require_identifier(self.stage_id, "predecessor stage_id")
        if (
            not isinstance(self.stage_index, int)
            or isinstance(self.stage_index, bool)
            or self.stage_index < 0
        ):
            raise StageEvidencePackError(
                "predecessor stage_index must be a non-negative integer"
            )
        if not isinstance(self.branch, BranchRef):
            raise TypeError("predecessor branch must be a BranchRef")
        self.branch.run.base.require_digest()
        if not isinstance(self.pack_ref, ProjectRecordRef):
            raise TypeError("predecessor pack_ref must be a ProjectRecordRef")
        object.__setattr__(
            self,
            "program_digest",
            _require_sha256(self.program_digest, "predecessor program_digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage_id": self.stage_id,
            "stage_index": self.stage_index,
            "branch": _lineage_branch_dict(self.branch),
            "pack_ref": _record_ref_dict(self.pack_ref),
            "program_digest": self.program_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CrossRunStagePackPredecessor":
        payload = _mapping(value, "cross-run stage pack predecessor")
        _exact(
            payload,
            {
                "schema",
                "stage_id",
                "stage_index",
                "branch",
                "pack_ref",
                "program_digest",
            },
            "cross-run stage pack predecessor",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageEvidencePackError(
                "cross-run stage pack predecessor schema changed"
            )
        return cls(
            stage_id=str(payload["stage_id"]),
            stage_index=payload["stage_index"],
            branch=_lineage_branch_from_dict(payload["branch"]),
            pack_ref=_record_ref_from_dict(payload["pack_ref"], "predecessor ref"),
            program_digest=str(payload["program_digest"]),
        )


def _stage_pack_predecessor_from_dict(
    value: object,
) -> StagePackPredecessor | CrossRunStagePackPredecessor:
    payload = _mapping(value, "stage pack predecessor")
    schema = payload.get("schema")
    if schema == StagePackPredecessor.SCHEMA:
        return StagePackPredecessor.from_dict(payload)
    if schema == CrossRunStagePackPredecessor.SCHEMA:
        return CrossRunStagePackPredecessor.from_dict(payload)
    raise StageEvidencePackError("stage pack predecessor schema changed")


_COMPLETE_ROLES = frozenset(
    {
        StageEvidenceRole.BRANCH_SELECTION,
        StageEvidenceRole.BRANCH_SCOPE,
        StageEvidenceRole.DECISION_UNIVERSE,
        StageEvidenceRole.BASIS_INDEX,
        StageEvidenceRole.DEPENDENCY_LEDGER,
        StageEvidenceRole.EVIDENCE_SUFFICIENCY,
        StageEvidenceRole.DESIGN_STATE,
        StageEvidenceRole.GEOMETRY_PROGRAM,
        StageEvidenceRole.MODEL_INSPECTION,
        StageEvidenceRole.STAGE_GATE,
        StageEvidenceRole.STAGE_CONVERGENCE,
        StageEvidenceRole.REVIEW,
    }
)

_RUN_RECORD_ROLES = frozenset({StageEvidenceRole.BRANCH_SELECTION})
_RUN_REVIEW_ROLES = frozenset({StageEvidenceRole.REVIEW})


@dataclass(frozen=True, slots=True)
class StageEvidencePack:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    branch: BranchRef
    scope_ref: ProjectRecordRef
    stage_id: str
    stage_index: int
    revision: int
    program_digest: str
    contract_ref: ProjectRecordRef
    bindings: tuple[StageEvidenceBinding, ...]
    artifacts: tuple[StageArtifactBinding, ...]
    gaps: tuple[StageEvidenceGap, ...]
    closure: StageClosureSummary
    compilation_status: StagePackCompilationStatus
    predecessor: StagePackPredecessor | CrossRunStagePackPredecessor | None = None
    supersedes_pack_ref: ProjectRecordRef | None = None

    SCHEMA = "StageEvidencePack@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.stage_id, "stage_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be a ProjectVersionRef")
        self.base.require_digest()
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        expected_run = RunRef(
            project_id=self.project_id,
            run_id=self.run_id,
            base=self.base,
        )
        if self.branch.run != expected_run:
            raise StageEvidencePackError(
                "branch does not share the pack's exact project/run/base"
            )
        if (
            not isinstance(self.stage_index, int)
            or isinstance(self.stage_index, bool)
            or self.stage_index < 0
        ):
            raise StageEvidencePackError(
                "stage_index must be a non-negative integer"
            )
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise StageEvidencePackError("revision must be a positive integer")
        object.__setattr__(
            self,
            "program_digest",
            _require_sha256(self.program_digest, "program_digest"),
        )
        for field in ("scope_ref", "contract_ref"):
            value = getattr(self, field)
            if not isinstance(value, ProjectRecordRef):
                raise TypeError(f"{field} must be a ProjectRecordRef")
            self._require_project_ref(value, field)
        branch_prefix = self._branch_prefix
        if not self.scope_ref.relative_path.startswith(branch_prefix):
            raise StageEvidencePackError(
                "scope_ref must live in this exact P036 branch area"
            )
        if not self.contract_ref.relative_path.startswith(
            f"runs/{self.run_id}/records/"
        ):
            raise StageEvidencePackError(
                "contract_ref must live in this exact P036 run record area"
            )
        if not isinstance(self.bindings, tuple):
            raise TypeError("bindings must be a tuple")
        if any(not isinstance(item, StageEvidenceBinding) for item in self.bindings):
            raise TypeError("bindings must contain StageEvidenceBinding")
        ordered_bindings = tuple(sorted(self.bindings, key=lambda item: item.identity))
        if ordered_bindings != self.bindings:
            raise StageEvidencePackError("bindings must be deterministically ordered")
        if len({item.identity for item in self.bindings}) != len(self.bindings):
            raise StageEvidencePackError("bindings contains duplicates")
        for binding in self.bindings:
            self._require_project_ref(binding.ref, "binding ref")
            expected_prefix = branch_prefix
            if binding.role in _RUN_RECORD_ROLES:
                expected_prefix = f"runs/{self.run_id}/records/"
            elif binding.role in _RUN_REVIEW_ROLES:
                expected_prefix = f"runs/{self.run_id}/reviews/"
            if not binding.ref.relative_path.startswith(expected_prefix):
                raise StageEvidencePackError(
                    f"{binding.role.value} binding is outside its P036 area"
                )
        if not isinstance(self.artifacts, tuple):
            raise TypeError("artifacts must be a tuple")
        if any(not isinstance(item, StageArtifactBinding) for item in self.artifacts):
            raise TypeError("artifacts must contain StageArtifactBinding")
        ordered_artifacts = tuple(sorted(self.artifacts, key=lambda item: item.identity))
        if ordered_artifacts != self.artifacts:
            raise StageEvidencePackError("artifacts must be deterministically ordered")
        if len({item.identity for item in self.artifacts}) != len(self.artifacts):
            raise StageEvidencePackError("artifacts contains duplicates")
        for artifact in self.artifacts:
            if artifact.ref.project_id != self.project_id:
                raise StageEvidencePackError("artifact belongs to another project")
            if not artifact.ref.relative_path.startswith("objects/sha256/"):
                raise StageEvidencePackError(
                    "binary artifact must live in the P036 object store"
                )
            if artifact.program_digest != self.program_digest:
                raise StageEvidencePackError(
                    "artifact does not bind the current geometry program"
                )
        if not isinstance(self.gaps, tuple):
            raise TypeError("gaps must be a tuple")
        if any(not isinstance(item, StageEvidenceGap) for item in self.gaps):
            raise TypeError("gaps must contain StageEvidenceGap")
        ordered_gaps = tuple(sorted(self.gaps, key=lambda item: item.identity))
        if ordered_gaps != self.gaps:
            raise StageEvidencePackError("gaps must be deterministically ordered")
        if len({item.gap_id for item in self.gaps}) != len(self.gaps):
            raise StageEvidencePackError("gaps contains duplicate ids")
        if not isinstance(self.closure, StageClosureSummary):
            raise TypeError("closure must be a StageClosureSummary")
        if not isinstance(self.compilation_status, StagePackCompilationStatus):
            raise TypeError(
                "compilation_status must be a StagePackCompilationStatus"
            )
        self._validate_lineage()
        self._validate_status()

    @property
    def _branch_prefix(self) -> str:
        return (
            f"runs/{self.run_id}/branches/{self.branch.branch_id}/records/"
        )

    def _require_project_ref(self, ref: ProjectRecordRef, field: str) -> None:
        if ref.project_id != self.project_id:
            raise StageEvidencePackError(f"{field} belongs to another project")
        run_prefix = f"runs/{self.run_id}/"
        if not ref.relative_path.startswith(run_prefix):
            raise StageEvidencePackError(
                f"{field} does not belong to this exact P036 run"
            )

    def _validate_lineage(self) -> None:
        if self.predecessor is not None and not isinstance(
            self.predecessor,
            (StagePackPredecessor, CrossRunStagePackPredecessor),
        ):
            raise TypeError(
                "predecessor must be a StagePackPredecessor or "
                "CrossRunStagePackPredecessor"
            )
        if self.stage_index == 0:
            if self.predecessor is not None:
                raise StageEvidencePackError("stage 0 cannot have a predecessor")
        else:
            if self.predecessor is None:
                raise StageEvidencePackError(
                    "stage N must bind the exact Stage N-1 pack"
                )
            if self.predecessor.stage_index != self.stage_index - 1:
                raise StageEvidencePackError(
                    "predecessor is not the immediately previous stage"
                )
            if isinstance(self.predecessor, StagePackPredecessor):
                self._require_project_ref(
                    self.predecessor.pack_ref,
                    "predecessor ref",
                )
                if not self.predecessor.pack_ref.relative_path.startswith(
                    self._branch_prefix
                ):
                    raise StageEvidencePackError(
                        "predecessor pack belongs to another branch"
                    )
            else:
                predecessor_run = self.predecessor.branch.run
                if (
                    predecessor_run.project_id != self.project_id
                    or self.predecessor.pack_ref.project_id != self.project_id
                ):
                    raise StageEvidencePackError(
                        "cross-run predecessor belongs to another project"
                    )
                if predecessor_run.base != self.base:
                    raise StageEvidencePackError(
                        "cross-run predecessor does not share the pack's exact "
                        "canonical base"
                    )
                if self.predecessor.branch.branch_id != self.branch.branch_id:
                    raise StageEvidencePackError(
                        "cross-run predecessor does not share the pack's branch_id"
                    )
                if predecessor_run.run_id == self.run_id:
                    raise StageEvidencePackError(
                        "cross-run predecessor must come from a different run"
                    )
                predecessor_prefix = (
                    f"runs/{predecessor_run.run_id}/branches/"
                    f"{self.predecessor.branch.branch_id}/records/"
                )
                if not self.predecessor.pack_ref.relative_path.startswith(
                    predecessor_prefix
                ):
                    raise StageEvidencePackError(
                        "cross-run predecessor pack_ref is outside its "
                        "predecessor branch run path"
                    )
        if self.revision == 1 and self.supersedes_pack_ref is not None:
            raise StageEvidencePackError("revision 1 cannot supersede another pack")
        if self.revision > 1 and self.supersedes_pack_ref is None:
            raise StageEvidencePackError(
                "later revisions must bind the superseded pack"
            )
        if self.supersedes_pack_ref is not None:
            self._require_project_ref(
                self.supersedes_pack_ref,
                "supersedes_pack_ref",
            )
            if not self.supersedes_pack_ref.relative_path.startswith(
                self._branch_prefix
            ):
                raise StageEvidencePackError(
                    "superseded pack belongs to another branch"
                )

    def _validate_status(self) -> None:
        blocking = tuple(
            gap
            for gap in self.gaps
            if gap.severity is StageEvidenceGapSeverity.BLOCKING
        )
        roles = {binding.role for binding in self.bindings}
        has_current_cad = any(
            artifact.role is StageArtifactRole.CAD_MODEL
            for artifact in self.artifacts
        )
        if self.compilation_status is StagePackCompilationStatus.COMPLETE:
            if blocking:
                raise StageEvidencePackError(
                    "complete pack cannot retain blocking gaps"
                )
            missing_roles = _COMPLETE_ROLES - roles
            if missing_roles:
                names = ", ".join(sorted(item.value for item in missing_roles))
                raise StageEvidencePackError(
                    f"complete pack is missing required bindings: {names}"
                )
            if not self.closure.closed:
                raise StageEvidencePackError(
                    "complete pack cannot claim open stage closure"
                )
            if not has_current_cad:
                raise StageEvidencePackError(
                    "complete pack requires a current CAD model artifact"
                )
        else:
            if self.closure.closed and not blocking and _COMPLETE_ROLES <= roles:
                raise StageEvidencePackError(
                    "fully closed pack cannot remain marked incomplete"
                )
        if self.closure.model_artifact_current != has_current_cad:
            raise StageEvidencePackError(
                "model_artifact_current disagrees with CAD artifact binding"
            )

    @property
    def pack_digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def blocking_gap_ids(self) -> tuple[str, ...]:
        return tuple(
            gap.gap_id
            for gap in self.gaps
            if gap.severity is StageEvidenceGapSeverity.BLOCKING
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "branch": _branch_dict(self.branch),
            "scope_ref": _record_ref_dict(self.scope_ref),
            "stage": {
                "stage_id": self.stage_id,
                "stage_index": self.stage_index,
                "revision": self.revision,
            },
            "program_digest": self.program_digest,
            "contract_ref": _record_ref_dict(self.contract_ref),
            "predecessor": (
                None if self.predecessor is None else self.predecessor.to_dict()
            ),
            "supersedes_pack_ref": (
                None
                if self.supersedes_pack_ref is None
                else _record_ref_dict(self.supersedes_pack_ref)
            ),
            "bindings": [item.to_dict() for item in self.bindings],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "gaps": [item.to_dict() for item in self.gaps],
            "closure": self.closure.to_dict(),
            "compilation_status": self.compilation_status.value,
            "selection_authority": False,
            "evidence_authority": False,
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageEvidencePack":
        payload = _mapping(value, "stage evidence pack")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "branch",
                "scope_ref",
                "stage",
                "program_digest",
                "contract_ref",
                "predecessor",
                "supersedes_pack_ref",
                "bindings",
                "artifacts",
                "gaps",
                "closure",
                "compilation_status",
                "selection_authority",
                "evidence_authority",
                "stage_acceptance_authority",
                "canonical_write_authority",
            },
            "stage evidence pack",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["selection_authority"] is not False
            or payload["evidence_authority"] is not False
            or payload["stage_acceptance_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise StageEvidencePackError("stage evidence pack acquired authority")
        base = _base_from_dict(payload["base"])
        branch_payload = _mapping(payload["branch"], "branch")
        _exact(branch_payload, {"branch_id", "epoch"}, "branch")
        run = RunRef(
            project_id=str(payload["project_id"]),
            run_id=str(payload["run_id"]),
            base=base,
        )
        branch = BranchRef(
            run=run,
            branch_id=str(branch_payload["branch_id"]),
            epoch=branch_payload["epoch"],
        )
        stage = _mapping(payload["stage"], "stage")
        _exact(stage, {"stage_id", "stage_index", "revision"}, "stage")
        bindings = payload["bindings"]
        artifacts = payload["artifacts"]
        gaps = payload["gaps"]
        if not isinstance(bindings, list):
            raise TypeError("bindings must be a list")
        if not isinstance(artifacts, list):
            raise TypeError("artifacts must be a list")
        if not isinstance(gaps, list):
            raise TypeError("gaps must be a list")
        predecessor = payload["predecessor"]
        supersedes = payload["supersedes_pack_ref"]
        return cls(
            project_id=str(payload["project_id"]),
            run_id=str(payload["run_id"]),
            base=base,
            branch=branch,
            scope_ref=_record_ref_from_dict(payload["scope_ref"], "scope_ref"),
            stage_id=str(stage["stage_id"]),
            stage_index=stage["stage_index"],
            revision=stage["revision"],
            program_digest=str(payload["program_digest"]),
            contract_ref=_record_ref_from_dict(
                payload["contract_ref"], "contract_ref"
            ),
            predecessor=(
                None
                if predecessor is None
                else _stage_pack_predecessor_from_dict(predecessor)
            ),
            supersedes_pack_ref=(
                None
                if supersedes is None
                else _record_ref_from_dict(supersedes, "supersedes_pack_ref")
            ),
            bindings=tuple(StageEvidenceBinding.from_dict(item) for item in bindings),
            artifacts=tuple(StageArtifactBinding.from_dict(item) for item in artifacts),
            gaps=tuple(StageEvidenceGap.from_dict(item) for item in gaps),
            closure=StageClosureSummary.from_dict(payload["closure"]),
            compilation_status=StagePackCompilationStatus(
                str(payload["compilation_status"])
            ),
        )


def compile_stage_evidence_pack(
    *,
    project_id: str,
    run: RunRef,
    branch: BranchRef,
    scope_ref: ProjectRecordRef,
    stage_id: str,
    stage_index: int,
    revision: int,
    program_digest: str,
    contract_ref: ProjectRecordRef,
    bindings: tuple[StageEvidenceBinding, ...],
    artifacts: tuple[StageArtifactBinding, ...],
    gaps: tuple[StageEvidenceGap, ...],
    closure: StageClosureSummary,
    predecessor: StagePackPredecessor | CrossRunStagePackPredecessor | None = None,
    supersedes_pack_ref: ProjectRecordRef | None = None,
) -> StageEvidencePack:
    """Compile a deterministic pack while deriving status from real deficits.

    Callers cannot force ``COMPLETE``.  Blocking gaps, missing mandatory record
    roles, open closure, or a missing current CAD model keep the pack
    ``INCOMPLETE``.
    """

    ordered_bindings = tuple(sorted(bindings, key=lambda item: item.identity))
    ordered_artifacts = tuple(sorted(artifacts, key=lambda item: item.identity))
    ordered_gaps = tuple(sorted(gaps, key=lambda item: item.identity))
    roles = {item.role for item in ordered_bindings}
    blocking = any(
        item.severity is StageEvidenceGapSeverity.BLOCKING
        for item in ordered_gaps
    )
    has_current_cad = any(
        item.role is StageArtifactRole.CAD_MODEL
        for item in ordered_artifacts
    )
    complete = (
        not blocking
        and closure.closed
        and _COMPLETE_ROLES <= roles
        and has_current_cad
    )
    return StageEvidencePack(
        project_id=project_id,
        run_id=run.run_id,
        base=run.base,
        branch=branch,
        scope_ref=scope_ref,
        stage_id=stage_id,
        stage_index=stage_index,
        revision=revision,
        program_digest=program_digest,
        contract_ref=contract_ref,
        bindings=ordered_bindings,
        artifacts=ordered_artifacts,
        gaps=ordered_gaps,
        closure=closure,
        compilation_status=(
            StagePackCompilationStatus.COMPLETE
            if complete
            else StagePackCompilationStatus.INCOMPLETE
        ),
        predecessor=predecessor,
        supersedes_pack_ref=supersedes_pack_ref,
    )
