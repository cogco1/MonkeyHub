from __future__ import annotations

import json
import unittest
from pathlib import Path

from archflow.evaluation.aesthetic import (
    AestheticMetric,
    AestheticObjective,
    AestheticSnapshot,
    AestheticStatus,
    ViewEvidence,
    evaluate_aesthetics,
    pareto_front,
)
from archflow.state import CanonicalState, GoalContract, StateRef
from archflow.submission import CandidateDelta, CandidateSubmission
from archflow.validation.usability import UsabilityReceipt


FIXTURE = Path(__file__).parent / "fixtures" / "views" / "candidate_views.json"


def _state() -> CanonicalState:
    return CanonicalState(
        ref=StateRef("run-aesthetic", 4),
        goal=GoalContract(
            prompt="Build a usable test building",
            must=("usable",),
            prefer=("legible entrance",),
        ),
    )


def _submission(
    state: CanonicalState,
    submission_id: str = "candidate-a",
) -> CandidateSubmission:
    return CandidateSubmission(
        submission_id=submission_id,
        base=state.ref,
        workspace_id=f"workspace-{submission_id}",
        intent="Offer a test candidate",
        delta=CandidateDelta(),
        claims=(),
    )


def _views() -> tuple[ViewEvidence, ...]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return tuple(ViewEvidence(**item) for item in payload["views"])


def _metric(
    objective: AestheticObjective,
    score: float,
    evidence_ref: str = "view:candidate:front",
) -> AestheticMetric:
    return AestheticMetric(
        objective=objective,
        score=score,
        confidence=0.8,
        rationale=f"{objective.value} is visible in the referenced view.",
        evidence_refs=(evidence_ref,),
    )


class AestheticEvaluationTests(unittest.TestCase):
    def test_same_evidence_yields_stable_bounded_schema(self) -> None:
        state = _state()
        snapshot = AestheticSnapshot.detach(
            state,
            _submission(state),
            _views(),
        )

        def assessor(_: AestheticSnapshot) -> tuple[AestheticMetric, ...]:
            return tuple(
                _metric(objective, 0.6) for objective in AestheticObjective
            )

        first = evaluate_aesthetics(
            snapshot,
            evaluator="fixture-visual-review",
            assessor=assessor,
        )
        second = evaluate_aesthetics(
            snapshot,
            evaluator="fixture-visual-review",
            assessor=assessor,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first.to_dict()["schema"], "AestheticObservation@1")
        self.assertEqual(len(first.metrics), 7)
        self.assertLess(len(first.to_json()), 10_000)
        self.assertTrue(
            all(metric.evidence_refs for metric in first.metrics)
        )

    def test_missing_or_failed_views_make_no_claim_and_do_not_touch_hard_gate(
        self,
    ) -> None:
        state = _state()
        submission = _submission(state)
        hard_receipt = UsabilityReceipt(
            receipt_id="hard-pass",
            observation_id="voxel-observation",
            program_schema="BuildingProgram@1",
            passed=True,
            findings=(),
        )
        calls = 0

        def should_not_run(_: AestheticSnapshot) -> tuple[AestheticMetric, ...]:
            nonlocal calls
            calls += 1
            return (_metric(AestheticObjective.PROPORTION, 0.5),)

        missing = evaluate_aesthetics(
            AestheticSnapshot.detach(state, submission, ()),
            evaluator="offline-visual-review",
            assessor=should_not_run,
        )

        def broken(_: AestheticSnapshot) -> tuple[AestheticMetric, ...]:
            raise RuntimeError("visual service offline")

        failed = evaluate_aesthetics(
            AestheticSnapshot.detach(state, submission, _views()),
            evaluator="broken-visual-review",
            assessor=broken,
        )

        self.assertEqual(calls, 0)
        self.assertIs(missing.status, AestheticStatus.UNAVAILABLE)
        self.assertIs(failed.status, AestheticStatus.ERROR)
        self.assertEqual(missing.metrics, ())
        self.assertEqual(failed.metrics, ())
        self.assertTrue(hard_receipt.passed)
        self.assertFalse(hasattr(missing, "waive_hard_failure"))

    def test_metrics_cannot_cite_an_unavailable_view(self) -> None:
        state = _state()
        snapshot = AestheticSnapshot.detach(
            state,
            _submission(state),
            _views(),
        )
        observation = evaluate_aesthetics(
            snapshot,
            evaluator="ungrounded-review",
            assessor=lambda _: (
                _metric(
                    AestheticObjective.PROPORTION,
                    0.9,
                    evidence_ref="view:not-supplied",
                ),
            ),
        )

        self.assertIs(observation.status, AestheticStatus.ERROR)
        self.assertEqual(observation.metrics, ())
        self.assertLessEqual(len(observation.notes[0]), 500)

    def test_pareto_comparison_preserves_tradeoffs_without_a_winner(self) -> None:
        state = _state()
        views = _views()

        def observation(
            submission_id: str,
            proportion: float,
            variety: float,
        ):
            snapshot = AestheticSnapshot.detach(
                state,
                _submission(state, submission_id),
                views,
            )
            return evaluate_aesthetics(
                snapshot,
                evaluator="tradeoff-review",
                assessor=lambda _: (
                    _metric(AestheticObjective.PROPORTION, proportion),
                    _metric(AestheticObjective.SPATIAL_VARIETY, variety),
                ),
            )

        proportion_led = observation("candidate-proportion", 0.9, 0.5)
        variety_led = observation("candidate-variety", 0.6, 0.9)
        dominated = observation("candidate-dominated", 0.5, 0.4)

        frontier = pareto_front((dominated, variety_led, proportion_led))

        self.assertEqual(
            tuple(item.submission_id for item in frontier),
            ("candidate-proportion", "candidate-variety"),
        )
        self.assertFalse(hasattr(frontier, "winner"))
        self.assertFalse(any(hasattr(item, "aggregate_score") for item in frontier))

    def test_evaluation_uses_detached_input_and_remains_read_only(self) -> None:
        state = _state()
        submission = _submission(state)
        view_list = list(_views())
        snapshot = AestheticSnapshot.detach(state, submission, view_list)
        before = state
        view_list.clear()

        def assessor(detached: AestheticSnapshot) -> tuple[AestheticMetric, ...]:
            self.assertFalse(hasattr(detached, "canonical_state"))
            self.assertFalse(hasattr(detached, "committer"))
            self.assertFalse(hasattr(detached, "mcp"))
            self.assertFalse(hasattr(detached, "world"))
            return (_metric(AestheticObjective.LEGIBILITY, 0.7),)

        result = evaluate_aesthetics(
            snapshot,
            evaluator="readonly-review",
            assessor=assessor,
        )

        self.assertEqual(state, before)
        self.assertEqual(len(snapshot.views), 2)
        self.assertEqual(result.checked_state, state.ref)
        self.assertIs(result.status, AestheticStatus.OK)


if __name__ == "__main__":
    unittest.main()
