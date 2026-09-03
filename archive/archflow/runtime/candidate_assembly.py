"""Candidate assembly and exact-plan handoff without promotion authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.project.ports import PersistenceArea, PersistenceDestination, RecordSink
from archflow.project.refs import ProjectRecordRef, ProjectVersionRef, RunRef
from archflow.project.refs import require_identifier
from archflow.state.model import ArtifactRef
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentCoordinationStatus,
)
from archflow.state.operational_state import require_logical_ref
from archflow.submission.model import CandidateDelta, CandidateSubmission, Claim
from archflow.contracts.canonical import canonical_digest, canonical_json


_HEX = frozenset("0123456789abcdef")
_MAX_ITEMS = 16_384


class CandidateAssemblyError(ValueError):
    """The candidate handoff is incomplete, stale, or untraceable."""


class CandidatePolicyKind(StrEnum):
    BUILD = "build"
    APPROVAL = "approval"


class CandidateDisposition(StrEnum):
    ASSEMBLED = "assembled"
    EXECUTED = "executed"
    REJECTED = "rejected"
    REVISED = "revised"


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _HEX for char in value.lower())
    ):
        raise CandidateAssemblyError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateAssemblyError(f"{field} must be non-empty text")
    return value


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise CandidateAssemblyError(f"{field} has an invalid item count")
    for value in values:
        require_logical_ref(value, field)
    if len(values) != len(set(values)):
        raise CandidateAssemblyError(f"{field} contains duplicates")
    return values


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if len(values) > _MAX_ITEMS or (not values and not allow_empty):
        raise CandidateAssemblyError(f"{field} has an invalid item count")
    for value in values:
        require_identifier(value, field)
    if len(values) != len(set(values)):
        raise CandidateAssemblyError(f"{field} contains duplicates")
    return values


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise CandidateAssemblyError(f"{label} schema drifted")


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        raise TypeError(f"{field} must be a string list")
    return tuple(value)


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "candidate base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "candidate base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


def _json_pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _leaf_paths(value: object, path: str = "") -> tuple[str, ...]:
    if isinstance(value, dict) and value:
        paths: list[str] = []
        for key in sorted(value):
            paths.extend(
                _leaf_paths(
                    value[key],
                    f"{path}/{_json_pointer_token(key)}",
                )
            )
        return tuple(paths)
    if isinstance(value, list) and value:
        paths = []
        for index, item in enumerate(value):
            paths.extend(_leaf_paths(item, f"{path}/{index}"))
        return tuple(paths)
    return (path or "/",)


@dataclass(frozen=True, slots=True)
class CandidatePolicyBinding:
    kind: CandidatePolicyKind
    policy_ref: str
    policy_digest: str
    evidence_refs: tuple[str, ...]

    SCHEMA = "CandidatePolicyBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CandidatePolicyKind):
            raise TypeError("kind must be CandidatePolicyKind")
        require_logical_ref(self.policy_ref, "policy_ref")
        _sha(self.policy_digest, "policy_digest")
        _refs(self.evidence_refs, "policy evidence_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "kind": self.kind.value,
            "policy_ref": self.policy_ref,
            "policy_digest": self.policy_digest,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidatePolicyBinding:
        payload = _mapping(value, "candidate policy binding")
        _exact(
            payload,
            {
                "schema",
                "kind",
                "policy_ref",
                "policy_digest",
                "evidence_refs",
            },
            "candidate policy binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CandidateAssemblyError("candidate policy schema changed")
        return cls(
            kind=CandidatePolicyKind(payload["kind"]),
            policy_ref=payload["policy_ref"],
            policy_digest=payload["policy_digest"],
            evidence_refs=_strings(payload["evidence_refs"], "evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class PlanValueBinding:
    """Derivation for one scalar or empty container in the MCP payload."""

    json_pointer: str
    design_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "PlanValueBinding@2"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.json_pointer, str)
            or not self.json_pointer.startswith("/")
        ):
            raise CandidateAssemblyError(
                "json_pointer must be an absolute JSON pointer"
            )
        _refs(
            self.design_refs,
            "plan design_refs",
            allow_empty=True,
        )
        _refs(
            self.evidence_refs,
            "plan evidence_refs",
            allow_empty=True,
        )
        if not self.design_refs and not self.evidence_refs:
            raise CandidateAssemblyError(
                "every plan value needs design or evidence provenance"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "json_pointer": self.json_pointer,
            "design_refs": list(self.design_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanValueBinding:
        payload = _mapping(value, "plan value binding")
        _exact(
            payload,
            {
                "schema",
                "json_pointer",
                "design_refs",
                "evidence_refs",
            },
            "plan value binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise CandidateAssemblyError("plan value binding schema changed")
        return cls(
            json_pointer=payload["json_pointer"],
            design_refs=_strings(
                payload["design_refs"],
                "design_refs",
            ),
            evidence_refs=_strings(payload["evidence_refs"], "evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class CandidateExecutablePlan:
    """Frozen project-authored MCP payload with complete leaf provenance."""

    plan_id: str
    project_id: str
    run_id: str
    base: ProjectVersionRef
    design_state_digest: str
    payload_json: str
    bindings: tuple[PlanValueBinding, ...]

    SCHEMA = "CandidateExecutablePlan@2"

    def __post_init__(self) -> None:
        require_identifier(self.plan_id, "plan_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if (
            not isinstance(self.base, ProjectVersionRef)
            or self.base.project_id != self.project_id
        ):
            raise CandidateAssemblyError("plan and base disagree")
        _sha(self.design_state_digest, "design_state_digest")
        if not isinstance(self.payload_json, str):
            raise TypeError("payload_json must be text")
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise CandidateAssemblyError(
                "payload_json must contain JSON"
            ) from exc
        if not isinstance(payload, dict) or not payload:
            raise CandidateAssemblyError(
                "MCP plan payload must be a non-empty object"
            )
        if canonical_json(payload) != self.payload_json:
            raise CandidateAssemblyError(
                "MCP plan payload must be canonical JSON"
            )
        if not isinstance(self.bindings, tuple) or any(
            not isinstance(item, PlanValueBinding)
            for item in self.bindings
        ):
            raise TypeError("bindings contains an invalid item")
        pointers = tuple(item.json_pointer for item in self.bindings)
        if pointers != tuple(sorted(pointers)) or len(pointers) != len(
            set(pointers)
        ):
            raise CandidateAssemblyError(
                "plan bindings require unique deterministic pointers"
            )
        leaves = _leaf_paths(payload)
        if pointers != tuple(sorted(leaves)):
            missing = sorted(set(leaves) - set(pointers))
            extra = sorted(set(pointers) - set(leaves))
            raise CandidateAssemblyError(
                f"plan leaf provenance is incomplete; missing={missing}, "
                f"extra={extra}"
            )

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        state: DevelopedDesignState,
        payload: Mapping[str, Any],
        bindings: tuple[PlanValueBinding, ...],
    ) -> CandidateExecutablePlan:
        return cls(
            plan_id=plan_id,
            project_id=state.project_id,
            run_id=state.run_id,
            base=state.base,
            design_state_digest=state.state_digest,
            payload_json=canonical_json(dict(payload)),
            bindings=tuple(
                sorted(bindings, key=lambda item: item.json_pointer)
            ),
        )

    @property
    def plan_digest(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()

    @property
    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):
            raise AssertionError("validated MCP plan stopped being an object")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "design_state_digest": self.design_state_digest,
            "payload_json": self.payload_json,
            "plan_digest": self.plan_digest,
            "bindings": [item.to_dict() for item in self.bindings],
            "preview_execute_same_payload_required": True,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateExecutablePlan:
        payload = _mapping(value, "candidate executable plan")
        _exact(
            payload,
            {
                "schema",
                "plan_id",
                "project_id",
                "run_id",
                "base",
                "design_state_digest",
                "payload_json",
                "plan_digest",
                "bindings",
                "preview_execute_same_payload_required",
                "canonical_write_authority",
            },
            "candidate executable plan",
        )
        bindings = payload["bindings"]
        if not isinstance(bindings, list):
            raise TypeError("plan bindings must be a list")
        plan = cls(
            plan_id=payload["plan_id"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            design_state_digest=payload["design_state_digest"],
            payload_json=payload["payload_json"],
            bindings=tuple(
                PlanValueBinding.from_dict(item) for item in bindings
            ),
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["plan_digest"] != plan.plan_digest
            or payload["preview_execute_same_payload_required"] is not True
        ):
            raise CandidateAssemblyError(
                "candidate executable plan authority or digest drifted"
            )
        return plan


def _artifact_to_dict(value: ArtifactRef) -> dict[str, object]:
    return {
        "artifact_id": value.artifact_id,
        "uri": value.uri,
        "media_type": value.media_type,
        "sha256": value.sha256,
    }


def _artifact_from_dict(value: object) -> ArtifactRef:
    payload = _mapping(value, "artifact")
    _exact(
        payload,
        {"artifact_id", "uri", "media_type", "sha256"},
        "artifact",
    )
    return ArtifactRef(
        artifact_id=payload["artifact_id"],
        uri=payload["uri"],
        media_type=payload["media_type"],
        sha256=payload["sha256"],
    )


def _submission_to_dict(value: CandidateSubmission) -> dict[str, object]:
    delta = value.delta
    if (
        delta.facts_add
        or delta.commitments_add
        or delta.obligations_discharge
        or delta.obligations_add
    ):
        raise CandidateAssemblyError(
            "P024 candidate delta may carry artifacts only"
        )
    return {
        "submission_id": value.submission_id,
        "base": _base_to_dict(value.base),
        "workspace_id": value.workspace_id,
        "intent": value.intent,
        "delta": {
            "artifacts_add": [
                _artifact_to_dict(item) for item in delta.artifacts_add
            ],
        },
        "claims": [
            {
                "key": item.key,
                "value": item.value,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in value.claims
        ],
        "evidence_refs": list(value.evidence_refs),
        "unresolved": list(value.unresolved),
    }


def _submission_from_dict(value: object) -> CandidateSubmission:
    payload = _mapping(value, "candidate submission")
    _exact(
        payload,
        {
            "submission_id",
            "base",
            "workspace_id",
            "intent",
            "delta",
            "claims",
            "evidence_refs",
            "unresolved",
        },
        "candidate submission",
    )
    delta = _mapping(payload["delta"], "candidate delta")
    _exact(delta, {"artifacts_add"}, "candidate delta")
    artifacts = delta["artifacts_add"]
    claims = payload["claims"]
    if not isinstance(artifacts, list) or not isinstance(claims, list):
        raise TypeError("candidate artifacts and claims must be lists")
    return CandidateSubmission(
        submission_id=payload["submission_id"],
        base=_base_from_dict(payload["base"]),
        workspace_id=payload["workspace_id"],
        intent=payload["intent"],
        delta=CandidateDelta(
            artifacts_add=tuple(_artifact_from_dict(item) for item in artifacts)
        ),
        claims=tuple(
            Claim(
                key=_mapping(item, "candidate claim")["key"],
                value=_mapping(item, "candidate claim")["value"],
                evidence_refs=_strings(
                    _mapping(item, "candidate claim")["evidence_refs"],
                    "claim evidence_refs",
                ),
            )
            for item in claims
        ),
        evidence_refs=_strings(
            payload["evidence_refs"],
            "submission evidence_refs",
        ),
        unresolved=_strings(payload["unresolved"], "submission unresolved"),
    )


def _design_refs(state: DevelopedDesignState) -> set[str]:
    """Logical identities exposed to executable-plan provenance bindings."""

    schematic = state.selected_schematic
    return {
        schematic.ref,
        schematic.option.ref,
        schematic.selection_decision_ref,
        *(item.identity_ref for item in schematic.option.proposal.components),
        *(item.ref for item in state.components),
    }


@dataclass(frozen=True, slots=True)
class CandidateAssembly:
    design_state: DevelopedDesignState
    plan: CandidateExecutablePlan
    policies: tuple[CandidatePolicyBinding, ...]
    submission: CandidateSubmission

    SCHEMA = "CandidateAssembly@2"

    def __post_init__(self) -> None:
        if not isinstance(self.design_state, DevelopedDesignState):
            raise TypeError("design_state must be DevelopedDesignState")
        if not isinstance(self.plan, CandidateExecutablePlan):
            raise TypeError("plan must be CandidateExecutablePlan")
        if (
            self.plan.project_id != self.design_state.project_id
            or self.plan.run_id != self.design_state.run_id
            or self.plan.base != self.design_state.base
            or self.plan.design_state_digest
            != self.design_state.state_digest
        ):
            raise CandidateAssemblyError(
                "design state and executable plan disagree"
            )
        if not isinstance(self.policies, tuple) or any(
            not isinstance(item, CandidatePolicyBinding)
            for item in self.policies
        ):
            raise TypeError("policies contains an invalid item")
        kinds = tuple(item.kind for item in self.policies)
        if kinds != tuple(sorted(kinds, key=lambda item: item.value)):
            raise CandidateAssemblyError(
                "candidate policies require deterministic kind order"
            )
        if len(kinds) != 2 or set(kinds) != {
            CandidatePolicyKind.APPROVAL,
            CandidatePolicyKind.BUILD,
        }:
            raise CandidateAssemblyError(
                "candidate requires explicit build and approval policies"
            )
        known_refs = _design_refs(self.design_state)
        referenced = {
            design_ref
            for binding in self.plan.bindings
            for design_ref in binding.design_refs
        }
        unknown = referenced - known_refs
        if unknown:
            raise CandidateAssemblyError(
                f"plan references unknown design identities: {sorted(unknown)}"
            )
        if not isinstance(self.submission, CandidateSubmission):
            raise TypeError("submission must be CandidateSubmission")
        if (
            self.submission.base != self.design_state.base
            or self.submission.delta.artifacts_add
        ):
            raise CandidateAssemblyError(
                "assembled submission must be exact-base and pre-execution"
            )

    @property
    def assembly_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"candidate-assembly:{self.assembly_digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "design_state": self.design_state.to_dict(),
            "plan": self.plan.to_dict(),
            "policies": [item.to_dict() for item in self.policies],
            "submission": _submission_to_dict(self.submission),
            "approval_receipt": None,
            "hard_usability_verdict": None,
            "aesthetic_winner": None,
            "commitment_monitor_verdict": None,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateAssembly:
        payload = _mapping(value, "candidate assembly")
        _exact(
            payload,
            {
                "schema",
                "design_state",
                "plan",
                "policies",
                "submission",
                "approval_receipt",
                "hard_usability_verdict",
                "aesthetic_winner",
                "commitment_monitor_verdict",
                "canonical_write_authority",
            },
            "candidate assembly",
        )
        policies = payload["policies"]
        if not isinstance(policies, list):
            raise TypeError("candidate policies must be a list")
        if (
            payload["schema"] != cls.SCHEMA
            or payload["approval_receipt"] is not None
            or payload["hard_usability_verdict"] is not None
            or payload["aesthetic_winner"] is not None
            or payload["commitment_monitor_verdict"] is not None
        ):
            raise CandidateAssemblyError(
                "candidate assembly acquired downstream authority"
            )
        return cls(
            design_state=DevelopedDesignState.from_dict(
                payload["design_state"]
            ),
            plan=CandidateExecutablePlan.from_dict(payload["plan"]),
            policies=tuple(
                CandidatePolicyBinding.from_dict(item) for item in policies
            ),
            submission=_submission_from_dict(payload["submission"]),
        )


@dataclass(frozen=True, slots=True)
class CandidateExecutionHandoff:
    """MCP evidence remains speculative until independent acceptance."""

    assembly_digest: str
    source_submission_id: str
    plan_digest: str
    preview_plan_digest: str
    execute_plan_digest: str
    preview_artifact: ArtifactRef
    executed_artifact: ArtifactRef
    submission: CandidateSubmission

    SCHEMA = "CandidateExecutionHandoff@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.assembly_digest, "assembly_digest"),
            (self.plan_digest, "plan_digest"),
            (self.preview_plan_digest, "preview_plan_digest"),
            (self.execute_plan_digest, "execute_plan_digest"),
        ):
            _sha(value, field)
        require_identifier(
            self.source_submission_id,
            "source_submission_id",
        )
        if (
            self.preview_plan_digest != self.plan_digest
            or self.execute_plan_digest != self.plan_digest
        ):
            raise CandidateAssemblyError(
                "preview and execute did not bind the exact candidate plan"
            )
        if not isinstance(self.preview_artifact, ArtifactRef) or not isinstance(
            self.executed_artifact,
            ArtifactRef,
        ):
            raise TypeError("MCP artifacts must be ArtifactRef values")
        if (
            not isinstance(self.submission, CandidateSubmission)
            or self.submission.delta.artifacts_add
            != (self.executed_artifact,)
        ):
            raise CandidateAssemblyError(
                "executed submission must carry only the candidate artifact"
            )

    @property
    def handoff_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assembly_digest": self.assembly_digest,
            "source_submission_id": self.source_submission_id,
            "plan_digest": self.plan_digest,
            "preview_plan_digest": self.preview_plan_digest,
            "execute_plan_digest": self.execute_plan_digest,
            "preview_artifact": _artifact_to_dict(self.preview_artifact),
            "executed_artifact": _artifact_to_dict(self.executed_artifact),
            "submission": _submission_to_dict(self.submission),
            "mcp_succeeded": True,
            "hard_usability_verdict": None,
            "aesthetic_winner": None,
            "commitment_monitor_verdict": None,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateExecutionHandoff:
        payload = _mapping(value, "candidate execution handoff")
        _exact(
            payload,
            {
                "schema",
                "assembly_digest",
                "source_submission_id",
                "plan_digest",
                "preview_plan_digest",
                "execute_plan_digest",
                "preview_artifact",
                "executed_artifact",
                "submission",
                "mcp_succeeded",
                "hard_usability_verdict",
                "aesthetic_winner",
                "commitment_monitor_verdict",
                "canonical_write_authority",
            },
            "candidate execution handoff",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["mcp_succeeded"] is not True
            or payload["hard_usability_verdict"] is not None
            or payload["aesthetic_winner"] is not None
            or payload["commitment_monitor_verdict"] is not None
        ):
            raise CandidateAssemblyError(
                "MCP handoff acquired acceptance or write authority"
            )
        return cls(
            assembly_digest=payload["assembly_digest"],
            source_submission_id=payload["source_submission_id"],
            plan_digest=payload["plan_digest"],
            preview_plan_digest=payload["preview_plan_digest"],
            execute_plan_digest=payload["execute_plan_digest"],
            preview_artifact=_artifact_from_dict(
                payload["preview_artifact"]
            ),
            executed_artifact=_artifact_from_dict(
                payload["executed_artifact"]
            ),
            submission=_submission_from_dict(payload["submission"]),
        )


@dataclass(frozen=True, slots=True)
class CandidateDerivationArchive:
    """Immutable full derivation retained for accepted or failed candidates."""

    assembly: CandidateAssembly
    disposition: CandidateDisposition
    execution: CandidateExecutionHandoff | None
    predecessor_candidate_ref: str | None
    review_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rationale: str

    SCHEMA = "CandidateDerivationArchive@1"

    def __post_init__(self) -> None:
        if not isinstance(self.assembly, CandidateAssembly):
            raise TypeError("assembly must be CandidateAssembly")
        if not isinstance(self.disposition, CandidateDisposition):
            raise TypeError("disposition must be CandidateDisposition")
        if self.execution is not None:
            if (
                not isinstance(self.execution, CandidateExecutionHandoff)
                or self.execution.assembly_digest
                != self.assembly.assembly_digest
            ):
                raise CandidateAssemblyError(
                    "execution does not belong to archived assembly"
                )
        if self.disposition is CandidateDisposition.ASSEMBLED and self.execution:
            raise CandidateAssemblyError(
                "assembled disposition cannot carry execution"
            )
        if self.disposition is CandidateDisposition.EXECUTED and not self.execution:
            raise CandidateAssemblyError(
                "executed disposition requires MCP handoff"
            )
        if self.disposition is CandidateDisposition.REJECTED and not self.review_refs:
            raise CandidateAssemblyError(
                "rejected candidate requires review receipts"
            )
        if self.disposition is CandidateDisposition.REVISED:
            if self.predecessor_candidate_ref is None:
                raise CandidateAssemblyError(
                    "revised candidate requires predecessor"
                )
        if self.predecessor_candidate_ref is not None:
            require_logical_ref(
                self.predecessor_candidate_ref,
                "predecessor_candidate_ref",
            )
        _refs(self.review_refs, "review_refs", allow_empty=True)
        _refs(self.evidence_refs, "archive evidence_refs")
        _text(self.rationale, "archive rationale")

    @property
    def archive_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "assembly": self.assembly.to_dict(),
            "disposition": self.disposition.value,
            "execution": (
                self.execution.to_dict()
                if self.execution is not None
                else None
            ),
            "predecessor_candidate_ref": self.predecessor_candidate_ref,
            "review_refs": list(self.review_refs),
            "evidence_refs": list(self.evidence_refs),
            "rationale": self.rationale,
            "full_derivation_embedded": True,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> CandidateDerivationArchive:
        payload = _mapping(value, "candidate derivation archive")
        _exact(
            payload,
            {
                "schema",
                "assembly",
                "disposition",
                "execution",
                "predecessor_candidate_ref",
                "review_refs",
                "evidence_refs",
                "rationale",
                "full_derivation_embedded",
                "canonical_write_authority",
            },
            "candidate derivation archive",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["full_derivation_embedded"] is not True
        ):
            raise CandidateAssemblyError(
                "candidate archive lost derivation or acquired authority"
            )
        execution = payload["execution"]
        return cls(
            assembly=CandidateAssembly.from_dict(payload["assembly"]),
            disposition=CandidateDisposition(payload["disposition"]),
            execution=(
                CandidateExecutionHandoff.from_dict(execution)
                if execution is not None
                else None
            ),
            predecessor_candidate_ref=payload[
                "predecessor_candidate_ref"
            ],
            review_refs=_strings(payload["review_refs"], "review_refs"),
            evidence_refs=_strings(
                payload["evidence_refs"],
                "archive evidence_refs",
            ),
            rationale=payload["rationale"],
        )


class CandidateArchiveLoader(Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


def assemble_candidate(
    state: DevelopedDesignState,
    *,
    workspace_id: str,
    plan_id: str,
    plan_payload: Mapping[str, Any],
    plan_bindings: tuple[PlanValueBinding, ...],
    policies: tuple[CandidatePolicyBinding, ...],
    evidence_refs: tuple[str, ...],
) -> CandidateAssembly:
    """Assemble one candidate without validating, executing, or committing it."""

    require_identifier(workspace_id, "workspace_id")
    _refs(evidence_refs, "candidate assembly evidence_refs")
    if not isinstance(state, DevelopedDesignState):
        raise TypeError("state must be DevelopedDesignState")
    if (
        state.coordination_status
        is not DevelopmentCoordinationStatus.COORDINATED
        or state.latest_invalidation is not None
    ):
        raise CandidateAssemblyError(
            "candidate requires a coordinated non-invalidated design state"
        )
    plan = CandidateExecutablePlan.create(
        plan_id=plan_id,
        state=state,
        payload=plan_payload,
        bindings=plan_bindings,
    )
    ordered_policies = tuple(
        sorted(policies, key=lambda item: item.kind.value)
    )
    candidate_seed = {
        "design_state": state.state_digest,
        "plan": plan.plan_digest,
        "policies": [item.to_dict() for item in ordered_policies],
        "workspace_id": workspace_id,
    }
    submission_id = f"candidate-{canonical_digest(candidate_seed)[:20]}"
    developments = {item.component_id: item for item in state.components}
    claims = tuple(
        Claim(
            key=f"component.{component.component_id}",
            value=canonical_json(
                {
                    "component": component.to_dict(),
                    "development": (
                        developments[component.component_id].to_dict()
                        if component.component_id in developments
                        else None
                    ),
                }
            ),
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *component.source_refs,
                        state.selected_schematic.ref,
                        state.selected_schematic.option.ref,
                        *(
                            developments[component.component_id].evidence_refs
                            if component.component_id in developments
                            else ()
                        ),
                    )
                )
            ),
        )
        for component in state.selected_schematic.option.proposal.components
    )
    all_evidence = tuple(
        dict.fromkeys(
            (
                *evidence_refs,
                state.selected_schematic.option.ref,
                state.selected_schematic.selection_decision_ref,
                *(
                    ref
                    for item in ordered_policies
                    for ref in (item.policy_ref, *item.evidence_refs)
                ),
            )
        )
    )
    unresolved = tuple(
        f"development-obligation:{item.obligation_id}"
        for item in state.obligations
        if item.status.value != "resolved"
    )
    submission = CandidateSubmission(
        submission_id=submission_id,
        base=state.base,
        workspace_id=workspace_id,
        intent=(
            "Review the exact coordinated design-development state as a "
            "speculative candidate."
        ),
        delta=CandidateDelta(),
        claims=claims,
        evidence_refs=all_evidence,
        unresolved=unresolved,
    )
    return CandidateAssembly(
        design_state=state,
        plan=plan,
        policies=ordered_policies,
        submission=submission,
    )


def bind_mcp_execution(
    assembly: CandidateAssembly,
    *,
    preview_artifact: ArtifactRef,
    executed_artifact: ArtifactRef,
    preview_plan_digest: str,
    execute_plan_digest: str,
) -> CandidateExecutionHandoff:
    """Bind MCP evidence; this still cannot evaluate or promote the candidate."""

    if not isinstance(assembly, CandidateAssembly):
        raise TypeError("assembly must be CandidateAssembly")
    source = assembly.submission
    executed_id = f"candidate-{canonical_digest({
        'source_submission_id': source.submission_id,
        'artifact_sha256': executed_artifact.sha256,
        'plan_digest': assembly.plan.plan_digest,
    })[:20]}"
    submission = CandidateSubmission(
        submission_id=executed_id,
        base=source.base,
        workspace_id=source.workspace_id,
        intent=source.intent,
        delta=CandidateDelta(artifacts_add=(executed_artifact,)),
        claims=source.claims,
        evidence_refs=tuple(
            dict.fromkeys(
                (
                    *source.evidence_refs,
                    preview_artifact.artifact_id,
                    executed_artifact.artifact_id,
                )
            )
        ),
        unresolved=source.unresolved,
    )
    return CandidateExecutionHandoff(
        assembly_digest=assembly.assembly_digest,
        source_submission_id=source.submission_id,
        plan_digest=assembly.plan.plan_digest,
        preview_plan_digest=preview_plan_digest,
        execute_plan_digest=execute_plan_digest,
        preview_artifact=preview_artifact,
        executed_artifact=executed_artifact,
        submission=submission,
    )


def persist_candidate_archive(
    sink: RecordSink,
    *,
    run: RunRef,
    archive: CandidateDerivationArchive,
    destination: PersistenceDestination,
) -> ProjectRecordRef:
    """Persist only through the generic project repository candidate area."""

    if not isinstance(run, RunRef):
        raise TypeError("run must be RunRef")
    if (
        destination.area is not PersistenceArea.RUN_CANDIDATE
        or destination.run_id != run.run_id
    ):
        raise CandidateAssemblyError(
            "candidate archive requires its run candidate destination"
        )
    if (
        archive.assembly.design_state.project_id != run.project_id
        or archive.assembly.design_state.run_id != run.run_id
        or archive.assembly.design_state.base != run.base
    ):
        raise CandidateAssemblyError(
            "candidate archive does not belong to the supplied run"
        )
    return sink.put_json(
        run=run,
        destination=destination,
        record_kind="candidate-derivation",
        payload=archive.to_dict(),
    )


def load_candidate_archive(
    loader: CandidateArchiveLoader,
    ref: ProjectRecordRef,
) -> CandidateDerivationArchive:
    archive = CandidateDerivationArchive.from_dict(loader.load_json(ref))
    if archive.assembly.design_state.project_id != ref.project_id:
        raise CandidateAssemblyError(
            "candidate archive record belongs to another project"
        )
    return archive
