"""Deterministic Pantheon project fixtures used by runners and tests.

These helpers build the neutral authoring context that predates the measured
Pantheon profile.  They intentionally use only public ``archflow`` contracts;
production code must never reach into ``tests`` for executable fixtures.

The JSON, operation, rebase, and validation helpers are mechanically useful
outside this profile, but this is a private first-boundary extraction.  Other
projects must not import this Pantheon package; a later framework extraction
must first give those mechanics typed public contracts and independent tests.
"""

from __future__ import annotations

from dataclasses import replace

from archive.archflow.adapters.site_observation import (
    SiteObservationAuthorization,
    authorize_site_observation,
)
from archflow.capabilities.geometry_proposal import (
    GeometryProposalProviderIdentity,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.runtime.brief_compiler import (
    BriefObservation,
    compile_design_brief,
)
from archive.archflow.runtime.production_runtime import ProductionAuthoringContext
from archive.archflow.runtime.program_compiler import (
    ProgramAssumptionProposal,
    ProgramNodeProposal,
    ProgramProposalBundle,
    ProgramRangeProposal,
    ProgramRelationshipProposal,
    compile_design_program,
)
from archive.archflow.runtime.resource_compiler import (
    BuildPolicyProposal,
    ConstructabilityConstraintProposal,
    compile_build_policy,
)
from archive.archflow.runtime.site_compiler import compile_site_context
from archive.archflow.state.design_brief import BriefClaimKind, BriefSlot
from archive.archflow.state.build_policy import BuildStagingMode, ConstructabilityTopic, PolicyConstraintStrength, ResourcePolicyMode
from archflow.state.stage_workflow import DesignPhase
from archive.archflow.state.design_maturity import DeliverableRole, DesignMaturityState, PhaseDeliverable, PhaseGateRequest, evaluate_forward_phase_gate
from archflow.state.operational_state import FactEpistemicStatus, OperationalMarkovState
from archive.archflow.state.design_program import ProgramMetricKind, ProgramNodeKind, ProgramRelationshipKind, ProgramRelationshipStrength
from archflow.state.spatial import SiteBounds
from archflow.state.geometry_program import (
    GeometryOperation,
    GeometryOperationKind,
    GeometryParameter,
    GeometryParameterKind,
    LengthUnit,
)
from archflow.contracts.canonical import canonical_json


IDENTITY = GeometryProposalProviderIdentity(
    provider_id="scripted-radial-author",
    model_id="scripted-radial-model",
    provider_version="1",
    provider_fingerprint="5" * 64,
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
        responds_to_binding_ids=((binding_id,) if responds_to_binding else ()),
        responds_to_object_ids=(inputs if responds_to_inputs else ()),
    )


def _request_ref(project_id: str) -> str:
    return f"project://{project_id}/input/raw-request.json"


def _base(project_id: str, digest_char: str) -> ProjectVersionRef:
    return ProjectVersionRef(
        project_id=project_id,
        version=0,
        state_sha256=digest_char * 64,
    )


def _brief(project_id: str, run_id: str, digest_char: str):  # type: ignore[no-untyped-def]
    request_ref = _request_ref(project_id)
    observations = tuple(
        BriefObservation(
            observation_id=observation_id,
            slot=slot,
            kind=BriefClaimKind.USER_FACT,
            key=key,
            value=value,
            epistemic_status=FactEpistemicStatus.DECLARED,
            authority_id="authority.user",
            source_refs=(request_ref,),
            resolves_slot=True,
        )
        for observation_id, slot, key, value in (
            ("use", BriefSlot.USE, "use", f"{project_id} use"),
            (
                "size",
                BriefSlot.SIZE,
                "size",
                f"{project_id} scale evidence",
            ),
            (
                "occupancy",
                BriefSlot.OCCUPANCY,
                "occupancy",
                f"{project_id} occupancy evidence",
            ),
            (
                "program",
                BriefSlot.SPACE_PROGRAM,
                "program",
                f"{project_id} function evidence",
            ),
        )
    )
    return compile_design_brief(
        project_id=project_id,
        run_id=run_id,
        base=_base(project_id, digest_char),
        raw_request_ref=request_ref,
        observations=observations,
    ).brief


def _program(brief, *, function_count: int):  # type: ignore[no-untyped-def]
    source_ref = brief.raw_request_ref
    assumption_id = "evidence-bound-program"
    nodes = tuple(
        ProgramNodeProposal(
            node_id=f"function-{index}",
            kind=ProgramNodeKind.FUNCTION,
            label=f"{brief.project_id} function {index}",
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for index in range(1, function_count + 1)
    )
    range_specs = (
        (ProgramMetricKind.CAPACITY, 4.0, 40.0, "people"),
        (ProgramMetricKind.NET_AREA, 8.0, 30.0, "square_cells"),
        (ProgramMetricKind.GROSS_ALLOWANCE, 0.1, 0.4, "ratio"),
        (ProgramMetricKind.FOOTPRINT, 8.0, 30.0, "square_cells"),
        (
            ProgramMetricKind.TOTAL_FLOOR_AREA,
            8.0,
            60.0,
            "square_cells",
        ),
    )
    ranges = tuple(
        ProgramRangeProposal(
            range_id=f"range-{metric.value}",
            metric=metric,
            applies_to_node_id=nodes[0].node_id,
            minimum=minimum,
            maximum=maximum,
            unit=unit,
            scenario_id=None,
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for metric, minimum, maximum, unit in range_specs
    )
    relationships = tuple(
        ProgramRelationshipProposal(
            relationship_id=f"required-link-{index}",
            kind=ProgramRelationshipKind.ADJACENCY,
            source_node_id=nodes[index].node_id,
            target_node_id=nodes[index + 1].node_id,
            strength=ProgramRelationshipStrength.REQUIRED,
            directed=False,
            epistemic_status=FactEpistemicStatus.HYPOTHESIS,
            source_refs=(source_ref,),
            assumption_ids=(assumption_id,),
        )
        for index in range(function_count - 1)
    )
    return compile_design_program(
        brief=brief,
        proposals=ProgramProposalBundle(
            assumptions=(
                ProgramAssumptionProposal(
                    assumption_id=assumption_id,
                    statement=(
                        "The test program remains an evidence-bound "
                        "project hypothesis."
                    ),
                    source_refs=(source_ref,),
                ),
            ),
            nodes=nodes,
            ranges=ranges,
            relationships=relationships,
        ),
    ).program


def _site_observation_payload(brief) -> dict[str, object]:  # type: ignore[no-untyped-def]
    source_ref = (
        f"project://{brief.project_id}/runs/{brief.run_id}/records/site-scan.json"
    )
    world_id = f"fixture-{brief.project_id}-world"
    return {
        "schema": "SiteToolObservation@1",
        "observation_id": f"{brief.project_id}-observation",
        "project_id": brief.project_id,
        "run_id": brief.run_id,
        "base_state_sha256": brief.base.require_digest(),
        "world_id": world_id,
        "dimension_id": "minecraft:overworld",
        "observed_envelope": {
            "minimum": [0, 60, 0],
            "maximum": [31, 90, 31],
        },
        "anchor": [16, 65, 16],
        "approaches": [
            {
                "approach_id": "observed-approach",
                "status": "observed",
                "cells": [[0, 65, 16]],
                "source_refs": [source_ref],
            }
        ],
        "ground_model": {
            "kind": "superflat",
            "samples": [
                {"coordinate": [0, 64, 0], "source_ref": source_ref},
                {"coordinate": [31, 64, 0], "source_ref": source_ref},
                {"coordinate": [0, 64, 31], "source_ref": source_ref},
                {"coordinate": [31, 64, 31], "source_ref": source_ref},
            ],
            "source_refs": [source_ref],
        },
        "protected_cells": [],
        "protection_source_refs": [],
        "unknowns": [],
        "source_refs": [source_ref],
        "read_only": True,
        "world_write_authority": False,
    }


def _site(brief):  # type: ignore[no-untyped-def]
    authorization = SiteObservationAuthorization(
        project_id=brief.project_id,
        run_id=brief.run_id,
        base=brief.base,
        world_id=f"fixture-{brief.project_id}-world",
        dimension_id="minecraft:overworld",
        authorized_envelope=SiteBounds(
            minimum=(0, 60, 0),
            maximum=(31, 90, 31),
        ),
        anchor=(16, 65, 16),
        authority_id="authority.user",
        authorization_ref=(
            f"project://{brief.project_id}/input/site-authorization.json"
        ),
    )
    observation = authorize_site_observation(
        _site_observation_payload(brief),
        authorization=authorization,
    )
    return compile_site_context(brief=brief, observation=observation).context


def _inputs(
    project_id: str = "spatial-alpha",
    *,
    digest_char: str = "a",
    function_count: int = 3,
    hard_constraint: bool = False,
):
    run_id = "spatial-run"
    brief = _brief(project_id, run_id, digest_char)
    program = _program(brief, function_count=function_count)
    site = _site(brief)
    policy = compile_build_policy(
        brief=brief,
        program=program,
        site_context=site,
        proposal=BuildPolicyProposal(
            resource_mode=ResourcePolicyMode.CREATIVE,
            staging_mode=BuildStagingMode.SINGLE_PASS,
            disposable_sandbox=True,
            unbounded_resources=True,
            authority_id="authority.user",
            source_refs=(brief.raw_request_ref,),
            constraints=(
                (
                    ConstructabilityConstraintProposal(
                        constraint_id="support-boundary",
                        topic=ConstructabilityTopic.SUPPORT,
                        strength=PolicyConstraintStrength.HARD,
                        statement=(
                            "The schematic option must identify the current "
                            "support risk without resolving structure."
                        ),
                        subject_refs=("program-node:function-1",),
                        authority_id="authority.user",
                        source_refs=(brief.raw_request_ref,),
                    ),
                )
                if hard_constraint
                else ()
            ),
        ),
    ).policy
    branch = BranchRef(
        run=RunRef(
            project_id=project_id,
            run_id=run_id,
            base=brief.base,
        ),
        branch_id="schematic-main",
        epoch=0,
    )
    state = OperationalMarkovState(
        branch=branch,
        compiler_version="test.spatial-state",
        phase=DesignPhase.SITE_RESOURCE_COORDINATION.value,
        evidence_refs=tuple(
            sorted(
                {
                    *program.evidence_refs,
                    *site.evidence_refs,
                    *policy.evidence_refs,
                }
            )
        ),
    )
    deliverables = (
        PhaseDeliverable(
            deliverable_id="site-context",
            role=DeliverableRole.SITE_CONTEXT,
            produced_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            branch=branch,
            base_state_digest=state.state_digest,
            artifact_ref=f"site-context:{site.context_digest}",
            evidence_refs=site.evidence_refs,
        ),
        PhaseDeliverable(
            deliverable_id="build-policy",
            role=DeliverableRole.BUILD_POLICY,
            produced_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            branch=branch,
            base_state_digest=state.state_digest,
            artifact_ref=f"build-policy:{policy.policy_digest}",
            evidence_refs=policy.evidence_refs,
        ),
    )
    maturity = DesignMaturityState.from_operational_state(
        state,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id="enter-schematic",
            branch=branch,
            base_state_digest=state.state_digest,
            from_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            to_phase=DesignPhase.SCHEMATIC_DESIGN,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    return brief, program, site, policy, state, maturity, gate


def _rebase_program(program, run):  # type: ignore[no-untyped-def]
    digest = run.base.require_digest()
    return replace(
        program,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        assumptions=tuple(
            replace(item, base_state_sha256=digest) for item in program.assumptions
        ),
        nodes=tuple(
            replace(item, base_state_sha256=digest) for item in program.nodes
        ),
        ranges=tuple(
            replace(item, base_state_sha256=digest) for item in program.ranges
        ),
        relationships=tuple(
            replace(item, base_state_sha256=digest)
            for item in program.relationships
        ),
        scenarios=tuple(
            replace(item, base_state_sha256=digest) for item in program.scenarios
        ),
    )


def _rebase_policy(policy, run, program, site):  # type: ignore[no-untyped-def]
    digest = run.base.require_digest()

    def provenance(value):  # type: ignore[no-untyped-def]
        return replace(value, base_state_sha256=digest)

    def provenanced(values):  # type: ignore[no-untyped-def]
        return tuple(
            replace(item, provenance=provenance(item.provenance))
            for item in values
        )

    return replace(
        policy,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
        program_digest=program.program_digest,
        site_context_digest=site.context_digest,
        policy_provenance=provenance(policy.policy_provenance),
        assumptions=tuple(
            replace(item, base_state_sha256=digest) for item in policy.assumptions
        ),
        availability=provenanced(policy.availability),
        demands=provenanced(policy.demands),
        protected_rules=provenanced(policy.protected_rules),
        budget_limits=provenanced(policy.budget_limits),
        staging_assumptions=provenanced(policy.staging_assumptions),
        constraints=provenanced(policy.constraints),
    )


def _rebase_context(
    context: ProductionAuthoringContext,
    run,
):  # type: ignore[no-untyped-def]
    branch = replace(context.state.branch, run=run)
    state = replace(context.state, branch=branch)
    deliverables = tuple(
        replace(
            item,
            branch=branch,
            base_state_digest=state.state_digest,
        )
        for item in context.maturity.deliverables
    )
    maturity = replace(
        context.maturity,
        branch=branch,
        operational_state_digest=state.state_digest,
        deliverables=deliverables,
    )
    gate = evaluate_forward_phase_gate(
        maturity,
        PhaseGateRequest(
            request_id=context.phase_gate.request_id,
            branch=branch,
            base_state_digest=state.state_digest,
            from_phase=context.phase_gate.from_phase,
            to_phase=context.phase_gate.to_phase,
            deliverable_refs=tuple(item.ref for item in deliverables),
        ),
    )
    program = _rebase_program(context.program, run)
    site = replace(
        context.site_context,
        project_id=run.project_id,
        run_id=run.run_id,
        base=run.base,
    )
    policy = _rebase_policy(context.build_policy, run, program, site)
    return replace(
        context,
        state=state,
        maturity=maturity,
        phase_gate=gate,
        program=program,
        site_context=site,
        build_policy=policy,
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


__all__ = [
    "IDENTITY",
    "canonical_json",
    "_inputs",
    "_operation",
    "_parameter",
    "_rebase_context",
    "_validation_payload",
]
