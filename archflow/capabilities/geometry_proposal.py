"""Record-driven, provider-neutral geometry proposal authoring.

The capability binds model-authored neutral geometry to an exact candidate
projection and a persisted spatial option.  It may compile and retain proposal
rounds, but it has no hard-gate, acceptance, canonical-write, or platform
mutation authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.project import (
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    ProjectVersionRef,
    RecordSink,
    RunRef,
    require_destination,
)
from archflow.runtime.geometry_compiler import (
    CompiledGeometryProgram,
    compile_geometry_program,
)
from archflow.state import SpatialOptionProposal
from archflow.state.candidate_program import CandidateProgramProjection
from archflow.state.geometry_program import (
    AffineTransform,
    AssemblyKind,
    AssemblyMember,
    AssemblyRole,
    AssetReference,
    CoordinateFrame,
    DetailMaturity,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    HostedAssembly,
    LengthUnit,
    ObjectRetirement,
    ObjectRevisionPrecondition,
    SemanticBinding,
)


_AUTHORING_OUTPUT_SCHEMA = "GeometryProposalAuthoringOutput@1"
_PROPOSAL_BODY_SCHEMA = "GeometryProgramProposalBody@1"
_PROPOSAL_RECORD_SCHEMA = "GeometryProgramProposalRecord@1"
_FUNCTION_CONTRACTS: dict[str, dict[str, object]] = {
    "array": {"inputs": 1, "parameters": ["count:integer", "step:vector3"]},
    "asset_instance": {"inputs": 0, "placement_fields": ["asset_id", "asset_socket_id", "asset_scale"]},
    "boolean_difference": {"inputs": "2+", "parameters": ["base_index:integer"]},
    "boolean_intersection": {"inputs": "2+", "parameters": []},
    "boolean_union": {"inputs": "2+", "parameters": []},
    "curve": {"inputs": 0, "parameters": ["basis:text(polyline|bezier)", "points:points3"]},
    "extrusion": {"inputs": 0, "parameters": ["profile:points3", "vector:vector3"]},
    "loft": {"inputs": 0, "parameters": ["cap_ends:boolean", "closed_profile:boolean", "profile_size:integer", "profiles:points3"]},
    "revolve": {"inputs": 0, "parameters": ["axis_end:vector3", "axis_start:vector3", "end_radius:number", "start_radius:number"]},
    "solid": {"inputs": 0, "parameters": ["origin:vector3", "size:vector3"]},
    "sweep": {"inputs": 0, "parameters": ["cap_ends:boolean", "closed_profile:boolean", "frame_mode:text(fixed)", "path:points3", "profile:points3"]},
    "transform": {"inputs": 1, "parameters": ["matrix:matrix4"]},
}


def _strict_object(
    properties: Mapping[str, object],
    *,
    description: str | None = None,
) -> dict[str, object]:
    contract: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": dict(properties),
    }
    if description is not None:
        contract["description"] = description
    return contract


def _array_contract(
    items: Mapping[str, object],
    *,
    minimum: int = 0,
    unique: bool = False,
    description: str | None = None,
) -> dict[str, object]:
    contract: dict[str, object] = {
        "type": "array",
        "items": dict(items),
        "minItems": minimum,
    }
    if unique:
        contract["uniqueItems"] = True
    if description is not None:
        contract["description"] = description
    return contract


def _authoring_output_contract() -> dict[str, object]:
    """Expose the exact generic parser topology without a building answer."""

    text = {"type": "string", "minLength": 1}
    identifier = {
        **text,
        "description": "Portable identifier; use only identities authored in this proposal or supplied records.",
    }
    logical_ref = {
        **text,
        "description": "Stable logical or project record reference supplied by the request.",
    }
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    number = {"type": "number"}
    string_list = _array_contract(
        identifier,
        unique=True,
        description="Unique lexicographically sorted identifiers.",
    )
    ref_list = _array_contract(
        logical_ref,
        unique=True,
        description="Unique lexicographically sorted references.",
    )
    vector3 = _array_contract(number, minimum=3)
    vector3["maxItems"] = 3
    matrix4 = _array_contract(number, minimum=16)
    matrix4["maxItems"] = 16
    nullable_identifier = {
        "anyOf": [{"type": "null"}, identifier],
    }
    nullable_digest = {"anyOf": [{"type": "null"}, digest]}
    nullable_unit = {
        "anyOf": [
            {"type": "null"},
            {"type": "string", "enum": [item.value for item in LengthUnit]},
        ]
    }

    transform = _strict_object(
        {
            "schema": {"const": AffineTransform.SCHEMA},
            "matrix": matrix4,
        }
    )
    frame = _strict_object(
        {
            "schema": {"const": CoordinateFrame.SCHEMA},
            "frame_id": identifier,
            "parent_frame_id": nullable_identifier,
            "transform_from_parent": transform,
            "source_refs": _array_contract(logical_ref, minimum=1, unique=True),
        }
    )
    parameter = _strict_object(
        {
            "schema": {"const": GeometryParameter.SCHEMA},
            "name": identifier,
            "kind": {
                "type": "string",
                "enum": [item.value for item in GeometryParameterKind],
            },
            "value_json": {
                "type": "string",
                "description": (
                    "Canonical compact JSON text encoding the typed value; for example a vector is encoded as the string [x,y,z], not as a JSON array field."
                ),
            },
            "unit": nullable_unit,
        }
    )
    asset = _strict_object(
        {
            "schema": {"const": AssetReference.SCHEMA},
            "asset_id": identifier,
            "uri": logical_ref,
            "media_type": text,
            "sha256": digest,
            "native_unit": {
                "type": "string",
                "enum": [item.value for item in LengthUnit],
            },
            "sockets": _array_contract(identifier, minimum=1, unique=True),
            "provenance_refs": _array_contract(logical_ref, minimum=1, unique=True),
        }
    )
    binding = _strict_object(
        {
            "schema": {"const": SemanticBinding.SCHEMA},
            "binding_id": identifier,
            "object_ids": _array_contract(identifier, minimum=1, unique=True),
            "candidate_value_ids": _array_contract(identifier, minimum=1, unique=True),
            "commitment_refs": ref_list,
            "evidence_refs": _array_contract(logical_ref, minimum=1, unique=True),
        }
    )
    operation = _strict_object(
        {
            "schema": {"const": GeometryOperation.SCHEMA},
            "op_id": identifier,
            "kind": {
                "type": "string",
                "enum": [item.value for item in GeometryOperationKind],
            },
            "output_object_ids": _array_contract(identifier, minimum=1, unique=True),
            "input_object_ids": string_list,
            "frame_id": identifier,
            "parameters": _array_contract(parameter),
            "semantic_binding_ids": _array_contract(identifier, minimum=1, unique=True),
            "asset_id": nullable_identifier,
            "asset_socket_id": nullable_identifier,
            "asset_scale": {"anyOf": [{"type": "null"}, vector3]},
            "responds_to_object_ids": string_list,
            "responds_to_frame_ids": string_list,
            "responds_to_binding_ids": string_list,
        },
        description=(
            "Use asset fields only for asset_instance. Parameter names and kinds must exactly follow geometry_function_contracts[kind]."
        ),
    )
    member = _strict_object(
        {
            "schema": {"const": AssemblyMember.SCHEMA},
            "role": {
                "type": "string",
                "enum": [item.value for item in AssemblyRole],
            },
            "object_ids": _array_contract(identifier, minimum=1, unique=True),
        }
    )
    assembly = _strict_object(
        {
            "schema": {"const": HostedAssembly.SCHEMA},
            "assembly_id": identifier,
            "kind": {
                "type": "string",
                "enum": [item.value for item in AssemblyKind],
            },
            "host_object_id": identifier,
            "host_socket_id": identifier,
            "members": _array_contract(member, minimum=1),
            "interface_refs": _array_contract(logical_ref, minimum=1, unique=True),
            "semantic_binding_ids": _array_contract(identifier, minimum=1, unique=True),
            "maturity": {
                "type": "string",
                "enum": [item.value for item in DetailMaturity],
            },
        },
        description=(
            "A hosted semantic component and its geometry are one typed assembly; bind it to the same semantic binding ids as its member objects."
        ),
    )

    def lifecycle(schema: str) -> dict[str, object]:
        return _strict_object(
            {
                "schema": {"const": schema},
                "object_id": identifier,
                "expected_digest": digest,
                "reason_refs": _array_contract(logical_ref, minimum=1, unique=True),
            }
        )

    proposal_body = _strict_object(
        {
            "schema": {"const": _PROPOSAL_BODY_SCHEMA},
            "proposal_id": identifier,
            "predecessor_program_digest": nullable_digest,
            "length_unit": {
                "type": "string",
                "enum": [item.value for item in LengthUnit],
            },
            "tolerance": _strict_object(
                {
                    "schema": {"const": GeometryTolerance.SCHEMA},
                    "linear": {"type": "number", "exclusiveMinimum": 0},
                    "angular_radians": {"type": "number", "exclusiveMinimum": 0},
                }
            ),
            "frames": _array_contract(frame, minimum=1),
            "assets": _array_contract(asset),
            "semantic_bindings": _array_contract(binding, minimum=1),
            "operations": _array_contract(operation, minimum=1),
            "assemblies": _array_contract(assembly),
            "revisions": _array_contract(lifecycle(ObjectRevisionPrecondition.SCHEMA)),
            "retirements": _array_contract(lifecycle(ObjectRetirement.SCHEMA)),
        }
    )
    output = _strict_object(
        {
            "schema": {"const": _AUTHORING_OUTPUT_SCHEMA},
            "selected_template_refs": _array_contract(
                logical_ref,
                unique=True,
                description=(
                    "Unique lexicographically sorted project URIs selected only from available_template_records."
                ),
            ),
            "proposal_body": proposal_body,
        }
    )
    return {
        "schema": "GeometryProposalAuthoringContract@1",
        "json_schema": output,
        "authority": {
            "proposal_only": True,
            "hard_gate": False,
            "canonical_write": False,
            "platform_mutation": False,
        },
    }


class GeometryProposalProductionError(ValueError):
    """Inputs, records, or provider output violate the producer contract."""


class GeometryProposalStatus(StrEnum):
    ACCEPTED = "accepted"
    REFUSED = "refused"
    EXHAUSTED = "exhausted"


class GeometryProposalRoundStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class GeometryProposalProviderIdentity:
    provider_id: str
    model_id: str
    provider_version: str
    provider_fingerprint: str

    SCHEMA = "GeometryProposalProviderIdentity@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.provider_id, "provider_id"),
            (self.model_id, "model_id"),
            (self.provider_version, "provider_version"),
            (self.provider_fingerprint, "provider_fingerprint"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise GeometryProposalProductionError(
                    f"{field} must be non-empty text"
                )

    def matches(self, receipt: ModelInvocationReceipt) -> bool:
        return (
            receipt.provider_id == self.provider_id
            and receipt.model_id == self.model_id
            and receipt.provider_version == self.provider_version
            and receipt.provider_fingerprint == self.provider_fingerprint
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_version": self.provider_version,
            "provider_fingerprint": self.provider_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalProviderIdentity:
        payload = _mapping(value, "provider identity")
        _exact(
            payload,
            {
                "schema",
                "provider_id",
                "model_id",
                "provider_version",
                "provider_fingerprint",
            },
            "provider identity",
        )
        if payload["schema"] != cls.SCHEMA:
            raise GeometryProposalProductionError(
                "provider identity schema changed"
            )
        return cls(
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            provider_version=payload["provider_version"],
            provider_fingerprint=payload["provider_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class GeometryProposalPolicy:
    maximum_rounds: int

    SCHEMA = "GeometryProposalPolicy@1"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_rounds, int)
            or isinstance(self.maximum_rounds, bool)
            or not 1 <= self.maximum_rounds <= 16
        ):
            raise GeometryProposalProductionError(
                "maximum_rounds must be between 1 and 16"
            )

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "maximum_rounds": self.maximum_rounds}


@dataclass(frozen=True, slots=True)
class GeometryProposalIssue:
    code: str
    detail: str

    SCHEMA = "GeometryProposalIssue@1"

    def __post_init__(self) -> None:
        for value, field in ((self.code, "issue code"), (self.detail, "issue detail")):
            if not isinstance(value, str) or not value.strip():
                raise GeometryProposalProductionError(
                    f"{field} must be non-empty text"
                )

    def to_dict(self) -> dict[str, object]:
        return {"schema": self.SCHEMA, "code": self.code, "detail": self.detail}

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalIssue:
        payload = _mapping(value, "geometry proposal issue")
        _exact(payload, {"schema", "code", "detail"}, "geometry proposal issue")
        if payload["schema"] != cls.SCHEMA:
            raise GeometryProposalProductionError("geometry issue schema changed")
        return cls(code=payload["code"], detail=payload["detail"])


@dataclass(frozen=True, slots=True)
class GeometryProposalRoundReceipt:
    round_id: str
    round_index: int
    status: GeometryProposalRoundStatus
    spatial_option_ref: ProjectRecordRef
    candidate_program_digest: str
    request: ModelInvocationRequest
    model_receipt: ModelInvocationReceipt
    selected_template_refs: tuple[str, ...]
    issues: tuple[GeometryProposalIssue, ...]
    proposal_digest: str | None
    compiler_receipt_json: str | None

    SCHEMA = "GeometryProposalRoundReceipt@1"

    def __post_init__(self) -> None:
        if not isinstance(self.round_id, str) or not self.round_id:
            raise GeometryProposalProductionError("round_id must be non-empty")
        if not isinstance(self.round_index, int) or isinstance(self.round_index, bool) or self.round_index < 1:
            raise GeometryProposalProductionError("round_index must be positive")
        if not isinstance(self.status, GeometryProposalRoundStatus):
            raise TypeError("status must be GeometryProposalRoundStatus")
        if not isinstance(self.spatial_option_ref, ProjectRecordRef):
            raise TypeError("spatial_option_ref must be ProjectRecordRef")
        _sha256(self.candidate_program_digest, "candidate_program_digest")
        if not isinstance(self.request, ModelInvocationRequest):
            raise TypeError("request must be ModelInvocationRequest")
        if not isinstance(self.model_receipt, ModelInvocationReceipt):
            raise TypeError("model_receipt must be ModelInvocationReceipt")
        _strings(self.selected_template_refs, "selected_template_refs", allow_empty=True)
        if not isinstance(self.issues, tuple) or any(
            not isinstance(item, GeometryProposalIssue) for item in self.issues
        ):
            raise TypeError("issues must contain GeometryProposalIssue values")
        if self.status is GeometryProposalRoundStatus.ACCEPTED:
            if self.issues or self.proposal_digest is None or self.compiler_receipt_json is None:
                raise GeometryProposalProductionError(
                    "accepted round requires proposal and compiler receipt without issues"
                )
        elif not self.issues:
            raise GeometryProposalProductionError(
                "rejected or refused round requires typed issues"
            )
        if self.proposal_digest is not None:
            _sha256(self.proposal_digest, "proposal_digest")
        if self.compiler_receipt_json is not None:
            decoded = json.loads(self.compiler_receipt_json)
            if _canonical_json(decoded) != self.compiler_receipt_json:
                raise GeometryProposalProductionError(
                    "compiler receipt JSON must be canonical"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "round_id": self.round_id,
            "round_index": self.round_index,
            "status": self.status.value,
            "spatial_option_ref": _record_dict(self.spatial_option_ref),
            "candidate_program_digest": self.candidate_program_digest,
            "request": self.request.to_dict(),
            "model_receipt": self.model_receipt.to_dict(),
            "selected_template_refs": list(self.selected_template_refs),
            "issues": [item.to_dict() for item in self.issues],
            "proposal_digest": self.proposal_digest,
            "compiler_receipt": (
                None if self.compiler_receipt_json is None else json.loads(self.compiler_receipt_json)
            ),
            "derivation_only": True,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalRoundReceipt:
        payload = _mapping(value, "geometry proposal round")
        _exact(
            payload,
            {
                "schema", "round_id", "round_index", "status",
                "spatial_option_ref", "candidate_program_digest", "request",
                "model_receipt", "selected_template_refs", "issues",
                "proposal_digest", "compiler_receipt", "derivation_only",
                "hard_gate_authority", "canonical_write_authority",
            },
            "geometry proposal round",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["derivation_only"] is not True
            or payload["hard_gate_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise GeometryProposalProductionError(
                "geometry proposal round acquired forbidden authority"
            )
        issues = payload["issues"]
        compiler_receipt = payload["compiler_receipt"]
        if not isinstance(issues, list):
            raise TypeError("round issues must be a list")
        return cls(
            round_id=payload["round_id"],
            round_index=payload["round_index"],
            status=GeometryProposalRoundStatus(payload["status"]),
            spatial_option_ref=_record_from_dict(payload["spatial_option_ref"]),
            candidate_program_digest=payload["candidate_program_digest"],
            request=ModelInvocationRequest.from_dict(payload["request"]),
            model_receipt=ModelInvocationReceipt.from_dict(payload["model_receipt"]),
            selected_template_refs=_strings_from_json(
                payload["selected_template_refs"], "selected_template_refs"
            ),
            issues=tuple(GeometryProposalIssue.from_dict(item) for item in issues),
            proposal_digest=payload["proposal_digest"],
            compiler_receipt_json=(
                None if compiler_receipt is None else _canonical_json(compiler_receipt)
            ),
        )


@dataclass(frozen=True, slots=True)
class GeometryProposalLineage:
    lineage_id: str
    status: GeometryProposalStatus
    project_id: str
    run_id: str
    base: ProjectVersionRef
    spatial_option_ref: ProjectRecordRef
    spatial_option_digest: str
    candidate_program_digest: str
    required_commitment_refs: tuple[str, ...]
    provider_identity: GeometryProposalProviderIdentity
    round_refs: tuple[ProjectRecordRef, ...]
    accepted_proposal_ref: ProjectRecordRef | None
    accepted_proposal_digest: str | None

    SCHEMA = "GeometryProposalLineage@1"

    def __post_init__(self) -> None:
        if not isinstance(self.lineage_id, str) or not self.lineage_id:
            raise GeometryProposalProductionError("lineage_id must be non-empty")
        if not isinstance(self.status, GeometryProposalStatus):
            raise TypeError("status must be GeometryProposalStatus")
        if self.base.project_id != self.project_id:
            raise GeometryProposalProductionError("lineage and base disagree")
        if self.spatial_option_ref.project_id != self.project_id:
            raise GeometryProposalProductionError("lineage source project disagrees")
        _sha256(self.spatial_option_digest, "spatial_option_digest")
        _sha256(self.candidate_program_digest, "candidate_program_digest")
        _strings(self.required_commitment_refs, "required_commitment_refs")
        if not isinstance(self.provider_identity, GeometryProposalProviderIdentity):
            raise TypeError("provider_identity is invalid")
        if not isinstance(self.round_refs, tuple) or not self.round_refs:
            raise GeometryProposalProductionError("lineage requires round refs")
        if any(item.project_id != self.project_id for item in self.round_refs):
            raise GeometryProposalProductionError("round refs cross project boundary")
        if self.status is GeometryProposalStatus.ACCEPTED:
            if self.accepted_proposal_ref is None or self.accepted_proposal_digest is None:
                raise GeometryProposalProductionError("accepted lineage requires proposal")
        elif self.accepted_proposal_ref is not None or self.accepted_proposal_digest is not None:
            raise GeometryProposalProductionError("failed lineage cannot carry proposal")
        if self.accepted_proposal_digest is not None:
            _sha256(self.accepted_proposal_digest, "accepted_proposal_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "lineage_id": self.lineage_id,
            "status": self.status.value,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "base": _base_dict(self.base),
            "spatial_option_ref": _record_dict(self.spatial_option_ref),
            "spatial_option_digest": self.spatial_option_digest,
            "candidate_program_digest": self.candidate_program_digest,
            "required_commitment_refs": list(self.required_commitment_refs),
            "provider_identity": self.provider_identity.to_dict(),
            "round_refs": [_record_dict(item) for item in self.round_refs],
            "accepted_proposal_ref": (
                None if self.accepted_proposal_ref is None else _record_dict(self.accepted_proposal_ref)
            ),
            "accepted_proposal_digest": self.accepted_proposal_digest,
            "proposal_only": True,
            "hard_gate_authority": False,
            "canonical_write_authority": False,
        }

    @classmethod
    def from_dict(cls, value: object) -> GeometryProposalLineage:
        payload = _mapping(value, "geometry proposal lineage")
        _exact(
            payload,
            {
                "schema", "lineage_id", "status", "project_id", "run_id",
                "base", "spatial_option_ref", "spatial_option_digest",
                "candidate_program_digest", "required_commitment_refs",
                "provider_identity", "round_refs", "accepted_proposal_ref",
                "accepted_proposal_digest", "proposal_only",
                "hard_gate_authority", "canonical_write_authority",
            },
            "geometry proposal lineage",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or payload["proposal_only"] is not True
            or payload["hard_gate_authority"] is not False
            or payload["canonical_write_authority"] is not False
        ):
            raise GeometryProposalProductionError("lineage acquired forbidden authority")
        round_refs = payload["round_refs"]
        if not isinstance(round_refs, list):
            raise TypeError("round_refs must be a list")
        accepted_ref = payload["accepted_proposal_ref"]
        return cls(
            lineage_id=payload["lineage_id"],
            status=GeometryProposalStatus(payload["status"]),
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            base=_base_from_dict(payload["base"]),
            spatial_option_ref=_record_from_dict(payload["spatial_option_ref"]),
            spatial_option_digest=payload["spatial_option_digest"],
            candidate_program_digest=payload["candidate_program_digest"],
            required_commitment_refs=_strings_from_json(
                payload["required_commitment_refs"], "required_commitment_refs"
            ),
            provider_identity=GeometryProposalProviderIdentity.from_dict(
                payload["provider_identity"]
            ),
            round_refs=tuple(_record_from_dict(item) for item in round_refs),
            accepted_proposal_ref=(
                None if accepted_ref is None else _record_from_dict(accepted_ref)
            ),
            accepted_proposal_digest=payload["accepted_proposal_digest"],
        )


@dataclass(frozen=True, slots=True)
class GeometryProposalProductionResult:
    status: GeometryProposalStatus
    lineage_ref: ProjectRecordRef
    round_refs: tuple[ProjectRecordRef, ...]
    proposal_ref: ProjectRecordRef | None
    proposal: GeometryProgramProposal | None
    program: CompiledGeometryProgram | None


@dataclass(frozen=True, slots=True)
class LoadedGeometryProposalLineage:
    lineage: GeometryProposalLineage
    rounds: tuple[GeometryProposalRoundReceipt, ...]
    proposal: GeometryProgramProposal | None


class GeometryProposalRepository(RecordSink, Protocol):
    def load_json(self, ref: ProjectRecordRef) -> dict[str, Any]: ...


async def produce_geometry_program_proposal(
    repository: GeometryProposalRepository,
    provider: AsyncModelProvider,
    *,
    run: RunRef,
    destination: PersistenceDestination,
    spatial_option_ref: ProjectRecordRef,
    projection: CandidateProgramProjection,
    required_commitment_refs: tuple[str, ...],
    provider_identity: GeometryProposalProviderIdentity,
    policy: GeometryProposalPolicy,
    template_refs: tuple[ProjectRecordRef, ...] = (),
    available_asset_digests: Mapping[str, str] | None = None,
    prior_program: CompiledGeometryProgram | None = None,
) -> GeometryProposalProductionResult:
    """Author, compile, and persist bounded proposal rounds without fallback."""

    destination = require_destination(destination, producer="geometry proposal producer")
    _validate_inputs(
        run,
        destination,
        spatial_option_ref,
        projection,
        required_commitment_refs,
        provider_identity,
        policy,
        template_refs,
    )
    spatial_payload = repository.load_json(spatial_option_ref)
    spatial_option = SpatialOptionProposal.from_dict(spatial_payload)
    if spatial_option.ref != projection.selected_option_ref:
        raise GeometryProposalProductionError(
            "candidate projection does not select the supplied spatial option record"
        )
    template_payloads = tuple(
        {"ref": _record_dict(ref), "payload": repository.load_json(ref)}
        for ref in template_refs
    )
    allowed_template_uris = frozenset(ref.uri for ref in template_refs)
    round_refs: list[ProjectRecordRef] = []
    repair_issues: tuple[GeometryProposalIssue, ...] = ()
    assets = {} if available_asset_digests is None else dict(available_asset_digests)

    for round_index in range(1, policy.maximum_rounds + 1):
        request_payload = _request_payload(
            spatial_option_ref,
            spatial_option,
            projection,
            required_commitment_refs,
            template_payloads,
            repair_issues,
        )
        request = ModelInvocationRequest.create(
            request_id=f"geometry-proposal-{run.run_id}-{round_index:02d}",
            phase=ModelPhase.ACTION_PROPOSAL,
            checkpoint_digest=projection.projection_digest,
            context_digest=_digest(request_payload),
            payload=request_payload,
        )
        receipt = await provider.invoke(request)
        issues: tuple[GeometryProposalIssue, ...]
        selected_templates: tuple[str, ...] = ()
        proposal: GeometryProgramProposal | None = None
        compiler_receipt: dict[str, object] | None = None
        round_status = GeometryProposalRoundStatus.REJECTED

        if receipt.request != request:
            issues = (
                GeometryProposalIssue(
                    "provider_request_mismatch",
                    "provider receipt does not bind the current request",
                ),
            )
            round_status = GeometryProposalRoundStatus.REFUSED
        elif not provider_identity.matches(receipt):
            issues = (
                GeometryProposalIssue(
                    "provider_identity_mismatch",
                    "provider or model identity changed; silent substitution rejected",
                ),
            )
            round_status = GeometryProposalRoundStatus.REFUSED
        elif receipt.status is not ModelInvocationStatus.SUCCESS:
            issues = (
                GeometryProposalIssue(
                    "provider_refused",
                    f"provider stopped with {receipt.status.value}: {receipt.error_code}",
                ),
            )
            round_status = GeometryProposalRoundStatus.REFUSED
        else:
            try:
                selected_templates, body = _authoring_output(receipt.output)
                if not set(selected_templates) <= allowed_template_uris:
                    raise GeometryProposalProductionError(
                        "model selected a template outside the supplied project records"
                    )
                proposal = _proposal_from_body(body, projection)
                _validate_semantic_coverage(
                    proposal,
                    projection,
                    required_commitment_refs,
                    spatial_option_ref,
                )
                compilation = compile_geometry_program(
                    projection,
                    proposal,
                    active_commitment_refs=required_commitment_refs,
                    available_asset_digests=assets,
                    prior_program=prior_program,
                )
                compiler_receipt = compilation.receipt.to_dict()
                if compilation.program is None:
                    issues = tuple(
                        GeometryProposalIssue(
                            f"compiler.{item.code.value}",
                            f"{item.subject_id}: {item.detail}",
                        )
                        for item in compilation.receipt.issues
                    )
                else:
                    issues = ()
                    round_status = GeometryProposalRoundStatus.ACCEPTED
                    program = compilation.program
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                issues = (GeometryProposalIssue("malformed_model_output", str(exc)),)

        round_receipt = GeometryProposalRoundReceipt(
            round_id=f"geometry-proposal-round-{round_index:02d}",
            round_index=round_index,
            status=round_status,
            spatial_option_ref=spatial_option_ref,
            candidate_program_digest=projection.projection_digest,
            request=request,
            model_receipt=receipt,
            selected_template_refs=selected_templates,
            issues=issues,
            proposal_digest=None if proposal is None else proposal.proposal_digest,
            compiler_receipt_json=(
                None if compiler_receipt is None else _canonical_json(compiler_receipt)
            ),
        )
        round_ref = repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"geometry-proposal-round-{round_index:02d}",
            payload=round_receipt.to_dict(),
        )
        round_refs.append(round_ref)

        if round_status is GeometryProposalRoundStatus.ACCEPTED:
            assert proposal is not None
            proposal_ref = repository.put_json(
                run=run,
                destination=destination,
                record_kind="geometry-program-proposal",
                payload=_proposal_record(
                    proposal,
                    spatial_option_ref,
                    required_commitment_refs,
                    provider_identity,
                    selected_templates,
                    round_ref,
                ),
            )
            lineage_ref = _persist_lineage(
                repository,
                run,
                destination,
                GeometryProposalStatus.ACCEPTED,
                spatial_option_ref,
                spatial_option.proposal_digest,
                projection,
                required_commitment_refs,
                provider_identity,
                tuple(round_refs),
                proposal_ref,
                proposal.proposal_digest,
            )
            return GeometryProposalProductionResult(
                GeometryProposalStatus.ACCEPTED,
                lineage_ref,
                tuple(round_refs),
                proposal_ref,
                proposal,
                program,
            )
        if round_status is GeometryProposalRoundStatus.REFUSED:
            lineage_ref = _persist_lineage(
                repository,
                run,
                destination,
                GeometryProposalStatus.REFUSED,
                spatial_option_ref,
                spatial_option.proposal_digest,
                projection,
                required_commitment_refs,
                provider_identity,
                tuple(round_refs),
                None,
                None,
            )
            return GeometryProposalProductionResult(
                GeometryProposalStatus.REFUSED,
                lineage_ref,
                tuple(round_refs),
                None,
                None,
                None,
            )
        repair_issues = issues

    lineage_ref = _persist_lineage(
        repository,
        run,
        destination,
        GeometryProposalStatus.EXHAUSTED,
        spatial_option_ref,
        spatial_option.proposal_digest,
        projection,
        required_commitment_refs,
        provider_identity,
        tuple(round_refs),
        None,
        None,
    )
    return GeometryProposalProductionResult(
        GeometryProposalStatus.EXHAUSTED,
        lineage_ref,
        tuple(round_refs),
        None,
        None,
        None,
    )


def load_geometry_proposal_lineage(
    repository: GeometryProposalRepository,
    lineage_ref: ProjectRecordRef,
) -> LoadedGeometryProposalLineage:
    lineage = GeometryProposalLineage.from_dict(repository.load_json(lineage_ref))
    rounds = tuple(
        GeometryProposalRoundReceipt.from_dict(repository.load_json(ref))
        for ref in lineage.round_refs
    )
    if tuple(item.round_index for item in rounds) != tuple(range(1, len(rounds) + 1)):
        raise GeometryProposalProductionError("proposal round lineage is not contiguous")
    if any(
        item.candidate_program_digest != lineage.candidate_program_digest
        or item.spatial_option_ref != lineage.spatial_option_ref
        for item in rounds
    ):
        raise GeometryProposalProductionError("proposal round lineage identity drifted")
    proposal = None
    if lineage.accepted_proposal_ref is not None:
        proposal_payload = repository.load_json(lineage.accepted_proposal_ref)
        proposal = _proposal_from_record(proposal_payload)
        if proposal.proposal_digest != lineage.accepted_proposal_digest:
            raise GeometryProposalProductionError("accepted proposal digest drifted")
        if proposal.candidate_program_digest != lineage.candidate_program_digest:
            raise GeometryProposalProductionError("accepted proposal base drifted")
    return LoadedGeometryProposalLineage(lineage, rounds, proposal)


def _validate_inputs(
    run: RunRef,
    destination: PersistenceDestination,
    spatial_option_ref: ProjectRecordRef,
    projection: CandidateProgramProjection,
    commitment_refs: tuple[str, ...],
    provider_identity: GeometryProposalProviderIdentity,
    policy: GeometryProposalPolicy,
    template_refs: tuple[ProjectRecordRef, ...],
) -> None:
    if not isinstance(run, RunRef) or not isinstance(projection, CandidateProgramProjection):
        raise TypeError("run and projection must be typed values")
    if destination.area is not PersistenceArea.RUN_RECORD or destination.run_id != run.run_id:
        raise GeometryProposalProductionError(
            "geometry proposals require the assigned run-record destination"
        )
    if (
        projection.project_id != run.project_id
        or projection.run_id != run.run_id
        or projection.base != run.base
    ):
        raise GeometryProposalProductionError("projection and run are not exact-base peers")
    if spatial_option_ref.project_id != run.project_id:
        raise GeometryProposalProductionError("spatial option crosses project boundary")
    _strings(commitment_refs, "required_commitment_refs")
    if not isinstance(provider_identity, GeometryProposalProviderIdentity):
        raise TypeError("provider_identity is invalid")
    if not isinstance(policy, GeometryProposalPolicy):
        raise TypeError("policy is invalid")
    if not isinstance(template_refs, tuple) or any(
        not isinstance(item, ProjectRecordRef) or item.project_id != run.project_id
        for item in template_refs
    ):
        raise GeometryProposalProductionError("template refs cross project boundary")
    uris = tuple(item.uri for item in template_refs)
    if uris != tuple(sorted(set(uris))):
        raise GeometryProposalProductionError("template refs must be deterministic")


def _request_payload(
    spatial_ref: ProjectRecordRef,
    spatial: SpatialOptionProposal,
    projection: CandidateProgramProjection,
    commitments: tuple[str, ...],
    templates: tuple[dict[str, object], ...],
    repair_issues: tuple[GeometryProposalIssue, ...],
) -> dict[str, object]:
    return {
        "schema": "GeometryProposalAuthoringRequest@1",
        "spatial_option_record": {
            "ref": _record_dict(spatial_ref),
            "proposal": spatial.to_dict(),
        },
        "candidate_program": projection.to_dict(),
        "required_commitment_refs": list(commitments),
        "available_template_records": list(templates),
        "geometry_function_contracts": _FUNCTION_CONTRACTS,
        "repair_issues": [item.to_dict() for item in repair_issues],
        "required_output_schema": _AUTHORING_OUTPUT_SCHEMA,
        "required_output_contract": _authoring_output_contract(),
        "instructions": [
            "Author geometry only from supplied project records and candidate values.",
            "Return exactly the keys and nested field shapes in required_output_contract.json_schema; do not invent aliases such as geometry_nodes or geometry_functions.",
            "Use only declared geometry function kinds and explicit parameters.",
            "GeometryParameter.value_json is canonical compact JSON encoded as a string, not a nested JSON value.",
            "Bind every candidate value and required commitment through semantic bindings.",
            "Include the spatial option record URI in every semantic binding evidence_refs.",
            "When a supplied semantic component requires a hosted assembly, represent its semantic identity and geometry together through semantic_binding_ids and typed assembly members.",
            "Do not claim hard-gate, acceptance, canonical-write, or platform authority.",
            "No framework fallback or unstated building dimension will be supplied.",
        ],
    }


def _authoring_output(value: object) -> tuple[tuple[str, ...], Mapping[str, Any]]:
    payload = _mapping(value, "geometry authoring output")
    _exact(
        payload,
        {"schema", "selected_template_refs", "proposal_body"},
        "geometry authoring output",
    )
    if payload["schema"] != _AUTHORING_OUTPUT_SCHEMA:
        raise GeometryProposalProductionError("geometry authoring output schema changed")
    selected = _strings(
        _strings_from_json(
            payload["selected_template_refs"],
            "selected_template_refs",
        ),
        "selected_template_refs",
        allow_empty=True,
    )
    return selected, _mapping(payload["proposal_body"], "proposal_body")


def _validate_semantic_coverage(
    proposal: GeometryProgramProposal,
    projection: CandidateProgramProjection,
    commitments: tuple[str, ...],
    spatial_ref: ProjectRecordRef,
) -> None:
    candidate_ids = {item.value_id for item in projection.values}
    bound_candidate_ids = {
        value_id for binding in proposal.semantic_bindings for value_id in binding.candidate_value_ids
    }
    if bound_candidate_ids != candidate_ids:
        missing = sorted(candidate_ids - bound_candidate_ids)
        extra = sorted(bound_candidate_ids - candidate_ids)
        raise GeometryProposalProductionError(
            f"semantic bindings do not exactly cover candidate values; missing={missing}, extra={extra}"
        )
    bound_commitments = {
        ref for binding in proposal.semantic_bindings for ref in binding.commitment_refs
    }
    if not set(commitments) <= bound_commitments:
        raise GeometryProposalProductionError(
            "semantic bindings omit required active commitments"
        )
    if any(spatial_ref.uri not in binding.evidence_refs for binding in proposal.semantic_bindings):
        raise GeometryProposalProductionError(
            "every semantic binding must cite the source spatial option record"
        )


def _proposal_from_body(
    value: Mapping[str, Any],
    projection: CandidateProgramProjection,
) -> GeometryProgramProposal:
    _exact(
        value,
        {
            "schema", "proposal_id", "predecessor_program_digest", "length_unit",
            "tolerance", "frames", "assets", "semantic_bindings", "operations",
            "assemblies", "revisions", "retirements",
        },
        "geometry proposal body",
    )
    if value["schema"] != _PROPOSAL_BODY_SCHEMA:
        raise GeometryProposalProductionError("geometry proposal body schema changed")
    return _construct_proposal(value, projection.project_id, projection.run_id, projection.base, projection.projection_digest)


def _construct_proposal(
    value: Mapping[str, Any],
    project_id: str,
    run_id: str,
    base: ProjectVersionRef,
    candidate_digest: str,
) -> GeometryProgramProposal:
    tolerance = _mapping(value["tolerance"], "geometry tolerance")
    _exact(tolerance, {"schema", "linear", "angular_radians"}, "geometry tolerance")
    if tolerance["schema"] != GeometryTolerance.SCHEMA:
        raise GeometryProposalProductionError("geometry tolerance schema changed")
    return GeometryProgramProposal(
        proposal_id=value["proposal_id"],
        project_id=project_id,
        run_id=run_id,
        base=base,
        candidate_program_digest=candidate_digest,
        predecessor_program_digest=value["predecessor_program_digest"],
        length_unit=LengthUnit(value["length_unit"]),
        tolerance=GeometryTolerance(tolerance["linear"], tolerance["angular_radians"]),
        frames=_decode_list(value["frames"], _frame, "frames"),
        assets=_decode_list(value["assets"], _asset, "assets"),
        semantic_bindings=_decode_list(value["semantic_bindings"], _binding, "semantic_bindings"),
        operations=_decode_list(value["operations"], _operation, "operations"),
        assemblies=_decode_list(value["assemblies"], _assembly, "assemblies"),
        revisions=_decode_list(value["revisions"], _revision, "revisions"),
        retirements=_decode_list(value["retirements"], _retirement, "retirements"),
    )


def _frame(value: object) -> CoordinateFrame:
    payload = _mapping(value, "coordinate frame")
    _exact(payload, {"schema", "frame_id", "parent_frame_id", "transform_from_parent", "source_refs"}, "coordinate frame")
    transform = _mapping(payload["transform_from_parent"], "affine transform")
    _exact(transform, {"schema", "matrix"}, "affine transform")
    if payload["schema"] != CoordinateFrame.SCHEMA or transform["schema"] != AffineTransform.SCHEMA:
        raise GeometryProposalProductionError("coordinate frame schema changed")
    return CoordinateFrame(
        frame_id=payload["frame_id"],
        parent_frame_id=payload["parent_frame_id"],
        transform_from_parent=AffineTransform(tuple(transform["matrix"])),
        source_refs=_strings_from_json(payload["source_refs"], "frame source_refs"),
    )


def _parameter(value: object) -> GeometryParameter:
    payload = _mapping(value, "geometry parameter")
    _exact(payload, {"schema", "name", "kind", "value_json", "unit"}, "geometry parameter")
    if payload["schema"] != GeometryParameter.SCHEMA:
        raise GeometryProposalProductionError("geometry parameter schema changed")
    return GeometryParameter(
        name=payload["name"],
        kind=GeometryParameterKind(payload["kind"]),
        value_json=payload["value_json"],
        unit=None if payload["unit"] is None else LengthUnit(payload["unit"]),
    )


def _binding(value: object) -> SemanticBinding:
    payload = _mapping(value, "semantic binding")
    _exact(payload, {"schema", "binding_id", "object_ids", "candidate_value_ids", "commitment_refs", "evidence_refs"}, "semantic binding")
    if payload["schema"] != SemanticBinding.SCHEMA:
        raise GeometryProposalProductionError("semantic binding schema changed")
    return SemanticBinding(
        binding_id=payload["binding_id"],
        object_ids=_strings_from_json(payload["object_ids"], "binding object_ids"),
        candidate_value_ids=_strings_from_json(payload["candidate_value_ids"], "candidate_value_ids"),
        commitment_refs=_strings_from_json(payload["commitment_refs"], "commitment_refs"),
        evidence_refs=_strings_from_json(payload["evidence_refs"], "evidence_refs"),
    )


def _asset(value: object) -> AssetReference:
    payload = _mapping(value, "asset reference")
    _exact(payload, {"schema", "asset_id", "uri", "media_type", "sha256", "native_unit", "sockets", "provenance_refs"}, "asset reference")
    if payload["schema"] != AssetReference.SCHEMA:
        raise GeometryProposalProductionError("asset reference schema changed")
    return AssetReference(
        asset_id=payload["asset_id"], uri=payload["uri"], media_type=payload["media_type"],
        sha256=payload["sha256"], native_unit=LengthUnit(payload["native_unit"]),
        sockets=_strings_from_json(payload["sockets"], "asset sockets"),
        provenance_refs=_strings_from_json(payload["provenance_refs"], "asset provenance_refs"),
    )


def _operation(value: object) -> GeometryOperation:
    payload = _mapping(value, "geometry operation")
    _exact(payload, {"schema", "op_id", "kind", "output_object_ids", "input_object_ids", "frame_id", "parameters", "semantic_binding_ids", "asset_id", "asset_socket_id", "asset_scale", "responds_to_object_ids", "responds_to_frame_ids", "responds_to_binding_ids"}, "geometry operation")
    if payload["schema"] != GeometryOperation.SCHEMA:
        raise GeometryProposalProductionError("geometry operation schema changed")
    scale = payload["asset_scale"]
    return GeometryOperation(
        op_id=payload["op_id"], kind=GeometryOperationKind(payload["kind"]),
        output_object_ids=_strings_from_json(payload["output_object_ids"], "output_object_ids"),
        input_object_ids=_strings_from_json(payload["input_object_ids"], "input_object_ids"),
        frame_id=payload["frame_id"],
        parameters=_decode_list(payload["parameters"], _parameter, "parameters"),
        semantic_binding_ids=_strings_from_json(payload["semantic_binding_ids"], "semantic_binding_ids"),
        asset_id=payload["asset_id"], asset_socket_id=payload["asset_socket_id"],
        asset_scale=None if scale is None else tuple(scale),
        responds_to_object_ids=_strings_from_json(payload["responds_to_object_ids"], "responds_to_object_ids"),
        responds_to_frame_ids=_strings_from_json(payload["responds_to_frame_ids"], "responds_to_frame_ids"),
        responds_to_binding_ids=_strings_from_json(payload["responds_to_binding_ids"], "responds_to_binding_ids"),
    )


def _member(value: object) -> AssemblyMember:
    payload = _mapping(value, "assembly member")
    _exact(payload, {"schema", "role", "object_ids"}, "assembly member")
    if payload["schema"] != AssemblyMember.SCHEMA:
        raise GeometryProposalProductionError("assembly member schema changed")
    return AssemblyMember(AssemblyRole(payload["role"]), _strings_from_json(payload["object_ids"], "member object_ids"))


def _assembly(value: object) -> HostedAssembly:
    payload = _mapping(value, "hosted assembly")
    _exact(payload, {"schema", "assembly_id", "kind", "host_object_id", "host_socket_id", "members", "interface_refs", "semantic_binding_ids", "maturity"}, "hosted assembly")
    if payload["schema"] != HostedAssembly.SCHEMA:
        raise GeometryProposalProductionError("hosted assembly schema changed")
    return HostedAssembly(
        assembly_id=payload["assembly_id"], kind=AssemblyKind(payload["kind"]),
        host_object_id=payload["host_object_id"], host_socket_id=payload["host_socket_id"],
        members=_decode_list(payload["members"], _member, "members"),
        interface_refs=_strings_from_json(payload["interface_refs"], "interface_refs"),
        semantic_binding_ids=_strings_from_json(payload["semantic_binding_ids"], "semantic_binding_ids"),
        maturity=DetailMaturity(payload["maturity"]),
    )


def _revision(value: object) -> ObjectRevisionPrecondition:
    payload = _mapping(value, "object revision")
    _exact(payload, {"schema", "object_id", "expected_digest", "reason_refs"}, "object revision")
    if payload["schema"] != ObjectRevisionPrecondition.SCHEMA:
        raise GeometryProposalProductionError("object revision schema changed")
    return ObjectRevisionPrecondition(payload["object_id"], payload["expected_digest"], _strings_from_json(payload["reason_refs"], "revision reason_refs"))


def _retirement(value: object) -> ObjectRetirement:
    payload = _mapping(value, "object retirement")
    _exact(payload, {"schema", "object_id", "expected_digest", "reason_refs"}, "object retirement")
    if payload["schema"] != ObjectRetirement.SCHEMA:
        raise GeometryProposalProductionError("object retirement schema changed")
    return ObjectRetirement(payload["object_id"], payload["expected_digest"], _strings_from_json(payload["reason_refs"], "retirement reason_refs"))


def _proposal_body(proposal: GeometryProgramProposal) -> dict[str, object]:
    payload = proposal.to_dict()
    return {
        "schema": _PROPOSAL_BODY_SCHEMA,
        "proposal_id": payload["proposal_id"],
        "predecessor_program_digest": payload["predecessor_program_digest"],
        "length_unit": payload["length_unit"],
        "tolerance": payload["tolerance"],
        "frames": payload["frames"],
        "assets": payload["assets"],
        "semantic_bindings": payload["semantic_bindings"],
        "operations": payload["operations"],
        "assemblies": payload["assemblies"],
        "revisions": payload["revisions"],
        "retirements": payload["retirements"],
    }


def _proposal_record(
    proposal: GeometryProgramProposal,
    spatial_ref: ProjectRecordRef,
    commitments: tuple[str, ...],
    identity: GeometryProposalProviderIdentity,
    selected_templates: tuple[str, ...],
    round_ref: ProjectRecordRef,
) -> dict[str, object]:
    return {
        "schema": _PROPOSAL_RECORD_SCHEMA,
        "proposal": proposal.to_dict(),
        "source_spatial_option_ref": _record_dict(spatial_ref),
        "required_commitment_refs": list(commitments),
        "selected_template_refs": list(selected_templates),
        "provider_identity": identity.to_dict(),
        "accepted_round_ref": _record_dict(round_ref),
        "proposal_only": True,
        "hard_gate_authority": False,
        "canonical_write_authority": False,
    }


def _proposal_from_record(value: object) -> GeometryProgramProposal:
    payload = _mapping(value, "geometry proposal record")
    _exact(payload, {"schema", "proposal", "source_spatial_option_ref", "required_commitment_refs", "selected_template_refs", "provider_identity", "accepted_round_ref", "proposal_only", "hard_gate_authority", "canonical_write_authority"}, "geometry proposal record")
    if (
        payload["schema"] != _PROPOSAL_RECORD_SCHEMA
        or payload["proposal_only"] is not True
        or payload["hard_gate_authority"] is not False
        or payload["canonical_write_authority"] is not False
    ):
        raise GeometryProposalProductionError("geometry proposal record acquired forbidden authority")
    proposal = _mapping(payload["proposal"], "geometry proposal")
    _exact(proposal, {"schema", "proposal_id", "project_id", "run_id", "base", "candidate_program_digest", "predecessor_program_digest", "length_unit", "tolerance", "frames", "assets", "semantic_bindings", "operations", "assemblies", "revisions", "retirements", "generation_authority", "hard_gate_authority", "canonical_write_authority"}, "geometry proposal")
    if (
        proposal["schema"] != GeometryProgramProposal.SCHEMA
        or proposal["generation_authority"] is not False
        or proposal["hard_gate_authority"] is not False
        or proposal["canonical_write_authority"] is not False
    ):
        raise GeometryProposalProductionError("geometry proposal acquired forbidden authority")
    body = dict(_proposal_body_from_full(proposal))
    return _construct_proposal(
        body,
        proposal["project_id"],
        proposal["run_id"],
        _base_from_dict(proposal["base"]),
        proposal["candidate_program_digest"],
    )


def _proposal_body_from_full(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "schema": _PROPOSAL_BODY_SCHEMA,
        **{
            key: value[key]
            for key in (
                "proposal_id", "predecessor_program_digest", "length_unit",
                "tolerance", "frames", "assets", "semantic_bindings",
                "operations", "assemblies", "revisions", "retirements",
            )
        },
    }


def _persist_lineage(
    repository: GeometryProposalRepository,
    run: RunRef,
    destination: PersistenceDestination,
    status: GeometryProposalStatus,
    spatial_ref: ProjectRecordRef,
    spatial_digest: str,
    projection: CandidateProgramProjection,
    commitments: tuple[str, ...],
    identity: GeometryProposalProviderIdentity,
    round_refs: tuple[ProjectRecordRef, ...],
    proposal_ref: ProjectRecordRef | None,
    proposal_digest: str | None,
) -> ProjectRecordRef:
    lineage = GeometryProposalLineage(
        lineage_id=f"geometry-proposal-lineage-{projection.projection_digest[:20]}",
        status=status,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        spatial_option_ref=spatial_ref,
        spatial_option_digest=spatial_digest,
        candidate_program_digest=projection.projection_digest,
        required_commitment_refs=commitments,
        provider_identity=identity,
        round_refs=round_refs,
        accepted_proposal_ref=proposal_ref,
        accepted_proposal_digest=proposal_digest,
    )
    return repository.put_json(
        run=run,
        destination=destination,
        record_kind="geometry-proposal-lineage",
        payload=lineage.to_dict(),
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be an object")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unexpected = sorted(actual - fields)
        raise GeometryProposalProductionError(
            f"{label}: field mismatch; missing={missing}; "
            f"unexpected={unexpected}"
        )


def _strings(values: tuple[str, ...], field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise GeometryProposalProductionError(f"{field} must be a tuple")
    if any(not isinstance(item, str) or not item for item in values):
        raise GeometryProposalProductionError(f"{field} contains invalid text")
    if values != tuple(sorted(set(values))):
        raise GeometryProposalProductionError(f"{field} must be deterministic")
    return values


def _strings_from_json(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    return tuple(value)


def _decode_list(value: object, decoder: Any, field: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    decoded = []
    for index, item in enumerate(value):
        try:
            decoded.append(decoder(item))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GeometryProposalProductionError(
                f"{field}[{index}]: {exc}"
            ) from exc
    return tuple(decoded)


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise GeometryProposalProductionError(f"{field} must be a SHA-256 digest")
    return value.lower()


def _base_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {"project_id": base.project_id, "version": base.version, "state_sha256": base.require_digest()}


def _base_from_dict(value: object) -> ProjectVersionRef:
    payload = _mapping(value, "project base")
    _exact(payload, {"project_id", "version", "state_sha256"}, "project base")
    return ProjectVersionRef(payload["project_id"], payload["version"], payload["state_sha256"])


def _record_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {"project_id": ref.project_id, "relative_path": ref.relative_path, "sha256": ref.sha256, "media_type": ref.media_type}


def _record_from_dict(value: object) -> ProjectRecordRef:
    payload = _mapping(value, "project record ref")
    _exact(payload, {"project_id", "relative_path", "sha256", "media_type"}, "project record ref")
    return ProjectRecordRef(payload["project_id"], payload["relative_path"], payload["sha256"], payload["media_type"])


def proposal_authoring_output(
    proposal: GeometryProgramProposal,
    *,
    selected_template_refs: tuple[str, ...] = (),
) -> dict[str, object]:
    """Encode a typed proposal as the strict provider response contract."""

    _strings(selected_template_refs, "selected_template_refs", allow_empty=True)
    return {
        "schema": _AUTHORING_OUTPUT_SCHEMA,
        "selected_template_refs": list(selected_template_refs),
        "proposal_body": _proposal_body(proposal),
    }
