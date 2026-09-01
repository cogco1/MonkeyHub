from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from archflow.capabilities.visual_inventory import (
    VisualSourceDisposition,
    VisualSourceDispositionKind,
    compile_visual_evidence_inventory,
)
from archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertRegistry,
    ExpertSpec,
)
from archflow.capabilities.phase_gates import PhaseExpertMetadata
from archflow.control.baseline import (
    BASELINE_LEVEL_ROLES,
    ComponentLineageBaselineSource,
    RelationRealizationBaselineSource,
    RelationTopologyBaselineSource,
    SpatialLayoutBaselineSource,
    StageBaselineLevel,
    StageBaselineRole,
    StageBaselineSourceSet,
    baseline_level_for_design_phase,
    derive_stage_requirement_profile,
)
from archflow.control.check_requirements import (
    assembly_stage_requirement,
    component_lineage_stage_requirement,
    relation_authoring_stage_requirements,
    spatial_layout_stage_requirement,
)
from archflow.control.component_functions import (
    DEFAULT_COMPONENT_FUNCTION_POLICY,
    ComponentFunctionContract,
    ComponentFunctionId,
    ComponentFunctionLedger,
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
    FunctionRelationRequirementSet,
    compile_function_relation_requirements,
)
from archflow.control.relation_checks import check_relation_coverage
from archflow.control.relation_promotion import (
    promote_verified_relation_graph,
)
from archflow.control.stage_closure import (
    CompositeStageClosureReceipt,
    StageClosureFinding,
    StageClosureFindingCode,
    StageClosureStatus,
    compile_composite_stage_closure,
)
from archflow.control.profile import StageRequirementProfileBinding
from archflow.control.semantic_capabilities import (
    bind_semantic_rule_packs,
    current_semantic_capability_policy,
)
from archflow.control.stage_subjects import (
    StageSubjectDisposition,
    StageSubjectInventory,
    StageSubjectInventoryEntry,
    StageSubjectRoleObligation,
)
from archflow.control.stage_control_sources import (
    ComponentFunctionBaselineSource,
    VisualInventoryBaselineSource,
)
from archflow.control.requirements import (
    RequirementBasisMode,
    RequirementTargetKind,
    StageCheckRequirement,
    StageRequirementProfile,
)
from archflow.interaction import (
    ClarificationAlternative,
    ClarificationDisposition,
    ClarificationEffect,
    ClarifiedFactValue,
)
from archflow.project import (
    BranchRef,
    ProjectRecordRef,
    ProjectVersionRef,
    RunRef,
)
from archflow.relations.authoring import (
    RelationAnswerStatus,
    RelationAuthoringProposal,
    RelationBasisBinding,
    RelationBasisKind,
    RelationBasisUse,
    RelationDerivationAnswer,
    RelationProposalSpec,
    RelationRuleProposalSpec,
    compile_relation_authoring,
)
from archflow.relations.contracts import (
    ArchitecturalRelationKind,
    ArchitecturalNode,
    ArchitecturalNodeKind,
    RelationEpistemicStatus,
    RelationParticipant,
    RelationProjection,
)
from archflow.runtime.clarification import (
    create_clarification_request,
    issue_authority_decision,
)
from archflow.runtime.commitment_compiler import (
    IntentObservation,
    IntentOperator,
    IntentTerm,
    compile_intent,
    confirm_proposal,
)
from archflow.runtime.design_controller import (
    ControllerOutcome,
    ControllerStatus,
    DesignControllerCheckpoint,
    DesignControllerError,
    GroundedArchitectAction,
    MidRunRequirementStatus,
    advance_design_phase,
    apply_architect_action,
    close_reopened_nodes,
    compile_mid_run_requirement,
    consult_selected_experts,
    pause_for_clarification,
    prepare_design_turn,
    revise_design_phase,
    resume_authority_pause,
)
from archflow.runtime.event_log import EventDecision
from archflow.runtime.state_reducer import (
    CanonicalStateMutation,
    make_initialization_event,
    make_transition_event,
)
from archflow.state import initialize_canonical_project
from archflow.state.commitments import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CriterionRef,
)
from archflow.state.decision_operator import DecisionOperator
from archflow.state.design_state import (
    ContextSliceCompiler,
    DesignStateError,
    DesignStateLayer,
    DesignStateNode,
    DesignStateTree,
    InterfaceConstraint,
    StatePath,
    StatePathSegment,
    compile_nested_decision,
)
from archflow.state.design_maturity import (
    BackwardRevisionRequest,
    DeliverableRole,
    DesignMaturityState,
    DesignPhase,
    PHASE_DELIVERABLE_ROLES,
    PhaseDeliverable,
    PhaseGateRequest,
    evaluate_forward_phase_gate,
)
from archflow.state.operational_state import (
    DependencyEdge,
    DependencyEffect,
    DesignObligation,
    ObligationStatus,
    OperationalMarkovState,
    StateLock,
    StateDomain,
    StateFact,
)
from archflow.control.convergence import (
    StageConvergenceOutcome,
    StageConvergencePotential,
    StageConvergenceReceipt,
    StageTransitionKind,
)
from archflow.validation.assembly import RelationshipKind, check_assembly
from archflow.validation.check_bridges import (
    bridge_component_lineage_receipt,
    bridge_spatial_validation_receipt,
)
from archflow.validation.contracts import CheckReceiptEnvelope, CheckStatus
from archflow.validation.stage_control import (
    check_component_function_baseline,
    check_visual_inventory_baseline,
)
from archflow.validation.spatial import validate_spatial_layout
from tests.test_stage_baseline import physical_sources
from tests.test_relation_authoring import (
    _context as relation_context_fixture,
    _proposal as relation_proposal_fixture,
)
from tests.test_relation_realization import (
    compiled_program as relation_compiled_program,
    manifest as relation_manifest,
    readback as relation_readback,
)


_GLOBAL_COMMITMENT_REF = "commitment:preserve-public-purpose"


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _stage_component_proposal_ref(
    branch: BranchRef,
    stage_id: str,
    subject_digest: str,
) -> ProjectRecordRef:
    prefix = (
        f"runs/{branch.run.run_id}/branches/"
        f"{branch.branch_id}/records"
    )
    return ProjectRecordRef(
        project_id=branch.run.project_id,
        relative_path=f"{prefix}/component-proposal.json",
        sha256=_hash(
            f"component-proposal:{stage_id}:{subject_digest}"
        ),
    )


def _branch(*, epoch: int = 0) -> BranchRef:
    return BranchRef(
        run=RunRef(
            project_id="nested-state-test",
            run_id="run-001",
            base=ProjectVersionRef(
                project_id="nested-state-test",
                version=2,
                state_sha256=_hash("canonical-base"),
            ),
        ),
        branch_id="option-a",
        epoch=epoch,
    )


def _path(*pairs: tuple[DesignStateLayer, str]) -> StatePath:
    return StatePath(
        tuple(
            StatePathSegment(layer=layer, node_id=node_id)
            for layer, node_id in pairs
        )
    )


def _state(
    *,
    facts: tuple[StateFact, ...] = (),
    commitments: tuple[Commitment, ...] = (),
    obligations: tuple[DesignObligation, ...] = (),
    epoch: int = 0,
) -> OperationalMarkovState:
    return OperationalMarkovState(
        branch=_branch(epoch=epoch),
        compiler_version="nested-test-1",
        phase="schematic_design",
        facts=facts,
        commitments=commitments,
        obligations=obligations,
        evidence_refs=("evidence://request/root",),
    )


def _fact(domain: StateDomain, key: str) -> StateFact:
    return StateFact(
        domain=domain,
        key=key,
        value={"value": key},
        source_ref=f"evidence://fact/{key}",
    )


def _global_commitment() -> Commitment:
    return Commitment(
        commitment_id="preserve-public-purpose",
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.ACTIVE,
        authority_id="authority-user",
        authorized_by="authority-user",
        source_event_ref="event://request/purpose",
        satisfaction_criterion=CriterionRef(
            criterion_id="criterion-public-purpose",
            provider_id="validator-program",
            subject_refs=("semantic://public-purpose",),
        ),
        evidence_refs=("evidence://request/purpose",),
        scope_refs=("semantic://public-purpose",),
        monitor_state_ref="monitor://public-purpose",
    )


def _tree() -> tuple[DesignStateTree, dict[str, DesignStateNode]]:
    root_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
    )
    phase_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
    )
    structure_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
        (DesignStateLayer.DISCIPLINE, "structure"),
    )
    circulation_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
        (DesignStateLayer.DISCIPLINE, "circulation"),
    )
    envelope_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
        (DesignStateLayer.DISCIPLINE, "envelope"),
    )
    grid_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
        (DesignStateLayer.DISCIPLINE, "structure"),
        (DesignStateLayer.COMPONENT, "grid"),
    )
    stair_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
        (DesignStateLayer.DISCIPLINE, "circulation"),
        (DesignStateLayer.COMPONENT, "stair"),
    )
    facade_path = _path(
        (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
        (DesignStateLayer.PHASE, "schematic_design"),
        (DesignStateLayer.DISCIPLINE, "envelope"),
        (DesignStateLayer.COMPONENT, "facade"),
    )

    root = DesignStateNode(
        path=root_path,
        operational_state=_state(
            facts=(_fact(StateDomain.BRIEF, "concept-intent"),),
            commitments=(_global_commitment(),),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "structure-agent",
            "circulation-agent",
            "envelope-agent",
        ),
        child_refs=(phase_path.ref,),
    )
    phase = DesignStateNode(
        path=phase_path,
        operational_state=_state(
            facts=(_fact(StateDomain.DECISION, "phase-goal"),),
        ),
        allowed_authority_ids=root.allowed_authority_ids,
        child_refs=(
            structure_path.ref,
            circulation_path.ref,
            envelope_path.ref,
        ),
    )
    structure = DesignStateNode(
        path=structure_path,
        operational_state=_state(
            facts=(_fact(StateDomain.SEMANTIC, "structural-intent"),),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "structure-agent",
        ),
        child_refs=(grid_path.ref,),
    )
    circulation = DesignStateNode(
        path=circulation_path,
        operational_state=_state(
            facts=(_fact(StateDomain.SEMANTIC, "circulation-intent"),),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "circulation-agent",
        ),
        child_refs=(stair_path.ref,),
    )
    envelope = DesignStateNode(
        path=envelope_path,
        operational_state=_state(
            facts=(_fact(StateDomain.SEMANTIC, "envelope-intent"),),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "envelope-agent",
        ),
        child_refs=(facade_path.ref,),
    )
    grid = DesignStateNode(
        path=grid_path,
        operational_state=_state(
            obligations=(
                DesignObligation(
                    obligation_id="resolve-grid",
                    statement="Resolve current grid relationship.",
                    source_ref="evidence://obligation/grid",
                    status=ObligationStatus.OPEN,
                    subject_refs=("semantic://grid",),
                ),
            ),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "structure-agent",
        ),
    )
    stair = DesignStateNode(
        path=stair_path,
        operational_state=_state(
            facts=(_fact(StateDomain.GEOMETRY, "stair-core"),),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "circulation-agent",
        ),
    )
    facade = DesignStateNode(
        path=facade_path,
        operational_state=_state(
            facts=(_fact(StateDomain.EVALUATION, "facade-character"),),
        ),
        allowed_authority_ids=(
            "authority-user",
            "architect",
            "envelope-agent",
        ),
    )
    interfaces = (
        InterfaceConstraint(
            interface_id="grid-to-stair",
            source_node_ref=grid.ref,
            target_node_ref=stair.ref,
            statement="Grid changes require stair coordination.",
            source_refs=("fact:parameter:grid-spacing",),
            target_refs=("fact:geometry:stair-core",),
            effect=DependencyEffect.INVALIDATES,
            evidence_refs=("evidence://interface/grid-stair",),
        ),
        InterfaceConstraint(
            interface_id="grid-to-facade",
            source_node_ref=grid.ref,
            target_node_ref=facade.ref,
            statement="Grid changes require facade revalidation.",
            source_refs=("fact:parameter:grid-spacing",),
            target_refs=("fact:evaluation:facade-character",),
            effect=DependencyEffect.REQUIRES_REVALIDATION,
            evidence_refs=("evidence://interface/grid-facade",),
        ),
        InterfaceConstraint(
            interface_id="concept-supports-facade",
            source_node_ref=root.ref,
            target_node_ref=facade.ref,
            statement="Concept is advisory context for facade work.",
            source_refs=("fact:brief:concept-intent",),
            target_refs=("fact:evaluation:facade-character",),
            effect=DependencyEffect.SUPPORTS_ONLY,
            evidence_refs=("evidence://interface/concept-facade",),
        ),
    )
    nodes = {
        "root": root,
        "phase": phase,
        "structure": structure,
        "circulation": circulation,
        "envelope": envelope,
        "grid": grid,
        "stair": stair,
        "facade": facade,
    }
    return (
        DesignStateTree(
            branch=_branch(),
            nodes=tuple(nodes.values()),
            interfaces=interfaces,
        ),
        nodes,
    )


class NestedDesignStateTests(unittest.TestCase):
    def test_tree_round_trip_has_one_root_and_narrowing_authority(
        self,
    ) -> None:
        tree, nodes = _tree()

        reloaded = DesignStateTree.from_dict(tree.to_dict())

        self.assertEqual(reloaded, tree)
        self.assertEqual(reloaded.tree_digest, tree.tree_digest)
        self.assertEqual(reloaded.root.ref, nodes["root"].ref)

        widened = DesignStateNode(
            path=nodes["grid"].path,
            operational_state=nodes["grid"].operational_state,
            allowed_authority_ids=(
                *nodes["grid"].allowed_authority_ids,
                "unscoped-agent",
            ),
        )
        with self.assertRaisesRegex(
            DesignStateError,
            "authority cannot widen",
        ):
            DesignStateTree(
                branch=tree.branch,
                nodes=tuple(
                    widened if item.ref == widened.ref else item
                    for item in tree.nodes
                ),
                interfaces=tree.interfaces,
            )

    def test_context_slice_keeps_global_constraints_and_omits_siblings(
        self,
    ) -> None:
        tree, nodes = _tree()

        context = ContextSliceCompiler().compile(
            tree,
            target_node_ref=nodes["grid"].ref,
        )
        reloaded = ContextSliceCompiler().compile(
            DesignStateTree.from_dict(tree.to_dict()),
            target_node_ref=nodes["grid"].ref,
        )

        self.assertEqual(
            type(context).from_dict(context.to_dict()),
            context,
        )
        self.assertEqual(reloaded, context)
        self.assertEqual(
            tuple(item.key for item in context.concept_facts),
            ("concept-intent",),
        )
        self.assertEqual(
            tuple(item.key for item in context.ancestor_facts),
            ("phase-goal", "structural-intent"),
        )
        self.assertEqual(
            tuple(
                item.commitment_id for item in context.commitments
            ),
            ("preserve-public-purpose",),
        )
        self.assertEqual(
            tuple(
                item.obligation_id for item in context.obligations
            ),
            ("resolve-grid",),
        )
        self.assertEqual(
            {item.interface_id for item in context.interfaces},
            {"grid-to-facade", "grid-to-stair"},
        )
        self.assertIn(nodes["stair"].ref, context.omitted_node_refs)
        self.assertIn(nodes["facade"].ref, context.omitted_node_refs)
        self.assertNotIn(
            "stair-core",
            {item.key for item in context.local_facts},
        )
        self.assertNotIn(
            "facade-character",
            {item.key for item in context.local_facts},
        )
        self.assertEqual(len(context.context_digest), 64)

    def test_local_decision_reopens_only_named_interface_targets(
        self,
    ) -> None:
        tree, nodes = _tree()
        original = {
            name: node.operational_state.state_digest
            for name, node in nodes.items()
        }
        operator = DecisionOperator(
            decision_id="set-grid-spacing",
            decision_type="parameter-derivation",
            base_state_digest=(
                nodes["grid"].operational_state.state_digest
            ),
            authority_id="structure-agent",
            intent="Record an evidence-backed grid spacing.",
            add_facts=(
                StateFact(
                    domain=StateDomain.PARAMETER,
                    key="grid-spacing",
                    value={"range": [6, 8], "unit": "m"},
                    source_ref="evidence://analysis/grid-spacing",
                ),
            ),
            discharge_obligation_ids=("resolve-grid",),
            evidence_refs=("evidence://analysis/grid-spacing",),
        )

        transition = compile_nested_decision(
            tree,
            target_node_ref=nodes["grid"].ref,
            operator=operator,
        )

        self.assertEqual(
            transition.invalidated_node_refs,
            (nodes["stair"].ref,),
        )
        self.assertEqual(
            transition.revalidation_node_refs,
            (nodes["facade"].ref,),
        )
        self.assertNotEqual(
            transition.tree.node(
                nodes["grid"].ref
            ).operational_state.state_digest,
            original["grid"],
        )
        for name in (
            "root",
            "phase",
            "structure",
            "circulation",
            "envelope",
            "stair",
            "facade",
        ):
            self.assertEqual(
                transition.tree.node(
                    nodes[name].ref
                ).operational_state.state_digest,
                original[name],
            )

    def test_obligation_status_change_reopens_named_interface_target(
        self,
    ) -> None:
        tree, nodes = _tree()
        obligation_interface = InterfaceConstraint(
            interface_id="grid-obligation-to-stair",
            source_node_ref=nodes["grid"].ref,
            target_node_ref=nodes["stair"].ref,
            statement="Grid obligation status changes affect stair work.",
            source_refs=("obligation:resolve-grid",),
            target_refs=("fact:geometry:stair-core",),
            effect=DependencyEffect.INVALIDATES,
            evidence_refs=("evidence://interface/grid-obligation",),
        )
        tree = DesignStateTree(
            branch=tree.branch,
            nodes=tree.nodes,
            interfaces=(obligation_interface,),
        )
        operator = DecisionOperator(
            decision_id="discharge-grid-obligation",
            decision_type="obligation-lifecycle",
            base_state_digest=(
                nodes["grid"].operational_state.state_digest
            ),
            authority_id="structure-agent",
            intent="Discharge the resolved grid obligation.",
            discharge_obligation_ids=("resolve-grid",),
            evidence_refs=("evidence://analysis/grid-resolved",),
        )

        transition = compile_nested_decision(
            tree,
            target_node_ref=nodes["grid"].ref,
            operator=operator,
        )

        self.assertEqual(
            transition.invalidated_node_refs,
            (nodes["stair"].ref,),
        )
        self.assertEqual(
            transition.tree.node(
                nodes["stair"].ref
            ).operational_state,
            nodes["stair"].operational_state,
        )

    def test_lock_and_dependency_changes_reopen_named_interfaces(
        self,
    ) -> None:
        for change_kind in ("lock", "dependency"):
            with self.subTest(change_kind=change_kind):
                tree, nodes = _tree()
                grid_state = nodes["grid"].operational_state
                if change_kind == "lock":
                    lock = StateLock(
                        target_ref="semantic://grid",
                        authority_id="structure-agent",
                        source_ref="evidence://authority/grid-lock",
                    )
                    grid_state = replace(grid_state, locks=(lock,))
                    source_ref = f"lock:{lock.target_ref}"
                    operator_fields = {
                        "release_lock_refs": (lock.target_ref,),
                    }
                else:
                    dependency = DependencyEdge(
                        upstream_ref="obligation:resolve-grid",
                        downstream_ref="deliverable://grid-package",
                        relation="supports-grid-package",
                        source_ref="evidence://dependency/grid-package",
                    )
                    grid_state = replace(
                        grid_state,
                        dependencies=(dependency,),
                    )
                    source_ref = dependency.ref
                    operator_fields = {
                        "delete_dependency_refs": (dependency.ref,),
                    }
                grid = replace(
                    nodes["grid"],
                    operational_state=grid_state,
                )
                interface = InterfaceConstraint(
                    interface_id=f"{change_kind}-to-facade",
                    source_node_ref=grid.ref,
                    target_node_ref=nodes["facade"].ref,
                    statement=(
                        f"Grid {change_kind} changes require facade review."
                    ),
                    source_refs=(source_ref,),
                    target_refs=(
                        "fact:evaluation:facade-character",
                    ),
                    effect=DependencyEffect.REQUIRES_REVALIDATION,
                    evidence_refs=(
                        f"evidence://interface/{change_kind}-facade",
                    ),
                )
                scoped_tree = DesignStateTree(
                    branch=tree.branch,
                    nodes=tuple(
                        grid if item.ref == grid.ref else item
                        for item in tree.nodes
                    ),
                    interfaces=(interface,),
                )
                operator = DecisionOperator(
                    decision_id=f"change-{change_kind}",
                    decision_type=f"{change_kind}-lifecycle",
                    base_state_digest=grid_state.state_digest,
                    authority_id="structure-agent",
                    intent=f"Apply the explicit {change_kind} change.",
                    evidence_refs=(
                        f"evidence://analysis/{change_kind}-change",
                    ),
                    **operator_fields,
                )

                transition = compile_nested_decision(
                    scoped_tree,
                    target_node_ref=grid.ref,
                    operator=operator,
                )

                self.assertEqual(
                    transition.revalidation_node_refs,
                    (nodes["facade"].ref,),
                )

    def test_unrelated_or_unauthorized_agent_cannot_mutate_node(
        self,
    ) -> None:
        tree, nodes = _tree()
        unauthorized = DecisionOperator(
            decision_id="change-grid",
            decision_type="parameter-derivation",
            base_state_digest=(
                nodes["grid"].operational_state.state_digest
            ),
            authority_id="envelope-agent",
            intent="Attempt a cross-discipline change.",
            add_facts=(
                _fact(StateDomain.PARAMETER, "grid-spacing"),
            ),
            evidence_refs=("evidence://attempt/cross-discipline",),
        )

        with self.assertRaisesRegex(
            DesignStateError,
            "outside target-node permission",
        ):
            compile_nested_decision(
                tree,
                target_node_ref=nodes["grid"].ref,
                operator=unauthorized,
            )

    def test_path_cannot_skip_hierarchy_levels(self) -> None:
        with self.assertRaisesRegex(
            DesignStateError,
            "without skipping",
        ):
            _path(
                (DesignStateLayer.GLOBAL_CONCEPT, "concept"),
                (DesignStateLayer.DISCIPLINE, "structure"),
            )


def _checkpoint(
    *,
    max_iterations: int = 4,
) -> tuple[DesignControllerCheckpoint, dict[str, DesignStateNode]]:
    tree, nodes = _tree()
    target = nodes["grid"]
    return (
        DesignControllerCheckpoint(
            tree=tree,
            target_node_ref=target.ref,
            maturity=DesignMaturityState(
                branch=tree.branch,
                operational_state_digest=(
                    target.operational_state.state_digest
                ),
                phase=DesignPhase.SCHEMATIC_DESIGN,
            ),
            status=ControllerStatus.READY,
            iteration=0,
            max_iterations=max_iterations,
            history_event_refs=("design-event:initial",),
        ),
        nodes,
    )


def _experts() -> tuple[
    ExpertRegistry,
    dict[str, PhaseExpertMetadata],
]:
    registry = ExpertRegistry()
    for expert_id, summary in (
        ("expert-structure-a", "Prefer the first bounded option."),
        ("expert-structure-b", "Prefer the second bounded option."),
    ):
        registry.register(
            ExpertSpec(
                expert_id=expert_id,
                description=f"Read-only {expert_id}",
                topics=frozenset({"structure"}),
            ),
            lambda snapshot, text=summary: ExpertAdvice(
                summary=text,
                findings=("Current obligation requires a decision.",),
                evidence_refs=("evidence://expert/structure",),
            ),
        )
    metadata = {
        expert_id: PhaseExpertMetadata(
            expert_id=expert_id,
            allowed_phases=frozenset(
                {DesignPhase.SCHEMATIC_DESIGN}
            ),
        )
        for expert_id in (
            "expert-structure-a",
            "expert-structure-b",
        )
    }
    return registry, metadata


def _operator(
    checkpoint: DesignControllerCheckpoint,
    *,
    decision_id: str,
    discharge: bool,
) -> DecisionOperator:
    target = checkpoint.tree.node(checkpoint.target_node_ref)
    return DecisionOperator(
        decision_id=decision_id,
        decision_type="parameter-derivation",
        base_state_digest=target.operational_state.state_digest,
        authority_id="structure-agent",
        intent="Record an evidence-backed grid spacing.",
        add_facts=(
            StateFact(
                domain=StateDomain.PARAMETER,
                key="grid-spacing",
                value={"range": [6, 8], "unit": "m"},
                source_ref="evidence://analysis/grid-spacing",
            ),
        ),
        discharge_obligation_ids=(
            ("resolve-grid",) if discharge else ()
        ),
        evidence_refs=("evidence://analysis/grid-spacing",),
    )


def _phase_deliverables(
    checkpoint: DesignControllerCheckpoint,
) -> tuple[PhaseDeliverable, ...]:
    branch = checkpoint.tree.branch
    subject_digest = checkpoint.tree.node(
        checkpoint.target_node_ref
    ).operational_state.state_digest
    proposal_ref = _stage_component_proposal_ref(
        branch,
        DesignPhase.SCHEMATIC_DESIGN.value,
        subject_digest,
    )
    return tuple(
        PhaseDeliverable(
            deliverable_id=f"schematic-{role.value}",
            role=role,
            produced_phase=DesignPhase.SCHEMATIC_DESIGN,
            branch=branch,
            base_state_digest=subject_digest,
            artifact_ref=f"artifact://schematic/{role.value}",
            evidence_refs=(
                f"evidence://schematic/{role.value}",
                proposal_ref.uri,
            ),
        )
        for role in sorted(
            PHASE_DELIVERABLE_ROLES[
                DesignPhase.SCHEMATIC_DESIGN
            ],
            key=lambda item: item.value,
        )
    )


def _phase_ready_checkpoint() -> DesignControllerCheckpoint:
    checkpoint, _ = _checkpoint()
    target = checkpoint.tree.node(checkpoint.target_node_ref)
    closed_target = replace(
        target,
        operational_state=replace(
            target.operational_state,
            obligations=tuple(
                replace(item, status=ObligationStatus.SATISFIED)
                for item in target.operational_state.obligations
            ),
        ),
    )
    tree = DesignStateTree(
        branch=checkpoint.tree.branch,
        nodes=tuple(
            closed_target if item.ref == target.ref else item
            for item in checkpoint.tree.nodes
        ),
        interfaces=checkpoint.tree.interfaces,
    )
    current = replace(
        checkpoint,
        tree=tree,
        maturity=DesignMaturityState(
            branch=tree.branch,
            operational_state_digest=(
                closed_target.operational_state.state_digest
            ),
            phase=DesignPhase.SCHEMATIC_DESIGN,
        ),
    )
    deliverables = _phase_deliverables(current)
    target_with_deliverables = replace(
        closed_target,
        phase_deliverable_refs=tuple(
            item.ref for item in deliverables
        ),
    )
    tree = DesignStateTree(
        branch=tree.branch,
        nodes=tuple(
            target_with_deliverables
            if item.ref == closed_target.ref
            else item
            for item in tree.nodes
        ),
        interfaces=tree.interfaces,
    )
    return replace(
        current,
        tree=tree,
        maturity=replace(
            current.maturity,
            deliverables=deliverables,
        ),
    )


def _convergence_potential(
    *,
    open_refs: tuple[str, ...] = (),
    invalidated_refs: tuple[str, ...] = (),
) -> StageConvergencePotential:
    return StageConvergencePotential(
        hard_gate_failure_refs=(),
        conflict_refs=(),
        tolerance_failure_refs=(),
        missing_mandatory_obligation_refs=(),
        blocked_mandatory_obligation_refs=(),
        open_mandatory_obligation_refs=open_refs,
        invalidated_refs=invalidated_refs,
        revalidation_refs=(),
    )


def _stage_convergence_receipt(
    checkpoint: DesignControllerCheckpoint,
    *,
    outcome: StageConvergenceOutcome = StageConvergenceOutcome.PROGRESS,
    transition_kind: StageTransitionKind = StageTransitionKind.RESOLVE,
    potential_before: StageConvergencePotential | None = None,
    potential_after: StageConvergencePotential | None = None,
    receipt_branch: BranchRef | None = None,
    child_state_digest: str | None = None,
) -> StageConvergenceReceipt:
    state = checkpoint.tree.node(
        checkpoint.target_node_ref
    ).operational_state
    rejected = outcome is StageConvergenceOutcome.REJECTED
    return StageConvergenceReceipt(
        receipt_id=f"scr-{_hash(checkpoint.checkpoint_digest)[:24]}",
        outcome=outcome,
        request_id="controller-stage-close",
        stage="schematic-design",
        transition_kind=transition_kind,
        branch=state.branch if receipt_branch is None else receipt_branch,
        policy_digest=_hash("controller-stage-policy"),
        parent_state_digest=_hash("controller-parent-state"),
        child_state_digest=(
            state.state_digest
            if child_state_digest is None
            else child_state_digest
        ),
        parent_sufficient_digest=_hash("controller-parent-sufficient"),
        child_sufficient_digest=state.sufficient_digest,
        parent_evidence_digest=_hash("controller-parent-evidence"),
        child_evidence_digest=_hash("controller-child-evidence"),
        potential_before=(
            _convergence_potential(
                open_refs=("obligation:prior-stage-work",)
            )
            if potential_before is None
            else potential_before
        ),
        potential_after=(
            _convergence_potential()
            if potential_after is None
            else potential_after
        ),
        protected_refs=(),
        changed_protected_refs=(),
        mandatory_obligation_ids=(),
        added_mandatory_obligation_ids=(),
        dependency_closure=(),
        authorization_ref=None,
        reason_codes=(
            ("stage_convergence.test_rejected",) if rejected else ()
        ),
    )


def _stage_baseline_evidence(
    checkpoint: DesignControllerCheckpoint,
    *,
    stage_id: str | None = None,
    receipt_branch: BranchRef | None = None,
    subject_digest: str | None = None,
) -> tuple[
    StageBaselineSourceSet,
    tuple[StageCheckRequirement, ...],
    tuple[CheckReceiptEnvelope, ...],
]:
    branch = (
        checkpoint.maturity.branch
        if receipt_branch is None
        else receipt_branch
    )
    stage = (
        checkpoint.maturity.phase.value if stage_id is None else stage_id
    )
    digest = (
        checkpoint.maturity.operational_state_digest
        if subject_digest is None
        else subject_digest
    )
    scope_digest = _hash("controller-stage-closure-scope")
    base = physical_sources(stage_subject_source_digest=digest)
    lineage = base.component_lineage[0]
    lineage_profile = replace(
        lineage.profile,
        branch=branch,
        scope_digest=scope_digest,
        predecessor_stage_id="prior-stage",
        successor_stage_id=stage,
    )
    lineage_source_receipt = replace(
        lineage.source_receipt,
        predecessor_stage_id="prior-stage",
        successor_stage_id=stage,
    )
    spatial = base.spatial_layout[0]
    spatial_profile = replace(
        spatial.profile,
        branch=branch,
        scope_digest=scope_digest,
        stage_id=stage,
    )
    spatial_input = spatial.validator_input
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
    sources = StageBaselineSourceSet(
        component_lineage=(
            ComponentLineageBaselineSource(
                lineage_profile,
                lineage_source_receipt,
            ),
        ),
        spatial_layout=(
            SpatialLayoutBaselineSource(
                spatial_profile,
                spatial_input,
            ),
        ),
        assembly=base.assembly,
    )
    requirements = (
        component_lineage_stage_requirement(lineage_profile),
        spatial_layout_stage_requirement(spatial_profile),
        assembly_stage_requirement(sources.assembly[0]),
    )
    receipts = (
        bridge_component_lineage_receipt(
            lineage_profile,
            lineage_source_receipt,
            stage_subject_digest=digest,
        ),
        bridge_spatial_validation_receipt(
            spatial_profile,
            spatial_receipt,
            stage_subject_digest=digest,
        ),
        check_assembly(
            sources.assembly[0],
            branch=branch,
            scope_digest=scope_digest,
            stage_subject_digest=digest,
        ),
    )
    return sources, requirements, receipts


def _stage_requirement_profile(
    checkpoint: DesignControllerCheckpoint,
    *,
    stage_id: str | None = None,
    receipt_branch: BranchRef | None = None,
    stage_subject_ref: str | None = None,
) -> StageRequirementProfile:
    deliverable = checkpoint.maturity.deliverables[0]
    subject_ref = (
        deliverable.ref if stage_subject_ref is None else stage_subject_ref
    )
    branch = (
        checkpoint.maturity.branch
        if receipt_branch is None
        else receipt_branch
    )
    _sources, requirements, _receipts = _stage_baseline_evidence(
        checkpoint,
        stage_id=stage_id,
        receipt_branch=receipt_branch,
        subject_digest=deliverable.base_state_digest,
    )
    return StageRequirementProfile(
        profile_id="controller-stage-closure",
        typology_id="synthetic-controller-fixture",
        stage_id=(
            checkpoint.maturity.phase.value
            if stage_id is None
            else stage_id
        ),
        branch=branch,
        predecessor_state_digest=(
            checkpoint.maturity.operational_state_digest
        ),
        scope_digest=_hash("controller-stage-closure-scope"),
        stage_subject_ref=subject_ref,
        requirements=requirements,
    )


def _stage_closure_receipt(
    checkpoint: DesignControllerCheckpoint,
    *,
    stage_id: str | None = None,
    receipt_branch: BranchRef | None = None,
    stage_subject_ref: str | None = None,
    subject_digest: str | None = None,
    findings: tuple[StageClosureFinding, ...] = (),
) -> CompositeStageClosureReceipt:
    _profile, _sources, _receipts, _inventory, closure = (
        _complete_stage_inputs(
            checkpoint,
            stage_id=stage_id,
            receipt_branch=receipt_branch,
            stage_subject_ref=stage_subject_ref,
            subject_digest=subject_digest,
        )
    )
    if not findings:
        return closure
    return replace(
        closure,
        findings=findings,
        status=StageClosureStatus.OPEN,
    )


def _legacy_stage_closure_receipt(
    checkpoint: DesignControllerCheckpoint,
    *,
    stage_id: str | None = None,
    receipt_branch: BranchRef | None = None,
    stage_subject_ref: str | None = None,
    subject_digest: str | None = None,
) -> CompositeStageClosureReceipt:
    """Build the pre-M083 closure shape for explicit negative tests only."""

    deliverable = checkpoint.maturity.deliverables[0]
    profile = _stage_requirement_profile(
        checkpoint,
        stage_id=stage_id,
        receipt_branch=receipt_branch,
        stage_subject_ref=stage_subject_ref,
    )
    selected_subject_digest = (
        deliverable.base_state_digest
        if subject_digest is None
        else subject_digest
    )
    _sources, _requirements, receipts = _stage_baseline_evidence(
        checkpoint,
        stage_id=stage_id,
        receipt_branch=receipt_branch,
        subject_digest=selected_subject_digest,
    )
    closure = compile_composite_stage_closure(
        profile,
        subject_digest=selected_subject_digest,
        check_receipts=receipts,
    )
    return closure


def _stage_requirement_profile_for_closure(
    checkpoint: DesignControllerCheckpoint,
    closure: CompositeStageClosureReceipt,
) -> StageRequirementProfile:
    return _stage_requirement_profile(
        checkpoint,
        stage_id=closure.stage_id,
        receipt_branch=closure.branch,
        stage_subject_ref=closure.stage_subject_ref,
    )


def _stage_inputs_for_closure(
    checkpoint: DesignControllerCheckpoint,
    closure: CompositeStageClosureReceipt,
) -> tuple[
    StageRequirementProfile,
    StageBaselineSourceSet,
    tuple[CheckReceiptEnvelope, ...],
    StageSubjectInventory,
]:
    profile, sources, receipts, inventory, _generated_closure = (
        _complete_stage_inputs(
            checkpoint,
            stage_id=closure.stage_id,
            receipt_branch=closure.branch,
            stage_subject_ref=closure.stage_subject_ref,
            subject_digest=closure.subject_digest,
        )
    )
    return profile, sources, receipts, inventory


def _stage_role_target_refs(
    sources: StageBaselineSourceSet,
) -> dict[StageBaselineRole, tuple[str, ...]]:
    targets: dict[StageBaselineRole, set[str]] = {
        role: set() for role in StageBaselineRole
    }
    for source in sources.component_lineage:
        targets[StageBaselineRole.COMPONENT_LINEAGE].update(
            f"component:{operation.ref.component_id}"
            for operation in source.source_receipt.predecessor_operations
        )
    for source in sources.spatial_layout:
        targets[StageBaselineRole.SPATIAL_ENVELOPE].update(
            f"component:{component_id}"
            for component_id in source.validator_input.required_component_ids
        )
    for source in sources.assembly:
        targets[StageBaselineRole.ASSEMBLY_RELATIONSHIPS].update(
            source.coverage_manifest.stage_subject_refs
        )
        targets[StageBaselineRole.OPENING_CLEARANCE].update(
            ref
            for requirement in source.requirements
            if requirement.kind is RelationshipKind.OPENING_CLEAR
            for ref in requirement.subject_refs
        )
        targets[StageBaselineRole.LOAD_PATH].update(
            ref
            for requirement in source.requirements
            if requirement.kind
            in {
                RelationshipKind.SUPPORT,
                RelationshipKind.VERTICAL_SUPPORT_CHAIN,
                RelationshipKind.LOAD_PATH_TO_FOUNDATION,
            }
            for ref in requirement.subject_refs
        )
    for source in sources.material_binding:
        targets[StageBaselineRole.MATERIAL_BINDING].update(
            requirement.semantic_subject_ref
            for requirement in source.profile.requirements
        )
    for source in sources.cad_readback:
        targets[StageBaselineRole.CAD_READBACK].update(
            requirement.object_ref
            for requirement in source.profile.object_requirements
        )
    return {
        role: tuple(sorted(refs))
        for role, refs in targets.items()
    }


def _text_only_visual_inventory(
    *,
    source_ref: str = "evidence:controller-visual-disposition",
    authority_ref: str = "authority:controller-visual-disposition",
):
    return compile_visual_evidence_inventory(
        source_disposition=VisualSourceDisposition(
            kind=VisualSourceDispositionKind.TEXT_ONLY,
            source_refs=(source_ref,),
            authority_refs=(authority_ref,),
        ),
        source_images=(),
        rois=(),
    )


def _stage_function_control_values(
    inventory: StageSubjectInventory,
    *,
    source_ref: str = "evidence:controller-function-relations",
    authority_ref: str = "authority:controller-function-relations",
) -> tuple[ComponentFunctionLedger, FunctionRelationRequirementSet]:
    """Build a synthetic but complete project-authored function denominator."""

    entries = inventory.entries
    entry_by_id = {entry.component_id: entry for entry in entries}
    support_peer_by_component = {
        "beam": "column",
        "column": "foundation",
        "foundation": "column",
        "roof": "beam",
        "stage-root": "foundation",
    }
    contracts = []
    envelopes = []
    for entry in entries:
        function_id = (
            ComponentFunctionId.SUPPORT_OTHERS
            if entry.component_id == "foundation"
            else ComponentFunctionId.BE_SUPPORTED
        )
        spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id)
        peer = entry_by_id[support_peer_by_component[entry.component_id]]
        endpoint_bindings = tuple(
            FunctionEndpointBinding(
                role=role.role,
                endpoint_refs=(
                    (entry.identity_ref,)
                    if role.component_slot
                    else (peer.identity_ref,)
                ),
            )
            for role in spec.endpoint_roles
        )
        claim = FunctionObligationClaim(
            obligation_ref=spec.obligation_ref,
            endpoint_bindings=endpoint_bindings,
            maturity=spec.required_maturity,
            status=FunctionClaimStatus.PASS,
            evidence_refs=(source_ref,),
            authority_refs=(authority_ref,),
            contradiction_refs=(),
        )
        contracts.append(
            ComponentFunctionContract(
                contract_id=f"{entry.component_id}-function-contract",
                branch=inventory.branch,
                stage_id=inventory.stage_id,
                subject_inventory_digest=inventory.inventory_digest,
                component_ref=entry.identity_ref,
                component_digest=entry.component_digest,
                applicability_decisions=tuple(
                    FunctionApplicabilityDecision(
                        function_id=item,
                        applicability=(
                            FunctionApplicability.REQUIRED
                            if item is function_id
                            else FunctionApplicability.NOT_APPLICABLE
                        ),
                        evidence_refs=(source_ref,),
                        authority_refs=(authority_ref,),
                    )
                    for item in ComponentFunctionId
                ),
                claims=(claim,),
            )
        )
    ledger = compile_component_function_ledger(
        ledger_id=f"{inventory.inventory_id}-functions",
        inventory=inventory,
        contracts=tuple(contracts),
    )
    for entry in entries:
        function_id = (
            ComponentFunctionId.SUPPORT_OTHERS
            if entry.component_id == "foundation"
            else ComponentFunctionId.BE_SUPPORTED
        )
        spec = DEFAULT_COMPONENT_FUNCTION_POLICY.spec_for(function_id)
        peer = entry_by_id[support_peer_by_component[entry.component_id]]
        envelopes.append(
            FunctionRelationEvidenceEnvelope(
                envelope_id=f"{entry.component_id}-function-relation",
                branch=inventory.branch,
                stage_id=inventory.stage_id,
                subject_inventory_digest=inventory.inventory_digest,
                function_ledger_ref=ledger.ledger_ref,
                function_ledger_digest=ledger.ledger_digest,
                component_ref=entry.identity_ref,
                component_digest=entry.component_digest,
                functional_obligation_ref=spec.obligation_ref,
                projection=RelationProjection.SUPPORT,
                relation_kind=ArchitecturalRelationKind.SUPPORT,
                scenario_ref="scenario:gravity",
                endpoint_bindings=tuple(
                    FunctionRelationEndpointBinding(
                        function_role=role.role,
                        relation_role=(
                            "supported"
                            if role.role == "supported_component"
                            else "supporter"
                        ),
                        endpoints=(
                            FunctionRelationEndpoint(
                                component_ref=(
                                    entry.identity_ref
                                    if role.component_slot
                                    else peer.identity_ref
                                ),
                                component_digest=(
                                    entry.component_digest
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
                    f"{entry.component_id}-function-policy",
                    f"{entry.component_id}-function-topology",
                ),
                evidence_refs=(source_ref,),
                authority_refs=(authority_ref,),
                prompt=(
                    "Identify the project-authored support relation for "
                    f"{entry.identity_ref}."
                ),
            )
        )
    requirements = compile_function_relation_requirements(
        set_id=f"{inventory.inventory_id}-function-relations",
        ledger=ledger,
        inventory=inventory,
        envelopes=tuple(envelopes),
    )
    return ledger, requirements


def _stage_function_control_source(
    inventory: StageSubjectInventory,
) -> ComponentFunctionBaselineSource:
    ledger, requirements = _stage_function_control_values(inventory)
    branch = inventory.branch
    prefix = (
        f"runs/{branch.run.run_id}/branches/"
        f"{branch.branch_id}/records"
    )
    return ComponentFunctionBaselineSource(
        ledger_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/component-function-ledger.json",
            sha256=_hash("component-function-ledger-record"),
        ),
        ledger=ledger,
        relation_requirements_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/function-relation-requirements.json",
            sha256=_hash("function-relation-requirements-record"),
        ),
        relation_requirements=requirements,
    )


def _stage_subject_inventory(
    checkpoint: DesignControllerCheckpoint,
    closure: CompositeStageClosureReceipt,
    profile: StageRequirementProfile,
    sources: StageBaselineSourceSet,
) -> StageSubjectInventory:
    level = baseline_level_for_design_phase(checkpoint.maturity.phase)
    targets = _stage_role_target_refs(sources)
    source_refs = ("evidence:controller-relations",)
    binding_authority_ref = ProjectRecordRef(
        project_id=closure.branch.run.project_id,
        relative_path=(
            f"runs/{closure.branch.run.run_id}/branches/"
            f"{closure.branch.branch_id}/records/"
            "stage-profile-authorization.json"
        ),
        sha256=_hash("controller-stage-profile-authorization"),
    )
    authority_refs = (binding_authority_ref.uri,)
    ordered_roles = tuple(
        sorted(
            BASELINE_LEVEL_ROLES[level],
            key=lambda item: item.value,
        )
    )

    def obligations_for(
        component_id: str,
    ) -> tuple[StageSubjectRoleObligation, ...]:
        relation_roles = {
            StageBaselineRole.ASSEMBLY_RELATIONSHIPS,
            StageBaselineRole.LOAD_PATH,
        }
        root_roles = {
            StageBaselineRole.COMPONENT_LINEAGE,
            StageBaselineRole.SPATIAL_ENVELOPE,
        }
        return tuple(
            StageSubjectRoleObligation(
                role=role,
                disposition=(
                    StageSubjectDisposition.REQUIRED
                    if (
                        component_id == "stage-root" and role in root_roles
                    ) or (
                        component_id == "roof" and role in relation_roles
                    )
                    else StageSubjectDisposition.NOT_APPLICABLE
                ),
                target_refs=(
                    (
                        targets[role]
                        or (f"component:uncovered-{role.value}",)
                    )
                    if component_id == "stage-root" and role in root_roles
                    else (
                        ("design-component:roof",)
                        if component_id == "roof" and role in relation_roles
                        else ()
                    )
                ),
                evidence_refs=source_refs,
                authority_refs=authority_refs,
            )
            for role in ordered_roles
        )
    branch = closure.branch
    prefix = (
        f"runs/{branch.run.run_id}/branches/"
        f"{branch.branch_id}/records"
    )
    proposal_ref = _stage_component_proposal_ref(
        branch,
        closure.stage_id,
        closure.subject_digest,
    )
    index_digest = _hash(
        f"component-index:{closure.stage_id}:{closure.subject_digest}"
    )
    visual_inventory_digest = _text_only_visual_inventory().inventory_digest
    return StageSubjectInventory(
        inventory_id="controller-stage-subjects",
        branch=branch,
        stage_id=closure.stage_id,
        stage_subject_ref=closure.stage_subject_ref,
        stage_subject_digest=closure.subject_digest,
        baseline_level=level,
        component_proposal_ref=proposal_ref,
        component_proposal_digest=proposal_ref.sha256,
        component_index_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/component-index.json",
            sha256=index_digest,
        ),
        component_index_digest=index_digest,
        entries=tuple(
            StageSubjectInventoryEntry(
                component_id=component_id,
                identity_ref=f"design-component:{component_id}",
                parent_component_id=parent_component_id,
                semantic_kind=semantic_kind,
                component_digest=closure.subject_digest,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=obligations_for(component_id),
            )
            for component_id, parent_component_id, semantic_kind in (
                ("stage-root", None, "stage-root"),
                ("foundation", "stage-root", "foundation"),
                ("column", "foundation", "column"),
                ("beam", "column", "beam"),
                ("roof", "beam", "roof"),
            )
        ),
        semantic_policy_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/semantic-capability-policy.json",
            sha256=_hash("semantic-capability-policy-record"),
        ),
        semantic_policy=current_semantic_capability_policy(),
        visual_inventory_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/visual-evidence-inventory.json",
            sha256=visual_inventory_digest,
        ),
        visual_inventory_digest=visual_inventory_digest,
    )


def _turn_subject_inventory(
    checkpoint: DesignControllerCheckpoint,
    *,
    semantic_kind: str = "stage-root",
) -> StageSubjectInventory:
    """Exact current turn inventory without inventing project geometry."""

    branch = checkpoint.tree.branch
    target = checkpoint.tree.node(checkpoint.target_node_ref)
    subject_digest = target.operational_state.state_digest
    level = baseline_level_for_design_phase(checkpoint.maturity.phase)
    prefix = (
        f"runs/{branch.run.run_id}/branches/"
        f"{branch.branch_id}/records"
    )
    component_digest = _hash(
        f"turn-component:{subject_digest}:{semantic_kind}"
    )
    policy = current_semantic_capability_policy()
    bindings = bind_semantic_rule_packs(
        policy=policy,
        branch=branch,
        stage_id=checkpoint.maturity.phase.value,
        stage_subject_digest=subject_digest,
        component_ref="design-component:stage-root",
        component_digest=component_digest,
        semantic_kind=semantic_kind,
        baseline_level=level,
    )
    obligations = tuple(
        StageSubjectRoleObligation(
            role=role,
            disposition=StageSubjectDisposition.NOT_APPLICABLE,
            target_refs=(),
            evidence_refs=("evidence:turn-subject-applicability",),
            authority_refs=("authority:turn-subject-applicability",),
        )
        for role in sorted(BASELINE_LEVEL_ROLES[level], key=lambda item: item.value)
    )
    mandatory = tuple(
        StageSubjectRoleObligation(
            role=role,
            disposition=StageSubjectDisposition.REQUIRED,
            target_refs=("design-component:stage-root",),
            evidence_refs=(binding.basis_ref,),
            authority_refs=(binding.authority_ref,),
        )
        for binding in bindings
        for role in binding.mandatory_roles
    )
    obligations = tuple(
        sorted((*obligations, *mandatory), key=lambda item: item.role.value)
    )
    proposal_digest = _hash(
        f"turn-proposal:{subject_digest}:{semantic_kind}"
    )
    index_digest = _hash(
        f"turn-index:{subject_digest}:{semantic_kind}"
    )
    visual_inventory_digest = _hash(
        f"turn-visual-inventory:{subject_digest}:{semantic_kind}"
    )
    return StageSubjectInventory(
        inventory_id="controller-turn-subjects",
        branch=branch,
        stage_id=checkpoint.maturity.phase.value,
        stage_subject_ref="artifact:controller-turn-subject",
        stage_subject_digest=subject_digest,
        baseline_level=level,
        component_proposal_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/turn-component-proposal.json",
            sha256=proposal_digest,
        ),
        component_proposal_digest=proposal_digest,
        component_index_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/turn-component-index.json",
            sha256=index_digest,
        ),
        component_index_digest=index_digest,
        entries=(
            StageSubjectInventoryEntry(
                component_id="stage-root",
                identity_ref="design-component:stage-root",
                parent_component_id=None,
                semantic_kind=semantic_kind,
                component_digest=component_digest,
                geometry_object_ids=(),
                binding_ids=(),
                role_obligations=obligations,
            ),
        ),
        semantic_policy_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/turn-semantic-policy.json",
            sha256=_hash("turn-semantic-policy-record"),
        ),
        semantic_policy=policy,
        semantic_rule_pack_bindings=bindings,
        visual_inventory_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/turn-visual-evidence-inventory.json",
            sha256=visual_inventory_digest,
        ),
        visual_inventory_digest=visual_inventory_digest,
    )


def _stage_relation_topology_evidence(
    inventory: StageSubjectInventory,
    *,
    state_digest: str,
    scope_digest: str,
    source_ref: str = "evidence:controller-relations",
    authority_ref: str | None = None,
    function_requirements: FunctionRelationRequirementSet | None = None,
) -> tuple[
    RelationTopologyBaselineSource,
    tuple[StageCheckRequirement, ...],
    tuple[CheckReceiptEnvelope, ...],
]:
    base_context = relation_context_fixture()
    entry_by_id = {
        entry.component_id: entry for entry in inventory.entries
    }
    relation_component_ids = (
        "beam",
        "column",
        "foundation",
        "roof",
        "stage-root",
    )
    if authority_ref is None:
        authority_ref = (
            f"project://{inventory.branch.run.project_id}/runs/"
            f"{inventory.branch.run.run_id}/branches/"
            f"{inventory.branch.branch_id}/records/"
            "stage-profile-authorization.json"
        )
    questions = list(base_context.questions)
    bases = [
        replace(
            basis,
            evidence_refs=(source_ref,),
            authority_refs=(authority_ref,),
        )
        for basis in base_context.bases
    ]
    if function_requirements is not None:
        if (
            function_requirements.branch != inventory.branch
            or function_requirements.stage_id != inventory.stage_id
            or function_requirements.subject_inventory_digest
            != inventory.inventory_digest
        ):
            raise AssertionError("function requirements crossed the inventory")
        questions.extend(function_requirements.topology_questions)
        for requirement in function_requirements.requirements:
            if len(requirement.question.basis_ids) != 2:
                raise AssertionError("fixture requires one policy and one topology basis")
            policy_basis_id, topology_basis_id = requirement.question.basis_ids
            for basis_id, basis_use in (
                (policy_basis_id, RelationBasisUse.POLICY),
                (topology_basis_id, RelationBasisUse.TOPOLOGY),
            ):
                bases.append(
                    RelationBasisBinding(
                        basis_id=basis_id,
                        basis_kind=(
                            RelationBasisKind.HUMAN
                            if basis_use is RelationBasisUse.POLICY
                            else RelationBasisKind.RAG
                        ),
                        basis_use=basis_use,
                        question_refs=(requirement.question.ref,),
                        allowed_relation_kinds=(
                            requirement.rule.relation_kind,
                        ),
                        epistemic_status=RelationEpistemicStatus.DERIVED,
                        evidence_refs=requirement.rule.evidence_refs,
                        authority_refs=requirement.rule.authority_refs,
                        summary=(
                            "Synthetic project-authored functional relation basis."
                        ),
                    )
                )
    context = replace(
        base_context,
        branch=inventory.branch,
        stage_id=inventory.stage_id,
        state_digest=state_digest,
        scope_digest=scope_digest,
        stage_subject_digest=inventory.stage_subject_digest,
        subject_inventory_ref=(
            f"stage-subject-inventory:{inventory.inventory_digest}"
        ),
        subject_inventory_digest=inventory.inventory_digest,
        nodes=tuple(
            ArchitecturalNode(
                node_ref=entry_by_id[component_id].identity_ref,
                node_kind=ArchitecturalNodeKind.COMPONENT,
                semantic_kind=entry_by_id[component_id].semantic_kind,
                stage_id=inventory.stage_id,
                source_refs=(
                    "stage-subject-entry:"
                    f"{entry_by_id[component_id].entry_digest}",
                ),
            )
            for component_id in relation_component_ids
        ),
        questions=tuple(questions),
        bases=tuple(bases),
    )
    proposal = relation_proposal_fixture(context)
    if function_requirements is not None:
        answers = list(proposal.answers)
        relations = list(proposal.relations)
        rules = list(proposal.rules)
        for requirement in function_requirements.requirements:
            relation_id = f"functional-{requirement.question.question_id}"
            policy_basis_id, topology_basis_id = requirement.question.basis_ids
            participants = tuple(
                RelationParticipant(
                    role=binding.relation_role,
                    node_ref=endpoint.component_ref,
                    ordinal=(
                        index if len(binding.endpoints) > 1 else None
                    ),
                )
                for binding in requirement.endpoint_bindings
                if binding.relation_role is not None
                for index, endpoint in enumerate(binding.endpoints)
            )
            normalized_participants = tuple(
                sorted(participants, key=lambda item: item.identity)
            )
            existing_index = next(
                (
                    index
                    for index, relation in enumerate(relations)
                    if relation.kind is requirement.rule.relation_kind
                    and relation.scenario_ref == requirement.rule.scenario_ref
                    and relation.participants == normalized_participants
                ),
                None,
            )
            if existing_index is None:
                relations.append(
                    RelationProposalSpec(
                        relation_id=relation_id,
                        question_refs=(requirement.question.ref,),
                        kind=requirement.rule.relation_kind,
                        participants=normalized_participants,
                        scenario_ref=requirement.rule.scenario_ref,
                        basis_ids=(topology_basis_id,),
                    )
                )
            else:
                existing = relations[existing_index]
                relation_id = existing.relation_id
                relations[existing_index] = replace(
                    existing,
                    question_refs=tuple(
                        sorted(
                            {
                                *existing.question_refs,
                                requirement.question.ref,
                            }
                        )
                    ),
                    basis_ids=tuple(
                        sorted({*existing.basis_ids, topology_basis_id})
                    ),
                )
            rules.append(
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
                    basis_ids=(policy_basis_id,),
                )
            )
            answers.append(
                RelationDerivationAnswer(
                    question_ref=requirement.question.ref,
                    status=RelationAnswerStatus.PROPOSED,
                    relation_ids=(relation_id,),
                    rule_ids=(requirement.rule.rule_id,),
                    rationale=(
                        "The exact component function obligation is represented "
                        "by the project-authored relation."
                    ),
                )
            )
        proposal = RelationAuthoringProposal(
            context_digest=context.context_digest,
            answers=tuple(answers),
            relations=tuple(relations),
            rules=tuple(rules),
        )
    compilation = compile_relation_authoring(
        context,
        proposal,
    )
    open_requirements = relation_authoring_stage_requirements(
        context,
        compilation,
        inventory,
    )
    verification_receipts = tuple(
        CheckReceiptEnvelope(
            check_id=requirement.requirement_id,
            checker_id=requirement.checker_id,
            checker_version="1.0.0",
            branch=inventory.branch,
            scope_digest=scope_digest,
            subject_refs=requirement.denominator_refs,
            subject_digest=inventory.stage_subject_digest,
            status=CheckStatus.PASS,
            source_refs=requirement.required_source_refs,
            authority_refs=requirement.required_authority_refs,
            coverage_denominator=requirement.denominator_refs,
            covered_refs=requirement.denominator_refs,
        )
        for requirement in open_requirements
        if requirement.requirement_id.startswith("relation-verification-")
    )
    promotion = promote_verified_relation_graph(
        context,
        compilation,
        inventory,
        verification_receipts,
    )
    requirements = relation_authoring_stage_requirements(
        context,
        compilation,
        inventory,
        promotion=promotion,
    )
    _manifest, coverage_receipt = check_relation_coverage(
        promotion.graph,
        compilation.policy,
        inventory,
        compilation.slots,
        promotion=promotion.receipt,
    )
    return (
        RelationTopologyBaselineSource(
            context=context,
            compilation=compilation,
            promotion=promotion,
        ),
        requirements,
        (*verification_receipts, coverage_receipt),
    )


def _complete_stage_inputs(
    checkpoint: DesignControllerCheckpoint,
    *,
    stage_id: str | None = None,
    receipt_branch: BranchRef | None = None,
    stage_subject_ref: str | None = None,
    subject_digest: str | None = None,
) -> tuple[
    StageRequirementProfile,
    StageBaselineSourceSet,
    tuple[CheckReceiptEnvelope, ...],
    StageSubjectInventory,
    CompositeStageClosureReceipt,
]:
    deliverable = checkpoint.maturity.deliverables[0]
    selected_digest = (
        deliverable.base_state_digest
        if subject_digest is None
        else subject_digest
    )
    seed_profile = _stage_requirement_profile(
        checkpoint,
        stage_id=stage_id,
        receipt_branch=receipt_branch,
        stage_subject_ref=stage_subject_ref,
    )
    sources, _requirements, receipts = _stage_baseline_evidence(
        checkpoint,
        stage_id=stage_id,
        receipt_branch=receipt_branch,
        subject_digest=selected_digest,
    )
    seed_closure = compile_composite_stage_closure(
        seed_profile,
        subject_digest=selected_digest,
        check_receipts=receipts,
    )
    inventory = _stage_subject_inventory(
        checkpoint,
        seed_closure,
        seed_profile,
        sources,
    )
    visual_inventory = _text_only_visual_inventory()
    assert inventory.visual_inventory_ref is not None
    visual_source = VisualInventoryBaselineSource(
        branch=inventory.branch,
        stage_id=inventory.stage_id,
        stage_subject_inventory_digest=inventory.inventory_digest,
        inventory_ref=inventory.visual_inventory_ref,
        inventory=visual_inventory,
    )
    function_source = _stage_function_control_source(inventory)
    topology_source, _topology_requirements, topology_receipts = (
        _stage_relation_topology_evidence(
            inventory,
            state_digest=checkpoint.maturity.operational_state_digest,
            scope_digest=seed_profile.scope_digest,
            function_requirements=function_source.relation_requirements,
        )
    )
    sources = replace(
        sources,
        visual_inventory=(visual_source,),
        component_functions=(function_source,),
        relation_topology=(topology_source,),
    )
    completed_profile = derive_stage_requirement_profile(
        seed_profile,
        level=baseline_level_for_design_phase(checkpoint.maturity.phase),
        sources=sources,
        subject_digest=selected_digest,
        subject_inventory=inventory,
    )
    control_receipts = (
        check_visual_inventory_baseline(
            visual_source,
            inventory,
            scope_digest=seed_profile.scope_digest,
            subject_digest=selected_digest,
        ),
        check_component_function_baseline(
            function_source,
            inventory,
            scope_digest=seed_profile.scope_digest,
            subject_digest=selected_digest,
        ),
    )
    all_receipts = (*receipts, *control_receipts, *topology_receipts)
    closure = compile_composite_stage_closure(
        completed_profile,
        subject_digest=selected_digest,
        check_receipts=all_receipts,
    )
    return (
        completed_profile,
        sources,
        all_receipts,
        inventory,
        closure,
    )


def _stage_profile_binding(
    closure: CompositeStageClosureReceipt,
    subject_inventory: StageSubjectInventory,
) -> StageRequirementProfileBinding:
    branch = closure.branch
    prefix = (
        f"runs/{branch.run.run_id}/branches/"
        f"{branch.branch_id}/records"
    )
    return StageRequirementProfileBinding(
        binding_id="controller-stage-profile-binding",
        profile_id=closure.profile_id,
        profile_digest=closure.profile_digest,
        branch=branch,
        stage_id=closure.stage_id,
        stage_subject_ref=closure.stage_subject_ref,
        subject_digest=closure.subject_digest,
        profile_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/stage-requirement-profile.json",
            sha256=closure.profile_digest,
        ),
        stage_subject_inventory_ref=ProjectRecordRef(
            project_id=branch.run.project_id,
            relative_path=f"{prefix}/stage-subject-inventory.json",
            sha256=subject_inventory.inventory_digest,
        ),
        stage_subject_inventory_digest=subject_inventory.inventory_digest,
        authority_refs=(
            ProjectRecordRef(
                project_id=branch.run.project_id,
                relative_path=f"{prefix}/stage-profile-authorization.json",
                sha256=_hash("controller-stage-profile-authorization"),
            ),
        ),
    )


def _event_backed_checkpoint() -> tuple[
    DesignControllerCheckpoint,
    tuple[object, ...],
    object,
]:
    canonical = initialize_canonical_project("nested-state-test")
    initial_event, sealed = make_initialization_event(
        canonical,
        actor_id="system",
    )
    requirement_event, unchanged = make_transition_event(
        sealed,
        initial_event,
        CanonicalStateMutation(),
        event_type="requirement.observed",
        decision=EventDecision.OBSERVED,
        actor_id="user",
        authority_id="authority-user",
        evidence_refs=("evidence://requirement/mid-run",),
    )
    assert unchanged.ref == sealed.ref
    template, template_nodes = _tree()
    branch = BranchRef(
        run=RunRef(
            project_id="nested-state-test",
            run_id="run-001",
            base=sealed.ref,
        ),
        branch_id=template.branch.branch_id,
        epoch=template.branch.epoch,
    )
    tree = DesignStateTree(
        branch=branch,
        nodes=tuple(
            replace(
                node,
                operational_state=replace(
                    node.operational_state,
                    branch=branch,
                ),
            )
            for node in template.nodes
        ),
        interfaces=template.interfaces,
    )
    target_ref = template_nodes["grid"].ref
    target = tree.node(target_ref)
    checkpoint = DesignControllerCheckpoint(
        tree=tree,
        target_node_ref=target_ref,
        maturity=DesignMaturityState(
            branch=branch,
            operational_state_digest=(
                target.operational_state.state_digest
            ),
            phase=DesignPhase.SCHEMATIC_DESIGN,
        ),
        status=ControllerStatus.READY,
        iteration=0,
        max_iterations=4,
        history_event_refs=(initial_event.event_id,),
    )
    return (
        checkpoint,
        (initial_event, requirement_event),
        requirement_event,
    )


class DesignControllerTurnTests(unittest.TestCase):
    def test_geometry_turn_requires_inventory_and_dispatches_stair_work(self) -> None:
        checkpoint, _ = _checkpoint()
        registry = ExpertRegistry()
        registry.register(
            ExpertSpec(
                expert_id="expert-stair",
                description="Read-only stair specialist",
                topics=frozenset({"vertical-circulation"}),
            ),
            lambda snapshot: ExpertAdvice(
                summary="Resolve the exact stair work-item clauses.",
            ),
        )
        metadata = {
            "expert-stair": PhaseExpertMetadata(
                expert_id="expert-stair",
                allowed_phases=frozenset(
                    {DesignPhase.SCHEMATIC_DESIGN}
                ),
            )
        }
        with self.assertRaisesRegex(
            DesignControllerError,
            "requires an exact stage subject inventory",
        ):
            prepare_design_turn(
                checkpoint,
                registry,
                phase_metadata=metadata,
                obligation_topics={"resolve-grid": "structure"},
            )

        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(
                checkpoint,
                semantic_kind="stair",
            ),
        )
        self.assertEqual(prepared.discovered_expert_ids, ("expert-stair",))
        self.assertEqual(len(prepared.semantic_work_items), 1)
        self.assertEqual(
            len(prepared.required_semantic_response_refs),
            1 + len(
                prepared.semantic_work_items[0].binding.active_rule_ids
            ),
        )
        consultation = consult_selected_experts(prepared, registry, ())
        incomplete = GroundedArchitectAction(
            action_id="stair-work-omitted",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="stair-work-omitted-op",
                discharge=True,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="An ordinary obligation cannot stand in for stair work.",
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "omitted mandatory semantic work",
        ):
            apply_architect_action(
                checkpoint,
                prepared,
                consultation,
                incomplete,
                history_event_ref="design-event:stair-work-omitted",
            )

        complete = replace(
            incomplete,
            action_id="stair-work-addressed",
            operator=_operator(
                checkpoint,
                decision_id="stair-work-addressed-op",
                discharge=True,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
                *prepared.required_semantic_response_refs,
            ),
        )
        transitioned = apply_architect_action(
            checkpoint,
            prepared,
            consultation,
            complete,
            history_event_ref="design-event:stair-work-addressed",
        )
        prepared_again = prepare_design_turn(
            transitioned.checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={},
            stage_subject_inventory=_turn_subject_inventory(
                transitioned.checkpoint,
                semantic_kind="stair",
            ),
        )
        self.assertEqual(prepared_again.context.obligations, ())
        self.assertEqual(
            prepared_again.discovered_expert_ids,
            ("expert-stair",),
        )
        self.assertTrue(prepared_again.required_semantic_response_refs)

    def test_phase_advance_rejects_legacy_unbound_semantic_inventory(self) -> None:
        checkpoint = _phase_ready_checkpoint()
        closure = _stage_closure_receipt(checkpoint)
        profile, sources, check_receipts, inventory = (
            _stage_inputs_for_closure(checkpoint, closure)
        )
        legacy_inventory = replace(
            inventory,
            semantic_policy_ref=None,
            semantic_policy=None,
            semantic_rule_pack_bindings=(),
        )
        phase_gate = evaluate_forward_phase_gate(
            checkpoint.maturity,
            PhaseGateRequest(
                request_id="reject-legacy-semantic-inventory",
                branch=checkpoint.maturity.branch,
                base_state_digest=(
                    checkpoint.maturity.operational_state_digest
                ),
                from_phase=DesignPhase.SCHEMATIC_DESIGN,
                to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                deliverable_refs=checkpoint.maturity.deliverable_refs,
            ),
        )

        with self.assertRaisesRegex(
            DesignControllerError,
            "legacy stage subject inventory is read-only",
        ):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=_stage_convergence_receipt(checkpoint),
                requirement_profile=profile,
                profile_binding=_stage_profile_binding(
                    closure,
                    legacy_inventory,
                ),
                closure_receipt=closure,
                baseline_sources=sources,
                subject_inventory=legacy_inventory,
                check_receipts=check_receipts,
                history_event_ref="design-event:legacy-inventory-rejected",
            )

    def test_action_requires_active_hard_global_commitment(
        self,
    ) -> None:
        checkpoint, _ = _checkpoint()
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(checkpoint),
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        action = GroundedArchitectAction(
            action_id="missing-global-commitment",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="missing-global-commitment-op",
                discharge=False,
            ),
            responds_to_refs=("obligation:resolve-grid",),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale=(
                "The local obligation was cited but the global "
                "commitment was omitted."
            ),
        )

        with self.assertRaisesRegex(
            DesignControllerError,
            "governing global commitments",
        ):
            apply_architect_action(
                checkpoint,
                prepared,
                consultation,
                action,
                history_event_ref="design-event:missing-global",
            )

    def test_obligation_driven_expert_order_and_grounded_action(
        self,
    ) -> None:
        checkpoint, nodes = _checkpoint()
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(checkpoint),
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (
                "expert-structure-b",
                "expert-structure-a",
            ),
        )
        action = GroundedArchitectAction(
            action_id="architect-grid-001",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="set-grid-spacing",
                discharge=True,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=consultation.selected_expert_ids,
            adopted_advice_refs=(consultation.advice_refs[0],),
            rejected_advice_refs=(consultation.advice_refs[1],),
            tradeoff_rationale=(
                "Adopted one bounded option and rejected the other "
                "against the current obligation."
            ),
        )

        result = apply_architect_action(
            checkpoint,
            prepared,
            consultation,
            action,
            history_event_ref="design-event:grid-transition",
        )

        self.assertEqual(
            result.receipt.outcome,
            ControllerOutcome.TRANSITIONED,
        )
        self.assertEqual(
            result.receipt.responds_to_refs,
            (
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
        )
        self.assertEqual(
            result.receipt.invalidated_node_refs,
            (nodes["stair"].ref,),
        )
        self.assertEqual(
            result.receipt.revalidation_node_refs,
            (nodes["facade"].ref,),
        )
        self.assertEqual(
            result.checkpoint.status,
            ControllerStatus.READY,
        )

    def test_reopened_refs_accumulate_and_close_only_named_nodes(
        self,
    ) -> None:
        checkpoint, nodes = _checkpoint()
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(checkpoint),
        )
        consultation = consult_selected_experts(prepared, registry, ())
        first_action = GroundedArchitectAction(
            action_id="reopen-grid-interfaces",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="reopen-grid-interfaces-op",
                discharge=True,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="Resolve the grid and retain interface scope.",
        )
        first = apply_architect_action(
            checkpoint,
            prepared,
            consultation,
            first_action,
            history_event_ref="design-event:first-interface-reopen",
        )
        expected = tuple(
            sorted((nodes["stair"].ref, nodes["facade"].ref))
        )
        self.assertEqual(first.checkpoint.reopened_node_refs, expected)

        prepared_again = prepare_design_turn(
            first.checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={},
            stage_subject_inventory=_turn_subject_inventory(
                first.checkpoint
            ),
        )
        consultation_again = consult_selected_experts(
            prepared_again,
            registry,
            (),
        )
        target_state = first.checkpoint.tree.node(
            first.checkpoint.target_node_ref
        ).operational_state
        unrelated_action = GroundedArchitectAction(
            action_id="unrelated-grid-note",
            checkpoint_digest=first.checkpoint.checkpoint_digest,
            context_digest=prepared_again.context_digest,
            operator=DecisionOperator(
                decision_id="unrelated-grid-note-op",
                decision_type="parameter-derivation",
                base_state_digest=target_state.state_digest,
                authority_id="structure-agent",
                intent="Record an unrelated local grid note.",
                add_facts=(
                    StateFact(
                        domain=StateDomain.PARAMETER,
                        key="grid-depth-note",
                        value="retained",
                        source_ref="evidence://analysis/grid-depth-note",
                    ),
                ),
                evidence_refs=("evidence://analysis/grid-depth-note",),
            ),
            responds_to_refs=(_GLOBAL_COMMITMENT_REF,),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="This local note changes no named interface.",
        )
        second = apply_architect_action(
            first.checkpoint,
            prepared_again,
            consultation_again,
            unrelated_action,
            history_event_ref="design-event:unrelated-after-reopen",
        )
        self.assertEqual(second.checkpoint.reopened_node_refs, expected)
        self.assertEqual(second.receipt.invalidated_node_refs, ())
        self.assertEqual(second.receipt.revalidation_node_refs, ())

        repair_receipt = _stage_convergence_receipt(
            second.checkpoint,
            outcome=StageConvergenceOutcome.REPAIR,
            transition_kind=StageTransitionKind.REPAIR,
            potential_before=_convergence_potential(
                invalidated_refs=expected
            ),
            potential_after=_convergence_potential(
                invalidated_refs=(nodes["facade"].ref,)
            ),
        )
        unrelated_repair = _stage_convergence_receipt(
            second.checkpoint,
            outcome=StageConvergenceOutcome.REPAIR,
            transition_kind=StageTransitionKind.REPAIR,
            potential_before=_convergence_potential(
                invalidated_refs=(nodes["facade"].ref,)
            ),
            potential_after=_convergence_potential(),
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "does not prove the named reopened refs closed",
        ):
            close_reopened_nodes(
                second.checkpoint,
                unrelated_repair,
                resolved_node_refs=(nodes["stair"].ref,),
                history_event_ref="design-event:unrelated-repair-close",
            )
        closed = close_reopened_nodes(
            second.checkpoint,
            repair_receipt,
            resolved_node_refs=(nodes["stair"].ref,),
            history_event_ref="design-event:stair-repair-closed",
        )
        self.assertIs(
            closed.receipt.outcome,
            ControllerOutcome.REOPENED_REFS_CLOSED,
        )
        self.assertEqual(
            closed.checkpoint.reopened_node_refs,
            (nodes["facade"].ref,),
        )
        self.assertIs(closed.stage_convergence, repair_receipt)
        with self.assertRaisesRegex(
            DesignControllerError,
            "outside the current reopened set",
        ):
            close_reopened_nodes(
                closed.checkpoint,
                repair_receipt,
                resolved_node_refs=(nodes["envelope"].ref,),
                history_event_ref="design-event:invalid-close",
            )

    def test_action_must_account_for_advice_and_current_work(
        self,
    ) -> None:
        checkpoint, _ = _checkpoint()
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(checkpoint),
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            ("expert-structure-a",),
        )
        unaccounted = GroundedArchitectAction(
            action_id="unaccounted",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="unaccounted-op",
                discharge=True,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=consultation.selected_expert_ids,
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="No advice disposition was recorded.",
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "adopt or reject every advice",
        ):
            apply_architect_action(
                checkpoint,
                prepared,
                consultation,
                unaccounted,
                history_event_ref="design-event:unaccounted",
            )

        unknown = replace(
            unaccounted,
            adopted_advice_refs=consultation.advice_refs,
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:invented",
            ),
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "unavailable refs",
        ):
            apply_architect_action(
                checkpoint,
                prepared,
                consultation,
                unknown,
                history_event_ref="design-event:unknown-work",
            )

    def test_repeated_substantive_plan_stops_before_second_compile(
        self,
    ) -> None:
        checkpoint, _ = _checkpoint()
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(checkpoint),
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        first_action = GroundedArchitectAction(
            action_id="repeat-001",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="repeat-op-001",
                discharge=False,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="No optional expert was selected.",
        )
        first = apply_architect_action(
            checkpoint,
            prepared,
            consultation,
            first_action,
            history_event_ref="design-event:repeat-first",
        )
        prepared_again = prepare_design_turn(
            first.checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(
                first.checkpoint
            ),
        )
        consultation_again = consult_selected_experts(
            prepared_again,
            registry,
            (),
        )
        repeated = GroundedArchitectAction(
            action_id="repeat-002",
            checkpoint_digest=first.checkpoint.checkpoint_digest,
            context_digest=prepared_again.context_digest,
            operator=_operator(
                first.checkpoint,
                decision_id="repeat-op-002",
                discharge=False,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="The same substantive plan was repeated.",
        )

        stopped = apply_architect_action(
            first.checkpoint,
            prepared_again,
            consultation_again,
            repeated,
            history_event_ref="design-event:repeat-second",
        )

        self.assertEqual(
            stopped.receipt.outcome,
            ControllerOutcome.STOPPED_REPEATED_ACTION,
        )
        self.assertEqual(
            stopped.checkpoint.status,
            ControllerStatus.STOPPED,
        )
        self.assertIsNone(stopped.transition)

    def test_stale_base_and_no_effect_are_reloadable_stops(
        self,
    ) -> None:
        for label, base_digest, expected in (
            (
                "stale",
                _hash("stale-operator-base"),
                ControllerOutcome.STOPPED_STALE_BASE,
            ),
            (
                "no-effect",
                None,
                ControllerOutcome.STOPPED_NO_PROGRESS,
            ),
        ):
            with self.subTest(label=label):
                checkpoint, _ = _checkpoint()
                registry, metadata = _experts()
                prepared = prepare_design_turn(
                    checkpoint,
                    registry,
                    phase_metadata=metadata,
                    obligation_topics={
                        "resolve-grid": "structure"
                    },
                    stage_subject_inventory=_turn_subject_inventory(
                        checkpoint
                    ),
                )
                consultation = consult_selected_experts(
                    prepared,
                    registry,
                    (),
                )
                state = checkpoint.tree.node(
                    checkpoint.target_node_ref
                ).operational_state
                action = GroundedArchitectAction(
                    action_id=f"{label}-action",
                    checkpoint_digest=(
                        checkpoint.checkpoint_digest
                    ),
                    context_digest=prepared.context_digest,
                    operator=DecisionOperator(
                        decision_id=f"{label}-operator",
                        decision_type="bounded-stop-test",
                        base_state_digest=(
                            state.state_digest
                            if base_digest is None
                            else base_digest
                        ),
                        authority_id="structure-agent",
                        intent="Exercise a typed controller stop.",
                    ),
                    responds_to_refs=(
                        _GLOBAL_COMMITMENT_REF,
                        "obligation:resolve-grid",
                    ),
                    selected_expert_ids=(),
                    adopted_advice_refs=(),
                    rejected_advice_refs=(),
                    tradeoff_rationale=(
                        "No optional expert was selected."
                    ),
                )

                stopped = apply_architect_action(
                    checkpoint,
                    prepared,
                    consultation,
                    action,
                    history_event_ref=(
                        f"design-event:{label}-stopped"
                    ),
                )
                reloaded = DesignControllerCheckpoint.from_dict(
                    stopped.checkpoint.to_dict()
                )

                self.assertIs(stopped.receipt.outcome, expected)
                self.assertIs(
                    reloaded.status,
                    ControllerStatus.STOPPED,
                )

    def test_iteration_budget_is_a_reloadable_stop(self) -> None:
        checkpoint, _ = _checkpoint(max_iterations=1)
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={"resolve-grid": "structure"},
            stage_subject_inventory=_turn_subject_inventory(checkpoint),
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        action = GroundedArchitectAction(
            action_id="budget-last",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context_digest,
            operator=_operator(
                checkpoint,
                decision_id="budget-last-op",
                discharge=True,
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                "obligation:resolve-grid",
            ),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale="No optional expert was selected.",
        )

        result = apply_architect_action(
            checkpoint,
            prepared,
            consultation,
            action,
            history_event_ref="design-event:budget-last",
        )
        reloaded = DesignControllerCheckpoint.from_dict(
            result.checkpoint.to_dict()
        )

        self.assertEqual(
            result.receipt.outcome,
            ControllerOutcome.STOPPED_BUDGET,
        )
        self.assertEqual(reloaded, result.checkpoint)
        self.assertEqual(reloaded.status, ControllerStatus.STOPPED)

    def test_authority_pause_round_trips_and_resumes_exact_base(
        self,
    ) -> None:
        checkpoint, nodes = _checkpoint()
        target_state = nodes["grid"].operational_state
        alternative = ClarificationAlternative(
            alternative_id="grid-range-a",
            label="Authorize the evidenced grid range",
            effect=ClarificationEffect(
                fact_updates=(
                    ClarifiedFactValue(
                        domain=StateDomain.PARAMETER,
                        key="grid-spacing",
                        value={"range": [6, 8], "unit": "m"},
                    ),
                ),
            ),
        )
        request = create_clarification_request(
            target_state,
            obligation_id="resolve-grid",
            requesting_agent_id="architect",
            authority_ids=("authority-user",),
            question="May the evidenced grid range become design state?",
            blocked_reason="The Architect lacks authority to lock it.",
            alternatives=(alternative,),
            created_at_utc="2026-07-25T10:00:00Z",
            expires_at_utc="2026-07-26T10:00:00Z",
        )

        paused = pause_for_clarification(
            checkpoint,
            request,
            history_event_ref="design-event:clarification-requested",
        )
        reloaded_pause = DesignControllerCheckpoint.from_dict(
            paused.checkpoint.to_dict()
        )
        receipt = issue_authority_decision(
            request,
            authority_id="authority-user",
            disposition=ClarificationDisposition.SELECTED,
            selected_alternative_id=alternative.alternative_id,
            authority_event_ref="event://authority/grid-range",
            issued_at_utc="2026-07-25T10:05:00Z",
            valid_until_utc="2026-07-25T11:00:00Z",
        )
        resumed = resume_authority_pause(
            reloaded_pause,
            now_utc="2026-07-25T10:10:00Z",
            history_event_ref="design-event:clarification-resumed",
            receipt=receipt,
        )

        self.assertEqual(
            paused.receipt.outcome,
            ControllerOutcome.PAUSED_AUTHORITY,
        )
        self.assertEqual(
            resumed.receipt.outcome,
            ControllerOutcome.RESUMED_AUTHORITY,
        )
        self.assertEqual(
            resumed.checkpoint.status,
            ControllerStatus.READY,
        )
        resumed_state = resumed.checkpoint.tree.node(
            nodes["grid"].ref
        ).operational_state
        self.assertEqual(
            resumed_state.value_for_ref(
                "fact:parameter:grid-spacing"
            ),
            {"range": [6, 8], "unit": "m"},
        )
        self.assertIs(
            resumed_state.obligations[0].status,
            ObligationStatus.SATISFIED,
        )
        self.assertEqual(
            resumed.checkpoint.decision_context_refs,
            (receipt.ref,),
        )
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            resumed.checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={},
            stage_subject_inventory=_turn_subject_inventory(
                resumed.checkpoint
            ),
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        action = GroundedArchitectAction(
            action_id="use-authority-receipt",
            checkpoint_digest=(
                resumed.checkpoint.checkpoint_digest
            ),
            context_digest=prepared.context_digest,
            operator=DecisionOperator(
                decision_id="detail-after-authority",
                decision_type="parameter-derivation",
                base_state_digest=resumed_state.state_digest,
                authority_id="structure-agent",
                intent="Continue from the explicit authority response.",
                add_facts=(
                    StateFact(
                        domain=StateDomain.PARAMETER,
                        key="grid-detail",
                        value={"status": "coordinating"},
                        source_ref=receipt.ref,
                    ),
                ),
                evidence_refs=(receipt.ref,),
            ),
            responds_to_refs=(
                _GLOBAL_COMMITMENT_REF,
                receipt.ref,
            ),
            selected_expert_ids=(),
            adopted_advice_refs=(),
            rejected_advice_refs=(),
            tradeoff_rationale=(
                "The named authority response resolved the blocking choice."
            ),
        )
        continued = apply_architect_action(
            resumed.checkpoint,
            prepared,
            consultation,
            action,
            history_event_ref="design-event:post-authority-action",
        )
        self.assertIs(
            continued.receipt.outcome,
            ControllerOutcome.TRANSITIONED,
        )

    def test_phase_advance_requires_closed_work_and_changes_expert_pool(
        self,
    ) -> None:
        blocked, _ = _checkpoint()
        phase_ready = _phase_ready_checkpoint()
        blocked_closure = _stage_closure_receipt(phase_ready)
        (
            blocked_profile,
            blocked_sources,
            blocked_receipts,
            blocked_inventory,
        ) = (
            _stage_inputs_for_closure(phase_ready, blocked_closure)
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "active obligations",
        ):
            advance_design_phase(
                blocked,
                evaluate_forward_phase_gate(
                    phase_ready.maturity,
                    PhaseGateRequest(
                        request_id="temporary-gate",
                        branch=phase_ready.maturity.branch,
                        base_state_digest=(
                            phase_ready.maturity.operational_state_digest
                        ),
                        from_phase=DesignPhase.SCHEMATIC_DESIGN,
                        to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                        deliverable_refs=(
                            phase_ready.maturity.deliverable_refs
                        ),
                    ),
                ),
                convergence_receipt=_stage_convergence_receipt(blocked),
                requirement_profile=blocked_profile,
                profile_binding=_stage_profile_binding(
                    blocked_closure,
                    blocked_inventory,
                ),
                closure_receipt=blocked_closure,
                baseline_sources=blocked_sources,
                subject_inventory=blocked_inventory,
                check_receipts=blocked_receipts,
                history_event_ref="design-event:blocked-advance",
            )

        checkpoint = _phase_ready_checkpoint()
        request = PhaseGateRequest(
            request_id="advance-design-development",
            branch=checkpoint.maturity.branch,
            base_state_digest=(
                checkpoint.maturity.operational_state_digest
            ),
            from_phase=DesignPhase.SCHEMATIC_DESIGN,
            to_phase=DesignPhase.DESIGN_DEVELOPMENT,
            deliverable_refs=checkpoint.maturity.deliverable_refs,
        )
        convergence_receipt = _stage_convergence_receipt(checkpoint)
        closure_receipt = _stage_closure_receipt(checkpoint)
        (
            stage_profile,
            stage_sources,
            stage_receipts,
            stage_inventory,
        ) = (
            _stage_inputs_for_closure(checkpoint, closure_receipt)
        )
        profile_binding = _stage_profile_binding(
            closure_receipt,
            stage_inventory,
        )
        phase_gate = evaluate_forward_phase_gate(
            checkpoint.maturity,
            request,
        )
        result = advance_design_phase(
            checkpoint,
            phase_gate,
            convergence_receipt=convergence_receipt,
            requirement_profile=stage_profile,
            profile_binding=profile_binding,
            closure_receipt=closure_receipt,
            baseline_sources=stage_sources,
            subject_inventory=stage_inventory,
            check_receipts=stage_receipts,
            history_event_ref="design-event:phase-advanced",
        )

        self.assertEqual(
            result.receipt.outcome,
            ControllerOutcome.PHASE_ADVANCED,
        )
        self.assertIs(
            result.checkpoint.maturity.phase,
            DesignPhase.DESIGN_DEVELOPMENT,
        )
        self.assertIs(result.stage_convergence, convergence_receipt)
        self.assertIs(result.stage_closure, closure_receipt)
        self.assertIs(result.stage_profile_binding, profile_binding)
        self.assertIsNotNone(result.stage_baseline_coverage)
        self.assertEqual(
            profile_binding.binding_digest,
            result.receipt.stage_profile_binding_digest,
        )
        self.assertEqual(
            result.stage_baseline_coverage.receipt_digest,
            result.receipt.stage_baseline_coverage_digest,
        )
        alternate_closure = _stage_closure_receipt(
            checkpoint,
            stage_subject_ref=checkpoint.maturity.deliverables[1].ref,
        )
        (
            alternate_profile,
            alternate_sources,
            alternate_receipts,
            alternate_inventory,
        ) = (
            _stage_inputs_for_closure(checkpoint, alternate_closure)
        )
        alternate = advance_design_phase(
            checkpoint,
            phase_gate,
            convergence_receipt=convergence_receipt,
            requirement_profile=alternate_profile,
            profile_binding=_stage_profile_binding(
                alternate_closure,
                alternate_inventory,
            ),
            closure_receipt=alternate_closure,
            baseline_sources=alternate_sources,
            subject_inventory=alternate_inventory,
            check_receipts=alternate_receipts,
            history_event_ref="design-event:phase-advanced",
        )
        self.assertIs(alternate.stage_closure, alternate_closure)
        self.assertNotEqual(
            result.receipt.receipt_id,
            alternate.receipt.receipt_id,
        )
        self.assertTrue(
            all(
                node.operational_state.phase
                == DesignPhase.DESIGN_DEVELOPMENT.value
                for node in result.checkpoint.tree.nodes
            )
        )
        self.assertEqual(
            ContextSliceCompiler().compile(
                checkpoint.tree,
                target_node_ref=checkpoint.target_node_ref,
            ).phase_deliverable_refs,
            checkpoint.maturity.deliverable_refs,
        )
        self.assertEqual(
            ContextSliceCompiler().compile(
                result.checkpoint.tree,
                target_node_ref=result.checkpoint.target_node_ref,
            ).phase_deliverable_refs,
            (),
        )
        registry, metadata = _experts()
        prepared = prepare_design_turn(
            result.checkpoint,
            registry,
            phase_metadata=metadata,
            obligation_topics={},
            stage_subject_inventory=_turn_subject_inventory(
                result.checkpoint
            ),
        )
        self.assertEqual(prepared.discovered_expert_ids, ())

    def test_phase_advance_requires_component_proposal_anchor(self) -> None:
        checkpoint = _phase_ready_checkpoint()
        subject = checkpoint.maturity.deliverables[0]
        proposal_ref = _stage_component_proposal_ref(
            checkpoint.maturity.branch,
            checkpoint.maturity.phase.value,
            subject.base_state_digest,
        )
        unanchored_subject = replace(
            subject,
            evidence_refs=tuple(
                ref
                for ref in subject.evidence_refs
                if ref != proposal_ref.uri
            ),
        )
        unanchored = replace(
            checkpoint,
            maturity=replace(
                checkpoint.maturity,
                deliverables=(
                    unanchored_subject,
                    *checkpoint.maturity.deliverables[1:],
                ),
            ),
        )
        closure = _stage_closure_receipt(unanchored)
        profile, sources, check_receipts, subject_inventory = (
            _stage_inputs_for_closure(unanchored, closure)
        )
        self.assertEqual(
            subject_inventory.component_proposal_ref,
            proposal_ref,
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "does not retain the exact component proposal",
        ):
            advance_design_phase(
                unanchored,
                evaluate_forward_phase_gate(
                    unanchored.maturity,
                    PhaseGateRequest(
                        request_id="unanchored-component-proposal",
                        branch=unanchored.maturity.branch,
                        base_state_digest=(
                            unanchored.maturity.operational_state_digest
                        ),
                        from_phase=DesignPhase.SCHEMATIC_DESIGN,
                        to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                        deliverable_refs=unanchored.maturity.deliverable_refs,
                    ),
                ),
                convergence_receipt=_stage_convergence_receipt(unanchored),
                requirement_profile=profile,
                profile_binding=_stage_profile_binding(
                    closure,
                    subject_inventory,
                ),
                closure_receipt=closure,
                baseline_sources=sources,
                subject_inventory=subject_inventory,
                check_receipts=check_receipts,
                history_event_ref="design-event:unanchored-proposal-rejected",
            )

    def test_phase_advance_rejects_reopen_stale_cross_and_nonzero(
        self,
    ) -> None:
        checkpoint = _phase_ready_checkpoint()
        request = PhaseGateRequest(
            request_id="strict-stage-convergence",
            branch=checkpoint.maturity.branch,
            base_state_digest=checkpoint.maturity.operational_state_digest,
            from_phase=DesignPhase.SCHEMATIC_DESIGN,
            to_phase=DesignPhase.DESIGN_DEVELOPMENT,
            deliverable_refs=checkpoint.maturity.deliverable_refs,
        )
        phase_gate = evaluate_forward_phase_gate(
            checkpoint.maturity,
            request,
        )
        valid = _stage_convergence_receipt(checkpoint)
        reopened = replace(
            checkpoint,
            reopened_node_refs=(checkpoint.tree.root.ref,),
        )
        with self.assertRaisesRegex(DesignControllerError, "reopened nodes"):
            reopened_closure = _stage_closure_receipt(reopened)
            (
                reopened_profile,
                reopened_sources,
                reopened_receipts,
                reopened_inventory,
            ) = (
                _stage_inputs_for_closure(reopened, reopened_closure)
            )
            advance_design_phase(
                reopened,
                phase_gate,
                convergence_receipt=_stage_convergence_receipt(reopened),
                requirement_profile=reopened_profile,
                profile_binding=_stage_profile_binding(
                    reopened_closure,
                    reopened_inventory,
                ),
                closure_receipt=reopened_closure,
                baseline_sources=reopened_sources,
                subject_inventory=reopened_inventory,
                check_receipts=reopened_receipts,
                history_event_ref="design-event:reopened-rejected",
            )

        invalid_receipts = (
            (
                replace(valid, child_state_digest=_hash("stale-child")),
                "stale against current state",
            ),
            (
                replace(
                    valid,
                    branch=replace(valid.branch, branch_id="option-b"),
                ),
                "cross-branch or stale",
            ),
            (
                replace(
                    valid,
                    potential_after=_convergence_potential(
                        open_refs=("obligation:still-open",)
                    ),
                ),
                "potential is not closed",
            ),
            (
                _stage_convergence_receipt(
                    checkpoint,
                    outcome=StageConvergenceOutcome.REJECTED,
                ),
                "receipt was rejected",
            ),
        )
        for receipt, message in invalid_receipts:
            with self.subTest(message=message):
                closure = _stage_closure_receipt(checkpoint)
                (
                    stage_profile,
                    stage_sources,
                    stage_receipts,
                    stage_inventory,
                ) = (
                    _stage_inputs_for_closure(checkpoint, closure)
                )
                with self.assertRaisesRegex(DesignControllerError, message):
                    advance_design_phase(
                        checkpoint,
                        phase_gate,
                        convergence_receipt=receipt,
                        requirement_profile=stage_profile,
                        profile_binding=_stage_profile_binding(
                            closure,
                            stage_inventory,
                        ),
                        closure_receipt=closure,
                        baseline_sources=stage_sources,
                        subject_inventory=stage_inventory,
                        check_receipts=stage_receipts,
                        history_event_ref="design-event:convergence-rejected",
                    )

    def test_phase_advance_requires_exact_composite_stage_closure(
        self,
    ) -> None:
        checkpoint = _phase_ready_checkpoint()
        phase_gate = evaluate_forward_phase_gate(
            checkpoint.maturity,
            PhaseGateRequest(
                request_id="strict-composite-stage-closure",
                branch=checkpoint.maturity.branch,
                base_state_digest=(
                    checkpoint.maturity.operational_state_digest
                ),
                from_phase=DesignPhase.SCHEMATIC_DESIGN,
                to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                deliverable_refs=checkpoint.maturity.deliverable_refs,
            ),
        )
        convergence_receipt = _stage_convergence_receipt(checkpoint)
        valid = _stage_closure_receipt(checkpoint)
        (
            valid_profile,
            valid_sources,
            valid_receipts,
            valid_inventory,
        ) = (
            _stage_inputs_for_closure(checkpoint, valid)
        )
        valid_binding = _stage_profile_binding(valid, valid_inventory)

        stale_topology, _, _ = _stage_relation_topology_evidence(
            valid_inventory,
            state_digest=_hash("stale-relation-operational-state"),
            scope_digest=valid_profile.scope_digest,
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "relation baseline source crossed the exact profile",
        ):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=valid_profile,
                profile_binding=valid_binding,
                closure_receipt=valid,
                baseline_sources=replace(
                    valid_sources,
                    relation_topology=(stale_topology,),
                ),
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:stale-relation-topology",
            )

        topology_graph = valid_sources.relation_topology[0].promotion.graph
        alternate_graph = replace(
            topology_graph,
            graph_id="alternate-realization-graph",
        )
        program = relation_compiled_program()
        snapshot = replace(
            relation_readback(
                program,
                selected_branch=alternate_graph.branch,
            ),
            stage_id=alternate_graph.stage_id,
        )
        alternate_realization = RelationRealizationBaselineSource(
            graph=alternate_graph,
            manifest=relation_manifest(
                alternate_graph,
                program,
                snapshot,
                (),
                pairings=(),
            ),
            program=program,
            readback=snapshot,
        )
        with self.assertRaisesRegex(
            ValueError,
            "topology and realization must bind the same exact graph set",
        ):
            derive_stage_requirement_profile(
                valid_profile,
                level=StageBaselineLevel.SPATIAL,
                sources=replace(
                    valid_sources,
                    relation_realization=(alternate_realization,),
                ),
                subject_digest=valid.subject_digest,
                subject_inventory=valid_inventory,
            )

        with self.assertRaisesRegex(TypeError, "requirement_profile"):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                profile_binding=valid_binding,
                closure_receipt=valid,
                baseline_sources=valid_sources,
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:missing-requirement-profile",
            )

        with self.assertRaisesRegex(TypeError, "closure_receipt"):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=valid_profile,
                profile_binding=valid_binding,
                baseline_sources=valid_sources,
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:missing-closure",
            )
        with self.assertRaisesRegex(
            TypeError,
            "CompositeStageClosureReceipt",
        ):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=valid_profile,
                profile_binding=valid_binding,
                closure_receipt=True,
                baseline_sources=valid_sources,
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:boolean-closure",
            )
        with self.assertRaisesRegex(TypeError, "profile_binding"):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=valid_profile,
                closure_receipt=valid,
                baseline_sources=valid_sources,
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:missing-profile-binding",
            )
        with self.assertRaisesRegex(
            TypeError,
            "StageRequirementProfileBinding",
        ):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=valid_profile,
                profile_binding=True,
                closure_receipt=valid,
                baseline_sources=valid_sources,
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:boolean-profile-binding",
            )
        substituted_closure = replace(
            valid,
            profile_id="caller-minimal-profile",
            profile_digest=_hash("caller-minimal-profile"),
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "authorized profile",
        ):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=valid_profile,
                profile_binding=_stage_profile_binding(
                    substituted_closure,
                    valid_inventory,
                ),
                closure_receipt=valid,
                baseline_sources=valid_sources,
                subject_inventory=valid_inventory,
                check_receipts=valid_receipts,
                history_event_ref="design-event:profile-substitution",
            )

        subject = checkpoint.maturity.deliverables[0]
        caller_minimal_profile = StageRequirementProfile(
            profile_id="caller-minimal-profile",
            typology_id="synthetic-controller-fixture",
            stage_id=checkpoint.maturity.phase.value,
            branch=checkpoint.maturity.branch,
            predecessor_state_digest=(
                checkpoint.maturity.operational_state_digest
            ),
            scope_digest=_hash("caller-minimal-profile-scope"),
            stage_subject_ref=subject.ref,
            requirements=(
                StageCheckRequirement(
                    requirement_id="caller-selected-only-check",
                    checker_id="caller-summary-checker",
                    target_kind=RequirementTargetKind.ARTIFACT,
                    basis_mode=RequirementBasisMode.UNIVERSAL,
                    denominator_refs=(subject.ref,),
                ),
            ),
        )
        caller_minimal_requirement = caller_minimal_profile.requirements[0]
        caller_minimal_receipt = CheckReceiptEnvelope(
            check_id=caller_minimal_requirement.requirement_id,
            checker_id=caller_minimal_requirement.checker_id,
            checker_version="1.0.0",
            branch=caller_minimal_profile.branch,
            scope_digest=caller_minimal_profile.scope_digest,
            subject_refs=caller_minimal_requirement.denominator_refs,
            subject_digest=subject.base_state_digest,
            status=CheckStatus.PASS,
            coverage_denominator=caller_minimal_requirement.denominator_refs,
            covered_refs=caller_minimal_requirement.denominator_refs,
        )
        caller_minimal_closure = compile_composite_stage_closure(
            caller_minimal_profile,
            subject_digest=subject.base_state_digest,
            check_receipts=(caller_minimal_receipt,),
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "omits framework baseline roles",
        ):
            advance_design_phase(
                checkpoint,
                phase_gate,
                convergence_receipt=convergence_receipt,
                requirement_profile=caller_minimal_profile,
                profile_binding=_stage_profile_binding(
                    caller_minimal_closure,
                    valid_inventory,
                ),
                closure_receipt=caller_minimal_closure,
                baseline_sources=StageBaselineSourceSet(),
                subject_inventory=valid_inventory,
                check_receipts=(caller_minimal_receipt,),
                history_event_ref="design-event:minimal-profile-rejected",
            )

        open_receipt = _stage_closure_receipt(
            checkpoint,
            findings=(
                StageClosureFinding(
                    code=StageClosureFindingCode.CHECK_FAILED,
                    requirement_id="structure-check",
                ),
            ),
        )
        invalid_receipts = (
            (open_receipt, "not satisfied"),
            (
                replace(
                    valid,
                    branch=replace(valid.branch, branch_id="option-b"),
                ),
                "cross-branch or stale",
            ),
            (
                replace(valid, stage_id="design_development"),
                "current phase",
            ),
            (
                replace(valid, subject_digest=_hash("stale-subject")),
                "authorized profile|subject digest is stale",
            ),
            (
                _stage_closure_receipt(
                    checkpoint,
                    stage_subject_ref="deliverable:unknown-subject",
                ),
                "not a known maturity deliverable",
            ),
        )
        for closure_receipt, message in invalid_receipts:
            with self.subTest(message=message):
                (
                    invalid_profile,
                    invalid_sources,
                    invalid_check_receipts,
                    invalid_inventory,
                ) = _stage_inputs_for_closure(
                    checkpoint,
                    closure_receipt,
                )
                with self.assertRaisesRegex(DesignControllerError, message):
                    advance_design_phase(
                        checkpoint,
                        phase_gate,
                        convergence_receipt=convergence_receipt,
                        requirement_profile=invalid_profile,
                        profile_binding=_stage_profile_binding(
                            closure_receipt,
                            invalid_inventory,
                        ),
                        closure_receipt=closure_receipt,
                        baseline_sources=invalid_sources,
                        subject_inventory=invalid_inventory,
                        check_receipts=invalid_check_receipts,
                        history_event_ref=(
                            "design-event:invalid-composite-closure"
                        ),
                    )

        stale_deliverable = replace(
            checkpoint.maturity.deliverables[0],
            base_state_digest=_hash("stale-deliverable-base"),
        )
        with_stale_deliverable = replace(
            checkpoint,
            maturity=replace(
                checkpoint.maturity,
                deliverables=(
                    stale_deliverable,
                    *checkpoint.maturity.deliverables[1:],
                ),
            ),
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "subject digest is stale",
        ):
            stale_closure = _stage_closure_receipt(
                with_stale_deliverable
            )
            (
                stale_profile,
                stale_sources,
                stale_check_receipts,
                stale_inventory,
            ) = _stage_inputs_for_closure(
                with_stale_deliverable,
                stale_closure,
            )
            advance_design_phase(
                with_stale_deliverable,
                phase_gate,
                convergence_receipt=_stage_convergence_receipt(
                    with_stale_deliverable
                ),
                requirement_profile=stale_profile,
                profile_binding=_stage_profile_binding(
                    stale_closure,
                    stale_inventory,
                ),
                closure_receipt=stale_closure,
                baseline_sources=stale_sources,
                subject_inventory=stale_inventory,
                check_receipts=stale_check_receipts,
                history_event_ref="design-event:stale-deliverable-closure",
            )

        unaccepted = PhaseDeliverable(
            deliverable_id="prior-site-context",
            role=DeliverableRole.SITE_CONTEXT,
            produced_phase=DesignPhase.SITE_RESOURCE_COORDINATION,
            branch=checkpoint.maturity.branch,
            base_state_digest=(
                checkpoint.maturity.operational_state_digest
            ),
            artifact_ref="artifact://prior/site-context",
            evidence_refs=(
                "evidence://prior/site-context",
                _stage_component_proposal_ref(
                    checkpoint.maturity.branch,
                    checkpoint.maturity.phase.value,
                    checkpoint.maturity.operational_state_digest,
                ).uri,
            ),
        )
        with_extra_deliverable = replace(
            checkpoint,
            maturity=replace(
                checkpoint.maturity,
                deliverables=(
                    *checkpoint.maturity.deliverables,
                    unaccepted,
                ),
            ),
        )
        with self.assertRaisesRegex(
            DesignControllerError,
            "not accepted by the current phase gate",
        ):
            unaccepted_closure = _stage_closure_receipt(
                with_extra_deliverable,
                stage_subject_ref=unaccepted.ref,
                subject_digest=unaccepted.base_state_digest,
            )
            (
                unaccepted_profile,
                unaccepted_sources,
                unaccepted_check_receipts,
                unaccepted_inventory,
            ) = _stage_inputs_for_closure(
                with_extra_deliverable,
                unaccepted_closure,
            )
            advance_design_phase(
                with_extra_deliverable,
                phase_gate,
                convergence_receipt=_stage_convergence_receipt(
                    with_extra_deliverable
                ),
                requirement_profile=unaccepted_profile,
                profile_binding=_stage_profile_binding(
                    unaccepted_closure,
                    unaccepted_inventory,
                ),
                closure_receipt=unaccepted_closure,
                baseline_sources=unaccepted_sources,
                subject_inventory=unaccepted_inventory,
                check_receipts=unaccepted_check_receipts,
                history_event_ref="design-event:unaccepted-closure-subject",
            )

    def test_backward_revision_reopens_only_impacted_deliverable(
        self,
    ) -> None:
        checkpoint = _phase_ready_checkpoint()
        closure = _stage_closure_receipt(checkpoint)
        profile, sources, check_receipts, subject_inventory = (
            _stage_inputs_for_closure(
            checkpoint,
            closure,
            )
        )
        advanced = advance_design_phase(
            checkpoint,
            evaluate_forward_phase_gate(
                checkpoint.maturity,
                PhaseGateRequest(
                    request_id="advance-before-revision",
                    branch=checkpoint.maturity.branch,
                    base_state_digest=(
                        checkpoint.maturity.operational_state_digest
                    ),
                    from_phase=DesignPhase.SCHEMATIC_DESIGN,
                    to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                    deliverable_refs=(
                        checkpoint.maturity.deliverable_refs
                    ),
                ),
            ),
            convergence_receipt=_stage_convergence_receipt(checkpoint),
            requirement_profile=profile,
            profile_binding=_stage_profile_binding(
                closure,
                subject_inventory,
            ),
            closure_receipt=closure,
            baseline_sources=sources,
            subject_inventory=subject_inventory,
            check_receipts=check_receipts,
            history_event_ref="design-event:advance-before-revision",
        ).checkpoint
        changed_ref = advanced.maturity.deliverable_refs[0]
        revised = revise_design_phase(
            advanced,
            BackwardRevisionRequest(
                revision_id="revise-schematic-selection",
                branch=advanced.maturity.branch,
                base_state_digest=(
                    advanced.maturity.operational_state_digest
                ),
                from_phase=DesignPhase.DESIGN_DEVELOPMENT,
                to_phase=DesignPhase.SCHEMATIC_DESIGN,
                changed_refs=(changed_ref,),
            ),
            (),
            history_event_ref="design-event:phase-revised",
        )

        self.assertEqual(
            revised.receipt.outcome,
            ControllerOutcome.PHASE_REVISED,
        )
        self.assertEqual(
            revised.checkpoint.maturity.invalidated_refs,
            (changed_ref,),
        )
        target_state = revised.checkpoint.tree.node(
            revised.checkpoint.target_node_ref
        ).operational_state
        self.assertEqual(
            tuple(
                item.source_ref
                for item in target_state.obligations
                if item.status is ObligationStatus.OPEN
            ),
            (
                "phase-revision:revise-schematic-selection",
            ),
        )
        open_elsewhere = tuple(
            item
            for node in revised.checkpoint.tree.nodes
            if node.ref != revised.checkpoint.target_node_ref
            for item in node.operational_state.obligations
            if item.status is ObligationStatus.OPEN
        )
        self.assertEqual(open_elsewhere, ())

    def test_mid_run_requirement_is_event_backed_and_stays_proposed(
        self,
    ) -> None:
        checkpoint, event_chain, event = _event_backed_checkpoint()
        observation = IntentObservation(
            observation_id="mid-run-reading-area",
            raw_text="Increase the evidenced reading-area allowance.",
            source_event_ref=event.event_id,
            authority_id="authority-user",
            scope_ref="semantic://reading-area",
            interpretations=(
                IntentTerm(
                    parameter_key="reading_area.minimum",
                    operator=IntentOperator.MINIMUM,
                    value=240,
                    unit="square_metres",
                ),
            ),
            evidence_refs=("evidence://requirement/mid-run",),
        )

        result = compile_mid_run_requirement(
            checkpoint,
            event_chain=event_chain,
            observation=observation,
        )

        self.assertIs(
            result.status,
            MidRunRequirementStatus.PROPOSED,
        )
        target = result.checkpoint.tree.node(
            result.checkpoint.target_node_ref
        ).operational_state
        self.assertEqual(len(target.commitments), 1)
        self.assertIs(
            target.commitments[0].status,
            CommitmentStatus.PROPOSED,
        )
        self.assertTrue(
            any(
                item.status is ObligationStatus.OPEN
                and (
                    f"commitment:{target.commitments[0].commitment_id}"
                    in item.subject_refs
                )
                for item in target.obligations
            )
        )
        self.assertEqual(
            result.checkpoint.history_event_refs[-1],
            event.event_id,
        )

    def test_locked_mid_run_conflict_blocks_and_propagates_locally(
        self,
    ) -> None:
        checkpoint, event_chain, event = _event_backed_checkpoint()
        original = compile_intent(
            IntentObservation(
                observation_id="locked-area",
                raw_text="Keep the locked minimum area.",
                source_event_ref="event://requirement/original",
                authority_id="authority-user",
                scope_ref="semantic://reading-area",
                interpretations=(
                    IntentTerm(
                        parameter_key="reading_area.minimum",
                        operator=IntentOperator.MINIMUM,
                        value=300,
                        unit="square_metres",
                    ),
                ),
                evidence_refs=("evidence://requirement/original",),
            )
        ).proposals[0]
        locked = confirm_proposal(
            original,
            actor_authority_id="authority-user",
            confirmation_event_ref="event://authority/original",
            monitor_state_ref="monitor://reading-area",
        )
        target = checkpoint.tree.node(checkpoint.target_node_ref)
        target_with_lock = replace(
            target,
            operational_state=replace(
                target.operational_state,
                commitments=(locked.commitment,),
            ),
        )
        stair_ref = next(
            node.ref
            for node in checkpoint.tree.nodes
            if node.path.node_id == "stair"
        )
        tree = DesignStateTree(
            branch=checkpoint.tree.branch,
            nodes=tuple(
                target_with_lock
                if node.ref == target.ref
                else node
                for node in checkpoint.tree.nodes
            ),
            interfaces=(
                *checkpoint.tree.interfaces,
                InterfaceConstraint(
                    interface_id="requirement-to-stair",
                    source_node_ref=target.ref,
                    target_node_ref=stair_ref,
                    statement=(
                        "Reading-area changes require circulation review."
                    ),
                    source_refs=(
                        f"commitment:{locked.commitment.commitment_id}",
                    ),
                    target_refs=("fact:geometry:stair-core",),
                    effect=DependencyEffect.INVALIDATES,
                    evidence_refs=(
                        "evidence://interface/reading-circulation",
                    ),
                ),
            ),
        )
        checkpoint = replace(
            checkpoint,
            tree=tree,
            maturity=replace(
                checkpoint.maturity,
                operational_state_digest=(
                    target_with_lock.operational_state.state_digest
                ),
            ),
        )
        changed = IntentObservation(
            observation_id="changed-area",
            raw_text="Reduce the locked reading area.",
            source_event_ref=event.event_id,
            authority_id="authority-user",
            scope_ref="semantic://reading-area",
            interpretations=(
                IntentTerm(
                    parameter_key="reading_area.minimum",
                    operator=IntentOperator.MINIMUM,
                    value=200,
                    unit="square_metres",
                ),
            ),
            evidence_refs=("evidence://requirement/mid-run",),
        )

        result = compile_mid_run_requirement(
            checkpoint,
            event_chain=event_chain,
            observation=changed,
            locked=(locked,),
        )

        self.assertIs(
            result.status,
            MidRunRequirementStatus.REVISION_REQUIRED,
        )
        self.assertEqual(
            result.conflicting_commitment_id,
            locked.commitment.commitment_id,
        )
        self.assertIn(stair_ref, result.affected_node_refs)
        self.assertEqual(
            result.checkpoint.reopened_node_refs,
            (stair_ref,),
        )
        next_target = result.checkpoint.tree.node(
            result.checkpoint.target_node_ref
        ).operational_state
        self.assertEqual(
            next_target.commitments,
            (locked.commitment,),
        )
        self.assertTrue(
            any(
                item.status is ObligationStatus.BLOCKED
                and (
                    f"commitment:{locked.commitment.commitment_id}"
                    in item.subject_refs
                )
                for item in next_target.obligations
            )
        )


if __name__ == "__main__":
    unittest.main()
