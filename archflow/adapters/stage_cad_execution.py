"""Authority-free stage guards around speculative Rhino export planning.

The ordinary CAD adapter intentionally remains usable for exploratory previews.
This module adds an opt-in boundary for callers that need to retain whether an
export is merely pre-stage or is mechanically bound to an entered stage.  It
does not persist records, execute Rhino, accept a stage, or write canonical
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping
from urllib.parse import quote

from archflow.adapters.cad_execution import (
    CadExecutionError,
    CadExecutionStatus,
    RhinoCadExecutionReceipt,
    RhinoCadExportPlan,
    RhinoCadProgramBinding,
    execute_rhino_three_dm_export,
    prepare_rhino_three_dm_export,
)
from archflow.control.stage_artifacts import (
    StageArtifactClaim,
    StageArtifactStatus,
)
from archflow.project.refs import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.runtime.geometry_compiler import CompiledGeometryProgram
from archflow.state.geometry_program import digest_value, require_sha256


class StageCadExportError(CadExecutionError):
    """A CAD export is not bound to the supplied stage evidence exactly."""


_AUTHORITY_FIELDS = {
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
    "materialization_authority": False,
    "readback_authority": False,
}


def _mapping(value: object, keys: set[str], field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    if set(value) != keys:
        raise StageCadExportError(f"{field} schema drifted")
    return value


def _artifact_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise StageCadExportError(
            "artifact_name must be one portable .3dm filename"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or any(part in ("", ".", "..") for part in path.parts)
        or path.suffix.lower() != ".3dm"
    ):
        raise StageCadExportError(
            "artifact_name must be one portable .3dm filename"
        )
    return value


def _artifact_uri(branch: BranchRef, artifact_name: str) -> str:
    return (
        f"project://{quote(branch.run.project_id, safe='')}/runs/"
        f"{quote(branch.run.run_id, safe='')}/branches/"
        f"{quote(branch.branch_id, safe='')}/artifacts/"
        f"{quote(artifact_name, safe='')}"
    )


def _program_binding_from_dict(value: object) -> RhinoCadProgramBinding:
    payload = _mapping(
        value,
        {
            "schema",
            "program_ref",
            "project_id",
            "run_id",
            "branch_id",
            "branch_epoch",
            "stage_id",
            "program_digest",
            "base",
            "design_state_digest",
            "predecessor_program_digest",
        },
        "stage CAD program binding",
    )
    if payload["schema"] != RhinoCadProgramBinding.SCHEMA:
        raise StageCadExportError("unsupported stage CAD program binding schema")
    record = _mapping(
        payload["program_ref"],
        {"project_id", "relative_path", "sha256", "media_type", "uri"},
        "stage CAD program record",
    )
    base = _mapping(
        payload["base"],
        {"project_id", "version", "state_sha256"},
        "stage CAD program base",
    )
    project_id = payload["project_id"]
    if base["project_id"] != project_id:
        raise StageCadExportError("stage CAD program base crossed projects")
    branch = BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=payload["run_id"],
            base=ProjectVersionRef(
                project_id=project_id,
                version=base["version"],
                state_sha256=base["state_sha256"],
            ),
        ),
        branch_id=payload["branch_id"],
        epoch=payload["branch_epoch"],
    )
    program_ref = ProjectRecordRef(
        project_id=record["project_id"],
        relative_path=record["relative_path"],
        sha256=record["sha256"],
        media_type=record["media_type"],
    )
    if program_ref.uri != record["uri"]:
        raise StageCadExportError("stage CAD program record URI changed")
    result = RhinoCadProgramBinding(
        program_ref=program_ref,
        branch=branch,
        stage_id=payload["stage_id"],
        program_digest=payload["program_digest"],
        design_state_digest=payload["design_state_digest"],
        predecessor_program_digest=payload["predecessor_program_digest"],
    )
    if result.to_dict() != payload:
        raise StageCadExportError("stage CAD program binding changed")
    return result


@dataclass(frozen=True, slots=True)
class StageCadExportGuardReceipt:
    """Exact, replayable classification of one proposed CAD export."""

    claim: StageArtifactClaim
    program_binding: RhinoCadProgramBinding
    compiled_program_digest: str
    artifact_name: str

    SCHEMA = "StageCadExportGuardReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.claim, StageArtifactClaim):
            raise TypeError("claim must be a StageArtifactClaim")
        if not isinstance(self.program_binding, RhinoCadProgramBinding):
            raise TypeError("program_binding must be a RhinoCadProgramBinding")
        object.__setattr__(
            self,
            "compiled_program_digest",
            require_sha256(
                self.compiled_program_digest,
                "compiled_program_digest",
            ),
        )
        object.__setattr__(self, "artifact_name", _artifact_name(self.artifact_name))
        binding = self.program_binding
        claim = self.claim
        if binding.program_digest != self.compiled_program_digest:
            raise StageCadExportError(
                "compiled program digest differs from the exact CAD binding"
            )
        if claim.branch != binding.branch:
            raise StageCadExportError("stage claim crossed the CAD branch or epoch")
        if claim.stage_id != binding.stage_id:
            raise StageCadExportError("stage claim and CAD stage disagree")

        expected_uri = _artifact_uri(claim.branch, self.artifact_name)
        if claim.status is StageArtifactStatus.EXPLORATORY_PRE_STAGE:
            if claim.geometry_program is not None or claim.artifact is not None:
                raise StageCadExportError(
                    "exploratory CAD claim acquired formal stage bindings"
                )
            candidate = claim.preview
        else:
            geometry = claim.geometry_program
            if geometry is None:
                raise StageCadExportError(
                    "formal stage claim is missing its geometry-program binding"
                )
            if geometry.record_ref != binding.program_ref:
                raise StageCadExportError(
                    "formal stage claim binds a different geometry-program record"
                )
            if geometry.content_digest != self.compiled_program_digest:
                raise StageCadExportError(
                    "formal stage claim binds a stale geometry-program digest"
                )
            candidate = claim.artifact
            if candidate is None:
                raise StageCadExportError(
                    "formal stage claim is missing its artifact binding"
                )
        if candidate is not None:
            if candidate.artifact_ref.uri != expected_uri:
                raise StageCadExportError(
                    "stage claim artifact does not match the requested CAD artifact"
                )
            if candidate.artifact_ref.media_type != "model/vnd.rhino":
                raise StageCadExportError(
                    "stage-bound CAD artifact must use model/vnd.rhino"
                )

    @property
    def status(self) -> StageArtifactStatus:
        return self.claim.status

    @property
    def formal_stage_bound(self) -> bool:
        return self.status is not StageArtifactStatus.EXPLORATORY_PRE_STAGE

    @property
    def expected_artifact_sha256(self) -> str | None:
        binding = (
            self.claim.artifact
            if self.formal_stage_bound
            else self.claim.preview
        )
        return None if binding is None else binding.artifact_sha256

    @property
    def receipt_digest(self) -> str:
        return digest_value(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "claim": self.claim.to_dict(),
            "program_binding": self.program_binding.to_dict(),
            "compiled_program_digest": self.compiled_program_digest,
            "artifact_name": self.artifact_name,
            "status": self.status.value,
            "formal_stage_bound": self.formal_stage_bound,
            "expected_artifact_sha256": self.expected_artifact_sha256,
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "receipt_digest": self.receipt_digest}

    @classmethod
    def from_dict(cls, value: object) -> "StageCadExportGuardReceipt":
        payload = _mapping(
            value,
            {
                "schema",
                "claim",
                "program_binding",
                "compiled_program_digest",
                "artifact_name",
                "status",
                "formal_stage_bound",
                "expected_artifact_sha256",
                "receipt_digest",
                *_AUTHORITY_FIELDS,
            },
            "stage CAD export guard receipt",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageCadExportError("unsupported stage CAD export guard schema")
        if any(payload[field] is not False for field in _AUTHORITY_FIELDS):
            raise StageCadExportError("stage CAD export guard acquired authority")
        result = cls(
            claim=StageArtifactClaim.from_dict(payload["claim"]),
            program_binding=_program_binding_from_dict(
                payload["program_binding"]
            ),
            compiled_program_digest=payload["compiled_program_digest"],
            artifact_name=payload["artifact_name"],
        )
        if result.to_dict() != payload:
            raise StageCadExportError("stage CAD export guard digest changed")
        return result


@dataclass(frozen=True, slots=True)
class StageBoundRhinoCadExportPlan:
    """A raw speculative export plan plus its non-authoritative stage guard."""

    guard: StageCadExportGuardReceipt
    export_plan: RhinoCadExportPlan

    SCHEMA = "StageBoundRhinoCadExportPlan@1"

    def __post_init__(self) -> None:
        if not isinstance(self.guard, StageCadExportGuardReceipt):
            raise TypeError("guard must be a StageCadExportGuardReceipt")
        if not isinstance(self.export_plan, RhinoCadExportPlan):
            raise TypeError("export_plan must be a RhinoCadExportPlan")
        if self.export_plan.identity.binding != self.guard.program_binding:
            raise StageCadExportError(
                "raw CAD plan and stage guard bind different programs"
            )
        if self.export_plan.model_path.name != self.guard.artifact_name:
            raise StageCadExportError(
                "raw CAD plan and stage guard bind different artifacts"
            )
        provenance = dict(self.export_plan.expected_document_user_text)
        expected = {
            "archflow:stage_claim_digest": self.guard.claim.claim_digest,
            "archflow:stage_claim_status": self.guard.status.value,
            "archflow:stage_formal_binding": str(
                self.guard.formal_stage_bound
            ).lower(),
            "archflow:stage_guard_receipt_digest": (
                self.guard.receipt_digest
            ),
        }
        if any(provenance.get(key) != value for key, value in expected.items()):
            raise StageCadExportError(
                "raw CAD plan does not retain the exact stage guard provenance"
            )

    @property
    def status(self) -> StageArtifactStatus:
        return self.guard.status

    @property
    def formal_stage_bound(self) -> bool:
        return self.guard.formal_stage_bound

    @property
    def plan_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "guard": self.guard.to_dict(),
            "export_identity": self.export_plan.identity.to_dict(),
            "artifact_name": self.export_plan.model_path.name,
            "raw_plan_digest": self.export_plan.plan_digest,
            **_AUTHORITY_FIELDS,
        }


def _require_raw_stage_execution_binding(
    guard: StageCadExportGuardReceipt,
    stage_plan_digest: str,
    raw: RhinoCadExecutionReceipt,
) -> None:
    if raw.identity.binding != guard.program_binding:
        raise StageCadExportError(
            "raw execution receipt crossed the stage program binding"
        )
    if raw.artifact_relative_path != guard.artifact_name:
        raise StageCadExportError(
            "raw execution receipt crossed the stage artifact binding"
        )
    expected_stage_plan_digest = digest_value(
        {
            "schema": StageBoundRhinoCadExportPlan.SCHEMA,
            "guard": guard.to_dict(),
            "export_identity": raw.identity.to_dict(),
            "artifact_name": raw.artifact_relative_path,
            "raw_plan_digest": raw.plan_digest,
            **_AUTHORITY_FIELDS,
        }
    )
    if stage_plan_digest != expected_stage_plan_digest:
        raise StageCadExportError(
            "raw execution receipt does not bind the exact stage plan"
        )
    expected_artifact_sha256 = guard.expected_artifact_sha256
    if raw.status is CadExecutionStatus.SUCCEEDED:
        inspection = raw.inspection
        actual_sha256 = (
            None if inspection is None else inspection.get("file_sha256")
        )
        if (
            expected_artifact_sha256 is not None
            and actual_sha256 != expected_artifact_sha256
        ):
            raise StageCadExportError(
                "readback artifact SHA differs from the stage claim"
            )


@dataclass(frozen=True, slots=True)
class StageBoundRhinoCadExecutionReceipt:
    """Raw execution evidence bound to one exact stage guard and plan."""

    guard: StageCadExportGuardReceipt
    stage_plan_digest: str
    raw_execution_receipt: RhinoCadExecutionReceipt
    raw_execution_receipt_digest: str = field(init=False)

    SCHEMA = "StageBoundRhinoCadExecutionReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.guard, StageCadExportGuardReceipt):
            raise TypeError("guard must be a StageCadExportGuardReceipt")
        if not isinstance(
            self.raw_execution_receipt,
            RhinoCadExecutionReceipt,
        ):
            raise TypeError(
                "raw_execution_receipt must be a RhinoCadExecutionReceipt"
            )
        object.__setattr__(
            self,
            "stage_plan_digest",
            require_sha256(self.stage_plan_digest, "stage_plan_digest"),
        )
        raw = self.raw_execution_receipt
        replayed_guard = StageCadExportGuardReceipt.from_dict(
            self.guard.to_dict()
        )
        if replayed_guard != self.guard:
            raise StageCadExportError(
                "stage CAD guard changed before execution binding"
            )
        _require_raw_stage_execution_binding(
            replayed_guard,
            self.stage_plan_digest,
            raw,
        )
        object.__setattr__(
            self,
            "raw_execution_receipt_digest",
            digest_value(raw.to_dict()),
        )

    @property
    def status(self) -> CadExecutionStatus:
        return self.raw_execution_receipt.status

    @property
    def claim_digest(self) -> str:
        return self.guard.claim.claim_digest

    @property
    def formal_stage_bound(self) -> bool:
        return self.guard.formal_stage_bound

    @property
    def readback_verified(self) -> bool:
        return self.raw_execution_receipt.readback_verified

    @property
    def receipt_digest(self) -> str:
        return digest_value(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        replayed_guard = StageCadExportGuardReceipt.from_dict(
            self.guard.to_dict()
        )
        if replayed_guard != self.guard:
            raise StageCadExportError(
                "stage CAD guard changed after execution binding"
            )
        _require_raw_stage_execution_binding(
            replayed_guard,
            self.stage_plan_digest,
            self.raw_execution_receipt,
        )
        current_raw_digest = digest_value(self.raw_execution_receipt.to_dict())
        if current_raw_digest != self.raw_execution_receipt_digest:
            raise StageCadExportError(
                "raw execution receipt changed after stage binding"
            )
        return {
            "schema": self.SCHEMA,
            "guard": self.guard.to_dict(),
            "claim_digest": self.claim_digest,
            "stage_plan_digest": self.stage_plan_digest,
            "status": self.status.value,
            "formal_stage_bound": self.formal_stage_bound,
            "readback_verified": self.readback_verified,
            "raw_execution_receipt": self.raw_execution_receipt.to_dict(),
            "raw_execution_receipt_digest": (
                self.raw_execution_receipt_digest
            ),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "receipt_digest": self.receipt_digest}


def require_stage_cad_binding(
    claim: StageArtifactClaim,
    program: CompiledGeometryProgram,
    *,
    binding: RhinoCadProgramBinding,
    artifact_name: str,
) -> StageCadExportGuardReceipt:
    """Validate the exact stage/program/artifact relation without any writes."""

    if not isinstance(claim, StageArtifactClaim):
        raise TypeError("claim must be a StageArtifactClaim")
    if not isinstance(program, CompiledGeometryProgram):
        raise TypeError("program must be a CompiledGeometryProgram")
    if not isinstance(binding, RhinoCadProgramBinding):
        raise TypeError("binding must be a RhinoCadProgramBinding")
    binding.bind_program(program)
    return StageCadExportGuardReceipt(
        claim=claim,
        program_binding=binding,
        compiled_program_digest=program.program_digest,
        artifact_name=artifact_name,
    )


def prepare_stage_bound_rhino_three_dm_export(
    program: CompiledGeometryProgram,
    *,
    claim: StageArtifactClaim,
    binding: RhinoCadProgramBinding,
    speculative_workspace: Path,
    artifact_name: str,
    readback_tolerance: float,
    provenance: Mapping[str, str] | None = None,
    material_by_component: Mapping[str, str] | None = None,
    material_colors: Mapping[str, tuple[int, int, int]] | None = None,
) -> StageBoundRhinoCadExportPlan:
    """Prepare a speculative Rhino plan after exact stage classification."""

    guard = require_stage_cad_binding(
        claim,
        program,
        binding=binding,
        artifact_name=artifact_name,
    )
    supplied = {} if provenance is None else dict(provenance)
    stage_values = {
        "stage_claim_digest": claim.claim_digest,
        "stage_claim_status": claim.status.value,
        "stage_formal_binding": str(guard.formal_stage_bound).lower(),
        "stage_guard_receipt_digest": guard.receipt_digest,
    }
    overlap = set(supplied).intersection(stage_values)
    if overlap:
        raise StageCadExportError(
            "provenance cannot override stage guard keys: "
            + ", ".join(sorted(overlap))
        )
    supplied.update(stage_values)
    export_plan = prepare_rhino_three_dm_export(
        program,
        binding=binding,
        speculative_workspace=speculative_workspace,
        artifact_name=artifact_name,
        readback_tolerance=readback_tolerance,
        provenance=supplied,
        material_by_component=material_by_component,
        material_colors=material_colors,
    )
    return StageBoundRhinoCadExportPlan(guard=guard, export_plan=export_plan)


def execute_stage_bound_rhino_three_dm_export(
    plan: StageBoundRhinoCadExportPlan,
    *,
    powershell_executable: Path,
    timeout_seconds: float = 300.0,
    runner: Callable[..., object] | None = None,
    cleanup_runner: Callable[..., object] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> StageBoundRhinoCadExecutionReceipt:
    """Execute only a complete stage-bound plan and retain the exact guard."""

    if not isinstance(plan, StageBoundRhinoCadExportPlan):
        raise TypeError("plan must be a StageBoundRhinoCadExportPlan")
    replayed_guard = StageCadExportGuardReceipt.from_dict(plan.guard.to_dict())
    if replayed_guard != plan.guard:
        raise StageCadExportError("stage CAD guard changed before execution")
    replayed_plan = StageBoundRhinoCadExportPlan(
        guard=replayed_guard,
        export_plan=plan.export_plan,
    )
    if replayed_plan.to_dict() != plan.to_dict():
        raise StageCadExportError("stage-bound CAD plan changed before execution")
    raw_receipt = execute_rhino_three_dm_export(
        replayed_plan.export_plan,
        powershell_executable=powershell_executable,
        timeout_seconds=timeout_seconds,
        runner=runner,
        cleanup_runner=cleanup_runner,
        monotonic=monotonic,
        sleeper=sleeper,
    )
    return StageBoundRhinoCadExecutionReceipt(
        guard=replayed_guard,
        stage_plan_digest=replayed_plan.plan_digest,
        raw_execution_receipt=raw_receipt,
    )


__all__ = [
    "StageBoundRhinoCadExecutionReceipt",
    "StageBoundRhinoCadExportPlan",
    "StageCadExportError",
    "StageCadExportGuardReceipt",
    "execute_stage_bound_rhino_three_dm_export",
    "prepare_stage_bound_rhino_three_dm_export",
    "require_stage_cad_binding",
]
