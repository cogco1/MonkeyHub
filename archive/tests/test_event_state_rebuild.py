from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from archflow.project.refs import ProjectVersionRef
from archive.archflow.runtime.event_log import (
    AppendOnlyEventLog,
    DesignEvent,
    EventDecision,
    EventLogError,
    verify_event_chain,
)
from archive.archflow.runtime.state_reducer import (
    REDUCER_VERSION,
    CanonicalStateMutation,
    CommitmentLifecycleTransition,
    StateReducerError,
    canonical_state_from_dict,
    canonical_state_to_dict,
    make_initialization_event,
    make_transition_event,
    rebuild_canonical_state,
)
from archflow.state.model import CanonicalState, Fact, initialize_canonical_project
from archflow.state.commitments import Commitment, CommitmentKind, CommitmentStatus, CommitmentStrength, CriterionRef, RevisionPolicy


class _DirectoryEventStore:
    """Disposable test adapter; production storage remains project-owned."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_event(
        self,
        *,
        project_id: str,
        sequence: int,
        event_sha256: str,
        payload: Mapping[str, Any],
    ) -> None:
        project = self.root / project_id
        project.mkdir(parents=True, exist_ok=True)
        existing = tuple(project.glob(f"{sequence:06d}-*.json"))
        if existing:
            raise RuntimeError("event sequence is immutable")
        path = project / f"{sequence:06d}-{event_sha256}.json"
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        path.write_text(encoded, encoding="utf-8")

    def list_events(
        self,
        *,
        project_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        project = self.root / project_id
        if not project.exists():
            return ()
        return tuple(
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(project.glob("*.json"))
        )


def _commitment(
    commitment_id: str,
    *,
    predecessor_id: str | None = None,
) -> Commitment:
    return Commitment(
        commitment_id=commitment_id,
        kind=CommitmentKind.MAINTENANCE,
        strength=CommitmentStrength.HARD,
        status=CommitmentStatus.PROPOSED,
        authority_id="authority-user",
        source_event_ref=f"evidence://request/{commitment_id}",
        satisfaction_criterion=CriterionRef(
            criterion_id=f"criterion-{commitment_id}",
            provider_id="validator-program",
            subject_refs=(f"semantic://{commitment_id}",),
        ),
        evidence_refs=(f"evidence://request/{commitment_id}",),
        scope_refs=(f"semantic://{commitment_id}",),
        revision_policy=RevisionPolicy.OWNER_ONLY,
        predecessor_id=predecessor_id,
    )


def _accepted_event(
    state: CanonicalState,
    previous: DesignEvent,
    mutation: CanonicalStateMutation,
    *,
    event_type: str,
    authority_id: str | None = None,
) -> tuple[DesignEvent, CanonicalState]:
    sequence = previous.sequence + 1
    return make_transition_event(
        state,
        previous,
        mutation,
        event_type=event_type,
        decision=EventDecision.ACCEPTED,
        actor_id="actor-architect",
        authority_id=authority_id,
        evidence_refs=(f"evidence://event/{sequence}",),
        validation_receipt_refs=(
            f"receipt://validation/{sequence}",
        ),
        commit_receipt_ref=f"receipt://commit/{sequence}",
    )


class EventStateRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = _DirectoryEventStore(Path(self.temporary.name))
        self.log = AppendOnlyEventLog(
            self.store,
            project_id="event-rebuild-test",
        )
        initial = initialize_canonical_project("event-rebuild-test")
        self.initial_event, self.initial_state = (
            make_initialization_event(
                initial,
                actor_id="actor-system",
                evidence_refs=("evidence://request/root",),
            )
        )
        self.log.append(self.initial_event)

    def test_commitment_lifecycle_and_revision_rebuild_exactly(
        self,
    ) -> None:
        events = [self.initial_event]
        state = self.initial_state
        capacity = _commitment("capacity")

        event, state = _accepted_event(
            state,
            events[-1],
            CanonicalStateMutation(commitments_add=(capacity,)),
            event_type="requirement.added",
            authority_id="authority-user",
        )
        self.log.append(event)
        events.append(event)
        for event_type, status in (
            ("commitment.accepted", CommitmentStatus.ACCEPTED),
            ("commitment.activated", CommitmentStatus.ACTIVE),
            ("commitment.violated", CommitmentStatus.VIOLATED),
            ("commitment.repaired", CommitmentStatus.ACTIVE),
        ):
            event, state = _accepted_event(
                state,
                events[-1],
                CanonicalStateMutation(
                    commitment_transitions=(
                        CommitmentLifecycleTransition(
                            commitment_id="capacity",
                            to_status=status,
                            monitor_state_ref=(
                                "monitor://capacity"
                                if status is CommitmentStatus.ACTIVE
                                else None
                            ),
                        ),
                    ),
                ),
                event_type=event_type,
                authority_id="authority-user",
            )
            self.log.append(event)
            events.append(event)

        successor = _commitment(
            "capacity-revised",
            predecessor_id="capacity",
        )
        revision_event, state = _accepted_event(
            state,
            events[-1],
            CanonicalStateMutation(
                commitments_add=(successor,),
                commitment_transitions=(
                    CommitmentLifecycleTransition(
                        commitment_id="capacity",
                        to_status=CommitmentStatus.REVISED,
                        successor_id="capacity-revised",
                    ),
                ),
            ),
            event_type="commitment.revised",
            authority_id="authority-user",
        )
        self.log.append(revision_event)

        reopened = AppendOnlyEventLog(
            self.store,
            project_id="event-rebuild-test",
        )
        result = rebuild_canonical_state(reopened.records())
        by_id = {
            item.commitment_id: item
            for item in result.state.commitments
        }

        self.assertEqual(result.state, state)
        self.assertEqual(result.event_count, 7)
        self.assertEqual(
            by_id["capacity"].status,
            CommitmentStatus.REVISED,
        )
        self.assertEqual(
            by_id["capacity"].successor_ids,
            ("capacity-revised",),
        )
        self.assertEqual(
            by_id["capacity"].monitor_state_ref,
            "monitor://capacity",
        )
        self.assertEqual(
            by_id["capacity-revised"].status,
            CommitmentStatus.PROPOSED,
        )
        self.assertEqual(
            by_id["capacity-revised"].predecessor_id,
            "capacity",
        )
        self.assertEqual(
            tuple(item.event_type for item in reopened.records()),
            (
                "project.initialized",
                "requirement.added",
                "commitment.accepted",
                "commitment.activated",
                "commitment.violated",
                "commitment.repaired",
                "commitment.revised",
            ),
        )

    def test_rejected_candidate_is_retained_without_advancing_state(
        self,
    ) -> None:
        rejected, unchanged = make_transition_event(
            self.initial_state,
            self.initial_event,
            CanonicalStateMutation(
                facts_add=(
                    Fact(
                        key="unsupported-grid",
                        value="candidate-only",
                        source_ref="evidence://candidate/rejected",
                    ),
                ),
            ),
            event_type="candidate.rejected",
            decision=EventDecision.REJECTED,
            actor_id="actor-architect",
            evidence_refs=("evidence://candidate/rejected",),
            validation_receipt_refs=(
                "receipt://validation/rejected",
            ),
        )
        self.log.append(rejected)
        self.assertEqual(unchanged.ref, self.initial_state.ref)

        accepted, advanced = _accepted_event(
            unchanged,
            rejected,
            CanonicalStateMutation(
                facts_add=(
                    Fact(
                        key="verified-grid",
                        value="accepted",
                        source_ref="evidence://candidate/accepted",
                    ),
                ),
            ),
            event_type="candidate.accepted",
        )
        self.log.append(accepted)

        result = rebuild_canonical_state(self.log.records())
        self.assertEqual(result.state, advanced)
        self.assertEqual(result.state.ref.version, 1)
        self.assertEqual(
            tuple(item.key for item in result.state.facts),
            ("verified-grid",),
        )
        self.assertEqual(
            result.rejected_event_ids,
            (rejected.event_id,),
        )

    def test_external_failure_remains_evidence_not_execution_replay(
        self,
    ) -> None:
        observed, unchanged = make_transition_event(
            self.initial_state,
            self.initial_event,
            CanonicalStateMutation(),
            event_type="external.preview_failed",
            decision=EventDecision.OBSERVED,
            actor_id="actor-adapter",
            evidence_refs=("evidence://mcp/preview-failure",),
            artifact_refs=("artifact://mcp/failure-receipt",),
        )
        self.log.append(observed)

        result = rebuild_canonical_state(self.log.records())

        self.assertEqual(result.state, unchanged)
        self.assertEqual(result.state.ref.version, 0)
        self.assertEqual(
            result.observed_event_ids,
            (observed.event_id,),
        )

    def test_hash_chain_detects_mutation_omission_and_reordering(
        self,
    ) -> None:
        accepted, _ = _accepted_event(
            self.initial_state,
            self.initial_event,
            CanonicalStateMutation(
                facts_add=(
                    Fact(
                        key="phase",
                        value="brief",
                        source_ref="evidence://phase/brief",
                    ),
                ),
            ),
            event_type="state.advanced",
        )
        self.log.append(accepted)

        project = (
            Path(self.temporary.name) / "event-rebuild-test"
        )
        second_path = sorted(project.glob("*.json"))[1]
        tampered = json.loads(
            second_path.read_text(encoding="utf-8")
        )
        tampered["actor_id"] = "actor-tampered"
        second_path.write_text(
            json.dumps(
                tampered,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            EventLogError,
            "content digest mismatch",
        ):
            self.log.records()

        with self.assertRaisesRegex(
            EventLogError,
            "sequence is reordered or incomplete",
        ):
            verify_event_chain((accepted, self.initial_event))
        with self.assertRaisesRegex(
            EventLogError,
            "sequence is reordered or incomplete",
        ):
            verify_event_chain((accepted,))

    def test_state_content_and_result_digest_tampering_fail_closed(
        self,
    ) -> None:
        state_payload = canonical_state_to_dict(self.initial_state)
        state_payload["evaluation_refs"] = ["evaluation://forged"]
        with self.assertRaisesRegex(
            StateReducerError,
            "digest disagrees",
        ):
            canonical_state_from_dict(state_payload)

        accepted, _ = _accepted_event(
            self.initial_state,
            self.initial_event,
            CanonicalStateMutation(
                facts_add=(
                    Fact(
                        key="phase",
                        value="brief",
                        source_ref="evidence://phase/brief",
                    ),
                ),
            ),
            event_type="state.advanced",
        )
        forged = replace(
            accepted,
            resulting_state=ProjectVersionRef(
                project_id="event-rebuild-test",
                version=1,
                state_sha256="f" * 64,
            ),
        )
        with self.assertRaisesRegex(
            StateReducerError,
            "resulting state digest mismatches",
        ):
            rebuild_canonical_state(
                (self.initial_event, forged)
            )

    def test_reducer_version_and_exact_base_are_enforced(self) -> None:
        accepted, advanced = _accepted_event(
            self.initial_state,
            self.initial_event,
            CanonicalStateMutation(
                facts_add=(
                    Fact(
                        key="phase",
                        value="brief",
                        source_ref="evidence://phase/brief",
                    ),
                ),
            ),
            event_type="state.advanced",
        )
        self.assertEqual(REDUCER_VERSION, accepted.reducer_version)

        future = replace(
            accepted,
            reducer_version="canonical-state-reducer-2",
        )
        with self.assertRaisesRegex(
            StateReducerError,
            "unsupported reducer version",
        ):
            rebuild_canonical_state(
                (self.initial_event, future)
            )

        stale = replace(
            accepted,
            prior_state=ProjectVersionRef(
                project_id="event-rebuild-test",
                version=0,
                state_sha256="b" * 64,
            ),
        )
        with self.assertRaisesRegex(
            StateReducerError,
            "prior state is stale",
        ):
            rebuild_canonical_state(
                (self.initial_event, stale)
            )
        self.assertEqual(advanced.ref.version, 1)


if __name__ == "__main__":
    unittest.main()
