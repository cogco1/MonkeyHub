"""Deterministic compilation of Architect-authored schematic alternatives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from archive.archflow.state.build_policy import (
    BuildPolicy,
    PolicyConstraintStrength,
)
from archflow.state.design_maturity import (
    DesignMaturityState,
    DesignPhase,
    PhaseGateReceipt,
    require_current_phase_gate,
)
from archive.archflow.state.design_program import (
    DesignProgram,
    ProgramMetricKind,
    ProgramNodeKind,
    ProgramRelationshipStrength,
)
from archflow.state.operational_state import (
    ObligationStatus,
    OperationalMarkovState,
)
from archflow.state.site_context import SiteContext
from archflow.state.spatial import (
    SchematicOption,
    SchematicOptionSet,
    SpatialOptionProposal,
)
from archflow.contracts.canonical import canonical_digest, canonical_json


_COMPILER_ID = "archflow.spatial-proposal-compiler"
_COMPILER_VERSION = "1.0.0"


class SpatialCompilationError(ValueError):
    """A proposal cannot be compiled against the current design context."""


@dataclass(frozen=True, slots=True)
class SpatialAuthoringReferenceContract:
    """Exact project refs exposed to and enforced against one proposal."""

    allowed_evidence_refs: tuple[str, ...]
    allowed_responds_to_refs: tuple[str, ...]
    required_response_refs: tuple[str, ...]
    allowed_expert_advice_refs: tuple[str, ...] = ()

    SCHEMA = "SpatialAuthoringReferenceContract@1"

    def __post_init__(self) -> None:
        for value, field in (
            (self.allowed_evidence_refs, "allowed_evidence_refs"),
            (self.allowed_responds_to_refs, "allowed_responds_to_refs"),
            (self.required_response_refs, "required_response_refs"),
            (self.allowed_expert_advice_refs, "allowed_expert_advice_refs"),
        ):
            if (
                not isinstance(value, tuple)
                or value != tuple(sorted(set(value)))
                or any(not isinstance(item, str) or not item for item in value)
            ):
                raise SpatialCompilationError(
                    f"{field} must be a sorted unique string tuple"
                )
        if not set(self.required_response_refs) <= set(
            self.allowed_responds_to_refs
        ):
            raise SpatialCompilationError(
                "required response refs must be allowed response refs"
            )
        if not set(self.allowed_expert_advice_refs) <= set(
            self.allowed_evidence_refs
        ):
            raise SpatialCompilationError(
                "expert advice refs must be current evidence refs"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "allowed_evidence_refs": list(self.allowed_evidence_refs),
            "allowed_responds_to_refs": list(self.allowed_responds_to_refs),
            "required_response_refs": list(self.required_response_refs),
            "allowed_expert_advice_refs": list(
                self.allowed_expert_advice_refs
            ),
            "nested_source_rule": (
                "every nested source_ref must also appear in proposal.evidence_refs"
            ),
            "unknown_reference_policy": "reject",
            "reference_normalization_authority": False,
        }


def _policy_ref(prefix: str, local_id: str) -> str:
    return f"build-{prefix}:{local_id}"


def _obligation_refs(values: tuple[Any, ...]) -> set[str]:
    return {
        f"obligation:{item.obligation_id}"
        for item in values
        if item.status in {ObligationStatus.OPEN, ObligationStatus.BLOCKED}
    }


def _require_bound_inputs(
    *,
    state: OperationalMarkovState,
    maturity: DesignMaturityState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> None:
    if not isinstance(state, OperationalMarkovState):
        raise TypeError("state must be OperationalMarkovState")
    if not isinstance(maturity, DesignMaturityState):
        raise TypeError("maturity must be DesignMaturityState")
    if not isinstance(phase_gate, PhaseGateReceipt):
        raise TypeError("phase_gate must be PhaseGateReceipt")
    if not isinstance(program, DesignProgram):
        raise TypeError("program must be DesignProgram")
    if not isinstance(site_context, SiteContext):
        raise TypeError("site_context must be SiteContext")
    if not isinstance(build_policy, BuildPolicy):
        raise TypeError("build_policy must be BuildPolicy")
    maturity.require_exact_operational_state(state)
    if maturity.phase is not DesignPhase.SITE_RESOURCE_COORDINATION:
        raise SpatialCompilationError(
            "spatial compilation requires site_resource_coordination input"
        )
    require_current_phase_gate(maturity, phase_gate)
    if phase_gate.to_phase is not DesignPhase.SCHEMATIC_DESIGN:
        raise SpatialCompilationError(
            "spatial compilation gate must enter schematic_design"
        )
    branch = state.branch
    expected = (
        branch.run.project_id,
        branch.run.run_id,
        branch.run.base,
    )
    for label, current in (
        (
            "program",
            (program.project_id, program.run_id, program.base),
        ),
        (
            "site context",
            (
                site_context.project_id,
                site_context.run_id,
                site_context.base,
            ),
        ),
        (
            "build policy",
            (
                build_policy.project_id,
                build_policy.run_id,
                build_policy.base,
            ),
        ),
    ):
        if current != expected:
            raise SpatialCompilationError(
                f"{label} is stale or belongs to another run"
            )
    if not (
        program.brief_digest
        == site_context.brief_digest
        == build_policy.brief_digest
    ):
        raise SpatialCompilationError("upstream brief digests disagree")
    if (
        build_policy.program_digest != program.program_digest
        or build_policy.site_context_digest
        != site_context.context_digest
    ):
        raise SpatialCompilationError(
            "build policy is not bound to current program and site"
        )


def _known_response_refs(
    *,
    state: OperationalMarkovState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> set[str]:
    return {
        phase_gate.ref,
        *(item.ref for item in program.nodes),
        *(item.ref for item in program.ranges),
        *(item.ref for item in program.relationships),
        *(item.ref for item in program.scenarios),
        *(_obligation_refs(program.obligations)),
        *(_obligation_refs(site_context.obligations)),
        *(_obligation_refs(build_policy.obligations)),
        *(_obligation_refs(state.obligations)),
        *(
            f"site-unknown:{item.unknown_id}"
            for item in site_context.unknowns
        ),
        *(
            _policy_ref("protected-rule", item.rule_id)
            for item in build_policy.protected_rules
        ),
        *(
            _policy_ref("budget", item.budget_id)
            for item in build_policy.budget_limits
        ),
        *(
            _policy_ref("constraint", item.constraint_id)
            for item in build_policy.constraints
        ),
    }


def _required_response_refs(
    *,
    state: OperationalMarkovState,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> set[str]:
    return {
        *(_obligation_refs(program.obligations)),
        *(_obligation_refs(site_context.obligations)),
        *(_obligation_refs(build_policy.obligations)),
        *(_obligation_refs(state.obligations)),
        *(
            item.ref
            for item in program.relationships
            if item.strength is ProgramRelationshipStrength.REQUIRED
        ),
        *(
            _policy_ref("protected-rule", item.rule_id)
            for item in build_policy.protected_rules
        ),
        *(
            _policy_ref("constraint", item.constraint_id)
            for item in build_policy.constraints
            if item.strength is PolicyConstraintStrength.HARD
        ),
    }


def _allowed_evidence_refs(
    *,
    state: OperationalMarkovState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> set[str]:
    return {
        phase_gate.ref,
        *state.evidence_refs,
        *program.evidence_refs,
        *site_context.evidence_refs,
        *build_policy.evidence_refs,
    }


def compile_spatial_authoring_reference_contract(
    *,
    state: OperationalMarkovState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> SpatialAuthoringReferenceContract:
    """Compile the one reference vocabulary shared by prompt and validator."""

    for value, expected, field in (
        (state, OperationalMarkovState, "state"),
        (phase_gate, PhaseGateReceipt, "phase_gate"),
        (program, DesignProgram, "program"),
        (site_context, SiteContext, "site_context"),
        (build_policy, BuildPolicy, "build_policy"),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{field} must be {expected.__name__}")
    allowed_responses = _known_response_refs(
        state=state,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )
    required_responses = _required_response_refs(
        state=state,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )
    return SpatialAuthoringReferenceContract(
        allowed_evidence_refs=tuple(
            sorted(
                _allowed_evidence_refs(
                    state=state,
                    phase_gate=phase_gate,
                    program=program,
                    site_context=site_context,
                    build_policy=build_policy,
                )
            )
        ),
        allowed_responds_to_refs=tuple(sorted(allowed_responses)),
        required_response_refs=tuple(sorted(required_responses)),
        allowed_expert_advice_refs=(),
    )


def compile_spatial_authoring_validation_contract(
    *,
    program: DesignProgram,
    site_context: SiteContext,
) -> dict[str, object]:
    """Publish current facts and the exact generic schematic gate semantics."""

    if not isinstance(program, DesignProgram):
        raise TypeError("program must be DesignProgram")
    if not isinstance(site_context, SiteContext):
        raise TypeError("site_context must be SiteContext")
    footprint_ranges = tuple(
        sorted(
            (
                {
                    "ref": item.ref,
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "unit": item.unit,
                    "scenario_ref": (
                        f"program-scenario:{item.scenario_id}"
                        if item.scenario_id is not None
                        else None
                    ),
                }
                for item in program.ranges
                if item.metric is ProgramMetricKind.FOOTPRINT
            ),
            key=lambda item: item["ref"],
        )
    )
    function_refs = tuple(
        sorted(
            item.ref
            for item in program.nodes
            if item.kind is ProgramNodeKind.FUNCTION
        )
    )
    relationships = tuple(
        sorted(
            (
                {
                    "ref": item.ref,
                    "source_node_ref": item.source_node_ref,
                    "target_node_ref": item.target_node_ref,
                    "directed": item.directed,
                    "strength": item.strength.value,
                }
                for item in program.relationships
            ),
            key=lambda item: item["ref"],
        )
    )
    return {
        "schema": "SpatialAuthoringValidationContract@1",
        "coordinate_semantics": {
            "bounds_minimum_and_maximum_are_inclusive": True,
            "footprint_cell_axes": ["x", "z"],
            "volume_projection_axes": ["x", "z"],
            "level_top_y_formula": "base_y + height - 1",
        },
        "current_facts": {
            "observed_site_envelope": site_context.observed_envelope.to_dict(),
            "footprint_ranges": list(footprint_ranges),
            "function_refs": list(function_refs),
            "relationships": list(relationships),
        },
        "required_invariants": [
            "program_scenario_ref names a current program scenario",
            "footprint_range_ref names a compatible current footprint range",
            "footprint area equals len(footprint_cells) multiplied by grid_basis.horizontal_area_per_cell",
            "footprint area is within the cited footprint range and uses the same area_unit",
            "every footprint cell x/z lies inside the observed site envelope",
            "every level spans inclusive y from base_y through base_y + height - 1 inside the site envelope",
            "every volume bound lies inside the site envelope and names only current level_ids",
            "for each volume include every inclusive [x,z] pair from bounds.minimum through bounds.maximum in footprint_cells",
            "each volume projection contains at most 4096 inclusive x/z cells",
            "each named level is vertically contained by its volume bounds",
            "every current function_ref appears in exactly one zone and no other program_node_ref is used",
            "every zone names only declared level_ids and volume_ids",
            "each connection joins zones containing its relationship source and target nodes and matches relationship directed exactly",
            "every required response ref is covered by a constraint_response or a matching connection relationship_ref",
        ],
        "output_repair_authority": False,
        "validation_authority": "deterministic_compiler",
    }


def _topology_signature(proposal: SpatialOptionProposal) -> str:
    level_by_id = {
        item.level_id: (item.base_y, item.height)
        for item in proposal.levels
    }
    volume_by_id = {
        item.volume_id: (
            item.bounds.minimum,
            item.bounds.maximum,
            tuple(sorted(level_by_id[level] for level in item.level_ids)),
        )
        for item in proposal.volumes
    }
    zone_by_id = {
        item.zone_id: (
            tuple(sorted(item.program_node_refs)),
            tuple(sorted(level_by_id[level] for level in item.level_ids)),
            tuple(
                sorted(volume_by_id[volume] for volume in item.volume_ids)
            ),
        )
        for item in proposal.zones
    }
    connections = []
    for item in proposal.connections:
        source = zone_by_id[item.source_zone_id]
        target = zone_by_id[item.target_zone_id]
        endpoints = (
            (source, target)
            if item.directed
            else tuple(sorted((source, target), key=canonical_json))
        )
        connections.append(
            {
                "endpoints": endpoints,
                "relationship_refs": sorted(item.relationship_refs),
                "directed": item.directed,
            }
        )
    identity = {
        "footprint_cells": sorted(proposal.footprint_cells),
        "levels": sorted(level_by_id.values()),
        "volumes": sorted(volume_by_id.values(), key=canonical_json),
        "zones": sorted(zone_by_id.values(), key=canonical_json),
        "connections": sorted(connections, key=canonical_json),
    }
    return canonical_digest(identity)


def _validate_option(
    proposal: SpatialOptionProposal,
    *,
    state: OperationalMarkovState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> SchematicOption:
    known_scenarios = {item.ref: item for item in program.scenarios}
    if known_scenarios:
        if proposal.program_scenario_ref not in known_scenarios:
            raise SpatialCompilationError(
                "proposal must name a current program scenario"
            )
    elif proposal.program_scenario_ref is not None:
        raise SpatialCompilationError(
            "proposal names a scenario absent from the program"
        )

    footprint_ranges = {
        item.ref: item
        for item in program.ranges
        if item.metric is ProgramMetricKind.FOOTPRINT
        and (
            item.scenario_id is None
            or proposal.program_scenario_ref
            == f"program-scenario:{item.scenario_id}"
        )
    }
    footprint_area = (
        len(proposal.footprint_cells)
        * proposal.grid_basis.horizontal_area_per_cell
    )
    if footprint_ranges:
        selected = footprint_ranges.get(proposal.footprint_range_ref)
        if selected is None:
            raise SpatialCompilationError(
                "proposal must cite a compatible footprint range"
            )
        if selected.unit != proposal.grid_basis.area_unit:
            raise SpatialCompilationError(
                "footprint range and grid basis units disagree"
            )
        if not selected.minimum <= footprint_area <= selected.maximum:
            raise SpatialCompilationError(
                "proposal footprint lies outside its cited range"
            )
    elif proposal.footprint_range_ref is not None:
        raise SpatialCompilationError(
            "proposal cites a footprint range absent from the program"
        )

    reference_contract = compile_spatial_authoring_reference_contract(
        state=state,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )
    allowed_evidence = set(reference_contract.allowed_evidence_refs)
    if not set(proposal.expert_advice_refs) <= set(
        reference_contract.allowed_expert_advice_refs
    ):
        raise SpatialCompilationError(
            "proposal cites expert advice absent from the current design state"
        )
    if not set(proposal.evidence_refs) <= allowed_evidence:
        raise SpatialCompilationError(
            "proposal cites evidence absent from the current design state"
        )
    known_response_refs = set(reference_contract.allowed_responds_to_refs)
    if not set(proposal.responds_to_refs) <= known_response_refs:
        raise SpatialCompilationError(
            "proposal responds to an unknown or stale reference"
        )
    response_refs = {
        item.constraint_ref for item in proposal.constraint_responses
    }
    if not response_refs <= known_response_refs:
        raise SpatialCompilationError(
            "constraint response names an unknown current constraint"
        )

    envelope = site_context.observed_envelope
    footprint = set(proposal.footprint_cells)
    if any(
        not (
            envelope.minimum[0] <= x <= envelope.maximum[0]
            and envelope.minimum[2] <= z <= envelope.maximum[2]
        )
        for x, z in footprint
    ):
        raise SpatialCompilationError(
            "proposal footprint exceeds the observed site envelope"
        )
    level_ids = {item.level_id for item in proposal.levels}
    volume_ids = {item.volume_id for item in proposal.volumes}
    zone_ids = {item.zone_id for item in proposal.zones}
    for level in proposal.levels:
        if (
            level.base_y < envelope.minimum[1]
            or level.top_y > envelope.maximum[1]
        ):
            raise SpatialCompilationError(
                "proposal level exceeds the observed site envelope"
            )
    for volume in proposal.volumes:
        if not envelope.contains_bounds(volume.bounds):
            raise SpatialCompilationError(
                "proposal volume exceeds the observed site envelope"
            )
        if not set(volume.level_ids) <= level_ids:
            raise SpatialCompilationError(
                "proposal volume names an unknown level"
            )
        projected_count = (
            volume.bounds.maximum[0] - volume.bounds.minimum[0] + 1
        ) * (
            volume.bounds.maximum[2] - volume.bounds.minimum[2] + 1
        )
        if projected_count > 4_096:
            raise SpatialCompilationError(
                "volume projection exceeds bounded schematic footprint"
            )
        projected = {
            (x, z)
            for x in range(
                volume.bounds.minimum[0],
                volume.bounds.maximum[0] + 1,
            )
            for z in range(
                volume.bounds.minimum[2],
                volume.bounds.maximum[2] + 1,
            )
        }
        if not projected <= footprint:
            raise SpatialCompilationError(
                "massing volume projects outside the proposed footprint"
            )
        associated_levels = tuple(
            item
            for item in proposal.levels
            if item.level_id in volume.level_ids
        )
        if any(
            item.base_y < volume.bounds.minimum[1]
            or item.top_y > volume.bounds.maximum[1]
            for item in associated_levels
        ):
            raise SpatialCompilationError(
                "massing volume does not contain its named levels"
            )

    function_refs = {
        item.ref
        for item in program.nodes
        if item.kind is ProgramNodeKind.FUNCTION
    }
    assigned_function_refs = tuple(
        ref for zone in proposal.zones for ref in zone.program_node_refs
    )
    if (
        set(assigned_function_refs) != function_refs
        or len(assigned_function_refs) != len(set(assigned_function_refs))
    ):
        raise SpatialCompilationError(
            "each current program function must enter exactly one zone"
        )
    for zone in proposal.zones:
        if (
            not set(zone.level_ids) <= level_ids
            or not set(zone.volume_ids) <= volume_ids
        ):
            raise SpatialCompilationError(
                "zone names an unknown level or volume"
            )

    relationship_by_ref = {
        item.ref: item for item in program.relationships
    }
    zone_by_id = {item.zone_id: item for item in proposal.zones}
    covered_relationships: set[str] = set()
    for connection in proposal.connections:
        if (
            connection.source_zone_id not in zone_ids
            or connection.target_zone_id not in zone_ids
        ):
            raise SpatialCompilationError(
                "connection names an unknown zone"
            )
        source_nodes = set(
            zone_by_id[connection.source_zone_id].program_node_refs
        )
        target_nodes = set(
            zone_by_id[connection.target_zone_id].program_node_refs
        )
        for relationship_ref in connection.relationship_refs:
            relationship = relationship_by_ref.get(relationship_ref)
            if relationship is None:
                raise SpatialCompilationError(
                    "connection names an unknown program relationship"
                )
            forward = (
                relationship.source_node_ref in source_nodes
                and relationship.target_node_ref in target_nodes
            )
            reverse = (
                relationship.source_node_ref in target_nodes
                and relationship.target_node_ref in source_nodes
            )
            if not forward and not (
                reverse and not relationship.directed
            ):
                raise SpatialCompilationError(
                    "connection endpoints disagree with program relationship"
                )
            if connection.directed != relationship.directed:
                raise SpatialCompilationError(
                    "connection direction disagrees with relationship"
                )
            covered_relationships.add(relationship_ref)

    required = set(reference_contract.required_response_refs)
    if not required <= (
        response_refs
        | covered_relationships
    ):
        raise SpatialCompilationError(
            "proposal leaves a required current constraint unaddressed"
        )
    return SchematicOption(
        proposal=proposal,
        footprint_area=footprint_area,
        topology_signature=_topology_signature(proposal),
    )


@dataclass(frozen=True, slots=True)
class SpatialCompilationReceipt:
    option_set_ref: str
    option_set_digest: str
    phase_gate_receipt_ref: str
    phase_gate_receipt_digest: str
    proposal_digests: tuple[str, ...]
    option_digests: tuple[str, ...]
    compiler_id: str = _COMPILER_ID
    compiler_version: str = _COMPILER_VERSION

    SCHEMA = "SpatialCompilationReceipt@1"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "option_set_ref": self.option_set_ref,
            "option_set_digest": self.option_set_digest,
            "phase_gate_receipt_ref": self.phase_gate_receipt_ref,
            "phase_gate_receipt_digest": self.phase_gate_receipt_digest,
            "proposal_digests": list(self.proposal_digests),
            "option_digests": list(self.option_digests),
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "proposal_acceptance_only": True,
            "selected_option_id": None,
            "ranked": False,
            "hard_usability_verdict": None,
            "canonical_write_authority": False,
        }


@dataclass(frozen=True, slots=True)
class SpatialCompilationResult:
    option_set: SchematicOptionSet
    receipt: SpatialCompilationReceipt


def validate_spatial_authoring_context(
    *,
    state: OperationalMarkovState,
    maturity: DesignMaturityState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
) -> None:
    """Require one current schematic-authoring context without generating it."""

    _require_bound_inputs(
        state=state,
        maturity=maturity,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )


def validate_spatial_option(
    *,
    state: OperationalMarkovState,
    maturity: DesignMaturityState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
    proposal: SpatialOptionProposal,
) -> SchematicOption:
    """Validate one authored option without selecting or persisting it."""

    validate_spatial_authoring_context(
        state=state,
        maturity=maturity,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )
    if not isinstance(proposal, SpatialOptionProposal):
        raise TypeError("proposal must be SpatialOptionProposal")
    return _validate_option(
        proposal,
        state=state,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )


def compile_spatial_options(
    *,
    state: OperationalMarkovState,
    maturity: DesignMaturityState,
    phase_gate: PhaseGateReceipt,
    program: DesignProgram,
    site_context: SiteContext,
    build_policy: BuildPolicy,
    proposals: tuple[SpatialOptionProposal, ...],
) -> SpatialCompilationResult:
    """Validate alternative proposals without generating or selecting one."""

    validate_spatial_authoring_context(
        state=state,
        maturity=maturity,
        phase_gate=phase_gate,
        program=program,
        site_context=site_context,
        build_policy=build_policy,
    )
    if (
        not isinstance(proposals, tuple)
        or len(proposals) < 2
        or any(
            not isinstance(item, SpatialOptionProposal)
            for item in proposals
        )
    ):
        raise SpatialCompilationError(
            "compile_spatial_options requires at least two proposals"
        )
    compiled = tuple(
        _validate_option(
            proposal,
            state=state,
            phase_gate=phase_gate,
            program=program,
            site_context=site_context,
            build_policy=build_policy,
        )
        for proposal in proposals
    )
    option_set = SchematicOptionSet(
        project_id=program.project_id,
        run_id=program.run_id,
        base=program.base,
        branch=state.branch,
        operational_state_digest=state.state_digest,
        input_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
        output_phase=DesignPhase.SCHEMATIC_DESIGN,
        program_digest=program.program_digest,
        site_context_digest=site_context.context_digest,
        build_policy_digest=build_policy.policy_digest,
        phase_gate_receipt_ref=phase_gate.ref,
        phase_gate_receipt_digest=canonical_digest(phase_gate.to_dict()),
        compiler_id=_COMPILER_ID,
        compiler_version=_COMPILER_VERSION,
        options=tuple(sorted(compiled, key=lambda item: item.option_id)),
    )
    receipt = SpatialCompilationReceipt(
        option_set_ref=option_set.ref,
        option_set_digest=option_set.option_set_digest,
        phase_gate_receipt_ref=phase_gate.ref,
        phase_gate_receipt_digest=canonical_digest(phase_gate.to_dict()),
        proposal_digests=tuple(
            item.proposal.proposal_digest
            for item in option_set.options
        ),
        option_digests=tuple(
            item.option_digest for item in option_set.options
        ),
    )
    return SpatialCompilationResult(
        option_set=option_set,
        receipt=receipt,
    )
