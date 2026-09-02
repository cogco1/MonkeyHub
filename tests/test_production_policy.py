"""P097: production policy — complete the task, keep the state exact.

Every knob is opt-in on GeometryProposalPolicy; with all flags off the
producer behaves exactly as the experiment policy (covered by the
existing producer tests). Here each knob is exercised through the real
producer on the room fixture.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from archflow.capabilities.geometry_proposal import (
    GeometryProposalPolicy,
    GeometryProposalProductionError,
    GeometryProposalStatus,
    produce_geometry_program_proposal,
    proposal_authoring_output,
    proposal_edit_authoring_output,
    resume_geometry_program_proposal,
)
from archflow.state.geometry_program import GeometryParameter
from tests.test_geometry_compiler import COMMITMENT
from tests.test_geometry_proposal_producer import IDENTITY, _ScriptedProvider
from tests.test_production_wiring import _ProducerFixture
from tests.test_sandbox_realization import compiled_room


class _PolicyFixture(_ProducerFixture):
    def setUp(self) -> None:
        super().setUp()
        _, self.prior_program, _ = compiled_room()
        self.floor = next(op for op in self.proposal.operations if op.op_id == "floor")

    async def _run(self, provider, policy, **extra):
        return await produce_geometry_program_proposal(
            self.repository, provider, run=self.run, destination=self.destination,
            spatial_option_ref=self.option_ref, design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,), provider_identity=IDENTITY, policy=policy,
            template_refs=(self.template_ref,), **extra,
        )

    def _issue_codes(self, result) -> set[str]:
        codes = set()
        for ref in result.round_refs:
            for row in self.repository.load_json(ref).get("issues", []):
                codes.add(row["code"])
        return codes

    def _with_bad_ops(self, count: int):
        """A proposal with `count` extra new ops naming an unknown binding."""

        extras = tuple(
            replace(self.floor, op_id=f"extra-{i}", output_object_ids=(f"extra-{i}",), semantic_binding_ids=("no-such-binding",))
            for i in range(count)
        )
        operations = tuple(sorted(tuple(self.proposal.operations) + extras, key=lambda op: op.op_id))
        return replace(self.proposal, operations=operations)


class PolicyContractTests(unittest.TestCase):
    def test_defaults_are_the_experiment_policy(self) -> None:
        policy = GeometryProposalPolicy(3)
        self.assertFalse(policy.production)
        self.assertEqual(policy.round_cap, 3)
        self.assertEqual(policy.to_dict()["complete_bookkeeping"], False)
        with self.assertRaises(GeometryProposalProductionError):
            GeometryProposalPolicy(3, progress_budget=2)
        with self.assertRaises(GeometryProposalProductionError):
            GeometryProposalPolicy(3, progress_budget=40)


class BookkeepingCompletionTests(_PolicyFixture):
    def _edit_changing_inner(self):
        """Edit the prior program: grow the `inner` solid, restate nothing."""

        ops = []
        for op in self.prior_program.proposal.operations:
            if op.op_id == "inner":
                size = next(p for p in op.parameters if p.name == "size")
                grown = [v * 1.1 for v in json.loads(size.value_json)]
                params = tuple(
                    GeometryParameter.create(name="size", kind=size.kind, value=grown, unit=size.unit) if p.name == "size" else p
                    for p in op.parameters
                )
                op = replace(op, parameters=params)
            ops.append(op)
        return replace(
            self.proposal, operations=tuple(ops), revisions=(),
            predecessor_program_digest=self.prior_program.program_digest,
        )

    async def test_off_refuses_on_restated_bookkeeping(self) -> None:
        revision = self._edit_changing_inner()
        provider = _ScriptedProvider((proposal_edit_authoring_output(self.prior_program, revision),))
        result = await self._run(provider, GeometryProposalPolicy(1), prior_program=self.prior_program)
        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        self.assertIn("compiler.missing_revision_precondition", self._issue_codes(result))

    async def test_on_completes_from_records_and_records_it(self) -> None:
        revision = self._edit_changing_inner()
        provider = _ScriptedProvider((proposal_edit_authoring_output(self.prior_program, revision),))
        result = await self._run(
            provider, GeometryProposalPolicy(1, complete_bookkeeping=True), prior_program=self.prior_program
        )
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        self.assertEqual(len(result.completion_refs), 1)
        completion = self.repository.load_json(result.completion_refs[0])
        self.assertEqual(completion["schema"], "ProtocolCompletion@1")
        self.assertIn("inner", completion["revision_preconditions"])
        self.assertTrue(any(a.get("object_id") == "inner" for a in completion["acknowledgements"]))
        self.assertFalse(completion["canonical_write_authority"])
        # the accepted proposal carries the derived preconditions, not the model's text
        assert result.proposal is not None
        self.assertIn("inner", {r.object_id for r in result.proposal.revisions})
        expected = {o.object_id: o.object_digest for o in self.prior_program.objects}["inner"]
        self.assertEqual(next(r.expected_digest for r in result.proposal.revisions if r.object_id == "inner"), expected)


class ProgressBudgetTests(_PolicyFixture):
    async def test_shrinking_issues_continue_past_the_bounded_budget(self) -> None:
        provider = _ScriptedProvider((
            proposal_authoring_output(self._with_bad_ops(2)),
            proposal_authoring_output(self._with_bad_ops(1)),
            proposal_authoring_output(self.proposal),
        ))
        result = await self._run(provider, GeometryProposalPolicy(1, progress_budget=4))
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        self.assertEqual(len(result.round_refs), 3)

    async def test_without_budget_the_same_provider_is_exhausted(self) -> None:
        provider = _ScriptedProvider((
            proposal_authoring_output(self._with_bad_ops(2)),
            proposal_authoring_output(self._with_bad_ops(1)),
            proposal_authoring_output(self.proposal),
        ))
        result = await self._run(provider, GeometryProposalPolicy(1))
        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        self.assertEqual(len(result.round_refs), 1)

    async def test_stall_stops_and_escalates(self) -> None:
        provider = _ScriptedProvider((
            proposal_authoring_output(self._with_bad_ops(2)),
            proposal_authoring_output(self._with_bad_ops(2)),
            proposal_authoring_output(self.proposal),
        ))
        result = await self._run(provider, GeometryProposalPolicy(1, progress_budget=4, escalate_on_stall=True))
        self.assertIs(result.status, GeometryProposalStatus.EXHAUSTED)
        self.assertEqual(len(result.round_refs), 2)
        assert result.escalation_ref is not None
        escalation = self.repository.load_json(result.escalation_ref)
        self.assertEqual(escalation["schema"], "GeometryProposalEscalation@1")
        self.assertEqual(escalation["last_round_ref"], result.round_refs[-1].uri)
        self.assertTrue(escalation["issues"])


class PartialAcceptanceTests(_PolicyFixture):
    async def test_object_scoped_failures_are_deferred(self) -> None:
        provider = _ScriptedProvider((proposal_authoring_output(self._with_bad_ops(1)),))
        result = await self._run(provider, GeometryProposalPolicy(1, partial_acceptance=True))
        self.assertIs(result.status, GeometryProposalStatus.ACCEPTED)
        assert result.program is not None and result.deferral_ref is not None
        self.assertNotIn("extra-0", {o.object_id for o in result.program.objects})
        deferral = self.repository.load_json(result.deferral_ref)
        self.assertEqual(deferral["schema"], "PartialAcceptanceDeferral@1")
        self.assertEqual(deferral["deferred_ops_removed"], ["extra-0"])
        self.assertTrue(deferral["issues"])

    async def test_non_object_scoped_failure_blocks_partial_acceptance(self) -> None:
        # a binding that omits the required commitment fails coverage before the
        # compiler runs: nothing is object-scoped, so nothing may be deferred
        binding = self.proposal.semantic_bindings[0]
        uncommitted = replace(self.proposal, semantic_bindings=(replace(binding, commitment_refs=("commitment:other",)),))
        provider = _ScriptedProvider((proposal_authoring_output(uncommitted),))
        result = await self._run(provider, GeometryProposalPolicy(1, partial_acceptance=True))
        self.assertIsNot(result.status, GeometryProposalStatus.ACCEPTED)
        self.assertIsNone(result.deferral_ref)


class ResumeTests(_PolicyFixture):
    async def test_resume_continues_from_the_escalated_round(self) -> None:
        bad = _ScriptedProvider((proposal_authoring_output(self._with_bad_ops(1)),))
        first = await self._run(bad, GeometryProposalPolicy(1, escalate_on_stall=True))
        self.assertIs(first.status, GeometryProposalStatus.EXHAUSTED)
        assert first.escalation_ref is not None
        good = _ScriptedProvider((proposal_authoring_output(self.proposal),))
        second = await resume_geometry_program_proposal(
            self.repository, good, escalation_ref=first.escalation_ref, run=self.run,
            destination=self.destination, spatial_option_ref=self.option_ref, design_state=self.design_state,
            required_commitment_refs=(COMMITMENT,), provider_identity=IDENTITY, policy=GeometryProposalPolicy(1),
            template_refs=(self.template_ref,),
        )
        self.assertIs(second.status, GeometryProposalStatus.ACCEPTED)
        # continuity lives on the record: the resumed round's request carries the
        # escalated round as its exact repair context, not a re-authored chain
        resumed_round = self.repository.load_json(second.round_refs[0])
        self.assertIn(first.round_refs[-1].uri, json.dumps(resumed_round))
        self.assertEqual(resumed_round["spatial_option_ref"]["sha256"], self.option_ref.sha256)


if __name__ == "__main__":
    unittest.main()
