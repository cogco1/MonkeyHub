"""Validate component-family manifests against exact neutral geometry values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.realization.sandbox import (
    HybridScene,
    RealizationStatus,
    SandboxRealizationReceipt,
)
from archflow.runtime.geometry_compiler import CompiledGeometryProgram
from archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleReceipt,
    SemanticGeometryLifecycleStatus,
)
from archflow.state.component_family import (
    ComponentFamilyInstance,
    ComponentFamilyKind,
    ComponentFamilySet,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import (
    GeometryOperationKind,
    digest_value,
    require_sha256,
)


class FamilyCompileStatus(StrEnum):
    COMPILED = "compiled"
    REJECTED = "rejected"


class FamilyRealizationStatus(StrEnum):
    REALIZED = "realized"
    REJECTED = "rejected"


class FamilyIssueCode(StrEnum):
    EXACT_PROJECT_MISMATCH = "exact_project_mismatch"
    UNKNOWN_COMPONENT = "unknown_component"
    UNKNOWN_GEOMETRY_REF = "unknown_geometry_ref"
    SEMANTIC_OWNER_MISMATCH = "semantic_owner_mismatch"
    PARAMETER_MISMATCH = "parameter_mismatch"
    ASSET_MISMATCH = "asset_mismatch"
    UNIT_OR_SCALE_MISMATCH = "unit_or_scale_mismatch"
    INTERFACE_MISMATCH = "interface_mismatch"
    SOCKET_MISMATCH = "socket_mismatch"
    ANCHOR_MISMATCH = "anchor_mismatch"
    DEPENDENCY_MISMATCH = "dependency_mismatch"
    KIND_MISMATCH = "kind_mismatch"
    LIFECYCLE_MISMATCH = "lifecycle_mismatch"
    IMMUTABLE_REVISION_CHANGED = "immutable_revision_changed"
    LOCAL_DEPENDENCY_NOT_INVALIDATED = "local_dependency_not_invalidated"
    REALIZATION_MISMATCH = "realization_mismatch"


class FamilyInstanceDisposition(StrEnum):
    PRESERVED = "preserved"
    REVISED = "revised"
    REPLACED = "replaced"
    RETIRED = "retired"
    ADDED = "added"


@dataclass(frozen=True, slots=True)
class FamilyCompileIssue:
    code: FamilyIssueCode
    subject_id: str
    detail: str
    component_ids: tuple[str, ...] = ()
    geometry_object_ids: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    SCHEMA = "ComponentFamilyCompileIssue@1"

    def __post_init__(self) -> None:
        if not isinstance(self.code, FamilyIssueCode):
            raise TypeError("code must be FamilyIssueCode")
        require_identifier(self.subject_id, "issue subject_id")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("issue detail must be non-empty")
        _ids(self.component_ids, "issue component_ids", allow_empty=True)
        _ids(
            self.geometry_object_ids,
            "issue geometry_object_ids",
            allow_empty=True,
        )
        _refs(self.source_refs, "issue source_refs", allow_empty=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "code": self.code.value,
            "subject_id": self.subject_id,
            "detail": self.detail,
            "component_ids": list(self.component_ids),
            "geometry_object_ids": list(self.geometry_object_ids),
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> FamilyCompileIssue:
        payload = _mapping(value, "family compile issue")
        _exact(
            payload,
            {
                "schema",
                "code",
                "subject_id",
                "detail",
                "component_ids",
                "geometry_object_ids",
                "source_refs",
            },
            "issue",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("family issue schema changed")
        return cls(
            code=FamilyIssueCode(payload["code"]),
            subject_id=payload["subject_id"],
            detail=payload["detail"],
            component_ids=_string_tuple(
                payload["component_ids"], "component_ids"
            ),
            geometry_object_ids=_string_tuple(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            source_refs=_string_tuple(payload["source_refs"], "source_refs"),
        )


@dataclass(frozen=True, slots=True)
class CompiledFamilyInstance:
    family_instance_id: str
    component_id: str
    instance_digest: str
    definition_digest: str
    object_digests: tuple[tuple[str, str], ...]
    parameter_digests: tuple[tuple[str, str], ...]
    asset_digests: tuple[tuple[str, str], ...]
    interface_refs: tuple[str, ...]
    dependency_component_ids: tuple[str, ...]

    SCHEMA = "CompiledComponentFamilyInstance@1"

    def __post_init__(self) -> None:
        require_identifier(self.family_instance_id, "family_instance_id")
        require_identifier(self.component_id, "component_id")
        for value, field in (
            (self.instance_digest, "instance_digest"),
            (self.definition_digest, "definition_digest"),
        ):
            require_sha256(value, field)
        _digest_pairs(self.object_digests, "object_digests")
        _digest_pairs(self.parameter_digests, "parameter_digests")
        _digest_pairs(self.asset_digests, "asset_digests")
        _refs(self.interface_refs, "interface_refs")
        _ids(
            self.dependency_component_ids,
            "dependency_component_ids",
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "family_instance_id": self.family_instance_id,
            "component_id": self.component_id,
            "instance_digest": self.instance_digest,
            "definition_digest": self.definition_digest,
            "object_digests": _pairs_to_dict(self.object_digests),
            "parameter_digests": _pairs_to_dict(self.parameter_digests),
            "asset_digests": _pairs_to_dict(self.asset_digests),
            "interface_refs": list(self.interface_refs),
            "dependency_component_ids": list(self.dependency_component_ids),
        }

    @classmethod
    def from_dict(cls, value: object) -> CompiledFamilyInstance:
        payload = _mapping(value, "compiled family instance")
        _exact(
            payload,
            {
                "schema",
                "family_instance_id",
                "component_id",
                "instance_digest",
                "definition_digest",
                "object_digests",
                "parameter_digests",
                "asset_digests",
                "interface_refs",
                "dependency_component_ids",
            },
            "compiled family instance",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("compiled family instance schema changed")
        return cls(
            family_instance_id=payload["family_instance_id"],
            component_id=payload["component_id"],
            instance_digest=payload["instance_digest"],
            definition_digest=payload["definition_digest"],
            object_digests=_pairs_from_dict(payload["object_digests"]),
            parameter_digests=_pairs_from_dict(payload["parameter_digests"]),
            asset_digests=_pairs_from_dict(payload["asset_digests"]),
            interface_refs=_string_tuple(payload["interface_refs"], "interface_refs"),
            dependency_component_ids=_string_tuple(
                payload["dependency_component_ids"], "dependency_component_ids"
            ),
        )


@dataclass(frozen=True, slots=True)
class ComponentFamilyCompilationReceipt:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    family_set_digest: str
    design_state_digest: str
    component_tree_digest: str
    geometry_program_digest: str
    status: FamilyCompileStatus
    compiled_instances: tuple[CompiledFamilyInstance, ...]
    issues: tuple[FamilyCompileIssue, ...]

    SCHEMA = "ComponentFamilyCompilationReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("family receipt and base disagree")
        self.base.require_digest()
        for value, field in (
            (self.family_set_digest, "family_set_digest"),
            (self.design_state_digest, "design_state_digest"),
            (self.component_tree_digest, "component_tree_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
        ):
            require_sha256(value, field)
        if not isinstance(self.status, FamilyCompileStatus):
            raise TypeError("status must be FamilyCompileStatus")
        _typed_sorted(
            self.compiled_instances,
            CompiledFamilyInstance,
            "compiled_instances",
            "family_instance_id",
            allow_empty=True,
        )
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, FamilyCompileIssue) for item in self.issues
        ):
            raise TypeError("issues contains an invalid item")
        if self.status is FamilyCompileStatus.COMPILED:
            if self.issues:
                raise ValueError("compiled family receipt is incomplete")
        elif self.compiled_instances or not self.issues:
            raise ValueError("rejected family receipt is inconsistent")

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "family_set_digest": self.family_set_digest,
            "design_state_digest": self.design_state_digest,
            "component_tree_digest": self.component_tree_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "status": self.status.value,
            "compiled_instances": [
                item.to_dict() for item in self.compiled_instances
            ],
            "issues": [item.to_dict() for item in self.issues],
            "geometry_generation_authority": False,
            "hard_gate_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentFamilyCompilationReceipt:
        payload = _mapping(value, "family compilation receipt")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "family_set_digest",
                "design_state_digest",
                "component_tree_digest",
                "geometry_program_digest",
                "status",
                "compiled_instances",
                "issues",
                "geometry_generation_authority",
                "hard_gate_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "family compilation receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["geometry_generation_authority"] is not False
            or payload["hard_gate_authority"] is not False
            or payload["persistence_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ValueError("family compilation authority changed")
        compiled = _list(payload["compiled_instances"], "compiled_instances")
        issues = _list(payload["issues"], "issues")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            family_set_digest=payload["family_set_digest"],
            design_state_digest=payload["design_state_digest"],
            component_tree_digest=payload["component_tree_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            status=FamilyCompileStatus(payload["status"]),
            compiled_instances=tuple(
                CompiledFamilyInstance.from_dict(item) for item in compiled
            ),
            issues=tuple(FamilyCompileIssue.from_dict(item) for item in issues),
        )


@dataclass(frozen=True, slots=True)
class ComponentFamilyRealizationReceipt:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    family_compilation_receipt_digest: str
    family_set_digest: str
    geometry_program_digest: str
    scene_digest: str | None
    sandbox_realization_receipt_digest: str
    family_instance_ids: tuple[str, ...]
    geometry_object_ids: tuple[str, ...]
    status: FamilyRealizationStatus
    issues: tuple[FamilyCompileIssue, ...]

    SCHEMA = "ComponentFamilyRealizationReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ValueError("family realization and base disagree")
        self.base.require_digest()
        for value, field in (
            (
                self.family_compilation_receipt_digest,
                "family_compilation_receipt_digest",
            ),
            (self.family_set_digest, "family_set_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
            (
                self.sandbox_realization_receipt_digest,
                "sandbox_realization_receipt_digest",
            ),
        ):
            require_sha256(value, field)
        if self.scene_digest is not None:
            require_sha256(self.scene_digest, "scene_digest")
        _ids(self.family_instance_ids, "family_instance_ids", allow_empty=True)
        _ids(self.geometry_object_ids, "geometry_object_ids", allow_empty=True)
        if not isinstance(self.status, FamilyRealizationStatus):
            raise TypeError("status must be FamilyRealizationStatus")
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, FamilyCompileIssue) for item in self.issues
        ):
            raise TypeError("issues contains an invalid item")
        if self.status is FamilyRealizationStatus.REALIZED:
            if self.scene_digest is None or self.issues:
                raise ValueError("realized family receipt is incomplete")
        elif self.scene_digest is not None or not self.issues:
            raise ValueError("rejected family realization is inconsistent")

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "family_compilation_receipt_digest": (
                self.family_compilation_receipt_digest
            ),
            "family_set_digest": self.family_set_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "scene_digest": self.scene_digest,
            "sandbox_realization_receipt_digest": (
                self.sandbox_realization_receipt_digest
            ),
            "family_instance_ids": list(self.family_instance_ids),
            "geometry_object_ids": list(self.geometry_object_ids),
            "status": self.status.value,
            "issues": [item.to_dict() for item in self.issues],
            "external_platform_required": False,
            "geometry_mutation_authority": False,
            "review_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentFamilyRealizationReceipt:
        payload = _mapping(value, "family realization receipt")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "family_compilation_receipt_digest",
                "family_set_digest",
                "geometry_program_digest",
                "scene_digest",
                "sandbox_realization_receipt_digest",
                "family_instance_ids",
                "geometry_object_ids",
                "status",
                "issues",
                "external_platform_required",
                "geometry_mutation_authority",
                "review_authority",
                "canonical_write_authority",
            },
            "family realization receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["external_platform_required"] is not False
            or payload["geometry_mutation_authority"] is not False
            or payload["review_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ValueError("family realization authority changed")
        issues = _list(payload["issues"], "issues")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            family_compilation_receipt_digest=payload[
                "family_compilation_receipt_digest"
            ],
            family_set_digest=payload["family_set_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            scene_digest=payload["scene_digest"],
            sandbox_realization_receipt_digest=payload[
                "sandbox_realization_receipt_digest"
            ],
            family_instance_ids=_string_tuple(
                payload["family_instance_ids"], "family_instance_ids"
            ),
            geometry_object_ids=_string_tuple(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            status=FamilyRealizationStatus(payload["status"]),
            issues=tuple(FamilyCompileIssue.from_dict(item) for item in issues),
        )


@dataclass(frozen=True, slots=True)
class FamilyInstanceTransition:
    component_id: str
    disposition: FamilyInstanceDisposition
    predecessor_instance_id: str | None
    predecessor_instance_digest: str | None
    current_instance_id: str | None
    current_instance_digest: str | None

    SCHEMA = "ComponentFamilyInstanceTransition@1"

    def __post_init__(self) -> None:
        require_identifier(self.component_id, "transition component_id")
        if not isinstance(self.disposition, FamilyInstanceDisposition):
            raise TypeError("disposition must be FamilyInstanceDisposition")
        for value, field in (
            (self.predecessor_instance_id, "predecessor_instance_id"),
            (self.current_instance_id, "current_instance_id"),
        ):
            if value is not None:
                require_identifier(value, field)
        for value, field in (
            (self.predecessor_instance_digest, "predecessor_instance_digest"),
            (self.current_instance_digest, "current_instance_digest"),
        ):
            if value is not None:
                require_sha256(value, field)
        if (self.predecessor_instance_id is None) != (
            self.predecessor_instance_digest is None
        ):
            raise ValueError("predecessor transition identity is incomplete")
        if (self.current_instance_id is None) != (
            self.current_instance_digest is None
        ):
            raise ValueError("current transition identity is incomplete")
        if self.disposition is FamilyInstanceDisposition.ADDED:
            if (
                self.predecessor_instance_id is not None
                or self.current_instance_id is None
            ):
                raise ValueError("added family transition is inconsistent")
        elif self.disposition is FamilyInstanceDisposition.RETIRED:
            if (
                self.predecessor_instance_id is None
                or self.current_instance_id is not None
            ):
                raise ValueError("retired family transition is inconsistent")
        elif (
            self.predecessor_instance_id is None
            or self.current_instance_id is None
        ):
            raise ValueError("family transition needs both instances")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component_id": self.component_id,
            "disposition": self.disposition.value,
            "predecessor_instance_id": self.predecessor_instance_id,
            "predecessor_instance_digest": self.predecessor_instance_digest,
            "current_instance_id": self.current_instance_id,
            "current_instance_digest": self.current_instance_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> FamilyInstanceTransition:
        payload = _mapping(value, "family instance transition")
        _exact(
            payload,
            {
                "schema",
                "component_id",
                "disposition",
                "predecessor_instance_id",
                "predecessor_instance_digest",
                "current_instance_id",
                "current_instance_digest",
            },
            "family instance transition",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ValueError("family transition schema changed")
        return cls(
            component_id=payload["component_id"],
            disposition=FamilyInstanceDisposition(payload["disposition"]),
            predecessor_instance_id=payload["predecessor_instance_id"],
            predecessor_instance_digest=payload["predecessor_instance_digest"],
            current_instance_id=payload["current_instance_id"],
            current_instance_digest=payload["current_instance_digest"],
        )


@dataclass(frozen=True, slots=True)
class ComponentFamilyLifecycleReceipt:
    project_id: str
    run_id: str
    predecessor_family_set_digest: str
    current_family_set_digest: str
    predecessor_program_digest: str
    current_program_digest: str
    semantic_lifecycle_receipt_digest: str
    status: FamilyCompileStatus
    transitions: tuple[FamilyInstanceTransition, ...]
    issues: tuple[FamilyCompileIssue, ...]

    SCHEMA = "ComponentFamilyLifecycleReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        for value, field in (
            (self.predecessor_family_set_digest, "predecessor_family_set_digest"),
            (self.current_family_set_digest, "current_family_set_digest"),
            (self.predecessor_program_digest, "predecessor_program_digest"),
            (self.current_program_digest, "current_program_digest"),
            (
                self.semantic_lifecycle_receipt_digest,
                "semantic_lifecycle_receipt_digest",
            ),
        ):
            require_sha256(value, field)
        if not isinstance(self.status, FamilyCompileStatus):
            raise TypeError("status must be FamilyCompileStatus")
        _typed_sorted(
            self.transitions,
            FamilyInstanceTransition,
            "transitions",
            "component_id",
            allow_empty=True,
        )
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, FamilyCompileIssue) for item in self.issues
        ):
            raise TypeError("issues contains an invalid item")
        if self.status is FamilyCompileStatus.COMPILED:
            if not self.transitions or self.issues:
                raise ValueError("compiled family lifecycle is incomplete")
        elif self.transitions or not self.issues:
            raise ValueError("rejected family lifecycle is inconsistent")

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "predecessor_family_set_digest": self.predecessor_family_set_digest,
            "current_family_set_digest": self.current_family_set_digest,
            "predecessor_program_digest": self.predecessor_program_digest,
            "current_program_digest": self.current_program_digest,
            "semantic_lifecycle_receipt_digest": (
                self.semantic_lifecycle_receipt_digest
            ),
            "status": self.status.value,
            "transitions": [item.to_dict() for item in self.transitions],
            "issues": [item.to_dict() for item in self.issues],
            "successor_authority": False,
            "geometry_mutation_authority": False,
            "review_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentFamilyLifecycleReceipt:
        payload = _mapping(value, "family lifecycle receipt")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "predecessor_family_set_digest",
                "current_family_set_digest",
                "predecessor_program_digest",
                "current_program_digest",
                "semantic_lifecycle_receipt_digest",
                "status",
                "transitions",
                "issues",
                "successor_authority",
                "geometry_mutation_authority",
                "review_authority",
                "canonical_write_authority",
            },
            "family lifecycle receipt",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["successor_authority"] is not False
            or payload["geometry_mutation_authority"] is not False
            or payload["review_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise ValueError("family lifecycle authority changed")
        transitions = _list(payload["transitions"], "transitions")
        issues = _list(payload["issues"], "issues")
        return cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            predecessor_family_set_digest=payload["predecessor_family_set_digest"],
            current_family_set_digest=payload["current_family_set_digest"],
            predecessor_program_digest=payload["predecessor_program_digest"],
            current_program_digest=payload["current_program_digest"],
            semantic_lifecycle_receipt_digest=payload[
                "semantic_lifecycle_receipt_digest"
            ],
            status=FamilyCompileStatus(payload["status"]),
            transitions=tuple(
                FamilyInstanceTransition.from_dict(item) for item in transitions
            ),
            issues=tuple(FamilyCompileIssue.from_dict(item) for item in issues),
        )


def compile_component_families(
    design_state: DevelopedDesignState,
    geometry_program: CompiledGeometryProgram,
    family_set: ComponentFamilySet,
) -> ComponentFamilyCompilationReceipt:
    """Check that family annotations describe the existing exact geometry."""

    if not isinstance(design_state, DevelopedDesignState):
        raise TypeError("design_state must be DevelopedDesignState")
    if not isinstance(geometry_program, CompiledGeometryProgram):
        raise TypeError("geometry_program must be CompiledGeometryProgram")
    if not isinstance(family_set, ComponentFamilySet):
        raise TypeError("family_set must be ComponentFamilySet")
    issues: list[FamilyCompileIssue] = []
    proposal = geometry_program.proposal
    component_proposal = design_state.selected_schematic.option.proposal
    if (
        (family_set.project_id, family_set.run_id, family_set.base)
        != (design_state.project_id, design_state.run_id, design_state.base)
        or proposal.project_id != design_state.project_id
        or proposal.run_id != design_state.run_id
        or proposal.base != design_state.base
        or proposal.design_state_digest != design_state.state_digest
        or family_set.design_state_digest != design_state.state_digest
        or family_set.component_tree_digest != component_proposal.proposal_digest
        or family_set.geometry_program_digest != geometry_program.program_digest
    ):
        issues.append(
            _issue(
                FamilyIssueCode.EXACT_PROJECT_MISMATCH,
                "family-set",
                "family set state component tree and geometry are not one exact value",
            )
        )
    component_ids = {item.component_id for item in component_proposal.components}
    bindings = {item.binding_id: item for item in proposal.semantic_bindings}
    operations = {item.op_id: item for item in proposal.operations}
    assemblies = {item.assembly_id: item for item in proposal.assemblies}
    assets = {item.asset_id: item for item in proposal.assets}
    objects = {item.object_id: item.object_digest for item in geometry_program.objects}
    producer = {
        object_id: operation
        for operation in proposal.operations
        for object_id in operation.output_object_ids
    }
    object_owner = {
        object_id: binding.component_id
        for binding in proposal.semantic_bindings
        for object_id in binding.object_ids
    }
    family_sockets = {
        (socket.object_id, socket.socket_id): socket
        for instance in family_set.instances
        for socket in instance.sockets
    }
    assembly_host_sockets = {
        (item.host_object_id, item.host_socket_id) for item in proposal.assemblies
    }
    compiled: list[CompiledFamilyInstance] = []
    for instance in family_set.instances:
        instance_issues, result = _compile_instance(
            instance,
            family_set,
            component_ids,
            bindings,
            operations,
            assemblies,
            assets,
            objects,
            producer,
            object_owner,
            family_sockets,
            assembly_host_sockets,
            {item.frame_id for item in proposal.frames},
            proposal.length_unit,
        )
        local_object_ids = tuple(
            sorted(
                object_id
                for operation_id in instance.operation_ids
                if operation_id in operations
                for object_id in operations[operation_id].output_object_ids
            )
        )
        issues.extend(
            FamilyCompileIssue(
                code=item.code,
                subject_id=item.subject_id,
                detail=item.detail,
                component_ids=(instance.component_id,),
                geometry_object_ids=local_object_ids,
                source_refs=instance.provenance_refs,
            )
            for item in instance_issues
        )
        if result is not None:
            compiled.append(result)
    ordered_issues = tuple(
        sorted(issues, key=lambda item: (item.code.value, item.subject_id))
    )
    base = {
        "project_id": family_set.project_id,
        "run_id": family_set.run_id,
        "base": family_set.base,
        "family_set_digest": family_set.family_set_digest,
        "design_state_digest": family_set.design_state_digest,
        "component_tree_digest": family_set.component_tree_digest,
        "geometry_program_digest": family_set.geometry_program_digest,
    }
    if ordered_issues:
        return ComponentFamilyCompilationReceipt(
            **base,
            status=FamilyCompileStatus.REJECTED,
            compiled_instances=(),
            issues=ordered_issues,
        )
    return ComponentFamilyCompilationReceipt(
        **base,
        status=FamilyCompileStatus.COMPILED,
        compiled_instances=tuple(
            sorted(compiled, key=lambda item: item.family_instance_id)
        ),
        issues=(),
    )


def bind_component_family_realization(
    compilation: ComponentFamilyCompilationReceipt,
    scene: HybridScene | None,
    sandbox_receipt: SandboxRealizationReceipt,
) -> ComponentFamilyRealizationReceipt:
    """Bind compiled family annotations to one exact sandbox realization.

    This receipt does not realize or mutate geometry.  It proves that the
    family objects already validated against the neutral program are present
    unchanged in the scene named by the sandbox receipt.
    """

    if not isinstance(compilation, ComponentFamilyCompilationReceipt):
        raise TypeError("compilation must be ComponentFamilyCompilationReceipt")
    if scene is not None and not isinstance(scene, HybridScene):
        raise TypeError("scene must be HybridScene or None")
    if not isinstance(sandbox_receipt, SandboxRealizationReceipt):
        raise TypeError("sandbox_receipt must be SandboxRealizationReceipt")

    family_instance_ids = tuple(
        item.family_instance_id for item in compilation.compiled_instances
    )
    expected_objects = {
        object_id: object_digest
        for instance in compilation.compiled_instances
        for object_id, object_digest in instance.object_digests
    }
    geometry_object_ids = tuple(sorted(expected_objects))
    issues: list[FamilyCompileIssue] = []

    if compilation.status is not FamilyCompileStatus.COMPILED:
        issues.append(
            _issue(
                FamilyIssueCode.REALIZATION_MISMATCH,
                "family-compilation",
                "rejected family compilation cannot bind a realization",
            )
        )
    if sandbox_receipt.status is not RealizationStatus.REALIZED:
        issues.append(
            _issue(
                FamilyIssueCode.REALIZATION_MISMATCH,
                "sandbox-receipt",
                "sandbox realization was rejected",
            )
        )
    if sandbox_receipt.geometry_program_digest != compilation.geometry_program_digest:
        issues.append(
            _issue(
                FamilyIssueCode.REALIZATION_MISMATCH,
                "sandbox-receipt",
                "sandbox receipt names a different geometry program",
            )
        )
    if scene is None:
        issues.append(
            _issue(
                FamilyIssueCode.REALIZATION_MISMATCH,
                "sandbox-scene",
                "family realization requires the exact sandbox scene",
            )
        )
    else:
        if (
            scene.project_id != compilation.project_id
            or scene.run_id != compilation.run_id
            or scene.base != compilation.base
            or scene.geometry_program_digest
            != compilation.geometry_program_digest
            or scene.workspace_id != sandbox_receipt.workspace_id
            or sandbox_receipt.scene_digest != scene.scene_digest
        ):
            issues.append(
                _issue(
                    FamilyIssueCode.REALIZATION_MISMATCH,
                    "sandbox-scene",
                    "scene identity does not match the compiled family and sandbox receipt",
                )
            )
        scene_objects = {item.object_id: item for item in scene.objects}
        missing_or_changed = tuple(
            sorted(
                object_id
                for object_id, object_digest in expected_objects.items()
                if object_id not in scene_objects
                or scene_objects[object_id].source_object_digest != object_digest
            )
        )
        if missing_or_changed:
            issues.append(
                FamilyCompileIssue(
                    code=FamilyIssueCode.REALIZATION_MISMATCH,
                    subject_id="family-geometry",
                    detail=(
                        "compiled family geometry is missing or changed in the scene"
                    ),
                    component_ids=tuple(
                        sorted(
                            instance.component_id
                            for instance in compilation.compiled_instances
                            if set(dict(instance.object_digests))
                            & set(missing_or_changed)
                        )
                    ),
                    geometry_object_ids=missing_or_changed,
                )
            )

    ordered_issues = tuple(
        sorted(issues, key=lambda item: (item.code.value, item.subject_id))
    )
    common = {
        "project_id": compilation.project_id,
        "run_id": compilation.run_id,
        "base": compilation.base,
        "family_compilation_receipt_digest": compilation.receipt_digest,
        "family_set_digest": compilation.family_set_digest,
        "geometry_program_digest": compilation.geometry_program_digest,
        "sandbox_realization_receipt_digest": sandbox_receipt.receipt_digest,
        "family_instance_ids": family_instance_ids,
        "geometry_object_ids": geometry_object_ids,
    }
    if ordered_issues:
        return ComponentFamilyRealizationReceipt(
            **common,
            scene_digest=None,
            status=FamilyRealizationStatus.REJECTED,
            issues=ordered_issues,
        )
    return ComponentFamilyRealizationReceipt(
        **common,
        scene_digest=scene.scene_digest,
        status=FamilyRealizationStatus.REALIZED,
        issues=(),
    )


def _compile_instance(
    instance: ComponentFamilyInstance,
    family_set: ComponentFamilySet,
    component_ids: set[str],
    bindings: Mapping[str, object],
    operations: Mapping[str, object],
    assemblies: Mapping[str, object],
    assets: Mapping[str, object],
    objects: Mapping[str, str],
    producer: Mapping[str, object],
    object_owner: Mapping[str, str],
    family_sockets: Mapping[tuple[str, str], object],
    assembly_host_sockets: set[tuple[str, str]],
    frame_ids: set[str],
    program_unit: object,
) -> tuple[list[FamilyCompileIssue], CompiledFamilyInstance | None]:
    issues: list[FamilyCompileIssue] = []
    subject = instance.family_instance_id
    if instance.component_id not in component_ids:
        issues.append(
            _issue(FamilyIssueCode.UNKNOWN_COMPONENT, subject, "component is absent")
        )
    if not set(instance.dependency_component_ids) <= component_ids:
        issues.append(
            _issue(
                FamilyIssueCode.DEPENDENCY_MISMATCH,
                subject,
                "family dependency names an unknown component",
            )
        )
    if instance.frame_id not in frame_ids:
        issues.append(
            _issue(FamilyIssueCode.UNKNOWN_GEOMETRY_REF, subject, "frame is absent")
        )
    unknown_bindings = set(instance.semantic_binding_ids) - set(bindings)
    unknown_operations = set(instance.operation_ids) - set(operations)
    unknown_assemblies = set(instance.assembly_ids) - set(assemblies)
    unknown_assets = set(instance.asset_ids) - set(assets)
    if unknown_bindings or unknown_operations or unknown_assemblies or unknown_assets:
        issues.append(
            _issue(
                FamilyIssueCode.UNKNOWN_GEOMETRY_REF,
                subject,
                "family names an unknown binding operation assembly or asset",
            )
        )
        return issues, None
    selected_bindings = [bindings[item] for item in instance.semantic_binding_ids]
    selected_operations = [operations[item] for item in instance.operation_ids]
    selected_assemblies = [assemblies[item] for item in instance.assembly_ids]
    selected_assets = [assets[item] for item in instance.asset_ids]
    if any(item.component_id != instance.component_id for item in selected_bindings):
        issues.append(
            _issue(
                FamilyIssueCode.SEMANTIC_OWNER_MISMATCH,
                subject,
                "family binding belongs to another semantic component",
            )
        )
    binding_ids = set(instance.semantic_binding_ids)
    if any(
        not set(item.semantic_binding_ids) <= binding_ids
        for item in (*selected_operations, *selected_assemblies)
    ):
        issues.append(
            _issue(
                FamilyIssueCode.SEMANTIC_OWNER_MISMATCH,
                subject,
                "family operation or assembly escapes its semantic binding",
            )
        )
    family_object_ids = {
        object_id
        for operation in selected_operations
        for object_id in operation.output_object_ids
    }
    if any(object_owner.get(item) != instance.component_id for item in family_object_ids):
        issues.append(
            _issue(
                FamilyIssueCode.SEMANTIC_OWNER_MISMATCH,
                subject,
                "family geometry is not owned by its existing component",
            )
        )
    parameter_map = {
        (operation.op_id, parameter.name): parameter
        for operation in selected_operations
        for parameter in operation.parameters
    }
    for reference in instance.parameter_refs:
        parameter = parameter_map.get(
            (reference.operation_id, reference.parameter_name)
        )
        if (
            parameter is None
            or digest_value(parameter.to_dict()) != reference.parameter_digest
        ):
            issues.append(
                _issue(
                    FamilyIssueCode.PARAMETER_MISMATCH,
                    subject,
                    "family parameter does not bind an exact geometry parameter",
                )
            )
            break
    used_asset_ids = {
        item.asset_id for item in selected_operations if item.asset_id is not None
    }
    if used_asset_ids != set(instance.asset_ids):
        issues.append(
            _issue(
                FamilyIssueCode.ASSET_MISMATCH,
                subject,
                "family asset ids do not equal referenced asset operations",
            )
        )
    has_asset = any(
        item.kind is GeometryOperationKind.ASSET_INSTANCE
        for item in selected_operations
    )
    has_parameters = bool(instance.parameter_refs)
    if (
        instance.kind is ComponentFamilyKind.PARAMETRIC_ASSEMBLY
        and (has_asset or not has_parameters)
    ) or (
        instance.kind is ComponentFamilyKind.EXTERNAL_MESH
        and (not has_asset or has_parameters)
    ) or (
        instance.kind is ComponentFamilyKind.HYBRID
        and (not has_asset or not has_parameters)
    ):
        issues.append(
            _issue(
                FamilyIssueCode.KIND_MISMATCH,
                subject,
                "family kind disagrees with its parameter and asset surfaces",
            )
        )
    if instance.native_unit is not program_unit or any(
        item.native_unit is not instance.native_unit for item in selected_assets
    ):
        issues.append(
            _issue(
                FamilyIssueCode.UNIT_OR_SCALE_MISMATCH,
                subject,
                "external assets and family native unit disagree",
            )
        )
    asset_operations = [
        item for item in selected_operations if item.asset_id is not None
    ]
    if any(item.asset_scale != instance.scale for item in asset_operations):
        issues.append(
            _issue(
                FamilyIssueCode.UNIT_OR_SCALE_MISMATCH,
                subject,
                "asset operation scale does not equal the explicit family scale",
            )
        )
    assembly_interfaces = {
        ref for item in selected_assemblies for ref in item.interface_refs
    }
    if not assembly_interfaces <= set(instance.interface_refs):
        issues.append(
            _issue(
                FamilyIssueCode.INTERFACE_MISMATCH,
                subject,
                "hosted assembly interface is absent from the family manifest",
            )
        )
    socket_ids = {item.socket_id for item in instance.sockets}
    asset_by_id = {item.asset_id: item for item in selected_assets}
    for socket in instance.sockets:
        if (
            socket.object_id not in objects
            or object_owner.get(socket.object_id) != instance.component_id
            or socket.frame_id not in frame_ids
            or not set(socket.interface_refs) <= set(instance.interface_refs)
        ):
            issues.append(
                _issue(
                    FamilyIssueCode.SOCKET_MISMATCH,
                    subject,
                    "socket lacks exact owned object frame or interface",
                )
            )
            break
        operation = producer.get(socket.object_id)
        if operation is not None and operation.asset_id is not None:
            asset = asset_by_id.get(operation.asset_id)
            if asset is None or socket.socket_id not in asset.sockets:
                issues.append(
                    _issue(
                        FamilyIssueCode.SOCKET_MISMATCH,
                        subject,
                        "mesh socket is absent from immutable asset metadata",
                    )
                )
                break
    for anchor in instance.anchors:
        local_socket = next(
            (
                item
                for item in instance.sockets
                if item.socket_id == anchor.local_socket_id
            ),
            None,
        )
        target_key = (anchor.target_object_id, anchor.target_socket_id)
        if (
            local_socket is None
            or anchor.local_socket_id not in socket_ids
            or anchor.target_object_id not in objects
            or (
                target_key not in family_sockets
                and target_key not in assembly_host_sockets
                and not _asset_socket_exists(
                    target_key,
                    producer,
                    assets,
                )
            )
            or not set(anchor.interface_refs) <= set(local_socket.interface_refs)
        ):
            issues.append(
                _issue(
                    FamilyIssueCode.ANCHOR_MISMATCH,
                    subject,
                    "anchor lacks a local socket target socket object or interface",
                )
            )
            break
        target_owner = object_owner.get(anchor.target_object_id)
        if (
            target_owner is not None
            and target_owner != instance.component_id
            and target_owner not in instance.dependency_component_ids
        ):
            issues.append(
                _issue(
                    FamilyIssueCode.DEPENDENCY_MISMATCH,
                    subject,
                    "anchor target owner is absent from family dependencies",
                )
            )
            break
    if issues:
        return issues, None
    return issues, CompiledFamilyInstance(
        family_instance_id=instance.family_instance_id,
        component_id=instance.component_id,
        instance_digest=instance.instance_digest,
        definition_digest=instance.definition_digest,
        object_digests=tuple(
            sorted((item, objects[item]) for item in family_object_ids)
        ),
        parameter_digests=tuple(
            sorted(
                (item.parameter_id, item.parameter_digest)
                for item in instance.parameter_refs
            )
        ),
        asset_digests=tuple(
            sorted((item.asset_id, item.sha256) for item in selected_assets)
        ),
        interface_refs=instance.interface_refs,
        dependency_component_ids=instance.dependency_component_ids,
    )


def compile_component_family_lifecycle(
    predecessor: ComponentFamilySet,
    current: ComponentFamilySet,
    predecessor_compilation: ComponentFamilyCompilationReceipt,
    current_compilation: ComponentFamilyCompilationReceipt,
    semantic_lifecycle: SemanticGeometryLifecycleReceipt,
) -> ComponentFamilyLifecycleReceipt:
    """Check family change only after P055 has compiled both successor values."""

    for value, expected, field in (
        (predecessor, ComponentFamilySet, "predecessor"),
        (current, ComponentFamilySet, "current"),
        (
            predecessor_compilation,
            ComponentFamilyCompilationReceipt,
            "predecessor_compilation",
        ),
        (
            current_compilation,
            ComponentFamilyCompilationReceipt,
            "current_compilation",
        ),
        (
            semantic_lifecycle,
            SemanticGeometryLifecycleReceipt,
            "semantic_lifecycle",
        ),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{field} has invalid type")
    issues: list[FamilyCompileIssue] = []
    exact = (
        predecessor.project_id == current.project_id
        and predecessor.run_id == current.run_id
        and predecessor.base == current.base
        and (
            predecessor_compilation.project_id,
            predecessor_compilation.run_id,
            predecessor_compilation.base,
            predecessor_compilation.design_state_digest,
            predecessor_compilation.component_tree_digest,
            predecessor_compilation.geometry_program_digest,
        )
        == (
            predecessor.project_id,
            predecessor.run_id,
            predecessor.base,
            predecessor.design_state_digest,
            predecessor.component_tree_digest,
            predecessor.geometry_program_digest,
        )
        and (
            current_compilation.project_id,
            current_compilation.run_id,
            current_compilation.base,
            current_compilation.design_state_digest,
            current_compilation.component_tree_digest,
            current_compilation.geometry_program_digest,
        )
        == (
            current.project_id,
            current.run_id,
            current.base,
            current.design_state_digest,
            current.component_tree_digest,
            current.geometry_program_digest,
        )
        and predecessor_compilation.status is FamilyCompileStatus.COMPILED
        and current_compilation.status is FamilyCompileStatus.COMPILED
        and predecessor_compilation.family_set_digest
        == predecessor.family_set_digest
        and current_compilation.family_set_digest == current.family_set_digest
        and semantic_lifecycle.status is SemanticGeometryLifecycleStatus.COMPILED
        and semantic_lifecycle.predecessor_program_digest
        == predecessor.geometry_program_digest
        and semantic_lifecycle.current_program_digest
        == current.geometry_program_digest
        and semantic_lifecycle.predecessor_component_digest
        == predecessor.component_tree_digest
        and semantic_lifecycle.current_component_digest
        == current.component_tree_digest
        and semantic_lifecycle.predecessor_design_state_digest
        == predecessor.design_state_digest
        and semantic_lifecycle.current_design_state_digest
        == current.design_state_digest
    )
    if not exact:
        issues.append(
            _issue(
                FamilyIssueCode.LIFECYCLE_MISMATCH,
                "family-lifecycle",
                "family compilations do not bind the exact compiled P055 lifecycle",
            )
        )
    before = {item.component_id: item for item in predecessor.instances}
    after = {item.component_id: item for item in current.instances}
    compiled_before = {
        item.component_id: item
        for item in predecessor_compilation.compiled_instances
    }
    compiled_after = {
        item.component_id: item for item in current_compilation.compiled_instances
    }
    changed = set(semantic_lifecycle.geometry_changed_component_ids)
    revalidated = set(semantic_lifecycle.revalidated_component_ids)
    retired = set(semantic_lifecycle.retired_component_ids)
    impacted = changed | retired
    transitions: list[FamilyInstanceTransition] = []
    for component_id in sorted(set(before) | set(after)):
        prior = before.get(component_id)
        next_instance = after.get(component_id)
        if prior is None:
            disposition = FamilyInstanceDisposition.ADDED
            if component_id not in changed:
                issues.append(
                    _issue(
                        FamilyIssueCode.LIFECYCLE_MISMATCH,
                        component_id,
                        "added family lacks changed component geometry",
                    )
                )
        elif next_instance is None:
            disposition = FamilyInstanceDisposition.RETIRED
            if component_id not in impacted:
                issues.append(
                    _issue(
                        FamilyIssueCode.LIFECYCLE_MISMATCH,
                        component_id,
                        "retired family lacks P055 retirement or geometry change",
                    )
                )
        elif prior.instance_digest == next_instance.instance_digest:
            disposition = FamilyInstanceDisposition.PRESERVED
        elif prior.family_id == next_instance.family_id:
            disposition = FamilyInstanceDisposition.REVISED
            if (
                next_instance.family_revision != prior.family_revision + 1
                or next_instance.predecessor_instance_digest
                != prior.instance_digest
            ):
                issues.append(
                    _issue(
                        FamilyIssueCode.LIFECYCLE_MISMATCH,
                        component_id,
                        "family revision lacks exact predecessor and next revision",
                    )
                )
        else:
            disposition = FamilyInstanceDisposition.REPLACED
            if next_instance.predecessor_instance_digest != prior.instance_digest:
                issues.append(
                    _issue(
                        FamilyIssueCode.LIFECYCLE_MISMATCH,
                        component_id,
                        "replacement family lacks exact predecessor instance",
                    )
                )
        if prior is not None and next_instance is not None:
            prior_compiled = compiled_before.get(component_id)
            next_compiled = compiled_after.get(component_id)
            if prior_compiled is None or next_compiled is None:
                issues.append(
                    _issue(
                        FamilyIssueCode.LIFECYCLE_MISMATCH,
                        component_id,
                        "family lifecycle lacks an exact compiled instance",
                    )
                )
            if (
                prior.family_id == next_instance.family_id
                and prior.family_revision == next_instance.family_revision
                and (
                    prior.definition_digest != next_instance.definition_digest
                    or (
                        prior_compiled is not None
                        and next_compiled is not None
                        and prior_compiled.asset_digests
                        != next_compiled.asset_digests
                    )
                )
            ):
                issues.append(
                    _issue(
                        FamilyIssueCode.IMMUTABLE_REVISION_CHANGED,
                        component_id,
                        "family definition or mesh content changed behind one revision",
                    )
                )
            if disposition in {
                FamilyInstanceDisposition.REVISED,
                FamilyInstanceDisposition.REPLACED,
            } and component_id not in changed:
                issues.append(
                    _issue(
                        FamilyIssueCode.LIFECYCLE_MISMATCH,
                        component_id,
                        "changed family lacks changed component geometry",
                    )
                )
            if disposition is FamilyInstanceDisposition.PRESERVED:
                dependency_impact = set(next_instance.dependency_component_ids) & impacted
                if dependency_impact and component_id not in changed | revalidated:
                    issues.append(
                        _issue(
                            FamilyIssueCode.LOCAL_DEPENDENCY_NOT_INVALIDATED,
                            component_id,
                            "preserved family ignored an impacted declared dependency",
                        )
                    )
        transitions.append(
            FamilyInstanceTransition(
                component_id=component_id,
                disposition=disposition,
                predecessor_instance_id=(
                    prior.family_instance_id if prior is not None else None
                ),
                predecessor_instance_digest=(
                    prior.instance_digest if prior is not None else None
                ),
                current_instance_id=(
                    next_instance.family_instance_id
                    if next_instance is not None
                    else None
                ),
                current_instance_digest=(
                    next_instance.instance_digest
                    if next_instance is not None
                    else None
                ),
            )
        )
    ordered_issues = tuple(
        sorted(issues, key=lambda item: (item.code.value, item.subject_id))
    )
    common = {
        "project_id": predecessor.project_id,
        "run_id": predecessor.run_id,
        "predecessor_family_set_digest": predecessor.family_set_digest,
        "current_family_set_digest": current.family_set_digest,
        "predecessor_program_digest": predecessor.geometry_program_digest,
        "current_program_digest": current.geometry_program_digest,
        "semantic_lifecycle_receipt_digest": semantic_lifecycle.receipt_digest,
    }
    if ordered_issues:
        return ComponentFamilyLifecycleReceipt(
            **common,
            status=FamilyCompileStatus.REJECTED,
            transitions=(),
            issues=ordered_issues,
        )
    return ComponentFamilyLifecycleReceipt(
        **common,
        status=FamilyCompileStatus.COMPILED,
        transitions=tuple(transitions),
        issues=(),
    )


def _asset_socket_exists(
    target: tuple[str, str],
    producer: Mapping[str, object],
    assets: Mapping[str, object],
) -> bool:
    object_id, socket_id = target
    operation = producer.get(object_id)
    if operation is None or operation.asset_id is None:
        return False
    asset = assets.get(operation.asset_id)
    return asset is not None and socket_id in asset.sockets


def _issue(code: FamilyIssueCode, subject_id: str, detail: str) -> FamilyCompileIssue:
    return FamilyCompileIssue(code=code, subject_id=subject_id, detail=detail)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} schema drifted")


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must be a string list")
    return tuple(values)


def _refs(values: object, field: str, *, allow_empty: bool = False) -> None:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ValueError(f"{field} must be a tuple")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must be sorted and unique")
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError(f"{field} contains an invalid ref")


def _ids(values: object, field: str, *, allow_empty: bool = False) -> None:
    _refs(values, field, allow_empty=allow_empty)
    for item in values:
        require_identifier(item, field)


def _digest_pairs(values: object, field: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    ids: list[str] = []
    for key, digest in values:
        require_identifier(key, field)
        require_sha256(digest, field)
        ids.append(key)
    if tuple(ids) != tuple(sorted(set(ids))):
        raise ValueError(f"{field} must be sorted and unique")


def _typed_sorted(
    values: object,
    item_type: type,
    field: str,
    id_field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ValueError(f"{field} must be a tuple")
    if any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{field} contains an invalid item")
    ids = tuple(getattr(item, id_field) for item in values)
    if ids != tuple(sorted(set(ids))):
        raise ValueError(f"{field} must be sorted and unique")


def _pairs_to_dict(values: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"id": key, "digest": digest} for key, digest in values]


def _pairs_from_dict(value: object) -> tuple[tuple[str, str], ...]:
    entries = _list(value, "digest pairs")
    result: list[tuple[str, str]] = []
    for item in entries:
        payload = _mapping(item, "digest pair")
        _exact(payload, {"id", "digest"}, "digest pair")
        result.append((payload["id"], payload["digest"]))
    return tuple(result)


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )
