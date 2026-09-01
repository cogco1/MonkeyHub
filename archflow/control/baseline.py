"""Framework minimum physical checks for building stage exits.

Roles are credited only when an exact stage requirement was derived from a
typed validator input and the deterministic validator/bridge receipt is
present in the stage receipt set.  Checker names and caller-authored summary
booleans therefore cannot manufacture baseline coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from archflow.capabilities.stair_solver import (
    StairSolveRequest,
    StairSolveResult,
    StairSolveStatus,
)
from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier
from archflow.control.check_requirements import (
    assembly_stage_requirement,
    cad_readback_stage_requirement,
    component_lineage_stage_requirement,
    material_binding_stage_requirement,
    spatial_layout_stage_requirement,
    vertical_circulation_stage_requirement,
)
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.control.stage_closure import (
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.materials.binding import (
    MaterialBindingProfile,
    MaterialBindingSnapshot,
    validate_material_bindings,
)
from archflow.materials.ledger import MaterialLedger
from archflow.project.refs import BranchRef
from archflow.state.design_maturity import DesignPhase
from archflow.validation.assembly import (
    AssemblyObligationDisposition,
    AssemblyProfile,
    RelationshipKind,
    check_assembly,
)
from archflow.validation.cad_readback import (
    CadReadbackProfile,
    CadReadbackSnapshot,
    validate_cad_readback,
)
from archflow.validation.check_bridges import (
    ComponentLineageCheckProfile,
    SpatialLayoutCheckProfile,
    bridge_component_lineage_receipt,
    bridge_spatial_validation_receipt,
)
from archflow.validation.component_lineage import StageComponentCoverageReceipt
from archflow.validation.contracts import (
    CheckFinding,
    CheckReceiptEnvelope,
    CheckStatus,
    FindingSeverity,
)
from archflow.validation.spatial import (
    SpatialValidationInput,
    validate_spatial_layout,
)
from archflow.validation.vertical_circulation import (
    VerticalCirculationContract,
    VerticalCirculationMaturity,
    check_vertical_circulation_maturity,
)

if TYPE_CHECKING:
    from archflow.control.stage_control_sources import (
        ComponentFunctionBaselineSource,
        VisualInventoryBaselineSource,
    )
    from archflow.control.stage_subjects import StageSubjectInventory
    from archflow.control.relation_promotion import RelationPromotionResult
    from archflow.relations.authoring import (
        RelationAuthoringCompilation,
        RelationAuthoringContext,
    )
    from archflow.relations.contracts import ArchitecturalRelationGraph
    from archflow.relations.coverage import RelationNotApplicable
    from archflow.relations.realization import RelationRealizationManifest
    from archflow.runtime.geometry_compiler import CompiledGeometryProgram


class StageBaselineError(ValueError):
    """A stage profile or source set cannot prove minimum physical checks."""


class StageBaselineLevel(StrEnum):
    PRE_GEOMETRY = "pre_geometry"
    SPATIAL = "spatial"
    DEVELOPED = "developed"
    COORDINATED = "coordinated"


class StageBaselineRole(StrEnum):
    COMPONENT_LINEAGE = "component_lineage"
    SPATIAL_ENVELOPE = "spatial_envelope"
    ASSEMBLY_RELATIONSHIPS = "assembly_relationships"
    OPENING_CLEARANCE = "opening_clearance"
    LOAD_PATH = "load_path"
    MATERIAL_BINDING = "material_binding"
    CAD_READBACK = "cad_readback"
    VERTICAL_CIRCULATION = "vertical_circulation"


class StageBaselineStatus(StrEnum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"


BASELINE_LEVEL_ROLES: dict[
    StageBaselineLevel,
    frozenset[StageBaselineRole],
] = {
    StageBaselineLevel.PRE_GEOMETRY: frozenset(),
    StageBaselineLevel.SPATIAL: frozenset(
        {
            StageBaselineRole.COMPONENT_LINEAGE,
            StageBaselineRole.SPATIAL_ENVELOPE,
            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
            StageBaselineRole.OPENING_CLEARANCE,
            StageBaselineRole.LOAD_PATH,
        }
    ),
    StageBaselineLevel.DEVELOPED: frozenset(
        {
            StageBaselineRole.COMPONENT_LINEAGE,
            StageBaselineRole.SPATIAL_ENVELOPE,
            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
            StageBaselineRole.OPENING_CLEARANCE,
            StageBaselineRole.LOAD_PATH,
            StageBaselineRole.MATERIAL_BINDING,
        }
    ),
    StageBaselineLevel.COORDINATED: frozenset(
        role
        for role in StageBaselineRole
        if role is not StageBaselineRole.VERTICAL_CIRCULATION
    ),
}

_RELATION_SOURCE_KINDS_BY_LEVEL: dict[
    StageBaselineLevel,
    frozenset[str],
] = {
    StageBaselineLevel.PRE_GEOMETRY: frozenset(),
    StageBaselineLevel.SPATIAL: frozenset({"relation_topology"}),
    StageBaselineLevel.DEVELOPED: frozenset(
        {"relation_topology", "relation_realization"}
    ),
    StageBaselineLevel.COORDINATED: frozenset(
        {"relation_topology", "relation_realization", "relation_inheritance"}
    ),
}


def _required_source_kinds_for_role(
    level: StageBaselineLevel,
    role: StageBaselineRole,
    *,
    include_control: bool = True,
) -> frozenset[str]:
    if role is StageBaselineRole.COMPONENT_LINEAGE:
        return frozenset({"component_lineage", "visual_inventory"}) if include_control else frozenset()
    if role is StageBaselineRole.ASSEMBLY_RELATIONSHIPS:
        return frozenset(
            {
                *({"component_functions"} if include_control else set()),
                *_RELATION_SOURCE_KINDS_BY_LEVEL[level],
            }
        )
    return frozenset()


_VERTICAL_CIRCULATION_MATURITY_BY_LEVEL: dict[
    StageBaselineLevel,
    VerticalCirculationMaturity | None,
] = {
    StageBaselineLevel.PRE_GEOMETRY: None,
    StageBaselineLevel.SPATIAL: VerticalCirculationMaturity.RESERVATION,
    # A developed stair is a physical assembly, not only a walkable path.
    # Support, load-path, host-cut, and applicable underpass evidence therefore
    # become mandatory at the spatial -> developed boundary.
    StageBaselineLevel.DEVELOPED: VerticalCirculationMaturity.ASSEMBLY,
    StageBaselineLevel.COORDINATED: VerticalCirculationMaturity.ASSEMBLY,
}

DESIGN_PHASE_BASELINE_LEVEL: dict[DesignPhase, StageBaselineLevel] = {
    DesignPhase.RESEARCH_BRIEF: StageBaselineLevel.PRE_GEOMETRY,
    DesignPhase.PROGRAMMING: StageBaselineLevel.PRE_GEOMETRY,
    DesignPhase.SITE_RESOURCE_COORDINATION: StageBaselineLevel.PRE_GEOMETRY,
    DesignPhase.SCHEMATIC_DESIGN: StageBaselineLevel.SPATIAL,
    DesignPhase.DESIGN_DEVELOPMENT: StageBaselineLevel.DEVELOPED,
    DesignPhase.CANDIDATE_COORDINATION: StageBaselineLevel.COORDINATED,
    DesignPhase.EXECUTION_READY: StageBaselineLevel.COORDINATED,
}


@dataclass(frozen=True, slots=True)
class ComponentLineageBaselineSource:
    profile: ComponentLineageCheckProfile
    source_receipt: StageComponentCoverageReceipt

    SCHEMA = "ComponentLineageBaselineSource@1"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ComponentLineageCheckProfile):
            raise TypeError("profile must be ComponentLineageCheckProfile")
        if not isinstance(self.source_receipt, StageComponentCoverageReceipt):
            raise TypeError(
                "source_receipt must be StageComponentCoverageReceipt"
            )

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile": self.profile.to_dict(),
            "source_receipt": self.source_receipt.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "ComponentLineageBaselineSource":
        payload = exact_mapping(
            value,
            {"schema", "profile", "source_receipt", "source_digest"},
            "component lineage baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported component lineage baseline source schema"
            )
        result = cls(
            profile=ComponentLineageCheckProfile.from_dict(
                payload["profile"]
            ),
            source_receipt=StageComponentCoverageReceipt.from_dict(
                payload["source_receipt"]
            ),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "component lineage baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class SpatialLayoutBaselineSource:
    profile: SpatialLayoutCheckProfile
    validator_input: SpatialValidationInput

    SCHEMA = "SpatialLayoutBaselineSource@2"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, SpatialLayoutCheckProfile):
            raise TypeError("profile must be SpatialLayoutCheckProfile")
        if not isinstance(self.validator_input, SpatialValidationInput):
            raise TypeError("validator_input must be SpatialValidationInput")
        if self.profile.input_digest != self.validator_input.input_digest:
            raise StageBaselineError(
                "spatial profile input digest does not match validator input"
            )

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile": self.profile.to_dict(),
            "validator_input": self.validator_input.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "SpatialLayoutBaselineSource":
        payload = exact_mapping(
            value,
            {"schema", "profile", "validator_input", "source_digest"},
            "spatial layout baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported spatial layout baseline source schema"
            )
        result = cls(
            profile=SpatialLayoutCheckProfile.from_dict(payload["profile"]),
            validator_input=SpatialValidationInput.from_dict(
                payload["validator_input"]
            ),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "spatial layout baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class MaterialBindingBaselineSource:
    profile: MaterialBindingProfile
    ledger: MaterialLedger
    snapshot: MaterialBindingSnapshot

    SCHEMA = "MaterialBindingBaselineSource@1"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, MaterialBindingProfile):
            raise TypeError("profile must be MaterialBindingProfile")
        if not isinstance(self.ledger, MaterialLedger):
            raise TypeError("ledger must be MaterialLedger")
        if not isinstance(self.snapshot, MaterialBindingSnapshot):
            raise TypeError("snapshot must be MaterialBindingSnapshot")

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile": self.profile.to_dict(),
            "ledger": self.ledger.to_dict(),
            "snapshot": self.snapshot.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "MaterialBindingBaselineSource":
        payload = exact_mapping(
            value,
            {"schema", "profile", "ledger", "snapshot", "source_digest"},
            "material binding baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported material binding baseline source schema"
            )
        result = cls(
            profile=MaterialBindingProfile.from_dict(payload["profile"]),
            ledger=MaterialLedger.from_dict(payload["ledger"]),
            snapshot=MaterialBindingSnapshot.from_dict(payload["snapshot"]),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "material binding baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class CadReadbackBaselineSource:
    profile: CadReadbackProfile
    snapshot: CadReadbackSnapshot

    SCHEMA = "CadReadbackBaselineSource@1"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, CadReadbackProfile):
            raise TypeError("profile must be CadReadbackProfile")
        if not isinstance(self.snapshot, CadReadbackSnapshot):
            raise TypeError("snapshot must be CadReadbackSnapshot")

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "profile": self.profile.to_dict(),
            "snapshot": self.snapshot.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "CadReadbackBaselineSource":
        payload = exact_mapping(
            value,
            {"schema", "profile", "snapshot", "source_digest"},
            "CAD readback baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported CAD readback baseline source schema"
            )
        result = cls(
            profile=CadReadbackProfile.from_dict(payload["profile"]),
            snapshot=CadReadbackSnapshot.from_dict(payload["snapshot"]),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "CAD readback baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class VerticalCirculationBaselineSource:
    """Exact solver and readback lineage for one circulation contract.

    The contract's SHA fields are claims until they are tied to retained typed
    inputs.  The baseline therefore retains the solve request/result here and,
    for any maturity that claims a realized walking path, names the exact CAD
    readback source whose program and snapshot are being credited.
    """

    contract: VerticalCirculationContract
    solve_request: StairSolveRequest
    solve_result: StairSolveResult
    readback_source_digest: str | None = None

    SCHEMA = "VerticalCirculationBaselineSource@1"

    def __post_init__(self) -> None:
        if not isinstance(self.contract, VerticalCirculationContract):
            raise TypeError("contract must be VerticalCirculationContract")
        if not isinstance(self.solve_request, StairSolveRequest):
            raise TypeError("solve_request must be StairSolveRequest")
        if not isinstance(self.solve_result, StairSolveResult):
            raise TypeError("solve_result must be StairSolveResult")
        if self.solve_result.request_digest != self.solve_request.digest:
            raise StageBaselineError(
                "vertical-circulation solver result crossed its exact request"
            )
        expected_unit_ref = f"unit:{self.solve_request.length_unit.value}"
        if self.contract.length_unit_ref != expected_unit_ref:
            raise StageBaselineError(
                "vertical-circulation contract crossed its exact solver unit"
            )
        result_digest = canonical_digest(self.solve_result.to_dict())
        if self.contract.solver_result_digest != result_digest:
            raise StageBaselineError(
                "vertical-circulation contract crossed its exact solver result"
            )
        if (
            self.contract.maturity
            is not VerticalCirculationMaturity.RESERVATION
            and (
                self.solve_result.status is not StairSolveStatus.SOLVED
                or self.solve_result.assembly is None
            )
        ):
            raise StageBaselineError(
                "resolved vertical circulation requires a solved stair result"
            )
        if (
            self.contract.maturity
            is not VerticalCirculationMaturity.RESERVATION
            and self.contract.solver_tread_refs != self.solve_result.tread_refs
        ):
            raise StageBaselineError(
                "vertical-circulation solver tread denominator changed"
            )
        if (
            self.contract.maturity
            is not VerticalCirculationMaturity.RESERVATION
            and self.contract.solver_landing_refs
            != self.solve_result.landing_refs
        ):
            raise StageBaselineError(
                "vertical-circulation solver landing denominator changed"
            )
        for contract_interface, request_interface, role in (
            (
                self.contract.lower_interface,
                self.solve_request.lower_interface,
                "lower",
            ),
            (
                self.contract.upper_interface,
                self.solve_request.upper_interface,
                "upper",
            ),
        ):
            if (
                contract_interface.role.value != request_interface.role.value
                or contract_interface.relation_ref
                != request_interface.relation_ref
                or contract_interface.interface_ref
                != request_interface.interface_ref
                or contract_interface.level_ref != request_interface.level_ref
                or contract_interface.datum_fact_ref
                != request_interface.datum_fact_ref
                or contract_interface.datum != request_interface.datum
            ):
                raise StageBaselineError(
                    f"vertical-circulation contract crossed its exact {role} "
                    "solver interface"
                )
        if (
            self.contract.lower_landing_ownership.value
            != self.solve_request.lower_landing_ownership.value
            or self.contract.upper_landing_ownership.value
            != self.solve_request.upper_landing_ownership.value
        ):
            raise StageBaselineError(
                "vertical-circulation terminal landing ownership changed"
            )
        if self.readback_source_digest is not None:
            object.__setattr__(
                self,
                "readback_source_digest",
                require_sha256(
                    self.readback_source_digest,
                    "readback_source_digest",
                ),
            )

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "contract": self.contract.to_dict(),
            "solve_request": self.solve_request.to_dict(),
            "solve_result": self.solve_result.to_dict(),
            "readback_source_digest": self.readback_source_digest,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "VerticalCirculationBaselineSource":
        payload = exact_mapping(
            value,
            {
                "schema",
                "contract",
                "solve_request",
                "solve_result",
                "readback_source_digest",
                "source_digest",
            },
            "vertical-circulation baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported vertical-circulation baseline source schema"
            )
        result = cls(
            contract=VerticalCirculationContract.from_dict(payload["contract"]),
            solve_request=StairSolveRequest.from_dict(
                payload["solve_request"]
            ),
            solve_result=StairSolveResult.from_dict(payload["solve_result"]),
            readback_source_digest=payload["readback_source_digest"],
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "vertical-circulation baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationTopologyBaselineSource:
    """Exact Stage 2 relation-authoring result and verified promotion.

    The source carries the controller-authored question denominator, the
    proposal compilation, and the independently verified promoted graph.  It
    does not author a relationship and has no stage-acceptance authority.
    """

    context: "RelationAuthoringContext"
    compilation: "RelationAuthoringCompilation"
    promotion: "RelationPromotionResult"
    not_applicable: tuple["RelationNotApplicable", ...] = ()

    SCHEMA = "RelationTopologyBaselineSource@1"

    def __post_init__(self) -> None:
        from archflow.control.relation_promotion import RelationPromotionResult
        from archflow.relations.authoring import (
            RelationAuthoringCompilation,
            RelationAuthoringCompilationStatus,
            RelationAuthoringContext,
        )
        from archflow.relations.coverage import RelationNotApplicable

        if not isinstance(self.context, RelationAuthoringContext):
            raise TypeError("context must be RelationAuthoringContext")
        if not isinstance(self.compilation, RelationAuthoringCompilation):
            raise TypeError("compilation must be RelationAuthoringCompilation")
        if not isinstance(self.promotion, RelationPromotionResult):
            raise TypeError("promotion must be RelationPromotionResult")
        if (
            self.compilation.receipt.status
            is not RelationAuthoringCompilationStatus.PROPOSAL_COMPILED
            or self.compilation.graph is None
            or self.compilation.policy is None
            or self.compilation.receipt.context_digest
            != self.context.context_digest
            or self.promotion.proposal_graph != self.compilation.graph
            or self.promotion.receipt.context_digest
            != self.context.context_digest
            or self.promotion.receipt.proposal_digest
            != self.compilation.receipt.proposal_digest
        ):
            raise StageBaselineError(
                "relation topology source crossed its exact authoring result"
            )
        if not isinstance(self.not_applicable, tuple) or any(
            not isinstance(item, RelationNotApplicable)
            for item in self.not_applicable
        ):
            raise TypeError(
                "not_applicable must contain RelationNotApplicable values"
            )
        ordered = tuple(
            sorted(self.not_applicable, key=lambda item: item.slot_ref)
        )
        refs = tuple(item.slot_ref for item in ordered)
        if len(refs) != len(set(refs)):
            raise StageBaselineError(
                "relation topology source repeats a not-applicable slot"
            )
        object.__setattr__(self, "not_applicable", ordered)

    @property
    def branch(self) -> BranchRef:
        return self.promotion.graph.branch

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "context": self.context.to_dict(),
            "compilation": self.compilation.to_dict(),
            "promotion": self.promotion.to_dict(),
            "not_applicable": [
                item.to_dict() for item in self.not_applicable
            ],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "RelationTopologyBaselineSource":
        from archflow.control.relation_promotion import RelationPromotionResult
        from archflow.relations.authoring import (
            RelationAuthoringCompilation,
            RelationAuthoringContext,
        )
        from archflow.relations.coverage import RelationNotApplicable

        payload = exact_mapping(
            value,
            {
                "schema",
                "context",
                "compilation",
                "promotion",
                "not_applicable",
                "source_digest",
            },
            "relation topology baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported relation topology baseline source schema"
            )
        if not isinstance(payload["not_applicable"], list):
            raise TypeError("not_applicable must be a list")
        result = cls(
            context=RelationAuthoringContext.from_dict(payload["context"]),
            compilation=RelationAuthoringCompilation.from_dict(
                payload["compilation"]
            ),
            promotion=RelationPromotionResult.from_dict(payload["promotion"]),
            not_applicable=tuple(
                RelationNotApplicable.from_dict(item)
                for item in payload["not_applicable"]
            ),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "relation topology baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class RelationRealizationBaselineSource:
    """Exact Stage 3 graph/program/readback realization validator inputs."""

    graph: "ArchitecturalRelationGraph"
    manifest: "RelationRealizationManifest"
    program: "CompiledGeometryProgram"
    readback: CadReadbackSnapshot
    verification_receipts: tuple[CheckReceiptEnvelope, ...] = ()

    SCHEMA = "RelationRealizationBaselineSource@1"

    def __post_init__(self) -> None:
        from archflow.relations.contracts import ArchitecturalRelationGraph
        from archflow.relations.realization import RelationRealizationManifest
        from archflow.runtime.geometry_compiler import CompiledGeometryProgram

        if not isinstance(self.graph, ArchitecturalRelationGraph):
            raise TypeError("graph must be ArchitecturalRelationGraph")
        if not isinstance(self.manifest, RelationRealizationManifest):
            raise TypeError("manifest must be RelationRealizationManifest")
        if not isinstance(self.program, CompiledGeometryProgram):
            raise TypeError("program must be CompiledGeometryProgram")
        if not isinstance(self.readback, CadReadbackSnapshot):
            raise TypeError("readback must be CadReadbackSnapshot")
        if not isinstance(self.verification_receipts, tuple) or any(
            not isinstance(item, CheckReceiptEnvelope)
            for item in self.verification_receipts
        ):
            raise TypeError(
                "verification_receipts must contain CheckReceiptEnvelope"
            )
        receipts = tuple(
            sorted(
                self.verification_receipts,
                key=lambda item: item.receipt_digest,
            )
        )
        digests = tuple(item.receipt_digest for item in receipts)
        if len(digests) != len(set(digests)):
            raise StageBaselineError(
                "relation realization source repeats a verification receipt"
            )
        object.__setattr__(self, "verification_receipts", receipts)

    @property
    def branch(self) -> BranchRef:
        return self.manifest.branch

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "graph": self.graph.to_dict(),
            "manifest": self.manifest.to_dict(),
            "program": self.program.to_dict(),
            "readback": self.readback.to_dict(),
            "verification_receipts": [
                item.to_dict() for item in self.verification_receipts
            ],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(cls, value: object) -> "RelationRealizationBaselineSource":
        from archflow.capabilities.geometry_proposal import (
            load_compiled_geometry_program,
        )
        from archflow.relations.contracts import ArchitecturalRelationGraph
        from archflow.relations.realization import RelationRealizationManifest

        payload = exact_mapping(
            value,
            {
                "schema",
                "graph",
                "manifest",
                "program",
                "readback",
                "verification_receipts",
                "source_digest",
            },
            "relation realization baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported relation realization baseline source schema"
            )
        if not isinstance(payload["verification_receipts"], list):
            raise TypeError("verification_receipts must be a list")
        result = cls(
            graph=ArchitecturalRelationGraph.from_dict(payload["graph"]),
            manifest=RelationRealizationManifest.from_dict(payload["manifest"]),
            program=load_compiled_geometry_program(payload["program"]),
            readback=CadReadbackSnapshot.from_dict(payload["readback"]),
            verification_receipts=tuple(
                CheckReceiptEnvelope.from_dict(item)
                for item in payload["verification_receipts"]
            ),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "relation realization baseline source digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class StageRelationInheritanceBaselineSource:
    """Exact predecessor graph plus the recomputable current realization."""

    predecessor: "AcceptedStageRelationPredecessor"
    current_realization: RelationRealizationBaselineSource

    SCHEMA = "StageRelationInheritanceBaselineSource@1"

    def __post_init__(self) -> None:
        from archflow.control.stage_relation_inheritance import (
            AcceptedStageRelationPredecessor,
        )

        if not isinstance(self.predecessor, AcceptedStageRelationPredecessor):
            raise TypeError(
                "predecessor must be AcceptedStageRelationPredecessor"
            )
        if not isinstance(
            self.current_realization,
            RelationRealizationBaselineSource,
        ):
            raise TypeError(
                "current_realization must be RelationRealizationBaselineSource"
            )

    @property
    def branch(self) -> BranchRef:
        return self.current_realization.branch

    @property
    def predecessor_graph(self) -> "ArchitecturalRelationGraph":
        return self.predecessor.graph

    @property
    def source_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "predecessor": self.predecessor.to_dict(),
            "current_realization": self.current_realization.to_dict(),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "source_digest": self.source_digest}

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> "StageRelationInheritanceBaselineSource":
        from archflow.control.stage_relation_inheritance import (
            AcceptedStageRelationPredecessor,
        )

        payload = exact_mapping(
            value,
            {
                "schema",
                "predecessor",
                "current_realization",
                "source_digest",
            },
            "stage relation inheritance baseline source",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported stage relation inheritance baseline source schema"
            )
        result = cls(
            predecessor=AcceptedStageRelationPredecessor.from_dict(
                payload["predecessor"]
            ),
            current_realization=RelationRealizationBaselineSource.from_dict(
                payload["current_realization"]
            ),
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "stage relation inheritance baseline source digest changed"
            )
        return result


def _typed_tuple(values: object, expected_type: type, field: str) -> tuple:
    if not isinstance(values, tuple):
        raise TypeError(f"{field} must be a tuple")
    if any(not isinstance(item, expected_type) for item in values):
        raise TypeError(f"{field} must contain {expected_type.__name__}")
    digests = tuple(
        item.profile_digest
        if isinstance(item, AssemblyProfile)
        else item.source_digest
        for item in values
    )
    if len(digests) != len(set(digests)):
        raise StageBaselineError(f"{field} contains duplicate sources")
    return tuple(
        item
        for _digest, item in sorted(
            zip(digests, values, strict=True),
            key=lambda pair: pair[0],
        )
    )


@dataclass(frozen=True, slots=True)
class StageBaselineSourceSet:
    """Exact validator inputs from which baseline receipts are recomputed."""

    component_lineage: tuple[ComponentLineageBaselineSource, ...] = ()
    spatial_layout: tuple[SpatialLayoutBaselineSource, ...] = ()
    assembly: tuple[AssemblyProfile, ...] = ()
    material_binding: tuple[MaterialBindingBaselineSource, ...] = ()
    cad_readback: tuple[CadReadbackBaselineSource, ...] = ()
    visual_inventory: tuple["VisualInventoryBaselineSource", ...] = ()
    component_functions: tuple["ComponentFunctionBaselineSource", ...] = ()
    vertical_circulation: tuple[VerticalCirculationBaselineSource, ...] = ()
    relation_topology: tuple[RelationTopologyBaselineSource, ...] = ()
    relation_realization: tuple[RelationRealizationBaselineSource, ...] = ()
    relation_inheritance: tuple[
        StageRelationInheritanceBaselineSource,
        ...,
    ] = ()
    _legacy_read_only: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )
    _replay_schema: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    SCHEMA = "StageBaselineSourceSet@4"
    PREVIOUS_SCHEMA = "StageBaselineSourceSet@3"
    SECONDARY_LEGACY_SCHEMA = "StageBaselineSourceSet@2"
    LEGACY_SCHEMA = "StageBaselineSourceSet@1"

    def __post_init__(self) -> None:
        from archflow.control.stage_control_sources import (
            ComponentFunctionBaselineSource,
            VisualInventoryBaselineSource,
        )

        for field, expected in (
            ("component_lineage", ComponentLineageBaselineSource),
            ("spatial_layout", SpatialLayoutBaselineSource),
            ("assembly", AssemblyProfile),
            ("material_binding", MaterialBindingBaselineSource),
            ("cad_readback", CadReadbackBaselineSource),
            ("visual_inventory", VisualInventoryBaselineSource),
            ("component_functions", ComponentFunctionBaselineSource),
            ("vertical_circulation", VerticalCirculationBaselineSource),
            ("relation_topology", RelationTopologyBaselineSource),
            ("relation_realization", RelationRealizationBaselineSource),
            (
                "relation_inheritance",
                StageRelationInheritanceBaselineSource,
            ),
        ):
            object.__setattr__(
                self,
                field,
                _typed_tuple(getattr(self, field), expected, field),
            )
        inheritance_sources = self.relation_inheritance
        if inheritance_sources:
            acceptance_set_digests = {
                source.predecessor.acceptance_set_digest
                for source in inheritance_sources
            }
            if len(acceptance_set_digests) != 1:
                raise StageBaselineError(
                    "relation inheritance sources crossed the accepted "
                    "predecessor topology set"
                )
            accepted_pairs = {
                (
                    item.topology_source_digest,
                    item.graph_digest,
                )
                for item in (
                    inheritance_sources[0].predecessor.accepted_topologies
                )
            }
            selected_pairs = tuple(
                (
                    source.predecessor.topology_source_digest,
                    source.predecessor.graph.graph_digest,
                )
                for source in inheritance_sources
            )
            if (
                len(selected_pairs) != len(set(selected_pairs))
                or set(selected_pairs) != accepted_pairs
            ):
                raise StageBaselineError(
                    "relation inheritance must cover the full accepted "
                    "predecessor topology set exactly once"
                )

    @property
    def is_legacy_read_only(self) -> bool:
        return self._legacy_read_only

    def _content_dict(self) -> dict[str, object]:
        schema = self._replay_schema or self.SCHEMA
        payload: dict[str, object] = {
            "schema": schema,
            "component_lineage": [
                item.to_dict() for item in self.component_lineage
            ],
            "spatial_layout": [
                item.to_dict() for item in self.spatial_layout
            ],
            "assembly": [item.to_dict() for item in self.assembly],
            "material_binding": [
                item.to_dict() for item in self.material_binding
            ],
            "cad_readback": [item.to_dict() for item in self.cad_readback],
        }
        if schema in {self.SCHEMA, self.PREVIOUS_SCHEMA, self.SECONDARY_LEGACY_SCHEMA}:
            payload["relation_topology"] = [
                item.to_dict() for item in self.relation_topology
            ]
            payload["relation_realization"] = [
                item.to_dict() for item in self.relation_realization
            ]
            payload["relation_inheritance"] = [
                item.to_dict() for item in self.relation_inheritance
            ]
        if schema in {self.SCHEMA, self.PREVIOUS_SCHEMA} and self.vertical_circulation:
            payload["vertical_circulation"] = [
                item.to_dict() for item in self.vertical_circulation
            ]
        if schema == self.SCHEMA:
            payload["visual_inventory"] = [
                item.to_dict() for item in self.visual_inventory
            ]
            payload["component_functions"] = [
                item.to_dict() for item in self.component_functions
            ]
        return payload

    @property
    def source_set_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "source_set_digest": self.source_set_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageBaselineSourceSet":
        if not isinstance(value, dict):
            raise TypeError("stage baseline source set must be a mapping")
        schema = value.get("schema")
        common = {
            "schema",
            "component_lineage",
            "spatial_layout",
            "assembly",
            "material_binding",
            "cad_readback",
            "source_set_digest",
        }
        from archflow.control.stage_control_sources import (
            ComponentFunctionBaselineSource,
            VisualInventoryBaselineSource,
        )
        if schema == cls.SCHEMA:
            expected = common | {
                "visual_inventory",
                "component_functions",
                "relation_topology",
                "relation_realization",
                "relation_inheritance",
            }
            if "vertical_circulation" in value:
                expected.add("vertical_circulation")
            legacy = False
            replay_schema = None
        elif schema == cls.PREVIOUS_SCHEMA:
            expected = common | {
                "relation_topology",
                "relation_realization",
                "relation_inheritance",
            }
            legacy = True
            replay_schema = cls.PREVIOUS_SCHEMA
        elif schema == cls.SECONDARY_LEGACY_SCHEMA:
            expected = common | {
                "relation_topology",
                "relation_realization",
                "relation_inheritance",
            }
            legacy = True
            replay_schema = cls.SECONDARY_LEGACY_SCHEMA
        elif schema == cls.LEGACY_SCHEMA:
            expected = common
            legacy = True
            replay_schema = cls.LEGACY_SCHEMA
        else:
            raise StageBaselineError(
                "unsupported stage baseline source set schema"
            )
        payload = exact_mapping(
            value,
            expected,
            "stage baseline source set",
        )
        for field in (
            "component_lineage",
            "spatial_layout",
            "assembly",
            "material_binding",
            "cad_readback",
            *(
                ("visual_inventory", "component_functions")
                if schema == cls.SCHEMA
                else ()
            ),
            *(
                ("vertical_circulation",)
                if schema in {cls.SCHEMA, cls.PREVIOUS_SCHEMA}
                and "vertical_circulation" in payload
                else ()
            ),
            *(
                (
                    "relation_topology",
                    "relation_realization",
                    "relation_inheritance",
                )
                if schema != cls.LEGACY_SCHEMA
                else ()
            ),
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            component_lineage=tuple(
                ComponentLineageBaselineSource.from_dict(item)
                for item in payload["component_lineage"]
            ),
            spatial_layout=tuple(
                SpatialLayoutBaselineSource.from_dict(item)
                for item in payload["spatial_layout"]
            ),
            assembly=tuple(
                AssemblyProfile.from_dict(item)
                for item in payload["assembly"]
            ),
            material_binding=tuple(
                MaterialBindingBaselineSource.from_dict(item)
                for item in payload["material_binding"]
            ),
            cad_readback=tuple(
                CadReadbackBaselineSource.from_dict(item)
                for item in payload["cad_readback"]
            ),
            visual_inventory=(
                tuple(
                    VisualInventoryBaselineSource.from_dict(item)
                    for item in payload["visual_inventory"]
                )
                if schema == cls.SCHEMA
                else ()
            ),
            component_functions=(
                tuple(
                    ComponentFunctionBaselineSource.from_dict(item)
                    for item in payload["component_functions"]
                )
                if schema == cls.SCHEMA
                else ()
            ),
            vertical_circulation=(
                tuple(
                    VerticalCirculationBaselineSource.from_dict(item)
                    for item in payload["vertical_circulation"]
                )
                if schema in {cls.SCHEMA, cls.PREVIOUS_SCHEMA}
                and "vertical_circulation" in payload
                else ()
            ),
            relation_topology=(
                tuple(
                    RelationTopologyBaselineSource.from_dict(item)
                    for item in payload["relation_topology"]
                )
                if schema != cls.LEGACY_SCHEMA
                else ()
            ),
            relation_realization=(
                tuple(
                    RelationRealizationBaselineSource.from_dict(item)
                    for item in payload["relation_realization"]
                )
                if schema != cls.LEGACY_SCHEMA
                else ()
            ),
            relation_inheritance=(
                tuple(
                    StageRelationInheritanceBaselineSource.from_dict(item)
                    for item in payload["relation_inheritance"]
                )
                if schema != cls.LEGACY_SCHEMA
                else ()
            ),
        )
        if legacy:
            object.__setattr__(result, "_legacy_read_only", True)
            object.__setattr__(result, "_replay_schema", replay_schema)
        if result.to_dict() != payload:
            raise StageBaselineError(
                "stage baseline source set digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class _ExpectedBaselineCheck:
    requirement: StageCheckRequirement
    receipt: CheckReceiptEnvelope
    source_kind: str
    roles: tuple[StageBaselineRole, ...]
    role_targets: tuple[
        tuple[StageBaselineRole, tuple[str, ...]],
        ...,
    ]
    source_digest: str


def _assembly_roles(profile: AssemblyProfile) -> tuple[StageBaselineRole, ...]:
    kinds = {
        item.relationship_kind
        for item in profile.coverage_manifest.obligations
        if item.disposition is AssemblyObligationDisposition.REQUIRED
    }
    roles = set()
    if kinds:
        roles.add(StageBaselineRole.ASSEMBLY_RELATIONSHIPS)
    if RelationshipKind.OPENING_CLEAR in kinds:
        roles.add(StageBaselineRole.OPENING_CLEARANCE)
    if RelationshipKind.LOAD_PATH_TO_FOUNDATION in kinds and kinds.intersection(
        {RelationshipKind.SUPPORT, RelationshipKind.VERTICAL_SUPPORT_CHAIN}
    ):
        roles.add(StageBaselineRole.LOAD_PATH)
    return tuple(sorted(roles))


def _assembly_role_targets(
    profile: AssemblyProfile,
) -> tuple[tuple[StageBaselineRole, tuple[str, ...]], ...]:
    opening_targets = {
        ref
        for requirement in profile.requirements
        if requirement.kind is RelationshipKind.OPENING_CLEAR
        for ref in requirement.subject_refs
    }
    load_targets = {
        ref
        for requirement in profile.requirements
        if requirement.kind
        in {
            RelationshipKind.SUPPORT,
            RelationshipKind.VERTICAL_SUPPORT_CHAIN,
            RelationshipKind.LOAD_PATH_TO_FOUNDATION,
        }
        for ref in requirement.subject_refs
    }
    return (
        (
            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
            tuple(sorted(profile.coverage_manifest.stage_subject_refs)),
        ),
        (
            StageBaselineRole.OPENING_CLEARANCE,
            tuple(sorted(opening_targets)),
        ),
        (
            StageBaselineRole.LOAD_PATH,
            tuple(sorted(load_targets)),
        ),
    )


def _relation_roles_and_targets(
    relations: tuple[object, ...],
) -> tuple[
    tuple[StageBaselineRole, ...],
    tuple[tuple[StageBaselineRole, tuple[str, ...]], ...],
]:
    from archflow.relations.contracts import (
        ArchitecturalRelation,
        ArchitecturalRelationKind,
    )

    if not isinstance(relations, tuple) or any(
        not isinstance(item, ArchitecturalRelation) for item in relations
    ):
        raise TypeError("relations must contain ArchitecturalRelation values")
    roles = {StageBaselineRole.ASSEMBLY_RELATIONSHIPS}
    all_targets = {
        participant.node_ref
        for relation in relations
        for participant in relation.participants
    }
    opening_kinds = {
        ArchitecturalRelationKind.ACCESS,
        ArchitecturalRelationKind.ALLOWS_PASSAGE,
        ArchitecturalRelationKind.CLEARANCE,
        ArchitecturalRelationKind.HOSTS_VOID,
        ArchitecturalRelationKind.FILLS_VOID,
    }
    load_kinds = {
        ArchitecturalRelationKind.SUPPORT,
        ArchitecturalRelationKind.LOAD_TRANSFER,
    }
    opening_targets = {
        participant.node_ref
        for relation in relations
        if relation.kind in opening_kinds
        for participant in relation.participants
    }
    load_targets = {
        participant.node_ref
        for relation in relations
        if relation.kind in load_kinds
        for participant in relation.participants
    }
    if opening_targets:
        roles.add(StageBaselineRole.OPENING_CLEARANCE)
    if load_targets:
        roles.add(StageBaselineRole.LOAD_PATH)
    return (
        tuple(sorted(roles)),
        (
            (
                StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
                tuple(sorted(all_targets)),
            ),
            (
                StageBaselineRole.OPENING_CLEARANCE,
                tuple(sorted(opening_targets)),
            ),
            (
                StageBaselineRole.LOAD_PATH,
                tuple(sorted(load_targets)),
            ),
        ),
    )


def _inventory_identity_refs(
    subject_inventory: "StageSubjectInventory",
    component_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve legacy validator component ids to current semantic identities.

    The component-lineage and spatial validators predate
    ``DesignComponent.identity_ref`` and therefore expose plain component ids.
    Stage obligations, however, are authored against the retained semantic
    identity (normally ``design-component:*``).  Crediting ``component:*`` here
    would make a valid current inventory impossible to cover, while blindly
    rewriting the prefix would guess identity.  The exact inventory is the
    only authority for this join.
    """

    by_id = {item.component_id: item.identity_ref for item in subject_inventory.entries}
    declared_targets = {
        target_ref
        for item in subject_inventory.entries
        for obligation in item.role_obligations
        for target_ref in obligation.target_refs
    }
    resolved: dict[str, str] = {}
    for component_id in component_ids:
        identity_ref = by_id.get(component_id)
        if identity_ref is not None:
            resolved[component_id] = identity_ref
            continue
        legacy_ref = f"component:{component_id}"
        if legacy_ref in declared_targets:
            resolved[component_id] = legacy_ref
    for component_id in sorted(set(component_ids) - set(resolved)):
        # Preserve the unresolved join in the exact denominator instead of
        # guessing a semantic identity or aborting coverage compilation.  No
        # stage-inventory obligation can be covered by this sentinel, so the
        # applicable role remains OPEN and diagnostics retain the validator id.
        resolved[component_id] = f"unresolved-component-id:{component_id}"
    return tuple(sorted(resolved[item] for item in component_ids))


def required_vertical_circulation_maturity(
    level: StageBaselineLevel,
) -> VerticalCirculationMaturity | None:
    """Return the mechanically required circulation maturity for a level."""

    if not isinstance(level, StageBaselineLevel):
        raise TypeError("level must be StageBaselineLevel")
    return _VERTICAL_CIRCULATION_MATURITY_BY_LEVEL[level]


def check_vertical_circulation_baseline_maturity(
    source: VerticalCirculationBaselineSource,
    *,
    required_maturity: VerticalCirculationMaturity,
) -> CheckReceiptEnvelope:
    """Recompute the stage result without trusting unproduced observations.

    Reservation is a negative-space claim and may close from its typed
    envelope.  Positive walking-surface and assembly observations currently
    have no retained generic producer/replay source in the baseline contract;
    a self-consistent nested receipt therefore remains UNKNOWN at the stage
    boundary.  The hook is deliberately isolated so a future extractor can
    replace this stop with a replay, rather than weakening the gate.
    """

    if not isinstance(source, VerticalCirculationBaselineSource):
        raise TypeError("source must be VerticalCirculationBaselineSource")
    if not isinstance(required_maturity, VerticalCirculationMaturity):
        raise TypeError("required_maturity must be VerticalCirculationMaturity")
    receipt = check_vertical_circulation_maturity(
        source.contract,
        required_maturity=required_maturity,
    )
    if (
        required_maturity is VerticalCirculationMaturity.RESERVATION
        or receipt.status is not CheckStatus.PASS
    ):
        return receipt
    finding = CheckFinding(
        code="vertical-circulation-producer-replay-unknown",
        severity=FindingSeverity.UNKNOWN,
        message=(
            "resolved walking-surface and assembly observations lack a "
            "retained generic producer/replay source"
        ),
        subject_refs=(source.contract.ref,),
    )
    return replace(
        receipt,
        status=CheckStatus.UNKNOWN,
        findings=tuple(
            sorted(
                (*receipt.findings, finding),
                key=lambda item: (item.code, item.subject_refs, item.message),
            )
        ),
        covered_refs=(),
    )


def _bind_semantic_rule_coverage(
    receipt: CheckReceiptEnvelope,
    binding: object | None,
) -> CheckReceiptEnvelope:
    """Make every active framework rule part of the replayed denominator."""

    if binding is None:
        return receipt
    rule_refs = tuple(getattr(binding, "rule_refs"))
    binding_ref = getattr(binding, "ref")
    basis_ref = getattr(binding, "basis_ref")
    authority_ref = getattr(binding, "authority_ref")
    denominator = tuple(
        sorted({*receipt.coverage_denominator, binding_ref, *rule_refs})
    )
    return replace(
        receipt,
        subject_refs=denominator,
        source_refs=tuple(sorted({*receipt.source_refs, basis_ref})),
        authority_refs=tuple(
            sorted({*receipt.authority_refs, authority_ref})
        ),
        coverage_denominator=denominator,
        covered_refs=(
            denominator if receipt.status is CheckStatus.PASS else ()
        ),
    )


def _vertical_circulation_component_refs(
    subject_inventory: "StageSubjectInventory",
) -> tuple[str, ...]:
    from archflow.control.stage_subjects import StageSubjectDisposition

    def has_explicit_stair_semantics(semantic_kind: str) -> bool:
        normalized = "-".join(
            semantic_kind.casefold().replace("_", " ").split()
        )
        tokens = tuple(
            token for token in normalized.split("-") if token
        )
        return (
            any(
                token
                in {
                    "stair",
                    "stairs",
                    "staircase",
                    "stairway",
                    "stairwell",
                    "step",
                    "steps",
                }
                for token in tokens
            )
            or any(
                left == "vertical" and right == "circulation"
                for left, right in zip(tokens, tokens[1:])
            )
        )

    refs = {
        entry.identity_ref
        for entry in subject_inventory.entries
        if (
            (
                next(
                    (
                        item.disposition
                        for item in entry.role_obligations
                        if item.role is StageBaselineRole.VERTICAL_CIRCULATION
                    ),
                    None,
                )
                is StageSubjectDisposition.REQUIRED
            )
            or (
                subject_inventory.is_legacy_read_only
                and
                not any(
                    item.role is StageBaselineRole.VERTICAL_CIRCULATION
                    for item in entry.role_obligations
                )
                and has_explicit_stair_semantics(entry.semantic_kind)
            )
        )
    }
    return tuple(sorted(refs))


def _vertical_circulation_semantic_binding(
    subject_inventory: "StageSubjectInventory",
    component_ref: str,
) -> object | None:
    """Return the one exact current stair binding for a component."""

    if subject_inventory.is_legacy_read_only:
        return None
    matches = tuple(
        item
        for item in subject_inventory.semantic_rule_pack_bindings
        if item.component_ref == component_ref
        and item.capability_id.value == "stair"
    )
    if len(matches) != 1:
        raise StageBaselineError(
            "vertical-circulation subject lacks one exact stair rule-pack binding"
        )
    binding = matches[0]
    if not binding.active_rule_ids:
        raise StageBaselineError(
            "vertical-circulation stair binding has no active rule denominator"
        )
    return binding


def _require_vertical_circulation_source_alignment(
    profile: StageRequirementProfile,
    sources: StageBaselineSourceSet,
    *,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> None:
    expected_refs = set(_vertical_circulation_component_refs(subject_inventory))
    required_maturity = required_vertical_circulation_maturity(
        subject_inventory.baseline_level
    )
    if required_maturity is None:
        if sources.vertical_circulation:
            raise StageBaselineError(
                "pre-geometry baseline cannot consume a circulation geometry source"
            )
        return
    seen_refs: set[str] = set()
    topology_graphs = tuple(
        source.promotion.graph for source in sources.relation_topology
    )
    if sources.vertical_circulation and expected_refs and (
        len(sources.vertical_circulation) != len(expected_refs)
    ):
        raise StageBaselineError(
            "vertical-circulation sources must cover every stair instance exactly once"
        )
    for source in sources.vertical_circulation:
        contract = source.contract
        exact_fields = (
            (contract.branch, profile.branch, "branch"),
            (contract.scope_digest, profile.scope_digest, "scope"),
            (contract.stage_id, profile.stage_id, "stage"),
            (
                contract.stage_subject_ref,
                profile.stage_subject_ref,
                "stage subject ref",
            ),
            (
                contract.stage_subject_digest,
                subject_digest,
                "stage subject digest",
            ),
            (
                contract.design_state_digest,
                profile.predecessor_state_digest,
                "design state digest",
            ),
        )
        for actual, expected, field in exact_fields:
            if actual != expected:
                raise StageBaselineError(
                    f"vertical-circulation source crossed the exact {field}"
                )
        contract_refs = set(contract.component_refs)
        if len(contract_refs) != 1:
            raise StageBaselineError(
                "vertical-circulation source must bind one exact stair component"
            )
        if not contract_refs.issubset(expected_refs):
            raise StageBaselineError(
                "vertical-circulation source names a non-circulation subject"
            )
        if seen_refs.intersection(contract_refs):
            raise StageBaselineError(
                "vertical-circulation sources overlap a subject denominator"
            )
        seen_refs.update(contract_refs)
        component_ref = contract.component_refs[0]
        if (
            contract.aabb_precheck is None
            or contract.aabb_precheck.component_ref != component_ref
        ):
            raise StageBaselineError(
                "vertical-circulation source lacks its per-instance AABB envelope"
            )
        if contract.lower_interface.relation_ref == contract.upper_interface.relation_ref:
            raise StageBaselineError(
                "vertical-circulation terminal interfaces must bind distinct relations"
            )
        for interface in (contract.lower_interface, contract.upper_interface):
            matches = tuple(
                (graph, relation)
                for graph in topology_graphs
                for relation in graph.relations
                if relation.ref == interface.relation_ref
            )
            if len(matches) != 1:
                raise StageBaselineError(
                    "vertical-circulation terminal relation_ref must name exactly "
                    "one retained topology relation"
                )
            graph, relation = matches[0]
            if (
                graph.branch != profile.branch
                or graph.stage_id != profile.stage_id
                or graph.state_digest != profile.predecessor_state_digest
                or graph.scope_digest != profile.scope_digest
                or graph.stage_subject_digest != subject_digest
                or graph.subject_inventory_digest
                != subject_inventory.inventory_digest
            ):
                raise StageBaselineError(
                    "vertical-circulation terminal relation crossed its exact "
                    "retained topology context"
                )
            endpoint_refs = {item.node_ref for item in relation.participants}
            if relation.kind.value not in {"access", "interface"}:
                raise StageBaselineError(
                    "vertical-circulation terminal relation must be ACCESS or INTERFACE"
                )
            if component_ref not in endpoint_refs or interface.level_ref not in endpoint_refs:
                raise StageBaselineError(
                    "vertical-circulation terminal relation endpoints do not "
                    "match the stair component and interface level"
                )

        realized_path_claimed = (
            contract.maturity is not VerticalCirculationMaturity.RESERVATION
        )
        if not realized_path_claimed:
            if source.readback_source_digest is not None:
                raise StageBaselineError(
                    "circulation reservation cannot credit a CAD readback source"
                )
            continue
        if source.readback_source_digest is None:
            raise StageBaselineError(
                "resolved vertical circulation lacks an exact CAD readback source"
            )
        readback_sources = tuple(
            item
            for item in sources.cad_readback
            if item.source_digest == source.readback_source_digest
        )
        if len(readback_sources) != 1:
            raise StageBaselineError(
                "vertical circulation does not bind exactly one retained CAD "
                "readback source"
            )
        readback_source = readback_sources[0]
        profile_fields = (
            (readback_source.profile.branch, contract.branch, "branch"),
            (
                readback_source.profile.scope_digest,
                contract.scope_digest,
                "scope",
            ),
            (readback_source.profile.stage_id, contract.stage_id, "stage"),
            (
                readback_source.profile.program_digest,
                contract.program_digest,
                "program digest",
            ),
            (
                readback_source.profile.length_unit,
                source.solve_request.length_unit.value,
                "length unit",
            ),
            (
                readback_source.profile.up_axis.value,
                contract.up_axis.value,
                "up axis",
            ),
            (
                readback_source.snapshot.snapshot_digest,
                contract.readback_digest,
                "readback digest",
            ),
        )
        for actual, expected, field in profile_fields:
            if actual != expected:
                raise StageBaselineError(
                    "vertical circulation crossed its exact CAD " + field
                )
        expected_pairs = {
            (item.object_ref, item.operation_ref)
            for item in (
                *contract.object_bindings,
                *contract.interface_host_bindings,
            )
        }
        profile_pairs = {
            (item.object_ref, item.operation_ref)
            for item in readback_source.profile.object_requirements
        }
        snapshot_pairs = {
            (item.object_ref, item.operation_ref)
            for item in readback_source.snapshot.objects
        }
        if not expected_pairs.issubset(profile_pairs) or not expected_pairs.issubset(
            snapshot_pairs
        ):
            raise StageBaselineError(
                "vertical circulation object/program bindings are absent from "
                "the exact CAD readback"
            )
    if sources.vertical_circulation and seen_refs != expected_refs:
        raise StageBaselineError(
            "vertical-circulation sources must cover every stair instance exactly once"
        )


def _require_current_relation_source_alignment(
    profile: StageRequirementProfile,
    sources: StageBaselineSourceSet,
    *,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> None:
    """Bind all relation validators to one exact current stage graph set."""

    topology_graphs = tuple(
        source.promotion.graph for source in sources.relation_topology
    )
    realization_graphs = tuple(
        source.graph for source in sources.relation_realization
    )
    current_graphs = (*topology_graphs, *realization_graphs)
    for graph in current_graphs:
        if (
            graph.branch != profile.branch
            or graph.stage_id != profile.stage_id
            or graph.state_digest != profile.predecessor_state_digest
            or graph.scope_digest != profile.scope_digest
            or graph.stage_subject_digest != subject_digest
            or graph.subject_inventory_digest
            != subject_inventory.inventory_digest
        ):
            raise StageBaselineError(
                "relation baseline source crossed the exact profile, state, "
                "scope, stage subject, or subject inventory"
            )

    # A satisfied component-function row is only a claim about one component.
    # It becomes a Stage 2/3 control chain only when the exact requirement-set
    # questions and rules are consumed by one verified topology source.  Do not
    # infer this join from semantic kind or from a broad graph-level PASS.
    if (
        len(sources.component_functions) == 1
        and sources.component_functions[0].relation_requirements is not None
        and topology_graphs
    ):
        requirement_set = (
            sources.component_functions[0].relation_requirements
        )
        assert requirement_set is not None
        for requirement in requirement_set.requirements:
            matching_sources = tuple(
                source
                for source in sources.relation_topology
                if any(
                    question == requirement.question
                    for question in source.context.questions
                )
            )
            if len(matching_sources) != 1:
                raise StageBaselineError(
                    "each functional relation question must be consumed by one "
                    "exact Stage 2 topology source"
                )
            topology_source = matching_sources[0]
            basis_by_id = {
                basis.basis_id: basis for basis in topology_source.context.bases
            }
            for basis_id in requirement.question.basis_ids:
                basis = basis_by_id.get(basis_id)
                if (
                    basis is None
                    or requirement.question.ref not in basis.question_refs
                    or requirement.rule.relation_kind
                    not in basis.allowed_relation_kinds
                    or not set(requirement.rule.evidence_refs).issubset(
                        basis.evidence_refs
                    )
                    or not set(requirement.rule.authority_refs).issubset(
                        basis.authority_refs
                    )
                ):
                    raise StageBaselineError(
                        "functional relation question lacks its exact evidence-"
                        "bound topology basis"
                    )
            policy = topology_source.compilation.policy
            if policy is None or sum(
                rule == requirement.rule for rule in policy.rules
            ) != 1:
                raise StageBaselineError(
                    "functional relation rule was not consumed by the Stage 2 "
                    "topology compilation"
                )
            expected_slot = requirement.slot
            matching_slots = tuple(
                slot
                for slot in topology_source.compilation.slots
                if (
                    slot.stage_id == expected_slot.stage_id
                    and slot.rule_id == expected_slot.rule_id
                    and slot.node_ref == expected_slot.node_ref
                    and slot.node_kind == expected_slot.node_kind
                    and slot.semantic_kind == expected_slot.semantic_kind
                    and slot.relation_kind == expected_slot.relation_kind
                    and slot.subject_role == expected_slot.subject_role
                    and slot.counted_role == expected_slot.counted_role
                    and slot.minimum_count == expected_slot.minimum_count
                    and slot.maximum_count == expected_slot.maximum_count
                    and slot.scenario_ref == expected_slot.scenario_ref
                    and slot.evidence_refs == expected_slot.evidence_refs
                    and slot.authority_refs == expected_slot.authority_refs
                    and slot.allow_not_applicable
                    == expected_slot.allow_not_applicable
                )
            )
            if len(matching_slots) != 1:
                raise StageBaselineError(
                    "functional relation obligation lacks one exact component-"
                    "bound topology slot"
                )
            if not any(
                requirement.question.ref in relation.source_refs
                for relation in topology_source.promotion.graph.relations
            ):
                raise StageBaselineError(
                    "functional relation question has no verified topology relation"
                )

    topology_digests = tuple(
        sorted(graph.graph_digest for graph in topology_graphs)
    )
    realization_digests = tuple(
        sorted(graph.graph_digest for graph in realization_graphs)
    )
    if len(topology_digests) != len(set(topology_digests)):
        raise StageBaselineError(
            "relation topology source repeats a current graph"
        )
    if len(realization_digests) != len(set(realization_digests)):
        raise StageBaselineError(
            "relation realization source repeats a current graph"
        )
    if realization_digests and realization_digests != topology_digests:
        raise StageBaselineError(
            "relation topology and realization must bind the same exact graph set"
        )

    inheritance_digests = tuple(
        sorted(
            source.current_realization.graph.graph_digest
            for source in sources.relation_inheritance
        )
    )
    if len(inheritance_digests) != len(set(inheritance_digests)):
        raise StageBaselineError(
            "relation inheritance source repeats a current graph"
        )
    if inheritance_digests and inheritance_digests != realization_digests:
        raise StageBaselineError(
            "relation inheritance and realization must bind the same exact "
            "current graph set"
        )

    inheritance_sources = sources.relation_inheritance
    if inheritance_sources:
        acceptance_set_digests = {
            source.predecessor.acceptance_set_digest
            for source in inheritance_sources
        }
        if len(acceptance_set_digests) != 1:
            raise StageBaselineError(
                "relation inheritance sources crossed the accepted predecessor "
                "topology set"
            )
        accepted_pairs = {
            (
                item.topology_source_digest,
                item.graph_digest,
            )
            for item in inheritance_sources[0].predecessor.accepted_topologies
        }
        selected_pairs = tuple(
            (
                source.predecessor.topology_source_digest,
                source.predecessor.graph.graph_digest,
            )
            for source in inheritance_sources
        )
        if (
            len(selected_pairs) != len(set(selected_pairs))
            or set(selected_pairs) != accepted_pairs
        ):
            raise StageBaselineError(
                "relation inheritance must cover the full accepted predecessor "
                "topology set exactly once"
            )


def _expected_checks(
    profile: StageRequirementProfile,
    sources: StageBaselineSourceSet,
    *,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> tuple[_ExpectedBaselineCheck, ...]:
    _require_current_relation_source_alignment(
        profile,
        sources,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    _require_vertical_circulation_source_alignment(
        profile,
        sources,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    required_circulation_maturity = required_vertical_circulation_maturity(
        subject_inventory.baseline_level
    )
    checks: list[_ExpectedBaselineCheck] = []
    from archflow.control.component_functions import ComponentFunctionId
    from archflow.validation.stage_control import (
        check_component_function_baseline,
        check_visual_inventory_baseline,
        component_function_stage_requirement,
        visual_inventory_stage_requirement,
    )

    visual_denominator = (
        f"visual-inventory:"
        f"{subject_inventory.visual_inventory_digest or 'missing'}",
    )
    visual_check_id = (
        f"visual-inventory-{subject_inventory.inventory_digest[:24]}"
    )
    if len(sources.visual_inventory) == 1:
        visual_source = sources.visual_inventory[0]
        checks.append(
            _ExpectedBaselineCheck(
                requirement=visual_inventory_stage_requirement(
                    visual_source,
                    subject_inventory,
                ),
                receipt=check_visual_inventory_baseline(
                    visual_source,
                    subject_inventory,
                    scope_digest=profile.scope_digest,
                    subject_digest=subject_digest,
                ),
                source_kind="visual_inventory",
                roles=(StageBaselineRole.COMPONENT_LINEAGE,),
                role_targets=((StageBaselineRole.COMPONENT_LINEAGE, ()),),
                source_digest=visual_source.source_digest,
            )
        )
    else:
        visual_requirement = StageCheckRequirement(
            requirement_id=visual_check_id,
            checker_id="visual-inventory-validator",
            target_kind=RequirementTargetKind.COMPONENT,
            basis_mode=RequirementBasisMode.UNIVERSAL,
            denominator_refs=visual_denominator,
        )
        visual_receipt = CheckReceiptEnvelope(
            check_id=visual_check_id,
            checker_id="visual-inventory-validator",
            checker_version="1.0.0",
            branch=profile.branch,
            scope_digest=profile.scope_digest,
            subject_refs=visual_denominator,
            subject_digest=subject_digest,
            status=CheckStatus.FAIL,
            findings=(
                CheckFinding(
                    code="visual-source-cardinality",
                    severity=FindingSeverity.ERROR,
                    message=(
                        "stage baseline requires exactly one visual inventory source"
                    ),
                    subject_refs=visual_denominator,
                ),
            ),
            coverage_denominator=visual_denominator,
        )
        checks.append(
            _ExpectedBaselineCheck(
                requirement=visual_requirement,
                receipt=visual_receipt,
                source_kind="visual_inventory",
                roles=(StageBaselineRole.COMPONENT_LINEAGE,),
                role_targets=((StageBaselineRole.COMPONENT_LINEAGE, ()),),
                source_digest=canonical_digest(
                    {
                        "kind": "missing-or-ambiguous-visual-inventory",
                        "inventory_digest": subject_inventory.inventory_digest,
                    }
                ),
            )
        )

    function_denominator = tuple(
        sorted(
            f"function-evaluation:{entry.identity_ref}/{function_id.value}"
            for entry in subject_inventory.entries
            for function_id in ComponentFunctionId
        )
    )
    function_check_id = (
        f"component-functions-{subject_inventory.inventory_digest[:24]}"
    )
    if len(sources.component_functions) == 1:
        function_source = sources.component_functions[0]
        checks.append(
            _ExpectedBaselineCheck(
                requirement=component_function_stage_requirement(
                    function_source,
                    subject_inventory,
                ),
                receipt=check_component_function_baseline(
                    function_source,
                    subject_inventory,
                    scope_digest=profile.scope_digest,
                    subject_digest=subject_digest,
                ),
                source_kind="component_functions",
                roles=(StageBaselineRole.ASSEMBLY_RELATIONSHIPS,),
                role_targets=(
                    (StageBaselineRole.ASSEMBLY_RELATIONSHIPS, ()),
                ),
                source_digest=function_source.source_digest,
            )
        )
    else:
        function_requirement = StageCheckRequirement(
            requirement_id=function_check_id,
            checker_id="component-function-validator",
            target_kind=RequirementTargetKind.RELATION,
            basis_mode=RequirementBasisMode.UNIVERSAL,
            denominator_refs=function_denominator,
        )
        function_receipt = CheckReceiptEnvelope(
            check_id=function_check_id,
            checker_id="component-function-validator",
            checker_version="1.0.0",
            branch=profile.branch,
            scope_digest=profile.scope_digest,
            subject_refs=function_denominator,
            subject_digest=subject_digest,
            status=CheckStatus.FAIL,
            findings=(
                CheckFinding(
                    code="function-source-cardinality",
                    severity=FindingSeverity.ERROR,
                    message=(
                        "stage baseline requires exactly one component function ledger"
                    ),
                    subject_refs=(function_denominator[0],),
                ),
            ),
            coverage_denominator=function_denominator,
        )
        checks.append(
            _ExpectedBaselineCheck(
                requirement=function_requirement,
                receipt=function_receipt,
                source_kind="component_functions",
                roles=(StageBaselineRole.ASSEMBLY_RELATIONSHIPS,),
                role_targets=(
                    (StageBaselineRole.ASSEMBLY_RELATIONSHIPS, ()),
                ),
                source_digest=canonical_digest(
                    {
                        "kind": "missing-or-ambiguous-component-functions",
                        "inventory_digest": subject_inventory.inventory_digest,
                    }
                ),
            )
        )
    if subject_inventory.is_legacy_read_only:
        checks.clear()
    for source in sources.component_lineage:
        lineage_component_ids = tuple(
            sorted(
                {
                    operation.ref.component_id
                    for operation in source.source_receipt.predecessor_operations
                }
            )
        )
        checks.append(
            _ExpectedBaselineCheck(
                requirement=component_lineage_stage_requirement(source.profile),
                receipt=bridge_component_lineage_receipt(
                    source.profile,
                    source.source_receipt,
                    stage_subject_digest=subject_digest,
                ),
                source_kind="component_lineage",
                roles=(StageBaselineRole.COMPONENT_LINEAGE,),
                role_targets=(
                    (
                        StageBaselineRole.COMPONENT_LINEAGE,
                        _inventory_identity_refs(
                            subject_inventory,
                            lineage_component_ids,
                        ),
                    ),
                ),
                source_digest=source.source_digest,
            )
        )
    for source in sources.spatial_layout:
        spatial_input = source.validator_input
        spatial_receipt = validate_spatial_layout(
            elements=spatial_input.elements,
            host_regions=spatial_input.host_regions,
            required_component_ids=spatial_input.required_component_ids,
            opening_clear_regions=spatial_input.opening_clear_regions,
            minimum_column_wall_clearance=(
                spatial_input.minimum_column_wall_clearance
            ),
            linear_tolerance=spatial_input.linear_tolerance,
            intersection_volume_tolerance=(
                spatial_input.intersection_volume_tolerance
            ),
            length_unit=spatial_input.length_unit,
        )
        checks.append(
            _ExpectedBaselineCheck(
                requirement=spatial_layout_stage_requirement(source.profile),
                receipt=bridge_spatial_validation_receipt(
                    source.profile,
                    spatial_receipt,
                    stage_subject_digest=subject_digest,
                ),
                source_kind="spatial_layout",
                roles=(StageBaselineRole.SPATIAL_ENVELOPE,),
                role_targets=(
                    (
                        StageBaselineRole.SPATIAL_ENVELOPE,
                        _inventory_identity_refs(
                            subject_inventory,
                            spatial_input.required_component_ids,
                        ),
                    ),
                ),
                source_digest=source.source_digest,
            )
        )
    if required_circulation_maturity is not None:
        for source in sources.vertical_circulation:
            contract = source.contract
            semantic_binding = _vertical_circulation_semantic_binding(
                subject_inventory,
                contract.component_refs[0],
            )
            semantic_rule_refs = (
                ()
                if semantic_binding is None
                else (
                    semantic_binding.ref,
                    *semantic_binding.rule_refs,
                )
            )
            semantic_basis_refs = (
                ()
                if semantic_binding is None
                else (semantic_binding.basis_ref,)
            )
            semantic_authority_refs = (
                ()
                if semantic_binding is None
                else (semantic_binding.authority_ref,)
            )
            checks.append(
                _ExpectedBaselineCheck(
                    requirement=vertical_circulation_stage_requirement(
                        contract,
                        required_maturity=required_circulation_maturity,
                        semantic_rule_refs=semantic_rule_refs,
                        semantic_basis_refs=semantic_basis_refs,
                        semantic_authority_refs=(
                            semantic_authority_refs
                        ),
                    ),
                    receipt=_bind_semantic_rule_coverage(
                        check_vertical_circulation_baseline_maturity(
                            source,
                            required_maturity=(
                                required_circulation_maturity
                            ),
                        ),
                        semantic_binding,
                    ),
                    source_kind="vertical_circulation",
                    roles=(StageBaselineRole.VERTICAL_CIRCULATION,),
                    role_targets=(
                        (
                            StageBaselineRole.VERTICAL_CIRCULATION,
                            contract.component_refs,
                        ),
                    ),
                    source_digest=source.source_digest,
                )
            )
    for source in sources.assembly:
        checks.append(
            _ExpectedBaselineCheck(
                requirement=assembly_stage_requirement(source),
                receipt=check_assembly(
                    source,
                    branch=profile.branch,
                    scope_digest=profile.scope_digest,
                    stage_subject_digest=subject_digest,
                ),
                source_kind="assembly",
                roles=_assembly_roles(source),
                role_targets=_assembly_role_targets(source),
                source_digest=source.profile_digest,
            )
        )
    for source in sources.material_binding:
        checks.append(
            _ExpectedBaselineCheck(
                requirement=material_binding_stage_requirement(source.profile),
                receipt=validate_material_bindings(
                    source.profile,
                    source.ledger,
                    source.snapshot,
                    stage_subject_digest=subject_digest,
                ),
                source_kind="material_binding",
                roles=(StageBaselineRole.MATERIAL_BINDING,),
                role_targets=(
                    (
                        StageBaselineRole.MATERIAL_BINDING,
                        tuple(
                            sorted(
                                requirement.semantic_subject_ref
                                for requirement in source.profile.requirements
                            )
                        ),
                    ),
                ),
                source_digest=source.source_digest,
            )
        )
    for source in sources.cad_readback:
        checks.append(
            _ExpectedBaselineCheck(
                requirement=cad_readback_stage_requirement(source.profile),
                receipt=validate_cad_readback(
                    source.profile,
                    source.snapshot,
                    stage_subject_digest=subject_digest,
                ),
                source_kind="cad_readback",
                roles=(StageBaselineRole.CAD_READBACK,),
                role_targets=(
                    (
                        StageBaselineRole.CAD_READBACK,
                        tuple(
                            sorted(
                                requirement.object_ref
                                for requirement in source.profile.object_requirements
                            )
                        ),
                    ),
                ),
                source_digest=source.source_digest,
            )
        )
    for source in sources.relation_topology:
        from archflow.control.check_requirements import (
            relation_authoring_stage_requirements,
        )
        from archflow.control.relation_checks import check_relation_coverage

        requirements = relation_authoring_stage_requirements(
            source.context,
            source.compilation,
            subject_inventory,
            promotion=source.promotion,
        )
        graph = source.promotion.graph
        _manifest, coverage_receipt = check_relation_coverage(
            graph,
            source.compilation.policy,
            subject_inventory,
            source.compilation.slots,
            not_applicable=source.not_applicable,
            promotion=source.promotion.receipt,
        )
        receipt_rows = (
            coverage_receipt,
            *source.promotion.verification_receipts,
        )
        receipts_by_id: dict[str, CheckReceiptEnvelope] = {}
        for receipt in receipt_rows:
            if receipt.check_id in receipts_by_id:
                raise StageBaselineError(
                    "relation topology source repeats a check_id"
                )
            receipts_by_id[receipt.check_id] = receipt
        relations_by_question = {
            question.question_id: tuple(
                relation
                for relation in graph.relations
                if question.ref in relation.source_refs
            )
            for question in source.context.questions
        }
        for requirement in requirements:
            receipt = receipts_by_id.get(requirement.requirement_id)
            if receipt is None:
                raise StageBaselineError(
                    "relation topology source omits a required verification receipt"
                )
            if requirement.requirement_id == coverage_receipt.check_id:
                relations = graph.relations
            else:
                prefix = "relation-verification-"
                if not requirement.requirement_id.startswith(prefix):
                    raise StageBaselineError(
                        "relation topology requirement has an unknown identity"
                    )
                question_id = requirement.requirement_id[len(prefix) :]
                relations = relations_by_question.get(question_id, ())
                if not relations:
                    raise StageBaselineError(
                        "relation verification requirement lost its question"
                    )
            roles, role_targets = _relation_roles_and_targets(relations)
            checks.append(
                _ExpectedBaselineCheck(
                    requirement=requirement,
                    receipt=receipt,
                    source_kind="relation_topology",
                    roles=roles,
                    role_targets=role_targets,
                    source_digest=source.source_digest,
                )
            )
    for source in sources.relation_realization:
        from archflow.control.check_requirements import (
            relation_realization_stage_requirement,
        )
        from archflow.validation.relation_realization import (
            check_relation_realization,
        )

        roles, role_targets = _relation_roles_and_targets(
            source.graph.relations
        )
        checks.append(
            _ExpectedBaselineCheck(
                requirement=relation_realization_stage_requirement(
                    source.graph,
                    source.manifest,
                ),
                receipt=check_relation_realization(
                    source.graph,
                    source.manifest,
                    source.program,
                    source.readback,
                    verification_receipts=source.verification_receipts,
                ),
                source_kind="relation_realization",
                roles=roles,
                role_targets=role_targets,
                source_digest=source.source_digest,
            )
        )
    for source in sources.relation_inheritance:
        from archflow.control.check_requirements import (
            stage_relation_inheritance_stage_requirement,
        )
        from archflow.control.stage_relation_inheritance import (
            bridge_stage_relation_inheritance_receipt,
            compile_stage_relation_inheritance,
        )
        from archflow.validation.relation_realization import (
            check_relation_realization,
        )

        if source.current_realization not in sources.relation_realization:
            raise StageBaselineError(
                "relation inheritance source is not backed by the exact "
                "current realization source"
            )
        realization_source = source.current_realization
        current_realization_receipt = check_relation_realization(
            realization_source.graph,
            realization_source.manifest,
            realization_source.program,
            realization_source.readback,
            verification_receipts=(
                realization_source.verification_receipts
            ),
        )
        inheritance_receipt = compile_stage_relation_inheritance(
            source.predecessor_graph,
            realization_source.graph,
            realization_source.manifest,
            current_realization_receipt,
        )
        roles, role_targets = _relation_roles_and_targets(
            realization_source.graph.relations
        )
        checks.append(
            _ExpectedBaselineCheck(
                requirement=stage_relation_inheritance_stage_requirement(
                    source.predecessor,
                    realization_source.graph,
                    realization_source.manifest,
                    current_realization_receipt,
                ),
                receipt=bridge_stage_relation_inheritance_receipt(
                    source.predecessor,
                    realization_source.graph,
                    realization_source.manifest,
                    current_realization_receipt,
                    inheritance_receipt,
                ),
                source_kind="relation_inheritance",
                roles=roles,
                role_targets=role_targets,
                source_digest=source.source_digest,
            )
        )
    ordered = tuple(sorted(checks, key=lambda item: item.requirement.requirement_id))
    ids = tuple(item.requirement.requirement_id for item in ordered)
    if len(ids) != len(set(ids)):
        raise StageBaselineError("baseline sources derive duplicate requirements")
    return ordered


@dataclass(frozen=True, slots=True)
class StageBaselineRoleCoverage:
    role: StageBaselineRole
    requirement_ids: tuple[str, ...]
    source_digests: tuple[str, ...]
    check_receipt_digests: tuple[str, ...]

    SCHEMA = "StageBaselineRoleCoverage@2"

    def __post_init__(self) -> None:
        if not isinstance(self.role, StageBaselineRole):
            raise TypeError("role must be StageBaselineRole")
        for field in (
            "requirement_ids",
            "source_digests",
            "check_receipt_digests",
        ):
            values = getattr(self, field)
            if not isinstance(values, tuple) or not values:
                raise StageBaselineError(f"{field} must be non-empty")
            normalized = tuple(sorted(values))
            if len(normalized) != len(set(normalized)):
                raise StageBaselineError(f"{field} contains duplicates")
            if field == "requirement_ids":
                for value in normalized:
                    identifier(value, "requirement_id")
            else:
                for value in normalized:
                    require_sha256(value, field)
            object.__setattr__(self, field, normalized)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role.value,
            "requirement_ids": list(self.requirement_ids),
            "source_digests": list(self.source_digests),
            "check_receipt_digests": list(self.check_receipt_digests),
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageBaselineRoleCoverage":
        payload = exact_mapping(
            value,
            {
                "schema",
                "role",
                "requirement_ids",
                "source_digests",
                "check_receipt_digests",
            },
            "stage baseline role coverage",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError("unsupported baseline role schema")
        for field in (
            "requirement_ids",
            "source_digests",
            "check_receipt_digests",
        ):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        return cls(
            role=StageBaselineRole(payload["role"]),
            requirement_ids=tuple(payload["requirement_ids"]),
            source_digests=tuple(payload["source_digests"]),
            check_receipt_digests=tuple(payload["check_receipt_digests"]),
        )


@dataclass(frozen=True, slots=True)
class StageBaselineCoverageReceipt:
    profile_id: str
    profile_digest: str
    branch: BranchRef
    stage_id: str
    level: StageBaselineLevel
    stage_subject_inventory_digest: str | None
    required_roles: tuple[StageBaselineRole, ...]
    coverage: tuple[StageBaselineRoleCoverage, ...]
    missing_roles: tuple[StageBaselineRole, ...]
    status: StageBaselineStatus

    SCHEMA = "StageBaselineCoverageReceipt@3"
    LEGACY_SCHEMA = "StageBaselineCoverageReceipt@2"

    def __post_init__(self) -> None:
        identifier(self.profile_id, "profile_id")
        object.__setattr__(
            self,
            "profile_digest",
            require_sha256(self.profile_digest, "profile_digest"),
        )
        if not isinstance(self.branch, BranchRef):
            raise TypeError("branch must be BranchRef")
        self.branch.run.base.require_digest()
        identifier(self.stage_id, "stage_id")
        if not isinstance(self.level, StageBaselineLevel):
            raise TypeError("level must be StageBaselineLevel")
        if self.stage_subject_inventory_digest is not None:
            object.__setattr__(
                self,
                "stage_subject_inventory_digest",
                require_sha256(
                    self.stage_subject_inventory_digest,
                    "stage_subject_inventory_digest",
                ),
            )
        if not isinstance(self.status, StageBaselineStatus):
            raise TypeError("status must be StageBaselineStatus")
        base_roles = tuple(sorted(BASELINE_LEVEL_ROLES[self.level]))
        allowed_role_sets = {base_roles}
        if self.level is not StageBaselineLevel.PRE_GEOMETRY:
            allowed_role_sets.add(
                tuple(
                    sorted(
                        {
                            *BASELINE_LEVEL_ROLES[self.level],
                            StageBaselineRole.VERTICAL_CIRCULATION,
                        }
                    )
                )
            )
        if self.required_roles not in allowed_role_sets:
            raise StageBaselineError("required_roles do not match baseline level")
        expected_roles = self.required_roles
        if not isinstance(self.coverage, tuple) or any(
            not isinstance(item, StageBaselineRoleCoverage)
            for item in self.coverage
        ):
            raise TypeError("coverage must contain StageBaselineRoleCoverage")
        ordered = tuple(sorted(self.coverage, key=lambda item: item.role))
        covered_roles = tuple(item.role for item in ordered)
        if len(covered_roles) != len(set(covered_roles)):
            raise StageBaselineError("coverage contains duplicate roles")
        if not set(covered_roles).issubset(expected_roles):
            raise StageBaselineError("coverage contains a role outside the level")
        object.__setattr__(self, "coverage", ordered)
        expected_missing = tuple(sorted(set(expected_roles) - set(covered_roles)))
        if self.missing_roles != expected_missing:
            raise StageBaselineError("missing_roles do not match coverage")
        expected_status = (
            StageBaselineStatus.SATISFIED
            if not expected_missing
            else StageBaselineStatus.OPEN
        )
        if self.status is not expected_status:
            raise StageBaselineError("baseline status does not match missing roles")

    @property
    def receipt_digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": (
                self.SCHEMA
                if self.stage_subject_inventory_digest is not None
                else self.LEGACY_SCHEMA
            ),
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "level": self.level.value,
            "required_roles": [item.value for item in self.required_roles],
            "coverage": [item.to_dict() for item in self.coverage],
            "missing_roles": [item.value for item in self.missing_roles],
            "status": self.status.value,
            "stage_acceptance_authority": False,
            "canonical_write_authority": False,
        }
        if self.stage_subject_inventory_digest is not None:
            payload["stage_subject_inventory_digest"] = (
                self.stage_subject_inventory_digest
            )
        return payload

    @classmethod
    def from_dict(cls, value: object) -> "StageBaselineCoverageReceipt":
        if not isinstance(value, dict):
            raise TypeError("stage baseline coverage receipt must be a mapping")
        schema = value.get("schema")
        common_fields = {
                "schema",
                "profile_id",
                "profile_digest",
                "branch",
                "stage_id",
                "level",
                "required_roles",
                "coverage",
                "missing_roles",
                "status",
                "stage_acceptance_authority",
                "canonical_write_authority",
        }
        if schema == cls.SCHEMA:
            payload = exact_mapping(
                value,
                common_fields | {"stage_subject_inventory_digest"},
                "stage baseline coverage receipt",
            )
            inventory_digest = payload["stage_subject_inventory_digest"]
        elif schema == cls.LEGACY_SCHEMA:
            payload = exact_mapping(
                value,
                common_fields,
                "stage baseline coverage receipt",
            )
            inventory_digest = None
        else:
            raise StageBaselineError("unsupported stage baseline schema")
        if (
            payload["stage_acceptance_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise StageBaselineError("baseline authority flags changed")
        for field in ("required_roles", "coverage", "missing_roles"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        return cls(
            profile_id=payload["profile_id"],
            profile_digest=payload["profile_digest"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            level=StageBaselineLevel(payload["level"]),
            stage_subject_inventory_digest=inventory_digest,
            required_roles=tuple(
                StageBaselineRole(item) for item in payload["required_roles"]
            ),
            coverage=tuple(
                StageBaselineRoleCoverage.from_dict(item)
                for item in payload["coverage"]
            ),
            missing_roles=tuple(
                StageBaselineRole(item) for item in payload["missing_roles"]
            ),
            status=StageBaselineStatus(payload["status"]),
        )


def baseline_level_for_design_phase(phase: DesignPhase) -> StageBaselineLevel:
    if not isinstance(phase, DesignPhase):
        raise TypeError("phase must be DesignPhase")
    return DESIGN_PHASE_BASELINE_LEVEL[phase]


def _validated_subject_inventory_digest(
    profile: StageRequirementProfile,
    *,
    level: StageBaselineLevel,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> str:
    # Local import is required because stage_subjects owns obligations typed
    # with the baseline enums declared by this module.
    from archflow.control.stage_subjects import StageSubjectInventory

    if not isinstance(subject_inventory, StageSubjectInventory):
        raise TypeError("subject_inventory must be StageSubjectInventory")
    exact_fields = (
        (subject_inventory.branch, profile.branch, "branch"),
        (subject_inventory.stage_id, profile.stage_id, "stage_id"),
        (
            subject_inventory.stage_subject_ref,
            profile.stage_subject_ref,
            "stage_subject_ref",
        ),
        (
            subject_inventory.stage_subject_digest,
            subject_digest,
            "stage_subject_digest",
        ),
        (subject_inventory.baseline_level, level, "baseline_level"),
    )
    for actual, expected, field in exact_fields:
        if actual != expected:
            raise StageBaselineError(
                f"subject inventory {field} does not match stage baseline"
            )
    return require_sha256(
        subject_inventory.inventory_digest,
        "stage_subject_inventory_digest",
    )


def _inventory_covers_role(
    subject_inventory: "StageSubjectInventory",
    *,
    role: StageBaselineRole,
    covered_target_refs: set[str],
    source_basis_refs: set[str],
    authority_basis_refs: set[str],
) -> bool:
    from archflow.control.stage_subjects import StageSubjectDisposition

    if role is StageBaselineRole.VERTICAL_CIRCULATION:
        expected_refs = set(
            _vertical_circulation_component_refs(subject_inventory)
        )
        return bool(expected_refs) and expected_refs.issubset(
            covered_target_refs
        )

    obligations = tuple(
        obligation
        for entry in subject_inventory.entries
        for obligation in entry.role_obligations
        if obligation.role is role
    )
    if not obligations:
        return False
    for obligation in obligations:
        if obligation.disposition is StageSubjectDisposition.REQUIRED:
            if not set(obligation.target_refs).issubset(covered_target_refs):
                return False
            continue
        if obligation.disposition is StageSubjectDisposition.NOT_APPLICABLE:
            if (
                not obligation.evidence_refs
                or not obligation.authority_refs
                or not set(obligation.evidence_refs).issubset(
                    source_basis_refs
                )
                or not set(obligation.authority_refs).issubset(
                    authority_basis_refs
                )
            ):
                return False
            continue
        return False
    return True


def derive_stage_baseline_requirements(
    profile: StageRequirementProfile,
    *,
    level: StageBaselineLevel,
    sources: StageBaselineSourceSet,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> tuple[StageCheckRequirement, ...]:
    """Derive the framework-owned denominator from exact typed sources."""

    if not isinstance(profile, StageRequirementProfile):
        raise TypeError("profile must be StageRequirementProfile")
    if not isinstance(level, StageBaselineLevel):
        raise TypeError("level must be StageBaselineLevel")
    if not isinstance(sources, StageBaselineSourceSet):
        raise TypeError("sources must be StageBaselineSourceSet")
    if sources.is_legacy_read_only:
        raise StageBaselineError(
            "legacy stage baseline sources are read-only"
        )
    subject_digest = require_sha256(subject_digest, "subject_digest")
    _validated_subject_inventory_digest(
        profile,
        level=level,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    return tuple(
        item.requirement
        for item in _expected_checks(
            profile,
            sources,
            subject_digest=subject_digest,
            subject_inventory=subject_inventory,
        )
    )


def derive_stage_requirement_profile(
    profile: StageRequirementProfile,
    *,
    level: StageBaselineLevel,
    sources: StageBaselineSourceSet,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> StageRequirementProfile:
    """Merge the framework-owned baseline denominator before profile binding.

    Caller-authored project checks are preserved, but a caller cannot replace
    a framework requirement under the same identity.  Legacy source sets are
    replay-only and therefore cannot author a current profile.
    """

    derived = derive_stage_baseline_requirements(
        profile,
        level=level,
        sources=sources,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    merged = {item.requirement_id: item for item in profile.requirements}
    for requirement in derived:
        existing = merged.get(requirement.requirement_id)
        if existing is not None and existing != requirement:
            raise StageBaselineError(
                "caller requirement conflicts with a framework baseline "
                f"requirement: {requirement.requirement_id}"
            )
        merged[requirement.requirement_id] = requirement
    return replace(
        profile,
        requirements=tuple(
            sorted(merged.values(), key=lambda item: item.requirement_id)
        ),
    )


def derive_stage_baseline_check_receipts(
    profile: StageRequirementProfile,
    *,
    sources: StageBaselineSourceSet,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
) -> tuple[CheckReceiptEnvelope, ...]:
    """Recompute every framework-owned receipt from the exact typed sources.

    Project-specific requirements may still require caller-supplied independent
    receipts.  This function returns only the framework denominator and refuses
    to do so until the supplied profile retains each corresponding requirement
    exactly, preventing a caller from deriving receipts and then dropping their
    requirements before closure.
    """

    if not isinstance(profile, StageRequirementProfile):
        raise TypeError("profile must be StageRequirementProfile")
    if not isinstance(sources, StageBaselineSourceSet):
        raise TypeError("sources must be StageBaselineSourceSet")
    subject_digest = require_sha256(subject_digest, "subject_digest")
    _validated_subject_inventory_digest(
        profile,
        level=subject_inventory.baseline_level,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    expected = _expected_checks(
        profile,
        sources,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    requirements = {item.requirement_id: item for item in profile.requirements}
    for item in expected:
        if requirements.get(item.requirement.requirement_id) != item.requirement:
            raise StageBaselineError(
                "profile omits or changes a framework baseline requirement: "
                + item.requirement.requirement_id
            )
    return tuple(item.receipt for item in expected)


def compile_stage_baseline_coverage(
    profile: StageRequirementProfile,
    *,
    level: StageBaselineLevel,
    sources: StageBaselineSourceSet,
    subject_digest: str,
    subject_inventory: "StageSubjectInventory",
    check_receipts: tuple[CheckReceiptEnvelope, ...],
) -> StageBaselineCoverageReceipt:
    """Recompute source receipts and credit only exact passing requirements."""

    if not isinstance(profile, StageRequirementProfile):
        raise TypeError("profile must be StageRequirementProfile")
    if not isinstance(level, StageBaselineLevel):
        raise TypeError("level must be StageBaselineLevel")
    if not isinstance(sources, StageBaselineSourceSet):
        raise TypeError("sources must be StageBaselineSourceSet")
    subject_digest = require_sha256(subject_digest, "subject_digest")
    inventory_digest = _validated_subject_inventory_digest(
        profile,
        level=level,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    )
    if not isinstance(check_receipts, tuple) or any(
        not isinstance(item, CheckReceiptEnvelope) for item in check_receipts
    ):
        raise TypeError("check_receipts must contain CheckReceiptEnvelope")

    required_set = set(BASELINE_LEVEL_ROLES[level])
    if (
        required_vertical_circulation_maturity(level) is not None
        and _vertical_circulation_component_refs(subject_inventory)
    ):
        required_set.add(StageBaselineRole.VERTICAL_CIRCULATION)
    required = tuple(sorted(required_set))
    requirements = {item.requirement_id: item for item in profile.requirements}
    receipts_by_id: dict[str, list[CheckReceiptEnvelope]] = {}
    for receipt in check_receipts:
        receipts_by_id.setdefault(receipt.check_id, []).append(receipt)
    role_rows: dict[
        StageBaselineRole,
        list[tuple[str, str, str]],
    ] = {role: [] for role in required}
    role_target_refs: dict[StageBaselineRole, set[str]] = {
        role: set() for role in required
    }
    role_source_kinds: dict[StageBaselineRole, set[str]] = {
        role: set() for role in required
    }

    for expected in _expected_checks(
        profile,
        sources,
        subject_digest=subject_digest,
        subject_inventory=subject_inventory,
    ):
        actual_requirement = requirements.get(
            expected.requirement.requirement_id
        )
        actual_receipts = receipts_by_id.get(
            expected.requirement.requirement_id,
            [],
        )
        if actual_requirement != expected.requirement or len(actual_receipts) != 1:
            continue
        actual_receipt = actual_receipts[0]
        if actual_receipt != expected.receipt or actual_receipt.status is not CheckStatus.PASS:
            continue
        single_profile = StageRequirementProfile(
            profile_id=profile.profile_id,
            typology_id=profile.typology_id,
            stage_id=profile.stage_id,
            branch=profile.branch,
            predecessor_state_digest=profile.predecessor_state_digest,
            scope_digest=profile.scope_digest,
            stage_subject_ref=profile.stage_subject_ref,
            requirements=(actual_requirement,),
        )
        single_closure = compile_composite_stage_closure(
            single_profile,
            subject_digest=subject_digest,
            check_receipts=(actual_receipt,),
        )
        if single_closure.status is not StageClosureStatus.SATISFIED:
            continue
        targets_by_role = dict(expected.role_targets)
        for role in expected.roles:
            if role in role_rows:
                role_rows[role].append(
                    (
                        actual_requirement.requirement_id,
                        expected.source_digest,
                        actual_receipt.receipt_digest,
                    )
                )
                role_target_refs[role].update(targets_by_role.get(role, ()))
                role_source_kinds[role].add(expected.source_kind)

    source_basis_refs = {
        ref
        for requirement in profile.requirements
        for ref in requirement.required_source_refs
    }
    authority_basis_refs = {
        ref
        for requirement in profile.requirements
        for ref in requirement.required_authority_refs
    }

    coverage = tuple(
        StageBaselineRoleCoverage(
            role=role,
            requirement_ids=tuple(row[0] for row in rows),
            source_digests=tuple(sorted({row[1] for row in rows})),
            check_receipt_digests=tuple(
                sorted({row[2] for row in rows})
            ),
        )
        for role, rows in sorted(role_rows.items())
        if rows
        and (
            sources.is_legacy_read_only
            or _required_source_kinds_for_role(
                level,
                role,
                include_control=not subject_inventory.is_legacy_read_only,
            ).issubset(
                role_source_kinds[role]
            )
        )
        and _inventory_covers_role(
            subject_inventory,
            role=role,
            covered_target_refs=role_target_refs[role],
            source_basis_refs=source_basis_refs,
            authority_basis_refs=authority_basis_refs,
        )
    )
    covered = {item.role for item in coverage}
    missing = tuple(sorted(set(required) - covered))
    return StageBaselineCoverageReceipt(
        profile_id=profile.profile_id,
        profile_digest=profile.profile_digest,
        branch=profile.branch,
        stage_id=profile.stage_id,
        level=level,
        stage_subject_inventory_digest=inventory_digest,
        required_roles=required,
        coverage=coverage,
        missing_roles=missing,
        status=(
            StageBaselineStatus.SATISFIED
            if not missing
            else StageBaselineStatus.OPEN
        ),
    )


__all__ = [
    "BASELINE_LEVEL_ROLES",
    "DESIGN_PHASE_BASELINE_LEVEL",
    "CadReadbackBaselineSource",
    "ComponentLineageBaselineSource",
    "MaterialBindingBaselineSource",
    "RelationRealizationBaselineSource",
    "RelationTopologyBaselineSource",
    "SpatialLayoutBaselineSource",
    "StageBaselineCoverageReceipt",
    "StageBaselineError",
    "StageBaselineLevel",
    "StageBaselineRole",
    "StageBaselineRoleCoverage",
    "StageBaselineSourceSet",
    "StageBaselineStatus",
    "StageRelationInheritanceBaselineSource",
    "VerticalCirculationBaselineSource",
    "check_vertical_circulation_baseline_maturity",
    "required_vertical_circulation_maturity",
    "baseline_level_for_design_phase",
    "compile_stage_baseline_coverage",
    "derive_stage_baseline_check_receipts",
    "derive_stage_baseline_requirements",
    "derive_stage_requirement_profile",
]
