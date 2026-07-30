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
from archflow.evaluation.aesthetic import (
    AestheticSnapshot,
    ViewEvidence,
    evaluate_aesthetics,
)
from archflow.interaction import (
    CandidateApprovalMode,
    CandidateApprovalPolicy,
    CandidateApprovalReceipt,
    CandidateApprovalSource,
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
    assess_candidate_promotion_readiness,
    validate_candidate_approval,
)
from archflow.state import (
    ArtifactRef,
    CanonicalState,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CriterionRef,
    OperationalMarkovState,
    RevisionPolicy,
)
from archflow.state.candidate_program import (
    CandidateProgramProjection,
    CandidateProgramValue,
    CandidateValueFacet,
)
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
    SemanticBinding,
    digest_value,
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
        raise SandboxGoldError(f"{field} schema drifted")


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


@dataclass(frozen=True, slots=True)
class SandboxCandidateProof:
    variant: str
    model_receipt: ModelInvocationReceipt
    proposal: dict[str, Any]
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
    approval: CandidateApprovalReceipt | None = None
    readiness: object | None = None
    aesthetic: object | None = None


@dataclass(frozen=True, slots=True)
class PersistedSandboxGold:
    summary_ref: ProjectRecordRef
    rejected_ref: ProjectRecordRef
    accepted_ref: ProjectRecordRef
    decision_ref: ProjectRecordRef
    committed: ProjectVersionRef


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
        "envelope": {
            "width_m": "integer 5..12",
            "depth_m": "integer 5..12",
            "clear_height_m": "integer 2..5",
        },
        "performance_requirements": {
            "minimum_clear_height_m": (
                "integer 2..3 no greater than the envelope clear height"
            ),
            "circulation_min_width_m": "integer 1..2",
        },
        "material_strategy": "short model-derived material intent",
        "rationale": "text",
    }


def _revision_output_schema() -> dict[str, object]:
    return {
        **_concept_output_schema(),
        "schema": _PROPOSAL_SCHEMA,
        "performance_requirements": {
            "minimum_clear_height_m": (
                "integer 2..3 no greater than either the envelope clear "
                "height or entry height"
            ),
            "circulation_min_width_m": "integer 1..2",
        },
        "entry": {
            "offset_m": "integer inside envelope width",
            "width_m": "integer 1..2",
            "height_m": (
                "integer 2..3 no lower than minimum_clear_height_m"
            ),
        },
        "window": {
            "offset_m": "integer inside envelope width",
            "width_m": "integer 1..3",
            "height_m": "integer 1..2",
            "sill_m": "integer 1..2",
        },
        "detail_asset": {
            "asset_id": "portable identifier",
            "uri": "stable archflow:// URI",
            "media_type": "model/gltf+json",
            "sockets": ["origin"],
            "vertices": [
                [0, 0, 0],
                [1, 0, 0],
                [0, 1, 0],
                [0, 0, 1],
            ],
            "faces": [
                [0, 1, 2],
                [0, 1, 3],
                [0, 2, 3],
                [1, 2, 3],
            ],
            "provenance_refs": ["evidence://model/p026-detail"],
        },
    }


async def _model_proposal(
    provider: AsyncModelProvider,
    request: RawSandboxRequest,
    base: ProjectVersionRef,
    *,
    concept: dict[str, Any] | None = None,
    findings: tuple[str, ...] = (),
) -> ModelInvocationReceipt:
    revision = concept is not None
    context = {
        "raw_request": request.to_dict(),
        "exact_base": _base_dict(base),
        "predecessor": concept,
        "hard_gate_findings": list(findings),
    }
    context_digest = _digest(context)
    prompt = ModelInvocationRequest.create(
        request_id=(
            f"{request.request_id}-revision"
            if revision
            else f"{request.request_id}-concept"
        ),
        phase=ModelPhase.ACTION_PROPOSAL,
        checkpoint_digest=(
            _digest(concept) if revision else base.require_digest()
        ),
        context_digest=context_digest,
        payload={
            "schema": _MODEL_PROMPT_SCHEMA,
            "instructions": (
                "Act as the state-responsive Architect. Derive project "
                "functions, capacities, areas, relations, dimensions, and "
                "performance requirements plus material intent only from "
                "the raw request. "
                + (
                    "Revise the exact predecessor to answer every supplied "
                    "hard-gate finding. Add functional door and window "
                    "assemblies plus one provenance-bound detail asset. "
                    "Keep performance requirements and geometry parameters "
                    "mutually consistent."
                    if revision
                    else
                    "Produce a bounded concept-stage semantic proposal. "
                    "Do not invent an entry, opening, detail asset, geometry "
                    "operation, platform command, or acceptance decision."
                )
                + " Return exactly the requested output schema."
            ),
            "context": context,
            "required_output_schema": (
                _revision_output_schema()
                if revision
                else _concept_output_schema()
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
    if receipt.status is not ModelInvocationStatus.SUCCESS:
        raise SandboxGoldError(
            f"Architect provider failed: {receipt.error_code}"
        )
    if receipt.output is None:
        raise SandboxGoldError("Architect provider returned no proposal")
    _validate_proposal(receipt.output, revision=revision)
    return receipt


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
        "envelope",
        "performance_requirements",
        "material_strategy",
        "rationale",
    }
    expected = common | (
        {"entry", "window", "detail_asset"} if revision else set()
    )
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
    width = _bounded_integer(envelope["width_m"], "width_m", 5, 12)
    depth = _bounded_integer(envelope["depth_m"], "depth_m", 5, 12)
    _bounded_integer(
        envelope["clear_height_m"],
        "clear_height_m",
        2,
        5,
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
    minimum_clear_height = _bounded_integer(
        performance["minimum_clear_height_m"],
        "minimum_clear_height_m",
        2,
        3,
    )
    _bounded_integer(
        performance["circulation_min_width_m"],
        "circulation_min_width_m",
        1,
        2,
    )
    if minimum_clear_height > envelope["clear_height_m"]:
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
    if not revision:
        return proposal

    entry = _mapping(proposal["entry"], "entry")
    _exact(entry, {"offset_m", "width_m", "height_m"}, "entry")
    entry_width = _bounded_integer(entry["width_m"], "entry width", 1, 2)
    entry_offset = _bounded_integer(
        entry["offset_m"], "entry offset", 1, width - 2
    )
    entry_height = _bounded_integer(
        entry["height_m"], "entry height", 2, 3
    )
    if entry_offset + entry_width >= width:
        raise SandboxGoldError("entry exceeds the front host")
    if entry_height < minimum_clear_height:
        raise SandboxGoldError(
            "entry height is below the proposed minimum clear height"
        )

    window = _mapping(proposal["window"], "window")
    _exact(
        window,
        {"offset_m", "width_m", "height_m", "sill_m"},
        "window",
    )
    window_width = _bounded_integer(
        window["width_m"], "window width", 1, 3
    )
    window_offset = _bounded_integer(
        window["offset_m"], "window offset", 1, width - 2
    )
    window_height = _bounded_integer(
        window["height_m"], "window height", 1, 2
    )
    sill = _bounded_integer(window["sill_m"], "window sill", 1, 2)
    if (
        window_offset + window_width >= width
        or sill + window_height
        > envelope["clear_height_m"]
    ):
        raise SandboxGoldError("window exceeds the back host")
    _asset_payload(proposal)
    return proposal


def _asset_payload(proposal: Mapping[str, Any]) -> SandboxAssetPayload:
    asset = _mapping(proposal["detail_asset"], "detail_asset")
    _exact(
        asset,
        {
            "asset_id",
            "uri",
            "media_type",
            "sockets",
            "vertices",
            "faces",
            "provenance_refs",
        },
        "detail_asset",
    )
    _identifier(asset["asset_id"], "asset_id")
    if (
        not isinstance(asset["uri"], str)
        or not asset["uri"].startswith("archflow://")
        or asset["media_type"] != "model/gltf+json"
        or asset["sockets"] != ["origin"]
        or not isinstance(asset["provenance_refs"], list)
        or not asset["provenance_refs"]
    ):
        raise SandboxGoldError("detail asset provenance is invalid")
    return SandboxAssetPayload(
        asset_id=asset["asset_id"],
        vertices=tuple(
            tuple(float(value) for value in vertex)
            for vertex in asset["vertices"]
        ),
        faces=tuple(tuple(face) for face in asset["faces"]),
    )


def _projection(
    proposal: Mapping[str, Any],
    receipt: ModelInvocationReceipt,
    run: RunRef,
) -> CandidateProgramProjection:
    proposal_digest = _digest(proposal)
    source = (f"model-receipt:{receipt.receipt_id}",)
    derivation = (f"architect-proposal:{proposal_digest}",)
    envelope = proposal["envelope"]
    functions = proposal["functions"]
    relations = proposal["relations"]
    values = (
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
        selected_option_ref=f"architect-proposal:{proposal_digest}",
        selection_transition_id=f"selection-{proposal_digest[:16]}",
        selection_decision_ref=f"model-receipt:{receipt.receipt_id}",
        values=tuple(sorted(values, key=lambda item: item.value_id)),
    )


def _parameter(
    name: str,
    kind: GeometryParameterKind,
    value: object,
    *,
    unit: LengthUnit | None = None,
) -> GeometryParameter:
    return GeometryParameter.create(
        name=name,
        kind=kind,
        value=value,
        unit=unit,
    )


def _solid(op_id: str, origin: list[float], size: list[float]) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.SOLID,
        output_object_ids=(op_id,),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            _parameter(
                "origin",
                GeometryParameterKind.VECTOR3,
                origin,
                unit=LengthUnit.METER,
            ),
            _parameter(
                "size",
                GeometryParameterKind.VECTOR3,
                size,
                unit=LengthUnit.METER,
            ),
        ),
        semantic_binding_ids=("building-binding",),
    )


def _curve(op_id: str, points: list[list[float]]) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=GeometryOperationKind.CURVE,
        output_object_ids=(op_id,),
        input_object_ids=(),
        frame_id="world",
        parameters=(
            _parameter(
                "points",
                GeometryParameterKind.POINTS3,
                points,
                unit=LengthUnit.METER,
            ),
        ),
        semantic_binding_ids=("building-binding",),
    )


def _boolean(
    op_id: str,
    kind: GeometryOperationKind,
    inputs: tuple[str, ...],
    *,
    base_id: str | None = None,
) -> GeometryOperation:
    ordered = tuple(sorted(inputs))
    parameters = ()
    if kind is GeometryOperationKind.BOOLEAN_DIFFERENCE:
        if base_id is None:
            raise SandboxGoldError("difference requires a base object")
        parameters = (
            _parameter(
                "base_index",
                GeometryParameterKind.INTEGER,
                ordered.index(base_id),
            ),
        )
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(op_id,),
        input_object_ids=ordered,
        frame_id="world",
        parameters=parameters,
        semantic_binding_ids=("building-binding",),
        responds_to_object_ids=ordered,
    )


def _geometry_proposal(
    projection: CandidateProgramProjection,
    proposal: Mapping[str, Any],
    *,
    detailed: bool,
) -> tuple[GeometryProgramProposal, tuple[SandboxAssetPayload, ...]]:
    envelope = proposal["envelope"]
    width = envelope["width_m"]
    depth = envelope["depth_m"]
    height = envelope["clear_height_m"]
    operations = [
        _solid("floor", [0, 0, 0], [width, 1, depth]),
        _solid("ceiling", [0, height + 1, 0], [width, 1, depth]),
        _solid("side-east", [width - 1, 1, 0], [1, height, depth]),
        _solid("side-west", [0, 1, 0], [1, height, depth]),
    ]
    assemblies: list[HostedAssembly] = []
    assets: list[AssetReference] = []
    payloads: list[SandboxAssetPayload] = []
    frames = [
        CoordinateFrame(
            frame_id="world",
            parent_frame_id=None,
            transform_from_parent=AffineTransform.identity(),
            source_refs=(
                f"architect-proposal:{_digest(proposal)}",
            ),
        )
    ]
    if not detailed:
        operations.extend(
            (
                _solid("front-wall", [0, 1, 0], [width, height, 1]),
                _solid(
                    "back-wall",
                    [0, 1, depth - 1],
                    [width, height, 1],
                ),
            )
        )
    else:
        entry = proposal["entry"]
        window = proposal["window"]
        operations.extend(
            (
                _solid(
                    "front-host",
                    [0, 1, 0],
                    [width, height, 1],
                ),
                _solid(
                    "entry-tool",
                    [entry["offset_m"], 1, 0],
                    [entry["width_m"], entry["height_m"], 1],
                ),
                _boolean(
                    "entry-opening",
                    GeometryOperationKind.BOOLEAN_INTERSECTION,
                    ("entry-tool", "front-host"),
                ),
                _boolean(
                    "front-wall",
                    GeometryOperationKind.BOOLEAN_DIFFERENCE,
                    ("entry-tool", "front-host"),
                    base_id="front-host",
                ),
                _curve(
                    "entry-clearance",
                    [
                        [entry["offset_m"], 1, 1],
                        [
                            entry["offset_m"] + entry["width_m"],
                            1 + entry["height_m"],
                            3,
                        ],
                    ],
                ),
                _curve(
                    "entry-frame",
                    [
                        [entry["offset_m"], 1, 0],
                        [
                            entry["offset_m"] + entry["width_m"],
                            entry["height_m"] + 1,
                            0,
                        ],
                    ],
                ),
                _curve(
                    "entry-leaf",
                    [
                        [entry["offset_m"], 1, 0],
                        [
                            entry["offset_m"] + entry["width_m"],
                            1,
                            0,
                        ],
                    ],
                ),
                _curve(
                    "entry-hardware",
                    [
                        [entry["offset_m"] + 0.2, 2, 0],
                        [entry["offset_m"] + 0.3, 2, 0],
                    ],
                ),
                _solid(
                    "back-host",
                    [0, 1, depth - 1],
                    [width, height, 1],
                ),
                _solid(
                    "window-tool",
                    [
                        window["offset_m"],
                        1 + window["sill_m"],
                        depth - 1,
                    ],
                    [window["width_m"], window["height_m"], 1],
                ),
                _boolean(
                    "window-opening",
                    GeometryOperationKind.BOOLEAN_INTERSECTION,
                    ("back-host", "window-tool"),
                ),
                _boolean(
                    "back-wall",
                    GeometryOperationKind.BOOLEAN_DIFFERENCE,
                    ("back-host", "window-tool"),
                    base_id="back-host",
                ),
                _curve(
                    "window-clearance",
                    [
                        [
                            window["offset_m"],
                            1 + window["sill_m"],
                            depth - 2,
                        ],
                        [
                            window["offset_m"] + window["width_m"],
                            1 + window["sill_m"] + window["height_m"],
                            depth - 1,
                        ],
                    ],
                ),
                _curve(
                    "window-frame",
                    [
                        [
                            window["offset_m"],
                            1 + window["sill_m"],
                            depth - 1,
                        ],
                        [
                            window["offset_m"] + window["width_m"],
                            1 + window["sill_m"] + window["height_m"],
                            depth - 1,
                        ],
                    ],
                ),
                _solid(
                    "window-glazing",
                    [
                        window["offset_m"],
                        1 + window["sill_m"],
                        depth - 1,
                    ],
                    [window["width_m"], window["height_m"], 1],
                ),
                _curve(
                    "window-hardware",
                    [
                        [
                            window["offset_m"] + 0.2,
                            1 + window["sill_m"],
                            depth - 1,
                        ],
                        [
                            window["offset_m"] + 0.3,
                            1 + window["sill_m"],
                            depth - 1,
                        ],
                    ],
                ),
            )
        )
        assemblies.extend(
            (
                HostedAssembly(
                    assembly_id="entry-assembly",
                    kind=AssemblyKind.DOOR,
                    host_object_id="front-host",
                    host_socket_id="entry-axis",
                    members=tuple(
                        sorted(
                            (
                                AssemblyMember(
                                    AssemblyRole.CLEARANCE,
                                    ("entry-clearance",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.FRAME,
                                    ("entry-frame",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.HARDWARE,
                                    ("entry-hardware",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.HOST_CUT,
                                    ("entry-opening",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.LEAF,
                                    ("entry-leaf",),
                                ),
                            ),
                            key=lambda item: item.role.value,
                        )
                    ),
                    interface_refs=("interface:outside-to-interior",),
                    semantic_binding_ids=("building-binding",),
                    maturity=DetailMaturity.FUNCTIONAL,
                ),
                HostedAssembly(
                    assembly_id="window-assembly",
                    kind=AssemblyKind.WINDOW,
                    host_object_id="back-host",
                    host_socket_id="window-axis",
                    members=tuple(
                        sorted(
                            (
                                AssemblyMember(
                                    AssemblyRole.CLEARANCE,
                                    ("window-clearance",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.FRAME,
                                    ("window-frame",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.GLAZING,
                                    ("window-glazing",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.HARDWARE,
                                    ("window-hardware",),
                                ),
                                AssemblyMember(
                                    AssemblyRole.HOST_CUT,
                                    ("window-opening",),
                                ),
                            ),
                            key=lambda item: item.role.value,
                        )
                    ),
                    interface_refs=("interface:interior-to-daylight",),
                    semantic_binding_ids=("building-binding",),
                    maturity=DetailMaturity.FUNCTIONAL,
                ),
            )
        )
        payload = _asset_payload(proposal)
        asset = proposal["detail_asset"]
        payloads.append(payload)
        assets.append(
            AssetReference(
                asset_id=payload.asset_id,
                uri=asset["uri"],
                media_type=asset["media_type"],
                sha256=payload.payload_digest,
                native_unit=LengthUnit.METER,
                sockets=tuple(asset["sockets"]),
                provenance_refs=tuple(asset["provenance_refs"]),
            )
        )
        frames.append(
            CoordinateFrame(
                frame_id="detail-frame",
                parent_frame_id="world",
                transform_from_parent=AffineTransform(
                    (
                        1.0, 0.0, 0.0, width / 2,
                        0.0, 1.0, 0.0, 1.0,
                        0.0, 0.0, 1.0, depth / 2,
                        0.0, 0.0, 0.0, 1.0,
                    )
                ),
                source_refs=tuple(asset["provenance_refs"]),
            )
        )
        operations.append(
            GeometryOperation(
                op_id="detail-instance",
                kind=GeometryOperationKind.ASSET_INSTANCE,
                output_object_ids=("detail-instance",),
                input_object_ids=(),
                frame_id="detail-frame",
                parameters=(),
                semantic_binding_ids=("building-binding",),
                asset_id=payload.asset_id,
                asset_socket_id="origin",
                asset_scale=(0.5, 0.5, 0.5),
            )
        )
    ordered_operations = tuple(
        sorted(operations, key=lambda item: item.op_id)
    )
    object_ids = tuple(
        sorted(
            object_id
            for operation in ordered_operations
            for object_id in operation.output_object_ids
        )
    )
    geometry = GeometryProgramProposal(
        proposal_id=(
            f"geometry-{projection.selected_revision_digest[:20]}"
        ),
        project_id=projection.project_id,
        run_id=projection.run_id,
        base=projection.base,
        candidate_program_digest=projection.projection_digest,
        predecessor_program_digest=None,
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
        frames=tuple(sorted(frames, key=lambda item: item.frame_id)),
        assets=tuple(sorted(assets, key=lambda item: item.asset_id)),
        semantic_bindings=(
            SemanticBinding(
                binding_id="building-binding",
                object_ids=object_ids,
                candidate_value_ids=tuple(
                    item.value_id for item in projection.values
                ),
                commitment_refs=(_COMMITMENT_REF,),
                evidence_refs=(
                    projection.selected_option_ref,
                    projection.selection_decision_ref,
                ),
            ),
        ),
        operations=ordered_operations,
        assemblies=tuple(
            sorted(assemblies, key=lambda item: item.assembly_id)
        ),
    )
    return geometry, tuple(sorted(payloads, key=lambda item: item.asset_id))


def _program(proposal: Mapping[str, Any]):
    envelope = proposal["envelope"]
    performance = proposal["performance_requirements"]
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
            "entrance_count": 1,
            "circulation_min_width": performance[
                "circulation_min_width_m"
            ],
            "hard_requirements": ["maintain-evidence-bound-egress"],
            "soft_preferences": [proposal["material_strategy"]],
            "prohibitions": [],
        }
    )


def _approval_policy(
    run: RunRef,
    *,
    evidence_ref: str,
) -> CandidateApprovalPolicy:
    return CandidateApprovalPolicy(
        policy_ref=(
            f"project://{run.project_id}/input/"
            "sandbox-approval-policy.json"
        ),
        authority_ids=("sandbox-owner",),
        mode=CandidateApprovalMode.PREAUTHORIZED_DISPOSABLE,
        max_validity_seconds=3_600,
        allows_disposable_automation=True,
        authorization_event_ref=(
            f"event://{run.project_id}/sandbox-preauthorization"
        ),
        evidence_refs=(evidence_ref,),
    )


def _candidate_assembly(
    projection: CandidateProgramProjection,
    program_digest: str,
    scene_digest: str,
    view_digest: str,
    *,
    variant: str,
    approval_policy: CandidateApprovalPolicy,
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
    build_policy_digest = _digest(
        {
            "schema": "SandboxBuildPolicy@1",
            "project_id": projection.project_id,
            "run_id": projection.run_id,
            "base": _base_dict(projection.base),
            "disposable_sandbox": True,
            "platform_export": False,
            "evidence_ref": evidence_ref,
        }
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
                    policy_ref=(
                        f"project://{projection.project_id}/input/"
                        "sandbox-build-policy.json"
                    ),
                    policy_digest=build_policy_digest,
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


def _candidate_proof(
    receipt: ModelInvocationReceipt,
    run: RunRef,
    *,
    variant: str,
    detailed: bool,
    raw_request_ref: str,
    predecessor_ref: str | None = None,
) -> SandboxCandidateProof:
    assert receipt.output is not None
    proposal = _validate_proposal(receipt.output, revision=detailed)
    projection = _projection(proposal, receipt, run)
    geometry_proposal, asset_payloads = _geometry_proposal(
        projection,
        proposal,
        detailed=detailed,
    )
    compilation = compile_geometry_program(
        projection,
        geometry_proposal,
        active_commitment_refs=(_COMMITMENT_REF,),
        available_asset_digests={
            item.asset_id: item.payload_digest for item in asset_payloads
        },
    )
    if compilation.program is None:
        raise SandboxGoldError(
            f"geometry compilation rejected: {compilation.receipt.issues}"
        )
    realized = realize_geometry(
        compilation.program,
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
    zones = tuple(
        UseZoneEvidence(
            space=item["function_id"],
            region_id=region.region_id,
            evidence_refs=(
                f"sandbox-region:{region.region_id}",
                f"architect-proposal:{_digest(proposal)}",
            ),
        )
        for item in proposal["functions"]
    )
    approval_policy = _approval_policy(
        run,
        evidence_ref=raw_request_ref,
    )
    assembly = _candidate_assembly(
        projection,
        compilation.program.program_digest,
        realized.scene.scene_digest,
        view.view_digest,
        variant=variant,
        approval_policy=approval_policy,
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
        geometry_program_digest=compilation.program.program_digest,
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
                geometry_program_digest=compilation.program.program_digest,
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
        else CandidateDisposition.REJECTED
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
            "The concept is retained after deterministic hard-gate rejection."
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
        projection=projection,
        assembly=assembly,
        geometry_program=compilation.program,
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
    )


def _candidate_record(proof: SandboxCandidateProof) -> dict[str, object]:
    return {
        "schema": "SandboxGoldCandidateRecord@1",
        "variant": proof.variant,
        "model_receipt": proof.model_receipt.to_dict(),
        "proposal": proof.proposal,
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


async def execute_sandbox_gold(
    repository: FilesystemProjectRepository,
    run: RunRef,
    provider: AsyncModelProvider,
    request: RawSandboxRequest,
    *,
    issued_at_utc: str,
    valid_until_utc: str,
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
    input_ref = repository.put_json(
        run=run,
        destination=PersistenceDestination(PersistenceArea.INPUT),
        record_kind="raw-sandbox-request",
        payload=request.to_dict(),
    )
    concept_receipt = await _model_proposal(
        provider,
        request,
        run.base,
    )
    concept = _candidate_proof(
        concept_receipt,
        run,
        variant="concept",
        detailed=False,
        raw_request_ref=input_ref.uri,
    )
    concept_findings = _finding_codes(concept)
    if not concept_findings:
        raise SandboxGoldError(
            "concept stage unexpectedly passed; no honest revision trace exists"
        )
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
        provider,
        request,
        run.base,
        concept=concept.proposal,
        findings=concept_findings,
    )
    accepted = _candidate_proof(
        revision_receipt,
        run,
        variant="revised",
        detailed=True,
        raw_request_ref=input_ref.uri,
        predecessor_ref=rejected_ref.uri,
    )
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

    policy = _approval_policy(run, evidence_ref=input_ref.uri)
    approval = CandidateApprovalReceipt(
        candidate_assembly_digest=accepted.assembly.assembly_digest,
        submission_id=accepted.assembly.submission.submission_id,
        plan_digest=accepted.assembly.plan.plan_digest,
        base=run.base,
        workspace_id=accepted.assembly.submission.workspace_id,
        policy_ref=policy.policy_ref,
        policy_digest=policy.policy_digest,
        authority_id="sandbox-owner",
        source=CandidateApprovalSource.PREAUTHORIZED_POLICY,
        authority_identity_receipt_ref=None,
        approval_event_ref=policy.authorization_event_ref,
        issued_at_utc=issued_at_utc,
        valid_until_utc=valid_until_utc,
    )
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
        authority_id="sandbox-owner",
        authorized_by="sandbox-owner",
        source_event_ref=policy.authorization_event_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id="criterion-maintain-egress",
            provider_id="p030-use-scenarios",
            subject_refs=(_COMMITMENT_REF,),
        ),
        activation_criterion=None,
        evidence_refs=(input_ref.uri,),
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
        evidence_refs=(input_ref.uri,),
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
    summary_payload = {
        "schema": "SandboxGoldRunRecord@1",
        "project_id": run.project_id,
        "run_id": run.run_id,
        "base": _base_dict(run.base),
        "raw_request_ref": _record_dict(input_ref),
        "rejected_candidate_ref": _record_dict(rejected_ref),
        "accepted_candidate_ref": _record_dict(accepted_ref),
        "promotion_decision_ref": _record_dict(decision_ref),
        "concept_findings": list(concept_findings),
        "provider_id": revision_receipt.provider_id,
        "model_id": revision_receipt.model_id,
        "model_provider_fingerprint": (
            revision_receipt.provider_fingerprint
        ),
        "candidate_program_digest": (
            accepted.projection.projection_digest
        ),
        "geometry_program_digest": (
            accepted.geometry_program.program_digest
        ),
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
        "schema": "AcceptedSandboxProjectState@1",
        "phase": "accepted-sandbox",
        "accepted": True,
        "sandbox_gold_ref": summary_ref.uri,
        "accepted_candidate_ref": accepted_ref.uri,
        "candidate_program_digest": (
            accepted.projection.projection_digest
        ),
        "geometry_program_digest": (
            accepted.geometry_program.program_digest
        ),
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
        == "SandboxGoldRunRecord@1"
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
    rejected_ref = ProjectRecordRef(**summary["rejected_candidate_ref"])
    accepted_ref = ProjectRecordRef(**summary["accepted_candidate_ref"])
    if rejected_ref.uri not in payloads or accepted_ref.uri not in payloads:
        raise SandboxGoldError("candidate archive reference is missing")
    rejected = payloads[rejected_ref.uri]
    accepted = payloads[accepted_ref.uri]
    for payload, variant in (
        (rejected, "concept"),
        (accepted, "revised"),
    ):
        if (
            payload.get("schema")
            != "SandboxGoldCandidateRecord@1"
            or payload.get("variant") != variant
            or payload.get("canonical_write_authority") is not False
            or payload.get("platform_export_authority") is not False
        ):
            raise SandboxGoldError("candidate record authority drifted")
        ModelInvocationReceipt.from_dict(payload["model_receipt"])
        projection = CandidateProgramProjection.from_dict(
            payload["projection"]
        )
        CandidateAssembly.from_dict(payload["assembly"])
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
        ):
            raise SandboxGoldError("candidate exact binding drifted")
    if (
        rejected["usability"]["passed"] is not False
        or rejected["hard_validation"]["passed"] is not False
        or accepted["usability"]["passed"] is not True
        or accepted["hard_validation"]["passed"] is not True
        or accepted["approval"] is None
        or accepted["readiness"]["ready"] is not True
    ):
        raise SandboxGoldError("Gold rejection or acceptance status drifted")
    CandidateApprovalReceipt.from_dict(accepted["approval"])
    current = repository.load_current_state()
    if (
        repository.read_head().version != run.base.version + 1
        or current.get("sandbox_gold_ref") != summary_ref.uri
        or current.get("accepted_candidate_ref") != accepted_ref.uri
        or current.get("scene_digest") != summary["scene_digest"]
        or summary.get("accepted") is not True
    ):
        raise SandboxGoldError("canonical accepted state does not bind Gold")
    repository.verify()
    return summary
