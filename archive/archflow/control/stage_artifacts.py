"""Authority-free claims about artifacts produced around a design stage.

The records in this module are pure values.  They neither load nor persist
project data and they do not accept a caller-authored success flag as evidence
of stage entry or verification.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping
from urllib.parse import quote

from archflow.project.refs import BranchRef, ProjectRecordRef, require_identifier
from archive.archflow.state.design_maturity import StageEntryProof, require_stage_entry_proof
from archflow.state.geometry_program import require_sha256
from archflow.state.model import ArtifactRef
from archflow.state.operational_state import require_logical_ref


class StageArtifactClaimError(ValueError):
    """A stage-artifact claim is incomplete, stale, or cross-lineage."""


class StageArtifactStatus(StrEnum):
    """Evidence-derived status; none of these values is acceptance."""

    EXPLORATORY_PRE_STAGE = "exploratory_pre_stage"
    STAGE_ENTERED_CANDIDATE = "stage_entered_candidate"
    STAGE3_VERIFIED_CANDIDATE = "stage3_verified_candidate"


_AUTHORITY_FIELDS = {
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
    "materialization_authority": False,
    "readback_authority": False,
}


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mapping(
    value: object,
    keys: set[str],
    field: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    if set(value) != keys:
        raise StageArtifactClaimError(f"{field} schema drifted")
    return value


def _branch_to_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "project_id": branch.run.base.project_id,
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.state_sha256,
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _branch_from_dict(value: object) -> BranchRef:
    # Reuse the proof's strict parser without importing a private helper.
    from archflow.project.refs import ProjectVersionRef, RunRef

    payload = _mapping(
        value,
        {"project_id", "run_id", "base", "branch_id", "epoch"},
        "branch",
    )
    base = _mapping(
        payload["base"],
        {"project_id", "version", "state_sha256"},
        "branch base",
    )
    if base["project_id"] != payload["project_id"]:
        raise StageArtifactClaimError("branch base crossed projects")
    project_id = payload["project_id"]
    return BranchRef(
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
        epoch=payload["epoch"],
    )


def _record_to_dict(ref: ProjectRecordRef) -> dict[str, str]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(value: object, field: str) -> ProjectRecordRef:
    payload = _mapping(
        value,
        {"project_id", "relative_path", "sha256", "media_type"},
        field,
    )
    return ProjectRecordRef(
        project_id=payload["project_id"],
        relative_path=payload["relative_path"],
        sha256=payload["sha256"],
        media_type=payload["media_type"],
    )


def _artifact_to_dict(ref: ArtifactRef) -> dict[str, str]:
    return {
        "artifact_id": ref.artifact_id,
        "uri": ref.uri,
        "media_type": ref.media_type,
        "sha256": ref.sha256.lower(),
    }


def _artifact_from_dict(value: object, field: str) -> ArtifactRef:
    payload = _mapping(
        value,
        {"artifact_id", "uri", "media_type", "sha256"},
        field,
    )
    return ArtifactRef(
        artifact_id=payload["artifact_id"],
        uri=payload["uri"],
        media_type=payload["media_type"],
        sha256=payload["sha256"],
    )


def _exact_record_scope(ref: ProjectRecordRef, branch: BranchRef, field: str) -> None:
    if ref.project_id != branch.run.project_id:
        raise StageArtifactClaimError(f"{field} crossed projects")
    prefix = (
        f"runs/{branch.run.run_id}/branches/{branch.branch_id}/records/"
    )
    if not ref.relative_path.startswith(prefix):
        raise StageArtifactClaimError(f"{field} crossed run or branch")


def _exact_artifact_scope(ref: ArtifactRef, branch: BranchRef, field: str) -> None:
    exact_prefix = (
        f"project://{quote(branch.run.project_id, safe='')}/"
        f"runs/{quote(branch.run.run_id, safe='')}/branches/"
        f"{quote(branch.branch_id, safe='')}/artifacts/"
    )
    if not ref.uri.startswith(exact_prefix):
        raise StageArtifactClaimError(f"{field} crossed project, run, or branch")


@dataclass(frozen=True, slots=True)
class RecordDigestBinding:
    """One persisted record reference bound to its semantic content digest."""

    record_ref: ProjectRecordRef
    content_digest: str

    SCHEMA = "RecordDigestBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.record_ref, ProjectRecordRef):
            raise TypeError("record_ref must be a ProjectRecordRef")
        object.__setattr__(
            self,
            "content_digest",
            require_sha256(self.content_digest, "content_digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "record_ref": _record_to_dict(self.record_ref),
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RecordDigestBinding":
        payload = _mapping(
            value,
            {"schema", "record_ref", "content_digest"},
            "record digest binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageArtifactClaimError(
                "unsupported record digest binding schema"
            )
        return cls(
            record_ref=_record_from_dict(payload["record_ref"], "record_ref"),
            content_digest=payload["content_digest"],
        )


@dataclass(frozen=True, slots=True)
class ArtifactShaBinding:
    """An artifact reference with an explicit, exact SHA assertion."""

    artifact_ref: ArtifactRef
    artifact_sha256: str

    SCHEMA = "ArtifactShaBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_ref, ArtifactRef):
            raise TypeError("artifact_ref must be an ArtifactRef")
        if self.artifact_ref.sha256 != self.artifact_ref.sha256.lower():
            raise StageArtifactClaimError("artifact reference SHA must be lowercase")
        digest = require_sha256(self.artifact_sha256, "artifact_sha256")
        object.__setattr__(self, "artifact_sha256", digest)
        if self.artifact_ref.sha256.lower() != digest:
            raise StageArtifactClaimError(
                "artifact reference and asserted SHA disagree"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "artifact_ref": _artifact_to_dict(self.artifact_ref),
            "artifact_sha256": self.artifact_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArtifactShaBinding":
        payload = _mapping(
            value,
            {"schema", "artifact_ref", "artifact_sha256"},
            "artifact SHA binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageArtifactClaimError(
                "unsupported artifact SHA binding schema"
            )
        return cls(
            artifact_ref=_artifact_from_dict(
                payload["artifact_ref"], "artifact_ref"
            ),
            artifact_sha256=payload["artifact_sha256"],
        )


@dataclass(frozen=True, slots=True)
class StageArtifactVerificationDenominator:
    """Exact Stage 3 source and receipt identities retained by one claim."""

    stage_subject_inventory_digest: str
    component_index_digest: str
    stage_requirement_profile_digest: str
    baseline_source_set_digest: str
    baseline_coverage_digest: str
    stage_closure_digest: str
    function_ledger_digest: str
    function_relation_requirement_digest: str
    program_digest: str
    readback_digest: str
    topology_source_digests: tuple[str, ...]
    topology_graph_digests: tuple[str, ...]
    realization_source_digests: tuple[str, ...]
    realization_receipt_digests: tuple[str, ...]
    stage_check_receipt_digests: tuple[str, ...]

    SCHEMA = "StageArtifactVerificationDenominator@2"

    def __post_init__(self) -> None:
        for field in (
            "stage_subject_inventory_digest",
            "component_index_digest",
            "stage_requirement_profile_digest",
            "baseline_source_set_digest",
            "baseline_coverage_digest",
            "stage_closure_digest",
            "function_ledger_digest",
            "function_relation_requirement_digest",
            "program_digest",
            "readback_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), field),
            )
        for field in (
            "topology_source_digests",
            "topology_graph_digests",
            "realization_source_digests",
            "realization_receipt_digests",
            "stage_check_receipt_digests",
        ):
            values = getattr(self, field)
            if not isinstance(values, tuple) or not values:
                raise StageArtifactClaimError(f"{field} must be non-empty")
            normalized = tuple(
                sorted(require_sha256(item, field) for item in values)
            )
            if len(normalized) != len(set(normalized)):
                raise StageArtifactClaimError(f"{field} contains duplicates")
            object.__setattr__(self, field, normalized)
        if len(self.topology_source_digests) != len(
            self.topology_graph_digests
        ) or len(self.topology_source_digests) != len(
            self.realization_source_digests
        ):
            raise StageArtifactClaimError(
                "Stage 3 topology and realization denominators differ"
            )

    @property
    def denominator_digest(self) -> str:
        return _canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "stage_subject_inventory_digest": self.stage_subject_inventory_digest,
            "component_index_digest": self.component_index_digest,
            "stage_requirement_profile_digest": (
                self.stage_requirement_profile_digest
            ),
            "baseline_source_set_digest": self.baseline_source_set_digest,
            "baseline_coverage_digest": self.baseline_coverage_digest,
            "stage_closure_digest": self.stage_closure_digest,
            "function_ledger_digest": self.function_ledger_digest,
            "function_relation_requirement_digest": (
                self.function_relation_requirement_digest
            ),
            "program_digest": self.program_digest,
            "readback_digest": self.readback_digest,
            "topology_source_digests": list(self.topology_source_digests),
            "topology_graph_digests": list(self.topology_graph_digests),
            "realization_source_digests": list(
                self.realization_source_digests
            ),
            "realization_receipt_digests": list(
                self.realization_receipt_digests
            ),
            "stage_check_receipt_digests": list(
                self.stage_check_receipt_digests
            ),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> "StageArtifactVerificationDenominator":
        fields = {
            "schema",
            "stage_subject_inventory_digest",
            "component_index_digest",
            "stage_requirement_profile_digest",
            "baseline_source_set_digest",
            "baseline_coverage_digest",
            "stage_closure_digest",
            "function_ledger_digest",
            "function_relation_requirement_digest",
            "program_digest",
            "readback_digest",
            "topology_source_digests",
            "topology_graph_digests",
            "realization_source_digests",
            "realization_receipt_digests",
            "stage_check_receipt_digests",
            *_AUTHORITY_FIELDS,
        }
        payload = _mapping(value, fields, "stage artifact denominator")
        if payload["schema"] != cls.SCHEMA:
            raise StageArtifactClaimError(
                "unsupported stage artifact denominator schema"
            )
        for field in (
            "topology_source_digests",
            "topology_graph_digests",
            "realization_source_digests",
            "realization_receipt_digests",
            "stage_check_receipt_digests",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            stage_subject_inventory_digest=payload[
                "stage_subject_inventory_digest"
            ],
            component_index_digest=payload["component_index_digest"],
            stage_requirement_profile_digest=payload[
                "stage_requirement_profile_digest"
            ],
            baseline_source_set_digest=payload["baseline_source_set_digest"],
            baseline_coverage_digest=payload["baseline_coverage_digest"],
            stage_closure_digest=payload["stage_closure_digest"],
            function_ledger_digest=payload["function_ledger_digest"],
            function_relation_requirement_digest=payload[
                "function_relation_requirement_digest"
            ],
            program_digest=payload["program_digest"],
            readback_digest=payload["readback_digest"],
            topology_source_digests=tuple(payload["topology_source_digests"]),
            topology_graph_digests=tuple(payload["topology_graph_digests"]),
            realization_source_digests=tuple(
                payload["realization_source_digests"]
            ),
            realization_receipt_digests=tuple(
                payload["realization_receipt_digests"]
            ),
            stage_check_receipt_digests=tuple(
                payload["stage_check_receipt_digests"]
            ),
        )
        if result.to_dict() != payload:
            raise StageArtifactClaimError(
                "stage artifact denominator identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class StageArtifactClaim:
    """Evidence-derived, authority-free classification of one stage artifact."""

    claim_id: str
    status: StageArtifactStatus
    branch: BranchRef
    stage_id: str
    proposal: RecordDigestBinding | None = None
    preview: ArtifactShaBinding | None = None
    stage_entry_proof: StageEntryProof | None = None
    stage_entry_proof_record: RecordDigestBinding | None = None
    artifact: ArtifactShaBinding | None = None
    geometry_program: RecordDigestBinding | None = None
    component_index: RecordDigestBinding | None = None
    stage_subject_inventory: RecordDigestBinding | None = None
    function_ledger: RecordDigestBinding | None = None
    function_relation_requirements: RecordDigestBinding | None = None
    stage_requirement_profile: RecordDigestBinding | None = None
    baseline_sources: RecordDigestBinding | None = None
    baseline_coverage: RecordDigestBinding | None = None
    stage_closure: RecordDigestBinding | None = None
    stage_checks: tuple[RecordDigestBinding, ...] = ()
    relation_topology: tuple[RecordDigestBinding, ...] = ()
    cad_readback: RecordDigestBinding | None = None
    relation_realization: tuple[RecordDigestBinding, ...] = ()
    functional_verification: tuple[RecordDigestBinding, ...] = ()
    verification_denominator: StageArtifactVerificationDenominator | None = None
    viewer_refs: tuple[str, ...] = ()
    diagnostic_refs: tuple[str, ...] = ()

    SCHEMA = "StageArtifactClaim@2"

    def __post_init__(self) -> None:
        require_identifier(self.claim_id, "claim_id")
        if not isinstance(self.status, StageArtifactStatus):
            raise TypeError("status must be a StageArtifactStatus")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be a BranchRef")
        require_identifier(self.stage_id, "stage_id")
        for field in (
            "proposal",
            "stage_entry_proof_record",
            "geometry_program",
            "component_index",
            "stage_subject_inventory",
            "function_ledger",
            "function_relation_requirements",
            "stage_requirement_profile",
            "baseline_sources",
            "baseline_coverage",
            "stage_closure",
            "cad_readback",
        ):
            value = getattr(self, field)
            if value is not None and not isinstance(value, RecordDigestBinding):
                raise TypeError(f"{field} must be a RecordDigestBinding or None")
            if value is not None:
                _exact_record_scope(value.record_ref, self.branch, field)
        for field in ("preview", "artifact"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, ArtifactShaBinding):
                raise TypeError(f"{field} must be an ArtifactShaBinding or None")
            if value is not None:
                _exact_artifact_scope(value.artifact_ref, self.branch, field)
        for field in (
            "stage_checks",
            "relation_topology",
            "relation_realization",
            "functional_verification",
        ):
            values = getattr(self, field)
            if not isinstance(values, tuple) or any(
                not isinstance(item, RecordDigestBinding) for item in values
            ):
                raise TypeError(
                    f"{field} must contain RecordDigestBinding values"
                )
            keys = tuple(item.record_ref.uri for item in values)
            if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
                raise StageArtifactClaimError(
                    f"{field} must be sorted and contain unique refs"
                )
            for item in values:
                _exact_record_scope(item.record_ref, self.branch, field)
        for field in ("viewer_refs", "diagnostic_refs"):
            values = getattr(self, field)
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be a tuple")
            for ref in values:
                require_logical_ref(ref, field)
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise StageArtifactClaimError(
                    f"{field} must be sorted and unique"
                )

        core = (
            self.stage_entry_proof_record,
            self.artifact,
            self.geometry_program,
            self.component_index,
            self.function_ledger,
        )
        verification_present = (
            self.verification_denominator is not None
            or self.function_relation_requirements is not None
            or self.stage_subject_inventory is not None
            or self.stage_requirement_profile is not None
            or self.baseline_sources is not None
            or self.baseline_coverage is not None
            or self.stage_closure is not None
            or bool(self.stage_checks)
            or bool(self.relation_topology)
            or self.cad_readback is not None
            or bool(self.relation_realization)
            or bool(self.functional_verification)
        )
        if self.stage_entry_proof is None:
            if any(item is not None for item in core) or verification_present:
                raise StageArtifactClaimError(
                    "artifacts without stage-entry proof must remain exploratory"
                )
            expected = StageArtifactStatus.EXPLORATORY_PRE_STAGE
        else:
            if not isinstance(self.stage_entry_proof, StageEntryProof):
                raise TypeError("stage_entry_proof must be a StageEntryProof")
            require_stage_entry_proof(
                self.stage_entry_proof,
                successor_branch=self.branch,
                from_phase=self.stage_entry_proof.phase_gate.from_phase,
                to_phase=self.stage_entry_proof.phase_gate.to_phase,
            )
            if self.stage_id != self.stage_entry_proof.phase_gate.to_phase.value:
                raise StageArtifactClaimError(
                    "stage-entry proof and claimed stage disagree"
                )
            if any(item is None for item in core):
                raise StageArtifactClaimError(
                    "stage-entered candidate evidence is incomplete"
                )
            assert self.stage_entry_proof_record is not None
            if (
                self.stage_entry_proof_record.content_digest
                != self.stage_entry_proof.proof_digest
            ):
                raise StageArtifactClaimError(
                    "stage-entry proof record binds the wrong proof digest"
                )
            if (
                self.stage_entry_proof_record.record_ref
                == self.stage_entry_proof.stage_exit_checkpoint_ref
            ):
                raise StageArtifactClaimError(
                    "proof record and checkpoint must be distinct to avoid a fixed point"
                )
            all_verification = (
                self.verification_denominator is not None
                and self.stage_subject_inventory is not None
                and self.function_relation_requirements is not None
                and self.stage_requirement_profile is not None
                and self.baseline_sources is not None
                and self.baseline_coverage is not None
                and self.stage_closure is not None
                and bool(self.stage_checks)
                and bool(self.relation_topology)
                and self.cad_readback is not None
                and bool(self.relation_realization)
                and bool(self.functional_verification)
            )
            if verification_present and not all_verification:
                raise StageArtifactClaimError(
                    "Stage3 verification evidence is incomplete"
                )
            if all_verification:
                denominator = self.verification_denominator
                assert denominator is not None
                assert self.geometry_program is not None
                assert self.component_index is not None
                assert self.stage_subject_inventory is not None
                assert self.function_ledger is not None
                assert self.function_relation_requirements is not None
                assert self.stage_requirement_profile is not None
                assert self.baseline_sources is not None
                assert self.baseline_coverage is not None
                assert self.stage_closure is not None
                assert self.cad_readback is not None
                exact_bindings = (
                    (
                        self.geometry_program.content_digest,
                        denominator.program_digest,
                    ),
                    (
                        self.component_index.content_digest,
                        denominator.component_index_digest,
                    ),
                    (
                        self.stage_subject_inventory.content_digest,
                        denominator.stage_subject_inventory_digest,
                    ),
                    (
                        self.function_ledger.content_digest,
                        denominator.function_ledger_digest,
                    ),
                    (
                        self.function_relation_requirements.content_digest,
                        denominator.function_relation_requirement_digest,
                    ),
                    (
                        self.stage_requirement_profile.content_digest,
                        denominator.stage_requirement_profile_digest,
                    ),
                    (
                        self.baseline_sources.content_digest,
                        denominator.baseline_source_set_digest,
                    ),
                    (
                        self.baseline_coverage.content_digest,
                        denominator.baseline_coverage_digest,
                    ),
                    (
                        self.stage_closure.content_digest,
                        denominator.stage_closure_digest,
                    ),
                    (
                        self.cad_readback.content_digest,
                        denominator.readback_digest,
                    ),
                )
                if any(actual != expected for actual, expected in exact_bindings):
                    raise StageArtifactClaimError(
                        "Stage3 record bindings differ from the exact denominator"
                    )
                if tuple(
                    sorted(item.content_digest for item in self.stage_checks)
                ) != denominator.stage_check_receipt_digests:
                    raise StageArtifactClaimError(
                        "Stage3 check records differ from the exact denominator"
                    )
                if tuple(
                    sorted(item.content_digest for item in self.relation_topology)
                ) != denominator.topology_source_digests:
                    raise StageArtifactClaimError(
                        "Stage3 topology records differ from the exact denominator"
                    )
                if tuple(
                    sorted(item.content_digest for item in self.relation_realization)
                ) != denominator.realization_source_digests:
                    raise StageArtifactClaimError(
                        "Stage3 realization records differ from the exact denominator"
                    )
                if tuple(
                    sorted(item.content_digest for item in self.functional_verification)
                ) != denominator.realization_receipt_digests:
                    raise StageArtifactClaimError(
                        "Stage3 verification receipts differ from the exact denominator"
                    )
            expected = (
                StageArtifactStatus.STAGE3_VERIFIED_CANDIDATE
                if all_verification
                else StageArtifactStatus.STAGE_ENTERED_CANDIDATE
            )
        if self.status is not expected:
            raise StageArtifactClaimError(
                "status is not justified by the exact evidence bindings"
            )

    @property
    def claim_ref(self) -> str:
        return f"stage-artifact-claim:{self.claim_digest}"

    @property
    def claim_digest(self) -> str:
        return _canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        def binding(value: RecordDigestBinding | ArtifactShaBinding | None) -> object:
            return None if value is None else value.to_dict()

        return {
            "schema": self.SCHEMA,
            "claim_id": self.claim_id,
            "status": self.status.value,
            "branch": _branch_to_dict(self.branch),
            "stage_id": self.stage_id,
            "proposal": binding(self.proposal),
            "preview": binding(self.preview),
            "stage_entry_proof": (
                None
                if self.stage_entry_proof is None
                else self.stage_entry_proof.to_dict()
            ),
            "stage_entry_proof_record": binding(
                self.stage_entry_proof_record
            ),
            "artifact": binding(self.artifact),
            "geometry_program": binding(self.geometry_program),
            "component_index": binding(self.component_index),
            "stage_subject_inventory": binding(
                self.stage_subject_inventory
            ),
            "function_ledger": binding(self.function_ledger),
            "function_relation_requirements": binding(
                self.function_relation_requirements
            ),
            "stage_requirement_profile": binding(
                self.stage_requirement_profile
            ),
            "baseline_sources": binding(self.baseline_sources),
            "baseline_coverage": binding(self.baseline_coverage),
            "stage_closure": binding(self.stage_closure),
            "stage_checks": [item.to_dict() for item in self.stage_checks],
            "relation_topology": [
                item.to_dict() for item in self.relation_topology
            ],
            "cad_readback": binding(self.cad_readback),
            "relation_realization": [
                item.to_dict() for item in self.relation_realization
            ],
            "functional_verification": [
                item.to_dict() for item in self.functional_verification
            ],
            "verification_denominator": (
                None
                if self.verification_denominator is None
                else self.verification_denominator.to_dict()
            ),
            "viewer_refs": list(self.viewer_refs),
            "diagnostic_refs": list(self.diagnostic_refs),
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "claim_digest": self.claim_digest}

    @classmethod
    def from_dict(cls, value: object) -> "StageArtifactClaim":
        keys = {
            "schema", "claim_id", "status", "branch", "stage_id",
            "proposal", "preview", "stage_entry_proof",
            "stage_entry_proof_record", "artifact", "geometry_program",
            "component_index", "function_ledger", "cad_readback",
            "stage_subject_inventory",
            "function_relation_requirements", "baseline_sources",
            "stage_requirement_profile", "baseline_coverage", "stage_closure",
            "stage_checks", "relation_topology",
            "relation_realization", "functional_verification",
            "verification_denominator",
            "viewer_refs", "diagnostic_refs", "claim_digest",
            *_AUTHORITY_FIELDS,
        }
        payload = _mapping(value, keys, "stage artifact claim")
        if payload["schema"] != cls.SCHEMA:
            raise StageArtifactClaimError("unsupported stage artifact claim schema")
        for field in (
            "stage_checks", "relation_topology", "relation_realization", "functional_verification",
            "viewer_refs", "diagnostic_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")

        def record(value: object) -> RecordDigestBinding | None:
            return None if value is None else RecordDigestBinding.from_dict(value)

        result = cls(
            claim_id=payload["claim_id"],
            status=StageArtifactStatus(payload["status"]),
            branch=_branch_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            proposal=record(payload["proposal"]),
            preview=(
                None
                if payload["preview"] is None
                else ArtifactShaBinding.from_dict(payload["preview"])
            ),
            stage_entry_proof=(
                None
                if payload["stage_entry_proof"] is None
                else StageEntryProof.from_dict(payload["stage_entry_proof"])
            ),
            stage_entry_proof_record=record(
                payload["stage_entry_proof_record"]
            ),
            artifact=(
                None
                if payload["artifact"] is None
                else ArtifactShaBinding.from_dict(payload["artifact"])
            ),
            geometry_program=record(payload["geometry_program"]),
            component_index=record(payload["component_index"]),
            stage_subject_inventory=record(
                payload["stage_subject_inventory"]
            ),
            function_ledger=record(payload["function_ledger"]),
            function_relation_requirements=record(
                payload["function_relation_requirements"]
            ),
            stage_requirement_profile=record(
                payload["stage_requirement_profile"]
            ),
            baseline_sources=record(payload["baseline_sources"]),
            baseline_coverage=record(payload["baseline_coverage"]),
            stage_closure=record(payload["stage_closure"]),
            stage_checks=tuple(
                RecordDigestBinding.from_dict(item)
                for item in payload["stage_checks"]
            ),
            relation_topology=tuple(
                RecordDigestBinding.from_dict(item)
                for item in payload["relation_topology"]
            ),
            cad_readback=record(payload["cad_readback"]),
            relation_realization=tuple(
                RecordDigestBinding.from_dict(item)
                for item in payload["relation_realization"]
            ),
            functional_verification=tuple(
                RecordDigestBinding.from_dict(item)
                for item in payload["functional_verification"]
            ),
            verification_denominator=(
                None
                if payload["verification_denominator"] is None
                else StageArtifactVerificationDenominator.from_dict(
                    payload["verification_denominator"]
                )
            ),
            viewer_refs=tuple(payload["viewer_refs"]),
            diagnostic_refs=tuple(payload["diagnostic_refs"]),
        )
        if result.to_dict() != payload:
            raise StageArtifactClaimError("stage artifact claim digest changed")
        return result


__all__ = [
    "ArtifactShaBinding",
    "RecordDigestBinding",
    "StageArtifactClaim",
    "StageArtifactClaimError",
    "StageArtifactStatus",
    "StageArtifactVerificationDenominator",
]
