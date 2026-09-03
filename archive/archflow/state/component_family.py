"""Project-authored component-family bindings over neutral geometry.

Families annotate the existing semantic component tree and geometry program;
they never own component hierarchy or generate geometry by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import ProjectVersionRef, require_identifier
from archflow.state.geometry_program import LengthUnit, require_sha256
from archflow.contracts.canonical import canonical_digest
from archflow.state.operational_state import require_logical_ref


class ComponentFamilyError(ValueError):
    """A family binding is malformed or changes protocol authority."""


class ComponentFamilyKind(StrEnum):
    PARAMETRIC_ASSEMBLY = "parametric_assembly"
    EXTERNAL_MESH = "external_mesh"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class FamilyParameterRef:
    parameter_id: str
    operation_id: str
    parameter_name: str
    parameter_digest: str
    source_refs: tuple[str, ...]

    SCHEMA = "ComponentFamilyParameterRef@1"

    def __post_init__(self) -> None:
        require_identifier(self.parameter_id, "parameter_id")
        require_identifier(self.operation_id, "operation_id")
        require_identifier(self.parameter_name, "parameter_name")
        object.__setattr__(
            self,
            "parameter_digest",
            require_sha256(self.parameter_digest, "parameter_digest"),
        )
        _refs(self.source_refs, "parameter source_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "parameter_id": self.parameter_id,
            "operation_id": self.operation_id,
            "parameter_name": self.parameter_name,
            "parameter_digest": self.parameter_digest,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> FamilyParameterRef:
        payload = _mapping(value, "family parameter ref")
        _exact(
            payload,
            {
                "schema",
                "parameter_id",
                "operation_id",
                "parameter_name",
                "parameter_digest",
                "source_refs",
            },
            "family parameter ref",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFamilyError("family parameter schema changed")
        return cls(
            parameter_id=payload["parameter_id"],
            operation_id=payload["operation_id"],
            parameter_name=payload["parameter_name"],
            parameter_digest=payload["parameter_digest"],
            source_refs=_string_tuple(payload["source_refs"], "source_refs"),
        )


@dataclass(frozen=True, slots=True)
class FamilySocket:
    socket_id: str
    object_id: str
    frame_id: str
    interface_refs: tuple[str, ...]

    SCHEMA = "ComponentFamilySocket@1"

    def __post_init__(self) -> None:
        require_identifier(self.socket_id, "socket_id")
        require_identifier(self.object_id, "socket object_id")
        require_identifier(self.frame_id, "socket frame_id")
        _refs(self.interface_refs, "socket interface_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "socket_id": self.socket_id,
            "object_id": self.object_id,
            "frame_id": self.frame_id,
            "interface_refs": list(self.interface_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> FamilySocket:
        payload = _mapping(value, "family socket")
        _exact(
            payload,
            {"schema", "socket_id", "object_id", "frame_id", "interface_refs"},
            "family socket",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFamilyError("family socket schema changed")
        return cls(
            socket_id=payload["socket_id"],
            object_id=payload["object_id"],
            frame_id=payload["frame_id"],
            interface_refs=_string_tuple(
                payload["interface_refs"], "interface_refs"
            ),
        )


@dataclass(frozen=True, slots=True)
class FamilyAnchorBinding:
    anchor_id: str
    local_socket_id: str
    target_object_id: str
    target_socket_id: str
    interface_refs: tuple[str, ...]

    SCHEMA = "ComponentFamilyAnchorBinding@1"

    def __post_init__(self) -> None:
        require_identifier(self.anchor_id, "anchor_id")
        require_identifier(self.local_socket_id, "local_socket_id")
        require_identifier(self.target_object_id, "target_object_id")
        require_identifier(self.target_socket_id, "target_socket_id")
        _refs(self.interface_refs, "anchor interface_refs")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "anchor_id": self.anchor_id,
            "local_socket_id": self.local_socket_id,
            "target_object_id": self.target_object_id,
            "target_socket_id": self.target_socket_id,
            "interface_refs": list(self.interface_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> FamilyAnchorBinding:
        payload = _mapping(value, "family anchor")
        _exact(
            payload,
            {
                "schema",
                "anchor_id",
                "local_socket_id",
                "target_object_id",
                "target_socket_id",
                "interface_refs",
            },
            "family anchor",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFamilyError("family anchor schema changed")
        return cls(
            anchor_id=payload["anchor_id"],
            local_socket_id=payload["local_socket_id"],
            target_object_id=payload["target_object_id"],
            target_socket_id=payload["target_socket_id"],
            interface_refs=_string_tuple(
                payload["interface_refs"], "interface_refs"
            ),
        )


@dataclass(frozen=True, slots=True)
class ComponentFamilyInstance:
    family_instance_id: str
    component_id: str
    family_id: str
    family_revision: int
    kind: ComponentFamilyKind
    definition_digest: str
    predecessor_instance_digest: str | None
    frame_id: str
    native_unit: LengthUnit
    scale: tuple[float, float, float]
    parameter_refs: tuple[FamilyParameterRef, ...]
    sockets: tuple[FamilySocket, ...]
    anchors: tuple[FamilyAnchorBinding, ...]
    interface_refs: tuple[str, ...]
    dependency_component_ids: tuple[str, ...]
    semantic_binding_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]
    assembly_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]
    provenance_refs: tuple[str, ...]

    SCHEMA = "ComponentFamilyInstance@1"

    def __post_init__(self) -> None:
        require_identifier(self.family_instance_id, "family_instance_id")
        require_identifier(self.component_id, "family component_id")
        require_identifier(self.family_id, "family_id")
        if (
            not isinstance(self.family_revision, int)
            or isinstance(self.family_revision, bool)
            or self.family_revision < 1
        ):
            raise ComponentFamilyError("family_revision must be positive")
        if not isinstance(self.kind, ComponentFamilyKind):
            raise TypeError("kind must be ComponentFamilyKind")
        object.__setattr__(
            self,
            "definition_digest",
            require_sha256(self.definition_digest, "definition_digest"),
        )
        if self.predecessor_instance_digest is not None:
            object.__setattr__(
                self,
                "predecessor_instance_digest",
                require_sha256(
                    self.predecessor_instance_digest,
                    "predecessor_instance_digest",
                ),
            )
        require_identifier(self.frame_id, "family frame_id")
        if not isinstance(self.native_unit, LengthUnit):
            raise TypeError("native_unit must be LengthUnit")
        if not isinstance(self.scale, tuple) or len(self.scale) != 3:
            raise ComponentFamilyError("scale must contain three values")
        normalized_scale: list[float] = []
        for value in self.scale:
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 < float(value) <= 1_000_000
            ):
                raise ComponentFamilyError("scale must be finite positive and bounded")
            normalized_scale.append(float(value))
        object.__setattr__(self, "scale", tuple(normalized_scale))
        _typed_sorted(
            self.parameter_refs,
            FamilyParameterRef,
            "parameter_refs",
            "parameter_id",
            allow_empty=True,
        )
        _typed_sorted(self.sockets, FamilySocket, "sockets", "socket_id")
        _typed_sorted(
            self.anchors,
            FamilyAnchorBinding,
            "anchors",
            "anchor_id",
            allow_empty=True,
        )
        _refs(self.interface_refs, "family interface_refs")
        _ids(
            self.dependency_component_ids,
            "dependency_component_ids",
            allow_empty=True,
        )
        if self.component_id in self.dependency_component_ids:
            raise ComponentFamilyError("family cannot depend on its own component")
        _ids(self.semantic_binding_ids, "semantic_binding_ids")
        _ids(self.operation_ids, "operation_ids")
        _ids(self.assembly_ids, "assembly_ids", allow_empty=True)
        _ids(self.asset_ids, "asset_ids", allow_empty=True)
        _refs(self.provenance_refs, "family provenance_refs")
        if not set(
            ref for socket in self.sockets for ref in socket.interface_refs
        ) <= set(self.interface_refs):
            raise ComponentFamilyError(
                "socket interfaces must be declared by the family instance"
            )
        if not set(
            ref for anchor in self.anchors for ref in anchor.interface_refs
        ) <= set(self.interface_refs):
            raise ComponentFamilyError(
                "anchor interfaces must be declared by the family instance"
            )
        if self.kind is ComponentFamilyKind.PARAMETRIC_ASSEMBLY:
            if not self.parameter_refs or self.asset_ids:
                raise ComponentFamilyError(
                    "parametric family requires parameters and no mesh asset"
                )
            if self.scale != (1.0, 1.0, 1.0):
                raise ComponentFamilyError(
                    "parametric geometry changes through parameters, not scale"
                )
        elif self.kind is ComponentFamilyKind.EXTERNAL_MESH:
            if self.parameter_refs or not self.asset_ids:
                raise ComponentFamilyError(
                    "external mesh requires assets and cannot claim parameters"
                )
        elif not self.parameter_refs or not self.asset_ids:
            raise ComponentFamilyError(
                "hybrid family requires both parameters and mesh assets"
            )

    @property
    def instance_digest(self) -> str:
        return canonical_digest(self._identity())

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "family_instance_id": self.family_instance_id,
            "component_id": self.component_id,
            "family_id": self.family_id,
            "family_revision": self.family_revision,
            "kind": self.kind.value,
            "definition_digest": self.definition_digest,
            "predecessor_instance_digest": self.predecessor_instance_digest,
            "frame_id": self.frame_id,
            "native_unit": self.native_unit.value,
            "scale": list(self.scale),
            "parameter_refs": [item.to_dict() for item in self.parameter_refs],
            "sockets": [item.to_dict() for item in self.sockets],
            "anchors": [item.to_dict() for item in self.anchors],
            "interface_refs": list(self.interface_refs),
            "dependency_component_ids": list(self.dependency_component_ids),
            "semantic_binding_ids": list(self.semantic_binding_ids),
            "operation_ids": list(self.operation_ids),
            "assembly_ids": list(self.assembly_ids),
            "asset_ids": list(self.asset_ids),
            "provenance_refs": list(self.provenance_refs),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "instance_digest": self.instance_digest,
            "owns_component_hierarchy": False,
            "geometry_generation_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentFamilyInstance:
        payload = _mapping(value, "component family instance")
        _exact(
            payload,
            {
                "schema",
                "family_instance_id",
                "component_id",
                "family_id",
                "family_revision",
                "kind",
                "definition_digest",
                "predecessor_instance_digest",
                "frame_id",
                "native_unit",
                "scale",
                "parameter_refs",
                "sockets",
                "anchors",
                "interface_refs",
                "dependency_component_ids",
                "semantic_binding_ids",
                "operation_ids",
                "assembly_ids",
                "asset_ids",
                "provenance_refs",
                "instance_digest",
                "owns_component_hierarchy",
                "geometry_generation_authority",
                "canonical_write_authority",
            },
            "component family instance",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["owns_component_hierarchy"] is not False
        ):
            raise ComponentFamilyError("component family authority changed")
        parameters = _list(payload["parameter_refs"], "parameter_refs")
        sockets = _list(payload["sockets"], "sockets")
        anchors = _list(payload["anchors"], "anchors")
        instance = cls(
            family_instance_id=payload["family_instance_id"],
            component_id=payload["component_id"],
            family_id=payload["family_id"],
            family_revision=payload["family_revision"],
            kind=ComponentFamilyKind(payload["kind"]),
            definition_digest=payload["definition_digest"],
            predecessor_instance_digest=payload["predecessor_instance_digest"],
            frame_id=payload["frame_id"],
            native_unit=LengthUnit(payload["native_unit"]),
            scale=tuple(_number_list(payload["scale"], "scale")),
            parameter_refs=tuple(
                FamilyParameterRef.from_dict(item) for item in parameters
            ),
            sockets=tuple(FamilySocket.from_dict(item) for item in sockets),
            anchors=tuple(
                FamilyAnchorBinding.from_dict(item) for item in anchors
            ),
            interface_refs=_string_tuple(
                payload["interface_refs"], "interface_refs"
            ),
            dependency_component_ids=_string_tuple(
                payload["dependency_component_ids"],
                "dependency_component_ids",
            ),
            semantic_binding_ids=_string_tuple(
                payload["semantic_binding_ids"], "semantic_binding_ids"
            ),
            operation_ids=_string_tuple(payload["operation_ids"], "operation_ids"),
            assembly_ids=_string_tuple(payload["assembly_ids"], "assembly_ids"),
            asset_ids=_string_tuple(payload["asset_ids"], "asset_ids"),
            provenance_refs=_string_tuple(
                payload["provenance_refs"], "provenance_refs"
            ),
        )
        if payload["instance_digest"] != instance.instance_digest:
            raise ComponentFamilyError("component family instance digest changed")
        return instance


@dataclass(frozen=True, slots=True)
class ComponentFamilySet:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    design_state_digest: str
    component_tree_digest: str
    geometry_program_digest: str
    available_interface_refs: tuple[str, ...]
    instances: tuple[ComponentFamilyInstance, ...]

    SCHEMA = "ComponentFamilySet@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ComponentFamilyError("family set and base disagree")
        self.base.require_digest()
        for value, field in (
            (self.design_state_digest, "design_state_digest"),
            (self.component_tree_digest, "component_tree_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
        ):
            require_sha256(value, field)
        _refs(self.available_interface_refs, "available_interface_refs")
        _typed_sorted(
            self.instances,
            ComponentFamilyInstance,
            "instances",
            "family_instance_id",
            allow_empty=True,
        )
        component_ids = tuple(item.component_id for item in self.instances)
        if len(component_ids) != len(set(component_ids)):
            raise ComponentFamilyError(
                "one semantic component may have at most one family instance"
            )
        available = set(self.available_interface_refs)
        for instance in self.instances:
            if not set(instance.interface_refs) <= available:
                raise ComponentFamilyError(
                    "family instance cites an unavailable project interface"
                )

    @property
    def family_set_digest(self) -> str:
        return canonical_digest(self._identity())

    def _identity(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "design_state_digest": self.design_state_digest,
            "component_tree_digest": self.component_tree_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "available_interface_refs": list(self.available_interface_refs),
            "instances": [item.to_dict() for item in self.instances],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._identity(),
            "family_set_digest": self.family_set_digest,
            "component_tree_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentFamilySet:
        payload = _mapping(value, "component family set")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "design_state_digest",
                "component_tree_digest",
                "geometry_program_digest",
                "available_interface_refs",
                "instances",
                "family_set_digest",
                "component_tree_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "component family set",
        )
        if (
            payload["schema"] != cls.SCHEMA
        ):
            raise ComponentFamilyError("component family set authority changed")
        instances = _list(payload["instances"], "instances")
        family_set = cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            design_state_digest=payload["design_state_digest"],
            component_tree_digest=payload["component_tree_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            available_interface_refs=_string_tuple(
                payload["available_interface_refs"],
                "available_interface_refs",
            ),
            instances=tuple(
                ComponentFamilyInstance.from_dict(item) for item in instances
            ),
        )
        if payload["family_set_digest"] != family_set.family_set_digest:
            raise ComponentFamilyError("component family set digest changed")
        return family_set


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return value


def _exact(value: Mapping[str, object], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ComponentFamilyError(f"{name} schema drifted")


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    values = _list(value, field)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{field} must be a list of strings")
    return tuple(values)


def _number_list(value: object, field: str) -> list[float]:
    values = _list(value, field)
    if any(
        not isinstance(item, (int, float)) or isinstance(item, bool)
        for item in values
    ):
        raise TypeError(f"{field} must be a numeric list")
    return [float(item) for item in values]


def _refs(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ComponentFamilyError(f"{field} must be a deterministic tuple")
    if values != tuple(sorted(set(values))):
        raise ComponentFamilyError(f"{field} must be sorted and unique")
    for value in values:
        require_logical_ref(value, field)
    return values


def _ids(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ComponentFamilyError(f"{field} must be a deterministic tuple")
    if values != tuple(sorted(set(values))):
        raise ComponentFamilyError(f"{field} must be sorted and unique")
    for value in values:
        require_identifier(value, field)
    return values


def _typed_sorted(
    values: object,
    item_type: type,
    field: str,
    id_field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ComponentFamilyError(f"{field} must be a deterministic tuple")
    if any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{field} contains an invalid item")
    ids = tuple(getattr(item, id_field) for item in values)
    if ids != tuple(sorted(set(ids))):
        raise ComponentFamilyError(f"{field} identities must be sorted and unique")


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
