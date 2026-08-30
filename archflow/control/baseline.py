"""Framework minimum physical checks for building stage exits.

Roles are credited only when an exact stage requirement was derived from a
typed validator input and the deterministic validator/bridge receipt is
present in the stage receipt set.  Checker names and caller-authored summary
booleans therefore cannot manufacture baseline coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from archflow.contracts.branch import branch_ref_from_dict, branch_ref_to_dict
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import exact_mapping, identifier
from archflow.control.check_requirements import (
    assembly_stage_requirement,
    cad_readback_stage_requirement,
    component_lineage_stage_requirement,
    material_binding_stage_requirement,
    spatial_layout_stage_requirement,
)
from archflow.control.requirements import (
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
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archflow.validation.spatial import (
    SpatialValidationInput,
    validate_spatial_layout,
)

if TYPE_CHECKING:
    from archflow.control.stage_subjects import StageSubjectInventory


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
    StageBaselineLevel.COORDINATED: frozenset(StageBaselineRole),
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

    SCHEMA = "StageBaselineSourceSet@1"

    def __post_init__(self) -> None:
        for field, expected in (
            ("component_lineage", ComponentLineageBaselineSource),
            ("spatial_layout", SpatialLayoutBaselineSource),
            ("assembly", AssemblyProfile),
            ("material_binding", MaterialBindingBaselineSource),
            ("cad_readback", CadReadbackBaselineSource),
        ):
            object.__setattr__(
                self,
                field,
                _typed_tuple(getattr(self, field), expected, field),
            )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
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
        payload = exact_mapping(
            value,
            {
                "schema",
                "component_lineage",
                "spatial_layout",
                "assembly",
                "material_binding",
                "cad_readback",
                "source_set_digest",
            },
            "stage baseline source set",
        )
        if payload["schema"] != cls.SCHEMA:
            raise StageBaselineError(
                "unsupported stage baseline source set schema"
            )
        for field in (
            "component_lineage",
            "spatial_layout",
            "assembly",
            "material_binding",
            "cad_readback",
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
        )
        if result.to_dict() != payload:
            raise StageBaselineError(
                "stage baseline source set digest changed"
            )
        return result


@dataclass(frozen=True, slots=True)
class _ExpectedBaselineCheck:
    requirement: StageCheckRequirement
    receipt: CheckReceiptEnvelope
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


def _expected_checks(
    profile: StageRequirementProfile,
    sources: StageBaselineSourceSet,
    *,
    subject_digest: str,
) -> tuple[_ExpectedBaselineCheck, ...]:
    checks: list[_ExpectedBaselineCheck] = []
    for source in sources.component_lineage:
        checks.append(
            _ExpectedBaselineCheck(
                requirement=component_lineage_stage_requirement(source.profile),
                receipt=bridge_component_lineage_receipt(
                    source.profile,
                    source.source_receipt,
                    stage_subject_digest=subject_digest,
                ),
                roles=(StageBaselineRole.COMPONENT_LINEAGE,),
                role_targets=(
                    (
                        StageBaselineRole.COMPONENT_LINEAGE,
                        tuple(
                            sorted(
                                {
                                    f"component:{operation.ref.component_id}"
                                    for operation in source.source_receipt.predecessor_operations
                                }
                            )
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
                roles=(StageBaselineRole.SPATIAL_ENVELOPE,),
                role_targets=(
                    (
                        StageBaselineRole.SPATIAL_ENVELOPE,
                        tuple(
                            sorted(
                                f"component:{component_id}"
                                for component_id in spatial_input.required_component_ids
                            )
                        ),
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
        expected_roles = tuple(sorted(BASELINE_LEVEL_ROLES[self.level]))
        if self.required_roles != expected_roles:
            raise StageBaselineError("required_roles do not match baseline level")
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

    required = tuple(sorted(BASELINE_LEVEL_ROLES[level]))
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

    for expected in _expected_checks(
        profile,
        sources,
        subject_digest=subject_digest,
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
            source_digests=tuple(row[1] for row in rows),
            check_receipt_digests=tuple(row[2] for row in rows),
        )
        for role, rows in sorted(role_rows.items())
        if rows
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
    "SpatialLayoutBaselineSource",
    "StageBaselineCoverageReceipt",
    "StageBaselineError",
    "StageBaselineLevel",
    "StageBaselineRole",
    "StageBaselineRoleCoverage",
    "StageBaselineSourceSet",
    "StageBaselineStatus",
    "baseline_level_for_design_phase",
    "compile_stage_baseline_coverage",
]
