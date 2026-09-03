from __future__ import annotations

from dataclasses import replace

import pytest

from archflow.capabilities.visual_inventory import (
    PixelRegion,
    ROISelection,
    SourceDerivationKind,
    SourceImageEvidence,
    VisualSourceDisposition,
    VisualSourceDispositionKind,
    compile_visual_evidence_inventory,
)
from archflow.control.baseline import BASELINE_LEVEL_ROLES, StageBaselineLevel
from archflow.control.component_functions import (
    DEFAULT_COMPONENT_FUNCTION_POLICY,
    ComponentFunctionContract,
    ComponentFunctionId,
    FunctionApplicability,
    FunctionApplicabilityDecision,
    FunctionClaimStatus,
    FunctionEndpointBinding,
    FunctionObligationClaim,
    compile_component_function_ledger,
)
from archflow.control.function_relations import (
    FunctionRelationEndpoint,
    FunctionRelationEndpointBinding,
    FunctionRelationEvidenceEnvelope,
)
from archflow.control.relation_checks import (
    RELATION_VERIFICATION_CHECKERS,
    relation_subject_inventory_ref,
)
from archflow.control.semantic_capabilities import (
    current_semantic_capability_policy,
)
from archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectRoleObligation,
)
from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationRuleEnvelope,
    RelationEpistemicStatus,
    RelationProposalSpec,
    RelationRuleProposalSpec,
    compile_relation_authoring,
)
from archflow.relations.contracts import (
    ArchitecturalNode,
    ArchitecturalNodeKind,
    ArchitecturalRelationKind,
    RelationParticipant,
    RelationProjection,
)
from archflow.runtime.stage_control_chain import (
    FinalizedStageControlChain,
    PreparedStageControlChain,
    StageControlChainError,
    finalize_stage_control_chain,
    prepare_stage_control_chain,
)
from archflow.runtime.stage_subject_inventory import (
    compile_stage_subject_inventory,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archflow.validation.relation_verification import (
    RelationQuestionVerificationProfile,
    RelationVerificationBinding,
    compile_relation_question_verification,
)
from tests.test_stage_subject_inventory import (
    _record_ref,
    _stair_sources,
    _text_only_visual_inventory,
)


SOURCE_REF = "evidence:project-stage-control"
AUTHORITY_REF = "authority:project-architect"
SCOPE_DIGEST = "d" * 64


def _raw_inputs(
    *,
    stage_id: str = "stage-2",
    baseline_level: StageBaselineLevel = StageBaselineLevel.PRE_GEOMETRY,
    access_semantic_kind: str = "exterior-stair-envelope",
):  # type: ignore[no-untyped-def]
    proposal, index, branch = _stair_sources()
    if access_semantic_kind != "exterior-stair-envelope":
        access_component = next(
            item
            for item in proposal.components
            if item.component_id == "exterior-stair-east"
        )
        replacement = replace(
            access_component,
            semantic_kind=access_semantic_kind,
        )
        proposal = replace(
            proposal,
            components=tuple(
                sorted(
                    (
                        replacement
                        if item.component_id == replacement.component_id
                        else item
                        for item in proposal.components
                    ),
                    key=lambda item: item.component_id,
                )
            ),
        )
        index = replace(
            index,
            component_proposal_digest=proposal.proposal_digest,
            entries=tuple(
                sorted(
                    (
                        replace(item, component=replacement)
                        if item.component_id == replacement.component_id
                        else item
                        for item in index.entries
                    ),
                    key=lambda item: item.component_id,
                )
            ),
        )
    visual = _text_only_visual_inventory()
    policy = current_semantic_capability_policy()
    values = {
        "inventory_id": "runtime-stage-inventory",
        "function_ledger_id": "runtime-function-ledger",
        "relation_requirements_id": "runtime-function-relations",
        "relation_context_id": "runtime-relation-context",
        "branch": branch,
        "stage_id": stage_id,
        "state_digest": index.design_state_digest,
        "scope_digest": SCOPE_DIGEST,
        "stage_subject_ref": f"design-state:{stage_id}",
        "stage_subject_digest": index.design_state_digest,
        "baseline_level": baseline_level,
        "component_proposal": proposal,
        "component_proposal_ref": _record_ref(
            branch, "runtime-proposal", "7" * 64
        ),
        "component_index": index,
        "component_index_ref": _record_ref(
            branch, "runtime-component-index", "8" * 64
        ),
        "visual_inventory": visual,
        "visual_inventory_ref": _record_ref(
            branch, "runtime-visual-inventory", "9" * 64
        ),
        "semantic_policy": policy,
        "semantic_policy_ref": _record_ref(
            branch, "runtime-semantic-policy", "a" * 64
        ),
        "role_obligations": {
            component.component_id: tuple(
                StageSubjectRoleObligation(
                    role=role,
                    disposition=StageSubjectDisposition.REQUIRED,
                    target_refs=(component.identity_ref,),
                    evidence_refs=(SOURCE_REF,),
                    authority_refs=(AUTHORITY_REF,),
                )
                for role in sorted(BASELINE_LEVEL_ROLES[baseline_level])
            )
            for component in proposal.components
        },
    }
    seed_inventory = compile_stage_subject_inventory(
        inventory_id=values["inventory_id"],
        branch=branch,
        stage_id=values["stage_id"],
        stage_subject_ref=values["stage_subject_ref"],
        stage_subject_digest=values["stage_subject_digest"],
        baseline_level=values["baseline_level"],
        component_proposal=proposal,
        component_proposal_ref=values["component_proposal_ref"],
        component_index=index,
        component_index_ref=values["component_index_ref"],
        visual_inventory=visual,
        visual_inventory_ref=values["visual_inventory_ref"],
        semantic_policy=policy,
        semantic_policy_ref=values["semantic_policy_ref"],
        role_obligations=values["role_obligations"],
    )
    entries = {item.component_id: item for item in seed_inventory.entries}
    function_rows = (
        (
            entries["building"],
            entries["exterior-stair-east"],
            ComponentFunctionId.HOST_OTHERS,
        ),
        (
            entries["exterior-stair-east"],
            entries["building"],
            ComponentFunctionId.BE_HOSTED,
        ),
    )
    contracts = []
    for component, peer, function_id in function_rows:
        spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id)
        claim = FunctionObligationClaim(
            obligation_ref=spec.obligation_ref,
            endpoint_bindings=tuple(
                FunctionEndpointBinding(
                    role=role.role,
                    endpoint_refs=(
                        (component.identity_ref,)
                        if role.component_slot
                        else (peer.identity_ref,)
                    ),
                )
                for role in spec.endpoint_roles
            ),
            maturity=spec.required_maturity,
            status=FunctionClaimStatus.PASS,
            evidence_refs=(SOURCE_REF,),
            authority_refs=(AUTHORITY_REF,),
            contradiction_refs=(),
        )
        contracts.append(
            ComponentFunctionContract(
                contract_id=f"{component.component_id}-function-contract",
                branch=branch,
                stage_id=values["stage_id"],
                subject_inventory_digest=seed_inventory.inventory_digest,
                component_ref=component.identity_ref,
                component_digest=component.component_digest,
                applicability_decisions=tuple(
                    FunctionApplicabilityDecision(
                        function_id=item,
                        applicability=(
                            FunctionApplicability.REQUIRED
                            if item is function_id
                            else FunctionApplicability.NOT_APPLICABLE
                        ),
                        evidence_refs=(SOURCE_REF,),
                        authority_refs=(AUTHORITY_REF,),
                    )
                    for item in ComponentFunctionId
                ),
                claims=(claim,),
            )
        )
    ledger = compile_component_function_ledger(
        ledger_id=values["function_ledger_id"],
        inventory=seed_inventory,
        contracts=tuple(contracts),
    )
    envelopes = []
    for component, peer, function_id in function_rows:
        spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id)
        envelopes.append(
            FunctionRelationEvidenceEnvelope(
                envelope_id=f"{component.component_id}-host-relation",
                branch=branch,
                stage_id=values["stage_id"],
                subject_inventory_digest=seed_inventory.inventory_digest,
                function_ledger_ref=ledger.ledger_ref,
                function_ledger_digest=ledger.ledger_digest,
                component_ref=component.identity_ref,
                component_digest=component.component_digest,
                functional_obligation_ref=spec.obligation_ref,
                projection=RelationProjection.HOST,
                relation_kind=ArchitecturalRelationKind.HOST,
                scenario_ref="scenario:runtime-hosting",
                endpoint_bindings=tuple(
                    FunctionRelationEndpointBinding(
                        function_role=role.role,
                        relation_role=(
                            "host" if role.role == "host_component" else "hosted"
                        ),
                        endpoints=(
                            FunctionRelationEndpoint(
                                component_ref=(
                                    component.identity_ref
                                    if role.component_slot
                                    else peer.identity_ref
                                ),
                                component_digest=(
                                    component.component_digest
                                    if role.component_slot
                                    else peer.component_digest
                                ),
                            ),
                        ),
                    )
                    for role in spec.endpoint_roles
                ),
                counted_function_role=next(
                    role.role for role in spec.endpoint_roles if not role.component_slot
                ),
                basis_ids=(
                    f"{component.component_id}-policy",
                    f"{component.component_id}-topology",
                ),
                evidence_refs=(SOURCE_REF,),
                authority_refs=(AUTHORITY_REF,),
                prompt="Identify the exact project-authored host relation.",
            )
        )
    from archflow.control.function_relations import (
        compile_function_relation_requirements,
    )

    requirements = compile_function_relation_requirements(
        set_id=values["relation_requirements_id"],
        ledger=ledger,
        inventory=seed_inventory,
        envelopes=tuple(envelopes),
    )
    nodes = tuple(
        ArchitecturalNode(
            node_ref=entry.identity_ref,
            node_kind=ArchitecturalNodeKind.COMPONENT,
            semantic_kind=entry.semantic_kind,
            stage_id=values["stage_id"],
            source_refs=(f"stage-subject-entry:{entry.entry_digest}",),
        )
        for entry in seed_inventory.entries
    )
    bases = tuple(
        RelationBasisBinding(
            basis_id=basis_id,
            basis_kind=(
                RelationBasisKind.HUMAN
                if basis_id.endswith("-policy")
                else RelationBasisKind.RAG
            ),
            basis_use=(
                RelationBasisUse.POLICY
                if basis_id.endswith("-policy")
                else RelationBasisUse.TOPOLOGY
            ),
            question_refs=(requirement.question.ref,),
            allowed_relation_kinds=(requirement.rule.relation_kind,),
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=(SOURCE_REF,),
            authority_refs=(AUTHORITY_REF,),
            summary="Project-provided evidence-bound relation basis.",
        )
        for requirement in requirements.requirements
        for basis_id in requirement.question.basis_ids
    )
    return {
        **values,
        "function_contracts": tuple(contracts),
        "function_relation_envelopes": tuple(envelopes),
        "relation_nodes": nodes,
        "relation_questions": (),
        "relation_bases": bases,
    }


def _prepared():  # type: ignore[no-untyped-def]
    return prepare_stage_control_chain(**_raw_inputs())


def _proposal(prepared):  # type: ignore[no-untyped-def]
    requirements = prepared.relation_requirements.requirements
    basis_by_id = {
        item.basis_id: item for item in prepared.relation_context.bases
    }
    question_refs = tuple(sorted(item.question.ref for item in requirements))
    topology_basis_ids = tuple(
        sorted(
            basis_id
            for requirement in requirements
            for basis_id in requirement.question.basis_ids
            if basis_by_id[basis_id].basis_use is RelationBasisUse.TOPOLOGY
        )
    )
    relation_id = "building-hosts-exterior-stair"
    relation = RelationProposalSpec(
        relation_id=relation_id,
        question_refs=question_refs,
        kind=ArchitecturalRelationKind.HOST,
        participants=(
            RelationParticipant("host", "design-component:building"),
            RelationParticipant(
                "hosted", "design-component:exterior-stair-east"
            ),
        ),
        scenario_ref="scenario:runtime-hosting",
        basis_ids=topology_basis_ids,
    )
    rules = tuple(
        RelationRuleProposalSpec(
            rule_id=requirement.rule.rule_id,
            question_refs=(requirement.question.ref,),
            node_kind=requirement.rule.node_kind,
            semantic_kind=requirement.rule.semantic_kind,
            relation_kind=requirement.rule.relation_kind,
            subject_role=requirement.rule.subject_role,
            counted_role=requirement.rule.counted_role,
            minimum_count=requirement.rule.minimum_count,
            maximum_count=requirement.rule.maximum_count,
            scenario_ref=requirement.rule.scenario_ref,
            basis_ids=tuple(
                basis_id
                for basis_id in requirement.question.basis_ids
                if basis_by_id[basis_id].basis_use is RelationBasisUse.POLICY
            ),
        )
        for requirement in requirements
    )
    return RelationAuthoringProposal(
        context_digest=prepared.relation_context.context_digest,
        answers=tuple(
            RelationDerivationAnswer(
                question_ref=requirement.question.ref,
                status=RelationAnswerStatus.PROPOSED,
                relation_ids=(relation_id,),
                rule_ids=(requirement.rule.rule_id,),
                rationale="Bind the shared host relation without inventing endpoints.",
            )
            for requirement in requirements
        ),
        relations=(relation,),
        rules=rules,
    )


def _verification_receipts(prepared, proposal):  # type: ignore[no-untyped-def]
    compilation = compile_relation_authoring(
        prepared.relation_context,
        proposal,
    )
    assert compilation.graph is not None
    graph = compilation.graph
    inventory_ref = relation_subject_inventory_ref(prepared.inventory)
    compiled = []
    for question in prepared.relation_context.questions:
        relations = tuple(
            relation
            for relation in graph.relations
            if question.ref in relation.source_refs
        )
        bindings = tuple(
            RelationVerificationBinding(
                relation_ref=relation.ref,
                checker_requirement_refs=(
                    f"independent-requirement:{relation.relation_id}",
                ),
                checker_subject_refs=tuple(
                    sorted(item.node_ref for item in relation.participants)
                ),
            )
            for relation in relations
        )
        base_checker_id = f"independent-{question.projection.value}-checker"
        profile = RelationQuestionVerificationProfile(
            profile_id=f"verify-{question.question_id}",
            question=question,
            proposal_graph=graph,
            subject_inventory_ref=inventory_ref,
            output_checker_id=RELATION_VERIFICATION_CHECKERS[
                question.projection
            ],
            base_checker_id=base_checker_id,
            bindings=bindings,
        )
        denominator = profile.checker_requirement_refs
        base_receipt = CheckReceiptEnvelope(
            check_id=f"independent-{question.question_id}",
            checker_id=base_checker_id,
            checker_version="1.0.0",
            branch=graph.branch,
            scope_digest=graph.scope_digest,
            subject_refs=denominator,
            subject_digest=graph.stage_subject_digest,
            status=CheckStatus.PASS,
            source_refs=(SOURCE_REF,),
            authority_refs=(AUTHORITY_REF,),
            coverage_denominator=denominator,
            covered_refs=denominator,
        )
        compiled.append(
            compile_relation_question_verification(profile, base_receipt)
        )
    return tuple(compiled)


def test_forward_runtime_prepares_finalizes_and_round_trips() -> None:
    prepared = _prepared()
    assert PreparedStageControlChain.from_dict(prepared.to_dict()) == prepared
    assert set(prepared.relation_context.questions) == set(
        prepared.relation_requirements.topology_questions
    )

    proposal = _proposal(prepared)
    finalized = finalize_stage_control_chain(
        prepared=prepared,
        relation_proposal=proposal,
        function_ledger_ref=_record_ref(
            prepared.inventory.branch, "runtime-function-ledger", "b" * 64
        ),
        relation_requirements_ref=_record_ref(
            prepared.inventory.branch, "runtime-function-relations", "c" * 64
        ),
        verification_receipts=_verification_receipts(prepared, proposal),
    )

    assert FinalizedStageControlChain.from_dict(finalized.to_dict()) == finalized
    assert finalized.baseline_sources.component_functions == (
        finalized.function_source,
    )
    assert finalized.baseline_sources.relation_topology == (
        finalized.topology_source,
    )
    assert all(
        value is False
        for key, value in finalized.to_dict().items()
        if key.endswith("authority")
    )


def test_prepare_rejects_cross_branch_stage_inventory_and_omission() -> None:
    base = _raw_inputs()
    foreign_branch = BranchRef(
        run=RunRef(
            project_id=base["branch"].run.project_id,
            run_id="foreign-run",
            base=ProjectVersionRef(
                base["branch"].run.project_id,
                0,
                "f" * 64,
            ),
        ),
        branch_id="foreign",
        epoch=1,
    )
    mutations = (
        {
            "function_contracts": (
                replace(base["function_contracts"][0], branch=foreign_branch),
                *base["function_contracts"][1:],
            )
        },
        {
            "function_relation_envelopes": (
                replace(
                    base["function_relation_envelopes"][0],
                    stage_id="stage-3",
                ),
                *base["function_relation_envelopes"][1:],
            )
        },
        {
            "function_contracts": (
                replace(
                    base["function_contracts"][0],
                    subject_inventory_digest="e" * 64,
                ),
                *base["function_contracts"][1:],
            )
        },
        {"function_relation_envelopes": base["function_relation_envelopes"][:-1]},
    )
    for mutation in mutations:
        with pytest.raises(ValueError):
            prepare_stage_control_chain(**{**base, **mutation})


def test_prepare_rejects_missing_or_stale_project_relation_basis() -> None:
    base = _raw_inputs()
    with pytest.raises(StageControlChainError, match="project-provided"):
        prepare_stage_control_chain(
            **{**base, "relation_bases": base["relation_bases"][:-1]}
        )

    stale = replace(
        base["relation_bases"][0],
        evidence_refs=("evidence:unrelated",),
    )
    with pytest.raises(StageControlChainError, match="stale or unbound"):
        prepare_stage_control_chain(
            **{
                **base,
                "relation_bases": (stale, *base["relation_bases"][1:]),
            }
        )


def test_prepare_injects_function_questions_without_replacing_project_questions() -> None:
    base = _raw_inputs()
    seed = prepare_stage_control_chain(**base)
    template = seed.relation_requirements.requirements[0].question
    project_question = replace(
        template,
        question_id="project-host-continuity",
        rule_envelopes=(
            RelationRuleEnvelope(
                relation_kind=template.allowed_relation_kinds[0],
                subject_role="hosted",
                counted_role="host",
                minimum_count=1,
                maximum_count=1,
            ),
        ),
        basis_ids=("project-policy", "project-topology"),
        prompt="Check the separate project host-continuity question.",
    )
    project_bases = tuple(
        RelationBasisBinding(
            basis_id=f"project-{use.value}",
            basis_kind=(
                RelationBasisKind.HUMAN
                if use is RelationBasisUse.POLICY
                else RelationBasisKind.RAG
            ),
            basis_use=use,
            question_refs=(project_question.ref,),
            allowed_relation_kinds=project_question.allowed_relation_kinds,
            epistemic_status=RelationEpistemicStatus.DERIVED,
            evidence_refs=(SOURCE_REF,),
            authority_refs=(AUTHORITY_REF,),
            summary="Project-authored context basis.",
        )
        for use in (RelationBasisUse.POLICY, RelationBasisUse.TOPOLOGY)
    )

    prepared = prepare_stage_control_chain(
        **{
            **base,
            "relation_questions": (project_question,),
            "relation_bases": (*base["relation_bases"], *project_bases),
        }
    )

    assert {item.ref for item in prepared.relation_context.questions} == {
        project_question.ref,
        *(item.question.ref for item in prepared.relation_requirements.requirements),
    }


def test_prepare_rejects_unknown_visual_inventory() -> None:
    base = _raw_inputs()
    blocked = compile_visual_evidence_inventory(
        source_disposition=VisualSourceDisposition(
            kind=VisualSourceDispositionKind.VISUAL_SOURCES,
            source_refs=("evidence:facade-photo",),
            authority_refs=(AUTHORITY_REF,),
        ),
        source_images=(
            SourceImageEvidence(
                image_id="facade",
                exact_sha256="1" * 64,
                width_px=1000,
                height_px=800,
                derivation_kind=SourceDerivationKind.ORIGINAL,
                derivation_refs=(),
            ),
        ),
        rois=(
            PixelRegion(
                roi_id="unclassified-region",
                image_id="facade",
                source_width_px=1000,
                source_height_px=800,
                x_px=100,
                y_px=100,
                width_px=200,
                height_px=200,
                selection=ROISelection.SELECTED,
            ),
        ),
    )
    with pytest.raises(ValueError, match="visual evidence inventory is incomplete"):
        prepare_stage_control_chain(
            **{**base, "visual_inventory": blocked}
        )
