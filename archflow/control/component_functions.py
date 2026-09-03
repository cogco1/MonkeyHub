"""Fail-closed component function contracts and exhaustive stage ledger.

The module is persistence- and CAD-neutral.  A framework-owned policy expands
function identifiers into exact relationship obligations.  The compiler uses
``StageSubjectInventory.entries`` as its denominator; names and semantic kinds
never create or satisfy a function contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from archflow.contracts.branch import (
    branch_ref_from_dict,
    branch_ref_to_dict,
    require_exact_branch,
)
from archflow.contracts.canonical import canonical_digest, require_sha256
from archflow.contracts.fields import (
    deterministic_refs,
    exact_mapping,
    identifier,
    logical_ref,
    text,
)
from archflow.control.function_diagnostics import (
    FUNCTION_DIAGNOSTIC_PALETTE,
    NO_FUNCTION_CONTRACT,
    FunctionDiagnosticEntry,
    FunctionStatus,
)
from archflow.control.stage_subjects import (
    StageSubjectInventory,
    StageSubjectInventoryEntry,
)
from archflow.project.refs import BranchRef


class ComponentFunctionError(ValueError):
    """A component function artifact is malformed, stale, or incomplete."""


class ComponentFunctionId(StrEnum):
    BE_SUPPORTED = "BE_SUPPORTED"
    SUPPORT_OTHERS = "SUPPORT_OTHERS"
    TRANSFER_LOAD = "TRANSFER_LOAD"
    BE_HOSTED = "BE_HOSTED"
    HOST_OTHERS = "HOST_OTHERS"
    PROVIDE_ACCESS = "PROVIDE_ACCESS"
    ENCLOSE_SPACE = "ENCLOSE_SPACE"
    CONTINUE_ASSEMBLY = "CONTINUE_ASSEMBLY"


class FunctionApplicability(StrEnum):
    REQUIRED = "REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class FunctionMaturity(StrEnum):
    DECLARED = "DECLARED"
    TOPOLOGICAL = "TOPOLOGICAL"
    GEOMETRIC = "GEOMETRIC"
    VERIFIED = "VERIFIED"


class FunctionClaimStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    PASS = "PASS"
    FAIL = "FAIL"


class FunctionEvaluationStatus(StrEnum):
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    PASS = "PASS"
    FAIL = "FAIL"


_MATURITY_ORDER = {
    FunctionMaturity.DECLARED: 0,
    FunctionMaturity.TOPOLOGICAL: 1,
    FunctionMaturity.GEOMETRIC: 2,
    FunctionMaturity.VERIFIED: 3,
}

_AUTHORITY_FIELDS = {
    "design_authority": False,
    "stage_acceptance_authority": False,
    "persistence_authority": False,
    "canonical_write_authority": False,
}


def _sorted_unique_enum_tuple(
    values: object,
    enum_type: type[StrEnum],
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[StrEnum, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, enum_type) for item in values
    ):
        raise TypeError(f"{field} must contain {enum_type.__name__} values")
    normalized = tuple(sorted(values, key=lambda item: item.value))
    if len(normalized) != len(set(normalized)):
        raise ComponentFunctionError(f"{field} contains duplicates")
    if not normalized and not allow_empty:
        raise ComponentFunctionError(f"{field} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class FunctionApplicabilityDecision:
    """Evidence-bound disposition of one policy function for one component."""

    function_id: ComponentFunctionId
    applicability: FunctionApplicability
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    contradiction_refs: tuple[str, ...] = ()

    SCHEMA = "FunctionApplicabilityDecision@1"

    def __post_init__(self) -> None:
        if not isinstance(self.function_id, ComponentFunctionId):
            raise TypeError("function_id must be ComponentFunctionId")
        if not isinstance(self.applicability, FunctionApplicability):
            raise TypeError("applicability must be FunctionApplicability")
        for field in ("evidence_refs", "authority_refs", "contradiction_refs"):
            object.__setattr__(
                self,
                field,
                deterministic_refs(
                    getattr(self, field),
                    f"function applicability {field}",
                    allow_empty=True,
                ),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "function_id": self.function_id.value,
            "applicability": self.applicability.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "contradiction_refs": list(self.contradiction_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionApplicabilityDecision":
        payload = exact_mapping(
            value,
            {
                "schema",
                "function_id",
                "applicability",
                "evidence_refs",
                "authority_refs",
                "contradiction_refs",
            },
            "function applicability decision",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError(
                "unsupported function applicability decision schema"
            )
        for field in ("evidence_refs", "authority_refs", "contradiction_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        return cls(
            function_id=ComponentFunctionId(payload["function_id"]),
            applicability=FunctionApplicability(payload["applicability"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
            contradiction_refs=tuple(payload["contradiction_refs"]),
        )


def _applicability_denominator(
    values: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[FunctionApplicabilityDecision, ...]:
    if not isinstance(values, tuple) or any(
        not isinstance(item, FunctionApplicabilityDecision) for item in values
    ):
        raise TypeError(
            f"{field} must contain FunctionApplicabilityDecision values"
        )
    ordered = tuple(sorted(values, key=lambda item: item.function_id.value))
    function_ids = tuple(item.function_id for item in ordered)
    if len(function_ids) != len(set(function_ids)):
        raise ComponentFunctionError(f"{field} classifies a function more than once")
    expected = tuple(sorted(ComponentFunctionId, key=lambda item: item.value))
    if not ordered and allow_empty:
        return ordered
    if function_ids != expected:
        raise ComponentFunctionError(
            f"{field} must exactly classify every policy function"
        )
    return ordered


@dataclass(frozen=True, slots=True)
class FunctionEndpointRole:
    role: str
    minimum: int
    maximum: int | None
    component_slot: bool = False

    SCHEMA = "FunctionEndpointRole@1"

    def __post_init__(self) -> None:
        identifier(self.role, "function endpoint role")
        if not isinstance(self.minimum, int) or isinstance(self.minimum, bool) or self.minimum < 0:
            raise ComponentFunctionError("endpoint minimum must be a non-negative integer")
        if self.maximum is not None and (
            not isinstance(self.maximum, int)
            or isinstance(self.maximum, bool)
            or self.maximum < self.minimum
        ):
            raise ComponentFunctionError("endpoint maximum is below its minimum")
        if not isinstance(self.component_slot, bool):
            raise TypeError("component_slot must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "role": self.role,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "component_slot": self.component_slot,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionEndpointRole":
        payload = exact_mapping(
            value,
            {"schema", "role", "minimum", "maximum", "component_slot"},
            "function endpoint role",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported function endpoint role schema")
        return cls(
            role=payload["role"],
            minimum=payload["minimum"],
            maximum=payload["maximum"],
            component_slot=payload["component_slot"],
        )


@dataclass(frozen=True, slots=True)
class FunctionObligationSpec:
    function_id: ComponentFunctionId
    obligation_ref: str
    purpose: str
    endpoint_roles: tuple[FunctionEndpointRole, ...]
    required_maturity: FunctionMaturity

    SCHEMA = "FunctionObligationSpec@1"

    def __post_init__(self) -> None:
        if not isinstance(self.function_id, ComponentFunctionId):
            raise TypeError("function_id must be ComponentFunctionId")
        object.__setattr__(
            self,
            "obligation_ref",
            logical_ref(self.obligation_ref, "function obligation_ref"),
        )
        text(self.purpose, "function obligation purpose")
        if not isinstance(self.endpoint_roles, tuple) or not self.endpoint_roles or any(
            not isinstance(item, FunctionEndpointRole) for item in self.endpoint_roles
        ):
            raise TypeError("endpoint_roles must contain FunctionEndpointRole values")
        ordered = tuple(sorted(self.endpoint_roles, key=lambda item: item.role))
        if ordered != self.endpoint_roles:
            raise ComponentFunctionError("endpoint_roles must be sorted by role")
        roles = tuple(item.role for item in ordered)
        if len(roles) != len(set(roles)):
            raise ComponentFunctionError("endpoint_roles contain duplicate roles")
        if sum(item.component_slot for item in ordered) != 1:
            raise ComponentFunctionError("obligation needs exactly one component endpoint role")
        if not isinstance(self.required_maturity, FunctionMaturity):
            raise TypeError("required_maturity must be FunctionMaturity")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "function_id": self.function_id.value,
            "obligation_ref": self.obligation_ref,
            "purpose": self.purpose,
            "endpoint_roles": [item.to_dict() for item in self.endpoint_roles],
            "required_maturity": self.required_maturity.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionObligationSpec":
        payload = exact_mapping(
            value,
            {"schema", "function_id", "obligation_ref", "purpose", "endpoint_roles", "required_maturity"},
            "function obligation spec",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported function obligation spec schema")
        if not isinstance(payload["endpoint_roles"], list):
            raise TypeError("endpoint_roles must be a list")
        return cls(
            function_id=ComponentFunctionId(payload["function_id"]),
            obligation_ref=payload["obligation_ref"],
            purpose=payload["purpose"],
            endpoint_roles=tuple(FunctionEndpointRole.from_dict(item) for item in payload["endpoint_roles"]),
            required_maturity=FunctionMaturity(payload["required_maturity"]),
        )


@dataclass(frozen=True, slots=True)
class ComponentFunctionPolicy:
    policy_id: str
    policy_version: int
    obligations: tuple[FunctionObligationSpec, ...]

    SCHEMA = "ComponentFunctionPolicy@1"

    def __post_init__(self) -> None:
        identifier(self.policy_id, "component function policy_id")
        if not isinstance(self.policy_version, int) or isinstance(self.policy_version, bool) or self.policy_version < 1:
            raise ComponentFunctionError("policy_version must be a positive integer")
        if not isinstance(self.obligations, tuple) or any(
            not isinstance(item, FunctionObligationSpec) for item in self.obligations
        ):
            raise TypeError("obligations must contain FunctionObligationSpec values")
        ordered = tuple(sorted(self.obligations, key=lambda item: item.function_id.value))
        if ordered != self.obligations:
            raise ComponentFunctionError("policy obligations must be sorted by function_id")
        ids = tuple(item.function_id for item in ordered)
        if ids != tuple(sorted(ComponentFunctionId, key=lambda item: item.value)):
            raise ComponentFunctionError("policy must exactly cover supported function IDs")
        refs = tuple(item.obligation_ref for item in ordered)
        if len(refs) != len(set(refs)):
            raise ComponentFunctionError("policy obligation refs must be unique")

    @property
    def policy_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "obligations": [item.to_dict() for item in self.obligations],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "policy_digest": self.policy_digest}

    @classmethod
    def from_dict(cls, value: object) -> "ComponentFunctionPolicy":
        payload = exact_mapping(
            value,
            {"schema", "policy_id", "policy_version", "obligations", "policy_digest", *_AUTHORITY_FIELDS},
            "component function policy",
        )
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported component function policy schema")
        if not isinstance(payload["obligations"], list):
            raise TypeError("policy obligations must be a list")
        result = cls(
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            obligations=tuple(FunctionObligationSpec.from_dict(item) for item in payload["obligations"]),
        )
        if result.to_dict() != payload:
            raise ComponentFunctionError("component function policy digest changed")
        return result

    def spec_for(self, function_id: ComponentFunctionId) -> FunctionObligationSpec:
        return next(item for item in self.obligations if item.function_id is function_id)


def _role(role: str, *, component: bool = False, maximum: int | None = 1) -> FunctionEndpointRole:
    return FunctionEndpointRole(role=role, minimum=1, maximum=maximum, component_slot=component)


def _spec(
    function_id: ComponentFunctionId,
    purpose: str,
    roles: tuple[FunctionEndpointRole, ...],
    maturity: FunctionMaturity,
) -> FunctionObligationSpec:
    return FunctionObligationSpec(
        function_id=function_id,
        obligation_ref=f"function-obligation:component-functions-v1/{function_id.value.lower()}",
        purpose=purpose,
        endpoint_roles=tuple(sorted(roles, key=lambda item: item.role)),
        required_maturity=maturity,
    )


DEFAULT_COMPONENT_FUNCTION_POLICY = ComponentFunctionPolicy(
    policy_id="component-functions-v1",
    policy_version=1,
    obligations=tuple(sorted((
        _spec(ComponentFunctionId.BE_SUPPORTED, "Component is supported by a distinct support endpoint.", (_role("supported_component", component=True), _role("supporting_component")), FunctionMaturity.GEOMETRIC),
        _spec(ComponentFunctionId.SUPPORT_OTHERS, "Component supports at least one distinct dependent endpoint.", (_role("supported_component", maximum=None), _role("supporting_component", component=True)), FunctionMaturity.GEOMETRIC),
        _spec(ComponentFunctionId.TRANSFER_LOAD, "Load has an identified source, transfer component, and receiving sink.", (_role("load_sink"), _role("load_source"), _role("load_transfer_component", component=True)), FunctionMaturity.VERIFIED),
        _spec(ComponentFunctionId.BE_HOSTED, "Component is hosted by a distinct host endpoint.", (_role("host_component"), _role("hosted_component", component=True)), FunctionMaturity.TOPOLOGICAL),
        _spec(ComponentFunctionId.HOST_OTHERS, "Component hosts at least one distinct hosted endpoint.", (_role("host_component", component=True), _role("hosted_component", maximum=None)), FunctionMaturity.TOPOLOGICAL),
        _spec(ComponentFunctionId.PROVIDE_ACCESS, "Access connects explicit from and to endpoints.", (_role("access_from"), _role("access_to"), _role("access_provider", component=True)), FunctionMaturity.TOPOLOGICAL),
        _spec(ComponentFunctionId.ENCLOSE_SPACE, "Component participates in enclosing an explicit space endpoint.", (_role("enclosed_space", maximum=None), _role("enclosure_component", component=True)), FunctionMaturity.GEOMETRIC),
        _spec(ComponentFunctionId.CONTINUE_ASSEMBLY, "Component continues an assembly between predecessor and successor endpoints.", (_role("assembly_component", component=True), _role("predecessor"), _role("successor")), FunctionMaturity.TOPOLOGICAL),
    ), key=lambda item: item.function_id.value)),
)

SUPPORTED_COMPONENT_FUNCTION_POLICIES = MappingProxyType(
    {DEFAULT_COMPONENT_FUNCTION_POLICY.policy_id: DEFAULT_COMPONENT_FUNCTION_POLICY}
)


@dataclass(frozen=True, slots=True)
class FunctionEndpointBinding:
    role: str
    endpoint_refs: tuple[str, ...]

    SCHEMA = "FunctionEndpointBinding@1"

    def __post_init__(self) -> None:
        identifier(self.role, "function endpoint binding role")
        object.__setattr__(self, "endpoint_refs", deterministic_refs(self.endpoint_refs, "function endpoint refs", allow_empty=True))

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "role": self.role, "endpoint_refs": list(self.endpoint_refs)}

    @classmethod
    def from_dict(cls, value: object) -> "FunctionEndpointBinding":
        payload = exact_mapping(value, {"schema", "role", "endpoint_refs"}, "function endpoint binding")
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported endpoint binding schema")
        if not isinstance(payload["endpoint_refs"], list):
            raise TypeError("endpoint_refs must be a list")
        return cls(role=payload["role"], endpoint_refs=tuple(payload["endpoint_refs"]))


@dataclass(frozen=True, slots=True)
class FunctionObligationClaim:
    obligation_ref: str
    endpoint_bindings: tuple[FunctionEndpointBinding, ...]
    maturity: FunctionMaturity
    status: FunctionClaimStatus
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    contradiction_refs: tuple[str, ...] = ()

    SCHEMA = "FunctionObligationClaim@1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_ref", logical_ref(self.obligation_ref, "claimed obligation_ref"))
        if not isinstance(self.endpoint_bindings, tuple) or any(not isinstance(item, FunctionEndpointBinding) for item in self.endpoint_bindings):
            raise TypeError("endpoint_bindings must contain FunctionEndpointBinding values")
        bindings = tuple(sorted(self.endpoint_bindings, key=lambda item: item.role))
        if len(bindings) != len({item.role for item in bindings}):
            raise ComponentFunctionError("obligation claim duplicates an endpoint role")
        object.__setattr__(self, "endpoint_bindings", bindings)
        if not isinstance(self.maturity, FunctionMaturity):
            raise TypeError("maturity must be FunctionMaturity")
        if not isinstance(self.status, FunctionClaimStatus):
            raise TypeError("status must be FunctionClaimStatus")
        for field in ("evidence_refs", "authority_refs", "contradiction_refs"):
            object.__setattr__(self, field, deterministic_refs(getattr(self, field), f"function claim {field}", allow_empty=True))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "obligation_ref": self.obligation_ref,
            "endpoint_bindings": [item.to_dict() for item in self.endpoint_bindings],
            "maturity": self.maturity.value,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "contradiction_refs": list(self.contradiction_refs),
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionObligationClaim":
        payload = exact_mapping(value, {"schema", "obligation_ref", "endpoint_bindings", "maturity", "status", "evidence_refs", "authority_refs", "contradiction_refs"}, "function obligation claim")
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported obligation claim schema")
        for field in ("endpoint_bindings", "evidence_refs", "authority_refs", "contradiction_refs"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        return cls(
            obligation_ref=payload["obligation_ref"],
            endpoint_bindings=tuple(FunctionEndpointBinding.from_dict(item) for item in payload["endpoint_bindings"]),
            maturity=FunctionMaturity(payload["maturity"]),
            status=FunctionClaimStatus(payload["status"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]),
            contradiction_refs=tuple(payload["contradiction_refs"]),
        )


@dataclass(frozen=True, slots=True)
class ComponentFunctionContract:
    contract_id: str
    branch: BranchRef
    stage_id: str
    subject_inventory_digest: str
    component_ref: str
    component_digest: str
    applicability_decisions: tuple[FunctionApplicabilityDecision, ...]
    claims: tuple[FunctionObligationClaim, ...]

    SCHEMA = "ComponentFunctionContract@2"

    def __post_init__(self) -> None:
        identifier(self.contract_id, "component function contract_id")
        require_exact_branch(self.branch)
        identifier(self.stage_id, "component function contract stage_id")
        object.__setattr__(self, "subject_inventory_digest", require_sha256(self.subject_inventory_digest, "subject_inventory_digest"))
        object.__setattr__(self, "component_ref", logical_ref(self.component_ref, "component function component_ref"))
        object.__setattr__(self, "component_digest", require_sha256(self.component_digest, "component_digest"))
        decisions = _applicability_denominator(
            self.applicability_decisions,
            "applicability_decisions",
        )
        object.__setattr__(self, "applicability_decisions", decisions)
        if not isinstance(self.claims, tuple) or any(not isinstance(item, FunctionObligationClaim) for item in self.claims):
            raise TypeError("claims must contain FunctionObligationClaim values")
        claims = tuple(sorted(self.claims, key=lambda item: item.obligation_ref))
        if len(claims) != len({item.obligation_ref for item in claims}):
            raise ComponentFunctionError("function contract duplicates an obligation claim")
        required_obligation_refs = {
            DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id).obligation_ref
            for function_id in self.required_functions
        }
        if any(
            item.obligation_ref not in required_obligation_refs for item in claims
        ):
            raise ComponentFunctionError(
                "function claims may correspond only to REQUIRED functions"
            )
        object.__setattr__(self, "claims", claims)

    @property
    def required_functions(self) -> tuple[ComponentFunctionId, ...]:
        return tuple(
            item.function_id
            for item in self.applicability_decisions
            if item.applicability is FunctionApplicability.REQUIRED
        )

    @property
    def not_applicable_functions(self) -> tuple[ComponentFunctionId, ...]:
        return tuple(
            item.function_id
            for item in self.applicability_decisions
            if item.applicability is FunctionApplicability.NOT_APPLICABLE
        )

    @property
    def unknown_functions(self) -> tuple[ComponentFunctionId, ...]:
        return tuple(
            item.function_id
            for item in self.applicability_decisions
            if item.applicability is FunctionApplicability.UNKNOWN
        )

    @property
    def contract_ref(self) -> str:
        return f"function-contract:{self.contract_id}"

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "contract_id": self.contract_id,
            "branch": branch_ref_to_dict(self.branch),
            "stage_id": self.stage_id,
            "subject_inventory_digest": self.subject_inventory_digest,
            "component_ref": self.component_ref,
            "component_digest": self.component_digest,
            "applicability_decisions": [
                item.to_dict() for item in self.applicability_decisions
            ],
            "claims": [item.to_dict() for item in self.claims],
            **_AUTHORITY_FIELDS,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "contract_digest": self.contract_digest}

    @classmethod
    def from_dict(cls, value: object) -> "ComponentFunctionContract":
        payload = exact_mapping(value, {"schema", "contract_id", "branch", "stage_id", "subject_inventory_digest", "component_ref", "component_digest", "applicability_decisions", "claims", "contract_digest", *_AUTHORITY_FIELDS}, "component function contract")
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported component function contract schema")
        for field in ("applicability_decisions", "claims"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        result = cls(
            contract_id=payload["contract_id"],
            branch=branch_ref_from_dict(payload["branch"]),
            stage_id=payload["stage_id"],
            subject_inventory_digest=payload["subject_inventory_digest"],
            component_ref=payload["component_ref"],
            component_digest=payload["component_digest"],
            applicability_decisions=tuple(
                FunctionApplicabilityDecision.from_dict(item)
                for item in payload["applicability_decisions"]
            ),
            claims=tuple(FunctionObligationClaim.from_dict(item) for item in payload["claims"]),
        )
        if result.to_dict() != payload:
            raise ComponentFunctionError("component function contract digest changed")
        return result


@dataclass(frozen=True, slots=True)
class FunctionObligationEvaluation:
    function_id: ComponentFunctionId
    obligation_ref: str
    purpose: str
    endpoint_roles: tuple[FunctionEndpointRole, ...]
    required_maturity: FunctionMaturity
    status: FunctionEvaluationStatus
    endpoint_bindings: tuple[FunctionEndpointBinding, ...]
    actual_maturity: FunctionMaturity | None
    evidence_refs: tuple[str, ...]
    authority_refs: tuple[str, ...]
    contradiction_refs: tuple[str, ...]
    failure_codes: tuple[str, ...]

    SCHEMA = "FunctionObligationEvaluation@1"

    def __post_init__(self) -> None:
        if not isinstance(self.function_id, ComponentFunctionId):
            raise TypeError("function_id must be ComponentFunctionId")
        object.__setattr__(self, "obligation_ref", logical_ref(self.obligation_ref, "evaluation obligation_ref"))
        text(self.purpose, "evaluation purpose")
        if not isinstance(self.endpoint_roles, tuple) or any(not isinstance(item, FunctionEndpointRole) for item in self.endpoint_roles):
            raise TypeError("endpoint_roles must contain FunctionEndpointRole values")
        if not isinstance(self.required_maturity, FunctionMaturity):
            raise TypeError("required_maturity must be FunctionMaturity")
        if not isinstance(self.status, FunctionEvaluationStatus):
            raise TypeError("status must be FunctionEvaluationStatus")
        if not isinstance(self.endpoint_bindings, tuple) or any(not isinstance(item, FunctionEndpointBinding) for item in self.endpoint_bindings):
            raise TypeError("endpoint_bindings must contain FunctionEndpointBinding values")
        if self.actual_maturity is not None and not isinstance(self.actual_maturity, FunctionMaturity):
            raise TypeError("actual_maturity must be FunctionMaturity or None")
        for field in ("evidence_refs", "authority_refs", "contradiction_refs"):
            object.__setattr__(self, field, deterministic_refs(getattr(self, field), f"evaluation {field}", allow_empty=True))
        if not isinstance(self.failure_codes, tuple):
            raise TypeError("failure_codes must be a tuple")
        codes = tuple(sorted(self.failure_codes))
        if len(codes) != len(set(codes)) or any(identifier(item, "failure code") != item for item in codes):
            raise ComponentFunctionError("failure_codes must be sorted unique identifiers")
        object.__setattr__(self, "failure_codes", codes)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "function_id": self.function_id.value,
            "obligation_ref": self.obligation_ref,
            "purpose": self.purpose,
            "endpoint_roles": [item.to_dict() for item in self.endpoint_roles],
            "required_maturity": self.required_maturity.value,
            "status": self.status.value,
            "endpoint_bindings": [item.to_dict() for item in self.endpoint_bindings],
            "actual_maturity": None if self.actual_maturity is None else self.actual_maturity.value,
            "evidence_refs": list(self.evidence_refs),
            "authority_refs": list(self.authority_refs),
            "contradiction_refs": list(self.contradiction_refs),
            "failure_codes": list(self.failure_codes),
        }

    @classmethod
    def from_dict(cls, value: object) -> "FunctionObligationEvaluation":
        payload = exact_mapping(value, {"schema", "function_id", "obligation_ref", "purpose", "endpoint_roles", "required_maturity", "status", "endpoint_bindings", "actual_maturity", "evidence_refs", "authority_refs", "contradiction_refs", "failure_codes"}, "function obligation evaluation")
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported obligation evaluation schema")
        for field in ("endpoint_roles", "endpoint_bindings", "evidence_refs", "authority_refs", "contradiction_refs", "failure_codes"):
            if not isinstance(payload[field], list):
                raise TypeError(f"{field} must be a list")
        return cls(
            function_id=ComponentFunctionId(payload["function_id"]), obligation_ref=payload["obligation_ref"], purpose=payload["purpose"],
            endpoint_roles=tuple(FunctionEndpointRole.from_dict(item) for item in payload["endpoint_roles"]), required_maturity=FunctionMaturity(payload["required_maturity"]),
            status=FunctionEvaluationStatus(payload["status"]), endpoint_bindings=tuple(FunctionEndpointBinding.from_dict(item) for item in payload["endpoint_bindings"]),
            actual_maturity=None if payload["actual_maturity"] is None else FunctionMaturity(payload["actual_maturity"]), evidence_refs=tuple(payload["evidence_refs"]),
            authority_refs=tuple(payload["authority_refs"]), contradiction_refs=tuple(payload["contradiction_refs"]), failure_codes=tuple(payload["failure_codes"]),
        )


def _applicability_failure_codes(
    decisions: tuple[FunctionApplicabilityDecision, ...],
) -> set[str]:
    failures: set[str] = set()
    for decision in decisions:
        if decision.applicability is FunctionApplicability.UNKNOWN:
            continue
        if not decision.evidence_refs:
            failures.add("APPLICABILITY_EVIDENCE_INCOMPLETE")
        if not decision.authority_refs:
            failures.add("APPLICABILITY_AUTHORITY_INCOMPLETE")
        if decision.contradiction_refs:
            failures.add("APPLICABILITY_CONTRADICTORY_EVIDENCE")
    return failures


@dataclass(frozen=True, slots=True)
class ComponentFunctionLedgerRow:
    component_ref: str
    component_digest: str
    contract_ref: str
    contract_digest: str | None
    applicability_decisions: tuple[FunctionApplicabilityDecision, ...]
    evaluations: tuple[FunctionObligationEvaluation, ...]
    status: FunctionStatus
    failure_codes: tuple[str, ...]

    SCHEMA = "ComponentFunctionLedgerRow@2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "component_ref", logical_ref(self.component_ref, "ledger row component_ref"))
        object.__setattr__(self, "component_digest", require_sha256(self.component_digest, "ledger row component_digest"))
        if self.contract_ref != NO_FUNCTION_CONTRACT:
            object.__setattr__(self, "contract_ref", logical_ref(self.contract_ref, "ledger row contract_ref"))
            if self.contract_digest is None:
                raise ComponentFunctionError("contract row needs contract_digest")
            object.__setattr__(self, "contract_digest", require_sha256(self.contract_digest, "contract_digest"))
        elif self.contract_digest is not None:
            raise ComponentFunctionError("orphan row cannot carry contract_digest")
        decisions = _applicability_denominator(
            self.applicability_decisions,
            "row applicability_decisions",
            allow_empty=self.contract_ref == NO_FUNCTION_CONTRACT,
        )
        object.__setattr__(self, "applicability_decisions", decisions)
        if not isinstance(self.evaluations, tuple) or any(not isinstance(item, FunctionObligationEvaluation) for item in self.evaluations):
            raise TypeError("evaluations must contain FunctionObligationEvaluation values")
        evaluations = tuple(sorted(self.evaluations, key=lambda item: item.obligation_ref))
        if len(evaluations) != len({item.obligation_ref for item in evaluations}):
            raise ComponentFunctionError("ledger row duplicates an obligation")
        object.__setattr__(self, "evaluations", evaluations)
        if not isinstance(self.status, FunctionStatus):
            raise TypeError("status must be FunctionStatus")
        codes = tuple(sorted(self.failure_codes))
        if len(codes) != len(set(codes)) or any(identifier(item, "row failure code") != item for item in codes):
            raise ComponentFunctionError("row failure_codes must be sorted unique identifiers")
        object.__setattr__(self, "failure_codes", codes)
        evaluation_ids = tuple(item.function_id for item in evaluations)
        if evaluation_ids != tuple(
            sorted(self.required_functions, key=lambda item: item.value)
        ):
            raise ComponentFunctionError(
                "ledger row evaluations do not exactly cover required functions"
            )
        if self.status is FunctionStatus.FUNCTION_ORPHAN:
            if (
                self.contract_ref != NO_FUNCTION_CONTRACT
                or self.applicability_decisions
                or self.evaluations
                or self.failure_codes != ("MISSING_FUNCTION_CONTRACT",)
            ):
                raise ComponentFunctionError(
                    "FUNCTION_ORPHAN row is not an exact missing-contract row"
                )
        else:
            if self.contract_ref == NO_FUNCTION_CONTRACT:
                raise ComponentFunctionError(
                    "only FUNCTION_ORPHAN may use the NONE contract"
                )
            expected_failures = _applicability_failure_codes(
                self.applicability_decisions
            )
            if any(
                item.status
                in {
                    FunctionEvaluationStatus.FAIL,
                    FunctionEvaluationStatus.MISSING,
                }
                for item in evaluations
            ):
                expected_failures.add("OBLIGATION_FAILED")
            expected_status = (
                FunctionStatus.FAIL
                if expected_failures
                else (
                    FunctionStatus.OPEN
                    if self.unknown_functions
                    or any(
                        item.status is FunctionEvaluationStatus.UNKNOWN
                        for item in evaluations
                    )
                    else FunctionStatus.SATISFIED
                )
            )
            if self.status is not expected_status or set(self.failure_codes) != expected_failures:
                raise ComponentFunctionError(
                    "ledger row status differs from obligation evaluations"
                )

    @property
    def required_functions(self) -> tuple[ComponentFunctionId, ...]:
        return tuple(
            item.function_id
            for item in self.applicability_decisions
            if item.applicability is FunctionApplicability.REQUIRED
        )

    @property
    def not_applicable_functions(self) -> tuple[ComponentFunctionId, ...]:
        return tuple(
            item.function_id
            for item in self.applicability_decisions
            if item.applicability is FunctionApplicability.NOT_APPLICABLE
        )

    @property
    def unknown_functions(self) -> tuple[ComponentFunctionId, ...]:
        return tuple(
            item.function_id
            for item in self.applicability_decisions
            if item.applicability is FunctionApplicability.UNKNOWN
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA, "component_ref": self.component_ref, "component_digest": self.component_digest,
            "contract_ref": self.contract_ref, "contract_digest": self.contract_digest,
            "applicability_decisions": [item.to_dict() for item in self.applicability_decisions],
            "evaluations": [item.to_dict() for item in self.evaluations], "status": self.status.value, "failure_codes": list(self.failure_codes),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ComponentFunctionLedgerRow":
        payload = exact_mapping(value, {"schema", "component_ref", "component_digest", "contract_ref", "contract_digest", "applicability_decisions", "evaluations", "status", "failure_codes"}, "component function ledger row")
        if payload["schema"] != cls.SCHEMA:
            raise ComponentFunctionError("unsupported component function ledger row schema")
        for field in ("applicability_decisions", "evaluations", "failure_codes"):
            if not isinstance(payload[field], list): raise TypeError(f"{field} must be a list")
        return cls(component_ref=payload["component_ref"], component_digest=payload["component_digest"], contract_ref=payload["contract_ref"], contract_digest=payload["contract_digest"],
            applicability_decisions=tuple(FunctionApplicabilityDecision.from_dict(item) for item in payload["applicability_decisions"]),
            evaluations=tuple(FunctionObligationEvaluation.from_dict(item) for item in payload["evaluations"]), status=FunctionStatus(payload["status"]), failure_codes=tuple(payload["failure_codes"]))


@dataclass(frozen=True, slots=True)
class ComponentFunctionLedger:
    ledger_id: str
    branch: BranchRef
    stage_id: str
    subject_inventory_digest: str
    policy_id: str
    policy_digest: str
    rows: tuple[ComponentFunctionLedgerRow, ...]

    SCHEMA = "ComponentFunctionLedger@2"

    def __post_init__(self) -> None:
        identifier(self.ledger_id, "component function ledger_id")
        require_exact_branch(self.branch)
        identifier(self.stage_id, "component function ledger stage_id")
        object.__setattr__(self, "subject_inventory_digest", require_sha256(self.subject_inventory_digest, "ledger subject_inventory_digest"))
        identifier(self.policy_id, "component function ledger policy_id")
        object.__setattr__(self, "policy_digest", require_sha256(self.policy_digest, "policy_digest"))
        supported = SUPPORTED_COMPONENT_FUNCTION_POLICIES.get(self.policy_id)
        if supported is None or self.policy_digest != supported.policy_digest:
            raise ComponentFunctionError(
                "ledger names an unsupported or drifted function policy"
            )
        if not isinstance(self.rows, tuple) or not self.rows or any(not isinstance(item, ComponentFunctionLedgerRow) for item in self.rows):
            raise TypeError("rows must contain ComponentFunctionLedgerRow values")
        ordered = tuple(sorted(self.rows, key=lambda item: item.component_ref))
        if ordered != self.rows or len(ordered) != len({item.component_ref for item in ordered}):
            raise ComponentFunctionError("ledger rows must be sorted with unique components")

    @property
    def ledger_ref(self) -> str:
        return f"function-ledger:{self.ledger_id}"

    @property
    def ledger_digest(self) -> str:
        return canonical_digest(self._content_dict())

    def _content_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "ledger_id": self.ledger_id, "branch": branch_ref_to_dict(self.branch), "stage_id": self.stage_id,
            "subject_inventory_digest": self.subject_inventory_digest, "policy_id": self.policy_id, "policy_digest": self.policy_digest,
            "rows": [item.to_dict() for item in self.rows], **_AUTHORITY_FIELDS}

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "ledger_digest": self.ledger_digest}

    @classmethod
    def from_dict(cls, value: object) -> "ComponentFunctionLedger":
        payload = exact_mapping(value, {"schema", "ledger_id", "branch", "stage_id", "subject_inventory_digest", "policy_id", "policy_digest", "rows", "ledger_digest", *_AUTHORITY_FIELDS}, "component function ledger")
        if payload["schema"] != cls.SCHEMA: raise ComponentFunctionError("unsupported component function ledger schema")
        if not isinstance(payload["rows"], list): raise TypeError("ledger rows must be a list")
        result = cls(ledger_id=payload["ledger_id"], branch=branch_ref_from_dict(payload["branch"]), stage_id=payload["stage_id"],
            subject_inventory_digest=payload["subject_inventory_digest"], policy_id=payload["policy_id"], policy_digest=payload["policy_digest"],
            rows=tuple(ComponentFunctionLedgerRow.from_dict(item) for item in payload["rows"]))
        if result.to_dict() != payload: raise ComponentFunctionError("component function ledger digest changed")
        return result


def _evaluate_claim(spec: FunctionObligationSpec, claim: FunctionObligationClaim | None, component_ref: str) -> FunctionObligationEvaluation:
    if claim is None:
        return FunctionObligationEvaluation(spec.function_id, spec.obligation_ref, spec.purpose, spec.endpoint_roles, spec.required_maturity,
            FunctionEvaluationStatus.MISSING, (), None, (), (), (), ("MISSING_OBLIGATION",))
    failures: set[str] = set()
    expected_roles = {item.role: item for item in spec.endpoint_roles}
    actual_roles = {item.role: item for item in claim.endpoint_bindings}
    if set(actual_roles) != set(expected_roles): failures.add("ENDPOINT_ROLE_MISMATCH")
    for role_name, role in expected_roles.items():
        refs = actual_roles.get(role_name, FunctionEndpointBinding(role_name, ())).endpoint_refs
        if len(refs) < role.minimum or (role.maximum is not None and len(refs) > role.maximum): failures.add("ENDPOINT_CARDINALITY")
        if role.component_slot and refs != (component_ref,): failures.add("COMPONENT_ENDPOINT_MISMATCH")
        if not role.component_slot and component_ref in refs: failures.add("SELF_RELATION")
    if _MATURITY_ORDER[claim.maturity] < _MATURITY_ORDER[spec.required_maturity]: failures.add("MATURITY_INCOMPLETE")
    if not claim.evidence_refs: failures.add("EVIDENCE_INCOMPLETE")
    if not claim.authority_refs: failures.add("AUTHORITY_INCOMPLETE")
    if claim.contradiction_refs: failures.add("CONTRADICTORY_EVIDENCE")
    if claim.status is FunctionClaimStatus.FAIL: failures.add("CLAIM_FAILED")
    status = FunctionEvaluationStatus.FAIL if failures else (FunctionEvaluationStatus.UNKNOWN if claim.status is FunctionClaimStatus.UNKNOWN else FunctionEvaluationStatus.PASS)
    return FunctionObligationEvaluation(spec.function_id, spec.obligation_ref, spec.purpose, spec.endpoint_roles, spec.required_maturity, status,
        claim.endpoint_bindings, claim.maturity, claim.evidence_refs, claim.authority_refs, claim.contradiction_refs, tuple(sorted(failures)))


def _compile_row(entry: StageSubjectInventoryEntry, contract: ComponentFunctionContract | None, policy: ComponentFunctionPolicy) -> ComponentFunctionLedgerRow:
    if contract is None:
        return ComponentFunctionLedgerRow(entry.identity_ref, entry.component_digest, NO_FUNCTION_CONTRACT, None, (), (), FunctionStatus.FUNCTION_ORPHAN, ("MISSING_FUNCTION_CONTRACT",))
    expected_specs = tuple(policy.spec_for(item) for item in contract.required_functions)
    expected_refs = {item.obligation_ref for item in expected_specs}
    claim_by_ref = {item.obligation_ref: item for item in contract.claims}
    foreign = set(claim_by_ref) - expected_refs
    if foreign:
        raise ComponentFunctionError("function contract claims an undeclared or wrong-policy obligation")
    evaluations = tuple(sorted((_evaluate_claim(spec, claim_by_ref.get(spec.obligation_ref), entry.identity_ref) for spec in expected_specs), key=lambda item: item.obligation_ref))
    row_failures = _applicability_failure_codes(
        contract.applicability_decisions
    )
    if any(item.status in {FunctionEvaluationStatus.FAIL, FunctionEvaluationStatus.MISSING} for item in evaluations): row_failures.add("OBLIGATION_FAILED")
    if row_failures: status = FunctionStatus.FAIL
    elif contract.unknown_functions or any(item.status is FunctionEvaluationStatus.UNKNOWN for item in evaluations): status = FunctionStatus.OPEN
    else: status = FunctionStatus.SATISFIED
    return ComponentFunctionLedgerRow(entry.identity_ref, entry.component_digest, contract.contract_ref, contract.contract_digest,
        contract.applicability_decisions, evaluations, status, tuple(sorted(row_failures)))


def compile_component_function_ledger(*, ledger_id: str, inventory: StageSubjectInventory, contracts: tuple[ComponentFunctionContract, ...], policy: ComponentFunctionPolicy = DEFAULT_COMPONENT_FUNCTION_POLICY) -> ComponentFunctionLedger:
    """Compile one ledger row for every exact stage subject inventory entry."""
    if not isinstance(inventory, StageSubjectInventory): raise TypeError("inventory must be StageSubjectInventory")
    if not isinstance(contracts, tuple) or any(not isinstance(item, ComponentFunctionContract) for item in contracts): raise TypeError("contracts must contain ComponentFunctionContract values")
    supported = SUPPORTED_COMPONENT_FUNCTION_POLICIES.get(policy.policy_id)
    if supported is None or policy != supported: raise ComponentFunctionError("unsupported or drifted component function policy")
    entries = {item.identity_ref: item for item in inventory.entries}
    by_component: dict[str, ComponentFunctionContract] = {}
    for contract in contracts:
        if contract.component_ref in by_component: raise ComponentFunctionError("duplicate component function contract")
        entry = entries.get(contract.component_ref)
        if entry is None: raise ComponentFunctionError("function contract names a foreign inventory component")
        if contract.branch != inventory.branch or contract.stage_id != inventory.stage_id:
            raise ComponentFunctionError("function contract crossed exact branch or stage")
        if contract.subject_inventory_digest != inventory.inventory_digest:
            raise ComponentFunctionError("function contract subject inventory is stale")
        if contract.component_digest != entry.component_digest:
            raise ComponentFunctionError("function contract component digest is stale")
        by_component[contract.component_ref] = contract
    if (
        len(by_component) == len(entries)
        and by_component
        and all(
            all(
                decision.applicability
                is FunctionApplicability.NOT_APPLICABLE
                for decision in contract.applicability_decisions
            )
            for contract in by_component.values()
        )
    ):
        raise ComponentFunctionError("all components cannot be NOT_APPLICABLE")
    rows = tuple(sorted((_compile_row(entry, by_component.get(entry.identity_ref), policy) for entry in inventory.entries), key=lambda item: item.component_ref))
    if tuple(item.component_ref for item in rows) != tuple(sorted(entries)):
        raise ComponentFunctionError("ledger denominator differs from stage subject inventory")
    return ComponentFunctionLedger(ledger_id, inventory.branch, inventory.stage_id, inventory.inventory_digest, policy.policy_id, policy.policy_digest, rows)


def compile_function_diagnostic_entries(*, ledger: ComponentFunctionLedger, inventory: StageSubjectInventory, stage_claim_ref: str) -> tuple[FunctionDiagnosticEntry, ...]:
    """Mechanically project ledger status; this never writes to a CAD system."""
    if not isinstance(ledger, ComponentFunctionLedger): raise TypeError("ledger must be ComponentFunctionLedger")
    if not isinstance(inventory, StageSubjectInventory): raise TypeError("inventory must be StageSubjectInventory")
    if ledger.branch != inventory.branch or ledger.stage_id != inventory.stage_id or ledger.subject_inventory_digest != inventory.inventory_digest:
        raise ComponentFunctionError("function ledger crossed or became stale against inventory")
    entries = {item.identity_ref: item for item in inventory.entries}
    if tuple(item.component_ref for item in ledger.rows) != tuple(sorted(entries)):
        raise ComponentFunctionError("function ledger does not exhaust inventory entries")
    result = []
    for row in ledger.rows:
        inventory_entry = entries[row.component_ref]
        if row.component_digest != inventory_entry.component_digest: raise ComponentFunctionError("function ledger component became stale")
        palette = FUNCTION_DIAGNOSTIC_PALETTE[row.status]
        result.append(FunctionDiagnosticEntry(component_ref=row.component_ref, geometry_object_ids=inventory_entry.geometry_object_ids,
            function_ledger_ref=ledger.ledger_ref, function_contract_ref=row.contract_ref, stage_claim_ref=stage_claim_ref,
            status=row.status, diagnostic_color=None if palette is None else palette.value))
    return tuple(result)


__all__ = [
    "DEFAULT_COMPONENT_FUNCTION_POLICY", "SUPPORTED_COMPONENT_FUNCTION_POLICIES", "ComponentFunctionContract", "ComponentFunctionError",
    "ComponentFunctionId", "ComponentFunctionLedger", "ComponentFunctionLedgerRow", "ComponentFunctionPolicy", "FunctionApplicability",
    "FunctionApplicabilityDecision", "FunctionClaimStatus", "FunctionEndpointBinding", "FunctionEndpointRole", "FunctionEvaluationStatus", "FunctionMaturity",
    "FunctionObligationClaim", "FunctionObligationEvaluation", "FunctionObligationSpec", "compile_component_function_ledger", "compile_function_diagnostic_entries",
]
