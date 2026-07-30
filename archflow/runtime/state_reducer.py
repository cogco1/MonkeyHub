"""Versioned deterministic reconstruction of canonical design state.

The reducer consumes verified design events. Accepted events advance canonical
state by one version; rejected candidates and external observations remain in
the trace without changing canonical state. This is state reconstruction, not
external-tool or world replay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Mapping

from archflow.project.refs import (
    ProjectRecordRef,
    ProjectVersionRef,
)
from archflow.project.digests import project_state_sha256
from archflow.runtime.event_log import (
    DesignEvent,
    EventDecision,
    EventLogError,
    verify_event_chain,
)
from archflow.state import (
    ArtifactRef,
    BuildingProgram,
    CanonicalState,
    Commitment,
    CommitmentStatus,
    Fact,
    GoalContract,
    Obligation,
    transition_commitment,
)


REDUCER_VERSION = "canonical-state-reducer-1"


class StateReducerError(ValueError):
    """The event stream cannot deterministically rebuild canonical state."""


def _tuple_from_json(value: object, field: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise StateReducerError(f"{field} must be a list")
    return tuple(value)


def _record_to_dict(
    ref: ProjectRecordRef | None,
) -> dict[str, object] | None:
    if ref is None:
        return None
    return {
        "project_id": ref.project_id,
        "relative_path": ref.relative_path,
        "sha256": ref.sha256,
        "media_type": ref.media_type,
    }


def _record_from_dict(
    value: object,
) -> ProjectRecordRef | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "project_id",
        "relative_path",
        "sha256",
        "media_type",
    }:
        raise StateReducerError("project record ref schema drifted")
    try:
        return ProjectRecordRef(
            project_id=value["project_id"],
            relative_path=value["relative_path"],
            sha256=value["sha256"],
            media_type=value["media_type"],
        )
    except (TypeError, ValueError) as exc:
        raise StateReducerError("project record ref is invalid") from exc


def _goal_to_dict(goal: GoalContract | None) -> object:
    if goal is None:
        return None
    return {
        "prompt": goal.prompt,
        "must": list(goal.must),
        "prefer": list(goal.prefer),
        "forbid": list(goal.forbid),
    }


def _goal_from_dict(value: object) -> GoalContract | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "prompt",
        "must",
        "prefer",
        "forbid",
    }:
        raise StateReducerError("goal schema drifted")
    try:
        return GoalContract(
            prompt=value["prompt"],
            must=tuple(_tuple_from_json(value["must"], "goal must")),
            prefer=tuple(
                _tuple_from_json(value["prefer"], "goal prefer")
            ),
            forbid=tuple(
                _tuple_from_json(value["forbid"], "goal forbid")
            ),
        )
    except (TypeError, ValueError) as exc:
        raise StateReducerError("goal is invalid") from exc


def _fact_to_dict(fact: Fact) -> dict[str, str]:
    return {
        "key": fact.key,
        "value": fact.value,
        "source_ref": fact.source_ref,
    }


def _fact_from_dict(value: object) -> Fact:
    if not isinstance(value, Mapping) or set(value) != {
        "key",
        "value",
        "source_ref",
    }:
        raise StateReducerError("fact schema drifted")
    try:
        return Fact(
            key=value["key"],
            value=value["value"],
            source_ref=value["source_ref"],
        )
    except (TypeError, ValueError) as exc:
        raise StateReducerError("fact is invalid") from exc


def _obligation_to_dict(item: Obligation) -> dict[str, str]:
    return {
        "obligation_id": item.obligation_id,
        "statement": item.statement,
        "source_ref": item.source_ref,
    }


def _obligation_from_dict(value: object) -> Obligation:
    if not isinstance(value, Mapping) or set(value) != {
        "obligation_id",
        "statement",
        "source_ref",
    }:
        raise StateReducerError("obligation schema drifted")
    try:
        return Obligation(
            obligation_id=value["obligation_id"],
            statement=value["statement"],
            source_ref=value["source_ref"],
        )
    except (TypeError, ValueError) as exc:
        raise StateReducerError("obligation is invalid") from exc


def _artifact_to_dict(item: ArtifactRef) -> dict[str, str]:
    return {
        "artifact_id": item.artifact_id,
        "uri": item.uri,
        "media_type": item.media_type,
        "sha256": item.sha256,
    }


def _artifact_from_dict(value: object) -> ArtifactRef:
    if not isinstance(value, Mapping) or set(value) != {
        "artifact_id",
        "uri",
        "media_type",
        "sha256",
    }:
        raise StateReducerError("artifact schema drifted")
    try:
        return ArtifactRef(
            artifact_id=value["artifact_id"],
            uri=value["uri"],
            media_type=value["media_type"],
            sha256=value["sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise StateReducerError("artifact is invalid") from exc


def _state_content(state: CanonicalState) -> dict[str, object]:
    return {
        "schema": "CanonicalState@1",
        "project_id": state.ref.project_id,
        "version": state.ref.version,
        "goal": _goal_to_dict(state.goal),
        "design_program_ref": _record_to_dict(
            state.design_program_ref
        ),
        "legacy_program_view": (
            None
            if state.legacy_program_view is None
            else state.legacy_program_view.to_dict()
        ),
        "facts": [_fact_to_dict(item) for item in state.facts],
        "commitments": [
            item.to_dict() for item in state.commitments
        ],
        "open_obligations": [
            _obligation_to_dict(item)
            for item in state.open_obligations
        ],
        "artifacts": [
            _artifact_to_dict(item) for item in state.artifacts
        ],
        "evaluation_refs": list(state.evaluation_refs),
    }


def canonical_state_sha256(state: CanonicalState) -> str:
    if not isinstance(state, CanonicalState):
        raise TypeError("state must be a CanonicalState")
    return project_state_sha256(_state_content(state))


def seal_canonical_state(state: CanonicalState) -> CanonicalState:
    digest = canonical_state_sha256(state)
    if (
        state.ref.state_sha256 is not None
        and state.ref.state_sha256 != digest
    ):
        raise StateReducerError(
            "canonical state reference digest disagrees with content"
        )
    return replace(
        state,
        ref=ProjectVersionRef(
            project_id=state.ref.project_id,
            version=state.ref.version,
            state_sha256=digest,
        ),
    )


def canonical_state_to_dict(
    state: CanonicalState,
) -> dict[str, object]:
    sealed = seal_canonical_state(state)
    return {
        **_state_content(sealed),
        "state_sha256": sealed.ref.require_digest(),
    }


def canonical_state_from_dict(value: object) -> CanonicalState:
    if not isinstance(value, Mapping):
        raise StateReducerError("canonical state must be an object")
    expected = {
        "schema",
        "project_id",
        "version",
        "state_sha256",
        "goal",
        "design_program_ref",
        "legacy_program_view",
        "facts",
        "commitments",
        "open_obligations",
        "artifacts",
        "evaluation_refs",
    }
    if set(value) != expected or value.get("schema") != "CanonicalState@1":
        raise StateReducerError("canonical state schema drifted")
    legacy = value["legacy_program_view"]
    try:
        state = CanonicalState(
            ref=ProjectVersionRef(
                project_id=value["project_id"],
                version=value["version"],
                state_sha256=value["state_sha256"],
            ),
            goal=_goal_from_dict(value["goal"]),
            design_program_ref=_record_from_dict(
                value["design_program_ref"]
            ),
            legacy_program_view=(
                None
                if legacy is None
                else BuildingProgram.from_dict(dict(legacy))
            ),
            facts=tuple(
                _fact_from_dict(item)
                for item in _tuple_from_json(
                    value["facts"],
                    "facts",
                )
            ),
            commitments=tuple(
                Commitment.from_dict(item)
                for item in _tuple_from_json(
                    value["commitments"],
                    "commitments",
                )
            ),
            open_obligations=tuple(
                _obligation_from_dict(item)
                for item in _tuple_from_json(
                    value["open_obligations"],
                    "open_obligations",
                )
            ),
            artifacts=tuple(
                _artifact_from_dict(item)
                for item in _tuple_from_json(
                    value["artifacts"],
                    "artifacts",
                )
            ),
            evaluation_refs=tuple(
                _tuple_from_json(
                    value["evaluation_refs"],
                    "evaluation_refs",
                )
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, StateReducerError):
            raise
        raise StateReducerError("canonical state is invalid") from exc
    return seal_canonical_state(state)


@dataclass(frozen=True, slots=True)
class CommitmentLifecycleTransition:
    commitment_id: str
    to_status: CommitmentStatus
    successor_id: str | None = None
    monitor_state_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.commitment_id, str) or not self.commitment_id:
            raise StateReducerError("commitment_id must be non-empty")
        if not isinstance(self.to_status, CommitmentStatus):
            raise TypeError("to_status must be a CommitmentStatus")

    def to_dict(self) -> dict[str, object]:
        return {
            "commitment_id": self.commitment_id,
            "to_status": self.to_status.value,
            "successor_id": self.successor_id,
            "monitor_state_ref": self.monitor_state_ref,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
    ) -> CommitmentLifecycleTransition:
        if not isinstance(value, Mapping) or set(value) != {
            "commitment_id",
            "to_status",
            "successor_id",
            "monitor_state_ref",
        }:
            raise StateReducerError(
                "commitment transition schema drifted"
            )
        try:
            return cls(
                commitment_id=value["commitment_id"],
                to_status=CommitmentStatus(value["to_status"]),
                successor_id=value["successor_id"],
                monitor_state_ref=value["monitor_state_ref"],
            )
        except (TypeError, ValueError) as exc:
            raise StateReducerError(
                "commitment transition is invalid"
            ) from exc


@dataclass(frozen=True, slots=True)
class CanonicalStateMutation:
    facts_add: tuple[Fact, ...] = ()
    commitments_add: tuple[Commitment, ...] = ()
    commitment_transitions: tuple[
        CommitmentLifecycleTransition,
        ...,
    ] = ()
    obligations_discharge: tuple[str, ...] = ()
    obligations_add: tuple[Obligation, ...] = ()
    artifacts_add: tuple[ArtifactRef, ...] = ()
    evaluation_refs_add: tuple[str, ...] = ()

    SCHEMA = "CanonicalStateMutation@1"

    def __post_init__(self) -> None:
        for field, values, item_type in (
            ("facts_add", self.facts_add, Fact),
            ("commitments_add", self.commitments_add, Commitment),
            (
                "commitment_transitions",
                self.commitment_transitions,
                CommitmentLifecycleTransition,
            ),
            ("obligations_add", self.obligations_add, Obligation),
            ("artifacts_add", self.artifacts_add, ArtifactRef),
        ):
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be a tuple")
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{field} contains invalid values")
        for field, values in (
            ("obligations_discharge", self.obligations_discharge),
            ("evaluation_refs_add", self.evaluation_refs_add),
        ):
            if not isinstance(values, tuple):
                raise TypeError(f"{field} must be a tuple")
            if any(
                not isinstance(item, str) or not item.strip()
                for item in values
            ):
                raise StateReducerError(f"{field} must contain text")
            if len(values) != len(set(values)):
                raise StateReducerError(f"{field} contains duplicates")
        if any(
            item.status is not CommitmentStatus.PROPOSED
            for item in self.commitments_add
        ):
            raise StateReducerError(
                "new commitments must enter as proposed"
            )
        transition_ids = tuple(
            item.commitment_id
            for item in self.commitment_transitions
        )
        if len(transition_ids) != len(set(transition_ids)):
            raise StateReducerError(
                "commitment transitions contain duplicates"
            )

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.facts_add,
                self.commitments_add,
                self.commitment_transitions,
                self.obligations_discharge,
                self.obligations_add,
                self.artifacts_add,
                self.evaluation_refs_add,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "facts_add": [
                _fact_to_dict(item) for item in self.facts_add
            ],
            "commitments_add": [
                item.to_dict() for item in self.commitments_add
            ],
            "commitment_transitions": [
                item.to_dict()
                for item in self.commitment_transitions
            ],
            "obligations_discharge": list(
                self.obligations_discharge
            ),
            "obligations_add": [
                _obligation_to_dict(item)
                for item in self.obligations_add
            ],
            "artifacts_add": [
                _artifact_to_dict(item)
                for item in self.artifacts_add
            ],
            "evaluation_refs_add": list(
                self.evaluation_refs_add
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> CanonicalStateMutation:
        if not isinstance(value, Mapping):
            raise StateReducerError("state mutation must be an object")
        expected = {
            "schema",
            "facts_add",
            "commitments_add",
            "commitment_transitions",
            "obligations_discharge",
            "obligations_add",
            "artifacts_add",
            "evaluation_refs_add",
        }
        if set(value) != expected or value.get("schema") != cls.SCHEMA:
            raise StateReducerError("state mutation schema drifted")
        return cls(
            facts_add=tuple(
                _fact_from_dict(item)
                for item in _tuple_from_json(
                    value["facts_add"],
                    "facts_add",
                )
            ),
            commitments_add=tuple(
                Commitment.from_dict(item)
                for item in _tuple_from_json(
                    value["commitments_add"],
                    "commitments_add",
                )
            ),
            commitment_transitions=tuple(
                CommitmentLifecycleTransition.from_dict(item)
                for item in _tuple_from_json(
                    value["commitment_transitions"],
                    "commitment_transitions",
                )
            ),
            obligations_discharge=tuple(
                _tuple_from_json(
                    value["obligations_discharge"],
                    "obligations_discharge",
                )
            ),
            obligations_add=tuple(
                _obligation_from_dict(item)
                for item in _tuple_from_json(
                    value["obligations_add"],
                    "obligations_add",
                )
            ),
            artifacts_add=tuple(
                _artifact_from_dict(item)
                for item in _tuple_from_json(
                    value["artifacts_add"],
                    "artifacts_add",
                )
            ),
            evaluation_refs_add=tuple(
                _tuple_from_json(
                    value["evaluation_refs_add"],
                    "evaluation_refs_add",
                )
            ),
        )


def apply_canonical_mutation(
    state: CanonicalState,
    mutation: CanonicalStateMutation,
    *,
    authority_id: str | None,
) -> CanonicalState:
    if not isinstance(state, CanonicalState):
        raise TypeError("state must be a CanonicalState")
    if not isinstance(mutation, CanonicalStateMutation):
        raise TypeError("mutation must be a CanonicalStateMutation")
    if mutation.is_empty:
        raise StateReducerError("accepted mutation cannot be empty")

    facts = {item.key: item for item in state.facts}
    for item in mutation.facts_add:
        if item.key in facts:
            raise StateReducerError(f"fact already exists: {item.key}")
        facts[item.key] = item

    commitments = {
        item.commitment_id: item for item in state.commitments
    }
    if mutation.commitment_transitions and authority_id is None:
        raise StateReducerError(
            "commitment transition requires event authority"
        )
    for item in mutation.commitment_transitions:
        current = commitments.get(item.commitment_id)
        if current is None:
            raise StateReducerError(
                f"unknown commitment transition: {item.commitment_id}"
            )
        try:
            commitments[item.commitment_id] = transition_commitment(
                current,
                item.to_status,
                actor_authority_id=authority_id,
                successor_id=item.successor_id,
                monitor_state_ref=item.monitor_state_ref,
            )
        except (PermissionError, ValueError) as exc:
            raise StateReducerError(
                f"invalid commitment transition: {item.commitment_id}"
            ) from exc
    for item in mutation.commitments_add:
        if item.commitment_id in commitments:
            raise StateReducerError(
                f"commitment already exists: {item.commitment_id}"
            )
        commitments[item.commitment_id] = item

    obligations = {
        item.obligation_id: item for item in state.open_obligations
    }
    unknown = set(mutation.obligations_discharge) - set(obligations)
    if unknown:
        raise StateReducerError(
            f"unknown obligation discharge: {sorted(unknown)}"
        )
    for obligation_id in mutation.obligations_discharge:
        del obligations[obligation_id]
    for item in mutation.obligations_add:
        if item.obligation_id in obligations:
            raise StateReducerError(
                f"obligation already exists: {item.obligation_id}"
            )
        obligations[item.obligation_id] = item

    artifacts = {
        item.artifact_id: item for item in state.artifacts
    }
    for item in mutation.artifacts_add:
        if item.artifact_id in artifacts:
            raise StateReducerError(
                f"artifact already exists: {item.artifact_id}"
            )
        artifacts[item.artifact_id] = item

    evaluations = tuple(
        dict.fromkeys(
            (*state.evaluation_refs, *mutation.evaluation_refs_add)
        )
    )
    replacement = CanonicalState(
        ref=ProjectVersionRef(
            project_id=state.ref.project_id,
            version=state.ref.version + 1,
        ),
        goal=state.goal,
        design_program_ref=state.design_program_ref,
        legacy_program_view=state.legacy_program_view,
        facts=tuple(sorted(facts.values(), key=lambda item: item.key)),
        commitments=tuple(
            sorted(
                commitments.values(),
                key=lambda item: item.commitment_id,
            )
        ),
        open_obligations=tuple(
            sorted(
                obligations.values(),
                key=lambda item: item.obligation_id,
            )
        ),
        artifacts=tuple(
            sorted(
                artifacts.values(),
                key=lambda item: item.artifact_id,
            )
        ),
        evaluation_refs=evaluations,
    )
    return seal_canonical_state(replacement)


@dataclass(frozen=True, slots=True)
class RebuildResult:
    state: CanonicalState
    event_count: int
    rejected_event_ids: tuple[str, ...]
    observed_event_ids: tuple[str, ...]


def make_initialization_event(
    state: CanonicalState,
    *,
    actor_id: str,
    evidence_refs: tuple[str, ...] = (),
) -> tuple[DesignEvent, CanonicalState]:
    sealed = seal_canonical_state(state)
    if sealed.ref.version != 0:
        raise StateReducerError("initial state must be version zero")
    event = DesignEvent.create(
        sequence=0,
        project_id=sealed.ref.project_id,
        event_type="project.initialized",
        decision=EventDecision.ACCEPTED,
        actor_id=actor_id,
        authority_id=None,
        prior_event_sha256=None,
        prior_state=None,
        proposed_delta={
            "schema": "CanonicalInitialization@1",
            "state": canonical_state_to_dict(sealed),
        },
        evidence_refs=evidence_refs,
        validation_receipt_refs=(),
        commit_receipt_ref=None,
        artifact_refs=(),
        reducer_version=REDUCER_VERSION,
        resulting_state=sealed.ref,
    )
    return event, sealed


def make_transition_event(
    current: CanonicalState,
    previous_event: DesignEvent,
    mutation: CanonicalStateMutation,
    *,
    event_type: str,
    decision: EventDecision,
    actor_id: str,
    authority_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    validation_receipt_refs: tuple[str, ...] = (),
    commit_receipt_ref: str | None = None,
    artifact_refs: tuple[str, ...] = (),
) -> tuple[DesignEvent, CanonicalState]:
    current = seal_canonical_state(current)
    if previous_event.resulting_state != current.ref:
        raise StateReducerError(
            "previous event does not reach current state"
        )
    if decision is EventDecision.ACCEPTED:
        if not validation_receipt_refs or commit_receipt_ref is None:
            raise StateReducerError(
                "accepted transition requires validation and commit receipts"
            )
        resulting = apply_canonical_mutation(
            current,
            mutation,
            authority_id=authority_id,
        )
    else:
        if commit_receipt_ref is not None:
            raise StateReducerError(
                "non-accepted event cannot carry a commit receipt"
            )
        if (
            decision is EventDecision.REJECTED
            and not validation_receipt_refs
        ):
            raise StateReducerError(
                "rejected transition requires validation evidence"
            )
        resulting = current
    event = DesignEvent.create(
        sequence=previous_event.sequence + 1,
        project_id=current.ref.project_id,
        event_type=event_type,
        decision=decision,
        actor_id=actor_id,
        authority_id=authority_id,
        prior_event_sha256=previous_event.event_sha256,
        prior_state=current.ref,
        proposed_delta=mutation.to_dict(),
        evidence_refs=evidence_refs,
        validation_receipt_refs=validation_receipt_refs,
        commit_receipt_ref=commit_receipt_ref,
        artifact_refs=artifact_refs,
        reducer_version=REDUCER_VERSION,
        resulting_state=resulting.ref,
    )
    return event, resulting


def rebuild_canonical_state(
    events: tuple[DesignEvent, ...],
) -> RebuildResult:
    try:
        verified = verify_event_chain(events)
    except EventLogError as exc:
        raise StateReducerError("event chain is invalid") from exc
    initial = verified[0]
    if (
        initial.event_type != "project.initialized"
        or initial.decision is not EventDecision.ACCEPTED
        or initial.reducer_version != REDUCER_VERSION
    ):
        raise StateReducerError("event chain lacks valid initialization")
    payload = initial.proposed_delta
    if set(payload) != {"schema", "state"} or payload.get(
        "schema"
    ) != "CanonicalInitialization@1":
        raise StateReducerError("initialization payload schema drifted")
    state = canonical_state_from_dict(payload["state"])
    if state.ref != initial.resulting_state:
        raise StateReducerError(
            "initial event resulting state digest mismatches"
        )

    rejected: list[str] = []
    observed: list[str] = []
    for event in verified[1:]:
        if event.reducer_version != REDUCER_VERSION:
            raise StateReducerError("unsupported reducer version")
        if event.prior_state != state.ref:
            raise StateReducerError(
                "event prior state is stale or discontinuous"
            )
        mutation = CanonicalStateMutation.from_dict(
            event.proposed_delta
        )
        if event.decision is EventDecision.ACCEPTED:
            if (
                not event.validation_receipt_refs
                or event.commit_receipt_ref is None
            ):
                raise StateReducerError(
                    "accepted event lacks decision receipts"
                )
            state = apply_canonical_mutation(
                state,
                mutation,
                authority_id=event.authority_id,
            )
        elif event.decision is EventDecision.REJECTED:
            if not event.validation_receipt_refs:
                raise StateReducerError(
                    "rejected event lacks validation receipt"
                )
            if event.commit_receipt_ref is not None:
                raise StateReducerError(
                    "rejected event carries a commit receipt"
                )
            rejected.append(event.event_id)
        else:
            if event.commit_receipt_ref is not None:
                raise StateReducerError(
                    "observed event carries a commit receipt"
                )
            observed.append(event.event_id)
        if state.ref != event.resulting_state:
            raise StateReducerError(
                "event resulting state digest mismatches reducer output"
            )
    return RebuildResult(
        state=state,
        event_count=len(verified),
        rejected_event_ids=tuple(rejected),
        observed_event_ids=tuple(observed),
    )
