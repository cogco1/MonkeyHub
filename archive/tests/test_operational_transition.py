from __future__ import annotations

import json
import unittest

from archflow.project.refs import BranchRef, ProjectVersionRef, RunRef
from archive.archflow.runtime.operational_transition import FactBasis, LegacyOperationalMarkovState, OperationalExpertDiscovery, OperationalFact, OperationalObligation, OperationalTransitionError, OperationalStateMigrationRequired, TransitionObservation, TransitionObservationStatus, TransitionProposal, TransitionReceiptStatus, apply_operational_transition, compile_operational_transition_trace, initial_operational_state, load_operational_transition_trace, operational_plan_sha256, require_compiled_operational_state
from archflow.state.operational_state import OperationalMarkovState as CompiledOperationalMarkovState
from archflow.state.model import StateRef


def _discovery(state, *expert_ids: str) -> OperationalExpertDiscovery:
    return OperationalExpertDiscovery(
        state_digest=state.state_digest,
        canonical_base=state.canonical_base,
        obligation_ids=tuple(
            item.obligation_id for item in state.obligations
        ),
        expert_ids=tuple(expert_ids),
    )


class OperationalTransitionTests(unittest.TestCase):
    def test_v1_is_explicit_compatibility_and_cannot_gain_v2_authority(
        self,
    ) -> None:
        legacy = initial_operational_state(
            canonical_base=StateRef("legacy-trace-project", 0),
        )
        self.assertIsInstance(legacy, LegacyOperationalMarkovState)
        self.assertEqual(
            legacy.to_dict()["schema"],
            "OperationalMarkovState@1",
        )
        with self.assertRaisesRegex(
            OperationalStateMigrationRequired,
            "recompile authoritative project records",
        ):
            require_compiled_operational_state(legacy)

        base = ProjectVersionRef(
            project_id="compiled-project",
            version=0,
            state_sha256="b" * 64,
        )
        compiled = CompiledOperationalMarkovState(
            branch=BranchRef(
                run=RunRef(
                    project_id="compiled-project",
                    run_id="run-001",
                    base=base,
                ),
                branch_id="option-a",
                epoch=0,
            ),
            compiler_version="compiler-1",
            phase="brief",
        )
        self.assertIs(
            require_compiled_operational_state(compiled),
            compiled,
        )

    def test_verified_result_advances_state_and_reloadable_trace(self) -> None:
        initial = initial_operational_state(
            canonical_base=StateRef("run-operational", 0),
            obligations=(
                OperationalObligation(
                    obligation_id="obligation.resolve-input",
                    topic="program",
                    statement="Resolve the current program question.",
                    source_ref="evidence://request",
                ),
            ),
        )
        digest = operational_plan_sha256({"operation": "compile-current-input"})
        proposal = TransitionProposal(
            proposal_id="proposal-compile",
            base_state_digest=initial.state_digest,
            capability_id="compiler.generic",
            intent="Compile one verified project-scoped fact.",
            plan_sha256=digest,
            resolves_obligation_ids=("obligation.resolve-input",),
        )
        observation = TransitionObservation(
            observation_id="observation-compile",
            proposal_id=proposal.proposal_id,
            base_state_digest=initial.state_digest,
            plan_sha256=digest,
            status=TransitionObservationStatus.CONFIRMED,
            summary="The deterministic compiler verified the result.",
            fact_updates=(
                OperationalFact(
                    key="program.current",
                    value="compiled",
                    basis=FactBasis.DETERMINISTIC_COMPILE,
                    source_ref="evidence://compiled",
                ),
            ),
            evidence_refs=("evidence://compiled",),
            exact_result_verified=True,
        )
        result = apply_operational_transition(initial, proposal, observation)

        self.assertEqual(result.state.epoch, 1)
        self.assertEqual(result.state.material_revision, 1)
        self.assertEqual(result.state.obligations, ())
        self.assertEqual(
            result.receipt.status,
            TransitionReceiptStatus.ADVANCED,
        )

        payload = compile_operational_transition_trace(
            initial_state=initial,
            initial_discovery=_discovery(initial, "expert.generic"),
            transitions=((result, _discovery(result.state)),),
        )
        trace = load_operational_transition_trace(payload)

        self.assertEqual(
            trace.final_state.state_digest,
            result.state.state_digest,
        )
        self.assertEqual(trace.final_state.canonical_base.version, 0)

    def test_prewrite_rejection_advances_knowledge_not_material(self) -> None:
        initial = initial_operational_state(
            canonical_base=StateRef("run-prewrite", 0),
        )
        digest = operational_plan_sha256({"operation": "preview"})
        proposal = TransitionProposal(
            proposal_id="proposal-preview",
            base_state_digest=initial.state_digest,
            capability_id="tool.preview",
            intent="Preview a pending transition.",
            plan_sha256=digest,
        )
        observation = TransitionObservation(
            observation_id="observation-prewrite",
            proposal_id=proposal.proposal_id,
            base_state_digest=initial.state_digest,
            plan_sha256=digest,
            status=TransitionObservationStatus.REJECTED_PREWRITE,
            summary="The environment rejected the plan before writing.",
            new_obligations=(
                OperationalObligation(
                    obligation_id="obligation.environment",
                    topic="environment",
                    statement="Resolve the observed environment relationship.",
                    source_ref="receipt://prewrite",
                ),
            ),
            evidence_refs=("receipt://prewrite",),
            exact_result_verified=True,
        )

        result = apply_operational_transition(initial, proposal, observation)

        self.assertEqual(result.state.epoch, 1)
        self.assertEqual(result.state.material_revision, 0)
        self.assertEqual(result.state.facts, initial.facts)
        self.assertEqual(
            result.receipt.status,
            TransitionReceiptStatus.ENVIRONMENT_OBSERVED,
        )

    def test_pending_stale_and_verified_noop_fail_closed(self) -> None:
        state = initial_operational_state(
            canonical_base=StateRef("run-transition-guards", 0),
        )
        digest = operational_plan_sha256({"operation": "test"})
        proposal = TransitionProposal(
            proposal_id="proposal-test",
            base_state_digest=state.state_digest,
            capability_id="compiler.generic",
            intent="Test exact-state binding.",
            plan_sha256=digest,
        )
        pending = TransitionObservation(
            observation_id="observation-pending",
            proposal_id=proposal.proposal_id,
            base_state_digest=state.state_digest,
            plan_sha256=digest,
            status=TransitionObservationStatus.CONFIRMED,
            summary="The result is not verified.",
        )
        with self.assertRaisesRegex(
            OperationalTransitionError,
            "pending or unverified",
        ):
            apply_operational_transition(state, proposal, pending)

        stale = TransitionProposal(
            proposal_id="proposal-stale",
            base_state_digest="0" * 64,
            capability_id="compiler.generic",
            intent="Test stale-state rejection.",
            plan_sha256=digest,
        )
        with self.assertRaisesRegex(
            OperationalTransitionError,
            "proposal exact base is stale",
        ):
            apply_operational_transition(state, stale, pending)

        noop = TransitionObservation(
            observation_id="observation-noop",
            proposal_id=proposal.proposal_id,
            base_state_digest=state.state_digest,
            plan_sha256=digest,
            status=TransitionObservationStatus.CONFIRMED,
            summary="The verified result changed nothing.",
            exact_result_verified=True,
        )
        result = apply_operational_transition(state, proposal, noop)
        self.assertEqual(result.state.state_digest, state.state_digest)
        self.assertEqual(
            result.receipt.status,
            TransitionReceiptStatus.NO_PROGRESS,
        )

    def test_trace_tampering_cannot_gain_authority(self) -> None:
        initial = initial_operational_state(
            canonical_base=StateRef("run-trace-authority", 0),
        )
        payload = compile_operational_transition_trace(
            initial_state=initial,
            initial_discovery=_discovery(initial),
            transitions=(),
        )
        tampered = json.loads(json.dumps(payload))
        tampered["canonical_commit_receipt"] = {"claimed": True}

        with self.assertRaisesRegex(
            OperationalTransitionError,
            "forbidden authority",
        ):
            load_operational_transition_trace(tampered)


if __name__ == "__main__":
    unittest.main()
