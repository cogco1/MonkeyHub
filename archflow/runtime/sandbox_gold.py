"""One exact-base raw-request-to-accepted-sandbox orchestration.

The module owns no building-type defaults and no filesystem path.  A detached
model proposes project semantics, deterministic compilers produce geometry and
validation evidence, and only an injected project repository may persist or
promote the accepted result.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Mapping

from archflow.adapters.model_provider import (
    AsyncModelProvider,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPhase,
)
from archflow.adapters.sandbox_render import (
    SandboxRenderSet,
    render_paper_views,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProviderIdentity,
    GeometryProposalStatus,
    load_geometry_proposal_lineage,
    produce_geometry_program_proposal,
)
from archflow.evaluation.aesthetic import (
    AestheticSnapshot,
    ViewEvidence,
    evaluate_aesthetics,
)
from archflow.interaction import (
    CandidateApprovalMode,
    CandidateApprovalPolicy,
    CandidateApprovalReceipt,
    PlayerAuthorityError,
    parse_utc,
)
from archflow.project import (
    BranchRef,
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.realization import (
    DerivedVoxelView,
    HybridScene,
    SandboxArchiveDisposition,
    SandboxArchiveRecord,
    SandboxAssetPayload,
    SandboxRealizationReceipt,
    VoxelizationPolicy,
    derive_voxel_view,
    realize_geometry,
)
from archflow.runtime.candidate_assembly import (
    CandidateAssembly,
    CandidateDerivationArchive,
    CandidateDisposition,
    CandidateExecutablePlan,
    CandidatePolicyBinding,
    CandidatePolicyKind,
    PlanValueBinding,
)
from archflow.runtime.geometry_compiler import compile_geometry_program
from archflow.runtime.player_control import (
    PlayerControlError,
    assess_candidate_promotion_readiness,
    issue_disposable_automation_approval,
    validate_candidate_approval,
)
from archflow.state import (
    ArtifactRef,
    BuildPolicy,
    BuildStagingMode,
    CanonicalState,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CriterionRef,
    OperationalMarkovState,
    PolicyProvenance,
    ResourcePolicyMode,
    RevisionPolicy,
)
from archflow.state.candidate_program import (
    CandidateProgramProjection,
    CandidateProgramValue,
    CandidateValueFacet,
)
from archflow.state.geometry_program import (
    AssemblyKind,
    GeometryProgramProposal,
    digest_value,
)
from archflow.state.site_context import SiteBounds
from archflow.state.spatial import (
    MassingVolume,
    SpatialConnection,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
)
from archflow.submission import CandidateDelta, CandidateSubmission, Claim
from archflow.validation import (
    ArtifactPresentValidator,
    compile_building_program,
    validate_submission,
)
from archflow.validation.commitments import (
    CriterionObservation,
    CriterionOutcome,
    monitor_commitments,
)
from archflow.validation.use_scenarios import (
    ScenarioObservationBinding,
    UseScenarioValidator,
)
from archflow.validation.usability import (
    UseZoneEvidence,
    validate_usability,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_CONCEPT_SCHEMA = "SandboxArchitectConcept@1"
_PROPOSAL_SCHEMA = "SandboxArchitectProposal@1"
_MODEL_PROMPT_SCHEMA = "SandboxArchitectPrompt@1"
_COMMITMENT_REF = "commitment:maintain-egress"


class SandboxGoldError(RuntimeError):
    """The proof stopped at a typed provider, compiler, gate, or authority edge."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SandboxGoldError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise SandboxGoldError(f"{field} keys must be text")
    return dict(value)


def _exact(value: Mapping[str, Any], fields: set[str], field: str) -> None:
    if set(value) != fields:
        missing = sorted(fields - set(value))
        unexpected = sorted(set(value) - fields)
        raise SandboxGoldError(
            f"{field} schema drifted; missing={missing}; "
            f"unexpected={unexpected}"
        )


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise SandboxGoldError(f"{field} must be a portable identifier")
    return value


def _text(value: object, field: str, *, maximum: int = 2_000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise SandboxGoldError(f"{field} must be bounded non-empty text")
    return value


def _positive_number(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise SandboxGoldError(f"{field} must be a positive finite number")
    return float(value)


def _bounded_integer(
    value: object,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise SandboxGoldError(
            f"{field} must be an integer inside [{minimum}, {maximum}]"
        )
    return value


def _base_dict(base: ProjectVersionRef) -> dict[str, object]:
    return {
        "project_id": base.project_id,
        "version": base.version,
        "state_sha256": base.require_digest(),
    }


def _record_dict(ref: ProjectRecordRef) -> dict[str, object]:
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


@dataclass(frozen=True, slots=True)
class RawSandboxRequest:
    request_id: str
    prompt: str

    SCHEMA = "RawSandboxRequest@1"

    def __post_init__(self) -> None:
        _identifier(self.request_id, "request_id")
        _text(self.prompt, "prompt", maximum=4_000)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "request_id": self.request_id,
            "prompt": self.prompt,
            "geometry_operations": None,
            "footprint": None,
            "room_list": None,
            "topology": None,
            "palette": None,
            "platform_script": None,
        }

    @classmethod
    def from_dict(cls, value: object) -> RawSandboxRequest:
        payload = _mapping(value, "raw sandbox request")
        _exact(
            payload,
            {
                "schema",
                "request_id",
                "prompt",
                "geometry_operations",
                "footprint",
                "room_list",
                "topology",
                "palette",
                "platform_script",
            },
            "raw sandbox request",
        )
        if (
            payload["schema"] != cls.SCHEMA
            or any(
                payload[field] is not None
                for field in (
                    "geometry_operations",
                    "footprint",
                    "room_list",
                    "topology",
                    "palette",
                    "platform_script",
                )
            )
        ):
            raise SandboxGoldError("raw request contains a staged building answer")
        return cls(payload["request_id"], payload["prompt"])


@dataclass(frozen=True, slots=True)
class SandboxCandidateProof:
    variant: str
    model_receipt: ModelInvocationReceipt
    proposal: dict[str, Any]
    spatial_option_ref: ProjectRecordRef
    geometry_lineage_ref: ProjectRecordRef
    geometry_round_refs: tuple[ProjectRecordRef, ...]
    geometry_proposal_ref: ProjectRecordRef
    projection: CandidateProgramProjection
    assembly: CandidateAssembly
    geometry_program: object
    geometry_receipt: object
    scene: HybridScene
    realization_receipt: SandboxRealizationReceipt
    voxel_view: DerivedVoxelView
    render_set: SandboxRenderSet
    review_submission: CandidateSubmission
    usability_receipt: object
    hard_validation: object
    archive: CandidateDerivationArchive
    sandbox_archive: SandboxArchiveRecord
    build_policy: BuildPolicy
    build_policy_ref: ProjectRecordRef
    approval: CandidateApprovalReceipt | None = None
    readiness: object | None = None
    aesthetic: object | None = None


@dataclass(frozen=True, slots=True)
class PersistedSandboxGold:
    summary_ref: ProjectRecordRef
    rejected_ref: ProjectRecordRef | None
    accepted_ref: ProjectRecordRef
    decision_ref: ProjectRecordRef
    committed: ProjectVersionRef


@dataclass(frozen=True, slots=True)
class SandboxGoldInputs:
    request_ref: ProjectRecordRef
    scenario_ref: ProjectRecordRef
    approval_policy_ref: ProjectRecordRef
    authorization_event_ref: ProjectRecordRef
    asset_payload_refs: tuple[ProjectRecordRef, ...]


def _concept_output_schema() -> dict[str, object]:
    return {
        "schema": _CONCEPT_SCHEMA,
        "proposal_id": "portable identifier",
        "functions": [
            {
                "function_id": "portable identifier",
                "label": "text",
                "capacity": "positive integer",
                "area_m2": "positive number",
            }
        ],
        "relations": [
            {
                "source_function_id": "identifier",
                "target_function_id": "identifier",
                "kind": "adjacent|shared|separated",
            }
        ],
        "semantic_components": [
            {
                "component_id": "portable identifier",
                "semantic_kind": "model-derived portable identifier",
                "function_ids": ["function identifier"],
                "assembly_kind": "door|window|generic_hosted|null",
                "intent": "model-derived text",
                "rationale": "model-derived text",
            }
        ],
        "envelope": {
            "width_m": "positive number derived from the request",
            "depth_m": "positive number derived from the request",
            "clear_height_m": "positive number derived from the request",
        },
        "performance_requirements": {
            "minimum_clear_height_m": (
                "positive number no greater than the envelope clear height"
            ),
            "circulation_min_width_m": "positive number",
        },
        "spatial_option": {
            "grid_size_m": "positive number",
            "footprint_cells": [["integer x", "integer z"]],
            "levels": [
                {
                    "level_id": "portable identifier",
                    "base_y": "integer",
                    "height": "positive integer",
                }
            ],
            "volumes": [
                {
                    "volume_id": "portable identifier",
                    "minimum": ["integer x", "integer y", "integer z"],
                    "maximum": ["integer x", "integer y", "integer z"],
                    "level_ids": ["level identifier"],
                }
            ],
            "zones": [
                {
                    "zone_id": "portable identifier",
                    "function_ids": ["function identifier"],
                    "level_ids": ["level identifier"],
                    "volume_ids": ["volume identifier"],
                }
            ],
            "typology_hypothesis": "model-derived text",
            "rationale": "model-derived text",
        },
        "material_strategy": "short model-derived material intent",
        "rationale": "text",
    }


def _revision_output_schema() -> dict[str, object]:
    return {
        **_concept_output_schema(),
        "schema": _PROPOSAL_SCHEMA,
    }


def _strict_contract(
    properties: Mapping[str, object],
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(properties),
        "properties": dict(properties),
    }


def _array_contract(
    items: Mapping[str, object],
    *,
    minimum: int = 0,
    maximum: int,
    unique: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "type": "array",
        "items": dict(items),
        "minItems": minimum,
        "maxItems": maximum,
    }
    if unique:
        result["uniqueItems"] = True
    return result


def _concept_output_contract(*, revision: bool) -> dict[str, object]:
    identifier = {
        "type": "string",
        "pattern": _IDENTIFIER.pattern,
    }
    text = {"type": "string", "minLength": 1, "maxLength": 4_000}
    positive_number = {"type": "number", "exclusiveMinimum": 0}
    integer = {"type": "integer"}
    function = _strict_contract(
        {
            "function_id": identifier,
            "label": text,
            "capacity": {"type": "integer", "minimum": 1, "maximum": 10_000},
            "area_m2": positive_number,
        }
    )
    relation = _strict_contract(
        {
            "source_function_id": identifier,
            "target_function_id": identifier,
            "kind": {"enum": ["adjacent", "separated", "shared"]},
        }
    )
    component = _strict_contract(
        {
            "component_id": identifier,
            "semantic_kind": identifier,
            "function_ids": _array_contract(
                identifier,
                minimum=1,
                maximum=12,
                unique=True,
            ),
            "assembly_kind": {
                "enum": [
                    None,
                    AssemblyKind.DOOR.value,
                    AssemblyKind.GENERIC_HOSTED.value,
                    AssemblyKind.WINDOW.value,
                ]
            },
            "intent": text,
            "rationale": text,
        }
    )
    vector2 = {
        "type": "array",
        "items": integer,
        "minItems": 2,
        "maxItems": 2,
    }
    vector3 = {
        "type": "array",
        "items": integer,
        "minItems": 3,
        "maxItems": 3,
    }
    level = _strict_contract(
        {
            "level_id": identifier,
            "base_y": integer,
            "height": {"type": "integer", "minimum": 1},
        }
    )
    volume = _strict_contract(
        {
            "volume_id": identifier,
            "minimum": vector3,
            "maximum": vector3,
            "level_ids": _array_contract(
                identifier,
                minimum=1,
                maximum=128,
                unique=True,
            ),
        }
    )
    zone = _strict_contract(
        {
            "zone_id": identifier,
            "function_ids": _array_contract(
                identifier,
                minimum=1,
                maximum=12,
                unique=True,
            ),
            "level_ids": _array_contract(
                identifier,
                minimum=1,
                maximum=128,
                unique=True,
            ),
            "volume_ids": _array_contract(
                identifier,
                minimum=1,
                maximum=512,
                unique=True,
            ),
        }
    )
    return _strict_contract(
        {
            "schema": {
                "const": _PROPOSAL_SCHEMA if revision else _CONCEPT_SCHEMA,
            },
            "proposal_id": identifier,
            "functions": _array_contract(
                function,
                minimum=1,
                maximum=12,
            ),
            "relations": _array_contract(relation, maximum=66),
            "semantic_components": _array_contract(component, maximum=64),
            "envelope": _strict_contract(
                {
                    "width_m": positive_number,
                    "depth_m": positive_number,
                    "clear_height_m": positive_number,
                }
            ),
            "performance_requirements": _strict_contract(
                {
                    "minimum_clear_height_m": positive_number,
                    "circulation_min_width_m": positive_number,
                }
            ),
            "spatial_option": _strict_contract(
                {
                    "grid_size_m": positive_number,
                    "footprint_cells": _array_contract(
                        vector2,
                        minimum=1,
                        maximum=4_096,
                        unique=True,
                    ),
                    "levels": _array_contract(
                        level,
                        minimum=1,
                        maximum=128,
                    ),
                    "volumes": _array_contract(
                        volume,
                        minimum=1,
                        maximum=512,
                    ),
                    "zones": _array_contract(
                        zone,
                        minimum=1,
                        maximum=1_024,
                    ),
                    "typology_hypothesis": text,
                    "rationale": text,
                }
            ),
            "material_strategy": text,
            "rationale": text,
        }
    )


async def _model_proposal(
    repository: FilesystemProjectRepository,
    run: RunRef,
    provider: AsyncModelProvider,
    request: RawSandboxRequest,
    base: ProjectVersionRef,
    *,
    concept: dict[str, Any] | None = None,
    findings: tuple[str, ...] = (),
) -> ModelInvocationReceipt:
    revision = concept is not None
    variant = "revised" if revision else "concept"
    previous_output: dict[str, Any] | None = None
    previous_ref: ProjectRecordRef | None = None
    repair_issues: tuple[str, ...] = ()
    initial_identity: GeometryProposalProviderIdentity | None = None
    for round_index in range(1, 3):
        context = {
            "raw_request": request.to_dict(),
            "exact_base": _base_dict(base),
            "predecessor": concept,
            "hard_gate_findings": list(findings),
            "authoring_previous_output": previous_output,
            "authoring_repair_issues": list(repair_issues),
        }
        prompt = ModelInvocationRequest.create(
            request_id=(
                f"{request.request_id}-{variant}"
                if round_index == 1
                else f"{request.request_id}-{variant}-authoring-{round_index:02d}"
            ),
            phase=ModelPhase.ACTION_PROPOSAL,
            checkpoint_digest=(
                _digest(previous_output)
                if previous_output is not None
                else (_digest(concept) if revision else base.require_digest())
            ),
            context_digest=_digest(context),
            payload={
                "schema": _MODEL_PROMPT_SCHEMA,
                "authoring_variant": variant,
                "authoring_round": round_index,
                "repair_issues": list(repair_issues),
                "previous_output": previous_output,
                "instructions": (
                    "Act as the state-responsive Architect. Derive project "
                    "functions, capacities, areas, relations, semantic components, "
                    "dimensions, and performance requirements plus material intent "
                    "only from the raw request. A door, window, or other hosted "
                    "component must be declared here as one semantic component so "
                    "the later neutral geometry proposal can bind the same identity "
                    "to its typed assembly and member geometry. "
                    + (
                        "Revise the exact predecessor to answer every supplied "
                        "hard-gate finding while preserving honest spatial "
                        "lineage and mutually consistent program requirements."
                        if revision
                        else
                        "Produce a bounded concept-stage semantic and spatial "
                        "proposal without a platform command or acceptance decision."
                    )
                    + (
                        " Repair the exact previous output against every authoring "
                        "repair issue; do not redesign unrelated values."
                        if repair_issues
                        else ""
                    )
                    + " Return exactly the requested output schema. The strict "
                    "required_output_contract is authoritative: include every "
                    "required field, including the root rationale, and add no fields."
                ),
                "context": context,
                "required_output_schema": (
                    _revision_output_schema()
                    if revision
                    else _concept_output_schema()
                ),
                "required_output_contract": _concept_output_contract(
                    revision=revision
                ),
                "authority": {
                    "proposal_only": True,
                    "hard_gate_waiver": False,
                    "acceptance": False,
                    "canonical_write": False,
                },
            },
        )
        receipt = await provider.invoke(prompt)
        issue: str | None = None
        identity = _provider_identity(receipt)
        if initial_identity is None:
            initial_identity = identity
        elif identity != initial_identity:
            issue = (
                "Architect provider or model identity changed during authoring; "
                "silent substitution rejected"
            )
        if issue is None and receipt.status is not ModelInvocationStatus.SUCCESS:
            issue = f"Architect provider failed: {receipt.error_code}"
        output = receipt.output
        if issue is None and output is None:
            issue = "Architect provider returned no proposal"
        if issue is None:
            assert output is not None
            try:
                _validate_proposal(
                    json.loads(_canonical_json(output)),
                    revision=revision,
                )
            except SandboxGoldError as exc:
                issue = str(exc)
        invocation_ref = repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
            record_kind=f"architect-{variant}-invocation-{round_index:02d}",
            payload={
                "schema": "SandboxArchitectInvocationRecord@2",
                "variant": variant,
                "round_index": round_index,
                "predecessor_invocation_ref": (
                    None if previous_ref is None else previous_ref.uri
                ),
                "repair_issues": list(repair_issues),
                "validation_issue": issue,
                "receipt": receipt.to_dict(),
                "proposal_only": True,
                "platform_export_authority": False,
                "canonical_write_authority": False,
            },
        )
        if issue is None:
            return receipt
        if (
            identity != initial_identity
            or receipt.status is not ModelInvocationStatus.SUCCESS
            or output is None
            or round_index == 2
        ):
            raise SandboxGoldError(issue)
        previous_output = output
        previous_ref = invocation_ref
        repair_issues = (issue,)
    raise AssertionError("bounded Architect authoring loop did not terminate")


def _provider_identity(
    receipt: ModelInvocationReceipt,
) -> GeometryProposalProviderIdentity:
    return GeometryProposalProviderIdentity(
        receipt.provider_id,
        receipt.model_id,
        receipt.provider_version,
        receipt.provider_fingerprint,
    )


def _validate_proposal(
    value: object,
    *,
    revision: bool,
) -> dict[str, Any]:
    proposal = _mapping(value, "Architect proposal")
    common = {
        "schema",
        "proposal_id",
        "functions",
        "relations",
        "semantic_components",
        "envelope",
        "performance_requirements",
        "spatial_option",
        "material_strategy",
        "rationale",
    }
    expected = common
    _exact(proposal, expected, "Architect proposal")
    if proposal["schema"] != (
        _PROPOSAL_SCHEMA if revision else _CONCEPT_SCHEMA
    ):
        raise SandboxGoldError("Architect proposal schema is unsupported")
    _identifier(proposal["proposal_id"], "proposal_id")
    _text(proposal["material_strategy"], "material_strategy")
    _text(proposal["rationale"], "rationale")

    functions = proposal["functions"]
    if not isinstance(functions, list) or not 1 <= len(functions) <= 12:
        raise SandboxGoldError("functions must contain 1..12 items")
    function_ids = []
    total_area = 0.0
    for item in functions:
        function = _mapping(item, "function")
        _exact(
            function,
            {"function_id", "label", "capacity", "area_m2"},
            "function",
        )
        function_ids.append(
            _identifier(function["function_id"], "function_id")
        )
        _text(function["label"], "function label")
        _bounded_integer(function["capacity"], "capacity", 1, 10_000)
        total_area += _positive_number(function["area_m2"], "area_m2")
    if len(function_ids) != len(set(function_ids)):
        raise SandboxGoldError("function identifiers must be unique")

    components = proposal["semantic_components"]
    if not isinstance(components, list) or len(components) > 64:
        raise SandboxGoldError("semantic_components must be a bounded list")
    component_ids = []
    known_functions = set(function_ids)
    allowed_assembly_kinds = {item.value for item in AssemblyKind}
    for index, item in enumerate(components):
        component = _mapping(item, f"semantic_components[{index}]")
        _exact(
            component,
            {
                "component_id",
                "semantic_kind",
                "function_ids",
                "assembly_kind",
                "intent",
                "rationale",
            },
            f"semantic_components[{index}]",
        )
        component_id = _identifier(
            component["component_id"],
            f"semantic_components[{index}].component_id",
        )
        _identifier(
            _semantic_component_value_id(component_id),
            f"semantic_components[{index}] candidate value_id",
        )
        component_ids.append(component_id)
        _identifier(
            component["semantic_kind"],
            f"semantic_components[{index}].semantic_kind",
        )
        raw_function_ids = component["function_ids"]
        if not isinstance(raw_function_ids, list) or not raw_function_ids:
            raise SandboxGoldError(
                f"semantic_components[{index}].function_ids must be non-empty"
            )
        component_function_ids = tuple(
            _identifier(
                value,
                f"semantic_components[{index}].function_ids",
            )
            for value in raw_function_ids
        )
        if (
            len(component_function_ids) != len(set(component_function_ids))
            or not set(component_function_ids) <= known_functions
        ):
            raise SandboxGoldError(
                f"semantic_components[{index}].function_ids are invalid"
            )
        component["function_ids"] = sorted(component_function_ids)
        assembly_kind = component["assembly_kind"]
        if assembly_kind is not None and assembly_kind not in allowed_assembly_kinds:
            raise SandboxGoldError(
                f"semantic_components[{index}].assembly_kind is invalid"
            )
        _text(component["intent"], f"semantic_components[{index}].intent")
        _text(
            component["rationale"],
            f"semantic_components[{index}].rationale",
        )
    if len(component_ids) != len(set(component_ids)):
        raise SandboxGoldError("semantic component identifiers must be unique")

    relations = proposal["relations"]
    if not isinstance(relations, list) or len(relations) > 66:
        raise SandboxGoldError("relations must be a bounded list")
    known = set(function_ids)
    for item in relations:
        relation = _mapping(item, "relation")
        _exact(
            relation,
            {"source_function_id", "target_function_id", "kind"},
            "relation",
        )
        if (
            relation["source_function_id"] not in known
            or relation["target_function_id"] not in known
            or relation["source_function_id"]
            == relation["target_function_id"]
            or relation["kind"] not in {"adjacent", "shared", "separated"}
        ):
            raise SandboxGoldError("relation is invalid")

    envelope = _mapping(proposal["envelope"], "envelope")
    _exact(
        envelope,
        {"width_m", "depth_m", "clear_height_m"},
        "envelope",
    )
    width = _positive_number(envelope["width_m"], "width_m")
    depth = _positive_number(envelope["depth_m"], "depth_m")
    clear_height = _positive_number(
        envelope["clear_height_m"],
        "clear_height_m",
    )
    performance = _mapping(
        proposal["performance_requirements"],
        "performance_requirements",
    )
    _exact(
        performance,
        {
            "minimum_clear_height_m",
            "circulation_min_width_m",
        },
        "performance_requirements",
    )
    minimum_clear_height = _positive_number(
        performance["minimum_clear_height_m"],
        "minimum_clear_height_m",
    )
    _positive_number(
        performance["circulation_min_width_m"],
        "circulation_min_width_m",
    )
    if minimum_clear_height > clear_height:
        raise SandboxGoldError(
            "minimum clear height exceeds the envelope clear height"
        )
    if total_area > width * depth * 1.05:
        raise SandboxGoldError(
            "program areas exceed the proposed envelope by more than 5%"
        )
    proposal["functions"] = sorted(
        proposal["functions"],
        key=lambda item: item["function_id"],
    )
    proposal["relations"] = sorted(
        proposal["relations"],
        key=lambda item: (
            item["source_function_id"],
            item["target_function_id"],
            item["kind"],
        ),
    )
    proposal["semantic_components"] = sorted(
        proposal["semantic_components"],
        key=lambda item: item["component_id"],
    )
    _mapping(proposal["spatial_option"], "spatial_option")
    return proposal


def _spatial_option(
    proposal: Mapping[str, Any],
    receipt: ModelInvocationReceipt,
) -> SpatialOptionProposal:
    payload = _mapping(proposal["spatial_option"], "spatial_option")
    _exact(
        payload,
        {
            "grid_size_m",
            "footprint_cells",
            "levels",
            "volumes",
            "zones",
            "typology_hypothesis",
            "rationale",
        },
        "spatial_option",
    )
    source = (f"model-receipt:{receipt.receipt_id}",)
    raw_cells = payload["footprint_cells"]
    if not isinstance(raw_cells, list) or not 1 <= len(raw_cells) <= 4_096:
        raise SandboxGoldError("footprint_cells must contain 1..4096 cells")
    footprint_cells: list[tuple[int, int]] = []
    for index, cell in enumerate(raw_cells):
        if not isinstance(cell, list) or len(cell) != 2:
            raise SandboxGoldError(f"footprint_cells[{index}] must be [x, z]")
        footprint_cells.append(
            (
                _bounded_integer(cell[0], f"footprint_cells[{index}].x", -1_000_000, 1_000_000),
                _bounded_integer(cell[1], f"footprint_cells[{index}].z", -1_000_000, 1_000_000),
            )
        )

    raw_levels = payload["levels"]
    if not isinstance(raw_levels, list) or not 1 <= len(raw_levels) <= 128:
        raise SandboxGoldError("levels must contain 1..128 items")
    levels = []
    for index, raw in enumerate(raw_levels):
        item = _mapping(raw, f"levels[{index}]")
        _exact(item, {"level_id", "base_y", "height"}, f"levels[{index}]")
        levels.append(
            SpatialLevel(
                _identifier(item["level_id"], "level_id"),
                _bounded_integer(item["base_y"], "level base_y", -1_000_000, 1_000_000),
                _bounded_integer(item["height"], "level height", 1, 1_000_000),
                source,
            )
        )

    raw_volumes = payload["volumes"]
    if not isinstance(raw_volumes, list) or not 1 <= len(raw_volumes) <= 512:
        raise SandboxGoldError("volumes must contain 1..512 items")
    volumes = []
    for index, raw in enumerate(raw_volumes):
        item = _mapping(raw, f"volumes[{index}]")
        _exact(
            item,
            {"volume_id", "minimum", "maximum", "level_ids"},
            f"volumes[{index}]",
        )
        vectors = []
        for field in ("minimum", "maximum"):
            vector = item[field]
            if not isinstance(vector, list) or len(vector) != 3:
                raise SandboxGoldError(f"volume {field} must be a 3-vector")
            vectors.append(
                tuple(
                    _bounded_integer(
                        value,
                        f"volume {field}",
                        -1_000_000,
                        1_000_000,
                    )
                    for value in vector
                )
            )
        raw_level_ids = item["level_ids"]
        if not isinstance(raw_level_ids, list) or not raw_level_ids:
            raise SandboxGoldError("volume level_ids must be non-empty")
        volumes.append(
            MassingVolume(
                _identifier(item["volume_id"], "volume_id"),
                SiteBounds(vectors[0], vectors[1]),
                tuple(_identifier(value, "volume level_id") for value in raw_level_ids),
                source,
            )
        )

    known_functions = {item["function_id"] for item in proposal["functions"]}
    raw_zones = payload["zones"]
    if not isinstance(raw_zones, list) or not 1 <= len(raw_zones) <= 1_024:
        raise SandboxGoldError("zones must contain 1..1024 items")
    zones = []
    function_to_zones: dict[str, list[str]] = {}
    for index, raw in enumerate(raw_zones):
        item = _mapping(raw, f"zones[{index}]")
        _exact(
            item,
            {"zone_id", "function_ids", "level_ids", "volume_ids"},
            f"zones[{index}]",
        )
        zone_id = _identifier(item["zone_id"], "zone_id")
        function_ids = item["function_ids"]
        level_ids = item["level_ids"]
        volume_ids = item["volume_ids"]
        if not all(isinstance(values, list) and values for values in (function_ids, level_ids, volume_ids)):
            raise SandboxGoldError("zone references must be non-empty lists")
        function_refs = []
        for function_id in function_ids:
            function_id = _identifier(function_id, "zone function_id")
            if function_id not in known_functions:
                raise SandboxGoldError("zone cites an unknown function")
            function_to_zones.setdefault(function_id, []).append(zone_id)
            function_refs.append(f"program-node:{function_id}")
        zones.append(
            SpatialZone(
                zone_id,
                tuple(sorted(set(function_refs))),
                tuple(_identifier(value, "zone level_id") for value in level_ids),
                tuple(_identifier(value, "zone volume_id") for value in volume_ids),
                source,
            )
        )

    connections = []
    for index, relation in enumerate(proposal["relations"]):
        source_function = relation["source_function_id"]
        target_function = relation["target_function_id"]
        source_zones = sorted(function_to_zones.get(source_function, ()))
        target_zones = sorted(function_to_zones.get(target_function, ()))
        if not source_zones or not target_zones:
            raise SandboxGoldError("relation function lacks a spatial zone")
        for source_zone in source_zones:
            for target_zone in target_zones:
                if source_zone == target_zone:
                    continue
                connections.append(
                    SpatialConnection(
                        f"relation-{index:03d}-{source_zone}-{target_zone}",
                        source_zone,
                        target_zone,
                        (f"program-relation:{index:03d}:{relation['kind']}",),
                        False,
                        source,
                    )
                )

    return SpatialOptionProposal(
        option_id=f"{proposal['proposal_id']}-spatial",
        label=f"{proposal['proposal_id']} spatial option",
        program_scenario_ref=None,
        footprint_range_ref=None,
        grid_basis=SpatialGridBasis(
            _positive_number(payload["grid_size_m"], "grid_size_m"),
            "square-meter",
            source,
        ),
        footprint_cells=tuple(sorted(set(footprint_cells))),
        levels=tuple(sorted(levels, key=lambda item: item.level_id)),
        volumes=tuple(sorted(volumes, key=lambda item: item.volume_id)),
        zones=tuple(sorted(zones, key=lambda item: item.zone_id)),
        connections=tuple(sorted(connections, key=lambda item: item.connection_id)),
        constraint_responses=(),
        typology_hypothesis=_text(
            payload["typology_hypothesis"],
            "typology_hypothesis",
        ),
        palette_refs=(),
        rationale=_text(payload["rationale"], "spatial rationale"),
        responds_to_refs=tuple(
            sorted(f"program-node:{function_id}" for function_id in known_functions)
        ),
        expert_advice_refs=(),
        evidence_refs=source,
    )


def _semantic_component_value_id(component_id: str) -> str:
    return f"semantic-component-{component_id}"


def _projection(
    proposal: Mapping[str, Any],
    receipt: ModelInvocationReceipt,
    run: RunRef,
    spatial_option: SpatialOptionProposal,
    spatial_option_ref: ProjectRecordRef,
) -> CandidateProgramProjection:
    proposal_digest = _digest(proposal)
    source = (f"model-receipt:{receipt.receipt_id}",)
    derivation = (
        f"architect-proposal:{proposal_digest}",
        spatial_option.ref,
        spatial_option_ref.uri,
    )
    envelope = proposal["envelope"]
    functions = proposal["functions"]
    relations = proposal["relations"]
    values = [
        CandidateProgramValue.create(
            value_id="area-program",
            facet=CandidateValueFacet.AREA,
            value={
                item["function_id"]: item["area_m2"]
                for item in functions
            },
            source_refs=source,
            derivation_refs=derivation,
        ),
        CandidateProgramValue.create(
            value_id="coordinate-envelope",
            facet=CandidateValueFacet.COORDINATE,
            value={
                "minimum": [0, 0, 0],
                "maximum": [
                    envelope["width_m"],
                    envelope["clear_height_m"] + 1,
                    envelope["depth_m"],
                ],
            },
            source_refs=source,
            derivation_refs=derivation,
        ),
        CandidateProgramValue.create(
            value_id="dimension-envelope",
            facet=CandidateValueFacet.DIMENSION,
            value=envelope,
            source_refs=source,
            derivation_refs=derivation,
        ),
        CandidateProgramValue.create(
            value_id="function-program",
            facet=CandidateValueFacet.FUNCTION,
            value=functions,
            source_refs=source,
            derivation_refs=derivation,
        ),
        CandidateProgramValue.create(
            value_id="material-strategy",
            facet=CandidateValueFacet.MATERIAL,
            value={"intent": proposal["material_strategy"]},
            source_refs=source,
            derivation_refs=derivation,
        ),
        CandidateProgramValue.create(
            value_id="performance-requirements",
            facet=CandidateValueFacet.VALIDATION_INPUT,
            value=proposal["performance_requirements"],
            source_refs=source,
            derivation_refs=derivation,
        ),
        CandidateProgramValue.create(
            value_id="topology-relations",
            facet=CandidateValueFacet.TOPOLOGY,
            value=relations,
            source_refs=source,
            derivation_refs=derivation,
        ),
    ]
    values.extend(
        CandidateProgramValue.create(
            value_id=_semantic_component_value_id(component["component_id"]),
            facet=CandidateValueFacet.FUNCTION,
            value=component,
            source_refs=source,
            derivation_refs=derivation,
        )
        for component in proposal["semantic_components"]
    )
    developed_digest = _digest(
        {
            "raw_request": receipt.request.payload["context"]["raw_request"],
            "model_receipt": receipt.receipt_id,
            "proposal": proposal_digest,
        }
    )
    return CandidateProgramProjection(
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        developed_state_digest=developed_digest,
        portfolio_id=f"portfolio-{proposal_digest[:16]}",
        portfolio_digest=_digest(
            {"proposal": proposal_digest, "kind": "model-derived"}
        ),
        selected_branch_id=f"branch-{proposal_digest[:16]}",
        selected_revision_id=f"revision-{proposal_digest[:16]}",
        selected_revision_digest=proposal_digest,
        selected_option_ref=spatial_option.ref,
        selection_transition_id=f"selection-{proposal_digest[:16]}",
        selection_decision_ref=f"model-receipt:{receipt.receipt_id}",
        values=tuple(sorted(values, key=lambda item: item.value_id)),
    )


def _program(proposal: Mapping[str, Any]):
    envelope = proposal["envelope"]
    performance = proposal["performance_requirements"]
    entrance_count = sum(
        1
        for component in proposal["semantic_components"]
        if component["assembly_kind"] == AssemblyKind.DOOR.value
    )
    return compile_building_program(
        {
            "use": proposal["proposal_id"],
            "width_blocks": envelope["width_m"],
            "depth_blocks": envelope["depth_m"],
            "required_spaces": [
                item["function_id"] for item in proposal["functions"]
            ],
            "minimum_clear_height": performance[
                "minimum_clear_height_m"
            ],
            "entrance_count": entrance_count,
            "circulation_min_width": performance[
                "circulation_min_width_m"
            ],
            "hard_requirements": ["maintain-evidence-bound-egress"],
            "soft_preferences": [proposal["material_strategy"]],
            "prohibitions": [],
        }
    )


def _validate_semantic_geometry_bindings(
    proposal: Mapping[str, Any],
    geometry: GeometryProgramProposal,
) -> str:
    """Require one identity across model semantics and hosted geometry."""

    producer_by_object = {
        object_id: operation
        for operation in geometry.operations
        for object_id in operation.output_object_ids
    }
    component_binding_ids: set[str] = set()
    claimed_member_objects: set[str] = set()
    digest_records: list[dict[str, object]] = []
    for component in proposal["semantic_components"]:
        value_id = _semantic_component_value_id(component["component_id"])
        covering = [
            binding
            for binding in geometry.semantic_bindings
            if value_id in binding.candidate_value_ids
        ]
        if not covering:
            raise SandboxGoldError(
                f"semantic component {component['component_id']} has no geometry binding"
            )
        assembly_kind = component["assembly_kind"]
        if assembly_kind is None:
            digest_records.append(
                {
                    "component": component,
                    "binding_ids": sorted(item.binding_id for item in covering),
                    "assembly": None,
                }
            )
            continue
        dedicated = [
            binding
            for binding in covering
            if binding.candidate_value_ids == (value_id,)
        ]
        if len(dedicated) != 1:
            raise SandboxGoldError(
                f"semantic component {component['component_id']} requires exactly "
                "one dedicated geometry binding"
            )
        binding = dedicated[0]
        component_binding_ids.add(binding.binding_id)
        assemblies = [
            assembly
            for assembly in geometry.assemblies
            if assembly.kind.value == assembly_kind
            and assembly.semantic_binding_ids == (binding.binding_id,)
        ]
        if len(assemblies) != 1:
            raise SandboxGoldError(
                f"semantic component {component['component_id']} requires exactly "
                f"one bound {assembly_kind} assembly"
            )
        assembly = assemblies[0]
        member_objects = {
            object_id
            for member in assembly.members
            for object_id in member.object_ids
        }
        if claimed_member_objects & member_objects:
            raise SandboxGoldError(
                "hosted semantic components reuse member geometry identities"
            )
        claimed_member_objects.update(member_objects)
        assembly_objects = {assembly.host_object_id, *member_objects}
        if not assembly_objects <= set(binding.object_ids):
            raise SandboxGoldError(
                f"assembly {assembly.assembly_id} escapes its semantic binding"
            )
        for object_id in assembly_objects:
            producer = producer_by_object.get(object_id)
            if (
                producer is None
                or binding.binding_id not in producer.semantic_binding_ids
            ):
                raise SandboxGoldError(
                    f"assembly {assembly.assembly_id} object {object_id} is not "
                    "produced under the same semantic binding"
                )
        digest_records.append(
            {
                "component": component,
                "binding": binding.to_dict(),
                "assembly": assembly.to_dict(),
            }
        )
    for assembly in geometry.assemblies:
        if (
            len(assembly.semantic_binding_ids) != 1
            or assembly.semantic_binding_ids[0] not in component_binding_ids
        ):
            raise SandboxGoldError(
                f"hosted assembly {assembly.assembly_id} has no model-authored "
                "semantic component identity"
            )
    return _digest(digest_records)


def _approval_policy(
    repository: FilesystemProjectRepository,
    policy_ref: ProjectRecordRef,
    authorization_event_ref: ProjectRecordRef,
    *,
    evidence_ref: str,
) -> tuple[CandidateApprovalPolicy, str]:
    policy_payload = _mapping(
        repository.load_json(policy_ref),
        "sandbox approval policy input",
    )
    _exact(
        policy_payload,
        {
            "schema",
            "authority_ids",
            "mode",
            "max_validity_seconds",
            "allows_disposable_automation",
        },
        "sandbox approval policy input",
    )
    if policy_payload["schema"] != "SandboxApprovalPolicyInput@1":
        raise SandboxGoldError("sandbox approval policy schema changed")
    authority_ids = policy_payload["authority_ids"]
    if not isinstance(authority_ids, list) or not authority_ids:
        raise SandboxGoldError("approval policy requires authority_ids")
    authorities = tuple(
        _identifier(value, "approval authority_id")
        for value in authority_ids
    )
    event_payload = _mapping(
        repository.load_json(authorization_event_ref),
        "sandbox authorization event",
    )
    _exact(
        event_payload,
        {"schema", "authority_id", "action", "scope", "evidence_refs"},
        "sandbox authorization event",
    )
    if (
        event_payload["schema"] != "SandboxAuthorizationEvent@1"
        or event_payload["action"] != "preauthorize-disposable-sandbox"
        or event_payload["scope"] != "candidate-promotion"
        or event_payload["authority_id"] not in authorities
        or not isinstance(event_payload["evidence_refs"], list)
        or not event_payload["evidence_refs"]
    ):
        raise SandboxGoldError("sandbox authorization event is not applicable")
    authority_id = _identifier(
        event_payload["authority_id"],
        "authorization event authority_id",
    )
    return CandidateApprovalPolicy(
        policy_ref=policy_ref.uri,
        authority_ids=authorities,
        mode=CandidateApprovalMode(policy_payload["mode"]),
        max_validity_seconds=policy_payload["max_validity_seconds"],
        allows_disposable_automation=policy_payload[
            "allows_disposable_automation"
        ],
        authorization_event_ref=authorization_event_ref.uri,
        evidence_refs=(
            evidence_ref,
            policy_ref.uri,
            authorization_event_ref.uri,
            *(str(value) for value in event_payload["evidence_refs"]),
        ),
    ), authority_id


def _asset_payloads(
    repository: FilesystemProjectRepository,
    refs: tuple[ProjectRecordRef, ...],
) -> tuple[SandboxAssetPayload, ...]:
    payloads = []
    for ref in refs:
        payload = _mapping(repository.load_json(ref), "sandbox asset input")
        _exact(
            payload,
            {
                "schema",
                "asset_id",
                "vertices",
                "faces",
                "media_type",
                "native_unit",
                "sockets",
                "provenance_refs",
            },
            "sandbox asset input",
        )
        if (
            payload["schema"] != "SandboxAssetInput@1"
            or payload["media_type"] != "model/gltf+json"
            or payload["native_unit"] != "m"
            or payload["sockets"] != ["origin"]
            or not isinstance(payload["provenance_refs"], list)
            or not payload["provenance_refs"]
        ):
            raise SandboxGoldError("sandbox asset input metadata is invalid")
        payloads.append(
            SandboxAssetPayload(
                asset_id=_identifier(payload["asset_id"], "asset_id"),
                vertices=tuple(
                    tuple(float(value) for value in vertex)
                    for vertex in payload["vertices"]
                ),
                faces=tuple(tuple(face) for face in payload["faces"]),
            )
        )
    if len({item.asset_id for item in payloads}) != len(payloads):
        raise SandboxGoldError("sandbox asset ids must be unique")
    return tuple(sorted(payloads, key=lambda item: item.asset_id))


def _sandbox_build_policy(
    projection: CandidateProgramProjection,
    *,
    authority_id: str,
    request_ref: ProjectRecordRef,
    scenario_ref: ProjectRecordRef,
) -> BuildPolicy:
    """Compile the disposable sandbox build policy from loaded case inputs."""

    provenance = PolicyProvenance(
        authority_id=authority_id,
        source_refs=(request_ref.uri,),
        assumption_refs=(),
        compiler_id="sandbox-gold",
        base_state_sha256=projection.base.require_digest(),
    )
    return BuildPolicy(
        project_id=projection.project_id,
        run_id=projection.run_id,
        base=projection.base,
        brief_digest=request_ref.sha256,
        program_digest=projection.projection_digest,
        site_context_digest=scenario_ref.sha256,
        compiler_id="sandbox-gold",
        compiler_version="sandbox-gold-1",
        resource_mode=ResourcePolicyMode.CREATIVE,
        staging_mode=BuildStagingMode.SINGLE_PASS,
        disposable_sandbox=True,
        unbounded_resources=False,
        policy_provenance=provenance,
        assumptions=(),
        availability=(),
        demands=(),
        protected_rules=(),
        budget_limits=(),
        staging_assumptions=(),
        constraints=(),
        obligations=(),
        evidence_refs=(request_ref.uri, scenario_ref.uri),
    )


def _candidate_assembly(
    projection: CandidateProgramProjection,
    program_digest: str,
    scene_digest: str,
    view_digest: str,
    *,
    variant: str,
    approval_policy: CandidateApprovalPolicy,
    build_policy: BuildPolicy,
    build_policy_ref: str,
    evidence_ref: str,
) -> CandidateAssembly:
    payload = {
        "geometry_program_digest": program_digest,
        "scene_digest": scene_digest,
        "voxel_view_digest": view_digest,
    }
    plan = CandidateExecutablePlan.create(
        plan_id=f"{variant}-sandbox-plan",
        projection=projection,
        payload=payload,
        bindings=(
            PlanValueBinding(
                "/geometry_program_digest",
                (
                    "coordinate-envelope",
                    "dimension-envelope",
                    "topology-relations",
                ),
                (evidence_ref,),
            ),
            PlanValueBinding(
                "/scene_digest",
                ("function-program", "material-strategy"),
                (evidence_ref,),
            ),
            PlanValueBinding(
                "/voxel_view_digest",
                ("area-program", "function-program"),
                (evidence_ref,),
            ),
        ),
    )
    policies = tuple(
        sorted(
            (
                CandidatePolicyBinding(
                    kind=CandidatePolicyKind.APPROVAL,
                    policy_ref=approval_policy.policy_ref,
                    policy_digest=approval_policy.policy_digest,
                    evidence_refs=(evidence_ref,),
                ),
                CandidatePolicyBinding(
                    kind=CandidatePolicyKind.BUILD,
                    policy_ref=build_policy_ref,
                    policy_digest=build_policy.policy_digest,
                    evidence_refs=(evidence_ref,),
                ),
            ),
            key=lambda item: item.kind.value,
        )
    )
    submission_id = (
        f"{variant}-candidate-{projection.projection_digest[:16]}"
    )
    submission = CandidateSubmission(
        submission_id=submission_id,
        base=projection.base,
        workspace_id=f"{variant}-sandbox-workspace",
        intent=(
            "Review the exact model-derived platform-neutral sandbox "
            "candidate."
        ),
        delta=CandidateDelta(),
        claims=tuple(
            Claim(
                key=f"candidate.{item.value_id}",
                value=item.value_json,
                evidence_refs=tuple(
                    dict.fromkeys(
                        (*item.source_refs, *item.derivation_refs)
                    )
                ),
            )
            for item in projection.values
        ),
        evidence_refs=(evidence_ref, projection.selected_option_ref),
    )
    return CandidateAssembly(
        projection=projection,
        plan=plan,
        policies=policies,
        submission=submission,
    )


def _receipt_dict(receipt: object) -> dict[str, object]:
    return {
        "receipt_id": receipt.receipt_id,
        "observation_id": receipt.observation_id,
        "program_schema": receipt.program_schema,
        "passed": receipt.passed,
        "findings": [
            {
                "code": item.code,
                "gate": item.gate.value,
                "message": item.message,
                "measured": item.measured,
                "threshold": item.threshold,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in receipt.findings
        ],
    }


def _validation_dict(receipt: object) -> dict[str, object]:
    return {
        "receipt_id": receipt.receipt_id,
        "submission_id": receipt.submission_id,
        "submission_digest": receipt.submission_digest,
        "checked_state": _base_dict(receipt.checked_state),
        "passed": receipt.passed,
        "findings": [
            {
                "code": item.code,
                "message": item.message,
                "severity": item.severity.value,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in receipt.findings
        ],
    }


def _derive_use_zones(
    spatial: SpatialOptionProposal,
    spatial_ref: ProjectRecordRef,
    observation: object,
) -> tuple[UseZoneEvidence, ...]:
    volumes = {item.volume_id: item for item in spatial.volumes}
    derived: list[UseZoneEvidence] = []
    for zone in spatial.zones:
        zone_volumes = tuple(volumes[volume_id] for volume_id in zone.volume_ids)
        matching_regions = []
        for region in observation.connected_regions:
            if any(
                any(
                    all(
                        volume.bounds.minimum[axis]
                        <= float(cell[axis]) + 0.5
                        <= volume.bounds.maximum[axis]
                        for axis in range(3)
                    )
                    for cell in region.cells
                )
                for volume in zone_volumes
            ):
                matching_regions.append(region)
        for program_ref in zone.program_node_refs:
            prefix = "program-node:"
            if not program_ref.startswith(prefix):
                continue
            space = program_ref[len(prefix):]
            for region in matching_regions:
                derived.append(
                    UseZoneEvidence(
                        space=space,
                        region_id=region.region_id,
                        evidence_refs=(
                            spatial_ref.uri,
                            f"spatial-zone:{zone.zone_id}",
                            *(f"spatial-volume:{item.volume_id}" for item in zone_volumes),
                            f"sandbox-region:{region.region_id}",
                        ),
                    )
                )
    return tuple(
        sorted(
            derived,
            key=lambda item: (item.space, item.region_id, item.evidence_refs),
        )
    )


async def _candidate_proof(
    repository: FilesystemProjectRepository,
    provider: AsyncModelProvider,
    receipt: ModelInvocationReceipt,
    run: RunRef,
    *,
    variant: str,
    detailed: bool,
    request_ref: ProjectRecordRef,
    scenario_ref: ProjectRecordRef,
    approval_policy: CandidateApprovalPolicy,
    authority_id: str,
    asset_payload_refs: tuple[ProjectRecordRef, ...],
    asset_payloads: tuple[SandboxAssetPayload, ...],
    predecessor_ref: str | None = None,
) -> SandboxCandidateProof:
    assert receipt.output is not None
    proposal = _validate_proposal(receipt.output, revision=detailed)
    spatial = _spatial_option(proposal, receipt)
    spatial_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind=f"{variant}-spatial-option",
        payload=spatial.to_dict(),
    )
    projection = _projection(proposal, receipt, run, spatial, spatial_ref)
    assets = {item.asset_id: item.payload_digest for item in asset_payloads}
    produced = await produce_geometry_program_proposal(
        repository,
        provider,
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        spatial_option_ref=spatial_ref,
        projection=projection,
        required_commitment_refs=(_COMMITMENT_REF,),
        provider_identity=GeometryProposalProviderIdentity(
            receipt.provider_id,
            receipt.model_id,
            receipt.provider_version,
            receipt.provider_fingerprint,
        ),
        policy=GeometryProposalPolicy(2),
        template_refs=asset_payload_refs,
        available_asset_digests=assets,
    )
    if (
        produced.status is not GeometryProposalStatus.ACCEPTED
        or produced.proposal is None
        or produced.proposal_ref is None
        or produced.program is None
    ):
        raise SandboxGoldError(
            "record-driven geometry proposal was not accepted: "
            f"{produced.status.value}; lineage={produced.lineage_ref.uri}"
        )
    loaded_geometry = load_geometry_proposal_lineage(
        repository,
        produced.lineage_ref,
    )
    if (
        loaded_geometry.proposal is None
        or loaded_geometry.proposal.proposal_digest
        != produced.proposal.proposal_digest
    ):
        raise SandboxGoldError("geometry proposal lineage failed immediate reload")
    _validate_semantic_geometry_bindings(proposal, loaded_geometry.proposal)
    compilation = compile_geometry_program(
        projection,
        produced.proposal,
        active_commitment_refs=(_COMMITMENT_REF,),
        available_asset_digests=assets,
    )
    if (
        compilation.program is None
        or compilation.program.program_digest != produced.program.program_digest
    ):
        raise SandboxGoldError(
            f"geometry compilation rejected: {compilation.receipt.issues}"
        )
    realized = realize_geometry(
        produced.program,
        workspace_id=f"{variant}-sandbox-workspace",
        asset_payloads=asset_payloads,
    )
    if realized.scene is None:
        raise SandboxGoldError(
            f"sandbox realization rejected: {realized.receipt.issues}"
        )
    view = derive_voxel_view(
        realized.scene,
        realized.receipt,
        policy=VoxelizationPolicy(default_resolution=1.0),
    )
    render_set = render_paper_views(realized.scene)
    validation_program = _program(proposal)
    observation = replace(view.to_observation(), base_state=run.base)
    if not observation.connected_regions:
        raise SandboxGoldError("sandbox observation has no walkable region")
    region = observation.connected_regions[0]
    zones = _derive_use_zones(
        spatial,
        spatial_ref,
        observation,
    )
    build_policy = _sandbox_build_policy(
        projection,
        authority_id=authority_id,
        request_ref=request_ref,
        scenario_ref=scenario_ref,
    )
    build_policy_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_RECORD,
            run_id=run.run_id,
        ),
        record_kind=f"{variant}-sandbox-build-policy",
        payload=build_policy.to_dict(),
    )
    assembly = _candidate_assembly(
        projection,
        produced.program.program_digest,
        realized.scene.scene_digest,
        view.view_digest,
        variant=variant,
        approval_policy=approval_policy,
        build_policy=build_policy,
        build_policy_ref=build_policy_ref.uri,
        evidence_ref=f"model-receipt:{receipt.receipt_id}",
    )
    review_submission = CandidateSubmission(
        submission_id=f"{assembly.submission.submission_id}-realized",
        base=assembly.submission.base,
        workspace_id=assembly.submission.workspace_id,
        intent=assembly.submission.intent,
        delta=CandidateDelta(artifacts_add=(view.artifact,)),
        claims=assembly.submission.claims,
        evidence_refs=tuple(
            dict.fromkeys(
                (*assembly.submission.evidence_refs, view.artifact.artifact_id)
            )
        ),
    )
    state = CanonicalState(ref=run.base)
    binding = ScenarioObservationBinding.from_sandbox_realization(
        binding_id=f"{variant}-sandbox-observation",
        program=validation_program,
        observation=observation,
        candidate_program_digest=projection.projection_digest,
        geometry_program_digest=produced.program.program_digest,
        realization_receipt_digest=realized.receipt.receipt_digest,
        evidence_refs=(
            f"sandbox-scene:{realized.scene.scene_digest}",
            f"sandbox-voxel:{view.view_digest}",
        ),
    )
    hard_validation = validate_submission(
        state,
        review_submission,
        (
            ArtifactPresentValidator(),
            UseScenarioValidator(
                validation_program,
                observation,
                zones,
                observation_binding=binding,
                candidate_program_digest=projection.projection_digest,
                geometry_program_digest=produced.program.program_digest,
                realization_receipt_digest=(
                    realized.receipt.receipt_digest
                ),
            ),
        ),
    )
    usability = validate_usability(
        validation_program,
        observation,
        use_zones=zones,
    )
    decision = {
        "variant": variant,
        "usability_receipt_id": usability.receipt_id,
        "hard_validation_receipt_id": hard_validation.receipt_id,
        "passed": usability.passed and hard_validation.passed,
        "predecessor_ref": predecessor_ref,
    }
    decision_digest = _digest(decision)
    disposition = (
        CandidateDisposition.REVISED
        if detailed
        else (
            CandidateDisposition.ASSEMBLED
            if decision["passed"]
            else CandidateDisposition.REJECTED
        )
    )
    archive = CandidateDerivationArchive(
        assembly=assembly,
        disposition=disposition,
        execution=None,
        predecessor_candidate_ref=predecessor_ref,
        review_refs=(
            f"hard-validation:{hard_validation.receipt_id}",
            f"usability:{usability.receipt_id}",
        ),
        evidence_refs=(
            f"model-receipt:{receipt.receipt_id}",
            f"sandbox-scene:{realized.scene.scene_digest}",
            f"sandbox-voxel:{view.view_digest}",
        ),
        rationale=(
            "The concept is retained after deterministic hard-gate review."
            if not detailed
            else
            "The exact predecessor was revised against detached hard-gate findings."
        ),
    )
    sandbox_archive = SandboxArchiveRecord(
        archive_id=f"{variant}-sandbox-archive",
        disposition=(
            SandboxArchiveDisposition.REPAIRED
            if detailed and decision["passed"]
            else SandboxArchiveDisposition.REJECTED
        ),
        geometry_program_digest=compilation.program.program_digest,
        realization_receipt_digest=realized.receipt.receipt_digest,
        scene_digest=realized.scene.scene_digest,
        decision_receipt_digest=decision_digest,
        evidence_refs=tuple(
            sorted(
                (
                    f"hard-validation:{hard_validation.receipt_id}",
                    f"model-receipt:{receipt.receipt_id}",
                    f"sandbox-scene:{realized.scene.scene_digest}",
                    f"sandbox-voxel:{view.view_digest}",
                    f"usability:{usability.receipt_id}",
                )
            )
        ),
    )
    return SandboxCandidateProof(
        variant=variant,
        model_receipt=receipt,
        proposal=proposal,
        spatial_option_ref=spatial_ref,
        geometry_lineage_ref=produced.lineage_ref,
        geometry_round_refs=produced.round_refs,
        geometry_proposal_ref=produced.proposal_ref,
        projection=projection,
        assembly=assembly,
        geometry_program=produced.program,
        geometry_receipt=compilation.receipt,
        scene=realized.scene,
        realization_receipt=realized.receipt,
        voxel_view=view,
        render_set=render_set,
        review_submission=review_submission,
        usability_receipt=usability,
        hard_validation=hard_validation,
        archive=archive,
        sandbox_archive=sandbox_archive,
        build_policy=build_policy,
        build_policy_ref=build_policy_ref,
    )


def _candidate_record(proof: SandboxCandidateProof) -> dict[str, object]:
    return {
        "schema": "SandboxGoldCandidateRecord@2",
        "variant": proof.variant,
        "model_receipt": proof.model_receipt.to_dict(),
        "proposal": proof.proposal,
        "spatial_option_ref": _record_dict(proof.spatial_option_ref),
        "geometry_lineage_ref": _record_dict(proof.geometry_lineage_ref),
        "geometry_round_refs": [
            _record_dict(ref) for ref in proof.geometry_round_refs
        ],
        "geometry_proposal_ref": _record_dict(proof.geometry_proposal_ref),
        "projection": proof.projection.to_dict(),
        "assembly": proof.assembly.to_dict(),
        "geometry_program": proof.geometry_program.to_dict(),
        "geometry_receipt": proof.geometry_receipt.to_dict(),
        "scene": proof.scene.to_dict(),
        "realization_receipt": proof.realization_receipt.to_dict(),
        "voxel_view": proof.voxel_view.to_dict(),
        "render_set": proof.render_set.to_dict(),
        "review_submission_id": proof.review_submission.submission_id,
        "usability": _receipt_dict(proof.usability_receipt),
        "hard_validation": _validation_dict(proof.hard_validation),
        "archive": proof.archive.to_dict(),
        "sandbox_archive": proof.sandbox_archive.to_dict(),
        "approval": (
            None if proof.approval is None else proof.approval.to_dict()
        ),
        "readiness": (
            None if proof.readiness is None else proof.readiness.to_dict()
        ),
        "aesthetic": (
            None if proof.aesthetic is None else proof.aesthetic.to_dict()
        ),
        "platform_export_authority": False,
        "canonical_write_authority": False,
    }


def _finding_codes(proof: SandboxCandidateProof) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *(item.code for item in proof.usability_receipt.findings),
                *(item.code for item in proof.hard_validation.findings),
            )
        )
    )


def load_sandbox_gold_inputs(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
) -> SandboxGoldInputs:
    """Discover one complete authorized sandbox case without path guessing."""

    records = repository.list_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
    )
    by_schema: dict[str, list[ProjectRecordRef]] = {}
    for ref in records:
        schema = repository.load_json(ref).get("schema")
        if isinstance(schema, str):
            by_schema.setdefault(schema, []).append(ref)

    def exactly_one(schema: str) -> ProjectRecordRef:
        matches = by_schema.get(schema, [])
        if len(matches) != 1:
            raise SandboxGoldError(
                f"sandbox case requires exactly one {schema} input"
            )
        return matches[0]

    assets = tuple(
        sorted(
            by_schema.get("SandboxAssetInput@1", []),
            key=lambda ref: ref.uri,
        )
    )
    if not assets:
        raise SandboxGoldError(
            "sandbox case requires at least one SandboxAssetInput@1 input"
        )
    return SandboxGoldInputs(
        request_ref=exactly_one(RawSandboxRequest.SCHEMA),
        scenario_ref=exactly_one("SandboxScenarioInput@1"),
        approval_policy_ref=exactly_one("SandboxApprovalPolicyInput@1"),
        authorization_event_ref=exactly_one("SandboxAuthorizationEvent@1"),
        asset_payload_refs=assets,
    )


def _scenario_window(
    repository: FilesystemProjectRepository,
    scenario_ref: ProjectRecordRef,
) -> tuple[str, str]:
    payload = _mapping(
        repository.load_json(scenario_ref),
        "sandbox scenario input",
    )
    _exact(
        payload,
        {"schema", "issued_at_utc", "valid_until_utc"},
        "sandbox scenario input",
    )
    if payload["schema"] != "SandboxScenarioInput@1":
        raise SandboxGoldError("sandbox scenario input schema changed")
    return (
        _text(payload["issued_at_utc"], "issued_at_utc"),
        _text(payload["valid_until_utc"], "valid_until_utc"),
    )


async def execute_sandbox_gold(
    repository: FilesystemProjectRepository,
    run: RunRef,
    provider: AsyncModelProvider,
    *,
    request_ref: ProjectRecordRef,
    scenario_ref: ProjectRecordRef,
    approval_policy_ref: ProjectRecordRef,
    authorization_event_ref: ProjectRecordRef,
    asset_payload_refs: tuple[ProjectRecordRef, ...],
) -> PersistedSandboxGold:
    """Run, persist, verify, and atomically promote one sandbox Gold."""

    if not isinstance(repository, FilesystemProjectRepository):
        raise TypeError(
            "repository must be a FilesystemProjectRepository"
        )
    if not isinstance(run, RunRef):
        raise TypeError("run must be a RunRef")
    if run.base != repository.read_head():
        raise SandboxGoldError("run is not based on current project HEAD")
    refs = (
        request_ref,
        scenario_ref,
        approval_policy_ref,
        authorization_event_ref,
        *asset_payload_refs,
    )
    if (
        any(not isinstance(ref, ProjectRecordRef) for ref in refs)
        or any(ref.project_id != run.project_id for ref in refs)
        or tuple(ref.uri for ref in asset_payload_refs)
        != tuple(sorted(set(ref.uri for ref in asset_payload_refs)))
    ):
        raise SandboxGoldError("sandbox input records are invalid or cross-project")
    request = RawSandboxRequest.from_dict(repository.load_json(request_ref))
    issued_at_utc, valid_until_utc = _scenario_window(
        repository,
        scenario_ref,
    )
    policy, authority_id = _approval_policy(
        repository,
        approval_policy_ref,
        authorization_event_ref,
        evidence_ref=request_ref.uri,
    )
    asset_payloads = _asset_payloads(repository, asset_payload_refs)
    concept_receipt = await _model_proposal(
        repository,
        run,
        provider,
        request,
        run.base,
    )
    run_provider_identity = _provider_identity(concept_receipt)
    concept = await _candidate_proof(
        repository,
        provider,
        concept_receipt,
        run,
        variant="concept",
        detailed=False,
        request_ref=request_ref,
        scenario_ref=scenario_ref,
        approval_policy=policy,
        authority_id=authority_id,
        asset_payload_refs=asset_payload_refs,
        asset_payloads=asset_payloads,
    )
    concept_findings = _finding_codes(concept)
    rejected_ref: ProjectRecordRef | None = None
    if concept_findings:
        rejected_ref = repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_CANDIDATE,
                run_id=run.run_id,
            ),
            record_kind="sandbox-candidate-rejected",
            payload=_candidate_record(concept),
        )
        revision_receipt = await _model_proposal(
            repository,
            run,
            provider,
            request,
            run.base,
            concept=concept.proposal,
            findings=concept_findings,
        )
        if not run_provider_identity.matches(revision_receipt):
            raise SandboxGoldError(
                "Architect provider or model identity changed; "
                "silent substitution rejected"
            )
        accepted = await _candidate_proof(
            repository,
            provider,
            revision_receipt,
            run,
            variant="revised",
            detailed=True,
            request_ref=request_ref,
            scenario_ref=scenario_ref,
            approval_policy=policy,
            authority_id=authority_id,
            asset_payload_refs=asset_payload_refs,
            asset_payloads=asset_payloads,
            predecessor_ref=rejected_ref.uri,
        )
    else:
        accepted = concept
    if (
        not accepted.usability_receipt.passed
        or not accepted.hard_validation.passed
    ):
        failed_ref = repository.put_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_CANDIDATE,
                run_id=run.run_id,
            ),
            record_kind="sandbox-candidate-revision-failed",
            payload=_candidate_record(accepted),
        )
        raise SandboxGoldError(
            f"revised candidate remained rejected: {failed_ref.uri}"
        )

    # The approval must pass the fail-closed issuance gate: dual-policy
    # (approval + disposable build) authority and the policy validity cap
    # are checked there, so a scenario window beyond max_validity_seconds
    # is rejected instead of self-certified.
    try:
        approval = issue_disposable_automation_approval(
            accepted.assembly,
            policy,
            accepted.build_policy,
            build_policy_ref=accepted.build_policy_ref.uri,
            authority_id=authority_id,
            issued_at_utc=issued_at_utc,
            valid_until_utc=valid_until_utc,
        )
    except (PlayerAuthorityError, PlayerControlError) as exc:
        raise SandboxGoldError(
            f"sandbox approval issuance rejected: {exc}"
        ) from exc
    validate_candidate_approval(
        accepted.assembly,
        policy,
        approval,
        now_utc=issued_at_utc,
    )
    branch = BranchRef(run=run, branch_id="sandbox-gold", epoch=0)
    commitment = Commitment(
        commitment_id="maintain-egress",
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id=authority_id,
        authorized_by=authority_id,
        source_event_ref=policy.authorization_event_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id="criterion-maintain-egress",
            provider_id="p030-use-scenarios",
            subject_refs=(_COMMITMENT_REF,),
        ),
        activation_criterion=None,
        evidence_refs=(request_ref.uri,),
        scope_refs=(_COMMITMENT_REF,),
        revision_policy=RevisionPolicy.OWNER_ONLY,
        permitted_authority_ids=(),
        dependency_ids=(),
        monitor_state_ref="monitor://p026/maintain-egress",
    )
    operational = OperationalMarkovState(
        branch=branch,
        compiler_version="sandbox-gold-1",
        phase="candidate-review",
        commitments=(commitment,),
        evidence_refs=(request_ref.uri,),
    )
    observation = CriterionObservation(
        observation_id="observation-maintain-egress",
        candidate_id=accepted.review_submission.submission_id,
        branch=branch,
        base_state_digest=operational.state_digest,
        provider_id="p030-use-scenarios",
        criterion_id="criterion-maintain-egress",
        outcome=CriterionOutcome.SATISFIED,
        measurement={
            "validation_receipt_id": (
                accepted.hard_validation.receipt_id
            ),
            "passed": True,
        },
        threshold={"required": True},
        evidence_refs=(
            f"hard-validation:{accepted.hard_validation.receipt_id}",
        ),
    )
    monitor = monitor_commitments(
        operational,
        candidate_id=accepted.review_submission.submission_id,
        observations=(observation,),
        completion_boundary=True,
    )
    readiness = assess_candidate_promotion_readiness(
        accepted.assembly,
        policy,
        approval,
        accepted.hard_validation,
        monitor,
        now_utc=issued_at_utc,
        review_submission=accepted.review_submission,
    )
    if not readiness.ready:
        raise SandboxGoldError(
            f"candidate is not promotion-ready: {readiness.blockers}"
        )
    accepted_archive = SandboxArchiveRecord(
        archive_id="accepted-sandbox-archive",
        disposition=SandboxArchiveDisposition.ACCEPTED,
        geometry_program_digest=accepted.geometry_program.program_digest,
        realization_receipt_digest=(
            accepted.realization_receipt.receipt_digest
        ),
        scene_digest=accepted.scene.scene_digest,
        decision_receipt_digest=_digest(
            {
                "approval": approval.to_dict(),
                "hard_validation": _validation_dict(
                    accepted.hard_validation
                ),
                "readiness": readiness.to_dict(),
                "usability": _receipt_dict(accepted.usability_receipt),
            }
        ),
        evidence_refs=tuple(
            sorted(
                (
                    f"approval:{approval.approval_id}",
                    f"hard-validation:{accepted.hard_validation.receipt_id}",
                    f"promotion-readiness:{_digest(readiness.to_dict())}",
                    f"sandbox-scene:{accepted.scene.scene_digest}",
                    f"usability:{accepted.usability_receipt.receipt_id}",
                )
            )
        ),
    )
    views = tuple(
        ViewEvidence(
            evidence_ref=(
                f"sandbox-view:{view.kind.value}:"
                f"{view.svg_sha256}"
            ),
            artifact_id=f"view-{view.kind.value}",
            uri=(
                f"archflow://sandbox/{accepted.scene.scene_digest}/"
                f"{view.kind.value}.svg"
            ),
            sha256=view.svg_sha256,
            viewpoint=view.kind.value,
        )
        for view in accepted.render_set.views
    )
    aesthetic = evaluate_aesthetics(
        AestheticSnapshot.detach(
            CanonicalState(ref=run.base),
            accepted.review_submission,
            views,
        ),
        evaluator="p026-read-only-aesthetic-boundary",
        assessor=lambda snapshot: (),
    )
    accepted = SandboxCandidateProof(
        **{
            field: getattr(accepted, field)
            for field in SandboxCandidateProof.__dataclass_fields__
            if field
            not in {
                "approval",
                "readiness",
                "aesthetic",
                "sandbox_archive",
            }
        },
        sandbox_archive=accepted_archive,
        approval=approval,
        readiness=readiness,
        aesthetic=aesthetic,
    )
    accepted_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_CANDIDATE,
            run_id=run.run_id,
        ),
        record_kind="sandbox-candidate-accepted",
        payload=_candidate_record(accepted),
    )
    decision_payload = {
        "schema": "PromotionDecision@1",
        "status": "accepted",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "checked_state": _base_dict(run.base),
        "candidate_ref": accepted_ref.uri,
    }
    decision_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
        record_kind="promotion-decision",
        payload=decision_payload,
    )
    semantic_geometry_digest = _validate_semantic_geometry_bindings(
        accepted.proposal,
        accepted.geometry_program.proposal,
    )
    summary_payload = {
        "schema": "SandboxGoldRunRecord@5",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": _base_dict(run.base),
        "raw_request_ref": _record_dict(request_ref),
        "scenario_ref": _record_dict(scenario_ref),
        "approval_policy_ref": _record_dict(approval_policy_ref),
        "authorization_event_ref": _record_dict(authorization_event_ref),
        "asset_payload_refs": [
            _record_dict(ref) for ref in asset_payload_refs
        ],
        "rejected_candidate_ref": (
            None if rejected_ref is None else _record_dict(rejected_ref)
        ),
        "accepted_candidate_ref": _record_dict(accepted_ref),
        "promotion_decision_ref": _record_dict(decision_ref),
        "concept_findings": list(concept_findings),
        "accepted_variant": accepted.variant,
        "provider_identity": run_provider_identity.to_dict(),
        "spatial_option_ref": _record_dict(accepted.spatial_option_ref),
        "geometry_lineage_ref": _record_dict(accepted.geometry_lineage_ref),
        "candidate_program_digest": (
            accepted.projection.projection_digest
        ),
        "geometry_program_digest": (
            accepted.geometry_program.program_digest
        ),
        "semantic_geometry_digest": semantic_geometry_digest,
        "scene_digest": accepted.scene.scene_digest,
        "voxel_view_digest": accepted.voxel_view.view_digest,
        "render_digest": accepted.render_set.render_digest,
        "usability_receipt_id": (
            accepted.usability_receipt.receipt_id
        ),
        "hard_validation_receipt_id": (
            accepted.hard_validation.receipt_id
        ),
        "approval_id": approval.approval_id,
        "commitment_monitor_receipt_id": monitor.receipt_id,
        "readiness": readiness.to_dict(),
        "aesthetic_observation_id": aesthetic.observation_id,
        "accepted": True,
        "platform_export_authority": False,
        "canonical_write_authority": False,
    }
    summary_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
        record_kind="sandbox-gold",
        payload=summary_payload,
    )
    replacement_state = {
        "schema": "AcceptedSandboxProjectState@3",
        "phase": "accepted-sandbox",
        "accepted": True,
        "sandbox_gold_ref": summary_ref.uri,
        "accepted_candidate_ref": accepted_ref.uri,
        "provider_identity": run_provider_identity.to_dict(),
        "scenario_ref": scenario_ref.uri,
        "approval_policy_ref": approval_policy_ref.uri,
        "authorization_event_ref": authorization_event_ref.uri,
        "spatial_option_ref": accepted.spatial_option_ref.uri,
        "geometry_lineage_ref": accepted.geometry_lineage_ref.uri,
        "candidate_program_digest": (
            accepted.projection.projection_digest
        ),
        "geometry_program_digest": (
            accepted.geometry_program.program_digest
        ),
        "semantic_geometry_digest": semantic_geometry_digest,
        "scene_digest": accepted.scene.scene_digest,
        "voxel_view_digest": accepted.voxel_view.view_digest,
        "render_digest": accepted.render_set.render_digest,
        "approval_id": approval.approval_id,
        "hard_validation_receipt_id": (
            accepted.hard_validation.receipt_id
        ),
        "commitment_monitor_receipt_id": monitor.receipt_id,
    }
    prepared = repository.prepare_transition(
        run=run,
        expected=run.base,
        replacement_state=replacement_state,
        decision_receipt=decision_ref,
    )
    committed = repository.compare_and_swap(
        expected=prepared.expected,
        event=prepared.event,
        replacement=prepared.replacement,
    )
    repository.verify()
    return PersistedSandboxGold(
        summary_ref=summary_ref,
        rejected_ref=rejected_ref,
        accepted_ref=accepted_ref,
        decision_ref=decision_ref,
        committed=committed,
    )


def reload_sandbox_gold(
    repository: FilesystemProjectRepository,
    *,
    run_id: str,
) -> dict[str, object]:
    """Reload and cross-check the accepted C_v / D_v,k evidence chain."""

    run = repository.load_run(run_id)
    reviews = repository.list_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_REVIEW,
            run_id=run.run_id,
        ),
    )
    summaries = [
        (ref, repository.load_json(ref))
        for ref in reviews
        if repository.load_json(ref).get("schema")
        == "SandboxGoldRunRecord@5"
    ]
    if len(summaries) != 1:
        raise SandboxGoldError(
            "run must contain exactly one sandbox Gold summary"
        )
    summary_ref, summary = summaries[0]
    candidates = repository.list_json(
        run=run,
        destination=PersistenceDestination(
            PersistenceArea.RUN_CANDIDATE,
            run_id=run.run_id,
        ),
    )
    payloads = {ref.uri: repository.load_json(ref) for ref in candidates}
    run_records = {
        ref.uri: repository.load_json(ref)
        for ref in repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
    }
    request_ref = ProjectRecordRef(**summary["raw_request_ref"])
    scenario_ref = ProjectRecordRef(**summary["scenario_ref"])
    policy_ref = ProjectRecordRef(**summary["approval_policy_ref"])
    authorization_event_ref = ProjectRecordRef(
        **summary["authorization_event_ref"]
    )
    asset_refs = tuple(
        ProjectRecordRef(**item) for item in summary["asset_payload_refs"]
    )
    provider_identity = GeometryProposalProviderIdentity.from_dict(
        summary["provider_identity"]
    )
    RawSandboxRequest.from_dict(repository.load_json(request_ref))
    _scenario_window(repository, scenario_ref)
    policy, _ = _approval_policy(
        repository,
        policy_ref,
        authorization_event_ref,
        evidence_ref=request_ref.uri,
    )
    _asset_payloads(repository, asset_refs)
    rejected_value = summary["rejected_candidate_ref"]
    rejected_ref = (
        None
        if rejected_value is None
        else ProjectRecordRef(**rejected_value)
    )
    accepted_ref = ProjectRecordRef(**summary["accepted_candidate_ref"])
    if (
        accepted_ref.uri not in payloads
        or (rejected_ref is not None and rejected_ref.uri not in payloads)
    ):
        raise SandboxGoldError("candidate archive reference is missing")
    accepted = payloads[accepted_ref.uri]
    checked_candidates = [(accepted, summary["accepted_variant"])]
    if rejected_ref is not None:
        checked_candidates.insert(0, (payloads[rejected_ref.uri], "concept"))
    semantic_geometry_digests: dict[str, str] = {}
    for payload, variant in checked_candidates:
        if (
            payload.get("schema")
            != "SandboxGoldCandidateRecord@2"
            or payload.get("variant") != variant
            or payload.get("canonical_write_authority") is not False
            or payload.get("platform_export_authority") is not False
        ):
            raise SandboxGoldError("candidate record authority drifted")
        model_receipt = ModelInvocationReceipt.from_dict(
            payload["model_receipt"]
        )
        projection = CandidateProgramProjection.from_dict(
            payload["projection"]
        )
        spatial_ref = ProjectRecordRef(**payload["spatial_option_ref"])
        spatial = SpatialOptionProposal.from_dict(
            repository.load_json(spatial_ref)
        )
        lineage_ref = ProjectRecordRef(**payload["geometry_lineage_ref"])
        lineage = load_geometry_proposal_lineage(repository, lineage_ref)
        if lineage.proposal is None:
            raise SandboxGoldError("accepted geometry lineage lost its proposal")
        candidate_proposal = _validate_proposal(
            payload["proposal"],
            revision=variant == "revised",
        )
        semantic_geometry_digests[variant] = (
            _validate_semantic_geometry_bindings(
                candidate_proposal,
                lineage.proposal,
            )
        )
        assembly = CandidateAssembly.from_dict(payload["assembly"])
        build_binding = next(
            item
            for item in assembly.policies
            if item.kind is CandidatePolicyKind.BUILD
        )
        build_payload = run_records.get(build_binding.policy_ref)
        if build_payload is None:
            raise SandboxGoldError(
                "candidate build policy record is missing"
            )
        try:
            build_policy = BuildPolicy.from_dict(build_payload)
        except (TypeError, ValueError) as exc:
            raise SandboxGoldError(
                "candidate build policy record is invalid"
            ) from exc
        if (
            build_policy.policy_digest != build_binding.policy_digest
            or not build_policy.disposable_sandbox
            or build_policy.project_id != run.project_id
            or build_policy.run_id != run.run_id
            or build_policy.base != run.base
        ):
            raise SandboxGoldError(
                "candidate build policy binding drifted"
            )
        scene = HybridScene.from_dict(payload["scene"])
        receipt = SandboxRealizationReceipt.from_dict(
            payload["realization_receipt"]
        )
        view = DerivedVoxelView.from_dict(payload["voxel_view"])
        render = SandboxRenderSet.from_dict(payload["render_set"])
        CandidateDerivationArchive.from_dict(payload["archive"])
        SandboxArchiveRecord.from_dict(payload["sandbox_archive"])
        if (
            receipt.scene_digest != scene.scene_digest
            or view.scene_digest != scene.scene_digest
            or render.scene_digest != scene.scene_digest
            # program_digest is a derived property that never serializes;
            # recompute it from the persisted program content instead of
            # trusting a self-declared key.
            or digest_value(payload["geometry_program"])
            != receipt.geometry_program_digest
            or projection.projection_digest
            != payload["geometry_program"][
                "proposal"
            ]["candidate_program_digest"]
            or projection.selected_option_ref != spatial.ref
            or digest_value(payload["geometry_program"]["proposal"])
            != lineage.proposal.proposal_digest
            or not provider_identity.matches(model_receipt)
            or lineage.lineage.provider_identity != provider_identity
        ):
            raise SandboxGoldError("candidate exact binding drifted")
    if (
        accepted["usability"]["passed"] is not True
        or accepted["hard_validation"]["passed"] is not True
        or accepted["approval"] is None
        or accepted["readiness"]["ready"] is not True
    ):
        raise SandboxGoldError("Gold rejection or acceptance status drifted")
    if (
        summary.get("semantic_geometry_digest")
        != semantic_geometry_digests[summary["accepted_variant"]]
    ):
        raise SandboxGoldError("semantic-geometry identity binding drifted")
    if rejected_ref is None:
        if summary["concept_findings"] or summary["accepted_variant"] != "concept":
            raise SandboxGoldError("directly accepted concept lineage drifted")
    else:
        rejected = payloads[rejected_ref.uri]
        if (
            rejected["usability"]["passed"] is not False
            or rejected["hard_validation"]["passed"] is not False
            or not summary["concept_findings"]
            or summary["accepted_variant"] != "revised"
        ):
            raise SandboxGoldError("rejected concept lineage drifted")
    approval = CandidateApprovalReceipt.from_dict(accepted["approval"])
    if (
        approval.policy_ref != policy_ref.uri
        or approval.policy_digest != policy.policy_digest
        or approval.approval_event_ref != authorization_event_ref.uri
    ):
        raise SandboxGoldError("persisted approval references do not resolve")
    if (
        parse_utc(approval.valid_until_utc)
        - parse_utc(approval.issued_at_utc)
        > timedelta(seconds=policy.max_validity_seconds)
    ):
        raise SandboxGoldError(
            "persisted approval exceeds approval policy validity"
        )
    current = repository.load_current_state()
    if (
        repository.read_head().version != run.base.version + 1
        or current.get("sandbox_gold_ref") != summary_ref.uri
        or current.get("accepted_candidate_ref") != accepted_ref.uri
        or current.get("provider_identity") != provider_identity.to_dict()
        or current.get("scene_digest") != summary["scene_digest"]
        or current.get("scenario_ref") != scenario_ref.uri
        or current.get("approval_policy_ref") != policy_ref.uri
        or current.get("authorization_event_ref")
        != authorization_event_ref.uri
        or current.get("spatial_option_ref")
        != ProjectRecordRef(**summary["spatial_option_ref"]).uri
        or current.get("geometry_lineage_ref")
        != ProjectRecordRef(**summary["geometry_lineage_ref"]).uri
        or current.get("semantic_geometry_digest")
        != summary["semantic_geometry_digest"]
        or summary.get("accepted") is not True
    ):
        raise SandboxGoldError("canonical accepted state does not bind Gold")
    repository.verify()
    return summary
