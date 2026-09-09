"""Canonical pure compiler for one exact-base neutral geometry proposal."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import require_identifier
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import AssemblyRole, AssetReference, DatumBinding, GeometryOperation, GeometryOperationKind, GeometryParameter, GeometryProgramError, GeometryProgramProposal, InterfaceDatum, require_sha256
from archflow.contracts.canonical import canonical_digest
from archflow.state.operational_state import require_logical_ref


class GeometryCompilationError(ValueError):
    """The compiler boundary received structurally invalid input."""


class GeometryIssueCode(StrEnum):
    EXACT_BASE_MISMATCH = "exact_base_mismatch"
    UNKNOWN_COMPONENT = "unknown_component"
    DUPLICATE_COMPONENT_BINDING = "duplicate_component_binding"
    AMBIGUOUS_OBJECT_OWNER = "ambiguous_object_owner"
    UNOWNED_OBJECT = "unowned_object"
    INACTIVE_COMMITMENT = "inactive_commitment"
    UNKNOWN_FRAME = "unknown_frame"
    FRAME_CYCLE = "frame_cycle"
    UNKNOWN_BINDING = "unknown_binding"
    UNKNOWN_OBJECT = "unknown_object"
    DUPLICATE_OBJECT = "duplicate_object"
    OPERATION_CYCLE = "operation_cycle"
    UNKNOWN_ASSET = "unknown_asset"
    MISSING_ASSET = "missing_asset"
    INVALID_ASSET_SUBSTITUTION = "invalid_asset_substitution"
    UNKNOWN_DATUM = "unknown_datum"
    INVALID_DATUM_BINDING = "invalid_datum_binding"
    RESTATED_DATUM_PARAMETER = "restated_datum_parameter"
    INVALID_ASSEMBLY = "invalid_assembly"
    PREDECESSOR_MISMATCH = "predecessor_mismatch"
    MISSING_REVISION_PRECONDITION = "missing_revision_precondition"
    STALE_REVISION_PRECONDITION = "stale_revision_precondition"
    REDUNDANT_REVISION_PRECONDITION = "redundant_revision_precondition"
    UNDECLARED_RETIREMENT = "undeclared_retirement"
    INVALID_RETIREMENT = "invalid_retirement"
    UNACKNOWLEDGED_DEPENDENCY_CHANGE = (
        "unacknowledged_dependency_change"
    )
    UNACKNOWLEDGED_FRAME_CHANGE = "unacknowledged_frame_change"
    UNACKNOWLEDGED_SEMANTIC_CHANGE = "unacknowledged_semantic_change"


class GeometryCompileStatus(StrEnum):
    COMPILED = "compiled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class GeometryIssue:
    code: GeometryIssueCode
    subject_id: str
    detail: str

    SCHEMA = "GeometryIssue@1"

    def __post_init__(self) -> None:
        if not isinstance(self.code, GeometryIssueCode):
            raise TypeError("code must be GeometryIssueCode")
        require_identifier(self.subject_id, "issue subject_id")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise GeometryCompilationError("issue detail must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "code": self.code.value,
            "subject_id": self.subject_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class AssetSubstitutionReceipt:
    requested_asset_id: str
    requested_sha256: str
    replacement_asset_id: str
    replacement_sha256: str
    loss_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    SCHEMA = "AssetSubstitutionReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.requested_asset_id, "requested_asset_id")
        object.__setattr__(
            self,
            "requested_sha256",
            require_sha256(self.requested_sha256, "requested_sha256"),
        )
        require_identifier(self.replacement_asset_id, "replacement_asset_id")
        object.__setattr__(
            self,
            "replacement_sha256",
            require_sha256(self.replacement_sha256, "replacement_sha256"),
        )
        if self.requested_asset_id == self.replacement_asset_id:
            raise GeometryCompilationError(
                "asset substitution must name a different replacement"
            )
        if (
            not isinstance(self.loss_codes, tuple)
            or not self.loss_codes
            or self.loss_codes != tuple(sorted(set(self.loss_codes)))
        ):
            raise GeometryCompilationError(
                "loss_codes require deterministic non-empty values"
            )
        for value in self.loss_codes:
            require_identifier(value, "loss code")
        if (
            not isinstance(self.evidence_refs, tuple)
            or not self.evidence_refs
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
        ):
            raise GeometryCompilationError(
                "substitution evidence_refs require deterministic values"
            )
        for value in self.evidence_refs:
            require_logical_ref(value, "substitution evidence_ref")

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "requested_asset_id": self.requested_asset_id,
            "requested_sha256": self.requested_sha256,
            "replacement_asset_id": self.replacement_asset_id,
            "replacement_sha256": self.replacement_sha256,
            "loss_codes": list(self.loss_codes),
            "evidence_refs": list(self.evidence_refs),
            "lossless": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class CompiledGeometryObject:
    object_id: str
    producer_op_id: str
    object_digest: str

    SCHEMA = "CompiledGeometryObject@1"

    def __post_init__(self) -> None:
        require_identifier(self.object_id, "compiled object_id")
        require_identifier(self.producer_op_id, "producer_op_id")
        object.__setattr__(
            self,
            "object_digest",
            require_sha256(self.object_digest, "object_digest"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "object_id": self.object_id,
            "producer_op_id": self.producer_op_id,
            "object_digest": self.object_digest,
        }


@dataclass(frozen=True, slots=True)
class CompiledGeometryProgram:
    proposal: GeometryProgramProposal
    operation_order: tuple[str, ...]
    frame_digests: tuple[tuple[str, str], ...]
    component_digests: tuple[tuple[str, str], ...]
    semantic_binding_digests: tuple[tuple[str, str], ...]
    objects: tuple[CompiledGeometryObject, ...]
    asset_substitutions: tuple[AssetSubstitutionReceipt, ...]
    interface_datums: tuple[InterfaceDatum, ...] = ()
    datum_bindings: tuple[DatumBinding, ...] = ()

    SCHEMA = "CompiledGeometryProgram@3"
    # Retained generations that still reload exactly (P090 bumped @2 -> @3;
    # @2 records predate interface datums and reload with empty datum fields).
    RETAINED_SCHEMAS = ("CompiledGeometryProgram@2",)
    ACCEPTED_SCHEMAS = (SCHEMA, *RETAINED_SCHEMAS)

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, GeometryProgramProposal):
            raise TypeError("proposal must be GeometryProgramProposal")
        if (
            not isinstance(self.operation_order, tuple)
            or len(self.operation_order) != len(self.proposal.operations)
            or set(self.operation_order)
            != {item.op_id for item in self.proposal.operations}
        ):
            raise GeometryCompilationError(
                "operation_order must cover every operation exactly once"
            )
        self._digest_pairs(self.frame_digests, "frame_digests")
        self._digest_pairs(self.component_digests, "component_digests")
        self._digest_pairs(
            self.semantic_binding_digests,
            "semantic_binding_digests",
        )
        if not isinstance(self.objects, tuple) or any(
            not isinstance(item, CompiledGeometryObject) for item in self.objects
        ):
            raise TypeError("objects contains an invalid item")
        object_ids = tuple(item.object_id for item in self.objects)
        if object_ids != tuple(sorted(set(object_ids))):
            raise GeometryCompilationError(
                "compiled objects require deterministic identities"
            )
        if (
            not isinstance(self.asset_substitutions, tuple)
            or any(
                not isinstance(item, AssetSubstitutionReceipt)
                for item in self.asset_substitutions
            )
        ):
            raise TypeError("asset_substitutions contains an invalid item")
        if not isinstance(self.interface_datums, tuple) or any(
            not isinstance(item, InterfaceDatum)
            for item in self.interface_datums
        ):
            raise TypeError("interface_datums contains an invalid item")
        if not isinstance(self.datum_bindings, tuple) or any(
            not isinstance(item, DatumBinding)
            for item in self.datum_bindings
        ):
            raise TypeError("datum_bindings contains an invalid item")

    @staticmethod
    def _digest_pairs(
        values: object,
        field: str,
    ) -> None:
        if not isinstance(values, tuple):
            raise TypeError(f"{field} must be a tuple")
        ids: list[str] = []
        for key, digest in values:
            require_identifier(key, field)
            require_sha256(digest, field)
            ids.append(key)
        if tuple(ids) != tuple(sorted(set(ids))):
            raise GeometryCompilationError(
                f"{field} requires deterministic identities"
            )

    @property
    def program_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def object_digest(self, object_id: str) -> str:
        require_identifier(object_id, "object_id")
        for item in self.objects:
            if item.object_id == object_id:
                return item.object_digest
        raise GeometryCompilationError(f"unknown compiled object: {object_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "proposal": self.proposal.to_dict(),
            "proposal_digest": self.proposal.proposal_digest,
            "operation_order": list(self.operation_order),
            "frame_digests": [
                {"frame_id": key, "digest": value}
                for key, value in self.frame_digests
            ],
            "component_digests": [
                {"component_id": key, "digest": value}
                for key, value in self.component_digests
            ],
            "semantic_binding_digests": [
                {"binding_id": key, "digest": value}
                for key, value in self.semantic_binding_digests
            ],
            "objects": [item.to_dict() for item in self.objects],
            "asset_substitutions": [
                item.to_dict() for item in self.asset_substitutions
            ],
            "interface_datums": [
                item.to_dict() for item in self.interface_datums
            ],
            "datum_bindings": [
                item.to_dict() for item in self.datum_bindings
            ],
            "execution_authority": False,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class GeometryCompilationReceipt:
    proposal_digest: str
    status: GeometryCompileStatus
    compiled_program_digest: str | None
    operation_order: tuple[str, ...]
    issues: tuple[GeometryIssue, ...]
    asset_substitutions: tuple[AssetSubstitutionReceipt, ...]

    SCHEMA = "GeometryCompilationReceipt@1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_digest",
            require_sha256(self.proposal_digest, "proposal_digest"),
        )
        if not isinstance(self.status, GeometryCompileStatus):
            raise TypeError("status must be GeometryCompileStatus")
        if self.compiled_program_digest is not None:
            object.__setattr__(
                self,
                "compiled_program_digest",
                require_sha256(
                    self.compiled_program_digest,
                    "compiled_program_digest",
                ),
            )
        if not isinstance(self.operation_order, tuple):
            raise TypeError("operation_order must be a tuple")
        for value in self.operation_order:
            require_identifier(value, "operation_order")
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, GeometryIssue) for item in self.issues
        ):
            raise TypeError("issues contains an invalid item")
        if not isinstance(self.asset_substitutions, tuple) or any(
            not isinstance(item, AssetSubstitutionReceipt)
            for item in self.asset_substitutions
        ):
            raise TypeError(
                "asset_substitutions contains an invalid item"
            )
        if self.status is GeometryCompileStatus.COMPILED:
            if self.compiled_program_digest is None or self.issues:
                raise GeometryCompilationError(
                    "compiled receipt cannot contain unresolved issues"
                )
        elif self.compiled_program_digest is not None or not self.issues:
            raise GeometryCompilationError(
                "rejected receipt requires issues and no compiled digest"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "proposal_digest": self.proposal_digest,
            "status": self.status.value,
            "compiled_program_digest": self.compiled_program_digest,
            "operation_order": list(self.operation_order),
            "issues": [item.to_dict() for item in self.issues],
            "asset_substitutions": [
                item.to_dict() for item in self.asset_substitutions
            ],
            "execution_authority": False,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class GeometryCompilationResult:
    program: CompiledGeometryProgram | None
    receipt: GeometryCompilationReceipt

    def __post_init__(self) -> None:
        if self.program is None:
            if self.receipt.status is not GeometryCompileStatus.REJECTED:
                raise GeometryCompilationError(
                    "missing program requires rejected receipt"
                )
        elif (
            self.receipt.status is not GeometryCompileStatus.COMPILED
            or self.program.program_digest
            != self.receipt.compiled_program_digest
        ):
            raise GeometryCompilationError(
                "compiled program and receipt disagree"
            )


def _issue(
    issues: list[GeometryIssue],
    code: GeometryIssueCode,
    subject_id: str,
    detail: str,
) -> None:
    issues.append(GeometryIssue(code, subject_id, detail))


def _frame_digests(
    proposal: GeometryProgramProposal,
    issues: list[GeometryIssue],
) -> dict[str, str]:
    frames = {item.frame_id: item for item in proposal.frames}
    result: dict[str, str] = {}
    visiting: set[str] = set()

    def visit(frame_id: str) -> str | None:
        if frame_id in result:
            return result[frame_id]
        if frame_id in visiting:
            _issue(
                issues,
                GeometryIssueCode.FRAME_CYCLE,
                frame_id,
                "coordinate-frame ancestry contains a cycle",
            )
            return None
        frame = frames[frame_id]
        visiting.add(frame_id)
        parent_digest: str | None = None
        if frame.parent_frame_id is not None:
            if frame.parent_frame_id not in frames:
                _issue(
                    issues,
                    GeometryIssueCode.UNKNOWN_FRAME,
                    frame_id,
                    "coordinate frame names an unknown parent",
                )
                visiting.remove(frame_id)
                return None
            parent_digest = visit(frame.parent_frame_id)
            if parent_digest is None:
                visiting.remove(frame_id)
                return None
        value = canonical_digest(
            {
                "frame": frame.to_dict(),
                "parent_digest": parent_digest,
            }
        )
        visiting.remove(frame_id)
        result[frame_id] = value
        return value

    for frame_id in sorted(frames):
        visit(frame_id)
    return result


def _semantic_digests(
    identity: "_StateIdentity",
    proposal: GeometryProgramProposal,
    active_commitment_refs: frozenset[str],
    issues: list[GeometryIssue],
) -> tuple[dict[str, str], dict[str, str]]:
    components = {item.component_id: item for item in identity.components}
    developments = {item.component_id: item for item in identity.developments}
    component_digests = {
        component_id: canonical_digest(
            {
                "component": component.to_dict(),
                "development": (
                    developments[component_id].to_dict()
                    if component_id in developments
                    else None
                ),
            }
        )
        for component_id, component in components.items()
    }
    result: dict[str, str] = {}
    bound_components: set[str] = set()
    for binding in proposal.semantic_bindings:
        if binding.component_id not in components:
            _issue(
                issues,
                GeometryIssueCode.UNKNOWN_COMPONENT,
                binding.binding_id,
                "semantic binding names a component absent from the selected design",
            )
        elif binding.component_id in bound_components:
            _issue(
                issues,
                GeometryIssueCode.DUPLICATE_COMPONENT_BINDING,
                binding.binding_id,
                "one component cannot own multiple geometry bindings",
            )
        bound_components.add(binding.component_id)
        for commitment_ref in binding.commitment_refs:
            if commitment_ref not in active_commitment_refs:
                _issue(
                    issues,
                    GeometryIssueCode.INACTIVE_COMMITMENT,
                    binding.binding_id,
                    "semantic binding names a non-active commitment",
                )
        result[binding.binding_id] = canonical_digest(
            {
                "binding": binding.to_dict(),
                "component_digest": component_digests.get(
                    binding.component_id
                ),
            }
        )
    return result, component_digests


def _resolve_assets(
    proposal: GeometryProgramProposal,
    available_asset_digests: Mapping[str, str],
    substitutions: tuple[AssetSubstitutionReceipt, ...],
    issues: list[GeometryIssue],
) -> tuple[dict[str, tuple[str, str, str | None]], tuple[AssetSubstitutionReceipt, ...]]:
    assets = {item.asset_id: item for item in proposal.assets}
    substitution_by_requested: dict[str, AssetSubstitutionReceipt] = {}
    for receipt in substitutions:
        if receipt.requested_asset_id in substitution_by_requested:
            _issue(
                issues,
                GeometryIssueCode.INVALID_ASSET_SUBSTITUTION,
                receipt.requested_asset_id,
                "multiple substitutions target the same asset",
            )
        substitution_by_requested[receipt.requested_asset_id] = receipt

    result: dict[str, tuple[str, str, str | None]] = {}
    used: list[AssetSubstitutionReceipt] = []
    for asset_id, asset in sorted(assets.items()):
        available = available_asset_digests.get(asset_id)
        if available == asset.sha256:
            result[asset_id] = (asset_id, asset.sha256, None)
            continue
        receipt = substitution_by_requested.get(asset_id)
        replacement: AssetReference | None = None
        if receipt is not None:
            replacement = assets.get(receipt.replacement_asset_id)
        if (
            receipt is None
            or receipt.requested_sha256 != asset.sha256
            or replacement is None
            or receipt.replacement_sha256 != replacement.sha256
            or available_asset_digests.get(replacement.asset_id)
            != replacement.sha256
        ):
            _issue(
                issues,
                GeometryIssueCode.MISSING_ASSET,
                asset_id,
                "asset content is unavailable and no valid explicit substitution exists",
            )
            continue
        result[asset_id] = (
            replacement.asset_id,
            replacement.sha256,
            receipt.receipt_digest,
        )
        used.append(receipt)
    for requested_id in substitution_by_requested:
        if requested_id not in assets:
            _issue(
                issues,
                GeometryIssueCode.INVALID_ASSET_SUBSTITUTION,
                requested_id,
                "substitution names an undeclared requested asset",
            )
    return result, tuple(
        sorted(used, key=lambda item: item.requested_asset_id)
    )


def _operation_graph(
    proposal: GeometryProgramProposal,
    frame_digests: Mapping[str, str],
    semantic_digests: Mapping[str, str],
    asset_resolutions: Mapping[str, tuple[str, str, str | None]],
    issues: list[GeometryIssue],
) -> tuple[
    tuple[str, ...],
    dict[str, GeometryOperation],
    dict[str, str],
    dict[str, str],
]:
    bindings = {item.binding_id for item in proposal.semantic_bindings}
    binding_by_id = {
        item.binding_id: item for item in proposal.semantic_bindings
    }
    assets = {item.asset_id for item in proposal.assets}
    operations = {item.op_id: item for item in proposal.operations}
    producer_by_object: dict[str, str] = {}
    owner_by_object: dict[str, str] = {}

    for binding in proposal.semantic_bindings:
        for object_id in binding.object_ids:
            prior_owner = owner_by_object.get(object_id)
            if prior_owner is not None:
                _issue(
                    issues,
                    GeometryIssueCode.AMBIGUOUS_OBJECT_OWNER,
                    object_id,
                    "geometry object is owned by more than one semantic component",
                )
            else:
                owner_by_object[object_id] = binding.component_id

    for operation in proposal.operations:
        if operation.frame_id not in frame_digests:
            _issue(
                issues,
                GeometryIssueCode.UNKNOWN_FRAME,
                operation.op_id,
                "operation names an unresolved coordinate frame",
            )
        for binding_id in operation.semantic_binding_ids:
            if binding_id not in bindings:
                _issue(
                    issues,
                    GeometryIssueCode.UNKNOWN_BINDING,
                    operation.op_id,
                    "operation names an unknown semantic binding",
                )
        covered_outputs = {
            object_id
            for binding_id in operation.semantic_binding_ids
            if binding_id in binding_by_id
            for object_id in binding_by_id[binding_id].object_ids
        }
        if not set(operation.output_object_ids) <= covered_outputs:
            _issue(
                issues,
                GeometryIssueCode.UNKNOWN_BINDING,
                operation.op_id,
                "operation output lacks semantic-binding coverage",
            )
        if (
            operation.kind is GeometryOperationKind.ASSET_INSTANCE
            and operation.asset_id not in assets
        ):
            _issue(
                issues,
                GeometryIssueCode.UNKNOWN_ASSET,
                operation.op_id,
                "asset instance names an undeclared asset",
            )
        elif operation.kind is GeometryOperationKind.ASSET_INSTANCE:
            asset = next(
                item
                for item in proposal.assets
                if item.asset_id == operation.asset_id
            )
            if operation.asset_socket_id not in asset.sockets:
                _issue(
                    issues,
                    GeometryIssueCode.UNKNOWN_ASSET,
                    operation.op_id,
                    "asset instance names an undeclared asset socket",
                )
        for object_id in operation.output_object_ids:
            if object_id in producer_by_object:
                _issue(
                    issues,
                    GeometryIssueCode.DUPLICATE_OBJECT,
                    object_id,
                    "stable object identity has multiple producers",
                )
            else:
                producer_by_object[object_id] = operation.op_id

    for binding in proposal.semantic_bindings:
        if not set(binding.object_ids) <= set(producer_by_object):
            _issue(
                issues,
                GeometryIssueCode.UNKNOWN_OBJECT,
                binding.binding_id,
                "semantic binding names an object with no producer",
            )
    for object_id in sorted(set(producer_by_object) - set(owner_by_object)):
        _issue(
            issues,
            GeometryIssueCode.UNOWNED_OBJECT,
            object_id,
            "produced geometry object has no semantic component owner",
        )

    dependencies: dict[str, set[str]] = {
        item.op_id: set() for item in proposal.operations
    }
    for operation in proposal.operations:
        for object_id in operation.input_object_ids:
            producer = producer_by_object.get(object_id)
            if producer is None:
                _issue(
                    issues,
                    GeometryIssueCode.UNKNOWN_OBJECT,
                    operation.op_id,
                    f"operation consumes unknown object {object_id}",
                )
            elif producer == operation.op_id:
                _issue(
                    issues,
                    GeometryIssueCode.OPERATION_CYCLE,
                    operation.op_id,
                    "operation consumes its own output",
                )
            else:
                dependencies[operation.op_id].add(producer)

    ready = sorted(
        op_id for op_id, incoming in dependencies.items() if not incoming
    )
    order: list[str] = []
    remaining = {key: set(value) for key, value in dependencies.items()}
    while ready:
        op_id = ready.pop(0)
        order.append(op_id)
        for candidate in sorted(remaining):
            if op_id in remaining[candidate]:
                remaining[candidate].remove(op_id)
                if (
                    not remaining[candidate]
                    and candidate not in order
                    and candidate not in ready
                ):
                    ready.append(candidate)
                    ready.sort()
    if len(order) != len(operations):
        cyclic = sorted(set(operations) - set(order))
        for op_id in cyclic:
            _issue(
                issues,
                GeometryIssueCode.OPERATION_CYCLE,
                op_id,
                "operation dependencies contain a cycle",
            )

    object_digests: dict[str, str] = {}
    if len(order) == len(operations):
        for op_id in order:
            operation = operations[op_id]
            if (
                operation.frame_id not in frame_digests
                or any(
                    binding_id not in semantic_digests
                    for binding_id in operation.semantic_binding_ids
                )
                or any(
                    object_id not in object_digests
                    for object_id in operation.input_object_ids
                )
            ):
                continue
            asset_resolution = None
            if operation.asset_id is not None:
                asset_resolution = asset_resolutions.get(operation.asset_id)
                if asset_resolution is None:
                    continue
            basis = {
                "operation": operation.to_dict(),
                "frame_digest": frame_digests[operation.frame_id],
                "semantic_binding_digests": [
                    semantic_digests[binding_id]
                    for binding_id in operation.semantic_binding_ids
                ],
                "input_object_digests": [
                    object_digests[object_id]
                    for object_id in operation.input_object_ids
                ],
                "asset_resolution": asset_resolution,
            }
            operation_digest = canonical_digest(basis)
            for object_id in operation.output_object_ids:
                object_digests[object_id] = canonical_digest(
                    {
                        "object_id": object_id,
                        "operation_digest": operation_digest,
                    }
                )
    return (
        tuple(order),
        operations,
        producer_by_object,
        object_digests,
    )


def _validate_assemblies(
    proposal: GeometryProgramProposal,
    operations: Mapping[str, GeometryOperation],
    producer_by_object: Mapping[str, str],
    issues: list[GeometryIssue],
) -> None:
    binding_ids = {item.binding_id for item in proposal.semantic_bindings}
    object_ids = set(producer_by_object)
    for assembly in proposal.assemblies:
        referenced = {
            assembly.host_object_id,
            *(
                object_id
                for member in assembly.members
                for object_id in member.object_ids
            ),
        }
        if not referenced <= object_ids:
            _issue(
                issues,
                GeometryIssueCode.INVALID_ASSEMBLY,
                assembly.assembly_id,
                "assembly names an unknown host or member object",
            )
            continue
        if not set(assembly.semantic_binding_ids) <= binding_ids:
            _issue(
                issues,
                GeometryIssueCode.INVALID_ASSEMBLY,
                assembly.assembly_id,
                "assembly names an unknown semantic binding",
            )
        for cut_object_id in assembly.objects_for(AssemblyRole.HOST_CUT):
            producer = operations[producer_by_object[cut_object_id]]
            if assembly.host_object_id not in producer.input_object_ids:
                _issue(
                    issues,
                    GeometryIssueCode.INVALID_ASSEMBLY,
                    assembly.assembly_id,
                    "host-cut geometry does not depend on its named host",
                )


def _validate_revision(
    proposal: GeometryProgramProposal,
    current_objects: Mapping[str, str],
    current_frame_digests: Mapping[str, str],
    current_semantic_digests: Mapping[str, str],
    operations: Mapping[str, GeometryOperation],
    prior: CompiledGeometryProgram | None,
    issues: list[GeometryIssue],
) -> None:
    if prior is None:
        if proposal.predecessor_program_digest is not None:
            _issue(
                issues,
                GeometryIssueCode.PREDECESSOR_MISMATCH,
                proposal.proposal_id,
                "initial proposal cannot name an unavailable predecessor",
            )
        if proposal.revisions or proposal.retirements:
            _issue(
                issues,
                GeometryIssueCode.STALE_REVISION_PRECONDITION,
                proposal.proposal_id,
                "initial proposal cannot revise or retire unknown objects",
            )
        return

    if (
        proposal.predecessor_program_digest is None
        or proposal.predecessor_program_digest != prior.program_digest
    ):
        _issue(
            issues,
            GeometryIssueCode.PREDECESSOR_MISMATCH,
            proposal.proposal_id,
            "proposal is not bound to the exact predecessor program",
        )
    prior_objects = {
        item.object_id: item.object_digest for item in prior.objects
    }
    revision_by_id = {item.object_id: item for item in proposal.revisions}
    retirement_by_id = {item.object_id: item for item in proposal.retirements}

    for object_id in sorted(set(prior_objects) & set(current_objects)):
        old_digest = prior_objects[object_id]
        new_digest = current_objects[object_id]
        revision = revision_by_id.get(object_id)
        if old_digest != new_digest:
            if revision is None:
                _issue(
                    issues,
                    GeometryIssueCode.MISSING_REVISION_PRECONDITION,
                    object_id,
                    "changed stable object lacks an exact prior-digest precondition",
                )
            elif revision.expected_digest != old_digest:
                _issue(
                    issues,
                    GeometryIssueCode.STALE_REVISION_PRECONDITION,
                    object_id,
                    "revision precondition does not match the predecessor object",
                )
        elif revision is not None:
            _issue(
                issues,
                GeometryIssueCode.REDUNDANT_REVISION_PRECONDITION,
                object_id,
                "revision precondition names an unchanged object",
            )

    for object_id in sorted(set(current_objects) - set(prior_objects)):
        if object_id in revision_by_id:
            _issue(
                issues,
                GeometryIssueCode.STALE_REVISION_PRECONDITION,
                object_id,
                "new object cannot carry a prior-object revision precondition",
            )
    for object_id in sorted(set(prior_objects) - set(current_objects)):
        retirement = retirement_by_id.get(object_id)
        if retirement is None:
            _issue(
                issues,
                GeometryIssueCode.UNDECLARED_RETIREMENT,
                object_id,
                "predecessor object disappeared without explicit retirement",
            )
        elif retirement.expected_digest != prior_objects[object_id]:
            _issue(
                issues,
                GeometryIssueCode.INVALID_RETIREMENT,
                object_id,
                "retirement does not match the predecessor object digest",
            )
    for object_id in retirement_by_id:
        if object_id not in prior_objects or object_id in current_objects:
            _issue(
                issues,
                GeometryIssueCode.INVALID_RETIREMENT,
                object_id,
                "retirement must name a removed predecessor object",
            )

    prior_operations = {
        item.op_id: item for item in prior.proposal.operations
    }
    prior_frames = dict(prior.frame_digests)
    prior_semantics = dict(prior.semantic_binding_digests)
    for op_id, operation in operations.items():
        prior_operation = prior_operations.get(op_id)
        if prior_operation is None:
            continue
        for object_id in operation.input_object_ids:
            if (
                object_id in prior_objects
                and object_id in current_objects
                and prior_objects[object_id] != current_objects[object_id]
                and object_id not in operation.responds_to_object_ids
            ):
                _issue(
                    issues,
                    GeometryIssueCode.UNACKNOWLEDGED_DEPENDENCY_CHANGE,
                    op_id,
                    f"operation did not acknowledge changed input {object_id}",
                )
        if (
            operation.frame_id in prior_frames
            and operation.frame_id in current_frame_digests
            and prior_frames[operation.frame_id]
            != current_frame_digests[operation.frame_id]
            and operation.frame_id not in operation.responds_to_frame_ids
        ):
            _issue(
                issues,
                GeometryIssueCode.UNACKNOWLEDGED_FRAME_CHANGE,
                op_id,
                "operation did not acknowledge its changed coordinate frame",
            )
        for binding_id in operation.semantic_binding_ids:
            if (
                binding_id in prior_semantics
                and binding_id in current_semantic_digests
                and prior_semantics[binding_id]
                != current_semantic_digests[binding_id]
                and binding_id not in operation.responds_to_binding_ids
            ):
                _issue(
                    issues,
                    GeometryIssueCode.UNACKNOWLEDGED_SEMANTIC_CHANGE,
                    op_id,
                    f"operation did not acknowledge changed binding {binding_id}",
                )


def resolve_interface_datums(
    proposal: GeometryProgramProposal,
    interface_datums: tuple[InterfaceDatum, ...],
    datum_bindings: tuple[DatumBinding, ...],
    issues: list[GeometryIssue],
) -> GeometryProgramProposal:
    """Resolve datum bindings into ordinary literal parameters (P090).

    Contact-by-construction: the authored proposal never restates a
    bound coordinate; this pass derives each bound parameter from its
    published datum, so every consumer of the value shares one node and
    downstream evaluators see plain parameters. On any binding issue
    the proposal is returned unchanged and the recorded issues reject
    the compile.
    """

    if not isinstance(interface_datums, tuple) or any(
        not isinstance(item, InterfaceDatum) for item in interface_datums
    ):
        raise TypeError("interface_datums contains an invalid item")
    if not isinstance(datum_bindings, tuple) or any(
        not isinstance(item, DatumBinding) for item in datum_bindings
    ):
        raise TypeError("datum_bindings contains an invalid item")
    datum_by_id: dict[str, InterfaceDatum] = {}
    for datum in interface_datums:
        if datum.datum_id in datum_by_id:
            raise GeometryCompilationError(
                f"duplicate interface datum: {datum.datum_id}"
            )
        datum_by_id[datum.datum_id] = datum
    binding_ids = tuple(item.binding_id for item in datum_bindings)
    if len(binding_ids) != len(set(binding_ids)):
        raise GeometryCompilationError(
            "datum_bindings contains duplicate binding ids"
        )
    if not datum_bindings:
        return proposal

    operations = {item.op_id: item for item in proposal.operations}
    additions: dict[str, dict[str, GeometryParameter]] = {}
    before = len(issues)
    for binding in datum_bindings:
        datum = datum_by_id.get(binding.datum_id)
        if datum is None:
            _issue(
                issues,
                GeometryIssueCode.UNKNOWN_DATUM,
                binding.binding_id,
                "binding names an undeclared interface datum",
            )
            continue
        operation = operations.get(binding.op_id)
        if operation is None:
            _issue(
                issues,
                GeometryIssueCode.INVALID_DATUM_BINDING,
                binding.binding_id,
                "binding names an unknown operation",
            )
            continue
        if binding.parameter_name in {
            item.name for item in operation.parameters
        }:
            _issue(
                issues,
                GeometryIssueCode.RESTATED_DATUM_PARAMETER,
                binding.binding_id,
                "bound parameter is restated as a literal on the operation",
            )
            continue
        synthesized = additions.setdefault(binding.op_id, {})
        if binding.parameter_name in synthesized:
            _issue(
                issues,
                GeometryIssueCode.INVALID_DATUM_BINDING,
                binding.binding_id,
                "parameter is bound by more than one datum binding",
            )
            continue
        try:
            kind, value = datum.resolve(binding.component)
        except GeometryProgramError as exc:
            _issue(
                issues,
                GeometryIssueCode.INVALID_DATUM_BINDING,
                binding.binding_id,
                str(exc),
            )
            continue
        synthesized[binding.parameter_name] = GeometryParameter.create(
            name=binding.parameter_name,
            kind=kind,
            value=value,
            unit=datum.unit,
        )
    if len(issues) > before:
        return proposal
    resolved_operations = tuple(
        replace(
            operation,
            parameters=tuple(
                sorted(
                    (
                        *operation.parameters,
                        *additions[operation.op_id].values(),
                    ),
                    key=lambda item: item.name,
                )
            ),
        )
        if operation.op_id in additions
        else operation
        for operation in proposal.operations
    )
    return replace(proposal, operations=resolved_operations)


@dataclass(frozen=True, slots=True)
class _StateIdentity:
    """What binding a program to a state actually requires (P102)."""

    project_id: str
    run_id: str
    base: object
    state_digest: str
    components: tuple
    developments: tuple


def _state_identity(state: object) -> _StateIdentity:
    """The canonical ``StateRecord@1`` or the developed-design projection it yields.

    The compiler never reads a design decision off the state: it checks that
    the proposal and the state are exact-base peers. Both the record and the
    legacy state answer those four questions, so the compiler depends on the
    question, not on either class.
    """

    from archflow.state.state_record import StateRecord, design_components_of

    if isinstance(state, StateRecord):
        components, developments = design_components_of(state), ()
    elif isinstance(state, DevelopedDesignState):
        components, developments = state.selected_schematic.option.proposal.components, state.components
    else:
        raise TypeError("state must be StateRecord or DevelopedDesignState")
    return _StateIdentity(state.project_id, state.run_id, state.base, state.state_digest, tuple(components), tuple(developments))


def compile_geometry_program(
    state: DevelopedDesignState,
    proposal: GeometryProgramProposal,
    *,
    active_commitment_refs: tuple[str, ...] = (),
    available_asset_digests: Mapping[str, str] | None = None,
    prior_program: CompiledGeometryProgram | None = None,
    asset_substitutions: tuple[AssetSubstitutionReceipt, ...] = (),
    interface_datums: tuple[InterfaceDatum, ...] = (),
    datum_bindings: tuple[DatumBinding, ...] = (),
) -> GeometryCompilationResult:
    """Compile a proposal or return a detached, explicit rejection receipt.

    Receipts carry the authored proposal digest; when datum bindings are
    supplied, the compiled program embeds the datum-resolved proposal
    together with the datums and bindings as its derivation receipt.
    """

    identity = _state_identity(state)
    if not isinstance(proposal, GeometryProgramProposal):
        raise TypeError("proposal must be GeometryProgramProposal")
    if not isinstance(active_commitment_refs, tuple):
        raise TypeError("active_commitment_refs must be a tuple")
    active_refs: set[str] = set()
    for value in active_commitment_refs:
        require_logical_ref(value, "active_commitment_ref")
        active_refs.add(value)
    if len(active_refs) != len(active_commitment_refs):
        raise GeometryCompilationError(
            "active_commitment_refs contains duplicates"
        )
    if available_asset_digests is None:
        available_asset_digests = {}
    if not isinstance(available_asset_digests, Mapping):
        raise TypeError("available_asset_digests must be a mapping")
    normalized_assets: dict[str, str] = {}
    for asset_id, digest in available_asset_digests.items():
        require_identifier(asset_id, "available asset_id")
        normalized_assets[asset_id] = require_sha256(
            digest,
            "available asset digest",
        )
    if not isinstance(asset_substitutions, tuple) or any(
        not isinstance(item, AssetSubstitutionReceipt)
        for item in asset_substitutions
    ):
        raise TypeError("asset_substitutions contains an invalid item")

    issues: list[GeometryIssue] = []
    authored_digest = proposal.proposal_digest
    proposal = resolve_interface_datums(
        proposal,
        interface_datums,
        datum_bindings,
        issues,
    )
    if (
        proposal.project_id != identity.project_id
        or proposal.run_id != identity.run_id
        or proposal.base != identity.base
        or proposal.design_state_digest != identity.state_digest
    ):
        _issue(
            issues,
            GeometryIssueCode.EXACT_BASE_MISMATCH,
            proposal.proposal_id,
            "geometry proposal and developed design state are not exact-base peers",
        )

    frame_digests = _frame_digests(proposal, issues)
    semantic_digests, component_digests = _semantic_digests(
        identity,
        proposal,
        frozenset(active_refs),
        issues,
    )
    asset_resolutions, used_substitutions = _resolve_assets(
        proposal,
        normalized_assets,
        asset_substitutions,
        issues,
    )
    (
        operation_order,
        operations,
        producer_by_object,
        object_digests,
    ) = _operation_graph(
        proposal,
        frame_digests,
        semantic_digests,
        asset_resolutions,
        issues,
    )
    _validate_assemblies(
        proposal,
        operations,
        producer_by_object,
        issues,
    )
    _validate_revision(
        proposal,
        object_digests,
        frame_digests,
        semantic_digests,
        operations,
        prior_program,
        issues,
    )

    ordered_issues = tuple(
        sorted(
            issues,
            key=lambda item: (item.code.value, item.subject_id, item.detail),
        )
    )
    if ordered_issues:
        receipt = GeometryCompilationReceipt(
            proposal_digest=authored_digest,
            status=GeometryCompileStatus.REJECTED,
            compiled_program_digest=None,
            operation_order=operation_order,
            issues=ordered_issues,
            asset_substitutions=used_substitutions,
        )
        return GeometryCompilationResult(None, receipt)

    objects = tuple(
        CompiledGeometryObject(
            object_id=object_id,
            producer_op_id=producer_by_object[object_id],
            object_digest=object_digest,
        )
        for object_id, object_digest in sorted(object_digests.items())
    )
    program = CompiledGeometryProgram(
        proposal=proposal,
        operation_order=operation_order,
        frame_digests=tuple(sorted(frame_digests.items())),
        component_digests=tuple(sorted(component_digests.items())),
        semantic_binding_digests=tuple(sorted(semantic_digests.items())),
        objects=objects,
        asset_substitutions=used_substitutions,
        interface_datums=interface_datums,
        datum_bindings=datum_bindings,
    )
    receipt = GeometryCompilationReceipt(
        proposal_digest=authored_digest,
        status=GeometryCompileStatus.COMPILED,
        compiled_program_digest=program.program_digest,
        operation_order=operation_order,
        issues=(),
        asset_substitutions=used_substitutions,
    )
    return GeometryCompilationResult(program, receipt)


__all__ = [
    "AssetSubstitutionReceipt",
    "CompiledGeometryObject",
    "CompiledGeometryProgram",
    "GeometryCompilationError",
    "GeometryCompilationReceipt",
    "GeometryCompilationResult",
    "GeometryCompileStatus",
    "GeometryIssue",
    "GeometryIssueCode",
    "compile_geometry_program",
    "resolve_interface_datums",
]
