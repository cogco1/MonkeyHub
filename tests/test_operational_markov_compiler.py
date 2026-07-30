from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path

from archflow.project import BranchRef, ProjectVersionRef, RunRef
from archflow.state import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    ConditionComparator,
    CriterionRef,
    DecisionCompilationError,
    DecisionOperator,
    DecisionOperatorMigrationRequired,
    DependencyEffect,
    DependencyEdge,
    DesignObligation,
    FactEpistemicStatus,
    LegacyDecisionOperatorV1,
    LegacyOperationalMarkovStateV2,
    ObligationCondition,
    ObligationStatus,
    OperationalMarkovState,
    OperationalStateMigrationRequired,
    ParameterBinding,
    RevisionPolicy,
    StateCondition,
    StateDomain,
    StateFact,
    StateLock,
    compile_decision_operator,
    load_decision_operator_record,
    load_operational_state_record,
    transition_commitment,
)


def _branch(
    *,
    branch_id: str = "option-a",
    epoch: int = 0,
) -> BranchRef:
    base = ProjectVersionRef(
        project_id="test-project",
        version=0,
        state_sha256="a" * 64,
    )
    run = RunRef(
        project_id="test-project",
        run_id="run-001",
        base=base,
    )
    return BranchRef(run=run, branch_id=branch_id, epoch=epoch)


def _commitment() -> Commitment:
    return Commitment(
        commitment_id="commitment-access",
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.PROPOSED,
        authority_id="authority-user",
        authorized_by=None,
        source_event_ref="event://request/1",
        satisfaction_criterion=CriterionRef(
            criterion_id="criterion-access",
            provider_id="validator-access",
            subject_refs=("semantic://entry",),
        ),
        evidence_refs=("evidence://request",),
        scope_refs=("semantic://entry",),
    )


def _state(
    *,
    branch: BranchRef | None = None,
    facts: tuple[StateFact, ...] = (),
    bindings: tuple[ParameterBinding, ...] = (),
    locks: tuple[StateLock, ...] = (),
    commitments: tuple[Commitment, ...] = (),
    obligations: tuple[DesignObligation, ...] = (),
    dependencies: tuple[DependencyEdge, ...] = (),
) -> OperationalMarkovState:
    return OperationalMarkovState(
        branch=branch or _branch(),
        compiler_version="compiler-1",
        phase="schematic",
        facts=facts,
        bindings=bindings,
        locks=locks,
        commitments=commitments,
        obligations=obligations,
        dependencies=dependencies,
        evidence_refs=("evidence://request",),
    )


class OperationalMarkovCompilerTests(unittest.TestCase):
    def test_existing_commitment_accepts_only_valid_lifecycle_successor(self) -> None:
        proposed = _commitment()
        accepted = transition_commitment(
            proposed,
            CommitmentStatus.ACCEPTED,
            actor_authority_id="authority-user",
        )
        active = transition_commitment(
            accepted,
            CommitmentStatus.ACTIVE,
            actor_authority_id="authority-user",
            monitor_state_ref="monitor://commitment/access",
        )
        state = _state(commitments=(proposed,))
        authorized = compile_decision_operator(
            state,
            DecisionOperator(
                decision_id="authorize-access",
                decision_type="authority-commitment-transition",
                base_state_digest=state.state_digest,
                authority_id="authority-user",
                intent="Authorize the proposed access commitment.",
                spawn_commitments=(active,),
                evidence_refs=("receipt://authority/access",),
            ),
        )
        self.assertEqual(
            authorized.state.commitments[0].status,
            CommitmentStatus.ACTIVE,
        )

        released = transition_commitment(
            active,
            CommitmentStatus.RELEASED,
            actor_authority_id="authority-user",
        )
        released_result = compile_decision_operator(
            authorized.state,
            DecisionOperator(
                decision_id="release-access",
                decision_type="authority-commitment-transition",
                base_state_digest=authorized.state.state_digest,
                authority_id="authority-user",
                intent="Release the active access commitment.",
                spawn_commitments=(released,),
                evidence_refs=("receipt://authority/release",),
            ),
        )
        self.assertEqual(
            released_result.state.commitments[0].status,
            CommitmentStatus.RELEASED,
        )

    def test_existing_commitment_revision_preserves_lineage(self) -> None:
        proposed = _commitment()
        active = transition_commitment(
            transition_commitment(
                proposed,
                CommitmentStatus.ACCEPTED,
                actor_authority_id="authority-user",
            ),
            CommitmentStatus.ACTIVE,
            actor_authority_id="authority-user",
            monitor_state_ref="monitor://commitment/access",
        )
        replacement_proposed = replace(
            proposed,
            commitment_id="commitment-access-v2",
            predecessor_id=active.commitment_id,
            source_event_ref="event://clarification/revision",
        )
        replacement_active = transition_commitment(
            transition_commitment(
                replacement_proposed,
                CommitmentStatus.ACCEPTED,
                actor_authority_id="authority-user",
            ),
            CommitmentStatus.ACTIVE,
            actor_authority_id="authority-user",
            monitor_state_ref="monitor://commitment/access-v2",
        )
        revised = transition_commitment(
            active,
            CommitmentStatus.REVISED,
            actor_authority_id="authority-user",
            successor_id=replacement_active.commitment_id,
        )
        state = _state(commitments=(active,))
        result = compile_decision_operator(
            state,
            DecisionOperator(
                decision_id="revise-access",
                decision_type="authority-commitment-transition",
                base_state_digest=state.state_digest,
                authority_id="authority-user",
                intent="Revise the access commitment with explicit lineage.",
                spawn_commitments=(revised, replacement_active),
                evidence_refs=("receipt://authority/revision",),
            ),
        )
        commitments = {
            item.commitment_id: item for item in result.state.commitments
        }
        self.assertEqual(
            commitments[active.commitment_id].status,
            CommitmentStatus.REVISED,
        )
        self.assertEqual(
            commitments[active.commitment_id].successor_ids,
            (replacement_active.commitment_id,),
        )
        self.assertEqual(
            commitments[replacement_active.commitment_id].predecessor_id,
            active.commitment_id,
        )

    def test_commitment_transition_rejects_policy_and_arbitrary_replacement(
        self,
    ) -> None:
        immutable = replace(
            _commitment(),
            revision_policy=RevisionPolicy.IMMUTABLE,
        )
        state = _state(commitments=(immutable,))
        forged_release = replace(
            immutable,
            status=CommitmentStatus.RELEASED,
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "invalid commitment lifecycle transition",
        ):
            compile_decision_operator(
                state,
                DecisionOperator(
                    decision_id="forged-release",
                    decision_type="authority-commitment-transition",
                    base_state_digest=state.state_digest,
                    authority_id="authority-user",
                    intent="Attempt an immutable release.",
                    spawn_commitments=(forged_release,),
                ),
            )

        arbitrary = replace(
            immutable,
            source_event_ref="event://forged/replacement",
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "invalid commitment lifecycle transition",
        ):
            compile_decision_operator(
                state,
                DecisionOperator(
                    decision_id="arbitrary-replacement",
                    decision_type="authority-commitment-transition",
                    base_state_digest=state.state_digest,
                    authority_id="authority-user",
                    intent="Attempt an arbitrary same-id replacement.",
                    spawn_commitments=(arbitrary,),
                ),
            )

        named = replace(
            _commitment(),
            revision_policy=RevisionPolicy.NAMED_AUTHORITIES,
            permitted_authority_ids=("authority-reviewer",),
        )
        named_active = transition_commitment(
            transition_commitment(
                named,
                CommitmentStatus.ACCEPTED,
                actor_authority_id="authority-user",
            ),
            CommitmentStatus.ACTIVE,
            actor_authority_id="authority-user",
            monitor_state_ref="monitor://commitment/named",
        )
        named_release = transition_commitment(
            named_active,
            CommitmentStatus.RELEASED,
            actor_authority_id="authority-reviewer",
        )
        named_state = _state(commitments=(named_active,))
        named_result = compile_decision_operator(
            named_state,
            DecisionOperator(
                decision_id="named-release",
                decision_type="authority-commitment-transition",
                base_state_digest=named_state.state_digest,
                authority_id="authority-reviewer",
                intent="Use named revision authority.",
                spawn_commitments=(named_release,),
            ),
        )
        self.assertEqual(
            named_result.state.commitments[0].status,
            CommitmentStatus.RELEASED,
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "another authority",
        ):
            compile_decision_operator(
                named_state,
                DecisionOperator(
                    decision_id="unauthorized-release",
                    decision_type="authority-commitment-transition",
                    base_state_digest=named_state.state_digest,
                    authority_id="authority-intruder",
                    intent="Attempt another authority's transition.",
                    spawn_commitments=(named_release,),
                ),
            )

    def test_typed_operator_applies_full_delta_on_exact_base(self) -> None:
        brief = StateFact(
            domain=StateDomain.BRIEF,
            key="request-status",
            value="compiled",
            source_ref="evidence://brief",
        )
        old_fact = StateFact(
            domain=StateDomain.SEMANTIC,
            key="working-hypothesis",
            value="provisional",
            source_ref="evidence://hypothesis",
        )
        open_obligation = DesignObligation(
            obligation_id="resolve-program",
            statement="Resolve the current program hypothesis.",
            source_ref="evidence://brief",
            subject_refs=(old_fact.ref,),
        )
        state = _state(
            facts=(brief, old_fact),
            obligations=(open_obligation,),
        )
        new_fact = StateFact(
            domain=StateDomain.SEMANTIC,
            key="program-status",
            value="derived",
            source_ref="evidence://program",
        )
        binding = ParameterBinding(
            key="program-version",
            value="candidate-1",
            source_ref="evidence://program",
        )
        operator = DecisionOperator(
            decision_id="derive-program",
            decision_type="derive-program-hypothesis",
            base_state_digest=state.state_digest,
            authority_id="architect-primary",
            intent="Derive a project-scoped program hypothesis.",
            preconditions=(
                StateCondition(
                    ref=brief.ref,
                    comparator=ConditionComparator.EQUALS,
                    expected_value="compiled",
                ),
            ),
            bindings=(binding,),
            add_facts=(new_fact,),
            delete_fact_refs=(old_fact.ref,),
            spawn_commitments=(_commitment(),),
            spawn_obligations=(
                DesignObligation(
                    obligation_id="test-program",
                    statement="Validate the derived program.",
                    source_ref="decision://derive-program",
                    subject_refs=(new_fact.ref,),
                ),
            ),
            discharge_obligation_ids=("resolve-program",),
            evidence_refs=("evidence://program",),
        )

        result = compile_decision_operator(state, operator)

        self.assertEqual(result.state.epoch, 1)
        self.assertEqual(result.delta.SCHEMA, "StateDelta@2")
        self.assertEqual(result.state.SCHEMA, "OperationalMarkovState@3")
        self.assertEqual(
            result.receipt.SCHEMA,
            "DesignStateClosureReceipt@2",
        )
        self.assertIsNone(result.state.value_for_ref(old_fact.ref))
        self.assertEqual(
            result.state.value_for_ref(new_fact.ref),
            "derived",
        )
        self.assertEqual(
            result.state.value_for_ref(binding.ref),
            "candidate-1",
        )
        self.assertEqual(len(result.state.commitments), 1)
        statuses = {
            item.obligation_id: item.status
            for item in result.state.obligations
        }
        self.assertEqual(
            statuses["resolve-program"],
            ObligationStatus.SATISFIED,
        )
        self.assertEqual(statuses["test-program"], ObligationStatus.OPEN)

        stale = replace(operator, base_state_digest="0" * 64)
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "exact base is stale",
        ):
            compile_decision_operator(state, stale)

    def test_preconditions_and_locks_fail_closed(self) -> None:
        binding = ParameterBinding(
            key="protected-value",
            value="current",
            source_ref="evidence://binding",
        )
        state = _state(
            bindings=(binding,),
            locks=(
                StateLock(
                    target_ref=binding.ref,
                    authority_id="authority-user",
                    source_ref="event://lock/1",
                ),
            ),
        )
        replacement = replace(binding, value="revised")
        unauthorized = DecisionOperator(
            decision_id="revise-locked",
            decision_type="revise-binding",
            base_state_digest=state.state_digest,
            authority_id="architect-primary",
            intent="Propose a binding revision.",
            bindings=(replacement,),
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "locked state requires authority",
        ):
            compile_decision_operator(state, unauthorized)

        failed_precondition = replace(
            unauthorized,
            decision_id="failed-precondition",
            authority_id="authority-user",
            preconditions=(
                StateCondition(
                    ref=binding.ref,
                    comparator=ConditionComparator.EQUALS,
                    expected_value="different",
                ),
            ),
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "precondition failed",
        ):
            compile_decision_operator(state, failed_precondition)

        authorized = replace(
            unauthorized,
            authority_id="authority-user",
        )
        result = compile_decision_operator(state, authorized)
        self.assertEqual(
            result.state.value_for_ref(binding.ref),
            "revised",
        )

        no_effect = DecisionOperator(
            decision_id="empty-transition",
            decision_type="invalid-noop",
            base_state_digest=state.state_digest,
            authority_id="authority-user",
            intent="Attempt to advance without a compiled consequence.",
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "no state effect",
        ):
            compile_decision_operator(state, no_effect)

        unauthorized_lock = replace(
            authorized,
            decision_id="foreign-lock",
            bindings=(),
            add_locks=(
                StateLock(
                    target_ref="binding:future-value",
                    authority_id="authority-other",
                    source_ref="event://lock/2",
                ),
            ),
        )
        with self.assertRaisesRegex(
            DecisionCompilationError,
            "lock for another authority",
        ):
            compile_decision_operator(state, unauthorized_lock)

    def test_dependency_closure_is_transitive_local_and_bounded(self) -> None:
        upstream = "fact:semantic:circulation-origin"
        evaluation = "fact:evaluation:circulation-check"
        deliverable = "fact:deliverable:schematic-package"
        unrelated = "fact:evaluation:unrelated-check"
        dependencies = (
            DependencyEdge(
                upstream_ref=upstream,
                downstream_ref=evaluation,
                relation="requires-recheck",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.REQUIRES_REVALIDATION,
            ),
            DependencyEdge(
                upstream_ref=evaluation,
                downstream_ref=deliverable,
                relation="contributes-to",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref=deliverable,
                downstream_ref=evaluation,
                relation="cycle-safe",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref=unrelated,
                downstream_ref="fact:deliverable:other-package",
                relation="contributes-to",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.INVALIDATES,
            ),
        )
        state = _state(dependencies=dependencies)
        operator = DecisionOperator(
            decision_id="revise-circulation-origin",
            decision_type="revise-semantic-relation",
            base_state_digest=state.state_digest,
            authority_id="architect-primary",
            intent="Revise one semantic relation.",
            invalidates=(upstream,),
        )

        result = compile_decision_operator(state, operator)

        self.assertEqual(
            result.receipt.closure_invalidations,
            tuple(sorted((upstream, evaluation, deliverable))),
        )
        self.assertNotIn(unrelated, result.state.invalidated_refs)
        self.assertEqual(
            len(result.receipt.generated_obligation_ids),
            3,
        )
        for obligation in result.state.obligations:
            self.assertIn(
                obligation.subject_refs[0],
                result.state.invalidated_refs,
            )
            self.assertNotIn("move", obligation.statement.lower())

    def test_history_distinct_states_are_operationally_equivalent(self) -> None:
        fact = StateFact(
            domain=StateDomain.BRIEF,
            key="evidence-status",
            value="available",
            source_ref="evidence://brief",
        )
        first = _state(branch=_branch(branch_id="option-a", epoch=2), facts=(fact,))
        second = _state(
            branch=_branch(branch_id="option-b", epoch=7),
            facts=(fact,),
        )
        self.assertNotEqual(first.state_digest, second.state_digest)
        self.assertEqual(first.sufficient_digest, second.sufficient_digest)

        addition = StateFact(
            domain=StateDomain.UNKNOWN,
            key="next-question",
            value="open",
            source_ref="evidence://question",
        )
        first_operator = DecisionOperator(
            decision_id="record-question",
            decision_type="record-unknown",
            base_state_digest=first.state_digest,
            authority_id="architect-primary",
            intent="Record one unresolved design question.",
            add_facts=(addition,),
        )
        second_operator = replace(
            first_operator,
            base_state_digest=second.state_digest,
        )

        first_result = compile_decision_operator(first, first_operator)
        second_result = compile_decision_operator(second, second_operator)
        self.assertEqual(
            first_result.state.sufficient_digest,
            second_result.state.sufficient_digest,
        )

    def test_structured_fact_epistemics_round_trip_and_affect_digest(self) -> None:
        observed = StateFact(
            domain=StateDomain.GEOMETRY,
            key="surveyed-envelope",
            value={
                "width": 18,
                "depth": 24,
                "units": "voxel",
                "openings": ["north", "east"],
            },
            source_ref="evidence://survey/1",
            epistemic_status=FactEpistemicStatus.OBSERVED,
            confidence=0.92,
            qualification="Observed in the current disposable workspace.",
        )
        state = _state(facts=(observed,))

        loaded = OperationalMarkovState.from_dict(state.to_dict())

        self.assertEqual(loaded, state)
        self.assertEqual(
            loaded.value_for_ref(observed.ref),
            {
                "width": 18,
                "depth": 24,
                "units": "voxel",
                "openings": ["north", "east"],
            },
        )
        disputed = replace(
            observed,
            epistemic_status=FactEpistemicStatus.DISPUTED,
        )
        self.assertNotEqual(
            state.sufficient_digest,
            _state(facts=(disputed,)).sufficient_digest,
        )
        self.assertNotIn(
            "observed",
            str(observed.python_value).lower(),
        )

    def test_blocked_obligation_opens_after_blocker_discharge(self) -> None:
        blocker = DesignObligation(
            obligation_id="resolve-source-conflict",
            statement="Resolve the conflicting source observations.",
            source_ref="evidence://conflict",
        )
        dependent = DesignObligation(
            obligation_id="publish-derived-dimension",
            statement="Publish the resolved derived dimension.",
            source_ref="decision://compile-conflict",
            status=ObligationStatus.BLOCKED,
            blocked_by=("obligation:resolve-source-conflict",),
        )
        block_edge = DependencyEdge(
            upstream_ref="obligation:resolve-source-conflict",
            downstream_ref="obligation:publish-derived-dimension",
            relation="must-resolve-before",
            source_ref="decision://compile-conflict",
            effect=DependencyEffect.BLOCKS,
        )
        state = _state(
            obligations=(blocker, dependent),
            dependencies=(block_edge,),
        )
        operator = DecisionOperator(
            decision_id="resolve-source-conflict",
            decision_type="resolve-evidence-conflict",
            base_state_digest=state.state_digest,
            authority_id="authority-user",
            intent="Record an authoritative resolution.",
            discharge_obligation_ids=("resolve-source-conflict",),
            evidence_refs=("evidence://resolution",),
        )

        result = compile_decision_operator(state, operator)
        statuses = {
            item.obligation_id: item.status
            for item in result.state.obligations
        }

        self.assertEqual(
            statuses["resolve-source-conflict"],
            ObligationStatus.SATISFIED,
        )
        self.assertEqual(
            statuses["publish-derived-dimension"],
            ObligationStatus.OPEN,
        )
        self.assertNotIn(
            "obligation:publish-derived-dimension",
            result.state.invalidated_refs,
        )

    def test_condition_change_recompiles_only_relevant_obligation(self) -> None:
        selection = StateFact(
            domain=StateDomain.DECISION,
            key="scheme-selected",
            value=False,
            source_ref="event://selection/0",
            epistemic_status=FactEpistemicStatus.DECLARED,
        )
        dependent = DesignObligation(
            obligation_id="develop-selected-scheme",
            statement="Develop the selected scheme.",
            source_ref="decision://workflow",
            status=ObligationStatus.BLOCKED,
            condition=ObligationCondition(
                ref=selection.ref,
                expected_value=True,
            ),
        )
        unrelated = DesignObligation(
            obligation_id="retain-audit-trace",
            statement="Retain the audit trace.",
            source_ref="decision://workflow",
        )
        state = _state(
            facts=(selection,),
            obligations=(dependent, unrelated),
        )
        revised = replace(
            selection,
            value=True,
            source_ref="event://selection/1",
        )
        operator = DecisionOperator(
            decision_id="accept-scheme",
            decision_type="record-authority-decision",
            base_state_digest=state.state_digest,
            authority_id="authority-user",
            intent="Activate only work dependent on the selected scheme.",
            add_facts=(revised,),
            evidence_refs=("event://selection/1",),
        )

        result = compile_decision_operator(state, operator)
        statuses = {
            item.obligation_id: item.status
            for item in result.state.obligations
        }

        self.assertEqual(
            statuses["develop-selected-scheme"],
            ObligationStatus.OPEN,
        )
        self.assertEqual(
            statuses["retain-audit-trace"],
            ObligationStatus.OPEN,
        )
        self.assertEqual(result.receipt.closure_invalidations, ())

    def test_typed_dependency_effects_do_not_over_propagate(self) -> None:
        source = "fact:semantic:revised-relation"
        invalidated = "fact:deliverable:affected-package"
        revalidated = "fact:evaluation:affected-check"
        supported = "fact:deliverable:reference-only"
        blocked = DesignObligation(
            obligation_id="await-authority",
            statement="Await the named authority.",
            source_ref="decision://workflow",
        )
        downstream = DesignObligation(
            obligation_id="resume-after-authority",
            statement="Resume after authority is available.",
            source_ref="decision://workflow",
            status=ObligationStatus.BLOCKED,
            blocked_by=("obligation:await-authority",),
        )
        dependencies = (
            DependencyEdge(
                upstream_ref=source,
                downstream_ref=invalidated,
                relation="changes-output",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.INVALIDATES,
            ),
            DependencyEdge(
                upstream_ref=source,
                downstream_ref=revalidated,
                relation="requires-check",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.REQUIRES_REVALIDATION,
            ),
            DependencyEdge(
                upstream_ref=source,
                downstream_ref=supported,
                relation="is-context-for",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.SUPPORTS_ONLY,
            ),
            DependencyEdge(
                upstream_ref="obligation:await-authority",
                downstream_ref="obligation:resume-after-authority",
                relation="must-resolve-before",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.BLOCKS,
            ),
        )
        state = _state(
            obligations=(blocked, downstream),
            dependencies=dependencies,
        )
        operator = DecisionOperator(
            decision_id="revise-one-relation",
            decision_type="revise-semantic-relation",
            base_state_digest=state.state_digest,
            authority_id="architect-primary",
            intent="Invalidate only authorized downstream products.",
            invalidates=(source,),
        )

        result = compile_decision_operator(state, operator)

        self.assertEqual(
            result.receipt.closure_invalidations,
            tuple(sorted((source, invalidated, revalidated))),
        )
        self.assertNotIn(supported, result.state.invalidated_refs)
        self.assertFalse(
            any(
                ref.startswith(("obligation:", "commitment:"))
                for ref in result.state.invalidated_refs
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "normative refs",
        ):
            DependencyEdge(
                upstream_ref=source,
                downstream_ref="obligation:await-authority",
                relation="invalid-model",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.INVALIDATES,
            )
        with self.assertRaisesRegex(
            ValueError,
            "connect obligation refs",
        ):
            DependencyEdge(
                upstream_ref=source,
                downstream_ref="obligation:await-authority",
                relation="invalid-blocker",
                source_ref="decision://dependency-map",
                effect=DependencyEffect.BLOCKS,
            )

    def test_blocking_graph_rejects_orphan_edges_and_cycles(self) -> None:
        first = DesignObligation(
            obligation_id="first",
            statement="Resolve the first obligation.",
            source_ref="decision://workflow",
            status=ObligationStatus.BLOCKED,
            blocked_by=("obligation:second",),
        )
        second = DesignObligation(
            obligation_id="second",
            statement="Resolve the second obligation.",
            source_ref="decision://workflow",
            status=ObligationStatus.BLOCKED,
            blocked_by=("obligation:first",),
        )
        dependencies = (
            DependencyEdge(
                upstream_ref="obligation:second",
                downstream_ref="obligation:first",
                relation="must-resolve-before",
                source_ref="decision://workflow",
                effect=DependencyEffect.BLOCKS,
            ),
            DependencyEdge(
                upstream_ref="obligation:first",
                downstream_ref="obligation:second",
                relation="must-resolve-before",
                source_ref="decision://workflow",
                effect=DependencyEffect.BLOCKS,
            ),
        )

        with self.assertRaisesRegex(ValueError, "contains a cycle"):
            _state(
                obligations=(first, second),
                dependencies=dependencies,
            )

        open_first = replace(
            first,
            status=ObligationStatus.OPEN,
            blocked_by=(),
        )
        with self.assertRaisesRegex(
            ValueError,
            "exactly match blocked_by",
        ):
            _state(
                obligations=(open_first,),
                dependencies=(
                    DependencyEdge(
                        upstream_ref="obligation:first",
                        downstream_ref="obligation:ghost",
                        relation="orphan-edge",
                        source_ref="decision://workflow",
                        effect=DependencyEffect.BLOCKS,
                    ),
                ),
            )

    def test_legacy_records_load_read_only_and_migrate_fail_closed(self) -> None:
        legacy_state = load_operational_state_record(
            {
                "schema": "OperationalMarkovState@2",
                "state_digest": "b" * 64,
                "facts": [],
            }
        )
        self.assertIsInstance(
            legacy_state,
            LegacyOperationalMarkovStateV2,
        )
        with self.assertRaises(OperationalStateMigrationRequired):
            legacy_state.migrate()

        legacy_operator = load_decision_operator_record(
            {
                "schema": "DecisionOperator@1",
                "decision_id": "legacy-decision",
            }
        )
        self.assertIsInstance(
            legacy_operator,
            LegacyDecisionOperatorV1,
        )
        with self.assertRaises(DecisionOperatorMigrationRequired):
            legacy_operator.migrate()

    def test_schema_is_generic_and_has_no_persistence_authority(self) -> None:
        state = _state()
        encoded = str(state.to_dict()).lower()
        self.assertNotIn("file://", encoded)
        self.assertNotIn("16x12", encoded)
        self.assertNotIn("pantheon", encoded)
        self.assertNotIn("library", encoded)

        sources = (
            Path(__file__).parents[1]
            / "archflow"
            / "state"
            / "operational_state.py"
        ).read_text(encoding="utf-8") + (
            Path(__file__).parents[1]
            / "archflow"
            / "state"
            / "decision_operator.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("mkdir(", sources)
        self.assertNotIn("write_text(", sources)
        self.assertNotIn("open(", sources)
        self.assertEqual(
            hashlib.sha256(
                str(state.to_dict()).encode("utf-8")
            ).hexdigest(),
            hashlib.sha256(
                str(state.to_dict()).encode("utf-8")
            ).hexdigest(),
        )

        digestless = ProjectVersionRef(
            project_id="test-project",
            version=0,
        )
        with self.assertRaisesRegex(
            ValueError,
            "requires state_sha256",
        ):
            OperationalMarkovState(
                branch=BranchRef(
                    run=RunRef(
                        project_id="test-project",
                        run_id="run-digestless",
                        base=digestless,
                    ),
                    branch_id="option-a",
                    epoch=0,
                ),
                compiler_version="compiler-1",
                phase="schematic",
            )


if __name__ == "__main__":
    unittest.main()
