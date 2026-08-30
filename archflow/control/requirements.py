"""Requirement-first stage profiles with an explicit coverage denominator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.project.refs import (
    BranchRef,
    ProjectVersionRef,
    RunRef,
    require_identifier,
)
from archflow.state.operational_state import require_logical_ref


class StageRequirementError(ValueError):
    """A requirement profile is malformed or authority-ambiguous."""


class RequirementBasisMode(StrEnum):
    """Authority required before a check may satisfy a requirement."""

    UNIVERSAL = "universal"
    CLAIM_BOUND = "claim_bound"
    AUTHORITY_BOUND = "authority_bound"


class RequirementTargetKind(StrEnum):
    STAGE = "stage"
    DECISION = "decision"
    COMPONENT = "component"
    RELATION = "relation"
    ASSEMBLY = "assembly"
    PARAMETER = "parameter"
    ARTIFACT = "artifact"
    MATERIAL = "material"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    candidate = value.strip()
    if not candidate or len(candidate) > 1_000:
        raise StageRequirementError(f"{field} must be bounded non-empty text")
    return candidate


def _refs(values: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    normalized = tuple(_text(item, field) for item in values)
    if required and not normalized:
        raise StageRequirementError(f"{field} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise StageRequirementError(f"{field} contains duplicates")
    return tuple(sorted(normalized))


def _branch_dict(branch: BranchRef) -> dict[str, object]:
    return {
        "project_id": branch.run.project_id,
        "run_id": branch.run.run_id,
        "base": {
            "project_id": branch.run.base.project_id,
            "version": branch.run.base.version,
            "state_sha256": branch.run.base.require_digest(),
        },
        "branch_id": branch.branch_id,
        "epoch": branch.epoch,
    }


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _branch_from_dict(value: object) -> BranchRef:
    payload = _mapping(value, "branch")
    if set(payload) != {
        "project_id",
        "run_id",
        "base",
        "branch_id",
        "epoch",
    }:
        raise StageRequirementError("branch schema drifted")
    base_payload = _mapping(payload.get("base"), "branch.base")
    if set(base_payload) != {"project_id", "version", "state_sha256"}:
        raise StageRequirementError("branch base schema drifted")
    base = ProjectVersionRef(
        project_id=_text(base_payload.get("project_id"), "base.project_id"),
        version=base_payload.get("version"),
        state_sha256=require_sha256(
            base_payload.get("state_sha256"), "base.state_sha256"
        ),
    )
    return BranchRef(
        run=RunRef(
            project_id=_text(payload.get("project_id"), "branch.project_id"),
            run_id=_text(payload.get("run_id"), "branch.run_id"),
            base=base,
        ),
        branch_id=_text(payload.get("branch_id"), "branch.branch_id"),
        epoch=payload.get("epoch"),
    )


@dataclass(frozen=True, slots=True)
class StageCheckRequirement:
    """One mandatory check and the exact subjects it must cover."""

    requirement_id: str
    checker_id: str
    target_kind: RequirementTargetKind
    basis_mode: RequirementBasisMode
    denominator_refs: tuple[str, ...]
    required_claim_refs: tuple[str, ...] = ()
    required_applicability_refs: tuple[str, ...] = ()
    required_adoption_refs: tuple[str, ...] = ()
    required_source_refs: tuple[str, ...] = ()
    required_authority_refs: tuple[str, ...] = ()
    allow_not_applicable: bool = False

    SCHEMA = "StageCheckRequirement@1"

    def __post_init__(self) -> None:
        require_identifier(self.requirement_id, "requirement_id")
        require_identifier(self.checker_id, "checker_id")
        if not isinstance(self.target_kind, RequirementTargetKind):
            raise TypeError("target_kind must be RequirementTargetKind")
        if not isinstance(self.basis_mode, RequirementBasisMode):
            raise TypeError("basis_mode must be RequirementBasisMode")
        object.__setattr__(
            self,
            "denominator_refs",
            _refs(self.denominator_refs, "denominator_refs", required=True),
        )
        for field in (
            "required_claim_refs",
            "required_applicability_refs",
            "required_adoption_refs",
            "required_source_refs",
            "required_authority_refs",
        ):
            object.__setattr__(self, field, _refs(getattr(self, field), field))
        if type(self.allow_not_applicable) is not bool:
            raise TypeError("allow_not_applicable must be bool")

        if self.basis_mode is RequirementBasisMode.UNIVERSAL:
            if (
                self.required_claim_refs
                or self.required_applicability_refs
                or self.required_adoption_refs
                or self.required_source_refs
                or self.required_authority_refs
            ):
                raise StageRequirementError(
                    "universal requirement cannot require claim or authority refs"
                )
            if self.allow_not_applicable:
                raise StageRequirementError(
                    "universal requirement cannot be declared not applicable"
                )
        elif self.basis_mode is RequirementBasisMode.CLAIM_BOUND:
            if not all(
                (
                    self.required_claim_refs,
                    self.required_applicability_refs,
                    self.required_adoption_refs,
                    self.required_source_refs,
                    self.required_authority_refs,
                )
            ):
                raise StageRequirementError(
                    "claim-bound requirement needs claim, applicability, adoption, "
                    "source, and authority refs"
                )
        elif not self.required_authority_refs:
            raise StageRequirementError(
                "authority-bound requirement needs authority refs"
            )

    @property
    def requirement_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requirement_id": self.requirement_id,
            "checker_id": self.checker_id,
            "target_kind": self.target_kind.value,
            "basis_mode": self.basis_mode.value,
            "denominator_refs": list(self.denominator_refs),
            "required_claim_refs": list(self.required_claim_refs),
            "required_applicability_refs": list(
                self.required_applicability_refs
            ),
            "required_adoption_refs": list(self.required_adoption_refs),
            "required_source_refs": list(self.required_source_refs),
            "required_authority_refs": list(self.required_authority_refs),
            "allow_not_applicable": self.allow_not_applicable,
            "design_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageCheckRequirement":
        payload = _mapping(value, "requirement")
        expected = {
            "schema",
            "requirement_id",
            "checker_id",
            "target_kind",
            "basis_mode",
            "denominator_refs",
            "required_claim_refs",
            "required_applicability_refs",
            "required_adoption_refs",
            "required_source_refs",
            "required_authority_refs",
            "allow_not_applicable",
            "design_authority",
            "canonical_write_authority",
        }
        if set(payload) != expected or payload.get("schema") != cls.SCHEMA:
            raise StageRequirementError("unsupported requirement schema")
        if (
            payload.get("design_authority") is not False
            or payload.get("canonical_write_authority") is not False
        ):
            raise StageRequirementError("requirement authority flags changed")
        list_fields = (
            "denominator_refs",
            "required_claim_refs",
            "required_applicability_refs",
            "required_adoption_refs",
            "required_source_refs",
            "required_authority_refs",
        )
        if any(not isinstance(payload.get(field), list) for field in list_fields):
            raise TypeError("serialized requirement refs must be lists")
        return cls(
            requirement_id=_text(payload.get("requirement_id"), "requirement_id"),
            checker_id=_text(payload.get("checker_id"), "checker_id"),
            target_kind=RequirementTargetKind(payload.get("target_kind")),
            basis_mode=RequirementBasisMode(payload.get("basis_mode")),
            denominator_refs=tuple(payload.get("denominator_refs", ())),
            required_claim_refs=tuple(payload.get("required_claim_refs", ())),
            required_applicability_refs=tuple(
                payload.get("required_applicability_refs", ())
            ),
            required_adoption_refs=tuple(
                payload.get("required_adoption_refs", ())
            ),
            required_source_refs=tuple(
                payload.get("required_source_refs", ())
            ),
            required_authority_refs=tuple(
                payload.get("required_authority_refs", ())
            ),
            allow_not_applicable=payload.get("allow_not_applicable", False),
        )


@dataclass(frozen=True, slots=True)
class StageRequirementProfile:
    """Exact branch/scope-bound denominator for one stage closure."""

    profile_id: str
    typology_id: str
    stage_id: str
    branch: BranchRef
    predecessor_state_digest: str
    scope_digest: str
    stage_subject_ref: str
    requirements: tuple[StageCheckRequirement, ...]

    SCHEMA = "StageRequirementProfile@1"

    def __post_init__(self) -> None:
        require_identifier(self.profile_id, "profile_id")
        require_identifier(self.typology_id, "typology_id")
        require_identifier(self.stage_id, "stage_id")
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        object.__setattr__(
            self,
            "predecessor_state_digest",
            require_sha256(
                self.predecessor_state_digest,
                "predecessor_state_digest",
            ),
        )
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        require_logical_ref(self.stage_subject_ref, "stage_subject_ref")
        if not isinstance(self.requirements, tuple) or not self.requirements:
            raise StageRequirementError("requirements must be a non-empty tuple")
        if any(
            not isinstance(item, StageCheckRequirement)
            for item in self.requirements
        ):
            raise TypeError("requirements must contain StageCheckRequirement")
        ids = tuple(item.requirement_id for item in self.requirements)
        if len(ids) != len(set(ids)):
            raise StageRequirementError("requirements contain duplicate ids")
        object.__setattr__(
            self,
            "requirements",
            tuple(sorted(self.requirements, key=lambda item: item.requirement_id)),
        )

    @property
    def profile_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile_id": self.profile_id,
            "typology_id": self.typology_id,
            "stage_id": self.stage_id,
            "branch": _branch_dict(self.branch),
            "predecessor_state_digest": self.predecessor_state_digest,
            "scope_digest": self.scope_digest,
            "stage_subject_ref": self.stage_subject_ref,
            "requirements": [item.to_dict() for item in self.requirements],
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageRequirementProfile":
        payload = _mapping(value, "profile")
        expected = {
            "schema",
            "profile_id",
            "typology_id",
            "stage_id",
            "branch",
            "predecessor_state_digest",
            "scope_digest",
            "stage_subject_ref",
            "requirements",
            "stage_acceptance_authority",
            "canonical_write_authority",
        }
        if set(payload) != expected or payload.get("schema") != cls.SCHEMA:
            raise StageRequirementError("unsupported stage requirement schema")
        if (
            payload.get("stage_acceptance_authority") is not False
            or payload.get("canonical_write_authority") is not False
        ):
            raise StageRequirementError("profile authority flags changed")
        raw_requirements = payload.get("requirements")
        if not isinstance(raw_requirements, list):
            raise TypeError("requirements must be a list")
        return cls(
            profile_id=_text(payload.get("profile_id"), "profile_id"),
            typology_id=_text(payload.get("typology_id"), "typology_id"),
            stage_id=_text(payload.get("stage_id"), "stage_id"),
            branch=_branch_from_dict(payload.get("branch")),
            predecessor_state_digest=require_sha256(
                payload.get("predecessor_state_digest"),
                "predecessor_state_digest",
            ),
            scope_digest=require_sha256(
                payload.get("scope_digest"), "scope_digest"
            ),
            stage_subject_ref=_text(
                payload.get("stage_subject_ref"), "stage_subject_ref"
            ),
            requirements=tuple(
                StageCheckRequirement.from_dict(item)
                for item in raw_requirements
            ),
        )


__all__ = [
    "RequirementBasisMode",
    "RequirementTargetKind",
    "StageCheckRequirement",
    "StageRequirementError",
    "StageRequirementProfile",
]
