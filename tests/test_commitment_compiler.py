from __future__ import annotations

import unittest
from pathlib import Path

import archflow.compilers.commitments as canonical_commitments
import archflow.runtime.commitment_compiler as legacy_commitments
from archflow.compilers.commitments import (
    CommitmentReplacementRequiresRevision,
    IntentCompilationStatus,
    IntentObservation,
    IntentOperator,
    IntentTerm,
    compile_intent,
    confirm_proposal,
    revise_locked_commitment,
)
from archflow.state import (
    CommitmentAuthorityError,
    CommitmentStatus,
    CommitmentStrength,
)


def _observation(
    *terms: IntentTerm,
    observation_id: str = "observation-001",
    explicit: bool = True,
    evidence_refs: tuple[str, ...] = ("project://case/input/request.json",),
) -> IntentObservation:
    return IntentObservation(
        observation_id=observation_id,
        raw_text="A bounded test request.",
        source_event_ref="project://case/events/request.json",
        authority_id="authority.user",
        scope_ref="project://case",
        interpretations=terms,
        evidence_refs=evidence_refs,
        explicit=explicit,
    )


class CommitmentCompilerTests(unittest.TestCase):
    def test_legacy_facade_reexports_canonical_objects_by_identity(self) -> None:
        for name in legacy_commitments.__all__:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(canonical_commitments, name),
                    getattr(legacy_commitments, name),
                )

    def test_reference_intent_preserves_schema_digest_and_behavior(self) -> None:
        term = IntentTerm(
            "gross_area",
            IntentOperator.MINIMUM,
            1200,
            "square_metres",
        )
        compilation = compile_intent(_observation(term))

        self.assertEqual(
            "39d393bfdd01ce889028c701160b17160ef0bbd481adf020acc1304e97e02697",
            term.digest,
        )
        self.assertEqual("IntentCompilation@1", compilation.schema)
        self.assertEqual(
            "intent-b223eea826e87aa3d6b6",
            compilation.compilation_id,
        )
        self.assertIs(compilation.status, IntentCompilationStatus.PROPOSED)
        self.assertEqual(
            "proposal.intent.1af8a30c198919835817",
            compilation.proposals[0].proposal_id,
        )

    def test_explicit_requirement_compiles_deterministically_but_not_active(self) -> None:
        use = IntentTerm(
            parameter_key="building.requested_use",
            operator=IntentOperator.EXACT,
            value="public assembly",
        )
        first = compile_intent(_observation(use))
        second = compile_intent(_observation(use))

        self.assertEqual(first, second)
        self.assertIs(first.status, IntentCompilationStatus.PROPOSED)
        self.assertEqual(len(first.proposals), 1)
        proposal = first.proposals[0]
        self.assertIs(
            proposal.commitment.status,
            CommitmentStatus.PROPOSED,
        )
        self.assertFalse(proposal.commitment.has_hard_gate_authority)
        self.assertEqual(
            proposal.commitment.evidence_refs,
            ("project://case/input/request.json",),
        )
        self.assertEqual(
            proposal.commitment.satisfaction_criterion.criterion_id,
            "building.requested_use",
        )

    def test_ambiguous_scale_preserves_target_minimum_and_maximum(self) -> None:
        interpretations = (
            IntentTerm(
                "occupancy",
                IntentOperator.TARGET,
                300,
                "people",
            ),
            IntentTerm(
                "occupancy",
                IntentOperator.MINIMUM,
                300,
                "people",
            ),
            IntentTerm(
                "occupancy",
                IntentOperator.MAXIMUM,
                300,
                "people",
            ),
        )
        compilation = compile_intent(
            _observation(*interpretations, explicit=False)
        )

        self.assertIs(compilation.status, IntentCompilationStatus.AMBIGUOUS)
        self.assertEqual(
            {item.term.operator for item in compilation.proposals},
            {
                IntentOperator.TARGET,
                IntentOperator.MINIMUM,
                IntentOperator.MAXIMUM,
            },
        )
        self.assertTrue(
            all(
                item.commitment.status is CommitmentStatus.PROPOSED
                for item in compilation.proposals
            )
        )

    def test_confirmation_requires_named_authority_and_creates_hard_lock(self) -> None:
        proposal = compile_intent(
            _observation(
                IntentTerm(
                    "gross_area",
                    IntentOperator.MINIMUM,
                    1200,
                    "square_metres",
                )
            )
        ).proposals[0]

        with self.assertRaises(CommitmentAuthorityError):
            confirm_proposal(
                proposal,
                actor_authority_id="authority.model",
                confirmation_event_ref="project://case/events/rejected.json",
                monitor_state_ref="monitor://case/gross-area",
            )

        locked = confirm_proposal(
            proposal,
            actor_authority_id="authority.user",
            confirmation_event_ref="project://case/events/confirmed.json",
            monitor_state_ref="monitor://case/gross-area",
        )
        self.assertIs(locked.commitment.status, CommitmentStatus.ACTIVE)
        self.assertTrue(locked.commitment.has_hard_gate_authority)
        self.assertEqual(locked.commitment.authorized_by, "authority.user")

    def test_later_text_cannot_overwrite_lock_without_revision_event(self) -> None:
        original = compile_intent(
            _observation(
                IntentTerm(
                    "gross_area",
                    IntentOperator.MINIMUM,
                    1200,
                    "square_metres",
                )
            )
        ).proposals[0]
        locked = confirm_proposal(
            original,
            actor_authority_id="authority.user",
            confirmation_event_ref="project://case/events/confirmed.json",
            monitor_state_ref="monitor://case/gross-area",
        )
        later = _observation(
            IntentTerm(
                "gross_area",
                IntentOperator.MINIMUM,
                900,
                "square_metres",
            ),
            observation_id="observation-002",
        )

        with self.assertRaises(CommitmentReplacementRequiresRevision):
            compile_intent(later, locked=(locked,))

        replacement = compile_intent(later).proposals[0]
        revision = revise_locked_commitment(
            locked,
            replacement,
            actor_authority_id="authority.user",
            revision_event_ref="project://case/events/revision.json",
            monitor_state_ref="monitor://case/gross-area-v2",
        )
        self.assertIs(revision.previous.status, CommitmentStatus.REVISED)
        self.assertEqual(
            revision.previous.successor_ids,
            (revision.replacement.commitment.commitment_id,),
        )
        self.assertEqual(
            revision.replacement.commitment.predecessor_id,
            locked.commitment.commitment_id,
        )

    def test_missing_evidence_or_interpretation_remains_unknown(self) -> None:
        term = IntentTerm(
            "building.requested_use",
            IntentOperator.EXACT,
            "community use",
        )
        no_evidence = compile_intent(
            _observation(term, evidence_refs=())
        )
        no_interpretation = compile_intent(_observation())

        self.assertIs(no_evidence.status, IntentCompilationStatus.UNKNOWN)
        self.assertEqual(
            no_evidence.unknown_reasons,
            ("intent.evidence_missing",),
        )
        self.assertIs(
            no_interpretation.status,
            IntentCompilationStatus.UNKNOWN,
        )
        self.assertEqual(no_evidence.proposals, ())
        self.assertEqual(no_interpretation.proposals, ())

    def test_compiler_has_no_pack_or_building_specific_default(self) -> None:
        source = (
            __import__(
                "archflow.compilers.commitments",
                fromlist=["__file__"],
            )
            .__file__
        )
        self.assertIsNotNone(source)
        text = Path(source).read_text(encoding="utf-8")
        lowered = text.lower()
        for forbidden in (
            "pantheon",
            "rotunda",
            "library",
            "pack default",
            "width_blocks",
            "depth_blocks",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertIn(
            CommitmentStrength.HARD,
            {item for item in CommitmentStrength},
        )


if __name__ == "__main__":
    unittest.main()
