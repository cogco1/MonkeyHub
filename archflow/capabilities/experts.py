"""State-responsive, read-only expert capability boundary.

Experts receive detached values only.  They can return advice for an exact
base state, but this module deliberately exposes no canonical-state writer,
committer, MCP adapter, or world handle.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from archflow.state.model import CanonicalState, StateRef
from archflow.project import ProjectRecordRef

_MAX_INPUT_ITEMS = 128
_MAX_INPUT_TEXT = 1_000
_MAX_ERROR_TEXT = 500


def _require_text(value: str, field: str, *, maximum: int = _MAX_INPUT_TEXT) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")


@dataclass(frozen=True, slots=True)
class ExpertObligation:
    obligation_id: str
    topic: str
    statement: str
    source_ref: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.obligation_id, "obligation_id"),
            (self.topic, "topic"),
            (self.statement, "statement"),
            (self.source_ref, "source_ref"),
        ):
            _require_text(value, field)


@dataclass(frozen=True, slots=True)
class ExpertEvidence:
    """A bounded evidence pointer, never the live observation or world."""

    kind: str
    evidence_ref: str
    summary: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.kind, "kind"),
            (self.evidence_ref, "evidence_ref"),
            (self.summary, "summary"),
        ):
            _require_text(value, field)


@dataclass(frozen=True, slots=True)
class ExpertSnapshot:
    """Detached, immutable input bound to one canonical state version."""

    base_state: StateRef
    program_json: str | None
    obligations: tuple[ExpertObligation, ...]
    evidence: tuple[ExpertEvidence, ...]
    design_program_ref: ProjectRecordRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.base_state, StateRef):
            raise TypeError("base_state must be a StateRef")
        if (
            self.design_program_ref is not None
            and not isinstance(self.design_program_ref, ProjectRecordRef)
        ):
            raise TypeError("design_program_ref must be a ProjectRecordRef or None")
        if self.program_json is not None:
            _require_text(self.program_json, "program_json", maximum=20_000)
        if not isinstance(self.obligations, tuple):
            raise TypeError("obligations must be a tuple")
        if not isinstance(self.evidence, tuple):
            raise TypeError("evidence must be a tuple")
        if len(self.obligations) > _MAX_INPUT_ITEMS:
            raise ValueError("too many obligations in expert snapshot")
        if len(self.evidence) > _MAX_INPUT_ITEMS:
            raise ValueError("too many evidence items in expert snapshot")

    @classmethod
    def detach(
        cls,
        state: CanonicalState,
        *,
        obligation_topics: Mapping[str, str],
        evidence: Sequence[ExpertEvidence] = (),
        legacy_validator_program_json: str | None = None,
    ) -> ExpertSnapshot:
        """Copy decision-relevant values without retaining ``state`` itself."""

        if not isinstance(state, CanonicalState):
            raise TypeError("state must be a CanonicalState")
        known_ids = {item.obligation_id for item in state.open_obligations}
        unknown_ids = set(obligation_topics) - known_ids
        if unknown_ids:
            raise ValueError(
                f"topics provided for unknown obligations: {sorted(unknown_ids)}"
            )
        detached_obligations = tuple(
            ExpertObligation(
                obligation_id=item.obligation_id,
                topic=obligation_topics.get(item.obligation_id, "general"),
                statement=item.statement,
                source_ref=item.source_ref,
            )
            for item in state.open_obligations
        )
        detached_evidence = tuple(evidence)
        if any(not isinstance(item, ExpertEvidence) for item in detached_evidence):
            raise TypeError("evidence items must be ExpertEvidence")
        return cls(
            base_state=StateRef(
                state.ref.project_id,
                state.ref.version,
                state.ref.state_sha256,
            ),
            design_program_ref=state.design_program_ref,
            program_json=legacy_validator_program_json,
            obligations=detached_obligations,
            evidence=detached_evidence,
        )


@dataclass(frozen=True, slots=True)
class ExpertSpec:
    expert_id: str
    description: str
    topics: frozenset[str]
    required_evidence_kinds: frozenset[str] = frozenset()
    timeout_seconds: float = 5.0
    max_output_chars: int = 8_000
    max_attempts: int = 1
    side_effects: bool = False

    def __post_init__(self) -> None:
        _require_text(self.expert_id, "expert_id")
        _require_text(self.description, "description")
        if not isinstance(self.topics, frozenset) or not self.topics:
            raise ValueError("topics must be a non-empty frozenset")
        if not isinstance(self.required_evidence_kinds, frozenset):
            raise TypeError("required_evidence_kinds must be a frozenset")
        for value in self.topics | self.required_evidence_kinds:
            _require_text(value, "topic or evidence kind")
        if not isinstance(self.timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be numeric")
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be greater than 0 and at most 30")
        if type(self.max_output_chars) is not int or not 128 <= self.max_output_chars <= 100_000:
            raise ValueError("max_output_chars must be between 128 and 100000")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 2:
            raise ValueError("max_attempts must be 1 or 2")
        if self.side_effects is not False:
            raise ValueError("expert capabilities must be read-only")


@dataclass(frozen=True, slots=True)
class ExpertAdvice:
    summary: str
    findings: tuple[str, ...] = ()
    suggested_obligations: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.summary, "summary", maximum=100_000)
        for field, values in (
            ("findings", self.findings),
            ("suggested_obligations", self.suggested_obligations),
            ("evidence_refs", self.evidence_refs),
        ):
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be a tuple")
            if len(values) > _MAX_INPUT_ITEMS:
                raise ValueError(f"{field} contains too many items")
            for value in values:
                _require_text(value, f"{field} item", maximum=100_000)

    def serialized_size(self) -> int:
        payload = {
            "summary": self.summary,
            "findings": self.findings,
            "suggested_obligations": self.suggested_obligations,
            "evidence_refs": self.evidence_refs,
        }
        return len(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


class ExpertReceiptStatus(StrEnum):
    ADVICE = "advice"
    ERROR = "error"
    TIMEOUT = "timeout"
    OVERSIZED = "oversized"
    MISSING_EVIDENCE = "missing_evidence"


@dataclass(frozen=True, slots=True)
class ExpertReceipt:
    schema: str
    receipt_id: str
    expert_id: str
    base_state: StateRef
    status: ExpertReceiptStatus
    attempts: int
    advice: ExpertAdvice | None = None
    error_code: str | None = None
    message: str | None = None


ExpertHandler = Callable[[ExpertSnapshot], ExpertAdvice]


class DuplicateExpertError(ValueError):
    pass


class ExpertRegistry:
    """Open registry whose sorted discovery result is not an execution plan."""

    def __init__(self) -> None:
        self._specs: dict[str, ExpertSpec] = {}
        self._handlers: dict[str, ExpertHandler] = {}

    def register(self, spec: ExpertSpec, handler: ExpertHandler) -> None:
        if spec.expert_id in self._specs:
            raise DuplicateExpertError(spec.expert_id)
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._specs[spec.expert_id] = spec
        self._handlers[spec.expert_id] = handler

    def get(self, expert_id: str) -> ExpertSpec:
        return self._specs[expert_id]

    def discover(self, snapshot: ExpertSnapshot) -> tuple[ExpertSpec, ...]:
        topics = {item.topic for item in snapshot.obligations}
        evidence_kinds = {item.kind for item in snapshot.evidence}
        relevant = (
            spec
            for spec in self._specs.values()
            if spec.topics & topics
            and spec.required_evidence_kinds <= evidence_kinds
        )
        return tuple(sorted(relevant, key=lambda item: item.expert_id))

    def invoke(self, expert_id: str, snapshot: ExpertSnapshot) -> ExpertReceipt:
        spec = self._specs[expert_id]
        available = {item.kind for item in snapshot.evidence}
        missing = sorted(spec.required_evidence_kinds - available)
        if missing:
            return _receipt(
                spec,
                snapshot,
                ExpertReceiptStatus.MISSING_EVIDENCE,
                attempts=0,
                error_code="expert.missing_evidence",
                message=f"required evidence unavailable: {missing}",
            )

        for attempt in range(1, spec.max_attempts + 1):
            outcome, value = _bounded_call(
                self._handlers[expert_id],
                snapshot,
                timeout_seconds=spec.timeout_seconds,
            )
            if outcome == "timeout":
                return _receipt(
                    spec,
                    snapshot,
                    ExpertReceiptStatus.TIMEOUT,
                    attempts=attempt,
                    error_code="expert.timeout",
                    message=f"expert exceeded {spec.timeout_seconds:g} seconds",
                )
            if outcome == "error":
                if attempt < spec.max_attempts:
                    continue
                return _receipt(
                    spec,
                    snapshot,
                    ExpertReceiptStatus.ERROR,
                    attempts=attempt,
                    error_code="expert.exception",
                    message=_bounded_error(value),
                )
            if not isinstance(value, ExpertAdvice):
                return _receipt(
                    spec,
                    snapshot,
                    ExpertReceiptStatus.ERROR,
                    attempts=attempt,
                    error_code="expert.invalid_output",
                    message="handler must return ExpertAdvice",
                )
            if value.serialized_size() > spec.max_output_chars:
                return _receipt(
                    spec,
                    snapshot,
                    ExpertReceiptStatus.OVERSIZED,
                    attempts=attempt,
                    error_code="expert.oversized_output",
                    message=f"output exceeds {spec.max_output_chars} characters",
                )
            return _receipt(
                spec,
                snapshot,
                ExpertReceiptStatus.ADVICE,
                attempts=attempt,
                advice=value,
            )

        raise AssertionError("bounded attempt loop must return")


def initial_expert_specs() -> tuple[ExpertSpec, ...]:
    """Initial vocabulary; callers remain free to register other experts."""

    return (
        ExpertSpec(
            expert_id="expert.program_use",
            description="Reviews program fit, use, and spatial allocation.",
            topics=frozenset({"program", "use", "use_zones"}),
        ),
        ExpertSpec(
            expert_id="expert.circulation",
            description="Reviews entrances, connectivity, clearances, and routes.",
            topics=frozenset(
                {"entrance", "connectivity", "circulation", "clear_height"}
            ),
            required_evidence_kinds=frozenset({"voxel_observation"}),
        ),
        ExpertSpec(
            expert_id="expert.constructibility",
            description="Reviews support, floating voxels, and constructibility.",
            topics=frozenset({"support", "floating", "constructibility"}),
            required_evidence_kinds=frozenset({"voxel_observation"}),
        ),
    )


def _bounded_call(
    handler: ExpertHandler,
    snapshot: ExpertSnapshot,
    *,
    timeout_seconds: float,
) -> tuple[str, object]:
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put_nowait(("ok", handler(snapshot)))
        except Exception as exc:  # Expert failure is converted into a receipt.
            result_queue.put_nowait(("error", exc))

    thread = threading.Thread(target=run, daemon=True, name="archflow-expert")
    thread.start()
    try:
        return result_queue.get(timeout=timeout_seconds)
    except queue.Empty:
        return ("timeout", None)


def _bounded_error(value: object) -> str:
    if isinstance(value, BaseException):
        message = f"{type(value).__name__}: {value}"
    else:
        message = str(value)
    return message[:_MAX_ERROR_TEXT]


def _receipt(
    spec: ExpertSpec,
    snapshot: ExpertSnapshot,
    status: ExpertReceiptStatus,
    *,
    attempts: int,
    advice: ExpertAdvice | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> ExpertReceipt:
    identity = json.dumps(
        {
            "expert_id": spec.expert_id,
            "project_id": snapshot.base_state.project_id,
            "version": snapshot.base_state.version,
            "status": status.value,
            "attempts": attempts,
            "error_code": error_code,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ExpertReceipt(
        schema="ExpertReceipt@1",
        receipt_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        expert_id=spec.expert_id,
        base_state=snapshot.base_state,
        status=status,
        attempts=attempts,
        advice=advice,
        error_code=error_code,
        message=message,
    )
