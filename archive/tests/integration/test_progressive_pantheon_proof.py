from __future__ import annotations

import asyncio
import hashlib
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.ports.model import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
    proposal_authoring_output,
)
from archive.archflow.capabilities.semantic_spatial_authoring import (
    semantic_spatial_authoring_output,
)
from archive.archflow.production.provider_runtime import InvocationEvidenceCollector, activate_model_provider
from archive.archflow.production.responsibility import ProviderIdentity
from archflow.contracts.canonical import canonical_digest
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.refs import ProjectRecordRef, RunRef
from archive.archflow.project.bootstrap import bootstrap_raw_request_project
from archive.archflow.runtime.persistence.production_transition import (
    ProductionRecordRole,
    persist_compiled_production_transition,
    production_intent_digest,
)
from archive.archflow.realization.sandbox import SandboxArchiveDisposition, SandboxArchiveRecord, VoxelizationPolicy, derive_voxel_view, realize_geometry
from archflow.compilers.geometry import compile_geometry_program
from archive.archflow.runtime.production_compiler import (
    ProductionRootCompiler,
    schematic_selection_output,
)
from archive.archflow.runtime.production_runtime import (
    ProductionAuthoringContext,
    run_or_resume_production_step,
)
from archive.archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleStatus,
    bind_initial_semantic_geometry,
    compile_semantic_geometry_lifecycle,
)
from archflow.state.model import CanonicalState
from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef
from archflow.state.spatial import ComponentMaturity, ConstraintResponseStatus, DesignComponent, MassingVolume, SpatialConnection, SpatialConstraintResponse, SpatialGridBasis, SpatialLevel, SpatialOptionProposal, SpatialZone
from archflow.state.stage_workflow import DesignPhase
from archive.archflow.state.design_maturity import DesignMaturityState, PhaseGateRequest, evaluate_forward_phase_gate
from archive.archflow.state.design_program import ProgramMetricKind, ProgramNodeKind
from archflow.state.spatial import SiteBounds
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    ObjectRevisionPrecondition,
    SemanticBinding,
)
from archflow.submission.model import CandidateDelta, CandidateSubmission
from archflow.validation.engine import ArtifactPresentValidator, validate_submission
from archive.tests.test_production_root_compiler import _rebase_context
from archive.tests.test_spatial_proposals import _inputs


PROJECT_ID = "p058-progressive-pantheon"
RUN_ID = "fresh-pantheon-001"
COMMITMENT_REF = "commitment:preserve-radial-public-hall"
PROMPT = (
    "Design a Pantheon-scale radial public hall from the supplied current "
    "project records. Co-author semantic components and coarse geometry, "
    "then deepen the dome through an oculus and local coffering without "
    "loading a prior final design."
)
def _probe_root() -> Path:
    """Resolve the relocated evidence probe; an absent root triggers skips."""

    try:
        from archive.tools._probe_paths import resolve_probe_root

        return resolve_probe_root(PROJECT_ID)
    except Exception:
        return Path(__file__).resolve().parents[2] / "probes" / PROJECT_ID


PROBE_ROOT = _probe_root()
IDENTITY = GeometryProposalProviderIdentity(
    provider_id="scripted-radial-author",
    model_id="scripted-radial-model",
    provider_version="1",
    provider_fingerprint="5" * 64,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _project_context() -> ProductionAuthoringContext:
    brief, program, site, policy, state, maturity, _ = _inputs(
        project_id=PROJECT_ID,
    )
    labels = {
        "function-1": "central radial public hall",
        "function-2": "axial entrance portico",
        "function-3": "daylit dome volume",
    }
    program = replace(
        program,
        nodes=tuple(
            replace(item, label=labels.get(item.node_id, item.label))
            for item in program.nodes
        ),
        ranges=tuple(
            replace(
                item,
                minimum=(300.0 if item.metric is ProgramMetricKind.FOOTPRINT else item.minimum),
                maximum=(
                    2_000.0
                    if item.metric is ProgramMetricKind.FOOTPRINT
                    else (
                        4_000.0
                        if item.metric
                        in {
                            ProgramMetricKind.NET_AREA,
                            ProgramMetricKind.TOTAL_FLOOR_AREA,
                        }
                        else item.maximum
                    )
                ),
            )
            for item in program.ranges
        ),
    )
    policy = replace(policy, program_digest=program.program_digest)
    commitment = Commitment(
        commitment_id=COMMITMENT_REF.removeprefix("commitment:"),
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority.user",
        authorized_by="authority.user",
        source_event_ref=brief.raw_request_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id="preserve-radial-hall",
            provider_id="validator.semantic-geometry",
        ),
        evidence_refs=(brief.raw_request_ref,),
    )
    state = replace(state, commitments=(commitment,))
    deliverables = tuple(
        replace(item, base_state_digest=state.state_digest)
        for item in maturity.deliverables
    )
    maturity = DesignMaturityState.from_operational_state(
        state,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id="enter-radial-schematic",
            branch=state.branch,
            base_state_digest=state.state_digest,
            from_phase=maturity.phase,
            to_phase=DesignPhase.SCHEMATIC_DESIGN,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    return ProductionAuthoringContext(
        state=state,
        maturity=maturity,
        phase_gate=gate,
        program=program,
        site_context=site,
        build_policy=policy,
        architect_id="primary-architect",
        required_commitment_refs=(COMMITMENT_REF,),
    )


def _radial_proposal(
    context: ProductionAuthoringContext,
    *,
    option_id: str,
    radius: int,
) -> SpatialOptionProposal:
    evidence = (context.program.evidence_refs[0],)
    center = (16, 16)
    footprint = tuple(
        sorted(
            {
                *(
                    (x, z)
                    for x in range(center[0] - radius, center[0] + radius + 1)
                    for z in range(center[1] - radius, center[1] + radius + 1)
                ),
                *((x, z) for x in range(8, 25) for z in range(0, 5)),
            }
        )
    )
    levels = (
        SpatialLevel("dome", 76, 12, evidence),
        SpatialLevel("ground", 64, 12, evidence),
        SpatialLevel("portico", 64, 8, evidence),
    )
    volumes = (
        MassingVolume(
            "dome-volume",
            SiteBounds(
                (16 - radius, 76, 16 - radius),
                (16 + radius, 88, 16 + radius),
            ),
            ("dome",),
            evidence,
        ),
        MassingVolume(
            "portico-volume",
            SiteBounds((8, 64, 0), (24, 72, 4)),
            ("portico",),
            evidence,
        ),
        MassingVolume(
            "rotunda-volume",
            SiteBounds(
                (16 - radius, 64, 16 - radius),
                (16 + radius, 76, 16 + radius),
            ),
            ("ground",),
            evidence,
        ),
    )
    functions = tuple(
        item
        for item in context.program.nodes
        if item.kind is ProgramNodeKind.FUNCTION
    )
    zone_specs = (
        ("rotunda-zone", functions[0].ref, "ground", "rotunda-volume"),
        ("portico-zone", functions[1].ref, "portico", "portico-volume"),
        ("dome-zone", functions[2].ref, "dome", "dome-volume"),
    )
    zones = tuple(
        SpatialZone(
            zone_id=zone_id,
            program_node_refs=(node_ref,),
            level_ids=(level_id,),
            volume_ids=(volume_id,),
            source_refs=evidence,
        )
        for zone_id, node_ref, level_id, volume_id in zone_specs
    )
    zone_by_node = {
        zone.program_node_refs[0]: zone.zone_id for zone in zones
    }
    connections = tuple(
        SpatialConnection(
            connection_id=f"connection-{index}",
            source_zone_id=zone_by_node[item.source_node_ref],
            target_zone_id=zone_by_node[item.target_node_ref],
            relationship_refs=(item.ref,),
            directed=item.directed,
            source_refs=evidence,
        )
        for index, item in enumerate(context.program.relationships, start=1)
    )
    responses = tuple(
        SpatialConstraintResponse(
            response_id=f"response-{index}",
            constraint_ref=item.ref,
            status=ConstraintResponseStatus.ACKNOWLEDGED,
            rationale="The unresolved discipline risk remains explicit.",
            source_refs=evidence,
        )
        for index, item in enumerate(context.build_policy.constraints, start=1)
    )
    components = (
        DesignComponent(
            component_id="building",
            parent_component_id=None,
            semantic_kind="radial-public-building",
            intent="Own the radial public hall composition.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="dome",
            parent_component_id="building",
            semantic_kind="hemispherical-dome",
            intent="Establish a meaningful coarse dome over the rotunda.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("dome-volume",),
            unresolved_child_roles=("coffers", "oculus"),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="portico",
            parent_component_id="building",
            semantic_kind="axial-portico",
            intent="Mark the axial public entrance.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("portico-volume",),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="rotunda",
            parent_component_id="building",
            semantic_kind="cylindrical-rotunda",
            intent="Form the central radial public room.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("rotunda-volume",),
            unresolved_child_roles=(),
            source_refs=evidence,
        ),
    )
    footprint_range = next(
        item.ref
        for item in context.program.ranges
        if item.metric is ProgramMetricKind.FOOTPRINT
    )
    return SpatialOptionProposal(
        option_id=option_id,
        label=f"Radial hall radius {radius}",
        program_scenario_ref=None,
        footprint_range_ref=footprint_range,
        grid_basis=SpatialGridBasis(1.0, "square_cells", evidence),
        footprint_cells=footprint,
        levels=levels,
        volumes=volumes,
        zones=zones,
        components=components,
        connections=connections,
        constraint_responses=responses,
        typology_hypothesis=(
            "Radial public hall with an axial portico and hemispherical dome."
        ),
        palette_refs=("material-intent:massive-masonry",),
        rationale=(
            "The current program and site support a central rotunda, axial "
            "approach, and dome volume at public-monument scale."
        ),
        responds_to_refs=tuple(
            sorted(
                {
                    *[item.ref for item in context.program.relationships],
                    *[item.ref for item in context.build_policy.constraints],
                }
            )
        ),
        expert_advice_refs=(),
        evidence_refs=evidence,
    )


def _parameter(
    name: str,
    kind: GeometryParameterKind,
    value: object,
    *,
    unit: LengthUnit | None = None,
) -> GeometryParameter:
    return GeometryParameter.create(name=name, kind=kind, value=value, unit=unit)


def _operation(
    op_id: str,
    kind: GeometryOperationKind,
    output_id: str,
    binding_id: str,
    parameters: tuple[GeometryParameter, ...],
    *,
    inputs: tuple[str, ...] = (),
    responds_to_binding: bool = False,
    responds_to_inputs: bool = False,
) -> GeometryOperation:
    return GeometryOperation(
        op_id=op_id,
        kind=kind,
        output_object_ids=(output_id,),
        input_object_ids=inputs,
        frame_id="world",
        parameters=tuple(sorted(parameters, key=lambda item: item.name)),
        semantic_binding_ids=(binding_id,),
        responds_to_binding_ids=(
            (binding_id,) if responds_to_binding else ()
        ),
        responds_to_object_ids=(inputs if responds_to_inputs else ()),
    )


def _dome_profiles(radius: float, *, opening_radius: float) -> list[list[float]]:
    points: list[list[float]] = []
    sections = (
        (76.0, radius),
        (79.0, math.sqrt(radius**2 - 3.0**2)),
        (82.0, math.sqrt(radius**2 - 6.0**2)),
        (85.0, math.sqrt(radius**2 - 9.0**2)),
        (87.5, opening_radius),
    )
    for y, section_radius in sections:
        for index in range(24):
            angle = 2.0 * math.pi * index / 24.0
            points.append(
                [
                    16.0 + section_radius * math.cos(angle),
                    y,
                    16.0 + section_radius * math.sin(angle),
                ]
            )
    return points


def _geometry_proposal(
    state: DevelopedDesignState,
    *,
    stage: int,
    prior=None,  # type: ignore[no-untyped-def]
) -> GeometryProgramProposal:
    evidence = state.selected_schematic.option.proposal.evidence_refs
    operations = [
        _operation(
            "portico-solid",
            GeometryOperationKind.SOLID,
            "portico-object",
            "portico-binding",
            (
                _parameter(
                    "origin",
                    GeometryParameterKind.VECTOR3,
                    [8, 64, 0],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "size",
                    GeometryParameterKind.VECTOR3,
                    [16, 8, 4],
                    unit=LengthUnit.METER,
                ),
            ),
        ),
        _operation(
            "rotunda-revolve",
            GeometryOperationKind.REVOLVE,
            "rotunda-object",
            "rotunda-binding",
            (
                _parameter(
                    "axis_end",
                    GeometryParameterKind.VECTOR3,
                    [16, 76, 16],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "axis_start",
                    GeometryParameterKind.VECTOR3,
                    [16, 64, 16],
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "end_radius",
                    GeometryParameterKind.NUMBER,
                    12,
                    unit=LengthUnit.METER,
                ),
                _parameter(
                    "start_radius",
                    GeometryParameterKind.NUMBER,
                    12,
                    unit=LengthUnit.METER,
                ),
            ),
        ),
    ]
    bindings: dict[str, tuple[str, tuple[str, ...]]] = {
        "portico-binding": ("portico", ("portico-object",)),
        "rotunda-binding": ("rotunda", ("rotunda-object",)),
    }
    if stage == 0:
        operations.append(
            _operation(
                "dome-shell",
                GeometryOperationKind.LOFT,
                "dome-shell-object",
                "dome-binding",
                (
                    _parameter("cap_ends", GeometryParameterKind.BOOLEAN, False),
                    _parameter(
                        "closed_profile", GeometryParameterKind.BOOLEAN, True
                    ),
                    _parameter("profile_size", GeometryParameterKind.INTEGER, 24),
                    _parameter(
                        "profiles",
                        GeometryParameterKind.POINTS3,
                        _dome_profiles(12.0, opening_radius=0.5),
                        unit=LengthUnit.METER,
                    ),
                ),
            )
        )
        bindings["dome-binding"] = ("dome", ("dome-shell-object",))
    else:
        operations.extend(
            (
                _operation(
                    "dome-raw",
                    GeometryOperationKind.LOFT,
                    "dome-raw-object",
                    "dome-binding",
                    (
                        _parameter(
                            "cap_ends", GeometryParameterKind.BOOLEAN, False
                        ),
                        _parameter(
                            "closed_profile", GeometryParameterKind.BOOLEAN, True
                        ),
                        _parameter(
                            "profile_size", GeometryParameterKind.INTEGER, 24
                        ),
                        _parameter(
                            "profiles",
                            GeometryParameterKind.POINTS3,
                            _dome_profiles(12.0, opening_radius=2.0),
                            unit=LengthUnit.METER,
                        ),
                    ),
                    responds_to_binding=True,
                ),
                _operation(
                    "dome-shell",
                    GeometryOperationKind.BOOLEAN_DIFFERENCE,
                    "dome-shell-object",
                    "dome-binding",
                    (
                        _parameter(
                            "base_index", GeometryParameterKind.INTEGER, 0
                        ),
                    ),
                    inputs=("dome-raw-object", "oculus-tool-object"),
                    responds_to_binding=True,
                    responds_to_inputs=True,
                ),
                _operation(
                    "oculus-tool",
                    GeometryOperationKind.SOLID,
                    "oculus-tool-object",
                    "oculus-binding",
                    (
                        _parameter(
                            "origin",
                            GeometryParameterKind.VECTOR3,
                            [14, 87, 14],
                            unit=LengthUnit.METER,
                        ),
                        _parameter(
                            "size",
                            GeometryParameterKind.VECTOR3,
                            [4, 2, 4],
                            unit=LengthUnit.METER,
                        ),
                    ),
                ),
            )
        )
        bindings["dome-binding"] = (
            "dome",
            ("dome-raw-object", "dome-shell-object"),
        )
        bindings["oculus-binding"] = ("oculus", ("oculus-tool-object",))
    if stage >= 2:
        operations.extend(
            (
                _operation(
                    "coffer-source",
                    GeometryOperationKind.SOLID,
                    "coffer-source-object",
                    "coffers-binding",
                    (
                        _parameter(
                            "origin",
                            GeometryParameterKind.VECTOR3,
                            [7, 76, 15],
                            unit=LengthUnit.METER,
                        ),
                        _parameter(
                            "size",
                            GeometryParameterKind.VECTOR3,
                            [0.5, 0.5, 0.5],
                            unit=LengthUnit.METER,
                        ),
                    ),
                ),
                _operation(
                    "coffer-array",
                    GeometryOperationKind.ARRAY,
                    "coffer-array-object",
                    "coffers-binding",
                    (
                        _parameter("count", GeometryParameterKind.INTEGER, 12),
                        _parameter(
                            "step",
                            GeometryParameterKind.VECTOR3,
                            [0.7, 0.25, 0],
                            unit=LengthUnit.METER,
                        ),
                    ),
                    inputs=("coffer-source-object",),
                ),
            )
        )
        bindings["coffers-binding"] = (
            "coffers",
            ("coffer-array-object", "coffer-source-object"),
        )
    prior_binding_evidence = (
        {}
        if prior is None
        else {
            item.component_id: item.evidence_refs
            for item in prior.proposal.semantic_bindings
        }
    )
    semantic_bindings = tuple(
        SemanticBinding(
            binding_id=binding_id,
            component_id=component_id,
            object_ids=tuple(sorted(object_ids)),
            commitment_refs=(COMMITMENT_REF,),
            evidence_refs=prior_binding_evidence.get(component_id, evidence),
        )
        for binding_id, (component_id, object_ids) in sorted(bindings.items())
    )
    revisions = ()
    if prior is not None:
        changed_ids = (
            ("dome-shell-object",)
            if stage == 1
            else ("dome-raw-object", "dome-shell-object")
        )
        revisions = tuple(
            ObjectRevisionPrecondition(
                object_id=object_id,
                expected_digest=prior.object_digest(object_id),
                reason_refs=(f"decision:progressive-dome-stage-{stage}",),
            )
            for object_id in changed_ids
        )
    return GeometryProgramProposal(
        proposal_id=f"radial-geometry-stage-{stage}",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=(
            None if prior is None else prior.program_digest
        ),
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.01, 0.01),
        frames=(
            CoordinateFrame(
                frame_id="world",
                parent_frame_id=None,
                transform_from_parent=AffineTransform.identity(),
                source_refs=evidence,
            ),
        ),
        assets=(),
        semantic_bindings=semantic_bindings,
        operations=tuple(sorted(operations, key=lambda item: item.op_id)),
        assemblies=(),
        revisions=revisions,
    )


class _RadialHallScriptedProvider:
    """Deterministic provider for wiring proof; it is not a live model claim."""

    def __init__(self, context: ProductionAuthoringContext) -> None:
        self.context = context
        self.calls = []
        self.generated_geometry: GeometryProgramProposal | None = None

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        schema = request.payload["schema"]
        if schema == "SemanticSpatialAuthoringPrompt@1":
            radius = 10 if len(self.calls) == 1 else 12
            option_id = (
                "radial-hall-compact"
                if radius == 10
                else "radial-hall-primary"
            )
            output = semantic_spatial_authoring_output(
                request,
                _radial_proposal(
                    self.context,
                    option_id=option_id,
                    radius=radius,
                ),
            )
        elif schema == "SchematicOptionSelectionPrompt@1":
            output = schematic_selection_output(
                request,
                selected_option_id="radial-hall-primary",
                rationale=(
                    "The larger radial option best matches the supplied "
                    "monument-scale current project range."
                ),
            )
        elif schema == "GeometryProposalAuthoringRequest@1":
            state = DevelopedDesignState.from_dict(
                request.payload["developed_design_state"]
            )
            spatial = request.payload["spatial_option_record"]["ref"]
            spatial_ref = ProjectRecordRef(
                project_id=spatial["project_id"],
                relative_path=spatial["relative_path"],
                sha256=spatial["sha256"],
                media_type=spatial["media_type"],
            )
            proposal = _geometry_proposal(state, stage=0)
            self.generated_geometry = replace(
                proposal,
                semantic_bindings=tuple(
                    replace(
                        item,
                        evidence_refs=tuple(
                            sorted({*item.evidence_refs, spatial_ref.uri})
                        ),
                    )
                    for item in proposal.semantic_bindings
                ),
            )
            output = proposal_authoring_output(self.generated_geometry)
        else:
            raise AssertionError(f"unexpected request schema: {schema}")
        encoded = _canonical(output)
        return ModelInvocationReceipt(
            receipt_id=f"scripted-radial-{len(self.calls):02d}",
            status=ModelInvocationStatus.SUCCESS,
            request=request,
            provider_id=IDENTITY.provider_id,
            model_id=IDENTITY.model_id,
            provider_version=IDENTITY.provider_version,
            provider_fingerprint=IDENTITY.provider_fingerprint,
            input_bytes=len(request.payload_json.encode("utf-8")),
            output_bytes=len(encoded.encode("utf-8")),
            output_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            output_json=encoded,
        )


def _next_state(
    state: DevelopedDesignState,
    *,
    stage: int,
) -> DevelopedDesignState:
    proposal = state.selected_schematic.option.proposal
    components = {item.component_id: item for item in proposal.components}
    dome = components["dome"]
    evidence = proposal.evidence_refs
    if stage == 1:
        components["dome"] = replace(
            dome,
            intent="Resolve the dome shell and explicit oculus.",
            maturity=ComponentMaturity.DEVELOPED,
            revision=1,
            unresolved_child_roles=("coffers",),
        )
        components["oculus"] = DesignComponent(
            component_id="oculus",
            parent_component_id="dome",
            semantic_kind="dome-oculus",
            intent="Open the crown of the dome.",
            maturity=ComponentMaturity.DEVELOPED,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=evidence,
        )
    elif stage == 2:
        components["dome"] = replace(
            dome,
            intent="Resolve the dome, oculus, and local coffer field.",
            maturity=ComponentMaturity.DETAILED,
            revision=2,
            unresolved_child_roles=(),
        )
        components["coffers"] = DesignComponent(
            component_id="coffers",
            parent_component_id="dome",
            semantic_kind="local-coffer-field",
            intent="Articulate a bounded interior dome field.",
            maturity=ComponentMaturity.DETAILED,
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=evidence,
        )
    else:
        raise ValueError("stage must be 1 or 2")
    current = replace(
        proposal,
        components=tuple(
            sorted(components.values(), key=lambda item: item.component_id)
        ),
    )
    return replace(
        state,
        selected_schematic=replace(
            state.selected_schematic,
            option=replace(state.selected_schematic.option, proposal=current),
        ),
    )


def _validation_payload(receipt) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "schema": "ValidationReceipt@1",
        "receipt_id": receipt.receipt_id,
        "submission_id": receipt.submission_id,
        "submission_digest": receipt.submission_digest,
        "checked_state": {
            "project_id": receipt.checked_state.project_id,
            "version": receipt.checked_state.version,
            "state_sha256": receipt.checked_state.state_sha256,
        },
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
        "gate_scope": "artifact-presence-only",
        "architectural_usability_claimed": False,
        "canonical_write_authority": False,
    }


def _persist_stage_validation(
    repository: FilesystemProjectRepository,
    *,
    run: RunRef,
    state: DevelopedDesignState,
    program,
    stage: int,
) -> dict[str, object]:
    realization = realize_geometry(
        program,
        workspace_id=f"p058-stage-{stage}",
    )
    assert realization.scene is not None
    view = derive_voxel_view(
        realization.scene,
        realization.receipt,
        policy=VoxelizationPolicy(default_resolution=1.0),
    )
    submission = CandidateSubmission(
        submission_id=f"p058-stage-{stage}",
        base=state.base,
        workspace_id=realization.scene.workspace_id,
        intent="Review the exact sandbox artifact produced for this stage.",
        delta=CandidateDelta(artifacts_add=(view.artifact,)),
        claims=(),
        evidence_refs=(view.artifact.artifact_id,),
    )
    validation = validate_submission(
        CanonicalState(ref=state.base),
        submission,
        (ArtifactPresentValidator(),),
    )
    assert validation.passed
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    scene_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p058-stage-{stage}-sandbox-scene",
        payload=realization.scene.to_dict(),
    )
    realization_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p058-stage-{stage}-sandbox-realization",
        payload=realization.receipt.to_dict(),
    )
    view_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p058-stage-{stage}-voxel-view",
        payload=view.to_dict(),
    )
    validation_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p058-stage-{stage}-validation",
        payload=_validation_payload(validation),
    )
    archive = SandboxArchiveRecord(
        archive_id=f"p058-stage-{stage}-accepted",
        disposition=SandboxArchiveDisposition.ACCEPTED,
        geometry_program_digest=program.program_digest,
        realization_receipt_digest=realization.receipt.receipt_digest,
        scene_digest=realization.scene.scene_digest,
        decision_receipt_digest=validation_ref.sha256,
        evidence_refs=tuple(sorted((scene_ref.uri, validation_ref.uri))),
    )
    archive_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p058-stage-{stage}-sandbox-archive",
        payload=archive.to_dict(),
    )
    return {
        "stage": stage,
        "design_state_digest": state.state_digest,
        "component_proposal_digest": (
            state.selected_schematic.option.proposal.proposal_digest
        ),
        "geometry_program_digest": program.program_digest,
        "scene_digest": realization.scene.scene_digest,
        "realization_receipt_digest": realization.receipt.receipt_digest,
        "validation_receipt_ref": validation_ref.uri,
        "archive_ref": archive_ref.uri,
        "scene_ref": scene_ref.uri,
        "realization_ref": realization_ref.uri,
        "voxel_view_ref": view_ref.uri,
        "validation_scope": "artifact-presence-only",
    }


def _run_proof(root: Path) -> dict[str, object]:
    if root.exists():
        raise FileExistsError(f"proof target already exists: {root}")
    bootstrapped = bootstrap_raw_request_project(
        root,
        project_id=PROJECT_ID,
        prompt=PROMPT,
        run_id=RUN_ID,
        synthetic_test=False,
    )
    repository = FilesystemProjectRepository.open(root)
    run = bootstrapped.run
    context = _rebase_context(_project_context(), run)
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    context_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="production-authoring-context",
        payload=context.to_dict(),
    )
    provider = _RadialHallScriptedProvider(context)
    collector = InvocationEvidenceCollector()
    authorized = activate_model_provider(
        provider,
        identity=ProviderIdentity(
            provider_id=IDENTITY.provider_id,
            version=IDENTITY.provider_version,
            fingerprint=IDENTITY.provider_fingerprint,
        ),
        responsibility_id="model.production-root",
        contract_owner_id="archflow.production-root",
        verification_evidence_refs=(context_ref.uri,),
        envelope_observer=collector.observe,
    )
    compiler = ProductionRootCompiler(
        repository=repository,
        context_ref=context_ref,
        context=context,
        provider=authorized,
        evidence_collector=collector,
        geometry_provider_identity=IDENTITY,
    )
    runtime = asyncio.run(
        run_or_resume_production_step(
            repository,
            run=run,
            raw_request=bootstrapped.request,
            prompt=PROMPT,
            step_id="progressive-pantheon-root",
            compiler=compiler,
        )
    )
    assert len(provider.calls) == 4
    assert provider.generated_geometry is not None
    state_record = next(
        item
        for item in runtime.archive.records
        if item.role is ProductionRecordRole.DESIGN_STATE
    )
    lifecycle_record = next(
        item
        for item in runtime.archive.records
        if item.role is ProductionRecordRole.LIFECYCLE_RECEIPT
    )
    program_record = next(
        item
        for item in runtime.archive.records
        if item.role is ProductionRecordRole.GEOMETRY_PROGRAM
    )
    initial_state = DevelopedDesignState.from_dict(state_record.content)
    compiled = compile_geometry_program(
        initial_state,
        provider.generated_geometry,
        active_commitment_refs=(COMMITMENT_REF,),
    )
    assert compiled.program is not None
    assert compiled.program.program_digest == program_record.semantic_digest
    initial = bind_initial_semantic_geometry(
        transaction_id=lifecycle_record.content["transaction_id"],
        current_state=initial_state,
        current_proposal=initial_state.selected_schematic.option.proposal,
        geometry_program=compiled.program,
        source_refs=tuple(lifecycle_record.content["source_refs"]),
    )
    assert initial.receipt.receipt_digest == runtime.archive.transition_digest

    stage_evidence = [
        {
            **_persist_stage_validation(
                repository,
                run=run,
                state=initial_state,
                program=compiled.program,
                stage=0,
            ),
            "lifecycle_receipt_digest": initial.receipt.receipt_digest,
            "transition_checkpoint_ref": runtime.archive.checkpoint_ref.uri,
            "predecessor_program_digest": None,
            "predecessor_stage": None,
            "stable_component_ids": [
                item.component_id
                for item in initial_state.selected_schematic.option.proposal.components
            ],
        }
    ]
    predecessor_state = initial_state
    predecessor_program = compiled.program
    for stage in (1, 2):
        current_state = _next_state(predecessor_state, stage=stage)
        lifecycle = compile_semantic_geometry_lifecycle(
            transaction_id=f"p058-progressive-stage-{stage}",
            predecessor_state=predecessor_state,
            current_state=current_state,
            predecessor_proposal=(
                predecessor_state.selected_schematic.option.proposal
            ),
            current_proposal=current_state.selected_schematic.option.proposal,
            prior_program=predecessor_program,
            geometry_proposal=_geometry_proposal(
                current_state,
                stage=stage,
                prior=predecessor_program,
            ),
            revalidated_component_ids=("oculus",) if stage == 2 else (),
            active_commitment_refs=(COMMITMENT_REF,),
        )
        if lifecycle.receipt.status is not SemanticGeometryLifecycleStatus.COMPILED:
            raise AssertionError(lifecycle.receipt.issues)
        assert lifecycle.geometry_program is not None
        intent = production_intent_digest(
            run,
            intent={
                "schema": "P058ProgressiveStageIntent@1",
                "stage": stage,
                "predecessor_program_digest": predecessor_program.program_digest,
                "predecessor_stage": stage - 1,
                "current_design_state_digest": current_state.state_digest,
            },
        )
        archive = persist_compiled_production_transition(
            repository,
            run=run,
            intent_digest=intent,
            current_design_state=current_state,
            result=lifecycle,
        )
        validation = _persist_stage_validation(
            repository,
            run=run,
            state=current_state,
            program=lifecycle.geometry_program,
            stage=stage,
        )
        stage_evidence.append(
            {
                **validation,
                "lifecycle_receipt_digest": lifecycle.receipt.receipt_digest,
                "transition_checkpoint_ref": archive.checkpoint_ref.uri,
                "predecessor_program_digest": predecessor_program.program_digest,
                "predecessor_stage": stage - 1,
                "geometry_changed_component_ids": list(
                    lifecycle.receipt.geometry_changed_component_ids
                ),
                "preserved_component_ids": list(
                    lifecycle.receipt.preserved_component_ids
                ),
                "stable_component_ids": sorted(
                    set(
                        item.component_id
                        for item in predecessor_state.selected_schematic.option.proposal.components
                    )
                    & set(
                        item.component_id
                        for item in current_state.selected_schematic.option.proposal.components
                    )
                ),
            }
        )
        predecessor_state = current_state
        predecessor_program = lifecycle.geometry_program

    invocation_records = tuple(
        item
        for item in runtime.archive.records
        if item.role is ProductionRecordRole.PROVIDER_INVOCATION
    )
    manifest = {
        "schema": "P058ProgressivePantheonManifest@1",
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "raw_request_ref": bootstrapped.request.uri,
        "raw_prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "formal_runtime_step_id": runtime.step_id,
        "formal_runtime_checkpoint_ref": runtime.archive.checkpoint_ref.uri,
        "formal_runtime_transition_digest": runtime.archive.transition_digest,
        "provider_mode": "deterministic-scripted-proof",
        "provider_identity": {
            "provider_id": IDENTITY.provider_id,
            "model_id": IDENTITY.model_id,
            "version": IDENTITY.provider_version,
            "fingerprint": IDENTITY.provider_fingerprint,
        },
        "provider_invocations": [
            {
                "ref": item.ref.uri,
                "semantic_digest": item.semantic_digest,
                "responsibility_id": item.content["authority"][
                    "responsibility_id"
                ],
                "authority_epoch": item.content["authority"][
                    "authority_epoch"
                ],
                "provider_id": item.content["authority"]["provider"][
                    "provider_id"
                ],
            }
            for item in invocation_records
        ],
        "stages": stage_evidence,
        "frozen_output_inputs": [],
        "probe_python_files": [],
        "live_agent_cli": {
            "attempted": False,
            "status": "not-run",
            "claim": "No live model result is claimed by this proof.",
        },
        "claims": {
            "formal_runtime_invoked": True,
            "p036_only_persistence": True,
            "p053_provider_envelopes_reloaded": True,
            "semantic_geometry_progression": True,
            "artifact_presence_validation": True,
            "architectural_usability": False,
            "external_platform_equivalence": False,
        },
        "canonical_write_authority": False,
    }
    manifest_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind="p058-progressive-pantheon-manifest",
        payload=manifest,
    )
    return {**manifest, "manifest_ref": manifest_ref.uri}


class ProgressivePantheonIntegrationTests(unittest.TestCase):
    def test_fresh_runtime_and_three_stage_progression_without_frozen_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = _run_proof(Path(temporary) / PROJECT_ID)

        self.assertTrue(manifest["claims"]["formal_runtime_invoked"])
        self.assertEqual([], manifest["frozen_output_inputs"])
        self.assertEqual(4, len(manifest["provider_invocations"]))
        self.assertEqual(
            [0, 1, 2],
            [item["stage"] for item in manifest["stages"]],
        )
        self.assertEqual(
            [None, 0, 1],
            [item["predecessor_stage"] for item in manifest["stages"]],
        )
        self.assertIn(
            "dome",
            manifest["stages"][1]["geometry_changed_component_ids"],
        )
        self.assertIn(
            "coffers",
            manifest["stages"][2]["geometry_changed_component_ids"],
        )
        self.assertTrue(
            all(
                item["validation_scope"] == "artifact-presence-only"
                for item in manifest["stages"]
            )
        )
        self.assertFalse(manifest["claims"]["architectural_usability"])

    def test_promoted_probe_reloads_p036_and_p053_provenance(self) -> None:
        if not (PROBE_ROOT / "project.json").is_file():
            self.skipTest("external workspace evidence probe unavailable")
        repository = FilesystemProjectRepository.open(PROBE_ROOT)
        run = repository.load_run(RUN_ID)
        records = repository.list_json(
            run=run,
            destination=PersistenceDestination(
                PersistenceArea.RUN_RECORD,
                run_id=run.run_id,
            ),
        )
        manifests = [
            repository.load_json(ref)
            for ref in records
            if repository.load_json(ref).get("schema")
            == "P058ProgressivePantheonManifest@1"
        ]

        self.assertEqual(1, len(manifests))
        manifest = manifests[0]
        self.assertEqual(PROJECT_ID, manifest["project_id"])
        self.assertEqual([], manifest["frozen_output_inputs"])
        self.assertEqual(4, len(manifest["provider_invocations"]))
        self.assertEqual(
            {1},
            {
                item["authority_epoch"]
                for item in manifest["provider_invocations"]
            },
        )
        self.assertTrue(
            all(item["ref"].startswith(f"project://{PROJECT_ID}/") for item in manifest["provider_invocations"])
        )
        self.assertFalse(manifest["live_agent_cli"]["attempted"])
        self.assertEqual("not-run", manifest["live_agent_cli"]["status"])

    def test_probe_has_no_case_code_or_external_final_answer(self) -> None:
        if not (PROBE_ROOT / "project.json").is_file():
            self.skipTest("external workspace evidence probe unavailable")
        files = tuple(path for path in PROBE_ROOT.rglob("*") if path.is_file())
        source = Path(__file__).read_text(encoding="utf-8")
        forbidden_source_fragments = (
            "test_" + "pantheon_full_flow",
            "test_" + "pantheon_compiled_state",
            "probes/" + "test_pantheon",
            "probes/" + "p026-sandbox-gold",
        )
        forbidden_project_refs = (
            "project://" + "test_pantheon/",
            "project://" + "p026-sandbox-gold/",
        )
        self.assertTrue(files)
        self.assertFalse(any(path.suffix == ".py" for path in files))
        self.assertFalse(any(path.name.lower().endswith("gold.json") for path in files))
        self.assertFalse((PROBE_ROOT / ".runs").exists())
        self.assertFalse(
            any(value in source for value in forbidden_source_fragments)
        )
        json_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in files
            if path.suffix == ".json"
        )
        self.assertFalse(
            any(value in json_text for value in forbidden_project_refs)
        )


if __name__ == "__main__":
    unittest.main()
