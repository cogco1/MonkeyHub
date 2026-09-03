"""Structured evaluation observations that never write canonical state."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from archflow.state.model import StateRef


class ObservationStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    score: float
    rationale: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("metric name must be non-empty")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("metric score must be finite and inside [0, 1]")
        if not self.rationale.strip():
            raise ValueError("metric rationale must be non-empty")


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    observation_id: str
    evaluator: str
    submission_id: str
    checked_state: StateRef
    status: ObservationStatus
    metrics: tuple[Metric, ...] = ()
    notes: tuple[str, ...] = ()
    uncertainty: float = 0.0

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.evaluator.strip():
            raise ValueError("observation_id and evaluator must be non-empty")
        if not 0.0 <= self.uncertainty <= 1.0:
            raise ValueError("uncertainty must be inside [0, 1]")
        if self.status is ObservationStatus.ERROR and self.metrics:
            raise ValueError("error observations cannot claim score metrics")
