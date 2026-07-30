from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from archflow.adapters import FakeVoxelAdapter
from archflow.commit import InMemoryStateStore
from archflow.runtime import FakeArchitect, RunStatus, initial_state, run_once
from archflow.state import (
    Commitment,
    CommitmentAuthorityError,
    CommitmentKind,
    CommitmentStatus,
    CommitmentStrength,
    CommitmentTransitionError,
    CriterionRef,
    BuildingProgram,
    FootprintTarget,
    RevisionPolicy,
    transition_commitment,
)
from archflow.submission import CandidateDelta
from archflow.workspace import WorkspaceManager


def _commitment(
    *,
    commitment_id: str = "commitment.test",
    kind: CommitmentKind = CommitmentKind.ACHIEVEMENT,
    strength: CommitmentStrength = CommitmentStrength.HARD,
    status: CommitmentStatus = CommitmentStatus.PROPOSED,
    revision_policy: RevisionPolicy = RevisionPolicy.OWNER_ONLY,
    permitted_authority_ids: tuple[str, ...] = (),
    authorized_by: str | None = None,
    predecessor_id: str | None = None,
) -> Commitment:
    return Commitment(
        commitment_id=commitment_id,
        kind=kind,
        strength=strength,
        status=status,
        authority_id="authority.user",
        authorized_by=authorized_by,
        source_event_ref="event://request/1",
        evidence_refs=("evidence://request",),
        scope_refs=("project://current",),
        activation_criterion=CriterionRef(
            criterion_id="criterion.activation",
            provider_id="monitor.lifecycle",
            subject_refs=("project://current",),
        ),
        satisfaction_criterion=CriterionRef(
            criterion_id="criterion.satisfaction",
            provider_id="validator.named",
            subject_refs=("project://current",),
        ),
        revision_policy=revision_policy,
        permitted_authority_ids=permitted_authority_ids,
        predecessor_id=predecessor_id,
    )


def _activate(commitment: Commitment) -> Commitment:
    accepted = transition_commitment(
        commitment,
        CommitmentStatus.ACCEPTED,
        actor_authority_id="authority.user",
    )
    return transition_commitment(
        accepted,
        CommitmentStatus.ACTIVE,
        actor_authority_id="authority.user",
        monitor_state_ref="monitor://commitment/active",
    )


class CommitmentContractTests(unittest.TestCase):
    def test_frozen_bounded_serialization_round_trip(self) -> None:
        commitment = _activate(_commitment())
        encoded = commitment.to_json()

        with self.assertRaises(FrozenInstanceError):
            commitment.status = CommitmentStatus.RELEASED  # type: ignore[misc]
        self.assertEqual(Commitment.from_json(encoded), commitment)
        self.assertEqual(json.loads(encoded)["schema"], "Commitment@1")
        self.assertNotIn("transcript", encoded)
        self.assertNotIn("expression", encoded)

        payload = commitment.to_dict()
        payload["transcript"] = ["hidden history"]
        with self.assertRaisesRegex(ValueError, "fields drifted"):
            Commitment.from_dict(payload)
        with self.assertRaisesRegex(TypeError, "Commitment values"):
            replace(
                initial_state("A test building"),
                commitments=("natural-language note",),  # type: ignore[arg-type]
            )

    def test_acceptance_and_revision_follow_named_authority(self) -> None:
        commitment = _commitment(
            revision_policy=RevisionPolicy.NAMED_AUTHORITIES,
            permitted_authority_ids=("authority.reviewer",),
        )
        with self.assertRaises(CommitmentAuthorityError):
            transition_commitment(
                commitment,
                CommitmentStatus.ACCEPTED,
                actor_authority_id="authority.unknown",
            )

        accepted = transition_commitment(
            commitment,
            CommitmentStatus.ACCEPTED,
            actor_authority_id="authority.reviewer",
        )
        active = transition_commitment(
            accepted,
            CommitmentStatus.ACTIVE,
            actor_authority_id="authority.reviewer",
        )
        revised = transition_commitment(
            active,
            CommitmentStatus.REVISED,
            actor_authority_id="authority.reviewer",
            successor_id="commitment.test.v2",
        )

        self.assertEqual(revised.source_event_ref, commitment.source_event_ref)
        self.assertEqual(revised.predecessor_id, commitment.predecessor_id)
        self.assertEqual(revised.successor_ids, ("commitment.test.v2",))

    def test_owner_only_and_immutable_revision_fail_closed(self) -> None:
        active = _activate(
            _commitment(predecessor_id="commitment.root")
        )
        with self.assertRaises(CommitmentAuthorityError):
            transition_commitment(
                active,
                CommitmentStatus.RELEASED,
                actor_authority_id="authority.reviewer",
            )

        immutable = _activate(
            _commitment(revision_policy=RevisionPolicy.IMMUTABLE)
        )
        with self.assertRaisesRegex(
            CommitmentAuthorityError,
            "immutable",
        ):
            transition_commitment(
                immutable,
                CommitmentStatus.RELEASED,
                actor_authority_id="authority.user",
            )

        released = transition_commitment(
            active,
            CommitmentStatus.RELEASED,
            actor_authority_id="authority.user",
        )
        self.assertEqual(released.source_event_ref, active.source_event_ref)
        self.assertEqual(released.predecessor_id, "commitment.root")

    def test_achievement_and_maintenance_have_distinct_completion(self) -> None:
        achievement = _activate(
            _commitment(kind=CommitmentKind.ACHIEVEMENT)
        )
        completed = transition_commitment(
            achievement,
            CommitmentStatus.SATISFIED,
            actor_authority_id="authority.user",
        )
        self.assertIs(completed.status, CommitmentStatus.SATISFIED)

        maintenance = _activate(
            _commitment(
                commitment_id="commitment.maintenance",
                kind=CommitmentKind.MAINTENANCE,
            )
        )
        with self.assertRaisesRegex(
            CommitmentTransitionError,
            "remain active",
        ):
            transition_commitment(
                maintenance,
                CommitmentStatus.SATISFIED,
                actor_authority_id="authority.user",
            )
        violated = transition_commitment(
            maintenance,
            CommitmentStatus.VIOLATED,
            actor_authority_id="authority.user",
        )
        repaired = transition_commitment(
            violated,
            CommitmentStatus.ACTIVE,
            actor_authority_id="authority.user",
        )
        self.assertIs(repaired.status, CommitmentStatus.ACTIVE)

    def test_only_authorized_hard_commitments_can_gate(self) -> None:
        hard_proposed = _commitment()
        hard_active = _activate(hard_proposed)
        preference = _activate(
            _commitment(
                commitment_id="commitment.preference",
                strength=CommitmentStrength.PREFERENCE,
            )
        )
        hypothesis = _activate(
            _commitment(
                commitment_id="commitment.hypothesis",
                strength=CommitmentStrength.HYPOTHESIS,
            )
        )

        self.assertFalse(hard_proposed.has_hard_gate_authority)
        self.assertTrue(hard_active.has_hard_gate_authority)
        self.assertFalse(preference.has_hard_gate_authority)
        self.assertFalse(hypothesis.has_hard_gate_authority)

    def test_candidate_cannot_self_authorize_a_hard_commitment(self) -> None:
        proposed = _commitment()
        active = _activate(proposed)

        self.assertEqual(
            CandidateDelta(commitments_add=(proposed,)).commitments_add,
            (proposed,),
        )
        with self.assertRaisesRegex(ValueError, "only proposed"):
            CandidateDelta(commitments_add=(active,))

    def test_unrelated_commit_preserves_active_commitment_and_program(self) -> None:
        active = _activate(_commitment())
        state = replace(
            initial_state("A test building", run_id="run-commitment"),
            commitments=(active,),
            legacy_program_view=BuildingProgram(
                use="test use",
                footprint=FootprintTarget(
                    width_blocks=9,
                    depth_blocks=11,
                ),
                required_spaces=("primary space",),
                minimum_clear_height=3,
                entrance_count=1,
                circulation_min_width=1,
            ),
        )
        store = InMemoryStateStore(state)

        with tempfile.TemporaryDirectory() as temp_dir:
            outcome = run_once(
                store,
                WorkspaceManager(Path(temp_dir)),
                FakeArchitect(FakeVoxelAdapter()),
            )

        self.assertIs(outcome.status, RunStatus.COMMITTED)
        self.assertEqual(store.read().commitments, (active,))
        self.assertIs(
            store.read().legacy_program_view,
            state.legacy_program_view,
        )


if __name__ == "__main__":
    unittest.main()
