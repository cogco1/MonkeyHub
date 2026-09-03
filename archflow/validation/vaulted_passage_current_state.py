"""Controller-inspected current-state morphology gate for vaulted passages.

The structural vaulted-underpass checker proves support/load relationships.  It
does not prove that an exterior portal reaches a host opening, that the vault
is a continuous curved surface, or that different facades share the same
current state.  This module supplies that separate, project-neutral gate.

Agents may propose the typed contract below.  The inspection is deliberately
*not* part of the proposal: a controller must pass the ``ThreeDmInspection``
created from the immutable saved file.  All morphology decisions use geometry
analysis computed by that inspector, never object names or Agent-authored
claims.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import product
from typing import ClassVar

from archflow.adapters.three_dm_inspector import ThreeDmInspection
from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    finite_number,
    identifier,
    logical_ref,
)
from archflow.project.refs import BranchRef
from archflow.validation.cad_readback import CadBoundingBox
from archflow.validation.contracts import (
    CheckFinding,
    CheckMeasurement,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)


VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID = (
    "vaulted-passage-current-state-checker"
)
_OBJECT_REF_USER_STRING = "archflow:object_ref"
_OPERATION_REF_USER_STRING = "archflow:operation_ref"
_COORDINATE_LIMIT_METRES = 1.0e9
_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "promotion_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class VaultedPassageCurrentStateError(ValueError):
    """A current-state vaulted-passage contract is malformed."""


class VaultedPassageFacadeState(StrEnum):
    """Current state for one explicitly denominated facade."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PARKED = "PARKED"


class VaultedPassageStateBasis(StrEnum):
    """How one facade state was selected."""

    DIRECT_EVIDENCE = "direct_evidence"
    AUTHORIZED_PROPAGATION = "authorized_propagation"
    UNRESOLVED = "unresolved"


class VaultedPassageMorphologyRole(StrEnum):
    """Current-state geometry roles; structural roles are kept separate."""

    EXTERIOR_PORTAL = "exterior_portal"
    PASSAGE_VAULT = "passage_vault"
    HOST_OPENING = "host_opening"
    HISTORICAL_INFILL = "historical_infill"


_OPEN_ROLES = frozenset(
    {
        VaultedPassageMorphologyRole.EXTERIOR_PORTAL,
        VaultedPassageMorphologyRole.PASSAGE_VAULT,
        VaultedPassageMorphologyRole.HOST_OPENING,
    }
)
_CLOSED_ROLES = frozenset(VaultedPassageMorphologyRole)


def _optional_logical_ref(value: object, field: str) -> str | None:
    if value is None:
        return None
    return logical_ref(value, field)


def _finite_positive(value: object, field: str) -> float:
    result = float(finite_number(value, field))
    if result <= 0.0:
        raise VaultedPassageCurrentStateError(f"{field} must be positive")
    return result


def _point(value: object, field: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise VaultedPassageCurrentStateError(
            f"{field} must contain three coordinates"
        )
    result = tuple(float(finite_number(item, field)) for item in value)
    if any(abs(item) > _COORDINATE_LIMIT_METRES for item in result):
        raise VaultedPassageCurrentStateError(
            f"{field} exceeds the canonical metre coordinate limit"
        )
    return result[0], result[1], result[2]


def _facade_requirement_ref(facade_ref: str) -> str:
    return f"vaulted-passage-facade-state-required:{facade_ref}"


@dataclass(frozen=True, slots=True)
class VaultedPassageRoleBinding:
    """Bind one morphology role to an exact saved-CAD identity."""

    role: VaultedPassageMorphologyRole
    component_ref: str
    readback_object_ref: str
    readback_operation_ref: str

    SCHEMA: ClassVar[str] = "VaultedPassageRoleBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, VaultedPassageMorphologyRole):
            raise TypeError("role must be VaultedPassageMorphologyRole")
        for field in (
            "component_ref",
            "readback_object_ref",
            "readback_operation_ref",
        ):
            object.__setattr__(
                self,
                field,
                logical_ref(getattr(self, field), f"vaulted passage {field}"),
            )

    @property
    def ref(self) -> str:
        return (
            f"vaulted-passage-role:{self.role.value}:"
            f"{canonical_digest(self.to_dict())}"
        )

    @property
    def identity_pair(self) -> tuple[str, str]:
        return self.readback_object_ref, self.readback_operation_ref

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "component_ref": self.component_ref,
            "readback_object_ref": self.readback_object_ref,
            "readback_operation_ref": self.readback_operation_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedPassageRoleBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "component_ref",
                "readback_object_ref",
                "readback_operation_ref",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-passage role binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedPassageCurrentStateError(
                "unsupported vaulted-passage role-binding schema"
            )
        result = cls(
            role=VaultedPassageMorphologyRole(payload["role"]),
            component_ref=payload["component_ref"],
            readback_object_ref=payload["readback_object_ref"],
            readback_operation_ref=payload["readback_operation_ref"],
        )
        if result.to_dict() != payload:
            raise VaultedPassageCurrentStateError(
                "vaulted-passage role binding identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class VaultedPassagePathBinding:
    """Bind one complete portal-to-host curve in the saved 3DM."""

    component_ref: str
    readback_object_ref: str
    readback_operation_ref: str

    SCHEMA: ClassVar[str] = "VaultedPassagePathBinding@1"

    def __post_init__(self) -> None:
        for field in (
            "component_ref",
            "readback_object_ref",
            "readback_operation_ref",
        ):
            object.__setattr__(
                self,
                field,
                logical_ref(getattr(self, field), f"vaulted passage path {field}"),
            )

    @property
    def ref(self) -> str:
        return f"vaulted-passage-path:{canonical_digest(self.to_dict())}"

    @property
    def identity_pair(self) -> tuple[str, str]:
        return self.readback_object_ref, self.readback_operation_ref

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_ref": self.component_ref,
            "readback_object_ref": self.readback_object_ref,
            "readback_operation_ref": self.readback_operation_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedPassagePathBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "component_ref",
                "readback_object_ref",
                "readback_operation_ref",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-passage path binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedPassageCurrentStateError(
                "unsupported vaulted-passage path-binding schema"
            )
        result = cls(
            component_ref=payload["component_ref"],
            readback_object_ref=payload["readback_object_ref"],
            readback_operation_ref=payload["readback_operation_ref"],
        )
        if result.to_dict() != payload:
            raise VaultedPassageCurrentStateError(
                "vaulted-passage path binding identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class VaultedPassageFacadeRecord:
    """One explicit facade state; omission never means symmetric inheritance."""

    facade_ref: str
    state: VaultedPassageFacadeState
    state_basis: VaultedPassageStateBasis
    state_evidence_refs: tuple[str, ...] = ()
    state_adoption_refs: tuple[str, ...] = ()
    propagated_from_facade_ref: str | None = None
    propagation_evidence_refs: tuple[str, ...] = ()
    role_bindings: tuple[VaultedPassageRoleBinding, ...] = ()
    path_binding: VaultedPassagePathBinding | None = None

    SCHEMA: ClassVar[str] = "VaultedPassageFacadeRecord@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "facade_ref",
            logical_ref(self.facade_ref, "vaulted passage facade_ref"),
        )
        if not isinstance(self.state, VaultedPassageFacadeState):
            raise TypeError("state must be VaultedPassageFacadeState")
        if not isinstance(self.state_basis, VaultedPassageStateBasis):
            raise TypeError("state_basis must be VaultedPassageStateBasis")
        for field in (
            "state_evidence_refs",
            "state_adoption_refs",
            "propagation_evidence_refs",
        ):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    f"vaulted passage facade {field}",
                    allow_empty=True,
                ),
            )
        object.__setattr__(
            self,
            "propagated_from_facade_ref",
            _optional_logical_ref(
                self.propagated_from_facade_ref,
                "vaulted passage propagated_from_facade_ref",
            ),
        )
        if not isinstance(self.role_bindings, tuple) or any(
            not isinstance(item, VaultedPassageRoleBinding)
            for item in self.role_bindings
        ):
            raise TypeError(
                "role_bindings must contain VaultedPassageRoleBinding"
            )
        bindings = tuple(sorted(self.role_bindings, key=lambda item: item.role.value))
        roles = tuple(item.role for item in bindings)
        if len(roles) != len(set(roles)):
            raise VaultedPassageCurrentStateError(
                "facade role_bindings repeat a morphology role"
            )
        pairs = tuple(item.identity_pair for item in bindings)
        if len(pairs) != len(set(pairs)):
            raise VaultedPassageCurrentStateError(
                "facade role_bindings repeat a saved-CAD identity"
            )
        object.__setattr__(self, "role_bindings", bindings)
        if self.path_binding is not None and not isinstance(
            self.path_binding,
            VaultedPassagePathBinding,
        ):
            raise TypeError(
                "path_binding must be VaultedPassagePathBinding or None"
            )
        if self.path_binding is not None and self.path_binding.identity_pair in set(
            pairs
        ):
            raise VaultedPassageCurrentStateError(
                "path and morphology roles must have distinct saved-CAD identities"
            )

    @property
    def ref(self) -> str:
        return (
            f"vaulted-passage-facade:{self.facade_ref}:"
            f"{canonical_digest(self.to_dict())}"
        )

    @property
    def bindings_by_role(
        self,
    ) -> dict[VaultedPassageMorphologyRole, VaultedPassageRoleBinding]:
        return {item.role: item for item in self.role_bindings}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "facade_ref": self.facade_ref,
            "state": self.state.value,
            "state_basis": self.state_basis.value,
            "state_evidence_refs": list(self.state_evidence_refs),
            "state_adoption_refs": list(self.state_adoption_refs),
            "propagated_from_facade_ref": self.propagated_from_facade_ref,
            "propagation_evidence_refs": list(self.propagation_evidence_refs),
            "role_bindings": [item.to_dict() for item in self.role_bindings],
            "path_binding": (
                None if self.path_binding is None else self.path_binding.to_dict()
            ),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedPassageFacadeRecord":
        payload = exact_mapping(
            value,
            {
                "schema",
                "facade_ref",
                "state",
                "state_basis",
                "state_evidence_refs",
                "state_adoption_refs",
                "propagated_from_facade_ref",
                "propagation_evidence_refs",
                "role_bindings",
                "path_binding",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-passage facade record",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedPassageCurrentStateError(
                "unsupported vaulted-passage facade schema"
            )
        for field in (
            "state_evidence_refs",
            "state_adoption_refs",
            "propagation_evidence_refs",
            "role_bindings",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"vaulted-passage facade {field} must be a list")
        result = cls(
            facade_ref=payload["facade_ref"],
            state=VaultedPassageFacadeState(payload["state"]),
            state_basis=VaultedPassageStateBasis(payload["state_basis"]),
            state_evidence_refs=tuple(payload["state_evidence_refs"]),
            state_adoption_refs=tuple(payload["state_adoption_refs"]),
            propagated_from_facade_ref=payload["propagated_from_facade_ref"],
            propagation_evidence_refs=tuple(payload["propagation_evidence_refs"]),
            role_bindings=tuple(
                VaultedPassageRoleBinding.from_dict(item)
                for item in payload["role_bindings"]
            ),
            path_binding=(
                None
                if payload["path_binding"] is None
                else VaultedPassagePathBinding.from_dict(payload["path_binding"])
            ),
        )
        if result.to_dict() != payload:
            raise VaultedPassageCurrentStateError(
                "vaulted-passage facade identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class VaultedPassageMorphologyCriteria:
    """Authority-selected geometric tolerances for the generic gate."""

    criteria_ref: str
    interface_tolerance: float
    minimum_section_overlap_ratio: float
    minimum_host_cut_depth: float
    maximum_path_sample_gap: float
    minimum_path_samples: int
    evidence_refs: tuple[str, ...]
    adoption_refs: tuple[str, ...]

    SCHEMA: ClassVar[str] = "VaultedPassageMorphologyCriteria@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "criteria_ref",
            logical_ref(self.criteria_ref, "vaulted passage criteria_ref"),
        )
        for field in (
            "interface_tolerance",
            "minimum_host_cut_depth",
            "maximum_path_sample_gap",
        ):
            object.__setattr__(
                self,
                field,
                _finite_positive(getattr(self, field), field),
            )
        overlap = float(
            finite_number(
                self.minimum_section_overlap_ratio,
                "minimum_section_overlap_ratio",
            )
        )
        if overlap <= 0.0 or overlap > 1.0:
            raise VaultedPassageCurrentStateError(
                "minimum_section_overlap_ratio must be in (0, 1]"
            )
        object.__setattr__(self, "minimum_section_overlap_ratio", overlap)
        if (
            isinstance(self.minimum_path_samples, bool)
            or not isinstance(self.minimum_path_samples, int)
            or self.minimum_path_samples < 3
        ):
            raise VaultedPassageCurrentStateError(
                "minimum_path_samples must be an integer of at least three"
            )
        for field in ("evidence_refs", "adoption_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    f"vaulted passage criteria {field}",
                    allow_empty=True,
                ),
            )

    @property
    def ref(self) -> str:
        return (
            f"vaulted-passage-criteria:{self.criteria_ref}:"
            f"{canonical_digest(self.to_dict())}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "criteria_ref": self.criteria_ref,
            "interface_tolerance": self.interface_tolerance,
            "minimum_section_overlap_ratio": self.minimum_section_overlap_ratio,
            "minimum_host_cut_depth": self.minimum_host_cut_depth,
            "maximum_path_sample_gap": self.maximum_path_sample_gap,
            "minimum_path_samples": self.minimum_path_samples,
            "evidence_refs": list(self.evidence_refs),
            "adoption_refs": list(self.adoption_refs),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedPassageMorphologyCriteria":
        payload = exact_mapping(
            value,
            {
                "schema",
                "criteria_ref",
                "interface_tolerance",
                "minimum_section_overlap_ratio",
                "minimum_host_cut_depth",
                "maximum_path_sample_gap",
                "minimum_path_samples",
                "evidence_refs",
                "adoption_refs",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-passage morphology criteria",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedPassageCurrentStateError(
                "unsupported vaulted-passage criteria schema"
            )
        for field in ("evidence_refs", "adoption_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"vaulted-passage criteria {field} must be a list")
        result = cls(
            criteria_ref=payload["criteria_ref"],
            interface_tolerance=payload["interface_tolerance"],
            minimum_section_overlap_ratio=payload[
                "minimum_section_overlap_ratio"
            ],
            minimum_host_cut_depth=payload["minimum_host_cut_depth"],
            maximum_path_sample_gap=payload["maximum_path_sample_gap"],
            minimum_path_samples=payload["minimum_path_samples"],
            evidence_refs=tuple(payload["evidence_refs"]),
            adoption_refs=tuple(payload["adoption_refs"]),
        )
        if result.to_dict() != payload:
            raise VaultedPassageCurrentStateError(
                "vaulted-passage criteria identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class VaultedPassageCurrentStateContract:
    """Exact per-facade denominator bound to one controller inspection."""

    contract_id: str
    branch: BranchRef
    scope_digest: str
    stage_subject_digest: str
    inspection_artifact_ref: str
    inspection_model_sha256: str
    inspection_digest: str
    expected_facade_refs: tuple[str, ...]
    facades: tuple[VaultedPassageFacadeRecord, ...]
    criteria: VaultedPassageMorphologyCriteria

    SCHEMA: ClassVar[str] = "VaultedPassageCurrentStateContract@1"

    def __post_init__(self) -> None:
        identifier(self.contract_id, "vaulted passage contract_id")
        require_exact_branch(self.branch)
        for field in (
            "scope_digest",
            "stage_subject_digest",
            "inspection_model_sha256",
            "inspection_digest",
        ):
            object.__setattr__(
                self,
                field,
                require_sha256(getattr(self, field), f"vaulted passage {field}"),
            )
        object.__setattr__(
            self,
            "inspection_artifact_ref",
            logical_ref(
                self.inspection_artifact_ref,
                "vaulted passage inspection_artifact_ref",
            ),
        )
        object.__setattr__(
            self,
            "expected_facade_refs",
            deterministic_refs(
                self.expected_facade_refs,
                "vaulted passage expected_facade_refs",
            ),
        )
        if not isinstance(self.facades, tuple) or any(
            not isinstance(item, VaultedPassageFacadeRecord)
            for item in self.facades
        ):
            raise TypeError(
                "facades must contain VaultedPassageFacadeRecord"
            )
        facades = tuple(sorted(self.facades, key=lambda item: item.facade_ref))
        refs = tuple(item.facade_ref for item in facades)
        if len(refs) != len(set(refs)):
            raise VaultedPassageCurrentStateError(
                "facades repeat a facade_ref"
            )
        object.__setattr__(self, "facades", facades)
        if not isinstance(self.criteria, VaultedPassageMorphologyCriteria):
            raise TypeError("criteria must be VaultedPassageMorphologyCriteria")

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return (
            f"vaulted-passage-current-state:{self.contract_id}:"
            f"{self.contract_digest}"
        )

    @property
    def denominator_refs(self) -> tuple[str, ...]:
        refs = {
            self.ref,
            self.criteria.ref,
            self.criteria.criteria_ref,
            self.inspection_artifact_ref,
            f"three-dm-file-sha256:{self.inspection_model_sha256}",
            f"three-dm-inspection:{self.inspection_digest}",
            *(_facade_requirement_ref(item) for item in self.expected_facade_refs),
        }
        for facade in self.facades:
            refs.update((facade.ref, facade.facade_ref))
            if facade.propagated_from_facade_ref is not None:
                refs.add(facade.propagated_from_facade_ref)
            for binding in facade.role_bindings:
                refs.update(
                    (
                        binding.ref,
                        binding.component_ref,
                        binding.readback_object_ref,
                        binding.readback_operation_ref,
                    )
                )
            if facade.path_binding is not None:
                refs.update(
                    (
                        facade.path_binding.ref,
                        facade.path_binding.component_ref,
                        facade.path_binding.readback_object_ref,
                        facade.path_binding.readback_operation_ref,
                    )
                )
        return tuple(sorted(refs))

    @property
    def source_refs(self) -> tuple[str, ...]:
        refs = set(self.criteria.evidence_refs)
        for facade in self.facades:
            refs.update(facade.state_evidence_refs)
            refs.update(facade.propagation_evidence_refs)
        return tuple(sorted(refs))

    @property
    def adoption_refs(self) -> tuple[str, ...]:
        refs = set(self.criteria.adoption_refs)
        for facade in self.facades:
            refs.update(facade.state_adoption_refs)
        return tuple(sorted(refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "contract_id": self.contract_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "inspection_artifact_ref": self.inspection_artifact_ref,
            "inspection_model_sha256": self.inspection_model_sha256,
            "inspection_digest": self.inspection_digest,
            "expected_facade_refs": list(self.expected_facade_refs),
            "facades": [item.to_dict() for item in self.facades],
            "criteria": self.criteria.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedPassageCurrentStateContract":
        payload = exact_mapping(
            value,
            {
                "schema",
                "contract_id",
                "branch",
                "scope_digest",
                "stage_subject_digest",
                "inspection_artifact_ref",
                "inspection_model_sha256",
                "inspection_digest",
                "expected_facade_refs",
                "facades",
                "criteria",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-passage current-state contract",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedPassageCurrentStateError(
                "unsupported vaulted-passage current-state schema"
            )
        for field in ("expected_facade_refs", "facades"):
            if not isinstance(payload[field], list):
                raise TypeError(f"vaulted-passage contract {field} must be a list")
        result = cls(
            contract_id=payload["contract_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            inspection_artifact_ref=payload["inspection_artifact_ref"],
            inspection_model_sha256=payload["inspection_model_sha256"],
            inspection_digest=payload["inspection_digest"],
            expected_facade_refs=tuple(payload["expected_facade_refs"]),
            facades=tuple(
                VaultedPassageFacadeRecord.from_dict(item)
                for item in payload["facades"]
            ),
            criteria=VaultedPassageMorphologyCriteria.from_dict(
                payload["criteria"]
            ),
        )
        if result.to_dict() != payload:
            raise VaultedPassageCurrentStateError(
                "vaulted-passage current-state identity changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class _InspectedObject:
    object_id: str
    identity_pair: tuple[str, str]
    bounds: CadBoundingBox
    analysis: dict[str, object]


def _finding(
    contract: VaultedPassageCurrentStateContract,
    code: str,
    severity: FindingSeverity,
    message: str,
    *subject_refs: str,
) -> CheckFinding:
    return CheckFinding(
        code=code,
        severity=severity,
        message=message,
        subject_refs=tuple(sorted(set(subject_refs or (contract.ref,)))),
        evidence_refs=contract.source_refs,
    )


def _measurement(
    measurement_id: str,
    subject_ref: str,
    name: str,
    value: float | int,
    unit_ref: str,
    evidence_refs: tuple[str, ...],
) -> CheckMeasurement:
    # Facade refs are logical refs (for example ``facade:south-west``), while
    # CheckMeasurement ids are portable identifiers.  Hash the descriptive
    # seed instead of leaking ref punctuation or unbounded lengths into the id.
    portable_id = (
        "vaulted-passage-measurement-"
        + canonical_digest({"seed": measurement_id})[:32]
    )
    return CheckMeasurement(
        measurement_id=portable_id,
        subject_ref=subject_ref,
        name=name,
        value=value,
        unit_ref=unit_ref,
        evidence_refs=evidence_refs,
    )


def _rows_by_object_id(
    rows: tuple[dict[str, object], ...],
    label: str,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("object_id"), str):
            raise VaultedPassageCurrentStateError(
                f"3dm {label} row is malformed"
            )
        object_id = row["object_id"]
        if object_id in result:
            raise VaultedPassageCurrentStateError(
                f"3dm {label} repeats object_id"
            )
        result[object_id] = row
    return result


def _user_strings(row: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in ("attributes", "geometry"):
        raw = row.get(field)
        if not isinstance(raw, (list, tuple)):
            raise VaultedPassageCurrentStateError(
                f"3dm object {field} user strings are malformed"
            )
        for item in raw:
            if (
                not isinstance(item, dict)
                or set(item) != {"key", "value"}
                or not isinstance(item["key"], str)
                or not isinstance(item["value"], str)
            ):
                raise VaultedPassageCurrentStateError(
                    "3dm object user string is malformed"
                )
            if item["key"] in result and result[item["key"]] != item["value"]:
                raise VaultedPassageCurrentStateError(
                    "3dm object user-string identity conflicts"
                )
            result[item["key"]] = item["value"]
    return result


def _bbox(value: object) -> CadBoundingBox:
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise VaultedPassageCurrentStateError("3dm object bbox is malformed")
    minimum = _point(value["min"], "bbox minimum")
    maximum = _point(value["max"], "bbox maximum")
    return CadBoundingBox(minimum=minimum, maximum=maximum)


def _inspection_object_universe(
    contract: VaultedPassageCurrentStateContract,
    inspection: ThreeDmInspection,
    findings: list[CheckFinding],
) -> tuple[dict[tuple[str, str], _InspectedObject], tuple[str, ...]]:
    try:
        geometry_by_id = _rows_by_object_id(
            inspection.object_geometry_sha256,
            "geometry-digest",
        )
        analysis_by_id = _rows_by_object_id(
            inspection.object_geometry_analysis,
            "geometry-analysis",
        )
        bbox_by_id = _rows_by_object_id(
            inspection.named_object_bboxes,
            "named-bbox",
        )
        identities: dict[tuple[str, str], list[str]] = {}
        for row in inspection.object_user_strings:
            if not isinstance(row, dict) or not isinstance(row.get("object_id"), str):
                raise VaultedPassageCurrentStateError(
                    "3dm object identity row is malformed"
                )
            strings = _user_strings(row)
            has_object = _OBJECT_REF_USER_STRING in strings
            has_operation = _OPERATION_REF_USER_STRING in strings
            if not has_object and not has_operation:
                continue
            if not has_object or not has_operation:
                raise VaultedPassageCurrentStateError(
                    "3dm identity requires object and operation refs"
                )
            pair = (
                logical_ref(strings[_OBJECT_REF_USER_STRING], "3dm object_ref"),
                logical_ref(
                    strings[_OPERATION_REF_USER_STRING],
                    "3dm operation_ref",
                ),
            )
            identities.setdefault(pair, []).append(row["object_id"])
    except (TypeError, ValueError, VaultedPassageCurrentStateError) as exc:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-inspection-malformed",
                FindingSeverity.ERROR,
                f"controller 3dm inspection cannot be replayed: {exc}",
                contract.inspection_artifact_ref,
            )
        )
        return {}, ()

    required_pairs = {
        binding.identity_pair
        for facade in contract.facades
        for binding in facade.role_bindings
    }
    required_pairs.update(
        facade.path_binding.identity_pair
        for facade in contract.facades
        if facade.path_binding is not None
    )
    result: dict[tuple[str, str], _InspectedObject] = {}
    inspection_refs: set[str] = set()
    for pair in sorted(required_pairs):
        object_ids = identities.get(pair, [])
        if len(object_ids) != 1:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-inspected-identity-contradiction",
                    FindingSeverity.ERROR,
                    "saved 3dm must contain exactly one object for each bound identity",
                    *pair,
                )
            )
            continue
        object_id = object_ids[0]
        geometry = geometry_by_id.get(object_id)
        analysis = analysis_by_id.get(object_id)
        bbox_row = bbox_by_id.get(object_id)
        try:
            if geometry is None or analysis is None or bbox_row is None:
                raise VaultedPassageCurrentStateError(
                    "bound object lacks digest, analysis, or named bounds"
                )
            geometry_sha = require_sha256(
                geometry.get("geometry_sha256"),
                "inspected geometry_sha256",
            )
            analysis_sha = require_sha256(
                analysis.get("geometry_sha256"),
                "analyzed geometry_sha256",
            )
            if geometry_sha != analysis_sha:
                raise VaultedPassageCurrentStateError(
                    "geometry analysis is not bound to encoded geometry"
                )
            if (
                geometry.get("type") != analysis.get("type")
                or geometry.get("layer_path") != analysis.get("layer_path")
                or bool(analysis.get("is_instance_definition_object"))
            ):
                raise VaultedPassageCurrentStateError(
                    "geometry analysis crossed type, layer, or instance scope"
                )
            bounds = _bbox(bbox_row.get("bbox"))
        except (TypeError, ValueError, VaultedPassageCurrentStateError) as exc:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-inspected-geometry-contradiction",
                    FindingSeverity.ERROR,
                    f"saved geometry cannot support morphology replay: {exc}",
                    *pair,
                )
            )
            continue
        result[pair] = _InspectedObject(
            object_id=object_id,
            identity_pair=pair,
            bounds=bounds,
            analysis=analysis,
        )
        inspection_refs.update(
            (
                f"three-dm-object-id:{object_id}",
                f"three-dm-geometry-sha256:{geometry_sha}",
            )
        )
    return result, tuple(sorted(inspection_refs))


def _inside(
    bounds: CadBoundingBox,
    point: tuple[float, float, float],
    tolerance: float,
) -> bool:
    return all(
        low - tolerance <= coordinate <= high + tolerance
        for low, high, coordinate in zip(bounds.minimum, bounds.maximum, point)
    )


def _bounds_overlap(
    first: CadBoundingBox,
    second: CadBoundingBox,
    tolerance: float,
) -> bool:
    return all(
        first.minimum[index] <= second.maximum[index] + tolerance
        and second.minimum[index] <= first.maximum[index] + tolerance
        for index in range(3)
    )


def _center(bounds: CadBoundingBox) -> tuple[float, float, float]:
    return tuple(
        (bounds.minimum[index] + bounds.maximum[index]) / 2.0
        for index in range(3)
    )


def _sub(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(first[index] - second[index] for index in range(3))


def _dot(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> float:
    return sum(first[index] * second[index] for index in range(3))


def _cross(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _unit(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(value, value))
    if length <= 1.0e-12:
        raise VaultedPassageCurrentStateError(
            "passage axis must have non-zero length"
        )
    return tuple(item / length for item in value)


def _bbox_projection(
    bounds: CadBoundingBox,
    direction: tuple[float, float, float],
) -> tuple[float, float]:
    values = [
        _dot(point, direction)
        for point in product(
            (bounds.minimum[0], bounds.maximum[0]),
            (bounds.minimum[1], bounds.maximum[1]),
            (bounds.minimum[2], bounds.maximum[2]),
        )
    ]
    return min(values), max(values)


def _interval_overlap_ratio(
    first: tuple[float, float],
    second: tuple[float, float],
    tolerance: float,
) -> float:
    first_length = first[1] - first[0]
    second_length = second[1] - second[0]
    denominator = max(first_length, second_length)
    if denominator <= tolerance:
        return 0.0
    overlap = max(
        0.0,
        min(first[1], second[1]) - max(first[0], second[0]),
    )
    return min(1.0, (overlap + tolerance) / denominator)


def _transverse_axes(
    axis: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    helper = (0.0, 0.0, 1.0)
    if abs(_dot(axis, helper)) > 0.9:
        helper = (1.0, 0.0, 0.0)
    first = _unit(_cross(axis, helper))
    return first, _unit(_cross(axis, first))


def _integer_analysis(
    analysis: dict[str, object],
    field: str,
) -> int | None:
    value = analysis.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VaultedPassageCurrentStateError(
            f"geometry analysis {field} is malformed"
        )
    return value


def _check_curved_vault(
    contract: VaultedPassageCurrentStateContract,
    facade: VaultedPassageFacadeRecord,
    vault_binding: VaultedPassageRoleBinding,
    vault: _InspectedObject,
    findings: list[CheckFinding],
) -> None:
    try:
        geometry_type = vault.analysis.get("type")
        face_count = _integer_analysis(vault.analysis, "face_count")
        planar = _integer_analysis(vault.analysis, "planar_face_count")
        curved = _integer_analysis(vault.analysis, "curved_face_count")
        faceted = geometry_type == "Mesh"
        lacks_curvature = (
            face_count is None
            or face_count <= 0
            or curved is None
            or curved <= 0
            or planar is None
        )
    except VaultedPassageCurrentStateError as exc:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-vault-analysis-contradiction",
                FindingSeverity.ERROR,
                str(exc),
                facade.facade_ref,
                vault_binding.ref,
            )
        )
        return
    if faceted or lacks_curvature:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-faceted-planar-shell",
                FindingSeverity.ERROR,
                "a mesh or all-planar face denominator cannot prove a continuous vault",
                facade.facade_ref,
                vault_binding.ref,
            )
        )
    if vault.analysis.get("is_manifold") is False:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-vault-nonmanifold",
                FindingSeverity.ERROR,
                "passage vault geometry is non-manifold",
                facade.facade_ref,
                vault_binding.ref,
            )
        )


def _check_solid_opening(
    contract: VaultedPassageCurrentStateContract,
    facade: VaultedPassageFacadeRecord,
    binding: VaultedPassageRoleBinding,
    observed: _InspectedObject,
    findings: list[CheckFinding],
) -> None:
    if (
        observed.analysis.get("is_solid") is not True
        or observed.analysis.get("is_closed") is not True
    ):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-opening-cutter-not-solid",
                FindingSeverity.ERROR,
                f"{binding.role.value} must be a closed solid cut witness",
                facade.facade_ref,
                binding.ref,
            )
        )


def _section_ratios(
    vault: CadBoundingBox,
    opening: CadBoundingBox,
    axis: tuple[float, float, float],
    tolerance: float,
) -> tuple[float, float]:
    transverse = _transverse_axes(axis)
    return tuple(
        _interval_overlap_ratio(
            _bbox_projection(vault, direction),
            _bbox_projection(opening, direction),
            tolerance,
        )
        for direction in transverse
    )


def _check_section_and_host_cut(
    contract: VaultedPassageCurrentStateContract,
    facade: VaultedPassageFacadeRecord,
    portal_binding: VaultedPassageRoleBinding,
    portal: _InspectedObject,
    vault_binding: VaultedPassageRoleBinding,
    vault: _InspectedObject,
    host_binding: VaultedPassageRoleBinding,
    host: _InspectedObject,
    axis: tuple[float, float, float],
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
) -> None:
    criteria = contract.criteria
    tolerance = criteria.interface_tolerance
    for label, opening_binding, opening in (
        ("portal", portal_binding, portal),
        ("host", host_binding, host),
    ):
        ratios = _section_ratios(vault.bounds, opening.bounds, axis, tolerance)
        for index, ratio in enumerate(ratios):
            measurements.append(
                _measurement(
                    f"{facade.facade_ref}-{label}-section-overlap-{index}",
                    opening_binding.ref,
                    f"{label}_section_overlap_ratio_{index}",
                    ratio,
                    "unit:ratio",
                    facade.state_evidence_refs,
                )
            )
        if any(
            ratio + 1.0e-12 < criteria.minimum_section_overlap_ratio
            for ratio in ratios
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-section-mismatch",
                    FindingSeverity.ERROR,
                    f"{label} section does not continuously fit the passage vault",
                    facade.facade_ref,
                    opening_binding.ref,
                    vault_binding.ref,
                )
            )
        if not _bounds_overlap(vault.bounds, opening.bounds, tolerance):
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-section-interface-disconnected",
                    FindingSeverity.ERROR,
                    f"{label} does not meet the passage-vault bounds",
                    facade.facade_ref,
                    opening_binding.ref,
                    vault_binding.ref,
                )
            )

    host_interval = _bbox_projection(host.bounds, axis)
    host_depth = host_interval[1] - host_interval[0]
    measurements.append(
        _measurement(
            f"{facade.facade_ref}-host-cut-depth",
            host_binding.ref,
            "host_cut_depth",
            host_depth,
            "unit:meter",
            facade.state_evidence_refs,
        )
    )
    if host_depth + tolerance < criteria.minimum_host_cut_depth:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-host-cut-insufficient",
                FindingSeverity.ERROR,
                "host opening lacks adopted through-cut depth",
                facade.facade_ref,
                host_binding.ref,
            )
        )


def _curve_samples(analysis: dict[str, object]) -> tuple[tuple[float, float, float], ...]:
    values = analysis.get("curve_samples")
    if not isinstance(values, list):
        raise VaultedPassageCurrentStateError(
            "path curve samples are unavailable"
        )
    return tuple(_point(item, "path curve sample") for item in values)


def _check_full_path(
    contract: VaultedPassageCurrentStateContract,
    facade: VaultedPassageFacadeRecord,
    path_binding: VaultedPassagePathBinding,
    path: _InspectedObject,
    portal_binding: VaultedPassageRoleBinding,
    portal: _InspectedObject,
    vault_binding: VaultedPassageRoleBinding,
    vault: _InspectedObject,
    host_binding: VaultedPassageRoleBinding,
    host: _InspectedObject,
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
) -> None:
    criteria = contract.criteria
    tolerance = criteria.interface_tolerance
    try:
        if path.analysis.get("type") != "Curve":
            raise VaultedPassageCurrentStateError(
                "full-path witness must be one saved curve"
            )
        if path.analysis.get("curve_closed") is not False:
            raise VaultedPassageCurrentStateError(
                "portal-to-host path must be an open curve"
            )
        samples = _curve_samples(path.analysis)
        start = _point(path.analysis.get("curve_start"), "path curve start")
        end = _point(path.analysis.get("curve_end"), "path curve end")
        if len(samples) < criteria.minimum_path_samples:
            raise VaultedPassageCurrentStateError(
                "path sample denominator is too small"
            )
        if math.dist(start, samples[0]) > tolerance or math.dist(
            end, samples[-1]
        ) > tolerance:
            raise VaultedPassageCurrentStateError(
                "path samples omit exact saved-curve endpoints"
            )
    except (TypeError, ValueError, VaultedPassageCurrentStateError) as exc:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-full-path-analysis-contradiction",
                FindingSeverity.ERROR,
                str(exc),
                facade.facade_ref,
                path_binding.ref,
            )
        )
        return

    forward = _inside(portal.bounds, start, tolerance) and _inside(
        host.bounds, end, tolerance
    )
    reverse = _inside(portal.bounds, end, tolerance) and _inside(
        host.bounds, start, tolerance
    )
    if reverse and not forward:
        samples = tuple(reversed(samples))
        start, end = end, start
        forward = True
    if not forward:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-full-path-endpoints-disconnected",
                FindingSeverity.ERROR,
                "one complete path must begin at the exterior portal and end in the host opening",
                facade.facade_ref,
                path_binding.ref,
                portal_binding.ref,
                host_binding.ref,
            )
        )
        if _inside(portal.bounds, start, tolerance) and not _inside(
            host.bounds,
            end,
            tolerance,
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-blind-end",
                    FindingSeverity.ERROR,
                    "passage path stops before reaching the host opening",
                    facade.facade_ref,
                    path_binding.ref,
                    host_binding.ref,
                )
            )
        return

    try:
        axis = _unit(_sub(end, start))
    except VaultedPassageCurrentStateError as exc:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-full-path-zero-span",
                FindingSeverity.ERROR,
                str(exc),
                facade.facade_ref,
                path_binding.ref,
            )
        )
        return

    if not all(_inside(vault.bounds, item, tolerance) for item in samples):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-full-path-leaves-vault",
                FindingSeverity.ERROR,
                "saved path samples leave the inspected passage-vault envelope",
                facade.facade_ref,
                path_binding.ref,
                vault_binding.ref,
            )
        )
    projections = [_dot(_sub(item, start), axis) for item in samples]
    if any(
        later + tolerance < earlier
        for earlier, later in zip(projections, projections[1:])
    ):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-full-path-backtracks",
                FindingSeverity.ERROR,
                "saved path backtracks instead of continuously reaching the host",
                facade.facade_ref,
                path_binding.ref,
            )
        )
    gaps = tuple(
        math.dist(first, second)
        for first, second in zip(samples, samples[1:])
    )
    maximum_gap = max(gaps, default=0.0)
    sampled_length = sum(gaps)
    measurements.extend(
        (
            _measurement(
                f"{facade.facade_ref}-path-sampled-length",
                path_binding.ref,
                "full_path_sampled_length",
                sampled_length,
                "unit:meter",
                facade.state_evidence_refs,
            ),
            _measurement(
                f"{facade.facade_ref}-path-maximum-gap",
                path_binding.ref,
                "full_path_maximum_sample_gap",
                maximum_gap,
                "unit:meter",
                facade.state_evidence_refs,
            ),
        )
    )
    if maximum_gap > criteria.maximum_path_sample_gap + tolerance:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-full-path-sampling-gap",
                FindingSeverity.ERROR,
                "path coverage contains a gap larger than the adopted maximum",
                facade.facade_ref,
                path_binding.ref,
            )
        )

    if not _inside(vault.bounds, end, tolerance) or not _bounds_overlap(
        vault.bounds,
        host.bounds,
        tolerance,
    ):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-blind-end",
                FindingSeverity.ERROR,
                "passage terminates before a connected host opening",
                facade.facade_ref,
                path_binding.ref,
                host_binding.ref,
                vault_binding.ref,
            )
        )

    _check_section_and_host_cut(
        contract,
        facade,
        portal_binding,
        portal,
        vault_binding,
        vault,
        host_binding,
        host,
        axis,
        findings,
        measurements,
    )


def _check_facade_state_basis(
    contract: VaultedPassageCurrentStateContract,
    facade: VaultedPassageFacadeRecord,
    facades_by_ref: dict[str, VaultedPassageFacadeRecord],
    findings: list[CheckFinding],
) -> None:
    if facade.state is VaultedPassageFacadeState.PARKED:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-facade-state-parked",
                FindingSeverity.UNKNOWN,
                "facade current state remains explicitly parked",
                facade.facade_ref,
            )
        )
        if facade.state_basis is not VaultedPassageStateBasis.UNRESOLVED:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-parked-basis-contradiction",
                    FindingSeverity.ERROR,
                    "PARKED facade must retain an unresolved state basis",
                    facade.facade_ref,
                )
            )
        return

    if not facade.state_evidence_refs:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-facade-state-evidence-unknown",
                FindingSeverity.UNKNOWN,
                "resolved facade state lacks retained evidence",
                facade.facade_ref,
            )
        )
    if not facade.state_adoption_refs:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-facade-state-adoption-unknown",
                FindingSeverity.UNKNOWN,
                "resolved facade state lacks an adoption decision",
                facade.facade_ref,
            )
        )
    if facade.state_basis is VaultedPassageStateBasis.DIRECT_EVIDENCE:
        if (
            facade.propagated_from_facade_ref is not None
            or facade.propagation_evidence_refs
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-direct-state-propagation-contradiction",
                    FindingSeverity.ERROR,
                    "direct facade evidence cannot carry a symmetry propagation source",
                    facade.facade_ref,
                )
            )
    elif facade.state_basis is VaultedPassageStateBasis.AUTHORIZED_PROPAGATION:
        source_ref = facade.propagated_from_facade_ref
        source = None if source_ref is None else facades_by_ref.get(source_ref)
        if (
            source_ref is None
            or source_ref == facade.facade_ref
            or source is None
            or not facade.propagation_evidence_refs
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-unsupported-symmetry-propagation",
                    FindingSeverity.ERROR,
                    "facade state cannot be propagated by symmetry without facade-specific evidence",
                    facade.facade_ref,
                    *(tuple() if source_ref is None else (source_ref,)),
                )
            )
        elif source.state is not facade.state:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-propagated-state-contradiction",
                    FindingSeverity.ERROR,
                    "propagated facade state differs from its evidence source",
                    facade.facade_ref,
                    source.facade_ref,
                )
            )
    else:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-resolved-state-basis-unknown",
                FindingSeverity.UNKNOWN,
                "OPEN/CLOSED facade lacks a resolved evidence basis",
                facade.facade_ref,
            )
        )


def _check_facade_geometry(
    contract: VaultedPassageCurrentStateContract,
    facade: VaultedPassageFacadeRecord,
    inspected_by_pair: dict[tuple[str, str], _InspectedObject],
    findings: list[CheckFinding],
    measurements: list[CheckMeasurement],
) -> None:
    if facade.state is VaultedPassageFacadeState.PARKED:
        return
    required_roles = (
        _OPEN_ROLES
        if facade.state is VaultedPassageFacadeState.OPEN
        else _CLOSED_ROLES
    )
    bindings = facade.bindings_by_role
    observed_roles = set(bindings)
    for role in sorted(required_roles - observed_roles, key=lambda item: item.value):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-morphology-role-missing",
                FindingSeverity.ERROR,
                f"{facade.state.value} facade lacks {role.value}",
                facade.facade_ref,
            )
        )
    unexpected = observed_roles - required_roles
    for role in sorted(unexpected, key=lambda item: item.value):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-morphology-role-contradiction",
                FindingSeverity.ERROR,
                f"{role.value} contradicts {facade.state.value} facade state",
                facade.facade_ref,
                bindings[role].ref,
            )
        )
    if required_roles - observed_roles:
        return

    observed: dict[VaultedPassageMorphologyRole, _InspectedObject] = {}
    for role in required_roles:
        item = inspected_by_pair.get(bindings[role].identity_pair)
        if item is None:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-morphology-inspection-missing",
                    FindingSeverity.ERROR,
                    f"{role.value} lacks controller-inspected geometry",
                    facade.facade_ref,
                    bindings[role].ref,
                )
            )
        else:
            observed[role] = item
    if set(observed) != set(required_roles):
        return

    portal_binding = bindings[VaultedPassageMorphologyRole.EXTERIOR_PORTAL]
    portal = observed[VaultedPassageMorphologyRole.EXTERIOR_PORTAL]
    vault_binding = bindings[VaultedPassageMorphologyRole.PASSAGE_VAULT]
    vault = observed[VaultedPassageMorphologyRole.PASSAGE_VAULT]
    host_binding = bindings[VaultedPassageMorphologyRole.HOST_OPENING]
    host = observed[VaultedPassageMorphologyRole.HOST_OPENING]
    _check_solid_opening(
        contract,
        facade,
        portal_binding,
        portal,
        findings,
    )
    _check_solid_opening(
        contract,
        facade,
        host_binding,
        host,
        findings,
    )
    _check_curved_vault(
        contract,
        facade,
        vault_binding,
        vault,
        findings,
    )

    if facade.state is VaultedPassageFacadeState.OPEN:
        if facade.path_binding is None:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-full-path-missing",
                    FindingSeverity.ERROR,
                    "OPEN facade lacks one complete portal-to-host curve",
                    facade.facade_ref,
                )
            )
            return
        path = inspected_by_pair.get(facade.path_binding.identity_pair)
        if path is None:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-full-path-inspection-missing",
                    FindingSeverity.ERROR,
                    "full path lacks controller-inspected curve geometry",
                    facade.facade_ref,
                    facade.path_binding.ref,
                )
            )
            return
        _check_full_path(
            contract,
            facade,
            facade.path_binding,
            path,
            portal_binding,
            portal,
            vault_binding,
            vault,
            host_binding,
            host,
            findings,
            measurements,
        )
        return

    if facade.path_binding is not None:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-closed-path-contradiction",
                FindingSeverity.ERROR,
                "CLOSED facade cannot carry a clear portal-to-host path",
                facade.facade_ref,
                facade.path_binding.ref,
            )
        )
    infill_binding = bindings[VaultedPassageMorphologyRole.HISTORICAL_INFILL]
    infill = observed[VaultedPassageMorphologyRole.HISTORICAL_INFILL]
    _check_solid_opening(
        contract,
        facade,
        infill_binding,
        infill,
        findings,
    )
    if not (
        _bounds_overlap(infill.bounds, portal.bounds, contract.criteria.interface_tolerance)
        or _bounds_overlap(
            infill.bounds,
            host.bounds,
            contract.criteria.interface_tolerance,
        )
    ):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-historical-infill-disconnected",
                FindingSeverity.ERROR,
                "historical infill does not close the portal or host interface",
                facade.facade_ref,
                infill_binding.ref,
            )
        )
    try:
        axis = _unit(_sub(_center(host.bounds), _center(portal.bounds)))
    except VaultedPassageCurrentStateError as exc:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-closed-axis-contradiction",
                FindingSeverity.ERROR,
                str(exc),
                facade.facade_ref,
            )
        )
        return
    _check_section_and_host_cut(
        contract,
        facade,
        portal_binding,
        portal,
        vault_binding,
        vault,
        host_binding,
        host,
        axis,
        findings,
        measurements,
    )


def check_vaulted_passage_current_state(
    contract: VaultedPassageCurrentStateContract,
    *,
    inspection: ThreeDmInspection | None,
) -> CheckReceiptEnvelope:
    """Validate exact per-facade morphology against controller 3DM inspection."""

    if not isinstance(contract, VaultedPassageCurrentStateContract):
        raise TypeError("contract must be VaultedPassageCurrentStateContract")
    findings: list[CheckFinding] = []
    measurements: list[CheckMeasurement] = []
    inspection_refs: tuple[str, ...] = ()

    if not contract.criteria.evidence_refs:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-criteria-evidence-unknown",
                FindingSeverity.UNKNOWN,
                "morphology criteria lack retained evidence",
                contract.criteria.ref,
            )
        )
    if not contract.criteria.adoption_refs:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-criteria-adoption-unknown",
                FindingSeverity.UNKNOWN,
                "morphology criteria lack an adoption decision",
                contract.criteria.ref,
            )
        )

    expected = set(contract.expected_facade_refs)
    facades_by_ref = {item.facade_ref: item for item in contract.facades}
    observed = set(facades_by_ref)
    for facade_ref in sorted(expected - observed):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-facade-state-missing",
                FindingSeverity.ERROR,
                "every expected facade needs OPEN, CLOSED, or PARKED state",
                _facade_requirement_ref(facade_ref),
            )
        )
    for facade_ref in sorted(observed - expected):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-facade-denominator-contradiction",
                FindingSeverity.ERROR,
                "facade record falls outside the adopted denominator",
                facade_ref,
            )
        )

    all_pairs: list[tuple[str, str]] = []
    for facade in contract.facades:
        all_pairs.extend(item.identity_pair for item in facade.role_bindings)
        if facade.path_binding is not None:
            all_pairs.append(facade.path_binding.identity_pair)
    if len(all_pairs) != len(set(all_pairs)):
        findings.append(
            _finding(
                contract,
                "vaulted-passage-cross-facade-geometry-contradiction",
                FindingSeverity.ERROR,
                "different facades cannot reuse one saved geometry identity as symmetric proof",
                *tuple(item.facade_ref for item in contract.facades),
            )
        )

    for facade in contract.facades:
        _check_facade_state_basis(contract, facade, facades_by_ref, findings)

    inspected_by_pair: dict[tuple[str, str], _InspectedObject] = {}
    if inspection is None:
        findings.append(
            _finding(
                contract,
                "vaulted-passage-controller-inspection-missing",
                FindingSeverity.ERROR,
                "Agent contract cannot substitute for controller-owned 3dm inspection",
                contract.inspection_artifact_ref,
            )
        )
    elif not isinstance(inspection, ThreeDmInspection):
        raise TypeError("inspection must be ThreeDmInspection or None")
    else:
        observed_digest = canonical_digest(inspection.to_dict())
        if inspection.file_sha256 != contract.inspection_model_sha256:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-inspection-file-sha-contradiction",
                    FindingSeverity.ERROR,
                    "controller inspection is not for the bound saved 3dm",
                    contract.inspection_artifact_ref,
                )
            )
        if observed_digest != contract.inspection_digest:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-inspection-digest-contradiction",
                    FindingSeverity.ERROR,
                    "controller inspection summary differs from the exact binding",
                    contract.inspection_artifact_ref,
                )
            )
        if inspection.read_only is not True or inspection.rhino_process_started is not False:
            findings.append(
                _finding(
                    contract,
                    "vaulted-passage-inspection-mode-contradiction",
                    FindingSeverity.ERROR,
                    "morphology evidence was not produced by the read-only headless inspector",
                    contract.inspection_artifact_ref,
                )
            )
        inspected_by_pair, inspection_refs = _inspection_object_universe(
            contract,
            inspection,
            findings,
        )

    for facade in contract.facades:
        _check_facade_geometry(
            contract,
            facade,
            inspected_by_pair,
            findings,
            measurements,
        )

    if any(item.severity is FindingSeverity.ERROR for item in findings):
        status = CheckStatus.FAIL
    elif any(item.severity is FindingSeverity.UNKNOWN for item in findings):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    denominator = tuple(sorted(set(contract.denominator_refs + inspection_refs)))
    ordered_findings = tuple(
        sorted(findings, key=lambda item: (item.code, item.subject_refs, item.message))
    )
    return CheckReceiptEnvelope(
        check_id=f"vaulted-passage-current-{contract.contract_digest[:24]}",
        checker_id=VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID,
        checker_version="1.0.0",
        branch=contract.branch,
        scope_digest=contract.scope_digest,
        subject_refs=denominator,
        subject_digest=contract.stage_subject_digest,
        status=status,
        adoption_refs=contract.adoption_refs,
        source_refs=contract.source_refs,
        authority_refs=contract.adoption_refs,
        findings=ordered_findings,
        measurements=tuple(
            sorted(measurements, key=lambda item: item.measurement_id)
        ),
        coverage_denominator=denominator,
        covered_refs=(
            denominator
            if status in {CheckStatus.PASS, CheckStatus.FAIL}
            else ()
        ),
    )


__all__ = [
    "VAULTED_PASSAGE_CURRENT_STATE_CHECKER_ID",
    "VaultedPassageCurrentStateContract",
    "VaultedPassageCurrentStateError",
    "VaultedPassageFacadeRecord",
    "VaultedPassageFacadeState",
    "VaultedPassageMorphologyCriteria",
    "VaultedPassageMorphologyRole",
    "VaultedPassagePathBinding",
    "VaultedPassageRoleBinding",
    "VaultedPassageStateBasis",
    "check_vaulted_passage_current_state",
]
