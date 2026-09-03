"""Pantheon-family monument baseline shared by project runners and tests.

A scripted P053 provider (not a live-model claim) derives a half-scale
drum-and-dome monument envelope from raw project input: one root production
step, then three P055 exact-predecessor deepening stages on the same
component identities. Typed linear and radial arrays expand order-tens of
authored operations into three hundred plus realized instances. No golden
geometry is loaded as generated output; the frozen golden sample remains an
external yardstick measured by a separate machine-local tool.

The P065 component graph, identifiers, stage topology, dimensions, and
symmetry subjects below are project-profile facts, not reusable framework
defaults.  Only the scripted-provider and lifecycle plumbing are candidates
for a later neutral extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import replace
from pathlib import Path

from archflow.adapters.model_provider import (
    ModelInvocationReceipt,
    ModelInvocationStatus,
)
from archflow.capabilities.geometry_proposal import (
    proposal_authoring_output,
)
from archflow.capabilities.semantic_spatial_authoring import (
    semantic_spatial_authoring_output,
)
from archflow.production import (
    InvocationEvidenceCollector,
    ProviderIdentity,
    activate_model_provider,
)
from archflow.project import (
    FilesystemProjectRepository,
    PersistenceArea,
    PersistenceDestination,
    bootstrap_raw_request_project,
)
from archflow.project.production_transition import (
    ProductionRecordRole,
    persist_compiled_production_transition,
    production_intent_digest,
)
from archflow.runtime.production_compiler import (
    ProductionRootCompiler,
    schematic_selection_output,
)
from archflow.runtime.production_runtime import (
    ProductionAuthoringContext,
    run_or_resume_production_step,
)
from archflow.runtime.semantic_geometry_lifecycle import (
    SemanticGeometryLifecycleStatus,
    compile_semantic_geometry_lifecycle,
)
from archflow.state import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    ComponentMaturity,
    ConstraintResponseStatus,
    CriterionRef,
    DesignComponent,
    DesignMaturityState,
    DesignPhase,
    MassingVolume,
    PhaseGateRequest,
    ProgramMetricKind,
    ProgramNodeKind,
    SiteBounds,
    SpatialConnection,
    SpatialConstraintResponse,
    SpatialGridBasis,
    SpatialLevel,
    SpatialOptionProposal,
    SpatialZone,
    evaluate_forward_phase_gate,
)
from archflow.state.developed_design import DevelopedDesignState
from archflow.state.geometry_program import (
    GeometryOperationKind,
    GeometryParameterKind,
    GeometryProgramProposal,
    GeometryTolerance,
    LengthUnit,
    ObjectRevisionPrecondition,
    SemanticBinding,
)
from archflow.state.geometry_program import (
    AffineTransform,
    CoordinateFrame,
)
from archflow.realization import (
    SandboxArchiveDisposition,
    SandboxArchiveRecord,
    VoxelizationPolicy,
    derive_voxel_view,
    realize_geometry,
)
from archflow.state import CanonicalState
from archflow.submission import CandidateDelta, CandidateSubmission
from archflow.validation import ArtifactPresentValidator, validate_submission
from tools.projects.pantheon.fixture_support import (
    IDENTITY,
    _inputs,
    _operation,
    _parameter,
    _rebase_context,
    _validation_payload,
)
from archflow.contracts.canonical import canonical_json as _canonical

PROJECT_ID = "p065-monument-derivation"
RUN_ID = "monument-001"
COMMITMENT_REF = "commitment:preserve-monument-envelope"
PROMPT = (
    "Derive a monument-scale domed civic hall with an axial colonnaded "
    "portico inside a neutral platform-independent sandbox, deepening the "
    "same components stage by stage."
)

CENTER_X = 24.0
CENTER_Z = 38.0
DRUM_OUTER = 23.0
DRUM_INNER = 20.0
BASE_Y = 60.0
DRUM_TOP = 84.0
AXIS_COMMITMENT_REF = "commitment:primary-axis-center"


def _axial_row_origin(count, step, width):
    """X origin of a column row derived from the committed primary axis.

    Row layouts are dependent decisions: the origin is computed center-out
    from the axis so the row is symmetric about it by construction, and a
    revised axis reopens the rows through the axis commitment binding.
    """

    return CENTER_X - ((count - 1) * step + width) / 2.0


def _monument_context(
    *,
    project_id: str = PROJECT_ID,
    center_x: float = CENTER_X,
    center_z: float = CENTER_Z,
) -> ProductionAuthoringContext:
    brief, program, site, policy, state, maturity, _ = _inputs(
        project_id=project_id,
    )
    labels = {
        "function-1": "central domed civic hall",
        "function-2": "axial colonnaded portico",
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
                minimum=(
                    1_000.0
                    if item.metric is ProgramMetricKind.FOOTPRINT
                    else item.minimum
                ),
                maximum=(
                    4_000.0
                    if item.metric is ProgramMetricKind.FOOTPRINT
                    else (
                        9_000.0
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
    envelope = SiteBounds(minimum=(0, 60, 0), maximum=(49, 110, 64))
    ground = replace(
        site.ground_model,
        samples=tuple(
            replace(sample, coordinate=coordinate)
            for sample, coordinate in zip(
                site.ground_model.samples,
                ((0, 60, 0), (49, 60, 0), (0, 60, 64), (49, 60, 64)),
                strict=True,
            )
        ),
    )
    site = replace(
        site,
        authorized_envelope=envelope,
        observed_envelope=envelope,
        anchor=(int(round(center_x)), 61, int(round(center_z))),
        ground_model=ground,
        approaches=tuple(
            replace(item, cells=((0, 61, int(round(center_z))),))
            for item in site.approaches
        ),
    )
    policy = replace(
        policy,
        program_digest=program.program_digest,
        site_context_digest=site.context_digest,
    )
    commitment = Commitment(
        commitment_id=COMMITMENT_REF.removeprefix("commitment:"),
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority.user",
        authorized_by="authority.user",
        source_event_ref=brief.raw_request_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id="preserve-monument-envelope",
            provider_id="validator.semantic-geometry",
        ),
        evidence_refs=(brief.raw_request_ref,),
    )
    axis_commitment = Commitment(
        commitment_id=AXIS_COMMITMENT_REF.removeprefix("commitment:"),
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority.user",
        authorized_by="authority.user",
        source_event_ref=brief.raw_request_ref,
        satisfaction_criterion=CriterionRef(
            criterion_id="primary-axis-center",
            provider_id="validator.semantic-geometry",
        ),
        evidence_refs=(brief.raw_request_ref,),
    )
    state = replace(state, commitments=(commitment, axis_commitment))
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
            request_id="enter-monument-schematic",
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
        required_commitment_refs=(COMMITMENT_REF, AXIS_COMMITMENT_REF),
    )


def _monument_proposal(
    context: ProductionAuthoringContext,
    *,
    option_id: str,
    radius: int,
) -> SpatialOptionProposal:
    evidence = (context.program.evidence_refs[0],)
    cx, cz = int(CENTER_X), int(CENTER_Z)
    footprint = tuple(
        sorted(
            {
                *(
                    (x, z)
                    for x in range(cx - radius, cx + radius + 1)
                    for z in range(cz - radius, cz + radius + 1)
                ),
                *((x, z) for x in range(10, 39) for z in range(0, 16)),
            }
        )
    )
    levels = (
        SpatialLevel("dome", 84, 23, evidence),
        SpatialLevel("ground", 60, 24, evidence),
        SpatialLevel("portico", 60, 16, evidence),
    )
    volumes = (
        MassingVolume(
            "dome-volume",
            SiteBounds(
                (cx - radius, 84, cz - radius),
                (cx + radius, 107, cz + radius),
            ),
            ("dome",),
            evidence,
        ),
        MassingVolume(
            "portico-volume",
            SiteBounds((10, 60, 0), (38, 76, 15)),
            ("portico",),
            evidence,
        ),
        MassingVolume(
            "rotunda-volume",
            SiteBounds(
                (cx - radius, 60, cz - radius),
                (cx + radius, 84, cz + radius),
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
        for index, item in enumerate(
            context.build_policy.constraints, start=1
        )
    )
    components = (
        DesignComponent(
            component_id="building",
            parent_component_id=None,
            semantic_kind="monumental-domed-hall",
            intent="Own the monument composition.",
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
            intent="Span the rotunda with a coarse dome.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("dome-volume",),
            unresolved_child_roles=("oculus", "coffers"),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="portico",
            parent_component_id="building",
            semantic_kind="axial-colonnaded-portico",
            intent="Mark the axial public entrance.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("portico-volume",),
            unresolved_child_roles=("colonnade",),
            source_refs=evidence,
        ),
        DesignComponent(
            component_id="rotunda",
            parent_component_id="building",
            semantic_kind="cylindrical-rotunda",
            intent="Form the central domed public room.",
            maturity=ComponentMaturity.SCHEMATIC,
            revision=0,
            volume_ids=("rotunda-volume",),
            unresolved_child_roles=("main-entry",),
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
        label=f"Monument drum radius {radius}",
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
            "Monumental rotunda with a hemispherical dome and an axial "
            "colonnaded portico."
        ),
        palette_refs=("material-intent:massive-masonry",),
        rationale=(
            "The current program and site support a central drum, axial "
            "approach, and dome volume at monument scale."
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


def _dome_sections(
    radius: float,
    *,
    opening_radius: float,
    base_y: float,
    rise: float,
    center_x: float | None = None,
    center_z: float | None = None,
) -> list[list[float]]:
    center_x = CENTER_X if center_x is None else center_x
    center_z = CENTER_Z if center_z is None else center_z
    points: list[list[float]] = []
    fractions = (0.0, 0.35, 0.6, 0.8, 0.93, 1.0)
    for t in fractions:
        y = base_y + t * rise
        section_radius = (
            opening_radius
            if t == 1.0
            else max(radius * math.sqrt(1.0 - t * t), opening_radius)
        )
        for index in range(24):
            angle = 2.0 * math.pi * index / 24.0
            points.append(
                [
                    center_x + section_radius * math.cos(angle),
                    y,
                    center_z + section_radius * math.sin(angle),
                ]
            )
    return points


def _solid(op_id, output, binding, origin, size):
    return _operation(
        op_id,
        GeometryOperationKind.SOLID,
        output,
        binding,
        (
            _parameter(
                "origin",
                GeometryParameterKind.VECTOR3,
                [float(item) for item in origin],
                unit=LengthUnit.METER,
            ),
            _parameter(
                "size",
                GeometryParameterKind.VECTOR3,
                [float(item) for item in size],
                unit=LengthUnit.METER,
            ),
        ),
    )


def _cylinder(
    op_id,
    output,
    binding,
    y0,
    y1,
    radius,
    *,
    center_x=None,
    center_z=None,
):
    center_x = CENTER_X if center_x is None else center_x
    center_z = CENTER_Z if center_z is None else center_z
    return _operation(
        op_id,
        GeometryOperationKind.REVOLVE,
        output,
        binding,
        (
            _parameter(
                "axis_end",
                GeometryParameterKind.VECTOR3,
                [center_x, float(y1), center_z],
                unit=LengthUnit.METER,
            ),
            _parameter(
                "axis_start",
                GeometryParameterKind.VECTOR3,
                [center_x, float(y0), center_z],
                unit=LengthUnit.METER,
            ),
            _parameter(
                "end_radius",
                GeometryParameterKind.NUMBER,
                float(radius),
                unit=LengthUnit.METER,
            ),
            _parameter(
                "start_radius",
                GeometryParameterKind.NUMBER,
                float(radius),
                unit=LengthUnit.METER,
            ),
        ),
    )


def _difference(op_id, output, binding, inputs, base_id):
    ordered = tuple(sorted(inputs))
    return _operation(
        op_id,
        GeometryOperationKind.BOOLEAN_DIFFERENCE,
        output,
        binding,
        (
            _parameter(
                "base_index",
                GeometryParameterKind.INTEGER,
                ordered.index(base_id),
            ),
        ),
        inputs=ordered,
        responds_to_binding=True,
        responds_to_inputs=True,
    )


def _loft(op_id, output, binding, sections):
    return _operation(
        op_id,
        GeometryOperationKind.LOFT,
        output,
        binding,
        (
            _parameter("cap_ends", GeometryParameterKind.BOOLEAN, True),
            _parameter("closed_profile", GeometryParameterKind.BOOLEAN, True),
            _parameter("profile_size", GeometryParameterKind.INTEGER, 24),
            _parameter(
                "profiles",
                GeometryParameterKind.POINTS3,
                sections,
                unit=LengthUnit.METER,
            ),
        ),
    )


def _linear_array(op_id, output, binding, source, count, step):
    return _operation(
        op_id,
        GeometryOperationKind.ARRAY,
        output,
        binding,
        (
            _parameter("count", GeometryParameterKind.INTEGER, count),
            _parameter(
                "step",
                GeometryParameterKind.VECTOR3,
                [float(item) for item in step],
                unit=LengthUnit.METER,
            ),
        ),
        inputs=(source,),
        responds_to_inputs=True,
    )


def _radial_array(op_id, output, binding, source, count, angle_step):
    return _operation(
        op_id,
        GeometryOperationKind.RADIAL_ARRAY,
        output,
        binding,
        (
            _parameter(
                "angle_step_degrees",
                GeometryParameterKind.NUMBER,
                angle_step,
            ),
            _parameter(
                "axis", GeometryParameterKind.VECTOR3, [0.0, 1.0, 0.0]
            ),
            _parameter(
                "center",
                GeometryParameterKind.VECTOR3,
                [CENTER_X, 0.0, CENTER_Z],
                unit=LengthUnit.METER,
            ),
            _parameter("count", GeometryParameterKind.INTEGER, count),
        ),
        inputs=(source,),
        responds_to_inputs=True,
    )


COFFER_RINGS = 5
COFFER_COUNT = 28


def _monument_geometry(
    state: DevelopedDesignState,
    *,
    stage: int,
    prior: object = None,
) -> GeometryProgramProposal:
    evidence = state.selected_schematic.option.proposal.evidence_refs
    operations = [
        _solid("plinth", "plinth-object", "rotunda-binding",
               [CENTER_X - 23.5, 60, 0], [47, 2, 62]),
        _cylinder("drum-outer", "drum-outer-object", "rotunda-binding",
                  62, 84, DRUM_OUTER),
        _cylinder("drum-inner", "drum-inner-object", "rotunda-binding",
                  62, 85, DRUM_INNER),
        _loft(
            "dome-outer",
            "dome-outer-object",
            "dome-binding",
            _dome_sections(DRUM_OUTER, opening_radius=1.0,
                           base_y=84, rise=23),
        ),
        _loft(
            "dome-inner",
            "dome-inner-object",
            "dome-binding",
            _dome_sections(DRUM_INNER, opening_radius=0.8,
                           base_y=84, rise=20),
        ),
    ]
    bindings: dict[str, tuple[str, set[str]]] = {
        "rotunda-binding": (
            "rotunda",
            {
                "plinth-object",
                "drum-outer-object",
                "drum-inner-object",
                "drum-wall-object",
            },
        ),
        "dome-binding": (
            "dome",
            {"dome-outer-object", "dome-inner-object", "dome-shell-object"},
        ),
        "portico-binding": ("portico", {"portico-mass-object"}),
    }
    drum_cut_inputs = {"drum-inner-object", "drum-outer-object"}
    dome_cut_inputs = {"dome-inner-object", "dome-outer-object"}
    if stage == 0:
        operations.append(
            _solid("portico-mass", "portico-mass-object", "portico-binding",
                   [10, 62, 0], [28, 12, 15])
        )
    if stage >= 1:
        operations.extend(
            (
                _solid("portico-mass", "portico-mass-object",
                       "portico-binding", [10, 74, 0], [28, 2, 15]),
                _solid("col-front-seed", "col-front-seed-object",
                       "colonnade-binding",
                       [_axial_row_origin(8, 3.5, 2), 62, 2], [2, 10, 2]),
                _linear_array("col-front-ring", "col-front-ring-object",
                              "colonnade-binding", "col-front-seed-object",
                              8, [3.5, 0, 0]),
                _solid("col-rear-seed", "col-rear-seed-object",
                       "colonnade-binding",
                       [_axial_row_origin(8, 3.5, 2), 62, 11], [2, 10, 2]),
                _linear_array("col-rear-ring", "col-rear-ring-object",
                              "colonnade-binding", "col-rear-seed-object",
                              8, [3.5, 0, 0]),
                _solid("cap-front-seed", "cap-front-seed-object",
                       "colonnade-binding",
                       [_axial_row_origin(8, 3.5, 3), 72, 1.5], [3, 1, 3]),
                _linear_array("cap-front-ring", "cap-front-ring-object",
                              "colonnade-binding", "cap-front-seed-object",
                              8, [3.5, 0, 0]),
                _solid("cap-rear-seed", "cap-rear-seed-object",
                       "colonnade-binding",
                       [_axial_row_origin(8, 3.5, 3), 72, 10.5], [3, 1, 3]),
                _linear_array("cap-rear-ring", "cap-rear-ring-object",
                              "colonnade-binding", "cap-rear-seed-object",
                              8, [3.5, 0, 0]),
                _solid("beam-seed", "beam-seed-object", "colonnade-binding",
                       [_axial_row_origin(9, 3.3, 1.4), 73, 1],
                       [1.4, 1.2, 13]),
                _linear_array("beam-ring", "beam-ring-object",
                              "colonnade-binding", "beam-seed-object",
                              9, [3.3, 0, 0]),
                _solid("door-tool", "door-tool-object", "entry-binding",
                       [22, 62, 13], [4, 8, 5]),
            )
        )
        bindings["colonnade-binding"] = (
            "colonnade",
            {
                "col-front-seed-object", "col-front-ring-object",
                "col-rear-seed-object", "col-rear-ring-object",
                "cap-front-seed-object", "cap-front-ring-object",
                "cap-rear-seed-object", "cap-rear-ring-object",
                "beam-seed-object", "beam-ring-object",
            },
        )
        bindings["entry-binding"] = ("main-entry", {"door-tool-object"})
        drum_cut_inputs.add("door-tool-object")
    if stage >= 2:
        operations.extend(
            (
                _solid("recess-seed", "recess-seed-object", "recess-binding",
                       [40, 63, 36], [3, 6, 3]),
                _radial_array("recess-ring", "recess-ring-object",
                              "recess-binding", "recess-seed-object",
                              15, 24.0),
                _solid("aedicula-seed", "aedicula-seed-object",
                       "aedicula-binding", [39, 70, 37], [2, 3, 2]),
                _radial_array("aedicula-ring", "aedicula-ring-object",
                              "aedicula-binding", "aedicula-seed-object",
                              36, 10.0),
                _cylinder("oculus-tool", "oculus-tool-object",
                          "oculus-binding", 103, 108, 4.0),
            )
        )
        bindings["recess-binding"] = (
            "recess-ring", {"recess-seed-object", "recess-ring-object"}
        )
        bindings["aedicula-binding"] = (
            "aedicula-ring", {"aedicula-seed-object", "aedicula-ring-object"}
        )
        bindings["oculus-binding"] = ("oculus", {"oculus-tool-object"})
        dome_cut_inputs.add("oculus-tool-object")
    if stage >= 3:
        coffer_objects: set[str] = set()
        for ring in range(COFFER_RINGS):
            y = 85.0 + ring * 2.5
            dy = y - 84.0
            radius = math.sqrt(DRUM_INNER**2 - dy * dy) - 0.8
            seed_id = f"coffer-seed-{ring}"
            ring_id = f"coffer-ring-{ring}"
            operations.extend(
                (
                    _solid(seed_id, f"{seed_id}-object", "coffer-binding",
                           [CENTER_X + radius - 0.8, y, CENTER_Z - 0.8],
                           [1.6, 1.6, 1.6]),
                    _radial_array(ring_id, f"{ring_id}-object",
                                  "coffer-binding", f"{seed_id}-object",
                                  COFFER_COUNT, 360.0 / COFFER_COUNT),
                )
            )
            coffer_objects.update({f"{seed_id}-object", f"{ring_id}-object"})
        operations.extend(
            (
                _solid("statuary-seed", "statuary-seed-object",
                       "statuary-binding", [46.5, 84, 37.5], [1, 3, 1]),
                _radial_array("statuary-ring", "statuary-ring-object",
                              "statuary-binding", "statuary-seed-object",
                              72, 5.0),
            )
        )
        bindings["coffer-binding"] = ("coffers", coffer_objects)
        bindings["statuary-binding"] = (
            "statuary-ring",
            {"statuary-seed-object", "statuary-ring-object"},
        )
    operations.append(
        _difference(
            "drum-wall",
            "drum-wall-object",
            "rotunda-binding",
            tuple(sorted({*drum_cut_inputs})),
            "drum-outer-object",
        )
    )
    operations.append(
        _difference(
            "dome-shell",
            "dome-shell-object",
            "dome-binding",
            tuple(sorted({*dome_cut_inputs})),
            "dome-outer-object",
        )
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
            commitment_refs=(COMMITMENT_REF, AXIS_COMMITMENT_REF),
            evidence_refs=prior_binding_evidence.get(component_id, evidence),
        )
        for binding_id, (component_id, object_ids) in sorted(
            bindings.items()
        )
    )
    revisions = ()
    if prior is not None:
        cumulative_revised = {
            component_id
            for prior_stage in range(1, stage + 1)
            for component_id in _STAGE_REVISED[prior_stage]
        }
        flagged_bindings = {
            binding_id
            for binding_id, (component_id, _) in bindings.items()
            if component_id in cumulative_revised
        }
        revised_components = set(_STAGE_REVISED[stage])
        revised_bindings = {
            binding_id
            for binding_id, (component_id, _) in bindings.items()
            if component_id in revised_components
        }
        operations = [
            replace(
                op,
                responds_to_binding_ids=tuple(
                    sorted(
                        {
                            *op.responds_to_binding_ids,
                            *(
                                set(op.semantic_binding_ids)
                                & flagged_bindings
                            ),
                        }
                    )
                ),
            )
            if set(op.semantic_binding_ids) & flagged_bindings
            else op
            for op in operations
        ]
        prior_objects = {
            item.object_id for item in prior.objects
        }
        changed_objects = sorted(
            {
                object_id
                for op in operations
                if set(op.semantic_binding_ids) & revised_bindings
                for object_id in op.output_object_ids
                if object_id in prior_objects
            }
        )
        revisions = tuple(
            ObjectRevisionPrecondition(
                object_id=object_id,
                expected_digest=prior.object_digest(object_id),
                reason_refs=(f"decision:monument-stage-{stage}",),
            )
            for object_id in changed_objects
        )
    return GeometryProgramProposal(
        proposal_id=f"monument-geometry-stage-{stage}",
        project_id=state.project_id,
        run_id=state.run_id,
        base=state.base,
        design_state_digest=state.state_digest,
        predecessor_program_digest=(
            None if prior is None else prior.program_digest
        ),
        length_unit=LengthUnit.METER,
        tolerance=GeometryTolerance(0.001, 0.001),
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
        retirements=(),
    )


class _MonumentScriptedProvider:
    """Deterministic provider for wiring proof; it is not a live-model claim."""

    def __init__(self, context: ProductionAuthoringContext) -> None:
        self.context = context
        self.calls = []
        self.generated_geometry: GeometryProgramProposal | None = None

    async def invoke(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        schema = request.payload["schema"]
        if schema == "SemanticSpatialAuthoringPrompt@1":
            radius = 20 if len(self.calls) == 1 else 23
            option_id = (
                "monument-compact" if radius == 20 else "monument-primary"
            )
            output = semantic_spatial_authoring_output(
                request,
                _monument_proposal(
                    self.context, option_id=option_id, radius=radius
                ),
            )
        elif schema == "SchematicOptionSelectionPrompt@1":
            output = schematic_selection_output(
                request,
                selected_option_id="monument-primary",
                rationale=(
                    "The larger drum best fills the authorized monument "
                    "envelope and program range."
                ),
            )
        elif schema == "GeometryProposalAuthoringRequest@1":
            state = DevelopedDesignState.from_dict(
                request.payload["developed_design_state"]
            )
            spatial = request.payload["spatial_option_record"]["ref"]
            proposal = _monument_geometry(state, stage=0)
            spatial_uri = (
                f"project://{spatial['project_id']}/"
                f"{spatial['relative_path']}"
            )
            self.generated_geometry = replace(
                proposal,
                semantic_bindings=tuple(
                    replace(
                        item,
                        evidence_refs=tuple(
                            sorted({*item.evidence_refs, spatial_uri})
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
            receipt_id=f"scripted-monument-{len(self.calls):02d}",
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


_STAGE_COMPONENTS = {
    1: (
        ("colonnade", "portico", "portico-colonnade",
         "March the paired column rows of the portico."),
        ("main-entry", "rotunda", "axial-opening",
         "Cut the axial doorway through the drum."),
    ),
    2: (
        ("recess-ring", "rotunda", "interior-recess-ring",
         "Ring the interior wall with niches."),
        ("aedicula-ring", "rotunda", "aedicula-ring",
         "Ring the interior wall with aediculae."),
        ("oculus", "dome", "dome-oculus",
         "Open the crown of the dome."),
    ),
    3: (
        ("coffers", "dome", "radial-coffer-field",
         "Articulate the dome interior with radial coffer rings."),
        ("statuary-ring", "building", "attic-ornament-ring",
         "Crown the drum attic with an ornament ring."),
    ),
}

_STAGE_REVISED = {
    1: {"portico": ComponentMaturity.DEVELOPED,
        "rotunda": ComponentMaturity.DEVELOPED},
    2: {"rotunda": ComponentMaturity.DETAILED,
        "dome": ComponentMaturity.DEVELOPED},
    3: {"dome": ComponentMaturity.DETAILED},
}


def _next_state(
    state: DevelopedDesignState,
    *,
    stage: int,
    stage_components=None,
    stage_revisions=None,
) -> DevelopedDesignState:
    components_for_stage = (
        _STAGE_COMPONENTS[stage]
        if stage_components is None
        else tuple(stage_components)
    )
    revisions_for_stage = (
        _STAGE_REVISED[stage]
        if stage_revisions is None
        else dict(stage_revisions)
    )
    proposal = state.selected_schematic.option.proposal
    components = {item.component_id: item for item in proposal.components}
    evidence = proposal.evidence_refs
    for component_id, maturity in revisions_for_stage.items():
        existing = components[component_id]
        remaining = tuple(
            role
            for role in existing.unresolved_child_roles
            if role
            not in {
                spec[0] for spec in components_for_stage
            }
        )
        components[component_id] = replace(
            existing,
            maturity=maturity,
            revision=existing.revision + 1,
            unresolved_child_roles=remaining,
        )
    for component_id, parent, kind, intent in components_for_stage:
        components[component_id] = DesignComponent(
            component_id=component_id,
            parent_component_id=parent,
            semantic_kind=kind,
            intent=intent,
            maturity=(
                ComponentMaturity.DETAILED
                if stage == 3
                else ComponentMaturity.DEVELOPED
            ),
            revision=0,
            volume_ids=(),
            unresolved_child_roles=(),
            source_refs=evidence,
        )
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


class StageGateError(AssertionError):
    """A project criterion failed at stage acceptance: no ACCEPTED record."""


SYMMETRY_GATE_TOLERANCE = 1e-6


def _stage_symmetry_findings(
    scene_objects,
    stage: int,
    *,
    axis_value: float | None = None,
):
    """Project-supplied symmetry subjects for one monument stage."""

    from archflow.evaluation import axial_group_offsets

    axis = CENTER_X if axis_value is None else float(axis_value)

    physical_groups = {
        "portico": ("portico-binding",),
        "rotunda": ("rotunda-binding",),
        "dome": ("dome-binding",),
    }
    findings = list(
        axial_group_offsets(
            scene_objects,
            axis_value=axis,
            axis_index=0,
            groups=physical_groups,
        )
    )
    if stage >= 1:
        findings += list(
            axial_group_offsets(
                scene_objects,
                axis_value=axis,
                axis_index=0,
                groups={
                    "colonnade": ("colonnade-binding",),
                },
            )
        )
        findings += list(
            axial_group_offsets(
                scene_objects,
                axis_value=axis,
                axis_index=0,
                groups={"main-entry": ("entry-binding",)},
                physical_only=False,
            )
        )
    return findings


def _persist_stage(
    repository,
    *,
    run,
    state,
    program,
    stage: int,
    symmetry_findings_hook=None,
    symmetry_axis_value: float | None = None,
) -> dict[str, object]:
    """P065 stage evidence: scene, receipt, voxel view, and validation.

    The workspace realization is measurement substrate, never
    acceptance: the symmetry criterion is evaluated before any archive
    disposition, and a failing stage writes REJECTED plus a typed
    error — an ACCEPTED record for a failing realization cannot exist.
    """

    realization = realize_geometry(
        program,
        workspace_id=f"p065-stage-{stage}",
    )
    assert realization.scene is not None, realization.receipt.issues
    view = derive_voxel_view(
        realization.scene,
        realization.receipt,
        policy=VoxelizationPolicy(default_resolution=1.0),
    )
    destination = PersistenceDestination(
        PersistenceArea.RUN_RECORD,
        run_id=run.run_id,
    )
    scene_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p065-stage-{stage}-sandbox-scene",
        payload=realization.scene.to_dict(),
    )
    repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p065-stage-{stage}-sandbox-realization",
        payload=realization.receipt.to_dict(),
    )
    view_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p065-stage-{stage}-voxel-view",
        payload=view.to_dict(),
    )
    symmetry_hook = (
        _stage_symmetry_findings
        if symmetry_findings_hook is None
        else symmetry_findings_hook
    )
    if not callable(symmetry_hook):
        raise TypeError("symmetry_findings_hook must be callable")
    findings = symmetry_hook(realization.scene.to_dict()["objects"], stage)
    worst_offset = max(abs(item.offset) for item in findings)
    gate_passed = worst_offset <= SYMMETRY_GATE_TOLERANCE
    gate_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p065-stage-{stage}-symmetry-gate",
        payload={
            "schema": "AxialSymmetryGate@1",
            "stage": stage,
            "axis_value": (
                CENTER_X
                if symmetry_axis_value is None
                else float(symmetry_axis_value)
            ),
            "axis_index": 0,
            "axis_commitment_ref": AXIS_COMMITMENT_REF,
            "tolerance": SYMMETRY_GATE_TOLERANCE,
            "findings": [item.to_dict() for item in findings],
            "max_abs_offset": worst_offset,
            "status": "pass" if gate_passed else "fail",
            "canonical_write_authority": False,
        },
    )
    if not gate_passed:
        rejected = SandboxArchiveRecord(
            archive_id=f"p065-stage-{stage}-rejected",
            disposition=SandboxArchiveDisposition.REJECTED,
            geometry_program_digest=program.program_digest,
            realization_receipt_digest=realization.receipt.receipt_digest,
            scene_digest=realization.scene.scene_digest,
            decision_receipt_digest=gate_ref.sha256,
            evidence_refs=tuple(sorted((scene_ref.uri, gate_ref.uri))),
        )
        repository.put_json(
            run=run,
            destination=destination,
            record_kind=f"p065-stage-{stage}-sandbox-archive",
            payload=rejected.to_dict(),
        )
        raise StageGateError(
            f"stage {stage} symmetry gate failed: max offset "
            f"{worst_offset:.4f} m exceeds {SYMMETRY_GATE_TOLERANCE}; "
            f"stage archived REJECTED at {gate_ref.uri}"
        )
    submission = CandidateSubmission(
        submission_id=f"p065-stage-{stage}",
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
    validation_ref = repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p065-stage-{stage}-validation",
        payload=_validation_payload(validation),
    )
    archive = SandboxArchiveRecord(
        archive_id=f"p065-stage-{stage}-accepted",
        disposition=SandboxArchiveDisposition.ACCEPTED,
        geometry_program_digest=program.program_digest,
        realization_receipt_digest=realization.receipt.receipt_digest,
        scene_digest=realization.scene.scene_digest,
        decision_receipt_digest=validation_ref.sha256,
        evidence_refs=tuple(
            sorted((scene_ref.uri, gate_ref.uri, validation_ref.uri))
        ),
    )
    repository.put_json(
        run=run,
        destination=destination,
        record_kind=f"p065-stage-{stage}-sandbox-archive",
        payload=archive.to_dict(),
    )
    return {
        "design_state_digest": state.state_digest,
        "geometry_program_digest": program.program_digest,
        "scene_digest": realization.scene.scene_digest,
        "occupied_cell_count": len(view.occupied_cells),
        "voxel_view_ref": view_ref.uri,
        "validation_scope": "artifact-presence-only",
    }


def _instance_count(scene) -> int:
    """Realized typed instances: array replica counts plus physical singles."""

    import json as _json

    total = 0
    for item in scene.objects:
        if not item.physical:
            continue
        geometry = _json.loads(item.geometry_json)
        kind = geometry.get("kind")
        if kind in ("array", "radial_array"):
            total += int(geometry["count"])
        else:
            total += 1
    return total


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
    context = _rebase_context(_monument_context(), run)
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
    provider = _MonumentScriptedProvider(context)
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
            step_id="monument-root",
            compiler=compiler,
        )
    )
    state_record = next(
        item
        for item in runtime.archive.records
        if item.role is ProductionRecordRole.DESIGN_STATE
    )
    initial_state = DevelopedDesignState.from_dict(state_record.content)
    from archflow.compilers.geometry import compile_geometry_program

    compiled = compile_geometry_program(
        initial_state,
        provider.generated_geometry,
        active_commitment_refs=(COMMITMENT_REF, AXIS_COMMITMENT_REF),
    )
    assert compiled.program is not None, compiled.receipt.issues
    stages = [
        {
            "stage": 0,
            **_persist_stage(
                repository,
                run=run,
                state=initial_state,
                program=compiled.program,
                stage=0,
            ),
        }
    ]
    predecessor_state = initial_state
    predecessor_program = compiled.program
    for stage in (1, 2, 3):
        current_state = _next_state(predecessor_state, stage=stage)
        lifecycle = compile_semantic_geometry_lifecycle(
            transaction_id=f"monument-stage-{stage}",
            predecessor_state=predecessor_state,
            current_state=current_state,
            predecessor_proposal=(
                predecessor_state.selected_schematic.option.proposal
            ),
            current_proposal=(
                current_state.selected_schematic.option.proposal
            ),
            prior_program=predecessor_program,
            geometry_proposal=_monument_geometry(
                current_state, stage=stage, prior=predecessor_program
            ),
            revalidated_component_ids=(
                ("main-entry",)
                if stage == 2
                else ("oculus",) if stage == 3 else ()
            ),
            active_commitment_refs=(COMMITMENT_REF, AXIS_COMMITMENT_REF),
        )
        if (
            lifecycle.receipt.status
            is not SemanticGeometryLifecycleStatus.COMPILED
        ):
            raise AssertionError(lifecycle.receipt.issues)
        assert lifecycle.geometry_program is not None
        intent = production_intent_digest(
            run,
            intent={
                "schema": "P065MonumentStageIntent@1",
                "stage": stage,
                "predecessor_program_digest": (
                    predecessor_program.program_digest
                ),
                "current_design_state_digest": current_state.state_digest,
            },
        )
        persist_compiled_production_transition(
            repository,
            run=run,
            intent_digest=intent,
            current_design_state=current_state,
            result=lifecycle,
        )
        stages.append(
            {
                "stage": stage,
                **_persist_stage(
                    repository,
                    run=run,
                    state=current_state,
                    program=lifecycle.geometry_program,
                    stage=stage,
                ),
                "preserved_component_ids": list(
                    lifecycle.receipt.preserved_component_ids
                ),
                "geometry_changed_component_ids": list(
                    lifecycle.receipt.geometry_changed_component_ids
                ),
            }
        )
        predecessor_state = current_state
        predecessor_program = lifecycle.geometry_program

    from archflow.realization import realize_geometry

    final = realize_geometry(
        predecessor_program, workspace_id="monument-final"
    )
    return {
        "provider_invocations": len(provider.calls),
        "stages": stages,
        "final_scene": final.scene,
        "final_state": predecessor_state,
        "final_program": predecessor_program,
    }


