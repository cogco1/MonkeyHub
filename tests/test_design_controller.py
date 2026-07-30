from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace

from archflow.capabilities.experts import (
    ExpertAdvice,
    ExpertRegistry,
    ExpertSpec,
)
from archflow.capabilities.phase_gates import PhaseExpertMetadata
from archflow.interaction import (
    ClarificationAlternative,
    ClarificationDisposition,
    ClarificationEffect,
    ClarifiedFactValue,
)
from archflow.project import BranchRef, ProjectVersionRef, RunRef
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


_GLOBAL_COMMITMENT_REF = "commitment:preserve-public-purpose"


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


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
    return tuple(
        PhaseDeliverable(
            deliverable_id=f"schematic-{role.value}",
            role=role,
            produced_phase=DesignPhase.SCHEMATIC_DESIGN,
            branch=checkpoint.tree.branch,
            base_state_digest=(
                checkpoint.tree.node(
                    checkpoint.target_node_ref
                ).operational_state.state_digest
            ),
            artifact_ref=f"artifact://schematic/{role.value}",
            evidence_refs=(f"evidence://schematic/{role.value}",),
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
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        action = GroundedArchitectAction(
            action_id="missing-global-commitment",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context.context_digest,
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
            context_digest=prepared.context.context_digest,
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
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            ("expert-structure-a",),
        )
        unaccounted = GroundedArchitectAction(
            action_id="unaccounted",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context.context_digest,
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
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        first_action = GroundedArchitectAction(
            action_id="repeat-001",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context.context_digest,
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
        )
        consultation_again = consult_selected_experts(
            prepared_again,
            registry,
            (),
        )
        repeated = GroundedArchitectAction(
            action_id="repeat-002",
            checkpoint_digest=first.checkpoint.checkpoint_digest,
            context_digest=prepared_again.context.context_digest,
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
                    context_digest=prepared.context.context_digest,
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
        )
        consultation = consult_selected_experts(
            prepared,
            registry,
            (),
        )
        action = GroundedArchitectAction(
            action_id="budget-last",
            checkpoint_digest=checkpoint.checkpoint_digest,
            context_digest=prepared.context.context_digest,
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
            context_digest=prepared.context.context_digest,
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
        with self.assertRaisesRegex(
            DesignControllerError,
            "active obligations",
        ):
            advance_design_phase(
                blocked,
                evaluate_forward_phase_gate(
                    _phase_ready_checkpoint().maturity,
                    PhaseGateRequest(
                        request_id="temporary-gate",
                        branch=_phase_ready_checkpoint().maturity.branch,
                        base_state_digest=(
                            _phase_ready_checkpoint()
                            .maturity.operational_state_digest
                        ),
                        from_phase=DesignPhase.SCHEMATIC_DESIGN,
                        to_phase=DesignPhase.DESIGN_DEVELOPMENT,
                        deliverable_refs=(
                            _phase_ready_checkpoint()
                            .maturity.deliverable_refs
                        ),
                    ),
                ),
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
        result = advance_design_phase(
            checkpoint,
            evaluate_forward_phase_gate(
                checkpoint.maturity,
                request,
            ),
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
        )
        self.assertEqual(prepared.discovered_expert_ids, ())

    def test_backward_revision_reopens_only_impacted_deliverable(
        self,
    ) -> None:
        checkpoint = _phase_ready_checkpoint()
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
