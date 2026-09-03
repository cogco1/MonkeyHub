"""Read-only, evidence-grounded multi-objective aesthetic observations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from archflow.state.model import CanonicalState, StateRef
from archflow.submission.model import CandidateSubmission

_MAX_VIEWS = 32
_MAX_TEXT = 1_000
_MAX_ERROR_TEXT = 500


def _require_text(value: str, field: str, *, maximum: int = _MAX_TEXT) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")


class AestheticObjective(StrEnum):
    PROPORTION = "proportion"
    MASSING_COHERENCE = "massing_coherence"
    FACADE_RHYTHM = "facade_rhythm"
    LEGIBILITY = "legibility"
    SPATIAL_VARIETY = "spatial_variety"
    MATERIAL_COHERENCE = "material_coherence"
    VIEW_DEPENDENT_QUALITY = "view_dependent_quality"


class AestheticStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ViewEvidence:
    """A stable pointer to a rendered view, not an inferred visual claim."""

    evidence_ref: str
    artifact_id: str
    uri: str
    sha256: str
    viewpoint: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.evidence_ref, "evidence_ref"),
            (self.artifact_id, "artifact_id"),
            (self.uri, "uri"),
            (self.viewpoint, "viewpoint"),
        ):
            _require_text(value, field)
        digest = self.sha256.lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("sha256 must be a 64-character hex digest")


@dataclass(frozen=True, slots=True)
class AestheticSnapshot:
    """Detached visual-review input bound to an exact candidate and state."""

    base_state: StateRef
    submission_id: str
    views: tuple[ViewEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.base_state, StateRef):
            raise TypeError("base_state must be a StateRef")
        _require_text(self.submission_id, "submission_id")
        if not isinstance(self.views, tuple):
            raise TypeError("views must be a tuple")
        if len(self.views) > _MAX_VIEWS:
            raise ValueError(f"views cannot exceed {_MAX_VIEWS} items")
        references = tuple(item.evidence_ref for item in self.views)
        if len(references) != len(set(references)):
            raise ValueError("view evidence references must be unique")

    @classmethod
    def detach(
        cls,
        state: CanonicalState,
        submission: CandidateSubmission,
        views: Sequence[ViewEvidence],
    ) -> AestheticSnapshot:
        if not isinstance(state, CanonicalState):
            raise TypeError("state must be a CanonicalState")
        if not isinstance(submission, CandidateSubmission):
            raise TypeError("submission must be a CandidateSubmission")
        if submission.base != state.ref:
            raise ValueError("submission is not bound to the supplied state")
        detached_views = tuple(views)
        if any(not isinstance(item, ViewEvidence) for item in detached_views):
            raise TypeError("views must contain ViewEvidence values")
        return cls(
            base_state=StateRef(state.ref.run_id, state.ref.version),
            submission_id=submission.submission_id,
            views=detached_views,
        )


@dataclass(frozen=True, slots=True)
class AestheticMetric:
    objective: AestheticObjective
    score: float
    confidence: float
    rationale: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.objective, AestheticObjective):
            raise TypeError("objective must be an AestheticObjective")
        for value, field in (
            (self.score, "score"),
            (self.confidence, "confidence"),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{field} must be finite")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field} must be inside [0, 1]")
        _require_text(self.rationale, "rationale")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("evidence_refs must be a non-empty tuple")
        for reference in self.evidence_refs:
            _require_text(reference, "evidence_ref")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence_refs must be unique")


@dataclass(frozen=True, slots=True)
class AestheticObservation:
    SCHEMA: ClassVar[str] = "AestheticObservation@1"

    observation_id: str
    evaluator: str
    submission_id: str
    checked_state: StateRef
    status: AestheticStatus
    metrics: tuple[AestheticMetric, ...] = ()
    view_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    uncertainty: float = 1.0

    def __post_init__(self) -> None:
        for value, field in (
            (self.observation_id, "observation_id"),
            (self.evaluator, "evaluator"),
            (self.submission_id, "submission_id"),
        ):
            _require_text(value, field)
        for value, field in (
            (self.metrics, "metrics"),
            (self.view_refs, "view_refs"),
            (self.notes, "notes"),
        ):
            if not isinstance(value, tuple):
                raise TypeError(f"{field} must be a tuple")
        if not 0.0 <= self.uncertainty <= 1.0:
            raise ValueError("uncertainty must be inside [0, 1]")
        if self.status is AestheticStatus.OK and not self.metrics:
            raise ValueError("ok observations must contain metrics")
        if self.status is not AestheticStatus.OK and self.metrics:
            raise ValueError("unavailable/error observations cannot claim metrics")
        objectives = tuple(item.objective for item in self.metrics)
        if len(objectives) != len(set(objectives)):
            raise ValueError("metric objectives must be unique")
        if len(self.metrics) > len(AestheticObjective):
            raise ValueError("too many aesthetic metrics")
        if len(self.view_refs) != len(set(self.view_refs)):
            raise ValueError("view_refs must be unique")
        for note in self.notes:
            _require_text(note, "note", maximum=_MAX_ERROR_TEXT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "observation_id": self.observation_id,
            "evaluator": self.evaluator,
            "submission_id": self.submission_id,
            "checked_state": {
                "run_id": self.checked_state.run_id,
                "version": self.checked_state.version,
            },
            "status": self.status.value,
            "metrics": [
                {
                    "objective": item.objective.value,
                    "score": item.score,
                    "confidence": item.confidence,
                    "rationale": item.rationale,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in self.metrics
            ],
            "view_refs": list(self.view_refs),
            "notes": list(self.notes),
            "uncertainty": self.uncertainty,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


VisualAssessor = Callable[[AestheticSnapshot], tuple[AestheticMetric, ...]]


def evaluate_aesthetics(
    snapshot: AestheticSnapshot,
    *,
    evaluator: str,
    assessor: VisualAssessor,
) -> AestheticObservation:
    """Return a soft observation; failures never acquire hard-gate authority."""

    _require_text(evaluator, "evaluator")
    if not callable(assessor):
        raise TypeError("assessor must be callable")
    if not snapshot.views:
        return _observation(
            snapshot,
            evaluator=evaluator,
            status=AestheticStatus.UNAVAILABLE,
            notes=("No visual evidence was supplied; no visual claims were made.",),
            uncertainty=1.0,
        )

    try:
        metrics = assessor(snapshot)
        if not isinstance(metrics, tuple):
            raise TypeError("assessor output must be a tuple")
        if not metrics:
            return _observation(
                snapshot,
                evaluator=evaluator,
                status=AestheticStatus.UNAVAILABLE,
                notes=("The evaluator returned no grounded visual metrics.",),
                uncertainty=1.0,
            )
        if len(metrics) > len(AestheticObjective):
            raise ValueError("assessor returned too many metrics")
        if any(not isinstance(item, AestheticMetric) for item in metrics):
            raise TypeError("assessor output must contain AestheticMetric values")
        available_refs = {view.evidence_ref for view in snapshot.views}
        claimed_refs = {
            reference for metric in metrics for reference in metric.evidence_refs
        }
        unknown_refs = sorted(claimed_refs - available_refs)
        if unknown_refs:
            raise ValueError(f"metrics cite unavailable views: {unknown_refs}")
        objectives = tuple(item.objective for item in metrics)
        if len(objectives) != len(set(objectives)):
            raise ValueError("assessor returned duplicate objectives")
    except Exception as exc:
        return _observation(
            snapshot,
            evaluator=evaluator,
            status=AestheticStatus.ERROR,
            notes=(f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_TEXT],),
            uncertainty=1.0,
        )

    uncertainty = 1.0 - sum(item.confidence for item in metrics) / len(metrics)
    return _observation(
        snapshot,
        evaluator=evaluator,
        status=AestheticStatus.OK,
        metrics=metrics,
        uncertainty=uncertainty,
    )


def pareto_front(
    observations: Sequence[AestheticObservation],
) -> tuple[AestheticObservation, ...]:
    """Return non-dominated observations without selecting or committing one."""

    candidates = tuple(
        item for item in observations if item.status is AestheticStatus.OK
    )
    if not candidates:
        return ()
    objective_sets = [
        frozenset(metric.objective for metric in item.metrics) for item in candidates
    ]
    if any(objectives != objective_sets[0] for objectives in objective_sets[1:]):
        raise ValueError("Pareto comparison requires the same objective set")

    def vector(item: AestheticObservation) -> dict[AestheticObjective, float]:
        return {metric.objective: metric.score for metric in item.metrics}

    def dominates(left: AestheticObservation, right: AestheticObservation) -> bool:
        left_values = vector(left)
        right_values = vector(right)
        return all(
            left_values[key] >= right_values[key] for key in objective_sets[0]
        ) and any(
            left_values[key] > right_values[key] for key in objective_sets[0]
        )

    frontier = (
        candidate
        for candidate in candidates
        if not any(
            other is not candidate and dominates(other, candidate)
            for other in candidates
        )
    )
    return tuple(sorted(frontier, key=lambda item: item.submission_id))


def _observation(
    snapshot: AestheticSnapshot,
    *,
    evaluator: str,
    status: AestheticStatus,
    metrics: tuple[AestheticMetric, ...] = (),
    notes: tuple[str, ...] = (),
    uncertainty: float,
) -> AestheticObservation:
    view_refs = tuple(view.evidence_ref for view in snapshot.views)
    identity = {
        "schema": AestheticObservation.SCHEMA,
        "evaluator": evaluator,
        "submission_id": snapshot.submission_id,
        "base_state": [
            snapshot.base_state.run_id,
            snapshot.base_state.version,
        ],
        "status": status.value,
        "view_refs": view_refs,
        "metrics": [
            [
                item.objective.value,
                item.score,
                item.confidence,
                item.rationale,
                item.evidence_refs,
            ]
            for item in metrics
        ],
        "notes": notes,
    }
    digest = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return AestheticObservation(
        observation_id=f"aesthetic-{digest[:20]}",
        evaluator=evaluator,
        submission_id=snapshot.submission_id,
        checked_state=snapshot.base_state,
        status=status,
        metrics=metrics,
        view_refs=view_refs,
        notes=notes,
        uncertainty=uncertainty,
    )
