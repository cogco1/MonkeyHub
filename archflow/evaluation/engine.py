"""Read-only, fail-open soft evaluators."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from archflow.evaluation.model import (
    EvaluationObservation,
    Metric,
    ObservationStatus,
)
from archflow.state import CanonicalState
from archflow.submission import CandidateSubmission


class Evaluator(Protocol):
    name: str

    def evaluate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Metric, ...]: ...


class ClaimCoverageEvaluator:
    """Compatibility-only P1 soft evaluator, never hard authority."""

    name = "claim-coverage"

    def evaluate(
        self, state: CanonicalState, submission: CandidateSubmission
    ) -> tuple[Metric, ...]:
        if state.goal is None:
            raise ValueError(
                "ClaimCoverageEvaluator requires compatibility GoalContract state"
            )
        required = set(state.goal.must)
        evidenced = {
            claim.key
            for claim in submission.claims
            if claim.evidence_refs and claim.key in required
        }
        score = 1.0 if not required else len(evidenced) / len(required)
        return (
            Metric(
                name="required_claim_coverage",
                score=score,
                rationale=f"{len(evidenced)}/{len(required)} required claims evidenced",
            ),
        )


def _observation_id(
    evaluator: str,
    state: CanonicalState,
    submission: CandidateSubmission,
    status: ObservationStatus,
) -> str:
    payload = [
        evaluator,
        state.ref.project_id,
        state.ref.version,
        submission.submission_id,
        status.value,
    ]
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"evaluation-{digest[:20]}"


def evaluate_submission(
    state: CanonicalState,
    submission: CandidateSubmission,
    evaluators: tuple[Evaluator, ...],
) -> tuple[EvaluationObservation, ...]:
    observations = []
    for evaluator in evaluators:
        try:
            metrics = evaluator.evaluate(state, submission)
            observations.append(
                EvaluationObservation(
                    observation_id=_observation_id(
                        evaluator.name,
                        state,
                        submission,
                        ObservationStatus.OK,
                    ),
                    evaluator=evaluator.name,
                    submission_id=submission.submission_id,
                    checked_state=state.ref,
                    status=ObservationStatus.OK,
                    metrics=metrics,
                )
            )
        except Exception as exc:
            observations.append(
                EvaluationObservation(
                    observation_id=_observation_id(
                        evaluator.name,
                        state,
                        submission,
                        ObservationStatus.ERROR,
                    ),
                    evaluator=evaluator.name,
                    submission_id=submission.submission_id,
                    checked_state=state.ref,
                    status=ObservationStatus.ERROR,
                    notes=(f"{type(exc).__name__}: {exc}",),
                    uncertainty=1.0,
                )
            )
    return tuple(observations)
