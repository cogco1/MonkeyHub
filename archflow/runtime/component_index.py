"""Regenerable component/task joins over authoritative design records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

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
from archflow.compilers.geometry import CompiledGeometryProgram
from archflow.runtime.semantic_geometry_lifecycle import (
    InitialSemanticGeometryReceipt,
    SemanticGeometryLifecycleReceipt,
    SemanticGeometryLifecycleStatus,
)
from archflow.state.design_state import (
    ContextSlice,
    ContextSliceCompiler,
    DesignStateTree,
)
from archflow.state.developed_design import (
    DevelopedDesignState,
    DevelopmentDependency,
    DevelopmentObligation,
)
from archflow.state.geometry_program import digest_value
from archflow.state.operational_state import require_logical_ref
from archflow.state.spatial import DesignComponent
from archflow.contracts.canonical import canonical_digest


class ComponentIndexError(ValueError):
    """Authoritative inputs cannot form one coherent derived index."""


def _sha(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ComponentIndexError(f"{field} must be a SHA-256 digest")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComponentIndexError(f"{field} must be an object")
    return value


def _exact(
    value: Mapping[str, Any],
    fields: set[str],
    subject: str,
) -> None:
    if set(value) != fields:
        raise ComponentIndexError(f"{subject} schema drifted")


def _strings(
    value: object,
    field: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise ComponentIndexError(f"{field} must contain text")
    result = tuple(value)
    if (not result and not allow_empty) or result != tuple(sorted(set(result))):
        raise ComponentIndexError(f"{field} must be sorted and unique")
    return result


def _base_to_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.state_sha256,
    }


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "base")
    return ProjectVersionRef(
        project_id=payload["project_id"],
        version=payload["version"],
        state_sha256=payload["state_sha256"],
    )


@dataclass(frozen=True, slots=True)
class ComponentIndexEntry:
    """One derived join; parentage exists only inside ``DesignComponent``."""

    component: DesignComponent
    geometry_object_ids: tuple[str, ...]
    binding_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    source_refs: tuple[str, ...]

    SCHEMA = "ComponentIndexEntry@1"

    def __post_init__(self) -> None:
        if not isinstance(self.component, DesignComponent):
            raise TypeError("component must be a DesignComponent")
        for values, field in (
            (self.geometry_object_ids, "geometry_object_ids"),
            (self.binding_ids, "binding_ids"),
            (self.dependency_ids, "dependency_ids"),
            (self.task_ids, "task_ids"),
        ):
            _strings(values, field)
            for value in values:
                require_identifier(value, field)
        _strings(self.source_refs, "source_refs", allow_empty=False)
        for value in self.source_refs:
            require_logical_ref(value, "source_ref")

    @property
    def component_id(self) -> str:
        return self.component.component_id

    @property
    def parent_component_id(self) -> str | None:
        return self.component.parent_component_id

    @property
    def stage(self) -> str:
        return self.component.maturity.value

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "component": self.component.to_dict(),
            "component_digest": self.component.component_digest,
            "geometry_object_ids": list(self.geometry_object_ids),
            "binding_ids": list(self.binding_ids),
            "dependency_ids": list(self.dependency_ids),
            "task_ids": list(self.task_ids),
            "source_refs": list(self.source_refs),
            "parentage_authority": "DesignComponent@1",
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentIndexEntry:
        payload = _mapping(value, "component index entry")
        _exact(
            payload,
            {
                "schema",
                "component",
                "component_digest",
                "geometry_object_ids",
                "binding_ids",
                "dependency_ids",
                "task_ids",
                "source_refs",
                "parentage_authority",
            },
            "component index entry",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["parentage_authority"] != "DesignComponent@1"
        ):
            raise ComponentIndexError("component index entry schema changed")
        component = DesignComponent.from_dict(payload["component"])
        if payload["component_digest"] != component.component_digest:
            raise ComponentIndexError("component index entry digest is stale")
        return cls(
            component=component,
            geometry_object_ids=_strings(
                payload["geometry_object_ids"], "geometry_object_ids"
            ),
            binding_ids=_strings(payload["binding_ids"], "binding_ids"),
            dependency_ids=_strings(
                payload["dependency_ids"], "dependency_ids"
            ),
            task_ids=_strings(payload["task_ids"], "task_ids"),
            source_refs=_strings(
                payload["source_refs"], "source_refs", allow_empty=False
            ),
        )


@dataclass(frozen=True, slots=True)
class ComponentIndex:
    project_id: str
    run_id: str
    base: ProjectVersionRef
    design_state_digest: str
    component_proposal_digest: str
    geometry_program_digest: str
    control_tree_digest: str
    control_context_digest: str
    lifecycle_receipt_digest: str
    control_target_node_ref: str
    entries: tuple[ComponentIndexEntry, ...]
    dependencies: tuple[DevelopmentDependency, ...]
    tasks: tuple[DevelopmentObligation, ...]

    SCHEMA = "ComponentIndex@1"

    def __post_init__(self) -> None:
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        if not isinstance(self.base, ProjectVersionRef):
            raise TypeError("base must be a ProjectVersionRef")
        if self.base.project_id != self.project_id:
            raise ComponentIndexError("index and base belong to different projects")
        for value, field in (
            (self.design_state_digest, "design_state_digest"),
            (self.component_proposal_digest, "component_proposal_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
            (self.control_tree_digest, "control_tree_digest"),
            (self.control_context_digest, "control_context_digest"),
            (self.lifecycle_receipt_digest, "lifecycle_receipt_digest"),
        ):
            _sha(value, field)
        require_logical_ref(self.control_target_node_ref, "control_target_node_ref")
        if not self.entries or any(
            not isinstance(item, ComponentIndexEntry) for item in self.entries
        ):
            raise ComponentIndexError("entries must contain derived component joins")
        component_ids = tuple(item.component_id for item in self.entries)
        if component_ids != tuple(sorted(set(component_ids))):
            raise ComponentIndexError("entries require deterministic component ids")
        component_by_id = {item.component_id: item for item in self.entries}
        roots = tuple(
            item for item in self.entries if item.parent_component_id is None
        )
        if len(roots) != 1:
            raise ComponentIndexError("authoritative components require one root")
        for entry in self.entries:
            parent = entry.parent_component_id
            if parent is not None and parent not in component_by_id:
                raise ComponentIndexError("authoritative component parent is missing")
            seen = {entry.component_id}
            while parent is not None:
                if parent in seen:
                    raise ComponentIndexError("authoritative component ancestry cycles")
                seen.add(parent)
                parent = component_by_id[parent].parent_component_id
        for values, value_type, field, identity in (
            (self.dependencies, DevelopmentDependency, "dependencies", "dependency_id"),
            (self.tasks, DevelopmentObligation, "tasks", "obligation_id"),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(item, value_type) for item in values
            ):
                raise TypeError(f"{field} contains an invalid item")
            ids = tuple(getattr(item, identity) for item in values)
            if ids != tuple(sorted(set(ids))):
                raise ComponentIndexError(f"{field} require deterministic ids")
        dependency_by_id = {
            item.dependency_id: item for item in self.dependencies
        }
        task_ids = {item.obligation_id for item in self.tasks}
        object_owners: dict[str, str] = {}
        binding_owners: dict[str, str] = {}
        for entry in self.entries:
            for dependency_id in entry.dependency_ids:
                dependency = dependency_by_id.get(dependency_id)
                if (
                    dependency is None
                    or dependency.target_component_id != entry.component_id
                ):
                    raise ComponentIndexError("dependency join contradicts its target")
            if not set(entry.task_ids) <= task_ids:
                raise ComponentIndexError("task join names an absent obligation")
            for object_id in entry.geometry_object_ids:
                if object_id in object_owners:
                    raise ComponentIndexError("geometry object has multiple owners")
                object_owners[object_id] = entry.component_id
            for binding_id in entry.binding_ids:
                if binding_id in binding_owners:
                    raise ComponentIndexError("semantic binding has multiple owners")
                binding_owners[binding_id] = entry.component_id
        if set(dependency_by_id) != {
            value for item in self.entries for value in item.dependency_ids
        }:
            raise ComponentIndexError("dependency table is not fully joined")

    @property
    def index_digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def unassigned_task_ids(self) -> tuple[str, ...]:
        assigned = {value for item in self.entries for value in item.task_ids}
        return tuple(
            item.obligation_id
            for item in self.tasks
            if item.obligation_id not in assigned
        )

    def entry(self, component_id: str) -> ComponentIndexEntry:
        require_identifier(component_id, "component_id")
        for item in self.entries:
            if item.component_id == component_id:
                return item
        raise ComponentIndexError(f"unknown component: {component_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_to_dict(self.base),
            "design_state_digest": self.design_state_digest,
            "component_proposal_digest": self.component_proposal_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "control_tree_digest": self.control_tree_digest,
            "control_context_digest": self.control_context_digest,
            "lifecycle_receipt_digest": self.lifecycle_receipt_digest,
            "control_target_node_ref": self.control_target_node_ref,
            "entries": [item.to_dict() for item in self.entries],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "tasks": [item.to_dict() for item in self.tasks],
            "unassigned_task_ids": list(self.unassigned_task_ids),
            "derived": True,
            "regenerable": True,
            "composition_tree_authority": False,
            "control_tree_authority": False,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> ComponentIndex:
        payload = _mapping(value, "component index")
        _exact(
            payload,
            {
                "schema",
                "project_id",
                "run_id",
                "base",
                "design_state_digest",
                "component_proposal_digest",
                "geometry_program_digest",
                "control_tree_digest",
                "control_context_digest",
                "lifecycle_receipt_digest",
                "control_target_node_ref",
                "entries",
                "dependencies",
                "tasks",
                "unassigned_task_ids",
                "derived",
                "regenerable",
                "composition_tree_authority",
                "control_tree_authority",
                "persistence_authority",
                "canonical_write_authority",
            },
            "component index",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["derived"] is not True
            or payload["regenerable"] is not True
        ):
            raise ComponentIndexError("component index authority flags changed")
        entries = payload["entries"]
        dependencies = payload["dependencies"]
        tasks = payload["tasks"]
        if not all(isinstance(item, list) for item in (entries, dependencies, tasks)):
            raise ComponentIndexError("component index collections must be lists")
        result = cls(
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            design_state_digest=payload["design_state_digest"],
            component_proposal_digest=payload["component_proposal_digest"],
            geometry_program_digest=payload["geometry_program_digest"],
            control_tree_digest=payload["control_tree_digest"],
            control_context_digest=payload["control_context_digest"],
            lifecycle_receipt_digest=payload["lifecycle_receipt_digest"],
            control_target_node_ref=payload["control_target_node_ref"],
            entries=tuple(ComponentIndexEntry.from_dict(item) for item in entries),
            dependencies=tuple(
                DevelopmentDependency.from_dict(item) for item in dependencies
            ),
            tasks=tuple(DevelopmentObligation.from_dict(item) for item in tasks),
        )
        if list(result.unassigned_task_ids) != payload["unassigned_task_ids"]:
            raise ComponentIndexError("unassigned task join is contradictory")
        return result


@dataclass(frozen=True, slots=True)
class ComponentTaskContext:
    index_digest: str
    component_id: str
    control_context_digest: str
    control_target_node_ref: str
    entries: tuple[ComponentIndexEntry, ...]
    dependencies: tuple[DevelopmentDependency, ...]
    tasks: tuple[DevelopmentObligation, ...]
    explicit_dependency_refs: tuple[str, ...]
    max_entries: int

    SCHEMA = "ComponentTaskContext@1"

    def __post_init__(self) -> None:
        _sha(self.index_digest, "index_digest")
        require_identifier(self.component_id, "component_id")
        _sha(self.control_context_digest, "control_context_digest")
        require_logical_ref(self.control_target_node_ref, "control_target_node_ref")
        if (
            not isinstance(self.max_entries, int)
            or isinstance(self.max_entries, bool)
            or not 1 <= self.max_entries <= 64
        ):
            raise ComponentIndexError("max_entries must be between 1 and 64")
        if (
            not self.entries
            or len(self.entries) > self.max_entries
            or any(not isinstance(item, ComponentIndexEntry) for item in self.entries)
        ):
            raise ComponentIndexError("task context component neighborhood is invalid")
        ids = tuple(item.component_id for item in self.entries)
        if ids != tuple(sorted(set(ids))) or self.component_id not in ids:
            raise ComponentIndexError("task context must contain its selected component")
        if any(
            not isinstance(item, DevelopmentDependency)
            for item in self.dependencies
        ) or any(not isinstance(item, DevelopmentObligation) for item in self.tasks):
            raise TypeError("task context contains an invalid record")
        _strings(self.explicit_dependency_refs, "explicit_dependency_refs")
        for value in self.explicit_dependency_refs:
            require_logical_ref(value, "explicit_dependency_ref")

    @property
    def context_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "index_digest": self.index_digest,
            "component_id": self.component_id,
            "control_context_digest": self.control_context_digest,
            "control_target_node_ref": self.control_target_node_ref,
            "entries": [item.to_dict() for item in self.entries],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "tasks": [item.to_dict() for item in self.tasks],
            "explicit_dependency_refs": list(self.explicit_dependency_refs),
            "max_entries": self.max_entries,
            "derived": True,
            "design_authority": False,
            "canonical_write_authority": False,
        }


def build_component_index(
    *,
    current_state: DevelopedDesignState,
    geometry_program: CompiledGeometryProgram,
    control_tree: DesignStateTree,
    control_context: ContextSlice,
    lifecycle_receipt: (
        InitialSemanticGeometryReceipt | SemanticGeometryLifecycleReceipt
    ),
) -> ComponentIndex:
    """Join exact records without creating another design authority."""

    if not isinstance(current_state, DevelopedDesignState):
        raise TypeError("current_state must be a DevelopedDesignState")
    if not isinstance(geometry_program, CompiledGeometryProgram):
        raise TypeError("geometry_program must be a CompiledGeometryProgram")
    if not isinstance(control_tree, DesignStateTree):
        raise TypeError("control_tree must be a DesignStateTree")
    if not isinstance(control_context, ContextSlice):
        raise TypeError("control_context must be a ContextSlice")
    run = RunRef(
        project_id=current_state.project_id,
        run_id=current_state.run_id,
        base=current_state.base,
    )
    if control_tree.branch.run != run:
        raise ComponentIndexError("control tree belongs to a different P036 run")
    rebuilt_context = ContextSliceCompiler().compile(
        control_tree,
        target_node_ref=control_context.target_node_ref,
    )
    if rebuilt_context.context_digest != control_context.context_digest:
        raise ComponentIndexError("control context is stale for its tree")

    proposal = current_state.selected_schematic.option.proposal
    geometry_proposal = geometry_program.proposal
    if (
        geometry_proposal.project_id != current_state.project_id
        or geometry_proposal.run_id != current_state.run_id
        or geometry_proposal.base != current_state.base
        or geometry_proposal.design_state_digest != current_state.state_digest
    ):
        raise ComponentIndexError("geometry program is stale for the design state")
    component_by_id = {item.component_id: item for item in proposal.components}
    development_by_id = {
        item.component_id: item for item in current_state.components
    }
    expected_component_digests = {
        component_id: digest_value(
            {
                "component": component.to_dict(),
                "development": (
                    development_by_id[component_id].to_dict()
                    if component_id in development_by_id
                    else None
                ),
            }
        )
        for component_id, component in component_by_id.items()
    }
    if dict(geometry_program.component_digests) != expected_component_digests:
        raise ComponentIndexError("compiled component digests contradict current state")
    expected_binding_digests = {
        binding.binding_id: digest_value(
            {
                "binding": binding.to_dict(),
                "component_digest": expected_component_digests.get(
                    binding.component_id
                ),
            }
        )
        for binding in geometry_proposal.semantic_bindings
    }
    if dict(geometry_program.semantic_binding_digests) != expected_binding_digests:
        raise ComponentIndexError("semantic binding digests contradict geometry")
    compiled_object_ids = {item.object_id for item in geometry_program.objects}
    bound_object_ids = {
        object_id
        for binding in geometry_proposal.semantic_bindings
        for object_id in binding.object_ids
    }
    if bound_object_ids != compiled_object_ids:
        raise ComponentIndexError("compiled geometry lacks exact semantic ownership")

    if isinstance(lifecycle_receipt, InitialSemanticGeometryReceipt):
        if (
            lifecycle_receipt.project_id != current_state.project_id
            or lifecycle_receipt.run_id != current_state.run_id
            or lifecycle_receipt.base_state_digest
            != current_state.base.require_digest()
            or lifecycle_receipt.design_state_digest != current_state.state_digest
            or lifecycle_receipt.component_proposal_digest
            != proposal.proposal_digest
            or lifecycle_receipt.geometry_proposal_digest
            != geometry_proposal.proposal_digest
            or lifecycle_receipt.geometry_program_digest
            != geometry_program.program_digest
        ):
            raise ComponentIndexError("initial lifecycle receipt is stale")
    elif isinstance(lifecycle_receipt, SemanticGeometryLifecycleReceipt):
        if (
            lifecycle_receipt.status
            is not SemanticGeometryLifecycleStatus.COMPILED
            or lifecycle_receipt.current_design_state_digest
            != current_state.state_digest
            or lifecycle_receipt.current_component_digest
            != proposal.proposal_digest
            or lifecycle_receipt.current_program_digest
            != geometry_program.program_digest
        ):
            raise ComponentIndexError("lifecycle receipt is stale or rejected")
    else:
        raise TypeError("lifecycle_receipt has an invalid type")

    binding_by_component = {
        binding.component_id: binding
        for binding in geometry_proposal.semantic_bindings
    }
    dependencies = tuple(
        sorted(current_state.dependencies, key=lambda item: item.dependency_id)
    )
    tasks = tuple(
        sorted(current_state.obligations, key=lambda item: item.obligation_id)
    )
    dependency_by_component: dict[str, list[DevelopmentDependency]] = {
        component_id: [] for component_id in component_by_id
    }
    for dependency in dependencies:
        if dependency.target_component_id not in component_by_id:
            raise ComponentIndexError("dependency target is absent from composition")
        dependency_by_component[dependency.target_component_id].append(dependency)
    root_id = next(
        item.component_id
        for item in proposal.components
        if item.parent_component_id is None
    )
    task_ids_by_component: dict[str, set[str]] = {
        component_id: set() for component_id in component_by_id
    }
    for task in tasks:
        matched: set[str] = set()
        for component_id, component in component_by_id.items():
            development = development_by_id.get(component_id)
            component_refs = {component.identity_ref, component.ref}
            if development is not None:
                component_refs.add(development.ref)
                if task.ref in development.requirement_refs:
                    matched.add(component_id)
            component_refs.update(
                item.source_ref
                for item in dependency_by_component[component_id]
            )
            if set(task.dependency_refs) & component_refs:
                matched.add(component_id)
        if not matched and set(task.dependency_refs) & {
            current_state.selected_schematic.ref,
            current_state.selected_schematic.option.ref,
            proposal.ref,
        }:
            matched.add(root_id)
        if not matched:
            matched.add(root_id)
        for component_id in matched:
            task_ids_by_component[component_id].add(task.obligation_id)

    entries: list[ComponentIndexEntry] = []
    for component_id, component in sorted(component_by_id.items()):
        binding = binding_by_component.get(component_id)
        development = development_by_id.get(component_id)
        component_dependencies = dependency_by_component[component_id]
        component_tasks = tuple(
            task
            for task in tasks
            if task.obligation_id in task_ids_by_component[component_id]
        )
        source_refs = set(component.source_refs)
        if binding is not None:
            source_refs.update(binding.commitment_refs)
            source_refs.update(binding.evidence_refs)
        if development is not None:
            source_refs.update(development.schematic_dependency_refs)
            source_refs.update(development.requirement_refs)
            source_refs.update(development.evidence_refs)
        for dependency in component_dependencies:
            source_refs.add(dependency.source_ref)
            source_refs.update(dependency.evidence_refs)
        for task in component_tasks:
            source_refs.add(task.ref)
            source_refs.update(task.source_refs)
            source_refs.update(task.dependency_refs)
        entries.append(
            ComponentIndexEntry(
                component=component,
                geometry_object_ids=(
                    () if binding is None else binding.object_ids
                ),
                binding_ids=(
                    () if binding is None else (binding.binding_id,)
                ),
                dependency_ids=tuple(
                    item.dependency_id for item in component_dependencies
                ),
                task_ids=tuple(sorted(task_ids_by_component[component_id])),
                source_refs=tuple(sorted(source_refs)),
            )
        )
    return ComponentIndex(
        project_id=current_state.project_id,
        run_id=current_state.run_id,
        base=current_state.base,
        design_state_digest=current_state.state_digest,
        component_proposal_digest=proposal.proposal_digest,
        geometry_program_digest=geometry_program.program_digest,
        control_tree_digest=control_tree.tree_digest,
        control_context_digest=control_context.context_digest,
        lifecycle_receipt_digest=lifecycle_receipt.receipt_digest,
        control_target_node_ref=control_context.target_node_ref,
        entries=tuple(entries),
        dependencies=dependencies,
        tasks=tasks,
    )


def compile_component_task_context(
    index: ComponentIndex,
    *,
    component_id: str,
    expected_index_digest: str,
    max_entries: int = 16,
) -> ComponentTaskContext:
    if not isinstance(index, ComponentIndex):
        raise TypeError("index must be a ComponentIndex")
    _sha(expected_index_digest, "expected_index_digest")
    if expected_index_digest != index.index_digest:
        raise ComponentIndexError("component index digest is stale")
    target = index.entry(component_id)
    entry_by_id = {item.component_id: item for item in index.entries}
    selected = {component_id}
    parent = target.parent_component_id
    while parent is not None:
        selected.add(parent)
        parent = entry_by_id[parent].parent_component_id
    selected.update(
        item.component_id
        for item in index.entries
        if item.parent_component_id == component_id
    )
    if len(selected) > max_entries:
        raise ComponentIndexError("component neighborhood exceeds task context bound")
    entries = tuple(
        item for item in index.entries if item.component_id in selected
    )
    dependency_ids = {
        value for item in entries for value in item.dependency_ids
    }
    task_ids = {value for item in entries for value in item.task_ids}
    dependencies = tuple(
        item for item in index.dependencies if item.dependency_id in dependency_ids
    )
    tasks = tuple(
        item for item in index.tasks if item.obligation_id in task_ids
    )
    explicit_refs = {
        item.source_ref for item in dependencies
    } | {
        value for item in tasks for value in item.dependency_refs
    }
    return ComponentTaskContext(
        index_digest=index.index_digest,
        component_id=component_id,
        control_context_digest=index.control_context_digest,
        control_target_node_ref=index.control_target_node_ref,
        entries=entries,
        dependencies=dependencies,
        tasks=tasks,
        explicit_dependency_refs=tuple(sorted(explicit_refs)),
        max_entries=max_entries,
    )


class ComponentIndexSnapshotRepository(RecordSink, Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


def persist_component_index_snapshot(
    repository: RecordSink,
    *,
    run: RunRef,
    destination: PersistenceDestination | None,
    index: ComponentIndex,
) -> ProjectRecordRef:
    """Persist a disposable derived snapshot through an explicit P036 port."""

    if not isinstance(index, ComponentIndex):
        raise TypeError("index must be a ComponentIndex")
    expected_run = RunRef(index.project_id, index.run_id, index.base)
    if run != expected_run:
        raise ComponentIndexError("snapshot run disagrees with component index")
    assigned = require_destination(
        destination,
        producer="component-index-derived-snapshot",
    )
    if (
        assigned.area is not PersistenceArea.RUN_RECORD
        or assigned.run_id != run.run_id
    ):
        raise ComponentIndexError(
            "component index snapshot requires its P036 run-record destination"
        )
    return repository.put_json(
        run=run,
        destination=assigned,
        record_kind="derived-component-index",
        payload=index.to_dict(),
    )


def load_component_index_snapshot(
    repository: ComponentIndexSnapshotRepository,
    ref: ProjectRecordRef,
    *,
    expected_index_digest: str,
) -> ComponentIndex:
    _sha(expected_index_digest, "expected_index_digest")
    index = ComponentIndex.from_dict(repository.load_json(ref))
    if index.index_digest != expected_index_digest:
        raise ComponentIndexError("persisted component index snapshot is stale")
    return index
