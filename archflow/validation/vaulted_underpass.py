"""Evidence-bound validation for a vaulted passage below circulation.

The checker is deliberately project-independent.  A clearance scalar proves
only clearance; it cannot prove that a selected vaulted construction form was
materialized.  A passing receipt therefore binds the adopted form to an exact
six-role geometry/readback denominator and replays the generic relation,
assembly, and interface-continuity checks.

``ThreeDmInspection`` is a controller-owned adapter boundary.  A generative
Agent may propose geometry, but it must never author or supply this inspection
witness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
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
from archflow.relations.realization import RELATION_REALIZATION_CHECKER_ID
from archflow.validation.assembly import (
    AssemblyProfile,
    RelationshipKind,
    check_assembly,
)
from archflow.validation.cad_readback import (
    CadBoundingBox,
    CadReadbackProfile,
    CadReadbackSnapshot,
    validate_cad_readback,
)
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archflow.validation.interface_continuity import (
    InterfaceBoundarySegment,
    InterfaceBoundarySupportSet,
    InterfaceContinuityError,
    check_interface_boundary_continuity,
)


UNDERPASS_ASSEMBLY_CHECKER_ID = "vaulted-underpass-assembly-checker"
_INTERFACE_CONTINUITY_CHECKER_ID = "interface-boundary-continuity"
_ASSEMBLY_RELATIONSHIP_CHECKER_ID = "assembly-relationship-checker"
_CAD_READBACK_CHECKER_ID = "cad-readback-validator"
_OBJECT_REF_USER_STRING = "archflow:object_ref"
_OPERATION_REF_USER_STRING = "archflow:operation_ref"
_AUTHORITY_FIELDS = {
    "design_authority": False,
    "verification_authority": False,
    "geometry_mutation_authority": False,
    "stage_acceptance_authority": False,
    "promotion_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


class VaultedUnderpassError(ValueError):
    """A vaulted-underpass contract or role binding is malformed."""


class UnderpassConstructionForm(StrEnum):
    """Evidence-selected construction form for an applicable underpass."""

    UNKNOWN = "unknown"
    FLAT = "flat"
    VAULTED = "vaulted"
    OTHER = "other"


class VaultedUnderpassRole(StrEnum):
    """Required physical roles in a coordinated vaulted underpass."""

    HOST_OPENING = "host_opening"
    LEFT_SUPPORT = "left_support"
    LOAD_PATH_TERMINAL = "load_path_terminal"
    OVERHEAD_VAULT = "overhead_vault"
    RIGHT_SUPPORT = "right_support"
    STAIR_OR_LANDING_ABOVE = "stair_or_landing_above"


_REQUIRED_ROLES = frozenset(VaultedUnderpassRole)
_REQUIRED_INTERFACE_SUPPORT_ROLES = frozenset(
    {VaultedUnderpassRole.LEFT_SUPPORT, VaultedUnderpassRole.RIGHT_SUPPORT}
)


def _receipt_ref(receipt: CheckReceiptEnvelope) -> str:
    return f"check-receipt:{receipt.receipt_id}"


def _role_requirement_ref(role: VaultedUnderpassRole) -> str:
    return f"vaulted-underpass-required-role:{role.value}"


@dataclass(frozen=True, slots=True)
class VaultedUnderpassRoleBinding:
    """Bind one required role through program and exact CAD readback IDs."""

    role: VaultedUnderpassRole
    component_ref: str
    program_object_ref: str
    producer_operation_ref: str
    readback_object_ref: str
    readback_operation_ref: str
    relation_binding_ref: str

    SCHEMA: ClassVar[str] = "VaultedUnderpassRoleBinding@1"

    def __post_init__(self) -> None:
        if not isinstance(self.role, VaultedUnderpassRole):
            raise TypeError("role must be VaultedUnderpassRole")
        for field in (
            "component_ref",
            "program_object_ref",
            "producer_operation_ref",
            "readback_object_ref",
            "readback_operation_ref",
            "relation_binding_ref",
        ):
            object.__setattr__(
                self,
                field,
                logical_ref(getattr(self, field), f"vaulted underpass {field}"),
            )

    @property
    def ref(self) -> str:
        return (
            f"vaulted-underpass-role-binding:{self.role.value}:"
            f"{canonical_digest(self.to_dict())}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "component_ref": self.component_ref,
            "program_object_ref": self.program_object_ref,
            "producer_operation_ref": self.producer_operation_ref,
            "readback_object_ref": self.readback_object_ref,
            "readback_operation_ref": self.readback_operation_ref,
            "relation_binding_ref": self.relation_binding_ref,
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedUnderpassRoleBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "component_ref",
                "program_object_ref",
                "producer_operation_ref",
                "readback_object_ref",
                "readback_operation_ref",
                "relation_binding_ref",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-underpass role binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedUnderpassError("unsupported role-binding schema")
        result = cls(
            role=VaultedUnderpassRole(payload["role"]),
            component_ref=payload["component_ref"],
            program_object_ref=payload["program_object_ref"],
            producer_operation_ref=payload["producer_operation_ref"],
            readback_object_ref=payload["readback_object_ref"],
            readback_operation_ref=payload["readback_operation_ref"],
            relation_binding_ref=payload["relation_binding_ref"],
        )
        if result.to_dict() != payload:
            raise VaultedUnderpassError("role-binding identity changed")
        return result


_THREE_DM_INSPECTION_SEQUENCE_FIELDS = (
    "layers",
    "object_counts_by_layer",
    "instance_definitions",
    "instance_references",
    "document_user_strings",
    "object_user_strings",
    "named_object_bboxes",
    "visible_bounds_witnesses",
    "materials",
    "render_materials",
    "object_material_bindings",
    "object_geometry_sha256",
    "object_geometry_analysis",
)


def _three_dm_inspection_from_dict(value: object) -> ThreeDmInspection:
    """Rehydrate the inspector's immutable summary without trusting a report."""

    payload = exact_mapping(
        value,
        {
            "schema",
            "file_sha256",
            "file_bytes",
            "three_dm_version",
            "archive_version",
            "units",
            "layers",
            "object_count",
            "top_level_object_count",
            "instance_definition_member_count",
            "object_counts_by_type",
            "object_counts_by_layer",
            "instance_definitions",
            "instance_references",
            "document_user_strings",
            "object_user_strings",
            "aggregate_bbox",
            "bbox_contributing_geometry_count",
            "named_object_bboxes",
            "visible_bounds_witnesses",
            "materials",
            "render_materials",
            "object_material_bindings",
            "object_geometry_sha256",
            "object_geometry_analysis",
            "read_only",
            "rhino_process_started",
        },
        "vaulted-underpass 3dm inspection",
    )
    if payload["schema"] != ThreeDmInspection.SCHEMA:
        raise VaultedUnderpassError("unsupported 3dm inspection schema")
    for field in _THREE_DM_INSPECTION_SEQUENCE_FIELDS:
        if not isinstance(payload[field], list):
            raise TypeError(f"3dm inspection {field} must be a list")
    result = ThreeDmInspection(
        file_sha256=payload["file_sha256"],
        file_bytes=payload["file_bytes"],
        three_dm_version=payload["three_dm_version"],
        archive_version=payload["archive_version"],
        units=payload["units"],
        layers=tuple(payload["layers"]),
        object_count=payload["object_count"],
        top_level_object_count=payload["top_level_object_count"],
        instance_definition_member_count=payload[
            "instance_definition_member_count"
        ],
        object_counts_by_type=payload["object_counts_by_type"],
        object_counts_by_layer=tuple(payload["object_counts_by_layer"]),
        instance_definitions=tuple(payload["instance_definitions"]),
        instance_references=tuple(payload["instance_references"]),
        document_user_strings=tuple(payload["document_user_strings"]),
        object_user_strings=tuple(payload["object_user_strings"]),
        aggregate_bbox=payload["aggregate_bbox"],
        bbox_contributing_geometry_count=payload[
            "bbox_contributing_geometry_count"
        ],
        named_object_bboxes=tuple(payload["named_object_bboxes"]),
        visible_bounds_witnesses=tuple(payload["visible_bounds_witnesses"]),
        materials=tuple(payload["materials"]),
        render_materials=tuple(payload["render_materials"]),
        object_material_bindings=tuple(payload["object_material_bindings"]),
        object_geometry_sha256=tuple(payload["object_geometry_sha256"]),
        object_geometry_analysis=tuple(payload["object_geometry_analysis"]),
        read_only=payload["read_only"],
        rhino_process_started=payload["rhino_process_started"],
    )
    if result.to_dict() != payload:
        raise VaultedUnderpassError("3dm inspection identity changed")
    return result


@dataclass(frozen=True, slots=True)
class VaultedUnderpassInspectionBinding:
    """Bind one immutable saved 3DM artifact to its real inspector output."""

    artifact_ref: str
    model_sha256: str
    inspection: ThreeDmInspection

    SCHEMA: ClassVar[str] = "VaultedUnderpassInspectionBinding@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_ref",
            logical_ref(self.artifact_ref, "vaulted-underpass artifact_ref"),
        )
        object.__setattr__(
            self,
            "model_sha256",
            require_sha256(self.model_sha256, "vaulted-underpass model_sha256"),
        )
        if not isinstance(self.inspection, ThreeDmInspection):
            raise TypeError("inspection must be ThreeDmInspection")
        require_sha256(
            self.inspection.file_sha256,
            "vaulted-underpass inspection file_sha256",
        )
        if self.inspection.to_dict().get("schema") != ThreeDmInspection.SCHEMA:
            raise VaultedUnderpassError("3dm inspection schema changed")

    @property
    def inspection_digest(self) -> str:
        return canonical_digest(self.inspection.to_dict())

    @property
    def ref(self) -> str:
        return (
            "vaulted-underpass-inspection:"
            f"{canonical_digest(self.to_dict())}"
        )

    @property
    def denominator_refs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                (
                    self.ref,
                    self.artifact_ref,
                    f"three-dm-file-sha256:{self.model_sha256}",
                    f"three-dm-inspection:{self.inspection_digest}",
                )
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "artifact_ref": self.artifact_ref,
            "model_sha256": self.model_sha256,
            "inspection": self.inspection.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedUnderpassInspectionBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "artifact_ref",
                "model_sha256",
                "inspection",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-underpass inspection binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedUnderpassError("unsupported inspection-binding schema")
        result = cls(
            artifact_ref=payload["artifact_ref"],
            model_sha256=payload["model_sha256"],
            inspection=_three_dm_inspection_from_dict(payload["inspection"]),
        )
        if result.to_dict() != payload:
            raise VaultedUnderpassError("inspection-binding identity changed")
        return result


@dataclass(frozen=True, slots=True)
class VaultedUnderpassInterfaceBinding:
    """Retain and replay one exact vault-to-side-support interface check."""

    support_role: VaultedUnderpassRole
    check_id: str
    vault_boundary_ref: str
    support_boundary_ref: str
    required_segments: tuple[InterfaceBoundarySegment, ...]
    supporting_segments: tuple[InterfaceBoundarySegment, ...]
    support_sets: tuple[InterfaceBoundarySupportSet, ...]
    expected_required_refs: tuple[str, ...]
    tolerance: float
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    receipt: CheckReceiptEnvelope

    SCHEMA: ClassVar[str] = "VaultedUnderpassInterfaceBinding@2"

    def __post_init__(self) -> None:
        if self.support_role not in _REQUIRED_INTERFACE_SUPPORT_ROLES:
            raise VaultedUnderpassError(
                "interface support_role must be left_support or right_support"
            )
        identifier(self.check_id, "vaulted-underpass interface check_id")
        for field in ("vault_boundary_ref", "support_boundary_ref"):
            object.__setattr__(
                self,
                field,
                logical_ref(getattr(self, field), f"vaulted-underpass {field}"),
            )
        if self.vault_boundary_ref == self.support_boundary_ref:
            raise VaultedUnderpassError("interface boundary refs must be distinct")
        if not isinstance(self.required_segments, tuple) or any(
            not isinstance(item, InterfaceBoundarySegment)
            for item in self.required_segments
        ):
            raise TypeError(
                "required_segments must contain InterfaceBoundarySegment"
            )
        if not isinstance(self.supporting_segments, tuple) or any(
            not isinstance(item, InterfaceBoundarySegment)
            for item in self.supporting_segments
        ):
            raise TypeError(
                "supporting_segments must contain InterfaceBoundarySegment"
            )
        if not isinstance(self.support_sets, tuple) or any(
            not isinstance(item, InterfaceBoundarySupportSet)
            for item in self.support_sets
        ):
            raise TypeError(
                "support_sets must contain InterfaceBoundarySupportSet"
            )
        required_segments = tuple(
            sorted(self.required_segments, key=lambda item: item.segment_ref)
        )
        supporting_segments = tuple(
            sorted(self.supporting_segments, key=lambda item: item.segment_ref)
        )
        support_sets = tuple(
            sorted(self.support_sets, key=lambda item: item.required_segment_ref)
        )
        object.__setattr__(self, "required_segments", required_segments)
        object.__setattr__(self, "supporting_segments", supporting_segments)
        object.__setattr__(self, "support_sets", support_sets)
        object.__setattr__(
            self,
            "expected_required_refs",
            deterministic_refs(
                self.expected_required_refs,
                "vaulted-underpass expected_required_refs",
            ),
        )
        if tuple(item.segment_ref for item in required_segments) != (
            self.vault_boundary_ref,
        ):
            raise VaultedUnderpassError(
                "required_segments must exactly realize vault_boundary_ref"
            )
        if tuple(item.segment_ref for item in supporting_segments) != (
            self.support_boundary_ref,
        ):
            raise VaultedUnderpassError(
                "supporting_segments must exactly realize support_boundary_ref"
            )
        if self.expected_required_refs != (self.vault_boundary_ref,):
            raise VaultedUnderpassError(
                "expected_required_refs must equal vault_boundary_ref"
            )
        if len(support_sets) != 1 or support_sets[0] != InterfaceBoundarySupportSet(
            self.vault_boundary_ref,
            (self.support_boundary_ref,),
        ):
            raise VaultedUnderpassError(
                "support_sets must bind the exact vault and support boundaries"
            )
        checked_tolerance = float(
            finite_number(self.tolerance, "vaulted-underpass interface tolerance")
        )
        if checked_tolerance <= 0.0:
            raise VaultedUnderpassError("interface tolerance must be positive")
        object.__setattr__(self, "tolerance", checked_tolerance)
        for field in ("evidence_refs", "authority_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    f"vaulted-underpass interface {field}",
                ),
            )
        if not isinstance(self.receipt, CheckReceiptEnvelope):
            raise TypeError("receipt must be CheckReceiptEnvelope")

    @property
    def ref(self) -> str:
        return (
            f"vaulted-underpass-interface:{self.support_role.value}:"
            f"{canonical_digest(self.to_dict())}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "support_role": self.support_role.value,
            "check_id": self.check_id,
            "vault_boundary_ref": self.vault_boundary_ref,
            "support_boundary_ref": self.support_boundary_ref,
            "required_segments": [
                item.to_dict() for item in self.required_segments
            ],
            "supporting_segments": [
                item.to_dict() for item in self.supporting_segments
            ],
            "support_sets": [item.to_dict() for item in self.support_sets],
            "expected_required_refs": list(self.expected_required_refs),
            "tolerance": self.tolerance,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "receipt": self.receipt.to_dict(),
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedUnderpassInterfaceBinding":
        payload = exact_mapping(
            value,
            {
                "schema",
                "support_role",
                "check_id",
                "vault_boundary_ref",
                "support_boundary_ref",
                "required_segments",
                "supporting_segments",
                "support_sets",
                "expected_required_refs",
                "tolerance",
                "evidence_refs",
                "authority_refs",
                "receipt",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-underpass interface binding",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedUnderpassError("unsupported interface-binding schema")
        for field in (
            "required_segments",
            "supporting_segments",
            "support_sets",
            "expected_required_refs",
            "evidence_refs",
            "authority_refs",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"vaulted-underpass interface {field} must be a list")
        result = cls(
            support_role=VaultedUnderpassRole(payload["support_role"]),
            check_id=payload["check_id"],
            vault_boundary_ref=payload["vault_boundary_ref"],
            support_boundary_ref=payload["support_boundary_ref"],
            required_segments=tuple(
                InterfaceBoundarySegment.from_dict(item)
                for item in payload["required_segments"]
            ),
            supporting_segments=tuple(
                InterfaceBoundarySegment.from_dict(item)
                for item in payload["supporting_segments"]
            ),
            support_sets=tuple(
                InterfaceBoundarySupportSet.from_dict(item)
                for item in payload["support_sets"]
            ),
            expected_required_refs=tuple(payload["expected_required_refs"]),
            tolerance=payload["tolerance"],
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
            receipt=CheckReceiptEnvelope.from_dict(payload["receipt"]),
        )
        if result.to_dict() != payload:
            raise VaultedUnderpassError("interface-binding identity changed")
        return result


@dataclass(frozen=True, slots=True)
class VaultedUnderpassAssemblyContract:
    """Exact evidence and geometry denominator for one selected underpass."""

    contract_id: str
    branch: BranchRef
    scope_digest: str
    stage_subject_digest: str
    construction_form: UnderpassConstructionForm
    form_evidence_refs: tuple[str, ...]
    form_adoption_refs: tuple[str, ...]
    context_refs: tuple[str, ...] = ()
    role_bindings: tuple[VaultedUnderpassRoleBinding, ...] = ()
    cad_readback_profile: CadReadbackProfile | None = None
    cad_readback_snapshot: CadReadbackSnapshot | None = None
    inspection_binding: VaultedUnderpassInspectionBinding | None = None
    assembly_profile: AssemblyProfile | None = None
    relation_realization_receipt: CheckReceiptEnvelope | None = None
    interface_bindings: tuple[VaultedUnderpassInterfaceBinding, ...] = ()

    SCHEMA: ClassVar[str] = "VaultedUnderpassAssemblyContract@2"

    def __post_init__(self) -> None:
        identifier(self.contract_id, "vaulted-underpass contract_id")
        require_exact_branch(self.branch)
        object.__setattr__(
            self,
            "scope_digest",
            require_sha256(self.scope_digest, "scope_digest"),
        )
        object.__setattr__(
            self,
            "stage_subject_digest",
            require_sha256(self.stage_subject_digest, "stage_subject_digest"),
        )
        if not isinstance(self.construction_form, UnderpassConstructionForm):
            raise TypeError("construction_form must be UnderpassConstructionForm")
        for field in ("form_evidence_refs", "form_adoption_refs", "context_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(getattr(self, field), field, allow_empty=True),
            )
        if not isinstance(self.role_bindings, tuple) or any(
            not isinstance(item, VaultedUnderpassRoleBinding)
            for item in self.role_bindings
        ):
            raise TypeError("role_bindings must contain VaultedUnderpassRoleBinding")
        bindings = tuple(sorted(self.role_bindings, key=lambda item: item.role.value))
        roles = tuple(item.role for item in bindings)
        if len(roles) != len(set(roles)):
            raise VaultedUnderpassError("role_bindings repeat a physical role")
        for field in (
            "program_object_ref",
            "producer_operation_ref",
            "readback_object_ref",
            "readback_operation_ref",
            "relation_binding_ref",
        ):
            refs = tuple(getattr(item, field) for item in bindings)
            if len(refs) != len(set(refs)):
                raise VaultedUnderpassError(f"role_bindings repeat {field}")
        object.__setattr__(self, "role_bindings", bindings)
        if self.cad_readback_profile is not None and not isinstance(
            self.cad_readback_profile,
            CadReadbackProfile,
        ):
            raise TypeError(
                "cad_readback_profile must be CadReadbackProfile or None"
            )
        if self.cad_readback_snapshot is not None and not isinstance(
            self.cad_readback_snapshot,
            CadReadbackSnapshot,
        ):
            raise TypeError(
                "cad_readback_snapshot must be CadReadbackSnapshot or None"
            )
        if self.inspection_binding is not None and not isinstance(
            self.inspection_binding,
            VaultedUnderpassInspectionBinding,
        ):
            raise TypeError(
                "inspection_binding must be "
                "VaultedUnderpassInspectionBinding or None"
            )
        if self.assembly_profile is not None and not isinstance(
            self.assembly_profile,
            AssemblyProfile,
        ):
            raise TypeError("assembly_profile must be AssemblyProfile or None")
        if self.relation_realization_receipt is not None and not isinstance(
            self.relation_realization_receipt,
            CheckReceiptEnvelope,
        ):
            raise TypeError(
                "relation_realization_receipt must be CheckReceiptEnvelope or None"
            )
        if not isinstance(self.interface_bindings, tuple) or any(
            not isinstance(item, VaultedUnderpassInterfaceBinding)
            for item in self.interface_bindings
        ):
            raise TypeError(
                "interface_bindings must contain VaultedUnderpassInterfaceBinding"
            )
        interface_bindings = tuple(
            sorted(self.interface_bindings, key=lambda item: item.support_role.value)
        )
        support_roles = tuple(item.support_role for item in interface_bindings)
        if len(support_roles) != len(set(support_roles)):
            raise VaultedUnderpassError("interface_bindings repeat a support role")
        object.__setattr__(self, "interface_bindings", interface_bindings)

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def ref(self) -> str:
        return f"vaulted-underpass-contract:{self.contract_id}:{self.contract_digest}"

    @property
    def role_bindings_by_role(self) -> dict[VaultedUnderpassRole, VaultedUnderpassRoleBinding]:
        return {item.role: item for item in self.role_bindings}

    @property
    def denominator_refs(self) -> tuple[str, ...]:
        refs = {
            self.ref,
            *self.context_refs,
            *(_role_requirement_ref(role) for role in _REQUIRED_ROLES),
        }
        for binding in self.role_bindings:
            refs.update(
                (
                    binding.ref,
                    binding.component_ref,
                    binding.program_object_ref,
                    binding.producer_operation_ref,
                    binding.readback_object_ref,
                    binding.readback_operation_ref,
                    binding.relation_binding_ref,
                )
            )
        if self.assembly_profile is not None:
            refs.update(self.assembly_profile.check_denominator)
        if self.cad_readback_profile is not None:
            refs.update(self.cad_readback_profile.check_denominator)
        if self.cad_readback_snapshot is not None:
            refs.add(
                f"cad-readback-snapshot:{self.cad_readback_snapshot.snapshot_digest}"
            )
            for item in self.cad_readback_snapshot.objects:
                refs.update((item.object_ref, item.operation_ref))
        if self.inspection_binding is not None:
            refs.update(self.inspection_binding.denominator_refs)
        if (
            self.cad_readback_profile is not None
            and self.cad_readback_snapshot is not None
        ):
            readback_receipt = validate_cad_readback(
                self.cad_readback_profile,
                self.cad_readback_snapshot,
                stage_subject_digest=self.stage_subject_digest,
            )
            refs.add(_receipt_ref(readback_receipt))
            refs.update(readback_receipt.coverage_denominator)
        if self.relation_realization_receipt is not None:
            refs.add(_receipt_ref(self.relation_realization_receipt))
            refs.update(self.relation_realization_receipt.coverage_denominator)
        for binding in self.interface_bindings:
            refs.add(binding.ref)
            refs.update(
                (binding.vault_boundary_ref, binding.support_boundary_ref)
            )
            refs.add(_receipt_ref(binding.receipt))
            refs.update(binding.receipt.coverage_denominator)
        return tuple(sorted(refs))

    @property
    def source_refs(self) -> tuple[str, ...]:
        refs = set(self.form_evidence_refs)
        if self.relation_realization_receipt is not None:
            refs.update(self.relation_realization_receipt.source_refs)
        if self.assembly_profile is not None:
            for requirement in self.assembly_profile.requirements:
                refs.update(requirement.evidence_refs)
        for binding in self.interface_bindings:
            refs.update(binding.receipt.source_refs)
        return tuple(sorted(refs))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "contract_id": self.contract_id,
            "branch": branch_ref_to_dict(self.branch),
            "scope_digest": self.scope_digest,
            "stage_subject_digest": self.stage_subject_digest,
            "construction_form": self.construction_form.value,
            "form_evidence_refs": list(self.form_evidence_refs),
            "form_adoption_refs": list(self.form_adoption_refs),
            "context_refs": list(self.context_refs),
            "role_bindings": [item.to_dict() for item in self.role_bindings],
            "cad_readback_profile": (
                None
                if self.cad_readback_profile is None
                else self.cad_readback_profile.to_dict()
            ),
            "cad_readback_snapshot": (
                None
                if self.cad_readback_snapshot is None
                else self.cad_readback_snapshot.to_dict()
            ),
            "inspection_binding": (
                None
                if self.inspection_binding is None
                else self.inspection_binding.to_dict()
            ),
            "assembly_profile": (
                None if self.assembly_profile is None else self.assembly_profile.to_dict()
            ),
            "relation_realization_receipt": (
                None
                if self.relation_realization_receipt is None
                else self.relation_realization_receipt.to_dict()
            ),
            "interface_bindings": [
                item.to_dict() for item in self.interface_bindings
            ],
            **_AUTHORITY_FIELDS,
        }

    @classmethod
    def from_dict(cls, value: object) -> "VaultedUnderpassAssemblyContract":
        payload = exact_mapping(
            value,
            {
                "schema",
                "contract_id",
                "branch",
                "scope_digest",
                "stage_subject_digest",
                "construction_form",
                "form_evidence_refs",
                "form_adoption_refs",
                "context_refs",
                "role_bindings",
                "cad_readback_profile",
                "cad_readback_snapshot",
                "inspection_binding",
                "assembly_profile",
                "relation_realization_receipt",
                "interface_bindings",
                *_AUTHORITY_FIELDS,
            },
            "vaulted-underpass assembly contract",
        )
        if payload["schema"] != cls.SCHEMA:
            raise VaultedUnderpassError("unsupported assembly-contract schema")
        for field in (
            "form_evidence_refs",
            "form_adoption_refs",
            "context_refs",
            "role_bindings",
            "interface_bindings",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"vaulted-underpass {field} must be a list")
        result = cls(
            contract_id=payload["contract_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            scope_digest=payload["scope_digest"],
            stage_subject_digest=payload["stage_subject_digest"],
            construction_form=UnderpassConstructionForm(payload["construction_form"]),
            form_evidence_refs=tuple(payload["form_evidence_refs"]),
            form_adoption_refs=tuple(payload["form_adoption_refs"]),
            context_refs=tuple(payload["context_refs"]),
            role_bindings=tuple(
                VaultedUnderpassRoleBinding.from_dict(item)
                for item in payload["role_bindings"]
            ),
            cad_readback_profile=(
                None
                if payload["cad_readback_profile"] is None
                else CadReadbackProfile.from_dict(payload["cad_readback_profile"])
            ),
            cad_readback_snapshot=(
                None
                if payload["cad_readback_snapshot"] is None
                else CadReadbackSnapshot.from_dict(payload["cad_readback_snapshot"])
            ),
            inspection_binding=(
                None
                if payload["inspection_binding"] is None
                else VaultedUnderpassInspectionBinding.from_dict(
                    payload["inspection_binding"]
                )
            ),
            assembly_profile=(
                None
                if payload["assembly_profile"] is None
                else AssemblyProfile.from_dict(payload["assembly_profile"])
            ),
            relation_realization_receipt=(
                None
                if payload["relation_realization_receipt"] is None
                else CheckReceiptEnvelope.from_dict(
                    payload["relation_realization_receipt"]
                )
            ),
            interface_bindings=tuple(
                VaultedUnderpassInterfaceBinding.from_dict(item)
                for item in payload["interface_bindings"]
            ),
        )
        if result.to_dict() != payload:
            raise VaultedUnderpassError("assembly-contract identity changed")
        return result


def _finding(
    contract: VaultedUnderpassAssemblyContract,
    code: str,
    severity: FindingSeverity,
    message: str,
    *subject_refs: str,
) -> CheckFinding:
    refs = tuple(sorted(set(subject_refs or (contract.ref,))))
    return CheckFinding(
        code=code,
        severity=severity,
        message=message,
        subject_refs=refs,
        evidence_refs=contract.form_evidence_refs,
    )


def _check_receipt_context(
    contract: VaultedUnderpassAssemblyContract,
    receipt: CheckReceiptEnvelope,
    *,
    label: str,
    checker_id: str,
    require_stage_subject: bool,
    findings: list[CheckFinding],
) -> None:
    replayed = CheckReceiptEnvelope.from_dict(receipt.to_dict())
    receipt_ref = _receipt_ref(receipt)
    if replayed != receipt:
        findings.append(
            _finding(
                contract,
                f"vaulted-underpass-{label}-identity-contradiction",
                FindingSeverity.ERROR,
                f"{label} receipt changed during typed replay",
                receipt_ref,
            )
        )
    comparisons: list[tuple[str, object, object]] = [
        ("checker", checker_id, receipt.checker_id),
        ("branch", contract.branch, receipt.branch),
        ("scope", contract.scope_digest, receipt.scope_digest),
    ]
    if require_stage_subject:
        comparisons.append(
            ("subject", contract.stage_subject_digest, receipt.subject_digest)
        )
    for binding, expected, observed in comparisons:
        if expected != observed:
            findings.append(
                _finding(
                    contract,
                    f"vaulted-underpass-{label}-{binding}-contradiction",
                    FindingSeverity.ERROR,
                    f"{label} receipt crossed its exact {binding} binding",
                    receipt_ref,
                )
            )
    if receipt.status is CheckStatus.FAIL:
        findings.append(
            _finding(
                contract,
                f"vaulted-underpass-{label}-failed",
                FindingSeverity.ERROR,
                f"{label} receipt failed",
                receipt_ref,
            )
        )
    elif receipt.status is not CheckStatus.PASS:
        findings.append(
            _finding(
                contract,
                f"vaulted-underpass-{label}-unknown",
                FindingSeverity.UNKNOWN,
                f"{label} receipt is not passing",
                receipt_ref,
            )
        )
    if not receipt.coverage_denominator:
        findings.append(
            _finding(
                contract,
                f"vaulted-underpass-{label}-denominator-unknown",
                FindingSeverity.UNKNOWN,
                f"{label} receipt has no checked denominator",
                receipt_ref,
            )
        )


def _inspection_user_strings(row: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in ("attributes", "geometry"):
        values = row.get(field)
        if not isinstance(values, (list, tuple)):
            raise VaultedUnderpassError(
                f"3dm inspection object {field} user strings are malformed"
            )
        for value in values:
            if (
                not isinstance(value, dict)
                or set(value) != {"key", "value"}
                or not isinstance(value["key"], str)
                or not isinstance(value["value"], str)
            ):
                raise VaultedUnderpassError(
                    "3dm inspection object user string is malformed"
                )
            key = value["key"]
            if key in result and result[key] != value["value"]:
                raise VaultedUnderpassError(
                    "3dm inspection object user string identity conflicts"
                )
            result[key] = value["value"]
    return result


def _inspection_rows_by_object_id(
    rows: tuple[dict[str, object], ...],
    field: str,
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("object_id"), str):
            raise VaultedUnderpassError(f"3dm inspection {field} row is malformed")
        object_id = row["object_id"]
        if object_id in result:
            raise VaultedUnderpassError(
                f"3dm inspection {field} repeats object_id"
            )
        result[object_id] = row
    return result


def _inspection_bbox(value: object) -> CadBoundingBox | None:
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        return None
    minimum = value["min"]
    maximum = value["max"]
    if (
        not isinstance(minimum, (list, tuple))
        or not isinstance(maximum, (list, tuple))
        or len(minimum) != 3
        or len(maximum) != 3
    ):
        return None
    try:
        return CadBoundingBox(
            minimum=tuple(float(item) for item in minimum),
            maximum=tuple(float(item) for item in maximum),
        )
    except (TypeError, ValueError):
        return None


def _same_bbox(first: object, second: CadBoundingBox) -> bool:
    observed = _inspection_bbox(first)
    if observed is None:
        return False
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-9)
        for left, right in zip(
            (*observed.minimum, *observed.maximum),
            (*second.minimum, *second.maximum),
        )
    )


def _bbox_contains_endpoint(
    bounds: CadBoundingBox,
    endpoint: tuple[float, float, float],
    tolerance: float,
) -> bool:
    return all(
        low - tolerance <= coordinate <= high + tolerance
        for low, high, coordinate in zip(
            bounds.minimum,
            bounds.maximum,
            endpoint,
        )
    )


def _check_three_dm_inspection(
    contract: VaultedUnderpassAssemblyContract,
    snapshot: CadReadbackSnapshot,
    findings: list[CheckFinding],
) -> dict[tuple[str, str], CadBoundingBox]:
    binding = contract.inspection_binding
    if binding is None:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-3dm-inspection-missing",
                FindingSeverity.ERROR,
                "selected vault lacks an immutable saved-3dm inspection binding",
            )
        )
        return {}

    inspection = binding.inspection
    if inspection.file_sha256 != binding.model_sha256:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-3dm-artifact-sha-contradiction",
                FindingSeverity.ERROR,
                "saved 3dm SHA differs from the exact artifact binding",
                binding.ref,
                binding.artifact_ref,
            )
        )
    if inspection.read_only is not True or inspection.rhino_process_started is not False:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-3dm-inspection-mode-contradiction",
                FindingSeverity.ERROR,
                "3dm witness was not produced by the read-only headless inspector",
                binding.ref,
            )
        )

    try:
        geometry_by_id = _inspection_rows_by_object_id(
            inspection.object_geometry_sha256,
            "geometry digest",
        )
        bbox_by_id = _inspection_rows_by_object_id(
            inspection.named_object_bboxes,
            "named bounding box",
        )
        identity_rows: list[
            tuple[tuple[str, str], str, dict[str, object], dict[str, str]]
        ] = []
        for row in inspection.object_user_strings:
            if not isinstance(row, dict) or not isinstance(row.get("object_id"), str):
                raise VaultedUnderpassError(
                    "3dm inspection object identity row is malformed"
                )
            strings = _inspection_user_strings(row)
            has_object = _OBJECT_REF_USER_STRING in strings
            has_operation = _OPERATION_REF_USER_STRING in strings
            if not has_object and not has_operation:
                continue
            if not has_object or not has_operation:
                raise VaultedUnderpassError(
                    "3dm object identity requires object and operation refs"
                )
            object_ref = logical_ref(
                strings[_OBJECT_REF_USER_STRING],
                "3dm object user-string object_ref",
            )
            operation_ref = logical_ref(
                strings[_OPERATION_REF_USER_STRING],
                "3dm object user-string operation_ref",
            )
            identity_rows.append(
                (
                    (object_ref, operation_ref),
                    row["object_id"],
                    row,
                    strings,
                )
            )
    except (TypeError, ValueError, VaultedUnderpassError) as exc:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-3dm-inspection-malformed",
                FindingSeverity.ERROR,
                f"saved 3dm inspection cannot be replayed: {exc}",
                binding.ref,
            )
        )
        return {}

    expected_by_pair = {
        (item.object_ref, item.operation_ref): item for item in snapshot.objects
    }
    expected_object_refs = {item.object_ref for item in snapshot.objects}
    expected_operation_refs = {item.operation_ref for item in snapshot.objects}
    scoped_rows = [
        item
        for item in identity_rows
        if item[0][0] in expected_object_refs
        or item[0][1] in expected_operation_refs
    ]
    scoped_pairs = tuple(item[0] for item in scoped_rows)
    if (
        set(scoped_pairs) != set(expected_by_pair)
        or len(scoped_pairs) != len(expected_by_pair)
    ):
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-3dm-object-universe-contradiction",
                FindingSeverity.ERROR,
                "saved 3dm identities do not exactly realize the snapshot universe",
                binding.ref,
                *expected_object_refs,
                *expected_operation_refs,
            )
        )

    rows_by_pair: dict[
        tuple[str, str],
        list[tuple[str, dict[str, object], dict[str, str]]],
    ] = {}
    for pair, object_id, row, strings in scoped_rows:
        rows_by_pair.setdefault(pair, []).append((object_id, row, strings))
    inspected_bounds: dict[tuple[str, str], CadBoundingBox] = {}
    for pair, readback in expected_by_pair.items():
        matches = rows_by_pair.get(pair, [])
        if len(matches) != 1:
            continue
        object_id, identity_row, strings = matches[0]
        geometry_row = geometry_by_id.get(object_id)
        bbox_row = bbox_by_id.get(object_id)
        observed_bbox = _inspection_bbox(
            bbox_row.get("bbox") if bbox_row is not None else None
        )
        geometry_sha = None if geometry_row is None else geometry_row.get(
            "geometry_sha256"
        )
        valid_geometry_sha = False
        if isinstance(geometry_sha, str):
            try:
                valid_geometry_sha = (
                    require_sha256(
                        geometry_sha,
                        "vaulted-underpass inspected geometry_sha256",
                    )
                    == geometry_sha
                )
            except (TypeError, ValueError):
                valid_geometry_sha = False
        if geometry_row is None or not valid_geometry_sha:
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-3dm-geometry-digest-missing",
                    FindingSeverity.ERROR,
                    "snapshot object lacks an exact inspected geometry digest",
                    binding.ref,
                    readback.object_ref,
                )
            )
        if readback.world_bbox is None or bbox_row is None or not _same_bbox(
            bbox_row.get("bbox") if bbox_row is not None else None,
            readback.world_bbox,
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-3dm-bounds-contradiction",
                    FindingSeverity.ERROR,
                    "snapshot bounds differ from the saved 3dm object bounds",
                    binding.ref,
                    readback.object_ref,
                )
            )
        elif observed_bbox is not None:
            inspected_bounds[pair] = observed_bbox
        inspected_layer = identity_row.get("layer_path")
        if (
            readback.layer_ref is None
            or inspected_layer != readback.layer_ref
            or (geometry_row is not None and geometry_row.get("layer_path") != readback.layer_ref)
            or (bbox_row is not None and bbox_row.get("layer_path") != readback.layer_ref)
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-3dm-layer-contradiction",
                    FindingSeverity.ERROR,
                    "snapshot layer differs from the saved 3dm object layer",
                    binding.ref,
                    readback.object_ref,
                )
            )
        if not set(readback.attributes) <= set(strings.items()):
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-3dm-attributes-contradiction",
                    FindingSeverity.ERROR,
                    "snapshot attributes are absent from saved 3dm user strings",
                    binding.ref,
                    readback.object_ref,
                )
            )
    return inspected_bounds


def check_vaulted_underpass_assembly(
    contract: VaultedUnderpassAssemblyContract,
) -> CheckReceiptEnvelope:
    """Require materialized geometry for every role of an adopted vault."""

    if not isinstance(contract, VaultedUnderpassAssemblyContract):
        raise TypeError("contract must be VaultedUnderpassAssemblyContract")
    findings: list[CheckFinding] = []
    if contract.construction_form is UnderpassConstructionForm.UNKNOWN:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-form-unknown",
                FindingSeverity.UNKNOWN,
                "underpass construction form is unresolved",
            )
        )
    elif contract.construction_form is not UnderpassConstructionForm.VAULTED:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-form-contradiction",
                FindingSeverity.ERROR,
                "vaulted-underpass checker received a non-vaulted form",
            )
        )
    if not contract.form_evidence_refs:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-form-evidence-unknown",
                FindingSeverity.UNKNOWN,
                "selected vaulted form lacks retained evidence",
            )
        )
    if not contract.form_adoption_refs:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-form-adoption-unknown",
                FindingSeverity.UNKNOWN,
                "selected vaulted form lacks an adoption decision",
            )
        )

    bindings = contract.role_bindings_by_role
    missing_roles = _REQUIRED_ROLES - set(bindings)
    for role in sorted(missing_roles, key=lambda item: item.value):
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-role-missing",
                FindingSeverity.ERROR,
                f"selected vault lacks exact geometry/readback for {role.value}",
                _role_requirement_ref(role),
            )
        )

    readback_profile = contract.cad_readback_profile
    readback_snapshot = contract.cad_readback_snapshot
    if readback_profile is None or readback_snapshot is None:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-cad-readback-unknown",
                FindingSeverity.UNKNOWN,
                "selected vault lacks a replayable CAD profile and snapshot",
            )
        )
    else:
        readback_receipt = validate_cad_readback(
            readback_profile,
            readback_snapshot,
            stage_subject_digest=contract.stage_subject_digest,
        )
        _check_receipt_context(
            contract,
            readback_receipt,
            label="cad-readback",
            checker_id=_CAD_READBACK_CHECKER_ID,
            require_stage_subject=True,
            findings=findings,
        )
        expected_pairs = {
            (item.program_object_ref, item.producer_operation_ref)
            for item in contract.role_bindings
        }
        profile_pairs = {
            (item.object_ref, item.operation_ref)
            for item in readback_profile.object_requirements
        }
        snapshot_pairs = {
            (item.object_ref, item.operation_ref)
            for item in readback_snapshot.objects
        }
        if profile_pairs != expected_pairs or snapshot_pairs != expected_pairs:
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-cad-readback-denominator-contradiction",
                    FindingSeverity.ERROR,
                    "CAD profile/snapshot do not exactly equal the six role objects",
                    readback_profile.ref,
                )
            )
        receipt_subjects = set(readback_receipt.subject_refs)
        receipt_coverage = set(readback_receipt.coverage_denominator)
        for binding in contract.role_bindings:
            exact_identity = (
                binding.program_object_ref == binding.readback_object_ref
                and binding.producer_operation_ref
                == binding.readback_operation_ref
            )
            required_refs = {
                binding.program_object_ref,
                binding.producer_operation_ref,
                binding.readback_object_ref,
                binding.readback_operation_ref,
            }
            if (
                not exact_identity
                or not required_refs <= receipt_subjects
                or not required_refs <= receipt_coverage
            ):
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-role-readback-contradiction",
                        FindingSeverity.ERROR,
                        "role program identity is not exactly proven by CAD readback",
                        binding.ref,
                        *required_refs,
                    )
                )

    inspected_bounds_by_pair: dict[tuple[str, str], CadBoundingBox] = {}
    if readback_snapshot is not None:
        inspected_bounds_by_pair = _check_three_dm_inspection(
            contract,
            readback_snapshot,
            findings,
        )
    elif contract.inspection_binding is None:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-3dm-inspection-missing",
                FindingSeverity.ERROR,
                "selected vault lacks an immutable saved-3dm inspection binding",
            )
        )

    profile = contract.assembly_profile
    if profile is None:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-assembly-profile-unknown",
                FindingSeverity.UNKNOWN,
                "selected vault lacks a generic AssemblyProfile",
            )
        )
    elif not missing_roles:
        expected_subject_refs = tuple(sorted(item.ref for item in bindings.values()))
        observed_subject_refs = tuple(
            sorted(item.subject_ref for item in profile.subjects)
        )
        if observed_subject_refs != expected_subject_refs:
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-assembly-subject-denominator-contradiction",
                    FindingSeverity.ERROR,
                    "AssemblyProfile subjects do not exactly match the six role bindings",
                    profile.ref,
                    *expected_subject_refs,
                )
            )
        subjects_by_ref = {item.subject_ref: item for item in profile.subjects}
        readback_by_ref = (
            {
                item.object_ref: item
                for item in contract.cad_readback_snapshot.objects
            }
            if contract.cad_readback_snapshot is not None
            else {}
        )
        for binding in bindings.values():
            subject = subjects_by_ref.get(binding.ref)
            if (
                subject is None
                or subject.bounds is None
                or subject.bounds_basis is None
                or subject.geometry_ref != binding.readback_object_ref
            ):
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-readback-geometry-contradiction",
                        FindingSeverity.ERROR,
                        "role geometry is absent or not bound to its readback object",
                        binding.ref,
                        binding.readback_object_ref,
                    )
                )
                continue
            observed = readback_by_ref.get(binding.readback_object_ref)
            observed_bounds = (
                None
                if observed is None
                else observed.world_bbox or observed.host_local_bbox
            )
            if observed_bounds is not None and (
                subject.bounds.minimum != observed_bounds.minimum
                or subject.bounds.maximum != observed_bounds.maximum
            ):
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-readback-bounds-contradiction",
                        FindingSeverity.ERROR,
                        "AssemblyProfile bounds differ from exact CAD readback",
                        binding.ref,
                        binding.readback_object_ref,
                    )
                )
        role_ref = {role: binding.ref for role, binding in bindings.items()}
        required_relationships = {
            (
                RelationshipKind.SUPPORT,
                (
                    role_ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                    role_ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                ),
            ),
            (
                RelationshipKind.SUPPORT,
                (
                    role_ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                    role_ref[VaultedUnderpassRole.LEFT_SUPPORT],
                ),
            ),
            (
                RelationshipKind.SUPPORT,
                (
                    role_ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                    role_ref[VaultedUnderpassRole.RIGHT_SUPPORT],
                ),
            ),
            (
                RelationshipKind.SUPPORT,
                (
                    role_ref[VaultedUnderpassRole.LEFT_SUPPORT],
                    role_ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
                ),
            ),
            (
                RelationshipKind.SUPPORT,
                (
                    role_ref[VaultedUnderpassRole.RIGHT_SUPPORT],
                    role_ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
                ),
            ),
            (
                RelationshipKind.VERTICAL_SUPPORT_CHAIN,
                (
                    role_ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                    role_ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                    role_ref[VaultedUnderpassRole.LEFT_SUPPORT],
                    role_ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
                ),
            ),
            (
                RelationshipKind.VERTICAL_SUPPORT_CHAIN,
                (
                    role_ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                    role_ref[VaultedUnderpassRole.OVERHEAD_VAULT],
                    role_ref[VaultedUnderpassRole.RIGHT_SUPPORT],
                    role_ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
                ),
            ),
            (
                RelationshipKind.LOAD_PATH_TO_FOUNDATION,
                (
                    role_ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                    role_ref[VaultedUnderpassRole.LOAD_PATH_TERMINAL],
                ),
            ),
            (
                RelationshipKind.OPENING_CLEAR,
                (
                    role_ref[VaultedUnderpassRole.HOST_OPENING],
                    role_ref[VaultedUnderpassRole.STAIR_OR_LANDING_ABOVE],
                ),
            ),
        }
        observed_relationships = {
            (item.kind, item.subject_refs) for item in profile.requirements
        }
        missing_relationships = required_relationships - observed_relationships
        if missing_relationships:
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-functional-relation-missing",
                    FindingSeverity.ERROR,
                    "AssemblyProfile omits a required support, load, or opening relation",
                    profile.ref,
                )
            )
        assembly_receipt = check_assembly(
            profile,
            branch=contract.branch,
            scope_digest=contract.scope_digest,
            stage_subject_digest=contract.stage_subject_digest,
        )
        _check_receipt_context(
            contract,
            assembly_receipt,
            label="assembly",
            checker_id=_ASSEMBLY_RELATIONSHIP_CHECKER_ID,
            require_stage_subject=True,
            findings=findings,
        )

    relation_receipt = contract.relation_realization_receipt
    if relation_receipt is None:
        findings.append(
            _finding(
                contract,
                "vaulted-underpass-relation-realization-unknown",
                FindingSeverity.UNKNOWN,
                "selected vault lacks a generic relation-realization receipt",
            )
        )
    else:
        _check_receipt_context(
            contract,
            relation_receipt,
            label="relation-realization",
            checker_id=RELATION_REALIZATION_CHECKER_ID,
            require_stage_subject=True,
            findings=findings,
        )
        required_relation_refs = {
            ref
            for binding in contract.role_bindings
            for ref in (
                binding.relation_binding_ref,
                binding.readback_object_ref,
                binding.readback_operation_ref,
            )
        }
        if not required_relation_refs <= set(relation_receipt.subject_refs) or not (
            required_relation_refs <= set(relation_receipt.coverage_denominator)
        ):
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-relation-denominator-contradiction",
                    FindingSeverity.ERROR,
                    "relation receipt omits role-to-readback identities",
                    _receipt_ref(relation_receipt),
                    *required_relation_refs,
                )
            )

    interface_by_role = {
        item.support_role: item for item in contract.interface_bindings
    }
    for support_role in sorted(
        _REQUIRED_INTERFACE_SUPPORT_ROLES,
        key=lambda item: item.value,
    ):
        interface = interface_by_role.get(support_role)
        if interface is None:
            findings.append(
                _finding(
                    contract,
                    "vaulted-underpass-interface-witness-unknown",
                    FindingSeverity.UNKNOWN,
                    f"vault-to-{support_role.value} boundary continuity is unresolved",
                    _role_requirement_ref(support_role),
                )
            )
            continue
        _check_receipt_context(
            contract,
            interface.receipt,
            label=f"interface-{support_role.value}",
            checker_id=_INTERFACE_CONTINUITY_CHECKER_ID,
            require_stage_subject=True,
            findings=findings,
        )
        if not missing_roles:
            vault_binding = bindings[VaultedUnderpassRole.OVERHEAD_VAULT]
            support_binding = bindings[support_role]
            expected_evidence_refs = tuple(
                sorted((vault_binding.ref, support_binding.ref))
            )
            expected_binding_refs = tuple(
                sorted(
                    (
                        vault_binding.readback_object_ref,
                        vault_binding.readback_operation_ref,
                        support_binding.readback_object_ref,
                        support_binding.readback_operation_ref,
                    )
                )
            )
            vault_pair = (
                vault_binding.readback_object_ref,
                vault_binding.readback_operation_ref,
            )
            support_pair = (
                support_binding.readback_object_ref,
                support_binding.readback_operation_ref,
            )
            vault_bounds = inspected_bounds_by_pair.get(vault_pair)
            support_bounds = inspected_bounds_by_pair.get(support_pair)
            if vault_bounds is None or support_bounds is None:
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-interface-inspected-geometry-missing",
                        FindingSeverity.ERROR,
                        "interface lacks inspected vault or support geometry bounds",
                        interface.ref,
                        vault_binding.readback_object_ref,
                        support_binding.readback_object_ref,
                    )
                )
            else:
                required_endpoints_inside = all(
                    _bbox_contains_endpoint(
                        vault_bounds,
                        endpoint,
                        interface.tolerance,
                    )
                    for segment in interface.required_segments
                    for endpoint in (segment.start, segment.end)
                )
                supporting_endpoints_inside = all(
                    _bbox_contains_endpoint(
                        support_bounds,
                        endpoint,
                        interface.tolerance,
                    )
                    for segment in interface.supporting_segments
                    for endpoint in (segment.start, segment.end)
                )
                if not required_endpoints_inside or not supporting_endpoints_inside:
                    findings.append(
                        _finding(
                            contract,
                            "vaulted-underpass-interface-geometry-contradiction",
                            FindingSeverity.ERROR,
                            "interface segment endpoints fall outside their "
                            "inspected role geometry",
                            interface.ref,
                            interface.vault_boundary_ref,
                            interface.support_boundary_ref,
                            vault_binding.readback_object_ref,
                            support_binding.readback_object_ref,
                        )
                    )
            try:
                replayed_interface_receipt = check_interface_boundary_continuity(
                    check_id=interface.check_id,
                    branch=contract.branch,
                    scope_digest=contract.scope_digest,
                    required_segments=interface.required_segments,
                    supporting_segments=interface.supporting_segments,
                    support_sets=interface.support_sets,
                    expected_required_refs=interface.expected_required_refs,
                    tolerance=interface.tolerance,
                    evidence_refs=interface.evidence_refs,
                    authority_refs=interface.authority_refs,
                    stage_subject_digest=contract.stage_subject_digest,
                    binding_refs=expected_binding_refs,
                )
            except (InterfaceContinuityError, TypeError, ValueError) as exc:
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-interface-replay-failed",
                        FindingSeverity.ERROR,
                        f"interface inputs cannot be replayed: {exc}",
                        interface.ref,
                    )
                )
                replayed_interface_receipt = None
            if (
                replayed_interface_receipt is not None
                and replayed_interface_receipt.receipt_digest
                != interface.receipt.receipt_digest
            ):
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-interface-receipt-contradiction",
                        FindingSeverity.ERROR,
                        "stored interface receipt differs from replayed raw inputs",
                        interface.ref,
                        _receipt_ref(interface.receipt),
                    )
                )
            if interface.evidence_refs != expected_evidence_refs:
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-interface-binding-contradiction",
                        FindingSeverity.ERROR,
                        "interface inputs are not bound to vault and support roles",
                        interface.ref,
                        *expected_evidence_refs,
                    )
                )
            if interface.authority_refs != contract.form_adoption_refs:
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-interface-authority-contradiction",
                        FindingSeverity.ERROR,
                        "interface inputs differ from the adopted underpass authority",
                        interface.ref,
                        *contract.form_adoption_refs,
                    )
                )
            required_interface_refs = {
                interface.vault_boundary_ref,
                interface.support_boundary_ref,
                *expected_binding_refs,
            }
            if not required_interface_refs <= set(
                interface.receipt.subject_refs
            ) or not required_interface_refs <= set(
                interface.receipt.coverage_denominator
            ):
                findings.append(
                    _finding(
                        contract,
                        "vaulted-underpass-interface-denominator-contradiction",
                        FindingSeverity.ERROR,
                        "interface receipt omits exact boundaries or readback objects",
                        interface.ref,
                        *required_interface_refs,
                    )
                )

    if any(item.severity is FindingSeverity.ERROR for item in findings):
        status = CheckStatus.FAIL
    elif any(item.severity is FindingSeverity.UNKNOWN for item in findings):
        status = CheckStatus.UNKNOWN
    else:
        status = CheckStatus.PASS
    denominator = contract.denominator_refs
    ordered_findings = tuple(
        sorted(findings, key=lambda item: (item.code, item.subject_refs, item.message))
    )
    return CheckReceiptEnvelope(
        check_id=f"vaulted-underpass-{contract.contract_digest[:24]}",
        checker_id=UNDERPASS_ASSEMBLY_CHECKER_ID,
        checker_version="1.0.0",
        branch=contract.branch,
        scope_digest=contract.scope_digest,
        subject_refs=denominator,
        subject_digest=contract.stage_subject_digest,
        status=status,
        adoption_refs=contract.form_adoption_refs,
        source_refs=contract.source_refs,
        authority_refs=contract.form_adoption_refs,
        findings=ordered_findings,
        coverage_denominator=denominator,
        covered_refs=(denominator if status in {CheckStatus.PASS, CheckStatus.FAIL} else ()),
    )


__all__ = [
    "UNDERPASS_ASSEMBLY_CHECKER_ID",
    "UnderpassConstructionForm",
    "VaultedUnderpassAssemblyContract",
    "VaultedUnderpassError",
    "VaultedUnderpassInspectionBinding",
    "VaultedUnderpassInterfaceBinding",
    "VaultedUnderpassRole",
    "VaultedUnderpassRoleBinding",
    "check_vaulted_underpass_assembly",
]
