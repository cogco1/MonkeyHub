"""Atomic value compilation for one semantic-and-geometry lifecycle move.

This module owns no writer.  A compiled result may later be persisted through
P036, while a rejected result contains receipts only and exposes no successor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from archflow.project.refs import require_identifier
from archflow.compilers.geometry import (
    AssetSubstitutionReceipt,
    CompiledGeometryProgram,
    GeometryCompilationReceipt,
    GeometryCompileStatus,
    compile_geometry_program,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import GeometryProgramProposal, digest_value
from archflow.state.geometry_program import DatumBinding, InterfaceDatum
from archflow.state.operational_state import require_logical_ref
from archflow.state.spatial import (
    ComponentTransitionReceipt,
    DesignComponent,
    SpatialOptionProposal,
    SpatialProposalError,
    compile_component_transition,
)
from archflow.contracts.canonical import require_sha256


class SemanticGeometryLifecycleStatus(StrEnum):
    COMPILED = "compiled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class InitialSemanticGeometryReceipt:
    """Root binding for the first selected component tree and geometry program."""

    transaction_id: str
    project_id: str
    run_id: str
    base_state_digest: str
    design_state_digest: str
    component_proposal_digest: str
    geometry_proposal_digest: str
    geometry_program_digest: str
    source_refs: tuple[str, ...]

    SCHEMA = "InitialSemanticGeometryReceipt@1"

    def __post_init__(self) -> None:
        require_identifier(self.transaction_id, "transaction_id")
        require_identifier(self.project_id, "project_id")
        require_identifier(self.run_id, "run_id")
        for value, field in (
            (self.base_state_digest, "base_state_digest"),
            (self.design_state_digest, "design_state_digest"),
            (self.component_proposal_digest, "component_proposal_digest"),
            (self.geometry_proposal_digest, "geometry_proposal_digest"),
            (self.geometry_program_digest, "geometry_program_digest"),
        ):
            require_sha256(value, field)
        if (
            not isinstance(self.source_refs, tuple)
            or not self.source_refs
            or any(not isinstance(item, str) or not item for item in self.source_refs)
            or self.source_refs != tuple(sorted(set(self.source_refs)))
        ):
            raise ValueError("source_refs must be sorted unique logical references")
        for value in self.source_refs:
            require_logical_ref(value, "source_ref")

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "transaction_id": self.transaction_id,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base_state_digest": self.base_state_digest,
            "design_state_digest": self.design_state_digest,
            "component_proposal_digest": self.component_proposal_digest,
            "geometry_proposal_digest": self.geometry_proposal_digest,
            "geometry_program_digest": self.geometry_program_digest,
            "source_refs": list(self.source_refs),
            "initial_binding": True,
            "persistence_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class InitialSemanticGeometryResult:
    receipt: InitialSemanticGeometryReceipt
    component_proposal: SpatialOptionProposal
    geometry_program: CompiledGeometryProgram

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, InitialSemanticGeometryReceipt):
            raise TypeError("receipt must be InitialSemanticGeometryReceipt")
        if not isinstance(self.component_proposal, SpatialOptionProposal):
            raise TypeError("component_proposal must be SpatialOptionProposal")
        if not isinstance(self.geometry_program, CompiledGeometryProgram):
            raise TypeError("geometry_program must be CompiledGeometryProgram")
        if (
            self.component_proposal.proposal_digest
            != self.receipt.component_proposal_digest
            or self.geometry_program.program_digest
            != self.receipt.geometry_program_digest
        ):
            raise ValueError("initial semantic-geometry result digests disagree")


class SemanticGeometryLifecycleIssueCode(StrEnum):
    STATE_PROPOSAL_MISMATCH = "state_proposal_mismatch"
    PREDECESSOR_PROGRAM_MISMATCH = "predecessor_program_mismatch"
    COMPONENT_TRANSITION_REJECTED = "component_transition_rejected"
    GEOMETRY_COMPILATION_REJECTED = "geometry_compilation_rejected"
    MISSING_GEOMETRY_RESPONSE = "missing_geometry_response"
    INVALID_REVALIDATION = "invalid_revalidation"
    RETIREMENT_GEOMETRY_MISMATCH = "retirement_geometry_mismatch"
    PRESERVED_GEOMETRY_CHANGED = "preserved_geometry_changed"


@dataclass(frozen=True, slots=True)
class SemanticGeometryLifecycleIssue:
    code: SemanticGeometryLifecycleIssueCode
    subject_id: str
    detail: str

    SCHEMA = "SemanticGeometryLifecycleIssue@1"

    def __post_init__(self) -> None:
        if not isinstance(self.code, SemanticGeometryLifecycleIssueCode):
            raise TypeError("code must be SemanticGeometryLifecycleIssueCode")
        require_identifier(self.subject_id, "subject_id")
        if not isinstance(self.detail, str) or not self.detail:
            raise ValueError("detail must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "code": self.code.value,
            "subject_id": self.subject_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SemanticGeometryLifecycleReceipt:
    transaction_id: str
    status: SemanticGeometryLifecycleStatus
    predecessor_component_digest: str
    current_component_digest: str
    predecessor_design_state_digest: str
    current_design_state_digest: str
    predecessor_program_digest: str
    current_program_digest: str | None
    component_transition: ComponentTransitionReceipt | None
    geometry_compilation: GeometryCompilationReceipt | None
    geometry_changed_component_ids: tuple[str, ...]
    revalidated_component_ids: tuple[str, ...]
    preserved_component_ids: tuple[str, ...]
    retired_component_ids: tuple[str, ...]
    issues: tuple[SemanticGeometryLifecycleIssue, ...]

    SCHEMA = "SemanticGeometryLifecycleReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_id, str) or not self.transaction_id:
            raise ValueError("transaction_id must be non-empty")
        if not isinstance(self.status, SemanticGeometryLifecycleStatus):
            raise TypeError("status must be SemanticGeometryLifecycleStatus")
        for value, field in (
            (self.predecessor_component_digest, "predecessor_component_digest"),
            (self.current_component_digest, "current_component_digest"),
            (self.predecessor_design_state_digest, "predecessor_design_state_digest"),
            (self.current_design_state_digest, "current_design_state_digest"),
            (self.predecessor_program_digest, "predecessor_program_digest"),
        ):
            require_sha256(value, field)
        if self.current_program_digest is not None:
            require_sha256(self.current_program_digest, "current_program_digest")
        if self.component_transition is not None and not isinstance(
            self.component_transition,
            ComponentTransitionReceipt,
        ):
            raise TypeError("component_transition has invalid type")
        if self.geometry_compilation is not None and not isinstance(
            self.geometry_compilation,
            GeometryCompilationReceipt,
        ):
            raise TypeError("geometry_compilation has invalid type")
        for values, field in (
            (
                self.geometry_changed_component_ids,
                "geometry_changed_component_ids",
            ),
            (self.revalidated_component_ids, "revalidated_component_ids"),
            (self.preserved_component_ids, "preserved_component_ids"),
            (self.retired_component_ids, "retired_component_ids"),
        ):
            _sorted_ids(values, field)
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, SemanticGeometryLifecycleIssue)
            for item in self.issues
        ):
            raise TypeError("issues contains an invalid item")
        if self.status is SemanticGeometryLifecycleStatus.COMPILED:
            if (
                self.current_program_digest is None
                or self.component_transition is None
                or self.geometry_compilation is None
                or self.issues
            ):
                raise ValueError("compiled lifecycle receipt is incomplete")
        elif self.current_program_digest is not None or not self.issues:
            raise ValueError("rejected lifecycle receipt is inconsistent")

    @property
    def receipt_digest(self) -> str:
        return digest_value(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "transaction_id": self.transaction_id,
            "status": self.status.value,
            "predecessor_component_digest": self.predecessor_component_digest,
            "current_component_digest": self.current_component_digest,
            "predecessor_design_state_digest": (
                self.predecessor_design_state_digest
            ),
            "current_design_state_digest": self.current_design_state_digest,
            "predecessor_program_digest": self.predecessor_program_digest,
            "current_program_digest": self.current_program_digest,
            "component_transition": (
                None
                if self.component_transition is None
                else self.component_transition.to_dict()
            ),
            "geometry_compilation": (
                None
                if self.geometry_compilation is None
                else self.geometry_compilation.to_dict()
            ),
            "geometry_changed_component_ids": list(
                self.geometry_changed_component_ids
            ),
            "revalidated_component_ids": list(
                self.revalidated_component_ids
            ),
            "preserved_component_ids": list(self.preserved_component_ids),
            "retired_component_ids": list(self.retired_component_ids),
            "issues": [item.to_dict() for item in self.issues],
            "persistence_authority": False,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class SemanticGeometryLifecycleResult:
    receipt: SemanticGeometryLifecycleReceipt
    component_proposal: SpatialOptionProposal | None = None
    geometry_program: CompiledGeometryProgram | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, SemanticGeometryLifecycleReceipt):
            raise TypeError("receipt must be SemanticGeometryLifecycleReceipt")
        compiled = self.receipt.status is SemanticGeometryLifecycleStatus.COMPILED
        if compiled:
            if not isinstance(self.component_proposal, SpatialOptionProposal):
                raise TypeError("compiled result requires component_proposal")
            if not isinstance(self.geometry_program, CompiledGeometryProgram):
                raise TypeError("compiled result requires geometry_program")
            if (
                self.component_proposal.proposal_digest
                != self.receipt.current_component_digest
                or self.geometry_program.program_digest
                != self.receipt.current_program_digest
            ):
                raise ValueError("compiled lifecycle result digests disagree")
        elif (
            self.component_proposal is not None
            or self.geometry_program is not None
        ):
            raise ValueError("rejected lifecycle cannot expose a successor")


def bind_initial_semantic_geometry(
    *,
    transaction_id: str,
    current_state: DevelopedDesignState,
    current_proposal: SpatialOptionProposal,
    geometry_program: CompiledGeometryProgram,
    source_refs: tuple[str, ...],
) -> InitialSemanticGeometryResult:
    """Bind the first selected semantic tree to an already compiled program.

    Geometry authoring and deterministic compilation occur at their existing
    boundary. This root receipt prevents callers from fabricating a predecessor
    lifecycle merely to enter the later revision protocol.
    """

    require_identifier(transaction_id, "transaction_id")
    if not isinstance(current_state, DevelopedDesignState):
        raise TypeError("current_state must be DevelopedDesignState")
    if not isinstance(current_proposal, SpatialOptionProposal):
        raise TypeError("current_proposal must be SpatialOptionProposal")
    if not isinstance(geometry_program, CompiledGeometryProgram):
        raise TypeError("geometry_program must be CompiledGeometryProgram")
    if current_state.selected_schematic.option.proposal != current_proposal:
        raise ValueError("current state does not select the component proposal")
    proposal = geometry_program.proposal
    if (
        proposal.project_id != current_state.project_id
        or proposal.run_id != current_state.run_id
        or proposal.base != current_state.base
        or proposal.design_state_digest != current_state.state_digest
    ):
        raise ValueError("geometry program does not bind the exact design state")
    receipt = InitialSemanticGeometryReceipt(
        transaction_id=transaction_id,
        project_id=current_state.project_id,
        run_id=current_state.run_id,
        base_state_digest=current_state.base.require_digest(),
        design_state_digest=current_state.state_digest,
        component_proposal_digest=current_proposal.proposal_digest,
        geometry_proposal_digest=proposal.proposal_digest,
        geometry_program_digest=geometry_program.program_digest,
        source_refs=source_refs,
    )
    return InitialSemanticGeometryResult(
        receipt=receipt,
        component_proposal=current_proposal,
        geometry_program=geometry_program,
    )


def compile_semantic_geometry_lifecycle(
    *,
    transaction_id: str,
    predecessor_state: DevelopedDesignState,
    current_state: DevelopedDesignState,
    predecessor_proposal: SpatialOptionProposal,
    current_proposal: SpatialOptionProposal,
    prior_program: CompiledGeometryProgram,
    geometry_proposal: GeometryProgramProposal,
    retired_component_ids: tuple[str, ...] = (),
    revalidated_component_ids: tuple[str, ...] = (),
    active_commitment_refs: tuple[str, ...] = (),
    available_asset_digests: Mapping[str, str] | None = None,
    asset_substitutions: tuple[AssetSubstitutionReceipt, ...] = (),
    interface_datums: tuple[InterfaceDatum, ...] = (),
    datum_bindings: tuple[DatumBinding, ...] = (),
) -> SemanticGeometryLifecycleResult:
    """Compile both successor values or expose neither of them.

    ``interface_datums``/``datum_bindings`` reach the geometry compiler
    so successor programs derive bound parameters from published datums
    (P090 on the lifecycle path).
    """

    require_identifier(transaction_id, "transaction_id")
    for value, expected, field in (
        (predecessor_state, DevelopedDesignState, "predecessor_state"),
        (current_state, DevelopedDesignState, "current_state"),
        (predecessor_proposal, SpatialOptionProposal, "predecessor_proposal"),
        (current_proposal, SpatialOptionProposal, "current_proposal"),
        (prior_program, CompiledGeometryProgram, "prior_program"),
        (geometry_proposal, GeometryProgramProposal, "geometry_proposal"),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{field} has invalid type")
    _sorted_ids(retired_component_ids, "retired_component_ids")
    _sorted_ids(revalidated_component_ids, "revalidated_component_ids")

    base = _receipt_base(
        transaction_id,
        predecessor_state,
        current_state,
        predecessor_proposal,
        current_proposal,
        prior_program,
    )
    state_issues: list[SemanticGeometryLifecycleIssue] = []
    if (
        predecessor_state.selected_schematic.option.proposal
        != predecessor_proposal
        or current_state.selected_schematic.option.proposal != current_proposal
        or predecessor_state.project_id != current_state.project_id
        or predecessor_state.run_id != current_state.run_id
        or predecessor_state.base != current_state.base
    ):
        state_issues.append(
            _issue(
                SemanticGeometryLifecycleIssueCode.STATE_PROPOSAL_MISMATCH,
                transaction_id,
                "developed states do not embed the supplied component proposals",
            )
        )
    if (
        prior_program.proposal.design_state_digest
        != predecessor_state.state_digest
        or geometry_proposal.design_state_digest != current_state.state_digest
        or geometry_proposal.predecessor_program_digest
        != prior_program.program_digest
    ):
        state_issues.append(
            _issue(
                SemanticGeometryLifecycleIssueCode.PREDECESSOR_PROGRAM_MISMATCH,
                transaction_id,
                "geometry lifecycle does not bind both exact design states and predecessor program",
            )
        )
    if state_issues:
        return _rejected(base, issues=tuple(state_issues))

    try:
        component_transition = compile_component_transition(
            predecessor_proposal,
            current_proposal,
            retired_component_ids=retired_component_ids,
        )
    except SpatialProposalError as exc:
        return _rejected(
            base,
            issues=(
                _issue(
                    SemanticGeometryLifecycleIssueCode.COMPONENT_TRANSITION_REJECTED,
                    transaction_id,
                    f"{type(exc).__name__}: {exc}",
                ),
            ),
        )

    geometry = compile_geometry_program(
        current_state,
        geometry_proposal,
        active_commitment_refs=active_commitment_refs,
        available_asset_digests=available_asset_digests,
        prior_program=prior_program,
        asset_substitutions=asset_substitutions,
        interface_datums=interface_datums,
        datum_bindings=datum_bindings,
    )
    if geometry.receipt.status is not GeometryCompileStatus.COMPILED:
        details = ", ".join(
            f"{item.code.value}:{item.subject_id}"
            for item in geometry.receipt.issues
        )
        return _rejected(
            base,
            component_transition=component_transition,
            geometry_compilation=geometry.receipt,
            issues=(
                _issue(
                    SemanticGeometryLifecycleIssueCode.GEOMETRY_COMPILATION_REJECTED,
                    transaction_id,
                    details,
                ),
            ),
        )
    assert geometry.program is not None

    lifecycle_issues, changed_geometry = _cross_validate_lifecycle(
        predecessor_proposal,
        current_proposal,
        prior_program,
        geometry.program,
        component_transition,
        revalidated_component_ids,
    )
    if lifecycle_issues:
        return _rejected(
            base,
            component_transition=component_transition,
            geometry_compilation=geometry.receipt,
            geometry_changed_component_ids=changed_geometry,
            revalidated_component_ids=revalidated_component_ids,
            issues=lifecycle_issues,
        )

    receipt = SemanticGeometryLifecycleReceipt(
        **base,
        status=SemanticGeometryLifecycleStatus.COMPILED,
        current_program_digest=geometry.program.program_digest,
        component_transition=component_transition,
        geometry_compilation=geometry.receipt,
        geometry_changed_component_ids=changed_geometry,
        revalidated_component_ids=revalidated_component_ids,
        preserved_component_ids=component_transition.preserved_component_ids,
        retired_component_ids=component_transition.retired_component_ids,
        issues=(),
    )
    return SemanticGeometryLifecycleResult(
        receipt=receipt,
        component_proposal=current_proposal,
        geometry_program=geometry.program,
    )


def _cross_validate_lifecycle(
    predecessor_proposal: SpatialOptionProposal,
    current_proposal: SpatialOptionProposal,
    prior_program: CompiledGeometryProgram,
    current_program: CompiledGeometryProgram,
    transition: ComponentTransitionReceipt,
    revalidated_component_ids: tuple[str, ...],
) -> tuple[tuple[SemanticGeometryLifecycleIssue, ...], tuple[str, ...]]:
    before_direct = _owned_objects(prior_program)
    after_direct = _owned_objects(current_program)
    before_components = {
        item.component_id: item for item in predecessor_proposal.components
    }
    after_components = {
        item.component_id: item for item in current_proposal.components
    }
    invalidated_survivors = set(transition.invalidated_component_ids) & set(
        after_components
    )
    revalidated = set(revalidated_component_ids)
    issues: list[SemanticGeometryLifecycleIssue] = []
    if not revalidated <= invalidated_survivors:
        issues.append(
            _issue(
                SemanticGeometryLifecycleIssueCode.INVALID_REVALIDATION,
                "revalidation-set",
                "revalidation must name surviving invalidated components only",
            )
        )

    changed_geometry = {
        component_id
        for component_id in invalidated_survivors
        if _subtree_objects(component_id, before_components, before_direct)
        != _subtree_objects(component_id, after_components, after_direct)
    }
    redundant = revalidated & changed_geometry
    if redundant:
        issues.append(
            _issue(
                SemanticGeometryLifecycleIssueCode.INVALID_REVALIDATION,
                sorted(redundant)[0],
                "changed geometry cannot also be declared merely revalidated",
            )
        )
    for component_id in sorted(
        invalidated_survivors - changed_geometry - revalidated
    ):
        issues.append(
            _issue(
                SemanticGeometryLifecycleIssueCode.MISSING_GEOMETRY_RESPONSE,
                component_id,
                "invalidated semantic component has neither changed geometry nor explicit revalidation",
            )
        )

    current_object_ids = {
        item.object_id for item in current_program.objects
    }
    for component_id in transition.retired_component_ids:
        retired_objects = _subtree_objects(
            component_id,
            before_components,
            before_direct,
        )
        if not retired_objects or set(retired_objects) & current_object_ids:
            issues.append(
                _issue(
                    SemanticGeometryLifecycleIssueCode.RETIREMENT_GEOMETRY_MISMATCH,
                    component_id,
                    "retired component lacks an exact removed geometry subtree",
                )
            )

    for component_id in transition.preserved_component_ids:
        if before_direct.get(component_id, {}) != after_direct.get(
            component_id,
            {},
        ):
            issues.append(
                _issue(
                    SemanticGeometryLifecycleIssueCode.PRESERVED_GEOMETRY_CHANGED,
                    component_id,
                    "preserved component changed direct geometry ownership or digest",
                )
            )
    ordered = tuple(
        sorted(issues, key=lambda item: (item.code.value, item.subject_id))
    )
    return ordered, tuple(sorted(changed_geometry))


def _owned_objects(
    program: CompiledGeometryProgram,
) -> dict[str, dict[str, str]]:
    object_digests = {
        item.object_id: item.object_digest for item in program.objects
    }
    result: dict[str, dict[str, str]] = {}
    for binding in program.proposal.semantic_bindings:
        owned = result.setdefault(binding.component_id, {})
        for object_id in binding.object_ids:
            owned[object_id] = object_digests[object_id]
    return result


def _subtree_objects(
    component_id: str,
    components: Mapping[str, DesignComponent],
    direct: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    if component_id not in components:
        return {}
    descendants = {component_id}
    changed = True
    while changed:
        changed = False
        for item in components.values():
            if (
                item.parent_component_id in descendants
                and item.component_id not in descendants
            ):
                descendants.add(item.component_id)
                changed = True
    return {
        object_id: digest
        for current_id in sorted(descendants)
        for object_id, digest in direct.get(current_id, {}).items()
    }


def _receipt_base(
    transaction_id: str,
    predecessor_state: DevelopedDesignState,
    current_state: DevelopedDesignState,
    predecessor_proposal: SpatialOptionProposal,
    current_proposal: SpatialOptionProposal,
    prior_program: CompiledGeometryProgram,
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "predecessor_component_digest": predecessor_proposal.proposal_digest,
        "current_component_digest": current_proposal.proposal_digest,
        "predecessor_design_state_digest": predecessor_state.state_digest,
        "current_design_state_digest": current_state.state_digest,
        "predecessor_program_digest": prior_program.program_digest,
    }


def _rejected(
    base: Mapping[str, object],
    *,
    component_transition: ComponentTransitionReceipt | None = None,
    geometry_compilation: GeometryCompilationReceipt | None = None,
    geometry_changed_component_ids: tuple[str, ...] = (),
    revalidated_component_ids: tuple[str, ...] = (),
    issues: tuple[SemanticGeometryLifecycleIssue, ...],
) -> SemanticGeometryLifecycleResult:
    receipt = SemanticGeometryLifecycleReceipt(
        **base,
        status=SemanticGeometryLifecycleStatus.REJECTED,
        current_program_digest=None,
        component_transition=component_transition,
        geometry_compilation=geometry_compilation,
        geometry_changed_component_ids=geometry_changed_component_ids,
        revalidated_component_ids=revalidated_component_ids,
        preserved_component_ids=(
            ()
            if component_transition is None
            else component_transition.preserved_component_ids
        ),
        retired_component_ids=(
            ()
            if component_transition is None
            else component_transition.retired_component_ids
        ),
        issues=issues,
    )
    return SemanticGeometryLifecycleResult(receipt=receipt)


def _issue(
    code: SemanticGeometryLifecycleIssueCode,
    subject_id: str,
    detail: str,
) -> SemanticGeometryLifecycleIssue:
    return SemanticGeometryLifecycleIssue(code, subject_id, detail)


def _sorted_ids(values: object, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    for value in values:
        require_identifier(value, field)
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must contain sorted unique ids")
    return values


