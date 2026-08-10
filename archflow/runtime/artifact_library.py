"""Platform-neutral building packages and explicit downstream export receipts.

The neutral package is a delivery envelope over existing authoritative records,
not a second design-state store.  Durable writes require the P036 project port
and the designated project ``exports/`` area.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.adapters.sandbox_render import (
    SandboxRenderSet,
    SandboxViewKind,
)
from archflow.project.ports import (
    PersistenceArea,
    PersistenceDestination,
    RecordSink,
    require_destination,
)
from archflow.project.refs import (
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.realization import (
    HybridScene,
    RealizationStatus,
    SandboxRealizationReceipt,
)
from archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidatePolicyKind,
)
from archflow.runtime.geometry_compiler import CompiledGeometryProgram
from archflow.runtime.staged_build import (
    MaterialAccountReceipt,
    StagedBuildCheckpoint,
    StagedBuildPlan,
    validate_build_checkpoint,
)
from archflow.state import ArtifactRef
from archflow.state.build_policy import BuildPolicy


class ArtifactLibraryError(ValueError):
    """A saved/imported/exported artifact lost exact provenance or authority."""


class PackageRecordRole(StrEnum):
    AUTHORITY = "authority"
    APPROVAL = "approval"
    VALIDATION = "validation"
    EVALUATION = "evaluation"


class PlatformTarget(StrEnum):
    SCHEM = "schem"
    MINECRAFT = "minecraft"
    RHINO = "rhino"
    REVIT = "revit"
    AGENTIC_CAD = "agentic_cad"


class ExportEquivalence(StrEnum):
    EXACT = "exact"
    LOSSY = "lossy"
    FAILED = "failed"


_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactLibraryError("record must be finite canonical JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    lowered = value.lower()
    if len(lowered) != 64 or any(c not in "0123456789abcdef" for c in lowered):
        raise ArtifactLibraryError(f"{field} must be a SHA-256 digest")
    return lowered


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactLibraryError(f"{field} must be non-empty text")
    return value


def _refs(values: object, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ArtifactLibraryError(f"{field} must be a tuple")
    for item in values:
        if (
            not isinstance(item, str)
            or _SCHEME.match(item) is None
            or item.lower().startswith("file:")
        ):
            raise ArtifactLibraryError(f"{field} contains an unstable reference")
    if values != tuple(sorted(set(values))):
        raise ArtifactLibraryError(f"{field} requires deterministic unique refs")
    return values


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _exact(value: Mapping[str, object], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ArtifactLibraryError(f"{label} schema drifted")


def _base_dict(value: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": value.project_id,
        "version": value.version,
        "state_sha256": value.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


def _artifact_dict(value: ArtifactRef) -> dict[str, object]:
    return {
        "artifact_id": value.artifact_id,
        "uri": value.uri,
        "media_type": value.media_type,
        "sha256": value.sha256,
    }


def _artifact_from_dict(value: object) -> ArtifactRef:
    payload = _mapping(value, "artifact ref")
    _exact(payload, {"artifact_id", "uri", "media_type", "sha256"}, "artifact ref")
    if not isinstance(payload["uri"], str) or _SCHEME.match(payload["uri"]) is None:
        raise ArtifactLibraryError("artifact URI must be scheme-qualified")
    if payload["uri"].lower().startswith("file:"):
        raise ArtifactLibraryError("artifact URI cannot persist a machine path")
    return ArtifactRef(
        artifact_id=payload["artifact_id"],
        uri=payload["uri"],
        media_type=payload["media_type"],
        sha256=payload["sha256"],
    )


@dataclass(frozen=True, slots=True)
class CanonicalProgramRecord:
    """Canonical compiled-program JSON retained even without a replay runtime."""

    program_json: str
    program_digest: str

    SCHEMA = "CanonicalProgramRecord@1"

    def __post_init__(self) -> None:
        if not isinstance(self.program_json, str):
            raise TypeError("program_json must be text")
        try:
            payload = json.loads(self.program_json)
        except json.JSONDecodeError as exc:
            raise ArtifactLibraryError("program_json is invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema") != CompiledGeometryProgram.SCHEMA:
            raise ArtifactLibraryError("program_json is not a compiled geometry program")
        if _canonical(payload) != self.program_json:
            raise ArtifactLibraryError("program_json must be canonical JSON")
        object.__setattr__(self, "program_digest", _sha(self.program_digest, "program_digest"))
        if _digest(payload) != self.program_digest:
            raise ArtifactLibraryError("program digest does not match canonical program JSON")

    @classmethod
    def from_program(cls, value: CompiledGeometryProgram) -> CanonicalProgramRecord:
        if not isinstance(value, CompiledGeometryProgram):
            raise TypeError("value must be CompiledGeometryProgram")
        return cls(
            program_json=_canonical(value.to_dict()),
            program_digest=value.program_digest,
        )

    @property
    def payload(self) -> dict[str, Any]:
        result = json.loads(self.program_json)
        if not isinstance(result, dict):
            raise AssertionError("validated program stopped being an object")
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "program_json": self.program_json,
            "program_digest": self.program_digest,
            "execution_replay": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CanonicalProgramRecord:
        payload = _mapping(value, "canonical program record")
        _exact(
            payload,
            {
                "schema",
                "program_json",
                "program_digest",
                "execution_replay",
                "canonical_write_authority",
            },
            "canonical program record",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["execution_replay"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArtifactLibraryError("canonical program authority drifted")
        return cls(
            program_json=payload["program_json"],
            program_digest=payload["program_digest"],
        )


@dataclass(frozen=True, slots=True)
class PackageEvidenceRecord:
    record_id: str
    role: PackageRecordRole
    authority_id: str
    payload_json: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "PackageEvidenceRecord@1"

    def __post_init__(self) -> None:
        require_identifier(self.record_id, "record_id")
        if not isinstance(self.role, PackageRecordRole):
            raise TypeError("role must be PackageRecordRole")
        _text(self.authority_id, "authority_id")
        if not isinstance(self.payload_json, str):
            raise TypeError("payload_json must be text")
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise ArtifactLibraryError("evidence payload is invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("schema"), str):
            raise ArtifactLibraryError("evidence payload requires a schema")
        if _canonical(payload) != self.payload_json:
            raise ArtifactLibraryError("evidence payload must be canonical JSON")
        _refs(self.evidence_refs, "evidence_refs")

    @classmethod
    def create(
        cls,
        *,
        record_id: str,
        role: PackageRecordRole,
        authority_id: str,
        payload: Mapping[str, Any],
        evidence_refs: tuple[str, ...],
    ) -> PackageEvidenceRecord:
        return cls(
            record_id=record_id,
            role=role,
            authority_id=authority_id,
            payload_json=_canonical(dict(payload)),
            evidence_refs=evidence_refs,
        )

    @property
    def record_digest(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "record_id": self.record_id,
            "role": self.role.value,
            "authority_id": self.authority_id,
            "payload_json": self.payload_json,
            "record_digest": self.record_digest,
            "evidence_refs": list(self.evidence_refs),
            "generation_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> PackageEvidenceRecord:
        payload = _mapping(value, "package evidence record")
        _exact(
            payload,
            {
                "schema",
                "record_id",
                "role",
                "authority_id",
                "payload_json",
                "record_digest",
                "evidence_refs",
                "generation_authority",
                "canonical_write_authority",
            },
            "package evidence record",
        )
        result = cls(
            record_id=payload["record_id"],
            role=PackageRecordRole(payload["role"]),
            authority_id=payload["authority_id"],
            payload_json=payload["payload_json"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["record_digest"] != result.record_digest
            or payload["generation_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArtifactLibraryError("package evidence authority or digest drifted")
        return result


def _readme(
    *,
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    candidate_digest: str,
    program_digest: str,
    scene_digest: str,
    render_set: SandboxRenderSet,
    evidence_records: tuple[PackageEvidenceRecord, ...],
) -> str:
    views = {item.kind: item for item in render_set.views}
    return "\n".join(
        (
            "# ArchFlow neutral building package",
            "",
            f"Project: {project_id}",
            f"Run: {run_id}",
            f"Base version: {base.version}",
            f"Base state SHA-256: {base.require_digest()}",
            f"Candidate assembly SHA-256: {candidate_digest}",
            f"Geometry program SHA-256: {program_digest}",
            f"Hybrid scene SHA-256: {scene_digest}",
            "",
            "## Provenance-coded views",
            "",
            f"- ISO: {views[SandboxViewKind.ISO].svg_sha256}",
            f"- Transverse section A: {views[SandboxViewKind.TRANSVERSE_SECTION].svg_sha256}",
            f"- Longitudinal section B: {views[SandboxViewKind.LONGITUDINAL_SECTION].svg_sha256}",
            f"- Elevation: {views[SandboxViewKind.ELEVATION].svg_sha256}",
            f"- Plan: {views[SandboxViewKind.PLAN].svg_sha256}",
            "",
            "## Authority",
            "",
            *(
                f"- {item.role.value}/{item.record_id}: {item.authority_id} "
                f"({item.record_digest})"
                for item in evidence_records
            ),
            "",
            "This package is reference-only: it grants no generation, external execution,",
            "or canonical project-write authority. Reload verifies saved records; it does",
            "not replay the model, MCP calls, or external platform execution.",
            "",
        )
    )


@dataclass(frozen=True, slots=True)
class NeutralBuildingPackage:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    candidate: CandidateAssembly
    build_policy: BuildPolicy
    geometry_program: CanonicalProgramRecord
    scene: HybridScene
    realization_receipt: SandboxRealizationReceipt
    render_set: SandboxRenderSet
    material_account: MaterialAccountReceipt
    staged_plan: StagedBuildPlan
    checkpoint: StagedBuildCheckpoint
    evidence_records: tuple[PackageEvidenceRecord, ...]
    readme: str

    SCHEMA = "NeutralBuildingPackage@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef) or self.base.project_id != self.project_id:
            raise ArtifactLibraryError("neutral package and base disagree")
        typed = (
            (self.candidate, CandidateAssembly, "candidate"),
            (self.build_policy, BuildPolicy, "build_policy"),
            (self.geometry_program, CanonicalProgramRecord, "geometry_program"),
            (self.scene, HybridScene, "scene"),
            (self.realization_receipt, SandboxRealizationReceipt, "realization_receipt"),
            (self.render_set, SandboxRenderSet, "render_set"),
            (self.material_account, MaterialAccountReceipt, "material_account"),
            (self.staged_plan, StagedBuildPlan, "staged_plan"),
            (self.checkpoint, StagedBuildCheckpoint, "checkpoint"),
        )
        for value, expected, field in typed:
            if not isinstance(value, expected):
                raise TypeError(f"{field} must be {expected.__name__}")
        state = self.candidate.design_state
        if (
            (state.project_id, state.run_id, state.base)
            != (self.project_id, self.run_id, self.base)
            or (self.build_policy.project_id, self.build_policy.run_id, self.build_policy.base)
            != (self.project_id, self.run_id, self.base)
            or (self.scene.project_id, self.scene.run_id, self.scene.base)
            != (self.project_id, self.run_id, self.base)
        ):
            raise ArtifactLibraryError("package records belong to different project versions")
        build_bindings = [
            item for item in self.candidate.policies if item.kind is CandidatePolicyKind.BUILD
        ]
        if len(build_bindings) != 1 or build_bindings[0].policy_digest != self.build_policy.policy_digest:
            raise ArtifactLibraryError("package candidate and build policy disagree")
        program_payload = self.geometry_program.payload
        proposal = program_payload.get("proposal")
        if not isinstance(proposal, dict) or proposal.get("design_state_digest") != state.state_digest:
            raise ArtifactLibraryError("saved geometry program lost its design-state binding")
        if (
            self.scene.geometry_program_digest != self.geometry_program.program_digest
            or self.realization_receipt.geometry_program_digest != self.geometry_program.program_digest
            or self.realization_receipt.status is not RealizationStatus.REALIZED
            or self.realization_receipt.scene_digest != self.scene.scene_digest
            or self.render_set.scene_digest != self.scene.scene_digest
        ):
            raise ArtifactLibraryError("materialized geometry records disagree")
        if (
            self.material_account.build_policy_digest != self.build_policy.policy_digest
            or self.staged_plan.candidate_assembly_digest != self.candidate.assembly_digest
            or self.staged_plan.design_state_digest != state.state_digest
            or self.staged_plan.executable_plan_digest != self.candidate.plan.plan_digest
            or self.staged_plan.build_policy_digest != self.build_policy.policy_digest
            or self.staged_plan.geometry_program_digest != self.geometry_program.program_digest
            or self.staged_plan.scene_digest != self.scene.scene_digest
            or self.staged_plan.material_account_digest != self.material_account.account_digest
        ):
            raise ArtifactLibraryError("saved plan lost an upstream digest binding")
        validate_build_checkpoint(self.staged_plan, self.checkpoint)
        required_views = {
            SandboxViewKind.ISO,
            SandboxViewKind.TRANSVERSE_SECTION,
            SandboxViewKind.LONGITUDINAL_SECTION,
            SandboxViewKind.ELEVATION,
        }
        if not required_views <= {item.kind for item in self.render_set.views}:
            raise ArtifactLibraryError("neutral package is missing a required paper view")
        if not isinstance(self.evidence_records, tuple) or not self.evidence_records or any(
            not isinstance(item, PackageEvidenceRecord) for item in self.evidence_records
        ):
            raise TypeError("evidence_records contains an invalid item")
        keys = tuple((item.role.value, item.record_id) for item in self.evidence_records)
        if keys != tuple(sorted(set(keys))):
            raise ArtifactLibraryError("evidence records require deterministic identities")
        if PackageRecordRole.AUTHORITY not in {item.role for item in self.evidence_records}:
            raise ArtifactLibraryError("neutral package requires explicit authority metadata")
        expected_readme = _readme(
            project_id=self.project_id,
            run_id=self.run_id,
            base=self.base,
            candidate_digest=self.candidate.assembly_digest,
            program_digest=self.geometry_program.program_digest,
            scene_digest=self.scene.scene_digest,
            render_set=self.render_set,
            evidence_records=self.evidence_records,
        )
        if self.readme != expected_readme:
            raise ArtifactLibraryError("README is not derived from exact package records")

    @property
    def package_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "candidate": self.candidate.to_dict(),
            "build_policy": self.build_policy.to_dict(),
            "geometry_program": self.geometry_program.to_dict(),
            "scene": self.scene.to_dict(),
            "realization_receipt": self.realization_receipt.to_dict(),
            "render_set": self.render_set.to_dict(),
            "material_account": self.material_account.to_dict(),
            "staged_plan": self.staged_plan.to_dict(),
            "checkpoint": self.checkpoint.to_dict(),
            "evidence_records": [item.to_dict() for item in self.evidence_records],
            "readme": self.readme,
            "reference_only": True,
            "generation_authority": False,
            "execution_replay": False,
            "external_execution_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> NeutralBuildingPackage:
        payload = _mapping(value, "neutral building package")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "candidate",
                "build_policy",
                "geometry_program",
                "scene",
                "realization_receipt",
                "render_set",
                "material_account",
                "staged_plan",
                "checkpoint",
                "evidence_records",
                "readme",
                "reference_only",
                "generation_authority",
                "execution_replay",
                "external_execution_authority",
                "canonical_write_authority",
            },
            "neutral building package",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["reference_only"] is not True
            or payload["generation_authority"] is not False
            or payload["execution_replay"] is not False
            or payload["external_execution_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArtifactLibraryError("neutral package authority drifted")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            candidate=CandidateAssembly.from_dict(payload["candidate"]),
            build_policy=BuildPolicy.from_dict(payload["build_policy"]),
            geometry_program=CanonicalProgramRecord.from_dict(payload["geometry_program"]),
            scene=HybridScene.from_dict(payload["scene"]),
            realization_receipt=SandboxRealizationReceipt.from_dict(payload["realization_receipt"]),
            render_set=SandboxRenderSet.from_dict(payload["render_set"]),
            material_account=MaterialAccountReceipt.from_dict(payload["material_account"]),
            staged_plan=StagedBuildPlan.from_dict(payload["staged_plan"]),
            checkpoint=StagedBuildCheckpoint.from_dict(payload["checkpoint"]),
            evidence_records=tuple(
                PackageEvidenceRecord.from_dict(item) for item in payload["evidence_records"]
            ),
            readme=payload["readme"],
        )


def create_neutral_building_package(
    *,
    candidate: CandidateAssembly,
    build_policy: BuildPolicy,
    geometry_program: CompiledGeometryProgram,
    scene: HybridScene,
    realization_receipt: SandboxRealizationReceipt,
    render_set: SandboxRenderSet,
    material_account: MaterialAccountReceipt,
    staged_plan: StagedBuildPlan,
    checkpoint: StagedBuildCheckpoint,
    evidence_records: tuple[PackageEvidenceRecord, ...],
) -> NeutralBuildingPackage:
    program_record = CanonicalProgramRecord.from_program(geometry_program)
    ordered_records = tuple(sorted(evidence_records, key=lambda item: (item.role.value, item.record_id)))
    state = candidate.design_state
    readme = _readme(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        candidate_digest=candidate.assembly_digest,
        program_digest=program_record.program_digest,
        scene_digest=scene.scene_digest,
        render_set=render_set,
        evidence_records=ordered_records,
    )
    return NeutralBuildingPackage(
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        candidate=candidate,
        build_policy=build_policy,
        geometry_program=program_record,
        scene=scene,
        realization_receipt=realization_receipt,
        render_set=render_set,
        material_account=material_account,
        staged_plan=staged_plan,
        checkpoint=checkpoint,
        evidence_records=ordered_records,
        readme=readme,
    )


@dataclass(frozen=True, slots=True)
class PlatformExportReceipt:
    source_package_digest: str
    source_geometry_digest: str
    target: PlatformTarget
    adapter_id: str
    adapter_version: str
    equivalence: ExportEquivalence
    artifact: ArtifactRef | None
    loss_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "PlatformExportReceipt@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_package_digest", _sha(self.source_package_digest, "source_package_digest")
        )
        object.__setattr__(
            self, "source_geometry_digest", _sha(self.source_geometry_digest, "source_geometry_digest")
        )
        if not isinstance(self.target, PlatformTarget):
            raise TypeError("target must be PlatformTarget")
        require_identifier(self.adapter_id, "adapter_id")
        _text(self.adapter_version, "adapter_version")
        if not isinstance(self.equivalence, ExportEquivalence):
            raise TypeError("equivalence must be ExportEquivalence")
        if not isinstance(self.loss_codes, tuple):
            raise TypeError("loss_codes must be a tuple")
        for item in self.loss_codes:
            require_identifier(item, "loss_codes")
        if self.loss_codes != tuple(sorted(set(self.loss_codes))):
            raise ArtifactLibraryError("loss_codes require deterministic unique ids")
        _refs(self.evidence_refs, "evidence_refs")
        if self.equivalence is ExportEquivalence.EXACT:
            if not isinstance(self.artifact, ArtifactRef) or self.loss_codes:
                raise ArtifactLibraryError("exact export requires an artifact and no losses")
        elif self.equivalence is ExportEquivalence.LOSSY:
            if not isinstance(self.artifact, ArtifactRef) or not self.loss_codes:
                raise ArtifactLibraryError("lossy export requires artifact and explicit losses")
        elif self.artifact is not None or not self.loss_codes:
            raise ArtifactLibraryError("failed export requires failure codes and no artifact")
        if self.artifact is not None:
            _artifact_from_dict(_artifact_dict(self.artifact))

    @property
    def receipt_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_package_digest": self.source_package_digest,
            "source_geometry_digest": self.source_geometry_digest,
            "target": self.target.value,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "equivalence": self.equivalence.value,
            "artifact": None if self.artifact is None else _artifact_dict(self.artifact),
            "loss_codes": list(self.loss_codes),
            "evidence_refs": list(self.evidence_refs),
            "source_geometry_mutated": False,
            "generation_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlatformExportReceipt:
        payload = _mapping(value, "platform export receipt")
        _exact(
            payload,
            {
                "schema",
                "source_package_digest",
                "source_geometry_digest",
                "target",
                "adapter_id",
                "adapter_version",
                "equivalence",
                "artifact",
                "loss_codes",
                "evidence_refs",
                "source_geometry_mutated",
                "generation_authority",
                "canonical_write_authority",
            },
            "platform export receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["source_geometry_mutated"] is not False
            or payload["generation_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArtifactLibraryError("platform export authority drifted")
        return cls(
            source_package_digest=payload["source_package_digest"],
            source_geometry_digest=payload["source_geometry_digest"],
            target=PlatformTarget(payload["target"]),
            adapter_id=payload["adapter_id"],
            adapter_version=payload["adapter_version"],
            equivalence=ExportEquivalence(payload["equivalence"]),
            artifact=(
                None if payload["artifact"] is None else _artifact_from_dict(payload["artifact"])
            ),
            loss_codes=tuple(payload["loss_codes"]),
            evidence_refs=tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ImportedPackageReference:
    source_artifact: ArtifactRef
    package_digest: str
    project_id: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "ImportedPackageReference@1"

    def __post_init__(self) -> None:
        if not isinstance(self.source_artifact, ArtifactRef):
            raise TypeError("source_artifact must be ArtifactRef")
        _artifact_from_dict(_artifact_dict(self.source_artifact))
        object.__setattr__(self, "package_digest", _sha(self.package_digest, "package_digest"))
        require_identifier(self.project_id, "project_id")
        _refs(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "source_artifact": _artifact_dict(self.source_artifact),
            "package_digest": self.package_digest,
            "project_id": self.project_id,
            "evidence_refs": list(self.evidence_refs),
            "reference_only": True,
            "candidate_only": True,
            "provider_registered": False,
            "generation_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ImportedPackageReference:
        payload = _mapping(value, "imported package reference")
        _exact(
            payload,
            {
                "schema",
                "source_artifact",
                "package_digest",
                "project_id",
                "evidence_refs",
                "reference_only",
                "candidate_only",
                "provider_registered",
                "generation_authority",
                "canonical_write_authority",
            },
            "imported package reference",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["reference_only"] is not True
            or payload["candidate_only"] is not True
            or payload["provider_registered"] is not False
            or payload["generation_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ArtifactLibraryError("imported package acquired production authority")
        return cls(
            source_artifact=_artifact_from_dict(payload["source_artifact"]),
            package_digest=payload["package_digest"],
            project_id=payload["project_id"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )


class PackageRecordLoader(Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


def _export_destination(destination: PersistenceDestination | None, producer: str) -> PersistenceDestination:
    resolved = require_destination(destination, producer=producer)
    if resolved.area is not PersistenceArea.EXPORT:
        raise ArtifactLibraryError(f"{producer} may persist only to the project export area")
    return resolved


def persist_neutral_building_package(
    sink: RecordSink,
    *,
    run: RunRef,
    package: NeutralBuildingPackage,
    destination: PersistenceDestination | None,
) -> ProjectRecordRef:
    resolved = _export_destination(destination, "neutral building package")
    if (
        run.project_id != package.project_id
        or run.run_id != package.run_id
        or run.base != package.base
    ):
        raise ArtifactLibraryError("run and neutral package disagree")
    return sink.put_json(
        run=run,
        destination=resolved,
        record_kind="neutral-building-package",
        payload=package.to_dict(),
    )


def load_neutral_building_package(
    loader: PackageRecordLoader,
    ref: ProjectRecordRef,
) -> NeutralBuildingPackage:
    package = NeutralBuildingPackage.from_dict(loader.load_json(ref))
    if package.project_id != ref.project_id:
        raise ArtifactLibraryError("record ref and neutral package disagree")
    return package


def persist_platform_export_receipt(
    sink: RecordSink,
    *,
    run: RunRef,
    receipt: PlatformExportReceipt,
    destination: PersistenceDestination | None,
) -> ProjectRecordRef:
    resolved = _export_destination(destination, "platform export receipt")
    return sink.put_json(
        run=run,
        destination=resolved,
        record_kind=f"{receipt.target.value}-export-receipt",
        payload=receipt.to_dict(),
    )
